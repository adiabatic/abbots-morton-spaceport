"""Ink-identity comparison for the review surface: a unit is ink_identical when both shipped fonts put exactly the same ink in the same places under every config in the unit's set — only glyph names (and inkless marker glyphs) differ, so no human judgment is meaningful. The ink is recorded the census reference's way: shape the unit's text with uharfbuzz via rebuild.validation.shaping.Shaper, record each glyph's outline with fontTools' DecomposingRecordingPen, and place it at the cumulative x_advance plus the glyph's x_offset/y_offset. Placement is compared, not built: each glyph's outline is interned once to a shape key and its own-frame origin (see `OutlineIntern`), and a placed piece travels as (shape key, absolute x, absolute y), which compares equal exactly when the translated geometry would — two glyphs drawing identical strokes from different origins included. The translated point tuples are then materialized only for the pieces that survive into a returned delta, which for most windows is none of them. `ink_identical` is the piece-grain boolean and the census reference itself: the sorted placed pieces (`ink_pieces`) compare equal across fonts under every config. `config_diff` is the picture-grain delta every dedup channel above it keys on — the cells only one font paints, read over each font's whole rasterized window rather than piece by piece — so a change that paints no different pixel (an overlap removed at a seam, a stroke handed to a neighbor) sits outside the grain, and a window it rides in shares its digest with its tuck-free siblings. Its identity sentinel (IDENTITY_DIFF — no cell lost or gained and no follower shift) is the one implementation of `picture_identical`, the machine channel the build asks of every unit the piece-grain reading refused: piece identity implies the sentinel by construction, since `config_diff` answers it before rasterizing anything, and the sentinel is equivalent to the reference picture comparison — each font's whole-run cell union equal (`picture_equal`, over `run_cells`) — which `rebuild/test_review_ink.py` holds over the frozen windows. The dedupe channel's `signature` is defined over the same two `run_ink` lists `config_diff` and `ink_pieces` consume, so equal signatures give equal deltas, equal ink verdicts and equal delta digests by definition rather than by agreement, with no corpus sample needed to hold the formulations together. All review-surface shaping is kern-neutral (`kern_neutral`): the rebuild has no kerning until its own later milestone, so the old font's kern feature is pure noise in before/after comparisons and is disabled on both sides."""

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

# The M1 mini-font carries epoch-zero head timestamps; fontTools logs a "'created' timestamp seems very low" warning for each, which is noise in the build output.
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
    """The hb feature dict for a config token: empty for default, one True entry per `+`-joined stylistic-set tag otherwise (matching rebuild.validation.rowmodel.CONFIGS for every acceptance config, and generalizing to table-diff configs)."""
    if not config or config == "default":
        return {}
    return {tag: True for tag in config.split("+")}


def kern_neutral(features: dict[str, bool] | None) -> dict[str, bool]:
    """The review surface's kern-off shaping features: the config's stylistic-set features plus an unconditional `kern: False`, for both fonts. A no-op on the after font (it carries no kern feature yet), but explicit so the rule survives the later kerning milestone, where kern differences get their own review."""
    return {**(features or {}), "kern": False}


IDENTITY_DIFF = ((), (), 0)


def delta_digest(diff: tuple) -> str:
    """The persisted identity of one `config_diff` result: `d-` plus the first twelve hex digits of the sha1 of the tuple's repr. The surface stores one digest per config whose delta is nonempty (the unit JSON's `ink_deltas`), so a standing-approval rule can bless a localized ink change once and match every window — in any batch, past or future — where exactly those pixels and nothing else appear and disappear. Like the cluster id's repr recipe, this is a byte-identity contract over the delta's shape as well as the recipe: changing either orphans every digest recorded in rebuild/standing-approvals.yaml, which are then re-derived from the standing probe's family line rather than hand-edited."""
    return "d-" + hashlib.sha1(repr(diff).encode()).hexdigest()[:12]


def signature_digest(signature: tuple) -> str:
    """The persisted identity of one `InkComparator.signature` result: the sha256 of its marshal-v2 bytes. Version 2 deliberately predates marshal's identity-based back references, so equal nested tuples digest equally even when one reuses an object the other reconstructs. Digest equality is signature equality for the ink-duplicate merge's purposes — the merge only ever groups by the value, never reads inside it — which is what lets the surface build serve signatures from the persisted store (rebuild/review/unit_cache.py) instead of re-shaping every relabel-split window on every pass. Unlike `delta_digest` this is not a byte contract with anything checked in: it is only ever compared for equality within one build and persisted in a store that self-invalidates on any stamp mismatch, so the day the pieces changed from translated geometry to (outline key, x, y) triples cost exactly one store miss."""
    return hashlib.sha256(marshal.dumps(signature, 2)).hexdigest()


