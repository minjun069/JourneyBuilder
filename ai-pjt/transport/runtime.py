"""Deadline budget shared by the SSE request execution path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic

from .contracts import ChatV2Request


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExecutionBudget:
    deadline_at: datetime
    started_at_monotonic: float

    @classmethod
    def from_request(cls, request: ChatV2Request) -> "ExecutionBudget":
        return cls(
            deadline_at=request.deadline_at.astimezone(timezone.utc),
            started_at_monotonic=monotonic(),
        )

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        current = (now or utc_now()).astimezone(timezone.utc)
        return max(0.0, (self.deadline_at - current).total_seconds())

    def require_time(self) -> None:
        if self.remaining_seconds() <= 0:
            raise TimeoutError("request deadline exceeded")
