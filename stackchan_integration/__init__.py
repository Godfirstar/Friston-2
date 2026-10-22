"""Friston-3 <-> M5Stack StackChan optional USB display integration."""

from .stackchan_bridge import StackChanBridge
from .face_events import StackChanFaceEvents

__all__ = ["StackChanBridge", "StackChanFaceEvents"]
