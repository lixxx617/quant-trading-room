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

	# 定義預測目標：未來 5 天收盤價是否高於今天（1為漲，0為跌/平）
	data["target"] = (data["Close"].shift(-5) > data["Close"]).astype(int)

	return data


def predict_future_signal(df):
	"""訓練模型並預測最新的上漲機率與詳細依據"""
	if len(df) < 60:
		return None, "數據量不足 (需至少60根K線)"

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

	# 清理缺失值
	clean_df = feature_df.dropna(subset=features + ["target"])

	if len(clean_df) < 40:
		return None, "有效數據過少"

	X = clean_df[features]
	y = clean_df["target"]

	# 2. 訓練 LightGBM 模型
	model = LGBMClassifier(n_estimators=50, learning_rate=0.05, max_depth=3, random_state=42, verbose=-1)
	model.fit(X, y)

	# 3. 預測最新一筆資料
	latest_X = feature_df[features].iloc[[-1]]

	if latest_X.isna().any().any():
		return None, "最新數據缺失"

	prob = round(float(model.predict_proba(latest_X)[0][1]) * 100, 1)

	# 4. 動態生成判斷依據 (根據最新特徵數據)
	latest_row = feature_df.iloc[-1]
	reasons = []

	# 依據 1：均線位置
	ma_ratio = latest_row.get("ma_ratio", 1)
	if ma_ratio > 1.02:
		reasons.append("短天期均線強勢多頭排列")
	elif ma_ratio < 0.98:
		reasons.append("短天期均線呈空頭排列")
	else:
		reasons.append("均線糾結且震盪走平")

	# 依據 2：RSI 指標狀態
	rsi = latest_row.get("rsi_14", 50)
	if rsi >= 70:
		reasons.append("RSI 進入超買區 (需留意回檔風險)")
	elif rsi <= 30:
		reasons.append("RSI 進入超賣區 (可能出現反彈)")
	elif rsi > 50:
		reasons.append("RSI 大於 50 偏多方控盤")
	else:
		reasons.append("RSI 小於 50 偏空方控盤")

	# 依據 3：近期動能
	ret_5d = latest_row.get("return_5d", 0)
	if ret_5d > 0.03:
		reasons.append("近 5 日價格動能強勁")
	elif ret_5d < -0.03:
		reasons.append("近 5 日價格跌勢較深")

	msg = "；".join(reasons) + "。"

	return prob, msg
