"""The baseline oracle's position comparison (M1-PLAN section 6): the kern-normalized old positions, the mismatch between them and the new font's shaped positions, the codec between a mismatch and the row store's position record, the digest of the settled stream a stored position is keyed on (`settled_digest`), the served-position verifier, the kern sidecar evaluator, and `_shaper_for`, which picks the shaper every stored position comes from. The oracle.py module docstring says why `oracle._compare_config` calls these through the module.

This module is the entry of `oracle_cache.POSITION_CODE_PATHS`, which names its whole import closure: the shapers (rebuild/pipeline/shapers.py), `geometry.PIXEL`, the row model, the record types (rebuild/pipeline/position_record.py), and what those import. The position stamp hashes that closure on its own, because a stored position is served across a move of the row stamp (rebuild/pipeline/oracle_cache.py). So this module imports neither the row store's module nor conform.py, whose settlement walk, settle memo and crate driver the settled digest covers instead. It must never import rebuild/pipeline/oracle.py either, or the classifier's code would be in the position stamp and a classifier edit would re-shape every position. rebuild/test_oracle_code_closure.py walks the import graph from here, checks that `POSITION_CODE_PATHS` names exactly the modules it reaches, and checks that the classifier, the store, and the walk are unreachable.

For the tables' stamp this module is comparison code: `fingerprint.COMPARISON_CODE_MODULES` names it with oracle.py, so it is in `pipeline_code_paths` and the run_m1 green record but not in `table_code_paths`, and a cycle after an edit here runs `run_m1 --gates-only` instead of a rebuild. rebuild/test_build_code_closure.py checks that the build never imports it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from rebuild.pipeline import geometry
from rebuild.pipeline.model import ResolvedSpec, Settled
from rebuild.pipeline.position_record import SETTLED_DIGEST_WIDTH, CachedPosition, PositionVerdict
from rebuild.pipeline.shapers import IsolatedOverlayShaper, Shaper
from rebuild.validation.rowmodel import Row, format_codepoints

ZWNJ_CODEPOINT = 0x200C


def _shaper_for(
    spec: ResolvedSpec, font_path: Path | None, overlay: bool
) -> "Shaper | IsolatedOverlayShaper | None":
    """The position comparison's shaper for one configuration: none without a font, the synthetic overlay shaper under an isolated overlay, HarfBuzz otherwise."""
    if font_path is None:
        return None
    if overlay:
        return IsolatedOverlayShaper(Path(font_path), spec)
    return Shaper(Path(font_path))


def _kern_normalized_positions(
    kern: "KernEvaluator | None", row: Row, pixel: int
) -> tuple[tuple[tuple[int, int, int], ...], tuple[bool, ...]]:
    """Return the baseline row's per-slot position triples with sidecar kerns subtracted from the old advances (the new font has no kerning), plus a per-slot kern-attribution mask. A slot is attributable when its old advance carried a nonzero sidecar kern, when it is a ZWNJ, or when a ZWNJ follows it. A slot's kern partner is the next non-ZWNJ glyph: uni200C is default-ignorable, so HarfBuzz's GPOS pair matching skips it and the old font kerns across a ZWNJ (checked against the baseline: ·Oy ZWNJ ·Pea carries the ·Oy·Pea kern)."""

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


def _position_mismatch(
    shaper: "Shaper | IsolatedOverlayShaper", kern: "KernEvaluator | None", features: frozenset[str], row: Row
) -> tuple[tuple[str, ...], bool] | None:
    """Shape the row with the new font through the shaper's position projection (offsets and advances only) and compare the drawn positions with the kern-normalized baseline. The comparison is visual: per-slot glyph origins (pen + x_offset, y_offset) plus the run's total advance, because the two fonts can split a junction differently between the left glyph's advance and the right glyph's x_offset while drawing the same join. Returns None when every slot and the total match, and otherwise the mismatch descriptions and whether every mismatch follows a kern-attributable slot. A slot-count mismatch returns one description and False."""
    shaped = shaper.positions(row.text, features)
    if len(shaped) != len(row.glyphs):
        return ((f"slot-count {len(row.glyphs)} (old) vs {len(shaped)} (new)",), False)
    expected, attributable = _kern_normalized_positions(kern, row, geometry.PIXEL)
    mismatches: list[str] = []
    kern_attributable = True
    pen_old = 0
    pen_new = 0
    upstream_attributable = False
    for index, ((x, y, advance), (new_x_offset, new_y_offset, new_advance)) in enumerate(
        zip(expected, shaped)
    ):
        want = (pen_old + x, y)
        got = (pen_new + new_x_offset, new_y_offset)
        if got != want:
            mismatches.append(f"slot {index} ({row.glyphs[index]}): origin want {want}, got {got}")
            kern_attributable = kern_attributable and upstream_attributable
        pen_old += advance
        pen_new += new_advance
        upstream_attributable = upstream_attributable or attributable[index]
    if pen_old != pen_new:
        mismatches.append(f"total advance: want {pen_old}, got {pen_new}")
        kern_attributable = kern_attributable and upstream_attributable
    if not mismatches:
        return None
    return (tuple(mismatches), kern_attributable)


