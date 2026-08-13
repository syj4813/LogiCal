# -*- coding: utf-8 -*-
"""화주용 실시간 Door-to-Door 추적.

page0_home.py에서 "철도 통합운송"으로 예약 확정한 화물만 여기 표시된다.
단계(현재단계)는 random이 아니라 shared_store.current_stage_idx()가
실제 시각표(가능한 경우) 또는 예약~도착 경과 비율로 결정론적으로 계산한다.

⚠️ st.set_page_config는 app.py(라우터)에서 한 번만 호출한다 — 서브페이지에서
   다시 호출하면 StreamlitAPIException이 난다.
"""

import streamlit as st

import shared_store
from gemini_assist import assess_delay_risk
from tz_utils import now_kst_naive

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
    "⚠️ 실제 지연 이력 데이터가 없어 학습된 예측 모델이 아닙니다. 아래 신호를 "
    "근거로 Gemini가 정성적으로 평가한 등급이며, 호출마다 표현이 달라질 수 있어 참고용입니다."
)


@st.cache_data(show_spinner=False)
def _cached_delay_risk(signals_key: str, signals: dict) -> dict:
    return assess_delay_risk(signals)


t_start = record.get("희망출발시각")
signals = {
    "시각표출처": record.get("시각표출처"),
    "결합배송여부": bool(record.get("결합화주ID목록")),
    "출발요일": t_start.strftime("%A") if t_start else None,
    "구간": f"{record.get('출발화물역', '-')}→{record.get('도착화물역', '-')}",
}
try:
    risk = _cached_delay_risk(record["화물ID"], signals)
    risk_icon = {"낮음": "🟢", "보통": "🟡", "높음": "🔴"}.get(risk.get("level"), "⚪")
    st.info(f"{risk_icon} **{risk.get('level', '판정불가')}** — {risk.get('reason', '-')}")
except Exception:
    st.caption("AI 위험도 평가를 생성하지 못했습니다 (Gemini API 키 확인 필요).")

st.divider()
st.subheader("진행 경로")
st.progress((stage_idx + 1) / len(shared_store.STAGE_LABELS))
for i, label in enumerate(shared_store.STAGE_LABELS):
    marker = "✅" if i < stage_idx else ("🚚" if i == stage_idx else "⬜")
    st.write(f"{marker} {label}")

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
if gwp_savings is not None:
    st.metric("탄소 절감량 (트럭 대비)", f"{gwp_savings:.1f} kgCO2eq")

st.caption(
    "※ 진행 단계는 실제 GPS/RFID 트래킹이 아니라, 실제 열차시각표(가능한 경우) 또는 "
    "예약시각~도착예정시각 경과 비율로 추정한 시뮬레이션입니다."
)
