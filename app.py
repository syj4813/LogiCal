# -*- coding: utf-8 -*-
"""RailLTL 통합 Streamlit 라우터.

화주 견적/추적, 트럭기사 대시보드, 관제센터, 화차 배치 추천을
하나의 앱에서 이동할 수 있도록 통합합니다.
"""

import streamlit as st

st.set_page_config(page_title="RailLTL", page_icon="🚆", layout="wide")

pages = [
    st.Page("pages/page0_home.py", title="화주 견적 비교", icon="🚚", default=True),
    st.Page("pages/page1_shipper_tracking.py", title="화주용 실시간추적", icon="📦"),
    st.Page("pages/page2_driver_app.py", title="트럭기사 대시보드", icon="🚛"),
    st.Page("pages/page3_control_tower.py", title="관제센터 대시보드", icon="🛰️"),
    st.Page("pages/page4_car_assignment.py", title="화차 배치 추천", icon="🚃"),
]

nav = st.navigation(pages)
nav.run()
