# -*- coding: utf-8 -*-
"""철도(화물역 간) 구간 요금/시간 계산 — 실제 시각표 우선, 없으면 추정."""

import math
from datetime import datetime

from rail_freight_nodes import (
    FREIGHT_NODES,
    AVG_FREIGHT_SPEED_KMH,
    RAIL_TON_KM_RATE_WON,
    TERMINAL_HANDLING_MIN,
    MIN_BILLING_TON,
    RAIL_HANDLING_FEE_WON,
    FreightNode,
)
from rail_schedule import find_next_departure


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_freight_node(lat: float, lng: float) -> tuple[FreightNode, float]:
    """화주 위치에서 가장 가까운 화물역과 거리(km) 반환."""
    best, best_dist = None, float("inf")
    for node in FREIGHT_NODES:
        d = _haversine_km(lat, lng, node.lat, node.lng)
        if d < best_dist:
            best, best_dist = node, d
    return best, best_dist


def estimate_rail_leg(
    origin_node: FreightNode,
    dest_node: FreightNode,
    weight_ton: float,
    departure_after: datetime,
) -> dict:
    """화물역 간 구간 소요시간/운임 + 전철화 여부 판정.

    소요시간은 rail_schedule.py의 실제 시각표에서 먼저 찾고, 매칭되는
    열차가 없으면(예: 순천-포항처럼 직행 노선이 없는 조합) 거리/평균속도
    기반 추정치로 폴백한다. 'schedule_source' 필드로 어느 쪽인지 구분.

    운임은 시각표 데이터에 없는 정보라 항상 추정치 — 거리 × 톤·km단가 ×
    청구중량(최저운임 톤수 적용) + 상하차 취급수수료(양단 2회).
    """
    if origin_node.name == dest_node.name:
        return {
            "distance_km": 0, "duration_min": 0, "fare_won": 0, "electrified": True,
            "schedule_source": "estimated", "departure_dt": None, "arrival_dt": None, "train_no": None,
        }

    dist_km = _haversine_km(origin_node.lat, origin_node.lng, dest_node.lat, dest_node.lng)
    electrified = origin_node.electrified and dest_node.electrified

    schedule = find_next_departure(
        origin_node.schedule_station, dest_node.schedule_station, departure_after
    )

    if schedule is not None:
        duration_min = schedule["duration_min"]
        schedule_source = "real"
        departure_dt = schedule["departure_dt"]
        arrival_dt = schedule["arrival_dt"]
        train_no = schedule["train_no"]
    else:
        duration_min = round((dist_km / AVG_FREIGHT_SPEED_KMH) * 60 + TERMINAL_HANDLING_MIN * 2)
        schedule_source = "estimated"
        departure_dt = None
        arrival_dt = None
        train_no = None

    billing_weight_ton = max(weight_ton, MIN_BILLING_TON)
    fare = dist_km * RAIL_TON_KM_RATE_WON * billing_weight_ton + RAIL_HANDLING_FEE_WON * 2

    return {
        "distance_km": round(dist_km, 1),
        "duration_min": duration_min,
        "fare_won": round(fare, -3),
        "electrified": electrified,
        "schedule_source": schedule_source,
        "departure_dt": departure_dt,
        "arrival_dt": arrival_dt,
        "train_no": train_no,
    }
