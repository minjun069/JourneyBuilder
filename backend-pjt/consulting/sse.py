import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from rest_framework.renderers import BaseRenderer


class EventStreamRenderer(BaseRenderer):
    """Allow DRF content negotiation for SSE StreamingHttpResponse views."""

    media_type = 'text/event-stream'
    format = 'sse'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode(self.charset)
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(',', ':'),
        ).encode(self.charset)


class SSEProtocolError(ValueError):
    """Raised when the upstream AI stream violates the agreed SSE contract."""


def encode_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return f'event: {event}\ndata: {payload}\n\n'


async def aiter_sse_events(
    lines: AsyncIterable[str | bytes],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    event_name = 'message'
    data_lines: list[str] = []

    async for raw_line in lines:
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
