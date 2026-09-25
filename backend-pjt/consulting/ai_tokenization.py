"""Request-scoped reference tokenization at the Django trust boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import json
from math import isfinite
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
_MONEY_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
# Keep a signed or compound amount together. Unsupported units are kept in the
# token too, so the trusted numeric decoder can reject them without truncation.
_KRW_AMOUNT_PATTERN = re.compile(
    rf'(?<![A-Za-z0-9_.,+\-−])(?:(?:[+\-−]\s*)*(?>{_MONEY_NUMBER})'
    r'\s*[A-Za-z%*/×÷십백천만억조달러유로엔화개월년]*\s*)+원'
)
_IDENTIFIER_CONTEXT = re.compile(
    r'계좌|전화|휴대폰|연락처|주민|식별|이름|성명|account|phone|contact|identifier|resident|name',
    re.IGNORECASE,
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
    ('chart_payload',),
)
_TEXTUAL_FIELDS = frozenset({
    'content',
    'description',
    'label',
    'message',
    'reason',
    'response',
    'summary',
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

    def restore_terminal(self, payload: Any, *, step_id: str) -> Any:
        """Restore approved numeric form values before persistence or SSE done."""

        from .field_registry import get_field_spec, validate_field_change

        if not isinstance(payload, Mapping) or (
            payload.get('request_id') != self._request_id
            or payload.get('session_id') != self._session_id
        ):
            raise ReferenceTokenValidationError('terminal token scope mismatch')
        restored = self.restore(payload)
        raw_output = payload.get('output')
        output = restored.get('output')
        if not isinstance(raw_output, Mapping) or not isinstance(output, Mapping):
            return restored
        if output.get('step_id') not in (None, step_id):
            raise ReferenceTokenValidationError('terminal step does not match request')
        raw_action = raw_output.get('action')
        action = output.get('action')
        if not isinstance(action, dict) or action.get('type') != 'form_change':
            return restored
        if not isinstance(raw_action, Mapping) or action.get('target', 'draft_form') != 'draft_form':
            raise ReferenceTokenValidationError('invalid form change target')
        for raw, change in zip(raw_action.get('changes', []), action.get('changes', []), strict=True):
            if not isinstance(raw, Mapping) or not isinstance(change, dict):
                raise ReferenceTokenValidationError('invalid form change')
            try:
                spec = validate_field_change(
                    step_id=step_id, section=change.get('section'),
                    path=change.get('path'), operation=change.get('operation'),
                )
            except (TypeError, ValueError) as error:
                raise ReferenceTokenValidationError('invalid canonical change target') from error
            if change.get('unit', spec.unit) != spec.unit:
                raise ReferenceTokenValidationError('change unit does not match canonical field')
            if change['operation'] == 'remove':
                continue
            if 'value' not in raw:
                raise ReferenceTokenValidationError('form change requires a value')
            if change['operation'] == 'add' and spec.cardinality in {'list', 'nested_list'}:
                if not isinstance(change['value'], dict) or not change['value']:
                    raise ReferenceTokenValidationError('added row must be an object')
                prefix = 'items[].' if spec.section == 'goal_periods' and spec.path == 'items' else ''
                if not isinstance(raw['value'], Mapping) or raw['value'].keys() != change['value'].keys():
                    raise ReferenceTokenValidationError('added row keys must remain canonical')
                for name, value in change['value'].items():
                    child = get_field_spec(spec.section, f'{prefix}{name}')
                    if child is None or child.step_id != step_id or not child.writable_by_ai:
                        raise ReferenceTokenValidationError('unapproved added row field')
                    change['value'][name] = _restore_change_value(raw['value'][name], value, child)
            else:
                change['value'] = _restore_change_value(raw['value'], change['value'], spec)
        return restored

    def to_record(self) -> dict[str, Any]:
        return {
            'version': TOKENIZATION_VERSION,
            'user_id': self._user_id,
            'session_id': self._session_id,
            'request_id': self._request_id,
            'namespace': self._namespace,
            'tokens': dict(self._tokens),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> 'ReferenceTokenVault':
        if not isinstance(record, Mapping) or record.get('version') != TOKENIZATION_VERSION:
            raise ReferenceTokenError('unsupported token vault record')
        vault = cls(
            user_id=record.get('user_id'),
            session_id=record.get('session_id'),
            request_id=record.get('request_id'),
            namespace=record.get('namespace'),
        )
        tokens = record.get('tokens')
        if not isinstance(tokens, Mapping):
            raise ReferenceTokenError('token vault entries must be an object')
        for token, value in tokens.items():
            match = REFERENCE_TOKEN_PATTERN.fullmatch(token) if isinstance(token, str) else None
            if match is None or match.group('namespace') != vault._namespace:
                raise ReferenceTokenError('token vault entry is malformed')
            _require_scalar(value)
            kind = match.group('kind')
            identity = (kind, _canonical_scalar(value))
            if identity in vault._reverse:
                raise ReferenceTokenError('token vault contains duplicate values')
            vault._tokens[token] = value
            vault._reverse[identity] = token
        return vault

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
            lambda match: match.group(0) if _is_money_occurrence(match, value)
            else issue('PHONE_NUMBER', match.group(0)),
            value,
        )
        value = _RRN_PATTERN.sub(
            lambda match: match.group(0) if _is_money_occurrence(match, value)
            else issue('DIRECT_IDENTIFIER', match.group(0)),
            value,
        )
        value = _LONG_IDENTIFIER_PATTERN.sub(
            lambda match: match.group(0) if _is_money_occurrence(match, value)
            else issue('ACCOUNT_NUMBER', match.group(0)),
            value,
        )
        value = _KOREAN_NAME_PATTERN.sub(
            lambda match: f"{match.group(1)}{issue('USER_NAME', match.group(2))}",
            value,
        )
        value = _KRW_AMOUNT_PATTERN.sub(
            lambda match: issue('FINANCIAL_VALUE', match.group(0))
            if not _identifier_context(value, match.start(), match.end()) else match.group(0),
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


def _identifier_context(text: str, start: int, end: int) -> bool:
    # Limit the decision to the current clause; commas inside the amount are
    # deliberately excluded from these surrounding slices.
    before = re.split(r'[\n;,.!?]', text[:start])[-1]
    after = re.split(r'[\n;,.!?]', text[end:])[0]
    return _IDENTIFIER_CONTEXT.search(before + after) is not None


def _is_money_occurrence(match: re.Match[str], text: str) -> bool:
    return any(
        money.start() <= match.start() and match.end() <= money.end()
        and not _identifier_context(text, money.start(), money.end())
        for money in _KRW_AMOUNT_PATTERN.finditer(text)
    )


def _decode_krw(value: Any, value_type: str) -> int | float:
    """Decode a whole issued amount without dropping signs or unit fragments."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ReferenceTokenValidationError('financial reference is not an amount')
    if isinstance(value, float) and not isfinite(value):
        raise ReferenceTokenValidationError('financial amount must be finite')
    source = str(value).strip()
    if len(source) > 128:
        raise ReferenceTokenValidationError('financial amount is too long')
    if re.search(r'\d\s+\d', source):
        raise ReferenceTokenValidationError('financial number contains ambiguous spacing')
    source = re.sub(r'\s+', '', source).replace('−', '-')
    if source.endswith('원'):
        source = source[:-1]
    sign = -1 if source.startswith('-') else 1
    if source.startswith(('+', '-')):
        source = source[1:]
    factors = {'십': 10, '백': 100, '천': 1000, '만': 10000, '억': 100000000, '조': 1000000000000}
    component = re.compile(rf'({_MONEY_NUMBER})([십백천만억조]*)')
    position = 0
    previous_factor = None
    total = Decimal(0)
    try:
        with localcontext() as context:
            context.prec = 256
            while position < len(source):
                match = component.match(source, position)
                if match is None:
                    raise ReferenceTokenValidationError('unsupported financial amount syntax')
                factor, previous_unit = 1, 0
                units = match.group(2)
                if len(units) > 2 or (len(units) == 2 and not (
                    units[0] in '십백천' and units[1] in '만억조'
                )):
                    raise ReferenceTokenValidationError('unsupported financial unit combination')
                for unit in units:
                    if factors[unit] <= previous_unit:
                        raise ReferenceTokenValidationError('financial units are out of order')
                    previous_unit = factors[unit]
                    factor *= previous_unit
                if previous_factor is not None and factor >= previous_factor:
                    raise ReferenceTokenValidationError('financial components are out of order')
                previous_factor = factor
                total += Decimal(match.group(1).replace(',', '')) * factor
                position = match.end()
            total *= sign
    except InvalidOperation as error:
        raise ReferenceTokenValidationError('invalid financial amount') from error
    if not position or not total.is_finite():
        raise ReferenceTokenValidationError('financial amount must be finite')
    if value_type == 'integer':
        if total != total.to_integral_value():
            raise ReferenceTokenValidationError('financial amount must be a whole won value')
        return int(total)
    result = float(total)
    if not isfinite(result):
        raise ReferenceTokenValidationError('financial amount must be finite')
    return result


