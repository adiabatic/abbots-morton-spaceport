"""The baseline oracle (M1-PLAN section 6): compare settlement with the section 13.1 baseline one configuration at a time, and match each divergent row against the divergence ledger (rebuild/m1-divergences.yaml). For the rows the ledger calls ink-identical, also compare the drawn positions with the kern-normalized old positions through the position comparison in rebuild/pipeline/oracle_positions.py.

Nothing here builds a table or a font; everything runs against tables and an M1.otf that are already built. So this module is left out of the stamp a serialized window enumeration carries (`fingerprint.table_code_paths` subtracts `fingerprint.COMPARISON_CODE_MODULES`), and rebuild/test_build_code_closure.py fails if the import graph from any build-side module or from `run_m1.run` reaches it. An edit to `classify_divergence`, a predicate, `compile_ledger`, `_match_compiled`, or the position comparison therefore keeps every enumeration on disk, and `run_m1 --gates-only` re-runs the oracle over them. Like every module `fingerprint.COMPARISON_CODE_MODULES` names, it stays in `fingerprint.pipeline_code_paths`, so an edit here still changes the Stage A `pipeline_code` component, the artifact cycle's run_m1 skip key, and the Stage A record the review corpus's manifest copies.

The rows the oracle classifies are produced in conform.py. `_compare_row` and `_SettledWindowWalk` are the entry points whose import graph `oracle_cache.ORACLE_ROW_CODE_PATHS` must cover, outside the closure test's stop-set (`WIDTH_AND_TELEMETRY_MODULES`), and the record codec (`_cached_verdict`, `_served_verdict`) and `_verify_served_sample` sit beside them. No module under that stamp imports this one, so after a classifier edit every row verdict is still served from the store; rebuild/test_oracle_code_closure.py fails if conform.py's import graph reaches this module. The position comparison's entry module is rebuild/pipeline/oracle_positions.py, and `oracle_cache.POSITION_CODE_PATHS` names its import closure. No module in that closure imports this one either, so a classifier edit also keeps every stored position, and the same test checks that direction. `_compare_config` calls the position comparison through the `oracle_positions` module, not through imported names, so monkeypatching `oracle_positions._position_mismatch` affects both the rows this pass shapes and `_verify_served_positions` (rebuild/test_conform.py relies on this).

`compare_against_baseline` is the serial path. For each configuration it streams the subset table and settles each row, or, given an `OracleRowCache`, serves the row verdict from the previous pass's store and walks only the rows an edit can reach (rebuild/pipeline/oracle_cache.py documents what the keys cover). It compares ligation, junctions, and cells through the alias map and matches each divergent row against the `CompiledLedger` (`compile_ledger`). Rows the ledger calls ink-identical are shaped against M1.otf to compare positions, or have their position verdict served from the same store under the position key.

run_m1's parallel path splits the work into row ranges of one configuration's table (`OracleShard`, planned by `oracle_shard_plan` for the worker count). `oracle_config_worker` runs `_compare_config` over one range in its own process and ships the range's audit rows, compressed (`AuditBlocks`), home in a POSIX shared memory segment, its store records, one gzip member, in another, and the windows it settled fresh, compressed (`conform.FreshMemo`), in a third (`ship`); its result names them. The parent's `InOrderWriter` submits each configuration's section of the audit, once its last range has returned, to the same pool (`audit_section_task`), which compares the configuration's rows, in row order, with the standing audit's section through the compare the serial path's writer runs over the whole audit (`SectionCompare`, `AuditWriter`) and ships the section's bytes in this pass's part home, and the parent writes the part from the sections in the audit's order; it hands each configuration's members at the same moment to the stores' writer (`oracle_cache.StoreSet`), and `merge_config_shards` sums the ranges' counts. The output is the same as the serial path's because `_compare_config` addresses every row by its absolute index in the table, so the store records, the scheduled re-derivation, and the verification samples all key on it, and because the audit's compare is a merge by key that never pairs rows across two configurations' sections.

The audit, `divergence-audit.tsv`, is held as a base and segments under a head (`rebuild/pipeline/row_delta.py`'s plain keyed merge): the base is the whole file, its header line and every row in the audit's order, and each pass that changes rows writes one segment of upserts and tombstones keyed by configuration and window, in the base's own order (`audit_key`). The audit's writer plans its route before any range starts (`AuditPlan`, `AuditRoute`), compares the pass's rows with the materialized audit in lockstep, over the whole audit on the serial path and a configuration's section at a time on the pooled one, and writes the rows that differ, or nothing and the head alone; a tree from before the segment format has its single file adopted by link as generation 0's base. A base stays the plain file it was adopted or first written as until its first fold; every segment and every base a fold writes is a run of gzip members, the header line alone and then one a configuration (`row_delta.MemberPart`), indexed by the head (`row_delta.Part.members`), so the corpus build and the pooled path's section tasks inflate only the sections they read, and a plain part's sections are found by bisection (`plain_sections`). The materialized audit (`materialized_audit`) is the whole file over the live rows, byte for byte, its parts inflated, and the head records when its content last moved (`CONTENT_TIME_STAMP`), which the review corpus takes as its identity.

For the overlay configuration (ss10), no settlement table produces the new side. Its rows are walked by `conform.IsolatedOverlayWalk`, which returns every letter bare from the registry alone, and shaped by `shapers.IsolatedOverlayShaper`, which uses the copies' `hmtx` advances instead of HarfBuzz. Read-back's isolation check and the conformance sweep over the overlay are what make both valid. So the old font's ss10 rows are compared with an all-bare stream, and no settlement or shaping runs for them.

An M1-only configuration (`conform.M1_ONLY_CONFIGS`) has no baseline table, and the oracle never compares it; its configurations are `conform.ORACLE_CONFIGS`.
"""

from __future__ import annotations

import functools
import gc
import gzip
import hashlib
import io
import itertools
import math
import operator
import os
import secrets
import shutil
import sys
import threading
import time
import zlib
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from multiprocessing import shared_memory
from pathlib import Path
from typing import BinaryIO, Callable, Container, Iterable, Iterator, Mapping, Protocol, Sequence, cast

import yaml

from rebuild.pipeline import (
    baseline_subset,
    kernel_exec,
    oracle_cache,
    oracle_positions,
    output_sink,
    position_record,
    row_delta,
    settle,
    spec_load,
)
from rebuild.pipeline.conform import (
    ORACLE_CONFIGS,
    OVERLAY_CONFIGS,
    DivergentRow,
    IsolatedOverlayWalk,
    FreshMemo,
    SettleMemoFile,
    _cached_verdict,
    _compare_row,
    _served_verdict,
    _SettledWindowWalk,
    _verify_served_sample,
)
from rebuild.pipeline.labels import BOUNDARY_GLYPH_NAMES, features_for_config, load_alias_map
from rebuild.pipeline.model import CellId, ResolvedSpec, isolated_overlay_active
from rebuild.pipeline.shapers import IsolatedOverlayShaper, Shaper
from rebuild.tools import console
from rebuild.tools.peak_rss import peak_footprint_self_bytes
from rebuild.validation.rowmodel import Row, iter_rows

# Baseline rows read and walked at a time: the oracle's counterpart of `conform.TEXT_CHUNK`.
ORACLE_ROW_CHUNK = 65536
# How many unmatched rows a result keeps for `oracle_summary.json` to quote. Every unmatched row is written to the audit regardless.
ORACLE_UNMATCHED_EXEMPLARS = 20
# The audit bytes a range holds uncompressed before it compresses them as one block (`AuditBlocks`). It sets only the audit's compressed framing in transit, never the audit's bytes. Deflate's window is 32 KiB, so a block this size compresses nearly as well as the range's whole audit would. A smaller block costs less on both sides: a range grows its pending bytes by reallocation up to the block, once a block, and macOS's allocator keeps each round's freed buffers dirty, so the range pays for them several times over; and the configuration's section task inflates one block at a time, so the block sizes each buffer it inflates into (both measured in `var/keep/ram-inventory/v4/11a-overrun/`).
ORACLE_AUDIT_BLOCK = 1 << 20
# The time an overlay-configuration row costs relative to a settlement-configuration row, which `oracle_shard_plan` weights row counts by. The overlay's walk reads the registry and its shaper reads `hmtx`, so its worker takes about half the wall-clock time over the same rows (measured from the `[t] oracle ss10` and `[t] oracle default` lines in the cycle journal).
OVERLAY_ROW_COST = 0.5


@dataclass(frozen=True)
class OracleShard:
    """Rows `[first_row, stop_row)` of one configuration's subset table, the `index`-th of the `of` ranges the configuration is cut into. `stop_row` None is the table's end. With `of == 1` the range is the whole table, and its store and `[t]` label are named as an unsharded run names them."""

    config: str
    first_row: int = 0
    stop_row: int | None = None
    index: int = 0
    of: int = 1

    @property
    def segment(self) -> int | None:
        """The range's place among its configuration's store segments, or None for the one range of an uncut configuration, which writes its store whole."""
        return None if self.of == 1 else self.index

    @property
    def label(self) -> str:
        """The range's `[t]` label: the configuration alone when it is uncut, else the configuration and `k/n`."""
        return self.config if self.of == 1 else f"{self.config} {self.index + 1}/{self.of}"


def oracle_shard_plan(
    jobs: int, rows_by_config: Mapping[str, int | None], configs: Iterable[str] = ORACLE_CONFIGS
) -> list[OracleShard]:
    """Split the oracle's rows into ranges for `jobs` workers. The configurations with a known row count are laid end to end, each row weighted by its configuration's cost, and the sequence is cut at every multiple of one worker's share of the total. The ranges are returned heaviest first, the order a pool should start them: the small ranges at the end fill in behind whichever workers finish early, so the wall-clock time comes within one range of a share. A configuration's last range has `stop_row` None, so a row count lower than the table's makes the balance worse but never skips a row. A configuration with an unknown row count (None or zero, as with a hand-made table directory) stays one range over its whole table and sorts first, as if it were the heaviest. `jobs` of one cuts nothing."""
    configs = tuple(configs)

    def cost(config: str) -> float:
        return OVERLAY_ROW_COST if config in OVERLAY_CONFIGS else 1.0

    counted = {config: rows for config in configs if (rows := rows_by_config.get(config))}
    total = sum(rows * cost(config) for config, rows in counted.items())
    share = total / jobs if jobs > 1 and total > 0 else None
    ranges: dict[str, list[tuple[int, int | None]]] = {}
    laid = 0.0
    for config in configs:
        rows = counted.get(config)
        if rows is None or share is None:
            ranges[config] = [(0, None)]
            continue
        weight = rows * cost(config)
        bounds = [0]
        cut = math.floor(laid / share) + 1
        while cut * share < laid + weight:
            bounds.append(round((cut * share - laid) / cost(config)))
            cut += 1
        bounds.append(rows)
        laid += weight
        pieces = [(first, stop) for first, stop in zip(bounds, bounds[1:]) if stop > first]
        ranges[config] = [*pieces[:-1], (pieces[-1][0], None)]

    def weight_of(shard: OracleShard) -> float:
        rows = counted.get(shard.config)
        if rows is None:
            return math.inf
        return ((rows if shard.stop_row is None else shard.stop_row) - shard.first_row) * cost(shard.config)

    shards = [
        OracleShard(config, first, stop, index, len(pieces))
        for config, pieces in ranges.items()
        for index, (first, stop) in enumerate(pieces)
    ]
    return sorted(shards, key=weight_of, reverse=True)


@dataclass
class BaselineReport:
    rows_compared: int = 0
    divergent_rows: int = 0
    positions_compared: int = 0
    positions_excluded: int = (
        0  # divergent rows not sent through the position comparison: a junction or ligation divergence, or no single ink-identical ledger match
    )
    positions_served: int = 0
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched_count: int = 0
    unmatched_exemplars: list[DivergentRow] = field(default_factory=list)
    multi_matched_count: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShipTo:
    """The names of the three POSIX shared memory segments one row range ships its bytes home in: `audit` for its compressed audit lines, `store` for its store segment, and `memo` for the settle memo windows it settled fresh, compressed a block at a time (`conform.FreshMemo`), which the parent hands its configuration's absorb by name. The parent's `InOrderWriter` chooses them (`InOrderWriter.ship_to`), so it can unlink a segment a range made even when the range's result never reaches it, or no section task or absorb maps it."""

    audit: str
    store: str
    memo: str


@dataclass(frozen=True)
class Shipped:
    """Bytes a row range or a conformance-sweep unit hands `run_m1`'s parent, or the settle memo absorb its parent starts, in a POSIX shared memory segment (`ship`) instead of on its pickled result: `name` is the segment and `lengths` the pieces laid end to end in it. A result reaches the parent through the pool's pipe as one message, which the parent reads into a buffer it grows to the message's size, and macOS's allocator keeps such buffers, and the large objects unpickled from them, dirty once freed, so what a range sends would cost the parent several times its size in footprint (measured in `var/keep/ram-inventory/v4/11a-overrun/`). A segment's pages count in the footprint of a process that has them mapped and has touched them, and in no other: the pages a range writes leave its footprint when it closes its mapping after the copy, and the parent's mapping touches none of them until the parent reads them."""

    name: str
    lengths: tuple[int, ...]


@dataclass
class OracleConfigResult:
    """One configuration's oracle counts, or one row range's before `merge_config_shards` combines them, returned to the parent process by `oracle_config_worker`. Unmatched rows are kept as a count plus the first `ORACLE_UNMATCHED_EXEMPLARS` of them, and multi-matched rows as a count alone (`multi_matched_count`). `oracle_summary.json` needs only those, and every divergent row reaches the parent as its audit line instead: `audit_blocks` names the shared memory segment that holds every divergent row's line, a multi-matched row's with its matched ids joined by `+`, compressed a block at a time (`AuditBlocks`), so the audit has them all. `store_segment` names the one that holds the range's store records as one gzip member (`oracle_cache.RowWriter` with no path): a cut configuration's range's records alone, and an uncut configuration's whole file, its header and trailer around them; it is None when the range writes no store. `fresh_memo` holds the counts of the settle memo windows the range settled fresh (`conform.FreshMemo` with no blocks), or None when it settled none, and `memo_blocks` names the segment that holds their blocks; `run_m1_gates.run_oracle` takes both off the result as it lands and hands them, with the configuration's other ranges', to its absorb. Any of the segments is None when the range has no such bytes or its caller named no segments (`oracle_config_worker`'s `ship_to`), and the result carries only names, lengths and counts, never the bytes (`Shipped`). The parent's `InOrderWriter` takes the audit and store segments off the result as it lands, handing the audit segment's name to the configuration's section task, and a folded result carries none of them. `positions_served` counts the rows among `positions_compared` whose position verdict came from the store instead of from shaping. `pass_ordinal` is the ordinal the range's store records were written under, or None when no store was written; the stores' writer records it in the set head's header for the configuration and measures the ages column against it. `peak_footprint_bytes` is the worker's peak footprint (`peak_rss.peak_footprint_self_bytes`), recorded in the pool record that `make job-costs` reads."""

    config: str
    rows_compared: int = 0
    divergent_rows: int = 0
    positions_compared: int = 0
    positions_excluded: int = 0
    positions_served: int = 0
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched_count: int = 0
    unmatched_exemplars: list[DivergentRow] = field(default_factory=list)
    multi_matched_count: int = 0
    notes: list[str] = field(default_factory=list)
    pass_ordinal: int | None = None
    peak_footprint_bytes: int = 0
    audit_blocks: Shipped | None = None
    store_segment: Shipped | None = None
    fresh_memo: FreshMemo | None = None
    memo_blocks: Shipped | None = None


@dataclass(frozen=True)
class OracleRowCache:
    """The row cache settings one oracle run passes to each configuration, so each can read the previous pass's verdicts and build this pass's. The parent computes the stamps and family keys once, before the first row is compared, and passes them to the workers. A worker that re-read the rune tree could see files edited mid-run, which is the race `run_m1_gates.run_oracle` checks for again at promotion. `position_environment` and `position_keys` are the position store's stamp and keys, computed from the font the same way; without them no position verdict is served. `read_dir` is where a committed store lives and `write_dir` the directory this pass's stores are written into, by the parent's stores' writer (`oracle_cache.StoreSet`), and either may be None; a worker only builds its records in memory when `write_dir` is set. A pass that may read but not write (`--gates-only`, which recompiles nothing and so may not write a build input) leaves `write_dir` None, which opens its stores read-only and so skips the scheduled re-derivation (`open_row_cache`), and sets a nonzero `rotation`: the pass ordinal advances only when a store is written, so the rotation is what moves the verification samples on such a pass (see `oracle_cache.RowStore`)."""

    environment: Mapping[str, oracle_cache.EnvironmentStamp]
    family_keys: Mapping[str, str]
    read_dir: Path | None = None
    write_dir: Path | None = None
    rotation: int = 0
    position_environment: oracle_cache.EnvironmentStamp | None = None
    position_keys: Mapping[str, str] | None = None


