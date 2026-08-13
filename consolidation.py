# -*- coding: utf-8 -*-
"""
소량 화물 통합(consolidation) — 규칙 기반 그룹핑.

방식: 같은 출발 화물역-도착 화물역 쌍 + 희망일 ±2일 이내인 화주끼리
      묶어서 몇 건이 결합됐는지 보여준다. 다만 철도 이용 가능 여부
      자체는 결합 여부와 무관하게 500kg 이상이면 통과한다 — 실제
      풀에 결합 상대가 있는지와 상관없이, 소량 화물 기준(500kg)만
      넘으면 철도 통합운송을 검토할 수 있게 하기 위함.

DBSCAN 등 밀도 기반 군집화 대신 이 방식을 쓰는 이유:
  - 표본이 적은 데모 환경에서 결과 재현성이 높고, 판정 근거를
    한 줄로 설명 가능 (심사 질의응답에서 방어 가능)
  - 물류 현실상 화주가 신경 쓰는 건 좌표 간 기하학적 거리가 아니라
    "어느 화물역을 쓸 수 있는가"이므로 규칙 기반이 구조에 더 부합
"""

from dataclasses import dataclass
from datetime import date, timedelta

from rail_freight_nodes import MIN_SHIPMENT_TON_FOR_RAIL
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
    """새 주문이 철도 이용 가능한지 판정.

    ⚠️ 철도 이용 가능 여부는 500kg 이상이기만 하면 통과한다 — 풀 안에
    결합 상대가 있는지, 합산 중량이 얼마인지는 이제 판정에 영향을 주지
    않는다(예전엔 최소 결합 기준 톤수 미달이면 거절했었음). 결합 상대
    탐색 자체는 여전히 하는데, 이건 순전히 "몇 건과 같이 묶였는지"를
    화주에게 보여주고 화차배치 등 후단에 넘기기 위한 정보용이다.
    """
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

    # 같은 화물역 쌍 + 희망일 인접 화주들과 그룹핑 (정보용 — 판정에는 미반영)
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

    if len(grouped) > 1:
        reason = (
            f"유사 조건 화주 {len(grouped) - 1}건과 결합해 철도 이용 가능 "
            f"(합산 {total_weight:.1f}톤)"
        )
    else:
        reason = "철도 이용 가능"

    return ConsolidationResult(
        True,
        reason,
        origin_node.name,
        dest_node.name,
        [o.order_id for o in grouped],
        total_weight,
    )
