"""
backtest_engine.py
輕量回測引擎：計算權益曲線、報酬率、最大回撤、Sharpe、勝率等指標。
供 donchian_breakout.py 產生的訊號與 validate_strategy.py 共用，
與策略邏輯解耦，未來要換其他策略（例如加回其他濾網）也能直接沿用。
"""

import numpy as np
import pandas as pd


def run_backtest(
    df: pd.DataFrame,
    position_col: str = "position",
    price_col: str = "close",
    initial_capital: float = 1_000_000.0,
    commission_rate: float = 0.001425,  # 台股券商手續費（買賣各計一次，可依券商折數調整）
    tax_rate: float = 0.003,            # 台股證交稅（僅賣出時收取）
) -> pd.DataFrame:
    """
    簡化資金曲線模擬：持倉時間全額投入標的，訊號翻轉時扣除交易成本。
    回傳新增 equity（策略權益曲線）與 buy_hold_equity（同期買進持有）欄位的 DataFrame。
    """
    out = df.copy()
    out["daily_return"] = out[price_col].pct_change().fillna(0)

    # 用昨日持倉狀態決定今日報酬，避免使用未來資訊
    out["position_shifted"] = out[position_col].shift(1).fillna(0)
    out["strategy_return"] = out["position_shifted"] * out["daily_return"]

    # 持倉狀態變化時計入交易成本
    pos_diff = out[position_col].diff().fillna(out[position_col])
    cost = pd.Series(0.0, index=out.index)
    cost[pos_diff > 0] = commission_rate               # 進場：手續費
    cost[pos_diff < 0] = commission_rate + tax_rate     # 出場：手續費 + 證交稅
    out["strategy_return"] = out["strategy_return"] - cost

    out["equity"] = initial_capital * (1 + out["strategy_return"]).cumprod()
    out["buy_hold_equity"] = initial_capital * (1 + out["daily_return"]).cumprod()

    return out


def count_trades(position: pd.Series) -> int:
    """計算完整交易次數（0→1 進場算一次），用來衡量策略被『洗出場』的頻繁程度"""
    entries = (position.diff().fillna(position) > 0).sum()
    return int(entries)


def compute_metrics(equity: pd.Series) -> dict:
    """計算總報酬、年化報酬(CAGR)、最大回撤、Sharpe、勝率"""
    ret = equity.pct_change().dropna()

    total_return = equity.iloc[-1] / equity.iloc[0] - 1

    n_years = len(equity) / 252
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 else np.nan

    cum_max = equity.cummax()
    drawdown = equity / cum_max - 1
    max_drawdown = drawdown.min()

    sharpe = (ret.mean() / ret.std()) * np.sqrt(252) if ret.std() > 0 else np.nan

    nonzero = ret[ret != 0]
    win_rate = (nonzero > 0).sum() / len(nonzero) if len(nonzero) > 0 else np.nan

    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "win_rate": win_rate,
    }
