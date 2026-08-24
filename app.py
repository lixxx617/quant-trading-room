from news_fetcher import get_stock_news
from predictor import predict_future_signal
from datetime import datetime
import dashboard_data as dd
import pandas as pd
import streamlit as st
import requests
import urllib.parse
import pytz
from datetime import datetime, time
import db_layer as db

st.set_page_config(page_title="donchian_M20 台股量化交易系統", layout="wide")

st.title("📈 donchian_M20 台股量化交易系統")
st.caption("唐奇安通道突破 (N=20 進場 / M=20 出場) + 200MA 個股濾網 + 0050 大盤濾網 + 組合部位管理")
st.info("💡 **提醒**：本系統報價源自 yfinance，數據約有 **15 分鐘延遲**，僅供量化策略分析參考，實際交易請以券商軟體即時報價為準。")

# ----------------
# 側邊欄：設定與資產編輯器
# ----------------
st.sidebar.header("⚙️ 系統設定與自動更新")

# 自動刷新設置
# ----------------------------------------------------
# 自動刷新設置：僅在台股開盤時間 (週一至週五 09:00 - 13:30) 每 30 秒刷新
# ----------------------------------------------------
import pytz
from datetime import datetime, time

# 指定台灣時區
tw_tz = pytz.timezone('Asia/Taipei')
now_tw = datetime.now(tw_tz)

is_weekday = now_tw.weekday() < 5  # 0~4 代表週一至週五
market_open = time(9, 0)
market_close = time(13, 30)
is_market_hours = is_weekday and (market_open <= now_tw.time() <= market_close)

if is_market_hours:
	try:
		from streamlit_autorefresh import st_autorefresh
		st_autorefresh(interval=30 * 1000, key="market_hours_autorefresh")
		st.sidebar.success("🟢 交易時間中：已開啟自動刷新 (每 30 秒)")
	except ImportError:
		st.sidebar.warning("請在終端機執行 `pip install streamlit-autorefresh` 以啟用自動定時功能")
else:
	st.sidebar.info("🔴 非台股交易時間 (09:00-13:30)：已自動暫停刷新")

# 在側邊欄原本的 if/else 判斷區塊下方加入這兩行：
if st.sidebar.button("🔄 手動更新最新股價", use_container_width=True):
	st.cache_data.clear()
	st.rerun()
# --- 網頁載入時，優先檢查網址是否有 user_id，有的話自動恢復登入與 Supabase 資料 ---
if "line_user_id" not in st.session_state:
	query_user_id = st.query_params.get("user_id")
	if query_user_id:
		st.session_state["line_user_id"] = query_user_id

# 若已登入但 session 沒有持倉，自動從 Supabase 載入最新持倉資料
if st.session_state.get("line_user_id") and "portfolio" not in st.session_state:
	user_data = db.get_user_data(st.session_state["line_user_id"])
	if user_data:
		st.session_state["portfolio"] = {"cash": float(user_data.get("cash", 100000.0)), "holdings": user_data.get("holdings", {})}
# ---- 💾 使用者資料與 Supabase 整合 ----
# 1. 嘗試從 session 恢復狀態
current_user_id = st.session_state.get("line_user_id", "")

client_id = st.secrets.get("LINE_CLIENT_ID", "")
client_secret = st.secrets.get("LINE_CLIENT_SECRET", "")
redirect_uri = st.secrets.get("LINE_REDIRECT_URI", "http://localhost:8501/")
# 2. 處理 LINE Login 授權回傳的 code
query_params = st.query_params
if "code" in query_params and not current_user_id:
	auth_code = query_params["code"]

	token_url = "https://api.line.me/oauth2/v2.1/token"
	headers = {"Content-Type": "application/x-www-form-urlencoded"}
	data = {"grant_type": "authorization_code", "code": auth_code, "redirect_uri": redirect_uri, "client_id": client_id, "client_secret": client_secret}

	res = requests.post(token_url, headers=headers, data=data)
	if res.status_code == 200:
		token_data = res.json()
		id_token = token_data.get("id_token")

		verify_url = "https://api.line.me/oauth2/v2.1/verify"
		verify_res = requests.post(verify_url, data={"id_token": id_token, "client_id": client_id})
		if verify_res.status_code == 200:
			user_info = verify_res.json()
			user_id = user_info.get("sub")
			user_name = user_info.get("name", "用戶")

			st.session_state["line_user_id"] = user_id
			st.session_state["line_user_name"] = user_name
			st.query_params["user_id"] = user_id
			# 從 Supabase 查資料，若無紀錄則自動建立新用戶
			user_record = db.get_user_data(user_id)
			if not user_record:
				db.save_or_update_user(user_id, user_name)
				st.session_state["portfolio"] = {"cash": 100000.0, "holdings": {}}
			else:
				st.session_state["portfolio"] = {"cash": float(user_record.get("cash", 100000.0)), "holdings": user_record.get("holdings", {})}

			st.sidebar.success(f"🎉 歡迎，{user_name}！")
			st.rerun()

