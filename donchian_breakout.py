"""
donchian_breakout.py
唐奇安通道突破策略 (Donchian Channel Breakout Strategy)

★ 標準策略架構（已定案）
------------------------
entry_period=20（20日新高進場）+ exit_period=20（20日新低出場，
donchian_low 模式）+ 200日均線進場濾網。此組合（代號 donchian_M20）
經多標的分段驗證比較後勝出：
    - 全期間總報酬優於 M=10 版本（80.8% → 118.3%，依標的而異）
    - 全期間最大回撤更低（-20.6% → -12.5%）
    - 交易次數更少（26→17），代表被長牛整理洗出場的頻率下降，
      多頭吃肉比例提升
即 DonchianBreakoutParams() 的預設值已經是這個標準架構，無須額外設定。

策略邏輯
--------
- 進場：收盤價創 N 日新高（不含當日）→ 做多
- 出場：收盤價創 M 日新低（不含當日）→ 出場
- 趨勢濾網：僅在收盤價 > 200 日均線時允許「進場」（維持熊市防守力）
- 移除 RSI>70 濾網 —— 這是舊版策略在 2023-2024 多頭中只拿到 4.8%
  報酬（遠輸 Buy & Hold 146%）的主因：RSI 濾網會在強勢多頭中
  持續過濾掉進場訊號，讓策略錯過整段趨勢。

設計說明
--------
- 唐奇安通道用 .shift(1) 計算，避免用到當日的高/低價造成未來函數。
- 200 日均線僅作為「進場關卡」，預設不強制出場，出場交給出場規則
  （見下方 exit_mode）。
- 若想要更保守、更接近舊版 0% 回撤的防守力，可將
  `exit_on_trend_break=True`，跌破 200MA 時強制出場（見下方參數）。

出場模式 exit_mode（解決長牛行情被 M 日新低頻繁洗出場的問題）
------------------------------------------------------------
- "donchian_low"（預設）：收盤跌破 M 日新低出場。M 太小（如10）在
  長牛整理時容易被正常拉回洗出場，導致「吃肉比例」下降。
- "ma_cross"：改用「收盤跌破 exit_ma_period 日均線」出場（移動停利，
  類似海龜法則的長期趨勢出場）。均線出場對短期拉回的容忍度通常比
  M 日新低更高，能減少假出場、拉長波段持有時間，但代價是回撤可能
  略放大、出場反應也可能較慢。
- 若只是想「加大 M 值」而不換出場邏輯，直接把 exit_period 調大
  （例如 20）即可，仍用 "donchian_low" 模式。

大盤濾網 require_market_filter（總體市場總開關）
------------------------------------------------
- 個股自己的 200MA 只能反映「這檔股票」的趨勢，無法反映大盤系統性風險
  （例如個股因公司利多逆勢創高，但大盤實際上已轉空）。
- 啟用後，generate_signals() 必須額外傳入 market_trend（由
  market_filter.py 產生的布林 Series，True=大盤多頭）。
  進場條件變成：個股突破 AND 個股200MA濾網 AND 大盤200MA濾網 全部成立。
- 出場規則「不」受大盤濾網影響 —— 風控原則是任何時候都可以離場，
  但只有大盤偏多時才允許開新倉。
- 若未啟用（預設 False），行為與舊版完全相同，向下相容
  compare_exit_strategies.py 等既有腳本。
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class DonchianBreakoutParams:
    entry_period: int = 20        # N 日新高，進場
    exit_period: int = 20         # M 日新低，出場（標準架構：與 entry 相同天數，donchian_M20）
    trend_ma_period: int = 200    # 200 日均線趨勢濾網
    use_trend_filter: bool = True     # 是否啟用 200MA 進場濾網
    exit_on_trend_break: bool = False  # 額外防禦：跌破200MA時強制出場（加強熊市防守）
    exit_mode: str = "donchian_low"    # "donchian_low" | "ma_cross"
    exit_ma_period: int = 20           # exit_mode="ma_cross" 時使用的均線天數
    require_market_filter: bool = False  # 是否啟用大盤(如0050)200MA進場總開關


class DonchianBreakoutStrategy:
    """唐奇安通道突破策略（保留 200MA 趨勢濾網，移除 RSI 濾網）"""

    def __init__(self, params: DonchianBreakoutParams = None):
        self.params = params or DonchianBreakoutParams()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        計算唐奇安通道與趨勢均線。
        df 需包含欄位: high, low, close（index 建議為日期）
        """
        p = self.params
        out = df.copy()

        # 唐奇安通道：shift(1) 避免用到當日高低價（防止未來函數）
        out["donchian_high"] = out["high"].rolling(p.entry_period).max().shift(1)
        out["donchian_low"] = out["low"].rolling(p.exit_period).min().shift(1)

        # 200 日趨勢均線
        out["trend_ma"] = out["close"].rolling(p.trend_ma_period).mean()

        # 出場用均線（僅 exit_mode="ma_cross" 時使用；用當日收盤計算，
        # 比較 close(t) vs MA(t) 屬標準做法，不構成未來函數）
        out["exit_ma"] = out["close"].rolling(p.exit_ma_period).mean()

        return out

    def generate_signals(self, df: pd.DataFrame, market_trend: pd.Series = None) -> pd.DataFrame:
        """
        產生進出場訊號與實際持倉狀態。

        參數：
            df            : 個股 OHLCV 資料
            market_trend  : 選用。布林 Series（index為日期，True=大盤多頭），
                             由 market_filter.compute_market_trend() 產生。
                             當 params.require_market_filter=True 時必須提供，
                             否則丟出 ValueError（避免誤以為濾網已生效但其實沒套用）。

        回傳新增欄位：
            donchian_high / donchian_low / trend_ma : 指標值
            market_ok : 大盤濾網是否通過（True=可進場），未啟用時全部為 True
            signal   : 1=進場訊號, -1=出場訊號, 0=無動作（原始訊號，可能連續出現）
            position : 實際持倉狀態 (1=持有, 0=空手)，已處理「已持倉時不重複進場」邏輯

        未來函數防範：
            - donchian_high/low 用 shift(1)，不含當日高低價。
            - trend_ma / exit_ma / market_trend 皆只使用「截至當日」的收盤價，
              且訊號設計為「收盤後產生、隔日執行」，不構成未來函數。
        """
        p = self.params
        out = self.compute_indicators(df)

        breakout_up = out["close"] > out["donchian_high"]

        if p.exit_mode == "donchian_low":
            exit_trigger = out["close"] < out["donchian_low"]
        elif p.exit_mode == "ma_cross":
            exit_trigger = out["close"] < out["exit_ma"]
        else:
            raise ValueError(f"未知的 exit_mode: {p.exit_mode!r}，請用 'donchian_low' 或 'ma_cross'")

        if p.use_trend_filter:
            trend_ok = out["close"] > out["trend_ma"]
        else:
            trend_ok = pd.Series(True, index=out.index)

        if p.require_market_filter:
            if market_trend is None:
                raise ValueError(
                    "params.require_market_filter=True，但沒有傳入 market_trend。"
                    "請先用 market_filter.compute_market_trend() 計算大盤趨勢，"
                    "再用 market_filter.align_market_trend() 對齊到本標的的交易日曆後傳入，"
                    "或將 require_market_filter 設為 False。"
                )
            # 對齊交易日曆；缺值一律視為 False（fail-safe：寧可少賺不可多冒風險）
            market_ok = market_trend.reindex(out.index).fillna(False)
        else:
            market_ok = pd.Series(True, index=out.index)
        out["market_ok"] = market_ok

        if p.exit_on_trend_break:
            trend_break = out["close"] < out["trend_ma"]
        else:
            trend_break = pd.Series(False, index=out.index)

        # 大盤濾網只作用在「進場」，出場永遠不受大盤濾網限制（風控優先）
        entry_signal = breakout_up & trend_ok & market_ok
        exit_signal = exit_trigger | trend_break

        out["signal"] = 0
        out.loc[entry_signal, "signal"] = 1
        out.loc[exit_signal, "signal"] = -1

        # 轉換成連續持倉狀態（避免重複進場 / 空手時重複出場）
        position = np.zeros(len(out))
        holding = 0
        entry_arr = entry_signal.to_numpy()
        exit_arr = exit_signal.to_numpy()

        for i in range(len(out)):
            if holding == 0 and entry_arr[i]:
                holding = 1
            elif holding == 1 and exit_arr[i]:
                holding = 0
            position[i] = holding

        out["position"] = position
        return out


if __name__ == "__main__":
    # 簡單自我測試：用隨機資料確認流程能跑通（正式驗證請用 validate_strategy.py）
    rng = pd.date_range("2020-01-01", periods=300, freq="B")
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(len(rng)))
    demo_df = pd.DataFrame(
        {
            "open": close,
            "high": close + np.random.rand(len(rng)),
            "low": close - np.random.rand(len(rng)),
            "close": close,
            "volume": np.random.randint(1000, 5000, len(rng)),
        },
        index=rng,
    )

    strategy = DonchianBreakoutStrategy(DonchianBreakoutParams(trend_ma_period=50))
    signals = strategy.generate_signals(demo_df)
    print(signals[["close", "donchian_high", "donchian_low", "trend_ma", "signal", "position"]].tail(10))
