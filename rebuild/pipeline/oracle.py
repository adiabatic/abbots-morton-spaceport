"""The baseline oracle (M1-PLAN section 6): compare settlement with the section 13.1 baseline one configuration at a time, and match each divergent row against the divergence ledger (rebuild/m1-divergences.yaml). For the rows the ledger calls ink-identical, also compare the drawn positions with the kern-normalized old positions through the position channel in rebuild/pipeline/oracle_positions.py.

Nothing here builds a table or a font; everything runs against tables and an M1.otf that are already built. So this module is left out of the stamp a serialized window enumeration carries (`fingerprint.table_code_paths` subtracts `fingerprint.COMPARISON_CODE_MODULES`), and rebuild/test_build_code_closure.py fails if the import graph from any build-side module or from `run_m1.run` reaches it. An edit to `classify_divergence`, a predicate, `compile_ledger`, `_match_compiled`, or the position channel therefore keeps every enumeration on disk, and `run_m1 --gates-only` re-runs the oracle over them. Both files stay in `fingerprint.pipeline_code_paths`, so an edit here still changes the Stage A `pipeline_code` component, the artifact cycle's run_m1 skip key, and the Stage A record the review surface's manifest copies.

The rows the oracle classifies are produced in conform.py. `_compare_row` and `_SettledWindowWalk` are the entry points whose import graph `oracle_cache.ORACLE_ROW_CODE_PATHS` must cover, and the record codec (`_cached_verdict`, `_served_verdict`) and `_verify_served_sample` sit beside them. No module under that stamp imports this one, so after a classifier edit every row verdict is still served from the store; rebuild/test_oracle_code_closure.py fails if conform.py's import graph reaches this module. The position channel is rebuild/pipeline/oracle_positions.py, the only module `oracle_cache.POSITION_CODE_PATHS` names. It never imports this module either, so a classifier edit also keeps every stored position, and the same test checks that direction. `_compare_config` calls the channel through the `oracle_positions` module, not through imported names, so monkeypatching `oracle_positions._position_drift` affects both the rows this pass shapes and `_verify_served_positions` (rebuild/test_conform.py relies on this).

`compare_against_baseline` is the serial path. For each configuration it streams the subset table and settles each row, or, given an `OracleRowCache`, serves the row verdict from the previous pass's store and walks only the rows an edit can reach (rebuild/pipeline/oracle_cache.py documents what the keys cover). It compares ligation, seams, and cells through the alias map and matches each divergent row against the `CompiledLedger` (`compile_ledger`). Rows the ledger calls ink-identical are shaped against M1.otf to compare positions, or have their position verdict served from the same store under the position key.

run_m1's parallel path splits the work into row ranges of one configuration's table (`OracleShard`, planned by `oracle_shard_plan` for the worker count). `oracle_config_worker` runs `_compare_config` over one range in its own process and writes its own audit segment under `oracle_audit_scratch` and its own store segment. `join_oracle_audit` and `oracle_cache.join_store_segments` join those in row order, and `merge_config_shards` sums the ranges' counts. The output is the same as the serial path's, because `_compare_config` addresses every row by its absolute index in the table: the store records, the renewal slice, and the verification samples all key on it.

For the overlay configuration (ss10), no settlement table produces the new side. Its rows are walked by `conform.IsolatedOverlayWalk`, which returns every letter bare from the registry alone, and shaped by `conform.IsolatedOverlayShaper`, which uses the twins' `hmtx` advances instead of HarfBuzz. Read-back's isolation check and the belt's overlay sweep are what make both valid. So the old font's ss10 rows are compared with an all-bare stream, and no settlement or shaping runs for them.
"""

from __future__ import annotations

import itertools
import math
import os
import shutil
import sys
import time
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Container, Iterable, Iterator, Mapping, Sequence, TextIO

import yaml

from rebuild.pipeline import baseline_subset, kernel_exec, oracle_cache, oracle_positions, settle
from rebuild.pipeline.conform import (
    ACCEPTANCE_CONFIGS,
    OVERLAY_CONFIGS,
    DivergentRow,
    IsolatedOverlayShaper,
    IsolatedOverlayWalk,
    SettleMemoFile,
    Shaper,
    _cached_verdict,
    _compare_row,
    _served_verdict,
    _SettledWindowWalk,
    _verify_served_sample,
)
from rebuild.pipeline.labels import BOUNDARY_GLYPH_NAMES, features_for_config, load_alias_map
from rebuild.pipeline.model import ResolvedSpec, isolated_overlay_active
from rebuild.tools.peak_rss import peak_rss_self_bytes
from rebuild.validation.rowmodel import iter_rows

