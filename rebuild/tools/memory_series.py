"""The memory series: measure what the M1 table build holds and writes at each of several alphabet cuts, one heavy process at a time, so a change that claims a term is flat shows it across the cuts instead of at the whole alphabet alone. It has the scaling sweep's shape (`rebuild/tools/scaling_sweep.py`), which times one configuration, and measures the whole build instead.

Each step is one `build-tables` over the configurations named (every settlement configuration unless `--configs` narrows them) on one alphabet cut, at the flags a bare run_m1 derives on this machine (`bare_flags`, as `run_m1.build_tables` derives them with nothing beside the build: the width, whether the memo files are written beside the wave, and how many deltas start from scratch beside `default`), unless `--kernel-threads`, `--overlap-memo-writes` or `--no-overlap-memo-writes`, or `--scratch-beside-default` states one. Every step runs the same flags, so the cuts differ only in their alphabet. The cut is `scaling_series.sub_spec`'s, over `scaling_series.series_order`, the rule the alphabet switch (`spec_load.M1_RUNES_ENV`) narrows by too, so a line's `alphabet` is what that switch takes to build the same cut in a tree of the fault kit's kind. The sizes default to `SIZES` below the whole alphabet and the whole alphabet itself, so the largest line is the live build's; positional arguments name other rune counts, and a count named twice is measured twice.

A step's build writes no file. Each configuration's windows payload goes through a FIFO at its path and is counted as it arrives (`scaling_sweep.scan`, through `kernel_exec.stream_payloads`), and so does each configuration's plain memo file, `memo-<config>.tsv`, which the crate writes under `--memo-stamp` as run_m1's build does: its FIFO is drained through SHA-256 and a byte count, so the line carries what the memo file would have held without the file. The settlement and joins paths are symbolic links to the null device. The crate opens every one of these paths with `File::create`, which opens a FIFO or the null device and truncates neither. The crate's stdout and stderr come back through pipes. Both stamps, `--inputs` and `--memo-stamp`, are `STAMP_PREFIX` and the rune count, so one cut hashes the same from run to run. A build fed previous memos would unpack them to plain files, so no step builds warm.

A line holds, beside the cut (`runes`, `letters`, `ligs`, `configs`, the three flags and `modes`, the settlement modes `kernel_exec.mode_flags` passes):

- `wall_s` and `cpu_s`: the child's wall time and its own user and system time, from the rusage `os.wait4` reaps it with.
- `peak_resident` and `peak_footprint`: the child's resident peak from that rusage and its lifetime peak footprint, read between its exit and its reap as `peak_rss.reap_peaks_bytes` reads it. This module reaps the child itself, because that function discards the rusage the CPU figure comes from.
- `start`, `peak` and `end`: a sampler thread reads the child's resident set and footprint (`peak_rss.resident_bytes`, `peak_rss.footprint_bytes`) and the machine (`read_machine`: swap in use, `peak_rss.swap_used_bytes`; the compressor's occupancy, `peak_rss.parse_compressor_bytes`, and the swapout count, `peak_rss.parse_swapouts`, both over `vm_stat`; and the internal SSD's write counter, as the write meter reads it) every `--interval` seconds. `start` is the machine just before the spawn, `peak` the sample whose footprint was highest, and `end` the machine once the child is reaped beside the last sample's child figures. `compressor_max` and `swap_used_max` are the highest of every reading, and `samples` how many the sampler took.
- `windows`: each configuration's `[windows, rules, cells]`, counted from its payload as `scaling_sweep.counts` counts them. `memo`: each configuration's memo `bytes` and `sha256`. `digests`: each configuration's table digest from the child's stdout.
- `phases`: the child's `[t]` lines, label to seconds. `tables`: the `[c]` lines that give a collection's length and capacity, as `[len, cap, load]` by configuration and collection, the load being length over capacity. `resident`: the `[c]` lines that give a resident size, in bytes. `stats`: every other `[c]` line's fields (`cache_lines` says how a line is keyed).
- `written`: the write meter's fields (`rebuild/tools/write_meter.py`). The meter is read with the device `--idle-seconds` before the step and again just before the step's FIFOs are made, which gives the device's idle rate (`idle_rate`); then once the child is reaped and every FIFO drained (`end`), and again `--after-seconds` after that (`after`), since a write the kernel defers and the swap the pressure leaves reach the device late. Each of the two blocks reads from the reading just before the FIFOs are made: `basis`, `written` (the coalition's logical writes, `WriteDelta.bytes`), `diskio`, `device`, `device_net` (the device's delta less the idle rate over the same seconds), `swap` (the VM volume's writes), `swapouts` (the `vm_stat` swapout delta from `start`), `seconds`, and the meter's `warning` when it has one. `vm.swapusage` lags and does not fall back once the pressure ends, so the swapout delta is the swap reading a step owns. The window from that reading to `end` holds the build, the machine reading before its spawn, and the making and removal of the step's FIFOs, which come to a few kilobytes of directory metadata; it holds neither the step directory's making nor its removal. Before the first step's idle reading the run flushes its own setup's writes (the crate's build, the binary's copy, the spec dumps, the index refresh `git describe` makes and the record's truncation) with `os.sync` and waits `SETTLE_S` for them to reach the device, so the first step's idle rate holds none of them. A basis other than `own` or a warning also prints a `[warn]` line.
- `alphabet` (the cut's rune names), `cpus` and `ram` (`memory_budget.usable_cores` and `total_memory_bytes`, which `doc/fleet.md` matches to a machine; no hostname is recorded), `head` (the commit, with `-dirty` after it when tracked files have changed) and `finished_at`.

The lines' writes read in the own basis only when nothing outside the series shares its resource coalition, which is what `make memory-series` gives it: it runs the series as a one-shot launchd job's own process (`rebuild/tools/job_launch.py`), as `make fault-kit` runs the kit, and follows its log in `tmp/memory-series.log`. Run another way, the series still runs, and each line's basis says what its figure is.

The series works in its own tree, `TREE_PATH` under this checkout's `var/perf/`, never in the live tree: `prepare_tree` empties it and copies the crate's release binary into it once `kernel_exec.cargo_build` has built it, so a crate rebuilt elsewhere during the series changes nothing it measures, and every cut's spec dump (`spec-r<runes>.json`) is written there before the first step, so a rune edited during the series changes nothing either. Each step's directory, `r<runes>`, holds only its FIFOs and links and is removed when the step ends. A run holds `<tree>.lock` beside the tree (`store_lock.hold_flock`) and refuses when another run holds it, since two series would share the tree and each other's memory. It refuses too while the alphabet switch is set, because the series cuts its alphabets from the whole store itself; it never sets the switch.

Each line prints to stdout as its step finishes and goes to `--out`, `OUT_PATH` by default, the checked-in record of the last run, which a run rewrites from its first line. After the last step both get the whole-series fit against runes of the windows, the peak footprint and resident, the CPU time and the first configuration's table lengths (`report`), the lengths rather than the table bytes, because a hash table's bytes move in doublings.

Run as: make memory-series ARGS='[k ...] [--configs a,b] [--kernel-threads N] [--[no-]overlap-memo-writes] [--scratch-beside-default N] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]'.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from rebuild.pipeline import conform, kernel_exec, kernel_io, run_m1, spec_load
from rebuild.review import store_lock
from rebuild.tools import memory_budget, peak_rss, scaling_series, scaling_sweep, write_meter
from rebuild.tools.console import INNER_LINE
from rebuild.tools.kernel_all_configs import configs_from

ROOT = Path(__file__).resolve().parents[2]
TREE_PATH = "var/perf/series"
OUT_PATH = "rebuild/memory-series.txt"
BINARY_NAME = "ams-m1-kernel"
SIZES = (34, 38, 41, 43, 44, 45)
IDLE_S = 60.0
AFTER_S = 300.0
SETTLE_S = 10.0
INTERVAL_S = 0.5
STAMP_PREFIX = "memory-series-r"
VM_STAT = "/usr/bin/vm_stat"
READ_CHUNK_BYTES = 1 << 20
WINDOWS = "windows"
MEMO = "memo"
# A `[c]` line's fields that say which instance of a collection it reports, such as `shared_memo_hits id=0`, rather than a figure.
CACHE_QUALIFIERS = ("id", "release")
_CACHE_LINE = re.compile(r"^\[c\] (\S+) (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Flags:
    """The table build's width, whether it writes its memo files beside the wave, and how many deltas it starts from scratch beside `default`."""

    threads: int
    overlap_memo_writes: bool
    scratch_beside_default: int


