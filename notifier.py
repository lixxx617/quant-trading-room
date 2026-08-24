"""
notifier.py
自動推播模組：把每日訊號報告發送到 Telegram Bot 和／或 LINE。

★ 重要說明：LINE Notify 已於 2025-03-31 正式終止服務
------------------------------------------------------
官方公告：https://notify-bot.line.me/closing-announce
舊版 LINE Notify 的 Token 已全數失效，無法再用它推播。本模組改用
LINE 官方帳號的 Messaging API push message 端點取代（見 config.py /
.env.example 的設定說明），這是目前 LINE 官方推薦的替代方案。

設計原則
--------
- 未設定 Token（.env 空白或 config.py 讀不到）時，自動跳過該管道，
  回傳 {"skipped": True, ...}，不會拋出例外、不影響呼叫端其餘流程。
- 任何網路錯誤、API 回傳錯誤都在函式內攔截，統一以回傳值表示成功/失敗，
  絕不讓例外往外拋，確保 generate_daily_signals.py 主流程不會因為推播
  失敗而中斷（推播只是錦上添花，不是每日訊號報告的必要條件）。
- Telegram 單則訊息上限 4096 字元、LINE 單則約 5000 字元，超過會自動
  依換行處分段發送，避免把訊息硬切在句子中間或直接被 API 拒絕。
- 4xx（例如 401 Token錯誤、400 chat_id錯誤）通常是設定問題，重試沒有
  意義，直接回傳失敗；其餘錯誤（網路逾時、5xx）才做輕量重試。
"""

import requests
import os


def get_channel_access_token():
	"""從 Streamlit secrets 或環境變數取得官方帳號 Token"""
	try:
		import streamlit as st
		if "LINE_CHANNEL_ACCESS_TOKEN" in st.secrets:
			return st.secrets["LINE_CHANNEL_ACCESS_TOKEN"]
	except:
		pass
	return os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")


def send_line_message(message: str, user_id: str = None) -> bool:
	"""透過 LINE 官方帳號推播訊息 (Push Message)"""

	# 若沒帶入 user_id，自動從 Streamlit session 抓取已綁定的用戶
	if not user_id:
		try:
			import streamlit as st
			user_id = st.session_state.get("line_user_id")
		except:
			pass

	if not user_id:
		return "找不到 LINE User ID（請確認是否已登入/綁定）"

	token = get_channel_access_token()
	if not token:
		return "找不到 Channel Access Token（請檢查 secrets.toml）"

	url = "https://api.line.me/v2/bot/message/push"
	headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
	payload = {"to": user_id, "messages": [{"type": "text", "text": message}]}

	try:
		res = requests.post(url, headers=headers, json=payload)
		if res.status_code != 200:
			error_msg = f"LINE 拒絕發送 ({res.status_code}): {res.text}"
			print(f"❌ {error_msg}")
			return error_msg  # 回傳詳細錯誤字串

		return True  # 成功送達回傳 True

	except Exception as e:
		error_msg = f"發送例外異常: {e}"
		print(f"❌ {error_msg}")
		return error_msg
