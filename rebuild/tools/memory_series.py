"""The memory series: measure what the M1 table build and the review corpus build hold and write at each of several alphabet cuts, one heavy process at a time, so a change that claims a term is flat shows it across the cuts instead of at the whole alphabet alone. It has the scaling sweep's shape (`rebuild/tools/scaling_sweep.py`), which times one configuration, and measures the whole build instead. It measures one holder a run, which `--holder` picks: the table build (`tables`, the default), whose step the paragraphs up to "The corpus step" describe, or the corpus build (`corpus`), whose step they then describe.

Each table step is one `build-tables` over the configurations named (every settlement configuration unless `--configs` narrows them) on one alphabet cut, at the flags a bare run_m1 derives on this machine (`bare_flags`, as `run_m1.build_tables` derives them with nothing beside the build: the width and how many deltas start from scratch beside `default`), unless `--kernel-threads` or `--scratch-beside-default` states one. Every step runs the same flags, so the cuts differ only in their alphabet. The cut is `scaling_series.sub_spec`'s, over `scaling_series.series_order`, the rule the alphabet switch (`spec_load.M1_RUNES_ENV`) narrows by too, so a line's `alphabet` is what that switch takes to build the same cut in a tree of the fault kit's kind. The sizes default to `SIZES` below the whole alphabet and the whole alphabet itself, so the largest line is the live build's; positional arguments name other rune counts, and a count named twice is measured twice.

A step's build writes no file. Each configuration's windows payload goes through a FIFO at its path and is counted as it arrives (`scaling_sweep.scan`, through `kernel_exec.stream_payloads`). The settlement and joins paths are symbolic links to the null device. The crate opens every one of these paths with `File::create`, which opens a FIFO or the null device and truncates neither. The crate's stdout and stderr come back through pipes. The `--inputs` stamp is `STAMP_PREFIX` and the rune count, so one cut hashes the same from run to run.

A table line holds, beside `step` (`tables`) and the cut (`runes`, `letters`, `ligs`, `configs`, the two flags and `modes`, the settlement modes `kernel_exec.mode_flags` passes):

- `wall_s` and `cpu_s`: the child's wall time and its own user and system time, from the rusage `os.wait4` reaps it with.
- `peak_resident` and `peak_footprint`: the child's resident peak from that rusage and its lifetime peak footprint, read between its exit and its reap as `peak_rss.reap_peaks_bytes` reads it. This module reaps the child itself, because that function discards the rusage the CPU figure comes from.
- `start`, `peak` and `end`: a sampler thread reads the child's resident set and footprint (`peak_rss.resident_bytes`, `peak_rss.footprint_bytes`) and the machine (`read_machine`: swap in use, `peak_rss.swap_used_bytes`; the compressor's occupancy, `peak_rss.parse_compressor_bytes`, and the swapout count, `peak_rss.parse_swapouts`, both over `vm_stat`; and the internal SSD's write counter, as the write meter reads it) every `--interval` seconds. `start` is the machine just before the spawn, `peak` the sample whose footprint was highest, and `end` the machine once the child is reaped beside the last sample's child figures, whose footprint reads None when that sample caught the child exiting (`peak_rss.footprint_bytes`). `compressor_max` and `swap_used_max` are the highest of every reading, and `samples` how many the sampler took.
- `windows`: each configuration's `[windows, rules, cells]`, counted from its payload as `scaling_sweep.counts` counts them. `digests`: each configuration's table digest from the child's stdout.
- `phases`: the child's `[t]` lines, label to seconds. `tables`: the `[c]` lines that give a collection's length and capacity, as `[len, cap, load]` by configuration and collection, the load being length over capacity. `resident`: the `[c]` lines that give a resident size, in bytes. `stats`: every other `[c]` line's fields (`cache_lines` says how a line is keyed).
- `written`: the write meter's fields (`rebuild/tools/write_meter.py`). The meter is read with the device `--idle-seconds` before the step and again just before the step's FIFOs are made, which gives the device's idle rate (`idle_rate`); then once the child is reaped and every FIFO drained (`end`), and again `--after-seconds` after that (`after`), since a write the kernel defers and the swap the pressure leaves reach the device late. Each of the two blocks reads from the reading just before the FIFOs are made: `basis`, `written` (the coalition's logical writes, `WriteDelta.bytes`), `diskio`, `device`, `device_net` (the device's delta less the idle rate over the same seconds), `swap` (the VM volume's writes), `swapouts` (the `vm_stat` swapout delta from `start`), `seconds`, and the meter's `warning` when it has one. `vm.swapusage` lags and does not fall back once the pressure ends, so the swapout delta is the swap reading a step owns. The window from that reading to `end` holds the build, the machine reading before its spawn, and the making and removal of the step's FIFOs, which come to a few kilobytes of directory metadata; it holds neither the step directory's making nor its removal. Before the first step's idle reading the run flushes its own setup's writes (the crate's build, the binary's copy, the spec dumps, the index refresh `git describe` makes and the record's truncation) with `os.sync` and waits `SETTLE_S` for them to reach the device, so the first step's idle rate holds none of them. A basis other than `own` or a warning also prints a `[warn]` line.
- `alphabet` (the cut's rune names), `cpus` and `ram` (`memory_budget.usable_cores` and `total_memory_bytes`, which `doc/fleet.md` matches to a machine; no hostname is recorded), `head` (the commit, with `-dirty` after it when tracked files have changed) and `finished_at`.

The lines' writes read in the own basis only when nothing outside the series shares its resource coalition, which is what `make memory-series` gives it: it runs the series as a one-shot launchd job's own process (`rebuild/tools/job_launch.py`), as `make fault-kit` runs the kit, and follows its log in `tmp/memory-series.log`. Run another way, the series still runs, and each line's basis says what its figure is.

The series works in its own tree, `TREE_PATH` under this checkout's `var/perf/`, never in the live tree, and empties it as a run starts (`empty_tree`). For the table holder `prepare_tree` then copies the crate's release binary into it once `kernel_exec.cargo_build` has built it, so a crate rebuilt elsewhere during the series changes nothing it measures, and every cut's spec dump (`spec-r<runes>.json`) is written there before the first step, so a rune edited during the series changes nothing either. Each table step's directory, `r<runes>`, holds only its FIFOs and links and is removed when the step ends. A run holds `<tree>.lock` beside the tree (`store_lock.hold_flock`) and refuses when another run holds it, since two series would share the tree and each other's memory. It refuses too while the alphabet switch is set, because the series cuts its alphabets from the whole store itself; it never sets the switch. SIGINT, SIGTERM and SIGHUP stop a run as they stop a cycle pass (`artifact_cycle.stop_signals`), which matters because `job_launch` forwards each to the series alone: the running step's child is killed (a corpus line's with its whole process group), the step's directory and the corpus step's clones are removed, the lock is released, and the run exits with 128 plus the signal's number.

Each line prints to stdout as its step finishes and goes to `--out`, `OUT_PATH` by default, the checked-in record of each holder's last run. A run replaces only its own holder's lines and fit there and keeps every other holder's as the file held them, holders in `HOLDERS` order (`Record`), rewriting the file as each line finishes. After a table run's last step both get the whole-series fit against runes of the windows, the peak footprint and resident, the CPU time and the first configuration's table lengths (`report`), the lengths rather than the table bytes, because a hash table's bytes move in doublings.

The corpus step builds the review corpus with `build.build_m1`, called as a library in a child process, once a line: `rebuild/tools/memory_series_child.py`, run as its own module, so the build's parent and its pool workers import the build's modules and not this module's (that module's docstring says why). A cut line, `r<runes>`, builds over the live divergence audit cut to `scaling_series.sub_spec`'s alphabet at that rune count: a row is kept when every Quikscript code point of its window is one of the cut's letters (`cut_pattern`), which gives the audit run_m1 writes at that alphabet byte for byte. The cuts default to `CORPUS_SIZES` below the whole alphabet and the whole alphabet. Every cut line is a full build, each unit recomputed, since its out dir holds no unit store. A load line, `load-x1` or `load-x2`, runs the child over the whole alphabet on the live audit (×1) or on the live audit followed by its moved copy (×2, `moved_row`: each Quikscript code point moved up by `MOVED` and both rendered-name fields tagged), and the child's mark reporter stops the build at the plan's mark (`PLAN_MARK`), before any worker or output exists. Both load lines extend `build.LETTERS` to the moved code points, copy each family's content key to its tagged name, and skip the signature resolution and the ink-duplicate merge, which a moved window has no signature for, so the two compare like with like. Positional arguments name the lines (`30`, `load-x1`); with none, a corpus run measures every cut and both load lines. The widths are the build's own defaults on this machine (`corpus_widths`) at every line, unless `--jobs`, `--signature-jobs` or `--write-jobs` states one.

The corpus step reads the live tree only through clones it makes as a run starts (`clone_inputs`, `clonefile(2)` through `build._clone`, which writes no data) under `<tree>/corpus/inputs/`: the audit, the subset tables and their pack, the M1 font and its Stage A record, the live corpus's manifest and ink-signature store, the spec root's paths (`SPEC_PATHS`, the ledger among them) and the two site fonts. A clone the volume refuses stops the run, since a copy would write gigabytes. A run refuses while the debug tally's switch (`memory_tally.TALLY_ENV`) is set, since the tally keeps alive the collections it reads, and `check_inputs` refuses when the live corpus was not built over the M1 outputs' Stage A record, since its signature store would then miss and the load would shape, or when the pack is not current for the tables, since the build would rewrite it. The child runs from this checkout, so its units workers run the checkout's code and crate binary, and each line reads their digests (`corpus_code`) just before the spawn, once its idle wait is over, and again as soon as the child is reaped, before the wait for the meter's last reading, so the two readings bracket the build and none of the line's waits; a digest that moves between them, or from the first line's to a later line's first reading, prints a `[warn]` line naming the line. An edit made during a line's waits shows at the next line, the first whose build runs it. As the run ends, however it ends, it removes `<tree>/corpus/`, the clones with it, since once the live tree's next pass replaces those files the clones alone would keep their blocks allocated.

Each corpus line's directory, `<tree>/corpus/<line>`, holds the audit's FIFO, which a harness thread fills from the cloned audit (`feed_audit`), the build's out dir, `out/`, the temporary directory the child's `TMPDIR` names, `temp/`, and `pools.ndjson`, where the child points `cycle_timings.JOURNAL`, so the build's pool records land there and not in the live journal. The audit comes through a FIFO because `audit._stage_audit` reads it once, front to back; a build that reads it by `pread` from one descriptor, as #618's change 15 has it, needs each cut's audit as a real, seekable file instead. A cut line's out dir holds a clone of the live signature store, which the build's load reads, and a link to the null device at the staging name of every output the build writes and never reads back (`null_routes`): each shard part a class can take, the units index, the three app sidecars, the unit store and the signature store's rewrite. The build writes for real only what it reads back or what is too small to route: the manifest, the fonts, the static files and `review-facts.json`. Beside them, each process that settles windows, every units worker, writes one dump of the whole alphabet's spec into `temp/` (`kernel_exec._spec_dump`; every line settles over the whole spec, so the dump is the same at every cut) and removes it as it exits, and runs `kernel_exec.ensure_built`'s `cargo build`, which rewrites the crate's dep-info files under `rebuild/kernel-rs/target/`, outside the line's directory, even when it builds nothing. Those writes are part of how far a cut line's `written` exceeds its `files.kept`, not all of it: `written` counts every byte the coalition writes over the line's window, and `files` only the files the line leaves, so a file written outside the line's directory, or written and removed during the build, counts in the first alone. Once the child is reaped the step lists every regular file the line left (`step_files`); any other is a stray, which the line records under `files` and which stops the run once the line is in the record. The directory is removed when the line ends.

A corpus line holds, beside `step` (`corpus` or `corpus-load` with its `scale`), the cut (`runes`, `letters`, `ligs`, `alphabet`), the widths the child was given (`jobs`, `signature_jobs`, `write_jobs`) and those that ran (`units_width` and `write_width`, from the phase notes, and `handout`, `_handout_width`'s batch):

- `rows`, the audit rows the harness fed, and `feed_s`, the seconds after the spawn at which it closed the FIFO. The harness scans the whole cloned audit at every cut, so a load whose `stage_audit returned` mark arrives soon after `feed_s` waited on that scan, and a small cut's load seconds include it. `units_premerge` and `units`, the units the build loaded and those left after the ink-duplicate merge; `windows`, the distinct windows; `human_units`, `recomputed`, `classes` and `write_runs`, each write run's classes and units as `build._write_runs` cuts them from the line's manifest at the width the write ran at.
- `wall_s`, `peak_footprint`, `start`, `peak`, `end`, `compressor_max`, `swap_used_max`, `samples` and `interval_s`, as a table line holds them, of the build's parent. `cpu_s` and `peak_resident` are the parent's own rusage, which the child prints as it exits (`[r]`), since the rusage `os.wait4` returns adds the reaped workers'; `children` holds their CPU and largest resident peak from the parent's `RUSAGE_CHILDREN`.
- `marks`: one entry a sub-step of the parent, from each `memory_tally.mark` the build makes (`CORPUS_MARKS` lists them in order), with the parent's resident and footprint at the mark, the highest of each a sampler of the parent alone read since the previous mark (every `parent_interval_s`, `PARENT_INTERVAL_S`, `parent_samples` times), and the mark's counts. The labels follow those of #618's corpus load probe, so a load line compares with its `load-x1.json` and `load-x2.json`.
- `phases`: each `[t] review.build <phase>` line's seconds, `rss_gb`, `rss_now_gb` and note. `signatures_shaped` is the load note's shaped count, which is 0 when the cloned store answers every window, and a cut line that shaped prints a `[warn]` line.
- `pools`: for the signature, units and write pools, the width and each worker's peak from the build's pool records (`reading` names whether they are footprints), the parent's as the controller, and each worker's resident peak as the sampler read it every `--interval` seconds (`resident`), with the median and top of each. The sampler reads every process below the parent. A child of the parent that runs multiprocessing's spawn entry is a pool's worker, of the pool between whose closing mark and the previous pool's it first appears (`POOL_MARKS`); every other process but multiprocessing's resource tracker, the git and crate calls the parent and its workers make, is summed under `subprocesses`.
- `fragments`: the units phase note's recomputed units and the arena's size and ratio; `sidecar_lines_mb`, each write run's compressed sidecar lines, from the write phase's note.
- `written`, as a table line holds it, and `files`: the regular files the line left, with their sizes, under `kept`, and any other under `stray`.
- `inputs` (the Stage A record and the live corpus's `generated_at`), `allocator_env` (the child's `Malloc*` variables), `code` (the digests just before the spawn) and, when they moved during the build, `code_after` (once the child is reaped), `cpus`, `ram`, `head` and `finished_at`.

After a corpus run's last line the record and stdout get the fit of each corpus figure against units, and of the parent's footprint at the load's and the plan's marks against rows, over the cut lines, and the ×2 load line's ratio to the ×1 line at each mark (`corpus_report`).

Run as: make memory-series ARGS='[--holder tables] [k ...] [--configs a,b] [--kernel-threads N] [--scratch-beside-default N] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]', or ARGS='--holder corpus [k ...] [load-x1] [load-x2] [--jobs N] [--signature-jobs N] [--write-jobs N] [--idle-seconds S] [--after-seconds S] [--interval S] [--out FILE]'.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import functools
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from array import array
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, BinaryIO

from rebuild.pipeline import conform, kernel_exec, kernel_io, run_m1, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import store_lock
from rebuild.tools import (
    artifact_cycle,
    memory_budget,
    memory_series_child,
    memory_tally,
    peak_rss,
    scaling_series,
    scaling_sweep,
    write_meter,
)
from rebuild.tools.console import INNER_LINE, WARN
from rebuild.tools.kernel_all_configs import configs_from
from rebuild.tools.memory_series_child import MOVED, PLAN_MARK, RUSAGE, CorpusInputs

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
# A `[c]` line's fields that say which instance of a collection it reports, such as `shared_memo_hits id=0`, rather than a figure.
CACHE_QUALIFIERS = ("id", "release")
_CACHE_LINE = re.compile(r"^\[c\] (\S+) (.+)$", re.MULTILINE)

TABLES = "tables"
CORPUS = "corpus"
HOLDERS = (TABLES, CORPUS)
CORPUS_STEP = "corpus"
LOAD_STEP = "corpus-load"
HOLDER_OF_STEP = {TABLES: TABLES, CORPUS_STEP: CORPUS, LOAD_STEP: CORPUS}
CORPUS_SIZES = (30, 34, 38, 41, 43, 44)
LOAD_SCALES = (1, 2)
LOAD_PREFIX = "load-x"
PARENT_INTERVAL_S = 0.1
LOAD_MARK = "compacted"
CORPUS_MARKS = (
    "pack",
    "stage_audit returned",
    "triage permutation built",
    "load_workload done",
    "spec loaded",
    "family keys",
    "signatures resolved",
    "premerge captured",
    "merged",
    LOAD_MARK,
    "unit store allocated",
    "input keys written",
    "rows released",
    "named built",
    "named freed",
    PLAN_MARK,
    "units pool started",
    "units drafted",
    "names released",
    "id index built",
    "triage sorted",
    "duplicate groups",
    "by_codepoints built",
    "by_codepoints freed",
    "verified",
    "units pool stopped",
    "write pool started",
    "manifest written",
    "sidecars written",
    "write pool stopped",
    "review facts written",
    "unit store written",
)
# Each pool's name in a line, its unit in the build's pool records, and the mark by which every one of its workers has started. A pool's workers start after the previous pool's mark, since the build runs its pools one after another.
POOL_MARKS = (
    ("signature", "signature", "signatures resolved"),
    ("units", "corpus", "units pool stopped"),
    ("write", "corpus-write", "write pool stopped"),
)
# The paths `enrich.load_spec` and `fingerprint.stage_b` read under a spec root, the ledger among them, as the mini bundle's pin lays them out.
SPEC_PATHS = ("glyph_data/runes", "rebuild/script.yaml", "rebuild/schema", "rebuild/m1-divergences.yaml")
AUDIT_NAME = "divergence-audit.tsv"
OUT_NAME = "out"
POOLS_NAME = "pools.ndjson"
TMP_NAME = "temp"
FIFO_POLL_S = 0.05
CHILD_PIDS_MAX = 4096
WORKER = "multiprocessing.spawn import spawn_main"
TRACKER = "multiprocessing.resource_tracker"
_MARK_LINE = re.compile(r"^\[m\] (.+?)\t(.*)$")
_SIGNATURE_NOTE = re.compile(r"signatures: ([\d,]+) cached, ([\d,]+) shaped")
_JOBS_NOTE = re.compile(r"\bjobs=(\d+)")
_RECOMPUTED_NOTE = re.compile(r"\brecomputed=([\d,]+)")
_ARENA_NOTE = re.compile(r"arena ([\d.]+) GB, deflated ([\d.]+) to 1")
_SIDECAR_NOTE = re.compile(r"sidecar lines ([\d., ]+) MB by run")


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
    """The rune counts a run cuts at when none is named: `count`, the whole alphabet's size, and each of `sizes` below it, `SIZES` for the table holder and `CORPUS_SIZES` for the corpus holder."""
    return sorted({*(size for size in sizes if size < count), count})


