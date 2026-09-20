"""Deterministic QA safety and disclosure policy."""

import re


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