# Baseline rows read and walked at a time: the oracle's counterpart of `conform.TEXT_CHUNK`.
ORACLE_ROW_CHUNK = 65536
# How many unmatched rows a result keeps for `oracle_summary.json` to quote. Every unmatched row is written to the audit regardless.
ORACLE_UNMATCHED_EXEMPLARS = 20
# The time an overlay-configuration row costs relative to a settlement-configuration row, which `oracle_shard_plan` weights row counts by. The overlay's walk reads the registry and its shaper reads `hmtx`, so its worker takes about half the wall-clock time over the same rows (measured from the `[t] oracle ss10` and `[t] oracle default` lines in the cycle journal).
OVERLAY_ROW_COST = 0.5


@dataclass(frozen=True)
class OracleShard:
    """Rows `[first_row, stop_row)` of one configuration's subset table, the `index`-th of the `of` ranges the configuration is cut into. `stop_row` None is the table's end. With `of == 1` the range is the whole table, and its audit segment, store, and `[t]` label are named as an unsharded run names them."""

    config: str
    first_row: int = 0
    stop_row: int | None = None
    index: int = 0
    of: int = 1

    @property
    def segment(self) -> int | None:
        """The suffix this range's scratch files carry, or None for the one range of an uncut configuration."""
        return None if self.of == 1 else self.index

    @property
    def label(self) -> str:
        """The range's `[t]` label: the configuration alone when it is uncut, else the configuration and `k/n`."""
        return self.config if self.of == 1 else f"{self.config} {self.index + 1}/{self.of}"


def oracle_shard_plan(
    jobs: int, rows_by_config: Mapping[str, int | None], configs: Iterable[str] = ACCEPTANCE_CONFIGS
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
        0  # divergent rows not sent through the position channel: a seam or ligation divergence, or no single ink-identical ledger match
    )
    positions_served: int = 0
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched_count: int = 0
    unmatched_exemplars: list[DivergentRow] = field(default_factory=list)
    multi_matched_count: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class OracleConfigResult:
    """One configuration's oracle counts, or one row range's before `merge_config_shards` combines them, returned to the parent process by `oracle_config_worker`. Unmatched rows are kept as a count plus the first `ORACLE_UNMATCHED_EXEMPLARS` of them, and multi-matched rows as a count alone (`multi_matched_count`). `oracle_summary.json` needs only those, and pickling every such `DivergentRow` to the parent would cost memory in proportion to the audit. The worker has already written every row to its audit shard, a multi-matched row with its matched ids joined by `+`, so `divergence-audit.tsv` has them all. `positions_served` counts the rows among `positions_compared` whose position verdict came from the store instead of from shaping. `pass_ordinal` is the ordinal the range's store segment was written under, or None when no store was written; the parent writes it into a joined store's header. `peak_rss_bytes` is the worker's peak memory, recorded in the pool record that `make job-costs` reads."""

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
    peak_rss_bytes: int = 0


@dataclass(frozen=True)
class OracleRowCache:
    """The row cache settings one oracle run passes to each configuration, so each can read the previous pass's verdicts and stage this pass's. The parent computes the stamps and family keys once, before the first row is compared, and passes them to the workers. A worker that re-read the rune tree could see files edited mid-run, which is the race `run_oracle` checks for again at promotion. `position_environment` and `position_keys` are the position store's stamp and keys, computed from the font the same way; without them no position verdict is served. `read_dir` is where a promoted store lives and `write_dir` is where a new one is staged, and either may be None. A pass that may read but not write (`--gates-only`, which recompiles nothing and so may not write a build input) leaves `write_dir` None and sets a nonzero `rotation`: the pass ordinal advances only when a store is written, so the rotation is what moves the renewal slice and the verification sample on such a pass (see `oracle_cache.RowStore`)."""

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
    """Return this configuration's loaded store, holding the records of rows `[first_row, stop_row)`, and the writer for its successor, or Nones without a cache. Both oracle paths call this, so they open the pair the same way. The subset digest is read from the stamp's `subset` line instead of hashing the table again. A range of a cut configuration passes its `segment` and row bounds: it stages a segment writer (records only, with no header or trailer) for `oracle_cache.join_store_segments` to join, and loads only its own rows. Every range reads the same store header, so all ranges agree on the pass ordinal the joined header records."""
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
        )
    writer = None
    if cache.write_dir is not None:
        writer = oracle_cache.RowWriter(
            oracle_cache.scratch_store_path(cache.write_dir, config, segment),
            stamp,
            subset_digest,
            oracle_cache.next_pass_ordinal(store),
            cache.family_keys,
            cache.position_environment,
            cache.position_keys,
            segment=segment is not None,
        )
    return store, writer


