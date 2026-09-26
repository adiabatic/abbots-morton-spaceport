"""Tests for the review surface's ink comparison.

`ink_identical` (the sorted placed pieces are equal) reproduces the worked readings: the ◊ZWNJ ·May·Oy·Pea window is ink-identical only because kerning is off, ␣·Pea·Pea is ink-identical outright, a real one-pixel change is not picture-identical, and two comparators give the same result. `config_diff` (the cells only one font paints over the whole window, with the shifted tail pulled back) ignores a tuck that leaves the union of painted cells unchanged. When one glyph stops painting a pixel that a neighbor still paints, the delta is the empty sentinel if nothing else changed. Beside a real change, the tuck leaves that change's digest as it is, so the window shares a key with its tuck-free siblings. The same change at a seam the neighbor no longer reaches stays in the delta as the hole it leaves.

Nothing here reads the live corpus. The windows come from the frozen mini bundle's audit and are shaped in that bundle's font, because every claim here is about the comparator. The ink-duplicate fold needs no sampling: `signature` returns the same two `run_ink` lists that `config_diff` and `ink_pieces` read, so equal signatures give equal deltas and equal ink flags. That the signature ignores glyph names is checked on the marker font, which gives two names one outline. A stride of the frozen windows checks that the sentinel matches the reference picture reading, so the build's picture flag, read from the same diffs it digests, always agrees with `picture_equal`.

Also here: `delta_digest`, the stored identity of one config's localized delta. `check_unit` checks its shape, and its recipe must stay byte-identical because rebuild/standing-approvals.yaml records digests made with it.

And, on inputs this file builds itself, the two pixel-grain readings the standing approvals' slide shape uses: `rectilinear_cells`, which rasterizes one grid-rectilinear outline under nonzero winding (a hole stays empty, two overlapping same-direction contours fill their union, and a curve or an off-grid coordinate returns None), and `named_run`, the shaped run with its glyph names attached, which keeps the inkless markers its pieces drop and whose projection without names is `run_ink`.
"""

import hashlib
import marshal
import shutil
from pathlib import Path

import pytest

from rebuild.review.ink import (
    IDENTITY_DIFF,
    InkComparator,
    JuniorOracle,
    delta_digest,
    features_for,
    kern_neutral,
    rectilinear_cells,
    release_shape_memos,
    shape_memo_census,
    shaper_for,
    signature_digest,
)
from rebuild.validation.shaping import Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_FONT = MINI / "M1.otf"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
JUNIOR_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf"


@pytest.fixture(scope="module")
def comparator():
    return InkComparator(BEFORE_FONT, MINI_FONT)


@pytest.fixture(scope="module")
def mini_units(mini_bundle):
    """Return the frozen bundle's whole workload as unit records: every window over the `LETTERS` and `BOUNDARIES` of rebuild/review/fixtures/mini/regenerate.py, plus its `EXAMPLE_WINDOWS`."""
    from rebuild.review.audit import load_workload
    from rebuild.review.enrich import LETTERS

    return load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(LETTERS)).units()


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def test_features_for_config_tokens():
    assert features_for("default") == {}
    assert features_for(None) == {}
    assert features_for("ss03") == {"ss03": True}
    assert features_for("ss02+ss03+ss05") == {"ss02": True, "ss03": True, "ss05": True}


def test_kern_neutral_always_disables_kern():
    assert kern_neutral(None) == {"kern": False}
    assert kern_neutral({}) == {"kern": False}
    assert kern_neutral({"ss03": True}) == {"ss03": True, "kern": False}
    assert kern_neutral({"kern": True}) == {"kern": False}


def _closed(*contours):
    value = []
    for contour in contours:
        value.append(("moveTo", (contour[0],)))
        value.extend(("lineTo", (point,)) for point in contour[1:])
        value.append(("closePath", ()))
    return tuple(value)


def test_rectilinear_cells_fills_a_rectangle():
    """A two-column, three-row block returns its six cells, indexed from the outline's own leftmost, lowest point."""
    outline = _closed(((0, 0), (100, 0), (100, 150), (0, 150)))
    assert rectilinear_cells(outline) == frozenset({(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)})


