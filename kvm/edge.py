"""Validated shared desktop geometry, with explicit OS-local coordinates."""

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Monitor:
    id: int
    peer: str
    x: int
    y: int
    width: int
    height: int
    local_x: Optional[int] = None
    local_y: Optional[int] = None

    def contains_point(self, x, y):
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def to_dict(self):
        return asdict(self)


class CoordinateMapper:
    def __init__(self, local_peer):
        self.local_peer = local_peer
        self.monitors = []

    def set_layout(self, monitors):
        if not isinstance(monitors, list) or not 1 <= len(monitors) <= 64:
            raise ValueError("Layout must contain 1–64 monitors")
        parsed = [Monitor(**m) for m in monitors]
        seen = set()
        for m in parsed:
            for name in ("id", "x", "y", "width", "height"):
                if type(getattr(m, name)) is not int:
                    raise ValueError(f"{name} must be an integer")
            if not isinstance(m.peer, str) or not m.peer or len(m.peer) > 128:
                raise ValueError("Invalid peer name")
            if not (0 < m.width <= 32768 and 0 < m.height <= 32768):
                raise ValueError("Invalid monitor size")
            if abs(m.x) > 1000000 or abs(m.y) > 1000000:
                raise ValueError("Monitor coordinates out of range")
            if (m.peer, m.id) in seen:
                raise ValueError("Monitor ids must be unique within each peer")
            seen.add((m.peer, m.id))
            if (m.local_x is None) != (m.local_y is None):
                raise ValueError("Specify both local_x and local_y")
            if m.local_x is not None and (
                type(m.local_x) is not int or type(m.local_y) is not int
            ):
                raise ValueError("Local coordinates must be integers")
        for i, m in enumerate(parsed):
            for n in parsed[i + 1 :]:
                if max(m.x, n.x) < min(m.x + m.width, n.x + n.width) and max(
                    m.y, n.y
                ) < min(m.y + m.height, n.y + n.height):
                    raise ValueError("Monitors must not overlap in the shared layout")
        if not any(m.peer == self.local_peer for m in parsed):
            raise ValueError(f"Layout has no monitor for {self.local_peer}")
        self.monitors = parsed

    def origin(self, m):
        peers = [n for n in self.monitors if n.peer == m.peer]
        return (
            m.local_x if m.local_x is not None else m.x - min(n.x for n in peers),
            m.local_y if m.local_y is not None else m.y - min(n.y for n in peers),
        )

    def local_to_global(self, peer, x, y):
        for m in self.monitors:
            ox, oy = self.origin(m)
            if m.peer == peer and ox <= x < ox + m.width and oy <= y < oy + m.height:
                return m.x + x - ox, m.y + y - oy
        return None

    def global_to_local(self, peer, x, y):
        m = self.get_monitor_at(x, y)
        if m is None or m.peer != peer:
            raise ValueError("Position is not on target peer")
        ox, oy = self.origin(m)
        return ox + x - m.x, oy + y - m.y

    def get_monitor_at(self, x, y):
        return next((m for m in self.monitors if m.contains_point(x, y)), None)

    def get_local_monitors(self):
        return [m for m in self.monitors if m.peer == self.local_peer]

    def get_layout_config(self):
        return [m.to_dict() for m in self.monitors]

    def check_edge_transition(self, x, y):
        m = self.get_monitor_at(x, y)
        if not m:
            return None
        # Only adjacent monitors under the actual crossing point qualify.
        candidates = []
        if x <= m.x + 1:
            candidates.append((m.x - 1, y))
        if x >= m.x + m.width - 2:
            candidates.append((m.x + m.width, y))
        if y <= m.y + 1:
            candidates.append((x, m.y - 1))
        if y >= m.y + m.height - 2:
            candidates.append((x, m.y + m.height))
        for tx, ty in candidates:
            n = self.get_monitor_at(tx, ty)
            if n and n.peer != m.peer:
                # Land inside the monitor, outside the transition strip.
                tx = max(
                    n.x + min(4, n.width // 2),
                    min(tx, n.x + n.width - 1 - min(4, n.width // 2)),
                )
                ty = max(
                    n.y + min(4, n.height // 2),
                    min(ty, n.y + n.height - 1 - min(4, n.height // 2)),
                )
                return n.peer, tx, ty
        return None

    def move(self, peer, x, y, dx, dy):
        m = self.get_monitor_at(x, y)
        if not m or m.peer != peer:
            raise ValueError("Invalid starting position")
        tx, ty = x + dx, y + dy
        # Substeps prevent a large delta from skipping over a gap or another peer.
        steps = max(1, abs(dx), abs(dy))
        for i in range(1, steps + 1):
            nx, ny = round(x + dx * i / steps), round(y + dy * i / steps)
            n = self.get_monitor_at(nx, ny)
            if not n:
                return (
                    peer,
                    max(m.x, min(nx, m.x + m.width - 1)),
                    max(m.y, min(ny, m.y + m.height - 1)),
                )
            if n.peer != peer:
                mx, my = min(4, (n.width - 1) // 2), min(4, (n.height - 1) // 2)
                return (
                    n.peer,
                    max(n.x + mx, min(nx, n.x + n.width - 1 - mx)),
                    max(n.y + my, min(ny, n.y + n.height - 1 - my)),
                )
            m = n
        return peer, tx, ty
