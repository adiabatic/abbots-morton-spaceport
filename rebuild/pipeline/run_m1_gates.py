"""The M1 driver's gate half, the code the table build and the font compile never run: the gates that check the tables and the M1.otf a build has written under rebuild/out/m1, and the command line `uv run python -m rebuild.pipeline.run_m1` hands to `main`.

It holds the Manual-pin gate (`run_manual_pin_gate`), the conformance sweep, serial or pooled (`run_font_conformance`), the units the pooled sweep, `make conform-deep` and the deep replay run in (`sweep_units`), the `--gates-only` rerun (`run_gates_only`), the check line and green settlement each invocation leaves when it finishes, stops at a gate, refuses or, as a bare run or a `--gates-only` pass, raises (`_record_cli_check`, `_settle_green`, `_exit_red` for a refusal or a stop before the run_m1 gate is evaluated, and `_recording_failure` for an exception raised past the run's own handlers; a child of the artifact cycle prints its check line for the cycle instead), and the Stage A record a build or a `--gates-only` pass deletes immediately before its first write under rebuild/out/m1 and writes back only once its gates pass (`_retract_stage_a`, `_settle_stage_a`). `main` parses the command line and runs `--gates-only` and `--conform-only` itself. A bare run goes to `run_m1.full_run`, which builds the tables and the font and then calls these gates.

`fingerprint.COMPARISON_CODE_MODULES` names this module, so it is in `pipeline_code_paths` and the run_m1 skip key but not in the tables' stamp (`fingerprint.table_code_paths`): an edit here re-runs the gates over the tables and font on disk instead of forcing a full build, and rebuild/test_build_code_closure.py checks that the build never reaches it. It reaches the build's own helpers through the `run_m1` module object at call time rather than importing their names, so a test that patches one on `run_m1` patches what this module calls. The `out_dir` defaults and `load_default_spec` are bound when this module is imported, so a test patches those on this module or passes `out_dir`. `run_m1.full_run` loads the spec through `run_m1.load_default_spec`, so a test that drives the full run patches that binding instead.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterator, Mapping, NoReturn, Sequence, cast

from rebuild.pipeline import (
    conform,
    fingerprint,
    glyph_facts,
    kernel_exec,
    manual_pins,
    output_sink,
    run_m1,
    shared_segments,
)
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import CellId, GlyphRecord, ResolvedSpec
from rebuild.pipeline.settle import FormationGuard
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable
from rebuild.tools import console
from rebuild.tools.cycle_timings import (
    CYCLE_RUN_ENV,
    REFUSED_STATUS,
    CheckResult,
    parse_inner_timings,
    record_check,
    record_pool,
)
from rebuild.tools.memory_budget import describe_fit
from rebuild.tools.peak_rss import (
    format_gb,
    peak_footprint_self_bytes,
    peak_rss_self_bytes,
    process_peak_rss_bytes,
    rss_token,
)


def run_font_conformance(
    out_dir: Path = run_m1.OUT_DIR,
    max_length: int = 4,
    jobs: int = 1,
    summary_name: str = "conform_summary.json",
) -> dict:
    """Run the exhaustive font-versus-settlement sweep: the per-edit sweep at `max_length` 4 over every settlement configuration, the overlay configuration at `conform.OVERLAY_MAX_LENGTH` whatever `max_length` is, and a hand run past `conform.SWEEP_MAX_LENGTH` through `--conform-max-length`. `make conform-deep` does not call it: `rebuild/tools/deep_sweep.py` runs its own pool. The tables under `out_dir` are read only for the glyph inventory `run_m1.mint_cell_glyphs` needs to name settled cells and read their anchors, and `kern-classes.json` beside the font for the kern assignment every shaped break is checked against (`load_build_kern_sides`). The sweep itself takes no table, because it checks HarfBuzz's behavior against the kernel's, and read-back has already checked that the font holds the rules the build planned. A stamp mismatch stops with an error instead of rebuilding: the enumeration costs a whole kernel fan-out, and an inventory built here could describe runes that have since changed. The split-buffer check runs as part of this sweep, on every text that contains a splitter.

    Above `jobs` 1 the sweep runs in units (`_pooled_conformance`): the ss10 overlay whole and each settlement configuration once per symbol of the alphabet (`sweep_units`), one spawn process each. The serial path sweeps each configuration whole inside `conform.run_conformance`, and both write the same summary. The pooled path computes the `doc/rebuild-design.md` §5.7 formation-guard verdicts (`kernel_exec.guard_sweep`) once for the whole run and passes them with each submission. A spawned worker inherits nothing, so each would otherwise build the crate and sweep the spec itself.

    At the per-edit maximum length, each settlement configuration's walks share its settle memo with the string replay that fills it and the witness stage that reads it, through a memo under `out_dir` keyed per family (`conform.settle_memo_files`, from `run_m1.settle_memo_inputs` taken before the spec loads), a base and segments under a head. The replay fills it, each later phase loads it and writes a segment of what it added, and a rune edit invalidates only the entries whose windows name an edited family. This sweep also prunes the rows no text reaches, naming them in its segment's dead bitmaps: the serial path in its walk's save, the pooled path in the parent over the units' reaches (`_pooled_conformance`). A deeper sweep shares nothing: its memo is a multiple of the conformance sweep's, and a file that size would cost the next conformance-sweep workers more to decode than they save.

    A pooled conformance sweep at `conform.SWEEP_MAX_LENGTH` writes one `conform-sweep` pool record (`cycle_timings.record_pool`), which `make job-costs` reports: one observation per configuration, its units' highest peak footprint (`_conformance_unit`), and one per settle-memo absorb that ran, labeled `<config> absorb`. The serial path starts no pool, and a hand sweep past `conform.SWEEP_MAX_LENGTH` has a different unit peak, so neither records one.
    """
    inputs = run_m1.tables_inputs()
    memo_inputs = run_m1.settle_memo_inputs()
    spec = load_default_spec()
    start = time.perf_counter()
    serialized = run_m1.serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; the sweep no longer rebuilds the fixpoint in process"
        )
    decisions: Mapping[str, DecisionTable | tuple[DecisionTable, ...]] = serialized
    print(f"[t] load_tables {time.perf_counter() - start:.1f}s", flush=True)
    kern_sides = load_build_kern_sides(out_dir)
    cell_glyphs = run_m1.mint_cell_glyphs(spec, decisions)
    settle_memos = (
        conform.settle_memo_files(out_dir, spec, memo_inputs)
        if max_length == conform.SWEEP_MAX_LENGTH
        else {}
    )
    if jobs > 1:
        report = _pooled_conformance(spec, out_dir, max_length, jobs, cell_glyphs, settle_memos, kern_sides)
        report.write(out_dir / summary_name)
    else:
        report = conform.run_conformance(
            out_dir / "M1.otf",
            spec,
            glyphs=cell_glyphs,
            max_length=max_length,
            out_dir=out_dir,
            summary_name=summary_name,
            settle_memos=settle_memos,
            kern_sides=kern_sides,
        )
    summary = {
        "sequences": report.sequences,
        "shaping_runs": report.shaping_runs,
        "divergences": report.divergence_count,
        "pass": report.passed,
        "notes": report.notes,
    }
    for divergence in (exemplar.divergence for exemplar in report.exemplars):
        summary.setdefault("divergence_exemplars", []).append(
            f"{divergence.config} {':'.join(f'{ord(ch):04X}' for ch in divergence.text)} position {divergence.position} [{divergence.kind}] expected {divergence.expected} got {divergence.got}"
        )
    return summary


def load_build_kern_sides(out_dir: Path) -> conform.KernSides:
    """Return the kern assignment the build compiled the font under `out_dir` from (`conform.load_kern_sides` over `kern-classes.json`), which the sweep checks every shaped break against, or stop with an error when the build left none that reads: the glyph chain writes it beside the font once read-back passes, so a font without one was not written by a build that finished."""
    path = out_dir / conform.KERN_CLASSES_JSON
    try:
        return conform.load_kern_sides(path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(
            f"{path} is missing or does not read ({error}) — the sweep checks every shaped break against the kern assignment the font was compiled from, which a finished build writes beside it; run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first"
        ) from None


def run_manual_pin_gate(out_dir: Path = run_m1.OUT_DIR, spec: ResolvedSpec | None = None) -> dict:
    if spec is None:
        spec = load_default_spec()
    report = manual_pins.run_gate(out_dir / "M1.otf", spec)
    summary = manual_pins.summarize(report)
    output_sink.write_output(
        out_dir / "manual_pins_summary.json", json.dumps(summary, indent=2) + "\n", keep_if_equal=True
    )
    return summary


def manual_pin_gate_failure(summary: Mapping) -> str | None:
    """Return why the Manual-pin gate does not count as passed, or None. `pass` alone means no disagreements, which a gate that replayed nothing also satisfies, so this also requires at least one pin in scope and every pin in scope replayed against the font."""
    if not summary.get("pass"):
        return f"Manual-pin gate failed ({len(summary.get('disagreements') or [])} disagreements)"
    in_scope = summary.get("pins_in_scope") or 0
    replayed = summary.get("replayed") or 0
    if in_scope < 1:
        return "Manual-pin gate passed with no pins in scope, which proves nothing about the font"
    if replayed != in_scope:
        return f"Manual-pin gate replayed {replayed} of {in_scope} pins in scope"
    return None


@dataclass(frozen=True)
class SweepUnit:
    """One process's share of a pooled conformance sweep (`run_font_conformance` and `make conform-deep`'s `deep_sweep.run_sweep`), or of the deep replay, which walks the settlement units (`deep_replay.walk_units`): a configuration, the symbol its texts end in or None for a configuration swept whole, the count of texts it shapes, which is the `sequences` its worker returns, and its trigger letters: when there are any, the unit shapes only the texts that contain one (`conform.sweep_texts`), which only the deep sweep asks for."""

    config: str
    last: str | None
    texts: int
    triggers: frozenset[str] = frozenset()

    @property
    def overlay(self) -> bool:
        return self.config in conform.OVERLAY_CONFIGS

    @property
    def cosmetic(self) -> bool:
        return self.config in conform.COSMETIC_CONFIGS


def sweep_units(
    alphabet: Sequence[str], max_length: int, triggers: Mapping[str, frozenset[str]] | None = None
) -> tuple[SweepUnit, ...]:
    """Return the sweep's units in the order the pool takes them: each overlay configuration whole, to `conform.OVERLAY_MAX_LENGTH`, then each cosmetic configuration whole, to `conform.COSMETIC_MAX_LENGTH`, then the settlement units, one per settlement configuration and symbol of `alphabet`, each shaping the texts of length 1 to `max_length` that end in its symbol. A settlement configuration that `triggers` maps to letters shapes only the texts that contain one of them: all of a unit's texts when its symbol is one, and otherwise each length's texts less the ones whose other characters avoid them all.

    The settlement units go largest first, and units of one size keep `conform.SETTLEMENT_CONFIGS` order and then `alphabet`'s order. The pool hands each unit to the first slot that frees, so a large unit queued last would run on after every other slot had gone idle; queued last, the small units fill the slots as they free instead. Without `triggers` every settlement unit is one size, so the order is configuration, then symbol. The overlay and cosmetic configurations stay first, small as they are, so the deep sweep's progress report has a rate for each kind of unit as soon as the sweep starts (`deep_sweep.SweepProgress`).
    """
    size = len(alphabet)
    overlay = sum(size**length for length in range(1, conform.OVERLAY_MAX_LENGTH + 1))
    cosmetic = sum(size**length for length in range(1, conform.COSMETIC_MAX_LENGTH + 1))
    unit = sum(size ** (length - 1) for length in range(1, max_length + 1))

    def settlement_unit(config: str, symbol: str) -> SweepUnit:
        letters = (triggers or {}).get(config, frozenset())
        if not letters or symbol in letters:
            return SweepUnit(config, symbol, unit, letters)
        rest = size - len(letters.intersection(alphabet))
        texts = sum(size ** (length - 1) - rest ** (length - 1) for length in range(1, max_length + 1))
        return SweepUnit(config, symbol, texts, letters)

    return (
        tuple(SweepUnit(config, None, overlay) for config in conform.OVERLAY_CONFIGS)
        + tuple(SweepUnit(config, None, cosmetic) for config in conform.COSMETIC_CONFIGS)
        + tuple(
            sorted(
                (
                    settlement_unit(config, symbol)
                    for config in conform.SETTLEMENT_CONFIGS
                    for symbol in alphabet
                ),
                key=lambda settlement: -settlement.texts,
            )
        )
    )


def config_texts(
    spec: ResolvedSpec, max_length: int, triggers: Mapping[str, frozenset[str]] | None = None
) -> dict[str, int]:
    """Return how many texts each acceptance configuration's units shape between them: every text of one letter up to its maximum length over the spec's alphabet, which is `max_length` for a settlement configuration, `conform.OVERLAY_MAX_LENGTH` for an overlay and `conform.COSMETIC_MAX_LENGTH` for a cosmetic configuration, and for a settlement configuration that `triggers` maps to letters, only the texts that contain one of them. `run_font_conformance` and `deep_sweep.run_sweep` check each configuration's merged `sequences` against it before they write anything, and `deep_replay.main` each settlement configuration's merged texts and skipped texts."""
    alphabet = conform.spec_alphabet(spec)
    size = len(alphabet)
    counts: dict[str, int] = {}
    for config in conform.ACCEPTANCE_CONFIGS:
        longest = (
            conform.OVERLAY_MAX_LENGTH
            if config in conform.OVERLAY_CONFIGS
            else conform.COSMETIC_MAX_LENGTH if config in conform.COSMETIC_CONFIGS else max_length
        )
        letters = (triggers or {}).get(config, frozenset())
        rest = size - len(letters.intersection(alphabet))
        counts[config] = sum(
            size**length - (rest**length if letters else 0) for length in range(1, longest + 1)
        )
    return counts


def _conformance_unit(
    spec: ResolvedSpec,
    font_path: Path,
    config: str,
    last: str | None,
    max_length: int,
    glyphs: Mapping[CellId, GlyphRecord] | None,
    guard_verdicts: FormationGuard | None,
    settle_memo: conform.SettleMemoFile | None,
    memo_to: str | None = None,
    spec_path: Path | None = None,
    kern_sides: conform.KernSides | None = None,
) -> tuple[conform.ConformanceConfigResult, int, shared_segments.Shipped | None]:
    """Run one unit of the pooled conformance sweep in a pool worker (`conform.conformance_config_worker` with `last`) and return the result, its peak footprint (`peak_rss.peak_footprint_self_bytes`), so the parent can write the `conform-sweep` pool record, and the segment that holds the unit's fresh settle memo windows. The unit ships those windows (`conform.MemoReach.fresh`) into the POSIX shared memory segment `memo_to` names (`shared_segments.ship`), the parent hands the segment to its configuration's absorb, and the result's reach keeps only their counts, so no block reaches the parent on a pickled message. `spec_path` names the parent's dump of `spec`, which the unit's settle calls read (`kernel_exec.adopt_spec_dump`), so it writes no `spec.json`. The pool runs one unit per process, so the reading is this unit's own. It is a footprint rather than a resident peak because every settlement unit of a configuration maps the same settle memo parts, whose clean pages the page cache holds once however many units touch them, while each unit's resident peak counts them again. The peak is returned in a tuple instead of as a field on the result because conform.py is in `settle_memo_keys.SETTLE_MEMO_CODE_PATHS`, and an edit there would invalidate every settle memo and row store."""
    if spec_path is not None:
        kernel_exec.adopt_spec_dump(spec, spec_path)
    result = conform.conformance_config_worker(
        spec,
        font_path,
        config,
        max_length,
        glyphs,
        guard_verdicts,
        settle_memo,
        last=last,
        kern_sides=kern_sides,
    )
    shipped: shared_segments.Shipped | None = None
    reach = result.memo_reach
    if reach is not None and reach.fresh is not None:
        fresh = reach.fresh
        result.memo_reach = replace(reach, fresh=replace(fresh, blocks=()))
        del reach
        if memo_to is not None:
            blocks = cast(list[bytes], list(fresh.blocks))
            del fresh
            shipped = shared_segments.ship(blocks, memo_to)
    return result, peak_footprint_self_bytes(), shipped