def unaliased_subset_names(subset_dir: Path, alias_path: Path) -> dict[str, list[str]]:
    """Map every old glyph name in the subset baseline rows that neither the alias map nor `BOUNDARY_GLYPH_NAMES` resolves to the sorted configurations it appears in. A missing alias does not always fail loudly: a ligation row never reaches the per-glyph alias check in `_compare_row`, so it is counted under a ledger class as if the name were understood. run_m1 therefore stops before building while this is non-empty. A `pending` alias passes this check but still counts as unaliased in the comparison. The names come from the sidecar the refilter writes (`baseline_subset.read_subset_names`), which changes only when the tables are refiltered, so this check is cheap enough to run on the `--gates-only` path too."""
    known = set(load_alias_map(alias_path)) | BOUNDARY_GLYPH_NAMES
    missing: dict[str, set[str]] = {}
    for config, names in baseline_subset.read_subset_names(subset_dir).items():
        for name in names:
            if name not in known:
                missing.setdefault(name, set()).add(config)
    return {name: sorted(configs) for name, configs in sorted(missing.items())}


def classify_divergence(row: DivergentRow) -> str | None:
    """Return the one ledger class for a divergent row, chosen from its phenomenon set (which `_compare_row` computes through the alias map), or None when no class applies. The order of the checks below is the precedence, and the ledger's header comment summarizes it. A row with no class can still match a function predicate or an unconditional ledger entry; otherwise it is unmatched and waits for a verdict on the review surface."""
    phenomena = set(row.phenomena)
    if not phenomena or any(item.startswith("unaliased") for item in phenomena):
        return None
    if any(item.startswith("position") for item in phenomena):
        # A cell-grain class claims the ink is identical, and the position channel is the test of that claim, so a row with position drift must not take one. Such rows are left to the function predicates (`kern_channel_out_of_scope`, `may_ligature_seam_loosened`).
        return None
    if {"0020", "200C"} & set(row.codepoints.split(":")):
        # Design section 3.4: the new font renders each segment of a window split by a space or ZWNJ the same as that segment alone, and the belt's split-buffer check verifies this on every build. So a boundary row can diverge from the baseline only where the old font was inconsistent across the boundary, and every divergence inside a segment also appears on that segment's own row. Boundary rows need no review of their own and take this class ahead of every other.
        return "boundary-echo"
    if row.config in OVERLAY_CONFIGS:
        # Under ss10 both fonts render every letter isolated, with no join and no ligature (the old font through its anchor-free `.ss10` twins, the rebuild through its pre-empt), so any other ss10 divergence is a regression and waits for review. Without this, a namer-dot ss10 row that ligates would take marker-staging-ligature-formation.
        return None
    if "ligation" in phenomena:
        if "E67B:E652" in row.codepoints and "ss03" in row.config:
            return "ss03-out-tea-ligature-kept"
        if "E652:E679" in row.codepoints and ("200C" in row.codepoints or "ss03" in row.config):
            return "marker-staging-ligature-formation"
        # The old font also forms qsDay_qsUtter in every configuration, so only the windows after a ZWNJ or the namer dot diverge: there the old font renames the lead to its .noentry form or leaves a bare name, and never forms the ligature. This is the same staging phenomenon as ·Tea·Oy above.
        if "E653:E67A" in row.codepoints and ("200C" in row.codepoints or "00B7" in row.codepoints):
            return "marker-staging-ligature-formation"
        return None
    gains = {item for item in phenomena if item.startswith("seam-gain:")}
    if "seam-moved" in phenomena:
        # The old font drew a letter after a ZWNJ with a .noentry variant that joined its follower at one height. The new model settles that letter as word-initial, the same as after a space, and the join is at another height. This class applies only when the move is the row's only seam change. Any other row with a moved seam gets no class, including one that also gains or loses a seam.
        if "old-noentry" in phenomena and not gains and "seam-loss" not in phenomena:
            return "zwnj-word-initial-seam-moved"
        return None
    if "seam-loss" in phenomena:
        if gains:
            return "regrouping-floor-drift"
        return None
    if gains:
        gain_runes = {item.split(":", 1)[1] for item in gains}
        unentered_it_gain = "seam-gain-unentered:qsIt" in phenomena
        if "old-noentry" in phenomena:
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
    if "+en-ext-1" in phenomena:
        return "halves-entry-extension-restored"
    if phenomena & {"-en-ext-1:same-seam", "-en-ext-2:same-seam"}:
        return "same-seam-extension-non-summing"
    if "-en-ext-1:qsMay" in phenomena:
        return "may-baseline-entry-extension-dropped"
    if "-en-ext-1:qsNo" in phenomena:
        return "no-xheight-entry-extension-dropped"
    if phenomena & {"-en-ext-1:qsDay", "-en-ext-1:qsDay_qsUtter"}:
        return "day-baseline-entry-extension-dropped"
    if phenomena & {"-en-ext-1:qsVie", "-en-ext-1:qsVie_qsUtter"}:
        return "vie-baseline-entry-extension-dropped"
    if (
        "-ex-con-1" in phenomena
        and phenomena <= {"-ex-con-1", "+en-trim-1"}
        and "E65A:E67B" in row.codepoints
    ):
        # The grounded ·See·Out fusion names the old pull-back differently. The old font's ex-con-1 tucks ·Out into ·See's whole tail (only the anchor moves). The runes keep the tail's anchor at its convention position and pull back the raked redraw's foot instead. The combined ink is identical and only the glyph names differ. The subset test keeps out any row where ink also moved elsewhere.
        return "see-out-fusion-respelled"
    if (
        "+ex-ext-2" in phenomena
        and phenomena <= {"+ex-ext-2", "-ex-ext-1", "-en-ext-2", "exit-dropped"}
        and "E665:E65D" in row.codepoints
    ):
        # At the ·May·J'ai seam, qsMay's single by-2 exit record replaces the old font's split extension (·May's exit by 1, ·J'ai's entry by 2) in every follower context. The -en-ext-2 token is the ·J'ai side of the same change: ·J'ai's alias keeps the old glyph's en-ext-2, which the new cell lacks. The subset test keeps out any row where unrelated ink also moved.
        return "may-jai-extension-consolidated"
    if (
        phenomena
        and phenomena <= {"+en-con-1", "+en-con-2"}
        and ("E65D" in row.codepoints or "E65F" in row.codepoints)
    ):
        # The old font's exit contractions before ·J'ai are tucks: the left letter keeps its ink and only its anchor moves in, overlapping the follower. M1 draws the same result as ·J'ai's own entry contraction: the crown drops the overlapped columns and abuts instead. The combined drawing, every origin, and every advance are unchanged, and only ·J'ai's cell name gains the con token. The unentered half-·Tea exit tuck before ·Jay is the same case on the same crown shape, drawn as ·Jay's entry contraction. The subset test keeps out any row where ink also moved elsewhere.
        return "jai-entry-contraction-respelled"
    if (
        phenomena == {"+en-con-1", "-en-trim-1"}
        and "E652:E65B" in row.codepoints
        and any(
            old_left == "qsTea.half.ex-y5.ex-con-1"
            and old_right == "qsZoo.en-trim-1"
            and new_left == "qsTea/half/None/x-height/"
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
        # ·Zoo's entry contraction places the same crown as the old ·Tea exit tuck plus ·Zoo entry trim. The unrelated ·It·Roe redraw changes ink without moving origins or advances, so the position channel cannot catch it, and this class excludes that old pair explicitly.
        return "zoo-entry-contraction-respelled"
    # A row with these tokens has an ink change that no class covers, so it must get no class instead of reaching the name-grain classes below.
    if any(item.startswith("+ex-bind-") for item in phenomena) or "-ex-ext-1" in phenomena:
        return None
    if "+locked" in phenomena or "old-noentry" in phenomena:
        return "zwnj-word-initial-unification"
    if "entry-dropped" in phenomena or "exit-dropped" in phenomena:
        return "dangling-anchor-dropped"
    if phenomena & {"entry-added", "exit-added", "entry-moved", "exit-moved", "stance"}:
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
    "boundary-echo",
    "ss03-out-tea-ligature-kept",
    "marker-staging-ligature-formation",
    "regrouping-floor-drift",
    "zwnj-word-initial-seam-moved",
    "zwnj-follower-exit-restored",
    "pre-ligature-cleanup-regularized",
    "ss03-chain-join-gains",
    "entered-it-baseline-join-gain",
    "pea-chain-regularized",
    "halves-entry-extension-restored",
    "same-seam-extension-non-summing",
    "may-baseline-entry-extension-dropped",
    "no-xheight-entry-extension-dropped",
    "day-baseline-entry-extension-dropped",
    "vie-baseline-entry-extension-dropped",
    "zwnj-word-initial-unification",
    "dangling-anchor-dropped",
    "bare-name-live-join",
    "see-out-fusion-respelled",
    "may-jai-extension-consolidated",
    "jai-entry-contraction-respelled",
    "zoo-entry-contraction-respelled",
):
    CLASS_PREDICATE_IDS[_class_id.replace("-", "_")] = _class_id
    PREDICATES[_class_id.replace("-", "_")] = _class_predicate(_class_id)


@predicate("kern_channel_out_of_scope")
def _kern_channel_out_of_scope(row: DivergentRow) -> bool:
    """Match position-only rows whose drift the position channel marked kern-attributable (`oracle_positions._position_drift` sets this when every drift comes after a slot whose old advance carries a nonzero sidecar kern or that sits next to a ZWNJ). Other position drift is not matched here, so it stays unmatched for review unless another predicate matches it."""
    return row.kinds == ("position",) and "position-kern-attributable" in row.phenomena


# The cell-grain tokens of the ink-identical name-grain classes. Any other cell-grain token on a `may_ligature_seam_loosened` candidate means ink moved elsewhere in the row, so that predicate does not match it.
_NAME_GRAIN_TOKENS = frozenset(
    {"stance", "entry-added", "entry-moved", "entry-dropped", "exit-added", "exit-moved", "exit-dropped"}
)


@predicate("may_ligature_seam_loosened")
def _may_ligature_seam_loosened(row: DivergentRow) -> bool:
    """Match the reviewed `·Day+Utter ~x~ ·May` seam. The old font tucks ·May's x-height entry one pixel into the ligature's exit; the new model places it at the anchor-aligned column and draws no connector, which is the intended design (the may-ligature-seam-loosened ledger entry records the decision). Matches non-kern position drift on rows whose old glyph names contain that pair and whose other cell-grain tokens, if any, are all in `_NAME_GRAIN_TOKENS`."""
    if "position-drift" not in row.phenomena or "position-kern-attributable" in row.phenomena:
        return False
    cell_grain = {item for item in row.phenomena if not item.startswith("position")}
    if not cell_grain <= _NAME_GRAIN_TOKENS:
        return False
    glyphs = row.baseline_glyphs
    return any(
        glyphs[index].startswith("qsDay_qsUtter") and glyphs[index + 1].startswith("qsMay.en-y5")
        for index in range(len(glyphs) - 1)
    )


@dataclass(frozen=True)
class CompiledLedger:
    """The divergence ledger grouped by what each entry's `match` tests, so matching a row is a class lookup instead of a pass over every entry. Every entry keeps its ledger index, so `_match_compiled` can return matches from several groups in ledger order. `by_class` is keyed on the class id a `CLASS_PREDICATE_IDS` entry names. `functions` holds the entries whose predicate is a function of the row. `unconditional` holds the entries with no predicate, which match every row their `window` and `seam_change` admit; an empty `match` matches every row (rebuild/test_conform.py's ink-identical fixture ledger is one). An entry's `configs` is None when it applies to every configuration, and otherwise a container `row.config` is tested against. Each worker builds its own (`oracle_config_worker`)."""

    by_class: Mapping[str, tuple[tuple[int, str, Container[str] | None], ...]]
    functions: tuple[tuple[int, str, Container[str] | None, Callable[[DivergentRow], bool]], ...]
    unconditional: tuple[tuple[int, str, Container[str] | None, str | None, bool], ...]


def compile_ledger(entries: Sequence[Mapping]) -> CompiledLedger:
    """Compile the raw ledger (`yaml.safe_load` of rebuild/m1-divergences.yaml) into a `CompiledLedger`. An entry without an `id` is named `<unnamed>`. `configs: all` compiles to None and a list to a frozenset. A bare string is kept as a string, so it gets the same substring `in` test as the reference walk in rebuild/test_conform.py. Any other value, such as a `configs:` with no value, raises TypeError instead of silently matching every configuration. An entry whose predicate is in neither `CLASS_PREDICATE_IDS` nor `PREDICATES` is dropped, because it can match no row."""
    by_class: dict[str, list[tuple[int, str, Container[str] | None]]] = {}
    functions: list[tuple[int, str, Container[str] | None, Callable[[DivergentRow], bool]]] = []
    unconditional: list[tuple[int, str, Container[str] | None, str | None, bool]] = []
    for index, entry in enumerate(entries):
        match = entry.get("match", {})
        entry_id = entry.get("id", "<unnamed>")
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
                (index, entry_id, configs, match.get("window"), match.get("seam_change") is not None)
            )
            continue
        class_id = CLASS_PREDICATE_IDS.get(predicate_name)
        if class_id is not None:
            by_class.setdefault(class_id, []).append((index, entry_id, configs))
            continue
        function = PREDICATES.get(predicate_name)
        if function is not None:
            functions.append((index, entry_id, configs, function))
    return CompiledLedger(
        by_class={class_id: tuple(filed) for class_id, filed in by_class.items()},
        functions=tuple(functions),
        unconditional=tuple(unconditional),
    )


