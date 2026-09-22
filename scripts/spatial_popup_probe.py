"""Owned Qt menu/dialog windows for native spatial surface acceptance."""

import argparse
import json
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

parser = argparse.ArgumentParser()
parser.add_argument("--events", type=Path)
parser.add_argument("--seconds", type=int, default=20)
parser.add_argument(
    "--auto-open", action="store_true", help="Show menu and dialog without remote input"
)
args = parser.parse_args()
app = QApplication([])
state = {"menu_selected": False, "dialog_opened": False}


def record():
    if args.events:
        args.events.write_text(json.dumps(state))


class Probe(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telesthete Spatial Popup Probe")
        self.resize(360, 220)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Right-click for menu. Click below for file dialog."))
        button = QPushButton("Open file dialog")
        button.clicked.connect(self.show_file_dialog)
        layout.addWidget(button)
        self.dialog = None
        self.menu = None

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPos())

    def show_menu(self, position):
        self.menu = QMenu(self)
        action = self.menu.addAction("Select action")
        action.triggered.connect(self.select_menu)
        self.menu.popup(position)
        state["menu_visible"] = self.menu.isVisible()
        record()

    def select_menu(self):
        state["menu_selected"] = True
        record()

    def show_file_dialog(self):
        self.dialog = QFileDialog(self, "Open a file")
        self.dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.dialog.show()
        state["dialog_opened"] = True
        record()


window = Probe()
window.show()
record()
if args.auto_open:
    QTimer.singleShot(
        1000, lambda: window.show_menu(window.mapToGlobal(QPoint(80, 80)))
    )
    QTimer.singleShot(3000, window.show_file_dialog)
QTimer.singleShot(args.seconds * 1000, app.quit)
app.exec()
