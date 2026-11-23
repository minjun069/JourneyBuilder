"""Validate a source-informed review design; never execute or score an assistant.

The registry describes published frameworks, while criteria are this project's
evidence checks. A valid mapping is neither an observed result nor certification.
Only the explicitly requested report/template files are written by the CLI.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rag.evaluation.counseling_coverage import evaluate_coverage
from evals.counseling_diversity import validate_diversity


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = Path("ai-pjt/evals/data/counseling-frameworks-v1.json")
PLAN_PATH = Path("ai-pjt/evals/data/counseling-case-plan-v1.json")
COVERAGE_PATH = Path("ai-pjt/rag/evaluation/counseling-coverage-v1.json")
BEHAVIOR_PATH = Path("ai-pjt/rag/evaluation/counseling-behavior-cases-v1.json")
FRAMEWORKS = {
    "kr-fpsb-six-step": ("process", "kr-process-2021"),
    "cfpboard-seven-step": (None, "cfpboard-process"),
    "cfpboard-communication-technology": ("explanation", "cfpboard-duties"),
    "miti-4.2.1": ("conversation", "miti-421"),
    "kr-client-satisfaction": ("customer-experience", "kr-client-checklist"),
    "cfpb-financial-wellbeing": ("financial-wellbeing", "cfpb-wellbeing-scale"),
}
CRITERIA = {
    "kr-1-scope": ("kr-fpsb-six-step", 1),
    "kr-2-goals": ("kr-fpsb-six-step", 2),
    "kr-2-data": ("kr-fpsb-six-step", 2),
    "kr-3-analysis": ("kr-fpsb-six-step", 3),
    **{key: ("cfpboard-communication-technology", None) for key in (
        "a11-accuracy", "a11-scope", "a11-response", "a11-clarity",
        "a14-care", "a14-assumptions", "a14-reliability",
    )},
    "custom-partnership": ("miti-4.2.1", None),
    "custom-empathy": ("miti-4.2.1", None),
}
VARIANTS = (
    "control-current-response", "baseline-aggregation-rag",
    "aggregation-rag-with-understanding", "generation-stage-rag",
)
EVALUATION_STATES = ["not-observed", "supported", "concern", "not-applicable"]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _object(value: Any, field: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{field} must be nonempty text")
    return value


def _keys(value: dict[str, Any], required: str, field: str, optional: str = "") -> None:
    required_keys, optional_keys = set(required.split()), set(optional.split())
    _require(required_keys <= value.keys(), f"{field} missing fields: {sorted(required_keys - value.keys())}")
    _require(value.keys() <= required_keys | optional_keys,
             f"{field} unknown fields: {sorted(value.keys() - required_keys - optional_keys)}")


def _strings(value: Any, field: str, *, nonempty: bool = True) -> list[str]:
    _require(isinstance(value, list), f"{field} must be a list")
    if nonempty:
        _require(bool(value), f"{field} must not be empty")
    for item in value:
        _text(item, field)
    _require(len(set(value)) == len(value), f"{field} must not repeat values")
    return value


def _ints(value: Any, field: str) -> list[int]:
    _require(isinstance(value, list) and all(type(item) is int for item in value),
             f"{field} must be a list of integers")
    _require(len(set(value)) == len(value), f"{field} must not repeat values")
    return value


def _indexed(value: Any, field: str) -> dict[str, dict[str, Any]]:
    _require(isinstance(value, list) and bool(value), f"{field} must be a nonempty list")
    result = {}
    for item in value:
        record = _object(item, field)
        identity = _text(record.get("id"), f"{field}.id")
        _require(identity not in result, f"{field} duplicate id: {identity}")
        result[identity] = record
    return result


def _date(value: Any, field: str) -> None:
    value = _text(value, field)
    try:
        _require(date.fromisoformat(value).isoformat() == value, f"{field} must be YYYY-MM-DD")
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def _url(value: Any, field: str) -> None:
    parsed = urlparse(_text(value, field))
    _require(parsed.scheme == "https" and bool(parsed.netloc), f"{field} must be an HTTPS URL")


def _unexecuted(value: Any, field: str) -> None:
    """Reject result/score claims even when hidden inside optional metadata."""
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{field}.{key}"
            _require(key not in {"total_score", "combined_score", "overall_score", "miti_score",
                                 "cfpb_score", "quality_passed", "certification_passed"},
                     f"{path}: scores or quality claims are outside this design")
            if key in {"llm_evaluated", "ax_gain_measured", "official_certification",
                       "official_scoring_in_this_project", "official_scores_implemented",
                       "official_or_combined_score", "runtime_implemented"}:
                _require(child is False, f"{path} must be false in this design")
            if key in {"measured_outcomes", "official_scores", "actual_response", "execution", "reviewer",
                       "actual_understanding_before", "actual_understanding_after"}:
                _require(child is None, f"{path} must be null without execution")
            _unexecuted(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _unexecuted(child, f"{field}[{index}]")


def validate_registry(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate the fixed v1 framework identities and their project boundaries."""
    _object(registry, "registry")
    _keys(registry, "schema_version as_of_date status no_total_score official_certification sources axes "
          "frameworks criteria scope comparison corrections_to_supplied_material evaluation_states measured_outcomes", "registry")
    _require(registry["schema_version"] == "counseling-frameworks/v1", "unsupported framework schema_version")
    _require(registry["status"] == "design-only", "registry must remain design-only")
    _require(registry["no_total_score"] is True, "no_total_score must be true")
    _require(registry["official_certification"] is False, "official_certification must be false")
    _require(registry["measured_outcomes"] is None, "measured_outcomes must be null")
    _date(registry["as_of_date"], "registry.as_of_date")
    _unexecuted(registry, "registry")
    _require(registry["evaluation_states"] == EVALUATION_STATES, "evaluation_states must preserve missing evidence and N/A")
    _strings(registry["corrections_to_supplied_material"], "registry.corrections_to_supplied_material")
    sources = _indexed(registry["sources"], "sources")
    axes = _indexed(registry["axes"], "axes")
    frameworks = _indexed(registry["frameworks"], "frameworks")
    criteria = _indexed(registry["criteria"], "criteria")
    _require(set(sources) == {source for _, source in FRAMEWORKS.values()}, "source identities do not match v1")
    _require(set(axes) == {axis for axis, _ in FRAMEWORKS.values() if axis}, "axis identities do not match v1")
    _require(set(frameworks) == set(FRAMEWORKS), "framework identities do not match v1")
    _require(set(criteria) == set(CRITERIA), "criterion identities do not match v1")
    for source_id, source in sources.items():
        field = f"sources.{source_id}"
        _keys(source, "id publisher title url published_at locator verification rights rights_url verified_at usage raw_content_ingested",
              field, "original_url")
        for key in ("publisher", "title", "locator", "verification", "rights"):
            _text(source[key], f"{field}.{key}")
        for key in ("url", "rights_url", "original_url"):
            if key in source:
                _url(source[key], f"{field}.{key}")
        _date(source["verified_at"], f"{field}.verified_at")
        if source["published_at"] is not None:
            _date(source["published_at"], f"{field}.published_at")
        _require(source["usage"] == "reference-only" and source["raw_content_ingested"] is False,
                 f"{field}: reference-only sources must not ingest raw content")
    for axis_id, axis in axes.items():
        _keys(axis, "id name framework_ids", f"axes.{axis_id}")
        _text(axis["name"], f"axes.{axis_id}.name")
        actual = _strings(axis["framework_ids"], f"axes.{axis_id}.framework_ids")
        _require(set(actual) == {key for key, (axis, _) in FRAMEWORKS.items() if axis == axis_id},
                 f"axes.{axis_id}: framework links mismatch")
    for framework_id, framework in frameworks.items():
        field = f"frameworks.{framework_id}"
        _keys(framework, "id axis_id source_ids name measurement_unit evaluator applicability limitations structure official_scoring_in_this_project", field)
        expected_axis, expected_source = FRAMEWORKS[framework_id]
        _require(framework["axis_id"] == expected_axis, f"{field}: axis mismatch")
        _require(_strings(framework["source_ids"], f"{field}.source_ids") == [expected_source], f"{field}: source links mismatch")
        for key in ("name", "measurement_unit", "evaluator", "applicability"):
            _text(framework[key], f"{field}.{key}")
        _strings(framework["limitations"], f"{field}.limitations")
        _object(framework["structure"], f"{field}.structure")
    _require(frameworks["cfpboard-seven-step"]["applicability"] == "reference-only", "seven-step framework is reference-only")
    _validate_structures(frameworks)
    for criterion_id, criterion in criteria.items():
        field = f"criteria.{criterion_id}"
        _keys(criterion, "id axis_id framework_id source_locator kind project_check required_evidence process_stage", field)
        framework_id, stage = CRITERIA[criterion_id]
        _require(criterion["framework_id"] == framework_id and criterion["axis_id"] == FRAMEWORKS[framework_id][0],
                 f"{field}: framework/axis mismatch")
        _require(criterion["process_stage"] == stage and type(criterion["process_stage"]) is type(stage),
                 f"{field}: process stage mismatch")
        _require(criterion["kind"] == "project-evidence-check", f"{field}: official criteria or scores are not implemented")
        _text(criterion["source_locator"], f"{field}.source_locator")
        _text(criterion["project_check"], f"{field}.project_check")
        _strings(criterion["required_evidence"], f"{field}.required_evidence")
    scope = _object(registry["scope"], "scope")
    _keys(scope, "included_process_stages excluded_process_stages notes", "scope")
    _require(_ints(scope["included_process_stages"], "scope.included_process_stages") == [1, 2, 3], "included process stages must be 1..3")
    _require(_ints(scope["excluded_process_stages"], "scope.excluded_process_stages") == [4, 5, 6], "excluded process stages must be 4..6")
    _strings(scope["notes"], "scope.notes")
    comparison = _object(registry["comparison"], "comparison")
    _keys(comparison, "baseline_variant_id variant_ids hold_constant procedure official_or_combined_score", "comparison")
    _require(comparison["variant_ids"] == list(VARIANTS), "comparison variants must preserve the four distinct variants")
    _require(comparison["baseline_variant_id"] == "baseline-aggregation-rag", "baseline must be aggregation RAG")
    _strings(comparison["hold_constant"], "comparison.hold_constant")
    _strings(comparison["procedure"], "comparison.procedure")
    return criteria


