from pydantic import BaseModel


class ChatInput(BaseModel):
    request_id: str
    session_id: int
    thread_id: str
    step_id: str
    message: str
    customer_info: dict


class ChatRequest(ChatInput):
    callback_url: str


class ChatStreamRequest(ChatInput):
    pass
