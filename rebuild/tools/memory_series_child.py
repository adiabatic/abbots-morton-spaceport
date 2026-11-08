"""The memory series' children (`rebuild/tools/memory_series.py`): one corpus line's build ("The corpus step") or one replay line's full replay ("The replay step").

A corpus child runs `build.build_m1` as a library over the line's inputs at the stated widths. It points the timings journal at the line's `--journal`, installs the mark reporter (`memory_tally.report_marks`), which prints each mark with this process's resident set and footprint (`[m]`) and on a load line (`--load`) stops the build at the plan's mark, makes a load line's substitutions (`substitute_for_load`), and prints this process's own rusage and its reaped children's (`[r]`) before it exits.

A replay child (`replay` before its flags) runs `run_m1.replay_and_absorb` over the line's cut, as a full replay of run_m1 runs it, at the stated width, with each crate's cache stats on and each absorb hashing its finished file and then discarding it, so the line writes no settle memo; it prints what the replay did as one JSON line (`replay_main`).

The child is a module of its own, whose top-level imports are the standard library and modules the build imports itself, because multiprocessing's spawn imports the launching process's main module into every worker the build starts. Run as this module, the build's parent and each of its units, write and signature workers hold the build's modules and this one beside them, where a cycle's build, run as `python -m rebuild.review.build`, holds the build's modules alone; a replay's absorb workers likewise hold run_m1's modules and this one, where run_m1's own hold run_m1's alone. The series' own imports, the table step's among them, reach none of them.

Run as: python -m rebuild.tools.memory_series_child --inputs JSON --audit FIFO --out DIR --journal FILE --jobs N --signature-jobs N --write-jobs N [--load], which the series does once a corpus line, or python -m rebuild.tools.memory_series_child replay --spec PICKLE --spec-dump JSON --out DIR --configs a,b --width N [--built], once a replay line.
"""

from __future__ import annotations

import argparse
import functools
import json
import pickle
import resource
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from types import ModuleType

from rebuild.pipeline.model import ResolvedSpec
from rebuild.tools import memory_tally, peak_rss

MARK = "[m] "
RUSAGE = "[r] "
PLAN_MARK = "plan"
MOVED = 0x100


@dataclass(frozen=True)
class CorpusInputs:
    """Where a corpus line's build reads each input: the audit the harness feeds the build's FIFO from, the subset tables' directory (`m1`, which also holds the Stage A record) and their pack, the M1 font, the live corpus's manifest and ink-signature store, the spec root and the ledger under it, and the two site fonts. `under` lays them out in a tree of clones."""

    audit: Path
    m1: Path
    subset_pack: Path
    after_font: Path
    manifest: Path
    signatures: Path
    spec_root: Path
    ledger: Path
    before_font: Path
    junior_font: Path

    @classmethod
    def under(cls, base: Path) -> CorpusInputs:
        """The inputs as `memory_series.clone_inputs` lays them out under `base`: `m1/`, `review/`, `spec/` and `site/`, each file under its live name."""
        from rebuild.review import build, subset_pack, unit_cache

        m1 = base / "m1"
        spec = base / "spec"
        return cls(
            audit=m1 / build.M1_AUDIT.name,
            m1=m1,
            subset_pack=m1 / subset_pack.PACK_NAME,
            after_font=m1 / build.M1_AFTER_FONT.name,
            manifest=base / "review" / "manifest.json",
            signatures=base / "review" / unit_cache.SIGNATURE_STORE_NAME,
            spec_root=spec,
            ledger=spec / build.M1_LEDGER.relative_to(build.REPO_ROOT),
            before_font=base / "site" / build.SITE_BEFORE_FONT.name,
            junior_font=base / "site" / build.SITE_JUNIOR_FONT.name,
        )

    def to_json(self) -> str:
        return json.dumps({field.name: str(getattr(self, field.name)) for field in fields(self)})

    @classmethod
    def from_json(cls, text: str) -> CorpusInputs:
        return cls(**{name: Path(value) for name, value in json.loads(text).items()})


class ThroughPlan(Exception):
    """Raised by a load line's mark reporter at the plan's mark, which stops the build there."""


def report_mark(label: str, counts: Mapping[str, int], *, through_plan: bool) -> None:
    """Print one `[m]` line: the mark's label, this process's resident set and footprint, and the mark's counts. On a load line the plan's mark raises `ThroughPlan` once printed."""
    reading = {"resident": peak_rss.resident_bytes(), "footprint": peak_rss.footprint_bytes(), **counts}
    fields_text = " ".join(f"{key}={'-' if value is None else value}" for key, value in reading.items())
    print(f"{MARK}{label}\t{fields_text}", file=sys.stderr, flush=True)
    if through_plan and label == PLAN_MARK:
        raise ThroughPlan