def open_row_cache(
    cache: "OracleRowCache | None",
    spec: ResolvedSpec,
    config: str,
    segment: int | None = None,
    *,
    first_row: int = 0,
    stop_row: int | None = None,
) -> tuple["oracle_cache.RowStore | None", "oracle_cache.RowWriter | None"]:
    """Return this configuration's loaded store, holding the records of rows `[first_row, stop_row)`, and the writer for its successor, or Nones without a cache. Both oracle paths call this, so they open the pair the same way. The subset digest is read from the stamp's `subset` line instead of hashing the table again. The writer builds its records in memory as one gzip member for the caller to hand the stores' writer (`oracle_cache.RowWriter` with no path): a range of a cut configuration, which passes its `segment` and row bounds, its records alone, with no header or trailer, and an uncut configuration its whole file, header and trailer included. A range loads only its own rows. Every range reads the same store, through the set head where there is one (`oracle_cache.load_store`), so all ranges agree on the pass ordinal the set head records. A cache with no `write_dir` keeps nothing it derives, so it loads its store read-only, which skips the scheduled re-derivation (`oracle_cache.RowStore`)."""
    if cache is None:
        return None, None
    stamp = cache.environment[config]
    subset_digest = stamp.labels["subset"]
    store = None
    if cache.read_dir is not None:
        store = oracle_cache.load_store(
            oracle_cache.store_path(cache.read_dir, config),
            stamp,
            subset_digest,
            spec,
            cache.family_keys,
            cache.rotation,
            cache.position_environment,
            cache.position_keys,
            first_row=first_row,
            stop_row=stop_row,
            read_only=cache.write_dir is None,
        )
    writer = None
    if cache.write_dir is not None:
        writer = oracle_cache.RowWriter(
            None,
            stamp,
            subset_digest,
            oracle_cache.next_pass_ordinal(store),
            cache.family_keys,
            cache.position_environment,
            cache.position_keys,
            whole=segment is None,
        )
    return store, writer


@functools.cache
def _registry_families(registry_path: Path = spec_load.DEFAULT_REGISTRY_PATH) -> frozenset[str]:
    """Return the family names `rebuild/script.yaml` registers, letters and ligatures alike."""
    raw = yaml.safe_load(Path(registry_path).read_text()) or {}
    return frozenset(raw.get("families") or ())


def unformed_old_ligature(name: str) -> bool:
    """Return whether an old glyph name belongs to a family the registry does not list. Every code point in a subset row is a registered letter, so such a glyph is an old-font ligature the rebuild does not have and draws as its separate letters (the old ·Vie+Utter is one). No alias can denote it, because an alias names one cell and the rebuild settles one cell per letter, so the alias map leaves it out: every row it appears in either has fewer glyphs than settled cells and diverges as `ligation`, or lines up with cells by accident and diverges as `unaliased`."""
    return name not in BOUNDARY_GLYPH_NAMES and name.split(".", 1)[0] not in _registry_families()


def unaliased_subset_names(subset_dir: Path, alias_path: Path) -> dict[str, list[str]]:
    """Map every old glyph name in the subset baseline rows that neither the alias map nor `BOUNDARY_GLYPH_NAMES` resolves to the sorted configurations it appears in. A missing alias does not always fail loudly: a ligation row never reaches the per-glyph alias check in `_compare_row`, so it is counted under a ledger class as if the name were understood. run_m1 therefore stops before building while this is non-empty. A `pending` alias passes this check but still counts as unaliased in the comparison. An `unformed_old_ligature` name needs no alias, and `classify_divergence` keeps every ligation row that holds one out of the ligation classes, so its rows stay unmatched. The names come from the sidecar the refilter writes (`baseline_subset.read_subset_names`), which changes only when the tables are refiltered, so this check is cheap enough to run on the `--gates-only` path too."""
    known = set(load_aliases(alias_path)) | BOUNDARY_GLYPH_NAMES
    missing: dict[str, set[str]] = {}
    for config, names in baseline_subset.read_subset_names(subset_dir).items():
        for name in names:
            if name not in known and not unformed_old_ligature(name):
                missing.setdefault(name, set()).add(config)
    return {name: sorted(configs) for name, configs in sorted(missing.items())}


_RENAMED_ROE_SHORTENED_TOP = (
    "qsRoe.en-ext-1-at-0",
    "qsRoe/sole/baseline/None/en-bind-shortened-top+en-ext-1",
)


def _only_renamed_roe_shortened_top(row: DivergentRow) -> bool:
    pairs = [
        (old, new) for old, new in zip(row.baseline_glyphs, row.new_cells) if "en-bind-shortened-top" in new
    ]
    return bool(pairs) and all(pair == _RENAMED_ROE_SHORTENED_TOP for pair in pairs)


# The tokens a `.noentry` name accounts for wherever it appears: the name itself and the lock token that its alias records or that a locked cell carries.
_NOENTRY_NAME_TOKENS = frozenset({"old-noentry", "+locked", "-locked"})
# The markers the zwnj-follower-exit-restored ruling names: ZWNJ and the namer dot.
_NOENTRY_MARKERS = frozenset({"200C", "00B7"})


def _noentry_glyphs(row: DivergentRow) -> list[int]:
    return [index for index, name in enumerate(row.baseline_glyphs) if ".noentry" in name]


def _cell_exit(token: str) -> str | None:
    fields = token.split("/")
    return fields[3] if len(fields) == 5 and fields[3] != "None" else None


def _noentry_name_tokens(row: DivergentRow, tags: set[str]) -> set[str]:
    """Return the row's tokens that its `.noentry` names account for by themselves: the name and lock tokens, and `exit-added` when every glyph that settles with an onward exit its old name does not spell is a `.noentry` glyph. The tag is row-level, so it is attributed per glyph here, the way `conform._cell_deltas` computes it, with the name's spelled exit standing for the alias's: another glyph's added exit in the same row keeps the token. Every other token is a difference of its own, which the rules for that token classify."""
    found = tags & _NOENTRY_NAME_TOKENS
    if "old-noentry" in found and "exit-added" in tags:
        gained = {
            index
            for index, (old, new) in enumerate(zip(row.baseline_glyphs, row.new_cells))
            if ".ex-" not in old and _cell_exit(new) is not None
        }
        if gained and gained <= set(_noentry_glyphs(row)):
            found.add("exit-added")
    return found


def _glyph_starts(row: DivergentRow) -> list[int] | None:
    """Return the codepoint index each baseline glyph starts at, or None when the junctions do not account for every glyph. The baseline junctions run between codepoints, so a `lig` junction marks two codepoints drawn as one glyph."""
    starts = [0] + [index + 1 for index, junction in enumerate(row.baseline_junctions) if junction != "lig"]
    return starts if len(starts) == len(row.baseline_glyphs) else None


def _follows_marker(row: DivergentRow, starts: list[int], index: int) -> bool:
    return starts[index] > 0 and row.codepoints.split(":")[starts[index] - 1] in _NOENTRY_MARKERS


def _noentry_glyphs_follow_markers(row: DivergentRow) -> bool:
    """Return whether every `.noentry` glyph in the row follows a ZWNJ or the namer dot, the post-marker variants the zwnj-word-initial rulings cover. The old font also draws some `.noentry` variants after a letter (its `noentry_after` derives), and those rows are not post-marker rows."""
    starts = _glyph_starts(row)
    indices = _noentry_glyphs(row)
    return (
        starts is not None and bool(indices) and all(_follows_marker(row, starts, index) for index in indices)
    )


def _post_marker_exits_restored(row: DivergentRow) -> bool:
    """Return whether every junction the row gains is the right-side join of a `.noentry` glyph that the old font drew right after a ZWNJ or the namer dot, the join the zwnj-follower-exit-restored ruling restores. `boundary-window` takes every ZWNJ row before this check runs, and the old font draws no `.noentry` glyph right after the namer dot, so the check matches no current row; it keeps the ruling's namer-dot case."""
    starts = _glyph_starts(row)
    if starts is None:
        return False
    old_junctions = [junction for junction in row.baseline_junctions if junction != "lig"]
    gained = [
        index
        for index, (old, new) in enumerate(zip(old_junctions, row.new_junctions))
        if old == "break" and new != "break"
    ]
    return bool(gained) and all(
        ".noentry" in row.baseline_glyphs[index] and _follows_marker(row, starts, index) for index in gained
    )


# The old unentered half letters whose exit tuck before ·Zoo pairs with ·Zoo's entry trim, each mapped to its new cell.
_ZOO_TUCK_LEADS = {
    "qsTea.half.ex-y5.ex-con-1": "qsTea/half/None/x-height/",
    "qsHe.half.ex-y5.ex-con-1": "qsHe/half/None/x-height/",
}


def classify_divergence(row: DivergentRow) -> str | None:
    """Return the one ledger class for a divergent row, chosen from its divergence tags (which `_compare_row` computes through the alias map), or None when no class applies. The order of the checks below is the precedence, and the ledger's header comment summarizes it. A row with no class can still match a function predicate or an unconditional ledger entry; otherwise it is unmatched and waits for a verdict on the review corpus."""
    tags = set(row.divergence_tags)
    if not tags or any(item.startswith("unaliased") for item in tags):
        return None
    if "+en-bind-shortened-top" in tags and _only_renamed_roe_shortened_top(row):
        # The old `qsRoe.en-ext-1-at-0` glyph is the shortened-top drawing, but its alias keeps only the en-ext-1 name, so the rebuild's bound cell draws the same ink under a longer name. The token is name grain only, so the rest of the row classifies as if it were absent.
        tags.discard("+en-bind-shortened-top")
    if any(item.startswith("position") for item in tags):
        # A cell-grain class claims the ink is identical, and the position comparison is the test of that claim, so a row with a position mismatch must not take one. Such rows are left to the function predicates (`kern_out_of_scope`, `may_ligature_junction_loosened`).
        return None
    if {"0020", "200C"} & set(row.codepoints.split(":")):
        # Design section 3.4: the new font renders each segment of a window split by a space or ZWNJ the same as that segment alone, and the conformance sweep's split-buffer check verifies this on every build. So a boundary row can diverge from the baseline only where the old font was inconsistent across the boundary, and every divergence inside a segment also appears on that segment's own row. Boundary rows need no review of their own and take this class ahead of every other.
        return "boundary-window"
    if row.config in OVERLAY_CONFIGS:
        # Under ss10 both fonts render every letter isolated, with no join and no ligature (the old font through its anchor-free `.ss10` copies, the rebuild through its ss10 input substitution), so any other ss10 divergence is a regression and waits for review. Without this, a namer-dot ss10 row that ligates would take post-marker-ligature-formation.
        return None
    if not tags:
        return "roe-shortened-top-renamed"
    if "ligation" in tags:
        if any(unformed_old_ligature(name) for name in row.baseline_glyphs):
            # The old font formed a ligature the rebuild does not have, which no ligation class describes, so the row waits for review.
            return None
        if "E67B:E652" in row.codepoints and "ss03" in row.config:
            return "ss03-out-tea-ligature-kept"
        if "E652:E679" in row.codepoints and ("200C" in row.codepoints or "ss03" in row.config):
            return "post-marker-ligature-formation"
        # The old font also forms qsDay_qsUtter in every configuration, so only the windows after a ZWNJ or the namer dot diverge: there the old font renames the lead to its .noentry form or leaves a bare name, and never forms the ligature. This is the same post-marker divergence as ·Tea·Oy above.
        if "E653:E67A" in row.codepoints and ("200C" in row.codepoints or "00B7" in row.codepoints):
            return "post-marker-ligature-formation"
        return None
    gains = {item for item in tags if item.startswith("junction-gain:")}
    if "junction-moved" in tags:
        # The old font drew a letter after a ZWNJ with a .noentry variant that joined its follower at one height. The new model settles that letter as word-initial, the same as after a space, and the join is at another height. This class applies only when the move is the row's only junction change and every `.noentry` glyph follows a marker. Any other row with a moved junction gets no class, including one that also gains or loses a junction and one whose `.noentry` variant follows a letter.
        if (
            "old-noentry" in tags
            and not gains
            and "junction-loss" not in tags
            and _noentry_glyphs_follow_markers(row)
        ):
            return "zwnj-word-initial-junction-moved"
        return None
    if "junction-loss" in tags:
        if gains:
            return "regrouped-chain"
        return None
    if gains:
        gain_runes = {item.split(":", 1)[1] for item in gains}
        unentered_it_gain = "junction-gain-unentered:qsIt" in tags
        if "old-noentry" in tags and _post_marker_exits_restored(row):
            return "zwnj-follower-exit-restored"
        if "E652:E679" in row.codepoints:
            return "pre-ligature-cleanup-regularized"
        if "ss03" in row.config and (gain_runes & {"qsTea", "qsMay"} or unentered_it_gain):
            return "ss03-chain-join-gains"
        if "qsIt" in gain_runes and not unentered_it_gain:
            return "entered-it-baseline-join-gain"
        if gain_runes <= {"qsPea"}:
            return "pea-chain-regularized"
        return None
    # A `.noentry` name that is the row's only difference makes it zwnj-word-initial-unification, below. Any other row is classified by its other tokens, as if its `.noentry` names were the plain ones.
    noentry_tokens = _noentry_name_tokens(row, tags)
    noentry_name_only = bool(noentry_tokens) and tags <= noentry_tokens
    tags -= noentry_tokens
    if "+en-ext-1" in tags:
        return "halves-entry-extension-restored"
    if tags & {"-en-ext-1:same-junction", "-en-ext-2:same-junction"}:
        return "same-junction-extension-non-summing"
    if "-en-ext-1:qsMay" in tags:
        return "may-baseline-entry-extension-dropped"
    if "-en-ext-1:qsNo" in tags:
        return "no-xheight-entry-extension-dropped"
    if tags & {"-en-ext-1:qsDay", "-en-ext-1:qsDay_qsEat", "-en-ext-1:qsDay_qsUtter"}:
        return "day-baseline-entry-extension-dropped"
    if "-en-ext-1:qsVie" in tags:
        return "vie-baseline-entry-extension-dropped"
    if "-ex-con-1" in tags and tags <= {"-ex-con-1", "+en-trim-1"} and "E65A:E67B" in row.codepoints:
        # The baseline ·See·Out fusion names the old pull-back differently. The old font's ex-con-1 tucks ·Out into ·See's whole tail (only the anchor moves). The runes keep the tail's anchor at its convention position and pull back the foot of ·Out's leg instead. The combined ink is identical and only the glyph names differ. The subset test keeps out any row where ink also moved elsewhere.
        return "see-out-fusion-renamed"
    if (
        "+ex-ext-2" in tags
        and tags <= {"+ex-ext-2", "-ex-ext-1", "-en-ext-2", "exit-dropped"}
        and "E665:E65D" in row.codepoints
    ):
        # At the ·May·J'ai junction, qsMay's single by-2 exit record replaces the old font's split extension (·May's exit by 1, ·J'ai's entry by 2) in every follower context. The -en-ext-2 token is the ·J'ai side of the same change: ·J'ai's alias keeps the old glyph's en-ext-2, which the new cell lacks. The subset test keeps out any row where unrelated ink also moved.
        return "may-jai-extension-consolidated"
    if tags and tags <= {"+en-con-1", "+en-con-2"} and ("E65D" in row.codepoints or "E65F" in row.codepoints):
        # The old font's exit contractions before ·J'ai are tucks: the left letter keeps its ink and only its anchor moves in, overlapping the follower. M1 draws the same result as ·J'ai's own entry contraction: the crown drops the overlapped columns and abuts instead. The combined drawing, every origin, and every advance are unchanged, and only ·J'ai's cell name gains the con token. The unentered half-·Tea exit tuck before ·Jay is the same case on the same crown shape, drawn as ·Jay's entry contraction. The subset test keeps out any row where ink also moved elsewhere.
        return "jai-entry-contraction-renamed"
    if (
        tags == {"+en-con-1", "-en-trim-1"}
        and ("E652:E65B" in row.codepoints or "E662:E65B" in row.codepoints)
        and any(
            _ZOO_TUCK_LEADS.get(old_left) == new_left
            and old_right == "qsZoo.en-trim-1"
            and new_right
            in {
                "qsZoo/full/x-height/None/en-con-1",
                "qsZoo/full/x-height/baseline/en-con-1",
            }
            for old_left, old_right, new_left, new_right in zip(
                row.baseline_glyphs, row.baseline_glyphs[1:], row.new_cells, row.new_cells[1:]
            )
        )
        and not any(
            left == "qsIt.ex-y5" and right == "qsRoe.en-ext-1-at-5"
            for left, right in zip(row.baseline_glyphs, row.baseline_glyphs[1:])
        )
    ):
        # ·Zoo's entry contraction places the same crown as the old half-·Tea or half-·He exit tuck plus ·Zoo entry trim. The unrelated ·It·Roe redraw changes ink without moving origins or advances, so the position comparison cannot catch it, and this class excludes that old pair explicitly.
        return "zoo-entry-contraction-renamed"
    # A row with these tokens has an ink change that no class covers, so it must get no class instead of reaching the name-grain classes below.
    if any(item.startswith("+ex-bind-") for item in tags) or "-ex-ext-1" in tags:
        return None
    if noentry_name_only:
        return "zwnj-word-initial-unification"
    if "entry-dropped" in tags or "exit-dropped" in tags:
        return "dangling-anchor-dropped"
    if tags & {"entry-added", "exit-added", "entry-moved", "exit-moved", "stance"}:
        return "bare-name-live-join"
    return None


PREDICATES: dict[str, Callable[[DivergentRow], bool]] = {}


def predicate(name: str):
    def register(function):
        PREDICATES[name] = function
        return function

    return register


