"""The persisted per-unit surface cache (issue 20): the review build's phase-1/phase-2 products carried across builds, keyed by per-unit content keys, so a one-rune edit re-enriches the windows that could feel it and serves everything else from the previous surface's shards.

A unit's expensive products — the ink diffs and machine-approval flags, the enrichment (cells, seams, highlights, explain, provenance), and the three drafts, of which a machine-approved or verdict-exempt unit's fragment carries only the first half (`audit.slim_fragment`: it omits the highlight, the explain and the drafts outright, since nothing under them reaches a reviewer) — are a pure function of a nameable closure, and the cache's soundness is exactly the claim that the content key covers that closure. The key is two-grained: per unit, the audit rows (which pin the window, its configs, both fonts' rendered names, and the matched ledger classes) plus a per-family digest for every window letter — the family's explain-aware rune digest expanded by its static `resolve.against` closure, joined with a digest of the after font's compiled glyphs for that family (outlines, advances, and cursive anchors, so a drawing or anchor change invalidates even when no name in the rows moves) — with ligature families included whenever all their components appear in the window. Whole store, everything that can move a unit's products without moving a named family: the code the surface build runs (`surface_code_paths` — the review modules the build imports, the pipeline and validation modules those reach, and the crate modules the `settle-cases` and `guard-sweep` verbs run, a walked closure rebuild/test_review_code_closure.py holds the rosters to, so an edit to the driver, a gate, the oracle, the font compile or the crate's enumeration and fold keeps the store), the non-rune data files, the engine's semantics flags, the resolved spec structure and capability-feature universe (cross-rune routes: predicate-class and group memberships, ligature sequences, the formation guard's feature combos), the before and Junior fonts wholesale, the acceptance configs' subset tables, the draft harness (test/test_shaping.py, tools/, postscript_glyph_names.yaml) and the three site corpus files it validates pins against, and the after font's non-family glyphs, cmap, and GPOS wiring. What is deliberately outside every stamp is the after font's GSUB wiring; `after_font_glyph_digests` carries the argument for why a window's glyph selection is covered without it. The divergence ledger is deliberately not in the store stamp: its per-unit effects reach the shards only through the audit's matched_entry column (in the rows) or through fields the build re-derives and re-patches on every pass (no_verdict, exemplar, class promotion), so a ledger edit invalidates exactly the units whose rows it moved. The refuse prose the explain panel quotes is deliberately not in the store stamp either, for the same reason it is in the family keys: rewording one re-enriches the windows holding that family and leaves every other unit served.

What the store serves is the previous build's emitted fragment (read back from the shard it lives in, at the address the record carries — the part, byte offset and length the shard writer handed back as it wrote the fragment, so the plan trusts an address rather than parsing the previous surface to find one, and the fragment is parsed once, at the write, where `PriorFragmentReader` holds the bytes at that address to the record's id and stamp) plus the slim projection the parent's global reduces need: the machine flags and ink deltas, the verdict family, the judged pair, the ink-diff digest for echo grouping, the seam-home projection and per-seam rects, and the unit's mismatch lines — and whether the fragment was written slim, because the shape a build writes turns on the exemption, a ledger fact outside the key. So a unit that crosses from machine-approved-or-exempt into the human workload on a ledger edit (no_verdict flipping) is a miss and is re-enriched in full rather than served the slim fragment it earned before the edit, and one crossing the other way is a miss too, so a served surface stays byte-identical to a from-scratch one. Everything order-derived or ledger-derived — id, batch, echo, class, no_verdict, exemplar, the secondary-seam homes — is recomputed over the full universe every build and patched into every fragment by address as its shard is written, a fresh one out of the build's own spool (drafted the moment its unit was enriched, with those fields as placeholders) exactly as a served one out of the previous surface, so a cache hit never freezes a global field and the two kinds of unit take one write path; the cluster id alone is trusted from the served fragment, because its inputs (configs, final class, ink diffs) are all under the key. The byte-identity gate (rebuild/test_unit_cache.py::test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit) is the standing proof: an incrementally rebuilt live surface must match a from-scratch build byte for byte.

This module also owns the carry content key (the render identity rebuild/tools/carry_verdicts.py resolves prior verdicts against), so the build can stamp each unit's `content_key` at emission time and carry can probe stamped hashes instead of re-serializing every unit — one definition, shared by both sides, with the stamp itself excluded from the projection it hashes.

Beside the per-unit store lives the ink-signature store (issue 18), which does for the ink-duplicate merge what the unit store does for enrichment: the merge needs one rendered-outcome signature per (window, config) over every relabel-split window — the one per-unit product computed before the unit universe exists, so the unit store can never serve it — and re-shaping those serially was the load phase's floor. Each entry's key follows the unit key's two-grained soundness argument exactly: the audit row pins the window, the config, the before font's rendered names, and the settled cells the after font is compiled to reproduce, and the per-family digests pin the after font's outlines, advances, and cursive anchors for every family the window can touch. The whole-store stamp carries what signatures depend on beyond that: the code the surface build runs (the same `surface_code_paths` closure the unit store keys on — the Shaper lives in rebuild/validation and the comparator in rebuild/review, and nothing narrower is proved) and the before font wholesale, plus the after font's non-family glyphs, cmap, and GPOS wiring. Deliberately absent: the ledger, the subsets, the Junior font, the corpus, and the draft harness — signatures read none of them, so this store survives edits that drop the unit store, and a build that re-enriches everything can still skip re-shaping the merge.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping

from rebuild.pipeline import fingerprint, kernel_exec, spec_load
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import unit_index
from rebuild.review.audit import ACCEPTANCE_CONFIGS, AuditRow, Unit, parse_codepoints
from rebuild.review.drafts import CORPUS_FILES

STORE_FORMAT = "ams-review-unit-cache/4"
STORE_NAME = "unit-cache.ndjson.gz"
SIGNATURE_STORE_FORMAT = "ams-review-ink-signatures/2"
SIGNATURE_STORE_NAME = "ink-signatures.tsv.gz"

# The carry identity's non-participating fields (rebuild/tools/carry_verdicts.py imports this): id, batch, no_verdict, exemplar, echo, and cluster are order- or ledger-derived and churn whenever the surface renumbers; explain, drafts, provenance, and secondary_seams are derived presentation whose adjudicable content is already covered by the window plus both fonts' glyphs, cells, and seams; ink_deltas is the same delta identity persisted per config; content_key is the stamp of this very projection and must not feed itself. The highlight is inside the projection, and a slim fragment (`audit.slim_fragment`) omits it, so a slim fragment's stamp is over what it carries and is not the stamp the same window's full fragment would have borne — which strands nothing, because the units written slim are the ones that take no verdict, and the exclusions here are what keep every human unit's stamp where it was. picture_identical is a pure function of the window and both fonts' placed glyphs, which the projection already covers through codepoints, configs, and both sides' glyphs, cells, and seams, so excluding it changes nothing the key says — while including it would restamp every unit whose flag flips the day the channel lands and strand the verdicts recorded against them; ink_identical is the one derived flag inside the key, kept there only as the byte-identity contract with every prior snapshot.
CARRY_PRESENTATION_KEYS = frozenset(
    {
        "id",
        "batch",
        "no_verdict",
        "exemplar",
        "explain",
        "drafts",
        "provenance",
        "secondary_seams",
        "echo",
        "cluster",
        "ink_deltas",
        "picture_identical",
        "content_key",
    }
)


def carry_projection(unit: Mapping) -> str:
    """The carry content key as recorded historically: the unit's non-presentation fields as sorted-key JSON. This is a byte-identity contract with every prior surface snapshot — changing the serialization or the exclusion set strands carried verdicts."""
    return json.dumps(
        {key: value for key, value in unit.items() if key not in CARRY_PRESENTATION_KEYS},
        sort_keys=True,
    )


