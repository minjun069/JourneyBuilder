"""One grounded rewrite of the verified aggregate answer."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "aggregator.counseling",
    "aggregator",
    """[할 일]

검증된 기존 답변을 상담 근거에 비추어 자연스럽게 다듬고, 도움이 되는 설명은 답변 흐름 안에 통합합니다. 별도의 보충 문단을 기계적으로 덧붙이지 않습니다.

[규칙]

- 질문에 직접 답한 뒤 필요한 의미와 확인할 항목을 간결하게 설명합니다.
- 기존 답변의 숫자·단위·조건·승인·저장 여부를 담은 문장은 글자와 순서를 그대로 보존합니다. action과 review는 다루지 않습니다.
- 제공된 근거의 적용 상황과 한계에 맞는 설명만 사용합니다. 이미 충분히 설명된 내용은 반복하지 않고 새 내용이 필요 없으면 기존 답변의 표현만 다듬습니다.
- 현재 질문과 최근 고객 정정·거절이 과거 요약이나 AI 추정보다 우선합니다. 근거의 예시를 고객의 사실로 단정하지 않습니다.
- 새 숫자·금액·기간·계산·상품 실행 권유·처리 완료 주장·안심 보장을 만들지 않습니다.
- 출처, 내부 ID, 문서명, URL을 답변에 노출하지 않습니다. 이미 제공하거나 거절한 정보를 다시 묻지 않습니다.

[출력]

완성된 답변 response만 반환합니다.""",
    (
        ("answer", "검증된 기존 답변"),
        ("question", "현재 사용자 질문"),
        ("conversation_context", "이전 대화 문맥"),
        ("evidence", "상담 참고 근거"),
    ),
    advisor=True,
    output_schema="aggregator_model_output",
    risk="financial_explanation",
)
