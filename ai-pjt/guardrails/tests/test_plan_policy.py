from __future__ import annotations

from copy import deepcopy
import unittest

from graph.router import enrich_route_plan
from guardrails.plan_policy import evaluate_route_plan


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


def candidate(raw_plan: dict, *, step_id: str, tools: tuple[str, ...] = ()) -> dict:
    return enrich_route_plan(
        raw_plan=raw_plan,
        step_id=step_id,
        registered_tool_capabilities=tools,
        plan_id="plan-test",
    )


class PlanGuardrailPolicyTests(unittest.TestCase):
    def test_allows_agent_and_registered_tool_routes(self) -> None:
        agent_decision = evaluate_route_plan(
            candidate(raw_agent_plan(), step_id="intro"),
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        tool_decision = evaluate_route_plan(
            candidate(
                raw_tool_plan(),
                step_id="financial-goals",
                tools=("lookup_reference_value",),
            ),
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=("lookup_reference_value",),
        )

        self.assertTrue(agent_decision.allowed)
        self.assertEqual(agent_decision.reason_code, "PLAN_GUARDRAIL_ALLOWED")
        self.assertEqual(
            agent_decision.plan["tasks"][0]["route"]["executor"],
            "qa",
        )
        self.assertTrue(tool_decision.allowed)

    def test_structural_validation_runs_before_route_validation(self) -> None:
        decision = evaluate_route_plan(
            candidate(raw_agent_plan(count=4), step_id="intro"),
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(decision.reason_code, "INVALID_TASK_COUNT")
        self.assertEqual(
            {violation.code for violation in decision.violations},
            {"INVALID_TASK_COUNT"},
        )

    def test_blocks_untrusted_route_enrichment_tampering(self) -> None:
        tampered = candidate(raw_agent_plan(), step_id="intro")
        tampered["route_enrichments"][0].update({
            "source_task_id": "forged",
            "executor_kind": "tool",
            "executor": "input_assist",
        })

        decision = evaluate_route_plan(
            tampered,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(decision.reason_code, "ROUTE_SOURCE_TASK_MISMATCH")
        self.assertEqual(
            [violation.code for violation in decision.violations],
            [
                "ROUTE_SOURCE_TASK_MISMATCH",
                "ROUTE_EXECUTOR_KIND_MISMATCH",
                "ROUTE_EXECUTOR_MISMATCH",
                "AGENT_NOT_ALLOWLISTED",
            ],
        )

    def test_rejects_extra_candidate_or_enrichment_fields(self) -> None:
        extra_candidate = candidate(raw_agent_plan(), step_id="intro")
        extra_candidate["trusted"] = True
        candidate_decision = evaluate_route_plan(
            extra_candidate,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        extra_route = candidate(raw_agent_plan(), step_id="intro")
        extra_route["route_enrichments"][0]["scope"] = "admin"
        route_decision = evaluate_route_plan(
            extra_route,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(
            candidate_decision.reason_code,
            "INVALID_PLAN_CANDIDATE_FIELDS",
        )
        self.assertEqual(route_decision.reason_code, "INVALID_ROUTE_FIELDS")

    def test_empty_trusted_registries_fail_in_structural_stage(self) -> None:
        agent_decision = evaluate_route_plan(
            candidate(raw_agent_plan(), step_id="intro"),
            step_id="intro",
            step_candidates={},
        )
        tool_decision = evaluate_route_plan(
            candidate(raw_tool_plan(), step_id="financial-goals"),
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=(),
        )

        self.assertEqual(
            agent_decision.reason_code,
            "TASK_TYPE_NOT_ALLOWED_FOR_STEP",
        )
        self.assertEqual(
            tool_decision.reason_code,
            "TASK_TYPE_NOT_ALLOWED_FOR_STEP",
        )

    def test_input_candidate_is_not_mutated(self) -> None:
        route_candidate = candidate(raw_agent_plan(), step_id="intro")
        original = deepcopy(route_candidate)

        evaluate_route_plan(
            route_candidate,
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(route_candidate, original)


if __name__ == "__main__":
    unittest.main()
