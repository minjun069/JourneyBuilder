"""Deterministic confidence bands and visibility for Error Review."""

from __future__ import annotations

from typing import Any


MEDIUM_CONFIDENCE_MIN = 0.60
HIGH_CONFIDENCE_MIN = 0.85
VISIBLE_CONFIDENCE_LEVELS = frozenset({"medium", "high"})


def confidence_level(confidence: float) -> str:
    if confidence >= HIGH_CONFIDENCE_MIN:
        return "high"
    if confidence >= MEDIUM_CONFIDENCE_MIN:
        return "medium"
    return "low"


def visible_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item for item in items
        if item.get("confidence_level") in VISIBLE_CONFIDENCE_LEVELS
    ]