def carry_content_hash(unit: Mapping) -> str:
    return hashlib.sha256(carry_projection(unit).encode()).hexdigest()


def store_path(out_dir: Path) -> Path:
    return Path(out_dir) / STORE_NAME


def _sha256_file(path: Path) -> str:
    try:
        return fingerprint.file_sha256(Path(path))
    except OSError:
        return "missing"


def _manifest_stamp(out_dir: Path) -> str:
    """The manifest's identity digest for the store's whole-store stamp, or the sentinel when there is no manifest to read — a first build, or a crash between the manifest write and this one. The sentinel turns that into a stamp mismatch and a full rebuild rather than an exception out of `load_store`."""
    try:
        return unit_index.manifest_sha256(Path(out_dir))
    except OSError:
        return "missing"


def _cursive_anchor_map(font) -> dict[str, list]:
    """Per glyph, the cursive-attachment geometry the after font positions it by: one (lookup index, entry, exit) triple per CursivePos record naming it, anchors as [x, y] or None. GPOS is the one channel a compiled glyph's rendering reads outside its charstring and advance, so it belongs in the per-glyph digest."""
    anchors: dict[str, list] = {}
    if "GPOS" not in font:
        return anchors
    lookup_list = font["GPOS"].table.LookupList  # pyright: ignore[reportAttributeAccessIssue]
    if lookup_list is None:
        return anchors
    for index, lookup in enumerate(lookup_list.Lookup):
        for subtable in lookup.SubTable:
            if lookup.LookupType == 9:
                subtable = subtable.ExtSubTable
            if getattr(subtable, "LookupType", lookup.LookupType) != 3:
                continue
            glyphs = subtable.Coverage.glyphs
            for name, record in zip(glyphs, subtable.EntryExitRecord):
                entry = record.EntryAnchor
                exit_anchor = record.ExitAnchor
                anchors.setdefault(name, []).append(
                    (
                        index,
                        None if entry is None else [entry.XCoordinate, entry.YCoordinate],
                        None if exit_anchor is None else [exit_anchor.XCoordinate, exit_anchor.YCoordinate],
                    )
                )
    return anchors