def _absorb_memo(
    memo: conform.SettleMemoFile,
    spec: ResolvedSpec,
    parts: Sequence[tuple[shared_segments.Shipped | None, conform.FreshMemo]],
    sweep: conform.SweepPrune | None = None,
    route: conform.SettleMemoRoute | None = None,
) -> tuple[conform.MemoAbsorb, float, int]:
    """Absorb one configuration's fresh settle memo windows into its memo (`conform.absorb_settle_memo`) in a fresh pool process, on the route the parent planned before it submitted any absorb (`conform.plan_settle_memos`): the conformance sweep's units' in unit-slot order with the units' prune (`sweep`). The absorb writes its part, a segment or a base, under its final name and commits its configuration's head itself, one writer a configuration at a time, and refuses to write once the memo on disk is not the one its route was planned against. Each part is the shared memory segment a unit shipped its blocks in, and its counts; this maps every one at once and unlinks its name (`shared_segments.Mapped`), hands the absorb the mapped pieces, and closes each mapping once the absorb has read it, or as this returns. Return what the absorb did, its seconds, and this process's peak footprint (`peak_rss.peak_footprint_self_bytes`), which goes into the pool record as `<config> absorb`: the pool runs one task a process, so the reading is the absorb's own. The absorb calls no crate."""
    started = time.perf_counter()
    mapped: list[shared_segments.Mapped] = []
    try:
        fresh: list[conform.FreshMemo] = []
        for shipped, counts in parts:
            if shipped is None:
                fresh.append(counts)
                continue
            segment = shared_segments.Mapped(shipped)
            mapped.append(segment)
            fresh.append(replace(counts, blocks=segment.pieces, release=segment.close))
        absorbed = conform.absorb_settle_memo(memo, spec, fresh, sweep, route=route)
    finally:
        for segment in mapped:
            segment.close()
    return absorbed, time.perf_counter() - started, peak_footprint_self_bytes()


