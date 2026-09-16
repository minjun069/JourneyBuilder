"""Langfuse v4 exporter for already-masked typed instrumentation payloads."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any, Protocol


class _LangfuseObservation(Protocol):
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


class LangfuseInstrumentationExporter:
    """Create Langfuse observations from masked mappings only.

    ``MaskedInstrumentationSink`` is the only production caller. Input and
    output are accepted only inside a payload carrying that sink's explicit
    masking marker; raw model callbacks remain unsupported.
    """

    __slots__ = ("_client",)

    def __init__(self, client: _LangfuseClient) -> None:
        if not callable(getattr(client, "start_observation", None)):
            raise TypeError("Langfuse client must provide start_observation")
        if not callable(getattr(client, "shutdown", None)):
            raise TypeError("Langfuse client must provide shutdown")
        self._client = client

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
        action = _event_action(payload)
        status = payload.get("status")
        decision = payload.get("decision")

        observation_args: dict[str, Any] = {
            "trace_context": {"trace_id": _otel_trace_id(trace_id)},
            "name": f"retirewise.{event_family}.{action}",
            "as_type": "span",
            "metadata": {
                name: value
                for name, value in payload.items()
                if name not in _OBSERVATION_CONTENT_FIELDS
            },
            "version": contract_version,
        }
        for field_name in _OBSERVATION_CONTENT_FIELDS:
            if field_name in payload:
                observation_args[field_name] = payload[field_name]
        if status in {"failed", "timeout", "blocked"} or decision == "denied":
            observation_args["level"] = "ERROR"

        if event_family == "operation" and payload.get("stage") == "model":
            observation_args.update(_generation_args(payload))

        observation = self._client.start_observation(**observation_args)
        observation.end()

    def shutdown(self) -> None:
        self._client.shutdown()


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