def _validate_structures(frameworks: dict[str, dict[str, Any]]) -> None:
    structures = {key: value["structure"] for key, value in frameworks.items()}
    expected = {
        "cfpboard-seven-step": {"stage_count": 7},
        "cfpboard-communication-technology": {"standards": ["A.11", "A.14(a)", "A.14(b)", "A.14(c)"]},
        "miti-4.2.1": {"global_rating_range": [1, 5], "global_dimensions": ["Cultivating Change Talk", "Softening Sustain Talk", "Partnership", "Empathy"], "official_scores_implemented": False},
        "kr-client-satisfaction": {"verified_item_count": 13, "primary_item_numbers": [1, 2, 3, 6, 7, 8, 11, 13], "conditional_item_numbers": [4, 5, 9, 10, 12]},
        "cfpb-financial-wellbeing": {"standard_item_count": 10, "official_score_range": [0, 100], "official_scores_implemented": False},
    }
    for key, structure in expected.items():
        _require(structures[key] == structure, f"frameworks.{key}.structure does not match source structure metadata")
    process = structures["kr-fpsb-six-step"]
    _keys(process, "stages", "process.structure")
    stages = process["stages"]
    _require(isinstance(stages, list) and len(stages) == 6, "process.structure requires six stages")
    for expected_id, stage in enumerate(stages, 1):
        _keys(_object(stage, "stage"), "id name scope", "stage")
        _require(type(stage["id"]) is int and stage["id"] == expected_id, "process stage identity mismatch")
        _text(stage["name"], "stage.name")
        _require(stage["scope"] == ("included" if expected_id <= 3 else "out-of-scope"), "process stage scope mismatch")


