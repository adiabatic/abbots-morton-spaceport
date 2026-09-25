"""Tests the decision and treaty tables the crate builds (`kernel_exec.build_tables`) from the mini fixture spec: the enumeration's shape, rule ordering, joint flags, per-configuration differences, the deep-class collapse, stable TSV output, and that rewording a refusal's `why` changes no table. A refusal's `why` is the only rune prose the crate reads, and only the explain ladder shows it.

The crate builds every table here, and every check is an independent Python reading of it. `replay` implements first-match-wins separately from the crate, so it can catch a fold mistake that the fold's own `assert_outcome_partition` check shares. Claims that need the enumerated rows the artifacts drop, or a fold to mutate, are tested in the crate: `fold::tests::the_reduced_replay_catches_what_the_whole_table_replay_catches`, `the_prospect_pass_raises_joints_and_clears_none`, `treaty_rows_tying_on_the_triple_are_ordered_by_the_whole_row`, `a_rule_that_splits_a_deep_class_is_refused` and `a_product_whose_cells_disagree_with_its_rows_is_refused`, and for E-STRANDED `engine::tests::a_left_that_committed_a_seam_nothing_accepts_is_stranded`.

The depth-3 and depth-4 classes run over synthetic ·Tea chains that `tea_chain_spec` adds to the mini spec, because the fixture has no chain of its own. The crate checks the live alphabet's own chain records at every table build.
"""

import dataclasses

import pytest

from rebuild.pipeline import fixtures, kernel_exec, model, table
from rebuild.pipeline.explain import explain_many, parse_sequence
from rebuild.pipeline.kernel_exec import build_tables
from rebuild.pipeline.table import (
    BOUNDARY_LOOKAHEAD_CLASS,
    BOUNDARYISH,
    NA_LABEL,
    TreatyRow,
)

SPEC = fixtures.mini_spec()


def candidacy_tables(spec, features):
    """Builds tables in the pinned world (`simulated_prospect` and `vote_slots` both off), whatever the shipping defaults are. In this world a deep slot opens only for an input whose own `then:` chains reach it, which is what the lazy-enumeration tests below check. `kernel_exec.world_flags` reads these module defaults at call time and passes them to the kernel. `MonkeyPatch.setattr` fails on a missing attribute, so a renamed default fails here instead of being set on a name nothing reads."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", False)
        patch.setattr(kernel_exec, "VOTE_SLOTS_DEFAULT", False)
        return build_tables(spec, features)


def chain_inputs(spec, reach):
    """Returns the runes whose own prefer or resolve records can read a slot `reach` past the input's first lookahead. A `then:` hop advances one slot, and an `except:` entry tests its parent's slot, so its hops count from there. The kernel computes the same sets while enumerating (`census::depth3_inputs` and `depth4_inputs`); this is the test's own statement of which inputs the pinned world may split on a deep slot."""

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


def tea_chain_spec(reach):
    """Returns the mini spec with a synthetic `then:` chain added to ·Tea, because the fixture has none of its own (see the `fixtures.py` docstring and `test_the_fixture_spec_splits_no_third_slot`). ·Tea gets an absolute-mode prefer for its `half` stance whose right condition reads ·May in the first `reach` lookahead slots and ·It in the next one. So `chain_inputs(spec, reach)` is {"qsTea"}, and the outcome depends on the raw token `reach + 1` slots to the right. `TestDepthThreeTablesSynthetic` uses `reach=2` and `TestDepthFourTablesSynthetic` uses `reach=3`."""
    spec = fixtures.mini_spec()
    tea = spec.runes["qsTea"]
    chain = model.Condition(family=("qsIt",))
    for _ in range(reach):
        chain = model.Condition(family=("qsMay",), then=chain)
    record = model.PolicyRecord(kind="prefer", stance="half", mode="absolute", when=model.When(right=chain))
    runes = dict(spec.runes)
    runes["qsTea"] = dataclasses.replace(tea, policy=dataclasses.replace(tea.policy, prefer=(record,)))
    return dataclasses.replace(spec, runes=runes)


@pytest.fixture(scope="module")
def default_tables():
    return build_tables(SPEC, frozenset())


