"""KVM application: explicit focus leases, recoverable input, and clipboard sync."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import signal
import socket
import time
from pathlib import Path

from .clipboard_sync import MAX_CLIPBOARD, Clipboard
from .edge import CoordinateMapper
from .network import Network

log = logging.getLogger(__name__)
LEASE_TIMEOUT = 1.5


def valid_key(key):
    if not isinstance(key, str) or len(key) > 48:
        return False
    if key.startswith("char:"):
        return len(key[5:]) == 1
    if key.startswith("vk:"):
        return key[3:].isdigit() and 0 <= int(key[3:]) <= 65535
    return key in {
        "special:" + k
        for k in (
            "alt",
            "alt_l",
            "alt_r",
            "alt_gr",
            "backspace",
            "caps_lock",
            "cmd",
            "cmd_l",
            "cmd_r",
            "ctrl",
            "ctrl_l",
            "ctrl_r",
            "delete",
            "down",
            "end",
            "enter",
            "esc",
            "home",
            "insert",
            "left",
            "menu",
            "num_lock",
            "page_down",
            "page_up",
            "pause",
            "print_screen",
            "right",
            "scroll_lock",
            "shift",
            "shift_l",
            "shift_r",
            "space",
            "tab",
            "up",
            *("f" + str(i) for i in range(1, 25)),
            "media_play_pause",
            "media_volume_mute",
            "media_volume_down",
            "media_volume_up",
            "media_previous",
            "media_next",
        )
    }


BUTTONS = {"left", "right", "middle", "x1", "x2"}


class KVMApp:
    def __init__(
        self,
        psk,
        hostname=None,
        bind_port=9999,
        enable_discovery=True,
        hub_url=None,
        peers=(),
        *,
        desktop=None,
        clipboard=None,
        status_file=None,
    ):
        self.hostname = hostname or socket.gethostname()
        if not self.hostname or len(self.hostname) > 128:
            raise ValueError("Hostname must contain 1–128 characters")
        self.mapper = CoordinateMapper(self.hostname)
        self.network = Network(
            psk,
            self.hostname,
            self.receive,
            port=bind_port,
            discovery=enable_discovery,
            peers=peers,
            hub=hub_url,
        )
        self.network.on_join = self.joined
        self.network.on_lost = self.lost
        self.desktop = desktop
        self.clipboard = clipboard
        self.status_file = Path(status_file) if status_file else None
        self.ready = set()
        self.layout_hash = ""
        self.running = False
        self.tasks = set()
        self.queue = asyncio.Queue(maxsize=2048)
        self.loop = None
        self.target = None
        self.owner = None
        self.token = None
        self.position = None
        self.return_position = None
        self.keys, self.buttons = set(), set()
        self.last_focus = 0
        self.cooldown = 0
        self.pending_focus = None
        self.input_serial = 0
        self.received_serial = 0
        self.clip_clock = 0
        self.clip_revision = (0, "")
        self.clip_incoming = {}
        self.clip_outgoing = {}
        self.clip_sent = {}
        self.latest_clip = None
        self.pending_clip = None

    def set_layout(self, monitors):
        self.mapper.set_layout(monitors)
        self.layout_hash = hashlib.sha256(
            json.dumps(self.mapper.get_layout_config(), sort_keys=True).encode()
        ).hexdigest()
        self.network.allowed = {m.peer for m in self.mapper.monitors} - {self.hostname}

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def done(t):
            self.tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc:
                    log.warning("Operation failed: %s", exc)

        task.add_done_callback(done)
        return task

    async def start(self):
        if not self.mapper.monitors:
            raise ValueError("A monitor layout is required")
        self.loop = asyncio.get_running_loop()
        if self.desktop is None:
            from .hid import Desktop

            self.desktop = Desktop(self.capture)
        if self.clipboard is None:
            self.clipboard = Clipboard()
        self.running = True
        try:
            await self.network.start()
            self.desktop.start()
            self.spawn(self.input_loop())
            self.spawn(self.tick())
            self.spawn(self.clipboard_loop())
            log.info("KVM %s ready; emergency return: Ctrl+Alt+Esc", self.hostname)
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if not self.running:
            return
        self.restore("shutdown")
        await asyncio.sleep(0.1)
        self.running = False
        if self.desktop:
            self.desktop.stop()
        await self.network.stop()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.write_status()

    def joined(self, peer):
        self.spawn(self.network.send(peer, "hello", {"layout": self.layout_hash}))

    def lost(self, peer):
        self.ready.discard(peer)
        self.clip_sent.pop(peer, None)
        self.clip_incoming.pop(peer, None)
        if peer in (self.owner, self.target) or (
            self.pending_focus and self.pending_focus[0] == peer
        ):
            self.restore("peer disconnected")

    def capture(self, kind, data):
        # pynput invokes callbacks on OS threads; no network/asyncio mutation there.
        if self.loop and self.running:
            self.loop.call_soon_threadsafe(self.enqueue, kind, data)

    def enqueue(self, kind, data):
        try:
            self.queue.put_nowait((kind, data))
        except asyncio.QueueFull:
            self.restore("input queue overflow")

    async def input_loop(self):
        while self.running:
            kind, data = await self.queue.get()
            try:
                self.local_event(kind, data)
            except Exception:
                log.exception("Input handling failed")
                self.restore("input error")

    def local_event(self, kind, data):
        if kind == "key":
            token = data["key"]
            if data["pressed"]:
                self.keys.add(token)
            else:
                self.keys.discard(token)
            if (
                "special:esc" in self.keys
                and self.keys & {"special:ctrl", "special:ctrl_l", "special:ctrl_r"}
                and self.keys
                & {"special:alt", "special:alt_l", "special:alt_r", "special:alt_gr"}
            ):
                self.restore("emergency hotkey")
                return
        elif kind == "button":
            (self.buttons.add if data["pressed"] else self.buttons.discard)(
                data["button"]
            )
        if self.owner:
            return  # Includes injected events: never echo remote input.
        if self.pending_focus:
            return
        if self.target:
            if kind == "delta":
                dx, dy = (
                    max(-4096, min(data["dx"], 4096)),
                    max(-4096, min(data["dy"], 4096)),
                )
                peer, x, y = self.mapper.move(self.target, *self.position, dx, dy)
                if peer == self.hostname:
                    self.return_position = self.mapper.global_to_local(peer, x, y)
                    self.restore("crossed back to local screen")
                elif peer != self.target:
                    self.spawn(self.switch(peer, x, y))
                else:
                    self.position = (x, y)
                    self.network.motion(
                        self.target, "pointer", {"token": self.token, "x": x, "y": y}
                    )
            elif kind in ("key", "button", "scroll"):
                self.input_serial += 1
                self.spawn(
                    self.network.send(
                        self.target,
                        "input",
                        {
                            "token": self.token,
                            "serial": self.input_serial,
                            "kind": kind,
                            **data,
                        },
                    )
                )
            return
        if kind == "move" and time.monotonic() >= self.cooldown:
            coords = self.mapper.local_to_global(self.hostname, data["x"], data["y"])
            transition = self.mapper.check_edge_transition(*coords) if coords else None
            if transition and transition[0] in self.ready:
                # Mark pending synchronously so queued mouse events cannot spawn
                # multiple concurrent focus requests.
                self.pending_focus = (transition[0], None, None)
                self.spawn(self.switch(*transition))

    async def switch(self, peer, x, y):
        if peer not in self.ready or self.owner:
            self.pending_focus = None
            return
        if self.target:
            self.restore("switching peer")
        self.return_position = self.desktop.position
        token = secrets.token_hex(16)
        future = self.loop.create_future()
        self.pending_focus = (peer, token, future)
        try:
            await self.network.send(peer, "request", {"token": token, "x": x, "y": y})
            granted = await asyncio.wait_for(future, 2)
            if not granted or not self.pending_focus or self.pending_focus[1] != token:
                raise ConnectionError("Focus request declined or cancelled")
            m = self.mapper.get_local_monitors()[0]
            ox, oy = self.mapper.origin(m)
            if hasattr(self.desktop, "release_local"):
                self.desktop.release_local(self.keys, self.buttons)
            self.desktop.set_forwarding(True, (ox + m.width // 2, oy + m.height // 2))
            self.target, self.token, self.position = peer, token, (x, y)
            self.last_focus = time.monotonic()
            self.input_serial = 0
            log.info("Controlling %s", peer)
        except (Exception, asyncio.CancelledError):
            self.restore("focus request failed")
            if peer in self.network.addresses:
                self.spawn(self.network.send(peer, "release", {"token": token}))
        finally:
            if self.pending_focus and self.pending_focus[1] == token:
                self.pending_focus = None

    def restore(self, reason):
        peer, token = self.target or self.owner, self.token
        was_active = bool(peer or self.pending_focus)
        self.target = self.owner = self.token = None
        if (
            self.pending_focus
            and self.pending_focus[2]
            and not self.pending_focus[2].done()
        ):
            self.pending_focus[2].set_result(False)
        self.pending_focus = None
        if self.desktop:
            self.desktop.set_forwarding(False)
            self.desktop.release_all()
            if self.return_position is not None:
                self.desktop.move(*self.return_position)
                self.return_position = None
        self.keys.clear()
        self.buttons.clear()
        self.cooldown = time.monotonic() + 0.5
        if peer and self.running and peer in self.network.addresses:
            self.spawn(self.network.send(peer, "release", {"token": token}))
        if was_active:
            log.info("Local control restored: %s", reason)

    def receive(self, peer, kind, data):
        if not isinstance(data, dict):
            return
        try:
            self._receive(peer, kind, data)
        except (ValueError, TypeError, KeyError, OverflowError):
            log.debug("Invalid %s from %s", kind, peer, exc_info=True)
        except Exception:
            log.exception("Desktop operation failed")
            self.restore("desktop operation failed")

    def _receive(self, peer, kind, data):
        now = time.monotonic()
        if kind == "ping":
            return
        if kind == "hello":
            if data.get("layout") == self.layout_hash:
                self.ready.add(peer)
                log.info("Peer ready: %s", peer)
            else:
                self.ready.discard(peer)
                log.warning("Layout mismatch with %s", peer)
            return
        if peer not in self.ready:
            return
        if kind.startswith("clip_"):
            self.receive_clipboard(peer, kind, data)
            return
        token = data.get("token")
        if not isinstance(token, str) or len(token) != 32:
            return
        if kind == "request":
            x, y = data["x"], data["y"]
            if type(x) is not int or type(y) is not int:
                return
            lx, ly = self.mapper.global_to_local(self.hostname, x, y)
            available = (
                not self.owner
                and not self.target
                and not self.pending_focus
                and now >= self.cooldown
            )
            if available:
                self.owner, self.token = peer, token
                self.last_focus = now
                self.received_serial = 0
                self.desktop.move(lx, ly)
                log.info("Controlled by %s", peer)
            self.spawn(
                self.network.send(
                    peer, "grant", {"token": token, "accepted": available}
                )
            )
            return
        if kind == "grant":
            if self.pending_focus and self.pending_focus[:2] == (peer, token):
                future = self.pending_focus[2]
                if future and not future.done():
                    future.set_result(data.get("accepted") is True)
            return
        if token != self.token or peer not in (self.owner, self.target):
            return
        if kind == "release":
            self.restore("peer released focus")
        elif kind == "alive" and peer == self.target:
            self.last_focus = now
        elif peer == self.owner:
            if kind == "pointer":
                x, y = data["x"], data["y"]
                if type(x) is not int or type(y) is not int:
                    return
                self.desktop.move(*self.mapper.global_to_local(self.hostname, x, y))
                self.last_focus = now
            elif kind == "input":
                serial = data["serial"]
                if type(serial) is not int or serial <= self.received_serial:
                    return
                subtype = data["kind"]
                if subtype == "key":
                    if not valid_key(data["key"]) or type(data["pressed"]) is not bool:
                        return
                elif subtype == "button":
                    if (
                        data["button"] not in BUTTONS
                        or type(data["pressed"]) is not bool
                    ):
                        return
                elif subtype == "scroll":
                    if any(
                        type(data[k]) is not int or abs(data[k]) > 100
                        for k in ("dx", "dy")
                    ):
                        return
                else:
                    return
                self.desktop.inject(subtype, data)
                self.received_serial = serial
                self.last_focus = now
            elif kind == "state":
                keys, buttons, serial = data["keys"], data["buttons"], data["serial"]
                if (
                    not isinstance(keys, list)
                    or len(keys) > 100
                    or not all(valid_key(k) for k in keys)
                    or not isinstance(buttons, list)
                    or not all(b in BUTTONS for b in buttons)
                    or type(serial) is not int
                ):
                    return
                # State never overtakes reliable key transitions: applying a
                # future snapshot would swallow fast taps still in flight.
                if serial == self.received_serial:
                    self.desktop.apply_state(keys, buttons)
                x, y = data["x"], data["y"]
                if type(x) is int and type(y) is int:
                    self.desktop.move(*self.mapper.global_to_local(self.hostname, x, y))
                self.last_focus = now

    async def tick(self):
        while self.running:
            try:
                now = time.monotonic()
                if hasattr(self.desktop, "check"):
                    self.desktop.check()
                if (
                    self.target or self.owner
                ) and now - self.last_focus > LEASE_TIMEOUT:
                    self.restore("focus lease expired")
                if self.target:
                    self.network.motion(
                        self.target,
                        "state",
                        {
                            "token": self.token,
                            "keys": sorted(self.keys),
                            "buttons": sorted(self.buttons),
                            "serial": self.input_serial,
                            "x": self.position[0],
                            "y": self.position[1],
                        },
                    )
                elif self.owner:
                    self.network.motion(self.owner, "alive", {"token": self.token})
                for peer, incoming in list(self.clip_incoming.items()):
                    if now - incoming["time"] > 15:
                        self.clip_incoming.pop(peer, None)
                self.write_status()
            except Exception:
                log.exception("Runtime maintenance failed")
                self.restore("runtime error")
            await asyncio.sleep(0.1)

    async def clipboard_loop(self):
        while self.running:
            try:
                if self.pending_clip is not None:
                    revision, text = self.pending_clip
                    await asyncio.to_thread(self.clipboard.set, text)
                    if self.pending_clip and self.pending_clip[0] == revision:
                        self.pending_clip = None
                text = await asyncio.to_thread(self.clipboard.poll)
                if text is not None:
                    self.clip_clock += 1
                    self.clip_revision = (self.clip_clock, self.hostname)
                    self.latest_clip = (self.clip_revision, text)
                if self.latest_clip:
                    for peer in self.ready:
                        if (
                            peer not in self.clip_outgoing
                            and self.clip_sent.get(peer) != self.latest_clip[0]
                        ):
                            value = self.latest_clip
                            self.clip_outgoing[peer] = value[0]
                            self.spawn(self.send_clipboard(peer, *value))
            except Exception:
                log.warning("Clipboard operation failed", exc_info=True)
            await asyncio.sleep(0.5)

    async def send_clipboard(self, peer, revision, text):
        try:
            data = text.encode("utf-8")
            if len(data) > MAX_CLIPBOARD:
                return
            await self.network.send(
                peer,
                "clip_begin",
                {
                    "revision": revision,
                    "size": len(data),
                    "hash": hashlib.sha256(data).hexdigest(),
                },
                lane=73,
            )
            # A short window allows large clipboard transfers without flooding
            # the input lane or exhausting memory.
            for start in range(0, len(data), 600 * 16):
                await asyncio.gather(
                    *(
                        self.network.send(
                            peer,
                            "clip_chunk",
                            {
                                "offset": offset,
                                "data": base64.b64encode(
                                    data[offset : offset + 600]
                                ).decode(),
                            },
                            lane=73,
                        )
                        for offset in range(
                            start, min(start + 600 * 16, len(data)), 600
                        )
                    )
                )
            await self.network.send(peer, "clip_end", {}, lane=73)
            self.clip_sent[peer] = revision
        finally:
            self.clip_outgoing.pop(peer, None)

    def receive_clipboard(self, peer, kind, data):
        if kind == "clip_begin":
            revision = data["revision"]
            size = data["size"]
            if (
                not isinstance(revision, list)
                or len(revision) != 2
                or type(revision[0]) is not int
                or not 0 < revision[0] < 2**63
                or revision[1] != peer
                or type(size) is not int
                or not 0 <= size <= MAX_CLIPBOARD
                or not isinstance(data["hash"], str)
                or len(data["hash"]) != 64
            ):
                return
            self.clip_clock = max(self.clip_clock, revision[0])
            self.clip_incoming[peer] = {
                "revision": tuple(revision),
                "size": size,
                "hash": data["hash"],
                "data": bytearray(),
                "time": time.monotonic(),
            }
        elif peer in self.clip_incoming:
            entry = self.clip_incoming[peer]
            if kind == "clip_chunk":
                if data["offset"] != len(entry["data"]):
                    self.clip_incoming.pop(peer, None)
                    return
                chunk = base64.b64decode(data["data"], validate=True)
                if len(chunk) > 600 or len(entry["data"]) + len(chunk) > entry["size"]:
                    self.clip_incoming.pop(peer, None)
                    return
                entry["data"].extend(chunk)
                entry["time"] = time.monotonic()
            elif kind == "clip_end":
                self.clip_incoming.pop(peer, None)
                raw = bytes(entry["data"])
                if (
                    len(raw) == entry["size"]
                    and hashlib.sha256(raw).hexdigest() == entry["hash"]
                    and entry["revision"] > self.clip_revision
                ):
                    text = raw.decode("utf-8")
                    self.pending_clip = (entry["revision"], text)
                    self.clip_revision = entry["revision"]
                    self.latest_clip = None
                    log.info("Clipboard received from %s (%s bytes)", peer, len(raw))

    def write_status(self):
        if self.status_file:
            data = {
                "hostname": self.hostname,
                "running": self.running,
                "peers": sorted(self.ready),
                "target": self.target,
                "owner": self.owner,
                "pending": bool(self.pending_focus),
                "position": self.position,
                "clipboard_revision": self.clip_revision,
            }
            temp = self.status_file.with_suffix(".tmp")
            temp.write_text(json.dumps(data))
            temp.replace(self.status_file)


def parser():
    import argparse

    p = argparse.ArgumentParser(
        description="Share keyboard, mouse and text clipboard over Telesthete"
    )
    secret = p.add_mutually_exclusive_group()
    secret.add_argument(
        "--psk", help="Shared secret (prefer --psk-file or TELESTHETE_PSK)"
    )
    secret.add_argument("--psk-file", type=Path, help="File containing shared secret")
    p.add_argument("--hostname", default=socket.gethostname())
    p.add_argument(
        "--layout", type=Path, required=True, help="Shared monitor layout JSON"
    )
    p.add_argument("--port", type=int, default=9999)
    p.add_argument(
        "--peer",
        action="append",
        default=[],
        help="Manual IPv4/hostname:port peer (repeatable)",
    )
    p.add_argument("--no-discovery", action="store_true")
    p.add_argument("--hub", help="Telesthetium ws:// or wss:// URL, including /band")
    p.add_argument(
        "--status-file",
        help="Write runtime state as JSON (no input or clipboard contents)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration without opening input devices",
    )
    p.add_argument("--verbose", action="store_true")
    return p


async def run(args):
    psk = (
        args.psk_file.read_text().strip()
        if args.psk_file
        else args.psk or os.environ.get("TELESTHETE_PSK")
    )
    if not psk:
        raise ValueError("Supply --psk-file, TELESTHETE_PSK, or --psk")
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    if args.hub and args.peer:
        raise ValueError("--hub and --peer cannot be combined")
    peers = []
    for value in args.peer:
        host, port = value.rsplit(":", 1)
        if not 1 <= int(port) <= 65535:
            raise ValueError("Invalid peer port")
        peers.append((socket.gethostbyname(host), int(port)))
    app = KVMApp(
        psk,
        args.hostname,
        args.port,
        not args.no_discovery,
        args.hub,
        peers,
        status_file=args.status_file,
    )
    app.set_layout(json.loads(args.layout.read_text()))
    if args.check:
        print(
            f"Configuration valid: {len(app.mapper.monitors)} monitors, hostname {app.hostname}"
        )
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await app.start()
        await stop.wait()
    finally:
        await app.stop()


def main():
    args = parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
