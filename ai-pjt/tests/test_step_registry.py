from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_ROOT))

from core.step_registry import (  # noqa: E402
    StepRegistryError,
    load_step_candidates,
    load_step_registry,
)


class StepRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_step_registry()

    def _write_registry(self, payload: dict) -> Path:
        temporary_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        )
        with temporary_file:
            json.dump(payload, temporary_file)
        self.addCleanup(Path(temporary_file.name).unlink, missing_ok=True)
        return Path(temporary_file.name)

    def test_shared_registry_loads_all_unique_steps(self) -> None:
        candidates = load_step_candidates()

        self.assertEqual(len(candidates), 23)
        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertTrue(all("fallback" in agents for agents in candidates.values()))

    def test_rejects_duplicate_step_ids(self) -> None:
        payload = copy.deepcopy(self.registry)
        payload["steps"].append(copy.deepcopy(payload["steps"][0]))

        with self.assertRaisesRegex(StepRegistryError, "duplicate step_id"):
            load_step_registry(self._write_registry(payload))

    def test_rejects_unregistered_or_missing_fallback_agents(self) -> None:
        for allowed_agents in (["qa", "unknown", "fallback"], ["qa"]):
            with self.subTest(allowed_agents=allowed_agents):
                payload = copy.deepcopy(self.registry)
                payload["steps"][0]["allowed_agents"] = allowed_agents
                with self.assertRaises(StepRegistryError):
                    load_step_registry(self._write_registry(payload))


if __name__ == "__main__":
    unittest.main()
