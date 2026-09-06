"""The section 6 baseline oracle (M1-PLAN section 6, Group 3): the settlement function against the section 13.1 baseline, one configuration at a time, with every divergent row classified into the divergence ledger and the position channel diffed against the kern-normalized old positions.

This is the comparison side of the pipeline, split from conform.py so that it can sit outside the stamp a serialized window enumeration carries (`fingerprint.tables_value`, keyed on `fingerprint.table_code_paths`): nothing here builds a decision table or a font, and everything here runs against tables and an M1.otf that are already built. What licenses that is `rebuild/test_build_code_closure.py`, which walks the import graph from every build-side module and from `run_m1.run` and fails the moment either reaches this module. The consequence is the workflow `run_m1 --gates-only` exists for: an edit to `classify_divergence`, a predicate, `SS10_UNCOVERED_BY_OLD_FONT`, `_match_ledger` or the position channel leaves every enumeration on disk exactly as fresh as it was, so the oracle re-adjudicates against them rather than waiting on a rebuild that would return the same bytes. The whole-run record does not narrow: this file is still `fingerprint.pipeline_code_paths`, so the Stage A `pipeline_code` component, the artifact cycle's run_m1 green and the review surface's stamp all still move on an edit here.

The producer of what the oracle classifies stays in conform.py: `_compare_row` and the memoized `_SettledWindowWalk` are the two entry points the oracle row cache's stamp is cut from (`oracle_cache.ORACLE_ROW_CODE_PATHS`), and the codec between a fresh `DivergentRow` and a stored record (`_cached_verdict`, `_served_verdict`) and the served-sample verification live beside them. This module imports those and is imported by nothing under that stamp, which is what lets a classifier edit serve every row from the store: `rebuild/test_oracle_code_closure.py` holds conform.py to that.

`compare_against_baseline` streams the filtered sub-tables, settles every row through a walk of its own (or, when the caller hands down an `OracleRowCache`, takes the row's pre-position verdict off the previous pass's store and walks only what an edit can still reach — see rebuild/pipeline/oracle_cache.py for what that key does and does not cover), compares ligation, seams and cells against the alias map, classifies each divergent row through `_match_ledger`, and shapes the rows the ledger calls ink-identical against M1.otf to diff drawn positions — or takes that answer off the same store, under the position key that adds the font's per-family glyphs and the kern sidecar, and re-shapes only the rows an edit can still reach. The per-configuration form, `oracle_config_worker`, is what run_m1 fans out one process per acceptance configuration, each writing its own audit shard under `oracle_audit_scratch` for `join_oracle_audit` to concatenate. The overlay configuration (ss10) is compared against a stream no table produced: its rows walk through `conform.IsolatedOverlayWalk`, which answers every letter bare from the registry alone, and its position channel shapes through `conform.IsolatedOverlayShaper`, the twins' `hmtx` advances in place of HarfBuzz — both licensed by read-back's isolation proof and the belt's overlay arm — so the old font's ss10 rows are held against "all bare", a function of the baseline and the alphabet, with no settlement and no shaping spent on them.
"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import time
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, TextIO

import yaml

from rebuild.pipeline import baseline_subset, geometry, kernel_exec, oracle_cache, settle
from rebuild.pipeline.conform import (
    ACCEPTANCE_CONFIGS,
    BOUNDARY_GLYPH_NAMES,
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
    features_for_config,
    load_alias_map,
)
from rebuild.pipeline.model import ResolvedSpec, isolated_overlay_active
from rebuild.validation.rowmodel import Row, format_codepoints, iter_rows

# The same bound on the oracle's side, where the texts arrive as baseline rows rather than as a product.
ORACLE_ROW_CHUNK = 65536
# How many unmatched rows a configuration keeps whole. Every one of them is written to its audit shard regardless; this is only how many the summary can quote.
ORACLE_UNMATCHED_EXEMPLARS = 20


@dataclass
class BaselineReport:
    rows_compared: int = 0
    divergent_rows: int = 0
    positions_compared: int = 0
    positions_excluded: int = (
        0  # rows skipped by the position channel: seam/ligation divergence, or a matched class that legitimately redraws ink
    )
    positions_served: int = 0
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched_count: int = 0
    unmatched_exemplars: list[DivergentRow] = field(default_factory=list)
    multi_matched: list[tuple[DivergentRow, tuple[str, ...]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class OracleConfigResult:
    """One configuration's oracle tally, which travels home from `oracle_config_worker` down a process pipe. The unmatched rows ride as a count plus the first `ORACLE_UNMATCHED_EXEMPLARS` of them rather than as the whole list: `oracle_summary.json` reads a length and quotes that many exemplars, so pickling every unmatched `DivergentRow` back to the parent spent an audit's worth of objects on a number. Nothing is lost by the cap — the worker has already written every unmatched row to its own audit shard, one line each, and `divergence-audit.tsv` is where they are read. `positions_served` counts the rows among `positions_compared` whose verdict came off the store rather than out of HarfBuzz."""

    config: str
    rows_compared: int = 0
    divergent_rows: int = 0
    positions_compared: int = 0
    positions_excluded: int = 0
    positions_served: int = 0
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched_count: int = 0
    unmatched_exemplars: list[DivergentRow] = field(default_factory=list)
    multi_matched: list[tuple[DivergentRow, tuple[str, ...]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OracleRowCache:
    """What one oracle run hands each of its configurations — `ACCEPTANCE_CONFIGS`, unless the caller narrows them — so each can read the previous pass's row verdicts and stage this pass's. The stamps and the family keys are cut once in the parent, before the first row is compared, and travel down the pool pipe: a worker that re-digested the rune tree for itself would be reading files the run has already been holding for minutes, which is the very race `run_oracle` re-checks at promotion. `position_environment` and `position_keys` are the position store's pair, cut off the font the same way; a run with neither (no font to shape against, or a font whose digests would not cut) records every position as unshaped and serves none. `read_dir` is where a promoted store lives and `write_dir` where a fresh one is staged; either may be absent on its own, and a pass that may read but not write (`--gates-only`, which recompiled nothing and so may not write a build input) simply leaves `write_dir` None. Such a pass also carries a nonzero `rotation`, because everything that keeps a record from laundering itself advances on the pass ordinal and the ordinal advances only when a store is written — see `oracle_cache.RowStore`."""

    environment: Mapping[str, oracle_cache.EnvironmentStamp]
    family_keys: Mapping[str, str]
    read_dir: Path | None = None
    write_dir: Path | None = None
    rotation: int = 0
    position_environment: oracle_cache.EnvironmentStamp | None = None
    position_keys: Mapping[str, str] | None = None


