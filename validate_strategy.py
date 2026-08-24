"""
validate_strategy.py
長週期分段驗證：唐奇安通道突破策略 vs Buy & Hold（支援多標的批次測試）

★ 預設策略參數已鎖定為標準架構 donchian_M20：
    entry_period=20（20日新高進場）+ exit_period=20（20日新低出場）
    + 200日均線進場濾網。此組合經比較測試（vs M=10、vs 20MA出場）
    證實在「多頭吃肉比例」與「熊市防守力」間取得最佳平衡，全期間
    總報酬與 MDD 皆優於前版本。詳見 donchian_breakout.py 頂部說明。

涵蓋期間：
    2018            盤整/下跌年（測試無明顯趨勢時的抗震性）
    2020            疫情崩跌 + V 型反轉（測試熊市防守 + 快速回補能力）
    2022            熊市（對照舊策略 0% 回撤的防守力是否維持）
    2023-2024       多頭（對照舊策略被 RSI 卡死只拿 4.8% 的問題是否解決）
    2018-2024       全期間總覽

用法：
    python validate_strategy.py                                  # 預設跑台積電/鴻海/聯發科/0050
    python validate_strategy.py --tickers 2330.TW 2317.TW 2454.TW 0050.TW
    python validate_strategy.py --tickers 0050.TW --entry 20 --exit 20
    python validate_strategy.py --csv a.csv b.csv                # 改用本地 CSV（可多檔）
    python validate_strategy.py --tickers 0050.TW --strict-trend-exit   # 加強熊市防守
"""

import argparse
import pandas as pd

from donchian_breakout import DonchianBreakoutStrategy, DonchianBreakoutParams
from backtest_engine import run_backtest, compute_metrics
from market_filter import compute_market_trend, align_market_trend


PERIODS = {
    "2018 盤整/下跌": ("2018-01-01", "2018-12-31"),
    "2020 疫情崩跌+V轉": ("2020-01-01", "2020-12-31"),
    "2022 熊市": ("2022-01-01", "2022-12-31"),
    "2023-2024 多頭": ("2023-01-01", "2024-12-31"),
    "2018-2024 全期間": ("2018-01-01", "2024-12-31"),
}


def load_price_data(ticker: str, start: str, end: str) -> pd.DataFrame:
	"""
    透過 yfinance 下載台股歷史資料
    """
	try:
		import yfinance as yf
	except ImportError as e:
		raise RuntimeError("需要 yfinance 套件，請執行: pip install yfinance") from e

	raw = yf.download(ticker, start=start, end=end, progress=False)
	if raw.empty:
		raise RuntimeError(f"下載 {ticker} 資料為空，請確認代碼或改用 --csv 載入本地資料")

	# 補上這段：處理新版 yfinance 的 MultiIndex 雙層欄位問題
	if isinstance(raw.columns, pd.MultiIndex):
		raw.columns = raw.columns.get_level_values(0)

	raw.columns = [str(c).lower() for c in raw.columns]
	raw.index.name = "date"
	return raw[["open", "high", "low", "close", "volume"]].dropna()


def load_price_data_csv(path: str, start: str = None, end: str = None) -> pd.DataFrame:
	"""從本地 CSV 載入資料，欄位需含: date, open, high, low, close, volume"""
	df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
	df.columns = [c.lower() for c in df.columns]
	if start:
		df = df[df.index >= start]
	if end:
		df = df[df.index <= end]
	return df[["open", "high", "low", "close", "volume"]].dropna()


