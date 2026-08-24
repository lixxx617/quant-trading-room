import datetime
import requests
import pandas as pd
import yfinance as yf


def send_line_notification(channel_access_token: str, user_id: str, message: str) -> tuple[bool, str]:
	"""
    發送 LINE Messaging API 訊息
    """
	if not channel_access_token or not user_id:
		return False, "未設定 Channel Access Token 或 User ID"

	url = "https://api.line.me/v2/bot/message/push"
	headers = {
	    "Content-Type": "application/json",
	    "Authorization": f"Bearer {channel_access_token}",
	}
	payload = {
	    "to": user_id,
	    "messages": [{
	        "type": "text",
	        "text": message
	    }],
	}

	try:
		response = requests.post(url, json=payload, headers=headers, timeout=10)
		if response.status_code == 200:
			return True, "LINE 測試訊號發送成功！"
		else:
			return False, f"發送失敗 (HTTP {response.status_code}): {response.text}"
	except Exception as e:
		return False, f"連線異常: {str(e)}"


def fetch_stock_data(ticker: str = "2330.TW", period: str = "1y") -> pd.DataFrame:
	try:
		import yfinance as yf

		# 1. 抓取歷史 K 線
		df = yf.download(ticker, period=period, progress=False)
		if df.empty:
			return pd.DataFrame()

		if isinstance(df.columns, pd.MultiIndex):
			df.columns = df.columns.get_level_values(0)

		df.columns = [str(c).title() for c in df.columns]
		df = df.dropna(subset=["Close"])

		# 2. 強制抓取即時行情 (Fast Info)，把最後一筆誤抓成 2375 的價格修正為最新的 2410
		try:
			t = yf.Ticker(ticker)
			realtime_price = t.fast_info.get('last_price') or t.fast_info.get('lastPrice')
			if realtime_price and not df.empty:
				df.iloc[-1, df.columns.get_loc('Close')] = round(float(realtime_price), 2)
		except Exception as inner_e:
			print(f"即時價格修正失敗: {inner_e}")

		return df
	except Exception as e:
		print(f"抓取 {ticker} 數據失敗: {e}")
		return pd.DataFrame()

def calculate_signal(df: pd.DataFrame) -> tuple[str, float | None, str]:
	"""
    計算唐奇安通道 (20進/20出) 與 200MA 策略訊號
    """
	if df.empty:
		return "no_action", None, "無歷史 K 線數據"

	# 清除 Close 空值
	df = df.dropna(subset=["Close"]).copy()

	if len(df) < 20:
		return "no_action", None, "數據列數不足 20 日，無法計算訊號"

	# 計算 20 日最高/最低與 200MA
	df["20_Upper"] = df["High"].rolling(window=20).max().shift(1)
	df["20_Lower"] = df["Low"].rolling(window=20).min().shift(1)

	if len(df) >= 200:
		df["200_MA"] = df["Close"].rolling(window=200).mean()
	else:
		df["200_MA"] = None

	latest = df.iloc[-1]

	# 1. 在 Terminal 印出最後一筆 K 線的日期與所有欄位，方便檢查
	print(f"\n[除錯資訊] 抓到的最後一筆日期: {latest.name}, 價格資料:\n{latest}")

	# 2. 優先抓取 Close，若抓不到則嘗試小寫 close
	raw_price = latest.get("Close", latest.get("close", 0.0))
	price = float(raw_price) if pd.notnull(raw_price) else 0.0

	raw_upper = latest.get("20_Upper")
	upper = float(raw_upper) if pd.notnull(raw_upper) else None

	raw_lower = latest.get("20_Lower")
	lower = float(raw_lower) if pd.notnull(raw_lower) else None

	raw_ma200 = latest.get("200_MA")
	ma200 = float(raw_ma200) if pd.notnull(raw_ma200) else None

	if price == 0.0:
		return "no_action", None, "最新收盤價無效"

	# 判斷策略訊號
	if upper is not None and price > upper and (ma200 is None or price > ma200):
		return "BUY", price, f"價格 (${price:.2f}) 突破 20 日高點 (${upper:.2f})，趨勢偏多"
	elif lower is not None and price < lower:
		return "SELL", price, f"價格 (${price:.2f}) 跌破 20 日低點 (${lower:.2f})，觸發出場條件"
	else:
		return "NO_ACTION", price, "未觸發進出場條件"

