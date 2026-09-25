"""The M1 integration driver: runs the full pipeline over the real rune files and writes the `doc/rebuild-design.md` §8 artifacts under rebuild/out/m1/.

First, `build_tables` builds the decision and treaty tables for every settlement configuration (`conform.SETTLEMENT_CONFIGS`) in one kernel-crate process. It enumerates and folds `default` first and each other configuration as a delta over `default`'s memo. As it folds each configuration it checks first-match-wins against the rows, writes the TSVs, writes one certificate per rule built from the rows' producer chains, and writes the window enumeration stamped with the fingerprint of its sources. `--conform-only` takes its glyph inventory from that enumeration and stops with an error when it is stale or missing. The payloads are packed on a background pool once their heads are read.

Two branches then run over the tables. The table-only branch (`_run_table_gates`, on one background thread) runs the string replay (`run_replay_strings`, recorded in `replay_summary.json`), which walks every configuration's stored rules over the string universe against the crate's own settlement, then the witness stage (`run_rule_witnesses`, recorded in `witness_summary.json`), which settles every certificate and checks that its rule fires. Beside those two it runs the shipped-order walk (`run_emitted_order`), where each configuration's walk waits for that configuration's pack. The glyph chain (`_run_glyph_chain`, on the calling thread) mints the glyphs (settled cells named by their cell labels, the raw cmap glyphs, the marker, chokepoint and ss10 twins, and the namer dot pair), runs the defect gates under the reviewed allow-list, emits GSUB and GPOS (also writing `behavior_classes.json`, the arming key `rebuild/tools/deep_sweep.py` reads), compiles the font, and runs read-back (rebuild/pipeline/readback.py). Read-back re-parses the written font, checks it against the emitters' plan, and checks the GSUB's uint16 subtable-offset headroom against its floor in the same parse.

`main` then runs the Manual-pin gate and the oracle. The oracle starts once the string replay has returned, beside the witness stage, and writes to the settle memo files only after the witness stage's writes are on disk. `main` joins the table-only branch after the oracle, before it decides the run_m1 gate. The join raises the first failure in serial order (the packing, the replay, the witnesses, the shipped order), and any of those is raised in place of a glyph-chain failure, so a failing build reports what a serial build would.

Settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set use the same names. The raw cmap glyph for each rune is the bare rune name, drawn as the isolated cell with no curs anchors. Marker, chokepoint, and ss10 twins reuse that drawing. Under ss10 the pre-empt lookup replaces every letter's cmap glyph with its anchor-free `.ss10` twin before formation, so no ligature forms, nothing settles, each letter keeps its own cluster, and every seam is a break. That is why the overlay configuration (`conform.OVERLAY_CONFIGS`) has no table: read-back checks that the pre-empt covers every letter cmap glyph and that the twins appear in no other stage, the belt (gate:conform's exhaustive HarfBuzz sweep) sweeps the overlay at `conform.OVERLAY_HORIZON`, and the oracle compares its rows against the bare stream, using the twins' `hmtx` advances for positions.

The split-buffer check runs inside gate:conform's belt, at horizon 4 on every build and at horizon 5 or deeper through `make conform-deep`. Read-back's boundary-glyphs stage checks the ZWNJ glyph's zero advance and empty outline on the written font bytes, so the belt does not check them at every shaped slot.

Run as `uv run python -m rebuild.pipeline.run_m1`. `--conform-only` runs only the belt against the M1.otf on disk. `--gates-only` re-runs the defect gate, the Manual-pin gate and the oracle over the tables and font already on disk, without rebuilding anything. It is the fast way to re-check an edit to a comparison-side input: the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, or the oracle's own code (the classifier and ledger match in rebuild/pipeline/oracle.py, the position channel in rebuild/pipeline/oracle_positions.py). The tables' stamp leaves all of these out (`fingerprint.table_code_paths`; rebuild/test_build_code_closure.py checks that the build never imports either module). A `--gates-only` pass records run_m1's green when a prior green exists and everything that changed since is comparison-side, so the artifact cycle takes this route itself and the pass after it skips run_m1.

Both routes serve what they can from the per-row verdict stores that rebuild/pipeline/oracle_cache.py keeps beside the tables. A ledger edit changes no family key and no stamp line, so every row and position verdict is served and only the ledger match and the audit run. An alias edit changes the family keys of the families it names, so only the rows that reach them are re-derived. An edit to the kern sidecar or to the position channel's module keeps the row verdicts and re-shapes the positions, and a classifier edit serves both. `--fresh-oracle-cache` ignores the stores, and `--gates-only` may read them but never writes one.
"""

from __future__ import annotations

import argparse
import functools
import gc
import gzip
import hashlib
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import time
import zlib
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Mapping, NoReturn, Sequence

import yaml

from rebuild.pipeline import (
    baseline_subset,
    compile_font,
    conform,
    defects,
    emit_gpos,
    emit_gsub,
    fingerprint,
    geometry,
    kernel_exec,
    kernel_io,
    manual_pins,
    oracle,
    oracle_cache,
    readback,
    surface,
    witness,
)
from rebuild.pipeline import table as table_module
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    locked_glyph_name,
    relevant_marker_features,
    ss10_twin_name,
)
from rebuild.pipeline.settle import FormationGuard, cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable
from rebuild.tools import console
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, CheckVerdict, record_check, record_pool
from rebuild.tools.memory_budget import describe_fit, usable_cores
from rebuild.tools.peak_rss import peak_rss_self_bytes, process_peak_rss_bytes, rss_token

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
PUNCTUATION_YAML = REPO_ROOT / "glyph_data" / "punctuation.yaml"
CONTACT_ALLOW_YAML = REPO_ROOT / "rebuild" / "m1-contact-allow.yaml"
ALIAS_YAML = REPO_ROOT / "rebuild" / "m1-aliases.yaml"
DIVERGENCES_YAML = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
KERN_SIDECAR_YAML = REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml"

RAW_STANCE = "cmap"


def _spawn_pool(jobs: int, units: int) -> ProcessPoolExecutor:
    """Return a spawn pool of at most `jobs` workers and at most `units`, the number of tasks the caller will submit. The belt submits one task per acceptance configuration, and the oracle one per row range."""
    workers = max(1, min(jobs, units))
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


MEMO_STAMP_FORMAT = "ams-m1-memo-stamp/1"
# The keys of a rune's dump that are prose the engine never reads: a record's or an unlock's `why`, and the rune's notes and ductus. `rune_content_digests` drops them so rewording a rationale invalidates no memo, as every other digest in this tree ignores prose.
PROSE_KEYS = frozenset({"why", "notes", "ductus"})


def _without_prose(payload):
    if isinstance(payload, dict):
        return {key: _without_prose(value) for key, value in payload.items() if key not in PROSE_KEYS}
    if isinstance(payload, list):
        return [_without_prose(value) for value in payload]
    return payload


