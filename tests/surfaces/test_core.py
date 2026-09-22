import os
import random
from dataclasses import replace

import pytest

from surfaces.atlas import LayoutCache, compose_bgra, pack
from surfaces.media import Fragment, Reassembler, fragment
from surfaces.model import (
    ProtocolError,
    Role,
    Size,
    Surface,
    TreeReplica,
    ViewHint,
    Visibility,
)
from surfaces.policy import History, decide, split_budget
from surfaces.tiles import (
    DamageTracker,
    Texture,
    decode_blocks,
    encode_blocks,
    qoi_decode,
    qoi_encode,
)


def test_tree_gap_is_atomic_and_requires_snapshot():
    tree = TreeReplica()
    first = Surface(1, Size(100, 100))
    assert tree.snapshot(1, [first])
    assert not tree.delta(3, "update", 1, title="gap")
    assert not tree.delta(2, "update", 1, title="late")
    assert tree.surfaces[1].title == ""
    assert tree.snapshot(3, [replace(first, title="recovered")])
    assert tree.delta(4, "update", 1, title="next")
    assert not tree.delta(4, "remove", 1)
    assert tree.surfaces[1].title == "next"


def test_tree_rejects_cycles_orphans_and_reused_ids():
    tree = TreeReplica()
    parent = Surface(1, Size(100, 100))
    dialog = Surface(2, Size(50, 50), role=Role.DIALOG, parent_id=1, modal=True)
    tree.snapshot(0, [parent, dialog])
    assert not tree.accepts_input(1)
    assert tree.accepts_input(2)
    with pytest.raises(ProtocolError):
        tree.delta(1, "remove", 1)
    assert tree.revision == 0
    with pytest.raises(ProtocolError):
        tree.delta(1, "update", 1, role=Role.DIALOG, parent_id=2)
    assert tree.surfaces[1] == parent
    tree.delta(1, "remove", 2)
    with pytest.raises(ProtocolError):
        tree.delta(2, "add", 2, surface=dialog)
    assert tree.accepts_input(1)


@pytest.mark.parametrize("title", ["a" * 257, "😀" * 65])
def test_title_cap_is_utf8(title):
    with pytest.raises(ProtocolError):
        Surface(1, Size(1, 1), title=title)


def test_resize_constraints():
    surface = Surface(1, Size(400, 300), size_min=Size(100, 50), size_max=Size(500, 0))
    assert surface.clamp_size(Size(700, 20)) == Size(500, 50)


def test_policy_hysteresis_freeze_thaw_and_lane_migration():
    surface = Surface(1, Size(1000, 800))
    hint = ViewHint(1, Visibility.IN_VIEW, Size(800, 640), focused=True)
    tile = surface.size
    decision = decide(surface, hint, 0.4, tile, History(), 0)
    assert decision.lane == "cold"
    decision = decide(surface, hint, 0.4, tile, decision.history, 1.01)
    assert decision.lane == "hot" and decision.repack
    history = replace(decision.history, lane="hot")
    decision = decide(surface, hint, 0.01, tile, history, 2)
    assert decision.lane == "hot"
    decision = decide(surface, hint, 0.01, tile, decision.history, 5)
    assert decision.lane == "cold"
    frozen = decide(
        surface, replace(hint, visible=Visibility.OUT_OF_VIEW), 1, tile, history, 6
    )
    assert frozen.frozen and frozen.max_updates_hz == 0
    thaw = decide(surface, hint, 0, tile, frozen.history, 7)
    assert thaw.refresh and not thaw.frozen


def test_policy_resolution_and_budget():
    surface = Surface(1, Size(1000, 800))
    hint = ViewHint(1, Visibility.PERIPHERAL, Size(200, 160))
    first = decide(surface, hint, 0, surface.size, History(), 0)
    assert not first.repack and first.max_updates_hz == 5 and first.qp_delta == 12
    later = decide(surface, hint, 0, surface.size, first.history, 2.01)
    assert later.repack and later.target == Size(250, 200)
    grown = decide(
        surface,
        replace(hint, display_px=surface.size),
        0,
        later.target,
        later.history,
        3,
    )
    assert grown.repack and grown.target == surface.size
    assert split_budget(100, 150) == (100, 0)
    assert split_budget(100, 20) == (20, 80)


def test_atlas_six_windows_alignment_and_resize():
    layout = pack({i: Size(640, 480) for i in range(6)})
    assert len(layout.tiles) == 6
    for tile in layout.tiles:
        assert tile.slot.x % 64 == tile.slot.y % 64 == 0
        assert tile.valid.x - tile.slot.x == 16
    resized = layout.resized(1, Size(650, 490))
    assert resized is not None and resized.epoch == layout.epoch
    assert layout.resized(1, Size(2000, 2000)) is None
    with pytest.raises(ValueError):
        pack({1: Size(4000, 3000)})


