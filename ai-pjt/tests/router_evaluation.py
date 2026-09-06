from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Mapping

AI_ROOT = Path(__file__).parents[1]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from core.step_registry import load_step_candidates


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "router-evaluation-v1.json"
REPOSITORY_ROOT = Path(__file__).parents[2]
STEP_REGISTRY_PATH = (
    REPOSITORY_ROOT
    / "contract-fixtures"
    / "ai-lifecycle"
    / "v2"
    / "step-registry.json"
)
FRONTEND_STEPS_PATH = (
    REPOSITORY_ROOT
    / "front-pjt"
    / "src"
    / "views"
    / "ai"
    / "data"
    / "processSteps.js"
)
FRONTEND_STEP_ID = re.compile(r"\{ id: '([^']+)'")


def load_evaluation_fixture(path: Path = FIXTURE_PATH) -> dict:
    with path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def load_contract_step_candidates(
    path: Path = STEP_REGISTRY_PATH,
) -> dict[str, list[str]]:
    return load_step_candidates(path)


def load_frontend_step_ids(path: Path = FRONTEND_STEPS_PATH) -> list[str]:
    step_ids = FRONTEND_STEP_ID.findall(path.read_text(encoding="utf-8"))
    if not step_ids:
        raise ValueError(f"Frontend step ID를 읽지 못했습니다: {path}")
    return step_ids


def route_accuracy(
    cases: list[dict],
    predictions: Mapping[str, str],
) -> float:
    if not cases:
        raise ValueError("라우팅 평가 입력은 한 건 이상이어야 합니다.")

    missing = [case["id"] for case in cases if case["id"] not in predictions]
    if missing:
        raise ValueError(f"예측이 누락된 case: {', '.join(missing)}")

    correct = sum(
        predictions[case["id"]] == case["expected"]["agent"]
        for case in cases
    )
    return correct / len(cases)


def passes_accuracy_gate(
    cases: list[dict],
    predictions: Mapping[str, str],
    threshold: float,
) -> bool:
    return route_accuracy(cases, predictions) >= threshold
