"""Deterministic task-result, artifact, and binding contracts for F03."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from math import isfinite
from collections.abc import Mapping
from typing import Any

from tools.output_policy import validate_form_update_contract


TASK_STATUSES = {"completed", "failed", "blocked"}
TOOL_ARTIFACT_SCHEMA_VERSION = "1.0"
ARTIFACT_TYPES = {
    "text_response",
    "form_change",
    "reference_value",
}
REFERENCE_VALUE_FIELDS = {
    "type",
    "schema_version",
    "producer_task_id",
    "reference_id",
    "age",
    "value",
    "unit",
    "period",
    "population",
    "comparator",
    "source_id",
    "source_version",
    "as_of",
    "calculation_policy_version",
}
@dataclass(frozen=True)
class TaskValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class TaskResultValidation:
    valid: bool
    result: dict[str, Any]
    issues: tuple[TaskValidationIssue, ...]


def failed_task_result(
    task_id: str,
    reason_code: str,
    *,
    message: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "task_id": task_id,
        "status": "failed",
        "reason_code": reason_code,
    }
    if message:
        result["response"] = message
    return result


def blocked_task_result(
    task_id: str,
    blocked_by: str | list[str],
) -> dict[str, Any]:
    blockers = [blocked_by] if isinstance(blocked_by, str) else list(blocked_by)
    return {
        "task_id": task_id,
        "status": "blocked",
        "reason_code": "dependency_failed",
        "blocked_by": blockers,
    }


def validate_task_result(
    *,
    task: Mapping[str, Any],
    result: Mapping[str, Any],
    step_id: str,
    field_registry: list[dict[str, Any]] | None = None,
) -> TaskResultValidation:
    """Validate status invariants and the task's declared typed artifact."""

    task_id = task.get("id")
    issues: list[TaskValidationIssue] = []
    if not isinstance(task_id, str) or not task_id:
        task_id = "task-unknown"
        issues.append(_issue("INVALID_TASK_ID", "task.id", "task id가 필요합니다."))

    if not isinstance(result, Mapping):
        issues.append(
            _issue("INVALID_TASK_RESULT", "result", "task result는 object여야 합니다.")
        )
        return _failed_validation(task_id, issues)

    candidate = deepcopy(dict(result))
    if candidate.get("task_id") != task_id:
        issues.append(
            _issue(
                "TASK_ID_MISMATCH",
                "result.task_id",
                "task result id가 실행 task와 일치하지 않습니다.",
            )
        )

    status = candidate.get("status")
    if status not in TASK_STATUSES:
        issues.append(
            _issue(
                "INVALID_TASK_STATUS",
                "result.status",
                "지원하지 않는 task 상태입니다.",
            )
        )
    elif status == "completed":
        _validate_completed_result(
            task=task,
            result=candidate,
            step_id=step_id,
            field_registry=field_registry or [],
            issues=issues,
        )
    elif status == "failed":
        if not _non_empty(candidate.get("reason_code")):
            issues.append(
                _issue(
                    "MISSING_FAILURE_REASON",
                    "result.reason_code",
                    "실패 task에는 reason code가 필요합니다.",
                )
            )
        if candidate.get("artifact") is not None:
            issues.append(
                _issue(
                    "FAILED_TASK_HAS_ARTIFACT",
                    "result.artifact",
                    "실패 task는 소비 가능한 artifact를 반환할 수 없습니다.",
                )
            )
    elif status == "blocked":
        _validate_blocked_result(task=task, result=candidate, issues=issues)

    if issues:
        return _failed_validation(task_id, issues)
    return TaskResultValidation(valid=True, result=candidate, issues=())


def _validate_completed_result(
    *,
    task: Mapping[str, Any],
    result: Mapping[str, Any],
    step_id: str,
    field_registry: list[dict[str, Any]],
    issues: list[TaskValidationIssue],
) -> None:
    if result.get("reason_code") is not None:
        issues.append(
            _issue(
                "COMPLETED_TASK_HAS_FAILURE",
                "result.reason_code",
                "완료 task에는 실패 reason code를 둘 수 없습니다.",
            )
        )
    artifact = result.get("artifact")
    if not isinstance(artifact, Mapping):
        issues.append(
            _issue(
                "MISSING_ARTIFACT",
                "result.artifact",
                "완료 task에는 typed artifact가 필요합니다.",
            )
        )
        return

    artifact_type = artifact.get("type")
    if artifact_type not in ARTIFACT_TYPES:
        issues.append(
            _issue(
                "UNSUPPORTED_ARTIFACT",
                "result.artifact.type",
                "지원하지 않는 artifact type입니다.",
            )
        )
        return
    if artifact_type != task.get("produces"):
        issues.append(
            _issue(
                "ARTIFACT_TYPE_MISMATCH",
                "result.artifact.type",
                "artifact type이 task produces와 일치하지 않습니다.",
            )
        )
        return
    if (
        artifact_type == "reference_value"
        and artifact.get("producer_task_id") != task.get("id")
    ):
        issues.append(
            _issue(
                "ARTIFACT_PRODUCER_MISMATCH",
                "result.artifact.producer_task_id",
                "tool artifact 생산 task가 실행 task와 일치하지 않습니다.",
            )
        )

    if artifact_type == "reference_value":
        _validate_reference_value(artifact, issues)
    elif artifact_type == "text_response":
        if not _non_empty(artifact.get("text")):
            issues.append(
                _issue(
                    "INVALID_TEXT_ARTIFACT",
                    "result.artifact.text",
                    "text artifact는 비어 있지 않아야 합니다.",
                )
            )
    elif artifact_type == "form_change":
        response = result.get("response", "입력 변경안을 만들었습니다.")
        output_result = validate_form_update_contract({
            "step_id": step_id,
            "output": {
                "response": response,
                "step_id": step_id,
                "action": dict(artifact),
            },
        })
        if not output_result.valid:
            for issue in output_result.issues:
                issues.append(
                    _issue(
                        issue.code,
                        f"result.artifact.{issue.path}",
                        issue.message,
                    )
                )


