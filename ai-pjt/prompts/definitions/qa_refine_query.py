"""Prompt definition for qa.refine_query."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.refine_query",
    "qa",
    """[역할]

현재 검색어를 같은 의미의 더 정확한 검색어로 한 번만 고칩니다.

[규칙]

- 원래 질문의 주제와 조건을 유지합니다.
- 관련 상품이나 새로운 사례로 범위를 넓히지 않습니다.
- 동의어, 정식 명칭 또는 더 구체적인 핵심어를 사용합니다.
- 답변은 작성하지 않습니다.

[출력]

query 필드에 수정된 검색어 하나만 반환합니다.""",
    (
        ("question", "원래 질문"),
        ("current_query", "현재 검색어"),
        ("reason", "검색 보완 이유"),
    ),
    output_schema="refine_result",
)
