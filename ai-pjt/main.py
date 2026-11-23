from contextlib import asynccontextmanager, suppress
import asyncio
import logging
from functools import partial

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from core.checkpointer import (
    build_committed_context_store,
    build_context_state_api,
    get_checkpointer,
)
from core.config import settings
from core.context_summary_model import ModelSummaryGenerator
from core.context_summary_worker import ConversationMemoryManager
from core.langfuse_exporter import build_langfuse_instrumentation_exporter
from core.logging_setup import configure_logging
from core.masking import MaskingSink
from core.monitoring import get_callbacks
from core.observability_masking import (
    StructuredLogInstrumentationExporter,
    build_masked_runtime_instrumentation,
)
from core.terminal_masking import TerminalPayloadMasker
from graph.builder import build_graph
from graph.aggregation import compose_aggregate_response
from graph.task_catalog import TASK_TYPE_CONTRACTS
from tools.graph_adapter import build_graph_tool_executors
from tools.runtime import build_production_tool_runtime
from transport.chat_service import (
    PROGRESS_EVENTS_VERSION,
    graph_input,
    parse_v2_request,
    stream_v2_chat_events,
    validation_error,
)
from transport.contracts import ServiceContractError, verify_service_authorization
from transport.sse_presenter import encode_sse


configure_logging()
logger = logging.getLogger(__name__)

# Compatibility aliases for internal tests and operational scripts.
_encode_sse = encode_sse
_graph_input = graph_input
_parse_v2_request = parse_v2_request
_stream_v2_chat_events = stream_v2_chat_events
_v2_validation_error = validation_error


def build_response_composer():
    """Fail startup closed unless the enabled candidate index is compatible."""
    if not settings.counseling_rag_enabled:
        return compose_aggregate_response
    from rag.counseling_retriever import get_counseling_retriever

    retriever = get_counseling_retriever()

    return partial(
        compose_aggregate_response,
        counseling_retriever=retriever,
        timeout_seconds=settings.counseling_rag_timeout_seconds,
    )


PROGRESS_EVENTS_VERSION = "v1"
PROGRESS_HEARTBEAT_SECONDS = 15.0
_PROGRESS_PHASE_ORDER = {
    "preparing": 0,
    "planning": 1,
    "executing": 2,
    "reviewing": 2,
    "composing": 2,
    "validating": 3,
    "saving": 4,
}
_GRAPH_PROGRESS_PHASES = {
    "plan": "planning",
    "validate": "validating",
    "persist_context": "saving",
}


async def _iter_with_optional_heartbeat(events, interval_seconds: float | None):
    if interval_seconds is None:
        async for event in events:
            yield event
        return
    if interval_seconds <= 0:
        raise ValueError("heartbeat interval must be positive")

    iterator = events.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait((pending,), timeout=interval_seconds)
            if not done:
                yield None
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield event
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        close = getattr(iterator, "aclose", None)
        if callable(close):
            with suppress(RuntimeError):
                await close()


def _graph_progress_phase(graph_event: dict[str, Any], request_mode: str) -> str | None:
    if graph_event.get("event") != "on_chain_start":
        return None
    metadata = graph_event.get("metadata")
    if not isinstance(metadata, dict):
        return None
    node = metadata.get("langgraph_node")
    if node == "execute":
        if request_mode == "error_review":
            return "reviewing"
        if request_mode == "presentation_entry":
            return "composing"
        return "executing"
    return _GRAPH_PROGRESS_PHASES.get(node)


async def _context_cleanup_loop(context_api, context_store) -> None:
    while True:
        try:
            await context_api.cleanup_expired_executions()
            await context_store.cleanup_closed_contexts()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - cleanup failure must not stop serving
            logger.exception("context_cleanup_failed")
        await asyncio.sleep(settings.context_cleanup_interval_seconds)


