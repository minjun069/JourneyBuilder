"""Static acceptance checks for F22 responsibility boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


AI_ROOT = Path(__file__).resolve().parents[1]


def _imports(relative: str) -> set[str]:
    tree = ast.parse((AI_ROOT / relative).read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
    return result


class ResponsibilityBoundaryTests(unittest.TestCase):
    def test_top_level_builder_contains_only_graph_assembly(self) -> None:
        source = (AI_ROOT / "graph/builder.py").read_text(encoding="utf-8")
        for forbidden in (
            "secure_ainvoke",
            "evaluate_user_message",
            "evaluate_route_plan",
            "execute_route_plan(",
            "build_context_window",
        ):
            self.assertNotIn(forbidden, source)

    def test_every_agent_is_a_package_with_explicit_boundaries(self) -> None:
        required = {
            "qa": {"builder.py", "service.py", "model.py", "policy.py"},
            "input_assist": {"builder.py", "service.py", "model.py", "defaults.py"},
            "error_review": {"builder.py", "service.py", "model.py", "policy.py"},
            "chart_interpret": {"builder.py", "service.py", "model.py", "policy.py"},
            "elicit": {"builder.py", "service.py", "model.py"},
        }
        for agent, names in required.items():
            with self.subTest(agent=agent):
                package = AI_ROOT / "agents" / agent
                self.assertTrue(package.is_dir())
                self.assertTrue(names.issubset({path.name for path in package.iterdir()}))
                builder_source = (package / "builder.py").read_text(encoding="utf-8")
                self.assertNotIn("secure_ainvoke", builder_source)
                self.assertNotIn("get_prompt", builder_source)

    def test_router_is_removed_and_tool_runtime_has_no_graph_dependency(self) -> None:
        self.assertFalse((AI_ROOT / "graph/router.py").exists())
        for path in AI_ROOT.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(AI_ROOT))
            self.assertNotIn("graph.router", _imports(relative))
        self.assertFalse(any(
            name.startswith("graph.")
            for name in _imports("tools/runtime.py") | _imports("tools/authorization.py")
        ))

    def test_context_transport_and_model_facades_are_importable(self) -> None:
        from core.context_domain import ConversationContext
        from core.context_service import ContextStateAPI
        from core.context_window import ContextWindow
        from core.model_contracts import ModelPurpose
        from transport.chat_service import stream_v2_chat_events
        from transport.sse_presenter import encode_sse

        self.assertIsNotNone(ConversationContext)
        self.assertIsNotNone(ContextStateAPI)
        self.assertIsNotNone(ContextWindow)
        self.assertEqual(ModelPurpose.PLANNER.value, "planner")
        self.assertTrue(callable(stream_v2_chat_events))
        self.assertTrue(callable(encode_sse))


if __name__ == "__main__":
    unittest.main()
