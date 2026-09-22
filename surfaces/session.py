"""Configured-peer cold-lane origin/client sessions for the native prototype."""

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, replace

from .media import Reassembler, fragment
from .model import ProtocolError, Size, Surface, TreeReplica, ViewHint, Visibility, uint
from .policy import History, decide
from .protocol import counter, decode, encode, surface_from_dict, surface_to_dict
from .tiles import DamageTracker, Texture, decode_blocks, encode_blocks
from .video import decoder_capabilities


def negotiated_canvas(decoders):
    for decoder in decoders:
        if not isinstance(decoder, dict) or decoder.get("codec") != "h264":
            continue
        width = uint(decoder.get("max_w"), 32, "decode width")
        height = uint(decoder.get("max_h"), 32, "decode height")
        instances = uint(decoder.get("max_instances"), 32, "decoder instances")
        fps = uint(decoder.get("max_fps"), 32, "decode fps")
        if (
            width >= 64
            and height >= 64
            and instances
            and fps >= 60
            and decoder.get("chroma") == "420-8"
        ):
            return Size(min(width, 3840), min(height, 2160))
    raise ProtocolError("hot lane requires an H.264 420-8 decoder supporting 60 fps")


class OriginSession:
    def __init__(
        self,
        link,
        name,
        client_name,
        *,
        on_input=None,
        on_request=None,
        hot_backend=None,
        adaptive=False,
        clipboard=None,
    ):
        self.link, self.name, self.client_name = link, name, client_name
        self.on_input = on_input
        self.on_request = on_request
        self.last_seen = time.monotonic()
        self.hot = None
        self.adaptive = adaptive
        self.assignments = {}
        self.adaptive_history = {}
        self.damage = {}
        self.lane_migrations = 0
        if hot_backend:
            from .hot import HotAtlas

            self.hot = HotAtlas(backend=hot_backend)
        self.session_id = None
        self.tree = TreeReplica()
        self.tree.snapshot(0, [])
        self.trackers = {}
        self.hints = {}
        self.hint_media = Reassembler(
            cold_lanes=(), byte_limit=2 * 1024 * 1024, frame_limit=4
        )
        self.histories = {}
        self.hot_size_history = {}
        self.hot_sizes = {}
        self.refresh = set()
        self.frame_seq = 0
        self.epoch = 0
        self.hint_seq = -1
        self.motion_seq = -1
        self.repairs = OrderedDict()
        self.repair_bytes = 0
        self._last_cold = {}
        self._source_buffers = {}
        self._requests = OrderedDict()
        self._hello_id = None
        self.errors = []
        self.link.on_control(self._control)
        self.link.on_stream("hints", self._hint)
        self.link.on_stream("motion", self._control)
        self.clipboard = None
        if clipboard:
            from .clipboard import ClipboardFlow

            self.clipboard = ClipboardFlow(self, client_name, *clipboard)
        self.encode_seconds = 0.0
        self.blocks_sent = 0

    def _send(self, cap, args):
        self.link.send_control(
            encode(
                cap,
                args,
                target=self.client_name,
                message_id=uuid.uuid4().hex,
                session_id=self.session_id,
            )
        )

    def _control(self, data):
        try:
            # Parse only enough to select bootstrap/session validation. All other
            # fields are consumed only from the strictly validated envelope.
            candidate = json.loads(data)
            bootstrap = (
                isinstance(candidate, dict) and candidate.get("cap") == "surface-hello"
            )
            body = decode(
                data,
                target=self.name,
                session_id=None if bootstrap else self.session_id,
            )
            args, cap = body["args"], body["cap"]
            self.last_seen = time.monotonic()
            if bootstrap:
                if not isinstance(args.get("caps"), dict):
                    raise ProtocolError("invalid client capabilities")
                if "qoi" not in args["caps"].get("tile_codecs", []):
                    raise ProtocolError("prototype requires qoi support")
                client_caps = args.get("caps", {})
                canvas = (
                    negotiated_canvas(args["caps"].get("decoders", []))
                    if self.hot
                    else None
                )
                if self._hello_id is not None and self._hello_id != body["id"]:
                    raise ProtocolError(
                        "new subscription requires a fresh transport session"
                    )
                if self._hello_id != body["id"]:
                    if self.hot:
                        self.hot.max_canvas = canvas
                    self.session_id = uuid.uuid4().hex
                    self._hello_id = body["id"]
                    self.frame_seq = 0
                    self.hint_seq = -1
                    self.hints.clear()
                    self.histories.clear()
                    self.repairs.clear()
                    self.repair_bytes = 0
                    self._requests.clear()
                if self.clipboard:
                    self.clipboard.enabled = client_caps.get("clipboard") is True
                self._send(
                    "surface-welcome",
                    {
                        "hello_id": body["id"],
                        "route": "direct",
                        "caps": {
                            "encoders": [
                                {
                                    "codec": "h264",
                                    "max_sessions": 1,
                                    "roi_support": False,
                                    "intra_refresh_support": False,
                                }
                            ]
                            if self.hot
                            else [],
                            "input_fidelity": "none"
                            if self.on_input is None
                            else "focused-only",
                            "clipboard": bool(
                                self.clipboard and self.clipboard.enabled
                            ),
                        },
                        "channels": {
                            "control": self.link.channel_base,
                            "clipboard": self.link.channel_base + 1,
                            "motion": self.link.channel_base + 2,
                            "hints": self.link.channel_base + 3,
                        },
                        "lanes": (
                            [
                                {
                                    "lane_id": 0,
                                    "kind": "hot",
                                    "codec": "h264",
                                    "stream_id": self.link.channel_base + 5,
                                }
                            ]
                            if self.hot and not self.adaptive
                            else [
                                {
                                    "lane_id": 1,
                                    "kind": "cold",
                                    "codec": "qoi",
                                    "stream_id": self.link.channel_base + 4,
                                }
                            ]
                            + (
                                [
                                    {
                                        "lane_id": 0,
                                        "kind": "hot",
                                        "codec": "h264",
                                        "stream_id": self.link.channel_base + 5,
                                    }
                                ]
                                if self.adaptive
                                else []
                            )
                        ),
                    },
                )
                self._snapshot()
                self.refresh.update(self.tree.surfaces)
                return
            if body["id"] in self._requests:
                return
            self._requests[body["id"]] = None
            if len(self._requests) > 4096:
                self._requests.popitem(last=False)
            if cap == "surface-ping":
                return
            elif cap in ("clipboard-offer", "clipboard-request"):
                if not self.clipboard:
                    raise ProtocolError("clipboard was not negotiated")
                self.clipboard.handle(cap, args)
            elif cap == "surface-release":
                if self.on_request:
                    self.on_request(cap, {})
            elif cap in (
                "focus-request",
                "resize-request",
                "close-request",
                "hide-request",
            ):
                surface = self.tree.surfaces.get(args["surface_id"])
                if surface is None or not self.tree.accepts_input(surface.surface_id):
                    raise ProtocolError("request targets an unavailable surface")
                if cap == "resize-request":
                    size = surface.clamp_size(Size(args["w"], args["h"]))
                    args = {**args, "w": size.w, "h": size.h}
                if self.on_request:
                    self.on_request(cap, args)
            elif cap == "snapshot-request":
                self._snapshot()
            elif cap == "lane-resync":
                if args["lane_id"] == 0 and self.hot:
                    self.hot.force_keyframe = True
                    if self.hot.layout:
                        from .hot import layout_to_dict

                        self._send("atlas-layout", layout_to_dict(self.hot.layout))
                else:
                    self.refresh.update(self.tree.surfaces)
            elif cap == "lane-nack":
                key = (args["lane_id"], args["epoch"], args["frame_seq"])
                cached = self.repairs.get(key)
                if cached and time.monotonic() - cached[0] <= 2:
                    missing = args["missing"]
                    if not isinstance(missing, list) or any(
                        type(i) is not int or not 0 <= i < len(cached[1])
                        for i in missing
                    ):
                        raise ProtocolError("invalid repair indexes")
                    self.link.send_media(
                        "cold" if args["lane_id"] == 1 else "hot",
                        [cached[1][i] for i in missing],
                    )
                else:
                    if args["lane_id"] == 0 and self.hot:
                        self.hot.force_keyframe = True
                    else:
                        self.refresh.update(self.tree.surfaces)
            elif cap == "surface-input":
                event = args["event"]
                if event.get("kind") == "motion":
                    seq = counter(args["seq"])
                    if seq <= self.motion_seq:
                        return
                    self.motion_seq = seq
                if self.on_input and self.tree.accepts_input(event["surface_id"]):
                    from .input import validate_event

                    self.on_input(
                        validate_event(event, self.tree.surfaces[event["surface_id"]])
                    )
            else:
                raise ProtocolError("unsupported origin request")
        except (
            ValueError,
            TypeError,
            KeyError,
            BufferError,
            RuntimeError,
            OSError,
        ) as exc:
            self.errors.append(str(exc))
            self.errors[:] = self.errors[-32:]

    def _hint(self, data):
        try:
            frame = self.hint_media.feed(data, time.monotonic())
            if frame is None:
                return
            if frame.lane_id != 2 or frame.epoch != 0:
                raise ProtocolError("invalid hint fragment header")
            data = frame.data
            body = decode(data, target=self.name, session_id=self.session_id)
            if body["cap"] != "view-hint":
                raise ProtocolError("wrong hint capability")
            args = body["args"]
            seq = counter(args["seq"])
            if seq <= self.hint_seq:
                return
            hints = {}
            for hint in args["hints"]:
                hint = ViewHint(
                    hint["surface_id"],
                    Visibility(hint["visible"]),
                    Size(**hint["display_px"]),
                    hint["fovea"],
                    hint["focused"],
                    hint["priority"],
                )
                if (
                    hint.surface_id not in self.tree.surfaces
                    or hint.surface_id in hints
                ):
                    raise ProtocolError("invalid hint surface")
                hints[hint.surface_id] = hint
            self.hints, self.hint_seq = hints, seq
            self.last_seen = time.monotonic()
        except (ValueError, TypeError, KeyError) as exc:
            self.errors.append(str(exc))
            self.errors[:] = self.errors[-32:]

    def set_surfaces(self, surfaces: list[Surface]):
        if {s.surface_id: s for s in surfaces} == self.tree.surfaces:
            return
        from .tree_diff import changes_for, plan_deltas

        old = self.tree.surfaces
        steps = plan_deltas(self.tree, surfaces) if self.session_id else None
        if steps is None:
            self.tree.snapshot(self.tree.revision + 1, surfaces)
        else:
            for operation, sid, value in steps:
                revision = self.tree.revision + 1
                if operation == "add":
                    self.tree.delta(revision, "add", sid, surface=value)
                    self._send(
                        "surface-add",
                        {"tree_rev": str(revision), "surface": surface_to_dict(value)},
                    )
                elif operation == "update":
                    changes = changes_for(self.tree.surfaces[sid], value)
                    self.tree.delta(
                        revision,
                        "update",
                        sid,
                        **{key: getattr(value, key) for key in changes},
                    )
                    self._send(
                        "surface-update",
                        {
                            "tree_rev": str(revision),
                            "surface_id": sid,
                            "changes": changes,
                        },
                    )
                else:
                    self.tree.delta(revision, "remove", sid)
                    self._send(
                        "surface-remove", {"tree_rev": str(revision), "surface_id": sid}
                    )
        for sid in set(old) - set(self.tree.surfaces):
            self.trackers.pop(sid, None)
            self.histories.pop(sid, None)
            self.hot_size_history.pop(sid, None)
            self.hot_sizes.pop(sid, None)
            self.hints.pop(sid, None)
            self.refresh.discard(sid)
            self._last_cold.pop(sid, None)
            self._source_buffers.pop(sid, None)
            self.assignments.pop(sid, None)
            self.adaptive_history.pop(sid, None)
            self.damage.pop(sid, None)
        for surface in surfaces:
            if (
                surface.surface_id not in old
                or old[surface.surface_id].size != surface.size
            ):
                self.refresh.add(surface.surface_id)
        if self.session_id and steps is None:
            self._snapshot()

    def _snapshot(self):
        from .hot import layout_to_dict

        self._send(
            "surface-snapshot",
            {
                "tree_rev": str(self.tree.revision),
                "surfaces": [surface_to_dict(s) for s in self.tree.surfaces.values()],
                "layouts": (
                    [layout_to_dict(self.hot.layout)]
                    if self.hot and self.hot.layout
                    else []
                ),
            },
        )
        for sid, (lane, fence) in self.assignments.items():
            self._send(
                "surface-lane",
                {"surface_id": sid, "lane_id": lane, "first_frame": fence},
            )

    async def publish_adaptive(self, frames, *, now=None):
        """Commit policy lane changes with a reliable per-surface sequence fence."""
        if self.session_id is None:
            return 0
        import numpy as np

        now = time.monotonic() if now is None else now
        hot_frames, updates = {}, 0
        for sid, (size, raw) in frames.items():
            surface = self.tree.surfaces[sid]
            previous, fraction, changed_at = self.damage.get(sid, (None, 0, now))
            if raw != previous:
                measured = (
                    float(
                        np.mean(
                            np.frombuffer(raw, dtype=np.uint32)[::16]
                            != np.frombuffer(previous, dtype=np.uint32)[::16]
                        )
                    )
                    if previous is not None and len(previous) == len(raw)
                    else 1.0
                )
                # Hold recent damage across sparse native frames; repeated CLI
                # polls and small timestamp changes must not erase video motion.
                if measured >= fraction or now - changed_at >= 0.5:
                    fraction, changed_at = measured, now
            elif now - changed_at >= 0.5:
                fraction = 0.0
            self.damage[sid] = raw, fraction, changed_at
            history = self.adaptive_history.get(sid, History())
            decision = decide(
                surface, self.hints.get(sid), fraction, size, history, now
            )
            self.adaptive_history[sid] = replace(decision.history, lane=decision.lane)
            lane = 0 if decision.lane == "hot" else 1
            if sid not in self.assignments or self.assignments[sid][0] != lane:
                self.lane_migrations += int(sid in self.assignments)
                self.assignments[sid] = lane, self.frame_seq
                self._send(
                    "surface-lane",
                    {"surface_id": sid, "lane_id": lane, "first_frame": self.frame_seq},
                )
                self.refresh.add(sid)
                if lane == 0:
                    self.hot.force_keyframe = True
            if lane == 0:
                hot_frames[sid] = size, raw
            elif await self.publish(sid, raw, now=now):
                updates += 1
        if hot_frames and await self.publish_hot(
            hot_frames, allowed=set(hot_frames), now=now
        ):
            updates += 1
        return updates

    async def publish(self, surface_id: int, bgra: bytes, *, now=None) -> bool:
        if self.session_id is None or surface_id not in self.tree.surfaces:
            return False
        now = time.monotonic() if now is None else now
        surface = self.tree.surfaces[surface_id]
        decision = decide(
            surface,
            self.hints.get(surface_id),
            0,
            surface.size,
            self.histories.get(surface_id, History()),
            now,
        )
        self.histories[surface_id] = decision.history
        if decision.frozen:
            return False
        if (
            decision.max_updates_hz
            and now - self._last_cold.get(surface_id, -1e9)
            < 1 / decision.max_updates_hz
        ):
            return False
        if (
            not decision.refresh
            and surface_id not in self.refresh
            and self._source_buffers.get(surface_id) == bgra
        ):
            return False
        self._source_buffers[surface_id] = bgra
        tracker = self.trackers.setdefault(surface_id, DamageTracker())
        start = time.perf_counter()
        blocks = await asyncio.to_thread(
            tracker.update,
            surface_id,
            surface.size,
            bgra,
            refresh=decision.refresh or surface_id in self.refresh,
        )
        self.encode_seconds += time.perf_counter() - start
        if not blocks:
            return False
        if self.frame_seq >= 2**32:
            raise ProtocolError("frame sequence exhausted; reconnect required")
        packets = fragment(encode_blocks(blocks), 1, self.epoch, self.frame_seq)
        try:
            self.link.send_media("cold", packets)
        except BufferError:
            self.refresh.add(surface_id)
            return False
        self._send(
            "lane-status",
            {"lane_id": 1, "epoch": self.epoch, "frame_seq": self.frame_seq},
        )
        self.repairs[(1, self.epoch, self.frame_seq)] = (now, packets)
        self.repair_bytes += sum(map(len, packets))
        while self.repairs and (
            self.repair_bytes > 32 * 1024 * 1024
            or now - next(iter(self.repairs.values()))[0] > 2
        ):
            _, (_, removed) = self.repairs.popitem(last=False)
            self.repair_bytes -= sum(map(len, removed))
        self.frame_seq += 1
        self.refresh.discard(surface_id)
        self._last_cold[surface_id] = now
        self.blocks_sent += len(blocks)
        return True

    async def publish_hot(self, frames, *, allowed=None, now=None):
        if self.hot is None or self.session_id is None:
            return False
        # Frozen surfaces retain the last atlas pixels; only visible damage is updated.
        previous = (
            {
                sid: (Size(t.valid.w, t.valid.h), self.hot.pixels[sid])
                for t in self.hot.layout.tiles
                for sid in (t.surface_id,)
            }
            if self.hot.layout
            else {}
        )
        selected = dict(previous)
        visible = False
        for sid, (size, raw) in frames.items():
            hint = self.hints.get(sid)
            if hint and hint.visible in (Visibility.IN_VIEW, Visibility.PERIPHERAL):
                selected[sid] = (size, raw)
                visible = True
        selected = {
            sid: frame
            for sid, frame in selected.items()
            if sid in self.tree.surfaces and (allowed is None or sid in allowed)
        }
        if not visible or not selected:
            return False
        from .hot import layout_to_dict
        from .video import resize_bgra

        now = time.monotonic() if now is None else now
        resized = {}
        for sid, (source_size, raw) in selected.items():
            if (
                sid not in frames
                or self.hints.get(sid) is None
                or self.hints[sid].visible
                not in (Visibility.IN_VIEW, Visibility.PERIPHERAL)
            ):
                resized[sid] = source_size, raw
                continue
            surface = self.tree.surfaces[sid]
            old_size = self.hot_sizes.get(sid, source_size)
            decision = decide(
                surface,
                self.hints.get(sid),
                1.0,
                old_size,
                self.hot_size_history.get(sid, History(lane="hot")),
                now,
            )
            self.hot_size_history[sid] = decision.history
            if sid not in self.hot_sizes:
                self.hot_sizes[sid] = source_size
            elif decision.repack or (
                old_size.w > source_size.w or old_size.h > source_size.h
            ):
                self.hot_sizes[sid] = decision.target
            target = self.hot_sizes[sid]
            resized[sid] = (
                target,
                await asyncio.to_thread(resize_bgra, raw, source_size, target),
            )

        started = time.perf_counter()
        result = await asyncio.to_thread(self.hot.encode, resized)
        self.encode_seconds += time.perf_counter() - started
        if result is None:
            return False
        layout, changed, encoded, keyframe = result
        if changed:
            self._send("atlas-layout", layout_to_dict(layout))
        if self.frame_seq >= 2**32:
            raise ProtocolError("frame sequence exhausted; reconnect required")
        packets = fragment(encoded, 0, layout.epoch, self.frame_seq, int(keyframe))
        self.link.send_media("hot", packets)
        self._send(
            "lane-status",
            {"lane_id": 0, "epoch": layout.epoch, "frame_seq": self.frame_seq},
        )
        if keyframe:
            now = time.monotonic()
            self.repairs[(0, layout.epoch, self.frame_seq)] = (now, packets)
            self.repair_bytes += sum(map(len, packets))
            while self.repairs and (
                self.repair_bytes > 32 * 1024 * 1024
                or now - next(iter(self.repairs.values()))[0] > 2
            ):
                _, (_, removed) = self.repairs.popitem(last=False)
                self.repair_bytes -= sum(map(len, removed))
        self.frame_seq += 1
        return True

    def stop(self):
        if self.hot:
            self.hot.stop()


