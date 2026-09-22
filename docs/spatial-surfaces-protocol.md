# Spatial Surfaces v1 — M0 protocol delta

Status: initial implementation contract, 2026-09-21. The implemented subset and
remaining gaps are tracked in [the prototype guide](spatial-prototype.md).
This document supplements the [design](Telesthete%20Spatial%20Surfaces%20%E2%80%94%20Design%20Doc.md).
All extensions below are proposed surface behavior, not existing Rook features.

## Reference audit

Read on cachyrig before implementation:

| Reference | Revision / relevant files |
| --- | --- |
| `~/rook` | `c5665cabba1d90909998cf539e2d12f0725cef8c`; `rook/worker/core.py`, `rook/worker/transports/telesthete_hub.py`, `rook/worker/wire/channel.py` |
| `~/telesthete` | `b6290c7afabe14c0e81d4e9adfd5bcf498a251e5` (also this repo's pinned dependency); `SPEC.md` §§1–6, 9.5, 11.1; `telesthete/protocol/{framing,crypto,fragment,channel}.py`, `telesthete/band.py` |

The 27-byte header is `!16sBHQ`: band ID, channel type, u16 channel ID,
u64 encryption sequence. Keep it unchanged. Baseline encryption is IETF
ChaCha20-Poly1305, with optional AES-256-GCM, not XChaCha. Use the library's
session negotiation, channel-bound AAD, replay handling and shared sequence
source. Retransmissions use fresh outer sequences.

Rook's worker encodes capability calls as UTF-8 JSON:
`{"id":"...","cap":"...","args":{},"target":"worker-id"}`.
Replies are `{"id":"...","from":"worker-id","ok":true,"result":{}}`
or `{"id":"...","from":"worker-id","ok":false,"error":"..."}`.
The transport spec's §11.1 msgpack description does not match this current
worker path; use JSON for this integration. Surface capability names retain
the design's hyphens; existing Rook built-ins also use dotted names.

Rook's hub adapter sends §6.6 chunks directly on CHANNEL 0 and routes every
packet through the hub. It does not use the library's §6.1 reliable Channel
state machine. Fragmentation alone provides neither ordering nor retransmission.
The spatial adapter must use the reliable Channel implementation for sessions
and establish a direct peer route; do not reuse the hub adapter for media.

## Channel mapping

Allocate IDs per endpoint session, negotiate them in `surface-welcome`, and
keep allocations distinct across simultaneous origins/clients. The Python
Band indexes channels by ID alone, so per-peer reuse in a shared Band is unsafe.
Do not reserve arbitrary global IDs or reuse legacy KVM streams 71–73.

| Purpose | Telesthete primitive | Delivery / scheduling |
| --- | --- | --- |
| Discovery and initial hello | Existing Rook capability path | Targeted hello; retry by request ID, idempotent welcome |
| Surface control and discrete input | CHANNEL (0x02), dedicated ID | Reliable ordered; highest application queue priority |
| Clipboard data | Separate CHANNEL | Reliable ordered; below interactive control |
| View hints | STREAM (0x01), dedicated ID | Latest complete hint set; coalesce before enqueue |
| Pointer motion | Separate STREAM | Latest motion; highest application queue priority |
| Hot video | Separate STREAM per hot lane | Lossy; priority below control and cold updates |
| Cold blocks | Separate STREAM per cold lane | Application repair; priority above hot video |

CONTROL (0x00) remains the transport's own binary control protocol. Application
control is a reliable CHANNEL, not a new CONTROL message type. Use independent
bounded queues and schedule between datagrams, never hold one send lock across
an entire video frame. Clipboard and snapshot bulk work must not occupy the
input queue indefinitely; snapshot processing is bounded and cancellable.

Reliable messages use the library's Channel API: §6.1 reliability contains
§6.6 chunks in its data, with 1003 application bytes per chunk (1024 minus the
21-byte fragment envelope). This follows `protocol/channel.py`, resolving the
ambiguous “first bytes of plaintext” wording in §6.6. Do not double-fragment
messages when using its `send` API. A full encrypted Channel datagram is at
most 27 + 16 + 19 + 1024 = 1086 bytes before IP/UDP overhead.

For v1 surface Streams select the §5.1 priority-byte format; do not advertise
`dmabuf-v1` on this endpoint (it changes interpretation of all its Streams).
The media UDP payload budget is 1200 bytes including Telesthete header and tag:
27 + 16 + 1 + 12 leaves 1144 encoded fragment bytes. Respect a smaller discovered
path budget. Streams retain existing high-water behavior: reordered fragments
may be discarded by transport and are recovered as loss, not by weakening replay
checks. NACK repair uses fresh outer sequences and the original media tuple.

Direct-route setup and authenticated endpoint binding must be demonstrated in
M1. The current Rook adapter does not provide them. Relay fallback must be
explicitly visible to the client; an IP learned from untrusted JSON is not
sufficient proof of peer identity. Initial development uses configured test
peers. Band membership alone is not an origin subscription authorization policy.

## Common schemas

All control calls retain the Rook envelope above. `target` is mandatory for
surface messages. `args` contains `version: 1`, `session_id` (32 hex characters)
and the fields below; hello omits session ID. The origin creates a fresh random
session ID for each subscription. Every receiver checks target, session, peer
binding, message direction and negotiated capabilities before dispatch.

Integers are range-checked; booleans are not integers. `uN` and `iN` use their
usual unsigned and signed ranges. JSON u64 counters are decimal strings to
avoid precision loss in clients. Floats must be finite. `size` is
`{"w":u32,"h":u32}`; `rect` is `{"x":i32,"y":i32,"w":u32,"h":u32}`.
Unknown optional fields are ignored; unknown versions, event kinds and enum
values are rejected. Invalid mutations never partially update the replica.

Surface records use the fields in the design: `surface_id:u32`, `role`,
`parent_id:u32|null`, `anchor:rect`, `size`, `size_min`, `size_max`, `modal:bool`,
`app_id:string`, `title:string`, `z_hint:i32`, `flags:u32`, `content:null|object`.
Flags bits 0–3 are focused, urgent, fullscreen_req, decorated; other bits are zero.
Title is at most 256 UTF-8 bytes. Non-toplevel parents must exist; trees cannot
cycle. IDs are never reused within a session. Content is initially null; hot
content is `{lane_id,epoch,tile:rect,valid:rect}`; cold content is
`{lane_id,epoch,surface_rev}`. Cold pixels live in independent textures.

Layout: `{lane_id:u8,epoch:u16,canvas:size,tiles:[{surface_id,tile,valid}]}`.
`tile` is the outer aligned allocation; `valid` is the absolute atlas crop of
actual pixels. Outer origins and dimensions are multiples of 64; valid content
starts at least 16 pixels inside each edge. Edge replication fills gutters and
padding. Valid crops must be contained in their slot; slots cannot overlap.
This resolves the apparent conflict between 64-pixel alignment and 16-pixel
gutters: align allocations, not the inner content dimensions.

Client caps: `decoders:[{codec,max_w,max_h,max_fps,max_instances,chroma}]`,
`tile_codecs:["qoi"|"zstd-bgra"]`, `input:[string]`,
`display:{refresh_hz,kind:"flat"|"spatial"}`. Codec values are `av1`, `hevc`,
`h264`; baseline chroma is `420-8`. Origin caps:
`encoders:[{codec,max_sessions,roi_support,intra_refresh_support}]`,
`input_fidelity:"full"|"focused-only"|"none"`, `clipboard:bool`.
Display kind never changes encode policy. At most one hot and one cold lane
per subscription; cold compression does not consume a video encoder session.

## Control catalog

Unless indicated, fields below are required in addition to common args.

| Cap | Direction | Fields |
| --- | --- | --- |
| `surface-origin` | Discovery query → origin | Empty; reply `{display_name,surface_count,version}`; advertise cap in normal worker announce |
| `surface-hello` | Client → origin | `caps` (client caps) |
| `surface-welcome` | Origin → client | `hello_id`, `caps` (origin caps), `channels:{control,clipboard,hints,motion}`, `lanes:[{lane_id,kind,stream_id,codec}]`, `route:"direct"|"relay"` |
| `surface-snapshot` | Origin → client | `tree_rev:u64`, `surfaces:[surface]`, `layouts:[layout]` |
| `window-catalog` | Origin → client | `catalog_rev:u64`, `surfaces:[surface]` for all currently capturable windows |
| `window-select` | Client → origin | `roots:[surface_id]` (up to 16 current top-level IDs); replaces this client's selection |
| `surface-add` | Origin → client | `tree_rev`, `surface` (complete record) |
| `surface-update` | Origin → client | `tree_rev`, `surface_id`, `changes` (partial mutable record; no ID mutation) |
| `surface-remove` | Origin → client | `tree_rev`, `surface_id`; remove children first |
| `atlas-layout` | Origin → client | Layout fields; reliable before media for that epoch |
| `view-hint` | Client → origin | `seq:u64`, `hints:[{surface_id,visible,display_px,fovea:u8,focused:bool,priority:u8}]` |
| `surface-input` | Client → origin | `event` (union below); motion also has `seq:u64` |
| `focus-request` | Client → origin | `surface_id` |
| `resize-request` | Client → origin | `surface_id`, `w:u32`, `h:u32` |
| `close-request` / `hide-request` | Client → origin | `surface_id` |
| `clipboard-offer` | Either | `offer_id`, `mime_types:["text/plain;charset=utf-8"]`, `byte_length:u32` |
| `clipboard-request` | Either | `offer_id`, `mime_type` |
| `clipboard-data` | Either, clipboard Channel | `offer_id`, `mime_type`, `data_b64:string` (UTF-8 base64); decoded text ≤1 MiB |
| `lane-resync` | Client → origin | `lane_id`, `epoch:u16|null`, `frame_seq:u32|null` (null before any good frame) |
| `snapshot-request` | Client → origin | `tree_rev:u64` |
| `lane-nack` | Client → origin | `lane_id`, `epoch`, `frame_seq`, `missing:[u16]` fragment indexes |

`lane-nack` is added because the design requires fragment repair but had no
message for requesting it. A welcome echoes the hello request ID; retries of
the same hello return the same live session. Unsupported versions or lack of
compatible codecs return the normal Rook error reply. Requests are deduplicated
by `(session_id,id)`; input/clipboard side effects must not run twice. An `ok`
reply acknowledges acceptance, while tree deltas remain authoritative for WM
focus, resize, close and hide outcomes.

The catalog is separate from the subscribed surface tree: listing a window
does not begin capture. The origin sends a full catalog at welcome and whenever
it changes. Catalogs are bounded to 512 records with valid, acyclic parent
relationships; `window-select` is admitted only for authenticated, current
top-level IDs. Owned children follow the selected root in the streamed tree.

Input union: each event includes `kind` and `surface_id`.

| Kind | Additional fields |
| --- | --- |
| `motion` | `x`, `y` (finite origin-pixel coordinates) |
| `button` | `button:u16` (Linux evdev BTN code), `pressed:bool` |
| `axis` | `dx`, `dy` (finite pixel deltas), `discrete:bool`; when discrete, deltas are wheel steps |
| `key` | `keycode:u16` (evdev), `pressed:bool`, `modifiers:u32` |
| `text-commit` | `text:string` (UTF-8) |
| `touch` | `slot:u8`, `phase:"down"|"move"|"up"|"cancel"`, `x`, `y` |

Modifier bits 0–5: shift, control, alt, meta, caps lock, num lock; remaining
bits zero. Key events are authoritative; the modifier bitmap describes state.
Only motion is latest-wins; touch and all other events use reliable control.
On disconnect, release injected keys/buttons and cancel touches. A modal child
blocks input to its parent; input injection also obeys advertised fidelity.

## Media schemas and state

After the Stream priority byte, use the design's 12-byte big-endian header
`!BHIHHB`: lane ID, epoch, frame sequence, fragment index, fragment count, flags.
Flags bit 0 = keyframe, bit 1 = intra-refresh-complete; all others zero.
Fragment indexes start at zero, counts are nonzero, and all fragments of a
logical frame agree on epoch/count/flags. Hot bodies are codec access units.
Lane kind negotiated at welcome distinguishes `lane-frame` from `cold-blocks`.
No extra JSON envelope wraps media.

Cold frame body: `block_count:u16`, followed by that many records, all BE:
`surface_id:u32, block_x:u32, block_y:u32, surface_rev:u64, w:u16, h:u16,
codec:u8, payload_len:u32, payload:bytes`. Block coordinates are 64-pixel grid
indexes, dimensions 1–64 (clipped at edges), codecs 0=QOI and 1=zstd-BGRA.
Decoded QOI is RGBA; zstd output is tightly packed BGRA. Both describe sRGB
straight-alpha pixels; the compositor converts to its texture convention.
Decompressed size must be exactly `w*h*4`. Apply only to a matching live
surface and epoch. Track revision per block, so out-of-order batches for
separate blocks do not discard valid updates. Revisions never wrap in-session.

Tree deltas advance revision by exactly one. A gap stops delta application and
requests an atomic snapshot. Ignore duplicate/older deltas. A reconnect discards
old session state, obtains a snapshot and refreshes content. Atlas layouts keep
the two newest epochs; unknown media epochs are discarded and trigger a
rate-limited resync. Switch hot crops atomically when a decodable frame for the
new layout arrives; keep old pixels until then. Epoch and frame counters must
not wrap in a live lane: renegotiate a fresh session before exhaustion.

Discard incomplete hot frames older than the latest completed frame. Cold
batches are independently repairable and must NOT follow that discard rule:
a later completed batch need not contain earlier damaged blocks. Repair cold
loss until complete or replace it with a full surface refresh. Cache keyframes,
first-epoch frames and cold batches for bounded repair; never retransmit ordinary
hot delta frames. Missing repair data produces a fresh keyframe/full cold
refresh. Thaw always refreshes the affected surface. Lane migration keeps the
old texture until the destination is ready.

Hints contain the full subscribed set. Visibility values are `in_view`,
`peripheral`, `out_of_view`, `hidden`; fovea and priority are u8. Missing hints
conservatively freeze a surface until a fresh set arrives. Hints and motion
have separate latest-wins counters. Hints normally emit at most 10 Hz with
immediate visibility or >25% display-size changes. Large hint sets may use
bounded application fragmentation with atomic reassembly; M1 must either
implement that framing or cap subscriptions so a complete hint fits one datagram.

## Bounds and M1 checks

Initial limits: 256 surfaces/session; 2 MiB encoded JSON message; 16 MiB media
frame; 32 MiB aggregate incomplete media/session; 64 KiB text commit; 1 MiB
clipboard text. Limit fragment count by frame size and the negotiated datagram
budget before allocating. Evict stale incomplete video after 250 ms; repair
cache holds at most 32 MiB/session for at most 2 s. A cache miss uses resync.
Coalesce repeat resync/NACK requests (at most 10/s/lane). Exhausted control
queues close the session and release input rather than silently dropping keys.

These are prototype limits, not measured performance claims. M1 must demonstrate:

- A multi-chunk 20+ surface snapshot round-trips without partial tree mutation.
- Lost/reordered control fragments recover while input remains responsive under
  video saturation; clipboard bulk transfer does not block input.
- Stale-session traffic, bad crops, oversized payloads and duplicate requests
  are rejected; two origins cannot collide on IDs or route input to each other.
- Media epoch reorder and cold batch loss recover without stale crops or text.
- Direct peer delivery and visible relay fallback, with configured authorization.
- One usable remote terminal with timestamp overlay, glass-to-glass latency,
  lane bitrate, encode/decode frame timings and repacks/minute recorded.

M0 is a schema/source audit, not evidence of these runtime checks. Runtime work
starts with protocol types, a thin transport adapter and M1 capture/client/input;
policy remains a separately testable pure module as the design requires.

## Prototype amendments

The native implementation uses configured peer sockets and a matching
`--channel-base` to bootstrap a real reliable Channel. Its welcome echoes that
allocation. Each subscription owns a Band, preventing channel-ID collisions.
Band discovery and the initial Rook capability path remain integration work.

Additional control messages: `lane-status` (origin → client) carries
`lane_id:u8, epoch:u16, frame_seq:u32` after a batch is queued, allowing detection
of an entirely lost batch. `surface-ping` (client → origin) has only common args
and maintains a 1.5-second native input lease. Missing batches trigger resync;
missing known fragments trigger NACK. A button event can additionally carry
`x,y` with motion's validation rules; these coordinates are applied before the
button, avoiding a race with its independent latest-wins motion stream.

All hint sets are fragmented with the 12-byte media header on the hint Stream:
lane ID 2, epoch 0, frame sequence equal to the hint sequence. The hint sequence
is limited to u32 for this framing version. Apply only complete newest sets and
replace unsent old sets as a group. This resolves the earlier bounded-hint-set
implementation choice. It does not allocate another codec lane.

Desktop lifecycle additions: `surface-release` (client → origin) has only common
args and releases held input on focus loss or client-view close. A valid crop
change inside an existing atlas allocation advances the immutable layout epoch
and forces a keyframe, but keeps the allocation, canvas and encoder. This avoids
packing churn without allowing late frames to use a newer crop rectangle.

Adaptive lane selection uses reliable `surface-lane` (origin → client):
`{surface_id:u32,lane_id:u8,first_frame:u32}`. `first_frame` is a fence in the
subscription-wide monotonically increasing media sequence. A client accepts
only the selected lane at or above that fence for the surface. It stages a cold
refresh separately and retains the displayed texture until every block arrives.
A new mapping requests destination resync because media may outrun control.
Snapshots resend active mappings; a repeated mapping is idempotent.

The desktop runtime now opts into text clipboard sync with `--clipboard` on
both peers. `clipboard-offer` and `clipboard-request` use the control Channel;
`clipboard-data` travels on the distinct reliable clipboard Channel. Base64
keeps the 1 MiB text limit inside the bounded 2 MiB JSON control envelope,
including text rich in JSON escape characters. Request IDs and offer IDs are
checked, and an intervening local copy wins over a delayed remote reply.

Small live tree changes use ordered `surface-add`, `surface-update`, and
`surface-remove` deltas. The sender chooses an order valid at every revision and
falls back to a full snapshot for complex churn. The receiver latches a revision
gap and requests a snapshot. Snapshots carry the current hot layout as well as
the tree and any per-surface lane mappings.
