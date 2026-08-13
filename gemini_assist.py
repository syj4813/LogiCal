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
from google.genai import types

import cargo
import rail_freight_nodes
import road_cost
from tz_utils import now_kst

GEMINI_API_KEY = ""  # TODO: Streamlit secrets 등으로 주입 (Agent Platform Model APIs 키)
GEMINI_MODEL = "gemini-3.5-flash"

# ⚠️ 클라이언트를 함수 호출마다 새로 만들지 않고 모듈 레벨에서 하나만 캐싱해서
#    재사용한다. start_chat()이 지역변수 client로 만들어서 Chat 객체만
#    반환했더니, 다음 Streamlit rerun 때 그 지역변수가 가비지컬렉션되면서
#    내부 httpx 클라이언트까지 닫혀 "Cannot send a request, as the client
#    has been closed" 오류가 났던 버그가 있었다 — 모듈이 강한 참조를 계속
#    들고 있으면 이 문제가 사라진다.
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, api_key=GEMINI_API_KEY)
    return _client


def _call_gemini(prompt: str) -> str:
    response = _get_client().models.generate_content(model=GEMINI_MODEL, contents=prompt)
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


def explain_delay_risk(probability: float, level: str, signals: dict) -> str:
    """delay_risk.py가 계산한 실제 지연위험 확률(LightGBM)을 화주에게 보여줄
    한두 문장으로, 어떤 요인이 그 예측에 영향을 줬는지 설명.

    ⚠️ 확률/등급 자체는 이미 delay_risk.py의 학습된 모델이 계산한 값이고,
    Gemini는 그 수치와 근거 신호(요일/품목/결합배송여부 등)를 문장으로
    풀어 설명하는 역할만 한다 — 다른 숫자를 새로 만들어내지 않는다
    (계산은 코드/모델, 설명은 AI 원칙, explain_carbon_savings와 동일).

    "어떤 요인 때문인지"를 그럴듯하게 지어내지 않도록, 학습 시 실제로
    확인된 피처 중요도 순위(data/README_delay_risk.md 근거)와 실측
    요일별 운휴율 통계를 프롬프트에 같이 줘서, 그 안에서만 요인을
    짚게 한다.
    """
    feature_importance_note = (
        "학습 시 확인된 피처 중요도 순위(1위가 가장 영향 큼): "
        "1위 공차회송여부, 2위 운행거리, 3위 화물중량, 4위 요일, 5위 수송품목."
    )
    weekday_base_rate_note = (
        "실측 요일별 운휴율(2024년 7월 기준): 월 17.1%, 화 17.7%, 수 16.6%, "
        "목 15.9%, 금 15.7%, 토 24.3%, 일 28.8% — 주말일수록 기준 위험이 높음."
    )
    prompt = f"""아래는 화물열차 지연(운휴) 위험도를 학습된 모델이 예측한
결과입니다. 화주에게 "왜 이 확률이 나왔는지" 요인 중심으로 한두 문장을
존댓말로 작성하세요. 주어진 숫자·신호·통계 외의 다른 수치·원인을 새로
만들어내지 마세요. 아래 피처 중요도 순위를 참고해서, 근거 신호 중 실제로
중요도가 높은 항목(예: 요일, 결합배송여부)을 우선 언급하세요.

{feature_importance_note}
{weekday_base_rate_note}

지연위험 확률: {probability * 100:.1f}%
위험 등급: {level}
이 화물의 근거 신호: {json.dumps(signals, ensure_ascii=False)}

문장만 출력하세요."""
    return _call_gemini(prompt)


