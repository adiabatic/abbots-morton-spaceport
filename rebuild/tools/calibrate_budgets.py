"""Check the checked-in per-unit memory peaks against what this machine measured (`make job-costs`).

Several fan-out widths are the machine's memory divided by a measured per-unit peak, such as what one pytest worker or one kernel configuration holds. Each peak is a checked-in constant, and `UNITS` lists the ones this module checks. A memory saving or a heavier fixture can move a real peak away from its constant without failing any test. A constant that is too low shows up as a machine in swap, and one that is too high holds a pool to a fraction of the width it has room for. The measurements that catch this are already recorded on every run: each xdist controller's per-worker peaks, and the peak RSS the cycle records for every step it spawns. This module compares those measurements with the constants. It builds nothing and imports none of the code whose constants it checks.

It reads measurements only from the cycle-timings journal. A `kind:"pool"` record gives one observation per worker, because the unit is one worker. A named `kind:"step"` record gives its `peak_rss_bytes`, which is the largest single process in the step's tree, because `peak_rss.reap_peak_rss_bytes` takes the max over the tree instead of the sum. That reading measures one unit only for some steps. For `run_m1` the widest process is the table-build child, which the kernel-build row checks, and for `surface-build` it is the parent, which the surface-parent row checks. It does not measure one unit for `gate:make-test`, whose tree also holds `make all` and `uv run pyright` beside the pool. Each `UNITS` entry states which sources count for it and why.

Constants are read from their source files with `ast`, never imported. pytest loads every conftest under the module name `conftest`, so from under `rebuild/` a plain `import conftest` gets the wrong file, and `import rebuild.conftest` would execute a second copy of a file pytest has already loaded and installed its lane-audit hook from. `ast` executes nothing, and it keeps this tool from importing pytest or inheriting that file's `sys.path` edits. The width clauses read their other inputs the same way, so each prints the width its pool actually takes: the surface rows read the jobs cap and each other's constant, and the conform-belt row reads its cap, the acceptance-configuration count, from the lengths of the configuration tuples in `rebuild/pipeline/conform.py` (`_acceptance_config_count`), along with the surface constants its second width needs. The kernel row's width is narrowed by the configuration count and the cores in `run_m1._table_build_threads`, which this module does not compute, so that clause prints the memory arithmetic and names the narrowing in words.

A peak above its constant means the constant is out of date. It does not mean an artifact is wrong: the cost is a pool of the wrong width, so the cycle does not fail on it. `--check` exits 1 for an overrun and 2 when the tool itself fails, because the artifact cycle prints a diff of the constants' files on 1 and an informational line on any other nonzero code, and a crash reported as an overrun would report a measurement nobody took. The fix is to re-seed the constant from the newer measurement; committing it accepts the new value, as committing `rebuild/review-census-pins.json` accepts the census. The tolerance defaults to zero because each constant is already rounded up above its measured peaks, as its comment says: an estimate that is too low puts the machine into swap, while one that is too high only narrows a pool. A peak that reaches the constant has used all of that headroom. `--tolerance` is for a survey with `--host all`, not for relaxing the default.

Observations are filtered to this host by default, because a per-unit peak is a property of one machine's working set, and a journal concatenated from several machines mixes machines running different versions of the code. Records that finished before the commit that set a constant's current value are set aside before anything is counted, so that after a constant is re-seeded downward, the older, higher peaks from the same host do not trip the check. `git blame` on the constant's line finds that commit, so a re-seed clears its row on the next pass. A constant that is edited but not yet committed has no such commit and keeps every record until it is committed. Within that bound, `--recent` keeps only the newest records, so that one anomalous run cannot hide a regression and a real improvement shows within a day's work. The journal records which machine measured a peak but not which machine a constant was sized on, so a unit with no rows from this host is reported as unverified on this host.
"""

from __future__ import annotations

import argparse
import ast
import functools
import socket
import subprocess
import statistics
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rebuild.tools import memory_budget
from rebuild.tools.cycle_timings import JOURNAL, load_journal, load_pool_records
from rebuild.tools.peak_rss import format_gb

