"""Field-scoped Elicit memory and planner projection.

The conversation graph deliberately has no global Elicit phase.  Each
qualitative field owns its own question, candidate, confirmation, and terminal
status so unrelated turns can be planned and executed without pausing or
resuming an Elicit session.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
from typing import Any


def _load_policy() -> dict[str, Any]:
    relative = Path("contract-fixtures/ai-lifecycle/v2/elicit-policy.json")
    module_path = Path(__file__).resolve()
    for candidate in (
        module_path.parents[2] / relative,
        module_path.parents[1] / relative,
    ):
        if candidate.is_file():
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            break
    else:
        raise ValueError("Elicit policy fixture unavailable")
    if (
        payload.get("fixture_kind") != "elicit-policy"
        or payload.get("schema_version") != "1.0"
    ):
        raise ValueError("unsupported Elicit policy")
    if set(payload.get("field_order", [])) != set(payload.get("fields", {})):
        raise ValueError("Elicit field policy mismatch")
    routing = payload.get("routing_contract", {})
    if (
        not isinstance(routing, Mapping)
        or routing.get("owner") != "planner"
        or routing.get("requires_sufficient_confidence") is not True
        or set(routing.get("turn_kinds", []))
        != {"new", "answer", "correction", "confirmation"}
    ):
        raise ValueError("Elicit routing policy mismatch")
    return payload


ELICIT_POLICY = _load_policy()
CONTEXT_SCHEMA_VERSION = ELICIT_POLICY["state_schema_version"]
ELICIT_STEP_ID = ELICIT_POLICY["step_id"]
FIELD_IDS = tuple(ELICIT_POLICY["field_order"])
FIELD_ID_SET = frozenset(FIELD_IDS)
FIELD_LABELS = {
    field_id: ELICIT_POLICY["fields"][field_id]["label"]
    for field_id in FIELD_IDS
}
FIELD_QUESTIONS = {
    field_id: ELICIT_POLICY["fields"][field_id]["question"]
    for field_id in FIELD_IDS
}
MAX_FIELD_TURNS = ELICIT_POLICY["limits"]["max_field_turns"]
MAX_FIELD_RESETS = ELICIT_POLICY["limits"]["max_resets_per_field"]

CONTEXT_STATUSES = frozenset({
    "unstarted",
    "open",
    "awaiting_confirmation",
    "deferred",
    "refused",
    "skipped",
    "completed",
})
CONFIRMATION_STATUSES = frozenset({
    "not_requested",
    "pending",
    "confirmed",
    "rejected",
})
ELICIT_TURN_KINDS = frozenset(
    ELICIT_POLICY["routing_contract"]["turn_kinds"]
)


def context_scope(state: Mapping[str, Any]) -> dict[str, Any]:
    raw = state.get("context_scope", {})
    scope = raw if isinstance(raw, Mapping) else {}
    return {
        "session_id": scope.get("session_id"),
        "thread_id": scope.get("thread_id"),
        "step_id": state.get("step_id"),
    }


def initial_elicit_contexts(state: Mapping[str, Any]) -> dict[str, Any]:
    current_form = state.get("customer_info", {})
    current_form = (
        current_form.get("current_form", {})
        if isinstance(current_form, Mapping)
        else {}
    )
    if not isinstance(current_form, Mapping):
        current_form = {}
    contexts: dict[str, dict[str, Any]] = {}
    for field_id in FIELD_IDS:
        existing = current_form.get(field_id)
        has_existing = existing not in (None, "")
        contexts[field_id] = {
            "topic": "financial_mindset",
            "field": field_id,
            "confirmed_summary": str(existing) if has_existing else None,
            "pending_candidate": None,
            "open_question": None,
            "source_turns": [],
            "confirmation_status": "confirmed" if has_existing else "not_requested",
            "status": "completed" if has_existing else "unstarted",
            "turn_count": 0,
            "reset_count": 0,
            "updated_at": None,
        }
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "scope": context_scope(state),
        "contexts": contexts,
    }


def load_elicit_contexts(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a safe mutable copy, initializing only when no memory exists."""

    candidate = state.get("elicit_contexts")
    if candidate in (None, {}):
        return initial_elicit_contexts(state)
    if not valid_elicit_contexts(state, candidate):
        raise ValueError("Elicit context state is invalid for the current scope")
    contexts = deepcopy(dict(candidate))
    _reconcile_current_form(state, contexts)
    return contexts


