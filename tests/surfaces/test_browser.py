import pytest

from surfaces.model import Rect, Role, Size, Surface


def test_browser_lists_local_and_remote_and_selects_dialog_owner(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from surfaces.browser import WindowBrowser

    class Session:
        session_id = "a" * 32
        catalog_rev = 1

        def __init__(self, name, catalog):
            self.origin_name = name
            self.catalog = {surface.surface_id: surface for surface in catalog}
            self.selections = []

        def select_windows(self, roots):
            self.selections.append(set(roots))

    local = Session("local", [Surface(1, Size(100, 80), title="Local editor")])
    remote = Session(
        "windows",
        [
            Surface(2, Size(120, 90), title="Remote app"),
            Surface(
                3,
                Size(40, 30),
                title="Save dialog",
                role=Role.DIALOG,
                parent_id=2,
                anchor=Rect(0, 0, 40, 30),
            ),
        ],
    )
    added = []
    browser = WindowBrowser([local, remote], on_add_remote=lambda *args: added.append(args))
    try:
        browser.refresh()
        assert browser.tree.topLevelItemCount() == 2
        assert browser.tree.topLevelItem(0).text(0) == "Local computer"
        assert browser.tree.topLevelItem(1).text(0) == "Remote: windows"
        dialog = browser.tree.topLevelItem(1).child(1)
        dialog.setSelected(True)
        browser.open_selected()
        assert remote.selections == [{2}]
        assert local.selections == []
        assert browser.tree.topLevelItem(1).child(0).text(3) == "Streaming"
        browser.stop_selected()
        assert remote.selections[-1] == set()
        browser.search.setText("Local editor")
        assert browser.tree.topLevelItem(1).isHidden()
        browser.add_remote()
        from PySide6.QtWidgets import QLineEdit, QSpinBox

        dialog = browser._add_dialog
        name, peer, secret = [
            widget for widget in dialog.findChildren(QLineEdit) if widget.parent() is dialog
        ]
        name.setText("third")
        peer.setText("192.0.2.10:10002")
        dialog.findChild(QSpinBox).setValue(10003)
        secret.setText("test-only-secret")
        dialog.accept()
        assert added == [("third", "192.0.2.10:10002", 10003, "test-only-secret")]
    finally:
        browser.stop()
