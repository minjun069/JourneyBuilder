from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chunking import DeterministicChunker
from .corpus import load_corpus, load_gold_queries
from .embeddings import DeterministicHashEmbedding
from .evaluation import EvaluationThresholds, evaluate_retriever
from .indexing import IndexPipeline, IndexedRetriever
from .models import IndexTarget
from .store import JsonVectorStore


_RAG_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_CORPUS = _RAG_DIR / "fixtures" / "synthetic" / "corpus.json"
_DEFAULT_GOLD = _RAG_DIR / "fixtures" / "synthetic" / "gold.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, evaluate, and promote an isolated synthetic RAG index."
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run-synthetic":
            return run_synthetic(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
