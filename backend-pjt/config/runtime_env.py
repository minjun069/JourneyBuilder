"""Strict environment parsing for Backend startup configuration."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping


INSECURE_SECRET_VALUES = {
    "change-me",
    "changeme",
    "password",
    "postgres",
    "secret",
    "ssafy",
}


class RuntimeConfigurationError(ValueError):
    """Raised when a required runtime setting is missing or unsafe."""


def require_secret(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    active_environ = os.environ if environ is None else environ
    value = active_environ.get(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required")
    if value.lower() in INSECURE_SECRET_VALUES:
        raise RuntimeConfigurationError(f"{name} uses an insecure placeholder")
    return value


def read_bool(
    name: str,
    default: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    active_environ = os.environ if environ is None else environ
    raw_value = active_environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeConfigurationError(f"{name} must be true or false")


def read_positive_int(
    name: str,
    default: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    active_environ = os.environ if environ is None else environ
    raw_value = active_environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeConfigurationError(f"{name} must be greater than 0")
    return value


def read_positive_float(
    name: str,
    default: float,
    environ: Mapping[str, str] | None = None,
) -> float:
    active_environ = os.environ if environ is None else environ
    raw_value = active_environ.get(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeConfigurationError(f"{name} must be greater than 0")
    return value


def read_csv(
    name: str,
    default: str,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    active_environ = os.environ if environ is None else environ
    values = [
        item.strip()
        for item in active_environ.get(name, default).split(",")
        if item.strip()
    ]
    if not values:
        raise RuntimeConfigurationError(f"{name} must not be empty")
    return values
