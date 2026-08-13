# -*- coding: utf-8 -*-
"""화주 견적 비교 계산기.

취급 범위: 500kg 이상 화물, 트럭 단독운송 vs 트럭+철도 통합운송(door-to-door)
비교만 다룬다 (퀵서비스·KTX특송은 소형화물 전용이라 이 비교에서 제외).
"""

from datetime import datetime, time

import streamlit as st

import geocode
import road_cost
from cargo import classify_cargo_type, apply_surcharge, is_mode_restricted
from consolidation import ShipperOrder, evaluate_consolidation
from data.mock_pool import get_mock_pool
from emission import (
    TransportMode,
    calculate_emission,
    calculate_tree_equivalent,
)
from intermodal import estimate_intermodal
from rail_freight_nodes import MIN_SHIPMENT_TON_FOR_RAIL
from tz_utils import today_kst

# ── 외부 API 키 주입 (레포에 하드코딩하지 않고 Streamlit secrets에서만 읽음) ──
# ⚠️ secrets.toml 파일 자체가 없으면 st.secrets.get()이 조용히 기본값을
#    돌려주지 않고 StreamlitSecretNotFoundError를 던진다 (일반 dict.get과
#    다른 동작) — 키를 아직 등록하지 않은 배포 초기 상태에서 앱이 통째로
#    죽는 걸 막기 위해 try/except로 감싼다.
try:
    road_cost.KAKAO_REST_API_KEY = st.secrets.get("KAKAO_REST_API_KEY", "")
except Exception:
    road_cost.KAKAO_REST_API_KEY = ""
try:
    geocode.GOOGLE_MAPS_API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
except Exception:
    geocode.GOOGLE_MAPS_API_KEY = ""


# ── 스타일: 기본 Streamlit 룩 탈피 ──
st.markdown(
    """
    <style>
    .fx-card {
        background: #FFFFFF;
        border: 1px solid #E3E9E6;
        border-radius: 14px;
        padding: 22px 24px;
        box-shadow: 0 2px 10px rgba(15, 110, 79, 0.06);
        height: 100%;
    }
    .fx-card.fx-best {
        border: 1.5px solid #0F6E4F;
        box-shadow: 0 4px 16px rgba(15, 110, 79, 0.15);
    }
    .fx-badge {
        display: inline-block;
        background: #0F6E4F;
        color: white;
        font-size: 0.75rem;
        padding: 2px 10px;
        border-radius: 999px;
        margin-bottom: 8px;
    }
    .fx-metric-row { display: flex; justify-content: space-between; margin: 6px 0; font-size: 0.95rem; }
    .fx-metric-label { color: #5A6B63; }
    .fx-metric-value { font-weight: 600; }
    .fx-hero {
        background: linear-gradient(135deg, #0F6E4F 0%, #14895F 100%);
        color: white;
        border-radius: 16px;
        padding: 26px 28px;
        margin-bottom: 22px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="fx-hero">
      <div style="font-size:1.5rem; font-weight:700;">🚆 RailLTL</div>
      <div style="opacity:0.9; margin-top:4px;">AI 기반 소량 화물(LTL) 철도 결합 운송 및 통합 관제 플랫폼</div>
    </div>
    """,
    unsafe_allow_html=True,
)


def _safe_geocode(address: str):
    try:
        return geocode.geocode_address(address)
    except Exception:
        return None


