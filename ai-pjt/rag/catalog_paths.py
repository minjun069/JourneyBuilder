"""Resolve approved catalog documents inside the packaged RAG data root."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any


CATALOG_DATA_PREFIX = PurePosixPath("ai-pjt/rag/data")


class CatalogDocumentPathError(ValueError):
    """Raised when a catalog path is not a safe project-relative RAG path."""


def resolve_catalog_document_path(
    catalog_path: Path,
    configured_path: Any,
) -> Path:
    """Map an ``ai-pjt/rag/data`` catalog path to its packaged data root.

    The source checkout stores the AI package below ``ai-pjt/`` while the
    production image copies that package directly to ``/app``. Resolving from
    the catalog's own directory preserves the same containment boundary in
    both layouts.
    """

    if not isinstance(configured_path, str) or not configured_path:
        raise CatalogDocumentPathError("catalog document path is required")

    relative_path = PurePosixPath(configured_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise CatalogDocumentPathError("catalog document path must be relative")
    try:
        data_relative_path = relative_path.relative_to(CATALOG_DATA_PREFIX)
    except ValueError as exc:
        raise CatalogDocumentPathError(
            "catalog document path must stay under ai-pjt/rag/data"
        ) from exc

    data_root = catalog_path.resolve().parent
    resolved = (data_root / Path(*data_relative_path.parts)).resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError as exc:
        raise CatalogDocumentPathError(
            "catalog document path escapes the packaged data root"
        ) from exc
    return resolved
