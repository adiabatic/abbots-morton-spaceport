"""Ink-identity comparison for the review surface: a unit is ink_identical when both shipped fonts put exactly the same ink in the same places under every config in the unit's set — only glyph names (and inkless marker glyphs) differ, so no human judgment is meaningful. The method is the proven census reference: shape the unit's text with uharfbuzz via rebuild.validation.shaping.Shaper, record each glyph's outline with fontTools' DecomposingRecordingPen, translate it by the cumulative x_advance plus the glyph's x_offset/y_offset, then sort the placed pieces and compare across fonts. All review-surface shaping is kern-neutral (`kern_neutral`): the rebuild has no kerning until its own later milestone, so the old font's kern feature is pure noise in before/after comparisons and is disabled on both sides."""

from __future__ import annotations

import logging
from pathlib import Path

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

from rebuild.validation.shaping import Shaper

# The M1 mini-font carries epoch-zero head timestamps; fontTools logs a "'created' timestamp seems very low" warning for each, which is noise in the build output.
logging.getLogger("fontTools.ttLib.tables._h_e_a_d").setLevel(logging.ERROR)

VERIFICATION_METHOD = (
    "Shaped with uharfbuzz in both shipped fonts (kerning disabled — the rebuild has no kern feature "
    "until its own milestone, so the old font's kerning is comparison noise) under every config in the "
    "unit's set; outlines decomposed with fontTools DecomposingRecordingPen, translated by the cumulative "
    "x_advance plus each glyph's x_offset/y_offset, sorted, and compared — the placed ink is "
    "identical under every config, so only glyph names differ."
)


def features_for(config: str | None) -> dict[str, bool]:
    """The hb feature dict for a config token: empty for default, one True entry per `+`-joined stylistic-set tag otherwise (matching rebuild.validation.rowmodel.CONFIGS for every acceptance config, and generalizing to table-diff configs)."""
    if not config or config == "default":
        return {}
    return {tag: True for tag in config.split("+")}


def kern_neutral(features: dict[str, bool] | None) -> dict[str, bool]:
    """The review surface's kern-off shaping features: the config's stylistic-set features plus an unconditional `kern: False`, for both fonts. A no-op on the after font (it carries no kern feature yet), but explicit so the rule survives the later kerning milestone, where kern differences get their own review."""
    return {**(features or {}), "kern": False}


def _translate(value: tuple, dx: int, dy: int) -> tuple:
    return tuple(
        (operator, tuple(point if point is None else (point[0] + dx, point[1] + dy) for point in points))
        for operator, points in value
    )


class InkComparator:
    """Holds one Shaper, one glyph set, and one outline cache per font; `ink_identical` is a deterministic boolean over (text, configs)."""

    def __init__(self, before_font: Path | str, after_font: Path | str) -> None:
        self._sides: dict[str, tuple[Shaper, object, dict[str, tuple]]] = {}
        for side, path in (("before", before_font), ("after", after_font)):
            self._sides[side] = (Shaper(path), TTFont(str(path)).getGlyphSet(), {})

    def _outline(self, side: str, name: str) -> tuple:
        _shaper, glyph_set, cache = self._sides[side]
        if name not in cache:
            pen = DecomposingRecordingPen(glyph_set)
            glyph_set[name].draw(pen)
            cache[name] = tuple((operator, tuple(points)) for operator, points in pen.value)
        return cache[name]

    def ink_pieces(self, side: str, text: str, features: dict[str, bool]) -> tuple:
        """The placed outlines of one shaped run, sorted: one piece per glyph that carries ink, translated to its pen position. Inkless glyphs (space, ZWNJ, empty markers) contribute no piece. Shaping is always kern-neutral."""
        shaper = self._sides[side][0]
        result = shaper.shape(text, kern_neutral(features))
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            value = self._outline(side, name)
            if value:
                pieces.append(_translate(value, pen_x + x_offset, y_offset))
            pen_x += x_advance
        pieces.sort()
        return tuple(pieces)

    def ink_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        for config in configs:
            features = features_for(config)
            if self.ink_pieces("before", text, features) != self.ink_pieces("after", text, features):
                return False
        return True