def _match_compiled(compiled: CompiledLedger, row: DivergentRow) -> list[str]:
    """Return the ids of every ledger entry this row matches, in ledger order, so the caller can tell a single match from a multi-match. The row is classified once and its class looked up in `by_class`. Function predicates run only when their `configs` admit the row's configuration, and unconditional entries apply their `window` and `seam_change` tests."""
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
    for index, entry_id, configs, window, needs_seam in compiled.unconditional:
        if configs is not None and config not in configs:
            continue
        if window is not None and window not in row.codepoints:
            continue
        if needs_seam and "seam" not in row.kinds:
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
    """Return this run's scratch directory for the audit shards and the staged audit, beside `divergence-audit.tsv`. The pid in the name lets two runs share an `out_dir` (a `--gates-only` pass during a cycle, for example) without mixing or deleting each other's shards. The name matches neither `artifact_cycle.M1_ARTIFACT_NAMES` nor any glob over the tables, so nothing a killed run leaves here is taken for an artifact."""
    return Path(out_dir) / f"divergence-audit.parts.{os.getpid()}"


def oracle_audit_shard(scratch_dir: Path, config: str, segment: int | None = None) -> Path:
    """Return the file for one configuration's audit rows, without the header, or for one row range's rows when `segment` is given. Workers pass their rows to the parent as a file instead of a pickled list because the audit is the largest output of the build and grows with every migrated letter; returning it through the pool would hold all of it in the parent's memory on top of the phase's existing peak."""
    name = f"{config}.part" if segment is None else f"{config}.{segment}.part"
    return Path(scratch_dir) / name


