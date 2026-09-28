"""The deep form of gate:conform: the same exhaustive font-versus-settlement sweep as the per-edit sweep, at maximum length 5 by default (`--max-length` refuses anything below the per-edit sweep's 4). The per-edit sweep shapes every text up to four letters and checks what only shaping the compiled font can test: HarfBuzz's application semantics over the rule shapes the lookup contains. Whether the six-slot window is sufficient for the texts the tables were built for is checked by the crate's string replay, which `run_m1` runs on every build. This tool asks the shaper's question at a depth the per-edit sweep cannot afford, over texts long enough to reach a letter's fourth lookahead slot, which no per-edit sweep text reaches.

It runs on demand (`make conform-deep`), not per edit. The key of its green record (`artifact_cycle.deep_sweep_skip_lines`) is the set of behavior classes the build enumerated from the emitted lookup (`emit_gsub.behavior_classes`), the font-compilation code, and the uharfbuzz version. It leaves out the runes and M1.otf, so a rune edit that changes many rules but adds no new rule shape leaves the sweep current. When a build emits a new shape, or the compilation code or the shaper changes, the key changes and the cycle reports the sweep as `due` once per pass. The per-edit sweep's key is the same lines plus its maximum length (`artifact_cycle.conform_skip_fingerprint`). No gate depends on this sweep: a due deep sweep means it should be run, and the cycle does not fail.

The per-edit sweep's split-buffer check (every text split at a boundary shapes the same as its segments shaped alone) runs at this depth too, and this is the only place it covers texts longer than the per-edit sweep's maximum length, since no build step shapes a length-5 text. The ZWNJ glyph's own properties (zero advance, no ink) need no depth: read-back checks them in the font bytes on every build.

Each acceptance configuration runs `conform.conformance_config_worker` in a spawn process of its own (`run_sweep`), one configuration per process, so no configuration starts in a worker that still holds what an earlier one allocated. A settlement configuration's walk shares no memo file at this depth and keeps every distinct window it settled until its last text, so a worker's windows grow through the whole walk, and its footprint peaks at the window dict's last doubling late in the walk, when it briefly holds the old and new tables together. The width is therefore fixed before anything is spawned, from the windows each worker holds at its end, priced so that it covers that peak: `window_bound` counts the windows a settlement worker can hold at the requested maximum length, `settlement_worker_bytes` prices them, and `sweep_width` fits that many workers into the machine's memory. When even one settlement worker does not fit, `memory_shortfall` says so before anything is spawned: a warning when it exceeds the memory less the reserve, and a refusal without a stated width when it exceeds the machine's memory in all. The ss10 overlay holds no windows, only what every worker holds before its walk (`DEEP_SWEEP_BASE_BYTES`). The parent holds the spec, the glyph inventory and the guard verdicts, well inside the reserve, as the per-edit sweep's controller does. Each worker returns its peak footprint (`peak_rss.peak_footprint_bytes`), and the run's check line in the cycle-timings journal records every worker's peak beside its estimate, so a real run can be held against the estimate.

A green run also refreshes gate:conform's green record, when the per-edit sweep's key did not change during the run, because an exhaustive sweep at depth N covers every text the per-edit sweep at depth 4 shapes. The next cycle can then skip the per-edit sweep. At or past the deep replay's maximum length it also refreshes the deep replay's record (`rebuild.tools.deep_replay`), because it settles every text it shapes against the tables in the font. That record holds the rune digests read before the sweep started, which are the runes the swept font was built from, and the refresh is skipped when a rune changed while the sweep ran.

Run as: uv run python -m rebuild.tools.deep_sweep, or through `make conform-deep`.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.pipeline import conform, fingerprint, kernel_exec, run_m1
from rebuild.pipeline.model import ResolvedSpec
from rebuild.tools import console, cycle_paths, memory_budget, peak_rss
from rebuild.tools.artifact_cycle import (
    CONFORM_MAX_LENGTH_DEFAULT,
    DEEP_REPLAY_MAX_LENGTH_DEFAULT,
    DEEP_SWEEP_MAX_LENGTH_DEFAULT,
    clear_contradicted_green,
    conform_skip_files,
    conform_skip_fingerprint,
    deep_sweep_skip_files,
    deep_sweep_skip_fingerprint,
    deep_sweep_status,
    record_deep_replay_green,
    record_deep_sweep_green,
    record_green,
)
from rebuild.tools.cycle_timings import CheckResult, record_check

SUMMARY_NAME = "deep_sweep_summary.json"

CHECK = "conform-deep"

JOBS_ENV = "AMS_DEEP_SWEEP_JOBS"

# What one settlement worker holds per window at its peak, the variable term of its need (`settlement_worker_bytes`). The walk keeps `conform._SettledWindowWalk.windows`, a dict from a six-label key tuple to a shared outcome, and shares no settle memo at a deep length, so it holds one key tuple and one dict entry for every distinct window it settled, until its walk ends. Labels and outcomes are interned and shared, so nothing else grows with the windows. The dict doubles its tables when it passes two thirds of its slots, and for that moment it holds the old tables and the new ones together, so a worker's peak is that transient at the last doubling, not its size at the end of the walk. Per window held, the peak is highest when the walk ends just past a doubling.
# Measured on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) on the alphabet with ·Ye, reading footprint (`peak_rss.peak_footprint_bytes`) with nothing else running and no swap in use, by the probe `var/keep/issue-480/measure_worker.py`, whose logs are beside it. Every acceptance configuration at maximum length 4, each in a fresh spawn worker with no settle memo, peaks at 0.662 to 0.701 GB over 2,909,666 to 2,911,159 windows (`len4-full/`). `default` alone at maximum length 5, walked without shaping, peaks at 16.93 GB at its doubling past 89,478,485 windows, from 11.41 GB just before it, and ends at 14.75 GB over 96,040,858 windows (`len5-walk-default/`). Shaping adds nothing per window: at 2,058,625 windows the full length-4 worker reads 0.42 GB against the walk's 0.49 GB. That walk ends just past a doubling, so its peak is the worst case per window. The 9.86 to 9.90 GB a worker that `make conform-deep` was seen holding partway through a run swapping at six jobs (issue #480) is a lower bound on the same peak, read hours before the walk's end.
# The line through the two highest peaks, 0.701 GB at 2,911,159 windows and 16.93 GB at 89,478,486, has a slope of 187.4 bytes a window over a base of 0.156 GB. The constant is the slope plus a quarter, rounded up to ten bytes, and DEEP_SWEEP_BASE_BYTES is the base plus a quarter, rounded up to the tenth, because a cost that is too low pushes the machine into swap. `window_bound` overcounts on top of that, 3,304,968 against 2,909,666 at maximum length 4 and 110,020,785 against 96,040,858 at 5, so a settlement worker at maximum length 5 is estimated at 26.60 GB against the 16.93 GB measured, and both fleet machines sweep one settlement configuration at a time, which `rebuild/test_deep_sweep.py` checks. The window count follows the alphabet through `window_bound`, so re-measure only when the window key or what the walk holds per window changes shape, or `conform.TEXT_CHUNK` grows.
DEEP_SWEEP_WINDOW_BYTES = 240

# What a worker holds before its walk holds a window: its interpreter, the spec, a HarfBuzz `Shaper` over M1.otf, the glyph names and anchors, the guard verdicts the parent passes, and a chunk of `conform.TEXT_CHUNK` texts in flight. It is a settlement worker's fixed term and all the ss10 overlay's worker holds, since the overlay walks nothing; that worker peaks at 0.045 GB in the length-4 measurement. DEEP_SWEEP_WINDOW_BYTES's comment derives it.
DEEP_SWEEP_BASE_BYTES = 200_000_000


@dataclass(frozen=True)
class SweepPlan:
    """What the sweep needs before it spawns anything: the spec, the glyph inventory the workers name settled cells with, the window bound for one settlement worker at the requested maximum length, and how long that bound took to compute."""

    spec: ResolvedSpec
    glyphs: Mapping
    windows: int
    bound_seconds: float


def window_bound(
    max_length: int, tokens: Mapping[str, int], boundaries: int, cells: Mapping[str, int]
) -> int:
    """Return an upper bound on the windows one settlement configuration's walk holds at the end of a sweep to `max_length`. `tokens` maps every letter token (each letter, and each ligature rune) to the characters it spans, `boundaries` is the number of boundary characters, and `cells` maps a token to the number of cells its family has in the glyph inventory; a token it leaves out is bounded by its lefts alone.

    A window is the walk's memo key (`conform._SettledWindowWalk`): a letter position's label, its left, and its four right slots (`conform._window_rights`). Counting the keys exactly means settling every window, which is the sweep's own work, so this bounds the count in closed form over token strings. Split a key into its ask, the label and the right slots, and its left. The ask is fixed by the tokens it spans: the letter token at the position, up to four letter tokens after it, and what ends the slots early, the end of the text (`#EDGE`) or a boundary (after which they read `#NA`). Every such token string is counted whether or not formation produces it, and the configuration's renaming maps labels one to one, so both can only merge asks. If an ask spans c characters, at most `max_length - c` characters come before it.

    The left is `#EDGE` when nothing comes before the ask, a boundary's label when a boundary does, and otherwise the settled cell of the letter token x just before it. x's window is x's label, x's left, and x's right slots, which are the ask's first four slots, so with the ask fixed x's outcome depends only on x's left. When x starts the text its left is `#EDGE` and it has one outcome. Otherwise it has at most as many outcomes as it has possible lefts, and at most as many as its family has cells. So with p characters available before the ask, the lefts number at most `lefts(p)`: none for negative p, one for p = 0, and for p ≥ 1 one for `#EDGE`, one per boundary, and for each token x of length ℓ ≤ p, one when ℓ = p and `min(cells[x], lefts(p - ℓ))` otherwise. The bound is the sum over asks of `lefts(max_length - c)`.

    It is tightest where the walk holds most: an ask that fills the maximum length has only `#EDGE` for its left, and one a character short has one left per token that can precede it. The slack is in the shorter asks, where a family's cell count stands in for the cells one right context allows; DEEP_SWEEP_WINDOW_BYTES's comment compares the bound with exact counts. The arithmetic walks a table indexed by character length, so it takes well under a millisecond at any maximum length.
    """

    @cache
    def lefts(prefix: int) -> int:
        if prefix < 0:
            return 0
        if prefix == 0:
            return 1
        total = 1 + boundaries
        for token, length in tokens.items():
            if length == prefix:
                total += 1
            elif length < prefix:
                ceiling = cells.get(token)
                total += lefts(prefix - length) if ceiling is None else min(ceiling, lefts(prefix - length))
        return total

    strings: list[Counter[int]] = [Counter({0: 1})]
    for _ in range(5):
        longer: Counter[int] = Counter()
        for span, count in strings[-1].items():
            for length in tokens.values():
                if span + length <= max_length:
                    longer[span + length] += count
        strings.append(longer)
    bound = 0
    for letters in range(1, 6):
        for span, count in strings[letters].items():
            bound += count * lefts(max_length - span)
            if letters < 5:
                bound += count * boundaries * lefts(max_length - span - 1)
    return bound


def settlement_window_bound(spec: ResolvedSpec, glyphs: Mapping, max_length: int) -> int:
    """Return `window_bound` for this spec and glyph inventory: every rune with a code point is a one-character letter token, every ligature rune a token as long as its sequence, the boundary tokens are the registry's, and a token's cells are the inventory's cells of its family."""
    tokens = {
        name: len(rune.sequence) if rune.sequence else 1
        for name, rune in spec.runes.items()
        if rune.sequence or rune.codepoint is not None
    }
    cells = Counter(cell.rune for cell in glyphs)
    return window_bound(max_length, tokens, len(spec.registry.boundary_tokens), cells)