ROOT = Path(__file__).resolve().parents[2]

SURFACE_SOURCE = "rebuild/tools/artifact_cycle.py"
SURFACE_CAP_NAME = "SURFACE_JOBS_CAP"
SURFACE_PARENT_NAME = "SURFACE_PARENT_BYTES"
SURFACE_WORKER_NAME = "SURFACE_WORKER_BYTES"
STANDING_FILL_PARENT_NAME = "STANDING_FILL_PARENT_BYTES"
STANDING_FILL_WORKER_NAME = "STANDING_FILL_WORKER_BYTES"
ORACLE_SHARD_NAME = "ORACLE_SHARD_BYTES"
CONFORM_BELT_NAME = "CONFORM_BELT_BYTES"
CONFORM_SOURCE = "rebuild/pipeline/conform.py"


@dataclass(frozen=True)
class Unit:
    """One unit that a fan-out width divides the machine's memory by: its constant and the file that holds it (both None for a row that is reported without a constant), the journal records that measure it, and the text printed with its figures."""

    name: str
    constant: str | None
    source: str | None
    pool_units: tuple[str, ...]
    step_names: tuple[str, ...]
    step_caveat: str
    note: str


# The kernel's constants live in one file: DELTA_PEAK_BYTES divides the delta fan-out width, DEFAULT_MEMO_BYTES is subtracted from the machine's memory before that division, and the kernel-build row checks the run_m1 step peak against TABLE_BUILD_PEAK_BYTES.
KERNEL_SOURCE = "rebuild/pipeline/kernel_exec.py"
KERNEL_DELTA_NAME = "DELTA_PEAK_BYTES"
KERNEL_MEMO_NAME = "DEFAULT_MEMO_BYTES"

