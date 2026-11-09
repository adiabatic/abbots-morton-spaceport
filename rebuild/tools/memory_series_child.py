"""The memory series' children (`rebuild/tools/memory_series.py`): one corpus line's build ("The corpus step"), one replay line's full replay ("The replay step"), or one absorbs line's absorb ("The absorbs step").

A corpus child runs `build.build_m1` as a library over the line's inputs at the stated widths. It points the timings journal at the line's `--journal`, installs the mark reporter (`memory_tally.report_marks`), which prints each mark with this process's resident set and footprint (`[m]`) and on a load line (`--load`) stops the build at the plan's mark, makes a load line's substitutions (`substitute_for_load`), and prints this process's own rusage and its reaped children's (`[r]`) before it exits.

A pack child (`pack` before its flags) runs `subset_pack.write_pack` over the tables under `--tables`, each a FIFO the series fills for every open, for `--configs`, into `--pack`, after linking the pack's temporary name to the null device, where `ftruncate` and `pwrite` both succeed on macOS, so it writes nothing; it prints a mark (`[m]`) as it starts the pack (`pack started`) and once the pack is renamed into place (`pack`), beside the one the pack's parent makes between the passes (`pack surveyed`), and its rusage line (`[r]`) before it exits (`pack_main`).

A replay child (`replay` before its flags) runs `run_m1.replay_and_absorb` over the line's cut, as a full replay of run_m1 runs it, at the stated width, with each crate's cache stats on and each absorb hashing its finished file and then discarding it, so the line writes no settle memo; it prints what the replay did as one JSON line (`replay_main`).

An absorb child (`absorb`) reads its job from stdin, maps the parts' shared memory segments as the pools' absorb worker maps them, runs `conform.absorb_settle_memo`, writing its part into the line's own clone of the standing memo or hashing the base it would write, or the carry-and-append absorb its bytes are proven against (`carry_and_append_absorb`), which hashes the file it would write instead of writing it, and prints what it did as one JSON line (`absorb_main`).

The child is a module of its own, whose top-level imports are the standard library and modules the build imports itself, because multiprocessing's spawn imports the launching process's main module into every worker the build starts. Run as this module, the build's parent and each of its units, write and signature workers hold the build's modules and this one beside them, where a cycle's build, run as `python -m rebuild.review.build`, holds the build's modules alone; a pack child's pack processes hold `subset_pack` and this module, where a cycle's hold the build's modules; a replay's absorb workers likewise hold run_m1's modules and this one, where run_m1's own hold run_m1's alone. The series' own imports, the table step's among them, reach none of them.

Run as: python -m rebuild.tools.memory_series_child --inputs JSON --audit FIFO --out DIR --journal FILE --jobs N --signature-jobs N --write-jobs N [--load], which the series does once a corpus line, python -m rebuild.tools.memory_series_child pack --tables DIR --configs a,b --pack FILE, once a cut line, python -m rebuild.tools.memory_series_child replay --spec PICKLE --spec-dump JSON --out DIR --configs a,b --width N [--built], once a replay line, or python -m rebuild.tools.memory_series_child absorb with its job on stdin, twice an absorbs line.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
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
PACK_STARTED_MARK = "pack started"
PACK_MARK = "pack"
# The digest a pack child's header records for every table: the cut tables reach it through FIFOs, which hashing would consume, and its pack goes to the null device.
PACK_CHILD_DIGEST = "0" * 64


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


def print_rusage() -> None:
    """Print this process's own rusage and its reaped children's (`[r]`): its CPU seconds and resident peak, and its children's CPU seconds and largest resident peak."""
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(
        f"{RUSAGE}cpu_s={own.ru_utime + own.ru_stime:.3f} peak_resident={peak_rss.maxrss_to_bytes(own.ru_maxrss)} "
        f"children_cpu_s={children.ru_utime + children.ru_stime:.3f} "
        f"children_peak_resident={peak_rss.maxrss_to_bytes(children.ru_maxrss)}",
        file=sys.stderr,
        flush=True,
    )


def pack_main(argv: Sequence[str]) -> int:
    """Run one cut line's pack child: link the temporary name `subset_pack.write_pack` stages `--pack` under to the null device, mark `pack started`, write the pack over the tables under `--tables` for `--configs`, each a FIFO the series fills for every open, mark `pack` once it is renamed into place, remove the link it leaves at `--pack`, and print the rusage line. The header records `PACK_CHILD_DIGEST` for every table."""
    parser = argparse.ArgumentParser(prog="python -m rebuild.tools.memory_series_child pack")
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--configs", required=True)
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args(argv)
    from rebuild.review import subset_pack

    configs = args.configs.split(",")
    memory_tally.report_marks(functools.partial(report_mark, through_plan=False))
    temp = args.pack.with_name(f"{args.pack.name}.tmp-{os.getpid()}")
    temp.symlink_to(os.devnull)
    try:
        memory_tally.mark(PACK_STARTED_MARK)
        subset_pack.write_pack(args.tables, configs, dict.fromkeys(configs, PACK_CHILD_DIGEST), args.pack)
        memory_tally.mark(PACK_MARK)
    finally:
        for path in (temp, args.pack):
            if path.is_symlink():
                path.unlink()
    print_rusage()
    return 0


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


