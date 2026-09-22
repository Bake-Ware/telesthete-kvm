# Windows acceptance output — 2026-09-17

Rook-controlled WIN11-FLOPHOUSE ↔ Soundwave VM 132. Assertions inspected actual
Tk desktop events, KVM status files, and OS clipboard hashes.

```text
27 passed in 22.37s (Windows Python 3.12.10)
PASS Linux to Windows exact typed text
PASS Linux source suppresses forwarded keys and clicks
PASS Windows receives scroll wheel
PASS Windows releases shifted key after modifier release
PASS Windows receives relative mouse movement
PASS Linux source returns across Windows edge
PASS Windows physical mouse edge captures Linux focus
PASS Windows hardware keyboard forwards exact text to Linux
PASS Windows suppresses physical keys and clicks on source
PASS Windows physical scroll reaches Linux
PASS Windows physical emergency hotkey restores local control
PASS Windows OS clipboard reaches Linux
PASS Linux OS clipboard reaches Windows
PASS Windows repeat handoff
PASS Windows shifted key releases after modifier release
PASS Windows physical relative motion reaches Linux
PASS Windows source returns across Linux edge
PASS Windows handoff before crash test
PASS Windows held key reaches Linux
PASS Windows process crash expires Linux focus lease
PASS Windows process crash releases remote held key
PASS Windows restart reconnects Linux peer
PASS Large Unicode clipboard Windows to Linux (144000 bytes)
PASS Large Unicode clipboard Linux to Windows hash matches
PASS Windows clipboard clearing reaches Linux
PASS Clipboard change after clearing
PASS Linux clipboard clearing reaches Windows
PASS Linux handoff after Windows restart
PASS Linux process crash expires Windows focus lease
PASS Linux process crash releases Windows held key
PASS Windows physical local input works after KVM shutdown
PASS Windows original clipboard restored after stopping both KVM processes
```

