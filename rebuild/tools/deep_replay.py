"""The deep sweep's cheap form (issue #189): the crate's `replay-strings` walk one letter past the belt's horizon, over the texts naming the runes whose content moved since the last walk, keyed on rune content the way the settle memo and the oracle row cache are. Every per-edit gate walks at horizon 4, where a text exercises a letter third slot only behind a boundary left and a letter fourth slot never, so a fault confined to the deep slots behind a letter left — §10's fault table, row 4 — is invisible to all of them together; `make conform-deep` sees it, at the hours a whole-universe HarfBuzz sweep costs, and this tool sees it at the minutes a per-family replay costs, because it settles nothing a shaper would add and walks only what an edit could have moved.

It stays out of the per-edit path on purpose. Priced on the live alphabet (`make cycle-timings ARGS='--by-step'` carries every run of this tool under `replay-deep`, the check it files itself as), a walk over the texts naming one family costs each configuration many times what the build's own horizon-4 replay costs and holds a memo over ten gigabytes wide, so the configurations walk one or two at a time on this box and riding `run_m1` would lengthen every rune-edit build by most of itself; the cycle arms this walk instead and reports it beside the deep sweep (`artifact_cycle.deep_replay_status`), and `make replay-deep` is the remedy. A walk is a claim about the runes as they stood, so the green record (`artifact_cycle.DEEP_REPLAY_GREEN`) carries every rune's prose-blind digest and the horizon it walked at, and the next walk covers the runes whose digest moved since, closed under `spec_load.rune_closure` — every rune whose records read a moved rune's content — which is the O(delta) form the window-locality theorem licenses. The structure stamp rides along for the record and never widens the walk: a code or structure change is the whole-universe deep sweep's question, not this tool's, and the cycle's deep-sweep line is where that shows.

Run as: uv run python -m rebuild.tools.deep_replay, or through `make replay-deep`. `--families` names the runes to walk instead of asking the record; `--all` walks the whole universe, which on the live alphabet is an overnight run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.pipeline import conform, fingerprint, kernel_exec, run_m1, spec_load
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import memory_budget
from rebuild.tools.artifact_cycle import (
    CONFORM_HORIZON_DEFAULT,
    DEEP_REPLAY_GREEN,
    DEEP_REPLAY_HORIZON_DEFAULT,
    deep_replay_moved,
    deep_replay_status,
    read_green_record,
    record_deep_replay_green,
)
from rebuild.tools.cycle_timings import CheckVerdict, record_check
from rebuild.tools.deep_sweep import tables_stamped

# What one configuration's walk holds at its peak: the engine's trace memo over every distinct window the texts naming one family reach at horizon 5, which on the live alphabet with ·Zoo is the better part of ten million windows. Measured with one `replay-strings` over `default` alone at horizon 5, `--families=qsAh`, `--threads=1`, under `/usr/bin/time -l`: 13.5 GB resident and a minute and a half of wall; the same walk five configurations wide paged the box (19 GB resident, the system time several times the user time) and ran each configuration seven times slower, which is why the width divides the box by this figure rather than assuming the configurations fit together. Re-measure it the same way whenever the alphabet grows or the trace memo's entry changes shape.
DEEP_REPLAY_PEAK_BYTES = 15_000_000_000

# The step name every walk files itself under in the cycle-timings journal (`rebuild.tools.cycle_timings.record_check`), which is where `make cycle-timings ARGS='--by-step'` prices it: no cycle spawns this tool, so it records its own line the way the other interactive entry points do.
CHECK = "replay-deep"


def replay_threads(total_bytes: int | None = None) -> int:
    """How many configurations walk at once: `AMS_DEEP_REPLAY_THREADS` wherever it is set, else `DEEP_REPLAY_PEAK_BYTES` divided into the box by `memory_budget.how_many_fit`, capped at the settlement configurations. A stated width is floored at one and clamped no further, as `kernel_exec.kernel_threads_default` treats its own knob; a value that is not a bare count raises rather than falling through to the arithmetic."""
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
    record = read_green_record(DEEP_REPLAY_GREEN)
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
    walked = "the whole universe" if families is None else f"{len(families)} families ({', '.join(families)})"
    print(
        f"deep replay: horizon {args.horizon} over {walked}, {threads} configurations at a time", flush=True
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
            timings=True,
        )
    except kernel_exec.ReplayDisagreement as error:
        message = f"the tables disagree with the engine at horizon {args.horizon}: {error}"
        print(f"deep replay: {message}", file=sys.stderr)
        record_check(
            CheckVerdict(check=CHECK, verdict="red", status="FAILED", failures=[message], failed_ids=[]),
            argv=list(argv) if argv is not None else sys.argv[1:],
            elapsed_s=time.perf_counter() - started,
        )
        return 1
    elapsed = time.perf_counter() - started
    print(f"[t] {CHECK} {elapsed:.1f}s", flush=True)
    record_check(
        CheckVerdict(check=CHECK, verdict="green", status="green", failures=[], failed_ids=[]),
        argv=list(argv) if argv is not None else sys.argv[1:],
        elapsed_s=elapsed,
    )
    for config in conform.SETTLEMENT_CONFIGS:
        counts = answered[config]
        print(
            f"deep replay[{config}]: {counts['texts']} texts, {counts['windows']} windows, {counts['skipped']} skipped"
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
    print(f"deep replay: green at horizon {args.horizon} — recorded in {DEEP_REPLAY_GREEN.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
