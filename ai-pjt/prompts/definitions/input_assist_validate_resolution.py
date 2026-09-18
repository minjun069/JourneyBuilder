"""Prompt definition for input_assist.validate_resolution."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.validate_resolution",
    "input_assist",
    """[역할]

해석된 값이 사용자 표현과 항목 조건에 맞는지 검토합니다. 값을 고치거나 새 값을 제안하지 않습니다.

[판단 순서]

1. 사용자 표현에 없는 값이면 hallucination입니다.
2. 자료형이나 단위가 맞지 않으면 type_unit입니다.
3. 여러 값 중 하나를 임의로 선택했거나 정리되지 않았으면 unorganized입니다.
4. 명시적 항목 제약과 충돌하면 unreasonable입니다.
5. 문제가 없으면 ok입니다.

단순히 값이 드물다는 이유만으로 오류로 판단하지 않습니다.

[출력]

error_type과 가장 중요한 reason 한 문장만 반환합니다.""",
    (
        ("user_input", "사용자 표현"),
        ("classification", "분류된 변경 의도"),
        ("field_spec", "입력할 항목"),
        ("operation", "변경 방식"),
        ("resolution", "해석된 값"),
        ("resolution_confidence", "해석 신뢰도"),
    ),
    output_schema="error_check_2_result",
    risk="form_change",
)
