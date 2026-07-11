"""Rule-witness coverage: every settlement rule the table builder emits must have a settle-verified realizing string, derived fresh from the decision table on every run — nothing is pinned, so the witness set tracks the rune files automatically. A rule with no witness is dead code in the emitted FEA, which is a generator defect. The worked example this guards: the `qsNo.loop qsMay' qsMay …` rules need six tokens (·Day·Tea·No·May·May·May), one past run_conformance's exhaustive five-token sweep, so witness derivation — not sweep length — is what keeps this gate exact as the alphabet grows."""

import dataclasses

import pytest

from rebuild.pipeline import conform, fixtures
from rebuild.pipeline import table as table_module
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import Rule


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


@pytest.mark.parametrize("config", conform.ACCEPTANCE_CONFIGS)
def test_every_rule_has_a_witness(spec, config):
    features = conform.features_for_config(config)
    decision, _treaty = table_module.build_tables(spec, features)
    report = conform.find_rule_witnesses(spec, features, decision)
    assert (
        not report.unwitnessed
    ), f"{config}: {len(report.unwitnessed)} rule(s) have no settle-verified witness:\n" + "\n".join(
        f"  {conform.rule_signature(decision.rules[index])}" for index in report.unwitnessed
    )
    assert len(report.witnessed) == len(decision.rules)


def test_mini_spec_rules_all_witnessed():
    spec = fixtures.mini_spec()
    decision, _treaty = table_module.build_tables(spec, frozenset())
    report = conform.find_rule_witnesses(spec, frozenset(), decision)
    assert not report.unwitnessed


def test_dead_rule_raises_the_alarm():
    spec = fixtures.mini_spec()
    decision, _treaty = table_module.build_tables(spec, frozenset())
    dead = Rule(
        input_glyph="qsMay",
        backtrack=("qsNever.loop",),
        look1=None,
        look2=None,
        outcome="qsMay",
        provenance=(),
        joint=False,
    )
    poisoned = dataclasses.replace(decision, rules=decision.rules + (dead,))
    report = conform.find_rule_witnesses(spec, frozenset(), poisoned)
    assert report.unwitnessed == [len(decision.rules)]
