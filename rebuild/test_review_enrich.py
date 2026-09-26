"""Tests for the review corpus's enrichment: the notation map against doc/glyph-names.md, divergent positions and pair selection on known units, highlight x-ranges against hand-computed hmtx sums, and the secondary-junction primary unit resolver over hand-built stubs.

Each test names its units by codepoints and takes them from `example_units`, a filtered load of the frozen mini bundle's audit settled under the spec that `mini_bundle` materializes, so no test reads the live corpus. If a named window disappears, regenerating the bundle fails and names it. The build checks three claims over every shipped unit instead: that re-settlement agrees with the audit, that the two before-junction derivations agree, and the shape of the summary.
"""

import dataclasses
import random
import re
import warnings
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from rebuild.review.enrich import (
    LETTERS,
    EnrichedUnit,
    Enricher,
    PrimaryUnitProjection,
    SecondaryJunction,
    letter_display,
    load_spec,
    notation,
    notation_tokens,
    parse_entry_extension,
    resolve_primary_unit_assignments,
    resolve_primary_units,
    rune_display,
    primary_unit_projection,
    text_entities,
)
from rebuild.review.unit_cache import unit_id_for
from rebuild.review.ink import kern_neutral, translate_outline
from rebuild.validation.rowmodel import iter_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_FONT = MINI / "M1.otf"


@pytest.fixture(scope="module")
def spec(mini_bundle):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_spec(mini_bundle.spec_root)


@pytest.fixture(scope="module")
def enricher(spec, mini_bundle):
    return Enricher(spec, MINI, MINI_FONT, repo_root=REPO_ROOT, subset_pack=mini_bundle.subset_pack)


@pytest.fixture(scope="module")
def units_by_key(example_units):
    """Return the example-window units, keyed by (codepoints, first config)."""
    return example_units


def test_letter_table_matches_glyph_names_doc():
    doc = (REPO_ROOT / "doc" / "glyph-names.md").read_text(encoding="utf-8")
    rows = re.findall(r"\|\s*(·\S+)\s*\|\s*U\+([0-9A-F]{4})\s*\|\s*(qs\w+)\s*\|", doc)
    assert len(rows) == len(LETTERS)
    for display, hex_value, family in rows:
        codepoint = int(hex_value, 16)
        assert LETTERS[codepoint] == family
        assert letter_display(family) == display


def test_notation_examples():
    assert notation((0x200C, 0xE652, 0xE679)) == "◊ZWNJ ·Tea·Oy"
    assert notation((0xE650, 0xE665)) == "·Pea·May"
    assert notation((0x00B7, 0xE679)) == "· ·Oy"
    assert notation((0xE650, 0x0020, 0xE650)) == "·Pea ␣ ·Pea"


def test_text_entities_are_numeric_references():
    assert text_entities((0x200C, 0xE652)) == "&#x200C;&#xE652;"


def test_notation_tokens_align_one_to_one_with_codepoints():
    assert notation_tokens((0x200C, 0xE652, 0xE679)) == ("◊ZWNJ", "·Tea", "·Oy")
    assert notation_tokens((0x00B7, 0xE679)) == ("·", "·Oy")
    assert notation_tokens((0xE650, 0x0020, 0xE650)) == ("·Pea", "␣", "·Pea")
    assert notation_tokens((0xE664, 0xE65D)) == ("·-ing", "·J’ai")


def test_pair_codepoints_covers_the_pairs_codepoint_span(enricher, units_by_key):
    # A plain two-letter pair: cell indices and codepoint positions coincide.
    plain = enricher.enrich(units_by_key[("E652:E670", "default")])
    assert plain.pair == (0, 1)
    assert plain.pair_codepoints == (0, 1)
    # An interior pair after a ZWNJ break: the span starts at the pair's first codepoint, not at zero.
    interior = enricher.enrich(units_by_key[("E650:200C:E650:E665", "default")])
    assert interior.pair == (2, 3)
    assert interior.pair_codepoints == (2, 3)
    # A trailing ligature: one cell covers two codepoints, so the span is wider than the cell pair.
    ligated = enricher.enrich(units_by_key[("200C:E652:E679", "default")])
    assert ligated.pair == (0, 1)
    assert ligated.after_cells[-1].startswith("qsTea_qsOy/")
    assert ligated.pair_codepoints == (0, 2)
    assert ligated.notation_tokens == ("◊ZWNJ", "·Tea", "·Oy")