def settlement_worker_bytes(windows: int) -> int:
    """Return what one settlement worker needs at the peak of a walk that holds `windows` windows at its end: DEEP_SWEEP_BASE_BYTES plus DEEP_SWEEP_WINDOW_BYTES a window."""
    return DEEP_SWEEP_BASE_BYTES + windows * DEEP_SWEEP_WINDOW_BYTES


def _fit_terms(settlement_bytes: int, ncores: int | None) -> tuple[int, int, int]:
    """Return the settlement worker's need, the overlay worker's need subtracted as co-resident, and the cap on settlement workers: the smaller of the settlement configuration count and the usable cores."""
    cores = ncores or memory_budget.usable_cores()
    return settlement_bytes, DEEP_SWEEP_BASE_BYTES, min(cores, len(conform.SETTLEMENT_CONFIGS))


def _overlay_slot(settling: int, ncores: int | None) -> bool:
    cores = ncores or memory_budget.usable_cores()
    return settling == len(conform.SETTLEMENT_CONFIGS) and cores > settling


def sweep_width(settlement_bytes: int, *, ncores: int | None = None, total_bytes: int | None = None) -> int:
    """Return how many configurations sweep at once. The settlement workers are `memory_budget.how_many_fit` over `settlement_bytes`, with the overlay worker's need subtracted as co-resident, capped at the settlement configurations and the cores, and floored at one. When every settlement configuration fits and a core is left, the overlay gets a slot of its own; otherwise it runs first in one of the settlement workers' slots (`run_sweep` submits it first) and the width is the settlement workers alone. Either way any set of configurations that can run together fits: every settlement worker at once beside the overlay, or at most the width's worth of them with the overlay's need subtracted anyway. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine."""
    per_unit, overlay, cap = _fit_terms(settlement_bytes, ncores)
    settling = memory_budget.how_many_fit(
        per_unit, coresident_bytes=overlay, cap=cap, total_bytes=total_bytes
    )
    return settling + 1 if _overlay_slot(settling, ncores) else settling


