"""Rule-witness coverage: every settlement rule the table builder emits must have a settle-verified realizing string, checked against the decision table on every run — nothing is pinned, so the witness set tracks the rune files automatically. A rule with no witness is dead code in the emitted FEA, which is a generator defect. The worked example this guards: the `qsNo.loop qsMay' qsMay …` rules need six tokens (·Day·Tea·No·May·May·May), past what any affordable exhaustive sweep enumerates (the per-edit belt stops at four), so witness derivation — not sweep length — is what keeps this gate exact as the alphabet grows.

The decision table itself is another matter: the fixpoint costs minutes per configuration, and the build stage already serialized every configuration's enumeration under rebuild/out/m1, stamped with the fingerprint of the sources it read — the same stamp the conformance sweep trusts instead of rebuilding. Every arm here reads that artifact, and an artifact that is missing, unreadable, or stamped from other sources than the ones on disk fails the gate outright with a message saying to run the build first, rather than rebuilding the fixpoint in-process: that rebuild was tracker #66's decision 4 to undo, because it turned a stale `make test-rebuild` from minutes into the better part of an hour and sprang on exactly the bare run an author reaches for after a rune edit. There is no parity arm beside them any more: the stamp binds an enumeration to the sources it was built from, and since issue 78 the crate is the only fixpoint there is, so a fresh enumeration here could only restate the same engine's answer at the price of a live build.

Only half the alarm still lives here. That no rule sits behind another and can never win a window at all is the crate's now: `fold::assert_outcome_partition` tallies the first-matching rule of every replayed row and refuses the table when one is never first, so a statically dead rule fails the build. What is left is realizability — whether any string reaches the windows a rule owns — and each arm reaches it hint-first: `conform.witness_hints_path` names a memo of verified witness texts under rebuild/out/m1, written by the arm itself, gitignored, and deliberately outside `artifact_cycle.M1_ARTIFACT_NAMES` so it can never move the validators lane's own key. Nothing in it is trusted: every hint is re-walked and re-checked against the current table before it counts, keyed by `conform.rule_signature` so it survives a table that reindexed around an edit, and a hint that no longer wins its rule is a miss the search picks up. So staleness costs a search and can never cost a false pass, and a run with no hints at all is exactly the run this gate always was. `rebuild.tools.rebuild_gate` refuses to spawn this lane at all while the tables' stamp is stale, so the refusal below is the second line of defense rather than the first.

Coverage is counted over the stamped tables, and that count reaches the shipped lookup by construction: `emit_gsub._assert_fold_sources` raises at emit time unless every table rule of every configuration folds into exactly one emitted row, so a build that compiled a font could not have dropped or doubled a witnessed rule on the way. What the per-configuration arms add is the realizing string itself. No accounting here runs over any other list, which is the gap behind the issue-28 incident: a coverage tally that read complete while a family of vote-chain rules had no witness at all.
"""

from collections import Counter

import pytest

from rebuild.pipeline import conform, emit_gsub, fixtures, kernel_exec, run_m1
from rebuild.pipeline import table as table_module
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable

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


@pytest.mark.parametrize("config", conform.SETTLEMENT_CONFIGS)
def test_every_rule_has_a_witness(spec, config, live_artifacts):
    features = conform.features_for_config(config)
    decision = stamped_decision(config)
    path = conform.witness_hints_path(run_m1.OUT_DIR, config)
    hints = conform.read_witness_hints(path, config)
    report = conform.find_rule_witnesses(spec, features, decision, hints=hints)
    conform.write_witness_hints(path, decision, report)
    print(f"{config}: {len(hints)} hint(s) read, {len(report.searched)} rule(s) searched")
    assert (
        not report.unwitnessed
    ), f"{config}: {len(report.unwitnessed)} rule(s) have no settle-verified witness:\n" + "\n".join(
        f"  {conform.rule_signature(decision.rules[index])}" for index in report.unwitnessed
    )
    assert len(report.witnessed) == len(decision.rules)


def test_mini_spec_emitted_rules_all_fold_from_witnessed_rules():
    """The whole claim end to end on the fixture, font-free and without a stamped artifact in sight: both mini tables are witnessed rule for rule, and every row the fold emits names sources that are among those witnessed rules, exactly one row per rule. What the live arms prove about the shipped lookup, this proves about the machinery that produces it."""
    spec = fixtures.mini_spec()
    tables = {
        config: kernel_exec.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS
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
