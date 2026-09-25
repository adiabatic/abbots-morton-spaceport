"""Compare the ink the two fonts put down for a review unit.

A unit is `ink_identical` when both fonts put the same ink in the same places under every config in its set, so only glyph names (and inkless marker glyphs) differ and no human judgment is needed. Each unit's text is shaped with uharfbuzz through `rebuild.validation.shaping.Shaper`, each glyph's outline is recorded with fontTools' `DecomposingRecordingPen`, and the outline is placed at the cumulative x_advance plus the glyph's x_offset and y_offset.

Placed outlines are compared without being built. `OutlineIntern` interns each outline once as a shape key and an own-frame origin, and a placed piece is the triple (shape key, absolute x, absolute y). Two triples are equal exactly when the translated geometry would be, including two glyphs that draw identical strokes from different origins. Translated point tuples are built only for the pieces that end up in a returned delta, which for most windows is none.

There are two readings:

- `ink_identical` compares both fonts' sorted placed pieces (`ink_pieces`) under every config. `ink_histogram` in `rebuild/review/census.py` flags units with it.
- `config_diff` returns the picture-grain delta that every deduplication channel keys on: the cells only one font paints, read over each font's whole rasterized window and not piece by piece. A change that paints no different pixel (an overlap removed at a seam, a stroke passed to a neighbor) does not appear in it, so a window containing such a change has the same digest as its siblings without it. Its sentinel `IDENTITY_DIFF` (no cell lost or gained, no follower shift) is the only implementation of `picture_identical`, the machine channel the build checks for every unit that is not ink-identical. Piece identity implies the sentinel, because `config_diff` returns it before rasterizing anything. `rebuild/test_review_ink.py` checks over the frozen windows that the sentinel agrees with the reference picture comparison, `picture_equal` over `run_cells`.

`signature` is built from the same two `run_ink` lists that `config_diff` and `ink_pieces` read, so equal signatures give equal deltas, equal ink flags, and equal delta digests without any sampling.

All review-surface shaping is kern-neutral (`kern_neutral`). The rebuild has no kerning yet, so the old font's kern feature would only add noise to before-and-after comparisons, and it is disabled on both sides.
"""

from __future__ import annotations

import hashlib
import logging
import marshal
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, NamedTuple

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

from rebuild.validation.classify import PIXEL_SIZE
from rebuild.validation.shaping import Shaper, ShapeResult

# The M1 font has epoch-zero head timestamps, and fontTools logs a "timestamp seems very low" warning for each one.
logging.getLogger("fontTools.ttLib.tables._h_e_a_d").setLevel(logging.ERROR)

VERIFICATION_METHOD = (
    "Shaped with uharfbuzz in both shipped fonts (kerning disabled — the rebuild has no kern feature "
    "until its own milestone, so the old font's kerning is comparison noise) under every config in the "
    "unit's set; outlines decomposed with fontTools DecomposingRecordingPen, translated by the cumulative "
    "x_advance plus each glyph's x_offset/y_offset, sorted, and compared — the placed ink is "
    "identical under every config, so only glyph names differ."
)

PICTURE_VERIFICATION_METHOD = (
    "Shaped with uharfbuzz in both shipped fonts, kern-neutral, under every config in the unit's set; "
    "each placed outline rasterized onto the PIXEL_SIZE grid under nonzero winding and the whole window's "
    "cells unioned per font — both fonts paint exactly the same pixels under every config, so the only "
    "change is which glyph owns which pixel (an overlap removed at a seam, a stroke handed to a neighbor), "
    "which no reviewer can see. Refused, and left for a human, on any curved or off-grid outline or any "
    "off-grid placement."
)

JUNIOR_VERIFICATION_METHOD = (
    "Divergent only under ss10 (suppress all joins), where the ratified spec is fully isolated letters; "
    "shaped with uharfbuzz in the rebuild under ss10 and in the shipped Junior font (the canonical "
    "isolated rendering) with no features, kern-neutral on both sides; outlines decomposed, placed, and "
    "compared after removing Junior's uniform one-pixel-per-letter tracking (verified against the shipped "
    "Senior at construction) — the rebuild draws every letter exactly as Junior draws it in isolation."
)