# 3. 如果已經登入，確保 Session 載入該使用者的最新資料
if current_user_id and "portfolio" not in st.session_state:
	user_record = db.get_user_data(current_user_id)
	if user_record:
		st.session_state["portfolio"] = {"cash": float(user_record.get("cash", 100000.0)), "holdings": user_record.get("holdings", {})}
	else:
		st.session_state["portfolio"] = {"cash": 100000.0, "holdings": {}}

# ----- 3. 側邊欄 - LINE 官方帳號通知綁定介面 -----
st.sidebar.markdown("---")
st.sidebar.subheader("🔔 LINE 官方帳號通知綁定")

if st.session_state.get("line_user_id"):
	st.sidebar.info(f"✅ 已連結 LINE 帳號：\n{st.session_state.get('line_user_name', '已存取')}")

	# 🚀 發送測試通知
	if st.button("🚀 發送測試通知"):
		notifier = importlib.import_module("notifier")
		importlib.reload(notifier)

	# 接收 send_line_message 回傳的結果（如果是 True 代表成功，如果是字串代表錯誤原因）
	target_user_id = st.session_state.get("line_user_id")
	result = notifier.send_line_message("恭喜！台股戰情室系統已成功連結您的 LINE 官方帳號！", user_id=target_user_id)

	if result is True:
		st.sidebar.success("測試訊息已發送，請查看 LINE！")
	else:
		# 直接把 LINE 回傳的真實錯誤原因印在紅框裡！
		st.sidebar.error(f"發送失敗：{result}")

	# 📊 發送個人持倉戰報至 LINE
	if st.sidebar.button("📊 發送個人持倉戰報至 LINE", use_container_width=True):
		import notifier
		import importlib
		importlib.reload(notifier)
		user_portfolio = st.session_state.get("portfolio", {})
		active_holdings = user_portfolio.get("holdings", {})

		if active_holdings:
			success_count = 0
			for ticker in active_holdings.keys():
				df_temp = dd.fetch_stock_data(ticker)
				if not df_temp.empty:
					signal, price, desc = dd.calculate_signal(df_temp)
					raw_info = active_holdings.get(ticker, {})
					avg_cost = raw_info.get("cost", 0.0) if isinstance(raw_info, dict) else 0.0

					msg = f"\n🔹 [{ticker}] 即時交易戰報\n◆ 當前股價: ${price:.1f}\n"
					if avg_cost > 0:
						pnl = (price - avg_cost) / avg_cost * 100
						msg += f"◆ 庫存報酬: {pnl:+.2f}%\n"
					msg += f"◆ 目前訊號: {signal}\n◆ 狀態: {desc}"

					if notifier.send_line_message(msg):
						success_count += 1
			st.sidebar.success(f"已成功發送 {success_count} 檔持倉戰報！")
		else:
			st.sidebar.warning("目前沒有持股股票！")

	# 🔓 解除綁定 / 登出按鈕
	if st.sidebar.button("🔓 解除綁定 / 登出", use_container_width=True):
		if "user_id" in st.query_params:
			del st.query_params["user_id"]
		if "code" in st.query_params:
			del st.query_params["code"]
		st.session_state.clear()
		st.rerun()

