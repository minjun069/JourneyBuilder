from __future__ import annotations

import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from core.context import (
    ContextWindow,
    ConversationTurn,
    StructuredConversationState,
)
from graph.plan_validation import validate_plan_structure
from graph.planner import PlannerModelOutput
from graph.planning_service import create_route_plan, planner_route_constraints
from graph.task_catalog import allowed_task_types_for
from guardrails.plan_policy import evaluate_route_plan


def task(
    alias: str,
    task_type: str,
    description: str,
    *,
    depends_on: list[str] | None = None,
    bindings: dict | None = None,
    arguments: dict | None = None,
) -> dict:
    value = {
        "id": alias,
        "type": task_type,
        "description": description,
        "depends_on": depends_on or [],
        "bindings": bindings or {},
    }
    if task_type == "lookup_reference_value":
        value["arguments"] = arguments or {
            "reference_id": "debt_service_ratio_max",
            "age": 45,
            "period": "annual",
            "population": "retirewise_kr_households",
        }
    return value


class FakePlanner:
    def __init__(self, plan: dict) -> None:
        self.plan = plan
        self.calls: list[dict] = []

    def __call__(self, *, message: str, step_id: str, context: dict) -> dict:
        self.calls.append({
            "message": message,
            "step_id": step_id,
            "context": context,
        })
        return self.plan


