"""AI v2 request parsing and chat execution lifecycle."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import hmac
import json
import logging
from typing import Any, AsyncIterator

from fastapi import Request
from pydantic import ValidationError

from core.context_domain import ContextScope, ConversationContext, ConversationTurn
from core.context_summary_worker import ConversationMemoryManager
from core.model_boundary import ModelOutputBlockedError
from core.monitoring import get_callbacks
from core.observability_masking import masked_exception_fields
from core.runtime_instrumentation import RuntimeStage
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
from core.terminal_masking import TerminalPayloadMasker
from transport.contracts import (
    ChatV2Request,
    CompletedTerminal,
    FailedTerminal,
    ServiceContractError,
    TerminalError,
)
from transport.runtime import ExecutionBudget, utc_now
from transport.sse_presenter import encode_sse


logger = logging.getLogger(__name__)


def validation_error(exc: ValidationError) -> ServiceContractError:
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


async def parse_v2_request(raw_request: Request) -> ChatV2Request:
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
        raise validation_error(exc) from None
    except ServiceContractError:
        raise
    except (TypeError, ValueError):
        raise ServiceContractError(
            status_code=422,
            code="REQUEST_SCHEMA_INVALID",
            message="AI 요청 스키마가 올바르지 않습니다.",
        ) from None


def graph_input(request: ChatV2Request) -> dict[str, Any]:
    customer_info = request.customer_info
    if hasattr(customer_info, "model_dump"):
        customer_info = customer_info.model_dump(mode="json")
    payload = {
        "request_id": request.request_id,
        "thread_id": request.thread_id,
        "message": request.message,
        "step_id": request.step_id,
        "customer_info": customer_info,
    }
    if request.request_mode is not None:
        payload["request_mode"] = request.request_mode
    if request.chart_payload is not None:
        payload["chart_payload"] = request.chart_payload
    return payload


async def _commit_failure(
    *,
    context_api,
    scope: ContextScope,
    request: ChatV2Request,
    terminal_masker: TerminalPayloadMasker,
    code: str,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    terminal = terminal_masker.mask(
        FailedTerminal(
            request_id=request.request_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
            completed_at=utc_now(),
            error=TerminalError(code=code, message=message, retryable=retryable),
        ).model_dump(mode="json")
    )
    try:
        return dict(await context_api.commit_terminal(
            scope,
            request.request_id,
            request.payload_digest,
            terminal,
        ))
    except Exception:  # noqa: BLE001 - still return a bounded terminal error
        logger.exception("terminal_receipt_commit_failed")
        return dict(terminal)


async def stream_v2_chat_events(
    graph,
    context_api,
    request: ChatV2Request,
    terminal_masker: TerminalPayloadMasker,
    memory_manager: ConversationMemoryManager | None = None,
    instrumentation: RuntimeInstrumentationAdapter | None = None,
) -> AsyncIterator[str]:
    identity = {
        "contract_version": "2.0",
        "request_id": request.request_id,
        "session_id": request.session_id,
        "thread_id": request.thread_id,
    }
    yield encode_sse("start", identity)

    final_state: dict[str, Any] = {}
    scope = ContextScope(request.session_id, request.thread_id)
    execution_started = False
    observed = None
    request_span = None
    try:
        replay = await context_api.read_terminal(
            scope, request.request_id, request.payload_digest
        )
        if replay is not None:
            event = "done" if replay.get("status") == "completed" else "error"
            yield encode_sse(event, dict(replay))
            return

        if instrumentation is not None:
            observed = instrumentation.bind(
                request_id=request.request_id,
                thread_id=request.thread_id,
                step_id=request.step_id,
            )
            request_span = observed.start(
                RuntimeStage.REQUEST_PROCESSING,
                input={
                    "message": request.message,
                    "step_id": request.step_id,
                    "request_mode": request.request_mode,
                },
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
        input_state = {
            **graph_input(request),
            **context_api.state_channels(scope, context, memories),
        }
        graph_config = {
            **context_api.config_for(scope, request.request_id),
            "callbacks": get_callbacks(),
        }

        async with asyncio.timeout(budget.remaining_seconds()):
            async for graph_event in graph.astream_events(
                input_state, config=graph_config, version="v2"
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
        response = output.get("response")
        if not isinstance(response, str) or not response:
            raise ValueError("graph output response must be a non-empty string")
        terminal = terminal_masker.mask(
            CompletedTerminal(
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
        committed_terminal = await context_api.commit(
            scope,
            request.request_id,
            request.payload_digest,
            ConversationContext.from_state(context_payload),
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
        yield encode_sse("done", dict(committed_terminal))
    except asyncio.CancelledError:
        if observed is not None and request_span is not None:
            observed.fail(request_span, reason_code="client_disconnected")
            request_span = None
        logger.info("AI v2 stream 연결 종료")
        raise
    except ModelOutputBlockedError:
        if observed is not None and request_span is not None:
            observed.fail(
                request_span,
                reason_code="policy_blocked",
                output={"error_code": "POLICY_BLOCKED"},
            )
            request_span = None
        terminal = await _commit_failure(
            context_api=context_api,
            scope=scope,
            request=request,
            terminal_masker=terminal_masker,
            code="POLICY_BLOCKED",
            message="AI 응답이 보안 정책을 통과하지 못했습니다.",
            retryable=False,
        )
        yield encode_sse("error", terminal)
    except (TimeoutError, asyncio.TimeoutError):
        if observed is not None and request_span is not None:
            observed.fail(
                request_span,
                reason_code="request_timeout",
                output={"error_code": "LLM_TIMEOUT"},
            )
            request_span = None
        terminal = await _commit_failure(
            context_api=context_api,
            scope=scope,
            request=request,
            terminal_masker=terminal_masker,
            code="LLM_TIMEOUT",
            message="AI 응답 생성 시간이 초과되었습니다.",
            retryable=True,
        )
        yield encode_sse("error", terminal)
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
                    error, request_id=str(request.request_id)
                ),
            },
        )
        terminal = await _commit_failure(
            context_api=context_api,
            scope=scope,
            request=request,
            terminal_masker=terminal_masker,
            code="INTERNAL_ERROR",
            message="AI 응답 생성에 실패했습니다.",
            retryable=False,
        )
        yield encode_sse("error", terminal)
    finally:
        if execution_started:
            try:
                await asyncio.shield(
                    context_api.delete_execution(scope, request.request_id)
                )
            except Exception:  # noqa: BLE001 - TTL cleanup remains as fallback
                logger.exception("temporary_context_cleanup_failed")
