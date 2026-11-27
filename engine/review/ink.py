"""Compare the ink the two fonts put down for a review unit's text.

A unit is `ink_identical` when both fonts put the same ink in the same places under every config in its set, so only glyph names (and inkless marker glyphs) differ and no human judgment is needed. Each unit's text is shaped with uharfbuzz through `engine.validation.shaping.Shaper`, each glyph's outline is recorded with fontTools' `DecomposingRecordingPen`, and the outline is placed at the cumulative x_advance plus the glyph's x_offset and y_offset.

Placed outlines are compared without being built. `OutlineIntern` interns each outline once as a shape key and an own-frame origin, and a placed piece is the triple (shape key, absolute x, absolute y). Two triples are equal exactly when the translated geometry would be, including two glyphs that draw identical strokes from different origins. Translated point tuples are built only for the pieces that end up in a returned delta, which for most texts is none.

There are two readings, which the table-diff build (`build.build_table_diff`, through `diff_units.fragment`) takes for each unit:

- `ink_identical` compares both fonts' sorted placed pieces (`ink_pieces`) under every config.
- `config_diff` returns the pixel-level delta: the cells only one font paints, read over each font's whole rasterized text and not piece by piece. A change that paints no different pixel (an overlap removed at a junction, a stroke passed to a neighbor) does not appear in it. Its sentinel `IDENTITY_DIFF` (no cell lost or gained, no follower shift) is the only implementation of `picture_identical`, the machine check the build tries on every unit that is not ink-identical. Piece identity implies the sentinel, because `config_diff` returns it before rasterizing anything. A unit's Queue `cluster` is made from the delta digests (`delta_digest`) of its configurations. `engine/test_review_ink.py` checks that the sentinel agrees with the reference picture comparison, `picture_equal` over `run_cells`.

All review-corpus shaping is kern-neutral (`kern_neutral`). The corpus compares settlement, joins and drawings, and the `kern` lookup stays out of it (doc/kerning-design.md §4), so both fonts shape with `kern` off and neither font's kerning enters a before-and-after comparison. A kern unit, which shows a moved kern value, is the one reading taken with kerning on, through `engine.review.diff_units.KernedInkComparator`.
"""

from __future__ import annotations

import hashlib
import logging
import marshal
from collections import Counter
from pathlib import Path

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

from engine.validation.classify import PIXEL_SIZE
from engine.validation.shaping import Shaper

# The built font has epoch-zero head timestamps, and fontTools logs a "timestamp seems very low" warning for each one.
logging.getLogger("fontTools.ttLib.tables._h_e_a_d").setLevel(logging.ERROR)

VERIFICATION_METHOD = (
    "Shaped with uharfbuzz in both shipped fonts (kerning disabled in both, so neither font's kerning "
    "enters the comparison) under every config in the "
    "unit's set; outlines decomposed with fontTools DecomposingRecordingPen, translated by the cumulative "
    "x_advance plus each glyph's x_offset/y_offset, sorted, and compared — the placed ink is "
    "identical under every config, so only glyph names differ."
)

PICTURE_VERIFICATION_METHOD = (
    "Shaped with uharfbuzz in both shipped fonts, kern-neutral, under every config in the unit's set; "
    "each placed outline rasterized onto the PIXEL_SIZE grid under nonzero winding and the whole window's "
    "cells unioned per font — both fonts paint exactly the same pixels under every config, so the only "
    "change is which glyph owns which pixel (an overlap removed at a junction, a stroke handed to a neighbor), "
    "which no reviewer can see. Refused, and left for a human, on any curved or off-grid outline or any "
    "off-grid placement."
)


def features_for(config: str | None) -> dict[str, bool]:
    """Return the HarfBuzz feature dict for a config token: empty for `default`, otherwise one True entry per `+`-joined tag."""
    if not config or config == "default":
        return {}
    return {tag: True for tag in config.split("+")}


def kern_neutral(features: dict[str, bool] | None) -> dict[str, bool]:
    """Return the config's features plus `kern: False`, used for both fonts: the corpus compares settlement, joins and drawings, and the `kern` lookup stays out of it (doc/kerning-design.md §4)."""
    return {**(features or {}), "kern": False}


IDENTITY_DIFF = ((), (), 0)


