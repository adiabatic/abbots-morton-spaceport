"""The cheap form of the deep sweep: the crate's `replay-strings` walk at one letter past the belt's horizon, over the texts that name the runes whose content changed since the last recorded walk. Every per-edit gate walks at horizon 4. At that length a text reaches a letter's third lookahead slot only behind a boundary on the left, and its fourth slot never, so a fault confined to the deep slots behind a letter on the left is invisible to all of them (§10's fault table in `doc/rebuild-design.md`, row 4). `make conform-deep` can see such a fault in hours of HarfBuzz sweeping. This tool sees it in minutes, because it settles only what an edit could have changed and shapes nothing.

It is not part of the per-edit path because of its cost. On the live alphabet, a walk over the texts that name one family costs each configuration several times what the build's own whole-universe horizon-4 replay costs (`make cycle-timings ARGS='--by-step'` reports every run under `replay-deep`, the check name this tool records itself under). Running it inside `run_m1` would multiply every rune-edit build's replay time. The cycle instead reports whether it is due beside the deep sweep (`artifact_cycle.deep_replay_status`), and `make replay-deep` runs it. The window ceiling (`DEEP_REPLAY_MEMO_WINDOWS`) keeps a walk's memory flat as the corpus grows, apart from the settled records and labels, which are never released and grow with the number of distinct records. At the measured ceiling every configuration walks at once on either fleet machine, which `rebuild/test_deep_replay.py` checks.

The green record (`cycle_paths.DEEP_REPLAY_GREEN`) stores every rune's prose-blind digest and the horizon walked. The next walk covers the runes whose digest changed since, closed under `spec_load.rune_closure` (every rune whose records read a changed rune's content), which the window-locality theorem in `doc/rebuild-design.md` permits. The record also stores the replay structure stamp, but the stamp never widens the walk: a code or structure change is for the whole-universe deep sweep, and the cycle's deep-sweep line reports it. A walk that finds a disagreement withdraws what the record claims for the runes it walked (`withdraw_walked`). After a family walk the status reports the walked runes armed and the next bare walk covers them again; after a whole-universe walk the record is gone and the status reports `never-run`.

Run as: uv run python -m rebuild.tools.deep_replay, or through `make replay-deep`. `--families` names the runes to walk instead of reading them from the record. `--all` walks the whole universe, which on the live alphabet is an overnight run.
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

# The most windows one configuration's walk holds memoized (the crate's `--memo-windows`; `AMS_DEEP_REPLAY_MEMO_WINDOWS` overrides it). Before any text that could take the walk memo past it, the walk releases that memo and its engine's memos and continues. It is counted in windows, not bytes, so a release happens at the same point on every machine and the printed window count depends only on the rune set. The value is 7/8 of 2^23, the most entries a hash table of 2^23 buckets holds before it doubles. The memo never passes the ceiling, so it fills that table and never doubles into a larger one that a release's `clear()` would keep; the `--cache-census` rows of the walks cited under DEEP_REPLAY_PEAK_BYTES show the walk memo at that capacity at every release and at the end of every walk, never above it. Three capacity steps were measured (1,835,008, 3,670,016 and 7,340,032 windows). This is the largest of the three whose measured peak, as DEEP_REPLAY_PEAK_BYTES derives it, stays under 5.2 GB, a margin below the 5.27 GB per walk at which the 32 GiB machine drops from five walks to four; all three keep both fleet machines at the configuration count. Its cost, over `default` at horizon 5 with `--families=qsAh` on the alphabet with ·Ye, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`): 15,469,605 window settles against the uncapped walk's 14,748,571 distinct windows (4.9% more), in 45.4 s against the uncapped 42.8 s. The two smaller steps settle 10.5% and 18.1% more, in 46.0 s and 48.6 s, so a larger ceiling costs fewer settles and less wall-clock time.
DEEP_REPLAY_MEMO_WINDOWS = 7_340_032

# What one configuration's walk holds at its peak under DEEP_REPLAY_MEMO_WINDOWS. It holds the walk memo, at most the ceiling. It holds the engine's trace, candidate, prospect, closure, delta and reads memos, with no explain ladder (`Replay::new` turns it off); these are released with the walk memo and hold what the walk's misses settled since the last release, so the ceiling bounds them only through the engine entries each walk window costs. The trace memo also holds the follower windows a simulated prospect settles, so it grows past the walk memo: at the first release of the solo walk below it holds 14,191,326 entries beside the walk memo's 7,340,029, filling 97% of its own table's capacity step, so a walk whose windows each cost more trace entries doubles that table before the walk memo reaches the ceiling. It also holds what the walk never releases: the settled records, their labels and the input labels (464 records and 549 labels at the end of that walk), which grow with distinct records, not with windows settled.
# Measured with ·Ye in the alphabet, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`): one `replay-strings` over `default` alone at horizon 5, `--families=qsAh`, `--threads=1`, `--memo-windows` at the ceiling and `--cache-census`, under `/usr/bin/time -l`, peaks at 2.85 GB of footprint and 2.90 GB maxrss, walking 8,127,145 texts in 15,469,605 window settles over two releases in 45.4 s. The uncapped walk reads 5.60 GB in 42.8 s, and 20.52 GB with the explain ladder on. The same walk over `--families=qsAh,qsIt` reads 2.88 GB of footprint over 15,381,526 texts and four releases, level with one family's, so the peak stays flat as the corpus grows. Over every settlement configuration at once at `--threads=5` it reads 14.20 GB of whole-process footprint and 14.46 GB maxrss (2.84 GB a configuration), with 3.8 s of system time against 278 s of user time, no change in swap, and each `[t] replay[<config>]` line at 56.9 to 57.6 s against the solo 45.4 s: the five walks share memory without paging.
# The constant is the higher of the solo footprint and maxrss, plus a quarter, rounded up to the tenth. The margin covers growth in the holders the walk never releases and run-to-run spread, and it errs high because a per-unit cost that is too low puts the machine into swap. At this figure both fleet machines walk every settlement configuration at once, which `rebuild/test_deep_replay.py` checks. Re-measure it whenever the ceiling changes, the alphabet grows, the trace memo's key or entry or the walk memo's key changes shape, or the engine's memos change what they hold or how many entries each walk window costs them; the last three change a walk's peak at a fixed ceiling. Repeat both runs above: the solo walk, taking the higher of its footprint and maxrss, and the walk over every settlement configuration at once. In the second, a `[t] replay[<config>]` line that gets longer than the solo walk's while the system time rises means the width is causing paging. The logs are under `var/keep/issue-274/m5pro-48gib/`.
DEEP_REPLAY_PEAK_BYTES = 3_700_000_000

# The check name each walk records itself under in the cycle-timings journal (`rebuild.tools.cycle_timings.record_check`), where `make cycle-timings ARGS='--by-step'` reports it. No cycle runs this tool, so it records its own line.
CHECK = "replay-deep"


def replay_threads(total_bytes: int | None = None) -> int:
    """Return how many configurations walk at once: `AMS_DEEP_REPLAY_THREADS` when it is set, else `memory_budget.how_many_fit` over `DEEP_REPLAY_PEAK_BYTES`, capped at the settlement configuration count. A stated width is floored at one and not capped, as in `kernel_exec.kernel_threads_default`, and a value that is not a bare count raises. `DEEP_REPLAY_PEAK_BYTES` is measured at `DEEP_REPLAY_MEMO_WINDOWS`, so a higher ceiling set through `AMS_DEEP_REPLAY_MEMO_WINDOWS` has no memory estimate here; set `AMS_DEEP_REPLAY_THREADS` with it."""
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
    """Return the most windows each configuration's walk holds memoized before it releases its memos: `AMS_DEEP_REPLAY_MEMO_WINDOWS` when it is set, else `DEEP_REPLAY_MEMO_WINDOWS`. A set value must be a bare decimal count of at least one; anything else raises. The ceiling is in windows because a byte limit would trigger at a different point on each machine, and the window count the walk prints (which counts settles once a release happens) would then depend on the machine instead of the runes."""
    stated = os.environ.get("AMS_DEEP_REPLAY_MEMO_WINDOWS")
    if stated is None:
        return DEEP_REPLAY_MEMO_WINDOWS
    if re.fullmatch(r"[0-9]+", stated) is None or int(stated) < 1:
        raise RuntimeError(
            f"AMS_DEEP_REPLAY_MEMO_WINDOWS={stated!r} is not a memo ceiling: it takes a bare decimal count of windows, at least one"
        )
    return int(stated)


