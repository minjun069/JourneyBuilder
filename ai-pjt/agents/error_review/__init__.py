"""Error Review Agent package public surface, loaded without import cycles."""

__all__ = [
    "ReviewItem",
    "ReviewResult",
    "_validated_item",
    "analyze_review_items",
    "build_graph",
    "graph",
]


def __getattr__(name: str):
    if name in {"build_graph", "graph"}:
        from . import builder

        return getattr(builder, name)
    if name in {"ReviewItem", "ReviewResult", "_validated_item", "analyze_review_items"}:
        from . import service

        return getattr(service, name)
    raise AttributeError(name)