else:
	login_url = ("https://access.line.me/oauth2/v2.1/authorize?"
	             f"response_type=code&client_id={client_id}"
	             f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
	             "&state=quant_trading_state&scope=profile%20openid"
	             "&bot_prompt=normal")

	st.sidebar.markdown(f'''
        <a href="{login_url}" target="_blank">
            <button style="background-color:#06C755;color:white;border:none;padding:10px 16px;border-radius:8px;width:100%;font-weight:bold;cursor:pointer;">
                💬 一鍵加好友綁定 LINE 通知
            </button>
        </a>
    ''',
	                    unsafe_allow_html=True)

# -------------------------------------------------------------------
# 🧰 資產與持倉編輯器（優化介面與成本欄位）
# -------------------------------------------------------------------
st.sidebar.header("🧰 資產與持倉編輯器")

# 取得目前使用者的 portfolio
user_portfolio = st.session_state.get("portfolio", {"cash": 100000.0, "holdings": {}})

# # 1. 現金輸入
cash_input = st.sidebar.number_input("現金總額 (TWD)", value=float(user_portfolio.get("cash", 100000.0)), step=1000.0, help="請輸入目前可用的未分配現金")


st.sidebar.markdown("### 📊 持股明細設定")
st.sidebar.info("💡 **輸入說明**：代碼請加 `.TW`，例如 `2330.TW`（台積電）。若買進「一張」請填寫 `1000` 股。")

# 從 Session 讀取持倉資料，若無資料才給預設值
portfolio = st.session_state.get("portfolio", {})
current_holdings = portfolio.get("holdings", {})

rows = []
for ticker, info in current_holdings.items():
	rows.append({"標的代碼": ticker, "股數": int(info.get("shares", 0)), "買入總成本": float(info.get("total_cost", 0.0))})

if not rows:
	rows = [{"標的代碼": "2330.TW", "股數": 1000, "買入總成本": 980000.0}, {"標的代碼": "0050.TW", "股數": 1000, "買入總成本": 160000.0}]

df_holdings = pd.DataFrame(rows)

# 3. 直觀的表格編輯器設定 (已移除舊版 Streamlit 不支援的參數)
edited_df = st.sidebar.data_editor(
    df_holdings,
    num_rows="dynamic",
    column_config={
        "標的代碼": st.column_config.TextColumn(
            "標的代碼",
            help="台股請務必加上 .TW (例如: 2330.TW)",
            required=True,
        ),
        "股數": st.column_config.NumberColumn(
            "股數",
            help="持有總股數（1張 = 1000股）",
            min_value=1,
            step=1,
            format="%d 股",
            required=True,
        ),
        "買入總成本": st.column_config.NumberColumn(
            "買入總成本 ($)",
            help="該筆買入的實際總金額（含手續費）",
            min_value=0.0,
            step=0.01,
            format="$%.2f",
            required=True,
        ),
    },
    hide_index=True,
)

# 4. 儲存與同步設定
# 4. 儲存與同步設定
if st.sidebar.button("💾 儲存持倉與現金設定"):
	new_holdings = {}
	for idx, row in edited_df.dropna(subset=["標的代碼"]).iterrows():
		ticker = str(row["標的代碼"]).strip().upper()
		if not ticker.endswith(".TW") and not ticker.endswith(".TWO"):
			ticker += ".TW"

		shares = int(row["股數"])
		total_cost_input = float(row["買入總成本"])

		avg_cost = total_cost_input / shares if shares > 0 else 0.0

		new_holdings[ticker] = {
		    "shares": shares,
		    "cost": avg_cost,
		    "total_cost": total_cost_input
		}

	# 更新 Session State 中的 portfolio
	st.session_state["portfolio"] = {"cash": cash_input, "holdings": new_holdings}

	# 若已登入 LINE，同步寫入 Supabase 資料庫
	if st.session_state.get("line_user_id"):
		db.save_or_update_user(line_user_id=st.session_state["line_user_id"],
		                       line_user_name=st.session_state.get("line_user_name", "用戶"),
		                       cash=cash_input,
		                       holdings=new_holdings)
	st.sidebar.success("✅ 持倉與成本資料已成功儲存！")
	st.rerun()

# ----------------
# 主頁面：儀表板與個股獨立 K 線空間
# ----------------
st.markdown(f"**最新更新時間**：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")

