from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DocumentMetadata, GoldQuery, SourceDocument


@dataclass(frozen=True)
class ApprovedCatalog:
    catalog_version: str
    as_of_date: str
    documents: tuple[SourceDocument, ...]

    def for_collection(self, collection: str) -> list[SourceDocument]:
        selected = [
            document
            for document in self.documents
            if document.metadata.collection == collection
        ]
        if not selected:
            raise ValueError(f"catalog has no approved documents for {collection}")
        return selected


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


def load_approved_catalog(path: Path, *, repository_root: Path) -> ApprovedCatalog:
    """Load only domain-approved files whose path and digest match the catalog."""

    payload = _read_json_object(path)
    if payload.get("hash_algorithm") != "sha256":
        raise ValueError("catalog hash_algorithm must be sha256")
    catalog_version = payload.get("catalog_version")
    as_of_date = payload.get("as_of_date")
    if not isinstance(catalog_version, str) or not catalog_version.strip():
        raise ValueError("catalog_version must be a non-empty string")
    if not isinstance(as_of_date, str) or not as_of_date.strip():
        raise ValueError("catalog as_of_date must be a non-empty string")

    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict) or acceptance.get("status") != "domain-approved":
        raise ValueError("operational catalog must be domain-approved")
    reviewed_by = acceptance.get("reviewed_by")
    approved_at = acceptance.get("approved_at")
    license_text = acceptance.get("license")
    sensitivity = acceptance.get("sensitivity")
    service_boundary = acceptance.get("service_boundary")
    for name, value in (
        ("acceptance.reviewed_by", reviewed_by),
        ("acceptance.approved_at", approved_at),
        ("acceptance.license", license_text),
        ("acceptance.sensitivity", sensitivity),
        ("acceptance.service_boundary", service_boundary),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if sensitivity != "public":
        raise ValueError("operational RAG catalog must contain public knowledge only")

    collection_records = payload.get("collections")
    if not isinstance(collection_records, list) or not collection_records:
        raise ValueError("catalog collections must be a non-empty list")
    collections: dict[str, dict[str, Any]] = {}
    for record in collection_records:
        if not isinstance(record, dict):
            raise ValueError("catalog collection entries must be objects")
        collection_id = record.get("id")
        if not isinstance(collection_id, str) or collection_id in collections:
            raise ValueError(f"invalid or duplicate collection id: {collection_id!r}")
        collections[collection_id] = record

    records = payload.get("documents")
    if not isinstance(records, list) or not records:
        raise ValueError("catalog documents must be a non-empty list")
    root = repository_root.resolve()
    documents: list[SourceDocument] = []
    catalog_ids: set[str] = set()
    source_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"documents[{index}] must be an object")
        catalog_id = record.get("id")
        source_id = record.get("source_id")
        if not isinstance(catalog_id, str) or not catalog_id.strip():
            raise ValueError(f"documents[{index}].id must be a non-empty string")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"documents[{index}].source_id must be a non-empty string")
        if catalog_id in catalog_ids or source_id in source_ids:
            raise ValueError(f"duplicate catalog id or source_id: {catalog_id}/{source_id}")
        catalog_ids.add(catalog_id)
        source_ids.add(source_id)

        collection = record.get("collection")
        collection_spec = collections.get(collection)
        if collection_spec is None:
            raise ValueError(f"unknown collection for {source_id}: {collection!r}")
        document_kind = record.get("document_type")
        if document_kind != collection_spec.get("document_type"):
            raise ValueError(f"document type does not match collection for {source_id}")

        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"path must be a non-empty string for {source_id}")
        if not Path(raw_path).match(str(collection_spec.get("path_pattern", ""))):
            raise ValueError(f"path is outside the collection pattern for {source_id}")
        document_path = (root / raw_path).resolve()
        if not document_path.is_relative_to(root) or not document_path.is_file():
            raise ValueError(f"catalog path is missing or escapes repository: {raw_path}")
        content = document_path.read_bytes()
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != record.get("sha256"):
            raise ValueError(f"catalog sha256 mismatch for {source_id}")

        locator_value = record.get("source_locator")
        if isinstance(locator_value, str):
            source_locators = (locator_value,)
        elif isinstance(locator_value, list) and all(
            isinstance(locator, str) and locator.strip() for locator in locator_value
        ):
            source_locators = tuple(locator_value)
        else:
            raise ValueError(f"invalid source_locator for {source_id}")
        refresh = record.get("refresh")
        if not isinstance(refresh, dict):
            raise ValueError(f"refresh must be an object for {source_id}")
        applies_to = record.get("applies_to")
        if not isinstance(applies_to, dict) or not applies_to:
            raise ValueError(f"applies_to must be a non-empty object for {source_id}")

        metadata = DocumentMetadata(
            source_id=source_id,
            title=record.get("title"),
            publisher=record.get("publisher"),
            source_locators=source_locators,
            published_at=record.get("published_at"),
            as_of_date=record.get("as_of_date"),
            version=record.get("version"),
            scope=json.dumps(
                {"service_boundary": service_boundary, "applies_to": applies_to},
                ensure_ascii=False,
                sort_keys=True,
            ),
            review_status="approved",
            reviewed_by=reviewed_by,
            reviewed_at=approved_at,
            document_kind=document_kind,
            collection=collection,
            license=license_text,
            sensitivity=sensitivity,
            sha256=actual_sha256,
            catalog_document_id=catalog_id,
            file_path=raw_path,
            refresh_cadence_days=refresh.get("cadence_days"),
            retire_when=record.get("retire_when", ""),
        )
        documents.append(
            SourceDocument(metadata=metadata, text=content.decode("utf-8"))
        )

    return ApprovedCatalog(
        catalog_version=catalog_version,
        as_of_date=as_of_date,
        documents=tuple(documents),
    )


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
        if not set(record).issubset(
            {"query_id", "query", "relevant_source_ids", "category"}
        ) or not {"query_id", "query", "relevant_source_ids"}.issubset(record):
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
            category=record.get("category", "positive"),
        )
        if query.query_id in query_ids:
            raise ValueError(f"duplicate query_id: {query.query_id}")
        query_ids.add(query.query_id)
        queries.append(query)
    return queries
