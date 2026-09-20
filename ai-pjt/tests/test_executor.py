from __future__ import annotations

import unittest
from unittest.mock import patch

from graph.executor import (
    aggregate_task_results,
    compose_aggregate_response,
    execute_route_plan,
    validate_agent_executor_registry,
)
from tests.instrumentation import bound_fake_instrumentation


FIELD_SPEC = {
    "step_id": "basic-info",
    "section": "basic_info",
    "path": "monthly_income",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0, "period": "month"},
    "cardinality": "single",
    "writable_by_ai": True,
    "nullable": False,
    "operations": ["set", "update"],
}


def reference_task() -> dict:
    return {
        "id": "task-1",
        "type": "lookup_reference_value",
        "description": "40대 월평균 비용 조회",
        "depends_on": [],
        "bindings": {},
        "executor_kind": "tool",
        "produces": "reference_value",
        "route": {
            "task_id": "task-1",
            "executor_kind": "tool",
            "executor": "lookup_reference_value",
            "reason_code": "capability_match",
        },
    }


def consumer_task(task_id: str, task_type: str, executor: str) -> dict:
    return {
        "id": task_id,
        "type": task_type,
        "description": "조회 값을 사용",
        "depends_on": ["task-1"],
        "bindings": {
            "value": {"task_id": "task-1", "path": "value"},
            "unit": {"task_id": "task-1", "path": "unit"},
        },
        "executor_kind": "agent",
        "produces": "form_change" if task_type == "propose_form_change" else "text_response",
        "route": {
            "task_id": task_id,
            "executor_kind": "agent",
            "executor": executor,
            "reason_code": "intent_match",
        },
    }


def reference_result() -> dict:
    return {
        "task_id": "task-1",
        "status": "completed",
        "artifact": {
            "type": "reference_value",
            "schema_version": "1.0",
            "producer_task_id": "task-1",
            "reference_id": "monthly_income",
            "age": 45,
            "value": 2_500_000,
            "unit": "KRW",
            "period": "monthly",
            "population": "40대",
            "comparator": "eq",
            "source_id": "approved-statistics-2026",
            "source_version": "1.0.0",
            "as_of": "2026-06-30",
            "calculation_policy_version": "2026-09-07.v1",
        },
    }


def form_result(task_id: str, value: int = 2_500_000) -> dict:
    return {
        "task_id": task_id,
        "status": "completed",
        "response": "목표 생활비 변경안을 만들었습니다.",
        "artifact": {
            "type": "form_change",
            "target": "draft_form",
            "user_requested": True,
            "confidence": 1.0,
            "changes": [{
                "section": "basic_info",
                "path": "monthly_income",
                "operation": "set",
                "value": value,
                "unit": "KRW",
                "reason": "승인된 조회 결과",
            }],
        },
    }


class SequentialExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_artifact_is_bound_to_two_consumers(self) -> None:
        calls: list[tuple[str, dict]] = []

        async def lookup(payload: dict) -> dict:
            calls.append(("lookup", payload))
            return reference_result()

        async def qa(payload: dict) -> dict:
            calls.append(("qa", payload))
            self.assertEqual(payload["task_input"]["bindings"], {
                "value": 2_500_000,
                "unit": "KRW",
            })
            return {
                "task_id": "task-2",
                "status": "completed",
                "response": "승인 통계 기준 월 250만 원입니다.",
                "artifact": {
                    "type": "text_response",
                    "text": "승인 통계 기준 월 250만 원입니다.",
                },
            }

        async def input_assist(payload: dict) -> dict:
            calls.append(("input_assist", payload))
            self.assertEqual(payload["task_input"]["bindings"]["value"], 2_500_000)
            return form_result("task-3")

        tasks = [
            reference_task(),
            consumer_task("task-2", "answer_question", "qa"),
            consumer_task("task-3", "propose_form_change", "input_assist"),
        ]
        report = await execute_route_plan(
            plan={"tasks": tasks},
            request_id="request-test",
            step_id="basic-info",
            customer_info={},
            agent_executors={"qa": qa, "input_assist": input_assist},
            tool_executors={"lookup_reference_value": lookup},
            instrumentation=bound_fake_instrumentation()[0],
        )

        self.assertEqual([name for name, _ in calls], ["lookup", "qa", "input_assist"])
        self.assertEqual(report.task_results["task-3"]["status"], "completed")
        self.assertEqual(report.output["action"]["changes"][0]["value"], 2_500_000)
        self.assertIn("2,500,000 KRW", report.output["response"])

    async def test_failed_dependency_blocks_consumer_but_independent_task_runs(self) -> None:
        dependent_calls = 0
        independent_calls = 0

        async def failing_lookup(_: dict) -> dict:
            return {
                "task_id": "task-1",
                "status": "failed",
                "reason_code": "provider_timeout",
            }

        async def dependent(_: dict) -> dict:
            nonlocal dependent_calls
            dependent_calls += 1
            return form_result("task-2")

        async def independent(_: dict) -> dict:
            nonlocal independent_calls
            independent_calls += 1
            return {
                "task_id": "task-3",
                "status": "completed",
                "artifact": {"type": "text_response", "text": "독립 응답"},
            }

        independent_task = {
            "id": "task-3",
            "type": "answer_question",
            "description": "독립 질문",
            "depends_on": [],
            "bindings": {},
            "executor_kind": "agent",
            "produces": "text_response",
            "route": {
                "task_id": "task-3",
                "executor_kind": "agent",
                "executor": "qa",
                "reason_code": "intent_match",
            },
        }
        report = await execute_route_plan(
            plan={"tasks": [
                reference_task(),
                consumer_task("task-2", "propose_form_change", "input_assist"),
                independent_task,
            ]},
            request_id="request-test",
            step_id="basic-info",
            customer_info={},
            agent_executors={"input_assist": dependent, "qa": independent},
            tool_executors={"lookup_reference_value": failing_lookup},
            instrumentation=bound_fake_instrumentation()[0],
        )

        self.assertEqual(dependent_calls, 0)
        self.assertEqual(independent_calls, 1)
        self.assertEqual(report.task_results["task-2"]["status"], "blocked")
        self.assertEqual(report.task_results["task-3"]["status"], "completed")

    async def test_form_change_must_use_exact_bound_value(self) -> None:
        async def lookup(_: dict) -> dict:
            return reference_result()

        async def input_assist(_: dict) -> dict:
            return form_result("task-2", value=3_000_000)

        report = await execute_route_plan(
            plan={"tasks": [
                reference_task(),
                consumer_task("task-2", "propose_form_change", "input_assist"),
            ]},
            request_id="request-test",
            step_id="basic-info",
            customer_info={},
            agent_executors={"input_assist": input_assist},
            tool_executors={"lookup_reference_value": lookup},
            instrumentation=bound_fake_instrumentation()[0],
        )
        self.assertEqual(
            report.task_results["task-2"]["reason_code"],
            "binding_not_consumed",
        )
        self.assertIsNone(report.output["action"])

    async def test_unregistered_lookup_fails_and_blocks_without_calls(self) -> None:
        lookup = reference_task()
        lookup["route"]["reason_code"] = "capability_unavailable"
        calls = 0

        async def dependent(_: dict) -> dict:
            nonlocal calls
            calls += 1
            return form_result("task-2")

        report = await execute_route_plan(
            plan={"tasks": [
                lookup,
                consumer_task("task-2", "propose_form_change", "input_assist"),
            ]},
            request_id="request-test",
            step_id="basic-info",
            customer_info={},
            agent_executors={"input_assist": dependent},
            tool_executors={},
            instrumentation=bound_fake_instrumentation()[0],
        )
        self.assertEqual(calls, 0)
        self.assertEqual(
            report.task_results["task-1"]["reason_code"],
            "capability_unavailable",
        )
        self.assertEqual(report.task_results["task-2"]["status"], "blocked")
        self.assertIsNone(report.output["action"])

    async def test_task_budget_stops_execution_after_limit(self) -> None:
        calls: list[str] = []

        async def qa(payload: dict) -> dict:
            task_id = payload["task"]["id"]
            calls.append(task_id)
            return {
                "task_id": task_id,
                "status": "completed",
                "artifact": {"type": "text_response", "text": task_id},
            }

        tasks = [
            {
                "id": f"task-{index}",
                "type": "answer_question",
                "description": f"독립 질문 {index}",
                "depends_on": [],
                "bindings": {},
                "executor_kind": "agent",
                "produces": "text_response",
                "route": {
                    "task_id": f"task-{index}",
                    "executor_kind": "agent",
                    "executor": "qa",
                    "reason_code": "intent_match",
                },
            }
            for index in range(1, 5)
        ]
        report = await execute_route_plan(
            plan={"tasks": tasks},
            request_id="request-test",
            step_id="basic-info",
            customer_info={},
            agent_executors={"qa": qa},
            tool_executors={},
            instrumentation=bound_fake_instrumentation()[0],
        )

        self.assertEqual(calls, ["task-1", "task-2", "task-3"])
        self.assertEqual(report.tasks_used, 3)
        self.assertEqual(report.task_results["task-4"]["status"], "failed")
        self.assertEqual(
            report.task_results["task-4"]["reason_code"],
            "task_budget_exhausted",
        )

    async def test_all_failed_is_closed_by_aggregator_without_virtual_task(self) -> None:
        async def invalid_qa(_: dict) -> dict:
            return {}

        task = consumer_task("task-1", "answer_question", "qa")
        task["depends_on"] = []
        task["bindings"] = {}
        report = await execute_route_plan(
            plan={"tasks": [task]},
            request_id="request-test",
            step_id="basic-info",
            customer_info={"sensitive": "must-not-leave-executor"},
            agent_executors={"qa": invalid_qa},
            tool_executors={},
            instrumentation=bound_fake_instrumentation()[0],
        )

        self.assertIn("안전성 검증", report.output["response"])
        self.assertIn("다시 시도", report.output["response"])

    async def test_partial_success_keeps_verified_result_without_failure_responder(self) -> None:
        async def failing(_: dict) -> dict:
            raise RuntimeError("expected failure")

        async def succeeding(_: dict) -> dict:
            return {
                "task_id": "task-2",
                "status": "completed",
                "artifact": {"type": "text_response", "text": "검증된 독립 결과"},
            }

        failed_task = consumer_task("task-1", "answer_question", "input_assist")
        failed_task["depends_on"] = []
        failed_task["bindings"] = {}
        successful_task = consumer_task("task-2", "answer_question", "qa")
        successful_task["depends_on"] = []
        successful_task["bindings"] = {}
        report = await execute_route_plan(
            plan={"tasks": [failed_task, successful_task]},
            request_id="request-test",
            step_id="basic-info",
            customer_info={},
            agent_executors={
                "input_assist": failing,
                "qa": succeeding,
            },
            tool_executors={},
            instrumentation=bound_fake_instrumentation()[0],
        )

        self.assertIn("검증된 독립 결과", report.output["response"])