class ShapeMemoCensus(NamedTuple):
    """What a shape memo holds: its entry count, and an approximate byte figure for the keys and results behind them — approximate because it is `sys.getsizeof` summed over the containers and the ints they hold, so it neither follows the glyph names (which are the font's own glyph-order strings, shared by every entry that shapes the same glyph) nor discounts the small ints CPython interns. Cheap enough to take at a batch boundary; it is the instrument issue #150's measurement reads beside a worker's peak, never something the build consults."""

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
    """A Shaper whose `shape` memoizes by (text, features): the surface build shapes the same (text, config) for `config_diff`, again in `Enricher.enrich`, again in the JuniorOracle, and once more in the Drafter's semantics replay, and the memo collapses the four to one HarfBuzz call, since the fragment is drafted in the same batch that enriched it. Bounded by the unit batch rather than by the build: the surface build releases every registered memo behind each unit batch (`release_shape_memos`, which rebuild/review/build.py's `_phase1_batches` and `_released_batches` call in the pool worker and the in-process runner alike, and which the parent's serial signature pass calls once it is done), so what a memo holds at any moment is one batch's windows. Only the surface build opts in, via `shaper_for`. The bound is load-bearing rather than tidy, measured on one tree one pass apart (issue #150): a serial build with the release read a 15.4 GB `surface-build` step peak and finished its units phase in about twenty minutes, while the same build with `release` made a no-op was holding 6.9 million shapes at roughly 10.5 GB by `census` when it was barely past half the corpus, had already pushed the 32 GiB box most of the way through its swap at twice the elapsed time, and was stopped there rather than finished. The memo grows linearly with the units, at about 1.5 KB a shape, so the premise an unbounded memo would rest on — that the retained EnrichedUnits dominate a process's memory anyway — is false: left alone, the memo outgrows every other pile a serial build holds."""

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
        """Forget every shape held, so the next `shape` of any text pays HarfBuzz again. The whole of the bound is this one statement: an A/B of the build with and without it replaces the `clear()` line with `pass` and touches nothing else, since every path reaches the release through here."""
        self._memo.clear()

    def census(self) -> ShapeMemoCensus:
        return ShapeMemoCensus(
            len(self._memo), sum(_approx_entry_bytes(key, result) for key, result in self._memo.items())
        )


_shaper_registry: dict[tuple[str, int, int], _MemoizedShaper] = {}


def release_shape_memos() -> None:
    """Release every memo `shaper_for` has handed out in this process — the surface build's unit batch boundary, called behind each batch in every path the build has. The shapers themselves stay registered, so the font loads they collapsed are never repeated; only their shapes go."""
    for shaper in _shaper_registry.values():
        shaper.release()


def shape_memo_census() -> ShapeMemoCensus:
    """What every registered memo holds between them, summed: the figure a measurement reads at a batch boundary, or at a worker's stop with the release disabled, beside the process's peak RSS."""
    entries = approx_bytes = 0
    for shaper in _shaper_registry.values():
        census = shaper.census()
        entries += census.entries
        approx_bytes += census.approx_bytes
    return ShapeMemoCensus(entries, approx_bytes)


