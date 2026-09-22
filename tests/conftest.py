import asyncio

import pytest
from helpers import Clipboard, Desktop, port, until

from kvm.kvm import KVMApp


@pytest.fixture
async def pair():
    a_port, b_port = port(), port()
    layout = [
        dict(id=0, peer="a", x=0, y=0, width=800, height=600),
        dict(id=0, peer="b", x=800, y=0, width=1024, height=768),
    ]
    a = KVMApp(
        "test-only-secret",
        "a",
        a_port,
        False,
        peers=[("127.0.0.1", b_port)],
        desktop=Desktop(),
        clipboard=Clipboard(),
    )
    b = KVMApp(
        "test-only-secret",
        "b",
        b_port,
        False,
        peers=[("127.0.0.1", a_port)],
        desktop=Desktop(),
        clipboard=Clipboard(),
    )
    for app in (a, b):
        app.set_layout(layout)
        await app.start()
    await until(lambda: a.ready == {"b"} and b.ready == {"a"})
    yield a, b
    await asyncio.gather(a.stop(), b.stop())
