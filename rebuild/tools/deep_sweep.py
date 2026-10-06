"""The deep form of gate:conform: the same font-versus-settlement sweep as the per-edit sweep, at maximum length 5 by default (`--max-length` refuses anything below the per-edit sweep's 4). The per-edit sweep shapes every text up to four letters and checks what only shaping the compiled font can test: HarfBuzz's application semantics over the rule shapes the lookup contains. Whether the six-slot window is sufficient for the texts the tables were built for is checked by the crate's string replay, which `run_m1` runs on every build. This tool asks the shaper's question at a depth the per-edit sweep cannot afford, over texts long enough to reach a letter's fourth lookahead slot, which no per-edit sweep text reaches.

It runs on demand (`make conform-deep`), not per edit. The key of its green record (`artifact_cycle.deep_sweep_skip_lines`) is the set of behavior classes the build enumerated from the emitted lookup (`emit_gsub.behavior_classes`), the font-compilation code, and the uharfbuzz version. It leaves out the runes and M1.otf, so a rune edit that changes many rules but adds no new rule shape leaves the sweep current. When a build emits a new shape, or the compilation code or the shaper changes, the key changes and the cycle reports the sweep as `due` once per pass. The record stores the maximum length beside the key, and a green shallower than a recorded green under the same key keeps the deeper length (`artifact_cycle.record_deep_sweep_green`); the green line then names both. The per-edit sweep's key is the same lines plus its maximum length (`artifact_cycle.conform_skip_fingerprint`). No gate depends on this sweep: a due deep sweep means it should be run, and the cycle does not fail.

The per-edit sweep's split-buffer check (every text split at a boundary shapes the same as its segments shaped alone) runs at this depth too, and this is the only place it covers texts longer than the per-edit sweep's maximum length, since no build step shapes a length-5 text. The ZWNJ glyph's own properties (zero advance, no ink) need no depth: read-back checks them in the font bytes on every build.

Each settlement configuration other than `default` differs from it only through the few runes its features rename (`conform.renamed_runes`; the plan line names them). So the sweep shapes `default` over every text and each other configuration only over the texts that name one of those runes (`sweep_triggers`), when a plan-time check passes for it: every policy record gated on one of its features restricts a slot of its window to a rune that feature renames (`conform.unconfined_feature_records`). A text that names none of those runes then reaches the font's settlement lookup with the glyphs `default` gives it, since the stylistic sets' lookups rename only those runes and the settlement lookup is the same in every configuration, and settles as it does under `default`, since no record or unlock row that reads the configuration's features fires on any of its windows. Its comparison is `default`'s, which this sweep makes. A configuration that fails the check, or renames nothing, is swept over every text, and the plan line names which texts each configuration shapes and any records that failed the check. The per-edit sweep stays exhaustive in every configuration.

The sweep runs in units, each in a spawn process of its own (`run_sweep`, `sweep_units`): the ss10 overlay whole, and each settlement configuration once per alphabet symbol, over the texts that end in that symbol and, in a configuration that skips texts, contain one of its trigger letters (`conform.conformance_config_worker` with `last` and `triggers`). The pool takes the units in that order and runs one per process (`max_tasks_per_child=1`), so no unit starts in a process that still holds what an earlier one allocated. A settlement unit's walk shares no memo file at this depth and keeps every distinct window it settles except the pinned ones (`conform._SettledWindowWalk`, `horizon`) until its last text, or until a wave of settles could take them past the window ceiling below. Every window whose right slots reach its text's end names the unit's symbol, so a unit keeps all of that reuse while it holds the window; a window whose right slots stop at a boundary before the text's end does not, so each unit whose texts reach it settles it again. The results merge per configuration (`conform.merge_unit_results`) and then across configurations in `conform.ACCEPTANCE_CONFIGS` order, and each configuration's summed sequences must equal every text of length 1 to its maximum length, or in a configuration that skips texts every such text that contains one of its trigger letters (`run_m1.config_texts`), before the summary is written, so the summary and its exemplars are the ones a single walk per configuration gives, whatever the width and the order in which units finish.

A unit's windows grow through its walk until they would pass the window ceiling (`sweep_memo_windows`: DEEP_SWEEP_MEMO_WINDOWS unless `AMS_DEEP_SWEEP_MEMO_WINDOWS` states another). Before a wave of settles that could take them past it, the walk empties its window dict and continues, settling again any window it meets after that (`conform._SettledWindowWalk`, `max_windows`). Below the ceiling a unit's footprint peaks at the window dict's last doubling late in the walk, when it briefly holds the old and new tables together. DEEP_SWEEP_MEMO_WINDOWS is the most a dict holds before it doubles, so a walk at that ceiling fills one table and never doubles past it. The width is therefore fixed before anything is spawned, from the most windows a unit's walk holds, priced so that it covers that peak: `window_bound` counts in closed form the windows the walk of the unit whose texts end in one symbol can hold at the requested maximum length with no ceiling, `unit_window_bounds` bounds every symbol's unit in every configuration, including a unit that skips texts, which holds a subset of the windows of its own configuration's unit over every text that ends in its symbol (its docstring has the argument), `settlement_worker_bytes` prices the smaller of the heaviest unit's bound and the ceiling as each settlement unit's need, and `sweep_width` fits that many settlement units into the machine's memory, capped at the cores and the unit count. At maximum length 5 every unit's bound is below the ceiling, on the alphabet projected to every letter too, so no walk releases and the bound sets the width; it grows with the alphabet, so a new letter needs no new measurement. At maximum length 6 the heaviest unit's bound is many times the ceiling, so every settlement unit is priced at the ceiling, and a unit's price, and with it the width, does not depend on the alphabet: every fleet machine sweeps as many units at once as it has cores, which `rebuild/test_deep_sweep.py` checks. When even one settlement unit does not fit, which only a ceiling stated well above DEEP_SWEEP_MEMO_WINDOWS can cause on a fleet machine, `memory_shortfall` says so before anything is spawned: a warning when it exceeds the memory less the reserve, and a refusal without a stated width when it exceeds the machine's memory in all. The ss10 overlay holds no windows, only what every worker holds before its walk (`DEEP_SWEEP_BASE_BYTES`). The parent holds the spec, the glyph inventory and the guard verdicts, well inside the reserve, as the per-edit sweep's controller does. Each unit returns its peak footprint (`peak_rss.peak_footprint_bytes`), and the run's check line in the cycle-timings journal records each configuration's highest unit peak beside its estimate, so a real run can be held against the estimate.

While the units run, the parent prints a progress report every 20 minutes (`REPORT_SECONDS_DEFAULT`; `AMS_DEEP_SWEEP_REPORT_SECONDS` sets another interval for a debugging run). Each unit's worker writes the count of texts it has shaped into its unit's slot of a shared array after each chunk of `conform.TEXT_CHUNK` texts, and its pid and the clock time it started into two others; the arrays reach the spawn workers through the pool's initializer, the one way a spawn process can inherit shared memory. One slot has one writer and the parent only reads, so no lock is taken, and the hot loop pays one store per chunk. A worker prints nothing. The parent reads the slots every `SAMPLE_SECONDS`, and each report states, in total and then per configuration over the configuration's units, the texts shaped out of the texts to shape (`SweepUnit.texts`, counted before any worker starts), the time elapsed, the rate since the last report, the estimated finish, and the footprint of every running unit's process (`peak_rss.footprint_bytes`) with the machine's swap in use (`peak_rss.swap_used_bytes`). `SweepProgress` says how the estimate is computed.

The total is a `[progress] <k>/<n> texts` counter line in `console`'s protocol, and the rest of the report rides on the same line: `console.parse_line` reads the counter's two bare counts and takes everything after them as the unit, which the artifact cycle's console reprints verbatim after the counts, which it formats with `console.fmt_count`. The two counts stay bare because the parser reads only digits there; every other count and duration in the report goes through `fmt_count` and `fmt_duration`. Each configuration gets a `deep sweep[<config>]: ` line after it, which sums its units and counts the ones running and finished. It is not a protocol line, so the cycle's console logs it without surfacing it, as it does the per-configuration peak lines at the end, while `tail -f` shows every line. Making them counter lines too would not show them: the console keeps only the latest counter of a burst and surfaces it a heartbeat later.

A green run also refreshes gate:conform's green record, when the per-edit sweep's key did not change during the run, because a sweep at depth N covers every text the per-edit sweep at depth 4 shapes: it shapes each one in every configuration, or, in a configuration that skips it, in `default`, where the configuration shapes and settles it as `default` does. The next cycle can then skip the per-edit sweep. At or past the deep replay's maximum length it also refreshes the deep replay's record (`rebuild.tools.deep_replay`), because it settles every text it shapes against the tables in the font, and every text it skips settles as it does in `default`, where it is settled. Both refreshes rest on the plan-time check, so neither runs unless the check passed in every configuration. That record holds the rune digests read before the sweep started, which are the runes the swept font was built from, and the refresh is skipped when a rune changed while the sweep ran. The refresh keeps a deeper maximum length the record already holds for the same digests and structure stamp (`artifact_cycle.record_deep_replay_green`), and its line names that length.

`--targeted` runs the same per-text checks over the texts the `settle:bk1-la4` rules give instead of over every text up to a maximum length. Such a rule reads one glyph before its input and four after it (`targeted_rule`), so it can match only a run of at least six glyphs, and no text at the sweep's default maximum length reaches one. For each such rule in each settlement configuration's table (`targeted_texts`) the mode shapes its certificate, the token stream the crate built to make it fire (`table.DecisionTable.certificates`, which otherwise only the witness stage settles), and every text whose six positions take a family from its backtrack class, its input and its four lookahead classes (`rule_contexts`), alone and after each alphabet symbol, so the backtrack letter's own left varies too. Each configuration's texts are deduplicated and cut into units of at least TARGETED_UNIT_TEXTS texts (`targeted_units`), which run in the same kind of pool as the exhaustive units (`_run_units`, `_texts_worker`), and the run reports per configuration how many of these rules' certificates it shaped. It is targeted, not exhaustive: it misses a fault that appears only where these rules meet other rules in contexts the enumeration does not produce, and it places no ZWNJ where no context class names one. Its green record (`cycle_paths.TARGETED_SWEEP_GREEN`) is keyed on the deep sweep's lines and a digest of each configuration's settle:bk1-la4 rules and their certificates (`targeted_lines`), so a rune edit that changes one of those rules makes it due and one that changes none leaves it current. `--targeted --status` asks. It runs on demand: the artifact cycle neither runs nor reports it, and a green targeted run refreshes no other record.

Run as: uv run python -m rebuild.tools.deep_sweep, or through `make conform-deep`.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
import multiprocessing
import os
import re
import sys
import time
from collections import Counter, deque
from collections.abc import Callable, Collection, Hashable, Iterator, Mapping, MutableSequence, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TypeVar

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.pipeline import conform, fingerprint, kernel_exec, run_m1, table, witness
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.run_m1 import SweepUnit, config_texts, sweep_units
from rebuild.pipeline.model import MARKER_TAG_SEPARATOR, ResolvedSpec
from rebuild.pipeline.table import DecisionTable, Rule
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
    deep_sweep_skip_lines,
    deep_sweep_status,
    moved_inputs_note,
    read_green_record,
    record_deep_replay_green,
    record_deep_sweep_green,
    record_green,
    tables_imports_digest,
)
from rebuild.tools.cycle_timings import CheckResult, record_check
from rebuild.tools.green_record import _digest_lines

SUMMARY_NAME = "deep_sweep_summary.json"

CHECK = "conform-deep"

TARGETED_SUMMARY_NAME = "deep_sweep_targeted_summary.json"

TARGETED_CHECK = "conform-deep-targeted"

TARGETED_CLASS = "settle:bk1-la4"

TARGETED_COMMAND = "`make conform-deep ARGS='--targeted'`"

# The fewest texts in a targeted unit, all but each configuration's last (`targeted_units`). A unit's walk shares windows only within itself, and each unit pays for a spawn process, so larger units settle less and start fewer processes, while smaller ones leave fewer slots idle at the end. Measured on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) on the alphabet with ·Way (44 runes), one run at a time with nothing else running, over 7,826,390 texts: 121 units of this size took 60.5 s, and 62 units of twice the size 61.9 s. Each configuration's highest unit peaked at 0.18 GB against an estimate of 0.36 GB, and the highest at twice the size at 0.21 GB against 0.51 GB, so every fleet machine runs as many units at once as it has cores. The logs are in `var/keep/issue-496/`, and the `conform-deep-targeted` check lines in the cycle-timings journal hold the peaks.
TARGETED_UNIT_TEXTS = 65_536

JOBS_ENV = "AMS_DEEP_SWEEP_JOBS"

REPORT_ENV = "AMS_DEEP_SWEEP_REPORT_SECONDS"

MEMO_WINDOWS_ENV = "AMS_DEEP_SWEEP_MEMO_WINDOWS"

REPORT_SECONDS_DEFAULT = 20 * 60.0

# How often the parent reads the workers' counters between reports. A count is dated to the first reading that shows it, so it is late by at most this much, against walks that run for hours and chunks that take seconds. A configuration's start is the clock time its worker recorded, so a worker that finishes between two readings, as the ss10 overlay's does, still has its duration.
SAMPLE_SECONDS = 5.0

# The share of a configuration's texts that the rate its estimate uses spans (`smoothed_rate`). A walk's rate changes as it moves through the alphabet: it takes the letters in order in the first position, one after another across its longest texts, and the letters' rules cost different amounts to settle and shape. A tenth of the texts spans several first letters, so one costly letter moves the rate little, while the rate still trails the walk by only a twentieth of it, half the window.
RATE_WINDOW_SHARE = 0.1

# What one settlement unit's worker holds per window at its peak, the variable term of its need (`settlement_worker_bytes`). The walk keeps `conform._SettledWindowWalk.windows`, a dict from a six-label key tuple to a shared outcome, and shares no settle memo at a deep length, so it holds one key tuple and one dict entry for every distinct window it settles except the pinned ones (`horizon`), until its walk ends or until a wave of settles could take them past the window ceiling (DEEP_SWEEP_MEMO_WINDOWS), when it empties the dict. Labels and outcomes are interned and shared, so nothing else grows with the windows. The dict doubles its tables when it passes two thirds of its slots, and for that moment it holds the old tables and the new ones together, so below the ceiling a worker's peak is that transient at the last doubling, not its size at the end of the walk. Per window held, the peak is highest when the walk ends just past a doubling.
# Measured on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) on the alphabet with ·Ye, reading footprint (`peak_rss.peak_footprint_bytes`) with nothing else running and swap in use unchanged, by the harness `var/keep/issue-482/harness/measure.py`, which runs one unit in a fresh spawn worker as `run_sweep` does; its records are in `var/keep/issue-482/after-step4/runs.ndjson` and `after-step3-gates/runs.ndjson`. At maximum length 5, `default`'s unit of the texts that end in ·Et holds 393,118 windows at its end and peaks at 0.234 GB, 0.009 GB above its end: the old tables of its doubling past 349,525 windows, so it is the worst case per window. ·Utter's unit holds 393,088 and peaks at 0.231 GB, and the space's holds 349,342 and peaks at 0.219 GB. `default`'s whole walk over the first 16 symbols of the harness's order holds 160,020 windows and peaks at 0.183 GB, and each of its units holds 23,363 to 26,804 and peaks at 0.153 to 0.167 GB.
# The line through the highest peak, ·Et's unit at 0.234 GB over 393,118 windows, and the reduced alphabet's whole walk at 0.183 GB over 160,020 has a slope of 218.9 bytes a window over a base of 0.148 GB. ·Utter's and the space's units peak higher than the reduced walk, but ·Utter's holds nearly ·Et's window count, and the space's is too close to it to fix a slope as well as a count less than half as large. The constant is the slope plus a quarter, rounded up to ten bytes, and DEEP_SWEEP_BASE_BYTES is the base plus a quarter, rounded up to the tenth, because a cost that is too low pushes the machine into swap. `window_bound` overcounts on top of that, 727,377 against ·Et's 393,118, 809,421 against ·Utter's 393,088, and 673,878 against the space's 349,342. The heaviest unit's bound, ·Utter's, prices every settlement unit at maximum length 5 at 0.43 GB against the 0.234 GB measured, so every fleet machine sweeps as many units at once as it has cores (18 and 12), which `rebuild/test_deep_sweep.py` checks. The window count follows the alphabet through `window_bound`, so re-measure only when the window key or what the walk holds per window changes shape, or `conform.TEXT_CHUNK` grows.
# The first full run at maximum length 5 on the same machine and alphabet (width 18, 181 units, 2,106 s, with no swapout; its records are in `var/keep/issue-482/deep-run/run-1/`) peaked at 0.246 to 0.250 GB in each settlement configuration's highest unit and at 0.052 GB in the overlay's worker, against estimates of 0.43 GB and 0.20 GB. Its check line in the cycle-timings journal records each configuration's highest unit peak but not that unit's window count, so the run tests the constants without refitting the slope. They cover a 0.250 GB peak in any unit that holds more than 177,650 windows, about half the space's 349,342, the fewest a full-alphabet unit holds above. Even the line through the space's unit at that peak and the reduced walk, 351.3 bytes a window over 0.127 GB, prices ·Utter's bound at 0.41 GB, under the 0.43 GB these constants give. On the line through ·Et's unit the run's peaks fall at 447,000 to 464,000 windows, more than ·Et's unit holds and fewer than the smallest unit bound, the space's 673,878.
# The one-unit runs at maximum length 6 under DEEP_SWEEP_MEMO_WINDOWS's comment test these constants on a far larger walk: the uncapped walk of `default`'s ·Utter unit on the alphabet with ·Way holds 49,436,318 windows at its end and peaks at 8.53 GB, under the 14.04 GB these constants give for that count, and the walk at the ceiling peaks at 0.91 GB against 1.77 GB.
DEEP_SWEEP_WINDOW_BYTES = 280

# What a worker holds before its walk holds a window: its interpreter, the spec, a HarfBuzz `Shaper` over M1.otf, the glyph names and anchors, the guard verdicts the parent passes, a chunk of `conform.TEXT_CHUNK` texts in flight, and one wave's pinned outcomes. It is a settlement unit's fixed term and all the ss10 overlay's worker holds, since the overlay walks nothing; that worker peaks at 0.045 GB in #480's length-4 measurement (`var/keep/issue-480/len4-full/`) and at 0.052 GB in the first full run at maximum length 5. DEEP_SWEEP_WINDOW_BYTES's comment derives it and holds that run against it.
DEEP_SWEEP_BASE_BYTES = 200_000_000

# The most windows one settlement unit's walk holds (`conform._SettledWindowWalk`, `max_windows`; `AMS_DEEP_SWEEP_MEMO_WINDOWS` states another through `sweep_memo_windows`). Before a wave of settles that could take its window dict past the ceiling, the walk empties the dict and continues, settling again the windows it meets after that. It is counted in windows, not bytes, so a walk releases at the same point on every machine. The value is the most entries a Python dict of 2^23 slots holds before it doubles, so a walk at the ceiling fills that table and never allocates the next. `settlement_worker_bytes` prices a unit at the smaller of its bound and the ceiling at DEEP_SWEEP_WINDOW_BYTES a window, 1.77 GB at the ceiling, so both 48 GiB machines (`doc/fleet.md`) sweep as many units at once as they have cores at any maximum length and on any alphabet, which `rebuild/test_deep_sweep.py` checks. The next dict size, 11,184,810 windows, would price a unit at 3.33 GB and narrow the 18-core M5 Pro to 13 units. No unit's bound at maximum length 5 reaches the ceiling: the heaviest, ·Utter's, is 884,172 windows on the alphabet with ·Way and 2,201,144 on a projection of the 57-rune alphabet in which each letter still to come has as many cells as the most any letter has on the alphabet with ·Way, and each ligature still to come as many as the most any ligature has, so a length-5 walk never releases.
# Picked by measurement on the 18-core M5 Pro 48 GiB MacBook Pro on the alphabet with ·Way (44 runes), one run at a time with nothing else running, by the harness `var/keep/issue-497/harness/measure.py`, which runs `default`'s unit of the texts that end in ·Utter at maximum length 6 (71,270,178 texts) in a fresh spawn worker as `run_sweep` does; its records are in `var/keep/issue-497/runs.ndjson`. Uncapped, the walk settles 109,726,262 windows, holds 49,436,318 at its end against a `window_bound` of 75,807,484, peaks at 8.53 GB, and takes 6,567 s. At this ceiling it releases 14 times, settles 139,664,902 windows (27.3% more), peaks at 0.91 GB, and takes 6,149 s. At the two smaller dict sizes, 2,796,202 and 1,398,101 windows, it releases 39 and 83 times, settles 54.5% and 60.3% more, peaks at 0.62 GB and 0.40 GB, and takes 6,172 s and 6,163 s. Every capped walk is faster than the uncapped one: the walk's own time outside its kernel calls falls from 2,216 s uncapped to 1,531 to 1,574 s, more than the 187 to 346 s that the repeated settles add to those calls. This ceiling settles the fewest windows of the three. All four walks settle the same stream (the harness's stream hash) and shape every text with no divergence, and none changed the swap in use. The peak at this ceiling is about half its 1.77 GB estimate, and DEEP_SWEEP_WINDOW_BYTES's comment holds the uncapped walk's peak against that constant.
DEEP_SWEEP_MEMO_WINDOWS = 5_592_405


@dataclass(frozen=True)
class SweepPlan:
    """What the sweep needs before it spawns anything: the spec, the glyph inventory the workers name settled cells with, the window bound of the heaviest settlement unit at the requested maximum length and the name of the symbol its texts end in, how long the units' bounds took to compute, the units (`sweep_units`), the window ceiling each settlement unit's walk releases its windows at (`sweep_memo_windows`), and the plan-time check's outcome (`sweep_triggers`): the runes whose texts alone each configuration that skips texts shapes, and the records that failed the check in each configuration that failed it."""

    spec: ResolvedSpec
    glyphs: Mapping
    windows: int
    heaviest: str
    bound_seconds: float
    units: tuple[SweepUnit, ...]
    memo_windows: int
    trigger_runes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    unconfined: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def window_bound(
    max_length: int,
    tokens: Mapping[str, int],
    boundaries: Collection[str],
    cells: Mapping[str, int],
    last: str,
    finals: Mapping[str, str] | None = None,
) -> int:
    """Return an upper bound on the windows one settlement unit's walk holds at the end of a sweep to `max_length`: the unit whose texts end in `last`, a one-character letter token or a boundary. `tokens` maps every letter token (each letter, and each ligature rune) to the characters it spans, `boundaries` names the boundary characters, `finals` maps a ligature token to the one-character token its sequence ends in (a token it leaves out ends in itself), and `cells` maps a token to the number of cells its family has in the glyph inventory; a token it leaves out is bounded by its lefts alone.

    A window is the walk's memo key (`conform._SettledWindowWalk`): a letter position's label, its left, and its four right slots (`conform._window_rights`). Counting the keys exactly means settling every window, which is the sweep's own work, so this bounds the count in closed form over token strings. Split a key into its ask, the label and the right slots, and its left. The ask is fixed by the tokens it spans: the letter token at the position, up to four letter tokens after it, and what ends the slots early, the end of the text (`#EDGE`) or a boundary (after which they read `#NA`). Every such token string is counted whether or not formation produces it, and the configuration's renaming maps labels one to one, so both can only merge asks.

    The left is `#EDGE` when nothing comes before the ask, a boundary's label when a boundary does, and otherwise the settled cell of the letter token x just before it. x's window is x's label, x's left, and x's right slots, which are the ask's first four slots, so with the ask fixed x's outcome depends only on x's left. When x starts the text its left is `#EDGE` and it has one outcome. Otherwise it has at most as many outcomes as it has possible lefts, and at most as many as its family has cells. So with p characters available before the ask, the lefts number at most `lefts(p)`: none for negative p, one for p = 0, and for p ≥ 1 one for `#EDGE`, one per boundary, and for each token x of length ℓ ≤ p, one when ℓ = p and `min(cells[x], lefts(p - ℓ))` otherwise.

    Every text of the unit ends in `last`, which bounds the characters before an ask of c characters by what must come after it. An ask that ends at the text's end (`#EDGE`) has a last token that ends in `last` and up to `max_length - c` characters before it. One cut short by `last` itself has up to `max_length - c - 1`, and one cut short by any other boundary has up to `max_length - c - 2`, since at least `last` follows that boundary. One whose right slots are four letter tokens has up to `max_length - c` when its last token ends in `last` and `max_length - c - 1` otherwise.

    The walk does not hold the pinned windows (`conform._SettledWindowWalk._is_pinned`), so the bound leaves out the lefts that only a pinned window has. Every `#EDGE` left of an ask that ends at the text's end is pinned. So is every left that uses up all the characters before an ask whose right slots reach the text's last token in a text at the maximum length (`pinned`): `#EDGE` when there are none, a boundary when there is one, and a token that spans them all. Only one text produces such a key, since anything more before the ask or after the text's end would make the text longer than the maximum length.

    The bound is exact over one-character tokens whose cell counts do not cap their lefts. The slack is where a family's cell count stands in for the cells one right context allows, and in token strings with ligatures that formation never produces; DEEP_SWEEP_WINDOW_BYTES's comment compares the bound with the windows a unit holds. The arithmetic walks a table indexed by character length, so it takes well under a millisecond at any maximum length.
    """
    finals = finals or {}
    ending = [length for token, length in tokens.items() if finals.get(token, token) == last]

    @cache
    def lefts(prefix: int) -> int:
        if prefix < 0:
            return 0
        if prefix == 0:
            return 1
        total = 1 + len(boundaries)
        for token, length in tokens.items():
            if length == prefix:
                total += 1
            elif length < prefix:
                ceiling = cells.get(token)
                total += lefts(prefix - length) if ceiling is None else min(ceiling, lefts(prefix - length))
        return total

    def pinned(prefix: int) -> int:
        if prefix <= 0:
            return int(prefix == 0)
        return (len(boundaries) if prefix == 1 else 0) + sum(length == prefix for length in tokens.values())

    def held(prefix: int) -> int:
        return lefts(prefix) - pinned(prefix)

    strings: list[Counter[int]] = [Counter({0: 1})]
    ends: list[Counter[int]] = [Counter()]
    for _ in range(5):
        longer: Counter[int] = Counter()
        longer_ends: Counter[int] = Counter()
        for span, count in strings[-1].items():
            for length in tokens.values():
                if span + length <= max_length:
                    longer[span + length] += count
            for length in ending:
                if span + length <= max_length:
                    longer_ends[span + length] += count
        strings.append(longer)
        ends.append(longer_ends)
    bound = 0
    for letters in range(1, 6):
        for span, count in strings[letters].items():
            room = max_length - span
            if letters < 5:
                bound += ends[letters][span] * (held(room) - (room > 0))
                for boundary in boundaries:
                    bound += count * (held(room - 1) if boundary == last else lefts(room - 2))
            else:
                bound += ends[letters][span] * held(room) + (count - ends[letters][span]) * lefts(room - 1)
    return bound


