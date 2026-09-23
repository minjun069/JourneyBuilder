"""FastAPI application assembly for the RetireWise AI service."""

from contextlib import asynccontextmanager
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
                response_composer=build_response_composer(),
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


@app.post("/api/ai/v2/chat/stream")
async def chat_v2_stream(
    raw_request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    verify_service_authorization(
        authorization,
        settings.ai_service_bearer_token,
    )
    request = await parse_v2_request(raw_request)
    return StreamingResponse(
        stream_v2_chat_events(
            app.state.chat_graph,
            app.state.context_api,
            request,
            app.state.terminal_masker,
            getattr(app.state, "context_memory", None),
            instrumentation=app.state.instrumentation,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/system/v1/health")
def health():
    return {"status": "ok"}