# 🟢 修改後
user_portfolio = st.session_state.get("portfolio", {})
active_holdings = user_portfolio.get("holdings", {})
active_tickers = list(active_holdings.keys())

# 🟢 修改後
cash = user_portfolio.get("cash", 100000.0)

# 計算各標的最新市值與總市值
portfolio_data = []
total_stock_value = 0.0

for ticker in active_tickers:
	raw_info = active_holdings.get(ticker, {})
	s_held = int(raw_info.get("shares", 0)) if isinstance(raw_info, dict) else int(raw_info)

	df_temp = dd.fetch_stock_data(ticker)
	if not df_temp.empty:
		last_price = df_temp["Close"].iloc[-1]
		mkt_val = last_price * s_held
		total_stock_value += mkt_val
		if mkt_val > 0:
			portfolio_data.append({"標的": ticker, "市值": mkt_val})

total_portfolio_value = total_stock_value + cash
cash_ratio = (cash / total_portfolio_value * 100) if total_portfolio_value > 0 else 0

# --- 2. 頂部 4 欄式卡片 ---
col1, col2, col3, col4 = st.columns(4)

with col1:
	st.metric("大盤趨勢", "多頭 🟢")

with col2:
	st.metric("總資產淨值 (TWD)", f"${total_portfolio_value:,.0f}")

with col3:
	st.metric("股票總市值", f"${total_stock_value:,.0f}")

with col4:
	st.metric("可用現金", f"${cash:,.0f}", delta=f"現金水位 {cash_ratio:.1f}%")

st.markdown("---")

# --- 3. 圓餅圖（含現金）---
import plotly.express as px

# 加入現金切片
if cash > 0:
	portfolio_data.append({"標的": "💵 可用現金", "市值": cash})

if portfolio_data:
	import pandas as pd
	df_pie = pd.DataFrame(portfolio_data)
	fig_pie = px.pie(df_pie, names="標的", values="市值", title="📊 庫存資產與現金配置占比", hole=0.4)
	st.plotly_chart(fig_pie, use_container_width=True)
	# --- 貼到這邊 ---
	# --- 大盤與持倉走勢對比圖 ---
	st.subheader("📈 總資產 vs. 大盤 (0050) 走勢對比")

	# 繪製總資產與 0050 對比圖
	if 'portfolio_history_df' in locals() and not portfolio_history_df.empty:
		fig_compare = px.line(portfolio_history_df,
		                      x='date',
		                      y=['total_asset', '0050_normalized'],
		                      labels={
		                          'value': '資產價值 / 指標',
		                          'variable': '類別'
		                      },
		                      title="策略總資產 vs. 0050 績效對比")
		fig_compare.for_each_trace(lambda t: t.update(name="我的總資產" if t.name == "total_asset" else "大盤 (0050)"))
		st.plotly_chart(fig_compare, use_container_width=True)
	else:
		st.info("💡 尚無足夠的歷史資產紀錄以繪製大盤對比圖")
if not active_tickers:
	st.info("目前尚無持倉，請至左側「資產與持倉編輯器」新增股票代碼！")
else:
	st.subheader("📌 個股專屬 K 線與即時訊號空間")

	tabs = st.tabs([f"📊 {ticker}" for ticker in active_tickers])

