import asyncio

import pytest
from test_transport import port, until

from surfaces.model import Size, Surface, ViewHint, Visibility
from surfaces.session import ClientSession, OriginSession
from surfaces.transport import DirectLink


def test_live_catalog_and_authenticated_window_selection():
    from surfaces.model import Rect, Role
    from surfaces.protocol import encode

    class Link:
        channel_base = 100

        def on_control(self, callback):
            self.control = callback

        def on_stream(self, _kind, _callback):
            pass

        def send_control(self, data):
            self.peer.control(data)

    a, b = Link(), Link()
    a.peer, b.peer = b, a
    selections = []
    origin = OriginSession(a, "origin", "client", on_select=selections.append)
    client = ClientSession(b, "client", "origin")
    root = Surface(1, Size(100, 80), title="Editor")
    child = Surface(
        2,
        Size(40, 30),
        title="Dialog",
        role=Role.DIALOG,
        parent_id=1,
        anchor=Rect(0, 0, 40, 30),
    )
    origin.set_catalog([root, child])
    client.hello()
    assert client.catalog == {1: root, 2: child}
    assert not client.tree.surfaces
    client.select_windows([1])
    assert selections == [{1}]
    # Children are listed but only their owning top-level can be selected.
    from surfaces.model import ProtocolError

    with pytest.raises(ProtocolError):
        client.select_windows([2])
    origin._control(
        encode(
            "window-select",
            {"roots": [2]},
            target="origin",
            message_id="invalid-selection",
            session_id=client.session_id,
        )
    )
    assert selections == [{1}]
    origin.set_catalog([root])
    assert client.catalog == {1: root}
    client.select_windows([])
    assert selections[-1] == set()


def test_catalog_rejects_missing_or_cyclic_parents():
    from surfaces.model import ProtocolError, Role
    from surfaces.session import catalog_map

    with pytest.raises(ProtocolError):
        catalog_map([Surface(1, Size(10, 10), role=Role.DIALOG, parent_id=2)])
    with pytest.raises(ProtocolError):
        catalog_map(
            [
                Surface(1, Size(10, 10), role=Role.DIALOG, parent_id=2),
                Surface(2, Size(10, 10), role=Role.DIALOG, parent_id=1),
            ]
        )


@pytest.mark.asyncio
async def test_end_to_end_tiles_freeze_thaw_and_lost_whole_batch():
    a_port, b_port = port(), port()
    a = DirectLink(
        "spatial-session-test",
        "origin",
        ("127.0.0.1", a_port),
        ("127.0.0.1", b_port),
        channel_base=200,
        expected_peer="client",
    )
    b = DirectLink(
        "spatial-session-test",
        "client",
        ("127.0.0.1", b_port),
        ("127.0.0.1", a_port),
        channel_base=200,
        expected_peer="origin",
    )
    requests = []
    origin, client = (
        OriginSession(
            a,
            "origin",
            "client",
            on_request=lambda cap, args: requests.append((cap, args)),
        ),
        ClientSession(b, "client", "origin"),
    )
    size = Size(100, 70)
    origin.set_surfaces([Surface(1, size, title="Test")])
    visible = ViewHint(1, Visibility.IN_VIEW, size)
    raw = b"\x12\x34\x56\xff" * size.area
    try:
        await asyncio.gather(a.start(), b.start())
        client.hello()
        await until(lambda: 1 in client.textures)
        client.release_input()
        await until(lambda: requests == [("surface-release", {})])
        client.hints([visible])
        await until(lambda: 1 in origin.hints)
        assert await origin.publish(1, raw)
        await until(lambda: client.textures[1].ready)
        assert client.textures[1].pixels == raw
        assert not await origin.publish(1, raw)
        client.hints([ViewHint(1, Visibility.HIDDEN, size)])
        await until(lambda: origin.hints[1].visible == Visibility.HIDDEN)
        assert not await origin.publish(1, raw)
        client.hints([visible])
        await until(lambda: origin.hints[1].visible == Visibility.IN_VIEW)
        assert await origin.publish(1, raw)
        await until(lambda: origin.frame_seq - 1 == next(reversed(client.completed))[2])
        # Lose an ENTIRE batch; the reliable status still reveals the loss.
        send = a.send_media
        a.send_media = lambda *args: None
        changed = b"\xff\xff\xff\xff" * size.area
        assert await origin.publish(1, changed)
        a.send_media = send
        await until(lambda: bool(client.pending))
        await asyncio.sleep(0.16)
        client.repair()
        await until(lambda: 1 in origin.refresh)
        assert await origin.publish(1, changed)
        await until(lambda: client.textures[1].pixels == changed)
        assert not origin.errors and not client.errors
    finally:
        await asyncio.gather(a.stop(), b.stop())


