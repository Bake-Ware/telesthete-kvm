"""Dedicated animated test window; optional real Windows WGC assertion."""

import argparse
import json
import time
import tkinter as tk


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--windows-capture", action="store_true")
    parser.add_argument("--inject-check", action="store_true")
    parser.add_argument("--minimize-check", action="store_true")
    args = parser.parse_args()
    root = tk.Tk()
    root.title("Telesthete Spatial Native Probe")
    root.geometry("480x240+100+100")
    label = tk.Label(
        root, font=("Consolas", 24), foreground="white", background="#17384a"
    )
    label.pack(fill="both", expand=True)
    entry = tk.Entry(root)
    entry.pack(fill="x")
    entry.focus_set()
    started = time.monotonic()
    adapter = None
    surface_id = None
    seen = set()
    frame_size = None
    injected = False
    minimized = False
    restored = False
    if args.windows_capture:
        from surfaces.adapters.windows import WindowsOrigin

        adapter = WindowsOrigin()

    def update():
        nonlocal surface_id, frame_size, injected, minimized, restored
        label.config(
            text=f"Spatial Surfaces\n{time.time():.3f}\nType here to test input"
        )
        if adapter:
            rows = adapter.snapshot()
            if minimized and not root.state() == "iconic":
                restored = True
            if surface_id is None:
                for surface in rows:
                    if surface.title == "Telesthete Spatial Native Probe":
                        surface_id = surface.surface_id
                        adapter.start_capture(surface_id)
                        print(
                            json.dumps(
                                {
                                    "surface_id": surface_id,
                                    "size": [surface.size.w, surface.size.h],
                                }
                            ),
                            flush=True,
                        )
                        break
            elif (frame := adapter.frame(surface_id)) is not None:
                import hashlib

                seen.add(hashlib.sha256(frame.bgra).hexdigest())
                frame_size = [frame.size.w, frame.size.h]
                if args.inject_check and not injected:
                    adapter.focus(surface_id)
                    for code in (31, 25, 30, 20, 23, 30, 38):
                        for pressed in (True, False):
                            adapter.inject(
                                {
                                    "kind": "key",
                                    "surface_id": surface_id,
                                    "keycode": code,
                                    "pressed": pressed,
                                    "modifiers": 0,
                                }
                            )
                    injected = True
        if args.minimize_check and not minimized and time.monotonic() - started > 1.5:
            minimized = True
            root.iconify()
        if time.monotonic() - started < args.seconds:
            root.after(100, update)
        else:
            if adapter:
                adapter.stop()
            print(
                json.dumps(
                    {
                        "distinct_captured_frames": len(seen),
                        "frame_size": frame_size,
                        "text": entry.get(),
                        "restored_after_minimize": restored,
                    }
                ),
                flush=True,
            )
            root.destroy()

    root.after(200, update)
    root.mainloop()
    if args.windows_capture and len(seen) < 2:
        raise SystemExit("FAIL: WGC did not deliver changing window frames")
    if args.minimize_check and not restored:
        raise SystemExit("FAIL: selected WGC window remained minimized")


if __name__ == "__main__":
    main()
