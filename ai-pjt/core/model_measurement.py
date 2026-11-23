"""Local-only provider usage collection; never changes or exports model content."""

from contextvars import ContextVar
from contextlib import contextmanager
from time import perf_counter
from threading import Lock
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook


_collector = ContextVar("retirewise_usage_collector", default=None)
register_configure_hook(_collector, inheritable=True)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def response_usage(response):
    """Prefer provider-normalized usage, including valid zero-token responses."""
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(response, "response_metadata", {}) or {}
        usage = metadata.get("token_usage", metadata.get("usage_metadata", {}))
    if not isinstance(usage, dict):
        return {}
    result = {}
    for target, keys in {
        "input_tokens": ("input_tokens", "prompt_tokens", "prompt_token_count"),
        "output_tokens": ("output_tokens", "completion_tokens", "candidates_token_count"),
        "total_tokens": ("total_tokens", "total_token_count"),
    }.items():
        for key in keys:
            value = _count(usage.get(key))
            if value is not None:
                result[target] = value
                break
    for target, parent, key in (
        ("cached_input_tokens", "input_token_details", "cache_read"),
        ("reasoning_output_tokens", "output_token_details", "reasoning"),
        ("cached_input_tokens", "prompt_tokens_details", "cached_tokens"),
        ("reasoning_output_tokens", "completion_tokens_details", "reasoning_tokens"),
    ):
        details = usage.get(parent) or {}
        value = _count(details.get(key)) if isinstance(details, dict) else None
        if value is not None:
            result[target] = value
    return result


class ModelMeasurement(BaseCallbackHandler):
    """Capture usage before a structured-output parser discards AIMessage metadata.

    This callback has no exporter, stores no prompts/responses, and cannot raise
    into inference. A context-local hook preserves the runnable's original config.
    """

    raise_error = False
    run_inline = True

    def __init__(self):
        self.call_id = "call_" + uuid4().hex
        self._usage = {}
        self._runs = set()
        self._missing = False
        self._lock = Lock()

    def __enter__(self):
        self._token = _collector.set(self)
        return self

    def __exit__(self, *_):
        _collector.reset(self._token)

    def on_llm_end(self, response, *, run_id, **kwargs):
        try:
            usages = [response_usage(getattr(generation, "message", None))
                      for generations in response.generations for generation in generations]
            with self._lock:
                if run_id in self._runs:
                    return
                self._runs.add(run_id)
                # A normal chat invocation has one result. Do not multiply
                # request-level provider usage across multiple candidates.
                usage = next((item for item in usages if item), {})
                self._missing |= not {"input_tokens", "output_tokens"}.issubset(usage)
                for key, value in usage.items():
                    self._usage[key] = self._usage.get(key, 0) + value
        except Exception:
            self._missing = True

    def fields(self, response=None):
        try:
            usage = dict(self._usage) if self._runs else response_usage(response)
            complete = "input_tokens" in usage and "output_tokens" in usage
            return {"call_id": self.call_id, **usage,
                    "usage_status": "reported" if complete and not self._missing else "unavailable"}
        except Exception:
            return {"call_id": self.call_id, "usage_status": "unavailable"}


@contextmanager
def measure_embedding(model_name):
    """Record retrieval embedding time without exporting query text or vectors.

    A context-local collector receives numeric usage before SDK conversion.
    Missing provider usage remains unavailable, including partially reported batches.
    """
    try:
        from core.model_boundary import _MODEL_CALL_RECORDER
        recorder = _MODEL_CALL_RECORDER.get()
    except ImportError:
        from core.model_observation import _recorder
        recorder = _recorder.get()
    started = perf_counter()
    call_id = "call_" + uuid4().hex
    from core.embedding_usage import EmbeddingUsage
    usage = EmbeddingUsage()

    def emit(status, error=None):
        if recorder is None:
            return
        try:
            from core.config import settings
            recorder(purpose="qa", operation_name="rag.embedding", status=status,
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
                input=None, output=None, call_id=call_id, **usage.fields(),
                model_name=model_name, model_provider=settings.embedding_provider,
                error_code=type(error).__name__ if error is not None else None)
        except Exception:
            pass

    emit("started")
    try:
        with usage:
            yield
    except BaseException as error:
        emit("failed", error)
        raise
    else:
        emit("succeeded")
