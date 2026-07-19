"""`make test`'s entry point: run the font suite only when its input closure has changed since the last green run.

The closure and its fingerprint are artifact_cycle's — every tracked or untracked-unignored file outside rebuild/, glyph_data/runes/, doc/, tmp/, .claude/, and Markdown, i.e. everything the suite (make all, typst, pyright, pytest test/ site/) can read. When the fingerprint matches the shared green record (rebuild/out/make-test-green.json), the recorded green already describes this exact closure content, so re-running the ~15 CPU-minute suite would verify nothing; the wrapper prints the skip and exits 0. Otherwise it runs the real suite and, on green, rewrites the record — so interactive runs and the artifact cycle's gate:make-test each skip on the other's greens. `make test FORCE=1` (--force) runs the suite regardless; a forced red run whose closure still matches the record deletes it, since the green it claims is contradicted. A green run during which the closure moved records nothing, because the tested content is no longer on disk.
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
    MAKE_TEST_GREEN,
    make_test_closure_fingerprint,
    read_make_test_green,
    record_make_test_green,
)

PYTEST_ARGV = ["uv", "run", "pytest", "test/", "site/", "-n", "auto", "--dist", "worksteal"]


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
            "Nothing the suite reads has changed (diffs confined to rebuild/, glyph_data/runes/, doc/, tmp/, .claude/, or Markdown cannot move it). "
            "Run `make test FORCE=1` to run it anyway."
        )
        return 0

    returncode = subprocess.run(PYTEST_ARGV, cwd=ROOT).returncode
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
