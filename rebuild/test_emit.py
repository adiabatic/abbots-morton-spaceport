"""emit_gsub / emit_gpos tests over the fixture spec with duck-typed decision tables."""

from dataclasses import dataclass

import pytest

from rebuild.pipeline import emit_gpos, emit_gsub, geometry
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan, marker_glyph_name, relevant_marker_features


@dataclass(frozen=True)
class FakeRule:
    input_glyph: str
    backtrack: tuple | None
    look1: tuple | None
    look2: tuple | None
    outcome: str
    joint: bool = False
    provenance: tuple = ()


@dataclass
class FakeDecision:
    rules: list

    def reachable_cells(self):
        return frozenset()


@pytest.fixture(scope="module")
def spec():
    return mini_spec()


@pytest.fixture(scope="module")
def glyphs(spec):
    cells = [
        CellId("qsIt", "hapax", None, None, ()),
        CellId("qsIt", "hapax", None, "baseline", ()),
        CellId("qsIt", "hapax", "x-height", "baseline", ()),
        CellId("qsTea", "full", None, None, ()),
        CellId("qsTea", "half", None, "x-height", ()),
        CellId("qsMay", "loop", None, "x-height", ()),
        CellId("qsMay", "loop", "baseline", "x-height", ()),
        CellId("qsOy", "hapax", None, None, ()),
        CellId("qsTea_qsOy", "hapax", None, "baseline", ()),
        CellId("qsTea", "full", None, None, ("locked",)),
        CellId("qsPea", "full", "y6", None, ()),
    ]
    records = {}
    for cell in cells:
        plan = CellPlan(cell=cell)
        if cell == CellId("qsTea", "half", None, "x-height", ()):
            plan = CellPlan(cell=cell, entry_curs_only=(0, 8))
        records[cell] = geometry.realize(spec, plan)
    return records


def _rules(spec, glyphs):
    names = {cell: record.name for cell, record in glyphs.items()}
    it_ex = names[CellId("qsIt", "hapax", None, "baseline", ())]
    may_en = names[CellId("qsMay", "loop", "baseline", "x-height", ())]
    tea_half = names[CellId("qsTea", "half", None, "x-height", ())]
    return [
        FakeRule("qsIt", None, ("qsMay",), ("uni200C", "space"), it_ex, provenance=("p1",)),
        FakeRule("qsIt", None, ("qsMay",), None, it_ex, provenance=("p1",)),
        FakeRule("qsMay", (it_ex,), None, None, may_en, joint=True, provenance=("p2",)),
        FakeRule("qsTea.ss03", (may_en,), None, None, tea_half, provenance=("p3",)),
    ]


class TestMarkers:
    def test_relevant_features(self, spec):
        assert relevant_marker_features(spec.runes["qsTea"]) == ("ss02", "ss03", "ss05")
        assert relevant_marker_features(spec.runes["qsIt"]) == ("ss04",)
        assert relevant_marker_features(spec.runes["qsMay"]) == ()

    def test_marker_names(self):
        assert marker_glyph_name("qsTea", frozenset()) == "qsTea"
        assert marker_glyph_name("qsTea", {"ss03"}) == "qsTea.ss03"
        assert marker_glyph_name("qsTea", {"ss03", "ss02"}) == "qsTea.ss02_ss03"