def features_for(config: str | None) -> dict[str, bool]:
    """Return the HarfBuzz feature dict for a config token: empty for `default`, otherwise one True entry per `+`-joined tag. This matches `rebuild.validation.rowmodel.CONFIGS` for every acceptance config and also covers table-diff configs."""
    if not config or config == "default":
        return {}
    return {tag: True for tag in config.split("+")}


def kern_neutral(features: dict[str, bool] | None) -> dict[str, bool]:
    """Return the config's features plus `kern: False`, used for both fonts. The after font has no kern feature yet; the entry is explicit so the rule still holds once the rebuild has kerning, when kern differences get their own review."""
    return {**(features or {}), "kern": False}


IDENTITY_DIFF = ((), (), 0)


def delta_digest(diff: tuple) -> str:
    """Return the stored id of one `config_diff` result: `d-` plus the first twelve hex digits of the sha1 of the tuple's repr. The surface stores one digest per config whose delta is not `IDENTITY_DIFF` (the unit JSON's `ink_deltas`), so a standing-approval rule can approve a localized ink change once and match every window, in any batch, where exactly those pixels appear and disappear. The digests recorded in rebuild/standing-approvals.yaml depend on both this recipe and the delta's shape, as the cluster id depends on its repr recipe. Changing either invalidates every recorded digest, and the digests are then re-derived from the standing probe's family line, not edited by hand."""
    return "d-" + hashlib.sha1(repr(diff).encode()).hexdigest()[:12]


def signature_digest(signature: tuple) -> str:
    """Return the sha256 of the marshal version 2 bytes of one `InkComparator.signature` result. Version 2 predates marshal's back references by object identity, so equal nested tuples give equal bytes even when one shares an object the other rebuilds. The ink-duplicate merge only groups by the value, so digest equality can stand in for signature equality, and the surface build can serve signatures from the persisted store (rebuild/review/unit_cache.py) instead of reshaping every relabel-split window on each pass. Unlike `delta_digest`, these digests are recorded in nothing checked in: they are compared only within one build and stored in a cache that is discarded on any stamp mismatch, so changing the signature's form costs one store miss."""
    return hashlib.sha256(marshal.dumps(signature, 2)).hexdigest()


class ShapeMemoCensus(NamedTuple):
    """A shape memo's entry count and an approximate byte size of its keys and results. The size is `sys.getsizeof` summed over the containers and the ints they hold, so it does not count the glyph-name strings (the font's glyph-order strings, shared by every entry that shapes the same glyph) and does not discount the small ints CPython caches. It is cheap enough to take at a batch boundary. Measurements read it beside a worker's peak memory (issue #150); the build never acts on it."""

    entries: int
    approx_bytes: int


def _approx_entry_bytes(key: tuple, result: ShapeResult) -> int:
    text, features = key
    size = sys.getsizeof(key) + sys.getsizeof(text)
    if features is not None:
        size += sys.getsizeof(features) + sum(
            sys.getsizeof(pair) + sys.getsizeof(pair[0]) for pair in features
        )
    size += sys.getsizeof(result) + sys.getsizeof(result.__dict__)
    size += sys.getsizeof(result.names) + sys.getsizeof(result.clusters) + sys.getsizeof(result.positions)
    size += sum(sys.getsizeof(cluster) for cluster in result.clusters)
    size += sum(
        sys.getsizeof(position) + sum(sys.getsizeof(v) for v in position) for position in result.positions
    )
    return size