def _class_predicate(class_id: str) -> Callable[[DivergentRow], bool]:
    def matches(row: DivergentRow) -> bool:
        return classify_divergence(row) == class_id

    return matches


# Predicate name to class id, for the ledger predicates that only test "classify_divergence chose this class". `compile_ledger` groups these entries by class id, and `_match_compiled` classifies each row once and looks the class up, instead of calling one closure per entry that each re-classifies the row. The two predicates below test something classification does not, so they stay functions.
CLASS_PREDICATE_IDS: dict[str, str] = {}

for _class_id in (
    "boundary-window",
    "ss03-out-tea-ligature-kept",
    "post-marker-ligature-formation",
    "regrouped-chain",
    "zwnj-word-initial-junction-moved",
    "zwnj-follower-exit-restored",
    "pre-ligature-cleanup-regularized",
    "ss03-chain-join-gains",
    "entered-it-baseline-join-gain",
    "pea-chain-regularized",
    "halves-entry-extension-restored",
    "same-junction-extension-non-summing",
    "may-baseline-entry-extension-dropped",
    "no-xheight-entry-extension-dropped",
    "day-baseline-entry-extension-dropped",
    "vie-baseline-entry-extension-dropped",
    "zwnj-word-initial-unification",
    "dangling-anchor-dropped",
    "bare-name-live-join",
    "see-out-fusion-renamed",
    "may-jai-extension-consolidated",
    "jai-entry-contraction-renamed",
    "zoo-entry-contraction-renamed",
    "roe-shortened-top-renamed",
):
    CLASS_PREDICATE_IDS[_class_id.replace("-", "_")] = _class_id
    PREDICATES[_class_id.replace("-", "_")] = _class_predicate(_class_id)


@predicate("kern_out_of_scope")
def _kern_out_of_scope(row: DivergentRow) -> bool:
    """Match position-only rows whose mismatch the position comparison marked kern-attributable (`oracle_positions._position_mismatch` sets this when every mismatch comes after a slot whose old advance carries a nonzero sidecar kern or that sits next to a ZWNJ). Other position mismatches are not matched here, so they stay unmatched for review unless another predicate matches them."""
    return row.kinds == ("position",) and "position-kern-attributable" in row.divergence_tags


# The cell-grain tokens of the ink-identical name-grain classes. Any other cell-grain token on a `may_ligature_junction_loosened` candidate means ink moved elsewhere in the row, so that predicate does not match it.
_NAME_GRAIN_TOKENS = frozenset(
    {"stance", "entry-added", "entry-moved", "entry-dropped", "exit-added", "exit-moved", "exit-dropped"}
)


@predicate("may_ligature_junction_loosened")
def _may_ligature_junction_loosened(row: DivergentRow) -> bool:
    """Match the reviewed `·Day+Utter ~x~ ·May` junction. The old font tucks ·May's x-height entry one pixel into the ligature's exit; the new model places it at the anchor-aligned column and draws no connector, which is the intended design (the may-ligature-junction-loosened ledger entry records the decision). Matches non-kern position mismatches on rows whose old glyph names contain that pair and whose other cell-grain tokens, if any, are all in `_NAME_GRAIN_TOKENS`."""
    if "position-mismatch" not in row.divergence_tags or "position-kern-attributable" in row.divergence_tags:
        return False
    cell_grain = {item for item in row.divergence_tags if not item.startswith("position")}
    if not cell_grain <= _NAME_GRAIN_TOKENS:
        return False
    glyphs = row.baseline_glyphs
    return any(
        glyphs[index].startswith("qsDay_qsUtter") and glyphs[index + 1].startswith("qsMay.en-y5")
        for index in range(len(glyphs) - 1)
    )


@dataclass(frozen=True)
class CompiledLedger:
    """The divergence ledger grouped by what each entry's `match` tests, so matching a row is a class lookup instead of a pass over every entry. Every entry keeps its ledger index, so `_match_compiled` can return matches from several groups in ledger order. `by_class` is keyed on the class id a `CLASS_PREDICATE_IDS` entry names. `functions` holds the entries whose predicate is a function of the row. `unconditional` holds the entries with no predicate, which match every row their `window` and `junction_change` admit; an empty `match` matches every row (rebuild/test_conform.py's ink-identical fixture ledger is one). An entry's `configs` is None when it applies to every configuration, and otherwise a container `row.config` is tested against. Each worker builds its own (`oracle_config_worker`)."""

    by_class: Mapping[str, tuple[tuple[int, str, Container[str] | None], ...]]
    functions: tuple[tuple[int, str, Container[str] | None, Callable[[DivergentRow], bool]], ...]
    unconditional: tuple[tuple[int, str, Container[str] | None, str | None, bool], ...]


