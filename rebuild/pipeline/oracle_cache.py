"""The persisted per-row oracle cache (issue 24): the section 13.1 baseline oracle's pre-position verdicts carried across runs, keyed per family, so an edit to any number of runes re-derives the subset rows that can reach one of them and serves the rest from the previous pass.

Two verdicts are cached per row, each under its own key, and the split between them is the whole design. The row verdict is exactly `conform._compare_row`'s answer — the `DivergentRow | None` a row's settled stream and the alias map produce — and its key is font-blind: `_compare_row(spec, aliases, config, features, row, settled)` takes no font and no shaper, so `M1.otf`, the GSUB fold and `glyph_data/senior_quikscript_kerning.yaml` are outside it entirely, which is what lets a rune edit serve every row that reaches no edited family. The position verdict is `oracle._position_drift`'s answer for the same row — the drift descriptions and the kern-attribution flag, or the clean answer — stored beside the row verdict under a second key that adds exactly what shaping reads. Per family, `position_family_keys` folds the after font's compiled glyphs for that family (`fingerprint.after_font_glyph_digests`: decomposed outlines, advances, cursive anchors) onto the row key; whole store, `position_stamp` carries the position channel's own module, the toolchain lock that pins uharfbuzz and fontTools, the font's non-family glyphs, cmap and GPOS wiring, and the kern sidecar. `_position_drift(shaper, kern, features, row)` takes no settled stream, which is why the position key need not fold the settlement beyond the row key it embeds. The after font's GSUB wiring is deliberately in neither key, and the argument is `fingerprint.after_font_glyph_digests`' argument at row grain: a position is served only when the row's own key still stands, so its settled cells are unchanged, and `gate:conform` proves every cycle that the compiled font selects exactly the settlement's cells — so which glyph a served row shapes to is a function of cells the row key covers and of glyph content the per-family digests cover. The review surface's unit cache measured the alternative: folding the GSUB wiring into a stamp produced "a store that never once served a unit", because a rune edit moves the lookup list on essentially every cycle. The position channel's only mutation of a row is appending `"position"` to `kinds` and `("position-drift",)` — plus optionally `"position-kern-attributable"` — to `phenomena`, so a served row and a fresh one enter that channel in the same state, and the verdict is stored raw — before the ledger decides whether the row is eligible for the channel at all — so a row the previous pass never shaped is recorded as `UNSHAPED` and shaped when a ledger edit makes it eligible, while one it did shape is served whether or not this pass's ledger still asks. If any of those signature facts stops holding, this cache is silently dishonest and its format version must move.

Classification is outside the key by construction rather than by argument: `classify_divergence` and `_match_ledger` run over every row on every pass, served or fresh, so `rebuild/m1-divergences.yaml` and every ledger predicate are re-applied to the served verdict. A pure ledger edit therefore serves every row and still rewrites every `matched_entry` — the workflow `run_m1 --gates-only` advertises, and the one this cache actually pays for. The classifier's *code* is outside it for the same reason, since issue 81 moved `classify_divergence`, the predicates and `_match_ledger` into `rebuild/pipeline/oracle.py`, a module `conform.py` never imports: the producer — `_compare_row`, `_cell_deltas`, the walk, and the record codec — stays in `conform.py`, which stays in `ORACLE_ROW_CODE_PATHS` permanently, and `rebuild/test_build_code_closure.py` holds the roster to never naming the comparison side.

The staleness test is per family and has no threshold. A row is served when no family it can reach carries a key different from the one recorded beside it; reachable means the families of the row's codepoints plus every ligature rune all of whose components appear among them. That second clause is not decoration: `rebuild/script.yaml` declares the ligature runes with a `sequence:` and no codepoint, so "the families in the row's codepoints" never names one, yet `settle.form_ligatures` routes a `qsTea qsOy` window through `qsTea_qsOy.yaml`. A family key is the family's prose-blind rune digest joined with the digests of its static `resolve.against` closure and with the alias map's entries for that family — the three routes by which a named family's own content reaches a row's verdict. A cited family absent from the recorded keys is a miss, never a match: absence compares equal under `.get()`, and the registry holds far more families than the rune tree holds files.

The row key is deliberately not `review.unit_cache.family_content_keys`, and the divergence is the point rather than an oversight. That key folds a `glyphs` line over the after font's compiled outlines, which is right for a cache whose product is a rendered review card and wrong for one whose product never reads a font — folding it into the row key would drop the row verdicts on exactly the workflow the store exists for. The position key is the analogue that does fold it, over the same per-family digest, because its product is shaped through that font; a glyph edit therefore re-shapes the rows reaching the family and re-derives none. The import direction settles where the sibling lives: `rebuild/review/` imports `rebuild/pipeline/`, never the reverse, so the shared halves (`fingerprint.rune_digests`, `fingerprint.after_font_glyph_digests`, `spec_load.rune_closure`, `spec_load.capability_features`, `spec_load.spec_structure_digest`) live in the pipeline and both callers reach for them there.

Deliberately outside the row store's whole-store stamp, each for a reason worth stating. `M1.otf` and the kern sidecar: they are the position stamp's, and moving either re-shapes every position while every row verdict is still served. `rebuild/m1-divergences.yaml`: classification always recomputes. `rebuild/m1-contact-allow.yaml`: no oracle stage reads it — it is the defect gate's allow-list — and it is outside `fingerprint.data_paths` entirely, so the stamp cannot reach it even by accident; it moves often enough that stamping it would have collapsed the whole store on a two-line bless. The rune files: they invalidate at per-family grain through the keys. What is inside is everything that can move a verdict without moving a named family — the comparison's own code closure, the remaining data inputs, the resolved spec structure and the capability-feature universe (a predicate class gaining a member, a rune-local group, a ligature sequence, a feature unlock: cross-rune routes the per-family grain cannot decompose, since `specificity::family_set` expands a class reference to its whole member set and ranks by set size, so a rune joining a class can flip a window holding no such rune), the engine's semantics flags, the configuration's feature set, and the subset table's own bytes. The position stamp rides on top of it: a store whose row stamp moved is not loaded at all, and one whose position stamp alone moved serves rows and re-shapes positions.

Window grain was measured and rejected, and the numbers belong here so it is not re-litigated from intuition: a settled window spans six slots over rows of at most four letters, so windows are letter-denser than rows — 287,280 of 499,989 distinct windows name four letters — and window grain serves 45.4% of the work at four edited runes against row grain's 48.6%, covers only the settlement slice rather than settlement plus the comparison, and keys its left slot on the previous window's output, so an edit propagates downstream keys into never-before-seen misses. Stacked on top of a row store its marginal value was 1.40% of lookups.

Records are positional and carry no per-row key, because the subset table is the complete product over the M1 alphabet in canonical order and the ordinal is therefore the key. What every record does carry is a twelve-hex truncated digest of its row's codepoints, checked on every serve: a positional store whose alignment is guaranteed only by a whole-file digest and a row count fails by serving every row wrong and silently, and the anchor converts that into a loud abort. `baseline_glyphs`, `baseline_seams` and `codepoints` are re-read from the table rather than stored — the table is on disk either way and the store is the thing that has to stay small.

Two mechanisms keep a wrong record from laundering itself forever, because nothing else would: a served record is re-emitted under the current stamp, so provenance alone can never age it out, and `gate:conform` — which does re-settle the identical universe every cycle — proves the font reproduces a fresh settlement and never compares a cached verdict against a fresh one, so it is not a backstop for this. First, every record keeps the `derived_at_pass` each of its two verdicts was *derived* at rather than the pass that last re-emitted it, and `RowStore.stale` and `RowStore.position_stale` force a re-derivation once that age reaches `MAX_RECORD_AGE`, spread by row ordinal so one row in that many re-derives every pass rather than the whole table on the pass the cap comes due. Second, `VerificationSample` draws up to `VERIFICATION_SAMPLE_PER_FAMILY` served rows for every family that served any, seeded on the stamp, the family and the ordinal of the pass the store is being read into, so the covered slice rotates instead of re-proving the same fraction of a percent forever — and since that ordinal advances only when a store is written, a pass forbidden to write one rotates on the clock instead of standing still, which is what keeps `--gates-only` from re-proving a single frozen sample every time it runs; the caller re-walks those rows and compares the full record, and a second sample of the same shape is drawn over the rows whose positions were served and re-shaped through HarfBuzz. Stratifying by family is what catches a family-wide poisoning with probability one rather than with probability sample-over-served, and a family-wide poisoning is the shape the mid-run rune edit produces.

The one assumption the key rests on that nothing else in the pipeline pins: every old compiled glyph name in a row belongs to a family the row's codepoints already reach, so the alias entries a served row would have consulted are all inside its own key. It holds over the whole live subset — twenty-seven distinct glyph heads, every one a rune file — and `unreachable_glyph_heads` is here so the caller can assert it per row rather than trusting it.

Everything degrades toward a full pass. `load_store` answers `None` for an absent, unreadable, format-mismatched, stamp-mismatched, digest-mismatched, short or trailer-less store, and a `None` costs one cold oracle and nothing else; a store whose position stamp or position keys will not compare loads with every position stale, which costs one pass of shaping and nothing else.

The settle memo the oracle shares with the conform belt (`conform.SettleMemoFile`) keys its entries the same way, and its key primitives live here beside the row store's because they are the same primitives: `settle_family_keys` is `family_keys` without the alias line, since the walk never reads the alias map, and `settle_memo_stamp` is the row stamp without the subset and alias-boundary lines, since a memo entry is a settlement and nothing more. A memo entry's reach is read off its window's labels rather than off a row's codepoints — the six slots' families plus every ligature rune all of whose components appear among them — and `StaleMask.bit_of` is the label-grain door into the same mask. `SettleMemoInputs` is the disk-derived half of both, snapshotted before the spec they describe is loaded, on `run_m1.tables_inputs`' discipline: a key cut before the load can only name content the settlements are at least as new as, so a rune edited during a run lands under a key the next pass reports moved, whichever side of the load the edit fell on.
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import os
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Collection, Iterable, Mapping, Sequence

import yaml

from rebuild.pipeline import fingerprint, kernel_exec, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.validation.rowmodel import format_codepoints

STORE_FORMAT = "ams-m1-oracle-rows/2"
STORE_STEM = "oracle-rows"
SCRATCH_SUBDIR = "oracle-rows"
ROW_COUNT_TRAILER = "#rows"
ANCHOR_WIDTH = 12

MAX_RECORD_AGE = 20
VERIFICATION_SAMPLE_PER_FAMILY = 8

# The code the position channel runs that the row closure does not already stamp: `_position_drift`, `_kern_normalized_positions` and `KernEvaluator` live in the oracle's module beside the classifier, and the whole module rides the position stamp at module grain because a function-grain digest is exactly the kind of roster nothing checks. A classifier edit therefore re-shapes every position while it still serves every row verdict — the safe direction, and a cost the `--gates-only` route pays in seconds per configuration. `Shaper` and `geometry.PIXEL` are already inside `ORACLE_ROW_CODE_PATHS`, so the row stamp covers them for both verdicts.
POSITION_CODE_PATHS = ("rebuild/pipeline/oracle.py",)
# The lock that pins uharfbuzz and fontTools, which is what turns the same font bytes into the same positions; `artifact_cycle.comparison_side_label` refuses it for the same reason, and a bump here rebuilds anyway.
TOOLCHAIN_LOCK = "uv.lock"

# The two alias heads that name a boundary glyph rather than a family. Their entries can never reach a verdict — `_compare_row` skips every name in `conform.BOUNDARY_GLYPH_NAMES` before it consults the map — so they ride the whole-store stamp instead of a family key, and `alias_family_digests` refuses any other head that has no rune digest beside it.
BOUNDARY_ALIAS_HEADS = frozenset({"space", "periodcentered"})

# The closure of what `_compare_row` and `_SettledWindowWalk` read, module by module rather than as the whole of rebuild/pipeline/: the comparison and its settlement, the crate that decides the settlement, the spec loader that resolves what both read, the fingerprints the keys are cut from, and this module. rebuild/test_oracle_code_closure.py walks the import graph from those two entry points on every contracts run and fails when anything reachable is outside this list, so it cannot go stale the way a hand-written roster otherwise would. conform.py is here permanently: it holds the producer this cache serves — `_compare_row`, the walk, and the codec between a verdict and a record. The classifier that re-runs over every served row is not, since issue 81 moved it to oracle.py, and the sibling test's stray check is what keeps it out: conform.py must never import oracle.py. The two tools files are here because `kernel_exec` derives its fan-out width from them (issue #63, sub-issue #86), which the comparison never consults and which cannot move a verdict at all — the streams are byte-identical at any width — so they buy the store nothing and cost it a drop whenever either moves. They stay anyway: the walk is at module grain, the sibling test forbids naming a module the comparison cannot reach, and between them the two have a couple of commits against `kernel_exec.py`'s many, so the churn this admits is close to none.
ORACLE_ROW_CODE_PATHS = (
    "rebuild/pipeline/conform.py",
    "rebuild/pipeline/emit_gsub.py",
    "rebuild/pipeline/fingerprint.py",
    "rebuild/pipeline/geometry.py",
    "rebuild/pipeline/kernel_exec.py",
    "rebuild/pipeline/kernel_io.py",
    "rebuild/pipeline/model.py",
    "rebuild/pipeline/oracle_cache.py",
    "rebuild/pipeline/settle.py",
    "rebuild/pipeline/spec_load.py",
    "rebuild/pipeline/table.py",
    "rebuild/tools/memory_budget.py",
    "rebuild/tools/peak_rss.py",
    "rebuild/validation/rowmodel.py",
)


def oracle_code_paths(repo_root: Path) -> list[Path]:
    """`ORACLE_ROW_CODE_PATHS` resolved against a checkout, plus the kernel crate's whole build-input surface — the crate decides every settlement this cache stores, and tracking its modules piecemeal goes wrong the next time one is added."""
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    return (
        [root / relative for relative in ORACLE_ROW_CODE_PATHS]
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
    )


