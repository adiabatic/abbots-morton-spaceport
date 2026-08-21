"""Rule-witness coverage: every settlement rule the table builder emits must have a settle-verified realizing string, derived fresh from the decision table on every run — nothing is pinned, so the witness set tracks the rune files automatically. A rule with no witness is dead code in the emitted FEA, which is a generator defect. The worked example this guards: the `qsNo.loop qsMay' qsMay …` rules need six tokens (·Day·Tea·No·May·May·May), past what any affordable exhaustive sweep enumerates (the per-edit belt stops at four), so witness derivation — not sweep length — is what keeps this gate exact as the alphabet grows.

The decision table itself is another matter: the fixpoint costs minutes per configuration, and the build stage already serialized every configuration's enumeration under rebuild/out/m1, stamped with the fingerprint of the sources it read — the same stamp the conformance sweep trusts instead of rebuilding. Every arm here reads that artifact, and an artifact that is missing, unreadable, or stamped from other sources than the ones on disk fails the gate outright with a message saying to run the build first, rather than rebuilding the fixpoint in-process: that rebuild was tracker #66's decision 4 to undo, because it turned a stale `make test-rebuild` from minutes into the better part of an hour and sprang on exactly the bare run an author reaches for after a rune edit. `test_the_stamped_table_is_what_a_fresh_fixpoint_builds` remains the one parity check between the stamped enumeration and a fresh fixpoint, paid once rather than once per arm.

Coverage is enumerated over the emitted rule list — the fold read-back proved the font holds, which the build records in `settle-fold.ndjson` with each row's per-configuration sources — by holding that record to the stamped tables: every emitted row folds from at least one table rule, every table rule folds into exactly one emitted row, and the per-configuration arms witness every table rule. Together that says every row the font ships has a settle-verified realizing string under the configuration that derived it, and it says so about the shipped lookup rather than about the six tables it folds from. No accounting here runs over any other list, which is the gap behind the issue-28 incident: a coverage tally that read complete while a family of vote-chain rules had no witness at all.
"""

import dataclasses
from collections import Counter

import pytest

from rebuild.pipeline import conform, emit_gsub, fixtures, readback, run_m1
from rebuild.pipeline import table as table_module
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable, Rule

CONFIGS = ("default", "ss03")


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


def stamped_decision(config: str, windows: bool = True) -> DecisionTable:
    """The build stage's serialized enumeration for `config`, or a failed gate — the per-config half of the trust decision run_m1.serialized_tables makes for the whole set, made singly here because each parametrized arm loads only its own configuration. A stale or missing artifact is a build to run, not a slow run to sit through: rebuilding the fixpoint in-process cost minutes per configuration and landed on the plain `make test-rebuild` an author reaches for after a rune edit, so the refusal names the command that fixes it instead."""
    stale = f"{config}: no enumeration under {run_m1.OUT_DIR} is stamped with the current sources — a stale or missing artifact fails this gate instead of rebuilding the fixpoint in-process; run `uv run python -m rebuild.pipeline.run_m1` (or a `make review-cycle` pass) first"
    try:
        stamp, decision = table_module.read_windows(
            table_module.windows_path(run_m1.OUT_DIR, config), windows=windows
        )
    except OSError, ValueError:
        pytest.fail(stale)
    if stamp != run_m1.tables_inputs():
        pytest.fail(stale)
    return decision


def stamped_fold() -> readback.SettleFold:
    """The build stage's record of the settlement rows it emitted, refused on the same terms as the tables beside it. A record whose read-back did not pass is refused too: its rows are what the emitters planned, but nothing has proven the font holds them, so counting coverage over it would claim more than the build earned."""
    stale = f"no {readback.SETTLE_FOLD_FILENAME} under {run_m1.OUT_DIR} is stamped with the current sources — a stale or missing record fails this gate instead of re-folding in-process; run `uv run python -m rebuild.pipeline.run_m1` (or a `make review-cycle` pass) first"
    try:
        fold = readback.read_settle_fold(readback.settle_fold_path(run_m1.OUT_DIR))
    except OSError, ValueError:
        pytest.fail(stale)
    if fold.inputs != run_m1.tables_inputs():
        pytest.fail(stale)
    if not fold.readback_pass:
        pytest.fail(
            f"the emitted settlement lookup under {run_m1.OUT_DIR} did not read back from the font — see readback_summary.json"
        )
    return fold


def _row_text(rule: emit_gsub.SettleRule) -> str:
    """One emitted row as a line, in `conform.rule_signature`'s idiom — which cannot serve for these rows, because it reads look1..look4 off a table rule where an emitted row carries only its live slots."""
    backtrack = sorted(rule.backtrack) if rule.backtrack else "any"
    lookahead = ", ".join(str(sorted(slot)) for slot in rule.lookahead) or "any"
    return f"{rule.input_glyph} [backtrack={backtrack}, lookahead={lookahead}] -> {rule.outcome} from {[list(source) for source in rule.sources]}"


