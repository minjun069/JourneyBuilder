"""Versioned, scope-bound qualitative information elicitation."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core.llm import llm
from graph.state import ChatState


def _load_policy() -> dict:
    relative = Path("contract-fixtures/ai-lifecycle/v2/elicit-policy.json")
    module_path = Path(__file__).resolve()
    for candidate in (module_path.parents[2] / relative, module_path.parents[1] / relative):
        if candidate.is_file():
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            break
    else:
        raise ValueError("Elicit policy fixture unavailable")
    if payload.get("fixture_kind") != "elicit-policy" or payload.get("schema_version") != "1.0":
        raise ValueError("unsupported Elicit policy")
    if set(payload.get("field_order", [])) != set(payload.get("fields", {})):
        raise ValueError("Elicit field policy mismatch")
    return payload


ELICIT_POLICY = _load_policy()
STATE_VERSION = ELICIT_POLICY["state_schema_version"]
MAX_FIELD_TURNS = ELICIT_POLICY["limits"]["max_field_turns"]
MAX_TOTAL_TURNS = ELICIT_POLICY["limits"]["max_total_turns"]
FIELDS = tuple(
    (field_id, ELICIT_POLICY["fields"][field_id]["question"])
    for field_id in ELICIT_POLICY["field_order"]
)
FIELD_LABELS = {
    field_id: ELICIT_POLICY["fields"][field_id]["label"]
    for field_id in ELICIT_POLICY["field_order"]
}


class ElicitAnalysis(BaseModel):
    response_class: Literal[
        "substantive", "uncertain", "refusal", "stop", "skip", "reset"
    ]
    captured_text: str | None = None


_analysis_chain = llm.with_structured_output(ElicitAnalysis)

ANALYZE_PROMPT = """\
정성정보 수집 질문에 대한 사용자의 현재 답변 하나만 분류하세요.
- substantive: 필드에 기록할 사용자의 직접 표현이 있음
- uncertain: 잘 모르겠음 또는 생각할 시간이 필요함
- refusal: 답하고 싶지 않음
- stop: 전체 수집을 중단함
- skip: 현재 항목만 건너뜀
- reset: 현재 항목 답변을 처음부터 다시 함
captured_text는 substantive일 때만 사용자의 의미를 보태지 말고 간결히 정리합니다.
재무 조언이나 다음 질문을 생성하지 마세요.
"""


def _scope(state: ChatState) -> dict:
    raw = state.get("context_scope", {})
    return {
        "session_id": raw.get("session_id"),
        "thread_id": raw.get("thread_id"),
        "step_id": state["step_id"],
    }


def _initial_state(state: ChatState) -> dict:
    current_form = state.get("customer_info", {}).get("current_form", {})
    confirmed = {
        field_id: {
            "status": "existing" if current_form.get(field_id) not in (None, "") else "pending",
            "value": current_form.get(field_id),
            "source_turn_ids": [],
        }
        for field_id, _ in FIELDS
    }
    index = next((i for i, (field_id, _) in enumerate(FIELDS) if confirmed[field_id]["status"] == "pending"), len(FIELDS))
    return {
        "schema_version": STATE_VERSION,
        "scope": _scope(state),
        "phase": "completed" if index == len(FIELDS) else "asking",
        "field_index": index,
        "fields": confirmed,
        "pending_value": None,
        "pending_source_turn_ids": [],
        "field_turns": 0,
        "total_turns": 0,
        "reset_used": False,
        "purpose_shown": True,
    }


def _valid_existing(state: ChatState, candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    required_keys = {
        "schema_version", "scope", "phase", "field_index", "fields",
        "pending_value", "pending_source_turn_ids", "field_turns", "total_turns",
        "reset_used", "purpose_shown",
    }
    allowed_keys = required_keys | {"paused_from"}
    if not required_keys.issubset(candidate) or set(candidate) - allowed_keys:
        return False
    if candidate.get("schema_version") != STATE_VERSION or candidate.get("scope") != _scope(state):
        return False
    if candidate.get("phase") not in {"asking", "confirming", "paused", "completed", "stopped"}:
        return False
    if candidate.get("phase") == "paused":
        if candidate.get("paused_from") not in {"asking", "confirming"}:
            return False
    elif "paused_from" in candidate:
        return False
    fields = candidate.get("fields")
    if not isinstance(fields, dict) or set(fields) != {field_id for field_id, _ in FIELDS}:
        return False
    if any(
        not isinstance(value, dict)
        or set(value) != {"status", "value", "source_turn_ids"}
        or value.get("status") not in {"pending", "existing", "confirmed", "skipped"}
        or not isinstance(value.get("source_turn_ids"), list)
        or not all(isinstance(turn_id, str) and turn_id for turn_id in value["source_turn_ids"])
        for value in fields.values()
    ):
        return False
    pending_turns = candidate.get("pending_source_turn_ids")
    if (
        not isinstance(pending_turns, list)
        or not all(isinstance(turn_id, str) and turn_id for turn_id in pending_turns)
        or not isinstance(candidate.get("reset_used"), bool)
        or candidate.get("purpose_shown") is not True
    ):
        return False
    index = candidate.get("field_index")
    counters = (candidate.get("field_turns"), candidate.get("total_turns"))
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index <= len(FIELDS)
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters)
        or candidate["field_turns"] > MAX_FIELD_TURNS + 1
        or candidate["total_turns"] > MAX_TOTAL_TURNS + 1
    ):
        return False
    effective_phase = candidate.get("paused_from") if candidate.get("phase") == "paused" else candidate.get("phase")
    if effective_phase == "confirming" and (
        candidate.get("pending_value") in (None, "") or not pending_turns
    ):
        return False
    if candidate.get("phase") == "completed" and index != len(FIELDS):
        return False
    if candidate.get("phase") in {"asking", "confirming", "paused"} and index >= len(FIELDS):
        return False
    return True


def _question(elicit_state: dict) -> str:
    index = int(elicit_state["field_index"])
    return FIELDS[index][1] if index < len(FIELDS) else ""


def _advance(elicit_state: dict) -> None:
    start = int(elicit_state["field_index"]) + 1
    index = next((
        i for i in range(start, len(FIELDS))
        if elicit_state["fields"][FIELDS[i][0]]["status"] == "pending"
    ), len(FIELDS))
    elicit_state["field_index"] = index
    elicit_state["field_turns"] = 0
    elicit_state["pending_value"] = None
    elicit_state["pending_source_turn_ids"] = []
    elicit_state["reset_used"] = False
    elicit_state["phase"] = "completed" if index == len(FIELDS) else "asking"


def _is_yes(message: str) -> bool:
    return message.strip().lower() in {"네", "예", "응", "맞아", "맞아요", "확인", "좋아요", "yes", "y"}


def _is_no(message: str) -> bool:
    return message.strip().lower() in {"아니", "아니요", "틀려", "수정", "no", "n"}


async def _run(state: ChatState) -> dict:
    action = state.get("elicit_action")
    existing = state.get("elicit_state")
    if action == "start":
        elicit_state = _initial_state(state)
        if elicit_state["phase"] == "completed":
            response = "이미 여섯 가지 재무 관점 정보가 모두 입력되어 있습니다."
        else:
            response = (
                "재무계획에 담을 고객님의 기준을 정확히 이해하기 위해 여섯 항목을 "
                "하나씩 여쭙고, 매 항목마다 정리한 내용을 확인받겠습니다. "
                + _question(elicit_state)
            )
        return {"output": _output(state, response), "elicit_state": elicit_state}

    if not _valid_existing(state, existing):
        return {
            "output": _output(
                state,
                "정성정보 대화는 이 단계의 ‘정성정보 대화 시작’ 버튼에서 동의한 뒤 시작할 수 있습니다.",
            )
        }
    elicit_state = deepcopy(existing)
    if action == "resume":
        if elicit_state["phase"] != "paused":
            raise ValueError("Elicit is not paused")
        elicit_state["phase"] = elicit_state.get("paused_from", "asking")
        elicit_state.pop("paused_from", None)
        if elicit_state["phase"] == "confirming":
            field_id = FIELDS[elicit_state["field_index"]][0]
            response = f"{FIELD_LABELS[field_id]} 항목을 ‘{elicit_state['pending_value']}’로 정리해도 될까요?"
        else:
            response = _question(elicit_state)
        return {"output": _output(state, response), "elicit_state": elicit_state}

    if elicit_state["phase"] in {"completed", "stopped", "paused"}:
        raise ValueError("Elicit continuation is not active")
    if elicit_state["phase"] == "confirming" and _is_yes(state["message"]):
        index = elicit_state["field_index"]
        field_id = FIELDS[index][0]
        value = elicit_state["pending_value"]
        elicit_state["fields"][field_id] = {
            "status": "confirmed",
            "value": value,
            "source_turn_ids": list(elicit_state.get("pending_source_turn_ids", [])),
        }
        change = {
            "section": "financial_mindset",
            "path": field_id,
            "operation": "set",
            "value": value,
            "reason": "Elicit 대화에서 사용자가 항목별로 확인한 표현",
            "source_turn_ids": list(elicit_state.get("pending_source_turn_ids", [])),
        }
        _advance(elicit_state)
        response = (
            "확인한 내용을 입력 제안으로 만들었습니다. " + _question(elicit_state)
            if elicit_state["phase"] == "asking"
            else "여섯 가지 재무 관점 확인을 마쳤습니다. 각 제안을 확인해 폼에 반영해 주세요."
        )
        return {
            "output": _output(state, response, change=change),
            "elicit_state": elicit_state,
        }
    if elicit_state["phase"] == "confirming" and _is_no(state["message"]):
        elicit_state["phase"] = "asking"
        elicit_state["pending_value"] = None
        elicit_state["pending_source_turn_ids"] = []
        return {
            "output": _output(state, "알겠습니다. 고객님의 표현으로 다시 말씀해 주세요."),
            "elicit_state": elicit_state,
        }

    elicit_state["total_turns"] += 1
    elicit_state["field_turns"] += 1
    if elicit_state["total_turns"] > MAX_TOTAL_TURNS:
        elicit_state["phase"] = "stopped"
        return {
            "output": _output(state, "정성정보 수집의 전체 대화 한도에 도달해 여기서 마치겠습니다. 현재 폼은 그대로 유지됩니다."),
            "elicit_state": elicit_state,
        }
    if elicit_state["field_turns"] > MAX_FIELD_TURNS:
        field_id = FIELDS[elicit_state["field_index"]][0]
        elicit_state["fields"][field_id]["status"] = "skipped"
        _advance(elicit_state)
        response = (
            "이 항목은 대화 한도에 도달해 비워 두겠습니다. " + _question(elicit_state)
            if elicit_state["phase"] == "asking"
            else "정성정보 확인을 마쳤습니다. 확인되지 않은 값은 비워 두었습니다."
        )
        return {"output": _output(state, response), "elicit_state": elicit_state}

    result = await _analysis_chain.ainvoke([
        SystemMessage(content=ANALYZE_PROMPT),
        HumanMessage(content=(
            f"현재 필드: {FIELDS[elicit_state['field_index']][0]}\n"
            f"현재 질문: {_question(elicit_state)}\n"
            f"사용자 답변: {state['message']}"
        )),
    ])
    response_class = result.response_class
    if response_class == "stop":
        elicit_state["phase"] = "stopped"
        response = "정성정보 수집을 중단했습니다. 확인되지 않은 값은 폼에 제안하지 않습니다."
    elif response_class in {"skip", "refusal"}:
        field_id = FIELDS[elicit_state["field_index"]][0]
        elicit_state["fields"][field_id]["status"] = "skipped"
        _advance(elicit_state)
        response = (
            "현재 항목은 비워 두겠습니다. " + _question(elicit_state)
            if elicit_state["phase"] == "asking"
            else "답변 가능한 항목의 확인을 마쳤습니다. 건너뛴 값은 비워 두었습니다."
        )
    elif response_class == "reset":
        if elicit_state["reset_used"]:
            response = "현재 항목은 이미 한 번 다시 시작했습니다. 답을 정리해 말씀하거나 이 항목을 건너뛰어 주세요."
        else:
            elicit_state["reset_used"] = True
            elicit_state["phase"] = "asking"
            elicit_state["pending_value"] = None
            elicit_state["pending_source_turn_ids"] = []
            elicit_state["field_turns"] = 0
            response = "현재 항목을 처음부터 다시 묻겠습니다. " + _question(elicit_state)
    elif response_class == "uncertain" or not result.captured_text:
        if elicit_state["field_turns"] >= MAX_FIELD_TURNS:
            field_id = FIELDS[elicit_state["field_index"]][0]
            elicit_state["fields"][field_id]["status"] = "skipped"
            _advance(elicit_state)
            response = (
                "이 항목은 대화 한도에 도달해 비워 두겠습니다. " + _question(elicit_state)
                if elicit_state["phase"] == "asking"
                else "정성정보 확인을 마쳤습니다. 확인되지 않은 값은 비워 두었습니다."
            )
        else:
            response = "정답은 없습니다. 최근 돈을 쓸 때 가장 먼저 떠올린 기준부터 한 문장으로 말씀해 보시겠어요?"
    else:
        elicit_state["pending_value"] = result.captured_text.strip()
        elicit_state["pending_source_turn_ids"] = [str(state.get("request_id", "turn-unknown"))]
        elicit_state["phase"] = "confirming"
        label = FIELD_LABELS[FIELDS[elicit_state["field_index"]][0]]
        response = f"{label} 항목을 ‘{elicit_state['pending_value']}’로 정리해도 될까요?"
    return {"output": _output(state, response), "elicit_state": elicit_state}


def _output(state: ChatState, response: str, *, change: dict | None = None) -> dict:
    output = {"response": response, "step_id": state["step_id"], "action": None}
    if change is not None:
        output["elicit_capture"] = {
            "type": "form_change",
            "target": "draft_form",
            "user_requested": False,
            "confidence": 1.0,
            "changes": [change],
        }
    return output

def _build():
    graph_builder = StateGraph(ChatState)
    graph_builder.add_node("run", _run)
    graph_builder.add_edge(START, "run")
    graph_builder.add_edge("run", END)
    return graph_builder.compile()


graph = _build()
