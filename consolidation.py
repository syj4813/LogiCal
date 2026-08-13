# -*- coding: utf-8 -*-
"""
소량 화물 통합(consolidation) — 규칙 기반 그룹핑.

방식: 같은 출발 화물역-도착 화물역 쌍 + 희망일 ±2일 이내인 화주끼리
      묶어서, 합산 중량이 LCL 최소 결합 기준(MIN_CONSOLIDATION_TON)을
      넘는지 판정한다. 컨테이너를 완전히 채울 필요는 없음 —
      단독으로 컨테이너를 다 채우는 대형 화물(CONTAINER_MAX_TON 이상)은
      풀 결합 없이 바로 단독 발송으로 처리한다.

DBSCAN 등 밀도 기반 군집화 대신 이 방식을 쓰는 이유:
  - 표본이 적은 데모 환경에서 결과 재현성이 높고, 판정 근거를
    한 줄로 설명 가능 (심사 질의응답에서 방어 가능)
  - 물류 현실상 화주가 신경 쓰는 건 좌표 간 기하학적 거리가 아니라
    "어느 화물역을 쓸 수 있는가"이므로 규칙 기반이 구조에 더 부합
"""

from dataclasses import dataclass
from datetime import date, timedelta

from rail_freight_nodes import CONTAINER_MAX_TON, MIN_CONSOLIDATION_TON, MIN_SHIPMENT_TON_FOR_RAIL
from rail_cost import nearest_freight_node


@dataclass
class ShipperOrder:
    order_id: str
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float
    weight_ton: float
    desired_date: date


@dataclass
class ConsolidationResult:
    eligible: bool
    reason: str
    origin_node_name: str = ""
    dest_node_name: str = ""
    grouped_order_ids: list[str] | None = None
    total_weight_ton: float = 0.0


def evaluate_consolidation(
    new_order: ShipperOrder,
    pool: list[ShipperOrder],
    date_window_days: int = 2,
) -> ConsolidationResult:
    """새 주문이 (단독으로 또는 풀과 결합해) 철도 이용 가능한지 판정."""
    if new_order.weight_ton < MIN_SHIPMENT_TON_FOR_RAIL:
        return ConsolidationResult(
            False,
            f"{new_order.weight_ton * 1000:.0f}kg은 소포 단위(최소 {MIN_SHIPMENT_TON_FOR_RAIL * 1000:.0f}kg 미만)"
            f"라 철도 화물 통합 대상이 아닙니다 — 퀵서비스·KTX특송을 이용하세요.",
        )

    origin_node, _ = nearest_freight_node(new_order.origin_lat, new_order.origin_lng)
    dest_node, _ = nearest_freight_node(new_order.dest_lat, new_order.dest_lng)

    if origin_node.name == dest_node.name:
        return ConsolidationResult(False, "출발/도착이 같은 화물역 권역이라 철도 이용 실익이 없습니다.")

    # 1) 단독으로 컨테이너 기준을 채우는 대형 화물인 경우
    if new_order.weight_ton >= CONTAINER_MAX_TON:
        return ConsolidationResult(
            True,
            "단독 화물만으로 철도 이용 가능",
            origin_node.name,
            dest_node.name,
            [new_order.order_id],
            new_order.weight_ton,
        )

    # 2) 같은 화물역 쌍 + 희망일 인접 화주들과 그룹핑
    window_start = new_order.desired_date - timedelta(days=date_window_days)
    window_end = new_order.desired_date + timedelta(days=date_window_days)

    grouped = [new_order]
    for other in pool:
        if other.order_id == new_order.order_id:
            continue
        other_origin, _ = nearest_freight_node(other.origin_lat, other.origin_lng)
        other_dest, _ = nearest_freight_node(other.dest_lat, other.dest_lng)
        if (
            other_origin.name == origin_node.name
            and other_dest.name == dest_node.name
            and window_start <= other.desired_date <= window_end
        ):
            grouped.append(other)

    total_weight = sum(o.weight_ton for o in grouped)

    if total_weight >= MIN_CONSOLIDATION_TON:
        return ConsolidationResult(
            True,
            f"유사 조건 화주 {len(grouped) - 1}건과 결합 시 철도 이용 가능 "
            f"(합산 {total_weight:.1f}톤 — 컨테이너 공유 적재)",
            origin_node.name,
            dest_node.name,
            [o.order_id for o in grouped],
            total_weight,
        )

    return ConsolidationResult(
        False,
        f"현재 풀 내 결합 가능 화주 기준 합산 {total_weight:.1f}톤 "
        f"(최소 결합 기준 {MIN_CONSOLIDATION_TON}톤 미달) — 철도 이용 불가, 트럭/퀵/KTX특송만 비교",
        origin_node.name,
        dest_node.name,
        [o.order_id for o in grouped],
        total_weight,
    )