UNITS: tuple[Unit, ...] = (
    Unit(
        name="font-suite",
        constant="FONT_SUITE_WORKER_BYTES",
        source="conftest.py",
        pool_units=("font-suite",),
        step_names=(),
        step_caveat="",
        note="Only the controller's own per-worker figures measure this unit, and the exclusion of gate:make-test's step peak is deliberate: that step's peak is the widest single process in the tree under `make test`, and that tree holds `make all` and `uv run pyright` — spawned from pytest_configure, beside the pool rather than in it — each of which dwarfs a worker this small. Admitting it as an observation would report a build's footprint as a worker's and trip this check on its first pass.",
    ),
    Unit(
        name="rebuild-contracts",
        constant=None,
        source=None,
        pool_units=("rebuild-contracts",),
        step_names=(),
        step_caveat="",
        note="The rebuild suite's width is a count of cores — a hand run takes every core this process may actually run on, and the cycle hands it the cores the surface build's parent and pool leave (`artifact_cycle.contracts_pool_width`) — and nothing divides the box by a per-worker cost to reach either: no test in it reads a live build artifact, so no worker holds a working set worth bounding, and there is nothing here to calibrate. The observations are collected and reported anyway, so that if the suite ever grows a memory-derived width the figure to seed it with is already on the record rather than a measurement someone still has to go and take.",
    ),
    Unit(
        name="kernel-build",
        constant="TABLE_BUILD_PEAK_BYTES",
        source=KERNEL_SOURCE,
        pool_units=(),
        step_names=("run_m1",),
        step_caveat="run_m1's peak is the widest single process in its tree, the max over its children, and on both fleet boxes that is the one build-tables child holding every settlement configuration — default's retained memo beside every seat in flight, default's own fold at one of them — so the step peak reads the whole table build at whatever width the cycle handed it, never one configuration. The other candidate is the string replay's child, which seats every settlement configuration in one wave (REPLAY_PEAK_BYTES apiece, `--replay-threads`) and peaks at about a third of the build on the shipped alphabet (6.10 GB maxrss for the five-configuration wave under `/usr/bin/time -l`, its footprint level with it, against this row's 19.50 GB maximum for the table build, both maxrss readings on the 18-core M5 Pro 48 GiB MacBook Pro); should a replay ever outrun the build, this row reads the replay, and the figure to re-seed is then that constant rather than this one. DELTA_PEAK_BYTES, the per-delta figure the width is divided out of, and DEFAULT_MEMO_BYTES, the memo taken off the box first, are not measured by any step here: their reading is the direct whole-wave measurement under --cache-census, and the bound they state is that this unit's peak stays under the memo plus one delta per seat of the width.",
        note="The direct measurement is one build-tables over every settlement configuration under /usr/bin/time -l with --cache-census, which is what to reach for before re-seeding either constant; this row is the cheap standing watch beside it rather than a replacement for it.",
    ),
    Unit(
        name="surface-parent",
        constant="SURFACE_PARENT_BYTES",
        source=SURFACE_SOURCE,
        pool_units=(),
        step_names=("surface-build",),
        step_caveat="reap_peak_rss_bytes maxes over the child's whole tree rather than summing it, and under this step that tree is one parent holding the whole corpus beside workers each holding one batch of it, so the max reads the parent — which is this unit exactly. What the same reading cannot see is the sum: parent plus every worker is the build's real footprint, and no step peak has ever been able to report it, which is why the divisor beside this row is measured by the surface pool records instead of here.",
        note="A row whose constant is subtracted from the box rather than divided into it: the parent's pile moves with the width only through the one batch reply in flight per worker, so it is surface_job_budget's co-resident term. Phase 2 streams into the shards, so the pile is the workload table, the packed unit store, the checker's identity dict and the pre-merge snapshot rather than every fragment, but it is still corpus-shaped — every migrated letter moves it — so expect to re-seed it per batch. The constant's comment in rebuild/tools/artifact_cycle.py argues which phase holds the step's peak and which readings seeded it: the load boundary makes the mark on a full-fresh pass and on a served one alike, the row columns and both ink-signature tables standing beside the workload table there, and the served plan folds each store record as it is parsed, so the two kinds of pass read within a few hundredths of a gigabyte of each other. A cycle-driven pass of either kind files a row here, a hand build files pool records alone and its step peak is read off its `[t] review.build` lines, and the figure to re-seed from is whichever of a full-fresh and a served pass reads higher on a pair taken with nothing edited between them.",
    ),
    Unit(
        name="surface-worker",
        constant="SURFACE_WORKER_BYTES",
        source=SURFACE_SOURCE,
        pool_units=("surface",),
        step_names=(),
        step_caveat="",
        note="These pool records come from rebuild/review/build.py's own runner rather than from a pytest controller — cycle_timings.record_pool is deliberately not a pytest entry point — and each supplies one observation per worker that answered. The row is legitimately quiet on a box the arithmetic has already narrowed to a single worker, because a serial build starts no pool to measure; a deliberate `--jobs N` hand run is what puts an observation on the record there, and the row's unverified-here line is the honest reading until one does.",
    ),
    Unit(
        name="signature-worker",
        constant=None,
        source=None,
        pool_units=("signature",),
        step_names=(),
        step_caveat="",
        note="The surface build's ink-signature pool is cores-bound rather than memory-bound — `artifact_cycle.signature_job_budget` hands it the box's cores, less gate:make-test's two under a gated cycle, and divides nothing — because a signature worker holds one comparator over the two fonts and a resident set flat in the pile it shapes, so no constant prices it and there is nothing here to calibrate. The observations are collected and reported anyway, one per worker per pooled pass, each the worker's own peak carried home on its last chunk's reply, so that a figure exists if a width ever needs one — the rebuild-contracts row's position. The surface-build step peak is deliberately not admitted: that max reads the parent, which the surface-parent row prices, and it would read a build's footprint as a comparator's.",
    ),
    Unit(
        name="oracle-shard",
        constant=ORACLE_SHARD_NAME,
        source=SURFACE_SOURCE,
        pool_units=("oracle-shard",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `run_m1.run_oracle`'s own fan-in rather than from a pytest controller: one record per oracle fan-out, one observation per row range that ran, each the range's worker's own peak as it reported it home. The run_m1 step peak is deliberately not admitted: that step's widest process is the table build's child, which the kernel-build row prices, and it would read a build's footprint as a shard's. The row is quiet on a box the arithmetic narrows to `--jobs 1`, since the serial oracle starts no pool; a hand `run_m1 --gates-only` at any wider width puts an observation on the record.",
    ),
    Unit(
        name="conform-belt",
        constant=CONFORM_BELT_NAME,
        source=SURFACE_SOURCE,
        pool_units=("conform-belt",),
        step_names=(),
        step_caveat="",
        note="These pool records come from `run_m1.run_font_conformance`'s own fan-in rather than from a pytest controller: one record per pooled belt at `conform.BELT_HORIZON`, one observation per acceptance configuration, each the configuration's worker's own peak as `run_m1._priced_conformance_config` carried it home beside the result. A deeper sweep (`make conform-deep`, or a `--conform-horizon` past the belt's) runs the same pooled arm and files nothing here, since its worker builds a horizon-deep memo in process and is a different pile from the belt's. A reading is the worker process's high-water mark and `run_m1._spawn_pool` sets no `maxtasksperchild`, so below the acceptance-configuration count a configuration that runs second in a reused worker reads at or above the mark the one before it left, and the record prices the pool's shape rather than one configuration's cost. The gate:conform step peak is deliberately not admitted: `reap_peak_rss_bytes` maxes over the child's tree rather than summing it, so that step's peak reads one process and never the pool. The row is quiet on a pass whose gate:conform skips on its green, and at `--jobs 1`, since the serial belt starts no pool; a hand `run_m1 --conform-only` at any wider width puts observations on the record. The row prices `CONFORM_BELT_BYTES`, the divisor `artifact_cycle.conform_job_budget` divides the box by once the build lane's larger step is off it.",
    ),
    Unit(
        name="standing-fill-parent",
        constant=STANDING_FILL_PARENT_NAME,
        source=SURFACE_SOURCE,
        pool_units=(),
        step_names=("plumbing",),
        step_caveat="reap_peak_rss_bytes maxes over the child's whole tree rather than summing it. Under this step the tree includes the verdict chain and any standing-fill refill workers, so the reading is a conservative parent/worker maximum, not an isolated parent measurement. No row prices the workers separately: the fill files no pool record, because the module that would file it is in the memo's code stamp and a cost reading there would drop the memo on every edit to it, so STANDING_FILL_WORKER_BYTES is seeded by hand at the chunk width, as its docstring says.",
        note="This constant is standing_fill_jobs's co-resident parent term, subtracted from the box before dividing by the worker cost. The chain retains every surface id and a human id/echo/notation projection. Normal standing fills stream the human records, retain primed keys, decisions and memo entries, and spool pool misses to temporary gzipped storage; submission holds at most one wave of records, bounded by width times _STANDING_POOL_CHUNK. The complaint docket retains compact grouping projections from a separate stream. The parent grows with ids and decisions without holding the full human corpus, so its budget still needs checking as the alphabet migrates. Every plumbing row reads against this constant, including passes that start no pool; a serial memo-drop pass evaluates the whole domain in the parent and can read higher than a pooled pass. Targeted authoring retains full records only for explicitly requested unit ids; the daemon holds its own resident surface outside this budget.",
    ),
)


@functools.cache
def _constant_assignment(path: Path, name: str) -> tuple[int, int]:
    """Return the integer `path` assigns to `name` at module scope and the line number of that assignment, parsed with `ast` for the reason the module docstring gives. Only module-level assignments count, so a same-named local inside a function is ignored. A missing name raises, so a renamed constant fails the check instead of going unchecked."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return int(ast.literal_eval(node.value)), node.lineno
    raise RuntimeError(
        f"{path} defines no {name}: make job-costs prices a measured peak against that constant, and a constant it cannot find is a width nothing is watching. Move the name in the UNITS registry beside whatever moved it there."
    )


def _int_constant(path: Path, name: str) -> int:
    return _constant_assignment(path, name)[0]


@functools.cache
def _acceptance_config_count(path: Path) -> int:
    """Return the number of acceptance configurations `path` defines: the summed lengths of its module-scope `SETTLEMENT_CONFIGS` and `OVERLAY_CONFIGS` tuples, which `conform.ACCEPTANCE_CONFIGS` concatenates, parsed with `ast` like the constants. It is the belt's width cap beside the cores. A missing name or a value that is not a literal tuple raises, because the report would otherwise state a wrong width."""
    names = ("SETTLEMENT_CONFIGS", "OVERLAY_CONFIGS")
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
                        f"{path} assigns {target.id} something other than a literal tuple: the conform-belt row reads the acceptance-configuration count off that tuple's length to state the belt's cap."
                    )
                lengths[target.id] = len(value)
    missing = [name for name in names if name not in lengths]
    if missing:
        raise RuntimeError(
            f"{path} defines no {' or '.join(missing)}: the conform-belt row reads the acceptance-configuration count off those tuples to state the belt's cap. Point `CONFORM_SOURCE` at the file that defines them, or update this reader beside whatever moved them."
        )
    return sum(lengths.values())


