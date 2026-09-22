import asyncio
import socket
import time


class Desktop:
    def __init__(self):
        self.position = (100, 100)
        self.forwarding = False
        self.keys = set()
        self.buttons = set()
        self.events = []

    def start(self):
        pass

    def stop(self):
        self.release_all()

    def set_forwarding(self, enabled, anchor=None):
        self.forwarding = enabled
        if enabled:
            self.position = anchor

    def move(self, x, y):
        self.position = (x, y)

    def inject(self, kind, data):
        self.events.append((kind, data.copy()))
        if kind == "key":
            (self.keys.add if data["pressed"] else self.keys.discard)(data["key"])
        if kind == "button":
            (self.buttons.add if data["pressed"] else self.buttons.discard)(
                data["button"]
            )

    def apply_state(self, keys, buttons):
        self.keys, self.buttons = set(keys), set(buttons)

    def release_all(self):
        self.apply_state([], [])


class Clipboard:
    def __init__(self):
        self.text = ""
        self.previous = ""

    def poll(self):
        if self.text != self.previous:
            self.previous = self.text
            return self.text

    def set(self, text):
        self.text = self.previous = text


def port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(fn, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Timed out waiting for condition")