def bare_flags(
    configs: Sequence[str],
    *,
    threads: int | None = None,
    scratch: int | None = None,
) -> Flags:
    """The flags a bare run_m1 builds `configs` at on this machine, derived as `run_m1.build_tables` derives them with nothing beside the build, each replaced by the one stated. A stated width is capped at the configuration count and the cores, as run_m1 caps it."""
    from_scratch = "default" not in configs
    width = run_m1._table_build_threads(threads, len(configs), from_scratch=from_scratch)
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


def route(out_dir: Path, configs: Sequence[str]) -> dict[str, Path]:
    """Make `out_dir`, which must not exist, with each configuration's settlement and joins paths linked to the null device, and return the paths `kernel_exec.stream_payloads` makes FIFOs at: each configuration's windows payload, keyed `windows <config>`."""
    out_dir.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for config in configs:
        settlement, joins, _packed = run_m1.config_table_files(out_dir, config)
        settlement.symlink_to(os.devnull)
        joins.symlink_to(os.devnull)
        paths[f"{WINDOWS} {config}"] = kernel_exec.windows_payload(out_dir, config)
    return paths


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
    """A daemon thread that samples one child (`Sample`) every `interval` seconds, from `start` until `stop`, which returns the samples. `tick`, when given, is called after each sample with its time, which the corpus step reads the child's descendants on."""

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
            lambda _name, source: scaling_sweep.scan(source),
            lambda: run_child(arguments, env=env, interval=interval_s, machine=machine),
        )
        end = meter()
    finally:
        shutil.rmtree(step_dir, ignore_errors=True)
    rate = None if idle is None else idle_rate(idle, start)
    digests = check_child(child, configs, arguments)
    windows: dict[str, list[int]] = {}
    for config in configs:
        scanned = payloads[f"{WINDOWS} {config}"]
        assert isinstance(scanned, tuple)
        windows[config] = list(scaling_sweep.counts(scanned, kernel_exec.windows_payload(step_dir, config)))
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


