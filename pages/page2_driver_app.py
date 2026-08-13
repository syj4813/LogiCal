# -*- coding: utf-8 -*-
"""트럭기사 대시보드.

shared_store에 저장된 화주 예약을 읽어 기사별 도착 예정 화물과
복귀 화물 후보를 한 화면에서 보여줍니다.

기사 7명은 화물역 7곳에 한 명씩 고정 배치돼 있다(DRIVER_CURRENT_STATION).
화면에는 GPS 기준 현재 위치로 표시한다.
"""

from datetime import datetime

import streamlit as st

import gemini_assist
import shared_store
from gemini_assist import explain_match
from rail_freight_nodes import MIN_CONSOLIDATION_TON
from road_cost import estimate_drayage_fare
from tz_utils import now_kst_naive
from utils.data import STATIONS

try:
    gemini_assist.GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    gemini_assist.GEMINI_API_KEY = ""

_DRIVER_STATION_MAP = {
    "김철수": "오봉역",
    "박영달": "의왕역",
    "이만호": "부산항역(신항)",
    "최광수": "부산진역",
    "정태식": "천안역",
    "강병준": "순천역",
    "윤재홍": "포항역",
}
DRIVER_CURRENT_STATION = {
    name: station for name, station in _DRIVER_STATION_MAP.items() if station in STATIONS
}
_DRIVER_NAMES = list(DRIVER_CURRENT_STATION.keys())

