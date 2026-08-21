"""Decision-table and treaty-table tests over the real M1 fixture spec, built through the crate (`kernel_exec.build_tables`): the enumeration's shape, the outcome-partition hard invariant, E-STRANDED at table level, rule-ordering discipline, joint flagging, configuration identities, and diff-stable TSV output. The depth-3 class at the bottom runs on the real loaded rune YAML because the frozen fixture spec predates the depth-3 chain records."""

import dataclasses

import pytest

from rebuild.pipeline import fixtures, kernel_exec, model, table
from rebuild.pipeline.kernel_exec import build_tables
from rebuild.pipeline.table import (
    BOUNDARY_LOOKAHEAD_CLASS,
    BOUNDARYISH,
    NA_LABEL,
    TreatyRow,
)

SPEC = fixtures.mini_spec()


def candidacy_tables(spec, features):
    """Build tables in the fully pinned world (`simulated_prospect` and `vote_slots` both off) regardless of the shipping defaults — the world the chain-arm lazy-enumeration tests document, where deep slots open only for own-rune `then:` chains. The defaults are what `kernel_exec.world_flags` carries to the kernel, so switching them here is what puts the crate in that world."""
    from rebuild.pipeline import settle as settle_module

    prior = settle_module.SIMULATED_PROSPECT_DEFAULT
    prior_votes = settle_module.VOTE_SLOTS_DEFAULT
    settle_module.SIMULATED_PROSPECT_DEFAULT = False
    settle_module.VOTE_SLOTS_DEFAULT = False
    try:
        return build_tables(spec, features)
    finally:
        settle_module.SIMULATED_PROSPECT_DEFAULT = prior
        settle_module.VOTE_SLOTS_DEFAULT = prior_votes


def chain_inputs(spec, reach):
    """The runes whose own prefer or resolve records can read a slot `reach` past the input's first lookahead: a `then:` hop advances one slot, and an `except:` entry tests its parent's slot, so its hops count from there. The kernel takes this census itself while enumerating; this is the test-side statement of which inputs the pinned world is entitled to split."""

    def hops(condition):
        reaches = [1 + hops(condition.then)] if condition.then is not None else [0]
        reaches.extend(hops(entry) for entry in condition.except_)
        return max(reaches)

    return frozenset(
        name
        for name, rune in spec.runes.items()
        for record in tuple(rune.policy.prefer) + tuple(rune.policy.resolve)
        if record.when.right is not None and hops(record.when.right) >= reach
    )


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
    decision._assert_deep_class_unions()
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


def test_ss04_opens_the_it_pass_through_after_day(default_tables):
    """·It's ss04 unlock is gated `left: qsDay` on the baseline/baseline pairing, so it can only bite in a world that holds a ·Day — which the fixture spec does. Stated over expanded_transitions, so the claim is about semantic rows rather than fiber boundaries: enabling ss04 moves exactly the ·It rows whose left is a ·Day cell onto the same-height pass-through, every row the table gains is one seated behind that new cell, nothing is lost, and the deep-class collapse is untouched."""
    decision, _treaty = default_tables
    ss04_decision, _ss04_treaty = build_tables(SPEC, frozenset({"ss04"}))
    ss04_decision.assert_outcome_partition()
    ss04_decision.assert_e_stranded()
    pass_through = "qsIt.hapax.en-y0.ex-y0.ex-ext-1"
    default_outcomes = {row.outcome for row in decision.transitions}
    assert {row.outcome for row in ss04_decision.transitions} - default_outcomes == {pass_through}
    assert not default_outcomes - {row.outcome for row in ss04_decision.transitions}
    base = {row.key: row.outcome for row in decision.expanded_transitions()}
    rows = list(ss04_decision.expanded_transitions())
    moved = [row for row in rows if row.key in base and base[row.key] != row.outcome]
    gained = [row for row in rows if row.key not in base]
    assert moved and gained
    assert {row.input_glyph for row in moved} == {"qsIt"}
    assert all(row.left.startswith("qsDay.") for row in moved)
    assert {row.outcome for row in moved} == {pass_through}
    assert {row.left for row in gained} == {pass_through}
    assert not set(base) - {row.key for row in rows}
    assert ss04_decision.deep_classes == decision.deep_classes


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


