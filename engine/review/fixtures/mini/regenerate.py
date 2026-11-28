"""Regenerate the mini bundle beside this file from the live build output in `engine/out/build`.

The bundle gives the contracts lane a frozen workload without reading `engine/out/`. The tests that read it (the ink comparator's readings of a placed run, the conformance sweep's comparisons) make claims about the code that reads a workload, not about the current build, so a frozen one tests them as well as the live one and takes seconds instead of minutes.

The bundle holds `Senior-Regular.otf`, a copy of the live build's font.

Two checks make the live font safe to freeze. A passing `run_build` must have recorded the inputs of `engine/out/build`, and its recorded `data` fingerprint must equal the one the tree hashes to now, so the frozen font is a passing build of the spec on disk.

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

LIVE = REPO_ROOT / "engine" / "out" / "build"
FILES = ("Senior-Regular.otf",)


def main() -> int:
    absent = [name for name in FILES if not (LIVE / name).is_file()]
    if absent:
        print(f"the live build has no {', '.join(absent)} under {LIVE}; run run_build first", file=sys.stderr)
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
    print(f"{', '.join(FILES)} copied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
