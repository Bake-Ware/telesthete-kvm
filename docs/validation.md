# Validation — 2026-09-17

## Result

The implemented 0.2 application passed automated protocol tests and real X11
capture/injection tests across two newly created Proxmox VMs. This verifies the
Linux X11 path. A subsequent Rook run on **WIN11-FLOPHOUSE** passed native Windows
capture/injection and clipboard acceptance checks against VM 132 (see below).
The added GitHub Actions matrix has not been run remotely.

## Automated checks

- **27 tests passed**, locally on Python 3.14 and on Debian 12/Python 3.11 in VM 132.
- Real encrypted UDP sockets with simulated desktop adapters: focus grant/return,
  input ordering, packet loss, temporary network blackhole, peer restart,
  wrong-token rejection, conflicting requests, busy targets, layout mismatch,
  clipboard limits/integrity, large Unicode text, clear/update propagation.
- WebSocket relay tests: three-peer routing, relay connection loss/reconnection.
- Geometry: per-peer monitor IDs, partial edges, negative/explicit OS coordinates,
  invalid layouts and overlaps.
- Ruff lint/format checks, `git diff --check`, dependency consistency, CLI help and
  configuration validation passed. Wheel and source archive builds succeeded.

## Proxmox / Rook test environment

Created and controlled through Rook on Soundwave:

| VM | Name | Configuration |
|---|---|---|
| 132 | kvm-test-132 | Debian 12, 2 vCPU, 1536 MiB RAM, 10 GiB disk |
| 133 | kvm-test-133 | Debian 12, 2 vCPU, 1536 MiB RAM, 10 GiB disk |

Both use the official Debian generic cloud image checked against its published
SHA-512 checksum. Each runs a separate 800×600 Xvfb server, pynput input capture,
and a Tk window that records OS-delivered events. `xdotool` generates input on
the source guest. Assertions inspect the receiving window's events and actual
OS clipboard rather than substituting application adapters.

The full scenario passed in **three configurations**:

1. Explicit direct UDP peer addresses.
2. UDP broadcast LAN discovery, with no configured peer addresses.
3. WebSocket connections through the actual Rust `telesthete-hub` relay.

Each run checked:

- Peer connection and identical-layout negotiation.
- Screen-edge handoff and return.
- Exact delivery of typed text to the remote window.
- No forwarded keyboard or mouse-button events reaching the source window.
- Remote clicks and relative pointer motion.
- Shifted key release after releasing the modifier.
- Clipboard changes in both directions, large Unicode text, and clearing.
- Forced source-process death while a remote key is held, followed by release of
  that key and expiry of remote focus.
- Source restart/reconnection and a new handoff.
- **Ctrl+Alt+Esc** emergency return.

The original Ctrl+Alt+F12 choice failed on X11 because that chord can be intercepted
for virtual-console switching. It was replaced with Ctrl+Alt+Esc and retested.

The relay binary built with the workstation's system Rust toolchain raised an
illegal-instruction fault on Soundwave's older CPU, including a build requesting
baseline x86-64. For the passing relay test, the actual relay ran on the workstation
and a temporary SSH reverse tunnel plus a TCP forwarder carried its WebSocket
endpoint to the VMs. No application mocks were used in that run. This establishes
relay interoperability, not public-internet latency, NAT behavior, or native WSS
certificate deployment. The temporary relay and forwarding processes were stopped
after testing.

See [captured successful output](test-output.md). Rook console records:

- Final LAN and Python 3.11 validation: `d62707b0022d400e`.
- Final actual-relay desktop validation: `ad575c97e75a4d7d`.

## Windows acceptance — 2026-09-17

Rook installed commit `a05d904` on the existing **WIN11-FLOPHOUSE** Windows 11
AMD64 VM, in its active console session. This VM is the workstation's libvirt
domain `win11`, not a Soundwave Proxmox guest. Its peer was Soundwave VM 132.
Windows used Python 3.12.10, pynput 1.8.2, and a 1280×800 desktop; Linux used
the existing 800×600 Xvfb desktop. Both ran the real KVM application and Tk
event probes. **All 27 automated tests also passed on Windows** (22.37 seconds),
as did dependency consistency and CLI checks.

Git was absent on Windows. Rook transferred the source archive and locally built
KVM/Telesthete wheels, whose SHA-256 hashes matched the originals. Telesthete was
built from the pinned dependency revision. Other dependencies were installed
with pip into `C:\Users\bake\telesthete-kvm-test\.venv`. This validates wheel
installation; the Git-based `install.ps1` / `install.bat` path was not exercised.

