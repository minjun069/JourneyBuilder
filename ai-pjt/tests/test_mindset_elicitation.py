from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from core.mindset_elicitation import (
    ElicitationResult, ElicitationState, FIRST_QUESTIONS, MINDSET_FIELDS,
)
from tests.test_agent_features import configure_runtime
from tests.test_transport_contract import make_request


ROOT = Path(__file__).resolve().parents[2]


def request_state(*, event="answer", field="values", count=0, form=None):
    return {
        "request_id": "mindset-test", "thread_id": "mindset-thread",
        "step_id": "financial-mindset", "request_mode": "mindset_elicitation",
        "message": "가족과 예측 가능한 생활을 지키고 싶어요.",
        "customer_info": {"saved_data": {"values": "과거 저장된 가치관"}, "current_form": form or {}},
        "elicitation_state": {"event": event, "field": field, "answer_count": count,
                              "completed_fields": list(MINDSET_FIELDS[:MINDSET_FIELDS.index(field)])},
    }


class MindsetContractTests(unittest.TestCase):
    def test_shared_fixture_matches_request_and_result_validators(self):
        fixture = json.loads((ROOT / "contract-fixtures/ai-lifecycle/v2/mindset-elicitation.json").read_text())
        self.assertEqual(list(MINDSET_FIELDS), fixture["fields"])
        for key, model, payload_key in (
            ("request_cases", ElicitationState, "state"),
            ("result_cases", ElicitationResult, "result"),
        ):
            for case in fixture[key]:
                with self.subTest(case=case["id"]):
                    if case["valid"]:
                        model.model_validate(case[payload_key])
                    else:
                        with self.assertRaises(ValidationError):
                            model.model_validate(case[payload_key])

    def test_request_requires_matching_mode_step_and_state(self):
        state = request_state()["elicitation_state"]
        for changes in (
            {"request_mode": "mindset_elicitation"},
            {"request_mode": "mindset_elicitation", "elicitation_state": state},
            {"step_id": "financial-mindset", "elicitation_state": state},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                make_request(**changes)
        accepted = make_request(step_id="financial-mindset", request_mode="mindset_elicitation", elicitation_state=state)
        self.assertEqual(accepted.elicitation_state.field, "values")

    def test_request_preserves_long_user_edited_summary_and_rejects_wrong_type(self):
        state = request_state()["elicitation_state"]
        text = "직접 편집한 내용. " * 600
        request = make_request(step_id="financial-mindset", request_mode="mindset_elicitation",
                               elicitation_state=state, customer_info={"saved_data": {}, "current_form": {"values": text}})
        self.assertEqual(request.customer_info.current_form["values"], text)
        with self.assertRaises(ValidationError):
            make_request(step_id="financial-mindset", request_mode="mindset_elicitation",
                         elicitation_state=state, customer_info={"saved_data": {}, "current_form": {"values": 42}})


class MindsetServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure_runtime()
        from agents.elicit import service
        from agents.elicit.model import MindsetModelOutput
        self.service = service
        self.result_type = MindsetModelOutput

    def answer(self, *, response="안정을 중시하시는군요. 그 생각을 갖게 된 경험이 있으신가요?",
               summary="예측 가능한 생활을 중시함.", sufficient=False, kind="answer"):
        return self.result_type(response=response, summary=summary, sufficient=sufficient, turn_kind=kind)

    async def test_all_first_questions_are_deterministic_without_model(self):
        with patch.object(self.service, "collect_answer", new_callable=AsyncMock) as model:
            for field in MINDSET_FIELDS:
                output = (await self.service._run(request_state(event="start", field=field)))["output"]
                self.assertEqual(output["response"], FIRST_QUESTIONS[field])
                self.assertEqual(output["elicitation"]["answer_count"], 0)
                self.assertEqual(output["elicitation"]["summary"], "")
            model.assert_not_awaited()


class MindsetModelBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure_runtime()
        from agents.elicit import model
        self.model = model

    async def test_one_model_call_preserves_latest_summary_and_masks_direct_identifiers(self):
        class Model:
            def __init__(self, result):
                self.result = result
                self.calls = []

            def with_structured_output(self, schema):
                self.schema = schema
                return self

            async def ainvoke(self, messages):
                self.calls.append(messages)
                return self.schema.model_validate(self.result)

        fake = Model({"response": "지출 변동에 대비하고 싶으시군요. 무엇이 가장 걱정되시나요?",
                      "summary": "안정적인 생활비 300만 원을 중시함.", "sufficient": False, "turn_kind": "answer"})
        with patch.object(self.model, "llm", fake):
            result = await self.model.collect_answer(
                field="values", message="안정이 중요해요. 제 연락처는 010-1234-5678입니다.",
                summary="안정적인 생활비 300만 원을 중시함.", answer_count=0, conversation_context={},
            )
        self.assertEqual(len(fake.calls), 1)
        prompt = str(fake.calls[0][-1].content)
        self.assertIn("안정적인 생활비", prompt)
        self.assertIn("300만 원", prompt)
        self.assertNotIn("010-1234-5678", prompt)
        self.assertEqual(result.summary, "안정적인 생활비 300만 원을 중시함.")

    async def test_sensitive_summary_is_blocked_even_when_response_is_safe(self):
        from core.model_contracts import ModelOutputBlockedError

        fake = Mock()
        runnable = Mock()
        runnable.ainvoke = AsyncMock(return_value=self.model.MindsetModelOutput(
            response="말씀해 주셔서 감사합니다.", summary="연락처는 010-1234-5678입니다.",
            sufficient=False, turn_kind="answer",
        ))
        fake.with_structured_output.return_value = runnable
        with patch.object(self.model, "llm", fake), self.assertRaises(ModelOutputBlockedError):
            await self.model.collect_answer(field="values", message="안정", summary="", answer_count=0, conversation_context={})
        runnable.ainvoke.assert_awaited_once()


class MindsetTerminalTests(unittest.TestCase):
    def test_terminal_keeps_summary_business_text_and_masks_identifiers_consistently(self):
        from core.terminal_masking import TerminalPayloadMasker

        summary = "생활비 300만 원을 유지하고 싶음. 연락처 010-1234-5678"
        payload = {
            "request_id": "req-test", "session_id": 1, "thread_id": "thread-test", "status": "completed",
            "completed_at": datetime.now(timezone.utc),
            "output": {"step_id": "financial-mindset", "action": None, "response": summary,
                       "elicitation": {"field": "values", "summary": summary, "answer_count": 1,
                                       "status": "in_progress", "completion_reason": None, "next_field": None}},
        }
        masked = TerminalPayloadMasker(pseudonym_key=b"mindset-test-masking-key-32-bytes!!").mask(payload)
        output = masked["output"]
        self.assertEqual(output["response"], output["elicitation"]["summary"])
        self.assertIn("300만 원", output["elicitation"]["summary"])
        self.assertNotIn("010-1234-5678", output["elicitation"]["summary"])

class MindsetContinuationTests(unittest.IsolatedAsyncioTestCase):
    setUp = MindsetServiceTests.setUp
    answer = MindsetServiceTests.answer

    async def test_existing_form_uses_confirmation_and_never_saved_summary(self):
        state = request_state(event="start", form={"values": "직접 고친 내용"})
        output = (await self.service._run(state))["output"]
        self.assertIn("현재도 같은 생각", output["response"])
        self.assertEqual(output["elicitation"]["summary"], "직접 고친 내용")
        state["customer_info"]["current_form"]["values"] = ""
        output = (await self.service._run(state))["output"]
        self.assertEqual(output["response"], FIRST_QUESTIONS["values"])
        self.assertEqual(output["elicitation"]["summary"], "")

    async def test_answer_uses_latest_form_and_advances_once(self):
        state = request_state(form={"values": "직접 수정한 가치관"})
        with patch.object(self.service, "collect_answer", AsyncMock(return_value=self.answer())) as model:
            output = (await self.service._run(state))["output"]
        model.assert_awaited_once()
        self.assertEqual(model.await_args.kwargs["summary"], "직접 수정한 가치관")
        self.assertEqual(output["elicitation"]["answer_count"], 1)
        self.assertEqual(output["elicitation"]["status"], "in_progress")

    async def test_sufficient_and_limit_finish_with_same_summary_and_next_fixed_question(self):
        for count, sufficient, reason in ((0, True, "sufficient"), (2, False, "limit"), (2, True, "sufficient")):
            with self.subTest(count=count, sufficient=sufficient):
                with patch.object(self.service, "collect_answer", AsyncMock(return_value=self.answer(sufficient=sufficient))):
                    output = (await self.service._run(request_state(count=count)))["output"]
                result = output["elicitation"]
                self.assertEqual(result["completion_reason"], reason)
                self.assertEqual(result["answer_count"], count + 1)
                self.assertIn(result["summary"], output["response"])
                self.assertTrue(output["response"].endswith(FIRST_QUESTIONS["past_experience"]))
                self.assertNotIn("그 생각을 갖게 된 경험", output["response"])

    async def test_next_nonempty_field_uses_confirmation_and_last_field_finishes(self):
        with patch.object(self.service, "collect_answer", AsyncMock(return_value=self.answer(sufficient=True))):
            output = (await self.service._run(request_state(form={"past_experience": "이미 작성한 경험"})))["output"]
            last = (await self.service._run(request_state(field="future_goals")))["output"]
        self.assertIn("현재도 같은 생각", output["response"])
        self.assertIsNone(last["elicitation"]["next_field"])
        self.assertIn("질문을 모두 마쳤습니다", last["response"])

    async def test_clarification_and_skip_preserve_summary_and_do_not_consume_answers(self):
        for kind in ("clarification", "skip"):
            with self.subTest(kind=kind):
                with patch.object(self.service, "collect_answer", AsyncMock(return_value=self.answer(summary="잘못 바뀐 요약", sufficient=True, kind=kind))):
                    output = (await self.service._run(request_state(count=2, form={"values": "내가 쓴 요약"})))["output"]
                self.assertEqual(output["elicitation"]["summary"], "내가 쓴 요약")
                self.assertEqual(output["elicitation"]["answer_count"], 2)
                self.assertEqual(output["elicitation"]["completion_reason"], "skipped" if kind == "skip" else None)

    async def test_invalid_state_and_model_failure_do_not_mutate_request(self):
        state = request_state()
        before = deepcopy(state)
        with patch.object(self.service, "collect_answer", AsyncMock(side_effect=ValueError("malformed model response"))):
            with self.assertRaises(ValueError):
                await self.service._run(state)
        self.assertEqual(state, before)
        state["elicitation_state"]["answer_count"] = 3
        with patch.object(self.service, "collect_answer", new_callable=AsyncMock) as model:
            with self.assertRaises(ValidationError):
                await self.service._run(state)
            model.assert_not_awaited()

    async def test_skipping_empty_fields_records_only_the_explicit_refusal(self):
        form = {}
        with patch.object(self.service, "collect_answer", AsyncMock(return_value=self.answer(summary="만들어 낸 선호", kind="skip"))):
            for field in MINDSET_FIELDS:
                output = (await self.service._run(request_state(field=field, form=form)))["output"]
                result = output["elicitation"]
                self.assertEqual(result["answer_count"], 0)
                self.assertEqual(result["completion_reason"], "skipped")
                self.assertEqual(result["summary"], "이 항목에 대한 답변을 보류함.")
                self.assertIn(result["summary"], output["response"])
                form[field] = result["summary"]
        self.assertTrue(all(form[field].strip() for field in MINDSET_FIELDS))


class MindsetGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_guided_graph_bypasses_planner_and_composer_and_keeps_typed_result(self):
        configure_runtime()
        from agents.elicit import service
        from agents.elicit.builder import build_graph as build_elicit
        from agents.elicit.model import MindsetModelOutput
        from graph.builder import build_graph
        from tests.instrumentation import fake_instrumentation
        from tests.test_graph_execution import all_agent_graphs

        planner = Mock(side_effect=AssertionError("guided collection cannot call planner"))
        composer = AsyncMock(side_effect=AssertionError("guided collection cannot call composer"))
        agents = all_agent_graphs()
        agents["elicit"] = build_elicit()
        graph = build_graph(None, planner=planner, response_composer=composer, agent_graphs=agents,
                            instrumentation=fake_instrumentation()[0])
        answer = MindsetModelOutput(response="안정을 중시하시는군요. 그 이유를 들려주시겠어요?",
                                   summary="안정을 중시함. 이유는 미확인.", sufficient=False, turn_kind="answer")
        with patch.object(service, "collect_answer", AsyncMock(return_value=answer)) as model:
            opening = await graph.ainvoke(request_state(event="start"))
            model.assert_not_awaited()
            result = await graph.ainvoke(request_state())
            model.assert_awaited_once()
        planner.assert_not_called()
        composer.assert_not_awaited()
        self.assertEqual(opening["output"]["response"], FIRST_QUESTIONS["values"])
        self.assertEqual(result["output"]["elicitation"]["answer_count"], 1)
        self.assertEqual(result["output"]["elicitation"]["summary"], answer.summary)
        self.assertEqual(result["output"]["response"], answer.response)
        self.assertIsNone(result["output"]["action"])

    async def test_invalid_or_failed_guided_result_cannot_change_form(self):
        configure_runtime()
        from agents.elicit import service
        from agents.elicit.builder import build_graph as build_elicit
        from graph.builder import build_graph
        from tests.instrumentation import fake_instrumentation
        from tests.test_graph_execution import all_agent_graphs

        agents = all_agent_graphs()
        agents["elicit"] = build_elicit()
        graph = build_graph(None, planner=Mock(), response_composer=AsyncMock(), agent_graphs=agents,
                            instrumentation=fake_instrumentation()[0])
        state = request_state(form={"values": "유저가 쓴 값"})
        original = deepcopy(state)
        with patch.object(service, "collect_answer", AsyncMock(side_effect=ValueError("invalid schema"))):
            result = await graph.ainvoke(state)
        self.assertNotIn("elicitation", result["output"])
        self.assertEqual(state, original)


class MindsetProjectionTests(unittest.TestCase):
    def test_graph_input_preserves_request_state_and_generic_output_omits_result(self):
        configure_runtime()
        from transport.chat_service import graph_input
        from transport.contracts import CompletedTerminal
        from tools.output_policy import validate_form_update_contract

        state = request_state()["elicitation_state"]
        projected = graph_input(make_request(step_id="financial-mindset", request_mode="mindset_elicitation",
                                             elicitation_state=state))
        self.assertEqual(projected["elicitation_state"], state)
        self.assertNotIn("elicitation_state", graph_input(make_request()))
        output = {"response": "일반 답변", "step_id": "financial-mindset", "action": None}
        terminal = CompletedTerminal(request_id="plain", session_id=1, thread_id="plain-thread",
                                     completed_at=datetime.now(timezone.utc), output=output)
        self.assertNotIn("elicitation", terminal.model_dump()["output"])
        wrong = dict(output, elicitation={"field": "past_experience", "summary": "요약", "answer_count": 1,
                                         "status": "in_progress", "completion_reason": None, "next_field": None})
        validation = validate_form_update_contract({**request_state(), "output": wrong})
        self.assertFalse(validation.valid)
        self.assertNotIn("elicitation", validation.output)
