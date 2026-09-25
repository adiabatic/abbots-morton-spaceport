"""Stores the baseline oracle's per-row verdicts between runs, keyed per rune family. After a rune edit, only the subset rows that can reach an edited family are compared again; the rest are served from the previous pass's store.

Each row has two verdicts, each under its own key. The row verdict is `conform._compare_row`'s result, a `DivergentRow` or None. `_compare_row(spec, aliases, config, features, row, settled)` takes no font and no shaper, so the row key covers no font: `M1.otf`, the GSUB fold and `glyph_data/senior_quikscript_kerning.yaml` are outside it, and a rune edit serves every row that reaches no edited family. The position verdict is `oracle_positions._position_drift`'s result for the same row: the drift descriptions and the kern-attribution flag, or None. Its per-family key (`position_family_keys`) adds the after font's compiled-glyph digest for that family (`fingerprint.after_font_glyph_digests`: decomposed outlines, advances, cursive anchors) to the row key. Its whole-store stamp (`position_keys`) covers `POSITION_CODE_PATHS`, the toolchain lock that pins uharfbuzz and fontTools, the font's non-family glyphs, cmap and GPOS wiring, and the kern sidecar. `_position_drift(shaper, kern, features, row)` takes no settled stream, so the position key needs no settlement input beyond the row key it contains.

The after font's GSUB wiring is in neither key, for the reason `fingerprint.after_font_glyph_digests` gives: a rune edit changes the lookup list on nearly every cycle, and the glyphs a row shapes to are already covered. A position is served only while the row's key is unchanged, so its settled cells are unchanged, and `gate:conform` checks every cycle that the compiled font selects the settlement's cells.

The position channel adds `"position"` to a row's `kinds` and `"position-drift"`, and optionally `"position-kern-attributable"`, to its `phenomena`, and changes nothing else, so a served row verdict and a fresh one enter the channel in the same state. The position verdict is stored before the ledger decides whether the row goes through the channel. A row the previous pass never shaped is recorded as `UNSHAPED` and is shaped when a ledger edit makes it eligible, and a shaped row's position verdict is written forward even when this pass's ledger excludes the row. If any of the facts above stops holding, the cache serves wrong verdicts without any error, and `STORE_FORMAT` must change.

Classification is outside both keys because it always runs: `classify_divergence` and `_match_compiled` run over every row on every pass, served or fresh, so `rebuild/m1-divergences.yaml` and every ledger predicate are applied again to the served verdict. A ledger-only edit therefore serves every row and still rewrites every `matched_entry`; this is the `run_m1 --gates-only` workflow. The classifier's code is outside both stamps for the same reason. It lives in `rebuild/pipeline/oracle.py`, which neither `conform.py` nor `oracle_positions.py` imports, so a classifier edit serves every row verdict and every position verdict. The producer (`_compare_row`, `_cell_deltas`, the walk, and the record codec) is in `conform.py`, which is always in `ORACLE_ROW_CODE_PATHS`. `rebuild/test_build_code_closure.py` checks that the roster names no comparison-side module.

The staleness test is per family and has no threshold. A row is served when no family it can reach has a key different from the one recorded for it. A row reaches the families of its codepoints plus every ligature rune whose components all appear among them. The ligature clause is needed because `rebuild/script.yaml` declares the ligature runes with a `sequence:` and no codepoint, yet `settle.form_ligatures` routes a `qsTea qsOy` window through `qsTea_qsOy.yaml`. A family key (`family_keys`) combines the family's prose-blind rune digest, the digests of the runes in its `spec_load.rune_closure`, and the alias map's entries for that family. A family present in only one of the recorded and current key maps counts as moved (`moved_families`).

The row key is not `review.unit_cache.family_content_keys`. That key includes a `glyphs` line over the after font's compiled outlines, which suits a cache of rendered review cards. The row verdict never reads a font, so that line would drop row verdicts on every font change. The position key includes the same per-family glyph digest because its verdict is shaped through that font, so a glyph edit re-shapes the rows that reach the family and re-derives none. `rebuild/review/` imports `rebuild/pipeline/` and not the reverse, so the parts both caches use (`fingerprint.rune_digests` here and `fingerprint.rune_explain_digests` in the unit cache, `fingerprint.after_font_glyph_digests`, `spec_load.rune_closure`, `spec_load.capability_features`, `spec_load.spec_structure_digest`) live in the pipeline.

The row store's whole-store stamp (`environment_stamp`) leaves out `M1.otf` and the kern sidecar, which the position stamp covers; `rebuild/m1-divergences.yaml`, because classification always runs again; `rebuild/m1-contact-allow.yaml`, which no oracle stage reads and which is not in `fingerprint.data_paths`; and the rune files, which the per-family keys cover. It covers everything that can change a verdict without changing a named family's key: the comparison's code closure, the other data inputs, the resolved spec structure and the capability-feature set, the engine's settlement flags, the configuration's feature set, and the subset table's bytes. The spec structure and the capability features cover cross-rune routes that the per-family keys cannot split: a predicate class gaining a member, a rune-local group, a ligature sequence, a feature unlock. For example, the kernel's specificity order (`rebuild/kernel-rs/src/specificity.rs`) expands a class reference to its whole member set before it compares records, so a rune joining a class can change the result in a window that contains no such rune. The position stamp is checked after the row stamp: a store whose row stamp moved is not loaded, and one whose position stamp alone moved serves rows and re-shapes positions.

Keying by settled window instead of by row was measured and rejected. A settled window spans six slots and a row has at most four letters, so windows name more letters than rows (287,280 of 499,989 distinct windows name four letters). With four edited runes, window keys served 45.4% of the work against row keys' 48.6%. Window keys cover only settlement, not the comparison, and a window's left slot is keyed on the previous window's output, so an edit changes keys downstream and produces misses. Added on top of a row store, window keys served 1.40% more lookups.

Records are positional and carry no row key: the subset table is the complete product over the M1 alphabet in canonical order, so the ordinal is the key. Each record starts with an anchor (`row_anchor`), a digest prefix of its row's codepoints that is checked on every serve. A store whose alignment was checked only by a whole-file digest and a row count would serve every row wrong with no error if the table changed under it; the anchor makes that an abort. `baseline_glyphs`, `baseline_seams` and `codepoints` are read from the table instead of stored, to keep the store small.

Two mechanisms stop a wrong record from being served indefinitely. They are needed because a served record is written again under the current stamp, so its provenance never ages it out, and `gate:conform` checks the font against a fresh settlement but never compares a cached verdict with a fresh one. First, each record keeps the pass at which each of its two verdicts was derived, not the pass that last wrote it, and `RowStore.due` and `RowStore.position_due` force a re-derivation once that age reaches `MAX_RECORD_AGE`. The renewal is spread by row ordinal, so one row in `MAX_RECORD_AGE` re-derives on every pass instead of the whole table on one pass. Second, `VerificationSample` draws up to `VERIFICATION_SAMPLE_PER_FAMILY` served rows for every family that served any, seeded on the stamp, the family and the pass's coverage ordinal, so the checked rows change from pass to pass. A pass that writes no store, such as `--gates-only`, advances that ordinal by the clock (see `RowStore`). The caller re-derives the sampled rows and compares whole records, and a second sample of the same shape re-shapes the rows whose positions were served. Because every family that served rows is sampled, a family whose records are all wrong is always caught, not with probability equal to the sample size over the rows served. A rune edited during a run produces that kind of error.

The key relies on one assumption that nothing else in the pipeline checks: every old compiled glyph name in a row belongs to a family the row's codepoints reach, so the alias entries a served row used are inside its own key. `unreachable_glyph_heads` lets the caller check it for each row.

Every failure falls back toward a full pass. `load_store` returns None for an absent, unreadable, format-mismatched, stamp-mismatched, digest-mismatched, short or trailer-less store, and None costs one uncached oracle pass. A store whose position stamp or position keys do not match loads with every position stale, which costs one pass of shaping.

The settle memo that the oracle shares with the conform belt (`conform.SettleMemoFile`) keys its entries with the same primitives, so they live here too. `settle_family_keys` is `family_keys` without the alias line, because the walk never reads the alias map. `settle_memo_stamp` is the row stamp without the format, subset and alias-boundary lines, because a memo entry is only a settlement. A memo entry's reach is read from its window's labels instead of a row's codepoints: the six slots' families, the rune a formed ligature label names, and every ligature rune whose components all appear among them. `StaleMask.bit_of` maps one label to its bit. `SettleMemoInputs` is the disk-derived half of both keys, read before the spec is loaded, as `run_m1.tables_inputs` is. The settlements are then at least as new as the content the keys name, so a rune edited during a run ends up under a key the next pass reports as moved, whichever side of the load the edit happened on.
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import os
import shutil
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Collection, Iterable, Mapping, Sequence

import yaml

from rebuild.pipeline import fingerprint, kernel_exec, spec_load
from rebuild.pipeline.fingerprint import EnvironmentStamp, moved_note
from rebuild.pipeline.model import ResolvedSpec
from rebuild.validation.rowmodel import Row, format_codepoints

STORE_FORMAT = "ams-m1-oracle-rows/2"
STORE_STEM = "oracle-rows"
SCRATCH_SUBDIR = "oracle-rows"
ROW_COUNT_TRAILER = "#rows"
ANCHOR_WIDTH = 12

MAX_RECORD_AGE = 20
VERIFICATION_SAMPLE_PER_FAMILY = 8

# The position channel's code that the row stamp does not cover: `_position_drift`, `_kern_normalized_positions`, the position record codec, `_verify_served_positions`, `KernEvaluator` and `_shaper_for`, which picks the shaper every stored position comes from. They share one module so this stamp works at module grain, like `ORACLE_ROW_CODE_PATHS`, and rebuild/test_oracle_code_closure.py walks that module's imports. The classifier in oracle.py is outside this stamp, so a classifier edit re-shapes no position. `Shaper` and `geometry.PIXEL` are in `ORACLE_ROW_CODE_PATHS`, so the row stamp covers them for both verdicts.
POSITION_CODE_PATHS = ("rebuild/pipeline/oracle_positions.py",)
# The lock that pins uharfbuzz and fontTools, which decide the positions the same font bytes shape to. It is hashed by its dependency pins (`fingerprint.lock_digest`), so a change to the project's own version block does not move the stamp. For the same reason `artifact_cycle.comparison_side_label` does not count it as a comparison-side input, so a toolchain bump rebuilds the tables and the font.
TOOLCHAIN_LOCK = "uv.lock"

# The two alias heads that name a boundary glyph instead of a family. `_compare_row` skips every name in `labels.BOUNDARY_GLYPH_NAMES` before it reads the alias map, so these entries never reach a verdict. They are hashed into the whole-store stamp instead of a family key, and `alias_family_digests` raises on any other head that has no rune digest.
BOUNDARY_ALIAS_HEADS = frozenset({"space", "periodcentered"})

# The modules that `_compare_row` and `_SettledWindowWalk` import, directly or transitively: the comparison and its settlement, the stream vocabulary (`labels`), the crate driver, the spec loader, the fingerprints the keys are computed from, and this module. rebuild/test_oracle_code_closure.py walks the import graph from conform.py on every contracts run and fails when a reachable module is missing here or a listed module is unreachable. conform.py holds the producer this cache serves: `_compare_row`, the walk, and the record codec. The classifier in oracle.py must stay out: if conform.py reached it, this test would fail until it was listed, and rebuild/test_build_code_closure.py fails when this list names it. The position channel in oracle_positions.py is `POSITION_CODE_PATHS`, stamped on top of this list, and the test also checks that it never reaches oracle.py, which would put the classifier in the position stamp. The witness stage's rule replay is in witness.py, which imports conform.py and emit_gsub.py, so emit_gsub.py is outside this closure and an emitter edit keeps the store. The four rebuild/tools modules cannot change a verdict: `kernel_exec` imports `memory_budget` to size its fan-out, `memory_budget` imports `peak_rss`, and the streams are byte-identical at any width; `fingerprint` imports `site_fonts.font_paths` and `lock_digest.lock_digest`. They are listed because the walk works at module grain and the test fails on an omission, and they change rarely, so the whole-store drops they cause are rare.
ORACLE_ROW_CODE_PATHS = (
    "rebuild/pipeline/conform.py",
    "rebuild/pipeline/fingerprint.py",
    "rebuild/pipeline/geometry.py",
    "rebuild/pipeline/kernel_exec.py",
    "rebuild/pipeline/kernel_io.py",
    "rebuild/pipeline/labels.py",
    "rebuild/pipeline/model.py",
    "rebuild/pipeline/oracle_cache.py",
    "rebuild/pipeline/settle.py",
    "rebuild/pipeline/spec_load.py",
    "rebuild/pipeline/table.py",
    "rebuild/tools/lock_digest.py",
    "rebuild/tools/memory_budget.py",
    "rebuild/tools/peak_rss.py",
    "rebuild/tools/site_fonts.py",
    "rebuild/validation/rowmodel.py",
)


def oracle_code_paths(repo_root: Path) -> list[Path]:
    """Return `ORACLE_ROW_CODE_PATHS` resolved against a checkout, plus the kernel crate's manifest, lock and every Rust source file. The crate decides every settlement this cache stores, and a hand-kept list of its modules would miss the next one added."""
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    return (
        [root / relative for relative in ORACLE_ROW_CODE_PATHS]
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
    )