class _MemoizedShaper(Shaper):
    """A Shaper whose `shape` memoizes by (text, features). The surface build shapes the same (text, config) in `config_diff`, in `Enricher.enrich`, in the JuniorOracle, and in the Drafter's semantics replay, and the memo turns those four calls into one HarfBuzz call, because a fragment is drafted in the same batch that enriched it. Only the surface build uses it, through `shaper_for`.

    The memo holds one unit batch of shapes at a time. The surface build calls `release_shape_memos` after every unit batch (`_phase1_batches` and `_released_batches` in rebuild/review/build.py, in the pool worker and the in-process runner alike) and once after the parent's serial signature pass. The release is required. Measured on one tree one pass apart (issue #150): a serial build with the release peaked at 15.4 GB in the `surface-build` step and finished its units phase in about twenty minutes. The same build with `release` made a no-op held 6.9 million shapes, about 10.5 GB by `census`, when it was just past half the corpus. By then it had used most of the 32 GiB machine's swap at twice the elapsed time, and it was stopped there. The memo grows linearly with the units, at about 1.5 KB a shape, so without the release it outgrows every other collection a serial build holds, the retained EnrichedUnits included.
    """

    def __init__(self, font_path: Path | str) -> None:
        super().__init__(font_path)
        self._memo: dict[tuple, ShapeResult] = {}

    def shape(self, text: str, features: dict[str, bool] | None = None) -> ShapeResult:
        key = (text, tuple(sorted(features.items())) if features else None)
        result = self._memo.get(key)
        if result is None:
            result = self._memo[key] = super().shape(text, features)
        return result

    def release(self) -> None:
        """Forget every shape held. Every release path calls this method, so an A/B measurement of the bound replaces the `clear()` line with `pass` and changes nothing else."""
        self._memo.clear()

    def census(self) -> ShapeMemoCensus:
        return ShapeMemoCensus(
            len(self._memo), sum(_approx_entry_bytes(key, result) for key, result in self._memo.items())
        )


_shaper_registry: dict[tuple[str, int, int], _MemoizedShaper] = {}


def release_shape_memos() -> None:
    """Release every memo `shaper_for` has handed out in this process. The surface build calls it at each unit batch boundary. The shapers stay registered, so their fonts are not loaded again."""
    for shaper in _shaper_registry.values():
        shaper.release()


def shape_memo_census() -> ShapeMemoCensus:
    """Return the sum of every registered memo's census. A measurement reads it beside the process's peak RSS, at a batch boundary or, with the release disabled, when a worker stops."""
    entries = approx_bytes = 0
    for shaper in _shaper_registry.values():
        census = shaper.census()
        entries += census.entries
        approx_bytes += census.approx_bytes
    return ShapeMemoCensus(entries, approx_bytes)


