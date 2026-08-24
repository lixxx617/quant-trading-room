"""
驗證整條 pipeline（資料層 -> 策略 -> 回測引擎）。

兩種資料來源可切換：
  USE_REAL_DATA = True -> 模擬隨機漫走資料，純測試pipeline邏輯有沒有bug用
  USE_REAL_DATA = True  -> 透過 yfinance 抓真實台股資料（需要網路+安裝yfinance）

本機執行方式：
  pip install -r requirements.txt
  python3 test_pipeline.py
"""
import logging
import numpy as np
import pandas as pd

from data_layer import MarketDataStore, tw_symbol
from engine import BacktestEngine, StopLossConfig, StopLossType, RiskConfig
from ma_cross import MACrossStrategy

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ---- 設定區：改這裡就好 ----
USE_REAL_DATA = True
STOCK_ID = "2330"          # 台積電；換代號就能測別的股票，例如 "2317" (鴻海)
START_DATE = "2020-01-01"
END_DATE = "2025-01-01"
# ---------------------------


def load_real_tw_data(stock_id: str, start: str, end: str) -> pd.DataFrame:
    """透過資料層抓取真實台股日K，會自動快取到 data/market_data.db"""
    store = MarketDataStore()
    symbol = tw_symbol(stock_id)
    df = store.load(symbol, start, end)
    if df.empty:
        raise RuntimeError(
            f"抓不到 {symbol} 的資料，請確認：\n"
            f"  1. 已執行 pip install yfinance\n"
            f"  2. 網路連線正常\n"
            f"  3. 股票代號是否正確（台股上市用 .TW，上櫃用 tw_symbol(id, otc=True)）"
        )
    logger.info(f"已載入 {symbol}: {len(df)} 筆日K，期間 {df.index[0].date()} ~ {df.index[-1].date()}")
    return df


def make_synthetic_data(n=250, seed=42) -> pd.DataFrame:
    """產生類似股價的隨機漫步資料，僅供pipeline測試用"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    returns = rng.normal(0.0005, 0.015, n)
    close = 100 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(1000, 10000, n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)
    return df


def run_test(stop_type: StopLossType, data: pd.DataFrame):
    strategy = MACrossStrategy(short_window=5, long_window=20)
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=1_000_000,
        stop_loss_config=StopLossConfig(type=stop_type, fixed_pct=0.05, trailing_pct=0.08),
        risk_config=RiskConfig(max_position_pct=0.3, max_daily_loss_pct=0.03),
    )
    result = engine.run(data)
    print(f"\n===== 止損類型: {stop_type.value} =====")
    print(f"最終資金: {result['final_equity']:,.0f}")
    print(f"總報酬率: {result['total_return_pct']:.2f}%")
    print(f"最大回撤: {result['max_drawdown_pct']:.2f}%")
    print(f"交易次數: {result['num_trades']}")
    print(f"勝率: {result['win_rate_pct']:.1f}%")
    print(f"Sharpe: {result['sharpe_ratio']:.2f}")
    if result["trades"]:
        print("前3筆交易:")
        for t in result["trades"][:3]:
            print(f"  {t.entry_date.date()} -> {t.exit_date.date()} | "
                  f"進場{t.entry_price:.2f} 出場{t.exit_price:.2f} 損益{t.pnl:.0f} 原因={t.exit_reason}")


if __name__ == "__main__":
    if USE_REAL_DATA:
        data = load_real_tw_data(STOCK_ID, START_DATE, END_DATE)
    else:
        data = make_synthetic_data()

    for st in [StopLossType.FIXED_PCT, StopLossType.TRAILING, StopLossType.TIME]:
        run_test(st, data)
