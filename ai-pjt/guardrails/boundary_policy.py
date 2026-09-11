"""Deterministic production policies for untrusted text boundaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any


BOUNDARY_POLICY_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class BoundaryDecision:
    decision: str
    reason_code: str
    effects: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


_DIRECT_OVERRIDE_PATTERNS = (
    re.compile(r"(?:이전|상위|시스템).{0,20}(?:지시|규칙|정책).{0,12}무시", re.I),
    re.compile(r"ignore\s+(?:all|previous|system)\s+(?:rules|instructions|polic(?:y|ies))", re.I),
)
_SECRET_REQUEST_PATTERNS = (
    re.compile(r"(?:시스템\s*프롬프트|system\s*prompt)", re.I),
    re.compile(r"(?:secret|비밀|api[_ -]?key|token)", re.I),
)
_OBFUSCATED_PATTERNS = (
    re.compile(r"base64[_ :\-].{8,}", re.I),
    re.compile(r"(?:가상|역할극|role\s*play).{0,24}(?:관리자|admin|정책이\s*없는)", re.I),
    re.compile(r"ignore\s+all\s+rules.*(?:다른\s*세션|別|表示|mostrar)", re.I),
)
_RAG_INSTRUCTION_PATTERNS = (
    re.compile(r"\[(?:지시|instruction|system)\]", re.I),
    re.compile(r"(?:모든|이전|상위).{0,16}(?:정책|지시|규칙).{0,12}무시", re.I),
    re.compile(r"ignore\s+(?:all|previous).{0,16}(?:rules|instructions|policy)", re.I),
)
_FINANCIAL_CERTAINTY_PATTERNS = (
    re.compile(r"(?:무조건|반드시|확실히).{0,24}(?:수익|이익|오릅니다|보장)", re.I),
    re.compile(r"(?:수익|이익|원금).{0,24}(?:확정|보장됩니다|보장한다)", re.I),
    re.compile(r"\d+(?:\.\d+)?\s*%.{0,24}(?:확정|보장)", re.I),
)
_UNCERTAINTY_PATTERNS = (
    re.compile(r"보장(?:하지|할\s*수\s*없)", re.I),
    re.compile(r"(?:가정|예상|가능성|불확실|달라질\s*수)", re.I),
)


def evaluate_user_message(message: str) -> BoundaryDecision:
    """Block instruction overrides before planner/model/tool execution."""

    if not isinstance(message, str) or not message.strip():
        return BoundaryDecision(
            "block",
            "INVALID_USER_MESSAGE",
            ("safe_refusal", "audit_security_event"),
        )
    if any(pattern.search(message) for pattern in _OBFUSCATED_PATTERNS):
        return BoundaryDecision(
            "block",
            "OBFUSCATED_BYPASS_BLOCKED",
            ("safe_refusal", "audit_security_event"),
        )
    overrides = any(pattern.search(message) for pattern in _DIRECT_OVERRIDE_PATTERNS)
    requests_secret = any(
        pattern.search(message) for pattern in _SECRET_REQUEST_PATTERNS
    )
    if overrides or (requests_secret and re.search(r"(?:출력|공개|말해|show|reveal)", message, re.I)):
        return BoundaryDecision(
            "block",
            "PROMPT_INJECTION_BLOCKED",
            ("safe_refusal", "audit_security_event"),
        )
    return BoundaryDecision(
        "allow",
        "USER_REQUEST_ALLOWED",
        ("answer_current_request",),
    )


def evaluate_rag_content(content: str) -> BoundaryDecision:
    """Treat retrieved prose as evidence only and reject embedded instructions."""

    if not isinstance(content, str) or not content.strip():
        return BoundaryDecision(
            "block",
            "RAG_CONTENT_INVALID",
            ("ignore_embedded_instruction",),
        )
    if any(pattern.search(content) for pattern in _RAG_INSTRUCTION_PATTERNS):
        return BoundaryDecision(
            "block",
            "RAG_INSTRUCTION_IGNORED",
            ("ignore_embedded_instruction", "answer_from_cited_content"),
        )
    return BoundaryDecision(
        "allow",
        "RAG_CONTENT_ALLOWED",
        ("answer_from_cited_content",),
    )


def filter_rag_results(results: Sequence[Any]) -> list[Any]:
    """Remove poisoned chunks without mutating approved result objects."""

    return [
        result
        for result in results
        if evaluate_rag_content(_rag_text(result)).allowed
    ]


def evaluate_financial_output(payload: Any) -> BoundaryDecision:
    """Reject unsupported certainty in actual model output text."""

    rendered = "\n".join(_text_values(payload))
    certainty = any(
        pattern.search(rendered) for pattern in _FINANCIAL_CERTAINTY_PATTERNS
    )
    bounded = any(pattern.search(rendered) for pattern in _UNCERTAINTY_PATTERNS)
    if certainty and not bounded:
        return BoundaryDecision(
            "block",
            "FINANCIAL_OVERCLAIM_BLOCKED",
            ("avoid_guarantee", "provide_uncertainty_notice"),
        )
    return BoundaryDecision(
        "allow",
        "BOUNDED_FINANCIAL_GUIDANCE_ALLOWED",
        ("provide_bounded_financial_guidance",),
    )


def _rag_text(result: Any) -> str:
    chunk = getattr(result, "chunk", None)
    text = getattr(chunk, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(result, Mapping):
        for key in ("text", "content", "excerpt"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    node = getattr(result, "node", None)
    getter = getattr(node, "get_content", None)
    if callable(getter):
        value = getter()
        return value if isinstance(value, str) else ""
    return ""


def _text_values(value: Any) -> list[str]:
    if isinstance(value, bytes):
        return [value.decode("utf-8", errors="replace")]
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        values: list[str] = []
        for child in value.values():
            values.extend(_text_values(child))
        return values
    if isinstance(value, Sequence):
        values = []
        for child in value:
            values.extend(_text_values(child))
        return values
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        return _text_values(dumper(mode="json"))
    return []