def open_row_cache(
    cache: "OracleRowCache | None", spec: ResolvedSpec, config: str
) -> tuple["oracle_cache.RowStore | None", "oracle_cache.RowWriter | None"]:
    """This configuration's loaded store and its staged successor, opened by whichever of the two oracle paths is running so the pair stays byte-equal between them. The subset digest is read off the stamp's own `subset` line rather than hashed a second time — the stamp already carries the bytes of the table this configuration is about to stream."""
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
        )
    writer = None
    if cache.write_dir is not None:
        writer = oracle_cache.RowWriter(
            oracle_cache.scratch_store_path(cache.write_dir, config),
            stamp,
            subset_digest,
            oracle_cache.next_pass_ordinal(store),
            cache.family_keys,
            cache.position_environment,
            cache.position_keys,
        )
    return store, writer


def unaliased_subset_names(subset_dir: Path, alias_path: Path) -> dict[str, list[str]]:
    """Every old glyph name in any subset baseline row that resolves through neither the alias map nor BOUNDARY_GLYPH_NAMES, mapped to the sorted configs it appears in. The alias map's contract is completeness over these rows, and a hole is a silent wrong-number generator rather than a loud failure — a ligation-grain row never reaches the per-glyph alias check in `_compare_row`, so its counts ride ledger classes as if the name were understood — which is why run_m1 refuses to build while this is non-empty. A `pending` entry acknowledges a name mid-migration without claiming a denotation: it resolves here and still reads as unaliased in the comparison. The names themselves are read from the sidecar the refilter wrote (`baseline_subset.read_subset_names`) rather than streamed out of ten million subset rows: the roster can only change when the tables are refiltered, so this costs milliseconds and runs on the `--gates-only` path as readily as on a build."""
    known = set(load_alias_map(alias_path)) | BOUNDARY_GLYPH_NAMES
    missing: dict[str, set[str]] = {}
    for config, names in baseline_subset.read_subset_names(subset_dir).items():
        for name in names:
            if name not in known:
                missing.setdefault(name, set()).add(config)
    return {name: sorted(configs) for name, configs in sorted(missing.items())}


