"""Clipboard opt-in, request/data routing and loop suppression on real Channels."""

import asyncio

import pytest
from test_transport import port, until

from surfaces.clipboard import MAX_TEXT_BYTES, ClipboardFlow
from surfaces.session import ClientSession, OriginSession
from surfaces.transport import DirectLink


@pytest.mark.asyncio
async def test_clipboard_offer_request_data_in_both_directions():
    a_port, b_port = port(), port()
    a = DirectLink(
        "spatial-clipboard-test",
        "origin",
        ("127.0.0.1", a_port),
        ("127.0.0.1", b_port),
        channel_base=300,
        expected_peer="client",
    )
    b = DirectLink(
        "spatial-clipboard-test",
        "client",
        ("127.0.0.1", b_port),
        ("127.0.0.1", a_port),
        channel_base=300,
        expected_peer="origin",
    )
    origin_text, client_text = [""], [""]
    origin = OriginSession(
        a,
        "origin",
        "client",
        clipboard=(
            lambda: origin_text[0],
            lambda text: origin_text.__setitem__(0, text),
        ),
    )
    client = ClientSession(
        b,
        "client",
        "origin",
        clipboard=(
            lambda: client_text[0],
            lambda text: client_text.__setitem__(0, text),
        ),
    )
    try:
        await asyncio.gather(a.start(), b.start())
        client.hello()
        await until(lambda: client.session_id is not None)
        assert origin.clipboard.enabled and client.clipboard.enabled
        origin_text[0] = "spatial ✓\n" * 500
        origin.clipboard.poll()
        await until(lambda: client_text[0] == origin_text[0])
        client.clipboard.poll()
        assert client.clipboard.offered_id is None  # no echo offer
        client_text[0] = "back from client"
        client.clipboard.poll()
        await until(lambda: origin_text[0] == "back from client")
        origin.clipboard.poll()
        assert not origin.errors and not client.errors
    finally:
        await asyncio.gather(a.stop(), b.stop())


def test_clipboard_bounds_and_stale_request():
    class Link:
        def on_clipboard(self, callback):
            self.receive = callback

        def send_control(self, data, *, clipboard=False):
            self.sent.append((data, clipboard))

        sent = []

    class Session:
        name = "origin"
        session_id = "a" * 32
        link = Link()
        errors = []

    text = ["x" * (MAX_TEXT_BYTES + 1)]
    flow = ClipboardFlow(
        Session(), "client", lambda: text[0], lambda value: text.__setitem__(0, value)
    )
    flow.enabled = True
    flow.poll()
    assert not flow.offered_id
    text[0] = "safe"
    flow.poll()
    assert flow.offered_id
    with pytest.raises(ValueError):
        flow.handle(
            "clipboard-request",
            {"offer_id": "0" * 32, "mime_type": "text/plain;charset=utf-8"},
        )