def unit_window_bounds(spec: ResolvedSpec, glyphs: Mapping, max_length: int) -> dict[str, int]:
    """Return `window_bound` for each settlement unit of this spec and glyph inventory, keyed by the name of the rune or boundary its texts end in, in `conform.spec_alphabet` order: every rune with a code point is a one-character letter token, every ligature rune a token as long as its sequence that ends in its sequence's last rune, the boundary tokens are the registry's, and a token's cells are the inventory's cells of its family. Each bound covers its symbol's unit in every configuration. Nothing `window_bound` counts from depends on the configuration: the inventory's cells span every settlement configuration's tables (`run_m1.serialized_tables`), so they cap the settled cells of each one, and the configuration's renaming maps labels one to one. So each bound covers each configuration's unit over every text that ends in its symbol. A unit that skips texts (`SweepUnit.triggers`) shapes a subset of those texts and holds a window only where one of them asks for it at a position that does not pin it, where its configuration's unit over every text asks for it too, so it holds a subset of that unit's windows."""
    tokens = {
        name: len(rune.sequence) if rune.sequence else 1
        for name, rune in spec.runes.items()
        if rune.sequence or rune.codepoint is not None
    }
    finals = {name: rune.sequence[-1] for name, rune in spec.runes.items() if rune.sequence}
    boundaries = tuple(spec.registry.boundary_tokens)
    cells = Counter(cell.rune for cell in glyphs)
    names = {rune.codepoint: name for name, rune in spec.runes.items() if rune.codepoint is not None} | {
        token.codepoint: name for name, token in spec.registry.boundary_tokens.items()
    }
    return {
        names[ord(symbol)]: window_bound(max_length, tokens, boundaries, cells, names[ord(symbol)], finals)
        for symbol in conform.spec_alphabet(spec)
    }


