"""Extract requested form changes in one batch."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.extract", "input_assist",
    """사용자가 명시적으로 요청한 입력 변경의 대상, 동작, 값을 한 번에 찾습니다.

일반 변경은 request=change, 승인된 평균·기준값으로 채우기는 fill_defaults,
설명이나 입력 위치만 묻는 질문은 none입니다. none과 fill_defaults의 changes는 비웁니다.
주어진 field_ref와 item_ref만 사용합니다. 고정 항목은 set, 기존 행은 update/remove,
새 행은 add입니다. 중첩 행 추가의 item_ref는 부모 행입니다.
값은 항목의 단위에 맞는 실제 값으로 반환합니다. add의 객체 키는 제공된 필드 키를 씁니다.
remove에는 값이 필요하지 않습니다. 사용자가 말하지 않은 값이나 평균값을 만들지 않습니다.
조회값으로 변경하라는 요청이면 제공된 조회값을 그대로 사용합니다. 조회값의 단위를 바꾸거나 새 값을 추정하지 않습니다.
금액 참조 토큰은 숫자를 추측하거나 단위를 덧붙이지 말고 value에 그대로 반환합니다.
대상이나 값을 확정할 수 없는 변경은 제외하고 questions에 필요한 확인 질문만 씁니다.

다음 구조의 JSON 객체만 반환합니다. value는 문자열로 직렬화하지 않은 실제 JSON 값입니다.
{"request":"change","changes":[{"field_ref":"f0","item_ref":null,"operation":"set","value":40}],"questions":[]}""",
    (("message", "입력 요청"), ("fields", "입력 항목"),
     ("current_values", "현재 고정 항목 값"), ("items", "현재 반복 항목"),
     ("reference", "검증된 조회값")),
    output_schema="extract_result", risk="form_change", version="4.0.0",
)
