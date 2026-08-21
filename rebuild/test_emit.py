"""emit_gsub / emit_gpos tests over the fixture spec with duck-typed decision tables."""

from collections import Counter
from dataclasses import dataclass, replace

import pytest

from rebuild.pipeline import emit_gpos, emit_gsub, geometry, kernel_exec
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan, marker_glyph_name, relevant_marker_features
from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, ZWNJ, RightToken


@dataclass(frozen=True)
class FakeRule:
    input_glyph: str
    backtrack: tuple | None
    look1: tuple | None
    look2: tuple | None
    outcome: str
    joint: bool = False
    provenance: tuple = ()
    look3: tuple | None = None
    look4: tuple | None = None


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


@pytest.fixture(scope="module")
def guard_verdicts(spec):
    return kernel_exec.guard_sweep(spec)


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
            fea.index("lookup m1_settle useExtension {"),
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
        settle_block = plan.fea_text.split("lookup m1_settle useExtension {")[1].split("} m1_settle;")[0]
        assert settle_block.count("subtable;") == 2  # qsIt | qsMay | qsTea.ss03

    def test_provenance_comments_ride_along(self, spec, glyphs):
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        assert "# joint row | p2" in plan.fea_text

    def test_three_slot_rule_emits_a_third_lookahead_class(self, spec, glyphs):
        names = {cell: record.name for cell, record in glyphs.items()}
        it_ex = names[CellId("qsIt", "hapax", None, "baseline", ())]
        rules = [
            FakeRule(
                "qsIt", None, ("qsMay",), ("qsTea",), it_ex, provenance=("p9",), look3=("qsOy", "qsPea")
            ),
            FakeRule("qsIt", None, ("qsMay",), ("qsTea",), "qsIt", provenance=("p9",)),
        ]
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(rules)}, glyphs=glyphs)
        settle_block = plan.fea_text.split("lookup m1_settle useExtension {")[1].split("} m1_settle;")[0]
        three_slot_line = next(line for line in settle_block.splitlines() if it_ex in line)
        assert f"sub qsIt' qsMay qsTea @s_qsIt_la3_0 by {it_ex};" in three_slot_line
        assert "@s_qsIt_la3_0 = [qsOy qsPea];" in plan.class_definitions

    def test_four_slot_rule_emits_a_fourth_lookahead_class(self, spec, glyphs):
        names = {cell: record.name for cell, record in glyphs.items()}
        it_ex = names[CellId("qsIt", "hapax", None, "baseline", ())]
        rules = [
            FakeRule(
                "qsIt",
                None,
                ("qsMay",),
                ("qsTea",),
                it_ex,
                provenance=("p9",),
                look3=("qsOy", "qsPea"),
                look4=("qsPea", "qsTea"),
            ),
            FakeRule("qsIt", None, ("qsMay",), ("qsTea",), "qsIt", provenance=("p9",)),
        ]
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(rules)}, glyphs=glyphs)
        settle_block = plan.fea_text.split("lookup m1_settle useExtension {")[1].split("} m1_settle;")[0]
        four_slot_line = next(line for line in settle_block.splitlines() if it_ex in line)
        assert f"sub qsIt' qsMay qsTea @s_qsIt_la3_0 @s_qsIt_la4_0 by {it_ex};" in four_slot_line
        assert "@s_qsIt_la4_0 = [qsPea qsTea];" in plan.class_definitions
        assert four_slot_line.index("@s_qsIt_la3_0") < four_slot_line.index("@s_qsIt_la4_0")

    def test_locked_twin_in_look3_raises(self, spec, glyphs):
        bad = [FakeRule("qsIt", None, ("qsMay",), None, "qsIt", provenance=(), look3=("qsTea.noentry",))]
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(bad)}, glyphs=glyphs)

    def test_locked_twin_in_look4_raises(self, spec, glyphs):
        bad = [FakeRule("qsIt", None, ("qsMay",), None, "qsIt", provenance=(), look4=("qsTea.noentry",))]
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(bad)}, glyphs=glyphs)

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

    def test_fold_settle_rules_matches_the_plan(self, spec, glyphs):
        tables = {
            frozenset(): FakeDecision(_rules(spec, glyphs)),
            frozenset({"ss03"}): FakeDecision(_rules(spec, glyphs)),
        }
        plan = emit_gsub.emit_gsub(spec, tables, glyphs=glyphs)
        assert plan.settle_rules
        assert plan.settle_rules == emit_gsub.fold_settle_rules(spec, tables)

    def test_folded_rows_carry_their_sources(self, spec, glyphs):
        names = {cell: record.name for cell, record in glyphs.items()}
        it_ex = names[CellId("qsIt", "hapax", None, "baseline", ())]
        shared = FakeRule("qsIt", None, ("qsMay",), None, it_ex, provenance=("p1",))
        default_only = FakeRule(
            "qsMay", (it_ex,), None, None, names[CellId("qsMay", "loop", "baseline", "x-height", ())]
        )
        ss03_only = FakeRule(
            "qsTea.ss03", (it_ex,), None, None, names[CellId("qsTea", "half", None, "x-height", ())]
        )
        plan = emit_gsub.emit_gsub(
            spec,
            {
                frozenset(): FakeDecision([default_only, shared]),
                frozenset({"ss03"}): FakeDecision([shared, ss03_only]),
            },
            glyphs=glyphs,
        )
        by_input = {rule.input_glyph: rule for rule in plan.settle_rules}
        assert by_input["qsIt"].sources == (("default", 1), ("ss03", 0))
        assert by_input["qsMay"].sources == (("default", 0),)
        assert by_input["qsTea.ss03"].sources == (("ss03", 1),)
        sourced = Counter(source for rule in plan.settle_rules for source in rule.sources)
        assert sourced == Counter([("default", 0), ("default", 1), ("ss03", 0), ("ss03", 1)])
        assert emit_gsub._config_name(frozenset()) == "default"
        assert emit_gsub._config_name(frozenset({"ss03", "ss05"})) == "ss03+ss05"
        assert emit_gsub._config_name("ss10") == "ss10"

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
        settle_block = fea.split("lookup m1_settle useExtension {")[1].split("} m1_settle;")[0]
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


