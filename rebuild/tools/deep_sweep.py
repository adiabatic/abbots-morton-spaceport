"""The periodic deep form of gate:conform (issue #74): the same exhaustive font-vs-settle sweep the belt runs, taken to horizon 5 or deeper. The belt shapes every string up to four tokens and is chartered for what only shaping the real binary can test — HarfBuzz's application semantics over the rule shapes the lookup emits; whether the six-slot window abstraction is itself sufficient over the strings the tables were built for is the crate's string replay, which run_m1 runs on every build. This tool is where the shaper's question gets asked at a depth the belt cannot afford, over strings long enough to reach past the window's own reach.

What makes it periodic rather than per-edit is what its green is keyed on, and the belt keys on the same posture (`artifact_cycle.conform_skip_fingerprint`). The arming key (`artifact_cycle.deep_sweep_skip_lines`) is the behavior-class set the build enumerated out of the emitted lookup (`emit_gsub.behavior_classes`), the font-compilation code that turns a plan into bytes, and the uharfbuzz version that shapes them — not the runes and not M1.otf. So a rune edit that moves thousands of rules but mints no new rule shape leaves this green standing, and the cycle keeps saying `current`; the moment a build emits a shape no earlier build did, or the compilation path or the shaper moves, the same key goes `armed` and the cycle says so once per pass. Nothing gates on it: an armed deep sweep is a note to run this overnight, not a red cycle.

The structural check the belt runs — every splitter-separated buffer identical to its segments shaped alone — comes along at this depth, which is the only place its coverage past the belt's horizon lives: nothing on a build's path shapes a length-5 string. The ZWNJ slot's own structure — zero advance, no ink — needs no depth at all: read-back proves it statically off the font bytes once per build.

A green run also refreshes gate:conform's own record when it swept at least the belt's horizon, because an exhaustive sweep at any depth N covers every string the belt at depth 4 would have shaped. So an overnight deep run leaves the next cycle's belt already proved rather than making the reviewer wait for it twice.

Run as: uv run python -m rebuild.tools.deep_sweep, or through `make conform-deep`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.pipeline import run_m1
from rebuild.tools.artifact_cycle import (
    CONFORM_GREEN,
    CONFORM_HORIZON_DEFAULT,
    DEEP_SWEEP_GREEN,
    DEEP_SWEEP_HORIZON_DEFAULT,
    clear_contradicted_green,
    conform_skip_files,
    conform_skip_fingerprint,
    deep_sweep_skip_files,
    deep_sweep_skip_fingerprint,
    deep_sweep_status,
    record_deep_sweep_green,
    record_green,
    sweep_job_budget,
)

SUMMARY_NAME = "deep_sweep_summary.json"


def tables_stamped() -> bool:
    """Whether the serialized enumeration under rebuild/out/m1 was produced from exactly the sources on disk — the same tables_inputs() stamp run_font_conformance itself refuses on. Artifact identity, never a receipt of a past run: a --gates-only re-adjudication and a build handed over from another box both arm the sweep exactly as a locally-green build does, whatever green either of them did or did not leave behind."""
    return run_m1.serialized_tables(run_m1.OUT_DIR, run_m1.tables_inputs()) is not None


def arming_key() -> str:
    """This build's arming key, after the two preconditions for the sweep to mean anything. Without a behavior-class sidecar there is no key at all, so a green could not be recorded against anything; and with the tables' stamp stale the M1.otf on disk is not the font the runes on disk describe, so a deep sweep of it would prove something about a build nobody is going to ship."""
    fingerprint = deep_sweep_skip_fingerprint(ROOT)
    if fingerprint is None:
        raise SystemExit(
            "no behavior-class sidecar under rebuild/out/m1 — run `make artifact-cycle` first so a build can leave one to arm this sweep"
        )
    if not tables_stamped():
        raise SystemExit(
            "the M1 artifacts are stale relative to the runes on disk — run `make artifact-cycle` (or `make review-cycle`) first, so the deep sweep never shapes a font the sources have outgrown"
        )
    return fingerprint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deep form of the font-vs-settle conformance sweep and record its green."
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=DEEP_SWEEP_HORIZON_DEFAULT,
        help=f"exhaustive sweep length (default {DEEP_SWEEP_HORIZON_DEFAULT}); anything below the belt's own {CONFORM_HORIZON_DEFAULT} is refused, since the belt already sweeps that on every edit",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=sweep_job_budget(),
        help="how many acceptance configurations sweep at once, in the same lane the cycle's conform gate uses",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print whether the deep sweep is current or armed and exit, sweeping nothing (exit 0 when current)",
    )
    args = parser.parse_args(argv)

    if args.status:
        status, note = deep_sweep_status(ROOT, args.horizon)
        print(f"deep sweep: {status} — {note}")
        return 0 if status == "current" else 1

    if args.horizon < CONFORM_HORIZON_DEFAULT:
        raise SystemExit(
            f"--horizon {args.horizon} is shallower than the per-edit belt's {CONFORM_HORIZON_DEFAULT}; the belt already sweeps that depth on every edit"
        )
    deep_key = arming_key()
    belt_key = conform_skip_fingerprint(ROOT, CONFORM_HORIZON_DEFAULT)
    jobs = max(1, args.jobs)
    print(
        f"deep sweep: horizon {args.horizon} over every settlement configuration at {jobs} jobs (the ss10 overlay's arm stays at its own horizon)",
        flush=True,
    )
    summary = run_m1.run_font_conformance(max_length=args.horizon, jobs=jobs, summary_name=SUMMARY_NAME)
    print(json.dumps(summary, indent=2))

    if not summary["pass"] or summary["divergences"]:
        clear_contradicted_green(DEEP_SWEEP_GREEN, deep_key)
        print(
            f"deep sweep: {summary['divergences']} font-vs-settle divergence(s) at horizon {args.horizon}; see {SUMMARY_NAME}",
            file=sys.stderr,
        )
        return 1

    if deep_sweep_skip_fingerprint(ROOT) != deep_key:
        print("deep sweep: green, but its inputs changed while it ran — green not recorded", flush=True)
        return 0
    record_deep_sweep_green(deep_key, args.horizon, files=deep_sweep_skip_files(ROOT))
    print(f"deep sweep: green at horizon {args.horizon} — recorded in {DEEP_SWEEP_GREEN.name}", flush=True)
    if (
        args.horizon >= CONFORM_HORIZON_DEFAULT
        and conform_skip_fingerprint(ROOT, CONFORM_HORIZON_DEFAULT) == belt_key
    ):
        record_green(CONFORM_GREEN, belt_key, files=conform_skip_files(ROOT, CONFORM_HORIZON_DEFAULT))
        print(
            f"gate:conform: green too — every belt text at horizon {CONFORM_HORIZON_DEFAULT} was swept here, so the next cycle skips it",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
