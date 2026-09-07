from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .chunking import DeterministicChunker, MarkdownStructureChunker
from .corpus import load_approved_catalog, load_corpus, load_gold_queries
from .embeddings import (
    DeterministicHashEmbedding,
    LlamaIndexEmbeddingAdapter,
)
from .evaluation import EvaluationThresholds, evaluate_retriever
from .indexing import IndexPipeline, IndexedRetriever
from .models import IndexTarget
from .store import JsonVectorStore, PostgresIndexStore


_RAG_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_CORPUS = _RAG_DIR / "fixtures" / "synthetic" / "corpus.json"
_DEFAULT_GOLD = _RAG_DIR / "fixtures" / "synthetic" / "gold.json"
_DEFAULT_CATALOG = _RAG_DIR / "data" / "catalog.json"
_DEFAULT_GOLD_DIR = _RAG_DIR / "evaluation"
_REPOSITORY_ROOT = _RAG_DIR.parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit, build, evaluate, and promote versioned RAG indexes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run-synthetic")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--corpus", type=Path, default=_DEFAULT_CORPUS)
    run.add_argument("--gold", type=Path, default=_DEFAULT_GOLD)
    run.add_argument("--collection", default="synthetic_financial_knowledge")
    run.add_argument("--index-version", default="v1")
    run.add_argument("--chunk-size", type=int, default=480)
    run.add_argument("--overlap", type=int, default=80)
    run.add_argument("--dimensions", type=int, default=128)
    run.add_argument("--top-k", type=int, default=5)
    run.add_argument("--evaluation-set-id", default="synthetic-rag-gold-v1")
    run.add_argument("--minimum-recall", type=float, default=0.85)
    run.add_argument("--minimum-mrr", type=float, default=0.70)

    dry_run = subparsers.add_parser("run-catalog-dry-run")
    dry_run.add_argument("--workspace", type=Path, required=True)
    dry_run.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    dry_run.add_argument("--repository-root", type=Path, default=_REPOSITORY_ROOT)
    dry_run.add_argument("--gold-dir", type=Path, default=_DEFAULT_GOLD_DIR)
    dry_run.add_argument("--index-version")
    dry_run.add_argument("--chunk-size", type=int, default=900)
    dry_run.add_argument("--overlap", type=int, default=100)
    dry_run.add_argument("--dimensions", type=int, default=2048)
    dry_run.add_argument("--top-k", type=int, default=5)
    dry_run.add_argument("--fetch-k", type=int, default=10)
    dry_run.add_argument("--minimum-dense-score", type=float, default=0.30)

    operational = subparsers.add_parser("run-operational")
    operational.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    operational.add_argument("--repository-root", type=Path, default=_REPOSITORY_ROOT)
    operational.add_argument("--gold-dir", type=Path, default=_DEFAULT_GOLD_DIR)
    operational.add_argument("--report-dir", type=Path, required=True)
    operational.add_argument(
        "--collection",
        action="append",
        choices=("interface-guides", "financial-knowledge"),
        dest="collections",
    )
    operational.add_argument("--index-version")
    operational.add_argument("--chunk-size", type=int, default=900)
    operational.add_argument("--overlap", type=int, default=100)
    operational.add_argument("--top-k", type=int, default=5)
    operational.add_argument("--fetch-k", type=int, default=10)
    operational.add_argument("--minimum-dense-score", type=float, default=0.30)
    operational.add_argument("--minimum-recall", type=float, default=0.85)
    operational.add_argument("--minimum-mrr", type=float, default=0.70)
    operational.add_argument("--retention-days", type=int, default=30)
    operational.add_argument("--promote", action="store_true")
    operational.add_argument(
        "--confirm-calculator-aligned",
        action="store_true",
        help="required to activate financial-knowledge until calculator alignment is complete",
    )
    return parser


def run_synthetic(args: argparse.Namespace) -> int:
    documents = load_corpus(args.corpus)
    gold_queries = load_gold_queries(args.gold)
    store = JsonVectorStore(args.workspace)
    embedding = DeterministicHashEmbedding(args.dimensions)
    pipeline = IndexPipeline(
        store=store,
        chunker=DeterministicChunker(args.chunk_size, args.overlap),
        embedding=embedding,
    )
    target = IndexTarget(
        collection=args.collection,
        index_version=args.index_version,
        operational=False,
    )
    manifest = pipeline.build(documents, target)
    report = evaluate_retriever(
        retriever=IndexedRetriever(store=store, embedding=embedding),
        manifest=manifest,
        gold_queries=gold_queries,
        evaluation_set_id=args.evaluation_set_id,
        top_k=args.top_k,
        thresholds=EvaluationThresholds(
            minimum_recall=args.minimum_recall,
            minimum_mrr=args.minimum_mrr,
        ),
    )
    if report.passed:
        pipeline.promote(manifest, report)

    output = {
        "schema_version": "rag-synthetic-run/v1",
        "manifest": manifest.to_dict(),
        "evaluation": report.to_dict(),
        "active_collection": store.active_collection(target.collection),
    }
    args.workspace.mkdir(parents=True, exist_ok=True)
    report_path = args.workspace / f"{target.physical_collection}-report.json"
    report_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.passed else 1


