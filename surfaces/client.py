"""Minimal Qt flat shell; placement stays entirely on the client."""

import sys

from .model import Role, Size, ViewHint, Visibility


class FlatClient:
    def __init__(self, session, *, placement_offset=(0, 0)):
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtWidgets import QApplication, QWidget

        self.app = QApplication.instance() or QApplication([])
        self.session = session
        self.windows = {}
        owner = self

        class Window(QWidget):
            def __init__(self, sid):
                surface = session.tree.surfaces[sid]
                parent = owner.windows.get(surface.parent_id)
                super().__init__(parent)
                if surface.role in (Role.POPUP, Role.TOOLTIP):
                    self.setWindowFlags(
                        Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                    )
                    if surface.role == Role.TOOLTIP:
                        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
                elif surface.role == Role.DIALOG:
                    self.setWindowFlags(Qt.WindowType.Dialog)
                    if surface.modal:
                        self.setWindowModality(Qt.WindowModality.WindowModal)
                self.sid = sid
                self.image = None
                self.held_buttons = set()
                self.resize_timer = QTimer(self)
                self.resize_timer.setSingleShot(True)
                self.resize_timer.setInterval(250)
                self.resize_timer.timeout.connect(self.request_resize)
                self.setMouseTracking(True)
                self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
                surface = session.tree.surfaces[sid]
                self.setWindowTitle(f"{surface.title} — {session.origin_name}")
                self.resize(surface.size.w, surface.size.h)
                self.show()
                if surface.parent_id is None and placement_offset != (0, 0):
                    self.move(
                        self.x() + placement_offset[0], self.y() + placement_offset[1]
                    )

            def resizeEvent(self, event):
                surface = session.tree.surfaces.get(self.sid)
                if surface and surface.role == Role.TOPLEVEL and self.image is not None:
                    self.resize_timer.start()
                super().resizeEvent(event)

            def request_resize(self):
                surface = session.tree.surfaces.get(self.sid)
                size = Size(self.width(), self.height())
                if surface and surface.size != size:
                    session.request("resize-request", self.sid, w=size.w, h=size.h)

            def paintEvent(self, event):
                painter = QPainter(self)
                painter.fillRect(self.rect(), Qt.GlobalColor.black)
                if self.image:
                    target = self.image.size().scaled(
                        self.size(), Qt.AspectRatioMode.KeepAspectRatio
                    )
                    x, y = (
                        (self.width() - target.width()) // 2,
                        (self.height() - target.height()) // 2,
                    )
                    painter.drawImage(x, y, self.image.scaled(target))

            def position(self, event):
                surface = session.tree.surfaces.get(self.sid)
                if surface is None:
                    return None
                scale = min(
                    self.width() / surface.size.w, self.height() / surface.size.h
                )
                x = (
                    event.position().x() - (self.width() - surface.size.w * scale) / 2
                ) / scale
                y = (
                    event.position().y() - (self.height() - surface.size.h * scale) / 2
                ) / scale
                if not 0 <= x < surface.size.w or not 0 <= y < surface.size.h:
                    return None
                return x, y

            def send(self, kind, **fields):
                session.input({"kind": kind, "surface_id": self.sid, **fields})

            def mouseMoveEvent(self, event):
                if point := self.position(event):
                    self.send("motion", x=point[0], y=point[1])

            def button(self, event, pressed):
                codes = {
                    Qt.MouseButton.LeftButton: 272,
                    Qt.MouseButton.RightButton: 273,
                    Qt.MouseButton.MiddleButton: 274,
                }
                code = codes.get(event.button())
                point = self.position(event)
                if code is not None and point:
                    self.mouseMoveEvent(event)
                    point = self.position(event)
                    self.send(
                        "button",
                        button=codes[event.button()],
                        pressed=pressed,
                        x=point[0],
                        y=point[1],
                    )
                    if pressed:
                        self.held_buttons.add(code)
                    else:
                        self.held_buttons.discard(code)
                elif not pressed and code in self.held_buttons:
                    self.send("button", button=code, pressed=False)
                    self.held_buttons.discard(code)

            def mousePressEvent(self, event):
                self.button(event, True)

            def mouseReleaseEvent(self, event):
                self.button(event, False)

            def wheelEvent(self, event):
                delta = event.angleDelta()
                self.send(
                    "axis", dx=-delta.x() / 120, dy=-delta.y() / 120, discrete=True
                )

            def key(self, event, pressed):
                if event.isAutoRepeat():
                    return
                code = event.nativeScanCode()
                if sys.platform == "win32":
                    extended = {
                        0x11C: 96,
                        0x11D: 97,
                        0x135: 98,
                        0x138: 100,
                        0x147: 102,
                        0x148: 103,
                        0x149: 104,
                        0x14B: 105,
                        0x14D: 106,
                        0x14F: 107,
                        0x150: 108,
                        0x151: 109,
                        0x152: 110,
                        0x153: 111,
                        0x15B: 125,
                        0x15C: 126,
                    }
                    code = extended.get(code, code)
                else:
                    code -= 8
                modifiers = 0
                for bit, flag in enumerate(
                    (
                        Qt.KeyboardModifier.ShiftModifier,
                        Qt.KeyboardModifier.ControlModifier,
                        Qt.KeyboardModifier.AltModifier,
                        Qt.KeyboardModifier.MetaModifier,
                    )
                ):
                    if event.modifiers() & flag:
                        modifiers |= 1 << bit
                if 0 <= code <= 65535:
                    self.send("key", keycode=code, pressed=pressed, modifiers=modifiers)

            def keyPressEvent(self, event):
                self.key(event, True)

            def keyReleaseEvent(self, event):
                self.key(event, False)

            def inputMethodEvent(self, event):
                if event.commitString():
                    self.send("text-commit", text=event.commitString())
                event.accept()

            def focusInEvent(self, event):
                session.request("focus-request", self.sid)

            def focusOutEvent(self, event):
                self.held_buttons.clear()
                session.release_input()
                super().focusOutEvent(event)

            def closeEvent(self, event):
                # Closing a client view hides that surface; it does not close the app.
                self.resize_timer.stop()
                self.held_buttons.clear()
                session.release_input()
                event.accept()

        self.Window = Window

        def texture(sid, texture):
            window = owner.windows.get(sid)
            if window is None:
                surface = session.tree.surfaces.get(sid)
                if surface is None or (
                    surface.parent_id is not None
                    and surface.parent_id not in owner.windows
                ):
                    return
                window = Window(sid)
                owner.windows[sid] = window
            window.image = QImage(
                bytes(texture.pixels),
                texture.size.w,
                texture.size.h,
                texture.size.w * 4,
                QImage.Format.Format_ARGB32,
            ).copy()
            window.update()

        session.on_texture = texture
        self._texture = texture

    def poll(self):
        self.app.processEvents()
        for sid, texture in self.session.textures.items():
            if sid not in self.windows and texture.ready:
                self._texture(sid, texture)
        from PySide6.QtCore import QPoint

        for surface in sorted(
            self.session.tree.surfaces.values(), key=lambda s: s.z_hint
        ):
            if surface.role not in (Role.POPUP, Role.TOOLTIP):
                continue
            parent = self.windows.get(surface.parent_id)
            child = self.windows.get(surface.surface_id)
            if parent and child:
                parent_surface = self.session.tree.surfaces[surface.parent_id]
                scale = min(
                    parent.width() / parent_surface.size.w,
                    parent.height() / parent_surface.size.h,
                )
                dx = (parent.width() - parent_surface.size.w * scale) / 2
                dy = (parent.height() - parent_surface.size.h * scale) / 2
                child.move(
                    parent.mapToGlobal(
                        QPoint(
                            round(dx + surface.anchor.x * scale),
                            round(dy + surface.anchor.y * scale),
                        )
                    )
                )
                child.resize(
                    round(surface.size.w * scale), round(surface.size.h * scale)
                )
                child.raise_()
        for sid in set(self.windows) - set(self.session.tree.surfaces):
            self.windows.pop(sid).close()

    def hints(self):
        hints = []
        for surface in self.session.tree.surfaces.values():
            window = self.windows.get(surface.surface_id)
            hidden = window and (not window.isVisible() or window.isMinimized())
            size = Size(window.width(), window.height()) if window else surface.size
            hints.append(
                ViewHint(
                    surface.surface_id,
                    Visibility.HIDDEN if hidden else Visibility.IN_VIEW,
                    size,
                    focused=bool(window and window.hasFocus()),
                )
            )
        return hints

    def stop(self):
        for window in self.windows.values():
            window.close()
        self.windows.clear()
