"""Container readiness probe for the Django Backend.

The probe checks only dependencies required to accept Backend traffic: the
local ASGI process, PostgreSQL with an up-to-date schema, and Redis. It avoids
calling the AI service or any external provider.
"""

from __future__ import annotations

import math
import os
import socket
import sys
from collections.abc import Callable, Mapping
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
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

    if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ReadinessConfigurationError(
            "READINESS_TIMEOUT_SECONDS must be greater than 0 and at most "
            f"{MAX_TIMEOUT_SECONDS:g}"
        )
    return timeout


def _read_port(environ: Mapping[str, str]) -> int:
    raw_value = environ.get("READINESS_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ReadinessConfigurationError("READINESS_PORT must be an integer") from exc

    if not 1 <= port <= 65535:
        raise ReadinessConfigurationError(
            "READINESS_PORT must be between 1 and 65535"
        )
    return port


def check_process(host: str, port: int, timeout: float) -> None:
    """Require the local ASGI process to accept a TCP connection."""

    with socket.create_connection((host, port), timeout=timeout):
        return


def _assert_database_ready(connection: Any, executor_factory: Callable[[Any], Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        if cursor.fetchone() != (1,):
            raise RuntimeError("PostgreSQL readiness query returned an unexpected result")

    executor = executor_factory(connection)
    targets = executor.loader.graph.leaf_nodes()
    if executor.migration_plan(targets):
        raise RuntimeError("PostgreSQL has unapplied Django migrations")


def check_database(timeout: float) -> None:
    """Verify Django's active PostgreSQL connection and migration state."""

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    django.setup()
    options = connection.settings_dict.setdefault("OPTIONS", {})
    options.setdefault("connect_timeout", max(1, math.ceil(timeout)))
    try:
        _assert_database_ready(connection, MigrationExecutor)
    finally:
        connection.close()


def _assert_redis_ready(client: Any) -> None:
    if client.ping() is not True:
        raise RuntimeError("Redis readiness ping returned an unexpected result")


def check_redis(timeout: float) -> None:
    """Verify the Redis store used by the Backend AI request lifecycle."""

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django
    import redis
    from django.conf import settings

    django.setup()
    redis_url = str(getattr(settings, "REDIS_URL", "")).strip()
    if not redis_url:
        raise ReadinessConfigurationError("REDIS_URL is required")

    client = redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        _assert_redis_ready(client)
    finally:
        client.close()


def check_readiness(
    environ: Mapping[str, str] | None = None,
    process_probe: Callable[[str, int, float], None] | None = None,
    database_probe: Callable[[float], None] | None = None,
    redis_probe: Callable[[float], None] | None = None,
) -> None:
    """Raise when the Backend should not receive requests."""

    active_environ = os.environ if environ is None else environ
    host = active_environ.get("READINESS_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ReadinessConfigurationError("READINESS_HOST must not be empty")

    port = _read_port(active_environ)
    timeout = _read_timeout(active_environ)

    (process_probe or check_process)(host, port, timeout)
    (database_probe or check_database)(timeout)
    (redis_probe or check_redis)(timeout)


def main() -> int:
    try:
        check_readiness()
    except Exception as exc:
        # Do not print exception text: connection errors may include secrets or URLs.
        print(f"not ready: {type(exc).__name__}", file=sys.stderr)
        return 1

    print("ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
