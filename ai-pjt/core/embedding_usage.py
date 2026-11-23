"""Read numeric Gemini embedding usage before SDK response conversion drops it."""

import json
from contextvars import ContextVar
from functools import wraps


_active_usage = ContextVar("embedding_usage", default=None)


class EmbeddingUsage:
    def __init__(self):
        self.tokens = 0
        self.responses = 0
        self.missing = False

    def __enter__(self):
        self._token = _active_usage.set(self)
        return self

    def __exit__(self, *_):
        _active_usage.reset(self._token)

    def record(self, response):
        # Do not retain body, vectors, headers, or request content.
        try:
            payload = json.loads(response.body)
            tokens = payload.get("usageMetadata", {}).get("promptTokenCount")
            if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                self.missing = True
                return
            self.tokens += tokens
            self.responses += 1
        except Exception:
            self.missing = True

    def fields(self):
        fields = {"usage_status": "reported" if self.responses and not self.missing else "unavailable"}
        if self.responses:
            fields.update(input_tokens=self.tokens, total_tokens=self.tokens)
        return fields


def _collector(args, kwargs):
    path = args[1] if len(args) > 1 else kwargs.get("path", "")
    if isinstance(path, str) and path.split("?", 1)[0].endswith((":embedContent", ":batchEmbedContents")):
        return _active_usage.get()
    return None


def instrument_gemini_embedding(embedding):
    """Wrap only this embedding client's transport; preserve SDK retries/results.

    google-genai's installed converter omits usageMetadata and keeps only HTTP
    headers in sdk_http_response. This instance adapter reads the already buffered
    body before conversion; it neither changes requests nor consumes a stream.
    """
    api = embedding._client._api_client
    if getattr(api, "_embedding_usage_instrumented", False):
        return embedding
    original_request, original_async_request = api.request, api.async_request

    @wraps(original_request)
    def request(*args, **kwargs):
        collector = _collector(args, kwargs)
        try:
            response = original_request(*args, **kwargs)
        except BaseException:
            if collector is not None:
                collector.missing = True
            raise
        if collector is not None:
            collector.record(response)
        return response

    @wraps(original_async_request)
    async def async_request(*args, **kwargs):
        collector = _collector(args, kwargs)
        try:
            response = await original_async_request(*args, **kwargs)
        except BaseException:
            if collector is not None:
                collector.missing = True
            raise
        if collector is not None:
            collector.record(response)
        return response

    api.request, api.async_request = request, async_request
    api._embedding_usage_instrumented = True
    return embedding
