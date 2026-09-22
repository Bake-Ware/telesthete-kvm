"""Telesthete wire-v1.2 transport and bounded, ordered application messages.

Small control/input messages use selective-repeat delivery over a dedicated
Stream. Pointer positions use a separate lossy Stream. All callbacks run on the
asyncio thread. Clipboard chunks use a separate reliable lane.
"""

import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field

from telesthete.band import Band
from telesthete.protocol.stream import Stream
from telesthete.transport.discovery import Discovery

log = logging.getLogger(__name__)
VERSION = 2
MAX_PACKET = 1400
WINDOW = 32


@dataclass
class Lane:
    send_next: int = 1
    recv_next: int = 1
    pending: dict = field(default_factory=dict)
    buffered: dict = field(default_factory=dict)


class Network:
    def __init__(
        self, psk, hostname, callback, *, port=9999, discovery=True, peers=(), hub=None
    ):
        # Nanosecond epochs reduce same-epoch collisions between peers sharing
        # a key; wire fields support u64. Restart epochs increase with wall time.
        self.band = Band(psk, hostname, bind_port=port, session_epoch=time.time_ns())
        self.hostname, self.callback = hostname, callback
        self.hub = hub
        if hub:
            from .hub import HubTransport

            transport = HubTransport(self.band, hub)
            self.band.transport = transport
            self.band.control.transport = transport
        self.discovery_enabled = discovery and not hub
        self.discovery = None
        self.manual = list(peers)
        self.addresses = {}
        self.epochs = {}
        self.last_seen = {}
        self.links = {}
        self.incoming_links = {}
        self.lanes = defaultdict(Lane)
        self.senders = {}
        self.tasks = []
        self.running = False
        self.on_lost = lambda name: None
        self.on_join = lambda name: None
        self.allowed = set()
        for sid in (71, 72, 73):
            stream = self.band.stream(sid, priority=128 if sid == 73 else 0)
            stream.on_receive(
                lambda data, addr, ts, sid=sid: self.receive(data, addr, sid)
            )

    async def start(self):
        self.running = True
        await self.band.start()
        if self.discovery_enabled:
            self.discovery = Discovery(
                self.hostname, self.band.transport.local_address[1], self.discovered
            )
            self.discovery.start()
            self.tasks.append(asyncio.create_task(self.discovery.run()))
        self.tasks.append(asyncio.create_task(self.maintenance()))

    def discovered(self, name, ip, port):
        if name != self.hostname and (not self.allowed or name in self.allowed):
            addr = (ip, port)
            if addr not in self.manual and len(self.manual) < 256:
                self.manual.append(addr)
            self.band.connect_peer(*addr)

    def sender(self, addr, sid):
        key = addr, sid
        if key not in self.senders:
            stream = Stream(
                self.band.band_id,
                sid,
                transport=self.band.transport,
                priority=128 if sid == 73 else 0,
                send_crypto=self.band.send_crypto,
                recv_crypto=self.band.recv_crypto,
                seq_source=self.band.seq_source,
            )
            stream.add_destination(addr)
            self.senders[key] = stream
        return self.senders[key]

    def emit(self, peer, payload, sid=71):
        addr = self.addresses.get(peer)
        if addr is None:
            return False
        envelope = {
            "v": VERSION,
            "to": peer,
            "from": self.hostname,
            "link": self.links.get(peer),
            **payload,
        }
        data = json.dumps(envelope, ensure_ascii=True, separators=(",", ":")).encode()
        if len(data) > (8192 if sid == 72 else MAX_PACKET):
            raise ValueError("Message exceeds datagram limit")
        self.sender(addr, sid).send(data)
        return True

    async def send(self, peer, kind, data, lane=71):
        if peer not in self.addresses:
            raise ConnectionError(f"Peer {peer} is not connected")
        state = self.lanes[peer, lane]
        while len(state.pending) >= WINDOW:
            await asyncio.sleep(0.01)
            if (
                not self.running
                or peer not in self.addresses
                or self.lanes[peer, lane] is not state
            ):
                raise ConnectionError("Peer disconnected")
        seq = state.send_next
        state.send_next += 1
        payload = {"seq": seq, "kind": kind, "data": data}
        future = asyncio.get_running_loop().create_future()
        state.pending[seq] = [payload, time.monotonic(), future]
        try:
            self.emit(peer, payload, lane)
            await asyncio.wait_for(asyncio.shield(future), 3)
        except BaseException:
            state.pending.pop(seq, None)
            if not future.done():
                future.cancel()
            # A missing reliable sequence must never permanently wedge a lane.
            if self.running:
                self.forget(peer)
            raise

    def motion(self, peer, kind, data):
        self.emit(peer, {"kind": kind, "data": data}, 72)

    def receive(self, raw, addr, sid):
        try:
            if len(raw) > (8192 if sid == 72 else MAX_PACKET):
                return
            msg = json.loads(raw)
            if (
                not isinstance(msg, dict)
                or msg.get("v") != VERSION
                or msg.get("to") != self.hostname
            ):
                return
            peer = msg.get("from")
            transport_peer = self.band.peers.get(addr)
            if (
                not transport_peer
                or transport_peer.hostname != peer
                or peer not in self.addresses
                or self.addresses[peer] != addr
            ):
                return
            self.last_seen[peer] = time.monotonic()
            link = msg.get("link")
            if not isinstance(link, str) or len(link) != 16:
                return
            if sid == 72 and msg.get("kind") == "ping":
                if self.incoming_links.get(peer) != link:
                    self.incoming_links[peer] = link
                    for lane_id in (71, 73):
                        lane = self.lanes[peer, lane_id]
                        lane.recv_next = 1
                        lane.buffered.clear()
                    self.on_join(peer)
                return
            if self.incoming_links.get(peer) != link:
                return
            if sid == 72:
                self.callback(peer, msg["kind"], msg["data"])
                return
            state = self.lanes[peer, sid]
            if "ack" in msg:
                ack = msg["ack"]
                if type(ack) is not int or msg.get("ack_link") != self.links.get(peer):
                    return
                entry = state.pending.pop(ack, None)
                if entry and not entry[2].done():
                    entry[2].set_result(None)
                return
            seq = msg["seq"]
            if type(seq) is not int or seq < 1 or seq >= state.recv_next + WINDOW:
                return
            if seq >= state.recv_next:
                state.buffered.setdefault(seq, msg)
            self.emit(peer, {"ack": seq, "ack_link": link}, sid)
            while state.recv_next in state.buffered:
                item = state.buffered.pop(state.recv_next)
                state.recv_next += 1
                self.callback(peer, item["kind"], item["data"])
        except (ValueError, TypeError, KeyError, AttributeError):
            log.debug("Rejected malformed packet", exc_info=True)

    def forget(self, peer):
        was_known = peer in self.addresses
        self.addresses.pop(peer, None)
        self.epochs.pop(peer, None)
        self.last_seen.pop(peer, None)
        self.links.pop(peer, None)
        self.incoming_links.pop(peer, None)
        for key in [k for k in self.lanes if k[0] == peer]:
            state = self.lanes.pop(key)
            for _, _, future in state.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Peer disconnected"))
        if was_known:
            self.on_lost(peer)

    async def maintenance(self):
        last_hello = 0
        while self.running:
            now = time.monotonic()
            if now - last_hello >= 1:
                for addr in self.manual:
                    self.band.connect_peer(*addr)
                if self.hub:
                    self.band.connect_peer("hub", 0)
                last_hello = now
            by_name = defaultdict(list)
            for addr, p in list(self.band.peers.items()):
                if p.hostname != self.hostname and (
                    not self.allowed or p.hostname in self.allowed
                ):
                    by_name[p.hostname].append((addr, p))
            for name, peers in by_name.items():
                if len(peers) != 1:
                    self.forget(name)
                    continue
                addr, p = peers[0]
                if (
                    name not in self.addresses
                    or self.epochs[name] != p.session_epoch
                    or self.addresses[name] != addr
                ):
                    self.forget(name)
                    self.addresses[name] = addr
                    self.epochs[name] = p.session_epoch
                    self.last_seen[name] = now
                    self.links[name] = secrets.token_hex(8)
            for name in list(self.addresses):
                if name not in by_name or now - self.last_seen[name] > 4:
                    self.forget(name)
                else:
                    self.motion(name, "ping", {})
            for (peer, sid), state in list(self.lanes.items()):
                for entry in list(state.pending.values()):
                    if now - entry[1] >= 0.15:
                        self.emit(peer, entry[0], sid)
                        entry[1] = now
            await asyncio.sleep(0.05)

    async def stop(self):
        self.running = False
        for name in list(self.addresses):
            self.forget(name)
        if self.discovery:
            await self.discovery.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.band.stop()
