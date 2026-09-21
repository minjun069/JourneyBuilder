"""Explain the verified proposed changes."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.respond", "input_assist",
    """검증된 입력 변경안을 짧게 설명합니다.

제안이 있으면 항목명과 기존 값에서 변경할 값으로의 변화를 보여 주고,
사용자가 확인해 적용해야 한다고 안내합니다. 삭제와 추가도 그 동작대로 설명합니다.
확인이 필요한 내용은 따로 알려 줍니다. 제공된 값·이유만 사용하며 저장됐다고 말하지 않습니다.
내부 참조키는 답변에 쓰지 않습니다. 변경안이 없으면 필요한 확인 내용만 안내합니다.""",
    (("message", "입력 요청"), ("changes", "검증된 변경안"), ("questions", "확인이 필요한 내용")),
    advisor=True, risk="form_change", version="4.0.0",
)
