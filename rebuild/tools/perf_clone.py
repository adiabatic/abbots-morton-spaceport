"""The scratch clones the speed-up check (`rebuild/tools/perf_check.py`, `make perf-check`) runs its cycle passes in, and the two hooks hyperfine calls around each timed run. `doc/perf-check.md` is the runbook.

A clone lives under `var/perf/` in the main checkout and never pushes anywhere: `bootstrap` removes its `origin` right after the clone, the driver removes any remote it finds each time it reuses the clone (`remove_remotes`), and a clone without `BOOTSTRAP_MARKER`, which the bootstrap writes as its last act, is bootstrapped again. Every command run in it runs under `child_env`: `HOME`, `USER`, `LOGNAME`, `LANG` and `TMPDIR` from the driver's environment, its `PATH` behind `SHIM_DIR`, `UV_CACHE_DIR` pointing at the main checkout's uv cache, and `cycle_paths.REVIEW_RELOAD_OFF_ENV`, so no output-changing, width-changing or journal-changing variable leaks into a pass, and no pass sends the live review server a reload. Every git command the check runs goes through `git_env`, without the driver's `GIT_*` variables, so an inherited `GIT_DIR` or `GIT_INDEX_FILE` cannot point it at the main checkout's repository or index. The timed command is `cycle_argv`: `make -C <clone> artifact-cycle ARGS=--stop-server` under `env -i` with that environment. `--stop-server` drops the readiness checklist's server row, which the main checkout's review server would fail, and does nothing else against a server from another checkout; its legacy branch stops review servers machine-wide with `pkill`. So every pass is preceded by the clone's own `probe_server`, with the code the pass runs, answering `foreign` or `none` (`wait_until_quiet`), and the `pkill` a pass finds first on its `PATH` is `install_shims`' stand-in, which stops nothing: a stop the pass's own probe asked for then times out with the port still held, and the pass refuses.

A snapshot (`take_snapshot`) holds a clone's warm state: every entry of `rebuild/out/` but the timings journal, the clone's whole `var/` and `tmp/`, the six site fonts, the verdict store with its stashes, a clonefile copy of the verdict journal with its inode and size, and the ref it was taken at. `restore_snapshot` puts it back with `cp -c` clonefile copies. It restores the verdict journal in place while the clone's journal is the snapshot's inode: by truncation when the snapshot's bytes are a prefix of the file (checked over the last `JOURNAL_TAIL_BYTES`), else by rewriting the snapshot's bytes into the same file, so the retention pass's journal scan state (restored with `var/`) still matches and no run rescans the journal. Only a journal on another inode is replaced by a copy, and that restore reports `journal_rescan`. `rebuild/kernel-rs/target/`, `.venv` and `__pycache__` are never snapshotted: cargo rebuilds by source mtime.

The hooks are the only CLI:

  uv run --directory <main> python -m rebuild.tools.perf_clone prepare --clone C --snapshot S --start REF [--patch P] --record DIR
  uv run --directory <main> python -m rebuild.tools.perf_clone conclude --clone C --record DIR

`prepare` refuses a clone outside the main checkout's `var/perf/`, installs the shims, waits for the clone's pass lock, resets the tracked files to the start ref (`git clean` without `-x`, so the warm state survives), restores the snapshot, applies the scenario's edit with `git apply`, then probes the machine and waits while anything `perf_check.probe_outcomes` marks as waiting holds, so the server probe runs the clone's code exactly as the run will, and writes the run's stamp to `<record>/runs/<k>/stamp.json` as its last act, so `prepared_at` is the moment before the timed command starts. Run 0 is hyperfine's warm-up. A refusal writes `<record>/refused.json` and exits 2. `conclude` copies the run directory `var/build-logs/latest` names and the clone's timings-journal lines past the stamp's offset into the run's directory, classifies the run from its `terminal.log` (`perf_check.classify_run`), probes again, and exits 1, which stops hyperfine, when the run was not READY or something disturbed it.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from rebuild.pipeline.fingerprint import file_sha256
from rebuild.review import store_lock
from rebuild.review.journal import AUTOSAVE_NAME, JOURNAL_NAME
from rebuild.tools import cycle_paths, standing_client

ROOT = Path(__file__).resolve().parents[2]
PERF_DIR = ROOT / "var" / "perf"
ENV_NAMES = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "TMPDIR")
DRIVER_PID_ENV = "AMS_PERF_DRIVER_PID"
SHIM_DIR = Path("var/perf/bin")
SHIMS = {"pkill": "#!/bin/sh\necho 'perf-check: pkill stops nothing in a scratch clone' >&2\nexit 1\n"}
BOOTSTRAP_MARKER = Path(".git/perf-bootstrap.json")
RETRIED_PASSES = ("refused", "interrupted")
SITE_FONTS = tuple(
    f"site/AbbotsMortonSpaceport{family}-{weight}.otf"
    for family in ("Mono", "SansJunior", "SansSenior")
    for weight in ("Regular", "Bold")
)
SENIOR_FONT = "site/AbbotsMortonSpaceportSansSenior-Regular.otf"
REBUILD_OUT = Path("rebuild/out")
TIMINGS_JOURNAL = REBUILD_OUT / "cycle-timings.ndjson"
BUILD_LOGS = Path("var/build-logs")
PASS_LOCK = Path("var/cycle/pass.lock")
STASH_PATTERN = "verdicts-autosave-*.json"
BASELINE_FILES = (
    "baseline-*.tsv.gz",
    "digest-*.json",
    "digests.tsv",
    "SUMMARY.md",
    "baseline-font-content-digests.json",
)
JOURNAL_TAIL_BYTES = 64 * 1024
LOCK_POLL_S = 5.0
IDENTITY = ("-c", "user.name=perf-check", "-c", "user.email=perf-check@localhost")


class PerfRefused(Exception):
    """The check cannot go on; the message says why. Every CLI exits 2 on it."""


class WarmFailed(PerfRefused):
    """A warm pass failed or ended a harness failure, or the state never settled: the tree's doing, not the machine's."""


class PassResult(NamedTuple):
    """One cycle pass in a clone: its classification (`perf_check.classify_run`), its run line from the clone's journal (None when it wrote none), its `terminal.log` text and the exit status of `make`."""

    classification: str
    run_line: dict | None
    log: str
    returncode: int
    elapsed_s: float