def sweep_width_derivation(
    settlement_bytes: int, *, ncores: int | None = None, total_bytes: int | None = None
) -> str:
    """Return `sweep_width`'s arithmetic as a clause for the plan line: `memory_budget.describe_fit` over the same terms, then where the overlay runs."""
    per_unit, overlay, cap = _fit_terms(settlement_bytes, ncores)
    settling = memory_budget.how_many_fit(
        per_unit, coresident_bytes=overlay, cap=cap, total_bytes=total_bytes
    )
    fit = memory_budget.describe_fit(per_unit, coresident_bytes=overlay, cap=cap, total_bytes=total_bytes)
    where = (
        "gets a slot of its own" if _overlay_slot(settling, ncores) else "runs first in one of their slots"
    )
    return f"settlement workers: {fit}; the ss10 overlay {where}"


def memory_shortfall(settlement_bytes: int, *, total_bytes: int | None = None) -> tuple[bool, str] | None:
    """Return None when one settlement worker's estimate beside the overlay's fits this machine's memory less the reserve, the budget `sweep_width` divides. Otherwise the floor at one, not the memory, set the width, and this returns whether to refuse without a stated width and the sentence that says why. The run is refused when that need exceeds the machine's total memory, which no reserve or margin in the estimate can make fit, and only warned about when it exceeds the budget alone, since the estimate runs above the measured peak (DEEP_SWEEP_WINDOW_BYTES's comment) and the worker may still fit. `total_bytes` is a keyword so a test can ask about an invented machine."""
    total = memory_budget.total_memory_bytes() if total_bytes is None else total_bytes
    reserve = memory_budget.os_reserve_bytes(total_bytes=total)
    need = settlement_bytes + DEEP_SWEEP_BASE_BYTES
    if need <= total - reserve:
        return None
    gb = peak_rss.format_gb
    estimate = f"one settlement worker is estimated at {gb(settlement_bytes)} GB beside the overlay's {gb(DEEP_SWEEP_BASE_BYTES)} GB"
    if need > total:
        return True, (
            f"{estimate}, more than this machine's {gb(total)} GB in all, so the run would swap for its whole length"
        )
    return False, (
        f"{estimate}, more than this machine's {gb(total)} GB less its reserve of {gb(reserve)} GB, so the width is floored at one and the run may swap"
    )