def test_rectilinear_cells_follows_an_l_shape():
    """A single concave contour, as a stroke turning a corner compiles to: the cells follow the outline, not its bounding box."""
    outline = _closed(((0, 0), (100, 0), (100, 50), (50, 50), (50, 150), (0, 150)))
    assert rectilinear_cells(outline) == frozenset({(0, 0), (1, 0), (0, 1), (0, 2)})


def test_rectilinear_cells_leaves_a_donuts_hole_empty():
    """Two contours wound in opposite directions, as a closed loop's counter is drawn: the winding number cancels inside the hole, so the cells form the ring."""
    outer = ((0, 0), (200, 0), (200, 200), (0, 200))
    hole = ((50, 50), (50, 150), (150, 150), (150, 50))
    ring = {(column, row) for column in range(4) for row in range(4)}
    ring -= {(1, 1), (2, 1), (1, 2), (2, 2)}
    assert rectilinear_cells(_closed(outer, hole)) == frozenset(ring)


def test_rectilinear_cells_fills_the_union_of_two_overlapping_rectangles():
    """Two contours wound in the same direction: the overlap has winding number two and stays filled, so crossing strokes leave no hole, and the shared column is counted once."""
    left = ((0, 0), (100, 0), (100, 100), (0, 100))
    right = ((50, 0), (150, 0), (150, 100), (50, 100))
    assert rectilinear_cells(_closed(left, right)) == frozenset(
        {(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)}
    )


def test_rectilinear_cells_refuses_a_curve():
    """An outline with anything other than straight grid edges returns None, which the slide shape reads as "cannot judge this window"."""
    outline = (("moveTo", ((0, 0),)), ("qCurveTo", ((50, 100), (100, 0))), ("closePath", ()))
    assert rectilinear_cells(outline) is None


def test_rectilinear_cells_refuses_an_off_grid_coordinate():
    """A rectangle three and a half pixels tall is rectilinear but off the grid, so a cell center could lie on an edge. It returns None instead of rounding."""
    outline = _closed(((0, 0), (100, 0), (100, 175), (0, 175)))
    assert rectilinear_cells(outline) is None


def test_rectilinear_cells_refuses_an_unclosed_contour():
    """A contour that is never closed returns None instead of an empty picture. Both shipped pens close every contour, so an open one means the outline is not the kind this rasterizer handles."""
    outline = (("moveTo", ((0, 0),)), ("lineTo", ((100, 0),)), ("lineTo", ((100, 150),)))
    assert rectilinear_cells(outline) is None


QS_A_OUTLINE = (((0, 0), (100, 0), (100, 150), (0, 150)),)
MARKER_GLYPHS = {
    "qsA": (QS_A_OUTLINE, 100),
    "qsB": ((((50, 0), (150, 0), (150, 150), (50, 150)),), 200),
    # qsA's outline and advance under another name, which a signature must ignore.
    "qsC": (QS_A_OUTLINE, 100),
    "space": ((), 100),
}
MARKER_CMAP = {0xE001: "qsA", 0xE002: "qsB", 0xE003: "qsC", 0x20: "space"}


