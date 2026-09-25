"""The deep form of gate:conform: the same exhaustive font-versus-settlement sweep the belt runs, at horizon 5 by default (`--horizon` refuses anything below the belt's 4). The belt shapes every text up to four letters and checks what only shaping the compiled font can test: HarfBuzz's application semantics over the rule shapes the lookup contains. Whether the six-slot window is sufficient for the texts the tables were built for is checked by the crate's string replay, which `run_m1` runs on every build. This tool asks the shaper's question at a depth the belt cannot afford, over texts long enough to reach a letter's fourth lookahead slot, which no belt text reaches.

It runs on demand (`make conform-deep`), not per edit. The key of its green record (`artifact_cycle.deep_sweep_skip_lines`) is the set of behavior classes the build enumerated from the emitted lookup (`emit_gsub.behavior_classes`), the font-compilation code, and the uharfbuzz version. It leaves out the runes and M1.otf, so a rune edit that changes many rules but adds no new rule shape leaves the sweep current. When a build emits a new shape, or the compilation code or the shaper changes, the key changes and the cycle reports the sweep as `armed` once per pass. The belt's key is the same lines plus its horizon (`artifact_cycle.conform_skip_fingerprint`). No gate depends on this sweep: an armed deep sweep means it should be run, and the cycle does not fail.

The belt's split-buffer check (every text split at a boundary shapes the same as its segments shaped alone) runs at this depth too, and this is the only place it covers texts longer than the belt's horizon, since no build step shapes a length-5 text. The ZWNJ glyph's own properties (zero advance, no ink) need no depth: read-back checks them in the font bytes on every build.

A green run also refreshes gate:conform's green record, when the belt's key did not change during the run, because an exhaustive sweep at depth N covers every text the belt at depth 4 shapes. The next cycle can then skip the belt. At or past the deep replay's horizon it also refreshes the deep replay's record (`rebuild.tools.deep_replay`), because it settles every text it shapes against the tables in the font.

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

from rebuild.pipeline import conform, run_m1
from rebuild.tools import cycle_paths
from rebuild.tools.artifact_cycle import (
    CONFORM_HORIZON_DEFAULT,
    DEEP_REPLAY_HORIZON_DEFAULT,
    DEEP_SWEEP_HORIZON_DEFAULT,
    clear_contradicted_green,
    conform_skip_files,
    conform_skip_fingerprint,
    deep_sweep_skip_files,
    deep_sweep_skip_fingerprint,
    deep_sweep_status,
    record_deep_replay_green,
    record_deep_sweep_green,
    record_green,
    sweep_job_budget,
)

SUMMARY_NAME = "deep_sweep_summary.json"


def tables_stamped() -> bool:
    """Return whether the serialized enumeration under rebuild/out/m1 was produced from the sources on disk, by the same `tables_inputs()` stamp `run_font_conformance` checks. It checks the artifacts themselves, not a record of a past run, so tables from a `--gates-only` pass or from another machine qualify the same way as a local build."""
    return run_m1.serialized_tables(run_m1.OUT_DIR, run_m1.tables_inputs()) is not None


def arming_key() -> str:
    """Return this build's arming key, or exit when the sweep would be meaningless. Without a behavior-class sidecar there is no key to record a green under. With a stale tables stamp, the M1.otf on disk is not the font the runes on disk describe."""
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


def refresh_deep_replay(horizon: int) -> None:
    """Record the deep replay as green at `horizon` for every rune at its current digest, since a green sweep at that depth settled every text that names any of them."""
    from rebuild.pipeline import fingerprint
    from rebuild.pipeline.spec_load import load_default_spec

    record_deep_replay_green(
        fingerprint.rune_digests(ROOT), horizon, run_m1.replay_structure_stamp(load_default_spec())
    )


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
        help="worker budget, defaulting to the oracle's sweep_job_budget() for this box; the sweep runs one process per acceptance configuration and no more, since a configuration is its unit, so a wider number is narrowed to that count",
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
    jobs = max(1, min(args.jobs, len(conform.ACCEPTANCE_CONFIGS)))
    print(
        f"deep sweep: horizon {args.horizon} over every settlement configuration at {jobs} jobs, one process per acceptance configuration at most (the ss10 overlay's arm stays at its own horizon)",
        flush=True,
    )
    summary = run_m1.run_font_conformance(max_length=args.horizon, jobs=jobs, summary_name=SUMMARY_NAME)
    print(json.dumps(summary, indent=2))

    if not summary["pass"] or summary["divergences"]:
        clear_contradicted_green(cycle_paths.DEEP_SWEEP_GREEN, deep_key)
        print(
            f"deep sweep: {summary['divergences']} font-vs-settle divergence(s) at horizon {args.horizon}; see {SUMMARY_NAME}",
            file=sys.stderr,
        )
        return 1

    if deep_sweep_skip_fingerprint(ROOT) != deep_key:
        print("deep sweep: green, but its inputs changed while it ran — green not recorded", flush=True)
        return 0
    record_deep_sweep_green(deep_key, args.horizon, files=deep_sweep_skip_files(ROOT))
    print(
        f"deep sweep: green at horizon {args.horizon} — recorded in {cycle_paths.DEEP_SWEEP_GREEN.name}",
        flush=True,
    )
    if args.horizon >= DEEP_REPLAY_HORIZON_DEFAULT:
        refresh_deep_replay(args.horizon)
        print(
            f"deep replay: green too — every text at horizon {args.horizon} was settled here, so nothing is left for `make replay-deep` to walk",
            flush=True,
        )
    if (
        args.horizon >= CONFORM_HORIZON_DEFAULT
        and conform_skip_fingerprint(ROOT, CONFORM_HORIZON_DEFAULT) == belt_key
    ):
        record_green(
            cycle_paths.CONFORM_GREEN, belt_key, files=conform_skip_files(ROOT, CONFORM_HORIZON_DEFAULT)
        )
        print(
            f"gate:conform: green too — every belt text at horizon {CONFORM_HORIZON_DEFAULT} was swept here, so the next cycle skips it",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
