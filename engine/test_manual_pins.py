"""Tests for the pin gate in `engine/pipeline/manual_pins.py`: `summarize` projects a report, the alphabet switch's scoping counts the letters a narrowed alphabet leaves out, and the checker the gate runs fails a pin that contradicts the font.

The build checks the live gate itself: `run_build_gates.main()` exits when the gate fails, has no pins in scope, or does not check every pin in scope (`run_build_gates.manual_pin_gate_failure`), so no test repeats that `run_gate` call. `engine/test_validation_check.py` tests the checker's reading of glyph names, modifiers and pending pins.
"""

from pathlib import Path

import pytest

from engine.pipeline import manual_pins
from engine.pipeline.spec_load import load_default_spec
from engine.validation import check, corpus

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_FONT = REPO_ROOT / "engine" / "review" / "fixtures" / "mini" / "Senior-Regular.otf"


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


def _pin(expect: str, text: str, pending: str | None = None) -> corpus.Pin:
    return corpus.Pin(
        page="synthetic",
        line=1,
        text=text,
        expect=expect,
        features={},
        runs=({"font": "senior", "text": text},),
        pending=pending,
    )


class TestGate:
    def test_summary_shape(self):
        """`summarize` depends only on the report, so a synthetic report tests its shape without a font. The module docstring says where the live gate is checked."""
        report = manual_pins.ManualPinReport()
        report.pins_in_scope = 3
        report.replayed = 3
        report.pages["site/the-manual.html"] = manual_pins.PageCounts(pins=4, checked=3, passed=2, pending=1)
        report.pending.append("site/the-manual.html:1 '·May': pending (why)")
        report.skipped["Junior runs"] = 2
        report.blocked_by[0xE665] = 2
        report.sole_blocker[0xE665] = 1
        summary = manual_pins.summarize(report)
        assert summary["pass"] == report.passed is True
        assert summary["pins_in_scope"] == 3
        assert summary["pages"]["site/the-manual.html"]["pending"] == 1
        assert summary["pending"] == report.pending
        assert summary["skipped"] == {"Junior runs": 2}
        assert all("letter" in entry and "blocks" in entry for entry in summary["top_blocking_letters"])

    def test_a_pending_pin_does_not_fail_the_gate_and_a_disagreement_does(self):
        report = manual_pins.ManualPinReport()
        report.pending.append("pending")
        assert report.passed
        report.disagreements.append("failed")
        assert not report.passed


class TestScope:
    def test_the_missing_letters_are_the_quikscript_letters_the_alphabet_lacks(self, spec):
        alphabet = manual_pins.spec_codepoints(spec)
        assert manual_pins.missing_letters(_pin("·May \\- ·Tea", "\ue665-\ue652"), alphabet) == set()
        narrowed = alphabet - {0xE665}
        assert manual_pins.missing_letters(_pin("·May | ·Tea", "\ue665\ue652"), narrowed) == {0xE665}

    def test_spec_codepoints_track_the_spec(self, spec):
        alphabet = manual_pins.spec_codepoints(spec)
        assert {0x0020, 0x00B7, 0x200C} < alphabet
        for rune in spec.runes.values():
            if rune.codepoint is not None:
                assert rune.codepoint in alphabet


class TestTheGateCanFail:
    def test_contradicting_pin_fails(self):
        """The gate must fail a pin the font contradicts. The frozen mini bundle's font has the cursive lookups the checker reads. The test checks two pins for ·Pea·Tea, a break (`·Pea | ·Tea`) and an x-height join (`·Pea ~x~ ·Tea`). They cannot both hold, so at least one must fail, and a pending mark turns that failure into an expected one."""
        fonts = {"senior": check.CheckFont(MINI_FONT), "junior": None}
        text = "\ue650\ue652"
        statuses = {
            expect: check.check_pin(fonts, _pin(expect, text)).status
            for expect in ("·Pea | ·Tea", "·Pea ~x~ ·Tea")
        }
        assert "fail" in statuses.values(), statuses
        failing = next(expect for expect, status in statuses.items() if status == "fail")
        assert check.check_pin(fonts, _pin(failing, text, pending="why")).status == "pending"
