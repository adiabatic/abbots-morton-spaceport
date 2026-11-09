"""The baseline oracle (M1-PLAN section 6): compare settlement with the section 13.1 baseline one configuration at a time, and match each divergent row against the divergence ledger (rebuild/m1-divergences.yaml). For the rows the ledger calls ink-identical, also compare the drawn positions with the kern-normalized old positions through the position comparison in rebuild/pipeline/oracle_positions.py.

Nothing here builds a table or a font; everything runs against tables and an M1.otf that are already built. So this module is left out of the stamp a serialized window enumeration carries (`fingerprint.table_code_paths` subtracts `fingerprint.COMPARISON_CODE_MODULES`), and rebuild/test_build_code_closure.py fails if the import graph from any build-side module or from `run_m1.run` reaches it. An edit to `classify_divergence`, a predicate, `compile_ledger`, `_match_compiled`, or the position comparison therefore keeps every enumeration on disk, and `run_m1 --gates-only` re-runs the oracle over them. Like every module `fingerprint.COMPARISON_CODE_MODULES` names, it stays in `fingerprint.pipeline_code_paths`, so an edit here still changes the Stage A `pipeline_code` component, the artifact cycle's run_m1 skip key, and the Stage A record the review corpus's manifest copies.

The rows the oracle classifies are produced in conform.py. `_compare_row` and `_SettledWindowWalk` are the entry points whose import graph `oracle_cache.ORACLE_ROW_CODE_PATHS` must cover, outside the closure test's stop-set (`WIDTH_AND_TELEMETRY_MODULES`), and the record codec (`_cached_verdict`, `_served_verdict`) and `_verify_served_sample` sit beside them. No module under that stamp imports this one, so after a classifier edit every row verdict is still served from the store; rebuild/test_oracle_code_closure.py fails if conform.py's import graph reaches this module. The position comparison's entry module is rebuild/pipeline/oracle_positions.py, and `oracle_cache.POSITION_CODE_PATHS` names its import closure. No module in that closure imports this one either, so a classifier edit also keeps every stored position, and the same test checks that direction. `_compare_config` calls the position comparison through the `oracle_positions` module, not through imported names, so monkeypatching `oracle_positions._position_mismatch` affects both the rows this pass shapes and `_verify_served_positions` (rebuild/test_conform.py relies on this).

`compare_against_baseline` is the serial path. For each configuration it streams the subset table and settles each row, or, given an `OracleRowCache`, serves the row verdict from the previous pass's store and walks only the rows an edit can reach (rebuild/pipeline/oracle_cache.py documents what the keys cover). It compares ligation, junctions, and cells through the alias map and matches each divergent row against the `CompiledLedger` (`compile_ledger`). Rows the ledger calls ink-identical are shaped against M1.otf to compare positions, or have their position verdict served from the same store under the position key.

run_m1's parallel path splits the work into row ranges of one configuration's table (`OracleShard`, planned by `oracle_shard_plan` for the worker count). `oracle_config_worker` runs `_compare_config` over one range in its own process and ships the range's audit rows, compressed (`AuditBlocks`), home in a POSIX shared memory segment, a range of a cut configuration also ships its store segment in another, and a range that settled windows fresh ships them, compressed (`conform.FreshMemo`), in a third (`ship`); its result names them. The parent's `InOrderWriter` writes `divergence-audit.tsv` once, in row order, and each cut configuration's store once its last range has returned (`oracle_cache.join_store_segments`), and `merge_config_shards` sums the ranges' counts. The output is the same as the serial path's, because `_compare_config` addresses every row by its absolute index in the table: the store records, the scheduled re-derivation, and the verification samples all key on it.

For the overlay configuration (ss10), no settlement table produces the new side. Its rows are walked by `conform.IsolatedOverlayWalk`, which returns every letter bare from the registry alone, and shaped by `shapers.IsolatedOverlayShaper`, which uses the copies' `hmtx` advances instead of HarfBuzz. Read-back's isolation check and the conformance sweep over the overlay are what make both valid. So the old font's ss10 rows are compared with an all-bare stream, and no settlement or shaping runs for them.

An M1-only configuration (`conform.M1_ONLY_CONFIGS`) has no baseline table, and the oracle never compares it; its configurations are `conform.ORACLE_CONFIGS`.
"""

