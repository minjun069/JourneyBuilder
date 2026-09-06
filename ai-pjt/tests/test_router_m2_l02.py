from __future__ import annotations

import os
import unittest

from router_evaluation import load_contract_step_candidates, load_evaluation_fixture


ROUTER_TESTS_ENABLED = os.environ.get("AI_M2_L02_ROUTER_TESTS") == "1"


class FakeClassifier:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, *, message: str, candidates: tuple[str, ...]) -> dict:
        self.calls.append({"message": message, "candidates": candidates})
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
        from graph.router import select_route

        cls.select_route = staticmethod(select_route)
        cls.fixture = load_evaluation_fixture()
        cls.step_candidates = load_contract_step_candidates()

    def test_evaluation_cases_match_agent_and_reason_code(self) -> None:
        for case in self.fixture["cases"]:
            classifier = FakeClassifier(case["classifier"])
            with self.subTest(case=case["id"]):
                decision = self.select_route(
                    step_id=case["step_id"],
                    message=case["message"],
                    step_candidates=self.step_candidates,
                    classifier=classifier,
                )
                self.assertEqual(decision, case["expected"])

                expected_calls = case.get("expected_classifier_calls", 1)
                self.assertEqual(len(classifier.calls), expected_calls)
                if classifier.calls:
                    self.assertEqual(
                        classifier.calls[0]["candidates"],
                        tuple(self.step_candidates[case["step_id"]]),
                    )


if __name__ == "__main__":
    unittest.main()
