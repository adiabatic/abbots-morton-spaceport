"""Tests `table.table_digest` over one configuration's decision and join tables. Two builds of unchanged sources give the same digest. Dropping any one rule, window row, join row, reachable cell, or cited-provenance pointer, changing the identity-guard count, flipping a rule's joint flag, or stripping a rule's provenance changes it."""

import dataclasses

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline import table as table_module
from rebuild.pipeline.kernel_exec import build_tables
from rebuild.pipeline.table import DecisionTable, JoinTable, table_digest

SPEC = fixtures.mini_spec()


@pytest.fixture(scope="module")
def built():
    return build_tables(SPEC, frozenset())


def drop_a_rule(decision: DecisionTable, joins: JoinTable):
    assert decision.rules
    return dataclasses.replace(decision, rules=decision.rules[:-1]), joins


def drop_a_window_row(decision: DecisionTable, joins: JoinTable):
    assert decision.transitions
    return dataclasses.replace(decision, transitions=decision.transitions[:-1]), joins


def drop_a_join_row(decision: DecisionTable, joins: JoinTable):
    assert joins.rows
    return decision, dataclasses.replace(joins, rows=joins.rows[:-1])


def drop_a_reachable_cell(decision: DecisionTable, joins: JoinTable):
    cells = sorted(decision.reachable_cells(), key=table_module._cell_key)
    assert cells
    return dataclasses.replace(decision, _cells=frozenset(cells[:-1])), joins


def drop_a_cited_pointer(decision: DecisionTable, joins: JoinTable):
    pointers = sorted(decision.cited_provenance)
    assert pointers
    return dataclasses.replace(decision, cited_provenance=frozenset(pointers[:-1])), joins


def bump_the_identity_guards(decision: DecisionTable, joins: JoinTable):
    return dataclasses.replace(decision, identity_guard_rules=decision.identity_guard_rules + 1), joins


def flip_a_rules_joint(decision: DecisionTable, joins: JoinTable):
    assert decision.rules
    flipped = dataclasses.replace(decision.rules[0], joint=not decision.rules[0].joint)
    return dataclasses.replace(decision, rules=(flipped,) + decision.rules[1:]), joins


def strip_a_rules_provenance(decision: DecisionTable, joins: JoinTable):
    index, rule = next((i, r) for i, r in enumerate(decision.rules) if r.provenance)
    stripped = dataclasses.replace(rule, provenance=())
    return (
        dataclasses.replace(
            decision, rules=decision.rules[:index] + (stripped,) + decision.rules[index + 1 :]
        ),
        joins,
    )


class TestStability:
    def test_two_builds_of_one_configuration_digest_identically(self, built):
        assert table_digest(*build_tables(SPEC, frozenset())) == table_digest(*built)


class TestSensitivity:
    @pytest.mark.parametrize(
        "mutate",
        (
            drop_a_rule,
            drop_a_window_row,
            drop_a_join_row,
            drop_a_reachable_cell,
            drop_a_cited_pointer,
            bump_the_identity_guards,
            flip_a_rules_joint,
            strip_a_rules_provenance,
        ),
        ids=lambda mutate: mutate.__name__,
    )
    def test_every_hashed_channel_moves_the_digest(self, built, mutate):
        decision, joins = built
        assert table_digest(*mutate(dataclasses.replace(decision), dataclasses.replace(joins))) != (
            table_digest(decision, joins)
        )
