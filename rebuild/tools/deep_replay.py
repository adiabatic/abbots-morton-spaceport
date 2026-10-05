"""The cheap form of the deep sweep: the crate's `replay-strings` walk at one letter past the per-edit sweep's maximum length, over the texts that name the runes whose content changed since the last recorded walk. Every per-edit gate walks at maximum length 4. At that length a text reaches a letter's third lookahead slot only behind a boundary on the left, and its fourth slot never, so a fault confined to the deep slots behind a letter on the left is invisible to all of them (§10's fault table in `doc/rebuild-design.md`, row 4). `make conform-deep` can see such a fault in hours of HarfBuzz sweeping. This tool sees it in minutes, because it settles only what an edit could have changed and shapes nothing.

It is not part of the per-edit path because of its cost. On the live alphabet, a walk over the texts that name one family costs each configuration several times what the build's own full replay at maximum length 4 costs (`make cycle-timings ARGS='--by-step'` reports every run under `replay-deep`, the check name this tool records itself under). Running it inside `run_m1` would multiply every rune-edit build's replay time. The cycle instead reports whether it is due beside the deep sweep (`artifact_cycle.deep_replay_status`), and `make replay-deep` runs it. The window ceiling (`DEEP_REPLAY_MEMO_WINDOWS`) keeps a walk's memory flat as the corpus grows, apart from the settled records and labels, which are never released and grow with the number of distinct records. At the measured ceiling every configuration walks at once on either fleet machine, which `rebuild/test_deep_replay.py` checks.

The green record (`cycle_paths.DEEP_REPLAY_GREEN`) stores every rune's prose-insensitive digest and one maximum length, the least depth any of its runes was walked to. A family walk deeper than the record therefore leaves it at the depth of the runes the walk carried over, and a shallower walk, or a deep sweep's refresh, over runes whose digests and structure stamp match the record keeps the deeper length; `artifact_cycle.record_deep_replay_green` states the rule, and the green line names the length the record holds when it differs from the walk's. The next walk covers the runes whose digest changed since, closed under `spec_load.rune_closure` (every rune whose records read a changed rune's content), which the window locality rule in `doc/rebuild-design.md` permits. That rule has one exception a narrowed walk must honor, the imported windows (§10): each configuration's table takes in the windows other configurations keep live, so a rune edit can reshape a configuration's rules for texts that name no edited rune. The record therefore stores the tables' imported windows (`artifact_cycle.tables_imports_digest`), and when they differ from the tables on disk, a walk without `--families` walks every text, the status reports it due, and a family walk carries no other rune's claim. A walk without `--families` or `--all` deeper than the record's maximum length (or over a record without one) walks every text instead, whether or not a rune moved, because only a walk over every text raises the record's depth; it says so before it starts. The record also stores the replay structure stamp, but the stamp never widens the walk: a code or structure change is for the deep sweep over every text, and the cycle's deep-sweep line reports it. The stamp only decides whether a walk keeps the record's deeper length for the runes it covers. A walk that finds a disagreement withdraws what the record claims for the runes it walked (`withdraw_walked`). After a family walk the status reports the walked runes due and the next bare walk covers them again; after a walk over every text, whether `--all` or a bare walk past the record's depth, the record is gone and the status reports `never-run`.

Run as: uv run python -m rebuild.tools.deep_replay, or through `make replay-deep`. `--families` names the runes to walk instead of reading them from the record, at any depth. `--all` walks every text at any depth, which on the live alphabet takes minutes on the 18-core M5 Pro (`doc/fleet.md`).
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
    CONFORM_MAX_LENGTH_DEFAULT,
    DEEP_REPLAY_MAX_LENGTH_DEFAULT,
    deep_replay_moved,
    deep_replay_status,
    read_green_record,
    record_deep_replay_green,
    recorded_max_length,
    tables_imports_digest,
)
from rebuild.tools.cycle_timings import CheckResult, record_check
from rebuild.tools.deep_sweep import tables_stamped

# The most windows one configuration's walk holds memoized (the crate's `--memo-windows`; `AMS_DEEP_REPLAY_MEMO_WINDOWS` overrides it). Before any text that could take the walk memo past it, the walk releases that memo and its engine's memos and continues. It is counted in windows, not bytes, so a release happens at the same point on every machine and the printed window count depends only on the rune set. The value is 7/8 of 2^23, the most entries a hash table of 2^23 buckets holds before it doubles. The memo never passes the ceiling, so it fills that table and never doubles into a larger one that a release's `clear()` would keep; the `--cache-stats` rows of the walks cited under DEEP_REPLAY_PEAK_BYTES show the walk memo at that capacity at every release and at the end of every walk, never above it. Three capacity steps were measured (1,835,008, 3,670,016 and 7,340,032 windows). This is the largest of the three, and its measured peak, as DEEP_REPLAY_PEAK_BYTES derives it, stays below the 8.71 GB per walk at which the 48 GiB machines drop from five walks to four, as the other two do. Its cost, over `default` at maximum length 5 with `--families=qsAh` on the alphabet with ·Ye, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`): 15,469,605 window settles against the uncapped walk's 14,748,571 distinct windows (4.9% more), in 45.4 s against the uncapped 42.8 s. The two smaller steps settle 10.5% and 18.1% more, in 46.0 s and 48.6 s, so a larger ceiling costs fewer settles and less wall-clock time.
DEEP_REPLAY_MEMO_WINDOWS = 7_340_032

# What one configuration's walk holds at its peak under DEEP_REPLAY_MEMO_WINDOWS. It holds the walk memo, at most the ceiling. It holds the engine's trace, candidate, prospect, closure, delta and reads memos, with no ranking (`Replay::new` turns it off); these are released with the walk memo and hold what the walk's misses settled since the last release, so the ceiling bounds them only through the engine entries each walk window costs. The trace memo also holds the follower windows a simulated prospect settles, so it grows past the walk memo: at the first release of the solo walk below it holds 13,932,782 entries beside the walk memo's 7,340,028, 95% of what its table holds before it doubles, and at the second 17,625,355 in a table doubled to hold 29,360,128, which the release keeps, so a walk whose windows each cost more trace entries doubles that table before the walk memo reaches the ceiling. It also holds what the walk never releases: the settled records, their labels and the input labels (471 records and 558 labels at the end of that walk), which grow with distinct records, not with windows settled.
# Measured at 44 runes on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`): one `replay-strings` over `default` alone at maximum length 5, `--families=qsAh`, `--threads=1`, `--memo-windows` at the ceiling and `--cache-stats`, under `/usr/bin/time -l`, peaks at 2.84 GB of footprint and 2.90 GB maxrss, walking 9,076,397 texts in 17,427,303 window settles over two releases in 49.6 s. The walk over every text, `make replay-deep ARGS='--all'` under `/usr/bin/time -l`, walks every settlement configuration at once at its width of five and reads 14.35 GB maxrss for the crate process (2.87 GB a configuration), with 6.5 s of system time against 2,671 s of user time and no swaps, each configuration walking 71,270,177 texts in 528.7 to 542.3 s: the five walks share memory without paging. With ·Ye the same solo walk read 2.85 GB of footprint and 2.90 GB maxrss over 8,127,145 texts, and five qsAh walks at once 2.84 GB a configuration, so the peak stays level as the corpus grows. The uncapped walk read 5.60 GB with ·Ye, and 20.52 GB with the ranking on.
# The constant is the higher of the solo footprint and maxrss, plus a quarter, rounded up to the tenth. The margin covers growth in the holders the walk never releases and run-to-run spread, and it errs high because a per-unit cost that is too low puts the machine into swap. At this figure both fleet machines walk every settlement configuration at once, which `rebuild/test_deep_replay.py` checks. Re-measure it whenever the ceiling changes, the alphabet grows, the trace memo's key or entry or the walk memo's key changes shape, or the engine's memos change what they hold or how many entries each walk window costs them; the last three change a walk's peak at a fixed ceiling. Repeat both runs above: the solo walk, taking the higher of its footprint and maxrss, and the walk over every settlement configuration at once, which `make replay-deep ARGS='--all'` is. In the second, a system time that rises against the user time, or swaps, means the width is causing paging. The logs are under `var/keep/issue-495/replay/`, and those taken with ·Ye under `var/keep/issue-274/m5pro-48gib/`.
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
    """Withdraw what the green record claims for the texts a red walk covered: delete the record after a full replay (`families` None), and rewrite it without the walked runes' digests after a family walk. The record's maximum length, structure stamp, imported windows and claims for runes the walk did not cover stay."""
    if record is None:
        return
    max_length = recorded_max_length(record)
    if families is None or not isinstance(record.get("files"), dict) or not isinstance(max_length, int):
        cycle_paths.DEEP_REPLAY_GREEN.unlink(missing_ok=True)
        return
    kept = {name: digest for name, digest in record["files"].items() if name not in families}
    if kept != record["files"]:
        record_deep_replay_green(
            {}, max_length, record.get("structure"), carry=kept, imports=record.get("imports")
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walk the texts naming the runes that moved to one letter past the per-edit sweep's maximum length, holding the tables to the engine, and record the walk."
    )
    parser.add_argument(
        "--max-length",
        "--horizon",
        type=int,
        default=DEEP_REPLAY_MAX_LENGTH_DEFAULT,
        help=f"walk length (default {DEEP_REPLAY_MAX_LENGTH_DEFAULT}); anything at or below the build's own {CONFORM_MAX_LENGTH_DEFAULT} is refused, since every build already walks that depth. Without --families or --all, a length past the record's maximum length walks every text, because only that raises the record's depth",
    )
    parser.add_argument(
        "--families",
        help="comma-separated rune names to walk instead of the ones the record says moved",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="walk every text rather than the moved runes' texts, at any --max-length (a walk without it does so only past the record's maximum length); a run of minutes on the live alphabet",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="how many settlement configurations walk at once (default: what this machine's memory fits, AMS_DEEP_REPLAY_THREADS to state one)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print whether the deep replay is current or due and exit, walking nothing (exit 0 when current)",
    )
    args = parser.parse_args(argv)

    if args.status:
        status, note = deep_replay_status(ROOT, args.max_length)
        print(f"deep replay: {status} — {note}")
        return 0 if status == "current" else 1

    if args.max_length <= CONFORM_MAX_LENGTH_DEFAULT:
        raise SystemExit(
            f"--max-length {args.max_length} is no deeper than the build's own replay at {CONFORM_MAX_LENGTH_DEFAULT}; every build already walks that depth"
        )
    if not tables_stamped():
        raise SystemExit(
            "the M1 tables are stale relative to the runes on disk — run `make artifact-cycle` (or a bare M1 build) first, so the walk never holds tables the sources have outgrown"
        )
    spec = load_default_spec()
    runes = fingerprint.rune_digests(ROOT)
    structure = run_m1.replay_structure_stamp(spec)
    imports = tables_imports_digest(ROOT)
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
        recorded = recorded_max_length(record)
        if not isinstance(recorded, int) or recorded < args.max_length:
            held = f"maximum length {recorded}" if isinstance(recorded, int) else "no maximum length"
            print(
                f"deep replay: the record holds {held}, so raising it to {args.max_length} walks every text",
                flush=True,
            )
            families = None
        elif record.get("imports") != imports:
            print(
                "deep replay: the windows the tables import from one another moved since the last walk, so this walk covers every text",
                flush=True,
            )
            families = None
        else:
            families = families_to_walk(spec, record, runes)
            if not families:
                print(f"deep replay: nothing moved since the last walk at maximum length {recorded}")
                return 0
    threads = max(1, args.threads) if args.threads is not None else replay_threads()
    memo_windows = replay_memo_windows()
    walked = "every text" if families is None else f"{len(families)} families ({', '.join(families)})"
    print(
        f"deep replay: maximum length {args.max_length} over {walked}, {threads} configurations at a time, at most {memo_windows} windows memoized per walk",
        flush=True,
    )
    started = time.perf_counter()
    try:
        answered = kernel_exec.replay_strings(
            spec,
            run_m1.OUT_DIR,
            conform.SETTLEMENT_CONFIGS,
            max_length=args.max_length,
            families=families,
            threads=threads,
            memo_windows=memo_windows,
            timings=True,
        )
    except kernel_exec.ReplayDisagreement as error:
        message = f"the tables disagree with the engine at maximum length {args.max_length}: {error}"
        print(f"deep replay: {message}", file=sys.stderr)
        record_check(
            CheckResult(check=CHECK, outcome="red", status="FAILED", failures=[message], failed_ids=[]),
            argv=list(argv) if argv is not None else sys.argv[1:],
            elapsed_s=time.perf_counter() - started,
            peak_rss_bytes=peak_rss.peak_rss_children_bytes(),
        )
        withdraw_walked(record, families)
        return 1
    elapsed = time.perf_counter() - started
    print(f"[t] {CHECK} {elapsed:.1f}s", flush=True)
    record_check(
        CheckResult(check=CHECK, outcome="green", status="green", failures=[], failed_ids=[]),
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
    recorded = record_deep_replay_green(covered, args.max_length, structure, carry=runes, imports=imports)
    store = cycle_paths.DEEP_REPLAY_GREEN.name
    if recorded > args.max_length:
        note = f"{store} keeps maximum length {recorded} from an earlier walk over the same runes"
    elif recorded < args.max_length:
        note = f"{store} stays at maximum length {recorded}, the depth the runes this walk did not cover were walked to"
    else:
        note = f"recorded in {store}"
    print(f"deep replay: green at maximum length {args.max_length} — {note}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
