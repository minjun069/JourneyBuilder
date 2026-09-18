"""Mandatory data-handling policy for every production model call."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
import json
import logging
import re
from time import perf_counter
from typing import Any, TypeVar

from pydantic import BaseModel

from core.masking import (
    MaskingPath,
    MaskingPolicy,
    MaskingSink,
    is_reference_token,
    mask_payload,
)
from core.prompt_registry import (
    PromptInvocationRecord,
    PromptSpec,
    emit_prompt_invocation,
)


prompt_policy_version = "1.1"
PROMPT_POLICY_VERSION = prompt_policy_version
DATA_HANDLING_PROMPT = f"""\
[공통 데이터 처리 정책 v{prompt_policy_version}]
- 현재 작업에 필요한 최소 입력만 사용하고 다른 목적에 재사용하지 않습니다.
- 이름·전화번호·이메일·계좌번호 등 직접 식별정보와 secret은 추론하거나 반복하지 않습니다.
- 허용된 금융 입력은 해당 작업의 계산·설명에만 사용하고 새 값으로 바꾸지 않습니다.
- `[[...]]` 형식의 참조 token은 철자와 구분을 바꾸지 않고 필요한 위치에 그대로 사용합니다.
- 응답에 직접 식별정보나 secret을 포함하지 않습니다.
"""


class ModelPurpose(str, Enum):
    PLANNER = "planner"
    AGGREGATOR = "aggregator"
    QA = "qa"
    INPUT_ASSIST = "input_assist"
    ERROR_REVIEW = "error_review"
    CHART_INTERPRET = "chart_interpret"
    ELICIT = "elicit"
    SUMMARY = "summary"


class ModelOutputBlockedError(ValueError):
    """Raised when a model attempts to return direct identifiers or secrets."""

    def __init__(self, message: str, *, reason_code: str = "DATA_POLICY_BLOCKED") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class StructuredModelOutputError(RuntimeError):
    """Raised after bounded structured-output attempts are exhausted."""


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
ModelCallRecorder = Callable[..., None]


_MODEL_CALL_RECORDER: ContextVar[ModelCallRecorder | None] = ContextVar(
    "model_call_recorder",
    default=None,
)
logger = logging.getLogger(__name__)


_ALLOWED_INPUT_PREFIXES: dict[ModelPurpose, tuple[MaskingPath, ...]] = {
    ModelPurpose.PLANNER: (),
    ModelPurpose.AGGREGATOR: (("response_blocks",),),
    ModelPurpose.QA: (("customer_info",), ("bound_inputs",), ("evidence",)),
    ModelPurpose.INPUT_ASSIST: (("customer_info",), ("bound_inputs",)),
    ModelPurpose.ERROR_REVIEW: (("current_form",),),
    ModelPurpose.CHART_INTERPRET: (("chart_payload",),),
    ModelPurpose.ELICIT: (),
    ModelPurpose.SUMMARY: (),
}
_DIRECT_TEXT_PATTERNS = (
    re.compile(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    ),
    re.compile(r"(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"),
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
    ),
)
_FORBIDDEN_OUTPUT_FIELDS = frozenset({
    "account_identifier",
    "account_id",
    "account_number",
    "address",
    "authorization",
    "bearer",
    "cookie",
    "customer_id",
    "customer_name",
    "date_of_birth",
    "email",
    "email_address",
    "full_name",
    "name",
    "password",
    "phone",
    "phone_number",
    "resident_registration_number",
    "rrn",
    "secret",
    "token",
    "user_id",
    "user_name",
})


def secure_system_prompt(role_prompt: str) -> str:
    if not isinstance(role_prompt, str) or not role_prompt.strip():
        raise ValueError("role_prompt must be a non-empty string")
    if DATA_HANDLING_PROMPT in role_prompt:
        return role_prompt
    return f"{DATA_HANDLING_PROMPT}\n{role_prompt.strip()}"


def prompt_messages(prompt: PromptSpec, payload: Mapping[str, Any]) -> list[Any]:
    """Render a static System message and a schema-checked JSON Human payload."""

    from langchain_core.messages import HumanMessage, SystemMessage

    validated = prompt.validate_payload(payload)
    human_content = json.dumps(
        {"runtime_payload": validated},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return [
        SystemMessage(content=secure_system_prompt(prompt.system_text)),
        HumanMessage(content=human_content),
    ]


def mask_model_payload(
    payload: Any,
    *,
    purpose: ModelPurpose,
    pseudonym_key: bytes | None = None,
) -> Any:
    """Mask structured model input with purpose-specific exact leaf exceptions."""

    if not isinstance(purpose, ModelPurpose):
        raise ValueError("purpose must be a ModelPurpose")
    key = pseudonym_key if pseudonym_key is not None else _runtime_key()
    allowed_paths = frozenset(
        path
        for path in _leaf_paths(payload)
        if any(path[: len(prefix)] == prefix for prefix in _ALLOWED_INPUT_PREFIXES[purpose])
    )
    return mask_payload(
        payload,
        sink=MaskingSink.MODEL_INPUT,
        policy=MaskingPolicy(
            pseudonym_key=key,
            allowed_model_paths=allowed_paths,
        ),
    )


@contextmanager
def bind_model_call_recorder(
    recorder: ModelCallRecorder | None,
) -> Iterator[None]:
    """Bind request-scoped observation without coupling agents to Langfuse."""

    if recorder is not None and not callable(recorder):
        raise TypeError("model call recorder must be callable")
    token = _MODEL_CALL_RECORDER.set(recorder)
    try:
        yield
    finally:
        _MODEL_CALL_RECORDER.reset(token)


def secure_invoke(
    runnable: Any,
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    operation_name: str | None = None,
    sensitive_payload: Any | None = None,
    prompt: PromptSpec | None = None,
) -> Any:
    safe_messages, policy = _prepare_call(
        messages,
        purpose=purpose,
        sensitive_payload=sensitive_payload,
        prompt=prompt,
    )
    trace_replacements, observation_input = _prepare_observation_input(
        safe_messages,
        sensitive_payload=sensitive_payload,
        policy=policy,
    )
    started = perf_counter()
    try:
        result = runnable.invoke(safe_messages)
        _block_forbidden_output(result)
    except Exception as error:
        _record_model_call(
            purpose=purpose,
            operation_name=operation_name,
            status="failed",
            duration_ms=_elapsed_ms(started),
            input=observation_input,
            output={"status": "failed"},
            prompt=prompt,
            error=error,
        )
        _emit_prompt_invocation(prompt, purpose, started, error=error)
        raise
    _record_model_call(
        purpose=purpose,
        operation_name=operation_name,
        status="succeeded",
        duration_ms=_elapsed_ms(started),
        input=observation_input,
        output=_safe_output_observation_value(
            result,
            replacements=trace_replacements,
            policy=policy,
        ),
        prompt=prompt,
    )
    _emit_prompt_invocation(prompt, purpose, started)
    return result


def secure_structured_invoke(
    model: Any,
    schema: type[StructuredModel],
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    operation_name: str | None = None,
    sensitive_payload: Any | None = None,
    max_attempts: int = 2,
    prompt: PromptSpec | None = None,
) -> StructuredModel:
    """Invoke a provider-enforced schema with one bounded retry by default.

    The provider adapter owns JSON/tool-call parsing. Every returned value is
    validated again with Pydantic so markdown fences, explanatory prose,
    missing fields, and invalid enum values never reach planner or aggregation policy.
    Model calls are read-only, so retrying a transient or parsing failure once
    cannot duplicate a business side effect.
    """

    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise TypeError("schema must be a pydantic BaseModel type")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError("max_attempts must be an integer")
    if max_attempts < 1 or max_attempts > 2:
        raise ValueError("max_attempts must be between 1 and 2")

    try:
        runnable = model.with_structured_output(schema)
    except Exception as error:  # noqa: BLE001 - normalize provider adapters
        raise StructuredModelOutputError(
            "model does not support the required structured-output contract"
        ) from error

    last_error: Exception | None = None
    for _attempt in range(max_attempts):
        try:
            result = secure_invoke(
                runnable,
                messages,
                purpose=purpose,
                operation_name=operation_name,
                sensitive_payload=sensitive_payload,
                prompt=prompt,
            )
            if isinstance(result, schema):
                return result
            return schema.model_validate(result)
        except Exception as error:  # noqa: BLE001 - bounded model retry
            last_error = error

    raise StructuredModelOutputError(
        "model did not return a valid structured response"
    ) from last_error


async def secure_ainvoke(
    runnable: Any,
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    operation_name: str | None = None,
    sensitive_payload: Any | None = None,
    prompt: PromptSpec | None = None,
) -> Any:
    safe_messages, policy = _prepare_call(
        messages,
        purpose=purpose,
        sensitive_payload=sensitive_payload,
        prompt=prompt,
    )
    trace_replacements, observation_input = _prepare_observation_input(
        safe_messages,
        sensitive_payload=sensitive_payload,
        policy=policy,
    )
    started = perf_counter()
    try:
        result = await runnable.ainvoke(safe_messages)
        _block_forbidden_output(result)
    except Exception as error:
        _record_model_call(
            purpose=purpose,
            operation_name=operation_name,
            status="failed",
            duration_ms=_elapsed_ms(started),
            input=observation_input,
            output={"status": "failed"},
            prompt=prompt,
            error=error,
        )
        _emit_prompt_invocation(prompt, purpose, started, error=error)
        raise
    _record_model_call(
        purpose=purpose,
        operation_name=operation_name,
        status="succeeded",
        duration_ms=_elapsed_ms(started),
        input=observation_input,
        output=_safe_output_observation_value(
            result,
            replacements=trace_replacements,
            policy=policy,
        ),
        prompt=prompt,
    )
    _emit_prompt_invocation(prompt, purpose, started)
    return result


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _prepare_observation_input(
    messages: Sequence[Any],
    *,
    sensitive_payload: Any | None,
    policy: MaskingPolicy,
) -> tuple[tuple[tuple[str, str], ...], Any]:
    try:
        replacements = _trace_observation_replacements(
            sensitive_payload,
            policy=policy,
        )
        return replacements, _message_observation_input(
            messages,
            replacements=replacements,
            policy=policy,
        )
    except Exception:
        return (), {"status": "observation_unavailable"}


def _message_observation_input(
    messages: Sequence[Any],
    *,
    replacements: tuple[tuple[str, str], ...],
    policy: MaskingPolicy,
) -> list[dict[str, Any]]:
    role_aliases = {
        "ai": "assistant",
        "human": "user",
    }
    captured: list[dict[str, Any]] = []
    for message in messages:
        raw_role = getattr(message, "type", "message")
        role = role_aliases.get(raw_role, raw_role)
        if role not in {"assistant", "system", "tool", "user"}:
            role = "message"
        content = _replace_literals(
            getattr(message, "content", None),
            replacements,
        )
        content = mask_payload(
            {"content": content},
            sink=MaskingSink.TRACE_EXPORT,
            policy=policy,
        )["content"]
        captured.append({
            "role": role,
            "content": _observation_value(content),
        })
    return captured


def _trace_observation_replacements(
    sensitive_payload: Any | None,
    *,
    policy: MaskingPolicy,
) -> tuple[tuple[str, str], ...]:
    if sensitive_payload is None:
        return ()
    trace_safe_payload = mask_payload(
        sensitive_payload,
        sink=MaskingSink.TRACE_EXPORT,
        policy=policy,
    )
    return _replacement_pairs(sensitive_payload, trace_safe_payload)


def _output_observation_value(
    result: Any,
    *,
    replacements: tuple[tuple[str, str], ...],
    policy: MaskingPolicy,
) -> Any:
    if hasattr(result, "content"):
        output: Any = {
            "role": "assistant",
            "content": _observation_value(getattr(result, "content", None)),
        }
    else:
        output = _observation_value(result)
    output = _replace_literals(output, replacements)
    return mask_payload(
        {"content": output},
        sink=MaskingSink.TRACE_EXPORT,
        policy=policy,
    )["content"]


def _safe_output_observation_value(
    result: Any,
    *,
    replacements: tuple[tuple[str, str], ...],
    policy: MaskingPolicy,
) -> Any:
    try:
        return _output_observation_value(
            result,
            replacements=replacements,
            policy=policy,
        )
    except Exception:
        return {"status": "observation_unavailable"}


def _observation_value(value: Any) -> Any:
    """Return only JSON-shaped values; never fall back to arbitrary repr/str."""

    if isinstance(value, BaseModel):
        return _observation_value(value.model_dump(mode="json"))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {
            str(key): _observation_value(child)
            for key, child in value.items()
        }
    if isinstance(value, Sequence):
        return [_observation_value(child) for child in value]
    return {"type": "unsupported"}


def _record_model_call(
    *,
    purpose: ModelPurpose,
    operation_name: str | None,
    status: str,
    duration_ms: int,
    input: Any,
    output: Any,
    prompt: PromptSpec | None,
    error: BaseException | None = None,
) -> None:
    recorder = _MODEL_CALL_RECORDER.get()
    if recorder is None:
        return
    try:
        from core.config import settings

        error_identity: dict[str, str] = {}
        if error is not None:
            from core.observability_masking import exception_identity_fields

            error_identity = exception_identity_fields(error)
        prompt_metadata: dict[str, Any] = {
            "model_provider": settings.llm_provider,
            "model_name": settings.llm_model,
            "model_temperature": float(
                getattr(settings, "llm_temperature", 0.0)
            ),
            "prompt_policy_version": prompt_policy_version,
        }
        if prompt is not None:
            from prompts.catalog import selected_prompt_release

            prompt_metadata.update({
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.version,
                "prompt_hash": prompt.content_hash,
                "prompt_release": selected_prompt_release(),
            })
        recorder(
            purpose=purpose.value,
            operation_name=operation_name or f"{purpose.value}.generate",
            status=status,
            duration_ms=duration_ms,
            input=input,
            output=output,
            error_code=(type(error).__name__ if error is not None else None),
            **prompt_metadata,
            **error_identity,
        )
    except Exception:
        # Telemetry must never alter model-call behavior.
        return


def _prepare_call(
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    sensitive_payload: Any | None,
    prompt: PromptSpec | None,
) -> tuple[list[Any], MaskingPolicy]:
    if not isinstance(purpose, ModelPurpose):
        raise ValueError("purpose must be a ModelPurpose")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise TypeError("messages must be a sequence")
    message_list = list(messages)
    if not message_list:
        raise ValueError("model call requires messages")
    system_content = getattr(message_list[0], "content", None)
    if not isinstance(system_content, str) or DATA_HANDLING_PROMPT not in system_content:
        raise ValueError("model call is missing DATA_HANDLING_PROMPT")
    if prompt is not None:
        if prompt.purpose != purpose.value:
            raise ValueError("prompt purpose does not match model purpose")
        if system_content != secure_system_prompt(prompt.system_text):
            raise ValueError("System message must be the static registered prompt")

    key = _runtime_key()
    policy = MaskingPolicy(pseudonym_key=key)
    replacements: tuple[tuple[str, str], ...] = ()
    if sensitive_payload is not None:
        safe_payload = mask_model_payload(
            sensitive_payload,
            purpose=purpose,
            pseudonym_key=key,
        )
        replacements = _replacement_pairs(sensitive_payload, safe_payload)
    return [
        _copy_message(
            message,
            _mask_message_content(
                getattr(message, "content", ""),
                policy=policy,
                replacements=replacements,
            ),
        )
        for message in message_list
    ], policy


def _emit_prompt_invocation(
    prompt: PromptSpec | None,
    purpose: ModelPurpose,
    started: float,
    *,
    error: Exception | None = None,
) -> None:
    if prompt is None:
        return
    if _MODEL_CALL_RECORDER.get() is not None:
        # The model recorder already emits content and prompt metadata together.
        return
    from core.config import settings
    from prompts.catalog import selected_prompt_release

    record = PromptInvocationRecord(
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        prompt_release=selected_prompt_release(),
        prompt_policy_version=prompt_policy_version,
        purpose=purpose.value,
        model_provider=settings.llm_provider,
        model_name=settings.llm_model,
        model_temperature=float(getattr(settings, "llm_temperature", 0.0)),
        status="failed" if error is not None else "succeeded",
        duration_ms=max(0, int((perf_counter() - started) * 1000)),
        error_code=(type(error).__name__ if error is not None else None),
    )
    if emit_prompt_invocation(record):
        return
    logger.info(
        "unbound prompt invocation",
        extra={
            "prompt_invocation": {
                "prompt_id": record.prompt_id,
                "prompt_version": record.prompt_version,
                "prompt_hash": record.prompt_hash,
                "prompt_release": record.prompt_release,
                "prompt_policy_version": record.prompt_policy_version,
                "purpose": record.purpose,
                "model_provider": record.model_provider,
                "model_name": record.model_name,
                "model_temperature": record.model_temperature,
                "status": record.status,
                "duration_ms": record.duration_ms,
                "error_code": record.error_code,
            }
        },
    )


def _runtime_key() -> bytes:
    from core.config import settings

    return settings.masking_pseudonym_key_bytes


def _copy_message(message: Any, content: Any) -> Any:
    copier = getattr(message, "model_copy", None)
    if not callable(copier):
        raise TypeError("model messages must support model_copy")
    return copier(update={"content": content})


def _mask_message_content(
    content: Any,
    *,
    policy: MaskingPolicy,
    replacements: tuple[tuple[str, str], ...],
) -> Any:
    replaced = _replace_literals(content, replacements)
    masked = mask_payload(
        {"content": replaced},
        sink=MaskingSink.MODEL_INPUT,
        policy=policy,
    )["content"]
    return _redact_direct_text(masked)


def _replace_literals(
    value: Any,
    replacements: tuple[tuple[str, str], ...],
) -> Any:
    if isinstance(value, str):
        for raw, safe in replacements:
            value = value.replace(raw, safe)
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _replace_literals(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_literals(child, replacements) for child in value]
    return value


def _replacement_pairs(raw: Any, safe: Any) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()

    def visit(left: Any, right: Any) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for (left_key, left_value), (right_key, right_value) in zip(
                left.items(),
                right.items(),
                strict=False,
            ):
                if left_key != right_key:
                    visit(left_key, right_key)
                visit(left_value, right_value)
            return
        if (
            isinstance(left, Sequence)
            and not isinstance(left, (str, bytes))
            and isinstance(right, Sequence)
            and not isinstance(right, (str, bytes))
        ):
            for left_item, right_item in zip(left, right, strict=False):
                visit(left_item, right_item)
            return
        if left == right or left is None or isinstance(left, bool):
            return
        raw_text = (
            left.decode("utf-8", errors="replace")
            if isinstance(left, bytes)
            else str(left)
        )
        safe_text = (
            right.decode("utf-8", errors="replace")
            if isinstance(right, bytes)
            else str(right)
        )
        if len(raw_text) >= 3:
            pairs.add((raw_text, safe_text))

    visit(raw, safe)
    return tuple(sorted(pairs, key=lambda item: len(item[0]), reverse=True))


def _leaf_paths(value: Any, path: MaskingPath = ()) -> tuple[MaskingPath, ...]:
    if isinstance(value, Mapping):
        paths: list[MaskingPath] = []
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            paths.extend(_leaf_paths(child, (*path, key)))
        return tuple(paths)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        paths = []
        for index, child in enumerate(value):
            paths.extend(_leaf_paths(child, (*path, index)))
        return tuple(paths)
    return (path,) if path else ()


def _block_forbidden_output(result: Any) -> None:
    payload = _serializable_output(result)
    if _contains_forbidden_text(payload) or _contains_forbidden_field(payload):
        raise ModelOutputBlockedError(
            "model output contained a direct identifier or secret"
        )
    from guardrails.boundary_policy import evaluate_financial_output

    decision = evaluate_financial_output(payload)
    if not decision.allowed:
        raise ModelOutputBlockedError(
            "model output contained unsupported financial certainty",
            reason_code=decision.reason_code,
        )


def _serializable_output(result: Any) -> Any:
    dumper = getattr(result, "model_dump", None)
    if callable(dumper):
        return dumper(mode="json")
    if isinstance(result, (str, bytes, int, float, bool)) or result is None:
        return result
    if isinstance(result, Mapping):
        return dict(result)
    if isinstance(result, Sequence):
        return list(result)
    raise ModelOutputBlockedError("model output could not be inspected safely")


def _redact_direct_text(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _DIRECT_TEXT_PATTERNS:
            value = pattern.sub("[DIRECT_IDENTIFIER]", value)
        for pattern in _SECRET_TEXT_PATTERNS:
            value = pattern.sub("[SECRET]", value)
        return value
    if isinstance(value, Mapping):
        return {key: _redact_direct_text(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_direct_text(child) for child in value]
    return value


def _contains_forbidden_text(value: Any) -> bool:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        patterns = (*_DIRECT_TEXT_PATTERNS, *_SECRET_TEXT_PATTERNS)
        if any(pattern.search(value) for pattern in patterns):
            return True
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            import json

            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                return False
            return _contains_forbidden_field(decoded)
        return False
    if isinstance(value, Mapping):
        return any(_contains_forbidden_text(child) for child in value.values())
    if isinstance(value, Sequence):
        return any(_contains_forbidden_text(child) for child in value)
    return False


def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            forbidden = (
                normalized in _FORBIDDEN_OUTPUT_FIELDS
                or normalized.endswith("_email")
                or normalized.endswith("_phone")
                or normalized.endswith("_account_number")
                or normalized.endswith("_api_key")
                or normalized.endswith("_password")
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
            )
            if forbidden and child not in (None, "") and not is_reference_token(child):
                return True
            if _contains_forbidden_field(child):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_forbidden_field(child) for child in value)
    return False