def test_full_hint_sets_fragment_and_never_apply_partial_or_stale_state():
    class Link:
        channel_base = 100

        def __init__(self):
            self.streams = {}
            self.latest = {}

        def on_control(self, callback):
            self.control = callback

        def on_stream(self, kind, callback):
            self.streams[kind] = callback

        def send_control(self, data):
            self.peer.control(data)

        def send_latest(self, kind, packets):
            self.latest[kind] = packets

    a, b = Link(), Link()
    a.peer, b.peer = b, a
    origin = OriginSession(a, "origin", "client")
    client = ClientSession(b, "client", "origin")
    size = Size(640, 480)
    origin.set_surfaces([Surface(i, size) for i in range(24)])
    client.hello()
    hints = [ViewHint(i, Visibility.IN_VIEW, size) for i in range(24)]
    client.hints(hints)
    older = b.latest["hints"]
    assert len(older) > 1
    for packet in older[:-1]:
        a.streams["hints"](packet)
    assert origin.hints == {}
    hidden = [ViewHint(i, Visibility.HIDDEN, size) for i in range(24)]
    client.hints(hidden)
    for packet in b.latest["hints"]:
        a.streams["hints"](packet)
    assert len(origin.hints) == 24
    a.streams["hints"](older[-1])
    assert all(h.visible == Visibility.HIDDEN for h in origin.hints.values())
    assert not origin.errors


def test_negotiated_decoder_bounds_and_unsupported_formats():
    from surfaces.model import ProtocolError
    from surfaces.session import negotiated_canvas

    decoder = dict(
        codec="h264", max_w=1280, max_h=720, max_fps=60, max_instances=1, chroma="420-8"
    )
    assert negotiated_canvas([decoder]) == Size(1280, 720)
    assert negotiated_canvas([dict(decoder, max_w=8192, max_h=8192)]) == Size(
        3840, 2160
    )
    for changes in (
        dict(max_w=True),
        dict(max_h=0),
        dict(max_fps=30),
        dict(max_instances=0),
        dict(chroma="444-10"),
    ):
        with pytest.raises(ProtocolError):
            negotiated_canvas([dict(decoder, **changes)])