@pytest.mark.parametrize("config", conform.ACCEPTANCE_CONFIGS)
def test_every_rule_has_a_witness(spec, config, live_artifacts):
    features = conform.features_for_config(config)
    decision = stamped_decision(config)
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
    fresh, _treaty = table_module.build_tables(spec, conform.features_for_config("default"))
    assert len(fresh.rules) == len(decision.rules)
    assert fresh.rules == decision.rules
    assert fresh.reachable_cells() == decision.reachable_cells()
    assert [(row.key, row.outcome) for row in fresh.transitions] == [
        (row.key, row.outcome) for row in decision.transitions
    ]


def test_every_emitted_rule_folds_from_a_stamped_table_rule(spec, live_artifacts):
    """The emitted-list half of the coverage claim. The arms above witness every rule of every stamped table; this holds the recorded fold — what read-back proved the font holds — to those same tables: re-folding the stamped rules reproduces the record row for row, sources included, every emitted row names at least one source, and every table rule is the source of exactly one row. Together: every row the font ships has a settle-verified realizing string under the configuration that derived it, and the accounting unit is the shipped lookup rather than the six tables it folds from."""
    fold = stamped_fold()
    tables = {config: stamped_decision(config, windows=False) for config in conform.ACCEPTANCE_CONFIGS}
    refolded = emit_gsub.fold_settle_rules(spec, tables)
    assert len(refolded) == len(
        fold.rules
    ), f"the recorded fold holds {len(fold.rules)} row(s); re-folding the stamped tables produces {len(refolded)}"
    for position, (recorded, rebuilt) in enumerate(zip(fold.rules, refolded)):
        assert recorded == rebuilt, (
            f"row {position} differs between the recorded fold and a re-fold of the stamped tables:\n"
            f"  recorded  {_row_text(recorded)}\n"
            f"  re-folded {_row_text(rebuilt)}"
        )
    assert refolded == fold.rules
    unsourced = [rule for rule in fold.rules if not rule.sources]
    assert (
        not unsourced
    ), f"{len(unsourced)} emitted row(s) name no table rule they folded from:\n" + "\n".join(
        f"  {_row_text(rule)}" for rule in unsourced[:5]
    )
    sourced = Counter(source for rule in fold.rules for source in rule.sources)
    for config, decision in tables.items():
        indices = {index for name, index in sourced if name == config}
        expected = set(range(len(decision.rules)))
        assert indices == expected, (
            f"{config}: {len(expected - indices)} table rule(s) fold into no emitted row, "
            f"{len(indices - expected)} recorded source(s) name no rule of the stamped table"
        )
    doubled = [source for source, count in sourced.items() if count != 1]
    assert (
        not doubled
    ), f"{len(doubled)} table rule(s) source more than one emitted row: {sorted(doubled)[:5]}"
    assert tuple(fold.configs) == tuple(
        dict.fromkeys(name for rule in fold.rules for name, _index in rule.sources)
    )
    assert set(fold.configs) == set(conform.ACCEPTANCE_CONFIGS)


def test_mini_spec_rules_all_witnessed():
    spec = fixtures.mini_spec()
    decision, _treaty = table_module.build_tables(spec, frozenset())
    report = conform.find_rule_witnesses(spec, frozenset(), decision)
    assert not report.unwitnessed


def test_mini_spec_emitted_rules_all_fold_from_witnessed_rules():
    """The whole claim end to end on the fixture, font-free and without a stamped artifact in sight: both mini tables are witnessed rule for rule, and every row the fold emits names sources that are among those witnessed rules, exactly one row per rule. What the live arms prove about the shipped lookup, this proves about the machinery that produces it."""
    spec = fixtures.mini_spec()
    tables = {
        config: table_module.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS
    }
    emitted = emit_gsub.fold_settle_rules(spec, tables)
    reports = {}
    for config, (decision, _treaty) in tables.items():
        report = conform.find_rule_witnesses(spec, conform.features_for_config(config), decision)
        assert (
            not report.unwitnessed
        ), f"{config}: {len(report.unwitnessed)} rule(s) have no settle-verified witness"
        reports[config] = report
    assert emitted
    assert all(rule.sources for rule in emitted)
    sourced = Counter(source for rule in emitted for source in rule.sources)
    for (config, index), count in sorted(sourced.items()):
        assert count == 1, f"{config} rule {index} is the source of {count} emitted rows"
        assert index in reports[config].witnessed, f"{config} rule {index} sources an emitted row unwitnessed"
    for config, (decision, _treaty) in tables.items():
        assert {index for name, index in sourced if name == config} == set(range(len(decision.rules)))


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
