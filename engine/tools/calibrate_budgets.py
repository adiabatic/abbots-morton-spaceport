"""Check the checked-in per-unit memory peaks against what this machine measured (`make job-costs`).

Several fan-out widths are the machine's memory divided by a measured per-unit peak, such as what one pytest worker or one kernel configuration holds. Each peak is a checked-in constant, and `UNITS` lists the ones this module checks, beside the constants no width divides by whose argument a width rests on (`CORPUS_BUILD_BYTES`, `LAND_BYTES`, `TABLE_BUILD_PEAK_BYTES`). Two sets of width terms have no row of their own. The kernel's (`DELTA_SLOT_BYTES`, `SCRATCH_PEAK_BYTES`, `DEFAULT_MEMO_BYTES`, `PARKED_FOLD_BYTES`, `FOLD_PREPARATION_BYTES` and `READ_BACK_FOLD_BYTES`) are watched only together, through the kernel-build row (below). The deep sweep's `DEEP_SWEEP_WINDOW_BYTES` and `DEEP_SWEEP_BASE_BYTES` have none: `make conform-deep`'s own check line records each configuration's highest unit peak beside the estimate they give, and nothing checks one against the other. A memory saving or a heavier fixture can move a real peak away from its constant without failing any test. A constant that is too low shows up as a machine in swap, and one that is too high holds a pool to a fraction of the width it has room for. The measurements that catch this are already recorded on every run: each xdist controller's per-worker peaks, the pools run_build records, and the peak RSS the cycle records for every step it spawns. This module compares those measurements with the constants. It builds nothing and imports none of the code whose constants it checks.

It reads measurements only from the cycle-timings journal. A `kind:"pool"` record gives one observation per worker, because the unit is one worker. Its readings sit under the record's `worker_peak_rss_bytes` field whichever line the writer reads, and its `worker_reading` names that line (`cycle_timings.record_pool`). The notes of the rows whose readings are peak footprints (`peak_rss.peak_footprint_self_bytes`) say so, and every other row's pool readings are resident peaks. The string replay's records give one observation each, the crate process's peak divided by the walks it ran at once, because the walks are threads of that one process. The deep replay's records give one observation per settlement configuration, the highest peak among its units' crate processes, because each unit is one walk in a process of its own. A named `kind:"step"` record gives its `peak_rss_bytes`, which is the largest single process in the step's tree, because `peak_rss.reap_peak_rss_bytes` takes the max over the tree instead of the sum. That reading measures one unit only for some steps. For `run_build` the widest process is the table-build child, which the kernel-build row checks, and for `corpus-build` and `land` it is the step's own one process. The kernel-build row also reads the table build's peak footprint off the same record's `[t] kernel_build_tables` phase, and its observation is the higher of the two lines, because the resident line leaves out what the memory compressor holds of the build; a record without a footprint gives its `peak_rss_bytes` alone. It does not measure one unit for `gate:make-test`, whose tree also holds the font publish (`engine.tools.build_fonts`) and `uv run pyright` beside the pool. Each `UNITS` entry states which sources count for it and why.

Constants are read from their source files with `ast`, never imported. pytest loads every conftest under the module name `conftest`, so from under `engine/` a plain `import conftest` gets the wrong file, and `import engine.conftest` would execute a second copy of a file pytest has already loaded and installed its lane-audit hook from. `ast` executes nothing, and it keeps this tool from importing pytest or inheriting that file's `sys.path` edits. The width clauses read their other inputs the same way, so each prints the width its pool actually takes: the conform-sweep row computes the corpus build's width beside it at the build lane's share less the build's main thread (`memory_budget.split_cores`), and reads the floor of its cap beside a corpus build, the acceptance-configuration count, from the lengths of the configuration tuples in `engine/pipeline/conform.py` (`_acceptance_config_count`), along with the corpus constants its second width needs. The kernel row's width is narrowed by the configuration count and the cores in `run_build._table_build_threads`, which this module does not compute, so that clause prints the memory arithmetic and names the narrowing in words.

A peak above its constant means the constant is out of date. It does not mean an artifact is wrong: the cost is a pool of the wrong width, so the cycle does not fail on it. `--check` exits 1 for an overrun and 2 when the tool itself fails, because the artifact cycle reports an overrun on 1 and an informational line on any other nonzero code, and a crash reported as an overrun would report a measurement nobody took. After an overrun the cycle runs `--moved`, which compares each checked constant's value in the working tree with its value at `HEAD` and prints the ones that differ, so the cycle can say which constants have already been re-measured. The fix is to re-measure the constant and set it from the newer measurement; committing it accepts the new value. The tolerance defaults to zero because each constant is already rounded up above its measured peaks, as its comment says: an estimate that is too low puts the machine into swap, while one that is too high only narrows a pool. A peak that reaches the constant has used all of that headroom; one that comes out above the constant once its rule's headroom is applied (below) has used the headroom that rule sets, which the report marks as headroom used before any overrun. `--tolerance` is for a survey with `--host all`, not for relaxing the default.

Each checked constant's comment but TABLE_BUILD_PEAK_BYTES's states the rule that sets it from its highest reading: a headroom it multiplies the reading by (none for FONT_SUITE_WORKER_BYTES, whose `headroom` is 1) and a multiple it rounds up to, which the unit's `headroom` and `quantum` restate. A row whose highest reading, put through that rule (`proposed_constant`), comes out above the constant prints a proposal: the raised value, how the rule reached it, and the width it gives here, and under `--host all` the width it gives on each host in the window, read from that host's newest run record. The proposal appears on every overrun, and before one, marked as headroom used, once the readings have used the headroom the rule leaves. The tool only proposes raises and writes nothing: a documented worst case can lie outside the recent window, so a lower reading is no case for lowering a constant, and a person edits the constant and the comment that argues it together and commits them. The kernel-build row has no rule and proposes no value: its reading is the whole table build, which DEFAULT_MEMO_BYTES, PARKED_FOLD_BYTES per configuration, DELTA_SLOT_BYTES per delta in flight, and FOLD_PREPARATION_BYTES for a slot that runs only default's fold preparation bound together, with READ_BACK_FOLD_BYTES per configuration for the rendezvous below which no width is booked, so no reading of it sets any kernel constant, and an overrun names them and SCRATCH_PEAK_BYTES to re-measure instead. For the same reason it reads every record since its constant's commit rather than the newest `--recent`: a high reading the window dropped would only hide the warning.

Observations are filtered to this host by default, because a per-unit peak is a property of one machine's working set, and a journal concatenated from several machines mixes machines running different versions of the code. Records that finished before the commit that set a constant's current value are set aside before anything is counted (the measurement cutoff), so that after a re-measure lowers a constant, the older, higher peaks from the same host do not trip the check. `git blame` on the constant's line finds that commit, so a re-measure clears its row on the next pass. A constant that is edited but not yet committed has no such commit and keeps every record until it is committed. Within that cutoff, `--recent` keeps only the newest records per unit and host, so an old high peak stops counting once newer runs replace it, and its default is long enough that one anomalous run cannot hide a regression. The journal records which machine measured a peak but not which machine a constant was sized on, so a unit with no rows from this host is reported as unverified on this host. A unit whose rows from this host all predate the constant's commit is reported as unverified too, with the reason that nothing on this host has measured the constant's current value yet.
"""

from __future__ import annotations

import argparse
import ast
import functools
import math
import socket
import subprocess
import statistics
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from engine.tools import memory_budget
from engine.tools.cycle_timings import JOURNAL, load_journal, load_pool_records
from engine.tools.peak_rss import format_gb

ROOT = Path(__file__).resolve().parents[2]

CORPUS_SOURCE = "engine/tools/artifact_cycle.py"
CORPUS_BUILD_NAME = "CORPUS_BUILD_BYTES"
LAND_NAME = "LAND_BYTES"
CONFORM_SWEEP_NAME = "CONFORM_SWEEP_UNIT_BYTES"
CONFORM_SOURCE = "engine/pipeline/conform.py"
DEEP_REPLAY_SOURCE = "engine/tools/deep_replay.py"
DEEP_REPLAY_NAME = "DEEP_REPLAY_PEAK_BYTES"