@dataclass(frozen=True)
class CorpusCut:
    """One corpus line's alphabet: its name (`r<runes>` or `load-x<scale>`), its runes, how many of them are letters, the code points of the whole alphabet's letters it leaves out, which the audit's cut drops (`cut_pattern`), and for a load line its scale."""

    name: str
    runes: tuple[str, ...]
    letters: int
    excluded: frozenset[int]
    scale: int = 1
    load: bool = False


@dataclass(frozen=True)
class Feed:
    """What the harness fed the audit's FIFO: the rows it wrote, why it stopped early when it did, and the seconds after the child's spawn at which it closed the FIFO."""

    rows: int
    error: str | None = None
    closed_s: float | None = None


@dataclass
class Descendant:
    """One process below the build's parent, as the sampler read it: its pid, its depth (1 for the parent's own child), when it first appeared, its command when it is the parent's child, and the highest resident set and footprint read of it. A pool's worker is the parent's child that runs multiprocessing's spawn entry (`WORKER`); the parent's other children are its resource tracker (`TRACKER`) and the git and crate calls it makes."""

    pid: int
    depth: int
    first_at: float
    command: str
    resident: int | None = None
    footprint: int | None = None

    def read(self, resident: int | None, footprint: int | None) -> None:
        self.resident = _highest((self.resident, resident))
        self.footprint = _highest((self.footprint, footprint))

    @property
    def worker(self) -> bool:
        return self.depth == 1 and WORKER in self.command

    @property
    def tracker(self) -> bool:
        return self.depth == 1 and TRACKER in self.command


@dataclass(frozen=True)
class CorpusRun:
    """What `run_corpus_child` returns: the child as `run_child` returns one, its stderr lines with the seconds after the spawn each arrived at, the 0.1 s trace of its resident set and footprint, every process below it, and the harness's feed."""

    child: ChildRun
    lines: tuple[tuple[float, str], ...]
    trace: tuple[Sample, ...]
    descendants: tuple[Descendant, ...]
    feed: Feed


def _clone_input(source: Path, target: Path) -> None:
    from rebuild.review import build

    if not source.exists():
        raise SystemExit(f"the corpus step reads {source}, which does not exist; build the live corpus first")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not build._clone(source, target):
        raise SystemExit(
            f"the volume will not clone {source} to {target}, and a copy would write it, so the corpus step stops"
        )