def classify_divergence(row: DivergentRow) -> str | None:
    """Assign a divergent row to exactly one ledger class from its phenomenon set (computed by `_compare_row` against the alias map). The set is a partition by construction: each row gets the single highest-precedence class, with the precedence documented in rebuild/m1-divergences.yaml. None = unexplained, which fails conformance."""
    phenomena = set(row.phenomena)
    if not phenomena or any(item.startswith("unaliased") for item in phenomena):
        return None
    if any(item.startswith("position") for item in phenomena):
        # Position drift never rides a cell-grain class (the ink-identity claim it would hide is exactly what the position channel tests); position-only rows go through the kern-attribution predicate instead.
        return None
    if {"0020", "200C"} & set(row.codepoints.split(":")):
        # The ratified boundary-equals-word-boundary rule (design section 3.4): the new font renders every segment of a window containing a run-splitting boundary (space or ZWNJ) identically to that segment standing alone — enforced per build by the belt's own split-buffer check — so a boundary row can only diverge from the baseline where the old font was itself inconsistent across the boundary, and every segment-internal divergence resurfaces on the segment's own enumerated row. Boundary rows therefore carry no adjudicable information and are absorbed wholesale, ahead of every other cell/seam-grain class.
        return "boundary-echo"
    if "ligation" in phenomena:
        # Under the isolated overlay the new font never forms the ligature at all (the ss10 pre-empt replaces every letter before formation) while the old font keeps drawing its own ligature, so the suppression class outranks the marker-staging one (whose 00B7 arm would otherwise swallow the namer-dot ss10 windows).
        if row.config == "ss10" and (
            "E653:E67A" in row.codepoints or "E652:E679" in row.codepoints or "E67B:E652" in row.codepoints
        ):
            return "ss10-ligature-suppressed"
        if "E67B:E652" in row.codepoints and "ss03" in row.config:
            return "ss03-out-tea-ligature-kept"
        if "E652:E679" in row.codepoints and ("200C" in row.codepoints or "ss03" in row.config):
            return "marker-staging-ligature-formation"
        # The qsDay_qsUtter ligature forms unconditionally in the old font too (bare E653:E67A renders as the ligature in every config), so only the post-marker windows diverge: the old pipeline renames the lead to .noentry / leaks a bare name after a ZWNJ or the namer dot, and never forms the ligature there. Same staging phenomenon as ·Tea·Oy.
        if "E653:E67A" in row.codepoints and ("200C" in row.codepoints or "00B7" in row.codepoints):
            return "marker-staging-ligature-formation"
        return None
    gains = {item for item in phenomena if item.startswith("seam-gain:")}
    if "seam-moved" in phenomena:
        # A pure seam move (no gain, no loss) on a row whose old glyph was a post-ZWNJ .noentry shadow is the word-initial unification choosing a different seam height than the shadow stance drew: the old .noentry shadow joined its follower at one height, but settling the post-ZWNJ letter as word-initial (identical to its post-space form) lands the join elsewhere. Routed only when the sole seam change is the move, so a post-ZWNJ row that also gains or loses a seam still falls through to its own class.
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
        # The grounded ·See·Out fusion re-spells the old pull-back across the seam: the old pipeline's ex-con-1 tucks ·Out into ·See's still-whole tail (anchor-only, ink kept), while the runes keep the tail's anchor at convention and pull the raked redraw's foot instead, so the composite ink is identical and only the names differ. The subset guard keeps any row where real ink moved elsewhere out of the class.
        return "see-out-fusion-respelled"
    if (
        "+ex-ext-2" in phenomena
        and phenomena <= {"+ex-ext-2", "-ex-ext-1", "-en-ext-2", "exit-dropped"}
        and "E665:E65D" in row.codepoints
    ):
        # The ·May·J'ai seam replaces the old split extension with qsMay's single by-2 exit record in every follower context; the rune's why records the binding one-pixel spacing choice. The -en-ext-2 token is the ·J'ai side of the same consolidation now that its alias spells the old name faithfully. The subset guard keeps any row where unrelated ink moved elsewhere out of the class.
        return "may-jai-extension-consolidated"
    if phenomena and phenomena <= {"+en-con-1", "+en-con-2"} and "E65D" in row.codepoints:
        # The old pipeline's exit contractions before ·J'ai are tucks — the left keeps its ink and only the anchor moves in, overlapping the follower — which M1 re-spells as ·J'ai's own entry contraction: the crown gives up the overlapped columns and abuts instead, so the placed composite, every origin, and every advance are unchanged and only ·J'ai's cell name gains the con token. The subset guard keeps any row where real ink moved elsewhere out of the class.
        return "jai-entry-contraction-respelled"
    # The may-exit-withdrawal-generalized class retired with qsMay's pulled-back exit; a row resurrecting these phenomena carries an ink delta, so it must surface UNMATCHED rather than fall through to the name-grain classes below.
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


# The ledger entries whose predicate is nothing but "classify_divergence chose this class". `_match_ledger` reads the class id straight out of this map and classifies each row once, where letting every one of these closures re-classify cost the oracle its slowest microsecond per divergent row; the three predicates below, which ask something classification cannot, keep functions of their own.
CLASS_PREDICATE_IDS: dict[str, str] = {}

for _class_id in (
    "boundary-echo",
    "ss10-ligature-suppressed",
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
):
    CLASS_PREDICATE_IDS[_class_id.replace("-", "_")] = _class_id
    PREDICATES[_class_id.replace("-", "_")] = _class_predicate(_class_id)


@predicate("kern_channel_out_of_scope")
def _kern_channel_out_of_scope(row: DivergentRow) -> bool:
    """Position-only rows whose drifted slot the comparison marked kern-attributable (the old pair carries a nonzero sidecar kern, or the drift sits on a ZWNJ adjacency). Everything else position-shaped stays unmatched and fails — non-kern position drift is chased to ground, never absorbed here."""
    return row.kinds == ("position",) and "position-kern-attributable" in row.phenomena


# Cell-grain tokens that ride the ink-identical name-grain classes; anything outside this set on a seam-loosened candidate means real ink moved elsewhere in the row, so the row stays unmatched and fails.
_NAME_GRAIN_TOKENS = frozenset(
    {"stance", "entry-added", "entry-moved", "entry-dropped", "exit-added", "exit-moved", "exit-dropped"}
)


@predicate("may_ligature_seam_loosened")
def _may_ligature_seam_loosened(row: DivergentRow) -> bool:
    """The adjudicated ·Day·Utter→·May x-height seam: the old font tucks ·May's x-height entry one pixel into the ligature's exit, the new model seats it at the anchor-aligned column and draws no connector, and the looser seat is the intended design (the may-ligature-seam-loosened ledger entry carries the adjudication). Matches non-kern position drift on rows whose old names carry that exact pair and whose cell-grain residue (if any) is pure name grain."""
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