def run_segment(
    df_full: pd.DataFrame, start: str, end: str, strategy: DonchianBreakoutStrategy,
    market_trend: pd.Series = None,
) -> dict:
	"""
    針對指定期間跑回測。

    注意：指標會用「全歷史資料」算完（讓 200MA、唐奇安通道在區間開頭就有暖機資料），
    再切出目標區間計算績效，避免因為區間開頭資料不足造成失真。

    market_trend（選用）：大盤趨勢布林 Series，會在傳入 generate_signals 前先
    align 到 df_full 的交易日曆；若策略設定 require_market_filter=True 卻沒有
    傳入，generate_signals 會直接丟出 ValueError（刻意不靜默失敗）。
    """
	aligned_market = align_market_trend(market_trend, df_full.index) if market_trend is not None else None
	signals = strategy.generate_signals(df_full, market_trend=aligned_market)
	segment = signals[(signals.index >= start) & (signals.index <= end)].copy()

	if segment.empty:
		return {"warning": "此區間無資料，請確認資料來源涵蓋此期間"}

	if segment["trend_ma"].isna().all():
		return {"warning": "區間內 200MA 尚未成熟（資料起始不足 200 個交易日），建議提早資料起始日"}

	bt = run_backtest(segment)
	strat_metrics = compute_metrics(bt["equity"])
	bh_metrics = compute_metrics(bt["buy_hold_equity"])

	return {"strategy": strat_metrics, "buy_and_hold": bh_metrics}


def print_report(results: dict):
	header = f"{'期間':<20}{'策略報酬':>10}{'B&H報酬':>10}{'策略MDD':>10}{'B&H MDD':>10}{'Sharpe':>8}{'勝率':>8}"
	print(header)
	print("-" * len(header))
	for period, res in results.items():
		if "warning" in res:
			print(f"{period:<20} [跳過] {res['warning']}")
			continue
		s, b = res["strategy"], res["buy_and_hold"]
		print(
		    f"{period:<20}"
		    f"{s['total_return']*100:>9.1f}%"
		    f"{b['total_return']*100:>9.1f}%"
		    f"{s['max_drawdown']*100:>9.1f}%"
		    f"{b['max_drawdown']*100:>9.1f}%"
		    f"{s['sharpe']:>8.2f}"
		    f"{s['win_rate']*100:>7.1f}%"
		)


DEFAULT_TICKERS = ["2330.TW", "2317.TW", "2454.TW", "0050.TW"]  # 台積電/鴻海/聯發科/0050


def print_summary(all_results: dict, full_period_label: str = "2018-2024 全期間"):
	"""
    彙總各標的在全期間的績效，並計算平均勝率 / Sharpe，
    方便判斷 donchian_M20 這套標準架構是否能穩定套用在不同標的上。
    """
	rows = []
	for ticker, results in all_results.items():
		full = results.get(full_period_label)
		if not full or "warning" in full:
			print(f"  [跳過彙總] {ticker}：{full.get('warning', '無資料') if full else '無資料'}")
			continue
		s = full["strategy"]
		rows.append((ticker, s["total_return"], s["max_drawdown"], s["sharpe"], s["win_rate"]))

	if not rows:
		print("  沒有可彙總的標的（可能都因暖機資料不足被跳過）")
		return

	print(f"\n{'='*66}")
	print(f"多標的績效總覽（{full_period_label}，標準架構 donchian_M20）")
	print(f"{'='*66}")
	header = f"{'標的':<12}{'總報酬':>10}{'最大回撤':>10}{'Sharpe':>8}{'勝率':>8}"
	print(header)
	print("-" * len(header))
	for ticker, ret, mdd, sharpe, win_rate in rows:
		print(f"{ticker:<12}{ret*100:>9.1f}%{mdd*100:>9.1f}%{sharpe:>8.2f}{win_rate*100:>7.1f}%")

	n = len(rows)
	avg_ret = sum(r[1] for r in rows) / n
	avg_mdd = sum(r[2] for r in rows) / n
	avg_sharpe = sum(r[3] for r in rows) / n
	avg_win = sum(r[4] for r in rows) / n
	print("-" * len(header))
	print(f"{'平均 (' + str(n) + '檔)':<12}{avg_ret*100:>9.1f}%{avg_mdd*100:>9.1f}%{avg_sharpe:>8.2f}{avg_win*100:>7.1f}%")


