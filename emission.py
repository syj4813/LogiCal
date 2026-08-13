# -*- coding: utf-8 -*-
"""
환경영향(GWP, PM) 계산.

배출계수 출처: 사용자 제공 (톤·km 기준)
  - 3.5-7.5t lorry (트럭)
  - Diesel 화물열차
  - Electricity 화물열차

⚠️ KTX특송은 이 계수로 계산하지 않습니다.
   화물열차 계수는 "전용 화물열차 운행"을 전제로 한 톤·km당 배출량이며,
   KTX특송처럼 이미 운행 중인 여객열차의 여유 공간을 활용하는 경우는
   한계배출량(marginal) 또는 배분(allocation) 방법론이 필요해
   전혀 다른 계산 체계입니다. 별도 방법론 없이 이 계수를 곱하면
   과대 계상됩니다. → 현재는 배출량 비교에서 KTX특송/퀵서비스 제외.
"""

from dataclasses import dataclass
from enum import Enum


class TransportMode(Enum):
    TRUCK_LORRY_3_5_7_5T = "3.5-7.5t lorry"
    RAIL_FREIGHT_DIESEL = "Diesel 화물열차"
    RAIL_FREIGHT_ELECTRIC = "Electricity 화물열차"


@dataclass(frozen=True)
class EmissionFactor:
    gwp_kg_per_tkm: float   # kg CO2eq / ton-km
    pm_kg_per_tkm: float    # kg PM / ton-km


EMISSION_FACTORS: dict[TransportMode, EmissionFactor] = {
    TransportMode.TRUCK_LORRY_3_5_7_5T: EmissionFactor(0.87239, 0.0008),
    TransportMode.RAIL_FREIGHT_DIESEL: EmissionFactor(0.0523, 6.90e-05),
    TransportMode.RAIL_FREIGHT_ELECTRIC: EmissionFactor(0.02324, 9.05e-06),
}


def calculate_emission(mode: TransportMode, distance_km: float, weight_ton: float) -> dict:
    """구간 거리(km) x 화물 중량(톤) 기준 GWP/PM 배출량 계산."""
    factor = EMISSION_FACTORS[mode]
    tkm = distance_km * weight_ton
    return {
        "gwp_kg_co2e": round(tkm * factor.gwp_kg_per_tkm, 3),
        "pm_kg": round(tkm * factor.pm_kg_per_tkm, 6),
    }


def calculate_truck_vs_rail_savings(distance_km: float, weight_ton: float) -> dict:
    """트럭 대비 철도(디젤/전철) 배출 절감량 비교."""
    truck = calculate_emission(TransportMode.TRUCK_LORRY_3_5_7_5T, distance_km, weight_ton)
    rail_diesel = calculate_emission(TransportMode.RAIL_FREIGHT_DIESEL, distance_km, weight_ton)
    rail_electric = calculate_emission(TransportMode.RAIL_FREIGHT_ELECTRIC, distance_km, weight_ton)

    def savings(rail):
        gwp_pct = (1 - rail["gwp_kg_co2e"] / truck["gwp_kg_co2e"]) * 100 if truck["gwp_kg_co2e"] else 0
        pm_pct = (1 - rail["pm_kg"] / truck["pm_kg"]) * 100 if truck["pm_kg"] else 0
        return {"gwp_savings_pct": round(gwp_pct, 1), "pm_savings_pct": round(pm_pct, 1)}

    return {
        "truck": truck,
        "rail_diesel": rail_diesel,
        "rail_electric": rail_electric,
        "diesel_savings": savings(rail_diesel),
        "electric_savings": savings(rail_electric),
    }


# 탄소 마일리지 전환 계수 (kg CO2eq 절감당 마일리지 포인트) — ⚠️ 임의 설정치,
# 대회 시연용. 실제 서비스라면 코레일 마일리지 제도나 탄소포인트제 등 기존
# 제도와 연동해 전환 비율을 재산정해야 함.
CARBON_MILEAGE_PER_KG_CO2 = 10


def calculate_carbon_mileage(gwp_savings_kg: float) -> int:
    """탄소 절감량(kgCO2eq)을 화주에게 보여줄 마일리지 포인트로 변환."""
    return max(0, round(gwp_savings_kg * CARBON_MILEAGE_PER_KG_CO2))


# 나무 1그루의 연간 CO2 흡수량 — ⚠️ 통상 인용되는 근사치(약 21kg/년)이며
# 수종·수령에 따라 편차가 큼. 체감형 비유 표시용으로만 사용, 정밀한
# 환경 지표로 인용하지 말 것.
TREE_CO2_ABSORPTION_KG_PER_YEAR = 21.0


def calculate_tree_equivalent(gwp_savings_kg: float) -> float:
    """절감된 CO2가 나무 몇 그루의 연간 흡수량과 비슷한지 (근사 비유)."""
    return round(gwp_savings_kg / TREE_CO2_ABSORPTION_KG_PER_YEAR, 1)
