"""`make test-rebuild`'s entry point: run the rebuild pytest suite, but only when its input closure has changed since its last green run.

The suite has one lane, contracts, which rebuild/conftest.py defines. No test under rebuild/ reads live build output (the audit guard in that conftest fails one that does), so the suite runs on every usable core, and its closure contains no build output, which lets an artifact-only cycle skip it. The closure is the rebuild/ and glyph_data/ sources (without Markdown and the paths in `cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES`), conftest.py, pyproject.toml, uv.lock, the site fonts the suite shapes against, and `artifact_cycle.REBUILD_GATE_HARNESS_PATHS`, the files the suite reads outside those two directories. The green record (rebuild/out/rebuild-contracts-green.json) is shared with the artifact cycle's gate:rebuild-contracts, so a green from either one counts for the other. The build checks its own artifacts: `run_m1.run_rule_witnesses` settles and checks every table's rule certificates on every M1 build, so no lane here reads `rebuild/out/m1`.

A run that spawns the suite is judged by the cycle's failure classifier, `classify_rebuild_output`, which parses the FAILED and ERROR summary lines so each failure is named. The result is also written as a kind:"check" line in the timings journal under the lane's name, rebuild-contracts (the name its pool line uses), with the failing test ids, so `make cycle-timings ARGS='--by-outcome'` can count which tests have caught failures across every run, not only the runs a cycle started. This wrapper always writes that line, because no cycle spawns it: `make test-rebuild` is only run from a terminal, so there is never a parent that writes the line instead, as the cycle does for the `make test` gate. A skip also writes a check line, with verdict skipped and no duration. An unchanged closure is still a result worth counting, but a suite that did not run has no duration, and a zero would pull the timing figures down.

The green record follows separate rules. A passing run during which the closure changed records nothing, because the tested content is no longer on disk. A failing run whose closure still matches the record deletes the record. Without git there is no closure to key on, so the suite always runs and records nothing. `make test-rebuild FORCE=1` (`--force`) runs the suite regardless.

The suite can run fewer tests than the key covers. The green record stores a per-test input closure beside the key: what each test read, imported and spawned, as recorded by rebuild/conftest.py's audit guard. When the key has changed, this wrapper diffs the record's per-label digests against the tree, writes the ids the diff cannot affect to a selection file the suite deselects, and prints what it skipped. `rebuild.tools.contracts_closure` defines the closure and the selection. Its rule is that any doubt runs the test: a test with no closure, a new or renamed id, a test that spawned a child the hook could not follow, and every test when an input was added or removed or a global label changed. A passing narrowed run records a green for the whole suite, because the skipped tests passed against inputs whose bytes have not changed, and it merges its sidecar into the record so the skipped tests keep their recorded closures. `--force` runs the whole suite and re-records every closure.

AMS_RUN_PYRIGHT is passed to the spawned suite in its environment. The suite's root conftest starts pyright in pytest_configure and joins it in pytest_sessionfinish, so it runs beside the xdist pool. Pyright skips itself on its own green record (`rebuild.tools.pyright_gate`), so a run narrowed to a rune edit's tests starts no type check. A pyright failure exits the suite nonzero with no FAILED or ERROR line, which `classify_rebuild_output` counts as a hard failure, so this wrapper fails and clears the lane's green record as it would for a failed test.

AMS_POOL_UNIT (`POOL_UNIT_ENV`) names the pool (`POOL_UNIT_BY_LANE`). It makes the suite's xdist controller append a kind:"pool" line with every worker's peak RSS to the cycle-timings journal, which is the figure `make job-costs` reports for this suite. The name is set only in a copy of the environment, so nothing spawned after the suite inherits it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.tools import contracts_closure
from rebuild.tools.artifact_cycle import (
    REBUILD_LANES,
    classify_rebuild_output,
    clear_contradicted_green,
    read_green_record,
    rebuild_lane_argv,
    rebuild_lane_closure,
    rebuild_lane_green,
    record_green,
)
from rebuild.tools.cycle_timings import POOL_UNIT_ENV, CheckVerdict, record_check
from rebuild.tools.pyright_gate import PYRIGHT_ENV

POOL_UNIT_BY_LANE = {"contracts": "rebuild-contracts"}


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
    """Run the lane, or skip it when its green record matches, and return its exit code and whether a suite was spawned. A nonzero code is a hard failure. A skip, a clean pass, and a pass whose closure changed during the run all return zero.

    Every path writes one check line, including the skip. The line is written before the green-record branches below, because those branches decide what happens to the green record, not whether the suite passed: a run whose green was not recorded (no git, or a closure that changed while the suite ran) still passed. The duration is measured around the spawn only, because the fingerprint computations on either side are the wrapper's overhead, not the suite's cost.
    """
    check = POOL_UNIT_BY_LANE[lane]
    record_path = rebuild_lane_green(lane)
    before, roster = rebuild_lane_closure(ROOT, lane)
    recorded = read_green_record(record_path)
    if not force and before is not None and recorded is not None and before == recorded["fingerprint"]:
        print(
            f"make test-rebuild: {lane} lane SKIPPED — its input closure is unchanged since its last green run ({recorded.get('finished_at')}). "
            "Run `make test-rebuild FORCE=1` to run it anyway."
        )
        record_check(
            CheckVerdict(check=check, verdict="skipped", status="skipped", failures=[], failed_ids=[])
        )
        return 0, False

    argv = rebuild_lane_argv(lane)
    files = _narrow(record_path, roster, recorded, force)
    lane_env = {**env, POOL_UNIT_ENV: check}
    started = time.perf_counter()
    returncode, stdout = _run_suite(argv, lane_env)
    elapsed = time.perf_counter() - started
    outcome = classify_rebuild_output(stdout, returncode, check)
    record_check(outcome, argv=argv, elapsed_s=elapsed)
    for test_id in outcome.failed_ids:
        print(f"  hard rebuild failure ({lane}): {test_id}")
    if not outcome.ok:
        clear_contradicted_green(record_path, before)
        print(f"make test-rebuild: {lane} lane {outcome.status}")
        return (returncode if returncode != 0 else 1), True
    if before is None:
        print(
            f"make test-rebuild: {lane} lane {outcome.status} (closure fingerprint unavailable without git — not recorded)"
        )
        return 0, True
    after, after_roster = rebuild_lane_closure(ROOT, lane)
    drifted = f"make test-rebuild: {lane} lane {outcome.status}, but its input closure changed while the suite ran — green not recorded"
    if after != before:
        print(drifted)
        return 0, True
    payload = contracts_closure.record_payload(
        ROOT, files or {}, after_roster or {}, recorded, contracts_closure.sidecar_path(record_path)
    )
    if payload.moved:
        print(drifted)
        return 0, True
    record_green(record_path, before, files=payload.files, closures=payload.closures)
    recorded_what = "closure fingerprint and per-test closures" if payload.closures else "closure fingerprint"
    where = record_path.relative_to(ROOT) if record_path.is_relative_to(ROOT) else record_path
    print(f"make test-rebuild: {lane} lane {outcome.status} — {recorded_what} recorded in {where}")
    return 0, True


def _narrow(
    record_path: Path, roster: dict[str, str] | None, recorded: dict | None, force: bool
) -> dict[str, str] | None:
    """Write the selection file the suite reads and print what it skips. Return the digest map the selection was computed over (the roster plus the paths recorded tests read outside it), so the green is recorded against the same labels. An empty selection runs the whole suite; that happens with no record or no closures in it, an added or removed input, a changed global label, `--force`, or no git. The previous sidecar is deleted first, so a suite that dies before session end cannot leave the last run's closures to be merged as this run's."""
    selection_file = contracts_closure.selection_path(record_path)
    contracts_closure.sidecar_path(record_path).unlink(missing_ok=True)
    if roster is None:
        contracts_closure.write_selection(selection_file, ())
        return None
    files = contracts_closure.current_files(ROOT, roster, recorded)
    if force:
        selection = contracts_closure.Selection(reason="--force runs the whole lane")
    else:
        selection = contracts_closure.select(recorded, files)
    contracts_closure.write_selection(selection_file, selection.skip)
    print(f"make test-rebuild: contracts lane — {selection.describe()}")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the rebuild pytest suite unless its input closure is unchanged since its last green run, judging the result through the artifact cycle's failure classifier."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run the whole suite even when its closure fingerprint matches the recorded green, re-recording every closure",
    )
    args = parser.parse_args(argv)

    env = dict(os.environ)
    for lane in REBUILD_LANES:
        returncode, _ran = _run_lane(lane, env, args.force)
        if returncode != 0:
            return returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