def families_to_walk(spec, record: dict | None, runes: dict[str, str]) -> list[str]:
    """Return the sorted runes whose texts this walk covers: every rune whose current digest differs from the record's, plus every rune whose records read one of those (`spec_load.rune_closure`). Empty when nothing changed."""
    moved = set(deep_replay_moved(record, runes))
    if not moved:
        return []
    closure = spec_load.rune_closure(spec)
    edited = {name for name, reads in closure.items() if reads & moved} | (moved & spec.runes.keys())
    return sorted(edited)


def withdraw_walked(record: dict | None, families: list[str] | None) -> None:
    """Withdraw what the green record claims for the texts a red walk covered: delete the record after a whole-universe walk (`families` None), and rewrite it without the walked runes' digests after a family walk. The record's horizon, structure stamp and claims for runes the walk did not cover stay."""
    if record is None:
        return
    horizon = record.get("horizon")
    if families is None or not isinstance(record.get("files"), dict) or not isinstance(horizon, int):
        cycle_paths.DEEP_REPLAY_GREEN.unlink(missing_ok=True)
        return
    kept = {name: digest for name, digest in record["files"].items() if name not in families}
    if kept != record["files"]:
        record_deep_replay_green(kept, horizon, record.get("structure"))


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
        withdraw_walked(record, families)
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
