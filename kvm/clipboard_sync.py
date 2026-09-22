"""Text clipboard access; network framing and retries live in the application."""

import hashlib
import logging
import threading

log = logging.getLogger(__name__)
MAX_CLIPBOARD = 1024 * 1024


class Clipboard:
    def __init__(self):
        import pyperclip

        self.backend = pyperclip
        self.lock = threading.Lock()
        self.last_hash = None
        self.failed = False

    def poll(self):
        with self.lock:
            return self._poll()

    def _poll(self):
        try:
            text = self.backend.paste()
            data = text.encode("utf-8")
            if len(data) > MAX_CLIPBOARD:
                return None
            digest = hashlib.sha256(data).digest()
            if digest == self.last_hash:
                return None
            initial = self.last_hash is None
            self.last_hash = digest
            self.failed = False
            return None if initial else text
        except Exception as exc:
            if not self.failed:
                log.warning("Clipboard unavailable: %s", exc)
                self.failed = True
            return None

    def set(self, text):
        with self.lock:
            self._set(text)

    def _set(self, text):
        data = text.encode("utf-8")
        if len(data) > MAX_CLIPBOARD:
            raise ValueError("Clipboard exceeds 1 MiB")
        self.backend.copy(text)
        self.last_hash = hashlib.sha256(data).digest()
