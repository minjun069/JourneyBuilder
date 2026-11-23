"""Match local and remote measurements by identity, never by arrival order."""
from collections import defaultdict
import hashlib


FIELDS = ("call_id", "operation_name", "status", "duration_ms", "model_name",
          "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
          "reasoning_output_tokens", "usage_status", "cost_microusd")


def reconcile_calls(turns, events):
    by_trace = defaultdict(dict)
    for event in events:
        if event.get("stage") != "model" or not event.get("call_id"):
            continue
        trace = hashlib.sha256(("retirewise:" + event["trace_id"]).encode()).hexdigest()[:32]
        calls = by_trace[trace]
        call = {k: event[k] for k in FIELDS if k in event}
        if event.get("status") == "started":
            call.pop("duration_ms", None)
            call.update(status="unfinished", usage_status="unavailable")
            calls.setdefault(event["call_id"], call)
        else:
            calls[event["call_id"]] = call
    changed = []
    for turn in turns:
        calls = list(by_trace[turn["trace_id"]].values())
        if {c["call_id"]: c for c in turn.get("model_calls", [])} != {c["call_id"]: c for c in calls}:
            changed.append({"ordinal": turn["ordinal"], "trace_id": turn["trace_id"],
                            "previous_model_calls": turn.get("model_calls", []), "reconciled_model_calls": calls})
            turn["model_calls"] = calls
    return changed


def counts(observation):
    usage = observation.get("usageDetails") or {}
    return {"input_tokens": usage.get("input", 0) + usage.get("input_cached_tokens", 0),
            "output_tokens": usage.get("output", 0) + usage.get("output_reasoning_tokens", 0)}


def select_observation(call, choices):
    def score(observation):
        matched = sum(value == call[key] for key, value in counts(observation).items() if key in call)
        duration = (observation.get("metadata") or {}).get("duration_ms") == call.get("duration_ms")
        return (matched, bool(observation.get("usageDetails")), duration,
                observation.get("output") is not None, bool(observation.get("endTime")))
    return max(choices, key=score)


def cost_of(call, observation):
    if call.get("usage_status") != "reported":
        return None
    cost = observation.get("calculatedTotalCost")
    details = observation.get("costDetails") or {}
    # A zero from an unpriced model is not evidence that the request was free.
    if not details and not observation.get("modelId"):
        return None
    if cost is None and details:
        cost = details.get("total", sum(v for k, v in details.items() if k != "total" and isinstance(v, (int, float))))
    return cost if isinstance(cost, (int, float)) else None


def audit_trace(turn, trace):
    by_call = defaultdict(list)
    unidentifiable = []
    models = [o for o in trace.get("observations", []) if o.get("type") in {"GENERATION", "EMBEDDING"}]
    for observation in models:
        call_id = (observation.get("metadata") or {}).get("call_id")
        if call_id:
            by_call[call_id].append(observation)
        else:
            unidentifiable.append(observation.get("id"))
    calls = turn.get("model_calls", [])
    expected = {c["call_id"] for c in calls}
    missing, unexpected = sorted(expected - set(by_call)), sorted(set(by_call) - expected)
    selected, costs, mismatches = [], [], []
    for call in calls:
        if not by_call.get(call["call_id"]):
            continue
        observation = select_observation(call, by_call[call["call_id"]])
        mismatches.extend({"call_id": call["call_id"], "field": key} for key, value in counts(observation).items()
                          if key in call and call[key] != value)
        measurement = {k: observation.get(k) for k in ("id", "type", "name", "model", "modelId", "startTime", "endTime",
                                                       "usageDetails", "costDetails", "calculatedTotalCost")}
        measurement["call_id"] = call["call_id"]
        selected.append(measurement)
        costs.append(cost_of(call, observation))
    failed_request = turn.get("status") in {"error", "timeout", "policy_blocked"} and any(
        o.get("name") == "retirewise.request" and o.get("level") == "ERROR" for o in trace.get("observations", []))
    unfinished = [c["call_id"] for c in calls if c.get("status") == "unfinished"]
    complete = (not (missing or unexpected or unidentifiable or mismatches or unfinished)
                and (trace.get("output") is not None or failed_request) and all(o.get("endTime") for o in selected))
    priced = len(costs) == len(calls) and all(c is not None for c in costs)
    return {"status": "found", "complete": complete, "observations": len(selected), "raw_observations": len(models),
            "expected_calls": len(calls), "root_output_present": trace.get("output") is not None,
            "failed_request_recorded": failed_request, "unfinished_call_ids": unfinished,
            "duplicate_observations": sum(max(0, len(v) - 1) for v in by_call.values()),
            "missing_call_ids": missing, "unexpected_call_ids": unexpected, "unidentifiable_observations": unidentifiable,
            "usage_mismatches": mismatches, "measurements": selected,
            "timings_complete": all(o.get("endTime") for o in selected),
            "priced_observations": sum(c is not None for c in costs),
            "total_cost_usd": sum(costs) if complete and priced else None,
            "reported_cost_subtotal_usd": sum(c for c in costs if c is not None),
            "raw_trace_total_cost_usd": trace.get("totalCost"), "environment": trace.get("environment"), "release": trace.get("release")}