# The migrated runes whose joins the old shipped font never wired into the ss10 isolated overlay, so the old font keeps drawing their cursive joins under ss10 while the new model isolates every letter by design.
# Membership is not automatic for a newly-migrated rune: qsFee was weighed and deliberately left out, because the old ss10 overlay substitutes every qsFee variant to the bare cmap glyph, which carries no cursive anchors, so the old font already isolates ·Fee correctly and its ss10 seam-loss rows ride the existing ss10_isolation_completed class instead.
# qsAh is a member because its baseline entry and x-height exit anchors ride the base cmap glyph (the ·Pea/·Oy→·Ah and ·Ah→·Day joins are bare-glyph GPOS attachments with no calt variant), so the old ss10 overlay has nothing to substitute away and keeps drawing those joins.
# qsOut is a member on the qsAh precedent, entry side only: its baseline entry anchor rides the bare cmap glyph (E650:E67B stays a y0 join under the old ss10), while its x-height exits live on calt variants the old overlay does substitute away. qsOut_qsTea inherits the same bare-glyph entry from its lead, the qsDay_qsUtter shape.
# qsAwe is a member on the qsAh precedent, both sides at once: the old record has no stances, so its x-height entry and baseline exit both ride the base cmap glyph, and bare qsAwe is the only ·Awe glyph the old font emits under ss10 — keeping the y5 joins into it and the y0 joins out of it wherever the neighbor's anchor also survives the overlay.
# qsOx joined at its own migration on the identical shape: no stances in the old record, so both anchors ride the base cmap glyph and bare qsOx keeps its seams under ss10 (qsMay|qsOx stays y5, qsOx|qsVie stays y0).
# qsEight joined at its own migration on the same shape: no stances in the old record, so both anchors ride the base cmap glyph and bare qsEight keeps its seams under ss10 (qsMay|qsEight stays y5, qsEight|qsVie stays y0).
# qsAt joined on direct pair evidence: the old overlay leaves bare qsAt in place, so qsPea|qsAt stays joined at the baseline and qsAt|qsDay stays joined at the x-height; the contextual before-·May and before-·J'ai forms are covered and isolate correctly.
# qsOoze joined at its own migration on the qsAwe shape: no stances in the old record, so its baseline entry and baseline exit both ride the base cmap glyph and bare qsOoze keeps its seams under ss10 (qsPea|qsOoze stays y0, qsOoze|qsVie stays y0).
# qsBay joined on direct pair evidence, the qsAt shape: the old overlay leaves bare qsBay's baseline exit live (qsBay|qsVie stays y0, likewise qsSee/qsLow/qsRoe/qsAt/qsAh/qsOut/qsOoze), while the contextual en-y5 entry form is substituted away correctly, so every entry into qsBay isolates (qsI|qsBay breaks under ss10).
# qsKey joined at its own migration on the qsAwe shape: no stances in the old record, so its top entry and baseline exit both ride the base cmap glyph and bare qsKey keeps its seams under ss10 (qsSee|qsKey stays y8, qsKey|qsVie stays y0), while the receivers the old font serves through contextual forms (qsTea.en-y0.en-ext-1, qsDay.half, qsMay.en-y0.ex-y5, qsNo.alt) are substituted away and isolate correctly.
# qsThaw joined at its own migration on the qsOut precedent, entry side only: its baseline entry anchor rides the bare cmap glyph, so every left whose exit anchor also survives the overlay keeps joining it under the old ss10 (qsBay|qsThaw stays y0, likewise qsDay/qsEt/qsEight/qsAwe/qsOx/qsOy/qsOoze — and qsPea|qsThaw rejoins under ss10 alone, because the after-tall break is itself a calt substitution the overlay disables), while its one exit lives on a calt stance the overlay does substitute away (qsThaw|qsIng breaks under ss10).
SS10_UNCOVERED_BY_OLD_FONT = frozenset(
    {
        "qsAh",
        "qsDay",
        "qsNo",
        "qsLow",
        "qsUtter",
        "qsDay_qsUtter",
        "qsOut",
        "qsOut_qsTea",
        "qsAwe",
        "qsOx",
        "qsEight",
        "qsAt",
        "qsOoze",
        "qsBay",
        "qsKey",
        "qsThaw",
    }
)


