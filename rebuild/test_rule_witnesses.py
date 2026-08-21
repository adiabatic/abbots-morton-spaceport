"""Rule-witness coverage: every settlement rule the table builder emits must have a settle-verified realizing string, derived fresh from the decision table on every run — nothing is pinned, so the witness set tracks the rune files automatically. A rule with no witness is dead code in the emitted FEA, which is a generator defect. The worked example this guards: the `qsNo.loop qsMay' qsMay …` rules need six tokens (·Day·Tea·No·May·May·May), past what any affordable exhaustive sweep enumerates (the per-edit belt stops at four), so witness derivation — not sweep length — is what keeps this gate exact as the alphabet grows.

The decision table itself is another matter: the fixpoint costs minutes per configuration, and the build stage already serialized every configuration's enumeration under rebuild/out/m1, stamped with the fingerprint of the sources it read — the same stamp the conformance sweep trusts instead of rebuilding. Each arm here reads that file when its stamp matches the current sources and rebuilds in-process when it does not, loudly, so a stale artifact reads as a slow run rather than as skipped coverage; test_the_stamped_table_is_what_a_fresh_fixpoint_builds asserts the substitution's parity once rather than every arm paying for a fresh build.
"""

import dataclasses
import warnings

import pytest

from rebuild.pipeline import conform, fixtures, run_m1
from rebuild.pipeline import table as table_module
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable, Rule


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


def stamped_decision(config: str) -> DecisionTable | None:
    """The build stage's serialized enumeration for `config`, or None when it is missing, unreadable, or stamped from sources other than the ones on disk — the per-config half of the trust decision run_m1.serialized_tables makes for the whole set, made singly here because each parametrized arm loads only its own configuration."""
    try:
        stamp, decision = table_module.read_windows(table_module.windows_path(run_m1.OUT_DIR, config))
    except OSError, ValueError:
        return None
    return decision if stamp == run_m1.tables_inputs() else None


@pytest.mark.parametrize("config", conform.ACCEPTANCE_CONFIGS)
def test_every_rule_has_a_witness(spec, config, live_artifacts):
    features = conform.features_for_config(config)
    decision = stamped_decision(config)
    if decision is None:
        warnings.warn(
            f"{config}: no enumeration under {run_m1.OUT_DIR} is stamped with the current sources; rebuilding the fixpoint in-process — slower, identical coverage"
        )
        decision = table_module.build_tables(spec, features)[0]
    report = conform.find_rule_witnesses(spec, features, decision)
    assert (
        not report.unwitnessed
    ), f"{config}: {len(report.unwitnessed)} rule(s) have no settle-verified witness:\n" + "\n".join(
        f"  {conform.rule_signature(decision.rules[index])}" for index in report.unwitnessed
    )
    assert len(report.witnessed) == len(decision.rules)


def test_the_stamped_table_is_what_a_fresh_fixpoint_builds(spec, live_artifacts):
    """The parity backstop for the substitution above, paid once on the default configuration instead of once per arm: a fresh fixpoint over the current sources produces exactly the rules and windows the stamped enumeration carries. One configuration suffices because every configuration rides the same write_windows/read_windows handoff and the same stamp, and test_windows.py already proves that handoff faithful on the mini spec; what this adds is the real spec, end to end, across processes."""
    decision = stamped_decision("default")
    if decision is None:
        pytest.skip(
            "no stamped enumeration matches the current sources, so the witness arms rebuilt fresh and there is no substitution to check"
        )
    fresh, _treaty = table_module.build_tables(spec, conform.features_for_config("default"))
    assert len(fresh.rules) == len(decision.rules)
    assert fresh.rules == decision.rules
    assert fresh.reachable_cells() == decision.reachable_cells()
    assert [(row.key, row.outcome) for row in fresh.transitions] == [
        (row.key, row.outcome) for row in decision.transitions
    ]


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
        look3=None,
        look4=None,
        outcome="qsMay",
        provenance=(),
        joint=False,
    )
    poisoned = dataclasses.replace(decision, rules=decision.rules + (dead,))
    report = conform.find_rule_witnesses(spec, frozenset(), poisoned)
    assert report.unwitnessed == [len(decision.rules)]