def shaper_for(font_path: Path | str) -> Shaper:
    """Return the surface build's shared memoized Shaper for one font, keyed by (resolved path, mtime, size) so that a font rewritten in place, such as a test building two surfaces over different mini fonts at one path, never gets stale shapes. Sharing one instance across the comparator, oracle, enricher, and drafter also loads each font once per process instead of four times."""
    path = Path(font_path).resolve()
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    shaper = _shaper_registry.get(key)
    if shaper is None:
        shaper = _shaper_registry[key] = _MemoizedShaper(path)
    return shaper


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
    """Split one decomposed outline into a shape key and an origin: the sha1 of the marshal version 2 bytes of the outline translated so its minimum x and minimum y are 0, plus that minimum x and y. Two glyphs drawn identically but placed differently in their own frames (`qsSee.ex-y0.ex-ext-2` and `qsSee.straighter.ex-y0.ex-ext-2`, 50 units apart) share a shape key, and their placements differ by the origins, so a placed piece written as (shape key, absolute x, absolute y) is equal exactly when the placed geometry is. The key is derived from content, not assigned in sequence, so the same outline gets the same key in every process: ink-signature digests are grouped across the parent's spawn pool, and a per-process counter would give two workers different digests for the same picture. Marshal version 2 has no back references by object identity, so equal nested tuples key equally even when one shares an object the other rebuilds."""
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
    """One Shaper and one OutlineCache per font, the two caches sharing one `OutlineIntern` so pieces compare across fonts as (shape key, absolute x, absolute y) without building translated geometry. The surface build passes `shaper_factory=shaper_for` so its components share one memoized Shaper per font. The default is a plain private Shaper, because a memo only costs memory for a caller that does not shape the same text twice."""

    def __init__(
        self, before_font: Path | str, after_font: Path | str, shaper_factory: Callable = Shaper
    ) -> None:
        self.intern = OutlineIntern()
        self._sides: dict[str, tuple[Shaper, OutlineCache]] = {}
        for side, path in (("before", before_font), ("after", after_font)):
            self._sides[side] = (shaper_factory(path), OutlineCache(path, self.intern))

    def ink_pieces(self, side: str, text: str, features: dict[str, bool]) -> tuple:
        """Return the sorted placed pieces of one shaped run: one (shape key, absolute x, absolute y) per glyph with ink, which is `run_ink` without the run order and the own-frame origin. Inkless glyphs (space, ZWNJ, empty markers) contribute nothing. Two pieces are equal exactly when the translated outlines would be, across fonts and processes, so the outlines are never built on this path. `ink_identical` compares this reading, and `JuniorOracle` compares it with Junior's tracking-free placement. Shaping is kern-neutral."""
        return tuple(sorted(piece[:3] for piece in self.run_ink(side, text, features)))

    def pieces_identical(self, text: str, config: str) -> bool:
        """Return whether both fonts' `ink_pieces` are equal under one config, so the same ink sits in the same places and only glyph names differ. `ink_identical` is this under every config, and `config_diff` makes the same comparison first and returns IDENTITY_DIFF when it holds."""
        features = features_for(config)
        return self.ink_pieces("before", text, features) == self.ink_pieces("after", text, features)

    def ink_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        """Return whether `pieces_identical` holds under every config in the set. `ink_pieces` is a projection of the two run-order lists that make up `signature`, so two windows with equal signatures always get the same answer here, and no sample is needed to check that the fold and this flag agree."""
        return all(self.pieces_identical(text, config) for config in configs)

    def signature(self, text: str, config: str) -> tuple:
        """Return the rendered-outcome identity of one text under one config: the pair of `run_ink` lists in run order, each entry (shape key, absolute x, absolute y, own-frame origin x). Equal signatures mean both fonts put the same ink in the same places, so the rows present the same visual question whatever their glyph names. `config_diff` reads only these two lists, so equal signatures give an equal delta, an equal `ink_identical` result, and an equal `delta_digest`. That makes the ink-duplicate fold sound without sampling: the fold groups by this value, and everything downstream that could tell two folded siblings apart is a function of it. That the signature ignores glyph names follows from `run_ink` dropping them, and rebuild/test_review_ink.py checks it on a font that gives two names one outline."""
        features = features_for(config)
        return (
            tuple(self.run_ink("before", text, features)),
            tuple(self.run_ink("after", text, features)),
        )

    def junior_pieces(self, text: str, tracking: int) -> tuple:
        """Return the before side's placed ink with a uniform letter tracking removed: like `ink_pieces` with no features, but each glyph whose name starts with `qs` advances the pen by its advance minus `tracking`, so the pieces land where a tracking-free rendering puts them. Meaningful only when the before side is the Junior font; see `JuniorOracle`."""
        shaper, outlines = self._sides["before"]
        result = shaper.shape(text, kern_neutral({}))
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            key, origin_x, origin_y = outlines.shape_key(name)
            if key:
                pieces.append((key, pen_x + x_offset + origin_x, y_offset + origin_y))
            pen_x += x_advance - (tracking if name.startswith("qs") else 0)
        pieces.sort()
        return tuple(pieces)

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
        """Return the pixel picture one font paints for the whole window, the union of every placed piece's cells, or None when any piece is not a grid-rectilinear picture or sits off the grid. `_span_cells` in rebuild/tools/standing_verdicts.py reads one span the same way."""
        pictures = self._run_pictures(self.run_ink(side, text, features))
        return None if pictures is None else set().union(*pictures)

    def picture_equal(self, text: str, config: str) -> bool:
        """The reference picture reading of one config: the placed pieces are equal, or both fonts' whole-run cell unions are. A property test in rebuild/test_review_ink.py checks that it agrees with `config_diff`'s sentinel. A window with no picture reads False unless its pieces are equal, so this reading fails in the same cases as the sentinel."""
        features = features_for(config)
        if self.ink_pieces("before", text, features) == self.ink_pieces("after", text, features):
            return True
        before = self.run_cells("before", text, features)
        return before is not None and before == self.run_cells("after", text, features)

    def picture_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        """Return whether `config_diff` returns IDENTITY_DIFF under every config in the set: both fonts paint the same pixels and nothing slid. `ink_identical` implies it, so the build asks it only of units that are not ink-identical. A window with no cell reading returns False, which leaves it to a human."""
        return all(self.config_diff(text, config) == IDENTITY_DIFF for config in configs)

    def config_diff(self, text: str, config: str) -> tuple:
        """Return the before-to-after ink delta under one config at the picture grain, localized to the changed region: (cells only the before font paints, cells only the after font paints, follower shift in columns).

        Each run is rasterized onto the PIXEL_SIZE grid and unioned per font over the whole window. A pixel one glyph gives up but a neighbor still paints is no change. For example, ·J'ai drops its crown pixel under an ·At that paints that pixel anyway, and the change adds nothing to any window's delta. The same change at a seam the neighbor no longer reaches leaves a real hole and stays in the delta, because the comparison is over the rendered union of this window and not over a list of allowed names.

        Followers that only slid are read at the same grain. The longest tail of glyphs whose after picture is the before picture displaced by a whole number of columns is moved back by that displacement before the subtraction, and the displacement is the shift. A tail that includes a pixel given up to a neighbor therefore still counts as slid, which keeps ·Fee·Tea·At·J'ai in the same group as every other window that shortens ·Fee the same way. The remaining cells are translated together so the delta's leftmost column is 0.

        IDENTITY_DIFF (nothing lost, nothing gained, no shift) is the sentinel that `picture_identical`, the surface build's per-unit flag, and the standing approvals' empty-delta digest all read. A window whose pieces are already equal returns it without rasterizing. A window with no picture (a curved or off-grid outline, or an off-grid placement) falls back to the piece grain (`_piece_diff`), which never returns the sentinel for pieces that differ. Two units whose judged pair, class, config set, and per-config deltas all agree show the same pixels appearing and disappearing, whatever unchanged letters surround the change; that is the echo-group key.
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
        """Return the piece-grain delta for a window with no picture. The two runs are aligned from both ends: the common prefix (same ink at the same position) and the common suffix (same ink shifted by one uniform dx) are stripped, the remaining middles are multiset-subtracted, and the result is translated so the delta's leftmost point is at x=0. Returns (outlines only the before font draws, outlines only the after font draws, suffix shift in font units). It returns IDENTITY_DIFF only when the middles cancel with no shift, which does not happen for pieces that differ."""
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
    """`config_diff`'s picture-grain arithmetic over two runs' per-piece pictures: find the longest tail of pieces whose after union is the before union displaced by a whole number of columns, move that tail back by the displacement, and subtract the whole-window unions both ways. A tail with no ink yet on either side is skipped, and a tail with ink on only one side matches nothing."""
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


class JuniorOracle:
    """The third machine-approval channel, after ink identity and picture identity. A unit divergent only under ss10 is approvable when the rebuild's ss10 rendering places the same ink the shipped Junior font places for the same string, once Junior's letter tracking is removed. Junior has Senior's isolated letterforms plus one pixel of extra advance on every Quikscript glyph. The constructor checks the advance part of that premise against the shipped Senior, derives the tracking from it, and raises ValueError when it does not hold. A pass means the rebuild draws every letter fully isolated, which is the ratified meaning of ss10 (see the ss10 ledger entries in rebuild/m1-divergences.yaml), so approval does not depend on what the old font did."""

    def __init__(
        self,
        junior_font: Path | str,
        before_font: Path | str,
        after_font: Path | str,
        shaper_factory: Callable = Shaper,
    ) -> None:
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
        self._comparator = InkComparator(junior_font, after_font, shaper_factory)

    def approves(self, configs, text: str) -> bool:
        if tuple(configs) != ("ss10",):
            return False
        junior = self._comparator.junior_pieces(text, self.tracking)
        return junior == self._comparator.ink_pieces("after", text, features_for("ss10"))
