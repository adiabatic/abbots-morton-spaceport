"""Tests for rule certificates. The table builder emits each settlement rule with a certificate: a string closed off the shortest chain of rows that produces a row the rule first-matches (`certificate.rs`). The build's witness stage (`run_m1.run_rule_witnesses`, over `witness.check_rule_certificates`) settles each certificate through the crate and asserts that its rule fires. This is the realizability part of the checks that fail the build on a rule that can never fire. A rule that no string fires is dead code in the emitted FEA, which is a generator defect. The fold cannot check this, because its never-first check replays the table's own rows, and a row is realizable only if its left state is. The worked example is the `qsNo.loop qsMay' qsMay …` rules, which need six tokens (·Day·Tea·No·May·May·May). No affordable exhaustive sweep reaches that length (the per-edit belt stops at `conform.BELT_HORIZON`), so the certificate is what keeps the check exact as the alphabet grows.

The stage runs in the build and not in this suite, because a certificate is a fact about the tables it was folded beside, and only the run that folded them can check it without first proving the tables current. A failing certificate fails the run_m1 gate, and `--gates-only` reuses tables that passed. This module tests the machinery on the mini fixture, without a font or a stamped artifact: both mini tables are certified rule for rule, a certificate that names the wrong text is reported, a table whose certificates do not cover its rules has none of them checked, and every row the fold emits names sources among the certified rules, exactly one row per rule.

The emitted lookup is covered by construction: `emit_gsub._assert_fold_sources` raises at emit time unless every table rule of every configuration folds into exactly one emitted row, so a build that compiled a font cannot have dropped or doubled a certified rule.
"""

import dataclasses
import itertools
from collections import Counter

import pytest

from rebuild.pipeline import conform, emit_gsub, fixtures, kernel_exec, oracle_cache, run_m1, settle, witness
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
    report = witness.check_rule_certificates(spec, conform.features_for_config(config), decision, guard)
    assert report.passed, report.failures
    assert sorted(report.witnessed) == list(range(len(decision.rules)))
    assert report.fresh and not report.served


def test_a_certificate_naming_the_wrong_text_is_reported(spec, tables, guard):
    """The check settles a certificate and reads which rule fires, so a certificate replaced by a letter that is not the rule's input fails with the rule's index, and every other rule is still checked."""
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
    report = witness.check_rule_certificates(spec, frozenset(), poisoned, guard)
    assert not report.passed
    assert len(report.failures) == 1
    assert f"rule {index} " in report.failures[0]
    assert index not in report.witnessed
    assert len(report.witnessed) == len(decision.rules) - 1


def test_a_certificate_the_registry_refuses_is_reported_against_its_rule_under_a_memo(
    spec, tables, guard, tmp_path, monkeypatch
):
    """A certificate text the registry will not tokenize fails only its own rule, not the whole stage. The walk restricted to the certificates' asks meets the refusal while it computes those asks, before the prefill, and the check goes on to witness every other rule against the shared file and reports the refused rule. When the restriction stops early like this, the whole file is loaded, which the served count shows."""
    decision, _treaty = tables["default"]
    memo = conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", "stamp")
    pile = sorted({witness._token_text(spec, tokens) for tokens in decision.certificates})
    seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
    seed.walk_many(pile)
    assert seed.save_memo()
    index = 0
    refused = witness._token_text(spec, decision.certificates[index])
    original = settle.tokens_from_codepoints

    def refusing(spec_, codepoints):
        if list(codepoints) == [ord(char) for char in refused]:
            raise settle.SettleError("refused for the test")
        return original(spec_, codepoints)

    monkeypatch.setattr(settle, "tokens_from_codepoints", refusing)
    report = witness.check_rule_certificates(
        spec, frozenset(), decision, guard, memo=dataclasses.replace(memo, write_path=tmp_path / "part.gz")
    )
    assert not report.passed
    assert len(report.failures) == 1
    assert (
        f"rule {index} " in report.failures[0] and "the crate refused its certificate" in report.failures[0]
    )
    assert sorted(report.witnessed) == [i for i in range(len(decision.rules)) if i != index]
    assert report.served == len(seed.windows) and report.unasked == 0


def test_a_rule_with_no_certificate_vouches_for_nothing(spec, tables, guard):
    """A rule appended without a certificate leaves the table with fewer certificates than rules. Such a table fails whole and is not checked in part, because nothing says which rule lacks its certificate."""
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
    report = witness.check_rule_certificates(spec, frozenset(), poisoned, guard)
    assert not report.passed
    assert report.witnessed == {}
    assert len(report.failures) == 1
    assert "certificate(s) for" in report.failures[0]


def test_the_witness_stage_writes_a_summary_and_shares_the_settle_memo(spec, tables, tmp_path):
    """The build stage over the mini tables writes one passing summary with a record per configuration. It also seeds the settle memo file that later phases load with the windows the certificates settled, so a second run over the same tables serves them all from the file."""
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


