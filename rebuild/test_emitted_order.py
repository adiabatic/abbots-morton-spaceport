"""The shipped settlement order held to the tables: every configuration's rows walked first-match against the order `emit_gsub._ordered_settle_rules` ships, by the crate's `replay-emitted` verb behind `run_m1.run_emitted_order`. The stage lives in the build because the order is a fact about exactly the tables it folded from; what this module holds is the machinery's own contract on the mini fixture — the order file and the context files the crate reads, the stage's summary over tables the fixture built, and that an order which answers a row differently from its table is refused naming the row and the rule."""

import dataclasses

import pytest

from rebuild.pipeline import conform, emit_gsub, fixtures, kernel_exec, run_m1

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


def test_the_context_file_carries_the_marker_fold_and_the_deep_classes(spec, built):
    _out_dir, tables = built
    decision, _treaty = tables["ss03"]
    lines = emit_gsub.emitted_context_tsv(spec, "ss03", decision).splitlines()
    renames = {raw: twin for kind, raw, twin in (line.split("\t") for line in lines) if kind == "rename"}
    assert renames == emit_gsub._raw_rename_map(spec, frozenset({"ss03"}))
    assert renames and all(
        twin == f"{raw.split('.')[0]}.ss03{raw[len(raw.split('.')[0]):]}" for raw, twin in renames.items()
    )
    classed = dataclasses.replace(decision, deep_classes={"#Cabc": ("qsPea", "qsTea")})
    assert "class\t#Cabc\tqsPea qsTea" in emit_gsub.emitted_context_tsv(spec, "ss03", classed).splitlines()
    plain = emit_gsub.emitted_context_tsv(spec, "default", decision).splitlines()
    assert not [line for line in plain if line.startswith("rename\t")]
    assert [line.split("\t")[1] for line in plain] == sorted(decision.deep_classes)


def test_the_stage_answers_every_row_of_every_configuration(spec, built):
    out_dir, tables = built
    summary = run_m1.run_emitted_order(spec, tables, out_dir)
    assert summary["pass"], summary["complaint"]
    assert list(summary["configs"]) == list(conform.SETTLEMENT_CONFIGS)
    assert summary["rules"] == len(emit_gsub.fold_settle_rules(spec, tables))
    for config, (decision, _treaty) in tables.items():
        assert summary["configs"][config]["rows"] > 0
        assert summary["configs"][config]["rows"] >= len(decision.rules)
    assert (out_dir / run_m1.EMITTED_ORDER_SUMMARY).is_file()


def test_an_order_that_answers_a_row_differently_is_refused_naming_the_row(spec, built, monkeypatch):
    """A rule pushed to the head of the shipped order with an outcome no table wrote: the first row it admits is answered wrongly, and the stage names the configuration, the row, the emitted rule that fired and the table's own rule."""
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
    complaint = summary["complaint"]
    assert "shipped-order disagreement" in complaint
    assert "emitted rule 0" in complaint
    assert ".perturbed" in complaint
    assert "its table's rule" in complaint
    assert ": row (" in complaint


def test_the_seam_refuses_a_missing_enumeration(spec, built, tmp_path):
    out_dir, tables = built
    decision, _treaty = tables["default"]
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
