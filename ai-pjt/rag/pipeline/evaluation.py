from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .indexing import IndexedRetriever
from .models import (
    EvaluationReport,
    GoldQuery,
    IndexManifest,
    QueryEvaluation,
)


@dataclass(frozen=True)
class EvaluationThresholds:
    minimum_recall: float = 0.85
    minimum_mrr: float = 0.70

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_recall", self.minimum_recall),
            ("minimum_mrr", self.minimum_mrr),
        ):
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


def evaluate_retriever(
    *,
    retriever: IndexedRetriever,
    manifest: IndexManifest,
    gold_queries: list[GoldQuery],
    evaluation_set_id: str = "inline",
    top_k: int = 5,
    thresholds: EvaluationThresholds | None = None,
) -> EvaluationReport:
    if not gold_queries:
        raise ValueError("gold_queries must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if not evaluation_set_id.strip():
        raise ValueError("evaluation_set_id must not be empty")
    thresholds = thresholds or EvaluationThresholds()
    evaluation_payload = [
        {
            "query_id": item.query_id,
            "query": item.query,
            "relevant_source_ids": list(item.relevant_source_ids),
        }
        for item in gold_queries
    ]
    evaluation_set_digest = hashlib.sha256(
        json.dumps(
            evaluation_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    query_reports: list[QueryEvaluation] = []
    for gold in gold_queries:
        results = retriever.retrieve_physical(
            manifest.physical_collection, gold.query, top_k=top_k
        )
        retrieved = tuple(result.chunk.metadata.source_id for result in results)
        relevant = set(gold.relevant_source_ids)
        recalled = relevant.intersection(retrieved)
        recall = len(recalled) / len(relevant)

        reciprocal_rank = 0.0
        for rank, source_id in enumerate(retrieved, start=1):
            if source_id in relevant:
                reciprocal_rank = 1.0 / rank
                break

        precision = (
            sum(source_id in relevant for source_id in retrieved) / len(retrieved)
            if retrieved
            else 0.0
        )
        query_reports.append(
            QueryEvaluation(
                query_id=gold.query_id,
                relevant_source_ids=gold.relevant_source_ids,
                recall_at_k=recall,
                reciprocal_rank_at_k=reciprocal_rank,
                context_precision_at_k=precision,
                retrieved_source_ids=retrieved,
            )
        )

    count = len(query_reports)
    recall_at_k = sum(item.recall_at_k for item in query_reports) / count
    mrr_at_k = sum(item.reciprocal_rank_at_k for item in query_reports) / count
    context_precision = (
        sum(item.context_precision_at_k for item in query_reports) / count
    )
    return EvaluationReport(
        physical_collection=manifest.physical_collection,
        corpus_digest=manifest.corpus_digest,
        evaluation_set_id=evaluation_set_id,
        evaluation_set_digest=evaluation_set_digest,
        top_k=top_k,
        query_count=count,
        recall_at_k=recall_at_k,
        mrr_at_k=mrr_at_k,
        context_precision_at_k=context_precision,
        minimum_recall=thresholds.minimum_recall,
        minimum_mrr=thresholds.minimum_mrr,
        passed=(
            recall_at_k >= thresholds.minimum_recall
            and mrr_at_k >= thresholds.minimum_mrr
        ),
        queries=tuple(query_reports),
    )
