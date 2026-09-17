from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from core.prompt_registry import PromptInvocationRecord, PromptSpec
from prompts import (
    ACTIVE_PROMPT_RELEASE,
    PREVIOUS_PROMPT_RELEASE,
    get_prompt,
    get_prompt_manifest,
    prompt_ids,
)


AI_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = AI_ROOT / "prompts" / "manifest.json"
PREVIOUS_MANIFEST_PATH = AI_ROOT / "prompts" / "manifest.previous.json"
PRODUCTION_ROOTS = (
    AI_ROOT / "agents",
    AI_ROOT / "core",
    AI_ROOT / "graph",
)


class PromptRegistryTests(unittest.TestCase):
    def test_committed_manifest_matches_active_release_exactly(self) -> None:
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            committed,
            get_prompt_manifest(release=ACTIVE_PROMPT_RELEASE),
        )

    def test_committed_previous_manifest_is_a_frozen_rollback_snapshot(self) -> None:
        committed = json.loads(
            PREVIOUS_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(
            committed,
            get_prompt_manifest(release=PREVIOUS_PROMPT_RELEASE),
        )

    def test_current_and_previous_releases_are_selectable(self) -> None:
        for release in (PREVIOUS_PROMPT_RELEASE, ACTIVE_PROMPT_RELEASE):
            with self.subTest(release=release), patch.dict(
                os.environ,
                {"PROMPT_RELEASE": release},
            ):
                self.assertEqual(get_prompt_manifest()["release"], release)
                self.assertEqual(len(prompt_ids()), 18)

    def test_changed_prompt_content_requires_a_new_semantic_version(self) -> None:
        for prompt_id in prompt_ids(release=ACTIVE_PROMPT_RELEASE):
            active = get_prompt(prompt_id, release=ACTIVE_PROMPT_RELEASE)
            previous = get_prompt(prompt_id, release=PREVIOUS_PROMPT_RELEASE)
            if active.content_hash != previous.content_hash:
                self.assertNotEqual(
                    active.version,
                    previous.version,
                    f"{prompt_id} changed content without a version bump",
                )

    def test_prompt_payload_contract_rejects_missing_and_unknown_fields(self) -> None:
        prompt = get_prompt("qa.specify")
        self.assertEqual(prompt.validate_payload({"message": "질문"}), {"message": "질문"})
        with self.assertRaisesRegex(ValueError, "missing keys"):
            prompt.validate_payload({})
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            prompt.validate_payload({"message": "질문", "extra": True})

    def test_invocation_metadata_rejects_incomplete_failed_records(self) -> None:
        with self.assertRaisesRegex(ValueError, "error_code"):
            PromptInvocationRecord(
                prompt_id="qa.specify",
                prompt_version="1.0.0",
                prompt_hash="a" * 64,
                prompt_release=ACTIVE_PROMPT_RELEASE,
                prompt_policy_version="1.1",
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

    def test_active_qa_prompts_require_product_term_rag_without_source_metadata(self) -> None:
        decide = get_prompt("qa.decide_retrieval").system_text
        answer = get_prompt("qa.answer_with_evidence").system_text
        merge = get_prompt("qa.merge").system_text

        self.assertIn("제품 화면의 용어명·별칭·뜻", decide)
        self.assertIn("retrieve로 판단하세요", decide)
        self.assertIn("검색된 용어 절의 정의·사용 맥락·주의사항", answer)
        self.assertIn("출처·참고 문서 목록을 표시하지 마세요", answer)
        self.assertIn("출처 목록을 새로 붙이거나 복원하지 마세요", merge)


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
                        path.name == "builder.py"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "agent_graph"
                    ):
                        continue
                    violations.append(f"{path.relative_to(AI_ROOT)}:{node.lineno}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
