# -*- coding: utf-8 -*-
"""
Gemini(Agent Platform express mode) 활용 — 역할을 의도적으로 제한한다.

  - 하지 않는 것: 경로 최적화, 통합 판정, 요금 계산 (전부 결정론적
    로직으로 처리 — 재현성과 설명가능성 확보 목적)
  - 하는 것: (1) 화주의 자연어 입력을 구조화된 필드로 파싱(+ 누락 항목
             자체 판단 및 재질문 문구 생성)
             (2) 계산된 비교 결과를 화주에게 설명하는 문장 생성
             (3) 화물 종류를 카테고리로 분류
             (4) 탄소 절감 수치를 체감형 문장으로 설명 (숫자는 코드가 계산)

인증 방식: "Agent Platform Model APIs" 키(AQ.로 시작하는 형식)를
공식 google-genai SDK의 express mode(`vertexai=True, api_key=...`)로
사용. 서비스 계정 JSON이나 프로젝트 ID 없이 API 키 하나로 인증되며,
Google Cloud 무료 크레딧을 그대로 소진할 수 있음.
"""

import json

from google import genai

from tz_utils import now_kst

GEMINI_API_KEY = ""  # TODO: Streamlit secrets 등으로 주입 (Agent Platform Model APIs 키)
GEMINI_MODEL = "gemini-3.5-flash"


def _call_gemini(prompt: str) -> str:
    client = genai.Client(vertexai=True, api_key=GEMINI_API_KEY)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text


def parse_free_text_order(text: str) -> dict:
    """자연어 입력 -> 구조화된 필드(JSON) 파싱 (단발성, 한 문장 통째로 입력받는 방식).

    필수 항목(출발지/도착지/중량)이 문장에서 파악되지 않으면 'missing_fields'에
    어떤 항목이 빠졌는지, 'clarification_message'에 화주에게 보여줄 안내
    문구를 함께 반환한다 — 화면에서는 이걸로 "다시 입력해 주세요" 재질문
    로직을 구현한다. 선택 항목(화물종류/날짜/시각)이 언급되지
    않았으면 'unset_optional_fields'에 목록으로 담아 반환 — 화면에서는
    이 항목들이 기본값으로 채워졌다는 걸 화주에게 알려주는 데 쓴다.
    """
    now = now_kst()
    today_str = now.date().isoformat()
    now_time_str = now.strftime("%H:%M")
    prompt = f"""오늘 날짜는 {today_str}, 지금 시각은 {now_time_str}입니다.
다음 화물 운송 요청 문장에서 정보를 추출해 JSON으로만 답하세요.
"내일", "다음주 화요일" 같은 상대적 표현은 오늘 날짜 기준으로 실제
날짜(YYYY-MM-DD)로 계산하고, "최대한 빨리"/"지금 바로" 같은 표현은
지금 시각을 desired_time으로 사용하세요.

필드:
- origin(출발지), destination(도착지): 필수. 문장에 없으면 null
- cargo_type(화물종류): 선택, 없으면 null
- weight_kg(중량, 숫자만): 필수. 문장에 없으면 null
- desired_date(YYYY-MM-DD): 선택, 없으면 null
- desired_time(HH:MM, 24시간제): 선택, 없으면 null
- missing_fields: 필수 항목(origin, destination, weight_kg) 중 이번
  문장에서 파악하지 못한 항목 이름의 리스트. 다 파악됐으면 빈 리스트.
- clarification_message: missing_fields가 있으면, 화주에게 존댓말로
  무엇을 더 알려달라고 요청하는 짧은 한두 문장. 없으면 null.
- unset_optional_fields: 선택 항목(cargo_type, desired_date,
  desired_time) 중 이번 문장에서 언급되지 않아 값이 null인 항목 이름의
  리스트. 다 언급됐으면 빈 리스트.

문장: "{text}"

JSON만 출력하세요. 다른 설명은 붙이지 마세요."""
    raw = _call_gemini(prompt)
    cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(cleaned)


CARGO_CATEGORIES = ["일반화물", "냉장·냉동", "위험물", "파손주의·고가품", "농산물·생물"]


def classify_cargo_category(cargo_type_text: str) -> str:
    """화물 종류 자연어 입력을 5개 카테고리 중 하나로 분류.

    키워드 매칭(cargo.classify_cargo_type)과 달리 목록에 없는 표현
    (예: "방사성물질")도 의미 기반으로 처리 가능. 단, LLM 특성상
    실행마다 결과가 미세하게 달라질 수 있어 재현성은 키워드 방식보다
    낮음 — 이 트레이드오프를 알고 쓰는 것.
    """
    prompt = f"""다음 화물 종류를 아래 카테고리 중 정확히 하나로 분류하세요.
카테고리: {", ".join(CARGO_CATEGORIES)}

화물 종류: "{cargo_type_text}"

카테고리 이름 하나만 정확히 출력하세요. 다른 설명은 붙이지 마세요."""
    raw = _call_gemini(prompt).strip()
    for category in CARGO_CATEGORIES:
        if category in raw:
            return category
    return "일반화물"  # 매칭 실패 시 보수적으로 일반화물 처리