def sweep_triggers(spec: ResolvedSpec) -> tuple[dict[str, frozenset[str]], dict[str, tuple[str, ...]]]:
    """Return the plan-time check's outcome over the settlement configurations: the runes each configuration that may skip texts renames (`conform.renamed_runes`), and the records that failed the check (`conform.unconfined_feature_records`) in each configuration that failed it. A configuration that renames runes and passes the check settles and shapes a text that names none of them as `default` does: its stylistic sets' lookups rename only those runes, the font's settlement lookup is the same in every configuration, and no record or unlock row that reads its features fires on a window of such a text. `default` sweeps every text, so its units shape that text against the same font and the same settlement. A configuration that renames nothing, or that fails the check, shapes every text."""
    triggers: dict[str, frozenset[str]] = {}
    unconfined: dict[str, tuple[str, ...]] = {}
    for config in conform.SETTLEMENT_CONFIGS:
        records = conform.unconfined_feature_records(spec, config)
        if records:
            unconfined[config] = records
            continue
        runes = conform.renamed_runes(spec, config)
        if runes:
            triggers[config] = runes
    return triggers, unconfined


def _series(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def triggers_clause(plan: SweepPlan) -> str:
    """Return the plan line's clause naming the texts each settlement configuration shapes: every text, or only the ones that name the runes it renames, and for each configuration that failed the plan-time check, the records that failed it."""
    groups: dict[frozenset[str], list[str]] = {}
    for config in conform.SETTLEMENT_CONFIGS:
        if config in plan.trigger_runes:
            groups.setdefault(plan.trigger_runes[config], []).append(config)
    whole = [
        config
        for config in conform.SETTLEMENT_CONFIGS
        if config not in plan.trigger_runes and config not in plan.unconfined
    ]
    parts = [f"{_series(whole)} {'shapes' if len(whole) == 1 else 'shape'} every text"] if whole else []
    parts += [
        f"{_series(configs)} only the texts that name {_series(sorted(runes))}"
        for runes, configs in groups.items()
    ]
    clause = "settlement configurations: " + "; ".join(parts)
    if plan.trigger_runes:
        clause += ", since every record gated on their features restricts a slot of its window to a rune they rename"
    for config, records in plan.unconfined.items():
        clause += f"; {config} shapes every text because {_series(list(records))} {'restricts' if len(records) == 1 else 'restrict'} no slot to a rune its feature renames"
    return clause


def settlement_worker_bytes(windows: int, memo_windows: int) -> int:
    """Return what one settlement unit's worker needs at the peak of a walk that would hold `windows` windows at its end and releases them before they pass `memo_windows`: DEEP_SWEEP_BASE_BYTES plus DEEP_SWEEP_WINDOW_BYTES for each window of the smaller of the two, since the walk never holds more than its ceiling."""
    return DEEP_SWEEP_BASE_BYTES + min(windows, memo_windows) * DEEP_SWEEP_WINDOW_BYTES


def _fit_terms(settlement_bytes: int, units: int, ncores: int | None, overlay: bool) -> tuple[int, int, int]:
    """Return the settlement unit's need, the overlay worker's need subtracted as co-resident when the run has an overlay unit, and the cap on units at once: the smaller of the unit count and the usable cores."""
    cores = ncores or memory_budget.usable_cores()
    return settlement_bytes, DEEP_SWEEP_BASE_BYTES if overlay else 0, min(cores, units)


def sweep_width(
    settlement_bytes: int,
    units: int,
    *,
    ncores: int | None = None,
    total_bytes: int | None = None,
    overlay: bool = True,
) -> int:
    """Return how many of `units` units sweep at once: `memory_budget.how_many_fit` over `settlement_bytes`, one settlement unit's need, with the overlay worker's need subtracted as co-resident, capped at the unit count and the cores, and floored at one. The overlay runs first in one of those slots (`run_sweep` submits it first), and any slot can take a settlement unit once it is free, so the width is counted in settlement units and any set of units that can run together fits: the overlay beside one fewer settlement unit, or the width's worth of settlement units with the overlay's need subtracted anyway. The targeted mode has no overlay unit and passes `overlay=False`, which subtracts nothing. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine."""
    per_unit, coresident, cap = _fit_terms(settlement_bytes, units, ncores, overlay)
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def sweep_width_derivation(
    settlement_bytes: int,
    units: int,
    *,
    ncores: int | None = None,
    total_bytes: int | None = None,
    overlay: bool = True,
) -> str:
    """Return `sweep_width`'s arithmetic as a clause for the plan line: `memory_budget.describe_fit` over the same terms, then where the overlay runs when there is one."""
    per_unit, coresident, cap = _fit_terms(settlement_bytes, units, ncores, overlay)
    fit = memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)
    return f"settlement units: {fit}" + (
        "; the ss10 overlay runs first in one of their slots" if overlay else ""
    )