def store_path(out_dir: Path, config: str) -> Path:
    """Return where a promoted store lives: beside the M1 artifacts but not one of them. The name matches neither `artifact_cycle.M1_ARTIFACT_NAMES` nor any glob over the tables there, so the file is hashed into no gate key and is not part of the artifacts-present check. It is a cache, and a cycle that deletes it loses only time."""
    return Path(out_dir) / f"{STORE_STEM}-{config}.tsv.gz"


def scratch_store_path(scratch_dir: Path, config: str, segment: int | None = None) -> Path:
    """Return where a store is staged while the oracle runs: a subdirectory of this run's pid-named audit scratch. `discard_oracle_audit_scratch` therefore removes a killed run's stores along with its shards, and `join_oracle_audit`'s missing-shard message lists one directory instead of a store per acceptance configuration. A row range of a cut configuration stages its `segment` beside the whole store's path, for `join_store_segments` to join."""
    name = f"{config}.tsv.gz" if segment is None else f"{config}.{segment}.tsv.gz"
    return Path(scratch_dir) / SCRATCH_SUBDIR / name


def _sha256_file(path: Path, digest: Callable[[Path], str] = fingerprint.file_sha256) -> str:
    try:
        return digest(Path(path))
    except OSError:
        return "missing"


def stamped_data_paths(repo_root: Path) -> list[Path]:
    """Return the data inputs the whole-store stamp hashes: `fingerprint.data_paths` without the rune files, the alias map, the divergence ledger and the kern sidecar, each of which is covered another way. The rune files are covered per family by the keys. The alias map's family heads are covered per family, and its two boundary heads have their own stamp line. The divergence ledger is read again by classification on every pass. The kern sidecar is in the position stamp, so an edit to it re-shapes every position and re-derives no row verdict. The contact allow-list is not in `fingerprint.data_paths`, so it needs no exclusion here. `artifact_cycle.oracle_cache_note` calls this to report what a moved input will cost the store before the oracle runs, so there is no second copy of this list to keep in step with the stamp."""
    root = Path(repo_root)
    runes = set(fingerprint.rune_paths(root))
    excluded = {
        root / "glyph_data" / "senior_quikscript_kerning.yaml",
        root / "rebuild" / "m1-aliases.yaml",
        root / "rebuild" / "m1-divergences.yaml",
    }
    return [
        path
        for path in fingerprint.data_paths(root)
        if path.is_file() and path not in runes and path not in excluded
    ]