def delta_digest(diff: tuple) -> str:
    """Return the digest of one `config_diff` result: `d-` plus the first twelve hex digits of the sha1 of the tuple's repr, so a localized ink change has one digest in every text where exactly those pixels appear and disappear. A unit's cluster id is made from these digests (`diff_units.fragment`), so changing this recipe or the delta's shape moves every cluster id."""
    return "d-" + hashlib.sha1(repr(diff).encode()).hexdigest()[:12]


def translate_outline(value: tuple, dx: int, dy: int) -> tuple:
    return tuple(
        (operator, tuple(point if point is None else (point[0] + dx, point[1] + dy) for point in points))
        for operator, points in value
    )


def rectilinear_cells(outline: tuple) -> frozenset[tuple[int, int]] | None:
    """Return the PIXEL_SIZE-grid cells a rectilinear outline fills under nonzero winding, which is the pixel picture of one of this family's bitmap-compiled glyphs. Return None for a curve, an unclosed contour, or any coordinate off the grid; callers read None as no picture claim being possible. Sampling the winding number at cell centers is exact, because every edge lies on the grid and no center lies on an edge."""
    verticals: list[tuple[int, int, int, int]] = []
    rows: set[int] = set()
    contour: list[tuple[int, int]] = []
    for operator, points in outline:
        if operator == "moveTo":
            contour = [points[0]]
        elif operator == "lineTo":
            contour.append(points[0])
        elif operator == "closePath":
            for (x1, y1), (x2, y2) in zip(contour, contour[1:] + contour[:1]):
                if x1 % PIXEL_SIZE or y1 % PIXEL_SIZE:
                    return None
                if x1 == x2 and y1 != y2:
                    verticals.append((x1, min(y1, y2), max(y1, y2), 1 if y2 > y1 else -1))
                    rows.update(range(min(y1, y2) // PIXEL_SIZE, max(y1, y2) // PIXEL_SIZE))
            contour = []
        else:
            return None
    if contour:
        return None
    cells: set[tuple[int, int]] = set()
    for row in sorted(rows):
        center = row * PIXEL_SIZE + PIXEL_SIZE // 2
        crossings = sorted((x, direction) for x, low, high, direction in verticals if low < center < high)
        winding = 0
        for (x, direction), (next_x, _next_direction) in zip(crossings, crossings[1:]):
            winding += direction
            if winding:
                cells.update((column, row) for column in range(x // PIXEL_SIZE, next_x // PIXEL_SIZE))
    return frozenset(cells)


EMPTY_OUTLINE_KEY = b""


def outline_shape_key(value: tuple) -> tuple[bytes, int, int]:
    """Split one decomposed outline into a shape key and an origin: the sha1 of the marshal version 2 bytes of the outline translated so its minimum x and minimum y are 0, plus that minimum x and y. Two glyphs drawn identically but placed differently in their own frames (`qsSee.ex-y0.ex-ext-2` and `qsSee.straighter.ex-y0.ex-ext-2`, 50 units apart) share a shape key, and their placements differ by the origins, so a placed piece written as (shape key, absolute x, absolute y) is equal exactly when the placed geometry is. The key is derived from content, not assigned in sequence, so the same outline gets the same key in every process and on every build, which a per-process counter would not give. Marshal version 2 has no back references by object identity, so equal nested tuples key equally even when one shares an object the other rebuilds."""
    points = [point for _operator, points in value for point in points if point is not None]
    if not points:
        return (hashlib.sha1(marshal.dumps(value, 2)).digest(), 0, 0)
    origin_x = min(point[0] for point in points)
    origin_y = min(point[1] for point in points)
    canonical = translate_outline(value, -origin_x, -origin_y)
    return (hashlib.sha1(marshal.dumps(canonical, 2)).digest(), origin_x, origin_y)


class OutlineIntern:
    """The outline table the fonts of one comparison share. `place` interns an outline as its shape key and keeps the origin-normalized value, which `value` returns when a delta piece needs its geometry. Interning by shape, not by glyph name, makes keys comparable across fonts. It lets `config_diff` align, subtract, and normalize over small tuples of ints and bytes, building geometry only for the pieces in the returned delta, and lets the enricher's `_segment_pieces` decide whether a divergent position has visible ink without building any geometry."""

    def __init__(self) -> None:
        self._values: dict[bytes, tuple] = {}
        self._drawn: dict[bytes, bool] = {}
        self._cells: dict[bytes, frozenset[tuple[int, int]] | None] = {}

    def place(self, value: tuple) -> tuple[bytes, int, int]:
        if not value:
            return (EMPTY_OUTLINE_KEY, 0, 0)
        key, origin_x, origin_y = outline_shape_key(value)
        if key not in self._values:
            canonical = translate_outline(value, -origin_x, -origin_y)
            self._values[key] = canonical
            self._drawn[key] = any(point is not None for _op, points in canonical for point in points)
        return (key, origin_x, origin_y)

    def value(self, key: bytes) -> tuple:
        return self._values[key]

    def draws(self, key: bytes) -> bool:
        """Return whether the shape has any point. An outline of bare path operators has none and contributes no x to a delta's normalization."""
        return self._drawn[key]

    def cells(self, key: bytes) -> frozenset[tuple[int, int]] | None:
        """Return the shape's filled grid cells in its canonical frame (minimum x and y at the origin), rasterized once per shape and shared by every placement in both fonts, or None when the shape is not a grid-rectilinear picture."""
        if key not in self._cells:
            self._cells[key] = rectilinear_cells(self._values[key])
        return self._cells[key]


class OutlineCache:
    """One font's decomposed glyph outlines, recorded on first use and cached by glyph name. `placed` translates an outline to a pen position and returns () for an inkless glyph, so callers can skip markers the same way. `shape_key` returns the glyph's entry in the shared intern's key space, where the comparator works."""

    def __init__(self, font_path: Path | str, intern: OutlineIntern | None = None) -> None:
        self._glyph_set = TTFont(str(font_path)).getGlyphSet()
        self._cache: dict[str, tuple] = {}
        self._keys: dict[str, tuple[bytes, int, int]] = {}
        self.intern = intern if intern is not None else OutlineIntern()

    def outline(self, name: str) -> tuple:
        if name not in self._cache:
            pen = DecomposingRecordingPen(self._glyph_set)
            self._glyph_set[name].draw(pen)
            self._cache[name] = tuple((operator, tuple(points)) for operator, points in pen.value)
        return self._cache[name]

    def shape_key(self, name: str) -> tuple[bytes, int, int]:
        """The glyph's (shape key, origin x, origin y), computed once per name."""
        entry = self._keys.get(name)
        if entry is None:
            entry = self._keys[name] = self.intern.place(self.outline(name))
        return entry

    def placed(self, name: str, dx: int, dy: int) -> tuple:
        value = self.outline(name)
        return translate_outline(value, dx, dy) if value else ()


class InkComparator:
    """One Shaper and one OutlineCache per font, the two caches sharing one `OutlineIntern` so pieces compare across fonts as (shape key, absolute x, absolute y) without building translated geometry."""

    def __init__(self, before_font: Path | str, after_font: Path | str) -> None:
        self.intern = OutlineIntern()
        self._sides: dict[str, tuple[Shaper, OutlineCache]] = {}
        for side, path in (("before", before_font), ("after", after_font)):
            self._sides[side] = (Shaper(path), OutlineCache(path, self.intern))

    def ink_pieces(self, side: str, text: str, features: dict[str, bool]) -> tuple:
        """Return the sorted placed pieces of one shaped run: one (shape key, absolute x, absolute y) per glyph with ink, which is `run_ink` without the run order and the own-frame origin. Inkless glyphs (space, ZWNJ, empty markers) contribute nothing. Two pieces are equal exactly when the translated outlines would be, across fonts and processes, so the outlines are never built on this path. `ink_identical` compares this reading. Shaping is kern-neutral."""
        return tuple(sorted(piece[:3] for piece in self.run_ink(side, text, features)))

    def pieces_identical(self, text: str, config: str) -> bool:
        """Return whether both fonts' `ink_pieces` are equal under one config, so the same ink sits in the same places and only glyph names differ. `ink_identical` is this under every config, and `config_diff` makes the same comparison first and returns IDENTITY_DIFF when it holds."""
        features = features_for(config)
        return self.ink_pieces("before", text, features) == self.ink_pieces("after", text, features)

    def ink_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        """Return whether `pieces_identical` holds under every config in the set."""
        return all(self.pieces_identical(text, config) for config in configs)

    def named_run(self, side: str, text: str, features: dict[str, bool]) -> tuple[tuple[str, ...], list]:
        """Return one shaped run's full glyph-name tuple (inkless markers included, so a caller can compare the run with a recorded glyph list) and one (glyph name, shape key, absolute x, absolute y, own-frame origin x) entry per glyph with ink. `run_ink` drops the names and is what the delta alignment reads; names never enter a piece comparison, because the two fonts use different names for the same ink. Shaping is kern-neutral."""
        shaper, outlines = self._sides[side]
        result = shaper.shape(text, kern_neutral(features))
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            key, origin_x, origin_y = outlines.shape_key(name)
            if key:
                pieces.append((name, key, pen_x + x_offset + origin_x, y_offset + origin_y, origin_x))
            pen_x += x_advance
        return result.names, pieces

    def run_ink(self, side: str, text: str, features: dict[str, bool]) -> list:
        """Return the placed ink of one shaped run in run order: one (shape key, absolute x, absolute y, own-frame origin x) entry per glyph with ink, so `config_diff` can align the two fonts' runs glyph by glyph. The multiset subtraction reads only the first three. The fourth distinguishes two glyphs that draw the same strokes from different origins, which the prefix and suffix strips in `_piece_diff` must tell apart. Shaping is kern-neutral."""
        _names, pieces = self.named_run(side, text, features)
        return [piece[1:] for piece in pieces]

    def _piece_cells(self, piece) -> frozenset[tuple[int, int]] | None:
        """Return one placed piece's pixel picture: its shape's cells translated to its placement, empty for a shape with no points, or None when the shape is not a grid-rectilinear picture or the placement is off the grid."""
        key, x, y, _origin = piece
        intern = self.intern
        if not intern.draws(key):
            return frozenset()
        shape = intern.cells(key)
        if shape is None or x % PIXEL_SIZE or y % PIXEL_SIZE:
            return None
        column, row = x // PIXEL_SIZE, y // PIXEL_SIZE
        return frozenset((column + dx, row + dy) for dx, dy in shape)

    def _run_pictures(self, run) -> list[frozenset[tuple[int, int]]] | None:
        """Return every piece of one shaped run as its pixel picture, in run order, or None when any piece has none, which a caller reads as no picture claim being possible for the window."""
        pictures = []
        for piece in run:
            cells = self._piece_cells(piece)
            if cells is None:
                return None
            pictures.append(cells)
        return pictures

    def run_cells(self, side: str, text: str, features: dict[str, bool]) -> set[tuple[int, int]] | None:
        """Return the pixel picture one font paints for the whole window, the union of every placed piece's cells, or None when any piece is not a grid-rectilinear picture or sits off the grid."""
        pictures = self._run_pictures(self.run_ink(side, text, features))
        return None if pictures is None else set().union(*pictures)

    def picture_equal(self, text: str, config: str) -> bool:
        """The reference picture reading of one config: the placed pieces are equal, or both fonts' whole-run cell unions are. A property test in engine/test_review_ink.py checks that it agrees with `config_diff`'s sentinel. A window with no picture reads False unless its pieces are equal, so this reading fails in the same cases as the sentinel."""
        features = features_for(config)
        if self.ink_pieces("before", text, features) == self.ink_pieces("after", text, features):
            return True
        before = self.run_cells("before", text, features)
        return before is not None and before == self.run_cells("after", text, features)

    def picture_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        """Return whether `config_diff` returns IDENTITY_DIFF under every config in the set: both fonts paint the same pixels and nothing slid. `ink_identical` implies it, so the build asks it only of units that are not ink-identical. A window with no cell reading returns False, which leaves it to a human."""
        return all(self.config_diff(text, config) == IDENTITY_DIFF for config in configs)

    def config_diff(self, text: str, config: str) -> tuple:
        """Return the before-to-after ink delta under one config at the pixel level, localized to the changed region: (cells only the before font paints, cells only the after font paints, follower shift in columns).

        Each run is rasterized onto the PIXEL_SIZE grid and unioned per font over the whole window. A pixel one glyph gives up but a neighbor still paints is no change. For example, ·J'ai drops its x-height entry pixel under an ·At that paints that pixel anyway, and the change adds nothing to any window's delta. The same change at a junction the neighbor no longer reaches leaves a real hole and stays in the delta, because the comparison is over the rendered union of this window and not over a list of allowed names.

        Followers that only slid are read at the pixel level too. The longest tail of glyphs whose after picture is the before picture displaced by a whole number of columns is moved back by that displacement before the subtraction, and the displacement is the shift. A tail that includes a pixel given up to a neighbor therefore still counts as slid, which keeps ·Fee·Tea·At·J'ai under ss03 on the same Queue card as every other text that shortens ·Fee the same way. The remaining cells are translated together so the delta's leftmost column is 0.

        IDENTITY_DIFF (nothing lost, nothing gained, no shift) is the sentinel that `picture_identical`, the corpus's per-unit flag, and the empty-delta digest read. A window whose pieces are already equal returns it without rasterizing. A window with no picture (a curved or off-grid outline, or an off-grid placement) falls back to the outline level (`_piece_diff`), which never returns the sentinel for pieces that differ. Two units whose per-config deltas agree show the same pixels appearing and disappearing, whatever unchanged letters surround the change, which is why a unit's cluster is made from them.
        """
        features = features_for(config)
        before = self.run_ink("before", text, features)
        after = self.run_ink("after", text, features)
        if sorted(piece[:3] for piece in before) == sorted(piece[:3] for piece in after):
            return IDENTITY_DIFF
        before_pictures = self._run_pictures(before)
        after_pictures = self._run_pictures(after)
        if before_pictures is None or after_pictures is None:
            return self._piece_diff(before, after)
        return _picture_diff(before_pictures, after_pictures)

    def _piece_diff(self, before: list, after: list) -> tuple:
        """Return the outline-level delta for a window with no picture. The two runs are aligned from both ends: the common prefix (same ink at the same position) and the common suffix (same ink shifted by one uniform dx) are stripped, the remaining middles are multiset-subtracted, and the result is translated so the delta's leftmost point is at x=0. Returns (outlines only the before font draws, outlines only the after font draws, suffix shift in font units). It returns IDENTITY_DIFF only when the middles cancel with no shift, which does not happen for pieces that differ."""
        start = 0
        while start < len(before) and start < len(after) and before[start] == after[start]:
            start += 1
        stripped = 0
        shift = None
        while len(before) - 1 - stripped >= start and len(after) - 1 - stripped >= start:
            key_b, x_b, y_b, origin_b = before[len(before) - 1 - stripped]
            key_a, x_a, y_a, origin_a = after[len(after) - 1 - stripped]
            if key_b != key_a or y_b != y_a or origin_b != origin_a:
                break
            dx = x_a - x_b
            if shift is None:
                shift = dx
            if dx != shift:
                break
            stripped += 1
        if shift is None:
            shift = 0
        middle_before = Counter(piece[:3] for piece in before[start : len(before) - stripped])
        middle_after = Counter(piece[:3] for piece in after[start : len(after) - stripped])
        before_only = list((middle_before - middle_after).elements())
        after_only = list((middle_after - middle_before).elements())
        if not before_only and not after_only:
            return ((), (), shift)
        intern = self.intern
        # Every shape's minimum x is 0 in its own frame, so a piece's leftmost point is its absolute x, and the delta's leftmost point is the smallest of those.
        xs = [x for key, x, _y in before_only + after_only if intern.draws(key)]
        if not xs:
            return ((), (), shift)
        x0 = min(xs)

        def normalize(pieces):
            return tuple(sorted(translate_outline(intern.value(key), x - x0, y) for key, x, y in pieces))

        return (normalize(before_only), normalize(after_only), shift)


def _picture_diff(before: list[frozenset[tuple[int, int]]], after: list[frozenset[tuple[int, int]]]) -> tuple:
    """`config_diff`'s pixel-level arithmetic over two runs' per-piece pictures: find the longest tail of pieces whose after union is the before union displaced by a whole number of columns, move that tail back by the displacement, and subtract the whole-window unions both ways. A tail with no ink yet on either side is skipped, and a tail with ink on only one side matches nothing."""
    tail = shift = 0
    before_tail: set[tuple[int, int]] = set()
    after_tail: set[tuple[int, int]] = set()
    for k in range(1, min(len(before), len(after)) + 1):
        before_tail |= before[-k]
        after_tail |= after[-k]
        if not before_tail or not after_tail:
            continue
        dx = min(column for column, _row in after_tail) - min(column for column, _row in before_tail)
        if {(column - dx, row) for column, row in after_tail} == before_tail:
            tail, shift = k, dx
    before_picture = set().union(*before)
    head = len(after) - tail
    after_picture = set().union(*after[:head])
    after_picture.update((column - shift, row) for cells in after[head:] for column, row in cells)
    lost = before_picture - after_picture
    gained = after_picture - before_picture
    if not lost and not gained:
        return ((), (), shift)
    x0 = min(column for column, _row in (*lost, *gained))
    return (
        tuple(sorted((column - x0, row) for column, row in lost)),
        tuple(sorted((column - x0, row) for column, row in gained)),
        shift,
    )
