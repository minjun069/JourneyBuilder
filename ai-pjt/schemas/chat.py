from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    request_id: str
    thread_id: str
    step_id: str
    message: str
    customer_info: dict
    field_registry: list[dict] = Field(default_factory=list)