def _restore_change_value(original: Any, value: Any, spec: Any) -> Any:
    match = REFERENCE_TOKEN_PATTERN.fullmatch(original) if isinstance(original, str) else None
    numeric = spec.value_type in {'integer', 'decimal', 'number'}
    if match is not None and (numeric or match.group('kind') == 'FINANCIAL_VALUE'):
        if not (match.group('kind') == 'FINANCIAL_VALUE' and numeric and spec.unit == 'KRW'):
            raise ReferenceTokenValidationError('reference kind does not match numeric field')
        value = _decode_krw(value, spec.value_type)
    elif isinstance(original, str) and '[[FINANCIAL_VALUE_' in original:
        raise ReferenceTokenValidationError('financial reference must be an exact token')
    if value is None:
        if not spec.nullable:
            raise ReferenceTokenValidationError('canonical field does not permit null')
        return None
    valid_type = {
        'string': isinstance(value, str),
        'integer': isinstance(value, int) and not isinstance(value, bool),
        'decimal': isinstance(value, (int, float)) and not isinstance(value, bool),
        'number': isinstance(value, (int, float)) and not isinstance(value, bool),
        'boolean': isinstance(value, bool),
        'date': isinstance(value, str),
        'object': isinstance(value, dict),
        'array': isinstance(value, list),
    }.get(spec.value_type, False)
    if not valid_type:
        raise ReferenceTokenValidationError('value does not match canonical type')
    if isinstance(value, float) and not isfinite(value):
        raise ReferenceTokenValidationError('canonical number must be finite')
    if spec.value_type == 'date':
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ReferenceTokenValidationError('invalid canonical date') from error
    constraints = spec.constraints
    if 'choices' in constraints and value not in constraints['choices']:
        raise ReferenceTokenValidationError('value is outside canonical choices')
    if numeric:
        if 'min' in constraints and value < constraints['min']:
            raise ReferenceTokenValidationError('value is below canonical minimum')
        if 'max' in constraints and value > constraints['max']:
            raise ReferenceTokenValidationError('value is above canonical maximum')
    if isinstance(value, str) and len(value) > constraints.get('max_length', len(value)):
        raise ReferenceTokenValidationError('value exceeds canonical length')
    if isinstance(value, (dict, list)):
        return _restore_nested_values(original, value, spec.section, spec.path)
    return value


def _restore_nested_values(original: Any, value: Any, section: str, path: str) -> Any:
    from .field_registry import get_field_spec

    spec = get_field_spec(section, path)
    if spec is not None and spec.value_type not in {'object', 'array'}:
        return _restore_change_value(original, value, spec)
    if isinstance(value, dict):
        if not isinstance(original, Mapping) or original.keys() != value.keys():
            raise ReferenceTokenValidationError('object keys must remain canonical')
        return {
            key: _restore_nested_values(original[key], child, section, f'{path}.{key}')
            for key, child in value.items()
        }
    if isinstance(value, list):
        if not isinstance(original, list) or len(original) != len(value):
            raise ReferenceTokenValidationError('invalid canonical array')
        return [
            _restore_nested_values(raw, child, section, f'{path}[]')
            for raw, child in zip(original, value, strict=True)
        ]
    if spec is not None:
        return _restore_change_value(original, value, spec)
    if isinstance(original, str) and '[[FINANCIAL_VALUE_' in original:
        raise ReferenceTokenValidationError('financial reference has no canonical numeric field')
    if isinstance(value, float) and not isfinite(value):
        raise ReferenceTokenValidationError('nested number must be finite')
    return value


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
