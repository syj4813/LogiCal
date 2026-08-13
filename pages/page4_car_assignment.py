# -*- coding: utf-8 -*-
"""화차 배치 추천.

shared_store에 예약 확정된 "철도 통합운송" 건 중 아직 화차가 배정되지
않은 건을 골라, 학습된 모델(car_assignment.py, LightGBM 150트리,
테스트 R²=0.970)로 화차 Top5를 추천하고 확정하면 shared_store에 반영한다.

⚠️ 이 화면에서 다루는 값들의 한계:
  - 화물 규격(길이/폭/높이)은 화주 폼에서 실측으로 안 받는다 — 대신
    중량+화물종류(카테고리별 평균 밀도)로 추정한다(cargo.estimate_dims_cm).
    실측 규격이 아니다.
  - 화차 편성 자체가 mock(열차번호 시드 기반 결정론적 생성)이라 실제
    편성표가 아니다. 편성 화차 수는 관제센터 담당자가 슬라이더로 직접
    조정할 수 있다(실제 편성 데이터를 받기 전까지의 임시 방편).
  - 위험물 등급(hazmat_class)은 이 앱의 "위험물여부/액체기체위험물여부"
    2단계 판정을 모델이 기대하는 0~4 등급 중 하나로 거칠게 매핑한
    근사치다.
"""

import streamlit as st

