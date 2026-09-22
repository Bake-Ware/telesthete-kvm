"""Native configured-peer prototype. Run `python -m surfaces --help`."""

import argparse
import asyncio
import json
import os
import secrets
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .model import Size
from .session import ClientSession, OriginSession
from .transport import DirectLink


def address(value):
    host, port = value.rsplit(":", 1)
    port = int(port)
    if not 0 < port <= 65535:
        raise argparse.ArgumentTypeError("invalid port")
    return host, port


def parser():
    parser = argparse.ArgumentParser(
        description="Telesthete Spatial Surfaces native window-streaming prototype"
    )
    parser.add_argument("mode", choices=("list", "origin", "client", "ui"))
    parser.add_argument(
        "--backend",
        choices=("kwin", "windows"),
        default="windows" if sys.platform == "win32" else "kwin",
    )
    parser.add_argument(
        "--bridge", default="/tmp/telesthete-kwin-build/telesthete-kwin-capture"
    )
    parser.add_argument(
        "--title",
        action="append",
        help="Initially stream an exact title; omit for browser-controlled selection",
    )
    parser.add_argument("--lane", choices=("cold", "hot", "auto"), default="cold")
    parser.add_argument(
        "--encoder", choices=("nvenc", "vaapi", "software", "portable"), default="nvenc"
    )
    parser.add_argument("--bind", type=address, default=("0.0.0.0", 10000))
    parser.add_argument("--peer", type=address)
    parser.add_argument("--name", default="origin")
    parser.add_argument("--peer-name", default="client")
    parser.add_argument("--channel-base", type=int, default=100)
    parser.add_argument("--psk-file", type=Path)
    parser.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="Stop after this many seconds (0: until interrupted)",
    )
    parser.add_argument("--stats", type=Path)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Decode client textures without opening windows",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Opt in to text clipboard sync with this peer (up to 1 MiB)",
    )
    parser.add_argument(
        "--origin-config",
        type=Path,
        help="Client/UI JSON file with up to four configured remote origins",
    )
    return parser


def adapter(args):
    if args.backend == "windows":
        from .adapters.windows import WindowsOrigin

        return WindowsOrigin()
    from .adapters.kwin import KWinOrigin

    return KWinOrigin(args.bridge)


