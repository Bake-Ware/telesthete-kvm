# Running the Spatial Surfaces prototype

This is a working native desktop prototype, not the completed M0–M7 design.
All application implementation is in this repository. Rook is only a protocol
reference and a way to operate the Windows testbed.

## Implemented

- Explicit, configured direct peers over the pinned Telesthete protocol. Real
  reliable Channels for control and clipboard transport; separate latest-wins
  motion/hint Streams and media Streams. Socket scheduling prioritizes control.
- Immutable surface records, atomic tree snapshots, live ordered add/update/remove
  deltas, revision-gap recovery, non-reused IDs, parent validation and modal input filtering.
- KWin script metadata, per-window native screencast feeds through PipeWire,
  client-content cropping, and libei keyboard/pointer input. Focus, resize and
  close operations target selected windows through KWin scripts.
- Windows HWND enumeration, owner relationships, WGC capture using HWNDs,
  DWM-correct content crops, foreground-only SendInput and held-input release.
  Selected minimized windows are restored to keep WGC frames flowing.
- A Qt flat client with local placement, letterboxing, native keycodes, pointer
  routing, popup anchoring, modal dialog windows and debounced source resizing.
  Losing client focus releases held origin input. No eye views cross the wire.
- Hot H.264 atlases with 64-pixel allocation alignment, 16-pixel replicated
  gutters, epoch-safe cuts, keyframes on layout changes and explicit resync. In-slot
  resizes retain allocations and the encoder, advancing the crop epoch safely. Six native
  KWin windows have run through one NVENC encoder and one client decoder.
- Lossless QOI damage tiles, per-block revisions, repairable missing fragments,
  and reliable lane status notices to detect completely lost batches.
- Live visibility freeze/thaw and peripheral cold update limiting. Pure,
  independently tested policy computes migration, target resolution, hysteresis,
  QP deltas and cold-first bandwidth allocation. `--lane auto` applies live
  hot/cold migration with a reliable frame fence and retains the prior texture
  until its destination is ready. Damage is estimated from sampled pixels.
  Hot surfaces held at a smaller view hint reduce resolution after two seconds
  and return to full size when needed; native Linux and Windows runs passed.

The CLI supports `cold` (default), `hot`, or adaptive mixed `auto` subscriptions.
It can stream several explicitly selected windows plus their owned children.
Small tree changes now use revisioned deltas; complex churn falls back to a
snapshot. A client may connect to four explicitly configured origins in one
flat workspace. Text clipboard sync is opt-in with `--clipboard` on both peers.

## Install

Python 3.10+ is the package baseline. Native validation used Python 3.14 on
cachyrig and Python 3.12 on Windows. From this checkout:

```sh
python -m venv --system-site-packages .venv
# Linux
. .venv/bin/activate
python -m pip install -e '.[test,spatial]'
```

Windows: create the venv without `--system-site-packages`, activate
`.venv\Scripts\Activate.ps1`, then install the same extras. The Windows-only
`windows-capture` dependency supplies WGC capture. For hot encoding on Windows,
use `--encoder portable` (software H.264); Windows hardware encoding is pending.

Linux origin also needs distro-provided PyGObject, dbus-python, the GStreamer
Python introspection bindings/plugins, PipeWire, libei, the Wayland client
headers/scanner, CMake, pkg-config and plasma-wayland-protocols. Using the system
Python's site packages makes its GI bindings available in the venv. PySide6 and
PyAV provide the client shell and H.264 decoder. Cold tiles do not require PyAV.

Build the KWin bridge and register its narrowly scoped screencast capability:

```sh
cmake -S native/kwin -B /tmp/telesthete-kwin-build
cmake --build /tmp/telesthete-kwin-build
desktop-file-install --dir="$HOME/.local/share/applications" \
  /tmp/telesthete-kwin-build/org.bake.TelestheteSpatialCapture.desktop
```

The generated desktop file points to that build's executable. If moving the
build, regenerate and reinstall the declaration. This does not disable KWin's
permission checks. The origin loads its own temporary KWin script and unloads
it during normal shutdown.

Cachyrig has an RTX 2070: `nvh264enc` works; its VAAPI H.264 encoder reports no
usable encoding entry point. `--encoder nvenc` is therefore the tested hardware
path. `--encoder software` uses GStreamer's x264; `portable` uses PyAV's x264.
The GStreamer VAAPI path is present but unvalidated on a VAAPI-capable machine.

## Run

