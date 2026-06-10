"""Tests for the section 6.3a explain CLI: sequence parsing, the per-position candidate table, elimination attribution to file and record, and the rank-comparison line."""

from rebuild.pipeline import fixtures
from rebuild.pipeline.explain import explain, parse_sequence

SPEC = fixtures.mini_spec()


def test_parse_sequence_accepts_names_hex_and_boundaries():
    assert parse_sequence(SPEC, "qsMay:qsIt:qsMay") == [0xE665, 0xE670, 0xE665]
    assert parse_sequence(SPEC, "E665:0xE670:U+E665") == [0xE665, 0xE670, 0xE665]
    assert parse_sequence(SPEC, "qsIt:zwnj:qsTea") == [0xE670, 0x200C, 0xE652]


def test_report_settles_and_renders_candidates():
    report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsIt:qsMay"), frozenset())
    text = report.render()
    assert "qsMay.loop.ex-y5.ex-ext-1" in text
    assert "qsIt.bar.en-y5.ex-y0.ex-ext-1" in text
    assert "join-count" in text
    assert "decided by:" in text


def test_eliminations_are_attributed_to_records():
    # qsMay's grounded baseline exit toward qsIt dies to the authored refusal; the report names the record's file and key path.
    report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsIt"), frozenset())
    text = report.render()
    assert "glyph_data/runes/qsMay.yaml:policy.refuse[0]" in text
    assert "(refuse)" in text


def test_feature_configuration_changes_the_outcome():
    default_report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsTea"), frozenset())
    ss03_report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsTea"), frozenset({"ss03"}))
    assert "qsMay.loop.ex-bind-pulled-back" in default_report.render()
    assert "qsTea.half.en-y5" in ss03_report.render()
    assert "config ss03" in ss03_report.render()


def test_boundary_positions_render():
    report = explain(SPEC, parse_sequence(SPEC, "qsIt:zwnj:qsTea"), frozenset())
    text = report.render()
    assert "boundary token" in text
    assert "qsTea.full.locked" in text