def child_env(main: Path = ROOT) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_NAMES if name in os.environ}
    env["PATH"] = os.pathsep.join(part for part in (str(main / SHIM_DIR), env.get("PATH")) if part)
    env["UV_CACHE_DIR"] = str(main / ".uv-cache")
    env[cycle_paths.REVIEW_RELOAD_OFF_ENV] = "1"
    return env


def install_shims(main: Path = ROOT) -> None:
    """Write each of `SHIMS` into `<main>/SHIM_DIR`, outside every clone, where `child_env`'s `PATH` finds it before the system's."""
    directory = main / SHIM_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in SHIMS.items():
        path = directory / name
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        path.chmod(0o755)


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The driver's environment without its `GIT_*` variables, plus `extra`."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return {**env, **(extra or {})}


def cycle_argv(clone: Path, main: Path = ROOT) -> list[str]:
    return [
        "env",
        "-i",
        *(f"{name}={value}" for name, value in child_env(main).items()),
        "make",
        "-C",
        str(clone),
        "artifact-cycle",
        "ARGS=--stop-server",
    ]


def check_clone_path(clone: Path, main: Path = ROOT) -> Path:
    """Return the clone's resolved path, or raise PerfRefused unless it lies strictly under `<main>/var/perf/` and is not the main checkout itself."""
    resolved = Path(clone).resolve()
    perf = (Path(main) / "var" / "perf").resolve()
    if resolved == Path(main).resolve() or resolved == perf or not resolved.is_relative_to(perf):
        raise PerfRefused(f"{clone} is not a scratch clone under {perf}")
    return resolved


