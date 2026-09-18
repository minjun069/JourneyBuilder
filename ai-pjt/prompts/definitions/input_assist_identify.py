"""Prompt definition for input_assist.identify."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.identify",
    "input_assist",
    """[역할]

사용자가 명시적으로 요청한 변경의 대상만 찾습니다.

[규칙]

- 변경 가능한 항목 목록에 있는 section과 path만 선택합니다.
- 사용자가 말하지 않은 대상이나 값을 추가하지 않습니다.
- 기존 항목을 수정하거나 삭제할 때는 제공된 item·parent 식별 정보를 그대로 사용합니다.
- 대상을 하나로 확정할 수 없거나 변경 요청이 아니면 items를 비웁니다.
- excerpt에는 판단 근거가 된 사용자 표현을 그대로 넣습니다.
- operation은 set, add, update 또는 remove만 사용합니다.

[출력]

items 배열만 반환하며 각 항목은 section, path, operation, 필요한 기존 항목 식별 정보,
선택 label과 excerpt를 지정된 identify_result 구조로 작성합니다.""",
    (
        ("message", "사용자 요청"),
        ("step_id", "현재 화면"),
        ("classification", "분류된 변경 의도"),
        ("user_requested_change", "명시적 변경 요청 여부"),
        ("current_form", "현재 입력값"),
        ("field_registry", "변경 가능한 항목"),
    ),
    output_schema="identify_result",
    risk="form_change",
)
