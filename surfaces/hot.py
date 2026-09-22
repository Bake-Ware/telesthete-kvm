"""One encoder session for all hot surfaces, with epoch-safe client atlas cuts."""

from dataclasses import asdict, replace

from .atlas import Layout, LayoutCache, Tile, compose_bgra, pack
from .model import ProtocolError, Rect, Size
from .video import H264Decoder


class HotAtlas:
    def __init__(self, *, backend="nvenc", max_canvas=Size(3840, 2160)):
        self.backend, self.max_canvas = backend, max_canvas
        self.layout = None
        self.encoder = None
        self.pixels = {}
        self.repacks = 0
        self.force_keyframe = True

    def encode(self, frames: dict[int, tuple[Size, bytes]]):
        sizes = {sid: size for sid, (size, _) in frames.items()}
        old_sizes = (
            {t.surface_id: Size(t.valid.w, t.valid.h) for t in self.layout.tiles}
            if self.layout
            else {}
        )
        changed = sizes != old_sizes
        if changed:
            epoch = self.layout.epoch + 1 if self.layout else 0
            if epoch > 65535:
                raise ProtocolError("atlas epoch exhausted; reconnect required")
            layout = self.layout
            if layout and sizes.keys() == old_sizes.keys():
                for sid, size in sizes.items():
                    layout = layout.resized(sid, size)
                    if layout is None:
                        break
            else:
                layout = None
            repack = layout is None
            layout = (
                pack(sizes, self.max_canvas, epoch=epoch)
                if repack
                else replace(layout, epoch=epoch)
            )
            if self.backend == "nvenc" and layout.canvas.w < 192:
                if self.max_canvas.w < 192:
                    raise ProtocolError("client decode width below NVENC minimum")
                layout = replace(layout, canvas=Size(192, layout.canvas.h))
            if self.layout is None or self.layout.canvas != layout.canvas:
                if self.encoder:
                    self.encoder.stop()
                if self.backend == "portable":
                    from .video import SoftwareH264Encoder

                    self.encoder = SoftwareH264Encoder(layout.canvas)
                else:
                    from .video import GstH264Encoder

                    self.encoder = GstH264Encoder(layout.canvas, backend=self.backend)
            self.layout = layout
            self.repacks += int(repack)
            self.force_keyframe = True
        pixels = {sid: raw for sid, (_, raw) in frames.items()}
        if not changed and pixels == self.pixels and not self.force_keyframe:
            return None
        self.pixels = pixels
        atlas = compose_bgra(self.layout, pixels)
        encoded, keyframe = self.encoder.encode(atlas, keyframe=self.force_keyframe)
        self.force_keyframe = False
        return self.layout, changed, encoded, keyframe

    def stop(self):
        if self.encoder:
            self.encoder.stop()
            self.encoder = None


def layout_to_dict(layout: Layout):
    return {
        "lane_id": layout.lane_id,
        "epoch": layout.epoch,
        "canvas": asdict(layout.canvas),
        "tiles": [
            {
                "surface_id": t.surface_id,
                "tile": asdict(t.slot),
                "valid": asdict(t.valid),
            }
            for t in layout.tiles
        ],
    }


def layout_from_dict(data):
    try:
        return Layout(
            data["lane_id"],
            data["epoch"],
            Size(**data["canvas"]),
            tuple(
                Tile(t["surface_id"], Rect(**t["tile"]), Rect(**t["valid"]))
                for t in data["tiles"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("invalid atlas layout") from exc


class HotCutter:
    def __init__(self):
        self.layouts = LayoutCache()
        self.decoders = {}
        self.active_epoch = -1

    def add_layout(self, layout):
        self.layouts.add(layout)
        for epoch in list(self.decoders):
            if not self.layouts.get(0, epoch):
                del self.decoders[epoch]

    def decode(self, frame):
        layout = self.layouts.get(frame.lane_id, frame.epoch)
        if layout is None:
            raise ProtocolError("unknown atlas epoch")
        if frame.epoch < self.active_epoch:
            return {}
        decoder = self.decoders.get(frame.epoch)
        if decoder is None:
            if not frame.flags & 1:
                raise ProtocolError("new epoch must begin with a keyframe")
            decoder = H264Decoder(layout.canvas)
            self.decoders[frame.epoch] = decoder
        result = {}
        for size, pixels in decoder.decode(frame.data):
            if size != layout.canvas:
                raise ProtocolError("decoded frame does not match layout")
            for tile in layout.tiles:
                r = tile.valid
                raw = b"".join(
                    pixels[
                        ((r.y + row) * size.w + r.x) * 4 : (
                            (r.y + row) * size.w + r.x + r.w
                        )
                        * 4
                    ]
                    for row in range(r.h)
                )
                result[tile.surface_id] = (Size(r.w, r.h), raw)
            self.active_epoch = frame.epoch
        return result
