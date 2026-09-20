"""Dependency-neutral runtime callable contracts."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any


RuntimeExecutor = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]

__all__ = ["RuntimeExecutor"]
