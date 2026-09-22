"""Bounded surface media fragmentation/reassembly above Telesthete Streams."""

import struct
from dataclasses import dataclass, field

from .model import ProtocolError, uint

HEADER = struct.Struct("!BHIHHB")
MAX_FRAME_BYTES = 16 * 1024 * 1024
FRAGMENT_BYTES = 1144
KEYFRAME = 1
INTRA_REFRESH_COMPLETE = 2


@dataclass(frozen=True)
class Fragment:
    lane_id: int
    epoch: int
    frame_seq: int
    index: int
    count: int
    flags: int
    data: bytes

    def __post_init__(self):
        for value, bits, name in (
            (self.lane_id, 8, "lane_id"),
            (self.epoch, 16, "epoch"),
            (self.frame_seq, 32, "frame_seq"),
            (self.index, 16, "frag_idx"),
            (self.count, 16, "frag_count"),
        ):
            uint(value, bits, name)
        if (
            not 0
            < self.count
            <= (MAX_FRAME_BYTES + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
        ):
            raise ProtocolError("invalid fragment count")
        if self.index >= self.count or type(self.flags) is not int or self.flags & ~3:
            raise ProtocolError("invalid fragment header")
        if not isinstance(self.data, bytes) or not 0 < len(self.data) <= FRAGMENT_BYTES:
            raise ProtocolError("invalid fragment size")

    def encode(self) -> bytes:
        return (
            HEADER.pack(
                self.lane_id,
                self.epoch,
                self.frame_seq,
                self.index,
                self.count,
                self.flags,
            )
            + self.data
        )

    @classmethod
    def decode(cls, packet: bytes) -> "Fragment":
        if len(packet) <= HEADER.size:
            raise ProtocolError("truncated media fragment")
        return cls(*HEADER.unpack_from(packet), packet[HEADER.size :])


def fragment(
    data: bytes, lane_id: int, epoch: int, frame_seq: int, flags: int = 0
) -> tuple[bytes, ...]:
    if not 0 < len(data) <= MAX_FRAME_BYTES:
        raise ProtocolError("invalid frame length")
    count = (len(data) + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
    return tuple(
        Fragment(
            lane_id,
            epoch,
            frame_seq,
            i,
            count,
            flags,
            data[i * FRAGMENT_BYTES : (i + 1) * FRAGMENT_BYTES],
        ).encode()
        for i in range(count)
    )


@dataclass
class Partial:
    count: int
    flags: int
    created: float
    pieces: dict[int, bytes] = field(default_factory=dict)
    size: int = 0


@dataclass(frozen=True)
class Frame:
    lane_id: int
    epoch: int
    frame_seq: int
    flags: int
    data: bytes


class Reassembler:
    """One instance per subscription; hot/cold completion semantics differ."""

    def __init__(
        self, cold_lanes=frozenset({1}), *, byte_limit=32 * 1024 * 1024, frame_limit=64
    ):
        self.cold_lanes = frozenset(cold_lanes)
        self.byte_limit = byte_limit
        self.frame_limit = frame_limit
        self.pending: dict[tuple[int, int, int], Partial] = {}
        self.completed: dict[tuple[int, int], int] = {}
        self._cold_seen: dict[tuple[int, int, int], float] = {}
        self.bytes_pending = 0

    def _drop(self, key):
        self.bytes_pending -= self.pending.pop(key).size

    def expire(self, now: float) -> list[tuple[int, int, int]]:
        expired = []
        for key, partial in list(self.pending.items()):
            ttl = 2 if key[0] in self.cold_lanes else 0.25
            if now - partial.created > ttl:
                expired.append(key)
                self._drop(key)
        self._cold_seen = {k: t for k, t in self._cold_seen.items() if now - t <= 2}
        return expired

    def missing(self, lane_id: int, epoch: int, frame_seq: int) -> tuple[int, ...]:
        partial = self.pending.get((lane_id, epoch, frame_seq))
        return (
            tuple(i for i in range(partial.count) if i not in partial.pieces)
            if partial
            else ()
        )

    def feed(self, packet: bytes, now: float) -> Frame | None:
        piece = Fragment.decode(packet)
        self.expire(now)
        key = (piece.lane_id, piece.epoch, piece.frame_seq)
        lane = key[:2]
        cold = piece.lane_id in self.cold_lanes
        if (cold and key in self._cold_seen) or (
            not cold and piece.frame_seq <= self.completed.get(lane, -1)
        ):
            return None
        partial = self.pending.get(key)
        if partial is None:
            if len(self.pending) >= self.frame_limit:
                raise ProtocolError("too many incomplete frames")
            partial = Partial(piece.count, piece.flags, now)
            self.pending[key] = partial
        if partial.count != piece.count or partial.flags != piece.flags:
            self._drop(key)
            raise ProtocolError("inconsistent media fragments")
        if piece.index in partial.pieces:
            if partial.pieces[piece.index] != piece.data:
                self._drop(key)
                raise ProtocolError("conflicting duplicate fragment")
            return None
        if (
            self.bytes_pending + len(piece.data) > self.byte_limit
            or partial.size + len(piece.data) > MAX_FRAME_BYTES
        ):
            self._drop(key)
            raise ProtocolError("media reassembly budget exceeded")
        partial.pieces[piece.index] = piece.data
        partial.size += len(piece.data)
        self.bytes_pending += len(piece.data)
        if len(partial.pieces) != partial.count:
            return None
        data = b"".join(partial.pieces[i] for i in range(partial.count))
        self._drop(key)
        if cold:
            self._cold_seen[key] = now
            if len(self._cold_seen) > 4096:
                del self._cold_seen[next(iter(self._cold_seen))]
        else:
            self.completed[lane] = piece.frame_seq
            for older in list(self.pending):
                if older[:2] == lane and older[2] < piece.frame_seq:
                    self._drop(older)
        return Frame(*key, piece.flags, data)

    def retain_epochs(self, lane_id: int, epochs: set[int]):
        for key in list(self.pending):
            if key[0] == lane_id and key[1] not in epochs:
                self._drop(key)
        self.completed = {
            k: v for k, v in self.completed.items() if k[0] != lane_id or k[1] in epochs
        }
        self._cold_seen = {
            k: v
            for k, v in self._cold_seen.items()
            if k[0] != lane_id or k[1] in epochs
        }
