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

    def test_reused_documents_cover_definition_reading_order_cautions_and_links(self):
        expectations = {
            "balance-sheet": ("재무상태표", "현금흐름표", "해석 원칙"),
            "cash-flow": ("현금흐름표", "재무상태표", "입력 확인 체크리스트"),
            "financial-indicators": ("공통 계산 규칙", "결과 표현", "현금흐름"),
            "financial-goal-result": ("좋은 재무목표", "현재가치와 미래가치", "설명 원칙"),
            "retirement-fund": ("은퇴자금 보고서", "필요저축액 보고서", "주요 은퇴 위험"),
            "retirement-readiness": ("은퇴준비도 보고서", "생애 현금흐름 보고서", "주요 은퇴 위험"),
            "retirement-simulation": ("주요 은퇴 위험", "생애 현금흐름 보고서", "가정 사용 원칙"),
            "pension-schedule": ("연금일정 보고서", "생애 현금흐름 보고서", "공식 조회 우선순위"),
            "lifetime-cashflow": ("생애 현금흐름 보고서", "현금흐름표", "주요 은퇴 위험"),
            "required-savings": ("필요저축액 보고서", "필요저축액 해석", "설명 원칙"),
        }
        for step_id, terms in expectations.items():
            with self.subTest(step_id=step_id):
                context = load_chart_knowledge(
                    step_id,
                    list(CHART_KNOWLEDGE_SOURCES[step_id]),
                )
                for term in terms:
                    self.assertIn(term, context)
