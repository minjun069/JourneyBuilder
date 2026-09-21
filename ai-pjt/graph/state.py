from typing import Any, NotRequired, TypedDict


class PersistedConversationState(TypedDict):
    schema_version: str
    summary: dict[str, Any]
    structured_state: dict[str, Any]
    turns: list[dict[str, Any]]


class TaskInputState(TypedDict):
    description: str
    bindings: dict[str, Any]


class ChatState(TypedDict):
    request_id: NotRequired[str]
    thread_id: NotRequired[str]
    message: str
    step_id: str
    customer_info: dict
    chart_payload: NotRequired[dict[str, Any] | None]
    request_mode: NotRequired[str]
    output: dict  # 에이전트 응답 — input_assist는 FormChangeAction 포함
    conversation_context: NotRequired[PersistedConversationState]
    retrieved_memories: NotRequired[list[dict[str, Any]]]
    route: NotRequired[dict[str, Any]]
    route_plan: NotRequired[dict[str, Any]]
    task_input: NotRequired[TaskInputState]
    task_results: NotRequired[dict[str, dict[str, Any]]]
    plan_issues: NotRequired[list[dict[str, Any]]]
