from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        compose,
    )
    if match is None:
        raise AssertionError(f"compose service is missing: {service}")
    return match.group(0)


class RAGBootstrapDeploymentContractTests(unittest.TestCase):
    def test_compose_bootstraps_before_ai_and_never_restarts_one_shot(self) -> None:
        compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        bootstrap = _service_block(compose, "rag-bootstrap")
        ai = _service_block(compose, "ai-pjt")

        self.assertIn('command: ["python", "-m", "rag.bootstrap"]', bootstrap)
        self.assertIn('restart: "no"', bootstrap)
        self.assertIn('DATABASE_URL: "${DATABASE_URL:?DATABASE_URL is required}"', bootstrap)
        self.assertIn("rag-bootstrap:\n        condition: service_completed_successfully", ai)

    def test_ci_validates_artifact_and_deploy_tracks_bootstrap_image(self) -> None:
        pipeline = (REPOSITORY_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        deploy = (REPOSITORY_ROOT / "deploy/deploy.sh").read_text(encoding="utf-8")

        self.assertIn("python -m rag.bootstrap --check-only", pipeline)
        self.assertIn(
            "BUILT_SERVICES=(web backend ai-pjt migrate rag-bootstrap)",
            deploy,
        )
        self.assertIn('rag_ok() { [ "$1" = "exited:0" ]; }', deploy)


if __name__ == "__main__":
    unittest.main()
