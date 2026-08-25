from pydantic import BaseModel


class ChatRequest(BaseModel):
    request_id: str
    thread_id: str
    step_id: str
    message: str
    customer_info: dict