def memory_shortfall(
    settlement_bytes: int, *, total_bytes: int | None = None, overlay: bool = True
) -> tuple[bool, str] | None:
    """Return None when one settlement unit's estimate, beside the overlay's when the run has one (`overlay`), fits this machine's memory less the reserve, the budget `sweep_width` divides. Otherwise the floor at one, not the memory, set the width, and this returns whether to refuse without a stated width and the sentence that says why. The run is refused when that need exceeds the machine's total memory, which no reserve or margin in the estimate can make fit, and only warned about when it exceeds the budget alone, since the estimate runs above the measured peak (DEEP_SWEEP_WINDOW_BYTES's comment) and the unit may still fit. `total_bytes` is a keyword so a test can ask about an invented machine."""
    total = memory_budget.total_memory_bytes() if total_bytes is None else total_bytes
    reserve = memory_budget.os_reserve_bytes(total_bytes=total)
    need = settlement_bytes + (DEEP_SWEEP_BASE_BYTES if overlay else 0)
    if need <= total - reserve:
        return None
    gb = peak_rss.format_gb
    estimate = f"one settlement unit is estimated at {gb(settlement_bytes)} GB" + (
        f" beside the overlay's {gb(DEEP_SWEEP_BASE_BYTES)} GB" if overlay else ""
    )
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
            f"{JOBS_ENV}={stated!r} is not a width: it takes a bare decimal count of units to sweep at once"
        ) from None


def sweep_memo_windows() -> int:
    """Return the most windows each settlement unit's walk holds before it releases them: `AMS_DEEP_SWEEP_MEMO_WINDOWS` when it is set, else DEEP_SWEEP_MEMO_WINDOWS. A set value must be a bare decimal count of at least one; anything else raises, as `deep_replay.replay_memo_windows` does for its own variable. The ceiling is in windows, not bytes, so a walk releases at the same point on every machine."""
    stated = os.environ.get(MEMO_WINDOWS_ENV)
    if stated is None:
        return DEEP_SWEEP_MEMO_WINDOWS
    if re.fullmatch(r"[0-9]+", stated) is None or int(stated) < 1:
        raise RuntimeError(
            f"{MEMO_WINDOWS_ENV}={stated!r} is not a window ceiling: it takes a bare decimal count of windows, at least one"
        )
    return int(stated)


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


Unit = TypeVar("Unit", bound=Hashable)


def schedule_finish(
    running: Mapping[Unit, float], queued: Sequence[tuple[Unit, float]], slots: int
) -> dict[Unit, float]:
    """Return the seconds from now at which each unit finishes, given the seconds each running one has left, each queued one's whole duration in the order the pool takes them, and the pool's slots. Each queued unit starts in the slot that frees first, a free slot at once, as `ProcessPoolExecutor` hands out work in submission order."""
    finishes = dict(running)
    slot_free = sorted(running.values()) + [0.0] * max(0, slots - len(running))
    heapq.heapify(slot_free)
    for unit, seconds in queued:
        finishes[unit] = heapq.heappop(slot_free) + seconds
        heapq.heappush(slot_free, finishes[unit])
    return finishes


