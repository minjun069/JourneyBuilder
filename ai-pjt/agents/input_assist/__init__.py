"""Input Assist Agent package public surface, loaded without import cycles."""

__all__ = ["build_graph", "graph"]


def __getattr__(name: str):
    if name in {"build_graph", "graph"}:
        from . import builder

        return getattr(builder, name)
    raise AttributeError(name)