class TestBehaviorClasses:
    """The deep sweep's arming enumeration over the same fixture plan the emission tests above assert the text of: the token set must be exactly what that plan's shapes imply, and every shape the enumeration does not recognize must raise rather than enumerate to nothing — a plan that arms nothing would leave a deep-sweep green standing over a build it never shaped."""

    FIXTURE_TOKENS = {
        "formation:2",
        "guard-form:2-slot",
        "guard-form:fallback",
        "guard-form:zwnj",
        "guard-ignore:1-slot",
        "guard-ignore:2-slot",
        "marker-fold:ss02",
        "marker-fold:ss03",
        "marker-fold:ss04",
        "marker-fold:ss05",
        "namer-dot",
        "settle:bk0-la1",
        "settle:bk0-la2",
        "settle:bk1-la0",
        "settle:cross-subtable",
        "settle:zwnj-in-lookahead",
    }

    @pytest.fixture
    def plan(self, spec, glyphs):
        return emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)

    def test_the_tokens_are_exactly_the_shapes_the_fixture_plan_emits(self, plan):
        assert set(emit_gsub.behavior_classes(plan)) == self.FIXTURE_TOKENS
        assert list(emit_gsub.behavior_classes(plan)) == sorted(self.FIXTURE_TOKENS)

    def test_the_ss10_preempt_is_a_shape_of_its_own(self, spec, glyphs):
        twins = {"qsIt": "qsIt.ss10", "qsMay": "qsMay.ss10", "qsTea": "qsTea.ss10", "qsOy": "qsOy.ss10"}
        with_twins = emit_gsub.emit_gsub(
            spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs, ss10_twins=twins
        )
        assert set(emit_gsub.behavior_classes(with_twins)) == self.FIXTURE_TOKENS | {"ss10-preempt"}

    def test_a_locked_input_and_a_backtrack_zwnj_each_mint_a_token(self, plan):
        grown = replace(
            plan,
            settle_rules=plan.settle_rules
            + (
                emit_gsub.SettleRule(
                    input_glyph="qsTea.noentry",
                    backtrack=frozenset({"uni200C"}),
                    lookahead=(),
                    outcome="qsTea",
                ),
            ),
        )
        assert {"settle:locked-input", "settle:zwnj-in-backtrack"} <= set(emit_gsub.behavior_classes(grown))

    def test_a_single_family_of_settle_rules_never_crosses_a_subtable(self, plan):
        alone = replace(plan, settle_rules=plan.settle_rules[:1])
        assert "settle:cross-subtable" not in emit_gsub.behavior_classes(alone)

    def test_a_rule_past_the_window_depth_raises(self, plan):
        slot = frozenset({"qsMay"})
        deeper = replace(
            plan,
            settle_rules=(emit_gsub.SettleRule("qsIt", None, (slot, slot, slot, slot, slot), "qsIt.ex-y0"),),
        )
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.behavior_classes(deeper)

    def test_an_unrecognized_guard_row_raises(self, plan):
        slot = frozenset({"qsSee"})
        cases = [
            emit_gsub.FormationRow(("qsDay", "qsUtter"), (slot, slot, slot), None),
            emit_gsub.FormationRow(("qsDay", "qsUtter"), (slot,), "qsDay_qsUtter"),
            emit_gsub.FormationRow(("qsDay", "qsUtter"), (slot, slot, slot), "qsDay_qsUtter"),
            emit_gsub.FormationRow(("qsDay",), (), "qsDay_qsUtter"),
        ]
        for row in cases:
            with pytest.raises(emit_gsub.EmitError):
                emit_gsub.behavior_classes(replace(plan, formation_guarded_rows=(row,)))

    def test_an_unknown_calt_stage_raises(self, plan):
        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.behavior_classes(replace(plan, calt_stages=plan.calt_stages + ("m1_something_new",)))

    def test_a_plan_that_grew_a_field_raises(self):
        @dataclass
        class GrownPlan(emit_gsub.GsubPlan):
            novel_stage: tuple[str, ...] = ()

        with pytest.raises(emit_gsub.EmitError):
            emit_gsub.behavior_classes(GrownPlan(fea_text=""))


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
    """The section 5.7 guard's FEA realization over the mini fixture spec, whose qsDay_qsUtter corner carries the guard's worked example; qsTea_qsOy is never blocked there, so it stays in the plain type-4 lookup asserted above."""

    def test_guarded_ligature_moves_to_its_own_contextual_lookup(self, spec, guard_verdicts):
        registry = emit_gsub._ClassRegistry()
        guarded, plain, ignores, _rows, _pairs = emit_gsub._formation_lines(spec, registry, guard_verdicts)
        assert "    sub qsTea qsOy by qsTea_qsOy;" in plain
        assert all("qsDay" not in line for line in plain)
        assert all("qsUtter" not in line for line in plain)
        assert "    sub qsDay' qsUtter' by qsDay_qsUtter;" in guarded
        assert guarded[-1] == "    sub qsDay' qsUtter' by qsDay_qsUtter;"
        letters = sorted(name for name, rune in spec.runes.items() if not rune.sequence)
        seconds = [RightToken("letter", name) for name in letters] + [EDGE, SPACE, ZWNJ, NAMER_DOT]
        full_followers = [
            follower
            for follower in letters
            if all(
                guard_verdicts[("qsDay_qsUtter", RightToken("letter", follower), second)]
                for second in seconds
            )
        ]
        assert full_followers == ["qsLow"]
        one_slot = f"    ignore sub qsDay' qsUtter' {' '.join(full_followers)};"
        assert one_slot in guarded
        assert one_slot.strip() in ignores
        # A one-member guard set is inlined by _ClassRegistry.ref, so the mini world defines no class for it.
        assert not [line for line in registry.definitions if "m1_form_guard" in line]
        assert guarded.index("    sub qsDay' qsUtter' uni200C by qsDay_qsUtter;") < guarded.index(one_slot)
        see_released = [
            "    ignore sub qsDay' qsUtter' qsSee uni200C;",
            "    sub qsDay' qsUtter' qsSee qsLow by qsDay_qsUtter;",
        ]
        for line in see_released:
            assert line in guarded
        assert guarded.index(see_released[0]) < guarded.index(see_released[1])
        assert guarded.index(see_released[1]) < guarded.index(one_slot)
        blanket = "    ignore sub qsDay' qsUtter' qsSee;"
        assert blanket in guarded
        assert guarded.index(blanket) > guarded.index(see_released[1])
        assert guarded.index(blanket) < guarded.index("    sub qsDay' qsUtter' by qsDay_qsUtter;")
        assert "ignore sub qsDay' qsUtter' qsSee;" in ignores
        assert "ignore sub qsDay' qsUtter' qsSee uni200C;" in ignores

    def test_partially_blocked_follower_gets_a_two_slot_ignore(self, spec, guard_verdicts):
        """·Tea takes the pair apart only when a second ·Tea follows, so it compiles to a two-slot ignore over that one third letter rather than joining the one-slot guard class — the branch the shipped alphabet no longer reaches."""
        registry = emit_gsub._ClassRegistry()
        guarded, _plain, ignores, _rows, _pairs = emit_gsub._formation_lines(spec, registry, guard_verdicts)
        letters = sorted(name for name, rune in spec.runes.items() if not rune.sequence)
        blocked_seconds = [
            second
            for second in letters
            if guard_verdicts[
                (
                    "qsDay_qsUtter",
                    RightToken("letter", "qsTea"),
                    RightToken("letter", second),
                )
            ]
        ]
        assert blocked_seconds == ["qsTea"]
        assert "    ignore sub qsDay' qsUtter' qsTea qsTea;" in guarded
        assert "ignore sub qsDay' qsUtter' qsTea qsTea;" in ignores
        assert not [line for line in guarded if line == "    ignore sub qsDay' qsUtter' qsTea;"]
        assert guarded.index("    sub qsDay' qsUtter' qsTea uni200C by qsDay_qsUtter;") < guarded.index(
            "    ignore sub qsDay' qsUtter' qsTea qsTea;"
        )

    def test_utter_second_slot_releases_uniformly(self, spec, guard_verdicts):
        """Ligature-transparent left scopes let the formed ligature serve a following alternate ·Utter wherever the unformed trail could — the alternate's x-height entry scope names qsDay_qsUtter alongside qsUtter — so before a following ·Utter the ligature always forms and the guard emits no second-slot ·Utter rows at all."""
        registry = emit_gsub._ClassRegistry()
        guarded, _plain, _ignores, _rows, _pairs = emit_gsub._formation_lines(spec, registry, guard_verdicts)
        utter_second = [line for line in guarded if "qsDay' qsUtter' qsUtter" in line]
        assert utter_second == []

    def test_emission_reads_one_crate_sweep_and_not_the_python_guard(
        self, spec, glyphs, guard_verdicts, monkeypatch
    ):
        from rebuild.pipeline import settle as settle_module

        sweeps = []

        def crate_guard(received_spec):
            sweeps.append(received_spec)
            return guard_verdicts

        def python_guard(*_arguments, **_keywords):
            raise AssertionError("emit_gsub consulted Python's formation guard")

        monkeypatch.setattr(kernel_exec, "guard_sweep", crate_guard)
        monkeypatch.setattr(settle_module, "formation_blocked", python_guard)
        plan = emit_gsub.emit_gsub(spec, {frozenset(): FakeDecision(_rules(spec, glyphs))}, glyphs=glyphs)
        assert plan.formation_guarded_rows
        assert sweeps == [spec]
