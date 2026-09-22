"""Run desktop integration checks against two dedicated SSH-accessible Linux VMs.

Run this on the Proxmox host with --a and --b set to test VM addresses. Requires
root SSH access, installed source at /root/kvm, and Xvfb/X11 tools in each guest.
Nothing is installed on or injected into the Proxmox host desktop.
"""

import argparse
import json
import shlex
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument("--a", required=True)
p.add_argument("--b", required=True)
p.add_argument("--hub")
p.add_argument("--discovery", action="store_true")
a = p.parse_args()


def ssh(host, cmd, check=True):
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", f"root@{host}", cmd],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if check and r.returncode:
        raise RuntimeError(f"{host}: {cmd}: {r.stderr} {r.stdout}")
    return r.stdout.strip()


def wait(fn, label, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            print("PASS", label, flush=True)
            return
        time.sleep(0.2)
    raise AssertionError(label)


def status(host):
    try:
        return json.loads(ssh(host, "cat /tmp/kvm-status.json", False))
    except ValueError:
        return {}


def xdo(host, cmd):
    return ssh(host, "DISPLAY=:99 xdotool " + cmd)


def events(host):
    raw = ssh(host, "cat /tmp/kvm-probe.jsonl", False)
    return [json.loads(line) for line in raw.splitlines() if line]


def clip(host):
    return ssh(host, "DISPLAY=:99 xclip -selection clipboard -o", False)


def start(host, name):
    peer = a.b if host == a.a else a.a
    extra = (
        "--hub " + shlex.quote(a.hub)
        if a.hub
        else ("" if a.discovery else "--peer " + peer + ":9999")
    )
    discovery = "" if a.discovery else "--no-discovery "
    cmd = (
        "cd /root/kvm; DISPLAY=:99 nohup .venv/bin/python -m kvm "
        "--psk vm-integration-only --hostname "
        + name
        + " --layout /tmp/kvm-layout.json "
        + discovery
        + "--status-file /tmp/kvm-status.json "
        + extra
        + " >/tmp/kvm.log 2>&1 </dev/null & echo $! >/tmp/kvm.pid"
    )
    ssh(host, cmd)


layout = [
    dict(id=0, peer="a", x=0, y=0, width=800, height=600),
    dict(id=0, peer="b", x=800, y=0, width=800, height=600),
]
for host in (a.a, a.b):
    ssh(host, "test ! -f /tmp/kvm.pid || kill $(cat /tmp/kvm.pid) 2>/dev/null || true")
    ssh(host, "pkill -f '^python3 /root/kvm/scripts/desktop_probe.py' || true")
    ssh(host, "rm -f /tmp/kvm-status.json /tmp/kvm-probe.jsonl")
    ssh(
        host,
        "printf reset | DISPLAY=:99 xclip -selection clipboard >/dev/null 2>&1",
        False,
    )
    ssh(
        host,
        "pgrep -x Xvfb >/dev/null || (nohup Xvfb :99 -screen 0 800x600x24 -ac +extension RECORD >/tmp/xvfb.log 2>&1 </dev/null &)",
    )
    ssh(host, "printf %s " + shlex.quote(json.dumps(layout)) + " >/tmp/kvm-layout.json")
    ssh(
        host,
        "DISPLAY=:99 nohup python3 /root/kvm/scripts/desktop_probe.py >/tmp/probe.log 2>&1 </dev/null &",
    )
    xdo(host, "mousemove 300 300")
    start(host, "a" if host == a.a else "b")
wait(
    lambda: status(a.a).get("peers") == ["b"] and status(a.b).get("peers") == ["a"],
    "peer discovery and layout negotiation",
)
xdo(a.a, "mousemove 799 250")
wait(
    lambda: status(a.a).get("target") == "b" and status(a.b).get("owner") == "a",
    "screen-edge focus handoff",
)
xdo(a.a, "type --delay 70 hello")
wait(
    lambda: "".join(e["char"] for e in events(a.b) if e["type"] == "2") == "hello",
    "remote OS receives exact typed text",
)
assert not [e for e in events(a.a) if e["type"] == "2"], (
    "Source desktop received forwarded keys"
)
print("PASS source keyboard suppression", flush=True)
xdo(a.a, "click 1")
wait(
    lambda: any(e["type"] == "4" and e["button"] == 1 for e in events(a.b)),
    "remote OS receives mouse click",
)
assert not [e for e in events(a.a) if e["type"] == "4"], (
    "Source desktop received forwarded mouse click"
)
print("PASS source mouse suppression", flush=True)
xdo(a.a, "keydown Shift_L keydown a keyup Shift_L keyup a")
wait(
    lambda: any(e["type"] == "3" and e["key"] in ("a", "A") for e in events(a.b)),
    "shifted key releases after modifier releases",
)
xdo(a.a, "mousemove_relative -- 100 20")
wait(lambda: status(a.a).get("position") == [904, 270], "relative mouse movement")
xdo(a.a, "mousemove_relative -- -115 0")
wait(
    lambda: status(a.a).get("target") is None and status(a.b).get("owner") is None,
    "return across screen edge",
)
ssh(
    a.a,
    "printf 'clipboard from a' | DISPLAY=:99 xclip -selection clipboard >/dev/null 2>&1",
)
wait(lambda: clip(a.b) == "clipboard from a", "OS clipboard a to b")
ssh(
    a.b,
    "printf 'clipboard from b' | DISPLAY=:99 xclip -selection clipboard >/dev/null 2>&1",
)
wait(lambda: clip(a.a) == "clipboard from b", "OS clipboard b to a")
unicode_code = "import sys; sys.stdout.write('héllo 🌍\\n' * 12000)"
ssh(
    a.a,
    "python3 -c "
    + shlex.quote(unicode_code)
    + " | DISPLAY=:99 xclip -selection clipboard >/dev/null 2>&1",
)
expected_hash = ssh(
    a.a, "DISPLAY=:99 xclip -selection clipboard -o | sha256sum"
).split()[0]
wait(
    lambda: (
        ssh(a.b, "DISPLAY=:99 xclip -selection clipboard -o | sha256sum").split()[0]
        == expected_hash
    ),
    "large Unicode OS clipboard transfer",
    timeout=30,
)
ssh(a.b, "printf '' | DISPLAY=:99 xclip -selection clipboard >/dev/null 2>&1")
wait(lambda: clip(a.a) == "", "clearing OS clipboard synchronizes")
xdo(a.a, "mousemove 799 250")
wait(lambda: status(a.a).get("target") == "b", "second handoff")
xdo(a.a, "keydown z")
wait(
    lambda: any(e["type"] == "2" and e["key"] == "z" for e in events(a.b)),
    "remote held key",
)
ssh(a.a, "kill -9 $(cat /tmp/kvm.pid)")
wait(lambda: status(a.b).get("owner") is None, "source crash expires focus lease")
wait(
    lambda: any(e["type"] == "3" and e["key"] == "z" for e in events(a.b)),
    "source crash releases held key in remote OS",
)
xdo(a.a, "keyup z mousemove 300 300")
start(a.a, "a")
wait(
    lambda: status(a.a).get("peers") == ["b"] and status(a.b).get("peers") == ["a"],
    "restart reconnects peers",
)
xdo(a.a, "mousemove 799 250")
wait(lambda: status(a.a).get("target") == "b", "handoff after restart")
xdo(a.a, "key ctrl+alt+Escape")
wait(
    lambda: status(a.a).get("target") is None and status(a.b).get("owner") is None,
    "emergency hotkey restores local control",
)
for host in (a.a, a.b):
    ssh(host, "kill $(cat /tmp/kvm.pid)")
print("ALL DESKTOP INTEGRATION CHECKS PASSED", flush=True)