def alias_family_digests(alias_path: Path, family_names: Collection[str]) -> dict[str, str]:
    """Group `rebuild/m1-aliases.yaml`'s entries by the part of each key before the first `.` and return one digest per head, over that group's sorted `key\\tdenotation` lines. Every head must be a name in `family_names` or one of `BOUNDARY_ALIAS_HEADS`. Any other head raises, because no key covers it, so a change to its entries could never be reported. The check is against the rune digests' names and not the script registry, because the registry has many families with no rune file and therefore no digest."""
    raw = yaml.safe_load(Path(alias_path).read_text()) or {}
    known = set(family_names)
    buckets: dict[str, list[str]] = {}
    for key in sorted(raw):
        head = str(key).split(".", 1)[0]
        if head not in known and head not in BOUNDARY_ALIAS_HEADS:
            raise ValueError(
                f"{Path(alias_path).name} entry {key!r} buckets to {head!r}, which has no family key — a family the alias map names but the rune digests do not can never be reported moved"
            )
        buckets.setdefault(head, []).append(f"{key}\t{json.dumps(raw[key], sort_keys=True)}")
    return {head: fingerprint.digest_lines(lines) for head, lines in buckets.items()}


def _reach_lines(name: str, digests: Mapping[str, str], closure: Mapping[str, frozenset[str]]) -> list[str]:
    """Return the `member\\tdigest` lines for the family and every rune in its `spec_load.rune_closure` (the runes reachable from it through ligature trailing components and `resolve.against` targets, transitively), which are the runes whose content its records read directly."""
    reach = sorted({name} | set(closure.get(name, frozenset())))
    return [f"{member}\t{digests.get(member, '-')}" for member in reach]


def family_keys(repo_root: Path, spec: ResolvedSpec, alias_path: Path) -> dict[str, str]:
    """Return, per rune family, the key a row's staleness test compares: a digest over the family's prose-blind rune digest, the digests of its `spec_load.rune_closure` (the runes reachable through the trailing component whose outgoing stroke a ligature keeps and through `resolve.against` targets, transitively), and the alias map's entries for the family, which the comparison reads to turn an old compiled name into a cell. A family with no alias entries records `-`, so its key changes when entries are added for it. The whole-store stamp covers every other cross-rune route."""
    digests = fingerprint.rune_digests(Path(repo_root))
    closure = spec_load.rune_closure(spec)
    aliases = alias_family_digests(Path(alias_path), digests.keys())
    keys: dict[str, str] = {}
    for name in sorted(digests):
        lines = _reach_lines(name, digests, closure)
        lines.append(f"alias\t{aliases.get(name, '-')}")
        keys[name] = fingerprint.digest_lines(lines)
    return keys


def position_family_keys(row_keys: Mapping[str, str], glyph_digests: Mapping[str, str]) -> dict[str, str]:
    """Return, per family, the key a row's position verdict compares: a digest of the family's row key and the after font's compiled-glyph digest for the family (`fingerprint.after_font_glyph_digests`). Including the row key makes a position stale wherever the row verdict is. Keys are computed over the union of both maps' names, with `-` for a name missing on one side, so a family with glyphs but no rune file, or the reverse, still has a key that can move."""
    return {
        name: fingerprint.digest_lines(
            (f"row\t{row_keys.get(name, '-')}", f"glyphs\t{glyph_digests.get(name, '-')}")
        )
        for name in sorted(set(row_keys) | set(glyph_digests))
    }


def position_keys(
    repo_root: Path, row_keys: Mapping[str, str], font_path: Path, kern_sidecar_path: Path | None
) -> tuple[dict[str, str], EnvironmentStamp]:
    """Return the position store's per-family keys (`position_family_keys`) and its whole-store position stamp, from one read of the font. The stamp covers the position channel's module (`POSITION_CODE_PATHS`), the toolchain lock's dependency pins, the digest `fingerprint.after_font_glyph_digests` returns for the font's non-family glyphs, cmap and GPOS wiring, and the kern sidecar's bytes, or `-` when there is no sidecar. The row stamp is not repeated here, because a store is loaded only when its row stamp matches."""
    root = Path(repo_root)
    glyph_digests, helpers = fingerprint.after_font_glyph_digests(Path(font_path))
    lines = (
        f"format\t{STORE_FORMAT}",
        f"position_code\t{fingerprint.hash_paths(root, [root / relative for relative in POSITION_CODE_PATHS])}",
        f"toolchain\t{_sha256_file(root / TOOLCHAIN_LOCK, fingerprint.lock_digest)}",
        f"font_helpers\t{helpers}",
        f"kern\t{'-' if kern_sidecar_path is None else _sha256_file(Path(kern_sidecar_path))}",
    )
    return position_family_keys(row_keys, glyph_digests), EnvironmentStamp(lines=lines)


