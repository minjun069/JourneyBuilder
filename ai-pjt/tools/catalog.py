from __future__ import annotations

from typing import Any

from .contracts import (
    FunctionToolDefinition,
    ToolErrorCode,
    ToolExecutionContext,
    ToolInvocationError,
)
from .registry import FunctionToolRegistry


EMPTY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def build_default_registry() -> FunctionToolRegistry:
    registry = FunctionToolRegistry()
    for definition in default_tool_definitions():
        registry.register(definition)
    return registry


def default_tool_definitions() -> tuple[FunctionToolDefinition, ...]:
    """Return the P10 foundation definitions before runtime graph binding."""

    return (
        FunctionToolDefinition(
            name="search_financial_knowledge",
            description=(
                "승인된 금융 지식에서 질문과 관련된 근거 구간을 검색합니다. "
                "사용자별 금융 데이터나 미검토 문서는 검색하지 않습니다."
            ),
            input_schema=_object_schema(
                properties={
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                required=("query",),
            ),
            output_schema=_object_schema(
                properties={
                    "query": {"type": "string"},
                    "results": {"type": "array", "items": {"type": "object"}},
                },
                required=("query", "results"),
            ),
            allowed_agents=frozenset({"qa", "input_assist", "error_review"}),
            handler=_search_financial_knowledge,
            timeout_seconds=5.0,
            argument_classes=("public_knowledge_query",),
        ),
        FunctionToolDefinition(
            name="get_form_field_spec",
            description=(
                "현재 화면 단계에서 AI 입력이 허용된 canonical 필드의 자료형, 단위, "
                "제약과 가능한 연산을 조회합니다. 현재 단계는 시스템 컨텍스트로 고정됩니다."
            ),
            input_schema=_object_schema(
                properties={
                    "field_path": {"type": "string", "minLength": 1, "maxLength": 200},
                },
            ),
            output_schema=_object_schema(
                properties={
                    "step_id": {"type": "string"},
                    "fields": {"type": "array", "items": _field_schema()},
                },
                required=("step_id", "fields"),
            ),
            allowed_agents=frozenset({"qa", "input_assist", "error_review"}),
            handler=_get_form_field_spec,
            timeout_seconds=0.5,
            argument_classes=("canonical_field_identifier",),
        ),
        FunctionToolDefinition(
            name="get_dashboard_metric",
            description=(
                "현재 상담 세션의 검증 완료된 Backend 보고서 지표를 조회합니다. "
                "Frontend mock이나 화면 표시값은 사용하지 않습니다."
            ),
            input_schema=_object_schema(
                properties={
                    "metric_id": {"type": "string", "minLength": 1, "maxLength": 100},
                    "report_version": {"type": "integer", "minimum": 1},
                },
                required=("metric_id",),
            ),
            output_schema=_object_schema(
                properties={"metric": _metric_schema()},
                required=("metric",),
            ),
            allowed_agents=frozenset({"qa", "chart_interpret"}),
            handler=_get_dashboard_metric,
            timeout_seconds=1.0,
            argument_classes=("financial_metric_identifier",),
        ),
        FunctionToolDefinition(
            name="get_retirement_scenario",
            description=(
                "현재 상담 세션에 저장되고 소유권이 검증된 은퇴 계산 시나리오를 조회합니다. "
                "시나리오 저장소가 준비되지 않은 경우 명시적 오류를 반환합니다."
            ),
            input_schema=_object_schema(
                properties={
                    "scenario_id": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                required=("scenario_id",),
            ),
            output_schema=_object_schema(
                properties={"scenario": _scenario_schema()},
                required=("scenario",),
            ),
            allowed_agents=frozenset({"qa", "chart_interpret"}),
            handler=_get_retirement_scenario,
            timeout_seconds=1.0,
            argument_classes=("retirement_scenario_identifier",),
        ),
        FunctionToolDefinition(
            name="list_available_reports",
            description=(
                "현재 상담 세션에서 조회 가능한 보고서의 타입, 버전, 상태와 생성 시각을 나열합니다."
            ),
            input_schema=EMPTY_INPUT_SCHEMA,
            output_schema=_object_schema(
                properties={
                    "reports": {"type": "array", "items": _report_schema()},
                },
                required=("reports",),
            ),
            allowed_agents=frozenset({"qa", "chart_interpret"}),
            handler=_list_available_reports,
            timeout_seconds=0.5,
        ),
        FunctionToolDefinition(
            name="get_step_completion_status",
            description=(
                "현재 상담 세션의 완료 영역과 미완료 영역, 보고서 생성 가능 여부를 조회합니다. "
                "개별 금융 값은 반환하지 않습니다."
            ),
            input_schema=EMPTY_INPUT_SCHEMA,
            output_schema=_object_schema(
                properties={
                    "completed_sections": {"type": "array", "items": {"type": "string"}},
                    "missing_sections": {"type": "array", "items": {"type": "string"}},
                    "progress_pct": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reports_ready": {"type": "boolean"},
                },
                required=(
                    "completed_sections",
                    "missing_sections",
                    "progress_pct",
                    "reports_ready",
                ),
            ),
            allowed_agents=frozenset({"qa", "input_assist", "elicit"}),
            handler=_get_step_completion_status,
            timeout_seconds=0.5,
        ),
    )


async def _search_financial_knowledge(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if context.knowledge_search is None:
        raise ToolInvocationError(
            ToolErrorCode.DEPENDENCY_UNAVAILABLE,
            "승인된 금융 지식 검색기가 준비되지 않았습니다.",
            retryable=True,
        )
    query = arguments["query"].strip()
    if not query:
        raise ToolInvocationError(
            ToolErrorCode.INVALID_ARGUMENTS,
            "공백만 있는 검색어는 사용할 수 없습니다.",
        )
    try:
        results = await context.knowledge_search(query, arguments.get("top_k", 5))
    except RuntimeError as exc:
        raise ToolInvocationError(
            ToolErrorCode.DEPENDENCY_UNAVAILABLE,
            "승인된 금융 지식 검색기를 사용할 수 없습니다.",
            retryable=True,
        ) from exc
    if not isinstance(results, list):
        raise ToolInvocationError(
            ToolErrorCode.INVALID_RESULT,
            "검색기가 목록 형태의 결과를 반환하지 않았습니다.",
        )
    return {"query": query, "results": results}


async def _get_form_field_spec(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    field_path = arguments.get("field_path")
    fields = [
        dict(field)
        for field in context.field_registry
        if field.get("step_id") == context.step_id and field.get("writable_by_ai") is True
    ]
    if field_path:
        fields = [
            field
            for field in fields
            if field.get("path") == field_path
            or f"{field.get('section')}.{field.get('path')}" == field_path
        ]
        if not fields:
            raise ToolInvocationError(
                ToolErrorCode.NOT_FOUND,
                "현재 단계에서 입력 가능한 canonical 필드를 찾지 못했습니다.",
            )
    return {"step_id": context.step_id, "fields": fields}


async def _get_dashboard_metric(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    metric_id = arguments["metric_id"]
    metric = context.dashboard_metrics.get(metric_id)
    if metric is None:
        raise ToolInvocationError(
            ToolErrorCode.NOT_FOUND,
            "현재 세션의 검증된 보고서에서 해당 지표를 찾지 못했습니다.",
        )
    if metric.get("status") != "ready":
        raise ToolInvocationError(
            ToolErrorCode.DEPENDENCY_UNAVAILABLE,
            "해당 지표의 원본 보고서가 아직 준비되지 않았습니다.",
            retryable=True,
        )
    requested_version = arguments.get("report_version")
    if requested_version is not None and metric.get("report_version") != requested_version:
        raise ToolInvocationError(
            ToolErrorCode.NOT_FOUND,
            "요청한 보고서 버전의 지표를 찾지 못했습니다.",
        )
    value = dict(metric)
    value.setdefault("metric_id", metric_id)
    return {"metric": value}


async def _get_retirement_scenario(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = arguments["scenario_id"]
    scenario = context.retirement_scenarios.get(scenario_id)
    if scenario is None:
        raise ToolInvocationError(
            ToolErrorCode.NOT_FOUND,
            "현재 세션에 저장된 은퇴 시나리오를 찾지 못했습니다.",
        )
    if scenario.get("status") != "ready":
        raise ToolInvocationError(
            ToolErrorCode.DEPENDENCY_UNAVAILABLE,
            "은퇴 시나리오 계산이 아직 준비되지 않았습니다.",
            retryable=True,
        )
    value = dict(scenario)
    value.setdefault("scenario_id", scenario_id)
    return {"scenario": value}


async def _list_available_reports(
    context: ToolExecutionContext,
    _: dict[str, Any],
) -> dict[str, Any]:
    return {"reports": [dict(report) for report in context.available_reports]}


async def _get_step_completion_status(
    context: ToolExecutionContext,
    _: dict[str, Any],
) -> dict[str, Any]:
    if any(not isinstance(value, bool) for value in context.section_completion.values()):
        raise ToolInvocationError(
            ToolErrorCode.INVALID_RESULT,
            "완료 상태 snapshot에 boolean이 아닌 값이 포함되어 있습니다.",
        )
    completed = sorted(
        section for section, is_complete in context.section_completion.items() if is_complete
    )
    missing = sorted(
        section for section, is_complete in context.section_completion.items() if not is_complete
    )
    total = len(completed) + len(missing)
    progress = round(len(completed) / total * 100) if total else 0
    return {
        "completed_sections": completed,
        "missing_sections": missing,
        "progress_pct": progress,
        "reports_ready": bool(total) and not missing,
    }


def _object_schema(
    *,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _field_schema() -> dict[str, Any]:
    return _object_schema(
        properties={
            "step_id": {"type": "string"},
            "section": {"type": "string"},
            "path": {"type": "string"},
            "value_type": {"type": "string"},
            "unit": {"type": "string"},
            "constraints": {"type": "object"},
            "cardinality": {"type": "string"},
            "sensitivity": {"type": "string"},
            "writable_by_ai": {"type": "boolean"},
            "nullable": {"type": "boolean"},
            "label": {"type": "string"},
            "operations": {"type": "array", "items": {"type": "string"}},
        },
        required=(
            "step_id",
            "section",
            "path",
            "value_type",
            "unit",
            "constraints",
            "cardinality",
            "writable_by_ai",
            "operations",
        ),
    )


def _metric_schema() -> dict[str, Any]:
    return _object_schema(
        properties={
            "metric_id": {"type": "string"},
            "value": {},
            "unit": {"type": "string"},
            "source_report": {"type": "string"},
            "report_version": {"type": "integer", "minimum": 1},
            "generated_at": {"type": "string"},
            "status": {"type": "string", "enum": ["ready"]},
            "assumptions": {"type": "object"},
        },
        required=(
            "metric_id",
            "value",
            "unit",
            "source_report",
            "report_version",
            "generated_at",
            "status",
        ),
    )


def _scenario_schema() -> dict[str, Any]:
    return _object_schema(
        properties={
            "scenario_id": {"type": "string"},
            "status": {"type": "string", "enum": ["ready"]},
            "version": {"type": "integer", "minimum": 1},
            "generated_at": {"type": "string"},
            "assumptions": {"type": "object"},
            "metrics": {"type": "object"},
        },
        required=(
            "scenario_id",
            "status",
            "version",
            "generated_at",
            "assumptions",
            "metrics",
        ),
    )


def _report_schema() -> dict[str, Any]:
    return _object_schema(
        properties={
            "report_type": {"type": "string"},
            "version": {"type": "integer", "minimum": 1},
            "status": {"type": "string", "enum": ["processing", "ready", "failed"]},
            "generated_at": {"type": "string"},
        },
        required=("report_type", "version", "status", "generated_at"),
    )
