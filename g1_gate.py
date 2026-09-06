"""Deterministic foundation checks for the G1 integration gate."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPOSITORY_ROOT / "contract-fixtures/g1/foundation-inputs.json"
EXPECTED_INPUTS = {"D1-G0", *(f"P{number:02d}" for number in range(1, 15))}


class GateFailure(RuntimeError):
    """Raised when a G1 foundation invariant is not satisfied."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateFailure(f"cannot load JSON fixture: {path.relative_to(REPOSITORY_ROOT)}") from exc


def _require_ancestor(commit: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise GateFailure(f"required input commit is not an ancestor of HEAD: {commit}")


def check_manifest() -> list[str]:
    manifest = _load_json(MANIFEST_PATH)
    if not isinstance(manifest, dict):
        raise GateFailure("foundation manifest root must be an object")
    if manifest.get("fixture_kind") != "g1-foundation-inputs":
        raise GateFailure("foundation manifest fixture_kind is unsupported")
    if manifest.get("gate") != "G1":
        raise GateFailure("foundation manifest gate must be G1")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise GateFailure("foundation manifest inputs must be an array")
    input_ids = [item.get("id") for item in inputs if isinstance(item, dict)]
    if len(input_ids) != len(inputs) or set(input_ids) != EXPECTED_INPUTS:
        raise GateFailure("foundation manifest must contain D1-G0 and P01 through P14 exactly once")
    if len(input_ids) != len(set(input_ids)):
        raise GateFailure("foundation manifest contains duplicate input IDs")

    checked_commits: list[str] = []
    for item in inputs:
        commits = item.get("commits")
        required_paths = item.get("required_paths")
        if not isinstance(commits, list) or not commits:
            raise GateFailure(f"{item['id']} must declare at least one commit")
        if not isinstance(required_paths, list) or not required_paths:
            raise GateFailure(f"{item['id']} must declare at least one required path")
        for commit in commits:
            if not isinstance(commit, str) or len(commit) != 40:
                raise GateFailure(f"{item['id']} contains an invalid commit ID")
            _require_ancestor(commit)
            checked_commits.append(commit)
        for relative_path in required_paths:
            path = REPOSITORY_ROOT / relative_path
            if not path.exists():
                raise GateFailure(f"{item['id']} required path is missing: {relative_path}")
    return checked_commits


def check_rag_catalog() -> int:
    catalog = _load_json(REPOSITORY_ROOT / "ai-pjt/rag/data/catalog.json")
    documents = catalog.get("documents") if isinstance(catalog, dict) else None
    if not isinstance(documents, list) or not documents:
        raise GateFailure("RAG catalog must contain documents")

    for document in documents:
        relative_path = document.get("path")
        expected_hash = document.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            raise GateFailure("RAG catalog document metadata is incomplete")
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            raise GateFailure(f"RAG catalog file is missing: {relative_path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise GateFailure(f"RAG catalog hash mismatch: {relative_path}")
    return len(documents)


def check_research_boundary() -> None:
    research_root = REPOSITORY_ROOT / "ai-pjt/experiments/asset-allocation"
    product_code = [
        path.relative_to(REPOSITORY_ROOT)
        for path in research_root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".js", ".ts", ".vue"}
    ]
    if product_code:
        raise GateFailure(
            "P12 diagnosis-only boundary contains executable product code: "
            + ", ".join(map(str, product_code))
        )


def main() -> int:
    try:
        commits = check_manifest()
        document_count = check_rag_catalog()
        check_research_boundary()
    except GateFailure as exc:
        print(f"G1 foundation: FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "G1 foundation: PASS "
        f"({len(EXPECTED_INPUTS)} inputs, {len(commits)} commits, "
        f"{document_count} governed RAG documents)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
