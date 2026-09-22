import asyncio
import json
import socket

import pytest

from surfaces.model import ProtocolError, Size, Surface
from surfaces.protocol import (
    counter,
    decode,
    encode,
    surface_from_dict,
    surface_to_dict,
)
from surfaces.transport import DirectLink


def port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def until(predicate, timeout=5):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)


def test_envelopes_reject_duplicates_nonfinite_wrong_session_and_target():
    args = {"tree_rev": "1", "surfaces": [surface_to_dict(Surface(1, Size(100, 100)))]}
    wire = encode(
        "surface-snapshot", args, target="c", message_id="m", session_id="a" * 32
    )
    assert surface_from_dict(
        decode(wire, target="c", session_id="a" * 32)["args"]["surfaces"][0]
    ).size == Size(100, 100)
    for target, session in (("wrong", "a" * 32), ("c", "b" * 32)):
        with pytest.raises(ProtocolError):
            decode(wire, target=target, session_id=session)
    with pytest.raises(ProtocolError):
        decode(b'{"id":"x","id":"y"}', target="c", session_id="a" * 32)
    with pytest.raises(ProtocolError):
        decode(b'{"value":NaN}', target="c", session_id="a" * 32)
    assert counter("18446744073709551615") == 2**64 - 1
    for bad in (True, 1, "01", "18446744073709551616", "-1"):
        with pytest.raises(ProtocolError):
            counter(bad)


@pytest.mark.asyncio
async def test_real_encrypted_channels_snapshot_media_and_loss():
    a_port, b_port = port(), port()
    a = DirectLink(
        "test-spatial-only",
        "origin",
        ("127.0.0.1", a_port),
        ("127.0.0.1", b_port),
        channel_base=100,
        expected_peer="client",
    )
    b = DirectLink(
        "test-spatial-only",
        "client",
        ("127.0.0.1", b_port),
        ("127.0.0.1", a_port),
        channel_base=100,
        expected_peer="origin",
    )
    received, frames, clipboard = [], [], []
    b.on_control(received.append)
    b.on_clipboard(clipboard.append)
    b.on_stream("hot", frames.append)
    try:
        await asyncio.gather(a.start(), b.start())
        payload = json.dumps(
            {
                "surfaces": [
                    surface_to_dict(Surface(i, Size(640, 480), title=f"Window {i}"))
                    for i in range(24)
                ]
            }
        ).encode()
        original = a.band.transport.send
        dropped = False

        def lose_one(destination, packet):
            nonlocal dropped
            if packet[16] == 2 and not dropped:
                dropped = True
                return
            original(destination, packet)

        a.band.transport.send = lose_one
        a.send_control(payload)
        a.send_control(b"ordered-input")
        a.send_control(b"clipboard" * 500, clipboard=True)
        a.send_media("hot", [b"media"] * 200)
        await until(lambda: len(received) == 2 and clipboard and frames)
        assert received == [payload, b"ordered-input"]
        assert clipboard == [b"clipboard" * 500]
        assert dropped
        assert frames[0] == b"media"
        assert not b.control._recv_queue and not b.control._msg_queue
        assert not b.clipboard._recv_queue and not b.clipboard._msg_queue
    finally:
        await asyncio.gather(a.stop(), b.stop())
