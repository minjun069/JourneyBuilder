from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any, Mapping

from agents.error_review_confidence import (
    VISIBLE_CONFIDENCE_LEVELS,
    confidence_level,
)


SAFE_VALIDATION_FAILURE_RESPONSE = (
    "응답 또는 입력 제안이 결정론적 검증을 통과하지 못했습니다. "
    "현재 화면의 값을 확인한 뒤 다시 요청해 주세요."
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class OutputValidationResult:
    valid: bool
    output: dict[str, Any]
    issues: tuple[ValidationIssue, ...]


def validate_form_update(state: Mapping[str, Any]) -> dict[str, Any]:
    """LangGraph-compatible mandatory post-generation node.

    Despite the historical name, this node validates every response, including
    responses without form changes. The integration owner connects it after every
    Agent. It returns only the public output key, so validation diagnostics do not
    cross the callback contract boundary.
    """

    result = validate_form_update_contract(state)
    return {"output": result.output}


def validate_agent_output(state: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility alias with a name that describes the broader validation."""

    return validate_form_update(state)


def validate_form_update_contract(
    state: Mapping[str, Any],
) -> OutputValidationResult:
    raw_output = state.get("output")
    issues: list[ValidationIssue] = []
    if not isinstance(raw_output, dict):
        issues.append(_issue("INVALID_OUTPUT", "output", "output은 object여야 합니다."))
        return _failed(issues, state.get("step_id"))

    output = deepcopy(raw_output)
    response = output.get("response")
    if not isinstance(response, str) or not response.strip():
        issues.append(_issue(
            "INVALID_RESPONSE",
            "output.response",
            "사용자 응답은 비어 있지 않은 문자열이어야 합니다.",
        ))

    output_step_id = output.get("step_id")
    current_step_id = state.get("step_id")
    if not isinstance(current_step_id, str) or not current_step_id:
        issues.append(_issue(
            "INVALID_CURRENT_STEP",
            "step_id",
            "runtime의 현재 step_id가 제공되지 않았습니다.",
        ))
    elif output_step_id is not None and output_step_id != current_step_id:
        issues.append(_issue(
            "STEP_MISMATCH",
            "output.step_id",
            "응답 step_id가 현재 요청 단계와 일치하지 않습니다.",
        ))

    legacy_update = output.get("update")
    if legacy_update not in (None, {}, []):
        issues.append(_issue(
            "LEGACY_UPDATE_NOT_ALLOWED",
            "output.update",
            "검증되지 않은 legacy update payload는 사용할 수 없습니다.",
        ))

    action = output.get("action")
    if action is not None:
        issues.extend(_validate_form_change(
            action=action,
            step_id=current_step_id,
            field_registry=state.get("field_registry", []),
        ))
    review = output.get("review")
    if review is not None:
        issues.extend(_validate_error_review(review, expected_trigger=state.get("review_trigger")))
    ui_action = output.get("ui_action")
    if ui_action is not None:
        if (
            not isinstance(ui_action, dict)
            or set(ui_action) != {"type", "label"}
            or ui_action.get("type") != "resume_elicit"
            or not isinstance(ui_action.get("label"), str)
            or not ui_action["label"].strip()
        ):
            issues.append(_issue(
                "INVALID_UI_ACTION",
                "output.ui_action",
                "지원하지 않는 UI action입니다.",
            ))

    if issues:
        return _failed(issues, current_step_id)
    return OutputValidationResult(valid=True, output=output, issues=())


def _validate_error_review(
    review: Any,
    *,
    expected_trigger: Any = None,
) -> list[ValidationIssue]:
    if not isinstance(review, dict) or set(review) != {"type", "trigger", "manual", "items"}:
        return [_issue("INVALID_REVIEW", "output.review", "review 계약이 올바르지 않습니다.")]
    issues: list[ValidationIssue] = []
    if review.get("type") != "error_review" or review.get("trigger") not in {"save", "user_request"}:
        issues.append(_issue("INVALID_REVIEW", "output.review", "review 종류 또는 trigger가 올바르지 않습니다."))
    if expected_trigger is not None and review.get("trigger") != expected_trigger:
        issues.append(_issue("REVIEW_TRIGGER_MISMATCH", "output.review.trigger", "review trigger가 요청과 일치하지 않습니다."))
    manual = review.get("manual")
    if (
        not isinstance(manual, dict)
        or set(manual) != {"document_id", "version"}
        or not all(isinstance(manual.get(key), str) and manual[key].strip() for key in ("document_id", "version"))
    ):
        issues.append(_issue("INVALID_REVIEW_MANUAL", "output.review.manual", "review manual provenance가 올바르지 않습니다."))
    items = review.get("items")
    if not isinstance(items, list):
        return issues + [_issue("INVALID_REVIEW_ITEMS", "output.review.items", "review items는 array여야 합니다.")]
    allowed = {
        "section", "path", "item_id", "client_item_id", "parent_item_id",
        "parent_client_item_id", "current_value", "reason", "suggested_value", "confidence",
        "confidence_level",
    }
    for index, item in enumerate(items):
        path = f"output.review.items[{index}]"
        if not isinstance(item, dict) or set(item) - allowed:
            issues.append(_issue("INVALID_REVIEW_ITEM", path, "review item이 올바르지 않습니다."))
            continue
        if "current_value" not in item:
            issues.append(_issue("INVALID_REVIEW_ITEM", path, "review item에 현재 값이 필요합니다."))
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("section", "path", "reason")):
            issues.append(_issue("INVALID_REVIEW_ITEM", path, "review field와 이유가 필요합니다."))
        confidence = item.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            issues.append(_issue("INVALID_REVIEW_CONFIDENCE", f"{path}.confidence", "review confidence가 올바르지 않습니다."))
            continue
        level = item.get("confidence_level")
        if (
            level not in VISIBLE_CONFIDENCE_LEVELS
            or level != confidence_level(float(confidence))
        ):
            issues.append(_issue(
                "INVALID_REVIEW_CONFIDENCE_LEVEL",
                f"{path}.confidence_level",
                "낮은 신뢰도이거나 confidence와 일치하지 않는 review item은 노출할 수 없습니다.",
            ))
    return issues


def validate_agent_output_contract(
    state: Mapping[str, Any],
) -> OutputValidationResult:
    """Compatibility alias for callers using the broader validator name."""

    return validate_form_update_contract(state)


def _validate_form_change(
    *,
    action: Any,
    step_id: Any,
    field_registry: Any,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(action, dict):
        return [_issue("INVALID_ACTION", "output.action", "action은 object여야 합니다.")]
    if action.get("type") != "form_change":
        issues.append(_issue(
            "INVALID_ACTION_TYPE",
            "output.action.type",
            "지원하지 않는 action type입니다.",
        ))
    if action.get("target") != "draft_form":
        issues.append(_issue(
            "INVALID_ACTION_TARGET",
            "output.action.target",
            "AI 변경은 미저장 draft_form만 대상으로 할 수 있습니다.",
        ))
    if "mode" in action:
        issues.append(_issue(
            "MODEL_MODE_NOT_ALLOWED",
            "output.action.mode",
            "변경 mode는 AI가 아니라 Django가 결정합니다.",
        ))
    action_step_id = action.get("step_id")
    if action_step_id is not None and action_step_id != step_id:
        issues.append(_issue(
            "ACTION_STEP_MISMATCH",
            "output.action.step_id",
            "form action step_id가 현재 요청 단계와 일치하지 않습니다.",
        ))
    if not isinstance(action.get("user_requested"), bool):
        issues.append(_issue(
            "INVALID_USER_REQUESTED",
            "output.action.user_requested",
            "user_requested는 boolean이어야 합니다.",
        ))
    confidence = action.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        issues.append(_issue(
            "INVALID_CONFIDENCE",
            "output.action.confidence",
            "confidence는 0부터 1 사이의 유한한 수여야 합니다.",
        ))

    if not isinstance(field_registry, list):
        issues.append(_issue(
            "INVALID_FIELD_REGISTRY",
            "field_registry",
            "canonical field registry가 제공되지 않았습니다.",
        ))
        return issues

    changes = action.get("changes")
    if not isinstance(changes, list) or not changes:
        issues.append(_issue(
            "EMPTY_FORM_CHANGES",
            "output.action.changes",
            "form_change에는 하나 이상의 change가 필요합니다.",
        ))
        return issues

    registry = {
        (field.get("section"), field.get("path")): field
        for field in field_registry
        if isinstance(field, dict) and field.get("step_id") == step_id
    }
    for index, change in enumerate(changes):
        issues.extend(_validate_change(change, index, registry))
    return issues


def _validate_change(
    change: Any,
    index: int,
    registry: dict[tuple[Any, Any], dict[str, Any]],
) -> list[ValidationIssue]:
    base_path = f"output.action.changes[{index}]"
    if not isinstance(change, dict):
        return [_issue("INVALID_CHANGE", base_path, "change는 object여야 합니다.")]

    section = change.get("section")
    path = change.get("path")
    operation = change.get("operation")
    spec = registry.get((section, path))
    if spec is None:
        return [_issue(
            "UNKNOWN_CANONICAL_FIELD",
            base_path,
            "현재 단계에 등록되지 않은 canonical 필드입니다.",
        )]
    if spec.get("writable_by_ai") is not True:
        return [_issue(
            "FIELD_NOT_WRITABLE",
            base_path,
            "AI 변경이 허용되지 않은 canonical 필드입니다.",
        )]
    if operation not in spec.get("operations", []):
        return [_issue(
            "OPERATION_NOT_ALLOWED",
            f"{base_path}.operation",
            "해당 필드에서 지원하지 않는 operation입니다.",
        )]

    issues: list[ValidationIssue] = []
    expected_unit = spec.get("unit", "none")
    if "unit" in change and change.get("unit") != expected_unit:
        issues.append(_issue(
            "UNIT_MISMATCH",
            f"{base_path}.unit",
            "change 단위가 canonical 필드 단위와 일치하지 않습니다.",
        ))

    policy_version = change.get("policy_version")
    if policy_version is not None and not (
        isinstance(policy_version, str) and policy_version.strip()
    ):
        issues.append(_issue(
            "INVALID_POLICY_VERSION",
            f"{base_path}.policy_version",
            "기본값 정책 version이 올바르지 않습니다.",
        ))
    source = change.get("source")
    if source is not None and (
        not isinstance(source, dict)
        or set(source) != {"source_id", "title", "reference_period"}
        or not all(
            isinstance(source.get(key), str) and source[key].strip()
            for key in ("source_id", "title", "reference_period")
        )
    ):
        issues.append(_issue(
            "INVALID_CHANGE_SOURCE",
            f"{base_path}.source",
            "기본값 출처 메타데이터가 올바르지 않습니다.",
        ))
    retrieved_at = change.get("retrieved_at")
    if retrieved_at is not None and not (
        isinstance(retrieved_at, str) and _is_iso_date(retrieved_at)
    ):
        issues.append(_issue(
            "INVALID_RETRIEVED_AT",
            f"{base_path}.retrieved_at",
            "기본값 조회 기준일이 올바르지 않습니다.",
        ))
    for metadata_field in ("transform", "assumption_level"):
        if change.get(metadata_field) is not None and not (
            isinstance(change[metadata_field], str) and change[metadata_field].strip()
        ):
            issues.append(_issue(
                "INVALID_CHANGE_METADATA",
                f"{base_path}.{metadata_field}",
                "기본값 설명 메타데이터가 올바르지 않습니다.",
            ))
    source_turn_ids = change.get("source_turn_ids")
    if source_turn_ids is not None and (
        not isinstance(source_turn_ids, list)
        or not source_turn_ids
        or not all(isinstance(turn_id, str) and turn_id for turn_id in source_turn_ids)
    ):
        issues.append(_issue(
            "INVALID_SOURCE_TURNS",
            f"{base_path}.source_turn_ids",
            "Elicit source turn 식별자가 올바르지 않습니다.",
        ))

    cardinality = spec.get("cardinality")
    if operation != "remove" and "value" not in change:
        issues.append(_issue(
            "VALUE_REQUIRED",
            f"{base_path}.value",
            "remove 이외 operation에는 value가 필요합니다.",
        ))
    elif "value" in change:
        value = change.get("value")
        if operation == "add" and cardinality in {"list", "nested_list"}:
            issues.extend(_validate_added_item(
                value=value,
                section=section,
                path=path,
                registry=registry,
                base_path=f"{base_path}.value",
            ))
        else:
            issues.extend(_validate_value(value, spec, f"{base_path}.value"))

    if cardinality in {"list", "nested_list"} and operation in {"update", "remove"}:
        if change.get("item_id") is None and not change.get("client_item_id"):
            issues.append(_issue(
                "ITEM_ID_REQUIRED",
                base_path,
                "반복 항목 update/remove에는 item_id 또는 client_item_id가 필요합니다.",
            ))
    if cardinality == "nested_list" and operation == "add":
        if change.get("parent_item_id") is None and not change.get("parent_client_item_id"):
            issues.append(_issue(
                "PARENT_ID_REQUIRED",
                base_path,
                "중첩 항목 add에는 부모 ID가 필요합니다.",
            ))
    return issues


def _validate_added_item(
    *,
    value: Any,
    section: Any,
    path: Any,
    registry: dict[tuple[Any, Any], dict[str, Any]],
    base_path: str,
) -> list[ValidationIssue]:
    """Validate a list row represented by one D1 form-action change.

    A list ``add`` uses ``path`` as the canonical anchor field while ``value``
    contains the new row. Nested ``goal_periods.items`` additions contain one
    child row whose fields map to ``items[].*`` registry paths.
    """

    if not isinstance(value, dict) or not value:
        return [_issue(
            "ADDED_ITEM_OBJECT_REQUIRED",
            base_path,
            "반복 항목 add의 value는 비어 있지 않은 object여야 합니다.",
        )]

    prefix = "items[]." if section == "goal_periods" and path == "items" else ""
    issues: list[ValidationIssue] = []
    for key, item_value in value.items():
        item_path = f"{prefix}{key}"
        item_spec = registry.get((section, item_path))
        item_value_path = f"{base_path}.{key}"
        if item_spec is None:
            issues.append(_issue(
                "UNKNOWN_ADDED_ITEM_FIELD",
                item_value_path,
                "추가 항목에 등록되지 않은 canonical 필드가 있습니다.",
            ))
            continue
        if item_spec.get("writable_by_ai") is not True:
            issues.append(_issue(
                "ADDED_ITEM_FIELD_NOT_WRITABLE",
                item_value_path,
                "추가 항목에 AI가 쓸 수 없는 canonical 필드가 있습니다.",
            ))
            continue
        issues.extend(_validate_value(item_value, item_spec, item_value_path))
    return issues


def _validate_value(
    value: Any,
    spec: Mapping[str, Any],
    path: str,
) -> list[ValidationIssue]:
    if value is None:
        if spec.get("nullable") is False:
            return [_issue("NULL_NOT_ALLOWED", path, "null을 허용하지 않는 필드입니다.")]
        return []

    value_type = spec.get("value_type")
    type_is_valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "decimal": isinstance(value, (int, float)) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "date": isinstance(value, str) and _is_iso_date(value),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }.get(value_type, False)
    if not type_is_valid:
        return [_issue(
            "VALUE_TYPE_MISMATCH",
            path,
            "value 자료형이 canonical field 계약과 일치하지 않습니다.",
        )]

    constraints = spec.get("constraints") or {}
    issues: list[ValidationIssue] = []
    if "choices" in constraints and value not in constraints["choices"]:
        issues.append(_issue("CHOICE_NOT_ALLOWED", path, "허용된 선택값이 아닙니다."))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in constraints and value < constraints["min"]:
            issues.append(_issue("VALUE_BELOW_MIN", path, "필드 최솟값보다 작습니다."))
        if "max" in constraints and value > constraints["max"]:
            issues.append(_issue("VALUE_ABOVE_MAX", path, "필드 최댓값보다 큽니다."))
    if isinstance(value, str) and "max_length" in constraints:
        if len(value) > constraints["max_length"]:
            issues.append(_issue("VALUE_TOO_LONG", path, "필드 최대 길이를 초과했습니다."))
    return issues


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _failed(
    issues: list[ValidationIssue],
    step_id: Any,
) -> OutputValidationResult:
    safe_output: dict[str, Any] = {
        "response": SAFE_VALIDATION_FAILURE_RESPONSE,
        "action": None,
    }
    if isinstance(step_id, str):
        safe_output["step_id"] = step_id
    return OutputValidationResult(
        valid=False,
        output=safe_output,
        issues=tuple(issues),
    )


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)
