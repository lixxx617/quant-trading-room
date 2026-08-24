"""
技術指標計算工具
----------------
獨立成模組，讓策略之間可以共用，之後要加KD、MACD等指標也統一放這裡。
"""
from __future__ import annotations
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI。用 ewm(alpha=1/period) 而不是簡單移動平均，
    這是RSI原始定義的算法，跟大部分看盤軟體算出來的數字才會一致。
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    # avg_loss=0（連續上漲）時 RSI 應該是100
    result = result.where(avg_loss != 0, 100.0)
    return result


def kd(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 9,
       k_smooth: int = 3, d_smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    """隨機指標 KD。回傳 (K值, D值)"""
    lowest_low = low.rolling(period).min()
    highest_high = high.rolling(period).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, float("nan")) * 100
    k = rsv.ewm(alpha=1 / k_smooth, adjust=False).mean()
    d = k.ewm(alpha=1 / d_smooth, adjust=False).mean()
    return k, d


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """當根成交量 / N日均量，>1代表量能放大"""
    vol_ma = volume.rolling(period).mean()
    return volume / vol_ma.replace(0, float("nan"))
