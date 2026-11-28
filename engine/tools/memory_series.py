"""The memory series: measure what the table build and the string replay hold and write at each of several alphabet cuts, one heavy process at a time, so a change that claims a term is flat shows it across the cuts instead of at the whole alphabet alone. It measures one holder a run, which `--holder` picks: the table build (`tables`, the default), whose step the paragraphs up to "The replay step" describe, or the string replay (`replay`), whose step "The replay step" describes.

Each table step is one `build-tables` over the configurations named (every settlement configuration unless `--configs` narrows them) on one alphabet cut, at the flags a bare run_build derives on this machine (`bare_flags`, as `run_build.build_tables` derives them with nothing beside the build: the width and how many deltas start from scratch beside `default`), unless `--kernel-threads` or `--scratch-beside-default` states one. Every step runs the same flags, so the cuts differ only in their alphabet. The cut is `scaling_series.sub_spec`'s, over `scaling_series.series_order`, the rule the alphabet switch (`spec_load.RUNES_ENV`) narrows by too, so a line's `alphabet` is what that switch takes to build the same cut in a tree of the fault kit's kind. The sizes default to `SIZES` below the whole alphabet and the whole alphabet itself, so the largest line is the live build's; positional arguments name other rune counts, and a count named twice is measured twice.

A step's build writes no file. Each configuration's windows payload goes through a pipe the crate inherits, named to it as `--windows=<config>=/dev/fd/<number>`, and is counted as it arrives (`scan`, through `kernel_exec.stream_payloads`, in `run_piped`), so it stands at no path; the bytes counted must be the bytes the crate answers it wrote (`counts`). The settlement and joins paths are symbolic links to the null device, which the crate opens with `File::create`, which does not truncate it. The crate's stdout and stderr come back through pipes. The `--inputs` stamp is `STAMP_PREFIX` and the rune count, so one cut hashes the same from run to run.

A table line holds, beside `step` (`tables`) and the cut (`runes`, `letters`, `ligs`, `configs`, the two flags and `modes`, the settlement modes `kernel_exec.mode_flags` passes):

- `wall_s` and `cpu_s`: the child's wall time and its own user and system time, from the rusage `os.wait4` reaps it with.
- `peak_resident` and `peak_footprint`: the child's resident peak from that rusage and its lifetime peak footprint, read between its exit and its reap as `peak_rss.reap_peaks_bytes` reads it. This module reaps the child itself, because that function discards the rusage the CPU figure comes from.
- `start`, `peak` and `end`: a sampler thread reads the child's resident set and footprint (`peak_rss.resident_bytes`, `peak_rss.footprint_bytes`) and the machine (`read_machine`: swap in use, `peak_rss.swap_used_bytes`; the compressor's occupancy, `peak_rss.parse_compressor_bytes`, and the swapout count, `peak_rss.parse_swapouts`, both over `vm_stat`; and the internal SSD's write counter, as the write meter reads it) every `--interval` seconds. `start` is the machine just before the spawn, `peak` the sample whose footprint was highest, and `end` the machine once the child is reaped beside the last sample's child figures, whose footprint reads None when that sample caught the child exiting (`peak_rss.footprint_bytes`). `compressor_max` and `swap_used_max` are the highest of every reading, and `samples` how many the sampler took.
- `windows`: each configuration's `[windows, rules, cells]`, counted from its payload as `counts` counts them. `digests`: each configuration's table digest from the child's stdout.
- `phases`: the child's `[t]` lines, label to seconds. `tables`: the `[c]` lines that give a collection's length and capacity, as `[len, cap, load]` by configuration and collection, the load being length over capacity. `resident`: the `[c]` lines that give a resident size, in bytes. `stats`: every other `[c]` line's fields (`cache_lines` says how a line is keyed).
- `written`: the write meter's fields (`engine/tools/write_meter.py`). The meter is read with the device `--idle-seconds` before the step and again just before the child's spawn, which gives the device's idle rate (`idle_rate`); then once the child is reaped and every payload's pipe drained (`end`), and again `--after-seconds` after that (`after`), since a write the kernel defers and the swap the pressure leaves reach the device late. Each of the two blocks reads from the reading just before the spawn: `basis`, `written` (the coalition's logical writes, `WriteDelta.bytes`), `diskio`, `device`, `device_net` (the device's delta less the idle rate over the same seconds), `swap` (the VM volume's writes), `swapouts` (the `vm_stat` swapout delta from `start`), `seconds`, and the meter's `warning` when it has one. `vm.swapusage` lags and does not fall back once the pressure ends, so the swapout delta is the swap reading a step owns. The window from that reading to `end` holds the build and the machine reading before its spawn, and nothing on the disk for the payloads, whose pipes have no path; it holds neither the step directory's making, with its links, nor its removal. Before the first step's idle reading the run flushes its own setup's writes (the crate's build, the binary's copy, the spec dumps, the index refresh `git describe` makes and the record's truncation) with `os.sync` and waits `SETTLE_S` for them to reach the device, so the first step's idle rate holds none of them. A basis other than `own` or a warning also prints a `[warn]` line.
- `alphabet` (the cut's rune names), `cpus` and `ram` (`memory_budget.usable_cores` and `total_memory_bytes`, which `doc/fleet.md` matches to a machine; no hostname is recorded), `head` (the commit, with `-dirty` after it when tracked files have changed) and `finished_at`.

The lines' writes read in the own basis only when nothing outside the series shares its resource coalition, which is what `make memory-series` gives it: it runs the series as a one-shot launchd job's own process (`engine/tools/job_launch.py`), as `make fault-kit` runs the kit, and follows its log in `tmp/memory-series.log`. Run another way, the series still runs, and each line's basis says what its figure is.

The series works in its own tree, `TREE_PATH` under this checkout's `var/perf/`, never in the live tree, and empties it as a run starts (`empty_tree`). For the table holder `prepare_tree` then copies the crate's release binary into it once `kernel_exec.cargo_build` has built it, so a crate rebuilt elsewhere during the series changes nothing it measures, and every cut's spec dump (`spec-r<runes>.json`) is written there before the first step, so a rune edited during the series changes nothing either. Each table step's directory, `r<runes>`, holds only its links to the null device and is removed when the step ends. A run holds `<tree>.lock` beside the tree (`store_lock.hold_flock`) and refuses when another run holds it, since two series would share the tree and each other's memory. It refuses too while the alphabet switch is set, because the series cuts its alphabets from the whole store itself; it never sets the switch. SIGINT, SIGTERM and SIGHUP stop a run as they stop a cycle pass (`artifact_cycle.stop_signals`), which matters because `job_launch` forwards each to the series alone: the running step's child is killed, the step's directory is removed, the lock is released, and the run exits with 128 plus the signal's number.

Each line prints to stdout as its step finishes and goes to `--out`, `OUT_PATH` by default, the record of each holder's last run, a file in the working tree that git does not track. The readings a constant's comment argues from are kept under `var/keep/`, where the comment cites them. A run replaces only its own holder's lines and fit there and keeps the other holder's as the file held them, holders in `HOLDERS` order (`Record`), rewriting the file as each line finishes. After a table run's last step both get the whole-series fit against runes of the windows, the peak footprint and resident, the CPU time and the first configuration's table lengths (`report`), the lengths rather than the table bytes, because a hash table's bytes move in doublings.

The replay step measures a full string replay as run_build runs one (`run_build.replay_and_absorb`): every configuration named (every settlement configuration unless `--configs` narrows them) walked over every text to `run_build.REPLAY_MAX_LENGTH`, one `replay-strings` process a configuration at most `--replay-threads` at once (the width a bare run_build derives, `run_build._replay_threads`, by default), each beside the spawn worker that absorbs its window memo from a pipe the crate inherits into the settle memo's columns, builds its index and holds the finished part until every walk has passed, or below the configuration count drops it at once while a walk still waits for a slot. The series' tree holds no settle memo, so every absorb takes the `first` route and lays its refill out as a whole base, today's file byte for byte (`conform.SettleMemoRoute`). The cuts default to `REPLAY_SIZES` below the whole alphabet and the whole alphabet; positional arguments name others, and a count named twice is measured twice. For each distinct cut the run first builds the tables once, unmeasured (`build_replay_tables`): one `build-tables` at the table holder's flags whose settlement and joins tables are written plain under `<tree>/replay/r<runes>/`, since the walks read them, about 0.05 GB a cut, and whose windows payloads are drained from pipes (`run_piped`); it writes the cut's spec dump and a pickle of the cut beside the tree's other spec dumps, and removes the tables as the run ends, however it ends. Each line then runs the replay in a child, `engine/tools/memory_series_child.py`'s `replay` entry, so the absorb workers import run_build's modules and the child's and not this module's. The child runs in `child_environment`, without the allocator setting, and takes this process's built kernel as its own (`--built`), so no `cargo build` runs inside a line. KERNEL_ALLOCATOR_ENVIRONMENT reaches the walks alone, as in run_build, since `kernel_exec.replay_memo_walk` adds it to each crate's environment and the absorb workers are Python processes: each crate runs with `--cache-stats`, and each absorb hashes its finished part's bytes in order and then discards it, as a red replay does, so a line writes no settle memo and commits no head. The walks and absorbs run the checkout's crate binary and code, as run_build's do.

A replay line holds, beside `step` (`replay`), the cut (`runes`, `letters`, `ligs`, `alphabet`), `configs`, `width`, `max_length` and `modes` (the settlement flags the walks take):

- `walks`: for each configuration, the walk's `counts`; its crate process's `peak_resident`, `peak_footprint` and `cpu_s` under `walk`, from the rusage `os.wait4` reaps it with and its footprint read before the reap (`peak_rss.ReapedPeaks`); its absorb worker's own `peak_resident`, `peak_footprint` and `cpu_s` under `absorb`, which the worker reads of itself as it ends, beside the absorbed file's `rows`, `bytes` and `sha256`, the seconds its read and index took and any warning; `pair`, the walk's crate and its absorb worker summed at each sample, the highest resident sum and the highest footprint sum; `phases`, the walk's `[t]` lines; and `cache`, from its `[c]` lines (`replay_cache`): the unit count, the largest unit's tables and its resident size after its release, the side memo, the row store and `resident_after_walk`.
- `combined`: every walk's crate and every absorb worker summed at each sample, the highest resident sum and the highest footprint sum, and `start`, `peak` and `end` as a table line holds them, the peak being the sample of the highest combined footprint, with `compressor_max`, `swap_used_max`, `samples`, `interval_s` and `wall_s`. The sampler reads the machine and every process below the child every `--interval` seconds (`_ReplayTree`), and tells each walk's crate by the configuration its command line names and each absorb worker by the pid its walk reports.
- `written`, as a table line holds it, and `cpus`, `ram`, `head` and `finished_at`.

After a replay run's last line the record and stdout get the fit against runes of the windows, the walk, absorb, pair and combined peaks, the walks' CPU time, the row store and the largest unit's walk memo (`replay_report`).

Run as: make memory-series ARGS='[--holder tables] [k ...] [--configs a,b] [--kernel-threads N] [--scratch-beside-default N] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]' or ARGS='--holder replay [k ...] [--configs a,b] [--replay-threads W] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]'.
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import json
import math
import os
import pickle
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, NamedTuple

from engine.pipeline import conform, kernel_exec, kernel_io, run_build, spec_load, table
from engine.pipeline.model import ResolvedSpec
from engine.review import store_lock
from engine.tools import (
    artifact_cycle,
    memory_budget,
    peak_rss,
    scaling_series,
    write_meter,
)
from engine.tools.console import INNER_LINE
from engine.tools.scratch_build import configs_from

ROOT = Path(__file__).resolve().parents[2]
TREE_PATH = "var/perf/series"
OUT_PATH = "engine/memory-series.txt"
BINARY_NAME = "ams-kernel"
SIZES = (34, 38, 41, 43, 44, 45)
IDLE_S = 60.0
AFTER_S = 300.0
SETTLE_S = 10.0
INTERVAL_S = 0.5
STAMP_PREFIX = "memory-series-r"
VM_STAT = "/usr/bin/vm_stat"
READ_CHUNK_BYTES = 1 << 20
# A `[c]` line's fields that say which instance of a collection it reports, such as `shared_memo_hits id=0` or a replay walk's `walk_memo unit=3`, rather than a figure.
CACHE_QUALIFIERS = ("id", "release", "unit")
_CACHE_LINE = re.compile(r"^\[c\] (\S+) (.+)$", re.MULTILINE)

TABLES = "tables"
REPLAY = "replay"
HOLDERS = (TABLES, REPLAY)
REPLAY_STEP = "replay"
HOLDER_OF_STEP = {
    TABLES: TABLES,
    REPLAY_STEP: REPLAY,
}
REPLAY_SIZES = (34, 38, 41)
CHILD_PIDS_MAX = 4096


@dataclass(frozen=True)
class Flags:
    """The table build's width and how many deltas it starts from scratch beside `default`."""

    threads: int
    scratch_beside_default: int


