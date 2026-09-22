import asyncio

from helpers import Clipboard, Desktop, until

from kvm.kvm import KVMApp


async def focus(a, b):
    a.local_event("move", {"x": 799, "y": 200})
    await until(lambda: a.target == "b" and b.owner == "a")


async def test_handoff_input_return(pair):
    a, b = pair
    await focus(a, b)
    assert a.desktop.forwarding
    a.local_event("key", {"key": "special:shift", "pressed": True})
    a.local_event("key", {"key": "char:A", "pressed": True})
    a.local_event("button", {"button": "left", "pressed": True})
    await until(
        lambda: (
            b.desktop.keys == {"special:shift", "char:A"}
            and b.desktop.buttons == {"left"}
        )
    )
    assert not a.desktop.events
    a.local_event("delta", {"dx": 100, "dy": 20})
    await until(lambda: b.desktop.position == (104, 220))
    a.local_event("delta", {"dx": -110, "dy": 0})
    await until(lambda: a.target is None and b.owner is None)
    assert not a.desktop.forwarding
    assert not b.desktop.keys and not b.desktop.buttons


async def test_clipboard_large_unicode_clear_and_update(pair):
    a, b = pair
    a.clipboard.text = "héllo 🌍\n" * 12000
    await until(lambda: b.clipboard.text == a.clipboard.text, timeout=20)
    a.clipboard.text = "second change"
    await until(lambda: b.clipboard.text == "second change")
    b.clipboard.text = ""
    await until(lambda: a.clipboard.text == "")
    assert a.clip_revision == b.clip_revision


async def test_emergency_hotkey(pair):
    a, b = pair
    await focus(a, b)
    for key in ("special:ctrl_l", "special:alt_l", "special:esc"):
        a.local_event("key", {"key": key, "pressed": True})
    await until(lambda: not a.target and not b.owner)
    assert not a.desktop.forwarding


async def test_focus_timeout_releases_stuck_keys(pair):
    a, b = pair
    await focus(a, b)
    a.local_event("key", {"key": "char:x", "pressed": True})
    await until(lambda: "char:x" in b.desktop.keys)
    a.network.band.transport.send = lambda *args: None
    b.network.band.transport.send = lambda *args: None
    await until(lambda: a.target is None and b.owner is None)
    assert not b.desktop.keys and not a.desktop.forwarding


async def test_reliable_input_survives_packet_loss(pair):
    a, b = pair
    await focus(a, b)
    transport = a.network.band.transport
    original = transport.send
    counter = 0

    def lossy(addr, packet):
        nonlocal counter
        counter += 1
        if counter % 3:
            original(addr, packet)

    transport.send = lossy
    for char in "abcdef":
        a.local_event("key", {"key": "char:" + char, "pressed": True})
        a.local_event("key", {"key": "char:" + char, "pressed": False})
    await until(lambda: len(b.desktop.events) == 12)
    assert [e[1]["key"] for e in b.desktop.events] == [
        f"char:{c}" for c in "abcdef" for _ in (0, 1)
    ]
    assert not b.desktop.keys


async def test_unsolicited_input_and_wrong_token_ignored(pair):
    a, b = pair
    a.network.motion("b", "pointer", {"token": "f" * 32, "x": 900, "y": 100})
    await asyncio.sleep(0.1)
    assert b.desktop.position == (100, 100)
    await focus(a, b)
    position = b.desktop.position
    a.network.motion("b", "pointer", {"token": "f" * 32, "x": 1000, "y": 100})
    await asyncio.sleep(0.1)
    assert b.desktop.position == position


async def test_duplicate_edge_events_single_handoff(pair):
    a, b = pair
    for _ in range(10):
        a.local_event("move", {"x": 799, "y": 200})
    await until(lambda: a.target == "b")
    assert b.owner == "a"


async def test_reconnect_after_transport_restart(pair):
    a, b = pair
    await focus(a, b)
    await b.stop()
    await until(lambda: a.target is None)
    replacement = KVMApp(
        "test-only-secret",
        "b",
        b.network.band.bind_port,
        False,
        peers=[("127.0.0.1", a.network.band.bind_port)],
        desktop=Desktop(),
        clipboard=Clipboard(),
    )
    replacement.set_layout(a.mapper.get_layout_config())
    try:
        await replacement.start()
        await until(lambda: replacement.ready == {"a"} and a.ready == {"b"})
        await asyncio.sleep(0.6)
        await focus(a, replacement)
        a.local_event("key", {"key": "char:z", "pressed": True})
        await until(lambda: "char:z" in replacement.desktop.keys)
    finally:
        await replacement.stop()


async def test_temporary_blackhole_then_reconnect(pair):
    a, b = pair
    await focus(a, b)
    original_a, original_b = (
        a.network.band.transport.send,
        b.network.band.transport.send,
    )
    a.network.band.transport.send = b.network.band.transport.send = lambda *args: None
    a.local_event("key", {"key": "char:z", "pressed": True})
    await asyncio.sleep(4.5)
    assert not a.target and not b.owner
    a.network.band.transport.send, b.network.band.transport.send = (
        original_a,
        original_b,
    )
    await until(lambda: a.ready == {"b"} and b.ready == {"a"}, timeout=8)
    await focus(a, b)
    a.local_event("key", {"key": "char:y", "pressed": True})
    await until(lambda: "char:y" in b.desktop.keys)
