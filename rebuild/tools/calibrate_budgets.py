"""Hold the checked-in per-unit memory peaks against what this box actually measured — `make job-costs`, the instrument for issue #92.

Several widths in this tree are a box divided by a measured per-unit peak: what one pytest worker of a suite holds, what one kernel configuration holds while it is live. Each of those peaks is a checked-in constant — `FONT_SUITE_WORKER_BYTES` in the root `conftest.py`, `CONFIG_PEAK_BYTES` in `rebuild/pipeline/kernel_exec.py`, `SURFACE_PARENT_BYTES` and `SURFACE_WORKER_BYTES` in `rebuild/tools/artifact_cycle.py` — and every one of them is a reading of a working set that a memory-saver or a heavier fixture can move out from under. The measurements that would catch such a move are already being taken on every run, by two instruments that were never introduced to each other: each xdist controller's per-worker peaks, and the peak RSS the cycle stamps on every step it spawns. So a divisor could go stale silently, and a stale one announces itself not as a red test but as a box in swap — or, in the other direction, as a pool held to a quarter of the width it had room for. This module is the introduction, and it is a file read: no build, no import of the code it prices.

What it reads is the cycle-timings journal and nothing else. `kind:"pool"` records supply one observation per worker, because the unit being priced is one worker and a pool of eight is therefore eight measurements of it. Named `kind:"step"` records supply their `peak_rss_bytes`, but only where that figure genuinely is one unit, and that caveat wants stating rather than hinting: `peak_rss.reap_peak_rss_bytes` maxes over a child's whole process tree instead of summing it, so a step peak is the widest single process under that step. That is one unit exactly where the step's tree is a parent holding heads over one-thread children (`run_m1`), or a parent holding the whole corpus over workers holding slices of it (`surface-build`), where the max reads the parent and the parent is what that row prices, and it is emphatically not one unit for `gate:make-test`, whose tree carries `make all` and `uv run pyright` beside the pool. The `UNITS` registry below states, per unit, which sources are honest for it, and each entry carries the argument in its own words.

What an observation is held against is that unit's constant, read out of its source file by `ast` and never imported. pytest loads every conftest under the plain name `conftest`, so from anywhere under `rebuild/` a plain `import conftest` answers the wrong file while `import rebuild.conftest` would execute a second copy of one pytest has already loaded and armed its lane-audit hook in; `ast` answers without executing anything, and it keeps a build tool out of the business of importing pytest and inheriting that file's `sys.path` edits. It is `artifact_cycle._font_suite_worker_bytes`' argument, made a third time here. The same mechanism settles one detail the width clauses need: the surface rows read their cap and each other's constant beside their own peak, so the width each prints is the one the build will actually take rather than an uncapped division nothing in the repo uses. The kernel row cannot be settled the same way — what narrows that fan-out is the configuration count and the cores at `run_m1.build_tables`' own call site, neither of them a constant to read — so it prints the memory arithmetic and says in words what narrows it afterwards.

An observed peak past its constant means the divisor is stale. It does not mean an artifact is wrong: what a stale divisor costs is a pool of the wrong width, so nothing here gates a build and `--check`'s nonzero exit is loudness rather than a failure. That nonzero is spelled apart from a crash — `1` is the verdict and `2` is this tool failing to reach one — because the artifact cycle reads the two differently, printing a constants diff on the first and an informational line on the second, and a traceback reported as an overrun would be a measurement nobody ever took. The fix is to re-seed the constant off the fresher measurement, and committing the updated constant is the acceptance — exactly the contract `rebuild/review-census-pins.json` already has for the census, where the diff is what a human reads and the commit is the blessing. The tolerance therefore defaults to zero, which is not strictness for its own sake: these constants are already headroom, each one's own comment saying it rounds up past the top of its measured range because a per-unit cost that errs low is what puts a box into swap while one that errs high only narrows a pool. A measurement that reaches the constant has already eaten all of that deliberate slack, and saying so is the whole news this check exists to deliver; softening it by a further fraction would be headroom on headroom. The knob stays for a caller surveying a fleet with `--host all`, not to soften the default.

Observations are filtered to one host by default, because a per-unit peak is a fact about a working set on a machine and a journal that has been concatenated across boxes mixes a machine that has since gained two memory-savers with one that has not — averaging those two says nothing true about either. A record older than the commit that set a constant's current value is not evidence about that constant, and is set aside before anything is counted: when a memory-saver lands and a constant is re-seeded downward, the journal still holds the old high-water marks from the same host, and a check that read them would tripwire on measurements of code that no longer exists for as long as they stayed inside its window. The commit is found by `git blame` on the constant's own line, so a re-seed clears its row on the very next pass, and a constant edited but not yet committed has no such commit and keeps every record until it does — committing is the acceptance, and the check judging the old rows until then is the check asking whether the new seed has been accepted. The recency bound inside that is the second, narrower window: a handful of the newest records, so that one anomalous run cannot hide a regression and a genuine improvement is believed within a day's work. And one limit cannot be closed, so it is reported rather than papered over: the journal records which box measured a peak, never which box a constant was sized on. A unit with no rows from this host is therefore reported as unverified *here*, which is what is actually known, and never as verified elsewhere.
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


@dataclass(frozen=True)
class Unit:
    """One thing a width in this tree is the box divided by, and everything needed to say whether its checked-in divisor is still true: the constant and the file that holds it, which journal records measure it, and the prose that has to travel with a figure for it to mean anything."""

    name: str
    constant: str | None
    source: str | None
    pool_units: tuple[str, ...]
    step_names: tuple[str, ...]
    step_caveat: str
    note: str


# The kernel's two constants live in one file: the per-configuration figure the fan-out width is divided out of, and the whole-process figure the run_m1 step is watched against.
KERNEL_SOURCE = "rebuild/pipeline/kernel_exec.py"
KERNEL_CONFIG_NAME = "CONFIG_PEAK_BYTES"

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
        note="The rebuild suite's width is the cores this process may actually run on, and nothing divides the box by a per-worker cost to reach it — no test in it reads a live build artifact, so no worker holds a working set worth bounding — and there is nothing here to calibrate. The observations are collected and reported anyway, so that if the suite ever grows a memory-derived width the figure to seed it with is already on the record rather than a measurement someone still has to go and take.",
    ),
    Unit(
        name="kernel-build",
        constant="TABLE_BUILD_PEAK_BYTES",
        source=KERNEL_SOURCE,
        pool_units=(),
        step_names=("run_m1",),
        step_caveat="run_m1's peak is the widest single process in its tree, and that is the one build-tables child holding every settlement configuration — default's retained memo beside every delta in flight — so the step peak reads the whole table build at whatever width the cycle handed it, never one configuration. CONFIG_PEAK_BYTES, the per-configuration figure the width is divided out of, is not measured by any step here: its reading is the crate's own --cache-census on default, and the bound it states is that this unit's peak stays under one configuration more than the delta width.",
        note="The direct measurement is one build-tables over every settlement configuration under /usr/bin/time -l with --cache-census, which is what to reach for before re-seeding either constant; this row is the cheap standing watch beside it rather than a replacement for it.",
    ),
    Unit(
        name="surface-parent",
        constant="SURFACE_PARENT_BYTES",
        source=SURFACE_SOURCE,
        pool_units=(),
        step_names=("surface-build",),
        step_caveat="reap_peak_rss_bytes maxes over the child's whole tree rather than summing it, and under this step that tree is one parent holding the whole corpus beside workers each holding a slice of it, so the max reads the parent — which is this unit exactly. What the same reading cannot see is the sum: parent plus every worker is the build's real footprint, and no step peak has ever been able to report it, which is why the divisor beside this row is measured by the surface pool records instead of here.",
        note="The one row here whose constant is subtracted from the box rather than divided into it: the parent's pile is flat in the width, so it is surface_job_budget's co-resident term. Phase 2 streams into the shards, so the pile is the workload and the projections rather than every fragment, but it is still corpus-shaped — every migrated letter moves it — so expect to re-seed it per batch. The seed sits above the cache-phase peak a served build reaches, where the parent alone writes the store after the pool has closed, which is a few hundred megabytes over the pool-live pile a full build shows; a served pass's row is the one to read when re-seeding, and a full build's row underneath it is not the constant reading low.",
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
)


@functools.cache
def _constant_assignment(path: Path, name: str) -> tuple[int, int]:
    """The integer `path` assigns to `name` at module scope and the line it is assigned on, read out of the source rather than imported. Importing is not available here and would be the wrong tool if it were: pytest loads every conftest under the plain name `conftest`, so from any run collected under rebuild/ a plain `import conftest` answers the wrong file, while `import rebuild.conftest` would execute a second copy of a file pytest has already loaded and armed hooks in. `ast` answers the same question without running anything, and it keeps this tool from importing pytest at all or inheriting that file's sys.path edits. Only `tree.body` is walked, so a constant is what this reads and a same-named local inside some function is not. A missing name raises rather than defaulting, because a constant that has been renamed out from under the registry is a calibration silently not being performed."""
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