@dataclass(frozen=True)
class Machine:
    """One reading of the machine: swap in use, the compressor's occupancy in bytes, the swapout count in pages, and the internal SSD's write counter, each None where it cannot be read."""

    swap_used: int | None
    compressor: int | None
    swapouts: int | None
    device: int | None


QUIET = Machine(None, None, None, None)


@dataclass(frozen=True)
class Sample:
    """One sample of a running child: seconds since its spawn, its resident set and footprint, and the machine."""

    at: float
    resident: int | None
    footprint: int | None
    machine: Machine


@dataclass(frozen=True)
class ChildRun:
    """What `run_child` returns: the child's exit status, stdout and stderr, wall and CPU seconds, resident and footprint peaks, the machine before its spawn, its samples, and the machine after its reap."""

    returncode: int
    stdout: bytes
    stderr: str
    wall: float
    cpu: float
    peak_resident: int
    peak_footprint: int | None
    begin: Machine
    samples: tuple[Sample, ...]
    end: Machine


def default_sizes(count: int, sizes: Sequence[int] = SIZES) -> list[int]:
    """The rune counts a run cuts at when none is named: `count`, the whole alphabet's size, and each of `sizes` below it, `SIZES` for the table holder and `REPLAY_SIZES` for the replay holder."""
    return sorted({*(size for size in sizes if size < count), count})


def bare_flags(
    configs: Sequence[str],
    *,
    threads: int | None = None,
    scratch: int | None = None,
) -> Flags:
    """The flags a bare run_build builds `configs` at on this machine, derived as `run_build.build_tables` derives them with nothing beside the build, each replaced by the one stated. A stated width is capped at the configuration count and the cores, as run_build caps it."""
    from_scratch = "default" not in configs
    width = run_build._table_build_threads(threads, len(configs), from_scratch=from_scratch)
    if scratch is None:
        scratch = kernel_exec.deltas_from_scratch(width, configs=len(configs), from_scratch=from_scratch)
    return Flags(width, scratch)


def step_arguments(
    binary: Path, spec_path: Path, out_dir: Path, configs: Sequence[str], stamp: str, flags: Flags
) -> list[str]:
    """The `build-tables` command line of one step, with its cache stats and timings on and its windows stamped `stamp`."""
    arguments = [
        str(binary),
        "build-tables",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--inputs={stamp}",
        f"--threads={flags.threads}",
        *kernel_exec.mode_flags(),
        "--cache-stats",
        "--timings",
    ]
    if flags.scratch_beside_default:
        arguments.append(f"--scratch-beside-default={flags.scratch_beside_default}")
    return arguments


