# Spatial prototype validation — 2026-09-21

All implementation is in `telesthete-kvm`. Windows target: **WIN11-FLOPHOUSE**.
Tests use dedicated short-lived windows and test secrets, never general desktop
capture. Native test processes were stopped after the runs.

## Automated

- 57 tests passed locally: 27 legacy tests plus 30 spatial tests (Python 3.14).
- 30 spatial tests passed on WIN11-FLOPHOUSE (Python 3.12, 2.31 s), including
  encrypted UDP packet-loss recovery, multi-chunk snapshots, hot atlas software
  encode/decode, cold loss repair and freeze/thaw. Rook record:
  `6805b71fc8f347f8baa0c0e0d6497f46`.
- Spatial coverage includes atomic tree rejection, gap recovery, modal input,
  policy hysteresis, six aligned allocations, replicated gutters, epoch changes,
  malformed/bounded reassembly, independent cold block revisions and QOI vectors.
- A callback-only transport adapter drains the pinned Channel library's duplicate
  receive queues, preventing accumulation during long sessions.

## Native Linux capture

KWin script exported 15 live surface records. A dedicated timestamp window
produced 51 distinct PipeWire frames in five seconds. KWin included a 28-pixel
server decoration; the adapter now crops to `clientGeometry`.

libei exposed resumed keyboard and absolute-pointer devices. Synthesized evdev
keys reached the dedicated Qt text field and produced `spatial`. The installed
KWin EIS endpoint did not expose a text-input device; Unicode IME fallback remains
unimplemented on that path.

## Native Linux streaming

| Run | Origin frames | Decoded frames | Duration | Media bytes | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| One KWin window, QOI cold lane, Qt client | 117 | 117 | 12.12 s | 248950 | No session errors |
| One KWin window, NVENC H.264, Qt client | 80 | 79 | 8.15 s | 72044 | No session errors; shutdown overlapped final frame |
| Six real KWin windows, one NVENC atlas, headless cutter | 187 | 187 | 10.15 s | 636012 | Six ready textures, one initial layout, no further repacks/errors |

Media bytes include Telesthete framing/tag/Stream overhead, excluding IP/UDP and
control traffic. The six-window run used about 501 kbit/s and one encoder session.
Origin pipeline work totaled 2.426 s and client decode/cut work 1.387 s. These are
CPU/wall pipeline counters, **not glass-to-glass latency**. NVENC was tested at
640×480 independently; VAAPI failed on this NVIDIA host, so no VAAPI success is
claimed. The atlas unit test separately verifies resize/repack and stale-epoch
rejection; the six-live-window run did not force a resize.

## Native Windows capture and input

WGC returned changing pixels by HWND. Initial testing revealed that GetWindowRect
included invisible resize borders, incorrectly cropping a 480×240 client to
474×240. Using DWM extended frame bounds fixed this.

Final dedicated-window probe:

```text
surface size: 480x240
captured content: 480x240
40 distinct WGC frames in approximately 5 seconds
SendInput evdev sequence produced: spatial
```

Rook record: `6820bf4867fb4a3d95cd3705543ad447`.

## Native Windows streaming

A WGC origin and independent headless client ran on actual encrypted loopback
UDP sockets on WIN11-FLOPHOUSE:

```text
origin: 73 updates; 239649 cold-lane bytes; 8.109 seconds; no session errors
client: 73 updates decoded; 8.094 seconds; no session errors
origin tile work: 0.570 seconds total
client decode work: 0.217 seconds total
```

Rook record: `85fd03baf7a04825b72ecf9d29599955`. That initial run reported UDP
ICMP reset messages at shutdown. Python 3.12 does not expose the corresponding
socket constant, so the final adapter uses `WSAIoctl(SIO_UDP_CONNRESET)` directly.
A repeated native run passed with **24/24 updates**, one ready texture, 98739
media bytes over 8.125 seconds, no session errors and no reset/traceback messages.
Origin tile work totaled 0.220 seconds; client decode work totaled 0.077 seconds.
Rook record: `1139037b6be64c32832f219a77bb6c63`.

## Limits

These are loopback native integration runs. They do not verify a cross-machine
route, hub discovery, a relay, Quest/Android clients, glass-to-glass latency,
full popup/IME/touch behavior or sustained congestion.
A further visible Qt client resize run passed on both Linux and Windows: a
client resize traveled through the reliable Channel to the native source, and
both the source metadata and decoded texture reached 640×320. Windows record:
`4afd2d6d2ef9427381d7b8cd99dec0f2`. The harness intentionally closes the client
before the bounded origin exits; the resulting Channel peer-timeout notice is
expected. This verifies Qt rendering integration, not visual layout quality.
The [prototype guide](spatial-prototype.md) lists remaining work explicitly.

## Adaptive lane integration

A native KWin timestamp window animated its background, then settled. The live
policy performed exactly two migrations (cold → NVENC hot → cold), with one
initial atlas allocation and 126/126 updates decoded. The 13.14-second run sent
205329 cold bytes and 76376 hot bytes, and recorded no session errors. The final
texture was ready on the cold lane. An automated test with delayed hot packets
verifies that migration retains old pixels until the destination is ready and
that late packets cannot overwrite a newer cold texture.

Windows WGC → portable H.264 → headless decode also completed both migrations:
41/41 updates, one ready final cold texture, one initial atlas, and no session
errors or shutdown resets over 13.11 seconds. Media: 200918 cold bytes, 34208
hot bytes. Record: `c317b7d5dfaa467e96d5b65abf1c7db0`. An earlier native run
exposed sparse-capture gaps resetting the motion timer; the final estimate holds
recent peak damage for 0.5 seconds, then lets static content demote normally.

## Further desktop acceptance — 2026-09-22

- Real Windows WGC minimization/restoration: 17 distinct frames over six
  seconds, 480×240 content, selected HWND restored. Rook record
  `16ccc402159b4fb784a77dccf04c3a4f`.
- KWin selected-window minimization/restoration: minimized state appeared in
  its scripted tree, then cleared; 65 PipeWire frames captured during the run.
- Hot resolution policy: both Linux NVENC and Windows portable H.264 reduced a
  native 480×240 source to 100×50 after a sustained small view hint, restored
  480×240 after the hint grew, kept one initial atlas allocation, and reported
  no session errors. Windows record `12bcb5b602d142dca2e27e79d958b897`.
- Two separate KWin origins in one headless client decoded 77 and 80 frames
  with both textures ready. Two separate Windows WGC origins in one client
  decoded 27 frames each with both textures ready and no session errors. Windows
  record `da532edc178745ae905d335ab2fef099`. This was same-host loopback
  on each platform, not a Linux-to-Windows network run.
- New encrypted-channel tests passed for clipboard offer/request/data in both
  directions, live dialog/resize deltas, and recovery from a skipped tree revision.
- Qt Wayland popup probe: a `QMenu` reported visible within the application,
  but KWin's scripted window list showed no popup and the parent PipeWire image
  hash stayed unchanged. Its Qt `QFileDialog` appeared as an owned dialog. Native
  right-click menu acceptance is **not achieved** by the current KWin path.

A live KWin Qt file-dialog run streamed two surfaces (parent plus owned dialog);
the independent headless client ended with both textures ready, 17 completed
updates, and no session errors. The same probe's Qt menu was visible locally
but absent from the KWin tree and parent capture, so the menu case remains open.