def substitute_for_load(build: ModuleType, unit_cache: ModuleType) -> None:
    """Make the three substitutions both load lines build under: `build.LETTERS` extended to the moved code points, each family's content key copied to its tagged name, and the signature resolution and the ink-duplicate merge skipped, since no moved window has a signature."""
    letters = build.LETTERS
    keys = unit_cache.family_content_keys

    def family_content_keys(
        repo_root: Path, spec: ResolvedSpec, after_font: Path
    ) -> tuple[dict[str, str], str]:
        found, helpers = keys(repo_root, spec, after_font)
        return {**found, **{f"{name}x": key for name, key in found.items()}}, helpers

    setattr(build, "LETTERS", {**letters, **{value + MOVED: f"{name}x" for value, name in letters.items()}})
    setattr(unit_cache, "family_content_keys", family_content_keys)
    setattr(build, "_resolve_signature_digests", lambda *arguments: ({}, {}, None, 0, 1))
    setattr(build, "merge_ink_duplicate_units", lambda *arguments: None)


# The stamp of the settle memo files a replay line's absorbs lay out and never write.
REPLAY_STAMP = "memory-series-replay"


def replay_main(argv: Sequence[str]) -> int:
    """Run one replay line's full replay (`run_m1.replay_and_absorb`) over the cut pickled at `--spec`, whose dump the crate reads from `--spec-dump`, with its tables under `--out`, over `--configs` at `--width`, every crate's cache stats on and every absorb hashing its file and discarding it. `--built` marks the kernel built (`kernel_exec._BUILT`), which the series passes once its own build has succeeded, so the replay runs no `cargo build` inside the line; without it the replay builds as run_m1's does. Print what it did as one JSON line on stdout, its last: each walk's counts or error, its crate's lines, peaks and CPU seconds, and its absorb worker's pid, and each absorb's peaks, CPU seconds, rows, seconds, warning and file digest."""
    parser = argparse.ArgumentParser(prog="python -m rebuild.tools.memory_series_child replay")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--spec-dump", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--configs", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument(
        "--built", action="store_true", help="the series' own `kernel_exec.ensure_built()` has succeeded"
    )
    args = parser.parse_args(argv)
    from rebuild.pipeline import conform, kernel_exec, run_m1

    if args.built:
        kernel_exec._BUILT = True

    with args.spec.open("rb") as handle:
        spec = pickle.load(handle)
    memos = {
        config: conform.SettleMemoFile(args.out / f"settle-memo-{config}.bin", REPLAY_STAMP, {})
        for config in args.configs.split(",")
    }
    replayed = run_m1.replay_and_absorb(
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
    parser = argparse.ArgumentParser(prog="python -m rebuild.tools.memory_series_child")
    parser.add_argument(
        "--inputs", required=True, help="the line's inputs, as `CorpusInputs.to_json` writes them"
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--signature-jobs", type=int, required=True)
    parser.add_argument("--write-jobs", type=int, required=True)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args(argv)
    from rebuild.review import build, unit_cache
    from rebuild.tools import cycle_timings

    inputs = CorpusInputs.from_json(args.inputs)
    cycle_timings.JOURNAL = args.journal
    memory_tally.report_marks(functools.partial(report_mark, through_plan=args.load))
    if args.load:
        substitute_for_load(build, unit_cache)
    try:
        build.build_m1(
            args.out,
            audit_path=args.audit,
            ledger_path=inputs.ledger,
            subset_dir=inputs.m1,
            before_font=inputs.before_font,
            after_font=inputs.after_font,
            junior_font=inputs.junior_font,
            jobs=args.jobs,
            signature_jobs=args.signature_jobs,
            spec_root=inputs.spec_root,
            subset_pack=inputs.subset_pack,
            write_jobs=args.write_jobs,
        )
    except ThroughPlan:
        pass
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(
        f"{RUSAGE}cpu_s={own.ru_utime + own.ru_stime:.3f} peak_resident={peak_rss.maxrss_to_bytes(own.ru_maxrss)} "
        f"children_cpu_s={children.ru_utime + children.ru_stime:.3f} "
        f"children_peak_resident={peak_rss.maxrss_to_bytes(children.ru_maxrss)}",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