for i, ticker in enumerate(active_tickers):
	with tabs[i]:
		with st.spinner(f"正在抓取 {ticker} 最新報價與 K 線..."):
			# 1. 抓取該檔股票資料
			df = dd.fetch_stock_data(ticker)
			signal, price, desc = dd.calculate_signal(df)

			# 2. 計算該檔股票持有成本與損益
			raw_holding_info = active_holdings.get(ticker, {})
			if isinstance(raw_holding_info, dict):
				shares_held = int(raw_holding_info.get("shares", 0))
				avg_cost = float(raw_holding_info.get("cost", 0.0))
				# 優先拿紀錄的總成本，若無則用 avg_cost * shares_held 計算
				total_cost = float(raw_holding_info.get("total_cost", avg_cost * shares_held))
			else:
				shares_held = int(raw_holding_info)
				avg_cost = 0.0
				total_cost = 0.0

			# 3. 計算損益
			market_val = price * shares_held
			unrealized_pnl = market_val - total_cost
			pnl_pct = (unrealized_pnl / total_cost * 100) if total_cost > 0 else 0.0

			# 4. 顯示 5 個卡片欄位
			sc1, sc2, sc3, sc4, sc5 = st.columns(5)
			sc1.metric("當前股價", f"${price:.2f}")
			sc2.metric("持有股數", f"{shares_held:,} 股")
			sc3.metric("買入總成本", f"${avg_cost:.2f}" if avg_cost > 0 else "未設定")
			sc4.metric(
			    "未實現損益",
			    f"${unrealized_pnl:,.0f}",
			    delta=f"{pnl_pct:+.2f}%"
			)
			sc5.metric("庫存市值", f"${market_val:,.0f}")

			st.caption(f"**訊號說明**：{desc}")

			# 5. 畫出該檔股票的 K 線圖 (保留原本指標線 + 疊加買賣標籤與成本線)
			if not df.empty:
				# 1. 呼叫你原本的繪圖函式，保留所有原本的均線/買賣線
				fig = dd.plot_candlestick(df, ticker)

				# 2. 疊加你的買入成本線
				if avg_cost > 0:
					fig.add_hline(
					 y=avg_cost, line_dash="dash", line_color="gold", line_width=2,
					 annotation_text=f"你的買入成本 ${avg_cost:.1f}",
					 annotation_position="bottom right"
					)

				# 3. 疊加明確的買賣訊號文字與箭頭
				if 'signal' in df.columns:
					import plotly.graph_objects as go
					l_col = 'Low' if 'Low' in df.columns else 'low'
					h_col = 'High' if 'High' in df.columns else 'high'

					buy_signals = df[df['signal'] == 'BUY']
					if not buy_signals.empty:
						fig.add_trace(go.Scatter(
						 x=buy_signals.index, y=buy_signals[l_col] * 0.97,
						 mode='markers+text',
						 marker=dict(symbol='triangle-up', size=16, color='#00FF7F'),
						 text="🟢 買進", textposition="bottom center", name='買進訊號'
						))

					sell_signals = df[df['signal'] == 'SELL']
					if not sell_signals.empty:
						fig.add_trace(go.Scatter(
						 x=sell_signals.index, y=sell_signals[h_col] * 1.03,
						 mode='markers+text',
						 marker=dict(symbol='triangle-down', size=16, color='#FF4500'),
						 text="🔴 賣出", textposition="top center", name='賣出訊號'
						))

				st.plotly_chart(fig, use_container_width=True)
		# 6. AI 預測 (含判斷依據說明)
		try:
			prob, msg = predict_future_signal(df)
		except Exception as e:
			prob, msg = None, f"發生錯誤: {str(e)}"

		if prob is not None:
			st.markdown("### 🤖 AI 趨勢預測 (未來 5 天)")
			col1, col2 = st.columns([1, 2])
			with col1:
				st.metric(label="AI 預估上漲機率", value=f"{prob}%")
			with col2:
				if prob >= 60:
					st.success(f"🔥 **AI 訊號：強勢偏多**\n\n📌 **判斷依據**：{msg}")
				elif prob <= 40:
					st.error(f"⚠️ **AI 訊號：弱勢偏空**\n\n📌 **判斷依據**：{msg}")
				else:
					st.info(f"🌐 **AI 訊號：盤整觀望**\n\n📌 **判斷依據**：{msg}")
		else:
			st.warning(f"⚠️ {ticker} 預測未啟動原因：{msg}")
# -------------------------------------------------------------------
# 📰 財報與即時新聞專區
# -------------------------------------------------------------------
st.markdown("---")
st.header("📰 財報與即時新聞專區")

news_tab1, news_tab2 = st.tabs(["💼 個人持股新聞", "🌐 全市場焦點新聞"])

# --- 頁籤 1：個人持股新聞 ---
with news_tab1:
	if active_tickers:
		selected_holding = st.selectbox("選擇要查看新聞的持股標的：", active_tickers)
		st.subheader(f"🔍 {selected_holding} 最新相關新聞")

		news_items = get_stock_news(selected_holding)

		if news_items:
			for item in news_items:
				st.markdown(f"• **[{item['title']}]({item['link']})**")
				st.caption(f"發布時間：{item['published']}")
		else:
			st.info("目前無相關新聞報導。")
	else:
		st.info("目前無持股，請至左側設定新增標的。")

