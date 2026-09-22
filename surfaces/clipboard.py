"""Opt-in, bounded text clipboard exchange over the reliable clipboard Channel."""

import base64
import binascii
import uuid

from .model import ProtocolError
from .protocol import decode, encode

MIME = "text/plain;charset=utf-8"
MAX_TEXT_BYTES = 1024 * 1024


class ClipboardFlow:
    def __init__(self, session, peer_name, read_text, write_text):
        self.session = session
        self.peer_name = peer_name
        self.read_text = read_text
        self.write_text = write_text
        self.enabled = False
        self.local_text = None
        self.offered_id = None
        self.offered_text = None
        self.requested_id = None
        self.requested_at_text = None
        session.link.on_clipboard(self._data)

    def _send(self, cap, args, *, bulk=False):
        self.session.link.send_control(
            encode(
                cap,
                args,
                target=self.peer_name,
                message_id=uuid.uuid4().hex,
                session_id=self.session.session_id,
            ),
            clipboard=bulk,
        )

    def poll(self):
        if not self.enabled or self.session.session_id is None:
            return
        text = self.read_text()
        if not isinstance(text, str) or text == self.local_text:
            return
        payload = text.encode("utf-8")
        if len(payload) > MAX_TEXT_BYTES:
            self.local_text = text
            return
        offer_id = uuid.uuid4().hex
        self._send(
            "clipboard-offer",
            {"offer_id": offer_id, "mime_types": [MIME], "byte_length": len(payload)},
        )
        self.local_text = self.offered_text = text
        self.offered_id = offer_id

    def handle(self, cap, args):
        if not self.enabled:
            raise ProtocolError("clipboard was not negotiated")
        offer_id = args.get("offer_id")
        if (
            not isinstance(offer_id, str)
            or len(offer_id) != 32
            or any(digit not in "0123456789abcdef" for digit in offer_id)
        ):
            raise ProtocolError("invalid clipboard offer ID")
        if cap == "clipboard-offer":
            length = args.get("byte_length")
            if (
                type(length) is not int
                or not 0 <= length <= MAX_TEXT_BYTES
                or args.get("mime_types") != [MIME]
            ):
                raise ProtocolError("unsupported clipboard offer")
            self.requested_id = offer_id
            self.requested_at_text = self.read_text()
            self._send("clipboard-request", {"offer_id": offer_id, "mime_type": MIME})
        elif cap == "clipboard-request":
            if (
                offer_id != self.offered_id
                or args.get("mime_type") != MIME
                or self.offered_text is None
            ):
                raise ProtocolError("stale clipboard request")
            self._send(
                "clipboard-data",
                {
                    "offer_id": offer_id,
                    "mime_type": MIME,
                    "data_b64": base64.b64encode(
                        self.offered_text.encode("utf-8")
                    ).decode("ascii"),
                },
                bulk=True,
            )
        else:
            raise ProtocolError("invalid clipboard control message")

    def _data(self, wire):
        try:
            body = decode(
                wire, target=self.session.name, session_id=self.session.session_id
            )
            if body["cap"] != "clipboard-data" or not self.enabled:
                raise ProtocolError("unexpected clipboard data")
            args = body["args"]
            data_b64 = args.get("data_b64")
            if not isinstance(data_b64, str) or len(data_b64) > 1398104:
                raise ProtocolError("clipboard data exceeds limit")
            try:
                raw = base64.b64decode(data_b64, validate=True)
                text = raw.decode("utf-8")
            except (binascii.Error, UnicodeError) as exc:
                raise ProtocolError("invalid clipboard text encoding") from exc
            if (
                args.get("offer_id") != self.requested_id
                or args.get("mime_type") != MIME
                or len(raw) > MAX_TEXT_BYTES
            ):
                raise ProtocolError("invalid clipboard data")
            self.requested_id = None
            # A fresh local copy during transfer wins over a pending remote offer.
            if self.read_text() != self.requested_at_text:
                return
            self.write_text(text)
            self.local_text = text
        except (ValueError, TypeError, KeyError, BufferError) as exc:
            self.session.errors.append(str(exc))
            self.session.errors[:] = self.session.errors[-32:]


class QtClipboard:
    def __init__(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.clipboard = self.app.clipboard()

    def read(self):
        return self.clipboard.text()

    def write(self, text):
        self.clipboard.setText(text)

    def pump(self):
        self.app.processEvents()
