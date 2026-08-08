"""The persisted per-unit surface cache (issue 20): the review build's phase-1/phase-2 products carried across builds, keyed by per-unit content keys, so a one-rune edit re-enriches the windows that could feel it and serves everything else from the previous surface's shards.

A unit's expensive products — the ink diffs and machine-approval flags, the enrichment (cells, seams, highlights, explain, provenance), and the three drafts — are a pure function of a nameable closure, and the cache's soundness is exactly the claim that the content key covers that closure. The key is two-grained, mirroring the trace memo (rebuild/pipeline/trace_memo.py): per unit, the audit rows (which pin the window, its configs, both fonts' rendered names, and the matched ledger classes) plus a per-family digest for every window letter — the family's prose-blind rune digest expanded by its static `resolve.against` closure, joined with a digest of the after font's compiled glyphs for that family (outlines, advances, and cursive anchors, so a drawing or anchor change invalidates even when no name in the rows moves) — with ligature families included whenever all their components appear in the window. Whole store, everything that can move a unit's products without moving a named family: the pipeline and review code, the non-rune data files, the engine's semantics flags, the resolved spec structure and capability-feature universe (cross-rune routes: predicate-class and group memberships, ligature sequences, the formation guard's feature combos), the before and Junior fonts wholesale, the acceptance configs' subset tables, the draft harness (test/test_shaping.py, tools/, postscript_glyph_names.yaml) and the three site corpus files it validates pins against, and the after font's non-family glyphs and layout wiring. The divergence ledger is deliberately not in the store stamp: its per-unit effects reach the shards only through the audit's matched_entry column (in the rows) or through fields the build re-derives and re-patches on every pass (no_verdict, exemplar, class promotion), so a ledger edit invalidates exactly the units whose rows it moved.

What the store serves is the previous build's emitted fragment (read back from the shards it lives in) plus the slim projection the parent's global reduces need: the machine flags and ink deltas, the verdict family, the judged pair, the ink-diff digest for echo grouping, the seam-home projection and per-seam rects, and the unit's mismatch lines. Everything order-derived or ledger-derived — id, batch, echo, class, no_verdict, exemplar, the secondary-seam homes — is recomputed over the full universe every build and patched into served fragments, so a cache hit never freezes a global field; the cluster id alone is trusted from the served fragment, because its inputs (configs, final class, ink diffs) are all under the key. The byte-identity gate (rebuild/test_review_build.py::test_builds_are_byte_identical) is the standing proof: an incrementally rebuilt live surface must match a from-scratch build byte for byte.

This module also owns the carry content key (the render identity rebuild/tools/carry_verdicts.py resolves prior verdicts against), so the build can stamp each unit's `content_key` at emission time and carry can probe stamped hashes instead of re-serializing every unit — one definition, shared by both sides, with the stamp itself excluded from the projection it hashes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from rebuild.pipeline import fingerprint, trace_memo
from rebuild.pipeline import settle as settle_module
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review.audit import ACCEPTANCE_CONFIGS, Unit
from rebuild.review.drafts import CORPUS_FILES

STORE_FORMAT = "ams-review-unit-cache/1"
STORE_NAME = "unit-cache.ndjson.gz"

# The carry identity's non-participating fields (rebuild/tools/carry_verdicts.py imports this): id, batch, no_verdict, exemplar, echo, and cluster are order- or ledger-derived and churn whenever the surface renumbers; explain, drafts, provenance, and secondary_seams are derived presentation whose adjudicable content is already covered by the window plus both fonts' glyphs, cells, and seams; ink_deltas is the same delta identity persisted per config; content_key is the stamp of this very projection and must not feed itself.
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
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
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
    """Per qs family, a digest over the after font's compiled glyphs whose name stem belongs to it (decomposed outline operations, so subroutine plumbing can never hide a change; advance and sidebearing; cursive anchors), plus one environment digest over everything else the shaped run can touch regardless of family: the non-qs glyphs (boundary and marker helpers), the cmap, and the GPOS feature-to-lookup wiring."""
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
    for tag in ("GSUB", "GPOS"):
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


def _capability_features(spec: ResolvedSpec) -> list[str]:
    return sorted(
        {
            unlock.feature
            for rune in spec.runes.values()
            for stance in rune.stances.values()
            for unlock in stance.surface.unlocks
        }
    )


