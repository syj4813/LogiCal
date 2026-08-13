# -*- coding: utf-8 -*-
"""
화물-화차 배치 추천 (추론 모듈).

학습된 모델(data/car_assignment_model.txt, LightGBM 회귀, 150트리,
테스트 R²=0.970)이 컬럼: cargo_weight_kg, cargo_length/width/height_cm,
hazmat_class, fragile_flag, train_total_cars, car_index, car_type,
car_max_load_kg, car_current_load_kg, car_remaining_capacity_m3,
adjacent_car_hazmat, distance_from_hazmat_car, position_in_car,
weight_fit_ratio, volume_fit_ratio 기준으로 적합도 점수(0~1)를 예측한다.

⚠️ 위험물 안전 규칙은 모델 점수를 신뢰하지 않고 규칙으로 강제한다.
   실제로 이 모델(v2)을 그대로 돌려보면, hazmat_class>0인 화물의 Top5
   추천에 탱크차가 전혀 안 뜨는 경우가 있었다 — 이전 v1 모델에서도
   같은 패턴(학습 라벨 자체가 위험물-탱크차 관계를 제대로 반영 못함)이
   있었던 것과 동일한 문제로 보인다. "액체·기체 위험물은 탱크차만
   허용, 그 외(비위험물+고체위험물)는 탱크차 배제"를 모델 점수 위에
   상호배타 규칙으로 강제 적용해서 이 문제를 우회한다.
"""

from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

MODEL_PATH = Path(__file__).parent / "data" / "car_assignment_model.txt"

CAR_TYPES = ["탱크차", "유개차", "무개차", "평판차", "컨테이너차"]
POSITIONS = ["전부", "중부", "후부"]

# 화물길이/폭/높이 미입력(화주 폼에서 안 받음) 시 폴백 기본 규격 — ⚠️ 추정치.
DEFAULT_CARGO_DIMS_CM = (100.0, 60.0, 50.0)

_MODEL: lgb.Booster | None = None


def _load_model() -> lgb.Booster:
    global _MODEL
    if _MODEL is None:
        _MODEL = lgb.Booster(model_file=str(MODEL_PATH))
    return _MODEL


@dataclass
class WagonCar:
    car_no: str
    car_type: str
    car_index: int
    train_total_cars: int
    car_max_load_kg: float
    car_current_load_kg: float
    car_remaining_capacity_m3: float
    adjacent_car_hazmat: int   # 0/1 — 바로 옆 화차가 위험물차인지
    distance_from_hazmat_car: int
    position_in_car: str       # 전부/중부/후부

    @property
    def car_remaining_load_kg(self) -> float:
        return round(self.car_max_load_kg - self.car_current_load_kg, 1)


def generate_mock_train_composition(train_no: str, n_wagons: int = 25) -> list[WagonCar]:
    """열차번호를 시드로 쓰는 결정론적 mock 편성 생성.

    ⚠️ 실제 화차 편성(적재현황) 데이터가 없어 mock — 열차번호가 같으면
    항상 같은 편성이 나오도록 해시를 시드로 고정한다(재현성 확보).
    편성마다 탱크차는 정확히 1량만 배치한다(현실적으로 위험물 전용
    화차 비중이 낮은 것을 반영).
    """
    seed = abs(hash(train_no)) % (2**32)
    rng = np.random.default_rng(seed)

    hazmat_idx = int(rng.integers(0, n_wagons))
    wagons = []
    for i in range(n_wagons):
        car_type = "탱크차" if i == hazmat_idx else rng.choice(CAR_TYPES[1:])
        max_load = float(rng.uniform(20000, 45000))
        current_load = float(rng.uniform(0, 0.75) * max_load)
        remaining_vol = float(rng.uniform(5, 90))
        dist = abs(i - hazmat_idx)
        pos = POSITIONS[0] if i < n_wagons * 0.15 else POSITIONS[2] if i > n_wagons * 0.85 else POSITIONS[1]
        wagons.append(WagonCar(
            car_no=f"{train_no}-{i+1:02d}",
            car_type=car_type,
            car_index=i + 1,
            train_total_cars=n_wagons,
            car_max_load_kg=round(max_load, 1),
            car_current_load_kg=round(current_load, 1),
            car_remaining_capacity_m3=round(remaining_vol, 2),
            adjacent_car_hazmat=int(dist == 1),
            distance_from_hazmat_car=dist,
            position_in_car=pos,
        ))
    return wagons


