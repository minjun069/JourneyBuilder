from __future__ import annotations

import os
import unittest

from router_evaluation import load_contract_step_candidates, load_evaluation_fixture


ROUTER_TESTS_ENABLED = os.environ.get("AI_M2_L02_ROUTER_TESTS") == "1"


class FakeClassifier:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, *, task: dict, candidates: tuple[str, ...]) -> dict:
        self.calls.append({"task": task, "candidates": candidates})
        if self.result["status"] == "error":
            raise RuntimeError(self.result.get("error", "classifier error"))
        return {
            "status": self.result["status"],
            "agent": self.result.get("agent"),
        }


@unittest.skipUnless(
    ROUTER_TESTS_ENABLED,
    "M2-L02에서 pure router 구현 뒤 AI_M2_L02_ROUTER_TESTS=1로 활성화",
)
class M2L02PureRouterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from core.context import ContextScope, ContextStateAPI, ConversationContext
        from graph.router import select_route
        from graph.task_contracts import blocked_task_result, validate_task_result
        from tools.output_validation import validate_form_update_contract

        cls.ContextScope = ContextScope
        cls.ContextStateAPI = ContextStateAPI
        cls.ConversationContext = ConversationContext
        cls.select_route = staticmethod(select_route)
        cls.blocked_task_result = staticmethod(blocked_task_result)
        cls.validate_task_result = staticmethod(validate_task_result)
        cls.validate_form_update_contract = staticmethod(validate_form_update_contract)
        cls.fixture = load_evaluation_fixture()
        cls.step_candidates = load_contract_step_candidates()

    def test_evaluation_cases_match_agent_and_reason_code(self) -> None:
        for case in self.fixture["cases"]:
            classifier = FakeClassifier(case["classifier"])
            with self.subTest(case=case["id"]):
                decision = self.select_route(
                    task={
                        "id": case["id"],
                        "type": case["kind"],
                        "description": case["message"],
                        "executor_kind": "agent",
                    },
                    step_id=case["step_id"],
                    step_candidates=self.step_candidates,
                    classifier=classifier,
                )
                self.assertEqual(decision["executor"], case["expected"]["agent"])
                self.assertEqual(
                    decision["reason_code"], case["expected"]["reason_code"]
                )

                expected_calls = case.get("expected_classifier_calls", 1)
                self.assertEqual(len(classifier.calls), expected_calls)
                if classifier.calls:
                    self.assertEqual(
                        classifier.calls[0]["candidates"],
                        tuple(self.step_candidates[case["step_id"]]),
                    )

    def test_typed_result_and_dependency_failure_smoke(self) -> None:
        lookup_task = {
            "id": "task-1",
            "depends_on": [],
            "produces": "reference_value",
        }
        completed = self.validate_task_result(
            task=lookup_task,
            result={
                "task_id": "task-1",
                "status": "completed",
                "artifact": {
                    "type": "reference_value",
                    "schema_version": "1.0",
                    "producer_task_id": "task-1",
                    "reference_id": "monthly_target_expense",
                    "age": 45,
                    "value": 2_500_000,
                    "unit": "KRW",
                    "period": "monthly",
                    "population": "40대",
                    "comparator": "eq",
                    "source_id": "approved-statistics-2026",
                    "source_version": "1.0.0",
                    "as_of": "2026-06-30",
                    "calculation_policy_version": "2026-09-07.v1",
                },
            },
            step_id="financial-goals",
        )
        self.assertTrue(completed.valid)

        dependent_task = {
            "id": "task-2",
            "depends_on": ["task-1"],
            "produces": "form_change",
        }
        blocked = self.validate_task_result(
            task=dependent_task,
            result=self.blocked_task_result("task-2", "task-1"),
            step_id="financial-goals",
        )
        self.assertTrue(blocked.valid)

    def test_context_route_and_output_contracts_share_graph_state(self) -> None:
        scope = self.ContextScope(session_id=7, thread_id="thread-7")
        context_channels = self.ContextStateAPI.state_channels(
            scope,
            self.ConversationContext(),
        )
        decision = self.select_route(
            task={
                "id": "task-1",
                "type": "role_success",
                "description": "서비스 이용 방법을 설명해 줘.",
                "executor_kind": "agent",
            },
            step_id="intro",
            step_candidates=self.step_candidates,
            classifier=FakeClassifier({"status": "matched", "agent": "qa"}),
        )
        graph_state = {
            **context_channels,
            "step_id": "intro",
            "route": decision,
            "field_registry": [],
            "output": {
                "response": "서비스 이용 방법을 안내합니다.",
                "step_id": "intro",
                "action": None,
            },
        }
        output = self.validate_form_update_contract(graph_state)

        self.assertTrue(output.valid)
        self.assertEqual(graph_state["context_scope"]["session_id"], 7)
        self.assertEqual(graph_state["route"]["executor"], "qa")


if __name__ == "__main__":
    unittest.main()