class RoutePlanValidationTests(unittest.TestCase):
    def test_model_requests_are_compiled_without_model_generated_wiring(self) -> None:
        from graph.request_plan import compile_request_plan
        model_output = PlannerModelOutput.model_validate({
            "requests": [{
                "type": "answer_question",
                "description": "단기 지향의 기간 설명",
            }],
        })

        route_plan = compile_request_plan(model_output.to_request_plan())

        self.assertNotIn("arguments", route_plan["tasks"][0])
        self.assertNotIn("elicit_routing", route_plan)
        self.assertTrue(validate_plan_structure(route_plan, step_id="basic-info").valid)

    def test_qualitative_task_needs_no_special_routing_contract(self) -> None:
        elicit_task = task(
            "elicit",
            "collect_qualitative_info",
            "미래 목표 답변을 이어서 정리",
        )
        missing = validate_plan_structure(
            {"tasks": [elicit_task]},
            step_id="financial-mindset",
        )
        self.assertTrue(missing.valid, [issue.to_dict() for issue in missing.issues])
        self.assertNotIn("elicit_routing", missing.plan)

    def test_writable_household_reference_requires_complete_single_source_binding(self) -> None:
        lookup = task(
            "lookup",
            "lookup_reference_value",
            "가구 주거비 평균 조회",
            arguments={
                "reference_id": "household_monthly_expense_housing_avg",
                "age": 45,
                "period": "monthly",
                "population": "kr_general_households_1plus",
            },
        )
        incomplete = validate_plan_structure({"tasks": [
            lookup,
            task(
                "fill",
                "propose_form_change",
                "기존 빈 주거비 변경안 제안",
                depends_on=["lookup"],
                bindings={"value": {"task_id": "lookup", "path": "value"}},
            ),
        ]}, step_id="financial-goals")
        self.assertFalse(incomplete.valid)
        self.assertIn(
            "INCOMPLETE_REFERENCE_BINDING",
            {issue.code for issue in incomplete.issues},
        )

        fields = (
            "reference_id", "age", "value", "unit", "period", "population",
            "comparator", "source_id", "source_version", "as_of",
            "calculation_policy_version",
        )
        complete = validate_plan_structure({"tasks": [
            lookup,
            task(
                "fill",
                "propose_form_change",
                "기존 빈 주거비 변경안 제안",
                depends_on=["lookup"],
                bindings={
                    field: {"task_id": "lookup", "path": field}
                    for field in fields
                },
            ),
        ]}, step_id="financial-goals")
        self.assertTrue(complete.valid, [issue.to_dict() for issue in complete.issues])

    def test_reference_lookup_requires_complete_typed_arguments(self) -> None:
        raw_task = task(
            "reference",
            "lookup_reference_value",
            "기준값 조회",
        )
        del raw_task["arguments"]["population"]

        validation = validate_plan_structure(
            {"tasks": [raw_task]},
            step_id="basic-info",
        )

        self.assertFalse(validation.valid)
        self.assertIn(
            "INVALID_TOOL_ARGUMENTS",
            {issue.code for issue in validation.issues},
        )

    def test_dependency_and_binding_are_normalized_to_runtime_ids(self) -> None:
        validation = validate_plan_structure({
            "tasks": [
                task(
                    "lookup",
                    "lookup_reference_value",
                    "40대 월평균 비용 조회",
                ),
                task(
                    "fill",
                    "propose_form_change",
                    "조회한 값을 목표 생활비에 제안",
                    depends_on=["lookup"],
                    bindings={
                        "value": {"task_id": "lookup", "path": "value"},
                    },
                ),
            ]
        }, step_id="financial-goals")

        self.assertTrue(validation.valid)
        lookup, fill = validation.plan["tasks"]
        self.assertEqual(lookup["id"], "task-1")
        self.assertEqual(lookup["executor_kind"], "tool")
        self.assertEqual(lookup["produces"], "reference_value")
        self.assertEqual(fill["id"], "task-2")
        self.assertEqual(fill["depends_on"], ["task-1"])
        self.assertEqual(
            fill["bindings"]["value"],
            {"task_id": "task-1", "path": "value"},
        )
        self.assertEqual(fill["produces"], "form_change")

    def test_one_artifact_can_feed_two_explicit_consumers(self) -> None:
        validation = validate_plan_structure({
            "tasks": [
                task("lookup", "lookup_reference_value", "40대 평균 비용 조회"),
                task(
                    "explain",
                    "answer_question",
                    "조회 결과와 출처 설명",
                    depends_on=["lookup"],
                    bindings={
                        "value": {"task_id": "lookup", "path": "value"},
                        "source_id": {
                            "task_id": "lookup",
                            "path": "source_id",
                        },
                    },
                ),
                task(
                    "fill",
                    "propose_form_change",
                    "조회 결과로 목표 생활비 변경 제안",
                    depends_on=["lookup"],
                    bindings={
                        "value": {"task_id": "lookup", "path": "value"},
                    },
                ),
            ]
        }, step_id="financial-goals")
        self.assertTrue(validation.valid)
        self.assertEqual(
            validation.plan["tasks"][1]["depends_on"],
            ["task-1"],
        )
        self.assertEqual(
            validation.plan["tasks"][2]["depends_on"],
            ["task-1"],
        )

    def test_more_than_three_tasks_is_rejected(self) -> None:
        validation = validate_plan_structure({
            "tasks": [
                task(str(index), "answer_question", f"질문 {index}")
                for index in range(4)
            ]
        }, step_id="intro")
        self.assertFalse(validation.valid)
        self.assertEqual(validation.issues[0].code, "INVALID_TASK_COUNT")

    def test_forward_or_cyclic_dependency_is_rejected(self) -> None:
        validation = validate_plan_structure({
            "tasks": [
                task(
                    "first",
                    "answer_question",
                    "첫 요청",
                    depends_on=["second"],
                ),
                task(
                    "second",
                    "answer_question",
                    "두 번째 요청",
                    depends_on=["first"],
                ),
            ]
        }, step_id="intro")
        self.assertFalse(validation.valid)
        self.assertIn(
            "FORWARD_OR_CYCLIC_DEPENDENCY",
            {issue.code for issue in validation.issues},
        )

    def test_binding_requires_declared_dependency_and_matching_path(self) -> None:
        undeclared = validate_plan_structure({
            "tasks": [
                task("lookup", "lookup_reference_value", "평균값 조회"),
                task(
                    "fill",
                    "propose_form_change",
                    "평균값 입력",
                    bindings={
                        "value": {"task_id": "lookup", "path": "value"},
                    },
                ),
            ]
        }, step_id="financial-goals")
        self.assertFalse(undeclared.valid)
        self.assertIn(
            "UNDECLARED_BINDING_DEPENDENCY",
            {issue.code for issue in undeclared.issues},
        )

        wrong_path = validate_plan_structure({
            "tasks": [
                task("lookup", "lookup_reference_value", "평균값 조회"),
                task(
                    "fill",
                    "propose_form_change",
                    "평균값 입력",
                    depends_on=["lookup"],
                    bindings={
                        "value": {"task_id": "lookup", "path": "source_id"},
                    },
                ),
            ]
        }, step_id="financial-goals")
        self.assertFalse(wrong_path.valid)
        self.assertIn(
            "BINDING_PATH_MISMATCH",
            {issue.code for issue in wrong_path.issues},
        )

    def test_planner_cannot_choose_executor_or_produces(self) -> None:
        injected = task("one", "answer_question", "질문")
        injected["agent"] = "input_assist"
        injected["produces"] = "form_change"
        validation = validate_plan_structure({"tasks": [injected]}, step_id="intro")
        self.assertFalse(validation.valid)
        self.assertIn(
            "PLANNER_EXECUTOR_INJECTION",
            {issue.code for issue in validation.issues},
        )


