"""The M1 integration driver: runs the full pipeline over the real rune files and writes the `doc/rebuild-design.md` §8 artifacts under rebuild/out/m1/.

First, `build_tables` builds the decision and join tables for every settlement configuration (`conform.SETTLEMENT_CONFIGS`) in one kernel-crate process. It enumerates and folds `default` first and each other configuration as a delta over `default`'s memo. As it folds each configuration it checks first-match-wins against the rows, writes the TSVs, writes one certificate per rule built from the table's row chains, and writes the window enumeration stamped with the fingerprint of its sources, as the rows that differ from the previous build's where there is one. `--conform-only` takes its glyph inventory from that enumeration's head file and stops with an error when it is stale or missing. Each configuration's windows head is committed on a background pool once its payload is written.

Two branches then run over the tables. The table-only branch (`_run_table_gates`, on one background thread) runs the string replay (`run_replay_strings`, recorded in `replay_summary.json`), which walks every configuration's stored rules over every text up to `REPLAY_MAX_LENGTH` against the crate's own settlement, then the witness stage (`_spawn_rule_witnesses`, in a spawned worker of its own handed one configuration's decision table at a time, the step `run_rule_witnesses` runs in process, recorded in `witness_summary.json`), which settles every certificate and checks that its rule fires. Beside those two it runs the shipped-order walk (`run_emitted_order`), where each configuration's walk waits for that configuration's pack. The glyph chain (`_run_glyph_chain`, on the calling thread) mints the glyphs (settled cells named by their cell labels, the raw cmap glyphs, the marker, locked and ss10 copies, and the namer dot pair), runs the defect gates under the reviewed allow-list, emits GSUB and GPOS (also writing `behavior_classes.json`, the record key `rebuild/tools/deep_sweep.py` reads), compiles the font, and runs read-back (rebuild/pipeline/readback.py). Read-back re-parses the written font, checks it against the emitters' plan, and checks the GSUB's uint16 subtable-offset headroom against its floor and the settlement lookup's largest packed group against its ceiling in the same parse. Its `[t] readback` line ends with HEAD's short commit id and the settlement lookup's size figures (`readback.budget_figures`), so the timings journal keeps them for every build and `make cycle-timings ARGS='--by-commit'` lists them.

`full_run` then runs the Manual-pin gate and the oracle (rebuild/pipeline/run_m1_gates.py). The oracle starts once the string replay has returned, beside the witness stage, and writes to the settle memos only after the witness stage, which reads them and writes none, has returned. `full_run` joins the table-only branch after the oracle, before it decides the run_m1 gate. The join raises the first failure in serial order (the packing, the replay, the witnesses, the shipped order), and any of those is raised in place of a glyph-chain failure, so a failing build reports what a serial build would.

Settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set use the same names. The raw cmap glyph for each rune is the bare rune name, drawn as the isolated cell with no curs anchors. Marker, locked, and ss10 copies reuse that drawing. Under ss10 the ss10 input substitution replaces every letter's cmap glyph with its anchor-free `.ss10` copy before formation, so no ligature forms, nothing settles, each letter keeps its own cluster, and every junction is a break. That is why the overlay configuration (`conform.OVERLAY_CONFIGS`) has no table: read-back checks that the ss10 input substitution covers every letter cmap glyph and that the copies appear in no other stage, the conformance sweep (gate:conform's exhaustive HarfBuzz sweep) sweeps the overlay at `conform.OVERLAY_MAX_LENGTH`, and the oracle compares its rows against the bare stream, using the copies' `hmtx` advances for positions.

The split-buffer check runs inside gate:conform's sweep, at maximum length 4 on every build and at maximum length 5 or more through `make conform-deep`. Read-back's boundary-glyphs stage checks the ZWNJ glyph's zero advance and empty outline on the written font bytes, so the conformance sweep does not check them at every shaped slot.

Run as `uv run python -m rebuild.pipeline.run_m1`. The entry (`cli`) refuses while the alphabet switch (`spec_load.M1_RUNES_ENV`) is set outside the fault kit, and otherwise hands the command line to `run_m1_gates.main`, which parses it and calls `full_run` for a bare run. `--conform-only` runs only the conformance sweep against the M1.otf on disk. `--gates-only` re-runs the defect gate, the Manual-pin gate and the oracle over the tables and font already on disk, without rebuilding anything. It is the fast way to re-check an edit to a comparison-side input: the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, or the code `fingerprint.COMPARISON_CODE_MODULES` names. The tables' stamp leaves all of these out (`fingerprint.table_code_paths`; rebuild/test_build_code_closure.py checks that the build never reaches that code). A `--gates-only` pass records run_m1's green when a prior green exists and everything that changed since is comparison-side, so the artifact cycle plans this gates-only rerun itself and the pass after it skips run_m1.

A full run and a `--gates-only` rerun both serve what they can from the per-row verdict stores that rebuild/pipeline/oracle_cache.py keeps beside the tables. A ledger edit changes no family key and no stamp line, so every row and position verdict is served and only the ledger match and the audit run. An alias edit changes the family keys of the families it names, so only the rows that reach them are re-derived. An edit to the kern sidecar or to the position comparison's code closure keeps the row verdicts and re-shapes the positions, an edit to the comparison's code outside that closure (the walk, the crate, the store) re-derives every row and keeps each position whose settled cells did not change, and a classifier edit serves both. `--fresh-oracle-cache` ignores the stores, and `--gates-only` may read them but never writes one.
"""

from __future__ import annotations

import functools
import gc
import glob
import gzip
import hashlib
import json
import multiprocessing
import os
import queue
import re
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zlib
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field, replace
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import IO, Any, Callable, Mapping, Sequence

import yaml

from rebuild.pipeline import (
    compile_font,
    conform,
    defects,
    emit_gpos,
    emit_gsub,
    fingerprint,
    geometry,
    kernel_exec,
    kernel_io,
    oracle_cache,
    output_sink,
    readback,
    row_delta,
    spec_load,
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
    ss10_copy_name,
)
from rebuild.pipeline.settle import FormationGuard, cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable
from rebuild.tools import console
from rebuild.tools.cycle_timings import record_pool
from rebuild.tools.memory_budget import usable_cores
from rebuild.tools.peak_rss import (
    ReapedPeaks,
    footprint_token,
    peak_footprint_self_bytes,
    peak_rss_self_bytes,
    process_peak_rss_bytes,
    rss_token,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
PUNCTUATION_YAML = REPO_ROOT / "glyph_data" / "punctuation.yaml"
CONTACT_ALLOW_YAML = REPO_ROOT / "rebuild" / "m1-contact-allow.yaml"

RAW_STANCE = "cmap"


def _spawn_pool(
    jobs: int,
    units: int,
    *,
    max_tasks_per_child: int | None = None,
    initializer: Callable[..., None] | None = None,
    initargs: tuple = (),
) -> ProcessPoolExecutor:
    """Return a spawn pool of at most `jobs` workers and at most `units`, the number of tasks the caller submits besides its absorbs. The conformance sweep submits one task per unit (`run_m1_gates.sweep_units`) and the oracle one per row range, and both pass `max_tasks_per_child=1`, so each unit or range and each settle memo absorb runs in a fresh process and reports its own peak. A full string replay submits one task per configuration to a pool as wide, with an `initializer` that hands each worker the replay's shared state (`_replay_worker_init`). The witness stage submits one task per configuration, one at a time, to a pool of one worker, whose `initializer` hands it what every configuration shares (`_rule_witnesses_init`)."""
    workers = max(1, min(jobs, units))
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        max_tasks_per_child=max_tasks_per_child,
        initializer=initializer,
        initargs=initargs,
    )


