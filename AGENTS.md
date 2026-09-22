# Repository direction

This repository implements **Telesthete Spatial Surfaces**. Read
`docs/Telesthete Spatial Surfaces — Design Doc.md`,
`docs/spatial-surfaces-protocol.md`, and `docs/spatial-prototype.md` before
changing the spatial runtime. The prototype guide distinguishes implemented
behavior from the larger design and lists outstanding acceptance checks.

- All implementation belongs here. Do not make or push implementation changes
  to `~/rook`; it is a read-only reference for the existing Rook wire integration.
- The authoritative Rook checkout is `~/rook` on cachyrig. Read the actual frame,
  crypto and channel implementation before changing transport assumptions.
- Use `win11-flophouse` for Windows native validation. Keep test installs and
  probes isolated under `C:\Users\bake\telesthete-spatial-test` and stop test apps.
- Keep protocol, tree, packing, loss handling and encode policy platform-neutral.
  Import desktop/capture dependencies lazily in adapters and shells.
- Preserve the existing `kvm/` CLI while bringing up spatial surfaces. Its docs
  are in `docs/legacy-kvm.md`; its native tests are not spatial acceptance evidence.
- Run relevant tests and record actual native results. Do not mark a milestone
  complete from unit tests or synthetic images alone. Record limitations and
  unfinished work explicitly in the design findings and prototype guide.