@dataclass(frozen=True)
class SettleMemoInputs:
    """The disk-derived half of the settle memo's keys, computed before the spec they describe is loaded, as `run_m1.tables_inputs` is, so the settlements are at least as new as the content these keys name. `settle_memo_stamp` computes the spec-derived half (`spec_structure`, `capability_features`) from the loaded spec that settles."""

    rune_digests: Mapping[str, str]
    oracle_code: str
    data: str


def settle_memo_inputs(repo_root: Path) -> SettleMemoInputs:
    root = Path(repo_root)
    return SettleMemoInputs(
        rune_digests=fingerprint.rune_digests(root),
        oracle_code=fingerprint.hash_paths(root, oracle_code_paths(root)),
        data=fingerprint.hash_paths(root, stamped_data_paths(root)),
    )


def settle_family_keys(inputs: SettleMemoInputs, spec: ResolvedSpec) -> dict[str, str]:
    """Return, per rune family, the key a memo entry's staleness test compares: `family_keys` without the alias line. The walk that fills the memo never reads the alias map, and a settlement depends only on the rune files a window names and their `spec_load.rune_closure`."""
    closure = spec_load.rune_closure(spec)
    return {
        name: fingerprint.digest_lines(_reach_lines(name, inputs.rune_digests, closure))
        for name in sorted(inputs.rune_digests)
    }


def settle_memo_stamp(
    inputs: SettleMemoInputs, spec: ResolvedSpec, config: str, features: Collection[str]
) -> EnvironmentStamp:
    """Return the stamp over everything that can change a memoized settlement without changing a named family's key. It is `environment_stamp` without the lines only the comparison reads (the subset table and the alias map's boundary heads) and without the store format, which the memo file records in its own header. It uses `settlement_flags` and not `kernel_exec.enumeration_tokens`, because the walk settles windows one at a time and never enumerates, so deep-class grain cannot affect it."""
    lines = (
        f"config\t{config}",
        "features\t" + json.dumps(sorted(features)),
        f"oracle_code\t{inputs.oracle_code}",
        f"data\t{inputs.data}",
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
    )
    return EnvironmentStamp(lines=lines)


def environment_stamp(
    repo_root: Path,
    spec: ResolvedSpec,
    config: str,
    features: Collection[str],
    subset_path: Path,
    alias_path: Path,
    family_names: Collection[str],
) -> EnvironmentStamp:
    """Return the stamp over everything that can change a row's pre-position verdict without changing a named family's key. The caller passes `family_names` so the rune tree is read once per run and not once per configuration, and passes `features` (its `labels.features_for_config(config)`) so the stamp names the feature set the caller shapes under. The alias map enters only through its boundary heads; `family_keys` covers every family head. The module docstring lists what the stamp leaves out and why."""
    root = Path(repo_root)
    boundary = alias_family_digests(Path(alias_path), family_names)
    lines = (
        f"format\t{STORE_FORMAT}",
        f"config\t{config}",
        "features\t" + json.dumps(sorted(features)),
        f"oracle_code\t{fingerprint.hash_paths(root, oracle_code_paths(root))}",
        f"data\t{fingerprint.hash_paths(root, stamped_data_paths(root))}",
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        "alias_boundary\t"
        + fingerprint.digest_lines(
            f"{head}\t{boundary[head]}" for head in sorted(boundary) if head in BOUNDARY_ALIAS_HEADS
        ),
        f"subset\t{_sha256_file(Path(subset_path))}",
    )
    return EnvironmentStamp(lines=lines)


def moved_families(recorded: Mapping[str, str], current: Mapping[str, str]) -> frozenset[str]:
    """Return every family whose key differs between the recorded and current maps. A name present in only one map counts as moved, so a rune file that appears or disappears is a miss and never a match."""
    moved = set(current.keys() ^ recorded.keys())
    for name in current.keys() & recorded.keys():
        if current[name] != recorded[name]:
            moved.add(name)
    return frozenset(moved)


class StaleMask:
    """The per-row staleness test, as a bitmask over the families a row can reach. There is one bit per family, in sorted registry order. `mask_of` builds a row's mask from its codepoints, and `stale` returns whether any moved family is inside it: directly, for a family with a codepoint, or through the ligature clause, which applies only when every component of a moved ligature rune is in the row. The ligature clause is the rule `unit_cache.UnitKeyer._relevant_families` applies, read from `spec.registry.families[...].sequence` instead of a `_`-split of the name, because `settle.form_ligatures` routes on the sequence. A moved ligature rune also sets its own bit, because the settle memo keys windows on formed labels: a window whose slot holds `qsThey_qsUtter` names that rune and none of its components, so the component clause alone would serve it after an edit to the ligature's file. A moved family that the registry places by neither codepoint nor sequence makes every row stale, since over-invalidation is the safe direction."""

    def __init__(self, spec: ResolvedSpec, moved: Collection[str] = ()) -> None:
        families = spec.registry.families
        self.spec = spec
        self._bit: dict[str, int] = {name: 1 << index for index, name in enumerate(sorted(families))}
        self._family_of: dict[int, str] = {
            info.codepoint: name for name, info in families.items() if info.codepoint is not None
        }
        self._ligatures: dict[str, int] = {}
        for name, info in families.items():
            if not info.sequence:
                continue
            bits = 0
            for component in info.sequence:
                bits |= self._bit.get(component, 0)
            self._ligatures[name] = bits
        self.moved = frozenset(moved)
        self.everything = False
        symbols = 0
        ligatures: list[int] = []
        for name in sorted(self.moved):
            info = families.get(name)
            if info is not None and info.codepoint is not None:
                symbols |= self._bit[name]
            elif info is not None and info.sequence:
                symbols |= self._bit[name]
                ligatures.append(self._ligatures[name])
            else:
                self.everything = True
        self._stale_symbols = symbols
        self._stale_ligatures = tuple(sorted(set(ligatures)))
        self._reach: dict[int, tuple[str, ...]] = {}

    def mask_of(self, codepoints: Iterable[int]) -> int:
        mask = 0
        for codepoint in codepoints:
            name = self._family_of.get(codepoint)
            if name is not None:
                mask |= self._bit[name]
        return mask

    def bit_of(self, family: str) -> int:
        """Return the bit for one family name, or zero for a name the registry does not place (a boundary label, a window edge, or an unknown family). The settle memo uses this because its windows name families by label, not by codepoint. A formed ligature label maps to the ligature rune's own bit, which `stale` checks directly when that rune moved."""
        return self._bit.get(family, 0)

    def stale(self, mask: int) -> bool:
        if self.everything:
            return True
        if mask & self._stale_symbols:
            return True
        return any((mask & bits) == bits for bits in self._stale_ligatures)

    def families_of(self, mask: int) -> tuple[str, ...]:
        """Return every family a row with this mask can reach: the letters its codepoints name, plus each ligature rune whose components are all among them. Memoized on the mask, because many rows share a mask."""
        cached = self._reach.get(mask)
        if cached is None:
            names = [name for name, bit in self._bit.items() if mask & bit]
            names += [name for name, bits in self._ligatures.items() if bits and (mask & bits) == bits]
            cached = tuple(sorted(set(names)))
            self._reach[mask] = cached
        return cached


