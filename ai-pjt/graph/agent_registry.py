"""Agent graph registration and task-result adaptation."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from graph.runtime_types import RuntimeExecutor
from graph.task_contracts import failed_task_result
from tools.output_policy import validate_form_update_contract


def default_agent_graphs() -> dict[str, Any]:
    """Load provider-dependent Agent packages only at graph build time."""

    from agents import chart_interpret, elicit, error_review, input_assist, qa

    return {
        "qa": qa.graph,
        "input_assist": input_assist.graph,
        "elicit": elicit.graph,
        "error_review": error_review.graph,
        "chart_interpret": chart_interpret.graph,
    }


def build_agent_executor(agent_name: str, agent_graph: Any) -> RuntimeExecutor:
    """Adapt one Agent package graph to the runtime executor contract."""

    async def execute(payload: dict[str, Any]) -> Mapping[str, Any]:
        task = payload["task"]
        task_id = str(task.get("id", "task-unknown"))
        agent_state = {
            "message": payload["message"],
            "step_id": payload["step_id"],
            "customer_info": payload["customer_info"],
            "task_input": deepcopy(payload["task_input"]),
            "chart_payload": payload.get("chart_payload"),
            "request_mode": payload.get("request_mode", "chat"),
            "conversation_context": deepcopy(
                payload.get("conversation_context", {})
            ),
            "request_id": payload.get("request_id"),
        }
        result = await agent_graph.ainvoke(agent_state)
        if (
            agent_name == "input_assist"
            and isinstance(result, Mapping)
            and result.get("reason_code") == "reference_target_not_allowed"
        ):
            return failed_task_result(task_id, "reference_target_not_allowed")

        raw_output = result.get("output") if isinstance(result, Mapping) else None
        validation = validate_form_update_contract({
            **agent_state,
            "output": raw_output,
        })
        if not validation.valid:
            return failed_task_result(task_id, "artifact_invalid")

        output = validation.output
        action = output.get("action")
        artifact = (
            deepcopy(dict(action))
            if isinstance(action, Mapping)
            else {"type": "text_response", "text": output["response"]}
        )
        task_result = {
            "task_id": task_id,
            "status": "completed",
            "response": output["response"],
            "artifact": artifact,
        }
        if isinstance(output.get("review"), Mapping):
            task_result["review"] = deepcopy(dict(output["review"]))
        return task_result

    return execute