def _memo_rows(memo, spec) -> set[tuple[str, ...]]:
    """Return every window the file at `memo.path` holds, as the six-tuples of labels a walk keys on."""
    store = conform._MemoStore()
    store.load(memo, spec, None, lambda item: (item, "", ""))
    try:
        return {window for window, _outcome in store.items()}
    finally:
        store.close()


def _ask_of(window) -> tuple[str, ...]:
    return (window[0],) + tuple(window[2:])


def test_the_stage_loads_only_the_windows_its_certificates_can_ask(spec, tables, guard, tmp_path):
    """The file is seeded the way the belt seeds it: every mini-alphabet string up to length 2, walked and saved whole, which holds more windows than the certificates ask. The stage serves exactly the rows whose five settlement-independent slots are among its asks, and witnesses every rule. Afterwards the file holds every seeded row plus the windows the stage settled fresh. That shows the part-plus-absorb write drops nothing. It also shows the ask set covers everything the walk asks, because a row the load dropped and the walk then needed would be settled again and counted fresh without adding a row."""
    inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
    memos = conform.settle_memo_files(tmp_path, spec, inputs)
    alphabet = sorted(conform.spec_alphabet(spec))
    texts = ["".join(combo) for length in (1, 2) for combo in itertools.product(alphabet, repeat=length)]
    seeded = {}
    for config in CONFIGS:
        features = conform.features_for_config(config)
        seed = conform._SettledWindowWalk(spec, features, {}, guard, memo=memos[config])
        seed.walk_many(texts)
        assert seed.save_memo()
        seeded[config] = _memo_rows(memos[config], spec)
        assert seeded[config] == set(seed.windows)
    summary = run_m1.run_rule_witnesses(spec, tables, tmp_path, inputs)
    assert summary["pass"]
    for config in CONFIGS:
        decision = tables[config][0]
        record = summary["configs"][config]
        assert record["witnessed"] == record["rules"] == len(decision.rules)
        pile = sorted({witness._token_text(spec, tokens) for tokens in decision.certificates})
        asker = conform._SettledWindowWalk(spec, conform.features_for_config(config), {}, guard)
        asks = asker.load_only_asked_by(pile)
        kept = {window for window in seeded[config] if _ask_of(window) in asks}
        assert 0 < len(kept) < len(seeded[config])
        assert record["served_windows"] == len(kept)
        after = _memo_rows(memos[config], spec)
        assert after >= seeded[config]
        assert len(after) == len(seeded[config]) + record["fresh_windows"]


def test_a_restricted_walk_settles_a_text_outside_its_asks_fresh_and_alike(spec, guard, tmp_path):
    """The other side of the superset check: a walk restricted to one text's asks, over a file holding only another text's windows, serves none of them and counts them all as unasked. It settles the other text the same way an unrestricted walk over the same file does, and counts every one of its windows as fresh. So a dropped row costs a re-settle but never changes the answer."""
    letters = sorted(_letters(spec).values())
    inside, outside = letters[0] * 2, letters[1] + letters[2]
    memo = conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", "stamp")
    seed = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
    seed.walk_many([outside])
    assert seed.save_memo()
    unrestricted = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=memo)
    expected = unrestricted.walk(outside)
    assert unrestricted.memo_windows == len(seed.windows) and unrestricted.fresh_windows == 0
    restricted = conform._SettledWindowWalk(
        spec, frozenset(), {}, guard, memo=dataclasses.replace(memo, write_path=tmp_path / "part.gz")
    )
    asks = restricted.load_only_asked_by([inside])
    assert asks and not any(_ask_of(window) in asks for window in seed.windows)
    assert restricted.walk(outside) == expected
    assert restricted.memo_windows == 0 and restricted.unasked_windows == len(seed.windows)
    assert restricted.fresh_windows == len(seed.windows) == len(restricted.windows)


def test_the_witness_stage_names_the_failing_rule(spec, tables, tmp_path):
    decision, treaty = tables["default"]
    poisoned = dataclasses.replace(decision, certificates=decision.certificates[:-1])
    summary = run_m1.run_rule_witnesses(spec, {"default": (poisoned, treaty)}, tmp_path, None)
    assert not summary["pass"]
    assert summary["configs"]["default"]["witnessed"] == 0
    assert "certificate(s) for" in summary["failures"][0]


def test_mini_spec_emitted_rules_all_fold_from_certified_rules(spec, tables, guard):
    """On the fixture, both mini tables are certified rule for rule, and every row the fold emits names sources among those certified rules, exactly one row per rule."""
    emitted = emit_gsub.fold_settle_rules(spec, tables)
    certified = {}
    for config, (decision, _treaty) in tables.items():
        report = witness.check_rule_certificates(spec, conform.features_for_config(config), decision, guard)
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