from __future__ import annotations

import gc
import itertools
import math
import os
import secrets
import shutil
import sys
import time
import zlib
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from multiprocessing import shared_memory
from pathlib import Path
from typing import Callable, Container, Iterable, Iterator, Mapping, Protocol, Sequence, TextIO, cast

import yaml

from rebuild.pipeline import (
    baseline_subset,
    kernel_exec,
    oracle_cache,
    oracle_positions,
    position_record,
    settle,
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
from rebuild.tools.peak_rss import peak_footprint_self_bytes
from rebuild.validation.rowmodel import Row, iter_rows

# Baseline rows read and walked at a time: the oracle's counterpart of `conform.TEXT_CHUNK`.
ORACLE_ROW_CHUNK = 65536
# How many unmatched rows a result keeps for `oracle_summary.json` to quote. Every unmatched row is written to the audit regardless.
ORACLE_UNMATCHED_EXEMPLARS = 20
# The audit bytes a range holds uncompressed before it compresses them as one block (`AuditBlocks`). It sets only the audit's compressed framing in transit, never the audit's bytes. Deflate's window is 32 KiB, so a block this size compresses nearly as well as the range's whole audit would. A smaller block costs less on both sides: a range grows its pending bytes by reallocation up to the block, once a block, and macOS's allocator keeps each round's freed buffers dirty, so the range pays for them several times over; and the parent inflates one block at a time, so the block sizes each buffer the parent inflates into (both measured in `var/keep/ram-inventory/v4/11a-overrun/`).
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
    """The names of the three POSIX shared memory segments one row range ships its bytes home in: `audit` for its compressed audit lines, `store` for its store segment, and `memo` for the settle memo windows it settled fresh, compressed a block at a time (`conform.FreshMemo`), which the parent hands its configuration's absorb by name. The parent's `InOrderWriter` chooses them (`InOrderWriter.ship_to`), so it can unlink a segment a range made even when the range's result never reaches it, or no absorb maps it."""

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
    """One configuration's oracle counts, or one row range's before `merge_config_shards` combines them, returned to the parent process by `oracle_config_worker`. Unmatched rows are kept as a count plus the first `ORACLE_UNMATCHED_EXEMPLARS` of them, and multi-matched rows as a count alone (`multi_matched_count`). `oracle_summary.json` needs only those, and every divergent row reaches the parent as its audit line instead: `audit_blocks` names the shared memory segment that holds every divergent row's line, a multi-matched row's with its matched ids joined by `+`, compressed a block at a time (`AuditBlocks`), so `divergence-audit.tsv` has them all. `store_segment` names the one that holds a cut configuration's range's store records as one gzip member (`oracle_cache.RowWriter` with no path); it is None when the range writes no store or writes its configuration's whole store itself. `fresh_memo` holds the counts of the settle memo windows the range settled fresh (`conform.FreshMemo` with no blocks), or None when it settled none, and `memo_blocks` names the segment that holds their blocks; `run_m1_gates.run_oracle` takes both off the result as it lands and hands them, with the configuration's other ranges', to its absorb. Any of the segments is None when the range has no such bytes or its caller named no segments (`oracle_config_worker`'s `ship_to`), and the result carries only names, lengths and counts, never the bytes (`Shipped`). The parent's `InOrderWriter` takes the audit and store segments off the result as it lands, and a folded result carries none of them. `positions_served` counts the rows among `positions_compared` whose position verdict came from the store instead of from shaping. `pass_ordinal` is the ordinal the range's store segment was written under, or None when no store was written; the parent writes it into a joined store's header. `peak_footprint_bytes` is the worker's peak footprint (`peak_rss.peak_footprint_self_bytes`), recorded in the pool record that `make job-costs` reads."""

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
    """The row cache settings one oracle run passes to each configuration, so each can read the previous pass's verdicts and stage this pass's. The parent computes the stamps and family keys once, before the first row is compared, and passes them to the workers. A worker that re-read the rune tree could see files edited mid-run, which is the race `run_m1_gates.run_oracle` checks for again at promotion. `position_environment` and `position_keys` are the position store's stamp and keys, computed from the font the same way; without them no position verdict is served. `read_dir` is where a promoted store lives and `write_dir` is where a new one is staged, and either may be None. A pass that may read but not write (`--gates-only`, which recompiles nothing and so may not write a build input) leaves `write_dir` None, which opens its stores read-only and so skips the scheduled re-derivation (`open_row_cache`), and sets a nonzero `rotation`: the pass ordinal advances only when a store is written, so the rotation is what moves the verification samples on such a pass (see `oracle_cache.RowStore`)."""

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
    """Return this configuration's loaded store, holding the records of rows `[first_row, stop_row)`, and the writer for its successor, or Nones without a cache. Both oracle paths call this, so they open the pair the same way. The subset digest is read from the stamp's `subset` line instead of hashing the table again. A range of a cut configuration passes its `segment` and row bounds: it opens a segment writer, which builds its records alone, with no header or trailer, as one gzip member in memory for the range to return (`oracle_cache.RowWriter` with no path), and loads only its own rows. Every range reads the same store header, so all ranges agree on the pass ordinal the joined header records. A cache with no `write_dir` keeps nothing it derives, so it loads its store read-only, which skips the scheduled re-derivation (`oracle_cache.RowStore`)."""
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
            None if segment is not None else oracle_cache.scratch_store_path(cache.write_dir, config),
            stamp,
            subset_digest,
            oracle_cache.next_pass_ordinal(store),
            cache.family_keys,
            cache.position_environment,
            cache.position_keys,
        )
    return store, writer


