from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "router-evaluation-v1.json"
REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "docs" / "contracts" / "ai-api.md"
FRONTEND_STEPS_PATH = (
    REPOSITORY_ROOT
    / "front-pjt"
    / "src"
    / "views"
    / "ai"
    / "data"
    / "processSteps.js"
)
STEP_TABLE_ROW = re.compile(r"^\| `([^`]+)` \| (.+) \|$")
BACKTICK_VALUE = re.compile(r"`([^`]+)`")
FRONTEND_STEP_ID = re.compile(r"\{ id: '([^']+)'")


def load_evaluation_fixture(path: Path = FIXTURE_PATH) -> dict:
    with path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def load_contract_step_candidates(path: Path = CONTRACT_PATH) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    in_step_registry = False

    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "## step_id 레지스트리":
            in_step_registry = True
            continue
        if in_step_registry and line.startswith("## "):
            break
        if not in_step_registry:
            continue

        match = STEP_TABLE_ROW.match(line)
        if match:
            step_id, agents_cell = match.groups()
            candidates[step_id] = BACKTICK_VALUE.findall(agents_cell)

    if not candidates:
        raise ValueError(f"step_id 레지스트리를 읽지 못했습니다: {path}")
    return candidates


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
