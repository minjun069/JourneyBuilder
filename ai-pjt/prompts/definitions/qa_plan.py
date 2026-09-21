"""Prompt definition for qa.plan."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.plan",
    "qa",
    """[할 일]

답할 질문과 참고자료 검색 계획을 정합니다.

[방법]

- 질문의 조건을 유지하고, 함께 설명할 수 있는 내용은 나누지 않습니다.
- 금융 개념, 제도, 제품 용어 설명에는 참고자료를 검색합니다.
- 사용자가 입력한 값 확인이나 가벼운 대화에는 검색하지 않습니다.
- 검색어에는 답할 질문의 핵심 용어와 조건만 담습니다.
- 보완할 점이 있으면 해당 질문의 검색어만 고칩니다.

[결과]

questions에 1~3개 항목을 반환합니다. 각 항목은 question(답할 질문),
need_retrieval(검색 여부), query(검색어이며 검색하지 않으면 null)만 포함합니다.""",
    (
        ("question", "사용자 질문"),
        ("step_id", "현재 화면"),
        ("feedback", "보완할 점"),
    ),
    output_schema="qa_plan_result",
    version="4.0.0",
)
