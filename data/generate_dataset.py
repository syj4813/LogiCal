# -*- coding: utf-8 -*-
"""
화물열차 지연위험도(운휴 위험) 합성 데이터 생성기 — LightGBM 학습용.

실측 근거 (2026 화물열차운행계획 엑셀 파일):
  - 요일별 실제 운휴율 (2024년 7월, 31일 실적, '7월 화물열차운행계획' 시트):
      월 17.1%, 화 17.7%, 수 16.6%, 목 15.9%, 금 15.7%, 토 24.3%, 일 28.8%
  - 운휴 사유 355건 실적 분포 ('표5' 시트): 시멘트 물량조절 57건, 컨테이너
    물량감소 50건, 공차 사전 집결수송 48건, 물량 없음 32건, 협약일 조정
    19건 등 — 품목별 물량 변동성과 "공차회송"이 취소의 핵심 요인임을 시사
  - 노선별 평균 운행거리·편성 수 ('표2' 시트, 575건): 경부선(320km,144건),
    중앙선(193km,103건), 충북선(161km,71건), 전라선(229km,49건) 등

이 데이터의 "정답 라벨(운휴여부)"은 실제 개별 열차의 취소 이력이 아니라,
위 실측 통계(요일별 기준율, 품목별/공차 관련 사유 비중)를 계수로 삼아
확률적으로 생성한 것이다 — 즉 "각 열차가 실제로 취소됐는지"가 아니라
"실측된 패턴을 반영하면 이런 분포가 나온다"를 재현한 합성 데이터다.
실제 개별 이력 데이터를 코레일로부터 받으면 이 생성기를 걷어내고 그
데이터로 바로 학습시키면 된다.
"""

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)
N = 10_000

# ── 실측 노선별 분포 (표2 시트, 575건 집계) ──
ROUTES = {
    "경부선": {"weight": 144, "dist_mean": 320.2, "dist_std": 40},
    "중앙선": {"weight": 103, "dist_mean": 193.3, "dist_std": 35},
    "충북선": {"weight": 71, "dist_mean": 161.3, "dist_std": 25},
    "전라선": {"weight": 49, "dist_mean": 229.4, "dist_std": 30},
    "장항선": {"weight": 34, "dist_mean": 179.6, "dist_std": 20},
    "태백선": {"weight": 28, "dist_mean": 135.5, "dist_std": 20},
    "부산신항선": {"weight": 25, "dist_mean": 10.6, "dist_std": 3},
    "호남선": {"weight": 20, "dist_mean": 208.2, "dist_std": 25},
    "영동선": {"weight": 19, "dist_mean": 109.8, "dist_std": 15},
}
route_names = list(ROUTES.keys())
route_probs = np.array([v["weight"] for v in ROUTES.values()], dtype=float)
route_probs /= route_probs.sum()

# ── 실측 품목 분포 (표2 시트 수송품목 컬럼 근사 비중) ──
CARGO_TYPES = {
    "컨테이너": 0.34,
    "시멘트": 0.27,
    "석탄": 0.10,
    "철강": 0.10,
    "유류": 0.08,
    "광석": 0.06,
    "기타": 0.05,
}
cargo_names = list(CARGO_TYPES.keys())
cargo_probs = np.array(list(CARGO_TYPES.values()))
cargo_probs /= cargo_probs.sum()

# 품목별 물량 변동성(취소 위험 가산) — 표5 사유 분포 근거:
# "시멘트 물량조절"(57건, 1위) + "컨테이너 물량감소"(50건, 2위)가
# 전체 355건 중 30%를 차지 -> 이 두 품목이 물량 변동에 취약함을 실측이 뒷받침.
# 유류/광석은 장기계약 기반 벌크품목이라 상대적으로 안정적이라고 가정(⚠️ 추정).
CARGO_VOLATILITY_LOGIT = {
    "시멘트": 0.55,
    "컨테이너": 0.45,
    "철강": 0.10,
    "기타": 0.05,
    "석탄": -0.05,
    "광석": -0.20,
    "유류": -0.30,
}

# 요일별 실측 운휴율(위 docstring 근거) -> 로짓 변환
WEEKDAY_RATE = {
    "월": 0.171, "화": 0.177, "수": 0.166, "목": 0.159,
    "금": 0.157, "토": 0.243, "일": 0.288,
}
WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]


def _logit(p):
    return np.log(p / (1 - p))


