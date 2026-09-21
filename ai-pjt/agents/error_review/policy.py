"""Deterministic Error Review candidate validation."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from tools.output_policy import validate_form_update_contract


_UNIT_LABELS = {"none": "", "KRW": "원", "age": "세", "year": "년", "percent": "%", "score": "점", "date": "날짜"}


def _unit_label(spec: Mapping[str, Any]) -> str:
    unit = _UNIT_LABELS.get(spec["unit"], spec["unit"])
    period = spec.get("constraints", {}).get("period")
    return unit + {"month": "/월", "year": "/년"}.get(period, "")


def _identity(row: Mapping[str, Any], *, parent: bool = False) -> dict[str, Any]:
    prefix = "parent_" if parent else ""
    if row.get("id") is not None:
        return {prefix + "item_id": row["id"]}
    if row.get("client_item_id"):
        return {prefix + "client_item_id": row["client_item_id"]}
    return {}


def _field_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _field_value(child) for key, child in value.items()
            if key not in {"id", "client_item_id", "created_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_field_value(child) for child in value]
    return value


def build_review_fields(
    current_form: Any,
    registry: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Give the model short references; keep paths and row identities locally."""
    fields: list[dict[str, Any]] = []
    references: dict[str, dict[str, Any]] = {}
    if not isinstance(current_form, Mapping):
        return fields, references

    def add(spec: Mapping[str, Any], row: Mapping[str, Any], key: str,
            identity: Mapping[str, Any], group: str = "") -> None:
        if key not in row:
            return
        ref = f"f{len(fields) + 1}"
        fields.append({
            "ref": ref,
            "name": group + str(spec["label"]),
            "unit": _unit_label(spec),
            "value": _field_value(row[key]),
        })
        references[ref] = {
            "section": spec["section"], "path": spec["path"],
            **identity, "current_value": deepcopy(row[key]),
        }

    for spec in registry:
        if spec.get("sensitivity") == "operational":
            continue
        path, section = str(spec["path"]), spec["section"]
        if spec["cardinality"] == "single":
            if "." not in path:
                add(spec, current_form, path, {})
            continue
        rows = current_form.get(section if section in {"goal_periods", "goal_milestones"} else "assets", [])
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, Mapping) or not _identity(row):
                continue
            group_name = {"goal_periods": "기간", "goal_milestones": "목표"}.get(section, "항목")
            group = f"{group_name} {index + 1} · "
            if path.startswith("items[]."):
                children = row.get("items", [])
                for child_index, child in enumerate(children if isinstance(children, list) else []):
                    if isinstance(child, Mapping) and _identity(child):
                        add(spec, child, path.split(".", 1)[1],
                            {**_identity(child), **_identity(row, parent=True)},
                            group + f"세부 항목 {child_index + 1} · ")
            elif "." not in path and path != "items":
                add(spec, row, path, _identity(row), group)
    return fields, references


def decode_review_item(
    item: Mapping[str, Any],
    references: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    reference = references.get(item.get("field_ref"))
    if reference is None:
        return None
    return {
        **reference,
        "reason": item["reason"],
        "suggested_value": item.get("suggested_value"),
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
    if (
        registry_spec.get("value_type") in {"integer", "decimal", "number"}
        and isinstance(candidate, str)
    ):
        # Review suggestions have no amount-token restoration path in the backend.
        candidate = None
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
