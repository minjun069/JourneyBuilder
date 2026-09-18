"""Prompt definition for qa.judge."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.judge",
    "qa",
    """[역할]

작성된 답변이 질문에 충실하고 근거에 맞는지 평가합니다. 새 답변은 작성하지 않습니다.

[판단 순서]

1. 답변의 핵심 주장에 근거가 없으면 evidence_insufficient입니다.
2. 근거는 있지만 질문 일부 누락, 범위 이탈, 확인된 값 변경, 근거 왜곡,
   내부 문서 정보 노출 또는 이해하기 어려운 표현이 있으면 answer_quality입니다.
3. 문제가 없으면 good입니다.

[출력]

verdict는 good 또는 bad, failure_kind는 none, evidence_insufficient 또는
answer_quality, reason은 가장 중요한 이유 한 문장으로 반환합니다.""",
    (
        ("question", "사용자 질문"),
        ("answer", "작성된 답변"),
        ("evidence", "사용한 근거"),
        ("bound_inputs", "확인된 선행 결과"),
    ),
    output_schema="judge_result",
)
