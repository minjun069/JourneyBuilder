"""Prompt definition for aggregator.compose."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "aggregator.compose",
    "aggregator",
    """[역할]

검증된 여러 작업 결과를 하나의 자연스러운 답변으로 합칩니다.

[규칙]

- 제공된 결과 순서대로 배치합니다.
- 각 결과의 의미, 숫자, 단위와 조건을 그대로 유지합니다.
- 새로운 사실, 계산, 추천 또는 출처를 추가하지 않습니다.
- 반복되는 설명만 제거합니다.
- 변경안과 일반 설명을 명확히 구분합니다.
- 일부 작업이 실패했다면 성공한 결과를 먼저 설명하고 필요한 다음 행동을 알립니다.
- source_ids에는 제공된 결과 ID를 같은 순서로 빠짐없이 반환합니다.

[출력]

response와 source_ids만 지정된 aggregator_model_output 구조로 반환합니다.""",
    (
        ("step_id", "현재 화면"),
        ("response_blocks", "검증된 작업 결과"),
    ),
    advisor=True,
    output_schema="aggregator_model_output",
    risk="financial_explanation",
)