def _operational_embedding() -> LlamaIndexEmbeddingAdapter:
    provider = os.environ.get("EMBEDDING_PROVIDER", "gemini").strip()
    model = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001").strip()
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    try:
        dimensions = int(os.environ.get("EMBED_DIM", "3072"))
    except ValueError as error:
        raise ValueError("EMBED_DIM must be an integer") from error
    if provider != "gemini":
        raise ValueError("operational indexing currently supports EMBEDDING_PROVIDER=gemini")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is required for operational indexing")
    if dimensions < 1:
        raise ValueError("EMBED_DIM must be positive")
    try:
        from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
    except ImportError as error:
        raise RuntimeError("Google GenAI embedding adapter is not installed") from error
    embedding = GoogleGenAIEmbedding(model_name=model, api_key=api_key)
    return LlamaIndexEmbeddingAdapter(
        embedding,
        model_id=model,
        dimensions=dimensions,
    )


def run_catalog_dry_run(args: argparse.Namespace) -> int:
    catalog = load_approved_catalog(
        args.catalog,
        repository_root=args.repository_root,
    )
    index_version = args.index_version or (
        "catalog-" + catalog.catalog_version.replace(".", "-")
    )
    store = JsonVectorStore(args.workspace)
    embedding = DeterministicHashEmbedding(args.dimensions)
    pipeline = IndexPipeline(
        store=store,
        chunker=MarkdownStructureChunker(args.chunk_size, args.overlap),
        embedding=embedding,
        approved_sources={
            document.metadata.source_id: document.metadata.version
            for document in catalog.documents
        },
    )
    retriever = IndexedRetriever(
        store=store,
        embedding=embedding,
        hybrid=True,
        fetch_k=args.fetch_k,
        minimum_dense_score=args.minimum_dense_score,
    )
    outputs: list[dict[str, object]] = []
    all_passed = True
    for collection in ("interface-guides", "financial-knowledge"):
        target = IndexTarget(collection, index_version, operational=True)
        manifest = pipeline.build(catalog.for_collection(collection), target)
        report = evaluate_retriever(
            retriever=retriever,
            manifest=manifest,
            gold_queries=load_gold_queries(
                args.gold_dir / f"{collection}-gold-v1.json"
            ),
            evaluation_set_id=f"{collection}-gold-v1",
            top_k=args.top_k,
        )
        if report.passed:
            pipeline.promote(manifest, report)
        all_passed = all_passed and report.passed
        output = {
            "schema_version": "rag-catalog-dry-run/v1",
            "catalog_version": catalog.catalog_version,
            "manifest": manifest.to_dict(),
            "evaluation": report.to_dict(),
            "active_collection": store.active_collection(collection),
        }
        args.workspace.mkdir(parents=True, exist_ok=True)
        report_path = args.workspace / f"{target.physical_collection}-report.json"
        report_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        outputs.append(output)
    print(json.dumps({"runs": outputs}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all_passed else 1


def run_operational(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required for operational indexing")
    catalog = load_approved_catalog(
        args.catalog,
        repository_root=args.repository_root,
    )
    collections = args.collections or ["interface-guides", "financial-knowledge"]
    if (
        args.promote
        and "financial-knowledge" in collections
        and not args.confirm_calculator_aligned
    ):
        raise ValueError(
            "financial-knowledge activation is blocked until calculator alignment is confirmed"
        )
    index_version = args.index_version or (
        "catalog-" + catalog.catalog_version.replace(".", "-")
    )
    embedding = _operational_embedding()
    store = PostgresIndexStore(database_url)
    store.ensure_schema()
    pipeline = IndexPipeline(
        store=store,
        chunker=MarkdownStructureChunker(args.chunk_size, args.overlap),
        embedding=embedding,
        approved_sources={
            document.metadata.source_id: document.metadata.version
            for document in catalog.documents
        },
    )
    retriever = IndexedRetriever(
        store=store,
        embedding=embedding,
        hybrid=True,
        fetch_k=args.fetch_k,
        minimum_dense_score=args.minimum_dense_score,
    )
    thresholds = EvaluationThresholds(
        minimum_recall=args.minimum_recall,
        minimum_mrr=args.minimum_mrr,
        minimum_provenance=1.0,
        maximum_collection_leakage=0,
        maximum_false_evidence=0,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, object]] = []
    all_passed = True
    for collection in collections:
        target = IndexTarget(collection, index_version, operational=True)
        documents = catalog.for_collection(collection)
        manifest = pipeline.build(documents, target)
        gold_path = args.gold_dir / f"{collection}-gold-v1.json"
        gold_queries = load_gold_queries(gold_path)
        report = evaluate_retriever(
            retriever=retriever,
            manifest=manifest,
            gold_queries=gold_queries,
            evaluation_set_id=f"{collection}-gold-v1",
            top_k=args.top_k,
            thresholds=thresholds,
        )
        promoted = False
        pruned_versions = 0
        if report.passed and args.promote:
            pipeline.promote(manifest, report)
            promoted = True
            pruned_versions = store.prune_retired(
                collection,
                retention_days=args.retention_days,
            )
        all_passed = all_passed and report.passed
        output = {
            "schema_version": "rag-operational-run/v1",
            "catalog_version": catalog.catalog_version,
            "catalog_as_of_date": catalog.as_of_date,
            "manifest": manifest.to_dict(),
            "evaluation": report.to_dict(),
            "promoted": promoted,
            "active_collection": store.active_collection(collection),
            "pruned_versions": pruned_versions,
        }
        report_path = args.report_dir / f"{target.physical_collection}-report.json"
        report_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        outputs.append(output)
    print(json.dumps({"runs": outputs}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run-synthetic":
            return run_synthetic(args)
        if args.command == "run-catalog-dry-run":
            return run_catalog_dry_run(args)
        if args.command == "run-operational":
            return run_operational(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
