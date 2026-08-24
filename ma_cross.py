"""
範例策略：均線交叉 (Moving Average Crossover)
--------------------------------------------
短線常用的入門策略：短均線上穿長均線 -> 買進；下穿 -> 賣出。
這裡只是「訊號產生器」的範例骨架，之後要換成 ML 模型，
只要保持 generate_signal(history) -> Signal 的介面就好，
回測引擎跟風控/止損模組完全不用動。
"""

from __future__ import annotations
import pandas as pd

from engine import Signal


class MACrossStrategy:
    def __init__(self, short_window: int = 5, long_window: int = 20):
        self.short_window = short_window
        self.long_window = long_window

    def generate_signal(self, history: pd.DataFrame) -> Signal:
        if len(history) < self.long_window + 1:
            return Signal.HOLD

        close = history["close"]
        short_ma = close.rolling(self.short_window).mean()
        long_ma = close.rolling(self.long_window).mean()

        prev_diff = short_ma.iloc[-2] - long_ma.iloc[-2]
        curr_diff = short_ma.iloc[-1] - long_ma.iloc[-1]

        if prev_diff <= 0 and curr_diff > 0:
            return Signal.BUY
        if prev_diff >= 0 and curr_diff < 0:
            return Signal.SELL
        return Signal.HOLD


class MLStrategyTemplate:
    """
    未來接 ML 模型的模板。實際訓練/推論邏輯之後再補，
    這裡先固定介面，讓回測引擎可以無痛切換策略。
    """

    def __init__(self, model=None, feature_fn=None, buy_threshold: float = 0.6):
        self.model = model
        self.feature_fn = feature_fn
        self.buy_threshold = buy_threshold

    def generate_signal(self, history: pd.DataFrame) -> Signal:
        if self.model is None or len(history) < 30:
            return Signal.HOLD
        features = self.feature_fn(history)
        prob_up = self.model.predict_proba(features)[-1][1]
        if prob_up > self.buy_threshold:
            return Signal.BUY
        elif prob_up < (1 - self.buy_threshold):
            return Signal.SELL
        return Signal.HOLD