Create a dedicated shared test secret and copy it securely to both peers:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))" > spatial.psk
chmod 600 spatial.psk  # Linux
```

List windows on the origin:

```sh
python -m surfaces list
```

Origin (replace the addresses and title):

```sh
python -m surfaces origin --name origin --peer-name client \
  --bind 0.0.0.0:10000 --peer 192.168.1.20:10001 \
  --psk-file spatial.psk --title 'Terminal' --stats origin.local.json
```

Client:

```sh
python -m surfaces client --name client --peer-name origin \
  --bind 0.0.0.0:10001 --peer 192.168.1.10:10000 \
  --psk-file spatial.psk --stats client.local.json
```

Start both within 30 seconds. Names, secret and `--channel-base` must match.
Only the configured peer address is admitted. Use `--lane hot --encoder nvenc`
on a Linux origin to test H.264, or `--lane auto --encoder nvenc` for adaptive
mixed streaming; leave `cold` for consistently lossless text. Repeat
`--title` for multiple windows. Titles must identify exactly one window each.
Use `--headless` on the client for decoder-only checks. `--seconds 10` ends a
bounded test run. `TELESTHETE_PSK` may supply the secret instead of a file.

For a client connected to several origins, create a JSON file such as:

```json
{"origins": [
  {"name": "linux", "bind": "0.0.0.0:10001", "peer": "192.168.1.10:10000", "psk_file": "linux.psk"},
  {"name": "windows", "bind": "0.0.0.0:10003", "peer": "192.168.1.20:10002", "psk_file": "windows.psk"}
]}
```

Run `python -m surfaces client --origin-config origins.json`. Each origin must
expect the client name `client`, use its matching channel base, and have its own
configured endpoint. Relative secret paths resolve next to the config file.
The Qt shell offsets windows by origin; `--headless` decodes both without views.

Add `--clipboard` to both ends of a single-origin connection to opt in to
request-based text sync. Text is capped at 1 MiB.

The installed command is `telesthete-surfaces`. `telesthete-kvm` remains the
legacy command. Close a client view to hide it; that does not close the source
application. Ctrl+C stops the process and releases injected input. Missing
client activity for 1.5 seconds releases origin input and clears hints.

## Tests and evidence

```sh
python -m pytest -q
python -m ruff check surfaces tests/surfaces scripts/spatial*.py
python -m build
```

The media test uses `av` and `numpy` and skips if they are absent. Native probes:

```sh
python scripts/spatial_qt_probe.py --count 6 --seconds 30
python scripts/spatial_resize_smoke.py
# On Windows, from the checkout root:
python -c "import runpy; runpy.run_path('scripts/spatial_native_probe.py', run_name='__main__')" \
  --windows-capture --inject-check --seconds 8
```

See [recorded native and automated results](spatial-test-output.md). These runs
establish native capture, encoding, loopback transport and decoding; they do
not establish LAN/Wi-Fi latency or glass-to-glass latency. The overlay is ready
for those measurements, but no camera/display measurement has been made.

## Remaining implementation and acceptance work

- Qt Wayland `xdg_popup` menus do not appear in KWin's scripted `windowList()`
  and are not inside its per-window PipeWire capture. A Qt file dialog was
  exported as a child and decoded into a ready texture; menu capture needs a
  compositor-level popup source or another verified approach. Dialog input
  interaction remains to be checked.
- Isolate remoted windows on a headless output so restored/occluded applications
  cannot interfere with the local desktop. The current selected-window restore
  behavior keeps minimized capture alive on both tested desktops.
- Add measured congestion estimation and apply the computed cold-first budget
  to hot encoder rate, per-tile QP/ROI where the encoder supports it, and intra
  refresh. KWin's tested NVENC GStreamer element does not expose a per-tile ROI
  control in the current adapter.
- Complete negotiated AV1/HEVC, native Windows hardware encoding, touch and IME
  fallback where EIS lacks text capability. Current input covers keys, pointer
  buttons/motion and scrolling; Windows supports Unicode text commit.
- Integrate band discovery, authenticated route negotiation, relay fallback,
  reconnect UX and per-origin authorization beyond configured test peers.
- Implement and validate Quest/OpenXR and Android/glasses shells. No Android
  device is currently connected to `adb` on cachyrig; model and connection
  details are still needed for device acceptance. The flat client can host
  multiple origins, but that does not establish spatial placement.
- Measure glass-to-glass latency, sustained congestion/loss, churn, and power use.

None of those items is represented as complete by the desktop prototype.