def store_path(out_dir: Path, config: str) -> Path:
    """Where a promoted store lives: beside the m1 artifacts, but not one of them. The name misses `artifact_cycle.M1_ARTIFACT_NAMES` and every glob over the tables there, so it rides no gate key and not the artifacts-present check — this is a cache, and a cycle that deletes it must lose nothing but time."""
    return Path(out_dir) / f"{STORE_STEM}-{config}.tsv.gz"


def scratch_store_path(scratch_dir: Path, config: str) -> Path:
    """Where a store is staged while the oracle runs: a subdirectory of this run's pid-named audit scratch, so `discard_oracle_audit_scratch` sweeps a killed run's stores the way it already sweeps its shards, and so `join_oracle_audit`'s missing-shard diagnostic lists one directory rather than a store per acceptance configuration."""
    return Path(scratch_dir) / SCRATCH_SUBDIR / f"{config}.tsv.gz"


def _sha256_file(path: Path) -> str:
    try:
        return fingerprint.file_sha256(Path(path))
    except OSError:
        return "missing"


def _digest_lines(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def stamped_data_paths(repo_root: Path) -> list[Path]:
    """The data inputs the whole-store stamp folds: `fingerprint.data_paths` less the rune files, which invalidate at family grain through the keys instead, and less the three the comparison never consults — the alias map, whose family heads are stamped per family and whose two boundary heads ride their own line; the divergence ledger, since classification re-reads it over every row on every pass; and the kern sidecar, which the position stamp carries so that an edit to it re-shapes every position and re-derives nothing. The contact-allow list needs no exclusion: it left `fingerprint.data_paths` with the defect gate's own key, so nothing here has to name a path that set no longer contains. It is exported because `artifact_cycle.oracle_cache_note` has to say what a moved input will cost the store before the oracle has run, and a second hand-kept copy of this list is precisely the thing that would drift out of agreement with the stamp it is describing."""
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
    """`rebuild/m1-aliases.yaml`'s entries bucketed by the `.`-split head of each key, one digest per head over that bucket's sorted `key\\tdenotation` lines. The heads are what a family key can cite, so every head must be either a name in `family_names` or one of `BOUNDARY_ALIAS_HEADS`; anything else raises, because a head with no key can never be reported moved and would leave its alias entries stamped by nothing. That guard is the invariant, not a name check against the registry: the script registry holds far more families than the rune tree holds files, so "is a registry family" would place a head and then find no digest for it."""
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
    return {head: _digest_lines(lines) for head, lines in buckets.items()}


def _reach_lines(name: str, digests: Mapping[str, str], closure: Mapping[str, frozenset[str]]) -> list[str]:
    """The `member\\tdigest` lines of a family's own rune digest and its static `resolve.against` closure's — the one route by which a rune's records read another rune file's content directly."""
    reach = sorted({name} | set(closure.get(name, frozenset())))
    return [f"{member}\t{digests.get(member, '-')}" for member in reach]


def family_keys(repo_root: Path, spec: ResolvedSpec, alias_path: Path) -> dict[str, str]:
    """Per rune family, the digest a row's staleness test cites for it: the family's prose-blind rune digest joined with the digests of its static `resolve.against` closure — the one route by which its records read another rune file's content directly — and with the alias map's entries for that family, which are what the comparison reads to turn an old compiled name into a cell. A family with no alias entries records `-` rather than being omitted, so the key still moves the day entries appear for it. Every other cross-rune route rides the whole-store stamp."""
    digests = fingerprint.rune_digests(Path(repo_root))
    closure = spec_load.rune_closure(spec)
    aliases = alias_family_digests(Path(alias_path), digests.keys())
    keys: dict[str, str] = {}
    for name in sorted(digests):
        lines = _reach_lines(name, digests, closure)
        lines.append(f"alias\t{aliases.get(name, '-')}")
        keys[name] = _digest_lines(lines)
    return keys


def position_family_keys(row_keys: Mapping[str, str], glyph_digests: Mapping[str, str]) -> dict[str, str]:
    """Per family, the digest a row's position verdict cites for it: the family's row key — so a position is stale wherever the row verdict is — joined with the after font's compiled-glyph digest for that family (`fingerprint.after_font_glyph_digests`). Cut over the union of the two rosters, so a family the font holds glyphs for and the rune tree holds no key for, or the reverse, still carries a key that can be reported moved; a name absent from one side records `-` on that side."""
    return {
        name: _digest_lines((f"row\t{row_keys.get(name, '-')}", f"glyphs\t{glyph_digests.get(name, '-')}"))
        for name in sorted(set(row_keys) | set(glyph_digests))
    }


def position_keys(
    repo_root: Path, row_keys: Mapping[str, str], font_path: Path, kern_sidecar_path: Path | None
) -> tuple[dict[str, str], EnvironmentStamp]:
    """The position store's two keys as one read of the font: the per-family keys over `position_family_keys`, and the whole-store position stamp — the position channel's own module (`POSITION_CODE_PATHS`), the toolchain lock, the font's non-family glyphs, cmap and GPOS wiring (`fingerprint.after_font_glyph_digests`' helpers digest) and the kern sidecar's bytes. The row stamp is not repeated here: a store is loaded at all only when that one matches, so the position stamp rides on top of it rather than beside it. A caller with no kern sidecar records `-` for it."""
    root = Path(repo_root)
    glyph_digests, helpers = fingerprint.after_font_glyph_digests(Path(font_path))
    lines = (
        f"format\t{STORE_FORMAT}",
        f"position_code\t{fingerprint.hash_paths(root, [root / relative for relative in POSITION_CODE_PATHS])}",
        f"toolchain\t{_sha256_file(root / TOOLCHAIN_LOCK)}",
        f"font_helpers\t{helpers}",
        f"kern\t{'-' if kern_sidecar_path is None else _sha256_file(Path(kern_sidecar_path))}",
    )
    return position_family_keys(row_keys, glyph_digests), EnvironmentStamp(lines=lines)


@dataclass(frozen=True)
class SettleMemoInputs:
    """The disk-derived half of the settle memo's keys, cut before the spec they describe is loaded — `run_m1.tables_inputs`' discipline, so a key can only ever name content the settlements are at least as new as. The spec-derived half (`spec_structure`, `capability_features`) is cut off the loaded spec itself in `settle_memo_stamp`, which is the spec that settles."""

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
    """Per rune family, the digest a memo entry's staleness test cites for it: `family_keys` without the alias line, because the walk that fills the memo never reads the alias map — a settlement is a function of the rune files a window names and of their `resolve.against` closure, and of nothing the comparison adds on top."""
    closure = spec_load.rune_closure(spec)
    return {
        name: _digest_lines(_reach_lines(name, inputs.rune_digests, closure))
        for name in sorted(inputs.rune_digests)
    }


def settle_memo_stamp(
    inputs: SettleMemoInputs, spec: ResolvedSpec, config: str, features: Collection[str]
) -> EnvironmentStamp:
    """Everything that can move a memoized settlement without moving a named family's key: `environment_stamp` less the lines only the comparison reads (the subset table, the alias map's boundary heads) and less the store format, which the memo file carries in its own header. `settlement_flags` rather than `kernel_exec.enumeration_tokens`, because the walk settles windows one at a time and never enumerates, so the deep-class grain cannot reach it."""
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


@dataclass(frozen=True)
class EnvironmentStamp:
    """The whole-store stamp as its own `label\\tdigest` lines rather than only as a hash, in the `fingerprint.path_lines` idiom: a store that records the lines it was written under lets a miss name which input moved, and the hit rate here is bimodal enough that a legitimate class-membership invalidation would otherwise read as a bug."""

    lines: tuple[str, ...]

    @property
    def value(self) -> str:
        return _digest_lines(self.lines)

    @property
    def labels(self) -> dict[str, str]:
        """The same lines as a `label -> digest` map, which is the shape `moved_note` compares."""
        labels: dict[str, str] = {}
        for line in self.lines:
            label, _, digest = line.partition("\t")
            labels[label] = digest
        return labels


def environment_stamp(
    repo_root: Path,
    spec: ResolvedSpec,
    config: str,
    features: Collection[str],
    subset_path: Path,
    alias_path: Path,
    family_names: Collection[str],
) -> EnvironmentStamp:
    """Everything that can move a row's pre-position verdict without moving a named family's key. `features` is passed in rather than derived from `config` because `features_for_config` lives in `conform.py` and this module must stay out of `conform`'s import cycle — `conform` imports this one — and `family_names` is passed in rather than re-read for the same reason `run_oracle` snapshots the keys before the first row: one read of the rune tree per run, not one per configuration. The alias map appears here only through its boundary heads; every family head is stamped at family grain by `family_keys`. See the module docstring for what is deliberately absent and why."""
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
        + _digest_lines(
            f"{head}\t{boundary[head]}" for head in sorted(boundary) if head in BOUNDARY_ALIAS_HEADS
        ),
        f"subset\t{_sha256_file(Path(subset_path))}",
    )
    return EnvironmentStamp(lines=lines)


