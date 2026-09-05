"""Tests for the review surface's ink-identity comparison: the unified boolean (config_diff's identity sentinel, which the signature now carries by definition) reproduces the worked readings — the ◊ZWNJ ·May·Oy·Pea window is ink-identical only because kerning is neutralized, ␣·Pea·Pea is ink-identical outright, a real one-pixel change is not picture-identical, and the verdict is deterministic across comparators.

Nothing here reads the live corpus. The windows come from the frozen mini bundle's audit and are shaped in that bundle's own font, because every claim in this file is about the comparator rather than about today's letters. The fold's soundness is definitional rather than sampled: `signature` returns the same two `run_ink` lists `config_diff` reads, so equal signatures give equal deltas and equal ink flags with nothing left to sample. What a sample cannot say either way is that the signature ignores glyph names, so the marker font gives two names one outline and holds their signatures equal.

Also here: `delta_digest`, the persisted identity of one config's localized delta, whose shape check_unit enforces and whose recipe is a byte-identity contract with the digests recorded in rebuild/standing-approvals.yaml.

And, on inputs this file builds for itself, the two pixel-grain readings the standing approvals' slide shape works from: `rectilinear_cells`, which rasterizes one grid-rectilinear outline under nonzero winding — a hole stays empty, two overlapping same-direction contours fill their union, and a curve or an off-grid coordinate answers None rather than a picture — and `named_run`, the shaped run with its glyph names still attached, which keeps the inkless markers its pieces drop and whose nameless projection is `run_ink`.
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
    """The frozen bundle's whole workload, which is about a thousand windows over four letters and the boundary tokens — enough for a stride to witness a property of the comparator, and small enough that loading it costs a second."""
    from rebuild.review.audit import load_workload
    from rebuild.review.enrich import LETTERS

    return load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(LETTERS))


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
    """The shape every bitmap-compiled letter is made of: a two-column, three-row block of ink answers exactly its six cells, indexed from the outline's own leftmost, lowest point."""
    outline = _closed(((0, 0), (100, 0), (100, 150), (0, 150)))
    assert rectilinear_cells(outline) == frozenset({(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)})


def test_rectilinear_cells_follows_an_l_shape():
    """A single concave contour, which is what a stroke turning a corner compiles to: the cells follow the outline rather than its bounding box."""
    outline = _closed(((0, 0), (100, 0), (100, 50), (50, 50), (50, 150), (0, 150)))
    assert rectilinear_cells(outline) == frozenset({(0, 0), (1, 0), (0, 1), (0, 2)})


def test_rectilinear_cells_leaves_a_donuts_hole_empty():
    """Two contours wound opposite ways — the counter every closed loop of stroke draws: the winding number cancels inside the hole, so a picture comparison sees the ring and not the block."""
    outer = ((0, 0), (200, 0), (200, 200), (0, 200))
    hole = ((50, 50), (50, 150), (150, 150), (150, 50))
    ring = {(column, row) for column in range(4) for row in range(4)}
    ring -= {(1, 1), (2, 1), (1, 2), (2, 2)}
    assert rectilinear_cells(_closed(outer, hole)) == frozenset(ring)


def test_rectilinear_cells_fills_the_union_of_two_overlapping_rectangles():
    """Same winding direction, so the overlap counts twice and stays filled: a glyph whose strokes cross must not punch a hole where they meet, and the shared column is one cell rather than two."""
    left = ((0, 0), (100, 0), (100, 100), (0, 100))
    right = ((50, 0), (150, 0), (150, 100), (50, 100))
    assert rectilinear_cells(_closed(left, right)) == frozenset(
        {(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)}
    )


def test_rectilinear_cells_refuses_a_curve():
    """The contract's edge: anything that is not a straight run of grid edges makes no picture claim at all, and None is what the slide shape reads as "cannot judge this window"."""
    outline = (("moveTo", ((0, 0),)), ("qCurveTo", ((50, 100), (100, 0))), ("closePath", ()))
    assert rectilinear_cells(outline) is None


def test_rectilinear_cells_refuses_an_off_grid_coordinate():
    """A rectangle three and a half pixels tall is rectilinear but not on the grid, so cell centers would no longer be exactly inside or outside — refused rather than rounded."""
    outline = _closed(((0, 0), (100, 0), (100, 175), (0, 175)))
    assert rectilinear_cells(outline) is None


