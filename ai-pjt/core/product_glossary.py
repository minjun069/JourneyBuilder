"""Validated, product-owned terminology used before general QA retrieval."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


FIXTURE_RELATIVE_PATH = Path(
    "contract-fixtures/ai-lifecycle/v2/product-glossary.json"
)
EXPECTED_FIXTURE_KIND = "product-glossary"
EXPECTED_SCHEMA_VERSION = "1.0"

EXPLANATION_CUES = (
    "뜻",
    "의미",
    "정의",
    "설명",
    "뭐",
    "무엇",
    "말하",
    "개념",
    "차이",
    "단위",
    "란",
)
QUALITATIVE_CHOICE_CUES = ("정확히", "기간", "어느정도", "기준")
QUALITATIVE_CHOICE_TERM_IDS = frozenset({
    "stability-oriented",
    "balanced-oriented",
    "risk-tolerant",
    "short-term-oriented",
    "long-term-oriented",
    "return-oriented",
    "liquidity-oriented",
})

_IGNORED_CHARACTERS = re.compile(r"[\s\-_/·()\[\]{}'\".,:;!?]+")


class ProductGlossaryError(ValueError):
    """Raised when the product glossary is missing or violates its contract."""


@dataclass(frozen=True)
class ProductGlossaryEntry:
    term_id: str
    term: str
    aliases: tuple[str, ...]
    definition: str
    caution: str | None
    category: str
    source_ids: tuple[str, ...]

    @property
    def surfaces(self) -> tuple[str, ...]:
        return (self.term, *self.aliases)

    def answer(self) -> str:
        answer = f"이 제품에서 ‘{self.term}’의 뜻은 {self.definition}"
        if self.caution:
            answer = f"{answer} {self.caution}"
        return answer


@dataclass(frozen=True)
class ProductGlossary:
    version: str
    entries: tuple[ProductGlossaryEntry, ...]
    _surface_index: tuple[tuple[str, ProductGlossaryEntry], ...]

    def find(self, text: str) -> ProductGlossaryEntry | None:
        matches = self.find_all(text, limit=1)
        return matches[0] if matches else None

    def find_all(self, text: str, *, limit: int = 3) -> tuple[ProductGlossaryEntry, ...]:
        normalized_text = normalize_term(text)
        if not normalized_text:
            return ()
        matched_surfaces: list[str] = []
        matched_entries: list[ProductGlossaryEntry] = []
        matched_ids: set[str] = set()
        for surface, entry in self._surface_index:
            if not _surface_is_present(surface, text, normalized_text):
                continue
            if entry.term_id in matched_ids:
                continue
            if any(surface in longer_surface for longer_surface in matched_surfaces):
                continue
            matched_surfaces.append(surface)
            matched_entries.append(entry)
            matched_ids.add(entry.term_id)
            if len(matched_entries) >= limit:
                break
        return tuple(matched_entries)

    def contains_surface(self, value: str) -> bool:
        normalized = normalize_term(value)
        return any(surface == normalized for surface, _ in self._surface_index)


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _IGNORED_CHARACTERS.sub("", normalized)


def _surface_is_present(surface: str, text: str, normalized_text: str) -> bool:
    if len(surface) != 1 or not surface.isalnum():
        return surface in normalized_text
    normalized_original = unicodedata.normalize("NFKC", text).casefold()
    return re.search(
        rf"(?<![0-9a-z가-힣]){re.escape(surface)}"
        rf"(?=$|[\s'\".,?!은는이가을를의와과로에])",
        normalized_original,
    ) is not None


def resolve_product_glossary_path() -> Path:
    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[2] / FIXTURE_RELATIVE_PATH,
        module_path.parents[1] / FIXTURE_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductGlossaryError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ProductGlossaryError(f"{field} must be an array")
    result = tuple(_non_empty_string(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ProductGlossaryError(f"{field} must not contain duplicates")
    return result


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductGlossaryError("product glossary could not be loaded") from exc
    if not isinstance(payload, dict):
        raise ProductGlossaryError("product glossary root must be an object")
    if payload.get("fixture_kind") != EXPECTED_FIXTURE_KIND:
        raise ProductGlossaryError("product glossary fixture_kind is unsupported")
    if payload.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise ProductGlossaryError("product glossary schema_version is unsupported")
    if payload.get("status") != "active":
        raise ProductGlossaryError("product glossary is not active")
    if payload.get("language") != "ko-KR":
        raise ProductGlossaryError("product glossary language is unsupported")
    _non_empty_string(payload.get("owner"), "owner")
    as_of_date = _non_empty_string(payload.get("as_of_date"), "as_of_date")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of_date) is None:
        raise ProductGlossaryError("as_of_date must use YYYY-MM-DD")
    _non_empty_string(payload.get("scope"), "scope")
    return payload


@lru_cache(maxsize=4)
def load_product_glossary(path: Path | None = None) -> ProductGlossary:
    fixture_path = resolve_product_glossary_path() if path is None else path
    payload = _read_payload(fixture_path)
    version = _non_empty_string(payload.get("glossary_version"), "glossary_version")
    sources = payload.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ProductGlossaryError("sources must be a non-empty object")
    for source_id, source_path in sources.items():
        _non_empty_string(source_id, "sources key")
        _non_empty_string(source_path, f"sources.{source_id}")

    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ProductGlossaryError("groups must be a non-empty array")

    entries: list[ProductGlossaryEntry] = []
    term_ids: set[str] = set()
    surfaces: dict[str, str] = {}
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ProductGlossaryError(f"groups[{group_index}] must be an object")
        group_id = _non_empty_string(group.get("group_id"), f"groups[{group_index}].group_id")
        category = _non_empty_string(group.get("category"), f"groups[{group_index}].category")
        source_ids = _string_list(
            group.get("source_ids"),
            f"groups[{group_index}].source_ids",
            allow_empty=False,
        )
        unknown_sources = set(source_ids) - set(sources)
        if unknown_sources:
            raise ProductGlossaryError(
                f"{group_id} references unknown sources: {sorted(unknown_sources)}"
            )
        raw_entries = group.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ProductGlossaryError(f"{group_id}.entries must be a non-empty array")
        for entry_index, raw_entry in enumerate(raw_entries):
            field = f"{group_id}.entries[{entry_index}]"
            if not isinstance(raw_entry, dict):
                raise ProductGlossaryError(f"{field} must be an object")
            term_id = _non_empty_string(raw_entry.get("term_id"), f"{field}.term_id")
            if term_id in term_ids:
                raise ProductGlossaryError(f"duplicate term_id: {term_id}")
            term_ids.add(term_id)
            term = _non_empty_string(raw_entry.get("term"), f"{field}.term")
            aliases = _string_list(
                raw_entry.get("aliases"),
                f"{field}.aliases",
                allow_empty=True,
            )
            definition = _non_empty_string(
                raw_entry.get("definition"),
                f"{field}.definition",
            )
            raw_caution = raw_entry.get("caution")
            caution = None
            if raw_caution is not None:
                caution = _non_empty_string(raw_caution, f"{field}.caution")
            entry = ProductGlossaryEntry(
                term_id=term_id,
                term=term,
                aliases=aliases,
                definition=definition,
                caution=caution,
                category=category,
                source_ids=source_ids,
            )
            for surface in entry.surfaces:
                normalized = normalize_term(surface)
                if not normalized:
                    raise ProductGlossaryError(f"{field} has an empty normalized surface")
                owner = surfaces.get(normalized)
                if owner is not None and owner != term_id:
                    raise ProductGlossaryError(
                        f"duplicate normalized surface {surface!r}: {owner}, {term_id}"
                    )
                surfaces[normalized] = term_id
            entries.append(entry)

    by_id = {entry.term_id: entry for entry in entries}
    surface_index = tuple(
        sorted(
            (
                (surface, by_id[term_id])
                for surface, term_id in surfaces.items()
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
    )
    return ProductGlossary(
        version=version,
        entries=tuple(entries),
        _surface_index=surface_index,
    )


def answer_product_term_question(message: str, current_query: str = "") -> str | None:
    """Return a deterministic product definition for an explicit explanation request."""

    text = f"{message} {current_query}"
    compact = normalize_term(text)
    entries = load_product_glossary().find_all(text)
    if not entries:
        return None
    if not any(normalize_term(cue) in compact for cue in EXPLANATION_CUES):
        if not any(
            normalize_term(cue) in compact
            for cue in QUALITATIVE_CHOICE_CUES
        ):
            return None
        entries = tuple(
            entry
            for entry in entries
            if entry.term_id in QUALITATIVE_CHOICE_TERM_IDS
        )
        if not entries:
            return None
    return "\n\n".join(entry.answer() for entry in entries)