def validate_protocol(
    registry: dict[str, Any], case_plan: dict[str, Any],
    coverage: dict[str, Any], behavior: dict[str, Any],
) -> dict[str, Any]:
    """Check mappings and produce design metadata, without judging any response."""
    criteria = validate_registry(registry)
    _object(case_plan, "case_plan")
    _keys(case_plan, "schema_version as_of_date status notes plans", "case_plan")
    _require(case_plan["schema_version"] == "counseling-case-plan/v1", "unsupported case-plan schema_version")
    _require(case_plan["status"] == "design-only", "case plan must remain design-only")
    _date(case_plan["as_of_date"], "case_plan.as_of_date")
    _strings(case_plan["notes"], "case_plan.notes")
    for name, payload in (("case_plan", case_plan), ("coverage", coverage), ("behavior", behavior)):
        _object(payload, name)
        _unexecuted(payload, name)
    _require(coverage.get("schema_version") == "counseling-coverage/v1", "unsupported coverage schema_version")
    _require(coverage.get("assessment_kind") == "document-coverage-not-AX" and coverage.get("runtime_implemented") is False,
             "coverage must remain document coverage without runtime implementation")
    _require(behavior.get("schema_version") == "counseling-behavior-cases/v1", "unsupported behavior schema_version")
    _require(behavior.get("status") == "not-executed" and behavior.get("data_classification") == "synthetic-only",
             "behavior must remain not-executed and synthetic-only")
    behavior_variants = _indexed(behavior.get("variants"), "behavior.variants")
    _require(list(behavior_variants) == list(VARIANTS), "behavior variants do not match registry")
    for variant in behavior_variants.values():
        _text(variant.get("description"), "variant.description")
    checks = _object(behavior.get("project_checks"), "behavior.project_checks")
    _require(checks.get("kind") == "project-regression-checks-not-professional-scale" and checks.get("aggregation") == "none",
             "behavior checks must remain unaggregated project checks")
    _require(checks.get("framework_contract") == REGISTRY_PATH.as_posix() and checks.get("case_plan") == PLAN_PATH.as_posix(),
             "behavior checks must link the current framework and case plan")
    records = {"coverage": _indexed(coverage.get("cases"), "coverage.cases"),
               "behavior": _indexed(behavior.get("cases"), "behavior.cases")}
    expected = {(kind, identity) for kind, cases in records.items() for identity in cases}
    diversity = validate_diversity(coverage, behavior)
    example = next(case for case in records["behavior"].values()
                   if case.get("variation", {}).get("axis") == "understanding-revision")
    plans = case_plan["plans"]
    _require(isinstance(plans, list) and bool(plans), "case_plan.plans must be a nonempty list")
    seen: set[tuple[str, str]] = set()
    summaries = []
    axis_counts = {axis["id"]: {kind: Counter() for kind in records} for axis in registry["axes"]}
    for index, plan in enumerate(plans):
        field = f"plans[{index}]"
        _keys(_object(plan, field), "kind case_id process_stage_ids criterion_ids axis_status formal_miti reason needed_evidence", field)
        kind, identity = _text(plan["kind"], f"{field}.kind"), _text(plan["case_id"], f"{field}.case_id")
        key = (kind, identity)
        _require(key in expected, f"{field}: unknown case {key}")
        _require(key not in seen, f"{field}: duplicate case mapping {key}")
        seen.add(key)
        selected = _strings(plan["criterion_ids"], f"{field}.criterion_ids")
        _require(set(selected) <= criteria.keys(), f"{field}: unknown criterion reference")
        stages = _ints(plan["process_stage_ids"], f"{field}.process_stage_ids")
        expected_stages = {criteria[identity]["process_stage"] for identity in selected} - {None}
        _require(set(stages) <= {1, 2, 3} and set(stages) == expected_stages,
                 f"{field}: process stages must match selected criteria in stages 1..3")
        selected_axes = {criteria[identity]["axis_id"] for identity in selected}
        _require("explanation" in selected_axes, f"{field}: explanation review requires an explanation criterion")
        statuses = _object(plan["axis_status"], f"{field}.axis_status")
        _require(statuses == {
            "process": "eligible-fragment-review" if stages else "not_applicable",
            "explanation": "eligible-response-review",
            "conversation": "project-observation-only" if "conversation" in selected_axes else "not_applicable",
            "customer-experience": "requires-client-response",
            "financial-wellbeing": "requires-valid-instrument",
        }, f"{field}: axis status must preserve applicability and client/instrument dependencies")
        miti = _object(plan["formal_miti"], f"{field}.formal_miti")
        _keys(miti, "status reason", f"{field}.formal_miti")
        _require(miti["status"] == "not_applicable", f"{field}: formal MITI is not applicable to these synthetic fragments")
        _text(miti["reason"], f"{field}.formal_miti.reason")
        _text(plan["reason"], f"{field}.reason")
        _strings(plan["needed_evidence"], f"{field}.needed_evidence")
        for axis_id, status in statuses.items():
            axis_counts[axis_id][kind][status] += 1
        source_case = records[kind][key[1]]
        summary = {**plan, "label": source_case.get("question") if kind == "coverage" else source_case.get("category")}
        if kind == "behavior":
            checkpoints = {item["turn_id"]: item for item in source_case.get("checkpoints", [])}
            summary["turn_review_plan"] = []
            for turn_index, turn in enumerate(source_case["turns"], 1):
                if turn["role"] != "user":
                    continue
                turn_id = turn.get("turn_id", turn_index)
                checkpoint = checkpoints.get(turn_id)
                summary["turn_review_plan"].append({
                    "turn_id": turn_id,
                    "checkpoint_ref": f"{identity}/checkpoints/{turn_id}" if checkpoint else None,
                    "expected_action": checkpoint["understanding_action"] if checkpoint else None,
                    "carry_forward_from": checkpoint["carry_forward_from"] if checkpoint else None,
                })
        summaries.append(summary)
    _require(seen == expected, f"missing case mappings: {sorted(expected - seen)}")
    return {
        "schema_version": "counseling-protocol-report/v1",
        "status": "design-validated-not-executed", "design_validation_passed": True,
        "validation_meaning": "Schema, references and applicability only; not response quality, professional compliance or certification.",
        "llm_evaluated": False, "ax_gain_measured": False, "official_scores": None,
        "measured_outcomes": None, "no_total_score": True, "official_certification": False,
        "as_of_date": registry["as_of_date"],
        "units": {"coverage": "document mapping scenario", "behavior": "synthetic conversation scenario"},
        "case_counts": {kind: len(cases) for kind, cases in records.items()},
        "variant_ids": list(behavior_variants),
        "review_template_slots": len(records["behavior"]) * len(behavior_variants),
        "diversity": diversity,
        "multiturn_design_example": {
            "case_id": example["id"], "kind": "synthetic-expectation-not-observed",
            "turns": example["turns"], "checkpoints": example["checkpoints"],
        },
        "axis_applicability_counts": axis_counts,
        "sources": registry["sources"], "axes": registry["axes"], "frameworks": registry["frameworks"],
        "criteria": registry["criteria"], "scope": registry["scope"], "comparison": registry["comparison"],
        "corrections_to_supplied_material": registry["corrections_to_supplied_material"],
        "plan_notes": case_plan["notes"], "cases": summaries,
    }