def constant_seeded_at(path: Path, name: str, *, root: Path = ROOT) -> str | None:
    """When the commit that set `name` to its current value landed, as the ISO-Z stamp the journal's `finished_at` fields are written in, or None where no such commit exists: the line is edited but not yet committed, the file is untracked, `root` is not a git checkout, or git is not on this box. Every one of those reads as "no bound" rather than as an error, because a missing bound only leaves old records standing, which is the state this tool was in before it had one. `git blame` on the assignment's own line is what answers, and it answers with the last commit that touched that line for any reason — a reflow of the line moves it as surely as a re-seed does — which errs in the one direction that is safe here: a bound that is too new sets aside records that were evidence, and the row says so in its count, while a bound that was too old would hold a fresh seed against a retired peak. The committer time rather than the author time is what a journal stamp is comparable to, since a record measured on the tree before the commit landed was measured on code the seed was taken from rather than code the seed was accepted on."""
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
    """The committer time out of one line's porcelain blame, or None for a line no commit holds yet: porcelain names an uncommitted line by the all-zero hash, and its committer time is the moment of the blame rather than of any commit."""
    lines = porcelain.splitlines()
    if not lines or lines[0].startswith("0" * 40):
        return None
    for line in lines:
        if line.startswith("committer-time "):
            seconds = int(line.split()[1])
            return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def read_seed_stamps(root: Path = ROOT) -> dict[str, str]:
    """When each unit's constant was last committed, keyed by unit name, for every unit whose constant a commit holds. A unit missing here is one with no bound, and `build_rows` keeps every record for it."""
    stamps: dict[str, str] = {}
    for unit in UNITS:
        if unit.constant is None or unit.source is None:
            continue
        stamp = constant_seeded_at(root / unit.source, unit.constant, root=root)
        if stamp is not None:
            stamps[unit.name] = stamp
    return stamps