def route(out_dir: Path, configs: Sequence[str]) -> None:
    """Make `out_dir`, which must not exist, with each configuration's settlement and joins paths linked to the null device. The windows payloads need nothing here: each goes through a pipe the crate inherits (`run_piped`), so it stands at no path."""
    out_dir.mkdir(parents=True)
    for config in configs:
        settlement, joins = run_build.config_table_files(out_dir, config)[:2]
        settlement.symlink_to(os.devnull)
        joins.symlink_to(os.devnull)


def _vm_stat() -> str | None:
    try:
        return subprocess.run([VM_STAT], capture_output=True, text=True, check=True, timeout=30).stdout
    except OSError, subprocess.SubprocessError:
        return None


def read_machine() -> Machine:
    """Read the machine (`Machine`): swap in use from `peak_rss.swap_used_bytes`, the compressor's occupancy and the swapout count from one `vm_stat`, and the device's write counter from the `ioreg` run the write meter makes."""
    text = _vm_stat()
    device, _swap = write_meter._read_device()
    return Machine(
        peak_rss.swap_used_bytes(),
        peak_rss.parse_compressor_bytes(text),
        peak_rss.parse_swapouts(text),
        device,
    )


class Sampler:
    """A daemon thread that samples one child (`Sample`) every `interval` seconds, from `start` until `stop`, which returns the samples. `tick`, when given, is called after each sample with its time, which the replay step reads the child's descendants on (`_ReplayTree`)."""

    def __init__(
        self,
        pid: int,
        interval: float,
        machine: Callable[[], Machine],
        origin: float,
        tick: Callable[[float], None] | None = None,
    ) -> None:
        self._pid = pid
        self._interval = interval
        self._machine = machine
        self._origin = origin
        self._tick = tick
        self._stopped = threading.Event()
        self._samples: list[Sample] = []
        self._thread = threading.Thread(target=self._run, name=f"sampler {pid}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            at = time.perf_counter() - self._origin
            resident = peak_rss.resident_bytes(self._pid)
            footprint = peak_rss.footprint_bytes(self._pid)
            self._samples.append(Sample(at, resident, footprint, self._machine()))
            if self._tick is not None:
                self._tick(at)
            if self._stopped.wait(self._interval):
                return

    def stop(self) -> tuple[Sample, ...]:
        self._stopped.set()
        if self._thread.ident is not None:
            self._thread.join()
        return tuple(self._samples)


def _drain(drained: dict[str, bytes], name: str, stream: IO[bytes]) -> None:
    with stream:
        drained[name] = stream.read()


def run_child(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    interval: float,
    machine: Callable[[], Machine] = read_machine,
    payloads: kernel_exec.PayloadPipes | None = None,
) -> ChildRun:
    """Run one kernel child with its stdout and stderr on pipes drained by threads of their own, sampled (`Sampler`) until it exits, and return what it did (`ChildRun`). With `payloads`, the child also inherits the write end of each payload's pipe (`kernel_exec.PayloadPipes`), and this process closes its own copies right after the spawn, and after a failed spawn too, so each payload's reader meets end of file when the child exits. Once both pipes close it waits for the exit without reaping (`os.waitid` with `WNOWAIT`), stops the sampler, reads the child's peak footprint, and reaps it with `os.wait4`, whose rusage gives its resident peak and CPU time. The sampler stops before the reap, so it never reads a pid the reap has freed. An interrupt kills and reaps the child before it propagates, so no crate outlives the end of its payloads' reads (`kernel_exec.stream_payloads`)."""
    begin = machine()
    origin = time.perf_counter()
    try:
        process = subprocess.Popen(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            pass_fds=() if payloads is None else payloads.pass_fds,
        )
    finally:
        if payloads is not None:
            payloads.close_write_ends()
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    readers = [
        threading.Thread(target=_drain, args=(drained, name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    sampler = Sampler(process.pid, interval, machine, origin)
    try:
        sampler.start()
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        samples = sampler.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        process.kill()
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    return ChildRun(
        process.returncode,
        drained["stdout"],
        drained["stderr"].decode(errors="replace"),
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )


def table_answers(stdout: bytes, configs: Sequence[str]) -> list[dict[str, Any]]:
    """Read a `build-tables` child's stdout as `kernel_exec.build_table_files` reads it and return its answers: one `{config, digest, windows_bytes}` object (`kernel_exec.TABLE_ANSWER_KEYS`) for each of `configs`, in the order named, where `windows_bytes` is how many bytes the child wrote of that configuration's window payload. Any other line, or answers for any other configurations, stops the run."""
    answers: list[dict[str, Any]] = []
    for number, line in enumerate(stdout.decode(errors="replace").splitlines(), 1):
        try:
            answer = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit(f"build-tables line {number} is not JSON: {error.msg}") from None
        if (
            not isinstance(answer, dict)
            or set(answer) != kernel_exec.TABLE_ANSWER_KEYS
            or type(answer["windows_bytes"]) is not int
        ):
            raise SystemExit(f"build-tables line {number} is not a {{config, digest, windows_bytes}} answer")
        answers.append(answer)
    named = [answer["config"] for answer in answers]
    if named != list(configs):
        raise SystemExit(f"build-tables answered for {named}, not for {list(configs)}")
    return answers


class Scan(NamedTuple):
    """What `scan` read of one payload: its head line, its column line, how many lines follow them, and how many bytes it received in all."""

    head: bytes
    columns: bytes
    rows: int
    received: int


def scan(source: IO[bytes]) -> Scan:
    """Read one payload to its end and return its head line, its column line, how many lines follow them and how many bytes it read in all (`Scan`). The body is counted a chunk at a time instead of loaded, so this process never holds a cut's millions of rows. Nothing is parsed here, so a read of a stream a failing child cut short ends as the child does; `counts` checks the bytes received and parses what this kept."""
    head = source.readline()
    columns = source.readline()
    received = len(head) + len(columns)
    rows = 0
    last = b"\n"
    while chunk := source.read(READ_CHUNK_BYTES):
        rows += chunk.count(b"\n")
        received += len(chunk)
        last = chunk[-1:]
    return Scan(head, columns, rows if last == b"\n" else rows + 1, received)


def counts(scanned: Scan, payload: Path, written: int) -> tuple[int, int, int]:
    """Return one configuration's window, rule and reachable-cell counts from what `scan` read of the payload the child wrote at `payload`, or through a pipe, which goes by that name in a refusal. `written` is the bytes the child answered it wrote of the payload (`table_answers`), and a payload whose bytes received differ stops the run before anything is parsed, since a payload missing bytes would give short counts. The header gives the rules and cells, and the lines after the column line are the windows."""
    if scanned.received != written:
        raise SystemExit(
            f"{payload}: the window payload's reader received {scanned.received} bytes where the crate wrote {written}"
        )
    _stamp, decision = table.read_windows(kernel_exec.payload_head(scanned.head, payload), windows=False)
    if tuple(scanned.columns.decode().rstrip("\n").split("\t")) != table.WINDOWS_COLUMNS:
        raise SystemExit(f"{payload}: window columns are not {table.WINDOWS_COLUMNS}")
    return scanned.rows, len(decision.rules), len(decision.reachable_cells())


def fit(xs: list[int], ys: list[float | None]) -> float | None:
    """Return the least-squares slope of ln y on ln x, the whole-series exponent to quote instead of any one consecutive pair. A point whose y is None is left out. Returns None when fewer than two points have positive x and y, or when every point has the same x."""
    points = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y is not None and y > 0]
    if len(points) < 2:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    variance = sum((x - mean_x) ** 2 for x, _ in points)
    if not variance:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / variance


def exponent(slope: float | None) -> str:
    """Return a fitted slope at two decimals, or `n/a` for no fit."""
    return "n/a" if slope is None else f"{slope:.2f}"


def check_child(
    child: ChildRun, configs: Sequence[str], arguments: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Hold the child to the CLI contract `kernel_exec.build_table_files` holds a build to and return its answers by configuration, each its `config`, `digest` and `windows_bytes` (`kernel_exec.TABLE_ANSWER_KEYS`): exit 2 is a usage error, any other nonzero exit an error about the inputs, stdout one such line for each configuration in the order named (`table_answers`), and stderr on a clean exit only `[t]` and `[c]` lines."""
    if child.returncode == 2:
        raise SystemExit(
            f"the kernel rejected the invocation as a usage error: {child.stderr.strip()} ({' '.join(arguments)})"
        )
    if child.returncode != 0:
        raise SystemExit(f"the kernel exited {child.returncode} on build-tables: {child.stderr.strip()}")
    answers = table_answers(child.stdout, configs)
    stray = [line for line in child.stderr.splitlines() if line and not line.startswith(("[t] ", "[c] "))]
    if stray:
        raise SystemExit(f"the kernel wrote a line that is neither [t] nor [c] on a clean exit: {stray[0]}")
    return {answer["config"]: answer for answer in answers}


def run_piped[T](
    arguments: Sequence[str],
    configs: Sequence[str],
    read: Callable[[IO[bytes]], T],
    *,
    env: Mapping[str, str] | None,
    interval: float,
    machine: Callable[[], Machine],
) -> tuple[list[str], ChildRun, dict[str, T]]:
    """Run one `build-tables` child over `configs` (`run_child`) with each configuration's windows payload sent into a pipe the child inherits, named to it as `--windows=<config>=/dev/fd/<number>` after `arguments`, and read by `read` on a thread of its own as the child writes it (`kernel_exec.stream_payloads`), so no payload reaches the disk or stands at a path another process could open. Return the command line the child ran, the child, and what `read` returned for each configuration."""
    spawned = list(arguments)

    def spawn(pipes: kernel_exec.PayloadPipes[T]) -> ChildRun:
        spawned.extend(pipes.windows_flags(configs))
        return run_child(spawned, env=env, interval=interval, machine=machine, payloads=pipes)

    child, read_back = kernel_exec.stream_payloads(configs, lambda _config, source: read(source), spawn)
    return spawned, child, read_back


def cache_lines(stderr: str) -> dict[str, dict[str, dict[str, object]]]:
    """Return the child's `[c]` lines by kind, each keyed by configuration and then by name: `tables` holds `[len, cap, load]` for a line whose fields are `len` and `cap`, the load being length over capacity (None at a capacity of 0), `resident` holds bytes for a line whose one field is `kb`, and `stats` holds every other line's fields. A line's name is its words that are not fields, followed by its `CACHE_QUALIFIERS` fields, so `shared_memo_hits id=0 count=130` is `stats`' `shared_memo_hits id=0` with `{"count": 130}`. A field that is not an integer stays a string."""
    kinds: dict[str, dict[str, dict[str, object]]] = {"tables": {}, "resident": {}, "stats": {}}
    for config, rest in _CACHE_LINE.findall(stderr):
        words = rest.split()
        fields = dict(word.split("=", 1) for word in words if "=" in word)
        qualifiers = [f"{key}={fields.pop(key)}" for key in CACHE_QUALIFIERS if key in fields]
        name = " ".join([*(word for word in words if "=" not in word), *qualifiers])
        values: dict[str, object] = {
            key: int(value) if value.isdecimal() else value for key, value in fields.items()
        }
        length, capacity, kb = values.get("len"), values.get("cap"), values.get("kb")
        if values.keys() == {"len", "cap"} and isinstance(length, int) and isinstance(capacity, int):
            load = round(length / capacity, 3) if capacity else None
            kinds["tables"].setdefault(config, {})[name] = [length, capacity, load]
        elif values.keys() == {"kb"} and isinstance(kb, int):
            kinds["resident"].setdefault(config, {})[name] = kb * 1024
        else:
            kinds["stats"].setdefault(config, {})[name] = values
    return kinds


def _moment(at: float, resident: int | None, footprint: int | None, machine: Machine) -> dict[str, object]:
    return {"at_s": round(at, 3), "resident": resident, "footprint": footprint, **asdict(machine)}


def _highest(values: Iterable[int | None]) -> int | None:
    return max((value for value in values if value is not None), default=None)


def moments(child: ChildRun) -> dict[str, object]:
    """Return a child's `start`, `peak` and `end` moments and the machine's highest compressor occupancy and swap in use, as the module docstring describes them. The peak is the sample with the highest footprint, or the highest resident set where no sample read a footprint."""
    samples = child.samples
    top = max(samples, key=lambda sample: sample.footprint or sample.resident or 0, default=None)
    last = samples[-1] if samples else None
    readings = [child.begin, *(sample.machine for sample in samples), child.end]
    return {
        "start": _moment(0.0, None, None, child.begin),
        "peak": None if top is None else _moment(top.at, top.resident, top.footprint, top.machine),
        "end": _moment(
            child.wall,
            None if last is None else last.resident,
            None if last is None else last.footprint,
            child.end,
        ),
        "compressor_max": _highest(reading.compressor for reading in readings),
        "swap_used_max": _highest(reading.swap_used for reading in readings),
    }


def idle_rate(earlier: write_meter.WriteReading, later: write_meter.WriteReading) -> float | None:
    """The device's write rate between two readings taken while the series ran nothing, in bytes a second, or None when either lacks the device or no time passed."""
    seconds = later.at - earlier.at
    if earlier.device is None or later.device is None or seconds <= 0:
        return None
    return (later.device - earlier.device) / seconds


def write_fields(
    start: write_meter.WriteReading,
    later: write_meter.WriteReading,
    rate: float | None,
    swapouts: int | None,
) -> dict[str, object]:
    """One `written` block (the module docstring): the meter's delta from `start` to `later`, `device_net` its device delta less `rate` over the same seconds, and the swapout delta the caller read beside it."""
    delta = write_meter.delta(start, later)
    net = None
    if rate is not None and delta.device_bytes is not None:
        net = delta.device_bytes - round(rate * delta.seconds)
    block: dict[str, object] = {
        "basis": delta.basis,
        "written": delta.bytes,
        "diskio": delta.diskio_bytes,
        "device": delta.device_bytes,
        "device_net": net,
        "swap": delta.swap_bytes,
        "swapouts": swapouts,
        "seconds": round(delta.seconds, 3),
    }
    if delta.warning is not None:
        block["warning"] = delta.warning
    return block


def _difference(earlier: int | None, later: int | None) -> int | None:
    return None if earlier is None or later is None else later - earlier


def measure_step(
    binary: Path,
    spec_path: Path,
    step_dir: Path,
    configs: Sequence[str],
    flags: Flags,
    stamp: str,
    *,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    env: Mapping[str, str] | None = None,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one step in `step_dir`, which must not exist and is removed once the meter has read the step's end, so the window from the meter's reading just before the child's spawn to its reading at the end holds the machine reading before the spawn and the build, whose payloads go through pipes with no path (`run_piped`), and neither the directory's making, with its links (`route`), nor its removal, and return its measured fields, everything a line holds but the cut, the flags and the provenance (the module docstring lists them). A payload whose bytes received differ from the bytes the crate answers it wrote stops the run (`counts`). `meter`, `machine` and `sleep` are the write meter, the machine reading and the wait, which a test replaces."""
    arguments = step_arguments(binary, spec_path, step_dir, configs, stamp, flags)
    try:
        route(step_dir, configs)
        idle = None
        if idle_s > 0:
            idle = meter()
            sleep(idle_s)
        start = meter()
        arguments, child, scanned = run_piped(
            arguments, configs, scan, env=env, interval=interval_s, machine=machine
        )
        end = meter()
    finally:
        shutil.rmtree(step_dir, ignore_errors=True)
    rate = None if idle is None else idle_rate(idle, start)
    answers = check_child(child, configs, arguments)
    digests = {config: answer["digest"] for config, answer in answers.items()}
    windows: dict[str, list[int]] = {}
    for config in configs:
        payload = kernel_exec.windows_payload(step_dir, config)
        windows[config] = list(counts(scanned[config], payload, answers[config]["windows_bytes"]))
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        swapouts = _difference(child.begin.swapouts, machine().swapouts)
        after = write_fields(start, later, rate, swapouts)
    return {
        "wall_s": round(child.wall, 3),
        "cpu_s": round(child.cpu, 3),
        "peak_resident": child.peak_resident,
        "peak_footprint": child.peak_footprint,
        "samples": len(child.samples),
        "interval_s": interval_s,
        **moments(child),
        "windows": windows,
        "digests": digests,
        "phases": {match.group(1): float(match.group(2)) for match in INNER_LINE.finditer(child.stderr)},
        **cache_lines(child.stderr),
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, child.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
    }


def empty_tree(tree: Path) -> None:
    """Make the series' tree at `tree` and empty it of what an earlier run left. A symbolic link at `tree` is refused, since emptying it would empty whatever it names."""
    if tree.is_symlink():
        raise SystemExit(
            f"{tree} is a symbolic link; the memory series empties its tree and will not follow one"
        )
    tree.mkdir(parents=True, exist_ok=True)
    for path in tree.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def prepare_tree(tree: Path, binary: Path) -> Path:
    """Empty the series' tree (`empty_tree`), copy `binary` into it, and return the copy, run once with no arguments so its first-exec scan is over before any step's clock starts."""
    empty_tree(tree)
    copy = tree / BINARY_NAME
    shutil.copy2(binary, copy)
    subprocess.run([str(copy)], capture_output=True, check=False)
    return copy


def report(lines: Sequence[Mapping]) -> list[str]:
    """Return the whole-series fit against runes (`fit`) of the windows over every configuration, the peak footprint and resident, the CPU time, and the length of each collection the first configuration's `tables` name, one figure a line. A figure a line lacks is left out of its fit."""
    if len(lines) < 2:
        return [f"the whole-series fit needs two sizes; this run has {len(lines)}"]
    runes = [line["runes"] for line in lines]
    figures: dict[str, list[float | None]] = {
        "windows": [sum(counts[0] for counts in line["windows"].values()) for line in lines],
        "peak footprint": [line["peak_footprint"] for line in lines],
        "peak resident": [line["peak_resident"] for line in lines],
        "cpu": [line["cpu_s"] for line in lines],
    }
    config = lines[0]["configs"][0]
    names = sorted({name for line in lines for name in line["tables"].get(config, {})})
    for name in names:
        figures[f"{config} {name} len"] = [
            line["tables"].get(config, {}).get(name, [None])[0] for line in lines
        ]
    width = max(len(label) for label in figures)
    return [
        f"whole-series fit over {len(lines)} sizes (runes {min(runes)}..{max(runes)})",
        *(f"  {label:<{width}} ~ runes^{exponent(fit(runes, values))}" for label, values in figures.items()),
    ]


def child_environment(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """A replay child's environment: this process's, without the cycle's run id, which a measured child should not see."""
    from engine.tools import cycle_timings

    return {key: value for key, value in environ.items() if key != cycle_timings.CYCLE_RUN_ENV}


@functools.cache
def _proc_listchildpids() -> Callable[..., int] | None:
    if sys.platform != "darwin":
        return None
    call = ctypes.CDLL(None, use_errno=True).proc_listchildpids
    call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    call.restype = ctypes.c_int
    return call


def child_pids(pid: int) -> list[int]:
    """The pids whose parent is `pid`: libproc's `proc_listchildpids` on Darwin, `/proc`'s children list elsewhere, and none where neither answers."""
    call = _proc_listchildpids()
    if call is None:
        try:
            return [int(word) for word in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
        except OSError:
            return []
    buffer = (ctypes.c_int * CHILD_PIDS_MAX)()
    count = call(pid, buffer, ctypes.sizeof(buffer))
    return [value for value in buffer[: max(0, count)] if value > 0]


def descendants(pid: int) -> list[tuple[int, int]]:
    """Every process below `pid`, each with its depth, its children's children included."""
    found: list[tuple[int, int]] = []
    frontier = [(pid, 0)]
    while frontier:
        parent, depth = frontier.pop()
        for child in child_pids(parent):
            found.append((child, depth + 1))
            frontier.append((child, depth + 1))
    return found


def _command(pid: int) -> str:
    done = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return done.stdout.strip()


def build_replay_tables(
    spec_path: Path,
    cut_dir: Path,
    configs: Sequence[str],
    flags: Flags,
    stamp: str,
    *,
    env: Mapping[str, str] | None,
) -> None:
    """Build one cut's tables, unmeasured, for the replay holder's lines to walk: one `build-tables` at the flags the table holder's step takes, its settlement and joins tables written plain into `cut_dir`, which must not exist, and each windows payload drained from a pipe the crate inherits (`run_piped`), so no payload reaches the disk. A build that fails stops the run (`check_child`)."""
    cut_dir.mkdir(parents=True)
    arguments = [
        str(kernel_exec.BINARY),
        "build-tables",
        str(spec_path),
        str(cut_dir),
        f"--configs={','.join(configs)}",
        f"--inputs={stamp}",
        f"--threads={flags.threads}",
        *kernel_exec.mode_flags(),
    ]
    if flags.scratch_beside_default:
        arguments.append(f"--scratch-beside-default={flags.scratch_beside_default}")
    arguments, child, _drained = run_piped(
        arguments, configs, _discard, env=env, interval=INTERVAL_S, machine=lambda: QUIET
    )
    check_child(child, configs, arguments)


def _discard(source: IO[bytes]) -> None:
    while source.read(READ_CHUNK_BYTES):
        pass


@dataclass(frozen=True)
class ReplayReading:
    """One reading of a replay line's processes: seconds since the child's spawn and each process's resident set and footprint, by pid."""

    at: float
    readings: dict[int, tuple[int | None, int | None]]


class _ReplayTree:
    """The processes below a replay line's child, as the sampler's `tick` reads them: every process's resident set and footprint at each tick (`ReplayReading`), and each one's command, read again until it names the crate or multiprocessing, since a process caught between its fork and its exec still shows its parent's."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self.readings: list[ReplayReading] = []
        self.commands: dict[int, str] = {}

    def scan(self, at: float) -> None:
        seen: dict[int, tuple[int | None, int | None]] = {}
        for pid, _depth in descendants(self._pid):
            command = self.commands.get(pid, "")
            if BINARY_NAME not in command and "multiprocessing" not in command:
                self.commands[pid] = _command(pid) or command
            seen[pid] = (peak_rss.resident_bytes(pid), peak_rss.footprint_bytes(pid))
        self.readings.append(ReplayReading(at, seen))


@dataclass(frozen=True)
class ReplayRun:
    """What `run_replay_child` returns: the child as `run_child` returns one, every reading of the processes below it, their commands, and what the child printed about the replay (`memory_series_child.replay_main`)."""

    child: ChildRun
    readings: tuple[ReplayReading, ...]
    commands: dict[int, str]
    replay: dict[str, Any]


def run_replay_child(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    interval: float,
    machine: Callable[[], Machine] = read_machine,
) -> ReplayRun:
    """Run one replay child in a session of its own, with its stdout and stderr drained and the `interval` sampler, which reads the child and the machine, also reading every process below it (`_ReplayTree`). Once both pipes close and the child has exited, unreaped, the sampler stops, the child's peak footprint is read, and it is reaped. A child that exits nonzero, or prints no replay, stops the run. An exception while it runs kills the child's whole process group, its walks and absorb workers with it, and reaps the child before it propagates."""
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        list(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    readers = [
        threading.Thread(target=_drain, args=(drained, name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    tree = _ReplayTree(process.pid)
    sampler = Sampler(process.pid, interval, machine, origin, tick=tree.scan)
    try:
        for reader in readers:
            reader.start()
        sampler.start()
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        samples = sampler.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    stderr = drained["stderr"].decode(errors="replace")
    lines = [line for line in drained["stdout"].decode(errors="replace").splitlines() if line.strip()]
    if process.returncode != 0 or not lines or not lines[-1].startswith("{"):
        raise SystemExit(
            f"the replay child exited {process.returncode}: {stderr.strip() or 'it printed no replay'}"
        )
    child = ChildRun(
        process.returncode,
        drained["stdout"],
        stderr,
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )
    return ReplayRun(child, tuple(tree.readings), dict(tree.commands), json.loads(lines[-1]))


def replay_child_arguments(
    spec: Path, spec_dump: Path, out: Path, configs: Sequence[str], width: int, *, built: bool
) -> list[str]:
    """The command line of one replay line's child (`memory_series_child.replay_main`). `built` says this process's own `kernel_exec.ensure_built()` has succeeded, which the child then takes as its own, so it runs no `cargo build` inside the line it measures."""
    return [
        sys.executable,
        "-m",
        "engine.tools.memory_series_child",
        "replay",
        "--spec",
        str(spec),
        "--spec-dump",
        str(spec_dump),
        "--out",
        str(out),
        "--configs",
        ",".join(configs),
        "--width",
        str(width),
        *(["--built"] if built else []),
    ]


def crate_configs(commands: Mapping[int, str]) -> dict[int, str]:
    """Each walk's crate process, by pid, with the configuration its command line names (`--configs=`)."""
    found: dict[int, str] = {}
    for pid, command in commands.items():
        words = command.split()
        if "replay-strings" not in words or not any(word.endswith(BINARY_NAME) for word in words):
            continue
        for word in words:
            if word.startswith("--configs="):
                found[pid] = word.removeprefix("--configs=")
    return found


def _pair_sums(
    reading: ReplayReading, crates: Mapping[int, str], workers: Mapping[int, str]
) -> dict[str, tuple[int, int]]:
    """Each configuration's walk crate and absorb worker summed in one reading, as resident and footprint, a process missing from the reading or a figure it could not read counting nothing."""
    sums: dict[str, tuple[int, int]] = {}
    for pid, (resident, footprint) in reading.readings.items():
        config = crates.get(pid) or workers.get(pid)
        if config is not None:
            held = sums.get(config, (0, 0))
            sums[config] = (held[0] + (resident or 0), held[1] + (footprint or 0))
    return sums


def _combined(sums: Mapping[str, tuple[int, int]]) -> tuple[int, int]:
    return sum(resident for resident, _ in sums.values()), sum(footprint for _, footprint in sums.values())


def replay_peaks(
    readings: Sequence[ReplayReading], crates: Mapping[int, str], workers: Mapping[int, str]
) -> tuple[dict[str, dict[str, int]], dict[str, int], tuple[float, int, int] | None]:
    """Each configuration's pair peak, its walk's crate and its absorb worker summed at each reading (`_pair_sums`), as the highest resident sum and the highest footprint sum; the step's combined peak, every crate and absorb worker summed the same way; and the reading whose combined footprint, or combined resident set where no footprint was read, is highest, as its time and its combined resident and footprint."""
    pairs = {config: {"resident": 0, "footprint": 0} for config in (*crates.values(), *workers.values())}
    combined = {"resident": 0, "footprint": 0}
    top: tuple[float, int, int] | None = None
    for reading in readings:
        sums = _pair_sums(reading, crates, workers)
        for config, (resident, footprint) in sums.items():
            pairs[config]["resident"] = max(pairs[config]["resident"], resident)
            pairs[config]["footprint"] = max(pairs[config]["footprint"], footprint)
        resident, footprint = _combined(sums)
        combined["resident"] = max(combined["resident"], resident)
        combined["footprint"] = max(combined["footprint"], footprint)
        if top is None or (footprint or resident) > (top[2] or top[1]):
            top = (reading.at, resident, footprint)
    return pairs, combined, top


def replay_cache(lines: Sequence[str], config: str) -> dict[str, Any]:
    """One walk's `[c]` lines as a replay line keeps them (`cache_lines`): the unit count, the largest unit, the one whose walk memo held the most windows when it was released, with every table its release reported and its resident size after the release, and at the walk's end the side memo, the row store and the resident size after the walk."""
    kinds = cache_lines("\n".join(lines))
    tables = kinds["tables"].get(config, {})
    resident = kinds["resident"].get(config, {})
    stats = kinds["stats"].get(config, {})
    released = {
        name: value[0]
        for name, value in tables.items()
        if name.startswith("walk_memo unit=") and isinstance(value, list)
    }
    largest = max(released, key=released.__getitem__, default=None)
    unit = None
    if largest is not None:
        qualifier = largest.removeprefix("walk_memo ")
        unit = {
            "unit": int(qualifier.removeprefix("unit=")),
            "tables": {
                name.removesuffix(f" {qualifier}"): value
                for name, value in tables.items()
                if name.endswith(f" {qualifier}")
            },
            "resident_after_release": resident.get(f"resident_after_release {qualifier}"),
        }
    units = stats.get("units", {})
    return {
        "units": units.get("count") if isinstance(units, dict) else None,
        "largest_unit": unit,
        "side_memo": tables.get("side_memo"),
        "row_store": stats.get("row_store"),
        "resident_after_walk": resident.get("resident_after_walk"),
    }


def measure_replay_line(
    arguments: Sequence[str],
    configs: Sequence[str],
    *,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    env: Mapping[str, str] | None = None,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
    run: Callable[..., ReplayRun] = run_replay_child,
) -> dict[str, Any]:
    """Run one replay line's child (`run_replay_child`) between the write meter's readings, as `measure_step` runs a table step, and return its measured fields (the module docstring's "The replay step"). A walk that fails stops the run. `meter`, `machine`, `sleep` and `run` are the write meter, the machine reading, the wait and the child, which a test replaces."""
    idle = None
    if idle_s > 0:
        idle = meter()
        sleep(idle_s)
    start = meter()
    ran = run(arguments, env=env, interval=interval_s, machine=machine)
    end = meter()
    rate = None if idle is None else idle_rate(idle, start)
    replay = ran.replay
    walks: Mapping[str, Mapping[str, Any]] = replay["walks"]
    absorbs: Mapping[str, Mapping[str, Any]] = replay["absorbs"]
    failed = [walk["error"] for walk in walks.values() if walk["error"] is not None]
    if failed or not replay["passed"]:
        raise SystemExit(f"the replay failed: {failed[0] if failed else 'not every walk passed'}")
    crates = crate_configs(ran.commands)
    workers = {walk["worker"]: config for config, walk in walks.items() if walk["worker"]}
    pairs, combined, top = replay_peaks(ran.readings, crates, workers)
    child = ran.child
    machines = {sample.at: sample.machine for sample in child.samples}
    readings = [child.begin, *(sample.machine for sample in child.samples), child.end]
    last = _combined(_pair_sums(ran.readings[-1], crates, workers)) if ran.readings else None
    lines: dict[str, Any] = {}
    for config in configs:
        walk = walks[config]
        absorb = absorbs.get(config, {})
        resident, footprint, cpu = walk["peaks"] or (None, None, None)
        digest = absorb.get("digest") or (None, None)
        lines[config] = {
            "counts": walk["counts"],
            "walk": {
                "peak_resident": resident,
                "peak_footprint": footprint,
                "cpu_s": None if cpu is None else round(cpu, 3),
            },
            "absorb": {
                "peak_resident": absorb.get("resident"),
                "peak_footprint": absorb.get("footprint"),
                "cpu_s": None if absorb.get("cpu") is None else round(absorb["cpu"], 3),
                "seconds": None if absorb.get("seconds") is None else round(absorb["seconds"], 3),
                "rows": absorb.get("entries"),
                "bytes": digest[0],
                "sha256": digest[1],
                "warning": absorb.get("warning"),
            },
            "pair": pairs.get(config),
            "phases": {
                match.group(1): float(match.group(2))
                for match in INNER_LINE.finditer("\n".join(walk["lines"]))
            },
            "cache": replay_cache(walk["lines"], config),
        }
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        swapouts = _difference(child.begin.swapouts, machine().swapouts)
        after = write_fields(start, later, rate, swapouts)
    return {
        "wall_s": round(child.wall, 3),
        "samples": len(ran.readings),
        "interval_s": interval_s,
        "start": _moment(0.0, None, None, child.begin),
        "peak": None if top is None else _moment(top[0], top[1], top[2], machines.get(top[0], child.end)),
        "end": _moment(
            child.wall, None if last is None else last[0], None if last is None else last[1], child.end
        ),
        "compressor_max": _highest(reading.compressor for reading in readings),
        "swap_used_max": _highest(reading.swap_used for reading in readings),
        "combined": combined,
        "configs_measured": lines,
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, child.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
    }


def replay_report(lines: Sequence[Mapping]) -> list[str]:
    """Return the replay series' fit against runes (`fit`), one figure a line: the windows over every configuration, the highest walk peak, absorb peak and pair peak over the configurations (each the higher of its resident and footprint), the step's combined peak, the walks' CPU time over every configuration, the largest row store, and the first configuration's largest unit's walk memo length. A figure a line lacks is left out of its fit."""
    if len({line["runes"] for line in lines}) < 2:
        return [f"the replay fit needs two cuts; this run has {len({line['runes'] for line in lines})}"]
    runes = [line["runes"] for line in lines]

    def most(line: Mapping, part: str) -> int | None:
        values = [
            max(value or 0 for value in (entry[part]["peak_resident"], entry[part]["peak_footprint"]))
            for entry in line["walks"].values()
        ]
        return max(values, default=None) or None

    def pair(line: Mapping) -> int | None:
        return max(
            (max(entry["pair"].values()) for entry in line["walks"].values() if entry["pair"]), default=None
        )

    def unit_memo(line: Mapping) -> int | None:
        unit = next(iter(line["walks"].values()))["cache"]["largest_unit"]
        return None if unit is None else unit["tables"].get("walk_memo", [None])[0]

    figures: dict[str, list[float | None]] = {
        "windows": [sum(entry["counts"]["windows"] for entry in line["walks"].values()) for line in lines],
        "walk peak": [most(line, "walk") for line in lines],
        "absorb peak": [most(line, "absorb") for line in lines],
        "pair peak": [pair(line) for line in lines],
        "combined peak": [max(line["combined"].values()) or None for line in lines],
        "walk cpu": [sum(entry["walk"]["cpu_s"] or 0 for entry in line["walks"].values()) for line in lines],
        "row store bytes": [
            max(
                ((entry["cache"]["row_store"] or {}).get("bytes") or 0 for entry in line["walks"].values()),
                default=0,
            )
            or None
            for line in lines
        ],
        "largest unit walk_memo len": [unit_memo(line) for line in lines],
    }
    width = max(len(label) for label in figures)
    return [
        f"replay fit over {len(lines)} lines (runes {min(runes)}..{max(runes)})",
        *(f"  {label:<{width}} ~ runes^{exponent(fit(runes, values))}" for label, values in figures.items()),
    ]


def run_replay(args: argparse.Namespace, tree: Path) -> int:
    """Run the replay holder's lines (the module docstring's "The replay step") under the held lock: build the kernel, empty the tree, write each distinct cut's spec dump and pickle and build its tables once, unmeasured (`build_replay_tables`), then measure each named line in turn, recording each as it finishes, then the replay fit (`replay_report`). The cuts' tables, about 0.05 GB a cut, are removed as the run ends, however it ends."""
    configs = configs_from(args.configs) if args.configs else list(conform.SETTLEMENT_CONFIGS)
    spec = spec_load.load_default_spec()
    order = scaling_series.series_order(spec)
    sizes = args.sizes or default_sizes(len(order), REPLAY_SIZES)
    width = run_build._replay_threads(args.replay_threads)
    kernel_exec.ensure_built()
    empty_tree(tree)
    tables_env = (
        {**os.environ, **kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT} if sys.platform == "darwin" else None
    )
    env = child_environment()
    try:
        cuts: dict[int, tuple[ResolvedSpec, list[str]]] = {}
        flags = bare_flags(configs)
        for size in dict.fromkeys(sizes):
            cut = scaling_series.sub_spec(spec, order, size)
            runes = len(cut.runes)
            dump = tree / f"spec-r{runes}.json"
            pickled = tree / f"spec-r{runes}.pickle"
            kernel_io.write_spec(cut, dump)
            with pickled.open("wb") as handle:
                pickle.dump(cut, handle, protocol=pickle.HIGHEST_PROTOCOL)
            cut_dir = tree / REPLAY / f"r{runes}"
            _progress(f"r{runes}: building the tables its lines walk, unmeasured")
            build_replay_tables(dump, cut_dir, configs, flags, f"{STAMP_PREFIX}{runes}", env=tables_env)
            cuts[size] = (cut, replay_child_arguments(pickled, dump, cut_dir, configs, width, built=True))
        head = _head()
        lines: list[dict[str, Any]] = []
        record = Record(args.out, REPLAY)
        os.sync()
        time.sleep(SETTLE_S)
        for size in sizes:
            cut, arguments = cuts[size]
            runes = len(cut.runes)
            letters = sum(1 for rune in cut.runes.values() if not rune.sequence)
            _progress(
                f"r{runes}: {args.idle_seconds:g} s idle, the replay, then {args.after_seconds:g} s before the meter's last reading"
            )
            measured = measure_replay_line(
                arguments,
                configs,
                idle_s=args.idle_seconds,
                after_s=args.after_seconds,
                interval_s=args.interval,
                env=env,
            )
            _warn_meter(f"r{runes}", measured["written"])
            walks = measured.pop("configs_measured")
            line: dict[str, Any] = {
                "step": REPLAY_STEP,
                "runes": runes,
                "letters": letters,
                "ligs": runes - letters,
                "configs": list(configs),
                "width": width,
                "max_length": run_build.REPLAY_MAX_LENGTH,
                "modes": " ".join(kernel_exec.settlement_flags()) or "default modes",
                **measured,
                "walks": walks,
                "alphabet": sorted(cut.runes),
                "cpus": memory_budget.usable_cores(),
                "ram": memory_budget.total_memory_bytes(),
                "head": head,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            lines.append(line)
            text = json.dumps(line)
            print(text, flush=True)
            record.add(text)
        for text in ("", *replay_report(lines)):
            print(text, flush=True)
            record.add(text)
        return 0
    finally:
        shutil.rmtree(tree / REPLAY, ignore_errors=True)


def holder_sections(text: str) -> dict[str, list[str]]:
    """Split a record's lines by the holder each belongs to: a JSON line by its `step` (`HOLDER_OF_STEP`), and every other line, the fit, by the JSON line before it."""
    sections: dict[str, list[str]] = {holder: [] for holder in HOLDERS}
    holder = None
    for line in text.splitlines():
        if line.startswith("{"):
            holder = HOLDER_OF_STEP[json.loads(line)["step"]]
        if holder is not None:
            sections[holder].append(line)
    return sections


class Record:
    """The record at `path` as one holder's run rewrites it: the other holder's lines and fit as the file held them, and this run's lines and fit in place of the holder's earlier ones, holders in `HOLDERS` order. Making one drops the holder's earlier lines, and each `add` rewrites the whole file, so a run stopped partway keeps the lines it finished."""

    def __init__(self, path: Path, holder: str) -> None:
        self._path = path
        self._holder = holder
        self._sections = holder_sections(path.read_text(encoding="utf-8") if path.is_file() else "")
        self._sections[holder] = []
        self._write()

    def add(self, *lines: str) -> None:
        self._sections[self._holder].extend(lines)
        self._write()

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            "".join(f"{line}\n" for holder in HOLDERS for line in self._sections[holder]), encoding="utf-8"
        )


def _head() -> str | None:
    done = subprocess.run(
        ["git", "-C", str(ROOT), "describe", "--always", "--dirty", "--abbrev=8", "--exclude=*"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or None


def _warn(text: str) -> None:
    print(f"[warn] {text}", file=sys.stderr, flush=True)


def _progress(text: str) -> None:
    print(f"memory series: {text}", file=sys.stderr, flush=True)


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m engine.tools.memory_series",
        description="Measure what the table build or the string replay holds and writes at each of several alphabet cuts, one build at a time, writing nothing but its record.",
    )
    parser.add_argument(
        "--holder",
        choices=HOLDERS,
        default=TABLES,
        help="the build to measure: the table build (the default) or the string replay with its settle-memo absorbs",
    )
    parser.add_argument(
        "lines",
        nargs="*",
        metavar="k",
        help=f"the rune counts to cut alphabets at; by default {', '.join(map(str, SIZES))} below the whole alphabet and the whole alphabet for the table holder, and {', '.join(map(str, REPLAY_SIZES))} below it and the whole alphabet for the replay holder",
    )
    parser.add_argument(
        "--configs",
        help="the settlement configurations to build or replay, comma-separated; every one by default",
    )
    parser.add_argument(
        "--replay-threads",
        type=int,
        help="how many walks run at once; by default the width run_build derives, `run_build._replay_threads(None)` (replay holder)",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        help="the table build's width; by default the one a bare run_build derives (table holder)",
    )
    parser.add_argument(
        "--scratch-beside-default",
        type=int,
        help="at most how many deltas start from scratch beside default; by default as a bare run_build decides (table holder)",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=IDLE_S,
        help=f"how long the device is read idle before each step, for device_net (default {IDLE_S:g}; 0 reads none)",
    )
    parser.add_argument(
        "--after-seconds",
        type=float,
        default=AFTER_S,
        help=f"how long after each step the meter is read again (default {AFTER_S:g}; 0 reads it only at the end)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=INTERVAL_S,
        help=f"seconds between the sampler's readings of the machine and, for the replay holder, of each walk's crate and absorb worker, whose sums give the pair and combined peaks (default {INTERVAL_S:g})",
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / OUT_PATH, help=f"the record to write (default {OUT_PATH})"
    )
    args = parser.parse_args(argv)
    if any(not text.isdecimal() for text in args.lines):
        parser.error("a line is a rune count")
    args.sizes = [int(text) for text in args.lines]
    if any(size < 1 for size in args.sizes):
        parser.error("a size is a rune count of at least 1")
    other = {
        TABLES: (("--replay-threads", args.replay_threads),),
        REPLAY: (
            ("--kernel-threads", args.kernel_threads),
            ("--scratch-beside-default", args.scratch_beside_default),
        ),
    }[args.holder]
    stated = [flag for flag, value in other if value is not None]
    if stated:
        parser.error(f"{', '.join(stated)} do not apply to the {args.holder} holder")
    for flag, value in (
        ("--kernel-threads", args.kernel_threads),
        ("--replay-threads", args.replay_threads),
    ):
        if value is not None and value < 1:
            parser.error(f"{flag} must be at least 1")
    if args.scratch_beside_default is not None and args.scratch_beside_default < 0:
        parser.error("--scratch-beside-default must be zero or more")
    if args.idle_seconds < 0 or args.after_seconds < 0:
        parser.error("--idle-seconds and --after-seconds must be zero or more")
    if args.interval <= 0:
        parser.error("--interval must be more than zero")
    return args


def _warn_meter(name: str, written: Mapping[str, Any]) -> None:
    for label in ("end", "after"):
        block = written[label]
        if block is None:
            continue
        if block["basis"] != "own":
            _warn(f"{name} {label}: the write meter read the {block['basis']} basis, not own")
        if "warning" in block:
            _warn(f"{name} {label}: {block['warning']}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    if spec_load.RUNES_ENV in os.environ:
        raise SystemExit(
            f"the memory series refuses: {spec_load.RUNES_ENV} is set ({os.environ[spec_load.RUNES_ENV]!r}), "
            f"and the series cuts its own alphabets from the whole store; unset it"
        )
    with artifact_cycle.stop_signals():
        try:
            return run_series(args)
        except artifact_cycle.CycleStopped as stopped:
            return 128 + stopped.signum


def run_series(args: argparse.Namespace) -> int:
    """Hold the series' lock (`store_lock.hold_flock` on `<tree>.lock`), refusing when another run holds it, and run the holder `args` names: the replay holder's lines (`run_replay`), or the table holder's steps and then the whole-series fit (`report`)."""
    configs = configs_from(args.configs) if args.configs else list(conform.SETTLEMENT_CONFIGS)
    tree = ROOT / TREE_PATH
    lock = store_lock.lock_path_for(tree)
    with ExitStack() as held:
        try:
            held.enter_context(store_lock.hold_flock(lock, blocking=False))
        except store_lock.LockBusy:
            raise SystemExit(f"another memory series holds {lock} and is using {tree}") from None
        if args.holder == REPLAY:
            return run_replay(args, tree)
        spec = spec_load.load_default_spec()
        order = scaling_series.series_order(spec)
        sizes = args.sizes or default_sizes(len(order))
        kernel_exec.cargo_build()
        binary = prepare_tree(tree, kernel_exec.BINARY)
        cuts = []
        for size in sizes:
            cut = scaling_series.sub_spec(spec, order, size)
            spec_path = tree / f"spec-r{len(cut.runes)}.json"
            kernel_io.write_spec(cut, spec_path)
            cuts.append((cut, spec_path))
        flags = bare_flags(
            configs,
            threads=args.kernel_threads,
            scratch=args.scratch_beside_default,
        )
        env = {**os.environ, **kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT} if sys.platform == "darwin" else None
        head = _head()
        lines: list[dict[str, object]] = []
        record = Record(args.out, TABLES)
        os.sync()
        time.sleep(SETTLE_S)
        for cut, spec_path in cuts:
            runes = len(cut.runes)
            letters = sum(1 for rune in cut.runes.values() if not rune.sequence)
            _progress(
                f"r{runes}: {args.idle_seconds:g} s idle, the build, then {args.after_seconds:g} s before the meter's last reading"
            )
            measured = measure_step(
                binary,
                spec_path,
                tree / f"r{runes}",
                configs,
                flags,
                f"{STAMP_PREFIX}{runes}",
                idle_s=args.idle_seconds,
                after_s=args.after_seconds,
                interval_s=args.interval,
                env=env,
            )
            _warn_meter(f"r{runes}", measured["written"])
            line: dict[str, object] = {
                "step": TABLES,
                "runes": runes,
                "letters": letters,
                "ligs": runes - letters,
                "configs": list(configs),
                **asdict(flags),
                "modes": " ".join(kernel_exec.mode_flags()) or "default modes",
                **measured,
                "alphabet": sorted(cut.runes),
                "cpus": memory_budget.usable_cores(),
                "ram": memory_budget.total_memory_bytes(),
                "head": head,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            lines.append(line)
            text = json.dumps(line)
            print(text, flush=True)
            record.add(text)
        for text in ("", *report(lines)):
            print(text, flush=True)
            record.add(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
