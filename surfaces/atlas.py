"""Deterministic shelf packing, aligned allocations and atomic layout epochs."""

from dataclasses import dataclass, replace

from .model import ProtocolError, Rect, Size, uint

ALIGN = 64
GUTTER = 16


def align(value: int) -> int:
    return (value + ALIGN - 1) // ALIGN * ALIGN


@dataclass(frozen=True)
class Tile:
    surface_id: int
    slot: Rect
    valid: Rect

    def __post_init__(self):
        uint(self.surface_id, 32, "surface_id")
        if any(v % ALIGN for v in (self.slot.x, self.slot.y, self.slot.w, self.slot.h)):
            raise ProtocolError("unaligned slot")
        inner = Rect(
            self.slot.x + GUTTER,
            self.slot.y + GUTTER,
            max(0, self.slot.w - 2 * GUTTER),
            max(0, self.slot.h - 2 * GUTTER),
        )
        if not self.valid.w or not self.valid.h or not inner.contains(self.valid):
            raise ProtocolError("valid crop violates gutter")


@dataclass(frozen=True)
class Layout:
    lane_id: int
    epoch: int
    canvas: Size
    tiles: tuple[Tile, ...]

    def __post_init__(self):
        uint(self.lane_id, 8, "lane_id")
        uint(self.epoch, 16, "epoch")
        if len(self.tiles) > 256:
            raise ProtocolError("too many atlas tiles")
        bounds = Rect(0, 0, self.canvas.w, self.canvas.h)
        ids = set()
        for i, tile in enumerate(self.tiles):
            if tile.surface_id in ids or not bounds.contains(tile.slot):
                raise ProtocolError("duplicate or out-of-canvas tile")
            if any(tile.slot.overlaps(t.slot) for t in self.tiles[:i]):
                raise ProtocolError("overlapping tiles")
            ids.add(tile.surface_id)

    def resized(self, surface_id: int, size: Size) -> "Layout | None":
        """Keep allocation/epoch when a new crop fits; otherwise request repack."""
        tiles = []
        for tile in self.tiles:
            if tile.surface_id == surface_id:
                if (
                    size.w + 2 * GUTTER > tile.slot.w
                    or size.h + 2 * GUTTER > tile.slot.h
                ):
                    return None
                tile = replace(
                    tile,
                    valid=Rect(
                        tile.slot.x + GUTTER, tile.slot.y + GUTTER, size.w, size.h
                    ),
                )
            tiles.append(tile)
        if not any(t.surface_id == surface_id for t in tiles):
            raise KeyError(surface_id)
        return replace(self, tiles=tuple(tiles))


def pack(
    sizes: dict[int, Size],
    max_canvas: Size = Size(3840, 2160),
    *,
    lane_id: int = 0,
    epoch: int = 0,
    slack: float = 0.20,
) -> Layout:
    """Allocate ~20% extra tile area, then shelf-pack tallest first.

    Shrink slack if it prevents fitting. Never exceed advertised decode bounds;
    oversized inputs fail rather than silently changing surface resolution.
    """
    if not 0 <= slack <= 0.25 or len(sizes) > 256:
        raise ValueError("invalid atlas limits")
    width, height = max_canvas.w // ALIGN * ALIGN, max_canvas.h // ALIGN * ALIGN
    for reserve in (slack, 0):
        growth = (1 + reserve) ** 0.5
        slots = [
            (
                sid,
                size,
                align(int(size.w * growth + 0.999) + 2 * GUTTER),
                align(int(size.h * growth + 0.999) + 2 * GUTTER),
            )
            for sid, size in sizes.items()
        ]
        slots.sort(key=lambda t: (-t[3], -t[2], t[0]))
        x = y = shelf = used_w = 0
        tiles = []
        for sid, size, w, h in slots:
            if x + w > width:
                y += shelf
                x = shelf = 0
            if w > width or y + h > height:
                break
            tiles.append(
                Tile(
                    sid, Rect(x, y, w, h), Rect(x + GUTTER, y + GUTTER, size.w, size.h)
                )
            )
            x += w
            shelf = max(shelf, h)
            used_w = max(used_w, x)
        else:
            return Layout(
                lane_id,
                epoch,
                Size(max(ALIGN, used_w), max(ALIGN, y + shelf)),
                tuple(tiles),
            )
    raise ValueError("surfaces do not fit advertised decode size")


class LayoutCache:
    def __init__(self):
        self._layouts: dict[tuple[int, int], Layout] = {}

    def add(self, layout: Layout):
        key = (layout.lane_id, layout.epoch)
        old = self._layouts.get(key)
        if old is not None:
            if old != layout:
                raise ProtocolError("epoch layout changed")
            return
        epochs = sorted(e for lane, e in self._layouts if lane == layout.lane_id)
        if epochs and layout.epoch < epochs[-1]:
            return
        self._layouts[key] = layout
        for epoch in epochs[:-1]:
            del self._layouts[(layout.lane_id, epoch)]

    def get(self, lane_id: int, epoch: int) -> Layout | None:
        return self._layouts.get((lane_id, epoch))


def compose_bgra(layout: Layout, pixels: dict[int, bytes]) -> bytes:
    """CPU reference compositor with edge-replicated gutters and alignment padding."""
    if layout.canvas.area > 3840 * 2160 * 4:
        raise ValueError("reference compositor canvas too large")
    output = bytearray(layout.canvas.area * 4)
    for tile in layout.tiles:
        source = pixels[tile.surface_id]
        w, h = tile.valid.w, tile.valid.h
        if len(source) != w * h * 4:
            raise ValueError("source dimensions disagree with valid crop")
        left = tile.valid.x - tile.slot.x
        right = tile.slot.w - w - left
        for y in range(tile.slot.h):
            sy = min(h - 1, max(0, tile.slot.y + y - tile.valid.y))
            row = source[sy * w * 4 : (sy + 1) * w * 4]
            row = row[:4] * left + row + row[-4:] * right
            start = ((tile.slot.y + y) * layout.canvas.w + tile.slot.x) * 4
            output[start : start + len(row)] = row
    return bytes(output)
