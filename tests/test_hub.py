import asyncio

from helpers import Clipboard, Desktop, until
from websockets.asyncio.server import serve

from kvm.kvm import KVMApp


async def test_hub_multiple_peers_targeting():
    clients = set()

    async def relay(ws):
        clients.add(ws)
        try:
            async for frame in ws:
                await asyncio.gather(
                    *(other.send(frame) for other in clients - {ws}),
                    return_exceptions=True,
                )
        finally:
            clients.discard(ws)

    async with serve(relay, "127.0.0.1", 0) as server:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/band"
        layout = [
            dict(id=0, peer=name, x=i * 800, y=0, width=800, height=600)
            for i, name in enumerate("abc")
        ]
        apps = [
            KVMApp(
                "hub-test-secret",
                name,
                hub_url=url,
                desktop=Desktop(),
                clipboard=Clipboard(),
            )
            for name in "abc"
        ]
        try:
            for app in apps:
                app.set_layout(layout)
                await app.start()
            a, b, c = apps
            await until(lambda: all(len(app.ready) == 2 for app in apps), timeout=10)
            a.local_event("move", {"x": 799, "y": 100})
            await until(lambda: a.target == "b")
            a.local_event("key", {"key": "char:q", "pressed": True})
            await until(lambda: b.desktop.keys == {"char:q"})
            assert not c.desktop.keys
            a.local_event("delta", {"dx": 900, "dy": 0})
            await until(lambda: a.target == "c")
            assert b.owner is None and not b.desktop.keys
            a.local_event("key", {"key": "char:w", "pressed": True})
            await until(lambda: "char:w" in c.desktop.keys)
        finally:
            await asyncio.gather(*(app.stop() for app in apps))


async def test_hub_connection_drop_restores_and_reconnects():
    clients = set()

    async def relay(ws):
        clients.add(ws)
        try:
            async for frame in ws:
                await asyncio.gather(
                    *(other.send(frame) for other in clients - {ws}),
                    return_exceptions=True,
                )
        finally:
            clients.discard(ws)

    async with serve(relay, "127.0.0.1", 0) as server:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/band"
        layout = [
            dict(id=0, peer=n, x=i * 800, y=0, width=800, height=600)
            for i, n in enumerate("ab")
        ]
        apps = [
            KVMApp(
                "hub-drop-test",
                n,
                hub_url=url,
                desktop=Desktop(),
                clipboard=Clipboard(),
            )
            for n in "ab"
        ]
        try:
            for app in apps:
                app.set_layout(layout)
                await app.start()
            a, b = apps
            await until(lambda: a.ready == {"b"} and b.ready == {"a"})
            a.local_event("move", {"x": 799, "y": 100})
            await until(lambda: a.target == "b")
            for ws in list(clients):
                await ws.close()
            # A brief reconnect may happen before the focus lease expires;
            # either continuity or safe release is acceptable. Input must work.
            await until(lambda: len(clients) == 2)
            await asyncio.sleep(2)
            if not a.target:
                a.local_event("move", {"x": 799, "y": 100})
                await until(lambda: a.target == "b")
            a.local_event("key", {"key": "char:r", "pressed": True})
            await until(lambda: b.desktop.keys == {"char:r"})
        finally:
            await asyncio.gather(*(app.stop() for app in apps))
