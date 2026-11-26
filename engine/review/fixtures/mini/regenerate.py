"""Regenerate the mini bundle beside this file from the live build output in `engine/out/build`.

The bundle gives the contracts lane a frozen workload without reading `engine/out/`. The tests that read it (the ink comparator's readings of a placed run, the conformance sweep's comparisons, the table diff's real tables) make claims about the code that reads a workload, not about the current build, so a frozen one tests them as well as the live one and takes seconds instead of minutes.

The bundle holds:

- `Senior-Regular.otf`, a copy of the live build's font.
- `settlement-default.tsv` and `joins-default.tsv`, which `engine/test_review_tablediff.py` reads as real tables.
- `pin.json`, the tree and blob shas of `pin.PINNED_PATHS` at the commit this ran on.

All of it must be regenerated together. A font from one build beside tables from another, or a pin from a third, would make a test report disagreements caused by the bundle and not by the code.

The pin replaces a checked-in copy of the spec. The `mini_bundle` fixture in `engine/conftest.py` writes the pinned objects out of git, once a test run, into a temporary directory that a test passes as its spec root. So a test that re-derives settlement re-derives the settlement these files were written under, a rune edit cannot make them stale, and there is no second copy of the runes in the tree to edit by mistake.

Two checks make HEAD safe to pin. The pinned paths must be clean, so HEAD's bytes are the working tree's. The live build's recorded `data` fingerprint must equal the one the tree hashes to now, so the frozen files were built under that spec. If either fails, the pin could name a spec no build ran, and a content-addressed pin cannot detect that later.

Run it after `run_build` has written a fresh `engine/out/build`:

    uv run python engine/review/fixtures/mini/regenerate.py
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from engine.pipeline import fingerprint  # noqa: E402
from engine.review.fixtures.mini import pin  # noqa: E402

LIVE = REPO_ROOT / "engine" / "out" / "build"
FILES = ("Senior-Regular.otf", "settlement-default.tsv", "joins-default.tsv")


def main() -> int:
    absent = [name for name in FILES if not (LIVE / name).is_file()]
    if absent:
        print(f"the live build has no {', '.join(absent)} under {LIVE}; run run_build first", file=sys.stderr)
        return 1
    dirty = pin.dirty_paths()
    if dirty:
        print("the pin names committed objects; commit (or stash) these first:", file=sys.stderr)
        for line in dirty:
            print(line, file=sys.stderr)
        return 1
    recorded = fingerprint.read_stage_a(LIVE)
    if recorded is None:
        print(
            "no passing run_build has recorded the inputs of engine/out/build; run run_build first",
            file=sys.stderr,
        )
        return 1
    if recorded["data"] != fingerprint.data_value(REPO_ROOT):
        print(
            "engine/out/build was not built from the spec as it stands on disk; run run_build first",
            file=sys.stderr,
        )
        return 1
    for name in FILES:
        shutil.copyfile(LIVE / name, HERE / name)
    record = pin.write_pin()
    print(f"{', '.join(FILES)} copied, spec pinned at {record['head'][:12]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