class ContextAwarePlanningTests(unittest.TestCase):
    @staticmethod
    def _empty_window() -> ContextWindow:
        return ContextWindow(
            turns=(),
            confirmed_facts={},
            structured_state=StructuredConversationState(),
            summary=None,
            retrieved_memories=(),
            input_units=0,
        )

    def test_validation_feedback_repairs_plan_once_through_single_entry(self) -> None:
        class RepairingPlanner:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def __call__(self, *, message: str, step_id: str, context: dict) -> dict:
                self.calls.append(context.copy())
                if len(self.calls) == 1:
                    return {"status": "executable", "tasks": []}
                return {
                    "status": "executable",
                    "tasks": [task("answer", "answer_question", message)],
                }

        planner = RepairingPlanner()
        validation = create_route_plan(
            message="안정추구의 뜻을 알려줘",
            step_id="intro",
            context_window=self._empty_window(),
            planner=planner,
            evaluator=lambda candidate: evaluate_route_plan(
                candidate,
                step_id="intro",
            ),
            allowed_task_types=allowed_task_types_for(step_id="intro"),
        )

        self.assertTrue(validation.valid)
        self.assertEqual(validation.retries_used, 1)
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(
            planner.calls[1]["validation_feedback"][0]["code"],
            "INVALID_TASK_COUNT",
        )

    def test_planner_owns_ambiguous_intent_without_virtual_task(self) -> None:
        planner = FakePlanner({"status": "ambiguous", "tasks": []})
        validation = create_route_plan(
            message="그거 좀 해줘",
            step_id="intro",
            context_window=self._empty_window(),
            planner=planner,
            evaluator=lambda candidate: evaluate_route_plan(
                candidate,
                step_id="intro",
            ),
            allowed_task_types=allowed_task_types_for(step_id="intro"),
        )

        self.assertTrue(validation.valid)
        self.assertEqual(validation.plan["tasks"], [])
        self.assertEqual(validation.plan["outcome_reason"], "ambiguous_intent")

    def test_route_constraints_describe_the_deterministic_task_menu(self) -> None:
        allowed_task_types = allowed_task_types_for(
            step_id="basic-info",
            registered_tool_capabilities=("lookup_reference_value",),
        )

        constraints = planner_route_constraints(
            step_id="basic-info",
            allowed_task_types=allowed_task_types,
            registered_tool_capabilities=("lookup_reference_value",),
        )

        self.assertEqual(
            constraints["allowed_task_types"],
            sorted(allowed_task_types),
        )
        self.assertEqual(
            constraints["available_tool_capabilities"],
            ["lookup_reference_value"],
        )
        self.assertNotIn(
            "lookup_reference_value",
            constraints["task_type_guidance"],
        )
        self.assertNotIn("reference_options", constraints)
        self.assertNotIn("elicit_enabled", constraints)

    def test_planner_receives_only_bounded_context_and_every_task_is_routed(self) -> None:
        planner = FakePlanner({
            "tasks": [
                task("lookup", "lookup_reference_value", "40대 평균 비용 조회"),
                task(
                    "fill",
                    "propose_form_change",
                    "그 금액으로 목표 생활비 변경 제안",
                    depends_on=["lookup"],
                    bindings={
                        "value": {"task_id": "lookup", "path": "value"},
                    },
                ),
            ]
        })
        window = ContextWindow(
            turns=(ConversationTurn(
                turn_id="turn-1",
                user_message="저는 40대입니다.",
                assistant_message="연령대를 확인했습니다.",
            ),),
            confirmed_facts={"profile.age_band": "40대"},
            structured_state=StructuredConversationState(),
            summary=None,
            retrieved_memories=(),
            input_units=20,
        )
        validation = create_route_plan(
            message="평균 비용을 찾고 그 값으로 목표치를 채워줘.",
            step_id="financial-goals",
            context_window=window,
            planner=planner,
            evaluator=lambda candidate: evaluate_route_plan(
                candidate,
                step_id="financial-goals",
                registered_tool_capabilities=("lookup_reference_value",),
            ),
            allowed_task_types=allowed_task_types_for(
                step_id="financial-goals",
                registered_tool_capabilities=("lookup_reference_value",),
            ),
            registered_tool_capabilities=("lookup_reference_value",),
        )

        self.assertTrue(validation.valid)
        self.assertEqual(
            planner.calls[0]["context"]["confirmed_facts"],
            {"profile.age_band": "40대"},
        )
        self.assertNotIn("elicit_context_index", planner.calls[0]["context"])
        self.assertIn(
            "lookup_reference_value",
            planner.calls[0]["context"]["route_constraints"]["allowed_task_types"],
        )
        lookup, fill = validation.plan["tasks"]
        self.assertEqual(lookup["route"]["reason_code"], "capability_match")
        self.assertEqual(fill["route"]["executor"], "input_assist")
        self.assertEqual(fill["route"]["reason_code"], "intent_match")

    def test_plan_using_task_outside_current_step_menu_is_rejected(self) -> None:
        planner = FakePlanner({
            "tasks": [task("chart", "interpret_chart", "차트 설명")],
        })
        window = ContextWindow(
            turns=(),
            confirmed_facts={},
            structured_state=StructuredConversationState(),
            summary=None,
            retrieved_memories=(),
            input_units=0,
        )

        validation = create_route_plan(
            message="이 차트를 설명해줘",
            step_id="basic-info",
            context_window=window,
            planner=planner,
            evaluator=lambda candidate: evaluate_route_plan(
                candidate,
                step_id="basic-info",
            ),
            allowed_task_types=allowed_task_types_for(step_id="basic-info"),
        )

        self.assertFalse(validation.valid)
        self.assertEqual(
            validation.issues[0].code,
            "TASK_TYPE_NOT_ALLOWED_FOR_STEP",
        )

    def test_planner_failures_do_not_bypass_planning_with_raw_questions(self) -> None:
        with self.assertRaises(ValidationError) as invalid_schema:
            PlannerModelOutput.model_validate({"requests": [{"type": "answer_question"}]})
        window = ContextWindow(
            turns=(),
            confirmed_facts={},
            structured_state=StructuredConversationState(),
            summary=None,
            retrieved_memories=(),
            input_units=0,
        )
        for message, error in (
            ("단기 지향이면 어느정도 기간을 말하지?", RuntimeError("simulated planner provider timeout")),
            ("주거비를 바꾸고 안정추구의 뜻도 설명해줘", invalid_schema.exception),
        ):
            with self.subTest(message=message):
                unavailable_planner = Mock(side_effect=error)
                evaluator = Mock()
                validation = create_route_plan(
                    message=message,
                    step_id="basic-info",
                    context_window=window,
                    planner=unavailable_planner,
                    evaluator=evaluator,
                    allowed_task_types=allowed_task_types_for(
                        step_id="basic-info",
                    ),
                )

                self.assertFalse(validation.valid)
                self.assertEqual(validation.plan["tasks"], [])
                self.assertEqual(validation.issues[0].code, "PLANNER_ERROR")
                self.assertEqual(validation.retries_used, 1)
                self.assertEqual(unavailable_planner.call_count, 2)
                evaluator.assert_not_called()
                self.assertEqual(
                    {item["component"] for item in validation.diagnostics},
                    {"planner"},
                )
                self.assertTrue(all("recovery" not in item for item in validation.diagnostics))


if __name__ == "__main__":
    unittest.main()
