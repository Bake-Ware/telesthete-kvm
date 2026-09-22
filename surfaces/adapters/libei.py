"""Small libei sender for KWin's native EIS RemoteDesktop endpoint."""

import ctypes as c
import ctypes.util
import select
import time


class EISInput:
    def __init__(self, bus):
        import dbus

        self.remote = dbus.Interface(
            bus.get_object("org.kde.KWin", "/org/kde/KWin/EIS/RemoteDesktop"),
            "org.kde.KWin.EIS.RemoteDesktop",
        )
        descriptor, self.cookie = self.remote.connectToEIS(3)
        self.lib = c.CDLL(ctypes.util.find_library("ei") or "libei.so.1")
        p, i, u, b, d = c.c_void_p, c.c_int, c.c_uint32, c.c_bool, c.c_double
        signatures = {
            "ei_new_sender": ([p], p),
            "ei_configure_name": ([p, c.c_char_p], None),
            "ei_setup_backend_fd": ([p, i], i),
            "ei_get_fd": ([p], i),
            "ei_dispatch": ([p], None),
            "ei_get_event": ([p], p),
            "ei_event_get_type": ([p], i),
            "ei_event_get_device": ([p], p),
            "ei_event_get_seat": ([p], p),
            "ei_event_unref": ([p], p),
            "ei_device_ref": ([p], p),
            "ei_device_unref": ([p], p),
            "ei_device_has_capability": ([p, i], b),
            "ei_device_start_emulating": ([p, u], None),
            "ei_device_frame": ([p, c.c_uint64], None),
            "ei_now": ([p], c.c_uint64),
            "ei_device_keyboard_key": ([p, u, b], None),
            "ei_device_pointer_motion_absolute": ([p, d, d], None),
            "ei_device_button_button": ([p, u, b], None),
            "ei_device_scroll_delta": ([p, d, d], None),
            "ei_device_scroll_discrete": ([p, c.c_int32, c.c_int32], None),
            "ei_unref": ([p], p),
            "ei_disconnect": ([p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.lib.ei_seat_bind_capabilities.argtypes = [p]
        self.lib.ei_seat_bind_capabilities.restype = None
        self.has_text = hasattr(self.lib, "ei_device_text_utf8")
        if self.has_text:
            self.lib.ei_device_text_utf8.argtypes = [p, c.c_char_p]
            self.lib.ei_device_text_utf8.restype = None
        self.context = self.lib.ei_new_sender(None)
        self.devices = set()
        self.lib.ei_configure_name(self.context, b"Telesthete Spatial Surfaces")
        if self.lib.ei_setup_backend_fd(self.context, descriptor.take()) != 0:
            self.stop()
            raise RuntimeError("libei backend initialization failed")
        self.fd = self.lib.ei_get_fd(self.context)
        deadline = time.monotonic() + 2

        def ready():
            return all(
                any(
                    self.lib.ei_device_has_capability(device, cap)
                    for device in self.devices
                )
                for cap in (2, 4)
            )

        while not ready() and time.monotonic() < deadline:
            self.dispatch()
            select.select([self.fd], [], [], 0.01)
        if not ready():
            self.stop()
            raise RuntimeError("KWin did not resume an emulated input device")

    def dispatch(self):
        self.lib.ei_dispatch(self.context)
        while event := self.lib.ei_get_event(self.context):
            try:
                kind = self.lib.ei_event_get_type(event)
                if kind == 2:
                    raise RuntimeError("KWin disconnected emulated input")
                if kind == 3:  # SEAT_ADDED
                    seat = self.lib.ei_event_get_seat(event)
                    self.lib.ei_seat_bind_capabilities(
                        seat,
                        c.c_int(2),
                        c.c_int(4),
                        c.c_int(16),
                        c.c_int(32),
                        c.c_int(64) if self.has_text else c.c_void_p(),
                        c.c_void_p(),
                    )
                elif kind == 8:  # DEVICE_RESUMED
                    device = self.lib.ei_event_get_device(event)
                    if device not in self.devices:
                        self.devices.add(self.lib.ei_device_ref(device))
                        self.lib.ei_device_start_emulating(device, 1)
                elif kind in (6, 7):  # REMOVED / PAUSED
                    device = self.lib.ei_event_get_device(event)
                    if device in self.devices:
                        self.devices.remove(device)
                        self.lib.ei_device_unref(device)
            finally:
                self.lib.ei_event_unref(event)

    def inject(self, event, origin_x=0, origin_y=0):
        self.dispatch()
        kind = event["kind"]
        cap = {"motion": 2, "key": 4, "axis": 16, "button": 32, "text-commit": 64}.get(
            kind
        )
        device = next(
            (d for d in self.devices if self.lib.ei_device_has_capability(d, cap or 0)),
            None,
        )
        if device is None:
            raise RuntimeError(f"KWin EIS has no active {kind} device")
        if kind == "motion":
            self.lib.ei_device_pointer_motion_absolute(
                device, origin_x + event["x"], origin_y + event["y"]
            )
        elif kind == "key":
            self.lib.ei_device_keyboard_key(device, event["keycode"], event["pressed"])
        elif kind == "button":
            self.lib.ei_device_button_button(device, event["button"], event["pressed"])
        elif kind == "axis":
            if event["discrete"]:
                self.lib.ei_device_scroll_discrete(
                    device, int(event["dx"] * 120), int(event["dy"] * 120)
                )
            else:
                self.lib.ei_device_scroll_delta(device, event["dx"], event["dy"])
        elif kind == "text-commit":
            self.lib.ei_device_text_utf8(device, event["text"].encode("utf-8"))
        self.lib.ei_device_frame(device, self.lib.ei_now(self.context))
        self.lib.ei_dispatch(self.context)

    def stop(self):
        if self.context:
            self.lib.ei_disconnect(self.context)
            for device in self.devices:
                self.lib.ei_device_unref(device)
            self.devices.clear()
            self.lib.ei_unref(self.context)
            self.context = None
            self.remote.disconnect(self.cookie)
