# -*- coding: utf-8 -*-
"""화주용 실시간 Door-to-Door 추적.

page0_home.py에서 "철도 통합운송"으로 예약 확정한 화물만 여기 표시된다.
단계(현재단계)는 random이 아니라 shared_store.current_stage_idx()가
실제 시각표(가능한 경우) 또는 예약~도착 경과 비율로 결정론적으로 계산한다.

⚠️ st.set_page_config는 app.py(라우터)에서 한 번만 호출한다 — 서브페이지에서
   다시 호출하면 StreamlitAPIException이 난다.
"""

import streamlit as st

import delay_risk
import map_view
import road_cost
import shared_store
from gemini_assist import explain_delay_risk
from rail_freight_nodes import FREIGHT_NODES
from tz_utils import now_kst_naive

try:
    road_cost.KAKAO_REST_API_KEY = st.secrets.get("KAKAO_REST_API_KEY", "")
except Exception:
    road_cost.KAKAO_REST_API_KEY = ""

st.title("📦 화주용 실시간추적")
st.caption("예약 확정된 화물의 door-to-door 진행 상황을 확인합니다.")

if st.button("🔄 새로고침"):
    st.rerun()

shipments = shared_store.read_shipments()

if not shipments:
    st.info(
        "아직 예약된 화물이 없습니다. '화주 견적 비교' 페이지에서 견적 비교 후 "
        "'철도 통합운송'을 예약 확정하면 여기에 표시됩니다."
    )
    st.page_link("pages/page0_home.py", label="견적 비교로 이동 →", icon="🚚")
    st.stop()

id_options = [s["화물ID"] for s in shipments]
default_idx = 0
last_id = st.session_state.get("last_shipment_id")
if last_id in id_options:
    default_idx = id_options.index(last_id)

with st.sidebar:
    st.header("내 화물 목록")
    selected_id = st.radio(
        "조회할 화물을 선택하세요",
        options=id_options,
        index=default_idx,
        format_func=lambda x: f"{x} · {shared_store.get_shipment(x).get('화물종류', '-')}",
    )

record = shared_store.get_shipment(selected_id)
stage_idx = shared_store.current_stage_idx(record)
stage_label = shared_store.STAGE_LABELS[stage_idx]

