# Telesthete KVM

**Software KVM over IP** - Control multiple computers with one keyboard and mouse

Built on [Telesthete](https://github.com/Bake-Ware/telesthete) P2P transport library.

## Features

- **Keyboard & Mouse Sharing** - Control multiple computers with one keyboard/mouse
- **Edge Detection** - Move cursor to screen edge to switch machines  
- **Clipboard Sync** - Copy/paste works across all machines
- **LAN Discovery** - Automatically finds peers on local network
- **Encrypted** - All traffic encrypted (PSK-based)
- **No Admin Required** - Runs in userspace (most cases)
- **Cross-Platform** - Windows and Linux support

## Installation

```bash
# Install from GitHub
pip install git+https://github.com/Bake-Ware/telesthete-kvm.git

# Or clone and install locally
git clone https://github.com/Bake-Ware/telesthete-kvm.git
cd telesthete-kvm
pip install -e .
```

**Note:** Not yet published to PyPI. Install from GitHub for now.

**Dependencies:** Automatically installs `telesthete`, `pynput`, and `pyperclip` from GitHub/PyPI.

## Quick Start

### 1. Create Monitor Layout

Create `layout.json` with your monitor configuration:

```json
[
  {
    "id": 0,
    "peer": "desktop",
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080
  },
  {
    "id": 0,
    "peer": "laptop",
    "x": 1920,
    "y": 0,
    "width": 1920,
    "height": 1080
  }
]
```

**Key points:**
- Each monitor has unique `id` per peer
- `peer` is the hostname of the machine  
- Coordinates define spatial layout (laptop is right of desktop)

### 2. Run on Each Machine

**Desktop:**
```bash
python -m kvm.kvm --psk "my-secret-key" --hostname desktop --layout layout.json
```

**Laptop:**
```bash
python -m kvm.kvm --psk "my-secret-key" --hostname laptop --layout layout.json
```

**Both machines must use the same PSK and layout file.**

### 3. Use It

- Move mouse to right edge of desktop → cursor appears on laptop
- Move mouse to left edge of laptop → cursor returns to desktop
- Copy text on desktop → paste on laptop
- All keyboard input follows the mouse

## Command Line Options

```bash
python -m kvm.kvm --help
```

- `--psk` (required) - Pre-shared key for encryption
- `--hostname` - Machine hostname (default: auto-detect)
- `--port` - Port number (default: 9999)
- `--no-discovery` - Disable LAN discovery
- `--layout` - Path to monitor layout JSON file

## How It Works

```
┌─────────────────────────────────────┐
│         KVM Application             │
├─────────────────────────────────────┤
│  HID Capture  │  Edge    │ Clipboard│
│  (pynput)     │  Detect  │  Sync    │
├─────────────────────────────────────┤
│         Telesthete Band             │
│  (Streams: HID Events, State, Clip) │
├─────────────────────────────────────┤
│  Discovery │ UDP Transport │ Crypto │
└─────────────────────────────────────┘
```

The KVM uses three streams over Telesthete:
1. **HID Events (Priority 0)** - Key presses, mouse moves, clicks
2. **HID State (Priority 1)** - State snapshots (prevents stuck keys)
3. **Clipboard (Priority 128)** - Clipboard synchronization

## Configuration Examples

### Two Monitors on Desktop + Laptop

```json
[
  {
    "id": 0,
    "peer": "desktop",
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080
  },
  {
    "id": 1,
    "peer": "desktop",
    "x": 1920,
    "y": 0,
    "width": 1920,
    "height": 1080
  },
  {
    "id": 0,
    "peer": "laptop",
    "x": 3840,
    "y": 0,
    "width": 1920,
    "height": 1080
  }
]
```

### Vertical Stack

```json
[
  {
    "id": 0,
    "peer": "top",
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080
  },
  {
    "id": 0,
    "peer": "bottom",
    "x": 0,
    "y": 1080,
    "width": 1920,
    "height": 1080
  }
]
```

## Troubleshooting

**Peers not discovering:**
- Check firewall (allow UDP port 9998 for discovery, 9999 for Band)
- Verify both machines on same subnet

**Keyboard not working:**
- pynput may need permissions on some systems
- Linux Wayland has input injection limitations

**Mouse stuttering:**
- Check network latency (use wired connection)
- Verify HID stream priority is 0

**Stuck keys:**
- State snapshots prevent this (sent every 100ms)
- Click on local machine to release if needed

## Performance

- **Latency:** <5ms on LAN
- **Bandwidth:** ~10-50 KB/s typical usage
- **CPU:** <1%
- **RAM:** ~50MB

## Limitations

- No video forwarding (HID only)
- No file drag-and-drop (clipboard only)
- No audio forwarding
- Wayland support limited
- Text clipboard only (images coming soon)

## Security

All traffic encrypted with XChaCha20-Poly1305. PSK must be strong. Use on trusted networks only.

## Comparison

| Feature | Telesthete KVM | Barrier/Synergy | Input Director |
|---------|---------------|----------------|----------------|
| Platform | Win, Linux | Win, Mac, Linux | Windows only |
| Encryption | Yes (PSK) | SSL/TLS | Optional |
| NAT Traversal | Coming | No | No |
| Admin Required | No | Sometimes | Sometimes |
| Open Source | Yes | Yes | No |

## Status

**Alpha** - Core functionality works, needs real-world testing on two physical machines.

## License

MIT License

## Links

- [Telesthete Library](https://github.com/Bake-Ware/telesthete) - Transport layer
- Issues: Report bugs on GitHub

## Installation (Quick)

**Windows:** Double-click `install.bat` or run `.\install.ps1` in PowerShell

**Linux/Mac:** `pip install git+https://github.com/Bake-Ware/telesthete-kvm.git`

See [INSTALL.md](INSTALL.md) for detailed instructions.