def stated_jobs() -> int | None:
    """Return the width `AMS_DEEP_SWEEP_JOBS` states, floored at one, or None when it is unset. A value that is not a bare count raises, as `deep_replay.replay_threads` does for its own variable."""
    stated = os.environ.get(JOBS_ENV)
    if stated is None:
        return None
    try:
        return max(1, int(stated))
    except ValueError:
        raise RuntimeError(
            f"{JOBS_ENV}={stated!r} is not a width: it takes a bare decimal count of configurations to sweep at once"
        ) from None


def tables_stamped() -> bool:
    """Return whether the serialized enumeration under rebuild/out/m1 was produced from the sources on disk, by the same `tables_inputs()` stamp `run_font_conformance` checks. It checks the artifacts themselves, not a record of a past run, so tables from a `--gates-only` pass or from another machine qualify the same way as a local build."""
    return run_m1.serialized_tables(run_m1.OUT_DIR, run_m1.tables_inputs()) is not None


def record_key() -> str:
    """Return this build's record key, or exit when the sweep would be meaningless. Without a behavior-class sidecar there is no key to record a green under. With a stale tables stamp, the M1.otf on disk is not the font the runes on disk describe."""
    fingerprint = deep_sweep_skip_fingerprint(ROOT)
    if fingerprint is None:
        raise SystemExit(
            "no behavior-class sidecar under rebuild/out/m1 — run `make artifact-cycle` first so a build can leave one to key this sweep on"
        )
    if not tables_stamped():
        raise SystemExit(
            "the M1 artifacts are stale relative to the runes on disk — run `make artifact-cycle` (or `make review-cycle`) first, so the deep sweep never shapes a font the sources have outgrown"
        )
    return fingerprint