class _PlannedRoutes:
    """The settle memo routes one fan-out's absorbs take, planned on a thread of its own as the fan-out starts (`conform.plan_settle_memos`), so that reading every configuration's head and hashing its parts overlaps the first units instead of holding them back. The first absorb's submission waits for the plan, and every absorb then gets its configuration's route, decided, its fold claim among them, before any absorb writes. `[t] settle_memo_plan` (`conform.SettleMemoPlan.report`) prints once, when the first absorb's submission takes the plan or, for a fan-out that submits none, when `close` does. `close` waits for the plan and deletes what it made for each configuration no absorb committed (`conform.SettleMemoPlan.discard`)."""

    def __init__(self, memos: Mapping[str, conform.SettleMemoFile]) -> None:
        self._planner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="settle-memo-plan")
        self._future = self._planner.submit(conform.plan_settle_memos, memos) if memos else None
        self._plan: conform.SettleMemoPlan | None = None

    def _taken(self) -> conform.SettleMemoPlan:
        if self._plan is None:
            assert self._future is not None
            self._plan = self._future.result()
            self._plan.report()
        return self._plan

    def route(self, config: str) -> conform.SettleMemoRoute:
        return self._taken().routes[config]

    def close(self) -> None:
        try:
            if self._future is not None:
                self._taken().discard()
        finally:
            self._planner.shutdown(wait=True)


def _shipped_bytes(shipped: shared_segments.Shipped | None) -> int:
    return 0 if shipped is None else sum(shipped.lengths)


@dataclass
class _MemoFold:
    """One settlement configuration's settle-memo use across its conformance-sweep units, folded in the parent as each unit finishes: the version of the memo the first unit mapped (`conform._MemoStore.identity`), the union of every unit's reached rows as one integer (each flag byte is zero or one, so OR-ing the little-endian integers ORs the flags), the fresh windows each unit shipped, by unit slot, and their summed memo seconds. `agree` turns False when a unit mapped another version of the file than the first, and then nothing is pruned. `retired` turns True when a unit's probe found the file corrupt, and then the absorb drops every row of the file rather than the unreached ones (`conform.SweepPrune`'s `discard`). `shipped` counts the bytes of the units' segments that wait for the absorb."""

    identity: tuple | None = None
    rows: int = 0
    live: int = 0
    loaded: int = 0
    stale: int = 0
    reached: int = 0
    parts: dict[int, tuple[shared_segments.Shipped | None, conform.FreshMemo]] = field(default_factory=dict)
    seconds: float = 0.0
    units: int = 0
    agree: bool = True
    retired: bool = False
    shipped: int = 0

    def add(self, reach: conform.MemoReach, slot: int, shipped: shared_segments.Shipped | None) -> None:
        """Fold in the reach of the unit in `slot`, whose fresh windows, if it settled any, wait in `shipped`."""
        if not self.units:
            self.identity, self.rows, self.live = reach.identity, reach.rows, reach.live
            self.loaded, self.stale = reach.loaded, reach.stale
        elif (reach.identity, reach.rows) != (self.identity, self.rows):
            self.agree = False
        self.units += 1
        self.retired = self.retired or reach.retired
        if self.agree:
            self.reached |= int.from_bytes(zlib.decompress(reach.reached), "little")
        if reach.fresh is not None:
            self.parts[slot] = (shipped, reach.fresh)
            self.shipped += _shipped_bytes(shipped)
        self.seconds += reach.seconds

    @property
    def pruned(self) -> int:
        """The live rows no unit reached, or zero when the units disagree about the file or a unit retired it."""
        return self.live - self.reached.bit_count() if self.agree and not self.retired else 0

    def prune(self) -> conform.SweepPrune:
        """What the absorb takes from the units: the version they mapped, their reached rows, whether they agree, and whether one retired the file."""
        return conform.SweepPrune(
            self.identity,
            zlib.compress(self.reached.to_bytes(self.rows, "little"), 1),
            self.agree,
            self.retired,
        )


