"""Manual-pin gate tests: the spec-based trait and exact-glyph semantics must resolve through stance declarations rather than glyph-name substrings, `summarize` must project a report faithfully, and the gate must actually fail on a pin that contradicts the font.

The standing conformance guarantee itself — every corpus pin the migrated alphabet can express replays cleanly, over a gate that really had pins in scope — is `run_m1.main()`'s, which raises on it. Re-running the identical `run_gate` call in a test afterwards would prove nothing the build has not already refused to ship without.
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
        """`summarize` is a pure projection of a report, so a synthetic one proves its shape without a font in sight. That the live gate passes with pins genuinely in scope is run_m1's own check — it raises on a gate that failed *or* replayed nothing — which is a stronger place for it than a test that re-ran the same call afterwards."""
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
        """The gate has to refuse a pin the font contradicts, which needs a font and a spec that agree with each other — not the live ones. The frozen mini bundle is exactly such a pair, and ·Pea·Tea is a window it carries: one of the two contradicting pins about that seam must fail, because they cannot both be true of any font."""
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