def moved_families(recorded: Mapping[str, str], current: Mapping[str, str]) -> frozenset[str]:
    """Every family whose key differs between what a store recorded and what this run computes, counting a name present in one map and absent from the other as moved. The symmetric difference is here rather than a `recorded.get(name) == current.get(name)` walk because two absences compare equal that way, and a family with no key on either side is exactly the case where nothing could ever be reported moved — the registry holds thirty families the rune tree holds no file for, and a new rune file appearing must read as a miss rather than as agreement."""
    moved = set(current.keys() ^ recorded.keys())
    for name in current.keys() & recorded.keys():
        if current[name] != recorded[name]:
            moved.add(name)
    return frozenset(moved)


def moved_note(recorded: Mapping[str, str], current: Mapping[str, str], limit: int = 8) -> str | None:
    """Which labels moved between two `label -> digest` maps — the miss diagnostic, over the stamp's own lines or over the family keys. `None` when nothing differs. Mirrors `artifact_cycle.moved_inputs_note`, which is the house idiom for this."""
    moved = [
        f"{name} (changed)"
        for name in sorted(recorded.keys() & current.keys())
        if recorded[name] != current[name]
    ]
    moved += [f"{name} (new)" for name in sorted(current.keys() - recorded.keys())]
    moved += [f"{name} (gone)" for name in sorted(recorded.keys() - current.keys())]
    if not moved:
        return None
    shown = ", ".join(moved[:limit])
    return f"{shown} and {len(moved) - limit} more" if len(moved) > limit else shown


