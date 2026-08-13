# -*- coding: utf-8 -*-
"""관제센터 통합 대시보드.

shared_store에 기록된 전체 화주 예약을 한 화면에서 집계합니다.
실시간 GPS/RFID 데이터가 없는 데모 구조이므로 현재 단계는 예약/열차 시각을
기준으로 shared_store.current_stage_idx()가 계산합니다.
"""

from collections import Counter

import pandas as pd
import streamlit as st

import shared_store
from tz_utils import now_kst_naive

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
    .fx-card {
        background: #FFFFFF;
        border: 1px solid #E3E9E6;
        border-radius: 14px;
        padding: 8px 20px;
        box-shadow: 0 2px 10px rgba(15, 110, 79, 0.06);
    }
    .fx-station-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 4px; border-bottom: 1px solid #E3E9E6; font-size: 0.95rem;
    }
    .fx-station-row:last-child { border-bottom: none; }
    .fx-station-bar {
        background: #EAF6EF; border-radius: 6px; height: 8px; margin-top: 6px;
        overflow: hidden;
    }
    .fx-station-bar-fill { background: #0F6E4F; height: 100%; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="fx-hero">
      <h2>🛰️ 관제센터 대시보드</h2>
      <p>전체 예약 · 운송 단계 · 화물역 집중도 · 개별 화물 현황</p>
    </div>
    """,
    unsafe_allow_html=True,
)

head1, head2 = st.columns([5, 1])
with head1:
    st.caption(f"조회 시각: {now_kst_naive().strftime('%Y-%m-%d %H:%M:%S')}")
with head2:
    if st.button("🔄 새로고침", width="stretch"):
        st.rerun()

shipments = shared_store.read_shipments()

if not shipments:
    st.info(
        "아직 예약된 화물이 없습니다. 화주 견적 비교 페이지에서 철도 통합운송 예약을 "
        "확정하면 관제센터에 자동 집계됩니다."
    )
    st.stop()

# ── 공통 계산 ───────────────────────────────────────────────
total_count = len(shipments)
grouped_count = sum(1 for s in shipments if s.get("결합화주ID목록"))
assigned_count = sum(1 for s in shipments if s.get("화차배정") is not None)
total_gwp_savings = sum(float(s.get("GWP절감(kgCO2eq대비트럭)") or 0) for s in shipments)
total_mileage = sum(int(s.get("탄소마일리지") or 0) for s in shipments)

lead_times_min = []
for s in shipments:
    t_start = s.get("희망출발시각")
    t_eta = s.get("도착예정시각")
    if t_start and t_eta:
        lead_times_min.append((t_eta - t_start).total_seconds() / 60)
avg_lead_time = sum(lead_times_min) / len(lead_times_min) if lead_times_min else None

stage_counts = Counter()
for s in shipments:
    idx = shared_store.current_stage_idx(s)
    stage_counts[shared_store.STAGE_LABELS[idx]] += 1

in_transit_count = sum(
    stage_counts.get(label, 0)
    for label in shared_store.STAGE_LABELS[1:-1]
)

# ── KPI ─────────────────────────────────────────────────────
# ⚠️ 탄소절감량(kgCO2eq)과 발행 탄소마일리지(P)를 같이 보여준다 — 전자는
# ESG/탄소중립 실적 보고용, 후자는 화주에게 지급해야 할 마일리지 부채
# 규모를 파악하는 운영 지표라 목적이 달라서 하나로 통합하지 않는다.
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("전체 예약", f"{total_count}건")
k2.metric("운송 진행 중", f"{in_transit_count}건")
k3.metric("결합 배송", f"{grouped_count}건", f"{grouped_count / total_count * 100:.1f}%")
k4.metric("화차 배정 완료", f"{assigned_count}건", f"{assigned_count / total_count * 100:.1f}%")
k5.metric("탄소절감 합계", f"{total_gwp_savings:,.1f} kgCO₂eq")
k6.metric("🪙 발행 탄소마일리지", f"{total_mileage:,}P")

if avg_lead_time is not None:
    st.caption(f"평균 door-to-door 리드타임: {avg_lead_time:.0f}분")

st.divider()

# ── 운송 단계 현황 ──────────────────────────────────────────
st.subheader("🚦 현재 운송 단계")

stage_cols = st.columns(4)
for i, label in enumerate(shared_store.STAGE_LABELS):
    stage_cols[i % 4].metric(label, f"{stage_counts.get(label, 0)}건")

st.divider()

# ── 화물역별 수요 ───────────────────────────────────────────
st.subheader("🏭 화물역별 예약 집중도")
st.caption("각 역이 출발 또는 도착 화물역으로 포함된 예약 건수를 집계합니다.")

station_counts = Counter()
for s in shipments:
    if s.get("출발화물역"):
        station_counts[s["출발화물역"]] += 1
    if s.get("도착화물역"):
        station_counts[s["도착화물역"]] += 1

if station_counts:
    max_count = max(station_counts.values())
    rows_html = ""
    for station, count in sorted(station_counts.items(), key=lambda x: x[1], reverse=True):
        pct = round(count / max_count * 100)
        # ⚠️ 줄바꿈/들여쓰기가 섞인 멀티라인 f-string을 그대로 이어붙이면,
        # 블록 사이에 빈 줄이 생겨서 Streamlit(commonmark) 마크다운 파서가
        # 첫 블록만 raw HTML로 인식하고 그 뒤부터는 일반 텍스트로 취급해
        # HTML 소스가 화면에 그대로 노출되는 문제가 있었다(실제 확인됨).
        # 한 줄짜리 문자열로 이어붙여서 빈 줄이 안 생기게 한다.
        rows_html += (
            '<div class="fx-station-row">'
            '<div style="flex: 1;">'
            f'<div>{station}</div>'
            f'<div class="fx-station-bar"><div class="fx-station-bar-fill" style="width:{pct}%;"></div></div>'
            '</div>'
            f'<div style="margin-left: 16px; font-weight: 600;">{count}건</div>'
            '</div>'
        )
    st.markdown(f'<div class="fx-card">{rows_html}</div>', unsafe_allow_html=True)
else:
    st.info("집계할 화물역 정보가 없습니다.")

st.divider()

# ── 개별 화물 조회 ──────────────────────────────────────────
st.subheader("🔎 개별 화물 모니터링")

filter_cols = st.columns([1.3, 1.3, 1.4])
stations = sorted({
    x for s in shipments for x in (s.get("출발화물역"), s.get("도착화물역")) if x
})
with filter_cols[0]:
    station_filter = st.selectbox("화물역", ["전체"] + stations)
with filter_cols[1]:
    stage_filter = st.selectbox("현재 단계", ["전체"] + shared_store.STAGE_LABELS)
with filter_cols[2]:
    keyword = st.text_input("화물ID/화물종류 검색", placeholder="예: KRL 또는 전자부품")

rows = []
for s in shipments:
    stage_idx = shared_store.current_stage_idx(s)
    stage_label = shared_store.STAGE_LABELS[stage_idx]

    if station_filter != "전체" and station_filter not in {
        s.get("출발화물역"), s.get("도착화물역")
    }:
        continue
    if stage_filter != "전체" and stage_label != stage_filter:
        continue

    haystack = f"{s.get('화물ID', '')} {s.get('화물종류', '')}".lower()
    if keyword.strip() and keyword.strip().lower() not in haystack:
        continue

    rows.append(
        {
            "화물ID": s.get("화물ID"),
            "화물종류": s.get("화물종류"),
            "출발역": s.get("출발화물역"),
            "도착역": s.get("도착화물역"),
            "중량(톤)": s.get("중량톤"),
            "현재 단계": stage_label,
            "열차번호": s.get("열차번호") or "-",
            "화차": s.get("화차배정") if s.get("화차배정") is not None else "미배정",
            "결합배송": "✅" if s.get("결합화주ID목록") else "-",
            "도착예정": s.get("도착예정시각"),
        }
    )

if rows:
    df = pd.DataFrame(rows)
    if "도착예정" in df.columns:
        df = df.sort_values("도착예정", ascending=True, na_position="last")
    st.dataframe(df, width="stretch", hide_index=True)
else:
    st.info("현재 필터 조건에 맞는 화물이 없습니다.")

st.caption(
    "※ 이 대시보드는 shared_store에 실제 등록된 예약 데이터를 집계합니다. "
    "현재 단계는 실제 GPS/RFID가 아니라 예약/철도 시각표를 기준으로 계산됩니다."
)