@dataclass(frozen=True)
class Machine:
    """One reading of the machine: swap in use, the compressor's occupancy in bytes, the swapout count in pages, and the internal SSD's write counter, each None where it cannot be read."""

    swap_used: int | None
    compressor: int | None
    swapouts: int | None
    device: int | None


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


def default_sizes(count: int) -> list[int]:
    """The rune counts a run cuts at when none is named: `count`, the whole alphabet's size, and each of `SIZES` below it."""
    return sorted({*(size for size in SIZES if size < count), count})


def bare_flags(
    configs: Sequence[str],
    *,
    threads: int | None = None,
    overlap: bool | None = None,
    scratch: int | None = None,
) -> Flags:
    """The flags a bare run_m1 builds `configs` at on this machine, derived as `run_m1.build_tables` derives them with nothing beside the build, each replaced by the one stated. A stated width is capped at the configuration count and the cores, as run_m1 caps it."""
    from_scratch = "default" not in configs
    width = run_m1._table_build_threads(threads, len(configs), from_scratch=from_scratch)
    if overlap is None:
        overlap = kernel_exec.memo_writes_overlap(width, configs=len(configs), from_scratch=from_scratch)
    if scratch is None:
        scratch = kernel_exec.deltas_from_scratch(
            width, configs=len(configs), overlap=overlap, from_scratch=from_scratch
        )
    return Flags(width, overlap, scratch)