def _cached_position(mismatch: tuple[tuple[str, ...], bool] | None) -> CachedPosition | None:
    """Convert a fresh `_position_mismatch` result to the stored form: `None` for a row that matched, otherwise a `CachedPosition` with the mismatch descriptions and the kern flag."""
    return None if mismatch is None else CachedPosition(mismatches=mismatch[0], kern_attributable=mismatch[1])


def _served_position(cached: CachedPosition | None) -> tuple[tuple[str, ...], bool] | None:
    """Convert a stored position verdict back to `_position_mismatch`'s return shape, so the code after the position comparison cannot tell a served row from a freshly shaped one."""
    return None if cached is None else (cached.mismatches, cached.kern_attributable)


def settled_digest(settled: Sequence[Settled], overlay: bool) -> str:
    """Return the digest a record keys its position verdict on: `SETTLED_DIGEST_WIDTH` hex characters of a BLAKE2b over the row's settled stream, every slot's cell (rune, stance, entry, exit, adjustments) with its junction and extension, and over which shaper the configuration shapes through (`overlay`). `gate:conform` checks every cycle that the compiled font selects the settlement's cells, so while the digest and the per-family position keys hold, the glyphs a row shapes to and their geometry hold too. A record carries the digest of the stream both of its verdicts were derived against, and `oracle._compare_config` serves its position after a fresh walk only where the fresh stream has the same digest."""
    text = "|".join(
        [
            f"{item.cell.rune}/{item.cell.stance}/{item.cell.entry}/{item.cell.exit}/{'+'.join(item.cell.adjustments)}/{item.junction}/{item.extension}"
            for item in settled
        ]
    )
    return hashlib.blake2b(
        f"{'overlay' if overlay else 'settled'}|{text}".encode(), digest_size=SETTLED_DIGEST_WIDTH // 2
    ).hexdigest()


def _verify_served_positions(
    shaper: "Shaper | IsolatedOverlayShaper",
    kern: "KernEvaluator | None",
    features: frozenset[str],
    served: Iterable[tuple[Row, PositionVerdict]],
) -> None:
    """Shape the pass's stratified sample of served positions again and compare each with the position verdict it was served from; the position counterpart of `conform._verify_served_sample`. The caller draws the sample per family over the rows whose position was served, whether the row's verdict was served too or derived again with an unchanged settled digest, so a family whose glyphs changed without its key changing is always caught. A mismatch exits, as a row mismatch does, because the audit is a fingerprinted artifact and a stale position in it would never be detected later. Each sampled `Row` is the one the main loop offered, so nothing here re-reads the table."""
    for row, recorded in served:
        fresh = _cached_position(_position_mismatch(shaper, kern, features, row))
        if fresh != recorded:
            raise SystemExit(
                f"the oracle position store served a stale verdict for {format_codepoints(row.codepoints)}: it holds {recorded}, and shaping the row again gives {fresh} — nothing this store holds can be trusted, so rerun with --fresh-oracle-cache and treat the difference as a staleness bug in the position key"
            )


class KernEvaluator:
    """Evaluates glyph_data/senior_quikscript_kerning.yaml for pairs of old glyph names, so sidecar kerns can be added back before a baseline position comparison. Family keys match by name prefix against the pair, as the sidecar documents. Each value depends only on the pair and the sidecar is read once, so values are memoized per pair: a configuration's rows repeat each distinct pair across hundreds of slots on average, and scanning every sidecar rule for each slot took most of the position comparison's time."""

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