def read_constants(root: Path = ROOT) -> dict[str, int]:
    """The checked-in per-unit peaks, keyed by unit name, for every unit that has one. `root` is a parameter so a test can point it at a copied tree; the default is this file's own tree, which is the one whose widths are in question."""
    return {
        unit.name: _int_constant(root / unit.source, unit.constant)
        for unit in UNITS
        if unit.constant is not None and unit.source is not None
    }


@dataclass(frozen=True)
class Observation:
    """One measurement of one unit: what it held, which box held it, when, and which kind of journal record said so."""

    peak_bytes: int
    host: str
    at: str
    source: str


def _source_records(
    unit: Unit, pool_records: list[dict], steps_by_run: dict[str, list[dict]]
) -> list[tuple[dict, str]]:
    """Every journal record that measures this unit, each paired with the `source` its observations will carry, in the order they were read. Pool records come first and step records after, which is the order the two loaders answer in rather than strict file order; it matters only as the stable tiebreak under the recency sort, where the timestamps have already decided."""
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
    """Every observation of one unit that survives the host filter, the seed bound and the recency bound, plus how many source records the host filter dropped and how many the seed bound set aside.

    `since` is the ISO-Z stamp of the commit that set this unit's constant to its current value, and a record that finished before it is set aside before the recency window is cut, so the window holds only records measured on code the seed is a claim about; None is no bound at all. A record with no stamp is kept, since nothing can say it is old.

    Every worker peak inside a kept pool record is its own observation, because the unit being calibrated is one worker: a pool of eight is eight measurements of it, which is what makes the median mean "a typical worker" and the max mean "the worst worker seen" — and the worst worker seen is exactly the figure a divisor has to cover. The controller's own peak is carried in the record for a human reading the journal and is deliberately not counted here; it measures a different process, and folding it in would drag the median toward a number no worker ever held.

    The recency bound keeps the most recent `recent` source records — one pool record or one step record apiece, never one worker — per unit and per host, ordered by `finished_at` (the ISO-Z stamps sort lexicographically) with read order as the stable tiebreak; `recent <= 0` keeps everything. What it protects against is a single anomalous run standing as the record for as long as the journal survives. The window is per host even when no host was selected, which is what makes `--host all` a survey of a fleet rather than of its busiest member: one machine that cycles ten times a day would otherwise fill a global window by itself and leave every quieter box unchecked while the report said nothing about it — and the quiet box is exactly the one nobody is watching. The dropped count is returned rather than logged so the report can say plainly that other machines' rows exist and were not checked.
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
    """One unit's whole verdict, decided before anything is rendered: the constant in hand, the observations that survived the filters, what the filters set aside, and the two states worth a word of their own — a peak past its divisor, and a divisor this box has never tested."""

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
    """One row per registered unit, in registry order. Pure over its inputs — the constants and the stamps of the commits that seeded them are injected rather than read here, and the box is not consulted at all — so every assertion about a verdict is an assertion about a function. `seeded_at` maps a unit name to the ISO-Z stamp its records must post-date; a unit it does not name keeps every record, and `recent <= 0` — the archaeology pass — ignores the stamps altogether, since a caller who asked for every record meant every record. The overrun test is strictly greater than the constant plus its tolerance: a peak that exactly reaches the constant is a pool that exactly fits, which is what the constant was chosen to mean.

    A constant is unverified *here* only when there is a here. Under `--host all` the report is a survey of a fleet and no box was named, so a unit with nothing to show has already said the whole truth in its observed line, and adding a sentence about "this host" would contradict the query that was actually run.
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
    """How many journal records a unit's observations came from, which is what tells a reader whether a max is one bad run or a standing figure. Records are counted by the host, stamp and source they share, so two pools of one unit that finished within the same second on one machine read as one — a cosmetic undercount in a figure that decides nothing, and the alternative was widening the pinned Observation to carry a record identity nothing else needs."""
    return len({(item.host, item.at, item.source) for item in observed})