class StaleMask:
    """The per-row staleness test, as a bitmask over the families a row can reach. One bit per family in sorted registry order; `mask_of` folds a row's codepoints, and `stale` answers whether any moved family is inside — directly, for a family carrying a codepoint, or through the ligature clause, which fires only when every component of a moved ligature rune appears in the row. That clause is `unit_cache.UnitKeyer._relevant_families` inverted at row grain and read off `spec.registry.families[...].sequence` rather than off a `_`-split of the name, because the sequence is the declaration `settle.form_ligatures` actually routes on. A moved family the registry can place neither by codepoint nor by sequence stales every row: over-invalidation is the safe direction, and there is no such family today."""

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
        """The bit one family name folds into a mask, zero for a name the registry does not place — a boundary label, a window edge, or an unknown family — which is the label-grain door the settle memo reads through, since a memo window names families by label rather than by codepoint."""
        return self._bit.get(family, 0)

    def stale(self, mask: int) -> bool:
        if self.everything:
            return True
        if mask & self._stale_symbols:
            return True
        return any((mask & bits) == bits for bits in self._stale_ligatures)

    def families_of(self, mask: int) -> tuple[str, ...]:
        """Every family a row with this mask can reach: the letters its codepoints name, plus each ligature rune all of whose components are among them. Memoized on the mask, because rows of at most four letters over a two-dozen-symbol alphabet share their masks heavily."""
        cached = self._reach.get(mask)
        if cached is None:
            names = [name for name, bit in self._bit.items() if mask & bit]
            names += [name for name, bits in self._ligatures.items() if bits and (mask & bits) == bits]
            cached = tuple(sorted(set(names)))
            self._reach[mask] = cached
        return cached


