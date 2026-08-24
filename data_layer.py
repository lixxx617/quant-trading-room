"""
資料層 (Data Layer)
------------------
負責：
1. 從資料來源（預設 yfinance）抓取台股/美股歷史K線
2. 存進本地 SQLite，避免重複打 API
3. 提供統一介面給回測引擎與策略層讀取

之後要換成富果 API / TEJ / 券商即時行情，只要實作同樣的
DataSource 介面，上層完全不用改。
"""

from __future__ import annotations
import sqlite3
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "market_data.db"


class DataSource(ABC):
	"""所有資料來源都要實作這個介面（yfinance / 富果 / TEJ / 券商API...）"""

	@abstractmethod
	def fetch(self, symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
		"""回傳欄位須包含: date, open, high, low, close, volume"""
		raise NotImplementedError


class YFinanceSource(DataSource):
	"""
    預設資料來源。免費但有限制：
    - 台股要在代號後面加 .TW（上市）或 .TWO（上櫃），例如 2330.TW
    - 美股直接用代號，例如 AAPL
    - 分鐘級資料只能抓最近 60 天，長線回測建議用日K
    """

	def fetch(self, symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
		try:
			import yfinance as yf
		except ImportError as e:
			raise ImportError(
			    "需要安裝 yfinance: pip install yfinance --break-system-packages"
			) from e

		# auto_adjust=True: close 會用還原股價（已處理除權息），
		# 長線回測一定要用這個，不然遇到配股配息報酬率會嚴重失真。
		df = yf.download(
		    symbol,
		    start=start,
		    end=end,
		    interval=interval,
		    progress=False,
		    auto_adjust=False,
		)
		if df.empty:
			logger.warning("抓不到資料: %s (%s ~ %s)", symbol, start, end)
			return pd.DataFrame()

		# yfinance 新版可能回傳 MultiIndex columns，攤平掉
		if isinstance(df.columns, pd.MultiIndex):
			df.columns = df.columns.get_level_values(0)

		df = df.reset_index()
		df.columns = [c.lower() for c in df.columns]
		df = df.rename(columns={"date": "date"})
		df["symbol"] = symbol

		df = df[["date", "symbol", "open", "high", "low", "close", "volume"]]
		before = len(df)
		df = df.dropna(subset=["open", "high", "low", "close"])
		if len(df) < before:
			logger.warning("%s 有 %d 筆資料缺值已捨棄（停牌/假日殘留）", symbol, before - len(df))
		return df


class MarketDataStore:
	"""本地 SQLite 快取層。所有策略/回測都應該透過這一層拿資料，不要直接呼叫 DataSource。"""

	def __init__(self, db_path: Path = DB_PATH, source: Optional[DataSource] = None):
		self.db_path = db_path
		self.source = source or YFinanceSource()
		self._init_db()

	def _init_db(self):
		with sqlite3.connect(self.db_path) as conn:
			conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    PRIMARY KEY (date, symbol)
                )
            """)
			conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON ohlcv(symbol, date)")

	def update(self, symbol: str, start: str, end: str, interval: str = "1d"):
		"""抓新資料並寫入（用 INSERT OR REPLACE 避免重複）"""
		df = self.source.fetch(symbol, start, end, interval)
		if df.empty:
			return 0
		df["date"] = df["date"].astype(str)
		with sqlite3.connect(self.db_path) as conn:
			df.to_sql("_tmp_ohlcv", conn, if_exists="replace", index=False)
			conn.execute("""
                INSERT OR REPLACE INTO ohlcv (date, symbol, open, high, low, close, volume)
                SELECT date, symbol, open, high, low, close, volume FROM _tmp_ohlcv
            """)
			conn.execute("DROP TABLE _tmp_ohlcv")
		logger.info("已更新 %s: %d 筆", symbol, len(df))
		return len(df)

	def load(self, symbol: str, start: str, end: str) -> pd.DataFrame:
		"""從本地 DB 讀取；讀不到就自動去source抓一次再讀"""
		with sqlite3.connect(self.db_path) as conn:
			df = pd.read_sql(
			    "SELECT * FROM ohlcv WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
			    conn, params=(symbol, start, end)
			)
		if df.empty:
			logger.info("本地無資料，嘗試線上抓取: %s", symbol)
			self.update(symbol, start, end)
			with sqlite3.connect(self.db_path) as conn:
				df = pd.read_sql(
				    "SELECT * FROM ohlcv WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
				    conn, params=(symbol, start, end)
				)
		df["date"] = pd.to_datetime(df["date"])
		return df.set_index("date")


# 常用代號對照（之後可擴充成完整清單或改讀設定檔）
TW_SUFFIX_LISTED = ".TW"   # 上市
TW_SUFFIX_OTC = ".TWO"     # 上櫃


def tw_symbol(stock_id: str, otc: bool = False) -> str:
	"""把台股代號轉成 yfinance 格式，例如 tw_symbol('2330') -> '2330.TW'"""
	suffix = TW_SUFFIX_OTC if otc else TW_SUFFIX_LISTED
	return f"{stock_id}{suffix}"


if __name__ == "__main__":
	logging.basicConfig(level=logging.INFO)
	store = MarketDataStore()
	# 範例：抓台積電近一年資料
	df = store.load(tw_symbol("2330"), "2024-01-01", "2025-01-01")
	print(df.tail())