def constant_seeded_at(path: Path, name: str, *, root: Path = ROOT) -> str | None:
    """Return the committer time of the commit that last changed `name`'s assignment line, as an ISO-Z stamp like the journal's `finished_at` fields, or None when there is no such commit: the line is uncommitted, the file is untracked, `root` is not a git checkout, or git is not installed. None means no bound, which only keeps older records in the count.

    `git blame` returns the last commit that touched the line for any reason, including a reformat, so the bound can be later than the re-seed. That is the safe direction: a bound that is too new sets aside records that were valid evidence, and the row reports how many, while a bound that is too old would check a new constant against peaks from code that no longer exists. The committer time is used instead of the author time because a record measured before the commit was made is a measurement of the code the new value was taken from, not of the code it was accepted on.
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
    return _seed_stamp_from_blame(blame.stdout)


def _seed_stamp_from_blame(porcelain: str) -> str | None:
    """Return the committer time from one line's porcelain blame, or None for an uncommitted line. Porcelain marks an uncommitted line with the all-zero hash, and its committer time is the time of the blame."""
    lines = porcelain.splitlines()
    if not lines or lines[0].startswith("0" * 40):
        return None
    for line in lines:
        if line.startswith("committer-time "):
            seconds = int(line.split()[1])
            return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def read_seed_stamps(root: Path = ROOT) -> dict[str, str]:
    """Map each unit name to the stamp of the commit that last set its constant, for every unit whose constant is committed. `build_rows` keeps every record for a unit missing here."""
    stamps: dict[str, str] = {}
    for unit in UNITS:
        if unit.constant is None or unit.source is None:
            continue
        stamp = constant_seeded_at(root / unit.source, unit.constant, root=root)
        if stamp is not None:
            stamps[unit.name] = stamp
    return stamps