def test_rectilinear_cells_refuses_an_unclosed_contour():
    """A contour that is never closed is not a picture either: both shipped pens close every contour, so an open one can only mean the outline is not what this rasterizer was written for — refused rather than read as empty."""
    outline = (("moveTo", ((0, 0),)), ("lineTo", ((100, 0),)), ("lineTo", ((100, 150),)))
    assert rectilinear_cells(outline) is None


QS_A_OUTLINE = (((0, 0), (100, 0), (100, 150), (0, 150)),)
MARKER_GLYPHS = {
    "qsA": (QS_A_OUTLINE, 100),
    "qsB": ((((50, 0), (150, 0), (150, 150), (50, 150)),), 200),
    # A second name over qsA's outline and advance exactly: the only thing that distinguishes it from qsA is what a signature must not read.
    "qsC": (QS_A_OUTLINE, 100),
    "space": ((), 100),
}
MARKER_CMAP = {0xE001: "qsA", 0xE002: "qsB", 0xE003: "qsC", 0x20: "space"}


def _build_font(path, glyphs=MARKER_GLYPHS, cmap=MARKER_CMAP):
    """A tiny TTF for the name-grain readings: two inked rectangles, one of them inset in its own frame so its origin_x is not zero, plus an outline-less `space` to stand in for the surface's inkless markers. Each glyph's left sidebearing is set to its own leftmost point, which is load-bearing rather than tidy — fontTools' TrueType glyph set translates an outline by `lsb - xMin` on the way out, and would otherwise pull the inset glyph back to x=0 and erase the very origin under test."""
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
    """The whole point of the named form: the name tuple is the shaped run entire, markers included, so a caller can hold it against a recorded glyph list — while the pieces are ink only, and carry each glyph's placed position plus the own-frame origin that distinguishes two glyphs drawing the same strokes from different frames."""
    names, pieces = marker_comparator.named_run("before", MARKER_TEXT, {})
    assert names == ("space", "qsA", "qsB")
    assert [piece[0] for piece in pieces] == ["qsA", "qsB"]
    assert [piece[2:] for piece in pieces] == [(100, 0, 0), (250, 0, 50)]


def test_run_ink_is_the_nameless_projection_of_named_run(marker_comparator):
    """`run_ink` is defined as the names dropped and nothing else, which is what keeps a name out of every piece comparison the delta alignment makes."""
    _names, pieces = marker_comparator.named_run("before", MARKER_TEXT, {})
    assert marker_comparator.run_ink("before", MARKER_TEXT, {}) == [piece[1:] for piece in pieces]


def test_the_signature_is_blind_to_the_glyph_name(marker_comparator):
    """The one thing the definitional fold cannot prove of itself: `signature` is `run_ink` on both sides, and `run_ink` drops the name, so two glyphs drawing the same outline at the same advance under different names present the same signature. That is what makes the ink-duplicate merge a fold of relabel-splits rather than of nothing — the real corpus's whole population of candidates differ in exactly this way — and the synthetic fold tests stub `ink_sig`, so the real function is witnessed here."""
    assert marker_comparator.signature(chr(0xE001), "default") == marker_comparator.signature(
        chr(0xE003), "default"
    )


def test_the_intern_rasterizes_a_shape_in_its_own_canonical_frame(marker_comparator):
    """A shape's cells are indexed from its own leftmost, lowest point, not from where it was placed — which is what lets one rasterization serve every placement of that shape in both fonts, with the placement added back a column at a time."""
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
    """The founding picture-identity shape at toy scale: before, ·A's second column is double-drawn by ·B's first; after, ·A keeps the column and ·B gives up its overlapping half — the same three columns of ink, owned differently."""
    root = tmp_path_factory.mktemp("overlap-fonts")
    before = _build_font(root / "before.ttf", OVERLAP_BEFORE, OVERLAP_CMAP)
    after = _build_font(root / "after.ttf", OVERLAP_AFTER, OVERLAP_CMAP)
    return InkComparator(before, after)


def test_picture_identity_sees_through_an_overlap_removal(overlap_comparator):
    """The piece-grain reading sees a changed ·B and a nonempty delta; the whole-run picture is the same six cells on both sides. The delta alone could not say so — ·A is stripped as common prefix, taking the pixel that still covers ·B's loss with it."""
    assert overlap_comparator.ink_identical(OVERLAP_TEXT, ("default",)) is False
    assert overlap_comparator.config_diff(OVERLAP_TEXT, "default") != IDENTITY_DIFF
    assert overlap_comparator.picture_identical(OVERLAP_TEXT, ("default",)) is True
    picture = {(column, row) for column in range(3) for row in range(3)}
    assert overlap_comparator.run_cells("before", OVERLAP_TEXT, {}) == picture
    assert overlap_comparator.run_cells("after", OVERLAP_TEXT, {}) == picture