@predicate("ss10_isolation_completed")
def _ss10_isolation_completed(row: DivergentRow) -> bool:
    """Under ss10 the new model renders every position bare (the overlay forces the default stance with no seam), so a join the old font still drew there reads as a seam-loss. The old font's ss10 overlay was authored before the runes in `SS10_UNCOVERED_BY_OLD_FONT` (whose anchors ride the base cmap glyph, so the old overlay keeps their joins too) and never isolates them, so it keeps joining the new letters under ss10; the new font's complete isolation is the intended correction. Matches ss10 rows whose only seam change is losses, each on a seam touching one of those new runes (an existing|existing seam never joins under the old ss10, so it can never reach here). Space and ZWNJ rows are excluded so the boundary-echo blanket keeps the partition exact."""
    if {"0020", "200C"} & set(row.codepoints.split(":")):
        return False
    if row.config != "ss10" or "seam" not in row.kinds:
        return False
    runes = [token.split("/", 1)[0] for token in row.new_cells]
    saw_loss = False
    for index, (old_seam, new_seam) in enumerate(zip(row.baseline_seams, row.new_seams)):
        if old_seam == new_seam:
            continue
        if new_seam != "break":
            return False
        if old_seam in ("break", "lig"):
            continue
        neighbors = {
            runes[index] if index < len(runes) else "?",
            runes[index + 1] if index + 1 < len(runes) else "?",
        }
        if not (neighbors & SS10_UNCOVERED_BY_OLD_FONT):
            return False
        saw_loss = True
    return saw_loss


def _match_ledger(ledger: list[dict], row: DivergentRow) -> list[str]:
    """Every ledger entry this row matches, in ledger order — all of them, so the caller can still tell a single match from the two-plus that fail the ledger. The row is classified once here and each class-grain entry compares against that answer (CLASS_PREDICATE_IDS); the entries asking something else keep their own predicate."""
    classified = classify_divergence(row)
    matches: list[str] = []
    for entry in ledger:
        match = entry.get("match", {})
        entry_configs = match.get("configs", "all")
        if entry_configs != "all" and row.config not in entry_configs:
            continue
        predicate_name = match.get("predicate")
        if predicate_name is not None:
            class_id = CLASS_PREDICATE_IDS.get(predicate_name)
            if class_id is not None:
                if classified != class_id:
                    continue
            else:
                function = PREDICATES.get(predicate_name)
                if function is None or not function(row):
                    continue
        else:
            window = match.get("window")
            if window is not None and window not in row.codepoints:
                continue
            seam_change = match.get("seam_change")
            if seam_change is not None and "seam" not in row.kinds:
                continue
        matches.append(entry.get("id", "<unnamed>"))
    return matches


ZWNJ_CODEPOINT = 0x200C


def _kern_normalized_positions(
    kern: "KernEvaluator | None", row: Row, pixel: int
) -> tuple[tuple[tuple[int, int, int], ...], tuple[bool, ...]]:
    """The baseline row's per-slot position triples with sidecar kerns subtracted from the old advances (the new font emits no kerning), plus a per-slot kern-attribution mask: True where the slot's old advance carried a nonzero sidecar kern or sits on a ZWNJ adjacency. The kern partner of a slot is the next non-ZWNJ glyph: uni200C is default-ignorable, so HarfBuzz's GPOS pair matching skips it and the old font kerns straight across a ZWNJ (verified against the baseline — ·Oy ZWNJ ·Pea carries the ·Oy·Pea kern)."""

    def slot_is_zwnj(index: int) -> bool:
        return row.codepoints[row.clusters[index]] == ZWNJ_CODEPOINT

    expected: list[tuple[int, int, int]] = []
    attributable: list[bool] = []
    for index, (glyph, (x, y, advance)) in enumerate(zip(row.glyphs, row.positions)):
        kern_value = 0
        zwnj_adjacent = False
        if not slot_is_zwnj(index):
            partner = index + 1
            while partner < len(row.glyphs) and slot_is_zwnj(partner):
                zwnj_adjacent = True
                partner += 1
            if kern is not None and partner < len(row.glyphs):
                kern_value = kern.value_for(glyph, row.glyphs[partner]) * pixel
        else:
            zwnj_adjacent = True
        expected.append((x, y, advance - kern_value))
        attributable.append(bool(kern_value) or zwnj_adjacent)
    return tuple(expected), tuple(attributable)


def _position_drift(
    shaper: "Shaper | IsolatedOverlayShaper", kern: "KernEvaluator | None", features: frozenset[str], row: Row
) -> tuple[tuple[str, ...], bool] | None:
    """Shape the row against the new font and diff drawn positions against the kern-normalized baseline. The comparison is visual, not encoding-level: per-slot glyph origins (pen + x_offset, y_offset) plus the run's total advance, because the two fonts legitimately decompose a seam differently between the left glyph's advance and the right glyph's x_offset while drawing the identical join. Returns (drift descriptions, kern-attributable) or None when every slot and the total match."""
    shaped = shaper.shape(row.text, features)
    if len(shaped) != len(row.glyphs):
        return ((f"slot-count {len(row.glyphs)} (old) vs {len(shaped)} (new)",), False)
    expected, attributable = _kern_normalized_positions(kern, row, geometry.PIXEL)
    drifts: list[str] = []
    kern_attributable = True
    pen_old = 0
    pen_new = 0
    upstream_attributable = False
    for index, ((x, y, advance), glyph) in enumerate(zip(expected, shaped)):
        want = (pen_old + x, y)
        got = (pen_new + glyph["x_offset"], glyph["y_offset"])
        if got != want:
            drifts.append(f"slot {index} ({row.glyphs[index]}): origin want {want}, got {got}")
            kern_attributable = kern_attributable and upstream_attributable
        pen_old += advance
        pen_new += glyph["x_advance"]
        upstream_attributable = upstream_attributable or attributable[index]
    if pen_old != pen_new:
        drifts.append(f"total advance: want {pen_old}, got {pen_new}")
        kern_attributable = kern_attributable and upstream_attributable
    if not drifts:
        return None
    return (tuple(drifts), kern_attributable)