def read_constants(root: Path = ROOT) -> dict[str, int]:
    """Map each unit name to its checked-in peak, for every unit that has one. `root` lets a test point it at a copied tree."""
    return {
        unit.name: _int_constant(root / unit.source, unit.constant)
        for unit in UNITS
        if unit.constant is not None and unit.source is not None
    }


@dataclass(frozen=True)
class Observation:
    """One peak measurement of one unit, with the host that measured it, the record's `finished_at`, and the record kind (`pool` or `step:<name>`)."""

    peak_bytes: int
    host: str
    at: str
    source: str


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
    """Return the observations of one unit that pass the host filter, the seed bound, and the recency bound, with the number of records the host filter dropped and the number the seed bound set aside.

    `since` is the ISO-Z stamp of the commit that set the constant's current value. A record that finished before it is set aside before the recency bound applies, so the bound counts only records measured on the code the constant describes. A record with no stamp is kept. None means no bound.

    Each worker peak in a kept pool record is a separate observation, because the unit is one worker: the median is a typical worker, and the max is the worst worker seen, which is the figure a constant has to cover. The controller's own peak is in the record but not counted, because it measures a different process and would pull the median toward a figure no worker held.

    The recency bound keeps the newest `recent` source records per host (each pool record or step record counts once, not once per worker), ordered by `finished_at` with read order breaking ties; ISO-Z stamps sort correctly as strings. `recent <= 0` keeps everything. The bound keeps one anomalous run from counting for as long as the journal lasts. It applies per host even when no host is selected, so under `--host all` a machine that runs many cycles a day cannot fill the window and leave a quieter machine unchecked. The dropped count is returned so the report can say that records from other hosts exist and were not checked.
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
    if recent > 0:
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
            if isinstance(peak, int | float):
                observed.append(Observation(int(peak), record_host, at, source))
    return observed, dropped, older


@dataclass(frozen=True)
class UnitRow:
    """One unit's result, computed before anything is rendered: the constant, the observations that passed the filters, what the filters set aside, whether a peak exceeds the constant, and whether this host has no observations of a unit that has a constant."""

    unit: Unit
    constant_bytes: int | None
    observed: list[Observation]
    dropped_other_hosts: int
    seeded_at: str | None
    dropped_older: int
    overrun: bool
    unverified_here: bool


