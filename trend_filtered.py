"""
趨勢濾網策略 (Trend-Filtered MA Crossover)
------------------------------------------
在原本的均線交叉訊號上疊三層濾網，目的是把「雜訊交叉」擋掉：

  1. 趨勢濾網：close > 200日均線 才允許做多（避免逆勢單）
  2. 量能濾網：當根成交量 > N日均量 * 倍數 才視為有效訊號（避免無量假突破）
  3. RSI濾網：RSI 不能過熱（避免追高在超買區進場）

出場條件放寬（多一種方式都可以出場），因為停損停利已經在
BacktestEngine層處理，這裡的出場主要處理「訊號本身失效」的情況：
  - 均線死予交叉，或
  - 跌破200日均線（趨勢反轉）
"""
from __future__ import annotations
import pandas as pd

from engine import Signal
from indicators import sma, rsi, volume_ratio


class TrendFilteredMACross:
    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        trend_ma_period: int = 200,
        use_trend_filter: bool = True,
        volume_ma_period: int = 20,
        volume_multiplier: float = 1.2,
        use_volume_filter: bool = True,
        rsi_period: int = 14,
        rsi_overbought: float = 70,
        use_rsi_filter: bool = True,
    ):
        self.short_window = short_window
        self.long_window = long_window
        self.trend_ma_period = trend_ma_period
        self.use_trend_filter = use_trend_filter
        self.volume_ma_period = volume_ma_period
        self.volume_multiplier = volume_multiplier
        self.use_volume_filter = use_volume_filter
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.use_rsi_filter = use_rsi_filter

    def _min_bars_needed(self) -> int:
        needed = [self.long_window + 1]
        if self.use_trend_filter:
            needed.append(self.trend_ma_period + 1)
        if self.use_volume_filter:
            needed.append(self.volume_ma_period + 1)
        if self.use_rsi_filter:
            needed.append(self.rsi_period + 1)
        return max(needed)

    def generate_signal(self, history: pd.DataFrame) -> Signal:
        if len(history) < self._min_bars_needed():
            return Signal.HOLD

        close = history["close"]
        short_ma = sma(close, self.short_window)
        long_ma = sma(close, self.long_window)

        prev_diff = short_ma.iloc[-2] - long_ma.iloc[-2]
        curr_diff = short_ma.iloc[-1] - long_ma.iloc[-1]
        golden_cross = prev_diff <= 0 and curr_diff > 0
        death_cross = prev_diff >= 0 and curr_diff < 0

        # ---- 出場：均線死亡交叉 或 跌破長期趨勢線，任一成立就出場 ----
        if death_cross:
            return Signal.SELL
        if self.use_trend_filter:
            trend_ma = sma(close, self.trend_ma_period)
            if close.iloc[-1] < trend_ma.iloc[-1]:
                return Signal.SELL

        # ---- 進場：均線黃金交叉 + 通過所有已啟用的濾網 ----
        if not golden_cross:
            return Signal.HOLD

        if self.use_trend_filter:
            trend_ma = sma(close, self.trend_ma_period)
            if close.iloc[-1] <= trend_ma.iloc[-1]:
                return Signal.HOLD  # 股價還在200日均線之下，不做多

        if self.use_volume_filter:
            vol_ratio = volume_ratio(history["volume"], self.volume_ma_period)
            if pd.isna(vol_ratio.iloc[-1]) or vol_ratio.iloc[-1] < self.volume_multiplier:
                return Signal.HOLD  # 量能不夠，訊號可信度低

        if self.use_rsi_filter:
            rsi_val = rsi(close, self.rsi_period)
            if pd.isna(rsi_val.iloc[-1]) or rsi_val.iloc[-1] >= self.rsi_overbought:
                return Signal.HOLD  # 已經超買，不追高

        return Signal.BUY
