"""Hold the review surface in one process so the standing probe and the standing dry run stop reloading it: `serve` loads the surface's human index records once (`standing_probe._human` over `unit_index.iter_human_units` projected onto `UNIT_FIELDS`, the same filter both tools apply) and opens one `standing_verdicts.SlideContext` over the surface's font pair. It then answers `probe` and `fill` requests over a Unix-domain socket. For each request it runs the tool's own `main` over the held objects, in the client's working directory, with stdout and stderr captured, and replies with the exit code and both streams. The client writes them unchanged; `rebuild/tools/standing_client.py` defines the protocol. Because the daemon runs the same code, a served run prints the same bytes as an in-process run. `rebuild/test_standing_daemon.py` checks this over the frozen mini bundle for the probe's unit, find, survey and coverage modes and both dry-run forms.

Only one daemon should run, because a second would hold a second copy of the surface. `serve` exits 1 when another `serve` holds its lock or a daemon already answers at its socket. The daemon handles one request at a time on a single thread, and the listen backlog queues the rest, because the held objects are not safe to share across requests. It exists to save memory and fan-out width, not per-request latency. After every request the `SlideContext` memos and the fill's alignment cache are emptied, so each request shapes the same windows a fresh process would and memory stays bounded. The rules file and the verdicts file are not held. Each request's tool reads the paths its argv names, so a scratch `--rules` works as well as the checked-in one.

`stamp_of` records what a served answer depends on besides the request: the surface manifest's `generated_at`, the repo code loaded in this process (`loaded_repo_files`, read from `sys.modules`), both fonts' bytes (the surface's own copies, which change only when the surface is rebuilt), and `uv.lock`'s dependency pins (`fingerprint.lock_digest`, so a bump of the project's own version does not move it). The daemon checks the stamp before every request and every `IDLE_CHECK_SECONDS` while idle. When any field has changed, it declines the request and exits, because code cannot be reloaded into a running process and a daemon that can serve nothing should not keep holding memory. A request for a different surface is declined without exiting.

Before anything else, `serve` takes an exclusive, non-blocking `flock` on a lock file beside the socket (`lock_path`: `var/standing-daemon.lock` for the default socket), writes its pid there, and holds the lock for its lifetime. A second `serve` that cannot take the lock exits 1 naming that pid, so two `serve` runs started in the same instant cannot both reach the socket. Only a lock holder deletes a leftover socket file, and while it holds the lock only a dead daemon can have left one. The socket is bound before the load. A client that connects during the load waits for the reply. The daemon records the socket file's device and inode right after binding. On exit it removes the socket file only when it is still that file, and while idle it exits when the file at the socket path is gone or is another file, because no client can reach it any more. SIGTERM, SIGINT and the `stop` subcommand all remove the socket on exit.

`main` has three subcommands: `serve`, `status` and `stop`. `make standing-daemon` runs `serve` detached under `nohup` with its log at `var/standing-daemon.log`, and `make standing-daemon-stop` runs `stop`.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import pathlib
import signal
import sys
import threading
import time
import traceback
from typing import Callable, NamedTuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.review.unit_index import iter_human_units  # noqa: E402
from rebuild.tools import memory_budget, peak_rss, standing_client  # noqa: E402
from rebuild.tools.review_docket import SURFACE  # noqa: E402

# Peak memory budget for the daemon process. It covers the held human index records (`iter_human_units` projected onto `UNIT_FIELDS`, sharing repeated values within the read and keeping no set of all surface ids), the comparator over the font pair, transient allocations while loading, allocator retention, and one whole-domain request's evaluation. It is the process's peak, which is larger than its idle resident set. The figure comes from the `peak rss` line the daemon prints at exit: 1.52 GB on the 32 GiB machine in doc/fleet.md, after a probe and a whole-domain dry run with no memo at the cycle-derived refill width, then rounded up to leave at least 25% headroom. That line measures the daemon process only; STANDING_FILL_WORKER_BYTES budgets the refill workers separately. `serve` prints `describe_fit(…, cap=1)` once, and the width of one is enforced by refusing to start beside a live daemon. No cycle width is computed from this constant or subtracts it: `surface_job_budget` and `kernel_threads_budget` assume no daemon is running, so stop the daemon before a cycle pass. The daemon also exits by itself once its surface is rebuilt. Re-measure the figure as the surface grows, as with SURFACE_PARENT_BYTES. The surface-holding cap in the dont-bug-me-about-this-ever-again skill uses the same figure.
STANDING_DAEMON_BYTES = 2_000_000_000
IDLE_CHECK_SECONDS = 30
REQUEST_READ_SECONDS = 30
STOP_WAIT_SECONDS = 15
EXCLUDED_TREES = (".venv", ".uv-cache")
FONT_NAMES = ("before.otf", "after.otf")
UNIT_FIELDS = frozenset(
    {
        "id",
        "batch",
        "class",
        "echo",
        "notation",
        "codepoints",
        "configs",
        "ink_deltas",
        "no_verdict",
        "content_key",
        "render_groups",
        "pair",
        "secondary_seams",
        "before",
        "after",
    }
)


class Stamp(NamedTuple):
    """The inputs a served answer depends on besides the request: the surface manifest's stamp, the loaded code, the font pair, and `uv.lock`'s dependency pins."""

    generated_at: str
    code: str
    fonts: str
    lock: str


class _Shutdown(BaseException):
    """Raised out of the signal handler so a SIGTERM or SIGINT unwinds through the serve loop's cleanup."""