def test_position_only_mismatch_marks_the_boundary_without_a_pair(enricher, units_by_key):
    # A position-only unit whose mismatch kerning explains: an advance-only one-pixel mismatch on the letter beside the boundary, with no cell- or junction-grain divergence. The mark is placed on the ◊ZWNJ beside the mismatch, and pair stays None so no sample band is highlighted.
    enriched = enricher.enrich(units_by_key[("E650:200C:E676:E665", "default")])
    assert enriched.pair is None
    assert enriched.diff_positions == ()
    assert enriched.notation_tokens == ("·Pea", "◊ZWNJ", "·Ah", "·May")
    assert enriched.pair_codepoints == (1, 1)


def test_parse_entry_extension():
    assert parse_entry_extension(("en-ext-1",)) == 1
    assert parse_entry_extension(("en-con-2", "locked")) == -2
    assert parse_entry_extension(()) == 0


def test_known_halves_extension_unit(enricher, units_by_key):
    # The ss03 window `·Tea ~b~ ·Day+Utter ~x~ ·Tea`: the qsDay_qsUtter ligature cell carries both its baseline en-ext-1 and its own x-height exit extension, so the enricher reports an extension at both of its junctions. The final ·Tea is the full bar, which also differs from the old font's half.
    unit = units_by_key[("E652:E653:E67A:E652", "ss03")]
    enriched = enricher.enrich(unit)
    assert enriched.before_glyphs == (
        "qsTea.ex-y0",
        "qsDay_qsUtter.half.en-y0.ex-y5.ex-ext-1",
        "qsTea.half.en-y5.after-xheight-exit",
    )
    assert enriched.before_junctions == ("y0", "y5")
    assert enriched.after_junctions == ("y0", "y5")
    assert enriched.after_extensions == (1, 1)
    assert enriched.diff_positions == (1, 2)
    assert enriched.pair == (1, 2)
    assert "glyph_data/runes/qsDay_qsUtter.yaml:policy.extend" in " ".join(enriched.provenance)