def git(repo: Path, *args: str, env: Mapping[str, str] | None = None, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=git_env(env))
    if check and result.returncode != 0:
        raise PerfRefused(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def git_ok(repo: Path, *args: str, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=git_env(env))
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def run_logged(argv: list[str], *, cwd: Path, log: Path, env: Mapping[str, str]) -> tuple[int, float]:
    """Run `argv` in `cwd` with its output appended to `log`, and return its exit status and wall seconds."""
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("ab") as out:
        out.write(f"$ {' '.join(argv)}\n".encode())
        out.flush()
        result = subprocess.run(
            argv, cwd=cwd, env=dict(env), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
    return result.returncode, time.monotonic() - started


def wait_for_pass_lock(
    clone: Path, *, poll_s: float = LOCK_POLL_S, sleep: Callable[[float], None] = time.sleep
) -> float:
    """Wait until no process holds the clone's pass lock, and return how long that took. A land runs in its own session and shares the lock, so this keeps a restore from racing a land the cycle has not finished."""
    lock = clone / PASS_LOCK
    waited = 0.0
    while store_lock.holder(lock) is not None:
        sleep(poll_s)
        waited += poll_s
    return waited


def reset_tracked(clone: Path, ref: str) -> None:
    git(clone, "reset", "-q", "--hard", ref)
    git(clone, "clean", "-q", "-fd")


def clonefile(src: Path, dst: Path) -> None:
    subprocess.run(["cp", "-c", "-R", str(src), str(dst)], check=True)


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def verdict_files(clone: Path) -> list[Path]:
    store = clone / AUTOSAVE_NAME
    return ([store] if store.is_file() else []) + sorted(clone.glob(STASH_PATTERN))


def latest_log(clone: Path) -> str | None:
    link = clone / BUILD_LOGS / "latest"
    return os.readlink(link) if link.is_symlink() else None


def take_snapshot(clone: Path, snap: Path, *, ref: str) -> None:
    """Snapshot the clone's warm state into `snap`, replacing what was there (the module docstring lists what a snapshot holds)."""
    remove(snap)
    (snap / "out").mkdir(parents=True)
    out = clone / REBUILD_OUT
    for entry in sorted(out.iterdir()) if out.is_dir() else []:
        if entry.name != TIMINGS_JOURNAL.name:
            clonefile(entry, snap / "out" / entry.name)
    for name in ("var", "tmp"):
        if (clone / name).exists():
            clonefile(clone / name, snap / name)
    (snap / "site").mkdir()
    for font in SITE_FONTS:
        if (clone / font).is_file():
            clonefile(clone / font, snap / "site" / Path(font).name)
    (snap / "store").mkdir()
    for path in verdict_files(clone):
        clonefile(path, snap / "store" / path.name)
    journal = clone / JOURNAL_NAME
    journal_meta = None
    if journal.is_file():
        stat = journal.stat()
        (snap / "journal").mkdir()
        clonefile(journal, snap / "journal" / JOURNAL_NAME)
        journal_meta = {"inode": stat.st_ino, "size": stat.st_size}
    meta = {
        "ref": ref,
        "sha": git(clone, "rev-parse", "HEAD"),
        "journal": journal_meta,
        "taken_at": time.time(),
    }
    (snap / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")


def snapshot_meta(snap: Path) -> dict:
    return json.loads((snap / "meta.json").read_text(encoding="utf-8"))


def _same_tail(live: Path, copy: Path, size: int) -> bool:
    count = min(JOURNAL_TAIL_BYTES, size)
    with live.open("rb") as first, copy.open("rb") as second:
        first.seek(size - count)
        second.seek(size - count)
        return first.read(count) == second.read(count)


def restore_journal(clone: Path, snap: Path) -> str:
    """Put the snapshot's verdict journal back and say how: `truncated`, `rewritten` (both on the snapshot's inode), `copied` (another inode, so the next pass rescans it) or `absent`."""
    meta = snapshot_meta(snap).get("journal")
    live = clone / JOURNAL_NAME
    if meta is None:
        remove(live)
        return "absent"
    copy = snap / "journal" / JOURNAL_NAME
    if live.is_file() and not live.is_symlink() and live.stat().st_ino == meta["inode"]:
        size = meta["size"]
        if live.stat().st_size >= size and _same_tail(live, copy, size):
            os.truncate(live, size)
            return "truncated"
        with copy.open("rb") as source, live.open("r+b") as target:
            target.truncate(0)
            shutil.copyfileobj(source, target, 1 << 22)
        return "rewritten"
    remove(live)
    clonefile(copy, live)
    return "copied"


def restore_snapshot(clone: Path, snap: Path) -> dict:
    """Put a snapshot back into the clone: every entry of `rebuild/out/` but the timings journal, `var/` and `tmp/` whole, the site fonts, the verdict store and exactly the snapshot's stashes, and the verdict journal (`restore_journal`)."""
    out = clone / REBUILD_OUT
    out.mkdir(parents=True, exist_ok=True)
    for entry in list(out.iterdir()):
        if entry.name != TIMINGS_JOURNAL.name:
            remove(entry)
    for entry in sorted((snap / "out").iterdir()):
        clonefile(entry, out / entry.name)
    for name in ("var", "tmp"):
        remove(clone / name)
        if (snap / name).exists():
            clonefile(snap / name, clone / name)
    for font in SITE_FONTS:
        remove(clone / font)
        saved = snap / "site" / Path(font).name
        if saved.is_file():
            clonefile(saved, clone / font)
    for path in verdict_files(clone):
        remove(path)
    for path in sorted((snap / "store").iterdir()):
        clonefile(path, clone / path.name)
    journal = restore_journal(clone, snap)
    return {"journal": journal, "journal_rescan": journal == "copied"}


def apply_patch(clone: Path, patch: Path) -> None:
    ok, message = git_ok(clone, "apply", str(patch))
    if not ok:
        raise PerfRefused(f"{patch} does not apply in {clone}: {message}")


def journal_size(clone: Path) -> int:
    path = clone / TIMINGS_JOURNAL
    return path.stat().st_size if path.is_file() else 0


def journal_lines(path: Path, offset: int) -> tuple[bytes, list[dict]]:
    """Return the bytes of the journal at `path` past `offset` and the lines among them that parse."""
    if not path.is_file():
        return b"", []
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    lines = []
    for raw in data.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict):
            lines.append(entry)
    return data, lines


def harvest(clone: Path, run_dir: Path, stamp: Mapping) -> dict:
    """Copy the run directory the clone's `var/build-logs/latest` names, when the run made a new one, and the clone's timings-journal lines past the stamp's offset into `run_dir`."""
    target = latest_log(clone)
    log = None
    if target is not None and target != stamp.get("latest_log"):
        source = clone / BUILD_LOGS / target
        if source.is_dir():
            remove(run_dir / "logs")
            shutil.copytree(source, run_dir / "logs", symlinks=True)
            log = target
    data, lines = journal_lines(clone / TIMINGS_JOURNAL, int(stamp.get("journal_offset") or 0))
    (run_dir / "journal.ndjson").write_bytes(data)
    return {"log": log, "lines": lines}


def tool_version(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    lines = (result.stdout or result.stderr).strip().splitlines()
    return lines[0] if lines else None


def command_output(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def daemon_answers(socket_path: Path) -> bool:
    try:
        with contextlib.closing(standing_client.connect(socket_path)):
            return True
    except OSError:
        return False


def server_kind(root: Path, *, main: Path = ROOT) -> str:
    """Return what the review server's port answers as seen from `root`'s own code (`artifact_cycle.probe_server().kind`), or `error: …` when the probe could not run. From the main checkout, a server it calls `current` or `stale` is rooted there, so every clone sees it as `foreign`, which is what this returns."""
    code = "from rebuild.tools import artifact_cycle; print(artifact_cycle.probe_server().kind)"
    result = subprocess.run(
        ["uv", "run", "python", "-c", code], cwd=root, env=child_env(main), capture_output=True, text=True
    )
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines:
        return f"error: {(result.stderr.strip().splitlines() or ['no output'])[-1]}"
    kind = lines[-1].strip()
    if Path(root).resolve() == Path(main).resolve() and kind in ("current", "stale"):
        return "foreign"
    return kind


TOOLS = {
    "hyperfine": ["hyperfine", "--version"],
    "rustc": ["rustc", "-V"],
    "uv": ["uv", "-V"],
    "git": ["git", "--version"],
    "typst": ["typst", "--version"],
}


def collect_probes(main: Path, clone: Path | None, *, driver_pid: int | None = None) -> dict:
    """Run every system probe once and return their raw outputs; `perf_check.summarize_probes` reads them. `clone` is the clone whose own server probe counts, or None before one exists."""
    journal = main / TIMINGS_JOURNAL
    probe_root = clone if clone is not None and (clone / ".git").exists() else main
    return {
        "at": time.time(),
        "pmset": command_output(["pmset", "-g"]),
        "therm": command_output(["pmset", "-g", "therm"]),
        "batt": command_output(["pmset", "-g", "batt"]),
        "pressure": command_output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]),
        "vm_stat": command_output(["vm_stat"]),
        "swapusage": command_output(["sysctl", "-n", "vm.swapusage"]),
        "pass_lock": store_lock.holder(main / PASS_LOCK),
        "standing_daemon": daemon_answers(main / "var" / "standing-daemon.sock"),
        "ps": command_output(["ps", "-axo", "pid=,ppid=,command="]),
        "exclude_pid": driver_pid if driver_pid is not None else os.getpid(),
        "main_journal_size": journal.stat().st_size if journal.is_file() else 0,
        "load_1m": os.getloadavg()[0],
        "server": server_kind(probe_root, main=main),
        "disk_free": shutil.disk_usage(main).free,
        "toolchain": {name: tool_version(argv) for name, argv in TOOLS.items()},
        "os_build": (command_output(["sw_vers", "-buildVersion"]) or "").strip() or None,
    }


def driver_pid_from_env() -> int | None:
    value = os.environ.get(DRIVER_PID_ENV)
    return int(value) if value and value.isdigit() else None


def append_ndjson(path: Path, entry: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def wait_until_quiet(
    main: Path,
    clone: Path | None,
    *,
    log: Path,
    phase: str = "prepare",
    collect: Callable[..., dict] = collect_probes,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    driver_pid: int | None = None,
    stop: Callable[[], bool] = lambda: False,
) -> tuple[dict, float]:
    """Probe until no condition `perf_check.probe_outcomes` marks as waiting holds, polling every `perf_check.POLL_S`, and return the last probe summary and the seconds waited, 0 when the first probe found nothing to wait for. Each probe is appended to `log`. A refusing condition, or a wait longer than `perf_check.WAIT_LIMIT_S`, raises PerfRefused. It also returns as soon as `stop` answers true, so the caller can act on a stop signal that arrived while it waited."""
    from rebuild.tools import perf_check

    started = now()
    polls = 0
    while True:
        summary = perf_check.summarize_probes(collect(main, clone, driver_pid=driver_pid))
        outcomes = perf_check.probe_outcomes(summary, phase=phase)
        append_ndjson(log, {"phase": phase, "probes": summary, "outcomes": [list(item) for item in outcomes]})
        refusals = [item.reason for item in outcomes if item.action == "refuse"]
        if refusals:
            raise PerfRefused("; ".join(refusals))
        waits = [item.reason for item in outcomes if item.action == "wait"]
        waited = now() - started if polls else 0.0
        if not waits or stop():
            return summary, waited
        if waited >= perf_check.WAIT_LIMIT_S:
            raise PerfRefused(f"waited {waited:.0f}s, past perf_check.WAIT_LIMIT_S, for: {'; '.join(waits)}")
        print(f"[perf] waiting: {'; '.join(waits)}", flush=True)
        sleep(perf_check.POLL_S)
        polls += 1


def next_run_dir(record: Path) -> Path:
    runs = record / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    taken = [int(path.name) for path in runs.iterdir() if path.name.isdigit()]
    run_dir = runs / str(max(taken, default=-1) + 1)
    run_dir.mkdir()
    return run_dir


def last_run_dir(record: Path) -> Path:
    runs = (
        [path for path in (record / "runs").iterdir() if path.name.isdigit()]
        if (record / "runs").is_dir()
        else []
    )
    if not runs:
        raise PerfRefused(f"{record} holds no prepared run to conclude")
    return max(runs, key=lambda path: int(path.name))


def prepare(
    clone: Path,
    snapshot: Path,
    start: str,
    patch: Path | None,
    record: Path,
    *,
    main: Path = ROOT,
    collect: Callable[..., dict] = collect_probes,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    lock_poll_s: float = LOCK_POLL_S,
) -> int:
    """hyperfine's `--prepare` hook (the module docstring lists its steps). Returns the exit status."""
    record.mkdir(parents=True, exist_ok=True)
    try:
        clone = check_clone_path(clone, main)
        install_shims(main)
        lock_wait = wait_for_pass_lock(clone, poll_s=lock_poll_s, sleep=sleep)
        reset_tracked(clone, start)
        restored = restore_snapshot(clone, snapshot)
        if patch is not None:
            apply_patch(clone, patch)
        summary, waited = wait_until_quiet(
            main,
            clone,
            log=record / "system.ndjson",
            collect=collect,
            sleep=sleep,
            now=now,
            driver_pid=driver_pid_from_env(),
        )
    except PerfRefused as refusal:
        (record / "refused.json").write_text(json.dumps({"reason": str(refusal), "at": now()}) + "\n")
        print(f"perf_clone prepare refused: {refusal}", file=sys.stderr)
        return 2
    run_dir = next_run_dir(record)
    stamp = {
        "k": int(run_dir.name),
        "start": start,
        "patch": None if patch is None else str(patch),
        "probes": summary,
        "waited_s": waited + lock_wait,
        "journal_restore": restored["journal"],
        "journal_rescan": restored["journal_rescan"],
        "latest_log": latest_log(clone),
        "main_journal_size": summary["main_journal_size"],
        "journal_offset": journal_size(clone),
    }
    stamp["prepared_at"] = now()
    (run_dir / "stamp.json").write_text(json.dumps(stamp, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def conclude(
    clone: Path,
    record: Path,
    *,
    main: Path = ROOT,
    collect: Callable[..., dict] = collect_probes,
    now: Callable[[], float] = time.time,
) -> int:
    """hyperfine's `--conclude` hook: harvest the run it follows, classify it and probe for what disturbed it. Exits 1 when the run is not READY or was disturbed, which stops hyperfine at once."""
    from rebuild.tools import perf_check

    concluded_at = now()
    try:
        clone = check_clone_path(clone, main)
        run_dir = last_run_dir(record)
    except PerfRefused as refusal:
        print(f"perf_clone conclude refused: {refusal}", file=sys.stderr)
        return 2
    stamp_path = run_dir / "stamp.json"
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    harvested = harvest(clone, run_dir, stamp)
    terminal = run_dir / "logs" / "terminal.log"
    log = (
        terminal.read_text(encoding="utf-8", errors="replace")
        if harvested["log"] and terminal.is_file()
        else ""
    )
    run_lines = [line for line in harvested["lines"] if line.get("kind") == "run"]
    inferred_exit = 0 if run_lines and all(line.get("exit") == "ok" for line in run_lines) else 1
    classification = perf_check.classify_run(log, inferred_exit)
    summary = perf_check.summarize_probes(collect(main, clone, driver_pid=driver_pid_from_env()))
    outcomes = perf_check.probe_outcomes(summary, phase="conclude", before=stamp.get("probes"))
    append_ndjson(
        record / "system.ndjson",
        {"phase": "conclude", "probes": summary, "outcomes": [list(item) for item in outcomes]},
    )
    disturbed = [item.reason for item in outcomes if item.action == "disturbed"]
    stamp.update(
        concluded_at=concluded_at,
        classification=classification,
        log=harvested["log"],
        conclude_probes=summary,
        disturbed="; ".join(disturbed) or None,
    )
    stamp_path.write_text(json.dumps(stamp, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if disturbed or classification != "ready" else 0


def last_run_line(lines: Iterable[Mapping]) -> dict | None:
    runs = [dict(line) for line in lines if line.get("kind") == "run"]
    return runs[-1] if runs else None


def run_pass(clone: Path, *, log: Path, main: Path = ROOT) -> PassResult:
    """Run one untimed cycle pass in the clone, as a timed run would, and classify it."""
    from rebuild.tools import perf_check

    install_shims(main)
    offset = journal_size(clone)
    before = latest_log(clone)
    returncode, elapsed = run_logged(cycle_argv(clone, main), cwd=clone, log=log, env=child_env(main))
    _, lines = journal_lines(clone / TIMINGS_JOURNAL, offset)
    target = latest_log(clone)
    terminal = (
        clone / BUILD_LOGS / target / "terminal.log" if target is not None and target != before else None
    )
    text = (
        terminal.read_text(encoding="utf-8", errors="replace")
        if terminal is not None and terminal.is_file()
        else ""
    )
    return PassResult(
        perf_check.classify_run(text, returncode), last_run_line(lines), text, returncode, elapsed
    )


def is_settled(result: PassResult, settle_shape: Mapping) -> bool:
    """Whether a pass has settled the state it ran on: it is READY and its run line holds the declared shape of the scenario that changes nothing (`perf_check.holds_declared`)."""
    from rebuild.tools import perf_check

    return (
        result.classification == "ready"
        and result.run_line is not None
        and not perf_check.holds_declared(result.run_line, settle_shape)
    )


def warm(
    clone: Path,
    ref: str,
    *,
    settle_shape: Mapping,
    log_dir: Path,
    label: str,
    max_passes: int,
    before_pass: Callable[[], object],
    from_snapshot: Path | None = None,
    main: Path = ROOT,
    retries: int = 0,
) -> list[PassResult]:
    """Warm the tree at `ref`: restore `from_snapshot` when given, reset the tracked files to `ref`, then run passes until one is settled (`is_settled`), at most `max_passes`. A refused or interrupted pass, the machine's doing, is run again after `before_pass` (the driver's probe wait, which also acts on a stop signal), at most `retries` times, and does not count toward the limit; past that, PerfRefused names it. A pass that fails or ends a harness failure stops the warm, and so does reaching the limit; WarmFailed names the failure or the pass that kept running. A green pass that is not yet READY, as a bootstrap's first-run pass is, counts toward the limit."""
    from rebuild.tools import perf_check

    if from_snapshot is not None:
        wait_for_pass_lock(clone)
        restore_snapshot(clone, from_snapshot)
    reset_tracked(clone, ref)
    results: list[PassResult] = []
    retried = 0
    while len(results) - retried < max_passes:
        before_pass()
        number = len(results) + 1
        result = run_pass(clone, log=log_dir / f"{label}-{number}.log", main=main)
        results.append(result)
        if is_settled(result, settle_shape):
            return results
        ended = f"{label}: pass {number} at {ref} ended {result.classification}{log_tail(result.log)}"
        if result.classification in RETRIED_PASSES:
            if retried >= retries:
                raise PerfRefused(ended)
            retried += 1
            continue
        green_not_ready = result.classification == "failed" and result.returncode == 0
        if result.classification != "ready" and not green_not_ready:
            raise WarmFailed(ended)
    last = results[-1]
    misses = (
        perf_check.holds_declared(last.run_line, settle_shape)
        if last.run_line is not None
        else ["no run line"]
    )
    raise WarmFailed(
        f"{label}: {ref} did not settle in {max_passes} passes; the last one ended {last.classification} "
        f"and still differs from a pass that changes nothing: {'; '.join(misses)}"
    )


def log_tail(text: str, lines: int = 12) -> str:
    kept = [line for line in text.splitlines() if line.strip()][-lines:]
    return (":\n    " + "\n    ".join(kept)) if kept else ""


def baseline_font_sha256s(out: Path) -> set[str]:
    """Return the `font_sha256` values the baseline headers under `out` carry."""
    found: set[str] = set()
    for path in sorted(out.glob("baseline-*.tsv.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("#"):
                    break
                if line.startswith("# font_sha256:"):
                    found.add(line.split(":", 1)[1].strip())
    return found


def ensure_baselines(clone: Path, *, main: Path, log: Path) -> str:
    """Make the clone's old-font baselines match its site Senior font: keep them when their headers name its raw digest, clonefile-copy the main checkout's when theirs do, and extract them otherwise. Nothing else is copied from the main checkout's `rebuild/out`."""
    font = file_sha256(clone / SENIOR_FONT)
    out = clone / REBUILD_OUT
    if baseline_font_sha256s(out) == {font}:
        return "kept"
    out.mkdir(parents=True, exist_ok=True)
    for pattern in BASELINE_FILES:
        for path in out.glob(pattern):
            remove(path)
    live = main / REBUILD_OUT
    if baseline_font_sha256s(live) == {font}:
        for pattern in BASELINE_FILES:
            for path in sorted(live.glob(pattern)):
                clonefile(path, out / path.name)
        return "copied"
    env = child_env(main)
    for argv in (
        ["uv", "run", "python", "-m", "rebuild.baseline.cli", "extract", "--all", "--out", "rebuild/out"],
        ["uv", "run", "python", "-m", "rebuild.baseline.cli", "summarize", "--out", "rebuild/out"],
    ):
        code, _ = run_logged(argv, cwd=clone, log=log, env=env)
        if code != 0:
            raise PerfRefused(f"{' '.join(argv)} failed in {clone} (log: {log})")
    return "extracted"


def bootstrapped(clone: Path) -> bool:
    return (clone / BOOTSTRAP_MARKER).is_file()


def remove_remotes(clone: Path) -> None:
    for remote in git(clone, "remote").split():
        git(clone, "remote", "remove", remote)


def bootstrap(clone: Path, base: str, *, main: Path, log: Path) -> dict:
    """Clone the main checkout into `clone` at `base`, removing `origin` before anything else runs, sync its environment and build the crate, timing each step, and write `BOOTSTRAP_MARKER` with the record as the last act. Whatever a failed or stopped bootstrap left at `clone` is deleted first."""
    clone = check_clone_path(clone, main)
    remove(clone)
    clone.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cloned = subprocess.run(
        ["git", "clone", "-q", "--no-checkout", str(main), str(clone)],
        capture_output=True,
        text=True,
        env=git_env(),
    )
    if cloned.returncode != 0:
        raise PerfRefused(f"git clone of {main} into {clone} failed: {cloned.stderr.strip()}")
    git(clone, "remote", "remove", "origin")
    git(clone, "checkout", "-q", "--detach", base)
    record: dict = {
        "clone": str(clone),
        "base": git(clone, "rev-parse", "HEAD"),
        "clone_s": time.monotonic() - started,
    }
    env = child_env(main)
    for name, argv in (("uv_sync", ["uv", "sync", "--frozen"]), ("kernel_build", ["make", "kernel-build"])):
        code, elapsed = run_logged(argv, cwd=clone, log=log, env=env)
        if code != 0:
            raise PerfRefused(f"{' '.join(argv)} failed in {clone} (log: {log})")
        record[f"{name}_s"] = elapsed
    (clone / BOOTSTRAP_MARKER).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record


def fetch(clone: Path, *, main: Path) -> None:
    git(
        clone,
        "fetch",
        "-q",
        "--no-tags",
        str(main),
        "+HEAD:refs/perf/main/HEAD",
        "+refs/heads/*:refs/perf/main/heads/*",
    )


def _commit_tree(clone: Path, index: Path, parent: str, message: str) -> str:
    env = {"GIT_INDEX_FILE": str(index)}
    tree = git(clone, "write-tree", env=env)
    return git(clone, *IDENTITY, "commit-tree", tree, "-p", parent, "-m", message)


def build_refs(
    clone: Path,
    side: str,
    base: str,
    *,
    candidate: Path | None,
    reverts: Mapping[str, Path],
    forwards: Mapping[str, Path],
    as_is: Iterable[str],
    exclude: Sequence[str] = (),
) -> dict[str, str | None]:
    """Build a side's local refs in the clone without touching its work tree or index: `refs/perf/<side>/tree` (`base` plus the candidate, less the paths `exclude`'s `git apply --exclude` patterns match, committed with a fixed identity), `refs/perf/<side>/composite` (the tree with every patch in `reverts` that reverses there reversed) and `refs/perf/<side>/start/<scenario>` (the tree with that scenario's patch reversed; the tree itself for a forward-form scenario, whose patch must apply there, and for each name in `as_is`). Return, per scenario, None when its start ref was built and otherwise why not. A candidate that does not apply at `base` raises PerfRefused."""
    index = clone / ".git" / f"perf-{side.replace('/', '-')}.index"
    env = {"GIT_INDEX_FILE": str(index)}
    prefix = f"refs/perf/{side}"
    try:
        tree = git(clone, "rev-parse", "--verify", f"{base}^{{commit}}")
        if candidate is not None:
            git(clone, "read-tree", tree, env=env)
            excludes = [f"--exclude={pattern}" for pattern in exclude]
            ok, message = git_ok(clone, "apply", "--cached", *excludes, str(candidate), env=env)
            if not ok:
                raise PerfRefused(f"the candidate does not apply at {base}: {message}")
            tree = _commit_tree(clone, index, tree, f"perf-check candidate {side}")
        git(clone, "update-ref", f"{prefix}/tree", tree)
        status: dict[str, str | None] = {}
        git(clone, "read-tree", tree, env=env)
        for name, patch in reverts.items():
            ok, message = git_ok(clone, "apply", "--cached", "-R", str(patch), env=env)
            status[name] = None if ok else f"its edit does not reverse on this side's tree: {message}"
        git(
            clone,
            "update-ref",
            f"{prefix}/composite",
            _commit_tree(clone, index, tree, f"perf-check composite {side}"),
        )
        for name, patch in reverts.items():
            if status[name] is not None:
                continue
            git(clone, "read-tree", tree, env=env)
            git(clone, "apply", "--cached", "-R", str(patch), env=env)
            git(
                clone,
                "update-ref",
                f"{prefix}/start/{name}",
                _commit_tree(clone, index, tree, f"perf-check start {name}"),
            )
        for name, patch in forwards.items():
            git(clone, "read-tree", tree, env=env)
            ok, message = git_ok(clone, "apply", "--cached", "--check", str(patch), env=env)
            status[name] = None if ok else f"its edit does not apply on this side's tree: {message}"
            if ok:
                git(clone, "update-ref", f"{prefix}/start/{name}", tree)
        for name in as_is:
            git(clone, "update-ref", f"{prefix}/start/{name}", tree)
            status[name] = None
        return status
    finally:
        remove(index)


def install_seed(clone: Path, seed: Path) -> None:
    """Write a scrubbed seed as the clone's verdict store."""
    target = clone / AUTOSAVE_NAME
    remove(target)
    shutil.copyfile(seed, target)


def delete_stashes(clone: Path) -> None:
    for path in clone.glob(STASH_PATTERN):
        remove(path)


def make_all(clone: Path, *, main: Path, log: Path) -> None:
    code, _ = run_logged(["make", "all"], cwd=clone, log=log, env=child_env(main))
    if code != 0:
        raise PerfRefused(f"make all failed in {clone} (log: {log})")


def prepare_root(
    clone: Path,
    ref: str,
    *,
    source: Path | None,
    target: Path,
    seed: Path | None,
    settle_shape: Mapping,
    log_dir: Path,
    label: str,
    max_passes: int,
    before_pass: Callable[[], object],
    main: Path = ROOT,
    retries: int = 0,
) -> list[PassResult]:
    """Prepare a side's root at `ref` and snapshot it into `target`: wait for the clone's pass lock, restore `source` (a root snapshot) when given, reset to `ref`, `make all` so the site fonts are this side's, bring the baselines in line with them (`ensure_baselines`), install `seed` as the verdict store when given, warm until settled, and delete every stash, so a start snapshot holds only stashes of the clone's own scrubbed lineage."""
    wait_for_pass_lock(clone)
    if source is not None:
        restore_snapshot(clone, source)
    reset_tracked(clone, ref)
    make_all(clone, main=main, log=log_dir / f"{label}-make-all.log")
    ensure_baselines(clone, main=main, log=log_dir / f"{label}-baselines.log")
    if seed is not None:
        install_seed(clone, seed)
    results = warm(
        clone,
        ref,
        settle_shape=settle_shape,
        log_dir=log_dir,
        label=label,
        max_passes=max_passes,
        before_pass=before_pass,
        main=main,
        retries=retries,
    )
    delete_stashes(clone)
    take_snapshot(clone, target, ref=ref)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="hyperfine's hooks around one timed run of the speed-up check."
    )
    sub = parser.add_subparsers(dest="hook", required=True)
    before = sub.add_parser("prepare", help="probe and wait, restore the start snapshot and apply the edit")
    before.add_argument("--clone", type=Path, required=True)
    before.add_argument("--snapshot", type=Path, required=True)
    before.add_argument("--start", required=True, help="the ref the tracked files are reset to")
    before.add_argument("--patch", type=Path, help="the scenario's edit, applied with git apply")
    before.add_argument("--record", type=Path, required=True)
    after = sub.add_parser("conclude", help="harvest and classify the run, and probe for what disturbed it")
    after.add_argument("--clone", type=Path, required=True)
    after.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.hook == "prepare":
        return prepare(args.clone, args.snapshot, args.start, args.patch, args.record)
    return conclude(args.clone, args.record)


if __name__ == "__main__":
    sys.exit(main())
