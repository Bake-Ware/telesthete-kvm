# Legacy KVM architecture

For the new repository direction, see [Spatial Surfaces protocol](spatial-surfaces-protocol.md).

## Modules

- `kvm.py`: application state, focus negotiation, leases, clipboard replication,
  CLI and lifecycle.
- `hid.py`: pynput capture/injection; X11 input grabs and Windows hook suppression.
  Desktop imports are lazy so configuration validation and protocol tests do not
  need a display.
- `edge.py`: shared layout validation and OS-local/global coordinate conversion.
- `clipboard_sync.py`: serialized OS clipboard access and feedback prevention.
- `network.py`: pinned Telesthete Band adapter, peer lifecycle and reliability.
- `hub.py`: current opaque-frame WebSocket relay carrier, with authenticated
  source attribution and automatic reconnection.

## Focus lifecycle

Idle peers keep local control. An edge crossing creates a random 128-bit focus
token and sends a reliable request. The target grants only when idle, configured
with the same layout, and outside its return cooldown. After the grant, the
source grabs local input and parks its cursor. The target accepts input only
from that source and token. A third peer cannot inject into the session.

The source maintains the virtual pointer in shared coordinates and sends absolute
positions to the target. Physical source motion becomes deltas relative to the
parked cursor. Crossing back releases the target and restores the source cursor;
crossing into another peer releases the old target before requesting the next.

Both sides exchange lease traffic at 100 ms intervals. A missing lease for 1.5 s,
peer loss, input error, Ctrl+Alt+Esc, or shutdown releases all injected keys and
buttons and removes local grabs. Input callbacks cross from OS threads into the
asyncio event loop via `call_soon_threadsafe` and a bounded queue.

## Transport

Telesthete supplies encryption, authentication, replay protection, LAN discovery,
and peer sessions. The dependency is pinned to a tested commit. The current
Python Band has no `from_hub` API. `HubTransport` carries its existing encrypted
frames over WebSocket and attributes received frames by authenticated HELLO and
session keys, before normal Band verification. The relay itself gets no PSK.

Application protocol version 2 uses three reserved Stream IDs:

| ID | Use | Delivery |
|---|---|---|
| 71 | Focus, layout handshake, discrete input | Ordered, acknowledged, retried |
| 72 | Pointer, held-state snapshots, leases, presence | Latest position/state |
| 73 | Clipboard metadata and chunks | Ordered, acknowledged, retried |

Each payload includes version, sender, target and link generation. Reliable lanes
have independent sequence numbers, a 32-message receive/send window, acknowledgments
and 150 ms retransmissions. A 3 s failure resets the application link. Transport
restarts and link generations reset ordering safely. Clipboard traffic cannot
block the reliable input lane. Regular messages are limited to 1400 bytes;
held-state snapshots allow up to 8192 bytes for unusually many simultaneous keys
(normal input stays below the network MTU).

Only configured names become application peers. All peers must have identical
layout hashes. Shared-secret group members remain mutually trusted; this is not
an identity system or protection against a malicious member of that group.

## Clipboard

Changes after startup receive a `(logical counter, hostname)` revision. A transfer
begins with byte length and SHA-256, continues with 600-byte chunks (up to 16 in
flight), and finishes with an integrity check. Transfers cap at 1 MiB. Empty
text is a valid change. Each peer has at most one incoming transfer; abandoned
transfers expire. Only newer revisions apply. OS calls are serialized outside the
input/lease loop. Applying remote text also updates the monitor's hash, preventing
feedback. Clipboard contents never enter normal logs or status JSON.

## Compatibility

The application is intentionally incompatible with the unfinished 0.1 protocol.
Upgrade peers together. Desktop support targets Linux X11 and Windows. The test
suite separates actual network tests with simulated desktop adapters from VM
checks that observe events delivered to actual X11 windows. Native Wayland,
macOS, video/audio, files and graphical setup are outside this release's scope.