@dataclass(frozen=True)
class Unit:
    """One unit that a fan-out width divides the machine's memory by, or that a width's argument rests on: its constant and the file that holds it (both None for a row that is reported without a constant), the journal records that measure it, the text printed with its figures, and the rule the constant's comment sets it by from its highest reading: `headroom` to multiply the reading by and `quantum`, in bytes, to round the product up to a multiple of. Both are None for a row whose reading does not set its constant.

    `recency_bound` False reads every record since the constant's commit, where `--recent` would keep only the newest. `footprint_phase` names the `[t]` phase whose `footprint_gb` token gives a step record a second line, a peak footprint, beside its `peak_rss_bytes`: the observation is then the higher of the two, and the report prints both (`_lines_line`). None reads `peak_rss_bytes` alone.
    """

    name: str
    constant: str | None
    source: str | None
    pool_units: tuple[str, ...]
    step_names: tuple[str, ...]
    step_caveat: str
    note: str
    headroom: float | None = None
    quantum: int | None = None
    recency_bound: bool = True
    footprint_phase: str | None = None


# The kernel's constants live in one file: DELTA_SLOT_BYTES divides the delta fan-out width, DEFAULT_MEMO_BYTES and PARKED_FOLD_BYTES per settlement configuration are subtracted from the machine's memory before that division, FOLD_PREPARATION_BYTES books the slot default's fold preparation takes once every delta has one, SCRATCH_PEAK_BYTES bounds a slot in a build without default's memo and each delta started from scratch beside default, UNCAPPED_SCRATCH_PEAK_BYTES bounds default in such a start while a delta is left to read its memo, READ_BACK_FOLD_BYTES per settlement configuration books the rendezvous, where every configuration reads its product back whole, below which no width is booked, and the kernel-build row checks the higher of the build step peak and the table build's peak footprint against TABLE_BUILD_PEAK_BYTES.
KERNEL_SOURCE = "engine/pipeline/kernel_exec.py"
KERNEL_DELTA_NAME = "DELTA_SLOT_BYTES"
KERNEL_PREPARATION_NAME = "FOLD_PREPARATION_BYTES"
KERNEL_SCRATCH_NAME = "SCRATCH_PEAK_BYTES"
KERNEL_UNCAPPED_NAME = "UNCAPPED_SCRATCH_PEAK_BYTES"
KERNEL_MEMO_NAME = "DEFAULT_MEMO_BYTES"
KERNEL_PARKED_NAME = "PARKED_FOLD_BYTES"
KERNEL_READ_BACK_NAME = "READ_BACK_FOLD_BYTES"

