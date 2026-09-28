"""The deep form of gate:conform: the same exhaustive font-versus-settlement sweep as the per-edit sweep, at maximum length 5 by default (`--max-length` refuses anything below the per-edit sweep's 4). The per-edit sweep shapes every text up to four letters and checks what only shaping the compiled font can test: HarfBuzz's application semantics over the rule shapes the lookup contains. Whether the six-slot window is sufficient for the texts the tables were built for is checked by the crate's string replay, which `run_m1` runs on every build. This tool asks the shaper's question at a depth the per-edit sweep cannot afford, over texts long enough to reach a letter's fourth lookahead slot, which no per-edit sweep text reaches.

It runs on demand (`make conform-deep`), not per edit. The key of its green record (`artifact_cycle.deep_sweep_skip_lines`) is the set of behavior classes the build enumerated from the emitted lookup (`emit_gsub.behavior_classes`), the font-compilation code, and the uharfbuzz version. It leaves out the runes and M1.otf, so a rune edit that changes many rules but adds no new rule shape leaves the sweep current. When a build emits a new shape, or the compilation code or the shaper changes, the key changes and the cycle reports the sweep as `due` once per pass. The per-edit sweep's key is the same lines plus its maximum length (`artifact_cycle.conform_skip_fingerprint`). No gate depends on this sweep: a due deep sweep means it should be run, and the cycle does not fail.

The per-edit sweep's split-buffer check (every text split at a boundary shapes the same as its segments shaped alone) runs at this depth too, and this is the only place it covers texts longer than the per-edit sweep's maximum length, since no build step shapes a length-5 text. The ZWNJ glyph's own properties (zero advance, no ink) need no depth: read-back checks them in the font bytes on every build.

Each acceptance configuration runs `conform.conformance_config_worker` in a spawn process of its own (`run_sweep`), one configuration per process, so no configuration starts in a worker that still holds what an earlier one allocated. A settlement configuration's walk shares no memo file at this depth and keeps every distinct window it settles except the pinned ones (`conform._SettledWindowWalk`, `horizon`) until its last text, so a worker's windows grow through the whole walk, and its footprint peaks at the window dict's last doubling late in the walk, when it briefly holds the old and new tables together. The width is therefore fixed before anything is spawned, from the windows each worker holds at its end, priced so that it covers that peak: `window_bound` counts the windows a settlement worker can hold at the requested maximum length, `settlement_worker_bytes` prices them, and `sweep_width` fits that many workers into the machine's memory. The bound and its price count every window the walk settles, the pinned ones included, so the estimate is higher than what a worker holds. When even one settlement worker does not fit, `memory_shortfall` says so before anything is spawned: a warning when it exceeds the memory less the reserve, and a refusal without a stated width when it exceeds the machine's memory in all. The ss10 overlay holds no windows, only what every worker holds before its walk (`DEEP_SWEEP_BASE_BYTES`). The parent holds the spec, the glyph inventory and the guard verdicts, well inside the reserve, as the per-edit sweep's controller does. Each worker returns its peak footprint (`peak_rss.peak_footprint_bytes`), and the run's check line in the cycle-timings journal records every worker's peak beside its estimate, so a real run can be held against the estimate.

While the workers run, the parent prints a progress report every 20 minutes (`REPORT_SECONDS_DEFAULT`; `AMS_DEEP_SWEEP_REPORT_SECONDS` sets another interval for a debugging run). Each worker writes the count of texts it has shaped into its configuration's slot of a shared array after each chunk of `conform.TEXT_CHUNK` texts, and its pid and the clock time it started into two others; the arrays reach the spawn workers through the pool's initializer, the one way a spawn process can inherit shared memory. One slot has one writer and the parent only reads, so no lock is taken, and the hot loop pays one store per chunk. A worker prints nothing. The parent reads the slots every `SAMPLE_SECONDS`, and each report states, in total and then per configuration, the texts shaped out of the texts to shape (`config_texts`, counted before any worker starts), the time elapsed, the rate since the last report, the estimated finish, and each worker's footprint (`peak_rss.footprint_bytes`) with the machine's swap in use (`peak_rss.swap_used_bytes`). `SweepProgress` says how the estimate is computed.

The total is a `[progress] <k>/<n> texts` counter line in `console`'s protocol, and the rest of the report rides on the same line: `console.parse_line` reads the counter's two bare counts and takes everything after them as the unit, which the artifact cycle's console reprints verbatim after the counts, which it formats with `console.fmt_count`. The two counts stay bare because the parser reads only digits there; every other count and duration in the report goes through `fmt_count` and `fmt_duration`. Each configuration gets a `deep sweep[<config>]: ` line after it, which is not a protocol line, so the cycle's console logs it without surfacing it, as it does the per-configuration peak lines at the end, while `tail -f` shows every line. Making them counter lines too would not show them: the console keeps only the latest counter of a burst and surfaces it a heartbeat later.

A green run also refreshes gate:conform's green record, when the per-edit sweep's key did not change during the run, because an exhaustive sweep at depth N covers every text the per-edit sweep at depth 4 shapes. The next cycle can then skip the per-edit sweep. At or past the deep replay's maximum length it also refreshes the deep replay's record (`rebuild.tools.deep_replay`), because it settles every text it shapes against the tables in the font. That record holds the rune digests read before the sweep started, which are the runes the swept font was built from, and the refresh is skipped when a rune changed while the sweep ran.

Run as: uv run python -m rebuild.tools.deep_sweep, or through `make conform-deep`.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import multiprocessing
import os
import sys
import time
from collections import Counter, deque
from collections.abc import Callable, Collection, Mapping, MutableSequence, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
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

REPORT_ENV = "AMS_DEEP_SWEEP_REPORT_SECONDS"

REPORT_SECONDS_DEFAULT = 20 * 60.0

# How often the parent reads the workers' counters between reports. A count is dated to the first reading that shows it, so it is late by at most this much, against walks that run for hours and chunks that take seconds. A configuration's start is the clock time its worker recorded, so a worker that finishes between two readings, as the ss10 overlay's does, still has its duration.
SAMPLE_SECONDS = 5.0

# The share of a configuration's texts that the rate its estimate uses spans (`smoothed_rate`). A walk's rate changes as it moves through the alphabet: it takes the letters in order in the first position, one after another across its longest texts, and the letters' rules cost different amounts to settle and shape. A tenth of the texts spans several first letters, so one costly letter moves the rate little, while the rate still trails the walk by only a twentieth of it, half the window.
RATE_WINDOW_SHARE = 0.1

# What one settlement worker holds per window at its peak, the variable term of its need (`settlement_worker_bytes`). The walk keeps `conform._SettledWindowWalk.windows`, a dict from a six-label key tuple to a shared outcome, and shares no settle memo at a deep length, so it holds one key tuple and one dict entry for every distinct window it settles except the pinned ones (`horizon`), until its walk ends. Labels and outcomes are interned and shared, so nothing else grows with the windows. The dict doubles its tables when it passes two thirds of its slots, and for that moment it holds the old tables and the new ones together, so a worker's peak is that transient at the last doubling, not its size at the end of the walk. Per window held, the peak is highest when the walk ends just past a doubling.
# Measured on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) on the alphabet with ·Ye, reading footprint (`peak_rss.peak_footprint_bytes`) with nothing else running and no swap in use, by the probe `var/keep/issue-480/measure_worker.py`, whose logs are beside it. Every acceptance configuration at maximum length 4, each in a fresh spawn worker with no settle memo, peaks at 0.662 to 0.701 GB over 2,909,666 to 2,911,159 windows (`len4-full/`). `default` alone at maximum length 5, walked without shaping, peaks at 16.93 GB at its doubling past 89,478,485 windows, from 11.41 GB just before it, and ends at 14.75 GB over 96,040,858 windows (`len5-walk-default/`). Shaping adds nothing per window: at 2,058,625 windows the full length-4 worker reads 0.42 GB against the walk's 0.49 GB. That walk ends just past a doubling, so its peak is the worst case per window. These measurements are of a walk that kept every window, pinned ones included, and `window_bound` counts all of them, so the price is an upper bound on what a worker holds. The 9.86 to 9.90 GB a worker that `make conform-deep` was seen holding partway through a run swapping at six jobs (issue #480) is a lower bound on the same peak, read hours before the walk's end.
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


def report_seconds() -> float:
    """Return the interval between progress reports: what `AMS_DEEP_SWEEP_REPORT_SECONDS` states, or REPORT_SECONDS_DEFAULT when it is unset. A value that is not a positive, finite decimal number of seconds raises, as `stated_jobs` does for its own variable."""
    stated = os.environ.get(REPORT_ENV)
    if stated is None:
        return REPORT_SECONDS_DEFAULT
    try:
        seconds = float(stated)
    except ValueError:
        seconds = math.nan
    if not (math.isfinite(seconds) and seconds > 0):
        raise RuntimeError(
            f"{REPORT_ENV}={stated!r} is not an interval: it takes a positive decimal number of seconds between progress reports"
        )
    return seconds


def config_texts(spec: ResolvedSpec, max_length: int) -> dict[str, int]:
    """Return how many texts each acceptance configuration's worker shapes: every text of one letter up to its maximum length over the spec's alphabet, which is `max_length` for a settlement configuration and `conform.OVERLAY_MAX_LENGTH` for an overlay. It is the `sequences` count each worker returns, known before any worker starts."""
    size = len(conform.spec_alphabet(spec))
    return {
        config: sum(
            size**length
            for length in range(
                1, (conform.OVERLAY_MAX_LENGTH if config in conform.OVERLAY_CONFIGS else max_length) + 1
            )
        )
        for config in conform.ACCEPTANCE_CONFIGS
    }


def smoothed_rate(samples: Sequence[tuple[float, int]], window: float) -> float | None:
    """Return the texts a second between the newest sample and the newest one at least `window` texts before it, or the oldest sample when none is that far back, or None when the samples span no time or no texts. A sample is a (seconds, texts shaped) reading, oldest first."""
    if len(samples) < 2:
        return None
    end_time, end_done = samples[-1]
    start_time, start_done = samples[0]
    for moment, done in samples:
        if done > end_done - window:
            break
        start_time, start_done = moment, done
    if end_time <= start_time or end_done <= start_done:
        return None
    return (end_done - start_done) / (end_time - start_time)


def schedule_finish(
    running: Mapping[str, float], queued: Sequence[tuple[str, float]], slots: int
) -> dict[str, float]:
    """Return the seconds from now at which each configuration finishes, given the seconds each running one has left, each queued one's whole duration in the order the pool takes them, and the pool's slots. Each queued configuration starts in the slot that frees first, a free slot at once, as `ProcessPoolExecutor` hands out work in submission order."""
    finishes = dict(running)
    slot_free = sorted(running.values()) + [0.0] * max(0, slots - len(running))
    heapq.heapify(slot_free)
    for config, seconds in queued:
        finishes[config] = heapq.heappop(slot_free) + seconds
        heapq.heappush(slot_free, finishes[config])
    return finishes


@dataclass
class _Walk:
    texts: int
    overlay: bool
    samples: deque[tuple[float, int]] = field(default_factory=deque)
    first: tuple[float, int] | None = None
    marked: tuple[float, int] | None = None
    finished: float | None = None

    @property
    def done(self) -> int:
        return self.samples[-1][1] if self.samples else 0

    @property
    def running(self) -> bool:
        return self.first is not None and self.finished is None


def _per_second(start: tuple[float, int], end: tuple[float, int]) -> float | None:
    return (end[1] - start[1]) / (end[0] - start[0]) if end[0] > start[0] else None


def _fmt_rate(rate: float | None) -> str:
    return "no rate yet" if rate is None else f"{console.fmt_count(round(rate))} texts/s"


def _fmt_finish(seconds: float | None, wall: float) -> str:
    if seconds is None:
        return "finish not yet estimated"
    return f"finishing in {console.fmt_duration(seconds)}, at {time.strftime('%a %H:%M', time.localtime(wall + seconds))}"


class SweepProgress:
    """The parent's record of each configuration's walk, and the progress report built from it. Every time is on the parent's monotonic clock; a worker's start, which the worker records on the wall clock, is converted to it at the reading that finds it (`run_sweep`).

    The estimate is a schedule, not the total rate extrapolated: the pool runs `slots` configurations at once and the settlement configurations can take more than one round, so a total rate would stand for configurations that have not started and misjudge when a round ends. A running configuration has its remaining texts left at its own rate, taken over the last `RATE_WINDOW_SHARE` of its texts (`smoothed_rate`), once it has shaped that many. A queued configuration takes its whole count at the rate of its kind (settlement or overlay): the texts of the configurations of that kind over their durations, a finished one's as it ran and a running one's elapsed time plus its remaining texts at its own rate. The kinds are kept apart because the overlay's texts are few and short, while all the settlement configurations walk the same texts. `schedule_finish` then places the queued configurations, in the order the pool takes them, into the slots as they free, and the run ends when the last slot does.

    A configuration that has shaped less than the window has a rate of its own that still carries its start, the worker's setup and the short texts first, so it takes its kind's rate too. A kind's rate counts only the configurations that are finished or have a rate over the whole window. Only when there are none, as while the first configuration of a kind covers its first window, does it count the rates the running ones have made so far. A configuration whose kind has no rate at all leaves the finish unestimated.
    """

    def __init__(
        self, texts: Mapping[str, int], overlays: Collection[str], slots: int, started: float
    ) -> None:
        self.walks = {
            config: _Walk(texts=count, overlay=config in overlays) for config, count in texts.items()
        }
        self.slots = slots
        self.started = started
        self.marked: tuple[float, int] | None = None

    def observe(self, config: str, done: int, now: float, began: float | None = None) -> None:
        """Record that `config`'s worker is running and has shaped `done` texts, as read at `now`, and that it started at `began` when that is known. A count is kept only when it changes, so it is dated to the first reading that shows it."""
        walk = self.walks[config]
        if walk.finished is not None:
            return
        if walk.first is None:
            walk.first = (now, done) if began is None else (min(began, now), 0)
            walk.samples.append(walk.first)
        if done == walk.done:
            return
        walk.samples.append((now, done))
        window = RATE_WINDOW_SHARE * walk.texts
        while len(walk.samples) > 2 and walk.samples[1][1] <= done - window:
            walk.samples.popleft()

    def finish(self, config: str, now: float, done: int, began: float | None = None) -> None:
        """Record that `config`'s worker returned at `now` having shaped `done` texts, and started at `began` when that is known and no reading saw it running."""
        walk = self.walks[config]
        if walk.first is None:
            walk.first = (now if began is None else min(began, now), 0)
        walk.samples.append((now, done))
        walk.finished = now

    def running(self, config: str) -> bool:
        return self.walks[config].running

    def _rate(self, walk: _Walk, *, whole_window: bool) -> float | None:
        """Return `walk`'s smoothed rate, or None when it has none, or when `whole_window` asks for a rate over the whole window and its samples do not span one yet."""
        window = RATE_WINDOW_SHARE * walk.texts
        if whole_window and not (walk.samples and walk.samples[0][1] <= walk.done - window):
            return None
        return smoothed_rate(walk.samples, window)

    def kind_rate(self, overlay: bool, now: float) -> float | None:
        """Return the texts a second of the configurations of one kind that have started, as the class docstring describes: their texts over their durations, a finished one's as it ran and a running one's elapsed time plus its remaining texts at its own rate. The running ones count only with a rate over the whole window, unless no configuration of the kind qualifies; the result is None when none has a rate at all."""
        for whole_window in (True, False):
            texts = seconds = 0.0
            for walk in self.walks.values():
                if walk.overlay != overlay or walk.first is None:
                    continue
                if walk.finished is not None:
                    duration = walk.finished - walk.first[0]
                else:
                    rate = self._rate(walk, whole_window=whole_window)
                    if rate is None:
                        continue
                    duration = now - walk.first[0] + (walk.texts - walk.done) / rate
                if duration > 0:
                    texts += walk.texts
                    seconds += duration
            if seconds > 0:
                return texts / seconds
        return None

    def estimate(self, now: float) -> tuple[dict[str, float | None], float | None]:
        """Return the seconds from `now` at which each unfinished configuration finishes, and at which the whole sweep does, by the schedule the class docstring describes. A configuration without an estimate maps to None; so does every queued one, and the sweep, when any unfinished configuration lacks one."""
        running: dict[str, float | None] = {}
        queued: list[tuple[str, float | None]] = []
        for config, walk in self.walks.items():
            if walk.finished is not None:
                continue
            if walk.first is None:
                rate = self.kind_rate(walk.overlay, now)
                queued.append((config, None if rate is None else walk.texts / rate))
                continue
            rate = self._rate(walk, whole_window=True) or self.kind_rate(walk.overlay, now)
            running[config] = None if rate is None else (walk.texts - walk.done) / rate
        known_running = {config: left for config, left in running.items() if left is not None}
        known_queued = [(config, seconds) for config, seconds in queued if seconds is not None]
        if len(known_running) < len(running) or len(known_queued) < len(queued):
            return {**running, **{config: None for config, _ in queued}}, None
        finishes = schedule_finish(known_running, known_queued, self.slots)
        return dict(finishes), max(finishes.values(), default=0.0)

    def report(
        self, now: float, wall: float, footprints: Mapping[str, int | None], swap: int | None
    ) -> list[str]:
        """Return one progress report as of `now`, with `wall` the clock time at `now` for the finish's time of day: the total counter line, then one line per configuration in submission order. `footprints` maps each running configuration to its worker's footprint, None where it could not be read, and `swap` is the machine's swap in use. Each rate since the last report is from this record's previous report, or from the start of the sweep or of the configuration when there was none."""
        finishes, total_finish = self.estimate(now)
        done = sum(walk.done for walk in self.walks.values())
        texts = sum(walk.texts for walk in self.walks.values())
        since = "since the start" if self.marked is None else "since the last report"
        readable = [footprint for footprint in footprints.values() if footprint is not None]
        workers = (
            f"{len(readable)} {'worker holds' if len(readable) == 1 else 'workers hold'} {peak_rss.format_gb(sum(readable))} GB"
            if readable
            else "no worker footprint readable"
        )
        held = "swap unreadable" if swap is None else f"{peak_rss.format_gb(swap)} GB of swap in use"
        lines = [
            f"{console.PROGRESS}{done}/{texts} texts, {console.fmt_duration(now - self.started)} elapsed, "
            f"{_fmt_rate(_per_second(self.marked or (self.started, 0), (now, done)))} {since}, "
            f"{_fmt_finish(total_finish, wall)}, {workers}, {held}"
        ]
        for config, walk in self.walks.items():
            counts = (
                f"deep sweep[{config}]: {console.fmt_count(walk.done)}/{console.fmt_count(walk.texts)} texts"
            )
            if walk.first is None:
                lines.append(f"{counts}, queued, {_fmt_finish(finishes.get(config), wall)}")
            elif walk.finished is not None:
                lines.append(f"{counts}, finished in {console.fmt_duration(walk.finished - walk.first[0])}")
            else:
                own_since = "since the last report" if walk.marked is not None else "since it started"
                rate = _per_second(walk.marked or walk.first, (now, walk.done))
                footprint = footprints.get(config)
                holds = (
                    "footprint unreadable"
                    if footprint is None
                    else f"holding {peak_rss.format_gb(footprint)} GB"
                )
                lines.append(
                    f"{counts}, running for {console.fmt_duration(now - walk.first[0])}, "
                    f"{_fmt_rate(rate)} {own_since}, {_fmt_finish(finishes.get(config), wall)}, {holds}"
                )
                walk.marked = (now, walk.done)
        self.marked = (now, done)
        return lines


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


