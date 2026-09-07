from typing import Any, NotRequired, TypedDict


class ContextScopeState(TypedDict):
    schema_version: str
    session_id: int
    thread_id: str


class PersistedConversationState(TypedDict):
    schema_version: str
    summary: dict[str, Any]
    structured_context: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    turn_digests: list[list[str]]


class ChatState(TypedDict):
    request_id: NotRequired[str]
    message: str
    step_id: str
    customer_info: dict
    field_registry: list[dict]
    next: str   # 라우터가 결정한 다음 에이전트 노드명
    output: dict  # 에이전트 응답 — input_assist는 FormChangeAction 포함
    context_scope: NotRequired[ContextScopeState]
    conversation_context: NotRequired[PersistedConversationState]
    route: NotRequired[dict[str, Any]]
    route_plan: NotRequired[dict[str, Any]]
    current_task: NotRequired[dict[str, Any]]
    task_results: NotRequired[dict[str, dict[str, Any]]]
    bound_inputs: NotRequired[dict[str, Any]]
    plan_issues: NotRequired[list[dict[str, str]]]
