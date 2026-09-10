from __future__ import annotations

from copy import deepcopy
import unittest

from guardrails.plan_policy import evaluate_route_plan


STEP_CANDIDATES = {
    "intro": ("qa", "fallback"),
    "financial-goals": ("qa", "input_assist", "error_review", "fallback"),
}


def agent_task(
    task_id: str = "task-1",
    *,
    step_id: str = "intro",
    executor: str = "qa",
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "id": task_id,
        "type": "answer_question",
        "description": "현재 단계의 은퇴 설계 질문에 답변",
        "depends_on": depends_on or [],
        "bindings": {},
        "executor_kind": "agent",
        "produces": "text_response",
        "step_id": step_id,
        "route": {
            "task_id": task_id,
            "executor_kind": "agent",
            "executor": executor,
            "reason_code": "intent_match",
        },
    }


def tool_task() -> dict:
    return {
        "id": "task-1",
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
        "executor_kind": "tool",
        "produces": "reference_value",
        "step_id": "financial-goals",
        "capability": "lookup_reference_value",
        "route": {
            "task_id": "task-1",
            "executor_kind": "tool",
            "executor": "lookup_reference_value",
            "reason_code": "capability_match",
        },
    }


class PlanGuardrailPolicyTests(unittest.TestCase):
    def test_allows_agent_and_registered_tool_routes(self) -> None:
        agent_decision = evaluate_route_plan(
            {"tasks": [agent_task()]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        tool_decision = evaluate_route_plan(
            {"tasks": [tool_task()]},
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=("lookup_reference_value",),
        )

        self.assertTrue(agent_decision.allowed)
        self.assertEqual(agent_decision.reason_code, "PLAN_GUARDRAIL_ALLOWED")
        self.assertTrue(tool_decision.allowed)

    def test_reuses_planner_task_limit_and_dependency_reasons(self) -> None:
        over_limit = evaluate_route_plan(
            {"tasks": [agent_task(f"task-{index}") for index in range(1, 5)]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        cyclic_tasks = [
            agent_task("task-1", depends_on=["task-2"]),
            agent_task("task-2", depends_on=["task-1"]),
        ]
        cyclic = evaluate_route_plan(
            {"tasks": cyclic_tasks},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(over_limit.reason_code, "INVALID_TASK_COUNT")
        self.assertEqual(cyclic.reason_code, "FORWARD_OR_CYCLIC_DEPENDENCY")

    def test_blocks_step_agent_and_tool_capability_tampering(self) -> None:
        disallowed_agent = evaluate_route_plan(
            {"tasks": [agent_task(executor="input_assist")]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        mismatched_tool = tool_task()
        mismatched_tool["route"]["executor"] = "get_dashboard_metric"
        tool_decision = evaluate_route_plan(
            {"tasks": [mismatched_tool]},
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=(
                "lookup_reference_value",
                "get_dashboard_metric",
            ),
        )

        self.assertEqual(disallowed_agent.reason_code, "AGENT_NOT_ALLOWLISTED")
        self.assertEqual(tool_decision.reason_code, "TOOL_CAPABILITY_MISMATCH")

    def test_accumulates_route_violations_with_stable_primary_reason(self) -> None:
        tampered = deepcopy(agent_task())
        tampered["route"].update({
            "task_id": "task-forged",
            "executor_kind": "tool",
            "executor": "input_assist",
            "scope": "admin",
        })

        decision = evaluate_route_plan(
            {"tasks": [tampered]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(decision.reason_code, "UNKNOWN_ROUTE_FIELDS")
        self.assertEqual(
            [violation.code for violation in decision.violations],
            [
                "UNKNOWN_ROUTE_FIELDS",
                "ROUTE_TASK_ID_MISMATCH",
                "ROUTE_EXECUTOR_KIND_MISMATCH",
                "AGENT_NOT_ALLOWLISTED",
            ],
        )

    def test_empty_trusted_registries_fail_closed(self) -> None:
        agent_decision = evaluate_route_plan(
            {"tasks": [agent_task()]},
            step_id="intro",
            step_candidates={},
        )
        tool_decision = evaluate_route_plan(
            {"tasks": [tool_task()]},
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=(),
        )

        self.assertEqual(agent_decision.reason_code, "UNKNOWN_STEP")
        self.assertEqual(tool_decision.reason_code, "TOOL_NOT_ALLOWLISTED")


if __name__ == "__main__":
    unittest.main()