def step_arguments(
    binary: Path, spec_path: Path, out_dir: Path, configs: Sequence[str], stamp: str, flags: Flags
) -> list[str]:
    """The `build-tables` command line of one step, with its cache stats and timings on and its memo files stamped `stamp`."""
    arguments = [
        str(binary),
        "build-tables",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--inputs={stamp}",
        f"--threads={flags.threads}",
        *kernel_exec.mode_flags(),
        f"--memo-stamp={stamp}",
        "--cache-stats",
        "--timings",
    ]
    if flags.overlap_memo_writes:
        arguments.append("--overlap-memo-writes")
    if flags.scratch_beside_default:
        arguments.append(f"--scratch-beside-default={flags.scratch_beside_default}")
    return arguments


def route(out_dir: Path, configs: Sequence[str]) -> dict[str, Path]:
    """Make `out_dir`, which must not exist, with each configuration's settlement and joins paths linked to the null device, and return the paths `kernel_exec.stream_payloads` makes FIFOs at: each configuration's windows payload and memo file, keyed `windows <config>` and `memo <config>`."""
    out_dir.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for config in configs:
        settlement, joins, _packed = run_m1.config_table_files(out_dir, config)
        settlement.symlink_to(os.devnull)
        joins.symlink_to(os.devnull)
        paths[f"{WINDOWS} {config}"] = kernel_exec.windows_payload(out_dir, config)
        paths[f"{MEMO} {config}"] = out_dir / f"memo-{config}.tsv"
    return paths


def drain_memo(source: IO[bytes]) -> dict[str, int | str]:
    """Read one memo file's stream to its end and return its byte count and SHA-256."""
    digest = hashlib.sha256()
    count = 0
    while chunk := source.read(READ_CHUNK_BYTES):
        digest.update(chunk)
        count += len(chunk)
    return {"bytes": count, "sha256": digest.hexdigest()}


