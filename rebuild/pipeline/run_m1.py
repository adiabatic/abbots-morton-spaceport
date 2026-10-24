"""The M1 integration driver: runs the full pipeline over the real rune files and writes the `doc/rebuild-design.md` §8 artifacts under rebuild/out/m1/.

First, `build_tables` builds the decision and join tables for every settlement configuration (`conform.SETTLEMENT_CONFIGS`) in one kernel-crate process. It enumerates and folds `default` first and each other configuration as a delta over `default`'s memo. As it folds each configuration it checks first-match-wins against the rows, writes the TSVs, writes one certificate per rule built from the table's row chains, and writes the window enumeration stamped with the fingerprint of its sources. `--conform-only` takes its glyph inventory from that enumeration and stops with an error when it is stale or missing. The payloads are packed on a background pool once their heads are read.

Two branches then run over the tables. The table-only branch (`_run_table_gates`, on one background thread) runs the string replay (`run_replay_strings`, recorded in `replay_summary.json`), which walks every configuration's stored rules over every text up to `REPLAY_MAX_LENGTH` against the crate's own settlement, then the witness stage (`run_rule_witnesses`, in a spawned process of its own, recorded in `witness_summary.json`), which settles every certificate and checks that its rule fires. Beside those two it runs the shipped-order walk (`run_emitted_order`), where each configuration's walk waits for that configuration's pack. The glyph chain (`_run_glyph_chain`, on the calling thread) mints the glyphs (settled cells named by their cell labels, the raw cmap glyphs, the marker, locked and ss10 copies, and the namer dot pair), runs the defect gates under the reviewed allow-list, emits GSUB and GPOS (also writing `behavior_classes.json`, the record key `rebuild/tools/deep_sweep.py` reads), compiles the font, and runs read-back (rebuild/pipeline/readback.py). Read-back re-parses the written font, checks it against the emitters' plan, and checks the GSUB's uint16 subtable-offset headroom against its floor and the settlement lookup's largest packed group against its ceiling in the same parse. Its `[t] readback` line ends with HEAD's short commit id and the settlement lookup's size figures (`readback.budget_figures`), so the timings journal keeps them for every build and `make cycle-timings ARGS='--by-commit'` lists them.

`full_run` then runs the Manual-pin gate and the oracle (rebuild/pipeline/run_m1_gates.py). The oracle starts once the string replay has returned, beside the witness stage, and writes to the settle memo files only after the witness stage's writes are on disk. `full_run` joins the table-only branch after the oracle, before it decides the run_m1 gate. The join raises the first failure in serial order (the packing, the replay, the witnesses, the shipped order), and any of those is raised in place of a glyph-chain failure, so a failing build reports what a serial build would.

Settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set use the same names. The raw cmap glyph for each rune is the bare rune name, drawn as the isolated cell with no curs anchors. Marker, locked, and ss10 copies reuse that drawing. Under ss10 the ss10 input substitution replaces every letter's cmap glyph with its anchor-free `.ss10` copy before formation, so no ligature forms, nothing settles, each letter keeps its own cluster, and every junction is a break. That is why the overlay configuration (`conform.OVERLAY_CONFIGS`) has no table: read-back checks that the ss10 input substitution covers every letter cmap glyph and that the copies appear in no other stage, the conformance sweep (gate:conform's exhaustive HarfBuzz sweep) sweeps the overlay at `conform.OVERLAY_MAX_LENGTH`, and the oracle compares its rows against the bare stream, using the copies' `hmtx` advances for positions.

The split-buffer check runs inside gate:conform's sweep, at maximum length 4 on every build and at maximum length 5 or more through `make conform-deep`. Read-back's boundary-glyphs stage checks the ZWNJ glyph's zero advance and empty outline on the written font bytes, so the conformance sweep does not check them at every shaped slot.

Run as `uv run python -m rebuild.pipeline.run_m1`. The entry hands the command line to `run_m1_gates.main`, which parses it and calls `full_run` for a bare run. `--conform-only` runs only the conformance sweep against the M1.otf on disk. `--gates-only` re-runs the defect gate, the Manual-pin gate and the oracle over the tables and font already on disk, without rebuilding anything. It is the fast way to re-check an edit to a comparison-side input: the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, or the code `fingerprint.COMPARISON_CODE_MODULES` names. The tables' stamp leaves all of these out (`fingerprint.table_code_paths`; rebuild/test_build_code_closure.py checks that the build never reaches that code). A `--gates-only` pass records run_m1's green when a prior green exists and everything that changed since is comparison-side, so the artifact cycle plans this gates-only rerun itself and the pass after it skips run_m1.

A full run and a `--gates-only` rerun both serve what they can from the per-row verdict stores that rebuild/pipeline/oracle_cache.py keeps beside the tables. A ledger edit changes no family key and no stamp line, so every row and position verdict is served and only the ledger match and the audit run. An alias edit changes the family keys of the families it names, so only the rows that reach them are re-derived. An edit to the kern sidecar or to the position comparison's code closure keeps the row verdicts and re-shapes the positions, an edit to the comparison's code outside that closure (the walk, the crate, the store) re-derives every row and keeps each position whose settled cells did not change, and a classifier edit serves both. `--fresh-oracle-cache` ignores the stores, and `--gates-only` may read them but never writes one.
"""

from __future__ import annotations

import functools
import gc
import gzip
import hashlib
import json
import multiprocessing
import shutil
import subprocess
import tempfile
import threading
import time
import zlib
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Mapping, Sequence

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
    ss10_copy_name,
)
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable
from rebuild.tools import console
from rebuild.tools.cycle_timings import record_pool
from rebuild.tools.memory_budget import usable_cores
from rebuild.tools.peak_rss import (
    ReapedPeaks,
    footprint_token,
    peak_rss_self_bytes,
    process_peak_rss_bytes,
    rss_token,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
PUNCTUATION_YAML = REPO_ROOT / "glyph_data" / "punctuation.yaml"
CONTACT_ALLOW_YAML = REPO_ROOT / "rebuild" / "m1-contact-allow.yaml"

RAW_STANCE = "cmap"


def _spawn_pool(jobs: int, units: int, *, max_tasks_per_child: int | None = None) -> ProcessPoolExecutor:
    """Return a spawn pool of at most `jobs` workers and at most `units`, the number of tasks the caller will submit before its absorbs. The conformance sweep submits one task per unit (`run_m1_gates.sweep_units`) and passes `max_tasks_per_child=1`, so each unit and each absorb runs in a fresh process and reports its own peak. The oracle submits one task per row range and reuses its workers."""
    workers = max(1, min(jobs, units))
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        max_tasks_per_child=max_tasks_per_child,
    )


