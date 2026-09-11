"""Hold the review surface in one process so the standing probe and the standing dry run stop reloading it: `serve` loads the surface's human index records once (`standing_probe._human` over `review_docket.load_units`, the same filter both tools apply), opens one `standing_verdicts.SlideContext` over the surface's font pair once, and then answers `probe` and `fill` requests over a Unix-domain socket by running the tools' own `main` — the same parser, the same functions, over those held objects, under the client's working directory, with stdout and stderr captured — and handing back the exit code and both streams, which the client (`rebuild/tools/standing_client.py`, the protocol's home) writes unchanged. Nothing is re-implemented, so a served run prints byte for byte what an in-process run prints; `rebuild/test_standing_daemon.py` holds that over the frozen mini bundle for the probe's unit, find, survey and coverage modes and both dry-run forms.

It is one process by design: the surface objects are shared, a second holder would be a second copy of the surface, and `serve` refuses to start beside a daemon that already answers at its socket. It answers one request at a time — a single thread, the listen backlog queuing the rest — because the held objects are not safe to share across requests and the win is memory and fan-out width, not per-request latency. The `SlideContext` memos are emptied after every request, so a served run shapes exactly the windows a fresh process would and the daemon's footprint stays bounded. The rules file and the verdicts file are not held: each request's tool reads them as its argv names them, so neither can go stale in here and a scratch `--rules` is served as readily as the checked-in one.

Staleness is part of the contract. `stamp_of` is the surface manifest's `generated_at`, the repo code actually loaded in this process (`loaded_repo_files`, read off `sys.modules`, so no hand roster can drift from what decides), both fonts' bytes and `uv.lock` for the shaper; it is checked before every request and every `IDLE_CHECK_SECONDS` while idle, and any field moving makes the daemon decline the request and exit, because code cannot be reloaded into a running process and a holder that can no longer answer is the one footprint the memory policy forbids. A request for a surface other than the one it holds is declined without exiting. The socket is bound before the load, so a second `serve` started in the same instant fails its bind rather than unlinking the first's live socket, and a client that connects during the load waits for the answer; SIGTERM, SIGINT and the `stop` verb all remove the socket on the way out. It takes no port: never 7293 (the site) and never 7294 (the review server).

`main` has three verbs — `serve`, `status`, `stop` — and `make standing-daemon` / `make standing-daemon-stop` wrap the first and the last, detaching `serve` under `nohup` with its log under `var/`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import signal
import sys
import threading
import time
import traceback
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.tools import memory_budget, peak_rss, standing_client  # noqa: E402
from rebuild.tools.review_docket import SURFACE, load_units  # noqa: E402

# What this one process holds for as long as it runs, and so a co-resident term on the box that no cycle width subtracts. The peak is the load itself: `load_units` materializes every index record before the human filter drops the machine ones, roughly eight in nine on the live surface, and an allocator that keeps those pages leaves the resident set at that peak for the daemon's whole life; the comparator over the font pair and one request's evaluation sit inside it. The seed is the `peak rss` line the daemon prints at exit — 6.94 GB on the 32 GiB box in doc/fleet.md after a probe and a targeted dry run over the live surface, beside 7.39 GB for the same probe loading in its own process — rounded up past both. `serve` resolves it once as `describe_fit(…, cap=1)` and enforces the one by refusing to start beside a live daemon; nothing divides by it and nothing else subtracts it — `surface_job_budget` and `kernel_threads_budget` price a box with no daemon on it — which is why the daemon is stopped before a cycle pass and exits by itself once the surface it holds is rebuilt. It is a reading to re-seed as the surface grows, like SURFACE_PARENT_BYTES.
STANDING_DAEMON_BYTES = 8_000_000_000
IDLE_CHECK_SECONDS = 30
REQUEST_READ_SECONDS = 30
STOP_WAIT_SECONDS = 15
EXCLUDED_TREES = (".venv", ".uv-cache")
FONT_NAMES = ("before.otf", "after.otf")


class Stamp(NamedTuple):
    """Everything a served answer is a function of beyond the request itself: the surface's manifest stamp, the loaded code, the font pair and the shaper's lockfile."""

    generated_at: str
    code: str
    fonts: str
    lock: str


class _Shutdown(BaseException):
    """Raised out of the signal handler so a SIGTERM or SIGINT unwinds through the serve loop's cleanup."""


def loaded_repo_files() -> list[pathlib.Path]:
    """Every module file under the repo root this process has imported, the interpreter's own packages and the uv cache excepted: the code that would decide a request, as it stands on disk."""
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


def _digest(path: pathlib.Path) -> str:
    try:
        return fingerprint.file_sha256(path)
    except OSError:
        return "-"


def stamp_of(surface: pathlib.Path) -> Stamp:
    """The stamp as the tree stands at this instant; a manifest or font that cannot be read stamps as `-`, which differs from whatever was loaded."""
    try:
        generated_at = str(json.loads((surface / "manifest.json").read_text())["generated_at"])
    except OSError, ValueError, KeyError, TypeError:
        generated_at = "-"
    return Stamp(
        generated_at,
        fingerprint.hash_paths(ROOT, loaded_repo_files()),
        ":".join(_digest(surface / "fonts" / name) for name in FONT_NAMES),
        _digest(ROOT / "uv.lock"),
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
    """The interpreter's own reading of a `SystemExit`: None is 0, an int is itself, anything else is printed to stderr and is 1."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(str(code), file=err)
    return 1


def run_tool(tool: str, argv: list[str], cwd: str, units: list, context) -> tuple[int, str, str]:
    """One request, run exactly as the tool's own `main` runs it: under the client's working directory, over the held units and context, with both streams captured. The context's memos and the fill's alignment cache are emptied afterwards, whatever happened, so the next request shapes what a fresh process would and the footprint stays bounded."""
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


def serve(surface=SURFACE, socket_path=None) -> int:
    """Load once, then answer until stopped, signaled, or stale. Exit 1 without loading when a daemon already answers at the socket."""
    socket_path = pathlib.Path(standing_client.SOCKET if socket_path is None else socket_path)
    status = standing_client.exchange(socket_path, {"tool": "status"})
    if status is not None and status.get("ok"):
        print(
            f"a standing daemon already answers at {socket_path} (pid {status.get('pid')}); "
            "one process holds the surface by design"
        )
        return 1
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(socket_path):
        os.unlink(socket_path)
    absolute = os.path.abspath(socket_path)
    listener = standing_client.bind(socket_path)
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
        units = standing_probe._human(load_units(surface))
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
        if os.path.lexists(absolute):
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
        "serve", help="load the surface and answer until stopped, signaled, or stale"
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
