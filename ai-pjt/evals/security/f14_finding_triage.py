"""Triage F14 attack findings into deduplicated, file-owned remediation tasks."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = AI_ROOT / "evals" / "artifacts" / "f14-integrated-mock-attack.json"
TASK_ID_PATTERN = re.compile(r"^F14-T(?:[2-9]\d)$")
DISPOSITIONS = {"remediate", "accepted_risk", "false_positive"}
SCHEMA_VERSION = "f14-finding-triage-v1"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source_file:
        payload = json.load(source_file)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_source(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != "f14-attack-assessment-v1":
        errors.append("source schema_version must be 'f14-attack-assessment-v1'")
    if report.get("suite_id") != "AI-F14-T01":
        errors.append("source suite_id must be 'AI-F14-T01'")
    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append("source findings must be an array")
        return errors
    for index, finding in enumerate(findings):
        prefix = f"findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in ("finding_id", "title", "severity", "impact_boundary"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")
        reproduction = finding.get("reproduction")
        if not isinstance(reproduction, dict) or not reproduction.get("command"):
            errors.append(f"{prefix}.reproduction.command is required")
        if not isinstance(finding.get("expected"), dict):
            errors.append(f"{prefix}.expected must be an object")
        if not isinstance(finding.get("actual"), dict):
            errors.append(f"{prefix}.actual must be an object")
        owner_paths = finding.get("owner_paths")
        if (
            not isinstance(owner_paths, list)
            or not owner_paths
            or not all(isinstance(path, str) and path for path in owner_paths)
        ):
            errors.append(f"{prefix}.owner_paths must contain repository paths")
    return errors


def _deduplication_key(finding: dict[str, Any]) -> str:
    material = {
        "category": finding.get("category"),
        "actual": finding.get("actual"),
        "impact_boundary": finding.get("impact_boundary"),
        "owner_paths": sorted(finding.get("owner_paths", [])),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_triage(
    report: dict[str, Any],
    *,
    source_path: Path,
    decisions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decision_map = decisions or {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for finding in report["findings"]:
        groups.setdefault(_deduplication_key(finding), []).append(finding)

    triage: list[dict[str, Any]] = []
    dynamic_tasks: list[dict[str, Any]] = []
    for offset, (dedupe_key, grouped) in enumerate(sorted(groups.items())):
        primary = grouped[0]
        configured = decision_map.get(primary["finding_id"], {})
        disposition = configured.get("disposition", "remediate")
        if disposition not in DISPOSITIONS:
            raise ValueError(
                f"unsupported disposition for {primary['finding_id']}: {disposition!r}"
            )
        rationale = configured.get("rationale")
        if disposition != "remediate" and (
            not isinstance(rationale, str) or not rationale.strip()
        ):
            raise ValueError(
                f"{disposition} requires rationale for {primary['finding_id']}"
            )
        entry: dict[str, Any] = {
            "deduplication_key": dedupe_key,
            "primary_finding_id": primary["finding_id"],
            "duplicate_finding_ids": [
                finding["finding_id"] for finding in grouped[1:]
            ],
            "disposition": disposition,
            "rationale": rationale,
            "severity": primary["severity"],
            "impact_boundary": primary["impact_boundary"],
            "owner_paths": sorted(set(primary["owner_paths"])),
            "reproduction": primary["reproduction"],
        }
        if disposition == "remediate":
            task_id = f"F14-T{20 + offset:02d}"
            lane_id = f"W8-L{offset + 1:02d}"
            entry["task_id"] = task_id
            entry["lane_id"] = lane_id
            dynamic_tasks.append({
                "task_id": task_id,
                "lane_id": lane_id,
                "start_gate": f"M8-L{offset + 1:02d}",
                "finding_id": primary["finding_id"],
                "owner_paths": entry["owner_paths"],
                "reproduction": primary["reproduction"],
                "completion": "재현 테스트를 보존한 보완과 targeted regression PASS",
            })
        triage.append(entry)

    source_bytes = source_path.read_bytes()
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": "AI-F14-T02",
        "source": {
            "artifact": str(source_path.relative_to(AI_ROOT.parent)),
            "sha256": sha256(source_bytes).hexdigest(),
            "summary": report["summary"],
        },
        "deduplication": {
            "key_fields": ["category", "actual", "impact_boundary", "owner_paths"],
            "input_findings": len(report["findings"]),
            "unique_findings": len(groups),
        },
        "decision": "no_findings" if not groups else "triaged",
        "triage": triage,
        "dynamic_tasks": dynamic_tasks,
    }


def validate_triage(triage: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if triage.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if triage.get("suite_id") != "AI-F14-T02":
        errors.append("suite_id must be 'AI-F14-T02'")
    tasks = triage.get("dynamic_tasks")
    if not isinstance(tasks, list):
        errors.append("dynamic_tasks must be an array")
        return errors
    seen_paths: set[str] = set()
    for index, task in enumerate(tasks):
        prefix = f"dynamic_tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if TASK_ID_PATTERN.fullmatch(str(task.get("task_id", ""))) is None:
            errors.append(f"{prefix}.task_id must be in F14-T20~F14-T99")
        owner_paths = set(task.get("owner_paths", []))
        overlap = seen_paths & owner_paths
        if overlap:
            errors.append(f"{prefix}.owner_paths overlap earlier task: {sorted(overlap)}")
        seen_paths.update(owner_paths)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = _load_json(args.source)
    errors = validate_source(report)
    if errors:
        for error in errors:
            print(f"INVALID: {error}")
        return 2
    decisions = _load_json(args.decisions) if args.decisions else None
    triage = build_triage(report, source_path=args.source, decisions=decisions)
    errors = validate_triage(triage)
    if errors:
        for error in errors:
            print(f"INVALID: {error}")
        return 2
    rendered = json.dumps(triage, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        print(
            f"triage: decision={triage['decision']} "
            f"findings={triage['deduplication']['input_findings']} "
            f"dynamic_tasks={len(triage['dynamic_tasks'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
