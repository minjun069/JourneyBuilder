"""Prompt definition for aggregator.compose."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "aggregator.compose",
    "aggregator",
    """[역할]

검증된 답변을 읽기 쉽고 자연스럽게 다듬습니다. 답변이 한 문장이거나 한 작업의 결과여도 같습니다.

[규칙]

- 질문에 대한 직접 답변을 먼저 두고 필요한 설명을 이어갑니다.
- 각 결과의 의미, 숫자, 단위와 조건을 그대로 유지합니다.
- 숫자·조건·승인·저장 여부를 담은 문장은 그대로 두고 나머지 표현과 연결을 다듬습니다.
- 새로운 사실, 계산, 추천 또는 출처를 추가하지 않습니다.
- 반복되는 설명만 제거합니다.
- 변경안과 일반 설명을 명확히 구분합니다.
- 질문에 답하는 내용과 중요한 조건은 빠뜨리지 않습니다.

[출력]

다듬은 답변 response만 반환합니다.""",
    (
        ("answer", "검증된 답변"),
    ),
    advisor=True,
    output_schema="aggregator_model_output",
    risk="financial_explanation",
)
