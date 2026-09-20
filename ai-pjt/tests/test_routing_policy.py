from __future__ import annotations

import unittest

from graph.router import allowed_task_types_for, enrich_route_plan, select_route


STEP_CANDIDATES = {
    "intro": ("qa",),
    "financial-goals": ("qa", "input_assist", "error_review"),
}


class DeterministicRoutingPolicyTests(unittest.TestCase):
    def test_agent_task_type_has_one_deterministic_route(self) -> None:
        decision = select_route(task={
            "id": "answer",
            "type": "answer_question",
            "description": "안정추구의 뜻을 설명",
        })

        self.assertEqual(decision, {
            "source_task_id": "answer",
            "executor_kind": "agent",
            "executor": "qa",
            "reason_code": "intent_match",
        })

    def test_tool_route_reports_runtime_capability_availability(self) -> None:
        task = {"id": "lookup", "type": "lookup_reference_value"}

        available = select_route(
            task=task,
            registered_tool_capabilities=("lookup_reference_value",),
        )
        unavailable = select_route(task=task)

        self.assertEqual(available["executor"], "lookup_reference_value")
        self.assertEqual(available["reason_code"], "capability_match")
        self.assertEqual(unavailable["reason_code"], "capability_unavailable")

    def test_unknown_task_type_is_enriched_but_never_authorized(self) -> None:
        decision = select_route(task={"id": "unknown", "type": "shell"})

        self.assertEqual(decision["executor"], "unresolved")
        self.assertEqual(decision["reason_code"], "plan_invalid")

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
        self.assertNotIn("interpret_chart", goals)

    def test_general_transport_step_routes_questions_to_qa(self) -> None:
        allowed = allowed_task_types_for(step_id="general")

        self.assertIn("answer_question", allowed)
        self.assertNotIn("propose_form_change", allowed)
        self.assertNotIn("review_validation_error", allowed)

    def test_enrichment_preserves_raw_planner_output(self) -> None:
        raw_plan = {
            "status": "executable",
            "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": "질문 답변",
                "depends_on": [],
                "bindings": {},
            }],
        }

        candidate = enrich_route_plan(
            raw_plan=raw_plan,
            step_id="intro",
        )

        self.assertEqual(candidate["planner_plan"], raw_plan)
        self.assertEqual(candidate["route_enrichments"][0]["executor"], "qa")
        self.assertNotIn("route", candidate["planner_plan"]["tasks"][0])


if __name__ == "__main__":
    unittest.main()
