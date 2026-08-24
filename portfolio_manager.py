"""
portfolio_manager.py
多標的組合投資的資金管理與部位分配（Position Sizing）

規則（依需求鎖定）：
    - 最高同時持有 4 檔標的
    - 每檔部位上限為「總資金」的 25%
    - 大盤（見 market_filter.py）處於空頭時，完全不開新倉，保留現金
    - 出場不受上述任何限制 —— 任何時候都可以離場（風控優先）
    - 買進以台股「一張」（1000股）為最小交易單位，無法湊到一張的資金
      直接跳過（不做零股）

這個模組刻意保持「無狀態、純函式邏輯」：不自己保存持倉，而是每次呼叫
plan_actions() 時，把目前的持倉狀態（state）與今天各標的的訊號
（signals）一起傳入，回傳建議動作清單。真正的持倉狀態由呼叫端
（例如 generate_daily_signals.py 讀寫的 JSON 檔）負責保存，
避免這個模組因為狀態不同步而算出跟實際帳戶對不上的建議。

已知限制（刻意簡化，非bug）：
    - 不支援加碼/減碼（pyramiding）：已持有的標的即使又出現進場訊號，
      也維持 HOLD，不加碼。
    - 不做同日多檔進場的動能排名，預設依 signals 字典的走訪順序決定
      優先權；若要依訊號強度排序，請呼叫端先排序好 signals 再傳入。
    - 部位大小以「總資金 × 25%」計算，不是「目前現金 × 25%」，
      所以總曝險上限視 max_positions × max_allocation_pct 而定
      （4 檔 × 25% = 100%，代表最多可以全數投入，不留緩衝現金）。
      如果想保留額外緩衝，請把 max_allocation_pct 設低一點
      （例如 0.20，4 檔滿倉時仍保留 20% 現金）。
"""

from dataclasses import dataclass


@dataclass
class PortfolioConfig:
    max_positions: int = 4          # 最高同時持有檔數
    max_allocation_pct: float = 0.25  # 每檔部位上限（佔總資金比例）
    lot_size: int = 1000            # 台股一張 = 1000 股
    reserve_cash_in_bear: bool = True  # 大盤空頭時是否保留現金、禁止開新倉


class PortfolioManager:
    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig()

    def plan_actions(
        self,
        state: dict,
        signals: dict,
        market_bullish: bool,
        prices: dict,
    ) -> list:
        """
        規劃今日的組合動作。

        參數：
            state   : {"cash": float, "holdings": {ticker: {"shares": int, "avg_cost": float}},
                       "total_capital": float(可省略，省略時用 cash + 持倉市值估算)}
            signals : {ticker: "entry" | "exit" | "hold"}  各標的今日訊號
            market_bullish : bool  大盤濾網今日是否為多頭
            prices  : {ticker: float}  各標的最新收盤價（用於估算部位大小與市值）

        回傳：
            list[dict]，每個 dict 描述一個建議動作，
            action 欄位為 "SELL" / "BUY" / "HOLD" / "SKIP" / "NO_ACTION" 之一。
        """
        c = self.config
        holdings = dict(state.get("holdings", {}) or {})
        cash = float(state.get("cash", 0.0))

        if "total_capital" in state and state["total_capital"] is not None:
            total_capital = float(state["total_capital"])
        else:
            holdings_value = sum(
                pos["shares"] * prices.get(ticker, 0.0) for ticker, pos in holdings.items()
            )
            total_capital = cash + holdings_value

        actions = []

        # 1) 出場優先處理，且不受大盤濾網或持倉數限制 —— 任何時候都能離場
        exited = set()
        for ticker, pos in holdings.items():
            if signals.get(ticker) == "exit":
                shares = pos["shares"]
                price = prices.get(ticker)
                actions.append({
                    "ticker": ticker,
                    "action": "SELL",
                    "shares": shares,
                    "ref_price": price,
                    "reason": "觸發出場條件，出場不受大盤濾網或持倉數上限限制",
                })
                exited.add(ticker)

        remaining_holdings = {t: p for t, p in holdings.items() if t not in exited}
        open_slots = c.max_positions - len(remaining_holdings)

        # 2) 大盤濾網：空頭時完全不開新倉
        if not market_bullish and c.reserve_cash_in_bear:
            for ticker, sig in signals.items():
                if ticker not in holdings and sig == "entry":
                    actions.append({
                        "ticker": ticker,
                        "action": "SKIP",
                        "shares": 0,
                        "reason": "大盤濾網為空頭，保留現金、不開新倉",
                    })
        else:
            # 3) 進場：依 signals 走訪順序分配，最多 open_slots 檔，
            #    每檔資金上限 max_allocation_pct * total_capital，用剩餘現金與上限取小值
            entry_candidates = [t for t, sig in signals.items() if sig == "entry" and t not in holdings]
            for ticker in entry_candidates:
                if open_slots <= 0:
                    actions.append({
                        "ticker": ticker, "action": "SKIP", "shares": 0,
                        "reason": f"已達最大持倉數上限（{c.max_positions} 檔）",
                    })
                    continue

                price = prices.get(ticker)
                if not price or price <= 0:
                    actions.append({
                        "ticker": ticker, "action": "SKIP", "shares": 0,
                        "reason": "缺少有效報價，無法計算部位大小",
                    })
                    continue

                target_amount = min(c.max_allocation_pct * total_capital, cash)
                lots = int(target_amount // (price * c.lot_size))
                shares = lots * c.lot_size

                if shares <= 0:
                    actions.append({
                        "ticker": ticker, "action": "SKIP", "shares": 0,
                        "reason": f"可用資金不足以買進最小單位（1張={c.lot_size}股）",
                    })
                    continue

                cost = shares * price
                cash -= cost
                open_slots -= 1
                actions.append({
                    "ticker": ticker,
                    "action": "BUY",
                    "shares": shares,
                    "ref_price": price,
                    "est_cost": cost,
                    "reason": f"進場訊號確認，配置上限 {c.max_allocation_pct*100:.0f}% 總資金",
                })

        # 4) 其餘標的：維持現狀（已處理過的出場/進場不重複輸出）
        handled = {a["ticker"] for a in actions}
        for ticker, sig in signals.items():
            if ticker in handled:
                continue
            if ticker in holdings:
                actions.append({
                    "ticker": ticker, "action": "HOLD",
                    "shares": holdings[ticker]["shares"],
                    "reason": "持有中，未觸發出場",
                })
            else:
                actions.append({
                    "ticker": ticker, "action": "NO_ACTION", "shares": 0,
                    "reason": "未觸發進場條件" if sig != "entry" else "已達持倉/資金限制",
                })

        return actions
