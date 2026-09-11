"""Masking-safe log and trace serialization for typed instrumentation events."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, Protocol

from core.masking import MaskingPolicy, MaskingSink, mask_payload
from core.runtime_instrumentation import (
    InstrumentationEvent,
    InstrumentationPort,
)
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter


class SerializedInstrumentationSink(Protocol):
    def emit(self, payload: Mapping[str, Any]) -> None: ...


def mask_application_log_payload(
    payload: Any,
    *,
    policy: MaskingPolicy,
) -> Any:
    return mask_payload(
        payload,
        sink=MaskingSink.APPLICATION_LOG,
        policy=policy,
    )


def mask_trace_payload(
    payload: Any,
    *,
    policy: MaskingPolicy,
) -> Any:
    return mask_payload(
        payload,
        sink=MaskingSink.TRACE_EXPORT,
        policy=policy,
    )


class MaskedInstrumentationSink:
    """Serialize typed events, mask them, and isolate exporter failures.

    The downstream receives only the masked mapping. A masking or exporter
    failure drops that one export and never retries with the raw event.
    """

    __slots__ = ("_downstream", "_policy", "_sink", "_dropped_exports")

    def __init__(
        self,
        *,
        policy: MaskingPolicy,
        sink: MaskingSink,
        downstream: SerializedInstrumentationSink,
    ) -> None:
        if not isinstance(policy, MaskingPolicy):
            raise TypeError("MaskedInstrumentationSink requires a MaskingPolicy")
        if sink not in {MaskingSink.APPLICATION_LOG, MaskingSink.TRACE_EXPORT}:
            raise ValueError(
                "instrumentation sink must be application_log or trace_export"
            )
        if not callable(getattr(downstream, "emit", None)):
            raise TypeError("serialized instrumentation downstream is required")
        self._policy = policy
        self._sink = sink
        self._downstream = downstream
        self._dropped_exports = 0

    def emit(self, event: InstrumentationEvent) -> None:
        try:
            raw = event.to_dict()
            masked = mask_payload(raw, sink=self._sink, policy=self._policy)
            if not isinstance(masked, Mapping):
                raise TypeError("masked instrumentation event must be a mapping")
            self._downstream.emit(masked)
        except Exception:
            self._dropped_exports += 1

    @property
    def dropped_exports(self) -> int:
        return self._dropped_exports


class StructuredLogInstrumentationExporter:
    """Write an already-masked event to the application logging backend."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        if not isinstance(logger, logging.Logger):
            raise TypeError("a logging.Logger is required")
        self._logger = logger

    def emit(self, payload: Mapping[str, Any]) -> None:
        self._logger.info(
            "ai_instrumentation_event",
            extra={"instrumentation_event": dict(payload)},
        )


class FakeSerializedInstrumentationSink:
    """In-memory sink that can also simulate an unavailable trace store."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._payloads: list[dict[str, Any]] = []

    def emit(self, payload: Mapping[str, Any]) -> None:
        self._payloads.append(dict(payload))
        if self._fail:
            raise RuntimeError("simulated instrumentation store outage")

    @property
    def payloads(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._payloads)


def build_masked_runtime_instrumentation(
    *,
    pseudonym_key: bytes,
    downstream: SerializedInstrumentationSink,
    sink: MaskingSink,
) -> tuple[RuntimeInstrumentationAdapter, MaskedInstrumentationSink]:
    policy = MaskingPolicy(pseudonym_key=pseudonym_key)
    masked_sink = MaskedInstrumentationSink(
        policy=policy,
        sink=sink,
        downstream=downstream,
    )
    return (
        RuntimeInstrumentationAdapter(InstrumentationPort(masked_sink)),
        masked_sink,
    )
