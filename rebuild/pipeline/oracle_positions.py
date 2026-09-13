"""The section 6 baseline oracle's position channel (M1-PLAN section 6): the kern-normalized old positions, the drift diff between them and the new font's shaped positions, the codec between a drift and the row store's position record, the served-position verifier, the sidecar evaluator that adds the old font's kerns back before the diff, and `_shaper_for`, which picks the shaper every stored position comes from (none without a font, the synthetic overlay shaper under an isolated overlay, HarfBuzz otherwise). `oracle._compare_config` is the caller, through this module's name rather than through imported symbols, so one monkeypatch over `_position_drift` reaches the main loop's renewal slice and `_verify_served_positions` alike (rebuild/test_conform.py's poisoned-drift alarm rests on that).

This module is `oracle_cache.POSITION_CODE_PATHS` alone. The position store stamps its channel with this file's prose-blind digest and nothing else from the comparison side, so an edit to the classifier, a predicate, or the ledger match in rebuild/pipeline/oracle.py leaves every stored position standing while the classifier re-runs over the served verdicts — which is what it does over a served row anyway, by design. What holds the stamp honest is the import direction: this module never imports oracle.py, since the classifier's code would then ride the position stamp and a classifier edit would re-shape every position for nothing. rebuild/test_oracle_code_closure.py walks the import graph from here and holds both directions — everything reachable is named by `ORACLE_ROW_CODE_PATHS` plus `POSITION_CODE_PATHS`, and `rebuild.pipeline.oracle` is unreachable. What the channel reads that this stamp does not name — `conform.Shaper`, `geometry.PIXEL`, the row model — is inside `ORACLE_ROW_CODE_PATHS`, and the position stamp rides on top of the row stamp, so an edit to any of them drops the whole store before the position stamp is consulted.

On the tables' side this module is comparison code: `fingerprint.COMPARISON_CODE_MODULES` names it beside oracle.py, so it rides `pipeline_code_paths` and the run_m1 green while `table_code_paths` leaves it out, and an edit here takes the `run_m1 --gates-only` route rather than forcing a rebuild. rebuild/test_build_code_closure.py holds the build side to never reaching it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rebuild.pipeline import geometry, oracle_cache
from rebuild.pipeline.conform import IsolatedOverlayShaper, Shaper
from rebuild.pipeline.model import ResolvedSpec
from rebuild.validation.rowmodel import Row, format_codepoints

ZWNJ_CODEPOINT = 0x200C


def _shaper_for(
    spec: ResolvedSpec, font_path: Path | None, overlay: bool
) -> "Shaper | IsolatedOverlayShaper | None":
    """The position channel's shaper for one configuration: none without a font, the synthetic overlay shaper under an isolated overlay, HarfBuzz otherwise."""
    if font_path is None:
        return None
    if overlay:
        return IsolatedOverlayShaper(Path(font_path), spec)
    return Shaper(Path(font_path))


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
    """Shape the row's positions against the new font through the shaper's position projection, the offsets and advances alone, and diff drawn positions against the kern-normalized baseline. The comparison is visual, not encoding-level: per-slot glyph origins (pen + x_offset, y_offset) plus the run's total advance, because the two fonts legitimately decompose a seam differently between the left glyph's advance and the right glyph's x_offset while drawing the identical join. Returns (drift descriptions, kern-attributable) or None when every slot and the total match."""
    shaped = shaper.positions(row.text, features)
    if len(shaped) != len(row.glyphs):
        return ((f"slot-count {len(row.glyphs)} (old) vs {len(shaped)} (new)",), False)
    expected, attributable = _kern_normalized_positions(kern, row, geometry.PIXEL)
    drifts: list[str] = []
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
            drifts.append(f"slot {index} ({row.glyphs[index]}): origin want {want}, got {got}")
            kern_attributable = kern_attributable and upstream_attributable
        pen_old += advance
        pen_new += new_advance
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
    store: "oracle_cache.RowStore",
    sample: "oracle_cache.VerificationSample",
) -> None:
    """The position channel's half of `conform._verify_served_sample`: re-shape the pass's stratified sample of served positions through HarfBuzz and prove each against the record it was served from. The sample is drawn per family over the rows whose position was served, so a family whose glyphs moved under a key that failed to notice is caught with probability one; a mismatch is a hard stop for the same reason a row mismatch is — the audit is a fingerprinted artifact and a stale position in it reads as green forever. The sampled rows ride the sample itself, each winner carrying the `Row` the main loop offered it with, so nothing here re-reads the table."""
    for index, row in sample.sampled_rows():
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
