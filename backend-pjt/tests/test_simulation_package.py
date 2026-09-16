"""은퇴 시뮬레이션 패키지가 BE 이미지에 정식 설치되어 있는지 검사한다 (S15P21B109-135).

두 층으로 나눈다. 이미지 계약 검사(파일만 읽음, 어디서나 실행)와 실제 설치 검증
(이미지 안에서만 통과). 설치가 깨졌을 때 "계약은 맞는데 설치가 안 됐다" 가 바로 갈린다.

readiness.py 는 이 모듈을 import 하지 않으므로 설치가 깨져도 컨테이너는 healthy 로
뜬다. 즉 이 파일이 유일한 게이트다. skipUnless 를 걸지 않는 이유가 이것이다 —
조건부 skip 을 넣으면 설치가 안 된 상태에서도 CI 가 초록불이 된다.

로컬 venv 에서 InstalledSimulationTests 가 실패하면 1회 설치하면 된다:
    <venv>/python -m pip install --no-deps ./backend-pjt/packages/retirement_simulation
"""

from __future__ import annotations

from importlib import metadata, util
import json
from pathlib import Path
import sysconfig
import tomllib
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
MODULE_ROOT = REPOSITORY_ROOT / "backend-pjt/packages/retirement_simulation"
DISTRIBUTION = "retirewise-retirement-simulation"
# 버전은 어디에도 적지 않는다. 모듈 pyproject 가 단일 출처다 (S15P21B109-142).
WHEEL_GLOB = "retirewise_retirement_simulation-*-py3-none-any.whl"


