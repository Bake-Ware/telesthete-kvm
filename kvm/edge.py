"""
Edge detection and coordinate mapping

Monitors cursor position and detects when it hits screen edges
to transition focus between machines.
"""

import logging
from typing import Optional, List, Dict, Tuple, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Edge(Enum):
    """Screen edges"""
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass
class Monitor:
    """
    A monitor with its geometry and edge connections
    """
    id: int
    peer: str  # Peer hostname
    x: int
    y: int
    width: int
    height: int

    def contains_point(self, x: int, y: int) -> bool:
        """Check if point is within this monitor"""
        return (self.x <= x < self.x + self.width and
                self.y <= y < self.y + self.height)

    def get_edge(self, x: int, y: int, threshold: int = 5) -> Optional[Edge]:
        """
        Get which edge (if any) the point is near

        Args:
            x, y: Point coordinates
            threshold: Pixels from edge to trigger

        Returns:
            Edge or None
        """
        if not self.contains_point(x, y):
            return None

        # Check edges (within threshold pixels)
        if x - self.x < threshold:
            return Edge.LEFT
        if self.x + self.width - x < threshold:
            return Edge.RIGHT
        if y - self.y < threshold:
            return Edge.TOP
        if self.y + self.height - y < threshold:
            return Edge.BOTTOM

        return None

    def to_dict(self) -> dict:
        """Serialize to dict"""
        return {
            "id": self.id,
            "peer": self.peer,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height
        }

    @staticmethod
    def from_dict(data: dict) -> 'Monitor':
        """Deserialize from dict"""
        return Monitor(
            id=data["id"],
            peer=data["peer"],
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"]
        )


class CoordinateMapper:
    """
    Maps coordinates across multiple monitors and peers
    """

    def __init__(self, local_peer: str):
        """
        Initialize coordinate mapper

        Args:
            local_peer: This machine's hostname
        """
        self.local_peer = local_peer
        self.monitors: List[Monitor] = []

        # Edge connections: (monitor_id, edge) -> (target_peer, target_monitor_id, target_edge)
        self.edge_map: Dict[Tuple[int, Edge], Tuple[str, int, Edge]] = {}

    def set_layout(self, monitors: List[Dict]):
        """
        Set monitor layout from configuration

        Args:
            monitors: List of monitor dicts
        """
        self.monitors = [Monitor.from_dict(m) for m in monitors]
        logger.info(f"Loaded {len(self.monitors)} monitors")

        # Auto-build edge map based on spatial adjacency
        self._build_edge_map()

    def _build_edge_map(self):
        """
        Automatically build edge connections based on monitor positions
        """
        self.edge_map.clear()

        for mon in self.monitors:
            # Check each edge
            self._find_adjacent(mon, Edge.LEFT)
            self._find_adjacent(mon, Edge.RIGHT)
            self._find_adjacent(mon, Edge.TOP)
            self._find_adjacent(mon, Edge.BOTTOM)

    def _find_adjacent(self, monitor: Monitor, edge: Edge):
        """
        Find adjacent monitor at given edge

        Args:
            monitor: Source monitor
            edge: Edge to check
        """
        # Get the line segment for this edge
        if edge == Edge.LEFT:
            # Left edge: x, y to y+height
            check_x = monitor.x - 1
            check_y_range = (monitor.y, monitor.y + monitor.height)
        elif edge == Edge.RIGHT:
            # Right edge
            check_x = monitor.x + monitor.width
            check_y_range = (monitor.y, monitor.y + monitor.height)
        elif edge == Edge.TOP:
            # Top edge
            check_y = monitor.y - 1
            check_x_range = (monitor.x, monitor.x + monitor.width)
        elif edge == Edge.BOTTOM:
            # Bottom edge
            check_y = monitor.y + monitor.height
            check_x_range = (monitor.x, monitor.x + monitor.width)

        # Find monitor at that position
        for other in self.monitors:
            if other.id == monitor.id:
                continue

            if edge in (Edge.LEFT, Edge.RIGHT):
                # Check if other monitor's range overlaps with our edge
                if other.x <= check_x < other.x + other.width:
                    # Check Y overlap
                    if self._ranges_overlap(check_y_range, (other.y, other.y + other.height)):
                        # Found adjacent monitor
                        target_edge = Edge.RIGHT if edge == Edge.LEFT else Edge.LEFT
                        self.edge_map[(monitor.id, edge)] = (other.peer, other.id, target_edge)
                        logger.debug(f"Monitor {monitor.id} {edge.value} -> Monitor {other.id} on {other.peer}")
                        break

            else:  # TOP or BOTTOM
                # Check if other monitor's range overlaps with our edge
                if other.y <= check_y < other.y + other.height:
                    # Check X overlap
                    if self._ranges_overlap(check_x_range, (other.x, other.x + other.width)):
                        # Found adjacent monitor
                        target_edge = Edge.BOTTOM if edge == Edge.TOP else Edge.TOP
                        self.edge_map[(monitor.id, edge)] = (other.peer, other.id, target_edge)
                        logger.debug(f"Monitor {monitor.id} {edge.value} -> Monitor {other.id} on {other.peer}")
                        break

    def _ranges_overlap(self, range1: Tuple[int, int], range2: Tuple[int, int]) -> bool:
        """Check if two ranges overlap"""
        return range1[0] < range2[1] and range2[0] < range1[1]

    def get_monitor_at(self, x: int, y: int) -> Optional[Monitor]:
        """Get monitor containing the given point"""
        for mon in self.monitors:
            if mon.contains_point(x, y):
                return mon
        return None

    def check_edge_transition(self, x: int, y: int) -> Optional[Tuple[str, int, int]]:
        """
        Check if cursor position should trigger edge transition

        Args:
            x, y: Cursor position

        Returns:
            (target_peer, target_x, target_y) if transition should occur, else None
        """
        # Find which monitor we're on
        monitor = self.get_monitor_at(x, y)
        if not monitor:
            return None

        # Check if we're at an edge
        edge = monitor.get_edge(x, y)
        if not edge:
            return None

        # Check if this edge has a connection
        key = (monitor.id, edge)
        if key not in self.edge_map:
            return None

        target_peer, target_monitor_id, target_edge = self.edge_map[key]

        # Find target monitor
        target_monitor = None
        for mon in self.monitors:
            if mon.id == target_monitor_id and mon.peer == target_peer:
                target_monitor = mon
                break

        if not target_monitor:
            return None

        # Calculate target coordinates
        # Map position along edge to target edge
        if edge in (Edge.LEFT, Edge.RIGHT):
            # Vertical edge - map Y coordinate
            ratio = (y - monitor.y) / monitor.height
            target_y = int(target_monitor.y + ratio * target_monitor.height)

            if target_edge == Edge.LEFT:
                target_x = target_monitor.x
            else:  # RIGHT
                target_x = target_monitor.x + target_monitor.width - 1

        else:  # TOP or BOTTOM
            # Horizontal edge - map X coordinate
            ratio = (x - monitor.x) / monitor.width
            target_x = int(target_monitor.x + ratio * target_monitor.width)

            if target_edge == Edge.TOP:
                target_y = target_monitor.y
            else:  # BOTTOM
                target_y = target_monitor.y + target_monitor.height - 1

        return (target_peer, target_x, target_y)

    def get_local_monitors(self) -> List[Monitor]:
        """Get monitors belonging to this peer"""
        return [m for m in self.monitors if m.peer == self.local_peer]

    def get_layout_config(self) -> List[Dict]:
        """Get layout configuration (for sending to peers)"""
        return [m.to_dict() for m in self.monitors]


