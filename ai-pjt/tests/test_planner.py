from __future__ import annotations

import unittest

from core.context import ContextWindow, ConversationTurn
from graph.planner import (
    PlannerModelOutput,
    create_route_plan,
    validate_route_plan,
)


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


class FakeClassifier:
    AGENTS = {
        "answer_question": "qa",
        "propose_form_change": "input_assist",
        "review_validation_error": "error_review",
        "interpret_chart": "chart_interpret",
        "collect_qualitative_info": "elicit",
        "fallback": "fallback",
    }

    def __call__(self, *, task: dict, candidates: tuple[str, ...]) -> dict:
        return {"status": "matched", "agent": self.AGENTS[task["type"]]}


class RoutePlanValidationTests(unittest.TestCase):
    def test_provider_empty_arguments_are_removed_before_plan_validation(self) -> None:
        model_output = PlannerModelOutput.model_validate({
            "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": "단기 지향의 기간 설명",
                "depends_on": [],
                "bindings": {},
                "arguments": {},
            }],
        })

        route_plan = model_output.to_route_plan()

        self.assertNotIn("arguments", route_plan["tasks"][0])
        self.assertEqual(route_plan["elicit_routing"]["is_related"], False)
        self.assertTrue(validate_route_plan(route_plan, step_id="basic-info").valid)

    def test_elicit_task_requires_structured_field_routing(self) -> None:
        elicit_task = task(
            "elicit",
            "collect_qualitative_info",
            "미래 목표 답변을 이어서 정리",
        )
        missing = validate_route_plan(
            {"tasks": [elicit_task]},
            step_id="financial-mindset",
        )
        self.assertFalse(missing.valid)
        self.assertIn(
            "ELICIT_ROUTING_REQUIRED",
            {issue.code for issue in missing.issues},
        )

        routed = validate_route_plan({
            "elicit_routing": {
                "is_related": True,
                "field": "future_goals",
                "turn_kind": "correction",
                "confidence_sufficient": True,
            },
            "tasks": [elicit_task],
        }, step_id="financial-mindset")
        self.assertTrue(routed.valid, [issue.to_dict() for issue in routed.issues])
        self.assertEqual(
            routed.plan["elicit_routing"]["field"],
            "future_goals",
        )

    def test_low_confidence_elicit_judgment_cannot_execute_elicit(self) -> None:
        routing = {
            "is_related": True,
            "field": None,
            "turn_kind": None,
            "confidence_sufficient": False,
        }
        routed = validate_route_plan({
            "elicit_routing": routing,
            "tasks": [task("clarify", "fallback", "대상 항목 확인")],
        }, step_id="financial-mindset")
        self.assertTrue(routed.valid, [issue.to_dict() for issue in routed.issues])
        blocked = validate_route_plan({
            "elicit_routing": routing,
            "tasks": [task(
                "elicit",
                "collect_qualitative_info",
                "불확실한 대상의 정성정보 수집",
            )],
        }, step_id="financial-mindset")
        self.assertFalse(blocked.valid)
        self.assertIn(
            "ELICIT_ROUTING_REQUIRED",
            {issue.code for issue in blocked.issues},
        )

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
        incomplete = validate_route_plan({"tasks": [
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
        complete = validate_route_plan({"tasks": [
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

    def test_retirement_gap_arguments_are_runtime_fixed(self) -> None:
        raw_task = task(
            "gap",
            "lookup_retirement_gap",
            "최신 보고서의 은퇴 부족액 조회",
        )
        validation = validate_route_plan(
            {"tasks": [raw_task]},
            step_id="basic-info",
        )

        self.assertTrue(validation.valid)
        self.assertEqual(
            validation.plan["tasks"][0]["arguments"],
            {"metric_id": "retirement_gap"},
        )

        raw_task["arguments"] = {"metric_id": "invented_metric"}
        rejected = validate_route_plan(
            {"tasks": [raw_task]},
            step_id="basic-info",
        )
        self.assertFalse(rejected.valid)
        self.assertIn(
            "PLANNER_FIXED_ARGUMENT_INJECTION",
            {issue.code for issue in rejected.issues},
        )

    def test_reference_lookup_requires_complete_typed_arguments(self) -> None:
        raw_task = task(
            "reference",
            "lookup_reference_value",
            "기준값 조회",
        )
        del raw_task["arguments"]["population"]

        validation = validate_route_plan(
            {"tasks": [raw_task]},
            step_id="basic-info",
        )

        self.assertFalse(validation.valid)
        self.assertIn(
            "INVALID_TOOL_ARGUMENTS",
            {issue.code for issue in validation.issues},
        )

    def test_dependency_and_binding_are_normalized_to_runtime_ids(self) -> None:
        validation = validate_route_plan({
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
        validation = validate_route_plan({
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
        validation = validate_route_plan({
            "tasks": [
                task(str(index), "answer_question", f"질문 {index}")
                for index in range(4)
            ]
        }, step_id="intro")
        self.assertFalse(validation.valid)
        self.assertEqual(validation.issues[0].code, "INVALID_TASK_COUNT")

    def test_forward_or_cyclic_dependency_is_rejected(self) -> None:
        validation = validate_route_plan({
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
        undeclared = validate_route_plan({
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

        wrong_path = validate_route_plan({
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
        validation = validate_route_plan({"tasks": [injected]}, step_id="intro")
        self.assertFalse(validation.valid)
        self.assertIn(
            "PLANNER_EXECUTOR_INJECTION",
            {issue.code for issue in validation.issues},
        )


class ContextAwarePlanningTests(unittest.TestCase):
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
            summary=None,
            input_units=20,
        )
        validation = create_route_plan(
            message="평균 비용을 찾고 그 값으로 목표치를 채워줘.",
            step_id="financial-goals",
            context_window=window,
            planner=planner,
            classifier=FakeClassifier(),
            elicit_context_index={
                "schema_version": "2.0",
                "fields": [{"field": "future_goals", "status": "open"}],
            },
        )

        self.assertTrue(validation.valid)
        self.assertEqual(
            planner.calls[0]["context"]["confirmed_facts"],
            {"profile.age_band": "40대"},
        )
        self.assertEqual(
            planner.calls[0]["context"]["elicit_context_index"]["fields"][0],
            {"field": "future_goals", "status": "open"},
        )
        lookup, fill = validation.plan["tasks"]
        self.assertEqual(lookup["route"]["reason_code"], "capability_unavailable")
        self.assertEqual(fill["route"]["executor"], "input_assist")
        self.assertEqual(fill["route"]["reason_code"], "intent_match")

    def test_explanation_questions_recover_to_single_qa_plan(self) -> None:
        def unavailable_planner(**_kwargs: object) -> dict:
            raise RuntimeError("simulated planner provider timeout")

        def unavailable_classifier(**_kwargs: object) -> dict:
            raise RuntimeError("simulated router provider timeout")

        window = ContextWindow(
            turns=(),
            confirmed_facts={},
            summary=None,
            input_units=0,
        )
        for message in (
            "단기 지향이면 어느정도 기간을 말하지?",
            "안정추구란 정확히 뭘 말하는거야?",
        ):
            with self.subTest(message=message):
                validation = create_route_plan(
                    message=message,
                    step_id="basic-info",
                    context_window=window,
                    planner=unavailable_planner,
                    classifier=unavailable_classifier,
                )

                self.assertTrue(validation.valid)
                self.assertEqual(len(validation.plan["tasks"]), 1)
                task_result = validation.plan["tasks"][0]
                self.assertEqual(task_result["type"], "answer_question")
                self.assertEqual(task_result["route"]["executor"], "qa")
                self.assertEqual(
                    task_result["route"]["reason_code"],
                    "intent_match",
                )
                self.assertEqual(
                    {item["component"] for item in validation.diagnostics},
                    {"planner", "router_classifier"},
                )


if __name__ == "__main__":
    unittest.main()
