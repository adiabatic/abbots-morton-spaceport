"""`make test`'s entry point: run the font suite only when its input closure has changed since the last green run.

The closure and its fingerprint are artifact_cycle's — everything the suite (make all, typst, pyright, pytest test/ site/) can read, which is every tracked or untracked-unignored file outside what make_test_exempt exempts (the authority, and the argument for each exemption): the exempt trees, the exempt files, Markdown, and the Makefile itself, whose two executed rules ride in as a hash of what `make -n all` and `make -n test` print rather than as the file's bytes. When the fingerprint matches the shared green record (rebuild/out/make-test-green.json), the recorded green already describes this exact closure content, so re-running the ≈15 CPU-minute suite would verify nothing; the wrapper prints the skip and exits 0. Otherwise it runs the real suite and, on green, rewrites the record — so interactive runs and the artifact cycle's gate:make-test each skip on the other's greens. `make test FORCE=1` (--force) runs the suite regardless; a forced red run whose closure still matches the record deletes it, since the green it claims is contradicted. A green run during which the closure moved records nothing, because the tested content is no longer on disk.

The suite child is told what its pool is called (POOL_UNIT, in AMS_POOL_UNIT), which is what has its controller append a kind:"pool" line to the cycle-timings journal naming every worker's peak — the measurement `make job-costs` holds FONT_SUITE_WORKER_BYTES against, so the constant that prices this pool cannot go stale in silence. The name goes on the child's own environment dict and never on this process's, so nothing spawned later inherits it and files its pool under the font suite's name. This covers both spellings of the same run, since the cycle's gate:make-test is literally `make test` and so comes through here too.

The run is also judged and filed as a kind:"check" line in that same journal, under the name the cycle's step already uses for it: green or red from judge_make_test, or skipped when the green record answered before anything spawned. That the two spellings of a run both come through here is exactly why this one has to stand down for a parent: when AMS_CYCLE_RUN (CYCLE_RUN_ENV) is in the environment a cycle spawned this as gate:make-test and is recording the same invocation itself, so recording here too would put one suite run on the record twice and turn --by-outcome's counts into a count of processes that had an opinion. The suppression covers every path through here, the self-skip included: the parent files one line for the invocation it drove whatever this process decided to do about it, and a second line from inside would be the same invocation counted twice however it is labeled.
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

from rebuild.tools.artifact_cycle import (
    MAKE_TEST_GREEN,
    make_test_closure_fingerprint,
    read_make_test_green,
    record_make_test_green,
)
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, POOL_UNIT_ENV, CheckVerdict, record_check

PYTEST_ARGV = ["uv", "run", "pytest", "test/", "site/", "-n", "auto", "--dist", "worksteal"]
POOL_UNIT = "font-suite"
CHECK = "make-test"


def judge_make_test(returncode: int) -> CheckVerdict:
    """The font suite's whole judgment, which is its return code — and unusually, that is honest. The rebuild lanes need their output parsed because pyright can exit them nonzero with no summary line to name, and this suite has no summary of its own, so a nonzero exit here means a test failed and nothing else does.

    That is what lets the child keep its TTY: nothing has to be captured to reach a verdict, so the suite prints its live progress line, its colors, and any traceback straight to the terminal a human is watching. The price is `failed_ids`, which stays empty — the ids are on that terminal and not in this process — and it is the right price, since the cycle's own gate:make-test spawns the same uncaptured child and could not populate them either. The status strings are the cycle's, so the label on a check line and the label in a cycle summary are one string with one spelling.
    """
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
    """File this invocation's check line, unless a cycle spawned us and is filing one for the same invocation. The environment is read here rather than at import so the variable a parent set is seen however this module was loaded."""
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
    if not args.force and before is not None and recorded is not None and before == recorded["fingerprint"]:
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
            MAKE_TEST_GREEN.unlink(missing_ok=True)
        return returncode
    if before is None:
        print("make test: green (closure fingerprint unavailable without git — not recorded)")
        return 0
    if make_test_closure_fingerprint(ROOT) != before:
        print("make test: green, but the input closure changed while the suite ran — green not recorded")
        return 0
    record_make_test_green(before)
    where = MAKE_TEST_GREEN.relative_to(ROOT) if MAKE_TEST_GREEN.is_relative_to(ROOT) else MAKE_TEST_GREEN
    print(f"make test: green — closure fingerprint recorded in {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