@pytest.fixture(scope="module")
def ss03_tables():
    return build_tables(SPEC, frozenset({"ss03"}))


def replay(decision):
    """Replays every expanded row against the crate's ordered rules under first-match-wins, and asserts that the rules predict each row's outcome and that every rule is the first match for some row. The crate checks the same two claims as it folds (`fold::assert_outcome_partition`, over one left per block). This implementation is independent, so a fold that derived wrong rules and replayed them with the same mistake would pass its own check and fail this one. It replays the whole table at label grain, which a fixture can afford and a live configuration cannot. The crate replays a subset of these rows, so a rule it found first for some row is first here too."""
    rules_by_input: dict[str, list] = {}
    for seat, rule in enumerate(decision.rules):
        rules_by_input.setdefault(rule.input_glyph, []).append((seat, rule))
    failures = []
    first: set[int] = set()
    for row in decision.expanded_transitions():
        predicted = row.input_glyph
        for seat, rule in rules_by_input.get(row.input_glyph, ()):
            if rule.backtrack is not None and row.left not in rule.backtrack:
                continue
            if rule.look1 is not None and row.right1 not in rule.look1:
                continue
            if rule.look2 is not None and row.right2 not in rule.look2:
                continue
            if rule.look3 is not None and row.right3 not in rule.look3:
                continue
            if rule.look4 is not None and row.right4 not in rule.look4:
                continue
            predicted = rule.outcome
            first.add(seat)
            break
        if predicted != row.outcome:
            failures.append((row.key, row.outcome, predicted))
    assert not failures, "; ".join(
        f"{key}: settlement says {expected}, rules say {predicted}"
        for key, expected, predicted in failures[:5]
    )
    never = [seat for seat in range(len(decision.rules)) if seat not in first]
    assert first == set(range(len(decision.rules))), "; ".join(
        f"no replayed row first-matches {rule.input_glyph} "
        f"{(rule.backtrack, rule.look1, rule.look2, rule.look3, rule.look4)} -> {rule.outcome}"
        for rule in (decision.rules[seat] for seat in never[:5])
    )


def test_the_ordered_rules_predict_every_enumerated_row(default_tables):
    decision, _treaty = default_tables
    replay(decision)
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
    # Within one (input, backtrack) group, a boundary rule (uni200C explicit in its first lookahead class) precedes every letter-lookahead rule, and the fallback with no lookahead comes last.
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
    """·It's ss04 unlock of the baseline/baseline pairing requires `left: qsDay`, so it takes effect only in a spec that has ·Day, which the fixture spec does. The test reads `expanded_transitions`, so it compares window rows however deep classes group them. With ss04 on, every row whose outcome changes is an ·It row after a ·Day cell and changes to the same-height pass-through, every row the table gains has the pass-through as its left, no row or outcome is lost, and the deep classes are unchanged."""
    decision, _treaty = default_tables
    ss04_decision, _ss04_treaty = build_tables(SPEC, frozenset({"ss04"}))
    replay(ss04_decision)
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
    replay(decision)
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
    # qsTea's refusal of a baseline entry into full ·Tea fires only inside the lookahead closure (it keeps ·It·Tea broken), so its citation shows that firings inside the closure are recorded as well as direct-window ones.
    assert "glyph_data/runes/qsTea.yaml:policy.refuse[0]" in decision.cited_provenance
    # qsMay's first exit extension produces ex-ext-1 on ·May·It in the default configuration; qsIt's entry extension after halves produces en-ext-1 on ·Tea·It.
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