def module_version() -> str:
    with (MODULE_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


class SimulationImageContractTests(unittest.TestCase):
    """test_asgi_runtime_contract.py 와 같은 층 — 파일만 읽으므로 어디서나 돈다."""

    @staticmethod
    def _dockerfile_instructions() -> str:
        """주석을 걷어낸 지시자만. 주석의 설명 문구가 검사에 걸리면 안 된다."""
        lines = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        return "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    def test_dockerfile_installs_the_module_from_a_built_wheel(self) -> None:
        dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("AS simulation-builder", dockerfile)
        self.assertIn("COPY backend-pjt/requirements-build.lock", dockerfile)
        self.assertIn("--require-hashes -r requirements-build.lock", dockerfile)
        self.assertIn("COPY backend-pjt/packages/retirement_simulation/", dockerfile)
        self.assertIn(WHEEL_GLOB, dockerfile)
        # 소스 폴백 금지 — PYTHONPATH 로 우회하면 설치본이 아닌 소스가 잡힌다.
        # 지시자만 본다. 이 규칙을 설명하는 주석 자체는 허용한다.
        self.assertNotIn("PYTHONPATH", self._dockerfile_instructions())

    def test_dockerfile_does_not_pin_the_module_version(self) -> None:
        """버전을 적으면 모듈이 올릴 때마다 이미지 빌드가 깨진다 (S15P21B109-142).

        실제로 0.1.0 을 네 곳에 적었다가 모듈 담당의 0.1.1 MR 이 충돌했다.
        """
        instructions = self._dockerfile_instructions()
        self.assertNotRegex(
            instructions, r"retirewise_retirement_simulation-\d+\.\d+"
        )

    def test_runtime_dependency_lock_is_not_touched_by_the_module_install(self) -> None:
        dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
        # 런타임 lock 은 여전히 --require-hashes 로 설치되어야 한다.
        self.assertIn("--require-hashes -r requirements.lock", dockerfile)
        # 모듈은 별도 pip 호출로 설치한다. 해시 잠금 호출에 로컬 wheel 을 섞을 수 없다.
        self.assertIn("--no-index --no-deps", dockerfile)

    def test_build_backend_pin_matches_the_module_pyproject(self) -> None:
        pyproject = (
            REPOSITORY_ROOT / "backend-pjt/packages/retirement_simulation/pyproject.toml"
        ).read_text(encoding="utf-8")
        build_lock = (BACKEND_ROOT / "requirements-build.lock").read_text(encoding="utf-8")
        self.assertIn('requires = ["setuptools==84.0.0"]', pyproject)
        self.assertIn("setuptools==84.0.0", build_lock)
        self.assertIn("--hash=sha256:", build_lock)

    def test_module_source_is_not_excluded_from_the_build_context(self) -> None:
        rules = [
            line.strip()
            for line in (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        # 패키지 docs/tests 제외는 있어도 된다(wheel 에 들어가지 않는다). 모듈 소스 자체를
        # 막는 규칙만 없으면 된다.
        for rule in rules:
            self.assertNotEqual(rule, "backend-pjt/packages/retirement_simulation")


class InstalledSimulationTests(unittest.TestCase):
    """이미지 안에서만 통과한다 — 이 클래스가 실제 설치 게이트다."""

    def test_installed_version_matches_the_module_source(self) -> None:
        """설치본 버전이 모듈 pyproject 와 같아야 한다.

        값을 여기 적지 않는다. 모듈이 버전을 올리면 이 테스트는 그대로 통과하고,
        이미지에 낡은 wheel 이 남아 있을 때만 실패한다.
        """
        self.assertEqual(metadata.version(DISTRIBUTION), module_version())

    def test_import_resolves_from_site_packages_not_the_repository_source(self) -> None:
        import retirement_simulation  # noqa: PLC0415

        package_root = Path(retirement_simulation.__file__).resolve().parent
        purelib = Path(sysconfig.get_path("purelib")).resolve()
        self.assertEqual(package_root, purelib / "retirement_simulation")

    def test_installed_package_adds_no_runtime_dependencies(self) -> None:
        distribution = metadata.distribution(DISTRIBUTION)
        self.assertFalse(distribution.requires)
        # 빌드 도구가 런타임 이미지로 새지 않았는지. 멀티스테이지의 요점이다.
        self.assertIsNone(util.find_spec("setuptools"))

    def test_install_is_a_normal_wheel_install(self) -> None:
        direct_url = json.loads(
            metadata.distribution(DISTRIBUTION).read_text("direct_url.json")
        )
        # 모듈 인수 검사(tests/installed/check_package.py)가 요구하는 형태다.
        # pip install <디렉터리> 나 -e 설치면 dir_info 가 남는다.
        self.assertNotIn("dir_info", direct_url)
        self.assertIn("archive_info", direct_url)

    def test_public_entry_point_runs_from_the_installed_package(self) -> None:
        from retirement_simulation import (  # noqa: PLC0415
            analysis_to_dict,
            analyze_retirement,
        )

        payload = analysis_to_dict(
            analyze_retirement(
                {
                    "basic_info": {"age": 60, "retirement_age": 60, "end_age": 62},
                    "financial_assets": [
                        {
                            "asset_type": "예금/적금",
                            "current_value": 100_000_000,
                            "monthly_savings": 0,
                        }
                    ],
                    "pension_assets": [],
                    "real_estate_assets": [],
                    "liabilities": [],
                    "goal_milestones": [],
                    "goal_periods": [
                        {
                            "phase": "after",
                            "from_age": 60,
                            "to_age": 62,
                            "items": [{"amount": 1_000_000}],
                        }
                    ],
                },
                {
                    "annual_return_mean": 0.0,
                    "annual_return_volatility": 0.0,
                    "annual_inflation_rate": 0.0,
                    "num_paths": 2,
                    "seed": 7,
                },
                confirmed_empty_sections=("pension_assets",),
            )
        )
        self.assertEqual(payload["contract_version"], "retirement-analysis-v1")
        self.assertEqual(payload["mode"], "baseline")
        # BE 는 결과를 엄격한 JSON 으로 인코딩해 응답한다. NaN/Infinity 가 새면 안 된다.
        json.dumps(payload, allow_nan=False, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