def unreachable_glyph_heads(glyph_names: Iterable[str], reachable: Collection[str]) -> tuple[str, ...]:
    """Return the `qs` family heads of a row's old compiled glyph names that are not in its reachable family set. A non-empty result means the row used alias entries that no key it compares covers, so the caller must derive the row instead of serving it. This checks the assumption the row key rests on (see the module docstring)."""
    known = set(reachable)
    heads = {name.split(".", 1)[0] for name in glyph_names}
    return tuple(sorted(head for head in heads if head.startswith("qs") and head not in known))


@dataclass(frozen=True, slots=True)
class CachedRow:
    """One row's pre-position comparison verdict: `conform._compare_row`'s `DivergentRow` without the three fields the subset table holds (`codepoints`, `baseline_glyphs`, `baseline_seams`) and without `config`, which the store records. It holds no provenance, so `==` is verdict equality, which the verification sample relies on. The pass a record was derived at is kept separately, in `RowStore.age`."""

    kinds: tuple[str, ...]
    position: int
    new_cells: tuple[str, ...]
    new_seams: tuple[str, ...]
    phenomena: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CachedPosition:
    """One row's position-channel verdict when the row drifted: `oracle_positions._position_drift`'s drift descriptions, which the audit prints as a position-only row's new cells, and whether every drifted slot follows a kern-attributable one. A row whose positions matched is stored as `None`, and a row the channel never shaped as `UNSHAPED`. Only a `CachedPosition` or `None` may be served, and `==` over them is the verification sample's check."""

    drifts: tuple[str, ...]
    kern_attributable: bool


class _Unshaped:
    """The position record of a row the previous pass never shaped, because the row was kept out of the channel (a ligation or seam divergence, or a divergence without exactly one ledger match that claims identical ink) or no font was open. It is distinct from `None`, a shaped row that matched, so that a row the channel never saw is not counted as clean."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSHAPED"


UNSHAPED = _Unshaped()
PositionVerdict = CachedPosition | None | _Unshaped


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One row's full record in the store: both verdicts and the pass each was derived at. The ages are per verdict because the two re-derive on different keys: a font compile that changes a family's outlines re-shapes its rows' positions while every row verdict is still served."""

    row: CachedRow | None
    row_age: int
    position: PositionVerdict
    position_age: int


def _split(text: str, separator: str) -> tuple[str, ...]:
    return tuple(text.split(separator)) if text else ()


def row_anchor(codepoints: Sequence[int]) -> str:
    """Return a record's alignment anchor: the first `ANCHOR_WIDTH` hex characters of the SHA-256 of the row's canonical codepoint string. It is checked on every serve, so a table that was refiltered or reordered under the store causes an abort instead of every row being served wrong."""
    return hashlib.sha256(format_codepoints(tuple(codepoints)).encode()).hexdigest()[:ANCHOR_WIDTH]


def encode_record(
    codepoints: Sequence[int],
    cached: CachedRow | None,
    derived_at_pass: int,
    position: PositionVerdict = UNSHAPED,
    position_at_pass: int = 0,
) -> str:
    """Return one store line: the anchor; then `-` for a clean row, or `P` and the row verdict's five fields; then `?` for a position never shaped, `-` for one that matched, or `D`, the `|`-joined drift descriptions and `k` or `n` for the kern-attribution flag; then the two derivation passes, the row verdict's first. `|` separates cells and `,` separates the token tuples, as in `divergence-audit.tsv`. The drift descriptions contain commas, so they are joined on `|` alone and the flag has its own field."""
    fields = [row_anchor(codepoints)]
    if cached is None:
        fields.append("-")
    else:
        fields += [
            "P",
            ",".join(cached.kinds),
            str(cached.position),
            "|".join(cached.new_cells),
            ",".join(cached.new_seams),
            ",".join(cached.phenomena),
        ]
    if position is UNSHAPED:
        fields.append("?")
    elif position is None:
        fields.append("-")
    else:
        assert isinstance(position, CachedPosition)
        fields += ["D", "|".join(position.drifts), "k" if position.kern_attributable else "n"]
    fields += [str(derived_at_pass), str(position_at_pass)]
    return "\t".join(fields)


def decode_record(line: str) -> StoredRecord:
    fields = line.split("\t")
    at = 1
    row: CachedRow | None = None
    if fields[at] == "-":
        at += 1
    else:
        row = CachedRow(
            kinds=_split(fields[at + 1], ","),
            position=int(fields[at + 2]),
            new_cells=_split(fields[at + 3], "|"),
            new_seams=_split(fields[at + 4], ","),
            phenomena=_split(fields[at + 5], ","),
        )
        at += 6
    position: PositionVerdict
    tag = fields[at]
    at += 1
    if tag == "?":
        position = UNSHAPED
    elif tag == "-":
        position = None
    else:
        position = CachedPosition(drifts=_split(fields[at], "|"), kern_attributable=fields[at + 1] == "k")
        at += 2
    return StoredRecord(row=row, row_age=int(fields[at]), position=position, position_age=int(fields[at + 1]))


