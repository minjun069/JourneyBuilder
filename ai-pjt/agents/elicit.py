"""Planner-routed, field-scoped qualitative information elicitation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core.llm import llm
from core.model_boundary import ModelPurpose, secure_ainvoke, secure_system_prompt
from graph.elicit_context import (
    ELICIT_TURN_KINDS,
    FIELD_ID_SET,
    FIELD_LABELS,
    FIELD_QUESTIONS,
    MAX_FIELD_RESETS,
    MAX_FIELD_TURNS,
    load_elicit_contexts,
)
from graph.state import ChatState


class ElicitAnalysis(BaseModel):
    response_class: Literal[
        "substantive", "uncertain", "refusal", "stop", "skip", "reset"
    ]
    captured_text: str | None = None


_analysis_chain = llm.with_structured_output(ElicitAnalysis)

ANALYZE_PROMPT = secure_system_prompt("""\
정성정보 필드에 관한 사용자의 현재 발화 하나만 분류하세요.
- substantive: 필드에 기록할 사용자의 직접 표현이 있음
- uncertain: 잘 모르겠음 또는 생각할 시간이 필요함
- refusal: 이 필드에 답하고 싶지 않음
- stop: 이 필드에 관한 질문을 더 이어가고 싶지 않음
- skip: 이 필드를 건너뜀
- reset: 이 필드의 후보를 버리고 처음부터 다시 질문함
captured_text는 substantive일 때만 작성하세요. 사용자의 의미를 보태지 마세요.
발화 종류가 correction이고 기존 확인 내용이 있다면, 기존 내용 중 사용자가 명시적으로
고친 부분만 반영해 필드 전체의 수정된 요약을 반환하세요.
재무 조언이나 다음 질문을 생성하지 마세요.
""")


def _routing(state: ChatState) -> tuple[str, str]:
    raw = state.get("elicit_routing")
    if not isinstance(raw, dict) or set(raw) != {
        "is_related",
        "field",
        "turn_kind",
        "confidence_sufficient",
    }:
        raise ValueError("Elicit routing metadata is missing")
    field_id = raw.get("field")
    turn_kind = raw.get("turn_kind")
    if (
        raw.get("is_related") is not True
        or raw.get("confidence_sufficient") is not True
        or field_id not in FIELD_ID_SET
        or turn_kind not in ELICIT_TURN_KINDS
    ):
        raise ValueError("Elicit routing metadata is not executable")
    return str(field_id), str(turn_kind)


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _turn_id(state: ChatState) -> str:
    value = state.get("request_id")
    return str(value) if value else "turn-unknown"


def _is_yes(message: str) -> bool:
    return message.strip().lower() in {
        "네", "예", "응", "맞아", "맞아요", "확인", "좋아요", "yes", "y",
    }


def _is_no(message: str) -> bool:
    return message.strip().lower() in {
        "아니", "아니요", "틀려", "수정", "no", "n",
    }


def _touch(context: dict[str, Any]) -> None:
    context["updated_at"] = _updated_at()


def _ask_new(
    state: ChatState,
    store: dict[str, Any],
    field_id: str,
) -> dict[str, Any]:
    is_first_elicit_turn = not any(
        item["updated_at"] is not None
        for item in store["contexts"].values()
    )
    context = store["contexts"][field_id]
    context.update({
        "pending_candidate": None,
        "open_question": FIELD_QUESTIONS[field_id],
        "confirmation_status": "not_requested",
        "status": "open",
        "turn_count": 0,
        "reset_count": 0,
    })
    _touch(context)
    prefix = ""
    if is_first_elicit_turn:
        prefix = (
            "재무계획에 담을 고객님의 기준을 정확히 이해하기 위해 한 항목씩 "
            "여쭙고, 정리한 내용은 확인받겠습니다. "
        )
    return _result(state, store, prefix + context["open_question"])


def _confirm_candidate(
    state: ChatState,
    store: dict[str, Any],
    field_id: str,
) -> dict[str, Any]:
    context = store["contexts"][field_id]
    if context["status"] != "awaiting_confirmation":
        raise ValueError("Elicit confirmation has no pending candidate")
    if _is_yes(state["message"]):
        value = context["pending_candidate"]
        source_turns = list(context["source_turns"])
        confirmation_turn = _turn_id(state)
        if confirmation_turn not in source_turns:
            source_turns.append(confirmation_turn)
        context.update({
            "confirmed_summary": value,
            "pending_candidate": None,
            "open_question": None,
            "source_turns": source_turns,
            "confirmation_status": "confirmed",
            "status": "completed",
        })
        _touch(context)
        change = {
            "section": "financial_mindset",
            "path": field_id,
            "operation": "set",
            "value": value,
            "reason": "Elicit 대화에서 사용자가 항목별로 확인한 표현",
            "source_turn_ids": source_turns,
        }
        return _result(
            state,
            store,
            "확인한 내용을 입력 제안으로 만들었습니다. 다른 질문을 하거나 "
            "나중에 다른 항목을 이어서 말씀하셔도 됩니다.",
            change=change,
        )
    if _is_no(state["message"]):
        source_turns = list(context["source_turns"])
        if source_turns:
            source_turns.pop()
        context.update({
            "pending_candidate": None,
            "open_question": "고객님의 표현으로 다시 말씀해 주시겠어요?",
            "source_turns": source_turns,
            "confirmation_status": "rejected",
            "status": "open",
        })
        _touch(context)
        return _result(state, store, "알겠습니다. " + context["open_question"])
    return _result(
        state,
        store,
        "정리한 내용이 맞으면 ‘네’, 다시 말하려면 ‘아니요’라고 알려 주세요.",
    )


async def _analyze_field_turn(
    state: ChatState,
    store: dict[str, Any],
    field_id: str,
    turn_kind: str,
) -> dict[str, Any]:
    context = store["contexts"][field_id]
    context["turn_count"] += 1
    _touch(context)
    if context["turn_count"] > MAX_FIELD_TURNS:
        context.update({
            "pending_candidate": None,
            "open_question": None,
            "confirmation_status": "not_requested",
            "status": "skipped",
        })
        return _result(
            state,
            store,
            "이 항목은 대화 한도에 도달해 비워 두겠습니다. 다른 대화는 그대로 이어갈 수 있습니다.",
        )

    result = await secure_ainvoke(
        _analysis_chain,
        [
            SystemMessage(content=ANALYZE_PROMPT),
            HumanMessage(content=(
                f"대상 필드: {field_id}\n"
                f"발화 종류: {turn_kind}\n"
                f"기존 확인 내용: {context['confirmed_summary']}\n"
                f"보류 후보: {context['pending_candidate']}\n"
                f"열린 질문: {context['open_question'] or FIELD_QUESTIONS[field_id]}\n"
                f"사용자 발화: {state['message']}"
            )),
        ],
        purpose=ModelPurpose.ELICIT,
        operation_name="elicit.analyze",
        sensitive_payload={"message": state["message"]},
    )
    response_class = result.response_class
    if response_class == "stop":
        context.update({
            "pending_candidate": None,
            "open_question": None,
            "confirmation_status": "not_requested",
            "status": "deferred",
        })
        response = "이 항목에 관한 질문을 더 이어가지 않겠습니다. 원하실 때 다시 말씀해 주세요."
    elif response_class in {"skip", "refusal"}:
        context.update({
            "pending_candidate": None,
            "open_question": None,
            "confirmation_status": "not_requested",
            "status": "refused" if response_class == "refusal" else "skipped",
        })
        response = "이 항목은 비워 두겠습니다. 다른 질문이나 작업은 계속하실 수 있습니다."
    elif response_class == "reset":
        if context["reset_count"] >= MAX_FIELD_RESETS:
            response = "이 항목은 이미 한 번 다시 시작했습니다. 답을 정리해 말씀하거나 건너뛰어 주세요."
        else:
            context.update({
                "pending_candidate": None,
                "open_question": FIELD_QUESTIONS[field_id],
                "confirmation_status": "not_requested",
                "status": "open",
                "turn_count": 0,
                "reset_count": context["reset_count"] + 1,
            })
            response = "이 항목을 처음부터 다시 여쭙겠습니다. " + context["open_question"]
    elif response_class == "uncertain" or not result.captured_text:
        context.update({
            "pending_candidate": None,
            "open_question": None,
            "confirmation_status": "not_requested",
            "status": "deferred",
        })
        response = "지금 정하지 않아도 괜찮습니다. 생각이 정리되면 이 항목을 다시 말씀해 주세요."
    else:
        candidate = result.captured_text.strip()
        source_turns = list(context["source_turns"])
        current_turn = _turn_id(state)
        if current_turn not in source_turns:
            source_turns.append(current_turn)
        question = f"{FIELD_LABELS[field_id]} 항목을 ‘{candidate}’로 정리해도 될까요?"
        context.update({
            "pending_candidate": candidate,
            "open_question": question,
            "source_turns": source_turns,
            "confirmation_status": "pending",
            "status": "awaiting_confirmation",
        })
        response = question
    _touch(context)
    return _result(state, store, response)


async def _run(state: ChatState) -> dict[str, Any]:
    field_id, turn_kind = _routing(state)
    store = load_elicit_contexts(state)
    if turn_kind == "new":
        return _ask_new(state, store, field_id)
    if turn_kind == "confirmation":
        return _confirm_candidate(state, store, field_id)
    return await _analyze_field_turn(state, store, field_id, turn_kind)


def _result(
    state: ChatState,
    store: dict[str, Any],
    response: str,
    *,
    change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = {"response": response, "step_id": state["step_id"], "action": None}
    if change is not None:
        output["elicit_capture"] = {
            "type": "form_change",
            "target": "draft_form",
            "user_requested": False,
            "confidence": 1.0,
            "changes": [change],
        }
    return {"output": output, "elicit_contexts": store}


def _build():
    graph_builder = StateGraph(ChatState)
    graph_builder.add_node("run", _run)
    graph_builder.add_edge(START, "run")
    graph_builder.add_edge("run", END)
    return graph_builder.compile()


graph = _build()