def _build_font(path, glyphs=MARKER_GLYPHS, cmap=MARKER_CMAP):
    """Build a small TTF from `glyphs` and `cmap`. The default marker set has two inked rectangles, one inset in its own frame so its origin_x is not zero, a copy of the first under another name, and an outline-less `space` that stands in for the surface's inkless markers. Each glyph's left sidebearing is set to its own leftmost point. This is required: fontTools' TrueType glyph set translates an outline by `lsb - xMin` when reading it, and would otherwise move the inset glyph back to x=0 and remove the origin under test."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", *glyphs]
    outlines = {}
    metrics = {}
    for name in order:
        contours, advance = glyphs.get(name, ((), 500))
        pen = TTGlyphPen(None)
        for contour in contours:
            pen.moveTo(contour[0])
            for point in contour[1:]:
                pen.lineTo(point)
            pen.closePath()
        outlines[name] = pen.glyph()
        columns = [x for contour in contours for x, _y in contour]
        metrics[name] = (advance, min(columns) if columns else 0)
    builder = FontBuilder(1000)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "MarkerTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 0  # pyright: ignore[reportAttributeAccessIssue]
    builder.font["head"].modified = 0
    builder.save(str(path))
    return path


MARKER_TEXT = " " + chr(0xE001) + chr(0xE002)


@pytest.fixture(scope="module")
def marker_comparator(tmp_path_factory):
    path = _build_font(tmp_path_factory.mktemp("marker-font") / "marker.ttf")
    return InkComparator(path, path)


def test_named_run_keeps_the_inkless_markers_the_pieces_drop(marker_comparator):
    """The name tuple is the whole shaped run, markers included, so a caller can compare it with a recorded glyph list. The pieces cover only glyphs with ink, and each carries its placed position and the own-frame origin that distinguishes two glyphs drawing the same strokes from different frames."""
    names, pieces = marker_comparator.named_run("before", MARKER_TEXT, {})
    assert names == ("space", "qsA", "qsB")
    assert [piece[0] for piece in pieces] == ["qsA", "qsB"]
    assert [piece[2:] for piece in pieces] == [(100, 0, 0), (250, 0, 50)]


def test_run_ink_is_the_nameless_projection_of_named_run(marker_comparator):
    """`run_ink` is `named_run`'s pieces with the names dropped, which keeps glyph names out of every piece comparison in the delta alignment."""
    _names, pieces = marker_comparator.named_run("before", MARKER_TEXT, {})
    assert marker_comparator.run_ink("before", MARKER_TEXT, {}) == [piece[1:] for piece in pieces]


def test_the_signature_is_blind_to_the_glyph_name(marker_comparator):
    """`signature` is `run_ink` on both sides, and `run_ink` drops the glyph name, so two glyphs drawing the same outline at the same advance under different names have the same signature. The ink-duplicate fold depends on this, because the windows it folds differ only in glyph names. The synthetic fold tests stub `ink_sig`, so this test covers the real function."""
    assert marker_comparator.signature(chr(0xE001), "default") == marker_comparator.signature(
        chr(0xE003), "default"
    )


def test_the_intern_rasterizes_a_shape_in_its_own_canonical_frame(marker_comparator):
    """A shape's cells are indexed from its own leftmost, lowest point, not from where it was placed, so one rasterization serves every placement of that shape in both fonts."""
    _names, pieces = marker_comparator.named_run("before", chr(0xE002), {})
    [(_name, key, _x, _y, origin_x)] = pieces
    assert origin_x == 50
    assert marker_comparator.intern.cells(key) == frozenset({(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)})


OVERLAP_CMAP = {0xE001: "qsA", 0xE002: "qsB"}
OVERLAP_TEXT = chr(0xE001) + chr(0xE002)
COLUMN = (((0, 0), (100, 0), (100, 150), (0, 150)),)
HALF_COLUMN = (((0, 0), (50, 0), (50, 150), (0, 150)),)
OVERLAP_BEFORE = {"qsA": (COLUMN, 50), "qsB": (COLUMN, 100)}
OVERLAP_AFTER = {"qsA": (COLUMN, 100), "qsB": (HALF_COLUMN, 100)}


@pytest.fixture(scope="module")
def overlap_comparator(tmp_path_factory):
    """A small version of the case picture identity exists for. Before, qsA's second column is drawn again by qsB's first. After, qsA keeps the column and qsB drops its overlapping half. Both fonts paint the same three columns."""
    root = tmp_path_factory.mktemp("overlap-fonts")
    before = _build_font(root / "before.ttf", OVERLAP_BEFORE, OVERLAP_CMAP)
    after = _build_font(root / "after.ttf", OVERLAP_AFTER, OVERLAP_CMAP)
    return InkComparator(before, after)