def generate():
    dates = pd.to_datetime("2026-01-01") + pd.to_timedelta(
        RNG.integers(0, 365, size=N), unit="D"
    )
    weekday_idx = dates.weekday  # 0=월
    weekday = np.array(WEEKDAY_NAMES)[weekday_idx]
    month = dates.month

    route = RNG.choice(route_names, size=N, p=route_probs)
    dist_mean = np.array([ROUTES[r]["dist_mean"] for r in route])
    dist_std = np.array([ROUTES[r]["dist_std"] for r in route])
    distance_km = np.clip(RNG.normal(dist_mean, dist_std), 5, None).round(1)

    cargo = RNG.choice(cargo_names, size=N, p=cargo_probs)
    weight_ton = np.clip(RNG.normal(12, 6, size=N), 1, 25).round(1)

    direction = RNG.choice(["상", "하"], size=N)
    departure_slot = RNG.choice(
        ["새벽(00-06)", "오전(06-12)", "오후(12-18)", "야간(18-24)"],
        size=N, p=[0.30, 0.25, 0.20, 0.25],
    )

    # 공차 사전 집결수송 — 표5 355건 중 48건(13.5%)이 이 사유, 실측 기반 비중
    empty_reposition = RNG.random(N) < 0.135
    # LCL 결합배송 여부(다른 화주와 공유 적재) — 별도 근거 없어 30%로 가정(⚠️ 추정)
    consolidated = RNG.random(N) < 0.30

    # 계절 리스크 — 장마철(6~8월)/동절기 폭설(12~2월) 소폭 가산 (⚠️ 일반적으로
    # 알려진 한국 기후 패턴에 근거한 가정, 이 파일의 실측 통계는 아님)
    monsoon = np.isin(month, [6, 7, 8])
    winter_snow = np.isin(month, [12, 1, 2])

    # ── 운휴(지연) 확률 로짓 결합 ──
    base_logit = np.array([_logit(WEEKDAY_RATE[w]) for w in weekday])
    cargo_logit = np.array([CARGO_VOLATILITY_LOGIT[c] for c in cargo])
    empty_logit = np.where(empty_reposition, 0.85, 0.0)  # 공차회송이면 취소위험 크게 가산
    consolidated_logit = np.where(consolidated, -0.15, 0.0)  # 결합배송이면 취소위험 소폭 감소(계약 안정)
    season_logit = np.where(monsoon, 0.20, 0.0) + np.where(winter_snow, 0.15, 0.0)
    long_route_logit = (distance_km - 200) / 400 * 0.10  # 장거리일수록 소폭 위험 증가(⚠️ 추정)
    noise = RNG.normal(0, 0.35, size=N)

    # 보정: 다른 변수들의 가산 효과 평균을 빼서, 요일별 기준 확률(base_logit)이
    # 실측 요일별 운휴율과 맞아떨어지도록 재중심화한다. 이렇게 해야
    # "요일별 평균은 실측과 같지만, 그 안에서 품목/공차회송 등에 따라
    # 개별 건별로 위험도가 갈린다"는 구조가 유지된다.
    other_terms = cargo_logit + empty_logit + consolidated_logit + season_logit + long_route_logit
    other_terms_centered = other_terms - other_terms.mean()

    logit = base_logit + other_terms_centered + noise
    prob = 1 / (1 + np.exp(-logit))
    is_delayed = (RNG.random(N) < prob).astype(int)

    df = pd.DataFrame({
        "운행일자": dates.strftime("%Y-%m-%d"),
        "요일": weekday,
        "월": month,
        "주운행선": route,
        "운행거리_km": distance_km,
        "수송품목": cargo,
        "화물중량_톤": weight_ton,
        "상하": direction,
        "출발시간대": departure_slot,
        "공차회송여부": empty_reposition.astype(int),
        "결합배송여부": consolidated.astype(int),
        "장마철여부": monsoon.astype(int),
        "동절기여부": winter_snow.astype(int),
        "운휴_지연위험_실현": is_delayed,  # 학습 타깃 (1=운휴/고위험 실현)
    })
    return df


if __name__ == "__main__":
    df = generate()
    df.to_csv("delay_risk_dataset.csv", index=False, encoding="utf-8-sig")
    print(f"생성 완료: {len(df)}행")
    print(f"전체 운휴 실현율: {df['운휴_지연위험_실현'].mean()*100:.1f}% (실측 전체 평균 18.2%와 비교)")
    print()
    print("요일별 운휴 실현율 (합성 vs 실측):")
    real = {"월": 17.1, "화": 17.7, "수": 16.6, "목": 15.9, "금": 15.7, "토": 24.3, "일": 28.8}
    syn = df.groupby("요일")["운휴_지연위험_실현"].mean() * 100
    for w in WEEKDAY_NAMES:
        print(f"  {w}: 합성 {syn[w]:.1f}%  |  실측 {real[w]:.1f}%")