@dataclass
class _Walk:
    config: str
    texts: int
    overlay: bool
    samples: deque[tuple[float, int]] = field(default_factory=deque)
    first: tuple[float, int] | None = None
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
    """The parent's record of each unit's walk, indexed by its slot in `run_sweep`'s submission order, and the progress report built from it, which sums each configuration's units. Every time is on the parent's monotonic clock; a worker's start, which the worker records on the wall clock, is converted to it at the reading that finds it (`run_sweep`).

    The estimate is a schedule, not the total rate extrapolated: the pool runs `slots` units at once and the units can outnumber the slots many times over, so a total rate would stand for units that have not started and misjudge when a slot frees. A running unit has its remaining texts left at its own rate, taken over the last `RATE_WINDOW_SHARE` of its texts (`smoothed_rate`), once it has shaped that many. A queued unit takes its whole count at the rate of its kind (settlement or overlay): the texts of the units of that kind over their durations, a finished one's as it ran and a running one's elapsed time plus its remaining texts at its own rate. The kinds are kept apart because the overlay's texts are few and short, while the settlement units walk texts up to the maximum length. `schedule_finish` then places the queued units, in the order the pool takes them, into the slots as they free, a configuration ends when its last unit does, and the run ends when the last slot frees.

    A unit that has shaped less than the window has a rate of its own that still carries its start, the worker's setup and the short texts first, so it takes its kind's rate too. A kind's rate counts only the units that are finished or have a rate over the whole window. Only when there are none, as while the first units of a kind cover their first window, does it count the rates the running ones have made so far. A unit whose kind has no rate at all leaves the finish unestimated.
    """

    def __init__(self, units: Sequence[SweepUnit], slots: int, started: float) -> None:
        self.walks = [_Walk(config=unit.config, texts=unit.texts, overlay=unit.overlay) for unit in units]
        self.configs: dict[str, list[int]] = {}
        for slot, unit in enumerate(units):
            self.configs.setdefault(unit.config, []).append(slot)
        self.slots = slots
        self.started = started
        self.marked: tuple[float, int] | None = None
        self.config_marked: dict[str, tuple[float, int]] = {}

    def observe(self, slot: int, done: int, now: float, began: float | None = None) -> None:
        """Record that the unit in `slot` is running and has shaped `done` texts, as read at `now`, and that it started at `began` when that is known. A count is kept only when it changes, so it is dated to the first reading that shows it."""
        walk = self.walks[slot]
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

    def finish(self, slot: int, now: float, done: int, began: float | None = None) -> None:
        """Record that the unit in `slot` returned at `now` having shaped `done` texts, and started at `began` when that is known and no reading saw it running."""
        walk = self.walks[slot]
        if walk.first is None:
            walk.first = (now if began is None else min(began, now), 0)
        walk.samples.append((now, done))
        walk.finished = now

    def running(self, slot: int) -> bool:
        return self.walks[slot].running

    def _rate(self, walk: _Walk, *, whole_window: bool) -> float | None:
        """Return `walk`'s smoothed rate, or None when it has none, or when `whole_window` asks for a rate over the whole window and its samples do not span one yet."""
        window = RATE_WINDOW_SHARE * walk.texts
        if whole_window and not (walk.samples and walk.samples[0][1] <= walk.done - window):
            return None
        return smoothed_rate(walk.samples, window)

    def kind_rate(self, overlay: bool, now: float) -> float | None:
        """Return the texts a second of the units of one kind that have started, as the class docstring describes: their texts over their durations, a finished one's as it ran and a running one's elapsed time plus its remaining texts at its own rate. The running ones count only with a rate over the whole window, unless no unit of the kind qualifies; the result is None when none has a rate at all."""
        for whole_window in (True, False):
            texts = seconds = 0.0
            for walk in self.walks:
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

    def estimate(self, now: float) -> tuple[dict[int, float | None], float | None]:
        """Return the seconds from `now` at which each unfinished unit finishes, by slot, and at which the whole sweep does, by the schedule the class docstring describes. A unit without an estimate maps to None; so does every queued one, and the sweep, when any unfinished unit lacks one."""
        running: dict[int, float | None] = {}
        queued: list[tuple[int, float | None]] = []
        for slot, walk in enumerate(self.walks):
            if walk.finished is not None:
                continue
            if walk.first is None:
                rate = self.kind_rate(walk.overlay, now)
                queued.append((slot, None if rate is None else walk.texts / rate))
                continue
            rate = self._rate(walk, whole_window=True) or self.kind_rate(walk.overlay, now)
            running[slot] = None if rate is None else (walk.texts - walk.done) / rate
        known_running = {slot: left for slot, left in running.items() if left is not None}
        known_queued = [(slot, seconds) for slot, seconds in queued if seconds is not None]
        if len(known_running) < len(running) or len(known_queued) < len(queued):
            return {**running, **{slot: None for slot, _ in queued}}, None
        finishes = schedule_finish(known_running, known_queued, self.slots)
        return dict(finishes), max(finishes.values(), default=0.0)

    def config_finish(self, config: str, finishes: Mapping[int, float | None]) -> float | None:
        """Return the seconds from now at which `config`'s last unfinished unit finishes, from `estimate`'s finishes, or None when any of them has no estimate."""
        pending = [finishes.get(slot) for slot in self.configs[config] if self.walks[slot].finished is None]
        if any(seconds is None for seconds in pending):
            return None
        return max((seconds for seconds in pending if seconds is not None), default=0.0)

    def report(
        self, now: float, wall: float, footprints: Mapping[int, int | None], swap: int | None
    ) -> list[str]:
        """Return one progress report as of `now`, with `wall` the clock time at `now` for the finish's time of day: the total counter line, then one line per configuration in submission order, each summing the configuration's units. `footprints` maps the slot of each running unit to its worker's footprint, None where it could not be read, and `swap` is the machine's swap in use. Each rate since the last report is from this record's previous report, or from the start of the sweep or of the configuration's first unit when there was none."""
        finishes, total_finish = self.estimate(now)
        done = sum(walk.done for walk in self.walks)
        texts = sum(walk.texts for walk in self.walks)
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
        for config, slots in self.configs.items():
            walks = [self.walks[slot] for slot in slots]
            config_done = sum(walk.done for walk in walks)
            counts = f"deep sweep[{config}]: {console.fmt_count(config_done)}/{console.fmt_count(sum(walk.texts for walk in walks))} texts"
            starts = [walk.first[0] for walk in walks if walk.first is not None]
            if not starts:
                lines.append(f"{counts}, queued, {_fmt_finish(self.config_finish(config, finishes), wall)}")
                continue
            ends = [walk.finished for walk in walks if walk.finished is not None]
            if len(ends) == len(walks):
                lines.append(f"{counts}, finished in {console.fmt_duration(max(ends) - min(starts))}")
                continue
            live = [slot for slot in slots if self.walks[slot].running]
            units = (
                f", {len(live)} of {len(slots)} units running" + (f", {len(ends)} finished" if ends else "")
                if len(slots) > 1
                else ""
            )
            marked = self.config_marked.get(config)
            own_since = "since the last report" if marked is not None else "since it started"
            rate = _per_second(marked or (min(starts), 0), (now, config_done))
            held_by = [footprint for slot in live if (footprint := footprints.get(slot)) is not None]
            holds = (
                "no unit running"
                if not live
                else (
                    "footprint unreadable"
                    if not held_by
                    else f"holding {peak_rss.format_gb(sum(held_by))} GB"
                )
            )
            lines.append(
                f"{counts}{units}, running for {console.fmt_duration(now - min(starts))}, "
                f"{_fmt_rate(rate)} {own_since}, {_fmt_finish(self.config_finish(config, finishes), wall)}, {holds}"
            )
            self.config_marked[config] = (now, config_done)
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


def plan_sweep(max_length: int, memo_windows: int) -> SweepPlan:
    """Load what the sweep needs, bound each settlement unit's windows at `max_length` and keep the heaviest beside `memo_windows`, the ceiling each settlement unit's walk releases its windows at, run the plan-time check (`sweep_triggers`), and list the units, each with its configuration's trigger letters (`conform.trigger_letters`) when the configuration may skip texts. The tables are read only for the glyph inventory (`run_m1.mint_cell_glyphs`) and released once it is minted."""
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
    bounds = unit_window_bounds(spec, glyphs, max_length)
    heaviest = max(bounds, key=lambda symbol: bounds[symbol])
    bound_seconds = time.perf_counter() - started
    trigger_runes, unconfined = sweep_triggers(spec)
    letters = {config: conform.trigger_letters(spec, runes) for config, runes in trigger_runes.items()}
    return SweepPlan(
        spec=spec,
        glyphs=glyphs,
        windows=bounds[heaviest],
        heaviest=heaviest,
        bound_seconds=bound_seconds,
        units=sweep_units(conform.spec_alphabet(spec), max_length, letters),
        memo_windows=memo_windows,
        trigger_runes=trigger_runes,
        unconfined=unconfined,
    )


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


def _unit_worker(
    spec,
    font_path: Path,
    config: str,
    last: str | None,
    triggers: frozenset[str],
    max_length: int,
    glyphs,
    guard_verdicts,
    memo_windows: int,
    slot: int,
):
    """Run one unit's sweep in its own process, over the texts that contain one of `triggers` when there are any, with a settlement unit's walk releasing its windows before they pass `memo_windows`, storing its count of texts shaped in its `slot` of the shared array after each chunk, and return the result with the process's peak footprint (`run_m1._peak_footprint`), read just before it returns."""
    result = conform.conformance_config_worker(
        spec,
        font_path,
        config,
        max_length,
        glyphs,
        guard_verdicts,
        progress=_counter_writer(slot),
        last=last,
        triggers=triggers,
        max_windows=memo_windows,
    )
    return result, run_m1._peak_footprint()


def _run_units(
    units: Sequence[SweepUnit], jobs: int, report_every: float, task: Callable[[int], tuple]
) -> tuple[dict[int, conform.ConformanceConfigResult], dict[str, int]]:
    """Run every unit `jobs` at a time, one spawn process per unit, in `units` order, and return each slot's result and each configuration's highest unit peak. `task(slot)` gives the worker function and its arguments for the unit in `slot`; the worker returns its result and its peak. A progress report is printed every `report_every` seconds while any unit runs, as the module docstring describes. When a unit raises, the units still queued are cancelled, so only the ones already running finish before the error reaches the caller."""
    collected: dict[int, conform.ConformanceConfigResult] = {}
    peaks: dict[str, int] = {}
    context = multiprocessing.get_context("spawn")
    shaped = context.RawArray("q", len(units))
    pids = context.RawArray("q", len(units))
    began = context.RawArray("d", len(units))
    progress = SweepProgress(units, jobs, time.monotonic())
    next_report = progress.started + report_every
    with ProcessPoolExecutor(
        max_workers=jobs,
        mp_context=context,
        max_tasks_per_child=1,
        initializer=_attach_counters,
        initargs=(shaped, pids, began),
    ) as pool:
        slot_of = {pool.submit(*task(slot)): slot for slot in range(len(units))}
        pending = set(slot_of)
        try:
            while pending:
                timeout = max(0.0, min(SAMPLE_SECONDS, next_report - time.monotonic()))
                finished, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                now = time.monotonic()
                wall = time.time()
                starts = [now - max(0.0, wall - start) if start > 0 else None for start in began]
                for slot in range(len(units)):
                    if pids[slot]:
                        progress.observe(slot, shaped[slot], now, starts[slot])
                for future in finished:
                    slot = slot_of[future]
                    result, peak = future.result()
                    collected[slot] = result
                    peaks[result.config] = max(peaks.get(result.config, 0), peak)
                    progress.finish(slot, now, result.sequences, starts[slot])
                    console.progress(len(collected), len(units), "units")
                if pending and now >= next_report:
                    footprints = {
                        slot: peak_rss.footprint_bytes(pids[slot])
                        for slot in range(len(units))
                        if progress.running(slot)
                    }
                    for line in progress.report(now, wall, footprints, peak_rss.swap_used_bytes()):
                        console.say(line)
                    while next_report <= now:
                        next_report += report_every
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return collected, peaks


