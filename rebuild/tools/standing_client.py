"""The client half of the standing daemon: how the standing probe and the standing dry run ask a running `rebuild/tools/standing_daemon.py` to run them over the surface it already holds, and what they do when nothing answers. Stdlib-only, because both tools import it at the top and it therefore sits on the memo's code roster (`standing_verdicts.MEMO_CODE_MODULES`) and on the verdict chain's plumbing closure (`artifact_cycle.PLUMBING_TOOL_MODULES`); the server side, which imports the tools, the width arithmetic and the cost reading, is the other module so that neither roster grows past it.

The protocol is one request per connection over a Unix-domain stream socket, newline-delimited JSON both ways. A `probe` or `fill` request carries the tool's argv as the user typed it, the client's working directory (so every relative path in the argv — the verdicts file, `--out`, `--memo`, `--rules` — resolves under the daemon exactly as it would in this process) and the surface path resolved absolute (so the daemon can decline a surface it does not hold). The reply is `{"ok": true, "code", "stdout", "stderr"}` — the tool's exit code and its captured streams, which `relay` writes to this process's streams unchanged, so a served run prints byte for byte what an in-process run prints — or `{"ok": false, "reason"}` when the daemon declines. A `status` request answers with what the daemon holds and a `stop` request has it exit; both are `standing_daemon.main`'s verbs.

`--daemon auto` (the default) asks and falls back to loading the surface in this process when no daemon answers or the daemon declines, saying so on stderr only when there was a socket to ask; `always` refuses to load in this process and exits with the reason; `never` refuses to ask. A caller that hands a tool's `main` its own `units` or `context` is by definition not a command-line run — the verdict chain's in-process call is the one such caller — and is never routed here.

The socket is bound and connected by basename under a momentary `contextlib.chdir` into its directory, because `sun_path` holds about a hundred bytes and both the repo's `var/` path and pytest's temporary root can exceed it; a caller therefore hands these helpers any path it likes and must capture `os.getcwd()` and any absolute path it needs before calling them. The default socket sits under `var/`, never `tmp/`, which may be wiped under a running daemon.
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
    """What a daemon-run tool handed back: its exit code and its two captured streams, verbatim."""

    code: int
    stdout: str
    stderr: str


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The two flags every tool that can be served takes. The socket default is read when the parser is built, so a test that points `SOCKET` elsewhere is seen."""
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
    """A connected client socket, or the OSError the connect raised — a missing directory, no socket file, a file that is not a socket, or a socket nothing listens on all surface as one."""
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
    """A listening server socket at `path`; the caller unlinks a dead socket file first and removes the live one at exit."""
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
    """One request and its reply, or None when no daemon answers at the socket: nothing to connect to, a connection the daemon closed before replying, or a reply that is not a JSON line."""
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
    """Ask the daemon to run `tool` over `argv`, or None when this process should load the surface itself: `never` mode, no daemon answering in `auto` mode, or a daemon declining in `auto` mode — the last two with one stderr line when there was a socket to ask. In `always` mode both of those exit with the reason instead. Stdout is never written here, so a served and an in-process run diff clean on it."""
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
    """Write a served run's streams to this process's streams and answer its exit code."""
    sys.stdout.write(served.stdout)
    sys.stdout.flush()
    sys.stderr.write(served.stderr)
    sys.stderr.flush()
    return served.code
