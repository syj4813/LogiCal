# -*- coding: utf-8 -*-
"""
⚠️ 임시 디버그 페이지 — 기상청 API 연동 확인용. 확인 끝나면 이 파일과
app.py의 등록 줄을 삭제하세요.

배포 환경(Streamlit Cloud)에서 실제로 무슨 일이 일어나는지 예외를
숨기지 않고 그대로 화면에 보여준다 — 로컬 anaconda 환경과 Cloud 환경이
네트워크 제약 등으로 다르게 동작할 수 있어서, 실제 배포 위치에서
직접 확인하는 게 가장 확실하다.
"""

from datetime import datetime, timedelta

import requests
import streamlit as st

import weather

st.title("🔧 기상청 API 디버그 (임시)")

try:
    weather.AUTH_KEY = st.secrets.get("KMA_AUTH_KEY", "")
except Exception as e:
    weather.AUTH_KEY = ""
    st.error(f"secrets 읽기 자체가 실패했습니다: {e}")

st.write(f"**AUTH_KEY 설정 여부**: {bool(weather.AUTH_KEY)}")
st.write(f"**AUTH_KEY 길이**: {len(weather.AUTH_KEY)}자")
if weather.AUTH_KEY:
    st.write(f"**AUTH_KEY 앞 4자**: `{weather.AUTH_KEY[:4]}...`")

st.divider()
st.subheader("1) 원시 HTTP 요청 (네트워크/방화벽 문제 확인)")
try:
    test_url = (
        "https://apihub.kma.go.kr/api/typ01/url/fct_afs_dl.php"
        f"?reg=11B20609&tmfc=0&disp=1&help=0&authKey={weather.AUTH_KEY}"
    )
    resp = requests.get(test_url, timeout=10)
    resp.encoding = "cp949"
    st.write(f"HTTP 상태코드: {resp.status_code}")
    st.code(resp.text[:1500])
except Exception as e:
    st.error("원시 요청 자체가 실패했습니다 (Cloud 환경 네트워크 제약일 수 있음):")
    st.exception(e)

st.divider()
st.subheader("2) weather.fetch_short_term() 직접 호출")
try:
    rows = weather.fetch_short_term(weather.REG_CODE_SHRT.get("오봉역", ""))
    st.write(f"반환된 행 개수: {len(rows)}")
    st.write(rows[:5])
except Exception as e:
    st.error("fetch_short_term에서 예외 발생:")
    st.exception(e)

st.divider()
st.subheader("3) weather.get_weather_summary() 최종 호출 (7개 역 전부)")
departure = datetime.now() + timedelta(hours=6)
st.write(f"테스트 출발시각: {departure}")
for station in weather.REG_CODE_SHRT:
    st.write(f"**{station}** (reg={weather.REG_CODE_SHRT.get(station)})")
    try:
        summary = weather.get_weather_summary(station, departure)
        st.write(f"→ {summary!r}")
    except Exception as e:
        st.error(f"{station} 에서 예외 발생:")
        st.exception(e)
