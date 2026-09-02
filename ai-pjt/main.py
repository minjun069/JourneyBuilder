from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI

from core.checkpointer import get_checkpointer
from core.config import settings
from core.monitoring import get_callbacks
from graph.builder import build_graph
from schemas.chat import ChatRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with get_checkpointer() as checkpointer:
        app.state.chat_graph = build_graph(checkpointer)
        yield


app = FastAPI(title="RetireWise AI Service", lifespan=lifespan)


async def _process_and_callback(graph, request: ChatRequest):
    input_data = {
        "message": request.message,
        "step_id": request.step_id,
        "customer_info": request.customer_info,
        "field_registry": request.field_registry,
    }
    config = {"configurable": {"thread_id": request.thread_id}, "callbacks": get_callbacks()}

    result = await graph.ainvoke(input_data, config=config)
    output = result.get("output", {})

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.backend_url}/api/ai/callback",  # TODO: 실제 엔드포인트로 교체
            json={
                "request_id": request.request_id,
                "thread_id": request.thread_id,
                "output": output,
            },
        )


@app.post("/chat", status_code=202)
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(_process_and_callback, app.state.chat_graph, request)
    return


@app.get("/health")
def health():
    return {"status": "ok"}
