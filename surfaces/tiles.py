"""Lossless 64x64 damage blocks and revision-safe client textures.

QOI wire format: https://qoiformat.org/qoi-specification.pdf
The dependency-free codec is a reference path, not a high-throughput encoder.
"""

import struct
from dataclasses import dataclass

from .model import ProtocolError, Size, uint

_QOI_HEADER = struct.Struct("!4sIIBB")
_END = b"\0" * 7 + b"\1"
_BLOCK = struct.Struct("!IIIQHHBI")


def _hash(pixel):
    return (pixel[0] * 3 + pixel[1] * 5 + pixel[2] * 7 + pixel[3] * 11) % 64


def qoi_encode(bgra: bytes, size: Size) -> bytes:
    if not 0 < size.w <= 64 or not 0 < size.h <= 64 or len(bgra) != size.area * 4:
        raise ProtocolError("invalid raw tile")
    output = bytearray(_QOI_HEADER.pack(b"qoif", size.w, size.h, 4, 0))
    previous = (0, 0, 0, 255)
    index = [(0, 0, 0, 0)] * 64
    run = 0
    for offset in range(0, len(bgra), 4):
        b, g, r, a = bgra[offset : offset + 4]
        pixel = (r, g, b, a)
        if pixel == previous:
            run += 1
            if run == 62 or offset == len(bgra) - 4:
                output.append(0xC0 | (run - 1))
                run = 0
            continue
        if run:
            output.append(0xC0 | (run - 1))
            run = 0
        slot = _hash(pixel)
        if index[slot] == pixel:
            output.append(slot)
        elif a == previous[3]:
            dr, dg, db = (pixel[i] - previous[i] for i in range(3))
            if all(-2 <= delta <= 1 for delta in (dr, dg, db)):
                output.append(0x40 | (dr + 2) << 4 | (dg + 2) << 2 | (db + 2))
            elif -32 <= dg <= 31 and -8 <= dr - dg <= 7 and -8 <= db - dg <= 7:
                output.extend((0x80 | (dg + 32), (dr - dg + 8) << 4 | (db - dg + 8)))
            else:
                output.extend((0xFE, r, g, b))
        else:
            output.extend((0xFF, r, g, b, a))
        index[slot] = pixel
        previous = pixel
    return bytes(output) + _END


def qoi_decode(data: bytes, expected: Size) -> bytes:
    if len(data) < 23 or len(data) > expected.area * 5 + 22:
        raise ProtocolError("invalid QOI length")
    magic, w, h, channels, color = _QOI_HEADER.unpack_from(data)
    if (
        magic != b"qoif"
        or (w, h) != (expected.w, expected.h)
        or not 0 < w <= 64
        or not 0 < h <= 64
        or channels not in (3, 4)
        or color != 0
        or data[-8:] != _END
    ):
        raise ProtocolError("invalid QOI header or trailer")
    index = [(0, 0, 0, 0)] * 64
    pixel = (0, 0, 0, 255)
    output = bytearray()
    pos, end = 14, len(data) - 8
    while len(output) < expected.area * 4:
        if pos >= end:
            raise ProtocolError("truncated QOI pixels")
        op = data[pos]
        pos += 1
        run = 1
        r, g, b, a = pixel
        if op in (0xFE, 0xFF):
            count = 3 if op == 0xFE else 4
            if pos + count > end:
                raise ProtocolError("truncated QOI literal")
            r, g, b = data[pos : pos + 3]
            if count == 4:
                a = data[pos + 3]
            pos += count
            pixel = (r, g, b, a)
        elif op >> 6 == 0:
            pixel = index[op]
        elif op >> 6 == 1:
            pixel = (
                (r + ((op >> 4) & 3) - 2) % 256,
                (g + ((op >> 2) & 3) - 2) % 256,
                (b + (op & 3) - 2) % 256,
                a,
            )
        elif op >> 6 == 2:
            if pos >= end:
                raise ProtocolError("truncated QOI luma")
            extra = data[pos]
            pos += 1
            dg = (op & 63) - 32
            pixel = (
                (r + dg + (extra >> 4) - 8) % 256,
                (g + dg) % 256,
                (b + dg + (extra & 15) - 8) % 256,
                a,
            )
        else:
            run = (op & 63) + 1
        if len(output) + run * 4 > expected.area * 4:
            raise ProtocolError("QOI run exceeds tile")
        index[_hash(pixel)] = pixel
        output.extend(bytes((pixel[2], pixel[1], pixel[0], pixel[3])) * run)
    if pos != end:
        raise ProtocolError("trailing QOI pixel data")
    return bytes(output)


