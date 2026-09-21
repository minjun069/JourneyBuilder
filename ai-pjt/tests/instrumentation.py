"""Explicit instrumentation fixtures for graph tests."""

from core.terminal_masking import TerminalPayloadMasker
from core.runtime_instrumentation import FakeInstrumentationSink, InstrumentationPort
from core.runtime_instrumentation_adapter import (
    BoundRuntimeInstrumentation,
    RuntimeInstrumentationAdapter,
)


TEST_MASKING_KEY = b"explicit-test-masking-key-32-bytes"


def fake_instrumentation() -> tuple[
    RuntimeInstrumentationAdapter,
    FakeInstrumentationSink,
]:
    sink = FakeInstrumentationSink()
    return RuntimeInstrumentationAdapter(InstrumentationPort(sink)), sink


def fake_terminal_masker() -> TerminalPayloadMasker:
    return TerminalPayloadMasker(pseudonym_key=TEST_MASKING_KEY)


def bound_fake_instrumentation(
    *,
    request_id: str = "request-test",
    thread_id: str = "thread-test",
    step_id: str = "step-test",
) -> tuple[BoundRuntimeInstrumentation, FakeInstrumentationSink]:
    adapter, sink = fake_instrumentation()
    return (
        adapter.bind(
            request_id=request_id,
            thread_id=thread_id,
            step_id=step_id,
        ),
        sink,
    )
