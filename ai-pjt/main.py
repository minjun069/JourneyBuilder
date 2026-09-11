from contextlib import asynccontextmanager
import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from core.callback_masking import CallbackPayloadMasker
from core.checkpointer import build_context_state_api, get_checkpointer
from core.config import settings
from core.context import ContextScope
from core.logging_setup import configure_logging
from core.masking import MaskingSink
from core.monitoring import get_callbacks
from core.observability_masking import (
    StructuredLogInstrumentationExporter,
    build_masked_runtime_instrumentation,
    masked_exception_fields,
)
from graph.builder import build_graph
from tools.graph_adapter import build_graph_tool_executors
from tools.runtime import build_production_tool_runtime
from transport.contracts import (
    CallbackError,
    ChatV2Request,
    CompletedCallback,
    FailedCallback,
    ServiceContractError,
    verify_service_authorization,
)
from transport.runtime import (
    AsyncChatTransport,
    CallbackClient,
    ExecutionBudget,
    PostgresRequestCheckpointStore,
    utc_now,
)


# uvicorn 기본 로깅 설정은 uvicorn* 로거만 구성하고 root 에는 핸들러를 붙이지 않는다.
# 그대로 두면 이 서비스의 로그와 계측 이벤트가 컨테이너 stdout 에 전혀 나오지 않는다.
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    get_callbacks()
    async with get_checkpointer() as checkpointer:
        tool_runtime = build_production_tool_runtime()
        instrumentation, instrumentation_sink = build_masked_runtime_instrumentation(
            pseudonym_key=settings.masking_pseudonym_key_bytes,
            sink=MaskingSink.TRACE_EXPORT,
            downstream=StructuredLogInstrumentationExporter(
                logging.getLogger("retirewise.instrumentation")
            ),
        )
        app.state.instrumentation = instrumentation
        app.state.instrumentation_sink = instrumentation_sink
        callback_masker = CallbackPayloadMasker(
            pseudonym_key=settings.masking_pseudonym_key_bytes,
        )
        app.state.callback_masker = callback_masker
        app.state.chat_graph = build_graph(
            checkpointer,
            instrumentation=instrumentation,
            tool_executors=build_graph_tool_executors(tool_runtime),
        )
        app.state.context_api = build_context_state_api(checkpointer)
        request_store = PostgresRequestCheckpointStore(settings.db_url)
        await request_store.setup()
        app.state.v2_transport = AsyncChatTransport(
            checkpoint_store=request_store,
            processor=_process_v2_graph,
            callback_client=CallbackClient(
                url=(
                    f"{settings.backend_url.rstrip('/')}"
                    "/api/v2/internal/ai/callback/"
                ),
                bearer_token=settings.ai_service_bearer_token,
                instrumentation=instrumentation,
                timeout_seconds=settings.callback_timeout_seconds,
            ),
            callback_masker=callback_masker,
            instrumentation=instrumentation,
        )
        yield


app = FastAPI(title="RetireWise AI Service", lifespan=lifespan)


def _v2_validation_error(exc: ValidationError) -> ServiceContractError:
    first = exc.errors(include_url=False)[0]
    field = str(first.get("loc", ("request",))[0])
    error_type = first.get("type")
    if field == "contract_version" and error_type == "literal_error":
        return ServiceContractError(
            status_code=422,
            code="CONTRACT_VERSION_UNSUPPORTED",
            message="지원하지 않는 계약 버전입니다.",
        )
    if error_type == "missing":
        message = f"{field}는 필수입니다."
    elif field == "session_id" and error_type in {"int_type", "int_parsing"}:
        message = "session_id는 정수여야 합니다."
    else:
        message = "AI 요청 스키마가 올바르지 않습니다."
    return ServiceContractError(
        status_code=422,
        code="REQUEST_SCHEMA_INVALID",
        message=message,
    )


def _encode_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _graph_input(request: ChatV2Request) -> dict[str, Any]:
    customer_info = request.customer_info
    if hasattr(customer_info, "model_dump"):
        customer_info = customer_info.model_dump(mode="json")
    payload = {
        "request_id": request.request_id,
        "thread_id": request.thread_id,
        "message": request.message,
        "step_id": request.step_id,
        "customer_info": customer_info,
        "field_registry": request.field_registry,
    }
    for field in ("request_mode", "review_trigger", "elicit_action"):
        value = getattr(request, field, None)
        if value is not None:
            payload[field] = value
    chart_payload = getattr(request, "chart_payload", None)
    if chart_payload is not None:
        payload["chart_payload"] = chart_payload
    tool_context = getattr(request, "tool_context", None)
    if tool_context is not None:
        payload["tool_context"] = (
            tool_context.model_dump(mode="json")
            if hasattr(tool_context, "model_dump")
            else tool_context
        )
    return payload


async def _process_v2_graph(
    request: ChatV2Request,
    budget: ExecutionBudget,
) -> dict[str, Any]:
    budget.require_time()
    scope = ContextScope(request.session_id, request.thread_id)
    context_api = app.state.context_api
    context = await context_api.read_context(scope)
    graph_input = {
        **_graph_input(request),
        **context_api.state_channels(scope, context),
        "execution_budget": budget.to_state(),
    }
    graph_config = {
        **context_api.config_for(scope),
        "callbacks": get_callbacks(),
    }
    result = await app.state.chat_graph.ainvoke(
        graph_input,
        config=graph_config,
    )
    output = result.get("output", {})
    if not isinstance(output, dict):
        raise ValueError("graph output must be an object")
    return output