SWEEP_SCRATCH_PREFIX = "conform-memo."


def _sweep_scratch(out_dir: Path) -> Path:
    return Path(out_dir) / f"{SWEEP_SCRATCH_PREFIX}{os.getpid()}"


def _sweep_segment_names(token: str, units: int) -> list[str]:
    return [f"conform-{token}-{slot}" for slot in range(units)]


def discard_sweep_scratch(out_dir: Path) -> None:
    """Unlink the shared memory segments a dead pooled conformance sweep over `out_dir` named for its units, from the token and unit count its `_SweepSegments` recorded, and delete its scratch directory. A SIGKILL of the sweep's whole process group kills its resource tracker too and skips every exit, and macOS lists no POSIX shared memory, so those names are the only way to find what it left. A directory whose pid is still running belongs to another sweep and is kept, and nothing here raises."""
    with suppress(OSError):
        for path in Path(out_dir).glob(f"{SWEEP_SCRATCH_PREFIX}*"):
            if shared_segments.owner_is_running(path.name.rpartition(".")[2]):
                continue
            try:
                token, count = (path / shared_segments.SEGMENT_RECORD).read_text().split()
                names = _sweep_segment_names(token, int(count))
            except OSError, ValueError:
                names = []
            for name in names:
                with suppress(OSError, ValueError):
                    shared_segments.unlink_if_made(name, track=False)
            shutil.rmtree(path, ignore_errors=True)


class _SweepSegments:
    """The names of the POSIX shared memory segments the pooled conformance sweep's units ship their fresh settle memo windows in, one a unit slot (`name`), under a random token of this sweep's own. As it enters it unlinks what a dead sweep over the same `out_dir` left (`discard_sweep_scratch`) and records its token and unit count in a scratch directory of its own under `out_dir` (`shared_segments.SEGMENT_RECORD`). As it exits, normally or not, it unlinks every name still there, a segment a unit made whose configuration's absorb never mapped it, and deletes the record, so a sweep that fails or is interrupted while this block unwinds leaves no segment behind, and one SIGKILLed with its process group leaves them to the next sweep's entry."""

    def __init__(self, out_dir: Path, units: int) -> None:
        self._out_dir = Path(out_dir)
        self._scratch = _sweep_scratch(out_dir)
        self._token = secrets.token_hex(6)
        self._names = _sweep_segment_names(self._token, units)

    def __enter__(self) -> "_SweepSegments":
        discard_sweep_scratch(self._out_dir)
        self._scratch.mkdir(parents=True, exist_ok=True)
        (self._scratch / shared_segments.SEGMENT_RECORD).write_text(f"{self._token} {len(self._names)}\n")
        return self

    def __exit__(self, *exception: object) -> None:
        for name in self._names:
            with suppress(OSError, ValueError):
                shared_segments.unlink_if_made(name)
        shutil.rmtree(self._scratch, ignore_errors=True)

    def name(self, slot: int) -> str:
        return self._names[slot]


