"""
generate_daily_signals.py
每日收盤後執行，印出 donchian_M20 策略（唐奇安通道 + 個股200MA + 0050大盤濾網
+ 組合部位管理）的最新買進/賣出/持有建議。

★ 執行時機與假設（務必遵守，否則會產生前瞻偏差 / 不切實際的成交假設）：
    - 本腳本應在「當日收盤後」執行，用當日收盤價計算訊號。
    - 訊號代表「隔日開盤」的建議動作 —— 收盤後才能算出訊號，
      當天收盤價已經無法用來下單，所以永遠是「隔日執行」。
    - 這裡只做「今天該不該動作」的即時判斷，不是重跑一次完整回測。
      實際持倉狀態一律以 portfolio_state.json 為準（可能包含人工調整），
      不會被回測式的連續 position 欄位覆蓋。
    - 本腳本僅供輔助判斷，不做自動下單，也不保證即時報價的正確性；
      實際送單前請自行覆核價格、流動性、手續費與稅負。

持倉狀態檔格式（portfolio_state.json，範例）：
    {
      "total_capital": 1000000,
      "cash": 400000,
      "holdings": {
        "2330.TW": {"shares": 1000, "avg_cost": 950.0}
      }
    }
    第一次執行若找不到狀態檔，會自動建立一個「全現金、無持倉」的預設狀態
    （total_capital = cash = 1,000,000，可用 --initial-capital 調整），
    之後請在實際下單成交後手動更新這個檔案。

自動推播（選用）：
    報告產生後會自動嘗試透過 notifier.py 推播到 Telegram / LINE。
    設定方式：複製 .env.example 為 .env，填入 Token（見該檔案內註解），
    未填寫的管道會自動跳過，不影響 Terminal 報告本身的產生。
    用 --no-notify 可以完全停用推播（例如測試時）。

用法：
    python generate_daily_signals.py
    python generate_daily_signals.py --tickers 2330.TW 2317.TW 2454.TW 0050.TW
    python generate_daily_signals.py --state my_portfolio.json
    python generate_daily_signals.py --csv-dir ./data   # 用本地CSV，檔名需為 {ticker}.csv
    python generate_daily_signals.py --no-notify         # 只印報告，不推播
"""

import argparse
import json
import os
from datetime import datetime

import pandas as pd

from donchian_breakout import DonchianBreakoutStrategy, DonchianBreakoutParams
from market_filter import compute_market_trend, align_market_trend
from portfolio_manager import PortfolioManager, PortfolioConfig
from validate_strategy import load_price_data, load_price_data_csv, DEFAULT_TICKERS

# notifier/config是選用功能：就算沒建立.env、沒裝python-dotenv，
# 甚至notifier.py/config.py整個不存在，每日訊號報告本身仍要能正常印出。
try:
    import notifier
    _NOTIFIER_AVAILABLE = True
except ImportError:
    _NOTIFIER_AVAILABLE = False


DEFAULT_STATE_PATH = "portfolio_state.json"
DATA_WARMUP_START = "2015-01-01"   # 抓取起始日，確保200MA在「今天」已經暖機完成
STALE_DAYS_WARNING = 5             # 最新資料若超過這麼多天沒更新，視為過期資料，停止判斷


def load_state(path: str, initial_capital: float) -> dict:
    if not os.path.exists(path):
        print(f"[提示] 找不到狀態檔 {path}，建立預設全現金狀態（本金 {initial_capital:,.0f}）")
        return {"total_capital": initial_capital, "cash": initial_capital, "holdings": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    """
    把資產狀態（現金＋持倉）寫回 JSON 檔，供 Asset Editor 的「儲存」功能使用。

    刻意不做原子寫入（write-to-temp-then-rename）以外的額外保護 —— 這是單機
    使用的個人工具，不是多程序併發寫入的資料庫；呼叫端（app.py）應確保
    使用者按下儲存前已經看過最終要寫入的內容，避免誤觸覆蓋。
    """
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)  # 用rename做準原子寫入，避免寫到一半當機留下half-written的壞檔