def explain_comparison(comparison_rows: list[dict], consolidation_note: str) -> str:
    """계산된 비교 결과를 화주 친화적 문장으로 요약."""
    prompt = f"""아래는 화물 운송 수단별 비교 계산 결과입니다. 화주에게 보여줄
2~3문장의 친절한 요약을 존댓말로 작성하세요. 숫자를 새로 만들어내지 말고
주어진 데이터만 근거로 설명하세요.

비교 데이터: {json.dumps(comparison_rows, ensure_ascii=False)}
철도 통합운송 판정 메모: {consolidation_note}"""
    return _call_gemini(prompt)


def explain_carbon_savings(gwp_savings_kg: float, mileage: int, tree_equivalent: float) -> str:
    """탄소 절감 수치를 체감되는 한두 문장으로 풀어서 설명.

    ⚠️ 숫자는 전부 emission.py에서 미리 계산해 인자로 넘겨받는다 —
    Gemini가 임의로 환산 수치를 만들어내지 않도록, 주어진 숫자를
    문장으로 표현하는 역할만 맡긴다 (계산은 코드, 설명은 AI 원칙).
    """
    prompt = f"""아래 수치를 바탕으로 화주에게 보여줄 탄소 절감 효과를
한두 문장으로, 친근하고 체감되게 존댓말로 설명하세요. 아래 제시된
숫자 외의 다른 수치나 비유는 절대 새로 만들어내지 마세요.

- 트럭 대비 절감량: {gwp_savings_kg:.1f} kgCO2eq
- 적립 예상 탄소 마일리지: {mileage}P
- 나무 {tree_equivalent}그루의 연간 CO2 흡수량과 비슷함 (참고용 근사 비유)

문장만 출력하세요."""
    return _call_gemini(prompt)


def assess_delay_risk(signals: dict) -> dict:
    """예약 1건의 지연 위험을 낮음/보통/높음 등급 + 근거 한 문장으로 평가.

    ⚠️ 이건 학습된 예측 모델이 아니다. 실제 지연 이력 데이터가 없어서
    (freight_train_schedule.csv는 정적 스냅샷이지 운행 결과 기록이 아님)
    통계적 예측 모델을 만들 근거 자체가 없다. 대신 이미 계산된 신호
    (시각표 매칭 여부, 결합 배송 여부, 요일 등)를 LLM에게 주고 정성
    평가를 요청하는 방식이다 — 같은 입력이라도 호출마다 등급이 달라질
    수 있어(재현성 낮음) 참고용 표시로만 써야 하고, "AI가 예측했다"보다는
    "AI가 신호를 근거로 평가했다" 쪽에 가깝다.
    """
    prompt = f"""아래는 철도 통합운송 예약 1건의 지연 위험을 판단하기 위한
신호입니다. 신호만 근거로 위험 등급(낮음/보통/높음) 하나와, 그 이유를
한 문장(존댓말)으로 평가하세요. 신호에 없는 정보를 추측해서 만들어내지
마세요.

신호: {json.dumps(signals, ensure_ascii=False)}

다음 JSON 형식으로만 답하세요: {{"level": "낮음|보통|높음", "reason": "..."}}"""
    raw = _call_gemini(prompt).strip().removeprefix("```json").removesuffix("```").strip()
    try:
        result = json.loads(raw)
        if result.get("level") not in ("낮음", "보통", "높음"):
            raise ValueError("unexpected level")
        return result
    except Exception:
        return {"level": "판정불가", "reason": "AI 응답을 해석하지 못했습니다."}


def explain_match(score: float, factors: dict) -> str:
    """복귀 화물 매칭 후보의 결합적합도 점수를 트럭기사에게 보여줄 한 문장으로 설명.

    ⚠️ 점수 자체는 여전히 명시적 규칙식(consolidation.py의 결합 기준)이
    계산한 값이고, Gemini는 그 근거를 자연어로 풀어 설명하는 역할만
    한다 — 점수나 다른 숫자를 새로 만들어내지 않는다 (계산은 코드,
    설명은 AI 원칙, explain_carbon_savings와 동일한 방식).
    """
    prompt = f"""아래는 트럭기사에게 보여줄 복귀 화물 매칭 후보의 결합적합도
점수와 근거 신호입니다. 왜 이 점수가 나왔는지 기사님께 보여줄 한
문장(존댓말)으로 설명하세요. 주어진 숫자 외의 다른 수치를 새로
만들어내지 마세요.

결합적합도 점수: {score}
근거 신호: {json.dumps(factors, ensure_ascii=False)}

문장만 출력하세요."""
    return _call_gemini(prompt)