def environment_stamp(
    repo_root: Path,
    spec: ResolvedSpec,
    subset_dir: Path,
    before_font: Path,
    junior_font: Path,
    after_helpers_digest: str,
) -> str:
    """The whole-store stamp: any of these moving drops the cache entirely, and over-invalidation is the safe direction. The rune files are absent on purpose — they invalidate at per-unit grain through the family keys — and so is the divergence ledger (see the module docstring for why its reach is already covered)."""
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
        f"pipeline_code\t{fingerprint.hash_paths(root, fingerprint.pipeline_code_paths(root))}",
        f"review_code\t{fingerprint.hash_paths(root, fingerprint.review_code_paths(root))}",
        "data\t" + hashlib.sha256("\n".join(data_lines).encode()).hexdigest(),
        f"engine_flags\tsimulated_prospect={settle_module.SIMULATED_PROSPECT_DEFAULT} vote_slots={settle_module.VOTE_SLOTS_DEFAULT}",
        f"spec_structure\t{trace_memo.spec_structure_digest(spec)}",
        "capability_features\t" + json.dumps(_capability_features(spec)),
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
    """Per family (bare letters and ligature runes alike), the digest a window's content key cites for it: the family's prose-blind rune digest joined with the digests of its static `resolve.against` closure — the one route by which its records read another rune file directly — and the after font's compiled-glyph digest for the family. Returns the family keys plus the after font's helpers digest for the environment stamp."""
    digests = fingerprint.rune_digests(Path(repo_root))
    closure = trace_memo.rune_closure(spec)
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


@dataclass
class CachedUnit:
    """One prior unit's reusable products: the identity needed to fetch its emitted fragment from the prior shards, plus the slim projection the parent's global reduces read."""

    key: str
    prior_id: str
    prior_class: str
    ink_identical: bool
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
            "ink_identical": self.ink_identical,
            "junior_equivalent": self.junior_equivalent,
            "ink_deltas": self.ink_deltas,
            "diffs_digest": self.diffs_digest,
            "cluster": self.cluster,
            "family": self.family,
            "pair_codepoints": list(self.pair_codepoints) if self.pair_codepoints else None,
            "proj": self.proj,
            "seams": self.seams,
            "mismatches": self.mismatches,
        }

    @classmethod
    def from_record(cls, record: dict) -> "CachedUnit":
        pair = record["pair_codepoints"]
        return cls(
            key=record["key"],
            prior_id=record["id"],
            prior_class=record["class"],
            ink_identical=record["ink_identical"],
            junior_equivalent=record["junior_equivalent"],
            ink_deltas=dict(record["ink_deltas"]),
            diffs_digest=record["diffs_digest"],
            cluster=record["cluster"],
            family=record["family"],
            pair_codepoints=(pair[0], pair[1]) if pair else None,
            proj=record["proj"],
            seams=record["seams"],
            mismatches=list(record["mismatches"]),
        )


def write_store(out_dir: Path, environment: str, records: Iterable[CachedUnit]) -> None:
    """Written after the manifest, stamped with the manifest's bytes, so a store can prove it describes the shards beside it; a crash between the two leaves a stamp mismatch and the next build falls back to a full pass. The gzip mtime is pinned so consecutive identical builds stay byte-identical."""
    manifest_sha = _sha256_file(Path(out_dir) / "manifest.json")
    header = {"format": STORE_FORMAT, "environment": environment, "manifest_sha256": manifest_sha}
    path = store_path(out_dir)
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write((json.dumps(header) + "\n").encode())
            for record in records:
                stream.write((json.dumps(record.to_record()) + "\n").encode())


def load_store(out_dir: Path, environment: str) -> dict[str, CachedUnit] | None:
    """The prior build's records keyed by content key, or None when there is no usable store: absent, unreadable, format- or environment-mismatched, or stamped for a manifest other than the one on disk (over-invalidation is the safe direction — a None simply costs a full build)."""
    path = store_path(out_dir)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if header.get("format") != STORE_FORMAT or header.get("environment") != environment:
                return None
            if header.get("manifest_sha256") != _sha256_file(Path(out_dir) / "manifest.json"):
                return None
            records = {}
            for line in stream:
                cached = CachedUnit.from_record(json.loads(line))
                records[cached.key] = cached
            return records
    except OSError, EOFError, ValueError, KeyError, TypeError, StopIteration:
        return None


def load_prior_fragments(out_dir: Path, wanted: Mapping[str, set[str]]) -> dict[str, dict]:
    """The prior shards' emitted fragments for the given {class id: prior unit ids}, keyed by prior id. A missing or unreadable shard simply contributes nothing — its units fall back to a fresh computation."""
    fragments: dict[str, dict] = {}
    for class_id, ids in wanted.items():
        shard_path = Path(out_dir) / "units" / f"{class_id}.json"
        try:
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        for fragment in shard:
            if fragment.get("id") in ids:
                fragments[fragment["id"]] = fragment
    return fragments
