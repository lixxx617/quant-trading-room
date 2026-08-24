# AI 買股票系統 - 資料層 + 回測引擎（第一階段）

## 這階段做了什麼
- `data/data_layer.py`：資料抽象層。`DataSource` 是介面，`YFinanceSource` 是預設實作，
  之後接台股券商正式行情（富果、永豐等）只要新寫一個 class 實作同樣介面即可，
  上層策略/回測完全不用改。內建 SQLite 快取，避免重複打 API。
- `backtest/engine.py`：event-driven 回測引擎，逐根K線推進。支援：
  - 4 種止損：固定百分比 / ATR動態 / 移動停利(trailing) / 時間止損
  - 部位大小控制、單日虧損熔斷（帳戶層級風控）
  - 手續費、證交稅、滑價成本計算（預設用台股費率，美股要另外調）
- `strategies/ma_cross.py`：範例策略（均線交叉）+ ML策略的模板介面
- `test_pipeline.py`：用模擬資料驗證整條 pipeline，已跑通無誤

## 如何在本機用真實資料跑
```bash
pip install -r requirements.txt

python3 -c "
from data.data_layer import MarketDataStore, tw_symbol
store = MarketDataStore()
df = store.load(tw_symbol('2330'), '2023-01-01', '2025-01-01')  # 台積電
print(df.tail())
"
```

然後把 `test_pipeline.py` 裡的 `make_synthetic_data()` 換成
`MarketDataStore().load(...)` 抓回來的真實資料，就能拿真實台股跑回測。

美股資料不用加後綴，例如 `store.load('AAPL', '2023-01-01', '2025-01-01')`。

## 目前設計上的限制（先讓你知道，不是bug）
1. **止損用當根 low 判斷**：保守假設，實際上可能沒那麼容易觸發，
   之後可以改成用分鐘資料模擬更精確的觸價時機。
2. **沒有處理除權息**：yfinance 的 close 預設不是還原股價，長線回測
   如果標的有配股配息，報酬率會失真，要改用 `adj_close` 或自行還原。
3. **短線 vs 長線目前共用同一個回測引擎**：這是刻意的，差異應該體現在
   策略邏輯（K線週期、持有時間）跟 StopLossConfig 參數上，而不是另外
   寫一套引擎，避免兩套邏輯不同步。
4. **這是回測，不是實盤**：回測績效好不代表實盤會賺錢（滑價、流動性、
   心理因素在真實交易中都會打折扣），正式接券商下單前務必先跑一段時間的
   紙上交易 (paper trading)。

## 下一步（照你原本的規劃）
你說全自動下單要先接台股，順序建議：
1. 先把這個回測引擎跑過幾個策略、選出一個穩定的（不要只看單一段時間）
2. 接台股券商 API（永豐 Shioaji 或富果 Fugle，兩個都有 Python SDK 且都支援下單）
   → 先串「查詢帳戶/查詢報價」，確認連線穩，再串下單
3. 把 `BacktestEngine` 的訊號邏輯抽出來，包成「即時模式」：
   收到新K線 -> 產生訊號 -> 過風控檢查 -> 呼叫券商下單 API
   （這樣策略邏輯只寫一次，回測跟實盤共用，不會有「回測寫一套、實盤寫一套」結果兜不起來的問題）
4. 全自動下單上線前，先切「訊號模式」跑至少幾週，人工比對訊號跟你自己盤感是否合理

想先做哪一步？例如要不要我先把「永豐 Shioaji API 串接查詢報價」的骨架也寫出來？
