"""Versioned prompt specifications and deterministic payload rendering."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any


_SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
_PROMPT_TRACE_SINK: ContextVar[Any | None] = ContextVar(
    "prompt_trace_sink",
    default=None,
)


@dataclass(frozen=True, slots=True)
class PromptInvocationRecord:
    """Content-free metadata for one physical provider invocation."""

    prompt_id: str
    prompt_version: str
    prompt_hash: str
    prompt_release: str
    prompt_policy_version: str
    purpose: str
    model_provider: str
    model_name: str
    model_temperature: float
    status: str
    duration_ms: int
    error_code: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "prompt_id",
            "prompt_version",
            "prompt_release",
            "prompt_policy_version",
            "purpose",
            "model_provider",
            "model_name",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
                raise ValueError(f"{name} must be a safe machine token")
        if not isinstance(self.prompt_hash, str) or _SHA256.fullmatch(
            self.prompt_hash
        ) is None:
            raise ValueError("prompt_hash must be a lowercase SHA-256 digest")
        if (
            isinstance(self.model_temperature, bool)
            or not isinstance(self.model_temperature, (int, float))
            or not 0 <= self.model_temperature <= 1
        ):
            raise ValueError("model_temperature must be between 0 and 1")
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("status must be succeeded or failed")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be a non-negative integer")
        if self.status == "succeeded" and self.error_code is not None:
            raise ValueError("successful prompt invocation cannot contain error_code")
        if self.status == "failed" and (
            not isinstance(self.error_code, str)
            or _SAFE_TOKEN.fullmatch(self.error_code) is None
        ):
            raise ValueError("failed prompt invocation requires a safe error_code")


@contextmanager
def bind_prompt_trace_sink(sink: Any):
    """Bind request-scoped typed prompt observation across nested async calls."""

    if not callable(sink):
        raise TypeError("prompt trace sink must be callable")
    token = _PROMPT_TRACE_SINK.set(sink)
    try:
        yield
    finally:
        _PROMPT_TRACE_SINK.reset(token)


def emit_prompt_invocation(record: PromptInvocationRecord) -> bool:
    """Emit to the bound typed sink and report whether one was available."""

    sink = _PROMPT_TRACE_SINK.get()
    if sink is None:
        return False
    sink(record)
    return True


@dataclass(frozen=True, slots=True)
class PromptSpec:
    """One immutable prompt contract in the current deployment image."""

    prompt_id: str
    version: str
    purpose: str
    system_text: str
    required_input_keys: frozenset[str]
    optional_input_keys: frozenset[str] = frozenset()
    output_schema: str | None = None
    owner: str = "ai-team"
    risk: str = "standard"
    input_sections: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("prompt_id", self.prompt_id),
            ("purpose", self.purpose),
            ("owner", self.owner),
            ("risk", self.risk),
        ):
            if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
                raise ValueError(f"{name} must be a safe machine identifier")
        if _SEMVER.fullmatch(self.version) is None:
            raise ValueError("prompt version must use semantic versioning")
        if not isinstance(self.system_text, str) or not self.system_text.strip():
            raise ValueError("system_text must be a non-empty string")
        if self.required_input_keys & self.optional_input_keys:
            raise ValueError("required and optional prompt inputs must not overlap")
        for key in self.required_input_keys | self.optional_input_keys:
            if not isinstance(key, str) or _SAFE_ID.fullmatch(key) is None:
                raise ValueError("prompt input keys must be safe machine identifiers")
        if self.output_schema is not None and (
            not isinstance(self.output_schema, str)
            or _SAFE_ID.fullmatch(self.output_schema) is None
        ):
            raise ValueError("output_schema must be a safe machine identifier")
        section_keys: list[str] = []
        for section in self.input_sections:
            if (
                not isinstance(section, tuple)
                or len(section) != 2
                or not isinstance(section[0], str)
                or not isinstance(section[1], str)
                or not section[1].strip()
            ):
                raise ValueError("input_sections must contain key/label string pairs")
            section_keys.append(section[0])
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("input_sections must not repeat payload keys")
        if set(section_keys) != self.required_input_keys | self.optional_input_keys:
            raise ValueError("input_sections must cover every prompt payload key exactly")

    @property
    def content_hash(self) -> str:
        return sha256(self.system_text.strip().encode("utf-8")).hexdigest()

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise TypeError("prompt payload must be a mapping")
        actual = set(payload)
        missing = self.required_input_keys - actual
        unknown = actual - self.required_input_keys - self.optional_input_keys
        if missing:
            raise ValueError(
                f"{self.prompt_id} payload is missing keys: {sorted(missing)}"
            )
        if unknown:
            raise ValueError(
                f"{self.prompt_id} payload has unknown keys: {sorted(unknown)}"
            )
        rendered = dict(payload)
        try:
            json.dumps(
                rendered,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("prompt payload must be JSON-serializable") from error
        return rendered


def prompt_manifest(
    prompts: Mapping[str, PromptSpec],
    *,
    release: str,
) -> dict[str, Any]:
    """Return the non-secret release manifest used by traces and eval artifacts."""

    return {
        "release": release,
        "prompts": [
            {
                "id": prompt.prompt_id,
                "version": prompt.version,
                "sha256": prompt.content_hash,
                "purpose": prompt.purpose,
                "output_schema": prompt.output_schema,
                "owner": prompt.owner,
                "risk": prompt.risk,
                "input_sections": [
                    {"key": key, "label": label}
                    for key, label in prompt.input_sections
                ],
            }
            for prompt in sorted(prompts.values(), key=lambda item: item.prompt_id)
        ],
    }