# ── 입력 폼 ──
_defaults = {
    "f_origin": "서울특별시 중구 세종대로",
    "f_dest": "부산광역시 동구 중앙대로",
    "f_cargo": "전자부품",
    "f_weight": 800.0,
    "f_date": today_kst(),
    "f_time": time(9, 0),
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

with st.form("quote_form"):
    c1, c2 = st.columns(2)
    with c1:
        origin_addr = st.text_input("출발지 주소", key="f_origin")
        cargo_text = st.text_input("화물 종류 (예: 전자부품, 냉동식품, 위험물 등)", key="f_cargo")
        weight_kg = st.number_input(
            "중량 (kg)", min_value=500.0, max_value=30000.0, step=100.0, key="f_weight",
            help="500kg 미만은 퀵서비스·KTX특송 등 소형화물 전용 수단을 이용해주세요.",
        )
    with c2:
        dest_addr = st.text_input("도착지 주소", key="f_dest")
        desired_date = st.date_input("희망 출발일", key="f_date")
        desired_time = st.time_input("희망 출발시각", key="f_time")

    with st.expander("주소 인식이 안 되면 좌표를 직접 입력하세요 (선택)"):
        oc1, oc2 = st.columns(2)
        with oc1:
            override_origin = st.checkbox("출발지 좌표 직접 입력")
            origin_lat_in = st.number_input("출발지 위도", value=37.5665, format="%.4f", disabled=not override_origin)
            origin_lng_in = st.number_input("출발지 경도", value=126.9780, format="%.4f", disabled=not override_origin)
        with oc2:
            override_dest = st.checkbox("도착지 좌표 직접 입력")
            dest_lat_in = st.number_input("도착지 위도", value=35.1796, format="%.4f", disabled=not override_dest)
            dest_lng_in = st.number_input("도착지 경도", value=129.0756, format="%.4f", disabled=not override_dest)

    submitted = st.form_submit_button("비교하기", type="primary", use_container_width=True)

if submitted:
    st.session_state["show_comparison"] = True

    if override_origin:
        origin_lat, origin_lng = origin_lat_in, origin_lng_in
    else:
        geo = _safe_geocode(origin_addr)
        if geo is None:
            st.error("출발지 주소를 인식하지 못했습니다. 위 '좌표 직접 입력'에서 출발지 좌표를 입력해주세요.")
            st.stop()
        origin_lat, origin_lng = geo

    if override_dest:
        dest_lat, dest_lng = dest_lat_in, dest_lng_in
    else:
        geo = _safe_geocode(dest_addr)
        if geo is None:
            st.error("도착지 주소를 인식하지 못했습니다. 위 '좌표 직접 입력'에서 도착지 좌표를 입력해주세요.")
            st.stop()
        dest_lat, dest_lng = geo

    weight_ton = weight_kg / 1000
    category = classify_cargo_type(cargo_text)
    departure_dt = datetime.combine(desired_date, desired_time)

    st.session_state["result"] = dict(
        origin_lat=origin_lat, origin_lng=origin_lng,
        dest_lat=dest_lat, dest_lng=dest_lng,
        weight_ton=weight_ton, category=category,
        departure_dt=departure_dt, cargo_text=cargo_text,
    )

if st.session_state.get("show_comparison") and "result" in st.session_state:
    r = st.session_state["result"]
    weight_ton = r["weight_ton"]
    category = r["category"]
    departure_dt = r["departure_dt"]

    st.divider()

    if category.value != "일반화물":
        st.info(f"화물 종류가 **{category.value}**로 분류되었습니다 — 해당 카테고리 할증 및 수단 제한이 적용됩니다.")

    # ── 트럭 단독 ──
    direct = road_cost.get_road_distance_duration(r["origin_lng"], r["origin_lat"], r["dest_lng"], r["dest_lat"])
    if direct.get("source") == "estimated":
        st.caption("⚠️ 지도 API 키 미설정 — 거리/시간은 직선거리 기반 추정치입니다.")
    truck_fare = apply_surcharge(
        road_cost.estimate_truck_fare(direct["distance_km"], weight_ton), category
    )
    truck_emission = calculate_emission(TransportMode.TRUCK_LORRY_3_5_7_5T, direct["distance_km"], weight_ton)
    truck_restricted = is_mode_restricted(category, "트럭")  # 현재 제한 대상 없음, 확장 대비

    # ── 철도 통합운송 (결합 판정 통과 시에만) ──
    pool = get_mock_pool()
    new_order = ShipperOrder("NEW", r["origin_lat"], r["origin_lng"], r["dest_lat"], r["dest_lng"], weight_ton, departure_dt.date())
    consolidation = evaluate_consolidation(new_order, pool)

    im = None
    if consolidation.eligible:
        im = estimate_intermodal(
            r["origin_lat"], r["origin_lng"], r["dest_lat"], r["dest_lng"], weight_ton, departure_dt
        )

    rail_available = im is not None and not is_mode_restricted(category, "철도")

    # ── 비교 카드 ──
    cols = st.columns(2)

    with cols[0]:
        st.markdown(
            f"""
            <div class="fx-card">
              <span class="fx-badge" style="background:#5A6B63;">트럭 단독운송</span>
              <div class="fx-metric-row"><span class="fx-metric-label">예상 요금</span>
                <span class="fx-metric-value">{truck_fare:,.0f}원</span></div>
              <div class="fx-metric-row"><span class="fx-metric-label">이동 거리</span>
                <span class="fx-metric-value">{direct['distance_km']:.0f} km</span></div>
              <div class="fx-metric-row"><span class="fx-metric-label">소요 시간</span>
                <span class="fx-metric-value">약 {direct['duration_min']/60:.1f} 시간</span></div>
              <div class="fx-metric-row"><span class="fx-metric-label">CO2 배출량</span>
                <span class="fx-metric-value">{truck_emission['gwp_kg_co2e']:.1f} kg</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[1]:
        if rail_available:
            gwp_savings_pct = (1 - im.total_gwp_kg_co2e / truck_emission["gwp_kg_co2e"]) * 100 if truck_emission["gwp_kg_co2e"] else 0
            tree_eq = calculate_tree_equivalent(truck_emission["gwp_kg_co2e"] - im.total_gwp_kg_co2e)
            fare_diff_pct = (1 - im.total_fare_won / truck_fare) * 100 if truck_fare else 0
            schedule_note = f"실제 열차시각표 반영 (열차번호 {im.train_no})" if im.schedule_source == "real" else "직행 열차 미매칭 → 평균속도 기준 추정"

            st.markdown(
                f"""
                <div class="fx-card fx-best">
                  <span class="fx-badge">🌱 철도 통합운송 (추천)</span>
                  <div class="fx-metric-row"><span class="fx-metric-label">예상 요금</span>
                    <span class="fx-metric-value">{im.total_fare_won:,.0f}원 ({'절감 ' + format(fare_diff_pct, '.0f') + '%' if fare_diff_pct > 0 else '트럭 대비 ' + format(-fare_diff_pct, '.0f') + '% 비쌈'})</span></div>
                  <div class="fx-metric-row"><span class="fx-metric-label">도착 예정</span>
                    <span class="fx-metric-value">{im.arrival_dt.strftime('%m/%d %H:%M')}</span></div>
                  <div class="fx-metric-row"><span class="fx-metric-label">총 소요 시간</span>
                    <span class="fx-metric-value">약 {im.total_duration_min/60:.1f} 시간</span></div>
                  <div class="fx-metric-row"><span class="fx-metric-label">CO2 배출량</span>
                    <span class="fx-metric-value">{im.total_gwp_kg_co2e:.1f} kg (−{gwp_savings_pct:.0f}%)</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption(f"🚉 {im.origin_node_name} → {im.dest_node_name} · {schedule_note}")
            st.success(f"🌳 트럭 대비 절감된 탄소량은 나무 약 **{tree_eq:.1f}그루**가 1년간 흡수하는 양과 비슷합니다.")
        else:
            st.markdown(
                f"""
                <div class="fx-card" style="opacity:0.75;">
                  <span class="fx-badge" style="background:#B0392F;">철도 통합운송 이용 불가</span>
                  <div style="margin-top:10px; color:#5A6B63; font-size:0.9rem;">{consolidation.reason}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ── 비교 차트 ──
    if rail_available:
        st.divider()
        chart_data = {
            "수단": ["트럭 단독", "철도 통합운송"],
            "요금(만원)": [truck_fare / 10000, im.total_fare_won / 10000],
            "CO2(kg)": [truck_emission["gwp_kg_co2e"], im.total_gwp_kg_co2e],
        }
        cc1, cc2 = st.columns(2)
        with cc1:
            st.caption("요금 비교 (만원)")
            st.bar_chart({"요금(만원)": dict(zip(chart_data["수단"], chart_data["요금(만원)"]))})
        with cc2:
            st.caption("CO2 배출량 비교 (kg)")
            st.bar_chart({"CO2(kg)": dict(zip(chart_data["수단"], chart_data["CO2(kg)"]))})