def unreachable_glyph_heads(glyph_names: Iterable[str], reachable: Collection[str]) -> tuple[str, ...]:
    """The `qs` family heads of a row's old compiled glyph names that its reachable family set does not contain — empty for every row of the live subset, and the assertion for the one assumption this key rests on that nothing else pins. A non-empty answer means the row consulted alias entries stamped by no key it cites, and the caller must refuse to serve it."""
    known = set(reachable)
    heads = {name.split(".", 1)[0] for name in glyph_names}
    return tuple(sorted(head for head in heads if head.startswith("qs") and head not in known))


@dataclass(frozen=True, slots=True)
class CachedRow:
    """One row's pre-position comparison verdict: `conform._compare_row`'s `DivergentRow` minus the three fields the subset table already holds (`codepoints`, `baseline_glyphs`, `baseline_seams`) and minus `config`, which is the store's. The verdict alone, with no provenance in it, so `==` is verdict equality and the verification sample needs nothing else. When a record was derived is store bookkeeping and lives beside it, in `RowStore.age`."""

    kinds: tuple[str, ...]
    position: int
    new_cells: tuple[str, ...]
    new_seams: tuple[str, ...]
    phenomena: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CachedPosition:
    """One row's position-channel verdict for a row that drifted: `oracle._position_drift`'s drift descriptions, which the audit prints as a position-only row's new cells, and whether every drifted slot sits downstream of a kern-attributable one. A row whose drawn positions matched is stored as `None`, and a row the channel never shaped as `UNSHAPED`; only the first two may ever be served, and `==` over them is the verification sample's whole check."""

    drifts: tuple[str, ...]
    kern_attributable: bool


