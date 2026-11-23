"""F24 semantic requests, runtime-owned wiring and single-result polishing."""

import unittest
from unittest.mock import AsyncMock, patch

from graph.request_plan import compile_request_plan
from guardrails.plan_policy import evaluate_route_plan


class RequestPlanTests(unittest.TestCase):
    def _create_plan(self, planner, message):
        from core.context import ContextWindow, StructuredConversationState
        from graph.planning_service import create_route_plan
        from graph.task_catalog import allowed_task_types_for

        tools = ("lookup_reference_value",)
        return create_route_plan(
            message=message, step_id="financial-goals", planner=planner,
            context_window=ContextWindow(
                turns=(), confirmed_facts={}, structured_state=StructuredConversationState(),
                summary=None, retrieved_memories=(), input_units=0,
            ),
            evaluator=lambda candidate: evaluate_route_plan(candidate, step_id="financial-goals", registered_tool_capabilities=tools),
            allowed_task_types=allowed_task_types_for(step_id="financial-goals", registered_tool_capabilities=tools),
            registered_tool_capabilities=tools,
        )

    def test_planner_reference_fields_are_rejected_without_partial_qa_recovery(self):
        from copy import deepcopy

        request = {"status": "executable", "requests": [
            {"type": "propose_form_change", "description": "가구 평균으로 빈 주거비를 채워줘",
             "reference_id": "household_monthly_expense_housing_avg"},
            {"type": "answer_question", "description": "부채상환 비율 기준도 설명해줘",
             "reference_id": "debt_service_ratio_max", "reference_age": 45},
        ]}
        calls = []

        def planner(**kwargs):
            calls.append(deepcopy(kwargs["context"]))
            return request

        result = self._create_plan(planner, "가구 평균으로 빈 주거비를 채워주고 부채상환 비율 기준도 설명해줘")
        self.assertFalse(result.valid)
        self.assertEqual(result.plan["tasks"], [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.issues[0].code, "INVALID_REQUEST_PLAN")
        self.assertIn("작업 종류와 설명만", calls[1]["validation_feedback"][0]["message"])
        self.assertFalse(any(item.get("recovery") == "single_qa_plan" for item in result.diagnostics))

    def test_compile_failure_then_provider_failure_cannot_recover_to_partial_qa(self):
        calls = []

        def planner(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("fixture provider unavailable")
            return {"status": "executable", "requests": [{
                "type": "propose_form_change", "description": "입력 변경과 설명", "arguments": {},
            }]}

        result = self._create_plan(planner, "주거비를 바꾸고 변경 내용을 설명해줘")
        self.assertFalse(result.valid)
        self.assertEqual(result.plan["tasks"], [])

    def test_compilation_feedback_can_be_repaired_once(self):
        calls = []
        description = "주거비는 유지하고 가구 평균의 의미만 설명해줘"

        def planner(**kwargs):
            calls.append(kwargs)
            request = {"type": "answer_question", "description": description}
            if len(calls) == 1:
                request["depends_on"] = []
            return {"status": "executable", "requests": [request]}

        result = self._create_plan(planner, description)
        self.assertTrue(result.valid)
        self.assertEqual(result.retries_used, 1)
        self.assertEqual(result.plan["tasks"][0]["description"], description)

    def test_description_keeps_conditions_and_tokens_without_raw_message(self):
        description = "식비를 [[FIN_AMOUNT_1]]원으로 변경하고 교통비는 유지, 월 기준으로 설명"
        plan = compile_request_plan({"status": "executable", "requests": [{
            "type": "propose_form_change", "description": description,
        }]})
        self.assertEqual(plan["tasks"][0]["description"], description)
        self.assertEqual(plan["tasks"][0]["bindings"], {})

    def test_semantic_requests_do_not_generate_reference_tasks(self):
        requests = [
            {"type": kind, "description": "가구 평균 식비 설명과 변경 제안"}
            for kind in ("answer_question", "propose_form_change")
        ]
        plan = compile_request_plan({"status": "executable", "requests": requests})
        self.assertEqual(len(plan["tasks"]), 2)
        for task in plan["tasks"]:
            self.assertEqual(task["depends_on"], [])
            self.assertEqual(task["bindings"], {})
        self.assertTrue(evaluate_route_plan(plan, step_id="expenses", registered_tool_capabilities=("lookup_reference_value",)).allowed)
        self.assertTrue(evaluate_route_plan(plan, step_id="expenses", registered_tool_capabilities=()).allowed)

    def test_reference_selection_is_not_part_of_planner_contract(self):
        with self.assertRaises(ValueError):
            compile_request_plan({"status": "executable", "requests": [{
                "type": "answer_question", "description": "부채상환 기준",
                "reference_id": "debt_service_ratio_max",
            }]})

    def test_model_cannot_supply_execution_fields(self):
        for extra in ({"bindings": {}}, {"id": "forged"}, {"depends_on": []}, {"arguments": {}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                compile_request_plan({"status": "executable", "requests": [{
                    "type": "answer_question", "description": "설명", **extra,
                }]})

    def test_request_plan_respects_three_task_budget(self):
        with self.assertRaises(ValueError):
            compile_request_plan({"status": "executable", "requests": [
                {"type": "answer_question", "description": "질문 설명"}
                for _ in range(4)
            ]})


class SingleAnswerPolishTests(unittest.IsolatedAsyncioTestCase):
    async def test_polisher_cannot_replace_approval_instruction_with_completion_claim(self):
        from graph.aggregation import AggregatorModelOutput, compose_aggregate_response
        output = {
            "response": "주거비는 361000원입니다. 내용을 확인한 뒤 적용해 주세요.",
            "step_id": "intro", "action": None,
        }
        rewrite = "주거비는 361000원입니다. 적용을 완료했습니다."
        with patch("graph.aggregation.secure_ainvoke", AsyncMock(return_value=AggregatorModelOutput(response=rewrite))):
            actual = await compose_aggregate_response(output=output, step_id="intro", field_registry=[])
        self.assertEqual(actual, output)

    async def test_polisher_cannot_append_new_storage_claim_while_copying_original(self):
        from graph.aggregation import AggregatorModelOutput, compose_aggregate_response
        output = {
            "response": "변경안은 승인 후 적용됩니다.", "step_id": "intro", "action": None,
        }
        rewrite = output["response"] + " 이미 저장했습니다."
        with patch("graph.aggregation.secure_ainvoke", AsyncMock(return_value=AggregatorModelOutput(response=rewrite))):
            actual = await compose_aggregate_response(output=output, step_id="intro", field_registry=[])
        self.assertEqual(actual, output)

    async def test_polisher_preserves_order_of_approval_and_completion_statements(self):
        from graph.aggregation import AggregatorModelOutput, compose_aggregate_response
        output = {"response": "변경안을 확인해 주세요. 승인 후 적용됩니다.", "step_id": "intro", "action": None}
        rewrite = "승인 후 적용됩니다. 변경안을 확인해 주세요."
        with patch("graph.aggregation.secure_ainvoke", AsyncMock(return_value=AggregatorModelOutput(response=rewrite))):
            actual = await compose_aggregate_response(output=output, step_id="intro", field_registry=[])
        self.assertEqual(actual, output)

    async def test_same_numbers_cannot_change_period_or_storage_status(self):
        from graph.aggregation import AggregatorModelOutput, compose_aggregate_response
        for original, rewrite in (("월 100만 원입니다.", "연 100만 원입니다."), ("저장하지 않았습니다.", "저장했습니다."), ("65세부터 받습니다.", "65세까지 받습니다.")):
            output = {"response": original, "step_id": "intro", "action": None}
            with self.subTest(original=original), patch("graph.aggregation.secure_ainvoke", AsyncMock(return_value=AggregatorModelOutput(response=rewrite))):
                actual = await compose_aggregate_response(output=output, step_id="intro", field_registry=[])
            self.assertEqual(actual, output)

    async def test_single_paragraph_is_polished_without_touching_action(self):
        from graph.aggregation import AggregatorModelOutput, compose_aggregate_response
        output = {"response": "현재 화면을 안내함.", "step_id": "intro", "action": None}
        model = AsyncMock(return_value=AggregatorModelOutput(response="현재 화면을 안내해 드릴게요."))
        with patch("graph.aggregation.secure_ainvoke", model):
            actual = await compose_aggregate_response(output=output, step_id="intro", field_registry=[])
        self.assertEqual(model.await_count, 1)
        self.assertEqual(actual["response"], "현재 화면을 안내해 드릴게요.")
        self.assertIsNone(actual["action"])

    async def test_polisher_cannot_change_verified_numbers(self):
        from graph.aggregation import AggregatorModelOutput, compose_aggregate_response
        output = {"response": "현재 값은 40입니다.", "step_id": "intro", "action": None}
        with patch("graph.aggregation.secure_ainvoke", AsyncMock(return_value=AggregatorModelOutput(response="현재 값은 50입니다."))):
            actual = await compose_aggregate_response(output=output, step_id="intro", field_registry=[])
        self.assertEqual(actual, output)
