"""Contract-preserving masking adapter for SSE terminal payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.masking import MaskingPath, MaskingPolicy, MaskingSink, mask_payload
from transport.contracts import CompletedTerminal, FailedTerminal


class TerminalPayloadMasker:
    """Validate, mask, and revalidate one completed or failed SSE terminal."""

    __slots__ = ("_pseudonym_key",)

    def __init__(self, *, pseudonym_key: bytes) -> None:
        MaskingPolicy(pseudonym_key=pseudonym_key)
        self._pseudonym_key = pseudonym_key

    def mask(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise TypeError("terminal payload must be a mapping")
        model = _terminal_model(payload)
        canonical = model.model_validate(payload).model_dump(mode="json")
        allowed = _terminal_business_value_paths(canonical)
        masked = mask_payload(
            canonical,
            sink=MaskingSink.TERMINAL,
            policy=MaskingPolicy(
                pseudonym_key=self._pseudonym_key,
                allowed_terminal_paths=frozenset(allowed),
            ),
        )
        return model.model_validate(masked).model_dump(mode="json")


def _terminal_model(payload: Mapping[str, Any]) -> type[CompletedTerminal] | type[FailedTerminal]:
    status = payload.get("status")
    if status == "completed":
        return CompletedTerminal
    if status == "failed":
        return FailedTerminal
    raise ValueError("terminal status must be completed or failed")


def _terminal_business_value_paths(payload: Mapping[str, Any]) -> set[MaskingPath]:
    output = payload.get("output")
    if not isinstance(output, Mapping):
        return set()
    paths: set[MaskingPath] = set()
    if isinstance(output.get("elicitation"), Mapping):
        paths.add(("output", "elicitation", "summary"))
    for action_name in ("action", "elicit_capture"):
        action = output.get(action_name)
        if not isinstance(action, Mapping):
            continue
        changes = action.get("changes")
        if not isinstance(changes, list):
            continue
        for index, change in enumerate(changes):
            if not isinstance(change, Mapping):
                continue
            if "value" in change:
                paths.update(
                    _leaf_paths(
                        change["value"],
                        ("output", action_name, "changes", index, "value"),
                    )
                )
            for identity_field in (
                "item_id",
                "client_item_id",
                "parent_item_id",
                "parent_client_item_id",
            ):
                if identity_field in change:
                    paths.add((
                        "output",
                        action_name,
                        "changes",
                        index,
                        identity_field,
                    ))
    review = output.get("review")
    items = review.get("items") if isinstance(review, Mapping) else None
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            for field_name in ("current_value", "suggested_value"):
                if field_name in item:
                    paths.update(
                        _leaf_paths(
                            item[field_name],
                            ("output", "review", "items", index, field_name),
                        )
                    )
    return paths


def _leaf_paths(value: Any, path: MaskingPath) -> set[MaskingPath]:
    if isinstance(value, Mapping):
        paths: set[MaskingPath] = set()
        for key, child in value.items():
            if isinstance(key, str):
                paths.update(_leaf_paths(child, (*path, key)))
        return paths
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        paths = set()
        for index, child in enumerate(value):
            paths.update(_leaf_paths(child, (*path, index)))
        return paths
    return {path}