def memo_structure_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the table build's structure stamp, the `structure` its `TableBuildRecord` carries: what the crate reads apart from each rune's content and each predicate class's membership. The crate reads nothing but the spec dump and its command line (`kernel_exec.build_table_files`). The lines are the code `table_build_code_paths` names (the crate, and the module-grain import closure of `kernel_exec` and `kernel_io` outside rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, so the width and telemetry helpers that closure stops at are not hashed), the dumped registry with each class reduced to its name (`_registry_digest`), the rune order, the semantics tokens, the alphabet with its ligature sequences and the resolved rune-local groups, and the capability features. The rest of `fingerprint.table_code_paths` and the data files reach the crate, if at all, only through the dump, or through command-line values that change no table: the width, which the crate's unit test `a_table_fan_out_files_the_same_bytes_at_every_width` (rebuild/kernel-rs/src/fanout.rs) shows changes no table; the schedule flags, which the identity tests in rebuild/kernel-rs/tests/cli.rs and rebuild/test_kernel_exec.py show change no table; and `--inputs` and `--timings`, which only label or time what the crate writes. So an edit to the spec loader, to an emitter, to read-back or to run_m1.py moves none of these code lines, and it moves the stamp only where it changes the dump or what the stamp's own code in run_m1.py and `spec_load.capability_features` compute from it; `replay_structure_stamp` hashes that code instead."""
    from rebuild.pipeline import spec_load

    lines = [
        *fingerprint.path_lines(root, table_build_code_paths(root)),
        f"registry\t{_registry_digest(spec)}",
        "rune_order\t" + ",".join(spec.runes),
        "semantics\t" + "+".join(kernel_exec.enumeration_tokens()),
        f"structure\t{_classless_structure_digest(spec)}",
        "capabilities\t" + ",".join(spec_load.capability_features(spec)),
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _registry_digest(spec: ResolvedSpec) -> str:
    """The SHA-256 of the registry's part of the spec dump (`kernel_io.registry_payload`) with each predicate class reduced to its name, in dump order. Membership stays out, so a rune joining a class leaves the structure stamp where it was. The keys keep the dump's order, because the crate stores every mapping in that order (rebuild/kernel-rs/src/model.rs)."""
    registry = kernel_io.registry_payload(spec.registry)
    payload = {**registry, "predicate_classes": list(registry["predicate_classes"])}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


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


TABLE_BUILD_CODE_MODULES = frozenset(
    {
        "rebuild/pipeline/kernel_exec.py",
        "rebuild/pipeline/kernel_io.py",
        "rebuild/pipeline/labels.py",
        "rebuild/pipeline/model.py",
        "rebuild/pipeline/output_sink.py",
        "rebuild/pipeline/row_delta.py",
        "rebuild/pipeline/settle.py",
        "rebuild/pipeline/table.py",
    }
)


def table_build_code_paths(root: Path = REPO_ROOT) -> list[Path]:
    """Return the code that `memo_structure_stamp` and a `TableBuildRecord`'s `code` digest both hash: the crate's manifest, lock and sources, and the modules `TABLE_BUILD_CODE_MODULES` names, as repo-relative paths. That roster is the module-grain import closure of the crate driver (`kernel_exec`, whose `build_table_files` runs the crate and is the whole timed `kernel_build_tables` span) and of the spec dump's writer (`kernel_io`), outside rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`. Being an import closure, it also holds modules the two import for work other than running the crate and writing the dump, and hashing those only moves the stamp and `code` more often. rebuild/test_build_code_closure.py checks the roster against that closure in both directions. The rest of `fingerprint.table_code_paths` reaches the crate, if at all, only through the dump, or through command-line values that change no table (`memo_structure_stamp` names them). So an edit there moves the structure stamp only where it changes the dump or the code that computes the stamp: the stamp's own code in run_m1.py, `spec_load.capability_features`, and the file digests `fingerprint.path_lines` takes. A letter batch's `baseline_subset.M1_ALPHABET` edit and a gate edit such as one to `conform` keep `code`, so a build before a batch can be paired with the first build after it."""
    kernel = root / "rebuild" / "kernel-rs"
    named = {root / relative for relative in TABLE_BUILD_CODE_MODULES}
    return [path for path in fingerprint.table_code_paths(root) if kernel in path.parents or path in named]


@dataclass(frozen=True)
class TableBuildRecord:
    """What the timings journal records of a table build into an `out_dir`, so that two builds with the same code and a different alphabet can be paired across a letter batch: the structure stamp (`memo_structure_stamp`, which hashes the code `code` covers beside the dumped registry, the rune order, the semantics tokens, the alphabet and the capability features, so under one `code` it moves with the spec, with the mode set, or with an edit to its own code in run_m1.py or to `spec_load.capability_features`, which `code` does not cover), the digest of the code the table build runs (`table_build_code_paths`, through `fingerprint.code_file_digest`, which drops its Python files' docstrings and its Rust files' whole-line comments and hashes the crate's manifest and lock as raw bytes), the table build's width, and the number of runes in the alphabet. Every build enumerates cold, since none reads a memo an earlier build wrote. `tokens` writes the record as the `key=value` tail of the `[t] kernel_build_tables` line, and `cycle_timings.parse_inner_timings` reads each token back under its key."""

    structure: str
    code: str
    width: int
    runes: int

    def tokens(self) -> str:
        return " ".join(
            [
                f"structure={self.structure}",
                f"code={self.code}",
                f"width={self.width}",
                f"runes={self.runes}",
            ]
        )


def table_build_record(spec: ResolvedSpec, width: int, root: Path = REPO_ROOT) -> TableBuildRecord:
    """Return the `TableBuildRecord` of a build of `spec` at `width`. `build_tables` takes the record before the crate starts, so an edit made while the crate runs cannot give `structure` or `code` a tree the build did not run."""
    return TableBuildRecord(
        structure=memo_structure_stamp(spec, root),
        code=fingerprint.hash_paths(root, table_build_code_paths(root)),
        width=width,
        runes=len(spec.runes),
    )


def _table_build_tail(record: TableBuildRecord | None, peaks: ReapedPeaks) -> str | None:
    """Return the tail of the `[t] kernel_build_tables` line: the build's `TableBuildRecord` tokens when it has one, then the `build-tables` process's own resident peak and peak footprint (`rss_token`, `footprint_token`), each where it was read. The kernel-build row of `make job-costs` reads the footprint beside the run_m1 step's peak."""
    tokens = [record.tokens()] if record is not None else []
    if peaks.resident is not None:
        tokens.append(rss_token(peaks.resident))
    if peaks.footprint is not None:
        tokens.append(footprint_token(peaks.footprint))
    return " ".join(tokens) or None


# The phase lines of the build and the pooled oracle this invocation ran, for its check line: the crate's `[t]` lines, the `kernel_build_tables` line with its `TableBuildRecord` tokens and the `build-tables` process's resident peak and peak footprint, then the `readback` line with the settlement lookup's figures and the commit, and the pooled oracle's `oracle_fan_in` line with what its fan-in held in shared memory. A cycle's run_m1 step parses the same lines from the output it captured, but a standalone check line has none, so `build_tables`, the glyph chain and `run_m1_gates.run_oracle` keep them here, `run_m1_gates.main` empties the list when an invocation starts, and `run_m1_gates._record_cli_check` parses it.
_build_phase_lines: list[str] = []


def head_commit(root: Path = REPO_ROOT) -> str | None:
    """Return HEAD's short commit id, or None when git cannot name one. It names the commit the working tree was at, not the tree itself: a build of uncommitted edits, such as the pass made before a commit, records the commit those edits sit on."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None


def readback_tokens(report: Mapping, commit: str | None) -> str:
    """Return the `key=value` tail of the `[t] readback` line: `commit`, then the settlement lookup's figures from `readback.budget_figures`. `cycle_timings.parse_inner_timings` reads each token back under its key (`cycle_timings.READBACK_TOKENS`). A commit git could not name, and a figure the report lacks, are left out."""
    figures = {"commit": commit, **readback.budget_figures(report)}
    return " ".join(f"{name}={value}" for name, value in figures.items() if value is not None)


def _table_build_threads(
    kernel_threads: int | None, configs: int = len(conform.SETTLEMENT_CONFIGS), from_scratch: bool = False
) -> int:
    """Return the table build's width for a build of `configs` configurations: `kernel_threads`, or the memory-derived `kernel_exec.kernel_threads_default`, which parks one fold product per configuration and books each slot from scratch when `from_scratch` says no configuration shares `default`'s memo, capped at the settlement configuration count and the cores this process may run on. The string replay uses `_replay_threads` instead."""
    return max(
        1,
        min(
            kernel_threads or kernel_exec.kernel_threads_default(configs=configs, from_scratch=from_scratch),
            len(conform.SETTLEMENT_CONFIGS),
            usable_cores(),
        ),
    )


def _replay_threads(replay_threads: int | None) -> int:
    """Return the string replay's width, the number of walks that run at once: `replay_threads`, or `kernel_exec.replay_threads_default()` (the machine's memory divided by `REPLAY_PEAK_BYTES`, one walk and its absorb, with nothing co-resident subtracted), capped at the settlement configuration count and the cores this process may run on, the same three terms as `_table_build_threads`. A full replay gates its walks' crate processes to this width (`replay_and_absorb`), and a narrowed one runs this many walks in its one crate process. The replay does not reuse the table build's width because the two stages have different memory costs. The build's width subtracts `default`'s live memo and divides by a delta's peak during enumeration, while each replay walk and its absorb hold a fraction of a delta and start after the build process has exited. Where the build's width is below the configuration count, reusing it would split the replay into two rounds, and the oracle waits for the replay (`settle_memo_wait`). `AMS_REPLAY_THREADS` and `--replay-threads` set this width; `AMS_KERNEL_THREADS` and `--kernel-threads` do not affect it. The caps only narrow a width and never widen one."""
    return max(
        1,
        min(
            replay_threads or kernel_exec.replay_threads_default(),
            len(conform.SETTLEMENT_CONFIGS),
            usable_cores(),
        ),
    )


def _core_bound_threads(count: int) -> int:
    """Return the width of a per-configuration pool whose tasks are CPU-bound and hold nothing the memory-derived widths account for: one task per settlement configuration, capped at the cores this process may run on, and at least one. It sizes the packing, whose tasks each commit one configuration's windows head over its staged head file and the part its payload's reader wrote (`_pack_config`), and the shipped-order walks. The window payloads' readers (`_take_windows`) run outside this pool, one a configuration for the crate's whole run (`build_tables`), because each must read its FIFO for as long as the crate writes it; each holds a zlib stream and a copy buffer, and `_pack_windows` over the plain `default` enumeration from a `ThreadPoolExecutor` measured 1.26s, 1.22s and 1.30s per task at widths 1, 3 and 5, with maxrss 0.044, 0.054 and 0.062 GB, so it scales flat. A shipped-order walk is one single-threaded crate process that holds the rules and labels and streams the rows, 0.105 GB of child RSS, a small fraction of the table build's peak. `AMS_KERNEL_THREADS` and `--kernel-threads` do not affect these pools: that setting exists to keep the table build out of swap, so a build set to width one narrows the crate's delta round while the packing and the walks still run every configuration at once. The string replay's setting (`_replay_threads`) affects neither pool."""
    return max(1, min(count, usable_cores()))


class Packing:
    """One table build's packing once its crate has exited cleanly, on a thread pool that can outlive `build_tables`: each configuration's staged head file renamed into place and its windows head committed over the part its payload's reader wrote (`_pack_config`). Each configuration's pack is a future keyed by the configuration's name, so a stage that reads a configuration's rows (`run_emitted_order`'s walks) waits for its own configuration's commit through `wait` instead of for the whole build. `build_tables` opens and closes its own `Packing` when the caller passes none, which blocks until packing finishes. `run` passes one, and the table-only branch closes it after the last walk that reads a committed head. `close` waits for every pack, shuts the pool down, reports `[t] pack_windows_total` (from the first submit to the last pack's completion, since the pool stays open across the kernel run and the walks), and raises the first pack failure."""

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


SEGMENT_ROUTE = "segment"
BASE_ROUTE = "base"


@dataclass
class WindowsRoute:
    """How one configuration's windows rows are written this build, decided from its head before the crate starts (`_plan_windows`), as #618's decisions 74 and 75 settle it, with no size test and no stamp clause:

    - `segment`: the rows that differ from `prior`'s, whose parts stay `opened` so that a thread a part can feed them to the crate (`prior_parts`). A build whose rows all come out the same writes the head file and the head alone.
    - `base`: the whole payload as the next generation's base, with its `reason`: `first`, no head and no single file; `unusable`, a head, a part or a single file that does not read, which the cycle reports as `rewritten whole` (`output_sink.note_rewritten`); or `fold`, `prior`'s fold is due and this configuration holds the pass's fold (`row_delta.claim_fold`), or holds `row_delta.FOLD_OVERRUN` segments past its cap (`forced`), so it folds whatever another output did.

    `adopted` says `prior` is the single file a tree from before the segment format holds, linked as generation 0's base, which no head lists until the commit. The payload's reader (`_take_windows`) stages the head file at `staged` and writes the base or the segment under its final name, kept in `part`; `_pack_config` commits them. A build that fails deletes them (`discard`).
    """

    config: str
    tables_dir: Path
    route: str
    reason: str = ""
    prior: row_delta.Head | None = None
    opened: row_delta.OpenParts | None = None
    adopted: bool = False
    forced: bool = False
    staged: Path | None = None
    part: row_delta.Part | None = None

    @property
    def output(self) -> str:
        return table_module.windows_output(self.config)

    def part_name(self) -> str:
        """The name the payload's reader writes this build's part under: the next segment of `prior`'s generation, or the next generation's base."""
        if self.route == SEGMENT_ROUTE:
            assert self.prior is not None
            return row_delta.segment_name(self.output, self.prior.generation, len(self.prior.segments) + 1)
        return row_delta.base_name(self.output, 0 if self.prior is None else self.prior.generation + 1)

    def prior_parts(self) -> list[kernel_exec.PriorPart]:
        """The parts the crate merges this configuration's rows against, base first, each fed from its open handle with its header lines dropped by the same check the plan ran on it (`_header_checks`); none off the segment route."""
        if self.route != SEGMENT_ROUTE:
            return []
        assert self.prior is not None and self.opened is not None
        return [
            kernel_exec.PriorPart.gunzipped(part.name, part.rows, handle, check)
            for part, handle, check in zip(
                self.prior.parts, self.opened.handles, _header_checks(self.prior), strict=True
            )
        ]

    def head(self, stamps: dict) -> row_delta.Head:
        """The head this build commits: `prior` with the new segment, `prior` alone under this build's stamps when no row differed, or the new base."""
        if self.route == SEGMENT_ROUTE:
            assert self.prior is not None
            if self.part is None:
                return replace(self.prior, stamps=stamps)
            return row_delta.with_segment(self.prior, self.part, stamps)
        assert self.part is not None
        generation = 0 if self.prior is None else self.prior.generation + 1
        content = 0 if self.prior is None else self.prior.content + 1
        return row_delta.Head(self.output, generation, content, stamps, self.part, (), row_delta.MERGE_KEY)

    def note(self, committed: row_delta.Head) -> str:
        """The tail of `[t] pack_windows[<config>]`: what the build wrote (`wrote=head|segment|base`), a base's reason, whether the base it merged against was adopted, whether a fold came at its overrun, the part's rows, their plain bytes (`rows_bytes`) and the part's packed bytes on disk (`packed_bytes`), the head file's packed bytes, which every stamped build writes, and the head's segments after the commit."""
        wrote = "head" if self.route == SEGMENT_ROUTE and self.part is None else self.route
        tokens = [f"wrote={wrote}"]
        if self.route == BASE_ROUTE:
            tokens.append(f"reason={self.reason}")
        if self.adopted:
            tokens.append("adopted=true")
        if self.forced:
            tokens.append("overrun=true")
        if self.part is not None:
            tokens.append(
                f"rows_written={self.part.rows} rows_bytes={self.part.rows_bytes} packed_bytes={self.part.size}"
            )
        tokens.append(f"head_file_bytes={committed.stamps[table_module.HEAD_FILE_STAMP]['size']}")
        tokens.append(f"segments={len(committed.segments)}")
        return " ".join(tokens)

    def close(self) -> None:
        if self.opened is not None:
            self.opened.close()
            self.opened = None

    def discard(self) -> None:
        """Delete what this build staged or wrote under a name no head lists, for a build that failed: the staged head file, the part, and an adopted single file's link."""
        self.close()
        if self.staged is not None:
            self.staged.unlink(missing_ok=True)
        (self.tables_dir / self.part_name()).unlink(missing_ok=True)
        if self.adopted:
            (self.tables_dir / row_delta.base_name(self.output, 0)).unlink(missing_ok=True)


def _adopted_base(tables_dir: Path, config: str) -> row_delta.Head | None:
    """Put the single file a tree from before the segment format holds under an uncommitted head, as generation 0's base, and return that head, or None when the file does not read: a hard link, which writes no data, beside a count of its rows and row bytes, which reads the file through once with its gzip trailer checked and its first two lines checked as a payload's head and column lines (`table.check_windows_header`). The head is committed only with this build's own head (`_pack_config`); a build that fails deletes the link (`WindowsRoute.discard`)."""
    output = table_module.windows_output(config)
    single = table_module.windows_path(tables_dir, config)
    rows = rows_bytes = 0
    try:
        with gzip.open(single, "rb") as stream:
            table_module.check_windows_header(stream, single.name)
            while chunk := stream.read(1 << 20):
                rows += chunk.count(b"\n")
                rows_bytes += len(chunk)
    except OSError, EOFError, ValueError, zlib.error:
        return None
    name = row_delta.base_name(output, 0)
    os.link(single, tables_dir / name)
    base = row_delta.Part(
        name, (tables_dir / name).stat().st_size, row_delta.file_sha256(tables_dir / name), rows, rows_bytes
    )
    return row_delta.Head(output, 0, 0, {}, base, (), row_delta.MERGE_KEY)


def _route_of(tables_dir: Path, config: str) -> WindowsRoute:
    """One configuration's route before any fold is claimed: `segment` over a head whose every part opens, hashes to what the head records and passes its header check, or over the adopted single file; otherwise a base, `first` or `unusable`. Every header check a part's feeder makes runs here first (`_header_checks`, `_parts_read`), so a part an earlier format wrote, which still hashes to its head, routes to a new base instead of failing the build and every build after it. The parts no head lists and the staging files a killed writer left are deleted first (`row_delta.remove_unlisted`, `row_delta.remove_staging`)."""
    output = table_module.windows_output(config)
    row_delta.remove_staging(tables_dir, output)
    try:
        head = row_delta.read_head(tables_dir, output)
    except row_delta.HeadError:
        row_delta.remove_unlisted(tables_dir, output, None)
        return WindowsRoute(config, tables_dir, BASE_ROUTE, "unusable")
    row_delta.remove_unlisted(tables_dir, output, head)
    if head is None:
        if not table_module.windows_path(tables_dir, config).exists():
            return WindowsRoute(config, tables_dir, BASE_ROUTE, "first")
        adopted = _adopted_base(tables_dir, config)
        if adopted is None:
            return WindowsRoute(config, tables_dir, BASE_ROUTE, "unusable")
        opened = row_delta.open_parts(tables_dir, adopted)
        return WindowsRoute(config, tables_dir, SEGMENT_ROUTE, prior=adopted, opened=opened, adopted=True)
    if head.merge != row_delta.MERGE_KEY:
        return WindowsRoute(config, tables_dir, BASE_ROUTE, "unusable", prior=head)
    try:
        opened = row_delta.open_parts(tables_dir, head, sha256=True)
    except row_delta.HeadError:
        return WindowsRoute(config, tables_dir, BASE_ROUTE, "unusable", prior=head)
    if not _parts_read(opened, _header_checks(head)):
        opened.close()
        return WindowsRoute(config, tables_dir, BASE_ROUTE, "unusable", prior=head)
    return WindowsRoute(config, tables_dir, SEGMENT_ROUTE, prior=head, opened=opened)


def _header_checks(head: row_delta.Head) -> list[Callable[[gzip.GzipFile], object]]:
    """Each part's header check, base first: a callable that reads the lines before a part's rows from its decompressed stream and raises unless they are what `head` expects, the base's head line's marker and column line (`table.check_windows_header`) and each segment's header line (`row_delta.check_segment_header`). The plan runs every one before it fixes the route (`_parts_read`), and each part's feeder runs its own again to drop those lines (`WindowsRoute.prior_parts`), so the one list holds every check a feeder makes and no feeder refuses a part the plan let through."""

    def base(stream: gzip.GzipFile) -> None:
        table_module.check_windows_header(stream, head.base.name)

    def segment(number: int, stream: gzip.GzipFile) -> None:
        row_delta.check_segment_header(stream.readline(), head.output, head.generation, number, head.merge)

    return [base, *(functools.partial(segment, number) for number in range(1, len(head.segments) + 1))]


def _parts_read(opened: row_delta.OpenParts, checks: Sequence[Callable[[gzip.GzipFile], object]]) -> bool:
    """Whether every open part passes its header check (`_header_checks`), each decompressing only the lines before its rows; every handle is left at its start for its feeder."""
    for handle, check in zip(opened.handles, checks, strict=True):
        try:
            with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
                check(stream)
        except OSError, EOFError, ValueError, zlib.error:
            return False
        finally:
            handle.seek(0)
    return True


def _plan_windows(tables_dir: Path, configs: Sequence[str]) -> dict[str, WindowsRoute]:
    """Decide every configuration's windows route before the crate starts (`WindowsRoute`), and print `[t] windows_plan` with how many take each route (`segment_routes=`, `base_routes=`) and how many adopt a single file (`adopting=`). Each configuration's head is read and its parts hashed, or its single file counted, on a thread of its own, since zlib and the hashes release the interpreter lock. Then the pass's fold is claimed (`row_delta.claim_fold`) among the configurations whose fold is due, the one with the most segments first and then in configuration order: the one that holds it folds, and so does any at its overrun (`row_delta.fold_forced`), and the rest write a segment and wait."""
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(configs)), thread_name_prefix="windows-plan") as pool:
        routes = dict(zip(configs, pool.map(functools.partial(_route_of, tables_dir), configs)))
    order = {config: position for position, config in enumerate(configs)}
    due = sorted(
        (
            route
            for route in routes.values()
            if route.route == SEGMENT_ROUTE and route.prior is not None and row_delta.fold_due(route.prior)
        ),
        key=lambda route: (-len(route.prior.segments) if route.prior is not None else 0, order[route.config]),
    )
    for route in due:
        assert route.prior is not None
        holds = row_delta.claim_fold(route.output)
        if holds or row_delta.fold_forced(route.prior):
            route.close()
            route.route, route.reason, route.forced = BASE_ROUTE, "fold", not holds
    counts = {
        kind: sum(route.route == kind for route in routes.values()) for kind in (SEGMENT_ROUTE, BASE_ROUTE)
    }
    console.timing(
        "windows_plan",
        time.perf_counter() - started,
        f"segment_routes={counts[SEGMENT_ROUTE]} base_routes={counts[BASE_ROUTE]} adopting={sum(route.adopted for route in routes.values())}",
    )
    return routes