class TestDepthThreeTablesSynthetic:
    """Tests the lazily enumerated third lookahead slot over the reach-2 chain from `tea_chain_spec`: only the chain-bearing input's windows split the third slot, the split rows compile to three-slot rules ordered ahead of their shallower fallbacks, `replay` passes with the extra slot, and the fourth slot stays unsplit. The live alphabet's own depth-3 and depth-4 chain records are checked in the crate at every `run_m1` table build: a prefer conflict raises E-INCOMPARABLE or E-AMBIGUOUS, `fold::assert_outcome_partition` checks first-match-wins, and either failure reaches Python as a `KernelRunError`. The class uses `candidacy_tables` because under the shipping simulated-prospect default every input becomes a candidate for deep slots, and this class tests the chain case alone."""

    @pytest.fixture(scope="class")
    def synthetic_spec(self):
        return tea_chain_spec(2)

    @pytest.fixture(scope="class")
    def synthetic_decision(self, synthetic_spec):
        decision, _treaty = candidacy_tables(synthetic_spec, frozenset())
        return decision

    def test_the_synthetic_chain_is_the_only_deep_input(self, synthetic_spec, synthetic_decision):
        assert chain_inputs(synthetic_spec, 2) == frozenset({"qsTea"})
        assert chain_inputs(synthetic_spec, 3) == frozenset()
        assert all(row.right4 == NA_LABEL for row in synthetic_decision.transitions)
        assert all(rule.look4 is None for rule in synthetic_decision.rules)

    def test_look3_enumerated_lazily(self, synthetic_spec, synthetic_decision):
        deep = chain_inputs(synthetic_spec, 2)
        saw_enumerated = False
        for row in synthetic_decision.transitions:
            if (
                row.input_glyph.split(".")[0] not in deep
                or row.right1 in BOUNDARYISH
                or row.right2 in BOUNDARYISH
            ):
                assert row.right3 == NA_LABEL, row.key
            elif row.right3 != NA_LABEL:
                saw_enumerated = True
        assert saw_enumerated

    def test_the_split_windows_are_the_chains_own(self, synthetic_decision):
        """The chain reads ·May in the first two lookahead slots, so only ·Tea windows with ·May in both should split the third slot. This checks that the enumeration is lazy as well as correct."""
        for row in synthetic_decision.transitions:
            if row.input_glyph.split(".")[0] != "qsTea":
                continue
            if row.right3 != NA_LABEL:
                assert (row.right1, row.right2) == ("qsMay", "qsMay"), row.key

    def test_hard_invariants_hold_with_the_third_slot(self, synthetic_decision):
        replay(synthetic_decision)

    def test_three_slot_rules_only_for_chain_bearing_inputs(self, synthetic_spec, synthetic_decision):
        deep = chain_inputs(synthetic_spec, 2)
        three_slot = [rule for rule in synthetic_decision.rules if rule.look3 is not None]
        assert three_slot
        assert {rule.input_glyph.split(".")[0] for rule in three_slot} <= deep

    def test_it_window_rule_and_ordering(self, synthetic_decision):
        rules = [rule for rule in synthetic_decision.rules if rule.input_glyph == "qsTea"]
        it_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsMay",)
            and rule.look2 == ("qsMay",)
            and rule.look3 == ("qsIt",)
        )
        assert rules[it_index].outcome == "qsTea.half"
        boundary3_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsMay",)
            and rule.look2 == ("qsMay",)
            and rule.look3 == BOUNDARY_LOOKAHEAD_CLASS
        )
        fallback_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.backtrack is None
            and rule.look1 == ("qsMay",)
            and rule.look2 == ("qsMay",)
            and rule.look3 is None
        )
        assert rules[boundary3_index].outcome == "qsTea.full.ex-y0"
        assert rules[fallback_index].outcome == "qsTea.full.ex-y0"
        assert "uni200C" in rules[boundary3_index].look3
        assert boundary3_index < it_index < fallback_index

    def test_tsv_carries_the_lookahead3_column(self, synthetic_decision, tmp_path):
        path = tmp_path / "settlement-synthetic.tsv"
        synthetic_decision.write_tsv(path)
        lines = path.read_text().splitlines()
        assert (
            lines[1]
            == "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance"
        )
        assert any(line.split("\t")[4] == "qsIt" for line in lines[2:])