def clone_inputs(root: Path, base: Path) -> CorpusInputs:
    """Clone every input a corpus line's build reads from the checkout at `root` into `base` (`CorpusInputs.under`) and return where they are. Each clone is `build._clone`'s, which writes no data and refuses where the volume cannot clone."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import build, subset_pack, unit_cache

    m1 = root / build.M1_SUBSETS.relative_to(build.REPO_ROOT)
    review = root / build.DEFAULT_OUT.relative_to(build.REPO_ROOT)
    inputs = CorpusInputs.under(base)
    pairs = [
        *((table, inputs.m1 / table.name) for table in sorted(m1.glob("baseline-*.subset.tsv.gz"))),
        (m1 / build.M1_AUDIT.name, inputs.audit),
        (m1 / subset_pack.PACK_NAME, inputs.subset_pack),
        (m1 / fingerprint.STAGE_A_FILENAME, inputs.m1 / fingerprint.STAGE_A_FILENAME),
        (m1 / build.M1_AFTER_FONT.name, inputs.after_font),
        (review / "manifest.json", inputs.manifest),
        (unit_cache.signature_store_path(review), inputs.signatures),
        *((root / relative, inputs.spec_root / relative) for relative in SPEC_PATHS),
        (root / build.SITE_BEFORE_FONT.relative_to(build.REPO_ROOT), inputs.before_font),
        (root / build.SITE_JUNIOR_FONT.relative_to(build.REPO_ROOT), inputs.junior_font),
    ]
    for source, target in pairs:
        _clone_input(source, target)
    return inputs


def check_inputs(inputs: CorpusInputs) -> dict[str, object]:
    """Refuse inputs a corpus line could not build over without shaping or writing: a live corpus whose manifest's Stage A values are not the M1 outputs' record beside the tables, since its signature store would then miss, and a pack that is not current for the tables, since the build would rewrite it. Return the inputs' identity a line records: that Stage A record and the live corpus's `generated_at`."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import audit, subset_pack

    manifest = json.loads(inputs.manifest.read_text(encoding="utf-8"))
    recorded = manifest.get("inputs_fingerprint") or {}
    stage_a = fingerprint.read_stage_a(inputs.m1)
    if stage_a is None or any(recorded.get(key) != stage_a[key] for key in fingerprint.STAGE_A_COMPONENTS):
        raise SystemExit(
            f"the corpus step refuses: the live corpus's manifest ({inputs.manifest}) was not built over the M1 outputs' "
            f"Stage A record ({inputs.m1 / fingerprint.STAGE_A_FILENAME}), so its signature store would miss; run a cycle pass first"
        )
    digests = subset_pack.table_digests(inputs.m1, audit.ACCEPTANCE_CONFIGS)
    try:
        header, _base = subset_pack._read_header(inputs.subset_pack)
    except OSError, ValueError:
        header = None
    if (
        header is None
        or header.get("configs") != list(audit.ACCEPTANCE_CONFIGS)
        or subset_pack._header_disagreement(header, digests) is not None
    ):
        raise SystemExit(
            f"the corpus step refuses: {inputs.subset_pack} is not current for the subset tables beside it, so the build "
            "would rewrite it; run a cycle pass first"
        )
    return {
        "stage_a": {key: stage_a[key] for key in fingerprint.STAGE_A_COMPONENTS},
        "corpus_generated_at": manifest.get("generated_at"),
    }


def corpus_cuts(spec: ResolvedSpec, sizes: Sequence[int], scales: Sequence[int]) -> list[CorpusCut]:
    """The corpus lines to measure, in order: a cut line at each of `sizes` (`scaling_series.sub_spec` over `scaling_series.series_order`), then a load line at each of `scales` over the whole alphabet."""
    order = scaling_series.series_order(spec)
    every = {rune.codepoint for rune in spec.runes.values() if rune.codepoint is not None}
    cuts: list[CorpusCut] = []
    for size in sizes:
        cut = scaling_series.sub_spec(spec, order, size)
        kept = {rune.codepoint for rune in cut.runes.values() if rune.codepoint is not None}
        cuts.append(
            CorpusCut(f"r{len(cut.runes)}", tuple(sorted(cut.runes)), len(kept), frozenset(every - kept))
        )
    whole = tuple(sorted(spec.runes))
    for scale in scales:
        cuts.append(CorpusCut(f"{LOAD_PREFIX}{scale}", whole, len(every), frozenset(), scale, True))
    return cuts


def corpus_widths(
    jobs: int | None = None, signature_jobs: int | None = None, write_jobs: int | None = None
) -> dict[str, int]:
    """The widths every corpus line runs at: the build's own command-line defaults on this machine (`artifact_cycle.corpus_job_budget(skip_gates=True)`, `signature_job_budget()`, and `corpus_write_job_budget` of the units width, as `build.main` and `build_m1` take them), each replaced by the one stated."""
    units = jobs or artifact_cycle.corpus_job_budget(skip_gates=True)
    return {
        "jobs": max(1, units),
        "signature_jobs": max(1, signature_jobs or artifact_cycle.signature_job_budget()),
        "write_jobs": max(1, write_jobs or artifact_cycle.corpus_write_job_budget(units)),
    }


