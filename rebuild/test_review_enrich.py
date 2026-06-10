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
    Enricher,
    letter_display,
    load_spec,
    notation,
    parse_entry_extension,
    text_entities,
)
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
    unit = units_by_key[("E652:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.before_glyphs == ("qsTea.half.ex-y5", "qsIt.en-y5.ex-y0")
    assert enriched.before_seams == ("y5",)
    assert enriched.after_seams == ("y5",)
    assert enriched.after_extensions == (1,)
    assert enriched.diff_positions == (1,)
    assert enriched.pair == (0, 1)
    assert "glyph_data/runes/qsIt.yaml:policy.extend" in " ".join(enriched.provenance)


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
    row = enricher.subset_row("default", unit.codepoints)
    assert enriched.highlight_before["advance_total"] == sum(adv for _x, _y, adv in row.positions)


def test_highlight_covers_the_pair_not_the_run_when_pair_is_interior(enricher, units_by_key):
    unit = units_by_key[("E650:200C:E650:E665", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.pair == (2, 3)
    assert enriched.highlight_after["x_min"] > 0
    assert enriched.highlight_after["x_max"] == enriched.highlight_after["advance_total"]


def test_explain_text_keeps_header_and_divergent_positions(enricher, units_by_key):
    unit = units_by_key[("E652:E670", "default")]
    enriched = enricher.enrich(unit)
    assert enriched.explain_text.startswith("sequence E652:E670")
    assert "position 1: qsIt" in enriched.explain_text
    assert "position 0: qsTea" not in enriched.explain_text


def test_subset_rows_load_for_every_config(enricher, workload):
    configs = {config for unit in workload.units for config in unit.configs}
    for config in configs:
        assert enricher.subset_row(config, "E650:E665") is not None


def test_subset_tables_iterate(enricher):
    rows = list(iter_rows(M1_DIR / "baseline-default.subset.tsv.gz"))
    assert len(rows) == 4680
