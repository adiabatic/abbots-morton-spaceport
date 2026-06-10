"""Decision-table and treaty-table tests over the real M1 fixture spec: the fixpoint enumeration, the outcome-partition hard invariant, E-STRANDED at table level, rule-ordering discipline, joint flagging, configuration identities, and diff-stable TSV output."""

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline.table import BOUNDARY_LOOKAHEAD_CLASS, TreatyRow, build_tables

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
    row = by_key[("qsIt", "#EDGE", "qsMay", "#EDGE")]
    assert row.outcome == "qsIt.bar.ex-y0"
    row = by_key[("qsTea", "#EDGE", "qsIt", "#EDGE")]
    assert row.outcome == "qsTea.half.ex-y5"
    row = by_key[("qsTea.noentry", "uni200C", "qsIt", "#EDGE")]
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
        TreatyRow(left="qsMay.loop.ex-y5.ex-ext-1", right="qsIt.bar.en-y5", junction="x-height", extension=1)
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


def test_rule_provenance_carries_yaml_pointers(default_tables):
    decision, _treaty = default_tables
    pointers = {
        item for rule in decision.rules for item in rule.provenance if item.startswith("glyph_data/runes/")
    }
    assert any("policy.extend" in pointer for pointer in pointers)
    assert any("policy.refuse" in pointer for pointer in pointers)
