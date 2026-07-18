"""Decision-table and treaty-table tests over the real M1 fixture spec: the fixpoint enumeration, the outcome-partition hard invariant, E-STRANDED at table level, rule-ordering discipline, joint flagging, configuration identities, and diff-stable TSV output. The depth-3 class at the bottom runs on the real loaded rune YAML because the frozen fixture spec predates the depth-3 chain records."""

import dataclasses

import pytest

from rebuild.pipeline import fixtures, model, table
from rebuild.pipeline.table import (
    BOUNDARY_LOOKAHEAD_CLASS,
    BOUNDARYISH,
    NA_LABEL,
    TreatyRow,
    build_tables,
    depth3_inputs,
    depth4_inputs,
    fourth_slot_filter,
)

SPEC = fixtures.mini_spec()


@pytest.fixture(scope="module")
def default_tables():
    return build_tables(SPEC, frozenset())


@pytest.fixture(scope="module")
def ss03_tables():
    return build_tables(SPEC, frozenset({"ss03"}))


def test_hard_invariants(default_tables):
    decision, _treaty = default_tables
    decision.assert_outcome_partition()
    decision.assert_e_stranded()
    assert decision.rules
    assert decision.transitions


def test_reachable_cells_cover_the_known_settlements(default_tables):
    decision, _treaty = default_tables
    labels = {
        f"{cell.rune}.{cell.stance}" + ("." + ".".join(cell.adjustments) if cell.adjustments else "")
        for cell in decision.reachable_cells()
    }
    assert any(label.startswith("qsTea.half") for label in labels)
    assert any(label.startswith("qsPea.half") for label in labels)
    assert any("locked" in label for label in labels)


def test_transition_outcomes_match_settlement_examples(default_tables):
    decision, _treaty = default_tables
    by_key = {row.key: row for row in decision.transitions}
    row = by_key[("qsIt", "#EDGE", "qsMay", "#EDGE", "#NA", "#NA")]
    assert row.outcome == "qsIt.hapax.ex-y0"
    row = by_key[("qsTea", "#EDGE", "qsIt", "#EDGE", "#NA", "#NA")]
    assert row.outcome == "qsTea.half.ex-y5"
    row = by_key[("qsTea.noentry", "uni200C", "qsIt", "#EDGE", "#NA", "#NA")]
    assert row.outcome == "qsTea.half.ex-y5.locked"


def test_formation_impossible_windows_are_excluded(default_tables):
    decision, _treaty = default_tables
    for row in decision.transitions:
        assert not (row.input_glyph.split(".")[0] == "qsTea" and row.right1 == "qsOy")
        assert not (row.right1 == "qsTea" and row.right2 == "qsOy")


def test_boundary_rows_lead_their_groups(default_tables):
    # The proven rule-ordering discipline: within one (input, backtrack) group, the boundary-outcome row with uni200C explicit in the class precedes every letter-lookahead row, and the slot-dropped fallback (no lookahead at all) comes last.
    decision, _treaty = default_tables
    groups: dict[tuple, list] = {}
    for rule in decision.rules:
        groups.setdefault((rule.input_glyph, rule.backtrack), []).append(rule)
    for rules in groups.values():
        boundary_positions = [
            i for i, rule in enumerate(rules) if rule.look1 == BOUNDARY_LOOKAHEAD_CLASS and rule.look2 is None
        ]
        letter_positions = [
            i
            for i, rule in enumerate(rules)
            if rule.look1 is not None and rule.look1 != BOUNDARY_LOOKAHEAD_CLASS
        ]
        fallback_positions = [i for i, rule in enumerate(rules) if rule.look1 is None and rule.look2 is None]
        if boundary_positions and letter_positions:
            assert boundary_positions[0] < letter_positions[0]
        if fallback_positions:
            assert fallback_positions[-1] == len(rules) - 1


def test_ss04_table_is_row_identical_to_default(default_tables):
    decision, treaty = default_tables
    ss04_decision, ss04_treaty = build_tables(SPEC, frozenset({"ss04"}))
    assert [(r.key, r.outcome) for r in ss04_decision.transitions] == [
        (r.key, r.outcome) for r in decision.transitions
    ]
    assert ss04_treaty.rows == treaty.rows


def test_ss03_table_differs_and_validates(ss03_tables):
    decision, _treaty = ss03_tables
    decision.assert_outcome_partition()
    decision.assert_e_stranded()
    outcomes = {row.outcome for row in decision.transitions}
    assert "qsTea.half.en-y5" in outcomes


