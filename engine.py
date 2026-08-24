"""
回測引擎 (Backtest Engine)
-------------------------
Event-driven 設計，逐根K線往前推進，模擬真實交易流程：
  1. 讀取當前K線
  2. 檢查手上部位是否觸發止損/止盈 -> 優先處理
  3. 呼叫策略取得訊號 -> 進場/出場
  4. 計算手續費、滑價、更新資金曲線

刻意不用向量化一次算完，是因為止損邏輯需要「知道當下發生了什麼」，
向量化回測很容易不小心用到未來資訊 (look-ahead bias)。
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class Signal(Enum):
    HOLD = 0
    BUY = 1
    SELL = 2  # 出清多單


class StopLossType(Enum):
    FIXED_PCT = "fixed_pct"       # 固定百分比止損
    ATR = "atr"                   # ATR動態止損
    TRAILING = "trailing"         # 移動停利
    TIME = "time"                 # 持有超過N根K線強制出場


@dataclass
class StopLossConfig:
    type: StopLossType = StopLossType.FIXED_PCT
    fixed_pct: float = 0.05        # 固定止損 5%
    atr_multiplier: float = 2.0    # ATR止損倍數
    atr_period: int = 14
    trailing_pct: float = 0.08     # 移動停利回撤幅度
    max_hold_bars: int = 60        # 時間止損（K線根數）


@dataclass
class RiskConfig:
    max_position_pct: float = 0.2      # 單筆部位不超過總資金20%
    max_daily_loss_pct: float = 0.03   # 單日虧損達3%就熔斷停止當日交易
    commission_pct: float = 0.001425   # 台股手續費約0.1425%
    tax_pct: float = 0.003             # 台股證交稅0.3%（賣出才收）
    slippage_pct: float = 0.001        # 滑價估計


class Strategy(Protocol):
    """策略介面：所有策略都要實作 generate_signal"""
    def generate_signal(self, history: pd.DataFrame) -> Signal: ...


@dataclass
class Position:
    entry_price: float
    entry_date: pd.Timestamp
    shares: float
    highest_price: float  # 用於 trailing stop
    bars_held: int = 0


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    exit_reason: str


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        initial_capital: float = 1_000_000,
        stop_loss_config: Optional[StopLossConfig] = None,
        risk_config: Optional[RiskConfig] = None,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.stop_cfg = stop_loss_config or StopLossConfig()
        self.risk_cfg = risk_config or RiskConfig()

        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self.equity_curve: list[dict] = []
        self._daily_start_equity: float = initial_capital
        self._current_day: Optional[pd.Timestamp] = None
        self._halted_today = False

    # ---------- 止損判斷 ----------
    def _check_stop_loss(self, bar: pd.Series, atr: Optional[float]) -> Optional[str]:
        if self.position is None:
            return None
        pos = self.position
        price = bar["low"]  # 保守假設：用當根最低價檢查是否觸及止損

        if self.stop_cfg.type == StopLossType.FIXED_PCT:
            stop_price = pos.entry_price * (1 - self.stop_cfg.fixed_pct)
            if price <= stop_price:
                return f"fixed_stop@{stop_price:.2f}"

        elif self.stop_cfg.type == StopLossType.ATR and atr is not None:
            stop_price = pos.entry_price - self.stop_cfg.atr_multiplier * atr
            if price <= stop_price:
                return f"atr_stop@{stop_price:.2f}"

        elif self.stop_cfg.type == StopLossType.TRAILING:
            pos.highest_price = max(pos.highest_price, bar["high"])
            stop_price = pos.highest_price * (1 - self.stop_cfg.trailing_pct)
            if price <= stop_price:
                return f"trailing_stop@{stop_price:.2f}"

        elif self.stop_cfg.type == StopLossType.TIME:
            if pos.bars_held >= self.stop_cfg.max_hold_bars:
                return "time_stop"

        return None

    # ---------- 部位大小計算 ----------
    def _calc_shares(self, price: float) -> float:
        max_position_value = self.cash * self.risk_cfg.max_position_pct
        shares = max_position_value / price
        return max(shares, 0)

    def _apply_costs(self, price: float, shares: float, is_buy: bool) -> float:
        """回傳含滑價/手續費/稅後的實際成交金額"""
        slip = price * self.risk_cfg.slippage_pct
        exec_price = price + slip if is_buy else price - slip
        gross = exec_price * shares
        commission = gross * self.risk_cfg.commission_pct
        tax = gross * self.risk_cfg.tax_pct if not is_buy else 0
        return gross, commission + tax, exec_price

    def _enter(self, date, price):
        shares = self._calc_shares(price)
        if shares <= 0:
            return
        gross, costs, exec_price = self._apply_costs(price, shares, is_buy=True)
        total_cost = gross + costs
        if total_cost > self.cash:
            shares = self.cash / (price * (1 + self.risk_cfg.slippage_pct + self.risk_cfg.commission_pct))
            gross, costs, exec_price = self._apply_costs(price, shares, is_buy=True)
            total_cost = gross + costs
        self.cash -= total_cost
        self.position = Position(entry_price=exec_price, entry_date=date, shares=shares, highest_price=price)
        logger.debug("進場 %s 股數=%.0f 價格=%.2f", date, shares, exec_price)

    def _exit(self, date, price, reason: str):
        pos = self.position
        gross, costs, exec_price = self._apply_costs(price, pos.shares, is_buy=False)
        proceeds = gross - costs
        self.cash += proceeds
        cost_basis = pos.entry_price * pos.shares
        pnl = proceeds - cost_basis
        self.trades.append(Trade(
            entry_date=pos.entry_date, exit_date=date,
            entry_price=pos.entry_price, exit_price=exec_price,
            shares=pos.shares, pnl=pnl, exit_reason=reason,
        ))
        logger.debug("出場 %s 原因=%s 損益=%.0f", date, reason, pnl)
        self.position = None

    def _portfolio_value(self, price: float) -> float:
        if self.position:
            return self.cash + self.position.shares * price
        return self.cash

    def _calc_atr(self, history: pd.DataFrame) -> Optional[float]:
        if len(history) < self.stop_cfg.atr_period + 1:
            return None
        h, l, c = history["high"], history["low"], history["close"]
        prev_c = c.shift(1)
        tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
        return tr.rolling(self.stop_cfg.atr_period).mean().iloc[-1]

    # ---------- 主迴圈 ----------
    def run(self, data: pd.DataFrame) -> dict:
        """
        data: index為日期, 欄位需含 open/high/low/close/volume, 由舊到新排序
        """
        for i in range(len(data)):
            bar = data.iloc[i]
            date = data.index[i]
            history = data.iloc[: i + 1]

            # 每日重置熔斷狀態
            day = date.normalize()
            if day != self._current_day:
                self._current_day = day
                self._daily_start_equity = self._portfolio_value(bar["close"])
                self._halted_today = False

            # 熔斷檢查：單日虧損超過門檻就不再開新倉（但仍檢查既有部位止損）
            current_equity = self._portfolio_value(bar["close"])
            daily_pnl_pct = (current_equity - self._daily_start_equity) / self._daily_start_equity
            if daily_pnl_pct <= -self.risk_cfg.max_daily_loss_pct:
                self._halted_today = True

            # 1. 先檢查止損（比策略訊號優先）
            if self.position is not None:
                atr = self._calc_atr(history) if self.stop_cfg.type == StopLossType.ATR else None
                stop_reason = self._check_stop_loss(bar, atr)
                if stop_reason:
                    self._exit(date, bar["close"], stop_reason)
                else:
                    self.position.bars_held += 1

            # 2. 策略訊號
            if not self._halted_today:
                signal = self.strategy.generate_signal(history)
                if signal == Signal.BUY and self.position is None:
                    self._enter(date, bar["close"])
                elif signal == Signal.SELL and self.position is not None:
                    self._exit(date, bar["close"], "signal_exit")

            self.equity_curve.append({
                "date": date,
                "equity": self._portfolio_value(bar["close"]),
                "cash": self.cash,
                "in_position": self.position is not None,
            })

        return self._summarize()

    def _summarize(self) -> dict:
        equity_df = pd.DataFrame(self.equity_curve).set_index("date")
        returns = equity_df["equity"].pct_change().dropna()
        total_return = (equity_df["equity"].iloc[-1] / self.initial_capital - 1) if len(equity_df) else 0
        max_dd = self._max_drawdown(equity_df["equity"])
        win_trades = [t for t in self.trades if t.pnl > 0]
        win_rate = len(win_trades) / len(self.trades) if self.trades else 0
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

        return {
            "final_equity": equity_df["equity"].iloc[-1] if len(equity_df) else self.initial_capital,
            "total_return_pct": total_return * 100,
            "max_drawdown_pct": max_dd * 100,
            "num_trades": len(self.trades),
            "win_rate_pct": win_rate * 100,
            "sharpe_ratio": sharpe,
            "equity_curve": equity_df,
            "trades": self.trades,
        }

    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        cummax = equity.cummax()
        drawdown = (equity - cummax) / cummax
        return drawdown.min()
