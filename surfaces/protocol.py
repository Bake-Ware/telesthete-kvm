"""Rook-compatible JSON envelopes and typed surface snapshots."""

import json
import re
from dataclasses import asdict

from .model import Flags, ProtocolError, Rect, Role, Size, Surface, uint

MAX_CONTROL_BYTES = 2 * 1024 * 1024
CAPS = frozenset(
    {
        "surface-origin",
        "surface-hello",
        "surface-welcome",
        "surface-snapshot",
        "surface-add",
        "surface-update",
        "surface-remove",
        "atlas-layout",
        "view-hint",
        "surface-input",
        "focus-request",
        "resize-request",
        "close-request",
        "hide-request",
        "clipboard-offer",
        "clipboard-request",
        "clipboard-data",
        "lane-resync",
        "snapshot-request",
        "lane-nack",
        "lane-status",
        "surface-ping",
        "surface-release",
        "surface-lane",
        "window-catalog",
        "window-select",
    }
)
_SESSION = re.compile(r"[0-9a-f]{32}\Z")


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ProtocolError("duplicate JSON key")
        obj[key] = value
    return obj


def _constant(value):
    raise ProtocolError(f"non-finite JSON number: {value}")


def encode(
    cap: str, args: dict, *, target: str, message_id: str, session_id: str | None
) -> bytes:
    body = {
        "id": message_id,
        "cap": cap,
        "target": target,
        "args": {**args, "version": 1},
    }
    if session_id is not None:
        body["args"]["session_id"] = session_id
    data = json.dumps(
        body, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    decode(data, target=target, session_id=session_id)
    return data


def decode(data: bytes, *, target: str, session_id: str | None) -> dict:
    if len(data) > MAX_CONTROL_BYTES:
        raise ProtocolError("control message exceeds limit")
    try:
        body = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("invalid JSON control payload") from exc
    if not isinstance(body, dict) or body.get("cap") not in CAPS:
        raise ProtocolError("unknown surface capability")
    if body.get("target") != target:
        raise ProtocolError("message target mismatch")
    if not isinstance(body.get("id"), str) or not 0 < len(body["id"]) <= 128:
        raise ProtocolError("invalid message id")
    args = body.get("args")
    if (
        not isinstance(args, dict)
        or type(args.get("version")) is not int
        or args["version"] != 1
    ):
        raise ProtocolError("unsupported surface protocol version")
    if body["cap"] not in ("surface-hello", "surface-origin"):
        supplied = args.get("session_id")
        if not isinstance(supplied, str) or not _SESSION.fullmatch(supplied):
            raise ProtocolError("invalid surface session")
        if session_id is None or supplied != session_id:
            raise ProtocolError("stale or unexpected surface session")
    elif session_id is not None:
        raise ProtocolError("bootstrap capability on established session")
    return body


def counter(value) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ProtocolError("u64 counters must be decimal strings")
    if len(value) > 20 or (len(value) > 1 and value[0] == "0"):
        raise ProtocolError("noncanonical u64 counter")
    return uint(int(value), 64, "counter")


def surface_to_dict(surface: Surface) -> dict:
    data = asdict(surface)
    data["role"] = surface.role.value
    data["flags"] = int(surface.flags)
    return data


def surface_from_dict(data: dict) -> Surface:
    if not isinstance(data, dict):
        raise ProtocolError("surface must be an object")
    data = data.copy()
    allowed = set(Surface.__dataclass_fields__)
    data = {key: value for key, value in data.items() if key in allowed}
    try:
        for key in ("size", "size_min", "size_max"):
            if key in data:
                data[key] = Size(**data[key])
        if "anchor" in data:
            data["anchor"] = Rect(**data["anchor"])
        if "role" in data:
            data["role"] = Role(data["role"])
        if "flags" in data:
            uint(data["flags"], 4, "flags")
            data["flags"] = Flags(data["flags"])
        return Surface(**data)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"invalid surface: {exc}") from exc
