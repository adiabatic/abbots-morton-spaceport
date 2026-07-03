"""Manual-pin gate tests: the corpus pins whose text the migrated alphabet can express must all replay cleanly against the built M1 artifact (the standing conformance guarantee, mirrored as a hard gate in run_m1.main()), the spec-based trait and exact-glyph semantics must resolve through stance declarations rather than glyph-name substrings, and the gate must actually fail on a pin that contradicts the font."""

from pathlib import Path

import pytest

from rebuild.pipeline import manual_pins
from rebuild.pipeline.geometry import isolated_cell
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.validation.classify import SeamClassifier
from rebuild.validation.pins import PinRun, _import_test_shaping
from rebuild.validation.shaping import Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent
M1_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


@pytest.fixture(scope="module")
def gate_report(spec):
    return manual_pins.run_gate(M1_FONT, spec)


class TestGate:
    def test_gate_has_scope(self, gate_report):
        assert gate_report.pins_in_scope > 0
        assert gate_report.replayed == gate_report.pins_in_scope

    def test_no_disagreements(self, gate_report):
        details = "\n".join(
            f"{d.source} [{d.config}] {d.expect!r}: {d.detail}" for d in gate_report.disagreements
        )
        assert gate_report.passed, f"{len(gate_report.disagreements)} Manual-pin disagreements:\n{details}"

    def test_summary_shape(self, gate_report):
        summary = manual_pins.summarize(gate_report)
        assert summary["pass"] == gate_report.passed
        assert summary["pins_in_scope"] == gate_report.pins_in_scope
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
    def test_contradicting_pin_fails(self, spec):
        ts = _import_test_shaping()
        shaper = Shaper(M1_FONT)
        classifier = SeamClassifier(M1_FONT)
        text = "\ue670\ue666"
        for expect in ("·It | ·No", "·It ~b~ ·No"):
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
        pytest.fail("neither a break pin nor a baseline-join pin failed for ·It·No — the gate has no teeth")
