"""`make test`'s entry point: run the font suite only when its input closure has changed since the last green run.

The closure and its fingerprint come from artifact_cycle. The closure is every tracked or untracked-unignored file the suite (make all, typst, pyright, pytest test/ site/) can read, minus what `make_test_exempt` exempts; that function's docstring argues each exemption. The Makefile is exempt as a file, and its two executed rules are hashed instead as the output of `make -n all` and `make -n test`. When the fingerprint matches the shared green record (rebuild/out/make-test-green.json), the wrapper prints the skip and exits 0. Otherwise it runs the suite and, on green, rewrites the record, so interactive runs and the artifact cycle's gate:make-test each skip on the other's greens. `make test FORCE=1` (--force) runs the suite regardless, and a forced cycle pass (--fresh or --force-make-test) passes FORCE=1. `make_test_skippable` in artifact_cycle is the one predicate both this wrapper and the cycle's plan use, so the plan reserves cores for the gate only when the suite will run. A forced red run whose closure still matches the record deletes the record. A green run during which the closure changed records nothing, because the tested content is no longer on disk.

The suite child gets POOL_UNIT in AMS_POOL_UNIT, which makes its xdist controller append a kind:"pool" line to the cycle-timings journal with every worker's peak. `make job-costs` compares that measurement with FONT_SUITE_WORKER_BYTES. The variable is set on the child's own environment, not on this process's, so nothing spawned later inherits it and records its pool under the font suite's name. This applies to the cycle's gate:make-test too, because that step runs `make test`.

Each run is also recorded as a kind:"check" line in the same journal, under the name the cycle's step uses: green or red from `judge_make_test`, or skipped when the green record matched. When AMS_CYCLE_RUN (CYCLE_RUN_ENV) is set, a cycle spawned this process as gate:make-test and records the check line itself, so this process records nothing on any path, the skip included. Otherwise each suite run would be counted twice in `--by-outcome`.
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

from rebuild.tools import cycle_paths
from rebuild.tools.artifact_cycle import (
    make_test_closure_fingerprint,
    make_test_skippable,
    read_make_test_green,
    record_make_test_green,
)
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, POOL_UNIT_ENV, CheckVerdict, record_check

PYTEST_ARGV = ["uv", "run", "pytest", "test/", "site/", "-n", "auto", "--dist", "worksteal"]
POOL_UNIT = "font-suite"
CHECK = "make-test"


def judge_make_test(returncode: int) -> CheckVerdict:
    """Return the font suite's verdict from its return code alone: zero is green, and any nonzero exit is red, whether a test, `make all` or pyright failed. Nothing reads the child's output, so the child keeps the terminal and prints its progress, colors and tracebacks directly. As a result `failed_ids` is always empty. The rebuild suite differs: `artifact_cycle.classify_rebuild_output` captures its output to name the failing tests. The status strings match the cycle's `_rc_verdict`, so a check line and a cycle summary use the same label."""
    if returncode == 0:
        return CheckVerdict(
            check=CHECK, verdict="green", status="green", failures=[], failed_ids=[], recordable=True
        )
    return CheckVerdict(
        check=CHECK,
        verdict="red",
        status=f"FAILED (exit {returncode})",
        failures=["make test failed"],
        failed_ids=[],
    )


def _record(verdict: CheckVerdict, **kw) -> None:
    """Record this invocation's check line, unless a cycle spawned this process and records the line itself. The environment is read at call time, not at import, so a variable the parent set is seen however this module was loaded."""
    if CYCLE_RUN_ENV in os.environ:
        return
    record_check(verdict, **kw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run `make test`'s pytest suite unless its input closure is unchanged since the last green run."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run the suite even when the closure fingerprint matches the recorded green",
    )
    args = parser.parse_args(argv)

    before = make_test_closure_fingerprint(ROOT)
    recorded = read_make_test_green()
    if recorded is not None and make_test_skippable(before, recorded["fingerprint"], force=args.force):
        print(
            f"make test: SKIPPED — input closure unchanged since its last green run ({recorded.get('finished_at')}). "
            "Nothing the suite reads has changed (make_test_exempt in rebuild/tools/artifact_cycle.py is the authority on what is outside its closure: the exempt trees and files, Markdown, and the Makefile beyond what `make -n all` and `make -n test` print). "
            "Run `make test FORCE=1` to run it anyway."
        )
        _record(CheckVerdict(check=CHECK, verdict="skipped", status="skipped", failures=[], failed_ids=[]))
        return 0

    started = time.perf_counter()
    returncode = subprocess.run(
        PYTEST_ARGV, cwd=ROOT, env={**os.environ, POOL_UNIT_ENV: POOL_UNIT}
    ).returncode
    _record(judge_make_test(returncode), argv=PYTEST_ARGV, elapsed_s=time.perf_counter() - started)
    if returncode != 0:
        if recorded is not None and before is not None and before == recorded["fingerprint"]:
            cycle_paths.MAKE_TEST_GREEN.unlink(missing_ok=True)
        return returncode
    if before is None:
        print("make test: green (closure fingerprint unavailable without git — not recorded)")
        return 0
    if make_test_closure_fingerprint(ROOT) != before:
        print("make test: green, but the input closure changed while the suite ran — green not recorded")
        return 0
    record_make_test_green(before)
    where = (
        cycle_paths.MAKE_TEST_GREEN.relative_to(ROOT)
        if cycle_paths.MAKE_TEST_GREEN.is_relative_to(ROOT)
        else cycle_paths.MAKE_TEST_GREEN
    )
    print(f"make test: green — closure fingerprint recorded in {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
