"""Small compatibility layer; imports resolve exclusively to the target checkout."""
from copy import deepcopy
from functools import partial
import inspect


class RequestContentSink:
    """Supply request content before the unchanged version-specific masker.

    Sep12's typed runtime event has no content fields. The instrumentation port
    still validates the original typed event; this sink adds content only to the
    serialized request root handed to MaskedInstrumentationSink.
    """
    def __init__(self, masked_sink):
        self.masked_sink = masked_sink
        self.content = {}

    @property
    def dropped_exports(self):
        return self.masked_sink.dropped_exports

    def set_content(self, **content):
        self.content = deepcopy(content)

    def emit(self, event):
        payload = event.to_dict()
        if payload.get("stage") == "request_processing":
            enriched = {**payload, **self.content}
            class RequestEvent:
                def to_dict(self):
                    return enriched
            self.masked_sink.emit(RequestEvent())
        else:
            self.masked_sink.emit(event)


def bind_request(instrumentation, request_id, thread_id, step_id):
    kwargs = dict(request_id=request_id, thread_id=thread_id, step_id=step_id)
    if "plan_id" in inspect.signature(instrumentation.bind).parameters:
        kwargs["plan_id"] = f"plan-{request_id}"
    return instrumentation.bind(**kwargs)


def request_start(observed, sink, stage, content):
    if hasattr(sink, "set_content"):
        sink.set_content(input=content)
    kwargs = {"input": content} if "input" in inspect.signature(observed.start).parameters else {}
    return observed.start(stage, **kwargs)


def request_succeed(observed, sink, span, output):
    if hasattr(sink, "set_content"):
        sink.set_content(output=output)
    kwargs = {"output": output} if "output" in inspect.signature(observed.succeed).parameters else {}
    observed.succeed(span, **kwargs)


def build_runtime(collector):
    from core.config import settings
    from core.context import ConversationContext
    from core.masking import MaskingSink
    from core.monitoring import get_callbacks
    from core.observability_masking import build_masked_runtime_instrumentation
    from core.runtime_instrumentation import InstrumentationPort
    from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
    from graph.builder import build_graph
    from tools.runtime import build_production_tool_runtime
    from tools.graph_adapter import build_graph_tool_executors
    from langgraph.checkpoint.memory import MemorySaver

    settings.validate_runtime()
    get_callbacks()  # Preserve the product's raw-tracing prohibition.
    instrumentation, sink = build_masked_runtime_instrumentation(
        pseudonym_key=settings.masking_pseudonym_key_bytes,
        downstream=collector, sink=MaskingSink.TRACE_EXPORT)
    sink = RequestContentSink(sink)
    instrumentation = RuntimeInstrumentationAdapter(InstrumentationPort(sink))
    tool_kwargs = {}
    if "task_contracts" in inspect.signature(build_production_tool_runtime).parameters:
        from graph.task_catalog import TASK_TYPE_CONTRACTS
        tool_kwargs["task_contracts"] = TASK_TYPE_CONTRACTS
    tools = build_production_tool_runtime(**tool_kwargs)
    kwargs = {"instrumentation": instrumentation,
              "tool_executors": build_graph_tool_executors(tools)}
    parameters = inspect.signature(build_graph).parameters
    if "tool_runtime" in parameters:
        kwargs["tool_runtime"] = tools
    if "plan_validation_enabled" in parameters:
        kwargs["plan_validation_enabled"] = settings.plan_validation_enabled
    if "response_composer" in parameters and getattr(settings, "counseling_rag_enabled", False):
        from graph.aggregation import compose_aggregate_response
        from rag.counseling_retriever import get_counseling_retriever
        kwargs["response_composer"] = partial(
            compose_aggregate_response, counseling_retriever=get_counseling_retriever(),
            timeout_seconds=settings.counseling_rag_timeout_seconds)
    return build_graph(MemorySaver(), **kwargs), instrumentation, sink, ConversationContext().to_state()


def graph_input(turn, context, request_id, thread_id, fixtures):
    from core.context import ContextScope
    state = {"request_id": request_id, "thread_id": thread_id, "step_id": turn["step_id"],
             "message": turn["message"], "customer_info": deepcopy(turn["customer_info"]),
             "field_registry": deepcopy(turn["field_registry"]), "request_mode": "chat",
             "conversation_context": deepcopy(context)}
    # Sep12 persists scoped checkpoint context; later versions use request context.
    if "structured_context" in context:
        state["context_scope"] = ContextScope(session_id=1, thread_id=thread_id).to_state()
    fixture = fixtures.get(turn["step_id"], {})
    for key in ("chart_payload", "tool_context"):
        if key in fixture:
            state[key] = deepcopy(fixture[key])
    if "report_snapshot" in fixture:
        state["customer_info"]["saved_data"]["report_snapshot"] = deepcopy(fixture["report_snapshot"])
    return state


def result_status(result):
    if result.get("security_blocked") or any(i.get("security_blocked") for i in result.get("plan_issues", []) if isinstance(i, dict)):
        return "policy_blocked"
    tasks = result.get("task_results", [])
    tasks = tasks.values() if isinstance(tasks, dict) else tasks
    failures = [t for t in tasks if isinstance(t, dict) and t.get("status") in {"failed", "blocked", "timeout"}]
    if failures:
        return "completed_with_task_failures"
    if result.get("plan_issues"):
        return "completed_with_plan_issues"
    return "completed"
