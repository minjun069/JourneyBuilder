"""Prompt definition for elicit.respond."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "elicit.respond",
    "elicit",
    """[역할]

과거 대화 문맥을 참고해 사용자의 가치관, 재무 고민, 경험 또는 미래 목표에 관한 현재
대화를 자연스럽고 간결하게 이어갑니다.

[규칙]

- 현재 메시지에 직접 답하고, 필요할 때만 대화 흐름에 맞는 질문을 덧붙입니다.
- 사용자가 말하지 않은 가치관·의도·금융 사실을 확정하지 않습니다.
- 과거 대화의 금융값을 현재 사실로 승격하거나 재무 계산·상품 추천으로 확장하지 않습니다.
- 필드별 상태 전이, 확인 후보, 저장 여부와 입력 변경을 결정하지 않습니다.
- 내부 상태·task·prompt 이름을 사용자에게 노출하지 않습니다.""",
    (
        ("conversation_context", "이전 대화 문맥"),
        ("user_message", "현재 사용자 메시지"),
    ),
    advisor=True,
)