class AggregationTests(unittest.TestCase):
    def test_conflicting_changes_are_kept_as_public_options(self) -> None:
        task_1 = {
            "id": "task-1",
            "depends_on": [],
            "produces": "form_change",
        }
        task_2 = {
            "id": "task-2",
            "depends_on": [],
            "produces": "form_change",
        }
        output = aggregate_task_results(
            tasks=[task_1, task_2],
            task_results={
                "task-1": form_result("task-1", 2_500_000),
                "task-2": form_result("task-2", 3_000_000),
            },
            step_id="basic-info",
            field_registry=[FIELD_SPEC],
        )
        self.assertEqual(
            [change["value"] for change in output["action"]["changes"]],
            [2_500_000, 3_000_000],
        )
        self.assertIn("선택지로 각각", output["response"])

    def test_equivalent_changes_are_merged_into_one_option(self) -> None:
        task_1 = {
            "id": "task-1",
            "depends_on": [],
            "produces": "form_change",
        }
        task_2 = {
            "id": "task-2",
            "depends_on": [],
            "produces": "form_change",
        }
        duplicate = form_result("task-2")
        duplicate["artifact"]["changes"][0]["reason"] = "표현만 다른 동일 제안"

        output = aggregate_task_results(
            tasks=[task_1, task_2],
            task_results={
                "task-1": form_result("task-1"),
                "task-2": duplicate,
            },
            step_id="basic-info",
            field_registry=[FIELD_SPEC],
        )

        self.assertEqual(len(output["action"]["changes"]), 1)
        self.assertNotIn("선택지로 각각", output["response"])

    def test_agent_registry_requires_all_five_roles(self) -> None:
        async def executor(_: dict) -> dict:
            return {}

        full = {
            name: executor
            for name in (
                "qa",
                "input_assist",
                "error_review",
                "chart_interpret",
                "elicit",
            )
        }
        validate_agent_executor_registry(full)
        del full["qa"]
        with self.assertRaises(ValueError):
            validate_agent_executor_registry(full)


