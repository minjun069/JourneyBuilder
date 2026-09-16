"""시뮬레이션 runtime 정책 소유·값·패키지 호환 계약을 검증한다.

BE 는 `contract-fixtures/simulation/v1/policy.json`을 기계 단일 원천으로 읽어
모듈에 가정 다섯 개를 전달한다. 패키지 문서는 근거와 과거 인수인계
기록이지 runtime 정책 사본이 아니다.
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

POLICY_RATIONALE_PATH = (
    REPOSITORY_ROOT / "backend-pjt/packages/retirement_simulation/docs/simulation-policy.md"
)


class SimulationPolicyOwnershipTests(unittest.TestCase):
    def test_runtime_fixture_records_the_human_rationale(self) -> None:
        rationale = load_simulation_policy()["rationale_document"]
        self.assertEqual(
            (REPOSITORY_ROOT / rationale).resolve(), POLICY_RATIONALE_PATH.resolve()
        )
        self.assertTrue(POLICY_RATIONALE_PATH.is_file())


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