def fetch_ticker_data(ticker: str, csv_path: str = None) -> pd.DataFrame:
    """抓取足夠長的歷史資料，確保 200 日均線在最新一天已經暖機完成"""
    end = datetime.today().strftime("%Y-%m-%d")
    if csv_path:
        return load_price_data_csv(csv_path)
    return load_price_data(ticker, DATA_WARMUP_START, end)


def compute_today_signal(
    df: pd.DataFrame,
    strategy: DonchianBreakoutStrategy,
    market_trend: pd.Series,
    currently_held: bool,
) -> dict:
    """
    只判斷「今天」這一天該做什麼，而不是用回測的連續 position 欄位。

    這麼做是刻意的：實盤的實際持倉狀態來自 portfolio_state.json（可能因為
    人工介入、部分成交、手續費差異等原因與純理論回測不同步），不應該讓
    回測式的 position 欄位覆蓋真實持倉狀態 —— 否則腳本可能建議「進場」
    一檔其實已經持有很久的股票，或忽略一檔實際持有但回測認為早該出場的股票。
    """
    aligned_market = align_market_trend(market_trend, df.index) if market_trend is not None else None
    signals = strategy.generate_signals(df, market_trend=aligned_market)

    if signals.empty:
        return {"action": "error", "reason": "無資料"}

    last = signals.iloc[-1]
    last_date = signals.index[-1]

    days_stale = (pd.Timestamp.today().normalize() - last_date).days
    if days_stale > STALE_DAYS_WARNING:
        return {
            "action": "stale",
            "reason": f"最新資料日期為 {last_date.date()}，距今已 {days_stale} 天，請確認資料來源是否更新",
            "date": str(last_date.date()),
        }

    price = float(last["close"])
    date_str = str(last_date.date())

    if currently_held:
        # 出場判斷：用當日 signal 是否為 -1（不受大盤濾網影響，任何時候都能離場）
        if last["signal"] == -1:
            return {"action": "exit", "reason": "收盤觸發出場門檻", "price": price, "date": date_str}
        return {"action": "hold", "reason": "持有中，未觸發出場", "price": price, "date": date_str}
    else:
        if last["signal"] == 1:
            reason = "收盤創新高突破，且通過個股與大盤趨勢濾網" if strategy.params.require_market_filter else "收盤創新高突破，且通過個股趨勢濾網"
            return {"action": "entry", "reason": reason, "price": price, "date": date_str}
        return {"action": "no_action", "reason": "未觸發進場條件", "price": price, "date": date_str}


