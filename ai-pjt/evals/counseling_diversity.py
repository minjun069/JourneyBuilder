"""Check the fixed synthetic case design, never counseling quality or results.

The preserved prefixes are canonical case lists from e1fcbaa. Family relations
and checkpoint links are structural checks; meaning, realism and observed model
behavior still require a separate review. Expected understanding is never used
as an actual response or copied into this module's summary.
"""

from collections import Counter, defaultdict
import hashlib
import json
from typing import Any


AXES = (
    "same-question-different-background",
    "same-finances-different-concern",
    "understanding-revision",
    "reduced-intervention",
)
ORIGINAL_COUNTS = {"coverage": 54, "behavior": 26}
TOTAL_COUNTS = {"coverage": 120, "behavior": 80}
ORIGINAL_SHA256 = {
    "coverage": "bf79be9d9335d30050ebeb0e33aa411e09b6142225564d628fe6dfb4ba8f0862",
    "behavior": "9a4ffbee46729cf028ebce20c9532a21f47d47d111953591d5d102482b8e2f32",
}
STEP_IDS = tuple((
    "intro collection basic-info financial-mindset financial-satisfaction "
    "financial-info financial-assets pension-assets real-estate liabilities "
    "income expenses financial-goals presentation balance-sheet cash-flow "
    "financial-indicators financial-goal-result retirement-fund retirement-readiness "
    "retirement-simulation pension-schedule lifetime-cashflow required-savings general"
).split())
VARIANT_IDS = (
    "control-current-response", "baseline-aggregation-rag",
    "aggregation-rag-with-understanding", "generation-stage-rag",
)
ACTIONS = ("initialize", "revise", "retain", "clear")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{field} must be nonempty text")
    return value


def _strings(value: Any, field: str, minimum: int = 1) -> list[str]:
    _require(isinstance(value, list) and len(value) >= minimum,
             f"{field} must contain at least {minimum} text items")
    for item in value:
        _text(item, field)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _no_results(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"actual_response", "actual_understanding", "execution", "reviewer", "measured_outcomes", "official_scores"}:
                _require(child is None, f"{key} must remain empty in a design-only dataset")
            if key in {"llm_evaluated", "ax_gain_measured", "runtime_implemented", "official_certification"}:
                _require(child is False, f"{key} must remain false in a design-only dataset")
            _no_results(child)
    elif isinstance(value, list):
        for child in value:
            _no_results(child)


