"""Validate input before native injection; surface coordinates remain origin pixels."""

import math

from .model import ProtocolError, Surface, uint


def validate_event(event: dict, surface: Surface) -> dict:
    if not isinstance(event, dict) or event.get("surface_id") != surface.surface_id:
        raise ProtocolError("invalid input surface")
    kind = event.get("kind")
    if kind in ("motion", "touch"):
        for name, maximum in (("x", surface.size.w), ("y", surface.size.h)):
            value = event.get(name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value < maximum
            ):
                raise ProtocolError("pointer outside surface")
        if kind == "touch":
            uint(event.get("slot"), 8, "slot")
            if event.get("phase") not in ("down", "move", "up", "cancel"):
                raise ProtocolError("invalid touch phase")
    elif kind in ("key", "button"):
        if kind == "button" and ("x" in event or "y" in event):
            validate_event({**event, "kind": "motion"}, surface)
        if type(event.get("pressed")) is not bool:
            raise ProtocolError("pressed must be boolean")
        uint(event.get("keycode" if kind == "key" else "button"), 16, kind)
        if kind == "key":
            uint(event.get("modifiers"), 6, "modifiers")
    elif kind == "axis":
        if type(event.get("discrete")) is not bool:
            raise ProtocolError("discrete must be boolean")
        for name in ("dx", "dy"):
            value = event.get(name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or abs(value) > 32767
            ):
                raise ProtocolError("invalid scroll delta")
    elif kind == "text-commit":
        text = event.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
            raise ProtocolError("invalid text commit")
    else:
        raise ProtocolError("unknown input event")
    return event
