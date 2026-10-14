"""`make test-rebuild`'s entry point: run the rebuild pytest suite, but only when its input closure has changed since its last green run.

The contracts suite contains every rebuild test. `contracts_closure.prepare_run`, `start_run`, and `finish_run` decide the skip, the selection, and the green record (rebuild/out/rebuild-contracts-green.json), which this wrapper shares with the artifact cycle's gate:rebuild-contracts; `artifact_cycle.contracts_closure` says what the lane's key covers, and the module docstring of `rebuild.tools.contracts_closure` says what a test's closure holds and which tests a run selects. This wrapper prints the skip or the selection, spawns the suite (`artifact_cycle.contracts_argv`), and reports whether a green was recorded. `make test-rebuild FORCE=1` (`--force`) runs the whole suite regardless.

A run that spawns the suite gets its result from the cycle's failure classifier, `classify_rebuild_output`, which parses the FAILED and ERROR summary lines so each failure is named. The result is also written as a kind:"check" line in the timings journal under the lane's name, rebuild-contracts (the name its pool line uses), with the failing test ids, so `make cycle-timings ARGS='--by-outcome'` can count which tests have caught failures across every run, not only the runs a cycle started. This wrapper always writes that line, because no cycle spawns it: `make test-rebuild` is only run from a terminal, so there is never a parent that writes the line instead, as the cycle does for the `make test` gate. A skip also writes a check line, with outcome skipped and no duration. An unchanged closure is still a result worth counting, but a suite that did not run has no duration, and a zero would pull the timing figures down.

AMS_RUN_PYRIGHT is passed to the spawned suite in its environment, and the root conftest runs pyright beside the suite (`rebuild.tools.pyright_gate`); a pyright failure fails this run through `classify_rebuild_output`.

AMS_POOL_UNIT (`POOL_UNIT_ENV`) names the contracts pool (`POOL_UNIT`). It makes the suite's xdist controller append a kind:"pool" line with every worker's peak RSS to the cycle-timings journal, which is the figure `make job-costs` reports for this suite. The name is set only in a copy of the environment, so nothing spawned after the suite inherits it.
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
    classify_rebuild_output,
    contracts_argv,
)
from rebuild.tools.cycle_timings import POOL_UNIT_ENV, CheckResult, record_check
from rebuild.tools.pyright_gate import PYRIGHT_ENV

POOL_UNIT = "rebuild-contracts"


def _run_suite(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, bufsize=1)
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line.rstrip("\r\n"))
    proc.stdout.close()
    return proc.wait(), "\n".join(lines)


def _run_contracts(env: dict[str, str], force: bool) -> int:
    """Run contracts through the shared lifecycle and write one timings check for either a skip or the spawned suite. The duration measures only the spawn, so digest preparation and publication remain wrapper overhead."""
    run = contracts_closure.prepare_run(ROOT, force)
    if run.skippable:
        assert run.previous is not None
        print(
            f"make test-rebuild: contracts lane SKIPPED — its input closure is unchanged since its last green run ({run.previous.get('finished_at')}). "
            "Run `make test-rebuild FORCE=1` to run it anyway."
        )
        record_check(
            CheckResult(check=POOL_UNIT, outcome="skipped", status="skipped", failures=[], failed_ids=[])
        )
        return 0

    argv = contracts_argv()
    contracts_closure.start_run(run)
    print(f"make test-rebuild: contracts lane — {run.selection.describe()}")
    suite_env = {**env, POOL_UNIT_ENV: POOL_UNIT}
    started = time.perf_counter()
    try:
        returncode, stdout = _run_suite(argv, suite_env)
    except Exception:
        contracts_closure.finish_run(ROOT, run, False)
        raise
    elapsed = time.perf_counter() - started
    result = classify_rebuild_output(stdout, returncode, POOL_UNIT)
    record_check(result, argv=argv, elapsed_s=elapsed)
    finished = contracts_closure.finish_run(ROOT, run, result.ok)
    for test_id in result.failed_ids:
        print(f"  hard rebuild failure (contracts): {test_id}")
    if not result.ok:
        print(f"make test-rebuild: contracts lane {result.status}")
        return returncode if returncode != 0 else 1
    if finished.status == "unavailable":
        print(
            f"make test-rebuild: contracts lane {result.status} (closure fingerprint unavailable without git — not recorded)"
        )
        return 0
    if finished.status == "drifted":
        print(
            f"make test-rebuild: contracts lane {result.status}, but its input closure changed while the suite ran — green not recorded"
        )
        return 0
    payload = finished.payload
    assert payload is not None
    recorded_what = "closure fingerprint and per-test closures" if payload.closures else "closure fingerprint"
    record_path = run.record_path
    where = record_path.relative_to(ROOT) if record_path.is_relative_to(ROOT) else record_path
    print(f"make test-rebuild: contracts lane {result.status} — {recorded_what} recorded in {where}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the rebuild pytest suite unless its input closure is unchanged since its last green run, taking its result from the artifact cycle's failure classifier."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run the whole suite even when its closure fingerprint matches the recorded green, re-recording every closure",
    )
    args = parser.parse_args(argv)

    env = dict(os.environ)
    return _run_contracts(env, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