c1, c2, c3, c4 = st.columns(4)
c1.metric("화물 ID", record["화물ID"])
eta = record.get("도착예정시각")
c2.metric("도착예정(ETA)", eta.strftime("%m/%d %H:%M") if eta else "-")
if eta:
    remaining = int((eta - now_kst_naive()).total_seconds() // 60)
    c3.metric("남은 예상 시간", f"{max(remaining, 0)}분")
else:
    c3.metric("남은 예상 시간", "-")
c4.metric("현재 단계", f"{stage_idx + 1}/{len(shared_store.STAGE_LABELS)}", stage_label)

st.divider()
st.subheader("🤖 AI 지연위험도")
st.caption(
    "⚠️ 실제 개별 열차 취소 이력이 아니라, 실측 요일별 운휴율 통계(2026 화물열차운행계획)를 "
    "근거로 만든 합성 데이터로 학습한 LightGBM 모델의 예측 확률입니다(테스트 AUC 0.631). "
    "이 앱에는 '공차회송여부'(모델의 최상위 중요 변수) 정보가 없어 항상 '아니오'로 간주하므로, "
    "실제보다 낮게 나올 수 있습니다."
)

_NODE_COORDS = {n.name: (n.lat, n.lng) for n in FREIGHT_NODES}
_origin_node_latlng = _NODE_COORDS.get(record.get("출발화물역"))
_dest_node_latlng = _NODE_COORDS.get(record.get("도착화물역"))
_direction = (
    "하" if (_origin_node_latlng and _dest_node_latlng and _origin_node_latlng[0] > _dest_node_latlng[0])
    else "상"
)
t_start = record.get("희망출발시각")


@st.cache_data(show_spinner=False)
def _cached_delay_risk(shipment_id: str, distance_km: float, weight_ton: float,
                        departure_dt, consolidated: bool, direction: str,
                        origin_node: str, dest_node: str) -> dict:
    return delay_risk.predict_delay_risk(
        origin_node_name=origin_node,
        dest_node_name=dest_node,
        distance_km=distance_km,
        weight_ton=weight_ton,
        departure_dt=departure_dt,
        consolidated=consolidated,
        direction=direction,
    )


rail_km = record.get("철도구간거리km")
if rail_km is not None and t_start is not None:
    result = _cached_delay_risk(
        record["화물ID"], rail_km, record.get("중량톤", 0.0), t_start,
        bool(record.get("결합화주ID목록")), _direction,
        record.get("출발화물역", ""), record.get("도착화물역", ""),
    )
    risk_icon = {"낮음": "🟢", "보통": "🟡", "높음": "🔴"}.get(result["level"], "⚪")
    rc1, rc2 = st.columns([1, 2])
    with rc1:
        st.metric("예측 지연위험 확률", f"{result['probability']*100:.1f}%", result["level"])
    with rc2:
        st.markdown("**🤖 AI 설명 — 이렇게 예측된 요인**")
        try:
            # explain_delay_risk 프롬프트가 요일/노선/장마철·동절기(해당
            # 시)/결합배송/공차회송/중량/거리 등 정성 신호를 빠짐없이
            # 한두 문장에 자동으로 녹여서 설명하도록 요청한다 — 사용자가
            # 따로 체크하거나 입력할 건 없다.
            reason = explain_delay_risk(result["probability"], result["level"], result["signals"])
            st.info(f"{risk_icon} {reason}")
        except Exception:
            st.info(f"{risk_icon} **{result['level']}** (Gemini 설명 생성 실패 — API 키 확인 필요, 확률 수치 자체는 로컬 모델 결과입니다)")
else:
    st.caption("지연위험도를 계산할 철도 구간 정보가 부족합니다.")

st.divider()
st.subheader("진행 경로")
st.progress((stage_idx + 1) / len(shared_store.STAGE_LABELS))
for i, label in enumerate(shared_store.STAGE_LABELS):
    marker = "✅" if i < stage_idx else ("🚚" if i == stage_idx else "⬜")
    st.write(f"{marker} {label}")

st.divider()
st.subheader("🗺️ 이동경로")


@st.cache_data(show_spinner=False)
def _cached_road_path(o_lng: float, o_lat: float, d_lng: float, d_lat: float) -> list[tuple[float, float]]:
    return road_cost.get_road_distance_duration(o_lng, o_lat, d_lng, d_lat)["path"]


origin_latlng = (record.get("출발지위도"), record.get("출발지경도"))
dest_latlng = (record.get("도착지위도"), record.get("도착지경도"))
if all(origin_latlng) and all(dest_latlng) and _origin_node_latlng and _dest_node_latlng:
    on_lat, on_lng = _origin_node_latlng
    dn_lat, dn_lng = _dest_node_latlng
    first_mile_path = _cached_road_path(origin_latlng[1], origin_latlng[0], on_lng, on_lat)
    last_mile_path = _cached_road_path(dn_lng, dn_lat, dest_latlng[1], dest_latlng[0])
    deck = map_view.build_route_map(
        origin_lat=origin_latlng[0], origin_lng=origin_latlng[1],
        dest_lat=dest_latlng[0], dest_lng=dest_latlng[1],
        origin_node=(on_lat, on_lng, record.get("출발화물역", "")),
        dest_node=(dn_lat, dn_lng, record.get("도착화물역", "")),
        first_mile_path=first_mile_path,
        last_mile_path=last_mile_path,
        show_truck_line=False,
    )
    st.caption("🟢 첫/막판마일(트럭) · 🔵 철도 구간(역-역 직선 근사)")
    st.pydeck_chart(deck)
else:
    st.caption("이 예약 건은 좌표 정보가 없어 이동경로 지도를 표시할 수 없습니다.")

st.divider()
st.subheader("예약 상세")
d1, d2 = st.columns(2)
with d1:
    st.write(f"**출발지**: {record.get('출발지주소', '-')}")
    st.write(f"**출발화물역**: {record.get('출발화물역', '-')}")
    st.write(f"**중량**: {record.get('중량톤', '-')}톤")
    st.write(f"**화물종류**: {record.get('화물종류', '-')}")
with d2:
    st.write(f"**도착지**: {record.get('도착지주소', '-')}")
    st.write(f"**도착화물역**: {record.get('도착화물역', '-')}")
    fare = record.get("요금원")
    st.write(f"**요금**: {fare:,.0f}원" if isinstance(fare, (int, float)) else f"**요금**: {fare}")
    grouped = record.get("결합화주ID목록") or []
    st.write(f"**결합 배송 여부**: {'묶음 배송 (' + str(len(grouped)) + '건 결합)' if grouped else '단독'}")

gwp_savings = record.get("GWP절감(kgCO2eq대비트럭)")
mileage = record.get("탄소마일리지")
if gwp_savings is not None or mileage is not None:
    m1, m2 = st.columns(2)
    if gwp_savings is not None:
        m1.metric("탄소 절감량 (트럭 대비)", f"{gwp_savings:.1f} kgCO2eq")
    if mileage is not None:
        m2.metric("🪙 이 화물로 적립된 탄소 마일리지", f"{mileage:,}P")

st.caption(
    "※ 진행 단계는 실제 GPS/RFID 트래킹이 아니라, 실제 열차시각표(가능한 경우) 또는 "
    "예약시각~도착예정시각 경과 비율로 추정한 시뮬레이션입니다."
)