def _pooled_conformance(
    spec: ResolvedSpec,
    out_dir: Path,
    max_length: int,
    jobs: int,
    glyphs: Mapping[CellId, GlyphRecord],
    settle_memos: Mapping[str, conform.SettleMemoFile],
    kern_sides: conform.KernSides | None = None,
) -> conform.ConformReport:
    """Sweep every unit of `sweep_units` at `max_length`, `jobs` at a time, one spawn process per unit (`run_m1._spawn_pool` with `max_tasks_per_child=1`), and return the merged report; `run_font_conformance` writes it.

    The units are submitted in unit order and only as slots free, so at most `jobs` tasks are submitted and not yet finished at once, and an absorb submitted as a configuration completes takes the next slot a unit frees rather than waiting behind every unit still to run. Each unit reads the parent's spec dump (`kernel_exec._spec_dump`, `kernel_exec.adopt_spec_dump`). A settlement unit whose configuration has a settle memo maps the shared memo, every part its head lists, ships the windows it settled fresh into the shared memory segment the parent names for its slot (`_SweepSegments`, `_conformance_unit`), and returns its reach with their counts. As each unit finishes, the parent folds its reach into its configuration's `_MemoFold` and drops the unit's flags, so the parent holds one integer the size of the memo's row count per configuration however many units there are. Each configuration's settle memo route is planned on a thread as the sweep starts, before any absorb is submitted (`_PlannedRoutes`, `conform.plan_settle_memos`, printed as `[t] settle_memo_plan`), and each absorb gets its configuration's route.

    As soon as all of a configuration's units have finished, its units merge into its result (`conform.merge_unit_results`, in unit-slot order), whose sequences must be every text of its lengths (`config_texts`), or this raises before that configuration's memo is written, so no memo is ever pruned from a partial set of units. Then, if its units shipped fresh windows, left a live row unreached, or found a part corrupt, its absorb is submitted to a fresh process (`_absorb_memo`), with the units' shared memory segments in unit-slot order, so the part it writes, a segment or on a fold a base, does not depend on the order the units finished in; a configuration with neither (a current memo) gets no task and its memo is not touched. When a unit raises or a configuration's count falls short, the tasks still queued are cancelled, the error propagates once the units and absorbs already running have finished, and a configuration absorbed before then holds only rows its own finished, checked units settled. `[t] conform_fan_in` follows the last task with the seconds the parent spent folding units and submitting absorbs and, as `memo_held_gb`, the most bytes its units' segments held at once while they waited for their absorbs, from a unit's landing until its configuration's absorb finished. One `[t] settle_memo` line per settlement configuration follows (`conform.settle_memo_line`), with the units' loaded and stale counts, the absorb's fresh and pruned counts, the units' memo seconds plus the absorb's, and the absorb's path, row counts and what it wrote when it laid a part out (`conform.MemoAbsorb.tokens`). The configurations then merge in `conform.ACCEPTANCE_CONFIGS` order, so the report is the serial sweep's at any width and in any finishing order. At `conform.SWEEP_MAX_LENGTH` this writes the `conform-sweep` pool record.
    """
    font_path = out_dir / "M1.otf"
    units = sweep_units(conform.spec_alphabet(spec), max_length)
    kernel_exec.ensure_built()
    guard_verdicts = kernel_exec.guard_sweep(spec)
    spec_path = kernel_exec._spec_dump(spec)
    expected = config_texts(spec, max_length)
    remaining = {config: 0 for config in conform.ACCEPTANCE_CONFIGS}
    for unit in units:
        remaining[unit.config] += 1
    collected: dict[int, conform.ConformanceConfigResult] = {}
    merged: dict[str, conform.ConformanceConfigResult] = {}
    worker_peaks: dict[str, int] = {}
    folds = {config: _MemoFold() for config in settle_memos}
    absorbed: dict[str, tuple[conform.MemoAbsorb, float]] = {}
    width = min(jobs, len(units))
    held = held_peak = 0
    folding = 0.0
    routes = _PlannedRoutes(settle_memos)
    with _SweepSegments(out_dir, len(units)) as segments, closing(routes):
        with run_m1._spawn_pool(jobs, len(units), max_tasks_per_child=1) as pool:
            queued = iter(enumerate(units))
            running: dict[Future, tuple[str, int]] = {}

            def top_up() -> None:
                while len(running) < width:
                    entry = next(queued, None)
                    if entry is None:
                        return
                    slot, unit = entry
                    memo = settle_memos.get(unit.config)
                    future = pool.submit(
                        _conformance_unit,
                        spec,
                        font_path,
                        unit.config,
                        unit.last,
                        max_length,
                        glyphs,
                        guard_verdicts,
                        None if memo is None else replace(memo, returns_fresh=True),
                        None if memo is None else segments.name(slot),
                        spec_path,
                        kern_sides,
                    )
                    running[future] = (unit.config, slot)

            try:
                top_up()
                while running:
                    done, _pending = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        config, slot = running.pop(future)
                        if slot < 0:
                            absorb, seconds, peak = future.result()
                            absorbed[config] = (absorb, seconds)
                            worker_peaks[f"{config} absorb"] = peak
                            held -= folds[config].shipped
                            continue
                        result, peak, shipped = future.result()
                        started = time.perf_counter()
                        worker_peaks[config] = max(worker_peaks.get(config, 0), peak)
                        if result.memo_reach is not None:
                            folds[config].add(result.memo_reach, slot, shipped)
                            held += _shipped_bytes(shipped)
                            held_peak = max(held_peak, held)
                            result.memo_reach = None
                        collected[slot] = result
                        remaining[config] -= 1
                        console.progress(len(collected), len(units), "units")
                        if not remaining[config]:
                            whole = conform.merge_unit_results(
                                config,
                                [collected[at] for at, unit in enumerate(units) if unit.config == config],
                            )
                            if whole.sequences != expected[config]:
                                raise RuntimeError(
                                    f"conformance sweep[{config}]: its units shaped {whole.sequences} texts, not the {expected[config]} of every length it sweeps"
                                )
                            merged[config] = whole
                            fold = folds.get(config)
                            if fold is not None and (fold.parts or fold.pruned or fold.retired):
                                absorb_future = pool.submit(
                                    _absorb_memo,
                                    settle_memos[config],
                                    spec,
                                    [fold.parts[at] for at in sorted(fold.parts)],
                                    fold.prune(),
                                    routes.route(config),
                                )
                                running[absorb_future] = (config, -1)
                        folding += time.perf_counter() - started
                    top_up()
            except BaseException:
                pool.shutdown(wait=True, cancel_futures=True)
                raise
    console.timing("conform_fan_in", folding, f"memo_held_gb={format_gb(held_peak)}")
    for config in conform.SETTLEMENT_CONFIGS:
        fold = folds.get(config)
        if fold is None:
            continue
        if not fold.agree:
            console.warn(
                f"settle memo: {settle_memos[config].path} was mapped at more than one version by {config}'s units; nothing is pruned this sweep"
            )
        absorb, seconds = absorbed.get(config, (None, 0.0))
        print(
            conform.settle_memo_line(
                config,
                fold.seconds + seconds,
                fold.loaded,
                fold.stale,
                0 if absorb is None else absorb.fresh,
                0 if absorb is None else absorb.pruned,
                absorb is not None and absorb.written,
                absorb,
            ),
            file=sys.stderr,
            flush=True,
        )
    if max_length == conform.SWEEP_MAX_LENGTH:
        record_pool(
            "conform-sweep",
            width=width,
            worker_peaks=worker_peaks,
            controller_peak_bytes=peak_rss_self_bytes(),
            worker_reading="footprint",
        )
    return conform.merge_conformance_results(
        font_path, [merged[config] for config in conform.ACCEPTANCE_CONFIGS]
    )


def _record_cli_check(result: CheckResult, started: float) -> None:
    """Record this invocation's check result in the timings journal, unless the artifact cycle is recording it. The result is recorded before the final `SystemExit`, so the exit cannot change it. `CYCLE_RUN_ENV` in the environment means the artifact cycle started this run and records its result under its own run id, so this function prints the result on one `console.CHECK` line instead, which the cycle files when the summaries the child left do not say why it stopped (`artifact_cycle._reported_red`), keeping one journal line per invocation. When this invocation built tables, the line carries the build's phase lines (`run_m1._build_phase_lines`) as `inner`, parsed as the cycle parses a run_m1 step's output, so a build run by hand can be paired with another as a cycle's can."""
    if CYCLE_RUN_ENV in os.environ:
        reported = {
            "check": result.check,
            "outcome": result.outcome,
            "status": result.status,
            "failures": list(result.failures),
        }
        console.say(console.CHECK + json.dumps(reported))
        return
    record_check(
        result,
        argv=sys.argv,
        elapsed_s=time.perf_counter() - started,
        peak_rss_bytes=process_peak_rss_bytes(),
        inner=parse_inner_timings("\n".join(run_m1._build_phase_lines)) or None,
    )


def _failed_check(check: str, message: str, status: str = "FAILED") -> CheckResult:
    """Return the check result for a run that failed before its gate was evaluated: a defect gate that stopped the build, a pin gate failure, a read-back or emit error, or, with `REFUSED_STATUS`, a refusal before any gate ran. The error message is all that is known, so it is the only failure text, and there are no failed ids because nothing enumerated cases."""
    return CheckResult(check=check, outcome="red", status=status, failures=[message], failed_ids=[])


def _record_red(message: str, started: float, *, status: str, key: str | None = None) -> None:
    """Settle run_m1's green red over `key`, the key the run took before its gates, or over the key of the sources on disk when it stopped before taking one, then record the red check line (`_failed_check` with `message` and `status`), timed from `started`. A red clears only a green over the same content (`_settle_green`). So a hand run settles its red as the cycle settles a red run_m1 child (`artifact_cycle._do_run_m1`), and no later `--gates-only` pass can stand on a green over the content this run refused or failed on. Under `CYCLE_RUN_ENV` the cycle settles the green, so the key is not computed, and the check line goes to the cycle (`_record_cli_check`)."""
    from rebuild.tools.artifact_cycle import run_m1_skip_fingerprint
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(run_m1.REPO_ROOT)

    if CYCLE_RUN_ENV not in os.environ:
        _settle_green(RUN_M1_GREEN, run_m1_key() if key is None else key, False, run_m1_key, "run_m1")
    _record_cli_check(_failed_check("run_m1", message, status), started)


def _exit_red(
    message: str, started: float, *, status: str = REFUSED_STATUS, key: str | None = None
) -> NoReturn:
    """Stop a run_m1 invocation that refused, or stopped before its run_m1 gate was evaluated, with `message`, once `_record_red` has settled the red and recorded its check line under `status`."""
    _record_red(message, started, status=status, key=key)
    raise SystemExit(message)


