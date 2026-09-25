"""The client side of the standing daemon: how the standing probe and the standing dry run ask a running `rebuild/tools/standing_daemon.py` to run them over the surface it already holds, and what they do when no daemon replies. Both tools import this module at the top, so it is in the fill memo's code list (`standing_verdicts.MEMO_CODE_MODULES`) and in the verdict chain's plumbing closure (`artifact_cycle.PLUMBING_TOOL_MODULES`). It therefore imports only the standard library. The server side, which imports the tools, `memory_budget` and `peak_rss`, is a separate module so that neither list includes those.

The protocol is one request per connection over a Unix-domain stream socket, with newline-delimited JSON both ways. A `probe` or `fill` request carries the tool's argv as typed; the client's working directory, so every relative path in the argv (the verdicts file, `--out`, `--memo`, `--rules`) resolves in the daemon as it would in this process; and the absolute surface path, so the daemon can decline a surface it does not hold. The reply is either `{"ok": true, "code", "stdout", "stderr"}`, the tool's exit code and captured streams, or `{"ok": false, "reason"}` when the daemon declines. `relay` writes the streams to this process's streams unchanged, so a served run prints the same bytes as an in-process run. A `status` request returns what the daemon holds, and a `stop` request makes it exit; both are subcommands of `standing_daemon.main`.

`--daemon auto`, the default, asks the daemon and falls back to loading the surface in this process when no daemon replies or the daemon declines. It says so on stderr only when there was a socket to ask. `always` exits with the reason instead of loading the surface in this process. `never` does not ask. A call that passes a tool's `main` its own `units` or `context` is not a command-line run and is never routed here; the verdict chain and the daemon itself make such calls.

The socket is bound and connected by its basename, under a brief `contextlib.chdir` into its directory, because `sun_path` holds only about a hundred bytes and both the repo's `var/` path and pytest's temporary root can be longer. A caller can therefore pass any path, but must read `os.getcwd()` and resolve any path it needs before calling these helpers. The default socket is under `var/`, not `tmp/`, because `tmp/` may be wiped while a daemon runs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
SOCKET = ROOT / "var" / "standing-daemon.sock"
MODES = ("auto", "always", "never")
TOOLS = ("probe", "fill")
CONNECT_TIMEOUT_SECONDS = 5
LISTEN_BACKLOG = 128
START_HINT = "start one with make standing-daemon"


class Served(NamedTuple):
    """A daemon-run tool's exit code and its two captured streams, unchanged."""

    code: int
    stdout: str
    stderr: str


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the `--daemon` and `--socket` flags every servable tool takes. The socket default is read when the parser is built, so a test that points `SOCKET` elsewhere takes effect."""
    parser.add_argument(
        "--daemon",
        choices=MODES,
        default="auto",
        help="auto: run through the standing daemon when one answers at --socket and holds this surface, else load the surface here; always: refuse to load here; never: refuse to ask (rebuild/tools/standing_daemon.py is the authority on what a daemon holds and when it declines)",
    )
    parser.add_argument(
        "--socket", default=str(SOCKET), help="where the standing daemon listens (default under var/)"
    )


def connect(path: str | os.PathLike[str]) -> socket.socket:
    """Return a connected client socket, or raise OSError. A missing directory, a missing socket file, a file that is not a socket, and a socket nothing listens on all raise OSError."""
    path = Path(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(CONNECT_TIMEOUT_SECONDS)
        with contextlib.chdir(path.parent):
            sock.connect(path.name)
        sock.settimeout(None)
    except BaseException:
        sock.close()
        raise
    return sock


def bind(path: str | os.PathLike[str]) -> socket.socket:
    """Return a listening server socket at `path`. The caller unlinks a stale socket file first and removes its own socket file at exit."""
    path = Path(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with contextlib.chdir(path.parent):
            sock.bind(path.name)
        sock.listen(LISTEN_BACKLOG)
    except BaseException:
        sock.close()
        raise
    return sock


def exchange(socket_path: str | os.PathLike[str], request: dict) -> dict | None:
    """Send one request and return the reply, or None when no daemon replies at the socket: nothing to connect to, a connection the daemon closed before replying, or a reply that is not a JSON object on one line."""
    try:
        sock = connect(socket_path)
    except OSError:
        return None
    try:
        with sock:
            sock.sendall(json.dumps(request).encode() + b"\n")
            with sock.makefile("rb") as reader:
                line = reader.readline()
    except OSError:
        return None
    if not line:
        return None
    try:
        reply = json.loads(line)
    except ValueError:
        return None
    return reply if isinstance(reply, dict) else None


def ask(
    tool: str,
    argv: list[str],
    surface: str | os.PathLike[str],
    *,
    mode: str,
    socket_path: str | os.PathLike[str],
) -> Served | None:
    """Ask the daemon to run `tool` over `argv` and return the served result. Returns None when this process should load the surface itself: in `never` mode, or in `auto` mode when no daemon replies or the daemon declines, in which case one stderr line says so if there was a socket to ask. In `always` mode those two cases exit with the reason. Nothing is written to stdout here, so a served run and an in-process run have identical stdout."""
    if mode == "never":
        return None
    path = Path(socket_path)
    request = {
        "tool": tool,
        "argv": list(argv),
        "cwd": os.getcwd(),
        "surface": str(Path(surface).resolve()),
    }
    reply = exchange(path, request)
    if reply is None:
        if mode == "always":
            raise SystemExit(f"--daemon always, but no standing daemon answers at {path}; {START_HINT}")
        if path.exists():
            print(
                f"standing daemon: nothing answers at {path}; loading the surface in this process instead",
                file=sys.stderr,
            )
        return None
    if not reply.get("ok"):
        reason = reply.get("reason", "no reason given")
        if mode == "always":
            raise SystemExit(f"the standing daemon at {path} declined: {reason}")
        print(f"standing daemon: {reason}; loading the surface in this process instead", file=sys.stderr)
        return None
    return Served(int(reply["code"]), str(reply["stdout"]), str(reply["stderr"]))


def relay(served: Served) -> int:
    """Write a served run's streams to this process's streams and return its exit code."""
    sys.stdout.write(served.stdout)
    sys.stdout.flush()
    sys.stderr.write(served.stderr)
    sys.stderr.flush()
    return served.code
