"""Prompt definition for summary.extract."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "summary.extract",
    "summary",
    """사용자 발화, AI 답변, 기존 요약에서 이후 대화에 필요한 내용을 간결하게 정리합니다.
목표는 goal, 답변 선호는 response_preference, 결정은 decision,
미해결 질문은 open_question, 그 밖의 대화 내용은 context로 구분합니다.

value에는 원뜻에 충실한 요약을, evidence_quote에는 해당 원문의 근거 구절을 그대로 씁니다.
누가 말했는지와 제안·확정의 차이를 유지하고, AI의 제안을 사용자 결정으로 바꾸지 않습니다.
기억할 내용이 없으면 claims를 비웁니다.""",
    (("source", "대화 원문"),),
    output_schema="summary_candidate",
    risk="conversation_memory",
)