def build_daily_report(
    tickers: list,
    market_ticker: str,
    state: dict,
    csv_dir: str = None,
    max_positions: int = 4,
    max_allocation: float = 0.25,
    use_market_filter: bool = True,
) -> dict:
    """
    核心邏輯：計算大盤濾網、逐檔訊號、組合部位建議，回傳結構化結果（不印出、不推播）。

    這支函式被 main()（CLI輸出 + 推播）與 app.py（Streamlit儀表板）共用 ——
    刻意抽成單一函式，是為了避免同一套「大盤濾網/個股訊號/部位規劃」邏輯
    在兩個地方各寫一份，日後改一邊、忘了改另一邊，兩個介面顯示的結果不一致。

    回傳 dict 欄位：
        date, market_ticker, market_filter_enabled, market_bullish,
        holdings, cash, max_positions, details（各標的訊號明細）,
        actions（組合建議動作清單）, warnings（過期資料/設定問題等提醒文字）
    """
    warnings_list = []
    strategy = DonchianBreakoutStrategy(DonchianBreakoutParams(
        entry_period=20,
        exit_period=20,
        trend_ma_period=200,
        use_trend_filter=True,
        require_market_filter=use_market_filter,
    ))
    pm = PortfolioManager(PortfolioConfig(
        max_positions=max_positions,
        max_allocation_pct=max_allocation,
    ))

    holdings = state.get("holdings", {})

    # 自動把目前持倉的標的併入追蹤清單，確保任何持股都會被正確計算市值與出場訊號 ——
    # 這原本只是丟出警告、要求使用者自己補進 --tickers，現在改成直接自動修正，
    # 避免使用者忘記加標的而讓部位規劃算出錯誤的總資金或漏判出場。
    tickers = list(dict.fromkeys(tickers))  # 先複製一份、去重，不動到呼叫端傳進來的原始 list
    auto_added = [t for t in holdings if t not in tickers]
    if auto_added:
        tickers = tickers + auto_added
        warnings_list.append(f"已自動將持倉標的 {auto_added} 併入本次追蹤清單（原清單未包含）")

    # 1) 大盤濾網（只抓一次、算一次）
    # 邊界條件：大盤資料本身也可能過期（例如0050資料來源當天沒更新），
    # 若不檢查就直接採用，可能會用「舊的多頭訊號」誤導今天的進場判斷 —— 這裡
    # 用跟個股訊號相同的新鮮度檢查，過期時 fail-safe 為空頭、暫停開新倉。
    market_trend = None
    market_bullish_today = True  # 停用濾網時視為永遠可進場（等同不限制）
    if use_market_filter:
        market_csv = os.path.join(csv_dir, f"{market_ticker}.csv") if csv_dir else None
        try:
            market_df = fetch_ticker_data(market_ticker, market_csv)
            if market_df.empty:
                raise RuntimeError("大盤資料為空")

            market_last_date = market_df.index[-1]
            market_days_stale = (pd.Timestamp.today().normalize() - market_last_date).days
            if market_days_stale > STALE_DAYS_WARNING:
                warnings_list.append(
                    f"大盤 ({market_ticker}) 資料最新日期為 {market_last_date.date()}，"
                    f"距今已 {market_days_stale} 天，視為過期資料 → fail-safe 視為空頭、暫停開新倉"
                )
                market_bullish_today = False
                strategy.params.require_market_filter = False  # 讓個股訊號仍可算出；進場一律由 market_bullish_today=False 在組合層擋下
            else:
                market_trend = compute_market_trend(market_df, ma_period=strategy.params.trend_ma_period)
                market_bullish_today = bool(market_trend.iloc[-1])
        except Exception as e:
            warnings_list.append(f"大盤資料載入失敗 ({e})，為安全起見視為空頭、暫停開新倉")
            market_bullish_today = False
            strategy.params.require_market_filter = False

    # 2) 逐檔計算今日訊號
    signals, prices, details = {}, {}, {}
    for ticker in tickers:
        csv_path = os.path.join(csv_dir, f"{ticker}.csv") if csv_dir else None
        try:
            df = fetch_ticker_data(ticker, csv_path)
        except Exception as e:
            details[ticker] = {"action": "error", "reason": f"資料載入失敗: {e}"}
            signals[ticker] = "hold"
            continue

        currently_held = ticker in holdings
        result = compute_today_signal(df, strategy, market_trend, currently_held)
        details[ticker] = result
        if "price" in result:
            prices[ticker] = result["price"]

        if result["action"] in ("error", "stale"):
            signals[ticker] = "hold"  # 資料異常時，保守處理為不動作，避免用壞資料誤判
        else:
            signals[ticker] = {"entry": "entry", "exit": "exit"}.get(result["action"], "hold")

    # 3) 組合部位規劃
    actions = pm.plan_actions(state, signals, market_bullish_today, prices)

    return {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "market_ticker": market_ticker,
        "market_filter_enabled": use_market_filter,
        "market_bullish": market_bullish_today,
        "holdings": holdings,
        "cash": state.get("cash", 0),
        "max_positions": max_positions,
        "details": details,
        "actions": actions,
        "warnings": warnings_list,
        "tickers_used": tickers,
        "auto_added_tickers": auto_added,
    }


