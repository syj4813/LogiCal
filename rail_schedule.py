# -*- coding: utf-8 -*-
"""
코레일 "2026년도 화물열차 설정 현황"(2026-08-01 기준) 파싱.

⚠️ 2026-07-29(초판)에는 data.go.kr 공공데이터(2025-04-14 스냅샷, 정차사유
   기반 stop-level 데이터)를 썼으나, 2026-08-07 사용자가 실제 코레일
   내부자료(2026-08-01 기준 설정열차 현황)를 제공해 이걸로 교체함.
   새 데이터는 시발역→종착역 직행 단위로 이미 정리돼 있어(중간 정차역
   재구성 불필요) 이전보다 파싱이 단순해졌고, 날짜도 훨씬 최신이다.
⚠️ 그래도 특정 시점 스냅샷(8/1 기준)인 건 동일 — 임시열차 편성, 선로
   사정 등으로 실제 운행은 바뀔 수 있어 "확정 시각표"가 아닌 "참고
   시각표"로 취급해야 한다.
⚠️ "부산신항"으로 표기된 역명은 rail_freight_nodes.py의
   schedule_station="부산항"과 맞추기 위해 추출 시점에 "부산항"으로
   통일함.
⚠️ 원본에서 "변압기" 등 비정기·특수 화물열차(고정된 요일 패턴이 없는
   임시편성)는 요일 정보가 없어 이 CSV에서 제외했다 — 정기편성만 포함.

컬럼: 열차번호, 품목, 구분, 시발역, 출발시각, 종착역, 도착시각, 운행선, 운행요일
"""

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

CSV_PATH = Path(__file__).parent / "freight_train_schedule.csv"

# Python date.weekday(): 0=월요일 ... 6=일요일
_WEEKDAY_CHARS = ["월", "화", "수", "목", "금", "토", "일"]


def _parse_service_days(text: str) -> set[str]:
    if text == "매일":
        return set(_WEEKDAY_CHARS)
    # "일,화,수,목,금,토" 형태 — 쉼표 등 요일이 아닌 문자가 섞여도
    # day_char in service_days 판정에는 영향 없음.
    return set(text)


@dataclass
class TrainMatch:
    train_no: str
    departure_time: time
    arrival_time: time
    overnight: bool  # 도착시각이 다음날로 넘어가는지 (출발시각보다 이르면 다음날로 간주)
    service_days: set[str]
    line: str


_rows_cache: list[dict] | None = None


def _get_rows() -> list[dict]:
    global _rows_cache
    if _rows_cache is None:
        with open(CSV_PATH, encoding="utf-8") as f:
            _rows_cache = list(csv.DictReader(f))
    return _rows_cache


def find_direct_trains(origin_station: str, dest_station: str) -> list[TrainMatch]:
    """origin에서 dest로 직행하는 열차 목록 (요일 필터 전).

    새 데이터는 이미 시발역→종착역 단위로 정리돼 있어, 이전처럼
    같은 열차번호의 여러 정차역 중 순서를 재구성할 필요가 없다.
    """
    results = []
    for r in _get_rows():
        if r["시발역"] != origin_station or r["종착역"] != dest_station:
            continue
        dep_t = datetime.strptime(r["출발시각"], "%H:%M:%S").time()
        arr_t = datetime.strptime(r["도착시각"], "%H:%M:%S").time()
        results.append(TrainMatch(
            train_no=r["열차번호"],
            departure_time=dep_t,
            arrival_time=arr_t,
            overnight=arr_t < dep_t,
            service_days=_parse_service_days(r["운행요일"]),
            line=r["운행선"],
        ))
    return results


def find_next_departure(
    origin_station: str, dest_station: str, after_dt: datetime, search_days: int = 7
) -> dict | None:
    """after_dt 이후 가장 빠르게 출발하는 실제 열차. 없으면 None.

    ⚠️ 환승(중간에 다른 열차로 갈아타는 경로)은 고려하지 않음 — 직행
    열차만 찾음. 실제로는 화물도 중계 운송(다른 열차로 환적)이 흔하지만
    이 근사에서는 단순화함.
    """
    candidates = find_direct_trains(origin_station, dest_station)
    if not candidates:
        return None

    best: TrainMatch | None = None
    best_departure_dt: datetime | None = None

    for day_offset in range(search_days):
        check_date = after_dt.date() + timedelta(days=day_offset)
        day_char = _WEEKDAY_CHARS[check_date.weekday()]
        for c in candidates:
            if day_char not in c.service_days:
                continue
            dep_dt = datetime.combine(check_date, c.departure_time)
            if dep_dt < after_dt:
                continue
            if best_departure_dt is None or dep_dt < best_departure_dt:
                best_departure_dt = dep_dt
                best = c
        if best is not None:
            break

    if best is None or best_departure_dt is None:
        return None

    arr_date = best_departure_dt.date() + timedelta(days=1 if best.overnight else 0)
    arrival_dt = datetime.combine(arr_date, best.arrival_time)

    return {
        "train_no": best.train_no,
        "departure_dt": best_departure_dt,
        "arrival_dt": arrival_dt,
        "duration_min": round((arrival_dt - best_departure_dt).total_seconds() / 60),
        "line": best.line,
    }