async def run(args):
    if args.mode == "ui":
        return await run_ui(args)
    if args.origin_config:
        return await run_multi(args)
    native = None
    shell = None
    link = None
    session = None
    clipboard = None
    started = time.monotonic()
    stats = {
        "mode": args.mode,
        "route": "direct",
        "frames": 0,
        "encode_seconds": 0,
        "decode_seconds": 0,
    }
    try:
        if args.mode in ("list", "origin"):
            native = adapter(args)
            surfaces = []
            for _ in range(50):
                surfaces = native.snapshot()
                if surfaces:
                    break
                await asyncio.sleep(0.05)
            if args.mode == "list":
                print(json.dumps([asdict(s) for s in surfaces], indent=2))
                return
            if args.title and any(
                sum(s.title == title for s in surfaces) != 1 for title in args.title
            ):
                raise ValueError(
                    "each --title must identify exactly one visible origin window"
                )
            selected = [s for s in surfaces if s.title in (args.title or ())]
            if any(s.parent_id is not None for s in selected):
                raise ValueError(
                    "select the owning top-level window; its children follow automatically"
                )
            roots = {s.surface_id for s in selected}
            captured = set()
            for surface in selected:
                native.start_capture(surface.surface_id)
                captured.add(surface.surface_id)
        psk = (
            args.psk_file.read_text().strip()
            if args.psk_file
            else os.environ.get("TELESTHETE_PSK", "")
        )
        if not psk or not args.peer:
            raise ValueError("supply --peer and --psk-file (or TELESTHETE_PSK)")
        # Resolve once before binding transport identity; callbacks compare numeric addresses.
        import socket

        peer = (socket.gethostbyname(args.peer[0]), args.peer[1])
        link = DirectLink(
            psk,
            args.name,
            args.bind,
            peer,
            channel_base=args.channel_base,
            expected_peer=args.peer_name,
        )
        if args.clipboard:
            from .clipboard import QtClipboard

            clipboard = QtClipboard()
        clipboard_io = (clipboard.read, clipboard.write) if clipboard else None
        if native:

            def request(cap, fields):
                if cap == "surface-release":
                    native.release_input()
                elif cap == "focus-request":
                    native.focus(fields["surface_id"])
                elif cap == "resize-request":
                    native.resize(fields["surface_id"], Size(fields["w"], fields["h"]))
                elif cap == "close-request":
                    native.close_window(fields["surface_id"])

            def select_windows(chosen):
                roots.clear()
                roots.update(chosen)

            session = OriginSession(
                link,
                args.name,
                args.peer_name,
                on_input=native.inject,
                on_request=request,
                on_select=select_windows,
                hot_backend=args.encoder if args.lane in ("hot", "auto") else None,
                adaptive=args.lane == "auto",
                clipboard=clipboard_io,
            )
            session.set_surfaces(selected)
            session.set_catalog(surfaces)
        else:
            session = ClientSession(
                link, args.name, args.peer_name, clipboard=clipboard_io
            )
            if not args.headless:
                from .client import FlatClient

                shell = FlatClient(session)
        await link.start(timeout=30)
        if not native:
            session.hello()
        last_hints = last_ping = last_tree = 0.0
        last_frames = {}
        while not args.seconds or time.monotonic() - started < args.seconds:
            now = time.monotonic()
            if clipboard:
                clipboard.pump()
                session.clipboard.poll()
            if native:
                if now - last_tree >= 0.1:
                    all_surfaces = native.snapshot()
                    session.set_catalog(all_surfaces)
                    included = set(roots)
                    while True:
                        children = {
                            s.surface_id
                            for s in all_surfaces
                            if s.parent_id in included
                        }
                        if children <= included:
                            break
                        included.update(children)
                    selected = [s for s in all_surfaces if s.surface_id in included]
                    live = {s.surface_id for s in selected}
                    for sid in captured - live:
                        native.stop_capture(sid)
                        last_frames.pop(sid, None)
                    captured.intersection_update(live)
                    for surface in selected:
                        if surface.surface_id not in captured:
                            native.start_capture(surface.surface_id)
                            captured.add(surface.surface_id)
                    session.set_surfaces(selected)
                    last_tree = now
                if now - session.last_seen > 1.5:
                    native.release_input()
                    session.hints.clear()
                for surface in selected:
                    frame = native.frame(surface.surface_id)
                    if frame and frame.size == surface.size:
                        last_frames[surface.surface_id] = frame
                current = {
                    s.surface_id: last_frames[s.surface_id]
                    for s in selected
                    if s.surface_id in last_frames
                    and last_frames[s.surface_id].size == s.size
                }
                if args.lane == "auto":
                    stats["frames"] += await session.publish_adaptive(
                        {
                            sid: (frame.size, frame.bgra)
                            for sid, frame in current.items()
                        }
                    )
                elif args.lane == "hot":
                    if current and await session.publish_hot(
                        {
                            sid: (frame.size, frame.bgra)
                            for sid, frame in current.items()
                        }
                    ):
                        stats["frames"] += 1
                else:
                    for sid, frame in current.items():
                        if await session.publish(sid, frame.bgra):
                            stats["frames"] += 1
                stats["encode_seconds"] = session.encode_seconds
            else:
                if shell:
                    shell.poll()
                if now - last_hints >= 0.1:
                    if shell:
                        hints = shell.hints()
                    else:
                        from .model import ViewHint, Visibility

                        hints = [
                            ViewHint(s.surface_id, Visibility.IN_VIEW, s.size)
                            for s in session.tree.surfaces.values()
                        ]
                    session.hints(hints)
                    last_hints = now
                if now - last_ping >= 0.25:
                    session.ping()
                    last_ping = now
                session.repair()
                stats["frames"] = len(session.completed)
                stats["decode_seconds"] = session.decode_seconds
            await asyncio.sleep(0.01)
        stats["errors"] = session.errors
        stats["surfaces"] = len(session.tree.surfaces)
        if native:
            stats["atlas_repacks"] = session.hot.repacks if session.hot else 0
            stats["lane_migrations"] = session.lane_migrations
            stats["surface_lanes"] = {
                sid: lane for sid, (lane, _) in session.assignments.items()
            }
        else:
            stats["surfaces_ready"] = sum(
                texture.ready for texture in session.textures.values()
            )
    finally:
        if shell:
            shell.stop()
        if native:
            if session:
                session.stop()
            native.stop()
        if link:
            stats["sent_bytes"] = link.sent_bytes
            await link.stop()
        stats["elapsed_seconds"] = time.monotonic() - started
        if args.stats:
            args.stats.write_text(json.dumps(stats, indent=2) + "\n")
        if args.mode != "list":
            print(json.dumps(stats))