def _summary(report: conform.ConformReport, results: Sequence[conform.ConformanceConfigResult]) -> dict:
    """Return the summary a sweep prints and records: the report's counts beside the texts each configuration shaped, and its exemplars one line each."""
    summary: dict = {
        "sequences": report.sequences,
        "sequences_by_config": {result.config: result.sequences for result in results},
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


def run_sweep(
    plan: SweepPlan, max_length: int, jobs: int, report_every: float = REPORT_SECONDS_DEFAULT
) -> tuple[dict, dict[str, int]]:
    """Sweep every unit of `plan` at `max_length`, `jobs` at a time, one spawn process per unit (`_run_units`), and return the summary `run_m1.run_font_conformance` returns, with the texts each configuration shaped beside it, and each configuration's highest unit peak. The units go to the pool in `plan.units` order, so the overlay finishes in the first slot before a settlement unit takes it. The §5.7 guard verdicts are computed once here and passed to every worker. Each configuration's units merge into its result (`conform.merge_unit_results`), whose sequences must be every text of its lengths, or in a configuration whose units carry trigger letters every such text that contains one (`config_texts`), before anything is written, and the configurations merge in `conform.ACCEPTANCE_CONFIGS` order."""
    kernel_exec.ensure_built()
    guard_verdicts = kernel_exec.guard_sweep(plan.spec)
    font_path = run_m1.OUT_DIR / "M1.otf"
    units = plan.units
    collected, peaks = _run_units(
        units,
        jobs,
        report_every,
        lambda slot: (
            _unit_worker,
            plan.spec,
            font_path,
            units[slot].config,
            units[slot].last,
            units[slot].triggers,
            max_length,
            plan.glyphs,
            guard_verdicts,
            plan.memo_windows,
            slot,
        ),
    )
    triggers = {unit.config: unit.triggers for unit in units if unit.triggers}
    expected = config_texts(plan.spec, max_length, triggers)
    results = []
    for config in conform.ACCEPTANCE_CONFIGS:
        result = conform.merge_unit_results(
            config, [collected[slot] for slot, unit in enumerate(units) if unit.config == config]
        )
        if result.sequences != expected[config]:
            raise RuntimeError(
                f"deep sweep[{config}]: its units shaped {result.sequences} texts, not the {expected[config]} of every length it sweeps{' that contain one of its trigger letters' if config in triggers else ''}"
            )
        results.append(result)
    report = conform.merge_conformance_results(font_path, results)
    report.write(run_m1.OUT_DIR / SUMMARY_NAME)
    return _summary(report, results), peaks


def refresh_deep_replay(max_length: int, runes: dict[str, str]) -> int:
    """Record the deep replay as green at `max_length` for every rune at the digest in `runes`, the snapshot taken before the sweep started, since a green sweep at that depth settled every text that names any of them, in `default` alone where a configuration that passed the plan-time check settles it as `default` does, over the tables' imported windows on disk (`artifact_cycle.tables_imports_digest`). Return the maximum length the record holds, which stays deeper than `max_length` when the record already held every rune deeper at the same digest under the same structure stamp and imported windows."""
    from rebuild.pipeline.spec_load import load_default_spec

    return record_deep_replay_green(
        runes,
        max_length,
        run_m1.replay_structure_stamp(load_default_spec()),
        imports=tables_imports_digest(),
    )


def targeted_rule(rule: Rule) -> bool:
    """Return whether a table rule is in the `settle:bk1-la4` behavior class (`emit_gsub.behavior_classes`): it reads one glyph before its input and four after it, so it can match only a run of at least six glyphs."""
    return rule.backtrack is not None and rule.look4 is not None


def _sweep_key(alphabet: Sequence[str]) -> Callable[[str], tuple[int, list[int]]]:
    """Return the sort key that orders texts by length and then by `itertools.product` rank over `alphabet`, the order the exhaustive sweep walks them in."""
    order = {symbol: index for index, symbol in enumerate(alphabet)}
    return lambda text: (len(text), [order[ch] for ch in text])


def _family_text(spec: ResolvedSpec, label: str) -> str:
    """Return the characters that spell the family of one member of a rule's class. A settled glyph, a marker copy or tag, and a locked copy name their rune before the first `.` or `@`; `witness._token_text` spells a ligature rune by its sequence and a boundary label by its character."""
    return witness._token_text(spec, (label.partition(MARKER_TAG_SEPARATOR)[0].split(".")[0],))


def rule_contexts(spec: ResolvedSpec, rule: Rule) -> Iterator[str]:
    """Yield every text whose six positions take a family from `rule`'s backtrack class, its input, and its four lookahead classes in turn. A ligature family spans its sequence, so a context can be longer than six characters, and formation can merge two positions or keep a ligature's components apart, so a context places the rule's families without promising that the rule fires; its certificate does that."""
    slots = (rule.backtrack, (rule.input_glyph,), rule.look1, rule.look2, rule.look3, rule.look4)
    spellings = [sorted({_family_text(spec, label) for label in slot or ()}) for slot in slots]
    for parts in itertools.product(*spellings):
        yield "".join(parts)


@dataclass(frozen=True)
class TargetedTexts:
    """What the targeted mode shapes in each settlement configuration (`targeted_texts`): `groups` holds its texts in sweep order, one group per context with its variants and one per certificate, with no text in two groups. `rules` counts the configuration's own settle:bk1-la4 rules, whose certificates are all shaped, and `guards` counts those among them whose certificate is a guard certificate and is shaped under the configuration it names."""

    groups: Mapping[str, tuple[tuple[str, ...], ...]]
    rules: Mapping[str, int]
    guards: Mapping[str, int]


def targeted_texts(spec: ResolvedSpec, tables: Mapping[str, DecisionTable]) -> TargetedTexts:
    """Return the targeted mode's texts for every settlement configuration's table in `tables`. For each settle:bk1-la4 rule (`targeted_rule`) they are its certificate, the token stream the crate built to make it fire (`DecisionTable.certificates`), and its contexts (`rule_contexts`), each alone and after each alphabet symbol, so the backtrack letter's own left varies too. A certificate is shaped under its own configuration, except a guard certificate (`witness.GUARD_MARKER`), which is shaped under the configuration it names, the one whose stream the rule fires in. Each configuration's texts are deduplicated and grouped by context or certificate, the groups ordered by their base text's length and then its rank in `itertools.product` order over the alphabet. A table whose certificates do not match its rules one for one raises ValueError."""
    alphabet = conform.spec_alphabet(spec)
    certificates: dict[str, set[str]] = {config: set() for config in conform.SETTLEMENT_CONFIGS}
    contexts: dict[str, set[str]] = {config: set() for config in conform.SETTLEMENT_CONFIGS}
    rules: dict[str, int] = {}
    guards: dict[str, int] = {}
    for config in conform.SETTLEMENT_CONFIGS:
        decision = tables[config]
        if len(decision.certificates) != len(decision.rules):
            raise ValueError(
                f"{config}: the table carries {len(decision.certificates)} certificate(s) for {len(decision.rules)} rule(s)"
            )
        rules[config] = guards[config] = 0
        for rule, tokens in zip(decision.rules, decision.certificates):
            if not targeted_rule(rule):
                continue
            rules[config] += 1
            source = config
            if tokens and tokens[0] == witness.GUARD_MARKER:
                source, tokens = tokens[1], tokens[2:]
                guards[config] += 1
            certificates[source].add(witness._token_text(spec, tokens))
            contexts[config].update(rule_contexts(spec, rule))
    groups: dict[str, tuple[tuple[str, ...], ...]] = {}
    for config in conform.SETTLEMENT_CONFIGS:
        seen: set[str] = set()
        ordered: list[tuple[str, ...]] = []
        for base in sorted(contexts[config] | certificates[config], key=_sweep_key(alphabet)):
            variants = (
                (base, *(symbol + base for symbol in alphabet)) if base in contexts[config] else (base,)
            )
            fresh = tuple(text for text in variants if text not in seen)
            seen.update(fresh)
            if fresh:
                ordered.append(fresh)
        groups[config] = tuple(ordered)
    return TargetedTexts(groups=groups, rules=rules, guards=guards)


def targeted_units(
    alphabet: Sequence[str],
    groups: Mapping[str, Sequence[Sequence[str]]],
    unit_texts: int = TARGETED_UNIT_TEXTS,
) -> tuple[tuple[SweepUnit, ...], tuple[tuple[str, ...], ...]]:
    """Return the targeted mode's units and each unit's texts: each settlement configuration's groups, in order, cut into units of at least `unit_texts` texts, its last unit holding the rest, so a context's variants share one walk. A unit's `last` is None, since it covers a slice of one configuration's texts rather than the texts that end in one symbol. Within a unit the texts are in sweep order, by length and then `itertools.product` rank over `alphabet`, the order `conform.DivergenceTally` ranks a unit's divergences in and `conform.merge_unit_results` merges them back in."""
    key = _sweep_key(alphabet)
    units: list[SweepUnit] = []
    slices: list[tuple[str, ...]] = []

    def cut(config: str, texts: list[str]) -> None:
        units.append(SweepUnit(config, None, len(texts)))
        slices.append(tuple(sorted(texts, key=key)))

    for config in conform.SETTLEMENT_CONFIGS:
        pending: list[str] = []
        for group in groups.get(config, ()):
            pending.extend(group)
            if len(pending) >= unit_texts:
                cut(config, pending)
                pending = []
        if pending:
            cut(config, pending)
    return tuple(units), tuple(slices)


@dataclass(frozen=True)
class TargetedPlan:
    """What the targeted mode needs before it spawns anything: the spec, the glyph inventory, the texts (`targeted_texts`), the units and each unit's texts (`targeted_units`), the most characters any unit's texts hold, which bounds the windows its walk can hold, and the window ceiling each unit's walk releases its windows at."""

    spec: ResolvedSpec
    glyphs: Mapping
    texts: TargetedTexts
    units: tuple[SweepUnit, ...]
    slices: tuple[tuple[str, ...], ...]
    windows: int
    memo_windows: int


def plan_targeted(memo_windows: int) -> TargetedPlan:
    """Load the spec, the stamped tables' heads and the glyph inventory, derive the targeted texts and cut them into units. A unit's walk asks for at most one window per letter position of its texts, so the count of its texts' characters bounds the windows it holds."""
    from rebuild.pipeline.spec_load import load_default_spec

    inputs = run_m1.tables_inputs()
    spec = load_default_spec()
    serialized = run_m1.serialized_tables(run_m1.OUT_DIR, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {run_m1.OUT_DIR} are missing, unreadable, or were built from other sources than the ones on disk — run `make artifact-cycle` first"
        )
    glyphs = run_m1.mint_cell_glyphs(spec, serialized)
    texts = targeted_texts(spec, serialized)
    del serialized
    units, slices = targeted_units(conform.spec_alphabet(spec), texts.groups)
    return TargetedPlan(
        spec=spec,
        glyphs=glyphs,
        texts=texts,
        units=units,
        slices=slices,
        windows=max((sum(map(len, unit_texts)) for unit_texts in slices), default=0),
        memo_windows=memo_windows,
    )


def _texts_worker(
    spec,
    font_path: Path,
    config: str,
    texts: Sequence[str],
    glyphs,
    guard_verdicts,
    memo_windows: int,
    slot: int,
):
    """Run one targeted unit in its own process: settle `texts` in order through a `conform._SettledWindowWalk` whose horizon is the longest of them and whose windows are released before they pass `memo_windows`, and give each the per-text checks `conform._conformance_config` gives a settlement configuration's text, `conform.check_split_buffer` where it holds a splitter, `conform.check_oracle`, and `conform.check_join_gaps`. Store the count of texts shaped in the unit's `slot` after each chunk of `conform.TEXT_CHUNK`, and return the result with the process's peak footprint. It lives here and not as a mode of `conform._conformance_config` because conform.py is in the tables' stamp and the oracle's row-code closure, which an edit there invalidates."""
    store = _counter_writer(slot)
    shaper = conform.Shaper(Path(font_path))
    features = features_for_config(config)
    splitters = conform.splitting_boundary_chars(spec)
    anchors_of = (
        conform.anchors_in_font_units({record.name: record for record in glyphs.values()}) if glyphs else None
    )
    result = conform.ConformanceConfigResult(config=config)
    divergences = conform.DivergenceTally(result, conform.spec_alphabet(spec))
    modes: set[str] = set()
    walker = conform._SettledWindowWalk(
        spec,
        features,
        {cell: record.name for cell, record in glyphs.items()},
        guard_verdicts,
        promote=False,
        horizon=max(map(len, texts)),
        max_windows=memo_windows,
    )
    for start in range(0, len(texts), conform.TEXT_CHUNK):
        chunk = texts[start : start + conform.TEXT_CHUNK]
        for text, (_settled, names) in zip(chunk, walker.walk_many(chunk)):
            shaped = shaper.shape(text, features)
            result.shaping_runs += 1
            if splitters.intersection(text):
                conform.check_split_buffer(text, config, features, shaper, shaped, divergences, splitters)
            conform.check_oracle(text, config, shaped, names, divergences, modes)
            if anchors_of is not None:
                conform.check_join_gaps(text, config, shaper, shaped, anchors_of, divergences)
        result.sequences += len(chunk)
        if store is not None:
            store(result.sequences)
    result.modes = sorted(modes)
    return result, run_m1._peak_footprint()


def run_targeted(
    plan: TargetedPlan, jobs: int, report_every: float = REPORT_SECONDS_DEFAULT
) -> tuple[dict, dict[str, int]]:
    """Shape every targeted unit of `plan`, `jobs` at a time, one spawn process per unit (`_run_units`), and return the summary, with the texts each configuration shaped beside it, and each configuration's highest unit peak. Each configuration's units merge into its result, whose sequences must be every text planned for it before anything is written, and the summary written to TARGETED_SUMMARY_NAME counts every configuration's texts."""
    kernel_exec.ensure_built()
    guard_verdicts = kernel_exec.guard_sweep(plan.spec)
    font_path = run_m1.OUT_DIR / "M1.otf"
    units = plan.units
    collected, peaks = _run_units(
        units,
        jobs,
        report_every,
        lambda slot: (
            _texts_worker,
            plan.spec,
            font_path,
            units[slot].config,
            plan.slices[slot],
            plan.glyphs,
            guard_verdicts,
            plan.memo_windows,
            slot,
        ),
    )
    results = []
    for config in conform.SETTLEMENT_CONFIGS:
        result = conform.merge_unit_results(
            config, [collected[slot] for slot, unit in enumerate(units) if unit.config == config]
        )
        planned = sum(len(group) for group in plan.texts.groups.get(config, ()))
        if result.sequences != planned:
            raise RuntimeError(
                f"deep sweep[{config}]: its targeted units shaped {result.sequences} texts, not the {planned} planned for it"
            )
        results.append(result)
    report = conform.merge_conformance_results(font_path, results)
    report.sequences = sum(result.sequences for result in results)
    report.write(run_m1.OUT_DIR / TARGETED_SUMMARY_NAME)
    return _summary(report, results), peaks


def targeted_rules_digest(decision: DecisionTable) -> str:
    """Return a hash of a table's settle:bk1-la4 rules in rule order: each one's input, backtrack and lookahead classes, outcome, and certificate. Provenance and the joint flag are left out, and so is the alphabet each context is also shaped after, so a new letter that changes none of these rules leaves the mode current."""
    rows = [
        [
            rule.input_glyph,
            *(list(slot or ()) for slot in (rule.backtrack, rule.look1, rule.look2, rule.look3, rule.look4)),
            rule.outcome,
            list(decision.certificates[index]) if index < len(decision.certificates) else None,
        ]
        for index, rule in enumerate(decision.rules)
        if targeted_rule(rule)
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def targeted_lines(root: Path = ROOT) -> list[str] | None:
    """Return the targeted mode's record key lines: the deep sweep's (`deep_sweep_skip_lines`) and one `settle:bk1-la4[<config>]` line per settlement configuration with `targeted_rules_digest` over the table head its M1 build left. A rune edit that changes a settle:bk1-la4 rule or its certificate therefore makes the mode due, as does a change in the deep sweep's lines, and a rune edit that changes none leaves it current. None when there is no behavior-class sidecar or a head is missing or unreadable."""
    lines = deep_sweep_skip_lines(root)
    if lines is None:
        return None
    out_dir = root / "rebuild" / "out" / "m1"
    for config in conform.SETTLEMENT_CONFIGS:
        try:
            _stamp, decision = table.read_windows(table.windows_path(out_dir, config), windows=False)
        except OSError, ValueError:
            return None
        lines.append(f"{TARGETED_CLASS}[{config}]\t{targeted_rules_digest(decision)}")
    return lines


def targeted_status(root: Path = ROOT) -> tuple[str, str]:
    """Return whether the targeted mode is current for what the build emits, as (status, note): `current` when its green record matches the key (`targeted_lines`), `due` naming what moved since, `never-run` without a record, and `unknown` when the build has left no key to compare. The artifact cycle does not run or report it."""
    lines = targeted_lines(root)
    if lines is None:
        return "unknown", "no behavior-class sidecar or table heads yet; they land with the next M1 build"
    record = read_green_record(cycle_paths.TARGETED_SWEEP_GREEN)
    if record is None:
        return "never-run", f"no targeted sweep has been recorded; run {TARGETED_COMMAND}"
    if record["fingerprint"] != _digest_lines(lines):
        moved = moved_inputs_note(record, dict(line.split("\t", 1) for line in lines))
        return "due", f"{f'{moved}; ' if moved else ''}run {TARGETED_COMMAND}"
    return "current", f"every {TARGETED_CLASS} rule's certificate and contexts shaped"


def run_targeted_mode(args: argparse.Namespace, argv: list[str] | None) -> int:
    """`main` for `--targeted`: plan, shape, report, record a check line, and record the targeted green record on a green run whose key did not move while it ran, or clear a contradicted one on a red run. It refreshes no other record."""
    record_key()
    lines = targeted_lines(ROOT)
    if lines is None:
        raise SystemExit(
            "no settlement table heads under rebuild/out/m1 — run `make artifact-cycle` first so a build can leave them"
        )
    key = _digest_lines(lines)
    stated = args.jobs if args.jobs is not None else stated_jobs()
    report_every = report_seconds()
    plan = plan_targeted(sweep_memo_windows())
    settlement_bytes = settlement_worker_bytes(plan.windows, plan.memo_windows)
    derived = sweep_width(settlement_bytes, len(plan.units), overlay=False)
    jobs = max(1, min(stated, len(plan.units))) if stated is not None else derived
    source = (
        f"stated by {'--jobs' if args.jobs is not None else JOBS_ENV}, where this machine's memory fits {derived}"
        if stated is not None
        else "from this machine's memory"
    )
    planned = {
        config: sum(len(group) for group in plan.texts.groups[config])
        for config in conform.SETTLEMENT_CONFIGS
    }
    counts = "; ".join(
        f"{config} {console.fmt_count(plan.texts.rules[config])} rules, {console.fmt_count(planned[config])} texts"
        for config in conform.SETTLEMENT_CONFIGS
    )
    print(
        f"deep sweep: targeted at the {TARGETED_CLASS} rules, every rule's certificate and every text whose six positions take a family from its backtrack class, its input and its four lookahead classes, alone and after each of the {len(conform.spec_alphabet(plan.spec))} symbols, over every settlement configuration ({counts}) at {jobs} jobs {source}, one process per unit, {len(plan.units)} units of at least {console.fmt_count(TARGETED_UNIT_TEXTS)} texts but each configuration's last; "
        f"the heaviest unit's texts hold {plan.windows} characters, so its walk holds at most {min(plan.windows, plan.memo_windows)} windows, {peak_rss.format_gb(settlement_bytes)} GB at {DEEP_SWEEP_WINDOW_BYTES} bytes each beside {peak_rss.format_gb(DEEP_SWEEP_BASE_BYTES)} GB; "
        f"{sweep_width_derivation(settlement_bytes, len(plan.units), overlay=False)}; a progress report every {console.fmt_duration(report_every)}",
        flush=True,
    )
    shortfall = memory_shortfall(settlement_bytes, overlay=False)
    if shortfall is not None:
        refuse, sentence = shortfall
        if refuse and stated is None:
            raise SystemExit(f"deep sweep: {sentence}; pass --jobs 1 or set {JOBS_ENV}=1 to run it anyway")
        console.warn(f"deep sweep: {sentence}")
    started = time.perf_counter()
    summary, peaks = run_targeted(plan, jobs, report_every=report_every)
    elapsed = time.perf_counter() - started
    print(json.dumps(summary, indent=2))
    shaped = summary.get("sequences_by_config", {})
    for config in conform.SETTLEMENT_CONFIGS:
        guarded = plan.texts.guards[config]
        under = f" ({guarded} of them under the configuration their guard names)" if guarded else ""
        peak = (
            f", highest unit peak footprint {peak_rss.format_gb(peaks[config])} GB against an estimate of {peak_rss.format_gb(settlement_bytes)} GB"
            if config in peaks
            else ""
        )
        print(
            f"deep sweep[{config}]: targeted, {console.fmt_count(shaped.get(config, 0))} texts, the certificates of its {console.fmt_count(plan.texts.rules[config])} {TARGETED_CLASS} rules among them{under}{peak}",
            flush=True,
        )
    print(f"[t] {TARGETED_CHECK} {elapsed:.1f}s", flush=True)
    green = bool(summary["pass"]) and not summary["divergences"]
    record_check(
        CheckResult(
            check=TARGETED_CHECK,
            outcome="green" if green else "red",
            status="green" if green else f"FAILED ({summary['divergences']} divergences)",
            failures=(
                []
                if green
                else [f"{summary['divergences']} font-vs-settle divergence(s) in the targeted sweep"]
            ),
            failed_ids=[],
        ),
        argv=list(argv) if argv is not None else sys.argv[1:],
        elapsed_s=elapsed,
        peak_rss_bytes=peak_rss.peak_rss_children_bytes(),
        worker_peak_footprint_bytes=peaks,
        worker_estimate_bytes={config: settlement_bytes for config in conform.SETTLEMENT_CONFIGS},
    )
    if not green:
        clear_contradicted_green(cycle_paths.TARGETED_SWEEP_GREEN, key)
        print(
            f"deep sweep: {summary['divergences']} font-vs-settle divergence(s) in the targeted sweep; see {TARGETED_SUMMARY_NAME}",
            file=sys.stderr,
        )
        return 1
    after = targeted_lines(ROOT)
    if after is None or _digest_lines(after) != key:
        print(
            "deep sweep: targeted green, but its inputs changed while it ran — green not recorded", flush=True
        )
        return 0
    record_green(cycle_paths.TARGETED_SWEEP_GREEN, key, files=dict(line.split("\t", 1) for line in lines))
    print(f"deep sweep: targeted green — recorded in {cycle_paths.TARGETED_SWEEP_GREEN.name}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deep form of the font-vs-settle conformance sweep and record its green."
    )
    parser.add_argument(
        "--max-length",
        "--horizon",
        type=int,
        default=None,
        help=f"maximum sweep length (default {DEEP_SWEEP_MAX_LENGTH_DEFAULT}); anything below the per-edit sweep's own {CONFORM_MAX_LENGTH_DEFAULT} is refused, since the per-edit sweep already covers that on every edit",
    )
    parser.add_argument(
        "--targeted",
        action="store_true",
        help=f"shape only the texts derived from the {TARGETED_CLASS} rules, which no text up to maximum length 5 can fire: each rule's certificate and every text whose six positions take a family from its six classes, alone and after each symbol; it has its own green record, keyed on those rules, and takes no --max-length",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"for debugging only: how many units sweep at once, overriding {JOBS_ENV} and the width this machine's memory fits at --max-length; narrowed to the unit count and never narrowed by memory; it also starts a run whose one settlement unit exceeds the machine's memory, which is otherwise refused",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print whether the deep sweep, or with --targeted the targeted mode, is current or due and exit, sweeping nothing (exit 0 when current)",
    )
    args = parser.parse_args(argv)

    if args.targeted:
        if args.max_length is not None:
            raise SystemExit(
                f"--targeted shapes the texts the {TARGETED_CLASS} rules give, not every text up to a maximum length; drop --max-length"
            )
        if args.status:
            status, note = targeted_status(ROOT)
            print(f"deep sweep, targeted: {status} — {note}")
            return 0 if status == "current" else 1
        return run_targeted_mode(args, argv)
    if args.max_length is None:
        args.max_length = DEEP_SWEEP_MAX_LENGTH_DEFAULT

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
    plan = plan_sweep(args.max_length, sweep_memo_windows())
    settlement_bytes = settlement_worker_bytes(plan.windows, plan.memo_windows)
    derived = sweep_width(settlement_bytes, len(plan.units))
    jobs = min(max(1, stated), len(plan.units)) if stated is not None else derived
    source = (
        f"stated by {'--jobs' if args.jobs is not None else JOBS_ENV}, where this machine's memory fits {derived}"
        if stated is not None
        else "from this machine's memory"
    )
    print(
        f"deep sweep: maximum length {args.max_length} over every acceptance configuration at {jobs} jobs {source}, one process per unit, {len(plan.units)} units: the ss10 overlay whole at its own maximum length, and each settlement configuration once per symbol its texts end in; "
        f"the heaviest settlement unit, the texts that end in {plan.heaviest}, would hold at most {plan.windows} windows (every unit bounded in {plan.bound_seconds * 1000:.1f} ms) and every unit's walk releases its windows before they pass {plan.memo_windows}, so a unit holds at most {min(plan.windows, plan.memo_windows)}, {peak_rss.format_gb(settlement_bytes)} GB at {DEEP_SWEEP_WINDOW_BYTES} bytes each beside {peak_rss.format_gb(DEEP_SWEEP_BASE_BYTES)} GB; "
        f"{sweep_width_derivation(settlement_bytes, len(plan.units))}; {triggers_clause(plan)}; a progress report every {console.fmt_duration(report_every)}",
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
    shaped = summary.get("sequences_by_config", {})
    for config in conform.ACCEPTANCE_CONFIGS:
        if config in peaks:
            texts = f"{console.fmt_count(shaped[config])} texts, " if config in shaped else ""
            only = (
                f"the ones that name {_series(sorted(plan.trigger_runes[config]))}, "
                if config in plan.trigger_runes
                else ""
            )
            print(
                f"deep sweep[{config}]: {texts}{only}highest unit peak footprint {peak_rss.format_gb(peaks[config])} GB against an estimate of {peak_rss.format_gb(estimates[config])} GB",
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
    recorded = record_deep_sweep_green(deep_key, args.max_length, files=deep_sweep_skip_files(ROOT))
    note = (
        f"{cycle_paths.DEEP_SWEEP_GREEN.name} keeps maximum length {recorded} from an earlier green over the same shapes"
        if recorded != args.max_length
        else f"recorded in {cycle_paths.DEEP_SWEEP_GREEN.name}"
    )
    print(f"deep sweep: green at maximum length {args.max_length} — {note}", flush=True)
    unchecked = (
        f"{_series(list(plan.unconfined))} failed the plan-time check, and a refresh stands for the texts a configuration skips only when the check passed in every configuration"
        if plan.unconfined
        else None
    )
    if args.max_length >= DEEP_REPLAY_MAX_LENGTH_DEFAULT:
        if unchecked is not None:
            print(f"deep replay: not recorded — {unchecked}", flush=True)
        elif fingerprint.rune_digests(ROOT) != runes:
            print(
                "deep replay: not recorded — the runes changed while the sweep ran, so the swept font does not describe the runes on disk",
                flush=True,
            )
        else:
            replay_recorded = refresh_deep_replay(args.max_length, runes)
            kept = (
                f", and {cycle_paths.DEEP_REPLAY_GREEN.name} keeps maximum length {replay_recorded} from an earlier walk over the same runes"
                if replay_recorded != args.max_length
                else ""
            )
            print(
                f"deep replay: green too — every text up to length {args.max_length} was settled here, in `default` alone where a configuration settles it as `default` does{kept}, so nothing is left for `make replay-deep` to walk",
                flush=True,
            )
    if unchecked is not None:
        print(f"gate:conform: not refreshed — {unchecked}", flush=True)
    elif (
        args.max_length >= CONFORM_MAX_LENGTH_DEFAULT
        and conform_skip_fingerprint(ROOT, CONFORM_MAX_LENGTH_DEFAULT) == conform_key
    ):
        record_green(
            cycle_paths.CONFORM_GREEN, conform_key, files=conform_skip_files(ROOT, CONFORM_MAX_LENGTH_DEFAULT)
        )
        print(
            f"gate:conform: green too — every per-edit sweep text up to length {CONFORM_MAX_LENGTH_DEFAULT} was swept here, in `default` alone where a configuration shapes and settles it as `default` does, so the next cycle skips it",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
