"""Validated artifact sealing and explicit dependency binding policy."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from graph.task_contracts import TaskResultValidation


BINDABLE_ARTIFACT_PATHS = {
    "reference_value": frozenset({
        "reference_id", "age", "value", "unit", "period", "population",
        "comparator", "source_id", "source_version", "as_of",
        "calculation_policy_version",
    }),
    "text_response": frozenset({"text"}),
    "form_change": frozenset(),
}


@dataclass(frozen=True, slots=True)
class SealedArtifact:
    task_id: str
    canonical_payload: str


class BindingResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def seal_validated_artifact(validation: TaskResultValidation) -> SealedArtifact:
    result = validation.result
    task_id = result.get("task_id")
    artifact = result.get("artifact")
    if (
        not validation.valid
        or result.get("status") != "completed"
        or not isinstance(task_id, str)
        or not isinstance(artifact, Mapping)
    ):
        raise ValueError("only a validated completed artifact can be sealed")
    return SealedArtifact(task_id, _canonical_artifact_payload(artifact))


def resolve_task_bindings(
    *,
    task: Mapping[str, Any],
    task_results: Mapping[str, Mapping[str, Any]],
    expectations: Mapping[str, Mapping[str, Any]],
    artifact_seals: Mapping[str, SealedArtifact],
) -> dict[str, Any]:
    bindings = task.get("bindings", {})
    if not isinstance(bindings, Mapping):
        raise BindingResolutionError("INVALID_BINDINGS", "bindings는 object여야 합니다.")
    dependencies = task.get("depends_on", [])
    if not isinstance(dependencies, list):
        raise BindingResolutionError("INVALID_DEPENDENCIES", "depends_on은 array여야 합니다.")

    resolved: dict[str, Any] = {}
    for input_name, binding in bindings.items():
        expectation = expectations.get(input_name)
        if expectation is None:
            raise BindingResolutionError("UNKNOWN_BINDING_INPUT", f"허용되지 않은 binding 입력입니다: {input_name}")
        if not isinstance(binding, Mapping):
            raise BindingResolutionError("INVALID_BINDING", f"binding은 object여야 합니다: {input_name}")
        source_id = binding.get("task_id")
        path = binding.get("path")
        if source_id not in dependencies:
            raise BindingResolutionError("UNDECLARED_DEPENDENCY", f"binding source가 depends_on에 없습니다: {source_id}")
        source_result = task_results.get(str(source_id))
        if source_result is None or source_result.get("status") != "completed":
            raise BindingResolutionError("DEPENDENCY_FAILED", f"완료되지 않은 dependency를 참조했습니다: {source_id}")
        artifact = source_result.get("artifact")
        if not isinstance(artifact, Mapping):
            raise BindingResolutionError("DEPENDENCY_ARTIFACT_MISSING", f"dependency artifact가 없습니다: {source_id}")
        seal = artifact_seals.get(str(source_id))
        if seal is None or seal.task_id != source_id:
            raise BindingResolutionError("ARTIFACT_INTEGRITY_MISSING", f"검증 후 봉인된 dependency artifact가 없습니다: {source_id}")
        if _canonical_artifact_payload(artifact) != seal.canonical_payload:
            raise BindingResolutionError("ARTIFACT_INTEGRITY_MISMATCH", f"검증 뒤 dependency artifact가 변경되었습니다: {source_id}")
        artifact_type = artifact.get("type")
        if artifact_type != expectation.get("artifact_type"):
            raise BindingResolutionError("ARTIFACT_TYPE_MISMATCH", f"binding artifact type이 맞지 않습니다: {input_name}")
        allowed_paths = BINDABLE_ARTIFACT_PATHS.get(str(artifact_type), frozenset())
        if path not in allowed_paths or path != expectation.get("path"):
            raise BindingResolutionError("ARTIFACT_PATH_NOT_ALLOWED", f"허용되지 않은 artifact path입니다: {path}")
        if path not in artifact:
            raise BindingResolutionError("ARTIFACT_VALUE_MISSING", f"artifact에 binding 값이 없습니다: {path}")
        expected_unit = expectation.get("unit")
        if expected_unit is not None and artifact.get("unit") != expected_unit:
            raise BindingResolutionError("ARTIFACT_UNIT_MISMATCH", f"artifact 단위가 맞지 않습니다: {input_name}")
        value = artifact[path]
        expected_type = expectation.get("value_type")
        if expected_type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise BindingResolutionError("ARTIFACT_VALUE_TYPE_MISMATCH", f"artifact 값 자료형이 맞지 않습니다: {input_name}")
        if expected_type == "string" and not isinstance(value, str):
            raise BindingResolutionError("ARTIFACT_VALUE_TYPE_MISMATCH", f"artifact 값 자료형이 맞지 않습니다: {input_name}")
        resolved[input_name] = deepcopy(value)
    return resolved


def _canonical_artifact_payload(artifact: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(artifact), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BindingResolutionError(
            "ARTIFACT_INTEGRITY_MISMATCH",
            "dependency artifact를 안전하게 정규화할 수 없습니다.",
        ) from exc
