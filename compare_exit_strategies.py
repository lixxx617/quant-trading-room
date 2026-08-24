"""
compare_exit_strategies.py
比較不同出場規則對「多頭吃肉比例」與「熊市防守力」的影響

背景（來自 0050.TW 實測）：
    全期間 (2018-2024) 策略報酬 81.9% vs B&H 204.5%，主因是
    M=10 日新低出場太敏感，長牛整理時的正常拉回就被洗出場，
    導致策略沒能完整吃到 2023-2024 的波段。

比較的三種出場設定（進場規則、200MA濾網皆相同，只變出場）：
    1. baseline_M10   : 唐奇安 M=10 日新低出場（原始設定，對照組）
    2. donchian_M20   : 唐奇安 M=20 日新低出場（單純放寬新低天數）
    3. ma20_exit      : 收盤跌破 20 日均線出場（移動停利）

輸出：
    - 各期間（2018 / 2020 / 2022 / 2023-2024 / 全期間）的策略報酬、
      B&H報酬、最大回撤、Sharpe、交易次數
    - 2023-2024 多頭的「吃肉比例」= 策略報酬 / B&H報酬，用來直接比較
      三種出場規則抓住波段的能力
    - 2022 熊市的最大回撤，用來確認防守力沒有被犧牲

用法：
    python compare_exit_strategies.py --ticker 0050.TW
    python compare_exit_strategies.py --csv my_data.csv
"""

import argparse
import pandas as pd

from donchian_breakout import DonchianBreakoutStrategy, DonchianBreakoutParams
from backtest_engine import run_backtest, compute_metrics, count_trades
from validate_strategy import PERIODS, load_price_data, load_price_data_csv


CONFIGS = {
    "baseline_M10 (原始)": DonchianBreakoutParams(exit_mode="donchian_low", exit_period=10),
    "donchian_M20 (放寬M值)": DonchianBreakoutParams(exit_mode="donchian_low", exit_period=20),
    "ma20_exit (20MA出場)": DonchianBreakoutParams(exit_mode="ma_cross", exit_ma_period=20),
}


def run_segment_with_trades(df_full: pd.DataFrame, start: str, end: str, strategy: DonchianBreakoutStrategy) -> dict:
    """跟 validate_strategy.run_segment 相同邏輯，但額外回傳交易次數"""
    signals = strategy.generate_signals(df_full)
    segment = signals[(signals.index >= start) & (signals.index <= end)].copy()

    if segment.empty:
        return {"warning": "此區間無資料"}
    if segment["trend_ma"].isna().all():
        return {"warning": "200MA 尚未成熟（資料暖機不足）"}

    bt = run_backtest(segment)
    strat_metrics = compute_metrics(bt["equity"])
    bh_metrics = compute_metrics(bt["buy_hold_equity"])
    strat_metrics["trades"] = count_trades(segment["position"])

    return {"strategy": strat_metrics, "buy_and_hold": bh_metrics}


def run_all_configs(df_full: pd.DataFrame) -> dict:
    """對每組出場設定，跑完整的分段驗證"""
    all_results = {}
    for config_name, params in CONFIGS.items():
        strategy = DonchianBreakoutStrategy(params)
        all_results[config_name] = {
            period: run_segment_with_trades(df_full, start, end, strategy)
            for period, (start, end) in PERIODS.items()
        }
    return all_results


def print_comparison(all_results: dict):
    for config_name, results in all_results.items():
        print(f"\n■ {config_name}")
        header = f"  {'期間':<20}{'策略報酬':>10}{'B&H報酬':>10}{'策略MDD':>10}{'Sharpe':>8}{'交易次數':>8}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for period, res in results.items():
            if "warning" in res:
                print(f"  {period:<20} [跳過] {res['warning']}")
                continue
            s = res["strategy"]
            print(
                f"  {period:<20}"
                f"{s['total_return']*100:>9.1f}%"
                f"{res['buy_and_hold']['total_return']*100:>9.1f}%"
                f"{s['max_drawdown']*100:>9.1f}%"
                f"{s['sharpe']:>8.2f}"
                f"{s['trades']:>8d}"
            )

    # 核心比較：多頭吃肉比例 + 熊市防守力
    bull_label = "2023-2024 多頭"
    bear_label = "2022 熊市"
    print(f"\n{'='*72}")
    print(f"核心比較：{bull_label} 吃肉比例 vs {bear_label} 防守力")
    print(f"{'='*72}")
    print(f"{'出場設定':<24}{'多頭吃肉比例':>14}{'多頭報酬':>10}{'熊市MDD':>10}{'熊市報酬':>10}")
    print("-" * 68)
    for config_name, results in all_results.items():
        bull = results.get(bull_label, {})
        bear = results.get(bear_label, {})
        if "warning" in bull or "warning" in bear:
            print(f"{config_name:<24} 資料不足，略過")
            continue
        bull_s, bull_b = bull["strategy"], bull["buy_and_hold"]
        bear_s = bear["strategy"]
        capture_ratio = bull_s["total_return"] / bull_b["total_return"] if bull_b["total_return"] != 0 else float("nan")
        print(
            f"{config_name:<24}"
            f"{capture_ratio*100:>13.1f}%"
            f"{bull_s['total_return']*100:>9.1f}%"
            f"{bear_s['max_drawdown']*100:>9.1f}%"
            f"{bear_s['total_return']*100:>9.1f}%"
        )
    print(
        "\n吃肉比例 = 策略在多頭期間的報酬 / Buy&Hold 報酬。"
        "數字愈接近或超過 100% 代表愈能完整吃到波段；"
        "熊市MDD 則用來確認沒有為了吃肉而犧牲防守。"
    )


def main():
    parser = argparse.ArgumentParser(description="比較不同出場規則的吃肉比例與防守力")
    parser.add_argument("--ticker", default="0050.TW", help="股票代碼，例如 2330.TW, 0050.TW")
    parser.add_argument("--csv", default=None, help="改用本地 CSV 檔案路徑（略過 yfinance 下載）")
    args = parser.parse_args()

    full_start, full_end = "2017-01-01", "2024-12-31"
    if args.csv:
        df_full = load_price_data_csv(args.csv, full_start, full_end)
    else:
        df_full = load_price_data(args.ticker, full_start, full_end)

    all_results = run_all_configs(df_full)

    print(f"標的: {args.csv or args.ticker}")
    print("比較設定: entry_period=20（三組共用）， 200MA 進場濾網（三組共用）")
    print_comparison(all_results)


if __name__ == "__main__":
    main()
