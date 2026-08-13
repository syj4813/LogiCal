# -*- coding: utf-8 -*-
"""얇은 라우터.

⚠️ pages/ 폴더 파일명은 전부 영문 ASCII로 고정한다 (page0_home.py 등).
   한글 파일명은 macOS git의 유니코드 정규화(NFC/NFD)나 Windows 압축
   해제 과정에서 실제 디스크 파일명과 바이트 단위로 어긋나
   StreamlitPageNotFoundError를 반복적으로 일으켰던 이력이 있다.
   화면에 보이는 한글 라벨은 st.Page(title=...)로만 지정한다.
"""

import streamlit as st

st.set_page_config(page_title="RailLTL", page_icon="🚆", layout="wide")

with st.sidebar:
    st.markdown(
        """
        <div style="padding: 4px 0 14px 0;">
          <span style="font-size:1.4rem; font-weight:800; color:#0F6E4F;">🚆 RailLTL</span><br/>
          <span style="font-size:0.8rem; color:#5A6B63;">AI 기반 철도 결합 운송 플랫폼</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

pages = {
    "화주용": [
        st.Page("pages/page0_home.py", title="견적 비교", icon="🚚", default=True),
        st.Page("pages/page1_shipper_tracking.py", title="실시간추적", icon="📦"),
    ],
    "트럭기사용": [
        st.Page("pages/page2_driver_app.py", title="기사 대시보드", icon="🚛"),
    ],
    "코레일용": [
        st.Page("pages/page3_control_tower.py", title="관제센터", icon="🛰️"),
        st.Page("pages/page4_car_assignment.py", title="화차 배치 추천", icon="🚃"),
    ]
}

nav = st.navigation(pages)
nav.run()
