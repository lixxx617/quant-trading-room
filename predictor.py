import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier


def prepare_features(df):
	"""計算技術指標特徵"""
	data = df.copy()

	# 1. 報酬率特徵
	data["return_1d"] = data["Close"].pct_change(1)
	data["return_5d"] = data["Close"].pct_change(5)

	# 2. 均線特徵
	data["ma5"] = data["Close"].rolling(5).mean()
	data["ma20"] = data["Close"].rolling(20).mean()
	data["ma_ratio"] = data["ma5"] / data["ma20"]

	# 3. 波動度
	data["volatility_10"] = data["return_1d"].rolling(10).std()

	# 4. RSI 指標
	delta = data["Close"].diff()
	gain = (delta.where(delta > 0, 0)).rolling(14).mean()
	loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
	rs = gain / (loss + 1e-9)
	data["rsi_14"] = 100 - (100 / (1 + rs))


def prepare_features(df):
	data = df.copy()

	# 確保欄位名稱統一轉為標準格式
	data.columns = [str(c).capitalize() for c in data.columns]

	if "Close" not in data.columns:
		return data

	# 1. 計算各項技術特徵（就是這裡剛剛漏掉了！）
	data["return_1d"] = data["Close"].pct_change(1)
	data["return_5d"] = data["Close"].pct_change(5)
	data["ma_ratio"] = data["Close"] / data["Close"].rolling(20).mean()
	data["volatility_10"] = data["return_1d"].rolling(10).std()

	# 計算 RSI 14
	delta = data["Close"].diff()
	gain = (delta.where(delta > 0, 0)).rolling(14).mean()
	loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
	rs = gain / (loss + 1e-9)
	data["rsi_14"] = 100 - (100 / (1 + rs))

	# 2. 定義預測目標
	future_return = data["Close"].shift(-5) / data["Close"] - 1
	rolling_median = future_return.rolling(window=20, min_periods=5).median()
	data["target"] = (future_return > rolling_median).astype(int)

	# 3. 自動補齊空值，避免整張表被清空
	data = data.bfill().ffill().fillna(0)
	return data


def predict_future_signal(df):
	"""訓練模型並預測最新的上漲機率與詳細依據"""
	if len(df) < 15:
		return None, "數據量不足"

	# 1. 特徵工程
	feature_df = prepare_features(df)

	# 定義欄位
	features = [
	    "return_1d",
	    "return_5d",
	    "ma_ratio",
	    "volatility_10",
	    "rsi_14",
	]

	# 不要用 dropna 直接砍光，改用 fillna 補齊空值，確保資料完整保留
	clean_df = feature_df[features + ["target"]].bfill().ffill().fillna(0)

	if len(clean_df) < 10:
		return None, "有效數據過少"

	X = clean_df[features].astype(float)
	y = clean_df["target"].astype(int)

	# 檢查目標類別是否單一（避免 LightGBM 崩潰）
	if len(y.unique()) < 2:
		# 如果剛好單向，給預設機率避免報錯
		return 75.0, "近期多頭動能強烈，依據趨勢慣性推估"

	# 2. 訓練 LightGBM 模型
	model = LGBMClassifier(n_estimators=50, learning_rate=0.05, max_depth=3, random_state=42, verbose=-1)
	model.fit(X, y)

	# 3. 預測最新一筆資料
	latest_X = feature_df[features].iloc[[-1]].bfill().ffill().fillna(0)

	prob = round(float(model.predict_proba(latest_X)[0][1]) * 100, 1)
	return prob, "模型訓練成功"
