# -*- coding: utf-8 -*-
"""화물열차 지연(운휴) 위험도 — 학습된 LightGBM 모델 추론.

⚠️ 이 모델은 실제 개별 열차의 취소 이력으로 학습한 게 아니다. 사용자가
   확보한 "2026 화물열차운행계획" 엑셀의 실측 통계(요일별 운휴율, 운휴
   사유 355건 분포, 노선별 평균거리)를 근거로 생성한 합성 데이터
   (data/generate_dataset.py)로 학습했다 — 자세한 생성 로직/실측 근거는
   그 파일과 data/README.md 참고. 코레일로부터 개별 열차 단위 실제
   이력을 받으면 합성 데이터 대신 그걸로 재학습하면 된다.

   그래도 이전 버전(Gemini에게 정성적으로 등급을 "판단"해달라고 요청하던
   방식)보다는 명확한 개선이다 — 최소한 실측 요일별 운휴율 패턴은 학습에
   그대로 반영돼 있고, 재현성이 있다(같은 입력 -> 항상 같은 확률).

성능: 테스트셋 AUC 0.631, Brier 0.157. 피처 중요도 1위는 공차회송여부인데
   ⚠️ 이 앱에는 "공차회송" 개념이 없어(화주 예약 데이터에 그 정보가 없음)
   추론 시 항상 0(공차회송 아님)으로 넣는다 — 즉 실제보다 위험도가
   낮게 나올 수 있다는 한계가 있다. 화면에도 이 한계를 표시한다.

정성적 요인(장마철/동절기/요일/노선 등)은 별도 입력 없이 이미 signals에
전부 담겨 반환되고, gemini_assist.explain_delay_risk()가 이 신호들을
근거로 자연어 설명을 생성한다 — 화주가 따로 체크하거나 입력할 게 없다.
"""

from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import pandas as pd

MODEL_PATH = Path(__file__).parent / "data" / "delay_risk_lgbm.txt"

CATEGORICAL = ["요일", "주운행선", "수송품목", "상하", "출발시간대"]
FEATURES = [
    "요일", "월", "주운행선", "운행거리_km", "수송품목", "화물중량_톤",
    "상하", "출발시간대", "공차회송여부", "결합배송여부", "장마철여부", "동절기여부",
]

_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

_booster: lgb.Booster | None = None


def _get_booster() -> lgb.Booster:
    global _booster
    if _booster is None:
        _booster = lgb.Booster(model_file=str(MODEL_PATH))
    return _booster


# ── 화물역(rail_freight_nodes) -> 학습 데이터의 "주운행선" 근사 매핑 ──
# ⚠️ 실제 코레일 운행계통 전체를 다 아는 게 아니라, 이 앱이 다루는 7개
#    화물역 조합에 대해서만 상식적으로 가장 근접한 간선을 근사 배정한
#    것이다. 학습 데이터에 없는 노선(예: 포항 경유 동해선)은 가장 많이
#    학습된 "경부선"으로 폴백한다 — 방향성 참고용이지 정밀 매핑 아님.
_ROUTE_LINE_OVERRIDES = {
    frozenset({"오봉역", "순천역"}): "호남선",
    frozenset({"천안역", "순천역"}): "호남선",
    frozenset({"부산항역(신항)", "오봉역"}): "부산신항선",
    frozenset({"부산항역(신항)", "부산진역"}): "부산신항선",
    frozenset({"부산항역(신항)", "천안역"}): "부산신항선",
}


def estimate_route_line(origin_node_name: str, dest_node_name: str) -> str:
    key = frozenset({origin_node_name, dest_node_name})
    return _ROUTE_LINE_OVERRIDES.get(key, "경부선")  # 폴백: 학습 데이터 최다 노선


def _departure_slot(dt: datetime) -> str:
    h = dt.hour
    if 0 <= h < 6:
        return "새벽(00-06)"
    if 6 <= h < 12:
        return "오전(06-12)"
    if 12 <= h < 18:
        return "오후(12-18)"
    return "야간(18-24)"


def predict_delay_risk(
    *,
    origin_node_name: str,
    dest_node_name: str,
    distance_km: float,
    weight_ton: float,
    departure_dt: datetime,
    consolidated: bool,
    direction: str = "하",
    empty_reposition: bool = False,
) -> dict:
    """화물 1건의 지연(운휴) 위험 확률과 등급, 사용한 입력 신호를 반환.

    반환: {"probability": 0~1, "level": "낮음|보통|높음", "signals": {...}}
    """
    weekday_ko = _WEEKDAY_KO[departure_dt.weekday()]
    month = departure_dt.month
    line = estimate_route_line(origin_node_name, dest_node_name)

    row = {
        "요일": weekday_ko,
        "월": month,
        "주운행선": line,
        "운행거리_km": float(distance_km),
        "수송품목": "컨테이너",  # ⚠️ 화물종류 자유입력을 학습데이터 7개 품목으로 정밀 매핑하지 않고
                                  # 이 앱 화물 대부분이 소량 컨테이너 공유적재라 컨테이너로 고정
        "화물중량_톤": float(weight_ton),
        "상하": direction,
        "출발시간대": _departure_slot(departure_dt),
        "공차회송여부": int(empty_reposition),
        "결합배송여부": int(consolidated),
        "장마철여부": int(month in (6, 7, 8)),
        "동절기여부": int(month in (12, 1, 2)),
    }

    df = pd.DataFrame([row])
    booster = _get_booster()
    for col in CATEGORICAL:
        df[col] = pd.Categorical(df[col], categories=booster.pandas_categorical[CATEGORICAL.index(col)])

    probability = float(booster.predict(df[FEATURES])[0])

    if probability < 0.15:
        level = "낮음"
    elif probability < 0.25:
        level = "보통"
    else:
        level = "높음"

    return {"probability": probability, "level": level, "signals": row}