async def run_multi(args):
    if args.mode != "client" or args.clipboard or args.peer:
        raise ValueError("--origin-config is for a client; set each peer in the file")
    entries = read_origin_config(args.origin_config)
    if args.headless:
        FlatClient = None
    else:
        from .client import FlatClient

    links, sessions, shells = [], [], []
    started = time.monotonic()
    client_name = "client" if args.name == "origin" else args.name
    try:
        for index, entry in enumerate(entries):
            name = entry["name"]
            bind = address(entry["bind"])
            peer_input = address(entry["peer"])
            peer = (socket.gethostbyname(peer_input[0]), peer_input[1])
            key_path = Path(entry["psk_file"])
            if not key_path.is_absolute():
                key_path = args.origin_config.parent / key_path
            link = DirectLink(
                key_path.read_text().strip(),
                client_name,
                bind,
                peer,
                channel_base=entry.get("channel_base", 100),
                expected_peer=name,
            )
            links.append(link)
            session = ClientSession(link, client_name, name)
            sessions.append(session)
            if FlatClient:
                shells.append(FlatClient(session, placement_offset=(index * 400, 0)))
        await asyncio.gather(*(link.start(timeout=30) for link in links))
        for session in sessions:
            session.hello()
        last_hints = last_ping = 0.0
        while not args.seconds or time.monotonic() - started < args.seconds:
            now = time.monotonic()
            for shell in shells:
                shell.poll()
            for index, session in enumerate(sessions):
                if now - last_hints >= 0.1:
                    if shells:
                        hints = shells[index].hints()
                    else:
                        from .model import ViewHint, Visibility

                        hints = [
                            ViewHint(s.surface_id, Visibility.IN_VIEW, s.size)
                            for s in session.tree.surfaces.values()
                        ]
                    session.hints(hints)
                if now - last_ping >= 0.25:
                    session.ping()
                session.repair()
            if now - last_hints >= 0.1:
                last_hints = now
            if now - last_ping >= 0.25:
                last_ping = now
            await asyncio.sleep(0.01)
        stats = {
            "mode": "client",
            "origins": {
                session.origin_name: {
                    "surfaces": len(session.tree.surfaces),
                    "ready": sum(
                        texture.ready for texture in session.textures.values()
                    ),
                    "frames": len(session.completed),
                    "errors": session.errors,
                    "decode_seconds": session.decode_seconds,
                }
                for session in sessions
            },
            "elapsed_seconds": time.monotonic() - started,
        }
        if args.stats:
            args.stats.write_text(json.dumps(stats, indent=2) + "\n")
        print(json.dumps(stats))
    finally:
        for shell in shells:
            shell.stop()
        await asyncio.gather(*(link.stop() for link in links), return_exceptions=True)