@pytest.mark.asyncio
async def test_adaptive_migration_keeps_old_texture_until_ready_and_rejects_late_hot():
    pytest.importorskip("av")
    pytest.importorskip("numpy")

    class Link:
        channel_base = 100

        def __init__(self):
            self.streams, self.packets = {}, []

        def on_control(self, callback):
            self.control = callback

        def on_stream(self, kind, callback):
            self.streams[kind] = callback

        def send_control(self, data):
            self.peer.control(data)

        def send_latest(self, kind, packets):
            for packet in packets:
                self.peer.streams[kind](packet)

        def send_media(self, kind, packets):
            self.packets.extend((kind, packet) for packet in packets)

        def deliver(self):
            pending, self.packets = self.packets, []
            for kind, packet in pending:
                self.peer.streams[kind](packet)

    a, b = Link(), Link()
    a.peer, b.peer = b, a
    origin = OriginSession(a, "origin", "client", hot_backend="portable", adaptive=True)
    client = ClientSession(b, "client", "origin")
    size = Size(128, 96)
    raw = b"\x10\x20\x30\xff" * size.area
    moving = b"\xa0\xb0\xc0\xff" * size.area
    final = b"\x60\x70\x80\xff" * size.area
    origin.set_surfaces([Surface(1, size)])
    client.hello()
    client.hints([ViewHint(1, Visibility.IN_VIEW, size)])
    try:
        await origin.publish_adaptive({1: (size, raw)}, now=0)
        a.deliver()
        assert client.textures[1].pixels == raw
        for now, pixels in (
            (0.1, raw),
            (0.3, raw),
            (0.4, moving),
            (0.5, moving),
            (0.8, raw),
            (0.9, raw),
        ):
            await origin.publish_adaptive({1: (size, pixels)}, now=now)
            a.deliver()
        await origin.publish_adaptive({1: (size, moving)}, now=1.2)
        assert origin.assignments[1][0] == 0
        assert client.textures[1].pixels == raw  # destination not delivered yet
        a.deliver()
        hot_texture = client.textures[1]
        assert hot_texture.pixels != raw
        await origin.publish_adaptive({1: (size, final)}, now=1.4)
        delayed, a.packets = a.packets, []
        await origin.publish_adaptive({1: (size, final)}, now=2.0)
        await origin.publish_adaptive({1: (size, final)}, now=5.1)
        assert origin.assignments[1][0] == 1
        assert client.textures[1] is hot_texture
        a.deliver()
        assert client.textures[1].ready and client.textures[1].pixels == final
        for kind, packet in delayed:
            client.link.streams[kind](packet)
        assert client.textures[1].pixels == final
        assert not origin.errors and not client.errors
    finally:
        origin.stop()


@pytest.mark.asyncio
async def test_tree_deltas_dialog_lifecycle_resize_and_gap_recovery():
    from dataclasses import replace

    from surfaces.model import Rect, Role
    from surfaces.protocol import encode

    a_port, b_port = port(), port()
    a = DirectLink(
        "spatial-tree-test",
        "origin",
        ("127.0.0.1", a_port),
        ("127.0.0.1", b_port),
        channel_base=310,
        expected_peer="client",
    )
    b = DirectLink(
        "spatial-tree-test",
        "client",
        ("127.0.0.1", b_port),
        ("127.0.0.1", a_port),
        channel_base=310,
        expected_peer="origin",
    )
    origin = OriginSession(a, "origin", "client")
    client = ClientSession(b, "client", "origin")
    root = Surface(1, Size(100, 70), title="editor")
    child = Surface(
        2,
        Size(40, 30),
        title="dialog",
        role=Role.DIALOG,
        parent_id=1,
        anchor=Rect(10, 10, 40, 30),
        modal=True,
    )
    origin.set_surfaces([root])
    try:
        await asyncio.gather(a.start(), b.start())
        client.hello()
        await until(lambda: set(client.tree.surfaces) == {1})
        client.hints([ViewHint(1, Visibility.IN_VIEW, root.size)])
        await until(lambda: 1 in origin.hints)
        raw = b"\x20\x30\x40\xff" * root.size.area
        assert await origin.publish(1, raw)
        await until(lambda: client.textures[1].ready)
        old_texture = client.textures[1]
        origin.set_surfaces([root, child])
        await until(lambda: set(client.tree.surfaces) == {1, 2})
        assert client.tree.surfaces[2].parent_id == 1
        origin.set_surfaces([replace(root, size=Size(120, 80)), child])
        await until(lambda: client.tree.surfaces[1].size == Size(120, 80))
        assert client.textures[1] is old_texture
        origin.set_surfaces([replace(root, size=Size(120, 80))])
        await until(lambda: set(client.tree.surfaces) == {1})
        assert client.tree.revision == origin.tree.revision
        bad = encode(
            "surface-update",
            {
                "tree_rev": str(client.tree.revision + 2),
                "surface_id": 1,
                "changes": {"title": "skipped"},
            },
            target="client",
            message_id="gap",
            session_id=client.session_id,
        )
        client._control(bad)
        assert client.tree.needs_snapshot
        await until(lambda: not client.tree.needs_snapshot)
        assert client.tree.revision == origin.tree.revision
        assert not origin.errors and not client.errors
    finally:
        await asyncio.gather(a.stop(), b.stop())