def read_payload(name: str, source: IO[bytes]) -> tuple[bytes, bytes, int] | dict[str, int | str]:
    """Read the stream `route` named `name`: a windows payload is scanned (`scaling_sweep.scan`) and a memo file drained (`drain_memo`)."""
    return scaling_sweep.scan(source) if name.startswith(f"{WINDOWS} ") else drain_memo(source)


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
    """A daemon thread that samples one child (`Sample`) every `interval` seconds, from `start` until `stop`, which returns the samples."""

    def __init__(self, pid: int, interval: float, machine: Callable[[], Machine], origin: float) -> None:
        self._pid = pid
        self._interval = interval
        self._machine = machine
        self._origin = origin
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
) -> ChildRun:
    """Run one kernel child with its stdout and stderr on pipes drained by threads of their own, sampled (`Sampler`) until it exits, and return what it did (`ChildRun`). Once both pipes close it waits for the exit without reaping (`os.waitid` with `WNOWAIT`), stops the sampler, reads the child's peak footprint, and reaps it with `os.wait4`, whose rusage gives its resident peak and CPU time. The sampler stops before the reap, so it never reads a pid the reap has freed. An interrupt kills and reaps the child before it propagates, so no crate outlives the end of its payloads' reads (`kernel_exec.stream_payloads`)."""
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        list(arguments), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
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


def check_child(child: ChildRun, configs: Sequence[str], arguments: Sequence[str]) -> dict[str, str]:
    """Hold the child to the CLI contract `kernel_exec.build_table_files` holds a build to and return its digests by configuration: exit 2 is a usage error, any other nonzero exit an error about the inputs, stdout one `{config, digest}` line for each configuration in the order named, and stderr on a clean exit only `[t]` and `[c]` lines."""
    if child.returncode == 2:
        raise SystemExit(
            f"the kernel rejected the invocation as a usage error: {child.stderr.strip()} ({' '.join(arguments)})"
        )
    if child.returncode != 0:
        raise SystemExit(f"the kernel exited {child.returncode} on build-tables: {child.stderr.strip()}")
    answers = [json.loads(line) for line in child.stdout.decode().splitlines()]
    if [answer.get("config") for answer in answers] != list(configs):
        raise SystemExit(
            f"build-tables answered for {[answer.get('config') for answer in answers]}, not for {list(configs)}"
        )
    stray = [line for line in child.stderr.splitlines() if line and not line.startswith(("[t] ", "[c] "))]
    if stray:
        raise SystemExit(f"the kernel wrote a line that is neither [t] nor [c] on a clean exit: {stray[0]}")
    return {answer["config"]: answer["digest"] for answer in answers}


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
    """Run one step in `step_dir`, which must not exist and is removed once the meter has read the step's end, so the window from the meter's reading just before the FIFOs are made to its reading at the end holds the FIFOs' making and removal, the machine reading before the spawn and the build, and neither the directory's making nor its removal, and return its measured fields, everything a line holds but the cut, the flags and the provenance (the module docstring lists them). `meter`, `machine` and `sleep` are the write meter, the machine reading and the wait, which a test replaces."""
    arguments = step_arguments(binary, spec_path, step_dir, configs, stamp, flags)
    try:
        paths = route(step_dir, configs)
        idle = None
        if idle_s > 0:
            idle = meter()
            sleep(idle_s)
        start = meter()
        child, payloads = kernel_exec.stream_payloads(
            paths,
            read_payload,
            lambda: run_child(arguments, env=env, interval=interval_s, machine=machine),
        )
        end = meter()
    finally:
        shutil.rmtree(step_dir, ignore_errors=True)
    rate = None if idle is None else idle_rate(idle, start)
    digests = check_child(child, configs, arguments)
    windows: dict[str, list[int]] = {}
    memo: dict[str, object] = {}
    for config in configs:
        scanned = payloads[f"{WINDOWS} {config}"]
        assert isinstance(scanned, tuple)
        windows[config] = list(scaling_sweep.counts(scanned, kernel_exec.windows_payload(step_dir, config)))
        memo[config] = payloads[f"{MEMO} {config}"]
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
        "memo": memo,
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


