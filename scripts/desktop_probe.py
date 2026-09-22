"""Integration-test receiver: record actual desktop events, no KVM internals."""

import json
import os
import tkinter as tk
from pathlib import Path

root = tk.Tk()
root.geometry(os.environ.get("PROBE_GEOMETRY", "800x600+0+0"))
root.title("Telesthete KVM desktop integration probe")
text = tk.Text(root, font=("monospace", 18))
text.pack(fill="both", expand=True)
text.focus_force()
path = Path(os.environ.get("PROBE_LOG", "/tmp/kvm-probe.jsonl"))


def record(event):
    with path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "type": str(event.type),
                    "key": event.keysym,
                    "char": event.char,
                    "button": event.num,
                    "x": event.x_root,
                    "y": event.y_root,
                    "delta": event.delta,
                }
            )
            + "\n"
        )


for pattern in (
    "<KeyPress>",
    "<KeyRelease>",
    "<ButtonPress>",
    "<ButtonRelease>",
    "<Motion>",
    "<MouseWheel>",
):
    text.bind(pattern, record, add="+")
root.mainloop()
