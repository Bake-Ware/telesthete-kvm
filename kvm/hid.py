"""Desktop input adapters. Importing this module does not connect to a display."""

import os
import sys
import threading
import time


class Desktop:
    def __init__(self, callback):
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
            raise RuntimeError(
                "An X11 desktop is required (DISPLAY is unset); native Wayland is unsupported"
            )
        if sys.platform == "win32":
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()
        from pynput import keyboard, mouse

        self.keyboard, self.mouse = keyboard, mouse
        self.kbd, self.pointer = keyboard.Controller(), mouse.Controller()
        self.callback = callback
        self.forwarding = False
        self.anchor = None
        self.listeners = []
        self.injected_keys = set()
        self.injected_buttons = set()
        self.lock = threading.RLock()
        self.grab_display = None
        self.hidden_cursor = None
        self.ignored = {}
        self.captured_keys = {}
        if sys.platform.startswith("linux"):
            from Xlib import display

            self.grab_display = display.Display()
            root = self.grab_display.screen().root
            pixmap = root.create_pixmap(1, 1, 1)
            gc = pixmap.create_gc(foreground=0)
            pixmap.fill_rectangle(gc, 0, 0, 1, 1)
            self.hidden_cursor = pixmap.create_cursor(
                pixmap, (0, 0, 0), (0, 0, 0), 0, 0
            )
            gc.free()
            pixmap.free()
        elif sys.platform != "win32":
            raise RuntimeError("Supported desktops are Windows and Linux X11")

    def key_token(self, key):
        if isinstance(key, self.keyboard.Key):
            return "special:" + key.name
        if getattr(key, "char", None):
            return "char:" + key.char
        if getattr(key, "vk", None) is not None:
            return "vk:" + str(key.vk)
        return None

    def decode_key(self, token):
        kind, value = token.split(":", 1)
        if kind == "special":
            return self.keyboard.Key[value]
        if kind == "char" and len(value) == 1:
            return self.keyboard.KeyCode.from_char(value)
        if kind == "vk" and 0 <= int(value) <= 65535:
            return self.keyboard.KeyCode.from_vk(int(value))
        raise ValueError("Invalid key token")

    def start(self):
        kargs, margs = {}, {}
        keyboard_listener, mouse_listener = self.keyboard.Listener, self.mouse.Listener
        if sys.platform.startswith("linux"):
            from Xlib import X

            desktop = self

            class XKeyboardListener(self.keyboard.Listener):
                def __init__(listener, *args, **kwargs):
                    listener.physical_keys = {}
                    super().__init__(*args, **kwargs)

                def _event_to_key(listener, display, event):
                    key = super()._event_to_key(display, event)
                    if event.type == X.KeyPress:
                        return listener.physical_keys.setdefault(event.detail, key)
                    if event.type == X.KeyRelease:
                        held = listener.physical_keys.get(event.detail, key)
                        ignore = ("key", desktop.key_token(held), False)
                        if desktop.ignored.get(ignore, 0) > time.monotonic():
                            return held
                        return listener.physical_keys.pop(event.detail, key)
                    return key

            keyboard_listener = XKeyboardListener
        if sys.platform == "win32":
            import ctypes

            desktop = self

            class KeyboardListener(self.keyboard.Listener):
                def _handler(self, code, msg, lpdata):
                    super()._handler(code, msg, lpdata)
                    if code != 0:
                        return
                    data = ctypes.cast(lpdata, self._LPKBDLLHOOKSTRUCT).contents
                    if desktop.forwarding and not data.flags & 0x10:
                        self.suppress_event()

            class MouseListener(self.mouse.Listener):
                def _handler(self, code, msg, lpdata):
                    super()._handler(code, msg, lpdata)
                    if code != 0:
                        return
                    data = ctypes.cast(lpdata, self._LPMSLLHOOKSTRUCT).contents
                    if desktop.forwarding and not data.flags & 0x01:
                        self.suppress_event()

            keyboard_listener, mouse_listener = KeyboardListener, MouseListener
            kargs["win32_event_filter"] = lambda msg, data: not bool(data.flags & 0x10)
            margs["win32_event_filter"] = lambda msg, data: not bool(data.flags & 0x01)

        def key(key, pressed):
            token = self.key_token(key)
            identity = getattr(key, "vk", None)
            if identity is None and isinstance(key, self.keyboard.Key):
                identity = key.value.vk
            with self.lock:
                if identity is not None:
                    if pressed:
                        token = self.captured_keys.setdefault(identity, token)
                    else:
                        token = self.captured_keys.get(identity, token)
                if token and self.is_ignored(("key", token, pressed)):
                    return
                if not pressed:
                    self.captured_keys.pop(identity, None)
            if token:
                self.callback("key", {"key": token, "pressed": pressed})

        def click(x, y, button, pressed):
            if self.is_ignored(("button", button.name, pressed)):
                return
            self.callback("button", {"button": button.name, "pressed": pressed})

        def scroll(x, y, dx, dy):
            self.callback("scroll", {"dx": int(dx), "dy": int(dy)})

        self.listeners = [
            keyboard_listener(
                on_press=lambda k: key(k, True),
                on_release=lambda k: key(k, False),
                **kargs,
            ),
            mouse_listener(
                on_move=self._move, on_click=click, on_scroll=scroll, **margs
            ),
        ]
        for listener in self.listeners:
            listener.start()
            listener.wait()

    def _move(self, x, y):
        with self.lock:
            if self.forwarding and self.anchor:
                ax, ay = self.anchor
                dx, dy = int(x - ax), int(y - ay)
                if dx or dy:
                    self.pointer.position = self.anchor
                    self.callback("delta", {"dx": dx, "dy": dy})
            else:
                self.callback("move", {"x": int(x), "y": int(y)})

    @property
    def position(self):
        return tuple(map(int, self.pointer.position))

    def set_forwarding(self, enabled, anchor=None):
        with self.lock:
            if enabled == self.forwarding:
                return
            if self.grab_display:
                from Xlib import X

                root = self.grab_display.screen().root
                if enabled:
                    result = root.grab_keyboard(
                        False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime
                    )
                    if result != X.GrabSuccess:
                        raise RuntimeError("Could not grab keyboard")
                    result = root.grab_pointer(
                        False,
                        X.PointerMotionMask | X.ButtonPressMask | X.ButtonReleaseMask,
                        X.GrabModeAsync,
                        X.GrabModeAsync,
                        root,
                        self.hidden_cursor,
                        X.CurrentTime,
                    )
                    if result != X.GrabSuccess:
                        self.grab_display.ungrab_keyboard(X.CurrentTime)
                        self.grab_display.sync()
                        raise RuntimeError("Could not grab pointer")
                else:
                    self.grab_display.ungrab_keyboard(X.CurrentTime)
                    self.grab_display.ungrab_pointer(X.CurrentTime)
                self.grab_display.sync()
            self.forwarding = enabled
            self.anchor = anchor if enabled else None
            if enabled:
                self.pointer.position = anchor

    def is_ignored(self, event):
        with self.lock:
            expiry = self.ignored.pop(event, 0)
            return expiry > time.monotonic()

    def release_local(self, keys, buttons):
        # Clear local applications' held state before grabbing the source. The
        # physical held state remains in KVM and is reconciled on the receiver.
        with self.lock:
            for token in keys:
                self.ignored[("key", token, False)] = time.monotonic() + 0.5
                self.kbd.release(self.decode_key(token))
            for button in buttons:
                self.ignored[("button", button, False)] = time.monotonic() + 0.5
                self.pointer.release(self.mouse.Button[button])

    def check(self):
        if any(not listener.is_alive() for listener in self.listeners):
            raise RuntimeError("Desktop input listener stopped")
        with self.lock:
            if self.grab_display:
                while self.grab_display.pending_events():
                    self.grab_display.next_event()
            now = time.monotonic()
            self.ignored = {
                event: expiry for event, expiry in self.ignored.items() if expiry > now
            }

    def move(self, x, y):
        if self.position != (x, y):
            self.pointer.position = (x, y)

    def inject(self, kind, data):
        if kind == "key":
            key = self.decode_key(data["key"])
            if data["pressed"]:
                self.kbd.press(key)
                self.injected_keys.add(data["key"])
            else:
                self.kbd.release(key)
                self.injected_keys.discard(data["key"])
        elif kind == "button":
            button = self.mouse.Button[data["button"]]
            if data["pressed"]:
                self.pointer.press(button)
                self.injected_buttons.add(data["button"])
            else:
                self.pointer.release(button)
                self.injected_buttons.discard(data["button"])
        elif kind == "scroll":
            self.pointer.scroll(data["dx"], data["dy"])
        elif kind == "move":
            self.move(data["x"], data["y"])

    def apply_state(self, keys, buttons):
        for token in self.injected_keys - set(keys):
            self.kbd.release(self.decode_key(token))
        for token in set(keys) - self.injected_keys:
            self.kbd.press(self.decode_key(token))
        for button in self.injected_buttons - set(buttons):
            self.pointer.release(self.mouse.Button[button])
        for button in set(buttons) - self.injected_buttons:
            self.pointer.press(self.mouse.Button[button])
        self.injected_keys = set(keys)
        self.injected_buttons = set(buttons)

    def release_all(self):
        self.apply_state([], [])

    def stop(self):
        self.set_forwarding(False)
        self.release_all()
        for listener in self.listeners:
            listener.stop()
        for listener in self.listeners:
            listener.join(timeout=2)
        if self.hidden_cursor:
            self.hidden_cursor.free()
        if self.grab_display:
            self.grab_display.close()