def corpus_environment(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The child's environment: this process's, without the debug tally's switch, which a run refuses anyway, and the cycle's run id, neither of which a measured build should see."""
    from rebuild.tools import cycle_timings

    return {
        key: value
        for key, value in environ.items()
        if key not in (memory_tally.TALLY_ENV, cycle_timings.CYCLE_RUN_ENV)
    }


def corpus_code(checkout: Path) -> dict[str, str]:
    """The digests of what a corpus line's units workers run from the checkout at `checkout`: the unit store's code stamp over `unit_cache.corpus_code_paths`, and the crate binary the enricher calls."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import unit_cache

    return {
        "corpus_code": fingerprint.digest_lines(
            fingerprint.path_lines(checkout, unit_cache.corpus_code_paths(checkout))
        ),
        "kernel": fingerprint.file_sha256(kernel_exec.BINARY),
    }


def null_routes(manifest: Mapping, class_ids: Iterable[str]) -> list[str]:
    """The paths under a cut line's out dir to link to the null device: the staging name of every output the build writes and never reads back. Each class the live manifest lists gets a shard part's staging name for its live part count plus one, since a cut's class holds a subset of the live class's units, and every other id in `class_ids` (the ledger's and `audit.RESERVED_CLASS_IDS`) gets one; then the units index, the three app sidecars, the unit store and the signature store, each under its writer's `.partial` name."""
    from rebuild.review import app_index, unit_cache, unit_index

    parts = {meta["id"]: len(meta["shards"]) for meta in manifest["classes"]}
    for class_id in class_ids:
        parts.setdefault(class_id, 0)
    routes = [
        f"units/{class_id}.{index:03d}.json.partial"
        for class_id, count in sorted(parts.items())
        for index in range(count + 1)
    ]
    sidecars = (
        unit_index.INDEX_NAME,
        app_index.APP_INDEX_NAME,
        app_index.LOCATOR_NAME,
        app_index.LOCATOR_ROWS_NAME,
        unit_cache.STORE_NAME,
        unit_cache.SIGNATURE_STORE_NAME,
    )
    return routes + [f"{name}.partial" for name in sidecars]


def expected_files(load: bool) -> frozenset[str]:
    """The regular files a line may leave in its directory: the build's pool records, and on a cut line the outputs the build writes for real (the manifest, both fonts, `review-facts.json` and the static app files)."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import build, facts

    if load:
        return frozenset({POOLS_NAME})
    static = [
        path.relative_to(build.STATIC_DIR).as_posix() for path in fingerprint.static_files(build.STATIC_DIR)
    ]
    names = (
        "manifest.json",
        "fonts/before.otf",
        "fonts/after.otf",
        facts.FACTS_FILENAME,
        "index.html",
        *static,
    )
    return frozenset({POOLS_NAME, *(f"{OUT_NAME}/{name}" for name in names)})


def step_files(step_dir: Path, expected: frozenset[str]) -> dict[str, dict[str, int]]:
    """Every regular file under `step_dir` with its size, by its path under it: under `kept` when `expected` names it, and under `stray` otherwise. Links and the FIFO are not regular files."""
    found: dict[str, dict[str, int]] = {"kept": {}, "stray": {}}
    for path in sorted(step_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        name = path.relative_to(step_dir).as_posix()
        found["kept" if name in expected else "stray"][name] = path.stat().st_size
    return found


def route_corpus(step_dir: Path, signatures: Path, routes: Sequence[str] | None) -> Path:
    """Make `step_dir`, which must not exist, with the audit's FIFO, the build's out dir and the temporary directory its `TMPDIR` names (`TMP_NAME`), and return the FIFO. With `routes`, for a cut line, the out dir also holds a clone of the live signature store `signatures`, which the build's load reads, and a link to the null device at each of `routes`. A load line's out dir stays empty, since its build stops before it writes anything."""
    step_dir.mkdir(parents=True)
    fifo = step_dir / AUDIT_NAME
    os.mkfifo(fifo)
    out_dir = step_dir / OUT_NAME
    out_dir.mkdir()
    (step_dir / TMP_NAME).mkdir()
    if routes is not None:
        _clone_input(signatures, out_dir / signatures.name)
        for relative in routes:
            link = out_dir / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.devnull)
    return fifo


def cut_pattern(excluded: Iterable[int]) -> re.Pattern[bytes] | None:
    """Return the pattern that matches each whole audit line, newline included, whose window holds none of `excluded`'s code points, or None when nothing is excluded and every line is kept. The window is the line's second field, four or more uppercase hex digits a code point joined by colons (`audit.format_codepoints`), so a code point is matched as a whole token of it."""
    tokens = sorted(f"{value:04X}".encode() for value in excluded)
    if not tokens:
        return None
    return re.compile(
        rb"^(?![^\t\n]*\t[^\t\n]*?(?<![0-9A-F])(?:" + b"|".join(tokens) + rb")(?![0-9A-F]))[^\n]*\n",
        re.MULTILINE,
    )


def moved_row(line: bytes) -> bytes:
    """Return an audit line's copy for the ×2 load line: each Quikscript code point of its window moved up by `MOVED` and both rendered-name fields tagged `.x`, so the copy is a new window with new names under the same configuration, kinds and class, as #618's corpus load probe made it at `--scale 2`."""
    config, codepoints, kinds, entry, baseline, new = line.rstrip(b"\n").split(b"\t")
    moved = b":".join(
        b"%04X" % (int(part, 16) + MOVED) if part.startswith(b"E6") else part
        for part in codepoints.split(b":")
    )
    return b"\t".join((config, moved, kinds, entry, baseline + b".x", new + b".x")) + b"\n"


def feed_audit(source: Path, sink: BinaryIO, pattern: re.Pattern[bytes] | None, scale: int) -> int:
    """Write `source`'s header and each line `pattern` keeps (every line when it is None) to `sink`, then at a `scale` of 2 each kept line's moved copy (`moved_row`), and return how many rows were written. The file is read `READ_CHUNK_BYTES` at a time and each chunk is cut at its last newline, so the pattern runs over whole lines."""
    rows = 0
    for copy in range(scale):
        with source.open("rb") as reader:
            header = reader.readline()
            if not copy:
                sink.write(header)
            remainder = b""
            while True:
                chunk = reader.read(READ_CHUNK_BYTES)
                data = remainder + chunk
                if not chunk:
                    block, remainder = (data + b"\n" if data else data), b""
                else:
                    cut = data.rfind(b"\n") + 1
                    block, remainder = data[:cut], data[cut:]
                kept = block if pattern is None else b"".join(pattern.findall(block))
                rows += kept.count(b"\n")
                if copy:
                    kept = b"".join(map(moved_row, kept.splitlines(keepends=True)))
                sink.write(kept)
                if not chunk:
                    break
    return rows


def _running(pid: int) -> bool:
    return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None


def _open_fifo(fifo: Path, alive: Callable[[], bool]) -> int | None:
    """Open `fifo` for writing once its reader has opened it, or return None once `alive` says the reader exited first. A nonblocking open fails with ENXIO until a reader exists, so the writer never blocks on a reader that will not come; the descriptor blocks once it is open."""
    while True:
        try:
            descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
            if not alive():
                return None
            time.sleep(FIFO_POLL_S)
            continue
        os.set_blocking(descriptor, True)
        return descriptor


def _feed(
    fifo: Path,
    source: Path,
    pattern: re.Pattern[bytes] | None,
    scale: int,
    alive: Callable[[], bool],
    fed: list[Feed],
    origin: float,
) -> None:
    try:
        descriptor = _open_fifo(fifo, alive)
        if descriptor is None:
            fed.append(Feed(0, "the build exited before it opened the audit"))
            return
        with os.fdopen(descriptor, "wb", buffering=READ_CHUNK_BYTES) as sink:
            rows = feed_audit(source, sink, pattern, scale)
        fed.append(Feed(rows, closed_s=time.perf_counter() - origin))
    except OSError as error:
        fed.append(Feed(0, f"{type(error).__name__}: {error}"))


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


class _Descendants:
    """The processes below one child, as the sampler's `tick` reads them: each one's highest resident set and footprint, and the command of each of the child's own children, so a pool's worker can be told from the child's other children. A child's command is read again at each scan until it names multiprocessing, since a process caught between its fork and its exec still shows its parent's."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self.seen: dict[int, Descendant] = {}

    def scan(self, at: float) -> None:
        for pid, depth in descendants(self._pid):
            found = self.seen.get(pid)
            if found is None:
                found = self.seen[pid] = Descendant(pid, depth, at, "")
            if depth == 1 and "multiprocessing" not in found.command:
                found.command = _command(pid) or found.command
            found.read(peak_rss.resident_bytes(pid), peak_rss.footprint_bytes(pid))


def _drain_lines(lines: list[tuple[float, str]], origin: float, stream: IO[bytes]) -> None:
    with stream:
        for raw in stream:
            text = raw.decode(errors="replace").rstrip("\n")
            lines.append((time.perf_counter() - origin, text))
            if text.startswith(WARN):
                print(text, file=sys.stderr, flush=True)


def run_corpus_child(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    audit: Path,
    fifo: Path,
    pattern: re.Pattern[bytes] | None,
    scale: int,
    interval: float,
    parent_interval: float = PARENT_INTERVAL_S,
    machine: Callable[[], Machine] = read_machine,
) -> CorpusRun:
    """Run one corpus child as `run_child` runs a kernel child, in a session of its own, with a thread feeding `fifo` from `audit` (`feed_audit`), the child's stderr read line by line with each line's arrival time, a sampler of the child alone every `parent_interval` seconds, and the `interval` sampler, which reads the machine, also reading every process below the child (`_Descendants`). Every `[warn]` line the child prints is echoed to this process's stderr as it arrives. Once both pipes close and the child has exited, unreaped, the feed is joined, the samplers stop, the child's peak footprint is read, and it is reaped. An exception while it runs, an interrupt or the stop signal `artifact_cycle.stop_signals` raises among them, kills the child's whole process group, its pool workers with it, and reaps the child before it propagates, since a worker would otherwise outlive the line and hold its memory beside the next one."""
    begin = machine()
    origin = time.perf_counter()
    process = subprocess.Popen(
        list(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    drained: dict[str, bytes] = {}
    lines: list[tuple[float, str]] = []
    fed: list[Feed] = []
    readers = [
        threading.Thread(target=_drain, args=(drained, "stdout", process.stdout), daemon=True),
        threading.Thread(target=_drain_lines, args=(lines, origin, process.stderr), daemon=True),
    ]
    feeder = threading.Thread(
        target=_feed,
        args=(fifo, audit, pattern, scale, lambda: _running(process.pid), fed, origin),
        daemon=True,
    )
    found = _Descendants(process.pid)
    trace = Sampler(process.pid, parent_interval, lambda: QUIET, origin)
    sampler = Sampler(process.pid, interval, machine, origin, tick=found.scan)
    try:
        for thread in (*readers, feeder):
            thread.start()
        trace.start()
        sampler.start()
        for reader in readers:
            reader.join()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        wall = time.perf_counter() - origin
        feeder.join()
        samples = sampler.stop()
        traced = trace.stop()
        footprint = peak_rss.peak_footprint_bytes(process.pid)
        _pid, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        sampler.stop()
        trace.stop()
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    child = ChildRun(
        process.returncode,
        drained["stdout"],
        "".join(f"{text}\n" for _at, text in lines),
        wall,
        usage.ru_utime + usage.ru_stime,
        peak_rss.maxrss_to_bytes(usage.ru_maxrss),
        footprint,
        begin,
        samples,
        machine(),
    )
    return CorpusRun(
        child, tuple(lines), traced, tuple(found.seen.values()), fed[0] if fed else Feed(0, "no feed")
    )


def corpus_child_arguments(
    inputs: CorpusInputs, step_dir: Path, cut: CorpusCut, widths: Mapping[str, int]
) -> list[str]:
    """The command line of one corpus line's child (`memory_series_child`)."""
    return [
        sys.executable,
        "-m",
        memory_series_child.__name__,
        "--inputs",
        inputs.to_json(),
        "--audit",
        str(step_dir / AUDIT_NAME),
        "--out",
        str(step_dir / OUT_NAME),
        "--journal",
        str(step_dir / POOLS_NAME),
        f"--jobs={widths['jobs']}",
        f"--signature-jobs={widths['signature_jobs']}",
        f"--write-jobs={widths['write_jobs']}",
        *(["--load"] if cut.load else []),
    ]


def _count(text: str) -> int:
    return int(text.replace(",", ""))


def _median(values: Sequence[int]) -> int | None:
    return round(statistics.median(values)) if values else None


def corpus_marks(lines: Sequence[tuple[float, str]], trace: Sequence[Sample]) -> list[dict[str, Any]]:
    """Return the child's `[m]` lines in order, each with its label, the seconds it arrived at, the resident set and footprint the child read of itself at the mark, the highest of each the trace read since the previous mark's arrival (the mark's own included), and its counts."""
    marks: list[dict[str, Any]] = []
    since = float("-inf")
    for at, text in lines:
        match = _MARK_LINE.match(text)
        if match is None:
            continue
        values = {
            key: None if value == "-" else int(value)
            for key, _, value in (token.partition("=") for token in match.group(2).split())
        }
        resident = values.pop("resident", None)
        footprint = values.pop("footprint", None)
        window = [sample for sample in trace if since < sample.at <= at]
        marks.append(
            {
                "label": match.group(1),
                "at_s": round(at, 3),
                "resident": resident,
                "footprint": footprint,
                "resident_max": _highest([resident, *(sample.resident for sample in window)]),
                "footprint_max": _highest([footprint, *(sample.footprint for sample in window)]),
                **values,
            }
        )
        since = at
    return marks


def corpus_phases(stderr: str) -> dict[str, dict[str, Any]]:
    """Return each `[t] review.build <phase>` line by phase: its seconds, its `rss_gb` and `rss_now_gb` tokens, and the note after its tab."""
    phases: dict[str, dict[str, Any]] = {}
    for match in INNER_LINE.finditer(stderr):
        label = match.group(1)
        if not label.startswith("review.build "):
            continue
        tokens, _, note = (match.group(3) or "").partition("\t")
        entry: dict[str, Any] = {"seconds": float(match.group(2))}
        for token in tokens.split():
            key, separator, value = token.partition("=")
            if separator and key in ("rss_gb", "rss_now_gb"):
                entry[key] = float(value)
        entry["note"] = note
        phases[label.removeprefix("review.build ")] = entry
    return phases


def _rusage(lines: Sequence[tuple[float, str]]) -> dict[str, float]:
    for _at, text in lines:
        if text.startswith(RUSAGE):
            return {
                key: float(value)
                for key, _, value in (token.partition("=") for token in text[len(RUSAGE) :].split())
            }
    return {}


def _pool_reading(record: Mapping | None, members: Sequence[Descendant]) -> dict[str, Any]:
    peaks = sorted(record["worker_peak_rss_bytes"].values()) if record else []
    resident = sorted(member.resident for member in members if member.resident is not None)
    return {
        "width": record["width"] if record else None,
        "reading": record.get("worker_reading", "resident") if record else None,
        "peaks": peaks,
        "peak_median": _median(peaks),
        "peak_top": max(peaks, default=None),
        "controller": record["controller_peak_rss_bytes"] if record else None,
        "resident": resident,
        "resident_median": _median(resident),
        "resident_top": max(resident, default=None),
    }


def corpus_pools(
    records: Sequence[Mapping], found: Sequence[Descendant], marks: Sequence[Mapping]
) -> dict[str, dict[str, Any]]:
    """Return each pool's reading (`POOL_MARKS`): its last pool record in the line's journal, and the workers (`Descendant.worker`) the sampler first saw after the previous pool's mark and by its own."""
    at = {mark["label"]: mark["at_s"] for mark in marks}
    workers = [member for member in found if member.worker]
    pools: dict[str, dict[str, Any]] = {}
    since = float("-inf")
    for name, unit, closing in POOL_MARKS:
        record = next((record for record in reversed(records) if record.get("unit") == unit), None)
        end = at.get(closing)
        members = [] if end is None else [member for member in workers if since < member.first_at <= end]
        if end is not None:
            since = end
        pools[name] = _pool_reading(record, members)
    return pools


def subprocesses(found: Sequence[Descendant]) -> dict[str, int | None]:
    """Return how many processes below the parent were neither a pool's worker nor its resource tracker, the git and crate calls the parent and its workers make, with the highest resident set and footprint any of them reached."""
    others = [member for member in found if not member.worker and not member.tracker]
    return {
        "count": len(others),
        "resident_top": _highest(member.resident for member in others),
        "footprint_top": _highest(member.footprint for member in others),
    }


def write_runs(manifest: Mapping, write_jobs: int) -> list[dict[str, Any]]:
    """Return the write runs a corpus build with `manifest` wrote at `write_jobs`: its classes in shard order, cut by `build._write_runs` at `build._write_width`, as `build._write_corpus` cuts them, each with its classes and units."""
    from rebuild.review import build, unit_index

    to_write = [
        build._ClassToWrite(
            meta["id"], "", "", "", "", "", False, False, "", array("I", bytes(4 * meta["unit_count"])), ()
        )
        for meta in sorted(manifest["classes"], key=lambda meta: unit_index.class_shard_key(meta["id"]))
    ]
    runs = build._write_runs(to_write, build._write_width(to_write, write_jobs))
    return [
        {"classes": [entry.id for entry in run], "units": sum(len(entry.ordinals) for entry in run)}
        for run in runs
    ]


def check_corpus_child(run: CorpusRun, cut: CorpusCut, marks: Sequence[Mapping]) -> None:
    """Fail the run on a child that exited nonzero, a feed that stopped early, or a load line whose build did not stop at the plan's mark, naming the last lines the child printed."""
    tail = "\n".join(text for _at, text in run.lines[-20:])
    if run.child.returncode != 0:
        raise SystemExit(f"{cut.name}: the corpus build exited {run.child.returncode}:\n{tail}")
    if run.feed.error is not None:
        raise SystemExit(f"{cut.name}: the audit's feed stopped early ({run.feed.error}):\n{tail}")
    if cut.load and (not marks or marks[-1]["label"] != PLAN_MARK):
        raise SystemExit(f"{cut.name}: the load line's build did not stop at the plan:\n{tail}")


def measure_corpus_line(
    inputs: CorpusInputs,
    step_dir: Path,
    cut: CorpusCut,
    widths: Mapping[str, int],
    routes: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    idle_s: float = IDLE_S,
    after_s: float = AFTER_S,
    interval_s: float = INTERVAL_S,
    meter: Callable[[], write_meter.WriteReading] = write_meter.read,
    machine: Callable[[], Machine] = read_machine,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one corpus line in `step_dir`, which must not exist and is removed once the line's files are listed, and return its measured fields, everything a corpus line holds but the cut, the widths and the provenance (the module docstring lists them). The meter windows are the table step's: an idle reading `idle_s` before the one just before the spawn, which every block reads from, `end` once the child is reaped, and `after` `after_s` later; the step directory is made before the idle reading and removed after `end`, so `end` holds neither. The child's `TMPDIR` is the step directory's `temp/`, so the temporary files the build's processes make stay inside the step, and one a process leaves there is a stray. The checkout's code digests (`corpus_code` at `cwd`) are read after the idle wait, just before the spawn, and once the child is reaped, before the `after` wait, and returned as `code` and, when they moved, `code_after`. `meter`, `machine` and `sleep` are the write meter, the machine reading and the wait, which a test replaces."""
    from rebuild.review import build

    out_dir = step_dir / OUT_NAME
    arguments = corpus_child_arguments(inputs, step_dir, cut, widths)
    try:
        fifo = route_corpus(step_dir, inputs.signatures, None if cut.load else routes)
        idle = None
        if idle_s > 0:
            idle = meter()
            sleep(idle_s)
        code = corpus_code(cwd)
        start = meter()
        run = run_corpus_child(
            arguments,
            env={**env, "TMPDIR": str(step_dir / TMP_NAME)},
            cwd=cwd,
            audit=inputs.audit,
            fifo=fifo,
            pattern=cut_pattern(cut.excluded),
            scale=cut.scale,
            interval=interval_s,
            machine=machine,
        )
        end = meter()
        code_after = corpus_code(cwd)
        pools_path = step_dir / POOLS_NAME
        records = (
            [json.loads(text) for text in pools_path.read_text(encoding="utf-8").splitlines() if text]
            if pools_path.is_file()
            else []
        )
        manifest_path = out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
        files = step_files(step_dir, expected_files(cut.load))
    finally:
        shutil.rmtree(step_dir, ignore_errors=True)
    rate = None if idle is None else idle_rate(idle, start)
    marks = corpus_marks(run.lines, run.trace)
    check_corpus_child(run, cut, marks)
    after: dict[str, object] | None = None
    if after_s > 0:
        sleep(after_s)
        later = meter()
        after = write_fields(start, later, rate, _difference(run.child.begin.swapouts, machine().swapouts))
    child = run.child
    phases = corpus_phases(child.stderr)
    by_label = {mark["label"]: mark for mark in marks}
    load_note = phases.get("load", {}).get("note", "")
    units_note = phases.get("units", {}).get("note", "")
    write_note = phases.get("manifest+check", {}).get("note", "")
    shaped = _SIGNATURE_NOTE.search(load_note)
    recomputed = _RECOMPUTED_NOTE.search(units_note)
    units_jobs = _JOBS_NOTE.search(units_note)
    write_jobs = _JOBS_NOTE.search(write_note)
    arena = _ARENA_NOTE.search(units_note)
    sidecars = _SIDECAR_NOTE.search(write_note)
    recomputed_count = _count(recomputed.group(1)) if recomputed else None
    rusage = _rusage(run.lines)
    return {
        "rows": run.feed.rows,
        "feed_s": None if run.feed.closed_s is None else round(run.feed.closed_s, 3),
        "units_premerge": by_label.get("load_workload done", {}).get("units"),
        "units": by_label.get(LOAD_MARK, {}).get("units"),
        "windows": by_label.get("by_codepoints built", {}).get("windows"),
        "human_units": None if manifest is None else len(manifest["human_unit_ids"]),
        "recomputed": recomputed_count,
        "classes": None if manifest is None else len(manifest["classes"]),
        "write_runs": None if manifest is None else write_runs(manifest, widths["write_jobs"]),
        "units_width": int(units_jobs.group(1)) if units_jobs else None,
        "handout": (
            build._handout_width(recomputed_count, min(widths["jobs"], recomputed_count))
            if recomputed_count
            else None
        ),
        "write_width": int(write_jobs.group(1)) if write_jobs else None,
        "allocator_env": {key: value for key, value in env.items() if key.startswith("Malloc")},
        "wall_s": round(child.wall, 3),
        "cpu_s": round(rusage["cpu_s"], 3) if "cpu_s" in rusage else None,
        "peak_resident": int(rusage["peak_resident"]) if "peak_resident" in rusage else None,
        "peak_footprint": child.peak_footprint,
        "children": {
            "cpu_s": round(rusage["children_cpu_s"], 3) if "children_cpu_s" in rusage else None,
            "peak_resident": (
                int(rusage["children_peak_resident"]) if "children_peak_resident" in rusage else None
            ),
        },
        "samples": len(child.samples),
        "interval_s": interval_s,
        "parent_samples": len(run.trace),
        "parent_interval_s": PARENT_INTERVAL_S,
        **moments(child),
        "marks": marks,
        "phases": phases,
        "signatures_shaped": _count(shaped.group(2)) if shaped else None,
        "pools": corpus_pools(records, run.descendants, marks),
        "subprocesses": subprocesses(run.descendants),
        "fragments": {
            "recomputed": recomputed_count,
            "arena_gb": float(arena.group(1)) if arena else None,
            "deflated": float(arena.group(2)) if arena else None,
        },
        "sidecar_lines_mb": [float(size) for size in sidecars.group(1).split(", ")] if sidecars else None,
        "written": {
            "idle_s": idle_s,
            "idle_rate": None if rate is None else round(rate),
            "end": write_fields(start, end, rate, _difference(child.begin.swapouts, child.end.swapouts)),
            "after_s": after_s,
            "after": after,
        },
        "files": files,
        "code": code,
        **({"code_after": code_after} if code_after != code else {}),
    }


def _mark_value(line: Mapping, label: str, key: str) -> int | None:
    return next((mark.get(key) for mark in line["marks"] if mark["label"] == label), None)


def corpus_report(lines: Sequence[Mapping]) -> list[str]:
    """Return the fit over the cut lines of each corpus figure against units (`scaling_sweep.fit`): the parent's peak footprint, resident peak and CPU time, the top units and write workers' peaks, the arena and the write runs' sidecar lines, and of the parent's footprint at the load's and the plan's marks against rows; then the ×2 load line's ratio to the ×1 line in its peak footprint and its footprint at each mark both reached, in `CORPUS_MARKS` order. A figure a line lacks is left out of its fit."""
    cuts = [line for line in lines if line["step"] == CORPUS_STEP]
    loads = {line["scale"]: line for line in lines if line["step"] == LOAD_STEP}
    text: list[str] = []
    if len(cuts) < 2:
        text.append(f"the corpus fit needs two cuts; this run has {len(cuts)}")
    else:
        units = [line["units"] or 0 for line in cuts]
        rows = [line["rows"] for line in cuts]
        by_units: dict[str, list[float | None]] = {
            "peak footprint": [line["peak_footprint"] for line in cuts],
            "peak resident": [line["peak_resident"] for line in cuts],
            "cpu": [line["cpu_s"] for line in cuts],
            "units worker top": [line["pools"]["units"]["peak_top"] for line in cuts],
            "write worker top": [line["pools"]["write"]["peak_top"] for line in cuts],
            "arena": [line["fragments"]["arena_gb"] for line in cuts],
            "sidecar lines": [sum(line["sidecar_lines_mb"] or ()) or None for line in cuts],
        }
        by_rows: dict[str, list[float | None]] = {
            f"{label} footprint": [_mark_value(line, label, "footprint") for line in cuts]
            for label in (LOAD_MARK, PLAN_MARK)
        }
        width = max(len(label) for label in (*by_units, *by_rows))
        text.append(f"corpus fit over {len(cuts)} cuts (units {min(units)}..{max(units)})")
        text.extend(
            f"  {label:<{width}} ~ units^{scaling_sweep.exponent(scaling_sweep.fit(units, values))}"
            for label, values in by_units.items()
        )
        text.extend(
            f"  {label:<{width}} ~ rows^{scaling_sweep.exponent(scaling_sweep.fit(rows, values))}"
            for label, values in by_rows.items()
        )
    if 1 in loads and 2 in loads:
        one, two = loads[1], loads[2]
        ratios = [("peak footprint", one["peak_footprint"], two["peak_footprint"])]
        ratios += [
            (f"{label} footprint", _mark_value(one, label, "footprint"), _mark_value(two, label, "footprint"))
            for label in CORPUS_MARKS
        ]
        shown = [(label, a, b) for label, a, b in ratios if a and b]
        width = max((len(label) for label, _a, _b in shown), default=0)
        text.append(f"load-x2 over load-x1 ({two['rows']} rows over {one['rows']})")
        text.extend(f"  {label:<{width}} {b / a:.2f}" for label, a, b in shown)
    return text


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
    """The record at `path` as one holder's run rewrites it: every other holder's lines and fit as the file held them, and this run's lines and fit in place of the holder's earlier ones, holders in `HOLDERS` order. Making one drops the holder's earlier lines, and each `add` rewrites the whole file, so a run stopped partway keeps the lines it finished."""

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
        prog="python -m rebuild.tools.memory_series",
        description="Measure what the M1 table build or the review corpus build holds and writes at each of several alphabet cuts, one build at a time, writing nothing but its record.",
    )
    parser.add_argument(
        "--holder",
        choices=HOLDERS,
        default=TABLES,
        help="the build to measure: the table build (the default) or the review corpus build",
    )
    parser.add_argument(
        "lines",
        nargs="*",
        metavar="k",
        help=f"the rune counts to cut alphabets at, and for the corpus holder the load lines ({', '.join(f'{LOAD_PREFIX}{scale}' for scale in LOAD_SCALES)}); by default {', '.join(map(str, SIZES))} below the whole alphabet and the whole alphabet for the table holder, and {', '.join(map(str, CORPUS_SIZES))} below it, the whole alphabet and both load lines for the corpus holder",
    )
    parser.add_argument(
        "--configs",
        help="the settlement configurations to build, comma-separated; every one by default (table holder)",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        help="the table build's width; by default the one a bare run_m1 derives (table holder)",
    )
    parser.add_argument(
        "--scratch-beside-default",
        type=int,
        help="at most how many deltas start from scratch beside default; by default as a bare run_m1 decides (table holder)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        help="the corpus build's units width; by default `corpus_job_budget(skip_gates=True)`, as a hand build takes it (corpus holder)",
    )
    parser.add_argument(
        "--signature-jobs",
        type=int,
        help="the corpus build's signature width; by default `signature_job_budget()` (corpus holder)",
    )
    parser.add_argument(
        "--write-jobs",
        type=int,
        help="the most workers the corpus build's write may start; by default `corpus_write_job_budget` of the units width (corpus holder)",
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
        help=f"seconds between the sampler's readings of the machine and, for the corpus holder, of the build's workers (default {INTERVAL_S:g})",
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / OUT_PATH, help=f"the record to write (default {OUT_PATH})"
    )
    args = parser.parse_args(argv)
    loads = {f"{LOAD_PREFIX}{scale}": scale for scale in LOAD_SCALES}
    named_loads = [text for text in args.lines if text in loads]
    if args.holder == TABLES and named_loads:
        parser.error(f"{', '.join(named_loads)} name corpus lines; pass --holder {CORPUS}")
    if any(not text.isdecimal() for text in args.lines if text not in loads):
        parser.error(f"a line is a rune count or one of {', '.join(loads)}")
    args.sizes = [int(text) for text in args.lines if text not in loads]
    args.loads = [loads[text] for text in named_loads]
    if any(size < 1 for size in args.sizes):
        parser.error("a size is a rune count of at least 1")
    other = {
        TABLES: (
            ("--jobs", args.jobs),
            ("--signature-jobs", args.signature_jobs),
            ("--write-jobs", args.write_jobs),
        ),
        CORPUS: (
            ("--configs", args.configs),
            ("--kernel-threads", args.kernel_threads),
            ("--scratch-beside-default", args.scratch_beside_default),
        ),
    }[args.holder]
    stated = [flag for flag, value in other if value is not None]
    if stated:
        parser.error(f"{', '.join(stated)} do not apply to the {args.holder} holder")
    for flag, value in (
        ("--kernel-threads", args.kernel_threads),
        ("--jobs", args.jobs),
        ("--signature-jobs", args.signature_jobs),
        ("--write-jobs", args.write_jobs),
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


def run_corpus(args: argparse.Namespace, tree: Path) -> int:
    """Run the corpus holder's lines (the module docstring's "The corpus step") under the held lock: clone and check the inputs, then measure each cut line and each load line in turn, recording each as it finishes, then the corpus fit (`corpus_report`). A line that leaves a stray file stops the run once its line is recorded. Each line reads the checkout's code digests just before its build's spawn and once the child is reaped (`measure_corpus_line`), and a reading that moved during the build, or since the first line's, prints a `[warn]` line naming the line. The corpus step's part of the tree, the clones among it, is removed as the run ends, however it ends."""
    from rebuild.review import audit, build

    kernel_exec.cargo_build()
    empty_tree(tree)
    try:
        inputs = clone_inputs(ROOT, tree / CORPUS / "inputs")
        identity = check_inputs(inputs)
        spec = spec_load.load_spec(*(inputs.spec_root / relative for relative in SPEC_PATHS[:3]))
        if args.lines:
            sizes, scales = args.sizes, args.loads
        else:
            sizes, scales = default_sizes(len(scaling_series.series_order(spec)), CORPUS_SIZES), list(
                LOAD_SCALES
            )
        cuts = corpus_cuts(spec, sizes, scales)
        widths = corpus_widths(args.jobs, args.signature_jobs, args.write_jobs)
        live = json.loads(inputs.manifest.read_text(encoding="utf-8"))
        routes = null_routes(
            live, [*(entry.id for entry in audit.load_ledger(inputs.ledger)), *audit.RESERVED_CLASS_IDS]
        )
        env = corpus_environment()
        head = _head()
        first_code: dict[str, str] | None = None
        lines: list[dict[str, Any]] = []
        record = Record(args.out, CORPUS)
        os.sync()
        time.sleep(SETTLE_S)
        for cut in cuts:
            _progress(
                f"{cut.name}: {args.idle_seconds:g} s idle, the build, then {args.after_seconds:g} s before the meter's last reading"
            )
            measured = measure_corpus_line(
                inputs,
                tree / CORPUS / cut.name,
                cut,
                widths,
                routes,
                env=env,
                cwd=build.REPO_ROOT,
                idle_s=args.idle_seconds,
                after_s=args.after_seconds,
                interval_s=args.interval,
            )
            if first_code is None:
                first_code = measured["code"]
            elif measured["code"] != first_code:
                _warn(f"{cut.name}: the checkout's corpus code or crate binary moved since the first line")
            if "code_after" in measured:
                _warn(f"{cut.name}: the checkout's corpus code or crate binary moved during the build")
            _warn_meter(cut.name, measured["written"])
            if not cut.load and measured["signatures_shaped"]:
                _warn(
                    f"{cut.name}: the load shaped {measured['signatures_shaped']:,} signatures, so the live store did not answer every window"
                )
            line: dict[str, Any] = {
                "step": LOAD_STEP if cut.load else CORPUS_STEP,
                "runes": len(cut.runes),
                "letters": cut.letters,
                "ligs": len(cut.runes) - cut.letters,
                **({"scale": cut.scale} if cut.load else {}),
                **widths,
                **measured,
                "alphabet": list(cut.runes),
                "inputs": identity,
                "cpus": memory_budget.usable_cores(),
                "ram": memory_budget.total_memory_bytes(),
                "head": head,
                "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            lines.append(line)
            text = json.dumps(line)
            print(text, flush=True)
            record.add(text)
            stray = measured["files"]["stray"]
            if stray:
                raise SystemExit(
                    f"{cut.name} left files the corpus step does not route or expect, so the line wrote more than it should: "
                    f"{', '.join(f'{name} ({size:,} B)' for name, size in stray.items())}; the line is in {args.out}"
                )
        for text in ("", *corpus_report(lines)):
            print(text, flush=True)
            record.add(text)
        return 0
    finally:
        shutil.rmtree(tree / CORPUS, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    if spec_load.M1_RUNES_ENV in os.environ:
        raise SystemExit(
            f"the memory series refuses: {spec_load.M1_RUNES_ENV} is set ({os.environ[spec_load.M1_RUNES_ENV]!r}), "
            f"and the series cuts its own alphabets from the whole store; unset it"
        )
    if args.holder == CORPUS and memory_tally.TALLY_ENV in os.environ:
        raise SystemExit(
            f"the memory series refuses: {memory_tally.TALLY_ENV} is set, and the debug tally keeps alive the "
            "collections it reads, which would move every corpus reading; unset it"
        )
    with artifact_cycle.stop_signals():
        try:
            return run_series(args)
        except artifact_cycle.CycleStopped as stopped:
            return 128 + stopped.signum


def run_series(args: argparse.Namespace) -> int:
    """Hold the series' lock (`store_lock.hold_flock` on `<tree>.lock`), refusing when another run holds it, and run the holder `args` names: the corpus holder's lines (`run_corpus`), or the table holder's steps and then the whole-series fit (`report`)."""
    configs = configs_from(args.configs) if args.configs else list(conform.SETTLEMENT_CONFIGS)
    tree = ROOT / TREE_PATH
    lock = store_lock.lock_path_for(tree)
    with ExitStack() as held:
        try:
            held.enter_context(store_lock.hold_flock(lock, blocking=False))
        except store_lock.LockBusy:
            raise SystemExit(f"another memory series holds {lock} and is using {tree}") from None
        if args.holder == CORPUS:
            return run_corpus(args, tree)
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
