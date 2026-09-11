"""Mandatory data-handling policy for every production model call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
import re
from typing import Any

from core.masking import MaskingPath, MaskingPolicy, MaskingSink, mask_payload


prompt_policy_version = "1.0"
PROMPT_POLICY_VERSION = prompt_policy_version
DATA_HANDLING_PROMPT = f"""\
[공통 데이터 처리 정책 v{prompt_policy_version}]
- 현재 작업에 필요한 최소 입력만 사용하고 다른 목적에 재사용하지 않습니다.
- 이름·전화번호·이메일·계좌번호 등 직접 식별정보와 secret은 추론하거나 반복하지 않습니다.
- 허용된 금융 입력은 해당 작업의 계산·설명에만 사용하고 새 값으로 바꾸지 않습니다.
- 응답에 직접 식별정보나 secret을 포함하지 않습니다.
"""


class ModelPurpose(str, Enum):
    PLANNER = "planner"
    ROUTER = "router"
    QA = "qa"
    INPUT_ASSIST = "input_assist"
    ERROR_REVIEW = "error_review"
    CHART_INTERPRET = "chart_interpret"
    ELICIT = "elicit"


class ModelOutputBlockedError(ValueError):
    """Raised when a model attempts to return direct identifiers or secrets."""


_ALLOWED_INPUT_PREFIXES: dict[ModelPurpose, tuple[MaskingPath, ...]] = {
    ModelPurpose.PLANNER: (),
    ModelPurpose.ROUTER: (),
    ModelPurpose.QA: (("customer_info",), ("bound_inputs",), ("evidence",)),
    ModelPurpose.INPUT_ASSIST: (("customer_info",), ("bound_inputs",)),
    ModelPurpose.ERROR_REVIEW: (("current_form",),),
    ModelPurpose.CHART_INTERPRET: (("chart_payload",),),
    ModelPurpose.ELICIT: (),
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


def secure_invoke(
    runnable: Any,
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    sensitive_payload: Any | None = None,
) -> Any:
    safe_messages, _ = _prepare_call(
        messages,
        purpose=purpose,
        sensitive_payload=sensitive_payload,
    )
    result = runnable.invoke(safe_messages)
    _block_forbidden_output(result)
    return result


async def secure_ainvoke(
    runnable: Any,
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    sensitive_payload: Any | None = None,
) -> Any:
    safe_messages, _ = _prepare_call(
        messages,
        purpose=purpose,
        sensitive_payload=sensitive_payload,
    )
    result = await runnable.ainvoke(safe_messages)
    _block_forbidden_output(result)
    return result


def _prepare_call(
    messages: Sequence[Any],
    *,
    purpose: ModelPurpose,
    sensitive_payload: Any | None,
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
            if forbidden and child not in (None, ""):
                return True
            if _contains_forbidden_field(child):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_forbidden_field(child) for child in value)
    return False
