"""Prompt definition for qa.answer."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.answer",
    "qa",
    """[할 일]

질문 전체에 하나의 답변을 작성합니다.

[방법]

- 사용자 정보와 확인된 결과는 그대로 설명합니다. 다시 계산하지 않습니다.
- 일반 설명은 각 질문에 연결된 참고자료가 뒷받침하는 범위에서 답합니다.
- 새 공식 금액이나 한도는 확인된 결과가 있을 때만 말합니다.
- 자료가 부족한 부분은 확인할 수 없다고 짧게 알리고, 답할 수 있는 부분은 답합니다.
- 보완할 점이 있으면 같은 정보와 근거 안에서 고칩니다.

[결과]

질문에 직접 답하는 문장을 먼저 쓰고 필요한 설명만 덧붙입니다.
짧은 질문에는 짧게 답하고, 여러 질문은 원래 순서대로 빠짐없이 답합니다.""",
    (
        ("question", "사용자 질문"),
        ("customer_info", "관련 사용자 정보"),
        ("bindings", "확인된 결과"),
        ("evidence", "질문별 참고자료"),
        ("feedback", "보완할 점"),
    ),
    advisor=True,
    risk="financial_explanation",
    version="4.0.0",
)