def carry_and_append_absorb(memo, spec, parts, sweep=None):
    """The carry-and-append absorb `conform.absorb_settle_memo`'s bytes are proven against, for the absorbs holder's comparison line, over the parts' blocks as `absorb_settle_memo` reads them: every part's blocks decoded and held at once, the standing memo loaded unrestricted, every part of it, the rows `sweep` keeps marked, the kept rows carried into new columns in part order (`conform._MemoStore.carry_into`), each part's blocks appended with their ids shifted past the labels and records before them, and the file finished in memory as `conform._write_settle_memo` lays it out, with the standing index when the memo is one part, no row dropped and none is dead. Its file is the materialized form `absorb_settle_memo` leaves (`conform.materialize_settle_memo`). Return the finished file (`conform.FinishedSettleMemo`), or None where nothing would be written."""
    import zlib
    from array import array

    from rebuild.pipeline import conform

    read = []
    for part in parts:
        blocks = []
        for at in range(0, len(part.blocks), 2):
            new_labels, new_items, count = pickle.loads(zlib.decompress(part.blocks[at]))
            flat = array("I")
            flat.frombytes(zlib.decompress(part.blocks[at + 1]))
            blocks.append(
                (
                    new_labels,
                    new_items,
                    [flat[slot * count : (slot + 1) * count] for slot in range(6)],
                    flat[6 * count :],
                )
            )
        read.append(blocks)
        if part.release is not None:
            part.release()
    introduced_labels = sum(len(block[0]) for blocks in read for block in blocks)
    introduced_outcomes = sum(len(block[1]) for blocks in read for block in blocks)
    store = conform._MemoStore()
    store.load(memo, spec, None, lambda item: (item, "", ""))
    dropped = 0
    if sweep is not None and sweep.prune:
        flags = zlib.decompress(sweep.reached)
        if store.identity() == sweep.identity and len(flags) == len(store.reached):
            if sweep.discard:
                dropped = len(store)
            else:
                store.reached[:] = flags
                dropped = len(store) - store.reached_count()
    if not parts and not dropped:
        store.close()
        return None
    labels: list = []
    outcomes: list = []
    columns = [array(conform._memo_typecode(len(store.labels) + introduced_labels)) for _ in range(6)]
    values = array(conform._memo_typecode(len(store.outcomes) + introduced_outcomes))
    store.carry_into(labels, outcomes, columns, values, reached=True if dropped else None)
    carried = len(values)
    standing = (
        (store.index_copy(), carried, len(labels), len(outcomes))
        if not dropped and store.dead.count(1) == 0 and len(store.parts) <= 1
        else None
    )
    store.close()
    for blocks in read:
        base_labels, base_outcomes = len(labels), len(outcomes)
        for new_labels, new_items, block_columns, block_values in blocks:
            labels.extend(new_labels)
            outcomes.extend(new_items)
            for column, block in zip(columns, block_columns):
                column.extend(map(base_labels.__add__, block))
            values.extend(map(base_outcomes.__add__, block_values))
    del read
    return conform._settled_memo(memo, labels, outcomes, columns, values, standing)


def absorb_main() -> int:
    """Run one absorbs line's absorb (`memory_series`' "The absorbs step") over the job pickled on stdin: `writer`, `absorb` for `conform.absorb_settle_memo` or `carry` for the carry-and-append absorb (`carry_and_append_absorb`); `memo`, the settle memo; `spec`; `parts`, each a shared memory segment's name and lengths (`oracle.Shipped`) and its fresh windows' counts, as the oracle's and the sweep's pools hand an absorb its parts; `sweep`, the sweep's prune or None; and `write`. It maps each segment and unlinks its name at once, as the pools' absorb worker does (`run_m1_gates._absorb_memo`), and runs the absorb: with `write`, over the line's own clone of the standing memo, writing its segment, or its base, and the head there as the pools' absorbs write theirs; otherwise with the finished file hashed in order instead of written. It prints, as one JSON line on stdout, its last, the absorb's seconds, the hashed file's row count, size and sha256 (none when the absorb wrote its part), and for `absorb` the absorb's path, row counts and what it wrote. The carry-and-append absorb writes nothing."""
    import time
    from dataclasses import asdict, replace

    from rebuild.pipeline import conform, oracle

    job = pickle.load(sys.stdin.buffer)
    mapped = []
    parts = []
    for shipped, counts in job["parts"]:
        if shipped is None:
            parts.append(counts)
            continue
        segment = oracle._Mapped(shipped)
        mapped.append(segment)
        parts.append(replace(counts, blocks=segment.pieces, release=segment.close))
    hashed: list = []

    def emit(finished) -> bool:
        hashed.append((finished.rows, *finished.digest()))
        return True

    started = time.perf_counter()
    try:
        if job["writer"] == "absorb":
            absorbed = conform.absorb_settle_memo(
                job["memo"], job["spec"], parts, job["sweep"], None if job.get("write") else emit
            )
            stats = {**asdict(absorbed), "dropped": absorbed.dropped}
        else:
            finished = carry_and_append_absorb(job["memo"], job["spec"], parts, job["sweep"])
            if finished is not None:
                emit(finished)
            del finished
            stats = None
    finally:
        for segment in mapped:
            segment.close()
    seconds = time.perf_counter() - started
    rows, size, sha256 = hashed[0] if hashed else (None, None, None)
    print(
        json.dumps(
            {"seconds": round(seconds, 3), "rows": rows, "size": size, "sha256": sha256, "absorb": stats}
        ),
        flush=True,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["pack"]:
        return pack_main(argv[1:])
    if argv[:1] == ["replay"]:
        return replay_main(argv[1:])
    if argv[:1] == ["absorb"]:
        return absorb_main()
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
    print_rusage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