def loaded_repo_files() -> list[pathlib.Path]:
    """Return the file of every module this process has imported from under the repo root, excluding the trees in `EXCLUDED_TREES`."""
    root = str(ROOT) + os.sep
    excluded = tuple(str(ROOT / tree) + os.sep for tree in EXCLUDED_TREES)
    files: set[pathlib.Path] = set()
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if not isinstance(file, str) or not file:
            continue
        path = os.path.abspath(file)
        if path.startswith(root) and not path.startswith(excluded) and os.path.isfile(path):
            files.add(pathlib.Path(path))
    return sorted(files)


def _digest(path: pathlib.Path, digest: Callable[[pathlib.Path], str] = fingerprint.file_sha256) -> str:
    try:
        return digest(path)
    except OSError:
        return "-"


def stamp_of(surface: pathlib.Path) -> Stamp:
    """Return the stamp as the files stand now. An unreadable manifest or font stamps as `-`, which differs from any value read at load."""
    try:
        generated_at = str(json.loads((surface / "manifest.json").read_text())["generated_at"])
    except OSError, ValueError, KeyError, TypeError:
        generated_at = "-"
    return Stamp(
        generated_at,
        fingerprint.hash_paths(ROOT, loaded_repo_files()),
        ":".join(_digest(surface / "fonts" / name) for name in FONT_NAMES),
        _digest(ROOT / "uv.lock", fingerprint.lock_digest),
    )


def _moved(held: Stamp, fresh: Stamp) -> str:
    names = {
        "generated_at": "the surface manifest's generated_at",
        "code": "the loaded code",
        "fonts": "the font pair",
        "lock": "uv.lock",
    }
    moved = [names[field] for field in Stamp._fields if getattr(held, field) != getattr(fresh, field)]
    return " and ".join(moved) + (" moved" if moved else "")


