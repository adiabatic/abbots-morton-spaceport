"""Ink-identity comparison for the review surface: a unit is ink_identical when both shipped fonts put exactly the same ink in the same places under every config in the unit's set — only glyph names (and inkless marker glyphs) differ, so no human judgment is meaningful. The method is the proven census reference: shape the unit's text with uharfbuzz via rebuild.validation.shaping.Shaper, record each glyph's outline with fontTools' DecomposingRecordingPen, translate it by the cumulative x_advance plus the glyph's x_offset/y_offset, then sort the placed pieces and compare across fonts. All review-surface shaping is kern-neutral (`kern_neutral`): the rebuild has no kerning until its own later milestone, so the old font's kern feature is pure noise in before/after comparisons and is disabled on both sides."""

from __future__ import annotations

import logging
from collections import Counter
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

JUNIOR_VERIFICATION_METHOD = (
    "Divergent only under ss10 (suppress all joins), where the ratified spec is fully isolated letters; "
    "shaped with uharfbuzz in the rebuild under ss10 and in the shipped Junior font (the canonical "
    "isolated rendering) with no features, kern-neutral on both sides; outlines decomposed, placed, and "
    "compared after removing Junior's uniform one-pixel-per-letter tracking (verified against the shipped "
    "Senior at construction) — the rebuild draws every letter exactly as Junior draws it in isolation."
)


def features_for(config: str | None) -> dict[str, bool]:
    """The hb feature dict for a config token: empty for default, one True entry per `+`-joined stylistic-set tag otherwise (matching rebuild.validation.rowmodel.CONFIGS for every acceptance config, and generalizing to table-diff configs)."""
    if not config or config == "default":
        return {}
    return {tag: True for tag in config.split("+")}


def kern_neutral(features: dict[str, bool] | None) -> dict[str, bool]:
    """The review surface's kern-off shaping features: the config's stylistic-set features plus an unconditional `kern: False`, for both fonts. A no-op on the after font (it carries no kern feature yet), but explicit so the rule survives the later kerning milestone, where kern differences get their own review."""
    return {**(features or {}), "kern": False}


def translate_outline(value: tuple, dx: int, dy: int) -> tuple:
    return tuple(
        (operator, tuple(point if point is None else (point[0] + dx, point[1] + dy) for point in points))
        for operator, points in value
    )


class OutlineCache:
    """One font's decomposed glyph outlines, recorded lazily and cached by glyph name; `placed` translates an outline to a pen position, returning () for an inkless glyph so callers can skip markers uniformly."""

    def __init__(self, font_path: Path | str) -> None:
        self._glyph_set = TTFont(str(font_path)).getGlyphSet()
        self._cache: dict[str, tuple] = {}

    def outline(self, name: str) -> tuple:
        if name not in self._cache:
            pen = DecomposingRecordingPen(self._glyph_set)
            self._glyph_set[name].draw(pen)
            self._cache[name] = tuple((operator, tuple(points)) for operator, points in pen.value)
        return self._cache[name]

    def placed(self, name: str, dx: int, dy: int) -> tuple:
        value = self.outline(name)
        return translate_outline(value, dx, dy) if value else ()


