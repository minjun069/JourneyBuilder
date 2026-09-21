"""Review only changes whose meaning requires interpretation."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.review", "input_assist",
    """입력 요청과 변경안의 의미가 일치하는지 확인합니다.

값과 단위의 해석, 대상, 변경 동작이 요청에 분명히 근거하면 accepted=true입니다.
조회값을 쓰라는 요청은 제공된 조회값을 근거로 확인합니다.
추측한 값·대상이나 요청하지 않은 변경이면 false와 필요한 확인 내용 한 문장을 씁니다.
자료형과 허용 범위는 코드가 이미 검사했습니다. 값을 고치거나 새 변경안을 만들지 않습니다.
제공된 모든 change_ref에 대해 판단을 하나씩 반환합니다.""",
    (("message", "입력 요청"), ("changes", "의미 확인이 필요한 변경안"), ("reference", "검증된 조회값")),
    output_schema="review_result", risk="form_change", version="4.0.0",
)
