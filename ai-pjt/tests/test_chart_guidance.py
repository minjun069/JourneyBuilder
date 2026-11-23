from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_API_KEY", "chart-guidance-test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@postgres/test")
os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "chart-guidance-test-bearer")
os.environ.setdefault(
    "AI_MASKING_PSEUDONYM_KEY",
    "chart-guidance-masking-key-at-least-32-bytes",
)


try:
    from agents.chart_interpret import service as chart_interpret
    from agents.chart_interpret import model as chart_model
except ModuleNotFoundError:
    chart_interpret = None
    chart_model = None


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "contract-fixtures" / "ai-lifecycle" / "v2" / "chart-payloads.json"


@unittest.skipIf(chart_interpret is None, "Agent runtime image에서 실행")
class ChartGuidanceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.ready = fixture["cases"]["lifetime_cashflow_ready"]["django_ai_request_fragment"]["chart_payload"]
        cls.guidance = fixture["cases"]["lifetime_cashflow_guidance_only"]["django_ai_request_fragment"]["chart_payload"]

    async def test_initial_entry_uses_direct_progressive_contract(self):
        model = AsyncMock(return_value=SimpleNamespace(
            content="핵심 결론입니다.\n\n- 첫 흐름\n- 둘째 흐름\n\n주의: 장기 가정을 확인하세요.",
        ))
        payload = deepcopy(self.ready)
        payload["connections"] = [{
            "step_id": "cash-flow",
            "title": "현금흐름",
            "highlights": [{"label": "월 순현금흐름", "display": "1,000,000원/월"}],
            "compatibility": {"latest": True, "status": "ready"},
        }]
        with (
            patch.object(chart_model, "secure_ainvoke", model),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
        ):
            result = await chart_interpret._run({
                "message": "이 보고서의 핵심을 먼저 안내해 주세요.",
                "step_id": "lifetime-cashflow",
                "request_mode": "presentation_entry",
                "chart_payload": payload,
            })

        self.assertEqual(result["output"]["response"].count("\n- "), 2)
        invocation_payload = model.await_args.args[1]
        for key in ("source_report", "provenance", "series", "details", "events", "connections"):
            self.assertIn(key, str(invocation_payload))
        self.assertEqual(model.await_args.kwargs["sensitive_payload"]["chart_facts"], payload)
        self.assertNotIn("customer_info", str(invocation_payload))

    async def test_model_response_is_delivered_without_chart_numeric_rejection(self):
        with (
            patch.object(
                chart_model,
                "secure_ainvoke",
                AsyncMock(return_value=SimpleNamespace(content="보고서 ID는 701원입니다.")),
            ),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
        ):
            result = await chart_interpret._run({
                "message": "처음 안내해 주세요.",
                "step_id": "lifetime-cashflow",
                "request_mode": "presentation_entry",
                "chart_payload": deepcopy(self.ready),
            })

        self.assertEqual(result["output"]["response"], "보고서 ID는 701원입니다.")

    async def test_new_numeric_claim_is_observed_without_changing_answer(self):
        model = AsyncMock(return_value=SimpleNamespace(content="근거 없는 999999원입니다."))
        with (
            patch.object(chart_model, "secure_ainvoke", model),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
            patch.object(chart_interpret, "record_operation_observation") as observed,
        ):
            result = await chart_interpret._run({
                "message": "원인을 설명해줘",
                "step_id": "lifetime-cashflow",
                "chart_payload": deepcopy(self.ready),
            })

        response = result["output"]["response"]
        self.assertEqual(response, "근거 없는 999999원입니다.")
        model.assert_awaited_once()
        self.assertEqual(observed.call_args.kwargs["output"]["delivery"], "model_output")
        self.assertGreater(observed.call_args.kwargs["output"]["unmatched_numeric_count"], 0)

    async def test_guidance_only_preserves_model_response(self):
        with (
            patch.object(
                chart_model,
                "secure_ainvoke",
                AsyncMock(return_value=SimpleNamespace(content="준비 전이지만 42원으로 보입니다.")),
            ),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
        ):
            result = await chart_interpret._run({
                "message": "처음 안내해줘",
                "step_id": "lifetime-cashflow",
                "request_mode": "presentation_entry",
                "chart_payload": deepcopy(self.guidance),
            })

        self.assertEqual(result["output"]["response"], "준비 전이지만 42원으로 보입니다.")

    def test_exact_payload_numbers_are_allowed(self):
        response = "순자산은 713,000,000원이고 적자 전환은 61세입니다."
        self.assertEqual(
            chart_interpret.unauthorized_numeric_claims(response, self.ready),
            set(),
        )

    async def test_chart_delivery_uses_one_model_call_and_records_diagnostics(self):
        answer = "시장 폭락 때문에 999999원이 부족합니다."
        model = AsyncMock(return_value=SimpleNamespace(content=answer))
        with (
            patch.object(chart_model, "secure_ainvoke", model),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
            patch.object(chart_interpret, "record_operation_observation") as observed,
        ):
            result = await chart_interpret._run({
                "message": "이 보고서의 핵심을 안내해 주세요.", "step_id": "lifetime-cashflow",
                "request_mode": "presentation_entry", "chart_payload": deepcopy(self.ready),
            })
        model.assert_awaited_once()
        self.assertEqual(result["output"]["response"], answer)
        output = observed.call_args.kwargs["output"]
        self.assertEqual(output["response"], answer)
        self.assertGreater(output["unmatched_numeric_count"], 0)
        self.assertGreater(output["causal_heuristic_count"], 0)

    async def test_model_failure_is_observed_and_propagated_without_retry(self):
        model = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        with (
            patch.object(chart_model, "secure_ainvoke", model),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
            patch.object(chart_interpret, "record_operation_observation") as observed,
        ):
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                await chart_interpret._run({
                    "message": "처음 안내해 주세요.", "step_id": "lifetime-cashflow",
                    "request_mode": "presentation_entry", "chart_payload": deepcopy(self.ready),
                })
        model.assert_awaited_once()
        self.assertEqual(observed.call_args.kwargs["error_code"], "RuntimeError")
        self.assertNotIn("provider unavailable", str(observed.call_args))

    async def test_unsupported_cause_is_delivered_as_generated(self):
        with (
            patch.object(
                chart_model,
                "secure_ainvoke",
                AsyncMock(return_value=SimpleNamespace(
                    content="시장 폭락 때문에 현금흐름이 바뀌었습니다.",
                )),
            ),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
        ):
            result = await chart_interpret._run({
                "message": "원인을 설명해줘",
                "step_id": "lifetime-cashflow",
                "chart_payload": deepcopy(self.ready),
            })

        self.assertEqual(result["output"]["response"], "시장 폭락 때문에 현금흐름이 바뀌었습니다.")

    async def test_customer_info_is_not_used_for_personalization(self):
        model = AsyncMock(return_value=SimpleNamespace(content="검증 결과를 확인했습니다."))
        with (
            patch.object(chart_model, "secure_ainvoke", model),
            patch.object(chart_model, "load_chart_knowledge", return_value="승인된 의미"),
        ):
            await chart_interpret._run({
                "message": "제 상황에 맞춰 설명해줘",
                "step_id": "lifetime-cashflow",
                "chart_payload": deepcopy(self.ready),
                "customer_info": {
                    "saved_data": {"email": "sensitive@example.com"},
                    "current_form": {"name": "민감이름"},
                },
            })

        invocation_payload = model.await_args.args[1]
        self.assertNotIn("sensitive@example.com", str(invocation_payload))
        self.assertNotIn("민감이름", str(invocation_payload))