def build_rows(
    pool_records: list[dict],
    steps_by_run: dict[str, list[dict]],
    *,
    constants: dict[str, int],
    host: str | None,
    recent: int,
    tolerance: float,
    seeded_at: Mapping[str, str] | None = None,
) -> list[UnitRow]:
    """Return one row per unit, in `UNITS` order. The function reads nothing itself: the constants and the seed stamps are passed in and the machine is not consulted, so tests can assert on it directly. `seeded_at` maps a unit name to the ISO-Z stamp its records must follow; a unit it does not name keeps every record, and `recent <= 0` ignores the stamps, because that caller asked for every record. An overrun is a peak strictly greater than the constant times `1 + tolerance`, so a peak equal to the constant fits.

    `unverified_here` is set only when a host was named. Under `--host all` there is no single host, and the observed line already says that nothing was measured.
    """
    rows: list[UnitRow] = []
    for unit in UNITS:
        since = (seeded_at or {}).get(unit.name) if recent > 0 else None
        observed, dropped, older = observations(
            unit, pool_records, steps_by_run, host=host, recent=recent, since=since
        )
        constant_bytes = constants.get(unit.name)
        overrun = (
            constant_bytes is not None
            and bool(observed)
            and max(item.peak_bytes for item in observed) > constant_bytes * (1 + tolerance)
        )
        rows.append(
            UnitRow(
                unit=unit,
                constant_bytes=constant_bytes,
                observed=observed,
                dropped_other_hosts=dropped,
                seeded_at=since,
                dropped_older=older,
                overrun=overrun,
                unverified_here=constant_bytes is not None and not observed and host is not None,
            )
        )
    return rows


def _record_count(observed: list[Observation]) -> int:
    """Return how many journal records a unit's observations came from, which tells a reader whether a max comes from one run or many. Records are identified by host, stamp, and source, so two pools of one unit that finished in the same second on one host count as one. The undercount affects only this printed figure."""
    return len({(item.host, item.at, item.source) for item in observed})


