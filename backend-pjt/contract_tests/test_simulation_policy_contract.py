"""시뮬레이션 정책 사본이 모듈 원본과 어긋나지 않게 막는다 (S15P21B109-135).

BE 는 contract-fixtures/simulation/v1/policy.json 을 읽어 모듈에 가정 다섯 개를
전달한다. 원본은 ai-pjt/retirement_simulation/docs/simulation-policy.json 이다.
사본을 두는 이유는 reports/policy.py 와 같은 선례를 따르기 위해서다 — BE 가 자기
schema_version 과 status 를 소유해야 fail-closed 검증을 걸 수 있고, 모듈 docs 는
sdist 자료라 안정 계약 경로가 아니다.

사본이 두 벌이면 반드시 어긋나므로 여기서 대조한다.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from simulations.policy import (  # noqa: E402
    ASSUMPTION_FIELDS,
    SimulationPolicyError,
    active_simulation_policy_version,
    load_simulation_policy,
    max_comparison_scenarios,
    simulation_assumption_values,
)

MODULE_POLICY_PATH = (
    REPOSITORY_ROOT / "ai-pjt/retirement_simulation/docs/simulation-policy.json"
)


def _module_policy() -> dict:
    return json.loads(MODULE_POLICY_PATH.read_text(encoding="utf-8"))


class SimulationPolicyDriftTests(unittest.TestCase):
    def test_assumption_values_match_the_module_policy(self) -> None:
        module = _module_policy()["assumption_values"]
        server = simulation_assumption_values()
        self.assertEqual(set(server), set(ASSUMPTION_FIELDS))
        self.assertEqual(server, module)

    def test_policy_version_matches_the_module_policy(self) -> None:
        self.assertEqual(
            active_simulation_policy_version(), _module_policy()["policy_version"]
        )

    def test_comparison_limit_matches_the_module_policy(self) -> None:
        self.assertEqual(
            max_comparison_scenarios(), _module_policy()["max_comparison_scenarios"]
        )

    def test_server_fixture_records_the_module_policy_as_its_source(self) -> None:
        source = load_simulation_policy()["source_policy"]
        self.assertEqual(
            (REPOSITORY_ROOT / source).resolve(), MODULE_POLICY_PATH.resolve()
        )


class SimulationPolicyContractTests(unittest.TestCase):
    def test_mvp_values_are_the_decided_ones(self) -> None:
        # 문서에 적힌 4% / 10% / 2% / 1,000경로 / seed 42 를 코드에 고정한다.
        self.assertEqual(
            simulation_assumption_values(),
            {
                "annual_return_mean": 0.04,
                "annual_return_volatility": 0.1,
                "annual_inflation_rate": 0.02,
                "num_paths": 1000,
                "seed": 42,
            },
        )
        self.assertEqual(max_comparison_scenarios(), 1)

    def test_assumption_values_are_a_fresh_copy_each_call(self) -> None:
        first = simulation_assumption_values()
        first["num_paths"] = 1
        self.assertEqual(simulation_assumption_values()["num_paths"], 1000)

    def test_module_accepts_the_server_assumptions(self) -> None:
        """정책 값이 모듈 검증을 실제로 통과하는지. 키 이름 하나만 틀려도 여기서 잡힌다."""
        from retirement_simulation.input_adapter import (  # noqa: PLC0415
            map_simulation_assumptions,
        )

        map_simulation_assumptions(simulation_assumption_values())


class SimulationPolicyFailClosedTests(unittest.TestCase):
    """설정이 깨지면 시연값으로 폴백하지 않고 멈춰야 한다."""

    def setUp(self) -> None:
        load_simulation_policy.cache_clear()
        self.addCleanup(load_simulation_policy.cache_clear)

    def _write(self, payload: object) -> Path:
        import tempfile  # noqa: PLC0415

        directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, directory, True)
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _load_with(self, payload: object) -> None:
        from unittest.mock import patch  # noqa: PLC0415

        path = self._write(payload)
        with patch("simulations.policy._policy_path", return_value=path):
            load_simulation_policy()

    def test_missing_file_raises(self) -> None:
        from unittest.mock import patch  # noqa: PLC0415

        missing = Path(__file__).resolve().parent / "does-not-exist.json"
        with patch("simulations.policy._policy_path", return_value=missing):
            with self.assertRaises(SimulationPolicyError):
                load_simulation_policy()

    def test_unapproved_or_malformed_policies_are_rejected(self) -> None:
        base = json.loads(
            (REPOSITORY_ROOT / "contract-fixtures/simulation/v1/policy.json").read_text(
                encoding="utf-8"
            )
        )
        cases = {
            "status_not_approved": {**base, "status": "draft"},
            "schema_version_bump": {**base, "schema_version": "2.0"},
            "empty_policy_version": {**base, "policy_version": ""},
            "unknown_key": {**base, "unexpected": 1},
            "missing_key": {k: v for k, v in base.items() if k != "approved_at"},
            "extra_assumption": {
                **base,
                "assumption_values": {**base["assumption_values"], "extra": 1},
            },
            "missing_assumption": {
                **base,
                "assumption_values": {
                    k: v
                    for k, v in base["assumption_values"].items()
                    if k != "seed"
                },
            },
            "bool_num_paths": {
                **base,
                "assumption_values": {**base["assumption_values"], "num_paths": True},
            },
            "zero_num_paths": {
                **base,
                "assumption_values": {**base["assumption_values"], "num_paths": 0},
            },
            "negative_seed": {
                **base,
                "assumption_values": {**base["assumption_values"], "seed": -1},
            },
            "string_rate": {
                **base,
                "assumption_values": {
                    **base["assumption_values"],
                    "annual_return_mean": "0.04",
                },
            },
            "zero_scenarios": {**base, "max_comparison_scenarios": 0},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                load_simulation_policy.cache_clear()
                with self.assertRaises(SimulationPolicyError):
                    self._load_with(payload)


if __name__ == "__main__":
    unittest.main()
