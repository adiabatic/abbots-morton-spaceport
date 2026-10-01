"""Tests for the shipped-order stage on the mini fixture. `run_m1.run_emitted_order` runs the crate's `replay-emitted` subcommand, which walks every configuration's rows first-match against the order `emit_gsub._ordered_settle_rules` emits. The build runs this stage over its own tables; these tests cover the order and context files the crate reads, the stage's summary, and the failure that names the row and rule when the order gives a row a different outcome from its table."""

import dataclasses

import pytest

from rebuild.pipeline import conform, emit_gsub, fixtures, kernel_exec, model, run_m1

STAMP = "emitted-order-test"


@pytest.fixture(scope="module")
def spec():
    return fixtures.mini_spec()


@pytest.fixture(scope="module")
def built(spec, tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("tables")
    tables, _digests = run_m1.build_tables(spec, out_dir, inputs=STAMP)
    return out_dir, tables


def test_the_order_file_is_the_fold_in_fea_order_naming_its_sources(spec, built):
    _out_dir, tables = built
    lines = emit_gsub.emitted_order_tsv(spec, tables).splitlines()
    assert lines[0] == f"# settlement table, config {emit_gsub.EMITTED_ORDER_CONFIG}"
    assert lines[1].split("\t") == [
        "input",
        "backtrack",
        "lookahead1",
        "lookahead2",
        "lookahead3",
        "lookahead4",
        "outcome",
        "joint",
        "provenance",
    ]
    folded = emit_gsub.fold_settle_rules(spec, tables)
    assert len(lines) - 2 == len(folded)
    for line, rule in zip(lines[2:], folded):
        fields = line.split("\t")
        assert fields[0] == rule.input_glyph
        assert fields[6] == rule.outcome
        assert fields[8] == "; ".join(f"{config}#{index}" for config, index in rule.sources)
        assert (fields[1] == "-") == (rule.backtrack is None)


def test_the_context_file_carries_the_marker_renaming_and_the_deep_classes(spec, built):
    _out_dir, tables = built
    decision, _joins = tables["ss03"]
    lines = emit_gsub.emitted_context_tsv(spec, "ss03", decision).splitlines()
    renames = {raw: copy for kind, raw, copy in (line.split("\t") for line in lines) if kind == "rename"}
    assert renames == model.raw_rename_map(spec, frozenset({"ss03"}))
    assert renames and all(
        copy == f"{raw.split('.')[0]}.ss03{raw[len(raw.split('.')[0]):]}" for raw, copy in renames.items()
    )
    classed = dataclasses.replace(decision, deep_classes={"#Cabc": ("qsPea", "qsTea")})
    assert "class\t#Cabc\tqsPea qsTea" in emit_gsub.emitted_context_tsv(spec, "ss03", classed).splitlines()
    plain = emit_gsub.emitted_context_tsv(spec, "default", decision).splitlines()
    assert not [line for line in plain if line.startswith("rename\t")]
    assert [line.split("\t")[1] for line in plain] == sorted(decision.deep_classes)


def test_the_stage_answers_every_row_of_every_configuration(spec, built):
    out_dir, tables = built
    summary = run_m1.run_emitted_order(spec, tables, out_dir)
    assert summary["pass"], summary["error"]
    assert list(summary["configs"]) == list(conform.SETTLEMENT_CONFIGS)
    assert summary["rules"] == len(emit_gsub.fold_settle_rules(spec, tables))
    for config, (decision, _joins) in tables.items():
        assert summary["configs"][config]["rows"] > 0
        assert summary["configs"][config]["rows"] >= len(decision.rules)
    assert (out_dir / run_m1.EMITTED_ORDER_SUMMARY).is_file()


def test_an_order_that_answers_a_row_differently_is_refused_naming_the_row(spec, built, monkeypatch):
    """A rule with an outcome no table wrote is put first in the shipped order, so the first row it matches gets the wrong outcome. The summary's `error` names the configuration, the row, the emitted rule that fired, and the table's own rule."""
    out_dir, tables = built
    ordered = emit_gsub._ordered_settle_rules

    def poisoned(rules, marker_names=frozenset()):
        grouped = ordered(rules, marker_names)
        first = grouped[0]
        bogus = dataclasses.replace(
            first,
            backtrack=None,
            look1=None,
            look2=None,
            look3=None,
            look4=None,
            outcome=f"{first.input_glyph}.perturbed",
        )
        return [bogus, *grouped]

    monkeypatch.setattr(emit_gsub, "_ordered_settle_rules", poisoned)
    monkeypatch.setattr(emit_gsub, "_assert_fold_sources", lambda rules, tables: None)
    summary = run_m1.run_emitted_order(spec, tables, out_dir)
    assert not summary["pass"]
    error = summary["error"]
    assert "shipped-order disagreement" in error
    assert "emitted rule 0" in error
    assert ".perturbed" in error
    assert "its table's rule" in error
    assert ": row (" in error


def test_the_kernel_interface_refuses_a_missing_enumeration(spec, built, tmp_path):
    out_dir, tables = built
    decision, _joins = tables["default"]
    order = tmp_path / "order.tsv"
    order.write_text(emit_gsub.emitted_order_tsv(spec, tables))
    context = tmp_path / "context.tsv"
    context.write_text(emit_gsub.emitted_context_tsv(spec, "default", decision))
    with pytest.raises(kernel_exec.KernelRunError):
        kernel_exec.replay_emitted(
            tmp_path / "windows-default.tsv.gz",
            config="default",
            table=out_dir / "settlement-default.tsv",
            order=order,
            context=context,
        )


def test_a_marker_tag_is_spelled_as_the_copy_it_names(spec):
    """A table names a raw label another configuration spells differently as a tag (`model.marker_tag_name`): the bare rune, a marker copy, or a locked copy, by the features after `@`. The emitter spells tags whether or not the configuration renames anything, and leaves every other label to the configuration's own renaming."""
    assert model.marker_tag_name("qsTea@") == "qsTea"
    assert model.marker_tag_name("qsTea@ss03_ss05") == "qsTea.ss03_ss05"
    assert model.marker_tag_name("qsTea.noentry@ss03") == "qsTea.ss03.noentry"
    assert model.marker_tag_name("qsTea") is None
    assert model.marker_tag_name("qsTea.full") is None
    rule = model_rule(look1=("qsTea@", "qsPea"), look2=("qsTea",))
    assert emit_gsub._renamed(rule, {}).look1 == ("qsTea", "qsPea")
    renames = model.raw_rename_map(spec, frozenset({"ss03"}))
    renamed = emit_gsub._renamed(rule, renames)
    assert renamed.look1 == ("qsTea", "qsPea")
    assert renamed.look2 == ("qsTea.ss03",)
    untagged = model_rule(look1=("qsPea",))
    assert emit_gsub._renamed(untagged, {}) is untagged


def test_a_tag_the_marker_features_do_not_give_another_configuration_is_refused(spec):
    """The crate writes a tag only for a marker rune whose spelling another configuration sets differently, so the emitter refuses a tag that names a rune without unlock rows, features outside the rune's unlock features, or the features the table's own configuration sets, which the crate writes as the raw label."""
    emit_gsub._check_tags(spec, "default", model_rule(look1=("qsTea@ss03",)))
    emit_gsub._check_tags(spec, "ss03", model_rule(look1=("qsTea@",)))
    for config, tag in (
        ("default", "qsPea@"),
        ("default", "qsTea@ss04"),
        ("ss03", "qsTea@ss03"),
        ("default", "qsTea@"),
    ):
        with pytest.raises(emit_gsub.EmitError, match="marker tag"):
            emit_gsub._check_tags(spec, config, model_rule(look1=(tag,)))


def test_the_fold_keeps_each_rule_at_its_first_occurrence_in_sorted_feature_order():
    """The shipped order's argument (`rebuild/kernel-rs/src/crossconfig.rs`) needs two facts of `emit_gsub._fold_rules`: it visits the configurations sorted by their sorted feature lists, so ss03+ss05 comes before ss05, and a rule two configurations share stays where its first configuration put it, naming both as sources. The crate's `Spellings::rank` mirrors the first fact."""
    shared = model_rule(look1=("qsPea",), outcome="qsTea.full")
    own = {
        config: model_rule(look1=(f"qsTea.{config}",), outcome=f"qsTea.{config}.out")
        for config in ("default", "ss05", "ss03+ss05")
    }
    tables = {
        "ss05": table_of("ss05", (own["ss05"], shared)),
        "default": table_of("default", (own["default"],)),
        "ss03+ss05": table_of("ss03+ss05", (shared, own["ss03+ss05"])),
    }
    folded = emit_gsub._fold_rules(tables)
    assert [rule.outcome for rule in folded] == [
        "qsTea.default.out",
        "qsTea.full",
        "qsTea.ss03+ss05.out",
        "qsTea.ss05.out",
    ]
    assert folded[1].sources == (("ss03+ss05", 0), ("ss05", 1))


def test_a_tag_in_one_table_reaches_the_rows_of_the_configuration_it_names(spec, built):
    """The tag path from a table to the shipped-order walk. A table that imports another configuration's window names that configuration's spelling of a marker rune as a tag (`rebuild/kernel-rs/src/crossconfig.rs`), and the emitter spells the tag as that configuration's copy. The mini fixture's whole build imports nothing, under any of its unlocks with or without their `when:`, so the tag is put into `default`'s table here, on a copy of one of ss03's rules whose lookahead names ss03's copy of ·Tea. Spelled, the copy folds into ss03's own rule as one shipped row, and the walk passes. Narrowed to one left and given another outcome, it ships ahead of ss03's rule and the walk fails on an ss03 row, which only a tag spelled as ss03's copy can reach."""
    out_dir, tables = built
    renames = model.raw_rename_map(spec, frozenset({"ss03"}))
    ss03, _joins = tables["ss03"]
    index, rule = next(
        (index, rule)
        for index, rule in enumerate(ss03.rules)
        if rule.backtrack
        and rule.input_glyph not in renames
        and rule.look1
        and set(rule.look1) & set(renames)
    )

    def tagged(slot):
        if slot is None:
            return None
        return tuple(
            f"{label}{model.MARKER_TAG_SEPARATOR}ss03" if label in renames else label for label in slot
        )

    copy = dataclasses.replace(
        rule,
        look1=tagged(rule.look1),
        look2=tagged(rule.look2),
        look3=tagged(rule.look3),
        look4=tagged(rule.look4),
    )
    default, joins = tables["default"]

    def leading(extra):
        ahead = dataclasses.replace(
            default, rules=(extra, *default.rules), buckets=(ss03.buckets[index], *default.buckets)
        )
        return {**tables, "default": (ahead, joins)}

    folded = emit_gsub.fold_settle_rules(spec, leading(copy))
    shipped = next(row for row in folded if ("default", 0) in row.sources)
    assert ("ss03", index) in shipped.sources
    summary = run_m1.run_emitted_order(spec, leading(copy), out_dir)
    assert summary["pass"], summary["error"]

    wrong = dataclasses.replace(copy, backtrack=rule.backtrack[:1], outcome=f"{rule.input_glyph}.perturbed")
    summary = run_m1.run_emitted_order(spec, leading(wrong), out_dir)
    assert not summary["pass"]
    assert "ss03" in summary["error"] and ".perturbed" in summary["error"]


def test_the_emitter_refuses_a_table_whose_model_of_the_shipped_order_disagrees_with_it(spec, built):
    """The crate's exchange decides each table's imports from where `emit_gsub._fold_rules` and `_ordered_settle_rules` ship every rule (`rebuild/kernel-rs/src/crossconfig.rs`), so a crate-built table carries that model: its configuration's place in the fold order and each rule's sort key. The mini build's tables carry a rank and one key per rule that agree with the emitter, and the emitter refuses a table whose key for one rule, or whose rank, disagrees with the order it ships."""
    _out_dir, tables = built
    for decision, _joins in tables.values():
        assert decision.fold_order == ("default", "ss03", "ss03+ss05", "ss04", "ss05")
        assert len(decision.buckets) == len(decision.rules)
    emit_gsub.fold_settle_rules(spec, tables)
    default, joins = tables["default"]
    flipped = dataclasses.replace(default, buckets=(default.buckets[0] ^ 1, *default.buckets[1:]))
    with pytest.raises(emit_gsub.EmitError, match="bucket"):
        emit_gsub.fold_settle_rules(spec, {**tables, "default": (flipped, joins)})
    ss03, ss03_joins = tables["ss03"]
    swapped = dataclasses.replace(ss03, fold_order=("default", "ss03+ss05", "ss03", "ss04", "ss05"))
    with pytest.raises(emit_gsub.EmitError, match="in that order"):
        emit_gsub.fold_settle_rules(spec, {**tables, "ss03": (swapped, ss03_joins)})


def model_rule(look1=None, look2=None, outcome="qsPea.half"):
    from rebuild.pipeline.table import Rule

    return Rule("qsPea", None, look1, look2, None, None, outcome, (), False)


def table_of(config, rules):
    from rebuild.pipeline.table import DecisionTable

    return DecisionTable(config=config, rules=tuple(rules))
