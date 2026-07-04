"""Tests for the review surface's enrichment: the notation map against doc/glyph-names.md, old seams against the baseline subset rows, new seams against a direct settle() call, divergent positions and pair selection on known units, and highlight x-ranges against hand-computed hmtx sums."""

import re
import warnings
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from rebuild.pipeline.conform import features_for_config
from rebuild.pipeline.settle import settle
from rebuild.review.audit import load_workload
from rebuild.review.enrich import (
    LETTERS,
    EnrichedUnit,
    Enricher,
    SecondarySeam,
    letter_display,
    load_spec,
    notation,
    notation_tokens,
    parse_entry_extension,
    resolve_secondary_homes,
    rune_display,
    text_entities,
)
from rebuild.review.ink import kern_neutral
from rebuild.validation.rowmodel import iter_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
M1_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
AFTER_FONT = M1_DIR / "M1.otf"


@pytest.fixture(scope="module")
def spec():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_spec(REPO_ROOT)


@pytest.fixture(scope="module")
def enricher(spec):
    return Enricher(spec, M1_DIR, AFTER_FONT)


@pytest.fixture(scope="module")
def workload():
    return load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))


@pytest.fixture(scope="module")
def units_by_key(workload):
    return {(unit.codepoints, unit.configs[0]): unit for unit in workload.units}


def test_letter_table_matches_glyph_names_doc():
    doc = (REPO_ROOT / "doc" / "glyph-names.md").read_text(encoding="utf-8")
    rows = re.findall(r"\|\s*(·\S+)\s*\|\s*U\+([0-9A-F]{4})\s*\|\s*(qs\w+)\s*\|", doc)
    assert len(rows) == 44
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


def test_position_only_drift_marks_the_boundary_without_a_pair(enricher, units_by_key):
    # A kern-channel-out-of-scope unit: an advance-only one-pixel drift on the boundary-adjacent letter, no cell- or seam-grain divergence. The mark lands on the word break beside the drift (the ◊ZWNJ), and pair stays None so no sample band lights up.
    enriched = enricher.enrich(units_by_key[("E650:E650:200C:E67A", "ss10")])
    assert enriched.pair is None
    assert enriched.diff_positions == ()
    assert enriched.notation_tokens == ("·Pea", "·Pea", "◊ZWNJ", "·Utter")
    assert enriched.pair_codepoints == (2, 2)


def test_parse_entry_extension():
    assert parse_entry_extension(("en-ext-1",)) == 1
    assert parse_entry_extension(("en-con-2", "locked")) == -2
    assert parse_entry_extension(()) == 0


def test_before_seams_agree_with_subset_rows(enricher, workload):
    sample = workload.units[::200]
    checked = 0
    for unit in sample:
        enriched = enricher.enrich(unit)
        row = enricher.subset_row(unit.configs[0], unit.codepoints)
        assert row is not None
        assert enriched.before_glyphs == row.glyphs
        glyph_level = tuple(seam for seam in row.seams if seam != "lig")
        assert enriched.before_seams == glyph_level
        checked += 1
    assert checked > 0


def test_after_seams_agree_with_direct_settle(spec, enricher, workload):
    for unit in workload.units[::250]:
        config = unit.configs[0]
        if config == "ss10":
            continue
        enriched = enricher.enrich(unit)
        settled = settle(spec, list(unit.codepoint_values), features_for_config(config))
        expected = tuple(
            "break" if item.seam is None else f"y{spec.registry.y_of(item.seam)}" for item in settled[:-1]
        )
        assert enriched.after_seams == expected


def test_derived_cells_match_the_audit_for_every_unit(enricher, workload):
    for unit in workload.units:
        enricher.enrich(unit)
    assert enricher.mismatches == []


def test_known_halves_extension_unit(enricher, units_by_key):
    # The round-1 verdict pass deleted qsIt's halves entry-extension record, so the old flagship ·Tea·It unit re-converged; the class's surviving exemplar is the ss03 phenomenon-1b window where ·May composes en-ext-1 + ex-ext-1.
    unit = units_by_key[("E650:E665:E652", "ss03")]
    enriched = enricher.enrich(unit)
    assert enriched.before_glyphs == (
        "qsPea",
        "qsMay.en-y0.ex-y5.ex-ext-1",
        "qsTea.half.en-y5.after-xheight-exit",
    )
    assert enriched.before_seams == ("y0", "y5")
    assert enriched.after_seams == ("y0", "y5")
    assert enriched.after_extensions == (1, 1)
    assert enriched.diff_positions == (0, 1, 2)
    assert enriched.pair == (0, 1)
    assert "glyph_data/runes/qsMay.yaml:policy.extend" in " ".join(enriched.provenance)


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
        "E652:E679",
        ("ligation",),
        "synthetic",
        ("qsTea_qsOy",),
        ("qsTea_qsOy/bar-into-loop/None/None/",),
    )
    unit = Unit(
        codepoints=row.codepoints,
        baseline=row.baseline,
        new=row.new,
        class_id="synthetic",
        rows=(row,),
        configs=("ss03",),
        kinds=("ligation",),
    )
    enriched = enricher.enrich(unit)
    assert len(enriched.after_cells) == 1
    assert enriched.pair is None


