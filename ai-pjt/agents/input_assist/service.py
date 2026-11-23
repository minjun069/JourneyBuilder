"""Batch form extraction with deterministic validation and optional meaning review."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, NotRequired, TypedDict

from agents.input_assist.defaults import (
    ReferenceTargetNotAllowed, build_bound_reference_proposals, build_default_proposals,
)
from core.field_registry import load_field_registry
from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from tools.output_policy import _validate_change

from .model import (
    EXTRACT_PROMPT, RESPOND_PROMPT, REVIEW_PROMPT,
    ExtractResult, ExtractedChange, ReviewResult, extract_chain, review_chain,
)


class InputAssistState(TypedDict):
    message: str
    step_id: str
    customer_info: dict
    task_input: NotRequired[dict[str, Any]]
    field_refs: dict[str, dict]
    item_refs: dict[str, dict]
    extraction: dict
    candidates: list[dict]
    review_candidates: list[dict]
    questions: list[str]
    output: dict


def _bindings(state: Mapping[str, Any]) -> dict:
    task = state.get("task_input", {})
    bindings = task.get("bindings", {}) if isinstance(task, Mapping) else {}
    return dict(bindings) if isinstance(bindings, Mapping) else {}


def _reference_for_model(state: Mapping[str, Any]) -> dict:
    bindings = _bindings(state)
    if "value" not in bindings:
        return {}
    return {key: deepcopy(bindings[key]) for key in ("value", "unit", "period", "population") if key in bindings}


def _current_form(state: Mapping[str, Any]) -> dict:
    form = state.get("customer_info", {}).get("current_form", {})
    return form if isinstance(form, dict) else {}


def _identity(row: Mapping[str, Any], *, parent: bool = False) -> dict:
    prefix = "parent_" if parent else ""
    if row.get("id") is not None:
        return {f"{prefix}item_id": row["id"]}
    if row.get("client_item_id"):
        return {f"{prefix}client_item_id": row["client_item_id"]}
    return {}


def _row_label(row: Mapping[str, Any]) -> str:
    for key in ("name", "content", "type", "asset_type"):
        if row.get(key):
            return str(row[key])
    if "from_age" in row and "to_age" in row:
        return f"{row['from_age']}세부터 {row['to_age']}세"
    return "이름 없는 항목"


def _form_context(state: Mapping[str, Any]) -> tuple[dict, dict, dict]:
    """Keep raw field paths and row identities in code-owned reference maps."""
    registry = [field for field in load_field_registry(state["step_id"]) if field["writable_by_ai"]]
    fields = {f"f{index}": field for index, field in enumerate(registry)}
    form = _current_form(state)
    items: dict[str, dict] = {}
    for section in dict.fromkeys(field["section"] for field in registry if field["cardinality"] != "single"):
        rows = form.get(section, []) if section in {"goal_periods", "goal_milestones"} else form.get("assets", [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not _identity(row):
                continue
            parent_ref = f"r{len(items)}"
            items[parent_ref] = {"section": section, "row": dict(row), "parent": None}
            if section == "goal_periods":
                for child in row.get("items", []) if isinstance(row.get("items"), list) else []:
                    if isinstance(child, Mapping) and _identity(child):
                        items[f"r{len(items)}"] = {
                            "section": section, "row": dict(child), "parent": dict(row), "parent_ref": parent_ref,
                        }
    field_inputs = []
    for ref, field in fields.items():
        entry = {
            "field_ref": ref, "name": field["label"], "type": field["value_type"],
            "unit": field["unit"], "constraints": field["constraints"],
            "required": not field["nullable"],
        }
        if field["cardinality"] != "single":
            entry["key"] = field["path"].removeprefix("items[].")
            entry["group"] = field["section"]
            entry["nested"] = field["path"].startswith("items[].")
        field_inputs.append(entry)
    item_inputs = []
    for ref, item in items.items():
        values = {}
        for field_ref, field in fields.items():
            if field["section"] != item["section"] or field["path"].startswith("items[].") != bool(item["parent"]):
                continue
            key = field["path"].removeprefix("items[].")
            if key != "items":
                value = item["row"].get(key)
                values[field_ref] = "입력 없음" if value is None else value
        item_inputs.append({
            "item_ref": ref, "name": _row_label(item["row"]), "values": values,
            **({"parent_ref": item["parent_ref"]} if item.get("parent_ref") else {}),
        })
    payload = {
        "message": state["message"], "fields": field_inputs, "items": item_inputs,
        "reference": _reference_for_model(state),
        "current_values": {ref: "입력 없음" if form.get(field["path"]) is None else form[field["path"]]
                           for ref, field in fields.items() if field["cardinality"] == "single"},
    }
    return fields, items, payload


def _sensitive_payload(state: Mapping[str, Any]) -> dict:
    return {"message": state["message"], "customer_info": state.get("customer_info", {}), "bindings": _bindings(state)}


async def _extract(state: InputAssistState) -> dict:
    fields, items, payload = _form_context(state)
    result = await secure_ainvoke(
        extract_chain, prompt_messages(EXTRACT_PROMPT, payload, step_id=state["step_id"]),
        purpose=ModelPurpose.INPUT_ASSIST, operation_name="input_assist.extract",
        sensitive_payload=_sensitive_payload(state), prompt=EXTRACT_PROMPT,
    )
    return {
        "field_refs": fields, "item_refs": items, "extraction": result.model_dump(),
        "questions": list(result.questions), "candidates": [], "review_candidates": [], "output": {},
    }


def _restore_change(raw: dict, fields: dict, items: dict) -> tuple[dict, dict, Any, str]:
    field = fields.get(raw["field_ref"])
    if field is None:
        raise ValueError("변경할 항목을 현재 화면에서 확인해 주세요.")
    operation = raw["operation"]
    item = items.get(raw.get("item_ref"))
    change = {"section": field["section"], "path": field["path"], "operation": operation}
    current_value = None
    row_name = ""
    if field["cardinality"] == "single":
        if raw.get("item_ref") is not None or operation not in {"set", "remove"}:
            raise ValueError(f"{field['label']}: 변경 방식을 확인해 주세요.")
        if operation == "remove" and not field["nullable"]:
            raise ValueError(f"{field['label']}: 필수 입력 항목은 비울 수 없습니다.")
    elif operation == "add":
        if field["section"] == "goal_periods":
            if field["path"] != "items" or item is None or item["section"] != "goal_periods" or item["parent"]:
                raise ValueError("추가할 기간별 항목의 부모 기간을 확인해 주세요.")
            change.update(_identity(item["row"], parent=True))
            row_name = _row_label(item["row"])
        elif raw.get("item_ref") is not None:
            raise ValueError("새 항목 추가와 기존 항목 수정을 구분해 주세요.")
    else:
        if operation not in {"update", "remove"} or item is None or item["section"] != field["section"]:
            raise ValueError(f"{field['label']}: 변경할 기존 항목을 확인해 주세요.")
        if field["path"].startswith("items[].") != bool(item["parent"]):
            raise ValueError("변경할 기간과 그 안의 항목을 확인해 주세요.")
        change.update(_identity(item["row"]))
        if item["parent"]:
            change.update(_identity(item["parent"], parent=True))
        current_value = item["row"].get(field["path"].removeprefix("items[]."))
        row_name = _row_label(item["row"])
    if operation != "remove":
        change["value"] = deepcopy(raw.get("value"))
    change["reason"] = "사용자가 요청한 입력 변경"
    return change, field, current_value, row_name


def _requires_review(message: str, change: dict, field: dict, row_name: str) -> bool:
    """Only literal, explicitly named changes can skip semantic review."""
    compact = re.sub(r"\s+", "", message)
    if not re.search(r"바꿔|변경|수정|넣어|입력|설정|추가|삭제|지워|제거|채워|작성|적용|고쳐", message):
        return True
    if re.search(r"말고|제외|않|유지|그대로|아니|하지|설명|알려|[~〜]", message):
        return True
    if row_name and re.sub(r"\s+", "", row_name) not in compact:
        return True
    if change["operation"] == "remove":
        return not (row_name and re.search(r"삭제|지워|제거|빼줘", message))
    if change["operation"] == "add" or re.sub(r"\s+", "", field["label"]) not in compact:
        return True
    value = change.get("value")
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        return True
    label = re.sub(r"\s+", "", field["label"])
    for other in load_field_registry(field["step_id"]):
        other_label = re.sub(r"\s+", "", other["label"])
        if label != other_label and label in other_label and other_label in compact:
            return True
    if isinstance(value, (int, float)):
        if re.search(r"(?:천|만|억)\s*원", message):
            return True
    if not isinstance(value, (str, int, float)) or value == "":
        return True
    literal = re.escape(re.sub(r"\s+", "", str(value)))
    pattern = rf"{re.escape(label)}(?:은|는|을|를|이|가)?[:=]?{literal}(?![\d.])"
    return re.search(pattern, compact.replace(",", "")) is None


def _description(change: dict, field: dict, current_value: Any, row_name: str = "") -> dict:
    return {
        "name": f"{row_name} / {field['label']}" if row_name else field["label"],
        "operation": change["operation"], "current_value": "입력 없음" if current_value is None else current_value,
        "value": "입력 비우기" if change.get("value") is None else change["value"], "unit": field["unit"],
    }


def _validation_message(code: str) -> str:
    return {
        "VALUE_TYPE_MISMATCH": "항목에 맞는 숫자나 문자 형식으로 알려 주세요.",
        "VALUE_BELOW_MIN": "허용된 최솟값보다 작습니다.",
        "VALUE_ABOVE_MAX": "허용된 최댓값보다 큽니다.",
        "NULL_NOT_ALLOWED": "필수 입력 항목은 비울 수 없습니다.",
        "CHOICE_NOT_ALLOWED": "화면에서 선택할 수 있는 값으로 알려 주세요.",
        "VALUE_TOO_LONG": "입력 가능한 글자 수를 넘었습니다.",
    }.get(code, "이 항목에 적용할 값과 변경 방식을 다시 확인해 주세요.")


def _default_changes(state: InputAssistState) -> list[dict]:
    bindings = _bindings(state)
    if "reference_id" in bindings:
        return build_bound_reference_proposals(
            step_id=state["step_id"], message=state["message"], current_form=_current_form(state),
            bindings=bindings, user_requested_change=True,
        )
    return build_default_proposals(
        step_id=state["step_id"], message=state["message"], current_form=_current_form(state),
        saved_data=state.get("customer_info", {}).get("saved_data", {}),
    )


def _policy_change(raw: dict, field: dict) -> dict:
    change = {key: value for key, value in raw.items() if key in {
        "section", "path", "operation", "item_id", "client_item_id", "parent_item_id", "parent_client_item_id",
    }}
    change.update(value=raw["policy_value"], unit=field["unit"], reason="승인된 기본값 정책", **raw.get("policy_metadata", {}))
    return change


def _bind_change(change: dict, field: dict, bindings: dict, approved: list[dict]) -> dict:
    """A model may select a target, but cannot rewrite a verified reference."""
    if change["operation"] == "remove" or (
        type(change.get("value")) is not type(bindings["value"])
        or change.get("value") != bindings["value"]
        or bindings.get("unit") != field["unit"]
    ):
        raise ValueError(f"{field['label']}: 조회한 기준값과 값 또는 단위가 달라 변경안을 만들지 않았습니다.")
    if "reference_id" in bindings:
        keys = ("section", "path", "operation", "item_id", "client_item_id", "parent_item_id", "parent_client_item_id")
        matches = [raw for raw in approved if all(raw.get(key) == change.get(key) for key in keys)]
        if len(matches) != 1 or matches[0]["policy_value"] != bindings["value"]:
            raise ValueError(f"{field['label']}: 조회값을 적용할 수 있는 승인된 기존 빈 항목이 아닙니다.")
        return _policy_change(matches[0], field)
    return {**change, "value": deepcopy(bindings["value"]), "unit": bindings["unit"], "reason": "검증된 조회 기준값으로 요청한 변경"}


def _validate(state: InputAssistState) -> dict:
    fields, items = state["field_refs"], state["item_refs"]
    registry = {(field["section"], field["path"]): field for field in load_field_registry(state["step_id"])}
    questions = list(state["questions"])
    candidates, review_candidates = [], []
    request = state["extraction"]["request"]
    if request == "none":
        if not questions:
            questions.append("어떤 항목을 어떤 값으로 변경할지 알려 주세요.")
        return {"candidates": [], "review_candidates": [], "questions": questions}
    defaults = request == "fill_defaults"
    bindings = _bindings(state)
    bound = not defaults and "value" in bindings
    approved: list[dict] = []
    try:
        raw_changes = _default_changes(state) if defaults else state["extraction"]["changes"]
        if bound and "reference_id" in bindings:
            approved = _default_changes(state)
    except ReferenceTargetNotAllowed:
        raw_changes = []
        questions.append("조회 기준값을 승인된 기존 항목에 연결할 수 없어 변경안을 만들지 않았습니다.")
    if defaults and not raw_changes and not questions:
        questions.append("승인된 기준값으로 채울 수 있는 빈 항목이 없어 자동 채우지 않았습니다.")
    for raw in raw_changes:
        try:
            if defaults:
                field = registry.get((raw["section"], raw["path"]))
                if field is None:
                    raise ValueError("기준값을 적용할 입력 항목을 확인해 주세요.")
                change = _policy_change(raw, field)
                current_value, row_name = None, ""
                for item in items.values():
                    if item["section"] == change["section"] and _identity(item["row"]) and all(
                        change.get(key) == value for key, value in _identity(item["row"]).items()
                    ) and all(change.get(key) == value for key, value in _identity(item["parent"] or {}, parent=True).items()):
                        current_value = item["row"].get(field["path"].removeprefix("items[]."))
                        row_name = _row_label(item["row"])
                        break
            else:
                change, field, current_value, row_name = _restore_change(raw, fields, items)
                if bound:
                    change = _bind_change(change, field, bindings, approved)
            if field["cardinality"] == "single":
                current_value = _current_form(state).get(field["path"])
            issues = _validate_change(change, len(candidates), registry)
            if change["operation"] == "add" and isinstance(change.get("value"), dict):
                prefix = "items[]." if change["section"] == "goal_periods" and change["path"] == "items" else ""
                required = [spec["path"].removeprefix(prefix) for spec in registry.values()
                            if spec["section"] == field["section"] and spec["writable_by_ai"] and not spec["nullable"]
                            and spec["path"].startswith(prefix) and "[]." not in spec["path"].removeprefix(prefix)]
                missing = [key for key in required if key not in change["value"]]
                if missing:
                    names = [registry[(field["section"], prefix + key)]["label"] for key in missing]
                    raise ValueError(f"{field['label']}: 새 항목의 필수값을 알려 주세요 ({', '.join(names)}).")
            if issues:
                raise ValueError(f"{field['label']}: {_validation_message(issues[0].code)}")
            candidate = {"change": change, "description": _description(change, field, current_value, row_name)}
            candidates.append(candidate)
            if not defaults and _requires_review(state["message"], change, field, row_name):
                review_candidates.append({"change_ref": f"c{len(candidates) - 1}", **candidate["description"]})
        except ValueError as exc:
            questions.append(str(exc))
    return {"candidates": candidates, "review_candidates": review_candidates, "questions": questions}


async def _review(state: InputAssistState) -> dict:
    result = await secure_ainvoke(
        review_chain, prompt_messages(REVIEW_PROMPT, {
            "message": state["message"], "changes": state["review_candidates"], "reference": _reference_for_model(state),
        }, step_id=state["step_id"]),
        purpose=ModelPurpose.INPUT_ASSIST, operation_name="input_assist.review",
        sensitive_payload=_sensitive_payload(state), prompt=REVIEW_PROMPT,
    )
    requested = {item["change_ref"] for item in state["review_candidates"]}
    verdicts: dict[str, list] = {}
    for item in result.items:
        verdicts.setdefault(item.change_ref, []).append(item)
    questions = list(state["questions"])
    candidates = []
    for index, candidate in enumerate(state["candidates"]):
        ref = f"c{index}"
        if ref not in requested:
            candidates.append(candidate)
            continue
        decisions = verdicts.get(ref, [])
        if len(decisions) == 1 and decisions[0].accepted:
            candidates.append(candidate)
        else:
            reason = decisions[0].reason if len(decisions) == 1 else "변경 의도를 확인할 수 없습니다."
            questions.append(f"{candidate['description']['name']}: {reason or '변경할 값을 다시 알려 주세요.'}")
    return {"candidates": candidates, "questions": questions}


async def _respond(state: InputAssistState) -> dict:
    result = await secure_ainvoke(
        llm, prompt_messages(RESPOND_PROMPT, {
            "message": state["message"], "changes": [item["description"] for item in state["candidates"]],
            "questions": state["questions"],
        }, step_id=state["step_id"]), purpose=ModelPurpose.INPUT_ASSIST, operation_name="input_assist.respond",
        sensitive_payload=_sensitive_payload(state), prompt=RESPOND_PROMPT,
    )
    changes = [item["change"] for item in state["candidates"]]
    return {"output": {
        "response": result.content, "step_id": state["step_id"],
        "action": {"type": "form_change", "target": "draft_form", "user_requested": True, "changes": changes} if changes else None,
    }}