class InkComparator:
    """Holds one Shaper and one OutlineCache per font; `ink_identical` is a deterministic boolean over (text, configs)."""

    def __init__(self, before_font: Path | str, after_font: Path | str) -> None:
        self._sides: dict[str, tuple[Shaper, OutlineCache]] = {}
        for side, path in (("before", before_font), ("after", after_font)):
            self._sides[side] = (Shaper(path), OutlineCache(path))

    def ink_pieces(self, side: str, text: str, features: dict[str, bool]) -> tuple:
        """The placed outlines of one shaped run, sorted: one piece per glyph that carries ink, translated to its pen position. Inkless glyphs (space, ZWNJ, empty markers) contribute no piece. Shaping is always kern-neutral."""
        shaper, outlines = self._sides[side]
        result = shaper.shape(text, kern_neutral(features))
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            placed = outlines.placed(name, pen_x + x_offset, y_offset)
            if placed:
                pieces.append(placed)
            pen_x += x_advance
        pieces.sort()
        return tuple(pieces)

    def ink_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        for config in configs:
            features = features_for(config)
            if self.ink_pieces("before", text, features) != self.ink_pieces("after", text, features):
                return False
        return True

    def signature(self, text: str, config: str) -> tuple:
        """The rendered-outcome identity of one text under one config: the (before pieces, after pieces) pair. Two rows whose signatures are equal put exactly the same ink in the same places in both fonts, so they present the same visual question no matter how their glyph names differ."""
        features = features_for(config)
        return (self.ink_pieces("before", text, features), self.ink_pieces("after", text, features))

    def junior_pieces(self, text: str, tracking: int) -> tuple:
        """The before side's placed ink with a uniform letter tracking removed: like ink_pieces with no features, but each Quikscript glyph advances the pen by its advance minus `tracking`, so the pieces land where a tracking-free rendering would put them. Only meaningful when the before side is the Junior font; see JuniorOracle."""
        shaper, outlines = self._sides["before"]
        result = shaper.shape(text, kern_neutral({}))
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            placed = outlines.placed(name, pen_x + x_offset, y_offset)
            if placed:
                pieces.append(placed)
            pen_x += x_advance - (tracking if name.startswith("qs") else 0)
        pieces.sort()
        return tuple(pieces)

    def run_ink(self, side: str, text: str, features: dict[str, bool]) -> list:
        """The placed ink of one shaped run in run order: one (own-frame outline, pen position) entry per glyph that carries ink, so config_diff can align the two fonts' runs glyph-by-glyph. Shaping is always kern-neutral."""
        shaper, outlines = self._sides[side]
        result = shaper.shape(text, kern_neutral(features))
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            placed = outlines.placed(name, 0, y_offset)
            if placed:
                pieces.append((placed, pen_x + x_offset))
            pen_x += x_advance
        return pieces

    def config_diff(self, text: str, config: str) -> tuple:
        """The before→after ink delta under one config, localized to the changed region: the two shaped runs are aligned glyph-by-glyph from both ends, stripping the common prefix (same ink at the same position) and the common suffix (same ink rigidly shifted by one uniform dx — followers that merely slid over because the change altered the run's advance), and the remaining middles are multiset-subtracted and jointly translated so the delta's leftmost point sits at x=0. Returns (pieces only the before font draws, pieces only the after font draws, suffix shift); ((), (), 0) means ink-identical. Two units whose judged pair, class, config set, and per-config deltas all agree show the same pixels appearing and disappearing — the echo-group key — no matter which unchanged letters surround the change."""
        features = features_for(config)
        before = self.run_ink("before", text, features)
        after = self.run_ink("after", text, features)
        start = 0
        while start < len(before) and start < len(after) and before[start] == after[start]:
            start += 1
        stripped = 0
        shift = None
        while len(before) - 1 - stripped >= start and len(after) - 1 - stripped >= start:
            outline_before, pen_before = before[len(before) - 1 - stripped]
            outline_after, pen_after = after[len(after) - 1 - stripped]
            if outline_before != outline_after:
                break
            dx = pen_after - pen_before
            if shift is None:
                shift = dx
            if dx != shift:
                break
            stripped += 1
        if shift is None:
            shift = 0
        middle_before = Counter(
            translate_outline(outline, pen, 0) for outline, pen in before[start : len(before) - stripped]
        )
        middle_after = Counter(
            translate_outline(outline, pen, 0) for outline, pen in after[start : len(after) - stripped]
        )
        before_only = list((middle_before - middle_after).elements())
        after_only = list((middle_after - middle_before).elements())
        xs = [
            point[0]
            for piece in before_only + after_only
            for _operator, points in piece
            for point in points
            if point is not None
        ]
        if not xs:
            return ((), (), shift)
        x0 = min(xs)

        def normalize(pieces):
            return tuple(
                sorted(
                    tuple(
                        (
                            operator,
                            tuple(point if point is None else (point[0] - x0, point[1]) for point in points),
                        )
                        for operator, points in piece
                    )
                    for piece in pieces
                )
            )

        return (normalize(before_only), normalize(after_only), shift)


class JuniorOracle:
    """The second machine-approval channel, alongside ink identity: a unit divergent only under ss10 is approvable when the rebuild's ss10 rendering places exactly the ink the shipped Junior font places for the same string, once Junior's letter tracking is removed. Junior carries the same isolated letterforms as Senior plus one pixel of extra advance on every Quikscript glyph; the constructor verifies that premise against the shipped Senior and derives the tracking from it, refusing to run if the fonts ever drift from it. A pass means the rebuild draws every letter fully isolated — the ratified meaning of ss10 (see the ss10 ledger entries in rebuild/m1-divergences.yaml) — so approval is mechanical regardless of what the old font did."""

    def __init__(self, junior_font: Path | str, before_font: Path | str, after_font: Path | str) -> None:
        junior_metrics = TTFont(str(junior_font))["hmtx"].metrics
        before_metrics = TTFont(str(before_font))["hmtx"].metrics
        shared = set(junior_metrics) & set(before_metrics)
        deltas = {name: junior_metrics[name][0] - before_metrics[name][0] for name in shared}
        letter_deltas = {delta for name, delta in deltas.items() if name.startswith("qs")}
        other_deltas = {delta for name, delta in deltas.items() if not name.startswith("qs")}
        if len(letter_deltas) != 1 or other_deltas - {0}:
            raise ValueError(
                "the Junior tracking premise does not hold: Quikscript advance deltas "
                f"{sorted(letter_deltas)} (expected exactly one value), non-Quikscript deltas "
                f"{sorted(other_deltas - {0})} (expected none)"
            )
        self.tracking = next(iter(letter_deltas))
        self._comparator = InkComparator(junior_font, after_font)

    def approves(self, configs, text: str) -> bool:
        if tuple(configs) != ("ss10",):
            return False
        junior = self._comparator.junior_pieces(text, self.tracking)
        return junior == self._comparator.ink_pieces("after", text, features_for("ss10"))