def rune_content_digests(spec: ResolvedSpec) -> dict[str, str]:
    """Return each modeled rune's SHA-256 over its resolved content: the rune's part of the spec dump the crate reads (`kernel_io.rune_payload`), without `PROSE_KEYS`. It hashes the resolved rune instead of the file because the engine reads the resolved rune: a cross-file `against:` target's content is copied into the record that names it, and a ligature registration rewrites every left condition its trailing component reaches. So the digest changes when what the engine reads of the rune changes, and the memo needs no `spec_load.rune_closure` pass over it. It works for any spec, a fixture's included, so the memo stamp is one function of the spec in hand."""
    return {
        name: hashlib.sha256(
            json.dumps(
                _without_prose(kernel_io.rune_payload(rune)), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        for name, rune in spec.runes.items()
    }


def memo_structure_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the whole-store half of the memo stamp: `locality_lines` hashed, with the predicate classes' membership left out of the structure line. The memo invalidates entries by class through the crate's read journal instead of discarding the whole store, so classes are recorded separately (`class_memberships`). The alphabet and its ligature sequences, the resolved rune-local groups, the registry, the code and the crate, and the semantics tokens stay in."""
    lines = [
        (f"structure\t{_classless_structure_digest(spec)}" if line.startswith("structure\t") else line)
        for line in locality_lines(spec, root)
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _classless_structure_digest(spec: ResolvedSpec) -> str:
    """`spec_load.spec_structure_digest` less the predicate classes: the alphabet with its ligature sequences and the resolved rune-local groups."""
    payload = {
        "runes": {name: list(rune.sequence) if rune.sequence else None for name, rune in spec.runes.items()},
        "groups": {
            name: {group: sorted(members) for group, members in rune.policy.groups.items()}
            for name, rune in spec.runes.items()
            if rune.policy.groups
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def class_memberships(spec: ResolvedSpec) -> dict[str, list[str]]:
    """Return every registry predicate class's sorted membership, as the memo stamp records it and `memo_edited` compares it."""
    return {name: sorted(members) for name, members in spec.registry.predicate_classes.items()}


def memo_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the stamp a build's memo files carry in their heads and the next build checks them against: `memo_structure_stamp`, every rune's `rune_content_digests` entry, and every predicate class's membership (`class_memberships`), as compact sorted JSON on one line, the form the crate writes into its head line verbatim. `memo_edited` reads it."""
    return json.dumps(
        {
            "format": MEMO_STAMP_FORMAT,
            "structure": memo_structure_stamp(spec, root),
            "runes": rune_content_digests(spec),
            "classes": class_memberships(spec),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class MemoDelta:
    """The runes whose content changed and the predicate classes whose membership changed between two memo stamps, each sorted."""

    runes: tuple[str, ...]
    classes: tuple[str, ...]


def memo_edited(previous: str, current: str) -> MemoDelta | None:
    """Return what a memo written under stamp `previous` cannot be trusted for under `current`, or None when it cannot be read at all. None means a different stamp format, a changed structure (the crate, the build-side code, the registry, the alphabet and its ligatures, the groups, the semantics tokens), or a recorded rune this spec no longer models. A rune new to the spec counts as edited, although a new rune also changes the structure stamp, which rejects the memo first. A class present in only one stamp counts as changed. An empty delta means every window in the memo is still valid."""
    try:
        before = json.loads(previous)
        after = json.loads(current)
    except ValueError:
        return None
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if before.get("format") != after.get("format") or before.get("structure") != after.get("structure"):
        return None
    recorded = before.get("runes")
    runes = after.get("runes")
    if not isinstance(recorded, dict) or not isinstance(runes, dict) or recorded.keys() - runes.keys():
        return None
    recorded_classes = before.get("classes")
    classes = after.get("classes")
    if not isinstance(recorded_classes, dict) or not isinstance(classes, dict):
        return None
    return MemoDelta(
        runes=tuple(sorted(name for name, digest in runes.items() if recorded.get(name) != digest)),
        classes=tuple(
            sorted(
                name
                for name in recorded_classes.keys() | classes.keys()
                if recorded_classes.get(name) != classes.get(name)
            )
        ),
    )


@dataclass(frozen=True)
class MemoSeed:
    """The previous build's memo files that a table build reads before settling a window itself: the directory they were unpacked into, and the runes and predicate classes that no memo may be trusted for."""

    directory: Path
    edited: tuple[str, ...]
    moved_classes: tuple[str, ...]


def memo_seed(
    out_dir: Path, stamp: str, scratch: Path, configs: Sequence[str] = conform.SETTLEMENT_CONFIGS
) -> MemoSeed | None:
    """Unpack into `scratch` the previous build's memos under `out_dir` that this build may read, or return None when there are none. Each named configuration's packed memo is read for its head, checked against this process's world and against `stamp` through `memo_edited`, and unpacked for the crate only if both pass. The edited runes are the union over every memo unpacked, so a rune that one memo cannot be trusted for is served by no memo. Only the memos of `configs` are read, because the crate reads only the memos of the configurations it builds; a narrowed build therefore unpacks only its own configurations' memos, and a memo that narrowed builds never rewrite cannot add every rune changed since the last whole build to the union. A memo whose head, stamp, or gzip stream fails is left in place and not read, and this build's own memo replaces it."""
    world = "+".join(kernel_exec.enumeration_tokens()) or "pinned"
    edited: set[str] = set()
    moved_classes: set[str] = set()
    unpacked = False
    for config in configs:
        packed = kernel_exec.memo_path(out_dir, config)
        head = kernel_exec.read_memo_head(packed)
        if head is None or head.config != config or head.world != world:
            continue
        moved = memo_edited(head.stamp, stamp)
        if moved is None:
            continue
        scratch.mkdir(parents=True, exist_ok=True)
        plain_path = scratch / f"memo-{config}.tsv"
        try:
            with gzip.open(packed, "rb") as source, plain_path.open("wb") as plain:
                shutil.copyfileobj(source, plain, length=1 << 20)
        except OSError, EOFError, zlib.error:
            plain_path.unlink(missing_ok=True)
            continue
        edited.update(moved.runes)
        moved_classes.update(moved.classes)
        unpacked = True
    if not unpacked:
        return None
    return MemoSeed(
        directory=scratch, edited=tuple(sorted(edited)), moved_classes=tuple(sorted(moved_classes))
    )


def _table_build_threads(kernel_threads: int | None) -> int:
    """Return the table build's width: `kernel_threads`, or the memory-derived `kernel_exec.KERNEL_THREADS_DEFAULT`, capped at the settlement configuration count and the cores this process may run on. The string replay uses `_replay_threads` instead."""
    return max(
        1,
        min(
            kernel_threads or kernel_exec.KERNEL_THREADS_DEFAULT,
            len(conform.SETTLEMENT_CONFIGS),
            usable_cores(),
        ),
    )


def _replay_threads(replay_threads: int | None) -> int:
    """Return the string replay's width: `replay_threads`, or `kernel_exec.replay_threads_default()` (the machine's memory divided by `REPLAY_PEAK_BYTES`, with nothing co-resident subtracted), capped at the settlement configuration count and the cores this process may run on, the same three terms as `_table_build_threads`. The replay does not reuse the table build's width because the two stages have different memory costs. The build's width subtracts `default`'s live memo and divides by a delta's peak during enumeration, while each replay engine holds a fraction of a delta and is built after the build process has exited. Where the build's width is below the configuration count, reusing it would split the replay into two rounds, and the oracle waits for the replay (`settle_memo_wait`). `AMS_REPLAY_THREADS` and `--replay-threads` set this width; `AMS_KERNEL_THREADS` and `--kernel-threads` do not affect it. The caps only narrow a width and never widen one."""
    return max(
        1,
        min(
            replay_threads or kernel_exec.replay_threads_default(),
            len(conform.SETTLEMENT_CONFIGS),
            usable_cores(),
        ),
    )


def _core_bound_threads(count: int) -> int:
    """Return the width of a per-configuration pool whose tasks are CPU-bound and hold nothing the memory-derived widths account for: one task per settlement configuration, capped at the cores this process may run on, and at least one. It sizes the window packers and the shipped-order walks. A packer holds a zlib stream and `_pack_windows`'s copy buffer: `_pack_windows` over the plain `default` enumeration from a `ThreadPoolExecutor` measured 1.26s, 1.22s and 1.30s per task at widths 1, 3 and 5, with maxrss 0.044, 0.054 and 0.062 GB, so it scales flat and widening it costs nothing. A shipped-order walk is one single-threaded crate process that holds the rules and labels and streams the rows, 0.105 GB of child RSS, a small fraction of the table build's peak. `AMS_KERNEL_THREADS` and `--kernel-threads` do not affect these pools: that setting exists to keep the table build out of swap, so a build set to width one narrows the crate's delta round while the packing and the walks still run every configuration at once. The string replay's setting (`_replay_threads`) affects neither pool."""
    return max(1, min(count, usable_cores()))


class Packing:
    """One table build's window and memo packing, on a thread pool that can outlive `build_tables`. Each configuration's pack is a future keyed by the configuration's name, so a stage that reads a packed enumeration (`run_emitted_order`'s walks) waits for its own configuration through `wait` instead of for the whole build. `build_tables` opens and closes its own `Packing` when the caller passes none, which blocks until packing finishes. `run` passes one, and the table-only branch closes it after the last walk that reads a packed file. `close` waits for every pack, shuts the pool down, reports `[t] pack_windows_total` (from the first submit to the last pack's completion, since the pool stays open across the kernel run and the walks), and raises the first pack failure."""

    def __init__(self, threads: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="pack-windows")
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()
        self._done = 0
        self._started: float | None = None
        self._finished: float | None = None
        self._closed = False

    def submit(self, config: str, task: Callable[[], None], total: int) -> None:
        def packed() -> None:
            task()
            with self._lock:
                self._done += 1
                done = self._done
                self._finished = time.perf_counter()
            console.progress(done, total, "packed configurations")

        if self._started is None:
            self._started = time.perf_counter()
        self._futures[config] = self._pool.submit(packed)

    def wait(self, config: str) -> None:
        future = self._futures.get(config)
        if future is not None:
            future.result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: BaseException | None = None
        for future in self._futures.values():
            try:
                future.result()
            except BaseException as error:
                if failure is None:
                    failure = error
        self._pool.shutdown(wait=True)
        if self._started is not None and self._finished is not None:
            console.timing("pack_windows_total", self._finished - self._started)
        if failure is not None:
            raise failure


def _pack_config(tables_dir: Path, config: str, keep_windows: bool) -> None:
    """Pack one configuration's files: the plain window payload into its `.gz` artifact when the build is stamped (`keep_windows`), otherwise delete it (its head has already been read), and the crate's memo file into `kernel_exec.memo_path`. Each plain file is removed inside the task once its pack is written, so a deferred pack never loses its own source."""
    started = time.perf_counter()
    payload = tables_dir / f"windows-{config}.tsv"
    if keep_windows:
        _pack_windows(payload, table_module.windows_path(tables_dir, config))
    payload.unlink()
    memo = tables_dir / f"memo-{config}.tsv"
    if memo.is_file():
        _pack_windows(memo, kernel_exec.memo_path(tables_dir, config))
        memo.unlink()
    console.timing(f"pack_windows[{config}]", time.perf_counter() - started)


def build_tables(
    spec: ResolvedSpec,
    out_dir: Path | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
    packing: Packing | None = None,
    configs: Sequence[str] = conform.SETTLEMENT_CONFIGS,
) -> tuple[dict[str, tuple], dict[str, str]]:
    """Build the decision and treaty tables for the named settlement configurations, all of them unless `configs` narrows the set. The resolved spec is dumped once, then one crate `build-tables` process (`kernel_exec.build_table_files`) enumerates `default`'s fixpoint and folds it, then enumerates each other configuration as a delta over `default`'s finished memo and folds it as it finishes. The crate writes the settlement TSV, the treaty TSV and the window enumeration itself; nothing is folded on the Python side.

    A narrowed set is for `rebuild/tools/scratch_build.py`, which searches for a record to change. The crate writes only the configurations it is asked for and deletes nothing, so a narrowed build into a directory another build wrote leaves that build's files for the other settlement configurations in place. `scratch_build.scratch_out_dir` keeps such builds out of `rebuild/out/m1`. A set that does not include `default` enumerates each member from scratch, since there is no finished memo to build a delta over. The build and the artifact cycle ask for the whole set. Overlay configurations get no tables. Any table files named for a configuration outside `conform.SETTLEMENT_CONFIGS`, an overlay configuration's or one that has left the set, are removed first (`stale_table_files`), so a whole-set build leaves only its own tables in the directory.

    Per configuration, Python reads the enumeration's head back for the rules, the reachable cells and the fired provenance every downstream stage needs, parses the treaty TSV back for the defect gates, and packs the plain window payload into its `.gz` artifact. The head reads run on a thread pool at the table build's width once the crate has exited, and this call waits for them. The packing (`_pack_config`, memo file included) runs on a `Packing` pool at `_core_bound_threads` width, one packer per configuration up to the cores, whatever the crate's width, because a packer holds only a zlib stream and a copy buffer and the compressor releases the interpreter lock. When `packing` is passed, the tables are returned as soon as the heads are read, and the caller waits on each pack through `Packing.wait` and closes the pool. Without it, packing finishes before the return.

    A build with an `out_dir` also reuses its trace memos across builds. `memo_seed` reads the previous build's `memo-<config>.tsv.gz` files for the configurations this build names, unpacks those whose stamp still matches, and names the runes whose content changed as edited, so a window naming no edited rune settles as it did last time. This build's own memos are packed under the same names, stamped with `memo_stamp` over the spec in hand. A caller with no `out_dir` reads and writes no memo.

    `out_dir`, when given, receives the TSVs listed in `doc/rebuild-design.md` §8. The second returned mapping is each configuration's `table.table_digest` as the crate reported it, computed while the window rows are still in memory, which avoids recomputing the fixpoint. The crate also prints it on stdout, where `rebuild/tools/scaling_sweep.py` reads it. Both returned mappings are built in `configs` order however the configurations finish, so completion order cannot affect an artifact.

    `inputs` is `tables_inputs` over the sources this spec was loaded from. Passing it with `out_dir` keeps each configuration's window enumeration beside the TSVs under the stamp that names those sources, where `run_font_conformance` reads it instead of rebuilding anything. Without it, the payload is deleted once its head is read. A caller building its own spec must omit it, because the fingerprint names the repository's rune files and does not describe tables built from other runes.

    `kernel_threads` is how many delta configurations are enumerated at once beside `default`, capped at the configuration count and the cores this process may run on, neither of which is a memory limit. The default it falls back to is the memory limit: `kernel_exec.KERNEL_THREADS_DEFAULT` is this machine's memory, less what `default`'s finished memo holds, divided by what one delta holds at its peak. So the cap only narrows a memory-derived width and never widens one.
    """
    configs = tuple(configs)
    threads = _table_build_threads(kernel_threads)
    kernel_exec.ensure_built()
    built: dict[str, tuple] = {}
    digests: dict[str, str] = {}
    deferred = packing is not None
    packers = packing if packing is not None else Packing(_core_bound_threads(len(configs)))
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        tables_dir = directory / "tables" if out_dir is None else out_dir
        tables_dir.mkdir(parents=True, exist_ok=True)
        for path in stale_table_files(tables_dir):
            path.unlink(missing_ok=True)

        stamp = memo_stamp(spec) if out_dir is not None else None
        seed = None
        if out_dir is not None and stamp is not None:
            start = time.perf_counter()
            seed = memo_seed(out_dir, stamp, directory / "seed", configs)
            console.timing(
                "memo_seed",
                time.perf_counter() - start,
                f"edited={','.join(seed.edited) if seed else '-'} classes={','.join(seed.moved_classes) if seed else '-'}",
            )
        start = time.perf_counter()
        digests = kernel_exec.build_table_files(
            spec_path,
            tables_dir,
            list(configs),
            inputs=inputs if inputs is not None else kernel_exec.UNSTAMPED_WINDOWS,
            threads=threads,
            timings=True,
            seed=seed.directory if seed else None,
            edited=seed.edited if seed else (),
            moved_classes=seed.moved_classes if seed else (),
            memo_stamp=stamp,
        )
        console.timing("kernel_build_tables", time.perf_counter() - start)

        def read_one(config: str) -> tuple[str, tuple]:
            payload = tables_dir / f"windows-{config}.tsv"
            with payload.open("rt", encoding="utf-8") as handle:
                _stamp, decision = table_module.read_windows(handle, windows=False)
            treaty = table_module.read_treaty_tsv(tables_dir / f"treaties-{config}.tsv")
            if out_dir is None:
                payload.unlink()
            else:
                packers.submit(
                    config,
                    functools.partial(_pack_config, tables_dir, config, inputs is not None),
                    len(configs),
                )
            return config, (decision, treaty)

        try:
            with ThreadPoolExecutor(max_workers=threads) as heads:
                for finished in as_completed([heads.submit(read_one, config) for config in configs]):
                    config, tables = finished.result()
                    built[config] = tables
                    console.progress(len(built), len(configs), "configurations")
        except BaseException:
            if not deferred:
                with suppress(Exception):
                    packers.close()
            raise
        if not deferred:
            packers.close()
    return {config: built[config] for config in configs}, {config: digests[config] for config in configs}


def config_table_files(tables_dir: Path, config: str) -> tuple[Path, ...]:
    """Return the three table files a settlement configuration writes under `tables_dir`, named for `config`."""
    return (
        tables_dir / f"settlement-{config}.tsv",
        tables_dir / f"treaties-{config}.tsv",
        table_module.windows_path(tables_dir, config),
    )


def stale_table_files(tables_dir: Path) -> list[Path]:
    """Return the table files under `tables_dir` named for a configuration outside `conform.SETTLEMENT_CONFIGS`: an overlay configuration, which settles nothing, or one that has left the set since an earlier build wrote its tables. `build_tables` deletes them, so a table never outlives the configuration that produced it or stands in for one that settles nothing."""
    tables_dir = Path(tables_dir)
    configs = {
        path.name.removeprefix(prefix).removesuffix(suffix)
        for prefix, suffix in (("settlement-", ".tsv"), ("treaties-", ".tsv"), ("windows-", ".tsv.gz"))
        for path in tables_dir.glob(f"{prefix}*{suffix}")
    }
    return [
        path
        for config in sorted(configs - set(conform.SETTLEMENT_CONFIGS))
        for path in config_table_files(tables_dir, config)
        if path.exists()
    ]


def _pack_windows(payload: Path, path: Path) -> None:
    """Gzip the plain window enumeration the kernel wrote into the artifact at `path`. The gzip timestamp is zeroed so two builds of one table are byte-identical. Level 1 is used because the payload is gigabytes of plain TSV per build, written once and read back a few times, and compression time is spent on every build that writes an `out_dir`. The level affects only time, since table identity is taken over the decompressed bytes. The memo files are packed the same way. `rebuild/kernel-rs/src/artifacts.rs` also states that the decompressed bytes are the artifact's identity."""
    with (
        payload.open("rb") as source,
        path.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as packed,
    ):
        shutil.copyfileobj(source, packed, length=1 << 20)


def mint_cell_glyphs(
    spec: ResolvedSpec, tables: Mapping[str, DecisionTable | tuple[DecisionTable, ...]]
) -> dict[CellId, GlyphRecord]:
    cells: set[CellId] = set()
    for entry in tables.values():
        decision = entry[0] if isinstance(entry, (tuple, list)) else entry
        cells.update(cell for cell in decision.reachable_cells() if cell.rune in spec.runes)
    glyphs: dict[CellId, GlyphRecord] = {}
    for cell in sorted(cells, key=lambda c: cell_label(spec, c)):
        plan = surface.resolve_cell(spec, cell)
        name = cell_label(spec, cell)
        if len(name.encode()) > geometry.MAX_GLYPH_NAME_BYTES:
            raise RuntimeError(f"cell label {name!r} exceeds {geometry.MAX_GLYPH_NAME_BYTES} bytes")
        glyphs[cell] = geometry.realize(spec, plan, name=name)
    return glyphs


def mint_raw_glyphs(
    spec: ResolvedSpec,
) -> tuple[dict[CellId, GlyphRecord], dict[CellId, GlyphRecord], dict[str, str]]:
    """Return the bare cmap glyphs, the marker, chokepoint and ss10 twins, and the map from raw name to ss10 twin name for the ss10 pre-empt lookup. Raw glyphs are keyed under the synthetic `RAW_STANCE` so they never collide with a reachable settled cell that happens to be the isolated cell. Only letter runes with a code point get ss10 twins: ligature runes never appear in a cmap buffer, and boundary tokens are not runes. Only runes with an entry get chokepoint (`locked`) twins."""
    bare: dict[CellId, GlyphRecord] = {}
    twins: dict[CellId, GlyphRecord] = {}
    ss10_twins: dict[str, str] = {}
    for rune_name, rune in spec.runes.items():
        isolated = geometry.isolated_cell(spec, rune_name)
        record = geometry.realize(spec, surface.resolve_cell(spec, isolated), name=rune_name)
        stripped = replace(record, entry=None, exit=None, entry_curs_only=None, safety_checks=())
        key = CellId(rune_name, RAW_STANCE, None, None, ())
        bare[key] = stripped

        if not rune.sequence and rune.codepoint is not None:
            twin_name = ss10_twin_name(rune_name)
            twins[CellId(rune_name, RAW_STANCE, None, None, ("ss10",))] = replace(stripped, name=twin_name)
            ss10_twins[rune_name] = twin_name

        live_names = [rune_name]
        for marker_name in emit_gsub.marker_states(rune_name, relevant_marker_features(rune)):
            twins[CellId(marker_name, RAW_STANCE, None, None, ())] = replace(stripped, name=marker_name)
            live_names.append(marker_name)
        if any(stance.surface.entries for stance in rune.stances.values()):
            for raw_name in live_names:
                twin_name = locked_glyph_name(raw_name)
                twins[CellId(rune_name, RAW_STANCE, None, None, ("locked", raw_name))] = replace(
                    stripped, name=twin_name
                )
    return bare, twins, ss10_twins


def namer_dot_glyphs() -> dict[CellId, GlyphRecord]:
    raw = yaml.safe_load(PUNCTUATION_YAML.read_text())["glyphs"]
    records: dict[CellId, GlyphRecord] = {}
    for name in ("periodcentered", "periodcentered.lowered"):
        definition = raw[f"{name}.prop"]
        records[CellId(name, RAW_STANCE, None, None, ())] = GlyphRecord(
            name=name,
            bitmap=tuple(definition["bitmap"]),
            y_offset=definition.get("y_offset", 0),
        )
    return records


def _run_defect_gates(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    cell_glyphs: Mapping[CellId, GlyphRecord],
) -> defects.DefectReport:
    """Run the `doc/rebuild-design.md` §9 defect gates (`defects.run_gates`) over one build's tables and minted glyphs, under the reviewed allow-list. The build and `--gates-only` both call it; `--gates-only` runs it over the tables and glyphs on disk. The allow-list is in no stamp and no fingerprint component, so blessing a signature re-runs this gate without a rebuild."""
    allow = frozenset(entry["signature"] for entry in yaml.safe_load(CONTACT_ALLOW_YAML.read_text()) or ())
    return defects.run_gates(spec, tables, cell_glyphs, allow=allow)


def _defect_summary_fields(report: defects.DefectReport) -> dict:
    """Return the `pipeline_summary.json` fields the defect gate owns, in the form `run` writes them. A `--gates-only` pass rewrites only these fields in the summary a build left."""
    return {
        "defect_errors": [f"{d.code} {d.signature}: {d.message}" for d in report.errors],
        "defect_flags": [f"{d.code} {d.signature}: {d.message}" for d in report.flags],
        "dead_in_alphabet": sorted(report.dead_in_alphabet),
        "deferred_partner": sorted(report.deferred_partner),
        "notes": report.notes,
    }


@dataclass
class _TableGateState:
    """What the table-only branch leaves for `TableGates`. `replay_ready` is set once the string replay has returned, with every settle memo file it fills on disk, and `memo_ready` once the witness stage's settle memo writes are on disk too. Both are also set as soon as the replay-then-witness sequence fails. The `*_red` fields hold the exception each stage would have raised in a serial run. The summaries reach their readers as the JSON files each stage writes."""

    replay_ready: threading.Event = field(default_factory=threading.Event)
    memo_ready: threading.Event = field(default_factory=threading.Event)
    pack_red: BaseException | None = None
    chain_red: BaseException | None = None
    emitted_red: BaseException | None = None

    def release(self) -> None:
        self.replay_ready.set()
        self.memo_ready.set()


def _emitted_order_stage(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    packing: Packing,
) -> None:
    console.phase("emitted_order")
    start = time.perf_counter()
    emitted = run_emitted_order(spec, tables, out_dir, ready=packing.wait)
    console.timing("emitted_order", time.perf_counter() - start)
    if not emitted["pass"]:
        raise SystemExit(
            f"the shipped settlement order answers a row differently from its table: {emitted['complaint']}"
        )


def _run_table_gates(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    inputs: str | None,
    packing: Packing,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
    replay_threads: int | None,
    state: _TableGateState,
) -> None:
    """Run the table-only branch on one background thread beside the glyph chain. The string replay runs first, at its own width (`replay_threads`, or the width `_replay_threads` derives from `kernel_exec.REPLAY_PEAK_BYTES`), and the witness stage runs after it. The order matters: a whole-universe replay writes each configuration's settle memo file whole (`conform.absorb_replay_memo`), and the witness stage then loads the rows of that file its certificates can ask for, writes the windows it settled fresh as a part, and merges the part into the file before it returns. `state.replay_ready` is set once the replay has returned, and the oracle starts after it. `state.memo_ready` is set once the witness stage's files are on disk, and the oracle's own memo writes wait for it. Both are set as soon as that sequence fails, and `TableGates` also sets them when this thread ends any other way, so a wait on either never hangs.

    When `inputs` is given, the shipped-order walks run on a thread of their own beside that sequence, one walk per configuration up to the cores (`_core_bound_threads`, from the configuration count and the cores, not from the build's or the oracle's width), each waiting for its own configuration's pack (`Packing.wait`). They are not narrowed to the cores the oracle's pool leaves: that pool uses the whole machine, so the walks would run one at a time on the critical path. What remains of them shares the machine with the pool's first seconds, the overlap `doc/parallelism.md` describes. The packing is closed here after the last walk. Nothing is raised from this thread: each stage's exception is stored in `state`, and `TableGates` raises the first one in serial order.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="emitted-order") as walks:
        emitted = (
            walks.submit(_emitted_order_stage, spec, tables, out_dir, packing) if inputs is not None else None
        )
        try:
            console.phase("replay_strings")
            start = time.perf_counter()
            replay = run_replay_strings(
                spec, out_dir, inputs, replay_threads=replay_threads, memo_inputs=memo_inputs
            )
            walked = "whole universe" if replay["families"] is None else f"{len(replay['families'])} families"
            console.timing("replay_strings", time.perf_counter() - start, rss_token(process_peak_rss_bytes()))
            console.say(f"replay_strings: horizon {replay['horizon']}, {walked}")
            if not replay["pass"]:
                raise SystemExit(f"the string replay found the tables incomplete: {replay['complaint']}")
            state.replay_ready.set()

            console.phase("rule_witnesses")
            start = time.perf_counter()
            witness_summary = run_rule_witnesses(spec, tables, out_dir, memo_inputs)
            console.timing("rule_witnesses", time.perf_counter() - start)
            if not witness_summary["pass"]:
                raise conform.WitnessError(
                    f"{len(witness_summary['failures'])} rule(s) whose certificate does not fire them; see {out_dir / 'witness_summary.json'}"
                )
        except BaseException as error:
            state.chain_red = error
        finally:
            state.release()
        if emitted is not None:
            try:
                emitted.result()
            except BaseException as error:
                state.emitted_red = error
    try:
        packing.close()
    except BaseException as error:
        state.pack_red = error


class TableGates:
    """The handle `run` returns for its table-only branch, with the two points where the oracle waits for it. `wait_for_replay` is called before the oracle starts: it blocks until the string replay has returned (every settle memo file the replay fills is on disk, and the replay crate has exited), reports the wait as `[t] settle_memo_wait`, and raises the replay's failure, or the witness stage's if that stage has already failed. `wait_for_memo` is called before the oracle writes: it blocks until the witness stage's settle memos are on disk too, reports the wait as `[t] witness_memo_wait`, and raises either failure.

    The witness stage reads a configuration's file and writes it back whole with its own windows added. An oracle write between that read and that write would be lost, and an oracle write based on a read made before the stage's write would drop the stage's windows. So the oracle writes every window it settles as a part and merges the parts into the files only after `wait_for_memo` (`run_oracle`'s `memo_ready`). The witness stage is then the only writer while the two overlap, and every oracle write includes what the stage wrote.

    `join` waits for the whole branch and raises the first failure in serial order (the packing, the replay, the witnesses, the shipped order). `first_red` returns that failure instead, for the paths where the glyph chain has also failed and the branch's failure is raised in its place. Call `close` in a `finally`, so no thread or pool outlives the run on an interrupt.
    """

    def __init__(
        self, packing: Packing, state: _TableGateState, pool: ThreadPoolExecutor, future: Future[None]
    ) -> None:
        self._packing = packing
        self._state = state
        self._pool = pool
        self._future = future
        future.add_done_callback(lambda _: state.release())

    def wait_for_replay(self) -> None:
        self._wait(self._state.replay_ready, "settle_memo_wait")

    def wait_for_memo(self) -> None:
        self._wait(self._state.memo_ready, "witness_memo_wait")

    def _wait(self, ready: threading.Event, label: str) -> None:
        if not ready.is_set():
            start = time.perf_counter()
            ready.wait()
            console.timing(label, time.perf_counter() - start)
        if self._state.chain_red is not None:
            raise self._state.chain_red
        if self._future.done():
            self._future.result()

    def first_red(self) -> BaseException | None:
        try:
            self._future.result()
        except BaseException as error:
            return error
        state = self._state
        return next(
            (red for red in (state.pack_red, state.chain_red, state.emitted_red) if red is not None), None
        )

    def join(self) -> None:
        red = self.first_red()
        if red is not None:
            raise red

    def close(self) -> None:
        with suppress(Exception):
            self._future.result()
        self._pool.shutdown(wait=True)
        with suppress(Exception):
            self._packing.close()


def run_ligature_outgoing(spec: ResolvedSpec, out_dir: Path = OUT_DIR) -> dict:
    """Verify authored outgoing mappings over the loaded spec and keep the result with this build's tables."""
    from rebuild.pipeline.ligature_outgoing_check import validate_ligature_outgoing
    from rebuild.pipeline.spec_load import DEFAULT_RUNES_DIR

    console.phase("ligature_outgoing")
    start = time.perf_counter()
    rune_raws = {
        path.stem: yaml.safe_load(path.read_text()) for path in sorted(DEFAULT_RUNES_DIR.glob("*.yaml"))
    }
    summary = validate_ligature_outgoing(spec, rune_raws)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ligature_outgoing_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    console.timing("ligature_outgoing", time.perf_counter() - start)
    return summary


def run(
    out_dir: Path = OUT_DIR,
    spec: ResolvedSpec | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
    memo_inputs: oracle_cache.SettleMemoInputs | None = None,
    replay_threads: int | None = None,
) -> tuple[dict, TableGates]:
    """Build the tables, then run two branches over them. The table-only branch (`_run_table_gates`, on one background thread) runs the string replay, the witness stage and the shipped-order walks. The glyph chain (minting, the defect gates, the emission, the compile and read-back) runs on the calling thread, writes `pipeline_summary.json` and the Stage A record, and returns its summary with the branch's `TableGates` handle without joining the branch. The caller joins it: `main` calls `wait_for_replay` before the oracle, passes `wait_for_memo` for the oracle to call before its memo writes, calls `join` after the oracle so the gate covers both branches, and calls `close` in a `finally`. When the glyph chain raises, the branch's first failure is raised in its place if there is one, so a failed replay is reported as incomplete tables and not as whatever the glyph chain made of them.

    `inputs` is `tables_inputs` over the sources `spec` was loaded from, computed before the load so it can only name content the tables are at least as new as. Passing it keeps the window enumeration under `out_dir` for the conformance sweep and the shipped-order walk. A caller running its own spec omits it, and the walk does not run. `memo_inputs` is `settle_memo_inputs`, computed at the same moment. It names the settle memo files the string replay fills and the witness stage, the oracle and the belt then load; without it the replay writes no memo, the witness stage settles every certificate, and nothing is shared. `kernel_threads` applies only to the table build, whose per-configuration memory cost that width was derived from. `replay_threads` applies only to the string replay, whose memory cost is `kernel_exec.REPLAY_PEAK_BYTES` and which derives its own width (`_replay_threads`) when none is given. The packing and the shipped-order walks run one task per configuration up to the cores (`_core_bound_threads`) regardless of either width. The witness stage and the walks can still be running when the oracle's pool starts, since the oracle waits only for the string replay (`TableGates.wait_for_replay`), and they share the machine with that pool.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    console.phase("spec_load")
    start = time.perf_counter()
    if spec is None:
        spec = load_default_spec()
    console.timing("spec_load", time.perf_counter() - start)

    console.phase("build_tables_total")
    start = time.perf_counter()
    packing = Packing(_core_bound_threads(len(conform.SETTLEMENT_CONFIGS)))
    try:
        tables, _digests = build_tables(
            spec, out_dir, inputs=inputs, kernel_threads=kernel_threads, packing=packing
        )
    except BaseException:
        with suppress(Exception):
            packing.close()
        raise
    console.timing("build_tables_total", time.perf_counter() - start, rss_token(process_peak_rss_bytes()))

    state = _TableGateState()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="table-gates")
    gates = TableGates(
        packing,
        state,
        pool,
        pool.submit(
            _run_table_gates,
            spec,
            tables,
            out_dir,
            inputs,
            packing,
            memo_inputs,
            replay_threads,
            state,
        ),
    )
    try:
        summary = _run_glyph_chain(spec, tables, out_dir)
    except BaseException as error:
        red = gates.first_red() if isinstance(error, (Exception, SystemExit)) else None
        gates.close()
        if red is not None:
            raise red
        raise
    return summary, gates


def _run_glyph_chain(spec: ResolvedSpec, tables: Mapping[str, tuple], out_dir: Path) -> dict:
    """Run the glyph chain over one build's tables: minting, the defect gates, the feature emission, the compile and read-back, then write `pipeline_summary.json` and the Stage A record. It reads the spec and the tables and writes nothing the table-only branch reads."""
    console.phase("glyph_minting")
    start = time.perf_counter()
    cell_glyphs = mint_cell_glyphs(spec, tables)
    bare, twins, ss10_twins = mint_raw_glyphs(spec)
    dots = namer_dot_glyphs()
    console.timing("glyph_minting", time.perf_counter() - start)

    console.phase("defect_gates")
    start = time.perf_counter()
    defect_report = _run_defect_gates(spec, tables, cell_glyphs)
    console.timing("defect_gates", time.perf_counter() - start)

    console.phase("emit_gsub_gpos")
    start = time.perf_counter()
    curs_glyphs = {**cell_glyphs, **bare, **twins}
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_twins=ss10_twins)
    classes = emit_gsub.behavior_classes(gsub_plan)
    (out_dir / "behavior_classes.json").write_text(
        json.dumps(
            {"format": emit_gsub.BEHAVIOR_CLASSES_FORMAT, "classes": list(classes)},
            indent=2,
        )
        + "\n"
    )
    gpos_fea = emit_gpos.emit_gpos(curs_glyphs, spec=spec)
    fea = gsub_plan.fea_text + "\n" + gpos_fea
    console.timing("emit_gsub_gpos", time.perf_counter() - start)

    console.phase("compile_font")
    start = time.perf_counter()
    all_glyphs = {**curs_glyphs, **dots}
    font_path = compile_font.build_mini_font(all_glyphs, fea, out_dir / "M1.otf")
    console.timing("compile_font", time.perf_counter() - start)
    (out_dir / "M1.generated.fea").write_text(fea)

    console.phase("readback")
    start = time.perf_counter()
    readback_report = readback.verify_font(
        font_path, gsub_plan, emit_gpos.cursive_registrations(curs_glyphs, spec=spec)
    )
    (out_dir / "readback_summary.json").write_text(json.dumps(readback_report, indent=2) + "\n")
    console.timing("readback", time.perf_counter() - start)
    if not readback_report["pass"]:
        raise readback.ReadbackError(
            f"{readback_report['divergence_count']} read-back divergence(s) between the compiled font and the plan; see {out_dir / 'readback_summary.json'}"
        )

    summary = {
        "configs": list(tables),
        "rules_per_config": {config: len(decision.rules) for config, (decision, _treaty) in tables.items()},
        "settled_cell_glyphs": len(cell_glyphs),
        "total_glyphs": len(all_glyphs),
        "gsub_rule_count": gsub_plan.rule_count,
        **_defect_summary_fields(defect_report),
        "font": str(font_path),
    }
    (out_dir / "pipeline_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fingerprint.write_stage_a(REPO_ROOT, out_dir)
    return summary


# The belt's horizon (`conform.BELT_HORIZON`), not one more. The walk's cost is its count of distinct raw windows, and every first-position window of a length-N text is distinct: the alphabet size to the Nth per configuration, each a full settlement, since the engine's trace memo is keyed on the raw slots. So each extra letter of depth multiplies the walk by the alphabet size. Over the whole alphabet, a whole-universe walk at horizon 5 exceeded `kernel_exec.TIMEOUT`, and even narrowed to one family it would cost more than the belt run it lets the cycle skip. Checking past this depth is the job of the periodic deep sweep (`make conform-deep`), which settles every text it shapes.
REPLAY_HORIZON = conform.BELT_HORIZON
REPLAY_FORMAT = "ams-m1-replay/1"
REPLAY_SUMMARY = "replay_summary.json"
RUNE_LABEL_PREFIX = "glyph_data/runes/"


def locality_lines(spec: ResolvedSpec, root: Path = REPO_ROOT) -> list[str]:
    """Return the whole-store half of the window-locality key (`doc/rebuild-design.md` §10) as lines: the non-rune data the tables' stamp covers (the script registry, the schema, the punctuation), the build side of the pipeline code and the crate, the engine's semantics tokens, the cross-rune structure `spec_load.spec_structure_digest` covers (the alphabet and its ligature sequences, the predicate classes' membership, the resolved rune-local groups), and the capability features. While these are unchanged, a window's result depends only on the runes it names. The string replay's stamp and the memo stamp are both built from these lines."""
    from rebuild.pipeline import spec_load

    lines = [line for line in fingerprint.table_data_lines(root) if not line.startswith(RUNE_LABEL_PREFIX)]
    lines += fingerprint.path_lines(root, fingerprint.table_code_paths(root))
    lines.append("semantics\t" + "+".join(kernel_exec.enumeration_tokens()))
    lines.append(f"structure\t{spec_load.spec_structure_digest(spec)}")
    lines.append("capabilities\t" + ",".join(spec_load.capability_features(spec)))
    return lines


def locality_structure_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the hash of `locality_lines`."""
    return hashlib.sha256("\n".join(locality_lines(spec, root)).encode()).hexdigest()


def replay_structure_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the hash of everything the string replay's result depends on apart from the rune files: `locality_lines` and the horizon. While it matches the last passing record's, a build walks only the texts naming a rune whose digest changed, closed under `spec_load.rune_closure`. When it differs, the build walks the whole universe."""
    lines = [*locality_lines(spec, root), f"horizon\t{REPLAY_HORIZON}"]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def replay_families(
    spec: ResolvedSpec, previous: Mapping | None, structure: str, runes: Mapping[str, str]
) -> list[str] | None:
    """Return which runes' texts this build's replay must walk, given the last passing replay record, this build's structure stamp and its per-rune digests: None for the whole universe, an empty list when nothing changed, and otherwise the runes whose digest changed plus every rune whose records read one of them (`spec_load.rune_closure`), sorted. The whole universe is walked when the record is missing, is not a passing record of this format, was walked under another structure stamp, or names a rune this spec no longer models. A narrowed walk is valid only by induction: it needs a passing whole-universe walk as its base and an unchanged structure stamp at every step since."""
    from rebuild.pipeline import spec_load

    if (
        previous is None
        or previous.get("format") != REPLAY_FORMAT
        or not previous.get("pass")
        or previous.get("structure") != structure
    ):
        return None
    recorded = previous.get("runes")
    if not isinstance(recorded, dict) or recorded.keys() - runes.keys():
        return None
    moved = {name for name, digest in runes.items() if recorded.get(name) != digest}
    if not moved:
        return []
    closure = spec_load.rune_closure(spec)
    edited = {name for name, reads in closure.items() if reads & moved} | (moved & spec.runes.keys())
    return sorted(edited)


def read_replay_record(out_dir: Path) -> dict | None:
    """The last replay's record under `out_dir`, or None when there is none or it will not parse."""
    try:
        record = json.loads((out_dir / REPLAY_SUMMARY).read_text())
    except OSError, ValueError:
        return None
    return record if isinstance(record, dict) else None


def run_replay_strings(
    spec: ResolvedSpec,
    out_dir: Path,
    inputs: str | None,
    replay_threads: int | None = None,
    memo_inputs: oracle_cache.SettleMemoInputs | None = None,
    configs: Sequence[str] = conform.SETTLEMENT_CONFIGS,
) -> dict:
    """Run the enumeration-completeness check every build runs right after its tables are written: the crate's `replay-strings` subcommand (`rebuild/kernel-rs/src/replay.rs`) over the named configurations' settlement TSVs under `out_dir`, walking the string universe to `REPLAY_HORIZON` and checking each window's first-match rule outcome against the engine's own settlement. `configs` is the whole settlement set unless an unstamped caller narrowed its tables the same way. A narrowed walk with `inputs` raises `ValueError` before it starts, because the record it would write is read back as a passing whole-universe base for configurations it never walked.

    On a rune edit only part of the universe is walked. `replay_families` reads the last passing record beside the tables, and while `replay_structure_stamp` matches, only the texts naming a changed rune, or a rune whose records read one, are walked. A build with no passing record or a changed structure walks everything, and a build where nothing changed walks nothing and carries the record forward. A caller with no stamp (its own spec, whose rune files are not the repository's) walks the whole universe and writes no record.

    The walk also produces the settle memo. With `memo_inputs` (`settle_memo_inputs`, computed before the spec was loaded), every whole-universe walk asks the crate to write its window memo for each walked configuration beside the tables (`kernel_exec.replay_memo_dump`) and absorbs each one into the configuration's `conform.SettleMemoFile` under the stamp and family keys `conform.settle_memo_files` computes. So the witness stage, the oracle and the belt load what the replay settled instead of settling it again. Only the walked configurations' files are touched, so a narrowed walk never dumps, reads, absorbs or deletes a configuration it did not walk. Every dump is deleted in this function whatever the walk or the absorb did, and a failed absorb is a warning, not a failure, since every reader settles what the file lacks. This can widen the walk beyond what the structure stamp requires: the memo stamp covers comparison-side modules the replay's own stamp does not, so when any configuration's file is missing or fails `conform.settle_memo_standing`, the whole universe is walked to refill it. A narrowed walk (a rune edit) writes no memo and leaves the existing files to drop their own stale entries.

    The record written beside the tables is what the next build's walk is narrowed against, so it carries the structure stamp and every rune digest as well as the counts. It is written whether the walk passed or failed: a disagreement is recorded with the crate's message, and `_run_table_gates` raises it as `SystemExit`. The width is `_replay_threads`: `replay_threads` when given, else the machine's memory divided by `kernel_exec.REPLAY_PEAK_BYTES` (what one configuration's walk holds: the trace memo over the windows its texts reach, plus the window memo's inverse label map and block buffer), capped at the configuration count and the cores, so the whole universe replays in one round on both fleet machines. The absorbs run after the crate has exited, one spawn process per walked configuration at that same width. Each holds one configuration's dump, columns and probe index while it builds the file, a fraction of what `REPLAY_PEAK_BYTES` allows for one configuration, so they use the replay's width and have no constant of their own.
    """
    configs = tuple(configs)
    if inputs is not None and set(configs) != set(conform.SETTLEMENT_CONFIGS):
        raise ValueError(
            f"a narrowed replay ({', '.join(configs)}) cannot record: the record beside the tables is read back as a green whole-universe base for every settlement configuration"
        )
    threads = _replay_threads(replay_threads)
    recordable = inputs is not None
    structure = replay_structure_stamp(spec) if recordable else None
    runes = fingerprint.rune_digests(REPO_ROOT) if recordable else {}
    families = (
        replay_families(spec, read_replay_record(out_dir), structure, runes)
        if recordable and structure is not None
        else None
    )
    memos = {
        config: memo
        for config, memo in conform.settle_memo_files(out_dir, spec, memo_inputs).items()
        if config in configs
    }
    for config in memos:
        with suppress(FileNotFoundError):
            kernel_exec.replay_memo_dump(out_dir, config).unlink()
    if memos and not all(conform.settle_memo_standing(memo) for memo in memos.values()):
        families = None
    emitting = families is None and bool(memos)
    summary: dict = {
        "format": REPLAY_FORMAT,
        "horizon": REPLAY_HORIZON,
        "families": families,
        "walked": families is None or bool(families),
        "configs": {},
        "structure": structure,
        "runes": runes,
        "pass": True,
        "complaint": None,
    }
    try:
        if families is None or families:
            try:
                summary["configs"] = kernel_exec.replay_strings(
                    spec,
                    out_dir,
                    configs,
                    horizon=REPLAY_HORIZON,
                    families=families,
                    threads=threads,
                    timings=True,
                    memo_dir=out_dir if emitting else None,
                )
            except kernel_exec.ReplayDisagreement as error:
                summary["pass"] = False
                summary["complaint"] = str(error)
        if emitting and summary["pass"]:
            with _spawn_pool(threads, len(memos)) as pool:
                absorbs = {
                    config: pool.submit(_absorb_replay_memo, out_dir, config, memo, spec)
                    for config, memo in memos.items()
                }
                for config, future in absorbs.items():
                    entries, seconds, complaint = future.result()
                    if complaint is not None:
                        console.warn(complaint)
                    else:
                        console.timing(f"settle_memo_emit {config}", seconds, f"entries={entries}")
    finally:
        for config in memos:
            with suppress(FileNotFoundError):
                kernel_exec.replay_memo_dump(out_dir, config).unlink()
    if recordable:
        (out_dir / REPLAY_SUMMARY).write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _absorb_replay_memo(
    out_dir: Path, config: str, memo: conform.SettleMemoFile, spec: ResolvedSpec
) -> tuple[int | None, float, str | None]:
    """Absorb one configuration's window memo into its settle memo file, in a pool worker. Return the row count and the seconds the absorb took, which the parent reports as `[t] settle_memo_emit <config>`, or else a warning to print instead. A dump the crate never wrote, or one `conform.absorb_replay_memo` rejects, is only a warning, since the readers settle what the file lacks."""
    dump = kernel_exec.replay_memo_dump(out_dir, config)
    started = time.perf_counter()
    if not dump.is_file():
        return None, 0.0, f"settle memo: the replay filed no window memo for {config} at {dump}"
    try:
        entries = conform.absorb_replay_memo(dump, memo, spec, config)
    except (kernel_exec.KernelRunError, OSError) as error:
        return None, 0.0, f"settle memo: {dump} not absorbed ({error}); the readers settle instead"
    return entries, time.perf_counter() - started, None


def run_rule_witnesses(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> dict:
    """Run the witness stage: settle every configuration's certificates through the crate and check that each rule fires on its own certificate (`witness.check_rule_certificates`). This is the reachability half of the checks that fail the build on a rule that can never fire: the crate's fold fails on a rule no replayed row first-matches, and this fails on a rule whose replayed row no string reaches, which is what a wrong pin in the worklist would produce. It runs here, on the tables the build just folded, because the certificates describe those tables; `--gates-only` reuses tables this stage already passed.

    Each configuration's walk shares the settle memo that the string replay fills and the oracle and the belt load (`conform.settle_memo_files`, keyed per family from `memo_inputs`, as the oracle row cache is), so a window any of them has settled since the runes it names last changed is settled once. After a whole-universe replay, this stage serves every certificate's windows from the file the replay just wrote and settles only what a narrowed replay left out. The certificates ask for a small part of the file, so the walk loads only the rows its certificate texts can ask for (`conform._SettledWindowWalk.load_only_asked_by`). It writes the windows it settled fresh as a part in a scratch directory (`SettleMemoFile.write_path`, the same form each row range of the pooled oracle uses), and this stage merges the part into the shared file (`conform.absorb_settle_memo_parts`) inside the configuration's timed span. The file then holds every existing window plus the fresh ones, and a walk that settled nothing writes no part and leaves the file unchanged. Because the key is per family, a rune edit invalidates only the memo entries naming an edited family, so only the certificates naming one are settled again. A caller with its own spec has no memo inputs and no memo, and settles everything.

    The summary is written to `witness_summary.json`; a failure is raised at the join, ahead of any glyph-chain failure. The stage runs after the string replay on the table-only branch (`_run_table_gates`), so it can load the file the replay wrote. The oracle runs beside it and may map the file before or after this stage's merge, but it writes every window it settles as a part and merges its parts only after this stage has returned (`TableGates.wait_for_memo`). So no oracle write can produce a file without this stage's windows or be overwritten by this stage's.
    """
    guard_verdicts = kernel_exec.guard_sweep(spec)
    memos = conform.settle_memo_files(out_dir, spec, memo_inputs)
    per_config: dict[str, dict] = {}
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        for config, entry in tables.items():
            started = time.perf_counter()
            decision = entry[0] if isinstance(entry, (tuple, list)) else entry
            memo = memos.get(config)
            part = Path(scratch) / f"{config}.gz"
            report = witness.check_rule_certificates(
                spec,
                features_for_config(config),
                decision,
                guard_verdicts,
                memo=None if memo is None else replace(memo, write_path=part),
            )
            if memo is not None:
                conform.absorb_settle_memo_parts(memo, [part], spec)
            per_config[config] = {
                "rules": report.rules,
                "witnessed": len(report.witnessed),
                "failures": list(report.failures),
                "served_windows": report.served,
                "unasked_windows": report.unasked,
                "fresh_windows": report.fresh,
            }
            failures.extend(report.failures)
            console.timing(
                f"rule_witnesses[{config}]",
                time.perf_counter() - started,
                f"rules={report.rules} witnessed={len(report.witnessed)} served={report.served} unasked={report.unasked} fresh={report.fresh}",
            )
    summary = {"pass": not failures, "configs": per_config, "failures": failures[:50]}
    (out_dir / "witness_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


EMITTED_ORDER_SUMMARY = "emitted_order_summary.json"


def run_emitted_order(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    ready: Callable[[str], None] | None = None,
) -> dict:
    """Run the shipped-order stage: replay the settlement order the emitter ships (every configuration's table folded into one lookup and sorted by `emit_gsub._ordered_settle_rules`), first match wins, against every row of every configuration's window enumeration. The crate's `replay-emitted` subcommand (`rebuild/kernel-rs/src/shipped_order.rs`) does this, one process per configuration over the packed enumeration the build just wrote under `out_dir`. Each row, renamed into the stream its configuration's marker lookups produce, must be matched by the first emitted rule of its input that admits it, with the row's own outcome. Where an emitted look class admits only part of the row's deep class, which a rule folded from another configuration's fiber partition can do while still giving every member the row's outcome, each member is checked separately. A row matched with a different outcome fails the build, naming the configuration, the row, the emitted rule that fired and the table's own rule.

    This is the only check of the shipped order that reads the tables. The fold's partition check, the string replay and the witness stage each walk a configuration's rules in that configuration's own order, and read-back compares the font with the plan, not the plan with the tables. Without this stage the shipped order would be checked only by the HarfBuzz belt, which is keyed on code and behavior classes and skips a rune edit that adds no new rule shape. So this runs on every build, on the tables the build just folded, and a rune edit that reorders the fold without adding a rule shape fails here instead of at the next code change. It costs O(rows) per configuration and settles nothing. Every configuration walks at once up to the cores (`_core_bound_threads`), each in one small single-threaded process that holds the rules and labels and no memo or engine, so the memory-derived width of the table build and the string replay does not apply to this pool. `ready`, when given, is called with the configuration's name on the walker thread before its enumeration is opened; the build passes `Packing.wait`, so each walk starts as soon as its own pack is written instead of after the whole build's.
    """
    from rebuild.pipeline import emit_gsub

    configs = [config for config in conform.SETTLEMENT_CONFIGS if config in tables]
    threads = _core_bound_threads(len(configs))
    summary: dict = {"pass": True, "configs": {}, "complaint": None}
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        order = directory / "emitted-order.tsv"
        order.write_text(emit_gsub.emitted_order_tsv(spec, tables))
        summary["rules"] = sum(1 for _ in order.read_text().splitlines()) - 2

        def walk_one(config: str) -> tuple[str, dict[str, int]]:
            started = time.perf_counter()
            entry = tables[config]
            decision = entry[0] if isinstance(entry, (tuple, list)) else entry
            context = directory / f"context-{config}.tsv"
            context.write_text(emit_gsub.emitted_context_tsv(spec, config, decision))
            if ready is not None:
                ready(config)
            answer = kernel_exec.replay_emitted(
                table_module.windows_path(out_dir, config),
                config=config,
                table=out_dir / f"settlement-{config}.tsv",
                order=order,
                context=context,
                timings=True,
            )
            console.timing(
                f"emitted_order[{config}]",
                time.perf_counter() - started,
                f"rows={answer['rows']} expanded={answer['expanded']}",
            )
            return config, answer

        with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="emitted-walk") as walkers:
            futures = [walkers.submit(walk_one, config) for config in configs]
            for finished in as_completed(futures):
                try:
                    config, answer = finished.result()
                except kernel_exec.EmittedOrderDisagreement as error:
                    summary["pass"] = False
                    summary["complaint"] = str(error)
                    for other in futures:
                        other.cancel()
                    break
                summary["configs"][config] = answer
    summary["configs"] = {
        config: summary["configs"][config] for config in configs if config in summary["configs"]
    }
    (out_dir / EMITTED_ORDER_SUMMARY).write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def serialized_tables(out_dir: Path, inputs: str) -> dict[str, DecisionTable] | None:
    """Return every settlement configuration's decision table as the build left it under `out_dir`, without the window rows, or None if any file is missing, unreadable, or was written from sources other than the ones `inputs` names. A partial set is never returned, since it would sweep some configurations against tables the runes on disk no longer produce."""
    tables: dict[str, DecisionTable] = {}
    for config in conform.SETTLEMENT_CONFIGS:
        try:
            stamp, decision = table_module.read_windows(
                table_module.windows_path(out_dir, config), windows=False
            )
        except OSError, ValueError:
            return None
        if stamp != inputs:
            return None
        tables[config] = decision
    return tables


def tables_inputs() -> str:
    """Return the stamp serialized windows carry: `fingerprint.tables_value` plus one token for each semantics mode that is on by default (`kernel_exec.enumeration_tokens`: the simulated prospect, the shifted vote slots, the class-grain deep slots). These environment flags change settlement semantics or enumeration grain without changing any hashed source, so without the tokens an enumeration made with a flag on would look current to a process with it off, and the reverse, and the sweep would replay tables the in-process kernel no longer produces.

    The stamp covers what the fixpoint reads, so its data half is `fingerprint.table_data_value`, not `fingerprint.data_value`. The alias map, the divergence ledger and the kern sidecar are the baseline oracle's comparison inputs, and the contact allow-list, which is in no fingerprint component, is the defect gate's. All of them are read against tables that are already built, so editing one leaves every enumeration on disk as current as it was, and `--gates-only` can re-check over the tables and font on disk. The same holds for the classifier in rebuild/pipeline/oracle.py and the position channel in rebuild/pipeline/oracle_positions.py: the stamp's code half is `fingerprint.table_code_paths`, the pipeline tree without those two modules, and rebuild/test_build_code_closure.py checks that the build never imports them. These edits are still checked: every one of those labels is in the artifact cycle's run_m1 key (`artifact_cycle.run_m1_skip_lines`), so the green record no longer matches and the gates they feed, the defect gate included, re-run over the artifacts on disk.
    """
    inputs = fingerprint.tables_value(REPO_ROOT)
    for token in kernel_exec.enumeration_tokens():
        inputs = f"{inputs}+{token}"
    return inputs


def settle_memo_inputs() -> oracle_cache.SettleMemoInputs:
    """Return the disk-derived half of the settle memo's keys (`oracle_cache.SettleMemoInputs`). Call it where `tables_inputs` is called, before `load_default_spec`, so a key can only name content the settlements are at least as new as. Every entry point that shares a memo between phases takes this beside the tables' stamp and passes both to `conform.settle_memo_files` once the spec is loaded."""
    return oracle_cache.settle_memo_inputs(REPO_ROOT)


def run_font_conformance(
    out_dir: Path = OUT_DIR,
    max_length: int = 4,
    jobs: int = 1,
    summary_name: str = "conform_summary.json",
) -> dict:
    """Run the exhaustive font-versus-settlement sweep: the per-edit belt at `max_length` 4 over every settlement configuration, the overlay configuration at `conform.OVERLAY_HORIZON` whatever `max_length` is, and the same sweep deeper when `rebuild.tools.deep_sweep` calls it with its own `summary_name`. The tables under `out_dir` are read only for the glyph inventory `mint_cell_glyphs` needs to name settled cells and read their anchors. The sweep itself takes no table, because it checks HarfBuzz's behavior against the kernel's, and read-back has already checked that the font holds the rules the build planned. A stamp mismatch stops with an error instead of rebuilding: the enumeration costs a whole kernel fan-out, and an inventory built here could describe runes that have since changed. The split-buffer check runs as part of this sweep, on every text that contains a splitter.

    The pooled path computes the `doc/rebuild-design.md` §5.7 formation-guard verdicts (`kernel_exec.guard_sweep`) once for the whole run and passes them with each submission. A spawned worker inherits nothing, so each would otherwise build the crate and sweep the spec itself. The serial path sweeps inside `run_conformance`.

    At the per-edit horizon, each configuration's walk shares its settle memo with the string replay that fills it and with the oracle's walk over the same texts, through a file under `out_dir` keyed per family as the oracle row cache is (`conform.settle_memo_files`, from `settle_memo_inputs` taken before the spec loads). The replay writes what it settled, each later phase loads it and writes back what it added, and a rune edit invalidates only the entries whose windows name an edited family. A deeper sweep shares nothing: its memo is a multiple of the belt's, and a file that size would cost the next belt and oracle workers more to decode than they save.

    A pooled belt at `conform.BELT_HORIZON` records every configuration's worker peak (`_priced_conformance_config`) as one observation of the `conform-belt` pool (`cycle_timings.record_pool`), which `make job-costs` reports. The serial path starts no pool, and a deeper sweep's worker has a different peak, so neither records one.
    """
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
    spec = load_default_spec()
    start = time.perf_counter()
    serialized = serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; the sweep no longer rebuilds the fixpoint in process"
        )
    decisions: Mapping[str, DecisionTable | tuple[DecisionTable, ...]] = serialized
    print(f"[t] load_tables {time.perf_counter() - start:.1f}s", flush=True)
    cell_glyphs = mint_cell_glyphs(spec, decisions)
    settle_memos = (
        conform.settle_memo_files(out_dir, spec, memo_inputs) if max_length == conform.BELT_HORIZON else {}
    )
    if jobs > 1:
        collected: dict[str, conform.ConformanceConfigResult] = {}
        worker_peaks: dict[str, int] = {}
        kernel_exec.ensure_built()
        guard_verdicts = kernel_exec.guard_sweep(spec)
        with _spawn_pool(jobs, len(conform.ACCEPTANCE_CONFIGS)) as pool:
            futures = {
                pool.submit(
                    _priced_conformance_config,
                    spec,
                    out_dir / "M1.otf",
                    config,
                    max_length,
                    cell_glyphs,
                    guard_verdicts,
                    settle_memos.get(config),
                ): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                result, peak = future.result()
                collected[result.config] = result
                worker_peaks[result.config] = peak
                console.progress(len(collected), len(conform.ACCEPTANCE_CONFIGS), "configurations")
        if max_length == conform.BELT_HORIZON:
            record_pool(
                "conform-belt",
                width=min(jobs, len(conform.ACCEPTANCE_CONFIGS)),
                worker_peaks=worker_peaks,
                controller_peak_bytes=peak_rss_self_bytes(),
            )
        ordered = [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
        report = conform.merge_conformance_results(out_dir / "M1.otf", ordered)
        report.write(out_dir / summary_name)
    else:
        report = conform.run_conformance(
            out_dir / "M1.otf",
            spec,
            glyphs=cell_glyphs,
            max_length=max_length,
            out_dir=out_dir,
            summary_name=summary_name,
            settle_memos=settle_memos,
        )
    summary = {
        "sequences": report.sequences,
        "shaping_runs": report.shaping_runs,
        "divergences": len(report.divergences),
        "pass": report.passed,
        "notes": report.notes,
    }
    for divergence in report.divergences[:20]:
        summary.setdefault("divergence_exemplars", []).append(
            f"{divergence.config} {':'.join(f'{ord(ch):04X}' for ch in divergence.text)} position {divergence.position} [{divergence.kind}] expected {divergence.expected} got {divergence.got}"
        )
    return summary


def run_manual_pin_gate(out_dir: Path = OUT_DIR, spec: ResolvedSpec | None = None) -> dict:
    if spec is None:
        spec = load_default_spec()
    report = manual_pins.run_gate(out_dir / "M1.otf", spec)
    summary = manual_pins.summarize(report)
    (out_dir / "manual_pins_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def manual_pin_gate_failure(summary: Mapping) -> str | None:
    """Return why the Manual-pin gate does not count as passed, or None. `pass` alone means no disagreements, which a gate that replayed nothing also satisfies, so this also requires at least one pin in scope and every pin in scope replayed against the font."""
    if not summary.get("pass"):
        return f"Manual-pin gate failed ({len(summary.get('disagreements') or [])} disagreements)"
    in_scope = summary.get("pins_in_scope") or 0
    replayed = summary.get("replayed") or 0
    if in_scope < 1:
        return "Manual-pin gate passed with no pins in scope, which proves nothing about the font"
    if replayed != in_scope:
        return f"Manual-pin gate replayed {replayed} of {in_scope} pins in scope"
    return None


def _promote_oracle_row_cache(
    spec: ResolvedSpec,
    out_dir: Path,
    scratch: Path,
    keys: Mapping[str, str],
    stamps: Mapping[str, oracle_cache.EnvironmentStamp],
    position_keys: Mapping[str, str] | None = None,
    position_stamp: oracle_cache.EnvironmentStamp | None = None,
) -> None:
    """Move this run's staged stores into place, but only if every key recomputed from the sources now matches what the run used: the row keys and stamps from the rune tree, and the position keys and stamp from the font and the kern sidecar. If the inputs changed during the run, its verdicts describe nothing on disk, and a store recorded under the wrong digest would be served as current indefinitely instead of failing once. So on any change nothing is written and a warning names the change, which costs the next pass an uncached oracle and nothing else. An alias map edited during the run into a form `alias_family_digests` rejects, or a font that cannot be digested again, is handled the same way."""
    try:
        keys_now, stamps_now = oracle_row_cache_keys(spec, out_dir)
        position_keys_now, position_stamp_now = oracle_position_keys(keys_now, out_dir)
    except (OSError, ValueError, yaml.YAMLError) as error:
        console.warn(f"oracle row cache: not written — its inputs would not re-read ({error})")
        return
    moved = oracle_cache.moved_note(dict(keys), keys_now)
    if moved is None:
        for config in conform.ACCEPTANCE_CONFIGS:
            moved = oracle_cache.moved_note(stamps[config].labels, stamps_now[config].labels)
            if moved is not None:
                moved = f"{config} {moved}"
                break
    if moved is None and position_keys is not None:
        moved = oracle_cache.moved_note(dict(position_keys), position_keys_now or {})
        if moved is None and position_stamp is not None:
            moved = oracle_cache.moved_note(
                position_stamp.labels, position_stamp_now.labels if position_stamp_now else {}
            )
        if moved is not None:
            moved = f"positions {moved}"
    if moved is not None:
        console.warn(
            f"oracle row cache: not written — its inputs moved while the oracle ran ({moved}), so nothing it derived describes what is on disk"
        )
        return
    promoted = oracle_cache.promote_stores(scratch, out_dir, conform.ACCEPTANCE_CONFIGS)
    if promoted:
        console.say(f"oracle row cache: written for {', '.join(promoted)}")
    else:
        console.warn("oracle row cache: not written — a configuration staged no store")


def oracle_row_cache_keys(
    spec: ResolvedSpec, out_dir: Path
) -> tuple[dict[str, str], dict[str, oracle_cache.EnvironmentStamp]]:
    """Return the oracle row cache's two keys from one read of their sources: a digest per rune family, and a whole-store stamp per acceptance configuration. They are computed once before the run and again at promotion, which is how the run detects that what it would record differs from what it built from."""
    keys = oracle_cache.family_keys(REPO_ROOT, spec, ALIAS_YAML)
    stamps = {
        config: oracle_cache.environment_stamp(
            REPO_ROOT,
            spec,
            config,
            features_for_config(config),
            out_dir / f"baseline-{config}.subset.tsv.gz",
            ALIAS_YAML,
            keys.keys(),
        )
        for config in conform.ACCEPTANCE_CONFIGS
    }
    return keys, stamps


def oracle_position_keys(
    keys: Mapping[str, str], out_dir: Path
) -> tuple[dict[str, str], oracle_cache.EnvironmentStamp] | tuple[None, None]:
    """Return the position store's two keys, computed from the font the oracle is about to shape against and the kern sidecar it reads: `oracle_cache.position_keys` over the row keys already computed. With no compiled font under `out_dir` there is nothing to shape, and it returns `(None, None)`, which records every position as unshaped and serves none."""
    font = Path(out_dir) / "M1.otf"
    if not font.is_file():
        return None, None
    return oracle_cache.position_keys(REPO_ROOT, keys, font, KERN_SIDECAR_YAML)


def _report_oracle_cache(
    out_dir: Path,
    keys: Mapping[str, str],
    stamps: Mapping[str, oracle_cache.EnvironmentStamp],
    position_keys: Mapping[str, str] | None = None,
    position_stamp: oracle_cache.EnvironmentStamp | None = None,
) -> None:
    """Print, before the fan-out, which stored verdicts will and will not be served. The hit rate is bimodal, and a whole-store drop looks like a bug unless the line that caused it is named. A changed stamp line (a pipeline module, a predicate class gaining a member, the settlement mode flags `kernel_exec.settlement_flags` returns) drops every row of every configuration. A changed family key re-derives only the rows that can reach that family, the usual result of a rune edit. The position store is reported on its own line: a changed position stamp line (the position channel's module, the kern sidecar, the font's helpers) re-shapes every row while the row verdicts are still served, and a changed position key re-shapes only the rows that reach that family, the usual result of a glyph edit."""
    recorded = oracle_cache.read_header(oracle_cache.store_path(out_dir, conform.ACCEPTANCE_CONFIGS[0]))
    if recorded is None:
        console.say("oracle row cache: no store on disk — this pass derives every row and writes one")
        return
    stamp = stamps[conform.ACCEPTANCE_CONFIGS[0]]
    stored_lines = {
        label: digest
        for label, _, digest in (str(line).partition("\t") for line in recorded.get("environment") or ())
    }
    moved_stamp = oracle_cache.moved_note(stored_lines, stamp.labels)
    if moved_stamp is not None:
        console.warn(f"oracle row cache: dropped — the stamp moved at {moved_stamp}")
        return
    stored_keys = {str(name): str(value) for name, value in (recorded.get("family_keys") or {}).items()}
    moved_keys = oracle_cache.moved_note(stored_keys, dict(keys))
    if moved_keys is None:
        console.say("oracle row cache: the stamp and every family key still stand")
    else:
        console.warn(f"oracle row cache: re-deriving the rows that reach {moved_keys}")
    if position_keys is None or position_stamp is None:
        console.say("oracle position store: no font to shape against — every position is shaped")
        return
    stored_position_lines = {
        label: digest
        for label, _, digest in (
            str(line).partition("\t") for line in recorded.get("position_environment") or ()
        )
    }
    moved_position_stamp = oracle_cache.moved_note(stored_position_lines, position_stamp.labels)
    if moved_position_stamp is not None:
        console.warn(
            f"oracle position store: re-shaping every row — the position stamp moved at {moved_position_stamp}"
        )
        return
    stored_position_keys = {
        str(name): str(value) for name, value in (recorded.get("position_keys") or {}).items()
    }
    moved_position_keys = oracle_cache.moved_note(stored_position_keys, dict(position_keys))
    if moved_position_keys is None:
        console.say("oracle position store: the position stamp and every glyph key still stand")
    else:
        console.warn(f"oracle position store: re-shaping the rows that reach {moved_position_keys}")


def _priced_conformance_config(
    spec: ResolvedSpec,
    font_path: Path,
    config: str,
    max_length: int = 4,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    guard_verdicts: FormationGuard | None = None,
    settle_memo: conform.SettleMemoFile | None = None,
) -> tuple[conform.ConformanceConfigResult, int]:
    """Run one configuration's belt sweep in a pool worker (`conform.conformance_config_worker`) and return the worker's peak memory (`peak_rss_self_bytes`) with the result, so the parent can write the belt's `conform-belt` pool record. The peak is returned in a pair instead of as a field on the result because conform.py is in `oracle_cache.ORACLE_ROW_CODE_PATHS`, and an edit there would invalidate every settle memo and row store. The reading is the process's peak memory, so a configuration that runs second in a reused worker reads at or above the one before it."""
    result = conform.conformance_config_worker(
        spec, font_path, config, max_length, glyphs, guard_verdicts, settle_memo
    )
    return result, peak_rss_self_bytes()


def _absorb_settle_memo_parts(
    memo: conform.SettleMemoFile, parts: Sequence[Path], spec: ResolvedSpec
) -> tuple[bool, float, int]:
    """Merge one configuration's parts into its shared settle memo file (`conform.absorb_settle_memo_parts`) in a pool worker. Return whether the file was rewritten, the seconds the merge took, and the worker's peak memory (`peak_rss_self_bytes`). The parent reports a rewrite as `[t] settle_memo_absorb <config>` beside the replay's `settle_memo_emit` lines; it is a whole-file write, once per pass for each configuration that settled anything fresh. The peak goes into the pool record beside the row ranges' peaks. Like every reading there it is the process's peak memory, so it reads at or above the range its worker ran before it."""
    started = time.perf_counter()
    written = conform.absorb_settle_memo_parts(memo, parts, spec)
    return written, time.perf_counter() - started, peak_rss_self_bytes()


def _shard_settle_memo(
    memo: conform.SettleMemoFile | None, scratch: Path, shard: oracle.OracleShard
) -> conform.SettleMemoFile | None:
    """Return the settle memo file one row range reads and writes: it reads the configuration's shared file and writes its own part under the run's scratch directory, whether or not the configuration is split into several ranges. So no range replaces the shared file, either over another range of its configuration or over the witness stage, which can be merging its own part into that file while the pool runs. The parent merges every part once all the ranges have finished and the witness stage has returned."""
    if memo is None:
        return None
    return replace(memo, write_path=oracle.settle_memo_part(scratch, shard.config, shard.index))


def run_oracle(
    out_dir: Path = OUT_DIR,
    spec: ResolvedSpec | None = None,
    jobs: int = 1,
    write_cache: bool = True,
    fresh_cache: bool = False,
    memo_inputs: oracle_cache.SettleMemoInputs | None = None,
    memo_ready: Callable[[], None] | None = None,
) -> dict:
    """Run the baseline oracle (`rebuild/M1-PLAN.md` §6) over the subset tables, reading the row cache before the first row and writing it after the last. Above `--jobs 1` the unit of work is a row range of one configuration's table: `oracle.oracle_shard_plan` splits every table (by the row counts the subset stamp records, `baseline_subset.subset_row_counts`; a table the stamp does not count stays one range) so that each of `jobs` workers gets an equal share of the whole, and `_spawn_pool` starts up to `jobs` workers, one per range at most. Each range writes its own audit segment and store segment, and writes the settle memo windows it settled fresh as a part. The parent merges the ranges' tallies (`oracle.merge_config_shards`), concatenates the segments (`oracle.join_oracle_audit`, `oracle_cache.join_store_segments`), and merges the parts into the shared memo files (`conform.absorb_settle_memo_parts`, on the same pool, each merge's peak recorded in the pool record as `<config> absorb` beside the ranges'), all in row order, so the summary, the audit and every store's records match an unsplit run. The crate's formation-guard verdicts are computed once here and passed with every submission, as in the belt's fan-out.

    `memo_inputs` is `settle_memo_inputs` as the caller took it before loading `spec`, and names the settle memo files this pass shares with the belt (`conform.settle_memo_files`). The oracle's rows are the belt's texts, so a settlement configuration whose file the replay or the belt already wrote under these keys settles nothing here, and one neither has reached yet writes the file the belt will load. A caller with no inputs shares nothing, and the overlay configuration has no memo, since its worker settles nothing. `memo_ready`, when given, is called before this pass writes a shared settle memo file. On the pooled path that is once every range has finished, because ranges write only parts and the parent's merges are the pass's only writes to the files. On the `--jobs 1` path it is before the first walk, because those walks replace the files whole as they go. `main` passes `TableGates.wait_for_memo`, which returns once the witness stage has merged its own part into each file. That lets the pool start after the string replay alone while the witness stage still runs: a range reads a whole file whether it maps it before or after the witness stage's merge, and every write this pass makes includes what the witness stage wrote.

    The cache keys are computed once here (the row keys from the rune tree, the position keys from the compiled font and the kern sidecar) and passed to the workers, then computed again at promotion, where a store is written only if no stamp and no key changed while the run used them. The second computation is needed because `fingerprint.rune_digests` reads the rune files from disk, a full run takes minutes, and a long run is usually detached while editing continues. Without it, a rune edited mid-run would be recorded under a digest the stored verdicts were never built from, and later passes would serve pre-edit verdicts as current indefinitely. `_settle_green`'s recompute-before-recording and `artifact_cycle`'s green keys follow the same rule for the same reason.

    Failing to compute those keys does not fail the gate. `alias_family_digests` rejects an alias head with no rune digest behind it, and the alias map is a hand-edited file that one typo can make unreadable. So a key that cannot be computed leaves this pass with no cache: every row is derived, no store is written, and the gate runs as it does without a cache.

    Staging is inside this run's pid-named audit scratch directory, so a killed run's stores, segments and memo parts are deleted by `discard_oracle_audit_scratch` along with its audit segments, and two oracles sharing an `out_dir` (a `--gates-only` pass beside a cycle) cannot read or promote each other's files. Promotion happens only after `join_oracle_audit` has accepted the audit, because a store describing an audit that was never written is worse than no store. A split configuration where one range staged no segment is neither joined nor promoted, the all-or-nothing rule `promote_stores` already follows.

    Every range's peak is recorded as one observation of the `oracle-shard` pool (`cycle_timings.record_pool`), which `make job-costs` compares against `artifact_cycle.ORACLE_SHARD_BYTES`.
    """
    if spec is None:
        spec = load_default_spec()
    oracle.discard_oracle_audit_scratch(out_dir)
    if fresh_cache and write_cache:
        oracle_cache.discard_stores(out_dir, conform.ACCEPTANCE_CONFIGS)
    scratch = oracle.oracle_audit_scratch(out_dir)
    keys: dict[str, str] | None = None
    stamps: dict[str, oracle_cache.EnvironmentStamp] | None = None
    position_keys: dict[str, str] | None = None
    position_stamp: oracle_cache.EnvironmentStamp | None = None
    row_cache: oracle.OracleRowCache | None = None
    try:
        keys, stamps = oracle_row_cache_keys(spec, out_dir)
        position_keys, position_stamp = oracle_position_keys(keys, out_dir)
    except (OSError, ValueError, yaml.YAMLError) as error:
        console.warn(
            f"oracle row cache: unavailable for this pass — its keys would not cut ({error}); every row is derived and no store is written"
        )
        keys = stamps = None
    else:
        if fresh_cache:
            console.say("oracle row cache: distrusted for this pass — every row is derived")
        else:
            _report_oracle_cache(out_dir, keys, stamps, position_keys, position_stamp)
        row_cache = oracle.OracleRowCache(
            environment=stamps,
            family_keys=keys,
            read_dir=None if fresh_cache else out_dir,
            write_dir=scratch if write_cache else None,
            rotation=0 if write_cache else int(time.time()),
            position_environment=position_stamp,
            position_keys=position_keys,
        )
    settle_memos = conform.settle_memo_files(out_dir, spec, memo_inputs)
    try:
        if jobs > 1:
            shards = oracle.oracle_shard_plan(jobs, baseline_subset.subset_row_counts(out_dir))
            segments = {config: 0 for config in conform.ACCEPTANCE_CONFIGS}
            for shard in shards:
                segments[shard.config] = shard.of
            kernel_exec.ensure_built()
            guard_verdicts = kernel_exec.guard_sweep(spec)
            landed: dict[str, list[tuple[oracle.OracleShard, oracle.OracleConfigResult]]] = {
                config: [] for config in conform.ACCEPTANCE_CONFIGS
            }
            width = min(jobs, len(shards))
            with _spawn_pool(jobs, len(shards)) as pool:
                futures = {
                    pool.submit(
                        oracle.oracle_config_worker,
                        spec,
                        out_dir,
                        ALIAS_YAML,
                        DIVERGENCES_YAML,
                        shard.config,
                        out_dir / "M1.otf",
                        KERN_SIDECAR_YAML,
                        audit_dir=scratch,
                        row_cache=row_cache,
                        settle_memo=_shard_settle_memo(settle_memos.get(shard.config), scratch, shard),
                        guard_verdicts=guard_verdicts,
                        shard=shard,
                    ): shard
                    for shard in shards
                }
                done = 0
                for future in as_completed(futures):
                    result = future.result()
                    landed[result.config].append((futures[future], result))
                    done += 1
                    console.progress(done, len(shards), "shards")
                if memo_ready is not None:
                    memo_ready()
                merges = {
                    config: pool.submit(
                        _absorb_settle_memo_parts,
                        memo,
                        [
                            oracle.settle_memo_part(scratch, config, index)
                            for index in range(segments[config])
                        ],
                        spec,
                    )
                    for config, memo in settle_memos.items()
                }
                absorbed = []
                worker_peaks = {
                    shard.label: result.peak_rss_bytes for pairs in landed.values() for shard, result in pairs
                }
                for config, future in merges.items():
                    written, seconds, peak = future.result()
                    worker_peaks[f"{config} absorb"] = peak
                    if written:
                        absorbed.append(config)
                        console.timing(f"settle_memo_absorb {config}", seconds)
            if absorbed:
                console.say(f"settle memo: absorbed the ranges' parts for {', '.join(absorbed)}")
            record_pool(
                "oracle-shard",
                width=width,
                worker_peaks=worker_peaks,
                controller_peak_bytes=peak_rss_self_bytes(),
            )
            ordered = [
                oracle.merge_config_shards(
                    [result for _shard, result in sorted(landed[config], key=lambda pair: pair[0].first_row)]
                )
                for config in conform.ACCEPTANCE_CONFIGS
            ]
            report = oracle.merge_oracle_results(ordered)
            oracle.join_oracle_audit(
                out_dir, scratch, conform.ACCEPTANCE_CONFIGS, report.divergent_rows, segments=segments
            )
            if row_cache is not None and row_cache.write_dir is not None:
                for merged in ordered:
                    if segments[merged.config] > 1 and merged.pass_ordinal is not None:
                        stamp = row_cache.environment[merged.config]
                        oracle_cache.join_store_segments(
                            scratch,
                            merged.config,
                            segments[merged.config],
                            stamp,
                            stamp.labels["subset"],
                            merged.pass_ordinal,
                            row_cache.family_keys,
                            merged.rows_compared,
                            row_cache.position_environment,
                            row_cache.position_keys,
                        )
        else:
            if memo_ready is not None:
                memo_ready()
            report = oracle.compare_against_baseline(
                spec,
                out_dir,
                ALIAS_YAML,
                DIVERGENCES_YAML,
                out_dir=out_dir,
                font_path=out_dir / "M1.otf",
                kern_sidecar_path=KERN_SIDECAR_YAML,
                row_cache=row_cache,
                settle_memos=settle_memos,
            )
        if write_cache and keys is not None and stamps is not None:
            _promote_oracle_row_cache(spec, out_dir, scratch, keys, stamps, position_keys, position_stamp)
    finally:
        oracle.discard_oracle_audit_scratch(out_dir)
    summary = {
        "rows_compared": report.rows_compared,
        "divergent_rows": report.divergent_rows,
        "positions_compared": report.positions_compared,
        "positions_excluded": report.positions_excluded,
        "positions_served": report.positions_served,
        "counts_by_entry": dict(sorted(report.counts_by_entry.items())),
        "unmatched": report.unmatched_count,
        "multi_matched": report.multi_matched_count,
        "notes": report.notes,
    }
    for row in report.unmatched_exemplars[: oracle.ORACLE_UNMATCHED_EXEMPLARS]:
        summary.setdefault("unmatched_exemplars", []).append(
            f"{row.config} {row.codepoints} {'|'.join(row.baseline_glyphs)} -> {'|'.join(row.new_cells)} {row.phenomena}"
        )
    (out_dir / "oracle_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _record_cli_check(verdict: CheckVerdict, started: float) -> None:
    """Record this invocation's verdict in the timings journal, unless the artifact cycle is recording it. The verdict is recorded before the final `SystemExit`, so the exit cannot change it. `CYCLE_RUN_ENV` in the environment means the artifact cycle started this run and records the same verdict under its own run id, so this function records nothing, keeping one line per invocation."""
    if CYCLE_RUN_ENV in os.environ:
        return
    record_check(
        verdict,
        argv=sys.argv,
        elapsed_s=time.perf_counter() - started,
        peak_rss_bytes=process_peak_rss_bytes(),
    )


def _failed_check(check: str, message: str) -> CheckVerdict:
    """Return the verdict for a run that failed before its gate was evaluated: a defect gate that stopped the build, a pin gate failure, a read-back or emit error. The error message is all that is known, so it is the only failure text, and there are no failed ids because nothing enumerated cases."""
    return CheckVerdict(check=check, verdict="red", status="FAILED", failures=[message], failed_ids=[])


def _run_pregate_guards() -> None:
    """Run the three checks that come before any gate, on both the build path and the `--gates-only` path. They check that every source baseline table was shaped by the site font on disk (`baseline_subset.ensure_fresh` compares each header's font_sha256 with the font that header names on every call, because `make all` rewrites that font without changing the stamp key), refilter the subset baselines when they no longer match the sources on disk, and check that every old glyph name in those subsets has an alias. All three protect the oracle, not the build: rows shaped by another font, or an unaliased name, make every oracle number wrong without any error. So a pass that re-runs the oracle over a build it did not make runs them as the building pass does. The ss06, ss07 and ss06+ss07 identity check runs inside the refilter, since only a refilter can change its result. A configuration that fails it is never stamped fresh, so its error is raised here on every run until it is fixed."""
    console.phase("baseline_subset")
    start = time.perf_counter()
    try:
        refiltered = baseline_subset.ensure_fresh(REPO_ROOT)
    except (baseline_subset.SubsetIdentityError, baseline_subset.BaselineProvenanceError) as error:
        raise SystemExit(str(error)) from error
    print(
        f"[t] baseline_subset {time.perf_counter() - start:.1f}s ({'refiltered' if refiltered else 'fresh'})",
        flush=True,
    )
    console.phase("alias_completeness")
    start = time.perf_counter()
    missing_aliases = oracle.unaliased_subset_names(OUT_DIR, ALIAS_YAML)
    print(f"[t] alias_completeness {time.perf_counter() - start:.1f}s", flush=True)
    if missing_aliases:
        listing = "\n".join(f"  {name} ({', '.join(configs)})" for name, configs in missing_aliases.items())
        raise SystemExit(
            f"rebuild/m1-aliases.yaml is missing {len(missing_aliases)} old glyph names that appear in subset baseline rows — every oracle number would be quietly wrong, so author each entry (or map it to the literal `pending` to run anyway with those rows unaliased):\n{listing}"
        )


def run_gates_only(out_dir: Path = OUT_DIR, jobs: int = 1, fresh_cache: bool = False) -> None:
    """Re-run everything a full run does after the table build except the stages that produce artifacts: the defect gate, the Manual-pin replay and the oracle, over the tables and the M1.otf already on disk. It rewrites the defect fields of `pipeline_summary.json`, the Stage A record, the gate summaries and `divergence-audit.tsv`, and compiles nothing. The reuse depends on the stamp the build left on its serialized enumerations. The stamp names the sources those tables came from, so a stamp that still matches the runes on disk means the M1.otf beside them is the font those runes describe, and a mismatch stops with an error instead of sweeping a stale binary. Because the stamp names only what the build reads, every comparison-side edit passes it and is re-checked here: the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, and the classifier and the position channel, rebuild/pipeline/oracle.py and rebuild/pipeline/oracle_positions.py, both outside the stamp's code half.

    It may record run_m1's green under two conditions: a prior green record exists, and every input that changed since it is comparison-side (`artifact_cycle.gates_only_reuse`). The prior green shows that the tables and font on disk came from a completed build over every build-side input, the stamp check shows that none of those inputs has changed since, and this pass re-checks the gates the changed inputs feed. With all three, the recorded green covers the new inputs, so the next cycle skips run_m1. Otherwise the pass still runs, still records its check line, and prints which input kept it from recording a green. A check line reports only how one invocation came out, while a green record lets a later pass skip work.

    It opens the oracle row cache read-only (`write_cache=False`). A ledger edit serves every row and position verdict, and an alias edit re-derives only the rows that reach the families it names, so the re-check is fast; a store this pass wrote would be one no build produced. Because no store is written, the store's pass ordinal does not advance, and the renewal slice and the verification sample, which advance with it, rotate on the clock instead. Otherwise repeated re-checks would verify the same slice of the table every time. `--fresh-oracle-cache` here skips reading the stores instead of deleting them, since deleting a build input is also a write.
    """
    from rebuild.tools.artifact_cycle import (
        comparison_side_label,
        evaluate_run_m1_gate,
        gates_only_reuse,
        moved_input_labels,
        read_green_record,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(REPO_ROOT)

    started = time.perf_counter()
    _run_pregate_guards()
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
    font_path = out_dir / "M1.otf"
    summary_path = out_dir / "pipeline_summary.json"
    serialized = serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; --gates-only re-runs the gates over a build, it does not make one"
        )
    if not font_path.is_file():
        raise SystemExit(
            f"no compiled font at {font_path} — run `uv run python -m rebuild.pipeline.run_m1` first"
        )
    try:
        pipeline_summary = json.loads(summary_path.read_text())
    except OSError, ValueError:
        pipeline_summary = None
    if not isinstance(pipeline_summary, dict):
        raise SystemExit(
            f"no readable {summary_path} — the defect fields are rewritten into the build's own summary, and a build that left none is not a build this pass can stand on; run `uv run python -m rebuild.pipeline.run_m1` first"
        )

    spec = load_default_spec()
    before = run_m1_key()
    record = read_green_record(RUN_M1_GREEN)
    current = run_m1_skip_files(REPO_ROOT)

    tables: dict[str, tuple] = {}
    for config, decision in serialized.items():
        treaty_path = out_dir / f"treaties-{config}.tsv"
        try:
            tables[config] = (decision, table_module.read_treaty_tsv(treaty_path))
        except (OSError, ValueError) as error:
            raise SystemExit(
                f"{treaty_path} is missing or unreadable ({error}) — the defect gate reads the treaty tables beside the enumeration, so this build is not one this pass can adjudicate; run `uv run python -m rebuild.pipeline.run_m1` first"
            )

    console.phase("defect_gates")
    start = time.perf_counter()
    cell_glyphs = mint_cell_glyphs(spec, tables)
    defect_fields = _defect_summary_fields(_run_defect_gates(spec, tables, cell_glyphs))
    print(f"[t] defect_gates {time.perf_counter() - start:.1f}s", flush=True)
    pipeline_summary.update(defect_fields)
    summary_path.write_text(json.dumps(pipeline_summary, indent=2) + "\n")
    fingerprint.write_stage_a(REPO_ROOT, out_dir)
    if defect_fields["defect_errors"]:
        message = f"{len(defect_fields['defect_errors'])} defect-gate errors; see pipeline_summary.json"
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", message), started)
        raise SystemExit(message)

    console.phase("run_manual_pin_gate")
    start = time.perf_counter()
    pin_gate = run_manual_pin_gate(out_dir=out_dir, spec=spec)
    print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(pin_gate, indent=2))
    pin_failure = manual_pin_gate_failure(pin_gate)
    if pin_failure is not None:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", pin_failure), started)
        raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")

    console.phase("run_oracle")
    start = time.perf_counter()
    oracle_summary = run_oracle(
        out_dir=out_dir,
        spec=spec,
        jobs=jobs,
        write_cache=False,
        fresh_cache=fresh_cache,
        memo_inputs=memo_inputs,
    )
    print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(oracle_summary, indent=2))
    gate = evaluate_run_m1_gate(pipeline_summary, pin_gate, oracle_summary)
    _record_cli_check(gate, started)
    if not gate.ok:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")
    if gates_only_reuse(record, current) is not None:
        _settle_green(
            RUN_M1_GREEN,
            before,
            True,
            run_m1_key,
            "run_m1",
            files_of=lambda: run_m1_skip_files(REPO_ROOT),
        )
        return
    labels = moved_input_labels(record, current) or []
    offenders = [label for label in labels if not comparison_side_label(label)]
    if offenders:
        why = f"these inputs are build-side, so the artifacts on disk are not the ones they describe: {', '.join(offenders)}"
    elif record is None:
        why = "there is no prior green M1 build for this pass to stand on"
    elif not isinstance(record.get("files"), dict):
        why = "the last green M1 build predates the per-file record this decision is taken over"
    else:
        why = "nothing has moved since the last green M1 build, so that green already stands"
    console.warn(f"run_m1: green, but this pass recorded no green — {why}")


def _settle_green(
    green_path: Path,
    key: str,
    ok: bool,
    recompute: Callable[[], str],
    label: str,
    files_of: Callable[[], dict[str, str]] | None = None,
) -> None:
    """Record or clear a last-green record, by the rule `rebuild.tools.make_test_gate` follows: the key is taken before the work, recomputed after, and recorded only if it still matches, since inputs edited mid-run describe content that was never tested. A failed result whose key still matches the record deletes the record, since the result contradicts it. Recording lets the artifact cycle skip work an interactive run already verified. `files_of` supplies the per-file digest lines behind the key, so a later skip miss can name the input that changed."""
    from rebuild.tools.artifact_cycle import clear_contradicted_green, record_green

    if not ok:
        clear_contradicted_green(green_path, key)
        return
    if recompute() != key:
        console.warn(f"{label}: green, but its inputs changed while it ran — green not recorded")
        return
    record_green(green_path, key, files=files_of() if files_of is not None else None)
    where = green_path.relative_to(REPO_ROOT) if green_path.is_relative_to(REPO_ROOT) else green_path
    print(f"{label}: green — fingerprint recorded in {where}", flush=True)


def main(argv: list[str] | None = None) -> None:
    from rebuild.tools.artifact_cycle import conform_job_budget, conform_job_derivation, sweep_job_budget

    sweep_jobs = sweep_job_budget()
    belt_jobs = conform_job_budget(skip_gates=True, skip_surface=True)
    parser = argparse.ArgumentParser(description="Run the M1 integration pipeline and its Phase-2 gates.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"worker budget for the oracle and the conformance sweep: the oracle cuts every configuration's table into row ranges and runs this many at once, while the conformance sweep runs one process per acceptance configuration and no more, since that is its unit. Each default is a budget the artifact cycle derives from the box rather than a checked-in width: a bare run takes the oracle's `sweep_job_budget()`, the width the cycle hands run_m1 — {sweep_jobs} on this box, the cores under the memory clamp that budget's own docstring argues from `ORACLE_SHARD_BYTES` — and --conform-only takes the belt's own `conform_job_budget()` at its idle arm, since a hand sweep shares the box with no surface build and no make-test pool — on this box {conform_job_derivation(skip_gates=True, skip_surface=True)}. `--jobs 1` is serial. The table build's own width is --kernel-threads.",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--gates-only",
        action="store_true",
        help="re-run the defect gate, the Manual-pin gate and the oracle against the M1.otf and tables already on disk, rewriting the defect fields of pipeline_summary.json, the gate summaries, the Stage A record and divergence-audit.tsv; refuses when those tables were built from other sources than the ones on disk, and records run_m1's green when everything that moved since the last green build is comparison-side",
    )
    parser.add_argument(
        "--fresh-oracle-cache",
        action="store_true",
        help="derive every row rather than serving any from the oracle's per-row verdict stores, and write fresh ones over them; under --gates-only, which may not write a build input, it declines to read the stores and leaves them where they are",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=4,
        help="exhaustive sweep length for --conform-only (the per-edit belt over the settlement configurations; the overlay configuration's arm stays at its own horizon); `make conform-deep` runs the same sweep deeper on demand",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        default=None,
        help=(
            "how many delta configurations the kernel enumerates and folds at once beside default's memo, capped at the configuration count and the cores this process may actually run on; the ceiling is memory rather than CPU, so the default is derived from the box in hand rather than checked in — on this one "
            f"{describe_fit(kernel_exec.DELTA_PEAK_BYTES, coresident_bytes=kernel_exec.DEFAULT_MEMO_BYTES)}, the co-resident term being default's retained memo — which AMS_KERNEL_THREADS short-circuits and this flag beats in turn; the string replay after the build has its own width, --replay-threads"
        ),
    )
    parser.add_argument(
        "--replay-threads",
        type=int,
        default=None,
        help=(
            "how many settlement configurations the string replay after the build walks at once in its one crate process, capped at the configuration count and the cores this process may actually run on; a replay's engine is priced on its own rather than at the table build's width, so the default is derived from the box in hand — on this one "
            f"{describe_fit(kernel_exec.REPLAY_PEAK_BYTES, cap=len(conform.SETTLEMENT_CONFIGS))}, nothing co-resident since the build's process has exited — which AMS_REPLAY_THREADS short-circuits and this flag beats in turn"
        ),
    )
    args = parser.parse_args(argv)
    belt_default = args.conform_only and not args.gates_only
    stated = args.jobs if args.jobs is not None else belt_jobs if belt_default else sweep_jobs
    jobs = stated if stated > 1 else 1
    started = time.perf_counter()

    if args.gates_only:
        run_gates_only(out_dir=OUT_DIR, jobs=jobs, fresh_cache=args.fresh_oracle_cache)
        return

    if args.conform_only:
        from rebuild.tools.artifact_cycle import (
            conform_skip_fingerprint,
            conform_skip_files,
            evaluate_conform_gate,
        )
        from rebuild.tools.cycle_paths import CONFORM_GREEN

        def conform_key() -> str:
            return conform_skip_fingerprint(REPO_ROOT, args.conform_horizon)

        before = conform_key()
        console.phase("run_font_conformance")
        start = time.perf_counter()
        conformance = run_font_conformance(max_length=args.conform_horizon, jobs=jobs)
        print(
            f"[t] run_font_conformance {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(conformance, indent=2))
        verdict = evaluate_conform_gate(conformance)
        _settle_green(
            CONFORM_GREEN,
            before,
            verdict.ok,
            conform_key,
            "gate:conform",
            files_of=lambda: conform_skip_files(REPO_ROOT, args.conform_horizon),
        )
        _record_cli_check(verdict, started)
        if not conformance["pass"]:
            raise SystemExit("font conformance failed; see conform_summary.json")
        return

    from rebuild.tools.artifact_cycle import (
        evaluate_run_m1_gate,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(REPO_ROOT)

    _run_pregate_guards()
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
    spec = load_default_spec()
    before = run_m1_key()
    gates: TableGates | None = None
    try:
        run_ligature_outgoing(spec)
        console.phase("run_total")
        start = time.perf_counter()
        summary, gates = run(
            spec=spec,
            inputs=inputs,
            kernel_threads=args.kernel_threads,
            memo_inputs=memo_inputs,
            replay_threads=args.replay_threads,
        )
        console.timing("run_total", time.perf_counter() - start, rss_token(process_peak_rss_bytes()))
        console.say(json.dumps(summary, indent=2))
        if summary["defect_errors"]:
            raise SystemExit(f"{len(summary['defect_errors'])} defect-gate errors; see pipeline_summary.json")
        console.phase("run_manual_pin_gate")
        start = time.perf_counter()
        pin_gate = run_manual_pin_gate(spec=spec)
        console.timing("run_manual_pin_gate", time.perf_counter() - start)
        console.say(json.dumps(pin_gate, indent=2))
        pin_failure = manual_pin_gate_failure(pin_gate)
        if pin_failure is not None:
            raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")
        gates.wait_for_replay()
        console.phase("run_oracle")
        start = time.perf_counter()
        oracle_summary = run_oracle(
            spec=spec,
            jobs=jobs,
            fresh_cache=args.fresh_oracle_cache,
            memo_inputs=memo_inputs,
            memo_ready=gates.wait_for_memo,
        )
        console.timing("run_oracle", time.perf_counter() - start)
        console.say(json.dumps(oracle_summary, indent=2))
        gates.join()
    except (SystemExit, readback.ReadbackError, emit_gsub.EmitError, conform.WitnessError) as error:
        red = gates.first_red() if gates is not None else None
        complaint: BaseException = error if red is None else red
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", str(complaint)), started)
        if isinstance(complaint, SystemExit):
            raise complaint
        raise SystemExit(str(complaint))
    finally:
        if gates is not None:
            gates.close()
    gate = evaluate_run_m1_gate(summary, pin_gate, oracle_summary)
    _settle_green(
        RUN_M1_GREEN, before, gate.ok, run_m1_key, "run_m1", files_of=lambda: run_m1_skip_files(REPO_ROOT)
    )
    _record_cli_check(gate, started)
    if not gate.ok:
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")


def _hard_exit(status: int) -> NoReturn:
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)


def _run_cli() -> None:
    try:
        main()
    except SystemExit as error:
        if error.code is None:
            status = 0
        elif isinstance(error.code, int):
            status = error.code
        else:
            sys.stdout.flush()
            print(error.code, file=sys.stderr)
            status = 1
        _hard_exit(status)
    _hard_exit(0)


if __name__ == "__main__":
    # This batch is short-lived, and its large live heap contains almost no cyclic garbage worth scanning.
    gc.freeze()
    gc.disable()
    _run_cli()