def shaper_for(font_path: Path | str) -> Shaper:
    """The surface build's shared, memoized Shaper for one font, keyed by (resolved path, mtime, size) so a font rewritten in place — a test building two surfaces over different mini fonts at one path — never serves stale shapes. Sharing one instance across the comparator, oracle, enricher, and drafter also collapses their four separate font loads to one per process."""
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
    """The PIXEL_SIZE-grid cells a rectilinear outline fills under nonzero winding — the pixel picture of one of this family's bitmap-compiled glyphs — or None when the outline steps off that contract (a curve, or any coordinate off the grid), which a caller reads as no picture claim being possible. Sampling the winding number at cell centers is exact here rather than approximate, because every edge lies on the grid and so no center ever sits on one."""
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
    """One decomposed outline split into a shape and an origin: the sha1 of the marshal-v2 bytes of the outline translated so its own leftmost, lowest point sits at (0, 0), plus that point. Splitting is the whole point — two glyphs drawn identically but positioned differently inside their own frames (`qsSee.ex-y0.ex-ext-2` and `qsSee.straighter.ex-y0.ex-ext-2` are the worked example, 50 units apart) share a shape key, and their placements differ by exactly the origins, so a placed piece written as (shape key, absolute x, absolute y) compares equal precisely when the placed geometry does. Content-derived rather than sequence-assigned so the same outline earns the same key in every process: ink-signature digests are grouped across the parent's spawn pool, and a per-process intern counter would hand two workers different digests for the same picture. Marshal version 2 predates the identity-based back references, so equal nested tuples key equally even when one reuses an object the other reconstructs."""
    points = [point for _operator, points in value for point in points if point is not None]
    if not points:
        return (hashlib.sha1(marshal.dumps(value, 2)).digest(), 0, 0)
    origin_x = min(point[0] for point in points)
    origin_y = min(point[1] for point in points)
    canonical = translate_outline(value, -origin_x, -origin_y)
    return (hashlib.sha1(marshal.dumps(canonical, 2)).digest(), origin_x, origin_y)


class OutlineIntern:
    """The outline table the fonts of one comparison share: `place` interns an outline to its shape key, remembering the origin-normalized value so a surviving delta piece can be materialized back, and `value` reads that back. Interning by shape rather than by glyph name is what makes a key comparable across fonts, and it is what lets `config_diff` do all of its alignment, multiset subtraction, and normalization over small tuples of ints and bytes, building translated geometry only for the pieces that survive into the returned delta — which for most windows is none of them — and what lets the enricher's `_segment_pieces` decide whether a divergent position is visible in ink over the same tuples without building any geometry at all."""

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
        """Whether the shape has any point at all. An outline of bare path operators contributes no x to the delta's normalization, exactly as the point-walking form it replaces contributed none."""
        return self._drawn[key]

    def cells(self, key: bytes) -> frozenset[tuple[int, int]] | None:
        """The shape's filled grid cells in its own canonical frame (leftmost, lowest point at the origin), rasterized once per shape and shared across every placement and both fonts; None when the shape is not a grid-rectilinear picture."""
        if key not in self._cells:
            self._cells[key] = rectilinear_cells(self._values[key])
        return self._cells[key]