class TestEmitGsub:
    def test_stage_order_fixed_by_definition_order(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        fea = plan.fea_text
        order = [
            fea.index("lookup m1_formation {"),
            fea.index("lookup m1_ss02_marker {"),
            fea.index("lookup m1_zwnj {"),
            fea.index("lookup m1_settle {"),
        ]
        assert order == sorted(order)

    def test_formation_is_type_four_over_the_sequence(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        assert "sub qsTea qsOy by qsTea_qsOy;" in plan.fea_text

    def test_composite_marker_staging(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        fea = plan.fea_text
        assert "sub qsTea by qsTea.ss03;" in fea
        assert "sub qsTea.ss02 by qsTea.ss02_ss03;" in fea
        assert "sub qsTea.ss02_ss03 by qsTea.ss02_ss03_ss05;" in fea

    def test_chokepoint_classes(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        fea = plan.fea_text
        assert "sub uni200C @m1_entry_live' by @m1_entry_locked;" in fea
        assert "qsTea_qsOy" not in fea.split("@m1_entry_live = [")[1].split("]")[0]

    def test_subtable_breaks_between_families(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        settle_block = plan.fea_text.split("lookup m1_settle {")[1].split("} m1_settle;")[0]
        assert settle_block.count("subtable;") == 2  # qsIt | qsMay | qsTea.ss03

    def test_provenance_comments_ride_along(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        assert "# joint row | p2" in plan.fea_text

    def test_locked_twin_in_lookahead_raises(self, spec, glyphs):
        bad = [FakeRule("qsIt", None, ("qsTea.noentry",), None, "qsIt", provenance=())]
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(bad)}, glyphs=glyphs)

    def test_unknown_glyph_raises(self, spec, glyphs):
        bad = [FakeRule("qsIt", None, ("qsNotARune",), None, "qsIt", provenance=())]
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(bad)}, glyphs=glyphs)

    def test_fold_conflict_raises(self, spec, glyphs):
        names = {cell: record.name for cell, record in glyphs.items()}
        it_ex = names[CellId("qsIt", "hapax", None, "baseline", ())]
        a = FakeRule("qsIt", None, ("qsMay",), None, it_ex, provenance=())
        b = FakeRule("qsIt", None, ("qsMay",), None, "qsIt", provenance=())
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.emit_gsub(
                spec,
                {frozenset(): FakeDecision([a]), frozenset({"ss03"}): FakeDecision([b])},
                glyphs=glyphs,
            )

    def test_ss10_preempt_defined_before_formation(self, spec, glyphs):
        twins = {"qsIt": "qsIt.ss10", "qsMay": "qsMay.ss10", "qsTea": "qsTea.ss10", "qsOy": "qsOy.ss10"}
        plan = emit_gsub.emit_gsub(
            spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs, ss10_twins=twins
        )
        fea = plan.fea_text
        assert fea.index("lookup m1_ss10_isolated_input {") < fea.index("lookup m1_formation {")
        preempt = fea.split("lookup m1_ss10_isolated_input {")[1].split("} m1_ss10_isolated_input;")[0]
        for raw_name, twin_name in twins.items():
            assert f"sub {raw_name} by {twin_name};" in preempt
        assert "feature ss10 {\n    lookup m1_ss10_isolated_input;\n} ss10;" in fea

    def test_ss10_twins_stay_out_of_the_join_pipeline(self, spec, glyphs):
        twins = {"qsIt": "qsIt.ss10", "qsMay": "qsMay.ss10", "qsTea": "qsTea.ss10", "qsOy": "qsOy.ss10"}
        plan = emit_gsub.emit_gsub(
            spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs, ss10_twins=twins
        )
        fea = plan.fea_text
        assert "m1_ss10_unligate" not in fea
        assert "lookup m1_ss10_isolated {" not in fea
        assert "qsTea_qsOy.ss10" not in fea  # ligature runes never appear in a cmap buffer, so no twin
        formation = fea.split("lookup m1_formation {")[1].split("} m1_formation;")[0]
        assert ".ss10" not in formation
        assert ".ss10" not in fea.split("@m1_entry_live = [")[1].split("]")[0]
        settle_block = fea.split("lookup m1_settle {")[1].split("} m1_settle;")[0]
        assert ".ss10" not in settle_block
        followers = fea.split("@m1_namer_short_followers = [")[1].split("]")[0].split()
        assert "qsIt.ss10" in followers  # the namer dot still lowers before a Short letter under ss10

    def test_namer_dot_stage_targets_short_cells(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        fea = plan.fea_text
        assert "lookup m1_namer_dot_word_start {" in fea
        followers = fea.split("@m1_namer_short_followers = [")[1].split("]")[0].split()
        assert all(name.startswith(("qsIt", "qsOy")) for name in followers)
        lookup_body = fea.split("lookup m1_namer_dot_word_start {")[1].split("}")[0]
        assert lookup_body.index("ignore sub periodcentered' uni200C;") < lookup_body.index(
            "sub periodcentered' @m1_namer_short_followers by periodcentered.lowered;"
        )


class TestEmitGpos:
    def test_four_height_lookups_emitted(self, spec, glyphs):
        curs = emit_gpos.emit_gpos(glyphs, spec=spec)
        for y in (0, 5, 6, 8):
            assert f"lookup m1_cursive_y{y} {{" in curs

    def test_anchor_coordinates_in_the_drawn_frame(self, spec, glyphs):
        curs = emit_gpos.emit_gpos(glyphs, spec=spec)
        record = glyphs[CellId("qsMay", "loop", None, "x-height", ())]
        assert f"pos cursive {record.name} <anchor NULL> <anchor 300 250>;" in curs

    def test_cross_height_cells_get_null_anchors(self, spec, glyphs):
        curs = emit_gpos.emit_gpos(glyphs, spec=spec)
        record = glyphs[CellId("qsIt", "hapax", "x-height", "baseline", ())]
        y0 = curs.split("lookup m1_cursive_y0 {")[1].split("}")[0]
        y5 = curs.split("lookup m1_cursive_y5 {")[1].split("}")[0]
        assert f"pos cursive {record.name} <anchor NULL> <anchor 100 0>;" in y0
        assert f"pos cursive {record.name} <anchor 50 250> <anchor NULL>;" in y5

    def test_entry_curs_only_registers_for_parity(self, spec, glyphs):
        curs = emit_gpos.emit_gpos(glyphs, spec=spec)
        record = glyphs[CellId("qsTea", "half", None, "x-height", ())]
        y8 = curs.split("lookup m1_cursive_y8 {")[1].split("}")[0]
        assert f"pos cursive {record.name} <anchor 50 400> <anchor NULL>;" in y8

    def test_locked_twin_null_null_parity(self, spec, glyphs):
        curs = emit_gpos.emit_gpos(glyphs, spec=spec)
        record = glyphs[CellId("qsTea", "full", None, None, ("locked",))]
        for y in (0, 5, 8):
            block = curs.split(f"lookup m1_cursive_y{y} {{")[1].split("}")[0]
            assert f"pos cursive {record.name} <anchor NULL> <anchor NULL>;" in block


class TestLateFormationGuardLines:
    """The section 5.7 guard's FEA realization over the real loaded rune YAML (the mini fixture spec has no guarded ligature, so its formation lookup keeps the plain type-4 shape asserted above)."""

    @pytest.fixture(scope="class")
    def real_spec(self):
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_default_spec()

    def test_guarded_ligature_moves_to_its_own_contextual_lookup(self, real_spec):
        registry = emit_gsub._ClassRegistry()
        guarded, plain, ignores = emit_gsub._formation_lines(real_spec, registry)
        assert "    sub qsTea qsOy by qsTea_qsOy;" in plain
        assert all("qsDay" not in line for line in plain)
        assert "    ignore sub qsDay' qsUtter' qsLow;" in guarded
        assert "    sub qsDay' qsUtter' by qsDay_qsUtter;" in guarded
        assert guarded.index("    sub qsDay' qsUtter' uni200C by qsDay_qsUtter;") < guarded.index(
            "    ignore sub qsDay' qsUtter' qsLow;"
        )
        assert guarded[-1] == "    sub qsDay' qsUtter' by qsDay_qsUtter;"
        assert "ignore sub qsDay' qsUtter' qsLow;" in ignores

    def test_partial_second_slot_guard_gets_a_two_slot_ignore(self, real_spec):
        registry = emit_gsub._ClassRegistry()
        guarded, _plain, _ignores = emit_gsub._formation_lines(real_spec, registry)
        two_slot = [line for line in guarded if line.startswith("    ignore sub qsDay' qsUtter' qsUtter ")]
        assert len(two_slot) == 1
        class_name = two_slot[0].split()[-1].rstrip(";")
        definition = next(line for line in registry.definitions if line.startswith(class_name + " "))
        assert definition == f"{class_name} = [qsDay qsIt qsLow qsMay qsNo qsTea];"
        assert "    sub qsDay' qsUtter' qsUtter uni200C by qsDay_qsUtter;" in guarded