def test_picture_identity_fails_closed_off_the_grid(tmp_path):
    """No cell reading can be made of a placement or an outline that is not on the PIXEL_SIZE grid, and the channel refuses rather than guesses: an off-grid advance and an off-grid edge each leave the window to a human."""
    before = _build_font(tmp_path / "before.ttf", OVERLAP_BEFORE, OVERLAP_CMAP)
    slid = _build_font(tmp_path / "slid.ttf", {"qsA": (COLUMN, 75), "qsB": (HALF_COLUMN, 100)}, OVERLAP_CMAP)
    comparator = InkComparator(before, slid)
    assert comparator.run_cells("after", OVERLAP_TEXT, {}) is None
    assert comparator.picture_identical(OVERLAP_TEXT, ("default",)) is False
    ragged = (((0, 0), (25, 0), (25, 150), (0, 150)),)
    torn = _build_font(tmp_path / "torn.ttf", {"qsA": (COLUMN, 100), "qsB": (ragged, 100)}, OVERLAP_CMAP)
    comparator = InkComparator(before, torn)
    assert comparator.run_cells("after", OVERLAP_TEXT, {}) is None
    assert comparator.picture_identical(OVERLAP_TEXT, ("default",)) is False


def test_u_0126_is_ink_identical_only_because_kerning_is_neutralized(comparator):
    """The worked kern-noise example: ◊ZWNJ ·May·Oy·Pea renders the same ink in both fonts once `kern` is off, and the old font really does kern it (positions move when the feature toggles), so the unit was a kern-only straggler before the census went kern-neutral."""
    text = "".join(chr(value) for value in (0x200C, 0xE665, 0xE679, 0xE650))
    assert comparator.ink_identical(text, ("default",)) is True
    before = Shaper(BEFORE_FONT)
    kerned = before.shape(text, {**features_for("default"), "kern": True})
    neutral = before.shape(text, kern_neutral(features_for("default")))
    assert kerned.names == neutral.names
    assert kerned.positions != neutral.positions


def test_u_0000_is_ink_identical(comparator):
    """The first window of the workload, ␣·Pea·Pea: the same ink in both fonts. That it is the *first* is `build_units`' ordering to state, and the contracts lane states it over the fixture; what is live here is the ink."""
    text = "".join(chr(value) for value in (0x0020, 0xE650, 0xE650))
    assert comparator.ink_identical(text, ("default",)) is True


