"""Unit tests for the §13.1 validation suite (rebuild/BASELINE-PLAN.md §7).

Every shaping outcome asserted here is either corpus-pinned or was verified against the current built Senior Sans font when this suite was written; the tests record current behavior, they do not assert what it should be.

Run with: uv run pytest rebuild/ -n auto --dist worksteal
"""

from __future__ import annotations

import gzip
import io
import itertools
import sys
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation.classify import SeamClassifier
from validation.pins import PinRun, ReplayReport, check_pin, collect_pin_runs
from validation.rowmodel import (
    ALPHABET,
    CONFIGS,
    Row,
    config_token_for_features,
    header_config_token,
    iter_rows,
    read_header,
    row_sort_key,
)
from validation.shaping import SENIOR_FONT, Shaper, row_for

QS = {
    "Pea": "",
    "Bay": "",
    "Tea": "",
    "Day": "",
    "Fee": "",
    "May": "",
    "Utter": "",
    "Foot": "",
}
ZWNJ = "‌"


@pytest.fixture(scope="module")
def shaper() -> Shaper:
    return Shaper(SENIOR_FONT)


@pytest.fixture(scope="module")
def classifier() -> SeamClassifier:
    return SeamClassifier(SENIOR_FONT)


def test_classifier_discovers_four_curs_lookups(classifier: SeamClassifier) -> None:
    assert classifier.heights() == (0, 5, 6, 8)


def test_shaper_name_recovery_survives_harfbuzz_truncation(shaper: Shaper) -> None:
    harfbuzz_name_byte_limit = 63
    tt_font = TTFont(str(SENIOR_FONT), lazy=True)
    long_names = sorted(
        name for name in tt_font.getGlyphOrder() if len(name.encode("utf-8")) > harfbuzz_name_byte_limit
    )
    assert long_names, "the truncation hazard premise no longer holds for this font"
    for name in long_names:
        assert shaper.glyph_name(tt_font.getGlyphID(name)) == name


def test_classifier_corpus_facts(shaper: Shaper, classifier: SeamClassifier) -> None:
    bay_it = row_for(shaper, classifier, QS["Bay"] + "")
    assert bay_it.seams == ("y0",)

    bay_foot = row_for(shaper, classifier, QS["Bay"] + QS["Foot"])
    assert bay_foot.seams == ("break",)

    fee_tea_default = row_for(shaper, classifier, QS["Fee"] + QS["Tea"])
    assert fee_tea_default.seams == ("break",)

    fee_tea_ss03 = row_for(shaper, classifier, QS["Fee"] + QS["Tea"], {"ss03": True})
    assert fee_tea_ss03.seams == ("y5",)


def test_ligature_cluster_alignment(shaper: Shaper, classifier: SeamClassifier) -> None:
    lig = row_for(shaper, classifier, QS["Day"] + QS["Utter"])
    assert lig.glyphs == ("qsDay_qsUtter",)
    assert lig.clusters == (0,)
    assert lig.seams == ("lig",)

    non_lig = row_for(shaper, classifier, QS["May"] + QS["Tea"])
    assert len(non_lig.glyphs) == 2
    assert non_lig.seams == ("break",)


def test_row_tsv_roundtrip(shaper: Shaper, classifier: SeamClassifier) -> None:
    row = row_for(shaper, classifier, QS["May"] + " " + QS["Tea"] + "·")
    assert row.to_tsv().split("\t")[0] == "E665:0020:E652:00B7"
    assert Row.from_tsv(row.to_tsv()) == row

    single = row_for(shaper, classifier, QS["May"])
    assert single.seams == ()
    assert Row.from_tsv(single.to_tsv()) == single


def test_row_sort_key_orders_by_length_then_codepoints() -> None:
    def row(*cps: int) -> Row:
        return Row(tuple(cps), ("x",), (0,), (), ((0, 0, 0),))

    rows = [row(0xE651, 0xE650), row(0xE650, 0xE651), row(0xE67E), row(0x0020)]
    ordered = sorted(rows, key=row_sort_key)
    assert [r.codepoints for r in ordered] == [
        (0x0020,),
        (0xE67E,),
        (0xE650, 0xE651),
        (0xE651, 0xE650),
    ]


