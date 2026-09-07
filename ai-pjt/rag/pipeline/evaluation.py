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
    minimum_provenance: float = 1.0
    maximum_collection_leakage: int = 0
    maximum_false_evidence: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_recall", self.minimum_recall),
            ("minimum_mrr", self.minimum_mrr),
            ("minimum_provenance", self.minimum_provenance),
        ):
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.maximum_collection_leakage < 0:
            raise ValueError("maximum_collection_leakage must not be negative")
        if self.maximum_false_evidence < 0:
            raise ValueError("maximum_false_evidence must not be negative")


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
    indexed_sources = {source_id for source_id, _ in manifest.source_versions}
    unknown_sources = sorted(
        {
            source_id
            for item in gold_queries
            for source_id in item.relevant_source_ids
            if source_id not in indexed_sources
        }
    )
    if unknown_sources:
        raise ValueError(
            f"gold queries reference sources outside the manifest: {unknown_sources}"
        )
    evaluation_payload = [
        {
            "query_id": item.query_id,
            "query": item.query,
            "relevant_source_ids": list(item.relevant_source_ids),
            "category": item.category,
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
    provenance_total = 0
    provenance_complete = 0
    collection_leakage_count = 0
    false_evidence_count = 0
    for gold in gold_queries:
        results = retriever.retrieve_physical(
            manifest.physical_collection, gold.query, top_k=top_k
        )
        retrieved = tuple(result.chunk.metadata.source_id for result in results)
        for result in results:
            provenance_total += 1
            metadata = result.chunk.metadata
            if (
                metadata.source_id
                and metadata.title
                and metadata.publisher
                and metadata.version
                and metadata.as_of_date
                and metadata.source_locators
                and result.chunk.chunk_id
                and result.chunk.end_offset > result.chunk.start_offset
            ):
                provenance_complete += 1
            if metadata.collection != manifest.collection:
                collection_leakage_count += 1
        if gold.category == "no_result" and results:
            false_evidence_count += 1
        relevant = set(gold.relevant_source_ids)
        recalled = relevant.intersection(retrieved)
        recall = len(recalled) / len(relevant) if relevant else 0.0

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
                category=gold.category,
                no_result_passed=(not results if gold.category == "no_result" else None),
            )
        )

    positive_reports = [
        item for item in query_reports if item.category != "no_result"
    ]
    if not positive_reports:
        raise ValueError("gold_queries must contain at least one positive query")
    count = len(positive_reports)
    recall_at_k = sum(item.recall_at_k for item in positive_reports) / count
    mrr_at_k = sum(item.reciprocal_rank_at_k for item in positive_reports) / count
    context_precision = (
        sum(item.context_precision_at_k for item in positive_reports) / count
    )
    provenance = provenance_complete / provenance_total if provenance_total else 1.0
    return EvaluationReport(
        physical_collection=manifest.physical_collection,
        corpus_digest=manifest.corpus_digest,
        evaluation_set_id=evaluation_set_id,
        evaluation_set_digest=evaluation_set_digest,
        top_k=top_k,
        query_count=len(query_reports),
        positive_query_count=count,
        no_result_query_count=len(query_reports) - count,
        recall_at_k=recall_at_k,
        mrr_at_k=mrr_at_k,
        context_precision_at_k=context_precision,
        provenance_completeness=provenance,
        collection_leakage_count=collection_leakage_count,
        false_evidence_count=false_evidence_count,
        minimum_recall=thresholds.minimum_recall,
        minimum_mrr=thresholds.minimum_mrr,
        minimum_provenance=thresholds.minimum_provenance,
        maximum_collection_leakage=thresholds.maximum_collection_leakage,
        maximum_false_evidence=thresholds.maximum_false_evidence,
        passed=(
            recall_at_k >= thresholds.minimum_recall
            and mrr_at_k >= thresholds.minimum_mrr
            and provenance >= thresholds.minimum_provenance
            and collection_leakage_count <= thresholds.maximum_collection_leakage
            and false_evidence_count <= thresholds.maximum_false_evidence
        ),
        queries=tuple(query_reports),
    )