def read_origin_config(path):
    config = json.loads(path.read_text())
    entries = config.get("origins") if isinstance(config, dict) else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4:
        raise ValueError("origin config requires one to four origins")
    used_names, used_binds = set(), set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"name", "bind", "peer", "psk_file"}
            and set(entry) != {"name", "bind", "peer", "psk_file", "channel_base"}
        ):
            raise ValueError("invalid origin config entry")
        name, bind = entry["name"], address(entry["bind"])
        if (
            not isinstance(name, str)
            or not name
            or name in used_names
            or bind in used_binds
            or not isinstance(entry.get("channel_base", 100), int)
        ):
            raise ValueError("duplicate or invalid origin name/bind")
        address(entry["peer"])
        used_names.add(name)
        used_binds.add(bind)
    return entries


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def run_ui(args):
    if args.peer or args.psk_file or args.title or args.headless or args.clipboard:
        raise ValueError("ui manages its local origin; use --origin-config for remotes")
    from .browser import WindowBrowser
    from .client import FlatClient

    client_name = "client" if args.name == "origin" else args.name
    origin_port, client_port = free_port(), free_port()
    while client_port == origin_port:
        client_port = free_port()
    secret = secrets.token_urlsafe(32)
    environment = {**os.environ, "TELESTHETE_PSK": secret}
    command = [
        sys.executable,
        "-m",
        "surfaces",
        "origin",
        "--backend",
        args.backend,
        "--bridge",
        args.bridge,
        "--name",
        "local",
        "--peer-name",
        client_name,
        "--bind",
        f"127.0.0.1:{origin_port}",
        "--peer",
        f"127.0.0.1:{client_port}",
        "--lane",
        args.lane,
        "--encoder",
        args.encoder,
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    local_errors = []

    async def drain_errors():
        while line := await process.stderr.readline():
            local_errors.append(line.decode(errors="replace").strip())
            local_errors[:] = local_errors[-4:]

    drain_task = asyncio.create_task(drain_errors())
    links, sessions, shells, connects = [], [], [], []
    browser = None
    started = time.monotonic()
    try:
        specs = [
            (
                "local",
                ("127.0.0.1", client_port),
                ("127.0.0.1", origin_port),
                secret,
                args.channel_base,
            )
        ]
        if args.origin_config:
            for entry in read_origin_config(args.origin_config):
                peer = address(entry["peer"])
                key_path = Path(entry["psk_file"])
                if not key_path.is_absolute():
                    key_path = args.origin_config.parent / key_path
                specs.append(
                    (
                        entry["name"],
                        address(entry["bind"]),
                        (socket.gethostbyname(peer[0]), peer[1]),
                        key_path.read_text().strip(),
                        entry.get("channel_base", 100),
                    )
                )
        def add_connection(name, bind, peer, key, base):
            if (
                not name
                or not key
                or len(sessions) >= 5
                or name in {session.origin_name for session in sessions}
                or bind[1] in {link.band.bind_port for link in links}
            ):
                raise ValueError("origin needs a unique name, port, and secret (up to four remotes)")
            index = len(sessions)
            link = DirectLink(
                key, client_name, bind, peer, channel_base=base, expected_peer=name
            )
            session = ClientSession(link, client_name, name)
            links.append(link)
            sessions.append(session)
            shells.append(FlatClient(session, placement_offset=(index * 400, 0)))

            async def connect(current_link=link, current_session=session):
                await current_link.start(timeout=30)
                current_session.hello()

            connects.append(asyncio.create_task(connect()))
            if browser:
                browser.session_added()

        for name, bind, peer, key, base in specs:
            add_connection(name, bind, peer, key, base)

        def add_remote(name, peer_text, port, key):
            remote = address(peer_text)
            peer = socket.gethostbyname(remote[0]), remote[1]
            add_connection(name, ("0.0.0.0", port), peer, key, args.channel_base)

        browser = WindowBrowser(sessions, on_add_remote=add_remote)
        last_hints = last_ping = 0.0
        while not browser.closed and (
            not args.seconds or time.monotonic() - started < args.seconds
        ):
            now = time.monotonic()
            statuses = []
            for index, (session, shell, task) in enumerate(
                zip(sessions, shells, connects)
            ):
                if task.done():
                    error = task.exception()
                    if error:
                        statuses.append(f"{session.origin_name}: {error}")
                        continue
                    shell.poll()
                    if now - last_hints >= 0.1:
                        session.hints(shell.hints())
                    if now - last_ping >= 0.25:
                        session.ping()
                    session.repair()
                    statuses.append(
                        f"{session.origin_name}: {len(session.catalog)} windows"
                    )
                else:
                    statuses.append(f"{session.origin_name}: connecting…")
            if process.returncode is not None:
                statuses[0] = f"local: origin exited ({process.returncode})"
                if local_errors:
                    statuses[0] += f" — {local_errors[-1]}"
            browser.poll(statuses)
            if now - last_hints >= 0.1:
                last_hints = now
            if now - last_ping >= 0.25:
                last_ping = now
            await asyncio.sleep(0.01)
    finally:
        if args.stats:
            args.stats.write_text(
                json.dumps(
                    {
                        "mode": "ui",
                        "origins": {
                            session.origin_name: {
                                "catalog_windows": len(session.catalog),
                                "streamed_windows": len(session.tree.surfaces),
                                "ready_textures": sum(
                                    texture.ready
                                    for texture in session.textures.values()
                                ),
                                "errors": session.errors,
                            }
                            for session in sessions
                        },
                    },
                    indent=2,
                )
                + "\n"
            )
        if browser:
            browser.stop()
        for task in connects:
            if not task.done():
                task.cancel()
        await asyncio.gather(*connects, return_exceptions=True)
        for shell in shells:
            shell.stop()
        await asyncio.gather(*(link.stop() for link in links), return_exceptions=True)
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)


def main():
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