class _Unshaped:
    """The position record of a row the previous pass never shaped — excluded by the ledger's ink-identity claim, or compared with no font open. Distinct from `None`, which is a shaped row that matched, because serving the one as the other would count a row the channel never saw as clean."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSHAPED"


UNSHAPED = _Unshaped()
PositionVerdict = CachedPosition | None | _Unshaped


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One row's whole record as the store holds it: both verdicts and the pass each was derived at. The ages are per verdict because the two re-derive on different keys — a font compile that moves a family's outlines re-shapes its rows' positions while every row verdict stands."""

    row: CachedRow | None
    row_age: int
    position: PositionVerdict
    position_age: int


def _split(text: str, separator: str) -> tuple[str, ...]:
    return tuple(text.split(separator)) if text else ()


def row_anchor(codepoints: Sequence[int]) -> str:
    """A record's alignment anchor: the leading `ANCHOR_WIDTH` hex characters of the digest of the row's canonical codepoint string. Checked on every serve, which is what turns a concurrently refiltered or reordered table from every row served wrong and silent into a loud abort."""
    return hashlib.sha256(format_codepoints(tuple(codepoints)).encode()).hexdigest()[:ANCHOR_WIDTH]


def encode_record(
    codepoints: Sequence[int],
    cached: CachedRow | None,
    derived_at_pass: int,
    position: PositionVerdict = UNSHAPED,
    position_at_pass: int = 0,
) -> str:
    """One store line: the anchor; then `-` for a clean row or `P` and the row verdict's five fields; then `?` for a position never shaped, `-` for one that matched, or `D`, the drift descriptions `|`-joined and `k` or `n` for the kern-attribution flag; then the two passes, the row verdict's first. `|` separates cells and `,` separates the token tuples, matching what `divergence-audit.tsv` already does with the same values; the drift descriptions carry commas of their own, which is why they are joined on `|` alone and the flag rides its own field."""
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
    """One configuration's loaded store. The body is held as a single decompressed blob plus an offsets array rather than as a third of a million parsed records: only the rows a pass actually serves are ever decoded, and the ages — which every row's staleness test reads — ride a parallel int array so the partition scan never touches the blob at all.

    `rotation` is what a pass that will never write one of these declares about itself. Both anti-laundering mechanisms advance on the pass ordinal, and the ordinal only advances when a store is written — so a read-only pass, repeated, would retire the same twentieth of the table and re-prove the same sample every time, forever, which is the shape `--gates-only` takes in the re-adjudication loop it exists for. A writing pass leaves this at zero and rides its own ordinal; a read-only one rotates on the clock, and the coverage moves whether or not anything on disk does.
    """

    def __init__(
        self,
        environment: EnvironmentStamp,
        recorded_lines: tuple[str, ...],
        recorded_keys: dict[str, str],
        subset_digest: str,
        pass_ordinal: int,
        mask: StaleMask,
        blob: bytes,
        offsets: "array[int]",
        ages: "array[int]",
        rotation: int = 0,
        position_mask: StaleMask | None = None,
        position_ages: "array[int] | None" = None,
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
        if position_mask is None:
            position_mask = StaleMask(mask.spec, mask.moved)
            position_mask.everything = True
        self.position_mask = position_mask
        self._position_ages = ages if position_ages is None else position_ages

    @property
    def rows(self) -> int:
        return len(self._ages)

    @property
    def moved(self) -> frozenset[str]:
        return self.mask.moved

    def age(self, index: int) -> int:
        """The pass this row's verdict was derived at — carried forward verbatim by every pass that only served it, so it measures how long the verdict has stood rather than how long the file has."""
        return self._ages[index]

    def position_age(self, index: int) -> int:
        return self._position_ages[index]

    @property
    def coverage_ordinal(self) -> int:
        """The ordinal the renewal slice and the verification sample are drawn against, as against `pass_ordinal + 1`, which is what the writer will record. They differ only for a pass that records nothing: see `rotation`."""
        return self.pass_ordinal + 1 + self.rotation

    def _due(self, index: int, age: int) -> bool:
        if self.pass_ordinal + 1 - age >= MAX_RECORD_AGE:
            return True
        return self.coverage_ordinal % MAX_RECORD_AGE == index % MAX_RECORD_AGE

    def due(self, index: int) -> bool:
        """Whether this row's verdict must re-derive regardless of its families. The ordinal clause retires one row in `MAX_RECORD_AGE` every pass, so no verdict can stand that many passes without being recomputed and the renewal is spread across passes rather than arriving all at once on the pass the cap comes due; the age clause is the belt to those braces, and catches a store whose pass ordinals skipped. Only the slice rotates for a read-only pass — the age arithmetic stays on the true ordinal, because a rotated `current` would read every record as older than the cap and retire the whole table."""
        return self._due(index, self._ages[index])

    def position_due(self, index: int) -> bool:
        """`due` over the position verdict's own age: the same slice retires both verdicts of a row on the same pass, and the age clause reads the pass the position was shaped at."""
        return self._due(index, self._position_ages[index])

    def stale(self, index: int, mask: int) -> bool:
        return self.mask.stale(mask) or self.due(index)

    def position_stale(self, index: int, mask: int) -> bool:
        """Whether this row's position verdict must re-shape: wherever its row verdict must re-derive — the position key embeds the row key, and a served position over a fresh settlement would be shaping the previous pass's cells — or wherever a family it reaches moved its glyphs, the position stamp moved, or the renewal clause is due."""
        return self.stale(index, mask) or self.position_mask.stale(mask) or self.position_due(index)

    def serve(self, index: int, codepoints: Sequence[int]) -> StoredRecord:
        """This row's whole record, after proving it is this row's; the caller decides which of its two verdicts the keys allow it to use and counts the position under `positions_served` itself. A mismatched anchor is not a miss and must not be treated as one — it means the table under this store was replaced or reordered, and every other record is wrong the same way."""
        start = self._offsets[index]
        end = self._offsets[index + 1] - 1
        anchor = self._blob[start : start + ANCHOR_WIDTH].decode("ascii")
        if anchor != row_anchor(codepoints):
            raise SystemExit(
                f"the oracle row cache is misaligned at row {index}: the record is anchored to {anchor} where the table holds {format_codepoints(tuple(codepoints))} — the store describes a different table and nothing it holds can be served"
            )
        self.served += 1
        return decode_record(self._blob[start:end].decode("utf-8"))


def read_header(path: Path) -> dict | None:
    """A store's header alone, for the caller that wants to name what moved after `load_store` has already declined it. `None` when there is nothing readable there."""
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
    """The position channel's staleness mask for a loaded store: over the families whose position keys moved when the store's position stamp still matches this run's, and over every row — `everything` — when either side has no position keys or stamp, the stamp moved, or the header's position fields will not read. `moved` is the row channel's moved set and seeds the mask so a family the row mask stales is stale here too whatever the position keys say."""
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
) -> RowStore | None:
    """The previous pass's records for one configuration, or `None` when there is no store this run may trust: absent, unreadable, format- or stamp-mismatched, written against another subset table, or missing the trailer that vouches for its own length. Over-invalidation is the only safe direction here — a `None` costs one cold oracle and nothing else — so every parse failure lands in the same place, `zlib`'s own included: a corrupt deflate body raises out of the compression layer rather than as an `OSError`, and would otherwise take the build down for a file whose only job is to save time. The position stamp and keys decide less: a store that loads serves its row verdicts whatever they say, and `position_stale_mask` decides whether any of its position verdicts may be served beside them. `rotation` is handed to the store unread; see `RowStore` for what a pass that may not write declares with it."""
    store_file = Path(path)
    if not store_file.is_file():
        return None
    try:
        with gzip.open(store_file, "rb") as stream:
            payload = stream.read()
        head, _, rest = payload.partition(b"\n")
        header = json.loads(head)
        if header["format"] != STORE_FORMAT:
            return None
        recorded_lines = tuple(header["environment"])
        if recorded_lines != environment.lines:
            return None
        if header["subset_digest"] != subset_digest:
            return None
        recorded_keys = {str(name): str(value) for name, value in header["family_keys"].items()}
        pass_ordinal = int(header["pass_ordinal"])

        if not rest.endswith(b"\n"):
            return None
        trailer_at = rest.rfind(b"\n", 0, len(rest) - 1)
        trailer = rest[trailer_at + 1 : -1].decode("utf-8").split("\t")
        if trailer[0] != ROW_COUNT_TRAILER:
            return None
        expected = int(trailer[1])
        blob = rest[: trailer_at + 1]

        offsets: array[int] = array("q", [0])
        ages: array[int] = array("i")
        position_ages: array[int] = array("i")
        cursor = 0
        limit = len(blob)
        while cursor < limit:
            end = blob.index(b"\n", cursor)
            last_tab = blob.rindex(b"\t", cursor, end)
            position_ages.append(int(blob[last_tab + 1 : end]))
            ages.append(int(blob[blob.rindex(b"\t", cursor, last_tab) + 1 : last_tab]))
            cursor = end + 1
            offsets.append(cursor)
        if len(ages) != expected:
            return None
    except OSError, EOFError, ValueError, KeyError, IndexError, TypeError, zlib.error:
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
    )