class RowStore:
    """One row range's part of a configuration's loaded store: the records of rows `[first_row, first_row + len(ages))`, held as one buffer and three packed arrays (each record's offset into the buffer and its two ages), indexed by `index - first_row`, so a worker holds only its own range's records. Every method takes the row's absolute ordinal in the table, so a cut configuration's ranges are self-contained segments of one store (rebuild/pipeline/oracle.py). An index outside the range raises instead of serving another row's record, since Python's arrays would otherwise read a negative index from the end. `rows` is the table's row count, not the range's. When it is not given it is the range's end, which is the table's count when the range runs to the table's end. The oracle treats an index at or above `rows` as fresh (`oracle._compare_config`), so a range given its own length as the count would re-derive every row above its range and write a store an uncut pass never writes. Only the rows a pass serves are decoded, and the staleness scan reads only the age arrays, never the buffer.

    `rotation` is set by a pass that will not write a store. The renewal slice and the verification sample both advance with the pass ordinal, and the ordinal advances only when a store is written. A read-only pass run repeatedly, which is how `--gates-only` is used when re-adjudicating the ledger, would otherwise re-derive the same rows and check the same sample every time. A writing pass leaves this at zero and uses its own ordinal. A read-only pass sets it from the clock, so the rows covered change even when nothing on disk does.
    """

    def __init__(
        self,
        environment: EnvironmentStamp,
        recorded_lines: tuple[str, ...],
        recorded_keys: dict[str, str],
        subset_digest: str,
        pass_ordinal: int,
        mask: StaleMask,
        blob: bytes | bytearray,
        offsets: "array[int]",
        ages: "array[int]",
        rotation: int = 0,
        position_mask: StaleMask | None = None,
        position_ages: "array[int] | None" = None,
        first_row: int = 0,
        rows: int | None = None,
    ) -> None:
        self.environment = environment
        self.recorded_lines = recorded_lines
        self.recorded_keys = recorded_keys
        self.subset_digest = subset_digest
        self.pass_ordinal = pass_ordinal
        self.mask = mask
        self.rotation = rotation
        self.served = 0
        self.positions_served = 0
        self._blob = blob
        self._offsets = offsets
        self._ages = ages
        self.first_row = first_row
        self._rows = first_row + len(ages) if rows is None else rows
        if position_mask is None:
            position_mask = StaleMask(mask.spec, mask.moved)
            position_mask.everything = True
        self.position_mask = position_mask
        self._position_ages = ages if position_ages is None else position_ages

    @property
    def rows(self) -> int:
        """The table's row count, as recorded in the store's trailer, whatever range this store holds."""
        return self._rows

    @property
    def stop_row(self) -> int:
        """One past the last row this store holds a record for."""
        return self.first_row + len(self._ages)

    @property
    def moved(self) -> frozenset[str]:
        return self.mask.moved

    def _at(self, index: int) -> int:
        """Return the arrays' position for the table's row `index`, raising `IndexError` for a row outside this store's range."""
        at = index - self.first_row
        if at < 0 or at >= len(self._ages):
            raise IndexError(
                f"row {index} is outside the range [{self.first_row}, {self.stop_row}) this oracle row store holds"
            )
        return at

    def age(self, index: int) -> int:
        """Return the pass this row's verdict was derived at. A pass that only serves the verdict writes this value forward unchanged, so it measures how long the verdict has stood, not how long the file has."""
        return self._ages[self._at(index)]

    def position_age(self, index: int) -> int:
        return self._position_ages[self._at(index)]

    @property
    def coverage_ordinal(self) -> int:
        """The ordinal the renewal slice and the verification sample are drawn against. It equals `pass_ordinal + 1`, the ordinal the writer records, except on a pass that writes nothing (see `rotation`)."""
        return self.pass_ordinal + 1 + self.rotation

    def _due(self, index: int, age: int) -> bool:
        if self.pass_ordinal + 1 - age >= MAX_RECORD_AGE:
            return True
        return self.coverage_ordinal % MAX_RECORD_AGE == index % MAX_RECORD_AGE

    def due(self, index: int) -> bool:
        """Whether this row's verdict must be re-derived regardless of its families. The ordinal clause selects one row in `MAX_RECORD_AGE` on every pass, so no verdict stands that many passes and the renewal is spread across passes. The age clause is a second check that catches a store whose pass ordinals skipped. Only the slice uses the rotated ordinal. The age arithmetic uses the true one, because a rotated ordinal would make every record look older than the cap and re-derive the whole table."""
        return self._due(index, self._ages[self._at(index)])

    def position_due(self, index: int) -> bool:
        """`due` over the position verdict's own age. The same slice re-derives both of a row's verdicts on the same pass, and the age clause reads the pass the position was shaped at."""
        return self._due(index, self._position_ages[self._at(index)])

    def stale(self, index: int, mask: int) -> bool:
        return self.mask.stale(mask) or self.due(index)

    def position_stale(self, index: int, mask: int) -> bool:
        """Whether this row's position verdict must be shaped again: wherever its row verdict must be re-derived (the position key includes the row key, and a served position over a fresh settlement would describe the previous pass's cells), or wherever a family it reaches changed its glyphs, the position stamp moved, or the renewal clause is due."""
        return self.stale(index, mask) or self.position_mask.stale(mask) or self.position_due(index)

    def serve(self, index: int, codepoints: Sequence[int]) -> StoredRecord:
        """Return this row's full record after checking its anchor. The caller decides which of the two verdicts the keys allow it to use, and counts a served position in `positions_served` itself. A mismatched anchor is not a miss: it means the table under this store was replaced or reordered and every other record is wrong in the same way, so it exits."""
        at = self._at(index)
        start = self._offsets[at]
        end = self._offsets[at + 1] - 1
        anchor = self._blob[start : start + ANCHOR_WIDTH].decode("ascii")
        if anchor != row_anchor(codepoints):
            raise SystemExit(
                f"the oracle row cache is misaligned at row {index}: the record is anchored to {anchor} where the table holds {format_codepoints(tuple(codepoints))} — the store describes a different table and nothing it holds can be served"
            )
        self.served += 1
        return decode_record(self._blob[start:end].decode("utf-8"))


