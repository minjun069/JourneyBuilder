"""Build-time verification for static assets required by production Agents."""

from __future__ import annotations

from agents.error_review.manuals import SUPPORTED_STEPS, load_error_review_manual
from rag.chart_knowledge import CHART_KNOWLEDGE_SOURCES, load_chart_knowledge


def verify_runtime_assets() -> None:
    """Require every direct catalog consumer to load its packaged assets."""

    for step_id, source_ids in CHART_KNOWLEDGE_SOURCES.items():
        load_chart_knowledge(step_id, list(source_ids))
    for step_id in SUPPORTED_STEPS:
        load_error_review_manual(step_id)


if __name__ == "__main__":
    verify_runtime_assets()