def _pack_config(
    tables_dir: Path, config: str, route: WindowsRoute | None, inputs: str | None, digest: str
) -> None:
    """Finish one configuration's packing after the crate's clean exit, when the build is stamped (`route`): rename the head file its payload's reader staged (`_take_windows`) into place, commit the windows head over it and the part the reader wrote (`row_delta.commit`, which deletes the generation a fold superseded), and delete the single file a tree from before the segment format held. The head's stamps are the build's `inputs`, the crate's `digest` over the table's own rows and the head file's name, size and sha256. A base written because a head, a part or a single file did not read is counted as `rewritten whole` (`output_sink.note_rewritten`). Prints `[t] pack_windows[<config>]` with what it wrote (`WindowsRoute.note`)."""
    started = time.perf_counter()
    if route is None:
        console.timing(f"pack_windows[{config}]", time.perf_counter() - started)
        return
    assert route.staged is not None, f"{config}: the crate exited cleanly without writing its payload"
    head_file = table_module.windows_head_file(tables_dir, config)
    os.replace(route.staged, head_file)
    stamps = {
        table_module.INPUTS_STAMP: inputs,
        table_module.TABLE_DIGEST_STAMP: digest,
        table_module.HEAD_FILE_STAMP: {
            "name": head_file.name,
            "size": head_file.stat().st_size,
            "sha256": row_delta.file_sha256(head_file),
        },
    }
    committed = route.head(stamps)
    if route.reason == "unusable":
        output_sink.note_rewritten(route.output)
    row_delta.commit(tables_dir, committed)
    table_module.windows_path(tables_dir, config).unlink(missing_ok=True)
    console.timing(f"pack_windows[{config}]", time.perf_counter() - started, route.note(committed))


def _copy_counting(source: IO[bytes], sink: row_delta.Sink, first: bytes = b"") -> tuple[int, int]:
    """Copy `first` and then the rest of `source` into `sink` a megabyte at a time, and return the lines and bytes copied."""
    rows, size = first.count(b"\n"), len(first)
    sink.write(first)
    while chunk := source.read(1 << 20):
        sink.write(chunk)
        rows += chunk.count(b"\n")
        size += len(chunk)
    return rows, size


def _take_windows(source: IO[bytes], route: WindowsRoute | None) -> bytes:
    """Read one configuration's window payload from its FIFO to the end and return its head line, the one line `table.read_windows` reads with `windows=False`. With `route`, the build is stamped: the head line is staged as the head file, gzipped as a base is (`_stage_head_file`), and the rest is written as the route says, a segment of the rows that differ, under its header (`row_delta.segment_header`) and only when some row differs, or a base, the whole payload through `_pack_windows`, each under its final name, which no head lists until `_pack_config` commits it. Without it, the rest is read and dropped. An empty stream, from a crate that never opened the FIFO, writes nothing."""
    head = source.readline()
    if route is None or not head:
        while source.read(1 << 20):
            pass
        return head
    route.staged = _stage_head_file(route.tables_dir, route.config, head)
    if route.route == BASE_ROUTE:
        route.part = _pack_windows(source, route.tables_dir, route.part_name(), head)
        return head
    first = source.read(1 << 20)
    if first:
        prior = route.prior
        assert prior is not None

        def fill(stream: row_delta.Sink) -> tuple[int, int]:
            stream.write(
                row_delta.segment_header(
                    prior.output, prior.generation, len(prior.segments) + 1, row_delta.MERGE_KEY
                )
            )
            return _copy_counting(source, stream, first)

        route.part = row_delta.write_part(route.tables_dir, route.part_name(), fill)
    return head


def _head_file_staging(tables_dir: Path, config: str) -> Path:
    """The name one configuration's head file is staged under, the head file's with `.tmp` added: every stamped build of the configuration writes it again, and `config_table_files` names it, so neither a killed build nor a configuration that leaves the set leaves it behind."""
    path = table_module.windows_head_file(tables_dir, config)
    return path.with_name(path.name + ".tmp")


def _stage_head_file(tables_dir: Path, config: str, head: bytes) -> Path:
    """Write one configuration's head file, its payload's head line gzipped as `_pack_windows` packs a base, under a staging name `_pack_config` renames into place, and return that name (`_head_file_staging`)."""
    staged = _head_file_staging(tables_dir, config)
    with (
        staged.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as packed,
    ):
        packed.write(head)
    return staged


def build_tables(
    spec: ResolvedSpec,
    out_dir: Path | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
    packing: Packing | None = None,
    configs: Sequence[str] = conform.SETTLEMENT_CONFIGS,
    scratch_beside_default: int | None = None,
) -> tuple[dict[str, tuple], dict[str, str]]:
    """Build the decision and join tables for the named settlement configurations, all of them unless `configs` narrows the set. The resolved spec is dumped once, into a pipe one crate `build-tables` process inherits and reads it from (`kernel_exec.build_table_files`), so the dump never reaches the disk. That process enumerates `default`'s fixpoint and folds it, then enumerates each other configuration as a delta over `default`'s finished memo, or from scratch beside `default` (`scratch_beside_default`), prepares its fold as it finishes, and, once every configuration has exchanged the windows the others keep live (`rebuild/kernel-rs/src/crossconfig.rs`), finishes each fold. The crate writes the settlement TSV, the join TSV and the window payload itself; nothing is folded on the Python side.

    A narrowed set is for `rebuild/tools/scratch_build.py`, which searches for a record to change. The crate writes only the configurations it is asked for and deletes nothing, so a narrowed build into a directory another build wrote leaves that build's files for the other settlement configurations in place. `scratch_build.scratch_out_dir` keeps such builds out of `rebuild/out/m1`. A narrowed set writes the same windows and join tables as the whole set, but its settlement tables can differ, since it has fewer configurations to import windows from. A set that does not include `default` enumerates each member from scratch, since there is no finished memo to build a delta over, so its width is derived from the from-scratch bound. The build and the artifact cycle ask for the whole set. Overlay configurations get no tables. Any table files named for a configuration outside `conform.SETTLEMENT_CONFIGS`, an overlay configuration's or one that has left the set, are removed first (`stale_table_files`), so a whole-set build leaves only its own tables in the directory.

    Each configuration's windows rows are an output in the segment format, beside a head file (`table`'s module docstring), and a stamped build writes each configuration's rows as its route says (`WindowsRoute`), decided before the crate starts (`_plan_windows`): with no size test and no stamp clause, so a letter batch and a structure-stamp move write a segment like any other pass (#618's decisions 74 and 75). On the segment route the parts the head lists are opened with their sha256 checked before the crate starts, and a thread a part decompresses each into a pipe the crate inherits, for the crate's whole run, outside the `Packing` pool (`kernel_exec.PriorPart`), so the crate merges the configuration's rows against them at its finish and writes only the rows that differ. A tree from before the segment format holds each configuration's whole payload in a single file, which the first build to find it and no head adopts as generation 0's base by hard link, counting its rows once before the crate starts (`_adopted_base`), and then merges against like any other base. The plan raises the descriptor limit first (`kernel_exec.raise_descriptor_limit`), since at six configurations each at a base and twenty segments it holds a part handle, a pipe's write end and, until the crate is spawned, its read end, for each of 126 parts.

    The window payload never reaches the disk as plain text. Before the crate starts, a FIFO stands at each configuration's payload path (`kernel_exec.stream_payloads`), and one thread a configuration, outside the `Packing` pool and whatever the crate's width, reads it for the crate's whole run (`_take_windows`): it keeps the head line, stages it as the head file and, when the build is stamped, gzips the rest into the configuration's next segment or base as the crate writes it, at compressor speed, since a packer holds only a zlib stream and a copy buffer and the compressor releases the interpreter lock. A part is written under its final name, which no head lists until the commit, and a part no head lists is deleted by the next build. Each payload's end of file comes with the crate's close of it. Once the crate has exited, any FIFO it never opened is opened and closed so that no reader waits forever, and an empty stream packs nothing. Only after the crate's clean exit, with every reader finished, is each staged head file renamed into place and each windows head committed over it (`_pack_config`), as `run_m1_gates` promotes the oracle's row stores, so a build whose crate or any reader fails, or that is interrupted before the crate's clean exit, deletes what it staged and wrote and leaves the previous build's head file, head and parts in place. A reader's failure fails the build, as the cause of the crate's broken pipe when the reader stopped early. Per configuration, Python then parses the kept head for the rules, the reachable cells and the fired provenance every downstream stage needs, and parses the join TSV back for the defect gates, on a thread pool at the table build's width, and this call waits for them. The commit (`_pack_config`) runs on a `Packing` pool at `_core_bound_threads` width, one task per configuration up to the cores. When `packing` is passed, the tables are returned as soon as the heads are parsed, and the caller waits on each commit through `Packing.wait` and closes the pool. Without it, packing finishes before the return.

    No build reads or writes a memo file: `default`'s memo lives only inside the crate's process, so every build enumerates cold. A build with an `out_dir` ends its `[t] kernel_build_tables` line with its `TableBuildRecord`; a caller with no `out_dir` carries no record. Every build's line then carries the `build-tables` process's own resident peak and peak footprint (`_table_build_tail`). Either way the crate's phase lines and that line are kept in `_build_phase_lines` for run_m1's check line.

    `out_dir`, when given, receives the TSVs listed in `doc/rebuild-design.md` §8. The second returned mapping is each configuration's `table.table_digest` as the crate reported it, computed while the window rows are still in memory, which avoids recomputing the fixpoint, and recorded in each windows head. The crate also prints it on stdout, where `rebuild/tools/scaling_sweep.py` reads it. Both returned mappings are built in `configs` order however the configurations finish, so completion order cannot affect an artifact.

    `inputs` is `tables_inputs` over the sources this spec was loaded from. Passing it with `out_dir` keeps each configuration's window rows and head file beside the TSVs under the stamp that names those sources, where `run_m1_gates.run_font_conformance` reads it instead of rebuilding anything. Without it, each reader keeps the head and drops the rest of the payload, and the crate writes every payload whole. A caller building its own spec must omit it, because the fingerprint names the repository's rune files and does not describe tables built from other runes.

    `kernel_threads` is how many worker slots the build runs at once, each enumerating a delta configuration or running `default`'s fold preparation, capped at the configuration count and the cores this process may run on, neither of which is a memory limit. The default it falls back to is the memory limit: `kernel_exec.kernel_threads_default` is the widest width whose `kernel_exec.table_build_booking_bytes` fits this machine's memory, which books what `default`'s finished memo holds, one parked fold product per configuration this build names, and what each slot holds beyond its own parked product. So the cap only narrows a memory-derived width and never widens one.

    `scratch_beside_default` says at most how many of the heaviest deltas the crate enumerates from scratch beside `default` instead of over its memo, so they need not wait for it; the crate takes them in whole tiers of equal unlocking-rune count. The artifact cycle passes the count it derives beside gate:make-test's pytest pool; when it is `None`, `kernel_exec.deltas_from_scratch` derives it at the width the build takes with nothing beside it: as many as fit the memory with each booked at `kernel_exec.SCRATCH_PEAK_BYTES`.
    """
    configs = tuple(configs)
    from_scratch = "default" not in configs
    threads = _table_build_threads(kernel_threads, len(configs), from_scratch=from_scratch)
    if scratch_beside_default is None:
        scratch_beside_default = kernel_exec.deltas_from_scratch(
            threads, configs=len(configs), from_scratch=from_scratch
        )
    kernel_exec.ensure_built()
    built: dict[str, tuple] = {}
    digests: dict[str, str] = {}
    deferred = packing is not None
    packers = packing if packing is not None else Packing(_core_bound_threads(len(configs)))
    keep_windows = out_dir is not None and inputs is not None
    spec_bytes = kernel_io.spec_bytes(spec)
    with ExitStack() as scratch:
        tables_dir = (
            out_dir if out_dir is not None else Path(scratch.enter_context(tempfile.TemporaryDirectory()))
        )
        tables_dir.mkdir(parents=True, exist_ok=True)
        for path in stale_table_files(tables_dir):
            path.unlink(missing_ok=True)

        record = table_build_record(spec, threads) if out_dir is not None else None
        routes: dict[str, WindowsRoute] = {}
        try:
            if keep_windows:
                kernel_exec.raise_descriptor_limit()
                routes = _plan_windows(tables_dir, configs)
            start = time.perf_counter()
            crate, kept = kernel_exec.stream_payloads(
                {config: kernel_exec.windows_payload(tables_dir, config) for config in configs},
                lambda config, source: _take_windows(source, routes.get(config)),
                lambda: kernel_exec.build_table_files(
                    spec_bytes,
                    tables_dir,
                    list(configs),
                    inputs=inputs if inputs is not None else kernel_exec.UNSTAMPED_WINDOWS,
                    threads=threads,
                    symbols=len(conform.spec_alphabet(spec)),
                    timings=True,
                    scratch_beside_default=scratch_beside_default,
                    previous={config: route.prior_parts() for config, route in routes.items()},
                ),
            )
        except BaseException:
            for route in routes.values():
                route.discard()
            raise
        finally:
            for route in routes.values():
                route.close()
        digests = crate.digests
        line = console.timing(
            "kernel_build_tables", time.perf_counter() - start, _table_build_tail(record, crate.peaks)
        )
        _build_phase_lines[:] = [*crate.timings, line]

        def read_one(config: str) -> tuple[str, tuple]:
            head = kernel_exec.payload_head(kept[config], kernel_exec.windows_payload(tables_dir, config))
            _stamp, decision = table_module.read_windows(head, windows=False)
            joins = table_module.read_join_tsv(tables_dir / f"joins-{config}.tsv")
            return config, (decision, joins)

        try:
            if out_dir is not None:
                for config in configs:
                    packers.submit(
                        config,
                        functools.partial(
                            _pack_config, tables_dir, config, routes.get(config), inputs, digests[config]
                        ),
                        len(configs),
                    )
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