def _validate_reference_value(
    artifact: Mapping[str, Any],
    issues: list[TaskValidationIssue],
) -> None:
    _validate_exact_fields(
        artifact,
        REFERENCE_VALUE_FIELDS,
        issues,
        artifact_name="reference",
    )
    _validate_tool_artifact_identity(artifact, issues)
    value = artifact.get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        issues.append(
            _issue(
                "INVALID_REFERENCE_VALUE",
                "result.artifact.value",
                "reference value는 0 이상의 유한한 숫자여야 합니다.",
            )
        )
    age = artifact.get("age")
    if isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 120:
        issues.append(
            _issue(
                "INVALID_REFERENCE_AGE",
                "result.artifact.age",
                "reference value 연령은 0~120 정수여야 합니다.",
            )
        )
    for field in (
        "reference_id",
        "unit",
        "period",
        "population",
        "comparator",
        "source_id",
        "source_version",
        "calculation_policy_version",
    ):
        if not _non_empty(artifact.get(field)):
            issues.append(
                _issue(
                    "MISSING_REFERENCE_METADATA",
                    f"result.artifact.{field}",
                    f"reference value에 {field}가 필요합니다.",
                )
            )
    as_of = artifact.get("as_of")
    if not isinstance(as_of, str) or not _is_iso_date(as_of):
        issues.append(
            _issue(
                "INVALID_REFERENCE_DATE",
                "result.artifact.as_of",
                "reference value 기준일은 ISO date여야 합니다.",
            )
        )


def _validate_tool_artifact_identity(
    artifact: Mapping[str, Any],
    issues: list[TaskValidationIssue],
) -> None:
    if artifact.get("schema_version") != TOOL_ARTIFACT_SCHEMA_VERSION:
        issues.append(
            _issue(
                "INVALID_ARTIFACT_SCHEMA_VERSION",
                "result.artifact.schema_version",
                "지원하지 않는 tool artifact schema version입니다.",
            )
        )
    if not _non_empty(artifact.get("producer_task_id")):
        issues.append(
            _issue(
                "MISSING_ARTIFACT_PRODUCER",
                "result.artifact.producer_task_id",
                "tool artifact에 생산 task ID가 필요합니다.",
            )
        )


def _validate_exact_fields(
    artifact: Mapping[str, Any],
    expected: set[str],
    issues: list[TaskValidationIssue],
    *,
    artifact_name: str,
) -> None:
    unknown = set(artifact) - expected
    missing = expected - set(artifact)
    if unknown:
        issues.append(
            _issue(
                "UNKNOWN_TOOL_ARTIFACT_FIELD",
                "result.artifact",
                f"알 수 없는 {artifact_name} field입니다: {', '.join(sorted(unknown))}",
            )
        )
    if missing:
        issues.append(
            _issue(
                "MISSING_TOOL_ARTIFACT_FIELD",
                "result.artifact",
                f"누락된 {artifact_name} field입니다: {', '.join(sorted(missing))}",
            )
        )


def _validate_blocked_result(
    *,
    task: Mapping[str, Any],
    result: Mapping[str, Any],
    issues: list[TaskValidationIssue],
) -> None:
    dependencies = task.get("depends_on", [])
    blocked_by = result.get("blocked_by")
    if result.get("reason_code") != "dependency_failed":
        issues.append(
            _issue(
                "INVALID_BLOCK_REASON",
                "result.reason_code",
                "blocked task는 dependency_failed여야 합니다.",
            )
        )
    if (
        not isinstance(blocked_by, list)
        or not blocked_by
        or any(item not in dependencies for item in blocked_by)
    ):
        issues.append(
            _issue(
                "INVALID_BLOCKED_BY",
                "result.blocked_by",
                "blocked_by는 실패한 dependency를 가리켜야 합니다.",
            )
        )
    if result.get("artifact") is not None:
        issues.append(
            _issue(
                "BLOCKED_TASK_HAS_ARTIFACT",
                "result.artifact",
                "실행되지 않은 task는 artifact를 반환할 수 없습니다.",
            )
        )


def _failed_validation(
    task_id: str,
    issues: list[TaskValidationIssue],
) -> TaskResultValidation:
    return TaskResultValidation(
        valid=False,
        result=failed_task_result(task_id, "artifact_invalid"),
        issues=tuple(issues),
    )


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _issue(code: str, path: str, message: str) -> TaskValidationIssue:
    return TaskValidationIssue(code=code, path=path, message=message)
