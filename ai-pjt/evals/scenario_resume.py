"""Reuse completed scenario prefixes without repeating model requests."""
from copy import deepcopy
import json


def read_rows(directory):
    result = directory / "results.json"
    if result.exists():
        return json.loads(result.read_text())
    journal = directory / "turns.jsonl"
    rows = []
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return rows


def validate_prefix(rows, prepared):
    if len(rows) > len(prepared):
        raise ValueError("saved turns exceed the current input")
    for row, turn in zip(rows, prepared):
        if row.get("repetition") != 1 or any(row.get(k) != turn.get(k) for k in
                ("ordinal", "step_id", "message", "raw_turn", "customer_info")):
            raise ValueError("saved turns do not match the current input")


def choose_run(roots, label, case, expected_turns):
    candidates = []
    for root in roots:
        path = root / label / case
        if not (path / "manifest.json").exists():
            continue
        rows = read_rows(path)
        if len(rows) == expected_turns:
            return path, True
        candidates.append((len(rows), path))
    if not candidates:
        return None, False
    return max(candidates, key=lambda item: item[0])[1], False


def restore_context(graph, context, rows, mode):
    if not rows:
        return context
    last = rows[-1]
    if mode == "vanilla":
        graph.history = deepcopy(last["provider_input"]["contents"])
        response = (last.get("output") or {}).get("response")
        if response:
            graph.history.append({"role": "model", "parts": [{"text": response}]})
        return {}
    state = deepcopy(last["graph_input"])
    context = state["conversation_context"]
    if last["status"] in {"error", "timeout"}:
        return context
    # This node only advances the local context; it never calls a model.
    from graph.context_node import persist_context
    state.update(output=deepcopy(last.get("output", {})), plan_issues=deepcopy(last.get("plan_issues", [])))
    return persist_context(state).get("conversation_context", context)