import json
import os

# ----------------------------------------------------
# 補上 app.py 所需的資產與持倉狀態讀取/儲存函式
# ----------------------------------------------------


def load_portfolio_state(filepath="portfolio_state.json"):
	"""讀取持倉與資產狀態 JSON 檔"""
	if os.path.exists(filepath):
		try:
			with open(filepath, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			pass
	# 預設值（包含 LINE 設定）
	return {
	    "cash": 1000000,
	    "line_token": "",
	    "line_user_id": "",
	    "holdings": {
	        "2330.TW": 1000,
	        "2317.TW": 1000,
	        "2002.TW": 1000,
	        "00992A.TW": 1000,
	        "0056.TW": 1000,
	        "0050.TW": 1000,
	        "00646.TW": 1000,
	    }
	}


def save_portfolio_state(state, filepath="portfolio_state.json"):
	"""儲存持倉與資產狀態至 JSON 檔"""
	try:
		with open(filepath, "w", encoding="utf-8") as f:
			json.dump(state, f, ensure_ascii=False, indent=4)
		return True
	except Exception as e:
		print(f"Error saving state: {e}")
		return False


import plotly.graph_objects as go

# ----------------------------------------------------
# 繪製 K 線圖與唐奇安通道、200MA 繪圖函式
# ----------------------------------------------------


def plot_candlestick(df, ticker, avg_cost=0.0, stop_loss_pct=0.05, take_profit_pct=0.15):
	"""繪製 K 線圖與唐奇安通道、200MA 指標"""
	df_plot = df.copy()
	df_plot["20_Upper"] = df_plot["High"].rolling(window=20).max().shift(1)
	df_plot["20_Lower"] = df_plot["Low"].rolling(window=20).min().shift(1)

	if len(df_plot) >= 200:
		df_plot["200_MA"] = df_plot["Close"].rolling(window=200).mean()

	fig = go.Figure()

	# K線
	fig.add_trace(go.Candlestick(
	    x=df_plot.index,
	    open=df_plot["Open"],
	    high=df_plot["High"],
	    low=df_plot["Low"],
	    close=df_plot["Close"],
	    name="K線",
	))

	# 20日高點 (上軌)
	if "20_Upper" in df_plot.columns:
		fig.add_trace(go.Scatter(
		    x=df_plot.index,
		    y=df_plot["20_Upper"],
		    line=dict(color="red", width=1.5, dash="dash"),
		    name="20日高點(上軌)",
		))

	# 20日低點 (下軌)
	if "20_Lower" in df_plot.columns:
		fig.add_trace(go.Scatter(
		    x=df_plot.index,
		    y=df_plot["20_Lower"],
		    line=dict(color="green", width=1.5, dash="dash"),
		    name="20日低點(下軌)",
		))

	# 200MA 均線
	if "200_MA" in df_plot.columns:
		fig.add_trace(go.Scatter(
		    x=df_plot.index,
		    y=df_plot["200_MA"],
		    line=dict(color="orange", width=2),
		    name="200日均線",
		))

	fig.update_layout(
	    title=f"{ticker} 互動式 K 線圖",
	    yaxis_title="價格 (TWD)",
	    xaxis_title="日期",
	    template="plotly_dark",
	    height=500,
	    xaxis_rangeslider_visible=False,
	)

	if avg_cost > 0:
		stop_loss_price = avg_cost * (1 - stop_loss_pct)
		take_profit_price = avg_cost * (1 + take_profit_pct)

		# 停損線 (紅色虛線)
		fig.add_hline(y=stop_loss_price,
		              line_dash="dash",
		              line_color="red",
		              annotation_text=f"停損價 ${stop_loss_price:.1f} (-{stop_loss_pct*100:.0f}%)",
		              annotation_position="bottom right")

		# 停利線 (綠色虛線)
		fig.add_hline(y=take_profit_price,
		              line_dash="dash",
		              line_color="green",
		              annotation_text=f"停利價 ${take_profit_price:.1f} (+{take_profit_pct*100:.0f}%)",
		              annotation_position="top right")

	return fig


# 正確的對齊樣子（頂格靠左）：
def fetch_stock_events(ticker: str = "2330.TW"):
	"""抓取除權息日與歷史股利資訊"""
	try:
		import yfinance as yf
		t = yf.Ticker(ticker)

		# 抓取歷史股利紀錄
		divs = t.dividends
		if not divs.empty:
			div_df = divs.tail(5).reset_index()
			div_df['Date'] = div_df['Date'].dt.strftime('%Y-%m-%d')
			div_df = div_df.sort_values(by='Date', ascending=False)
			div_df.columns = ['除息日期', '每股配息(元)']
		else:
			div_df = pd.DataFrame()

		return div_df
	except Exception as e:
		print(f"抓取 {ticker} 除息事件失敗: {e}")
		return pd.DataFrame()


def fetch_market_macro_and_volume(ticker: str = "2330.TW"):
	"""抓取成交量能、美元匯率與填息狀況"""
	try:
		import yfinance as yf
		import pandas as pd

		# 1. 抓取匯率 (USD/TWD)
		fx = yf.Ticker("USDTWD=X").history(period="5d")
		fx_current = fx['Close'].iloc[-1]
		fx_prev = fx['Close'].iloc[-2]
		fx_change = fx_current - fx_prev
		fx_pct = (fx_change / fx_prev) * 100

		# 2. 抓取個股成交資訊
		stk = yf.Ticker(ticker)
		df = stk.history(period="5d")
		latest_close = df['Close'].iloc[-1]
		latest_vol = df['Volume'].iloc[-1]
		vol_shares = int(latest_vol / 1000) # 轉為張數
		turnover_hundred_m = (latest_close * latest_vol) / 100000000 # 轉為億元

		# 3. 填息進度計算 (最近一次除息)
		divs = stk.dividends
		fill_status = "無除息資料"
		if not divs.empty:
			last_div_date = divs.index[-1]
			last_div_amt = divs.iloc[-1]

			# 取得除息日前一天的收盤價 (基準價)
			hist_all = stk.history(period="1y")
			pre_div_data = hist_all[hist_all.index < last_div_date]

			if not pre_div_data.empty:
				pre_div_price = pre_div_data['Close'].iloc[-1]
				target_price = pre_div_price # 填息目標價
				if latest_close >= target_price:
					fill_status = f"已填息 (配息 ${last_div_amt} 元)"
				else:
					diff = round(target_price - latest_close, 2)
					fill_status = f"尚未填息 (距填息還差 ${diff} 元)"

		return {
		    "fx_current": round(fx_current, 3),
		    "fx_change": round(fx_change, 3),
		    "fx_pct": round(fx_pct, 2),
		    "vol_shares": vol_shares,
		    "turnover": round(turnover_hundred_m, 2),
		    "fill_status": fill_status
		}
	except Exception as e:
		print(f"抓取宏觀與量能資料失敗: {e}")
		return None


# ---- 1. 三大法人買賣超 (預估/簡易模擬數據) ----
def fetch_institutional_investors(ticker: str = "2330.TW"):
	"""抓取或估算三大法人買賣超張數"""
	try:
		import yfinance as yf
		stk = yf.Ticker(ticker)
		hist = stk.history(period="5d")
		if hist.empty:
			return {}

		# 利用成交量與漲跌幅進行籌碼估算（可改接 TWSE Open Data）
		last_vol = hist['Volume'].iloc[-1] / 1000
		pct_change = hist['Close'].pct_change().iloc[-1]

		foreign = int(last_vol * 0.35 * (1 if pct_change > 0 else -1))
		investment_trust = int(last_vol * 0.12 * (1 if pct_change > 0 else -1))
		dealer = int(last_vol * 0.08 * (1 if pct_change > 0 else -1))

		return {"外資": foreign, "投信": investment_trust, "自營商": dealer, "合計": foreign + investment_trust + dealer}
	except Exception as e:
		print(f"抓取法人籌碼失敗: {e}")
		return {}


# ---- 2. 歷史回測與勝率計算 ----
def calculate_backtest_performance(df):
	"""根據歷史數據計算 MA 策略的勝率與最大回撤 (MDD)"""
	try:
		if df.empty or 'Close' not in df.columns:
			return {}

		# 簡單均線策略擬真
		df = df.copy()
		df['MA20'] = df['Close'].rolling(20).mean()
		df['Signal'] = (df['Close'] > df['MA20']).astype(int)
		df['Returns'] = df['Close'].pct_change()
		df['Strat_Returns'] = df['Returns'] * df['Signal'].shift(1)

		trades = df[df['Strat_Returns'] != 0]['Strat_Returns']
		if trades.empty:
			return {"win_rate": 0, "mdd": 0, "total_return": 0}

		win_rate = round((trades > 0).mean() * 100, 1)
		cum_returns = (1 + df['Strat_Returns'].fillna(0)).cumprod()
		peak = cum_returns.cummax()
		drawdown = (cum_returns - peak) / peak
		mdd = round(drawdown.min() * 100, 1)
		total_ret = round((cum_returns.iloc[-1] - 1) * 100, 1)

		return {"win_rate": win_rate, "mdd": mdd, "total_return": total_ret}
	except Exception as e:
		print(f"回測運算失敗: {e}")
		return {}


# ---- 1. 估值與乖離率指標計算 ----
def calculate_valuation_and_bias(df, ticker: str = "2330.TW"):
	"""計算乖離率 (BIAS) 與本益比估值區間 (P/E Band)"""
	try:
		if df.empty or 'Close' not in df.columns:
			return {}

		import yfinance as yf
		stk = yf.Ticker(ticker)
		info = stk.info

		current_price = df['Close'].iloc[-1]

		# 乖離率計算 (以 20 日 MA 為基準)
		ma20 = df['Close'].rolling(20).mean().iloc[-1]
		bias_20 = round(((current_price - ma20) / ma20) * 100, 2)

		# 乖離率狀態警示
		bias_status = "正常區間"
		if bias_20 > 8:
			bias_status = "⚠️ 短線過熱 (正乖離過大)"
		elif bias_20 < -8:
			bias_status = "💡 潛在超賣 (負乖離過大)"

		# 本益比估值區間 (P/E Band)
		pe_ratio = info.get('trailingPE', None)
		eps = info.get('trailingEps', None)

		pe_status = "無 PE 資料"
		if pe_ratio and eps and eps > 0:
			cheap_price = round(eps * 12, 1)  # 12x 便宜價
			fair_price = round(eps * 16, 1)  # 16x 合理價
			expensive_price = round(eps * 20, 1)  # 20x 昂貴價

			if current_price < cheap_price:
				pe_status = f"🟢 便宜區 (P/E {round(pe_ratio,1)})"
			elif current_price > expensive_price:
				pe_status = f"🔴 昂貴區 (P/E {round(pe_ratio,1)})"
			else:
				pe_status = f"🟡 合理區 (P/E {round(pe_ratio,1)})"

		return {"bias_20": bias_20, "bias_status": bias_status, "pe_status": pe_status, "pe_ratio": round(pe_ratio, 2) if pe_ratio else "N/A"}
	except Exception as e:
		print(f"計算估值與乖離率失敗: {e}")
		return {}


# ---- 2. 每日盯盤日報 Excel 匯出功能 ----
def generate_excel_report(df, ticker: str = "2330.TW"):
	"""將當前標的數據與技術指標整理打包成漂亮的中文 Excel"""
	import io
	import pandas as pd

	output = io.BytesIO()

	# 複製數據並計算技術指標
	report_df = df.tail(30).copy()
	report_df['MA20'] = df['Close'].rolling(20).mean().tail(30)
	report_df['乖離率(%)'] = ((report_df['Close'] - report_df['MA20']) / report_df['MA20'] * 100).round(2)
	report_df['成交張數'] = (report_df['Volume'] / 1000).astype(int)

	# 整理欄位與順序
	report_df = report_df[['Open', 'High', 'Low', 'Close', '成交張數', 'MA20', '乖離率(%)']]
	report_df.columns = ['開盤價', '最高價', '最低價', '收盤價', '成交張數', '20日均線', '20日乖離率(%)']

	# 重設索引讓日期成為獨立一欄並格式化
	report_df = report_df.reset_index()
	if 'Date' in report_df.columns:
		report_df['Date'] = report_df['Date'].dt.strftime('%Y-%m-%d')
		report_df.rename(columns={'Date': '交易日期'}, inplace=True)

	with pd.ExcelWriter(output, engine='openpyxl') as writer:
		report_df.to_excel(writer, sheet_name='近30日分析日報', index=False)

	output.seek(0)
	return output
