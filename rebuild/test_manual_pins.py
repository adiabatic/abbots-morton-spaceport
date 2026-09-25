"""Tests for the Manual-pin gate in `rebuild/pipeline/manual_pins.py`: trait and exact-glyph matching resolve through stance declarations, not glyph-name substrings; `summarize` projects a report correctly; and the gate fails on a pin that contradicts the font.

The build checks the live gate itself: `run_m1.main()` exits when the gate fails, has no pins in scope, or does not replay every pin in scope (`run_m1.manual_pin_gate_failure`), so no test repeats that `run_gate` call.
"""

from pathlib import Path

import pytest

from rebuild.pipeline import manual_pins
from rebuild.pipeline.geometry import isolated_cell
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.review import enrich
from rebuild.validation.classify import SeamClassifier
from rebuild.validation.pins import PinRun, _import_test_shaping
from rebuild.validation.shaping import Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_FONT = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf"


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


class TestGate:
    def test_summary_shape(self):
        """`summarize` depends only on the report, so a synthetic report tests its shape without a font. The module docstring says where the live gate is checked."""
        report = manual_pins.ManualPinReport()
        report.pins_in_scope = 3
        report.replayed = 3
        report.blocked_by[0xE665] = 2
        report.sole_blocker[0xE665] = 1
        summary = manual_pins.summarize(report)
        assert summary["pass"] == report.passed
        assert summary["pins_in_scope"] == 3
        assert all("letter" in entry and "blocks" in entry for entry in summary["top_blocking_letters"])


class TestSemantics:
    def test_traits_resolve_through_stance_declarations(self, spec):
        for rune_name, rune in spec.runes.items():
            for stance_name, stance in rune.stances.items():
                label = f"{rune_name}.{stance_name}.en-y0.ex-y5"
                assert manual_pins._stance_traits(spec, label) == frozenset(stance.traits)

    def test_alt_trait_visible_on_qsNo(self, spec):
        alt_stances = [name for name, stance in spec.runes["qsNo"].stances.items() if "alt" in stance.traits]
        assert alt_stances
        for name in alt_stances:
            assert "alt" in manual_pins._stance_traits(spec, f"qsNo.{name}.en-y0")

    def test_bare_and_boundary_glyphs_carry_no_traits(self, spec):
        assert manual_pins._stance_traits(spec, "qsMay") == frozenset()
        assert manual_pins._stance_traits(spec, "space") == frozenset()
        assert manual_pins._stance_traits(spec, "uni200C") == frozenset()

    def test_exact_glyph_accepts_bare_and_isolated_cell(self, spec):
        names = manual_pins._exact_glyph_names(spec, "qsMay")
        assert "qsMay" in names
        assert cell_label(spec, isolated_cell(spec, "qsMay")) in names

    def test_migrated_alphabet_tracks_spec(self, spec):
        alphabet = manual_pins.migrated_alphabet(spec)
        assert {0x0020, 0x00B7, 0x200C} < alphabet
        for rune in spec.runes.values():
            if rune.codepoint is not None:
                assert rune.codepoint in alphabet


class TestTeeth:
    def test_contradicting_pin_fails(self, mini_bundle):
        """The gate must fail a pin the font contradicts. That needs a font and a spec that match each other, which the frozen mini bundle provides. The test checks two pins for ·Pea·Tea, a break (`·Pea | ·Tea`) and an x-height join (`·Pea ~x~ ·Tea`). They cannot both hold, so at least one must fail."""
        spec = enrich.load_spec(mini_bundle.spec_root)
        ts = _import_test_shaping()
        shaper = Shaper(MINI_FONT)
        classifier = SeamClassifier(MINI_FONT)
        text = "\ue650\ue652"
        for expect in ("·Pea | ·Tea", "·Pea ~x~ ·Tea"):
            tokens, connections = ts.parse_expect(expect)
            pin = PinRun(
                source="synthetic",
                expect=expect,
                text=text,
                config_token="default",
                features={},
                tokens=tuple(tokens),
                connections=tuple(connections),
            )
            report = manual_pins.ManualPinReport()
            manual_pins._check_pin(spec, shaper, classifier, pin, report)
            if report.disagreements:
                return
        pytest.fail(
            "neither a break pin nor an x-height-join pin failed for ·Pea·Tea — the gate has no teeth"
        )
