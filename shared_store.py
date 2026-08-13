# -*- coding: utf-8 -*-
"""
화주용 예약 확정(app.py) → 트럭기사 앱 / 관제센터가 참조하는 세션 간 공유 저장소.

st.cache_resource로 반환하는 객체는 세션(브라우저 탭)이 아니라 앱 프로세스
전체에서 싱글턴으로 공유된다. 별도 DB 없이 데모 수준의 "세션 간 데이터 공유"를
구현하기 위한 선택.

⚠️ 한계: Streamlit Cloud에서 앱이 유휴 상태로 슬립하거나 재배포되면 프로세스가
   재시작되면서 인메모리 데이터가 초기화된다 — 영속성이 필요하면 SQLite 파일이나
   외부 DB(Supabase 등)로 교체가 필요하다 (TODO, 데모 스코프에서는 보류).
⚠️ "실시간"은 웹소켓 push가 아니라 화면을 다시 그릴 때(rerun) 최신 상태를
   읽어오는 폴링 방식이다. 각 페이지에 새로고침 버튼을 뒀고, 자동 주기 갱신이
   필요하면 Streamlit 1.37+의 st.fragment(run_every=...)로 교체 가능 (TODO).
"""

import uuid
from datetime import datetime
from threading import Lock

import streamlit as st

from tz_utils import now_kst_naive

# 화주 door-to-door 여정의 8단계 — 예약시각~도착예정시각 사이 경과 비율로
# 결정론적으로 계산한다 (random 사용 안 함).
STAGE_LABELS = [
    "화주 공장 출발",
    "육상 트럭 이동중 (첫마일)",
    "화물역(CY) 도착",
    "철도 상차 대기",
    "철도 운송중",
    "목적지 화물역 도착",
    "육상 트럭 배송중 (막판마일)",
    "최종 목적지 도착",
]


@st.cache_resource
def _get_store():
    return {"shipments": {}, "lock": Lock()}


def add_shipment(**fields) -> str:
    """예약 확정 시 화물 1건을 스토어에 기록하고 화물ID를 반환.

    필수로 기대하는 키(app.py 쪽에서 채워 넣음):
      화물종류, 출발지주소, 도착지주소, 출발화물역, 도착화물역, 중량톤,
      예약시각, 희망출발시각, 도착예정시각, 요금원,
      GWP(kgCO2eq), GWP절감(kgCO2eq대비트럭), 결합화주ID목록, 열차번호, 시각표출처
    """
    store = _get_store()
    shipment_id = fields.pop("화물ID", None) or f"KRL-{uuid.uuid4().hex[:8].upper()}"
    record = {"화물ID": shipment_id, **fields}
    with store["lock"]:
        store["shipments"][shipment_id] = record
    return shipment_id


def assign_car(shipment_id: str, car_index: int) -> bool:
    """화차 배치 추천에서 확정한 배정 결과를 기존 예약 기록에 반영."""
    store = _get_store()
    with store["lock"]:
        record = store["shipments"].get(shipment_id)
        if record is None:
            return False
        record["화차배정"] = car_index
    return True


def read_shipments() -> list[dict]:
    """전체 예약 목록 조회 (최신 등록순)."""
    store = _get_store()
    with store["lock"]:
        rows = list(store["shipments"].values())
    return sorted(rows, key=lambda r: r.get("예약시각") or datetime.min, reverse=True)


def get_shipment(shipment_id: str) -> dict | None:
    store = _get_store()
    with store["lock"]:
        return store["shipments"].get(shipment_id)


def current_stage_idx(record: dict) -> int:
    """화물의 현재 진행 단계(8단계 중 인덱스)를 계산.

    두 가지 방식이 있다.

    1) 시각표출처가 'real'이고 철도출발/도착시각이 있으면 — 공공데이터
       화물열차 시각표(freight_train_schedule.csv)에서 나온 실제 열차
       출발/도착 시각을 그대로 경계값으로 써서 지금이 "철도 운송중"
       구간에 있는지 정확히 판정한다. random이나 선형근사가 아니라
       실제 시각표 데이터에 근거한 판정이다.
    2) 시각표에 매칭되는 열차가 없어(순천-포항처럼 직행 노선 없는 조합)
       추정치로 폴백한 예약이면, 정밀한 열차 출발/도착 경계를 알 수
       없으므로 희망출발시각~도착예정시각 사이 경과 비율로 근사한다.
       이 경우도 여전히 "화물이 정말 그 단계에 있다"는 실측치는 아니라
       시간 흐름을 선형 근사한 시뮬레이션이라는 한계가 있다.
    """
    now = now_kst_naive()
    t_start = record.get("희망출발시각")
    t_eta = record.get("도착예정시각")
    if not t_start or not t_eta:
        return 0

    if now < t_start:
        return 0  # 아직 출발 전 (화주 공장 출발 대기)
    if now >= t_eta:
        return len(STAGE_LABELS) - 1  # 최종 목적지 도착

    rail_dep = record.get("철도출발시각")
    rail_arr = record.get("철도도착시각")
    t1 = record.get("첫마일완료시각")
    t4 = record.get("막판마일시작시각")

    if record.get("시각표출처") == "real" and rail_dep and rail_arr and t1 and t4:
        boundaries = [t_start, t1, rail_dep, rail_arr, t4, t_eta]
        # boundaries[i] <= now < boundaries[i+1] 인 구간에 매핑
        # STAGE_LABELS 인덱스: 1=첫마일이동중, 3=철도상차대기, 4=철도운송중,
        # 6=막판마일배송중 (2:CY도착, 5:목적지도착은 경계 순간에만 존재하는
        # 시점이라 인접 구간에 흡수)
        stage_for_segment = [1, 3, 4, 6]
        for i in range(len(boundaries) - 1):
            if boundaries[i] <= now < boundaries[i + 1]:
                return stage_for_segment[i]
        return 6

    # ── 폴백: 실제 시각표 매칭 실패(추정치) → 경과 비율 선형 근사 ──
    ratio = (now - t_start).total_seconds() / (t_eta - t_start).total_seconds()
    ratio = min(max(ratio, 0.0), 0.999)
    return min(int(ratio * len(STAGE_LABELS)), len(STAGE_LABELS) - 1)
