"""Validate a counseling document coverage map; this is not runtime routing or AX evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from core.step_registry import FIXTURE_RELATIVE_PATH, load_step_registry
from rag.evaluation.counseling_candidate import load_candidate
from rag.pipeline.corpus import load_approved_catalog
from rag.pipeline.models import SourceDocument


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ENUMS = {
    "primary_action": ("answer", "clarify", "read_verified_data", "scope_boundary"),
    "knowledge_mode": ("none", "existing", "counseling", "combined"),
    "retrieval_timing": ("now", "after_confirmation", "after_verified_data", "never"),
    "before": ("covered", "partial", "gap", "not_applicable"),
    "improvement": ("reuse", "new_document", "expanded_document", "data_or_tool", "no_retrieval", "outside_scope"),
    "after": ("documented", "data_dependency", "not_needed", "outside_scope"),
}
LIMITATIONS = [
    "문서 대응 범위의 설계 검토이며 실제 상담 품질이나 AX 개선을 측정한 결과가 아니다.",
    "before/after 분류는 작성된 매핑의 판단이다. 이 검사는 과거 Git 문서와의 의미 비교를 자동 수행하지 않는다.",
    "문서와 정확한 H2 절의 존재를 확인한다. 실제 검색 순위, LLM 답변, 데이터 조회, 세션 갱신은 실행하지 않는다.",
    "지식 사용 여부와 데이터 확인 순서는 별개다. documented도 필요한 개인 데이터가 확보되었다는 뜻이 아니다.",
    "후보 승인, 운영 인덱스, 라우팅, 계산 정책은 변경하지 않는다. 단계별 집계는 복수 단계 사례를 중복 포함한다.",
]


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{field} must be a {'string' if allow_empty else 'non-empty string'}")
    return value


def _strings(value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} must be a {'non-empty ' if nonempty else ''}list")
    for item in value:
        _text(item, field)
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return value


def _json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _headings(text: str) -> set[str]:
    """Read level-two headings outside fenced code, preserving exact title text."""
    headings: set[str] = set()
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1]:
                fence = None
            continue
        if fence is None:
            match = re.match(r"^ {0,3}##[ \t]+(.+?)\s*$", line)
            if match:
                headings.add(re.sub(r"[ \t]+#+[ \t]*$", "", match.group(1)))
    return headings


def _documents(documents: Iterable[SourceDocument], label: str) -> dict[str, SourceDocument]:
    result: dict[str, SourceDocument] = {}
    for document in documents:
        source_id = document.metadata.source_id
        if source_id in result:
            raise ValueError(f"duplicate {label} source_id: {source_id}")
        result[source_id] = document
    return result


def _refs(value: Any, field: str, documents: dict[str, SourceDocument]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    seen: set[tuple[str, str]] = set()
    for ref in value:
        if not isinstance(ref, dict) or set(ref) != {"source_id", "section"}:
            raise ValueError(f"{field} entries must contain source_id and section only")
        source_id = _text(ref["source_id"], f"{field}.source_id")
        section = _text(ref["section"], f"{field}.section")
        if source_id not in documents:
            raise ValueError(f"{field} unknown source_id: {source_id}")
        if section not in _headings(documents[source_id].text):
            raise ValueError(f"{field} unknown H2 section: {source_id}: {section}")
        pair = (source_id, section)
        if pair in seen:
            raise ValueError(f"{field} duplicate reference: {pair}")
        seen.add(pair)
    return value


def validate_coverage(
    payload: dict[str, Any],
    *,
    registry: dict[str, Any],
    existing_documents: Iterable[SourceDocument],
    candidate_documents: Iterable[SourceDocument],
    evaluation_ids: set[str],
) -> dict[str, Any]:
    """Validate references and design contracts without retrieval or data access."""
    if payload.get("schema_version") != "counseling-coverage/v1":
        raise ValueError("unsupported counseling coverage schema_version")
    if payload.get("assessment_kind") != "document-coverage-not-AX" or payload.get("runtime_implemented") is not False:
        raise ValueError("coverage must be document-coverage-not-AX with runtime_implemented=false")
    revision = _text(payload.get("baseline_revision"), "baseline_revision")
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise ValueError("baseline_revision must identify a Git revision")
    supported = _strings(payload.get("supported_steps"), "supported_steps", nonempty=True)
    actual_steps = [step["step_id"] for step in registry["steps"]]
    if set(supported) != set(actual_steps):
        raise ValueError("supported_steps must exactly match the current step registry")
    fallback = payload.get("fallback_step")
    if fallback != "general" or fallback not in registry["transport_fallback_step_ids"]:
        raise ValueError("fallback_step must be the registered general fallback")
    notes = _strings(payload.get("interpretation_notes", []), "interpretation_notes")
    existing = _documents(existing_documents, "existing")
    candidates = _documents(candidate_documents, "candidate")
    if existing.keys() & candidates.keys():
        raise ValueError("existing and candidate source_ids must not overlap")
    documents = {**existing, **candidates}
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    seen_ids: set[str] = set()
    used_steps: set[str] = set()
    allowed_steps = set(actual_steps) | {fallback}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        case_id = _text(case.get("id"), "case.id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        for field in ("area", "question", "concern", "reason"):
            _text(case.get(field), f"{case_id}.{field}")
        _text(case.get("gap"), f"{case_id}.gap", allow_empty=True)
        for field, choices in ENUMS.items():
            if case.get(field) not in choices:
                raise ValueError(f"{case_id}.{field} must be one of {choices}")
        step_ids = _strings(case.get("step_ids"), f"{case_id}.step_ids", nonempty=True)
        if set(step_ids) - allowed_steps:
            raise ValueError(f"{case_id} has unregistered step_ids")
        used_steps.update(step_ids)
        for field in ("required_context", "ax_observation", "evaluation_ids"):
            _strings(case.get(field), f"{case_id}.{field}")
        if set(case["evaluation_ids"]) - evaluation_ids:
            raise ValueError(f"{case_id} has unknown evaluation_ids")
        old_refs = _refs(case.get("existing_refs"), f"{case_id}.existing_refs", existing)
        new_refs = _refs(case.get("candidate_refs"), f"{case_id}.candidate_refs", candidates)
        expected = {"none": (False, False), "existing": (True, False), "counseling": (False, True), "combined": (True, True)}
        if (bool(old_refs), bool(new_refs)) != expected[case["knowledge_mode"]]:
            raise ValueError(f"{case_id} knowledge_mode conflicts with document references")
        if (case["knowledge_mode"] == "none") != (case["retrieval_timing"] == "never"):
            raise ValueError(f"{case_id} no-retrieval contract requires none with never")
        if case["before"] in {"partial", "gap"} and not case["gap"].strip():
            raise ValueError(f"{case_id} partial/gap classification requires a gap explanation")
    if used_steps != allowed_steps:
        raise ValueError(f"cases omit registered stages: {sorted(allowed_steps - used_steps)}")

    def counts(items: list[dict[str, Any]], field: str) -> dict[str, int]:
        counter = Counter(item[field] for item in items)
        return {value: counter[value] for value in ENUMS[field]}

    return {
        "schema_version": "counseling-coverage-report/v1",
        "assessment_kind": "document-coverage-not-AX",
        "baseline_revision": revision,
        "validation_passed": True,
        "runtime_implemented": False,
        "retrieval_executed": False,
        "llm_evaluated": False,
        "ax_gain_measured": False,
        "candidate_acceptance": "pending",
        "limitations": LIMITATIONS,
        "interpretation_notes": notes,
        "total_cases": len(cases),
        "supported_steps": actual_steps,
        "fallback_step": fallback,
        "source_counts": {"existing": len(existing), "candidate": len(candidates)},
        "counts": {field: counts(cases, field) for field in ENUMS},
        "by_step": {
            step: {
                "case_count": sum(step in case["step_ids"] for case in cases),
                "knowledge_mode": counts([case for case in cases if step in case["step_ids"]], "knowledge_mode"),
                "retrieval_timing": counts([case for case in cases if step in case["step_ids"]], "retrieval_timing"),
                "after": counts([case for case in cases if step in case["step_ids"]], "after"),
            }
            for step in [*actual_steps, fallback]
        },
        "source_paths": {source_id: doc.metadata.file_path for source_id, doc in documents.items()},
        "cases": cases,
    }


def evaluate_coverage(
    coverage_path: Path | None = None,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    rag_root = repository_root / "ai-pjt/rag"
    path = coverage_path or rag_root / "evaluation/counseling-coverage-v1.json"
    catalog = load_approved_catalog(rag_root / "data/catalog.json", repository_root=repository_root)
    candidate = load_candidate(rag_root / "data/counseling-candidate.json", repository_root=repository_root)
    known_ids: set[str] = set()
    evaluation_paths = sorted((rag_root / "evaluation").glob("*-gold-v*.json"))
    evaluation_paths.append(rag_root / "evaluation/counseling-behavior-cases-v1.json")
    for evaluation_path in evaluation_paths:
        evaluation = _json_object(evaluation_path)
        records = evaluation.get("queries", evaluation.get("cases", []))
        if not isinstance(records, list):
            raise ValueError(f"evaluation records must be a list: {evaluation_path}")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"evaluation record must be an object: {evaluation_path}")
            known_ids.add(_text(record.get("query_id", record.get("id")), "evaluation.id"))
    report = validate_coverage(
        _json_object(path),
        registry=load_step_registry(repository_root / FIXTURE_RELATIVE_PATH),
        existing_documents=catalog.documents,
        candidate_documents=candidate.documents,
        evaluation_ids=known_ids,
    )
    report["coverage_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["approved_catalog_version"] = catalog.catalog_version
    report["candidate_version"] = candidate.version
    report["candidate_manifest_sha256"] = candidate.manifest_sha256
    report["repository_root"] = str(repository_root.resolve())
    return report


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 상담 지식 문서 대응 지도",
        "",
        f"기준 커밋: `{report['baseline_revision']}`. 검증 사례: {report['total_cases']}개.",
        "",
        "문서 대응 범위의 설계 검토다. 실제 AX 개선, 검색 품질, 런타임 구현 완료를 뜻하지 않는다.",
        "",
        *[f"- {note}" for note in report["limitations"]],
        "",
        "## 판정을 읽는 기준",
        "",
        *[f"- {note}" for note in report["interpretation_notes"]],
        "",
        "## 현황 요약",
        "",
        "| 분류 | 값 | 사례 수 |",
        "|---|---|---:|",
    ]
    for field, values in report["counts"].items():
        lines.extend(f"| {field} | {value} | {count} |" for value, count in values.items())
    lines.extend(["", "## 단계별 현황", "", "| 단계 | 사례 수 | 문서 설명 마련 | 데이터 의존 |", "|---|---:|---:|---:|"])
    for step, values in report["by_step"].items():
        lines.append(f"| {_cell(step)} | {values['case_count']} | {values['after']['documented']} | {values['after']['data_dependency']} |")
    lines.extend([
        "", "## 사례 요약", "",
        "| ID | 단계 | 질문 | 지식·검색 시점 | 이전 → 이후 |",
        "|---|---|---|---|---|",
    ])
    for case in report["cases"]:
        cells = [
            case["id"], ", ".join(case["step_ids"]), case["question"],
            f"{case['knowledge_mode']} / {case['retrieval_timing']}",
            f"{case['before']} → {case['after']}",
        ]
        lines.append("| " + " | ".join(_cell(value) for value in cells) + " |")
    lines.extend(["", "## 사례별 검토", ""])
    for case in report["cases"]:
        lines.extend([
            f"### {_cell(case['id'])} — {_cell(case['area'])}", "",
            f"**질문:** {_cell(case['question'])}", "",
            f"**고민:** {_cell(case['concern'])}", "",
            f"**먼저 할 일:** `{case['primary_action']}`", "",
            f"**판단 이유:** {_cell(case['reason'])}", "",
            f"**보강 방식:** `{case['improvement']}`", "",
            f"**부족했던 설명:** {_cell(case['gap']) if case['gap'] else '별도 부족 항목 없음.'}", "",
            "**확인할 맥락**", "",
            *([f"- {_cell(item)}" for item in case["required_context"]] or ["- 없음"]),
        ])
        lines.extend(["", "**참조 문서와 정확한 절**", ""])
        if not case["existing_refs"] and not case["candidate_refs"]:
            lines.append("- 검색 문서 없음.")
        for label, refs in (("기존", case["existing_refs"]), ("후보", case["candidate_refs"])):
            for ref in refs:
                source_id = ref["source_id"]
                source_path = report["source_paths"][source_id]
                if report.get("repository_root"):
                    target = (Path(report["repository_root"]) / source_path).resolve()
                    source = f"[{source_id}](<{target}>)"
                else:
                    source = f"{source_id} (`{source_path}`)"
                lines.append(f"- {label}: {source} — {_cell(ref['section'])}")
        lines.extend([
            "", "**후속 AX 평가에서 관찰할 행동**", "",
            *([f"- {_cell(item)}" for item in case["ax_observation"]] or ["- 없음"]),
        ])
        evaluation_ids = ", ".join(f"`{item}`" for item in case["evaluation_ids"]) or "연결된 평가 ID 없음."
        lines.extend(["", f"**평가 ID:** {evaluation_ids}", ""])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate_coverage(args.coverage, repository_root=args.repository_root)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        for path, content in ((args.report, rendered), (args.markdown, render_markdown(report))):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        print(rendered, end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"counseling coverage validation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