def build_review_template(report: dict[str, Any]) -> dict[str, Any]:
    """Generate empty human-review slots, never an answer or an inferred rating."""
    return {
        "schema_version": "counseling-review-template/v1", "status": "not-executed",
        "purpose": "Manual follow-up only; this command does not import, score or aggregate results.",
        "llm_evaluated": False, "ax_gain_measured": False, "official_scores": None,
        "no_total_score": True, "official_certification": False,
        "evaluation_states": EVALUATION_STATES,
        "evaluation_unit": "conversation-variant; turns are dependent observations, not additional independent samples",
        "reviews": [{
            "behavior_case_id": case["case_id"], "variant_id": variant,
            "actual_response": None, "execution": None, "reviewer": None,
            "needed_evidence": case["needed_evidence"],
            "criteria": [{"criterion_id": identity, "status": "not-observed", "evidence": [], "reason": None}
                         for identity in case["criterion_ids"]],
            "customer_experience": None, "financial_wellbeing": None,
            "formal_miti": dict(case["formal_miti"]),
            "turn_reviews": [_empty_turn_review(turn, variant) for turn in case["turn_review_plan"]],
        } for case in report["cases"] if case["kind"] == "behavior" for variant in report["variant_ids"]],
    }


def _empty_turn_review(turn: dict[str, Any], variant: str) -> dict[str, Any]:
    has_understanding = variant == "aggregation-rag-with-understanding"
    return {
        **turn,
        "actual_response": None, "execution": None,
        "actual_understanding_before": None, "actual_understanding_after": None,
        "script_compatibility": {"status": "not-observed", "evidence": [], "reason": None},
        "response_observation": {"status": "not-observed", "evidence": [], "reason": None},
        "understanding_observation": {
            "status": "not-observed" if has_understanding else "not-applicable",
            "evidence": [],
            "reason": None if has_understanding else "이 변형에는 별도의 고객 이해 문단이 없다. 응답의 정정 반영은 별도로 관찰한다.",
        },
    }


