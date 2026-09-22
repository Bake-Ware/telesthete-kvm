"""Direct configured-peer transport using Telesthete's real Channels and Streams.

Each subscription owns a Band/socket, so channel IDs are scoped to that endpoint.
Discovery/relay are deliberately not silently substituted for direct delivery.
"""

import asyncio
import itertools
import sys
import time
from collections import deque

from telesthete import Band
from telesthete.protocol.framing import ChannelType, unpack_packet
from telesthete.transport.udp import UDPTransport

from .model import ProtocolError


class _PeerTransport(UDPTransport):
    """Configured-peer filter plus priority at the actual socket send queue."""

    def __init__(self, bind, peer, base):
        super().__init__(*bind)
        self.peer = peer
        self.base = base
        self._send_queue = asyncio.PriorityQueue(maxsize=8192)
        self._order = itertools.count()

    def start(self):
        super().start()
        if sys.platform == "win32":
            # Python's socket.ioctl does not expose this Winsock operation.
            # Disable ICMP PORT_UNREACHABLE reports on our unconnected UDP socket.
            import ctypes as c
            from ctypes import wintypes as w

            winsock = c.WinDLL("ws2_32")
            ioctl = winsock.WSAIoctl
            ioctl.argtypes = [
                c.c_size_t,
                w.DWORD,
                c.c_void_p,
                w.DWORD,
                c.c_void_p,
                w.DWORD,
                c.POINTER(w.DWORD),
                c.c_void_p,
                c.c_void_p,
            ]
            ioctl.restype = c.c_int
            disabled, returned = w.BOOL(False), w.DWORD()
            if ioctl(
                self.socket.fileno(),
                0x9800000C,
                c.byref(disabled),
                c.sizeof(disabled),
                None,
                0,
                c.byref(returned),
                None,
                None,
            ):
                raise OSError(winsock.WSAGetLastError(), "SIO_UDP_CONNRESET failed")

    def register_handler(self, channel_type, handler):
        def bound(address, packet):
            if address == self.peer:
                handler(address, packet)

        super().register_handler(channel_type, bound)

    def send(self, destination, packet_bytes):
        if destination != self.peer:
            raise ProtocolError("attempted send to an unconfigured peer")
        packet = unpack_packet(packet_bytes)
        priority = 0
        if packet.channel_type == ChannelType.STREAM:
            priority = {
                self.base + 2: 0,
                self.base + 3: 1,
                self.base + 4: 2,
                self.base + 5: 3,
            }.get(packet.channel_id, 3)
        elif (
            packet.channel_type == ChannelType.CHANNEL
            and packet.channel_id == self.base + 1
        ):
            priority = 1
        self._send_queue.put_nowait(
            (priority, next(self._order), destination, packet_bytes)
        )

    async def _send_loop(self):
        loop = asyncio.get_running_loop()
        while self._running:
            _, _, destination, data = await self._send_queue.get()
            await loop.sock_sendto(self.socket, data, destination)


