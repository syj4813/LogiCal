# -*- coding: utf-8 -*-
"""화주 견적 비교 계산기.

취급 범위: 500kg 이상 화물, 트럭 단독운송 vs 트럭+철도 통합운송(door-to-door)
비교만 다룬다 (퀵서비스·KTX특송은 소형화물 전용이라 이 비교에서 제외).
"""

from datetime import date, datetime, time

import streamlit as st

import gemini_assist
import geocode
import map_view
import road_cost
import shared_store
from cargo import CargoCategory, classify_cargo_type, apply_surcharge, is_mode_restricted, is_liquid_or_gas_hazmat
from consolidation import ShipperOrder, evaluate_consolidation
from data.mock_pool import get_mock_pool
from emission import (
    TransportMode,
    calculate_carbon_mileage,
    calculate_emission,
    calculate_tree_equivalent,
)
from intermodal import estimate_intermodal
from rail_freight_nodes import MIN_SHIPMENT_TON_FOR_RAIL
from tz_utils import today_kst, now_kst_naive

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
try:
    gemini_assist.GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    gemini_assist.GEMINI_API_KEY = ""


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

# ── AI 자유입력: 한 문장으로 필수 항목을 자동으로 채워줌 ──
# ⚠️ Gemini는 자연어 파싱만 담당한다 — 요금/시간/배출량 계산은 전부
#    아래 결정론적 로직(consolidation/rail_cost/emission 등)이 처리한다.
AUTOFILL_EXAMPLES = [
    "부산에서 서울로 냉동식품 500kg 내일까지 보내야 해요",
    "천안에서 순천으로 전자부품 8톤, 모레 오전 10시 출발이요",
    "포항에서 오봉으로 위험물 2톤 최대한 빨리 보내주세요",
]
st.caption("💬 문장으로 한 번에 입력해보세요 — 예: " + AUTOFILL_EXAMPLES[0])
free_text = st.text_area("자유 입력 (선택)", placeholder=AUTOFILL_EXAMPLES[1], label_visibility="collapsed")
if st.button("✨ AI로 자동 입력"):
    if not free_text.strip():
        st.warning("문장을 먼저 입력해주세요.")
    elif not gemini_assist.GEMINI_API_KEY:
        st.error("Gemini API 키가 설정되지 않아 자동 입력을 쓸 수 없습니다. secrets.toml에 GEMINI_API_KEY를 등록해주세요.")
    else:
        try:
            parsed = gemini_assist.parse_free_text_order(free_text)
        except Exception as e:
            st.error(f"자동 입력 처리 중 오류가 발생했습니다: {e}")
            parsed = None

        if parsed is not None:
            if parsed.get("missing_fields"):
                st.warning(parsed.get("clarification_message") or f"다음 항목을 더 알려주세요: {', '.join(parsed['missing_fields'])}")
            else:
                if parsed.get("origin"):
                    st.session_state["f_origin"] = parsed["origin"]
                if parsed.get("destination"):
                    st.session_state["f_dest"] = parsed["destination"]
                if parsed.get("weight_kg"):
                    st.session_state["f_weight"] = float(parsed["weight_kg"])
                if parsed.get("cargo_type"):
                    st.session_state["f_cargo"] = parsed["cargo_type"]
                if parsed.get("desired_date"):
                    st.session_state["f_date"] = date.fromisoformat(parsed["desired_date"])
                if parsed.get("desired_time"):
                    st.session_state["f_time"] = datetime.strptime(parsed["desired_time"], "%H:%M").time()

                if parsed.get("unset_optional_fields"):
                    st.info(f"문장에 없던 항목({', '.join(parsed['unset_optional_fields'])})은 기본값으로 채웠습니다 — 필요하면 아래에서 직접 수정하세요.")
                st.success("아래 폼에 자동으로 채워 넣었습니다. 확인 후 '비교하기'를 눌러주세요.")
                st.rerun()

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

    submitted = st.form_submit_button("비교하기", type="primary", width="stretch")

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
        origin_addr=origin_addr, dest_addr=dest_addr,
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
            fare_diff_won = truck_fare - im.total_fare_won
            schedule_note = f"실제 열차시각표 반영 (열차번호 {im.train_no})" if im.schedule_source == "real" else "직행 열차 미매칭 → 평균속도 기준 추정"

            st.markdown(
                f"""
                <div class="fx-card fx-best">
                  <span class="fx-badge">🌱 철도 통합운송 (추천)</span>
                  <div class="fx-metric-row"><span class="fx-metric-label">예상 요금</span>
                    <span class="fx-metric-value">{im.total_fare_won:,.0f}원 ({'절감 ' + format(fare_diff_won, ',.0f') + '원' if fare_diff_won > 0 else '트럭 대비 ' + format(-fare_diff_won, ',.0f') + '원 비쌈'})</span></div>
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
            gwp_savings_kg = truck_emission["gwp_kg_co2e"] - im.total_gwp_kg_co2e
            mileage_preview = calculate_carbon_mileage(gwp_savings_kg)
            st.success(f"🌳 트럭 대비 절감된 탄소량은 나무 약 **{tree_eq:.1f}그루**가 1년간 흡수하는 양과 비슷합니다.")
            st.markdown(
                f"""
                <div style="background:linear-gradient(135deg, #FFF7E0 0%, #FFEFC2 100%);
                            border:2px solid #E8A800; border-radius:12px;
                            padding:16px 20px; margin-top:10px;
                            display:flex; align-items:center; justify-content:space-between;">
                  <div style="font-size:1rem; color:#5A4600;">
                    🪙 <b>철도 통합운송으로 예약 확정</b> 시 적립되는 탄소 마일리지
                  </div>
                  <div style="font-size:1.6rem; font-weight:800; color:#C98A00;">
                    +{mileage_preview:,}P
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
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

    # ── CO2 배출량 비교 ──
    if rail_available:
        st.divider()
        st.caption("CO2 배출량 비교 (kg)")
        st.bar_chart({
            "CO2(kg)": {"트럭 단독": truck_emission["gwp_kg_co2e"], "철도 통합운송": im.total_gwp_kg_co2e}
        })

    # ── 이동경로 지도 ──
    st.divider()
    st.subheader("🗺️ 이동경로")
    if rail_available:
        deck = map_view.build_route_map(
            origin_lat=r["origin_lat"], origin_lng=r["origin_lng"],
            dest_lat=r["dest_lat"], dest_lng=r["dest_lng"],
            truck_only_path=direct.get("path"),
            origin_node=(im.origin_node_lat, im.origin_node_lng, im.origin_node_name),
            dest_node=(im.dest_node_lat, im.dest_node_lng, im.dest_node_name),
            first_mile_path=im.first_mile_path,
            last_mile_path=im.last_mile_path,
        )
        st.caption("🟠 트럭 직송 · 🟢 첫/막판마일(트럭) · 🔵 철도 구간(역-역 직선 근사)")
    else:
        deck = map_view.build_route_map(
            origin_lat=r["origin_lat"], origin_lng=r["origin_lng"],
            dest_lat=r["dest_lat"], dest_lng=r["dest_lng"],
            truck_only_path=direct.get("path"),
        )
        st.caption("🟠 트럭 직송 경로")
    st.pydeck_chart(deck)

    # ── 예약 확정 → 공유 저장소 기록 ──────────────────────────
    # 실시간 Door-to-Door 추적·트럭기사 앱·관제센터 연계는 "철도 통합운송"
    # 예약 건에 한정한다. 트럭 단독은 화물역(CY) 환적 구간 자체가 없어
    # 후단 화면들의 데이터 모델과 맞지 않는다.
    #
    # ⚠️ 이 블록은 st.session_state["show_comparison"]로 이미 게이팅된
    # 영역 안에 있다 — "예약 확정" 버튼을 눌러도 화면 전체가 사라지지
    # 않고 비교 결과가 계속 보이는 이유. (예전 버전에서 `if submitted:`를
    # 직접 조건으로 써서, 그 안의 버튼을 누르면 재실행 시 submitted가
    # 다시 False가 돼 블록 전체가 사라지던 버그가 있었음 — session_state
    # 플래그 패턴으로 처음부터 피해감.)
    if rail_available:
        st.divider()
        st.subheader("예약 확정")
        if st.button("✅ 예약 확정 (Door-to-Door 추적 시작)", type="primary"):
            gwp_savings = truck_emission["gwp_kg_co2e"] - im.total_gwp_kg_co2e
            mileage_earned = calculate_carbon_mileage(gwp_savings)
            shipment_id = shared_store.add_shipment(
                화물종류=r["cargo_text"],
                출발지주소=r["origin_addr"],
                도착지주소=r["dest_addr"],
                출발지위도=r["origin_lat"],
                출발지경도=r["origin_lng"],
                도착지위도=r["dest_lat"],
                도착지경도=r["dest_lng"],
                출발화물역=im.origin_node_name,
                도착화물역=im.dest_node_name,
                중량톤=weight_ton,
                예약시각=now_kst_naive(),
                희망출발시각=departure_dt,
                도착예정시각=im.arrival_dt,
                요금원=im.total_fare_won,
                **{
                    "GWP(kgCO2eq)": im.total_gwp_kg_co2e,
                    "GWP절감(kgCO2eq대비트럭)": gwp_savings,
                    "탄소마일리지": mileage_earned,
                },
                결합화주ID목록=consolidation.grouped_order_ids,
                열차번호=im.train_no,
                시각표출처=im.schedule_source,
                첫마일완료시각=im.station_ready_dt,
                철도출발시각=im.rail_departure_dt,
                철도도착시각=im.rail_arrival_dt,
                막판마일시작시각=im.station_release_dt,
                첫마일거리km=im.first_mile_km,
                막판마일거리km=im.last_mile_km,
                철도구간거리km=im.rail_km,
                # ── 화차 배치 추천용(4단계) — 폼에서 직접 안 받은 값이라 근사/기본값 ──
                화물중량kg=weight_ton * 1000,
                화물길이cm=None,
                화물폭cm=None,
                화물높이cm=None,
                위험물여부=(category == CargoCategory.HAZARDOUS),
                액체기체위험물여부=(
                    category == CargoCategory.HAZARDOUS
                    and is_liquid_or_gas_hazmat(r["cargo_text"])
                ),
                파손주의여부=(category == CargoCategory.FRAGILE_HIGH_VALUE),
                화차배정=None,
            )
            st.session_state["last_shipment_id"] = shipment_id
            st.session_state.setdefault("my_shipment_ids", [])
            st.session_state["my_shipment_ids"].append(shipment_id)
            st.success(
                f"예약이 확정되었습니다. 화물ID **{shipment_id}** — "
                f"탄소 마일리지 **{mileage_earned:,}P**가 적립되었습니다. "
                "왼쪽 메뉴의 '화주용 실시간추적'에서 진행 상황을 확인하세요."
            )
    elif st.session_state.get("show_comparison"):
        st.caption("※ 철도 통합운송이 가능한 건에 한해 예약 확정 및 실시간 추적을 제공합니다.")

# ── AI 상담 챗봇 ──────────────────────────────────────────────
# ⚠️ 견적 비교 폼과 별개 섹션이라, 조건문(rail_available 등) 바깥의
# 최상위 레벨에 둔다. 견적을 아직 조회하지 않은 상태에서도 챗봇 자체는
# 열 수 있게(그럴 땐 "먼저 견적을 조회해달라"고 안내).
st.divider()
st.markdown("#### 💬 AI 상담 챗봇")
st.caption("견적 결과에 대해 궁금한 점을 물어보세요. (예: \"철도가 왜 더 저렴해?\", \"위험물이면 요금이 어떻게 바뀌어?\")")

if not gemini_assist.GEMINI_API_KEY:
    st.info("Gemini API 키가 설정되지 않아 챗봇을 사용할 수 없습니다. secrets.toml에 GEMINI_API_KEY를 등록해주세요.")
else:
    # 현재 화면에 표시된 견적 결과를 챗봇 컨텍스트로 넘긴다 — 챗봇이
    # 숫자를 새로 만들어내지 않고 이미 계산된 값 안에서만 답하게 하기 위함.
    have_quote = st.session_state.get("show_comparison") and "result" in st.session_state
    chat_context = None
    if have_quote:
        chat_context = {
            "화물종류": cargo_text,
            "화물종류_분류": category.value,
            "중량_kg": weight_kg,
            "트럭단독_요금_원": truck_fare,
            "트럭단독_거리_km": direct["distance_km"],
            "트럭단독_소요시간_분": direct["duration_min"],
            "트럭단독_CO2_kg": truck_emission["gwp_kg_co2e"],
            "철도통합운송_가능여부": rail_available,
        }
        if rail_available:
            chat_context.update({
                "철도통합운송_요금_원": im.total_fare_won,
                "철도통합운송_소요시간_분": im.total_duration_min,
                "철도통합운송_도착예정": im.arrival_dt.strftime("%Y-%m-%d %H:%M"),
                "철도통합운송_CO2_kg": im.total_gwp_kg_co2e,
                "출발화물역": im.origin_node_name,
                "도착화물역": im.dest_node_name,
                "트럭대비_탄소절감량_kgCO2eq": round(truck_emission["gwp_kg_co2e"] - im.total_gwp_kg_co2e, 1),
                "나무환산_그루_연간흡수량기준": tree_eq,
                "예약확정시_적립예상_탄소마일리지_P": mileage_preview,
            })
        else:
            chat_context["철도이용불가사유"] = consolidation.reason

    # 새로운 견적을 조회하면(폼 재제출로 result가 바뀌면) 챗봇도 최신
    # 컨텍스트로 다시 시작한다 — 이전 견적 얘기가 섞여 헷갈리지 않도록.
    current_result_key = id(st.session_state.get("result"))
    if (
        "chat_session" not in st.session_state
        or st.session_state.get("chat_context_key") != current_result_key
    ):
        try:
            st.session_state["chat_session"] = gemini_assist.start_chat(chat_context)
            st.session_state["chat_context_key"] = current_result_key
            st.session_state["chat_history"] = []
            st.session_state["chat_error"] = None
        except Exception as e:
            st.session_state["chat_session"] = None
            st.session_state["chat_error"] = str(e)

    if st.session_state.get("chat_error"):
        st.error(f"챗봇 세션을 시작하지 못했습니다: {st.session_state['chat_error']}")
    else:
        for role, text in st.session_state.get("chat_history", []):
            with st.chat_message(role):
                st.write(text)

        user_msg = st.chat_input("질문을 입력하세요")
        if user_msg:
            st.session_state["chat_history"].append(("user", user_msg))
            with st.chat_message("user"):
                st.write(user_msg)
            with st.chat_message("assistant"):
                with st.spinner("답변 작성 중..."):
                    try:
                        reply = gemini_assist.send_chat_message(
                            st.session_state["chat_session"], user_msg
                        )
                    except Exception as e:
                        reply = f"답변 생성 중 오류가 발생했습니다: {e}"
                st.write(reply)
            st.session_state["chat_history"].append(("assistant", reply))
