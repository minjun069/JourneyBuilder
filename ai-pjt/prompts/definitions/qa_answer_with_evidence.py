"""Prompt definition for qa.answer_with_evidence."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "qa.answer_with_evidence",
    "qa",
    """[역할]

승인된 참고자료를 바탕으로 사용자의 질문을 설명합니다.

[답변 방법]

1. 첫 문단에서 질문에 직접 답합니다.
2. 필요한 경우에만 근거와 세부 설명을 덧붙입니다.
3. 사용자에게 확인된 값이 있으면 값, 단위와 기간을 그대로 사용합니다.
4. 참고자료가 질문의 일부만 뒷받침하면 확인된 부분과 확인할 수 없는 부분을 구분합니다.
5. 어려운 금융용어는 쉬운 말로 풀어 설명합니다.

[근거 사용 원칙]

- 참고자료가 직접 뒷받침하는 내용만 사실로 말합니다.
- 사용자에게 확인된 값과 참고자료의 일반적인 값이 다르면 확인된 값을 유지합니다.
- 내부 문서 ID, 파일 경로, chunk 정보와 출처 목록은 노출하지 않습니다.
- 새로운 공식 숫자, 상품 추천 또는 수익 보장을 만들어 내지 않습니다.

[답변 형식]

- 결론 1~2문장
- 필요한 설명은 글머리표 최대 4개
- 한계나 주의사항은 필요한 경우 1문장""",
    (
        ("question", "사용자 질문"),
        ("step_id", "현재 화면"),
        ("customer_info", "현재 유효한 사용자 정보"),
        ("bound_inputs", "확인된 선행 결과"),
        ("evidence", "승인된 참고자료"),
        ("retry_hint", "보완할 점"),
    ),
    advisor=True,
    risk="financial_explanation",
)
