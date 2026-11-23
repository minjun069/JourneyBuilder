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
    load_agent_descriptions,
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

        self.assertEqual(len(candidates), 25)
        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertTrue(all("fallback" not in agents for agents in candidates.values()))

    def test_rejects_duplicate_step_ids(self) -> None:
        payload = copy.deepcopy(self.registry)
        payload["steps"].append(copy.deepcopy(payload["steps"][0]))

        with self.assertRaisesRegex(StepRegistryError, "duplicate step_id"):
            load_step_registry(self._write_registry(payload))

    def test_general_is_an_explicit_transport_only_fallback(self) -> None:
        self.assertEqual(self.registry["transport_fallback_step_ids"], ["general"])
        self.assertEqual(
            self.registry["transport_fallback_allowed_agents"],
            ["qa"],
        )
        self.assertEqual(load_step_candidates()["general"], ["qa"])

    def test_every_registered_agent_has_one_runtime_description(self) -> None:
        descriptions = load_agent_descriptions()
        self.assertEqual(set(descriptions), set(self.registry["agents"]))
        self.assertTrue(all(description.strip() for description in descriptions.values()))

    def test_rejects_missing_registered_agent_description(self) -> None:
        payload = copy.deepcopy(self.registry)
        payload["agent_descriptions"].pop(payload["agents"][0])

        with self.assertRaisesRegex(StepRegistryError, "describe every"):
            load_step_registry(self._write_registry(payload))

    def test_rejects_transport_fallback_that_duplicates_supported_step(self) -> None:
        payload = copy.deepcopy(self.registry)
        payload["transport_fallback_step_ids"] = [payload["steps"][0]["step_id"]]

        with self.assertRaisesRegex(StepRegistryError, "must not duplicate"):
            load_step_registry(self._write_registry(payload))

    def test_rejects_unregistered_agent(self) -> None:
        payload = copy.deepcopy(self.registry)
        payload["steps"][0]["allowed_agents"] = ["qa", "unknown"]
        with self.assertRaises(StepRegistryError):
            load_step_registry(self._write_registry(payload))

        payload = copy.deepcopy(self.registry)
        payload["transport_fallback_allowed_agents"] = ["unknown"]
        with self.assertRaises(StepRegistryError):
            load_step_registry(self._write_registry(payload))


if __name__ == "__main__":
    unittest.main()
