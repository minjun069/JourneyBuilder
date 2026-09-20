"""Deterministic Error Review candidate validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agents.error_review.confidence import confidence_level
from tools.output_policy import validate_form_update_contract


def decode_review_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        current_value = json.loads(item["current_value_json"])
        suggested_raw = item.get("suggested_value_json")
        suggested_value = (
            json.loads(suggested_raw) if suggested_raw is not None else None
        )
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    return {
        **{
            key: value for key, value in item.items()
            if key not in {"current_value_json", "suggested_value_json"}
        },
        "current_value": current_value,
        "suggested_value": suggested_value,
    }


def validated_item(
    item: Mapping[str, Any],
    *,
    step_id: str,
    current_form: Any,
    field_registry: list[dict[str, Any]],
) -> dict[str, Any] | None:
    registry_spec = next((
        field for field in field_registry
        if field.get("section") == item.get("section")
        and field.get("path") == item.get("path")
    ), None)
    if registry_spec is None:
        return None
    cardinality = registry_spec.get("cardinality")
    if cardinality in {"list", "nested_list"} and not (
        item.get("item_id") is not None or item.get("client_item_id")
    ):
        return None
    if (
        cardinality == "nested_list"
        and str(item.get("path", "")).startswith("items[].")
        and not (
            item.get("parent_item_id") is not None
            or item.get("parent_client_item_id")
        )
    ):
        return None
    actual, found = _current_value(current_form, item, registry_spec)
    if not found or actual != item.get("current_value"):
        return None
    candidate = item.get("suggested_value")
    if candidate is not None:
        change = {
            "section": item["section"],
            "path": item["path"],
            "operation": "set" if cardinality == "single" else "update",
            "value": candidate,
            "reason": item["reason"],
        }
        for key in (
            "item_id", "client_item_id", "parent_item_id", "parent_client_item_id"
        ):
            if item.get(key) is not None:
                change[key] = item[key]
        validation = validate_form_update_contract({
            "step_id": step_id,
            "output": {
                "response": "후보 검증",
                "step_id": step_id,
                "action": {
                    "type": "form_change",
                    "target": "draft_form",
                    "user_requested": False,
                    "confidence": item["confidence"],
                    "changes": [change],
                },
            },
        })
        if not validation.valid:
            candidate = None
    return {
        "section": item["section"],
        "path": item["path"],
        **{
            key: item[key] for key in (
                "item_id", "client_item_id", "parent_item_id", "parent_client_item_id"
            ) if item.get(key) is not None
        },
        "current_value": actual,
        "reason": item["reason"],
        "suggested_value": candidate,
        "confidence": item["confidence"],
        "confidence_level": confidence_level(float(item["confidence"])),
    }


def _current_value(
    current_form: Any,
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> tuple[Any, bool]:
    if not isinstance(current_form, Mapping):
        return None, False
    if spec.get("cardinality") == "single":
        path = str(item.get("path", ""))
        return (current_form.get(path), path in current_form)
    section = item.get("section")
    rows = (
        current_form.get(section, [])
        if section in {"goal_periods", "goal_milestones"}
        else current_form.get("assets", [])
    )
    if not isinstance(rows, list):
        return None, False
    if section == "goal_periods" and str(item.get("path", "")).startswith("items[]."):
        for parent in rows:
            if not isinstance(parent, Mapping) or not _parent_identity_matches(parent, item):
                continue
            children = parent.get("items", [])
            for child in children if isinstance(children, list) else []:
                if isinstance(child, Mapping) and _identity_matches(child, item):
                    key = str(item["path"]).split(".", 1)[1]
                    return child.get(key), key in child
        return None, False
    for row in rows:
        if isinstance(row, Mapping) and _identity_matches(row, item):
            path = str(item.get("path", ""))
            return row.get(path), path in row
    return None, False


def _identity_matches(row: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    if item.get("item_id") is not None:
        return str(row.get("id")) == str(item["item_id"])
    client_id = item.get("client_item_id")
    return isinstance(client_id, str) and row.get("client_item_id") == client_id


def _parent_identity_matches(row: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    if item.get("parent_item_id") is not None:
        return str(row.get("id")) == str(item["parent_item_id"])
    client_id = item.get("parent_client_item_id")
    return isinstance(client_id, str) and row.get("client_item_id") == client_id