def settle_memo_part(scratch_dir: Path, config: str, segment: int) -> Path:
    """Return the file where one row range of the pooled oracle, whether its configuration is cut or not, writes the settle memo windows it settled. It is under the run's scratch directory, so a killed run's parts are deleted with its other scratch files and `conform.absorb_settle_memo_parts` finds every range's part in one directory."""
    return Path(scratch_dir) / "settle-memo" / f"{config}.{segment}.gz"


@contextmanager
def _staged_oracle_audit(out_dir: Path) -> Iterator[Path]:
    """Yield the staging path for this run's audit, and move it to `divergence-audit.tsv` only when the block exits normally. The audit is a fingerprinted artifact, and a truncated one would read as a new build with fewer units while every downstream gate passes. So an oracle killed partway (Ctrl-C, a timeout, a full disk) must leave the previous complete audit in place. The staging file is in this run's scratch directory, so the same cleanup deletes it with the shards."""
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


def join_oracle_audit(
    out_dir: Path,
    scratch_dir: Path,
    configs: Iterable[str],
    expect_rows: int,
    segments: Mapping[str, int] | None = None,
) -> None:
    """Concatenate the shards into `divergence-audit.tsv` after the header, in the caller's configuration order and, within a configuration that `segments` says is cut into several ranges, in row order. The copy is binary with a 1 MiB buffer, so the parent never holds the audit in memory, and the bytes match what `_compare_config` writes on the serial path. Two checks keep a partial join from replacing the audit: every shard must exist before any byte is copied (FileNotFoundError names the missing ones), and the number of lines copied must equal `expect_rows`, the workers' own `divergent_rows` total (ValueError otherwise). The join needs disk space for a second copy of the audit while it runs. Deleting the scratch directory is left to the caller, because the failure it cleans up after is usually a worker's."""
    scratch = Path(scratch_dir)
    shards: list[tuple[str, Path]] = []
    for config in configs:
        count = (segments or {}).get(config, 1)
        if count <= 1:
            shards.append((config, oracle_audit_shard(scratch, config)))
        else:
            shards.extend((config, oracle_audit_shard(scratch, config, segment)) for segment in range(count))
    missing = sorted({config for config, path in shards if not path.is_file()})
    if missing:
        left = sorted(found.name for found in scratch.glob("*")) if scratch.is_dir() else []
        raise FileNotFoundError(
            f"the oracle wrote no audit shard for {', '.join(missing)} — it left {left}, and divergence-audit.tsv is untouched"
        )
    rows = 0
    with _staged_oracle_audit(out_dir) as staging:
        with staging.open("wb") as target:
            target.write((ORACLE_AUDIT_HEADER + "\n").encode("utf-8"))
            for _, path in shards:
                with path.open("rb") as shard:
                    while chunk := shard.read(1 << 20):
                        rows += chunk.count(b"\n")
                        target.write(chunk)
        if rows != expect_rows:
            raise ValueError(
                f"the oracle's audit shards hold {rows} rows where its workers counted {expect_rows} divergent — divergence-audit.tsv is untouched"
            )


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
    """Delete this run's scratch directory, and any earlier run's whose process has exited, since a killed run skips the `finally` that would have deleted its shards and they can take as much disk as the audit. A directory whose pid is still running belongs to another oracle and is kept. Nothing here raises, so calling it in a `finally` cannot replace the exception being handled."""
    out = Path(out_dir)
    shutil.rmtree(oracle_audit_scratch(out), ignore_errors=True)
    with suppress(OSError):
        for path in out.glob("divergence-audit.parts.*"):
            if not _oracle_audit_owner_is_running(path.name.rpartition(".")[2]):
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
    audit: TextIO | None,
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
    # The oracle's rows are texts the belt also sweeps, so they settle through the belt's shared settle memo file (`settle_memo`). The rows of each chunk that need walking are settled in one `walk_many` call, and each row is compared with the stream that call returned. `_compare_row` reads cells, not glyph names, so the walk gets an empty `glyph_names`; its memo keys are the same either way. The overlay configuration's walk reads the registry and settles nothing.
    walker: _SettledWindowWalk | IsolatedOverlayWalk
    if isolated_overlay_active(spec, features):
        walker = IsolatedOverlayWalk(spec)
    else:
        assert guard_verdicts is not None
        walker = _SettledWindowWalk(spec, features, {}, guard_verdicts, memo=settle_memo)
    config_started = time.perf_counter()
    rows = iter_rows(table_path, first_row, stop_row)
    # Only stale rows are walked. A served row's verdict comes from the store in the same form as a fresh one before `_match_compiled` sees it, and the second loop visits the chunk in table order, so the audit bytes do not depend on which rows were served. Verification samples are drawn from served rows, not from written ones, because a read-only pass (`--gates-only`) serves verdicts without writing any. A served position verdict is used only when this pass's ledger sends the row through the position channel. For a row the ledger excludes, the stored position verdict is written forward unchanged, so a later ledger edit that includes the row again can use it.
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
    while True:
        chunk = list(itertools.islice(rows, ORACLE_ROW_CHUNK))
        if not chunk:
            break
        served_at: dict[int, oracle_cache.StoredRecord] = {}
        positions_at: dict[int, tuple[oracle_cache.StoredRecord, tuple[str, ...]]] = {}
        fresh_at: list[int] = []
        for offset, row in enumerate(chunk):
            index = first_row + offset
            if store is None or index >= store.rows:
                fresh_at.append(offset)
                continue
            mask = store.mask.mask_of(row.codepoints)
            if store.stale(index, mask):
                fresh_at.append(offset)
                continue
            reachable = store.mask.families_of(mask)
            if oracle_cache.unreachable_glyph_heads(row.glyphs, reachable):
                # The row uses alias entries of a family none of its keys cover, so no key on this store could report them changed. Nothing in the live subset does this; a row that does is walked instead of served.
                fresh_at.append(offset)
                continue
            record = store.serve(index, row.codepoints)
            served_at[offset] = record
            if sample is not None:
                sample.offer(index, row, reachable)
            if record.position is not oracle_cache.UNSHAPED and not store.position_stale(index, mask):
                positions_at[offset] = (record, reachable)
        walked = dict(zip(fresh_at, walker.walk_many([chunk[offset].text for offset in fresh_at])))
        for offset, row in enumerate(chunk):
            index = first_row + offset
            result.rows_compared += 1
            if offset in served_at:
                record = served_at[offset]
                cached = record.row
                divergent = None if cached is None else _served_verdict(config, row, cached)
                derived_at = record.row_age
            else:
                settled, _names = walked[offset]
                divergent = _compare_row(spec, aliases, config, features, row, settled)
                cached = _cached_verdict(divergent)
                derived_at = this_pass
            carried = positions_at.get(offset)
            position: oracle_cache.PositionVerdict = oracle_cache.UNSHAPED
            position_at = this_pass
            if carried is not None:
                position, position_at = carried[0].position, carried[0].position_age
            matches = _match_compiled(ledger, divergent) if divergent is not None else []
            if shaper is not None:
                topology_clean = divergent is None or not ({"ligation", "seam"} & set(divergent.kinds))
                class_claims_ink_identity = divergent is None or (
                    len(matches) == 1 and matches[0] in ink_identical_ids
                )
                if topology_clean and class_claims_ink_identity:
                    if carried is not None and store is not None and position_sample is not None:
                        assert not isinstance(position, oracle_cache._Unshaped)
                        drift = oracle_positions._served_position(position)
                        store.positions_served += 1
                        position_sample.offer(index, row, carried[1])
                    else:
                        drift = oracle_positions._position_drift(shaper, kern, features, row)
                        position, position_at = oracle_positions._cached_position(drift), this_pass
                    result.positions_compared += 1
                    if drift is not None:
                        drift_notes, kern_attributable = drift
                        phenomena = ("position-kern-attributable",) if kern_attributable else ()
                        prior_ink_match = matches[0] if len(matches) == 1 else None
                        if divergent is None:
                            divergent = DivergentRow(
                                config=config,
                                codepoints=":".join(f"{cp:04X}" for cp in row.codepoints),
                                kinds=("position",),
                                position=-1,
                                baseline_glyphs=tuple(row.glyphs),
                                baseline_seams=tuple(row.seams),
                                new_cells=tuple(glyph for glyph in drift_notes),
                                new_seams=(),
                                phenomena=phenomena + ("position-drift",),
                            )
                        else:
                            divergent = replace(
                                divergent,
                                kinds=divergent.kinds + ("position",),
                                phenomena=divergent.phenomena + phenomena + ("position-drift",),
                            )
                        rematch = _match_compiled(ledger, divergent)
                        # Kern-attributable drift is out of scope, so when nothing matches after it is added, a row that already matched a single ink-identical class keeps that match. In every other case, including drift that is not kern-attributable (a real ink shift), the match list computed with the drift replaces the old one.
                        if not rematch and kern_attributable and prior_ink_match is not None:
                            matches = [prior_ink_match]
                        else:
                            matches = rematch
                else:
                    result.positions_excluded += 1
            if writer is not None:
                writer.append(row.codepoints, cached, derived_at, position, position_at)
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
        first_row += len(chunk)
    served_rows = 0 if store is None else store.served
    result.positions_served = 0 if store is None else store.positions_served
    if store is not None and sample is not None:
        _verify_served_sample(spec, aliases, config, features, walker, store, sample)
    if store is not None and position_sample is not None and shaper is not None:
        oracle_positions._verify_served_positions(shaper, kern, features, store, position_sample)
    memo_line = walker.memo_line(label, walker.save_memo())
    if memo_line is not None:
        print(memo_line, file=sys.stderr, flush=True)
    print(
        f"[t] oracle {label} {time.perf_counter() - config_started:.2f}s rows={result.rows_compared} first={start_row} positions={result.positions_compared} served={served_rows} positions_served={result.positions_served}",
        file=sys.stderr,
        flush=True,
    )
    result.peak_rss_bytes = peak_rss_self_bytes()
    return result


