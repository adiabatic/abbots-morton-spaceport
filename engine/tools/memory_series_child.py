"""The memory series' child (`engine/tools/memory_series.py`): one replay line's full replay ("The replay step").

The child (`replay` before its flags) runs `run_build.replay_and_absorb` over the line's cut, as a full replay of run_build runs it, at the stated width, with each crate's cache stats on and each absorb hashing its finished file and then discarding it, so the line writes no settle memo; it prints what the replay did as one JSON line (`replay_main`).

The child is a module of its own, whose top-level imports are the standard library alone, because multiprocessing's spawn imports the launching process's main module into every worker the child starts. Run as this module, a replay's absorb workers hold run_build's modules and this one beside them, where run_build's own hold run_build's alone. The series' own imports, the table step's among them, reach none of them.

Run as: python -m engine.tools.memory_series_child replay --spec PICKLE --spec-dump JSON --out DIR --configs a,b --width N [--built], which the series does once a replay line.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections.abc import Sequence
from pathlib import Path

# The stamp of the settle memo files a replay line's absorbs lay out and never write.
REPLAY_STAMP = "memory-series-replay"


def replay_main(argv: Sequence[str]) -> int:
    """Run one replay line's full replay (`run_build.replay_and_absorb`) over the cut pickled at `--spec`, whose dump the crate reads from `--spec-dump`, with its tables under `--out`, over `--configs` at `--width`, every crate's cache stats on and every absorb hashing its file and discarding it. `--built` marks the kernel built (`kernel_exec._BUILT`), which the series passes once its own build has succeeded, so the replay runs no `cargo build` inside the line; without it the replay builds as run_build's does. Print what it did as one JSON line on stdout, its last: each walk's counts or error, its crate's lines, peaks and CPU seconds, and its absorb worker's pid, and each absorb's peaks, CPU seconds, rows, seconds, warning and file digest."""
    parser = argparse.ArgumentParser(prog="python -m engine.tools.memory_series_child replay")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--spec-dump", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--configs", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument(
        "--built", action="store_true", help="the series' own `kernel_exec.ensure_built()` has succeeded"
    )
    args = parser.parse_args(argv)
    from engine.pipeline import conform, kernel_exec, run_build

    if args.built:
        kernel_exec._BUILT = True

    with args.spec.open("rb") as handle:
        spec = pickle.load(handle)
    memos = {
        config: conform.SettleMemoFile(args.out / f"settle-memo-{config}.bin", REPLAY_STAMP, {})
        for config in args.configs.split(",")
    }
    replayed = run_build.replay_and_absorb(
        spec, args.out, memos, args.width, cache_stats=True, keep=False, digest=True, spec_path=args.spec_dump
    )
    walks = {
        config: {
            "counts": walk.counts,
            "error": walk.error,
            "lines": list(walk.lines),
            "peaks": None if walk.peaks is None else list(walk.peaks),
            "worker": walk.worker,
        }
        for config, walk in replayed.walks.items()
    }
    absorbs = {
        config: {
            "resident": absorb.resident,
            "footprint": absorb.footprint,
            "cpu": absorb.cpu,
            "entries": absorb.entries,
            "seconds": absorb.seconds,
            "warning": absorb.warning,
            "digest": None if absorb.digest is None else list(absorb.digest),
        }
        for config, absorb in replayed.absorbs.items()
    }
    print(json.dumps({"passed": replayed.passed, "walks": walks, "absorbs": absorbs}), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["replay"]:
        return replay_main(argv[1:])
    raise SystemExit("usage: python -m engine.tools.memory_series_child replay ...")


if __name__ == "__main__":
    raise SystemExit(main())
