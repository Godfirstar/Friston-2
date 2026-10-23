"""Friston-3 StackChan integration, with hardware imports loaded on demand.

The offline voice installer must work without importing a serial driver, and
running a submodule with ``python -m`` must not preload that same submodule.
"""

__all__ = ["StackChanBridge", "StackChanFaceEvents"]


def __getattr__(name):
    if name == "StackChanBridge":
        from .stackchan_bridge import StackChanBridge
        return StackChanBridge
    if name == "StackChanFaceEvents":
        from .face_events import StackChanFaceEvents
        return StackChanFaceEvents
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