def next_pass_ordinal(store: RowStore | None) -> int:
    return 0 if store is None else store.pass_ordinal + 1


class RowWriter:
    """One configuration's store being written, one record per subset row in table order. The row count is a trailer rather than a header field, which is what lets the header go out before the count is known and, better, makes a truncated store fail to load instead of loading short: a store whose last bytes are missing has no trailer at all. The gzip mtime is pinned so consecutive identical passes stay byte-identical and the compression level with it — level 1, because this file is written once and read once per run and level 9's seconds would come off every cycle. The finished bytes land through a temporary file and `os.replace`, so a store on disk is always a whole one."""

    def __init__(
        self,
        path: Path,
        environment: EnvironmentStamp,
        subset_digest: str,
        pass_ordinal: int,
        family_keys: Mapping[str, str],
        position_environment: EnvironmentStamp | None = None,
        position_keys: Mapping[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.pass_ordinal = pass_ordinal
        self.rows = 0
        self._scratch = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "format": STORE_FORMAT,
            "environment": list(environment.lines),
            "family_keys": {name: family_keys[name] for name in sorted(family_keys)},
            "pass_ordinal": pass_ordinal,
            "position_environment": (
                None if position_environment is None else list(position_environment.lines)
            ),
            "position_keys": (
                None
                if position_keys is None
                else {name: position_keys[name] for name in sorted(position_keys)}
            ),
            "subset_digest": subset_digest,
        }
        self._raw: IO[bytes] = self._scratch.open("wb")
        self._stream = gzip.GzipFile(filename="", fileobj=self._raw, mode="wb", mtime=0, compresslevel=1)
        self._stream.write((json.dumps(header, sort_keys=True) + "\n").encode())

    def append(
        self,
        codepoints: Sequence[int],
        cached: CachedRow | None,
        derived_at_pass: int,
        position: PositionVerdict = UNSHAPED,
        position_at_pass: int = 0,
    ) -> None:
        """Record one row, divergent or clean — every subset row gets a record, in table order, because the ordinal is the key and a clean row with no age would be the one thing the renewal cap could not retire. `derived_at_pass` is the pass the row verdict was computed at: `RowStore.age(index)` for a record this pass only served, `self.pass_ordinal` for one it derived; `position_at_pass` is the same for the position verdict, read off `RowStore.position_age(index)` when it was served. Passing this pass for a served verdict is the laundering the age exists to prevent."""
        self._stream.write(
            (encode_record(codepoints, cached, derived_at_pass, position, position_at_pass) + "\n").encode()
        )
        self.rows += 1

    def close(self) -> None:
        self._stream.write(f"{ROW_COUNT_TRAILER}\t{self.rows}\n".encode())
        self._stream.close()
        self._raw.close()
        os.replace(self._scratch, self.path)

    def abandon(self) -> None:
        """Drop a partially written store without promoting it — what a failed configuration owes the next pass."""
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


def promote_stores(scratch_dir: Path, out_dir: Path, configs: Iterable[str]) -> list[str]:
    """Move a finished run's staged stores into place beside the m1 artifacts, all of them or none. Called only after `join_oracle_audit` has promoted the audit and only after the caller has re-verified that neither the stamp nor any family key moved while the run held them — a run whose inputs shifted under it wrote nothing reusable, and recording the digests it did not build from is the one failure that reads as green forever. Returns the configurations promoted."""
    staged = [(config, scratch_store_path(scratch_dir, config)) for config in configs]
    if any(not path.is_file() for _, path in staged):
        return []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for config, path in staged:
        os.replace(path, store_path(out_dir, config))
    return [config for config, _ in staged]


def discard_stores(out_dir: Path, configs: Iterable[str]) -> None:
    """Take the promoted stores off disk — what `--fresh-oracle-cache` does before a pass that will write replacements, so the next one trusts only what the distrusting pass wrote. A pass that may not write a store never calls this: declining to read one costs the same pass the same full derivation, while deleting it would spend the *next* pass's savings too."""
    for config in configs:
        store_path(out_dir, config).unlink(missing_ok=True)


def _mix64(value: int) -> int:
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
    value = (value ^ (value >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
    return value ^ (value >> 31)


class VerificationSample:
    """The stratified served-vs-recomputed sample, drawn while the pass runs. Every family that served at least one row contributes up to `per_family` of them, chosen by the smallest mixes of a per-family seed with the row's ordinal — so the draw is a pure function of the store's stamp, the family and the pass ordinal, independent of the order rows are offered in and of how many there turn out to be. Seeding on the pass ordinal is what makes coverage accumulate instead of re-proving the same fraction of a percent forever, and stratifying by family is what catches a family-wide poisoning with probability one rather than with probability sample-over-served: a rune edited while the oracle runs poisons a whole family at once, which is the failure this sample exists for. The caller re-walks `indexes()` through the live walker and compares each served `CachedRow` with the one a fresh `_compare_row` returns; the record holds the verdict alone, so `==` is the whole check."""

    def __init__(
        self, stamp: str, pass_ordinal: int, per_family: int = VERIFICATION_SAMPLE_PER_FAMILY
    ) -> None:
        self.stamp = stamp
        self.pass_ordinal = pass_ordinal
        self.per_family = per_family
        self._seeds: dict[str, int] = {}
        self._kept: dict[str, list[tuple[int, int]]] = {}

    def _seed(self, family: str) -> int:
        seed = self._seeds.get(family)
        if seed is None:
            digest = hashlib.sha256(f"{self.stamp}\t{family}\t{self.pass_ordinal}".encode()).digest()
            seed = int.from_bytes(digest[:8], "big")
            self._seeds[family] = seed
        return seed

    def offer(self, index: int, families: Iterable[str]) -> None:
        if self.per_family <= 0:
            return
        for family in families:
            kept = self._kept.setdefault(family, [])
            score = -_mix64(self._seed(family) ^ (index * 0x9E3779B97F4A7C15 & 0xFFFFFFFFFFFFFFFF))
            if len(kept) < self.per_family:
                heapq.heappush(kept, (score, index))
            elif score > kept[0][0]:
                heapq.heapreplace(kept, (score, index))

    def by_family(self) -> dict[str, tuple[int, ...]]:
        return {family: tuple(sorted(index for _, index in kept)) for family, kept in self._kept.items()}

    def indexes(self) -> tuple[int, ...]:
        return tuple(sorted({index for kept in self._kept.values() for _, index in kept}))
