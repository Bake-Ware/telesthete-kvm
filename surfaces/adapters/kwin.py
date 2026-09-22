"""KWin script tree, native screencast bridge, PipeWire capture and libei input."""

import json
import os
import select
import subprocess
import tempfile
from pathlib import Path

from ..model import Flags, Rect, Role, Size, Surface
from .pipewire import PipeWireCapture
from .windows import CaptureFrame


class KWinOrigin:
    input_fidelity = "focused-only"

    def __init__(self, bridge: str):
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        DBusGMainLoop(set_as_default=True)
        self.dbus, self.GLib = dbus, GLib
        self.bus = dbus.SessionBus()
        self.bridge = str(Path(bridge).resolve())
        self.rows = []
        self.ids, self.uuids = {}, {}
        self._next_id = 1
        self._captures = {}
        self._restore_pending = set()
        self._input = None
        owner = self

        class TreeService(dbus.service.Object):
            @dbus.service.method(
                "org.bake.SpatialTree", in_signature="s", out_signature=""
            )
            def Update(self, payload):
                rows = json.loads(str(payload))
                if isinstance(rows, list) and len(rows) <= 1024:
                    owner.rows = rows

        self.service_name = f"org.bake.SpatialTree.p{os.getpid()}"
        self.bus_name = dbus.service.BusName(
            self.service_name, bus=self.bus, do_not_queue=True
        )
        self.service = TreeService(self.bus_name, "/Tree")
        self.scripting = dbus.Interface(
            self.bus.get_object("org.kde.KWin", "/Scripting"), "org.kde.kwin.Scripting"
        )
        self._temp = tempfile.TemporaryDirectory(prefix="telesthete-kwin-")
        self.script_name = f"telesthete-spatial-{os.getpid()}"
        script = Path(self._temp.name) / "tree.js"
        template = Path(__file__).with_name("kwin_tree.js").read_text()
        script.write_text(template.replace("__SERVICE__", self.service_name))
        sid = self.scripting.loadScript(str(script), self.script_name, signature="ss")
        if sid < 0:
            raise RuntimeError("KWin refused surface tree script")
        dbus.Interface(
            self.bus.get_object("org.kde.KWin", f"/Scripting/Script{sid}"),
            "org.kde.kwin.Script",
        ).run()

    def poll(self):
        context = self.GLib.MainContext.default()
        for _ in range(100):
            if not context.pending():
                break
            context.iteration(False)
        if self._input:
            self._input.dispatch()

    def snapshot(self) -> list[Surface]:
        self.poll()
        live = {row["uuid"] for row in self.rows}
        for uuid in set(self.ids) - live:
            self.uuids.pop(self.ids.pop(uuid), None)
            self._restore_pending.discard(uuid)
        for uuid in live:
            if uuid not in self.ids:
                self.ids[uuid] = self._next_id
                self.uuids[self._next_id] = uuid
                self._next_id += 1
        by_uuid = {
            row["uuid"]: row
            for row in self.rows
            if row["client"]["w"] and row["client"]["h"]
        }
        surfaces = []
        for z, row in enumerate(self.rows):
            uuid = row["uuid"]
            if not row.get("minimized"):
                self._restore_pending.discard(uuid)
            elif self.ids[uuid] in self._captures and uuid not in self._restore_pending:
                self._restore_pending.add(uuid)
                self._command(self.ids[uuid], "w.minimized = false;")
            c = row["client"]
            if not c["w"] or not c["h"]:
                continue
            parent_row = by_uuid.get(row["parent"])
            parent = self.ids.get(row["parent"]) if parent_row else None
            role = Role(row["role"]) if parent else Role.TOPLEVEL
            if parent and role == Role.TOPLEVEL:
                role = Role.DIALOG
            anchor = Rect(
                c["x"] - (parent_row["client"]["x"] if parent_row else 0),
                c["y"] - (parent_row["client"]["y"] if parent_row else 0),
                c["w"],
                c["h"],
            )
            flags = (
                (Flags.FOCUSED if row["focused"] else 0)
                | (Flags.DECORATED if row["decorated"] else 0)
                | (Flags.URGENT if row["urgent"] else 0)
            )
            title = row["title"].encode("utf-8")[:256].decode("utf-8", errors="ignore")
            surfaces.append(
                Surface(
                    self.ids[row["uuid"]],
                    Size(c["w"], c["h"]),
                    str(row["app_id"]),
                    title,
                    role=role,
                    parent_id=parent,
                    anchor=anchor,
                    modal=row["modal"],
                    flags=Flags(flags),
                    z_hint=z,
                )
            )
        return surfaces

    def start_capture(self, surface_id: int):
        if surface_id in self._captures:
            return
        process = subprocess.Popen(
            [self.bridge, self.uuids[surface_id]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if not select.select([process.stdout], [], [], 5)[0]:
            process.terminate()
            process.wait(timeout=5)
            raise TimeoutError("KWin screencast creation timed out")
        line = process.stdout.readline().strip()
        if not line.isdecimal():
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError(f"KWin screencast failed: {process.stderr.read()}")
        try:
            capture = PipeWireCapture(int(line))
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            raise
        self._captures[surface_id] = (process, capture)

    def frame(self, surface_id: int):
        frame = self._captures[surface_id][1].frame()
        if frame is None:
            return None
        row = next(
            (r for r in self.rows if r["uuid"] == self.uuids.get(surface_id)), None
        )
        if row is None:
            return None
        c, f = row["client"], row["frame"]
        if frame.size.w != f["w"] or frame.size.h != f["h"]:
            # Geometry change and capture buffer can arrive in either order.
            return None
        x, y = c["x"] - f["x"], c["y"] - f["y"]
        raw = b"".join(
            frame.bgra[
                ((y + r) * frame.size.w + x) * 4 : ((y + r) * frame.size.w + x + c["w"])
                * 4
            ]
            for r in range(c["h"])
        )
        return CaptureFrame(Size(c["w"], c["h"]), raw)

    def _command(self, surface_id: int, action: str):
        uuid = json.dumps(self.uuids[surface_id])
        script = Path(self._temp.name) / "command.js"
        script.write_text(
            f"const w = workspace.windowList().find(w => String(w.internalId) === {uuid}); if (w) {{ {action} }}"
        )
        name = self.script_name + "-command"
        sid = self.scripting.loadScript(str(script), name, signature="ss")
        if sid < 0:
            raise RuntimeError("KWin rejected command script")
        try:
            self.dbus.Interface(
                self.bus.get_object("org.kde.KWin", f"/Scripting/Script{sid}"),
                "org.kde.kwin.Script",
            ).run()
        finally:
            self.scripting.unloadScript(name)

    def focus(self, surface_id: int):
        self._command(surface_id, "w.minimized = false; workspace.activeWindow = w;")
        return True  # Actual focus is reported asynchronously by the tree.

    def resize(self, surface_id: int, size: Size):
        self._command(
            surface_id,
            f"const f=w.frameGeometry,c=w.clientGeometry; w.frameGeometry={{x:f.x,y:f.y,width:{size.w}+f.width-c.width,height:{size.h}+f.height-c.height}};",
        )

    def close_window(self, surface_id: int):
        self._command(surface_id, "w.closeWindow();")

    def inject(self, event):
        from ..input import validate_event
        from .libei import EISInput

        surface = next(
            (s for s in self.snapshot() if s.surface_id == event["surface_id"]), None
        )
        if surface is None:
            raise ValueError("surface no longer exists")
        validate_event(event, surface)
        if event["kind"] == "button" and "x" in event:
            self.inject(
                {
                    "kind": "motion",
                    "surface_id": event["surface_id"],
                    "x": event["x"],
                    "y": event["y"],
                }
            )
        if self._input is None:
            self._input = EISInput(self.bus)
        if not surface.flags & Flags.FOCUSED:
            self.focus(surface.surface_id)
        row = next(r for r in self.rows if self.ids[r["uuid"]] == surface.surface_id)
        self._input.inject(event, row["client"]["x"], row["client"]["y"])

    def stop_capture(self, surface_id):
        capture = self._captures.pop(surface_id, None)
        if capture:
            process, pipeline = capture
            pipeline.stop()
            process.terminate()
            process.wait(timeout=5)

    def release_input(self):
        if self._input:
            self._input.stop()
            self._input = None

    def stop(self):
        self.release_input()
        for process, capture in self._captures.values():
            capture.stop()
            process.terminate()
            process.wait(timeout=5)
        self._captures.clear()
        self.scripting.unloadScript(self.script_name)
        self.service.remove_from_connection()
        self._temp.cleanup()