_COUNTERS: tuple[MutableSequence[int], MutableSequence[int], MutableSequence[float]] | None = None


def _attach_counters(
    shaped: MutableSequence[int], pids: MutableSequence[int], began: MutableSequence[float]
) -> None:
    """The pool's initializer: keep the shared arrays a worker writes its count, pid, and start into."""
    global _COUNTERS
    _COUNTERS = (shaped, pids, began)


def _counter_writer(slot: int) -> Callable[[int], None] | None:
    """Record this process's start and pid in `slot` and return the function that stores its count of texts shaped there, or None outside a pool that attached the arrays. The start is written first, since the parent reads it once it sees the pid."""
    if _COUNTERS is None:
        return None
    shaped, pids, began = _COUNTERS
    began[slot] = time.time()
    pids[slot] = os.getpid()

    def store(count: int) -> None:
        shaped[slot] = count

    return store


def _config_worker(spec, font_path: Path, config: str, max_length: int, glyphs, guard_verdicts, slot: int):
    """Run one configuration's sweep in its own process, storing its count of texts shaped in its `slot` of the shared array after each chunk, and return the result with the process's peak footprint, read just before it returns."""
    result = conform.conformance_config_worker(
        spec, font_path, config, max_length, glyphs, guard_verdicts, progress=_counter_writer(slot)
    )
    peak = peak_rss.peak_footprint_bytes()
    return result, peak if peak is not None else peak_rss.peak_rss_self_bytes()


