from __future__ import annotations

import unittest

from graph.router import select_route
from router_evaluation import load_contract_step_candidates, load_evaluation_fixture


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


class ProductionRoutingPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_evaluation_fixture()
        cls.step_candidates = load_contract_step_candidates()

    def test_all_p06_cases_match_agent_and_reason_code(self) -> None:
        for case in self.fixture["cases"]:
            classifier = FakeClassifier(case["classifier"])
            task = {
                "id": case["id"],
                "type": case["kind"],
                "description": case["message"],
                "executor_kind": "agent",
            }
            with self.subTest(case=case["id"]):
                decision = select_route(
                    task=task,
                    step_id=case["step_id"],
                    step_candidates=self.step_candidates,
                    classifier=classifier,
                )
                self.assertEqual(decision["task_id"], case["id"])
                self.assertEqual(decision["executor"], case["expected"]["agent"])
                self.assertEqual(
                    decision["reason_code"],
                    case["expected"]["reason_code"],
                )
                self.assertEqual(
                    len(classifier.calls),
                    case.get("expected_classifier_calls", 1),
                )

    def test_registered_tool_capability_is_selected_without_classifier(self) -> None:
        classifier = FakeClassifier({"status": "matched", "agent": "qa"})
        decision = select_route(
            task={
                "id": "task-1",
                "executor_kind": "tool",
                "capability": "lookup_reference_value",
            },
            step_id="financial-goals",
            step_candidates=self.step_candidates,
            classifier=classifier,
            registered_tool_capabilities=("lookup_reference_value",),
        )
        self.assertEqual(decision["executor_kind"], "tool")
        self.assertEqual(decision["executor"], "lookup_reference_value")
        self.assertEqual(decision["reason_code"], "capability_match")
        self.assertEqual(classifier.calls, [])

    def test_unregistered_tool_capability_fails_closed(self) -> None:
        decision = select_route(
            task={
                "id": "task-1",
                "executor_kind": "tool",
                "capability": "lookup_reference_value",
            },
            step_id="financial-goals",
            step_candidates=self.step_candidates,
            classifier=FakeClassifier({"status": "matched", "agent": "qa"}),
        )
        self.assertEqual(decision["executor_kind"], "tool")
        self.assertEqual(decision["reason_code"], "capability_unavailable")

    def test_unknown_executor_kind_falls_back(self) -> None:
        decision = select_route(
            task={"id": "task-1", "executor_kind": "shell"},
            step_id="intro",
            step_candidates=self.step_candidates,
            classifier=FakeClassifier({"status": "matched", "agent": "qa"}),
        )
        self.assertEqual(decision["executor"], "fallback")
        self.assertEqual(decision["reason_code"], "plan_invalid")


if __name__ == "__main__":
    unittest.main()