def read_header(path: Path) -> dict | None:
    """Return a store's header alone, for a caller that reports what moved without loading the store. `None` when nothing readable is there."""
    try:
        with gzip.open(Path(path), "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration, zlib.error:
        return None
    return header if isinstance(header, dict) else None


def position_stale_mask(
    spec: ResolvedSpec,
    moved: Collection[str],
    header: Mapping,
    position_environment: EnvironmentStamp | None,
    current_position_keys: Mapping[str, str] | None,
) -> StaleMask:
    """Return the position channel's staleness mask for a loaded store. When the store's position stamp matches this run's, the mask covers the families whose position keys moved. It covers every row (`everything`) when either side has no position keys or stamp, the stamp moved, or the header's position fields cannot be read. `moved` is the row channel's moved set and is always included, so a family stale for row verdicts is stale for positions too."""
    everything = StaleMask(spec, moved)
    everything.everything = True
    if position_environment is None or current_position_keys is None:
        return everything
    try:
        recorded_lines = header.get("position_environment")
        recorded_keys = header.get("position_keys")
        if not isinstance(recorded_lines, list) or not isinstance(recorded_keys, dict):
            return everything
        if tuple(recorded_lines) != position_environment.lines:
            return everything
        keys = {str(name): str(value) for name, value in recorded_keys.items()}
    except TypeError, ValueError:
        return everything
    return StaleMask(spec, set(moved) | moved_families(keys, current_position_keys))


def load_store(
    path: Path,
    environment: EnvironmentStamp,
    subset_digest: str,
    spec: ResolvedSpec,
    current_keys: Mapping[str, str],
    rotation: int = 0,
    position_environment: EnvironmentStamp | None = None,
    current_position_keys: Mapping[str, str] | None = None,
    *,
    first_row: int = 0,
    stop_row: int | None = None,
) -> RowStore | None:
    """Return the previous pass's records for rows `[first_row, stop_row)` of one configuration (`stop_row` None means the table's end), or `None` when the store cannot be trusted: absent, unreadable, format- or stamp-mismatched, written against another subset table, or missing its row-count trailer. `None` costs one uncached oracle pass, so every parse failure returns `None`, including `zlib.error`, which a corrupt deflate body raises instead of `OSError`. The file is read to its end whatever the range: the trailer is the last line and holds the count the store is checked against, and every record's two ages are parsed in range or out, so all ranges of one configuration agree on whether the store loads. Only the range's records are kept, in one buffer with three packed arrays. A range whose kept bytes exceed the packed offsets' width returns `None` like any other parse failure. The position stamp and keys do not affect loading: `position_stale_mask` decides which position verdicts may be served. `rotation` is passed to the store unread; see `RowStore`."""
    store_file = Path(path)
    if not store_file.is_file():
        return None
    try:
        with gzip.open(store_file, "rb") as stream:
            header = json.loads(stream.readline())
            if header["format"] != STORE_FORMAT:
                return None
            recorded_lines = tuple(header["environment"])
            if recorded_lines != environment.lines:
                return None
            if header["subset_digest"] != subset_digest:
                return None
            recorded_keys = {str(name): str(value) for name, value in header["family_keys"].items()}
            pass_ordinal = int(header["pass_ordinal"])

            blob = bytearray()
            offsets: array[int] = array("I", [0])
            ages: array[int] = array("i")
            position_ages: array[int] = array("i")
            seen = 0
            pending = stream.readline()
            if not pending:
                return None
            for line in stream:
                end = len(pending) - 1
                last_tab = pending.rindex(b"\t", 0, end)
                position_age = int(pending[last_tab + 1 : end])
                age = int(pending[pending.rindex(b"\t", 0, last_tab) + 1 : last_tab])
                if seen >= first_row and (stop_row is None or seen < stop_row):
                    blob += pending
                    offsets.append(len(blob))
                    ages.append(age)
                    position_ages.append(position_age)
                seen += 1
                pending = line
            if not pending.endswith(b"\n"):
                return None
            trailer = pending[:-1].decode("utf-8").split("\t")
            if trailer[0] != ROW_COUNT_TRAILER:
                return None
            if seen != int(trailer[1]):
                return None
    except OSError, EOFError, ValueError, KeyError, IndexError, TypeError, OverflowError, zlib.error:
        return None
    moved = moved_families(recorded_keys, current_keys)
    return RowStore(
        environment=environment,
        recorded_lines=recorded_lines,
        recorded_keys=recorded_keys,
        subset_digest=subset_digest,
        pass_ordinal=pass_ordinal,
        mask=StaleMask(spec, moved),
        blob=blob,
        offsets=offsets,
        ages=ages,
        rotation=rotation,
        position_mask=position_stale_mask(spec, moved, header, position_environment, current_position_keys),
        position_ages=position_ages,
        first_row=first_row,
        rows=seen,
    )


def next_pass_ordinal(store: RowStore | None) -> int:
    return 0 if store is None else store.pass_ordinal + 1


def store_header(
    environment: EnvironmentStamp,
    subset_digest: str,
    pass_ordinal: int,
    family_keys: Mapping[str, str],
    position_environment: EnvironmentStamp | None = None,
    position_keys: Mapping[str, str] | None = None,
) -> bytes:
    """Return a store's first line: the format, the two stamps and the two key maps `load_store` compares, the pass ordinal and the subset digest, as one JSON object with sorted keys, so two writers over the same inputs write the same bytes."""
    header = {
        "format": STORE_FORMAT,
        "environment": list(environment.lines),
        "family_keys": {name: family_keys[name] for name in sorted(family_keys)},
        "pass_ordinal": pass_ordinal,
        "position_environment": (None if position_environment is None else list(position_environment.lines)),
        "position_keys": (
            None if position_keys is None else {name: position_keys[name] for name in sorted(position_keys)}
        ),
        "subset_digest": subset_digest,
    }
    return (json.dumps(header, sort_keys=True) + "\n").encode()


def _open_member(raw: IO[bytes]) -> gzip.GzipFile:
    """Open one gzip member over `raw` with the settings every member of a store uses: the mtime fixed at zero, so identical passes write identical bytes, and compression level 1, because a store is written once and read once per run and a higher level would add seconds to every cycle."""
    return gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1)


class RowWriter:
    """One configuration's store being written, one record per subset row in table order. The row count is a trailer instead of a header field, so the header can be written before the count is known, and a truncated store has no trailer and fails to load. The file is written to a temporary path and moved into place with `os.replace`, so a store on disk is always complete. A `segment` writer writes one row range of a cut configuration as its own gzip member, with records only and no header or trailer, for `join_store_segments` to place between a header member and a trailer member once every range has finished. A store with one range is written whole."""

    def __init__(
        self,
        path: Path,
        environment: EnvironmentStamp,
        subset_digest: str,
        pass_ordinal: int,
        family_keys: Mapping[str, str],
        position_environment: EnvironmentStamp | None = None,
        position_keys: Mapping[str, str] | None = None,
        segment: bool = False,
    ) -> None:
        self.path = Path(path)
        self.pass_ordinal = pass_ordinal
        self.rows = 0
        self.segment = segment
        self._scratch = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._raw: IO[bytes] = self._scratch.open("wb")
        self._stream = _open_member(self._raw)
        if not segment:
            self._stream.write(
                store_header(
                    environment, subset_digest, pass_ordinal, family_keys, position_environment, position_keys
                )
            )

    def append(
        self,
        codepoints: Sequence[int],
        cached: CachedRow | None,
        derived_at_pass: int,
        position: PositionVerdict = UNSHAPED,
        position_at_pass: int = 0,
    ) -> None:
        """Record one row, divergent or clean. Every subset row gets a record, in table order, because the ordinal is the key and a clean row with no age could never be renewed. `derived_at_pass` is the pass the row verdict was computed at: `RowStore.age(index)` for a verdict this pass only served, `self.pass_ordinal` for one it derived. `position_at_pass` is the same for the position verdict, from `RowStore.position_age(index)` when it was served. Recording this pass for a served verdict would reset its age and defeat the renewal cap."""
        self._stream.write(
            (encode_record(codepoints, cached, derived_at_pass, position, position_at_pass) + "\n").encode()
        )
        self.rows += 1

    def close(self) -> None:
        if not self.segment:
            self._stream.write(f"{ROW_COUNT_TRAILER}\t{self.rows}\n".encode())
        self._stream.close()
        self._raw.close()
        os.replace(self._scratch, self.path)

    def abandon(self) -> None:
        """Delete a partly written store without promoting it, so a failed configuration leaves nothing for the next pass to load."""
        try:
            self._stream.close()
            self._raw.close()
        except OSError, ValueError:
            pass
        Path(self._scratch).unlink(missing_ok=True)

    def __enter__(self) -> "RowWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abandon()


