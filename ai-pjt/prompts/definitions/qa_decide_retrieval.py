"""Prompt definition for qa.decide_retrieval."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.decide_retrieval",
    "qa",
    """[역할]

현재 질문에 승인된 참고자료가 필요한지 판단합니다. 답변은 작성하지 않습니다.

[판단 기준]

- 현재 유효한 사용자 정보나 화면 정보만으로 답할 수 있으면 need_retrieval=false입니다.
- 금융 개념, 제도, 제품 용어, 배경 또는 주의사항 설명에 승인 자료가 필요하면 need_retrieval=true입니다.
- 현재 제공되지 않은 공식 금액, 한도, 비율 또는 평균값을 새로 조회해야 하면 requests_new_official_value=true입니다.
- 참고자료는 사용자의 확인된 값을 바꾸는 용도로 사용하지 않습니다.

[출력]

need_retrieval과 requests_new_official_value boolean 필드만 반환합니다.""",
    (
        ("current_query", "현재 질문"),
        ("step_id", "현재 화면"),
        ("customer_info", "현재 유효한 사용자 정보"),
        ("bound_inputs", "확인된 선행 결과"),
    ),
    output_schema="retrieval_decision",
    risk="financial_explanation",
)
