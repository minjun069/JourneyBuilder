"""Prompt definition for qa.plan."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.plan",
    "qa",
    """[할 일]

답할 질문과 참고자료 검색 계획을 정합니다.

[방법]

- 질문의 조건을 유지하고, 함께 설명할 수 있는 내용은 나누지 않습니다.
- 작업 질문의 범위 안에서 사용자 원문과 최근 대화를 함께 읽습니다. '그게', '하나'처럼
  앞선 답변을 가리키는 표현은 실제 지시 대상으로 풀어 검색 계획을 세웁니다.
- 현재 보고서의 경고·사실이 있으면 그 내용을 사용합니다. 이전 AI 답변만으로 새로운 사실을 만들지 않습니다.
- 금융 개념, 제도, 제품 용어 설명에는 참고자료를 검색합니다.
- 사용자가 입력한 값 확인이나 가벼운 대화에는 검색하지 않습니다.
- 검색어에는 답할 질문의 핵심 용어와 조건만 담습니다.
- 보완할 점이 있으면 해당 질문의 검색어만 고칩니다.

[결과]

questions에 1~3개 항목을 반환합니다. 각 항목은 question(답할 질문),
need_retrieval(검색 여부), query(검색어이며 검색하지 않으면 null)만 포함합니다.""",
    (
        ("question", "사용자 질문"),
        ("user_message", "사용자 원문"),
        ("conversation", "최근 대화와 관련 기억"),
        ("report", "현재 보고서의 확인된 내용"),
        ("step_id", "현재 화면"),
        ("feedback", "보완할 점"),
    ),
    output_schema="qa_plan_result",
    version="4.1.0",
)
