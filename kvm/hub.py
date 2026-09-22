"""WebSocket carrier for the current Telesthetium opaque-frame relay.

The hub doesn't attach source addresses. Authenticated HELLO frames establish
virtual addresses; data frames are attributed by their negotiated session key.
The Band still performs its normal authentication and replay checks afterward.
"""

import asyncio
import json
import logging
from urllib.parse import urlsplit

from telesthete.protocol.framing import unpack_packet

log = logging.getLogger(__name__)


class HubTransport:
    def __init__(self, band, url):
        if urlsplit(url).scheme not in ("ws", "wss"):
            raise ValueError("Hub URL must use ws:// or wss://")
        self.band, self.url = band, url
        self.local_address = ("hub", 0)
        self.handlers = band.transport._handlers
        self.queue = asyncio.Queue(maxsize=1024)
        self.ws = None

    def register_handler(self, kind, handler):
        self.handlers[kind].append(handler)

    def start(self):
        """Band transport hook; the asynchronous connection is opened by run()."""

    def send(self, destination, packet):
        if self.ws is not None:
            try:
                self.queue.put_nowait(packet)
            except asyncio.QueueFull:
                pass  # Application reliability retries control; motion may drop.

    def route(self, raw):
        try:
            packet = unpack_packet(raw)
            if packet.band_id != self.band.band_id:
                return
            aad = bytes(
                [packet.channel_type, packet.channel_id >> 8, packet.channel_id & 255]
            )
            addr = None
            if packet.channel_type == 0:
                try:
                    plain = self.band.base_crypto().decrypt(
                        packet.sequence, packet.ciphertext, aad
                    )
                    msg = json.loads(plain)
                    if msg["type"] in (1, 2):
                        name = msg["payload"]["hostname"]
                        if (
                            not isinstance(name, str)
                            or not name
                            or len(name) > 128
                            or name == self.band.hostname
                        ):
                            return
                        addr = (name, 0)
                except Exception:
                    # Authentication failure is expected for session-key control.
                    addr = None
            if addr is None:
                for candidate in list(self.band.peers):
                    crypto = self.band.recv_crypto(candidate)
                    if crypto:
                        try:
                            crypto.decrypt(packet.sequence, packet.ciphertext, aad)
                            addr = candidate
                            break
                        except Exception:
                            continue
            if addr is not None:
                for handler in self.handlers[packet.channel_type]:
                    handler(addr, raw)
        except (ValueError, TypeError, IndexError):
            log.debug("Invalid hub frame")

    async def run(self):
        from websockets.asyncio.client import connect

        while True:
            try:
                async with connect(
                    self.url, max_size=65535, max_queue=32, open_timeout=10, proxy=None
                ) as ws:
                    self.ws = ws
                    self.band.connect_peer("hub", 0)

                    async def writer():
                        while True:
                            await ws.send(await self.queue.get())

                    task = asyncio.create_task(writer())
                    try:
                        async for raw in ws:
                            if isinstance(raw, bytes):
                                self.route(raw)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Hub connection interrupted: %s; reconnecting", exc)
            finally:
                self.ws = None
                while not self.queue.empty():
                    self.queue.get_nowait()
            await asyncio.sleep(1)

    async def stop(self):
        if self.ws:
            await self.ws.close()