def test_treaty_rows_carry_junction_and_summed_extension(default_tables):
    _decision, treaty = default_tables
    assert (
        TreatyRow(
            left="qsMay.loop.ex-y5.ex-ext-1", right="qsIt.hapax.en-y5", junction="x-height", extension=1
        )
        in treaty.rows
    )
    assert (
        TreatyRow(
            left="qsTea.full.ex-y0", right="qsMay.loop.en-y0.en-ext-1", junction="baseline", extension=1
        )
        in treaty.rows
    )
    assert any(row.junction == "break" and row.extension == 0 for row in treaty.rows)
    assert all(row.kern == 0 for row in treaty.rows)


def test_tsv_artifacts_are_diff_stable(default_tables, tmp_path):
    decision, treaty = default_tables
    first = tmp_path / "settlement-a.tsv"
    second = tmp_path / "settlement-b.tsv"
    decision.write_tsv(first)
    decision.write_tsv(second)
    assert first.read_text() == second.read_text()
    treaty_path = tmp_path / "treaties.tsv"
    treaty.write_tsv(treaty_path)
    lines = treaty_path.read_text().splitlines()
    assert lines[1] == "left\tright\tjunction\textension\tkern"
    assert lines[2:] == sorted(lines[2:])


def test_joint_rows_accessor(default_tables):
    decision, _treaty = default_tables
    joints = decision.joint_rows()
    assert isinstance(joints, frozenset)
    for index in joints:
        assert decision.rules[index].joint


def test_cited_provenance_records_demonstrably_firing_policy(default_tables, ss03_tables):
    decision, _treaty = default_tables
    # qsTea's full-baseline-entry refusal fires only inside the lookahead closure (it is what keeps ·It·Tea broken), so its citation proves the closure channel records firings, not just direct-window ones.
    assert "glyph_data/runes/qsTea.yaml:policy.refuse[0]" in decision.cited_provenance
    # qsMay's first exit extension produces ex-ext-1 on ·May·It under default; qsIt's halves entry extension produces en-ext-1 on ·Tea·It.
    assert "glyph_data/runes/qsMay.yaml:policy.extend[0]" in decision.cited_provenance
    assert "glyph_data/runes/qsIt.yaml:policy.extend[0]" in decision.cited_provenance
    ss03_decision, _ss03_treaty = ss03_tables
    # The ss03-gated reach toward half-·Tea and the half-·Tea entry unlock fire only under ss03.
    assert "glyph_data/runes/qsMay.yaml:policy.extend[1]" in ss03_decision.cited_provenance
    assert "glyph_data/runes/qsMay.yaml:policy.extend[1]" not in decision.cited_provenance
    assert "glyph_data/runes/qsTea.yaml:stances.half.surface.unlocks[0]" in ss03_decision.cited_provenance


def test_fixture_spec_has_no_depth3_inputs_and_no_look3(default_tables):
    assert depth3_inputs(SPEC) == frozenset()
    decision, _treaty = default_tables
    assert all(row.right3 == NA_LABEL for row in decision.transitions)
    assert all(rule.look3 is None for rule in decision.rules)


def test_fixture_spec_has_no_depth4_inputs_and_no_look4(default_tables):
    assert depth4_inputs(SPEC) == frozenset()
    decision, _treaty = default_tables
    assert all(row.right4 == NA_LABEL for row in decision.transitions)
    assert all(rule.look4 is None for rule in decision.rules)


def test_cap_and_slot_arity_are_tied():
    table._assert_window_arity(model.RIGHT_WINDOW_SLOTS)
    with pytest.raises(AssertionError):
        table._assert_window_arity(model.RIGHT_WINDOW_SLOTS + 1)