The Windows peer initiated direct encrypted UDP to `192.168.1.200:9999` across
the workstation's libvirt NAT. Both peers negotiated the same layout:

| Peer | Global origin | Size |
|---|---|---|
| windows | 0, 0 | 1280×800 |
| linux | 1280, 0 | 800×600 |

Linux source events came from `xdotool`. Windows source events came from QEMU
virtual keyboard, mouse, and tablet devices through libvirt `input-send-event`.
This matters because the Windows backend deliberately ignores software-injected
input. Rook SendInput was used only to focus the test window, not as evidence of
Windows source capture. Probe window focus was explicitly restored before key
assertions; remote command consoles can take focus on Windows.

Passed with actual OS events and clipboard contents:

- Exact text in both directions (`hello` and `world`), clicks, and scroll wheel.
- Source keyboard and mouse-button suppression on both operating systems.
- Screen-edge handoff and return, and relative pointer movement in both directions.
- Shifted key release after releasing Shift, on both receiving operating systems.
- Windows physical **Ctrl+Alt+Esc** emergency return.
- Clipboard updates and clearing in both directions; large Unicode transfers
  (144,000 bytes Windows to Linux, and a larger Linux-to-Windows payload), checked
  with SHA-256 hashes.
- Forced process termination on each source while a key was held: receiver focus
  expired and the remote OS received the key release.
- Windows restart/reconnection and subsequent handoff.
- Normal Windows physical input after stopping the KVM application.

No application backend fixes were required by these checks. The probe now supports
configurable geometry and records Windows wheel events. See
[Windows acceptance evidence](windows-test-output.md) for the recorded assertions.

The Windows firewall prompt was dismissed; the outbound-initiated peer connection
worked without adding an inbound rule. This run does **not** establish Windows
LAN broadcast discovery, unsolicited inbound connectivity, relay operation on
Windows, elevated/secure-desktop input, multiple monitors, or non-US layouts.
The prompt itself demonstrated that secure-desktop UI can prevent input from
reaching the normal test window.

Test applications were stopped, the original Windows clipboard was restored,
the QEMU mouse selection was restored, and VM 132 was powered off. Windows remains
running. Its isolated installation, layout, probes, and test logs remain in
`C:\Users\bake\telesthete-kvm-test` for reuse; no startup task was installed.
That folder also contains `run-test.ps1`, which starts the Windows peer using the
test-only shared secret and saved layout. Before rerunning, start VM 132, confirm
its DHCP address, then start Xvfb, the probe, and the Linux KVM peer as described
below. Update the Windows launcher if the address changed.

The Linux peer's command for this layout is:

```sh
cd /root/kvm
DISPLAY=:99 .venv/bin/python -m kvm --psk windows-vm-test-only \
  --hostname linux --layout /tmp/kvm-layout.json --no-discovery \
  --status-file /tmp/kvm-status.json
```

The test secret is only for these disposable acceptance sessions. Configure a new
shared secret and a matching layout before using the installation on other peers.

Rook console records:

- Installation and 27 passing tests: `14b8b35560664b7d`.
- Initial desktop run: `4421568592574b3b`.
- Restart and clipboard/recovery run: `56b1dc6e571348d7`.
- Native event probe: `14fd5403f5cb4910`.

## Reproduce

Run `python -m pytest -q` after installing `.[test]`.
For the VM scenario, provision two disposable Debian X11 test guests with root
SSH access, the project installed at `/root/kvm`, and `Xvfb`, `xdotool`, `xclip`,
and `python3-tk`. Copy the included `scripts/desktop_probe.py` into each checkout.
From their SSH controller:

```sh
python3 scripts/vm_test.py --a VM_A_IP --b VM_B_IP
python3 scripts/vm_test.py --a VM_A_IP --b VM_B_IP --discovery
python3 scripts/vm_test.py --a VM_A_IP --b VM_B_IP --hub ws://RELAY_HOST:PORT/band
```

The harness intentionally kills the source KVM process to exercise recovery. Use
only dedicated test desktops. Both created VMs are retained, powered off, for
future reruns; source and test artifacts remain under `/root/kvm` in each guest
and `/var/lib/vz/telesthete-kvm-test` on Soundwave. DHCP addresses may change.
