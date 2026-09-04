from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import DocumentMetadata, GoldQuery, SourceDocument


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load JSON fixture {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"fixture root must be an object: {path}")
    return payload


def load_corpus(path: Path, *, require_approved: bool = False) -> list[SourceDocument]:
    payload = _read_json_object(path)
    records = payload.get("documents")
    if not isinstance(records, list) or not records:
        raise ValueError("corpus must contain a non-empty documents list")

    documents: list[SourceDocument] = []
    source_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != {"metadata", "text"}:
            raise ValueError(f"documents[{index}] must contain metadata and text only")
        if not isinstance(record["metadata"], dict):
            raise ValueError(f"documents[{index}].metadata must be an object")
        metadata = DocumentMetadata.from_dict(record["metadata"])
        if require_approved and metadata.review_status != "approved":
            raise ValueError(
                f"operational corpus contains unapproved source: {metadata.source_id}"
            )
        if metadata.source_id in source_ids:
            raise ValueError(f"duplicate source_id: {metadata.source_id}")
        source_ids.add(metadata.source_id)
        documents.append(SourceDocument(metadata=metadata, text=record["text"]))
    return documents


def load_gold_queries(path: Path) -> list[GoldQuery]:
    payload = _read_json_object(path)
    records = payload.get("queries")
    if not isinstance(records, list) or not records:
        raise ValueError("gold fixture must contain a non-empty queries list")

    queries: list[GoldQuery] = []
    query_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"queries[{index}] must be an object")
        if set(record) != {"query_id", "query", "relevant_source_ids"}:
            raise ValueError(f"queries[{index}] contains invalid fields")
        relevant = record["relevant_source_ids"]
        if not isinstance(relevant, list) or not all(
            isinstance(source_id, str) for source_id in relevant
        ):
            raise ValueError(f"queries[{index}].relevant_source_ids must be strings")
        query = GoldQuery(
            query_id=record["query_id"],
            query=record["query"],
            relevant_source_ids=tuple(relevant),
        )
        if query.query_id in query_ids:
            raise ValueError(f"duplicate query_id: {query.query_id}")
        query_ids.add(query.query_id)
        queries.append(query)
    return queries
