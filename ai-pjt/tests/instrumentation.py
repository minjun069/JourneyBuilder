"""Explicit instrumentation fixtures for graph tests."""

from core.runtime_instrumentation import FakeInstrumentationSink, InstrumentationPort
from core.runtime_instrumentation_adapter import (
    BoundRuntimeInstrumentation,
    RuntimeInstrumentationAdapter,
)


def fake_instrumentation() -> tuple[
    RuntimeInstrumentationAdapter,
    FakeInstrumentationSink,
]:
    sink = FakeInstrumentationSink()
    return RuntimeInstrumentationAdapter(InstrumentationPort(sink)), sink


def bound_fake_instrumentation(
    *,
    request_id: str = "request-test",
    thread_id: str = "thread-test",
    plan_id: str = "plan-unknown",
    step_id: str = "step-test",
) -> tuple[BoundRuntimeInstrumentation, FakeInstrumentationSink]:
    adapter, sink = fake_instrumentation()
    return (
        adapter.bind(
            request_id=request_id,
            thread_id=thread_id,
            plan_id=plan_id,
            step_id=step_id,
        ),
        sink,
    )