def unaliased_subset_names(subset_dir: Path, alias_path: Path) -> dict[str, list[str]]:
    """Map every old glyph name in the subset baseline rows that neither the alias map nor `BOUNDARY_GLYPH_NAMES` resolves to the sorted configurations it appears in. A missing alias does not always fail loudly: a ligation row never reaches the per-glyph alias check in `_compare_row`, so it is counted under a ledger class as if the name were understood. run_m1 therefore stops before building while this is non-empty. A `pending` alias passes this check but still counts as unaliased in the comparison. The names come from the sidecar the refilter writes (`baseline_subset.read_subset_names`), which changes only when the tables are refiltered, so this check is cheap enough to run on the `--gates-only` path too."""
    known = set(load_aliases(alias_path)) | BOUNDARY_GLYPH_NAMES
    missing: dict[str, set[str]] = {}
    for config, names in baseline_subset.read_subset_names(subset_dir).items():
        for name in names:
            if name not in known:
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
    if tags & {"-en-ext-1:qsDay", "-en-ext-1:qsDay_qsUtter"}:
        return "day-baseline-entry-extension-dropped"
    if tags & {"-en-ext-1:qsVie", "-en-ext-1:qsVie_qsUtter"}:
        return "vie-baseline-entry-extension-dropped"
    if "-ex-con-1" in tags and tags <= {"-ex-con-1", "+en-trim-1"} and "E65A:E67B" in row.codepoints:
        # The baseline ·See·Out fusion names the old pull-back differently. The old font's ex-con-1 tucks ·Out into ·See's whole tail (only the anchor moves). The runes keep the tail's anchor at its convention position and pull back the raked redraw's foot instead. The combined ink is identical and only the glyph names differ. The subset test keeps out any row where ink also moved elsewhere.
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


def oracle_audit_scratch(out_dir: Path) -> Path:
    """Return this run's scratch directory for the staged audit, the staged stores (`oracle_cache.scratch_store_path`) and the in-order writer's record of its shared memory segments' names (`SEGMENT_RECORD`), beside `divergence-audit.tsv`. The pid in the name lets two runs share an `out_dir` (a `--gates-only` pass during a cycle, for example) without mixing or deleting each other's files. The name matches neither `artifact_cycle.M1_ARTIFACT_NAMES` nor any glob over the tables, so nothing a killed run leaves here is taken for an artifact."""
    return Path(out_dir) / f"divergence-audit.parts.{os.getpid()}"


