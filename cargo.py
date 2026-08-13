# -*- coding: utf-8 -*-
"""
화물 종류(cargo type) 분류 및 그에 따른 요금 할증·수단 제한.

실제 화물 운송 요금표(트럭/용달 업체 다수 조사 결과 공통 관행)에서
위험물·이손품(파손주의)·생물(농산물·수산물) 등에 별도 할증을 적용하고,
KTX특송 등 여객열차 특송 서비스는 위험물·부패성 물품 접수를 거절하는
관행이 있습니다. 이를 반영합니다.

⚠️ FARE_SURCHARGE_MULTIPLIER 수치는 예시 추정치입니다. 실제 화물
운송사·주선업체 요금표의 할증 조항으로 보정이 필요합니다.
"""

from dataclasses import dataclass
from enum import Enum


class CargoCategory(Enum):
    GENERAL = "일반화물"
    REFRIGERATED = "냉장·냉동"
    HAZARDOUS = "위험물"
    FRAGILE_HIGH_VALUE = "파손주의·고가품"
    PERISHABLE = "농산물·생물"


# 요금 할증 배율 — ⚠️ 추정치, TODO: 실제 화물 요금표 할증 조항으로 보정
FARE_SURCHARGE_MULTIPLIER: dict[CargoCategory, float] = {
    CargoCategory.GENERAL: 1.0,
    CargoCategory.REFRIGERATED: 1.3,   # 냉장차량 필요
    CargoCategory.HAZARDOUS: 1.8,      # 위험물 취급 자격·전용차량 필요
    CargoCategory.FRAGILE_HIGH_VALUE: 1.2,
    CargoCategory.PERISHABLE: 1.15,
}

# 실무상 접수 거절/이용 불가 수단 (수단명은 app.py의 "수단" 라벨과 매칭)
# 근거: KTX특송 표준약관 제10조(운송물의 수탁거절) — 위험물·부패성 물품 등 제외
RESTRICTED_MODES: dict[CargoCategory, set[str]] = {
    CargoCategory.HAZARDOUS: {"KTX특송", "퀵서비스"},
}

# 자연어 화물종류 입력 -> 카테고리 분류용 키워드
# ⚠️ 단순 키워드 매칭이라 오분류 가능성이 있습니다. 실제 서비스라면
# 화주가 드롭다운에서 직접 카테고리를 선택하게 하는 편이 안전합니다.
_KEYWORD_MAP: dict[CargoCategory, list[str]] = {
    CargoCategory.HAZARDOUS: [
        "위험물", "화학물질", "가스", "인화성", "폭발물",
        "배터리", "리튬", "황산", "질산", "염산", "부식성",
    ],
    CargoCategory.REFRIGERATED: ["냉동", "냉장", "신선식품", "아이스"],
    CargoCategory.PERISHABLE: ["농산물", "과일", "채소", "생물", "수산물", "화훼"],
    CargoCategory.FRAGILE_HIGH_VALUE: ["파손", "유리", "고가", "정밀", "전자부품", "귀중품"],
}

# 위험물 중에서도 액체·기체류만 별도 표시 — 탱크차는 액체/기체 저장 구조라
# 고체 위험물(폭발물·리튬배터리 등)엔 부적합합니다. "위험물이면 탱크차"로
# 뭉뚱그리면 이 구분이 사라져서 별도 키워드 세트로 분리합니다.
# ⚠️ 이것도 키워드 매칭이라 오분류 가능성이 있고, 폭발성 가스처럼 액체·기체이면서
# 다른 위험 특성이 겹치는 경우까지 정밀하게 다루지는 못합니다.
_LIQUID_OR_GAS_HAZMAT_KEYWORDS = [
    "가스", "인화성", "황산", "질산", "염산", "부식성", "액체", "유류", "용액",
]


def is_liquid_or_gas_hazmat(text: str) -> bool:
    """위험물 화물종류 텍스트가 액체·기체류로 보이는지 판정 (탱크차 적합 여부용)."""
    return any(kw in text for kw in _LIQUID_OR_GAS_HAZMAT_KEYWORDS)


def classify_cargo_type(text: str) -> CargoCategory:
    """화물 종류 자연어 입력을 키워드 매칭으로 분류."""
    for category, keywords in _KEYWORD_MAP.items():
        if any(kw in text for kw in keywords):
            return category
    return CargoCategory.GENERAL


def apply_surcharge(base_fare: int, category: CargoCategory) -> int:
    return round(base_fare * FARE_SURCHARGE_MULTIPLIER[category], -3)


def is_mode_restricted(category: CargoCategory, mode_label: str) -> bool:
    return mode_label in RESTRICTED_MODES.get(category, set())


# ── 화물 규격(길이/폭/높이) 추정 — 화주 폼에서 치수를 직접 안 받기 때문에,
#    중량과 화물종류(카테고리)만으로 화차배치 화면에 쓸 규격을 근사한다.
#
# 방식: 중량 ÷ 카테고리별 평균 밀도로 부피를 구하고, 직육면체 종횡비를
# 고정(길이:폭:높이 ≈ 1.5:1:0.9 — 일반적인 화물 상자/크레이트 비율)해서
# 세 변을 한꺼번에 역산한다. 이 앱이 다루는 화물은 500kg~수십 톤 범위라
# 파렛트 1개짜리 footprint를 고정하면(예: 120×100cm) 무거운 화물에서
# 높이가 비현실적으로 계속 치솟는 문제가 있어, 세 변이 함께 커지는 방식을
# 쓴다. ⚠️ 전부 추정치입니다. 실제 화물 규격 데이터로 교체가 필요합니다.
CARGO_DENSITY_KG_PER_M3: dict[CargoCategory, float] = {
    CargoCategory.GENERAL: 200.0,
    CargoCategory.REFRIGERATED: 150.0,       # 보냉 포장재로 부피 대비 가벼움
    CargoCategory.HAZARDOUS: 400.0,          # 드럼통 등 밀도 높은 포장
    CargoCategory.FRAGILE_HIGH_VALUE: 100.0,  # 완충재 때문에 부피 대비 가장 가벼움
    CargoCategory.PERISHABLE: 250.0,         # 크레이트(상자) 포장
}

# 길이:폭:높이 종횡비 (일반적인 화물 상자/크레이트 비율 근사)
_DIM_RATIO = (1.5, 1.0, 0.9)
_MIN_DIM_CM, _MAX_DIM_CM = 40.0, 1200.0  # 화차 규격을 크게 벗어나지 않도록 하한/상한


def estimate_dims_cm(weight_kg: float, category: CargoCategory) -> tuple[float, float, float]:
    """중량+화물종류만으로 (길이, 폭, 높이) cm 추정 — 종횡비 고정, 부피 기반 역산."""
    density = CARGO_DENSITY_KG_PER_M3[category]
    volume_m3 = max(weight_kg, 1.0) / density
    rl, rw, rh = _DIM_RATIO
    unit_cm = (volume_m3 * 1_000_000 / (rl * rw * rh)) ** (1 / 3)
    dims = tuple(min(max(unit_cm * r, _MIN_DIM_CM), _MAX_DIM_CM) for r in _DIM_RATIO)
    return tuple(round(d, 1) for d in dims)