class DirectLink:
    """Explicit matching ID base is the bootstrap contract for configured peers.

    Separate queues keep media loss from delaying reliable control. This adapter
    does not claim to implement hub discovery or arbitrary band authorization.
    """

    def __init__(
        self,
        psk: str,
        name: str,
        bind: tuple[str, int],
        peer: tuple[str, int],
        *,
        channel_base: int,
        expected_peer: str,
        max_queue_bytes=32 * 1024 * 1024,
    ):
        if (
            not psk
            or not 1 <= channel_base <= 65529
            or channel_base <= 73
            and channel_base + 5 >= 71
        ):
            raise ValueError("invalid secret or channel allocation")
        self.band = Band(
            psk,
            hostname=name,
            bind_address=bind[0],
            bind_port=bind[1],
            capabilities=["spatial-surfaces-v1"],
        )
        transport = _PeerTransport(bind, peer, channel_base)
        for kind, handlers in self.band.transport._handlers.items():
            for handler in handlers:
                transport.register_handler(kind, handler)
        self.band.transport = transport
        self.band.control.transport = transport
        self.peer = peer
        self.channel_base = channel_base
        self.expected_peer = expected_peer
        self.control = self.band.channel(channel_base, peer)
        self.clipboard = self.band.channel(channel_base + 1, peer)
        self.streams = {}
        for offset, priority, kind in (
            (2, 0, "motion"),
            (3, 1, "hints"),
            (4, 64, "cold"),
            (5, 128, "hot"),
        ):
            stream = self.band.stream(channel_base + offset, priority=priority)
            stream.add_destination(peer)
            self.streams[kind] = stream
        self.latest: dict[str, deque] = {}
        self.media = {"cold": deque(), "hot": deque()}
        self.queued_bytes = 0
        self.max_queue_bytes = max_queue_bytes
        self.sent_bytes = {kind: 0 for kind in self.streams}
        self.started_at = 0.0
        self._pump_task = None
        self._wake = asyncio.Event()
        self._closed = False
        self.on_control(lambda data: None)
        self.on_clipboard(lambda data: None)

    async def start(self, timeout=5):
        # LAN discovery is not started by Band.start(); only the configured peer
        # receives our handshake. Reject unexpected addresses before Band dispatch.
        await self.band.start()
        try:
            deadline = time.monotonic() + timeout
            while self.peer not in self.band.peers:
                self.band.connect_peer(*self.peer)
                if time.monotonic() >= deadline:
                    raise TimeoutError("configured surface peer did not handshake")
                await asyncio.sleep(0.05)
            peer = self.band.peers[self.peer]
            if peer.hostname != self.expected_peer:
                raise ProtocolError("configured peer name mismatch")
            for channel in (self.control, self.clipboard):
                if channel._state == "CLOSED":
                    await channel.open()
            while any(
                c._state != "ESTABLISHED" for c in (self.control, self.clipboard)
            ):
                if time.monotonic() >= deadline:
                    raise TimeoutError("surface channels did not open")
                await asyncio.sleep(0.01)
            self.started_at = time.monotonic()
            self._pump_task = asyncio.create_task(self._pump())
        except BaseException:
            await self.band.stop()
            raise

    @staticmethod
    def _subscribe(channel, callback):
        def consumed(data):
            try:
                callback(data)
            finally:
                # The pinned library duplicates callback deliveries into both recv
                # queues. This adapter is callback-only, so consume those copies.
                channel._recv_queue.clear()
                channel._msg_queue.clear()
                channel._recv_ready.clear()
                channel._msg_ready.clear()

        channel.on_message(consumed)

    def on_control(self, callback):
        self._subscribe(self.control, callback)

    def on_clipboard(self, callback):
        self._subscribe(self.clipboard, callback)

    def on_stream(self, kind, callback):
        def receive(data, address, timestamp):
            if address == self.peer:
                callback(data)

        self.streams[kind].on_receive(receive)

    def send_control(self, data: bytes, *, clipboard=False):
        channel = self.clipboard if clipboard else self.control
        # The pinned Channel has an unbounded send deque. Bound admission here.
        limit = 2200
        needed = (len(data) + 1002) // 1003
        if self._closed or len(channel._send_queue) + needed > limit:
            raise BufferError("reliable surface queue exhausted")
        channel.send(data)

    def send_latest(self, kind: str, data):
        packets = (data,) if isinstance(data, bytes) else tuple(data)
        if (
            kind not in ("motion", "hints")
            or self._closed
            or sum(map(len, packets)) > 2 * 1024 * 1024
            or any(not 0 < len(packet) <= 1156 for packet in packets)
        ):
            raise ValueError("invalid latest-wins datagram set")
        self.latest[kind] = deque(packets)
        self._wake.set()

    def send_media(self, kind: str, fragments):
        if kind not in self.media or self._closed:
            raise ValueError("invalid media lane")
        fragments = tuple(fragments)
        if any(not 0 < len(data) <= 1156 for data in fragments):
            raise ValueError("oversized media datagram")
        size = sum(map(len, fragments))
        if self.queued_bytes + size > self.max_queue_bytes:
            raise BufferError("media queue exhausted")
        self.media[kind].extend(fragments)
        self.queued_bytes += size
        self._wake.set()

    async def _pump(self):
        while True:
            await self._wake.wait()
            if self.band.transport._send_queue.qsize() >= 4096:
                await asyncio.sleep(0.001)
                continue
            if self.latest:
                kind = "motion" if "motion" in self.latest else "hints"
                data = self.latest[kind].popleft()
                if not self.latest[kind]:
                    del self.latest[kind]
            else:
                kind = "cold" if self.media["cold"] else "hot"
                if not self.media[kind]:
                    self._wake.clear()
                    continue
                data = self.media[kind].popleft()
                self.queued_bytes -= len(data)
            self.streams[kind].send(data)
            self.sent_bytes[kind] += len(data) + 44
            await asyncio.sleep(0)  # Let control/ACK callbacks run between datagrams.

    async def stop(self):
        self._closed = True
        if self._pump_task:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        await self.band.stop()
        self.latest.clear()
        for queue in self.media.values():
            queue.clear()
        self.queued_bytes = 0
