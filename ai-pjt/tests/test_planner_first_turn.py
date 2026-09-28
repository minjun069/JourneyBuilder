"""First-turn planner regressions at the provider, parser and compiler boundary."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from core.config import settings
from core.model_contracts import StructuredModelOutputError
from graph.planner import invoke_planner_model, planner_output_schema
from graph.planning_service import planner_route_constraints
from graph.request_plan import compile_request_plan
from graph.task_catalog import allowed_task_types_for
from guardrails.plan_policy import evaluate_route_plan


class PlannerFirstTurnTests(unittest.TestCase):
    def setUp(self):
        self.context = {"route_constraints": planner_route_constraints(
            step_id="intro", allowed_task_types=allowed_task_types_for(step_id="intro"),
        )}

    def invoke(self, provider, message="안녕", *, context=None, provider_name="gemini"):
        with (
            patch.dict("sys.modules", {"core.llm": SimpleNamespace(llm=provider)}),
            patch.object(settings, "llm_provider", provider_name),
            patch.object(settings, "masking_pseudonym_key", "planner-first-turn-test-key-32bytes"),
        ):
            return invoke_planner_model(
                message=message, step_id="intro", context=context or self.context,
            )

    def test_empty_history_requires_json_and_routes_without_repair(self):
        for message in ("안녕", "시작해줘", "노후 준비가 걱정돼요"):
            with self.subTest(message=message):
                provider = Mock()
                provider.with_structured_output.return_value.invoke.return_value = {
                    "status": "executable",
                    "requests": [{"type": "answer_question", "description": message}],
                }
                result = self.invoke(provider, message)
                schema = provider.with_structured_output.call_args.args[0]
                self.assertEqual(provider.with_structured_output.call_args.kwargs, {"method": "json_mode"})
                self.assertEqual(schema.model_json_schema()["required"], ["status", "requests"])
                schema.model_validate(result)
                decision = evaluate_route_plan(compile_request_plan(result), step_id="intro")
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.plan["tasks"][0]["type"], "answer_question")
                runnable = provider.with_structured_output.return_value
                runnable.invoke.assert_called_once()
                prompt = str(runnable.invoke.call_args.args[0])
                self.assertIn(message, prompt)
                self.assertIn("서비스 안내", prompt)
                self.assertNotIn("[관련 대화]", prompt)

    def test_schema_rejects_invented_or_out_of_step_tasks(self):
        schema = planner_output_schema(("answer_question",))
        type_schema = schema.model_json_schema()["$defs"]["PlannerRequest"]["properties"]["type"]
        self.assertEqual(type_schema["enum"], ["answer_question"])
        self.assertNotIn("const", type_schema)
        for task_type in ("start_service", "interpret_chart", "lookup_reference_value"):
            with self.subTest(task_type=task_type), self.assertRaises(ValidationError):
                schema.model_validate({"status": "executable", "requests": [
                    {"type": task_type, "description": "시작 안내"},
                ]})

    def test_schema_keeps_non_executable_and_multiple_request_contract(self):
        schema = planner_output_schema(("answer_question", "propose_form_change"))
        for status in ("ambiguous", "unsupported"):
            self.assertEqual(schema.model_validate({"status": status, "requests": []}).status, status)
        requests = [{"type": kind, "description": "요청"} for kind in (
            "answer_question", "propose_form_change", "answer_question",
        )]
        self.assertEqual(len(schema.model_validate({"status": "executable", "requests": requests}).requests), 3)
        for payload in ({"requests": requests}, {"status": "executable"},
                        {"status": "executable", "requests": []},
                        {"status": "unsupported", "requests": requests},
                        {"status": "executable", "requests": requests * 2}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                schema.model_validate(payload)

    def test_none_result_still_fails_closed(self):
        provider = Mock()
        provider.with_structured_output.return_value.invoke.return_value = None
        with self.assertRaises(StructuredModelOutputError) as error:
            self.invoke(provider)
        self.assertIsInstance(error.exception.__cause__, ValidationError)
        provider.with_structured_output.return_value.invoke.assert_called_once()

    def test_existing_history_is_preserved_and_other_provider_keeps_default_method(self):
        provider = Mock()
        provider.with_structured_output.return_value.invoke.return_value = {"status": "ambiguous", "requests": []}
        context = {**self.context, "turns": [{"user_message": "서비스 소개", "assistant_message": "진행 순서 안내"}]}
        self.invoke(provider, context=context, provider_name="openai")
        self.assertEqual(provider.with_structured_output.call_args.kwargs, {})
        prompt = str(provider.with_structured_output.return_value.invoke.call_args.args[0])
        self.assertIn("[관련 대화]", prompt)
        self.assertIn("진행 순서 안내", prompt)