# --- 頁籤 2：全市場 / 熱門股新聞 ---
with news_tab2:
	market_keyword = st.selectbox(
	    "選擇市場熱門主題：",
	    ["台股 大盤", "半導體 晶圓代工", "AI 概念股", "美聯準會 降息"],
	)
	st.subheader(f"🔥 {market_keyword} 最新動態")

	market_news = get_stock_news(market_keyword)

	if market_news:
		for item in market_news:
			st.markdown(f"• **[{item['title']}]({item['link']})**")
			st.caption(f"發布時間：{item['published']}")
	else:
		st.info("目前無相關新聞報導。")
# ---- 📅 公司除權息與股利紀錄 ----
st.markdown("---")
st.subheader("📅 公司除權息與股利紀錄")

# 自動取得目前選取的股票代碼 (對應新聞選單的 selected_holding)
target_ticker = selected_holding if ('selected_holding' in locals() and selected_holding) else "2330.TW"

events_df = dd.fetch_stock_events(target_ticker)

if not events_df.empty:
	col1, col2 = st.columns([1, 2])
	with col1:
		latest_div = events_df.iloc[0]['每股配息(元)']
		st.metric(label="最近一次配息金額", value=f"${latest_div} 元")
	with col2:
		st.dataframe(events_df, use_container_width=True, hide_index=True)
else:
	st.info("尚無此標的之除權息紀錄數據。")


# ---- 📊 市場籌碼、匯率與填息狀態 ----
st.markdown("---")
st.subheader("💡 籌碼量能與總體指標")

target_tk = selected_holding if ('selected_holding' in locals() and selected_holding) else "2330.TW"
macro_data = dd.fetch_market_macro_and_volume(target_tk)

if macro_data:
	c1, c2, c3 = st.columns(3)

	with c1:
		# 台幣匯率 (升值為負/貶值為正)
		fx_label = "台幣升值 📈" if macro_data['fx_change'] < 0 else "台幣貶值 📉"
		st.metric(
		    label=f"美元/台幣匯率 ({fx_label})",
		    value=f"${macro_data['fx_current']}",
		    delta=f"{macro_data['fx_change']} ({macro_data['fx_pct']}%)",
		    delta_color="inverse"
		)

	with c2:
		# 成交量與金額
		st.metric(
		    label="今日成交量 / 成交金額",
		    value=f"{macro_data['vol_shares']:,} 張",
		    delta=f"約 {macro_data['turnover']} 億元"
		)

	with c3:
		# 填息進度
		st.metric(
		    label="最近一次除息狀況",
		    value=macro_data['fill_status']
		)

# ---- 🏛️ 三大法人籌碼、策略績效與位階試算器 ----
st.markdown("---")
st.subheader("🏛️ 機構籌碼、策略勝率與風控部位試算")

target_tk = selected_holding if ('selected_holding' in locals() and selected_holding) else "2330.TW"

# 1. 抓取法人數據與回測數據
inst_data = dd.fetch_institutional_investors(target_tk)
# 假設 df 是你主畫面的歷史價格 DataFrame
perf_data = dd.calculate_backtest_performance(df) if 'df' in locals() and not df.empty else {}

tab1, tab2, tab3 = st.tabs(["📊 三大法人買賣超", "📈 策略歷史績效 (回測)", "🧮 幾張才安全？部位試算"])

with tab1:
	if inst_data:
		i1, i2, i3, i4 = st.columns(4)
		i1.metric("外資買賣超", f"{inst_data.get('外資', 0):,} 張")
		i2.metric("投信買賣超", f"{inst_data.get('投信', 0):,} 張")
		i3.metric("自營商買賣超", f"{inst_data.get('自營商', 0):,} 張")
		i4.metric("三大法人合計", f"{inst_data.get('合計', 0):,} 張")
	else:
		st.info("尚無三大法人詳細籌碼資料。")

