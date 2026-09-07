from contextlib import asynccontextmanager
import asyncio
import logging
import secrets

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException

from core.checkpointer import get_checkpointer
from core.config import settings
from core.monitoring import get_callbacks
from graph.builder import build_graph
from schemas.chat import ChatRequest


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    async with get_checkpointer() as checkpointer:
        app.state.chat_graph = build_graph(checkpointer)
        yield


app = FastAPI(title="RetireWise AI Service", lifespan=lifespan)


async def _send_callback(request: ChatRequest, payload: dict):
    headers = {}
    if settings.ai_callback_secret:
        headers["X-Internal-Secret"] = settings.ai_callback_secret

    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=settings.callback_timeout_seconds) as client:
                response = await client.post(
                    request.callback_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                return
        except httpx.HTTPError:
            logger.exception(
                "AI callback 전송 실패",
                extra={"request_id": request.request_id, "attempt": attempt},
            )
            if attempt < 3:
                await asyncio.sleep(2 ** (attempt - 1))


async def _process_and_callback(graph, request: ChatRequest):
    input_data = {
        "message": request.message,
        "step_id": request.step_id,
        "customer_info": request.customer_info,
        "field_registry": request.field_registry,
    }
    config = {"configurable": {"thread_id": request.thread_id}, "callbacks": get_callbacks()}

    try:
        result = await graph.ainvoke(input_data, config=config)
        payload = {
            "request_id": request.request_id,
            "session_id": request.session_id,
            "thread_id": request.thread_id,
            "status": "completed",
            "output": result.get("output", {}),
        }
    except Exception:
        logger.exception("AI 응답 생성 실패", extra={"request_id": request.request_id})
        payload = {
            "request_id": request.request_id,
            "session_id": request.session_id,
            "thread_id": request.thread_id,
            "status": "failed",
            "error_code": "AI_PROCESSING_FAILED",
        }

    await _send_callback(request, payload)


@app.post("/chat", status_code=202)
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
):
    if settings.ai_callback_secret and not secrets.compare_digest(
        x_internal_secret or "",
        settings.ai_callback_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid internal secret")

    background_tasks.add_task(_process_and_callback, app.state.chat_graph, request)
    return {"request_id": request.request_id, "status": "accepted"}


@app.get("/health")
def health():
    return {"status": "ok"}