class ClientSession:
    def __init__(self, link, name, origin_name, *, clipboard=None):
        self.link, self.name, self.origin_name = link, name, origin_name
        self.session_id = None
        self.hello_id = uuid.uuid4().hex
        self.tree = TreeReplica()
        self.textures = {}
        self.assignments = {}
        self.cold_candidates = {}
        self.media = Reassembler()
        self.pending = {}
        self.completed = OrderedDict()
        self.errors = []
        self.hint_seq = 0
        self.on_texture = None
        self.decode_seconds = 0.0
        self.link.on_control(self._control)
        self.link.on_stream("cold", self._cold)
        self.motion_seq = 0
        from .hot import HotCutter

        self.hot = HotCutter()
        self.link.on_stream("hot", self._hot)
        self._last_repair = 0.0
        self.clipboard = None
        if clipboard:
            from .clipboard import ClipboardFlow

            self.clipboard = ClipboardFlow(self, origin_name, *clipboard)

    def hello(self):
        self.link.send_control(
            encode(
                "surface-hello",
                {
                    "caps": {
                        "tile_codecs": ["qoi"],
                        "clipboard": bool(self.clipboard),
                        "decoders": decoder_capabilities(),
                    }
                },
                target=self.origin_name,
                message_id=self.hello_id,
                session_id=None,
            )
        )

    def _send(self, cap, args):
        self.link.send_control(
            encode(
                cap,
                args,
                target=self.origin_name,
                message_id=uuid.uuid4().hex,
                session_id=self.session_id,
            )
        )

    def _sync_textures(self, old):
        for sid in set(old) - set(self.tree.surfaces):
            self.textures.pop(sid, None)
            self.assignments.pop(sid, None)
            self.cold_candidates.pop(sid, None)
        for sid, surface in self.tree.surfaces.items():
            previous = old.get(sid)
            if previous is None:
                self.textures[sid] = Texture(sid, surface.size)
            elif previous.size != surface.size:
                self.cold_candidates[sid] = Texture(sid, surface.size)

    def _control(self, data):
        try:
            candidate = json.loads(data)
            if self.session_id is None:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("cap") != "surface-welcome"
                ):
                    raise ProtocolError("expected welcome")
                proposed = candidate["args"]["session_id"]
                body = decode(data, target=self.name, session_id=proposed)
                if body["args"].get("hello_id") != self.hello_id:
                    raise ProtocolError("welcome does not match hello")
                self.session_id = proposed
                if self.clipboard:
                    self.clipboard.enabled = (
                        body["args"].get("caps", {}).get("clipboard") is True
                    )
                return
            body = decode(data, target=self.name, session_id=self.session_id)
            args, cap = body["args"], body["cap"]
            if cap in ("clipboard-offer", "clipboard-request"):
                if not self.clipboard:
                    raise ProtocolError("clipboard was not negotiated")
                self.clipboard.handle(cap, args)
            elif cap == "surface-snapshot":
                surfaces = [surface_from_dict(s) for s in args["surfaces"]]
                from .hot import layout_from_dict

                for layout in args.get("layouts", []):
                    self.hot.add_layout(layout_from_dict(layout))
                old = self.tree.surfaces.copy()
                if self.tree.snapshot(counter(args["tree_rev"]), surfaces):
                    self._sync_textures(old)
            elif cap in ("surface-add", "surface-update", "surface-remove"):
                old = self.tree.surfaces.copy()
                revision = counter(args["tree_rev"])
                if cap == "surface-add":
                    surface = surface_from_dict(args["surface"])
                    applied = self.tree.delta(
                        revision, "add", surface.surface_id, surface=surface
                    )
                elif cap == "surface-remove":
                    applied = self.tree.delta(revision, "remove", args["surface_id"])
                else:
                    sid = args["surface_id"]
                    raw_changes = args["changes"]
                    if (
                        sid not in old
                        or not isinstance(raw_changes, dict)
                        or "surface_id" in raw_changes
                    ):
                        raise ProtocolError("invalid surface update")
                    merged = {**surface_to_dict(old[sid]), **raw_changes}
                    updated = surface_from_dict(merged)
                    changes = {
                        key: getattr(updated, key)
                        for key in raw_changes
                        if key in Surface.__dataclass_fields__
                    }
                    applied = self.tree.delta(revision, "update", sid, **changes)
                if applied:
                    self._sync_textures(old)
                elif self.tree.needs_snapshot:
                    self._send(
                        "snapshot-request", {"tree_rev": str(self.tree.revision)}
                    )
            elif cap == "surface-lane":
                sid, lane, fence = (
                    args["surface_id"],
                    args["lane_id"],
                    args["first_frame"],
                )
                uint(fence, 32, "first_frame")
                if (
                    sid not in self.tree.surfaces
                    or type(lane) is not int
                    or lane not in (0, 1)
                ):
                    raise ProtocolError("invalid surface lane")
                previous = self.assignments.get(sid)
                if previous is not None and fence <= previous[1]:
                    return
                self.assignments[sid] = lane, fence
                if lane == 1:
                    self.cold_candidates[sid] = Texture(
                        sid, self.tree.surfaces[sid].size
                    )
                # Media may outrun reliable control. Repair the destination even
                # if its initial batch was already decoded under the old mapping.
                self._send(
                    "lane-resync", {"lane_id": lane, "epoch": None, "frame_seq": None}
                )
            elif cap == "atlas-layout":
                from .hot import layout_from_dict

                self.hot.add_layout(layout_from_dict(args))
            elif cap == "lane-status":
                key = (args["lane_id"], args["epoch"], args["frame_seq"])
                if key not in self.completed:
                    self.pending[key] = time.monotonic()
                    if len(self.pending) > 256:
                        self.pending.pop(next(iter(self.pending)))
            elif cap != "surface-welcome":
                raise ProtocolError("unsupported client message")
        except (ValueError, TypeError, KeyError) as exc:
            self.errors.append(str(exc))
            self.errors[:] = self.errors[-32:]

    def _cold(self, data):
        try:
            if self.session_id is None or self.tree.needs_snapshot:
                return
            frame = self.media.feed(data, time.monotonic())
            if frame is None:
                return
            if frame.lane_id != 1 or frame.epoch != 0:
                raise ProtocolError("unnegotiated cold lane or epoch")
            start = time.perf_counter()
            changed = set()
            for block in decode_blocks(frame.data):
                assignment = self.assignments.get(block.surface_id)
                if assignment and (
                    assignment[0] != 1 or frame.frame_seq < assignment[1]
                ):
                    continue
                texture = self.cold_candidates.get(
                    block.surface_id
                ) or self.textures.get(block.surface_id)
                if texture is not None and texture.apply(block):
                    changed.add(block.surface_id)
            for sid in changed:
                texture = self.cold_candidates.get(sid) or self.textures[sid]
                if texture.ready:
                    self.textures[sid] = texture
                if texture.ready and self.on_texture:
                    self.on_texture(sid, texture)
            self.decode_seconds += time.perf_counter() - start
            key = (frame.lane_id, frame.epoch, frame.frame_seq)
            self.pending.pop(key, None)
            self.completed[key] = None
            if len(self.completed) > 4096:
                self.completed.popitem(last=False)
        except (ValueError, KeyError) as exc:
            self.errors.append(str(exc))
            self.errors[:] = self.errors[-32:]

    def _hot(self, data):
        if self.session_id is None or self.tree.needs_snapshot:
            return
        try:
            frame = self.media.feed(data, time.monotonic())
            if frame is None:
                return
            started = time.perf_counter()
            for sid, (size, pixels) in self.hot.decode(frame).items():
                if sid not in self.tree.surfaces:
                    continue
                assignment = self.assignments.get(sid)
                if assignment and (
                    assignment[0] != 0 or frame.frame_seq < assignment[1]
                ):
                    continue
                texture = Texture(sid, size)
                texture.pixels[:] = pixels
                texture.revisions = {
                    (x, y): 0
                    for y in range((size.h + 63) // 64)
                    for x in range((size.w + 63) // 64)
                }
                self.textures[sid] = texture
                if self.on_texture:
                    self.on_texture(sid, texture)
            self.decode_seconds += time.perf_counter() - started
            key = (0, frame.epoch, frame.frame_seq)
            self.pending.pop(key, None)
            self.completed[key] = None
            if len(self.completed) > 4096:
                self.completed.popitem(last=False)
        except (ValueError, RuntimeError) as exc:
            self.errors.append(str(exc))
            self.errors[:] = self.errors[-32:]
            if time.monotonic() - self._last_repair >= 0.1:
                self._send(
                    "lane-resync", {"lane_id": 0, "epoch": None, "frame_seq": None}
                )
                self._last_repair = time.monotonic()

    def hints(self, hints: list[ViewHint]):
        if self.session_id is None:
            return
        data = encode(
            "view-hint",
            {"seq": str(self.hint_seq), "hints": [asdict(h) for h in hints]},
            target=self.origin_name,
            message_id=uuid.uuid4().hex,
            session_id=self.session_id,
        )
        if self.hint_seq >= 2**32:
            raise ProtocolError("hint sequence exhausted; reconnect required")
        self.link.send_latest("hints", fragment(data, 2, 0, self.hint_seq))
        self.hint_seq += 1

    def repair(self, now=None):
        now = time.monotonic() if now is None else now
        if self.session_id is None or now - self._last_repair < 0.1:
            return
        for (lane, epoch, seq), created in list(self.pending.items()):
            if now - created < 0.15:
                continue
            missing = self.media.missing(lane, epoch, seq)
            if missing:
                self._send(
                    "lane-nack",
                    {
                        "lane_id": lane,
                        "epoch": epoch,
                        "frame_seq": seq,
                        "missing": list(missing),
                    },
                )
                self.pending[(lane, epoch, seq)] = now
            else:
                self._send(
                    "lane-resync", {"lane_id": lane, "epoch": epoch, "frame_seq": seq}
                )
                self.pending.pop((lane, epoch, seq), None)
            self._last_repair = now
            break

    def input(self, event):
        from .input import validate_event

        if not self.tree.accepts_input(event["surface_id"]):
            return
        validate_event(event, self.tree.surfaces[event["surface_id"]])
        args = {"event": event}
        if event["kind"] == "motion":
            args["seq"] = str(self.motion_seq)
            data = encode(
                "surface-input",
                args,
                target=self.origin_name,
                message_id=uuid.uuid4().hex,
                session_id=self.session_id,
            )
            self.link.send_latest("motion", data)
            self.motion_seq += 1
        else:
            self._send("surface-input", args)

    def request(self, cap, surface_id, **args):
        self._send(cap, {"surface_id": surface_id, **args})

    def ping(self):
        if self.session_id:
            self._send("surface-ping", {})

    def release_input(self):
        if self.session_id:
            self._send("surface-release", {})