def run_sweep(
    plan: SweepPlan, max_length: int, jobs: int, report_every: float = REPORT_SECONDS_DEFAULT
) -> tuple[dict, dict[str, int]]:
    """Sweep every acceptance configuration at `max_length`, `jobs` at a time, one spawn process per configuration, and return the summary `run_m1.run_font_conformance` returns with each configuration's worker peak. The overlay is submitted first, so when it shares a slot it finishes before a settlement configuration takes that slot. The §5.7 guard verdicts are computed once here and passed to every worker. A progress report is printed every `report_every` seconds while any worker runs, as the module docstring describes."""
    kernel_exec.ensure_built()
    guard_verdicts = kernel_exec.guard_sweep(plan.spec)
    font_path = run_m1.OUT_DIR / "M1.otf"
    order = conform.OVERLAY_CONFIGS + conform.SETTLEMENT_CONFIGS
    texts = config_texts(plan.spec, max_length)
    collected: dict[str, conform.ConformanceConfigResult] = {}
    peaks: dict[str, int] = {}
    context = multiprocessing.get_context("spawn")
    shaped = context.RawArray("q", len(order))
    pids = context.RawArray("q", len(order))
    began = context.RawArray("d", len(order))
    progress = SweepProgress(
        {config: texts[config] for config in order}, conform.OVERLAY_CONFIGS, jobs, time.monotonic()
    )
    next_report = progress.started + report_every
    with ProcessPoolExecutor(
        max_workers=jobs,
        mp_context=context,
        max_tasks_per_child=1,
        initializer=_attach_counters,
        initargs=(shaped, pids, began),
    ) as pool:
        pending = {
            pool.submit(
                _config_worker, plan.spec, font_path, config, max_length, plan.glyphs, guard_verdicts, slot
            )
            for slot, config in enumerate(order)
        }
        while pending:
            timeout = max(0.0, min(SAMPLE_SECONDS, next_report - time.monotonic()))
            finished, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            now = time.monotonic()
            wall = time.time()
            starts = {
                config: now - max(0.0, wall - began[slot]) if began[slot] > 0 else None
                for slot, config in enumerate(order)
            }
            for slot, config in enumerate(order):
                if pids[slot]:
                    progress.observe(config, shaped[slot], now, starts[config])
            for future in finished:
                result, peak = future.result()
                collected[result.config] = result
                peaks[result.config] = peak
                progress.finish(result.config, now, result.sequences, starts[result.config])
                console.progress(len(collected), len(order), "configurations")
            if pending and now >= next_report:
                footprints = {
                    config: peak_rss.footprint_bytes(pids[slot])
                    for slot, config in enumerate(order)
                    if progress.running(config)
                }
                for line in progress.report(now, wall, footprints, peak_rss.swap_used_bytes()):
                    console.say(line)
                while next_report <= now:
                    next_report += report_every
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
    report_every = report_seconds()
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
        f"{sweep_width_derivation(settlement_bytes)}; a progress report every {console.fmt_duration(report_every)}",
        flush=True,
    )
    shortfall = memory_shortfall(settlement_bytes)
    if shortfall is not None:
        refuse, sentence = shortfall
        if refuse and stated is None:
            raise SystemExit(f"deep sweep: {sentence}; pass --jobs 1 or set {JOBS_ENV}=1 to run it anyway")
        console.warn(f"deep sweep: {sentence}")
    started = time.perf_counter()
    summary, peaks = run_sweep(plan, args.max_length, jobs, report_every=report_every)
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
