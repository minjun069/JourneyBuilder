"""QA Agent package public surface, loaded lazily at composition time."""

__all__ = ["build_graph", "graph"]


def __getattr__(name: str):
    if name in __all__:
        from . import builder

        return getattr(builder, name)
    raise AttributeError(name)
