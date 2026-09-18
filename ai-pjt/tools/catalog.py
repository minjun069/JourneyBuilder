from __future__ import annotations

from typing import Any

from .contracts import (
    FunctionToolDefinition,
    ToolErrorCode,
    ToolExecutionContext,
    ToolInvocationError,
)
from .financial_policy import (
    ApprovedFinancialPolicyError,
    ApprovedReferenceValueNotFound,
    REFERENCE_VALUE_INPUT_SCHEMA,
    lookup_approved_reference_value,
)
from .registry import FunctionToolRegistry


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
            allowed_task_types=frozenset({
                "answer_question", "propose_form_change", "review_validation_error"
            }),
            handler=_search_financial_knowledge,
            timeout_seconds=5.0,
            argument_classes=("public_knowledge_query",),
        ),
        FunctionToolDefinition(
            name="get_approved_reference_value",
            description=(
                "승인된 공용 계산 정책에서 기준 항목, 연령, 기간과 모집단이 정확히 "
                "일치하는 구조화 기준값을 조회합니다. 값을 추정하거나 보간하지 않습니다."
            ),
            input_schema=REFERENCE_VALUE_INPUT_SCHEMA,
            output_schema=_object_schema(
                properties={"reference": _reference_value_schema()},
                required=("reference",),
            ),
            allowed_agents=frozenset({"qa", "input_assist"}),
            allowed_task_types=frozenset({
                "answer_question", "lookup_reference_value", "propose_form_change"
            }),
            handler=_get_approved_reference_value,
            timeout_seconds=0.5,
            argument_classes=(
                "financial_reference_identifier",
                "age",
                "statistical_period",
                "statistical_population_identifier",
            ),
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
            allowed_task_types=frozenset({
                "answer_question", "propose_form_change", "review_validation_error"
            }),
            handler=_get_form_field_spec,
            timeout_seconds=0.5,
            argument_classes=("canonical_field_identifier",),
        ),
    )


async def _get_approved_reference_value(
    _: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        reference = lookup_approved_reference_value(
            reference_id=arguments["reference_id"],
            age=arguments["age"],
            period=arguments["period"],
            population=arguments["population"],
        )
    except ApprovedReferenceValueNotFound as exc:
        raise ToolInvocationError(
            ToolErrorCode.NOT_FOUND,
            "조건에 정확히 일치하는 승인 기준값을 찾지 못했습니다.",
        ) from exc
    except ApprovedFinancialPolicyError as exc:
        raise ToolInvocationError(
            ToolErrorCode.DEPENDENCY_UNAVAILABLE,
            "승인된 계산 정책을 사용할 수 없습니다.",
        ) from exc
    return {"reference": reference}


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


def _reference_value_schema() -> dict[str, Any]:
    return _object_schema(
        properties={
            "reference_id": {"type": "string"},
            "age": {"type": "integer", "minimum": 0, "maximum": 120},
            "value": {"type": "number"},
            "unit": {"type": "string", "minLength": 1},
            "period": {
                "type": "string",
                "enum": ["monthly", "annual", "current"],
            },
            "population": {"type": "string", "minLength": 1},
            "comparator": {
                "type": "string",
                "enum": ["lt", "lte", "eq", "gte", "gt"],
            },
            "source_id": {"type": "string", "minLength": 1},
            "source_version": {"type": "string", "minLength": 1},
            "as_of": {"type": "string", "minLength": 1},
            "calculation_policy_version": {"type": "string", "minLength": 1},
        },
        required=(
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
        ),
    )
