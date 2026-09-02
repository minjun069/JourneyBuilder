from typing import TypedDict


class ChatState(TypedDict):
    message: str
    step_id: str
    customer_info: dict
    field_registry: list[dict]
    next: str   # 라우터가 결정한 다음 에이전트 노드명
    output: dict  # 에이전트 응답 — input_assist는 FormChangeAction 포함
