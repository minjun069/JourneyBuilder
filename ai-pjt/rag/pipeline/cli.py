from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
_DEFAULT_POLICY = (
    _REPOSITORY_ROOT / "contract-fixtures" / "financial-calculation" / "v1" / "policy.json"
)
_OPERATIONAL_COLLECTIONS = ("interface-guides", "financial-knowledge")
_OPERATIONAL_EMBEDDING_MODEL = "gemini-embedding-2"
_OPERATIONAL_EMBEDDING_DIMENSIONS = 3072
_OPERATIONAL_MINIMUM_DENSE_SCORE = 0.54
_GMS_GEMINI_BASE_URL = (
    "https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com"
)


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
        choices=_OPERATIONAL_COLLECTIONS,
        dest="collections",
    )
    operational.add_argument("--index-version")
    operational.add_argument(
        "--artifact-kind",
        choices=("official", "experiment"),
        default="experiment",
        help="marks filenames, physical collection IDs, and manifests",
    )
    operational.add_argument("--environment-id", default="local")
    operational.add_argument("--operator-id")
    operational.add_argument("--source-revision")
    operational.add_argument(
        "--calculation-policy",
        type=Path,
        default=_DEFAULT_POLICY,
    )
    operational.add_argument("--chunk-size", type=int, default=900)
    operational.add_argument("--overlap", type=int, default=100)
    operational.add_argument("--top-k", type=int, default=5)
    operational.add_argument("--fetch-k", type=int, default=10)
    operational.add_argument(
        "--minimum-dense-score",
        type=float,
        default=_OPERATIONAL_MINIMUM_DENSE_SCORE,
    )
    operational.add_argument("--minimum-recall", type=float, default=0.85)
    operational.add_argument("--minimum-mrr", type=float, default=0.70)
    operational.add_argument("--retention-days", type=int, default=30)
    operational.add_argument("--promote", action="store_true")
    operational.add_argument(
        "--confirm-rag-owner",
        action="store_true",
        help="required when a RAG owner creates or promotes an official candidate",
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
    model = os.environ.get(
        "EMBEDDING_MODEL", _OPERATIONAL_EMBEDDING_MODEL
    ).strip()
    base_url = os.environ.get("GOOGLE_GEMINI_BASE_URL", "").strip()
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    try:
        dimensions = int(os.environ.get("EMBED_DIM", "3072"))
    except ValueError as error:
        raise ValueError("EMBED_DIM must be an integer") from error
    if provider != "gemini":
        raise ValueError("operational indexing currently supports EMBEDDING_PROVIDER=gemini")
    if model != _OPERATIONAL_EMBEDDING_MODEL:
        raise ValueError(
            "operational indexing requires "
            f"EMBEDDING_MODEL={_OPERATIONAL_EMBEDDING_MODEL}"
        )
    if base_url != _GMS_GEMINI_BASE_URL:
        raise ValueError(
            "operational indexing requires GOOGLE_GEMINI_BASE_URL="
            f"{_GMS_GEMINI_BASE_URL}"
        )
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is required for operational indexing")
    if dimensions != _OPERATIONAL_EMBEDDING_DIMENSIONS:
        raise ValueError(
            "operational indexing requires "
            f"EMBED_DIM={_OPERATIONAL_EMBEDDING_DIMENSIONS}"
        )
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repository_relative_path(path: Path, repository_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"path must be inside repository_root: {path}") from error


def _load_calculation_policy(
    path: Path, repository_root: Path
) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("calculation policy root must be an object")
    version = payload.get("policy_version")
    status = payload.get("status")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("calculation policy must declare policy_version")
    if status != "approved":
        raise ValueError("calculation policy must be approved")
    return {
        "path": _repository_relative_path(path, repository_root),
        "version": version,
        "status": status,
        "sha256": _sha256_file(path),
    }


def _source_revision(explicit: str | None, repository_root: Path) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    environment_revision = os.environ.get("RAG_SOURCE_REVISION", "").strip()
    if environment_revision:
        return environment_revision
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
            document.metadata.source_id: (
                document.metadata.version,
                document.metadata.sha256,
            )
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
    if not args.environment_id.strip():
        raise ValueError("environment-id must not be empty")
    if args.artifact_kind == "official":
        if set(args.collections or _OPERATIONAL_COLLECTIONS) != set(
            _OPERATIONAL_COLLECTIONS
        ):
            raise ValueError("official qualification must include both collections")
        if not args.operator_id or not args.operator_id.strip():
            raise ValueError("official qualification requires --operator-id")
        if not args.confirm_rag_owner:
            raise ValueError("official qualification requires --confirm-rag-owner")
    catalog = load_approved_catalog(
        args.catalog,
        repository_root=args.repository_root,
    )
    collections = args.collections or list(_OPERATIONAL_COLLECTIONS)
    if len(collections) != len(set(collections)):
        raise ValueError("a collection may be specified only once")
    index_version = args.index_version or (
        "catalog-" + catalog.catalog_version.replace(".", "-")
    )
    if index_version.startswith(("official-", "experiment-")):
        raise ValueError("index-version must not repeat the artifact kind prefix")
    calculation_policy = _load_calculation_policy(
        args.calculation_policy, args.repository_root
    )
    source_revision = _source_revision(args.source_revision, args.repository_root)
    embedding = _operational_embedding()
    store = PostgresIndexStore(database_url)
    store.ensure_schema()
    pipeline = IndexPipeline(
        store=store,
        chunker=MarkdownStructureChunker(args.chunk_size, args.overlap),
        embedding=embedding,
        approved_sources={
            document.metadata.source_id: (
                document.metadata.version,
                document.metadata.sha256,
            )
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
    collection_results: list[dict[str, object]] = []
    candidates = []
    active_before = {
        collection: store.active_collection(collection) for collection in collections
    }
    for collection in collections:
        target = IndexTarget(
            collection,
            index_version,
            operational=True,
            artifact_kind=args.artifact_kind,
        )
        try:
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
            result: dict[str, object] = {
                "logical_collection": collection,
                "physical_collection": target.physical_collection,
                "decision": "PASS" if report.passed else "FAIL",
                "manifest": manifest.to_dict(),
                "evaluation": report.to_dict(),
            }
            if report.passed:
                candidates.append((manifest, report))
        # Provider and database client exceptions are evidence for this collection,
        # so preserve the other collection result and finalize the qualification.
        except Exception as error:
            result = {
                "logical_collection": collection,
                "physical_collection": target.physical_collection,
                "decision": "ERROR",
                "error": str(error),
            }
        _write_json(
            args.report_dir / f"{target.physical_collection}-report.json",
            {
                "schema_version": "rag-collection-qualification/v1",
                "artifact_kind": args.artifact_kind,
                "environment_id": args.environment_id,
                **result,
            },
        )
        collection_results.append(result)

    all_passed = len(candidates) == len(collections) and all(
        result["decision"] == "PASS" for result in collection_results
    )
    includes_all_operational = set(collections) == set(_OPERATIONAL_COLLECTIONS)
    activatable = all_passed and includes_all_operational
    activation_performed = False
    activation_error = None
    pruned_versions: dict[str, int | str] = {
        collection: 0 for collection in collections
    }
    if args.promote and activatable:
        try:
            pipeline.promote_many(candidates)
            activation_performed = True
        except Exception as error:
            activation_error = str(error)
        if activation_performed:
            for collection in collections:
                try:
                    pruned_versions[collection] = store.prune_retired(
                        collection,
                        retention_days=args.retention_days,
                    )
                except Exception as error:
                    pruned_versions[collection] = f"ERROR: {error}"
    active_after = {
        collection: store.active_collection(collection) for collection in collections
    }
    qualification = {
        "schema_version": "rag-qualification/v1",
        "qualification_id": f"{args.artifact_kind}-{index_version}",
        "artifact_kind": args.artifact_kind,
        "environment_id": args.environment_id,
        "operator_id": args.operator_id,
        "source_revision": source_revision,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog": {
            "path": _repository_relative_path(args.catalog, args.repository_root),
            "version": catalog.catalog_version,
            "as_of_date": catalog.as_of_date,
            "sha256": _sha256_file(args.catalog),
        },
        "calculation_policy": calculation_policy,
        "embedding": {
            "provider": "gemini",
            "model": embedding.model_id,
            "dimensions": embedding.dimensions,
            "endpoint": "gms",
        },
        "retrieval": {
            "strategy": "dense-field-bm25-weighted-rrf",
            "top_k": args.top_k,
            "fetch_k": args.fetch_k,
            "minimum_dense_score": args.minimum_dense_score,
            "bm25_field_weights": {
                "title": 4.0,
                "aliases": 2.5,
                "body": 1.0,
            },
            "korean_particle_normalization": True,
            "exact_match_priority": ["title", "aliases", "body"],
        },
        "thresholds": {
            "recall_at_5_minimum": thresholds.minimum_recall,
            "mrr_at_5_minimum": thresholds.minimum_mrr,
            "provenance_minimum": thresholds.minimum_provenance,
            "collection_leakage_maximum": thresholds.maximum_collection_leakage,
            "false_evidence_maximum": thresholds.maximum_false_evidence,
        },
        "collections": collection_results,
        "query_count": sum(
            int(result.get("evaluation", {}).get("query_count", 0))
            for result in collection_results
        ),
        "overall_decision": "PASS" if all_passed else "FAIL",
        "activatable": activatable,
        "deployable": activatable and args.artifact_kind == "official",
        "activation": {
            "requested": args.promote,
            "performed": activation_performed,
            "error": activation_error,
            "before": active_before,
            "after": active_after,
        },
        "pruned_versions": pruned_versions,
    }
    qualification_path = (
        args.report_dir
        / f"{args.artifact_kind}__{index_version}__qualification.json"
    )
    _write_json(qualification_path, qualification)
    print(json.dumps(qualification, ensure_ascii=False, indent=2, sort_keys=True))
    if args.promote and (not activatable or activation_error is not None):
        return 1
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
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