def _percent(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def _plural(count: int, noun: str) -> str:
    """Return the count and the noun, plural unless the count is 1."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _since_line(row: UnitRow) -> str | None:
    """Return the line that says which records count for this unit's constant: the seed commit's stamp and how many older records were set aside, or that the constant is uncommitted and every record counts. None for a unit without a constant."""
    if row.constant_bytes is None:
        return None
    if row.seeded_at is None:
        return f"  since     : {row.unit.constant} has no commit yet (edited, untracked, or no git here), so every record stands"
    aside = f"; {_plural(row.dropped_older, 'older record')} set aside" if row.dropped_older else ""
    return (
        f"  since     : {row.seeded_at}, the commit that set {row.unit.constant} to its current value{aside}"
    )


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

    The font suite's pool takes the cores without dividing by its constant, so the clause prints the cores and, beside them, the width memory would allow. The kernel's delta wave divides by `DELTA_PEAK_BYTES` after subtracting `DEFAULT_MEMO_BYTES`, and `run_m1._table_build_threads` then caps it at the configuration count and the cores, which the clause states in words. The surface build subtracts its parent constant and divides by its worker constant, so neither surface row is the whole width alone and each reads the other's constant. The standing fill's parent is subtracted the same way before dividing by `STANDING_FILL_WORKER_BYTES`. The conform belt divides by its own constant, capped at the acceptance-configuration count and the cores, and prints two widths: with the build lane idle, and beside a surface build at the surface build's width. None of these widths subtracts gate:make-test's pool; the surface-worker and standing-fill clauses say so. Caps and sibling constants are read from `root`, like the constants, so a test can supply both the machine and the tree.
    """
    if unit.name == "font-suite":
        allowed = memory_budget.describe_fit(constant_bytes, total_bytes=total_bytes)
        return f"the font suite takes the cores this process may run on ({cores}), not the division; memory would allow {allowed}"
    if unit.name == "kernel-build":
        delta = _int_constant(root / KERNEL_SOURCE, KERNEL_DELTA_NAME)
        memo = _int_constant(root / KERNEL_SOURCE, KERNEL_MEMO_NAME)
        fit = memory_budget.describe_fit(delta, coresident_bytes=memo, total_bytes=total_bytes)
        return f"the whole table build is watched here and divides nothing; its delta wave runs {fit} ({KERNEL_DELTA_NAME} divided in, {KERNEL_MEMO_NAME} taken off first for default's memo), which run_m1.build_tables then narrows by the configurations there are to answer and the cores there are to answer them with"
    if unit.name == "surface-parent":
        worker = _int_constant(root / SURFACE_SOURCE, SURFACE_WORKER_NAME)
        cap = min(_int_constant(root / SURFACE_SOURCE, SURFACE_CAP_NAME), cores)
        allowed = memory_budget.describe_fit(
            worker, coresident_bytes=constant_bytes, cap=cap, total_bytes=total_bytes
        )
        return f"the surface build's parent is subtracted from the box rather than divided into it; with it off, {allowed}"
    if unit.name == "surface-worker":
        parent = _int_constant(root / SURFACE_SOURCE, SURFACE_PARENT_NAME)
        cap = min(_int_constant(root / SURFACE_SOURCE, SURFACE_CAP_NAME), cores)
        fit = memory_budget.describe_fit(
            constant_bytes, coresident_bytes=parent, cap=cap, total_bytes=total_bytes
        )
        return f"{fit}; under a gated cycle gate:make-test's pool comes off the box before this division too, and two cores off the cap"
    if unit.name == "standing-fill-parent":
        worker = _int_constant(root / SURFACE_SOURCE, STANDING_FILL_WORKER_NAME)
        allowed = memory_budget.describe_fit(
            worker, coresident_bytes=constant_bytes, cap=cores, total_bytes=total_bytes
        )
        return f"the standing fill's chain parent is subtracted from the box rather than divided into it; with it off, the refill pool runs {allowed} ({STANDING_FILL_WORKER_NAME}, seeded by hand); under a gated cycle gate:make-test's pool comes off the box before this division too, and two cores off the cap"
    if unit.name == "conform-belt":
        cap = min(_acceptance_config_count(root / CONFORM_SOURCE), cores)
        parent = _int_constant(root / SURFACE_SOURCE, SURFACE_PARENT_NAME)
        worker = _int_constant(root / SURFACE_SOURCE, SURFACE_WORKER_NAME)
        surface_width = memory_budget.how_many_fit(
            worker,
            coresident_bytes=parent,
            cap=min(_int_constant(root / SURFACE_SOURCE, SURFACE_CAP_NAME), cores),
            total_bytes=total_bytes,
        )
        idle = memory_budget.describe_fit(constant_bytes, cap=cap, total_bytes=total_bytes)
        beside = memory_budget.describe_fit(
            constant_bytes, coresident_bytes=parent + surface_width * worker, cap=cap, total_bytes=total_bytes
        )
        return f"the belt runs {idle} with the build lane idle, and {beside} beside a surface build of its parent and {surface_width} workers"
    return memory_budget.describe_fit(constant_bytes, total_bytes=total_bytes)


def render_rows(
    rows: list[UnitRow], *, host: str | None, total_bytes: int, cores: int, root: Path = ROOT
) -> list[str]:
    """Return the report lines: one block per unit in `UNITS` order, then a summary line. The machine's memory and cores are parameters so a test can render the report for an invented machine, and `root` is the tree the width clauses read caps and sibling constants from."""
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
                f" — re-seed {unit.constant} in {unit.source} off a fresh measurement; committing that constant is the acceptance."
            )
        if row.unverified_here:
            lines.append(
                "  UNVERIFIED HERE: no rows from this host for this unit, so the constant's headroom is unproven on this box."
                " The journal records which box measured a peak, never which box a constant was sized on."
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
        lines.append("job costs: green — every measured unit fits its checked-in constant")
    return lines


def _report(args: argparse.Namespace, seed_stamps: Mapping[str, str] | None) -> int:
    """Read the journal, build the rows, print them, and return the exit code. It is separate from `main` so that `main` can catch any exception raised here, such as a renamed constant or a source file that does not parse, and exit 2: an uncaught exception exits 1, which callers read as an overrun."""
    host = None if args.host == "all" else args.host
    pool_records = load_pool_records(args.journal)
    _, steps_by_run, _ = load_journal(args.journal)
    rows = build_rows(
        pool_records,
        steps_by_run,
        constants=read_constants(),
        host=host,
        recent=args.recent,
        tolerance=args.tolerance,
        seeded_at=read_seed_stamps() if seed_stamps is None else seed_stamps,
    )
    scope = f"host {host}" if host else "every host"
    window = (
        f"most recent {args.recent} records per unit since each constant's commit"
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
            )
        )
    )
    return 1 if args.check and any(row.overrun for row in rows) else 0


