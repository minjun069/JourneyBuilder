from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from core.prompt_registry import PromptInvocationRecord, PromptSpec
from prompts import (
    PROMPT_RELEASE,
    get_prompt,
    get_prompt_manifest,
    prompt_ids,
)
from prompts.common import ADVISOR_PERSONA, COMMON_PRINCIPLES
from prompts.rendering import render_prompt_payload


AI_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = AI_ROOT / "prompts" / "manifest.json"
DEFINITION_ROOT = AI_ROOT / "prompts" / "definitions"
PRODUCTION_ROOTS = (
    AI_ROOT / "agents",
    AI_ROOT / "core",
    AI_ROOT / "graph",
)


class PromptRegistryTests(unittest.TestCase):
    def test_committed_manifest_matches_current_image_exactly(self) -> None:
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(committed, get_prompt_manifest())
        self.assertEqual(committed["release"], PROMPT_RELEASE)

    def test_current_catalog_has_one_definition_module_per_prompt(self) -> None:
        modules = {
            path.stem
            for path in DEFINITION_ROOT.glob("*.py")
            if path.stem not in {"__init__", "_factory"}
        }
        self.assertEqual(len(modules), len(prompt_ids()))
        self.assertEqual(len(prompt_ids()), 12)
        self.assertTrue(all(get_prompt(prompt_id).version == "4.0.0" for prompt_id in prompt_ids()))

    def test_prompt_payload_contract_rejects_missing_and_unknown_fields(self) -> None:
        prompt = get_prompt("qa.plan")
        payload = {
            "step_id": "basic-info",
            "question": "질문",
            "feedback": "",
        }
        self.assertEqual(prompt.validate_payload(payload), payload)
        with self.assertRaisesRegex(ValueError, "missing keys"):
            prompt.validate_payload({})
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            prompt.validate_payload({**payload, "extra": True})

    def test_model_contracts_keep_only_task_specific_inputs(self) -> None:
        self.assertEqual(get_prompt("aggregator.compose").required_input_keys, {"answer"})
        self.assertEqual(get_prompt("error_review.analyze").required_input_keys, {"fields", "review_rules"})
        self.assertNotIn("route_constraints", get_prompt("planner.plan").required_input_keys)
        self.assertEqual((get_prompt("qa.answer").required_input_keys - {"feedback"}) | {"answer"}, get_prompt("qa.judge").required_input_keys)

    def test_common_principles_persona_and_readable_sections_are_composed(self) -> None:
        answer = get_prompt("qa.answer")
        classifier = get_prompt("qa.plan")
        rendered = render_prompt_payload(classifier, {
            "step_id": "basic-info",
            "question": "질문",
            "feedback": "",
        })

        self.assertIn(COMMON_PRINCIPLES, answer.system_text)
        self.assertIn(ADVISOR_PERSONA, answer.system_text)
        self.assertIn(COMMON_PRINCIPLES, classifier.system_text)
        self.assertNotIn(ADVISOR_PERSONA, classifier.system_text)
        self.assertIn("[사용자 질문]", rendered)
        self.assertNotIn("runtime_payload", rendered)

    def test_invocation_metadata_rejects_incomplete_failed_records(self) -> None:
        with self.assertRaisesRegex(ValueError, "error_code"):
            PromptInvocationRecord(
                prompt_id="qa.plan",
                prompt_version="1.0.0",
                prompt_hash="a" * 64,
                prompt_release=PROMPT_RELEASE,
                prompt_policy_version="2.0",
                purpose="qa",
                model_provider="gemini",
                model_name="gemini-2.5-flash-lite",
                model_temperature=0.0,
                status="failed",
                duration_ms=1,
            )

    def test_all_specs_have_distinct_ids_and_content_hashes(self) -> None:
        ids = prompt_ids()
        specs = [get_prompt(prompt_id) for prompt_id in ids]
        self.assertTrue(all(isinstance(prompt, PromptSpec) for prompt in specs))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(prompt.content_hash) == 64 for prompt in specs))

    def test_qa_has_one_planning_answering_and_review_prompt(self) -> None:
        self.assertEqual({key for key in prompt_ids() if key.startswith("qa.")}, {"qa.plan", "qa.answer", "qa.judge"})


class ProductionPromptCallAuditTests(unittest.TestCase):
    def _production_files(self) -> list[Path]:
        return sorted(
            path
            for root in PRODUCTION_ROOTS
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )

    def _prompt_modules(self) -> list[Path]:
        modules: list[Path] = []
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "get_prompt"
                for node in ast.walk(tree)
            ):
                modules.append(path)
        return modules

    def test_registered_prompt_usage_matches_catalog(self) -> None:
        referenced: set[str] = set()
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "get_prompt"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    referenced.add(node.args[0].value)
        self.assertEqual(referenced, set(prompt_ids()))

    def test_every_production_secure_call_declares_prompt_metadata(self) -> None:
        missing: list[str] = []
        secure_names = {"secure_invoke", "secure_ainvoke", "secure_structured_invoke"}
        for path in self._production_files():
            if path.name == "model_boundary.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else None
                if name in secure_names and not any(
                    keyword.arg == "prompt" for keyword in node.keywords
                ):
                    missing.append(f"{path.relative_to(AI_ROOT)}:{node.lineno}")
        self.assertEqual(missing, [])

    def test_only_model_boundary_constructs_model_messages(self) -> None:
        violations: list[str] = []
        for path in self._production_files():
            if path.name == "model_boundary.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"SystemMessage", "HumanMessage"}
                ):
                    violations.append(f"{path.relative_to(AI_ROOT)}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_no_production_provider_call_bypasses_secure_boundary(self) -> None:
        violations: list[str] = []
        for path in self._production_files():
            if path.name == "model_boundary.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"invoke", "ainvoke"}
                ):
                    # This is a compiled LangGraph subgraph call, not a model
                    # provider boundary. Every model call inside it is audited
                    # in the registered Agent modules.
                    if (
                        path.name in {"builder.py", "agent_registry.py"}
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "agent_graph"
                    ):
                        continue
                    violations.append(f"{path.relative_to(AI_ROOT)}:{node.lineno}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
