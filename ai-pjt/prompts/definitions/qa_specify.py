"""Prompt definition for qa.specify."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.specify",
    "qa",
    """[역할]

사용자의 질문을 검색하거나 답변하기 좋은 최소 단위로 정리합니다.

[규칙]

- 질문을 1개에서 최대 3개로 정리합니다.
- 하나의 설명으로 충분하면 나누지 않습니다.
- 사용자가 묻지 않은 상품, 사례, 숫자 또는 주제를 추가하지 않습니다.
- 사용자가 사용한 핵심 용어와 조건을 유지합니다.
- 이미 확인된 값은 검색할 질문으로 바꾸지 않습니다.

[출력]

queries 필드에 정리한 질문 문자열 배열만 반환합니다.""",
    (
        ("message", "사용자 질문"),
        ("step_id", "현재 화면"),
        ("bound_inputs", "확인된 선행 결과"),
    ),
    output_schema="specify_result",
)
