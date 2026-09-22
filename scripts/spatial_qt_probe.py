"""KWin-friendly timestamp windows for capture and latency measurements."""

import argparse
import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=int, default=30)
parser.add_argument("--events", type=Path)
parser.add_argument("--count", type=int, default=1)
parser.add_argument("--motion-count", type=int, default=1)
parser.add_argument("--minimize-check", action="store_true")
parser.add_argument(
    "--motion-seconds",
    type=float,
    default=0,
    help="Animate full-window color, then settle, to exercise lane migration",
)
args = parser.parse_args()
app = QApplication([])
windows, labels = [], []
for index in range(args.count):
    window = QWidget()
    layout = QVBoxLayout(window)
    label = QLabel()
    entry = QLineEdit()
    entry.setPlaceholderText("Remote keyboard input")
    layout.addWidget(label)
    layout.addWidget(entry)
    entry.textChanged.connect(
        lambda text: (
            args.events.write_text(json.dumps({"text": text})) if args.events else None
        )
    )
    title = "Telesthete Spatial Native Probe"
    window.setWindowTitle(title if args.count == 1 else f"{title} {index + 1}")
    label.setStyleSheet(
        "background:#17384a;color:white;font:20px monospace;padding:10px"
    )
    window.resize(480 if args.count == 1 else 260, 240 if args.count == 1 else 160)
    if args.count > 1:
        window.move(100 + index % 3 * 290, 100 + index // 3 * 210)
    window.show()
    entry.setFocus()
    windows.append(window)
    labels.append(label)
timer = QTimer()
started = time.monotonic()


def tick():
    for i, label in enumerate(labels):
        label.setText(f"Surface {i + 1}\n{time.time():.3f}")
        if i < args.motion_count and time.monotonic() - started < args.motion_seconds:
            color = "#1972a4" if int(time.monotonic() * 10) % 2 else "#a43a19"
            label.setStyleSheet(
                f"background:{color};color:white;font:20px monospace;padding:10px"
            )


timer.timeout.connect(tick)
timer.start(100)
if args.minimize_check:
    QTimer.singleShot(1500, windows[0].showMinimized)
QTimer.singleShot(args.seconds * 1000, app.quit)
app.exec()