def test_unflagged_rows_have_no_prospect_divergence(default_tables):
    # Over the expanded label-grain stream: a class row's joint is the OR over its members, so an unflagged row means no member diverges.
    decision, _treaty = default_tables
    rows = [row for row in decision.expanded_transitions() if isinstance(row, table.Transition)]
    index = table.prospect_successor_index(rows)
    for row in rows:
        if row.joint:
            continue
        for successor in table.prospect_successors(index, row):
            realized = 1 if successor.settled.seam is not None else 0
            assert realized == row.prospect, f"{row.key} diverges but is not flagged joint"


def test_prospect_divergence_inventory_rows_are_flagged_joint(default_tables, tmp_path):
    from rebuild.tools import prospect_divergence

    decision, _treaty = default_tables
    first = tmp_path / "prospect-a.tsv"
    second = tmp_path / "prospect-b.tsv"
    prospect_divergence.write_divergences(decision, first)
    prospect_divergence.write_divergences(decision, second)
    assert first.read_text() == second.read_text()
    lines = first.read_text().splitlines()
    assert lines[0] == f"# prospect divergence, config {decision.config}"
    assert lines[1] == "\t".join(prospect_divergence.COLUMNS)
    assert lines[2:] == sorted(lines[2:])
    joint_by_key = {row.key: row.joint for row in decision.expanded_transitions()}
    for line in lines[2:]:
        parts = line.split("\t")
        assert joint_by_key[tuple(parts[:6])], f"inventory window {parts[:6]} is not flagged joint"


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


def test_the_fixture_spec_splits_no_third_slot():
    assert chain_inputs(SPEC, 2) == frozenset()
    decision, _treaty = candidacy_tables(SPEC, frozenset())
    assert all(row.right3 == NA_LABEL for row in decision.transitions)
    assert all(rule.look3 is None for rule in decision.rules)


def test_the_fixture_spec_splits_no_fourth_slot():
    assert chain_inputs(SPEC, 3) == frozenset()
    decision, _treaty = candidacy_tables(SPEC, frozenset())
    assert all(row.right4 == NA_LABEL for row in decision.transitions)
    assert all(rule.look4 is None for rule in decision.rules)


def test_cap_and_slot_arity_are_tied():
    table._assert_window_arity(model.RIGHT_WINDOW_SLOTS)
    with pytest.raises(AssertionError):
        table._assert_window_arity(model.RIGHT_WINDOW_SLOTS + 1)