st.markdown(
    """
    <style>
    .fx-hero {
        padding: 22px 24px;
        border-radius: 16px;
        background: linear-gradient(135deg, #0F6E4F 0%, #14895F 100%);
        color: white;
        margin-bottom: 18px;
    }
    .fx-hero h2 { margin: 0; color: white; }
    .fx-hero p { margin: 6px 0 0 0; opacity: .9; }
    .fx-brand {
        text-align: center; margin-bottom: 8px;
        font-size: 1.05rem; font-weight: 800; color: #0F6E4F; letter-spacing: 0.5px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="fx-brand">🚆 RailLTL</div>', unsafe_allow_html=True)
st.markdown(
    """
    <div class="fx-hero">
      <h2>🚛 화물 매칭</h2>
      <p>오늘의 배차 · 도착 예정 화물 · 복귀 화물 매칭 · 예상 수익</p>
    </div>
    """,
    unsafe_allow_html=True,
)

head1, head2 = st.columns([4, 1])
with head1:
    driver = st.selectbox("기사 선택", _DRIVER_NAMES, label_visibility="collapsed")
with head2:
    if st.button("🔄 새로고침", width="stretch"):
        st.rerun()

my_station = DRIVER_CURRENT_STATION[driver]
shipments = shared_store.read_shipments()
now = now_kst_naive()

# 기사 상단 요약
arriving = sorted(
    [
        s for s in shipments
        if s.get("도착화물역") == my_station and s.get("도착예정시각")
    ],
    key=lambda s: s["도착예정시각"],
)
return_candidates = [s for s in shipments if s.get("출발화물역") == my_station]

next_eta = None
for s in arriving:
    if s["도착예정시각"] >= now:
        next_eta = s["도착예정시각"]
        break

k1, k2, k3, k4 = st.columns(4)
k1.metric("📍 GPS 기준 현재 위치", my_station)
k2.metric("도착 예정 화물", f"{len(arriving)}건")
k3.metric("복귀 화물 후보", f"{len(return_candidates)}건")
k4.metric("다음 도착", next_eta.strftime("%H:%M") if next_eta else "-")

st.divider()

if not shipments:
    st.info("현재 예약된 화물이 없습니다. 화주가 철도 통합운송 예약을 확정하면 이 화면에 반영됩니다.")
    st.stop()

# ── 도착 예정 화물 ──────────────────────────────────────────
st.subheader(f"📦 {my_station} 도착 예정 화물")

if not arriving:
    st.info("현재 이 화물역으로 도착 예정인 화물이 없습니다.")
else:
    for s in arriving:
        eta = s["도착예정시각"]
        remaining_min = int((eta - now).total_seconds() // 60)
        stage_idx = shared_store.current_stage_idx(s)
        stage_label = shared_store.STAGE_LABELS[stage_idx]

        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2.2, 1.1, 1.1, 1.3])
            with c1:
                st.markdown(f"**{s.get('화물ID', '-')} · {s.get('화물종류', '-')}**")
                st.caption(f"{s.get('출발화물역', '-')} → {s.get('도착화물역', '-')} · {s.get('중량톤', '-')}톤")
            with c2:
                st.metric("도착 예정", eta.strftime("%H:%M"))
            with c3:
                if remaining_min >= 0:
                    st.metric("남은 시간", f"{remaining_min}분")
                else:
                    st.metric("예정시각 경과", f"{abs(remaining_min)}분")
            with c4:
                st.markdown(f"**현재 단계**  \n{stage_label}")
                if s.get("열차번호"):
                    st.caption(f"열차 {s['열차번호']}")

st.divider()

# ── 복귀 화물 매칭 ──────────────────────────────────────────
st.subheader("♻️ 복귀 화물 추천")
st.caption("현재 화물역에서 출발하는 예약 중 공차 복귀를 줄일 수 있는 후보를 적합도 순으로 보여줍니다.")

if not return_candidates:
    st.info(f"{my_station}에서 출발 예정인 복귀 화물이 없습니다.")
    st.stop()


def _match_score(s: dict) -> float:
    weight = float(s.get("중량톤") or 0)
    grouped = bool(s.get("결합화주ID목록"))
    weight_component = (
        min(40.0, (weight / MIN_CONSOLIDATION_TON) * 40.0)
        if MIN_CONSOLIDATION_TON else 0.0
    )
    grouped_component = 10.0 if grouped else 0.0
    return round(50.0 + weight_component + grouped_component, 1)


def _expected_revenue(s: dict):
    distance = s.get("막판마일거리km")
    weight = s.get("중량톤")
    if distance is None or weight is None:
        return None
    return estimate_drayage_fare(float(distance), float(weight))


@st.cache_data(show_spinner=False)
def _cached_explain_match(score: float, shipment_id: str, factors: dict) -> str:
    # shipment_id는 캐시를 화물별로 나누기 위한 키입니다.
    return explain_match(score, factors)


scored = []
for s in return_candidates:
    scored.append((s, _match_score(s), _expected_revenue(s)))
scored.sort(key=lambda x: x[1], reverse=True)

for rank, (s, score, revenue) in enumerate(scored, start=1):
    with st.container(border=True):
        c0, c1, c2, c3 = st.columns([0.55, 2.35, 1.05, 1.15])
        with c0:
            st.markdown(f"### {rank}위")
        with c1:
            st.markdown(f"**{s.get('화물ID', '-')} · {s.get('화물종류', '-')}**")
            st.caption(
                f"{s.get('출발화물역', '-')} → {s.get('도착화물역', '-')} · "
                f"목적지 {s.get('도착지주소', '-')}"
            )
            st.caption(f"중량 {s.get('중량톤', '-')}톤")
        with c2:
            st.metric("매칭 적합도", f"{score:.1f}점")
            st.caption("공차 방지 적합" if score >= 75 else "검토 필요")
        with c3:
            st.metric("예상 수익", f"{revenue:,}원" if revenue is not None else "-")

        factors = {
            "중량톤": s.get("중량톤"),
            "결합배송여부": bool(s.get("결합화주ID목록")),
            "결합최소기준톤": MIN_CONSOLIDATION_TON,
        }
        if gemini_assist.GEMINI_API_KEY:
            try:
                narrative = _cached_explain_match(score, s.get("화물ID", "-"), factors)
                st.info(f"🤖 {narrative}")
            except Exception:
                st.caption("AI 설명을 불러오지 못했습니다. 위 적합도와 운임 정보를 참고해 주세요.")# -*- coding: utf-8 -*-
"""트럭기사 대시보드.

shared_store에 저장된 화주 예약을 읽어 기사별 도착 예정 화물과
복귀 화물 후보를 한 화면에서 보여줍니다.

기사 7명은 화물역 7곳에 한 명씩 고정 배치돼 있다(DRIVER_CURRENT_STATION).
화면에는 GPS 기준 현재 위치로 표시한다.
"""

from datetime import datetime

import streamlit as st

import gemini_assist
import shared_store
from gemini_assist import explain_match
from rail_freight_nodes import MIN_CONSOLIDATION_TON
from road_cost import estimate_drayage_fare
from tz_utils import now_kst_naive
from utils.data import STATIONS

try:
    gemini_assist.GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    gemini_assist.GEMINI_API_KEY = ""

_DRIVER_STATION_MAP = {
    "김철수": "오봉역",
    "박영달": "의왕역",
    "이만호": "부산항역(신항)",
    "최광수": "부산진역",
    "정태식": "천안역",
    "강병준": "순천역",
    "윤재홍": "포항역",
}
DRIVER_CURRENT_STATION = {
    name: station for name, station in _DRIVER_STATION_MAP.items() if station in STATIONS
}
_DRIVER_NAMES = list(DRIVER_CURRENT_STATION.keys())

st.markdown(
    """
    <style>
    .fx-hero {
        padding: 22px 24px;
        border-radius: 16px;
        background: linear-gradient(135deg, #0F6E4F 0%, #14895F 100%);
        color: white;
        margin-bottom: 18px;
    }
    .fx-hero h2 { margin: 0; color: white; }
    .fx-hero p { margin: 6px 0 0 0; opacity: .9; }
    .fx-brand {
        text-align: center; margin-bottom: 8px;
        font-size: 1.05rem; font-weight: 800; color: #0F6E4F; letter-spacing: 0.5px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="fx-brand">🚆 RailLTL</div>', unsafe_allow_html=True)
st.markdown(
    """
    <div class="fx-hero">
      <h2>🚛 화물 매칭</h2>
      <p>오늘의 배차 · 도착 예정 화물 · 복귀 화물 매칭 · 예상 수익</p>
    </div>
    """,
    unsafe_allow_html=True,
)

head1, head2 = st.columns([4, 1])
with head1:
    driver = st.selectbox("기사 선택", _DRIVER_NAMES, label_visibility="collapsed")
with head2:
    if st.button("🔄 새로고침", width="stretch"):
        st.rerun()

my_station = DRIVER_CURRENT_STATION[driver]
shipments = shared_store.read_shipments()
now = now_kst_naive()

# 기사 상단 요약
arriving = sorted(
    [
        s for s in shipments
        if s.get("도착화물역") == my_station and s.get("도착예정시각")
    ],
    key=lambda s: s["도착예정시각"],
)
return_candidates = [s for s in shipments if s.get("출발화물역") == my_station]

next_eta = None
for s in arriving:
    if s["도착예정시각"] >= now:
        next_eta = s["도착예정시각"]
        break

k1, k2, k3, k4 = st.columns(4)
k1.metric("📍 GPS 기준 현재 위치", my_station)
k2.metric("도착 예정 화물", f"{len(arriving)}건")
k3.metric("복귀 화물 후보", f"{len(return_candidates)}건")
k4.metric("다음 도착", next_eta.strftime("%H:%M") if next_eta else "-")

st.divider()

if not shipments:
    st.info("현재 예약된 화물이 없습니다. 화주가 철도 통합운송 예약을 확정하면 이 화면에 반영됩니다.")
    st.stop()

# ── 도착 예정 화물 ──────────────────────────────────────────
st.subheader(f"📦 {my_station} 도착 예정 화물")

if not arriving:
    st.info("현재 이 화물역으로 도착 예정인 화물이 없습니다.")
else:
    for s in arriving:
        eta = s["도착예정시각"]
        remaining_min = int((eta - now).total_seconds() // 60)
        stage_idx = shared_store.current_stage_idx(s)
        stage_label = shared_store.STAGE_LABELS[stage_idx]

        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2.2, 1.1, 1.1, 1.3])
            with c1:
                st.markdown(f"**{s.get('화물ID', '-')} · {s.get('화물종류', '-')}**")
                st.caption(f"{s.get('출발화물역', '-')} → {s.get('도착화물역', '-')} · {s.get('중량톤', '-')}톤")
            with c2:
                st.metric("도착 예정", eta.strftime("%H:%M"))
            with c3:
                if remaining_min >= 0:
                    st.metric("남은 시간", f"{remaining_min}분")
                else:
                    st.metric("예정시각 경과", f"{abs(remaining_min)}분")
            with c4:
                st.markdown(f"**현재 단계**  \n{stage_label}")
                if s.get("열차번호"):
                    st.caption(f"열차 {s['열차번호']}")

st.divider()

# ── 복귀 화물 매칭 ──────────────────────────────────────────
st.subheader("♻️ 복귀 화물 추천")
st.caption("현재 화물역에서 출발하는 예약 중 공차 복귀를 줄일 수 있는 후보를 적합도 순으로 보여줍니다.")

if not return_candidates:
    st.info(f"{my_station}에서 출발 예정인 복귀 화물이 없습니다.")
    st.stop()


def _match_score(s: dict) -> float:
    weight = float(s.get("중량톤") or 0)
    grouped = bool(s.get("결합화주ID목록"))
    weight_component = (
        min(40.0, (weight / MIN_CONSOLIDATION_TON) * 40.0)
        if MIN_CONSOLIDATION_TON else 0.0
    )
    grouped_component = 10.0 if grouped else 0.0
    return round(50.0 + weight_component + grouped_component, 1)


def _expected_revenue(s: dict):
    distance = s.get("막판마일거리km")
    weight = s.get("중량톤")
    if distance is None or weight is None:
        return None
    return estimate_drayage_fare(float(distance), float(weight))


@st.cache_data(show_spinner=False)
def _cached_explain_match(score: float, shipment_id: str, factors: dict) -> str:
    # shipment_id는 캐시를 화물별로 나누기 위한 키입니다.
    return explain_match(score, factors)


scored = []
for s in return_candidates:
    scored.append((s, _match_score(s), _expected_revenue(s)))
scored.sort(key=lambda x: x[1], reverse=True)

for rank, (s, score, revenue) in enumerate(scored, start=1):
    with st.container(border=True):
        c0, c1, c2, c3 = st.columns([0.55, 2.35, 1.05, 1.15])
        with c0:
            st.markdown(f"### {rank}위")
        with c1:
            st.markdown(f"**{s.get('화물ID', '-')} · {s.get('화물종류', '-')}**")
            st.caption(
                f"{s.get('출발화물역', '-')} → {s.get('도착화물역', '-')} · "
                f"목적지 {s.get('도착지주소', '-')}"
            )
            st.caption(f"중량 {s.get('중량톤', '-')}톤")
        with c2:
            st.metric("매칭 적합도", f"{score:.1f}점")
            st.caption("공차 방지 적합" if score >= 75 else "검토 필요")
        with c3:
            st.metric("예상 수익", f"{revenue:,}원" if revenue is not None else "-")

        factors = {
            "중량톤": s.get("중량톤"),
            "결합배송여부": bool(s.get("결합화주ID목록")),
            "결합최소기준톤": MIN_CONSOLIDATION_TON,
        }
        if gemini_assist.GEMINI_API_KEY:
            try:
                narrative = _cached_explain_match(score, s.get("화물ID", "-"), factors)
                st.info(f"🤖 {narrative}")
            except Exception:
                st.caption("AI 설명을 불러오지 못했습니다. 위 적합도와 운임 정보를 참고해 주세요.")
