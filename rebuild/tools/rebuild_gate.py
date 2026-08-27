"""`make test-rebuild`'s entry point: run the rebuild pytest suite one lane at a time, and only the lanes whose input closure has changed since that lane's last green run.

The suite splits into two lanes, and rebuild/conftest.py is the authority on which test is which: a test whose fixture closure names `live_artifacts` reads live build output and belongs to **validators**, everything else to **contracts**. That split is what makes two gates worth having. Contracts holds most of the suite and reads no artifact, so it runs at the box's full xdist width and its closure — the rebuild/ and glyph_data/ sources (minus Markdown, the carried-verdict evidence, the JS-only jstests, and the census pins the suite no longer reads) plus conftest.py, pyproject.toml, uv.lock, and the site fonts it shapes against — contains no build output at all, which is what lets an artifact-only cycle skip it. Validators adds exactly what its readers touch on top: the out/m1 artifacts, the oracle's subset tables, and the baselines. Each lane keeps its own green record (rebuild/out/rebuild-contracts-green.json, rebuild/out/rebuild-validators-green.json), shared with the artifact cycle's gate:rebuild-contracts and gate:rebuild-validators, so interactive greens and cycle greens count for each other in both directions.

Contracts runs first, and a hard failure there returns immediately without starting validators — running the cheap lane first is what buys that fail-fast, since a code error surfaces in minutes instead of after the long lane has finished. Each lane that actually runs is judged through the cycle's own failure classifier, which parses the FAILED/ERROR summary lines so a failure is named rather than just counted; every green is recordable. A green run during which that lane's closure moved records nothing, because the tested content is no longer on disk; a red run whose closure still matches its record deletes it, since the green it claims is contradicted; and without git there is no closure to key on, so the lane runs unconditionally and records nothing. `make test-rebuild FORCE=1` (--force) runs both lanes regardless.

AMS_RUN_PYRIGHT rides the environment into whichever lane actually spawns first and is stripped from every lane after it: pyright checks the whole tree from `[tool.pyright] include` and its answer cannot change between two pytest invocations of the same working tree, so type-checking twice would only cost a second copy of the same verdict.

AMS_POOL_UNIT goes the other way — each lane names its own pool (POOL_UNIT_BY_LANE), which is what has that lane's xdist controller append a kind:"pool" line to the cycle-timings journal recording every worker's peak, the measurement `make job-costs` holds VALIDATORS_WORKER_BYTES against. The name is written into a per-lane copy of the environment and never into the shared one, because the shared dict outlives the lane: writing it there would leave lane two spawning under lane one's name and file the validators pool as contracts.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.tools.artifact_cycle import (
    REBUILD_LANES,
    classify_rebuild_output,
    clear_contradicted_green,
    read_green_record,
    rebuild_lane_argv,
    rebuild_lane_fingerprint,
    rebuild_lane_green,
    record_green,
)
from rebuild.tools.cycle_timings import POOL_UNIT_ENV

PYRIGHT_ENV = "AMS_RUN_PYRIGHT"
POOL_UNIT_BY_LANE = {"contracts": "rebuild-contracts", "validators": "rebuild-validators"}


def _run_suite(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, bufsize=1)
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line.rstrip("\r\n"))
    proc.stdout.close()
    return proc.wait(), "\n".join(lines)


def _run_lane(lane: str, env: dict[str, str], force: bool) -> tuple[int, bool]:
    """Run (or validly skip) one lane, returning its exit code and whether it actually spawned a suite. A nonzero code is a hard failure and stops the run; every other outcome — a skip, a clean green, a green whose closure drifted — is a zero the caller carries on from."""
    record_path = rebuild_lane_green(lane)
    before = rebuild_lane_fingerprint(ROOT, lane)
    recorded = read_green_record(record_path)
    if not force and before is not None and recorded is not None and before == recorded["fingerprint"]:
        print(
            f"make test-rebuild: {lane} lane SKIPPED — its input closure is unchanged since its last green run ({recorded.get('finished_at')}). "
            "Run `make test-rebuild FORCE=1` to run it anyway."
        )
        return 0, False

    lane_env = {**env, POOL_UNIT_ENV: POOL_UNIT_BY_LANE[lane]}
    returncode, stdout = _run_suite(rebuild_lane_argv(lane), lane_env)
    outcome = classify_rebuild_output(stdout, returncode)
    for test_id in outcome.hard_ids:
        print(f"  hard rebuild failure ({lane}): {test_id}")
    if outcome.hard_ids:
        clear_contradicted_green(record_path, before)
        print(f"make test-rebuild: {lane} lane {outcome.status}")
        return (returncode if returncode != 0 else 1), True
    if before is None:
        print(
            f"make test-rebuild: {lane} lane {outcome.status} (closure fingerprint unavailable without git — not recorded)"
        )
        return 0, True
    if rebuild_lane_fingerprint(ROOT, lane) != before:
        print(
            f"make test-rebuild: {lane} lane {outcome.status}, but its input closure changed while the suite ran — green not recorded"
        )
        return 0, True
    record_green(record_path, before)
    where = record_path.relative_to(ROOT) if record_path.is_relative_to(ROOT) else record_path
    print(f"make test-rebuild: {lane} lane {outcome.status} — closure fingerprint recorded in {where}")
    return 0, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run each lane of the rebuild pytest suite unless that lane's input closure is unchanged since its last green run, judging each result through the artifact cycle's failure classifier."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run both lanes even when their closure fingerprints match the recorded greens",
    )
    args = parser.parse_args(argv)

    env = dict(os.environ)
    for index, lane in enumerate(REBUILD_LANES):
        returncode, ran = _run_lane(lane, env, args.force)
        if returncode != 0:
            for later in REBUILD_LANES[index + 1 :]:
                print(f"make test-rebuild: {later} lane not run ({lane} lane failed)")
            return returncode
        if ran:
            env.pop(PYRIGHT_ENV, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
