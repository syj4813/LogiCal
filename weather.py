# -*- coding: utf-8 -*-
"""
기상청 API Hub(apihub.kma.go.kr) 단기/중기 육상예보 조회.

⚠️ delay_risk.py(LightGBM 모델)의 입력 피처로 쓰는 게 아니다 — 모델은
   재학습하지 않는다. 대신 예보 결과를 사람이 읽을 수 있는 문장으로
   요약해서, gemini_assist.explain_delay_risk()의 프롬프트에 "참고
   신호"로 얹어 Gemini가 정성적으로 언급하게 하는 용도다(계산은
   모델, 설명은 AI 원칙과 동일한 결로 — 다만 여기서는 모델 확률
   자체에 날씨가 반영되진 않는다는 걸 명확히 인지하고 써야 한다).

인증: 통합인증키를 authKey 파라미터로 쓴다 (data.go.kr의 serviceKey와
      다른 시스템). 발급 방법: https://apihub.kma.go.kr

사용하는 엔드포인트 (전부 typ01, 고정폭/구분자 텍스트 응답):
  - fct_afs_dl.php : 단기 육상예보 (오늘~+5일, 세분화된 지점코드 필요.
    예: 11B10101=서울. reg=11B00000처럼 광역코드를 넣으면 빈 응답만 옴 —
    확인된 사실, 반드시 세분화된 지점코드를 써야 한다.)
  - fct_afs_wl.php : 중기 육상예보 (+3~+10일, 하늘상태/강수확률.
    광역코드(예: 11B00000=서울·인천·경기)로도 동작 확인됨.)
  - fct_afs_wc.php : 중기 기온예보 (+3~+10일, 최저/최고기온.
    fct_afs_dl과 마찬가지로 세분화된 지점코드가 필요함 — 확인됨.)

⚠️ 7개 화물역 전부 세분화된 지점코드로 검증됨(2026-08-13, kma_find_region_codes.py
   결과 기준): 오봉/의왕→의왕(11B20609), 부산항/부산진→부산(11H20201),
   천안→천안(11C20301), 순천→순천(11F20603), 포항→포항(11H10201).
"""

from datetime import datetime
from pathlib import Path

import requests

BASE_URL = "https://apihub.kma.go.kr/api/typ01/url"

# ── 화물역 -> 예보구역코드 매핑 ──
# REG_CODE_MID: 중기예보용 광역코드 (fct_afs_wl/wc에 필요할 수 있는 광역 단위,
#   여기 없는 지역은 fct_afs_wc가 세분화된 코드를 요구하는 걸로 확인돼서
#   REG_CODE_SHRT와 동일하게 세분화된 코드를 채워야 할 가능성이 높다).
# REG_CODE_SHRT: 단기예보(fct_afs_dl)/중기기온(fct_afs_wc)용 세분화된 지점코드.
#   ⚠️ 서울(오봉/의왕 임시 대체)만 검증됨. 나머지는 kma_find_region_codes.py
#   결과로 채워야 한다 — 빈 문자열이면 조회를 건너뛴다.
REG_CODE_SHRT: dict[str, str] = {
    "오봉역": "11B20609",         # 의왕 (검증됨)
    "의왕역": "11B20609",         # 의왕 (검증됨)
    "부산항역(신항)": "11H20201",  # 부산 (검증됨)
    "부산진역": "11H20201",        # 부산 (검증됨)
    "천안역": "11C20301",         # 천안 (검증됨)
    "순천역": "11F20603",         # 순천 (검증됨)
    "포항역": "11H10201",         # 포항 (검증됨)
}
REG_CODE_MID: dict[str, str] = {
    "오봉역": "11B20609",         # 의왕 (검증됨 — 중기 목록에도 리프코드 존재)
    "의왕역": "11B20609",
    "부산항역(신항)": "11H20201",  # 부산 (검증됨)
    "부산진역": "11H20201",
    "천안역": "11C20301",         # 천안 (검증됨)
    "순천역": "11F20603",         # 순천 (검증됨)
    "포항역": "11H10201",         # 포항 (검증됨)
}