import car_assignment
import shared_store
from cargo import classify_cargo_type, estimate_dims_cm

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
    .fx-rank-card {
        background: #FFFFFF;
        border: 1px solid #E3E9E6;
        border-radius: 14px;
        padding: 20px 24px;
        box-shadow: 0 2px 10px rgba(15, 110, 79, 0.06);
        display: flex;
        align-items: center;
        gap: 22px;
        margin-bottom: 10px;
    }
    .fx-rank-card.fx-rank-1 { border: 1.5px solid #0F6E4F; box-shadow: 0 4px 16px rgba(15, 110, 79, 0.15); }
    .fx-rank-badge { font-size: 0.95rem; color: #5A6B63; white-space: nowrap; }
    .fx-rank-score { font-size: 2.1rem; font-weight: 800; color: #0F6E4F; line-height: 1; margin-top: 2px; }
    .fx-rank-detail { flex: 1; }
    .fx-rank-carno { font-size: 1.15rem; font-weight: 700; }
    .fx-rank-sub { color: #5A6B63; font-size: 0.9rem; margin-top: 4px; }
    .fx-status-pill {
        display: inline-block; padding: 6px 14px; border-radius: 999px;
        font-weight: 700; font-size: 0.95rem; white-space: nowrap;
    }
    .fx-status-ok { background: #E4F5EA; color: #0F6E4F; }
    .fx-status-bad { background: #FBE7E4; color: #B0392F; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    """
    <div class="fx-hero">
      <h2>🚃 화차 배치 추천</h2>
      <p>예약된 화물에 가장 적합한 화차를 자동으로 추천합니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if st.button("🔄 새로고침"):
    st.rerun()

if st.session_state.get("last_car_assignment"):
    msg = st.session_state.pop("last_car_assignment")
    st.success(f"✅ 배정 완료: {msg}")
    st.divider()

shipments = shared_store.read_shipments()
rail_shipments = [s for s in shipments if s.get("출발화물역") and s.get("도착화물역")]
unassigned = [s for s in rail_shipments if s.get("화차배정") is None]
assigned = [s for s in rail_shipments if s.get("화차배정") is not None]

if not unassigned:
    st.info("현재 화차 배정이 필요한 예약 건이 없습니다. (철도 통합운송 예약 중 미배정 건만 표시)")
    if not rail_shipments:
        st.page_link("pages/page0_home.py", label="견적 비교로 이동 →", icon="🚚")
    st.stop()

labels = [
    f"{s['화물ID']} · {s.get('열차번호') or '추정시각표'} · {s.get('화물종류', '-')} {s.get('중량톤', '-')}톤"
    for s in unassigned
]
idx = st.selectbox("배치할 화물을 선택하세요", range(len(unassigned)), format_func=lambda i: labels[i])
record = unassigned[idx]
selected_id = record["화물ID"]

st.subheader(f"화물 {selected_id} — 열차 {record.get('열차번호') or '배정 예정'} 편성")

total_cars = st.slider("편성 화차 수", min_value=10, max_value=30, value=20)
train_key = record.get("열차번호") or f"EST-{selected_id}"
wagons = car_assignment.generate_mock_train_composition(train_key, n_wagons=total_cars)

with st.expander("편성 전체 보기"):
    st.dataframe(
        [
            {
                "화차번호": w.car_no, "종류": w.car_type,
                "최대적재(kg)": w.car_max_load_kg, "현재적재(kg)": w.car_current_load_kg,
                "잔여용적(m³)": w.car_remaining_capacity_m3,
                "위험물차와거리": w.distance_from_hazmat_car, "위치": w.position_in_car,
            }
            for w in wagons
        ],
        width="stretch", hide_index=True,
    )

# ── 화물 규격: 실측값이 있으면 그대로, 없으면 중량+화물종류로 추정 ──
weight_kg = record.get("화물중량kg") or (record.get("중량톤", 0) * 1000)
if record.get("화물길이cm"):
    length, width, height = record["화물길이cm"], record["화물폭cm"], record["화물높이cm"]
else:
    category = classify_cargo_type(record.get("화물종류", ""))
    length, width, height = estimate_dims_cm(weight_kg, category)

hazmat = bool(record.get("위험물여부"))
liquid_or_gas = bool(record.get("액체기체위험물여부"))
fragile = bool(record.get("파손주의여부"))
hazmat_class = 0 if not hazmat else (3 if liquid_or_gas else 4)  # ⚠️ 근사 매핑

c1, c2, c3, c4 = st.columns(4)
c1.metric("중량", f"{weight_kg:,.0f} kg")
c2.metric("규격(길이×폭×높이)", f"{length:.0f}×{width:.0f}×{height:.0f} cm")
c3.metric("위험물", ("예 (액체·기체)" if liquid_or_gas else "예 (고체)") if hazmat else "아니오")
c4.metric("파손주의", "예" if fragile else "아니오")

recommendations = car_assignment.recommend_wagons(
    cargo_weight_kg=weight_kg,
    cargo_length_cm=length,
    cargo_width_cm=width,
    cargo_height_cm=height,
    hazmat_class=hazmat_class,
    fragile_flag=fragile,
    wagons=wagons,
    is_liquid_or_gas_hazmat=liquid_or_gas,
)

st.divider()
st.subheader("🤖 추천 순위 (적합도 점수)")
if liquid_or_gas:
    st.caption("ℹ️ 액체·기체 위험물이라 탱크차만 후보로 보여드립니다.")
else:
    st.caption("ℹ️ 액체·기체 위험물이 아니라 탱크차는 후보에서 제외했습니다.")

if recommendations.empty:
    st.error("이 편성에는 조건에 맞는 화차가 없습니다 — 화차 수를 늘리거나 다른 열차를 선택해주세요.")
    st.stop()

# 추천 카드마다 큼직한 순위/점수와 상태를 보여주고, 바로 아래 전용
# 버튼으로 배정합니다 (카드 자체는 st.markdown이라 배지·색상 등을
# 자유롭게 꾸밀 수 있고, 배정 동작은 별도 st.button이 담당).
for rank, row in recommendations.reset_index(drop=True).iterrows():
    remaining_kg = row["최대적재_kg"] - row["현재적재_kg"]
    assignable = row["적재가능여부"] != "❌ 초과"

    if not assignable:
        status_html = '<span class="fx-status-pill fx-status-bad">❌ 적재 초과</span>'
    elif liquid_or_gas and row["위험물차와_거리"] == 0:
        status_html = '<span class="fx-status-pill fx-status-ok">✅ 위험물차 본인</span>'
    else:
        status_html = '<span class="fx-status-pill fx-status-ok">✅ 적재 가능</span>'

    card_class = "fx-rank-card fx-rank-1" if rank == 0 else "fx-rank-card"
    st.markdown(
        f"""
        <div class="{card_class}">
          <div style="text-align:center; min-width:76px;">
            <div class="fx-rank-badge">{rank + 1}위</div>
            <div class="fx-rank-score">{row['적합도_점수'] * 100:.1f}점</div>
          </div>
          <div class="fx-rank-detail">
            <div class="fx-rank-carno">{row['화차번호']} · {row['화차종류']} · {row['위치']}</div>
            <div class="fx-rank-sub">잔여적재 {remaining_kg:,.0f}kg / 잔여용적 {row['잔여용적_m3']}m³ · 위험물차와 {row['위험물차와_거리']}칸</div>
          </div>
          {status_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button(
        f"✅ {row['화차번호']} 화차로 배정",
        key=f"assign_card_{selected_id}_{row['화차번호']}",
        width="stretch",
        disabled=not assignable,
    ):
        shared_store.assign_car(selected_id, row["화차번호"])
        st.session_state["last_car_assignment"] = (
            f"{selected_id} → {row['화차번호']} 화차"
        )
        st.rerun()
    st.write("")

if assigned:
    st.divider()
    with st.expander(f"배정 완료된 예약 건 ({len(assigned)}건)"):
        st.table([
            {"화물ID": s["화물ID"], "화물종류": s.get("화물종류", "-"), "배정 화차": s.get("화차배정")}
            for s in assigned
        ])
