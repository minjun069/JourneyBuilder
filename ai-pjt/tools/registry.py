from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from math import isfinite
from time import monotonic
from typing import Any

from .contracts import (
    FunctionToolDefinition,
    ToolAuditEvent,
    ToolCallResult,
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
    ToolInvocationError,
)


class FunctionToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, FunctionToolDefinition] = {}

    def register(self, definition: FunctionToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"이미 등록된 Function tool입니다: {definition.name}")
        if not inspect.iscoroutinefunction(definition.handler):
            raise TypeError(
                f"Function tool handler는 timeout 적용을 위해 async 함수여야 합니다: "
                f"{definition.name}"
            )
        self._definitions[definition.name] = definition

    def registered_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def schemas_for(self, agent: str) -> list[dict[str, Any]]:
        """Return canonical schemas; a provider adapter maps these at bind time."""

        return [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
                "version": definition.version,
            }
            for definition in self._definitions.values()
            if agent in definition.allowed_agents
        ]

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolCallResult:
        started_at = monotonic()
        definition = self._definitions.get(tool_name)
        if definition is None:
            result = self._failure(
                tool_name,
                "unknown",
                ToolErrorCode.UNKNOWN_TOOL,
                "등록되지 않은 Function tool입니다.",
            )
            self._audit(context, tool_name, "unknown", (), result, started_at)
            return result

        if context.agent not in definition.allowed_agents:
            result = self._failure(
                definition.name,
                definition.version,
                ToolErrorCode.FORBIDDEN,
                "현재 Agent에는 이 도구를 호출할 권한이 없습니다.",
            )
            self._audit(
                context,
                definition.name,
                definition.version,
                definition.argument_classes,
                result,
                started_at,
            )
            return result

        if not isinstance(arguments, Mapping):
            argument_errors = [
                _schema_error("$", "type", "도구 인자는 object여야 합니다.")
            ]
            normalized_arguments: dict[str, Any] = {}
        else:
            normalized_arguments = dict(arguments)
            argument_errors = validate_json_schema(
                definition.input_schema,
                normalized_arguments,
            )
        if argument_errors:
            result = self._failure(
                definition.name,
                definition.version,
                ToolErrorCode.INVALID_ARGUMENTS,
                "도구 인자가 계약과 일치하지 않습니다.",
                details=tuple(argument_errors),
            )
            self._audit(
                context,
                definition.name,
                definition.version,
                definition.argument_classes,
                result,
                started_at,
            )
            return result

        remaining = context.remaining_seconds()
        if remaining is not None and remaining <= 0:
            result = self._failure(
                definition.name,
                definition.version,
                ToolErrorCode.DEADLINE_EXCEEDED,
                "AI 작업 deadline이 지나 도구를 실행하지 않았습니다.",
                retryable=False,
            )
            self._audit(
                context,
                definition.name,
                definition.version,
                definition.argument_classes,
                result,
                started_at,
            )
            return result

        timeout_seconds = definition.timeout_seconds
        if remaining is not None:
            timeout_seconds = min(timeout_seconds, remaining)

        try:
            outcome = await asyncio.wait_for(
                definition.handler(context, normalized_arguments),
                timeout=timeout_seconds,
            )
            result_errors = validate_json_schema(definition.output_schema, outcome)
            if result_errors:
                raise ToolInvocationError(
                    ToolErrorCode.INVALID_RESULT,
                    "도구 결과가 출력 계약과 일치하지 않습니다.",
                    details=tuple(result_errors),
                )
            result = ToolCallResult(
                tool_name=definition.name,
                tool_version=definition.version,
                ok=True,
                data=outcome,
            )
        except TimeoutError:
            result = self._failure(
                definition.name,
                definition.version,
                ToolErrorCode.DEADLINE_EXCEEDED,
                "도구 실행 시간이 초과되었습니다.",
                retryable=True,
            )
        except ToolInvocationError as exc:
            result = ToolCallResult(
                tool_name=definition.name,
                tool_version=definition.version,
                ok=False,
                error=exc.error,
            )
        except Exception:
            # Internal exception text may contain sensitive data and is not returned.
            result = self._failure(
                definition.name,
                definition.version,
                ToolErrorCode.INTERNAL_ERROR,
                "도구 실행 중 내부 오류가 발생했습니다.",
                retryable=False,
            )

        self._audit(
            context,
            definition.name,
            definition.version,
            definition.argument_classes,
            result,
            started_at,
        )
        return result

    @staticmethod
    def _failure(
        tool_name: str,
        tool_version: str,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: tuple[dict[str, Any], ...] = (),
    ) -> ToolCallResult:
        return ToolCallResult(
            tool_name=tool_name,
            tool_version=tool_version,
            ok=False,
            error=ToolError(
                code=code,
                message=message,
                retryable=retryable,
                details=details,
            ),
        )

    @staticmethod
    def _audit(
        context: ToolExecutionContext,
        tool_name: str,
        tool_version: str,
        argument_classes: tuple[str, ...],
        result: ToolCallResult,
        started_at: float,
    ) -> None:
        if context.audit_sink is None:
            return
        context.audit_sink(ToolAuditEvent(
            request_id=context.request_id,
            plan_id=context.plan_id,
            task_id=context.task_id,
            tool_name=tool_name,
            tool_version=tool_version,
            agent=context.agent,
            status="succeeded" if result.ok else "failed",
            duration_ms=max(0, int((monotonic() - started_at) * 1000)),
            argument_classes=argument_classes,
            call_source=context.call_source,
            error_code=result.error.code.value if result.error else None,
        ))


def validate_json_schema(
    schema: Mapping[str, Any],
    value: Any,
    path: str = "$",
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    expected_type = schema.get("type")

    if expected_type == "object":
        if not isinstance(value, dict):
            return [_schema_error(path, "type", "object가 필요합니다.")]
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append(_schema_error(
                    f"{path}.{required}",
                    "required",
                    "필수 인자입니다.",
                ))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(_schema_error(
                        f"{path}.{key}",
                        "additional_property",
                        "허용되지 않은 인자입니다.",
                    ))
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(validate_json_schema(
                    child_schema,
                    value[key],
                    f"{path}.{key}",
                ))
        return errors

    if expected_type == "array":
        if not isinstance(value, list):
            return [_schema_error(path, "type", "array가 필요합니다.")]
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            errors.extend(validate_json_schema(item_schema, item, f"{path}[{index}]"))
        return errors

    if expected_type == "string":
        if not isinstance(value, str):
            return [_schema_error(path, "type", "string이 필요합니다.")]
        if len(value) < schema.get("minLength", 0):
            errors.append(_schema_error(path, "min_length", "문자열이 너무 짧습니다."))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(_schema_error(path, "max_length", "문자열이 너무 깁니다."))
    elif expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [_schema_error(path, "type", "integer가 필요합니다.")]
    elif expected_type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            return [_schema_error(path, "type", "number가 필요합니다.")]
    elif expected_type == "boolean" and not isinstance(value, bool):
        return [_schema_error(path, "type", "boolean이 필요합니다.")]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(_schema_error(path, "enum", "허용되지 않은 값입니다."))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(_schema_error(path, "minimum", "최솟값보다 작습니다."))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(_schema_error(path, "maximum", "최댓값보다 큽니다."))
    return errors


def _schema_error(path: str, code: str, message: str) -> dict[str, Any]:
    return {"path": path, "code": code, "message": message}