class OutlineCache:
    """One font's decomposed glyph outlines, recorded lazily and cached by glyph name; `placed` translates an outline to a pen position, returning () for an inkless glyph so callers can skip markers uniformly, and `shape_key` answers the same question in the shared intern's key space, which is where the comparator does its work."""

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
    """Holds one Shaper and one OutlineCache per font, both caches sharing one `OutlineIntern` so a piece can be compared across fonts as (shape key, absolute x, absolute y) without ever building the translated geometry; `ink_identical` is a deterministic boolean over (text, configs). The surface build passes `shaper_factory=shaper_for` so its components share one memoized Shaper per font; the default keeps a plain private Shaper, because a memo is pure memory cost for callers like carry_verdicts that never shape the same text twice."""

    def __init__(
        self, before_font: Path | str, after_font: Path | str, shaper_factory: Callable = Shaper
    ) -> None:
        self.intern = OutlineIntern()
        self._sides: dict[str, tuple[Shaper, OutlineCache]] = {}
        for side, path in (("before", before_font), ("after", after_font)):
            self._sides[side] = (shaper_factory(path), OutlineCache(path, self.intern))

    def ink_pieces(self, side: str, text: str, features: dict[str, bool]) -> tuple:
        """The placed outlines of one shaped run, sorted: one (shape key, absolute x, absolute y) piece per glyph that carries ink, at its pen position — `run_ink` with the run order and the own-frame origin dropped. Inkless glyphs (space, ZWNJ, empty markers) contribute no piece. The triple stands in for the geometry — two pieces compare equal exactly when the translated outlines would, across fonts and across processes alike — which is what keeps the translated outlines from being built at all on this path. This is the census reference reading `ink_identical` compares, and what the Junior oracle holds against Junior's tracking-free placement. Shaping is always kern-neutral."""
        return tuple(sorted(piece[:3] for piece in self.run_ink(side, text, features)))

    def pieces_identical(self, text: str, config: str) -> bool:
        """The piece-grain reading of one config: both fonts' `ink_pieces` compare equal, so the same ink sits in the same places and only glyph names differ. `ink_identical` is this under every config, and `config_diff` answers IDENTITY_DIFF from it before rasterizing anything."""
        features = features_for(config)
        return self.ink_pieces("before", text, features) == self.ink_pieces("after", text, features)

    def ink_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        """The ink-identity boolean: True exactly when `pieces_identical` holds under every config in the set. It is a function of `signature` alone, since `ink_pieces` is a projection of the two run-order lists the signature is: two windows with one signature cannot disagree here, so no sample is needed to hold the fold predicate and this one together."""
        return all(self.pieces_identical(text, config) for config in configs)

    def signature(self, text: str, config: str) -> tuple:
        """The rendered-outcome identity of one text under one config: the pair of `run_ink` lists, in run order, each entry an (outline key, absolute x, absolute y, own-frame origin x). Two rows whose signatures are equal put exactly the same ink in the same places in both fonts, so they present the same visual question no matter how their glyph names differ — and this is the definitional half of that claim rather than a resemblance to it. `config_diff` reads exactly these two lists and nothing else, so equal signatures give it equal arguments, hence an equal delta, hence an equal `ink_identical` verdict and an equal `delta_digest`. That is what makes the ink-duplicate fold sound with nothing left to sample: a fold groups by this value, and everything downstream that could tell two folded siblings apart is a function of it. The one thing the identity cannot state about itself is that it ignores glyph names, which `run_ink` supplies by dropping them and rebuild/test_review_ink.py witnesses on a font giving two names one outline."""
        features = features_for(config)
        return (
            tuple(self.run_ink("before", text, features)),
            tuple(self.run_ink("after", text, features)),
        )

    def junior_pieces(self, text: str, tracking: int) -> tuple:
        """The before side's placed ink with a uniform letter tracking removed: like ink_pieces with no features, but each Quikscript glyph advances the pen by its advance minus `tracking`, so the pieces land where a tracking-free rendering would put them. Only meaningful when the before side is the Junior font; see JuniorOracle."""
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
        """One shaped run with its glyph names still attached: the full shaped name tuple (inkless markers included, so a caller can hold the run against a recorded glyph list), plus one (glyph name, shape key, absolute x, absolute y, own-frame origin x) entry per glyph that carries ink. The nameless projection of the pieces is `run_ink`, which is what the delta alignment consumes — names never enter a piece comparison, because the two fonts spell the same ink under different names by design. Shaping is always kern-neutral."""
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
        """The placed ink of one shaped run in run order: one (shape key, absolute x, absolute y, own-frame origin x) entry per glyph that carries ink, so config_diff can align the two fonts' runs glyph-by-glyph. The first three place the ink and are all the multiset subtraction reads; the fourth is what distinguishes two glyphs that draw the same strokes from different origins, which the prefix and suffix strips — alignment questions about the run, not about the page — still need to tell apart. Shaping is always kern-neutral."""
        _names, pieces = self.named_run(side, text, features)
        return [piece[1:] for piece in pieces]

    def _piece_cells(self, piece) -> frozenset[tuple[int, int]] | None:
        """One placed piece's pixel picture: its shape's cells translated to its placement, empty for a shape with no points, or None when the shape is not a grid-rectilinear picture or the placement is off the grid."""
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
        """Every piece of one shaped run as its pixel picture, in run order, or None when any piece has none — which a caller reads as no picture claim being possible for the window."""
        pictures = []
        for piece in run:
            cells = self._piece_cells(piece)
            if cells is None:
                return None
            pictures.append(cells)
        return pictures

    def run_cells(self, side: str, text: str, features: dict[str, bool]) -> set[tuple[int, int]] | None:
        """The whole-window pixel picture one font paints: the union of every placed piece's rasterized cells translated to its placement, or None when any piece is not a grid-rectilinear picture or sits off the grid — the same reading the standing approvals' slide spans take, over the run entire."""
        pictures = self._run_pictures(self.run_ink(side, text, features))
        return None if pictures is None else set().union(*pictures)

    def picture_equal(self, text: str, config: str) -> bool:
        """The reference picture reading of one config, which the property test in rebuild/test_review_ink.py holds equivalent to `config_diff`'s sentinel: the placed pieces compare equal, or both fonts' whole-run cell unions do. A window no picture can be made of reads False unless its pieces are equal, so the reading fails closed exactly where the sentinel does."""
        features = features_for(config)
        if self.ink_pieces("before", text, features) == self.ink_pieces("after", text, features):
            return True
        before = self.run_cells("before", text, features)
        return before is not None and before == self.run_cells("after", text, features)

    def picture_identical(self, text: str, configs: tuple[str, ...]) -> bool:
        """The picture-identity boolean, read from `config_diff`'s sentinel: both fonts paint the same pixels under every config in the set, with nothing slid. Implied by `ink_identical` by construction, so the build asks it only of units the piece-grain reading refused; it fails closed on any window no cell reading can be made of, which leaves that window to a human."""
        return all(self.config_diff(text, config) == IDENTITY_DIFF for config in configs)

    def config_diff(self, text: str, config: str) -> tuple:
        """The before→after ink delta under one config at the picture grain, localized to the changed region: (cells only the before font paints, cells only the after font paints, follower shift in columns). Each run is rasterized onto the PIXEL_SIZE grid and unioned per font over the whole window, so a pixel one glyph gives up that its neighbor still paints is no change at all — the crown pixel ·J'ai drops under an ·At that paints it anyway rides in any window without minting a digest of its own — while the same re-spelling at a seam the neighbor no longer reaches paints a real hole and stays in the delta, because the grain is the rendered union of this window and never a name-level allowlist. Followers that merely slid over are read at the same grain: the longest tail of glyphs whose after picture is the before picture rigidly displaced by a whole number of columns is pulled back by that displacement before the subtraction, and the displacement is the shift — so a tail carrying such a tuck still counts as slid, which is what keeps ·Fee·Tea·At·J'ai in the family of every other window shortening ·Fee the same way. The surviving cells are jointly translated so the delta's leftmost column sits at 0. IDENTITY_DIFF — nothing lost, nothing gained, no shift — is the one sentinel `picture_identical`, the surface build's per-unit flag, and the standing approvals' empty-delta digest all read, and a window whose pieces already compare equal returns it without rasterizing anything. A window no picture can be made of — a curved or off-grid outline, an off-grid placement — falls back to the piece grain (`_piece_diff`), which never answers the sentinel for pieces that differ. Two units whose judged pair, class, config set, and per-config deltas all agree show the same pixels appearing and disappearing — the echo-group key — no matter which unchanged letters surround the change."""
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
        """The piece-grain delta, for a window no picture can be made of: the two runs aligned glyph-by-glyph from both ends, stripping the common prefix (same ink at the same position) and the common suffix (same ink rigidly shifted by one uniform dx), the remaining middles multiset-subtracted and jointly translated so the delta's leftmost point sits at x=0. Returns (outlines only the before font draws, outlines only the after font draws, suffix shift in font units), and IDENTITY_DIFF only when the middles cancel with no shift — which for pieces that differ they never do."""
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
        # Every shape sits at x = 0 in its own frame, so a piece's leftmost point is its absolute x and the delta's leftmost point is the smallest of them; no geometry needs building to find it.
        xs = [x for key, x, _y in before_only + after_only if intern.draws(key)]
        if not xs:
            return ((), (), shift)
        x0 = min(xs)

        def normalize(pieces):
            return tuple(sorted(translate_outline(intern.value(key), x - x0, y) for key, x, y in pieces))

        return (normalize(before_only), normalize(after_only), shift)


def _picture_diff(before: list[frozenset[tuple[int, int]]], after: list[frozenset[tuple[int, int]]]) -> tuple:
    """`config_diff`'s picture-grain arithmetic over two runs' per-piece pictures: find the longest tail of pieces whose after union is the before union displaced by a whole number of columns, pull that tail back by the displacement, and subtract the whole-window unions both ways. A tail that has no ink yet on either side makes no claim, and a tail with ink on only one side matches nothing."""
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
    """The second machine-approval channel, alongside ink identity: a unit divergent only under ss10 is approvable when the rebuild's ss10 rendering places exactly the ink the shipped Junior font places for the same string, once Junior's letter tracking is removed. Junior carries the same isolated letterforms as Senior plus one pixel of extra advance on every Quikscript glyph; the constructor verifies that premise against the shipped Senior and derives the tracking from it, refusing to run if the fonts ever drift from it. A pass means the rebuild draws every letter fully isolated — the ratified meaning of ss10 (see the ss10 ledger entries in rebuild/m1-divergences.yaml) — so approval is mechanical regardless of what the old font did."""

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
