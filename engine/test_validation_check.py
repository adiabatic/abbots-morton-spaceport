"""Tests for the `data-expect` grammar, corpus and checker (`engine/validation/expect.py`, `corpus.py`, `check.py`): the collector's runs, the glyph-name parser against `settle.cell_label` and the raw glyphs' names over the live spec, the modifier vocabulary over the mini fixture's font with a synthetic sidecar, pending pins, a feature the font lacks, and the any-of check.

The checker is a leaf and imports nothing from engine/pipeline; these tests may import both, so the parser is held to the labels the build mints.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.pipeline import emit_gsub, surface
from engine.pipeline.model import CellId, cosmetic_copy_name, locked_glyph_name, ss10_copy_name
from engine.pipeline.settle import cell_label
from engine.pipeline.spec_load import load_default_spec
from engine.validation import check, corpus, expect

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_FONT = REPO_ROOT / "engine" / "review" / "fixtures" / "mini" / "Senior-Regular.otf"
ADJUSTMENTS = (
    (),
    ("en-ext-1",),
    ("locked",),
    ("ex-con-2", "locked", "ex-ext-1"),
    ("ex-bind-padded-left-1",),
    ("en-bind-low-bend", "en-con-1"),
)


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        (
            'A<span class="force-junior">B</span>C',
            [
                {"font": "senior", "text": "A", "features": {"ss10": True}},
                {"font": "junior", "text": "B", "features": {"ss10": True}},
                {"font": "senior", "text": "C", "features": {"ss10": True}},
            ],
        ),
        (
            'A<span data-stylistic-set="02">B</span>C',
            [
                {"font": "senior", "text": "A", "features": {"ss10": True}},
                {"font": "senior", "text": "B", "features": {"ss10": True, "ss02": True}},
                {"font": "senior", "text": "C", "features": {"ss10": True}},
            ],
        ),
        (
            'A<span class="force-junior" data-stylistic-set="02">B</span>C',
            [
                {"font": "senior", "text": "A", "features": {"ss10": True}},
                {"font": "junior", "text": "B", "features": {"ss10": True, "ss02": True}},
                {"font": "senior", "text": "C", "features": {"ss10": True}},
            ],
        ),
    ],
)
def test_collector_keeps_enclosing_stylistic_set_on_nested_runs(inner: str, expected: list) -> None:
    collector = corpus.DataExpectCollector()
    collector.feed(f'<span data-expect="x"><span data-stylistic-set="10">{inner}</span></span>')
    collector.close()
    assert [cell.runs for cell in collector.cells] == [expected]


def test_a_pin_carries_its_pending_reason_and_its_own_stylistic_set() -> None:
    html = '<td data-expect="·May | ·Tea" data-stylistic-set="03" data-expect-pending="why">\ue665\ue652</td><dd data-expect="">x</dd>'
    (pin,) = corpus.collect_pins("page.html", html)
    assert (pin.source, pin.pending, pin.features) == ("page.html:1", "why", {"ss03": True})
    (run,) = corpus.collect_pin_runs(pin)
    assert run.get("features") == {"ss03": True}
    assert [token["base"] for token in run["tokens"]] == ["qsMay", "qsTea"]


def test_the_grammar_maps_escapes_through_the_glyph_name_map() -> None:
    tokens, connections = expect.parse_expect("·Way.half | \\= ~x~ ◊ZWNJ")
    assert [token["base"] for token in tokens] == ["qsWay", "equal", "space"]
    assert [connection["kind"] for connection in connections] == ["break", "height"]
    assert expect.CHAR_TO_GLYPH["\ue650"] == "qsPea"


class TestTheGlyphNameParser:
    def test_every_cell_label_parses_back_to_its_cell(self, spec):
        features = surface._all_features(spec)
        checked = 0
        for rune in spec.runes:
            for cell in surface.enumerate_cells(spec, rune, features):
                for adjustments in ADJUSTMENTS:
                    labeled = CellId(cell.rune, cell.stance, cell.entry, cell.exit, adjustments)
                    name = check.parse_glyph_name(cell_label(spec, labeled))
                    assert name.base == rune
                    assert name.stance == cell.stance
                    assert name.entry == (None if cell.entry is None else spec.registry.y_of(cell.entry))
                    assert name.exit == (None if cell.exit is None else spec.registry.y_of(cell.exit))
                    assert name.adjustments == adjustments
                    assert name.locked == ("locked" in adjustments)
                    assert name.features == ()
                    checked += 1
        assert checked > 1000

    def test_raw_glyphs_and_their_copies_parse_as_raw(self, spec):
        for rune in spec.runes:
            for name, locked in (
                (rune, False),
                (locked_glyph_name(rune), True),
                (ss10_copy_name(rune), False),
            ):
                parsed = check.parse_glyph_name(name)
                assert (parsed.base, parsed.stance, parsed.locked) == (rune, None, locked), name
            for marker, active in emit_gsub.marker_states(rune, ("ss03", "ss05")).items():
                for name, locked in ((marker, False), (locked_glyph_name(marker), True)):
                    parsed = check.parse_glyph_name(name)
                    assert (parsed.base, parsed.stance, parsed.locked) == (rune, None, locked), name
                    assert set(parsed.features) == set(active)

    def test_a_cosmetic_copy_parses_as_its_cell_with_the_set(self):
        parsed = check.parse_glyph_name(cosmetic_copy_name("qsOwe.sole.locked", "ss06"))
        assert (parsed.base, parsed.stance, parsed.adjustments, parsed.features, parsed.locked) == (
            "qsOwe",
            "sole",
            ("locked",),
            ("ss06",),
            True,
        )

    def test_a_glyph_outside_the_runes_is_its_name_up_to_the_first_dot(self):
        assert check.parse_glyph_name("periodcentered.lowered").base == "periodcentered"
        assert not check.parse_glyph_name("uni200C").is_rune


@pytest.fixture(scope="module")
def mini_font(tmp_path_factory) -> check.CheckFont:
    sidecar = tmp_path_factory.mktemp("facts") / "mini.glyph-facts.json"
    sidecar.write_text(
        json.dumps(
            {
                "format": check.GLYPH_FACTS_FORMAT,
                "glyphs": {
                    "qsPea": {"isolated": True},
                    "qsPea.full": {"isolated": True},
                    "qsPea.half.ex-y5": {"traits": ["half"]},
                    "qsTea.full.ex-y0": {"bitmap": "reaches-way-back"},
                },
            }
        )
    )
    return check.CheckFont(MINI_FONT, glyph_facts=sidecar)


class TestTheModifiers:
    @pytest.mark.parametrize(
        ("glyph", "modifier", "holds"),
        [
            ("qsPea.half.ex-y5", "half", True),
            ("qsPea.full", "half", False),
            ("qsPea.half.ex-y5", "exit", True),
            ("qsPea.full", "exit", False),
            ("qsPea.full.en-y5", "entry", True),
            ("qsPea.half.ex-y5", "ex-y5", True),
            ("qsPea.half.ex-y5", "ex-y0", False),
            ("qsTea.full.en-y0.en-ext-1", "en-y0", True),
            ("qsTea.full.en-y0.en-ext-1", "extended", True),
            ("qsTea.full.en-y0.en-ext-1", "en-ext-1", True),
            ("qsTea.full.en-y0.en-ext-1", "ex-ext-1", False),
            ("qsTea.full.ex-y0", "extended", False),
            ("qsTea.full.ex-y0.locked", "locked", True),
            ("qsTea.noentry", "locked", True),
            ("qsTea.full.ex-y0", "locked", False),
            ("qsTea.full.ex-y0", "reaches-way-back", True),
            ("qsTea.full", "reaches-way-back", False),
        ],
    )
    def test_each_modifier_reads_the_name_or_the_sidecar(self, mini_font, glyph, modifier, holds):
        assert check.modifier_holds(mini_font, check.parse_glyph_name(glyph), modifier) is holds

    @pytest.mark.parametrize("modifier", ["noentry", "baseline", "wide", "ex-dips"])
    def test_a_modifier_outside_the_vocabulary_is_an_error(self, mini_font, modifier):
        with pytest.raises(check.UnknownModifier, match=f"unknown modifier .{modifier}"):
            check.modifier_holds(mini_font, check.parse_glyph_name("qsPea.full"), modifier)

    def test_a_trait_needs_a_sidecar(self):
        with pytest.raises(check.UnknownModifier, match="sidecar"):
            check.modifier_holds(check.CheckFont(MINI_FONT), check.parse_glyph_name("qsPea.full"), "half")


def _pin(expect_string: str, text: str, pending: str | None = None, features=None) -> corpus.Pin:
    return corpus.Pin(
        page="synthetic",
        line=1,
        text=text,
        expect=expect_string,
        features=features or {},
        runs=({"font": "senior", "text": text},),
        pending=pending,
    )


class TestThePinOutcome:
    def test_a_pass_a_failure_and_their_pending_forms(self, mini_font):
        fonts = {"senior": mini_font}
        assert check.check_pin(fonts, _pin("·Tea ~b~ ·Utter", "\ue652\ue67a")).status == "pass"
        assert check.check_pin(fonts, _pin("·Tea | ·Utter", "\ue652\ue67a")).status == "fail"
        assert (
            check.check_pin(fonts, _pin("·Tea | ·Utter", "\ue652\ue67a", pending="why")).status == "pending"
        )
        assert (
            check.check_pin(fonts, _pin("·Tea ~b~ ·Utter", "\ue652\ue67a", pending="why")).status
            == "pending-passes"
        )

    def test_an_unknown_modifier_is_invalid_even_on_a_pending_pin(self, mini_font):
        outcome = check.check_pin(
            {"senior": mini_font}, _pin("·Tea.noentry | ·Utter", "\ue652\ue67a", pending="why")
        )
        assert outcome.status == "invalid"
        assert "unknown modifier .noentry" in outcome.message()

    def test_a_feature_the_font_lacks_fails_the_run(self, mini_font):
        outcome = check.check_pin(
            {"senior": mini_font}, _pin("·Tea ~b~ ·Utter", "\ue652\ue67a", features={"ss02": True})
        )
        assert outcome.status == "fail"
        assert "has no ss02" in outcome.message()

    def test_a_run_of_a_font_the_caller_has_not_is_skipped_with_its_reason(self, mini_font):
        pin = corpus.Pin(
            page="synthetic",
            line=1,
            text="\ue652\ue67a",
            expect="·Tea ~b~ ·Utter",
            features={},
            runs=({"font": "junior", "text": "\ue652\ue67a"},),
            pending=None,
        )
        outcome = check.check_pin({"senior": mini_font, "junior": None}, pin, {"junior": "no Junior here"})
        assert outcome.checked_runs == 0
        assert outcome.skipped_runs == [f"junior run {pin.text!r}: no Junior here"]

    def test_the_exact_glyph_reads_the_isolated_flag(self, mini_font):
        fonts = {"senior": mini_font}
        assert check.check_pin(fonts, _pin("·Pea.∅", "\ue650")).status == "pass"
        assert check.check_pin(fonts, _pin("·Tea.∅", "\ue652")).status == "fail"


def test_the_any_of_check_passes_on_any_candidate_and_lists_every_attempt(mini_font):
    text = "\ue652\ue67a"
    check.assert_expect_any(mini_font, text, ["·Tea | ·Utter", "·Tea ~b~ ·Utter"])
    with pytest.raises(AssertionError, match="No candidate data-expect matched") as raised:
        check.assert_expect_any(mini_font, text, ["·Tea | ·Utter", "·Tea ~x~ ·Utter"])
    assert "'·Tea | ·Utter'" in str(raised.value) and "'·Tea ~x~ ·Utter'" in str(raised.value)
