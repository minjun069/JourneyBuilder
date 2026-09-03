"""Container readiness probe for the AI service.

The probe deliberately avoids calling an LLM or the Backend. It verifies only
the local HTTP service and the PostgreSQL dependency required by the current
LangGraph checkpointer.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any
from urllib.request import urlopen


DEFAULT_HTTP_URL = "http://127.0.0.1:8001/health"
DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_TIMEOUT_SECONDS = 10.0


class ReadinessConfigurationError(ValueError):
    """Raised when the probe cannot be configured safely."""


def _read_timeout(environ: Mapping[str, str]) -> float:
    raw_value = environ.get("READINESS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = float(raw_value)
    except ValueError as exc:
        raise ReadinessConfigurationError(
            "READINESS_TIMEOUT_SECONDS must be a number"
        ) from exc

    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ReadinessConfigurationError(
            f"READINESS_TIMEOUT_SECONDS must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    return timeout


def check_http(url: str, timeout: float) -> None:
    """Require the existing liveness endpoint to return its expected payload."""

    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed/local URL by default
        if response.status != 200:
            raise RuntimeError("AI health endpoint returned a non-200 status")
        payload: Any = json.loads(response.read())

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError("AI health endpoint returned an unexpected payload")


def check_postgres(database_url: str, timeout: float) -> None:
    """Run a lightweight query against the active checkpointer database."""

    import psycopg

    connect_timeout = max(1, math.ceil(timeout))
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("PostgreSQL readiness query returned an unexpected result")


def check_readiness(
    environ: Mapping[str, str] | None = None,
    http_probe: Callable[[str, float], None] | None = None,
    database_probe: Callable[[str, float], None] | None = None,
) -> None:
    """Raise when the service should not receive requests."""

    active_environ = os.environ if environ is None else environ
    database_url = active_environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ReadinessConfigurationError("DATABASE_URL is required")

    timeout = _read_timeout(active_environ)
    http_url = active_environ.get("READINESS_HTTP_URL", DEFAULT_HTTP_URL).strip()
    if not http_url:
        raise ReadinessConfigurationError("READINESS_HTTP_URL must not be empty")

    (http_probe or check_http)(http_url, timeout)
    (database_probe or check_postgres)(database_url, timeout)


def main() -> int:
    try:
        check_readiness()
    except Exception as exc:
        # Do not print exception text: connection errors may contain credentials or URLs.
        print(f"not ready: {type(exc).__name__}", file=sys.stderr)
        return 1

    print("ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
