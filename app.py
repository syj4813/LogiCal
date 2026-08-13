# -*- coding: utf-8 -*-
"""얇은 라우터.

⚠️ pages/ 폴더 파일명은 전부 영문 ASCII로 고정한다 (page0_home.py 등).
   한글 파일명은 macOS git의 유니코드 정규화(NFC/NFD)나 Windows 압축
   해제 과정에서 실제 디스크 파일명과 바이트 단위로 어긋나
   StreamlitPageNotFoundError를 반복적으로 일으켰던 이력이 있다.
   화면에 보이는 한글 라벨은 st.Page(title=...)로만 지정한다.
"""

import streamlit as st

st.set_page_config(page_title="철도 화물 통합운송 비교", page_icon="🚆", layout="wide")

pages = [
    st.Page("pages/page0_home.py", title="화주 견적 비교", icon="🚚", default=True),
    st.Page("pages/page1_shipper_tracking.py", title="화주용 실시간추적", icon="📦"),
]

nav = st.navigation(pages)
nav.run()
