import json
from collections.abc import Iterable, Iterator
from typing import Any


class SSEProtocolError(ValueError):
    """Raised when the upstream AI stream violates the agreed SSE contract."""


def encode_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return f'event: {event}\ndata: {payload}\n\n'


def iter_sse_events(lines: Iterable[str | bytes]) -> Iterator[tuple[str, dict[str, Any]]]:
    event_name = 'message'
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.decode('utf-8') if isinstance(raw_line, bytes) else raw_line
        if line == '':
            if data_lines:
                yield event_name, _decode_data(data_lines)
            event_name = 'message'
            data_lines = []
            continue

        if line.startswith(':'):
            continue

        field, separator, value = line.partition(':')
        if separator and value.startswith(' '):
            value = value[1:]
        if field == 'event':
            event_name = value
        elif field == 'data':
            data_lines.append(value)

    if data_lines:
        yield event_name, _decode_data(data_lines)


def _decode_data(data_lines: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads('\n'.join(data_lines))
    except json.JSONDecodeError as exc:
        raise SSEProtocolError('SSE data must be valid JSON') from exc
    if not isinstance(payload, dict):
        raise SSEProtocolError('SSE data must be a JSON object')
    return payload
