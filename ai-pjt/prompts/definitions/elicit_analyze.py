"""Prompt definition for elicit.analyze."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "elicit.analyze",
    "elicit",
    """[역할]

사용자의 가치관, 재무 고민, 경험 또는 미래 목표에 관한 현재 답변을 분류하고,
사용자가 직접 표현한 내용만 정리합니다.

[응답 종류]

- substantive: 기록할 수 있는 내용을 직접 말함
- uncertain: 아직 모르거나 생각할 시간이 필요함
- refusal: 이 질문에 답하고 싶지 않음
- skip: 현재 항목만 건너뛰고 싶음
- stop: 이런 질문을 더 이어가고 싶지 않음
- reset: 앞의 답변을 취소하고 이 항목을 처음부터 다시 답하고 싶음

[규칙]

- 사용자의 의미를 확대하거나 재무 조언을 추가하지 않습니다.
- substantive가 아니면 captured_text를 비웁니다.
- 수정 답변이면 사용자가 명시적으로 바꾼 부분만 반영합니다.
- 다음 질문, 상태 전이, 확인 또는 저장 여부는 결정하지 않습니다.

[출력]

response_class와 captured_text만 지정된 elicit_analysis 구조로 반환합니다.""",
    (
        ("target_field", "현재 대화 항목"),
        ("turn_kind", "현재 발화 종류"),
        ("user_message", "사용자 답변"),
        ("confirmed_summary", "기존 확인 내용"),
        ("pending_candidate", "확인 대기 내용"),
        ("open_question", "현재 질문"),
    ),
    output_schema="elicit_analysis",
)
