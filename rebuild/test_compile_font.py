"""compile_font integration test: the prototype's verified senior_fea recipe end to end on a four-glyph mini-font, with the budget gate artifacts checked. Read-only with respect to the old pipeline."""

import json

import pytest

from rebuild.pipeline import compile_font, emit_gpos, geometry
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    spec = mini_spec()
    cells = [
        CellId("qsIt", "bar", None, None, ()),
        CellId("qsIt", "bar", None, "baseline", ()),
        CellId("qsMay", "loop", None, "x-height", ()),
        CellId("qsMay", "loop", "baseline", "x-height", ()),
        CellId("qsPea", "full", "y6", None, ()),
    ]
    glyphs = {cell: geometry.realize(spec, CellPlan(cell=cell)) for cell in cells}
    tea_half = CellId("qsTea", "half", None, "x-height", ())
    glyphs[tea_half] = geometry.realize(spec, CellPlan(cell=tea_half, entry_curs_only=(0, 8)))
    names = {cell: record.name for cell, record in glyphs.items()}
    fea = (
        "lookup t_settle {\n"
        f"    sub qsIt' qsMay by {names[cells[1]]};\n"
        f"    sub {names[cells[1]]} qsMay' by {names[cells[3]]};\n"
        "} t_settle;\n"
        "feature calt {\n    lookup t_settle;\n} calt;\n" + emit_gpos.emit_gpos(glyphs, spec=spec)
    )
    out_path = tmp_path_factory.mktemp("m1-font") / "M1Test.otf"
    compile_font.build_mini_font(glyphs, fea, out_path)
    return out_path, names


class TestBuildMiniFont:
    def test_font_and_sidecars_exist(self, built):
        out_path, _names = built
        assert out_path.exists()
        assert out_path.with_suffix(".fea").exists()
        assert (out_path.parent / "budget.json").exists()

    def test_budget_gate_contents(self, built):
        out_path, _names = built
        budget = json.loads((out_path.parent / "budget.json").read_text())
        assert budget["gate"]["failed"] is False
        assert "extension_promotion_yellow_flag" in budget["gate"]
        assert budget["measured"]["lookup_count"] >= 1

    def test_shaping_matches_the_settlement_rules(self, built):
        out_path, names = built
        from rebuild.pipeline.conform import Shaper

        shaper = Shaper(out_path)
        shaped = shaper.shape(chr(0xE670) + chr(0xE665), frozenset())
        got = [glyph["name"] for glyph in shaped]
        assert got == [
            names[CellId("qsIt", "bar", None, "baseline", ())],
            names[CellId("qsMay", "loop", "baseline", "x-height", ())],
        ]

    def test_curs_anchors_close_the_seam(self, built):
        out_path, _names = built
        from rebuild.validation.classify import SeamClassifier

        classifier = SeamClassifier(out_path)
        shaped_names = []
        from rebuild.pipeline.conform import Shaper

        shaper = Shaper(out_path)
        for glyph in shaper.shape(chr(0xE670) + chr(0xE665), frozenset()):
            shaped_names.append(glyph["name"])
        assert classifier.classify(shaped_names[0], shaped_names[1]) == "y0"