@contextmanager
def _recording_failure(started: float) -> Iterator[None]:
    """Record an exception a bare run or a `--gates-only` pass raises past its own handlers as a FAILED red naming the exception's type and message (`_record_red`), then let it propagate with its traceback, which names where it was raised. `main` runs both under it, so such a run that fails leaves a check line unless it is interrupted or killed, whatever it failed on: a contact allow-list or a rune that does not load, say. A SystemExit passes through untouched, because the run records its line before each exit it takes."""
    try:
        yield
    except Exception as error:
        _record_red(f"{type(error).__name__}: {error}", started, status="FAILED")
        raise


@dataclass(frozen=True)
class GatesOnlyBase:
    """The build a `--gates-only` pass re-runs the gates over: each settlement configuration's decision table paired with its join table, and the build's own pipeline_summary.json, whose defect fields the pass rewrites."""

    tables: dict[str, tuple]
    pipeline_summary: dict


def gates_only_base(out_dir: Path, inputs: str) -> GatesOnlyBase | str:
    """Return the build under `out_dir` that a `--gates-only` pass reads, or the reason the pass refuses it. The pass reads five things: the table heads stamped with `inputs` (`run_m1.serialized_tables`, through each configuration's head file), M1.otf, the glyph-facts sidecar beside it, which the pin gate reads (`glyph_facts.FILE_NAME`), a pipeline_summary.json that parses to an object, and every configuration's join table. `run_gates_only` exits with the returned reason, and the artifact cycle plans a gates-only rerun only when this returns a build (`artifact_cycle.m1_gates_only_base_present`), so the cycle never plans a rerun the pass would refuse over the build on disk, which it would otherwise plan again on every pass until `--fresh`. It leaves out manual_pins_summary.json and the Stage A record, because the pass writes them. Nor does it read the table-only branch's results (the string replay, the rule witnesses and the shipped-order walks), which the pass does not rerun: the pass records a green only over a prior green, which shows that branch passed over a build of the same build-side inputs its key names, and the stamp shows the tables on disk were stamped from those inputs. The build is deterministic, so those can be the tables that branch passed even when the full build that wrote them went red in that branch, and the two checks are necessary conditions for that, not sufficient ones (`run_gates_only` names the gaps). What they show rests on the prior green, never on the red build: a failed run never vouches for the artifacts it left. The cycle deletes pipeline_summary.json before it spawns a full build, and the build writes that file only after the compile and the read-back, so a build the cycle spawned that stopped earlier has no summary and is refused here."""
    serialized = run_m1.serialized_tables(out_dir, inputs)
    if serialized is None:
        return f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; --gates-only re-runs the gates over a build, it does not make one"
    font_path = out_dir / "M1.otf"
    if not font_path.is_file():
        return f"no compiled font at {font_path} — run `uv run python -m rebuild.pipeline.run_m1` first"
    if not (out_dir / glyph_facts.FILE_NAME).is_file():
        return f"no glyph-facts sidecar beside {font_path}, which the pin gate reads — run `uv run python -m rebuild.pipeline.run_m1` first"
    summary_path = out_dir / "pipeline_summary.json"
    try:
        pipeline_summary = json.loads(summary_path.read_text())
    except OSError, ValueError:
        pipeline_summary = None
    if not isinstance(pipeline_summary, dict):
        return f"no readable {summary_path} — the defect fields are rewritten into the build's own summary, and a build that left none is not a build this pass can stand on; run `uv run python -m rebuild.pipeline.run_m1` first"
    tables: dict[str, tuple] = {}
    for config, decision in serialized.items():
        joins_path = out_dir / f"joins-{config}.tsv"
        try:
            tables[config] = (decision, table_module.read_join_tsv(joins_path))
        except (OSError, ValueError) as error:
            return f"{joins_path} is missing or unreadable ({error}) — the defect gate reads the join tables beside the enumeration, so this pass cannot rerun the gates over this build; run `uv run python -m rebuild.pipeline.run_m1` first"
    return GatesOnlyBase(tables=tables, pipeline_summary=pipeline_summary)


