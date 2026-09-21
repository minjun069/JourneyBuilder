"""Deterministic QA safety and disclosure policy."""

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from core.field_registry import FIELDS


_INTERNAL_KEYS = frozenset({
    "id", "client_item_id", "created_at", "updated_at", "source_id", "chunk_id",
    "source_locators", "start_offset", "end_offset", "task_id",
})
_PERSONAL_CONTEXT = re.compile(
    r"(?:제(?:가|게|것|정보|재무|소득|자산|나이|\s)|내(?:가|게|것|\s)|저(?:는|의)|"
    r"나의|우리|입력|작성|등록|저장|현재|지금|이\s*(?:값|금액|내용)|알려준)"
)
_GENERAL_EXPLANATION = re.compile(r"정의|뜻|용어|개념|의미|설명|뭐야|뭔가요|무엇|차이")
_GREETING = re.compile(r"(?:안녕(?:하세요)?|고마워(?:요)?|감사합니다|수고했어)[.!?\s]*")


def model_facts(value: Any) -> Any:
    """Keep values and units while leaving runtime identifiers outside prompts."""

    if isinstance(value, Mapping):
        return {
            key: model_facts(child)
            for key, child in value.items()
            if key not in _INTERNAL_KEYS
        }
    if isinstance(value, list):
        return [model_facts(child) for child in value]
    return deepcopy(value)


def has_reference_value(bindings: Mapping[str, Any]) -> bool:
    """An unrelated calculation result does not authorize a new official value."""

    if (
        isinstance(bindings.get("value"), (int, float))
        and not isinstance(bindings.get("value"), bool)
        and bindings.get("unit")
        and bindings.get("source_id")
    ):
        return True
    return any(
        has_reference_value(value)
        for value in bindings.values()
        if isinstance(value, Mapping)
    )


def relevant_customer_info(
    message: str, customer_info: Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    """Prefer the current form; expose saved values only when explicitly named."""

    if _GREETING.fullmatch(message.strip()) or (
        _GENERAL_EXPLANATION.search(message) and not _PERSONAL_CONTEXT.search(message)
    ):
        return {}
    current = customer_info.get("current_form")
    if not isinstance(current, Mapping):
        current = {
            key: value for key, value in customer_info.items()
            if key != "saved_data"
        }
    selected = {"current_form": model_facts(current)} if current else {}
    saved = customer_info.get("saved_data")
    if not isinstance(saved, Mapping):
        return selected
    text = message.replace(" ", "")
    named: dict[str, Any] = {}
    for field in FIELDS:
        if field.sensitivity == "operational" or "." in field.path or "[]" in field.path:
            continue
        if field.label.replace(" ", "") not in text and field.path not in message:
            continue
        section = saved.get(field.section)
        if not isinstance(section, Mapping) or field.path not in section:
            continue
        active = current.get(field.section)
        if field.step_id == step_id and (
            field.path in current
            or f"{field.section}.{field.path}" in current
            or isinstance(active, Mapping) and field.path in active
        ):
            continue
        named.setdefault(field.section, {})[field.path] = model_facts(section[field.path])
    if named:
        selected["saved_data"] = named
    return selected


_SOURCE_BLOCK_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|__)?"
    r"(?:출처|참고\s*문서|검색\s*문서|sources?)"
    r"\s*:?\s*(?:\*\*|__)?\s*:?\s*$",
    re.IGNORECASE,
)
_SOURCE_DISCLOSURE_LINE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:#{1,6}\s*)?(?:\*\*|__)?"
    r"(?:출처|참고\s*문서|검색\s*문서|원문\s*위치|source_id|문서\s*제목|sources?)"
    r"\s*:?\s*(?:\*\*|__)?\s*:?\s*.+$",
    re.IGNORECASE,
)
_CONTEXT_METADATA_LINE = re.compile(
    r"^\s*(?:\*\*|__)?\[(?:근거|source_id|원문\s*위치)\s*:.*\]"
    r"(?:\*\*|__)?\s*$",
    re.IGNORECASE,
)
_SOURCE_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_INLINE_SOURCE_NOTE = re.compile(
    r"\s*\((?:출처|원문\s*위치|source_id)\s*:[^)]*\)",
    re.IGNORECASE,
)


def requests_unbound_official_value(
    *,
    message: str,
    current_query: str,
    has_bindings: bool,
) -> bool:
    if has_bindings:
        return False
    text = f"{message} {current_query}".replace(" ", "")
    official_tokens = ("공식값", "기준값", "한도", "법정", "공단", "세율", "평균금액", "최신값")
    value_tokens = ("얼마", "수치", "금액", "비율", "몇", "%", "원")
    return any(token in text for token in official_tokens) and any(
        token in text for token in value_tokens
    )


def strip_retrieval_source_disclosure(answer: str) -> str:
    """Keep grounded prose while removing retrieved-document source disclosure."""

    kept: list[str] = []
    dropping_source_list = False
    for line in answer.splitlines():
        stripped = line.strip()
        if _SOURCE_BLOCK_HEADING.fullmatch(line):
            dropping_source_list = True
            continue
        if dropping_source_list:
            if not stripped or _SOURCE_LIST_ITEM.match(line):
                continue
            dropping_source_list = False
        if (
            _SOURCE_DISCLOSURE_LINE.fullmatch(line)
            or _CONTEXT_METADATA_LINE.fullmatch(line)
        ):
            continue
        kept.append(_INLINE_SOURCE_NOTE.sub("", line).rstrip())
    return "\n".join(kept).strip()