def test_picture_identity_sees_through_an_overlap_removal(overlap_comparator):
    """The piece-grain reading sees a changed qsB, but both fonts paint the same nine cells, so the delta is the empty sentinel and the picture channel approves. The column qsB gives up is one qsA still paints, and the delta is read over the window's union, not over the pieces that changed."""
    assert overlap_comparator.ink_identical(OVERLAP_TEXT, ("default",)) is False
    assert overlap_comparator.pieces_identical(OVERLAP_TEXT, "default") is False
    assert overlap_comparator.config_diff(OVERLAP_TEXT, "default") == IDENTITY_DIFF
    assert overlap_comparator.picture_identical(OVERLAP_TEXT, ("default",)) is True
    picture = {(column, row) for column in range(3) for row in range(3)}
    assert overlap_comparator.run_cells("before", OVERLAP_TEXT, {}) == picture
    assert overlap_comparator.run_cells("after", OVERLAP_TEXT, {}) == picture


def test_picture_identity_fails_closed_off_the_grid(tmp_path):
    """A placement or outline off the PIXEL_SIZE grid has no cell reading, so the picture channel does not approve it. An off-grid advance and an off-grid edge each leave the window to a human, and the delta falls back to the piece grain (translated outlines, with the shift in font units), which is never the sentinel for pieces that differ."""
    before = _build_font(tmp_path / "before.ttf", OVERLAP_BEFORE, OVERLAP_CMAP)
    slid = _build_font(tmp_path / "slid.ttf", {"qsA": (COLUMN, 75), "qsB": (HALF_COLUMN, 100)}, OVERLAP_CMAP)
    comparator = InkComparator(before, slid)
    assert comparator.run_cells("after", OVERLAP_TEXT, {}) is None
    assert comparator.picture_identical(OVERLAP_TEXT, ("default",)) is False
    diff = comparator.config_diff(OVERLAP_TEXT, "default")
    assert diff != IDENTITY_DIFF
    assert all(isinstance(operator, str) for piece in diff[0] + diff[1] for operator, _points in piece)
    ragged = (((0, 0), (25, 0), (25, 150), (0, 150)),)
    torn = _build_font(tmp_path / "torn.ttf", {"qsA": (COLUMN, 100), "qsB": (ragged, 100)}, OVERLAP_CMAP)
    comparator = InkComparator(before, torn)
    assert comparator.run_cells("after", OVERLAP_TEXT, {}) is None
    assert comparator.picture_identical(OVERLAP_TEXT, ("default",)) is False
    assert comparator.config_diff(OVERLAP_TEXT, "default") != IDENTITY_DIFF


THREE_COLUMNS = (((0, 0), (150, 0), (150, 150), (0, 150)),)
INSET_TWO_COLUMNS = (((50, 0), (150, 0), (150, 150), (50, 150)),)
TUCK_CMAP = {0xE001: "qsFee", 0xE002: "qsAt", 0xE003: "qsJai", 0xE004: "qsAt.plain"}
# ·At's third column is double-drawn by ·J'ai's first in the before font; after, ·At gives that column up and ·J'ai paints it alone. ·Fee shortens by a column in both windows, which is the real change, and the plain ·At keeps its old drawing on both sides.
TUCK_BEFORE = {
    "qsFee": (THREE_COLUMNS, 150),
    "qsAt": (THREE_COLUMNS, 100),
    "qsJai": (COLUMN, 100),
    "qsAt.plain": (THREE_COLUMNS, 100),
}
TUCK_AFTER = {**TUCK_BEFORE, "qsFee": (COLUMN, 100), "qsAt": (COLUMN, 100)}
TUCK_HOLE = {**TUCK_AFTER, "qsJai": (INSET_TWO_COLUMNS, 100)}
TUCK_ALONE = chr(0xE002) + chr(0xE003)
TUCKED_WINDOW = chr(0xE001) + chr(0xE002) + chr(0xE003)
PLAIN_WINDOW = chr(0xE001) + chr(0xE004) + chr(0xE003)
FEE_SHORTENED = (((0, 0), (0, 1), (0, 2)), (), -1)


