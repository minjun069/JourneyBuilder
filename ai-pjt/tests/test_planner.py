from __future__ import annotations

import unittest

from core.context import ContextWindow, ConversationTurn
from graph.planner import create_route_plan, validate_route_plan


def task(
    alias: str,
    task_type: str,
    description: str,
    *,
    depends_on: list[str] | None = None,
    bindings: dict | None = None,
) -> dict:
    return {
        "id": alias,
        "type": task_type,
        "description": description,
        "depends_on": depends_on or [],
        "bindings": bindings or {},
    }


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
                confirmed_facts={"profile.age_band": "40대"},
            ),),
            confirmed_facts={"profile.age_band": "40대"},
            summary=None,
            structured_facts=(),
            input_units=20,
        )
        validation = create_route_plan(
            message="평균 비용을 찾고 그 값으로 목표치를 채워줘.",
            step_id="financial-goals",
            context_window=window,
            planner=planner,
            classifier=FakeClassifier(),
        )

        self.assertTrue(validation.valid)
        self.assertEqual(
            planner.calls[0]["context"]["confirmed_facts"],
            {"profile.age_band": "40대"},
        )
        lookup, fill = validation.plan["tasks"]
        self.assertEqual(lookup["route"]["reason_code"], "capability_unavailable")
        self.assertEqual(fill["route"]["executor"], "input_assist")
        self.assertEqual(fill["route"]["reason_code"], "intent_match")


if __name__ == "__main__":
    unittest.main()
