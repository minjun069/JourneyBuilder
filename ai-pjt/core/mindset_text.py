"""Recover display text from known mindset output wrappers, without rewriting prose."""

import json
import re


TEXT_KEYS = (
    "summary", "response", "values", "past_experience", "current_situation",
    "current_challenge", "invest_philosophy", "future_goals",
)
_KEY = r'["\x27`*]*'
_CONTROL = re.compile(r'(?<!\w)' + _KEY + r'(sufficient|turn_kind)' + _KEY + r'\s*[:=]')
_VALUE = re.compile(
    r'(?<!\w)' + _KEY + r'(?:sufficient' + _KEY + r'\s*[:=]\s*' + _KEY
    + r'(?:true|false)|turn_kind' + _KEY + r'\s*[:=]\s*' + _KEY
    + r'(?:answer|clarification|skip))\b' + _KEY + r'\s*[,;]?', re.IGNORECASE,
)
_LABEL = re.compile(r'^\s*(?:[-*]\s+)?' + _KEY + '(' + '|'.join(TEXT_KEYS) + ')' + _KEY + r'\s*:\s*')


def clean_mindset_text(value: str, *, preferred: str = "summary", depth: int = 0) -> str:
    """Only unwrap recognized serialization; ambiguous control syntax fails closed."""
    if depth > 3:
        raise ValueError("invalid mindset display text")
    text = value.strip()
    fenced = re.fullmatch(r'```(?:json|yaml|text)?\s*\n?([\s\S]*?)\n?```', text)
    candidate = fenced[1].strip() if fenced else text
    if candidate.startswith('{'):
        try:
            payload = json.loads(candidate)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and set(payload) & set(TEXT_KEYS + ("sufficient", "turn_kind")):
            if set(payload) - set(TEXT_KEYS + ("sufficient", "turn_kind")):
                raise ValueError("invalid mindset display text")
            keys = [key for key in TEXT_KEYS if key in payload and key != "response"]
            key = preferred if preferred in payload else (keys[0] if len(keys) == 1 else None)
            if key is None or not isinstance(payload[key], str):
                raise ValueError("invalid mindset display text")
            return clean_mindset_text(payload[key], preferred=preferred, depth=depth + 1)
    cleaned = _VALUE.sub('', candidate)
    label = _LABEL.match(cleaned)
    if label:
        cleaned = cleaned[label.end():]
    changed = cleaned != candidate
    if _CONTROL.search(cleaned) or re.search(r'(?<!\w)' + _KEY + '(?:' + '|'.join(TEXT_KEYS) + ')' + _KEY + r'\s*:', cleaned):
        raise ValueError("invalid mindset display text")
    if not changed:
        return value
    cleaned = cleaned.strip().rstrip(',;').strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in '\"\x27`':
        cleaned = cleaned[1:-1]
    if not cleaned or cleaned.startswith('{') or cleaned.endswith('}'):
        raise ValueError("invalid mindset display text")
    return clean_mindset_text(cleaned, preferred=preferred, depth=depth + 1)


def clean_mindset_context(value):
    """Sanitize only a model-facing copy; persisted conversation stays untouched."""
    if isinstance(value, str):
        try:
            return clean_mindset_text(value)
        except ValueError:
            return ""
    if isinstance(value, dict):
        return {key: clean_mindset_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_mindset_context(item) for item in value]
    return value
