"""Prompt definition for qa.judge."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.judge",
    "qa",
    """[할 일]

질문 전체에 답했고 제공된 정보에 맞는지 확인합니다.

[방법]

- 사용자 정보와 확인된 결과도 근거입니다. 검색하지 않았다는 이유로 실패시키지 않습니다.
- 확인할 수 없다고 알린 부분은 근거 없는 주장으로 보지 않습니다.
- 근거가 있는데 질문 누락, 값 변경, 어려운 표현이 있으면 answer_quality입니다.
- 근거 없는 사실이나 새로운 공식 숫자를 단정하면 evidence_insufficient입니다.

[결과]

status는 good, answer_quality, evidence_insufficient 중 하나입니다.
reason에는 고쳐야 할 부분을 한 문장으로 쓰고, good이면 빈 문자열을 씁니다.""",
    (
        ("question", "사용자 질문"),
        ("customer_info", "관련 사용자 정보"),
        ("bindings", "확인된 결과"),
        ("evidence", "질문별 참고자료"),
        ("answer", "작성된 답변"),
    ),
    output_schema="qa_judge_result",
    version="4.0.0",
)