def run_gates_only(out_dir: Path = run_m1.OUT_DIR) -> None:
    """Re-run everything a full run does after the table build except the stages that produce artifacts: the defect gate and the Manual-pin replay, over the tables and the M1.otf already on disk. It captures the Stage A values before it reads any input and deletes the Stage A record immediately before its first write (`_retract_stage_a`), the defect fields, so a refusal before then leaves the record and the build as it found them. It rewrites the defect fields of `pipeline_summary.json` and the Manual-pin summary, writes the record back only once its gates pass over a prior green it stands on (`_settle_stage_a`; a later paragraph says when), and compiles nothing. The rerun depends on the stamp the build left on its serialized enumerations. The stamp names the sources those tables came from, not the build that wrote the M1.otf beside them. A stamp that matches the runes on disk is a necessary condition for the green this pass may record; that green also needs a prior green over the same build-side inputs (`artifact_cycle.gates_only_rerun`), because a failed run never vouches for the artifacts it left, and the build is deterministic, so the passing build need not be the one that stamped the tables. Even both together do not show that the tables or M1.otf on disk are a passing build's (the next paragraph names the gaps). A mismatch stops with an error instead of sweeping a stale binary. That refusal and the others over the build on disk are `gates_only_base`, the same check the artifact cycle makes before it plans a gates-only rerun. Each refusal records a red check line and clears a green over the same content (`_exit_red`), as a red gate does. Because the stamp names only what the build reads, every comparison-side edit passes it and is re-checked here: the contact allow-list the defect gate reads, and the code `fingerprint.COMPARISON_CODE_MODULES` names (the pin gate and its checker), which is outside the stamp's code half. That saves a build for a contact allow-list bless and for an edit to the pin gate's code.

    It may record run_m1's green under two conditions: a prior green record exists, and every input that changed since it is comparison-side (`artifact_cycle.gates_only_rerun`). The prior green shows that a completed build over every build-side input its key names passed, the stamp check shows that each enumeration's head carries the stamp of the current sources and semantics tokens, and this pass re-checks the gates the changed inputs feed. With all three it records a green over the new inputs, so the next cycle skips run_m1. The two checks are necessary conditions for the tables and M1.otf on disk to be the green build's, not sufficient ones: a build that stops without settling the green (an interrupt or a kill) can leave artifacts that pass both. The green's key (`artifact_cycle.run_m1_skip_lines`) leaves out the semantics tokens the stamp includes, so a pass run under other semantics than the green build's accepts tables a hand build stamped under those semantics. The stamp check reads only each enumeration's head, and `run_m1.build_tables` packs each enumeration into a staged name (`run_m1._take_windows`) that it renames over the enumeration only after the crate's clean exit, so a pack a killed build cut short after its head is left only in the staged file, which the check never reads. Neither the stamp nor the Stage A record covers the font's bytes, nothing else this pass or the cycle checks ties them to a passing build, and a hand build stamps its tables before `compile_font` writes the font, so a hand build interrupted between the two leaves whatever M1.otf an earlier build wrote, which can be one a red build over other build-side inputs left. In each case a green this pass records vouches for artifacts no passing build wrote. Of the runs behind the pass, only the prior green vouches for the build it stands on: a failed run never vouches for the artifacts it left, even one that went red only at a comparison gate over current tables (`artifact_cycle.gates_only_rerun` says why). Otherwise the pass still runs, still records its check line, and prints which input kept it from recording a green. A check line reports only how one invocation came out, while a green record lets a later pass skip work.

    It writes the Stage A record back (through `_settle_stage_a`, which declines when a Stage A input has moved since the pass began) only when its gates pass, the prior green has per-file lines, and no build-side input has moved since that green, whether or not this pass records a green. Otherwise a pass that reached its first write leaves no record, which would vouch for tables and a font that no green build over these build-side inputs passed (`artifact_cycle.m1_stage_a_current` says what the cycle trusts the record for).
    """
    from rebuild.tools.artifact_cycle import (
        _defect_gate_failure,
        comparison_side_label,
        evaluate_run_m1_gate,
        gates_only_rerun,
        moved_input_labels,
        read_green_record,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(run_m1.REPO_ROOT)

    started = time.perf_counter()
    stage = fingerprint.stage_a(run_m1.REPO_ROOT)
    inputs = run_m1.tables_inputs()
    summary_path = out_dir / "pipeline_summary.json"
    base = gates_only_base(out_dir, inputs)
    if isinstance(base, str):
        _exit_red(base, started)
    pipeline_summary = base.pipeline_summary

    spec = load_default_spec()
    before = run_m1_key()
    record = read_green_record(RUN_M1_GREEN)
    current = run_m1_skip_files(run_m1.REPO_ROOT)

    console.phase("defect_gates")
    start = time.perf_counter()
    cell_glyphs = run_m1.mint_cell_glyphs(spec, base.tables)
    kerned = run_m1.resolve_kern(spec, base.tables, cell_glyphs)
    defect_fields = run_m1._defect_summary_fields(
        run_m1._run_defect_gates(spec, base.tables, cell_glyphs, kerned)
    )
    print(f"[t] defect_gates {time.perf_counter() - start:.1f}s", flush=True)
    pipeline_summary.update(defect_fields)
    _retract_stage_a(out_dir)
    output_sink.write_output(summary_path, json.dumps(pipeline_summary, indent=2) + "\n", keep_if_equal=True)
    defect_failure = _defect_gate_failure(pipeline_summary)
    if defect_failure is not None:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", defect_failure), started)
        raise SystemExit(f"{defect_failure}; see pipeline_summary.json")

    console.phase("run_manual_pin_gate")
    start = time.perf_counter()
    pin_gate = run_manual_pin_gate(out_dir=out_dir, spec=spec)
    print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(pin_gate, indent=2))
    pin_failure = manual_pin_gate_failure(pin_gate)
    if pin_failure is not None:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", pin_failure), started)
        raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")

    gate = evaluate_run_m1_gate(pipeline_summary, pin_gate)
    _record_cli_check(gate, started)
    if not gate.ok:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        raise SystemExit(
            "; ".join(gate.failures) + "; see pipeline_summary.json and manual_pins_summary.json"
        )
    labels = moved_input_labels(record, current) or []
    offenders = [label for label in labels if not comparison_side_label(label)]
    if record is not None and isinstance(record.get("files"), dict) and not offenders:
        _settle_stage_a(out_dir, stage)
    if gates_only_rerun(record, current) is not None:
        _settle_green(
            RUN_M1_GREEN,
            before,
            True,
            run_m1_key,
            "run_m1",
            files_of=lambda: run_m1_skip_files(run_m1.REPO_ROOT),
        )
        return
    if offenders:
        why = f"these build-side inputs moved since the last green M1 build, and a failed run never vouches for the artifacts it left, so only a full build can record a green over them: {', '.join(offenders)}"
    elif record is None:
        why = "there is no prior green M1 build for this pass to stand on"
    elif not isinstance(record.get("files"), dict):
        why = "the last green M1 build predates the per-file record this decision is taken over"
    else:
        why = "nothing has moved since the last green M1 build, so that green already stands"
    console.warn(f"run_m1: green, but this pass recorded no green — {why}")


def _retract_stage_a(out_dir: Path) -> None:
    """Delete the Stage A record under `out_dir`. Each run_m1 path captures the record's values (`fingerprint.stage_a`) before it reads an input, and calls this immediately before its first write under `out_dir`: the table build (`run_m1.run`) on a build and the defect fields of pipeline_summary.json on a `--gates-only` pass. A build then writes the tables, M1.otf and the summaries; a `--gates-only` pass writes the gate summaries. The artifact cycle trusts a matching record beside the summaries and M1.otf (`artifact_cycle.m1_stage_a_current`). So a run that writes under `out_dir` and then stops, fails a gate or raises before `_settle_stage_a` leaves no record to vouch for what it left: a corpus built over it carries null Stage A values, which `make verdict-ready` refuses, and the next cycle skips neither run_m1 nor the corpus build. A run that a check refuses, or that fails, before its first write (`gates_only_base`, a rune that does not load) has changed nothing the record vouches for, so it leaves the record as well: deleting it would keep the next cycle from skipping run_m1 and the corpus build once the edit is fixed or reverted, though no artifact changed. A `--kern-only` pass never deletes it: it runs only over a record that matches the sources, and replaces nothing the record vouches for but the font's `kern` lookup, which the corpus does not read (`run_m1.kern_only_run`)."""
    (Path(out_dir) / fingerprint.STAGE_A_FILENAME).unlink(missing_ok=True)


def _settle_stage_a(out_dir: Path, values: Mapping[str, str]) -> None:
    """Write the Stage A record under `out_dir` (`fingerprint.write_stage_a`) once a run's gates have passed: the values the run captured (`fingerprint.stage_a`) before it read its inputs, and only while the sources on disk still give them. A Stage A input edited while the run ran may have reached the artifacts after its values were captured, so a record of the captured values could match the sources again once the edit is reverted, beside artifacts built from the edit. The run warns and leaves no record instead, as `_settle_green` declines a green whose key moved. The check catches a Stage A edit still on disk at the settle, but not one made and reverted while the run was going: the run may have read the edit, and the record names the sources without it. `_settle_green`'s recompute has the same limit. The cycle never writes this record, so unlike a green it is written under `CYCLE_RUN_ENV` too."""
    if fingerprint.stage_a(run_m1.REPO_ROOT) != dict(values):
        console.warn(
            "run_m1: green, but its Stage A inputs changed while it ran — Stage A record not written"
        )
        return
    fingerprint.write_stage_a(out_dir, values)


def _settle_green(
    green_path: Path,
    key: str,
    ok: bool,
    recompute: Callable[[], str],
    label: str,
    files_of: Callable[[], dict[str, str]] | None = None,
) -> None:
    """Finalize a standalone green through `green_record.settle_green` and report publication or input drift. `CYCLE_RUN_ENV` gives the cycle ownership of both publication and invalidation, so a child leaves its records untouched. `files_of` supplies the per-file digest lines behind the key, so a later skip miss can name the input that changed."""
    if CYCLE_RUN_ENV in os.environ:
        return
    from rebuild.tools.green_record import settle_green

    if not settle_green(green_path, key, ok, recompute, files_of=files_of):
        if ok:
            console.warn(f"{label}: green, but its inputs changed while it ran — green not recorded")
        return
    where = (
        green_path.relative_to(run_m1.REPO_ROOT)
        if green_path.is_relative_to(run_m1.REPO_ROOT)
        else green_path
    )
    print(f"{label}: green — fingerprint recorded in {where}", flush=True)


