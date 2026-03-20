# Telesthete KVM over IP

Software KVM that shares keyboard, mouse, and clipboard across multiple machines using the Telesthete P2P transport layer.

## Features

- **Keyboard & Mouse Sharing** - Control multiple computers with one keyboard and mouse
- **Edge Detection** - Move cursor to screen edge to switch machines
- **Clipboard Sync** - Copy/paste works across all machines
- **LAN Discovery** - Automatically finds peers on local network
- **Encrypted** - All traffic encrypted with PSK-based encryption
- **No Admin Required** - Runs in userspace (most cases)
- **Cross-Platform** - Windows and Linux support

## Installation

```bash
cd telesthete
pip install -r requirements.txt
```

## Quick Start

### 1. Create Monitor Layout

Edit `example_layout.json` to match your setup:

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
    "peer": "laptop",
    "x": 1920,
    "y": 0,
    "width": 1920,
    "height": 1080
  }
]
```

Key points:
- Each monitor has unique `id` per peer
- `peer` is the hostname of the machine
- Coordinates define spatial layout (monitor 1 is to the right of monitor 0)

### 2. Run on Each Machine

**Machine 1 (desktop):**
```bash
python -m telesthete.kvm.kvm --psk "my-secret-key" --hostname desktop --layout example_layout.json
```

**Machine 2 (laptop):**
```bash
python -m telesthete.kvm.kvm --psk "my-secret-key" --hostname laptop --layout example_layout.json
```

Both machines must use the same PSK and layout file.

### 3. Use It

- Move mouse to right edge of desktop → cursor appears on laptop
- Move mouse to left edge of laptop → cursor returns to desktop
- Copy text on desktop → paste on laptop
- All keyboard input follows the mouse

## Command Line Options

```bash
python -m telesthete.kvm.kvm --help
```

Options:
- `--psk` (required) - Pre-shared key for encryption
- `--hostname` - Machine hostname (default: auto-detect)
- `--port` - Port number (default: 9999)
- `--no-discovery` - Disable LAN discovery (manual connection)
- `--layout` - Path to monitor layout JSON file

## How It Works

### Architecture

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

### Streams

The KVM uses three streams over the Telesthete transport:

1. **HID Events (Priority 0)** - Individual key presses, mouse moves, clicks
2. **HID State (Priority 1)** - Periodic state snapshots (prevents stuck keys)
3. **Clipboard (Priority 128)** - Clipboard content synchronization

### Focus Model

- Each machine starts with local focus
- When cursor hits screen edge, focus transfers to adjacent machine
- The controlling machine sends HID events to the controlled machine
- The controlled machine injects received events as if local

### Edge Detection

- Polls cursor position at ~60Hz
- Builds coordinate map from layout JSON
- Calculates which edge is nearest
- Triggers transition when cursor stays at edge (debounced)

### Clipboard Sync

- Monitors local clipboard every 500ms
- On change, sends clipboard data to all peers
- Peers update their clipboard
- Prevents feedback loops via hash tracking

## Configuration

### Multi-Monitor Setup

Desktop with two monitors + laptop:

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

Transitions:
- Right edge of desktop monitor 1 (x=3839) → laptop (x=3840)
- Left edge of laptop (x=3840) → desktop monitor 1 (x=3839)

### Vertical Arrangements

Stack monitors vertically:

```json
[
  {
    "id": 0,
    "peer": "machine1",
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080
  },
  {
    "id": 0,
    "peer": "machine2",
    "x": 0,
    "y": 1080,
    "width": 1920,
    "height": 1080
  }
]
```

## Troubleshooting

### Peers Not Discovering

- Check firewall (allow UDP port 9998 for discovery, 9999 for Band)
- Verify both machines on same subnet
- Try manual connection (disable discovery, specify peer IP)

### Keyboard Not Working

- pynput may need permissions on some systems
- On Linux with Wayland, input injection has limitations
- Check logs for errors

### Mouse Stuttering

- Reduce network latency (use wired connection)
- Check CPU usage on both machines
- Verify HID event stream priority is 0 (highest)

### Stuck Keys

- HID state snapshots should prevent this (sent every 100ms)
- If keys stick, click on local machine to release
- Check logs for packet loss

### Clipboard Not Syncing

- Verify pyperclip is installed
- Some clipboard managers may interfere
- Check clipboard stream logs

## Security

### Encryption

- All traffic encrypted with XChaCha20-Poly1305
- Keys derived from PSK using HKDF
- No plaintext data on wire (except Band ID for routing)

### Attack Surface

- PSK must be strong (treat like a password)
- Anyone with PSK can join Band and send input
- Recommended: Use on trusted networks only
- Consider: Run over VPN for internet use

### Privacy

- No telemetry, no external servers (unless using Telesthetium relay)
- All data stays between your machines
- LAN discovery broadcasts hostname only

## Performance

### Latency

- LAN: <5ms end-to-end (HID event to injection)
- Typical: Mouse feels native up to ~20ms
- Over internet: Depends on network (not recommended for gaming)

### Bandwidth

- HID events: ~1-10 KB/s (very low)
- HID state: ~1 KB/s (snapshots)
- Clipboard: Burst when copying (depends on size)
- Total: <50 KB/s typical usage

### Resource Usage

- CPU: <1% on modern hardware
- RAM: ~50MB per instance
- Network: Negligible (see bandwidth above)

## Comparison to Alternatives

| Feature | Telesthete | Barrier/Synergy | Input Director | Mouse Without Borders |
|---------|-----------|----------------|----------------|---------------------|
| **Platform** | Win, Linux | Win, Mac, Linux | Windows only | Windows only |
| **Encryption** | Yes (PSK) | SSL/TLS | Optional | Optional |
| **NAT Traversal** | Coming | No | No | No |
| **Admin Required** | No | Sometimes | Sometimes | Sometimes |
| **Clipboard** | Yes | Yes | Yes | Yes |
| **Open Source** | Yes | Yes | No | No |
| **Active** | Yes | Forks active | Abandoned | Abandoned |

## Limitations

### Current

- No video forwarding (HID only)
- No file drag-and-drop (clipboard only)
- No audio forwarding
- Windows/Linux only (macOS needs testing)
- Wayland support limited (input injection)

### Future Enhancements

- GUI configuration tool
- Hotkey support (e.g., Win+` to release)
- Per-application input routing
- Window-level forwarding (RAIL-style)
- Multi-hop (control A through B through C)
- Touch/pen tablet support