def format_report_text(report: dict) -> str:
    """把 build_daily_report() 的結構化結果格式化成純文字報告（CLI輸出與推播共用同一份文字）"""
    lines = []
    lines.append(f"=== donchian_M20 每日訊號報告 {report['date']} ===")
    if report["market_filter_enabled"]:
        status = "多頭 → 可開新倉" if report["market_bullish"] else "空頭 → 禁止開新倉，保留現金"
        lines.append(f"大盤濾網 ({report['market_ticker']}): {status}")
    else:
        lines.append("大盤濾網: 已停用")
    lines.append(f"目前持倉: {len(report['holdings'])}/{report['max_positions']} 檔, 現金: {report['cash']:,.0f}")
    lines.append("")
    lines.append("[個股訊號]")
    for ticker, detail in report["details"].items():
        price_str = f" @ {detail['price']:.2f}" if detail.get("price") else ""
        lines.append(f"  {ticker:<10} {detail['action']:<10} {detail['reason']}{price_str}")

    lines.append("")
    lines.append("[建議動作]")
    if not report["actions"]:
        lines.append("  今日無任何建議動作。")
    for a in report["actions"]:
        extra = f", 預估金額 {a['est_cost']:,.0f}" if "est_cost" in a else ""
        lines.append(f"  {a['action']:<10} {a['ticker']:<10} 股數:{a['shares']:>6}{extra} — {a['reason']}")

    lines.append("")
    lines.append("⚠️ 提醒：以上為「隔日開盤」參考訊號，不是即時成交建議，也不是自動下單。")
    lines.append("   實際送單前請自行確認報價、流動性與券商手續費／稅負，並在成交後手動更新狀態檔。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="donchian_M20 每日訊號產生器（含0050大盤濾網 + 組合部位管理）")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS, help="追蹤標的清單")
    parser.add_argument("--market-ticker", default="0050.TW", help="大盤濾網代理標的")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="持倉狀態檔路徑（JSON）")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0, help="狀態檔不存在時的預設本金")
    parser.add_argument("--csv-dir", default=None, help="改用本地 CSV 資料夾（檔名需為 {ticker}.csv），略過線上下載")
    parser.add_argument("--max-positions", type=int, default=4)
    parser.add_argument("--max-allocation", type=float, default=0.25)
    parser.add_argument("--no-market-filter", action="store_true", help="停用大盤濾網（不建議，僅供比較測試）")
    parser.add_argument("--no-notify", action="store_true", help="停用推播，只在Terminal印出報告（除錯/測試用）")
    args = parser.parse_args()

    state = load_state(args.state, args.initial_capital)

    report = build_daily_report(
        tickers=args.tickers,
        market_ticker=args.market_ticker,
        state=state,
        csv_dir=args.csv_dir,
        max_positions=args.max_positions,
        max_allocation=args.max_allocation,
        use_market_filter=not args.no_market_filter,
    )

    for w in report["warnings"]:
        print(f"⚠️  {w}")

    report_text = format_report_text(report)
    print("\n" + report_text)

    # 5) 推播（選用功能；未設定憑證、模組缺失、或推播過程出錯，都絕不能影響上面已經印出的報告）
    if args.no_notify:
        return

    if not _NOTIFIER_AVAILABLE:
        print("\n[推播狀態] 找不到 notifier.py / config.py，已略過推播（僅印出報告，不影響上述結果）")
        return

    try:
        notify_results = notifier.send_notifications(report_text)
        print("\n[推播狀態]")
        for channel, result in notify_results.items():
            if result.get("skipped"):
                print(f"  {channel:<10} 跳過（未設定憑證，見 .env.example）")
            elif result.get("ok"):
                print(f"  {channel:<10} 發送成功")
            else:
                print(f"  {channel:<10} 發送失敗: {result.get('error')}")
    except Exception as e:
        # notifier內部已經有自己的try/except了，這裡是最後一道防線 ——
        # 就算notifier模組本身壞掉，也不能讓已經算好、已經印出的每日訊號報告受影響
        print(f"\n[推播狀態] 推播模組發生未預期錯誤，已略過（不影響上述報告）: {e}")


if __name__ == "__main__":
    main()
