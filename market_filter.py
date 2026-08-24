"""
market_filter.py
大盤（市場整體）趨勢濾網

用 0050.TW（或其他大盤代理標的，例如台灣加權指數）的 200 日均線，
判斷目前大盤是多頭還是空頭，作為所有個股「進場」的總開關：
    - 大盤收盤 > 200MA → 多頭 → 允許個股依自身訊號進場
    - 大盤收盤 < 200MA → 空頭 → 全面禁止開新倉，保留現金

重要設計原則
------------
1. 只作用在「進場」，不影響「出場」：任何時候都可以離場停損/停利，
   但只有大盤偏多時才允許開新倉（風控優先於進攻）。
2. 避免未來函數：market_trend(t) 只用截至第 t 天（含）的收盤價計算，
   不使用任何未來資料。
3. 對齊個股交易日曆時，缺值一律視為「空頭 / 濾網不通過」的 fail-safe
   設計 —— 資料有缺漏時，寧可錯過進場機會，也不要在濾網失效的情況下
   誤判為可以進場。
"""

import pandas as pd


def compute_market_trend(market_df: pd.DataFrame, ma_period: int = 200, price_col: str = "close") -> pd.Series:
    """
    計算大盤趨勢燈號。

    market_df 需包含 price_col 欄位（預設 close），index 為日期。
    回傳布林 Series：True=多頭(可開新倉)，False=空頭(禁止開新倉)。
    rolling 暖機期間（MA 尚未成熟，為 NaN）一律標記為 False，
    避免暖機期間因 NaN 比較被誤判。
    """
    if price_col not in market_df.columns:
        raise ValueError(f"market_df 缺少欄位 '{price_col}'")

    ma = market_df[price_col].rolling(ma_period).mean()
    trend = market_df[price_col] > ma
    trend = trend.where(ma.notna(), other=False)
    trend.name = "market_bullish"
    return trend


def align_market_trend(market_trend: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """
    將大盤濾網對齊到個股的交易日曆。

    用 ffill 處理極少數的日曆落差（例如兩者資料來源在個別交易日有缺漏），
    對齊後仍缺值（例如目標日期早於大盤資料起始日）一律視為 False。
    """
    aligned = market_trend.reindex(target_index, method="ffill")
    aligned = aligned.fillna(False)
    aligned.name = "market_bullish"
    return aligned