def plan_sweep(max_length: int) -> SweepPlan:
    """Load what the sweep needs and bound one settlement worker's windows at `max_length`. The tables are read only for the glyph inventory (`run_m1.mint_cell_glyphs`) and released once it is minted."""
    from rebuild.pipeline.spec_load import load_default_spec

    inputs = run_m1.tables_inputs()
    spec = load_default_spec()
    serialized = run_m1.serialized_tables(run_m1.OUT_DIR, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {run_m1.OUT_DIR} are missing, unreadable, or were built from other sources than the ones on disk — run `make artifact-cycle` first"
        )
    glyphs = run_m1.mint_cell_glyphs(spec, serialized)
    del serialized
    started = time.perf_counter()
    windows = settlement_window_bound(spec, glyphs, max_length)
    return SweepPlan(spec=spec, glyphs=glyphs, windows=windows, bound_seconds=time.perf_counter() - started)


def _config_worker(spec, font_path: Path, config: str, max_length: int, glyphs, guard_verdicts):
    """Run one configuration's sweep in its own process and return the result with the process's peak footprint, read just before it returns."""
    result = conform.conformance_config_worker(spec, font_path, config, max_length, glyphs, guard_verdicts)
    peak = peak_rss.peak_footprint_bytes()
    return result, peak if peak is not None else peak_rss.peak_rss_self_bytes()


