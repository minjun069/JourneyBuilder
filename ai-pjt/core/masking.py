"""Deterministic, sink-aware masking for nested AI runtime payloads.

The core accepts JSON-like values and returns a new value with the same
container shape. Runtime adapters remain responsible for serializing that
value immediately before a model, callback, log, or trace boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import hmac
from math import isfinite
import re
from typing import Any, TypeAlias


PathPart: TypeAlias = str | int
MaskingPath: TypeAlias = tuple[PathPart, ...]


class DataClass(str, Enum):
    """Project data classes enforced by the production masking boundary."""

    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    PROJECT_HIGH_RISK_FINANCIAL = "project_high_risk_financial"
    OPERATIONAL_INFORMATION = "operational_information"
    SECRET = "secret"


class MaskingSink(str, Enum):
    """Externalization boundaries supported by the masking core."""

    MODEL_INPUT = "model_input"
    MODEL_OUTPUT = "model_output"
    CALLBACK = "callback"
    APPLICATION_LOG = "application_log"
    TRACE_EXPORT = "trace_export"


class MaskingPolicyError(ValueError):
    """Raised when a payload cannot be masked without weakening the policy."""


@dataclass(frozen=True, slots=True)
class MaskingPolicy:
    """Required key material and explicit model-use exceptions.

    ``allowed_model_paths`` contains exact leaf paths. Direct identifiers,
    secrets, and operational identifiers cannot be allowed through it.
    """

    pseudonym_key: bytes = field(repr=False)
    allowed_model_paths: frozenset[MaskingPath] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.pseudonym_key, bytes) or len(self.pseudonym_key) < 32:
            raise MaskingPolicyError("pseudonym_key must contain at least 32 bytes")
        if not isinstance(self.allowed_model_paths, frozenset):
            raise MaskingPolicyError("allowed_model_paths must be a frozenset")
        for path in self.allowed_model_paths:
            if not isinstance(path, tuple) or not path:
                raise MaskingPolicyError(
                    "allowed_model_paths must contain non-empty tuple paths"
                )
            if any(
                isinstance(part, bool) or not isinstance(part, (str, int))
                for part in path
            ):
                raise MaskingPolicyError(
                    "model allowlist paths may contain only string and integer parts"
                )


@dataclass(frozen=True, slots=True)
class _FieldDecision:
    data_class: DataClass | None = None
    namespace: str | None = None
    sensitive_container: bool = False


_DIRECT_IDENTIFIER_TOKEN = "[DIRECT_IDENTIFIER]"
_QUASI_IDENTIFIER_TOKEN = "[QUASI_IDENTIFIER_BUCKETED]"
_FINANCIAL_TOKEN = "[FINANCIAL_VALUE_MASKED]"
_SECRET_TOKEN = "[SECRET]"
_FREE_TEXT_TOKEN = "[FREE_TEXT_REDACTED]"

_OBSERVABILITY_SINKS = frozenset(
    {MaskingSink.APPLICATION_LOG, MaskingSink.TRACE_EXPORT}
)
_MODEL_SINKS = frozenset({MaskingSink.MODEL_INPUT, MaskingSink.MODEL_OUTPUT})
_CALLBACK_ROOT_CORRELATION_PATHS = frozenset(
    {("request_id",), ("session_id",), ("thread_id",)}
)

_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "access_key",
        "authorization",
        "bearer",
        "cookie",
        "password",
        "refresh_token",
        "secret",
        "service_token",
        "set_cookie",
        "token",
    }
)
_DIRECT_IDENTIFIER_FIELDS = frozenset(
    {
        "account_identifier",
        "account_id",
        "account_number",
        "address",
        "contact_number",
        "customer_id",
        "customer_name",
        "date_of_birth",
        "email",
        "email_address",
        "full_name",
        "name",
        "phone",
        "phone_number",
        "resident_registration_number",
        "rrn",
        "user_id",
        "user_name",
    }
)
_QUASI_IDENTIFIER_FIELDS = frozenset(
    {
        "age",
        "age_job_family_profile",
        "birth_date",
        "family_composition",
        "family_size",
        "gender",
        "household_size",
        "job",
        "marital_status",
        "occupation",
    }
)
_FINANCIAL_FIELDS = frozenset(
    {
        "annual_income",
        "assets",
        "balance",
        "current_value",
        "debt",
        "expenses",
        "financial_assets",
        "income",
        "liabilities",
        "monthly_income",
        "net_worth",
        "pension",
        "pension_assets",
        "real_estate_assets",
        "salary",
        "savings",
        "suggested_value",
        "total_assets",
        "total_debt",
    }
)
_OPERATIONAL_FIELD_NAMESPACES = {
    "attempt_id": "attempt",
    "change_id": "change",
    "client_item_id": "item",
    "correlation_id": "correlation",
    "context_id": "context",
    "item_id": "item",
    "parent_client_item_id": "item",
    "parent_item_id": "item",
    "plan_id": "plan",
    "producer_task_id": "task",
    "proposal_id": "proposal",
    "request_id": "request",
    "session_id": "session",
    "source_turn_id": "turn",
    "task_id": "task",
    "thread_id": "thread",
    "trace_id": "trace",
    "turn_id": "turn",
}
_OPERATIONAL_COLLECTION_NAMESPACES = {
    "blocked_by": "task",
    "depends_on": "task",
    "source_turn_ids": "turn",
}
_SENSITIVE_CONTAINERS = frozenset(
    {
        "arguments",
        "assumptions",
        "bound_inputs",
        "chart_payload",
        "current_form",
        "customer_info",
        "metrics",
        "saved_data",
        "tool_context",
    }
)
_FREE_TEXT_FIELDS = frozenset(
    {
        "content",
        "description",
        "detail",
        "excerpt",
        "message",
        "prompt",
        "query",
        "raw",
        "response",
        "text",
    }
)
_FINANCIAL_ARTIFACT_TYPES = frozenset(
    {"dashboard_metric", "form_change", "reference_value"}
)

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)


def mask_payload(
    payload: Any,
    *,
    sink: MaskingSink,
    policy: MaskingPolicy,
) -> Any:
    """Return a recursively masked copy of one JSON-like runtime payload.

    Unsupported objects and invalid mapping keys fail closed instead of being
    stringified, because an object's representation may contain raw data.
    """

    if not isinstance(sink, MaskingSink):
        raise MaskingPolicyError("sink must be a MaskingSink")
    if not isinstance(policy, MaskingPolicy):
        raise MaskingPolicyError("policy must be a MaskingPolicy")
    masker = _PayloadMasker(sink=sink, policy=policy)
    masker.collect_sensitive_literals(payload)
    return masker.mask(payload)


class _PayloadMasker:
    def __init__(self, *, sink: MaskingSink, policy: MaskingPolicy) -> None:
        self._sink = sink
        self._policy = policy
        self._always_replacements: dict[str, str] = {}
        self._sensitive_replacements: dict[str, str] = {}
        self._pseudonyms: dict[tuple[str, str], str] = {}

    def collect_sensitive_literals(self, payload: Any) -> None:
        self._collect(payload, path=(), sensitive_context=False)

    def mask(self, payload: Any) -> Any:
        return self._mask_node(payload, path=(), sensitive_context=False)

    def _collect(
        self,
        value: Any,
        *,
        path: MaskingPath,
        sensitive_context: bool,
        forced_class: DataClass | None = None,
        forced_namespace: str | None = None,
    ) -> None:
        if isinstance(value, Mapping):
            for raw_key, child in value.items():
                key = self._require_key(raw_key, path)
                child_path = (*path, key)
                decision = self._decide_field(
                    key=key,
                    value=child,
                    parent=value,
                    path=child_path,
                )
                data_class = decision.data_class
                namespace = decision.namespace
                next_sensitive = sensitive_context or decision.sensitive_container
                if data_class is None and next_sensitive and self._is_scalar(child):
                    data_class = DataClass.PROJECT_HIGH_RISK_FINANCIAL
                if forced_class is not None and data_class is None:
                    data_class = forced_class
                    namespace = forced_namespace
                self._collect_classified_literal(
                    child,
                    data_class,
                    namespace,
                    child_path,
                )
                self._collect(
                    child,
                    path=child_path,
                    sensitive_context=next_sensitive,
                    forced_class=data_class if self._is_container(child) else None,
                    forced_namespace=namespace,
                )
            return
        if self._is_sequence(value):
            for index, child in enumerate(value):
                self._collect_classified_literal(
                    child,
                    forced_class,
                    forced_namespace,
                    (*path, index),
                )
                self._collect(
                    child,
                    path=(*path, index),
                    sensitive_context=sensitive_context,
                    forced_class=forced_class,
                    forced_namespace=forced_namespace,
                )
            return
        self._require_scalar(value, path)

    def _collect_classified_literal(
        self,
        value: Any,
        data_class: DataClass | None,
        namespace: str | None,
        path: MaskingPath,
    ) -> None:
        if data_class is DataClass.OPERATIONAL_INFORMATION and self._is_scalar(value):
            replacement = self._pseudonymize(value, namespace or "operation")
            raw = self._literal_text(value)
            if len(raw) >= 4:
                self._always_replacements.setdefault(raw, replacement)
            return
        if data_class in {DataClass.DIRECT_IDENTIFIER, DataClass.SECRET}:
            if self._is_scalar(value):
                raw = self._literal_text(value)
                if len(raw) >= 4:
                    token = (
                        _SECRET_TOKEN
                        if data_class is DataClass.SECRET
                        else _DIRECT_IDENTIFIER_TOKEN
                    )
                    self._always_replacements.setdefault(raw, token)
            return
        if data_class in {
            DataClass.QUASI_IDENTIFIER,
            DataClass.PROJECT_HIGH_RISK_FINANCIAL,
        } and self._should_mask_sensitive_path(path):
            token = (
                _QUASI_IDENTIFIER_TOKEN
                if data_class is DataClass.QUASI_IDENTIFIER
                else _FINANCIAL_TOKEN
            )
            for fragment in self._sensitive_fragments(value, data_class):
                self._sensitive_replacements.setdefault(fragment, token)

    def _mask_node(
        self,
        value: Any,
        *,
        path: MaskingPath,
        sensitive_context: bool,
        forced_class: DataClass | None = None,
        forced_namespace: str | None = None,
    ) -> Any:
        if isinstance(value, Mapping):
            masked: dict[str, Any] = {}
            for raw_key, child in value.items():
                key = self._require_key(raw_key, path)
                masked_key = self._mask_mapping_key(key, path)
                if masked_key in masked:
                    raise MaskingPolicyError(
                        f"masking creates duplicate mapping key at {path!r}"
                    )
                child_path = (*path, key)
                decision = self._decide_field(
                    key=key,
                    value=child,
                    parent=value,
                    path=child_path,
                )
                data_class = decision.data_class
                namespace = decision.namespace
                next_sensitive = sensitive_context or decision.sensitive_container
                if data_class is None and next_sensitive and self._is_scalar(child):
                    data_class = DataClass.PROJECT_HIGH_RISK_FINANCIAL
                if forced_class is not None and data_class is None:
                    data_class = forced_class
                    namespace = forced_namespace

                if data_class is not None and self._is_scalar(child):
                    masked[masked_key] = self._mask_classified_scalar(
                        child,
                        data_class=data_class,
                        namespace=namespace,
                        path=child_path,
                    )
                elif (
                    data_class is None
                    and key.lower() in _FREE_TEXT_FIELDS
                    and self._sink in _OBSERVABILITY_SINKS
                    and child is not None
                ):
                    masked[masked_key] = self._mask_free_text(
                        child,
                        path=child_path,
                    )
                else:
                    masked[masked_key] = self._mask_node(
                        child,
                        path=child_path,
                        sensitive_context=next_sensitive,
                        forced_class=data_class if self._is_container(child) else None,
                        forced_namespace=namespace,
                    )
            return masked

        if self._is_sequence(value):
            masked_items = [
                self._mask_node(
                    child,
                    path=(*path, index),
                    sensitive_context=sensitive_context,
                    forced_class=forced_class,
                    forced_namespace=forced_namespace,
                )
                for index, child in enumerate(value)
            ]
            return tuple(masked_items) if isinstance(value, tuple) else masked_items

        self._require_scalar(value, path)
        data_class = forced_class
        if data_class is None and sensitive_context:
            data_class = DataClass.PROJECT_HIGH_RISK_FINANCIAL
        if data_class is not None:
            return self._mask_classified_scalar(
                value,
                data_class=data_class,
                namespace=forced_namespace,
                path=path,
            )
        if isinstance(value, bytes):
            return self._scrub_text(value.decode("utf-8", errors="replace"))
        if isinstance(value, str):
            return self._scrub_text(value)
        return value

    def _mask_free_text(self, value: Any, *, path: MaskingPath) -> Any:
        if isinstance(value, Mapping):
            masked: dict[str, Any] = {}
            for raw_key, child in value.items():
                key = self._require_key(raw_key, path)
                masked_key = self._mask_mapping_key(key, path)
                if masked_key in masked:
                    raise MaskingPolicyError(
                        f"masking creates duplicate mapping key at {path!r}"
                    )
                masked[masked_key] = self._mask_free_text(
                    child,
                    path=(*path, key),
                )
            return masked
        if self._is_sequence(value):
            masked_items = [
                self._mask_free_text(child, path=(*path, index))
                for index, child in enumerate(value)
            ]
            return tuple(masked_items) if isinstance(value, tuple) else masked_items
        self._require_scalar(value, path)
        return None if value is None else _FREE_TEXT_TOKEN

    def _mask_classified_scalar(
        self,
        value: Any,
        *,
        data_class: DataClass,
        namespace: str | None,
        path: MaskingPath,
    ) -> Any:
        if value is None:
            return None
        self._require_scalar(value, path)
        if data_class is DataClass.SECRET:
            return _SECRET_TOKEN
        if data_class is DataClass.DIRECT_IDENTIFIER:
            return _DIRECT_IDENTIFIER_TOKEN
        if data_class is DataClass.OPERATIONAL_INFORMATION:
            if (
                self._sink is MaskingSink.CALLBACK
                and path in _CALLBACK_ROOT_CORRELATION_PATHS
            ):
                return value
            return self._pseudonymize(value, namespace or "operation")
        if data_class is DataClass.QUASI_IDENTIFIER:
            if self._is_allowed_model_path(path):
                return self._copy_allowed_scalar(value)
            return _QUASI_IDENTIFIER_TOKEN
        if data_class is DataClass.PROJECT_HIGH_RISK_FINANCIAL:
            if self._is_allowed_model_path(path):
                return self._copy_allowed_scalar(value)
            return _FINANCIAL_TOKEN
        raise MaskingPolicyError(f"unsupported data class at {path!r}")

    def _copy_allowed_scalar(self, value: Any) -> Any:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return (
            self._scrub_text(value, include_sensitive=False)
            if isinstance(value, str)
            else value
        )

    def _is_allowed_model_path(self, path: MaskingPath) -> bool:
        return (
            self._sink in _MODEL_SINKS
            and path in self._policy.allowed_model_paths
        )

    def _should_mask_sensitive_path(self, path: MaskingPath) -> bool:
        return not self._is_allowed_model_path(path)

    @classmethod
    def _sensitive_fragments(
        cls,
        value: Any,
        data_class: DataClass,
    ) -> tuple[str, ...]:
        if value is None or isinstance(value, bool):
            return ()
        raw = cls._literal_text(value)
        fragments = {raw} if len(raw) >= 4 else set()
        if data_class is DataClass.PROJECT_HIGH_RISK_FINANCIAL:
            digit_groups = re.findall(r"(?<!\d)\d{4,}(?!\d)", raw.replace(",", ""))
            for digits in digit_groups:
                fragments.add(digits)
                fragments.add(f"{int(digits):,}")
        return tuple(sorted(fragments, key=len, reverse=True))

    def _decide_field(
        self,
        *,
        key: str,
        value: Any,
        parent: Mapping[Any, Any],
        path: MaskingPath,
    ) -> _FieldDecision:
        normalized = key.lower().replace("-", "_")
        if self._is_secret_field(normalized):
            return _FieldDecision(data_class=DataClass.SECRET)
        if self._is_direct_identifier_field(normalized):
            return _FieldDecision(data_class=DataClass.DIRECT_IDENTIFIER)
        namespace = _OPERATIONAL_FIELD_NAMESPACES.get(normalized)
        if namespace is not None:
            return _FieldDecision(
                data_class=DataClass.OPERATIONAL_INFORMATION,
                namespace=namespace,
            )
        collection_namespace = _OPERATIONAL_COLLECTION_NAMESPACES.get(normalized)
        if collection_namespace is not None:
            return _FieldDecision(
                data_class=DataClass.OPERATIONAL_INFORMATION,
                namespace=collection_namespace,
            )
        if normalized == "id" and self._is_task_id_path(path, parent):
            return _FieldDecision(
                data_class=DataClass.OPERATIONAL_INFORMATION,
                namespace="task",
            )
        if self._is_quasi_identifier_field(normalized):
            return _FieldDecision(data_class=DataClass.QUASI_IDENTIFIER)
        if self._is_financial_field(normalized):
            return _FieldDecision(data_class=DataClass.PROJECT_HIGH_RISK_FINANCIAL)
        if normalized == "value" and self._value_is_sensitive(
            value=value,
            parent=parent,
            path=path,
        ):
            return _FieldDecision(data_class=DataClass.PROJECT_HIGH_RISK_FINANCIAL)
        return _FieldDecision(sensitive_container=normalized in _SENSITIVE_CONTAINERS)

    @staticmethod
    def _is_secret_field(key: str) -> bool:
        return (
            key in _SECRET_FIELDS
            or key.endswith("_api_key")
            or key.endswith("_password")
            or key.endswith("_secret")
            or key.endswith("_token")
        )

    @staticmethod
    def _is_direct_identifier_field(key: str) -> bool:
        return (
            key in _DIRECT_IDENTIFIER_FIELDS
            or key.endswith("_email")
            or key.endswith("_phone")
            or key.endswith("_account_number")
        )

    @staticmethod
    def _is_quasi_identifier_field(key: str) -> bool:
        return key in _QUASI_IDENTIFIER_FIELDS or key.endswith("_age")

    @staticmethod
    def _is_financial_field(key: str) -> bool:
        suffixes = (
            "_amount",
            "_assets",
            "_balance",
            "_debt",
            "_expenses",
            "_income",
            "_liabilities",
            "_pension",
            "_salary",
            "_savings",
        )
        return key in _FINANCIAL_FIELDS or key.endswith(suffixes)

    @staticmethod
    def _is_task_id_path(path: MaskingPath, parent: Mapping[Any, Any]) -> bool:
        return (
            "tasks" in path
            or (
                "depends_on" in parent
                and "bindings" in parent
                and "type" in parent
            )
        )

    def _value_is_sensitive(
        self,
        *,
        value: Any,
        parent: Mapping[Any, Any],
        path: MaskingPath,
    ) -> bool:
        if self._is_container(value):
            return False
        artifact_type = parent.get("type")
        if artifact_type in _FINANCIAL_ARTIFACT_TYPES:
            return True
        if {"section", "path", "operation"}.issubset(parent):
            return True
        return "bound_inputs" in path

    def _pseudonymize(self, value: Any, namespace: str) -> str:
        if value is None or isinstance(value, bool) or not isinstance(
            value, (str, int, bytes)
        ):
            raise MaskingPolicyError(
                f"operational identifier in {namespace!r} must be str, int, or bytes"
            )
        canonical = self._literal_text(value)
        cache_key = (namespace, canonical)
        cached = self._pseudonyms.get(cache_key)
        if cached is not None:
            return cached
        digest = hmac.new(
            self._policy.pseudonym_key,
            f"{namespace}\0{canonical}".encode("utf-8"),
            sha256,
        ).hexdigest()[:24]
        pseudonym = f"{namespace}_{digest}"
        self._pseudonyms[cache_key] = pseudonym
        return pseudonym

    def _scrub_text(self, value: str, *, include_sensitive: bool = True) -> str:
        masked = value
        replacements: dict[str, str] = {}
        if include_sensitive:
            replacements.update(self._sensitive_replacements)
        replacements.update(self._always_replacements)
        for raw, replacement in sorted(
            replacements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            masked = masked.replace(raw, replacement)
        masked = _EMAIL_PATTERN.sub(_DIRECT_IDENTIFIER_TOKEN, masked)
        masked = _PHONE_PATTERN.sub(_DIRECT_IDENTIFIER_TOKEN, masked)
        masked = _BEARER_PATTERN.sub(f"Bearer {_SECRET_TOKEN}", masked)
        masked = _JWT_PATTERN.sub(_SECRET_TOKEN, masked)
        return masked

    def _mask_mapping_key(self, key: str, path: MaskingPath) -> str:
        if path and path[-1] == "task_results":
            return self._pseudonymize(key, "task")
        return self._scrub_text(key)

    @staticmethod
    def _literal_text(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))

    @classmethod
    def _is_container(cls, value: Any) -> bool:
        return isinstance(value, Mapping) or cls._is_sequence(value)

    @staticmethod
    def _is_scalar(value: Any) -> bool:
        return value is None or isinstance(value, (bool, int, float, str, bytes))

    @classmethod
    def _require_scalar(cls, value: Any, path: MaskingPath) -> None:
        if not cls._is_scalar(value):
            raise MaskingPolicyError(
                f"unsupported payload value {type(value).__name__} at {path!r}"
            )
        if isinstance(value, float) and not isfinite(value):
            raise MaskingPolicyError(f"non-finite number at {path!r}")

    @staticmethod
    def _require_key(value: Any, path: MaskingPath) -> str:
        if not isinstance(value, str):
            raise MaskingPolicyError(
                f"mapping key at {path!r} must be a string, got {type(value).__name__}"
            )
        return value
