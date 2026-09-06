"""Rule certificates: every settlement rule the table builder emits arrives with a realizing string closed off the shortest chain of rows that produces a row the rule first-matches (`certificate.rs`), and the build's witness stage (`run_m1.run_rule_witnesses`, over `conform.check_rule_certificates`) settles each one through the crate and asserts its rule fires. That is the realizability half of the dead-rule alarm — a rule with no string that fires it is dead code in the emitted FEA, which is a generator defect — and the half a fold cannot state, since the fold's never-first refusal replays the table's own rows and a row is realizable only if its left state is. The worked example this guards: the `qsNo.loop qsMay' qsMay …` rules need six tokens (·Day·Tea·No·May·May·May), past what any affordable exhaustive sweep enumerates (the per-edit belt stops at four), so the certificate — not sweep length — is what keeps the alarm exact as the alphabet grows.

The stage lives in the build rather than in a lane of this suite, and the reason is the artifact: a certificate is a fact about exactly the tables it was folded beside, so the only place it can be checked without first proving the tables current is the run that folded them. `run_m1` refuses to mint a glyph while any certificate fails, and `--gates-only` reuses tables that passed. What this module holds is the machinery's own contract on the mini fixture, font-free and with no stamped artifact in sight: both mini tables are certified rule for rule, a certificate that names the wrong text is reported rather than believed, a table whose certificates do not cover its rules vouches for none of them, and every row the fold emits names sources that are among the certified rules, exactly one row per rule.

Coverage reaches the shipped lookup by construction: `emit_gsub._assert_fold_sources` raises at emit time unless every table rule of every configuration folds into exactly one emitted row, so a build that compiled a font could not have dropped or doubled a certified rule on the way. No accounting here runs over any other list, which is the gap behind the issue-28 incident: a coverage tally that read complete while a family of vote-chain rules had no witness at all.
"""

import dataclasses
from collections import Counter

import pytest

from rebuild.pipeline import conform, emit_gsub, fixtures, kernel_exec, oracle_cache, run_m1, settle
from rebuild.pipeline.table import Rule

CONFIGS = ("default", "ss03")


@pytest.fixture(scope="module")
def spec():
    return fixtures.mini_spec()


@pytest.fixture(scope="module")
def tables(spec):
    return {config: kernel_exec.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS}


@pytest.fixture(scope="module")
def guard(spec):
    return kernel_exec.guard_sweep(spec)


def _letters(spec):
    """One codepoint per bare rune of the alphabet, keyed by family."""
    letters = {}
    for char in sorted(conform.spec_alphabet(spec)):
        token = settle.tokens_from_codepoints(spec, [ord(char)])[0]
        if token.kind == "letter":
            letters[token.rune] = char
    return letters


@pytest.mark.parametrize("config", CONFIGS)
def test_every_rule_of_the_mini_tables_is_certified(spec, tables, guard, config):
    decision, _treaty = tables[config]
    assert len(decision.certificates) == len(decision.rules)
    assert all(decision.certificates)
    report = conform.check_rule_certificates(spec, conform.features_for_config(config), decision, guard)
    assert report.passed, report.failures
    assert sorted(report.witnessed) == list(range(len(decision.rules)))
    assert report.fresh and not report.served


def test_a_certificate_naming_the_wrong_text_is_reported(spec, tables, guard):
    """Nothing in a certificate is believed: the check settles it and reads which rule fires, so a certificate swapped for a letter that is not even the rule's input is a failure naming the rule, and every other rule is still verified."""
    decision, _treaty = tables["default"]
    letters = _letters(spec)
    index, foreign = next(
        (index, rune)
        for index, rule in enumerate(decision.rules)
        for rune in sorted(letters)
        if rune not in rule.input_glyph
    )
    certificates = list(decision.certificates)
    certificates[index] = (foreign,)
    poisoned = dataclasses.replace(decision, certificates=tuple(certificates))
    report = conform.check_rule_certificates(spec, frozenset(), poisoned, guard)
    assert not report.passed
    assert len(report.failures) == 1
    assert f"rule {index} " in report.failures[0]
    assert index not in report.witnessed
    assert len(report.witnessed) == len(decision.rules) - 1


def test_a_rule_with_no_certificate_vouches_for_nothing(spec, tables, guard):
    """A rule appended without a certificate leaves the table's certificate count short of its rule count, and a table like that is refused whole rather than checked partially — nothing vouches for which rule the missing certificate was."""
    decision, _treaty = tables["default"]
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
    report = conform.check_rule_certificates(spec, frozenset(), poisoned, guard)
    assert not report.passed
    assert report.witnessed == {}
    assert len(report.failures) == 1
    assert "certificate(s) for" in report.failures[0]


def test_the_witness_stage_writes_a_summary_and_shares_the_settle_memo(spec, tables, tmp_path):
    """The build stage over the mini tables: one summary with a per-configuration record, green, and the settle memo file every later phase loads seeded with the windows the certificates settled — so a second stage over the same tables serves them all."""
    inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
    summary = run_m1.run_rule_witnesses(spec, tables, tmp_path, inputs)
    assert summary["pass"]
    assert summary["failures"] == []
    assert sorted(summary["configs"]) == sorted(CONFIGS)
    for config in CONFIGS:
        record = summary["configs"][config]
        assert record["rules"] == record["witnessed"] == len(tables[config][0].rules)
        assert record["fresh_windows"] and not record["served_windows"]
        assert conform.settle_memo_files(tmp_path, spec, inputs)[config].path.is_file()
    assert (tmp_path / "witness_summary.json").is_file()
    again = run_m1.run_rule_witnesses(spec, tables, tmp_path, inputs)
    for config in CONFIGS:
        assert again["configs"][config]["served_windows"] and not again["configs"][config]["fresh_windows"]


def test_the_witness_stage_names_the_failing_rule(spec, tables, tmp_path):
    decision, treaty = tables["default"]
    poisoned = dataclasses.replace(decision, certificates=decision.certificates[:-1])
    summary = run_m1.run_rule_witnesses(spec, {"default": (poisoned, treaty)}, tmp_path, None)
    assert not summary["pass"]
    assert summary["configs"]["default"]["witnessed"] == 0
    assert "certificate(s) for" in summary["failures"][0]


def test_mini_spec_emitted_rules_all_fold_from_certified_rules(spec, tables, guard):
    """The whole claim end to end on the fixture: both mini tables are certified rule for rule, and every row the fold emits names sources that are among those certified rules, exactly one row per rule. What the build's witness stage proves about the shipped lookup, this proves about the machinery that produces it."""
    emitted = emit_gsub.fold_settle_rules(spec, tables)
    certified = {}
    for config, (decision, _treaty) in tables.items():
        report = conform.check_rule_certificates(spec, conform.features_for_config(config), decision, guard)
        assert report.passed, report.failures
        certified[config] = set(report.witnessed)
    assert emitted
    assert all(rule.sources for rule in emitted)
    sourced = Counter(source for rule in emitted for source in rule.sources)
    for (config, index), count in sorted(sourced.items()):
        assert count == 1, f"{config} rule {index} is the source of {count} emitted rows"
        assert index in certified[config], f"{config} rule {index} sources an emitted row uncertified"
    for config, (decision, _treaty) in tables.items():
        assert {index for name, index in sourced if name == config} == set(range(len(decision.rules)))