def main(argv: list[str] | None = None) -> None:
    from rebuild.tools.artifact_cycle import conform_job_budget, conform_job_derivation

    conform_jobs = conform_job_budget(skip_gates=True, skip_corpus=True)
    parser = argparse.ArgumentParser(description="Run the M1 integration pipeline and its Phase-2 gates.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"worker budget for --conform-only: the conformance sweep runs this many of its units at once, one process each (the ss10 overlay whole and each settlement configuration once per final symbol), and no more than there are units. The default is a budget the artifact cycle derives from the machine rather than a checked-in width, the conformance sweep's own `conform_job_budget()` with the build lane idle, since a hand sweep shares the machine with no corpus build and no make-test pool — on this machine {conform_job_derivation(skip_gates=True, skip_corpus=True)}. `--jobs 1` is serial. The table build's own width is --kernel-threads.",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--gates-only",
        action="store_true",
        help="re-run the defect gate and the Manual-pin gate against the M1.otf and tables already on disk, rewriting the defect fields of pipeline_summary.json and the Manual-pin summary, and writing the Stage A record, which it deletes just before its first write, only once those gates pass over a prior green build of the same build-side inputs; refuses when those tables were built from other sources than the ones on disk, and records run_m1's green when everything that moved since the last green build is comparison-side",
    )
    parser.add_argument(
        "--kern-only",
        action="store_true",
        help="recompile the font over the tables already on disk after an edit that moved only runes' kern blocks: the kern stage, the defect gates, the GSUB and GPOS emission, the compile and read-back, keeping the prior green build's Manual-pin summary; refuses as --gates-only does, and records run_m1's green when everything that moved since the last green build is a kern block",
    )
    parser.add_argument(
        "--fresh-tables",
        action="store_true",
        help="run the kernel crate's table build even when the table-reuse record the last green build left under rebuild/out/m1 shows that nothing the crate reads, nor the table build's driver, has moved since; without it a bare run, by hand as under the artifact cycle, re-stamps those tables in place of running the crate whenever the record holds its key and every table file its raw digest, and the cycle passes this flag under its own --fresh",
    )
    parser.add_argument(
        "--conform-max-length",
        "--conform-horizon",
        type=int,
        default=4,
        help="exhaustive sweep length for --conform-only (the per-edit sweep over the settlement configurations; the overlay configuration keeps its own maximum length); `make conform-deep` runs the same sweep deeper on demand",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        default=None,
        help=(
            "how many worker slots the kernel's table build runs at once beside default's memo, each holding a delta configuration or default's fold preparation, capped at the configuration count and the cores this process may actually run on; the ceiling is memory rather than CPU, so the default is derived from this machine rather than checked in — on this one "
            f"{kernel_exec.describe_kernel_threads(configs=len(conform.SETTLEMENT_CONFIGS))}, the booking being default's retained memo, every configuration's parked fold product, what each delta in flight adds beyond its own, and default's fold preparation in a slot of its own once every delta has one, and no less than every configuration's product read back at the rendezvous — which AMS_KERNEL_THREADS short-circuits and this flag beats in turn; the string replay after the build has its own width, --replay-threads"
        ),
    )
    parser.add_argument(
        "--scratch-beside-default",
        type=int,
        default=None,
        help=(
            "at most how many of the heaviest delta configurations the kernel's table build enumerates from scratch beside default instead of over default's memo once it has enumerated (the same tables, for more memory and CPU; the comment on kernel_exec.SCRATCH_PEAK_BYTES gives what it does to the wall time), taken in whole tiers of equal unlocking-rune count and capped at the delta count and one less than --kernel-threads; the default is derived from this machine at the build's width: as many as fit the memory with each booked at kernel_exec.SCRATCH_PEAK_BYTES — "
            f"{kernel_exec.deltas_from_scratch(len(conform.SETTLEMENT_CONFIGS), configs=len(conform.SETTLEMENT_CONFIGS))} on this one at the whole wave's width; the artifact cycle passes the count it derives beside gate:make-test's pool"
        ),
    )
    parser.add_argument(
        "--replay-threads",
        type=int,
        default=None,
        help=(
            "how many settlement configurations the string replay after the build walks at once, each in a crate process of its own beside the worker that absorbs its window memo on a full replay and as threads of one crate process on a narrowed one, capped at the configuration count and the cores this process may actually run on; a walk and its absorb are estimated together on their own rather than at the table build's width, so the default is derived from this machine — on this one "
            f"{describe_fit(kernel_exec.REPLAY_PEAK_BYTES, cap=len(conform.SETTLEMENT_CONFIGS))}, nothing co-resident since the build's process has exited — which AMS_REPLAY_THREADS short-circuits and this flag beats in turn"
        ),
    )
    args = parser.parse_args(argv)
    if args.scratch_beside_default is not None and args.scratch_beside_default < 0:
        parser.error("--scratch-beside-default must be zero or more")
    stated = args.jobs if args.jobs is not None else conform_jobs
    jobs = stated if stated > 1 else 1
    started = time.perf_counter()
    run_m1._build_phase_lines.clear()

    if args.gates_only:
        with _recording_failure(started):
            run_gates_only(out_dir=run_m1.OUT_DIR)
        return

    if args.kern_only:
        with _recording_failure(started):
            run_m1.kern_only_run(started=started)
        return

    if args.conform_only:
        from rebuild.tools.artifact_cycle import (
            conform_skip_fingerprint,
            conform_skip_files,
            evaluate_conform_gate,
        )
        from rebuild.tools.cycle_paths import CONFORM_GREEN

        def conform_key() -> str:
            return conform_skip_fingerprint(run_m1.REPO_ROOT, args.conform_max_length)

        before = conform_key()
        console.phase("run_font_conformance")
        start = time.perf_counter()
        try:
            conformance = run_font_conformance(max_length=args.conform_max_length, jobs=jobs)
        except SystemExit as refusal:
            _settle_green(CONFORM_GREEN, before, False, conform_key, "gate:conform")
            _record_cli_check(_failed_check("conform", str(refusal), REFUSED_STATUS), started)
            raise
        print(
            f"[t] run_font_conformance {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(conformance, indent=2))
        result = evaluate_conform_gate(conformance)
        _settle_green(
            CONFORM_GREEN,
            before,
            result.ok,
            conform_key,
            "gate:conform",
            files_of=lambda: conform_skip_files(run_m1.REPO_ROOT, args.conform_max_length),
        )
        _record_cli_check(result, started)
        if not result.ok:
            raise SystemExit("font conformance failed; see conform_summary.json")
        return

    with _recording_failure(started):
        run_m1.full_run(
            started=started,
            kernel_threads=args.kernel_threads,
            replay_threads=args.replay_threads,
            scratch_beside_default=args.scratch_beside_default,
            fresh_tables=args.fresh_tables,
        )


def _hard_exit(status: int) -> NoReturn:
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)


def _run_cli() -> None:
    try:
        main()
    except SystemExit as error:
        if error.code is None:
            status = 0
        elif isinstance(error.code, int):
            status = error.code
        else:
            sys.stdout.flush()
            print(error.code, file=sys.stderr)
            status = 1
        _hard_exit(status)
    _hard_exit(0)
