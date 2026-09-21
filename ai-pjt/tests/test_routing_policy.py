from __future__ import annotations

import unittest

from graph.task_catalog import allowed_task_types_for
from guardrails.plan_policy import evaluate_and_route


STEP_CANDIDATES = {
    "intro": ("qa",),
    "financial-goals": ("qa", "input_assist", "error_review"),
}


class DeterministicRoutingPolicyTests(unittest.TestCase):
    def test_agent_task_type_has_one_canonical_route(self) -> None:
        decision = evaluate_and_route(
            {"status": "executable", "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": "안정추구의 뜻을 설명",
                "depends_on": [],
                "bindings": {},
            }]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.plan["tasks"][0]["route"], {
            "task_id": "task-1",
            "executor_kind": "agent",
            "executor": "qa",
            "reason_code": "intent_match",
        })

    def test_tool_route_requires_registered_capability(self) -> None:
        raw_plan = {"status": "executable", "tasks": [{
            "id": "lookup",
            "type": "lookup_reference_value",
            "description": "기준값 조회",
            "depends_on": [],
            "bindings": {},
            "arguments": {
                "reference_id": "debt_service_ratio_max",
                "age": 45,
                "period": "annual",
                "population": "retirewise_kr_households",
            },
        }]}
        available = evaluate_and_route(
            raw_plan,
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=("lookup_reference_value",),
        )
        unavailable = evaluate_and_route(
            raw_plan,
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertTrue(available.allowed)
        self.assertEqual(
            available.plan["tasks"][0]["route"]["executor"],
            "lookup_reference_value",
        )
        self.assertEqual(unavailable.reason_code, "TASK_TYPE_NOT_ALLOWED_FOR_STEP")

    def test_unknown_or_injected_route_is_never_authorized(self) -> None:
        unknown = evaluate_and_route(
            {"status": "executable", "tasks": [{
                "id": "unknown",
                "type": "shell",
                "description": "실행",
                "depends_on": [],
                "bindings": {},
            }]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        injected = evaluate_and_route(
            {"status": "executable", "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": "질문 답변",
                "depends_on": [],
                "bindings": {},
                "route": {"executor": "shell"},
            }]},
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )

        self.assertEqual(unknown.reason_code, "UNSUPPORTED_TASK_TYPE")
        self.assertEqual(injected.reason_code, "UNKNOWN_TASK_FIELDS")

    def test_step_task_menu_is_derived_from_agent_and_tool_registries(self) -> None:
        intro = allowed_task_types_for(
            step_id="intro",
            step_candidates=STEP_CANDIDATES,
        )
        goals = allowed_task_types_for(
            step_id="financial-goals",
            step_candidates=STEP_CANDIDATES,
            registered_tool_capabilities=("lookup_reference_value",),
        )

        self.assertEqual(intro, ("answer_question",))
        self.assertIn("propose_form_change", goals)
        self.assertIn("review_validation_error", goals)
        self.assertIn("lookup_reference_value", goals)

    def test_general_transport_step_routes_questions_to_qa(self) -> None:
        allowed = allowed_task_types_for(step_id="general")

        self.assertIn("answer_question", allowed)
        self.assertNotIn("propose_form_change", allowed)
        self.assertNotIn("review_validation_error", allowed)


if __name__ == "__main__":
    unittest.main()