def _write_fixture_table(path: Path, rows: list[Row], config_token: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write("# baseline-extract v0-fixture\n")
        fh.write("# git_sha: ae9d08d\n")
        fh.write(f"# config: {config_token}   (feature dict)\n")
        fh.write("# columns: codepoints glyphs clusters seams positions\n")
        for row in sorted(rows, key=row_sort_key):
            fh.write(row.to_tsv() + "\n")


def test_header_parse_and_row_iteration(tmp_path: Path, shaper: Shaper, classifier: SeamClassifier) -> None:
    rows = [row_for(shaper, classifier, QS["May"]), row_for(shaper, classifier, QS["May"] + QS["Tea"])]
    table = tmp_path / "baseline-ss02.tsv.gz"
    _write_fixture_table(table, rows, "ss02")
    header = read_header(table)
    assert header["tool"] == "baseline-extract v0-fixture"
    assert header_config_token(header) == "ss02"
    assert list(iter_rows(table)) == sorted(rows, key=row_sort_key)


def test_config_token_for_features() -> None:
    assert config_token_for_features(None) == "default"
    assert config_token_for_features({}) == "default"
    assert config_token_for_features({"ss03": True, "ss02": True}) == "ss02+ss03"
    assert config_token_for_features({"ss02": True, "ss03": True, "ss05": True}) == "ss02+ss03+ss05"
    assert config_token_for_features({"ss01": True}) is None
    assert config_token_for_features({"ss04": True, "ss05": True}) is None
    assert set(CONFIGS) == {
        "default",
        "ss02",
        "ss03",
        "ss04",
        "ss05",
        "ss06",
        "ss07",
        "ss10",
        "ss02+ss03",
        "ss06+ss07",
        "ss02+ss03+ss05",
        "ss03+ss05",
    }


def _synthetic_pin(expect: str, text: str) -> PinRun:
    from validation.pins import _import_test_shaping

    ts = _import_test_shaping()
    tokens, connections = ts.parse_expect(expect)
    return PinRun(
        source="synthetic",
        expect=expect,
        text=text,
        config_token="default",
        features={},
        tokens=tuple(tokens),
        connections=tuple(connections),
    )


def test_check_pin_passes_on_corpus_grounded_facts(shaper: Shaper, classifier: SeamClassifier) -> None:
    report = ReplayReport()
    check_pin(shaper, classifier, _synthetic_pin("·Day+Utter", QS["Day"] + QS["Utter"]), report)
    check_pin(shaper, classifier, _synthetic_pin("·May | ·Tea", QS["May"] + QS["Tea"]), report)
    assert report.disagreements == []
    assert report.runs_replayed == 2


def test_check_pin_records_disagreement(shaper: Shaper, classifier: SeamClassifier) -> None:
    report = ReplayReport()
    check_pin(shaper, classifier, _synthetic_pin("·May ~x~ ·Tea", QS["May"] + QS["Tea"]), report)
    assert len(report.disagreements) == 1
    assert report.disagreements[0].kind == "pin-live"
    assert "expected y5" in report.disagreements[0].detail


def test_full_corpus_replay_live(shaper: Shaper, classifier: SeamClassifier) -> None:
    report = ReplayReport()
    pins = collect_pin_runs(report)
    assert report.cells_total > 500
    assert len(pins) > 450
    assert {pin.config_token for pin in pins} >= {"default", "ss02", "ss03"}
    for pin in pins:
        check_pin(shaper, classifier, pin, report)
    assert report.disagreements == []


def test_determinism_sample_emit_is_stable() -> None:
    from check_determinism import emit_sample

    first = io.StringIO()
    emit_sample("default", [1], SENIOR_FONT, first)
    second = io.StringIO()
    emit_sample("default", [1], SENIOR_FONT, second)
    assert first.getvalue() == second.getvalue()
    assert len(first.getvalue().splitlines()) == len(ALPHABET) + 2