def _exit_code(code: object, err: io.StringIO) -> int:
    """Convert a `SystemExit` code the way the interpreter does: None is 0, an int is itself, and anything else is printed to stderr and becomes 1."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(str(code), file=err)
    return 1


def run_tool(tool: str, argv: list[str], cwd: str, units: list, context) -> tuple[int, str, str]:
    """Run one request through the tool's own `main` in the client's working directory, over the held units and context, and return the exit code, stdout and stderr. The context's memos and the fill's alignment cache are emptied afterwards even on failure, so the next request shapes what a fresh process would and memory stays bounded."""
    from rebuild.tools import standing_probe, standing_verdicts

    tool_main = standing_probe.main if tool == "probe" else standing_verdicts.main
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                with contextlib.chdir(cwd):
                    code = tool_main(argv, units=units, context=context) or 0
            except SystemExit as exc:
                code = _exit_code(exc.code, err)
            except Exception:
                traceback.print_exc(file=err)
                code = 1
    finally:
        if context is not None:
            context.memo.clear()
            context.composed.clear()
        standing_verdicts.release_alignment_cache()
    return code, out.getvalue(), err.getvalue()


def _read_request(conn) -> dict:
    with conn.makefile("rb") as reader:
        line = reader.readline()
    request = json.loads(line)
    if not isinstance(request, dict):
        raise ValueError("a request is a JSON object")
    return request


def _reply(conn, reply: dict) -> None:
    try:
        conn.sendall(json.dumps(reply).encode() + b"\n")
    except OSError as exc:
        print(f"standing daemon: a client went away before its reply ({exc})", flush=True)


def _raise_shutdown(signum, _frame) -> None:
    raise _Shutdown(signum)


def lock_path(socket_path: pathlib.Path) -> pathlib.Path:
    """Return the lock file that `serve` holds for its lifetime: the socket path with its suffix replaced by `.lock`."""
    return socket_path.with_suffix(".lock")


def _identity(path: str) -> tuple[int, int] | None:
    try:
        stat = os.lstat(path)
    except OSError:
        return None
    return stat.st_dev, stat.st_ino


def _already_answers(socket_path: pathlib.Path, pid: object) -> int:
    print(
        f"a standing daemon already answers at {socket_path} (pid {pid}); "
        "one process holds the surface by design"
    )
    return 1


def serve(surface=SURFACE, socket_path=None) -> int:
    """Load the surface once, then serve requests until stopped, signaled, stale, or unreachable. Return 1 without loading when another `serve` holds the lock or a daemon already answers at the socket."""
    socket_path = pathlib.Path(standing_client.SOCKET if socket_path is None else socket_path)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    lock = os.open(lock_path(socket_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        return _serve_locked(pathlib.Path(surface), socket_path, lock)
    finally:
        os.close(lock)


def _serve_locked(surface: pathlib.Path, socket_path: pathlib.Path, lock: int) -> int:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = os.pread(lock, 32, 0).decode(errors="replace").strip()
        return _already_answers(socket_path, holder or "unknown")
    os.ftruncate(lock, 0)
    os.pwrite(lock, f"{os.getpid()}\n".encode(), 0)
    status = standing_client.exchange(socket_path, {"tool": "status"})
    if status is not None and status.get("ok"):
        return _already_answers(socket_path, status.get("pid"))
    if os.path.lexists(socket_path):
        os.unlink(socket_path)
    absolute = os.path.abspath(socket_path)
    listener = standing_client.bind(socket_path)
    bound = _identity(absolute)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _raise_shutdown)
        signal.signal(signal.SIGINT, _raise_shutdown)
    served = 0
    since = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        print(f"standing daemon: {memory_budget.describe_fit(STANDING_DAEMON_BYTES, cap=1)}", flush=True)
        from rebuild.tools import standing_probe, standing_verdicts

        surface = pathlib.Path(surface).resolve()
        started = time.monotonic()
        manifest = json.loads((surface / "manifest.json").read_text())
        units = standing_probe._human(iter_human_units(surface, fields=UNIT_FIELDS))
        fonts = [surface / "fonts" / name for name in FONT_NAMES]
        context = standing_verdicts.SlideContext(*fonts) if all(font.is_file() for font in fonts) else None
        held = stamp_of(surface)
        print(
            f"standing daemon: holding {surface} (generated {manifest['generated_at']}, {len(units)} human "
            f"units, {'a font pair' if context is not None else 'no fonts'}) loaded in "
            f"{time.monotonic() - started:.1f} s; listening at {absolute} (pid {os.getpid()})",
            flush=True,
        )
        listener.settimeout(IDLE_CHECK_SECONDS)
        while True:
            try:
                conn, _address = listener.accept()
            except TimeoutError:
                if _identity(absolute) != bound:
                    print(f"standing daemon: {absolute} is no longer its socket; exiting", flush=True)
                    break
                fresh = stamp_of(surface)
                if fresh != held:
                    print(f"standing daemon: {_moved(held, fresh)} since it loaded; exiting", flush=True)
                    break
                continue
            with conn:
                begun = time.monotonic()
                conn.settimeout(REQUEST_READ_SECONDS)
                try:
                    request = _read_request(conn)
                except OSError, ValueError:
                    _reply(conn, {"ok": False, "reason": "unreadable request"})
                    continue
                tool = request.get("tool")
                if tool == "status":
                    _reply(
                        conn,
                        {
                            "ok": True,
                            "surface": str(surface),
                            "generated_at": manifest["generated_at"],
                            "pid": os.getpid(),
                            "units": len(units),
                            "served": served,
                            "since": since,
                        },
                    )
                    continue
                if tool == "stop":
                    _reply(conn, {"ok": True, "pid": os.getpid()})
                    print("standing daemon: stopped by request", flush=True)
                    break
                if tool not in standing_client.TOOLS:
                    _reply(conn, {"ok": False, "reason": f"unknown tool {tool!r}"})
                    continue
                asked = pathlib.Path(str(request.get("surface", "")))
                if asked != surface:
                    _reply(conn, {"ok": False, "reason": f"it holds {surface}, not {asked}"})
                    continue
                fresh = stamp_of(surface)
                if fresh != held:
                    reason = f"stale: {_moved(held, fresh)} since it loaded"
                    _reply(
                        conn,
                        {"ok": False, "reason": f"{reason}; it is exiting, {standing_client.START_HINT}"},
                    )
                    print(f"standing daemon: {reason}; exiting", flush=True)
                    break
                argv = [str(arg) for arg in request.get("argv", [])]
                code, out, err = run_tool(tool, argv, str(request.get("cwd", ROOT)), units, context)
                _reply(conn, {"ok": True, "code": code, "stdout": out, "stderr": err})
                served += 1
                print(f"served {tool} in {time.monotonic() - begun:.1f} s (code {code})", flush=True)
    except _Shutdown as exc:
        print(f"standing daemon: signal {exc.args[0]}; exiting", flush=True)
    finally:
        listener.close()
        if bound is not None and _identity(absolute) == bound:
            os.unlink(absolute)
        print(
            f"standing daemon: exiting, peak rss {peak_rss.format_gb(peak_rss.peak_rss_self_bytes())} GB",
            flush=True,
        )
    return 0


def _wait_for_socket_removal(socket_path: str) -> None:
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while os.path.lexists(socket_path) and time.monotonic() < deadline:
        time.sleep(0.1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    verbs = parser.add_subparsers(dest="verb", required=True)
    serve_parser = verbs.add_parser(
        "serve", help="load the surface and answer until stopped, signaled, stale, or unreachable"
    )
    serve_parser.add_argument("--surface", default=str(SURFACE))
    serve_parser.add_argument("--socket", default=str(standing_client.SOCKET))
    status_parser = verbs.add_parser("status", help="say what the daemon holds; exit 1 when none answers")
    status_parser.add_argument("--socket", default=str(standing_client.SOCKET))
    stop_parser = verbs.add_parser(
        "stop", help="have the daemon exit and remove its socket; exit 0 either way"
    )
    stop_parser.add_argument("--socket", default=str(standing_client.SOCKET))
    args = parser.parse_args(argv)
    if args.verb == "serve":
        return serve(args.surface, args.socket)
    reply = standing_client.exchange(args.socket, {"tool": args.verb})
    answered = reply is not None and bool(reply.get("ok"))
    if args.verb == "status":
        if not answered or reply is None:
            print(f"no standing daemon answers at {args.socket}")
            return 1
        print(
            f"standing daemon pid {reply['pid']} holds {reply['surface']} (generated {reply['generated_at']}, "
            f"{reply['units']} human units); {reply['served']} requests served since {reply['since']}"
        )
        return 0
    if not answered or reply is None:
        print(f"no standing daemon answers at {args.socket}; nothing to stop")
        return 0
    _wait_for_socket_removal(args.socket)
    print(f"stopped the standing daemon (pid {reply['pid']}) at {args.socket}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
