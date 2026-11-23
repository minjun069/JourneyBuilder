from __future__ import annotations

import re
import unittest

from rag.chart_knowledge import (
    CHART_KNOWLEDGE_SOURCES,
    ChartKnowledgeError,
    load_chart_knowledge,
)


class ChartKnowledgeTests(unittest.TestCase):
    def test_all_ten_steps_load_only_approved_hash_matched_text(self):
        self.assertEqual(len(CHART_KNOWLEDGE_SOURCES), 10)
        for step_id, sources in CHART_KNOWLEDGE_SOURCES.items():
            with self.subTest(step_id=step_id):
                context = load_chart_knowledge(step_id, list(sources))
                self.assertTrue(context)
                self.assertIsNone(re.search(r"(?<![A-Za-z가-힣])\d", context))
                self.assertNotIn("https://", context)

    def test_payload_cannot_expand_approved_scope(self):
        with self.assertRaisesRegex(ChartKnowledgeError, "scope mismatch"):
            load_chart_knowledge(
                "cash-flow",
                ["retirewise-household-balance-cash-flow", "unapproved"],
            )

    def test_entry_contains_only_the_current_report_guide(self):
        for step_id in CHART_KNOWLEDGE_SOURCES:
            with self.subTest(step_id=step_id):
                context = load_chart_knowledge(
                    step_id,
                    list(CHART_KNOWLEDGE_SOURCES[step_id]),
                )
                self.assertEqual(context.count("보고서:"), 1)
                self.assertIn("의미:", context)
                self.assertIn("읽는 순서:", context)
                self.assertIn("주의사항:", context)
                self.assertLess(len(context), 500)

    def test_detail_adds_relevant_sections_without_other_report_table_rows(self):
        context = load_chart_knowledge(
            "lifetime-cashflow", list(CHART_KNOWLEDGE_SOURCES["lifetime-cashflow"]),
            question="장기 가정과 은퇴 위험을 설명해줘",
        )
        self.assertIn("생애 현금흐름", context)
        self.assertIn("위험", context)
        self.assertNotIn("| 필요저축액 |", context)
        self.assertLessEqual(context.count("## "), 2)
        self.assertLessEqual(len(context), 2400)