def valid_elicit_contexts(state: Mapping[str, Any], candidate: Any) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    if set(candidate) != {"schema_version", "scope", "contexts"}:
        return False
    if candidate.get("schema_version") != CONTEXT_SCHEMA_VERSION:
        return False
    if candidate.get("scope") != context_scope(state):
        return False
    contexts = candidate.get("contexts")
    if not isinstance(contexts, Mapping) or set(contexts) != FIELD_ID_SET:
        return False
    return all(_valid_field_context(field_id, contexts[field_id]) for field_id in FIELD_IDS)


def elicit_context_index(store: Mapping[str, Any]) -> dict[str, Any]:
    """Build the bounded planner input for all six field memories."""

    contexts = store.get("contexts", {})
    return {
        "schema_version": store.get("schema_version"),
        "fields": [
            {
                "topic": context["topic"],
                "field": field_id,
                "status": context["status"],
                "confirmed_summary": context["confirmed_summary"],
                "pending_candidate": context["pending_candidate"],
                "open_question": context["open_question"],
                "confirmation_status": context["confirmation_status"],
                "source_turns": list(context["source_turns"]),
                "updated_at": context["updated_at"],
            }
            for field_id in FIELD_IDS
            for context in [contexts[field_id]]
        ],
    }


def _valid_field_context(field_id: str, value: Any) -> bool:
    required = {
        "topic",
        "field",
        "confirmed_summary",
        "pending_candidate",
        "open_question",
        "source_turns",
        "confirmation_status",
        "status",
        "turn_count",
        "reset_count",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        return False
    if value.get("topic") != "financial_mindset" or value.get("field") != field_id:
        return False
    status = value.get("status")
    confirmation = value.get("confirmation_status")
    if status not in CONTEXT_STATUSES or confirmation not in CONFIRMATION_STATUSES:
        return False
    for key in (
        "confirmed_summary",
        "pending_candidate",
        "open_question",
        "updated_at",
    ):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            return False
    source_turns = value.get("source_turns")
    if (
        not isinstance(source_turns, list)
        or not all(isinstance(turn_id, str) and turn_id for turn_id in source_turns)
    ):
        return False
    counters = (value.get("turn_count"), value.get("reset_count"))
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in counters
    ):
        return False
    if value["turn_count"] > MAX_FIELD_TURNS + 1 or value["reset_count"] > MAX_FIELD_RESETS:
        return False
    if status == "awaiting_confirmation" and (
        confirmation != "pending"
        or value.get("pending_candidate") in (None, "")
        or value.get("open_question") in (None, "")
        or not source_turns
    ):
        return False
    if status == "completed" and (
        confirmation != "confirmed"
        or value.get("confirmed_summary") in (None, "")
        or value.get("pending_candidate") is not None
        or value.get("open_question") is not None
    ):
        return False
    if status == "open" and value.get("open_question") in (None, ""):
        return False
    if (
        status in {"unstarted", "deferred", "refused", "skipped"}
        and value.get("open_question") is not None
    ):
        return False
    return True


def _reconcile_current_form(
    state: Mapping[str, Any],
    store: dict[str, Any],
) -> None:
    customer_info = state.get("customer_info", {})
    current_form = (
        customer_info.get("current_form", {})
        if isinstance(customer_info, Mapping)
        else {}
    )
    if not isinstance(current_form, Mapping):
        return
    for field_id in FIELD_IDS:
        context = store["contexts"][field_id]
        existing = current_form.get(field_id)
        if context["status"] == "unstarted" and existing not in (None, ""):
            context.update({
                "confirmed_summary": str(existing),
                "pending_candidate": None,
                "open_question": None,
                "confirmation_status": "confirmed",
                "status": "completed",
            })