@dataclass(frozen=True)
class Block:
    surface_id: int
    x: int
    y: int
    revision: int
    size: Size
    codec: int
    payload: bytes

    def __post_init__(self):
        for value, bits, name in (
            (self.surface_id, 32, "surface_id"),
            (self.x, 32, "block_x"),
            (self.y, 32, "block_y"),
            (self.revision, 64, "surface_rev"),
        ):
            uint(value, bits, name)
        if not 0 < self.size.w <= 64 or not 0 < self.size.h <= 64:
            raise ProtocolError("invalid damage block dimensions")
        if self.codec not in (0, 1) or not 0 < len(self.payload) <= 64 * 64 * 5 + 22:
            raise ProtocolError("invalid damage block payload")

    def decode(self) -> bytes:
        if self.codec == 0:
            return qoi_decode(self.payload, self.size)
        try:
            import zstandard
        except ImportError as exc:
            raise ProtocolError("zstd-bgra was not installed/negotiated") from exc
        try:
            declared = zstandard.frame_content_size(self.payload)
            if declared not in (zstandard.CONTENTSIZE_UNKNOWN, self.size.area * 4):
                raise ProtocolError("zstd declared size mismatch")
            result = zstandard.ZstdDecompressor().decompress(
                self.payload, max_output_size=self.size.area * 4
            )
        except zstandard.ZstdError as exc:
            raise ProtocolError("invalid zstd block") from exc
        if len(result) != self.size.area * 4:
            raise ProtocolError("zstd size mismatch")
        return result


def encode_blocks(blocks: list[Block]) -> bytes:
    if len(blocks) > 65535:
        raise ProtocolError("too many blocks")
    parts = [struct.pack("!H", len(blocks))]
    for block in blocks:
        parts.extend(
            (
                _BLOCK.pack(
                    block.surface_id,
                    block.x,
                    block.y,
                    block.revision,
                    block.size.w,
                    block.size.h,
                    block.codec,
                    len(block.payload),
                ),
                block.payload,
            )
        )
    return b"".join(parts)


def decode_blocks(data: bytes) -> list[Block]:
    if len(data) < 2:
        raise ProtocolError("truncated cold batch")
    (count,) = struct.unpack_from("!H", data)
    pos, blocks = 2, []
    for _ in range(count):
        if pos + _BLOCK.size > len(data):
            raise ProtocolError("truncated block header")
        sid, x, y, rev, w, h, codec, length = _BLOCK.unpack_from(data, pos)
        pos += _BLOCK.size
        if pos + length > len(data):
            raise ProtocolError("truncated block payload")
        blocks.append(
            Block(sid, x, y, rev, Size(w, h), codec, data[pos : pos + length])
        )
        pos += length
    if pos != len(data):
        raise ProtocolError("trailing cold batch data")
    return blocks


class DamageTracker:
    def __init__(self):
        self.previous: dict[tuple[int, int], bytes] = {}
        self.size = Size(0, 0)
        self.revision = 0

    def update(
        self, surface_id: int, size: Size, bgra: bytes, *, refresh=False, zstd=False
    ) -> list[Block]:
        if not size.area or len(bgra) != size.area * 4:
            raise ProtocolError("invalid capture buffer")
        uint(self.revision + 1, 64, "surface_rev")
        previous = self.previous if size == self.size and not refresh else {}
        current, blocks = {}, []
        compressor = None
        if zstd:
            import zstandard

            compressor = zstandard.ZstdCompressor(level=1)
        for y in range(0, size.h, 64):
            for x in range(0, size.w, 64):
                tile = Size(min(64, size.w - x), min(64, size.h - y))
                raw = b"".join(
                    bgra[
                        ((y + row) * size.w + x) * 4 : ((y + row) * size.w + x + tile.w)
                        * 4
                    ]
                    for row in range(tile.h)
                )
                key = (x // 64, y // 64)
                current[key] = raw
                if previous.get(key) == raw:
                    continue
                payload, codec = qoi_encode(raw, tile), 0
                if compressor:
                    compressed = compressor.compress(raw)
                    if len(compressed) < len(payload):
                        payload, codec = compressed, 1
                blocks.append(
                    Block(surface_id, *key, self.revision + 1, tile, codec, payload)
                )
        self.previous, self.size = current, size
        self.revision += 1
        return blocks


class Texture:
    def __init__(self, surface_id: int, size: Size):
        uint(surface_id, 32, "surface_id")
        if not size.area or size.area > 3840 * 2160:
            raise ProtocolError("texture exceeds prototype limit")
        self.surface_id, self.size = surface_id, size
        self.pixels = bytearray(size.area * 4)
        self.revisions: dict[tuple[int, int], int] = {}

    @property
    def ready(self):
        return len(self.revisions) == ((self.size.w + 63) // 64) * (
            (self.size.h + 63) // 64
        )

    def apply(self, block: Block) -> bool:
        if block.surface_id != self.surface_id:
            raise ProtocolError("block belongs to another surface")
        x, y = block.x * 64, block.y * 64
        if x >= self.size.w or y >= self.size.h:
            raise ProtocolError("block outside texture")
        expected = Size(min(64, self.size.w - x), min(64, self.size.h - y))
        if expected != block.size:
            raise ProtocolError("block dimensions disagree with texture")
        key = (block.x, block.y)
        if block.revision <= self.revisions.get(key, -1):
            return False
        raw = block.decode()
        for row in range(block.size.h):
            start = ((y + row) * self.size.w + x) * 4
            self.pixels[start : start + block.size.w * 4] = raw[
                row * block.size.w * 4 : (row + 1) * block.size.w * 4
            ]
        self.revisions[key] = block.revision
        return True