class TestDepthThreeTables:
    """The lazy third and fourth lookahead slots over the real loaded rune YAML: only chain-bearing inputs get their windows split by right3, only the deeper-chain runes split on right4, the split rows compile to deeper-slot rules ordered ahead of their shallower fallbacks, and the hard invariants hold with the extra slots — which is also the corpus-wide proof that the depth-3 and depth-4 chain records introduce no E-INCOMPARABLE/E-AMBIGUOUS prefer conflict. Built via `candidacy_tables`: the class documents the chain arm, and under the shipping simulated-prospect default the prospect arm would open deep slots for every input."""

    @pytest.fixture(scope="class")
    def real_spec(self):
        import warnings

        from rebuild.pipeline.spec_load import load_default_spec

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_default_spec()

    @pytest.fixture(scope="class")
    def real_default_decision(self, real_spec):
        decision, _treaty = candidacy_tables(real_spec, frozenset())
        return decision

    def test_look3_enumerated_lazily(self, real_spec, real_default_decision):
        deep = chain_inputs(real_spec, 2)
        saw_enumerated = False
        for row in real_default_decision.transitions:
            if row.input_glyph.split(".")[0] not in deep or row.right2 in BOUNDARYISH:
                assert row.right3 == NA_LABEL, row.key
            elif row.right3 != NA_LABEL:
                saw_enumerated = True
        assert saw_enumerated

    def test_hard_invariants_hold_with_the_third_slot(self, real_default_decision):
        real_default_decision.assert_outcome_partition()
        real_default_decision.assert_e_stranded()

    def test_three_slot_rules_only_for_chain_bearing_inputs(self, real_spec, real_default_decision):
        deep = chain_inputs(real_spec, 2)
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
        orphans = ("qsAh", "qsDay", "qsDay_qsUtter", "qsIt", "qsLow", "qsMay", "qsNo", "qsUtter")
        orphan_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.look1 == ("qsTea",)
            and rule.look2 == ("qsUtter",)
            and rule.look3 == ("qsTea",)
            and rule.look4 == orphans
            and "qsPea.full.ex-y0" in rule.backtrack
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
    """The lazy fourth lookahead slot, exercised over a synthetic reach-3 record because the frozen fixture spec carries no depth-4 chain of its own (the real loaded YAML's chains are covered by TestDepthThreeTables). One fixture rune (·Tea) is handed an absolute-stance prefer whose right condition chains three `then:` hops, built straight from `model.Condition` objects, with the innermost hop distinguishing outcomes by the fourth raw token: only that input's windows get their fourth slot split, the split rows compile to four-slot rules ordered ahead of their three-slot fallbacks, and the hard invariants hold with the extra slot. Built via `candidacy_tables`, like TestDepthThreeTables and for the same reason."""

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
        decision, _treaty = candidacy_tables(synthetic_spec, frozenset())
        return decision

    def test_the_synthetic_chain_is_the_only_deep_input(self, synthetic_spec):
        assert chain_inputs(synthetic_spec, 3) == frozenset({"qsTea"})
        assert "qsTea" in chain_inputs(synthetic_spec, 2)

    def test_look4_enumerated_lazily(self, synthetic_spec, synthetic_decision):
        deep = chain_inputs(synthetic_spec, 3)
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

    def test_the_split_windows_are_the_chains_own(self, synthetic_decision):
        """The chain reads ·May at every hop, so the only windows the kernel has any reason to split are the ones standing under that chain — which is what keeps the enumeration lazy rather than merely correct."""
        for row in synthetic_decision.transitions:
            if row.input_glyph.split(".")[0] != "qsTea":
                continue
            if row.right3 != NA_LABEL:
                assert (row.right1, row.right2) == ("qsMay", "qsMay"), row.key
            if row.right4 != NA_LABEL:
                assert (row.right1, row.right2, row.right3) == ("qsMay", "qsMay", "qsMay"), row.key

    def test_hard_invariants_hold_with_the_fourth_slot(self, synthetic_decision):
        synthetic_decision.assert_outcome_partition()
        synthetic_decision.assert_e_stranded()

    def test_four_slot_rules_only_for_chain_bearing_inputs(self, synthetic_spec, synthetic_decision):
        deep = chain_inputs(synthetic_spec, 3)
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


class TestProspectLiveSlots:
    """The issue-28 arm of the deep-slot enumeration: under the simulated prospect, a window whose simulated follower choice a raw deep token can move enumerates that slot, and nothing else does — flag-off, the arm is inert and an own-rune chain stays the only thing that opens a slot. The verdicts themselves are the kernel's; what is stated here is the table they produce."""

    @pytest.fixture()
    def prospect_spec(self):
        from rebuild.test_settle import _prospect_spec

        return _prospect_spec()

    def test_flag_off_keeps_the_chain_only_world(self, prospect_spec):
        decision, _treaty = candidacy_tables(prospect_spec, frozenset())
        assert all(row.right3 == NA_LABEL for row in decision.transitions)

    def test_flag_on_opens_exactly_the_sensitive_window(self, prospect_spec, monkeypatch):
        from rebuild.pipeline import settle as settle_module

        monkeypatch.setattr(settle_module, "SIMULATED_PROSPECT_DEFAULT", True)
        decision, _treaty = build_tables(prospect_spec, frozenset())
        decision.assert_outcome_partition()
        decision.assert_e_stranded()
        split = {
            row.right3: row.outcome
            for row in decision.transitions
            if row.input_glyph == "A" and row.left == "#EDGE" and row.right1 == "B" and row.right2 == "C"
        }
        assert split["D"] == "A.stroke.ex-y5"
        assert split["#EDGE"] == "A.stroke.ex-y0"
        assert all(outcome == "A.stroke.ex-y0" for right3, outcome in split.items() if right3 != "D")
        assert any(rule.look3 == ("D",) for rule in decision.rules if rule.input_glyph == "A")