UNITS: tuple[Unit, ...] = (
    Unit(
        name="font-suite",
        constant="FONT_SUITE_WORKER_BYTES",
        source="conftest.py",
        pool_units=("font-suite",),
        step_names=(),
        step_caveat="",
        note="Only the controller's own per-worker figures measure this unit, and the exclusion of gate:make-test's step peak is deliberate: that step's peak is the widest single process in the tree under `make test`, and that tree holds the font publish (`engine.tools.build_fonts`) and `uv run pyright` — spawned from pytest_configure, beside the pool rather than in it — each of which dwarfs a worker this small. Admitting it as an observation would report a build's footprint as a worker's and trip this check on its first pass.",
        headroom=1.0,
        quantum=100_000_000,
    ),
    Unit(
        name="engine-contracts",
        constant=None,
        source=None,
        pool_units=("engine-contracts",),
        step_names=(),
        step_caveat="",
        note="The engine suite's width is a count of cores — a hand run takes every core this process may actually run on, and the cycle hands it the cores the corpus build's process leaves, less gate:make-test's pool, which holds the gate lane's share (`memory_budget.split_cores`), under the overlap policy on a pass that runs that gate beside the build lane (`artifact_cycle.contracts_pool_width`) — and nothing divides the machine's memory by a per-worker cost to reach either: no test in it reads a live build artifact, so no worker holds a working set worth bounding, and there is nothing here to calibrate. The observations are collected and reported anyway, so that if the suite ever grows a memory-derived width the figure to set it from is already on the record rather than a measurement someone still has to go and take.",
    ),
    Unit(
        name="kernel-build",
        constant="TABLE_BUILD_PEAK_BYTES",
        source=KERNEL_SOURCE,
        pool_units=(),
        step_names=("build",),
        step_caveat="run_build's peak is the widest single process in its tree, the max over its children, and on both fleet machines that is the one build-tables child holding every settlement configuration — default's retained memo beside every delta build in flight, the deltas started from scratch beside default, and each configuration's prepared fold product, parked compact from its preparation until every configuration has parked and then read back whole through the cross-configuration exchange — so the step peak reads the whole table build at whatever width the cycle handed it, never one configuration. The other candidate is the string replay's child, which runs every settlement configuration at once in one wave (REPLAY_PEAK_BYTES apiece, `--replay-threads`) and peaks at a fraction of the build on the shipped alphabet (3.58 to 4.04 GB maxrss for the five-configuration wave under `/usr/bin/time -l` on the 18-core M5 Pro 48 GiB MacBook Pro, its footprint 0.05 GB below, against the table build's maximum this row reports); should a replay ever outrun the build, this row reads the replay, which the replay-walk row reads on its own, and the constant to re-measure is then that one rather than this one. A pass that reuses the tables on disk runs no build-tables child at all (`reused=true` on its `[t] kernel_build_tables` line, `run_build.build_tables`), so its step peak reads the widest of the rest, the string replay's child among them, never the build, and its line carries no footprint: that observation is no reading of this constant. DELTA_SLOT_BYTES, what each delta in flight adds beyond its parked product and the figure the width is divided out of, DEFAULT_MEMO_BYTES, the memo subtracted from the machine's memory first, PARKED_FOLD_BYTES, one configuration's parked fold product, subtracted once per configuration, FOLD_PREPARATION_BYTES, the slot default's fold preparation takes once every delta has one, and READ_BACK_FOLD_BYTES, one configuration's product read back at the rendezvous, are not measured by any step here: their reading is the direct whole-wave measurement under --cache-stats, and the bound they state, kernel_exec.table_build_booking_bytes, is that this unit's peak stays under the memo, plus every configuration's parked product, plus one delta slot per worker slot of the width up to the delta count, plus the preparation's slot above it, with SCRATCH_PEAK_BYTES less a parked product in place of the slot of each delta started from scratch beside default, and at least one SCRATCH_PEAK_BYTES for each of them together with UNCAPPED_SCRATCH_PEAK_BYTES for default while a delta is left to read its memo, or one more SCRATCH_PEAK_BYTES once none is, or under the rendezvous, a READ_BACK_FOLD_BYTES for each configuration, where that is higher. Each reading is the higher of the step peak and the build-tables child's peak footprint, which kernel_exec.build_table_files reads between the child's exit and its reap and run_build writes on its `[t] kernel_build_tables` line: the step peak is a resident line, which leaves out what the memory compressor holds of the build, so the report's `lines` line shows the highest reading's resident and footprint figures, and a record without a footprint reads its step peak alone.",
        note="The direct measurement is one build-tables over every settlement configuration under /usr/bin/time -l with --cache-stats, which is what to reach for before re-measuring any kernel constant; this row is the cheap standing watch beside it rather than a replacement for it. It reads every record since its constant's commit, not the newest `--recent`: it proposes no value, so a high reading the window dropped would only hide the warning until the constant was next re-measured.",
        recency_bound=False,
        footprint_phase="kernel_build_tables",
    ),
    Unit(
        name="build-parent",
        constant=None,
        source=None,
        pool_units=("build-parent",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `run_build.full_run`: one record per bare run_build that joined its table-only branch, whatever its gate decided, with one observation, run_build's own process's lifetime peak footprint (`peak_rss.peak_footprint_self_bytes`), never a child's. No constant books it: it runs beside the table build's crate and the string replay's crate and absorbs, and none of their widths subtracts it, so it comes out of the reserve. The build step peak is deliberately not admitted: that step's widest process is the table build's child, which the kernel-build row reads. A `--gates-only` or `--conform-only` run writes nothing here, since its process holds a different collection. The figure follows the spec and the merged rules, so read it beside the alphabet a pass ran at.",
    ),
    Unit(
        name="replay-walk",
        constant="REPLAY_PEAK_BYTES",
        source=KERNEL_SOURCE,
        pool_units=("replay-walk",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `run_build.run_replay_strings`: one record per replay that walked any text, at whatever width run_build ran it. A full replay walks each configuration in a `replay-strings` process of its own, and its record holds one observation a configuration that passed, that walk's own crate peak RSS (`kernel_exec.replay_memo_walk` reaps it with `os.wait4`). A narrowed replay after a rune edit walks the configurations as threads of one process, and its record holds one observation, that process's peak RSS (`kernel_exec.replay_strings`) divided by the walks it ran at once; it walks fewer texts and reads lower. The constant books each full replay's walk with the absorb worker beside it, whose peak the replay-absorb row reports, so a walk's own reading sits under the constant by that worker's share. The build step peak is deliberately not admitted: that step's widest process is the table build's child, which the kernel-build row measures. Nothing subtracts this constant or divides by it beside the replay, because the replay starts after the table build's process has exited.",
        headroom=1.25,
        quantum=100_000_000,
    ),
    Unit(
        name="replay-absorb",
        constant=None,
        source=None,
        pool_units=("replay-absorb",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `run_build.run_replay_strings`: one record per full replay, with one observation per absorb worker that read its configuration's window memo whole, labeled `<config> absorb`, its peak footprint (`peak_rss.peak_footprint_self_bytes`), a red replay's among them, since the verdict moves no absorb's peak. The workers run on a spawn pool with one worker a walked configuration, each beside its own walk, reading the walk's memo from a pipe the walk's crate inherits as it arrives, and holding the finished file from the walk's end until the verdict, or at a width below the configuration count staging or discarding it at once while a walk still waits for a slot, so each reading is one absorb's own. No constant books them apart: `kernel_exec.REPLAY_PEAK_BYTES` books each with its walk, which the replay-walk row checks. A narrowed replay writes no memo and so no record. The conformance sweep's absorbs are among the conform-sweep row's readings.",
    ),
    Unit(
        name="conform-sweep",
        constant=CONFORM_SWEEP_NAME,
        source=CORPUS_SOURCE,
        pool_units=("conform-sweep",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `run_build_gates.run_font_conformance`'s own fan-in rather than from a pytest controller: one record per pooled conformance sweep at `conform.SWEEP_MAX_LENGTH`, one observation per acceptance configuration, the highest peak footprint among its units as `run_build_gates._conformance_unit` returned them to the parent process beside the results, plus one per settle-memo absorb that ran (`<config> absorb`), each run in a fresh process in the slot a unit frees as soon as its configuration's units have finished and their counts check (`run_build_gates._absorb_memo`, over `conform.absorb_settle_memo`). The pool runs one unit or absorb per process (`max_tasks_per_child=1`), so every reading is one task's own and the record measures a unit's cost rather than the pool's shape. The readings are footprints (`peak_rss.peak_footprint_self_bytes`), which leave out the clean pages of the mapped settle memo parts: the page cache holds those once per machine, and the reserve covers them; the pool-record field keeps its `worker_peak_rss_bytes` name. A hand sweep at a `--conform-max-length` past the per-edit sweep's runs the same pooled branch and writes nothing here, since its units build a memo over every text up to its maximum length in process and hold a different collection from the per-edit sweep's. `make conform-deep` runs its own pool and records each unit's peak footprint beside its estimate on its check line instead (`engine/tools/deep_sweep.py`). The gate:conform step peak is deliberately not admitted: `reap_peak_rss_bytes` maxes over the child's tree rather than summing it, so that step's peak reads one process and never the pool. The row is quiet on a pass whose gate:conform skips on its green, and at `--jobs 1`, since the serial conformance sweep starts no pool; a hand `run_build --conform-only` at any wider width puts observations on the record. The row checks `CONFORM_SWEEP_UNIT_BYTES`, the divisor `artifact_cycle.conform_job_budget` divides the machine's memory by once the build lane's larger step is off it.",
        headroom=1.25,
        quantum=100_000_000,
    ),
    Unit(
        name="corpus-build",
        constant=CORPUS_BUILD_NAME,
        source=CORPUS_SOURCE,
        pool_units=(),
        step_names=("corpus-build",),
        step_caveat="the corpus build is one Python process that spawns nothing, so its step peak is the build's own.",
        note="No width divides by this constant: the conformance sweep, which runs beside the corpus build in the gate lane, takes it off the machine's memory before it divides (`artifact_cycle._conform_build_lane`). The build holds the corpus's fragments until it writes them, so its peak grows with the units an edit makes.",
        headroom=1.25,
        quantum=100_000_000,
    ),
    Unit(
        name="land",
        constant=LAND_NAME,
        source=CORPUS_SOURCE,
        pool_units=(),
        step_names=("land",),
        step_caveat="the land is one process that spawns only a `cp -c -R` clone of the corpus, so its step peak is the land's own.",
        note="No width divides by this constant or subtracts it: the land runs after the corpus build in the build lane, so the reservation the conformance sweep makes for that step (`artifact_cycle._conform_build_lane`) covers it as long as it stays below CORPUS_BUILD_BYTES, which `test_the_land_holds_less_than_the_build_lane_step_before_it` checks. It reads the store whole, which holds the verdicts made since the last accept, and the new corpus's human unit ids.",
        headroom=1.25,
        quantum=100_000_000,
    ),
    Unit(
        name="deep-replay-walk",
        constant=DEEP_REPLAY_NAME,
        source=DEEP_REPLAY_SOURCE,
        pool_units=("deep-replay-walk",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `make replay-deep` (`deep_replay.record_walk_peaks`): one record per walk at the checked-in memo ceiling, written beside its `replay-deep` check line, with one observation per settlement configuration, the highest peak RSS among its units' crate processes, each unit one walk in a process of its own. No cycle runs the deep replay, so the row moves only when someone runs it.",
        headroom=1.25,
        quantum=100_000_000,
    ),
)


def _module_assignment(source: str, name: str) -> tuple[int, int] | None:
    """Return the integer `source` assigns to `name` at module scope and the line number of that assignment, or None when it assigns none."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return int(ast.literal_eval(node.value)), node.lineno
    return None


@functools.cache
def _constant_assignment(path: Path, name: str) -> tuple[int, int]:
    """Return the integer `path` assigns to `name` at module scope and the line number of that assignment, parsed with `ast` for the reason the module docstring gives. Only module-level assignments count, so a same-named local inside a function is ignored. A missing name raises, so a renamed constant fails the check instead of going unchecked."""
    found = _module_assignment(path.read_text(encoding="utf-8"), name)
    if found is not None:
        return found
    raise RuntimeError(
        f"{path} defines no {name}: make job-costs checks a measured peak against that constant, and a constant it cannot find is a width nothing is watching. Move the name in the UNITS registry beside whatever moved it there."
    )


def _int_constant(path: Path, name: str) -> int:
    return _constant_assignment(path, name)[0]


@functools.cache
def _settlement_config_count(path: Path) -> int:
    """Return the number of settlement configurations `path` defines: the length of its module-scope `SETTLEMENT_CONFIGS` tuple, parsed with `ast` like the constants. The kernel-build row's width takes one parked fold product per settlement configuration off the machine (`kernel_exec.kernel_threads_default`). A missing name or a value that is not a literal tuple raises, because the report would otherwise state a wrong width."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SETTLEMENT_CONFIGS":
                value = ast.literal_eval(node.value) if isinstance(node.value, ast.Tuple) else None
                if not isinstance(value, tuple):
                    raise RuntimeError(
                        f"{path} assigns SETTLEMENT_CONFIGS something other than a literal tuple: the kernel-build row reads the settlement-configuration count off that tuple's length to state the table build's width."
                    )
                return len(value)
    raise RuntimeError(
        f"{path} defines no SETTLEMENT_CONFIGS: the kernel-build row reads the settlement-configuration count off it to state the table build's width. Point `CONFORM_SOURCE` at the file that defines it, or update this reader beside whatever moved it."
    )


@functools.cache
def _acceptance_config_count(path: Path) -> int:
    """Return the number of acceptance configurations `path` defines: the summed lengths of its module-scope `SETTLEMENT_CONFIGS`, `OVERLAY_CONFIGS` and `COSMETIC_CONFIGS` tuples, which `conform.ACCEPTANCE_CONFIGS` concatenates, parsed with `ast` like the constants. It is the floor of the conformance sweep's cap beside a corpus build (`artifact_cycle._conform_core_cap`). A missing name or a value that is not a literal tuple raises, because the report would otherwise state a wrong width."""
    names = ("SETTLEMENT_CONFIGS", "OVERLAY_CONFIGS", "COSMETIC_CONFIGS")
    lengths: dict[str, int] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                value = ast.literal_eval(node.value) if isinstance(node.value, ast.Tuple) else None
                if not isinstance(value, tuple):
                    raise RuntimeError(
                        f"{path} assigns {target.id} something other than a literal tuple: the conform-sweep row reads the acceptance-configuration count off that tuple's length to state the floor of the conformance sweep's cap."
                    )
                lengths[target.id] = len(value)
    missing = [name for name in names if name not in lengths]
    if missing:
        raise RuntimeError(
            f"{path} defines no {' or '.join(missing)}: the conform-sweep row reads the acceptance-configuration count off those tuples to state the floor of the conformance sweep's cap. Point `CONFORM_SOURCE` at the file that defines them, or update this reader beside whatever moved them."
        )
    return sum(lengths.values())


def constant_set_at(path: Path, name: str, *, root: Path = ROOT) -> str | None:
    """Return the committer time of the commit that last changed `name`'s assignment line, as an ISO-Z stamp like the journal's `finished_at` fields, or None when there is no such commit: the line is uncommitted, the file is untracked, `root` is not a git checkout, or git is not installed. None means no cutoff, which only keeps older records in the count.

    `git blame` returns the last commit that touched the line for any reason, including a reformat, so the cutoff can be later than the re-measure. That is the safe direction: a cutoff that is too new sets aside records that were valid evidence, and the row reports how many, while a cutoff that is too old would check a new constant against peaks from code that no longer exists. The committer time is used instead of the author time because a record measured before the commit was made is a measurement of the code the new value was taken from, not of the code it was accepted on.
    """
    _, lineno = _constant_assignment(path, name)
    try:
        blame = subprocess.run(
            ["git", "blame", "--porcelain", "-L", f"{lineno},{lineno}", "--", str(path)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if blame.returncode != 0:
        return None
    return _commit_time_from_blame(blame.stdout)


def _commit_time_from_blame(porcelain: str) -> str | None:
    """Return the committer time from one line's porcelain blame, or None for an uncommitted line. Porcelain marks an uncommitted line with the all-zero hash, and its committer time is the time of the blame."""
    lines = porcelain.splitlines()
    if not lines or lines[0].startswith("0" * 40):
        return None
    for line in lines:
        if line.startswith("committer-time "):
            seconds = int(line.split()[1])
            return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def read_constant_commit_times(root: Path = ROOT) -> dict[str, str]:
    """Map each unit name to the stamp of the commit that last set its constant, for every unit whose constant is committed. `build_rows` keeps every record for a unit missing here."""
    stamps: dict[str, str] = {}
    for unit in UNITS:
        if unit.constant is None or unit.source is None:
            continue
        stamp = constant_set_at(root / unit.source, unit.constant, root=root)
        if stamp is not None:
            stamps[unit.name] = stamp
    return stamps


def watched_constants() -> tuple[tuple[str, str], ...]:
    """Return the `(source, name)` of every constant this module checks: each `UNITS` constant, then the seven kernel constants the kernel-build row's width and bound read (`DELTA_SLOT_BYTES`, `DEFAULT_MEMO_BYTES`, `PARKED_FOLD_BYTES`, `FOLD_PREPARATION_BYTES`, `SCRATCH_PEAK_BYTES`, `UNCAPPED_SCRATCH_PEAK_BYTES` and `READ_BACK_FOLD_BYTES`)."""
    pairs = [
        (unit.source, unit.constant)
        for unit in UNITS
        if unit.constant is not None and unit.source is not None
    ]
    return (
        *pairs,
        (KERNEL_SOURCE, KERNEL_DELTA_NAME),
        (KERNEL_SOURCE, KERNEL_MEMO_NAME),
        (KERNEL_SOURCE, KERNEL_PARKED_NAME),
        (KERNEL_SOURCE, KERNEL_PREPARATION_NAME),
        (KERNEL_SOURCE, KERNEL_SCRATCH_NAME),
        (KERNEL_SOURCE, KERNEL_UNCAPPED_NAME),
        (KERNEL_SOURCE, KERNEL_READ_BACK_NAME),
    )


def committed_constant(source: str, name: str, *, root: Path = ROOT) -> int | None:
    """Return the integer `HEAD`'s copy of `source` assigns to `name`, or None when that copy assigns none. It raises when git cannot show the file at `HEAD` (no checkout, no commit, or an untracked file), because the caller would otherwise report every constant as moved."""
    try:
        shown = subprocess.run(
            ["git", "show", f"HEAD:{source}"], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise RuntimeError(f"git could not show HEAD:{source}: {exc}") from exc
    if shown.returncode != 0:
        raise RuntimeError(f"git could not show HEAD:{source}: {shown.stderr.strip()}")
    found = _module_assignment(shown.stdout, name)
    return None if found is None else found[0]


def moved_constants(root: Path = ROOT) -> list[tuple[str, int | None, int]]:
    """Return `(name, value at HEAD, value in the working tree)` for every watched constant whose working-tree value differs from `HEAD`'s, in `watched_constants` order. It compares the values, not the text, so an uncommitted edit elsewhere in a constant's file moves nothing."""
    moved: list[tuple[str, int | None, int]] = []
    for source, name in watched_constants():
        here = _module_assignment((root / source).read_text(encoding="utf-8"), name)
        if here is None:
            raise RuntimeError(f"{root / source} defines no {name}")
        committed = committed_constant(source, name, root=root)
        if committed != here[0]:
            moved.append((name, committed, here[0]))
    return moved


def moved_lines(moved: list[tuple[str, int | None, int]]) -> list[str]:
    """Return one `NAME: <HEAD value> at HEAD, <working-tree value> in the working tree` line per moved constant, in gigabytes. The artifact cycle reads the constant's name from the text before the colon."""
    return [
        f"{name}: {'not assigned' if committed is None else format_gb(committed) + ' GB'} at HEAD, {format_gb(here)} GB in the working tree"
        for name, committed, here in moved
    ]


def read_constants(root: Path = ROOT) -> dict[str, int]:
    """Map each unit name to its checked-in peak, for every unit that has one. `root` lets a test point it at a copied tree."""
    return {
        unit.name: _int_constant(root / unit.source, unit.constant)
        for unit in UNITS
        if unit.constant is not None and unit.source is not None
    }


@dataclass(frozen=True)
class Observation:
    """One peak measurement of one unit, with the host that measured it, the record's `finished_at`, and the record kind (`pool` or `step:<name>`). A step record's observation also keeps its two lines, `resident_bytes` (its `peak_rss_bytes`) and `footprint_bytes` (its unit's `footprint_phase` reading), either None where the record has none; `peak_bytes` is the higher of them."""

    peak_bytes: int
    host: str
    at: str
    source: str
    resident_bytes: int | None = None
    footprint_bytes: int | None = None


def _source_records(
    unit: Unit, pool_records: list[dict], steps_by_run: dict[str, list[dict]]
) -> list[tuple[dict, str]]:
    """Return every journal record that measures `unit`, each paired with the `source` label its observations carry. Pool records come before step records; this order only breaks ties in the recency sort, after the timestamps."""
    records = [(record, "pool") for record in pool_records if str(record.get("unit", "")) in unit.pool_units]
    for step_list in steps_by_run.values():
        for step in step_list:
            name = step.get("name")
            if isinstance(name, str) and name in unit.step_names:
                records.append((step, f"step:{name}"))
    return records


def observations(
    unit: Unit,
    pool_records: list[dict],
    steps_by_run: dict[str, list[dict]],
    *,
    host: str | None,
    recent: int,
    since: str | None = None,
) -> tuple[list[Observation], int, int]:
    """Return the observations of one unit that pass the host filter, the measurement cutoff and the recency bound, with the number of records the host filter dropped and the number the measurement cutoff set aside.

    `since` is the ISO-Z stamp of the commit that set the constant's current value. A record that finished before it is set aside before the recency bound applies, so the bound counts only records measured on the code the constant describes. A record with no stamp is kept. None means no cutoff.

    Each worker peak in a kept pool record is a separate observation, because the unit is one worker: the median is a typical worker, and the max is the worst worker seen, which is the figure a constant has to cover. The controller's own peak is in the record but not counted, because it measures a different process and would pull the median toward a figure no worker held.

    The recency bound keeps the newest `recent` source records per host (each pool record or step record counts once, not once per worker), ordered by `finished_at` with read order breaking ties; ISO-Z stamps sort correctly as strings. `recent <= 0` keeps everything, and so does a unit whose `recency_bound` is False. The bound keeps one anomalous run from counting for as long as the journal lasts. It applies per host even when no host is selected, so under `--host all` a machine that runs many cycles a day cannot fill the window and leave a quieter machine unchecked. The dropped count is returned so the report can say that records from other hosts exist and were not checked.
    """
    records = _source_records(unit, pool_records, steps_by_run)
    dropped = 0
    if host is not None:
        kept = [item for item in records if str(item[0].get("host", "")) == host]
        dropped = len(records) - len(kept)
        records = kept
    older = 0
    if since is not None:
        kept = [
            item
            for item in records
            if not (isinstance(item[0].get("finished_at"), str) and item[0]["finished_at"] < since)
        ]
        older = len(records) - len(kept)
        records = kept
    if recent > 0 and unit.recency_bound:
        by_host: dict[str, list[int]] = {}
        for index, (record, _) in enumerate(records):
            by_host.setdefault(str(record.get("host", "")), []).append(index)
        keep: set[int] = set()
        for indexes in by_host.values():
            ranked = sorted(indexes, key=lambda index: (str(records[index][0].get("finished_at", "")), index))
            keep.update(ranked[-recent:])
        records = [item for index, item in enumerate(records) if index in keep]
    observed: list[Observation] = []
    for record, source in records:
        record_host = str(record.get("host", "?"))
        stamp = record.get("finished_at")
        at = stamp if isinstance(stamp, str) else ""
        if source == "pool":
            peaks = record.get("worker_peak_rss_bytes")
            if isinstance(peaks, dict):
                for peak in peaks.values():
                    if isinstance(peak, int | float):
                        observed.append(Observation(int(peak), record_host, at, source))
        else:
            peak = record.get("peak_rss_bytes")
            resident = int(peak) if isinstance(peak, int | float) else None
            footprint = _phase_footprint_bytes(record, unit.footprint_phase) if unit.footprint_phase else None
            lines = [line for line in (resident, footprint) if line is not None]
            if lines:
                observed.append(Observation(max(lines), record_host, at, source, resident, footprint))
    return observed, dropped, older


def _phase_footprint_bytes(record: dict, label: str) -> int | None:
    """Return the highest peak footprint a step record's `[t] <label>` phases carry (`footprint_gb`, which `cycle_timings.parse_inner_timings` stores), in bytes, or None when none carries one."""
    readings = [
        round(phase["footprint_gb"] * 1e9)
        for phase in record.get("inner") or []
        if isinstance(phase, dict)
        and phase.get("label") == label
        and isinstance(phase.get("footprint_gb"), int | float)
    ]
    return max(readings) if readings else None


def proposed_constant(max_bytes: int, headroom: float, quantum: int) -> int:
    """Return the value a constant's rule sets from its highest reading: `max_bytes` times `headroom`, rounded up to a multiple of `quantum` bytes. The headroom is read as the decimal it is written as, so a product that lands on a multiple of the quantum stays there instead of rounding up past it on a binary fraction."""
    return math.ceil(Fraction(max_bytes) * Fraction(str(headroom)) / quantum) * quantum


@dataclass(frozen=True)
class UnitRow:
    """One unit's result, computed before anything is rendered: the constant, the observations that passed the filters, what the filters set aside, whether a peak exceeds the constant, whether this host has no observations of a unit that has a constant, and the raised value the unit's rule proposes, if any."""

    unit: Unit
    constant_bytes: int | None
    observed: list[Observation]
    dropped_other_hosts: int
    set_at: str | None
    dropped_older: int
    overrun: bool
    unverified_here: bool
    proposal: int | None


def build_rows(
    pool_records: list[dict],
    steps_by_run: dict[str, list[dict]],
    *,
    constants: dict[str, int],
    host: str | None,
    recent: int,
    tolerance: float,
    constant_commit_times: Mapping[str, str] | None = None,
) -> list[UnitRow]:
    """Return one row per unit, in `UNITS` order. The function reads nothing itself: the constants and their commit times are passed in and the machine is not consulted, so tests can assert on it directly. `constant_commit_times` maps a unit name to the ISO-Z stamp its records must follow; a unit it does not name keeps every record, and `recent <= 0` ignores the stamps, because that caller asked for every record. An overrun is a peak strictly greater than the constant times `1 + tolerance`, so a peak equal to the constant fits.

    `unverified_here` is set only when a host was named. Under `--host all` there is no single host, and the observed line already says that nothing was measured.

    `proposal` is the unit's rule applied to its highest observation (`proposed_constant`), kept only when it exceeds the constant, so a row never proposes lowering one. The overrun test does not read it: a row with a proposal and no overrun has used its constant's headroom before any peak outran the constant.
    """
    rows: list[UnitRow] = []
    for unit in UNITS:
        since = (constant_commit_times or {}).get(unit.name) if recent > 0 else None
        observed, dropped, older = observations(
            unit, pool_records, steps_by_run, host=host, recent=recent, since=since
        )
        constant_bytes = constants.get(unit.name)
        overrun = (
            constant_bytes is not None
            and bool(observed)
            and max(item.peak_bytes for item in observed) > constant_bytes * (1 + tolerance)
        )
        proposal = None
        if constant_bytes is not None and observed and unit.headroom is not None and unit.quantum is not None:
            proposed = proposed_constant(
                max(item.peak_bytes for item in observed), unit.headroom, unit.quantum
            )
            if proposed > constant_bytes:
                proposal = proposed
        rows.append(
            UnitRow(
                unit=unit,
                constant_bytes=constant_bytes,
                observed=observed,
                dropped_other_hosts=dropped,
                set_at=since,
                dropped_older=older,
                overrun=overrun,
                unverified_here=constant_bytes is not None and not observed and host is not None,
                proposal=proposal,
            )
        )
    return rows


def host_machines(runs: Mapping[str, dict]) -> dict[str, tuple[int, int]]:
    """Map each host to the cores and total memory (`cpu_count`, `mem_total_bytes`) its newest run record states, by `finished_at`, for the proposal's per-host widths under `--host all`. A run record that lacks either figure is skipped, so a host none of whose run records states both is missing from the map."""
    newest: dict[str, tuple[str, int, int]] = {}
    for run in runs.values():
        host = run.get("host")
        cores = run.get("cpu_count")
        total = run.get("mem_total_bytes")
        if not isinstance(host, str) or not isinstance(cores, int) or not isinstance(total, int):
            continue
        if cores <= 0 or total <= 0:
            continue
        stamp = run.get("finished_at")
        stamp = stamp if isinstance(stamp, str) else ""
        if host not in newest or stamp >= newest[host][0]:
            newest[host] = (stamp, cores, total)
    return {host: (cores, total) for host, (_, cores, total) in newest.items()}


def _record_count(observed: list[Observation]) -> int:
    """Return how many journal records a unit's observations came from, which tells a reader whether a max comes from one run or many. Records are identified by host, stamp, and source, so two pools of one unit that finished in the same second on one host count as one. The undercount affects only this printed figure."""
    return len({(item.host, item.at, item.source) for item in observed})


def _percent(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def _plural(count: int, noun: str) -> str:
    """Return the count and the noun, plural unless the count is 1."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _since_line(row: UnitRow) -> str | None:
    """Return the line that says which records count for this unit's constant: the constant's commit time and how many older records were set aside, or that the constant is uncommitted and every record counts. None for a unit without a constant."""
    if row.constant_bytes is None:
        return None
    if row.set_at is None:
        return f"  since     : {row.unit.constant} has no commit yet (edited, untracked, or no git here), so every record stands"
    aside = f"; {_plural(row.dropped_older, 'older record')} set aside" if row.dropped_older else ""
    return f"  since     : {row.set_at}, the commit that set {row.unit.constant} to its current value{aside}"


def _observed_line(row: UnitRow, *, host: str | None) -> str:
    if not row.observed:
        return f"  observed  : no observations {'on ' + host if host else 'in the journal'}"
    peaks = [item.peak_bytes for item in row.observed]
    tail = ""
    if row.constant_bytes is not None:
        tail = f" ({_percent(max(peaks), row.constant_bytes)}% of the constant)"
    return (
        f"  observed  : {_plural(_record_count(row.observed), 'record')}, {_plural(len(peaks), 'observation')}"
        f" — median {format_gb(statistics.median(peaks))} GB, max {format_gb(max(peaks))} GB{tail}"
    )


def _lines_line(row: UnitRow) -> str | None:
    """Return the line that splits a two-line row's max into its resident and footprint lines and counts the observations that carry a footprint, so an overrun the memory compressor's share makes can be told apart from a unit that grew. None for a unit with no `footprint_phase`, or when no observation carries a footprint."""
    if row.unit.footprint_phase is None or all(item.footprint_bytes is None for item in row.observed):
        return None
    top = max(row.observed, key=lambda item: item.peak_bytes)
    resident = (
        "no resident line" if top.resident_bytes is None else f"resident {format_gb(top.resident_bytes)} GB"
    )
    footprint = (
        "no footprint line"
        if top.footprint_bytes is None
        else f"footprint {format_gb(top.footprint_bytes)} GB"
    )
    carried = sum(item.footprint_bytes is not None for item in row.observed)
    total = len(row.observed)
    if carried == total:
        tally = f"{'the one observation carries' if total == 1 else f'all {total} observations carry'} a footprint line"
    else:
        verb = "carries" if carried == 1 else "carry"
        tally = f"{carried} of {_plural(total, 'observation')} {verb} a footprint line, and the rest read their resident line alone"
    return f"  lines     : max {format_gb(top.peak_bytes)} GB is the higher of {resident} and {footprint}; {tally}"


def _sources_line(row: UnitRow) -> str | None:
    """Return the line naming what measured this unit. The unit's `step_caveat` is appended only when a step peak contributed, because it explains why that one step's reading counts for this unit."""
    kinds = {item.source for item in row.observed}
    parts: list[str] = []
    if "pool" in kinds:
        parts.append("per-worker peaks from this unit's own pool records")
    step_names = sorted(name.removeprefix("step:") for name in kinds if name.startswith("step:"))
    if step_names:
        parts.append(f"{', '.join(step_names)} step peaks")
    if not parts:
        return None
    line = f"  sources   : {'; '.join(parts)}"
    return f"{line} — {row.unit.step_caveat}" if step_names and row.unit.step_caveat else line


def _width_clause(unit: Unit, constant_bytes: int, *, total_bytes: int, cores: int, root: Path) -> str:
    """Return the width this constant implies on the given machine, computed the way the code that sizes that pool computes it.

    The font suite's pool takes the cores without dividing by its constant, so the clause prints the cores a hand run takes, the gate lane's share a cycle whose build lane runs gives gate:make-test's pool (`memory_budget.split_cores`), and, beside them, the width memory would allow. The kernel's table build runs every settlement configuration at once when its whole booking fits (`DEFAULT_MEMO_BYTES`, `PARKED_FOLD_BYTES` for each settlement configuration, `DELTA_SLOT_BYTES` for each delta and `FOLD_PREPARATION_BYTES`, or the rendezvous, `READ_BACK_FOLD_BYTES` for each settlement configuration, where that is higher, as at every width), and otherwise divides by `DELTA_SLOT_BYTES` after subtracting `DEFAULT_MEMO_BYTES` and `PARKED_FOLD_BYTES` for each settlement configuration, capped at the delta count, the arithmetic `kernel_exec.kernel_threads_default` does; `run_build._table_build_threads` then caps it at the cores, which the clause states in words. The clause then says how many of the heaviest deltas the build starts from scratch beside `default` on top of that width's booking, each booked at `SCRATCH_PEAK_BYTES` less its parked product in place of a delta's slot, with the start booked at a `SCRATCH_PEAK_BYTES` for each of them and `UNCAPPED_SCRATCH_PEAK_BYTES` for `default` while a delta is left to read its memo, and the whole at no less than the rendezvous: the largest count whose booking fits, which can be every delta where one fewer does not fit (`kernel_exec.deltas_from_scratch`). The conformance sweep divides by its own constant and prints two widths: with the build lane idle, capped at the cores, and beside the corpus build, CORPUS_BUILD_BYTES co-resident, capped at the cores less the build's process but never below the acceptance-configuration count (or the cores, where there are fewer), as `artifact_cycle._conform_build_lane` books it and `artifact_cycle._conform_core_cap` caps it on a pass that skips gate:make-test. The string replay and the deep replay divide by their per-walk constants: the string replay capped at the settlement configuration count and the cores, as `run_build._replay_threads` caps it, and the deep replay, whose units are each one configuration over the texts ending in one symbol, at the cores (`deep_replay.replay_threads`). The land row names what the build-lane step before it holds, the corpus build, which it has to stay below, and the corpus-build row names the sweep that books it. None of these widths subtracts gate:make-test's pool; the conform-sweep and replay-walk clauses say where a cycle subtracts it: under the overlap policy, on a pass that also runs run_build or the corpus build, before sizing both of the sweep's widths. Caps and sibling constants are read from `root`, like the constants, so a test can supply both the machine and the tree.
    """
    if unit.name == "font-suite":
        allowed = memory_budget.describe_fit(constant_bytes, total_bytes=total_bytes)
        gate_lane = memory_budget.split_cores(cores)[1]
        return f"the font suite takes the cores this process may run on ({cores}) by hand and the gate lane's share ({gate_lane}) under a cycle whose build lane runs, not the division; memory would allow {allowed}"
    if unit.name == "kernel-build":
        delta = _int_constant(root / KERNEL_SOURCE, KERNEL_DELTA_NAME)
        memo = _int_constant(root / KERNEL_SOURCE, KERNEL_MEMO_NAME)
        parked = _int_constant(root / KERNEL_SOURCE, KERNEL_PARKED_NAME)
        preparation = _int_constant(root / KERNEL_SOURCE, KERNEL_PREPARATION_NAME)
        scratch = _int_constant(root / KERNEL_SOURCE, KERNEL_SCRATCH_NAME)
        uncapped = _int_constant(root / KERNEL_SOURCE, KERNEL_UNCAPPED_NAME)
        read_back = _int_constant(root / KERNEL_SOURCE, KERNEL_READ_BACK_NAME)
        configs = _settlement_config_count(root / CONFORM_SOURCE)
        rendezvous = configs * read_back
        wave = memo + configs * parked + (configs - 1) * delta + preparation
        whole = max(wave, rendezvous)
        usable = total_bytes - memory_budget.os_reserve_bytes(total_bytes=total_bytes)
        if whole <= usable:
            width = configs
            runs = f"all {configs} settlement configurations at once, booked at {format_gb(whole)} GB of the {format_gb(usable)} GB the reserve leaves of {format_gb(total_bytes)} GB ({KERNEL_MEMO_NAME} for default's memo, {KERNEL_PARKED_NAME} for each configuration's parked fold product, {KERNEL_DELTA_NAME} for each delta and {KERNEL_PREPARATION_NAME} for default's fold preparation, or {KERNEL_READ_BACK_NAME} for each configuration's product read back at the rendezvous where that is higher)"
        else:
            width = memory_budget.how_many_fit(
                delta, coresident_bytes=memo + configs * parked, cap=configs - 1, total_bytes=total_bytes
            )
            wave = memo + configs * parked + width * delta
            fit = memory_budget.describe_fit(
                delta, coresident_bytes=memo + configs * parked, cap=configs - 1, total_bytes=total_bytes
            )
            runs = f"{fit} ({KERNEL_DELTA_NAME} divided in, {KERNEL_MEMO_NAME} for default's memo and {KERNEL_PARKED_NAME} for each of the {configs} settlement configurations' parked fold products taken off first, the whole wave's {format_gb(whole)} GB booking not fitting)"

        def started(count: int) -> int:
            lead = scratch if count == configs - 1 else uncapped
            return max(wave + count * (scratch - parked - delta), lead + count * scratch, rendezvous)

        count = max(
            (taken for taken in range(1, min(configs - 1, width - 1) + 1) if started(taken) <= usable),
            default=0,
        )
        start = (
            f"it starts at most {count} of the heaviest deltas from scratch beside default, in whole tiers, booked at {format_gb(started(count))} GB"
            if count
            else "it starts no delta from scratch beside default"
        )
        return f"the whole table build is watched here and divides nothing; its delta wave runs {runs}, which run_build.build_tables then caps at the cores this process may run on; every width is booked at no less than the rendezvous, {format_gb(rendezvous)} GB ({KERNEL_READ_BACK_NAME} for each configuration's product read back); with {KERNEL_SCRATCH_NAME} less a parked product in place of a delta's slot for each delta started from scratch beside default, and at least one {KERNEL_SCRATCH_NAME} for each of them together with {KERNEL_UNCAPPED_NAME} for default while a delta is left to read its memo, or one more {KERNEL_SCRATCH_NAME} once none is, {start}"
    if unit.name == "replay-walk":
        configs = _settlement_config_count(root / CONFORM_SOURCE)
        fit = memory_budget.describe_fit(constant_bytes, cap=min(configs, cores), total_bytes=total_bytes)
        return f"a bare run_build walks {fit}, nothing co-resident since the table build's process has exited, each walk of a full replay beside the absorb worker that reads its window memo, which the constant books with it, and a cycle's replay the same, since gate:make-test starts after run_build (`artifact_cycle.replay_threads_budget`)"
    if unit.name == "deep-replay-walk":
        fit = memory_budget.describe_fit(constant_bytes, cap=cores, total_bytes=total_bytes)
        return f"`make replay-deep` walks {fit}, one unit a crate process, each one settlement configuration over the texts ending in one symbol (`deep_replay.replay_threads`)"
    if unit.name == "corpus-build":
        return "no width divides by it; the conformance sweep takes it off the machine's memory before it divides, as what the corpus build beside it holds (`artifact_cycle._conform_build_lane`)"
    if unit.name == "land":
        build = _int_constant(root / CORPUS_SOURCE, CORPUS_BUILD_NAME)
        stated = f"{CORPUS_BUILD_NAME} ({format_gb(build)} GB)"
        if constant_bytes < build:
            return f"no width divides by it or subtracts it; it is below {stated}, what the build-lane step before it holds, so the conformance sweep's reservation for that step covers it"
        return f"no width divides by it or subtracts it, and it is not below {stated}, so the conformance sweep's reservation for the build lane (`artifact_cycle._conform_build_lane`) no longer covers it"
    if unit.name == "conform-sweep":
        floor = min(_acceptance_config_count(root / CONFORM_SOURCE), cores)
        build = _int_constant(root / CORPUS_SOURCE, CORPUS_BUILD_NAME)
        idle = memory_budget.describe_fit(constant_bytes, cap=cores, total_bytes=total_bytes)
        beside = memory_budget.describe_fit(
            constant_bytes, coresident_bytes=build, cap=max(floor, cores - 1), total_bytes=total_bytes
        )
        return f"the conformance sweep runs {idle} with the build lane idle, and {beside} beside the corpus build's one process ({CORPUS_BUILD_NAME}); under the overlap policy, on a pass that also runs run_build or the corpus build, gate:make-test's pool is subtracted from the machine's memory before both divisions and its workers come off both caps, neither of which drops below the acceptance-configuration count or the cores, whichever is fewer"
    return memory_budget.describe_fit(constant_bytes, total_bytes=total_bytes)


HEADROOM_USED = "headroom used, not yet an overrun"


def _proposal_lines(
    row: UnitRow,
    *,
    host: str | None,
    total_bytes: int,
    cores: int,
    root: Path,
    machines: Mapping[str, tuple[int, int]],
) -> list[str]:
    """Return the row's proposal: one `proposal` line with the raised value, how the rule reached it, and the width it gives here, then under `--host all` one line per host in the window with the width it gives there. A row without a proposal prints nothing, except an overrun of the kernel-build row, which proposes no value and says what to re-measure instead."""
    unit = row.unit
    if row.proposal is None or unit.headroom is None or unit.quantum is None:
        if row.overrun and unit.name == "kernel-build":
            return [
                f"  proposal  : no value — this row reads the whole table build, which {unit.constant} bounds at {KERNEL_MEMO_NAME}, plus {KERNEL_PARKED_NAME} per configuration, plus {KERNEL_DELTA_NAME} per delta, plus {KERNEL_PREPARATION_NAME}, with {KERNEL_SCRATCH_NAME} less a parked product in place of the slot of each delta started from scratch beside default and {KERNEL_UNCAPPED_NAME} for default in their start while a delta is left to read its memo, or at {KERNEL_READ_BACK_NAME} per configuration for the rendezvous where that is higher, and no step reading sets any of those; re-measure {KERNEL_DELTA_NAME}, {KERNEL_PREPARATION_NAME}, {KERNEL_SCRATCH_NAME}, {KERNEL_UNCAPPED_NAME}, {KERNEL_MEMO_NAME}, {KERNEL_PARKED_NAME} and {KERNEL_READ_BACK_NAME} together by the recipe in {KERNEL_DELTA_NAME}'s comment in {KERNEL_SOURCE}, and {unit.constant} from them"
            ]
        return []
    value = format_gb(row.proposal)
    peak = format_gb(max(item.peak_bytes for item in row.observed))
    factor = "" if unit.headroom == 1 else f" × {unit.headroom:g}"
    state = "" if row.overrun else f", {HEADROOM_USED}"
    here = _width_clause(unit, row.proposal, total_bytes=total_bytes, cores=cores, root=root)
    lines = [
        f"  proposal  : {unit.constant} at {value} GB (max {peak} GB{factor}, rounded up to a multiple of {format_gb(unit.quantum)} GB){state}; width here at {value} GB: {here}"
    ]
    if host is None:
        for name in sorted({item.host for item in row.observed}):
            machine = machines.get(name)
            if machine is None:
                lines.append(f"  on {name}: no run record from this host states its cores and memory")
                continue
            host_cores, host_bytes = machine
            there = _width_clause(unit, row.proposal, total_bytes=host_bytes, cores=host_cores, root=root)
            lines.append(
                f"  on {name} ({host_cores} cores, {format_gb(host_bytes)} GB) at {value} GB: {there}"
            )
    return lines


def render_rows(
    rows: list[UnitRow],
    *,
    host: str | None,
    total_bytes: int,
    cores: int,
    root: Path = ROOT,
    machines: Mapping[str, tuple[int, int]] | None = None,
) -> list[str]:
    """Return the report lines: one block per unit in `UNITS` order, then a summary line. The machine's memory and cores are parameters so a test can render the report for an invented machine, and `root` is the tree the width clauses read caps and sibling constants from. `machines` maps each host to the cores and memory its run records state (`host_machines`), read only under `--host all` (`host` None) for each proposal's per-host widths."""
    lines: list[str] = []
    for row in rows:
        unit = row.unit
        lines.append("")
        if row.constant_bytes is None:
            lines.append(f"{unit.name}  (no constant — deliberately unmeasured)")
            lines.append(f"  policy    : {unit.note}")
        else:
            lines.append(f"{unit.name}  ({unit.constant} in {unit.source})")
            lines.append(f"  constant  : {format_gb(row.constant_bytes)} GB")
        lines.append(_observed_line(row, host=host))
        both = _lines_line(row)
        if both is not None:
            lines.append(both)
        since = _since_line(row)
        if since is not None:
            lines.append(since)
        if row.dropped_other_hosts:
            verb = "was" if row.dropped_other_hosts == 1 else "were"
            lines.append(
                f"  others    : {_plural(row.dropped_other_hosts, 'record')} from other hosts {verb} not checked — pass --host all to include them"
            )
        if row.overrun and row.constant_bytes is not None:
            peak = max(item.peak_bytes for item in row.observed)
            over = _percent(peak - row.constant_bytes, row.constant_bytes)
            # A small overrun rounds to 0%, and "exceeds it by 0%" would contradict the exit code.
            margin = f"by {over}%" if over else "by less than 1%"
            lines.append(
                f"  OVERRUN   : max {format_gb(peak)} GB exceeds the constant of {format_gb(row.constant_bytes)} GB {margin}"
                f" — re-measure {unit.constant} in {unit.source}; committing that constant is the acceptance."
            )
        lines.extend(
            _proposal_lines(
                row, host=host, total_bytes=total_bytes, cores=cores, root=root, machines=machines or {}
            )
        )
        if row.unverified_here and row.dropped_older:
            lines.append(
                "  UNVERIFIED HERE: no record from this host for this unit has been measured since the commit that set the constant,"
                " so its current value is unchecked on this machine until the next pass that runs this unit."
            )
        elif row.unverified_here:
            lines.append(
                "  UNVERIFIED HERE: no rows from this host for this unit, so the constant's headroom is unchecked on this machine."
                " The journal records which machine measured a peak, never which machine a constant was sized on."
            )
        sources = _sources_line(row)
        if sources is not None:
            lines.append(sources)
        if row.constant_bytes is not None:
            lines.append(
                f"  width here: {_width_clause(unit, row.constant_bytes, total_bytes=total_bytes, cores=cores, root=root)}"
            )
            lines.append(f"  note      : {unit.note}")
    overruns = [row for row in rows if row.overrun]
    lines.append("")
    if overruns:
        lines.append(f"job costs: OVERRUN — {len(overruns)} unit(s) outrun their constants")
    elif not any(row.observed for row in rows):
        where = f"on {host}" if host else "in this journal"
        lines.append(f"job costs: green — nothing measured {where} yet")
    else:
        used = sum(row.proposal is not None for row in rows)
        tail = ""
        if used == 1:
            tail = ", though one has used its headroom (see its proposal line)"
        elif used:
            tail = f", though {used} have used their headroom (see their proposal lines)"
        lines.append(f"job costs: green — every measured unit fits its checked-in constant{tail}")
    return lines


def _report(args: argparse.Namespace, constant_commit_times: Mapping[str, str] | None) -> int:
    """Read the journal, build the rows, print them, and return the exit code. It is separate from `main` so that `main` can catch any exception raised here, such as a renamed constant or a source file that does not parse, and exit 2: an uncaught exception exits 1, which callers read as an overrun."""
    host = None if args.host == "all" else args.host
    pool_records = load_pool_records(args.journal)
    runs, steps_by_run, _ = load_journal(args.journal)
    rows = build_rows(
        pool_records,
        steps_by_run,
        constants=read_constants(),
        host=host,
        recent=args.recent,
        tolerance=args.tolerance,
        constant_commit_times=(
            read_constant_commit_times() if constant_commit_times is None else constant_commit_times
        ),
    )
    scope = f"host {host}" if host else "every host"
    unbounded = ", ".join(unit.name for unit in UNITS if not unit.recency_bound)
    window = (
        f"most recent {args.recent} records per unit since each constant's commit"
        + (f" ({unbounded}: every record since it)" if unbounded else "")
        if args.recent > 0
        else "every record"
    )
    print(f"{args.journal} — {scope}, {window}, tolerance {_percent(args.tolerance, 1)}%")
    print(
        "\n".join(
            render_rows(
                rows,
                host=host,
                total_bytes=memory_budget.total_memory_bytes(),
                cores=memory_budget.usable_cores(),
                machines=host_machines(runs),
            )
        )
    )
    return 1 if args.check and any(row.overrun for row in rows) else 0


def main(argv: list[str] | None = None, *, constant_commit_times: Mapping[str, str] | None = None) -> int:
    """Print the report and return 0 for a report or a passing check, 1 when `--check` finds an overrun, and 2 when the tool fails. `--moved` prints only the moved constants instead of the report. `constant_commit_times` replaces the commit times read from git, so a test can supply them or supply none.

    `_constant_assignment` raises when a constant has been renamed, so that the constant does not go unchecked. An uncaught exception would exit 1, and the artifact cycle reads 1 as an overrun: it writes OVERRUN into the cycle summary, pointing a reader at a re-measure nothing asked for. So `main` catches every failure and returns 2, which the cycle reports as informational.
    """
    parser = argparse.ArgumentParser(
        description="Hold the checked-in per-unit memory peaks against what this machine measured, and state the width each one implies here."
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=JOURNAL,
        help="timing journal to read (default: engine/out/cycle-timings.ndjson; a journal that does not exist reads as nothing measured yet, which is not a failure)",
    )
    parser.add_argument(
        "--host",
        default=socket.gethostname(),
        help='which machine\'s measurements to check (default: this host); the literal "all" reads every host in the journal, which is for surveying a fleet rather than for deciding whether this machine is in trouble',
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=20,
        help="how many of the most recent source records to keep per unit and host, counted from the commit that set each constant to its current value — older records are never held against it (default: 20 — long enough that one anomalous run cannot hide a regression by itself, short enough that an old high peak stops counting once that many newer records replace it). 0 reads every record regardless of that commit, for a deliberate full-history run.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="fraction of its constant an observation may exceed before it counts as an overrun (default: 0.0). These constants are already headroom — each rounds up past the top of its measured range because erring low puts a machine into swap — so a peak that reaches one has eaten all the deliberate slack, and that is the news. The knob is for a fleet survey, not for softening the default.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when an observed peak outruns its constant; a unit with no observations and a unit with no constant are informational and never fail, and a failure of the check itself exits 2 so a caller can tell a check result from a crash",
    )
    parser.add_argument(
        "--moved",
        action="store_true",
        help="print only the checked constants whose working-tree value differs from HEAD's, one per line, and exit 0 (2 when git cannot show a constant's file at HEAD); the artifact cycle runs this after a tripped check to tell whether a constant has already been re-measured",
    )
    args = parser.parse_args(argv)
    try:
        if args.moved:
            for line in moved_lines(moved_constants()):
                print(line)
            return 0
        return _report(args, constant_commit_times)
    except Exception as exc:
        print(f"job costs: check FAILED — {exc!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