def test_atlas_gutters_do_not_sample_neighbor():
    layout = pack({1: Size(2, 2), 2: Size(2, 2)}, slack=0)
    red, blue = b"\0\0\xff\xff", b"\xff\0\0\xff"
    canvas = compose_bgra(layout, {1: red * 4, 2: blue * 4})
    for tile in layout.tiles:
        expected = red if tile.surface_id == 1 else blue
        for y in range(tile.slot.y, tile.slot.y + tile.slot.h):
            for x in range(tile.slot.x, tile.slot.x + tile.slot.w):
                start = (y * layout.canvas.w + x) * 4
                assert canvas[start : start + 4] == expected


def test_layout_cache_holds_last_two_immutable_epochs():
    cache = LayoutCache()
    for epoch in range(3):
        cache.add(pack({1: Size(10, 10)}, epoch=epoch))
    assert cache.get(0, 0) is None
    assert cache.get(0, 1) and cache.get(0, 2)
    with pytest.raises(ProtocolError):
        cache.add(pack({1: Size(20, 20)}, epoch=2))


def test_media_reorder_duplicate_loss_and_completion():
    data = os.urandom(5000)
    pieces = fragment(data, 0, 4, 9, 1)
    assert all(len(piece) + 44 <= 1200 for piece in pieces)
    decoder = Reassembler()
    assert decoder.feed(pieces[3], 0) is None
    assert decoder.feed(pieces[3], 0.01) is None
    assert decoder.missing(0, 4, 9) == (0, 1, 2, 4)
    output = None
    for i in (4, 2, 0, 1):
        output = decoder.feed(pieces[i], 0.02)
    assert output.data == data
    assert decoder.bytes_pending == 0
    assert decoder.feed(pieces[0], 0.03) is None


def test_new_hot_frame_supersedes_old_but_cold_does_not():
    for lane in (0, 1):
        decoder = Reassembler()
        old = fragment(b"a" * 2000, lane, 0, 0)
        new = fragment(b"b", lane, 0, 1)
        decoder.feed(old[0], 0)
        assert decoder.feed(new[0], 0.01).data == b"b"
        finished = decoder.feed(old[1], 0.02)
        assert (finished is not None) == (lane == 1)


def test_media_memory_bounds_and_invalid_fragments():
    decoder = Reassembler(byte_limit=1000)
    with pytest.raises(ProtocolError):
        decoder.feed(fragment(b"a" * 2000, 0, 0, 0)[0], 0)
    assert decoder.bytes_pending == 0
    with pytest.raises(ProtocolError):
        Fragment(0, 0, 0, 1, 1, 0, b"a")
    with pytest.raises(ProtocolError):
        Fragment.decode(b"short")


@pytest.mark.parametrize("size", [Size(1, 1), Size(64, 64), Size(17, 39)])
def test_qoi_roundtrip_noise_and_runs(size):
    for raw in (
        os.urandom(size.area * 4),
        b"\0\0\0\xff" * size.area,
        b"\x12\x34\x56\x78" * size.area,
    ):
        assert qoi_decode(qoi_encode(raw, size), size) == raw


def test_qoi_known_external_format_vector_and_diff_luma():
    header = b"qoif\0\0\0\x03\0\0\0\x01\x04\x00"
    # Initial black; RGB +1,+1,+1; luma +10,+10,+10.
    data = header + bytes([0xC0, 0x7F, 0xAA, 0x88]) + b"\0" * 7 + b"\1"
    assert qoi_decode(data, Size(3, 1)) == bytes(
        [0, 0, 0, 255, 1, 1, 1, 255, 11, 11, 11, 255]
    )
    with pytest.raises(ProtocolError):
        qoi_decode(data[:-1], Size(3, 1))


def test_cold_damage_atomic_decode_revision_per_block_and_resize():
    size = Size(100, 70)
    raw = bytearray(b"\0\0\0\xff" * size.area)
    tracker, texture = DamageTracker(), Texture(1, size)
    first = decode_blocks(encode_blocks(tracker.update(1, size, raw)))
    assert len(first) == 4
    random.Random(2).shuffle(first)
    for block in first:
        assert texture.apply(block)
    assert texture.ready and texture.pixels == raw
    assert tracker.update(1, size, raw) == []
    raw[:4] = b"\xff\xff\xff\xff"
    changed = tracker.update(1, size, raw)
    assert len(changed) == 1
    assert texture.apply(changed[0])
    for block in first:
        assert not texture.apply(block)
    assert texture.pixels == raw
    assert len(tracker.update(1, size, raw, refresh=True)) == 4


def test_malformed_cold_payload_never_changes_texture():
    tracker = DamageTracker()
    block = tracker.update(1, Size(1, 1), b"\0\0\0\xff")[0]
    texture = Texture(1, Size(1, 1))
    with pytest.raises(ProtocolError):
        texture.apply(replace(block, payload=b"bad"))
    assert texture.pixels == bytes(4) and not texture.ready
    with pytest.raises(ProtocolError):
        decode_blocks(encode_blocks([block])[:-1])