def start_chat(context: dict | None = None):
    """화주 상담용 대화 세션을 새로 시작한다.

    context: 현재 화면에 표시된 견적 결과(요금/시간/탄소배출량 등)를
    담은 dict. 넘기면 챗봇이 그 숫자 범위 안에서만 답하도록 시스템
    프롬프트에 그대로 박아넣는다. None이면 "아직 견적을 조회하지
    않았다"고 안내하는 챗봇으로 시작한다.

    반환값(Chat 세션 객체)은 Streamlit이라면 st.session_state에 담아
    재실행(rerun) 사이에도 유지해야 한다 — 매번 새로 만들면 대화
    맥락이 끊긴다.

    ⚠️ 이전 버전은 "현재 견적 정보에 없는 건 전부 모른다고 답하라"는
    규칙이 너무 엄격해서, "위험물이면 요금이 어떻게 바뀌어?"처럼 이
    앱이 실제로는 결정론적 규칙(cargo.py)으로 계산할 수 있는 질문까지
    거절해버렸다. 그래서 견적 수치와 별개로, 이 앱이 실제로 쓰는
    "일반 요금 규칙"(카테고리별 할증률, 철도 톤·km 단가, 트럭 차급별
    요금체계)을 항상 시스템 프롬프트에 같이 넣어, 그 규칙 안에서는
    추론/설명을 할 수 있게 하되 다른 구간의 구체적 새 견적 금액을
    지어내는 것만 금지한다.
    """
    client = _get_client()

    general_rules = {
        "화물종류별_요금_할증률": {
            c.value: m for c, m in cargo.FARE_SURCHARGE_MULTIPLIER.items()
        },
        "철도_톤킬로미터당_운임_원": rail_freight_nodes.RAIL_TON_KM_RATE_WON,
        "철도_최저운임_적용톤수": rail_freight_nodes.MIN_BILLING_TON,
        "철도_상하차_취급수수료_원_편도당": rail_freight_nodes.RAIL_HANDLING_FEE_WON,
        "트럭_차급별_요금체계": [
            {"차급": t.label, "기본료_원": t.base_won, "km당_원": t.per_km_won, "톤당_원": t.per_ton_won}
            for t in road_cost.TRUCK_TIERS
        ],
    }
    rules_block = (
        "이 서비스가 실제로 쓰는 일반 요금 산정 규칙(모든 견적에 항상 적용됨):\n"
        f"{json.dumps(general_rules, ensure_ascii=False, indent=2)}\n\n"
        "예를 들어 철도는 '거리 × 톤·km단가 × 청구중량 + 상하차 취급수수료×2'로, "
        "트럭은 화물량에 맞는 차급의 '기본료 + 거리×km당단가 + 중량×톤당단가'로 "
        "계산되고, 화물종류 할증률은 이 금액에 곱해진다."
    )

    if context:
        system_instruction = (
            "당신은 코레일 화물 운송 견적 상담 챗봇입니다. 화주의 질문에 "
            "존댓말로 간결하게 답하세요.\n\n"
            f"{rules_block}\n\n"
            "규칙:\n"
            "1. '현재 견적 정보'의 구체적 금액이나 '일반 요금 규칙'에 없는 "
            "숫자를 새로 지어내지 마세요(예: 다른 구간의 정확한 원화 금액).\n"
            "2. 하지만 '일반 요금 규칙'을 근거로 한 설명·추론(예: 특정 화물종류면 "
            "할증이 몇 배 적용되는지, 왜 철도가 저렴한 경향이 있는지)은 적극적으로 "
            "답하세요 — 이건 모른다고 답할 필요 없습니다.\n"
            "3. 다른 구간의 정확한 견적처럼 위 두 정보 다 없는 질문을 받으면, "
            "모른다고 답하고 화면에서 직접 조회해보라고 안내하세요.\n"
            "4. 화물 운송·철도·물류와 무관한 질문에는 정중히 답변을 "
            "거절하고 주제를 안내하세요.\n\n"
            f"현재 견적 정보:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        )
    else:
        system_instruction = (
            "당신은 코레일 화물 운송 견적 상담 챗봇입니다. 화주의 질문에 "
            "존댓말로 간결하게 답하세요. 아직 견적을 조회하지 않은 상태입니다.\n\n"
            f"{rules_block}\n\n"
            "위 '일반 요금 규칙'에 대한 질문(예: 위험물 할증률, 톤·km 단가)은 "
            "그 규칙으로 바로 답하세요. 하지만 실제 견적(구체적 요금/시간)이 "
            "필요한 질문에는, 위 폼에서 출발지/도착지/화물정보를 입력하고 "
            "'비교하기'를 눌러 견적을 먼저 조회해달라고 안내하세요. 화물 운송·철도·"
            "물류와 무관한 질문에는 정중히 답변을 거절하세요."
        )

    return client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(system_instruction=system_instruction),
    )


def send_chat_message(chat, message: str) -> str:
    """진행 중인 대화 세션(start_chat이 반환한 객체)에 메시지를 보내고 답변을 받는다."""
    response = chat.send_message(message)
    return response.text
