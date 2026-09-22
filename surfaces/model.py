"""Validated, immutable surface records; no desktop or transport dependencies."""

from dataclasses import dataclass, replace
from enum import Enum, IntFlag


class ProtocolError(ValueError):
    pass


def uint(value: int, bits: int, name: str) -> int:
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ProtocolError(f"{name} must be a u{bits}")
    return value


def sint(value: int, name: str) -> int:
    if type(value) is not int or not -(1 << 31) <= value < 1 << 31:
        raise ProtocolError(f"{name} must be an i32")
    return value


@dataclass(frozen=True)
class Size:
    w: int
    h: int

    def __post_init__(self):
        uint(self.w, 32, "width")
        uint(self.h, 32, "height")

    @property
    def area(self):
        return self.w * self.h


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def __post_init__(self):
        sint(self.x, "x")
        sint(self.y, "y")
        Size(self.w, self.h)

    def contains(self, other: "Rect") -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.x + self.w >= other.x + other.w
            and self.y + self.h >= other.y + other.h
        )

    def overlaps(self, other: "Rect") -> bool:
        return (
            self.x < other.x + other.w
            and other.x < self.x + self.w
            and self.y < other.y + other.h
            and other.y < self.y + self.h
        )


class Role(str, Enum):
    TOPLEVEL = "toplevel"
    DIALOG = "dialog"
    POPUP = "popup"
    TOOLTIP = "tooltip"


class Visibility(str, Enum):
    IN_VIEW = "in_view"
    PERIPHERAL = "peripheral"
    OUT_OF_VIEW = "out_of_view"
    HIDDEN = "hidden"


class Flags(IntFlag):
    FOCUSED = 1
    URGENT = 2
    FULLSCREEN_REQ = 4
    DECORATED = 8


@dataclass(frozen=True)
class Surface:
    surface_id: int
    size: Size
    app_id: str = ""
    title: str = ""
    role: Role = Role.TOPLEVEL
    parent_id: int | None = None
    anchor: Rect = Rect(0, 0, 0, 0)
    size_min: Size = Size(0, 0)
    size_max: Size = Size(0, 0)
    modal: bool = False
    z_hint: int = 0
    flags: Flags = Flags(0)

    def __post_init__(self):
        uint(self.surface_id, 32, "surface_id")
        if not isinstance(self.role, Role):
            raise ProtocolError("invalid role")
        if (self.role == Role.TOPLEVEL) != (self.parent_id is None):
            raise ProtocolError("only toplevel surfaces have no parent")
        if self.parent_id is not None:
            uint(self.parent_id, 32, "parent_id")
        if self.parent_id == self.surface_id:
            raise ProtocolError("surface cannot parent itself")
        if type(self.modal) is not bool:
            raise ProtocolError("modal must be boolean")
        if not isinstance(self.title, str) or len(self.title.encode("utf-8")) > 256:
            raise ProtocolError("title exceeds 256 UTF-8 bytes")
        if not isinstance(self.app_id, str) or len(self.app_id.encode("utf-8")) > 1024:
            raise ProtocolError("invalid app_id")
        sint(self.z_hint, "z_hint")
        if type(self.flags) not in (int, Flags) or not 0 <= self.flags <= 15:
            raise ProtocolError("invalid surface flags")
        if not self.size.area:
            raise ProtocolError("surface dimensions must be positive")
        for lo, hi in (
            (self.size_min.w, self.size_max.w),
            (self.size_min.h, self.size_max.h),
        ):
            if hi and lo > hi:
                raise ProtocolError("minimum size exceeds maximum")

    def clamp_size(self, requested: Size) -> Size:
        def clamp(value, lo, hi):
            return min(max(1, value, lo), hi or (1 << 32) - 1)

        return Size(
            clamp(requested.w, self.size_min.w, self.size_max.w),
            clamp(requested.h, self.size_min.h, self.size_max.h),
        )


@dataclass(frozen=True)
class ViewHint:
    surface_id: int
    visible: Visibility
    display_px: Size
    fovea: int = 255
    focused: bool = False
    priority: int = 128

    def __post_init__(self):
        uint(self.surface_id, 32, "surface_id")
        uint(self.fovea, 8, "fovea")
        uint(self.priority, 8, "priority")
        if not isinstance(self.visible, Visibility) or type(self.focused) is not bool:
            raise ProtocolError("invalid view hint")


class TreeReplica:
    """Atomic snapshots/deltas. A revision gap latches until a new snapshot."""

    def __init__(self):
        self.revision = 0
        self.surfaces: dict[int, Surface] = {}
        self.needs_snapshot = True
        self._seen: set[int] = set()

    @staticmethod
    def _validate(surfaces):
        if len(surfaces) > 256:
            raise ProtocolError("surface limit exceeded")
        for surface in surfaces.values():
            visited = {surface.surface_id}
            parent = surface.parent_id
            while parent is not None:
                if parent not in surfaces or parent in visited:
                    raise ProtocolError("missing parent or cyclic tree")
                visited.add(parent)
                parent = surfaces[parent].parent_id

    def snapshot(self, revision: int, surfaces: list[Surface]) -> bool:
        uint(revision, 64, "tree_rev")
        if revision < self.revision:
            return False
        proposed = {s.surface_id: s for s in surfaces}
        if len(proposed) != len(surfaces):
            raise ProtocolError("duplicate surface IDs")
        self._validate(proposed)
        if any(sid in self._seen and sid not in self.surfaces for sid in proposed):
            raise ProtocolError("surface ID reused")
        self.surfaces = proposed
        self._seen.update(proposed)
        self.revision = revision
        self.needs_snapshot = False
        return True

    def delta(
        self,
        revision: int,
        operation: str,
        surface_id: int,
        surface: Surface | None = None,
        **changes,
    ) -> bool:
        uint(revision, 64, "tree_rev")
        uint(surface_id, 32, "surface_id")
        if revision <= self.revision:
            return False
        if self.needs_snapshot or revision != self.revision + 1:
            self.needs_snapshot = True
            return False
        proposed = self.surfaces.copy()
        if operation == "add":
            if (
                surface_id in self._seen
                or surface is None
                or surface.surface_id != surface_id
            ):
                raise ProtocolError("invalid surface add")
            proposed[surface_id] = surface
        elif operation == "remove":
            if surface_id not in proposed:
                raise ProtocolError("removing unknown surface")
            del proposed[surface_id]
        elif operation == "update":
            if surface_id not in proposed or "surface_id" in changes:
                raise ProtocolError("invalid surface update")
            try:
                proposed[surface_id] = replace(proposed[surface_id], **changes)
            except TypeError as exc:
                raise ProtocolError("unknown surface field") from exc
        else:
            raise ProtocolError("unknown tree operation")
        self._validate(proposed)
        self.surfaces = proposed
        self._seen.add(surface_id)
        self.revision = revision
        return True

    def accepts_input(self, surface_id: int) -> bool:
        if self.needs_snapshot or surface_id not in self.surfaces:
            return False
        for surface in self.surfaces.values():
            if not surface.modal:
                continue
            parent = surface.parent_id
            while parent is not None:
                if parent == surface_id:
                    return False
                parent = self.surfaces[parent].parent_id
        return True
