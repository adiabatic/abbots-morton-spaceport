"""The deep sweep's cheap form (issue #189): the crate's `replay-strings` walk one letter past the belt's horizon, over the texts naming the runes whose content moved since the last walk, keyed on rune content the way the settle memo and the oracle row cache are. Every per-edit gate walks at horizon 4, where a text exercises a letter third slot only behind a boundary left and a letter fourth slot never, so a fault confined to the deep slots behind a letter left — §10's fault table, row 4 — is invisible to all of them together; `make conform-deep` sees it, at the hours a whole-universe HarfBuzz sweep costs, and this tool sees it at the minutes a per-family replay costs, because it settles nothing a shaper would add and walks only what an edit could have moved.

It stays out of the per-edit path on purpose. Priced on the live alphabet (`make cycle-timings ARGS='--by-step'` carries every run of this tool under `replay-deep`, the check it files itself as), a walk over the texts naming one family costs each configuration several times what the build's own whole-universe horizon-4 replay costs, so riding `run_m1` would lengthen every rune-edit build by several times its own replay; the cycle arms this walk instead and reports it beside the deep sweep (`artifact_cycle.deep_replay_status`), and `make replay-deep` is the remedy. The window ceiling (`DEEP_REPLAY_MEMO_WINDOWS`) holds what a walk keeps flat in the corpus, apart from the settled records and labels it never releases, which grow with distinct records, so every configuration walks at once on either fleet box at the priced ceiling, which `rebuild/test_deep_replay.py` pins. A walk is a claim about the runes as they stood, so the green record (`cycle_paths.DEEP_REPLAY_GREEN`) carries every rune's prose-blind digest and the horizon it walked at, and the next walk covers the runes whose digest moved since, closed under `spec_load.rune_closure` — every rune whose records read a moved rune's content — which is the O(delta) form the window-locality theorem licenses. The structure stamp rides along for the record and never widens the walk: a code or structure change is the whole-universe deep sweep's question, not this tool's, and the cycle's deep-sweep line is where that shows.

Run as: uv run python -m rebuild.tools.deep_replay, or through `make replay-deep`. `--families` names the runes to walk instead of asking the record; `--all` walks the whole universe, which on the live alphabet is an overnight run.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.pipeline import conform, fingerprint, kernel_exec, run_m1, spec_load
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import cycle_paths, memory_budget, peak_rss
from rebuild.tools.artifact_cycle import (
    CONFORM_HORIZON_DEFAULT,
    DEEP_REPLAY_HORIZON_DEFAULT,
    deep_replay_moved,
    deep_replay_status,
    read_green_record,
    record_deep_replay_green,
)
from rebuild.tools.cycle_timings import CheckVerdict, record_check
from rebuild.tools.deep_sweep import tables_stamped

# The most windows one configuration's walk holds memoized: before any text that could carry the walk memo past it, the walk releases that memo and its engine's memos and walks on (the crate's `--memo-windows`; `AMS_DEEP_REPLAY_MEMO_WINDOWS` states another). It is counted in windows rather than bytes, so a release fires at the same point on every box and the printed window count is a function of the rune set. It sits at a hash-table capacity step of the walk memo, 7/8 of 2^23, which is the most a table of that many buckets holds before it doubles: the memo never passes the ceiling, so it fills that table exactly and never doubles into a larger one that a release's `clear()` would then keep, and the `--cache-census` rows of the walks DEEP_REPLAY_PEAK_BYTES cites read the walk memo at that capacity at every release and at the end of every walk, never above it. It is the largest of the three steps measured (1,835,008, 3,670,016 and 7,340,032 windows) whose DEEP_REPLAY_PEAK_BYTES seed stays under 5.2 GB, the margin below the 32 GiB box's fifth walk; all three keep both fleet boxes at the configuration count. What it costs, over `default` at horizon 5 with `--families=qsAh` on the alphabet with ·Ye, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`): 15,469,605 window settles against the uncapped walk's 14,748,571 distinct windows, 4.9% more, in 45.4 s of walk against the uncapped 42.8 s, where the two smaller steps settle 10.5% and 18.1% more in 46.0 s and 48.6 s, so a larger ceiling costs fewer settles and less wall.
DEEP_REPLAY_MEMO_WINDOWS = 7_340_032

# What one configuration's walk holds at its peak under DEEP_REPLAY_MEMO_WINDOWS: the walk memo, at most the ceiling; the engine's trace, candidate, prospect, closure, delta and reads memos, with no explain ladder (`Replay::new` pins it off), released together with the walk memo and holding what the walk's misses settled since the last release, so the ceiling bounds them only through the engine entries each walk window costs — the trace memo also holds the follower windows a simulated prospect settles, so it runs past the walk memo: at the first release of the solo walk below it holds 14,191,326 entries beside the walk memo's 7,340,029, which fills 97% of its own table's capacity step, so a walk whose windows cost more trace entries each doubles that table before the walk memo reaches the ceiling; and beside them what the walk never releases, the settled records, their labels and the input labels (464 records and 549 labels at the end of that walk), which grow with distinct records rather than with windows settled. With ·Ye in the alphabet, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), one `replay-strings` over `default` alone at horizon 5, `--families=qsAh`, `--threads=1`, `--memo-windows` at the ceiling and `--cache-census`, under `/usr/bin/time -l`, peaks at 2.85 GB of footprint and 2.90 GB maxrss, walking 8,127,145 texts in 15,469,605 window settles over two releases in 45.4 s; the uncapped walk reads 5.60 GB in 42.8 s, and 20.52 GB with the explain ladder on. The same walk over `--families=qsAh,qsIt` reads 2.88 GB of footprint over 15,381,526 texts and four releases, level with one family's, which is the peak holding flat in the corpus; over every settlement configuration at once at `--threads=5` it reads 14.20 GB of whole-process footprint and 14.46 GB maxrss, 2.84 GB a configuration, with 3.8 s of system time against 278 s of user and swap unmoved, and each `[t] replay[<config>]` line at 56.9 to 57.6 s against the solo 45.4: five walks sharing the cores' memory rather than paging. It rounds the higher of the solo footprint and maxrss up by a quarter and then to the tenth, for the growth of the holders the walk never releases and the run-to-run spread, and for the reason every divisor here rounds up: a per-unit cost that errs low is what puts a box into swap. At this figure both fleet boxes walk every settlement configuration at once, which `rebuild/test_deep_replay.py` pins. Re-measure it whenever the ceiling moves, the alphabet grows, the trace memo's key or entry or the walk memo's key changes shape, or the engine's memos change what they hold or how many entries each walk window costs them, since the last three move a walk's peak at a fixed ceiling: the solo walk above, seeded from the higher of its footprint and maxrss, and the same walk over every settlement configuration at once, where a `[t] replay[<config>]` line that lengthens against the solo walk while the system time climbs is the width having been bought with paging. The logs are under `var/keep/issue-274/m5pro-48gib/`.
DEEP_REPLAY_PEAK_BYTES = 3_700_000_000

# The step name every walk files itself under in the cycle-timings journal (`rebuild.tools.cycle_timings.record_check`), which is where `make cycle-timings ARGS='--by-step'` prices it: no cycle spawns this tool, so it records its own line the way the other interactive entry points do.
CHECK = "replay-deep"


def replay_threads(total_bytes: int | None = None) -> int:
    """How many configurations walk at once: `AMS_DEEP_REPLAY_THREADS` wherever it is set, else `DEEP_REPLAY_PEAK_BYTES` divided into the box by `memory_budget.how_many_fit`, capped at the settlement configurations. A stated width is floored at one and clamped no further, as `kernel_exec.kernel_threads_default` treats its own knob; a value that is not a bare count raises rather than falling through to the arithmetic. The divisor is priced at `DEEP_REPLAY_MEMO_WINDOWS`: a ceiling stated above it through `AMS_DEEP_REPLAY_MEMO_WINDOWS` is priced by nothing here, so state `AMS_DEEP_REPLAY_THREADS` beside it."""
    stated = os.environ.get("AMS_DEEP_REPLAY_THREADS")
    if stated is not None:
        try:
            return max(1, int(stated))
        except ValueError:
            raise RuntimeError(
                f"AMS_DEEP_REPLAY_THREADS={stated!r} is not a width: it takes a bare decimal count of configurations to walk at once"
            ) from None
    return memory_budget.how_many_fit(
        DEEP_REPLAY_PEAK_BYTES, cap=len(conform.SETTLEMENT_CONFIGS), total_bytes=total_bytes
    )


def replay_memo_windows() -> int:
    """The most windows each configuration's walk holds memoized before it releases its memos and walks on: `AMS_DEEP_REPLAY_MEMO_WINDOWS` wherever it is set, else `DEEP_REPLAY_MEMO_WINDOWS`. A stated ceiling is a bare decimal count of at least one, and anything else raises rather than falling back to the default, as `replay_threads` treats its own knob. It counts windows rather than bytes because a byte trigger would fire at a different point on each box, and the window count the walk prints, which counts settles once a release fires, would then be a fact about the machine rather than about the runes."""
    stated = os.environ.get("AMS_DEEP_REPLAY_MEMO_WINDOWS")
    if stated is None:
        return DEEP_REPLAY_MEMO_WINDOWS
    if re.fullmatch(r"[0-9]+", stated) is None or int(stated) < 1:
        raise RuntimeError(
            f"AMS_DEEP_REPLAY_MEMO_WINDOWS={stated!r} is not a memo ceiling: it takes a bare decimal count of windows, at least one"
        )
    return int(stated)


