# -*- coding: utf-8 -*-
"""
한국 표준시(KST, UTC+9) 기준 날짜/시각 헬퍼.

⚠️ Streamlit Cloud 등 배포 서버는 기본적으로 UTC로 동작합니다.
   date.today()/datetime.now()를 그냥 쓰면 서버 시간대에 따라
   날짜가 최대 하루 어긋날 수 있습니다 (특히 한국 자정 전후,
   UTC 기준 오후 3시~다음날 오전 사이). 화물열차 요일 매칭처럼
   날짜에 민감한 로직이 있어 반드시 KST로 고정해야 합니다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """타임존 정보가 붙은 현재 KST 시각."""
    return datetime.now(KST)


def now_kst_naive() -> datetime:
    """타임존 정보 없는(naive) 현재 KST 시각.

    ⚠️ 이 리포에서 예약시각/희망출발시각/도착예정시각 등은 전부
    tzinfo가 없는 naive datetime으로 저장돼 있다(datetime.combine이나
    naive datetime.now() 결과를 그대로 씀). 이 naive 값들과 "지금"을
    비교하려고 무심코 raw datetime.now()를 쓰면, 서버가 UTC로 도는
    Streamlit Cloud에서는 실제 KST보다 9시간 늦은 시각과 비교하게 돼
    "이미 지난 ETA인데도 아직 안 지난 것처럼" 보이는 버그가 생긴다.
    now_kst()는 tzinfo가 붙어있어서 naive 값과 그냥 뺄셈하면 TypeError가
    나므로, 기존 naive 값들과 같은 형태로 맞춘 이 함수를 대신 쓴다.
    """
    return now_kst().replace(tzinfo=None)


def today_kst():
    """현재 KST 기준 날짜(date)."""
    return now_kst().date()
