"""`make test-rebuild`'s entry point: run the rebuild pytest suite, and only when its input closure has changed since its last green run.

The suite is one lane, contracts, and rebuild/conftest.py is the authority on what that means: no test under rebuild/ reads live build output — the audit guard there fails one that does — so the suite runs at the box's full xdist width and its closure — the rebuild/ and glyph_data/ sources (minus Markdown and the paths `artifact_cycle.REBUILD_GATE_EXEMPT_PREFIXES` names), conftest.py, pyproject.toml, uv.lock, the site fonts it shapes against, and the shaping harness and corpus pages in `artifact_cycle.REBUILD_GATE_HARNESS_PATHS`, the roster of what the suite reads outside those two trees — contains no build output at all, which is what lets an artifact-only cycle skip it. The green record (rebuild/out/rebuild-contracts-green.json) is shared with the artifact cycle's gate:rebuild-contracts, so interactive greens and cycle greens count for each other in both directions. What the build can prove about its own artifacts it proves in the build: the rule certificates every table carries are settled and asserted by `run_m1.run_rule_witnesses` on every M1 build, so there is no lane here that has to read `rebuild/out/m1` and no stamp for this wrapper to refuse on.

A run that actually spawns the suite is judged through the cycle's own failure classifier, which parses the FAILED/ERROR summary lines so a failure is named rather than just counted; every green is recordable. That verdict is also filed, as a kind:"check" line in the timings journal under the lane's own name — rebuild-contracts, the same spelling its pool line already uses — carrying the ids the suite failed on, so `make cycle-timings --by-outcome` can say which test has ever caught anything across every run rather than only across the ones a cycle happened to drive. This wrapper files that line unconditionally, because no cycle ever spawns it: `make test-rebuild` is always someone at a terminal, so there is never a second writer to stand down for the way the `make test` gate stands down for its parent. A skip files a check too, judged skipped and carrying no seconds — a closure judged unchanged is still a judgment and worth counting, while a suite that never ran has no duration to report and a zero would drag the timing rows toward a suite that never happened. The green record is a separate ledger on separate rules: a green run during which the closure moved records nothing, because the tested content is no longer on disk; a red run whose closure still matches its record deletes it, since the green it claims is contradicted; and without git there is no closure to key on, so the suite runs unconditionally and records nothing. `make test-rebuild FORCE=1` (--force) runs it regardless.

The suite runs narrower than its key says. The green record carries a per-test input closure beside the key — what each test read, imported and spawned, recorded by rebuild/conftest.py's audit guard — and when the key has moved this wrapper diffs the record's per-label digests against the tree, writes the ids the diff cannot reach to a selection file the suite deselects from, and prints what that kept off. `rebuild.tools.contracts_closure` is the authority on the closure and the selection, and its rule is that every doubt runs the test: a test with no closure, a new or renamed id, a test that spawned a child the hook could not follow, and every test at all when an input was added or removed or a global one moved. A green narrowed run records a green for the whole suite, because the tests it kept off passed against inputs whose bytes have not changed, and it merges its sidecar into the record so those tests keep the closures they were recorded with. `--force` runs the whole suite and re-records every closure.

AMS_RUN_PYRIGHT rides the environment into the spawned suite: pyright checks the whole tree from `[tool.pyright] include`, and the suite's pytest_configure is where it runs, overlapping collection.

AMS_POOL_UNIT names the pool (POOL_UNIT_BY_LANE), which is what has the suite's xdist controller append a kind:"pool" line to the cycle-timings journal recording every worker's peak, the observation `make job-costs` reports for this suite. The name is written into a copy of the environment and never into the shared one, so nothing spawned after the suite inherits it.
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

PYRIGHT_ENV = "AMS_RUN_PYRIGHT"
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
    """Run (or validly skip) the lane, returning its exit code and whether it actually spawned a suite. A nonzero code is a hard failure; every other outcome — a skip, a clean green, a green whose closure drifted — is a zero the caller carries on from.

    Every path through here files exactly one check line, the skip included. The judgment is filed before the green-record branching below rather than inside it, because those branches are four things to do with a green record and not four verdicts: a run whose green went unrecorded — no git to key on, or a closure that moved while the suite ran — is still a run that passed, and the journal is asked what the suite decided, not what the record keeper could do about it. The seconds are the suite's own, measured around the spawn rather than around this function, since the fingerprint passes on either side of it are the wrapper's overhead and not the check's cost.
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
    """Write the selection file the spawn reads and say what it keeps off, returning the widened digest map the selection was taken over so the green can be recorded against the same labels. An empty selection — no record, no closures in it, a structural or global diff, `--force`, or no repository to key on — runs the whole suite, and the previous sidecar is cleared first so a suite that dies before session end cannot leave last run's closures to be merged as this one's."""
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
