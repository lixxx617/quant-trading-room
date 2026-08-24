import json
import os
from datetime import datetime
import dashboard_data as dd


def run_daily_monitor():
	print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行每日盤後掃描...")

	# 1. 讀取持倉狀態與 LINE 設定
	state_path = "portfolio_state.json"
	if not os.path.exists(state_path):
		print("錯誤：找不到 portfolio_state.json 檔案")
		return

	with open(state_path, "r", encoding="utf-8") as f:
		current_state = json.load(f)

	line_token = current_state.get("line_token", "")
	line_user_id = current_state.get("line_user_id", "")
	active_holdings = current_state.get("holdings", {})

	if not line_token or not line_user_id:
		print("錯誤：未設定 LINE Token 或 User ID")
		return

	# 2. 逐一檢查持倉與訊號
	alerts = []
	for ticker, raw_info in active_holdings.items():
		df_temp = dd.fetch_stock_data(ticker)
		if df_temp.empty:
			continue

		signal, price, desc = dd.calculate_signal(df_temp)
		avg_cost = raw_info.get("cost", 0.0) if isinstance(raw_info, dict) else 0.0

		# 計算停損停利價位
		stop_loss_price = avg_cost * 0.95 if avg_cost > 0 else 0
		take_profit_price = avg_cost * 1.15 if avg_cost > 0 else 0

		# 判斷是否需要發送警報
		is_alert = False
		alert_reason = ""

		if signal == "買進":
			is_alert = True
			alert_reason = "🚀 突破買進訊號"
		elif stop_loss_price > 0 and price <= stop_loss_price:
			is_alert = True
			alert_reason = "⚠️ 觸發 5% 停損"
		elif take_profit_price > 0 and price >= take_profit_price:
			is_alert = True
			alert_reason = "🎉 觸發 15% 停利"

		if is_alert:
			pnl_str = f"({(price - avg_cost) / avg_cost * 100:+.2f}%)" if avg_cost > 0 else ""
			alerts.append(f"📌 【{ticker}】{alert_reason}\n"
			              f"  現價：${price:.1f} | 成本：${avg_cost:.1f} {pnl_str}\n"
			              f"  說明：{desc}")

	# 3. 如果有觸發警報，發送 LINE 通知
	if alerts:
		msg = f"\n🔔 【盤後訊號觸發警報】\n───────────────\n" + "\n\n".join(alerts)
		success, _ = dd.send_line_notification(line_token, line_user_id, msg)
		if success:
			print("已成功發送警報至 LINE！")
	else:
		print("今日無觸發突破或停損/停利訊號。")


if __name__ == "__main__":
	run_daily_monitor()


# ---- 🔔 開盤 LINE 通知功能 ----
def send_market_open_notification():
	"""發送台股開盤提醒至 LINE"""
	now = datetime.now()
	# 0~4 代表週一至週五
	if now.weekday() < 5:
		# 讀取 LINE 設定
		state_path = "portfolio_state.json"
		if os.path.exists(state_path):
			with open(state_path, "r", encoding="utf-8") as f:
				current_state = json.load(f)

			line_token = current_state.get("line_token", "")
			line_user_id = current_state.get("line_user_id", "")

			if line_token and line_user_id:
				import notifier
				msg = ("\n🔔【台股開盤提醒】\n"
				       "台股市場已開盤 (09:00)！\n"
				       "量化交易系統已啟動即時監控與自動刷新，祝您今日交易順利！")
				notifier.send_line_message(line_token, line_user_id, msg)
				print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 成功發送開盤 LINE 通知！")


# 若檔案底部有 schedule 設定，加上這行：
import schedule

schedule.every().day.at("09:00").do(send_market_open_notification)