_TABLE_FILE = re.compile(
    r"(?:settlement|joins)-(?P<table>.+)\.tsv"
    r"|windows-(?P<single>.+)\.tsv\.gz"
    r"|windows-(?P<head_file>.+)\.head\.json\.gz(?:\.tmp)?"
    r"|windows-(?P<rows>.+)\.tsv\.gz\.(?:head|\d+\.base|\d+\.\d+\.delta)"
    rf"(?:{re.escape(output_sink.STAGING_SUFFIX)})?"
)


def config_table_files(tables_dir: Path, config: str) -> tuple[Path, ...]:
    """Return the table files a settlement configuration has under `tables_dir`, named for `config`: its settlement and join tables, its windows head and head file, the head file's staging name, every part a readable head lists, every other part named for it (`row_delta.remove_unlisted` deletes the unlisted ones too), the staging file a killed writer left of a part or the head (`row_delta.remove_staging` deletes those too) and the single file a tree from before the segment format holds, where it remains."""
    tables_dir = Path(tables_dir)
    output = table_module.windows_output(config)
    named = [
        tables_dir / f"settlement-{config}.tsv",
        tables_dir / f"joins-{config}.tsv",
        table_module.windows_head_path(tables_dir, config),
        table_module.windows_head_file(tables_dir, config),
        table_module.windows_path(tables_dir, config),
        _head_file_staging(tables_dir, config),
    ]
    try:
        head = row_delta.read_head(tables_dir, output)
    except row_delta.HeadError:
        head = None
    listed = [tables_dir / part.name for part in head.parts] if head is not None else []
    stray = sorted(
        path
        for path in tables_dir.glob(f"{glob.escape(output)}.*")
        if _TABLE_FILE.fullmatch(path.name) and path not in named and path not in listed
    )
    return (*named, *listed, *stray)


def stale_table_files(tables_dir: Path) -> list[Path]:
    """Return the table files under `tables_dir` named for a configuration outside `conform.SETTLEMENT_CONFIGS`: an overlay configuration, which settles nothing, or one that has left the set since an earlier build wrote its tables, every file `config_table_files` names for it. `build_tables` deletes them, so neither a table nor any part of its windows output outlives the configuration that produced it or stands in for one that settles nothing."""
    tables_dir = Path(tables_dir)
    configs = set()
    for path in tables_dir.iterdir():
        match = _TABLE_FILE.fullmatch(path.name)
        if match is not None:
            configs.add(next(name for name in match.groups() if name is not None))
    return [
        path
        for config in sorted(configs - set(conform.SETTLEMENT_CONFIGS))
        for path in config_table_files(tables_dir, config)
        if path.exists()
    ]


def _pack_windows(source: IO[bytes], directory: Path, name: str, head: bytes) -> row_delta.Part:
    """Gzip a whole payload the kernel writes into a windows base, `name` under `directory` (`row_delta.write_part`), and return its `Part`: `head`, what a reader has already taken from `source`, then the column line, checked, and the rest of `source`, whose lines are the base's rows. The gzip timestamp is zeroed and the header names no file, so two builds of one table are byte-identical whatever the part's name, and so is a payload however its reads are cut, and the base is the whole payload packed exactly as a tree from before the segment format holds it in its single file. Level 1 is used because the payload is gigabytes of plain TSV per build, written once and read back a few times, and compression time is spent on every build that writes an `out_dir`. The level affects only time, since table identity is taken over the decompressed bytes. `rebuild/kernel-rs/src/artifacts.rs` also states that the decompressed bytes are the artifact's identity."""

    def fill(stream: row_delta.Sink) -> tuple[int, int]:
        stream.write(head)
        columns = source.readline()
        if columns != table_module.WINDOWS_COLUMN_LINE:
            raise ValueError(f"{name}: the payload's second line is not the window column line")
        stream.write(columns)
        return _copy_counting(source, stream)

    return row_delta.write_part(directory, name, fill, gzip_name="")


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
    """Return the bare cmap glyphs, the marker, locked and ss10 copies, and the map from raw name to ss10 copy name for the ss10 input substitution lookup. Raw glyphs are keyed under the synthetic `RAW_STANCE` so they never collide with a reachable settled cell that happens to be the isolated cell. Only letter runes with a code point get ss10 copies: ligature runes never appear in a cmap buffer, and boundary tokens are not runes. Only runes with an entry get locked copies."""
    bare: dict[CellId, GlyphRecord] = {}
    copies: dict[CellId, GlyphRecord] = {}
    ss10_copies: dict[str, str] = {}
    for rune_name, rune in spec.runes.items():
        isolated = geometry.isolated_cell(spec, rune_name)
        record = geometry.realize(spec, surface.resolve_cell(spec, isolated), name=rune_name)
        stripped = replace(record, entry=None, exit=None, entry_curs_only=None, safety_checks=())
        key = CellId(rune_name, RAW_STANCE, None, None, ())
        bare[key] = stripped

        if not rune.sequence and rune.codepoint is not None:
            copy_name = ss10_copy_name(rune_name)
            copies[CellId(rune_name, RAW_STANCE, None, None, ("ss10",))] = replace(stripped, name=copy_name)
            ss10_copies[rune_name] = copy_name

        live_names = [rune_name]
        for marker_name in emit_gsub.marker_states(rune_name, relevant_marker_features(spec, rune_name)):
            copies[CellId(marker_name, RAW_STANCE, None, None, ())] = replace(stripped, name=marker_name)
            live_names.append(marker_name)
        if any(stance.surface.entries for stance in rune.stances.values()):
            for raw_name in live_names:
                copy_name = locked_glyph_name(raw_name)
                copies[CellId(rune_name, RAW_STANCE, None, None, ("locked", raw_name))] = replace(
                    stripped, name=copy_name
                )
    return bare, copies, ss10_copies


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
        "unused_records": sorted(report.unused_records),
        "waiting_on_unmigrated": sorted(report.waiting_on_unmigrated),
        "notes": report.notes,
    }


@dataclass
class _TableGateState:
    """What the table-only branch leaves for `TableGates`. `replay_ready` is set once the string replay has returned, with every settle memo head it commits in place, and `memo_ready` once the witness stage's process has returned, having read every settle memo it loads and written none. Both are also set as soon as the replay-then-witness sequence fails. The `*_red` fields hold the exception each stage would have raised in a serial run. The summaries reach their readers as the JSON files each stage writes."""

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
            f"the shipped settlement order answers a row differently from its table: {emitted['error']}"
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
    """Run the table-only branch on one background thread beside the glyph chain. The string replay runs first, at its own width (`replay_threads`, or the width `_replay_threads` derives from `kernel_exec.REPLAY_PEAK_BYTES`), and the witness stage runs after it, in one spawned process this thread waits on (`_spawn_rule_witnesses`). The order matters: a full replay refills each configuration's settle memo, writing a segment of what its refill changed, the head alone when nothing changed, or a whole base (`replay_and_absorb`, `conform.read_replay_memo`, `conform.compare_replay`), and the witness stage then loads the rows of that memo its certificates can ask for and writes nothing back (`conform.SettleMemoFile.read_only`). The `[t] replay_strings` line names the walk's scope, `scope=full` when it walked every text and `scope=narrowed` when `replay_families` narrowed it to the texts of the changed runes (or to none, when nothing changed), before the process's peak (`rss_token`); the timings journal keeps it (`cycle_timings.REPLAY_TOKENS`). `state.replay_ready` is set once the replay has returned, and the oracle starts after it. `state.memo_ready` is set once the witness stage's process has returned, and the oracle's own memo writes wait for it, so the stage reads each memo as the replay left it. Both are set as soon as that sequence fails, and `TableGates` also sets them when this thread ends any other way, so a wait on either never hangs.

    The witness stage's walk is CPU-bound Python, so it gets a process of its own. On a thread of run_m1's process it would hold the interpreter lock that the main thread needs for the glyph chain and the oracle's setup, and slow both, the work that reads and hashes files most (the cache keys within `[t] oracle_setup`). The process runs one serial walk over the configurations. A process per configuration would need a memory-derived width of its own (`doc/parallelism.md`) and would take cores from the oracle's pool, which takes every core. This thread runs the formation-guard sweep (`kernel_exec.guard_sweep`, one sweep per spec in run_m1's process, which the oracle's setup reads too) and keys the settle memos once, and the process gets the spec, the guard verdicts and every configuration's reachable cells once, then one configuration's decision table and read-only settle memo at a time (`_spawn_rule_witnesses`), so it holds one table at a time. It writes no settle memo byte, and this thread writes `witness_summary.json` once the last configuration has returned, so `TableGates.wait_for_memo` orders the stage's reads before any oracle write. Its `[t] rule_witnesses[<config>]` lines go to the stdout it inherits, and the `[t] rule_witnesses` line this thread prints carries its peak memory (`rss_token`).

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
                spec,
                out_dir,
                inputs,
                replay_threads=replay_threads,
                memo_inputs=memo_inputs,
                imports=imports_digest(tables),
            )
            walked = "every text" if replay["families"] is None else f"{len(replay['families'])} families"
            scope = "full" if replay["families"] is None else "narrowed"
            console.timing(
                "replay_strings",
                time.perf_counter() - start,
                f"scope={scope} {rss_token(process_peak_rss_bytes())}",
            )
            console.say(f"replay_strings: maximum length {replay['max_length']}, {walked}")
            if not replay["pass"]:
                raise SystemExit(f"the string replay found the tables incomplete: {replay['error']}")
            state.replay_ready.set()

            console.phase("rule_witnesses")
            start = time.perf_counter()
            witness_summary, witness_peak = _spawn_rule_witnesses(spec, tables, out_dir, memo_inputs)
            console.timing("rule_witnesses", time.perf_counter() - start, rss_token(witness_peak))
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
    """The handle `run` returns for its table-only branch, with the two points where the oracle waits for it. `wait_for_replay` is called before the oracle starts: it blocks until the string replay has returned (every settle memo head the replay commits is in place, and the replay crate has exited), reports the wait as `[t] settle_memo_wait`, and raises the replay's failure, or the witness stage's if that stage has already failed. `wait_for_memo` is called before the oracle writes to a shared settle memo: it blocks until the witness stage's process has returned, having read every settle memo it loads, raises either failure, and returns the seconds it blocked, or None when it did not block. It prints nothing, because the pooled oracle calls it on a thread beside its ranges, where how long it blocks is the witness stage's overlap with the pool; the oracle reports the part of the wait that held its writes back as `[t] witness_memo_wait` (`run_m1_gates.run_oracle`).

    For each configuration, the witness stage maps that configuration's memo, every part its head lists, serves the rows its certificates can ask for, and writes nothing to it (`conform.SettleMemoFile.read_only`). The oracle makes no write to a shared memo before `wait_for_memo` (`run_m1_gates.run_oracle`'s `memo_ready`): its pooled ranges write no settle memo part, and it submits their configurations' absorbs, each of which writes a segment and commits its head, only after the wait, and its `--jobs 1` walks, which write their own segments, start after the wait. A writer deletes a part only once the head that replaced it is in place, and a stage that mapped a part keeps reading it, so the stage reads each memo as the replay left it, and its served and fresh counts, and `witness_summary.json`, do not depend on when an oracle write lands.

    `join` waits for the whole branch and raises the first failure in serial order (the packing, the replay, the witnesses, the shipped order). `first_red` returns that failure instead, for the paths where the glyph chain has also failed and the branch's failure is raised in its place. Call `close` in a `finally`, so no thread, pool or process outlives the run on an interrupt. It waits for the branch, which shuts the witness stage's pool down before it ends. A Ctrl-C reaches the witness stage's process as well, since that process stays in run_m1's process group, so on an interrupt the wait ends as soon as that process does.
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

    def wait_for_memo(self) -> float | None:
        return self._wait(self._state.memo_ready, None)

    def _wait(self, ready: threading.Event, label: str | None) -> float | None:
        waited = None
        if not ready.is_set():
            start = time.perf_counter()
            ready.wait()
            waited = time.perf_counter() - start
            if label is not None:
                console.timing(label, waited)
        if self._state.chain_red is not None:
            raise self._state.chain_red
        if self._future.done():
            self._future.result()
        return waited

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


def run_ligature_outgoing(spec: ResolvedSpec) -> None:
    """Verify authored outgoing mappings over the loaded spec, and log the number of mappings and settled windows on the `[t] ligature_outgoing` line, so the build log shows how large a domain the check covered."""
    from rebuild.pipeline.ligature_outgoing_check import validate_ligature_outgoing
    from rebuild.pipeline.spec_load import DEFAULT_RUNES_DIR

    console.phase("ligature_outgoing")
    start = time.perf_counter()
    rune_raws = {
        path.stem: yaml.safe_load(path.read_text()) for path in sorted(DEFAULT_RUNES_DIR.glob("*.yaml"))
    }
    summary = validate_ligature_outgoing(spec, rune_raws)
    console.timing(
        "ligature_outgoing",
        time.perf_counter() - start,
        f"mappings={summary['mappings']} windows={summary['windows']}",
    )