@pytest.fixture(scope="module")
def tuck_comparator(tmp_path_factory):
    root = tmp_path_factory.mktemp("tuck-fonts")
    before = _build_font(root / "before.ttf", TUCK_BEFORE, TUCK_CMAP)
    after = _build_font(root / "after.ttf", TUCK_AFTER, TUCK_CMAP)
    return InkComparator(before, after)


def test_a_union_invisible_tuck_is_the_empty_sentinel_on_its_own(tuck_comparator):
    """·At·J'ai alone: the pieces differ, the picture does not, so the delta is the sentinel and the picture channel approves the window."""
    assert tuck_comparator.pieces_identical(TUCK_ALONE, "default") is False
    assert tuck_comparator.config_diff(TUCK_ALONE, "default") == IDENTITY_DIFF
    assert tuck_comparator.picture_identical(TUCK_ALONE, ("default",)) is True


def test_a_tuck_beside_a_real_change_keeps_the_real_changes_digest(tuck_comparator):
    """·Fee shortens by a column, and the ·At·J'ai tuck is in the tail after it. The after tail is the before tail shifted one column left with no pixel different, so it is pulled back and removed, and what remains is the shortened ·Fee. That is the same delta and digest as the tuck-free window, so both windows share one echo key and one blessed digest fills both."""
    tucked = tuck_comparator.config_diff(TUCKED_WINDOW, "default")
    plain = tuck_comparator.config_diff(PLAIN_WINDOW, "default")
    assert tucked == plain == FEE_SHORTENED
    assert delta_digest(tucked) == delta_digest(plain)
    assert tuck_comparator.picture_identical(TUCKED_WINDOW, ("default",)) is False


def test_a_re_spelling_the_neighbor_no_longer_covers_stays_in_the_delta(tmp_path):
    """The same change to ·At beside a ·J'ai that no longer reaches the column leaves a real hole, and the delta is that hole (a column lost, with no shift) instead of the shortened-·Fee digest. The delta is read from the rendered union of this window, so a change that is usually invisible still shows when it leaves a hole."""
    before = _build_font(tmp_path / "before.ttf", TUCK_BEFORE, TUCK_CMAP)
    holed = _build_font(tmp_path / "holed.ttf", TUCK_HOLE, TUCK_CMAP)
    comparator = InkComparator(before, holed)
    diff = comparator.config_diff(TUCKED_WINDOW, "default")
    assert diff != FEE_SHORTENED
    assert diff == (((0, 0), (0, 1), (0, 2)), (), 0)
    assert comparator.picture_identical(TUCK_ALONE, ("default",)) is False


def test_u_0126_is_ink_identical_only_because_kerning_is_neutralized(comparator):
    """◊ZWNJ ·May·Oy·Pea renders the same ink in both fonts once `kern` is off, and the old font does kern it: positions change when the feature is toggled."""
    text = "".join(chr(value) for value in (0x200C, 0xE665, 0xE679, 0xE650))
    assert comparator.ink_identical(text, ("default",)) is True
    before = Shaper(BEFORE_FONT)
    kerned = before.shape(text, {**features_for("default"), "kern": True})
    neutral = before.shape(text, kern_neutral(features_for("default")))
    assert kerned.names == neutral.names
    assert kerned.positions != neutral.positions


def test_u_0000_is_ink_identical(comparator):
    """The first window of the workload, ␣·Pea·Pea, renders the same ink in both fonts. This test checks only the ink, not the ordering."""
    text = "".join(chr(value) for value in (0x0020, 0xE650, 0xE650))
    assert comparator.ink_identical(text, ("default",)) is True