def test_annotation_grain_renames_do_not_anchor_the_pair(enricher, units_by_key):
    # ·It·Utter·It·May: position 1 is a bare-name ·Utter rename with an identical drawing, and the ink change (a dropped non-summing extension) is at the ·It·May junction. The pair anchors on the ink-visible positions. The rename stays in diff_positions without a secondary junction, and the summary describes the anchored position.
    unit = units_by_key[("E670:E67A:E670:E665", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.before_glyphs[1] == "qsUtter"
    assert enriched.diff_positions == (1, 2, 3)
    assert enriched.pair == (2, 3)
    assert enriched.secondary_junctions == ()
    assert enriched.summary.startswith("New: ·It ")


def test_pure_rename_unit_keeps_its_anchor(enricher, units_by_key):
    # A dangling-anchor drop whose ink is identical everywhere (·It·It's benign ex-y5): no position is ink-visible, so pair picking falls back to the full divergent-position set instead of leaving the unit without a judged pair.
    unit = units_by_key[("E670:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.before_glyphs == ("qsIt.ex-y5", "qsIt")
    assert enriched.diff_positions == (0,)
    assert enriched.pair == (0, 1)


@pytest.fixture(scope="module")
def mini_units(mini_bundle):
    """Return the frozen bundle's whole workload, settled under the pinned spec: every window over the `LETTERS` and `BOUNDARIES` of rebuild/review/fixtures/mini/regenerate.py, plus its `EXAMPLE_WINDOWS`. The enricher can enrich every one of them."""
    from rebuild.review.audit import load_workload

    return load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(LETTERS))


def _geometry_segment(outlines, shaped, pens, spans, cp_start: int, cp_end: int) -> tuple:
    """Return the reference geometry of one segment: each covering glyph's decomposed outline placed at its pen position, then all of them translated so the leftmost point is at x=0, as a sorted tuple."""
    placed = []
    for index, (start, end) in enumerate(spans):
        if start < cp_end and cp_start < end:
            x_offset, y_offset, _advance = shaped.positions[index]
            value = outlines.outline(shaped.names[index])
            if value:
                placed.append((value, pens[index] + x_offset, y_offset))
    xs = [
        dx + point[0]
        for value, dx, _dy in placed
        for _operator, points in value
        for point in points
        if point is not None
    ]
    if not xs:
        return ()
    x0 = min(xs)
    return tuple(sorted(translate_outline(value, dx - x0, dy) for value, dx, dy in placed))


def test_segment_pieces_materialize_to_the_geometry_they_stand_in_for(
    spec, mini_units, mini_bundle, monkeypatch
):
    """`_segment_pieces` compares (shape key, x, y) triples from the intern both fonts share instead of building translated outlines, and `_ink_visible_positions` reads only `before != after` over the result. The test checks that materializing each piece through the intern gives exactly the sorted geometry `_geometry_segment` builds, for every segment the enricher compares over the frozen workload in both fonts. So the ink-visible positions, the judged pair, and the shard bytes rebuild/test_unit_cache.py compares are the same under either form. The last assertion checks that the comparison returned both equal and unequal results, so a comparison that returned the same answer for every segment would fail."""
    enricher = Enricher(spec, MINI, MINI_FONT, repo_root=REPO_ROOT, subset_pack=mini_bundle.subset_pack)
    recorded: list[tuple] = []
    original = enricher._segment_pieces

    def recording(side, shaped, pens, spans, cp_start, cp_end):
        pieces = original(side, shaped, pens, spans, cp_start, cp_end)
        recorded.append((side, shaped, pens, spans, cp_start, cp_end, pieces))
        return pieces

    monkeypatch.setattr(enricher, "_segment_pieces", recording)
    enricher.enrich_many(mini_units.units())
    assert recorded
    intern = enricher._intern
    for side, shaped, pens, spans, cp_start, cp_end, pieces in recorded:
        materialized = tuple(sorted(translate_outline(intern.value(key), x, y) for key, x, y in pieces))
        reference = _geometry_segment(enricher._outlines[side], shaped, pens, spans, cp_start, cp_end)
        assert materialized == reference, (side, shaped.names, cp_start, cp_end)
    verdicts = {before[-1] != after[-1] for before, after in zip(recorded[::2], recorded[1::2])}
    assert verdicts == {True, False}


def test_zwnj_unit_carries_boundary_mark(enricher, units_by_key):
    unit = units_by_key[("200C:E652:E679", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.notation == "◊ZWNJ ·Tea·Oy"
    marks = list(enriched.boundary_marks)
    assert marks and marks[0]["kind"] == "zwnj" and marks[0]["index"] == 0
    assert enriched.after_cells[-1].startswith("qsTea_qsOy/")


def test_single_cell_unit_has_null_pair(enricher):
    from rebuild.review.audit import AuditRow, Unit

    row = AuditRow(
        "ss03",
        "E67B:E652",
        ("ligation",),
        "synthetic",
        ("qsOut.en-y0.ex-y5.ex-ext-1", "qsTea.half.en-y5.after-xheight-exit"),
        ("qsOut_qsTea/hapax/None/None/",),
    )
    unit = Unit(
        codepoints=row.codepoints,
        baseline=row.baseline,
        new=row.new,
        class_id="synthetic",
        row_count=1,
        configs=("ss03",),
        kinds=("ligation",),
    )
    enriched = enricher.enrich(unit)
    assert len(enriched.after_cells) == 1
    assert enriched.pair is None


def test_highlight_matches_hmtx_sums_on_a_break_only_unit(enricher, units_by_key):
    unit = units_by_key[("E670:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.after_junctions == ("break",)
    font = TTFont(str(MINI_FONT))
    hmtx = font["hmtx"]
    shaped = enricher.after_shaper.shape("".join(chr(v) for v in unit.codepoint_values))
    advances = [hmtx[name][0] for name in shaped.names]
    assert enriched.highlight_after["advance_total"] == sum(advances)
    assert enriched.highlight_after["x_min"] == 0
    assert enriched.highlight_after["x_max"] == sum(advances)


def test_highlight_matches_shaped_advances_on_a_joined_unit(enricher, units_by_key):
    unit = units_by_key[("E652:E670", "default")]
    enriched = enricher.enrich(unit)
    shaped = enricher.after_shaper.shape("".join(chr(v) for v in unit.codepoint_values))
    assert enriched.highlight_after["advance_total"] == sum(adv for _x, _y, adv in shaped.positions)
    assert enriched.highlight_after["x_min"] == 0
    assert enriched.highlight_after["x_max"] == enriched.highlight_after["advance_total"]
    before_shaped = enricher.before_shaper.shape(
        "".join(chr(v) for v in unit.codepoint_values), kern_neutral(None)
    )
    assert enriched.highlight_before["advance_total"] == sum(adv for _x, _y, adv in before_shaped.positions)
    row = enricher.subset_row("default", unit.codepoints)
    assert enriched.before_glyphs == row.glyphs


def test_highlight_covers_the_pair_not_the_run_when_pair_is_interior(enricher, units_by_key):
    unit = units_by_key[("E650:200C:E650:E665", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.pair == (2, 3)
    assert enriched.highlight_after["x_min"] > 0
    assert enriched.highlight_after["x_max"] == enriched.highlight_after["advance_total"]


def test_rune_display_uses_letter_names_not_raw_glyph_names():
    assert rune_display("qsMay") == "·May"
    assert rune_display("qsIng") == "·-ing"
    assert rune_display("qsTea_qsOy") == "·Tea+Oy"
    assert rune_display("zwnj") == "◊ZWNJ"
    assert rune_display("space") == "the space"


def test_summary_for_the_known_extension_unit(enricher, units_by_key):
    unit = units_by_key[("E652:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.summary.startswith("New: ")
    assert "·It" in enriched.summary
    assert "decided by" in enriched.summary


def test_summary_names_a_join_gain_in_prose(enricher, units_by_key):
    unit = units_by_key[("E650:E650:E67A", "default")]
    assert unit.class_id == "pea-chain-regularized"
    enriched = enricher.enrich(unit)
    assert "joins" in enriched.summary
    assert "·Pea" in enriched.summary
    assert "qs" not in enriched.summary.split("decided by")[0], "letters appear in rune-name notation"


def test_explain_text_keeps_header_and_divergent_positions(enricher, units_by_key):
    unit = units_by_key[("E652:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.explain_text.startswith("sequence E652:E670")
    assert "position 1: qsIt" in enriched.explain_text
    assert "position 0: qsTea" not in enriched.explain_text


@pytest.mark.parametrize("flag", ("ink_identical", "picture_identical", "junior_equivalent", "no_verdict"))
def test_a_slim_unit_renders_no_explain(enricher, units_by_key, flag):
    """A machine-approved or verdict-exempt unit ships without an explain, so the enricher does not render the candidate table for it. Its summary still comes from the same report, and its cells, junctions, and highlight geometry are computed as for any other unit."""
    unit = dataclasses.replace(units_by_key[("E652:E670", "default")], **{flag: True})
    enriched = enricher.enrich(unit)
    assert enriched.explain_text == ""
    assert enriched.summary.startswith("New: ")
    assert enriched.after_cells and enriched.highlight_after


def _stub_enriched(
    unit_id,
    values,
    cells,
    junctions,
    pair,
    *,
    ink_identical=False,
    picture_identical=False,
    junction_pairs=(),
):
    """A minimal EnrichedUnit for resolver tests: one codepoint per cell, before glyphs derived from the cell tokens, all before junctions break."""
    from rebuild.review.audit import Unit, format_codepoints

    spans = tuple((index, index + 1) for index in range(len(values)))
    return EnrichedUnit(
        unit=Unit(
            codepoints=format_codepoints(values),
            baseline=tuple(f"old-{cell}" for cell in cells),
            new=tuple(cells),
            class_id="synthetic",
            row_count=0,
            unit_id=unit_id,
            ink_identical=ink_identical,
            picture_identical=picture_identical,
        ),
        notation="",
        text_entities="",
        before_glyphs=tuple(f"old-{cell}" for cell in cells),
        before_junctions=("break",) * (len(cells) - 1),
        after_cells=tuple(cells),
        after_junctions=tuple(junctions),
        after_extensions=(),
        diff_positions=(),
        pair=pair,
        highlight_before={},
        highlight_after={},
        boundary_marks=(),
        explain_text="",
        provenance=(),
        report=None,  # pyright: ignore[reportArgumentType]
        after_spans=spans,
        before_spans=spans,
        secondary_junctions=tuple(
            SecondaryJunction(pair=junction_pair, highlight_before={}, highlight_after={})
            for junction_pair in junction_pairs
        ),
    )


def test_primary_unit_prefers_the_shortest_matching_substring_unit():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    short = _stub_enriched("u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1))
    longer = _stub_enriched("u-0003", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(0, 1))
    counts = resolve_primary_units([item, short, longer])
    assert item.secondary_junctions[0].primary_unit == "u-0002"
    assert counts == {
        "units_with_markers": 1,
        "junctions_with_primary_unit": 1,
        "junctions_without_primary_unit": 0,
        "junctions_suppressed_invisible": 0,
    }


def test_primary_unit_rejects_a_substring_candidate_whose_outcome_differs():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    wrong_cell = _stub_enriched("u-0002", (0xE665, 0xE652), ("B", "C-other"), ("y5",), pair=(0, 1))
    matching = _stub_enriched(
        "u-0003", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(0, 1)
    )
    resolve_primary_units([item, wrong_cell, matching])
    assert item.secondary_junctions[0].primary_unit == "u-0003"


def test_primary_unit_requires_the_junction_to_be_the_candidates_primary_pair():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    secondary_there_too = _stub_enriched(
        "u-0002", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(1, 2)
    )
    counts = resolve_primary_units([item, secondary_there_too])
    assert item.secondary_junctions[0].primary_unit is None
    assert counts["junctions_without_primary_unit"] == 1


def test_secondary_junction_with_an_ink_identical_primary_unit_is_suppressed():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    invisible = _stub_enriched(
        "u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1), ink_identical=True
    )
    counts = resolve_primary_units([item, invisible])
    junction = item.secondary_junctions[0]
    assert junction.suppressed is True
    assert junction.primary_unit is None
    assert counts == {
        "units_with_markers": 0,
        "junctions_with_primary_unit": 0,
        "junctions_without_primary_unit": 0,
        "junctions_suppressed_invisible": 1,
    }


def test_secondary_junction_with_a_picture_identical_primary_unit_is_suppressed():
    """A picture-identical primary unit shows no visible change, like an ink-identical one, so it suppresses the marker in the same way."""
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    invisible = _stub_enriched(
        "u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1), picture_identical=True
    )
    counts = resolve_primary_units([item, invisible])
    junction = item.secondary_junctions[0]
    assert junction.suppressed is True
    assert junction.primary_unit is None
    assert counts["junctions_suppressed_invisible"] == 1


def test_secondary_junction_without_any_primary_unit_is_emitted_with_primary_unit_none():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    counts = resolve_primary_units([item])
    junction = item.secondary_junctions[0]
    assert junction.primary_unit is None
    assert junction.suppressed is False
    assert counts == {
        "units_with_markers": 1,
        "junctions_with_primary_unit": 0,
        "junctions_without_primary_unit": 1,
        "junctions_suppressed_invisible": 0,
    }


class _ColumnarPrimaryUnits:
    """A `PrimaryUnitSource` shaped like the packed unit store, which this file must not import: projections held as parallel column lists, a `PrimaryUnitProjection` rebuilt on every lookup, ids ranked as integers, and primary units written back as ordinals into a side map. It shares no code with `_ListSource`, so when the two give the same result, the primary-unit resolution pass is reading only through the protocol."""

    def __init__(self, projections):
        self.ids = [item.unit_id for item in projections]
        self.codepoints = [item.codepoint_values for item in projections]
        self.junctions = [item.junction_pairs for item in projections]
        self.flags = [(item.ink_identical, item.picture_identical) for item in projections]
        self.outcomes = [
            (
                item.pair,
                item.after_spans,
                item.after_cells,
                item.after_junctions,
                item.before_spans,
                item.before_glyphs,
                item.before_junctions,
            )
            for item in projections
        ]
        self.primary_units: dict[int, list[tuple[int | None, bool]]] = {}

    def __len__(self):
        return len(self.ids)

    def windows(self):
        return enumerate(self.codepoints)

    def junction_count(self, ordinal):
        return len(self.junctions[ordinal])

    def projection(self, ordinal):
        pair, after_spans, after_cells, after_junctions, before_spans, before_glyphs, before_junctions = (
            self.outcomes[ordinal]
        )
        ink_identical, picture_identical = self.flags[ordinal]
        return PrimaryUnitProjection(
            unit_id=self.ids[ordinal],
            codepoint_values=self.codepoints[ordinal],
            ink_identical=ink_identical,
            picture_identical=picture_identical,
            pair=pair,
            after_spans=after_spans,
            after_cells=after_cells,
            after_junctions=after_junctions,
            before_spans=before_spans,
            before_glyphs=before_glyphs,
            before_junctions=before_junctions,
            junction_pairs=self.junctions[ordinal],
        )

    def id_word(self, ordinal):
        return int.from_bytes(self.ids[ordinal].encode(), "big")

    def invisible(self, ordinal):
        ink_identical, picture_identical = self.flags[ordinal]
        return ink_identical or picture_identical

    def set_primary_units(self, ordinal, junction_assign):
        if junction_assign:
            self.primary_units[ordinal] = list(junction_assign)

    def named_primary_units(self):
        """Return the side map keyed and valued by unit id, the form the list path returns, so one equality compares both."""
        return {
            self.ids[ordinal]: [
                (None if primary_unit is None else self.ids[primary_unit], suppressed)
                for primary_unit, suppressed in entries
            ]
            for ordinal, entries in self.primary_units.items()
        }


def _primary_unit_fixtures():
    """Return the resolver tests' corpora as projections for the primary-unit resolution pass: one corpus per outcome (a shortest-substring primary unit, a rejected outcome, a candidate that judges the junction as a secondary junction itself, an ink- or picture-identical primary unit, no primary unit), plus a corpus in which no unit has a secondary junction."""
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        junction_pairs=((1, 2),),
    )
    short = _stub_enriched("u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1))
    longer = _stub_enriched("u-0003", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(0, 1))
    wrong_cell = _stub_enriched("u-0004", (0xE665, 0xE652), ("B", "C-other"), ("y5",), pair=(0, 1))
    secondary_there_too = _stub_enriched(
        "u-0005", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(1, 2)
    )
    ink_identical = _stub_enriched(
        "u-0006", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1), ink_identical=True
    )
    picture_identical = _stub_enriched(
        "u-0007", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1), picture_identical=True
    )
    corpora = {
        "shortest_substring_wins": [item, short, longer],
        "differing_outcome_rejected": [item, wrong_cell, longer],
        "junction_must_be_the_candidates_primary": [item, secondary_there_too],
        "ink_identical_primary_unit_suppresses": [item, ink_identical],
        "picture_identical_primary_unit_suppresses": [item, picture_identical],
        "no_primary_unit_at_all": [item],
        "every_unit_without_secondary_junctions": [short, longer, wrong_cell],
    }
    return {
        name: [primary_unit_projection(enriched) for enriched in enriched_units]
        for name, enriched_units in corpora.items()
    }


@pytest.mark.parametrize("name", sorted(_primary_unit_fixtures()))
def test_a_columnar_source_and_the_list_adapter_resolve_the_same_primary_units(name):
    """A source that rebuilds each projection from columns and a plain list of projections must give the same assignments and the same secondary-junction counts. Otherwise the packed store would ship different primary units from the ones the tests above check."""
    projections = _primary_unit_fixtures()[name]
    expected, expected_counts = resolve_primary_unit_assignments(projections)
    columnar = _ColumnarPrimaryUnits(projections)
    assignments, counts = resolve_primary_unit_assignments(columnar)
    assert columnar.named_primary_units() == expected
    assert counts == expected_counts
    assert assignments == {}, "a source that keeps its own primary_units is not handed a second copy of them"


def test_the_list_adapter_hands_back_the_assignment_dict_its_readers_index():
    """`apply_primary_unit_assignments` indexes the list path's result by unit id and reads (primary unit id, suppressed) pairs from it, so the list adapter must return ids instead of the ordinals the primary-unit resolution pass works with."""
    assignments, _ = resolve_primary_unit_assignments(_primary_unit_fixtures()["shortest_substring_wins"])
    assert assignments == {"u-0001": [("u-0002", False)]}


def test_a_projection_without_secondary_junctions_gets_no_assignment_entry():
    """A unit with no secondary junction gets no entry in the assignments dict, because every reader treats a missing entry the same as an empty one. `apply_primary_unit_assignments` must handle the missing entry, which `resolve_primary_units` exercises here."""
    projections = _primary_unit_fixtures()["every_unit_without_secondary_junctions"]
    assignments, counts = resolve_primary_unit_assignments(projections)
    assert assignments == {}
    assert counts == {
        "units_with_markers": 0,
        "junctions_with_primary_unit": 0,
        "junctions_without_primary_unit": 0,
        "junctions_suppressed_invisible": 0,
    }
    without_junctions = _stub_enriched("u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1))
    assert resolve_primary_units([without_junctions])["units_with_markers"] == 0
    assert without_junctions.secondary_junctions == ()


def test_a_unit_ids_string_order_is_the_integer_order_of_the_word_it_encodes():
    """The primary-unit resolution pass breaks a tie between primary units on `id_word`, so that integer order must match the order of the `unit_id` strings, or a tie could resolve to a different primary unit. `unit_cache.base58_64` writes a fixed `ID_SYMBOLS` symbols from an alphabet in ascending ASCII order, so the base58 form of a 64-bit word sorts as the word does. With the constant `u-` prefix and that fixed width, the whole id string also sorts as its bytes read as a big-endian integer, which is the value `_ListSource.id_word` returns."""
    draws = random.Random(299)
    words = sorted({0, 1, 2**63, 2**64 - 1} | {draws.getrandbits(64) for _ in range(200)})
    ids = [unit_id_for(f"{word:016x}") for word in words]
    assert ids == sorted(ids)
    assert len(set(len(unit_id) for unit_id in ids)) == 1
    assert [int.from_bytes(unit_id.encode(), "big") for unit_id in ids] == sorted(
        int.from_bytes(unit_id.encode(), "big") for unit_id in ids
    )


def test_enrich_emits_secondary_junctions_with_primary_style_rects(enricher, units_by_key):
    # ·May·No·No: both junctions are ink-visible, so the trailing ·No·No junction gets a marker in addition to the primary ·May·No pair.
    unit = units_by_key[("E665:E666:E666", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.pair == (0, 1)
    assert len(enriched.secondary_junctions) == 1
    junction = enriched.secondary_junctions[0]
    assert junction.pair == (1, 2)
    for rect in (junction.highlight_before, junction.highlight_after):
        assert set(rect) == {"x_min", "x_max", "advance_total"}
        assert 0 <= rect["x_min"] <= rect["x_max"] <= rect["advance_total"]
    assert junction.highlight_after["x_min"] > enriched.highlight_after["x_min"]


def test_subset_tables_iterate():
    """`iter_rows` reads a subset table's windows: the windows it yields over the bundle's default slice include every window the bundle's audit names. A subset table has a row for every window the configuration renders, divergent or not, so the slice is a superset of the audit's windows. The test checks containment instead of a row count, because a row count would describe this bundle and not the reader."""
    windows = {
        row.split("\t")[1] for row in (MINI / "audit.tsv").read_text(encoding="utf-8").splitlines()[1:]
    }
    yielded = {
        ":".join(f"{value:04X}" for value in row.codepoints)
        for row in iter_rows(MINI / "baseline-default.subset.tsv.gz")
    }
    assert windows <= yielded