def after_font_glyph_digests(after_font: Path) -> tuple[dict[str, str], str]:
    """Per qs family, a digest over the after font's compiled glyphs whose name stem belongs to it (decomposed outline operations, so subroutine plumbing can never hide a change; advance and sidebearing; cursive anchors), plus one environment digest over everything else the shaped run can touch regardless of family: the non-qs glyphs (boundary and marker helpers), the cmap, and the GPOS feature-to-lookup wiring.

    The GSUB wiring is deliberately not in the environment digest, and it is the one omission worth arguing. A rune edit moves the GSUB lookup list on essentially every cycle, so folding it in here made both whole-store stamps move on exactly the workflow the cache exists for — a store that never once served a unit. What covers a window's glyph selection instead is a pair of things already in the keys: the audit row's `new` column, which is the settled cell the window resolves to and which the per-unit and per-signature keys both hash, and `gate:conform`, which re-shapes the compiled font through HarfBuzz every cycle and proves its selection is the settlement's. So within a cycle whose conform gate is green, which glyph the after font puts in a window is a function of that window's settled cells, and those cells cannot move without the unit's key moving. Everything the selected glyph then contributes — outline, advance, cursive anchors — is in the per-family digests, for every variant of every family the window can reach rather than only the selected one, and the code that emits the GSUB at all is in the environment stamp's pipeline fingerprint. GPOS stays because its channel is positional: it can move a run without moving a name or a cell.
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(after_font))
    glyph_set = font.getGlyphSet()
    metrics = font["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
    anchors = _cursive_anchor_map(font)
    per_glyph: dict[str, str] = {}
    for name in sorted(glyph_set.keys()):
        pen = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(pen)
        payload = repr((name, tuple(pen.value), metrics.get(name), anchors.get(name)))
        per_glyph[name] = hashlib.sha256(payload.encode()).hexdigest()

    families: dict[str, list[str]] = {}
    helper_lines: list[str] = []
    for name in sorted(per_glyph):
        stem = name.split(".")[0]
        if stem.startswith("qs"):
            families.setdefault(stem, []).append(f"{name}\t{per_glyph[name]}")
        else:
            helper_lines.append(f"{name}\t{per_glyph[name]}")

    family_digests = {
        stem: hashlib.sha256("\n".join(lines).encode()).hexdigest() for stem, lines in families.items()
    }

    wiring: list = []
    for tag in ("GPOS",):
        if tag not in font:
            continue
        table = font[tag].table  # pyright: ignore[reportAttributeAccessIssue]
        features = [
            (record.FeatureTag, list(record.Feature.LookupListIndex))
            for record in (table.FeatureList.FeatureRecord if table.FeatureList else ())
        ]
        types = [lookup.LookupType for lookup in (table.LookupList.Lookup if table.LookupList else ())]
        wiring.append((tag, features, types))
    helper_lines.append(
        "cmap\t" + hashlib.sha256(repr(sorted((font.getBestCmap() or {}).items())).encode()).hexdigest()
    )
    helper_lines.append("layout\t" + hashlib.sha256(repr(wiring).encode()).hexdigest())
    helpers = hashlib.sha256("\n".join(helper_lines).encode()).hexdigest()
    return family_digests, helpers


# The rebuild/pipeline modules the surface build never imports: the driver, the defect and Manual-pin gates it runs, the baseline oracle, the GPOS emitter, the GSUB packer, read-back, the font compile, the CoreText smoke and the cell enumeration the driver realizes glyphs from. Every other pipeline module is in build.py's walked import closure and rides `surface_code_paths`. An exclusion roster rather than an inclusion one, so a module that lands in the tree is hashed until rebuild/test_review_code_closure.py says the build never reaches it.
PIPELINE_NON_SURFACE_MODULES = frozenset(
    {
        "compile_font.py",
        "coretext_smoke.py",
        "defects.py",
        "emit_gpos.py",
        "manual_pins.py",
        "oracle.py",
        "pack_gsub.py",
        "readback.py",
        "run_m1.py",
        "surface.py",
    }
)

# The crate modules the surface build never executes: the enumeration, its deep-fiber and third- and fourth-slot machinery, the liveness probes, the window options, the fold and its rule fold, the artifact writers and digests, the stream, and the fan-out. The surface reaches the crate through `settle-cases` and `guard-sweep` alone, and those two verbs' handlers in main.rs reach the parser, the index, the engine and its specificity order, the case replay, the guard, the emitter and the error and type vocabularies — which is what stays hashed, beside main.rs and lib.rs themselves. The same exclusion shape as the pipeline roster, held by the same test to the `crate::` references the two handlers reach outside the crate's test modules.
KERNEL_NON_SURFACE_MODULES = frozenset(
    {
        "artifacts.rs",
        "census.rs",
        "fanout.rs",
        "fiber.rs",
        "fixpoint.rs",
        "fold.rs",
        "liveness.rs",
        "options.rs",
        "rulefold.rs",
        "sha256.rs",
        "stream.rs",
    }
)


def surface_code_paths(repo_root: Path) -> list[Path]:
    """The code whose edit drops both per-unit stores: what the surface build actually runs, rather than every tree it might. The review side is `fingerprint.review_code_paths`, already held to build.py's import graph. The pipeline side is rebuild/pipeline minus `PIPELINE_NON_SURFACE_MODULES` and rebuild/validation whole, every module of which the build reaches. The crate side is rebuild/kernel-rs/src minus `KERNEL_NON_SURFACE_MODULES`, plus both Cargo files, since the crate's dependencies and profile shape every verb it answers. Before this closure existed the stamps folded `fingerprint.pipeline_code_paths` whole — every pipeline module, every Rust source, the font-compile tools — so an edit to the driver, a gate, the oracle or the crate's fold dropped the store and the next build paid a cold units phase for code it never executed.

    Module grain, which over-invalidates in the safe direction: a module imported for something the build never calls is still stamped, and the served-vs-recomputed sample inside every build stays the check that a served fragment equals a fresh computation. Two things are left out on purpose. The width and telemetry modules under rebuild/tools that the build takes its fan-out and its cost readings from cannot move a byte of a unit's products — rebuild/test_unit_cache.py's serial-and-parallel byte identity holds the width half — and the test that pins the rosters also pins that those are the only modules the build reaches outside the three trees. The font-compile tools roster is code the build never runs, and the draft harness line hashes tools/*.py anyway.
    """
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    pipeline = [
        path
        for path in sorted((root / "rebuild" / "pipeline").glob("*.py"))
        if path.name not in PIPELINE_NON_SURFACE_MODULES
    ]
    validation = sorted((root / "rebuild" / "validation").glob("*.py"))
    crate = [kernel / "Cargo.toml", kernel / "Cargo.lock"] + [
        path for path in sorted((kernel / "src").rglob("*.rs")) if path.name not in KERNEL_NON_SURFACE_MODULES
    ]
    return pipeline + validation + crate + fingerprint.review_code_paths(root)


def environment_stamp(
    repo_root: Path,
    spec: ResolvedSpec,
    subset_dir: Path,
    before_font: Path,
    junior_font: Path,
    after_helpers_digest: str,
) -> str:
    """The whole-store stamp: any of these moving drops the cache entirely, and over-invalidation is the safe direction. The code line is `surface_code_paths`, the code the build runs and nothing more, so a pipeline or crate edit outside that closure — the driver, a gate, the oracle, the font compile, the crate's enumeration and fold — leaves the stamp where it was and the store serving. The rune files are absent on purpose — they invalidate at per-unit grain through the family keys — and so is the divergence ledger (see the module docstring for why its reach is already covered)."""
    root = Path(repo_root)
    runes = set(fingerprint.rune_paths(root))
    ledger = root / "rebuild" / "m1-divergences.yaml"
    data_lines = sorted(
        f"{path.name}\t{_sha256_file(path)}"
        for path in fingerprint.data_paths(root)
        if path.is_file() and path not in runes and path != ledger
    )
    harness_paths = [root / "test" / "test_shaping.py", root / "postscript_glyph_names.yaml"]
    harness_paths += sorted((root / "tools").glob("*.py"))
    lines = [
        f"format\t{STORE_FORMAT}",
        f"surface_code\t{fingerprint.hash_paths(root, surface_code_paths(root))}",
        "data\t" + hashlib.sha256("\n".join(data_lines).encode()).hexdigest(),
        "settlement_flags\t" + json.dumps(kernel_exec.settlement_flags()),
        f"spec_structure\t{spec_load.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(spec_load.capability_features(spec)),
        f"before_font\t{_sha256_file(Path(before_font))}",
        f"junior_font\t{_sha256_file(Path(junior_font))}",
        "subsets\t"
        + " ".join(
            f"{config}={_sha256_file(Path(subset_dir) / f'baseline-{config}.subset.tsv.gz')}"
            for config in ACCEPTANCE_CONFIGS
        ),
        "corpus\t" + " ".join(f"{name}={_sha256_file(root / name)}" for name in CORPUS_FILES),
        f"draft_harness\t{fingerprint.hash_paths(root, harness_paths)}",
        f"after_helpers\t{after_helpers_digest}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def family_content_keys(repo_root: Path, spec: ResolvedSpec, after_font: Path) -> tuple[dict[str, str], str]:
    """Per family (bare letters and ligature runes alike), the digest a window's content key cites for it: the family's explain-aware rune digest — prose-blind but for the refuse `why` the served explain text quotes — joined with the digests of its static `resolve.against` closure, the one route by which its records read another rune file directly, and the after font's compiled-glyph digest for the family. Returns the family keys plus the after font's helpers digest for the environment stamp."""
    digests = fingerprint.rune_explain_digests(Path(repo_root))
    closure = spec_load.rune_closure(spec)
    glyph_digests, helpers = after_font_glyph_digests(after_font)
    keys: dict[str, str] = {}
    for name in sorted(set(digests) | set(glyph_digests)):
        reach = sorted({name} | set(closure.get(name, frozenset())))
        lines = [f"{member}\t{digests.get(member, '-')}" for member in reach]
        lines.append(f"glyphs\t{glyph_digests.get(name, '-')}")
        keys[name] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return keys, helpers


class UnitKeyer:
    """Computes per-unit content keys over the family keys, memoizing the family-set expansion per distinct window letter set (windows share their letter sets heavily, and the ligature-membership scan need not repeat per unit)."""

    def __init__(self, family_keys: Mapping[str, str], family_of: Mapping[int, str]) -> None:
        self._family_keys = dict(family_keys)
        self._family_of = dict(family_of)
        self._relevant: dict[frozenset[str], tuple[str, ...]] = {}

    def _relevant_families(self, families: frozenset[str]) -> tuple[str, ...]:
        cached = self._relevant.get(families)
        if cached is None:
            cached = tuple(
                name
                for name in sorted(self._family_keys)
                if all(component in families for component in name.split("_"))
            )
            self._relevant[families] = cached
        return cached

    def key(self, unit: Unit) -> str:
        families = frozenset(
            self._family_of[value] for value in unit.codepoint_values if value in self._family_of
        )
        lines = [
            "\t".join(
                (
                    row.config,
                    row.codepoints,
                    ",".join(row.kinds),
                    row.matched_entry,
                    "|".join(row.baseline),
                    "|".join(row.new),
                )
            )
            for row in unit.rows
        ]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()

    def signature_key(self, row: AuditRow) -> str:
        """One ink-signature store entry's content key: the audit row's window, config, the before font's rendered names, and the settled cells the after font is compiled to reproduce — everything the row pins that a signature depends on, deliberately without `kinds` and `matched_entry`, which are classification the shaped ink never reads (a ledger edit must not re-shape a window) — plus the same per-family digests the unit key cites. Truncated to sixteen hex characters, unlike the unit key: this one is written a million times over into a store whose 64-character keys were most of its bytes, and sixty-four bits over a million entries puts a collision at one in forty million — which would in any case only hand one window's sibling group a wrong-but-equal ink signature."""
        families = frozenset(
            self._family_of[value] for value in parse_codepoints(row.codepoints) if value in self._family_of
        )
        lines = ["\t".join((row.config, row.codepoints, "|".join(row.baseline), "|".join(row.new)))]
        lines += [f"{name}\t{self._family_keys[name]}" for name in self._relevant_families(families)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


@dataclass
class CachedUnit:
    """One prior unit's reusable products: the identity needed to fetch its emitted fragment from the prior shards, plus the slim projection the parent's global reduces read. The record also carries the fragment's own `content_key` stamp — distinct from `key`, which is the content key over the unit's *inputs* — and a prior fragment is served only when the stamp on disk equals it, so what is fetched is proved to be the bytes this record describes. `slim` says which shape those bytes are (`audit.slim_fragment`), and the build serves them only when that is the shape it would write for the unit now.

    `address` is where those bytes are: the shard part (the manifest's relative spelling), byte offset and length the shard writer handed back as the fragment went down, the same `(part, start, length)` the app's sidecars carry for a Range fetch. It is recorded from the writer's own return rather than derived from anything else, and it is never a second copy of the stamp: the stamp beside it is what `PriorFragmentReader` holds the bytes at the address to when the fragment is read back at the write. A record without one — a store written before addresses were recorded, or one whose part `load_store` found resized underneath the store — is served through the walk (`locate_prior_fragments`), which re-derives the address off the part's own text.
    """

    key: str
    prior_id: str
    prior_class: str
    content_key: str
    slim: bool
    address: tuple[str, int, int] | None
    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: dict[str, str]
    diffs_digest: str
    cluster: str
    family: str
    pair_codepoints: tuple[int, int] | None
    proj: dict
    seams: list[dict]
    mismatches: list[str]

    def to_record(self) -> dict:
        return {
            "key": self.key,
            "id": self.prior_id,
            "class": self.prior_class,
            "content_key": self.content_key,
            "slim": self.slim,
            "ink_identical": self.ink_identical,
            "picture_identical": self.picture_identical,
            "junior_equivalent": self.junior_equivalent,
            "ink_deltas": self.ink_deltas,
            "diffs_digest": self.diffs_digest,
            "cluster": self.cluster,
            "family": self.family,
            "pair_codepoints": list(self.pair_codepoints) if self.pair_codepoints else None,
            "proj": self.proj,
            "seams": self.seams,
            "mismatches": self.mismatches,
            "address": list(self.address) if self.address else None,
        }

    def located(self) -> "PriorFragment | None":
        """The record's address as the plan's `PriorFragment`, stamped with the record's own `content_key`, or None for a record the walk has to place."""
        if self.address is None:
            return None
        part, start, length = self.address
        return PriorFragment(part, start, length, self.prior_id, self.content_key)

    @classmethod
    def from_record(cls, record: dict) -> "CachedUnit":
        """The record parsed back, with every string the parent holds per served unit and that repeats across units — the class, the cluster and diff digests, the config names and delta digests, the projection's glyph names, cell names and seam tokens — interned through the `sys.intern` table `audit.load_audit` describes, so a store of a million records costs one instance per distinct name rather than one per record."""
        pair = record["pair_codepoints"]
        address = record.get("address")
        proj = record["proj"]
        for name in ("after_cells", "after_seams", "before_glyphs", "before_seams"):
            proj[name] = [sys.intern(value) for value in proj[name]]
        return cls(
            key=record["key"],
            prior_id=record["id"],
            prior_class=sys.intern(record["class"]),
            content_key=record["content_key"],
            slim=record["slim"],
            address=(sys.intern(address[0]), int(address[1]), int(address[2])) if address else None,
            ink_identical=record["ink_identical"],
            picture_identical=record["picture_identical"],
            junior_equivalent=record["junior_equivalent"],
            ink_deltas={
                sys.intern(config): sys.intern(delta) for config, delta in record["ink_deltas"].items()
            },
            diffs_digest=sys.intern(record["diffs_digest"]),
            cluster=sys.intern(record["cluster"]),
            family=sys.intern(record["family"]),
            pair_codepoints=(pair[0], pair[1]) if pair else None,
            proj=proj,
            seams=record["seams"],
            mismatches=list(record["mismatches"]),
        )


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _part_sizes(out_dir: Path, parts: Iterable[str]) -> dict[str, int | None]:
    return {part: _file_size(Path(out_dir) / part) for part in sorted(set(parts))}


def write_store(out_dir: Path, environment: str, records: Iterable[CachedUnit]) -> None:
    """Written after the manifest, stamped with the manifest's identity (`unit_index.manifest_sha256`, which projects the copied UI assets' components out), so a store can prove it describes the shards beside it while an assets refresh that rewrites only that field leaves it current; a crash between the two leaves a stamp mismatch and the next build falls back to a full pass. The header also carries the byte size of every shard part the records' addresses point into, read off the committed parts here: the manifest stamp says nothing about the shards' bytes, and the size is the one fact about a part that a rewrite cannot leave where it was without leaving every address right too, so `load_store` can tell a part whose addresses still hold from one to walk again at the cost of a stat per part. The gzip mtime is pinned so consecutive identical builds stay byte-identical, and the compression level with it — level 1 rather than 9, because this file is written once and read once per build and the four seconds level 9 spends buying ten megabytes on a scratch artifact are four seconds off every cycle."""
    records = list(records)
    header = {
        "format": STORE_FORMAT,
        "environment": environment,
        "manifest_sha256": _manifest_stamp(out_dir),
        "parts": _part_sizes(out_dir, (record.address[0] for record in records if record.address)),
    }
    path = store_path(out_dir)
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
            stream.write((json.dumps(header) + "\n").encode())
            for record in records:
                stream.write((json.dumps(record.to_record()) + "\n").encode())


def load_store(out_dir: Path, environment: str) -> dict[str, CachedUnit] | None:
    """The prior build's records keyed by content key, or None when there is no usable store: absent, unreadable, format- or environment-mismatched, or stamped for a manifest whose identity is not the one on disk (over-invalidation is the safe direction — a None simply costs a full build). A record keeps its address only while the part it points into is the size the header recorded; a record whose part has moved, or that carries no address at all, comes back with `address` None and is placed by the walk instead, so a shard rewritten underneath the store still serves rather than refusing at the write."""
    path = store_path(out_dir)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if header.get("format") != STORE_FORMAT or header.get("environment") != environment:
                return None
            if header.get("manifest_sha256") != _manifest_stamp(out_dir):
                return None
            recorded = header.get("parts") or {}
            trusted = {
                part
                for part, size in _part_sizes(out_dir, recorded).items()
                if size is not None and size == recorded[part]
            }
            records = {}
            for line in stream:
                cached = CachedUnit.from_record(json.loads(line))
                if cached.address is not None and cached.address[0] not in trusted:
                    cached = replace(cached, address=None)
                records[cached.key] = cached
            return records
    except OSError, EOFError, ValueError, KeyError, TypeError, StopIteration:
        return None


def signature_store_path(out_dir: Path) -> Path:
    return Path(out_dir) / SIGNATURE_STORE_NAME


def signature_environment(repo_root: Path, before_font: Path, after_helpers_digest: str) -> str:
    """The ink-signature store's whole-store stamp: only what a signature reads that the per-entry keys do not cover — the shaping code (`surface_code_paths`, the closure the unit store keys on: the Shaper lives in rebuild/validation and the comparator in rebuild/review, and a narrower closure than the build's is not proved), the before font wholesale, and the after font's non-family glyphs, cmap, and layout wiring. See the module docstring for why this is narrower than `environment_stamp`."""
    root = Path(repo_root)
    lines = [
        f"format\t{SIGNATURE_STORE_FORMAT}",
        f"surface_code\t{fingerprint.hash_paths(root, surface_code_paths(root))}",
        f"before_font\t{_sha256_file(Path(before_font))}",
        f"after_helpers\t{after_helpers_digest}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def write_signature_store(out_dir: Path, environment: str, entries: Mapping[str, str]) -> None:
    """One JSON header line, then one `key\\tdigest` line per entry, sorted by key; the pinned gzip mtime and the sort are what keep consecutive builds of the same inputs byte-identical. Written fresh each build with exactly the entries the merge needed, so stale windows age out rather than accumulating. Level 1, like the unit store: this is a million lines of hex, which is incompressible, and level 9 was spending four seconds for well under a percent."""
    header = {"format": SIGNATURE_STORE_FORMAT, "environment": environment}
    with open(signature_store_path(out_dir), "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
            stream.write((json.dumps(header) + "\n").encode())
            for key in sorted(entries):
                stream.write(f"{key}\t{entries[key]}\n".encode())


def load_signature_store(out_dir: Path, environment: str) -> dict[str, str] | None:
    """The prior build's signature digests keyed by content key, or None when there is no usable store — absent, unreadable, or format- or environment-mismatched; a None costs one parallel re-shaping pass, so over-invalidation stays the safe direction here too."""
    path = signature_store_path(out_dir)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if header.get("format") != SIGNATURE_STORE_FORMAT or header.get("environment") != environment:
                return None
            entries: dict[str, str] = {}
            for line in stream:
                key, digest = line.rstrip("\n").split("\t")
                entries[key] = digest
            return entries
    except OSError, EOFError, ValueError, KeyError, TypeError, StopIteration:
        return None


@dataclass(frozen=True)
class PriorFragment:
    """Where one of the prior surface's fragments lives and the stamp it carries: the shard part it was written to (the manifest's relative spelling), the byte offset and length of its own JSON element there, and its `content_key`. The address is the same `(part, start, length)` the app's sidecars carry for a Range fetch. It comes from the store record (`CachedUnit.located`), which took it off the shard writer's own return, or from `locate_prior_fragments`, which re-derives it off the part's own text for a record without one and so stays right for a shard something rewrote by hand as long as the part is still ASCII."""

    part: str
    start: int
    length: int
    unit_id: str
    content_key: str | None


_JSON_WHITESPACE = re.compile(r"[ \t\n\r]*")


def _skip_whitespace(text: str, index: int) -> int:
    match = _JSON_WHITESPACE.match(text, index)
    return match.end() if match else index


def _walk_elements(text: str):
    """The elements of one shard part, each with the character offset and length of its own bytes: the `[`, then one `raw_decode` per element with the commas and whitespace between them skipped, so a fragment is parsed and released before the next is read and the whole part is never resident as objects at once. The framing `_write_shard` lays down is what an address is later read back through, but nothing here assumes it — a compact `json.dumps` of the same list walks the same way — and a part that is not a JSON array raises out to the caller, which treats it as unreadable."""
    decoder = json.JSONDecoder()
    index = _skip_whitespace(text, 0)
    if index >= len(text) or text[index] != "[":
        raise ValueError("a shard part is a JSON array")
    index = _skip_whitespace(text, index + 1)
    while index < len(text) and text[index] != "]":
        fragment, end = decoder.raw_decode(text, index)
        yield index, end - index, fragment
        index = _skip_whitespace(text, end)
        if index < len(text) and text[index] == ",":
            index = _skip_whitespace(text, index + 1)


def locate_prior_fragments(out_dir: Path, wanted: Mapping[str, set[str]]) -> dict[str, PriorFragment]:
    """Where the prior shards hold each of the given {class id: prior unit ids}, keyed by prior id, with the stamp each fragment carries — one pass over the parts the prior manifest names for those classes, parsing one fragment at a time and keeping an address rather than the fragment, so the build decides what to serve without ever holding the previous surface's units. This is the fallback rather than the plan's path: a store record carries the address the shard writer returned, so a served build's plan is a lookup into the store, and the walk is asked only for the records `load_store` handed back without one — an older store's, or those in a part whose size moved underneath the store — which on a build from a surface this code wrote is nothing at all. The prior manifest says which parts a class was written as — a class large enough to be split has no single file to guess at — and a missing or unreadable manifest or part simply contributes nothing, its units falling back to a fresh computation. So does a part that is not pure ASCII: the address is a character offset read back as a byte offset, which only `ensure_ascii` makes the same thing, and no part this build writes is anything else."""
    out_dir = Path(out_dir)
    try:
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        by_id = {meta.get("id"): meta for meta in manifest["classes"]}
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return {}
    located: dict[str, PriorFragment] = {}
    for class_id, ids in wanted.items():
        meta = by_id.get(class_id)
        if meta is None:
            continue
        try:
            parts = unit_index.class_shards(meta)
        except KeyError:
            continue
        for part in parts:
            try:
                raw = (out_dir / part).read_bytes()
                if not raw.isascii():
                    continue
                text = raw.decode("ascii")
                del raw
                for start, length, fragment in _walk_elements(text):
                    unit_id = fragment.get("id") if isinstance(fragment, dict) else None
                    if unit_id in ids:
                        located[unit_id] = PriorFragment(
                            part, start, length, unit_id, fragment.get("content_key")
                        )
            except OSError, ValueError:
                continue
    return located


class PriorFragmentReader:
    """Reads served fragments back out of the prior shards by address — the store record's, or the one the walk recorded — one open part at a time; the build reads them in shard order, so the handle changes once per class rather than once per unit. Each read is held against the address it was made from: the element must still be the unit with the stamp the plan served it under, or the file has changed underneath this build, which is a refusal rather than a fragment. For a store-addressed fragment this is its one parse, and the one place its bytes are ever held to its record."""

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = Path(out_dir)
        self._part: str | None = None
        self._handle: BinaryIO | None = None

    def read(self, located: PriorFragment) -> dict:
        if self._handle is None or self._part != located.part:
            self.close()
            self._handle = (self._out_dir / located.part).open("rb")
            self._part = located.part
        self._handle.seek(located.start)
        fragment = json.loads(self._handle.read(located.length))
        if (
            not isinstance(fragment, dict)
            or fragment.get("id") != located.unit_id
            or fragment.get("content_key") != located.content_key
        ):
            raise ValueError(
                f"{located.part} no longer holds unit {located.unit_id} at bytes "
                f"{located.start}+{located.length}: the prior surface changed underneath this build"
            )
        return fragment

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
            self._part = None

    def __enter__(self) -> "PriorFragmentReader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
