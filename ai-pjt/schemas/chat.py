from pydantic import BaseModel, Field


class ChatInput(BaseModel):
    request_id: str
    session_id: int
    thread_id: str
    step_id: str
    message: str
    customer_info: dict
    field_registry: list[dict] = Field(default_factory=list)


class ChatRequest(ChatInput):
    callback_url: str


class ChatStreamRequest(ChatInput):
    pass