class TestDeepClasses:
    """The issue-26 class-grain enumeration: deep window slots keyed by outcome fibers, expanded back to labels for every fold-side consumer. The two-arm equality tests build the same spec with the class-grain flag on and off — the off arm is genuinely the kernel's label-grain path, bypassing all fiber code — and assert the expansion boundary holds: identical expanded row multiset, identical rules, identical cited provenance, identical treaty. The real-left arm re-traces every member of every multi-member row at the row's actual settled left, through Python settle rather than the crate, which is what keeps the left-class collapse the fibers import checked by something other than the engine that performed it."""

    @pytest.fixture()
    def deep_world(self, monkeypatch):
        from rebuild.pipeline import settle as settle_module

        monkeypatch.setattr(settle_module, "SIMULATED_PROSPECT_DEFAULT", True)
        return None

    @pytest.fixture()
    def prospect_spec(self):
        from rebuild.test_settle import _prospect_spec

        return _prospect_spec()

    @pytest.fixture()
    def synthetic_depth4_spec(self):
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

    def _both_arms(self, spec, monkeypatch):
        monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", True)
        class_decision, class_treaty = build_tables(spec, frozenset())
        monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", False)
        label_decision, label_treaty = build_tables(spec, frozenset())
        return class_decision, class_treaty, label_decision, label_treaty

    def _assert_arms_equal(self, class_decision, class_treaty, label_decision, label_treaty):
        assert not label_decision.deep_classes
        expanded = list(class_decision.expanded_transitions())
        assert [(r.key, r.outcome) for r in expanded] == [
            (r.key, r.outcome) for r in label_decision.transitions
        ]
        assert class_decision.rules == label_decision.rules
        assert class_decision.identity_guard_rules == label_decision.identity_guard_rules
        assert class_decision.reachable_cells() == label_decision.reachable_cells()
        assert class_decision.cited_provenance == label_decision.cited_provenance
        assert class_treaty.rows == label_treaty.rows

    def test_two_arm_expansion_equality_on_the_mini_spec(self, monkeypatch):
        class_decision, class_treaty, label_decision, label_treaty = self._both_arms(SPEC, monkeypatch)
        assert class_decision.deep_classes
        assert len(class_decision.transitions) < len(label_decision.transitions)
        self._assert_arms_equal(class_decision, class_treaty, label_decision, label_treaty)

    def test_two_arm_expansion_equality_on_the_synthetic_depth4_spec(
        self, synthetic_depth4_spec, monkeypatch
    ):
        class_decision, class_treaty, label_decision, label_treaty = self._both_arms(
            synthetic_depth4_spec, monkeypatch
        )
        assert any(
            row.right4 in class_decision.deep_classes for row in class_decision.transitions
        ), "the synthetic reach-3 chain should mint an r4 class"
        self._assert_arms_equal(class_decision, class_treaty, label_decision, label_treaty)

    def test_two_arm_expansion_equality_on_the_prospect_spec(self, deep_world, prospect_spec, monkeypatch):
        class_decision, class_treaty, label_decision, label_treaty = self._both_arms(
            prospect_spec, monkeypatch
        )
        assert class_decision.deep_classes
        self._assert_arms_equal(class_decision, class_treaty, label_decision, label_treaty)

    def test_the_pinned_world_stays_label_grain(self):
        decision, _treaty = candidacy_tables(SPEC, frozenset())
        assert not decision.deep_classes

    def test_class_ids_are_content_addressed(self, default_tables):
        decision, _treaty = default_tables
        for token, members in decision.deep_classes.items():
            assert token == table.deep_class_id(members)
            assert members == tuple(sorted(members))
            assert len(members) > 1
            assert token not in BOUNDARYISH

    @pytest.mark.parametrize(
        ("spec_fixture", "expect_r4"),
        [("prospect_spec", False), ("synthetic_depth4_spec", True)],
        ids=["prospect", "synthetic-depth4"],
    )
    def test_real_lefts_agree_with_the_fiber_collapse(self, request, deep_world, spec_fixture, expect_r4):
        """The section 2.2 real-left arm: for every multi-member token in the built table, every member traces identically at the row's actual settled left — full probe record, not just the settled cell. The collapse is the crate's, and the re-trace here is Python settle's, so this stays an independent check rather than the engine confirming itself. Two fixtures, because the prospect spec mints no r4 classes: the synthetic depth-4 arm is what exercises the per-(context, r3 class) r4 partition at real lefts, and its `checked4` assertion is what keeps that branch from going quietly dead again."""
        from rebuild.pipeline.settle import EDGE, Engine, LeftContext, RightToken

        spec = request.getfixturevalue(spec_fixture)
        decision, _treaty = build_tables(spec, frozenset())
        assert decision.deep_classes
        engine = Engine(spec, frozenset(), simulated_prospect=True)
        kinds = {"#EDGE": "edge", "space": "space", "uni200C": "zwnj", "periodcentered": "namer-dot"}
        checked3 = 0
        checked4 = 0
        for row in decision.transitions:
            members3 = decision.deep_classes.get(row.right3)
            members4 = decision.deep_classes.get(row.right4)
            if members3 is None and members4 is None:
                continue
            assert isinstance(row, table.Transition)
            left = (
                LeftContext("letter", row.left_settled)
                if row.left_settled is not None
                else LeftContext(kinds[row.left])
            )
            token = RightToken("letter", row.input_glyph.split(".")[0])
            r1tok = RightToken("letter", row.right1)
            r2tok = RightToken("letter", row.right2)
            rep4_label = decision.token_representative(row.right4)
            if rep4_label == NA_LABEL:
                rep4 = EDGE
            elif rep4_label in BOUNDARYISH:
                rep4 = {"#EDGE": EDGE}.get(rep4_label) or RightToken(kinds[rep4_label])
            else:
                rep4 = RightToken("letter", rep4_label)

            def record(r3tok, r4tok):
                trace = engine.transition_trace(left, token, r1tok, r2tok, r3tok, r4tok)
                return (trace.settled, trace.prospect, trace.joint_floor, trace.notes)

            if members3 is not None:
                records = {record(RightToken("letter", member), rep4): member for member in members3}
                assert len(records) == 1, (row.key, sorted(records.values()))
                checked3 += 1
            if members4 is not None:
                rep3 = RightToken("letter", decision.token_representative(row.right3))
                records4 = {record(rep3, RightToken("letter", member)): member for member in members4}
                assert len(records4) == 1, (row.key, sorted(records4.values()))
                checked4 += 1
        assert checked3
        if expect_r4:
            assert checked4, "the fixture stopped minting r4 classes, so the r4 arm never ran"

    def test_union_of_fibers_negative_control(self, deep_world, prospect_spec):
        decision, _treaty = build_tables(prospect_spec, frozenset())
        decision._assert_deep_class_unions()
        token, members = next(iter(decision.deep_classes.items()))
        split_rule = table.Rule(
            input_glyph=next(row.input_glyph for row in decision.transitions if row.right3 == token),
            backtrack=None,
            look1=None,
            look2=None,
            look3=(members[0],),
            look4=None,
            outcome="whatever",
            provenance=(),
            joint=False,
        )
        tampered = dataclasses.replace(decision, rules=decision.rules + (split_rule,))
        with pytest.raises(table.PartitionError):
            tampered._assert_deep_class_unions()