@app.exception_handler(ServiceContractError)
async def service_contract_error_handler(
    _request: Request,
    exc: ServiceContractError,
):
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


async def _parse_v2_request(raw_request: Request) -> ChatV2Request:
    try:
        raw_payload = await raw_request.json()
        return ChatV2Request.model_validate(raw_payload)
    except ValidationError as exc:
        raise _v2_validation_error(exc) from None
    except ValueError:
        raise ServiceContractError(
            status_code=422,
            code="REQUEST_SCHEMA_INVALID",
            message="AI 요청 스키마가 올바르지 않습니다.",
        ) from None


@app.post("/v2/chat", status_code=202)
async def chat_v2(
    raw_request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    verify_service_authorization(
        authorization,
        settings.ai_service_bearer_token,
    )
    request = await _parse_v2_request(raw_request)

    accepted = await app.state.v2_transport.accept(
        request,
        background_tasks.add_task,
    )
    return accepted.model_dump(mode="json")


async def _stream_v2_chat_events(
    graph,
    context_api,
    request: ChatV2Request,
    callback_masker: CallbackPayloadMasker,
) -> AsyncIterator[str]:
    identity = {
        "contract_version": "2.0",
        "request_id": request.request_id,
        "session_id": request.session_id,
        "thread_id": request.thread_id,
    }
    yield _encode_sse("start", identity)

    final_state: dict[str, Any] = {}
    try:
        budget = ExecutionBudget.from_request(request)
        budget.require_time()
        scope = ContextScope(request.session_id, request.thread_id)
        context = await context_api.read_context(scope)
        graph_input = {
            **_graph_input(request),
            **context_api.state_channels(scope, context),
            "execution_budget": budget.to_state(),
        }
        graph_config = {
            **context_api.config_for(scope),
            "callbacks": get_callbacks(),
        }

        async with asyncio.timeout(budget.remaining_seconds()):
            async for graph_event in graph.astream_events(
                graph_input,
                config=graph_config,
                version="v2",
            ):
                if graph_event.get("event") == "on_chat_model_stream":
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
            raise ValueError("graph output must be an object")
        terminal = callback_masker.mask(
            CompletedCallback(
                request_id=request.request_id,
                session_id=request.session_id,
                thread_id=request.thread_id,
                completed_at=utc_now(),
                output=output,
            ).model_dump(mode="json")
        )
        safe_output = terminal["output"]
        response = safe_output.get("response", "")
        if isinstance(response, str) and response:
            yield _encode_sse(
                "delta",
                {
                    "contract_version": "2.0",
                    "request_id": request.request_id,
                    "content": response,
                },
            )
        yield _encode_sse("done", terminal)
    except asyncio.CancelledError:
        logger.info("AI v2 stream 연결 종료")
        raise
    except (TimeoutError, asyncio.TimeoutError):
        terminal = callback_masker.mask(
            FailedCallback(
                request_id=request.request_id,
                session_id=request.session_id,
                thread_id=request.thread_id,
                completed_at=utc_now(),
                error=CallbackError(
                    code="LLM_TIMEOUT",
                    message="AI 응답 생성 시간이 초과되었습니다.",
                    retryable=True,
                ),
            ).model_dump(mode="json")
        )
        yield _encode_sse("error", terminal)
    except Exception as error:
        logger.error(
            "AI v2 stream 처리 실패",
            extra={
                "error_code": "stream_processing_failed",
                "detail": masked_exception_fields(
                    error,
                    request_id=str(request.request_id),
                ),
            },
        )
        terminal = callback_masker.mask(
            FailedCallback(
                request_id=request.request_id,
                session_id=request.session_id,
                thread_id=request.thread_id,
                completed_at=utc_now(),
                error=CallbackError(
                    code="INTERNAL_ERROR",
                    message="AI 응답 생성에 실패했습니다.",
                    retryable=False,
                ),
            ).model_dump(mode="json")
        )
        yield _encode_sse("error", terminal)


@app.post("/v2/chat/stream")
async def chat_v2_stream(
    raw_request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    verify_service_authorization(
        authorization,
        settings.ai_service_bearer_token,
    )
    request = await _parse_v2_request(raw_request)
    return StreamingResponse(
        _stream_v2_chat_events(
            app.state.chat_graph,
            app.state.context_api,
            request,
            app.state.callback_masker,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _retired_v1_response() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "success": False,
            "error": {
                "code": "CONTRACT_VERSION_UNSUPPORTED",
                "message": "AI v1 서비스 경로는 종료되었습니다.",
                "retryable": False,
            },
        },
    )


@app.post("/chat", include_in_schema=False)
async def chat(_request: Request):
    return _retired_v1_response()


@app.post("/chat/stream", include_in_schema=False)
async def chat_stream(_request: Request):
    return _retired_v1_response()


@app.get("/health")
def health():
    return {"status": "ok"}