MEMO_STAMP_FORMAT = "ams-m1-memo-stamp/2"
# The keys of a rune's dump that are prose the engine never reads: a record's or an unlock's `why`, and the rune's notes and ductus. `rune_content_digests` drops them so rewording a rationale invalidates no memo, as every other digest in this tree ignores prose.
PROSE_KEYS = frozenset({"why", "notes", "ductus"})


def _without_prose(payload):
    if isinstance(payload, dict):
        return {key: _without_prose(value) for key, value in payload.items() if key not in PROSE_KEYS}
    if isinstance(payload, list):
        return [_without_prose(value) for value in payload]
    return payload


def rune_content_digests(spec: ResolvedSpec) -> dict[str, str]:
    """Return each modeled rune's SHA-256 over its resolved content: the rune's part of the spec dump the crate reads (`kernel_io.rune_payload`), without `PROSE_KEYS`. It hashes the resolved rune instead of the file because the engine reads the resolved rune: a ligature carries the outgoing stroke it inherits from its trailing component, and a ligature registration rewrites every left condition its trailing component reaches. So the digest changes when what the engine reads of the rune changes. A cross-file `against:` holds only its target rune's name and an optional record id, and the engine consults it only where a prefer of that rune collides with one of the resolving rune's, in a window that names both, so the target's own digest covers it. The memo therefore needs no `spec_load.rune_closure` pass over the digests. It works for any spec, a fixture's included, so the memo stamp is one function of the spec in hand. The payload is hashed in the dump's own key order, never sorted, because the crate reads a rune's mappings in that order: stance declaration order ranks the stances `policy.order` omits, and exit declaration order is the final tiebreak's last key (kernel_io's module docstring)."""
    return {
        name: hashlib.sha256(
            json.dumps(_without_prose(kernel_io.rune_payload(rune)), separators=(",", ":")).encode()
        ).hexdigest()
        for name, rune in spec.runes.items()
    }


def memo_structure_stamp(spec: ResolvedSpec, root: Path = REPO_ROOT) -> str:
    """Return the whole-store half of the memo stamp: what a memo entry depends on apart from each rune's content and each predicate class's membership, which the stamp records per rune and per class (`rune_content_digests`, `class_memberships`) so that an edit to one invalidates only the windows that read it. The crate reads nothing but the spec dump and its command line (`kernel_exec.build_table_files`). The lines are the code `table_build_code_paths` names (the crate, and the module-grain import closure of `kernel_exec` and `kernel_io` outside rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, so the width and telemetry helpers that closure stops at are not hashed), the dumped registry with each class reduced to its name (`_registry_digest`), the rune order, the semantics tokens, the alphabet with its ligature sequences and the resolved rune-local groups, and the capability features. The rest of `fingerprint.table_code_paths` and the data files reach the crate, if at all, only through the dump, which these lines and the per-rune and per-class records hash as data, or through command-line values that cannot make a memo entry wrong: the configuration and mode set each memo head records, which the crate checks the file against; the previous memo files and the edited runes and moved classes, which `previous_memos` and `memo_edited` choose from the stamps themselves; the width, which the crate's unit test `a_table_fan_out_files_the_same_bytes_at_every_width` (rebuild/kernel-rs/src/fanout.rs) shows changes no table; the schedule flags, which the identity tests in rebuild/kernel-rs/tests/cli.rs and rebuild/test_kernel_exec.py show change no table; and `--inputs`, `--memo-stamp` and `--timings`, which only label or time what the crate writes. So an edit to the spec loader, to an emitter, to read-back or to run_m1.py moves none of these code lines, and it moves the stamp only where it changes the dump or what the stamp's own code in run_m1.py and `spec_load.capability_features` compute from it; `replay_structure_stamp` hashes that code instead."""
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
    """The SHA-256 of the registry's part of the spec dump (`kernel_io.registry_payload`) with each predicate class reduced to its name, in dump order. Membership stays out because `class_memberships` records it, so a rune joining a class moves only that class. The keys keep the dump's order, because the crate stores every mapping in that order (rebuild/kernel-rs/src/model.rs)."""
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
    """Return what a memo written under stamp `previous` cannot be trusted for under `current`, or None when it cannot be read at all. None means a different stamp format, a changed structure (the crate and the modules `TABLE_BUILD_CODE_MODULES` names, the dumped registry with each class reduced to its name, the rune order, the alphabet and its ligatures, the groups, the semantics tokens, the capability features), or a recorded rune this spec no longer models. A rune new to the spec counts as edited, although a new rune also changes the structure stamp, which rejects the memo first. A class present in only one stamp counts as changed. An empty delta means every window in the memo is still valid."""
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
class PreviousMemos:
    """The previous build's memo files that a table build reads before settling a window itself: the directory they were unpacked into, the configurations whose memo was unpacked, and the runes and predicate classes that no memo may be trusted for."""

    directory: Path
    configs: tuple[str, ...]
    edited: tuple[str, ...]
    moved_classes: tuple[str, ...]


