"""
config.py
集中管理外部推播服務的憑證（Telegram Bot Token、LINE Messaging API Token等）。

★ 安全原則
----------
- 真正的金鑰一律寫在 .env（不進版控），這支檔案只負責「讀取」，
  絕對不要把任何真實 Token 寫死在這支 .py 檔裡。
- .env 應加入 .gitignore，避免不小心 commit 金鑰外洩。
- 找不到 .env、或欄位是空字串，一律視為「未設定」——
  notifier.py 會自動跳過該推播管道，不會讓整支每日訊號腳本崩潰。

用法
----
1. 複製 .env.example 為 .env
2. 依註解說明填入你的 Token / Chat ID
3. pip install python-dotenv（若尚未安裝；沒裝的話會退化為只讀
   系統環境變數，仍可運作，只是要自己 export）

關於 LINE
---------
LINE Notify 已於 2025-03-31 正式終止服務（官方公告：
https://notify-bot.line.me/closing-announce），舊版 Token 已全數失效。
這裡改用 LINE 官方帳號的 Messaging API push message 取代，需要
Channel Access Token + 目標使用者的 userId，設定方式與舊版 LINE Notify
完全不同，請見 .env.example 的說明。
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # 自動讀取專案根目錄的 .env（找不到檔案時安靜地跳過，不報錯）
except ImportError:
    # 沒裝 python-dotenv 時，退化為只讀系統環境變數。
    # 仍然可以運作：手動 export TELEGRAM_BOT_TOKEN=... 之類的環境變數即可，
    # 或執行 `pip install python-dotenv` 後改用 .env 檔案管理。
    pass


def _clean(value):
    """None 或空白字串一律視為未設定，回傳 None（避免空字串被誤判為「已設定」）。"""
    if value is None:
        return None
    value = value.strip()
    return value or None


TELEGRAM_BOT_TOKEN = _clean(os.environ.get("TELEGRAM_BOT_TOKEN"))
TELEGRAM_CHAT_ID = _clean(os.environ.get("TELEGRAM_CHAT_ID"))

# LINE Notify 已停止服務，這裡用 Messaging API push message 取代
LINE_CHANNEL_ACCESS_TOKEN = _clean(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"))
LINE_USER_ID = _clean(os.environ.get("LINE_USER_ID"))


def has_telegram_config() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def has_line_config() -> bool:
    return bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_USER_ID)
