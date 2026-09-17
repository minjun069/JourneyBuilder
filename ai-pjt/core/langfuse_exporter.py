"""Langfuse v4 exporter for already-masked typed instrumentation payloads."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite
import re
from threading import RLock
from typing import Any, Protocol


class _LangfuseObservation(Protocol):
    def start_observation(self, **kwargs: Any) -> "_LangfuseObservation": ...

    def update(self, **kwargs: Any) -> object: ...

    def end(self) -> object: ...


class _LangfuseClient(Protocol):
    def start_observation(self, **kwargs: Any) -> _LangfuseObservation: ...

    def shutdown(self) -> object: ...


_MASKED_TRACE_ID = re.compile(r"^trace_[0-9a-f]{24}$")
_SAFE_METADATA_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,511}$")
_FORBIDDEN_CONTENT_FIELDS = frozenset(
    {
        "arguments",
        "content",
        "customer_info",
        "message",
        "prompt",
        "query",
        "response",
        "tool_context",
    }
)
_OBSERVATION_CONTENT_FIELDS = frozenset({"input", "output"})
_ROOT_METADATA_FIELDS = frozenset(
    {"attempt_id", "plan_id", "request_id", "step_id", "thread_id"}
)
_OBSERVATION_METADATA_FIELDS_BY_FAMILY = {
    "operation": frozenset(
        {
            "agent",
            "error_code",
            "error_fingerprint",
            "error_type",
            "index_version",
            "model_provider",
            "operation_name",
            "prompt_version",
            "resource_ids",
            "result_count",
            "scores",
            "tool_version",
        }
    ),
    "attempt": frozenset({"assistance_mode", "transition_reason"}),
    "proposal_decision": frozenset({"proposal_id"}),
    "change": frozenset({"change_id", "rollback_reason"}),
    "survey": frozenset({"effort_reduction", "helpfulness", "trust"}),
    "runtime": frozenset(
        {
            "artifact_id",
            "artifact_type",
            "blocked_by",
            "depends_on",
            "error_fingerprint",
            "error_type",
            "executor_kind",
            "executor_name",
            "producer_task_id",
            "reason_code",
            "response_owner",
            "task_id",
        }
    ),
    "security_decision": frozenset(
        {
            "actor_kind",
            "actor_name",
            "argument_classes",
            "call_source",
            "reason_code",
            "target_name",
            "target_version",
            "task_id",
        }
    ),
}
_COMMON_FIELDS = frozenset(
    {
        "contract_version",
        "event_family",
        "masking_status",
        "trace_id",
        "occurred_at_epoch_ms",
    }
)
_ALLOWED_FIELDS_BY_FAMILY = {
    "operation": _COMMON_FIELDS
    | {
        "agent",
        "attempt_id",
        "cost_microusd",
        "duration_ms",
        "error_code",
        "error_fingerprint",
        "error_type",
        "index_version",
        "input",
        "input_tokens",
        "model_name",
        "model_provider",
        "operation_name",
        "output",
        "output_tokens",
        "prompt_version",
        "resource_ids",
        "result_count",
        "scores",
        "stage",
        "status",
        "step_id",
        "tool_version",
    },
    "attempt": _COMMON_FIELDS
    | {
        "assistance_mode",
        "attempt_id",
        "duration_ms",
        "event_type",
        "step_id",
        "transition_reason",
    },
    "proposal_decision": _COMMON_FIELDS
    | {"attempt_id", "outcome", "proposal_id"},
    "change": _COMMON_FIELDS
    | {"attempt_id", "change_id", "event_type", "rollback_reason"},
    "survey": _COMMON_FIELDS
    | {"attempt_id", "effort_reduction", "helpfulness", "trust"},
    "runtime": _COMMON_FIELDS
    | {
        "artifact_id",
        "artifact_type",
        "blocked_by",
        "depends_on",
        "duration_ms",
        "error_fingerprint",
        "error_type",
        "executor_kind",
        "executor_name",
        "plan_id",
        "producer_task_id",
        "reason_code",
        "request_id",
        "response_owner",
        "input",
        "output",
        "stage",
        "status",
        "task_id",
        "thread_id",
    },
    "security_decision": _COMMON_FIELDS
    | {
        "actor_kind",
        "actor_name",
        "argument_classes",
        "boundary",
        "call_source",
        "decision",
        "plan_id",
        "reason_code",
        "request_id",
        "step_id",
        "target_name",
        "target_version",
        "task_id",
        "thread_id",
    },
}
_TOKEN_LIST_FIELDS = frozenset(
    {"argument_classes", "blocked_by", "depends_on", "resource_ids"}
)
_MAX_RETAINED_TRACES = 1_024


@dataclass(slots=True)
class _OpenObservation:
    observation: _LangfuseObservation
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _TraceState:
    root: _LangfuseObservation
    root_metadata: dict[str, Any]
    observations: dict[tuple[str, ...], _OpenObservation] = field(
        default_factory=dict
    )
    ended: bool = False


class LangfuseInstrumentationExporter:
    """Create one request-rooted Langfuse lifecycle from masked mappings.

    ``MaskedInstrumentationSink`` is the only production caller. Input and
    output are accepted only inside a payload carrying that sink's explicit
    masking marker; raw model callbacks remain unsupported. Runtime start and
    terminal events update the same child observation instead of creating two.
    """

    __slots__ = ("_client", "_lock", "_traces")

    def __init__(self, client: _LangfuseClient) -> None:
        if not callable(getattr(client, "start_observation", None)):
            raise TypeError("Langfuse client must provide start_observation")
        if not callable(getattr(client, "shutdown", None)):
            raise TypeError("Langfuse client must provide shutdown")
        self._client = client
        self._lock = RLock()
        self._traces: OrderedDict[str, _TraceState] = OrderedDict()

    def emit(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError("Langfuse exporter accepts only masked mappings")

        trace_id = _required_token(payload, "trace_id")
        if _MASKED_TRACE_ID.fullmatch(trace_id) is None:
            raise ValueError("Langfuse exporter requires a masked trace_id")
        if payload.get("masking_status") != "applied":
            raise ValueError("Langfuse exporter requires masking_status=applied")
        forbidden_fields = _FORBIDDEN_CONTENT_FIELDS.intersection(payload)
        if forbidden_fields:
            raise ValueError(
                "Langfuse exporter rejects content-bearing fields: "
                + ", ".join(sorted(forbidden_fields))
            )
        event_family = _required_token(payload, "event_family")
        contract_version = _required_token(payload, "contract_version")
        _validate_metadata_payload(payload, event_family)
        otel_trace_id = _otel_trace_id(trace_id)
        with self._lock:
            state = self._trace_state(
                otel_trace_id=otel_trace_id,
                payload=payload,
                contract_version=contract_version,
            )
            self._traces.move_to_end(otel_trace_id)
            self._merge_root_metadata(state, payload)

            if _is_request_lifecycle(payload):
                self._update_request_root(state, payload)
                self._evict_closed_traces()
                return

            lifecycle_key = _runtime_lifecycle_key(payload)
            if lifecycle_key is not None and payload.get("status") == "started":
                self._start_runtime_observation(
                    state,
                    lifecycle_key,
                    payload,
                    contract_version,
                )
                self._evict_closed_traces()
                return

            if lifecycle_key is not None and lifecycle_key in state.observations:
                self._finish_runtime_observation(
                    state,
                    lifecycle_key,
                    payload,
                )
                self._evict_closed_traces()
                return

            observation = state.root.start_observation(
                **_observation_args(payload, contract_version)
            )
            observation.end()
            self._evict_closed_traces()

    def shutdown(self) -> None:
        failure: Exception | None = None
        with self._lock:
            for state in self._traces.values():
                for open_observation in state.observations.values():
                    try:
                        open_observation.observation.end()
                    except Exception as error:  # noqa: BLE001 - flush all spans
                        failure = failure or error
                if not state.ended:
                    try:
                        state.root.end()
                    except Exception as error:  # noqa: BLE001 - still stop client
                        failure = failure or error
            self._traces.clear()
        try:
            self._client.shutdown()
        except Exception as error:  # noqa: BLE001 - preserve first lifecycle error
            failure = failure or error
        if failure is not None:
            raise failure

    def _trace_state(
        self,
        *,
        otel_trace_id: str,
        payload: Mapping[str, Any],
        contract_version: str,
    ) -> _TraceState:
        existing = self._traces.get(otel_trace_id)
        if existing is not None:
            return existing

        root_metadata = _root_metadata(payload)
        root_args: dict[str, Any] = {
            "trace_context": {"trace_id": otel_trace_id},
            "name": "retirewise.request",
            "as_type": "span",
            "metadata": root_metadata,
            "version": contract_version,
        }
        if "input" in payload:
            root_args["input"] = payload["input"]
        root = self._client.start_observation(**root_args)
        state = _TraceState(root=root, root_metadata=root_metadata)
        self._traces[otel_trace_id] = state
        return state

    def _merge_root_metadata(
        self,
        state: _TraceState,
        payload: Mapping[str, Any],
    ) -> None:
        if state.ended:
            return
        metadata = {**state.root_metadata, **_root_metadata(payload)}
        if metadata == state.root_metadata:
            return
        state.root.update(metadata=metadata)
        state.root_metadata = metadata

    def _update_request_root(
        self,
        state: _TraceState,
        payload: Mapping[str, Any],
    ) -> None:
        if state.ended:
            return
        metadata = {**state.root_metadata, **_root_metadata(payload)}
        update_args = _content_update_args(payload, metadata=metadata)
        state.root.update(**update_args)
        state.root_metadata = metadata
        if payload.get("status") == "started":
            return

        for open_observation in state.observations.values():
            open_observation.observation.end()
        state.observations.clear()
        state.root.end()
        state.ended = True

    def _start_runtime_observation(
        self,
        state: _TraceState,
        lifecycle_key: tuple[str, ...],
        payload: Mapping[str, Any],
        contract_version: str,
    ) -> None:
        existing = state.observations.get(lifecycle_key)
        if existing is not None:
            metadata = {
                **existing.metadata,
                **_observation_metadata(payload),
            }
            existing.observation.update(
                **_content_update_args(
                    payload,
                    metadata=metadata,
                )
            )
            existing.metadata = metadata
            return
        metadata = _observation_metadata(payload)
        observation = state.root.start_observation(
            **_observation_args(payload, contract_version, metadata=metadata)
        )
        state.observations[lifecycle_key] = _OpenObservation(
            observation=observation,
            metadata=metadata,
        )

    def _finish_runtime_observation(
        self,
        state: _TraceState,
        lifecycle_key: tuple[str, ...],
        payload: Mapping[str, Any],
    ) -> None:
        open_observation = state.observations[lifecycle_key]
        metadata = {
            **open_observation.metadata,
            **_observation_metadata(payload),
        }
        open_observation.observation.update(
            **_content_update_args(payload, metadata=metadata)
        )
        open_observation.observation.end()
        del state.observations[lifecycle_key]

    def _evict_closed_traces(self) -> None:
        if len(self._traces) <= _MAX_RETAINED_TRACES:
            return
        for trace_id, state in tuple(self._traces.items()):
            if state.ended:
                del self._traces[trace_id]
                if len(self._traces) <= _MAX_RETAINED_TRACES:
                    return


def build_langfuse_instrumentation_exporter(
    *,
    public_key: str,
    secret_key: str,
    base_url: str,
) -> LangfuseInstrumentationExporter:
    """Build an isolated masked-event client without third-party span export."""

    from langfuse import Langfuse, is_langfuse_span

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        tracing_enabled=True,
        should_export_span=is_langfuse_span,
    )
    return LangfuseInstrumentationExporter(client)


def _required_token(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"masked instrumentation payload requires {name}")
    return value


def _validate_metadata_payload(
    payload: Mapping[str, Any],
    event_family: str,
) -> None:
    allowed_fields = _ALLOWED_FIELDS_BY_FAMILY.get(event_family)
    if allowed_fields is None:
        raise ValueError("Langfuse exporter rejects unknown event_family")
    if not all(isinstance(name, str) for name in payload):
        raise ValueError("Langfuse exporter requires string metadata fields")

    unexpected_fields = set(payload).difference(allowed_fields)
    if unexpected_fields:
        raise ValueError(
            "Langfuse exporter rejects unapproved fields: "
            + ", ".join(sorted(unexpected_fields))
        )

    for name, value in payload.items():
        if name in _OBSERVATION_CONTENT_FIELDS:
            _validate_observation_content(value, name)
            continue
        if isinstance(value, str):
            if _SAFE_METADATA_TOKEN.fullmatch(value) is None:
                raise ValueError(
                    f"Langfuse exporter requires machine-token metadata: {name}"
                )
            continue
        if isinstance(value, bool):
            raise ValueError(f"Langfuse exporter rejects boolean metadata: {name}")
        if isinstance(value, int):
            if value < 0:
                raise ValueError(
                    f"Langfuse exporter requires non-negative metadata: {name}"
                )
            continue
        if isinstance(value, list):
            if name in _TOKEN_LIST_FIELDS and all(
                isinstance(item, str)
                and _SAFE_METADATA_TOKEN.fullmatch(item) is not None
                for item in value
            ):
                continue
            if name == "scores" and all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and isfinite(item)
                for item in value
            ):
                continue
        raise ValueError(f"Langfuse exporter rejects metadata shape: {name}")


def _validate_observation_content(value: Any, name: str) -> None:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Langfuse exporter requires JSON-safe observation {name}"
        ) from error


def _root_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: payload[name]
        for name in _ROOT_METADATA_FIELDS
        if name in payload
    }


def _observation_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = _OBSERVATION_METADATA_FIELDS_BY_FAMILY[
        str(payload["event_family"])
    ]
    return {name: payload[name] for name in fields if name in payload}


def _failure_args(payload: Mapping[str, Any]) -> dict[str, str]:
    if (
        payload.get("status") not in {"failed", "timeout", "blocked"}
        and payload.get("decision") != "denied"
    ):
        return {}
    args = {"level": "ERROR"}
    for name in ("reason_code", "error_code", "status", "decision"):
        value = payload.get(name)
        if isinstance(value, str) and value:
            args["status_message"] = value
            break
    return args


def _content_update_args(
    payload: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    args: dict[str, Any] = {"metadata": dict(metadata)}
    for field_name in _OBSERVATION_CONTENT_FIELDS:
        if field_name in payload:
            args[field_name] = payload[field_name]
    args.update(_failure_args(payload))
    return args


def _observation_args(
    payload: Mapping[str, Any],
    contract_version: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    event_family = str(payload["event_family"])
    args: dict[str, Any] = {
        "name": f"retirewise.{event_family}.{_event_action(payload)}",
        "as_type": "span",
        "metadata": dict(
            _observation_metadata(payload) if metadata is None else metadata
        ),
        "version": contract_version,
    }
    for field_name in _OBSERVATION_CONTENT_FIELDS:
        if field_name in payload:
            args[field_name] = payload[field_name]
    args.update(_failure_args(payload))
    if event_family == "operation" and payload.get("stage") == "model":
        args.update(_generation_args(payload))
    return args


def _is_request_lifecycle(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("event_family") == "runtime"
        and payload.get("stage") == "request_processing"
    )


def _runtime_lifecycle_key(
    payload: Mapping[str, Any],
) -> tuple[str, ...] | None:
    if payload.get("event_family") != "runtime":
        return None
    stage = payload.get("stage")
    if not isinstance(stage, str) or stage == "dependency_block":
        return None
    return tuple(
        str(payload.get(name) or "")
        for name in (
            "stage",
            "task_id",
            "executor_kind",
            "executor_name",
            "artifact_id",
        )
    )


def _event_action(payload: Mapping[str, Any]) -> str:
    for name in ("stage", "event_type", "boundary", "outcome", "decision"):
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    return "event"


def _otel_trace_id(masked_trace_id: str) -> str:
    return sha256(f"retirewise:{masked_trace_id}".encode("utf-8")).hexdigest()[:32]


def _generation_args(payload: Mapping[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {"as_type": "generation"}
    model_name = payload.get("model_name")
    if isinstance(model_name, str) and model_name:
        args["model"] = model_name

    usage_details: dict[str, int] = {}
    for source, target in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        value = payload.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage_details[target] = value
    if usage_details:
        args["usage_details"] = usage_details

    cost_microusd = payload.get("cost_microusd")
    if (
        isinstance(cost_microusd, int)
        and not isinstance(cost_microusd, bool)
        and cost_microusd >= 0
    ):
        args["cost_details"] = {"total_cost": cost_microusd / 1_000_000}
    return args
