"""`make test-rebuild`'s entry point: run the rebuild pytest suite only when gate:rebuild's input closure has changed since the last green run.

The closure and its fingerprint are artifact_cycle's — the rebuild/ and glyph_data/ sources (minus Markdown, the carried-verdict evidence, and the JS-only jstests) plus conftest.py, pyproject.toml, uv.lock, the out/m1 artifacts the suite reads, and the site fonts and baselines it shapes against. When the fingerprint matches the shared green record (rebuild/out/rebuild-gate-green.json), the recorded green already describes this exact closure content and the wrapper exits 0. Otherwise it runs the suite with the cycle's exact argv and judges the result through the cycle's own failure classifier, because a plain exit-code check cannot: the suite exits nonzero by design on the documented baseline failures, which classify as green; a stale-census green is a pass but not recordable (it depends on the pin re-baseline only the artifact cycle runs); an unexplained failure is red. A recordable green rewrites the record — so interactive runs and the artifact cycle's gate:rebuild each skip on the other's greens. `make test-rebuild FORCE=1` (--force) runs the suite regardless; a red run whose closure still matches the record deletes it, since the green it claims is contradicted. A green run during which the closure moved records nothing, because the tested content is no longer on disk.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.tools.artifact_cycle import (
    REBUILD_GATE_GREEN,
    REBUILD_PYTEST_ARGV,
    classify_rebuild_output,
    clear_contradicted_green,
    read_green_record,
    rebuild_gate_skip_fingerprint,
    record_green,
)


def _run_suite() -> tuple[int, str]:
    proc = subprocess.Popen(REBUILD_PYTEST_ARGV, cwd=ROOT, text=True, stdout=subprocess.PIPE, bufsize=1)
    lines: list[str] = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line.rstrip("\r\n"))
    proc.stdout.close()
    return proc.wait(), "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the rebuild pytest suite unless its input closure is unchanged since the last green run, judging the result through the artifact cycle's failure classifier."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run the suite even when the closure fingerprint matches the recorded green",
    )
    args = parser.parse_args(argv)

    before = rebuild_gate_skip_fingerprint(ROOT)
    recorded = read_green_record(REBUILD_GATE_GREEN)
    if not args.force and before is not None and recorded is not None and before == recorded["fingerprint"]:
        print(
            f"make test-rebuild: SKIPPED — input closure unchanged since its last green run ({recorded.get('finished_at')}). "
            "Nothing the suite reads has changed (the rebuild/ and glyph_data/ sources, the out/m1 artifacts, the site fonts and baselines, conftest.py, pyproject.toml, uv.lock). "
            "Run `make test-rebuild FORCE=1` to run it anyway."
        )
        return 0

    returncode, stdout = _run_suite()
    outcome = classify_rebuild_output(stdout, returncode, update_pins=False)
    for test_id in outcome.hard_ids:
        print(f"  hard rebuild failure: {test_id}")
    if outcome.hard_ids:
        clear_contradicted_green(REBUILD_GATE_GREEN, before)
        print(f"make test-rebuild: {outcome.status}")
        return returncode if returncode != 0 else 1
    if not outcome.recordable:
        print(
            f"make test-rebuild: {outcome.status} — green not recorded (a stale-census green depends on the pin re-baseline only the artifact cycle runs)"
        )
        return 0
    if before is None:
        print(
            f"make test-rebuild: {outcome.status} (closure fingerprint unavailable without git — not recorded)"
        )
        return 0
    if rebuild_gate_skip_fingerprint(ROOT) != before:
        print(
            f"make test-rebuild: {outcome.status}, but the input closure changed while the suite ran — green not recorded"
        )
        return 0
    record_green(REBUILD_GATE_GREEN, before)
    where = (
        REBUILD_GATE_GREEN.relative_to(ROOT)
        if REBUILD_GATE_GREEN.is_relative_to(ROOT)
        else REBUILD_GATE_GREEN
    )
    print(f"make test-rebuild: {outcome.status} — closure fingerprint recorded in {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
