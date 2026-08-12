"""The golden single-window settlement corpus: the artifact a Rust settlement core is differentially tested against, so what these tests hold is exactly what a differential harness relies on — that a corpus is a function of the spec it was cut from (two exports at one HEAD are byte-identical, or a Rust run and a Python run disagree about which cases they ran), that every line is a complete call with exactly one answer, and that both answer shapes are actually represented, raises included. The fired-pointer delta gets its own check because it is the field no other artifact re-derives: a port that settles every window correctly and journals the wrong records still builds a table the dead-policy gate rejects."""

import gzip
import json

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline.settle import Engine, RightToken
from rebuild.pipeline.table import DecisionTable, enumerate_transitions
from rebuild.tools import export_settlement_corpus

CONFIG = "default"


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    first = tmp_path_factory.mktemp("corpus-first")
    second = tmp_path_factory.mktemp("corpus-second")
    for out_dir in (first, second):
        export_settlement_corpus.main(
            ["--spec", "mini", "--configs", CONFIG, "--out", str(out_dir), "--per-group", "2"]
        )
    return (
        export_settlement_corpus.corpus_path(first, CONFIG),
        export_settlement_corpus.corpus_path(second, CONFIG),
    )


@pytest.fixture(scope="module")
def corpus(exported):
    return export_settlement_corpus.read_corpus(exported[0])


def test_two_exports_of_one_spec_are_byte_identical(exported):
    first, second = exported
    assert first.read_bytes() == second.read_bytes()


def test_the_head_names_the_spec_the_cases_were_cut_from(corpus):
    head, cases = corpus
    assert head["config"] == CONFIG
    assert head["spec"] == "mini"
    assert head["spec_structure_digest"]
    assert cases


def test_every_case_is_a_whole_call_with_exactly_one_answer(corpus):
    _head, cases = corpus
    for case in cases:
        assert set(case) == {"left", "input", "right", "result"}
        assert set(case["left"]) == {"kind", "settled"}
        assert (case["left"]["settled"] is not None) == (case["left"]["kind"] == "letter")
        assert isinstance(case["input"], str)
        assert len(case["right"]) == 4
        for token in case["right"]:
            assert set(token) == {"kind", "letter"}
            assert (token["letter"] is not None) == (token["kind"] == "letter")
        result = case["result"]
        if "raise" in result:
            assert set(result) == {"raise"}
            assert result["raise"] in {
                export_settlement_corpus.RAISE_INCOMPARABLE,
                export_settlement_corpus.RAISE_AMBIGUOUS,
                export_settlement_corpus.RAISE_UNREACHABLE,
            }
            continue
        assert set(result) == {"settled", "prospect", "joint_floor", "notes", "fired"}
        assert set(result["settled"]) == {"cell", "seam", "extension"}
        assert len(result["settled"]["cell"]) == 5
        assert isinstance(result["joint_floor"], bool)


def test_the_lines_are_sorted_and_deduplicated(exported, corpus):
    _head, cases = corpus
    lines = [json.dumps(case, separators=(",", ":")) for case in cases]
    assert lines == sorted(lines)
    assert len(set(lines)) == len(lines)


def test_both_answer_shapes_are_represented(corpus):
    """The raising arm has to find its own cases — no enumerated row reaches an unsettleable window — so the corpus is only as good as the virtual-left probing, and a silent zero there would leave the Rust core's error paths untested."""
    _head, cases = corpus
    assert any("raise" in case["result"] for case in cases)
    assert any("settled" in case["result"] for case in cases)


def test_a_settled_case_carries_the_records_its_evaluation_fired(corpus):
    _head, cases = corpus
    assert any(case["result"].get("fired") for case in cases)


def test_the_strata_reach_the_rare_row_shapes(corpus):
    """The stratum key exists to pull in what a plain key-order prefix never reaches — key order puts boundary lookaheads and `#NA` deep slots first — so the shapes that would otherwise be sampled zero times are asserted present: a flagged joint floor, a non-empty notes list, and a live letter at the fourth slot."""
    _head, cases = corpus
    settled = [case["result"] for case in cases if "settled" in case["result"]]
    assert any(result["joint_floor"] for result in settled)
    assert any(result["notes"] for result in settled)
    assert any(case["right"][3]["kind"] == "letter" for case in cases if "settled" in case["result"])


def test_a_file_that_is_not_a_corpus_is_refused(tmp_path):
    path = tmp_path / "elsewhere.ndjson.gz"
    with gzip.open(path, "wt") as handle:
        handle.write("# ams-m1-windows/2\t{}\n")
    with pytest.raises(ValueError):
        export_settlement_corpus.read_corpus(path)


def test_a_replayed_window_reproduces_the_record_the_fixpoint_enumerated():
    """The reconstruction is the corpus's whole claim to being the oracle's answers rather than a neighboring engine's: a boundary label read back as its token, a deep class id read back as its representative, and a `#NA` slot read back as EDGE must hand the kernel the call the enumeration made. Stated over every enumerated row, not the sample, so a shape the sampler happens to skip today cannot rot unnoticed."""
    spec = fixtures.mini_spec()
    features = frozenset()
    product = enumerate_transitions(spec, features)
    engine = Engine(spec, features, trace_memo=True)
    decision = DecisionTable(config=product.config, deep_classes=product.deep_classes)
    for row in product.transitions:
        left = export_settlement_corpus._left_of(row)
        rights = tuple(
            export_settlement_corpus._token_of(label, decision)
            for label in (row.right1, row.right2, row.right3, row.right4)
        )
        token = RightToken("letter", row.input_glyph.split(".")[0])
        result, raised = export_settlement_corpus._replay(engine, left, token, rights)
        assert not raised, row.key
        assert result["settled"] == export_settlement_corpus._settled_row(row.settled), row.key
        assert result["prospect"] == row.prospect, row.key
        assert result["joint_floor"] == row.joint, row.key
        assert result["notes"] == list(row.provenance), row.key
