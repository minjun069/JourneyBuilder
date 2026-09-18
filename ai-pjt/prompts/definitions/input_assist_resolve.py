"""Prompt definition for input_assist.resolve."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.resolve",
    "input_assist",
    """[역할]

사용자 표현에서 현재 항목에 입력할 값 하나를 해석합니다.

[규칙]

- 사용자가 말한 값과 단위만 사용합니다.
- 항목의 자료형에 맞게 표현을 정리할 수 있습니다.
- 320만 원을 3200000원으로 바꾸는 것처럼 단위 변환이 명확할 때만 변환합니다.
- 값, 단위 또는 대상이 모호하면 추측하지 않습니다.
- 범위를 벗어난 값을 임의로 경계값에 맞추지 않습니다.
- 평균값이나 추천값을 새로 만들지 않습니다.
- value_json은 해석된 실제 값을 유효한 JSON 문자열로 직렬화합니다.
- confidence는 명확하면 1.0, 하나로 정규화할 수 있으면 0.7~0.9,
  확정할 수 없으면 0.4 이하로 둡니다.

[출력]

value_json과 confidence만 반환합니다.""",
    (
        ("user_excerpt", "사용자 표현"),
        ("field_spec", "입력할 항목"),
        ("operation", "변경 방식"),
        ("intent_instruction", "현재 의도에 적용할 규칙"),
        ("hint", "재검토할 점"),
    ),
    output_schema="resolve_result",
    risk="form_change",
)