def families_to_walk(spec, record: dict | None, runes: dict[str, str]) -> list[str]:
    """The runes whose texts this walk covers: every rune whose digest the record does not carry at its current value, closed under `spec_load.rune_closure` so a rune whose records read a moved rune's content walks too, sorted. Empty when nothing moved."""
    moved = set(deep_replay_moved(record, runes))
    if not moved:
        return []
    closure = spec_load.rune_closure(spec)
    edited = {name for name, reads in closure.items() if reads & moved} | (moved & spec.runes.keys())
    return sorted(edited)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walk the string universe one letter past the belt over the texts naming the runes that moved, holding the tables to the engine, and record the walk."
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=DEEP_REPLAY_HORIZON_DEFAULT,
        help=f"walk length (default {DEEP_REPLAY_HORIZON_DEFAULT}); anything at or below the build's own {CONFORM_HORIZON_DEFAULT} is refused, since every build already walks that depth",
    )
    parser.add_argument(
        "--families",
        help="comma-separated rune names to walk instead of the ones the record says moved",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="walk the whole universe rather than the moved runes' texts; an overnight run on the live alphabet",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="how many settlement configurations walk at once (default: what this box's memory fits, AMS_DEEP_REPLAY_THREADS to state one)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print whether the deep replay is current or armed and exit, walking nothing (exit 0 when current)",
    )
    args = parser.parse_args(argv)

    if args.status:
        status, note = deep_replay_status(ROOT, args.horizon)
        print(f"deep replay: {status} — {note}")
        return 0 if status == "current" else 1

    if args.horizon <= CONFORM_HORIZON_DEFAULT:
        raise SystemExit(
            f"--horizon {args.horizon} is no deeper than the build's own replay at {CONFORM_HORIZON_DEFAULT}; every build already walks that depth"
        )
    if not tables_stamped():
        raise SystemExit(
            "the M1 tables are stale relative to the runes on disk — run `make artifact-cycle` (or a bare M1 build) first, so the walk never holds tables the sources have outgrown"
        )
    spec = load_default_spec()
    runes = fingerprint.rune_digests(ROOT)
    structure = run_m1.replay_structure_stamp(spec)
    record = read_green_record(cycle_paths.DEEP_REPLAY_GREEN)
    if args.all:
        families = None
    elif args.families:
        families = sorted({name.strip() for name in args.families.split(",") if name.strip()})
        unknown = [name for name in families if name not in spec.runes]
        if unknown:
            raise SystemExit(f"--families names runes this spec does not model: {', '.join(unknown)}")
    else:
        if record is None:
            raise SystemExit(
                "no deep replay has been recorded yet, so there is no last walk to cut a delta against: name the runes with --families, or walk everything with --all"
            )
        families = families_to_walk(spec, record, runes)
        if not families:
            print(f"deep replay: nothing moved since the last walk at horizon {record.get('horizon')}")
            return 0
    threads = max(1, args.threads) if args.threads is not None else replay_threads()
    memo_windows = replay_memo_windows()
    walked = "the whole universe" if families is None else f"{len(families)} families ({', '.join(families)})"
    print(
        f"deep replay: horizon {args.horizon} over {walked}, {threads} configurations at a time, at most {memo_windows} windows memoized per walk",
        flush=True,
    )
    started = time.perf_counter()
    try:
        answered = kernel_exec.replay_strings(
            spec,
            run_m1.OUT_DIR,
            conform.SETTLEMENT_CONFIGS,
            horizon=args.horizon,
            families=families,
            threads=threads,
            memo_windows=memo_windows,
            timings=True,
        )
    except kernel_exec.ReplayDisagreement as error:
        message = f"the tables disagree with the engine at horizon {args.horizon}: {error}"
        print(f"deep replay: {message}", file=sys.stderr)
        record_check(
            CheckVerdict(check=CHECK, verdict="red", status="FAILED", failures=[message], failed_ids=[]),
            argv=list(argv) if argv is not None else sys.argv[1:],
            elapsed_s=time.perf_counter() - started,
            peak_rss_bytes=peak_rss.peak_rss_children_bytes(),
        )
        return 1
    elapsed = time.perf_counter() - started
    print(f"[t] {CHECK} {elapsed:.1f}s", flush=True)
    record_check(
        CheckVerdict(check=CHECK, verdict="green", status="green", failures=[], failed_ids=[]),
        argv=list(argv) if argv is not None else sys.argv[1:],
        elapsed_s=elapsed,
        peak_rss_bytes=peak_rss.peak_rss_children_bytes(),
    )
    for config in conform.SETTLEMENT_CONFIGS:
        counts = answered[config]
        print(
            f"deep replay[{config}]: {counts['texts']} texts, {counts['windows']} window settles, {counts['skipped']} skipped"
        )
    if fingerprint.rune_digests(ROOT) != runes:
        print("deep replay: green, but the runes changed while it ran — green not recorded", flush=True)
        return 0
    covered = dict(runes) if families is None else {name: runes[name] for name in families if name in runes}
    if families is not None and record is not None and isinstance(record.get("files"), dict):
        carried = {
            name: digest for name, digest in record["files"].items() if name in runes and name not in covered
        }
        covered = {**carried, **covered}
    record_deep_replay_green(covered, args.horizon, structure)
    print(
        f"deep replay: green at horizon {args.horizon} — recorded in {cycle_paths.DEEP_REPLAY_GREEN.name}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
