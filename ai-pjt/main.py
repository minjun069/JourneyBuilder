from contextlib import asynccontextmanager
import asyncio
import json
import logging
import secrets
from typing import Any, AsyncIterator

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from core.checkpointer import get_checkpointer
from core.config import settings
from core.monitoring import get_callbacks
from graph.builder import build_graph
from schemas.chat import ChatRequest, ChatStreamRequest


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    async with get_checkpointer() as checkpointer:
        app.state.chat_graph = build_graph(checkpointer)
        yield


app = FastAPI(title="RetireWise AI Service", lifespan=lifespan)

_PUBLIC_STREAM_NODES = {"merge", "run"}


def _verify_internal_secret(value: str | None) -> None:
    if settings.ai_callback_secret and not secrets.compare_digest(
        value or "",
        settings.ai_callback_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid internal secret")


def _encode_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _message_chunk_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _graph_input(request: ChatRequest | ChatStreamRequest) -> dict[str, Any]:
    return {
        "message": request.message,
        "step_id": request.step_id,
        "customer_info": request.customer_info,
        "field_registry": request.field_registry,
    }


def _graph_config(request: ChatRequest | ChatStreamRequest) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": request.thread_id},
        "callbacks": get_callbacks(),
    }


async def _stream_chat_events(graph, request: ChatStreamRequest) -> AsyncIterator[str]:
    yield _encode_sse(
        "start",
        {
            "request_id": request.request_id,
            "session_id": request.session_id,
        },
    )

    streamed_text = ""
    final_state: dict[str, Any] = {}
    try:
        async for graph_event in graph.astream_events(
            _graph_input(request),
            config=_graph_config(request),
            version="v2",
        ):
            if graph_event.get("event") == "on_chat_model_stream":
                node = graph_event.get("metadata", {}).get("langgraph_node")
                if node not in _PUBLIC_STREAM_NODES:
                    continue
                chunk = graph_event.get("data", {}).get("chunk")
                content = _message_chunk_text(getattr(chunk, "content", ""))
                if content:
                    streamed_text += content
                    yield _encode_sse(
                        "delta",
                        {"request_id": request.request_id, "content": content},
                    )
                continue

            if (
                graph_event.get("event") == "on_chain_end"
                and not graph_event.get("parent_ids")
            ):
                output = graph_event.get("data", {}).get("output")
                if isinstance(output, dict):
                    final_state = output

        output = final_state.get("output", {})
        if not isinstance(output, dict):
            output = {}
        response = output.get("response", "")
        if not streamed_text and isinstance(response, str) and response:
            yield _encode_sse(
                "delta",
                {"request_id": request.request_id, "content": response},
            )

        yield _encode_sse(
            "done",
            {
                "request_id": request.request_id,
                "session_id": request.session_id,
                "thread_id": request.thread_id,
                "output": output,
            },
        )
    except asyncio.CancelledError:
        logger.info("AI stream 연결 종료", extra={"request_id": request.request_id})
        raise
    except Exception:
        logger.exception("AI stream 처리 실패", extra={"request_id": request.request_id})
        yield _encode_sse(
            "error",
            {
                "request_id": request.request_id,
                "code": "AI_STREAM_FAILED",
                "message": "AI 응답 생성에 실패했습니다.",
            },
        )


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
    try:
        result = await graph.ainvoke(
            _graph_input(request),
            config=_graph_config(request),
        )
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
    _verify_internal_secret(x_internal_secret)

    background_tasks.add_task(_process_and_callback, app.state.chat_graph, request)
    return {"request_id": request.request_id, "status": "accepted"}


@app.post("/chat/stream")
async def chat_stream(
    request: ChatStreamRequest,
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
):
    _verify_internal_secret(x_internal_secret)
    return StreamingResponse(
        _stream_chat_events(app.state.chat_graph, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