def run_sweep(plan: SweepPlan, max_length: int, jobs: int) -> tuple[dict, dict[str, int]]:
    """Sweep every acceptance configuration at `max_length`, `jobs` at a time, one spawn process per configuration, and return the summary `run_m1.run_font_conformance` returns with each configuration's worker peak. The overlay is submitted first, so when it shares a slot it finishes before a settlement configuration takes that slot. The §5.7 guard verdicts are computed once here and passed to every worker."""
    kernel_exec.ensure_built()
    guard_verdicts = kernel_exec.guard_sweep(plan.spec)
    font_path = run_m1.OUT_DIR / "M1.otf"
    order = conform.OVERLAY_CONFIGS + conform.SETTLEMENT_CONFIGS
    collected: dict[str, conform.ConformanceConfigResult] = {}
    peaks: dict[str, int] = {}
    with ProcessPoolExecutor(
        max_workers=jobs, mp_context=multiprocessing.get_context("spawn"), max_tasks_per_child=1
    ) as pool:
        futures = [
            pool.submit(_config_worker, plan.spec, font_path, config, max_length, plan.glyphs, guard_verdicts)
            for config in order
        ]
        for future in as_completed(futures):
            result, peak = future.result()
            collected[result.config] = result
            peaks[result.config] = peak
            console.progress(len(collected), len(order), "configurations")
    report = conform.merge_conformance_results(
        font_path, [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
    )
    report.write(run_m1.OUT_DIR / SUMMARY_NAME)
    summary: dict = {
        "sequences": report.sequences,
        "shaping_runs": report.shaping_runs,
        "divergences": len(report.divergences),
        "pass": report.passed,
        "notes": report.notes,
    }
    for divergence in report.divergences[:20]:
        summary.setdefault("divergence_exemplars", []).append(
            f"{divergence.config} {':'.join(f'{ord(ch):04X}' for ch in divergence.text)} position {divergence.position} [{divergence.kind}] expected {divergence.expected} got {divergence.got}"
        )
    return summary, peaks


def refresh_deep_replay(max_length: int, runes: dict[str, str]) -> None:
    """Record the deep replay as green at `max_length` for every rune at the digest in `runes`, the snapshot taken before the sweep started, since a green sweep at that depth settled every text that names any of them."""
    from rebuild.pipeline.spec_load import load_default_spec

    record_deep_replay_green(runes, max_length, run_m1.replay_structure_stamp(load_default_spec()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deep form of the font-vs-settle conformance sweep and record its green."
    )
    parser.add_argument(
        "--max-length",
        "--horizon",
        type=int,
        default=DEEP_SWEEP_MAX_LENGTH_DEFAULT,
        help=f"exhaustive sweep length (default {DEEP_SWEEP_MAX_LENGTH_DEFAULT}); anything below the per-edit sweep's own {CONFORM_MAX_LENGTH_DEFAULT} is refused, since the per-edit sweep already covers that on every edit",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"for debugging only: how many configurations sweep at once, overriding {JOBS_ENV} and the width this machine's memory fits at --max-length; narrowed to the acceptance configuration count and never narrowed by memory; it also starts a run whose one settlement worker exceeds the machine's memory, which is otherwise refused",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print whether the deep sweep is current or due and exit, sweeping nothing (exit 0 when current)",
    )
    args = parser.parse_args(argv)

    if args.status:
        status, note = deep_sweep_status(ROOT, args.max_length)
        print(f"deep sweep: {status} — {note}")
        return 0 if status == "current" else 1

    if args.max_length < CONFORM_MAX_LENGTH_DEFAULT:
        raise SystemExit(
            f"--max-length {args.max_length} is shorter than the per-edit sweep's {CONFORM_MAX_LENGTH_DEFAULT}, which already covers that depth on every edit"
        )
    runes = fingerprint.rune_digests(ROOT)
    deep_key = record_key()
    conform_key = conform_skip_fingerprint(ROOT, CONFORM_MAX_LENGTH_DEFAULT)
    stated = args.jobs if args.jobs is not None else stated_jobs()
    plan = plan_sweep(args.max_length)
    settlement_bytes = settlement_worker_bytes(plan.windows)
    derived = sweep_width(settlement_bytes)
    configs = len(conform.ACCEPTANCE_CONFIGS)
    jobs = min(max(1, stated), configs) if stated is not None else derived
    source = (
        f"stated by {'--jobs' if args.jobs is not None else JOBS_ENV}, where this machine's memory fits {derived}"
        if stated is not None
        else "from this machine's memory"
    )
    print(
        f"deep sweep: maximum length {args.max_length} over every acceptance configuration at {jobs} jobs {source}, one process per configuration (the ss10 overlay keeps its own maximum length); "
        f"a settlement worker holds at most {plan.windows} windows (bound in {plan.bound_seconds * 1000:.1f} ms), {peak_rss.format_gb(settlement_bytes)} GB at {DEEP_SWEEP_WINDOW_BYTES} bytes each beside {peak_rss.format_gb(DEEP_SWEEP_BASE_BYTES)} GB; "
        f"{sweep_width_derivation(settlement_bytes)}",
        flush=True,
    )
    shortfall = memory_shortfall(settlement_bytes)
    if shortfall is not None:
        refuse, sentence = shortfall
        if refuse and stated is None:
            raise SystemExit(f"deep sweep: {sentence}; pass --jobs 1 or set {JOBS_ENV}=1 to run it anyway")
        console.warn(f"deep sweep: {sentence}")
    started = time.perf_counter()
    summary, peaks = run_sweep(plan, args.max_length, jobs)
    elapsed = time.perf_counter() - started
    print(json.dumps(summary, indent=2))
    estimates = {
        config: settlement_bytes if config in conform.SETTLEMENT_CONFIGS else DEEP_SWEEP_BASE_BYTES
        for config in conform.ACCEPTANCE_CONFIGS
    }
    for config in conform.ACCEPTANCE_CONFIGS:
        if config in peaks:
            print(
                f"deep sweep[{config}]: peak footprint {peak_rss.format_gb(peaks[config])} GB against an estimate of {peak_rss.format_gb(estimates[config])} GB",
                flush=True,
            )
    print(f"[t] {CHECK} {elapsed:.1f}s", flush=True)
    green = bool(summary["pass"]) and not summary["divergences"]
    failures = (
        []
        if green
        else [f"{summary['divergences']} font-vs-settle divergence(s) at maximum length {args.max_length}"]
    )
    record_check(
        CheckResult(
            check=CHECK,
            outcome="green" if green else "red",
            status="green" if green else f"FAILED ({summary['divergences']} divergences)",
            failures=failures,
            failed_ids=[],
        ),
        argv=list(argv) if argv is not None else sys.argv[1:],
        elapsed_s=elapsed,
        peak_rss_bytes=peak_rss.peak_rss_children_bytes(),
        worker_peak_footprint_bytes=peaks,
        worker_estimate_bytes=estimates,
    )

    if not green:
        clear_contradicted_green(cycle_paths.DEEP_SWEEP_GREEN, deep_key)
        print(
            f"deep sweep: {summary['divergences']} font-vs-settle divergence(s) at maximum length {args.max_length}; see {SUMMARY_NAME}",
            file=sys.stderr,
        )
        return 1

    if deep_sweep_skip_fingerprint(ROOT) != deep_key:
        print("deep sweep: green, but its inputs changed while it ran — green not recorded", flush=True)
        return 0
    record_deep_sweep_green(deep_key, args.max_length, files=deep_sweep_skip_files(ROOT))
    print(
        f"deep sweep: green at maximum length {args.max_length} — recorded in {cycle_paths.DEEP_SWEEP_GREEN.name}",
        flush=True,
    )
    if args.max_length >= DEEP_REPLAY_MAX_LENGTH_DEFAULT:
        if fingerprint.rune_digests(ROOT) != runes:
            print(
                "deep replay: not recorded — the runes changed while the sweep ran, so the swept font does not describe the runes on disk",
                flush=True,
            )
        else:
            refresh_deep_replay(args.max_length, runes)
            print(
                f"deep replay: green too — every text up to length {args.max_length} was settled here, so nothing is left for `make replay-deep` to walk",
                flush=True,
            )
    if (
        args.max_length >= CONFORM_MAX_LENGTH_DEFAULT
        and conform_skip_fingerprint(ROOT, CONFORM_MAX_LENGTH_DEFAULT) == conform_key
    ):
        record_green(
            cycle_paths.CONFORM_GREEN, conform_key, files=conform_skip_files(ROOT, CONFORM_MAX_LENGTH_DEFAULT)
        )
        print(
            f"gate:conform: green too — every per-edit sweep text up to length {CONFORM_MAX_LENGTH_DEFAULT} was swept here, so the next cycle skips it",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