def previous_memos(
    out_dir: Path, stamp: str, scratch: Path, configs: Sequence[str] = conform.SETTLEMENT_CONFIGS
) -> PreviousMemos | None:
    """Unpack into `scratch` the previous build's memos under `out_dir` that this build may read, or return None when there are none. Each named configuration's packed memo is read for its head, checked against this process's mode set and against `stamp` through `memo_edited`, and unpacked for the crate only if both pass. The edited runes are the union over every memo unpacked, so a rune that one memo cannot be trusted for is served by no memo. Only the memos of `configs` are read, because the crate reads only the memos of the configurations it builds; a narrowed build therefore unpacks only its own configurations' memos, and a memo that narrowed builds never rewrite cannot add every rune changed since the last whole build to the union. A memo whose head, stamp, or gzip stream fails is left in place and not read, and this build's own memo replaces it."""
    modes = "+".join(kernel_exec.enumeration_tokens()) or "pinned"
    edited: set[str] = set()
    moved_classes: set[str] = set()
    unpacked: list[str] = []
    for config in configs:
        packed = kernel_exec.memo_path(out_dir, config)
        head = kernel_exec.read_memo_head(packed)
        if head is None or head.config != config or head.modes != modes:
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
        unpacked.append(config)
    if not unpacked:
        return None
    return PreviousMemos(
        directory=scratch,
        configs=tuple(unpacked),
        edited=tuple(sorted(edited)),
        moved_classes=tuple(sorted(moved_classes)),
    )


TABLE_BUILD_CODE_MODULES = frozenset(
    {
        "rebuild/pipeline/kernel_exec.py",
        "rebuild/pipeline/kernel_io.py",
        "rebuild/pipeline/labels.py",
        "rebuild/pipeline/model.py",
        "rebuild/pipeline/settle.py",
        "rebuild/pipeline/table.py",
    }
)


def table_build_code_paths(root: Path = REPO_ROOT) -> list[Path]:
    """Return the code that `memo_structure_stamp` and a `TableBuildRecord`'s `code` digest both hash: the crate's manifest, lock and sources, and the modules `TABLE_BUILD_CODE_MODULES` names, as repo-relative paths. That roster is the module-grain import closure of the crate driver (`kernel_exec`, whose `build_table_files` runs the crate and is the whole timed `kernel_build_tables` span) and of the spec dump's writer (`kernel_io`), outside rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`. Being an import closure, it also holds modules the two import for work other than running the crate and writing the dump, and hashing those only drops memos more often. rebuild/test_build_code_closure.py checks the roster against that closure in both directions. The rest of `fingerprint.table_code_paths` reaches the crate, if at all, only through the dump, which the memo stamp hashes as data, or through command-line values that cannot make a memo entry wrong (`memo_structure_stamp` names them). So an edit there moves the memo stamp only where it changes the dump or the code that computes the stamp: the stamp's own code in run_m1.py, `spec_load.capability_features`, and the file digests `fingerprint.path_lines` takes. A letter batch's `baseline_subset.M1_ALPHABET` edit and a gate edit such as one to `conform` keep `code`, so a build before a batch can be paired with the first build after it."""
    kernel = root / "rebuild" / "kernel-rs"
    named = {root / relative for relative in TABLE_BUILD_CODE_MODULES}
    return [path for path in fingerprint.table_code_paths(root) if kernel in path.parents or path in named]


@dataclass(frozen=True)
class TableBuildRecord:
    """What the timings journal records of a table build into an `out_dir`, so that two builds with the same code and a different alphabet can be paired across a letter batch: the memo structure stamp the build's memos carry (`memo_structure_stamp`, which hashes the code `code` covers beside the dumped registry, the rune order, the semantics tokens, the alphabet and the capability features, so under one `code` it moves with the spec, with the mode set, or with an edit to its own code in run_m1.py or to `spec_load.capability_features`, which `code` does not cover), the digest of the code the table build runs (`table_build_code_paths`, through `fingerprint.code_file_digest`, which drops its Python files' docstrings and its Rust files' whole-line comments and hashes the crate's manifest and lock as raw bytes), how many configurations read a previous build's memo, the runes and predicate classes those memos were not trusted for, the table build's width, and the number of runes in the alphabet. A build that read no memo is a fresh build, as the first build after a new rune always is, since the rune moves the structure stamp and `memo_edited` then rejects every memo. `tokens` writes the record as the `key=value` tail of the `[t] kernel_build_tables` line, and `cycle_timings.parse_inner_timings` reads each token back under its key."""

    structure: str
    code: str
    memos_read: int
    edited: tuple[str, ...]
    classes: tuple[str, ...]
    width: int
    runes: int

    def tokens(self) -> str:
        return " ".join(
            [
                f"structure={self.structure}",
                f"code={self.code}",
                f"memos_read={self.memos_read}",
                f"edited={','.join(self.edited) or '-'}",
                f"classes={','.join(self.classes) or '-'}",
                f"width={self.width}",
                f"runes={self.runes}",
            ]
        )


