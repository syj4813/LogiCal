# -*- coding: utf-8 -*-
"""
철도 통합운송 door-to-door 계산.

첫마일(트럭) 도착 시각을 기준으로 실제 화물열차 시각표에서 다음 열차를
찾고, 그 열차의 실제 도착시각에 막판마일(트럭) 소요시간을 더해 최종
도착예정시각을 계산한다. 실제 시각표에 매칭되는 열차가 없으면
거리/평균속도 기반 추정치로 자동 폴백한다 (rail_cost.estimate_rail_leg).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from rail_cost import nearest_freight_node, estimate_rail_leg
from rail_freight_nodes import TERMINAL_HANDLING_MIN
from road_cost import get_road_distance_duration, estimate_drayage_fare
from emission import calculate_emission, TransportMode


@dataclass
class IntermodalResult:
    departure_dt: datetime
    arrival_dt: datetime
    total_duration_min: int
    total_fare_won: int
    total_gwp_kg_co2e: float
    total_pm_kg: float
    electrified: bool
    schedule_source: str  # 'real' | 'estimated'
    train_no: str | None
    origin_node_name: str
    dest_node_name: str
    origin_node_lat: float
    origin_node_lng: float
    dest_node_lat: float
    dest_node_lng: float
    first_mile_km: float
    rail_km: float
    last_mile_km: float
    first_mile_path: list[tuple[float, float]]
    last_mile_path: list[tuple[float, float]]
    # ── 단계(stage) 판정용 중간 타임스탬프 ──
    # schedule_source == 'real'일 때만 rail_departure_dt가 채워진다(실제
    # CSV 시각표 기반). 'estimated'면 None — 이 경우 하류(shared_store)에서는
    # 정밀 단계 판정을 포기하고 예약~도착 경과비율 방식으로 폴백해야 한다.
    station_ready_dt: datetime  # 첫마일 트럭 도착 + 상차처리 완료 시각
    rail_departure_dt: datetime | None  # 실제 열차 출발시각 (real일 때만)
    rail_arrival_dt: datetime  # 열차 도착시각 (real이면 실제, 아니면 추정)
    station_release_dt: datetime  # 하차처리 완료 + 막판마일 트럭 출발 시각


def estimate_intermodal(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    weight_ton: float,
    departure_dt: datetime,
) -> IntermodalResult:
    origin_node, _ = nearest_freight_node(origin_lat, origin_lng)
    dest_node, _ = nearest_freight_node(dest_lat, dest_lng)

    # 첫마일: 화주 출발지 -> 가장 가까운 출발 화물역 (카카오맵 실제 도로 데이터)
    first_mile = get_road_distance_duration(origin_lng, origin_lat, origin_node.lng, origin_node.lat)
    # 화물역 도착 후 상차 처리 시간을 더해 "열차 탑승 가능 시각" 산출
    ready_at_origin_station = (
        departure_dt
        + timedelta(minutes=first_mile["duration_min"])
        + timedelta(minutes=TERMINAL_HANDLING_MIN)
    )

    rail_leg = estimate_rail_leg(origin_node, dest_node, weight_ton, ready_at_origin_station)

    if rail_leg["schedule_source"] == "real":
        rail_arrival_dt = rail_leg["arrival_dt"]
    else:
        rail_arrival_dt = ready_at_origin_station + timedelta(minutes=rail_leg["duration_min"])

    # 도착 화물역 하차 처리 시간을 더한 뒤 막판마일 트럭 출발
    ready_for_last_mile = rail_arrival_dt + timedelta(minutes=TERMINAL_HANDLING_MIN)
    last_mile = get_road_distance_duration(dest_node.lng, dest_node.lat, dest_lng, dest_lat)
    final_arrival_dt = ready_for_last_mile + timedelta(minutes=last_mile["duration_min"])

    first_mile_fare = estimate_drayage_fare(first_mile["distance_km"], weight_ton)
    last_mile_fare = estimate_drayage_fare(last_mile["distance_km"], weight_ton)
    total_fare = first_mile_fare + rail_leg["fare_won"] + last_mile_fare
    total_duration = round((final_arrival_dt - departure_dt).total_seconds() / 60)

    rail_mode = TransportMode.RAIL_FREIGHT_ELECTRIC if rail_leg["electrified"] else TransportMode.RAIL_FREIGHT_DIESEL
    rail_emission = calculate_emission(rail_mode, rail_leg["distance_km"], weight_ton)
    first_mile_emission = calculate_emission(TransportMode.TRUCK_LORRY_3_5_7_5T, first_mile["distance_km"], weight_ton)
    last_mile_emission = calculate_emission(TransportMode.TRUCK_LORRY_3_5_7_5T, last_mile["distance_km"], weight_ton)

    total_gwp = (
        rail_emission["gwp_kg_co2e"]
        + first_mile_emission["gwp_kg_co2e"]
        + last_mile_emission["gwp_kg_co2e"]
    )
    total_pm = (
        rail_emission["pm_kg"] + first_mile_emission["pm_kg"] + last_mile_emission["pm_kg"]
    )

    return IntermodalResult(
        departure_dt=departure_dt,
        arrival_dt=final_arrival_dt,
        total_duration_min=total_duration,
        total_fare_won=round(total_fare, -3),
        total_gwp_kg_co2e=round(total_gwp, 3),
        total_pm_kg=round(total_pm, 6),
        electrified=rail_leg["electrified"],
        schedule_source=rail_leg["schedule_source"],
        train_no=rail_leg["train_no"],
        origin_node_name=origin_node.name,
        dest_node_name=dest_node.name,
        origin_node_lat=origin_node.lat,
        origin_node_lng=origin_node.lng,
        dest_node_lat=dest_node.lat,
        dest_node_lng=dest_node.lng,
        first_mile_km=first_mile["distance_km"],
        rail_km=rail_leg["distance_km"],
        last_mile_km=last_mile["distance_km"],
        first_mile_path=first_mile["path"],
        last_mile_path=last_mile["path"],
        station_ready_dt=ready_at_origin_station,
        rail_departure_dt=rail_leg["departure_dt"],  # real이 아니면 None
        rail_arrival_dt=rail_arrival_dt,
        station_release_dt=ready_for_last_mile,
    )
