import asyncio
import subprocess
import sys

from helpers import until

from kvm.kvm import valid_key


def test_cli_help_has_no_display_requirement():
    result = subprocess.run(
        [sys.executable, "-m", "kvm", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--psk-file" in result.stdout


def test_key_validation():
    for key in ["char:é", "special:ctrl_l", "special:f12", "vk:65"]:
        assert valid_key(key)
    for key in [None, "garbage", "char:ab", "vk:-1", "vk:65536", "special:__class__"]:
        assert not valid_key(key)


async def test_malformed_messages_dont_change_focus(pair):
    a, b = pair
    for data in (b"", b"null", b"[]", b"{", b"{}", b'"abc"', b"x" * 2000):
        b.network.receive(data, ("127.0.0.1", a.network.band.bind_port), 71)
    for kind, data in [
        ("request", {"token": "a" * 32, "x": "bad", "y": 0}),
        ("clip_begin", {"size": 2**40, "revision": [1, "a"], "hash": "f" * 64}),
        ("input", {"token": "a" * 32, "kind": "key", "key": "char:x"}),
    ]:
        b.receive("a", kind, data)
    assert not b.owner and not b.desktop.events


async def test_simultaneous_focus_does_not_deadlock(pair):
    a, b = pair
    a.local_event("move", {"x": 799, "y": 200})
    b.local_event("move", {"x": 0, "y": 200})
    await asyncio.sleep(0.4)
    assert not (a.target and b.target)
    assert not a.pending_focus and not b.pending_focus
    assert not a.desktop.forwarding and not b.desktop.forwarding


async def test_clipboard_integrity_and_size_limits(pair):
    a, b = pair
    b.receive("a", "clip_begin", {"revision": [1, "a"], "size": 1, "hash": "0" * 64})
    b.receive("a", "clip_chunk", {"offset": 0, "data": "eA=="})
    b.receive("a", "clip_end", {})
    assert b.clipboard.text == ""
    b.receive(
        "a",
        "clip_begin",
        {"revision": [2, "a"], "size": 1024 * 1024 + 1, "hash": "0" * 64},
    )
    assert not b.clip_incoming


async def test_busy_receiver_declines_focus(pair):
    a, b = pair
    b.owner = "someone-else"
    b.token = "e" * 32
    b.last_focus = __import__("time").monotonic()
    a.local_event("move", {"x": 799, "y": 200})
    await asyncio.sleep(0.3)
    assert a.target is None and not a.desktop.forwarding
    assert b.owner == "someone-else"
    b.owner = None


async def test_wrong_layout_declines_control(pair):
    a, b = pair
    b.layout_hash = "different"
    await b.network.send("a", "hello", {"layout": "different"})
    await until(lambda: "b" not in a.ready)
    a.local_event("move", {"x": 799, "y": 200})
    await asyncio.sleep(0.2)
    assert a.target is None
