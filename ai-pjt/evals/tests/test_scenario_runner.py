import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

EVALS = Path(__file__).resolve().parents[1]
ROOT = EVALS.parents[1]
if str(EVALS) not in sys.path:
    sys.path.append(str(EVALS))
from scenario_inputs import ScenarioInputs, load_registry, load_scenario, prepare_turns
from scenario_results import EventCollector, measured_calls, summarize
from scenario_runner import execute, main, parse_args, readback, _readback_get


class InputTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry(ROOT)
        self.inputs = ScenarioInputs(self.registry)

    def test_complete_example_and_causal_inputs(self):
        path = EVALS / "data/community-retirement-case-v1.input.json"
        data = load_scenario(path, EVALS / "schemas/ai-evaluation-scenario-v1.schema.json")
        turns = prepare_turns(data, self.registry)
        self.assertEqual(len(turns), 43)
        self.assertEqual(turns[0]["customer_info"]["saved_data"], {})
        self.assertEqual(turns[3]["customer_info"]["saved_data"]["basic_info"]["end_age"], 90)
        self.assertEqual(turns[4]["customer_info"]["saved_data"]["basic_info"]["end_age"], 95)
        self.assertNotIn("financial_assets", turns[4]["customer_info"]["saved_data"])
        self.assertTrue(any(t["mapping_notes"] for t in turns))

    def test_partial_change_empty_and_null(self):
        self.inputs.apply("basic-info", {"나이": 58, "관심사": "은퇴"})
        customer, _, _ = self.inputs.apply("basic-info", {})
        customer["saved_data"]["basic_info"]["age"] = 99
        customer, _, _ = self.inputs.apply("basic-info", {"관심사": None})
        self.assertEqual(customer["current_form"], {"age": 58, "interest": None})

    def test_arrays_replace_and_false_zero_survive(self):
        self.inputs.apply("income", {"소득": [{"구분": "근로소득", "소득 금액(원/월)": 0, "고정/변동": "변동"}]})
        customer, _, _ = self.inputs.apply("income", {})
        self.assertEqual(customer["current_form"]["assets"][0]["amount"], 0)
        self.assertIs(customer["current_form"]["assets"][0]["is_fixed"], False)
        customer, _, _ = self.inputs.apply("income", {"소득": []})
        self.assertEqual(customer["current_form"], {"assets": []})

    def test_dependent_partial_update(self):
        self.inputs.apply("basic-info", {"자녀": [{"나이": 25}], "기타 부양가족": [{"관계": "배우자", "나이": 53}]})
        customer, _, _ = self.inputs.apply("basic-info", {"자녀": []})
        self.assertEqual(customer["current_form"]["dependents"], {"children": [], "others": [{"name": "배우자", "age": 53}]})

    def test_unknown_invalid_and_duplicate_fields_fail(self):
        for values in ({"없는항목": 1}, {"나이": True}, {"직업": "CEO"}, {"나이": 200},
                       {"성향": [], "재무 성향": []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.inputs.apply("basic-info", values)

    def test_goal_phase_replacement_keeps_other_phase(self):
        row = {"시작 나이": 58, "종료 나이": 60, "내용": "생활", "금액(원/월)": 0, "고정/변동": "고정"}
        self.inputs.apply("financial-goals", {"은퇴 전 기간별 지출": [row], "은퇴 후 기간별 지출": [row]})
        customer, _, _ = self.inputs.apply("financial-goals", {"은퇴 전 기간별 지출": []})
        self.assertEqual(len(customer["current_form"]["goal_periods"]), 1)
        self.assertEqual(customer["current_form"]["goal_periods"][0]["phase"], "after")

    def test_dry_run_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--worktree", str(ROOT), "--input", str(EVALS / "data/community-retirement-case-v1.input.json"),
                    "--output", str(Path(tmp) / "run"), "--dry-run"]
            self.assertEqual(main(args), 0)
            with self.assertRaises(ValueError):
                main(args)


class CollectionTests(unittest.TestCase):
    def test_legacy_request_signature_and_content_still_cross_masker(self):
        from scenario_runtime import RequestContentSink, bind_request, request_start, request_succeed
        from core.masking import MaskingSink
        from core.observability_masking import build_masked_runtime_instrumentation
        from core.runtime_instrumentation import InstrumentationPort, RuntimeStage
        from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
        class Collector:
            def __init__(self): self.events = []
            def emit(self, payload): self.events.append(payload)
        collector = Collector()
        _, masked = build_masked_runtime_instrumentation(
            pseudonym_key=b"scenario-test-pseudonym-key-32bytes", downstream=collector, sink=MaskingSink.TRACE_EXPORT)
        sink = RequestContentSink(masked)
        adapter = RuntimeInstrumentationAdapter(InstrumentationPort(sink))
        bound = adapter.bind(request_id="eval-one", thread_id="eval-thread", step_id="intro")
        class LegacyBound:
            def start(self, stage): return bound.start(stage)
            def succeed(self, span): return bound.succeed(span)
        class Legacy:
            def bind(self, *, request_id, thread_id, step_id, plan_id):
                self.plan_id = plan_id
                return LegacyBound()
        legacy = Legacy()
        observed = bind_request(legacy, "eval-one", "eval-thread", "intro")
        span = request_start(observed, sink, RuntimeStage.REQUEST_PROCESSING, {"message": "private@example.com"})
        request_succeed(observed, sink, span, {"response": "private@example.com"})
        self.assertEqual(legacy.plan_id, "plan-eval-one")
        self.assertEqual(len(collector.events), 2)
        self.assertIn("input", collector.events[0])
        self.assertIn("output", collector.events[1])
        self.assertNotIn("private@example.com", json.dumps(collector.events))

    def test_remote_cost_requires_usage_prices_and_all_observations(self):
        settings = SimpleNamespace(langfuse_base_url="https://example.invalid", langfuse_public_key="test", langfuse_secret_key="test")
        class Response:
            is_success = True
            status_code = 200
            def __init__(self, value):
                self.value = value
            def json(self):
                return self.value
        trace = {"output": {"response": "answer"}, "totalCost": 0.02,
                 "observations": [{"type": "GENERATION", "metadata": {"call_id": "call-1"},
                                   "endTime": "2026-09-29", "costDetails": {"total": 0.02}}]}
        class Client:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get(self, path):
                return Response({"data": [{"id": "test"}]} if path.endswith("projects") else deepcopy(trace))
        turns = [{"ordinal": 1, "repetition": 1, "trace_id": "a", "model_calls": [{"call_id": "call-1", "usage_status": "reported"}]}]
        with patch("httpx.Client", Client):
            self.assertEqual(readback(settings, turns)["total_cost_usd"], 0.02)
            trace["observations"][0]["costDetails"] = {}
            trace["totalCost"] = 0
            self.assertIsNone(readback(settings, turns)["total_cost_usd"])
            trace["observations"][0]["costDetails"] = {"total": 0}
            turns[0]["model_calls"][0]["usage_status"] = "unavailable"
            self.assertIsNone(readback(settings, turns)["total_cost_usd"])
            trace["observations"] = []
            self.assertEqual(readback(settings, turns)["status"], "incomplete")

    def test_remote_rate_limit_waits_and_preserves_success(self):
        from unittest.mock import Mock
        client = Mock()
        successful = SimpleNamespace(status_code=200)
        client.get.side_effect = [SimpleNamespace(status_code=429, headers={"retry-after": "120"}), successful]
        with patch("scenario_runner.sleep") as wait:
            self.assertIs(_readback_get(client, "/trace"), successful)
            wait.assert_called_once_with(60)

    def test_remote_verified_cache_does_not_refetch_trace(self):
        from unittest.mock import Mock
        settings = SimpleNamespace(langfuse_base_url="https://example.invalid", langfuse_public_key="test", langfuse_secret_key="test")
        client = Mock()
        client.get.return_value = SimpleNamespace(status_code=200, is_success=True, json=lambda: {"data": []})
        cached = {"trace_id": "a", "complete": True, "measurements": [], "expected_calls": 0,
                  "priced_observations": 0, "total_cost_usd": 0}
        with patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = readback(settings, [{"ordinal": 1, "repetition": 1, "trace_id": "a"}],
                              {"verification": "call IDs", "traces": [cached]})
        self.assertEqual(result["status"], "verified")
        client.get.assert_called_once_with("/api/public/projects")

    def test_usage_missing_not_zero_cost_and_started_not_counted(self):
        events = [{"stage": "model", "status": "started", "call_id": "a"},
                  {"stage": "model", "status": "succeeded", "call_id": "a", "input_tokens": 0,
                   "output_tokens": 0, "usage_status": "reported"},
                  {"stage": "model", "status": "failed", "call_id": "b", "usage_status": "unavailable"}]
        calls = measured_calls(events + [events[1]])
        self.assertEqual(len(calls), 2)
        result = summarize([{"status": "completed", "elapsed_ms": 10, "model_calls": calls}])
        self.assertEqual(result["usage_unavailable_calls"], 1)
        self.assertFalse(result["usage_complete"])

    def test_export_failure_preserves_local_events(self):
        class Broken:
            def emit(self, value):
                raise RuntimeError("offline")
        with tempfile.TemporaryDirectory() as tmp:
            collector = EventCollector(Path(tmp) / "events.jsonl", Broken())
            collector.emit({"stage": "model"})
            self.assertEqual(collector.export_errors, 1)
            self.assertEqual(len(collector.events), 1)

    def test_mock_execution_carries_only_completed_context(self):
        from core.masking import MaskingSink
        from core.observability_masking import build_masked_runtime_instrumentation
        from core.config import settings
        observed_inputs = []
        class Graph:
            async def ainvoke(self, state, config):
                observed_inputs.append(deepcopy(state))
                if len(observed_inputs) == 2:
                    raise TimeoutError()
                return {"output": {"response": "실제 mock 응답", "actions": [{"kind": "proposal"}]},
                        "conversation_context": {"turns": ["completed"]}}
        def build(collector):
            adapter, sink = build_masked_runtime_instrumentation(
                pseudonym_key=b"scenario-test-pseudonym-key-32bytes", downstream=collector, sink=MaskingSink.TRACE_EXPORT)
            return Graph(), adapter, sink, {"turns": []}
        with tempfile.TemporaryDirectory() as tmp:
            args = parse_args(["--worktree", str(ROOT), "--input", str(EVALS / "data/community-retirement-case-v1.input.json"),
                               "--output", tmp, "--no-langfuse"])
            turns = prepare_turns(load_scenario(args.input, args.schema), load_registry(ROOT))[:3]
            manifest = {"run_id": "mock", "label": "mock", "revision": {"commit": "test"}, "mode": "graph-direct"}
            with patch("scenario_runtime.build_runtime", build), patch("scenario_runner.index_metadata", return_value={}), patch.object(settings, "validate_runtime"):
                result = asyncio.run(execute(args, manifest, turns, {}))
            self.assertEqual(result, 1)
            self.assertEqual(observed_inputs[1]["conversation_context"], {"turns": ["completed"]})
            self.assertEqual(observed_inputs[2]["conversation_context"], {"turns": ["completed"]})
            self.assertEqual(observed_inputs[2]["customer_info"]["saved_data"], {})
            rows = json.loads((Path(tmp) / "results.json").read_text())
            self.assertEqual([r["status"] for r in rows], ["completed", "timeout", "completed"])
            self.assertEqual(len((Path(tmp) / "turns.jsonl").read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
