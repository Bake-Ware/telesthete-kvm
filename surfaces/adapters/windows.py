"""Windows origin: HWND tree, WGC frames, foreground-only SendInput.

Native libraries are loaded in constructors so importing the protocol is portable.
"""

import ctypes
import sys
import threading
from dataclasses import dataclass

from ..model import Flags, Rect, Role, Size, Surface


@dataclass(frozen=True)
class CaptureFrame:
    size: Size
    bgra: bytes


class WindowsOrigin:
    input_fidelity = "focused-only"

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("WindowsOrigin requires Windows")
        from ctypes import wintypes as w

        self.w = w
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.dwm.DwmGetWindowAttribute.argtypes = [
            w.HWND,
            w.DWORD,
            ctypes.c_void_p,
            w.DWORD,
        ]
        self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        u = self.user
        signatures = {
            "GetWindow": ([w.HWND, w.UINT], w.HWND),
            "GetForegroundWindow": ([], w.HWND),
            "GetWindowTextLengthW": ([w.HWND], ctypes.c_int),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetClassNameW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL),
            "IsWindowEnabled": ([w.HWND], w.BOOL),
            "SetForegroundWindow": ([w.HWND], w.BOOL),
            "ShowWindow": ([w.HWND, ctypes.c_int], w.BOOL),
            "SetWindowPos": (
                [
                    w.HWND,
                    w.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    w.UINT,
                ],
                w.BOOL,
            ),
            "PostMessageW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(u, name)
            function.argtypes, function.restype = args, result
        try:
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass
        self._ids = {}
        self._handles = {}
        self._next_id = 1
        self._captures = {}
        self._frames = {}
        self._lock = threading.Lock()
        self._held = set()
        self._buttons = set()

    def snapshot(self) -> list[Surface]:
        w, u = self.w, self.user
        handles = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        @callback_type
        def visit(hwnd, _):
            if u.IsWindowVisible(hwnd):
                # WGC stops supplying content for minimized HWNDs. A selected
                # window stays restored for the lifetime of its capture.
                if u.IsIconic(hwnd) and self._ids.get(hwnd) in self._captures:
                    u.ShowWindow(hwnd, 9)  # SW_RESTORE
                if u.IsIconic(hwnd):
                    return True
                rect = w.RECT()
                if (
                    u.GetClientRect(hwnd, ctypes.byref(rect))
                    and rect.right
                    and rect.bottom
                ):
                    handles.append(hwnd)
            return True

        u.EnumWindows.argtypes = [callback_type, w.LPARAM]
        u.EnumWindows(visit, 0)
        for hwnd in set(self._ids) - set(handles):
            sid = self._ids.pop(hwnd)
            self._handles.pop(sid, None)
        for hwnd in handles:
            if hwnd not in self._ids:
                self._ids[hwnd] = self._next_id
                self._handles[self._next_id] = hwnd
                self._next_id += 1
        surfaces = []
        for z, hwnd in enumerate(reversed(handles)):
            sid = self._ids[hwnd]
            length = u.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, title, length + 1)
            name = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, name, 256)
            rect = w.RECT()
            u.GetClientRect(hwnd, ctypes.byref(rect))
            owner = u.GetWindow(hwnd, 4)  # GW_OWNER
            parent = self._ids.get(owner)
            point = w.POINT(0, 0)
            u.ClientToScreen(hwnd, ctypes.byref(point))
            if parent is not None:
                parent_point = w.POINT(0, 0)
                u.ClientToScreen(owner, ctypes.byref(parent_point))
                point.x -= parent_point.x
                point.y -= parent_point.y
            role = (
                Role.TOPLEVEL
                if parent is None
                else (Role.TOOLTIP if name.value == "tooltips_class32" else Role.DIALOG)
            )
            text = title.value.encode("utf-8")[:256].decode("utf-8", errors="ignore")
            flags = Flags.DECORATED
            if u.GetForegroundWindow() == hwnd:
                flags |= Flags.FOCUSED
            surfaces.append(
                Surface(
                    sid,
                    Size(rect.right, rect.bottom),
                    name.value,
                    text,
                    role=role,
                    parent_id=parent,
                    anchor=Rect(point.x, point.y, rect.right, rect.bottom),
                    modal=bool(parent and not u.IsWindowEnabled(owner)),
                    flags=flags,
                    z_hint=z,
                )
            )
        return surfaces

    def start_capture(self, surface_id: int):
        from windows_capture import WindowsCapture

        if surface_id in self._captures:
            return
        hwnd = self._handles[surface_id]
        capture = WindowsCapture(
            window_hwnd=hwnd,
            cursor_capture=False,
            draw_border=True,
            minimum_update_interval=16,
        )

        @capture.event
        def on_frame_arrived(frame, control):
            # Copy while the native mapped frame is alive; never store its view.
            # WGC includes the frame border. Crop to the client content origin.
            rect, point = self.w.RECT(), self.w.POINT(0, 0)
            self.user.GetWindowRect(hwnd, ctypes.byref(rect))
            # WGC excludes invisible resize borders; GetWindowRect includes them.
            # Align against DWM's visible frame bounds before taking the client crop.
            self.dwm.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
            )
            self.user.ClientToScreen(hwnd, ctypes.byref(point))
            client = self.w.RECT()
            self.user.GetClientRect(hwnd, ctypes.byref(client))
            x, y = max(0, point.x - rect.left), max(0, point.y - rect.top)
            image = frame.frame_buffer
            cropped = image[
                y : min(frame.height, y + client.bottom),
                x : min(frame.width, x + client.right),
            ]
            h, width = cropped.shape[:2]
            if width and h:
                with self._lock:
                    self._frames[surface_id] = CaptureFrame(
                        Size(width, h), cropped.tobytes()
                    )

        @capture.event
        def on_closed():
            with self._lock:
                self._frames.pop(surface_id, None)

        control = capture.start_free_threaded()
        self._captures[surface_id] = (capture, control)

    def frame(self, surface_id: int) -> CaptureFrame | None:
        with self._lock:
            return self._frames.pop(surface_id, None)

    def focus(self, surface_id: int) -> bool:
        hwnd = self._handles[surface_id]
        self.user.ShowWindow(hwnd, 9)
        self.user.SetForegroundWindow(hwnd)
        return self.user.GetForegroundWindow() == hwnd

    def resize(self, surface_id: int, size: Size):
        hwnd = self._handles[surface_id]
        window, client = self.w.RECT(), self.w.RECT()
        self.user.GetWindowRect(hwnd, ctypes.byref(window))
        self.user.GetClientRect(hwnd, ctypes.byref(client))
        width = size.w + window.right - window.left - client.right
        height = size.h + window.bottom - window.top - client.bottom
        return bool(self.user.SetWindowPos(hwnd, None, 0, 0, width, height, 0x16))

    def close_window(self, surface_id: int):
        self.user.PostMessageW(self._handles[surface_id], 0x10, 0, 0)

    def inject(self, event):
        from ..input import validate_event

        surface = next(
            (s for s in self.snapshot() if s.surface_id == event["surface_id"]), None
        )
        if surface is None:
            raise ValueError("input surface no longer exists")
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
        hwnd = self._handles[surface.surface_id]
        if self.user.GetForegroundWindow() != hwnd and not self.focus(
            surface.surface_id
        ):
            raise RuntimeError("Windows refused foreground focus")
        kind = event["kind"]
        if kind == "motion":
            point = self.w.POINT(int(event["x"]), int(event["y"]))
            self.user.ClientToScreen(hwnd, ctypes.byref(point))
            self.user.SetCursorPos(point.x, point.y)
        elif kind == "key":
            code = event["keycode"]
            extended = {
                96: 0x1C,
                97: 0x1D,
                98: 0x35,
                100: 0x38,
                102: 0x47,
                103: 0x48,
                104: 0x49,
                105: 0x4B,
                106: 0x4D,
                107: 0x4F,
                108: 0x50,
                109: 0x51,
                110: 0x52,
                111: 0x53,
                125: 0x5B,
                126: 0x5C,
                127: 0x5D,
            }
            if code in extended:
                scan, flags = extended[code], 0x9
            elif 1 <= code <= 83 or code in (86, 87, 88):
                scan, flags = code, 0x8
            else:
                raise ValueError("unsupported evdev keycode")
            self._send_native(key=(scan, flags | (0 if event["pressed"] else 2)))
            if event["pressed"]:
                self._held.add((scan, flags))
            else:
                self._held.discard((scan, flags))
        elif kind == "button":
            choices = {272: (2, 4), 273: (8, 16), 274: (32, 64)}
            if event["button"] not in choices:
                raise ValueError("unsupported pointer button")
            down, up = choices[event["button"]]
            self._send_native(mouse=(down if event["pressed"] else up, 0))
            if event["pressed"]:
                self._buttons.add(up)
            else:
                self._buttons.discard(up)
        elif kind == "axis":
            scale = 120 if event["discrete"] else 1
            if event["dy"]:
                self._send_native(mouse=(0x800, -int(event["dy"] * scale)))
            if event["dx"]:
                self._send_native(mouse=(0x1000, int(event["dx"] * scale)))
        elif kind == "text-commit":
            raw = event["text"].encode("utf-16-le")
            for offset in range(0, len(raw), 2):
                code = int.from_bytes(raw[offset : offset + 2], "little")
                self._send_native(key=(code, 4))
                self._send_native(key=(code, 6))
        else:
            raise ValueError("touch injection not available in Windows prototype")

    def _send_native(self, *, key=None, mouse=None):
        w = self.w

        class Mouse(ctypes.Structure):
            _fields_ = [
                ("dx", w.LONG),
                ("dy", w.LONG),
                ("data", w.DWORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("extra", ctypes.c_void_p),
            ]

        class Key(ctypes.Structure):
            _fields_ = [
                ("vk", w.WORD),
                ("scan", w.WORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("extra", ctypes.c_void_p),
            ]

        class Union(ctypes.Union):
            _fields_ = [("mouse", Mouse), ("key", Key)]

        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("value", Union)]

        value = Input()
        if key:
            value.type = 1
            value.value.key = Key(0, key[0], key[1], 0, None)
        else:
            value.type = 0
            value.value.mouse = Mouse(0, 0, mouse[1] & 0xFFFFFFFF, mouse[0], 0, None)
        self.user.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        if self.user.SendInput(1, ctypes.byref(value), ctypes.sizeof(Input)) != 1:
            raise OSError(ctypes.get_last_error(), "SendInput failed")

    def stop_capture(self, surface_id):
        capture = self._captures.pop(surface_id, None)
        if capture:
            capture[1].stop()
        with self._lock:
            self._frames.pop(surface_id, None)

    def release_input(self):
        for scan, flags in list(self._held):
            self._send_native(key=(scan, flags | 2))
        self._held.clear()
        for flag in list(self._buttons):
            self._send_native(mouse=(flag, 0))
        self._buttons.clear()

    def stop(self):
        self.release_input()
        for _, control in self._captures.values():
            control.stop()
        self._captures.clear()
        with self._lock:
            self._frames.clear()