def table_build_record(
    spec: ResolvedSpec, stamp: str, previous: PreviousMemos | None, width: int, root: Path = REPO_ROOT
) -> TableBuildRecord:
    """Return the `TableBuildRecord` of a build of `spec` at `width` whose memos carry `stamp` and that read `previous`. The structure comes out of `stamp` itself, so the record names the stamp the memos were written under. `build_tables` takes the record beside the stamp, before the crate starts, so an edit made while the crate runs cannot give `code` a tree the build did not run."""
    return TableBuildRecord(
        structure=json.loads(stamp)["structure"],
        code=fingerprint.hash_paths(root, table_build_code_paths(root)),
        memos_read=len(previous.configs) if previous else 0,
        edited=previous.edited if previous else (),
        classes=previous.moved_classes if previous else (),
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


# The phase lines of the build this invocation ran, for its check line: the crate's `[t]` lines, the `kernel_build_tables` line with its `TableBuildRecord` tokens and the `build-tables` process's resident peak and peak footprint, then the `readback` line with the settlement lookup's figures and the commit. A cycle's run_m1 step parses the same lines from the output it captured, but a standalone check line has none, so `build_tables` and the glyph chain keep them here, `run_m1_gates.main` empties the list when an invocation starts, and `run_m1_gates._record_cli_check` parses it.
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
    overlap_memo_writes: bool | None = None,
    scratch_beside_default: int | None = None,
) -> tuple[dict[str, tuple], dict[str, str]]:
    """Build the decision and join tables for the named settlement configurations, all of them unless `configs` narrows the set. The resolved spec is dumped once, then one crate `build-tables` process (`kernel_exec.build_table_files`) enumerates `default`'s fixpoint and folds it, then enumerates each other configuration as a delta over `default`'s finished memo, or from scratch beside `default` (`scratch_beside_default`), prepares its fold as it finishes, and, once every configuration has exchanged the windows the others keep live (`rebuild/kernel-rs/src/crossconfig.rs`), finishes each fold. The crate writes the settlement TSV, the join TSV and the window enumeration itself; nothing is folded on the Python side.

    A narrowed set is for `rebuild/tools/scratch_build.py`, which searches for a record to change. The crate writes only the configurations it is asked for and deletes nothing, so a narrowed build into a directory another build wrote leaves that build's files for the other settlement configurations in place. `scratch_build.scratch_out_dir` keeps such builds out of `rebuild/out/m1`. A narrowed set writes the same windows and join tables as the whole set, but its settlement tables can differ, since it has fewer configurations to import windows from. A set that does not include `default` enumerates each member from scratch, since there is no finished memo to build a delta over, so its width is derived from the from-scratch bound. The build and the artifact cycle ask for the whole set. Overlay configurations get no tables. Any table files named for a configuration outside `conform.SETTLEMENT_CONFIGS`, an overlay configuration's or one that has left the set, are removed first (`stale_table_files`), so a whole-set build leaves only its own tables in the directory.

    Per configuration, Python reads the enumeration's head back for the rules, the reachable cells and the fired provenance every downstream stage needs, parses the join TSV back for the defect gates, and packs the plain window payload into its `.gz` artifact. The head reads run on a thread pool at the table build's width once the crate has exited, and this call waits for them. The packing (`_pack_config`, memo file included) runs on a `Packing` pool at `_core_bound_threads` width, one packer per configuration up to the cores, whatever the crate's width, because a packer holds only a zlib stream and a copy buffer and the compressor releases the interpreter lock. When `packing` is passed, the tables are returned as soon as the heads are read, and the caller waits on each pack through `Packing.wait` and closes the pool. Without it, packing finishes before the return.

    A build with an `out_dir` also reuses its trace memos across builds. `previous_memos` reads the previous build's `memo-<config>.tsv.gz` files for the configurations this build names, unpacks those whose stamp still matches, and names the runes whose content changed as edited, so a window naming no edited rune settles as it did last time. This build's own memos are packed under the same names, stamped with `memo_stamp` over the spec in hand. Such a build ends its `[t] kernel_build_tables` line with its `TableBuildRecord`, which says whether it read those memos. A caller with no `out_dir` reads and writes no memo, and its line carries no record. Every build's line then carries the `build-tables` process's own resident peak and peak footprint (`_table_build_tail`). Either way the crate's phase lines and that line are kept in `_build_phase_lines` for run_m1's check line.

    `out_dir`, when given, receives the TSVs listed in `doc/rebuild-design.md` §8. The second returned mapping is each configuration's `table.table_digest` as the crate reported it, computed while the window rows are still in memory, which avoids recomputing the fixpoint. The crate also prints it on stdout, where `rebuild/tools/scaling_sweep.py` reads it. Both returned mappings are built in `configs` order however the configurations finish, so completion order cannot affect an artifact.

    `inputs` is `tables_inputs` over the sources this spec was loaded from. Passing it with `out_dir` keeps each configuration's window enumeration beside the TSVs under the stamp that names those sources, where `run_m1_gates.run_font_conformance` reads it instead of rebuilding anything. Without it, the payload is deleted once its head is read. A caller building its own spec must omit it, because the fingerprint names the repository's rune files and does not describe tables built from other runes.

    `kernel_threads` is how many worker slots the build runs at once, each enumerating a delta configuration or running `default`'s fold preparation, capped at the configuration count and the cores this process may run on, neither of which is a memory limit. The default it falls back to is the memory limit: `kernel_exec.kernel_threads_default` is the widest width whose `kernel_exec.table_build_booking_bytes` fits this machine's memory, which books what `default`'s finished memo holds, one parked fold product per configuration this build names, and what each slot holds beyond its own parked product. So the cap only narrows a memory-derived width and never widens one.

    `overlap_memo_writes` says whether the crate writes the memo files beside the delta wave and the folds rather than ahead of them. The artifact cycle passes the choice it derives beside gate:make-test's pytest pool; when it is `None`, as for a bare run, `kernel_exec.memo_writes_overlap` derives it at the width the build takes with nothing beside it, so it is on wherever that width's booking with `kernel_exec.MEMO_WRITE_OVERLAP_BYTES` added still fits the memory.

    `scratch_beside_default` says at most how many of the heaviest deltas the crate enumerates from scratch beside `default` instead of over its memo, so they need not wait for it; the crate takes them in whole tiers of equal unlocking-rune count. The artifact cycle passes the count it derives beside gate:make-test's pytest pool; when it is `None`, `kernel_exec.deltas_from_scratch` derives it at the width and memo-write order the build takes with nothing beside it: as many as fit the memory with each booked at `kernel_exec.SCRATCH_PEAK_BYTES`.
    """
    configs = tuple(configs)
    from_scratch = "default" not in configs
    threads = _table_build_threads(kernel_threads, len(configs), from_scratch=from_scratch)
    if overlap_memo_writes is None:
        overlap_memo_writes = kernel_exec.memo_writes_overlap(
            threads, configs=len(configs), from_scratch=from_scratch
        )
    if scratch_beside_default is None:
        scratch_beside_default = kernel_exec.deltas_from_scratch(
            threads, configs=len(configs), overlap=overlap_memo_writes, from_scratch=from_scratch
        )
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
        previous = None
        if out_dir is not None and stamp is not None:
            start = time.perf_counter()
            previous = previous_memos(out_dir, stamp, directory / "previous-memos", configs)
            console.timing(
                "previous_memos",
                time.perf_counter() - start,
                f"edited={','.join(previous.edited) if previous else '-'} classes={','.join(previous.moved_classes) if previous else '-'}",
            )
        record = table_build_record(spec, stamp, previous, threads) if stamp is not None else None
        start = time.perf_counter()
        crate = kernel_exec.build_table_files(
            spec_path,
            tables_dir,
            list(configs),
            inputs=inputs if inputs is not None else kernel_exec.UNSTAMPED_WINDOWS,
            threads=threads,
            symbols=len(conform.spec_alphabet(spec)),
            timings=True,
            previous_memos=previous.directory if previous else None,
            edited=previous.edited if previous else (),
            moved_classes=previous.moved_classes if previous else (),
            memo_stamp=stamp,
            overlap_memo_writes=overlap_memo_writes,
            scratch_beside_default=scratch_beside_default,
        )
        digests = crate.digests
        line = console.timing(
            "kernel_build_tables", time.perf_counter() - start, _table_build_tail(record, crate.peaks)
        )
        _build_phase_lines[:] = [*crate.timings, line]

        def read_one(config: str) -> tuple[str, tuple]:
            payload = tables_dir / f"windows-{config}.tsv"
            with payload.open("rt", encoding="utf-8") as handle:
                _stamp, decision = table_module.read_windows(handle, windows=False)
            joins = table_module.read_join_tsv(tables_dir / f"joins-{config}.tsv")
            if out_dir is None:
                payload.unlink()
            else:
                packers.submit(
                    config,
                    functools.partial(_pack_config, tables_dir, config, inputs is not None),
                    len(configs),
                )
            return config, (decision, joins)

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
        tables_dir / f"joins-{config}.tsv",
        table_module.windows_path(tables_dir, config),
    )


