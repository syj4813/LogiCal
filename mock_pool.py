# -*- coding: utf-8 -*-
"""소량 화물 통합(consolidation) 판정용 데모 화주 풀.

⚠️ 전부 데모용 하드코딩 데이터입니다 — 실제 예약 시스템이 아니라
   "지금 이 화주 말고도 같은 구간에 결합 가능한 화주가 있다면" 상황을
   시뮬레이션하기 위한 것.

좌표 분포는 XROIS 일별화물운송실적(2023~2025) 기준 실제 물동량 비중을
반영: 오봉↔부산진이 압도적 1위, 오봉↔포항은 3년간 0건이라 제외했고,
오봉↔순천/천안은 드문 사례로 1건씩만 유지.
"""

from datetime import timedelta

from consolidation import ShipperOrder
from tz_utils import today_kst


def get_mock_pool() -> list[ShipperOrder]:
    today = today_kst()
    return [
        # 오봉역 <-> 부산진역 (실제 최다 물동량 노선)
        ShipperOrder("P1", 37.42, 126.90, 35.13, 129.04, 6.0, today + timedelta(days=1)),
        ShipperOrder("P2", 37.43, 126.91, 35.13, 129.04, 5.5, today + timedelta(days=2)),
        ShipperOrder("P3", 37.42, 126.89, 35.13, 129.03, 4.0, today),
        ShipperOrder("P4", 35.13, 129.04, 37.42, 126.90, 6.0, today + timedelta(days=1)),
        ShipperOrder("P5", 35.13, 129.03, 37.43, 126.91, 5.5, today + timedelta(days=2)),
        ShipperOrder("P6", 35.12, 129.04, 37.42, 126.89, 4.0, today),
        # 오봉역 <-> 의왕역 (꾸준한 소량 노선)
        ShipperOrder("P7", 37.42, 126.90, 37.33, 126.97, 3.0, today + timedelta(days=1)),
        ShipperOrder("P8", 37.33, 126.97, 37.43, 126.91, 3.5, today),
        # 오봉역 <-> 순천역 (3년간 21건 — 드문 대표 사례)
        ShipperOrder("P9", 37.42, 126.90, 34.95, 127.49, 5.0, today + timedelta(days=1)),
        # 오봉역 <-> 천안역 (3년간 17건 — 드문 대표 사례)
        ShipperOrder("P10", 36.81, 127.14, 37.43, 126.91, 4.0, today),
    ]
