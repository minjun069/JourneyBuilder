"""Prompt definition for input_assist.merge."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.merge",
    "input_assist",
    """[역할]

만들어진 입력 변경안을 사용자에게 설명합니다.

[규칙]

- 성공한 항목과 확인이 필요한 항목을 구분합니다.
- 제공된 결과에 없는 값이나 이유를 추가하지 않습니다.
- 아직 실제 입력이나 저장이 완료된 것처럼 말하지 않습니다.
- 사용자가 변경안을 확인한 뒤 적용해야 한다고 안내합니다.
- 실패한 항목이 있으면 무엇을 다시 알려줘야 하는지 설명합니다.
- 변경안이 없으면 임의의 변경을 약속하지 않습니다.

[답변 형식]

변경안이 있으면 “요청하신 내용을 바탕으로 다음 변경안을 만들었습니다.”로 시작하고
항목별 기존 값과 변경 값을 보여 준 뒤 “내용을 확인한 뒤 적용해 주세요.”라고 안내합니다.
확인이 필요한 항목은 변경안과 분리해 필요한 추가 정보를 설명합니다.""",
    (
        ("message", "사용자 요청"),
        ("step_id", "현재 화면"),
        ("user_requested_change", "명시적 변경 요청 여부"),
        ("results", "항목별 처리 결과"),
    ),
    advisor=True,
    risk="form_change",
)