def recommend_wagons(
    cargo_weight_kg: float,
    cargo_length_cm: float,
    cargo_width_cm: float,
    cargo_height_cm: float,
    hazmat_class: int,
    fragile_flag: bool,
    wagons: list[WagonCar],
    is_liquid_or_gas_hazmat: bool = False,
    top_n: int = 5,
) -> pd.DataFrame:
    """화차 편성 중 이 화물을 실을 화차 Top N을 적합도순으로 추천.

    hazmat_class > 0인데 is_liquid_or_gas_hazmat이 False면 "고체 위험물"로
    간주 — 탱크차는 액체·기체 저장 구조라 부적합하므로 여전히 배제한다.
    """
    model = _load_model()
    cargo_volume_m3 = cargo_length_cm * cargo_width_cm * cargo_height_cm / 1_000_000

    rows = []
    for w in wagons:
        remaining_load = w.car_remaining_load_kg
        weight_fit_ratio = cargo_weight_kg / max(remaining_load, 1)
        volume_fit_ratio = cargo_volume_m3 / max(w.car_remaining_capacity_m3, 0.01)
        rows.append({
            "cargo_weight_kg": cargo_weight_kg,
            "cargo_length_cm": cargo_length_cm,
            "cargo_width_cm": cargo_width_cm,
            "cargo_height_cm": cargo_height_cm,
            "cargo_volume_m3": cargo_volume_m3,
            "hazmat_class": hazmat_class,
            "fragile_flag": int(fragile_flag),
            "train_total_cars": w.train_total_cars,
            "car_index": w.car_index,
            "car_type": w.car_type,
            "car_max_load_kg": w.car_max_load_kg,
            "car_current_load_kg": w.car_current_load_kg,
            "car_remaining_load_kg": remaining_load,
            "car_remaining_capacity_m3": w.car_remaining_capacity_m3,
            "adjacent_car_hazmat": w.adjacent_car_hazmat,
            "distance_from_hazmat_car": w.distance_from_hazmat_car,
            "position_in_car": w.position_in_car,
            "weight_fit_ratio": weight_fit_ratio,
            "volume_fit_ratio": volume_fit_ratio,
            "_car_no": w.car_no,
            "_overcapacity": remaining_load < cargo_weight_kg,
        })

    X = pd.DataFrame(rows)
    model = _load_model()
    for i, col in enumerate(["car_type", "position_in_car"]):
        X[col] = pd.Categorical(X[col], categories=model.pandas_categorical[i])

    feature_cols = [
        "cargo_weight_kg", "cargo_length_cm", "cargo_width_cm", "cargo_height_cm",
        "cargo_volume_m3", "hazmat_class", "fragile_flag",
        "train_total_cars", "car_index", "car_type",
        "car_max_load_kg", "car_current_load_kg", "car_remaining_load_kg",
        "car_remaining_capacity_m3", "adjacent_car_hazmat", "distance_from_hazmat_car",
        "position_in_car", "weight_fit_ratio", "volume_fit_ratio",
    ]
    scores = model.predict(X[feature_cols])

    result = pd.DataFrame({
        "화차번호": X["_car_no"],
        "화차종류": X["car_type"].astype(str),
        "위치": X["position_in_car"].astype(str),
        "최대적재_kg": X["car_max_load_kg"],
        "현재적재_kg": X["car_current_load_kg"],
        "잔여용적_m3": X["car_remaining_capacity_m3"],
        "위험물차와_거리": X["distance_from_hazmat_car"],
        "적재가능여부": np.where(X["_overcapacity"], "❌ 초과", "✅ 가능"),
        "적합도_점수": scores.round(4),
    })

    # ── 위험물 안전 규칙(모델 점수보다 우선) ──
    if hazmat_class > 0 and is_liquid_or_gas_hazmat:
        result = result[result["화차종류"] == "탱크차"]
    else:
        result = result[result["화차종류"] != "탱크차"]

    return result.sort_values("적합도_점수", ascending=False).head(top_n).reset_index(drop=True)