def _cached_position(drift: tuple[tuple[str, ...], bool] | None) -> oracle_cache.CachedPosition | None:
    """A fresh position answer as the store holds it — `None` for a row that matched, the drift descriptions and the kern flag otherwise."""
    return None if drift is None else oracle_cache.CachedPosition(drifts=drift[0], kern_attributable=drift[1])


def _served_position(cached: oracle_cache.CachedPosition | None) -> tuple[tuple[str, ...], bool] | None:
    """A stored position verdict back in the shape `_position_drift` answers, so everything after the channel cannot tell a served row from a freshly shaped one."""
    return None if cached is None else (cached.drifts, cached.kern_attributable)


def _verify_served_positions(
    shaper: "Shaper | IsolatedOverlayShaper",
    kern: "KernEvaluator | None",
    features: frozenset[str],
    table_path: Path,
    store: "oracle_cache.RowStore",
    sample: "oracle_cache.VerificationSample",
) -> None:
    """The position channel's half of `conform._verify_served_sample`: re-shape the pass's stratified sample of served positions through HarfBuzz and prove each against the record it was served from. The sample is drawn per family over the rows whose position was served, so a family whose glyphs moved under a key that failed to notice is caught with probability one; a mismatch is a hard stop for the same reason a row mismatch is — the audit is a fingerprinted artifact and a stale position in it reads as green forever."""
    wanted = set(sample.indexes())
    if not wanted:
        return
    for index, row in enumerate(iter_rows(table_path)):
        if index not in wanted:
            continue
        fresh = _cached_position(_position_drift(shaper, kern, features, row))
        recorded = store.serve(index, row.codepoints).position
        if fresh != recorded:
            raise SystemExit(
                f"the oracle position store served a stale verdict for {format_codepoints(row.codepoints)}: it holds {recorded}, and shaping the row again gives {fresh} — nothing this store holds can be trusted, so rerun with --fresh-oracle-cache and treat the difference as a staleness bug in the position key"
            )


class KernEvaluator:
    """Read-only evaluation of glyph_data/senior_quikscript_kerning.yaml over old-name glyph pairs, for adding sidecar kerns back before any baseline position diff. Family keys expand by name prefix against the supplied pair, mirroring the sidecar's documented expansion. Every answer is a pure function of the pair and the sidecar is read once, so pairs are memoized: the oracle asks about a few thousand distinct pairs across millions of slots, and the uncached scan over every sidecar rule was the bulk of the position channel."""

    def __init__(self, sidecar_path: Path):
        self._values: dict[tuple[str, str], int] = {}
        documents = [
            document
            for document in yaml.safe_load_all(Path(sidecar_path).read_text())
            if isinstance(document, dict)
        ]
        self.global_value = 0
        self.rules: list[dict] = []
        for document in documents:
            if "global" in document:
                self.global_value += document["global"].get("value", 0)
            else:
                self.rules.append(document)

    @staticmethod
    def _side_matches(glyph: str, names: list[str] | None, kind: str) -> bool:
        if names is None:
            return True
        for name in names:
            if kind == "exact" and glyph == name:
                return True
            if kind in ("family", "stance") and (glyph == name or glyph.startswith(name + ".")):
                return True
        return False

    def value_for(self, left_glyph: str, right_glyph: str) -> int:
        cached = self._values.get((left_glyph, right_glyph))
        if cached is None:
            cached = self._value_for(left_glyph, right_glyph)
            self._values[(left_glyph, right_glyph)] = cached
        return cached

    def _value_for(self, left_glyph: str, right_glyph: str) -> int:
        total = self.global_value
        for rule in self.rules:
            left_ok = (
                self._side_matches(left_glyph, rule.get("left_family"), "family")
                if "left_family" in rule
                else (
                    self._side_matches(left_glyph, rule.get("left_stance"), "stance")
                    if "left_stance" in rule
                    else self._side_matches(left_glyph, rule.get("left"), "exact") if "left" in rule else True
                )
            )
            if not left_ok:
                continue
            for prefix in rule.get("except_left", ()):
                if left_glyph == prefix or left_glyph.startswith(prefix + "."):
                    left_ok = False
            if not left_ok:
                continue
            if "right_group" in rule:
                right_ok = rule["right_group"] == "noentry" and right_glyph.endswith(".noentry")
            elif "right_family" in rule:
                right_ok = self._side_matches(right_glyph, rule["right_family"], "family")
            elif "right_stance" in rule:
                right_ok = self._side_matches(right_glyph, rule["right_stance"], "stance")
            elif "right" in rule:
                right_ok = self._side_matches(right_glyph, rule["right"], "exact")
            else:
                right_ok = True
            for prefix in rule.get("except_right", ()):
                if right_glyph == prefix or right_glyph.startswith(prefix + "."):
                    right_ok = False
            if right_ok:
                total += rule.get("value", 0)
        return total


ORACLE_AUDIT_HEADER = "config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew"