def test_highlight_matches_hmtx_sums_on_a_break_only_unit(enricher, units_by_key):
    unit = units_by_key[("E670:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.after_seams == ("break",)
    font = TTFont(str(AFTER_FONT))
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


def test_summary_names_a_join_gain_in_prose(enricher, workload):
    unit = next(item for item in workload.units if item.class_id == "pea-chain-regularized")
    enriched = enricher.enrich(unit)
    assert "joins" in enriched.summary
    assert "·Pea" in enriched.summary
    assert "qs" not in enriched.summary.split("decided by")[0], "letters appear in rune-name notation"


def test_every_sampled_summary_is_one_nonempty_line(enricher, workload):
    for unit in workload.units[::200]:
        enriched = enricher.enrich(unit)
        assert enriched.summary.startswith("New: ")
        assert "\n" not in enriched.summary
        assert "decided by" in enriched.summary or "no policy record" in enriched.summary


def test_explain_text_keeps_header_and_divergent_positions(enricher, units_by_key):
    unit = units_by_key[("E652:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.explain_text.startswith("sequence E652:E670")
    assert "position 1: qsIt" in enriched.explain_text
    assert "position 0: qsTea" not in enriched.explain_text


def _stub_enriched(unit_id, values, cells, seams, pair, *, ink_identical=False, seam_pairs=()):
    """A minimal EnrichedUnit for resolver tests: one codepoint per cell, before glyphs derived from the cell tokens, all before seams break."""
    from rebuild.review.audit import Unit, format_codepoints

    spans = tuple((index, index + 1) for index in range(len(values)))
    return EnrichedUnit(
        unit=Unit(
            codepoints=format_codepoints(values),
            baseline=tuple(f"old-{cell}" for cell in cells),
            new=tuple(cells),
            class_id="synthetic",
            rows=(),
            unit_id=unit_id,
            ink_identical=ink_identical,
        ),
        notation="",
        text_entities="",
        before_glyphs=tuple(f"old-{cell}" for cell in cells),
        before_seams=("break",) * (len(cells) - 1),
        after_cells=tuple(cells),
        after_seams=tuple(seams),
        after_extensions=(),
        diff_positions=(),
        pair=pair,
        highlight_before={},
        highlight_after={},
        boundary_marks=(),
        explain_text="",
        provenance=(),
        report=None,
        after_spans=spans,
        before_spans=spans,
        secondary_seams=tuple(
            SecondarySeam(pair=seam_pair, highlight_before={}, highlight_after={}) for seam_pair in seam_pairs
        ),
    )


def test_secondary_home_prefers_the_shortest_matching_substring_unit():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        seam_pairs=((1, 2),),
    )
    short = _stub_enriched("u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1))
    longer = _stub_enriched("u-0003", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(0, 1))
    census = resolve_secondary_homes([item, short, longer])
    assert item.secondary_seams[0].home == "u-0002"
    assert census == {
        "units_with_markers": 1,
        "seams_homed": 1,
        "seams_homeless": 0,
        "seams_suppressed_invisible": 0,
    }


def test_secondary_home_rejects_a_substring_candidate_whose_outcome_differs():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        seam_pairs=((1, 2),),
    )
    wrong_cell = _stub_enriched("u-0002", (0xE665, 0xE652), ("B", "C-other"), ("y5",), pair=(0, 1))
    matching = _stub_enriched(
        "u-0003", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(0, 1)
    )
    resolve_secondary_homes([item, wrong_cell, matching])
    assert item.secondary_seams[0].home == "u-0003"


def test_secondary_home_requires_the_seam_to_be_the_candidates_primary_pair():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        seam_pairs=((1, 2),),
    )
    secondary_there_too = _stub_enriched(
        "u-0002", (0xE665, 0xE652, 0xE670), ("B", "C", "D"), ("y5", "break"), pair=(1, 2)
    )
    census = resolve_secondary_homes([item, secondary_there_too])
    assert item.secondary_seams[0].home is None
    assert census["seams_homeless"] == 1


def test_secondary_seam_with_an_ink_identical_home_is_suppressed():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        seam_pairs=((1, 2),),
    )
    invisible = _stub_enriched(
        "u-0002", (0xE665, 0xE652), ("B", "C"), ("y5",), pair=(0, 1), ink_identical=True
    )
    census = resolve_secondary_homes([item, invisible])
    seam = item.secondary_seams[0]
    assert seam.suppressed is True
    assert seam.home is None
    assert census == {
        "units_with_markers": 0,
        "seams_homed": 0,
        "seams_homeless": 0,
        "seams_suppressed_invisible": 1,
    }


def test_secondary_seam_without_any_home_is_emitted_with_home_none():
    item = _stub_enriched(
        "u-0001",
        (0xE650, 0xE665, 0xE652, 0xE670),
        ("A", "B", "C", "D"),
        ("y0", "y5", "break"),
        pair=(0, 1),
        seam_pairs=((1, 2),),
    )
    census = resolve_secondary_homes([item])
    seam = item.secondary_seams[0]
    assert seam.home is None
    assert seam.suppressed is False
    assert census == {
        "units_with_markers": 1,
        "seams_homed": 0,
        "seams_homeless": 1,
        "seams_suppressed_invisible": 0,
    }


def test_enrich_emits_secondary_seams_with_primary_style_rects(enricher, workload):
    unit = next(item for item in workload.units if item.codepoints == "E650:E650:E670:E670")
    enriched = enricher.enrich(unit)
    assert enriched.pair == (0, 1)
    assert len(enriched.secondary_seams) == 1
    seam = enriched.secondary_seams[0]
    assert seam.pair == (1, 2)
    for rect in (seam.highlight_before, seam.highlight_after):
        assert set(rect) == {"x_min", "x_max", "advance_total"}
        assert 0 <= rect["x_min"] <= rect["x_max"] <= rect["advance_total"]
    assert seam.highlight_after["x_min"] > enriched.highlight_after["x_min"]


def test_subset_rows_load_for_every_config(enricher, workload):
    configs = {config for unit in workload.units for config in unit.configs}
    for config in configs:
        assert enricher.subset_row(config, "E650:E665") is not None


def test_subset_tables_iterate(enricher):
    rows = list(iter_rows(M1_DIR / "baseline-default.subset.tsv.gz"))
    assert len(rows) == 16104
