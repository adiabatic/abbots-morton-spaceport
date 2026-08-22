"""Tests for the section 6.3a explain CLI: sequence parsing, the Rust-backed per-position candidate table, elimination attribution to file and record, and the rank-comparison line."""

import pytest

from rebuild.pipeline import explain as explain_module
from rebuild.pipeline import fixtures, kernel_exec
from rebuild.pipeline.explain import ExplainReport, PositionReport, explain, explain_many, parse_sequence
from rebuild.pipeline.settle import Engine, settle_traces

SPEC = fixtures.mini_spec()


def test_parse_sequence_accepts_names_hex_and_boundaries():
    assert parse_sequence(SPEC, "qsMay:qsIt:qsMay") == [0xE665, 0xE670, 0xE665]
    assert parse_sequence(SPEC, "E665:0xE670:U+E665") == [0xE665, 0xE670, 0xE665]
    assert parse_sequence(SPEC, "qsIt:zwnj:qsTea") == [0xE670, 0x200C, 0xE652]


def test_report_settles_and_renders_candidates():
    report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsIt:qsMay"), frozenset())
    text = report.render()
    assert "qsMay.loop.ex-y5.ex-ext-1" in text
    assert "qsIt.hapax.en-y5.ex-y0.ex-ext-1" in text
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


def test_cli_prints_the_rust_backed_report(monkeypatch, capsys):
    monkeypatch.setattr(explain_module, "_load_spec", lambda: (SPEC, None))
    explain_module.main(["qsMay:qsIt"])
    output = capsys.readouterr().out
    assert output.startswith("sequence E665:E670")
    assert "glyph_data/runes/qsMay.yaml:policy.refuse[0]" in output


@pytest.mark.parametrize(
    ("sequence", "features"),
    [
        ("qsMay:qsIt", frozenset()),
        ("qsMay:qsTea", frozenset({"ss03"})),
        ("qsIt:zwnj:qsTea", frozenset()),
        ("qsTea:qsOy", frozenset()),
    ],
)
def test_rust_reports_render_byte_identically_to_the_python_oracle(sequence, features):
    codepoints = parse_sequence(SPEC, sequence)
    traces = settle_traces(Engine(SPEC, features), codepoints)
    legacy = ExplainReport(
        spec=SPEC,
        codepoints=tuple(codepoints),
        features=features,
        positions=tuple(
            PositionReport(index, trace.settled.cell.rune, trace) for index, trace in enumerate(traces)
        ),
    )
    assert explain(SPEC, codepoints, features).render() == legacy.render()


def test_explain_many_batches_same_config_sequences_by_position(monkeypatch):
    calls: list[tuple[frozenset[str], int]] = []
    original = kernel_exec.settle_cases

    def recording(spec, cases, features):
        calls.append((features, len(cases)))
        return original(spec, cases, features)

    monkeypatch.setattr(kernel_exec, "settle_cases", recording)
    requests = [
        (parse_sequence(SPEC, "qsMay:qsIt:qsMay"), frozenset()),
        (parse_sequence(SPEC, "qsIt:zwnj:qsTea"), frozenset()),
        (parse_sequence(SPEC, "qsMay:qsTea"), frozenset({"ss03"})),
    ]
    reports = explain_many(SPEC, requests)
    assert [report.codepoints for report in reports] == [tuple(codepoints) for codepoints, _ in requests]
    assert calls == [
        (frozenset(), 2),
        (frozenset({"ss03"}), 1),
        (frozenset(), 1),
        (frozenset({"ss03"}), 1),
        (frozenset(), 2),
    ]