def _percent(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def _plural(count: int, noun: str) -> str:
    """A count with its noun agreeing with it. Worth the three lines because the singular is the common case rather than the corner one — two machines writing into one journal is exactly the arrangement `--host all` exists for, and a report that reads "1 records" in the line a fleet survey is there to print looks like a report nobody read."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _since_line(row: UnitRow) -> str | None:
    """Which records were allowed to speak for this constant, said only where there is a constant to speak for: the stamp of the commit that seeded it and how many older records were set aside, or that the constant is uncommitted and every record stands."""
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
    """What measured this unit, and — when a step peak is one of the answers — why that reading is honest for it. The caveat rides on the line rather than standing alone so it is never read as a general disclaimer: it is an argument about one step's process tree, and it prints only where that step actually contributed."""
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
    """The width this constant implies on the box in hand, said the way the call site that owns it says it. Four units, four shapes: the font suite's pool takes the cores flat and prices this constant as a co-resident rather than dividing by it, so a bare `describe_fit` there would state a width the repo never asks for; the kernel fan-out divides with no memory cap at all and is narrowed afterwards by the configuration count and the cores at run_m1.build_tables' own `min()`, neither of which is a constant to read, so that narrowing is stated in words; and the surface build names two constants rather than one, because its flat half is as large as its divided half — the parent row states what is subtracted from the box and the worker row what the remainder is divided by, so neither is the whole width on its own and each prints the other's figure to reach one. Both surface rows print the unreserved arm and say so, the gated cycle's further subtraction being a fact about a pass rather than about a box. The cap comes out of `root` rather than out of this module's own tree for the reason the constants do: a report is a pure function of a stated box and a stated tree, and a tree hard-coded here would have a test that invented both quietly reading the live repo for half its answer."""
    if unit.name == "font-suite":
        allowed = memory_budget.describe_fit(constant_bytes, total_bytes=total_bytes)
        return f"the font suite takes the cores this process may run on ({cores}), not the division; memory would allow {allowed}"
    if unit.name == "kernel-build":
        configuration = _int_constant(root / KERNEL_SOURCE, KERNEL_CONFIG_NAME)
        fit = memory_budget.describe_fit(
            configuration, coresident_bytes=configuration, total_bytes=total_bytes
        )
        return f"the whole table build is watched here and divides nothing; its delta wave runs {fit} ({KERNEL_CONFIG_NAME}, one taken off first for default's memo), which run_m1.build_tables then narrows by the configurations there are to answer and the cores there are to answer them with"
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
    return memory_budget.describe_fit(constant_bytes, total_bytes=total_bytes)


def render_rows(
    rows: list[UnitRow], *, host: str | None, total_bytes: int, cores: int, root: Path = ROOT
) -> list[str]:
    """The report body, one block per unit in registry order and a verdict line under them all. The box arrives as `total_bytes` and `cores` rather than being probed here for the reason every width in this tree takes its box as a keyword: a report about a machine the suite is not running on has to be a pure function over an invented one. `root` is the tree whose caps and sibling constants the surface widths quote, defaulting to this file's own, so an invented box can be paired with an invented tree."""
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
            # A peak that only just clears its constant rounds to zero here, and "exceeds it by 0%" is a line that argues against the exit code beside it. The margin is the least interesting part of the news anyway — the constant was chosen with headroom, so reaching it at all is the event — so say the margin is small rather than say it is nothing.
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
    """Read the journal, build the rows, print them, and answer the verdict. Split out of `main` so that everything that can go wrong here — a constant renamed out from under the registry, a source file that no longer parses — comes back as a caught exception rather than as an interpreter exit of 1, which is the one code this tool's callers read as a measured overrun."""
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
    """Print the report and answer with the verdict: 0 for a report or a green check, 1 for a check that a measured peak tripped, 2 for this tool failing to reach a verdict at all. `seed_stamps` is the commit stamp each constant's records must post-date, read from git by default and a keyword so a test can state one or state none.

    The third code is what keeps the second honest. `_int_constant` raises on purpose when a constant has been renamed out from under the registry, because a calibration silently not performed is the failure this whole module exists to prevent — but an uncaught raise exits 1, and the artifact cycle reads 1 as "a peak outran its constant", diffs the four constant-bearing files, and writes an OVERRUN into the cycle summary. That would be a measurement nobody took, announced as loudly as a real one and pointing a reader at a re-seeding that nothing asked for. So the failure is caught and spelled 2, which every caller already reads as informational.
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