class TestDepthFourTablesSynthetic:
    """Tests the lazily enumerated fourth lookahead slot over the reach-3 chain from `tea_chain_spec`, whose innermost hop reads the fourth raw token: only ·Tea's windows split the fourth slot, the split rows compile to four-slot rules ordered ahead of their three-slot fallbacks, and `replay` passes with the extra slot. It uses `candidacy_tables` for the same reason `TestDepthThreeTablesSynthetic` does."""

    @pytest.fixture(scope="class")
    def synthetic_spec(self):
        return tea_chain_spec(3)

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
        """The chain reads ·May in the first three lookahead slots, so only ·Tea windows with ·May in every slot before a split slot should split it. This checks that the enumeration is lazy as well as correct."""
        for row in synthetic_decision.transitions:
            if row.input_glyph.split(".")[0] != "qsTea":
                continue
            if row.right3 != NA_LABEL:
                assert (row.right1, row.right2) == ("qsMay", "qsMay"), row.key
            if row.right4 != NA_LABEL:
                assert (row.right1, row.right2, row.right3) == ("qsMay", "qsMay", "qsMay"), row.key

    def test_hard_invariants_hold_with_the_fourth_slot(self, synthetic_decision):
        replay(synthetic_decision)

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
    """Tests the prospect case of deep-slot enumeration: with the simulated prospect on, a window whose follower's simulated settlement a raw deep token can change enumerates that slot, and no other window does. With it off, only an own-rune chain opens a slot. The kernel decides which slots are live; these tests check the table that results."""

    @pytest.fixture()
    def prospect_spec(self):
        return fixtures.prospect_spec()

    def test_flag_off_keeps_the_chain_only_world(self, prospect_spec):
        decision, _treaty = candidacy_tables(prospect_spec, frozenset())
        assert all(row.right3 == NA_LABEL for row in decision.transitions)

    def test_flag_on_opens_exactly_the_sensitive_window(self, prospect_spec, monkeypatch):
        monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", True)
        decision, _treaty = build_tables(prospect_spec, frozenset())
        replay(decision)
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
    """Tests class-grain enumeration, where deep slots hold outcome fibers that `expanded_transitions` expands back to labels. The `test_two_arm_expansion_equality_*` tests build one spec with `DEEP_CLASSES_DEFAULT` on and off (off runs the kernel's label-grain path, which uses no fiber code) and assert the same expanded rows, rules, identity-guard count, reachable cells, cited provenance, and treaty rows. `test_real_lefts_agree_with_the_fiber_collapse` settles every member of every multi-member row at the row's settled left, one window at a time, so a fiber whose members settle differently there fails."""

    @pytest.fixture()
    def deep_world(self, monkeypatch):
        monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", True)
        return None

    @pytest.fixture()
    def prospect_spec(self):
        return fixtures.prospect_spec()

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
        """Asserts that for every multi-member deep-class token in the enumeration, every member traces the same at the row's settled left: the same settled cell, prospect, joint floor, and notes. The classes come from the enumeration and the traces from `settle-cases`, so a class whose members settle differently at the real left fails here. The test reads the enumeration product (`kernel_exec.enumerate_transitions`) because the tables drop each row's settled left. All member windows go to one `settle_cases` call, in the world the enumeration ran in. The prospect spec creates no r4 classes, so the synthetic depth-4 spec is the case that checks r4 classes (per context and r3 class) at real lefts, and the `checked4` assertion fails if it stops creating them."""
        from rebuild.pipeline.settle import EDGE, LeftContext, RightToken

        spec = request.getfixturevalue(spec_fixture)
        product = kernel_exec.enumerate_transitions(spec, frozenset())
        assert product.deep_classes

        def representative(token):
            members = product.deep_classes.get(token)
            return members[0] if members else token

        kinds = {"#EDGE": "edge", "space": "space", "uni200C": "zwnj", "periodcentered": "namer-dot"}

        def right_token(label):
            if label == NA_LABEL:
                return EDGE
            if label in BOUNDARYISH:
                return {"#EDGE": EDGE}.get(label) or RightToken(kinds[label])
            return RightToken("letter", label)

        cases = []
        asked = []
        for index, row in enumerate(product.transitions):
            members3 = product.deep_classes.get(row.right3)
            members4 = product.deep_classes.get(row.right4)
            if members3 is None and members4 is None:
                continue
            left = (
                LeftContext("letter", row.left_settled)
                if row.left_settled is not None
                else LeftContext(kinds[row.left])
            )
            token = RightToken("letter", row.input_glyph.split(".")[0])
            r1tok = RightToken("letter", row.right1)
            r2tok = RightToken("letter", row.right2)
            rep4 = right_token(representative(row.right4))
            if members3 is not None:
                for member in members3:
                    cases.append(
                        kernel_exec.case_line(left, token, (r1tok, r2tok, RightToken("letter", member), rep4))
                    )
                    asked.append(((index, 3), row.key, member))
            if members4 is not None:
                rep3 = right_token(representative(row.right3))
                for member in members4:
                    cases.append(
                        kernel_exec.case_line(left, token, (r1tok, r2tok, rep3, RightToken("letter", member)))
                    )
                    asked.append(((index, 4), row.key, member))

        answers = kernel_exec.settle_cases(spec, cases, frozenset(), decode=kernel_exec.trace_of)
        records: dict[tuple[int, int], tuple[tuple, dict[tuple, str]]] = {}
        for (asked_at, key, member), trace in zip(asked, answers):
            probe = (trace.settled, trace.prospect, trace.joint_floor, trace.notes)
            records.setdefault(asked_at, (key, {}))[1][probe] = member
        checked3 = 0
        checked4 = 0
        for (_index, slot), (key, seen) in records.items():
            assert len(seen) == 1, (key, slot, sorted(seen.values()))
            if slot == 3:
                checked3 += 1
            else:
                checked4 += 1
        assert checked3
        if expect_r4:
            assert checked4, "the fixture stopped minting r4 classes, so the r4 arm never ran"


