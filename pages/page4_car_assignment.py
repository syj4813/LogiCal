# -*- coding: utf-8 -*-
"""화차 배치 추천.

shared_store에 예약 확정된 "철도 통합운송" 건 중 아직 화차가 배정되지
않은 건을 골라, 학습된 모델(car_assignment.py)로 화차 Top5를 추천하고
확정하면 shared_store에 반영한다.

⚠️ 이 화면에서 다루는 값들의 한계:
  - 화물 규격(길이/폭/높이)은 화주 폼에서 안 받아서 기본값(100x60x50cm)
    폴백이다 — 실측 규격이 아니다.
  - 화차 편성 자체가 mock(열차번호 시드 기반 결정론적 생성)이라 실제
    편성표가 아니다.
  - 위험물 등급(hazmat_class)은 이 앱의 "위험물여부/액체기체위험물여부"
    2단계 판정을 모델이 기대하는 0~4 등급 중 하나로 거칠게 매핑한
    근사치다.
"""

import streamlit as st

import car_assignment
import shared_store
from cargo import CargoCategory

st.title("🚃 화차 배치 추천")
st.caption("예약 확정된 철도 통합운송 건 중 화차가 아직 배정되지 않은 화물에 화차를 추천·배정합니다.")

if st.button("🔄 새로고침"):
    st.rerun()

shipments = shared_store.read_shipments()
rail_shipments = [s for s in shipments if s.get("출발화물역") and s.get("도착화물역")]
unassigned = [s for s in rail_shipments if s.get("화차배정") is None]
assigned = [s for s in rail_shipments if s.get("화차배정") is not None]

if not unassigned:
    st.info("현재 화차 배정이 필요한 예약 건이 없습니다.")
    if not rail_shipments:
        st.page_link("pages/page0_home.py", label="견적 비교로 이동 →", icon="🚚")
    st.stop()

id_options = [s["화물ID"] for s in unassigned]
selected_id = st.selectbox(
    "화차를 배정할 예약 건을 선택하세요",
    options=id_options,
    format_func=lambda x: f"{x} · {shared_store.get_shipment(x).get('화물종류', '-')} · "
                           f"{shared_store.get_shipment(x).get('출발화물역', '-')}→{shared_store.get_shipment(x).get('도착화물역', '-')}",
)
record = shared_store.get_shipment(selected_id)

c1, c2, c3, c4 = st.columns(4)
c1.metric("화물종류", record.get("화물종류", "-"))
c2.metric("중량", f"{record.get('중량톤', 0):.1f}톤")
c3.metric("열차번호", record.get("열차번호") or "미매칭(추정시각표)")
c4.metric("위험물 여부", "⚠️ 위험물" if record.get("위험물여부") else "일반")

# ── 화물 규격: 폼에서 안 받은 값이라 기본값 폴백 ──
length = record.get("화물길이cm") or car_assignment.DEFAULT_CARGO_DIMS_CM[0]
width = record.get("화물폭cm") or car_assignment.DEFAULT_CARGO_DIMS_CM[1]
height = record.get("화물높이cm") or car_assignment.DEFAULT_CARGO_DIMS_CM[2]
if record.get("화물길이cm") is None:
    st.caption(f"⚠️ 화물 규격 실측값이 없어 기본값({length:.0f}×{width:.0f}×{height:.0f}cm)으로 계산합니다.")

# ── 위험물 등급 근사 매핑 ──
if not record.get("위험물여부"):
    hazmat_class = 0
elif record.get("액체기체위험물여부"):
    hazmat_class = 3  # 액체·기체 위험물 근사 등급
else:
    hazmat_class = 4  # 고체 위험물 근사 등급

# ── mock 편성: 열차번호가 있으면 그걸로, 없으면(추정시각표) 화물ID로 시드 고정 ──
train_key = record.get("열차번호") or f"EST-{selected_id}"
wagons = car_assignment.generate_mock_train_composition(train_key, n_wagons=25)

recommendations = car_assignment.recommend_wagons(
    cargo_weight_kg=record.get("화물중량kg", record.get("중량톤", 0) * 1000),
    cargo_length_cm=length,
    cargo_width_cm=width,
    cargo_height_cm=height,
    hazmat_class=hazmat_class,
    fragile_flag=bool(record.get("파손주의여부")),
    wagons=wagons,
    is_liquid_or_gas_hazmat=bool(record.get("액체기체위험물여부")),
)

st.divider()
st.subheader("추천 화차 Top 5" if hazmat_class == 0 or not record.get("액체기체위험물여부") else "추천 화차 (액체·기체 위험물 — 탱크차만 허용)")

if recommendations.empty:
    st.error(
        "이 편성에는 조건에 맞는 화차가 없습니다"
        + ("(탱크차가 없는 편성입니다 — 다른 열차 배정이 필요합니다)." if hazmat_class > 0 and record.get("액체기체위험물여부") else ".")
    )
else:
    st.dataframe(recommendations, width="stretch", hide_index=True)

    chosen_car_no = st.selectbox("배정할 화차를 선택하세요", recommendations["화차번호"].tolist())
    chosen_row = recommendations[recommendations["화차번호"] == chosen_car_no].iloc[0]
    if chosen_row["적재가능여부"] == "❌ 초과":
        st.warning("선택한 화차는 잔여 적재중량을 초과합니다 — 그래도 배정하시겠습니까?")

    if st.button("✅ 이 화차로 배정 확정", type="primary"):
        ok = shared_store.assign_car(selected_id, chosen_car_no)
        if ok:
            st.success(f"화물 {selected_id} → 화차 {chosen_car_no} 배정 완료.")
        else:
            st.error("배정에 실패했습니다 — 예약 건을 찾을 수 없습니다.")

if assigned:
    st.divider()
    with st.expander(f"배정 완료된 예약 건 ({len(assigned)}건)"):
        st.table([
            {"화물ID": s["화물ID"], "화물종류": s.get("화물종류", "-"), "배정 화차": s.get("화차배정")}
            for s in assigned
        ])