def join_store_segments(
    scratch_dir: Path,
    config: str,
    segments: int,
    environment: EnvironmentStamp,
    subset_digest: str,
    pass_ordinal: int,
    family_keys: Mapping[str, str],
    rows: int,
    position_environment: EnvironmentStamp | None = None,
    position_keys: Mapping[str, str] | None = None,
) -> Path | None:
    """Assemble one cut configuration's staged store from the `segments` its row ranges wrote, and return its path. The file is a header member, then each segment's compressed bytes copied in row order, then a trailer member counting `rows`. It is written through a temporary file and `os.replace` to `scratch_store_path(scratch_dir, config)`, where `promote_stores` finds it as it would an uncut configuration's store. Nothing is decompressed, so the parent's cost is a copy. The result is a multi-member gzip stream and is not byte-identical to the single-member store the same records would make, but its decompressed content is. `gzip.open(...)` reads across members, empty ones included, so `load_store`'s trailer check, anchor check and ages work unchanged, and a store missing a segment's tail still loads as `None`. The different framing is safe because nothing hashes this file (see `store_path`). Returns `None`, and stages nothing, when a segment is missing."""
    paths = [scratch_store_path(scratch_dir, config, segment) for segment in range(segments)]
    if any(not path.is_file() for path in paths):
        return None
    target = scratch_store_path(scratch_dir, config)
    staged = target.with_name(target.name + ".tmp")
    with staged.open("wb") as raw:
        with _open_member(raw) as head:
            head.write(
                store_header(
                    environment, subset_digest, pass_ordinal, family_keys, position_environment, position_keys
                )
            )
        for path in paths:
            with path.open("rb") as segment_file:
                shutil.copyfileobj(segment_file, raw, 1 << 20)
        with _open_member(raw) as tail:
            tail.write(f"{ROW_COUNT_TRAILER}\t{rows}\n".encode())
    os.replace(staged, target)
    return target


def promote_stores(scratch_dir: Path, out_dir: Path, configs: Iterable[str]) -> list[str]:
    """Move a finished run's staged stores into place beside the M1 artifacts, all of them or none, and return the configurations promoted. The caller calls this only after `join_oracle_audit` has written the audit and after it has checked that neither the stamps nor any family key moved during the run. A store recorded under digests the run did not build from would be served as current on every later pass."""
    staged = [(config, scratch_store_path(scratch_dir, config)) for config in configs]
    if any(not path.is_file() for _, path in staged):
        return []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for config, path in staged:
        os.replace(path, store_path(out_dir, config))
    return [config for config, _ in staged]


def discard_stores(out_dir: Path, configs: Iterable[str]) -> None:
    """Delete the promoted stores. `--fresh-oracle-cache` does this before a pass that will write replacements, so the next pass trusts only what that pass wrote. A pass that may not write a store never calls this: skipping the read already costs that pass a full derivation, and deleting the store would cost the next pass one as well."""
    for config in configs:
        store_path(out_dir, config).unlink(missing_ok=True)


def _mix64(value: int) -> int:
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
    value = (value ^ (value >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
    return value ^ (value >> 31)


class VerificationSample:
    """The stratified sample of served rows that the caller checks again, drawn while the pass runs. Every family that served at least one row contributes up to `per_family` rows, chosen by the smallest mix of a per-family seed with the row's ordinal. The draw therefore depends only on the store's stamp, the family and the pass ordinal, not on the order or the number of rows offered. Seeding on the pass ordinal makes the checked rows change from pass to pass. Because every family that served rows is sampled, a family whose records are all wrong is always caught, not with probability equal to the sample size over the rows served. A rune edited while the oracle runs produces that error. Each kept entry holds the `Row` it was offered with, so `sampled_rows()` returns the parsed rows in index order and the verifier does not re-read the table. For the row sample the caller re-walks those rows and compares each served `CachedRow` with a fresh `_compare_row` result, and for the position sample it shapes them again. A heap entry is `(score, index, row)` and `Row` has no ordering. An index is offered once per family per pass, so no two entries tie on the first two fields and the comparison never reaches the row."""

    def __init__(
        self, stamp: str, pass_ordinal: int, per_family: int = VERIFICATION_SAMPLE_PER_FAMILY
    ) -> None:
        self.stamp = stamp
        self.pass_ordinal = pass_ordinal
        self.per_family = per_family
        self._seeds: dict[str, int] = {}
        self._kept: dict[str, list[tuple[int, int, Row]]] = {}

    def _seed(self, family: str) -> int:
        seed = self._seeds.get(family)
        if seed is None:
            digest = hashlib.sha256(f"{self.stamp}\t{family}\t{self.pass_ordinal}".encode()).digest()
            seed = int.from_bytes(digest[:8], "big")
            self._seeds[family] = seed
        return seed

    def offer(self, index: int, row: Row, families: Iterable[str]) -> None:
        if self.per_family <= 0:
            return
        for family in families:
            kept = self._kept.setdefault(family, [])
            score = -_mix64(self._seed(family) ^ (index * 0x9E3779B97F4A7C15 & 0xFFFFFFFFFFFFFFFF))
            if len(kept) < self.per_family:
                heapq.heappush(kept, (score, index, row))
            elif score > kept[0][0]:
                heapq.heapreplace(kept, (score, index, row))

    def by_family(self) -> dict[str, tuple[int, ...]]:
        return {
            family: tuple(sorted(index for _score, index, _row in kept))
            for family, kept in self._kept.items()
        }

    def indexes(self) -> tuple[int, ...]:
        return tuple(sorted({index for kept in self._kept.values() for _score, index, _row in kept}))

    def sampled_rows(self) -> tuple[tuple[int, Row], ...]:
        rows = {index: row for kept in self._kept.values() for _score, index, row in kept}
        return tuple(sorted(rows.items()))