def prepare_tree(tree: Path, binary: Path) -> Path:
    """Make the series' tree at `tree`, empty it of what an earlier run left, copy `binary` into it, and return the copy, run once with no arguments so its first-exec scan is over before any step's clock starts. A symbolic link at `tree` is refused, since emptying it would empty whatever it names."""
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
    copy = tree / BINARY_NAME
    shutil.copy2(binary, copy)
    subprocess.run([str(copy)], capture_output=True, check=False)
    return copy


def report(lines: Sequence[Mapping]) -> list[str]:
    """Return the whole-series fit against runes (`scaling_sweep.fit`) of the windows over every configuration, the peak footprint and resident, the CPU time, and the length of each collection the first configuration's `tables` name, one figure a line. A figure a line lacks is left out of its fit."""
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
        *(
            f"  {label:<{width}} ~ runes^{scaling_sweep.exponent(scaling_sweep.fit(runes, values))}"
            for label, values in figures.items()
        ),
    ]


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
        prog="python -m rebuild.tools.memory_series",
        description="Measure what the M1 table build holds and writes at each of several alphabet cuts, one build at a time, writing nothing but its record.",
    )
    parser.add_argument(
        "sizes",
        nargs="*",
        type=int,
        help=f"the rune counts to cut alphabets at; by default {', '.join(map(str, SIZES))} below the whole alphabet, and the whole alphabet",
    )
    parser.add_argument(
        "--configs", help="the settlement configurations to build, comma-separated; every one by default"
    )
    parser.add_argument(
        "--kernel-threads", type=int, help="the table build's width; by default the one a bare run_m1 derives"
    )
    parser.add_argument(
        "--overlap-memo-writes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="whether the memo files are written beside the wave; by default as a bare run_m1 decides",
    )
    parser.add_argument(
        "--scratch-beside-default",
        type=int,
        help="at most how many deltas start from scratch beside default; by default as a bare run_m1 decides",
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
        help=f"seconds between the sampler's readings (default {INTERVAL_S:g})",
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / OUT_PATH, help=f"the record to write (default {OUT_PATH})"
    )
    args = parser.parse_args(argv)
    if any(size < 1 for size in args.sizes):
        parser.error("a size is a rune count of at least 1")
    if args.kernel_threads is not None and args.kernel_threads < 1:
        parser.error("--kernel-threads must be at least 1")
    if args.scratch_beside_default is not None and args.scratch_beside_default < 0:
        parser.error("--scratch-beside-default must be zero or more")
    if args.idle_seconds < 0 or args.after_seconds < 0:
        parser.error("--idle-seconds and --after-seconds must be zero or more")
    if args.interval <= 0:
        parser.error("--interval must be more than zero")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    if spec_load.M1_RUNES_ENV in os.environ:
        raise SystemExit(
            f"the memory series refuses: {spec_load.M1_RUNES_ENV} is set ({os.environ[spec_load.M1_RUNES_ENV]!r}), "
            f"and the series cuts its own alphabets from the whole store; unset it"
        )
    configs = configs_from(args.configs) if args.configs else list(conform.SETTLEMENT_CONFIGS)
    tree = ROOT / TREE_PATH
    lock = store_lock.lock_path_for(tree)
    with ExitStack() as held:
        try:
            held.enter_context(store_lock.hold_flock(lock, blocking=False))
        except store_lock.LockBusy:
            raise SystemExit(f"another memory series holds {lock} and is using {tree}") from None
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
            overlap=args.overlap_memo_writes,
            scratch=args.scratch_beside_default,
        )
        env = {**os.environ, **kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT} if sys.platform == "darwin" else None
        head = _head()
        lines: list[dict[str, object]] = []
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as record:
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
                for label in ("end", "after"):
                    block = measured["written"][label]
                    if block is None:
                        continue
                    if block["basis"] != "own":
                        _warn(f"r{runes} {label}: the write meter read the {block['basis']} basis, not own")
                    if "warning" in block:
                        _warn(f"r{runes} {label}: {block['warning']}")
                line: dict[str, object] = {
                    "step": "tables",
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
                record.write(text + "\n")
                record.flush()
            for text in ("", *report(lines)):
                print(text, flush=True)
                record.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