class AggregationComposerTests(unittest.IsolatedAsyncioTestCase):
    async def test_composer_naturalizes_verified_blocks(self) -> None:
        class Runnable:
            async def ainvoke(self, _messages: list) -> dict:
                return {
                    "response": "첫 번째 결과와 두 번째 결과를 함께 안내합니다.",
                    "source_ids": ["result-1", "result-2"],
                }

        class Provider:
            def with_structured_output(self, _schema: object) -> Runnable:
                return Runnable()

        output = {
            "response": "첫 번째 결과\n\n두 번째 결과",
            "step_id": "intro",
            "action": None,
        }
        with patch("core.llm.llm", Provider()):
            composed = await compose_aggregate_response(
                output=output,
                step_id="intro",
                field_registry=[],
            )

        self.assertEqual(
            composed["response"],
            "첫 번째 결과와 두 번째 결과를 함께 안내합니다.",
        )

    async def test_composer_new_number_falls_back_to_deterministic_join(self) -> None:
        class Runnable:
            async def ainvoke(self, _messages: list) -> dict:
                return {
                    "response": "검증되지 않은 999원을 추가합니다.",
                    "source_ids": ["result-1", "result-2"],
                }

        class Provider:
            def with_structured_output(self, _schema: object) -> Runnable:
                return Runnable()

        output = {
            "response": "100원\n\n200원",
            "step_id": "intro",
            "action": None,
        }
        with patch("core.llm.llm", Provider()):
            composed = await compose_aggregate_response(
                output=output,
                step_id="intro",
                field_registry=[],
            )

        self.assertEqual(composed, output)

    async def test_composer_omitted_number_falls_back_to_deterministic_join(self) -> None:
        class Runnable:
            async def ainvoke(self, _messages: list) -> dict:
                return {
                    "response": "100원 결과만 안내합니다.",
                    "source_ids": ["result-1", "result-2"],
                }

        class Provider:
            def with_structured_output(self, _schema: object) -> Runnable:
                return Runnable()

        output = {
            "response": "100원\n\n200원",
            "step_id": "intro",
            "action": None,
        }
        with patch("core.llm.llm", Provider()):
            composed = await compose_aggregate_response(
                output=output,
                step_id="intro",
                field_registry=[],
            )

        self.assertEqual(composed, output)


if __name__ == "__main__":
    unittest.main()