def run(
    out_dir: Path = OUT_DIR,
    spec: ResolvedSpec | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
    memo_inputs: oracle_cache.SettleMemoInputs | None = None,
    replay_threads: int | None = None,
    scratch_beside_default: int | None = None,
) -> tuple[dict, TableGates]:
    """Build the tables, then run two branches over them. The table-only branch (`_run_table_gates`, on one background thread) runs the string replay, the witness stage (in a spawned process of its own) and the shipped-order walks. The glyph chain (minting, the defect gates, the emission, the compile and read-back) runs on the calling thread, writes `pipeline_summary.json`, and returns its summary with the branch's `TableGates` handle without joining the branch. The caller joins it: `full_run` calls `wait_for_replay` before the oracle, passes `wait_for_memo` for the oracle to call before its memo writes, calls `join` after the oracle so the gate covers both branches, and calls `close` in a `finally`. When the glyph chain raises, the branch's first failure is raised in its place if there is one, so a failed replay is reported as incomplete tables and not as whatever the glyph chain made of them.

    `inputs` is `tables_inputs` over the sources `spec` was loaded from, computed before the load so it can only name content the tables are at least as new as. Passing it keeps the window enumeration under `out_dir` for the conformance sweep and the shipped-order walk. A caller running its own spec omits it, and the walk does not run. `memo_inputs` is `settle_memo_inputs`, computed at the same moment. It names the settle memos the string replay fills and the witness stage, the oracle and the conformance sweep then load; without it the replay writes no memo, the witness stage settles every certificate, and nothing is shared. `kernel_threads` applies only to the table build, whose per-configuration memory cost that width was derived from, and so does `scratch_beside_default` (`build_tables`). `replay_threads` applies only to the string replay, whose memory cost is `kernel_exec.REPLAY_PEAK_BYTES` and which derives its own width (`_replay_threads`) when none is given. The packing and the shipped-order walks run one task per configuration up to the cores (`_core_bound_threads`) regardless of either width. The witness stage and the walks can still be running when the oracle's pool starts, since the oracle waits only for the string replay (`TableGates.wait_for_replay`), and they share the machine with that pool.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = head_commit()
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
            spec,
            out_dir,
            inputs=inputs,
            kernel_threads=kernel_threads,
            packing=packing,
            scratch_beside_default=scratch_beside_default,
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
        summary = _run_glyph_chain(spec, tables, out_dir, commit)
    except BaseException as error:
        red = gates.first_red() if isinstance(error, (Exception, SystemExit)) else None
        gates.close()
        if red is not None:
            raise red
        raise
    return summary, gates


def _run_glyph_chain(
    spec: ResolvedSpec, tables: Mapping[str, tuple], out_dir: Path, commit: str | None
) -> dict:
    """Run the glyph chain over one build's tables: minting, the defect gates, the feature emission, the compile and read-back, then write `pipeline_summary.json`. It reads the spec and the tables and writes nothing the table-only branch reads. The `[t] readback` line carries `readback_tokens` for `commit`, the commit `run` took before the build read its inputs, and is printed before a failed read-back raises, so a build that falls through the headroom floor still records its figures."""
    console.phase("glyph_minting")
    start = time.perf_counter()
    cell_glyphs = mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = mint_raw_glyphs(spec)
    dots = namer_dot_glyphs()
    console.timing("glyph_minting", time.perf_counter() - start)

    console.phase("defect_gates")
    start = time.perf_counter()
    defect_report = _run_defect_gates(spec, tables, cell_glyphs)
    console.timing("defect_gates", time.perf_counter() - start)

    console.phase("emit_gsub_gpos")
    start = time.perf_counter()
    curs_glyphs = {**cell_glyphs, **bare, **copies}
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_copies=ss10_copies)
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

    console.phase("readback")
    start = time.perf_counter()
    readback_report = readback.verify_font(
        font_path, gsub_plan, emit_gpos.cursive_registrations(curs_glyphs, spec=spec)
    )
    output_sink.write_output(
        out_dir / "readback_summary.json", json.dumps(readback_report, indent=2) + "\n", keep_if_equal=True
    )
    _build_phase_lines.append(
        console.timing("readback", time.perf_counter() - start, readback_tokens(readback_report, commit))
    )
    if not readback_report["pass"]:
        raise readback.ReadbackError(
            f"{readback_report['divergence_count']} read-back divergence(s) between the compiled font and the plan; see {out_dir / 'readback_summary.json'}"
        )

    summary = {
        "configs": list(tables),
        "rules_per_config": {config: len(decision.rules) for config, (decision, _joins) in tables.items()},
        "settled_cell_glyphs": len(cell_glyphs),
        "total_glyphs": len(all_glyphs),
        "gsub_rule_count": gsub_plan.rule_count,
        **_defect_summary_fields(defect_report),
        "font": str(font_path),
    }
    output_sink.write_output(
        out_dir / "pipeline_summary.json", json.dumps(summary, indent=2) + "\n", keep_if_equal=True
    )
    return summary


# The conformance sweep's maximum length (`conform.SWEEP_MAX_LENGTH`), not one more. The walk's cost is its count of distinct raw windows, and every first-position window of a length-N text is distinct: the alphabet size to the Nth per configuration, each a full settlement, since the engine's trace memo is keyed on the raw slots. So each extra letter of depth multiplies the walk by the alphabet size. Over the whole alphabet, a full replay at maximum length 5 costs every configuration minutes even under the deep replay's memo ceiling (`make cycle-timings ARGS='--by-step'` reports it under `replay-deep`), and even narrowed to one family it would cost more than the conformance sweep run it lets the cycle skip. Checking past this depth is the job of the periodic deep sweep (`make conform-deep`), which settles every text it shapes.
REPLAY_MAX_LENGTH = conform.SWEEP_MAX_LENGTH
REPLAY_FORMAT = "ams-m1-replay/1"
REPLAY_SUMMARY = "replay_summary.json"
# The pool unit a replay that walked any text records its walks' peaks under: on a full replay each walk's own crate process's resident peak, one observation a configuration (`report_replay`), and on a narrowed one its one process's peak over the walks it ran at once (`_record_replay_walk`). The replay-walk row of `make job-costs` holds them against `kernel_exec.REPLAY_PEAK_BYTES`.
REPLAY_POOL_UNIT = "replay-walk"
# The pool unit a full replay's absorb workers record their peak footprints under (`report_replay`), one reading a worker, each taken beside its own walk. The replay-absorb row of `make job-costs` reports them without a constant, since `kernel_exec.REPLAY_PEAK_BYTES` books each with its walk.
REPLAY_ABSORB_POOL_UNIT = "replay-absorb"
# How often a full replay's parent looks for an absorb worker that ended without reporting its walk while it waits for the reports, and how often an absorb worker whose parent has gone sends itself SIGTERM again until it has ended (`_stop_when_orphaned`), in seconds.
REPLAY_REPORT_POLL_S = 0.2
# The pool unit a bare run records its own process's peak footprint under (`full_run`), which the run-m1-parent row of `make job-costs` reports without a constant.
PARENT_POOL_UNIT = "run-m1-parent"
RUNE_LABEL_PREFIX = "glyph_data/runes/"


def locality_lines(spec: ResolvedSpec, root: Path = REPO_ROOT) -> list[str]:
    """Return the whole-store half of the window-locality key (`doc/rebuild-design.md` §10) as lines: the non-rune data the tables' stamp covers (the script registry, the schemas, the punctuation), the build side of the pipeline code and the crate, the engine's semantics tokens, the cross-rune structure `spec_load.spec_structure_digest` covers (the alphabet and its ligature sequences, the predicate classes' membership, the resolved rune-local groups), and the capability features. While these are unchanged, a window's result depends only on the runes it names. The string replay's stamp is built from these lines. The memo stamp hashes its own key: the crate and the import closure of its driver and of the dump's writer outside rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, beside what reaches the crate through the dump and the mode set (`memo_structure_stamp`)."""
    from rebuild.pipeline import spec_load

    lines = [line for line in fingerprint.table_data_lines(root) if not line.startswith(RUNE_LABEL_PREFIX)]
    lines += fingerprint.path_lines(root, fingerprint.table_code_paths(root))
    lines.append("semantics\t" + "+".join(kernel_exec.enumeration_tokens()))
    lines.append(f"structure\t{spec_load.spec_structure_digest(spec)}")
    lines.append("capabilities\t" + ",".join(spec_load.capability_features(spec)))
    return lines


