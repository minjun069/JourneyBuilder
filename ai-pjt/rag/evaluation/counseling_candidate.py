"""Offline retrieval evaluation for unapproved counseling documents.

This module never approves a source, connects to a database, or activates an
index. Hash embeddings measure repeatable retrieval behavior, not LLM answers
or the quality of the production Gemini embedding model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from rag.pipeline.chunking import MarkdownStructureChunker
from rag.pipeline.corpus import load_approved_catalog, load_gold_queries
from rag.pipeline.embeddings import DeterministicHashEmbedding
from rag.pipeline.evaluation import evaluate_retriever
from rag.pipeline.indexing import IndexedRetriever, corpus_digest
from rag.pipeline.models import (
    DocumentMetadata,
    EmbeddedChunk,
    GoldQuery,
    IndexManifest,
    SourceDocument,
)
from rag.pipeline.store import JsonVectorStore


RAG_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = RAG_ROOT.parents[1]
DEFAULT_MANIFEST = RAG_ROOT / "data" / "counseling-candidate.json"
DEFAULT_GOLD = RAG_ROOT / "evaluation" / "counseling-candidate-gold-v1.json"
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
        document_path = root / relative
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


def _evaluate(documents: list[SourceDocument], queries: list[GoldQuery], *, name: str) -> dict[str, Any]:
    if len({document.metadata.source_id for document in documents}) != len(documents):
        raise ValueError("candidate and approved corpus source ids must not overlap")
    chunker = MarkdownStructureChunker(900, 100)
    embedding = DeterministicHashEmbedding(2048)
    chunks = chunker.chunk_documents(documents)
    vectors = embedding.embed_texts([chunk.text for chunk in chunks])
    physical = f"candidate-financial-knowledge__{name}"
    manifest = IndexManifest(
        collection="financial-knowledge", physical_collection=physical,
        index_version=name, corpus_digest=corpus_digest(documents),
        document_kind="financial-knowledge", document_count=len(documents),
        chunk_count=len(chunks), chunker_id=chunker.chunker_id,
        embedding_model=embedding.model_id, embedding_dimensions=embedding.dimensions,
        source_versions=tuple(sorted((doc.metadata.source_id, doc.metadata.version) for doc in documents)),
        built_at=datetime.now(timezone.utc).isoformat(), artifact_kind="experiment",
    )
    with tempfile.TemporaryDirectory(prefix="counseling-candidate-") as directory:
        store = JsonVectorStore(Path(directory))
        store.upsert(physical, [EmbeddedChunk(chunk, vector) for chunk, vector in zip(chunks, vectors)])
        store.write_manifest(physical, manifest)
        retriever = IndexedRetriever(store=store, embedding=embedding, hybrid=True, fetch_k=10, minimum_dense_score=0.30)
        evaluation = evaluate_retriever(
            retriever=retriever, manifest=manifest, gold_queries=queries,
            evaluation_set_id=f"counseling-candidate-{name}", top_k=5,
        )
        if store.active_collection("financial-knowledge") is not None:
            raise RuntimeError("candidate evaluation must never activate an index")
    return {"manifest": manifest.to_dict(), "evaluation": evaluation.to_dict()}


def evaluate_candidate(
    manifest_path: Path = DEFAULT_MANIFEST,
    gold_path: Path = DEFAULT_GOLD,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    approved_catalog_path: Path | None = None,
    baseline_gold_path: Path | None = None,
) -> dict[str, Any]:
    candidate = load_candidate(manifest_path, repository_root=repository_root)
    queries = load_gold_queries(gold_path)
    result: dict[str, Any] = {
        "schema_version": "counseling-candidate-evaluation/v1",
        "candidate_version": candidate.version,
        "candidate_manifest_sha256": candidate.manifest_sha256,
        "acceptance": "pending", "deployable": False, "llm_evaluated": False,
        "limitations": [
            "Deterministic hash embeddings are an offline regression check, not production Gemini qualification.",
            "Counseling responses, session-understanding updates, and LLM safety have not been evaluated.",
            "No database, active index, approved catalog, or official artifact is modified.",
        ],
        "candidate_only": _evaluate(list(candidate.documents), queries, name="candidate-only"),
    }
    if (approved_catalog_path is None) != (baseline_gold_path is None):
        raise ValueError("mixed comparison requires both approved catalog and baseline gold")
    if approved_catalog_path is not None and baseline_gold_path is not None:
        approved = list(load_approved_catalog(approved_catalog_path, repository_root=repository_root).for_collection("financial-knowledge"))
        baseline_queries = load_gold_queries(baseline_gold_path)
        mixed = [*approved, *candidate.documents]
        result["approved_baseline"] = _evaluate(approved, baseline_queries, name="approved-baseline")
        result["mixed_existing_queries"] = _evaluate(mixed, baseline_queries, name="mixed-existing")
        result["mixed_candidate_queries"] = _evaluate(mixed, queries, name="mixed-candidate")
        before = result["approved_baseline"]["evaluation"]
        after = result["mixed_existing_queries"]["evaluation"]
        result["existing_query_delta"] = {
            metric: after[metric] - before[metric]
            for metric in ("recall_at_k", "mrr_at_k", "context_precision_at_k", "false_evidence_count")
        }
    result["retrieval_checks_passed"] = all(
        value["evaluation"]["passed"] for value in result.values()
        if isinstance(value, dict) and "evaluation" in value
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--candidate-only", action="store_true", help="skip comparison against existing financial knowledge")
    args = parser.parse_args(argv)
    try:
        rag_root = args.repository_root / "ai-pjt" / "rag"
        report = evaluate_candidate(
            args.manifest, args.gold, repository_root=args.repository_root,
            approved_catalog_path=None if args.candidate_only else rag_root / "data" / "catalog.json",
            baseline_gold_path=None if args.candidate_only else rag_root / "evaluation" / "financial-knowledge-gold-v1.json",
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0 if report["retrieval_checks_passed"] else 1
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"counseling candidate evaluation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