def main(argv: list[str] | None = None, *, seed_stamps: Mapping[str, str] | None = None) -> int:
    """Print the report and return 0 for a report or a passing check, 1 when `--check` finds an overrun, and 2 when the tool fails. `seed_stamps` replaces the commit stamps read from git, so a test can supply them or supply none.

    `_constant_assignment` raises when a constant has been renamed, so that the constant does not go unchecked. An uncaught exception would exit 1, and the artifact cycle reads 1 as an overrun: it diffs the files that hold the constants and writes OVERRUN into the cycle summary, pointing a reader at a re-seed nothing asked for. So `main` catches every failure and returns 2, which the cycle reports as informational.
    """
    parser = argparse.ArgumentParser(
        description="Hold the checked-in per-unit memory peaks against what this box measured, and state the width each one implies here."
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=JOURNAL,
        help="timing journal to read (default: rebuild/out/cycle-timings.ndjson; a journal that does not exist reads as nothing measured yet, which is not a failure)",
    )
    parser.add_argument(
        "--host",
        default=socket.gethostname(),
        help='which machine\'s measurements to check (default: this host); the literal "all" reads every host in the journal, which is for surveying a fleet rather than for deciding whether this box is in trouble',
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=20,
        help="how many of the most recent source records to keep per unit and host, counted from the commit that set each constant to its current value — older records are never held against it (default: 20, a handful of cycles on a working box — long enough that one anomalous run cannot hide a regression by itself, short enough that a genuine improvement is believed within a day's work). 0 reads every record regardless of that commit, for a deliberate archaeology pass.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="fraction of its constant an observation may exceed before it counts as an overrun (default: 0.0). These constants are already headroom — each rounds up past the top of its measured range because erring low puts a box into swap — so a peak that reaches one has eaten all the deliberate slack, and that is the news. The knob is for a fleet survey, not for softening the default.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when an observed peak outruns its constant; a unit with no observations and a unit with no constant are informational and never fail, and a failure of the check itself exits 2 so a caller can tell a verdict from a crash",
    )
    args = parser.parse_args(argv)
    try:
        return _report(args, seed_stamps)
    except Exception as exc:
        print(f"job costs: check FAILED — {exc!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
