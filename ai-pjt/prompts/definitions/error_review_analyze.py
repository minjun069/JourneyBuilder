"""Prompt definition for error_review.analyze."""

from prompts.definitions._factory import define_prompt


PROMPT = define_prompt(
    "error_review.analyze",
    "error_review",
    """입력 항목과 검토 기준을 읽고 명확한 오기·단위 착오·항목 사이의 모순을 찾습니다.
필수값·자료형·허용 범위는 이미 검사했습니다. 드물다는 이유만으로 오류를 만들지 않습니다.

문제가 있는 항목의 ref를 field_ref에 넣고, reason에 사용자가 확인할 내용을 설명합니다.
수정값이 확실할 때만 suggested_value를 채우고 그 외에는 null로 둡니다.
출력은 {"items": [{"field_ref": "항목 ref", "reason": "확인할 내용", "suggested_value": null}]} 형식의 JSON입니다.
수정값은 숫자·문자열·객체 등 원래 자료형을 유지하고, 문제가 없으면 items를 비웁니다.""",
    (
        ("fields", "검토할 입력 항목"),
        ("review_rules", "검토 기준"),
    ),
    output_schema="review_result",
    risk="financial_review",
)
