"""Shared read-only validation for pending public counseling source documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from rag.pipeline.models import DocumentMetadata, SourceDocument


RAG_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = (
    RAG_ROOT.parents[1]
    if RAG_ROOT.parents[1] / "ai-pjt/rag" == RAG_ROOT
    else RAG_ROOT.parent
)
DEFAULT_MANIFEST = RAG_ROOT / "data" / "counseling-candidate.json"
DOCUMENT_DIRECTORY = Path("ai-pjt/rag/data/financial-knowledge")


@dataclass(frozen=True)
class CandidateCorpus:
    version: str
    manifest_sha256: str
    documents: tuple[SourceDocument, ...]


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _date(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    value = _text(value, name)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} must be an ISO date")
    date.fromisoformat(value)
    return value


def _url(value: Any, name: str) -> str:
    value = _text(value, name)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be a public HTTPS source URL")
    return value


def load_candidate(path: Path, *, repository_root: Path) -> CandidateCorpus:
    """Validate a pending manifest without passing it through the approved loader."""

    content = path.read_bytes()
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("schema_version") != "counseling-candidate/v1":
        raise ValueError("unsupported counseling candidate schema")
    if payload.get("hash_algorithm") != "sha256":
        raise ValueError("candidate hash_algorithm must be sha256")
    version = _text(payload.get("catalog_version"), "catalog_version")
    _date(payload.get("as_of_date"), "as_of_date")
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict) or acceptance.get("status") != "pending":
        raise ValueError("candidate acceptance must remain pending")
    if not {"approved_at", "reviewed_by"}.issubset(acceptance) or any(
        acceptance[name] is not None for name in ("approved_at", "reviewed_by")
    ):
        raise ValueError("pending candidate must not claim approval or a reviewer")
    if acceptance.get("sensitivity") != "public":
        raise ValueError("candidate corpus must contain public knowledge only")
    boundary = _text(acceptance.get("service_boundary"), "service_boundary")
    license_text = _text(acceptance.get("license"), "license")

    source_records = payload.get("sources")
    if not isinstance(source_records, list) or not source_records:
        raise ValueError("sources must be a non-empty list")
    sources: dict[str, dict[str, Any]] = {}
    for source in source_records:
        if not isinstance(source, dict):
            raise ValueError("source must be an object")
        source_id = _text(source.get("id"), "source.id")
        if source_id in sources:
            raise ValueError(f"duplicate source registry id: {source_id}")
        for field in ("publisher", "title", "locator"):
            _text(source.get(field), f"source.{field}")
        _url(source.get("url"), "source.url")
        _date(source.get("verified_at"), "source.verified_at")
        _date(source.get("published_at"), "source.published_at", nullable=True)
        if source.get("usage") not in {"adapted-concepts", "reference-only"}:
            raise ValueError("source usage must identify adaptation or reference only")
        rights = source.get("rights")
        if not isinstance(rights, dict) or rights.get("status") not in {"public-domain", "reference-only"}:
            raise ValueError("source rights status is required")
        _url(rights.get("url"), "source.rights.url")
        _text(rights.get("note"), "source.rights.note")
        sources[source_id] = source

    records = payload.get("documents")
    if not isinstance(records, list) or not records:
        raise ValueError("candidate documents must be a non-empty list")
    root = repository_root.resolve()
    repository_layout = (root / "ai-pjt/rag/data").is_dir()
    bundled_layout = (root / "rag/data").is_dir()
    if repository_layout == bundled_layout:
        raise ValueError("candidate root must have exactly one repository or bundled data layout")
    documents: list[SourceDocument] = []
    ids: set[str] = set()
    source_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("candidate document must be an object")
        document_id = _text(record.get("id"), "document.id")
        source_id = _text(record.get("source_id"), "document.source_id")
        if document_id in ids or source_id in source_ids:
            raise ValueError("duplicate candidate document id or source_id")
        ids.add(document_id)
        source_ids.add(source_id)
        if record.get("document_type") != "financial-knowledge" or record.get("collection") != "financial-knowledge":
            raise ValueError("candidate document must target financial-knowledge")
        if any(record.get(field) not in (None, "pending") for field in ("review_status", "approval_status")):
            raise ValueError("candidate document must not claim approval")
        relative = Path(_text(record.get("path"), "document.path"))
        if relative.parent != DOCUMENT_DIRECTORY or not re.fullmatch(r"counseling-[a-z0-9-]+\.md", relative.name):
            raise ValueError("candidate path must name a counseling Markdown document")
        # The manifest's canonical path allowlist remains unchanged. Docker
        # copies ai-pjt/ into /app, so only that known prefix is removed there.
        local_relative = relative if repository_layout else Path(*relative.parts[1:])
        document_path = root / local_relative
        resolved = document_path.resolve()
        if resolved != document_path or not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("candidate path is missing, a symlink, or escapes repository")
        document_bytes = resolved.read_bytes()
        digest = hashlib.sha256(document_bytes).hexdigest()
        if record.get("sha256") != digest:
            raise ValueError(f"candidate sha256 mismatch: {source_id}")
        refs = record.get("source_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            raise ValueError("source_refs must be a non-empty string list")
        if len(set(refs)) != len(refs) or set(refs) - sources.keys():
            raise ValueError(f"duplicate or unknown source_refs: {source_id}")
        applies_to = record.get("applies_to")
        if not isinstance(applies_to, dict) or applies_to.get("country") != "KR":
            raise ValueError("candidate applies_to must identify KR")
        _text(applies_to.get("topic"), "applies_to.topic")
        refresh = record.get("refresh")
        if not isinstance(refresh, dict) or type(refresh.get("cadence_days")) is not int or refresh["cadence_days"] < 1:
            raise ValueError("refresh.cadence_days must be a positive integer")
        triggers = refresh.get("triggers")
        if not isinstance(triggers, list) or not all(isinstance(item, str) and item.strip() for item in triggers):
            raise ValueError("refresh.triggers must be a string list")
        metadata = DocumentMetadata(
            source_id=source_id,
            title=_text(record.get("title"), "document.title"),
            publisher=_text(record.get("publisher"), "document.publisher"),
            source_locators=tuple(dict.fromkeys(sources[ref]["url"] for ref in refs)),
            published_at=_date(record.get("published_at"), "document.published_at", nullable=True),
            as_of_date=_date(record.get("as_of_date"), "document.as_of_date"),
            version=_text(record.get("version"), "document.version"),
            scope=json.dumps({"service_boundary": boundary, "applies_to": applies_to}, ensure_ascii=False, sort_keys=True),
            review_status="pending",
            # The shared model requires text here. This is an absence sentinel,
            # not a reviewer identity; the source manifest retains null.
            reviewed_by="unreviewed",
            reviewed_at=None,
            document_kind="financial-knowledge",
            collection="financial-knowledge",
            license=license_text,
            sensitivity="public",
            sha256=digest,
            catalog_document_id=document_id,
            file_path=relative.as_posix(),
            refresh_cadence_days=refresh["cadence_days"],
            retire_when=_text(record.get("retire_when"), "document.retire_when"),
        )
        documents.append(SourceDocument(metadata=metadata, text=document_bytes.decode("utf-8")))
    return CandidateCorpus(version, hashlib.sha256(content).hexdigest(), tuple(documents))