async def _context_memory_loop(memory_manager: ConversationMemoryManager) -> None:
    while True:
        try:
            await memory_manager.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - durable jobs remain retryable
            logger.exception("context_memory_worker_failed")
        await asyncio.sleep(settings.context_memory_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    get_callbacks()
    if settings.langfuse_enabled:
        instrumentation_exporter = build_langfuse_instrumentation_exporter(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            release=settings.langfuse_release,
        )
    else:
        instrumentation_exporter = StructuredLogInstrumentationExporter(
            logging.getLogger("retirewise.instrumentation")
        )
    try:
        async with get_checkpointer() as checkpointer:
            context_store = build_committed_context_store()
            await context_store.setup()
            tool_runtime = build_production_tool_runtime(
                task_contracts=TASK_TYPE_CONTRACTS,
            )
            instrumentation, instrumentation_sink = build_masked_runtime_instrumentation(
                pseudonym_key=settings.masking_pseudonym_key_bytes,
                sink=MaskingSink.TRACE_EXPORT,
                downstream=instrumentation_exporter,
            )
            app.state.instrumentation = instrumentation
            app.state.instrumentation_sink = instrumentation_sink
            app.state.instrumentation_exporter = instrumentation_exporter
            app.state.terminal_masker = TerminalPayloadMasker(
                pseudonym_key=settings.masking_pseudonym_key_bytes,
            )
            app.state.chat_graph = build_graph(
                checkpointer,
                instrumentation=instrumentation,
                tool_executors=build_graph_tool_executors(tool_runtime),
                tool_runtime=tool_runtime,
                response_composer=build_response_composer(),
                plan_validation_enabled=settings.plan_validation_enabled,
            )
            app.state.context_store = context_store
            app.state.context_api = build_context_state_api(checkpointer, context_store)

            from core.llm import embed_model, llm

            app.state.context_memory = ConversationMemoryManager(
                app.state.context_api,
                embedding_model=embed_model,
                summary_generator=ModelSummaryGenerator(
                    llm,
                    instrumentation=instrumentation,
                ),
                retrieval_limit=settings.context_memory_top_k,
                embedding_batch_size=settings.context_embedding_batch_size,
            )
            await app.state.context_api.cleanup_expired_executions()
            cleanup_task = asyncio.create_task(
                _context_cleanup_loop(app.state.context_api, context_store)
            )
            memory_task = asyncio.create_task(
                _context_memory_loop(app.state.context_memory)
            )
            try:
                yield
            finally:
                cleanup_task.cancel()
                memory_task.cancel()
                for task in (cleanup_task, memory_task):
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
    finally:
        shutdown = getattr(instrumentation_exporter, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:  # noqa: BLE001 - observability must not break shutdown
                logger.warning("langfuse_instrumentation_shutdown_failed")


app = FastAPI(title="RetireWise AI Service", lifespan=lifespan)


@app.exception_handler(ServiceContractError)
async def service_contract_error_handler(
    _request: Request,
    exc: ServiceContractError,
):
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


async def _parse_v2_request(raw_request: Request) -> ChatV2Request:
    try:
        raw_payload = await raw_request.json()
        if not isinstance(raw_payload, dict):
            raise ValueError("request body must be an object")
        supplied_digest = raw_payload.get("payload_digest")
        digest_payload = {
            key: value for key, value in raw_payload.items() if key != "payload_digest"
        }
        computed_digest = sha256(json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if not isinstance(supplied_digest, str) or not hmac.compare_digest(
            supplied_digest,
            computed_digest,
        ):
            raise ServiceContractError(
                status_code=409,
                code="PAYLOAD_DIGEST_MISMATCH",
                message="request_id에 결합된 요청 내용이 다릅니다.",
            )
        return ChatV2Request.model_validate(raw_payload)
    except ValidationError as exc:
        raise _v2_validation_error(exc) from None
    except ServiceContractError:
        raise
    except (TypeError, ValueError):
        raise ServiceContractError(
            status_code=422,
            code="REQUEST_SCHEMA_INVALID",
            message="AI 요청 스키마가 올바르지 않습니다.",
        ) from None


async def _stream_v2_chat_events(
    graph,
    context_api,
    request: ChatV2Request,
    callback_masker: CallbackPayloadMasker,
    memory_manager: ConversationMemoryManager | None = None,
    instrumentation: RuntimeInstrumentationAdapter | None = None,
    progress_events: bool = False,
    heartbeat_interval_seconds: float = PROGRESS_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    identity = {
        "contract_version": "2.0",
        "request_id": request.request_id,
        "session_id": request.session_id,
        "thread_id": request.thread_id,
    }
    yield _encode_sse("start", identity)

    event_sequence = 0
    latest_progress_rank = -1

    def progress_frame(phase: str) -> str | None:
        nonlocal event_sequence, latest_progress_rank
        if not progress_events:
            return None
        rank = _PROGRESS_PHASE_ORDER[phase]
        if rank <= latest_progress_rank:
            return None
        latest_progress_rank = rank
        event_sequence += 1
        return _encode_sse("progress", {
            **identity,
            "event_seq": event_sequence,
            "phase": phase,
            "status": "active",
        })

    def heartbeat_frame() -> str | None:
        nonlocal event_sequence
        if not progress_events:
            return None
        event_sequence += 1
        return _encode_sse("heartbeat", {
            **identity,
            "event_seq": event_sequence,
        })

    final_state: dict[str, Any] = {}
    scope = ContextScope(request.session_id, request.thread_id)
    execution_started = False
    observed = None
    request_span = None
    try:
        replay = await context_api.read_terminal(
            scope,
            request.request_id,
            request.payload_digest,
        )
        if replay is not None:
            event = "done" if replay.get("status") == "completed" else "error"
            yield _encode_sse(event, dict(replay))
            return

        frame = progress_frame("preparing")
        if frame is not None:
            yield frame

        if instrumentation is not None:
            observed = instrumentation.bind(
                request_id=request.request_id,
                thread_id=request.thread_id,
                plan_id=f"plan-{request.request_id}",
                step_id=request.step_id,
            )
            request_input = {
                "message": request.message,
                "step_id": request.step_id,
                "request_mode": request.request_mode,
            }
            request_span = observed.start(
                RuntimeStage.REQUEST_PROCESSING,
                input=request_input,
            )

        budget = ExecutionBudget.from_request(request)
        budget.require_time()
        await context_api.begin_execution(scope, request.request_id)
        execution_started = True
        context = await context_api.read_context(scope)
        memories = (
            await memory_manager.retrieve(scope, request.message, context)
            if memory_manager is not None
            else ()
        )
        graph_input = {
            **_graph_input(request),
            **context_api.state_channels(scope, context, memories),
            "execution_budget": budget.to_state(),
        }
        graph_config = {
            **context_api.config_for(scope, request.request_id),
            "callbacks": get_callbacks(),
        }

        frame = progress_frame("planning")
        if frame is not None:
            yield frame

        async with asyncio.timeout(budget.remaining_seconds()):
            graph_events = graph.astream_events(
                graph_input, config=graph_config, version="v2"
            )
            async for graph_event in _iter_with_optional_heartbeat(
                graph_events,
                heartbeat_interval_seconds if progress_events else None,
            ):
                if graph_event is None:
                    frame = heartbeat_frame()
                    if frame is not None:
                        yield frame
                    continue

                phase = _graph_progress_phase(graph_event, request.request_mode)
                if phase is not None:
                    frame = progress_frame(phase)
                    if frame is not None:
                        yield frame

                if graph_event.get("event") == "on_chat_model_stream":
                    continue

                if (
                    graph_event.get("event") == "on_chain_end"
                    and not graph_event.get("parent_ids")
                ):
                    output = graph_event.get("data", {}).get("output")
                    if isinstance(output, dict):
                        final_state = output

        frame = progress_frame("validating")
        if frame is not None:
            yield frame

        output = final_state.get("output", {})
        if not isinstance(output, dict):
            raise ValueError("graph output must be an object")
        response = output.get("response")
        if not isinstance(response, str) or not response:
            raise ValueError("graph output response must be a non-empty string")
        terminal = callback_masker.mask(
            CompletedCallback(
                request_id=request.request_id,
                session_id=request.session_id,
                thread_id=request.thread_id,
                completed_at=utc_now(),
                output=output,
            ).model_dump(mode="json")
        )
        context_payload = final_state.get("conversation_context")
        if not isinstance(context_payload, dict):
            raise ValueError("completed graph state must include conversation_context")
        completed_context = ConversationContext.from_state(context_payload)
        frame = progress_frame("saving")
        if frame is not None:
            yield frame
        committed_terminal = await context_api.commit(
            scope,
            request.request_id,
            request.payload_digest,
            completed_context,
            terminal,
            ConversationTurn(
                turn_id=request.request_id,
                user_message=request.message,
                assistant_message=response,
            ),
        )
        if observed is not None and request_span is not None:
            observed.succeed(request_span, output=output)
            request_span = None
        yield _encode_sse("done", dict(committed_terminal))
    except asyncio.CancelledError:
        if observed is not None and request_span is not None:
            observed.fail(request_span, reason_code="client_disconnected")
            request_span = None
        logger.info("AI v2 stream 연결 종료")
        raise
    except (TimeoutError, asyncio.TimeoutError):
        if observed is not None and request_span is not None:
            observed.fail(
                request_span,
                reason_code="request_timeout",
                output={"error_code": "LLM_TIMEOUT"},
            )
            request_span = None
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
        try:
            terminal = dict(await context_api.commit_terminal(
                scope,
                request.request_id,
                request.payload_digest,
                terminal,
            ))
        except Exception:  # noqa: BLE001 - still return a bounded terminal error
            logger.exception("terminal_receipt_commit_failed")
        yield _encode_sse("error", terminal)
    except Exception as error:
        if observed is not None and request_span is not None:
            observed.fail(
                request_span,
                reason_code="request_processing_failed",
                output={"error_code": "INTERNAL_ERROR"},
            )
            request_span = None
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
        try:
            terminal = dict(await context_api.commit_terminal(
                scope,
                request.request_id,
                request.payload_digest,
                terminal,
            ))
        except Exception:  # noqa: BLE001 - still return a bounded terminal error
            logger.exception("terminal_receipt_commit_failed")
        yield _encode_sse("error", terminal)
    finally:
        if execution_started:
            try:
                await asyncio.shield(
                    context_api.delete_execution(scope, request.request_id)
                )
            except Exception:  # noqa: BLE001 - TTL cleanup remains as fallback
                logger.exception("temporary_context_cleanup_failed")


@app.post("/api/ai/v2/chat/stream")
async def chat_v2_stream(
    raw_request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    progress_events_version: str | None = Header(
        default=None,
        alias="X-AI-Progress-Events",
    ),
):
    verify_service_authorization(
        authorization,
        settings.ai_service_bearer_token,
    )
    request = await _parse_v2_request(raw_request)
    if progress_events_version not in {None, PROGRESS_EVENTS_VERSION}:
        raise ServiceContractError(
            status_code=422,
            code="REQUEST_SCHEMA_INVALID",
            message="지원하지 않는 진행 이벤트 capability입니다.",
        )
    return StreamingResponse(
        stream_v2_chat_events(
            app.state.chat_graph,
            app.state.context_api,
            request,
            app.state.terminal_masker,
            getattr(app.state, "context_memory", None),
            instrumentation=app.state.instrumentation,
            progress_events=progress_events_version == PROGRESS_EVENTS_VERSION,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/system/v1/health")
def health():
    return {"status": "ok"}
