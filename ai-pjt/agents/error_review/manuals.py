"""Direct, hash-verified Error Review manual mapping (never similarity search)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag.catalog_paths import (
    CatalogDocumentPathError,
    resolve_catalog_document_path,
)


CATALOG_PATH = Path(__file__).resolve().parents[2] / "rag" / "data" / "catalog.json"
SUPPORTED_STEPS = frozenset({
    "basic-info",
    "financial-mindset",
    "financial-satisfaction",
    "financial-assets",
    "pension-assets",
    "real-estate",
    "liabilities",
    "income",
    "expenses",
    "financial-goals",
})


class ErrorReviewManualError(ValueError):
    pass


@dataclass(frozen=True)
class ErrorReviewManual:
    document_id: str
    title: str
    version: str
    path: Path
    text: str


def load_error_review_manual(
    step_id: str,
    *,
    catalog_path: Path = CATALOG_PATH,
) -> ErrorReviewManual:
    if step_id not in SUPPORTED_STEPS:
        raise ErrorReviewManualError("unsupported error review step")
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorReviewManualError("approved catalog unavailable") from exc
    if catalog.get("acceptance", {}).get("status") != "domain-approved":
        raise ErrorReviewManualError("catalog is not domain-approved")
    collections = {
        item.get("id"): item
        for item in catalog.get("collections", [])
        if isinstance(item, dict)
    }
    interface = collections.get("interface-guides", {})
    if "error_review" not in interface.get("consumers", []):
        raise ErrorReviewManualError("catalog does not approve Error Review")
    matches = [
        item for item in catalog.get("documents", [])
        if isinstance(item, dict)
        and item.get("collection") == "interface-guides"
        and item.get("applies_to", {}).get("step_id") == step_id
    ]
    if len(matches) != 1:
        raise ErrorReviewManualError("step manual mapping must be unique")
    document: dict[str, Any] = matches[0]
    try:
        manual_path = resolve_catalog_document_path(
            catalog_path,
            document.get("path"),
        )
    except CatalogDocumentPathError as exc:
        raise ErrorReviewManualError("mapped manual path is invalid") from exc
    approved_root = catalog_path.resolve().parent
    if manual_path.parent != approved_root:
        raise ErrorReviewManualError("manual path escapes approved directory")
    try:
        raw = manual_path.read_bytes()
    except OSError as exc:
        raise ErrorReviewManualError("mapped manual unavailable") from exc
    if hashlib.sha256(raw).hexdigest() != document.get("sha256"):
        raise ErrorReviewManualError("mapped manual digest mismatch")
    return ErrorReviewManual(
        document_id=str(document["id"]),
        title=str(document["title"]),
        version=str(document["version"]),
        path=manual_path,
        text=raw.decode("utf-8"),
    )
