"""Process-wide logging setup so runtime failures stay visible in deployments.

uvicorn's default configuration wires only the ``uvicorn*`` loggers and leaves
the root logger without a handler, so every record this service emitted was
dropped before it reached container stdout.  The handler installed here is also
the only place that renders structured ``extra`` fields, which is what makes an
``executor_error`` traceable after the fact.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


_RESERVED_RECORD_FIELDS = frozenset({
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "stacklevel",
    "taskName",
    "thread",
    "threadName",
})
_HANDLER_NAME = "retirewise-structured"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _opaque(value: Any) -> str:
    """Never let ``repr`` of an unexpected object leak into a log line."""

    return f"<{type(value).__name__}>"


class StructuredFormatter(logging.Formatter):
    """Append ``extra=`` fields that the default formatter silently drops."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_FIELDS and not key.startswith("_")
        }
        if not extras:
            return base
        try:
            rendered = json.dumps(
                extras,
                ensure_ascii=False,
                sort_keys=True,
                default=_opaque,
            )
        except Exception:  # noqa: BLE001 - formatting must never raise
            rendered = '{"log_extras": "unserializable"}'
        return f"{base} {rendered}"


def _resolve_level(level: str | int | None) -> int:
    if level is None:
        from core.config import settings

        level = settings.log_level
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).strip().upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def configure_logging(*, level: str | int | None = None) -> None:
    """Attach one stdout handler to the root logger.  Safe to call twice."""

    resolved = _resolve_level(level)
    root = logging.getLogger()
    root.setLevel(resolved)
    for handler in root.handlers:
        if getattr(handler, "name", None) == _HANDLER_NAME:
            handler.setLevel(resolved)
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.name = _HANDLER_NAME
    handler.setLevel(resolved)
    handler.setFormatter(StructuredFormatter(fmt=_LOG_FORMAT))
    root.addHandler(handler)
