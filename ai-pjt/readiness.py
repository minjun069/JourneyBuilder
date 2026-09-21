"""Container readiness probe for the AI service.

The probe deliberately avoids calling an LLM or the Backend. It verifies the
local HTTP service, PostgreSQL, and the two active RAG indexes required by QA.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any
from urllib.request import urlopen


DEFAULT_HTTP_URL = "http://127.0.0.1:8001/api/system/v1/health"
DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_TIMEOUT_SECONDS = 10.0
REQUIRED_RAG_COLLECTIONS = frozenset(
    {"financial-knowledge", "interface-guides"}
)


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
    """Require PostgreSQL and both populated operational RAG indexes."""

    import psycopg

    connect_timeout = max(1, math.ceil(timeout))
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("PostgreSQL readiness query returned an unexpected result")
            cursor.execute(
                """
                SELECT active.logical_collection,
                       active.physical_collection,
                       COUNT(chunks.chunk_id)
                FROM rag_active_indexes AS active
                JOIN rag_index_manifests AS manifests
                  ON manifests.physical_collection = active.physical_collection
                 AND manifests.logical_collection = active.logical_collection
                 AND manifests.retired_at IS NULL
                LEFT JOIN rag_index_chunks AS chunks
                  ON chunks.physical_collection = active.physical_collection
                WHERE active.logical_collection = ANY(%s)
                GROUP BY active.logical_collection, active.physical_collection
                """,
                (sorted(REQUIRED_RAG_COLLECTIONS),),
            )
            active_rows = cursor.fetchall()

    active_collections = {row[0] for row in active_rows}
    if active_collections != REQUIRED_RAG_COLLECTIONS:
        raise RuntimeError("required active RAG indexes are missing")
    if any(int(row[2]) < 1 for row in active_rows):
        raise RuntimeError("an active RAG index contains no chunks")


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
