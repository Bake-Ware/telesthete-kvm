"""Live Qt window catalog and subscription controls for configured origins."""


class WindowBrowser:
    def __init__(self, sessions, *, on_add_remote=None):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QSpinBox,
            QTreeWidget,
            QTreeWidgetItem,
            QVBoxLayout,
            QWidget,
        )

        self.app = QApplication.instance() or QApplication([])
        self.sessions = sessions
        self.on_add_remote = on_add_remote
        self.selected_roots = [set() for _ in sessions]
        self.closed = False
        self._signature = None
        owner = self

        class BrowserWindow(QMainWindow):
            def closeEvent(self, event):
                owner.closed = True
                super().closeEvent(event)

        self.window = BrowserWindow()
        self.window.setWindowTitle("Telesthete Spatial Browser")
        self.window.resize(860, 580)
        central = QWidget()
        layout = QVBoxLayout(central)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter windows by title or app")
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Window", "App", "Size", "State"])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 180)
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open selected")
        self.stop_button = QPushButton("Stop selected")
        self.refresh_button = QPushButton("Refresh")
        self.add_button = QPushButton("Add remote…")
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch()
        if on_add_remote:
            buttons.addWidget(self.add_button)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)
        layout.addWidget(
            QLabel("Double-click a window to stream it. Owned dialogs follow their app.")
        )
        self.status = QLabel("Connecting to origins…")
        layout.addWidget(self.status)
        self.window.setCentralWidget(central)
        self.open_button.clicked.connect(self.open_selected)
        self.stop_button.clicked.connect(self.stop_selected)
        self.refresh_button.clicked.connect(self.refresh)
        self.add_button.clicked.connect(self.add_remote)
        self.search.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(lambda _item, _column: self.open_selected())
        self._item_class = QTreeWidgetItem
        self._user_role = Qt.ItemDataRole.UserRole
        self._dialog_types = (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QSpinBox,
            QMessageBox,
        )
        self.window.show()

    def add_remote(self):
        if self.on_add_remote is None:
            return
        (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QSpinBox,
            QMessageBox,
        ) = self._dialog_types
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Add a remote origin")
        form = QFormLayout(dialog)
        name = QLineEdit()
        name.setPlaceholderText("windows")
        peer = QLineEdit()
        peer.setPlaceholderText("192.168.1.20:10000")
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(10001 + 2 * (len(self.sessions) - 1))
        secret = QLineEdit()
        secret.setEchoMode(QLineEdit.EchoMode.Password)
        secret.setPlaceholderText("Shared secret from remote origin")
        form.addRow("Origin name", name)
        form.addRow("Origin IP:port", peer)
        form.addRow("Local receive port", port)
        form.addRow("Shared secret", secret)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def submit():
            try:
                self.on_add_remote(
                    name.text().strip(), peer.text().strip(), port.value(), secret.text()
                )
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self.window, "Could not add origin", str(exc))

        dialog.accepted.connect(submit)
        self._add_dialog = dialog
        dialog.open()

    def session_added(self):
        self.selected_roots.append(set())
        self.refresh()

    def _chosen(self):
        chosen = {}
        for item in self.tree.selectedItems():
            identity = item.data(0, self._user_role)
            if identity is None:
                continue
            index, sid = identity
            catalog = self.sessions[index].catalog
            surface = catalog.get(sid)
            if surface is None:
                continue
            while surface.parent_id is not None:
                surface = catalog.get(surface.parent_id)
                if surface is None:
                    break
            if surface is not None:
                chosen.setdefault(index, set()).add(surface.surface_id)
        return chosen

    def _select(self, opening):
        for index, roots in self._chosen().items():
            selected = self.selected_roots[index]
            updated = selected | roots if opening else selected - roots
            try:
                self.sessions[index].select_windows(updated)
            except (ValueError, BufferError) as exc:
                self.status.setText(f"Selection failed: {exc}")
                continue
            self.selected_roots[index] = updated
        self.refresh()

    def open_selected(self):
        self._select(True)

    def stop_selected(self):
        self._select(False)

    def refresh(self):
        selected_items = {
            item.data(0, self._user_role)
            for item in self.tree.selectedItems()
            if item.data(0, self._user_role) is not None
        }
        self.tree.clear()
        for index, session in enumerate(self.sessions):
            label = (
                "Local computer"
                if index == 0
                else f"Remote: {session.origin_name}"
            )
            group = self._item_class(self.tree, [label])
            group.setExpanded(True)
            for surface in sorted(session.catalog.values(), key=lambda s: s.z_hint):
                title = surface.title or "[untitled]"
                if surface.parent_id is not None:
                    title = f"↳ {title}"
                root = surface
                while root.parent_id is not None and root.parent_id in session.catalog:
                    root = session.catalog[root.parent_id]
                state = (
                    "Streaming"
                    if root.surface_id in self.selected_roots[index]
                    else "Available"
                )
                item = self._item_class(
                    group,
                    [
                        title,
                        surface.app_id,
                        f"{surface.size.w} × {surface.size.h}",
                        state,
                    ],
                )
                identity = index, surface.surface_id
                item.setData(0, self._user_role, identity)
                if identity in selected_items:
                    item.setSelected(True)
        self._filter(self.search.text())

    def _filter(self, text):
        needle = text.casefold().strip()
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            visible = 0
            for row in range(group.childCount()):
                item = group.child(row)
                matches = needle in (item.text(0) + " " + item.text(1)).casefold()
                item.setHidden(not matches)
                visible += matches
            group.setHidden(bool(needle) and visible == 0)

    def poll(self, statuses):
        self.app.processEvents()
        for session, selected in zip(self.sessions, self.selected_roots):
            selected.intersection_update(session.catalog)
        signature = tuple(
            (session.session_id, session.catalog_rev, tuple(sorted(selected)))
            for session, selected in zip(self.sessions, self.selected_roots)
        )
        if signature != self._signature:
            self.refresh()
            self._signature = signature
        self.status.setText("  ·  ".join(statuses))

    def stop(self):
        self.window.close()