def stale_table_files(tables_dir: Path) -> list[Path]:
    """Return the table files under `tables_dir` named for a configuration outside `conform.SETTLEMENT_CONFIGS`: an overlay configuration, which settles nothing, or one that has left the set since an earlier build wrote its tables. `build_tables` deletes them, so a table never outlives the configuration that produced it or stands in for one that settles nothing."""
    tables_dir = Path(tables_dir)
    configs = {
        path.name.removeprefix(prefix).removesuffix(suffix)
        for prefix, suffix in (("settlement-", ".tsv"), ("joins-", ".tsv"), ("windows-", ".tsv.gz"))
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
    """What the table-only branch leaves for `TableGates`. `replay_ready` is set once the string replay has returned, with every settle memo file it fills on disk, and `memo_ready` once the witness stage's process has returned, with its settle memo writes on disk too. Both are also set as soon as the replay-then-witness sequence fails. The `*_red` fields hold the exception each stage would have raised in a serial run. The summaries reach their readers as the JSON files each stage writes."""

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
    """Run the table-only branch on one background thread beside the glyph chain. The string replay runs first, at its own width (`replay_threads`, or the width `_replay_threads` derives from `kernel_exec.REPLAY_PEAK_BYTES`), and the witness stage runs after it, in one spawned process this thread waits on (`_spawn_rule_witnesses`). The order matters: a full replay writes each configuration's settle memo file whole (`conform.absorb_replay_memo`), and the witness stage then loads the rows of that file its certificates can ask for, writes the windows it settled fresh as a part, and merges the part into the file before its process returns. `state.replay_ready` is set once the replay has returned, and the oracle starts after it. `state.memo_ready` is set once the witness stage's process has returned with its files on disk, and the oracle's own memo writes wait for it. Both are set as soon as that sequence fails, and `TableGates` also sets them when this thread ends any other way, so a wait on either never hangs.

    The witness stage's walk is CPU-bound Python, so it gets a process of its own. On a thread of run_m1's process it would hold the interpreter lock that the main thread needs for the glyph chain and the oracle's setup, and slow both, the work that reads and hashes files most (the cache keys within `[t] oracle_setup`). The process runs one serial walk over the configurations. A process per configuration would need a memory-derived width of its own (`doc/parallelism.md`) and would take cores from the oracle's pool, which takes every core. The process gets the spec, each configuration's decision table, the output directory and the memo inputs, and runs the formation-guard sweep itself. It writes `witness_summary.json` and merges its parts into the settle memo files before it returns, so `TableGates.wait_for_memo` orders its writes before any oracle write. Its `[t] rule_witnesses[<config>]` lines go to the stdout it inherits, and the `[t] rule_witnesses` line this thread prints carries its peak memory (`rss_token`).

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
            console.timing("replay_strings", time.perf_counter() - start, rss_token(process_peak_rss_bytes()))
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
    """The handle `run` returns for its table-only branch, with the two points where the oracle waits for it. `wait_for_replay` is called before the oracle starts: it blocks until the string replay has returned (every settle memo file the replay fills is on disk, and the replay crate has exited), reports the wait as `[t] settle_memo_wait`, and raises the replay's failure, or the witness stage's if that stage has already failed. `wait_for_memo` is called before the oracle writes a shared settle memo file: it blocks until the witness stage's process has returned, with its parts merged into the settle memo files, reports the wait as `[t] witness_memo_wait`, and raises either failure.

    For each configuration, the witness stage writes the windows it settles fresh as a part and merges the part into that configuration's file (`conform.absorb_settle_memo_parts`), and that merge reads the file and writes it back whole with the part's windows added. An oracle write between that read and that write would be lost, and an oracle write based on a read made before the merge's write would drop the stage's windows. So the oracle makes no write to a shared file before `wait_for_memo` (`run_m1_gates.run_oracle`'s `memo_ready`): its pooled ranges write only parts, which it merges into the files after the wait, and its `--jobs 1` walks, which replace the files whole, start after the wait. The witness stage is then the only writer while the two overlap, and every oracle write includes what the stage wrote.

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
    overlap_memo_writes: bool | None = None,
    scratch_beside_default: int | None = None,
) -> tuple[dict, TableGates]:
    """Build the tables, then run two branches over them. The table-only branch (`_run_table_gates`, on one background thread) runs the string replay, the witness stage (in a spawned process of its own) and the shipped-order walks. The glyph chain (minting, the defect gates, the emission, the compile and read-back) runs on the calling thread, writes `pipeline_summary.json`, and returns its summary with the branch's `TableGates` handle without joining the branch. The caller joins it: `full_run` calls `wait_for_replay` before the oracle, passes `wait_for_memo` for the oracle to call before its memo writes, calls `join` after the oracle so the gate covers both branches, and calls `close` in a `finally`. When the glyph chain raises, the branch's first failure is raised in its place if there is one, so a failed replay is reported as incomplete tables and not as whatever the glyph chain made of them.

    `inputs` is `tables_inputs` over the sources `spec` was loaded from, computed before the load so it can only name content the tables are at least as new as. Passing it keeps the window enumeration under `out_dir` for the conformance sweep and the shipped-order walk. A caller running its own spec omits it, and the walk does not run. `memo_inputs` is `settle_memo_inputs`, computed at the same moment. It names the settle memo files the string replay fills and the witness stage, the oracle and the conformance sweep then load; without it the replay writes no memo, the witness stage settles every certificate, and nothing is shared. `kernel_threads` applies only to the table build, whose per-configuration memory cost that width was derived from, and so do `overlap_memo_writes` and `scratch_beside_default` (`build_tables`). `replay_threads` applies only to the string replay, whose memory cost is `kernel_exec.REPLAY_PEAK_BYTES` and which derives its own width (`_replay_threads`) when none is given. The packing and the shipped-order walks run one task per configuration up to the cores (`_core_bound_threads`) regardless of either width. The witness stage and the walks can still be running when the oracle's pool starts, since the oracle waits only for the string replay (`TableGates.wait_for_replay`), and they share the machine with that pool.
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
            overlap_memo_writes=overlap_memo_writes,
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
    (out_dir / "readback_summary.json").write_text(json.dumps(readback_report, indent=2) + "\n")
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
    (out_dir / "pipeline_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


# The conformance sweep's maximum length (`conform.SWEEP_MAX_LENGTH`), not one more. The walk's cost is its count of distinct raw windows, and every first-position window of a length-N text is distinct: the alphabet size to the Nth per configuration, each a full settlement, since the engine's trace memo is keyed on the raw slots. So each extra letter of depth multiplies the walk by the alphabet size. Over the whole alphabet, a full replay at maximum length 5 costs every configuration minutes even under the deep replay's memo ceiling (`make cycle-timings ARGS='--by-step'` reports it under `replay-deep`), and even narrowed to one family it would cost more than the conformance sweep run it lets the cycle skip. Checking past this depth is the job of the periodic deep sweep (`make conform-deep`), which settles every text it shapes.
REPLAY_MAX_LENGTH = conform.SWEEP_MAX_LENGTH
REPLAY_FORMAT = "ams-m1-replay/1"
REPLAY_SUMMARY = "replay_summary.json"
# The pool unit a replay that walked any text records its per-walk peak under (`_record_replay_walk`), which the replay-walk row of `make job-costs` holds against `kernel_exec.REPLAY_PEAK_BYTES`.
REPLAY_POOL_UNIT = "replay-walk"
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

    The walk also produces the settle memo. With `memo_inputs` (`settle_memo_inputs`, computed before the spec was loaded), every full replay asks the crate to write its window memo for each walked configuration beside the tables (`kernel_exec.replay_memo_dump`) and absorbs each one into the configuration's `conform.SettleMemoFile` under the stamp and family keys `conform.settle_memo_files` computes. So the witness stage, the oracle and the conformance sweep load what the replay settled instead of settling it again. Only the walked configurations' files are touched, so a narrowed walk never dumps, reads, absorbs or deletes a configuration it did not walk. Every dump is deleted in this function whatever the walk or the absorb did, and a failed absorb is a warning, not a failure, since every reader settles what the file lacks. When any configuration's file is missing or fails `conform.settle_memo_standing`, every text is walked to refill it, which can widen the walk beyond what the structure stamp requires. The code `oracle_cache.settle_memo_stamp` hashes (`oracle_cache.oracle_code_paths`: the modules `oracle_cache.ORACLE_ROW_CODE_PATHS` names and the crate) is inside the build-side code `locality_lines` hashes, so an edit that moves that stamp's `oracle_code` line also moves the structure stamp. A narrowed walk (a rune edit) writes no memo and leaves the existing files to drop their own stale entries.

    The record written beside the tables is what the next build's walk is narrowed against, so it carries the structure stamp and every rune digest as well as the counts. It is written whether the walk passed or failed: a disagreement is recorded with the crate's message, and `_run_table_gates` raises it as `SystemExit`. The width is `_replay_threads`: `replay_threads` when given, else the machine's memory divided by `kernel_exec.REPLAY_PEAK_BYTES` (what one configuration's walk holds: the trace memo over the windows its texts reach, plus the window memo's inverse label map and block buffer), capped at the configuration count and the cores, so every text replays in one round on both fleet machines. The absorbs run after the crate has exited, one spawn process per walked configuration at that same width. Each holds one configuration's dump, columns and probe index while it builds the file, a fraction of what `REPLAY_PEAK_BYTES` allows for one configuration, so they use the replay's width and have no constant of their own. A walk that answers records what one walk held (`_record_replay_walk`).
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
    for config in memos:
        with suppress(FileNotFoundError):
            kernel_exec.replay_memo_dump(out_dir, config).unlink()
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
    try:
        if families is None or families:
            try:
                summary["configs"] = kernel_exec.replay_strings(
                    spec,
                    out_dir,
                    configs,
                    max_length=REPLAY_MAX_LENGTH,
                    families=families,
                    threads=threads,
                    timings=True,
                    memo_dir=out_dir if emitting else None,
                    on_peak=functools.partial(_record_replay_walk, walks=min(threads, len(configs))),
                )
            except kernel_exec.ReplayDisagreement as error:
                summary["pass"] = False
                summary["error"] = str(error)
        if emitting and summary["pass"]:
            with _spawn_pool(threads, len(memos)) as pool:
                absorbs = {
                    config: pool.submit(_absorb_replay_memo, out_dir, config, memo, spec)
                    for config, memo in memos.items()
                }
                for config, future in absorbs.items():
                    entries, seconds, error = future.result()
                    if error is not None:
                        console.warn(error)
                    else:
                        console.timing(f"settle_memo_emit {config}", seconds, f"entries={entries}")
    finally:
        for config in memos:
            with suppress(FileNotFoundError):
                kernel_exec.replay_memo_dump(out_dir, config).unlink()
    if recordable:
        (out_dir / REPLAY_SUMMARY).write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _record_replay_walk(peak: int, *, walks: int) -> None:
    """Record what one walk of the string replay held as one observation of the `replay-walk` pool (`cycle_timings.record_pool`): the crate process's own peak RSS (`kernel_exec.replay_strings`'s `on_peak`) divided by the walks it ran at once. The walks are threads of that one process, so no figure per walk exists, and this share is the wide run's figure `REPLAY_PEAK_BYTES`'s comment takes beside the solo walk's."""
    record_pool(
        REPLAY_POOL_UNIT,
        width=walks,
        worker_peaks={"per walk": peak // walks},
        controller_peak_bytes=peak_rss_self_bytes(),
    )


def _absorb_replay_memo(
    out_dir: Path, config: str, memo: conform.SettleMemoFile, spec: ResolvedSpec
) -> tuple[int | None, float, str | None]:
    """Absorb one configuration's window memo into its settle memo file, in a pool worker. Return the row count and the seconds the absorb took, which the parent reports as `[t] settle_memo_emit <config>`, or else a warning to print instead. A dump the crate never wrote, or one `conform.absorb_replay_memo` rejects, is only a warning, since the readers settle what the file lacks."""
    dump = kernel_exec.replay_memo_dump(out_dir, config)
    started = time.perf_counter()
    if not dump.is_file():
        return None, 0.0, f"settle memo: the replay wrote no window memo for {config} at {dump}"
    try:
        entries = conform.absorb_replay_memo(dump, memo, spec, config)
    except (kernel_exec.KernelRunError, OSError) as error:
        return None, 0.0, f"settle memo: {dump} not absorbed ({error}); the readers settle instead"
    return entries, time.perf_counter() - started, None


def run_rule_witnesses(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple | DecisionTable],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> dict:
    """Run the witness stage: settle every configuration's certificates through the crate and check that each rule fires on its own certificate (`witness.check_rule_certificates`). This is the reachability half of the checks that fail the build on a rule that can never fire: the crate's fold fails on a rule no replayed row first-matches, and this fails on a rule whose replayed row no string reaches, which is what a wrong slot restriction in the worklist would produce. It runs here, on the tables the build just folded, because the certificates describe those tables; `--gates-only` reuses tables this stage already passed.

    Each configuration's walk shares the settle memo that the string replay fills and the oracle and the conformance sweep load (`conform.settle_memo_files`, keyed per family from `memo_inputs`, as the oracle row cache is), so a window any of them has settled since the runes it names last changed is settled once. After a full replay, this stage serves every certificate's windows from the file the replay just wrote and settles only what a narrowed replay left out. The certificates ask for a small part of the file, so the walk loads only the rows its certificate texts can ask for (`conform._SettledWindowWalk.load_only_asked_by`). It writes the windows it settled fresh as a part in a scratch directory (`SettleMemoFile.write_path`, the same form each row range of the pooled oracle uses), and this stage merges the part into the shared file (`conform.absorb_settle_memo_parts`) inside the configuration's timed span. The file then holds every existing window plus the fresh ones, and a walk that settled nothing writes no part and leaves the file unchanged. Because the key is per family, a rune edit invalidates only the memo entries naming an edited family, so only the certificates naming one are settled again. A caller with its own spec has no memo inputs and no memo, and settles everything.

    `tables` maps each configuration to its decision table, alone or with its join table, which this stage never reads. The summary is written to `witness_summary.json`; a failure is raised at the join, ahead of any glyph-chain failure. The build runs this stage in a spawned process of its own (`_spawn_rule_witnesses`), which the table-only branch (`_run_table_gates`) starts after the string replay, so it can load the file the replay wrote. The oracle also starts once the replay has returned, but it makes no write to a shared file until this stage has returned (`TableGates.wait_for_memo`). Its pooled ranges may map the file before or after this stage's merge, and they write the windows they settle fresh as parts, which the oracle merges into the files only after that wait. Its `--jobs 1` walks replace the files whole and start only after that wait, so any file one of them maps already holds this stage's windows. So no oracle write can produce a file without this stage's windows or be overwritten by this stage's.
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
                sources=tables,
            )
            if memo is not None:
                conform.absorb_settle_memo_parts(memo, [part], spec)
            per_config[config] = {
                "rules": report.rules,
                "witnessed": len(report.witnessed),
                "guards": report.guards,
                "failures": list(report.failures),
                "served_windows": report.served,
                "unasked_windows": report.unasked,
                "fresh_windows": report.fresh,
            }
            failures.extend(report.failures)
            console.timing(
                f"rule_witnesses[{config}]",
                time.perf_counter() - started,
                f"rules={report.rules} witnessed={len(report.witnessed)} guards={report.guards} served={report.served} unasked={report.unasked} fresh={report.fresh}",
            )
    summary = {"pass": not failures, "configs": per_config, "failures": failures[:50]}
    (out_dir / "witness_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _spawn_rule_witnesses(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> tuple[dict, int]:
    """Run the witness stage (`run_rule_witnesses`) in one spawned process, block until it returns, and return its summary and the process's peak memory. The process gets each configuration's decision table without its join table. `_run_table_gates` calls this and says why the stage has a process of its own."""
    decisions = {
        config: entry[0] if isinstance(entry, (tuple, list)) else entry for config, entry in tables.items()
    }
    with _spawn_pool(1, 1) as pool:
        return pool.submit(_rule_witnesses_child, spec, decisions, out_dir, memo_inputs).result()


def _rule_witnesses_child(
    spec: ResolvedSpec,
    decisions: Mapping[str, DecisionTable],
    out_dir: Path,
    memo_inputs: oracle_cache.SettleMemoInputs | None,
) -> tuple[dict, int]:
    """Run the witness stage in the process `_spawn_rule_witnesses` starts, with the cyclic collector off as in run_m1's own process, and return its summary and this process's peak memory (`peak_rss_self_bytes`)."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        summary = run_rule_witnesses(spec, decisions, out_dir, memo_inputs)
    finally:
        if enabled:
            gc.enable()
    return summary, peak_rss_self_bytes()


EMITTED_ORDER_SUMMARY = "emitted_order_summary.json"


def run_emitted_order(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    out_dir: Path,
    ready: Callable[[str], None] | None = None,
) -> dict:
    """Run the shipped-order stage: replay the settlement order the emitter ships (every configuration's table folded into one lookup and sorted by `emit_gsub._ordered_settle_rules`), first match wins, against every row of every configuration's window enumeration. The crate's `replay-emitted` subcommand (`rebuild/kernel-rs/src/shipped_order.rs`) does this, one process per configuration over the packed enumeration the build just wrote under `out_dir`. Each row, renamed into the stream its configuration's marker lookups produce, must be matched by the first emitted rule of its input that admits it, with the row's own outcome. A `#NA` after a letter stands for every label in that slot and every later one (`fold::open_slot`, the reading the cross-configuration exchange uses), so for such a row the first match must give the row's outcome on every continuation the configuration's stream can carry, the row's own configuration's rules included. Where an emitted lookahead class admits only part of the row's deep class, which a rule folded from another configuration's fiber partition can do while still giving every member the row's outcome, each member is checked separately. A row matched with a different outcome fails the build, naming the configuration, the row, the continuation of its open slot, the emitted rule that fired and the table's own rule.

    This is the only check of the shipped order that reads the tables. The fold's partition check, the string replay and the witness stage each walk a configuration's rules in that configuration's own order, and read-back compares the font with the plan, not the plan with the tables. Without this stage the shipped order would be checked only by the HarfBuzz conformance sweep, which is keyed on code and behavior classes and skips a rune edit that adds no new rule shape. So this runs on every build, on the tables the build just folded, and a rune edit that reorders the fold without adding a rule shape fails here instead of at the next code change. It costs O(rows) per configuration and settles nothing. Every configuration walks at once up to the cores (`_core_bound_threads`), each in one small single-threaded process that holds the rules and labels and no memo or engine. Its other scratch, the per-row candidate lists and the continuation search's, is reused from row to row and bounded by the largest input's emitted rules and the labels their slots name, so the memory-derived width of the table build and the string replay does not apply to this pool. `ready`, when given, is called with the configuration's name on the walker thread before its enumeration is opened; the build passes `Packing.wait`, so each walk starts as soon as its own pack is written instead of after the whole build's.
    """
    from rebuild.pipeline import emit_gsub

    configs = [config for config in conform.SETTLEMENT_CONFIGS if config in tables]
    threads = _core_bound_threads(len(configs))
    summary: dict = {"pass": True, "configs": {}, "error": None}
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
                " ".join(f"{key}={answer[key]}" for key in kernel_exec.EMITTED_COUNTS),
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
    """Return the stamp serialized windows carry: `fingerprint.tables_value` plus one token for each semantics mode that is on by default (`kernel_exec.enumeration_tokens`: the simulated prospect, the shifted follower prefer slots, the class-grain deep slots). These environment flags change settlement semantics or enumeration grain without changing any hashed source, so without the tokens an enumeration made with a flag on would look current to a process with it off, and the reverse, and the sweep would replay tables the in-process kernel no longer produces.

    The stamp covers what the fixpoint reads, so its data half is `fingerprint.table_data_value`, not `fingerprint.data_value`. The alias map, the divergence ledger and the kern sidecar are the baseline oracle's comparison inputs, and the contact allow-list, which is in no fingerprint component, is the defect gate's. All of them are read against tables that are already built, so editing one leaves every enumeration on disk as current as it was, and `--gates-only` can re-check over the tables and font on disk. The same holds for the comparison-side code `fingerprint.COMPARISON_CODE_MODULES` names: the stamp's code half is `fingerprint.table_code_paths`, the pipeline code without those modules, and rebuild/test_build_code_closure.py checks that the build never reaches them. These edits are still checked: every one of those labels is in the artifact cycle's run_m1 key (`artifact_cycle.run_m1_skip_lines`), so the green record no longer matches and the gates they feed, the defect gate included, re-run over the artifacts on disk.
    """
    inputs = fingerprint.tables_value(REPO_ROOT)
    for token in kernel_exec.enumeration_tokens():
        inputs = f"{inputs}+{token}"
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
    overlap_memo_writes: bool | None = None,
    scratch_beside_default: int | None = None,
    fresh_oracle_cache: bool = False,
) -> None:
    """Run a bare `python -m rebuild.pipeline.run_m1` once `run_m1_gates.main` has parsed its command line: the Stage A capture, the pre-gate guards, `run_ligature_outgoing`, the deletion of the Stage A record immediately before the run's first write (`run_m1_gates._retract_stage_a`; that write is the guards' refilter when the subset tables are stale, and the build otherwise), the build (`run`), the defect exit, the Manual-pin gate, the oracle at `jobs` once the string replay has returned, and the join of the table-only branch, then the run_m1 gate, the Stage A record once that gate passes (`run_m1_gates._settle_stage_a`), the green record and the check line (timed from `started`). The gates it calls are in rebuild/pipeline/run_m1_gates.py, outside the tables' stamp, but this function stays here, inside it: `--gates-only` never runs it, so a build-side check added here must restamp the tables rather than earn a green without running."""
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
            overlap_memo_writes=overlap_memo_writes,
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
    gate = evaluate_run_m1_gate(summary, pin_gate, oracle_summary)
    if gate.ok:
        run_m1_gates._settle_stage_a(OUT_DIR, stage)
    run_m1_gates._settle_green(
        RUN_M1_GREEN, before, gate.ok, run_m1_key, "run_m1", files_of=lambda: run_m1_skip_files(REPO_ROOT)
    )
    run_m1_gates._record_cli_check(gate, started)
    if not gate.ok:
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")


if __name__ == "__main__":
    from rebuild.pipeline import run_m1_gates

    # This batch is short-lived, and its large live heap contains almost no cyclic garbage worth scanning.
    gc.freeze()
    gc.disable()
    run_m1_gates._run_cli()