def _load(path: Path) -> dict[str, Any]:
    return _object(json.loads(path.read_text(encoding="utf-8")), str(path))


def evaluate_protocol(*, repository_root: Path = REPOSITORY_ROOT) -> dict[str, Any]:
    paths = [repository_root / path for path in (REGISTRY_PATH, PLAN_PATH, COVERAGE_PATH, BEHAVIOR_PATH)]
    report = validate_protocol(*[_load(path) for path in paths])
    # Reuse the document checker; no retrieval, model, active index or runtime routing.
    coverage_report = evaluate_coverage(paths[2], repository_root=repository_root)
    report["document_validation"] = {
        "references_validated": True, "response_quality_assessed": False,
        "coverage_sha256": coverage_report["coverage_sha256"],
        "candidate_manifest_sha256": coverage_report["candidate_manifest_sha256"],
        "meaning": "Catalog, candidate hashes, section references and coverage contracts; no observed counseling quality.",
    }
    report["inputs"] = [{"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
    return report


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["case_counts"]
    lines = ["# 상담 평가 프로토콜 설계 검증", "",
             "스키마·참조·적용 범위를 확인했습니다. 문서가 존재하거나 설계 검증을 통과했다는 사실은 실제 응답의 품질·업무 준수·공식 자격을 입증하지 않습니다.", "",
             f"지식 지도 {counts['coverage']}개와 합성 대화 {counts['behavior']}개는 서로 다른 관찰 단위입니다. 합성 대화에 {len(report['variant_ids'])}개 변형을 연결한 {report['review_template_slots']}개 후속 검토 칸은 모두 미실행입니다.", "",
             "LLM 실행: 없음. AX 향상 측정: 없음. 공식 점수: 없음. 총점·공식 인증 판정: 없음.", "",
             "## 적용 범위와 해석", ""]
    for note in report["scope"]["notes"] + report["plan_notes"] + report["corrections_to_supplied_material"]:
        lines.append(f"- {note}")
    diversity = report["diversity"]
    axis_labels = {
        "same-question-different-background": "같은 질문, 다른 배경",
        "same-finances-different-concern": "같은 재무 상태, 다른 고민",
        "understanding-revision": "대화에 따른 이해 수정",
        "reduced-intervention": "개입 축소",
    }
    lines += ["", "## 합성 다양성 확장", "",
              "원래 54개 상황과 26개 대화는 내용·ID·순서를 보존했습니다. 아래는 추가한 사례 수이며 고객 모집단의 비율이 아닙니다.", "",
              "| 확장 방향 | 추가 상황 | 추가 대화 |", "| --- | ---: | ---: |"]
    for axis, label in axis_labels.items():
        lines.append(f"| {label} | {diversity['added_axis_counts']['coverage'][axis]} | {diversity['added_axis_counts']['behavior'][axis]} |")
    lines += ["", f"추가 대화 {diversity['dialogue_case_count']}개에 턴별 체크포인트 {diversity['checkpoint_count']}개가 있습니다. "
              "이해 유지·수정은 의미를 검토하며 문장 일치로 채점하지 않습니다.", "",
              "| 사용자 단계 | 관련 상황 수 |", "| --- | ---: |"]
    for step, count in diversity["coverage_step_counts"].items():
        lines.append(f"| {step} | {count} |")
    lines += ["", "한 상황이 여러 단계에 관련될 수 있어 위 개수의 합은 고유 상황 수와 다릅니다.", "",
              "### 이해 수정의 기대 흐름", "",
              "아래는 합성 입력과 검토 기대이며 관찰된 모델 응답이 아닙니다. 기대 문단을 다음 턴 입력으로 주입하지 않습니다.", "",
              "| 턴 | 사용자 입력 | 기대 동작 | 기대 이해 | 재사용할 앞선 턴 |", "| ---: | --- | --- | --- | --- |"]
    example = report["multiturn_design_example"]
    for turn, checkpoint in zip(example["turns"], example["checkpoints"]):
        lines.append(f"| {turn['turn_id']} | {_cell(turn['content'])} | {checkpoint['understanding_action']} | "
                     f"{_cell(checkpoint['expected_understanding'])} | {checkpoint['carry_forward_from'] or '없음'} |")
    lines += ["", f"사례: `{example['case_id']}`. 320개 비교 단위 안의 턴별 증거이며, 반복 턴은 독립 고객 표본이 아닙니다."]
    lines += ["", "## 출처와 관찰 단위", ""]
    sources = {source["id"]: source for source in report["sources"]}
    for framework in report["frameworks"]:
        lines += [f"### {framework['name']}", "", f"축: {framework['axis_id'] or '비교 참고만'} · 관찰 단위: {framework['measurement_unit']}", "",
                  f"평가자: {framework['evaluator']}", "", f"적용: {framework['applicability']}", ""]
        for source_id in framework["source_ids"]:
            source = sources[source_id]
            lines += [f"- [{source['title']}]({source['url']}) — {source['locator']}",
                      f"- 확인: {source['verified_at']} · {source['verification']}",
                      f"- [이용조건]({source['rights_url']}): {source['rights']}"]
        lines += [f"- 한계: {limitation}" for limitation in framework["limitations"]]
        lines.append("")
    lines += ["## 프로젝트 관찰 항목", "", "아래 항목은 출처를 참고한 자체 증거 검토이며 공식 척도 문항이나 점수가 아닙니다.", "",
              "| ID / 축 | 검토 내용 | 필요한 증거 |", "| --- | --- | --- |"]
    for criterion in report["criteria"]:
        lines.append(f"| {_cell(criterion['id'])}<br>{_cell(criterion['axis_id'])} | {_cell(criterion['project_check'])}<br>근거: {_cell(criterion['source_locator'])} | {_cell(' · '.join(criterion['required_evidence']))} |")
    lines += ["", "## 축별 적용 계획", "", "아래 수는 적용 계획의 개수이며 관찰 성공·실패 수가 아닙니다.", "",
              "| 축 | 단위 | 적용 상태 | 개수 |", "| --- | --- | --- | ---: |"]
    for axis, by_kind in report["axis_applicability_counts"].items():
        for kind, statuses in by_kind.items():
            for status, count in statuses.items():
                lines.append(f"| {axis} | {kind} | {status} | {count} |")
    lines += ["", "## 변형 비교 절차", "", f"Baseline: `{report['comparison']['baseline_variant_id']}`", ""]
    lines += [f"- `{variant}`" for variant in report["variant_ids"]]
    lines += ["", "고정할 조건:", ""] + [f"- {item}" for item in report["comparison"]["hold_constant"]]
    lines += ["", "후속 검토 절차:", ""] + [f"- {item}" for item in report["comparison"]["procedure"]]
    for kind, title in (("coverage", "지식 지도 사례"), ("behavior", "합성 대화 사례")):
        lines += ["", f"## {title}", "", "| 사례 | 질문 / 영역 | 과정 단계 | 자체 관찰 항목 |", "| --- | --- | --- | --- |"]
        for case in report["cases"]:
            if case["kind"] == kind:
                lines.append(f"| {_cell(case['case_id'])} | {_cell(case['label'])} | {_cell(', '.join(map(str, case['process_stage_ids'])) or '해당 없음')} | {_cell(', '.join(case['criterion_ids']))} |")
    lines += ["", "## 검증의 경계", "",
              "이 명령은 공개 출처를 다시 내려받거나 권리를 법적으로 확인하지 않습니다. 기록된 출처·근거 위치·이용 제한과 사례 참조의 일관성을 검증합니다. 공식 척도 채점, 실제 응답 생성, 평가 결과 import 및 총점 계산은 구현하지 않습니다.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate_protocol()
        destinations = [path.resolve() for path in (args.report, args.markdown, args.template) if path]
        _require(len(set(destinations)) == len(destinations), "output paths must be distinct")
        inputs = {Path(item["path"]) for item in report["inputs"]}
        _require(not inputs.intersection(destinations), "outputs must not overwrite protocol inputs")
        for path, contents in ((args.report, report), (args.template, build_review_template(report))):
            if path:
                path.write_text(json.dumps(contents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.markdown:
            args.markdown.write_text(render_markdown(report), encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"counseling protocol validation failed: {exc}\n")
    print(json.dumps({key: report[key] for key in ("status", "case_counts", "review_template_slots", "llm_evaluated", "ax_gain_measured", "official_scores")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