def main():
	parser = argparse.ArgumentParser(description="唐奇安通道突破策略 (donchian_M20 標準架構) - 多標的長週期分段驗證")
	parser.add_argument(
	    "--tickers", nargs="+", default=DEFAULT_TICKERS,
	    help=f"股票代碼清單，可多個，例如 2330.TW 2317.TW（預設: {' '.join(DEFAULT_TICKERS)}）",
	)
	parser.add_argument("--csv", nargs="+", default=None, help="改用本地 CSV 檔案路徑清單（提供則忽略 --tickers）")
	parser.add_argument("--entry", type=int, default=20, help="唐奇安進場天數 N（新高進場）")
	parser.add_argument("--exit", type=int, default=20, help="唐奇安出場天數 M（新低出場，標準架構=20）")
	parser.add_argument("--trend-ma", type=int, default=200, help="趨勢濾網均線天數")
	parser.add_argument(
	    "--strict-trend-exit",
	    action="store_true",
	    help="啟用嚴格模式：跌破200MA時強制出場，加強熊市防守力（更接近舊版0%%回撤）",
	)
	parser.add_argument("--market-ticker", default="0050.TW", help="大盤濾網代理標的（預設0050.TW）")
	parser.add_argument("--market-csv", default=None, help="大盤資料改用本地CSV（搭配 --csv 使用時）")
	parser.add_argument("--no-market-filter", action="store_true", help="停用大盤濾網（僅用個股自身200MA）")
	args = parser.parse_args()

	params = DonchianBreakoutParams(
	    entry_period=args.entry,
	    exit_period=args.exit,
	    trend_ma_period=args.trend_ma,
	    use_trend_filter=True,
	    exit_on_trend_break=args.strict_trend_exit,
	    require_market_filter=not args.no_market_filter,
	)
	strategy = DonchianBreakoutStrategy(params)

	# 資料起始日提早於最早驗證期間，讓 200MA 有足夠暖機資料
	full_start, full_end = "2017-01-01", "2024-12-31"

	# 決定要跑的標的清單：--csv 提供時優先使用（逐檔載入），否則用 --tickers 透過 yfinance 下載
	targets = [(path, True) for path in args.csv] if args.csv else [(t, False) for t in args.tickers]

	print(
	    f"參數: N={args.entry} 日新高進場, M={args.exit} 日新低出場, "
	    f"{args.trend_ma}日均線趨勢濾網（無RSI）, 嚴格出場={args.strict_trend_exit}, "
	    f"大盤濾網={'啟用 (' + args.market_ticker + ')' if not args.no_market_filter else '停用'}"
	)

	# 大盤濾網只需抓一次、算一次，之後對齊到每檔個股各自的交易日曆即可重複使用
	market_trend = None
	if not args.no_market_filter:
		try:
			market_df = (
			    load_price_data_csv(args.market_csv, full_start, full_end) if args.market_csv
			    else load_price_data(args.market_ticker, full_start, full_end)
			)
			market_trend = compute_market_trend(market_df, ma_period=args.trend_ma)
		except Exception as e:
			print(f"  [警告] 大盤資料載入失敗，將自動停用大盤濾網: {e}")
			strategy.params.require_market_filter = False

	all_results = {}
	for label, is_csv in targets:
		print(f"\n{'#'*66}\n標的: {label}\n{'#'*66}")
		try:
			df_full = load_price_data_csv(label, full_start, full_end) if is_csv else load_price_data(label, full_start, full_end)
		except Exception as e:
			print(f"  [跳過] 載入失敗: {e}")
			continue

		results = {
		    period: run_segment(df_full, start, end, strategy, market_trend=market_trend)
		    for period, (start, end) in PERIODS.items()
		}
		all_results[label] = results
		print_report(results)

	print_summary(all_results)


if __name__ == "__main__":
	main()