def compile_ledger(entries: Sequence[Mapping]) -> CompiledLedger:
    """Compile the raw ledger (`yaml.safe_load` of rebuild/m1-divergences.yaml) into a `CompiledLedger`. An entry without an `id` is named `<unnamed>`. `configs: all` compiles to None and a list to a frozenset. A bare string is kept as a string, so it gets the same substring `in` test as the reference walk in rebuild/test_conform.py. Any other value, such as a `configs:` with no value, raises TypeError instead of silently matching every configuration. So does an entry or a `match` that is not a mapping, such as a `match:` with no value, and a `predicate` that is not a name, each naming the entry. An entry whose predicate is in neither `CLASS_PREDICATE_IDS` nor `PREDICATES` raises ValueError naming the entry, because it would match no row and leave the rows it was meant for unmatched without failing any gate. `load_ledger` turns all of these into a SystemExit that names the file."""
    by_class: dict[str, list[tuple[int, str, Container[str] | None]]] = {}
    functions: list[tuple[int, str, Container[str] | None, Callable[[DivergentRow], bool]]] = []
    unconditional: list[tuple[int, str, Container[str] | None, str | None, bool]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise TypeError(f"ledger entry {index + 1} must be a mapping, not {entry!r}")
        entry_id = entry.get("id", "<unnamed>")
        match = entry.get("match", {})
        if not isinstance(match, Mapping):
            raise TypeError(f"ledger entry {entry_id!r}: `match` must be a mapping, not {match!r}")
        raw_configs = match.get("configs", "all")
        configs: Container[str] | None
        if raw_configs == "all":
            configs = None
        elif isinstance(raw_configs, (list, tuple, set, frozenset)):
            configs = frozenset(raw_configs)
        elif isinstance(raw_configs, str):
            configs = raw_configs
        else:
            raise TypeError(
                f"ledger entry {entry_id!r}: `configs` must be `all` or a list of configurations, not {raw_configs!r}"
            )
        predicate_name = match.get("predicate")
        if predicate_name is None:
            unconditional.append(
                (index, entry_id, configs, match.get("window"), match.get("junction_change") is not None)
            )
            continue
        if not isinstance(predicate_name, str):
            raise TypeError(
                f"ledger entry {entry_id!r}: `predicate` must be the name of a predicate, not {predicate_name!r}"
            )
        class_id = CLASS_PREDICATE_IDS.get(predicate_name)
        if class_id is not None:
            by_class.setdefault(class_id, []).append((index, entry_id, configs))
            continue
        function = PREDICATES.get(predicate_name)
        if function is None:
            raise ValueError(
                f"ledger entry {entry_id!r}: `predicate` {predicate_name!r} is not one the oracle defines, so it would match no row"
            )
        functions.append((index, entry_id, configs, function))
    return CompiledLedger(
        by_class={class_id: tuple(filed) for class_id, filed in by_class.items()},
        functions=tuple(functions),
        unconditional=tuple(unconditional),
    )


def load_ledger(path: Path) -> tuple[list, CompiledLedger]:
    """Read the divergence ledger at `path` and return its entries as parsed with the ledger `compile_ledger` makes of them. An empty file is an empty ledger. A file that cannot be read, decoded or parsed, a document that is not a list of entries, and an entry `compile_ledger` refuses each raise SystemExit naming the file and the problem, so run_m1 records a red check line that names them instead of ending in a traceback. run_m1's pre-gate guards load the ledger before any gate runs (`run_m1_gates._run_pregate_guards`) and record a stop there as a refusal (`REFUSED_STATUS`). The oracle loads it again from disk on the serial path and in every worker, so a ledger edited while a run is going stops the run there, and that stop is recorded as FAILED."""
    try:
        entries = yaml.safe_load(Path(path).read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise SystemExit(f"{path}: {error}") from error
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise SystemExit(
            f"{path}: the ledger must be a list of entries, not a value of type {type(entries).__name__}"
        )
    try:
        return entries, compile_ledger(entries)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"{path}: {error}") from error


def load_aliases(path: Path) -> dict[str, CellId | str]:
    """Read the alias map at `path` (`labels.load_alias_map`). A file that cannot be read, decoded or parsed, and a document or entry that makes that function raise AttributeError, KeyError or TypeError (an entry that is neither a string nor a mapping with `rune` and `stance`), raise SystemExit naming the file and the problem, as `load_ledger` does for the ledger. run_m1's pre-gate guards load it before any gate runs (`unaliased_subset_names`) and the oracle loads it again from disk on the serial path and in every worker, so such a stop is recorded as a red check line, a refusal in the guards and FAILED in the oracle, not a traceback. A map that function reads can still stop the oracle with an exception elsewhere, such as keys of mixed types in `oracle_cache.alias_family_digests`, which run_m1 records red before the exception propagates with its traceback (`run_m1_gates._recording_failure`)."""
    try:
        return load_alias_map(path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise SystemExit(f"{path}: {error}") from error
    except (AttributeError, KeyError, TypeError) as error:
        raise SystemExit(
            f"{path}: the alias map must map each old glyph name to a string or to a mapping with `rune` and `stance` ({type(error).__name__}: {error})"
        ) from error


def _match_compiled(compiled: CompiledLedger, row: DivergentRow) -> list[str]:
    """Return the ids of every ledger entry this row matches, in ledger order, so the caller can tell a single match from a multi-match. The row is classified once and its class looked up in `by_class`. Function predicates run only when their `configs` admit the row's configuration, and unconditional entries apply their `window` and `junction_change` tests."""
    classified = classify_divergence(row)
    config = row.config
    hits: list[tuple[int, str]] = []
    if classified is not None:
        for index, entry_id, configs in compiled.by_class.get(classified, ()):
            if configs is None or config in configs:
                hits.append((index, entry_id))
    for index, entry_id, configs, function in compiled.functions:
        if (configs is None or config in configs) and function(row):
            hits.append((index, entry_id))
    for index, entry_id, configs, window, needs_junction in compiled.unconditional:
        if configs is not None and config not in configs:
            continue
        if window is not None and window not in row.codepoints:
            continue
        if needs_junction and "junction" not in row.kinds:
            continue
        hits.append((index, entry_id))
    if len(hits) > 1:
        hits.sort()
    return [entry_id for _index, entry_id in hits]


def _match_ledger(ledger: Sequence[Mapping], row: DivergentRow) -> list[str]:
    """Return the ids of every ledger entry this row matches, compiling the raw ledger first. This is for tests and probes that hold a raw list; `_compare_config` uses a `CompiledLedger` built once."""
    return _match_compiled(compile_ledger(ledger), row)


ORACLE_AUDIT_HEADER = "config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew"
AUDIT_NAME = "divergence-audit.tsv"
# The head's stamp that records when the audit's content last moved: the single file's modification time on adoption, and the commit's time whenever a segment, or a base whose rows differ from what the head served, is written.
CONTENT_TIME_STAMP = row_delta.CONTENT_TIME_STAMP
SEGMENT_ROUTE = "segment"
BASE_ROUTE = "base"
_AUDIT_HEADER_LINE = (ORACLE_AUDIT_HEADER + "\n").encode("utf-8")
_AUDIT_RANKS = {config.encode(): rank for rank, config in enumerate(ORACLE_CONFIGS)}


def audit_key(row: bytes) -> tuple[int, bytes, int, bytes]:
    """An audit row's key in the audit's own order, from the row or from a tombstone's `config\\tcodepoints` payload: the configuration's rank in `ORACLE_CONFIGS`, the order `review.audit._config_index` ranks them in, then the window field's length and its text, which in `format_codepoints`' form is the canonical `(length, codepoints)` order of the subset tables."""
    fields = row.split(b"\t", 2)
    if len(fields) < 2:
        raise ValueError(f"the audit row {row[:120]!r} has no window field")
    config, window = fields[0], fields[1].rstrip(b"\n")
    return (_AUDIT_RANKS.get(config, len(_AUDIT_RANKS)), config, len(window), window)


_KEY_FIELDS = operator.methodcaller("split", b"\t", 2)


def _audit_config_order(config: str) -> tuple[int, bytes]:
    """A configuration's place in the audit's order, the first two fields of `audit_key`."""
    name = config.encode()
    return (_AUDIT_RANKS.get(name, len(_AUDIT_RANKS)), name)


def _audit_key_fields(row: bytes) -> list[bytes]:
    """An audit row's configuration and window fields, the fields its key is made of (`audit_key`)."""
    return _KEY_FIELDS(row)[:2]


def _lines(block: bytes) -> list[bytes]:
    """A block of whole lines split at its newlines alone, each line keeping its newline."""
    return [line + b"\n" for line in block.split(b"\n")[:-1]]


def _tombstone(row: bytes) -> bytes:
    config, window, _rest = (row.rstrip(b"\n") + b"\t").split(b"\t", 2)
    return row_delta.TOMBSTONE + b"\t" + config + b"\t" + window + b"\n"


def oracle_audit_scratch(out_dir: Path) -> Path:
    """Return this run's scratch directory beside the audit, which holds only the in-order writer's record of its shared memory segments' names (`SEGMENT_RECORD`). The pid in the name keeps two runs' records apart. The name matches neither `artifact_cycle.M1_ARTIFACT_NAMES` nor any glob over the tables, so nothing a killed run leaves here is taken for an artifact."""
    return Path(out_dir) / f"divergence-audit.parts.{os.getpid()}"


def _audit_parts_read(opened: row_delta.OpenParts) -> bool:
    """Whether every part of an open audit head passes its header check: the base's first line is the audit's header, and each segment's header names the plain keyed merge, the head's output and generation and its own number. A part held as gzip members has its header member inflated whole (`row_delta.part_header_line`), and one that does not inflate to what its index records fails the check. Each handle is left at its start."""
    head = opened.head
    try:
        for number, (handle, part) in enumerate(zip(opened.handles, head.parts)):
            line = row_delta.part_header_line(handle, part)
            if number == 0:
                if line != _AUDIT_HEADER_LINE:
                    return False
            else:
                row_delta.check_segment_header(
                    line, head.output, head.generation, number, row_delta.MERGE_PLAIN_KEY
                )
    except OSError, row_delta.HeadError:
        return False
    return True


@dataclass
class AuditRoute:
    """How the audit is written this pass, decided before any range starts (`AuditPlan`), under #618's decisions 72 to 75 as change 31 applies them, with no size test and no stamp clause:

    - `segment`: one plain keyed segment of the rows that differ from what `head` serves, upserts and tombstones, written as gzip members, a configuration a member (`row_delta.MemberPart`), or nothing and the head alone when no row differs.
    - `base`: the whole file, its header line and every row, as the next generation's base, with its `reason`: `first`, no head and no single file; `unusable`, a head that does not parse, a part missing or not the size and sha256 its head records or whose header does not read, or a single file whose header does not read, which the cycle reports as `rewritten whole` (`output_sink.note_rewritten`); or `fold`, `head`'s fold is due at `row_delta.AUDIT_SEGMENT_CAP` and the audit holds the pass's fold (`row_delta.claim_fold`), or holds `row_delta.FOLD_OVERRUN` segments past its cap (`forced`). A `first` or `unusable` base is the plain file, and a `fold` base is that file as gzip members, as a segment is (#618's decision 48).

    `adopted` says `head` is the single file a tree from before the segment format holds, linked as generation 0's base under a head no writer has committed. `opened` holds every part `head` lists, open, for the compare to read in lockstep, on the segment and fold routes.
    """

    out_dir: Path
    route: str
    reason: str = ""
    head: row_delta.Head | None = None
    adopted: bool = False
    forced: bool = False
    opened: row_delta.OpenParts | None = None
    seconds: float = 0.0
    hash_seconds: float = 0.0
    wait_seconds: float = 0.0

    def tail(self) -> str:
        """The tail of `[t] oracle_audit_plan`: the route, a base's reason, whether it adopted a single file, whether a fold came at its overrun, the seconds the parts' hashes or the adoption's read-through took, and the seconds the first write waited for them."""
        tokens = [f"route={self.route}"]
        if self.route == BASE_ROUTE:
            tokens.append(f"reason={self.reason}")
        tokens.append(f"adopted={'true' if self.adopted else 'false'}")
        if self.forced:
            tokens.append("overrun=true")
        tokens.append(f"segments={0 if self.head is None else len(self.head.segments)}")
        tokens.append(f"hash_seconds={self.hash_seconds:.2f}")
        tokens.append(f"wait_seconds={self.wait_seconds:.2f}")
        return " ".join(tokens)

    def unusable(self) -> None:
        if self.opened is not None:
            self.opened.close()
            self.opened = None
        self.route, self.reason, self.forced = BASE_ROUTE, "unusable", False

    def close(self) -> None:
        if self.opened is not None:
            self.opened.close()
            self.opened = None

    def discard(self) -> None:
        """Close the parts and delete the link an adoption made while no head lists it, for a writer that commits nothing."""
        self.close()
        if not self.adopted:
            return
        with suppress(row_delta.HeadError, OSError):
            if row_delta.read_head(self.out_dir, AUDIT_NAME) is None:
                (self.out_dir / row_delta.base_name(AUDIT_NAME, 0)).unlink(missing_ok=True)


def _adopted_audit(out_dir: Path, single: Path) -> row_delta.Head:
    """Link the single file at `single` as generation 0's base, which writes no data, and return a head over it no writer has committed, recording what the file's own bytes give: its size and sha256, its rows and their bytes below the header, read through once, and its modification time as the time its content last moved."""
    name = row_delta.base_name(AUDIT_NAME, 0)
    link = out_dir / name
    os.link(single, link)
    digest = hashlib.sha256()
    lines = size = 0
    with open(link, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            lines += chunk.count(b"\n")
            size += len(chunk)
        mtime = os.fstat(handle.fileno()).st_mtime_ns
    base = row_delta.Part(name, size, digest.hexdigest(), lines - 1, size - len(_AUDIT_HEADER_LINE))
    return row_delta.Head(AUDIT_NAME, 0, 0, {CONTENT_TIME_STAMP: mtime}, base, (), row_delta.MERGE_PLAIN_KEY)


def _single_audit_reads(single: Path) -> bool:
    try:
        with open(single, "rb") as handle:
            return handle.readline() == _AUDIT_HEADER_LINE
    except OSError:
        return False


class AuditPlan:
    """The audit's route for one pass (`AuditRoute`), planned before any range starts: the head read, the staging files a killed writer left and the parts no head lists deleted (`row_delta.remove_staging`, `row_delta.remove_unlisted`), which one oracle at a time does over an out dir (`run_m1_gates.oracle_lock`), a single file adopted by link, and the pass's fold claimed (`row_delta.claim_fold`) where the fold is due, all at once; the parts' sha256 checks and an adoption's read-through then run on threads, a part a thread, each hashing through one buffer it reuses (`row_delta.part_hashes_to`, `row_delta.HASH_READ_BYTES` a part), which `route` joins before the writer's first write. A part that fails its check takes the `unusable` route. `route` prints `[t] oracle_audit_plan` once, when it first joins, with the route (`AuditRoute.tail`) and the plan's seconds: from its start to the end of its own work and its threads', whichever ends last, so the ranges' compute before the first write is not among them; `hash_seconds` is the threads' share, and `wait_seconds` how long the first write waited for them."""

    def __init__(self, out_dir: Path) -> None:
        self._started = time.perf_counter()
        self._threads_ended = self._started
        self._route: AuditRoute | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oracle-audit-plan")
        self._future: Future | None = None
        self._pending = self._plan(Path(out_dir))
        self._planned = time.perf_counter()

    def _plan(self, out: Path) -> AuditRoute:
        out.mkdir(parents=True, exist_ok=True)
        row_delta.remove_staging(out, AUDIT_NAME)
        try:
            head = row_delta.read_head(out, AUDIT_NAME)
        except row_delta.HeadError:
            row_delta.remove_unlisted(out, AUDIT_NAME, None)
            return AuditRoute(out, BASE_ROUTE, "unusable")
        row_delta.remove_unlisted(out, AUDIT_NAME, head)
        single = out / AUDIT_NAME
        if head is None:
            if not single.exists():
                return AuditRoute(out, BASE_ROUTE, "first")
            if not _single_audit_reads(single):
                return AuditRoute(out, BASE_ROUTE, "unusable")
            route = AuditRoute(out, SEGMENT_ROUTE, adopted=True)
            self._future = self._pool.submit(self._adopt, route, single)
            return route
        if head.merge != row_delta.MERGE_PLAIN_KEY:
            return AuditRoute(out, BASE_ROUTE, "unusable", head=head)
        route = AuditRoute(out, SEGMENT_ROUTE, head=head)
        if row_delta.fold_due(head, row_delta.AUDIT_SEGMENT_CAP):
            holds = row_delta.claim_fold(AUDIT_NAME)
            if holds or row_delta.fold_forced(head, row_delta.AUDIT_SEGMENT_CAP):
                route.route, route.reason, route.forced = BASE_ROUTE, "fold", not holds
        self._future = self._pool.submit(self._check, route)
        return route

    def _adopt(self, route: AuditRoute, single: Path) -> None:
        started = time.perf_counter()
        try:
            route.head = _adopted_audit(route.out_dir, single)
            route.opened = row_delta.open_parts(route.out_dir, route.head)
        finally:
            self._threads_ended = time.perf_counter()
            route.hash_seconds = self._threads_ended - started

    def _check(self, route: AuditRoute) -> None:
        started = time.perf_counter()
        assert route.head is not None
        try:
            try:
                opened = row_delta.open_parts(route.out_dir, route.head)
            except row_delta.HeadError:
                route.unusable()
                return
            route.opened = opened
            with ThreadPoolExecutor(
                max_workers=len(opened.handles), thread_name_prefix="oracle-audit-hash"
            ) as hashing:
                hashed = list(hashing.map(row_delta.part_hashes_to, opened.handles, route.head.parts))
            if not all(hashed) or not _audit_parts_read(opened):
                route.unusable()
        finally:
            self._threads_ended = time.perf_counter()
            route.hash_seconds = self._threads_ended - started

    def settled(self) -> AuditRoute:
        """Join the plan's threads whatever they raised and return the route as far as it got, for a writer that commits nothing."""
        with suppress(Exception):
            if self._future is not None:
                self._future.result()
        self._pool.shutdown(wait=True)
        return self._pending

    def route(self) -> AuditRoute:
        """Join the plan's threads and return its route, printing `[t] oracle_audit_plan` the first time."""
        if self._route is None:
            waited = time.perf_counter()
            try:
                if self._future is not None:
                    self._future.result()
            finally:
                self._pool.shutdown(wait=True)
            route = self._route = self._pending
            route.wait_seconds = time.perf_counter() - waited
            route.seconds = max(self._planned, self._threads_ended) - self._started
            console.timing("oracle_audit_plan", route.seconds, route.tail())
        return self._route


class _OldRows:
    """The old materialized audit's rows as one byte stream read through a buffer, for the compare: whole blocks taken where they equal the new rows byte for byte, and lines peeked and taken one at a time where they do not. A refill gathers at least `ORACLE_AUDIT_BLOCK` bytes of the stream, however finely the merge splits it around its changes, and joins them behind the unconsumed bytes once, so the buffer is rebuilt about once a block and each old byte is copied a fixed number of times however dense the changes are."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""
        self._at = 0
        self.held_peak_bytes = 0
        self.consumed = 0

    def _more(self) -> bool:
        """Append at least `ORACLE_AUDIT_BLOCK` more bytes of the stream, or the rest of it, behind the unconsumed bytes, which then start the buffer; say whether there were any."""
        gathered: list[bytes] = []
        size = 0
        for chunk in self._chunks:
            gathered.append(chunk)
            size += len(chunk)
            if size >= ORACLE_AUDIT_BLOCK:
                break
        if not size:
            return False
        self._buffer = b"".join([self._buffer[self._at :], *gathered])
        self._at = 0
        self.held_peak_bytes = max(self.held_peak_bytes, len(self._buffer))
        return True

    def take_equal(self, block: bytes) -> bool:
        """Take `block`'s length of bytes when they equal `block`, and say whether they did."""
        while len(self._buffer) - self._at < len(block):
            if not self._more():
                return False
        if self._buffer[self._at : self._at + len(block)] != block:
            return False
        self._at += len(block)
        self.consumed += len(block)
        return True

    def peek(self) -> bytes | None:
        """The next whole line with its newline, or None at the end."""
        while True:
            end = self._buffer.find(b"\n", self._at)
            if end >= 0:
                return self._buffer[self._at : end + 1]
            if not self._more():
                if self._at < len(self._buffer):
                    raise ValueError("the old audit ends inside a row")
                return None

    def advance(self, line: bytes) -> None:
        self._at += len(line)
        self.consumed += len(line)

    def lines(self, count: int) -> list[bytes]:
        """The next `count` whole lines, each with its newline, or fewer at the end, without taking them. Each refill's newlines are counted once, as it arrives."""
        held = self._buffer.count(b"\n", self._at)
        while held < count:
            kept = len(self._buffer) - self._at
            if not self._more():
                break
            held += self._buffer.count(b"\n", kept)
        found: list[bytes] = []
        at = self._at
        while len(found) < count:
            end = self._buffer.find(b"\n", at)
            if end < 0:
                break
            found.append(self._buffer[at : end + 1])
            at = end + 1
        return found


class SectionCompare:
    """The compare the audit's writers share: the pass's rows, taken in the audit's order a block of whole lines at a time (`feed`), against the standing rows they replace, read in lockstep from `old`, the old materialized rows through buffers (`_OldRows`), or None on a route that reads no standing audit, with what the route writes handed to `write`: the upserts and tombstones on the segment route and every row on a base route. A block whose bytes equal the old stream's next bytes costs a memory compare; a block whose rows name the same keys as the old stream's next rows, as after a ledger edit that rewrites rows in place, has its changed rows upserted with a compare a line through C-level maps; and any other block drops to line level, where an equal line is passed over, a changed or new row is an upsert, and an old row the new stream lacks is a tombstone. `drain` removes every old row the new stream did not reach. The keys must strictly increase on both streams (`audit_key`): the line level takes every line's key, and a block the old stream holds byte for byte or key for key has the keys at its two ends taken on both streams, its first against the row before it and its last against its first, since within it the new stream's order is the old stream's, which the writer of those rows checked; a break raises `ValueError` naming the row.

    The result is the key-ordered merge-join of two sorted streams, whatever the block boundaries, so a compare over the whole audit (`AuditWriter`) and one compare a configuration over that configuration's section of both streams (`audit_section_task`) reach the same decisions: sections never interleave in the audit's order, so cutting both streams at a section's ends cuts no run the merge-join would pair across it.
    """

    def __init__(self, old: _OldRows | None, write: Callable[[bytes], object], route: str) -> None:
        self._old = old
        self._write = write
        self._writes_rows = route == BASE_ROUTE
        self._writes_changes = route == SEGMENT_ROUTE
        self._last_new: tuple | None = None
        self._last_old: tuple[int, tuple] | None = None
        self.rows = 0
        self.upserts = 0
        self.tombstones = 0
        self.seconds = 0.0

    @property
    def held_peak_bytes(self) -> int:
        """The most the old stream held at once."""
        return 0 if self._old is None else self._old.held_peak_bytes

    def _new_key(self, line: bytes) -> tuple:
        """The key of the pass's next row, refused unless it is above the last one taken."""
        at = audit_key(line)
        if self._last_new is not None and at <= self._last_new:
            raise ValueError(
                f"the oracle's audit rows are out of the audit's order at {line.rstrip().decode('utf-8', 'replace')[:160]!r}"
            )
        self._last_new = at
        return at

    def _old_key(self, line: bytes, position: int | None = None, at: tuple | None = None) -> tuple:
        """The key of the standing row at `position` in the old stream (the next one by default), refused unless it is above the last one taken at another position; `at` is its key when the caller has it."""
        assert self._old is not None
        position = self._old.consumed if position is None else position
        at = audit_key(line) if at is None else at
        if self._last_old is not None and self._last_old[0] != position and at <= self._last_old[1]:
            raise ValueError(
                f"the standing audit is out of the audit's order at {line.rstrip().decode('utf-8', 'replace')[:160]!r}"
            )
        self._last_old = (position, at)
        return at

    def _span_ends(
        self, lines: tuple[bytes, bytes], standing: tuple[bytes, bytes], positions: tuple[int, int]
    ) -> None:
        """Take the keys at the two ends of a run of whole lines the two streams share key for key, as `lines`' first and last on the new stream and `standing`'s, at `positions`, on the old: each first against the last key its stream took, and each last against its first. Within the run the new stream's order is the old stream's."""
        at = self._new_key(lines[0])
        self._old_key(standing[0], positions[0], at)
        if positions[1] != positions[0]:
            at = self._new_key(lines[1])
            self._old_key(standing[1], positions[1], at)

    def feed(self, block: bytes) -> None:
        """Take the next block of the pass's rows, whole lines in the audit's order."""
        if not block:
            return
        started = time.perf_counter()
        self.rows += block.count(b"\n")
        old = self._old
        if old is None:
            for line in _lines(block):
                self._new_key(line)
            self._write(block)
            self.seconds += time.perf_counter() - started
            return
        position = old.consumed
        if old.take_equal(block):
            last = block.rfind(b"\n", 0, len(block) - 1) + 1
            ends = (block[: block.index(b"\n") + 1], block[last:])
            self._span_ends(ends, ends, (position, position + last))
            if self._writes_rows:
                self._write(block)
            self.seconds += time.perf_counter() - started
            return
        lines = _lines(block)
        standing = old.lines(len(lines))
        if len(standing) == len(lines) and list(map(_audit_key_fields, lines)) == list(
            map(_audit_key_fields, standing)
        ):
            self._span_ends(
                (lines[0], lines[-1]),
                (standing[0], standing[-1]),
                (position, position + sum(map(len, standing)) - len(standing[-1])),
            )
            for at in itertools.compress(range(len(lines)), map(operator.ne, lines, standing)):
                self._upsert(lines[at])
            for line in standing:
                old.advance(line)
            if self._writes_rows:
                self._write(block)
            self.seconds += time.perf_counter() - started
            return
        for line in lines:
            at = self._new_key(line)
            if self._writes_rows:
                self._write(line)
            if old.peek() == line:
                self._old_key(line, at=at)
                old.advance(line)
                continue
            while True:
                standing = old.peek()
                if standing is None:
                    self._upsert(line)
                    break
                standing_at = self._old_key(standing)
                if standing_at < at:
                    self._remove(standing)
                    old.advance(standing)
                    continue
                if standing_at == at:
                    old.advance(standing)
                    if standing == line:
                        break
                self._upsert(line)
                break
        self.seconds += time.perf_counter() - started

    def _upsert(self, line: bytes) -> None:
        self.upserts += 1
        if self._writes_changes:
            self._write(row_delta.UPSERT + b"\t" + line)

    def _remove(self, line: bytes) -> None:
        self.tombstones += 1
        if self._writes_changes:
            self._write(_tombstone(line))

    def drain(self) -> None:
        """Remove every old row the new stream did not reach."""
        started = time.perf_counter()
        old = self._old
        if old is not None:
            while (standing := old.peek()) is not None:
                self._old_key(standing)
                self._remove(standing)
                old.advance(standing)
        self.seconds += time.perf_counter() - started


def _audit_head(route: AuditRoute, part: row_delta.Part | None, changed: bool) -> row_delta.Head:
    """The head a pass commits over `route`, having written `part` (None for a segment route that wrote none) and changed rows or not: on the segment route the standing head with the segment, its stamps' content time moved, or the standing head alone; on a base route the next generation's head over the base, its content generation and time moved unless a fold changed no row."""
    prior = route.head
    now = time.time_ns()
    if route.route == SEGMENT_ROUTE:
        assert prior is not None
        return (
            prior
            if part is None
            else row_delta.with_segment(prior, part, {**prior.stamps, CONTENT_TIME_STAMP: now})
        )
    assert part is not None
    moved = route.reason != "fold" or changed or prior is None
    generation = 0 if prior is None else prior.generation + 1
    content = 0 if prior is None else prior.content + (1 if moved else 0)
    stamps = {
        CONTENT_TIME_STAMP: (now if moved or prior is None else prior.stamps.get(CONTENT_TIME_STAMP, now))
    }
    return row_delta.Head(AUDIT_NAME, generation, content, stamps, part, (), row_delta.MERGE_PLAIN_KEY)


def _commit_audit(out_dir: Path, route: AuditRoute, head: row_delta.Head) -> None:
    """Close the route's parts, count an unusable route's base as `rewritten whole`, commit `head` (`row_delta.commit`), which then deletes every part it does not list, and delete the single file a tree from before the segment format held."""
    route.close()
    if route.reason == "unusable":
        output_sink.note_rewritten(AUDIT_NAME)
    row_delta.commit(out_dir, head)
    (out_dir / AUDIT_NAME).unlink(missing_ok=True)


def _audit_timing(
    seconds: float,
    counts: tuple[int, int, int],
    route: AuditRoute,
    part: row_delta.Part | None,
    compressing: float,
    head: row_delta.Head,
    held: int,
) -> None:
    """Print `[t] oracle_audit`: the compare's seconds, the rows streamed, the upserts and tombstones, the part's bytes on disk and inflated, its members (0 for a plain part) and the seconds spent compressing them, the segments after the commit and the most an old stream held at once."""
    rows, upserts, tombstones = counts
    size = inflated = members = 0
    if part is not None:
        size = inflated = part.size
        if part.members is not None:
            inflated, members = sum(member.inflated for member in part.members), len(part.members)
    console.timing(
        "oracle_audit",
        seconds,
        f"rows={rows} upserts={upserts} tombstones={tombstones} "
        f"wrote={'nothing' if part is None else route.route} part_bytes={size} inflated_bytes={inflated} "
        f"members={members} compress_seconds={compressing:.2f} "
        f"segments={len(head.segments)} held_mb={held / 1e6:.1f}",
    )


class AuditWriter:
    """The serial path's audit writer (`compare_against_baseline`), and the reference the pooled path's per-configuration tasks are held to (`audit_section_task`): it plans the route as it is made (`AuditPlan`), takes the pass's rows in the audit's order a block of whole lines at a time (`feed`) through one compare over the whole audit (`SectionCompare`), and on `commit` writes the head. Over a usable head (the segment and fold routes) the compare reads the old materialized audit in lockstep, the base and the segments merged by key through buffers (`row_delta.merge_plain_keyed`), never held whole; a key that breaks the order raises `ValueError` naming the row, and nothing is committed. On the segment route it writes the upserts and tombstones as one plain keyed segment held as gzip members, a configuration a member (`row_delta.MemberPart`), opened at the first difference, so a pass that changes nothing writes no segment and commits a head that `row_delta.commit` leaves in place; on a base route it writes the whole file, its header line and every row, as the next generation's base, `first`, `unusable` or `fold`, a `first` or `unusable` base plain and with every key checked, and a `fold` base as gzip members. It compresses on the thread that feeds it, as each block arrives, and a member's bytes do not depend on how the rows were cut into blocks. Parts are written under their final names (`row_delta.StreamedPart`, `row_delta.MemberPart`), which no head lists until `commit`, and `abandon` leaves nothing listed: a part already under its final name is deleted by the next plan (`row_delta.remove_unlisted`).

    The head's content generation and its `CONTENT_TIME_STAMP` move when a segment, or a base whose rows differ from what the head served, is written, and stay on a head-alone commit and on a fold that changes no row (`Head.identity`, `_audit_head`). A base written because a head or a part did not read is counted as `rewritten whole` (`output_sink.note_rewritten`). `commit` prints `[t] oracle_audit` (`_audit_timing`).
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self._plan = AuditPlan(self.out_dir)
        self._route: AuditRoute | None = None
        self._part: row_delta.StreamedPart | None = None
        self._compare: SectionCompare | None = None
        self.seconds = 0.0

    def _ready(self) -> SectionCompare:
        if self._compare is not None:
            return self._compare
        route = self._route = self._plan.route()
        prior = route.head
        old = None
        if route.opened is not None:
            assert prior is not None
            old = _OldRows(_materialized_rows(route.opened))
        if route.route == SEGMENT_ROUTE:
            assert prior is not None
            number = len(prior.segments) + 1
            self._part = row_delta.MemberPart(
                self.out_dir,
                row_delta.segment_name(AUDIT_NAME, prior.generation, number),
                row_delta.segment_header(AUDIT_NAME, prior.generation, number, row_delta.MERGE_PLAIN_KEY),
                field=1,
            )
        else:
            name = row_delta.base_name(AUDIT_NAME, 0 if prior is None else prior.generation + 1)
            self._part = (
                row_delta.MemberPart(self.out_dir, name, _AUDIT_HEADER_LINE, field=0)
                if route.reason == "fold"
                else row_delta.StreamedPart(self.out_dir, name, _AUDIT_HEADER_LINE, gzipped=False)
            )
        self._compare = SectionCompare(old, self._part.write, route.route)
        return self._compare

    def feed(self, block: bytes) -> None:
        """Take the next block of the pass's rows, whole lines in the audit's order."""
        if block:
            self._ready().feed(block)

    def commit(self) -> row_delta.Head:
        """Remove every old row the new stream did not reach, write the part, and commit the head (`_commit_audit`)."""
        compare = self._ready()
        route, part_writer = self._route, self._part
        assert route is not None and part_writer is not None
        compare.drain()
        started = time.perf_counter()
        if route.route == BASE_ROUTE and not part_writer.opened:
            part_writer.write(b"")
        part = part_writer.close()
        self.seconds += compare.seconds + time.perf_counter() - started
        head = _audit_head(route, part, bool(compare.upserts or compare.tombstones))
        _commit_audit(self.out_dir, route, head)
        compressing = part_writer.seconds if isinstance(part_writer, row_delta.MemberPart) else 0.0
        _audit_timing(
            self.seconds,
            (compare.rows, compare.upserts, compare.tombstones),
            route,
            part,
            compressing,
            head,
            compare.held_peak_bytes,
        )
        return head

    def abandon(self) -> None:
        """Commit nothing: remove a part not yet in place, close the parts the plan opened, and delete an adoption's link while no head lists it."""
        if self._part is not None:
            self._part.abandon()
        (self._route or self._plan.settled()).discard()

    def __enter__(self) -> AuditWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.abandon()


def _materialized_rows(opened: row_delta.OpenParts) -> Iterator[bytes]:
    """The rows of an open audit head's materialized form, as runs of whole lines: the base after its header merged by key with the segments (`row_delta.merge_plain_keyed`), or the base's own bytes when it has none. Each part is read through its inflated stream (`row_delta.part_stream`), so a plain base, plain segments and parts held as gzip members merge alike."""
    head = opened.head
    base = row_delta.part_stream(opened.handles[0], head.base)
    base.readline()
    segments = [
        row_delta.plain_keyed_segment_rows(
            row_delta.part_stream(handle, part), head.output, head.generation, number
        )
        for number, (handle, part) in enumerate(zip(opened.handles[1:], head.segments), start=1)
    ]
    yield from row_delta.merge_plain_keyed(base, segments, audit_key, audit_key)


def materialized_audit(out_dir: Path) -> Iterator[bytes]:
    """The audit's materialized form under `out_dir`, a run of bytes at a time: the head's base and segments, each inflated where it is held as gzip members, merged by key after the header (`_materialized_rows`), which is the whole file over the live rows byte for byte, its header line and every row in the audit's order, or, for a tree with no head, the single file's bytes. Every part is opened, and checked against the size its head records, before the first byte is yielded; a member that does not inflate to what its index records raises `row_delta.HeadError` as it is read."""
    out = Path(out_dir)
    head = row_delta.read_head(out, AUDIT_NAME)
    if head is None:
        with open(out / AUDIT_NAME, "rb") as single:
            while chunk := single.read(1 << 20):
                yield chunk
        return
    with row_delta.open_parts(out, head) as opened:
        yield _AUDIT_HEADER_LINE
        yield from _materialized_rows(opened)


class AuditSink(Protocol):
    """What `_compare_config` writes audit lines to: the serial path's blocks fed to the audit's writer (`_FedBlocks`), or a range's `AuditBlocks`."""

    def write(self, text: str, /) -> int: ...


class _FedBlocks:
    """The serial path's audit sink: lines gathered into blocks of `ORACLE_AUDIT_BLOCK` bytes and fed to the audit's writer whole, so the serial path feeds the compare as the pooled path's section tasks do."""

    def __init__(self, writer: AuditWriter) -> None:
        self._writer = writer
        self._pending = bytearray()

    def write(self, text: str, /) -> int:
        self._pending += text.encode("utf-8")
        if len(self._pending) >= ORACLE_AUDIT_BLOCK:
            self.flush()
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._writer.feed(bytes(self._pending))
            self._pending = bytearray()


class AuditBlocks:
    """One row range's audit lines, held in memory until the range ships them home (`ship`). They are compressed with zlib at level 1 a block at a time, each block a whole zlib stream that ends at a line's end once `ORACLE_AUDIT_BLOCK` bytes have gathered, so the range holds at most one block uncompressed and its configuration's section task inflates one block at a time (`audit_section_task`). The bytes are the lines' UTF-8, as the serial path feeds them to the audit's writer."""

    def __init__(self) -> None:
        self.blocks: list[bytes] = []
        self._pending = bytearray()

    def write(self, text: str, /) -> int:
        self._pending += text.encode("utf-8")
        if len(self._pending) >= ORACLE_AUDIT_BLOCK:
            self._seal()
        return len(text)

    def _seal(self) -> None:
        self.blocks.append(zlib.compress(self._pending, 1))
        self._pending = bytearray()

    def close(self) -> list[bytes]:
        """Compress what is left and return every block in line order."""
        if self._pending:
            self._seal()
        return self.blocks


def ship(pieces: list[bytes], name: str) -> Shipped | None:
    """Copy `pieces` end to end into a new POSIX shared memory segment called `name`, dropping each from the list once it is copied, close this process's mapping, and return what names them, or None when there are no bytes, for which no segment is made. Making the segment registers it with the resource tracker, which a spawned pool worker shares with the parent that started it, and which unlinks any segment still registered once the parent and every worker have exited. Whoever maps the segment unlinks it as it maps it, and unregisters it in doing so (`_Mapped`: a configuration's section task, `audit_section_task`, for the ranges' audit lines; the parent's `InOrderWriter`, for a range's store members as it lands and a section's bytes as it writes the part; or a settle memo absorb's worker, `run_m1_gates._absorb_memo`), and the parent unlinks any segment it named and nobody mapped as it exits (`InOrderWriter.__exit__`, `run_m1_gates._SweepSegments`)."""
    lengths = tuple(len(piece) for piece in pieces)
    if not sum(lengths):
        return None
    segment = shared_memory.SharedMemory(name, create=True, size=sum(lengths))
    buffer = segment.buf
    assert buffer is not None
    try:
        at = 0
        for index, length in enumerate(lengths):
            buffer[at : at + length] = pieces[index]
            pieces[index] = b""
            at += length
    finally:
        segment.close()
    return Shipped(name, lengths)


def _unlink_if_made(name: str, track: bool = True) -> None:
    """Unlink the shared memory segment `name` if a range made it. Attaching registers the name with the resource tracker and unlinking unregisters it, so the tracker holds nothing for it afterward, whether or not the range's own registration reached it. A dead run's segment is attached with `track` false, since the tracker it was registered with died with that run."""
    try:
        segment = shared_memory.SharedMemory(name, track=track)
    except FileNotFoundError:
        return
    segment.unlink()
    segment.close()


SEGMENT_RECORD = "segment-names"


def _segment_names(token: str, position: int) -> ShipTo:
    stem = f"oracle-{token}-{position}"
    return ShipTo(f"{stem}a", f"{stem}s", f"{stem}m")


def _section_name(token: str, slot: int) -> str:
    """The name of the segment the section task in `slot` ships its configuration's bytes of the part home in (`audit_section_task`)."""
    return f"oracle-{token}-x{slot}"


def _unlink_recorded_segments(scratch: Path) -> None:
    """Unlink every shared memory segment a dead run's writer named, from the token, range count and section count it recorded in that run's scratch directory (`InOrderWriter`, `SEGMENT_RECORD`). A segment already gone, or a record that cannot be read, leaves nothing to do, and nothing here raises."""
    try:
        token, count, *sections = (scratch / SEGMENT_RECORD).read_text().split()
        positions = range(int(count))
        slots = range(int(sections[0]) if sections else 0)
    except OSError, ValueError:
        return
    for position in positions:
        names = _segment_names(token, position)
        for name in (names.audit, names.store, names.memo):
            with suppress(OSError, ValueError):
                _unlink_if_made(name, track=False)
    for slot in slots:
        with suppress(OSError, ValueError):
            _unlink_if_made(_section_name(token, slot), track=False)


class _Mapped:
    """A shipped segment mapped and unlinked at once, so that its name never outlives the mapping, with its pieces as views: by a section task, by the parent's in-order writer, or by a settle memo absorb's worker. Attaching registers the name with the resource tracker again, which holds a name once, and unlinking unregisters it, so a segment mapped here leaves the tracker nothing to clean up. Its pages enter the mapping process's footprint as it reads them and leave at `close`."""

    def __init__(self, shipped: Shipped) -> None:
        self._segment = shared_memory.SharedMemory(shipped.name)
        self._segment.unlink()
        buffer = self._segment.buf
        assert buffer is not None
        self.size = sum(shipped.lengths)
        self.closed = False
        bounds = list(itertools.accumulate(shipped.lengths, initial=0))
        self.pieces = [buffer[first:stop] for first, stop in itertools.pairwise(bounds)]

    def close(self) -> None:
        for piece in self.pieces:
            piece.release()
        self.pieces = []
        self._segment.close()
        self.closed = True


def _section_order(name: bytes) -> tuple[int, bytes]:
    """A section's place in the audit's order, from its configuration's name as the part holds it: the first two fields of `audit_key`."""
    return (_AUDIT_RANKS.get(name, len(_AUDIT_RANKS)), name)


def _field_of(line: bytes, field: int) -> bytes:
    """The field at `field` of a line, the section a line of a part belongs to: a base row's first field, a segment line's second, after its `+` or `-`."""
    return line.rstrip(b"\n").split(b"\t", field + 1)[field]


class _FileLines:
    """Whole lines of an open part read by position (`os.pread`), which leaves the handle where it was, for a bisection over its line starts."""

    _READ = 1 << 12

    def __init__(self, descriptor: int, size: int) -> None:
        self._descriptor = descriptor
        self._size = size

    def line_at(self, start: int) -> bytes:
        """The whole line that starts at `start`, with its newline, or the rest of the part when it has none."""
        pieces: list[bytes] = []
        at = start
        while at < self._size:
            piece = os.pread(self._descriptor, min(self._READ, self._size - at), at)
            if not piece:
                break
            end = piece.find(b"\n")
            if end >= 0:
                pieces.append(piece[: end + 1])
                break
            pieces.append(piece)
            at += len(piece)
        return b"".join(pieces)

    def line_start(self, low: int, middle: int) -> int:
        """The start of the line that holds byte `middle`, no lower than `low`, a line start: one past the last newline in `[low, middle)`, or `low`."""
        end = middle
        while end > low:
            begin = max(low, end - self._READ)
            found = os.pread(self._descriptor, end - begin, begin).rfind(b"\n")
            if found >= 0:
                return begin + found + 1
            end = begin
        return low


def plain_sections(handle: BinaryIO, part: row_delta.Part, field: int) -> list[tuple[bytes, int, int]]:
    """Each section of a plain audit part, in file order, as its configuration's name and the span of its lines, found without reading the part through: past the header line, the first line names a section, and a bisection over line starts by `os.pread` (`_FileLines`) finds the first line whose section comes after it in the audit's order (`_section_order`), which starts the next. `field` is where a line names its section, 0 for a base and 1 for a segment. A section that does not come after the one before it, or whose last line is not its own, raises `ValueError`, since a part out of the audit's order has no sections to cut."""
    lines = _FileLines(handle.fileno(), part.size)
    start = len(lines.line_at(0))
    sections: list[tuple[bytes, int, int]] = []
    while start < part.size:
        line = lines.line_at(start)
        name = _field_of(line, field)
        order = _section_order(name)
        if sections and order <= _section_order(sections[-1][0]):
            raise ValueError(
                f"{part.name} is out of the audit's order at its {name.decode('utf-8', 'replace')!r} section"
            )
        low, high = start + len(line), part.size
        while low < high:
            at = lines.line_start(low, (low + high) // 2)
            found = lines.line_at(at)
            if _section_order(_field_of(found, field)) <= order:
                low = at + len(found)
            else:
                high = at
        closing = _field_of(lines.line_at(lines.line_start(start, low - 1)), field)
        if closing != name:
            raise ValueError(
                f"{part.name} is out of the audit's order: a {closing.decode('utf-8', 'replace')!r} row ends its {name.decode('utf-8', 'replace')!r} section"
            )
        sections.append((name, start, low))
        start = low
    return sections


def part_sections(handle: BinaryIO, part: row_delta.Part, field: int) -> list[bytes]:
    """The configurations an audit part holds a section of, in file order: its member index's sections for a part held as gzip members, and `plain_sections` for a plain one. A section that does not come after the one before it in the audit's order (`_section_order`) raises `ValueError`, from the index as `plain_sections` raises it from the lines, since a section task reads each section alone and would not see the order break that the serial writer's compare refuses."""
    if part.members is None:
        return [name for name, _start, _end in plain_sections(handle, part, field)]
    sections = [member.section.encode("utf-8") for member in part.members[1:]]
    for before, after in itertools.pairwise(sections):
        if _section_order(after) <= _section_order(before):
            raise ValueError(
                f"{part.name} is out of the audit's order at its {after.decode('utf-8', 'replace')!r} section"
            )
    return sections


class _Span(io.RawIOBase):
    """The bytes `[start, end)` of an open part, read by position (`os.preadv`) into the caller's buffer, which leaves the handle where it was: one section of a plain part."""

    def __init__(self, descriptor: int, name: str, start: int, end: int) -> None:
        super().__init__()
        self._descriptor = descriptor
        self._name = name
        self._at = start
        self._end = end

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        view = memoryview(buffer).cast("B")
        count = min(len(view), self._end - self._at)
        if count <= 0:
            return 0
        read = os.preadv(self._descriptor, [view[:count]], self._at)
        if not read:
            raise row_delta.HeadError(f"{self._name} ends at byte {self._at}, inside a section")
        self._at += read
        return read


class _SectionInflating(row_delta._Inflating):
    """The raw stream of one gzip member of a part held as members, inflated from its offset and checked as `row_delta.part_stream` checks every member: one that does not inflate, whose gzip stream ends before its recorded length or runs past it, or that inflates to another length than the index records raises `row_delta.HeadError` naming the part and the section."""

    def __init__(self, descriptor: int, part: row_delta.Part, member: row_delta.Member, offset: int) -> None:
        super().__init__(descriptor, replace(part, members=(member,)))
        self._offset = offset


def section_stream(handle: BinaryIO, part: row_delta.Part, section: bytes, field: int) -> BinaryIO:
    """One configuration's section of an open audit part, inflated, with no header line, as a buffered stream read by position, so the handle stays where it was: for a part held as gzip members the section's member (`_SectionInflating`), found from the member index, and for a plain part its span of lines (`plain_sections`, `_Span`). A part with no such section gives an empty stream."""
    if part.members is not None:
        offset = 0
        for member in part.members:
            if member.section.encode("utf-8") == section:
                return cast(
                    BinaryIO,
                    io.BufferedReader(
                        _SectionInflating(handle.fileno(), part, member, offset), row_delta.INFLATE_READ_BYTES
                    ),
                )
            offset += member.length
        return io.BytesIO()
    for name, start, end in plain_sections(handle, part, field):
        if name == section:
            return cast(BinaryIO, io.BufferedReader(_Span(handle.fileno(), part.name, start, end), 1 << 20))
    return io.BytesIO()


def _section_rows(opened: row_delta.OpenParts, section: bytes) -> Iterator[bytes]:
    """The materialized rows of one configuration's section of an open audit head, as runs of bytes: its section of the base merged by key with its section of each segment (`row_delta.merge_plain_keyed`), each read through `section_stream`. A section is one configuration's run of rows in the audit's order, so these are the rows `_materialized_rows` yields between the section's ends."""
    head = opened.head
    base = section_stream(opened.handles[0], head.base, section, 0)
    segments = [
        row_delta._keyed_rows(section_stream(handle, part, section, 1), head.output, number)
        for number, (handle, part) in enumerate(zip(opened.handles[1:], head.segments), start=1)
    ]
    yield from row_delta.merge_plain_keyed(base, segments, audit_key, audit_key)


class _Ends:
    """A byte stream of whole lines passed through as it is read, keeping its first and last lines, for the check that a section's rows are all its configuration's (`_check_section`)."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = chunks
        self.first: bytes | None = None
        self.last: bytes | None = None

    def __iter__(self) -> Iterator[bytes]:
        head = tail = b""
        for chunk in self._chunks:
            if not chunk:
                continue
            if self.first is None:
                head += chunk
                if (cut := head.find(b"\n")) >= 0:
                    self.first, head = head[: cut + 1], b""
            at = chunk.rfind(b"\n", 0, len(chunk) - 1)
            tail = chunk[at + 1 :] if at >= 0 else (chunk if tail.endswith(b"\n") else tail + chunk)
            yield chunk
        self.last = tail or None


def _check_section(config: str, ends: tuple[bytes | None, bytes | None], what: str) -> None:
    """Refuse a stream of rows in the audit's order whose first or last row is not `config`'s, which, since the order sorts by configuration first, finds any row of another configuration between them."""
    for line in ends:
        if line is not None and _field_of(line, 0) != config.encode():
            raise ValueError(
                f"{what} for {config} hold a row of another configuration: {line.rstrip().decode('utf-8', 'replace')[:160]!r}"
            )


class _SectionMember:
    """One section's lines as one gzip member in memory, opened at its first line, so a section with none makes no member, and written by one `gzip.GzipFile` at level 1 with mtime 0 and no file name, as `row_delta.MemberPart` writes each of its members, so its bytes are that part's member for the same lines. `rows` and `inflated` count the lines and their bytes, and `seconds` the time spent compressing."""

    def __init__(self) -> None:
        self._buffer = io.BytesIO()
        self._stream: gzip.GzipFile | None = None
        self.rows = 0
        self.inflated = 0
        self.seconds = 0.0

    def write(self, data: bytes) -> None:
        started = time.perf_counter()
        if self._stream is None:
            self._stream = gzip.GzipFile(
                filename="", fileobj=self._buffer, mode="wb", mtime=0, compresslevel=1
            )
        self._stream.write(data)
        self.rows += data.count(b"\n")
        self.inflated += len(data)
        self.seconds += time.perf_counter() - started

    def discard(self) -> None:
        """Close the member's stream and its buffer without shipping it, for a task that stops partway, so no stream is left to flush into a closed buffer when it is collected."""
        stream, self._stream = self._stream, None
        if stream is not None:
            with suppress(Exception):
                stream.close()
        self._buffer.close()

    def ship(self, name: str) -> Shipped | None:
        """Close the member and copy it into the shared memory segment `name` (`ship`), or make none when the section had no line."""
        if self._stream is None:
            return None
        started = time.perf_counter()
        self._stream.close()
        self.seconds += time.perf_counter() - started
        view = self._buffer.getbuffer()
        try:
            return ship([cast(bytes, view)], name)
        finally:
            view.release()
            self._buffer.close()


@dataclass(frozen=True)
class SectionResult:
    """What one configuration's section of the audit came to (`audit_section_task`): the pass's rows the compare took, its upserts and tombstones, and the section's bytes in this pass's part, in the shared memory segment `output` names, or None when the part holds none of it: one gzip member, which `member` describes for the part's member index, or, on a `first` or `unusable` base, the section's plain lines as the ranges' compressed blocks, a piece a block, which the parent inflates as it writes them. `part_rows` and `part_bytes` count the lines in the part and their inflated bytes. `seconds` is the compare's, `compress_seconds` the member's, `held_peak_bytes` the most its old stream held at once, `wall_seconds` the task's, and `peak_footprint_bytes` its process's peak footprint (`peak_rss.peak_footprint_self_bytes`), which the pool record takes as `<config> audit`."""

    config: str
    rows: int
    upserts: int
    tombstones: int
    output: Shipped | None
    member: row_delta.Member | None
    part_rows: int
    part_bytes: int
    seconds: float
    compress_seconds: float
    held_peak_bytes: int
    wall_seconds: float
    peak_footprint_bytes: int


def audit_section_task(
    out_dir: Path,
    config: str,
    route: str,
    reason: str,
    head: row_delta.Head | None,
    blocks: Sequence[Shipped | None],
    ship_name: str,
) -> SectionResult:
    """Compare one configuration's section of the audit, in a fresh pool process (`InOrderWriter`): the lines its row ranges shipped home, compressed a block at a time (`AuditBlocks`), mapped from their shared memory segments in range order and each name unlinked as it is mapped (`_Mapped`), against the standing audit's section, its base's and each segment's merged by key (`_section_rows`, over `head`'s parts, which this process opens itself; None on a route that reads no standing audit), through the compare the serial writer runs over the whole audit (`SectionCompare`). The new rows' and the standing rows' first and last lines must be `config`'s (`_check_section`). The section's bytes in the part go home in the shared memory segment `ship_name`: on the segment route its upserts and tombstones and on a fold every row, each as one gzip member (`_SectionMember`), which `row_delta.MemberPart` would write for the same lines; and on a `first` or `unusable` base every row, plain, which are exactly the ranges' lines, so the task ships the ranges' compressed blocks as they came, one piece a block, and the parent inflates them as it writes the part, as the serial writer inflates them as it feeds them, so neither holds a section's plain lines. With no blocks it tombstones every standing row of a configuration the plan does not compare. Returns what the section came to (`SectionResult`)."""
    started = time.perf_counter()
    mapped: list[_Mapped] = []
    member = _SectionMember()
    try:
        for shipped in blocks:
            if shipped is not None:
                mapped.append(_Mapped(shipped))
        pieces = [piece for segment in mapped for piece in segment.pieces]
        with ExitStack() as stack:
            ends: _Ends | None = None
            old: _OldRows | None = None
            if head is not None:
                opened = stack.enter_context(row_delta.open_parts(Path(out_dir), head))
                ends = _Ends(_section_rows(opened, config.encode()))
                old = _OldRows(ends)
            plain = route == BASE_ROUTE and reason != "fold"
            size = 0

            def counted(data: bytes) -> None:
                nonlocal size
                size += len(data)

            compare = SectionCompare(old, counted if plain else member.write, route)
            first: bytes | None = None
            last: bytes | None = None
            for piece in pieces:
                lines = zlib.decompress(piece)
                if lines:
                    if first is None:
                        first = lines[: lines.index(b"\n") + 1]
                    last = lines[lines.rfind(b"\n", 0, len(lines) - 1) + 1 :]
                compare.feed(lines)
            compare.drain()
            _check_section(config, (first, last), "the pass's audit rows")
            if ends is not None:
                _check_section(config, (ends.first, ends.last), "the standing audit's rows")
        if plain:
            output = ship(cast(list[bytes], list(pieces)), ship_name)
            part_rows, part_bytes, index = compare.rows, size, None
        else:
            output = member.ship(ship_name)
            part_rows, part_bytes = member.rows, member.inflated
            index = None if output is None else row_delta.Member(config, sum(output.lengths), member.inflated)
        del pieces
    except BaseException:
        member.discard()
        raise
    finally:
        for segment in mapped:
            segment.close()
    return SectionResult(
        config,
        compare.rows,
        compare.upserts,
        compare.tombstones,
        output,
        index,
        part_rows,
        part_bytes,
        compare.seconds,
        member.seconds,
        compare.held_peak_bytes,
        time.perf_counter() - started,
        peak_footprint_self_bytes(),
    )


def _header_member(header: bytes) -> bytes:
    """The header line's gzip member as `row_delta.MemberPart` writes it: level 1, mtime 0, no file name."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buffer, mode="wb", mtime=0, compresslevel=1) as stream:
        stream.write(header)
    return buffer.getvalue()


class _InlineExecutor(Executor):
    """An executor that runs each task as it is submitted, in this process, and returns its finished future: the in-order writer's when its caller hands it no pool, as the tests that drive a fan-in by hand do."""

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


class InOrderWriter:
    """The pooled oracle's fan-in, in `run_m1`'s parent: it writes the audit once, as the serial writer (`AuditWriter`) writes it over the same rows, from one comparison a configuration run on the oracle's pool, and it hands each configuration's store records, the members its ranges ship, to the store set's writer (`oracle_cache.StoreSet`) when its last range lands. A range ships its bytes in the POSIX shared memory segments the writer names for it (`ship_to`, `ship`), and its result carries only their names and lengths. `land` takes each range's result as its future resolves, in any order, and takes the names off the result, so neither it nor its future keeps them. The audit's lines wait unmapped in their segments until the configuration's last range lands; then `land` submits the configuration's section task (`audit_section_task`) to `executor`, the oracle's pool, with the route the plan chose (`AuditPlan`, joined at the first submission) and the configuration's range segments in row order, and the task maps them, compares them with the standing audit's section and ships the section's bytes in the part home in a segment the writer names for the configuration (`section_name`). A configuration none of whose rows the plan compares but which the standing audit holds a section of gets a task with no ranges on the segment route, which tombstones every row of it; the plan's first submission finds those sections (`part_sections`), or the exit when nothing was submitted, which then runs them in this process. `in_flight` lists the section tasks not yet finished, which `run_m1_gates.run_oracle` counts against the pool's width as it does its absorbs. The writer maps each store segment as its range lands, and holds a configuration's members only until its last range lands and the store set's writer, on a thread of its own (`stores`), has compared or written them, so at worst it holds every configuration's members at once, which are the compressed stores the pass writes. The writer names each range's settle memo segment too, which it never maps: `run_m1_gates.run_oracle` hands it to the configuration's absorb, whose worker maps it.

    What it holds sits in shared memory, which no process's footprint counts while it waits: a range closes its mapping once it has copied its bytes in, a range's audit segment waits unmapped until its configuration's task maps it, and the writer's mapping of a store segment touches a page only as the store set's writer reads it (`Shipped`). The peak footprint a bare run records under `run_m1.PARENT_POOL_UNIT`, which the run-m1-parent row of `make job-costs` reports, therefore leaves out what waits. So the writer counts it itself: `held_peak_bytes` is the most it held in shared memory at once, a landed range's audit lines until its configuration's task has finished, a store segment until the store set's writer has read it, and a section's bytes from its task's end until they are written into the part; `landing_seconds` is the time `land` took over every range, and `committed_at` the moment the audit was committed and the stores finished; `run_m1_gates.run_oracle` prints them on its `[t] oracle_fan_in` line.

    The audit's head is committed only when the block exits normally and three checks pass, so a partial audit never replaces the standing one: every planned range must have landed, every section task must have returned, and the lines the tasks took must equal the landed ranges' own `divergent_rows` total; a failure raises, and nothing is committed (`AuditPlan.settled`, `AuditRoute.discard`). Then the writer writes the part from the sections, in the audit's order, under the head's next part name: the header line, or the header line's gzip member, then each section's bytes as its task shipped them, mapped and unlinked one at a time (`_write_sections`), so the part, its size and sha256, its rows and its member index are the ones `AuditWriter` writes. On the segment route a pass whose sections hold nothing writes no part and commits the head alone. The commit follows `AuditWriter`'s content rule (`_audit_head`, `_commit_audit`), and `[t] oracle_audit` reports the tasks' compare and compress seconds summed and the most any section's old stream held. The stores are written only when `stores` is given and `row_cache` writes them: a configuration one of whose ranges returned no member, or whose ranges disagree on the pass ordinal (which `merge_config_shards` refuses), hands the store set's writer nothing, so `oracle_cache.promote_stores` commits no head. The store set's writer's own errors stop the block before the audit commits. On every exit, normal or not, the writer waits for the store set's writer and every section task it submitted, cancelling those not started when the exit is not normal, closes each mapping it still holds and unlinks every segment it named that is still there, a range's, a section's, and a settle memo segment no absorb mapped, so a run that fails or is interrupted while this block unwinds leaves no segment behind, and a run that maps every segment leaves the resource tracker nothing to warn about. The writer records its token, its range count and its section count in the run's scratch directory as it enters, and again as it names a section the plan did not (`SEGMENT_RECORD`), and deletes the record once its exit has unlinked every name, so after a SIGKILL of the whole process group, which skips the exit, the next oracle run over the same `out_dir` unlinks the segments the dead run left (`discard_oracle_audit_scratch`).
    """

    def __init__(
        self,
        out_dir: Path,
        shards: Iterable[OracleShard],
        row_cache: OracleRowCache | None = None,
        configs: Sequence[str] = ORACLE_CONFIGS,
        stores: oracle_cache.StoreSet | None = None,
        executor: Executor | None = None,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._order = sorted(shards, key=lambda shard: (configs.index(shard.config), shard.first_row))
        self._positions = {shard: position for position, shard in enumerate(self._order)}
        self._by_config: dict[str, list[int]] = {}
        for position, shard in enumerate(self._order):
            self._by_config.setdefault(shard.config, []).append(position)
        self._slots = {config: slot for slot, config in enumerate(self._by_config)}
        self.executor: Executor = _InlineExecutor() if executor is None else executor
        self._row_cache = row_cache
        self.stores = stores if row_cache is not None and row_cache.write_dir is not None else None
        self._token = secrets.token_hex(6)
        self._landed: dict[int, Shipped | None] = {}
        self._counted = 0
        self._segments: dict[str, dict[int, tuple[int, _Mapped | None, int | None]]] = {}
        self._handed: list[tuple[Future, list[_Mapped | None]]] = []
        self._sections: dict[str, Future] = {}
        self._waiting: dict[str, int] = {}
        self._unwritten: dict[str, int] = {}
        self._reaped: set[str] = set()
        self._discovered = False
        self._lock = threading.Lock()
        self._held = 0
        self.held_peak_bytes = 0
        self.landing_seconds = 0.0
        self.committed_at: float | None = None
        self._plan: AuditPlan | None = None
        self._route: AuditRoute | None = None
        self._storing: ThreadPoolExecutor | None = None

    def __enter__(self) -> "InOrderWriter":
        scratch = oracle_audit_scratch(self._out_dir)
        scratch.mkdir(parents=True, exist_ok=True)
        self._record = scratch / SEGMENT_RECORD
        self._write_record()
        try:
            self._plan = AuditPlan(self._out_dir)
        except BaseException:
            self._record.unlink(missing_ok=True)
            with suppress(OSError):
                scratch.rmdir()
            raise
        if self.stores is not None:
            self._storing = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oracle-stores")
        return self

    def _write_record(self) -> None:
        self._record.write_text(f"{self._token} {len(self._order)} {len(self._slots)}\n")

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            try:
                self._release(cancel=True)
            finally:
                self._abandon()
            return
        try:
            missing = [
                shard.label for position, shard in enumerate(self._order) if position not in self._landed
            ]
            if missing:
                raise ValueError(
                    f"the oracle has no audit rows from {', '.join(missing)} — divergence-audit.tsv is untouched"
                )
            route = self._joined_route()
            if not self._discovered:
                self._discover(route, inline=True)
            results = {config: future.result() for config, future in self._sections.items()}
            failures = self._join_stores()
            rows = sum(result.rows for result in results.values())
            if rows != self._counted:
                raise ValueError(
                    f"the oracle's ranges returned {rows} audit rows where they counted {self._counted} divergent — divergence-audit.tsv is untouched"
                )
            if failures:
                raise failures[0]
            self._commit(route, results)
        except BaseException:
            try:
                self._release(cancel=True)
            finally:
                self._abandon()
            raise
        self._release(cancel=False)
        if self.stores is not None:
            self.stores.finish()
        self.committed_at = time.perf_counter()

    def _abandon(self) -> None:
        assert self._plan is not None
        (self._route or self._plan.settled()).discard()
        if self.stores is not None:
            self.stores.abandon()

    def ship_to(self, shard: OracleShard) -> ShipTo:
        """Return the names of the segments `shard` ships its bytes home in (`ShipTo`): the writer's own random token and the range's place in the audit's order, so two writers' names never meet and each range's are its own, in fewer than the 31 bytes macOS allows a segment's name."""
        return _segment_names(self._token, self._positions[shard])

    def section_name(self, config: str) -> str:
        """The name of the segment `config`'s section task ships the section's bytes home in."""
        return _section_name(self._token, self._slots[config])

    def in_flight(self) -> list[Future]:
        """The section tasks submitted and not yet finished, each of which holds one slot of the pool's width."""
        self._reap()
        return [future for future in self._sections.values() if not future.done()]

    def section_results(self) -> dict[str, SectionResult]:
        """Every section task's result, waiting for each, in the order they were submitted; a task's error is raised."""
        return {config: future.result() for config, future in self._sections.items()}

    def land(self, shard: OracleShard, result: OracleConfigResult) -> None:
        """Take one range's result: keep its audit segment's name for its configuration's section task, submitting that task when this is the last of the configuration's ranges to land, and hand the configuration's members to the store set's writer at the same moment."""
        started = time.perf_counter()
        self._land(shard, result)
        self.landing_seconds += time.perf_counter() - started

    def _land(self, shard: OracleShard, result: OracleConfigResult) -> None:
        position = self._positions.get(shard)
        if position is None or position in self._landed:
            raise ValueError(f"the oracle's fan-in is not waiting for {shard.label}")
        blocks, result.audit_blocks = result.audit_blocks, None
        segment, result.store_segment = result.store_segment, None
        self._counted += result.divergent_rows
        self._landed[position] = blocks
        self._waiting[shard.config] = self._waiting.get(shard.config, 0) + _shipped_size(blocks)
        self._hold(_shipped_size(blocks))
        positions = self._by_config[shard.config]
        if all(at in self._landed for at in positions):
            self._submit(shard.config, [self._landed[at] for at in positions])
        member = self._map(segment)
        stores = self.stores
        if stores is None or self._storing is None:
            self._close(member)
            return
        held = self._segments.setdefault(shard.config, {})
        held[shard.index] = (result.rows_compared, member, result.pass_ordinal)
        if len(held) < shard.of:
            return
        del self._segments[shard.config]
        ranges = [held[index] for index in range(shard.of)]
        mappings = [mapped for _rows, mapped, _ordinal in ranges]
        ordinal = ranges[0][2]
        if (
            ordinal is None
            or any(mapped is None for mapped in mappings)
            or any(other != ordinal for *_, other in ranges)
        ):
            for mapped in mappings:
                self._close(mapped)
            return
        members = [(rows, cast(_Mapped, mapped).pieces[0]) for rows, mapped, _ordinal in ranges]
        future = self._storing.submit(self._store, shard.config, members, shard.of == 1, ordinal, mappings)
        self._handed.append((future, mappings))

    def _joined_route(self) -> AuditRoute:
        if self._route is None:
            assert self._plan is not None
            self._route = self._plan.route()
        return self._route

    def _submit(self, config: str, blocks: list[Shipped | None]) -> None:
        route = self._joined_route()
        if not self._discovered:
            self._discover(route, inline=False)
        self._sections[config] = self.executor.submit(
            audit_section_task,
            self._out_dir,
            config,
            route.route,
            route.reason,
            None if route.opened is None else route.head,
            blocks,
            self.section_name(config),
        )

    def _discover(self, route: AuditRoute, inline: bool) -> None:
        """Check the order of the standing audit's sections in every part (`part_sections`), which raises for a part out of the audit's order, and submit a task with no ranges for each configuration the standing audit holds a section of and the plan does not compare: on the segment route it writes a tombstone for every row of it, and on a fold, whose base holds only the pass's rows, it writes nothing and counts those rows as tombstones, so the fold moves the head's content as the serial writer's does. A route that reads no standing audit needs neither. `inline` runs the tasks in this process, for an exit whose pool may have shut down."""
        self._discovered = True
        if route.opened is None:
            return
        head = route.opened.head
        standing = {
            name.decode("utf-8")
            for number, (handle, part) in enumerate(zip(route.opened.handles, head.parts))
            for name in part_sections(handle, part, 0 if number == 0 else 1)
        }
        unplanned = sorted(standing - set(self._slots), key=_audit_config_order)
        if not unplanned:
            return
        for config in unplanned:
            self._slots[config] = len(self._slots)
        self._write_record()
        executor = _InlineExecutor() if inline else self.executor
        for config in unplanned:
            self._sections[config] = executor.submit(
                audit_section_task,
                self._out_dir,
                config,
                route.route,
                route.reason,
                head,
                [],
                self.section_name(config),
            )

    def _reap(self) -> None:
        """Count each finished section task's ranges as no longer held and its shipped section as held."""
        for config, future in self._sections.items():
            if config in self._reaped or not future.done():
                continue
            self._reaped.add(config)
            self._hold(-self._waiting.pop(config, 0))
            if not future.cancelled() and future.exception() is None:
                self._unwritten[config] = _shipped_size(future.result().output)
                self._hold(self._unwritten[config])

    def _commit(self, route: AuditRoute, results: Mapping[str, SectionResult]) -> None:
        """Write the part from the sections and commit the head, as `AuditWriter.commit` does over the same rows."""
        self._reap()
        started = time.perf_counter()
        ordered = sorted(results.values(), key=lambda result: _audit_config_order(result.config))
        prior = route.head
        if route.route == SEGMENT_ROUTE:
            assert prior is not None
            number = len(prior.segments) + 1
            name = row_delta.segment_name(AUDIT_NAME, prior.generation, number)
            header = row_delta.segment_header(AUDIT_NAME, prior.generation, number, row_delta.MERGE_PLAIN_KEY)
        else:
            name = row_delta.base_name(AUDIT_NAME, 0 if prior is None else prior.generation + 1)
            header = _AUDIT_HEADER_LINE
        written = [result for result in ordered if result.output is not None]
        part = (
            None
            if route.route == SEGMENT_ROUTE and not written
            else self._write_sections(
                name, header, route.route == SEGMENT_ROUTE or route.reason == "fold", written
            )
        )
        upserts = sum(result.upserts for result in ordered)
        tombstones = sum(result.tombstones for result in ordered)
        head = _audit_head(route, part, bool(upserts or tombstones))
        _commit_audit(self._out_dir, route, head)
        _audit_timing(
            sum(result.seconds for result in ordered) + time.perf_counter() - started,
            (sum(result.rows for result in ordered), upserts, tombstones),
            route,
            part,
            sum(result.compress_seconds for result in ordered),
            head,
            max((result.held_peak_bytes for result in ordered), default=0),
        )

    def _write_sections(
        self, name: str, header: bytes, members: bool, sections: Sequence[SectionResult]
    ) -> row_delta.Part:
        """Write the part `name` under its final name, which no head lists until the commit: `header`, as its own gzip member when the part is held as `members`, then each section's bytes in order, each segment mapped and unlinked as it is reached (`_Mapped`) and closed once written, all through one hashing count (`row_delta._Counting`), and return its `Part`. A plain part's sections come as the ranges' compressed blocks, each inflated as it is written, and must inflate to the bytes their tasks counted."""
        index: list[row_delta.Member] = []
        with output_sink.replacing(self._out_dir / name) as handle:
            counting = row_delta._Counting(handle)
            if members:
                leading = _header_member(header)
                index.append(row_delta.Member("", len(leading), len(header)))
                counting.write(leading)
            else:
                counting.write(header)
            for section in sections:
                assert section.output is not None
                mapped = _Mapped(section.output)
                try:
                    written = 0
                    for piece in mapped.pieces:
                        data = piece if members else zlib.decompress(piece)
                        counting.write(data)
                        written += len(data)
                    if not members and written != section.part_bytes:
                        raise ValueError(
                            f"{section.config}'s audit rows inflated to {written} bytes where its task counted {section.part_bytes}"
                        )
                finally:
                    mapped.close()
                    self._hold(-self._unwritten.pop(section.config))
                if section.member is not None:
                    index.append(section.member)
        return row_delta.Part(
            name,
            counting.size,
            counting.hexdigest(),
            sum(section.part_rows for section in sections),
            sum(section.part_bytes for section in sections),
            members=tuple(index) if members else None,
        )

    def _store(
        self,
        config: str,
        members: list[tuple[int, memoryview]],
        whole: bool,
        ordinal: int,
        mappings: list[_Mapped | None],
    ) -> None:
        assert self.stores is not None
        try:
            self.stores.put(config, members, whole, ordinal)
        finally:
            members.clear()
            for mapped in mappings:
                self._close(mapped)

    def _hold(self, size: int) -> None:
        with self._lock:
            self._held += size
            self.held_peak_bytes = max(self.held_peak_bytes, self._held)

    def _map(self, shipped: Shipped | None) -> _Mapped | None:
        if shipped is None:
            return None
        mapped = _Mapped(shipped)
        self._hold(mapped.size)
        return mapped

    def _close(self, mapped: _Mapped | None) -> None:
        if mapped is not None and not mapped.closed:
            mapped.close()
            self._hold(-mapped.size)

    def _join_stores(self, cancel: bool = False) -> list[BaseException]:
        """Wait for the store set's writer to finish what it was handed, or with `cancel` for what it has begun, cancelling the rest, closing the members of every store job that never ran, and return the store jobs' errors."""
        failures: list[BaseException] = []
        if self._storing is not None:
            self._storing.shutdown(wait=True, cancel_futures=cancel)
        for future, mappings in self._handed:
            if future.cancelled():
                for mapped in mappings:
                    self._close(mapped)
            elif (error := future.exception()) is not None:
                failures.append(error)
        self._handed = []
        return failures

    def _release(self, cancel: bool) -> None:
        """Wait for the store set's writer (`_join_stores`) and for every section task, with `cancel` cancelling those not started, close every mapping still held (a configuration's members short of its last range, members whose store job never ran), then unlink every segment named for one of the writer's ranges or sections that is still there: one a range made whose result the writer never took or whose task never mapped it, a section no commit wrote, or a settle memo segment no absorb mapped. Once every name is unlinked it deletes the record of them (`SEGMENT_RECORD`)."""
        self._join_stores(cancel)
        for future in self._sections.values():
            if cancel:
                future.cancel()
            with suppress(CancelledError):
                future.exception()
        held = [mapped for ranges in self._segments.values() for _rows, mapped, _ordinal in ranges.values()]
        self._segments = {}
        for mapped in held:
            self._close(mapped)
        for shard in self._order:
            names = self.ship_to(shard)
            _unlink_if_made(names.audit)
            _unlink_if_made(names.store)
            _unlink_if_made(names.memo)
        for slot in range(len(self._slots)):
            _unlink_if_made(_section_name(self._token, slot))
        self._reap()
        self._hold(-sum(self._waiting.values()) - sum(self._unwritten.values()))
        self._waiting, self._unwritten = {}, {}
        self._record.unlink(missing_ok=True)
        with suppress(OSError):
            self._record.parent.rmdir()


def _shipped_size(shipped: Shipped | None) -> int:
    return 0 if shipped is None else sum(shipped.lengths)


def _oracle_audit_owner_is_running(pid: str) -> bool:
    """Return whether the process whose pid names a scratch directory is still running. A name that is not a positive integer counts as not running. A pid this process may not signal, or one too large to be a pid, counts as running, so the cleanup leaves the directory in place instead of raising."""
    if not pid.isdigit() or int(pid) < 1:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError, OverflowError:
        return True
    return True


def discard_oracle_audit_scratch(out_dir: Path) -> None:
    """Delete this run's scratch directory, and any earlier run's whose process has exited, since a killed run skips the `finally` that would have deleted it. Before deleting an earlier run's directory it unlinks the shared memory segments that run's writer recorded there (`_unlink_recorded_segments`): a SIGKILL of that run's whole process group kills its resource tracker too, and macOS lists no POSIX shared memory, so those names are the only way to find what it left. A directory whose pid is still running belongs to another oracle and is kept. Nothing here raises, so calling it in a `finally` cannot replace the exception being handled."""
    out = Path(out_dir)
    shutil.rmtree(oracle_audit_scratch(out), ignore_errors=True)
    with suppress(OSError):
        for path in out.glob("divergence-audit.parts.*"):
            if not _oracle_audit_owner_is_running(path.name.rpartition(".")[2]):
                _unlink_recorded_segments(path)
                shutil.rmtree(path, ignore_errors=True)


def _compare_config(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    config: str,
    features: frozenset[str],
    aliases,
    ledger: CompiledLedger,
    ink_identical_ids,
    shaper: "Shaper | IsolatedOverlayShaper | None",
    kern: "oracle_positions.KernEvaluator | None",
    guard_verdicts: settle.FormationGuard | None,
    audit: AuditSink | None,
    *,
    store: "oracle_cache.RowStore | None" = None,
    writer: "oracle_cache.RowWriter | None" = None,
    settle_memo: SettleMemoFile | None = None,
    first_row: int = 0,
    stop_row: int | None = None,
    label: str | None = None,
) -> OracleConfigResult:
    """Compare, classify, audit, and position-check rows `[first_row, stop_row)` of one configuration's table (the whole table by default). `guard_verdicts` is the crate's formation guard the walk forms under, and is None only for the overlay configuration, whose walk forms nothing. `shaper` is the overlay's synthetic shaper under the overlay, HarfBuzz otherwise, and None when there is no font. `label` names the `[t]` lines and defaults to the configuration."""
    result = OracleConfigResult(config=config, pass_ordinal=None if writer is None else writer.pass_ordinal)
    label = config if label is None else label
    start_row = first_row
    table_path = Path(subset_tables_dir) / f"baseline-{config}.subset.tsv.gz"
    if not table_path.exists():
        result.notes.append(f"{config}: subset table missing at {table_path}")
        return result
    # The oracle's rows are texts the conformance sweep also shapes, so they settle through the conformance sweep's shared settle memo (`settle_memo`). The rows of each chunk that need walking are settled in one `walk_many` call, and each row is compared with the stream that call returned. `_compare_row` reads cells, not glyph names, so the walk gets an empty `glyph_names`; its memo keys are the same either way. A store hit whose window spells out its whole row stays in the mapped file (`promote_pinned=False`), since no other row reads it. The overlay configuration's walk reads the registry and settles nothing.
    walker: _SettledWindowWalk | IsolatedOverlayWalk
    if isolated_overlay_active(spec, features):
        walker = IsolatedOverlayWalk(spec)
    else:
        assert guard_verdicts is not None
        walker = _SettledWindowWalk(
            spec, features, {}, guard_verdicts, memo=settle_memo, promote_pinned=False
        )
    config_started = time.perf_counter()
    rows = iter_rows(table_path, first_row, stop_row)
    # Only stale rows are walked. A served row's verdict comes from the store in the same form as a fresh one before `_match_compiled` sees it, and the second loop visits the chunk in table order, so the audit bytes do not depend on which rows were served. A walked row whose stored position `store.position_servable` allows keeps that position when its fresh settled digest equals the record's, which on a pass whose row stamp moved is how positions are served at all. Verification samples are drawn from served rows, not from written ones, because a read-only pass (`--gates-only`) serves verdicts without writing any. A served position verdict is used only when this pass's ledger sends the row through the position comparison. For a row the ledger excludes, the stored position verdict is written forward unchanged, so a later ledger edit that includes the row again can use it.
    sample = (
        oracle_cache.VerificationSample(store.environment.value, store.coverage_ordinal)
        if store is not None
        else None
    )
    position_sample = (
        oracle_cache.VerificationSample(f"{store.environment.value}\tpositions", store.coverage_ordinal)
        if store is not None
        else None
    )
    this_pass = writer.pass_ordinal if writer is not None else 0
    overlay = isinstance(walker, IsolatedOverlayWalk)
    unkeyed = position_record.NO_SETTLED_DIGEST
    # A writing pass carries each served row's settled digest forward from its record; a pass that writes nothing never reads it.
    carried_settled = store.settled if store is not None and writer is not None else None
    # A writing pass records each row it derives that `oracle_cache.unreachable_glyph_heads` refuses at `UNCOVERED_AGE`. Reach depends only on the registry, so a pass that loaded no store asks a mask of its own.
    coverage = None if writer is None else (store.mask if store is not None else oracle_cache.StaleMask(spec))
    while True:
        chunk = list(itertools.islice(rows, ORACLE_ROW_CHUNK))
        if not chunk:
            break
        served_at: dict[int, oracle_cache.StoredRecord] = {}
        positions_at: dict[int, tuple[oracle_cache.PositionVerdict, int, tuple[str, ...]]] = {}
        settled_at: dict[int, tuple[str, ...]] = {}
        fresh_at: list[int] = []
        offered: list[tuple[int, Row, tuple[str, ...]]] = []
        positions_offered: list[tuple[int, Row, tuple[str, ...]]] = []
        for offset, row in enumerate(chunk):
            index = first_row + offset
            if store is None or index >= store.rows:
                fresh_at.append(offset)
                continue
            mask = store.mask.mask_of(row.codepoints)
            servable = store.servable(index, mask)
            if servable == oracle_cache.SERVE_NOTHING:
                fresh_at.append(offset)
                if store.position_servable(index, mask):
                    settled_at[offset] = store.mask.families_of(mask)
                continue
            record = store.serve(index, row.codepoints)
            served_at[offset] = record
            reachable = store.mask.families_of(mask)
            offered.append((index, row, reachable))
            if servable == oracle_cache.SERVE_BOTH and record.position is not oracle_cache.UNSHAPED:
                positions_at[offset] = (record.position, record.position_age, reachable)
        if sample is not None:
            sample.offer_many(offered)
        walked = dict(zip(fresh_at, walker.walk_many([chunk[offset].text for offset in fresh_at])))
        for offset, row in enumerate(chunk):
            index = first_row + offset
            result.rows_compared += 1
            settled_digest = unkeyed
            if offset in served_at:
                record = served_at[offset]
                cached = record.row
                divergent = None if cached is None else _served_verdict(config, row, cached)
                derived_at = record.row_age
                if carried_settled is not None:
                    settled_digest = carried_settled(index)
            else:
                settled, _names = walked[offset]
                divergent = _compare_row(spec, aliases, config, features, row, settled)
                cached = _cached_verdict(divergent)
                derived_at = this_pass
                if coverage is not None and oracle_cache.unreachable_glyph_heads(
                    row.glyphs, coverage.families_of(coverage.mask_of(row.codepoints))
                ):
                    derived_at = oracle_cache.UNCOVERED_AGE
                position_reach = settled_at.get(offset)
                if writer is not None or position_reach is not None:
                    settled_digest = oracle_positions.settled_digest(settled, overlay)
                if (
                    position_reach is not None
                    and store is not None
                    and store.settled(index) == settled_digest
                ):
                    stored, stored_at = store.serve_position(index, row.codepoints)
                    if stored is not position_record.UNSHAPED:
                        positions_at[offset] = (stored, stored_at, position_reach)
            carried = positions_at.get(offset)
            position: oracle_cache.PositionVerdict = oracle_cache.UNSHAPED
            position_at = this_pass
            if carried is not None:
                position, position_at = carried[0], carried[1]
            matches = _match_compiled(ledger, divergent) if divergent is not None else []
            if shaper is not None:
                topology_clean = divergent is None or not ({"ligation", "junction"} & set(divergent.kinds))
                class_claims_ink_identity = divergent is None or (
                    len(matches) == 1 and matches[0] in ink_identical_ids
                )
                if topology_clean and class_claims_ink_identity:
                    if carried is not None and store is not None and position_sample is not None:
                        assert not isinstance(position, position_record._Unshaped)
                        mismatch = oracle_positions._served_position(position)
                        store.positions_served += 1
                        positions_offered.append((index, row, carried[2]))
                    else:
                        mismatch = oracle_positions._position_mismatch(shaper, kern, features, row)
                        position, position_at = oracle_positions._cached_position(mismatch), this_pass
                    result.positions_compared += 1
                    if mismatch is not None:
                        mismatch_notes, kern_attributable = mismatch
                        position_tags = ("position-kern-attributable",) if kern_attributable else ()
                        prior_ink_match = matches[0] if len(matches) == 1 else None
                        if divergent is None:
                            divergent = DivergentRow(
                                config=config,
                                codepoints=":".join(f"{cp:04X}" for cp in row.codepoints),
                                kinds=("position",),
                                position=-1,
                                baseline_glyphs=tuple(row.glyphs),
                                baseline_junctions=tuple(row.junctions),
                                new_cells=tuple(glyph for glyph in mismatch_notes),
                                new_junctions=(),
                                divergence_tags=position_tags + ("position-mismatch",),
                            )
                        else:
                            divergent = replace(
                                divergent,
                                kinds=divergent.kinds + ("position",),
                                divergence_tags=divergent.divergence_tags
                                + position_tags
                                + ("position-mismatch",),
                            )
                        rematch = _match_compiled(ledger, divergent)
                        # A kern-attributable mismatch is out of scope, so when nothing matches after it is added, a row that already matched a single ink-identical class keeps that match. In every other case, including a mismatch that is not kern-attributable (a real ink shift), the match list computed with the mismatch replaces the old one.
                        if not rematch and kern_attributable and prior_ink_match is not None:
                            matches = [prior_ink_match]
                        else:
                            matches = rematch
                else:
                    result.positions_excluded += 1
            if writer is not None:
                writer.append(row.codepoints, cached, derived_at, position, position_at, settled_digest)
            if divergent is None:
                continue
            result.divergent_rows += 1
            if len(matches) == 1:
                entry_id = matches[0]
                result.counts_by_entry[entry_id] = result.counts_by_entry.get(entry_id, 0) + 1
            elif not matches:
                result.unmatched_count += 1
                if len(result.unmatched_exemplars) < ORACLE_UNMATCHED_EXEMPLARS:
                    result.unmatched_exemplars.append(divergent)
            else:
                result.multi_matched_count += 1
            if audit is not None:
                audit.write(
                    "\t".join(
                        (
                            config,
                            divergent.codepoints,
                            ",".join(divergent.kinds),
                            (
                                matches[0]
                                if len(matches) == 1
                                else ("UNMATCHED" if not matches else "+".join(matches))
                            ),
                            "|".join(divergent.baseline_glyphs),
                            "|".join(divergent.new_cells),
                        )
                    )
                    + "\n"
                )
        if position_sample is not None:
            position_sample.offer_many(positions_offered)
        first_row += len(chunk)
    served_rows = 0 if store is None else store.served
    result.positions_served = 0 if store is None else store.positions_served
    if store is not None and sample is not None:
        _verify_served_sample(spec, aliases, config, features, walker, store, sample)
    if store is not None and position_sample is not None and shaper is not None:
        oracle_positions._verify_served_positions(
            shaper,
            kern,
            features,
            (
                (row, store.serve_position(index, row.codepoints)[0])
                for index, row in position_sample.sampled_rows()
            ),
        )
    memo_line = walker.memo_line(label, walker.save_memo())
    result.fresh_memo = walker.fresh_memo
    if memo_line is not None:
        print(memo_line, file=sys.stderr, flush=True)
    print(
        f"[t] oracle {label} {time.perf_counter() - config_started:.2f}s rows={result.rows_compared} first={start_row} positions={result.positions_compared} served={served_rows} positions_served={result.positions_served} promoted={walker.promoted_hits} kept={walker.kept_hits}",
        file=sys.stderr,
        flush=True,
    )
    result.peak_footprint_bytes = peak_footprint_self_bytes()
    return result


@contextmanager
def _collector_held() -> Iterator[None]:
    """Hold Python's cyclic collector off while one row range runs. A range allocates container objects for every row it reads, serves and compares, so with the collector on it runs thousands of collections, and each also walks part of the heap the worker loaded before the range (the spec, the ledger, the store and the shaper). One full collection first frees the cycles the worker's start and the range's own setup left (each range runs in a process of its own, `run_m1._spawn_pool` with `max_tasks_per_child=1`), and `gc.freeze()` then takes the heap that is left out of every collection, so the collector never walks it during this range. A range builds few reference cycles, so holding the collector costs little memory, and the collector, enabled again on exit, reclaims them; the `oracle-shard` row of `make job-costs` checks the range's peak against `artifact_cycle.ORACLE_SHARD_BYTES`. On exit the collector is enabled again only if it was enabled on entry, and the heap is unfrozen only if nothing had frozen it before: when something had, nothing is unfrozen, so the range's own heap stays frozen too."""
    enabled = gc.isenabled()
    frozen = gc.get_freeze_count()
    gc.collect()
    gc.freeze()
    gc.disable()
    try:
        yield
    finally:
        if not frozen:
            gc.unfreeze()
        if enabled:
            gc.enable()


def oracle_config_worker(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    alias_path: Path,
    ledger_path: Path,
    config: str,
    font_path: Path | None,
    kern_sidecar_path: Path | None,
    row_cache: "OracleRowCache | None" = None,
    settle_memo: SettleMemoFile | None = None,
    guard_verdicts: settle.FormationGuard | None = None,
    shard: OracleShard | None = None,
    ship_to: ShipTo | None = None,
    spec_path: Path | None = None,
) -> OracleConfigResult:
    """Run `_compare_config` for one configuration, or one row range when `shard` is given, in a pool worker. The range's audit lines, compressed (`AuditBlocks`), its store records, which its writer builds in memory as one member (a cut configuration's records alone, an uncut one's whole file), and the settle memo windows the range settled fresh (`conform.FreshMemo`) go home in the three shared memory segments `ship_to` names, each piece dropped as it is copied in (`ship`), and the result carries their names and lengths and the fresh windows' counts; without `ship_to`, for a caller that reads only the counts, the range ships none of them, and no range writes a store file itself. When the caller passes no `guard_verdicts`, the worker runs the section 5.7 guard sweep itself, as the conformance sweep's worker does; the overlay configuration's worker forms nothing and skips it. A caller that fans out several ranges runs the sweep once and passes it to every range. The worker opens the row cache itself, through the same `open_row_cache` call the serial path makes, and reads `settle_memo` itself, because a spawned worker inherits no open files. For a range of the pooled oracle, `settle_memo` is `returns_fresh`: the range writes no settle memo part, and the parent's absorb writes its fresh windows into a segment of the shared memo once every range of its configuration has landed and the witness stage has returned. `spec_path`, when given, names the parent's dump of `spec` (`kernel_exec._spec_dump`), which every settle call then reads (`kernel_exec.adopt_spec_dump`), so the worker writes no `spec.json` of its own."""
    shard = OracleShard(config) if shard is None else shard
    if spec_path is not None:
        kernel_exec.adopt_spec_dump(spec, spec_path)
    aliases = load_aliases(alias_path)
    entries, ledger = load_ledger(ledger_path)
    ink_identical_ids = {entry.get("id") for entry in entries if entry.get("ink_identical")}
    features = features_for_config(config)
    overlay = isolated_overlay_active(spec, features)
    shaper = oracle_positions._shaper_for(spec, font_path, overlay)
    kern = oracle_positions.KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    store, writer = open_row_cache(
        row_cache, spec, config, shard.segment, first_row=shard.first_row, stop_row=shard.stop_row
    )
    if guard_verdicts is None and not overlay:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    audit = AuditBlocks()
    with _collector_held(), ExitStack() as stack:
        if writer is not None:
            stack.enter_context(writer)
        result = _compare_config(
            spec,
            subset_tables_dir,
            config,
            features,
            aliases,
            ledger,
            ink_identical_ids,
            shaper,
            kern,
            None if overlay else guard_verdicts,
            audit,
            store=store,
            writer=writer,
            settle_memo=settle_memo,
            first_row=shard.first_row,
            stop_row=shard.stop_row,
            label=shard.label,
        )
    fresh = result.fresh_memo
    if fresh is not None:
        result.fresh_memo = replace(fresh, blocks=())
    if ship_to is not None:
        result.audit_blocks = ship(audit.close(), ship_to.audit)
        if writer is not None and writer.member is not None:
            member, writer.member = [writer.member], None
            result.store_segment = ship(member, ship_to.store)
        if fresh is not None:
            blocks = cast(list[bytes], list(fresh.blocks))
            del fresh
            result.memo_blocks = ship(blocks, ship_to.memo)
    return result


def merge_config_shards(results: Sequence[OracleConfigResult]) -> OracleConfigResult:
    """Combine one configuration's row-range results, given in row order, into one result. Counts and `counts_by_entry` are summed. `unmatched_exemplars` are concatenated and cut to `ORACLE_UNMATCHED_EXEMPLARS`, which gives the same rows an uncut run keeps, because a range's list holds all of its unmatched rows whenever it is shorter than the cap. `notes` are concatenated without repeats, since a missing table is noted once per range. All ranges must report the same `pass_ordinal`, because they read the same store header; a mismatch is a fan-out bug and raises ValueError. `peak_footprint_bytes` is the largest range's."""
    if not results:
        raise ValueError("merge_config_shards folds at least one range")
    configs = {result.config for result in results}
    if len(configs) != 1:
        raise ValueError(f"merge_config_shards folds one configuration's ranges, not {sorted(configs)}")
    ordinals = {result.pass_ordinal for result in results}
    if len(ordinals) != 1:
        raise ValueError(
            f"the ranges of {results[0].config} wrote their store under different pass ordinals ({sorted(ordinals, key=str)}), so no joined store describes them"
        )
    merged = OracleConfigResult(config=results[0].config, pass_ordinal=results[0].pass_ordinal)
    seen_notes: set[str] = set()
    for result in results:
        merged.rows_compared += result.rows_compared
        merged.divergent_rows += result.divergent_rows
        merged.positions_compared += result.positions_compared
        merged.positions_excluded += result.positions_excluded
        merged.positions_served += result.positions_served
        for entry_id, count in result.counts_by_entry.items():
            merged.counts_by_entry[entry_id] = merged.counts_by_entry.get(entry_id, 0) + count
        merged.unmatched_count += result.unmatched_count
        merged.unmatched_exemplars.extend(result.unmatched_exemplars)
        merged.multi_matched_count += result.multi_matched_count
        for note in result.notes:
            if note not in seen_notes:
                seen_notes.add(note)
                merged.notes.append(note)
        merged.peak_footprint_bytes = max(merged.peak_footprint_bytes, result.peak_footprint_bytes)
    del merged.unmatched_exemplars[ORACLE_UNMATCHED_EXEMPLARS:]
    return merged


def merge_oracle_results(results: Iterable[OracleConfigResult]) -> BaselineReport:
    report = BaselineReport()
    for result in results:
        report.rows_compared += result.rows_compared
        report.divergent_rows += result.divergent_rows
        report.positions_compared += result.positions_compared
        report.positions_excluded += result.positions_excluded
        report.positions_served += result.positions_served
        for entry_id, count in result.counts_by_entry.items():
            report.counts_by_entry[entry_id] = report.counts_by_entry.get(entry_id, 0) + count
        report.unmatched_count += result.unmatched_count
        report.unmatched_exemplars.extend(result.unmatched_exemplars)
        report.multi_matched_count += result.multi_matched_count
        report.notes.extend(result.notes)
    return report


def compare_against_baseline(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    alias_path: Path,
    ledger_path: Path,
    configs: Iterable[str] = ORACLE_CONFIGS,
    out_dir: Path | None = None,
    font_path: Path | None = None,
    kern_sidecar_path: Path | None = None,
    row_cache: "OracleRowCache | None" = None,
    settle_memos: Mapping[str, SettleMemoFile] | None = None,
    stores: oracle_cache.StoreSet | None = None,
) -> BaselineReport:
    """The serial oracle, `--jobs 1`'s: each configuration in turn over its whole table, in the audit's order (`audit_key`'s configuration rank) whatever order `configs` names them in when it writes the audit, through the same writers the pooled path's fan-in uses, so the two leave the same heads, parts and materialized bytes. The audit's lines go to the audit's writer (`AuditWriter`) a block at a time as they are written (`_FedBlocks`), and its head is committed only once the last configuration is compared, so a pass that fails partway leaves the standing audit as it was. With `stores` and a row cache that writes stores, each configuration's records, which its writer builds in memory as one member holding the configuration's whole file (`oracle_cache.RowWriter` with `whole`), go to the store set's writer once the configuration is compared (`oracle_cache.StoreSet.put`), and the caller promotes them (`oracle_cache.promote_stores`)."""
    aliases = load_aliases(alias_path)
    entries, ledger = load_ledger(ledger_path)
    ink_identical_ids = {entry.get("id") for entry in entries if entry.get("ink_identical")}
    shapers: dict[bool, "Shaper | IsolatedOverlayShaper | None"] = {}
    kern = oracle_positions.KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    guard_verdicts: settle.FormationGuard | None = None
    started = time.perf_counter()
    writes_stores = stores is not None and row_cache is not None and row_cache.write_dir is not None

    results: list[OracleConfigResult] = []
    with ExitStack() as stack:
        audit: _FedBlocks | None = None
        if out_dir is not None:
            audit = _FedBlocks(stack.enter_context(AuditWriter(out_dir)))
            configs = sorted(configs, key=_audit_config_order)
        if writes_stores:
            assert stores is not None

            @stack.push
            def _abandon_stores(exc_type, exc, traceback) -> None:
                if exc_type is not None:
                    stores.abandon()

        for config in configs:
            features = features_for_config(config)
            overlay = isolated_overlay_active(spec, features)
            if overlay not in shapers:
                shapers[overlay] = oracle_positions._shaper_for(spec, font_path, overlay)
            if not overlay and guard_verdicts is None:
                guard_verdicts = kernel_exec.guard_sweep(spec)
            store, writer = open_row_cache(row_cache, spec, config)
            with ExitStack() as per_config:
                if writer is not None:
                    per_config.enter_context(writer)
                results.append(
                    _compare_config(
                        spec,
                        subset_tables_dir,
                        config,
                        features,
                        aliases,
                        ledger,
                        ink_identical_ids,
                        shapers[overlay],
                        kern,
                        None if overlay else guard_verdicts,
                        audit,
                        store=store,
                        writer=writer,
                        settle_memo=(settle_memos or {}).get(config),
                    )
                )
            if writes_stores and writer is not None and writer.member is not None:
                assert stores is not None
                member, writer.member = writer.member, None
                stores.put(config, [(writer.rows, member)], True, writer.pass_ordinal)
                del member
        if audit is not None:
            audit.flush()
    if writes_stores:
        assert stores is not None
        stores.finish()
    report = merge_oracle_results(results)

    print(
        f"[t] oracle total {time.perf_counter() - started:.2f}s rows_compared={report.rows_compared} positions_compared={report.positions_compared}",
        file=sys.stderr,
        flush=True,
    )
    return report