def oracle_config_worker(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    alias_path: Path,
    ledger_path: Path,
    config: str,
    font_path: Path | None,
    kern_sidecar_path: Path | None,
    audit_dir: Path,
    row_cache: "OracleRowCache | None" = None,
    settle_memo: SettleMemoFile | None = None,
    guard_verdicts: settle.FormationGuard | None = None,
    shard: OracleShard | None = None,
) -> OracleConfigResult:
    """Run `_compare_config` for one configuration, or one row range when `shard` is given, in a pool worker. Audit rows go to the range's segment under `audit_dir`, so the result carries only counts. When the caller passes no `guard_verdicts`, the worker runs the section 5.7 guard sweep itself, as the belt's worker does; the overlay configuration's worker forms nothing and skips it. A caller that fans out several ranges runs the sweep once and passes it to every range. The worker opens the row cache itself, through the same `open_row_cache` call the serial path makes, and reads and writes `settle_memo` itself, because a spawned worker inherits no open files. For a range of the pooled oracle, `settle_memo.write_path` is the range's own part, which the parent absorbs into the shared file after every range has finished and the witness stage has returned."""
    shard = OracleShard(config) if shard is None else shard
    aliases = load_alias_map(alias_path)
    entries = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in entries if entry.get("ink_identical")}
    ledger = compile_ledger(entries)
    features = features_for_config(config)
    overlay = isolated_overlay_active(spec, features)
    shaper = oracle_positions._shaper_for(spec, font_path, overlay)
    kern = oracle_positions.KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    segment_path = oracle_audit_shard(audit_dir, config, shard.segment)
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    store, writer = open_row_cache(
        row_cache, spec, config, shard.segment, first_row=shard.first_row, stop_row=shard.stop_row
    )
    if guard_verdicts is None and not overlay:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    with ExitStack() as stack:
        audit = stack.enter_context(segment_path.open("w", encoding="utf-8", newline="\n"))
        if writer is not None:
            stack.enter_context(writer)
        return _compare_config(
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


def merge_config_shards(results: Sequence[OracleConfigResult]) -> OracleConfigResult:
    """Combine one configuration's row-range results, given in row order, into one result. Counts and `counts_by_entry` are summed. `unmatched_exemplars` are concatenated and cut to `ORACLE_UNMATCHED_EXEMPLARS`, which gives the same rows an uncut run keeps, because a range's list holds all of its unmatched rows whenever it is shorter than the cap. `notes` are concatenated without repeats, since a missing table is noted once per range. All ranges must report the same `pass_ordinal`, because they read the same store header; a mismatch is a fan-out bug and raises ValueError. `peak_rss_bytes` is the largest range's."""
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
        merged.peak_rss_bytes = max(merged.peak_rss_bytes, result.peak_rss_bytes)
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
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    out_dir: Path | None = None,
    font_path: Path | None = None,
    kern_sidecar_path: Path | None = None,
    row_cache: "OracleRowCache | None" = None,
    settle_memos: Mapping[str, SettleMemoFile] | None = None,
) -> BaselineReport:
    aliases = load_alias_map(alias_path)
    entries = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in entries if entry.get("ink_identical")}
    ledger = compile_ledger(entries)
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
