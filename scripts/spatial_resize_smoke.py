"""Native origin -> encrypted UDP -> Qt client resize acceptance on either OS.

Run from the repository root with the spatial dependencies installed. Owns and
cleans up only its dedicated probe, origin process and temporary test secret.
"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Running as a script must still import this checkout's package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surfaces.client import FlatClient  # noqa: E402
from surfaces.model import Size  # noqa: E402
from surfaces.session import ClientSession  # noqa: E402
from surfaces.transport import DirectLink  # noqa: E402


def port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def check(origin_port, client_port, secret):
    link = DirectLink(
        secret,
        "client",
        ("127.0.0.1", client_port),
        ("127.0.0.1", origin_port),
        expected_peer="origin",
        channel_base=100,
    )
    client = ClientSession(link, "client", "origin")
    shell = FlatClient(client)
    requested = False
    deadline = time.monotonic() + 15
    try:
        await link.start(timeout=10)
        client.hello()
        while time.monotonic() < deadline:
            shell.poll()
            client.hints(shell.hints())
            client.ping()
            client.repair()
            if shell.windows:
                sid, window = next(iter(shell.windows.items()))
                if not requested:
                    window.resize(640, 320)
                    requested = True
                elif client.tree.surfaces[sid].size == Size(640, 320):
                    texture = client.textures[sid]
                    if texture.ready and texture.size == Size(640, 320):
                        assert not client.errors, client.errors
                        print(
                            json.dumps(
                                {
                                    "source_size": [640, 320],
                                    "texture_size": [texture.size.w, texture.size.h],
                                    "errors": client.errors,
                                }
                            ),
                            flush=True,
                        )
                        return
            await asyncio.sleep(0.02)
        raise AssertionError("native source and client texture did not reach 640x320")
    finally:
        shell.stop()
        await asyncio.sleep(0.05)
        await link.stop()


def main():
    processes, logs = [], []
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with tempfile.TemporaryDirectory(prefix="spatial-resize-") as temporary:
        directory = Path(temporary)
        secret = secrets.token_urlsafe(32)
        key = directory / "test.psk"
        key.write_text(secret)
        os.chmod(key, 0o600)

        def launch(arguments, name):
            log = (directory / (name + ".log")).open("w")
            logs.append(log)
            process = subprocess.Popen(
                [sys.executable, *arguments],
                stdout=log,
                stderr=log,
                creationflags=flags,
            )
            processes.append(process)
            return process

        try:
            probe = (
                "spatial_native_probe.py"
                if sys.platform == "win32"
                else "spatial_qt_probe.py"
            )
            launch(["scripts/" + probe, "--seconds", "30"], "probe")
            time.sleep(2)
            origin_port, client_port = port(), port()
            origin = launch(
                [
                    "-m",
                    "surfaces",
                    "origin",
                    "--title",
                    "Telesthete Spatial Native Probe",
                    "--name",
                    "origin",
                    "--peer-name",
                    "client",
                    "--bind",
                    f"127.0.0.1:{origin_port}",
                    "--peer",
                    f"127.0.0.1:{client_port}",
                    "--psk-file",
                    str(key),
                    "--seconds",
                    "12",
                ],
                "origin",
            )
            asyncio.run(check(origin_port, client_port, secret))
            assert origin.wait(timeout=20) == 0
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                process.wait(timeout=5)
            for log in logs:
                log.close()
            for path in directory.glob("*.log"):
                content = path.read_text()
                if content:
                    print(path.name + ": " + content, file=sys.stderr)


if __name__ == "__main__":
    main()
