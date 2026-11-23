"""Translate authored scenario deltas using the selected checkout's field contract.

No model imports or calls. Only explicit scenario inputs become confirmed data;
suggested AI actions never mutate this state.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path
import re
import runpy


SECTIONS = {
    "basic-info": "basic_info", "financial-mindset": "financial_mindset",
    "financial-satisfaction": "financial_satisfaction",
    "financial-assets": "financial_assets", "pension-assets": "pension_assets",
    "real-estate": "real_estate_assets", "liabilities": "liabilities",
    "income": "incomes", "expenses": "expenses",
}
LIST_LABELS = {
    "financial-assets": "금융자산", "pension-assets": "연금자산",
    "real-estate": "부동산자산", "liabilities": "부채", "income": "소득",
    "expenses": "지출",
}
ALIASES = {
    "basic_info": {"결혼 여부": "marital_status", "월 소득": "monthly_income",
                   "성향": "tendency", "관심사": "interest", "재무 니즈": "need",
                   "은퇴 나이": "retirement_age", "분석 종료 나이": "end_age"},
    "financial_mindset": {"현 재무상황": "current_situation"},
    "financial_satisfaction": {"현금흐름 관리": "cashflow_score", "위험 관리": "risk_score",
                               "투자": "investment_score", "재무관리": "management_score",
                               "상속": "inheritance_score"},
    "financial_assets": {"목적": "purpose"},
    "pension_assets": {"구분": "type", "평가금액": "current_value", "개시 연령": "pension_start_age"},
    "real_estate_assets": {"구분": "type", "이름": "name", "평가금액": "current_value",
                           "취득시기": "acquisition_date", "월세소득": "monthly_rental_income"},
    "liabilities": {"구분": "type", "대출일시": "loan_date", "만기일시": "maturity_date"},
    "incomes": {"구분": "type", "내용": "name", "고정/변동": "is_fixed"},
    "expenses": {"항목": "name", "고정/변동": "is_fixed", "주기": "frequency"},
}


def normalized(label):
    return re.sub(r"\s+|\([^)]*\)", "", label)


def load_scenario(path: Path, schema_path: Path):
    from jsonschema import Draft202012Validator
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(data))
    if errors:
        error = errors[0]
        raise ValueError(f"scenario schema: {error.json_path}: {error.message}")
    return data


def load_registry(worktree: Path):
    # Both historical and current Backend registries are standalone dataclasses.
    # runpy avoids importing Django or a different worktree's same-named modules.
    namespace = runpy.run_path(str(worktree / "backend-pjt/consulting/field_registry.py"))
    return namespace["get_step_field_registry"]


def validate_value(value, spec):
    if value is None:
        if not spec.get("nullable", True):
            raise ValueError(f"{spec['section']}.{spec['path']}: null is not allowed")
        return
    kind = spec["value_type"]
    types = {"integer": int, "decimal": (int, float), "string": str,
             "date": str, "boolean": bool, "object": dict, "array": list}
    if kind in types and (not isinstance(value, types[kind]) or
                         (kind in {"integer", "decimal"} and isinstance(value, bool))):
        raise ValueError(f"{spec['section']}.{spec['path']}: expected {kind}")
    rules = spec.get("constraints", {})
    if "choices" in rules and value not in rules["choices"]:
        raise ValueError(f"{spec['section']}.{spec['path']}: unsupported choice {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < rules.get("min", float("-inf")) or value > rules.get("max", float("inf")):
            raise ValueError(f"{spec['section']}.{spec['path']}: out of range")
    if isinstance(value, str) and len(value) > rules.get("max_length", float("inf")):
        raise ValueError(f"{spec['section']}.{spec['path']}: too long")
    if kind == "date":
        date.fromisoformat(value)


def converted(value, path):
    if path == "is_fixed" and isinstance(value, str):
        if value not in {"고정", "변동"}:
            raise ValueError(f"unknown fixed/variable value: {value}")
        return value == "고정"
    if path == "frequency" and isinstance(value, str):
        if value == "해당 없음":
            return {"none": True, "unit": None, "count": None}
        match = re.fullmatch(r"(연|월|주)\s*(\d+)회", value)
        if not match or int(match[2]) < 1:
            raise ValueError(f"unknown expense frequency: {value}")
        return {"none": False, "unit": match[1], "count": int(match[2])}
    return deepcopy(value)


class ScenarioInputs:
    def __init__(self, registry, preserve_source_values=False):
        self.registry = registry
        self.preserve_source_values = preserve_source_values
        self.notes = []
        self.saved = {}  # Unvisited sections stay absent, not confirmed empty.

    def _row(self, section, delta, specs):
        fields = {s["path"]: s for s in specs if s["section"] == section}
        labels = {normalized(s["label"]): path for path, s in fields.items()}
        labels.update({normalized(k): v for k, v in ALIASES.get(section, {}).items()})
        result = {}
        for label, value in delta.items():
            path = labels.get(normalized(label))
            if path is None or path not in fields:
                raise ValueError(f"unmapped input: {section}.{label}")
            if path in result:
                raise ValueError(f"duplicate aliases for {section}.{path}")
            value = converted(value, path)
            spec = fields[path]
            choices = spec.get("constraints", {}).get("choices")
            if self.preserve_source_values and value is not None and choices and value not in choices:
                self.notes.append(f"{section}.{path}: 원문 {value!r} 유지; 제품 허용값 밖의 입력")
                spec = {**spec, "constraints": {k: v for k, v in spec["constraints"].items() if k != "choices"}}
            validate_value(value, spec)
            result[path] = value
        return result

    def apply(self, step, delta):
        self.notes = []
        specs = self.registry(step)
        notes = []
        if step == "financial-goals":
            self._goals(delta, specs, notes)
        elif step in LIST_LABELS:
            label, section = LIST_LABELS[step], SECTIONS[step]
            if set(delta) - {label}:
                raise ValueError(f"unknown list input for {step}: {sorted(set(delta) - {label})}")
            if label in delta:
                rows = delta[label]
                if rows is not None and not isinstance(rows, list):
                    raise ValueError(f"{label} must be an array or null")
                self.saved[section] = None if rows is None else [
                    {**self._row(section, row, specs), "id": i + 1}
                    for i, row in enumerate(rows)
                ]
        elif step in SECTIONS:
            section = SECTIONS[step]
            source = deepcopy(delta)
            nested = {}
            if step == "basic-info":
                for label, key in (("자녀", "children"), ("부모", "parents"), ("기타 부양가족", "others")):
                    if label in source:
                        rows = source.pop(label)
                        if rows is not None and not isinstance(rows, list):
                            raise ValueError(f"{label} must be an array or null")
                        nested[key] = None if rows is None else [self._dependent(row, key, specs) for row in rows]
            update = self._row(section, source, specs)
            previous = deepcopy(self.saved.get(section, {}))
            if nested:
                update["dependents"] = {**previous.get("dependents", {}), **nested}
            if delta:
                self.saved[section] = {**previous, **update}
        elif delta:
            by_label = {label: target for target, label in LIST_LABELS.items()}
            if not self.preserve_source_values or set(delta) - by_label.keys():
                raise ValueError(f"step {step} has no editable form")
            for label, value in delta.items():
                target = by_label[label]
                self.apply(target, {label: value})
                notes.append(f"{step}: 명시된 {label} 변경을 {SECTIONS[target]}에 반영")
        form = {}
        if step in SECTIONS:
            value = deepcopy(self.saved.get(SECTIONS[step]))
            form = ({"assets": value} if step in LIST_LABELS else value) if value is not None else {}
            if step in LIST_LABELS and SECTIONS[step] in self.saved:
                form = {"assets": value}
        elif step == "financial-goals":
            form = {key: deepcopy(self.saved[key]) for key in ("goal_periods", "goal_milestones") if key in self.saved}
        return {"saved_data": deepcopy(self.saved), "current_form": form}, specs, notes + self.notes

    @staticmethod
    def _dependent(row, key, specs):
        fields = {s["path"]: s for s in specs}
        result = {}
        for label, value in row.items():
            path = {"나이": "age", "관계": "name"}.get(label)
            full = f"dependents.{key}[].{path}"
            if full not in fields:
                raise ValueError(f"unknown dependent field: {key}.{label}")
            validate_value(value, fields[full])
            result[path] = deepcopy(value)
        return result

    def _goals(self, delta, specs, notes):
        fields = {(s["section"], s["path"]): s for s in specs}
        for label, rows in delta.items():
            match = re.fullmatch(r"은퇴 (전|후) (기간별|일시) (지출|소득)", label)
            if not match or not isinstance(rows, list):
                raise ValueError(f"unsupported goal input: {label}; expected array")
            phase = "before" if match[1] == "전" else "after"
            section = "goal_periods" if match[2] == "기간별" else "goal_milestones"
            if section == "goal_periods" and match[3] != "지출":
                raise ValueError("periodic income has no goal_periods form contract")
            translated = []
            for row in rows:
                if section == "goal_periods":
                    allowed = {"시작 나이", "종료 나이", "항목", "내용", "금액(원/월)", "고정/변동"}
                    item = {"name": row.get("내용") or row.get("항목"),
                            "amount": row.get("금액(원/월)"),
                            "is_fixed": converted(row.get("고정/변동"), "is_fixed")}
                    entry = {"phase": phase, "from_age": row.get("시작 나이"),
                             "to_age": row.get("종료 나이"), "items": [item]}
                    for key, value in item.items():
                        validate_value(value, fields[(section, "items[]." + key)])
                    item["id"] = 1
                else:
                    allowed = {"나이", "분류", "내용", "금액(원)", "비고"}
                    category = row.get("분류", match[3])
                    if category == "기타":
                        category = match[3]
                        notes.append(f"{label}: 분류 '기타'는 그룹의 {category}로 매핑 (제품 분류: 소득/지출)")
                    entry = {"phase": phase, "age": row.get("나이"), "category": category,
                             "content": row.get("내용"), "amount": row.get("금액(원)"), "note": row.get("비고")}
                if set(row) - allowed:
                    raise ValueError(f"unknown goal fields: {sorted(set(row) - allowed)}")
                for key, value in entry.items():
                    validate_value(value, fields[(section, key)])
                translated.append(entry)
            # Arrays replace only the named phase/group; other phases survive.
            existing = self.saved.get(section, [])
            kept = [row for row in existing if row["phase"] != phase or
                    (section == "goal_milestones" and row["category"] != match[3])]
            self.saved[section] = [{**row, "id": i + 1} for i, row in enumerate(kept + translated)]


def prepare_turns(data, registry, preserve_source_values=False):
    state = ScenarioInputs(registry, preserve_source_values)
    turns = []
    for step in data:
        for index, dialogue in enumerate(step["대화"], 1):
            customer, fields, notes = state.apply(step["step_id"], dialogue["입력값"])
            turns.append({"ordinal": len(turns) + 1, "step_id": step["step_id"],
                          "process": step["프로세스"], "step_turn": index,
                          "message": dialogue["사용자"], "input_delta": deepcopy(dialogue["입력값"]),
                          "customer_info": customer, "field_registry": fields, "mapping_notes": notes})
    return turns
