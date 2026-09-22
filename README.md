# Telesthete Spatial Surfaces

This repository is becoming Telesthete's spatial surface server and client:
stream individual application windows from Linux and Windows origins into a
client-owned desktop or spatial workspace. Clients composite windows locally;
head motion stays local. A hot video atlas carries motion, while lossless cold
tiles keep static text sharp. View hints drive origin-side encoding policy.

The [Spatial Surfaces design](docs/Telesthete%20Spatial%20Surfaces%20%E2%80%94%20Design%20Doc.md)
is the product direction. Implementation lives here in `telesthete-kvm`;
`~/rook` on cachyrig remains the authoritative Rook integration reference.

## Status

A native desktop prototype runs here now: KWin/PipeWire and Windows WGC
origins, a Qt window browser and flat viewer, encrypted direct transport,
lossless damage tiles, and a single H.264 atlas encoder. The browser lists
local and configured remote origin windows and opens selected windows in the
viewer. Six real KWin windows have streamed through one NVENC session. Windows
capture, input and streaming were exercised on **win11-flophouse**.

On a configured Linux desktop, run `python -m surfaces ui` to browse and open
local windows. Add `--origin-config origins.json` to list remote origins too;
see the [prototype setup and commands](docs/spatial-prototype.md).

Start with the [prototype setup and commands](docs/spatial-prototype.md),
[validation evidence](docs/spatial-test-output.md), and
[M0 protocol delta](docs/spatial-surfaces-protocol.md).
The full spatial-client roadmap is still in progress; the prototype guide
identifies the unimplemented features rather than treating unit-tested modules
as completed milestones.

The existing `kvm/` application and `telesthete-kvm` command remain available;
see the [legacy KVM guide](docs/legacy-kvm.md). The new entry point is
`telesthete-surfaces` (or `python -m surfaces`).

## Milestones

1. M0 — Message schemas and channel mapping; no runtime code.
2. M1 — One Linux window and a flat Linux client, with input and measurements.
3. M2 — Surface tree, anchored popups, focus and resize.
4. M3 — Hot atlas, six windows through one encoder, safe epoch changes.
5. M4 — Lossless cold tiles and hot/cold migration.
6. M5 — View hints, freeze/thaw, resolution and bandwidth policy.
7. M6 — Quest and Android spatial clients; multiple origins in one space.
8. M7 — Windows origin with explicit input-fidelity limits.

Protocol types and encoding policy belong in a platform-neutral library with
unit tests. Platform adapters and client shells stay thin. Each runtime milestone
ends with a demo and findings in the design doc. Audio and macOS are outside v1.

## Development

```sh
python -m pip install -e '.[test]'
python -m pytest -q
python -m build
```

The [existing architecture](docs/architecture.md) and
[validation results](docs/validation.md) describe the legacy KVM implementation,
not completed spatial-surface functionality.

## License

MIT. Dependencies retain their own licenses.
