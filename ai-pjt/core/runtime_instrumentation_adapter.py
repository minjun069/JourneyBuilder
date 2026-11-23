"""Producer-facing runtime instrumentation for F10-T02.

The graph must receive this adapter explicitly.  Export policy remains a sink
concern: before F11-T03 installs a masking sink, production uses the explicit
``ExportBlockedInstrumentationSink`` instead of an implicit no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic, time
from typing import Any, Callable

from core.monitoring import OperationStage, OperationStatus, OperationTraceEvent
from core.prompt_registry import PromptInvocationRecord
from core.runtime_instrumentation import (
    InstrumentationEvent,
    InstrumentationPort,
    RuntimeStage,
    RuntimeStatus,
    RuntimeTraceEvent,
    SecurityDecision,
    SecurityDecisionTraceEvent,
)


def _epoch_ms() -> int:
    return int(time() * 1000)


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{sha256(material).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class RuntimeCorrelation:
    trace_id: str
    attempt_id: str
    request_id: str
    thread_id: str
    step_id: str


@dataclass(frozen=True, slots=True)
class RuntimeStageSpan:
    correlation: RuntimeCorrelation
    stage: RuntimeStage
    started_at_monotonic: float
    task_id: str | None = None
    depends_on: tuple[str, ...] = ()
    executor_kind: str | None = None
    executor_name: str | None = None
    artifact_id: str | None = None
    artifact_type: str | None = None
    producer_task_id: str | None = None
    input: Any | None = None


class ExportBlockedInstrumentationSink:
    """Explicitly discard typed events until a reviewed masked sink is wired."""

    def __init__(self) -> None:
        self._discarded_count = 0

    def emit(self, _event: InstrumentationEvent) -> None:
        self._discarded_count += 1

    @property
    def discarded_count(self) -> int:
        return self._discarded_count


class RuntimeInstrumentationAdapter:
    """Create validated orchestration and operation events for one graph."""

    __slots__ = ("_port", "_epoch_ms", "_monotonic")

    def __init__(
        self,
        port: InstrumentationPort,
        *,
        epoch_ms: Callable[[], int] = _epoch_ms,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isinstance(port, InstrumentationPort):
            raise TypeError("instrumentation port is required")
        if not callable(epoch_ms) or not callable(monotonic_clock):
            raise TypeError("instrumentation clocks must be callable")
        self._port = port
        self._epoch_ms = epoch_ms
        self._monotonic = monotonic_clock

    def bind(
        self,
        *,
        request_id: str,
        thread_id: str,
        step_id: str,
    ) -> "BoundRuntimeInstrumentation":
        for name, value in (
            ("request_id", request_id),
            ("thread_id", thread_id),
            ("step_id", step_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} is required for instrumentation")
        correlation = RuntimeCorrelation(
            trace_id=_stable_id("trace", request_id),
            attempt_id=_stable_id("attempt", request_id, step_id),
            request_id=request_id,
            thread_id=thread_id,
            step_id=step_id,
        )
        return BoundRuntimeInstrumentation(
            port=self._port,
            correlation=correlation,
            epoch_ms=self._epoch_ms,
            monotonic_clock=self._monotonic,
        )

class BoundRuntimeInstrumentation:
    """Instrumentation API bound to one request, thread, plan, and step."""

    __slots__ = ("_port", "correlation", "_epoch_ms", "_monotonic")

    def __init__(
        self,
        *,
        port: InstrumentationPort,
        correlation: RuntimeCorrelation,
        epoch_ms: Callable[[], int],
        monotonic_clock: Callable[[], float],
    ) -> None:
        self._port = port
        self.correlation = correlation
        self._epoch_ms = epoch_ms
        self._monotonic = monotonic_clock

    def start(
        self,
        stage: RuntimeStage,
        *,
        task_id: str | None = None,
        depends_on: tuple[str, ...] = (),
        executor_kind: str | None = None,
        executor_name: str | None = None,
        artifact_id: str | None = None,
        artifact_type: str | None = None,
        producer_task_id: str | None = None,
        input: Any | None = None,
    ) -> RuntimeStageSpan:
        if stage is RuntimeStage.DEPENDENCY_BLOCK:
            raise ValueError("dependency blocks are terminal-only events")
        span = RuntimeStageSpan(
            correlation=self.correlation,
            stage=stage,
            started_at_monotonic=self._monotonic(),
            task_id=task_id,
            depends_on=depends_on,
            executor_kind=executor_kind,
            executor_name=executor_name,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            producer_task_id=producer_task_id,
            input=input,
        )
        self._emit_runtime(span, status=RuntimeStatus.STARTED)
        return span

    def succeed(
        self,
        span: RuntimeStageSpan,
        *,
        artifact_id: str | None = None,
        artifact_type: str | None = None,
        producer_task_id: str | None = None,
        response_owner: str | None = None,
        output: Any | None = None,
    ) -> None:
        self._emit_runtime(
            span,
            status=RuntimeStatus.SUCCEEDED,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            producer_task_id=producer_task_id,
            response_owner=response_owner,
            output=output,
        )

    def fail(
        self,
        span: RuntimeStageSpan,
        *,
        reason_code: str,
        output: Any | None = None,
        error_type: str | None = None,
        error_fingerprint: str | None = None,
    ) -> None:
        self._emit_runtime(
            span,
            status=RuntimeStatus.FAILED,
            reason_code=reason_code,
            output=output,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
        )

    def record_block(
        self,
        *,
        task_id: str,
        depends_on: tuple[str, ...],
        blocked_by: tuple[str, ...],
        reason_code: str = "dependency_failed",
    ) -> None:
        span = RuntimeStageSpan(
            correlation=self.correlation,
            stage=RuntimeStage.DEPENDENCY_BLOCK,
            started_at_monotonic=self._monotonic(),
            task_id=task_id,
            depends_on=depends_on,
        )
        self._emit_runtime(
            span,
            status=RuntimeStatus.BLOCKED,
            blocked_by=blocked_by,
            reason_code=reason_code,
        )

    def record_operation(
        self,
        *,
        stage: OperationStage,
        status: OperationStatus,
        operation_name: str,
        duration_ms: int,
        task_id: str | None = None,
        agent: str | None = None,
        error_code: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_temperature: float | None = None,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
        prompt_release: str | None = None,
        prompt_policy_version: str | None = None,
        index_version: str | None = None,
        tool_version: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_output_tokens: int | None = None,
        call_id: str | None = None,
        usage_status: str | None = None,
        cost_microusd: int | None = None,
        resource_ids: tuple[str, ...] = (),
        scores: tuple[float, ...] = (),
        result_count: int | None = None,
        input: Any | None = None,
        output: Any | None = None,
        error_type: str | None = None,
        error_fingerprint: str | None = None,
    ) -> None:
        self._port.emit(OperationTraceEvent(
            trace_id=self.correlation.trace_id,
            attempt_id=self.correlation.attempt_id,
            occurred_at_epoch_ms=self._epoch_ms(),
            stage=stage,
            status=status,
            duration_ms=duration_ms,
            operation_name=operation_name,
            step_id=self.correlation.step_id,
            task_id=task_id,
            agent=agent,
            error_code=error_code,
            model_provider=model_provider,
            model_name=model_name,
            model_temperature=model_temperature,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            prompt_release=prompt_release,
            prompt_policy_version=prompt_policy_version,
            index_version=index_version,
            tool_version=tool_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            call_id=call_id,
            usage_status=usage_status,
            cost_microusd=cost_microusd,
            resource_ids=resource_ids,
            scores=scores,
            result_count=result_count,
            input=input,
            output=output,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
        ))

    def record_model_call(
        self,
        *,
        purpose: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_output_tokens: int | None = None,
        call_id: str | None = None,
        usage_status: str | None = None,
        operation_name: str | None = None,
        status: str,
        duration_ms: int,
        input: Any,
        output: Any,
        task_id: str | None = None,
        error_code: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        model_temperature: float | None = None,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
        prompt_release: str | None = None,
        prompt_policy_version: str | None = None,
        error_type: str | None = None,
        error_fingerprint: str | None = None,
    ) -> None:
        """Record one actual provider invocation made by the model boundary."""

        from core.config import settings
        from core.model_boundary import PROMPT_POLICY_VERSION

        operation_status = OperationStatus(status)
        if operation_status not in {
            OperationStatus.STARTED,
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.TIMEOUT,
        }:
            raise ValueError("model call status must be terminal")
        self.record_operation(
            stage=OperationStage.MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            call_id=call_id,
            usage_status=usage_status,
            status=operation_status,
            operation_name=operation_name or f"{purpose}.generate",
            duration_ms=duration_ms,
            task_id=task_id,
            agent=purpose,
            error_code=(
                None
                if operation_status in {OperationStatus.SUCCEEDED, OperationStatus.STARTED}
                else error_code or "model_call_failed"
            ),
            model_provider=model_provider or settings.llm_provider,
            model_name=model_name or settings.llm_model,
            model_temperature=(
                float(getattr(settings, "llm_temperature", 0.0))
                if model_temperature is None
                else model_temperature
            ),
            prompt_id=prompt_id,
            prompt_version=(
                prompt_version or f"data-policy-{PROMPT_POLICY_VERSION}"
            ),
            prompt_hash=prompt_hash,
            prompt_release=prompt_release,
            prompt_policy_version=(
                prompt_policy_version or PROMPT_POLICY_VERSION
            ),
            input=input,
            output=output,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
        )

    def record_prompt_invocation(self, record: PromptInvocationRecord) -> None:
        """Translate one physical model call into a version-complete event."""

        if not isinstance(record, PromptInvocationRecord):
            raise TypeError("prompt invocation must use PromptInvocationRecord")
        status = (
            OperationStatus.SUCCEEDED
            if record.status == "succeeded"
            else OperationStatus.FAILED
        )
        self.record_operation(
            stage=OperationStage.MODEL,
            status=status,
            operation_name=record.prompt_id,
            duration_ms=record.duration_ms,
            agent=record.purpose,
            error_code=record.error_code,
            model_provider=record.model_provider,
            model_name=record.model_name,
            model_temperature=record.model_temperature,
            prompt_id=record.prompt_id,
            prompt_version=record.prompt_version,
            prompt_hash=record.prompt_hash,
            prompt_release=record.prompt_release,
            prompt_policy_version=record.prompt_policy_version,
        )

    def record_tool_audit(self, event: object) -> None:
        """Translate a tool audit without accepting values or raw arguments."""

        required = {
            "request_id": self.correlation.request_id,
            "step_id": self.correlation.step_id,
        }
        for field_name, expected in required.items():
            if getattr(event, field_name, None) != expected:
                raise ValueError(f"tool audit {field_name} does not match correlation")
        decision_value = getattr(event, "decision", None)
        try:
            decision = SecurityDecision(decision_value)
        except ValueError as exc:
            raise ValueError("tool audit decision is invalid") from exc
        self.record_security_decision(
            task_id=str(getattr(event, "task_id")),
            boundary="tool_authorization",
            decision=decision,
            actor_kind="agent",
            actor_name=str(getattr(event, "agent")),
            target_name=str(getattr(event, "tool_name")),
            target_version=str(getattr(event, "tool_version")),
            argument_classes=tuple(getattr(event, "argument_classes")),
            call_source=str(getattr(event, "call_source")),
            reason_code=getattr(event, "error_code", None),
        )

    def record_security_decision(
        self,
        *,
        task_id: str,
        boundary: str,
        decision: SecurityDecision,
        actor_kind: str,
        actor_name: str,
        target_name: str,
        target_version: str,
        argument_classes: tuple[str, ...] = (),
        call_source: str = "graph_task",
        reason_code: str | None = None,
    ) -> None:
        self._port.emit(SecurityDecisionTraceEvent(
            trace_id=self.correlation.trace_id,
            request_id=self.correlation.request_id,
            thread_id=self.correlation.thread_id,
            step_id=self.correlation.step_id,
            task_id=task_id,
            occurred_at_epoch_ms=self._epoch_ms(),
            boundary=boundary,
            decision=decision,
            actor_kind=actor_kind,
            actor_name=actor_name,
            target_name=target_name,
            target_version=target_version,
            argument_classes=argument_classes,
            call_source=call_source,
            reason_code=reason_code,
        ))

    def elapsed_ms(self, span: RuntimeStageSpan) -> int:
        return max(0, int((self._monotonic() - span.started_at_monotonic) * 1000))

    def _emit_runtime(
        self,
        span: RuntimeStageSpan,
        *,
        status: RuntimeStatus,
        blocked_by: tuple[str, ...] = (),
        reason_code: str | None = None,
        artifact_id: str | None = None,
        artifact_type: str | None = None,
        producer_task_id: str | None = None,
        response_owner: str | None = None,
        output: Any | None = None,
        error_type: str | None = None,
        error_fingerprint: str | None = None,
    ) -> None:
        terminal = status is not RuntimeStatus.STARTED
        self._port.emit(RuntimeTraceEvent(
            trace_id=span.correlation.trace_id,
            request_id=span.correlation.request_id,
            thread_id=span.correlation.thread_id,
            occurred_at_epoch_ms=self._epoch_ms(),
            stage=span.stage,
            status=status,
            task_id=span.task_id,
            depends_on=span.depends_on,
            blocked_by=blocked_by,
            artifact_id=artifact_id or span.artifact_id,
            artifact_type=artifact_type or span.artifact_type,
            producer_task_id=producer_task_id or span.producer_task_id,
            duration_ms=self.elapsed_ms(span) if terminal else None,
            reason_code=reason_code,
            executor_kind=span.executor_kind,
            executor_name=span.executor_name,
            response_owner=response_owner,
            input=span.input if status is RuntimeStatus.STARTED else None,
            output=output,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
        ))


def build_export_blocked_instrumentation() -> tuple[
    RuntimeInstrumentationAdapter,
    ExportBlockedInstrumentationSink,
]:
    """Create the explicit pre-F11 production policy used by startup."""

    sink = ExportBlockedInstrumentationSink()
    return RuntimeInstrumentationAdapter(InstrumentationPort(sink)), sink