def test_verdicts_are_deterministic_across_two_comparators(mini_units, comparator):
    """A second comparator over the same fonts reaches the same verdict on a sample of about a hundred windows, which checks that the comparator's caches cannot change an answer."""
    again = InkComparator(BEFORE_FONT, MINI_FONT)
    sample = mini_units[:: max(1, len(mini_units) // 100)]
    assert [comparator.ink_identical(_text(unit), unit.configs) for unit in sample] == [
        again.ink_identical(_text(unit), unit.configs) for unit in sample
    ]


def test_config_diff_localizes_the_delta_to_the_changed_region(comparator):
    """In the may-baseline-entry-extension-dropped class, ·Pea·May drops ·May's one-pixel baseline entry extension. Followers after the judged pair add no cell to the delta: ·Low and ·Low·Fee paint the same picture shifted left by the dropped column, so the localized delta is the same across the follower contexts and they share one echo key. Only the recorded shift distinguishes a window with followers from the bare pair, whose shift is 0."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    one_follower = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667))
    two_followers = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667, 0xE658))
    diff_pair = comparator.config_diff(pair, "default")
    diff_one = comparator.config_diff(one_follower, "default")
    diff_two = comparator.config_diff(two_followers, "default")
    assert diff_two == diff_one
    assert diff_two[:2] == diff_pair[:2]
    assert diff_two[0] and diff_two[1]
    assert diff_pair[2] == 0
    assert diff_two[2] == -1


def test_a_real_one_pixel_change_is_not_picture_identical(comparator):
    """·Pea·Tea·Eight·Roe differs by a single pixel that no neighbor covers, so the picture channel must not approve it."""
    text = "".join(chr(value) for value in (0xE650, 0xE652, 0xE673, 0xE668))
    assert comparator.picture_identical(text, ("default",)) is False


def test_the_delta_sentinel_is_the_picture_reading_over_a_sample(mini_units, comparator):
    """Over a stride of the frozen windows, with real compiled outlines, `config_diff` returns the sentinel exactly when `picture_equal` is true, and always when the pieces are identical. So piece identity implies the sentinel, the sentinel matches picture identity, and the build's picture flag, read from the same diffs it digests, always agrees with `picture_equal`."""
    for unit in mini_units[::5]:
        for config in unit.configs:
            sentinel = comparator.config_diff(_text(unit), config) == IDENTITY_DIFF
            assert sentinel == comparator.picture_equal(_text(unit), config), (unit.codepoints, config)
            if comparator.pieces_identical(_text(unit), config):
                assert sentinel, (unit.codepoints, config)


def test_delta_digest_is_a_d_prefixed_twelve_hex_token(comparator):
    """The shape a standing-approval rule matches on and check_unit validates: `d-` followed by twelve lowercase hex digits, for a real localized delta and for the identity sentinel alike."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    for diff in (comparator.config_diff(pair, "default"), ((), (), 0), ((), (), -50)):
        digest = delta_digest(diff)
        assert len(digest) == 14
        assert digest.startswith("d-")
        assert all(character in "0123456789abcdef" for character in digest[2:])


def test_the_identity_diff_digests_to_a_pinned_constant():
    """IDENTITY_DIFF is ((), (), 0), the sentinel the build does not record in `ink_deltas`. The test pins both the value and its digest, because changing the recipe would invalidate every digest in rebuild/standing-approvals.yaml. A nonzero shift is a different delta and gets a different digest even when both cell lists are empty."""
    assert IDENTITY_DIFF == ((), (), 0)
    assert delta_digest(((), (), 0)) == "d-f923c43ec75a"
    assert delta_digest(((), (), 1)) != delta_digest(((), (), 0))


def test_signature_digest_is_determined_by_the_tuple_alone(comparator):
    """Equal signatures get equal digests across comparators and processes, which lets the persisted ink-signature store serve a digest recorded by an earlier build, and different placed ink gets a different digest."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    digest = signature_digest(comparator.signature(pair, "default"))
    again = InkComparator(BEFORE_FONT, MINI_FONT)
    assert signature_digest(again.signature(pair, "default")) == digest
    assert signature_digest(comparator.signature(pair[:1], "default")) != digest


def test_signature_digest_uses_alias_insensitive_marshal_v2():
    outline = (("lineTo", ((1, 2), (3, 4))),)
    shared = (outline, outline)
    reconstructed = (
        outline,
        tuple((operator, tuple((x, y) for x, y in points)) for operator, points in outline),
    )
    assert shared == reconstructed
    expected = hashlib.sha256(marshal.dumps(shared, 2)).hexdigest()
    assert signature_digest(shared) == expected
    assert signature_digest(reconstructed) == expected


def test_shaper_for_shares_one_memoized_shaper_per_font():
    """`shaper_for` returns one shared shaper per font per process. Its memoized `shape` returns what a plain Shaper returns, and the features dict is canonicalized so that {} and None, and any key order, share one memo entry."""
    shared = shaper_for(BEFORE_FONT)
    assert shaper_for(BEFORE_FONT) is shared
    plain = Shaper(BEFORE_FONT)
    text = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667))
    features = {"ss03": True, "kern": False}
    assert shared.shape(text, features) == plain.shape(text, features)
    assert shared.shape(text, {"kern": False, "ss03": True}) is shared.shape(text, features)
    assert shared.shape(text) == plain.shape(text)
    assert shared.shape(text, {}) is shared.shape(text)