@contextmanager
def _staged_oracle_audit(out_dir: Path) -> Iterator[Path]:
    """Yield the staging path for this run's audit, and move it to `divergence-audit.tsv` only when the block exits normally. The audit is a fingerprinted artifact, and a truncated one would read as a new build with fewer units while every downstream gate passes. So an oracle killed partway (Ctrl-C, a timeout, a full disk) must leave the previous complete audit in place. The staging file is in this run's scratch directory, so the same cleanup deletes it with the run's other scratch files."""
    out = Path(out_dir)
    scratch = oracle_audit_scratch(out)
    scratch.mkdir(parents=True, exist_ok=True)
    staging = scratch / "divergence-audit.tsv"
    try:
        yield staging
        staging.replace(out / "divergence-audit.tsv")
    finally:
        with suppress(OSError):
            staging.unlink(missing_ok=True)
            scratch.rmdir()


class AuditSink(Protocol):
    """What `_compare_config` writes audit lines to: the serial path's staged file, or a range's `AuditBlocks`."""

    def write(self, text: str, /) -> int: ...


class AuditBlocks:
    """One row range's audit lines, held in memory until the range ships them home (`ship`). They are compressed with zlib at level 1 a block at a time, each block a whole zlib stream that ends at a line's end once `ORACLE_AUDIT_BLOCK` bytes have gathered, so the range holds at most one block uncompressed and the parent inflates one block at a time (`InOrderWriter`). The bytes are the lines' UTF-8, as the serial path's staged file writes them."""

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
    """Copy `pieces` end to end into a new POSIX shared memory segment called `name`, dropping each from the list once it is copied, close this process's mapping, and return what names them, or None when there are no bytes, for which no segment is made. Making the segment registers it with the resource tracker, which a spawned pool worker shares with the parent that started it, and which unlinks any segment still registered once the parent and every worker have exited. Whoever maps the segment unlinks it as it maps it, and unregisters it in doing so (`_Mapped`: the parent's `InOrderWriter.land`, or a settle memo absorb's worker, `run_m1_gates._absorb_memo`), and the parent unlinks any segment it named and nobody mapped as it exits (`InOrderWriter.__exit__`, `run_m1_gates._SweepSegments`)."""
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


def _unlink_recorded_segments(scratch: Path) -> None:
    """Unlink every shared memory segment a dead run's writer named, from the token and range count it recorded in that run's scratch directory (`InOrderWriter`, `SEGMENT_RECORD`). A segment already gone, or a record that cannot be read, leaves nothing to do, and nothing here raises."""
    try:
        token, count = (scratch / SEGMENT_RECORD).read_text().split()
        positions = range(int(count))
    except OSError, ValueError:
        return
    for position in positions:
        names = _segment_names(token, position)
        for name in (names.audit, names.store, names.memo):
            with suppress(OSError, ValueError):
                _unlink_if_made(name, track=False)


class _Mapped:
    """A shipped segment mapped and unlinked at once, so that its name never outlives the mapping, with its pieces as views: by the parent's in-order writer, or by a settle memo absorb's worker. Attaching registers the name with the resource tracker again, which holds a name once, and unlinking unregisters it, so a segment mapped here leaves the tracker nothing to clean up. Its pages enter the mapping process's footprint as it reads them and leave at `close`."""

    def __init__(self, shipped: Shipped) -> None:
        self._segment = shared_memory.SharedMemory(shipped.name)
        self._segment.unlink()
        buffer = self._segment.buf
        assert buffer is not None
        self.size = sum(shipped.lengths)
        bounds = list(itertools.accumulate(shipped.lengths, initial=0))
        self.pieces = [buffer[first:stop] for first, stop in itertools.pairwise(bounds)]

    def close(self) -> None:
        for piece in self.pieces:
            piece.release()
        self.pieces = []
        self._segment.close()