def replay_structure_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the hash of everything the string replay's result depends on apart from the rune files: `locality_lines` and the maximum length. While it matches the last passing record's, a build walks only the texts naming a rune whose digest changed, closed under `spec_load.rune_closure`. When it differs, the build walks every text."""
    lines = [*locality_lines(spec, root), f"max_length\t{REPLAY_MAX_LENGTH}"]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def imports_digest(tables: Mapping[str, tuple]) -> str:
    """Return the hash of every configuration's imported windows (`table.DecisionTable.imports`), the windows the crate's fold took from other configurations (`rebuild/kernel-rs/src/crossconfig.rs`). They tie one configuration's rules to another's rows, so a rune edit can reshape a configuration's rules for windows whose texts name no edited rune, and `replay_families` walks every text when this hash moves."""
    payload = {
        config: [
            list(row)
            for row in getattr(entry[0] if isinstance(entry, (tuple, list)) else entry, "imports", ())
        ]
        for config, entry in sorted(tables.items())
    }
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def replay_families(
    spec: ResolvedSpec,
    previous: Mapping | None,
    structure: str,
    runes: Mapping[str, str],
    imports: str | None = None,
) -> list[str] | None:
    """Return which runes' texts this build's replay must walk, given the last passing replay record, this build's structure stamp, its per-rune digests and its `imports_digest`: None for every text, an empty list when nothing changed, and otherwise the runes whose digest changed plus every rune whose records read one of them (`spec_load.rune_closure`), sorted. Every text is walked when the record is missing, is not a passing record of this format, was walked under another structure stamp or another set of imported windows, or names a rune this spec no longer models. A narrowed walk is valid only by induction: it needs a passing full replay as its base and an unchanged structure stamp and imported windows at every step since. The imported windows are a cross-configuration exception to the window locality rule: a configuration's rules for a pair of letters can change because another configuration's rows did, whatever the texts reaching the pair name."""
    from rebuild.pipeline import spec_load

    if (
        previous is None
        or previous.get("format") != REPLAY_FORMAT
        or not previous.get("pass")
        or previous.get("structure") != structure
        or previous.get("imports") != imports
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
    imports: str | None = None,
) -> dict:
    """Run the enumeration-completeness check every build runs right after its tables are written: the crate's `replay-strings` subcommand (`rebuild/kernel-rs/src/replay.rs`) over the named configurations' settlement TSVs under `out_dir`, walking every text up to `REPLAY_MAX_LENGTH` and checking each window's first-match rule outcome against the engine's own settlement. `configs` is the whole settlement set unless an unstamped caller narrowed its tables the same way. A narrowed walk with `inputs` raises `ValueError` before it starts, because the record it would write is read back as a passing full-replay base for configurations it never walked.

    On a rune edit only some of the texts are walked. `replay_families` reads the last passing record beside the tables, and while `replay_structure_stamp` and the tables' imported windows (`imports`, the build's `imports_digest`) match, only the texts naming a changed rune, or a rune whose records read one, are walked. A build with no passing record or a changed structure walks everything, and a build where nothing changed walks nothing and carries the record forward. A caller with no stamp (its own spec, whose rune files are not the repository's) walks every text and writes no record.

    The walk also produces the settle memo. With `memo_inputs` (`settle_memo_inputs`, computed before the spec was loaded), every full replay walks each configuration in a crate process of its own beside the spawn worker that absorbs its window memo as the walk writes it (`replay_and_absorb`), on the route its plan gave its configuration before the pool started (`conform.plan_settle_memos`). On the segment route the absorb compares its refill with the standing memo and writes a segment of what differs, or nothing when every window settled as the memo holds it (`conform.compare_replay`), and the refill whole as an `unusable` base when the standing memo does not read; on a base route it writes the refill whole as the next generation's base, today's file byte for byte. Once every walk has passed, the parent commits each configuration's head under the stamp and family keys `conform.settle_memo_files` computes, every part recorded under them. So the witness stage, the oracle and the conformance sweep load what the replay settled instead of settling it again. A red replay commits no head and writes no part at a width of the configuration count, and removes the parts its workers wrote early at a narrower one (`replay_and_absorb`), and a failed absorb is a warning, not a failure, since every reader settles what the memo lacks. When any configuration's memo is missing or fails `conform.settle_memo_standing`, a head that does not read, a part missing or not its size, or a part recording another stamp, every text is walked to refill it, which can widen the walk beyond what the structure stamp requires. The code `oracle_cache.settle_memo_stamp` hashes (`oracle_cache.oracle_code_paths`: the modules `oracle_cache.ORACLE_ROW_CODE_PATHS` names and the crate) is inside the build-side code `locality_lines` hashes, so an edit that moves that stamp's `oracle_code` line also moves the structure stamp. A narrowed walk (a rune edit), or a full walk with no `memo_inputs`, writes no memo and runs every configuration it walks in one crate process (`kernel_exec.replay_strings`), and leaves the existing memos to drop their own stale entries.

    The record written beside the tables is what the next build's walk is narrowed against, so it carries the structure stamp and every rune digest as well as the counts. It is written whether the walk passed or failed: a disagreement is recorded with the crate's message, and `_run_table_gates` raises it as `SystemExit`. The width is `_replay_threads`: `replay_threads` when given, else the machine's memory divided by `kernel_exec.REPLAY_PEAK_BYTES` (what one configuration's walk and its absorb hold together), capped at the configuration count and the cores, so every text replays in one round on both fleet machines. On a full replay that width gates the walks' crate processes, and `report_replay` records each walk's own peak as one reading of the `replay-walk` record and each absorb worker's peak footprint as one of the `REPLAY_ABSORB_POOL_UNIT` record. A narrowed replay records its one process's peak over the walks it ran at once (`_record_replay_walk`).
    """
    configs = tuple(configs)
    if inputs is not None and set(configs) != set(conform.SETTLEMENT_CONFIGS):
        raise ValueError(
            f"a narrowed replay ({', '.join(configs)}) cannot record: the record beside the tables is read back as a green full-replay base for every settlement configuration"
        )
    threads = _replay_threads(replay_threads)
    recordable = inputs is not None
    structure = replay_structure_stamp(spec) if recordable else None
    runes = fingerprint.rune_digests(REPO_ROOT) if recordable else {}
    families = (
        replay_families(spec, read_replay_record(out_dir), structure, runes, imports)
        if recordable and structure is not None
        else None
    )
    memos = {
        config: memo
        for config, memo in conform.settle_memo_files(out_dir, spec, memo_inputs).items()
        if config in configs
    }
    clear_replay_leftovers(out_dir, memos)
    if memos and not all(conform.settle_memo_standing(memo) for memo in memos.values()):
        families = None
    emitting = families is None and bool(memos)
    summary: dict = {
        "format": REPLAY_FORMAT,
        "max_length": REPLAY_MAX_LENGTH,
        "families": families,
        "walked": families is None or bool(families),
        "configs": {},
        "structure": structure,
        "runes": runes,
        "imports": imports,
        "pass": True,
        "error": None,
    }
    if emitting:
        replayed = replay_and_absorb(spec, out_dir, memos, threads)
        report_replay(replayed, memos, threads)
        if replayed.passed:
            summary["configs"] = {config: replayed.walks[config].counts for config in memos}
        else:
            failed = replayed.failure()
            if not failed.disagreement:
                raise kernel_exec.KernelRunError(failed.error)
            summary["pass"] = False
            summary["error"] = failed.error
    elif families is None or families:
        try:
            summary["configs"] = kernel_exec.replay_strings(
                spec,
                out_dir,
                configs,
                max_length=REPLAY_MAX_LENGTH,
                families=families,
                threads=threads,
                timings=True,
                on_peak=functools.partial(_record_replay_walk, walks=min(threads, len(configs))),
            )
        except kernel_exec.ReplayDisagreement as error:
            summary["pass"] = False
            summary["error"] = str(error)
    if recordable:
        (out_dir / REPLAY_SUMMARY).write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _record_replay_walk(peak: int, *, walks: int) -> None:
    """Record a narrowed replay's walks as one observation of the `replay-walk` pool (`cycle_timings.record_pool`): the crate process's own peak RSS (`kernel_exec.replay_strings`'s `on_peak`) divided by the walks it ran at once. The walks are threads of that one process, so no figure per walk exists. A full replay records each walk's own peak instead (`report_replay`)."""
    record_pool(
        REPLAY_POOL_UNIT,
        width=walks,
        worker_peaks={"per walk": peak // walks},
        controller_peak_bytes=peak_rss_self_bytes(),
    )


def clear_replay_leftovers(out_dir: Path, memos: Mapping[str, conform.SettleMemoFile]) -> None:
    """Remove what a killed full replay can leave for each configuration of `memos` at the FIFO at its window memo path (`kernel_exec.replay_memo_dump`). Only the replay writes that path while it runs, so whatever is there is a leftover. A part an absorb worker wrote under its final name, which no head lists, and a staging file a killed write left, are the next plan's to delete (`conform.plan_settle_memos`, through `row_delta.remove_unlisted` and `row_delta.remove_staging`)."""
    for config in memos:
        kernel_exec.replay_memo_dump(out_dir, config).unlink(missing_ok=True)


@dataclass(frozen=True)
class ReplayWalkReport:
    """What a full replay's absorb worker sends the parent as soon as its walk has ended, before it waits for the verdict (`_walk_and_absorb`): the configuration, the walk's `{texts, windows, skipped}` or its failure's message (`error`), whether that failure is a disagreement, the crate's `[t]` lines (and `[c]` lines when asked) untagged, the crate's own peaks and CPU seconds, and the worker's pid."""

    config: str
    counts: dict[str, int] | None
    error: str | None
    disagreement: bool
    lines: tuple[str, ...]
    peaks: ReapedPeaks | None
    worker: int


@dataclass(frozen=True)
class ReplayAbsorb:
    """What a full replay's absorb worker returns once the verdict has reached it, or once it has written or discarded its part while a walk still waited for a slot (`_walk_and_absorb`): the part it wrote under its final name, which no head lists until the parent commits it (None when it discarded the part or wrote none), the windows its refill holds and the seconds its read and its index or compare took, or the warning the parent prints instead, its own resident peak, peak footprint and CPU seconds, the route its part takes (`route`: the one the parent planned, or that route as an `unusable` base when the compare found the standing memo would not read, `conform.SettleMemoRoute.unusable`), through which the parent commits, and, when asked, its part's size and sha256 as laid out. `wrote` says what the absorb's route leaves (`head`, `segment` or `base`), `rows_written` the rows its part holds, `named` the standing rows its dead bitmaps name, `compare_seconds` the seconds its compare with the standing memo took on the segment route, and `segments` the segments the head the parent committed lists."""

    config: str
    part: row_delta.Part | None
    entries: int | None
    seconds: float
    warning: str | None
    resident: int
    footprint: int
    cpu: float
    route: conform.SettleMemoRoute
    digest: tuple[int, str] | None = None
    wrote: str = ""
    rows_written: int = 0
    named: int = 0
    compare_seconds: float = 0.0
    segments: int = 0


@dataclass(frozen=True)
class FullReplay:
    """What `replay_and_absorb` did: each configuration's walk as its worker reported it and each worker's absorb, keyed in the order the memos were named, and whether every walk passed. A worker that ended without a result has no absorb."""

    walks: dict[str, ReplayWalkReport]
    absorbs: dict[str, ReplayAbsorb]
    passed: bool

    def failure(self) -> ReplayWalkReport:
        """The earliest failed walk in the memos' order, whose error the replay reports, as the crate's own fan-out picks the earliest configuration."""
        return next(walk for walk in self.walks.values() if walk.error is not None)


@dataclass
class _ReplayWorker:
    """What a full replay's absorb worker holds from its pool's initializer (`_replay_worker_init`): the parent's spec dump, the gate on walks, the report queue, the verdict and whether it keeps the files, how many walks have taken a slot, how many the replay runs, and whether its width is below that count."""

    spec_path: Path
    gate: Any
    reports: Any
    decided: Any
    kept: Any
    admitted: Any
    walks: int
    narrow: bool


_REPLAY_WORKER: _ReplayWorker | None = None


def _replay_worker_init(
    built: bool,
    spec_path: str,
    gate: Any,
    reports: Any,
    decided: Any,
    kept: Any,
    admitted: Any,
    walks: int,
    narrow: bool,
) -> None:
    """Initialize a full replay's absorb worker: mark the kernel built (`kernel_exec._BUILT`) only when the parent's own `ensure_built()` succeeded, which `built` says, so a worker never runs the no-op `cargo build` under the relink lock and one whose parent never built cannot skip cargo after a crate edit; and keep the parent's spec dump path, so no worker writes a `spec.json`, beside the replay's gate on walks, its report queue, its verdict, its count of walks that have taken a slot, its walk count and whether its width is below it (`replay_and_absorb`). In a spawned worker it also starts the thread that stops the worker once its parent has gone (`_stop_when_orphaned`)."""
    global _REPLAY_WORKER
    if built:
        kernel_exec._BUILT = True
    _REPLAY_WORKER = _ReplayWorker(Path(spec_path), gate, reports, decided, kept, admitted, walks, narrow)
    parent = multiprocessing.parent_process()
    if parent is not None:
        threading.Thread(
            target=_stop_when_orphaned, args=(parent,), name="replay parent", daemon=True
        ).start()


def _stop_when_orphaned(parent: BaseProcess) -> None:
    """Wait for the process that started this full replay's absorb worker to end, as a stop that reached run_m1 alone ends it, and then stop the worker wherever it is: waiting for a slot, walking, waiting for the verdict, or idle in its pool. Nothing is left to read its report or set its verdict. It sends the worker's main thread SIGTERM, and again every `REPLAY_REPORT_POLL_S` until the worker has ended. At its default action a SIGTERM ends the worker at once; inside a walk, where it raises an interrupt instead (`kernel_exec.stream_payloads`), the first one kills and reaps the walk's crate and removes its FIFO, and a later one ends the worker once the walk's handlers are gone. A slot the worker held is not given back, which no worker misses, since every worker of the replay stops the same way. A file it staged stays at its staged path until the next replay starts (`clear_replay_leftovers`)."""
    parent.join()
    main = threading.main_thread().ident
    assert main is not None
    while True:
        signal.pthread_kill(main, signal.SIGTERM)
        time.sleep(REPLAY_REPORT_POLL_S)


def _write_finished(
    finished: conform.FinishedSettleMemo | None,
    memo: conform.SettleMemoFile,
    route: conform.SettleMemoRoute,
    warning: str | None,
) -> tuple[row_delta.Part | None, str | None]:
    """Write a full replay's finished part, its segment or its base, under its final name, which no head lists until the parent commits it (`conform.SettleMemoRoute.write`), and return it, beside the warning the result carries: the absorb's own, or, for a memo that changed after the plan or a part it could not write, that one."""
    if finished is None:
        return None, warning
    if conform.settle_memo_source(memo) != route.signature:
        return (
            None,
            f"settle memo: {memo.path} changed after the replay's plan; nothing is written, and the readers settle instead",
        )
    try:
        return route.write(finished), warning
    except OSError as error:
        return None, f"settle memo: {memo.path} not written ({error}); the readers settle instead"


def _walk_and_absorb(
    out_dir: Path,
    config: str,
    memo: conform.SettleMemoFile,
    spec: ResolvedSpec,
    symbols: int,
    route: conform.SettleMemoRoute,
    cache_stats: bool = False,
    digest: bool = False,
    keep: bool = True,
) -> ReplayAbsorb:
    """One configuration's walk and absorb, in a full replay's absorb worker. Holding a slot of the replay's gate, it runs the walk, a `replay-strings` process for `config` alone, reading its window memo from a FIFO into the settle memo's columns as it arrives (`kernel_exec.replay_memo_walk`, `conform.read_replay_memo`). Once the crate has exited it sends the parent its walk (`ReplayWalkReport`) and lays out its part as `route`, which the parent planned, says: on a base route the refill's whole file, its index built (`conform.ReplayMemoRows.finish`); on the segment route the refill compared with the standing memo (`conform.compare_replay`), which gives a segment of what differs, or nothing when the refill settles every window as the memo holds it, so the parent commits the head alone, or, when the standing memo does not read, the refill's whole file as an `unusable` base (`conform.SettleMemoRoute.unusable`), the route it returns for the parent to commit through. With `digest` it hashes the part's bytes in order. Where the width is below the walk count and a walk has yet to take a slot, the slot it is about to leave goes to that walk, so it writes the part at once under its final name with `keep`, which no head lists until the parent commits it on a pass and which the parent removes on a red replay, or discards it without, and then waits for the verdict holding no part; a red replay at such a width can therefore write parts it then removes. Otherwise it leaves the slot and holds the part until the parent's verdict: on a pass with the parts kept it writes it under its final name, and on a red replay it discards it. Either way it returns only once the verdict is set, so no worker takes a second walk. A memo the absorb could not read, a standing memo that changed after the plan, or a part it could not write, is a warning on the result rather than a failure, as a memo the crate never wrote is."""
    state = _REPLAY_WORKER
    assert state is not None, "a full replay's worker runs under _replay_worker_init"
    name = kernel_exec.replay_memo_dump(out_dir, config)
    read_seconds: list[float] = []

    def read(source: IO[bytes]) -> conform.ReplayMemoRows | str:
        started = time.perf_counter()
        try:
            return conform.read_replay_memo(source, spec, config, name)
        except (kernel_exec.KernelRunError, OSError) as error:
            return f"settle memo: {name} not absorbed ({error}); the readers settle instead"
        finally:
            read_seconds.append(time.perf_counter() - started)

    absorbed: conform.ReplayMemoRows | str | None = None
    finished: conform.FinishedSettleMemo | None = None
    part: row_delta.Part | None = None
    wrote = ""
    entries: int | None = None
    rows_written = named = 0
    compare_seconds = 0.0
    with state.gate:
        with state.admitted.get_lock():
            state.admitted.value += 1
        try:
            walk = kernel_exec.replay_memo_walk(
                state.spec_path,
                out_dir,
                config,
                max_length=REPLAY_MAX_LENGTH,
                symbols=symbols,
                read=read,
                cache_stats=cache_stats,
            )
        except kernel_exec.KernelRunError as error:
            report = ReplayWalkReport(
                config,
                None,
                str(error),
                isinstance(error, kernel_exec.ReplayDisagreement),
                (),
                None,
                os.getpid(),
            )
        else:
            report = ReplayWalkReport(config, walk.counts, None, False, walk.lines, walk.peaks, os.getpid())
            absorbed = walk.memo
            del walk
        state.reports.put(report)
        warning = absorbed if isinstance(absorbed, str) else None
        started = time.perf_counter()
        if isinstance(absorbed, conform.ReplayMemoRows):
            compared: conform.ReplayCompare | str | None = None
            if route.route == conform.SEGMENT_ROUTE:
                compared = conform.compare_replay(memo, spec, absorbed, route)
                if compared is None:
                    route = route.unusable()
            if isinstance(compared, str):
                warning, entries = compared, absorbed.rows
            elif compared is not None:
                finished = compared.segment
                wrote = "head" if finished is None else conform.SEGMENT_ROUTE
                entries, rows_written, named = compared.entries, compared.rows, compared.named
                compare_seconds = compared.seconds
            else:
                finished = absorbed.finish(memo)
                wrote, entries, rows_written = conform.BASE_ROUTE, finished.rows, finished.rows
        seconds = sum(read_seconds) + time.perf_counter() - started
        del absorbed
        hashed = finished.digest() if digest and finished is not None else None
        early = state.narrow and state.admitted.value < state.walks
        if early:
            part, warning = _write_finished(finished if keep else None, memo, route, warning)
            finished = None
    state.decided.wait()
    if not early:
        part, warning = _write_finished(finished if state.kept.value else None, memo, route, warning)
        finished = None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return ReplayAbsorb(
        config,
        part,
        entries,
        seconds,
        warning,
        peak_rss_self_bytes(),
        peak_footprint_self_bytes(),
        usage.ru_utime + usage.ru_stime,
        route,
        hashed,
        wrote,
        rows_written,
        named,
        compare_seconds,
    )


def replay_and_absorb(
    spec: ResolvedSpec,
    out_dir: Path,
    memos: Mapping[str, conform.SettleMemoFile],
    threads: int,
    *,
    cache_stats: bool = False,
    keep: bool = True,
    digest: bool = False,
    spec_path: Path | None = None,
) -> FullReplay:
    """Walk every configuration of `memos` over every text up to `REPLAY_MAX_LENGTH`, each in a crate process of its own, at most `threads` crate processes at once, each beside the spawn worker that absorbs its window memo (`_walk_and_absorb`), and commit each configuration's settle memo head once every walk has passed.

    Before the pool starts, the parent plans each configuration's settle memo route (`conform.plan_settle_memos`, printed as `[t] settle_memo_plan`): a base for a configuration with no memo, one that does not read or whose fold this pass holds, and otherwise the segment route over its head, or over its single file from before the segment format, adopted by hard link as generation 0's base. It builds the kernel (`kernel_exec.ensure_built`) and dumps the spec once (`kernel_exec._spec_dump`), then starts a spawn pool with one worker a configuration, whose initializer marks the kernel built from the parent's word, keeps the spec dump's path and starts the thread that stops the worker, wherever it waits, once the parent has gone (`_replay_worker_init`, `_stop_when_orphaned`). Each worker gets its route, never rows. The width gates the walks through a semaphore and not the workers: a worker holds its slot from its walk's start until its part is laid out, then leaves it and holds the part until the verdict, so a worker waiting with its part never holds a slot another walk needs and a width of one cannot deadlock. Where the width is below the configuration count, a worker whose part is laid out while a walk has yet to take a slot writes it at once under its final name, which no head lists, or discards it without `keep`, before it leaves its slot, and then waits for the verdict holding no part: so the walks running and the parts held never outnumber the width. No worker returns before the verdict, so each runs one walk. Each worker reports its walk on a queue as soon as its crate has exited; a worker that ends without reporting counts as a failed walk. Once every walk has reported, the replay passes if every walk passed. The parent then sets the verdict: on a pass with `keep` each worker still holding its part writes it, and the parent commits each configuration's head over it, or alone where the refill settled every window as the memo held it, with every part recorded under the current stamp and family keys, through the route the worker returns (`conform.SettleMemoRoute.commit`), one writer a configuration; otherwise every worker still holding its part discards it, and the parent removes every part a worker wrote early and every adoption's link, so a red replay commits no head and leaves no part. With `digest` each worker hashes its part before the verdict, which the memory series reads with `keep` off. Every message between the parent and a worker is counts, lines, peaks, a route and a part's name, size and sha256, never rows or the part. Every FIFO is removed however this ends (`clear_replay_leftovers` removes what a killed run left, before a replay starts). `cache_stats` passes `--cache-stats` to each crate. `spec_path` names a dump of `spec` the caller already wrote, in place of this process's own (`kernel_exec._spec_dump`).
    """
    configs = list(memos)
    plan = conform.plan_settle_memos(memos)
    plan.report()
    kernel_exec.ensure_built()
    spec_path = kernel_exec._spec_dump(spec) if spec_path is None else spec_path
    symbols = len(conform.spec_alphabet(spec))
    context = multiprocessing.get_context("spawn")
    gate = context.Semaphore(max(1, threads))
    reports = context.Queue()
    decided = context.Event()
    kept = context.Value("b", 0)
    admitted = context.Value("i", 0)
    walks: dict[str, ReplayWalkReport] = {}
    absorbs: dict[str, ReplayAbsorb] = {}
    committed: set[str] = set()
    passed = False
    try:
        with _spawn_pool(
            len(configs),
            len(configs),
            initializer=_replay_worker_init,
            initargs=(
                True,
                str(spec_path),
                gate,
                reports,
                decided,
                kept,
                admitted,
                len(configs),
                max(1, threads) < len(configs),
            ),
        ) as pool:
            try:
                futures = {
                    config: pool.submit(
                        _walk_and_absorb,
                        out_dir,
                        config,
                        memos[config],
                        spec,
                        symbols,
                        plan.routes[config],
                        cache_stats,
                        digest,
                        keep,
                    )
                    for config in configs
                }
                while len(walks) < len(futures):
                    try:
                        report = reports.get(timeout=REPLAY_REPORT_POLL_S)
                    except queue.Empty:
                        for config, future in futures.items():
                            if config not in walks and future.done():
                                walks[config] = ReplayWalkReport(
                                    config,
                                    None,
                                    f"{config}: the replay's absorb worker ended before its walk reported ({future.exception()!r})",
                                    False,
                                    (),
                                    None,
                                    0,
                                )
                        continue
                    walks[report.config] = report
                passed = all(walk.error is None for walk in walks.values())
                kept.value = 1 if passed and keep else 0
            finally:
                decided.set()
            for config, future in futures.items():
                try:
                    absorbs[config] = future.result()
                except Exception as error:
                    console.warn(
                        f"settle memo: the absorb worker for {config} failed ({error!r}); the readers settle instead"
                    )
        for config in configs:
            absorb = absorbs.get(config)
            if not (passed and keep) or absorb is None or absorb.warning is not None or not absorb.wrote:
                continue
            try:
                head = absorb.route.commit(memos[config], absorb.part, restamp=True)
            except OSError as error:
                absorbs[config] = replace(
                    absorb,
                    warning=f"settle memo: {memos[config].path} not written ({error}); the readers settle instead",
                )
                continue
            committed.add(config)
            absorbs[config] = replace(absorb, segments=len(head.segments))
    finally:
        for config in configs:
            kernel_exec.replay_memo_dump(out_dir, config).unlink(missing_ok=True)
            absorb = absorbs.get(config)
            if config not in committed and absorb is not None and absorb.part is not None:
                (memos[config].path.parent / absorb.part.name).unlink(missing_ok=True)
        plan.discard(committed)
    return FullReplay({config: walks[config] for config in configs}, absorbs, passed)


def report_replay(replayed: FullReplay, memos: Mapping[str, conform.SettleMemoFile], threads: int) -> None:
    """Print what a full replay did and record its pools: each walk's crate lines, the `[t]` ones tagged with its configuration where a label carries none, in the memos' order, then, on a passing replay, each absorb's `[t] settle_memo_emit <config>` line or its warning in the same order. The line keeps `entries=`, the windows the refill holds, and says what the absorb wrote (`wrote=head|segment|base`), a base's `reason=`, `adopted=true` and `overrun=true` where they hold, the rows its part holds (`rows_written=`), the standing rows its dead bitmaps name (`named=`), its part's bytes (`part_bytes=`), the segments the committed head lists and, on the segment route, the compare's seconds (`compare_seconds=`). Then one `replay-walk` record with each passing walk's own crate resident peak as one observation, and one `REPLAY_ABSORB_POOL_UNIT` record with the peak footprint of each absorb worker that read its memo whole, whatever the verdict, since the verdict moves no absorb's peak."""
    for config, walk in replayed.walks.items():
        kernel_exec._write_lines(
            [kernel_exec._tagged(line, config) if line.startswith("[t] ") else line for line in walk.lines]
        )
    for config in memos:
        absorb = replayed.absorbs.get(config)
        if absorb is None or not replayed.passed:
            continue
        if absorb.warning is not None:
            console.warn(absorb.warning)
        elif absorb.entries is not None:
            route = absorb.route
            tokens = [f"entries={absorb.entries}", f"wrote={absorb.wrote}"]
            if route.route == conform.BASE_ROUTE:
                tokens.append(f"reason={route.reason}")
            if route.adopted:
                tokens.append("adopted=true")
            if route.forced:
                tokens.append("overrun=true")
            tokens += [
                f"rows_written={absorb.rows_written}",
                f"named={absorb.named}",
                f"part_bytes={0 if absorb.part is None else absorb.part.size}",
                f"segments={absorb.segments}",
            ]
            if route.route == conform.SEGMENT_ROUTE:
                tokens.append(f"compare_seconds={absorb.compare_seconds:.2f}")
            console.timing(f"settle_memo_emit {config}", absorb.seconds, " ".join(tokens))
    width = min(threads, len(memos))
    walked = {
        config: walk.peaks.resident
        for config, walk in replayed.walks.items()
        if walk.error is None and walk.peaks is not None and walk.peaks.resident
    }
    if walked:
        record_pool(
            REPLAY_POOL_UNIT,
            width=width,
            worker_peaks=walked,
            controller_peak_bytes=peak_rss_self_bytes(),
        )
    absorbed = {
        f"{config} absorb": absorb.footprint
        for config, absorb in replayed.absorbs.items()
        if absorb.entries is not None
    }
    if absorbed:
        record_pool(
            REPLAY_ABSORB_POOL_UNIT,
            width=len(absorbed),
            worker_peaks=absorbed,
            controller_peak_bytes=peak_rss_self_bytes(),
            worker_reading="footprint",
        )


def run_rule_witnesses(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple | DecisionTable],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> dict:
    """Run the witness stage in this process: settle every configuration's certificates through the crate and check that each rule fires on its own certificate (`witness.check_rule_certificates`). This is the reachability half of the checks that fail the build on a rule that can never fire: the crate's fold fails on a rule no replayed row first-matches, and this fails on a rule whose replayed row no string reaches, which is what a wrong slot restriction in the worklist would produce. It runs here, on the tables the build just folded, because the certificates describe those tables; `--gates-only` reuses tables this stage already passed.

    Each configuration's walk reads the settle memo that the string replay fills and the oracle and the conformance sweep extend (`conform.settle_memo_files`, keyed per family from `memo_inputs`, as the oracle row cache is), so a window any of them has settled since the runes it names last changed is served from the file. After a full replay the walk serves the certificate windows the replay's texts reach from the file the replay just wrote, and settles the rest. The certificates ask for a small part of the file, so the walk loads only the rows its certificate texts can ask for (`conform._SettledWindowWalk.load_only_asked_by`). Its memo is read-only (`conform.SettleMemoFile.read_only`): the walk keeps what it settles in its own `windows` for the rest of its configuration's check and writes neither the file nor a part, so this stage writes no settle memo byte. After a full replay the windows it settles fresh are certificate windows that no sweep text reaches, which gate:conform's prune would remove, so it settles those again on every pass; on a narrowed pass some of them are windows naming an edited family that the oracle or the sweep also reaches, which that writer then adds to the file. A caller with its own spec has no memo inputs and no memo, and settles everything.

    `tables` maps each configuration to its decision table, alone or with its join table, which this stage never reads. `_witness_inputs` lifts what every configuration shares out of the per-configuration step and computes it once, and `_witness_config` is that step, which the stage's spawned worker runs too (`_rule_witnesses_child`), so this function and `_spawn_rule_witnesses` give the same summary over the same tables and files. The summary is written to `witness_summary.json` (`_witness_summary`); a failure is raised at the join, ahead of any glyph-chain failure. The build runs this stage in a spawned process of its own (`_spawn_rule_witnesses`), which the table-only branch (`_run_table_gates`) starts after the string replay, so it can load the file the replay wrote. The oracle also starts once the replay has returned, but it makes no write to a shared file until this stage has returned (`TableGates.wait_for_memo`): its pooled ranges return the windows they settle fresh, which the oracle absorbs into the files only after that wait, and its `--jobs 1` walks, which replace the files whole, start only after it. So this stage reads each file as the replay left it, and its served and fresh counts, and `witness_summary.json`, do not depend on when an oracle absorb lands.
    """
    shared, decisions, memos = _witness_inputs(spec, tables, out_dir, memo_inputs)
    records = {
        config: _witness_config(shared, config, decision, memos.get(config))
        for config, decision in decisions.items()
    }
    return _witness_summary(out_dir, records)


@dataclass(frozen=True)
class _WitnessShared:
    """What every configuration's step of the witness stage reads besides its own decision table and settle memo: the spec, the crate's formation-guard verdicts, and every configuration's reachable cells, which a guard certificate's walk under that configuration reads (`witness.guard_sources`). `_witness_inputs` computes it once, and the stage's worker gets it once, through its pool's initializer (`_rule_witnesses_init`)."""

    spec: ResolvedSpec
    guard_verdicts: FormationGuard
    sources: Mapping[str, frozenset[CellId]]


def _witness_inputs(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple | DecisionTable],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> tuple[_WitnessShared, dict[str, DecisionTable], dict[str, conform.SettleMemoFile]]:
    """The witness stage's inputs, each computed once in the calling process: what every configuration's step shares (`_WitnessShared`, with the guard verdicts from `kernel_exec.guard_sweep`, one sweep per spec per process, which in run_m1's process the oracle's setup reads too), each configuration's decision table without its join table, in `tables` order, and each configuration's settle memo in its read-only form (`conform.settle_memo_files`, `conform.SettleMemoFile.read_only`), none when `memo_inputs` is None."""
    decisions = {
        config: entry[0] if isinstance(entry, (tuple, list)) else entry for config, entry in tables.items()
    }
    memos = {
        config: replace(memo, read_only=True)
        for config, memo in conform.settle_memo_files(out_dir, spec, memo_inputs).items()
    }
    shared = _WitnessShared(spec, kernel_exec.guard_sweep(spec), witness.guard_sources(decisions))
    return shared, decisions, memos


def _witness_config(
    shared: _WitnessShared,
    config: str,
    decision: DecisionTable,
    memo: conform.SettleMemoFile | None,
) -> dict:
    """One configuration's step of the witness stage: check its certificates (`witness.check_rule_certificates`) against its decision table under its read-only settle memo, print its `[t] rule_witnesses[<config>]` line, whose `served=`, `unasked=` and `fresh=` count the rows the load kept, the rows it dropped and the windows the crate settled, and return its record for `witness_summary.json`."""
    started = time.perf_counter()
    report = witness.check_rule_certificates(
        shared.spec,
        features_for_config(config),
        decision,
        shared.guard_verdicts,
        memo=memo,
        sources=shared.sources,
    )
    record = {
        "rules": report.rules,
        "witnessed": len(report.witnessed),
        "guards": report.guards,
        "failures": list(report.failures),
        "served_windows": report.served,
        "unasked_windows": report.unasked,
        "fresh_windows": report.fresh,
    }
    console.timing(
        f"rule_witnesses[{config}]",
        time.perf_counter() - started,
        f"rules={report.rules} witnessed={len(report.witnessed)} guards={report.guards} served={report.served} unasked={report.unasked} fresh={report.fresh}",
    )
    return record


def _witness_summary(out_dir: Path, records: Mapping[str, dict]) -> dict:
    """Assemble the witness stage's summary from each configuration's record (`_witness_config`), in `tables` order, with every configuration's failures in that order and the first 50 of them listed, write it to `witness_summary.json` unless the file already holds its bytes (`output_sink.write_output`), and return it."""
    failures = [failure for record in records.values() for failure in record["failures"]]
    summary = {"pass": not failures, "configs": dict(records), "failures": failures[:50]}
    output_sink.write_output(
        out_dir / "witness_summary.json", json.dumps(summary, indent=2) + "\n", keep_if_equal=True
    )
    return summary


def _spawn_rule_witnesses(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> tuple[dict, int]:
    """Run the witness stage (`run_rule_witnesses`' step, over the same inputs) in one spawned worker, block until it has checked every configuration, and return the summary and the worker's peak memory. `_run_table_gates` calls this and says why the stage has a process of its own.

    This process lifts the stage's shared inputs once (`_witness_inputs`). The worker gets the spec, the guard verdicts and the guard sources once, through its pool's initializer (`_rule_witnesses_init`), and then one task a configuration, in `tables` order, each carrying the configuration's name, its decision table without its join table, and its read-only settle memo. This process waits for each result before it submits the next, so the worker and this process's pickler hold one table at a time. A result is small: the configuration's record and the worker's resident peak so far (`peak_rss_self_bytes`), so the stage's peak is the last result's, since that peak only rises. The worker prints each configuration's `[t] rule_witnesses[<config>]` line to the stdout it inherits as that configuration finishes, and this process writes `witness_summary.json` once the last has returned (`_witness_summary`). A task that raises stops the stage with its error. The pool is shut down however the stage ends, and its worker stays in run_m1's process group, so a Ctrl-C reaches it.
    """
    shared, decisions, memos = _witness_inputs(spec, tables, out_dir, memo_inputs)
    records: dict[str, dict] = {}
    peak = 0
    with _spawn_pool(1, len(decisions), initializer=_rule_witnesses_init, initargs=(shared,)) as pool:
        for config, decision in decisions.items():
            records[config], peak = pool.submit(
                _rule_witnesses_child, config, decision, memos.get(config)
            ).result()
    return _witness_summary(out_dir, records), peak


_WITNESS_WORKER: _WitnessShared | None = None


def _rule_witnesses_init(shared: _WitnessShared) -> None:
    """Initialize the witness stage's worker: turn its cyclic collector off for the rest of its life, as in run_m1's own process, and keep what every configuration's step reads (`_WitnessShared`)."""
    global _WITNESS_WORKER
    gc.disable()
    _WITNESS_WORKER = shared


def _rule_witnesses_child(
    config: str, decision: DecisionTable, memo: conform.SettleMemoFile | None
) -> tuple[dict, int]:
    """Run one configuration's step of the witness stage (`_witness_config`) in the worker `_spawn_rule_witnesses` starts, over what its initializer kept and the task's decision table and read-only settle memo, and return the configuration's record and the worker's resident peak so far (`peak_rss_self_bytes`)."""
    shared = _WITNESS_WORKER
    assert shared is not None, "the witness stage's worker runs under _rule_witnesses_init"
    return _witness_config(shared, config, decision, memo), peak_rss_self_bytes()


EMITTED_ORDER_SUMMARY = "emitted_order_summary.json"


def run_emitted_order(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    ready: Callable[[str], None] | None = None,
) -> dict:
    """Run the shipped-order stage: replay the settlement order the emitter ships (every configuration's table folded into one lookup and sorted by `emit_gsub._ordered_settle_rules`), first match wins, against every row of every configuration's window enumeration. The crate's `replay-emitted` subcommand (`rebuild/kernel-rs/src/shipped_order.rs`) does this, one process per configuration over the window rows the build just committed under `out_dir`, the base and segments its windows head lists merged by key (`kernel_exec.replay_emitted`), whose count each walk's `[t] emitted_order[<config>]` line gives as `parts=`. The order is built once (`emit_gsub.emitted_order_tsv`), its rules counted for the summary's `rules`, and encoded once, and each walk builds its configuration's context (`emit_gsub.emitted_context_tsv`); each walk's crate reads both from pipes it inherits (`kernel_exec.replay_emitted`), so neither reaches the disk, and every walk's order is written from the one encoded copy. The count goes to the crate beside the order, so a walk whose order ends short fails instead of walking a shorter one. Each row, renamed into the stream its configuration's marker lookups produce, must be matched by the first emitted rule of its input that admits it, with the row's own outcome. A `#NA` after a letter stands for every label in that slot and every later one (`fold::open_slot`, the reading the cross-configuration exchange uses), so for such a row the first match must give the row's outcome on every continuation the configuration's stream can carry, the row's own configuration's rules included. Where an emitted lookahead class admits only part of the row's deep class, which a rule folded from another configuration's fiber partition can do while still giving every member the row's outcome, each member is checked separately. A row matched with a different outcome fails the build, naming the configuration, the row, the continuation of its open slot, the emitted rule that fired and the table's own rule.

    This is the only check of the shipped order that reads the tables. The fold's partition check, the string replay and the witness stage each walk a configuration's rules in that configuration's own order, and read-back compares the font with the plan, not the plan with the tables. Without this stage the shipped order would be checked only by the HarfBuzz conformance sweep, which is keyed on code and behavior classes and skips a rune edit that adds no new rule shape. So this runs on every build, on the tables the build just folded, and a rune edit that reorders the fold without adding a rule shape fails here instead of at the next code change. It costs O(rows) per configuration and settles nothing. Every configuration walks at once up to the cores (`_core_bound_threads`), each in one small single-threaded process that holds the rules and labels and no memo or engine. Its other scratch, the per-row candidate lists and the continuation search's, is reused from row to row and bounded by the largest input's emitted rules and the labels their slots name, so the memory-derived width of the table build and the string replay does not apply to this pool. `ready`, when given, is called with the configuration's name on the walker thread before its head is read; the build passes `Packing.wait`, so each walk starts as soon as its own head is committed instead of after the whole build's. The walks raise the descriptor limit first (`kernel_exec.raise_descriptor_limit`), since six walks at a base and twenty segments each hold a part handle and a pipe a part beside their order and context pipes.
    """
    from rebuild.pipeline import emit_gsub

    configs = [config for config in conform.SETTLEMENT_CONFIGS if config in tables]
    threads = _core_bound_threads(len(configs))
    kernel_exec.raise_descriptor_limit()
    summary: dict = {"pass": True, "configs": {}, "error": None}
    text = emit_gsub.emitted_order_tsv(spec, tables)
    rules = len(text.splitlines()) - 2
    order = text.encode()
    del text
    summary["rules"] = rules

    def walk_one(config: str) -> tuple[str, dict[str, int]]:
        started = time.perf_counter()
        entry = tables[config]
        decision = entry[0] if isinstance(entry, (tuple, list)) else entry
        context = emit_gsub.emitted_context_tsv(spec, config, decision).encode()
        if ready is not None:
            ready(config)
        head = table_module.windows_head_path(out_dir, config)
        try:
            parts = len(table_module.windows_head(out_dir, config).parts)
        except (OSError, ValueError) as error:
            raise kernel_exec.KernelRunError(f"{head}: {error}") from None
        answer = kernel_exec.replay_emitted(
            head,
            config=config,
            table=out_dir / f"settlement-{config}.tsv",
            order=order,
            order_rules=rules,
            context=context,
            timings=True,
        )
        console.timing(
            f"emitted_order[{config}]",
            time.perf_counter() - started,
            " ".join([*(f"{key}={answer[key]}" for key in kernel_exec.EMITTED_COUNTS), f"parts={parts}"]),
        )
        return config, answer

    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="emitted-walk") as walkers:
        futures = [walkers.submit(walk_one, config) for config in configs]
        for finished in as_completed(futures):
            try:
                config, answer = finished.result()
            except kernel_exec.EmittedOrderDisagreement as error:
                summary["pass"] = False
                summary["error"] = str(error)
                for other in futures:
                    other.cancel()
                break
            summary["configs"][config] = answer
    summary["configs"] = {
        config: summary["configs"][config] for config in configs if config in summary["configs"]
    }
    output_sink.write_output(
        out_dir / EMITTED_ORDER_SUMMARY, json.dumps(summary, indent=2) + "\n", keep_if_equal=True
    )
    return summary


def serialized_tables(out_dir: Path, inputs: str) -> dict[str, DecisionTable] | None:
    """Return every settlement configuration's decision table as the build left it under `out_dir`, without the window rows, read from its head file through its windows head (`table.read_windows_head`), or None if any head or head file is missing, unreadable, not what the head records, or was written from sources other than the ones `inputs` names. A partial set is never returned, since it would sweep some configurations against tables the runes on disk no longer produce."""
    tables: dict[str, DecisionTable] = {}
    for config in conform.SETTLEMENT_CONFIGS:
        try:
            stamp, decision = table_module.read_windows_head(out_dir, config)
        except OSError, ValueError:
            return None
        if stamp != inputs:
            return None
        tables[config] = decision
    return tables


def tables_inputs() -> str:
    """Return the stamp serialized windows carry: `fingerprint.tables_value` plus one token for each semantics mode that is on by default (`kernel_exec.enumeration_tokens`: the simulated prospect, the shifted follower prefer slots, the class-grain deep slots), then, while the alphabet switch is set, `alphabet=` and its token (`spec_load.alphabet_token`). These environment settings change settlement semantics, enumeration grain or the alphabet without changing any hashed source, so without the tokens an enumeration made with a flag on, or over a small alphabet, would look current to a process with it off, or over the whole alphabet, and the reverse, and the sweep would replay tables the in-process kernel no longer produces.

    The stamp covers what the fixpoint reads, so its data half is `fingerprint.table_data_value`, not `fingerprint.data_value`. The alias map, the divergence ledger and the kern sidecar are the baseline oracle's comparison inputs, and the contact allow-list, which is in no fingerprint component, is the defect gate's. All of them are read against tables that are already built, so editing one leaves every enumeration on disk as current as it was, and `--gates-only` can re-check over the tables and font on disk. The same holds for the comparison-side code `fingerprint.COMPARISON_CODE_MODULES` names: the stamp's code half is `fingerprint.table_code_paths`, the pipeline code without those modules, and rebuild/test_build_code_closure.py checks that the build never reaches them. These edits are still checked: every one of those labels is in the artifact cycle's run_m1 key (`artifact_cycle.run_m1_skip_lines`), so the green record no longer matches and the gates they feed, the defect gate included, re-run over the artifacts on disk.
    """
    inputs = fingerprint.tables_value(REPO_ROOT)
    for token in kernel_exec.enumeration_tokens():
        inputs = f"{inputs}+{token}"
    alphabet = spec_load.alphabet_token()
    if alphabet is not None:
        inputs = f"{inputs}+{spec_load.ALPHABET_LABEL}={alphabet}"
    return inputs


def settle_memo_inputs() -> oracle_cache.SettleMemoInputs:
    """Return the disk-derived half of the settle memo's keys (`oracle_cache.SettleMemoInputs`). Call it where `tables_inputs` is called, before `load_default_spec`, so a key can only name content the settlements are at least as new as. Every entry point that shares a memo between phases takes this beside the tables' stamp and passes both to `conform.settle_memo_files` once the spec is loaded."""
    return oracle_cache.settle_memo_inputs(REPO_ROOT)


def full_run(
    *,
    jobs: int,
    started: float,
    kernel_threads: int | None = None,
    replay_threads: int | None = None,
    scratch_beside_default: int | None = None,
    fresh_oracle_cache: bool = False,
) -> None:
    """Run a bare `python -m rebuild.pipeline.run_m1` once `run_m1_gates.main` has parsed its command line: the Stage A capture, the pre-gate guards, `run_ligature_outgoing`, the deletion of the Stage A record immediately before the run's first write (`run_m1_gates._retract_stage_a`; that write is the guards' refilter when the subset tables are stale, and the build otherwise), the build (`run`), the defect exit, the Manual-pin gate, the oracle at `jobs` once the string replay has returned, and the join of the table-only branch, then the record of this process's own peak footprint (`PARENT_POOL_UNIT`, one reading, `peak_rss.peak_footprint_self_bytes`), then the run_m1 gate, the Stage A record once that gate passes (`run_m1_gates._settle_stage_a`), the green record and the check line (timed from `started`). The gates it calls are in rebuild/pipeline/run_m1_gates.py, outside the tables' stamp, but this function stays here, inside it: `--gates-only` never runs it, so a build-side check added here must restamp the tables rather than earn a green without running."""
    from rebuild.pipeline import run_m1_gates
    from rebuild.tools.artifact_cycle import (
        _defect_gate_failure,
        evaluate_run_m1_gate,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(REPO_ROOT)

    stage = fingerprint.stage_a(REPO_ROOT)
    run_m1_gates._run_pregate_guards(OUT_DIR)
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
    spec = load_default_spec()
    before = run_m1_key()
    gates: TableGates | None = None
    defect_failure: str | None = None
    try:
        run_ligature_outgoing(spec)
        run_m1_gates._retract_stage_a(OUT_DIR)
        console.phase("run_total")
        start = time.perf_counter()
        summary, gates = run(
            spec=spec,
            inputs=inputs,
            kernel_threads=kernel_threads,
            memo_inputs=memo_inputs,
            replay_threads=replay_threads,
            scratch_beside_default=scratch_beside_default,
        )
        console.timing("run_total", time.perf_counter() - start, rss_token(process_peak_rss_bytes()))
        console.say(json.dumps(summary, indent=2))
        defect_failure = _defect_gate_failure(summary)
        if defect_failure is not None:
            raise SystemExit(defect_failure)
        console.phase("run_manual_pin_gate")
        start = time.perf_counter()
        pin_gate = run_m1_gates.run_manual_pin_gate(spec=spec)
        console.timing("run_manual_pin_gate", time.perf_counter() - start)
        console.say(json.dumps(pin_gate, indent=2))
        pin_failure = run_m1_gates.manual_pin_gate_failure(pin_gate)
        if pin_failure is not None:
            raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")
        gates.wait_for_replay()
        console.phase("run_oracle")
        start = time.perf_counter()
        oracle_summary = run_m1_gates.run_oracle(
            spec=spec,
            jobs=jobs,
            fresh_cache=fresh_oracle_cache,
            memo_inputs=memo_inputs,
            memo_ready=gates.wait_for_memo,
        )
        console.timing("run_oracle", time.perf_counter() - start)
        console.say(json.dumps(oracle_summary, indent=2))
        gates.join()
    except (
        SystemExit,
        compile_font.GroupTooLargeError,
        readback.ReadbackError,
        emit_gsub.EmitError,
        conform.WitnessError,
    ) as error:
        red = gates.first_red() if gates is not None else None
        failure: BaseException = error if red is None else red
        run_m1_gates._settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        run_m1_gates._record_cli_check(run_m1_gates._failed_check("run_m1", str(failure)), started)
        if red is None and defect_failure is not None:
            raise SystemExit(f"{defect_failure}; see pipeline_summary.json")
        if isinstance(failure, SystemExit):
            raise failure
        raise SystemExit(str(failure))
    finally:
        if gates is not None:
            gates.close()
    record_pool(
        PARENT_POOL_UNIT,
        width=1,
        worker_peaks={"parent": peak_footprint_self_bytes()},
        controller_peak_bytes=peak_rss_self_bytes(),
        worker_reading="footprint",
    )
    gate = evaluate_run_m1_gate(summary, pin_gate, oracle_summary)
    if gate.ok:
        run_m1_gates._settle_stage_a(OUT_DIR, stage)
    run_m1_gates._settle_green(
        RUN_M1_GREEN, before, gate.ok, run_m1_key, "run_m1", files_of=lambda: run_m1_skip_files(REPO_ROOT)
    )
    run_m1_gates._record_cli_check(gate, started)
    if not gate.ok:
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")


def cli() -> None:
    """Run `python -m rebuild.pipeline.run_m1`: refuse while the alphabet switch is set outside the fault kit (`spec_load.small_alphabet_refusal`), before anything is read or written, and otherwise hand the command line to `run_m1_gates`, so a bare run, `--gates-only` and `--conform-only` all refuse."""
    from rebuild.pipeline import run_m1_gates

    refusal = spec_load.small_alphabet_refusal("run_m1")
    if refusal is not None:
        raise SystemExit(refusal)
    # This batch is short-lived, and its large live heap contains almost no cyclic garbage worth scanning.
    gc.freeze()
    gc.disable()
    run_m1_gates._run_cli()


if __name__ == "__main__":
    cli()
