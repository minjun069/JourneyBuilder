"""Chart Interpret Agent package public surface, loaded lazily."""

__all__ = [
    "_fallback_response",
    "build_graph",
    "graph",
    "recalculation_claims",
    "unauthorized_numeric_claims",
    "unsupported_causal_claims",
]


def __getattr__(name: str):
    if name in {"build_graph", "graph"}:
        from . import builder

        return getattr(builder, name)
    if name in {
        "_fallback_response",
        "recalculation_claims",
        "unauthorized_numeric_claims",
        "unsupported_causal_claims",
    }:
        from . import service

        return getattr(service, name)
    raise AttributeError(name)
