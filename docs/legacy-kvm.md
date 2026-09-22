# Telesthete KVM

Share a keyboard, mouse, and text clipboard between computers. Move to an adjacent
screen edge to switch machines; move back to return. Each computer keeps its own
display and applications. There is no video, audio, or file forwarding.

## Install

Requires Python 3.10+, Git, and a Windows or Linux **X11** desktop.
Native Wayland and macOS are not supported.

```sh
python -m venv .venv
# Linux:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install git+https://github.com/Bake-Ware/telesthete-kvm.git
```

For this checkout, use `python -m pip install -e '.[test]'`. Installation includes
a pinned Telesthete transport revision, pynput, pyperclip, and WebSocket support.
On Debian/Ubuntu X11, install `python3-venv python3-tk xclip` first.
Windows shortcuts: `install.bat` or `install.ps1`. See [INSTALL.md](../INSTALL.md).

## Configure both machines

Save the **same layout** on every peer. Names must match `--hostname`. Coordinates
`x`/`y` place monitors in a shared desktop; monitor IDs are unique **within a peer**.

```json
[
  {"id": 0, "peer": "desktop", "x": 0, "y": 0, "width": 1920, "height": 1080},
  {"id": 0, "peer": "laptop", "x": 1920, "y": 0, "width": 1920, "height": 1080}
]
```

Use each OS's actual pixel dimensions. Monitors must not overlap. Only touching
edges connect, including partially shared edges. Unequal resolutions and vertical
layouts work. `local_x` and `local_y` optionally specify the monitor's position in
its own OS coordinate space (useful for monitors left of the primary display).
Without those fields, the top-left extent of each peer defaults to local `(0, 0)`.
See [example_layout.json](../example_layout.json) for multiple local monitors.

Generate a strong shared secret once and securely copy the file to each peer:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))" > kvm.psk
# On Linux: chmod 600 kvm.psk
```

Run on each computer:

```sh
telesthete-kvm --hostname desktop --layout layout.json --psk-file kvm.psk
telesthete-kvm --hostname laptop --layout layout.json --psk-file kvm.psk
```

`python -m kvm` and `python -m kvm.kvm` are equivalent entry points. The secret can
also come from `TELESTHETE_PSK`. `--psk` is available but exposes it in process args.

**Emergency return: Ctrl+Alt+Esc.** Either endpoint can end the active session.
A lost connection releases held remote keys/buttons and restores local control
after approximately 1.5 seconds. Graceful shutdown also releases all input; if the
controlling process crashes, the OS releases its input grabs automatically.

## Connections

### LAN

LAN discovery is enabled by default. Allow UDP 9998 (discovery) and UDP 9999
(transport) between peers. For explicit connections:

```sh
telesthete-kvm --hostname desktop --layout layout.json --psk-file kvm.psk \
  --no-discovery --peer 192.168.1.25:9999
```

Repeat `--peer` for additional computers. Only configured layout peers can control
this application, and all peers must use the same layout and protocol version.
Duplicate peer names are rejected on direct LAN connections.

### Internet / relay

Run a current [Telesthetium hub](https://github.com/Bake-Ware/telesthete/tree/main/rust/telesthitium)
and supply its full WebSocket endpoint on every peer:

```sh
telesthete-kvm --hostname desktop --layout layout.json --psk-file kvm.psk \
  --hub wss://relay.example.com/band
```

Hub mode disables LAN discovery. Peers reconnect automatically when the relay
returns; focus always returns locally during an outage. The relay transports
opaque encrypted Telesthete frames and does not need the shared secret. WSS
certificate verification uses the system trust store; there is no insecure bypass.
Use unique hostnames for every instance, including in hub mode.

## Behavior and limits

- The source grabs keyboard/mouse input while controlling a peer. Pointer motion
  uses relative deltas and a parked local cursor, so motion can continue beyond
  the source screen's physical edge.
- Focus handoff requires an explicit grant. Concurrent requests are declined;
  neither machine silently steals an active session.
- Keyboard, button, and scroll events are ordered, acknowledged, and retried.
  Pointer positions are lossy for responsiveness. Periodic state reconciles held
  keys/buttons; focus leases prevent stuck input after disconnects.
- Clipboard transfers support UTF-8 text (including clearing the clipboard),
  up to 1 MiB, with chunking, retries, integrity checks, and feedback prevention.
  Copy something after startup to synchronize it. Images and file lists are not
  supported. Concurrent clipboard changes resolve by logical revision and name.
- Computers sharing the secret are trusted to control configured peers and read
  clipboard changes. Use a dedicated secret for your KVM group.
- Windows elevated applications/secure desktops can restrict input injection.
  Display scaling must match the coordinates reported by the input backend.

## Diagnostics and development

```sh
telesthete-kvm --hostname desktop --layout layout.json --psk-file kvm.psk --check
telesthete-kvm --help
python -m pytest -q
python -m build
```

`--check` validates configuration without opening input devices. `--verbose`
enables diagnostic logs. `--status-file status.local.json` writes connection and
focus state as JSON, without keystrokes, clipboard contents, or the secret.

See [architecture](architecture.md) and [validation results](validation.md).
The application protocol is version 2; the original 0.1 alpha is incompatible.
Both endpoints must be upgraded together.

## License

MIT. Dependencies retain their own licenses.