## Development

### Project Structure

```
telesthete/
├── protocol/          # Transport layer (crypto, streams, channels)
├── transport/         # UDP, discovery, relay client
├── kvm/              # KVM application
│   ├── hid.py        # Keyboard/mouse capture and injection
│   ├── edge.py       # Edge detection and coordinate mapping
│   ├── clipboard_sync.py  # Clipboard monitoring
│   └── kvm.py        # Main application
└── cli.py            # Entry point
```

### Running Tests

```bash
# Protocol layer tests
python tests/test_full_stack.py

# Edge detection
python -m telesthete.kvm.edge

# HID (requires manual testing)
python -m telesthete.kvm.hid

# Clipboard (copy text during test)
python -m telesthete.kvm.clipboard_sync
```

### Debug Logging

```bash
# Set log level
export LOG_LEVEL=DEBUG
python -m telesthete.kvm.kvm --psk "test" --layout example_layout.json
```

## License

MIT (TBD - specify in LICENSE file)

## Contributing

Not yet accepting contributions (early development).

## Credits

Built on:
- PyNaCl (crypto)
- pynput (HID)
- pyperclip (clipboard)

Inspired by:
- Input Director (original inspiration)
- Barrier/Synergy (prior art)
- Magic Wormhole (transport concepts)

---

**Status:** Alpha - Core functionality works, edge cases remain.

**Tested:** Windows 11, will need Linux testing for Wayland compatibility.