REFUSE_WHY_MARKER = "Two adjacent verticals"
REWORDED_WHY = "Two adjacent verticals joined at the baseline blot into one fat stroke."
REFUSAL_WINDOW = "qsSee:qsIt:qsIt"


def refuse_reworded_spec(spec, rune_name, marker):
    """Returns `spec` with `REWORDED_WHY` as the `why` of each of `rune_name`'s refuse records whose `why` starts with `marker`, and nothing else changed. The test uses ·It's "Two adjacent verticals" refusal because it fires on `REFUSAL_WINDOW`."""
    rune = spec.runes[rune_name]
    refuse = tuple(
        dataclasses.replace(record, why=REWORDED_WHY) if (record.why or "").startswith(marker) else record
        for record in rune.policy.refuse
    )
    assert any(record.why == REWORDED_WHY for record in refuse), f"{rune_name} lost its {marker!r} refusal"
    runes = dict(spec.runes)
    runes[rune_name] = dataclasses.replace(rune, policy=dataclasses.replace(rune.policy, refuse=refuse))
    return dataclasses.replace(spec, runes=runes)


def refusal_sentences(spec, window):
    report = explain_many(spec, [(parse_sequence(spec, window), frozenset())])[0]
    return [
        elimination.description
        for position in report.positions
        for elimination in position.trace.eliminations
        if elimination.stage == "refuse"
    ]


def test_a_refuse_why_rewording_leaves_the_tables_byte_identical_and_reaches_the_explain(tmp_path):
    """Checks the assumption behind `fingerprint.rune_file_digest` leaving out a refusal's `why`: the crate reads that text only when it builds an explain ladder. Rewording it leaves the decision TSV, the treaty TSV, and `table.table_digest` unchanged, while the explain output's refusal message shows the new words and not the old ones. The test compares the TSV bytes as well as the digest, because the artifacts are what a stamp describes."""
    spec = fixtures.mini_spec()
    reworded = refuse_reworded_spec(spec, "qsIt", REFUSE_WHY_MARKER)
    tables = {"before": build_tables(spec, frozenset()), "after": build_tables(reworded, frozenset())}
    for name, (decision, treaty) in tables.items():
        decision.write_tsv(tmp_path / f"{name}-decision.tsv")
        treaty.write_tsv(tmp_path / f"{name}-treaty.tsv")
    for artifact in ("decision", "treaty"):
        assert (tmp_path / f"before-{artifact}.tsv").read_bytes() == (
            tmp_path / f"after-{artifact}.tsv"
        ).read_bytes()
    assert table.table_digest(*tables["before"]) == table.table_digest(*tables["after"])

    original = refusal_sentences(spec, REFUSAL_WINDOW)
    assert any("extra-thick stroke" in sentence for sentence in original)
    assert not any(REWORDED_WHY in sentence for sentence in original)
    after = refusal_sentences(reworded, REFUSAL_WINDOW)
    assert any(REWORDED_WHY in sentence for sentence in after)
    assert not any("extra-thick stroke" in sentence for sentence in after)