AUTH_KEY = ""  # TODO: Streamlit secrets 등으로 주입 (KMA_AUTH_KEY)

# ── 코드 해석표 ──
# 단기예보(fct_afs_dl)는 DB 접두, 중기예보(fct_afs_wl)는 WB 접두로 네임스페이스가
# 다르다 — 같은 의미(맑음/흐림 등)라도 코드 문자열 자체가 다르므로 각각 따로 둔다.
SKY_SHRT = {"DB01": "맑음", "DB02": "구름조금", "DB03": "구름많음", "DB04": "흐림"}
PREP_SHRT = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "눈/비"}
SKY_MID = {"WB01": "맑음", "WB02": "구름조금", "WB03": "구름많음", "WB04": "흐림"}
PRE_MID = {"WB00": "없음", "WB09": "비", "WB11": "비/눈", "WB13": "눈/비", "WB12": "눈"}


def _parse_disp1(text: str) -> list[list[str]]:
    """disp=1(구분자 콤마) 응답 텍스트 -> 데이터 행 리스트.

    형식: '#'로 시작하는 헤더/구분 줄을 건너뛰고, 각 데이터 줄은
    'v1,v2,...,vN,=' 형태(끝에 '=' 마커)라 마지막 빈 항목을 제거한다.
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if parts and parts[-1] == "=":
            parts = parts[:-1]
        rows.append(parts)
    return rows


def _fetch(endpoint: str, **params) -> str:
    params["authKey"] = AUTH_KEY
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=10)
    # ⚠️ 이 API들은 UTF-8이 아니라 CP949(EUC-KR)로 응답한다 — utf-8로 강제
    # 디코딩하면 지역명 등 한글이 전부 깨진다(코드값 자체는 ASCII라 안 깨져서
    # 파싱 자체는 되지만, 지역코드를 이름으로 찾는 작업이 불가능해진다).
    # 실제로 이 문제 때문에 지역코드 검색 스크립트가 계속 "후보 없음"만
    # 반환했었다 — 원인 확인 후 cp949로 수정.
    resp.encoding = "cp949"
    resp.raise_for_status()
    return resp.text


def fetch_short_term(reg: str) -> list[dict]:
    """단기 육상예보(fct_afs_dl) — 오늘~+5일, 12시간 간격 내외.

    반환 필드: TM_EF(발효시각 datetime), TA(기온), ST(강수확률%),
    SKY_desc, PREP_desc
    """
    if not reg or not AUTH_KEY:
        return []
    text = _fetch("fct_afs_dl.php", reg=reg, tmfc=0, disp=1, help=0)
    rows = _parse_disp1(text)
    results = []
    for r in rows:
        if len(r) < 16:
            continue
        # REG_ID,TM_FC,TM_EF,MOD,NE,STN,C,MAN_ID,MAN_FC,W1,T,W2,TA,ST,SKY,PREP,WF
        try:
            tm_ef = datetime.strptime(r[2], "%Y%m%d%H%M")
            results.append({
                "tm_ef": tm_ef,
                "ta": float(r[12]),
                "st": int(r[13]),
                "sky_desc": SKY_SHRT.get(r[14], r[14]),
                "prep_desc": PREP_SHRT.get(r[15], r[15]),
            })
        except (ValueError, IndexError):
            continue
    return results


def fetch_mid_term_land(reg: str) -> list[dict]:
    """중기 육상예보(fct_afs_wl) — +3~+10일, 오전/오후(3~7일)+일단위(8~10일).

    반환 필드: TM_EF(발효시각 datetime), SKY_desc, PRE_desc, RN_ST(강수확률%)
    """
    if not reg or not AUTH_KEY:
        return []
    text = _fetch("fct_afs_wl.php", reg=reg, tmfc=0, disp=1, help=0)
    rows = _parse_disp1(text)
    results = []
    for r in rows:
        if len(r) < 11:
            continue
        # REG_ID,TM_FC,TM_EF,MOD,STN,C,SKY,PRE,CONF,WF,RN_ST
        try:
            tm_ef = datetime.strptime(r[2], "%Y%m%d%H%M")
            results.append({
                "tm_ef": tm_ef,
                "sky_desc": SKY_MID.get(r[6], r[6]),
                "pre_desc": PRE_MID.get(r[7], r[7]),
                "rn_st": int(r[10]) if r[10].strip().isdigit() else None,
            })
        except (ValueError, IndexError):
            continue
    return results


def fetch_mid_term_temp(reg: str) -> list[dict]:
    """중기 기온예보(fct_afs_wc) — +3~+10일, 일별 최저/최고기온.

    반환 필드: TM_EF(날짜, datetime), MIN, MAX
    """
    if not reg or not AUTH_KEY:
        return []
    text = _fetch("fct_afs_wc.php", reg=reg, tmfc=0, disp=1, help=0)
    rows = _parse_disp1(text)
    results = []
    for r in rows:
        if len(r) < 8:
            continue
        # REG_ID,TM_FC,TM_EF,MOD,STN,C,MIN,MAX,MIN_L,MIN_H,MAX_L,MAX_H
        try:
            tm_ef = datetime.strptime(r[2], "%Y%m%d%H%M")
            results.append({"tm_ef": tm_ef, "min_ta": int(r[6]), "max_ta": int(r[7])})
        except (ValueError, IndexError):
            continue
    return results


def get_weather_summary(origin_node_name: str, departure_dt: datetime) -> str | None:
    """화물역 이름 + 희망출발시각으로 사람이 읽을 요약 문장 생성.

    단기(오늘~+5일)를 우선 쓰고, 없으면 중기(+3~+10일)로 폴백한다.
    이 함수는 delay_risk.py의 모델 입력이 아니라, gemini_assist의
    설명 프롬프트에 '참고용 정성 신호'로만 넘길 문자열을 만든다.
    reg 코드가 비어있거나(REG_CODE_SHRT/MID TODO) 조회 결과가 없으면
    None을 반환 — 호출부에서 "날씨 정보 없음"으로 처리해야 한다.
    """
    now = datetime.now()
    days_ahead = (departure_dt.date() - now.date()).days

    if 0 <= days_ahead <= 4:
        reg = REG_CODE_SHRT.get(origin_node_name, "")
        rows = fetch_short_term(reg)
        if rows:
            closest = min(rows, key=lambda x: abs((x["tm_ef"] - departure_dt).total_seconds()))
            return (
                f"{closest['tm_ef'].strftime('%m/%d %H:%M')} 기준 단기예보 — "
                f"기온 {closest['ta']}°C, 강수확률 {closest['st']}%, "
                f"{closest['sky_desc']}, 강수형태 {closest['prep_desc']}"
            )

    if 3 <= days_ahead <= 10:
        reg_mid = REG_CODE_MID.get(origin_node_name, "")
        land = fetch_mid_term_land(reg_mid)
        temp = fetch_mid_term_temp(REG_CODE_SHRT.get(origin_node_name, "") or reg_mid)
        if land:
            closest = min(land, key=lambda x: abs((x["tm_ef"] - departure_dt).total_seconds()))
            parts = [
                f"{closest['tm_ef'].strftime('%m/%d')} 기준 중기예보 — "
                f"강수확률 {closest['rn_st']}%, {closest['sky_desc']}, 강수형태 {closest['pre_desc']}"
            ]
            if temp:
                same_day = [t for t in temp if t["tm_ef"].date() == closest["tm_ef"].date()]
                if same_day:
                    t = same_day[0]
                    parts.append(f"(예상 기온 {t['min_ta']}~{t['max_ta']}°C)")
            return " ".join(parts)

    return None
