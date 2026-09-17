"""Request-scoped producer boundary for non-model operation observations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


OperationRecorder = Callable[..., None]


_OPERATION_RECORDER: ContextVar[OperationRecorder | None] = ContextVar(
    "operation_recorder",
    default=None,
)


@contextmanager
def bind_operation_recorder(
    recorder: OperationRecorder | None,
) -> Iterator[None]:
    if recorder is not None and not callable(recorder):
        raise TypeError("operation recorder must be callable")
    token = _OPERATION_RECORDER.set(recorder)
    try:
        yield
    finally:
        _OPERATION_RECORDER.reset(token)


def record_operation_observation(**event: Any) -> None:
    """Best-effort emit through the current request without leaking payloads."""

    recorder = _OPERATION_RECORDER.get()
    if recorder is None:
        return
    try:
        recorder(**event)
    except Exception:
        # Telemetry must never alter route, retrieval, or agent behavior.
        return
