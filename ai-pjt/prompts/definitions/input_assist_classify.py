"""Prompt definition for input_assist.classify."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "input_assist.classify",
    "input_assist",
    """[역할]

사용자가 현재 입력 화면에 무엇을 요청했는지 분류합니다. 값을 추출하거나 변경안을 만들지 않습니다.

[의도 종류]

- fill_avg: 승인된 평균값이나 기준값으로 항목을 채워 달라는 요청
- find_field: 값은 알지만 어느 항목에 넣을지 모르는 요청
- organize: 여러 정보를 입력 항목별로 정리해야 하는 요청
- other: 대상과 값이 명확한 직접 변경 또는 입력 변경이 아닌 질문·설명·대화

[구분]

- “평균 생활비가 얼마예요?”는 other이며 user_requested_change=false입니다.
- “평균 생활비로 빈칸을 채워줘.”는 fill_avg이며 true입니다.
- “세후 월급은 320만 원이야. 월 소득에 넣어줘.”는 other이며 true입니다.
- “퇴직연금이 3천만 원인데 어디에 입력해?”는 find_field이며 true입니다.

[출력]

intent와 user_requested_change만 반환합니다.""",
    (
        ("message", "사용자 요청"),
        ("step_id", "현재 화면"),
    ),
    output_schema="classify_result",
    risk="form_change",
)
