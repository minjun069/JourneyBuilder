from __future__ import annotations

from copy import deepcopy
import unittest

from guardrails.plan_policy import evaluate_and_route


STEP_CANDIDATES = {
    "intro": ("qa",),
    "financial-goals": ("qa", "input_assist", "error_review"),
}


def raw_agent_plan(*, count: int = 1) -> dict:
    return {
        "status": "executable",
        "tasks": [
            {
                "id": f"answer-{index}",
                "type": "answer_question",
                "description": "현재 단계의 은퇴 설계 질문에 답변",
                "depends_on": [],
                "bindings": {},
            }
            for index in range(1, count + 1)
        ],
    }


def raw_tool_plan() -> dict:
    return {
        "status": "executable",
        "tasks": [{
            "id": "lookup",
            "type": "lookup_reference_value",
            "description": "승인된 부채상환 기준값 조회",
            "depends_on": [],
            "bindings": {},
            "arguments": {
                "reference_id": "debt_service_ratio_max",
                "age": 45,
                "period": "annual",
                "population": "retirewise_kr_households",
            },
        }],
    }


class PlanGuardrailPolicyTests(unittest.TestCase):
    def test_allows_agent_and_registered_tool_routes(self) -> None:
        agent = evaluate_and_route(
            raw_agent_plan(),
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        tool = evaluate_and_route(
            raw_tool_plan(),
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=("lookup_reference_value",),
        )

        self.assertTrue(agent.allowed)
        self.assertEqual(agent.plan["tasks"][0]["route"]["executor"], "qa")
        self.assertTrue(tool.allowed)

    def test_structural_validation_runs_before_route_creation(self) -> None:
        decision = evaluate_and_route(
            raw_agent_plan(count=4),
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(decision.reason_code, "INVALID_TASK_COUNT")

    def test_blocks_planner_route_injection(self) -> None:
        tampered = raw_agent_plan()
        tampered["tasks"][0]["route"] = {
            "executor_kind": "tool",
            "executor": "input_assist",
        }

        decision = evaluate_and_route(
            tampered,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(decision.reason_code, "UNKNOWN_TASK_FIELDS")
        self.assertIn(
            "PLANNER_EXECUTOR_INJECTION",
            {item.code for item in decision.violations},
        )

    def test_rejects_extra_root_fields(self) -> None:
        candidate = raw_agent_plan()
        candidate["trusted"] = True

        decision = evaluate_and_route(
            candidate,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(decision.reason_code, "INVALID_PLAN_FIELDS")

    def test_empty_trusted_registries_fail_in_structural_stage(self) -> None:
        agent = evaluate_and_route(
            raw_agent_plan(),
            step_id="intro",
            step_candidates={},
        )
        tool = evaluate_and_route(
            raw_tool_plan(),
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(agent.reason_code, "TASK_TYPE_NOT_ALLOWED_FOR_STEP")
        self.assertEqual(tool.reason_code, "TASK_TYPE_NOT_ALLOWED_FOR_STEP")

    def test_input_plan_is_not_mutated(self) -> None:
        raw_plan = raw_agent_plan()
        original = deepcopy(raw_plan)

        evaluate_and_route(
            raw_plan,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(raw_plan, original)


if __name__ == "__main__":
    unittest.main()