class TestDepthThreeTables:
    """The lazy third and fourth lookahead slots over the real loaded rune YAML: only depth-3-bearing inputs get their windows split by right3 and only the lone depth-4 input (qsDay's entry-live carve-out) splits on right4 — and only in the chain-live windows `fourth_slot_filter` admits — the split rows compile to deeper-slot rules ordered ahead of their shallower fallbacks, and the hard invariants hold with the extra slots — which is also the corpus-wide proof that the depth-3 and depth-4 chain records introduce no E-INCOMPARABLE/E-AMBIGUOUS prefer conflict."""

    @pytest.fixture(scope="class")
    def real_spec(self):
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_default_spec()

    @pytest.fixture(scope="class")
    def real_default_decision(self, real_spec):
        decision, _treaty = build_tables(real_spec, frozenset())
        return decision

    def test_depth3_inputs_census(self, real_spec):
        assert depth3_inputs(real_spec) == frozenset({"qsDay", "qsOy", "qsTea_qsOy"})

    def test_depth4_inputs_census(self, real_spec):
        assert depth4_inputs(real_spec) == frozenset({"qsDay"})

    def test_look3_enumerated_lazily(self, real_spec, real_default_decision):
        deep = depth3_inputs(real_spec)
        saw_enumerated = False
        for row in real_default_decision.transitions:
            if row.input_glyph.split(".")[0] not in deep or row.right2 in BOUNDARYISH:
                assert row.right3 == NA_LABEL, row.key
            elif row.right3 != NA_LABEL:
                saw_enumerated = True
        assert saw_enumerated

    def test_look4_enumerated_only_where_the_chain_is_live(self, real_spec, real_default_decision):
        live = fourth_slot_filter(real_spec, frozenset())
        assert live("qsDay", "qsTea", "qsUtter", "qsTea")
        assert not live("qsDay", "qsTea", "qsUtter", "qsLow")
        assert not live("qsDay", "qsTea", "qsTea", "qsUtter")
        for row in real_default_decision.transitions:
            if row.input_glyph.split(".")[0] != "qsDay":
                continue
            if (row.right1, row.right2, row.right3) == ("qsTea", "qsUtter", "qsTea"):
                assert row.right4 != NA_LABEL, row.key
            else:
                assert row.right4 == NA_LABEL, row.key

    def test_hard_invariants_hold_with_the_third_slot(self, real_default_decision):
        real_default_decision.assert_outcome_partition()
        real_default_decision.assert_e_stranded()

    def test_three_slot_rules_only_for_depth3_inputs(self, real_spec, real_default_decision):
        deep = depth3_inputs(real_spec)
        three_slot = [rule for rule in real_default_decision.rules if rule.look3 is not None]
        assert three_slot
        assert {rule.input_glyph.split(".")[0] for rule in three_slot} <= deep

    def test_low_window_rule_and_ordering(self, real_default_decision):
        rules = [rule for rule in real_default_decision.rules if rule.input_glyph == "qsDay"]
        low_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 == ("qsLow",)
        )
        assert rules[low_index].outcome == "qsDay.full.ex-y0"
        boundary3_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 == BOUNDARY_LOOKAHEAD_CLASS
        )
        fallback_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 is None
        )
        assert rules[boundary3_index].outcome == "qsDay.full"
        assert rules[fallback_index].outcome == "qsDay.full"
        assert boundary3_index < low_index < fallback_index

    def test_orphan_window_rule_and_ordering(self, real_default_decision):
        rules = [rule for rule in real_default_decision.rules if rule.input_glyph == "qsDay"]
        orphans = ("qsDay", "qsDay_qsUtter", "qsIt", "qsLow", "qsMay", "qsNo", "qsUtter")
        orphan_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 == ("qsTea",)
            and rule.look4 == orphans
        )
        assert rules[orphan_index].outcome == "qsDay.half.en-y0"
        backtrack = rules[orphan_index].backtrack
        boundary4_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack == backtrack
            and rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 == ("qsTea",)
            and rule.look4 == BOUNDARY_LOOKAHEAD_CLASS
        )
        fallback_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack == backtrack
            and rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 == ("qsTea",)
            and rule.look4 is None
        )
        assert rules[boundary4_index].outcome == "qsDay.half.en-y0.ex-y0"
        assert rules[fallback_index].outcome == "qsDay.half.en-y0.ex-y0"
        assert boundary4_index < orphan_index < fallback_index

    def test_tsv_carries_the_lookahead3_column(self, real_default_decision, tmp_path):
        path = tmp_path / "settlement-default.tsv"
        real_default_decision.write_tsv(path)
        lines = path.read_text().splitlines()
        assert (
            lines[1]
            == "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance"
        )
        assert any(line.split("\t")[4] == "qsLow" for line in lines[2:])