class InOrderWriter:
    """The pooled oracle's fan-in, in `run_m1`'s parent: it writes `divergence-audit.tsv` once, in `configs` order and in row order within each configuration, from the lines each range ships home (`OracleConfigResult.audit_blocks`), and writes each cut configuration's store once, from the segments its ranges ship, when its last range lands (`oracle_cache.join_store_segments`). A range ships its bytes in the POSIX shared memory segments the writer names for it (`ship_to`, `ship`), and its result carries only their names and lengths. The writer names each range's settle memo segment too, which it never maps: `run_m1_gates.run_oracle` hands it to the configuration's absorb, whose worker maps it. `land` takes each range's result as its future resolves, in any order, takes the names off the result, so neither it nor its future keeps them, and maps each segment and unlinks its name at once. A range's lines are written, inflated a block at a time from the mapping, with every later range's that has already landed, once every range before it is written, and each range's mapping is closed once its lines are written. The writer therefore holds a landed range's compressed lines only until the ranges before it are written: at most the whole audit compressed, when the first range lands last. `oracle_shard_plan` starts the heaviest ranges first, so the small ranges its cuts leave at a configuration's ends start and land last, and much of the audit can wait for one of them. It holds a cut configuration's segments only until its last range lands, joins them from the mappings and closes them once joined, so at worst it holds every cut configuration's segments at once. Together it holds at most the whole audit compressed and every cut configuration's segments, which are the compressed stores the pass writes.

    What it holds sits in shared memory, which no process's footprint counts while it waits: a range closes its mapping once it has copied its bytes in, and the writer's mapping touches a page only as the writer reads it, so a segment's pages count in the parent's footprint only from the inflate or the join that reads them to the close that follows (`Shipped`). The peak footprint a bare run records under `run_m1.PARENT_POOL_UNIT`, which the run-m1-parent row of `make job-costs` reports, therefore leaves out what waits. So the writer counts it itself: `held_peak_bytes` is the most it held mapped at once, and `landing_seconds` the time `land` took over every range; `run_m1_gates.run_oracle` prints both on its `[t] oracle_fan_in` line.

    The audit goes through a staging file in the run's scratch directory (`_staged_oracle_audit`) and is moved into place only when the block exits normally and two checks pass, so a partial audit never replaces the standing one: every planned range must have landed, and the lines written must equal the landed ranges' own `divergent_rows` total; either failure raises ValueError. A store is staged only when `row_cache` writes one, under its `write_dir`. A cut configuration one of whose ranges returned no segment, or whose ranges disagree on the pass ordinal (which `merge_config_shards` refuses), stages no store, so `oracle_cache.promote_stores` promotes none. On every exit, normal or not, the writer closes each mapping it still holds and unlinks every segment it named for a range that is still there, a settle memo segment no absorb mapped among them, so a run that fails or is interrupted while this block unwinds leaves no segment behind, and a run that maps every segment leaves the resource tracker nothing to warn about. A landing maps its range's store segment only after writing the lines now due, so a landing whose write fails (a full disk) leaves that segment under its name for the exit to unlink, not in a mapping nothing holds. The writer records its token and range count in the run's scratch directory as it enters (`SEGMENT_RECORD`) and deletes the record once its exit has unlinked every name, so after a SIGKILL of the whole process group, which skips the exit, the next oracle run over the same `out_dir` unlinks the segments the dead run left (`discard_oracle_audit_scratch`).
    """

    def __init__(
        self,
        out_dir: Path,
        shards: Iterable[OracleShard],
        row_cache: OracleRowCache | None = None,
        configs: Sequence[str] = ORACLE_CONFIGS,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._order = sorted(shards, key=lambda shard: (configs.index(shard.config), shard.first_row))
        self._positions = {shard: position for position, shard in enumerate(self._order)}
        self._row_cache = row_cache
        self._token = secrets.token_hex(6)
        self._waiting: dict[int, _Mapped | None] = {}
        self._written = 0
        self._rows = 0
        self._counted = 0
        self._segments: dict[str, dict[int, tuple[int, _Mapped | None, int | None]]] = {}
        self._held = 0
        self.held_peak_bytes = 0
        self.landing_seconds = 0.0
        self._staged = ExitStack()

    def __enter__(self) -> "InOrderWriter":
        with ExitStack() as stack:
            staging = stack.enter_context(_staged_oracle_audit(self._out_dir))
            self._record = staging.parent / SEGMENT_RECORD
            self._record.write_text(f"{self._token} {len(self._order)}\n")
            self._target = stack.enter_context(staging.open("wb"))
            self._target.write((ORACLE_AUDIT_HEADER + "\n").encode("utf-8"))
            self._staged = stack.pop_all()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            try:
                self._release()
            finally:
                self._staged.__exit__(exc_type, exc, traceback)
            return
        with self._staged:
            missing = [
                shard.label
                for position, shard in enumerate(self._order)
                if position >= self._written and position not in self._waiting
            ]
            self._release()
            if missing:
                raise ValueError(
                    f"the oracle has no audit rows from {', '.join(missing)} — divergence-audit.tsv is untouched"
                )
            if self._rows != self._counted:
                raise ValueError(
                    f"the oracle's ranges returned {self._rows} audit rows where they counted {self._counted} divergent — divergence-audit.tsv is untouched"
                )

    def ship_to(self, shard: OracleShard) -> ShipTo:
        """Return the names of the segments `shard` ships its bytes home in (`ShipTo`): the writer's own random token and the range's place in the audit's order, so two writers' names never meet and each range's are its own, in fewer than the 31 bytes macOS allows a segment's name."""
        return _segment_names(self._token, self._positions[shard])

    def land(self, shard: OracleShard, result: OracleConfigResult) -> None:
        """Take one range's result: write its lines and every waiting range's that now follows in order, and stage its configuration's store when this is the last of its ranges to land."""
        started = time.perf_counter()
        self._land(shard, result)
        self.landing_seconds += time.perf_counter() - started

    def _land(self, shard: OracleShard, result: OracleConfigResult) -> None:
        position = self._positions.get(shard)
        if position is None or position < self._written or position in self._waiting:
            raise ValueError(f"the oracle's fan-in is not waiting for {shard.label}")
        blocks, result.audit_blocks = result.audit_blocks, None
        segment, result.store_segment = result.store_segment, None
        self._counted += result.divergent_rows
        self._waiting[position] = self._map(blocks)
        while self._written in self._waiting:
            mapped = self._waiting.pop(self._written)
            try:
                for block in () if mapped is None else mapped.pieces:
                    lines = zlib.decompress(block)
                    self._rows += lines.count(b"\n")
                    self._target.write(lines)
            finally:
                self._close(mapped)
            self._written += 1
        member = self._map(segment)
        cache = self._row_cache
        if shard.of == 1 or cache is None or cache.write_dir is None:
            self._close(member)
            return
        held = self._segments.setdefault(shard.config, {})
        held[shard.index] = (result.rows_compared, member, result.pass_ordinal)
        if len(held) < shard.of:
            return
        del self._segments[shard.config]
        ranges = [held[index] for index in range(shard.of)]
        try:
            members = [(rows, mapped.pieces[0]) for rows, mapped, _ordinal in ranges if mapped is not None]
            ordinal = ranges[0][2]
            if ordinal is None or len(members) < shard.of or any(other != ordinal for *_, other in ranges):
                return
            stamp = cache.environment[shard.config]
            oracle_cache.join_store_segments(
                cache.write_dir,
                shard.config,
                members,
                stamp,
                stamp.labels["subset"],
                ordinal,
                cache.family_keys,
                cache.position_environment,
                cache.position_keys,
            )
        finally:
            for _rows, mapped, _ordinal in ranges:
                self._close(mapped)

    def _map(self, shipped: Shipped | None) -> _Mapped | None:
        if shipped is None:
            return None
        mapped = _Mapped(shipped)
        self._held += mapped.size
        self.held_peak_bytes = max(self.held_peak_bytes, self._held)
        return mapped

    def _close(self, mapped: _Mapped | None) -> None:
        if mapped is not None:
            mapped.close()
            self._held -= mapped.size

    def _release(self) -> None:
        """Close every mapping still held (a range's lines waiting on an earlier range, a cut configuration's segments short of its last range), then unlink every segment named for one of the writer's ranges that is still there: one a range made whose result the writer never took, or a settle memo segment no absorb mapped. Once every name is unlinked it deletes the record of them (`SEGMENT_RECORD`)."""
        held = [
            *self._waiting.values(),
            *(mapped for ranges in self._segments.values() for _rows, mapped, _ordinal in ranges.values()),
        ]
        self._waiting = {}
        self._segments = {}
        for mapped in held:
            self._close(mapped)
        for shard in self._order:
            names = self.ship_to(shard)
            _unlink_if_made(names.audit)
            _unlink_if_made(names.store)
            _unlink_if_made(names.memo)
        self._record.unlink(missing_ok=True)


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
    """Delete this run's scratch directory, and any earlier run's whose process has exited, since a killed run skips the `finally` that would have deleted its staged audit and stores, and they can take as much disk as the audit. Before deleting an earlier run's directory it unlinks the shared memory segments that run's writer recorded there (`_unlink_recorded_segments`): a SIGKILL of that run's whole process group kills its resource tracker too, and macOS lists no POSIX shared memory, so those names are the only way to find what it left. A directory whose pid is still running belongs to another oracle and is kept. Nothing here raises, so calling it in a `finally` cannot replace the exception being handled."""
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
    """Run `_compare_config` for one configuration, or one row range when `shard` is given, in a pool worker. The range's audit lines, compressed (`AuditBlocks`), a cut configuration's range's store segment, which its writer builds in memory, and the settle memo windows the range settled fresh (`conform.FreshMemo`) go home in the three shared memory segments `ship_to` names, each piece dropped as it is copied in (`ship`), and the result carries their names and lengths and the fresh windows' counts; without `ship_to`, for a caller that reads only the counts, the range ships none of them. An uncut configuration's range writes its whole store under the row cache's `write_dir` itself. When the caller passes no `guard_verdicts`, the worker runs the section 5.7 guard sweep itself, as the conformance sweep's worker does; the overlay configuration's worker forms nothing and skips it. A caller that fans out several ranges runs the sweep once and passes it to every range. The worker opens the row cache itself, through the same `open_row_cache` call the serial path makes, and reads `settle_memo` itself, because a spawned worker inherits no open files. For a range of the pooled oracle, `settle_memo` is `returns_fresh`: the range writes no settle memo part, and the parent's absorb writes its fresh windows into a segment of the shared memo once every range of its configuration has landed and the witness stage has returned. `spec_path`, when given, names the parent's dump of `spec` (`kernel_exec._spec_dump`), which every settle call then reads (`kernel_exec.adopt_spec_dump`), so the worker writes no `spec.json` of its own."""
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
) -> BaselineReport:
    aliases = load_aliases(alias_path)
    entries, ledger = load_ledger(ledger_path)
    ink_identical_ids = {entry.get("id") for entry in entries if entry.get("ink_identical")}
    shapers: dict[bool, "Shaper | IsolatedOverlayShaper | None"] = {}
    kern = oracle_positions.KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    guard_verdicts: settle.FormationGuard | None = None
    started = time.perf_counter()

    results: list[OracleConfigResult] = []
    with ExitStack() as stack:
        audit: TextIO | None = None
        if out_dir is not None:
            staging = stack.enter_context(_staged_oracle_audit(out_dir))
            audit = stack.enter_context(staging.open("w", encoding="utf-8", newline="\n"))
            audit.write(ORACLE_AUDIT_HEADER + "\n")
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
    report = merge_oracle_results(results)

    print(
        f"[t] oracle total {time.perf_counter() - started:.2f}s rows_compared={report.rows_compared} positions_compared={report.positions_compared}",
        file=sys.stderr,
        flush=True,
    )
    return report
