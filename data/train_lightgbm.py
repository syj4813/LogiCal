# -*- coding: utf-8 -*-
"""LightGBM으로 지연위험도(운휴 확률) 분류 모델 학습."""

import lightgbm as lgb
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.model_selection import train_test_split

df = pd.read_csv("delay_risk_dataset.csv")

CATEGORICAL = ["요일", "주운행선", "수송품목", "상하", "출발시간대"]
for col in CATEGORICAL:
    df[col] = df[col].astype("category")

FEATURES = [
    "요일", "월", "주운행선", "운행거리_km", "수송품목", "화물중량_톤",
    "상하", "출발시간대", "공차회송여부", "결합배송여부", "장마철여부", "동절기여부",
]
TARGET = "운휴_지연위험_실현"

X_train, X_test, y_train, y_test = train_test_split(
    df[FEATURES], df[TARGET], test_size=0.2, random_state=42, stratify=df[TARGET]
)

model = lgb.LGBMClassifier(
    n_estimators=300,
    learning_rate=0.05,
    num_leaves=15,
    random_state=42,
    verbosity=-1,
    importance_type="gain",  # split 기준은 연속형 변수(거리/중량)가 과대평가되는 경향이 있어 gain 기준 사용
)
model.fit(
    X_train, y_train,
    categorical_feature=CATEGORICAL,
    eval_set=[(X_test, y_test)],
    callbacks=[lgb.early_stopping(30, verbose=False)],
)

pred_proba = model.predict_proba(X_test)[:, 1]
auc = roc_auc_score(y_test, pred_proba)
brier = brier_score_loss(y_test, pred_proba)

print(f"테스트셋 AUC: {auc:.3f}")
print(f"테스트셋 Brier score: {brier:.3f} (낮을수록 확률보정이 잘 됨)")
print()

importance = pd.Series(model.feature_importances_, index=FEATURES).sort_values(ascending=False)
print("피처 중요도:")
print(importance.to_string())

model.booster_.save_model("delay_risk_lgbm.txt")
print("\n모델 저장: delay_risk_lgbm.txt")

# 예시 예측 5건
sample = X_test.iloc[:5].copy()
sample["예측_지연위험도(%)"] = (pred_proba[:5] * 100).round(1)
sample["실제_운휴실현"] = y_test.iloc[:5].values
print("\n예시 예측:")
print(sample.to_string())
