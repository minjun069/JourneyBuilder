"""Request-scoped reference tokenization at the Django trust boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
import secrets
from typing import Any


TOKENIZATION_VERSION = '1.0'
TOKEN_VAULT_TTL_SECONDS = 600

REFERENCE_TOKEN_KINDS = (
    'USER_NAME',
    'PHONE_NUMBER',
    'EMAIL_ADDRESS',
    'ACCOUNT_NUMBER',
    'DIRECT_IDENTIFIER',
    'FINANCIAL_VALUE',
)
_TOKEN_KIND_PATTERN = '|'.join(REFERENCE_TOKEN_KINDS)
REFERENCE_TOKEN_PATTERN = re.compile(
    rf'\[\[(?P<kind>{_TOKEN_KIND_PATTERN})_'
    rf'(?P<namespace>[0-9a-f]{{12}})_(?P<index>[1-9][0-9]*)\]\]'
)
_RESERVED_TOKEN_PATTERN = re.compile(
    r'\[\[[A-Z][A-Z0-9_]{2,63}_[0-9a-f]{12}_[1-9][0-9]*\]\]'
)

_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r'@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+'
)
_PHONE_PATTERN = re.compile(
    r'(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)'
)
_RRN_PATTERN = re.compile(r'(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)')
_LONG_IDENTIFIER_PATTERN = re.compile(
    r'(?<![\dA-Za-z])(?:\d[- ]?){9,15}\d(?![\dA-Za-z])'
)
_KOREAN_NAME_PATTERN = re.compile(
    r'((?:이름|성명)(?:은|는|이|가|:)?\s*)([가-힣]{2,5})'
)
_KRW_AMOUNT_PATTERN = re.compile(
    r'(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*'
    r'[십백천만억조]*\s*원'
)

_DIRECT_IDENTIFIER_FIELDS = {
    'account_identifier': 'ACCOUNT_NUMBER',
    'account_number': 'ACCOUNT_NUMBER',
    'address': 'DIRECT_IDENTIFIER',
    'contact_number': 'PHONE_NUMBER',
    'customer_name': 'USER_NAME',
    'date_of_birth': 'DIRECT_IDENTIFIER',
    'email': 'EMAIL_ADDRESS',
    'email_address': 'EMAIL_ADDRESS',
    'full_name': 'USER_NAME',
    'phone': 'PHONE_NUMBER',
    'phone_number': 'PHONE_NUMBER',
    'resident_registration_number': 'DIRECT_IDENTIFIER',
    'rrn': 'DIRECT_IDENTIFIER',
    'user_name': 'USER_NAME',
}
_FINANCIAL_FIELDS = frozenset({
    'annual_income',
    'assets',
    'balance',
    'current_value',
    'debt',
    'expenses',
    'financial_assets',
    'income',
    'liabilities',
    'monthly_income',
    'net_worth',
    'pension',
    'pension_assets',
    'real_estate_assets',
    'salary',
    'savings',
    'suggested_value',
    'total_assets',
    'total_debt',
})
TRUSTED_FINANCIAL_PREFIXES = (
    ('customer_info',),
    ('tool_context',),
    ('chart_payload',),
)
_TEXTUAL_FIELDS = frozenset({
    'content',
    'description',
    'label',
    'message',
    'reason',
    'response',
    'title',
})


class ReferenceTokenError(ValueError):
    """Raised when tokenization cannot preserve the security contract."""


class ReferenceTokenValidationError(ReferenceTokenError):
    """Raised when an AI result contains a token outside the request vault."""


class ReferenceTokenVault:
    """Opaque per-request mapping whose repr never exposes source values."""

    __slots__ = (
        '_namespace',
        '_request_id',
        '_session_id',
        '_user_id',
        '_tokens',
        '_reverse',
    )

    def __init__(
        self,
        *,
        user_id: int,
        session_id: int,
        request_id: str,
        namespace: str | None = None,
    ) -> None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ReferenceTokenError('token scope user_id must be a positive integer')
        if (
            isinstance(session_id, bool)
            or not isinstance(session_id, int)
            or session_id <= 0
        ):
            raise ReferenceTokenError('token scope session_id must be a positive integer')
        if not isinstance(request_id, str) or not request_id:
            raise ReferenceTokenError('token scope request_id must be non-empty')
        namespace = namespace or secrets.token_hex(6)
        if re.fullmatch(r'[0-9a-f]{12}', namespace) is None:
            raise ReferenceTokenError('token namespace must be 12 lowercase hex characters')
        self._user_id = user_id
        self._session_id = session_id
        self._request_id = request_id
        self._namespace = namespace
        self._tokens: dict[str, Any] = {}
        self._reverse: dict[tuple[str, str], str] = {}

    def __repr__(self) -> str:
        return (
            'ReferenceTokenVault('
            f'request_id={self._request_id!r}, token_count={len(self._tokens)})'
        )

    @property
    def request_id(self) -> str:
        return self._request_id

    def assert_scope(self, *, user_id: int, session_id: int, request_id: str) -> None:
        if (
            self._user_id != user_id
            or self._session_id != session_id
            or self._request_id != request_id
        ):
            raise ReferenceTokenValidationError('reference token scope mismatch')

    def tokenize(self, payload: Any) -> Any:
        return self._tokenize_node(payload, path=())

    def restore(self, payload: Any) -> Any:
        return self._restore_node(payload, path=())

    def to_record(self) -> dict[str, Any]:
        return {
            'version': TOKENIZATION_VERSION,
            'user_id': self._user_id,
            'session_id': self._session_id,
            'request_id': self._request_id,
            'namespace': self._namespace,
            'tokens': dict(self._tokens),
        }

    def _tokenize_node(
        self,
        value: Any,
        *,
        path: tuple[str | int, ...],
        forced_kind: str | None = None,
    ) -> Any:
        if isinstance(value, Mapping):
            tokenized: dict[str, Any] = {}
            for raw_key, child in value.items():
                if not isinstance(raw_key, str):
                    raise ReferenceTokenError('tokenized payload keys must be strings')
                child_path = (*path, raw_key)
                kind = forced_kind or self._field_kind(raw_key, child_path)
                tokenized[raw_key] = self._tokenize_node(
                    child,
                    path=child_path,
                    forced_kind=kind,
                )
            return tokenized
        if _is_sequence(value):
            return [
                self._tokenize_node(
                    child,
                    path=(*path, index),
                    forced_kind=forced_kind,
                )
                for index, child in enumerate(value)
            ]
        _require_scalar(value)
        if value is None:
            return None
        if forced_kind is not None:
            return self._issue(forced_kind, value)
        if isinstance(value, str):
            return self._tokenize_text(value)
        return value

    def _field_kind(self, key: str, path: tuple[str | int, ...]) -> str | None:
        normalized = key.lower().replace('-', '_')
        direct_kind = _DIRECT_IDENTIFIER_FIELDS.get(normalized)
        if direct_kind is None:
            if normalized.endswith('_email'):
                direct_kind = 'EMAIL_ADDRESS'
            elif normalized.endswith('_phone'):
                direct_kind = 'PHONE_NUMBER'
            elif normalized.endswith('_account_number'):
                direct_kind = 'ACCOUNT_NUMBER'
        if direct_kind is not None:
            return direct_kind
        if _is_financial_field(normalized) and not _is_trusted_financial_path(path):
            return 'FINANCIAL_VALUE'
        return None

    def _tokenize_text(self, value: str) -> str:
        protected: dict[str, str] = {}

        def issue(kind: str, raw: str) -> str:
            index = len(protected)
            placeholder = f'\x00RWISSUEDTOKENPLACEHOLDER{index}\x00'
            while placeholder in value or placeholder in protected:
                index += 1
                placeholder = f'\x00RWISSUEDTOKENPLACEHOLDER{index}\x00'
            protected[placeholder] = self._issue(kind, raw)
            return placeholder

        value = _RESERVED_TOKEN_PATTERN.sub(
            lambda match: issue('DIRECT_IDENTIFIER', match.group(0)),
            value,
        )
        value = _EMAIL_PATTERN.sub(
            lambda match: issue('EMAIL_ADDRESS', match.group(0)),
            value,
        )
        value = _PHONE_PATTERN.sub(
            lambda match: issue('PHONE_NUMBER', match.group(0)),
            value,
        )
        value = _RRN_PATTERN.sub(
            lambda match: issue('DIRECT_IDENTIFIER', match.group(0)),
            value,
        )
        value = _LONG_IDENTIFIER_PATTERN.sub(
            lambda match: issue('ACCOUNT_NUMBER', match.group(0)),
            value,
        )
        value = _KOREAN_NAME_PATTERN.sub(
            lambda match: f"{match.group(1)}{issue('USER_NAME', match.group(2))}",
            value,
        )
        value = _KRW_AMOUNT_PATTERN.sub(
            lambda match: issue('FINANCIAL_VALUE', match.group(0)),
            value,
        )
        for placeholder, token in protected.items():
            value = value.replace(placeholder, token)
        return value

    def _issue(self, kind: str, value: Any) -> str:
        if kind not in REFERENCE_TOKEN_KINDS:
            raise ReferenceTokenError('unsupported reference token kind')
        _require_scalar(value)
        identity = (kind, _canonical_scalar(value))
        existing = self._reverse.get(identity)
        if existing is not None:
            return existing
        token = f'[[{kind}_{self._namespace}_{len(self._tokens) + 1}]]'
        self._tokens[token] = value
        self._reverse[identity] = token
        return token

    def _restore_node(self, value: Any, *, path: tuple[str | int, ...]) -> Any:
        if isinstance(value, Mapping):
            restored: dict[str, Any] = {}
            for raw_key, child in value.items():
                if not isinstance(raw_key, str):
                    raise ReferenceTokenValidationError(
                        'terminal payload keys must be strings'
                    )
                restored_key = self._restore_text(raw_key, preserve_type=False)
                if restored_key in restored:
                    raise ReferenceTokenValidationError(
                        'reference token restoration creates a duplicate key'
                    )
                restored[restored_key] = self._restore_node(
                    child,
                    path=(*path, restored_key),
                )
            return restored
        if _is_sequence(value):
            return [
                self._restore_node(child, path=(*path, index))
                for index, child in enumerate(value)
            ]
        _require_scalar(value)
        if not isinstance(value, str):
            return value
        preserve_type = not (
            path
            and isinstance(path[-1], str)
            and path[-1].lower().replace('-', '_') in _TEXTUAL_FIELDS
        )
        return self._restore_text(value, preserve_type=preserve_type)

    def _restore_text(self, value: str, *, preserve_type: bool) -> Any:
        matches = tuple(_RESERVED_TOKEN_PATTERN.finditer(value))
        if not matches:
            return value
        if preserve_type and len(matches) == 1 and matches[0].span() == (0, len(value)):
            token = matches[0].group(0)
            if token not in self._tokens:
                raise ReferenceTokenValidationError(
                    'terminal contains an unissued reference token'
                )
            return self._tokens[token]

        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            if token not in self._tokens:
                raise ReferenceTokenValidationError(
                    'terminal contains an unissued reference token'
                )
            return str(self._tokens[token])

        return _RESERVED_TOKEN_PATTERN.sub(replace, value)


def _canonical_scalar(value: Any) -> str:
    return json.dumps(
        {'type': type(value).__name__, 'value': value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def _is_financial_field(key: str) -> bool:
    suffixes = (
        '_amount',
        '_assets',
        '_balance',
        '_debt',
        '_expenses',
        '_income',
        '_liabilities',
        '_pension',
        '_salary',
        '_savings',
    )
    return key in _FINANCIAL_FIELDS or key.endswith(suffixes)


def _is_trusted_financial_path(path: tuple[str | int, ...]) -> bool:
    return any(path[: len(prefix)] == prefix for prefix in TRUSTED_FINANCIAL_PREFIXES)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _require_scalar(value: Any) -> None:
    if value is not None and not isinstance(value, (bool, int, float, str)):
        raise ReferenceTokenError('tokenized payload values must be JSON scalars')
