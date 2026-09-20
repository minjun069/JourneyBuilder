"""Internal planning request, result, issue, and context projection contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.context_window import ContextWindow


PLAN_SCHEMA_VERSION = "1.0"
MAX_PLAN_TASKS = 3


@dataclass(frozen=True)
class PlanIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class RoutePlanValidation:
    valid: bool
    plan: dict[str, Any]
    issues: tuple[PlanIssue, ...]
    diagnostics: tuple[dict[str, str], ...] = ()
    retries_used: int = 0


def context_window_payload(window: ContextWindow) -> dict[str, Any]:
    """Expose only the bounded context projection to planning and execution."""

    return {
        "confirmed_facts": deepcopy(dict(window.confirmed_facts)),
        "structured_state": window.structured_state.to_state(),
        "summary": window.summary.to_state() if window.summary else {},
        "retrieved_memories": [
            memory.to_state() for memory in window.retrieved_memories
        ],
        "turns": [turn.to_state() for turn in window.turns],
        "input_units": window.input_units,
    }


def invalid_plan(
    issues: list[PlanIssue],
    *,
    diagnostics: tuple[dict[str, str], ...] = (),
) -> RoutePlanValidation:
    return RoutePlanValidation(
        valid=False,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": []},
        issues=tuple(issues),
        diagnostics=diagnostics,
    )


def issue(code: str, path: str, message: str) -> PlanIssue:
    return PlanIssue(code=code, path=path, message=message)
