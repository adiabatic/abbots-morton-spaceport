"""Tests for the glyph outlines `engine/pipeline/font_assembly.py` draws.

- `union_contours` traces the union of a glyph's pixel rectangles: abutting and overlapping rectangles merge into one contour, outer contours run counter-clockwise and holes clockwise, diagonal pixels keep a contour each, and no contour holds a point where it does not turn.
- In every glyph of all six fonts, the outline's nonzero winding number is 0 or 1 everywhere, so no two contours overlap and no contour runs clockwise around ink, no two contours share an edge, and every point is a corner.
"""

from pathlib import Path

import pytest
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

from engine.pipeline.font_assembly import union_contours

ROOT = Path(__file__).resolve().parent.parent

SITE_DIR = ROOT / "site"
FONTS = [
    f"{family}-{style}.otf"
    for family in (
        "AbbotsMortonSpaceportMono",
        "AbbotsMortonSpaceportSansJunior",
        "AbbotsMortonSpaceportSansSenior",
    )
    for style in ("Regular", "Bold")
]

Contour = list[tuple[int, int]]


def _signed_area(contour: Contour) -> int:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(contour, contour[1:] + contour[:1]))


def _edges(contour: Contour):
    return zip(contour, contour[1:] + contour[:1])


def _windings(contours: list[Contour]) -> set[int]:
    """Return every nonzero winding number the contours give a region between their edges, read along horizontal lines halfway between consecutive edge heights."""
    verticals = [
        (x1, min(y1, y2), max(y1, y2), 1 if y2 < y1 else -1)
        for contour in contours
        for (x1, y1), (x2, y2) in _edges(contour)
        if x1 == x2 and y1 != y2
    ]
    heights = sorted({y for _x, low, high, _d in verticals for y in (low, high)})
    found: set[int] = set()
    for low, high in zip(heights, heights[1:]):
        middle = (low + high) / 2
        winding = 0
        for x, _low, _high, direction in sorted(v for v in verticals if v[1] < middle < v[2]):
            winding += direction
            found.add(winding)
    found.discard(0)
    return found


def _problems(contours: list[Contour]) -> list[str]:
    problems = []
    windings = _windings(contours)
    if windings != {1} and windings:
        problems.append(f"winding numbers {sorted(windings)}")
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for contour in contours:
        for (x1, y1), (x2, y2) in _edges(contour):
            if x1 != x2 and y1 != y2:
                problems.append(f"diagonal edge {(x1, y1)}–{(x2, y2)}")
            # Split each edge into 25-unit steps, the finest grid Bold draws on, so edges that overlap only in part are caught too.
            step = 25
            if y1 == y2:
                for x in range(min(x1, x2), max(x1, x2), step):
                    segment = ((x, y1), (x + step, y1))
                    if segment in seen:
                        problems.append(f"shared edge {segment}")
                    seen.add(segment)
            else:
                for y in range(min(y1, y2), max(y1, y2), step):
                    segment = ((x1, y), (x1, y + step))
                    if segment in seen:
                        problems.append(f"shared edge {segment}")
                    seen.add(segment)
        for previous, point, following in zip(
            contour[-1:] + contour[:-1], contour, contour[1:] + contour[:1]
        ):
            if previous[0] == point[0] == following[0] or previous[1] == point[1] == following[1]:
                problems.append(f"point {point} is not a corner")
    return problems


class TestUnionContours:
    def test_single_square_is_counter_clockwise(self):
        assert union_contours([(0, 0, 50, 50)]) == [[(0, 0), (50, 0), (50, 50), (0, 50)]]

    def test_abutting_squares_merge(self):
        assert union_contours([(0, 0, 50, 50), (50, 0, 50, 50), (0, 50, 50, 50)]) == [
            [(0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100)]
        ]

    def test_overlapping_bold_squares_merge(self):
        assert union_contours([(0, 0, 75, 50), (50, 0, 75, 50)]) == [[(0, 0), (125, 0), (125, 50), (0, 50)]]

    def test_ring_has_a_clockwise_hole(self):
        ring = [(x * 50, y * 50, 50, 50) for x in range(3) for y in range(3) if (x, y) != (1, 1)]
        outer, hole = union_contours(ring)
        assert outer == [(0, 0), (150, 0), (150, 150), (0, 150)]
        assert hole == [(50, 50), (50, 100), (100, 100), (100, 50)]
        assert _signed_area(outer) > 0 > _signed_area(hole)

    def test_diagonal_pixels_keep_a_contour_each(self):
        assert union_contours([(0, 0, 50, 50), (50, 50, 50, 50)]) == [
            [(0, 0), (50, 0), (50, 50), (0, 50)],
            [(50, 50), (100, 50), (100, 100), (50, 100)],
        ]

    def test_no_rectangles_draw_nothing(self):
        assert union_contours([]) == []


def _glyph_contours(font: TTFont, glyph_name: str) -> list[Contour]:
    glyph_set = font.getGlyphSet()
    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    contours: list[Contour] = []
    for operator, points in pen.value:
        if operator == "moveTo":
            contours.append([points[0]])
        elif operator == "lineTo":
            contours[-1].append(points[0])
        elif operator != "closePath":
            raise AssertionError(f"{glyph_name} draws a {operator}")
    return contours


@pytest.mark.parametrize("font_file", FONTS)
def test_every_glyph_outline_is_clean(font_file):
    font = TTFont(SITE_DIR / font_file)
    failures = {}
    for glyph_name in font.getGlyphOrder():
        problems = _problems(_glyph_contours(font, glyph_name))
        if problems:
            failures[glyph_name] = problems[:3]
    assert not failures, f"{len(failures)} glyphs, e.g. {dict(list(failures.items())[:5])}"