def oracle_audit_scratch(out_dir: Path) -> Path:
    """This oracle run's own directory for the audit's shards and its staging copy, beside the artifact they become. The pid in the name is what lets two runs share an out_dir — a `--gates-only` pass beside a cycle is the shape that happens here — without splicing rows into one another's shards or sweeping them out from under one another's concatenation. Nothing reads a directory in `rebuild/out/m1`, and the name misses `M1_ARTIFACT_NAMES` and every glob over the tables there, so what a killed run leaves behind cannot be mistaken for an artifact."""
    return Path(out_dir) / f"divergence-audit.parts.{os.getpid()}"


def oracle_audit_shard(scratch_dir: Path, config: str) -> Path:
    """Where one configuration's header-free audit rows go while the oracle runs. The pool is spawned, so a configuration's rows reach the parent as a file rather than as a pickled list: the audit is much the largest thing this build writes and it grows with every migrated letter, so carrying it through the pipe stood the whole of it up in the parent right where the phase's own peak already sat. `+` is already at home in a filename here, beside `baseline-ss02+ss03.subset.tsv.gz`."""
    return Path(scratch_dir) / f"{config}.part"


@contextmanager
def _staged_oracle_audit(out_dir: Path) -> Iterator[Path]:
    """Hand out the path this run writes its audit to, and let it become `divergence-audit.tsv` only once the last configuration has been written. A short audit is the failure to design against: it is a fingerprinted artifact its readers parse straight off disk, and a truncated one hashes differently rather than reading as stale, so it comes back as a fresh build with fewer units in it and every gate downstream stays green. Whatever kills an oracle partway — a Ctrl-C, the harness timeout the long builds here collect, a full disk — therefore has to leave the previous complete audit standing rather than a well-formed prefix of this one. The staging copy sits in this run's scratch directory so one sweep takes it and the shards together."""
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