with tab2:
	if perf_data:
		p1, p2, p3 = st.columns(3)
		p1.metric("歷史勝率 (MA20突破)", f"{perf_data.get('win_rate', 0)} %")
		p2.metric("歷史最大回撤 (MDD)", f"{perf_data.get('mdd', 0)} %")
		p3.metric("累積總報酬率", f"{perf_data.get('total_return', 0)} %")
	else:
		st.info("無足夠歷史資料進行回測。")

with tab3:
	st.write("##### 🛡️ 風險控管：根據個人資金規模計算建議買進張數")
	col_a, col_b, col_c = st.columns(3)
	with col_a:
		capital = st.number_input("您的總交易資金 (TWD)", value=1000000, step=50000)
	with col_b:
		max_risk_pct = st.slider("單筆最大可承受虧損比例 (%)", 1.0, 5.0, 2.0)
	with col_c:
		# 取當前收盤價估算
		current_p = df['Close'].iloc[-1] if ('df' in locals() and not df.empty) else 100.0
		st.write(f"當前參考股價：**${current_p} 元**")

	# 假設停損設 5%
	stop_loss_pct = 0.05
	risk_amount = capital * (max_risk_pct / 100)
	loss_per_share = current_p * stop_loss_pct
	max_shares = int(risk_amount / loss_per_share) if loss_per_share > 0 else 0
	max_lots = max_shares // 1000

	st.success(f"💡 建議下單上限：**{max_lots} 張**（相當於 {max_shares:,} 股）。若觸發 5% 停損，最大虧損金額約控制在 **${int(risk_amount):,} 元**。")


# ---- 🏷️ 估值河流圖、過熱警示與 Excel 日報匯出 ----
st.markdown("---")
st.subheader("🎯 估值位階、乖離警示與 Excel 報表匯出")

target_tk = selected_holding if ('selected_holding' in locals() and selected_holding) else "2330.TW"
val_data = dd.calculate_valuation_and_bias(df, target_tk) if 'df' in locals() and not df.empty else {}

col_val1, col_val2, col_val3 = st.columns([1, 1, 1])

with col_val1:
	st.metric(label="20日 MA 乖離率 (BIAS)",
	          value=f"{val_data.get('bias_20', 0)} %",
	          delta=val_data.get('bias_status', '正常'),
	          help="【乖離率 BIAS】代表當前股價偏離 20 日均線（月線）的幅度。\n• 正乖離過大 (>8%)：代表短線漲多，有回檔風險。\n• 負乖離過大 (<-8%)：代表短線跌深，可能迎來技術性反彈。")

with col_val2:
	st.metric(label="本益比 (P/E) 估值位階",
	          value=f"{val_data.get('pe_ratio', 'N/A')}",
	          delta=val_data.get('pe_status', '無資料'),
	          delta_color="off",
	          help="【本益比 P/E】衡量買進該股票需要幾年才能回本 (股價 ÷ 近四年 EPS)。\n• 便宜區：本益比低於 12 倍。\n• 合理區：本益比介於 12 ~ 20 倍。\n• 昂貴區：本益比高於 20 倍。")

with col_val3:
	st.write("##### 📄 每日盯盤日報匯出")
	if 'df' in locals() and not df.empty:
		excel_data = dd.generate_excel_report(df, target_tk)
		st.download_button(label="📥 下載 Excel 分析日報",
		                   data=excel_data,
		                   file_name=f"{target_tk}_daily_report.xlsx",
		                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		                   help="點擊打包下載當前標的近 30 日歷史行情與分析數據。")
	else:
		st.info("暫無數據供下載")

# ---- 📚 專業名詞小字典 (可折疊欄位) ----
with st.expander("📚 點我查看此區塊專業術語解析"):
	st.markdown("""
    * **20日 MA (月線)**：過去 20 個交易日的平均收盤價，通常作為短線強弱的分水嶺。
    * **乖離率 (BIAS)**：`(股價 - 均線) ÷ 均線 × 100%`。當股價走得太快、離均線太遠時，會有拉回均線修正的引力。
    * **本益比 (P/E Ratio)**：評估股價是貴還是便宜的經典指標。數值越低代表股票越便宜、回本時間越短。
    """)
