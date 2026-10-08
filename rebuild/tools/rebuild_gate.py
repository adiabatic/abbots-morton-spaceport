"""`make test-rebuild`'s entry point: run the rebuild pytest suite, but only when its input closure has changed since its last green run.

The contracts suite contains every rebuild test. No test under rebuild/ reads live build output (the audit guard in rebuild/conftest.py fails one that does), so the suite runs on every usable core, and its closure contains no build output, which lets an artifact-only cycle skip it. The closure is the rebuild/ and glyph_data/ sources (without Markdown and the paths in `cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES`), conftest.py, pyproject.toml, uv.lock, the site fonts the suite shapes against, and `artifact_cycle.REBUILD_GATE_HARNESS_PATHS`, the files the suite reads outside those two directories. The green record (rebuild/out/rebuild-contracts-green.json) is shared with the artifact cycle's gate:rebuild-contracts, so a green from either one counts for the other. The build checks its own artifacts: `run_m1.run_rule_witnesses` settles and checks every table's rule certificates on every M1 build, so no test here reads `rebuild/out/m1`.

A run that spawns the suite gets its result from the cycle's failure classifier, `classify_rebuild_output`, which parses the FAILED and ERROR summary lines so each failure is named. The result is also written as a kind:"check" line in the timings journal under the lane's name, rebuild-contracts (the name its pool line uses), with the failing test ids, so `make cycle-timings ARGS='--by-outcome'` can count which tests have caught failures across every run, not only the runs a cycle started. This wrapper always writes that line, because no cycle spawns it: `make test-rebuild` is only run from a terminal, so there is never a parent that writes the line instead, as the cycle does for the `make test` gate. A skip also writes a check line, with outcome skipped and no duration. An unchanged closure is still a result worth counting, but a suite that did not run has no duration, and a zero would pull the timing figures down.

`contracts_closure.prepare_run`, `start_run`, and `finish_run` own both callers' selection and green-record decisions. A whole-suite skip requires both the roster key and the recorded extra-file projection to match. The green record follows separate rules. A passing run during which the closure changed records nothing, because the tested content is no longer on disk. A failing run whose closure still matches the record deletes the record. Without git there is no closure to key on, so the suite always runs and records nothing. `make test-rebuild FORCE=1` (`--force`) runs the suite regardless.

The suite can run fewer tests than the key covers. The green record stores a per-test input closure beside the key: what each test read, imported and spawned, as recorded by rebuild/conftest.py's audit guard. When the key has changed, this wrapper diffs the record's per-label digests against the tree, writes the ids the diff cannot affect to a selection file the suite deselects, and prints what it skipped. `rebuild.tools.contracts_closure` defines the closure and the selection. Its rule is that any doubt runs the test: a test with no closure, a new or renamed id, a test that spawned a child the hook could not follow, and every test when an input was added or removed or a global label changed. A passing narrowed run records a green for the whole suite, because the skipped tests passed against inputs whose bytes have not changed, and it merges its sidecar into the record so the skipped tests keep their recorded closures. `--force` runs the whole suite and re-records every closure.

AMS_RUN_PYRIGHT is passed to the spawned suite in its environment. The suite's root conftest starts pyright in pytest_configure and joins it in pytest_sessionfinish, so it runs beside the xdist pool. Pyright skips itself on its own green record (`rebuild.tools.pyright_gate`), so a run narrowed to a rune edit's tests starts no type check. A pyright failure exits the suite nonzero with no FAILED or ERROR line, which `classify_rebuild_output` counts as a hard failure, so this wrapper fails and clears the lane's green record as it would for a failed test.

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
