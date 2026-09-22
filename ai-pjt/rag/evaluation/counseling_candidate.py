"""Offline retrieval evaluation for unapproved counseling documents.

This module never approves a source, connects to a database, or activates an
index. Hash embeddings measure repeatable retrieval behavior, not LLM answers
or the quality of the production Gemini embedding model.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.counseling_corpus import CandidateCorpus, load_candidate
from rag.pipeline.chunking import MarkdownStructureChunker
from rag.pipeline.corpus import load_approved_catalog, load_gold_queries
from rag.pipeline.embeddings import DeterministicHashEmbedding
from rag.pipeline.evaluation import evaluate_retriever
from rag.pipeline.indexing import IndexedRetriever, corpus_digest
from rag.pipeline.models import (
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