def test_shaper_for_rekeys_when_the_font_changes_on_disk(tmp_path):
    """A font rewritten in place, as when a test builds surfaces over different mini fonts at one path, must not get stale shapes, so the registry keys on the path, mtime, and size."""
    target = tmp_path / "font.otf"
    shutil.copyfile(BEFORE_FONT, target)
    first = shaper_for(target)
    shutil.copyfile(JUNIOR_FONT, target)
    second = shaper_for(target)
    assert second is not first


def test_the_shape_memo_reports_what_it_holds_and_releases_it_whole():
    """`shape_memo_census` counts the entries all of `shaper_for`'s shapers hold, with an approximate byte figure that grows with the entries and is zero when there are none. A repeated shape is a memo hit and changes neither number. `release_shape_memos` empties every memo at once (the build calls it after each unit batch), and the next shape calls HarfBuzz again: the result is equal but is a new object, not the one the memo held."""
    release_shape_memos()
    assert shape_memo_census() == (0, 0)
    shared = shaper_for(BEFORE_FONT)
    text = "".join(chr(value) for value in (0xE650, 0xE665, 0xE667))
    held = shared.shape(text)
    one = shape_memo_census()
    assert one.entries == 1 and one.approx_bytes > 0
    assert shared.shape(text) is held
    assert shape_memo_census() == one
    shared.shape(text, {"ss03": True, "kern": False})
    two = shape_memo_census()
    assert two.entries == 2 and two.approx_bytes > one.approx_bytes
    release_shape_memos()
    assert shape_memo_census() == (0, 0)
    fresh = shared.shape(text)
    assert fresh == held and fresh is not held
    assert shape_memo_census().entries == 1


@pytest.fixture(scope="module")
def oracle():
    return JuniorOracle(JUNIOR_FONT, BEFORE_FONT, MINI_FONT)


def test_junior_tracking_premise_holds(oracle):
    """The oracle's premise: Junior carries the same isolated letterforms as Senior plus one pixel (50 units at 550 units per em) of extra advance on every Quikscript glyph, and no advance difference on any other glyph. The constructor checks the advances, and this test pins the tracking value."""
    assert oracle.tracking == 50


def test_junior_oracle_approves_a_suppressed_ligature_unit(oracle):
    """The ·No·Day·Utter·Utter window, where every configuration but ss10 forms the ·Day·Utter ligature: the rebuild's ss10 rendering is Junior's isolated rendering minus the tracking, so the unit is machine-approvable."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("ss10",), text) is True


def test_junior_oracle_only_judges_ss10_only_units(oracle):
    """The oracle judges only units whose whole divergence is under ss10. A unit also divergent under any other config still needs those configs judged, so the oracle returns False regardless of the ink."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("default",), text) is False
    assert oracle.approves(("default", "ss10"), text) is False
    assert oracle.approves((), text) is False


def test_junior_oracle_refuses_the_lowered_namer_dot(oracle):
    """The known counterexample, the `· ◊ZWNJ ·X·Y` boundary windows: Junior draws the namer dot lowered (periodcentered.lowered) where the rebuild's ss10 run draws the plain dot, so the placed ink differs and the oracle leaves the unit to a human."""
    text = "".join(chr(value) for value in (0x00B7, 0x200C, 0xE666, 0xE653))
    assert oracle.approves(("ss10",), text) is False
