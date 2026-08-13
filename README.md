# Freight — 소량 화물 트럭 vs 철도 통합운송 비교

## 실행
```
pip install -r requirements.txt
streamlit run app.py
```

## API 키 (선택)
`.streamlit/secrets.toml` 파일에 아래 키를 등록하면 실제 지도 API 기반
거리/시간을 쓰고, 없으면 자동으로 직선거리 기반 추정치로 폴백합니다
(앱이 죽지 않음).
```toml
KAKAO_REST_API_KEY = "..."
GOOGLE_MAPS_API_KEY = "..."
```

## 진행 상태 (사다리)
- [x] 1단계 — 화주 견적비교 계산기 (트럭 vs 철도, 7개 화물역, 요금/배출량)
- [ ] 2단계 — Gemini 자유입력 파싱
- [ ] 3단계 — shared_store + 예약확정 + 실시간추적
- [ ] 4단계 — 트럭기사앱/관제센터/화차배치

## 구조
- `rail_freight_nodes.py`, `emission.py`, `cargo.py`, `road_cost.py`,
  `rail_cost.py`, `rail_schedule.py`, `intermodal.py`, `consolidation.py`,
  `tz_utils.py`, `geocode.py` — 백엔드 로직 (프레임워크 독립적)
- `data/mock_pool.py` — 소량화물 결합 판정용 데모 화주 풀
- `pages/page0_home.py` — 화주 견적 비교 화면
- `app.py` — `st.navigation()` 기반 얇은 라우터 (pages/ 파일명은 전부 영문 ASCII)
