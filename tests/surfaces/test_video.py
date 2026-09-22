import pytest

from surfaces.hot import HotAtlas, HotCutter
from surfaces.media import Frame
from surfaces.model import Size


def test_six_windows_one_encoder_and_epoch_safe_repack():
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    atlas = HotAtlas(backend="portable", max_canvas=Size(1024, 768))
    cutter = HotCutter()
    frames = {
        i: (Size(128, 96), bytes([i * 35, 20, 200, 255]) * 128 * 96) for i in range(6)
    }
    try:
        layout, changed, encoded, key = atlas.encode(frames)
        assert changed and key and len(layout.tiles) == 6
        cutter.add_layout(layout)
        decoded = cutter.decode(Frame(0, layout.epoch, 0, 1, encoded))
        assert set(decoded) == set(frames)
        for sid, (size, pixels) in decoded.items():
            assert size == frames[sid][0]
            # Lossy video may shift colors a little, but cannot sample its neighbor.
            assert abs(pixels[0] - sid * 35) < 8
        assert atlas.encode(frames) is None
        prior = atlas.encoder
        frames[1] = (Size(128, 96), bytes([5, 100, 150, 255]) * 128 * 96)
        _, changed, encoded, key = atlas.encode(frames)
        assert not changed and atlas.encoder is prior
        # An in-slot resize updates the crop atomically, retaining all allocations
        # and the encoder. Its new epoch cannot reinterpret late old-frame crops.
        frames[1] = (Size(140, 100), bytes([5, 100, 150, 255]) * 140 * 100)
        fitted, changed, encoded, key = atlas.encode(frames)
        assert changed and key and fitted.epoch == 1
        assert atlas.encoder is prior and atlas.repacks == 1
        assert [t.slot for t in fitted.tiles] == [t.slot for t in layout.tiles]
        cutter.add_layout(fitted)
        decoded = cutter.decode(Frame(0, 1, 2, 1, encoded))
        assert decoded[1][0] == Size(140, 100)
        frames[1] = (Size(200, 100), bytes([5, 100, 150, 255]) * 200 * 100)
        new_layout, changed, encoded, key = atlas.encode(frames)
        assert changed and key and new_layout.epoch == 2
        assert atlas.repacks == 2
        cutter.add_layout(new_layout)
        decoded = cutter.decode(Frame(0, 2, 3, 1, encoded))
        assert decoded[1][0] == Size(200, 100)
        assert cutter.decode(Frame(0, 1, 2, 1, b"obsolete")) == {}
    finally:
        atlas.stop()


@pytest.mark.asyncio
async def test_hot_view_hint_reduces_resolution_after_hysteresis_and_restores():
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    from surfaces.model import Surface, ViewHint, Visibility
    from surfaces.session import ClientSession, OriginSession

    class Link:
        channel_base = 100

        def on_control(self, callback):
            self.control = callback

        def on_stream(self, kind, callback):
            if not hasattr(self, "streams"):
                self.streams = {}
            self.streams[kind] = callback

        def send_control(self, data):
            self.peer.control(data)

        def send_latest(self, kind, packets):
            for packet in packets:
                self.peer.streams[kind](packet)

        def send_media(self, kind, packets):
            for packet in packets:
                self.peer.streams[kind](packet)

    a, b = Link(), Link()
    a.peer, b.peer = b, a
    origin = OriginSession(a, "origin", "client", hot_backend="portable")
    client = ClientSession(b, "client", "origin")
    size = Size(480, 240)
    raw = b"\x20\x40\x60\xff" * size.area
    origin.set_surfaces([Surface(1, size)])
    client.hello()
    try:
        client.hints([ViewHint(1, Visibility.IN_VIEW, Size(80, 40))])
        assert await origin.publish_hot({1: (size, raw)}, now=0)
        assert client.textures[1].size == size
        assert not await origin.publish_hot({1: (size, raw)}, now=1)
        assert await origin.publish_hot({1: (size, raw)}, now=2.1)
        assert client.textures[1].size == Size(100, 50)
        assert origin.hot.repacks == 1
        client.hints([ViewHint(1, Visibility.IN_VIEW, size)])
        assert await origin.publish_hot({1: (size, raw)}, now=2.2)
        assert client.textures[1].size == size
        assert origin.hot.repacks == 1
        assert not client.errors and not origin.errors
    finally:
        origin.stop()