class TestDepthFourTablesSynthetic:
    """The lazy fourth lookahead slot, exercised over a synthetic reach-3 record because the production YAML lint still caps chains at two hops, so no shipped rune reaches depth 4 yet. One fixture rune (·Tea) is handed an absolute-stance prefer whose right condition chains three `then:` hops, built straight from `model.Condition` objects to bypass the lint, with the innermost hop distinguishing outcomes by the fourth raw token: only that input's chain-live windows get their fourth slot split, the split rows compile to four-slot rules ordered ahead of their three-slot fallbacks, and the hard invariants hold with the extra slot."""

    @pytest.fixture(scope="class")
    def synthetic_spec(self):
        spec = fixtures.mini_spec()
        tea = spec.runes["qsTea"]
        chain = model.Condition(
            family=("qsMay",),
            then=model.Condition(
                family=("qsMay",),
                then=model.Condition(
                    family=("qsMay",),
                    then=model.Condition(family=("qsIt",)),
                ),
            ),
        )
        record = model.PolicyRecord(
            kind="prefer", stance="half", mode="absolute", when=model.When(right=chain)
        )
        runes = dict(spec.runes)
        runes["qsTea"] = dataclasses.replace(tea, policy=dataclasses.replace(tea.policy, prefer=(record,)))
        return dataclasses.replace(spec, runes=runes)

    @pytest.fixture(scope="class")
    def synthetic_decision(self, synthetic_spec):
        decision, _treaty = build_tables(synthetic_spec, frozenset())
        return decision

    def test_depth4_inputs_census(self, synthetic_spec):
        assert depth4_inputs(synthetic_spec) == frozenset({"qsTea"})
        assert "qsTea" in depth3_inputs(synthetic_spec)

    def test_look4_enumerated_lazily(self, synthetic_spec, synthetic_decision):
        deep = depth4_inputs(synthetic_spec)
        saw_enumerated = False
        for row in synthetic_decision.transitions:
            if (
                row.input_glyph.split(".")[0] not in deep
                or row.right1 in BOUNDARYISH
                or row.right2 in BOUNDARYISH
                or row.right3 in BOUNDARYISH
            ):
                assert row.right4 == NA_LABEL, row.key
            elif row.right4 != NA_LABEL:
                saw_enumerated = True
        assert saw_enumerated

    def test_look4_enumerated_only_where_the_chain_is_live(self, synthetic_spec, synthetic_decision):
        live = fourth_slot_filter(synthetic_spec, frozenset())
        assert live("qsTea", "qsMay", "qsMay", "qsMay")
        assert not live("qsTea", "qsMay", "qsMay", "qsIt")
        assert not live("qsTea", "qsIt", "qsMay", "qsMay")
        for row in synthetic_decision.transitions:
            if row.input_glyph.split(".")[0] != "qsTea" or row.right4 == NA_LABEL:
                continue
            assert (row.right1, row.right2, row.right3) == ("qsMay", "qsMay", "qsMay"), row.key

    def test_hard_invariants_hold_with_the_fourth_slot(self, synthetic_decision):
        synthetic_decision.assert_outcome_partition()
        synthetic_decision.assert_e_stranded()

    def test_four_slot_rules_only_for_depth4_inputs(self, synthetic_spec, synthetic_decision):
        deep = depth4_inputs(synthetic_spec)
        four_slot = [rule for rule in synthetic_decision.rules if rule.look4 is not None]
        assert four_slot
        assert {rule.input_glyph.split(".")[0] for rule in four_slot} <= deep

    def test_may_window_rule_and_ordering(self, synthetic_decision):
        rules = [rule for rule in synthetic_decision.rules if rule.input_glyph == "qsTea"]
        it_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsMay",)
            and rule.look2 == ("qsMay",)
            and rule.look3 == ("qsMay",)
            and rule.look4 == ("qsIt",)
        )
        assert rules[it_index].outcome == "qsTea.half"
        boundary4_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsMay",)
            and rule.look2 == ("qsMay",)
            and rule.look3 == ("qsMay",)
            and rule.look4 == BOUNDARY_LOOKAHEAD_CLASS
        )
        fallback_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsMay",)
            and rule.look2 == ("qsMay",)
            and rule.look3 == ("qsMay",)
            and rule.look4 is None
        )
        assert rules[boundary4_index].outcome == "qsTea.full.ex-y0"
        assert rules[fallback_index].outcome == "qsTea.full.ex-y0"
        assert "uni200C" in rules[boundary4_index].look4
        assert boundary4_index < it_index < fallback_index

    def test_tsv_carries_the_lookahead4_column(self, synthetic_decision, tmp_path):
        path = tmp_path / "settlement-synthetic.tsv"
        synthetic_decision.write_tsv(path)
        lines = path.read_text().splitlines()
        assert (
            lines[1]
            == "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance"
        )
        assert any(line.split("\t")[5] == "qsIt" for line in lines[2:])


def test_rule_provenance_carries_yaml_pointers(default_tables):
    decision, _treaty = default_tables
    pointers = {
        item for rule in decision.rules for item in rule.provenance if item.startswith("glyph_data/runes/")
    }
    assert any("policy.extend" in pointer for pointer in pointers)
    assert any("policy.refuse" in pointer for pointer in pointers)