def test_verdicts_are_deterministic_across_two_comparators(mini_units, comparator):
    """Memoization hygiene: a second comparator over the same fonts reaches the same verdict. A hundred windows witness that as well as a corpus stride did — the property is that the memo cannot serve a different answer, not that it holds for a particular number of windows."""
    again = InkComparator(BEFORE_FONT, MINI_FONT)
    sample = mini_units.units[:: max(1, len(mini_units.units) // 100)]
    assert [comparator.ink_identical(_text(unit), unit.configs) for unit in sample] == [
        again.ink_identical(_text(unit), unit.configs) for unit in sample
    ]


def test_config_diff_localizes_the_delta_to_the_changed_region(comparator):
    """The worked flanking-context example from the may-baseline-entry-extension-dropped class: ·Pea·May drops ·May's one-pixel baseline entry extension, and followers appended after the judged pair add no ink to the delta — ·Low and ·Low·Fee render identically in their own frames and merely slide left by the dropped pixel's 50 units, so the localized delta is byte-identical across the follower contexts (one echo key, one visual question) and only the recorded shift distinguishes a window with followers from the bare pair, whose delta shows nothing sliding."""
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
    assert diff_two[2] == -50


def test_a_real_one_pixel_change_is_not_picture_identical(comparator):
    """·Pea·Tea·Eight·Roe differs by a single pixel that no neighbor covers, which is exactly the difference the channel must keep asking about."""
    text = "".join(chr(value) for value in (0xE650, 0xE652, 0xE673, 0xE668))
    assert comparator.picture_identical(text, ("default",)) is False


def test_piece_identity_implies_picture_identity_over_a_sample(mini_units, comparator):
    """The build asks the picture question only where the piece question said no, on the strength of this implication; a stride over the frozen windows holds it against real compiled outlines, off-grid placements included."""
    for unit in mini_units.units[::5]:
        for config in unit.configs:
            if comparator.config_diff(_text(unit), config) == IDENTITY_DIFF:
                assert comparator.picture_equal(_text(unit), config), (unit.codepoints, config)


def test_delta_digest_is_a_d_prefixed_twelve_hex_token(comparator):
    """The shape a standing-approval rule matches on and check_unit validates: `d-` followed by exactly twelve lowercase hex digits, for a real localized delta and for the identity sentinel alike."""
    pair = "".join(chr(value) for value in (0xE650, 0xE665))
    for diff in (comparator.config_diff(pair, "default"), ((), (), 0), ((), (), -50)):
        digest = delta_digest(diff)
        assert len(digest) == 14
        assert digest.startswith("d-")
        assert all(character in "0123456789abcdef" for character in digest[2:])


def test_the_identity_diff_digests_to_a_pinned_constant():
    """((), (), 0) is IDENTITY_DIFF, the ink-identical sentinel the build declines to record, and both the constant's value and its digest are pinned here — a byte-identity contract, since changing the recipe orphans every digest already written into rebuild/standing-approvals.yaml. A nonzero shift is a different delta and digests apart even with empty middles."""
    assert IDENTITY_DIFF == ((), (), 0)
    assert delta_digest(((), (), 0)) == "d-f923c43ec75a"
    assert delta_digest(((), (), 1)) != delta_digest(((), (), 0))


def test_signature_digest_is_determined_by_the_tuple_alone(comparator):
    """Equal signatures digest equally across comparators and processes — what lets the persisted ink-signature store serve a digest recorded by a prior build — and different placed ink digests apart."""
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
    """The surface build's shared shaper: one instance per font per process, and its memoized `shape` returns exactly what a plain Shaper returns, with the features dict canonicalized so {} and None — and any key order — land on one memo entry."""
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
    """A font rewritten in place — a test building surfaces over different mini fonts at one path — must never serve stale shapes: the registry keys on the file's identity, not its path alone."""
    target = tmp_path / "font.otf"
    shutil.copyfile(BEFORE_FONT, target)
    first = shaper_for(target)
    shutil.copyfile(JUNIOR_FONT, target)
    second = shaper_for(target)
    assert second is not first


def test_the_shape_memo_reports_what_it_holds_and_releases_it_whole():
    """The memo's two instruments, and the bound they serve. `shape_memo_census` counts every entry `shaper_for`'s shapers hold between them, with an approximate byte figure that grows with the entries and answers zero for nothing; a repeated shape is a memo hit and moves neither. `release_shape_memos` empties every one of them at once — the build calls it behind each unit batch — and a shape after the release is HarfBuzz again: an equal result, but a fresh object rather than the one the memo held, which is the difference between a served shape and a recomputed one."""
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
    """The oracle's founding premise, verified at construction and pinned here: Junior carries the same isolated letterforms as Senior plus exactly one pixel (50 units at upem 550) of extra advance on every Quikscript glyph, and no advance difference anywhere else."""
    assert oracle.tracking == 50


def test_junior_oracle_approves_a_suppressed_ligature_unit(oracle):
    """The ·No·Day·Utter·Utter window (divergent only under ss10 because the old font still formed the ·Day·Utter ligature there): the rebuild's ss10 rendering is Junior's isolated rendering minus the tracking, so the unit is machine-approvable."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("ss10",), text) is True


def test_junior_oracle_only_judges_ss10_only_units(oracle):
    """The oracle's ruling covers exactly the units whose entire divergence is under ss10; a unit also divergent under any other config still needs its other legs judged, so the oracle abstains regardless of the ink."""
    text = "".join(chr(value) for value in (0xE666, 0xE653, 0xE67A, 0xE67A))
    assert oracle.approves(("default",), text) is False
    assert oracle.approves(("default", "ss10"), text) is False
    assert oracle.approves((), text) is False


def test_junior_oracle_refuses_the_lowered_namer_dot(oracle):
    """The known counterexample (the `· ◊ZWNJ ·X·Y` boundary windows): Junior renders the namer dot lowered (periodcentered.lowered) where the rebuild's ss10 run draws the plain dot, so the placed ink differs and the oracle correctly leaves the unit for human eyes."""
    text = "".join(chr(value) for value in (0x00B7, 0x200C, 0xE666, 0xE653))
    assert oracle.approves(("ss10",), text) is False