def _cases(document: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    _require(isinstance(document, dict), f"{kind} must be an object")
    cases = document.get("cases")
    _require(isinstance(cases, list) and len(cases) == TOTAL_COUNTS[kind],
             f"{kind}.cases must contain exactly {TOTAL_COUNTS[kind]} cases")
    _require(all(isinstance(case, dict) for case in cases), f"{kind}.cases must contain objects")
    prefix = cases[:ORIGINAL_COUNTS[kind]]
    digest = hashlib.sha256(_canonical(prefix).encode("utf-8")).hexdigest()
    _require(digest == ORIGINAL_SHA256[kind],
             f"{kind}: original {ORIGINAL_COUNTS[kind]} cases from e1fcbaa changed or reordered")
    added = cases[ORIGINAL_COUNTS[kind]:]
    expected_ids = ([f"coverage-{number}" for number in range(55, 121)] if kind == "coverage"
                    else [f"dialogue-{number:02d}" for number in range(1, 55)])
    _require([case.get("id") for case in added] == expected_ids,
             f"{kind}: appended case IDs must match the fixed ordered ID range")
    return added


def _families(cases: list[dict[str, Any]], kind: str) -> tuple[Counter, Counter]:
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter = Counter()
    for case in cases:
        field = f"{kind}.{case['id']}.variation"
        variation = case.get("variation")
        _require(isinstance(variation, dict), f"{field} must be an object")
        _require(set(variation) == {"axis", "family_id", "variant", "background", "financial_state"},
                 f"{field} must contain the agreed variation fields")
        axis = _text(variation["axis"], f"{field}.axis")
        _require(axis in AXES, f"{field}: unknown variation axis")
        for name in ("family_id", "variant", "background"):
            _text(variation[name], f"{field}.{name}")
        _strings(variation["financial_state"], f"{field}.financial_state", 2 if axis == AXES[1] else 0)
        if axis == AXES[1]:
            _require(len(set(variation["financial_state"])) >= 2,
                     f"{field}.financial_state needs at least two distinct facts")
        if kind == "coverage":
            _text(case.get("question"), f"{kind}.{case['id']}.question")
            _text(case.get("concern"), f"{kind}.{case['id']}.concern")
        else:
            _text(case.get("category"), f"{kind}.{case['id']}.category")
            _strings(case.get("answer_expectations"), f"{kind}.{case['id']}.answer_expectations")
        counts[axis] += 1
        families[variation["family_id"]].append(case)
    _require(set(counts) == set(AXES), f"{kind}: added cases must cover all four variation axes")
    family_counts: Counter = Counter()
    for family_id, members in families.items():
        field = f"{kind}.family {family_id}"
        variations = [case["variation"] for case in members]
        axes = {variation["axis"] for variation in variations}
        _require(len(axes) == 1, f"{field}: all members must share the same axis")
        axis = next(iter(axes))
        family_counts[axis] += 1
        _require(len({variation["variant"] for variation in variations}) == len(members),
                 f"{field}: variant labels must be unique")
        if axis not in AXES[:2]:
            continue
        _require(len(members) >= 2, f"{field}: paired variation needs at least two cases")
        if axis == AXES[0]:
            questions = [case["question"] if kind == "coverage" else case["turns"][0]["content"]
                         for case in members]
            _require(len(set(questions)) == 1, f"{field}: same-question texts must match exactly")
            _require(len({variation["background"] for variation in variations}) == len(members),
                     f"{field}: same-question backgrounds must differ")
        else:
            _require(len({_canonical(variation["financial_state"]) for variation in variations}) == 1,
                     f"{field}: same-finances facts must match exactly")
            concerns = ([case["concern"] for case in members] if kind == "coverage" else
                        [_canonical([case["category"], case["answer_expectations"]]) for case in members])
            _require(len(set(concerns)) >= 2, f"{field}: same-finances concerns must differ")
    return counts, family_counts


def _checkpoints(cases: list[dict[str, Any]]) -> tuple[Counter, int]:
    actions: Counter = Counter()
    revision_dialogues = 0
    checkpoint_fields = {"turn_id", "understanding_action", "expected_understanding", "must_preserve",
                         "must_not_infer", "answer_expectations", "retrieval_policy", "carry_forward_from"}
    for case in cases:
        field = f"behavior.{case['id']}"
        turns, checkpoints = case.get("turns"), case.get("checkpoints")
        _require(isinstance(turns, list) and len(turns) >= 3, f"{field}.turns needs at least three user turns")
        _require(isinstance(checkpoints, list) and len(checkpoints) == len(turns),
                 f"{field}.checkpoints must correspond one-to-one with turns")
        revise_turns: set[int] = set()
        has_revision_followup = False
        for number, (turn, checkpoint) in enumerate(zip(turns, checkpoints), 1):
            _require(isinstance(turn, dict) and set(turn) == {"turn_id", "role", "content"},
                     f"{field}.turns[{number}] must contain turn_id, role and content")
            _require(type(turn["turn_id"]) is int and turn["turn_id"] == number and turn["role"] == "user",
                     f"{field}: user turn IDs must be consecutive integers starting at 1")
            _text(turn["content"], f"{field}.turns[{number}].content")
            _require(isinstance(checkpoint, dict) and set(checkpoint) == checkpoint_fields,
                     f"{field}.checkpoints[{number}] must contain the agreed checkpoint fields")
            _require(type(checkpoint["turn_id"]) is int and checkpoint["turn_id"] == number,
                     f"{field}: checkpoint IDs must match their user turn")
            action = checkpoint["understanding_action"]
            _require(action in ACTIONS, f"{field}: unknown understanding action")
            _text(checkpoint["expected_understanding"], f"{field}.expected_understanding")
            for name in ("must_preserve", "must_not_infer", "answer_expectations"):
                _strings(checkpoint[name], f"{field}.{name}")
            _require(checkpoint["retrieval_policy"] in ("none", "conditional"),
                     f"{field}: unknown retrieval policy")
            reference = checkpoint["carry_forward_from"]
            _require(reference is None or (type(reference) is int and 1 <= reference < number),
                     f"{field}: carry_forward_from must refer to an earlier turn")
            if reference in revise_turns:
                has_revision_followup = True
            if action == "revise":
                revise_turns.add(number)
            actions[action] += 1
        variation = case.get("variation")
        _require(isinstance(variation, dict), f"{field}.variation must be an object")
        if variation.get("axis") == AXES[2]:
            _require(has_revision_followup,
                     f"{field}: revision variation needs a follow-up referencing an earlier revise turn")
            revision_dialogues += 1
    _require(actions["retain"] > 0, "behavior: at least one retain checkpoint is required")
    return actions, revision_dialogues


def validate_diversity(coverage: dict[str, Any], behavior: dict[str, Any]) -> dict[str, Any]:
    """Validate this fixed design and return counts, with no observed outcomes."""
    added = {"coverage": _cases(coverage, "coverage"), "behavior": _cases(behavior, "behavior")}
    _no_results(coverage)
    _no_results(behavior)
    _require(coverage.get("assessment_kind") == "document-coverage-not-AX"
             and coverage.get("runtime_implemented") is False,
             "coverage must remain document-only and runtime-unimplemented")
    _require(behavior.get("status") == "not-executed" and behavior.get("data_classification") == "synthetic-only",
             "behavior must remain synthetic-only and not-executed")
    variants = behavior.get("variants")
    _require(isinstance(variants, list) and all(isinstance(variant, dict) for variant in variants)
             and [variant.get("id") for variant in variants] == list(VARIANT_IDS),
             "behavior must retain the four comparison variants")
    _require(coverage.get("supported_steps") == list(STEP_IDS[:-1]) and coverage.get("fallback_step") == "general",
             "coverage must retain the 24 supported steps and general fallback")
    step_counts: Counter = Counter()
    for case in coverage["cases"]:
        steps = _strings(case.get("step_ids"), f"coverage.{case['id']}.step_ids")
        _require(len(set(steps)) == len(steps) and set(steps) <= set(STEP_IDS),
                 f"coverage.{case['id']}: step IDs must be supported and unique")
        step_counts.update(steps)
    missing_steps = [step for step in STEP_IDS if step_counts[step] < 4]
    _require(not missing_steps, f"coverage: each step needs at least four related cases; insufficient: {missing_steps}")
    actions, revision_dialogues = _checkpoints(added["behavior"])
    variations, families = {}, {}
    for kind, cases in added.items():
        counts, family_counts = _families(cases, kind)
        variations[kind], families[kind] = dict(counts), dict(family_counts)
    user_turn_count = sum(turn.get("role") == "user" for case in behavior["cases"] for turn in case["turns"])
    return {
        "schema_version": "counseling-diversity-summary/v1",
        "status": "design-validated-not-executed",
        "meaning": "Synthetic case structure and declared variations only; no measured counseling quality, financial outcomes or client diversity.",
        "llm_evaluated": False, "ax_gain_measured": False, "official_scores": None, "measured_outcomes": None,
        "original_revision": "e1fcbaa", "preserved_case_counts": dict(ORIGINAL_COUNTS),
        "preserved_case_sha256": dict(ORIGINAL_SHA256), "case_counts": dict(TOTAL_COUNTS),
        "added_case_counts": {kind: len(cases) for kind, cases in added.items()},
        "added_axis_counts": variations, "added_family_counts": families,
        "coverage_step_counts": {step: step_counts[step] for step in STEP_IDS},
        "dialogue_case_count": len(added["behavior"]), "checkpoint_count": sum(actions.values()),
        "checkpoint_action_counts": {action: actions[action] for action in ACTIONS},
        "revision_followup_dialogue_count": revision_dialogues,
        "behavior_variant_count": len(variants), "review_template_slots": len(behavior["cases"]) * len(variants),
        "behavior_user_turn_count": user_turn_count,
        "added_checkpoint_review_slots": sum(actions.values()) * len(variants),
        "turn_review_template_slots": user_turn_count * len(variants),
        "limitations": [
            "Declared backgrounds, shared financial facts and concern labels require a separate semantic review.",
            "Retain checkpoints describe unchanged meaning; string equality is neither required nor an observed result.",
            "Expected understanding and answer expectations are review references, never actual responses or ratings.",
        ],
    }
