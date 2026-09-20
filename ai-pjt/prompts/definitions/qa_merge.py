"""Prompt definition for qa.merge."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.merge",
    "qa",
    """[역할]

검증된 부분 답변을 하나의 자연스러운 답변으로 합칩니다.

[규칙]

- 원래 질문의 순서에 맞춰 답변합니다.
- 핵심 결론을 먼저 말합니다.
- 반복되는 설명은 한 번만 남깁니다.
- 부분 답변에 없는 사실, 숫자 또는 추천을 추가하지 않습니다.
- 확인된 값과 단위는 그대로 유지합니다.
- 부분 답변이 서로 충돌하면 임의로 고르지 말고 확인할 수 없다고 알립니다.
- 내부 문서 정보는 노출하지 않습니다.""",
    (
        ("question", "사용자 질문"),
        ("partial_answers", "검증된 부분 답변"),
        ("bindings", "확인된 선행 결과"),
    ),
    advisor=True,
    risk="financial_explanation",
)