class EdgeDetector:
    """
    Monitors cursor position and triggers transitions on edge hits
    """

    def __init__(
        self,
        mapper: CoordinateMapper,
        on_transition: Callable[[str, int, int], None]
    ):
        """
        Initialize edge detector

        Args:
            mapper: Coordinate mapper with monitor layout
            on_transition: Callback(target_peer, x, y) when edge hit
        """
        self.mapper = mapper
        self.on_transition = on_transition

        # Debounce state to prevent rapid transitions
        self.last_check_coords = (0, 0)
        self.edge_hit_count = 0
        self.edge_threshold = 3  # Must hit edge N times to trigger

    def check_position(self, x: int, y: int):
        """
        Check if current position should trigger transition

        Args:
            x, y: Current cursor position
        """
        # Debounce - only check if position changed significantly
        if abs(x - self.last_check_coords[0]) < 2 and abs(y - self.last_check_coords[1]) < 2:
            return

        self.last_check_coords = (x, y)

        # Check for edge transition
        result = self.mapper.check_edge_transition(x, y)

        if result:
            # At an edge
            self.edge_hit_count += 1

            if self.edge_hit_count >= self.edge_threshold:
                # Trigger transition
                target_peer, target_x, target_y = result
                logger.info(f"Edge transition: {target_peer} at ({target_x}, {target_y})")
                self.on_transition(target_peer, target_x, target_y)
                self.edge_hit_count = 0
        else:
            # Not at edge
            self.edge_hit_count = 0


def test_edge_detection():
    """Test edge detection and coordinate mapping"""
    print("Testing Edge Detection")
    print("=" * 60)

    # Create a simple two-monitor layout
    # Monitor 0: 0,0 1920x1080 on "desktop"
    # Monitor 1: 1920,0 1920x1080 on "laptop"
    layout = [
        {"id": 0, "peer": "desktop", "x": 0, "y": 0, "width": 1920, "height": 1080},
        {"id": 1, "peer": "laptop", "x": 1920, "y": 0, "width": 1920, "height": 1080},
    ]

    mapper = CoordinateMapper("desktop")
    mapper.set_layout(layout)

    print(f"\nLoaded {len(mapper.monitors)} monitors")
    print(f"Edge map: {len(mapper.edge_map)} connections")

    # Test edge transitions
    test_cases = [
        (1919, 500, True, "laptop"),    # Right edge of monitor 0 -> laptop
        (1000, 500, False, None),       # Middle of monitor 0 -> no transition
        (1920, 500, True, "desktop"),   # Left edge of monitor 1 -> desktop (wraps around)
        (2500, 500, False, None),       # Middle of monitor 1 -> no transition
    ]

    for x, y, should_transition, expected_peer in test_cases:
        result = mapper.check_edge_transition(x, y)

        if should_transition:
            if result:
                target_peer, target_x, target_y = result
                print(f"[PASS] ({x}, {y}) -> {target_peer} at ({target_x}, {target_y})")
                assert target_peer == expected_peer
            else:
                print(f"[FAIL] ({x}, {y}) should transition but didn't")
        else:
            if result:
                print(f"[FAIL] ({x}, {y}) shouldn't transition but did: {result}")
            else:
                print(f"[PASS] ({x}, {y}) -> no transition")

    print("\nEdge detection test complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_edge_detection()
