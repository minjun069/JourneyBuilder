"""Prompt definition for summary.extract."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "summary.extract",
    "summary",
    """[역할]

대화에서 이후에도 유용한 비금융 정보를 추출합니다.

[추출할 수 있는 정보]

- 사용자가 직접 말한 목표
- 답변 형식이나 대화 방식에 대한 선호
- 사용자가 확정한 결정
- 아직 해결되지 않은 질문
- 사용자가 직접 언급한 비민감 프로젝트나 관심 대상

[추출하지 않는 정보]

- 금액, 소득, 자산, 부채, 연금 또는 폼 입력값
- 개인정보와 인증정보
- Assistant가 제안했지만 사용자가 확인하지 않은 내용
- 추측하거나 의미를 보태야만 만들 수 있는 내용

[규칙]

- kind는 goal, response_preference, decision, open_question 또는 entity만 사용합니다.
- value와 evidence_quote는 사용자 발화에 실제로 있는 표현만 사용합니다.
- 근거가 분명하지 않으면 claims를 비웁니다.""",
    (("source", "대화 원문"),),
    output_schema="summary_candidate",
    risk="conversation_memory",
)