def join_oracle_audit(out_dir: Path, scratch_dir: Path, configs: Iterable[str], expect_rows: int) -> None:
    """Stream the shards into `divergence-audit.tsv` behind the header, in the caller's configuration order, and promote nothing the workers' own counts do not vouch for. Shard to target is binary, so nothing is decoded on the way through and the parent's high-water is a copy buffer rather than an audit; the bytes are the ones `_compare_config` writes when it writes the file directly — the header line, then each row's line, each closed by a newline. The two refusals are what keep a partial concatenation from reading as a complete audit: every shard is found before a byte is copied, so a missing one is named rather than skipped, and the rows that land are counted against the `divergent_rows` the same workers reported, which is the only cross-check the parent can make between the counts that came home through the pipe and the bytes that came home on disk. The trade for keeping an audit off the heap is a second copy of it on disk while the join runs. Sweeping the scratch directory back off disk is the caller's job rather than this function's, because the failure worth cleaning up after is usually a worker's rather than this concatenation's."""
    scratch = Path(scratch_dir)
    shards = [(config, oracle_audit_shard(scratch, config)) for config in configs]
    missing = [config for config, path in shards if not path.is_file()]
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
    """Whether the process that named a scratch directory is still around. A pid this process may not signal, or one too large to be one at all, counts as running: the sweep only ever errs toward leaving another run's directory standing, and it must not raise on a name nobody here wrote."""
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
    """Take this run's scratch directory back off disk whether the oracle finished with it or died holding it, and any earlier run's whose process is gone along with it — between them the shards are a whole audit's worth of disk, and the `finally` that would have swept them is exactly what a kill skips. A directory whose pid is still alive belongs to another oracle and is left standing, which is the same reason the name carries a pid at all. Nothing here raises, so a sweep in a `finally` cannot displace the failure it is cleaning up after."""
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
    ledger,
    ink_identical_ids,
    shaper: "Shaper | IsolatedOverlayShaper | None",
    kern: "KernEvaluator | None",
    guard_verdicts: settle.FormationGuard | None,
    audit: TextIO | None,
    *,
    store: "oracle_cache.RowStore | None" = None,
    writer: "oracle_cache.RowWriter | None" = None,
    settle_memo: SettleMemoFile | None = None,
) -> OracleConfigResult:
    """One configuration's rows compared, classified, audited and position-checked. `guard_verdicts` is the crate's formation surface the walk forms under, and `None` only for the overlay configuration, whose walk forms nothing; `shaper` is likewise the overlay's synthetic shaper there, and a real one everywhere else."""
    result = OracleConfigResult(config=config)
    table_path = Path(subset_tables_dir) / f"baseline-{config}.subset.tsv.gz"
    if not table_path.exists():
        result.notes.append(f"{config}: subset table missing at {table_path}")
        return result
    # The oracle's rows are the same texts the belt sweeps, so they settle through the window memo the belt's walk shares by file (`settle_memo`) rather than from scratch a row at a time: a chunk of rows walks in waves, and each row is compared against the settled stream that walk already handed back rather than being walked a second time. Names go unread here — the row's own cells are what `_compare_row` reads — so the walk gets no glyph inventory; its keys are the same either way. The overlay configuration's walk is the registry's answer and settles nothing.
    walker: _SettledWindowWalk | IsolatedOverlayWalk
    if isolated_overlay_active(spec, features):
        walker = IsolatedOverlayWalk(spec)
    else:
        assert guard_verdicts is not None
        walker = _SettledWindowWalk(spec, features, {}, guard_verdicts, memo=settle_memo)
    config_started = time.perf_counter()
    rows = iter_rows(table_path)
    # Only the stale rows are walked; a served row's pre-position verdict comes back off the store and enters `_match_ledger` in the same state a fresh one does, and the chunk is re-read in table order afterward so the audit's bytes cannot depend on the partition. The verification samples ride on serving rather than on writing, because a pass that may read the store and not write one (`--gates-only`) is exactly a pass whose verdicts all came out of it. The position verdict is served by the same record under its own key, and only where this pass's ledger still sends the row through the channel; a row the ledger excludes carries its stored verdict forward unread, so a later ledger edit that admits it again finds it.
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
    first_row = 0
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
                # The row consulted alias entries belonging to a family no key it cites covers, so no key on this store could report them moved. Nothing in the live subset does this; a row that starts to is walked rather than served.
                fresh_at.append(offset)
                continue
            record = store.serve(index, row.codepoints)
            served_at[offset] = record
            if sample is not None:
                sample.offer(index, reachable)
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
            matches = _match_ledger(ledger, divergent) if divergent is not None else []
            if shaper is not None:
                topology_clean = divergent is None or not ({"ligation", "seam"} & set(divergent.kinds))
                class_claims_ink_identity = divergent is None or (
                    len(matches) == 1 and matches[0] in ink_identical_ids
                )
                if topology_clean and class_claims_ink_identity:
                    if carried is not None and store is not None and position_sample is not None:
                        assert not isinstance(position, oracle_cache._Unshaped)
                        drift = _served_position(position)
                        store.positions_served += 1
                        position_sample.offer(index, carried[1])
                    else:
                        drift = _position_drift(shaper, kern, features, row)
                        position, position_at = _cached_position(drift), this_pass
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
                        rematch = _match_ledger(ledger, divergent)
                        # A kern-attributable position residue is out of scope (the kern channel), so it never demotes a cell-grain row that already matched a single ink-identical class — that row's ink-identity claim survives the kern bookkeeping. A non-kern-attributable drift is a genuine ink shift and is allowed to override the prior match (so the position channel can chase it to ground).
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
                result.multi_matched.append((divergent, tuple(matches)))
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
        _verify_served_sample(spec, aliases, config, features, walker, table_path, store, sample)
    if store is not None and position_sample is not None and shaper is not None:
        _verify_served_positions(shaper, kern, features, table_path, store, position_sample)
    memo_line = walker.memo_line(config, walker.save_memo())
    if memo_line is not None:
        print(memo_line, file=sys.stderr, flush=True)
    print(
        f"[t] oracle {config} {time.perf_counter() - config_started:.2f}s rows={result.rows_compared} positions={result.positions_compared} served={served_rows} positions_served={result.positions_served}",
        file=sys.stderr,
        flush=True,
    )
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
) -> OracleConfigResult:
    """One config's oracle compare in its own process, its audit rows written to this configuration's shard under `audit_dir` so only counts ride the result home. The section 5.7 verdict surface is swept here, once per worker, exactly as the belt's worker sweeps its own — except by the overlay configuration's worker, which forms nothing and shapes through `IsolatedOverlayShaper` instead of HarfBuzz. The row cache is opened here rather than handed in already open for the same reason the shard is: a spawned worker inherits no file handles, and opening it on this side of the pipe is what keeps this path and the serial one byte-equal. `settle_memo` is the belt's shared settle memo file for this configuration, read and written on this side of the pipe for the same reason."""
    aliases = load_alias_map(alias_path)
    ledger = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in ledger if entry.get("ink_identical")}
    features = features_for_config(config)
    overlay = isolated_overlay_active(spec, features)
    shaper = _shaper_for(spec, font_path, overlay)
    kern = KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    shard = oracle_audit_shard(audit_dir, config)
    shard.parent.mkdir(parents=True, exist_ok=True)
    store, writer = open_row_cache(row_cache, spec, config)
    with ExitStack() as stack:
        audit = stack.enter_context(shard.open("w", encoding="utf-8", newline="\n"))
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
            None if overlay else kernel_exec.guard_sweep(spec),
            audit,
            store=store,
            writer=writer,
            settle_memo=settle_memo,
        )


def _shaper_for(
    spec: ResolvedSpec, font_path: Path | None, overlay: bool
) -> "Shaper | IsolatedOverlayShaper | None":
    """The position channel's shaper for one configuration: none without a font, the synthetic overlay shaper under an isolated overlay, HarfBuzz otherwise."""
    if font_path is None:
        return None
    if overlay:
        return IsolatedOverlayShaper(Path(font_path), spec)
    return Shaper(Path(font_path))


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
        report.multi_matched.extend(result.multi_matched)
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
    ledger = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in ledger if entry.get("ink_identical")}
    shapers: dict[bool, "Shaper | IsolatedOverlayShaper | None"] = {}
    kern = KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
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
                shapers[overlay] = _shaper_for(spec, font_path, overlay)
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
