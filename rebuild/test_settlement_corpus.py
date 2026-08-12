"""The settlement corpora and the differential that consumes them: the artifacts a Rust settlement core is measured against, so what these tests hold is exactly what the harness relies on.

Three claims, in the order they matter. A corpus is a function of the spec and the seed it was cut from — two exports at one HEAD are byte-identical, and so are two fuzz runs at one seed, or a Rust run and a Python run disagree about which cases they ran rather than about how one settled. Every line is a complete call with exactly one answer, raises included, and a raise carries its message: the unreachable bucket holds every plain `SettleError` alongside `EStrandedError`, and only the text tells them apart, so the message is compared byte for byte like everything else. And both piles read through one reader and settle through one replay, because a golden corpus and a fuzz corpus that drifted into two formats would need two Rust readers to match them.

The fired-pointer delta gets its own check because it is the field no other artifact re-derives: a port that settles every window correctly and journals the wrong records still builds a table the dead-policy gate rejects. The trace grain beside it — the deciding stage, the runner-up, the ranked ladder, the eliminations — gets one for the mirror reason: no row shows any of it, so a port can get all of it wrong and still land on the cell this window happens to want. The differential's own arms are checked here too — the guard sweep's order and verdicts, and the replay plumbing against a stub kernel — since the harness has to be trustworthy before the binary it drives exists.
"""

import gzip
import json
from pathlib import Path

import pytest

from rebuild.pipeline import fixtures, kernel_io, settle
from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, UNKNOWN, ZWNJ, Engine, RightToken
from rebuild.pipeline.table import DecisionTable, enumerate_transitions
from rebuild.tools import export_settlement_corpus, fuzz_settlement_corpus, kernel_differential

CONFIG = "default"
FUZZ_CASES = 60
# A settled result's keys, in the order the format fixes them: the row-visible record first, then the trace grain no row shows. Order is contract and not presentation — the Rust side re-emits these bytes and the comparison is byte for byte — so the assertions below read `list(result)`, never `set(result)`.
RESULT_KEYS = [
    "settled",
    "prospect",
    "joint_floor",
    "notes",
    "fired",
    "decided_stage",
    "runner_up",
    "ranked",
    "eliminations",
]
DECIDED_STAGES = {"only-candidate", "absolute-prefer", "join-count", "yielding-prefer", "order", "floor"}


def _is_candidate_row(row: list) -> bool:
    """A candidate as a case line spells it: the stance, its entry and seam heights or nulls, and the two indices the ranking and the floor sort on — the non-joining sentinel among them, which is a number and never a null."""
    stance, entry, seam, order_index, exit_index = row
    return (
        isinstance(stance, str)
        and (entry is None or isinstance(entry, str))
        and (seam is None or isinstance(seam, str))
        and isinstance(order_index, int)
        and isinstance(exit_index, int)
    )


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


@pytest.fixture(scope="module")
def fuzzed(tmp_path_factory):
    """One fuzz corpus per run of the seed, twice over, so byte identity at a seed is assertable; the mini spec keeps it cheap enough to build in the fixture rather than to check in."""
    first = tmp_path_factory.mktemp("fuzz-first")
    second = tmp_path_factory.mktemp("fuzz-second")
    for out_dir in (first, second):
        fuzz_settlement_corpus.main(
            ["--spec", "mini", "--configs", CONFIG, "--cases", str(FUZZ_CASES), "--out", str(out_dir)]
        )
    return (
        fuzz_settlement_corpus.fuzz_path(first, CONFIG, True, True),
        fuzz_settlement_corpus.fuzz_path(second, CONFIG, True, True),
    )


def test_two_exports_of_one_spec_are_byte_identical(exported):
    first, second = exported
    assert first.read_bytes() == second.read_bytes()


def test_the_head_names_the_spec_the_cases_were_cut_from(corpus):
    head, cases = corpus
    assert head["config"] == CONFIG
    assert head["spec"] == "mini"
    assert head["spec_structure_digest"]
    assert cases


def test_the_head_records_the_engine_modes_the_cases_were_cut_under(corpus):
    """A replay builds its engine from these, never from its own defaults: `simulated_prospect` and `vote_slots` are settlement semantics, and a corpus read back without them answers a different question from the one it recorded."""
    head, _cases = corpus
    assert head["modes"] == {"simulated_prospect": True, "vote_slots": True}
    assert export_settlement_corpus.head_modes(head) == (True, True)


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
            assert set(result) == {"raise", "message"}
            assert result["raise"] in {
                export_settlement_corpus.RAISE_INCOMPARABLE,
                export_settlement_corpus.RAISE_AMBIGUOUS,
                export_settlement_corpus.RAISE_UNREACHABLE,
            }
            assert result["message"]
            continue
        assert list(result) == RESULT_KEYS
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


def test_a_settled_case_carries_the_reasoning_and_not_only_the_row(corpus):
    """The grain the row cannot show, and the reason the format moved: a port that eliminates a candidate at the wrong stage, ranks it by the wrong join count, or breaks a tie at the floor where Python broke it at the prefers can still land on the same cell here and diverge at the next window. Key order is asserted because the Rust side re-emits these bytes and the comparison is byte for byte."""
    _head, cases = corpus
    settled = [case["result"] for case in cases if "settled" in case["result"]]
    assert settled
    for result in settled:
        assert list(result) == RESULT_KEYS
        assert result["decided_stage"] in DECIDED_STAGES
        assert result["runner_up"] is None or _is_candidate_row(result["runner_up"])
        assert result["ranked"]
        for row, join_count, prospect in result["ranked"]:
            assert _is_candidate_row(row)
            assert isinstance(join_count, int)
            assert isinstance(prospect, int)
        ladder = [(-join_count, row[3], row[4]) for row, join_count, _prospect in result["ranked"]]
        assert ladder == sorted(ladder)
        assert result["settled"]["cell"][1] in [row[0] for row, _joins, _prospect in result["ranked"]]
        for stage, description, provenance in result["eliminations"]:
            assert stage
            assert description
            assert provenance is None or ":" in provenance
    assert len({result["decided_stage"] for result in settled}) > 1
    assert any(result["runner_up"] is not None for result in settled)
    assert any(result["eliminations"] for result in settled)
    assert any(
        provenance is not None
        for result in settled
        for _stage, _description, provenance in result["eliminations"]
    )


def test_the_strata_reach_the_rare_row_shapes(corpus):
    """The stratum key exists to pull in what a plain key-order prefix never reaches — key order puts boundary lookaheads and `#NA` deep slots first — so the shapes that would otherwise be sampled zero times are asserted present: a flagged joint floor, a non-empty notes list, and a live letter at the fourth slot."""
    _head, cases = corpus
    settled = [case["result"] for case in cases if "settled" in case["result"]]
    assert any(result["joint_floor"] for result in settled)
    assert any(result["notes"] for result in settled)
    assert any(case["right"][3]["kind"] == "letter" for case in cases if "settled" in case["result"])


@pytest.mark.parametrize("marker", ["# ams-m1-windows/2", "# ams-m1-corpus/1", "# ams-m1-corpus/2"])
def test_a_file_that_is_not_a_corpus_of_this_format_is_refused(tmp_path, marker):
    """Every earlier format is refused as flatly as a foreign one: a corpus is regenerated from the spec it measures, never migrated, so a `/1` or `/2` file on disk is a stale file rather than a readable one."""
    path = tmp_path / "elsewhere.ndjson.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(f"{marker}\t{{}}\n")
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


# --- the seeded fuzz corpus ---------------------------------------------------


def test_two_fuzz_runs_at_one_seed_are_byte_identical(fuzzed):
    first, second = fuzzed
    assert first.read_bytes() == second.read_bytes()


def test_the_fuzz_corpus_reads_back_through_the_golden_corpus_reader(fuzzed):
    """One format, one reader, one Rust-side parser: the fuzz pile earns nothing by having a layout of its own, and would cost the port a second reader to match."""
    head, cases = export_settlement_corpus.read_corpus(fuzzed[0])
    assert head["config"] == CONFIG
    assert head["spec"] == "mini"
    assert head["seed"] == fuzz_settlement_corpus.DEFAULT_SEED
    assert head["cases"] == FUZZ_CASES
    assert export_settlement_corpus.head_modes(head) == (True, True)
    assert cases


def test_a_fuzz_case_is_the_same_shape_as_a_golden_one(fuzzed):
    _head, cases = export_settlement_corpus.read_corpus(fuzzed[0])
    for case in cases:
        assert set(case) == {"left", "input", "right", "result"}
        result = case["result"]
        if "raise" in result:
            assert set(result) == {"raise", "message"}
            assert result["message"]
        else:
            assert list(result) == RESULT_KEYS


def test_the_fuzz_arm_reaches_windows_the_enumeration_cannot(fuzzed):
    """The point of drawing from the argument surface rather than the reachable one: unsettleable windows the fixpoint never assembles, which is where the port's refusal paths live."""
    _head, cases = export_settlement_corpus.read_corpus(fuzzed[0])
    assert any("raise" in case["result"] for case in cases)
    assert any("settled" in case["result"] for case in cases)


def test_a_fuzz_raise_carries_the_oracle_s_own_message(fuzzed):
    """The message is `str(error)` from the kernel and nothing rephrased, so the differential's byte comparison is against the oracle's text — re-raising the same call has to reproduce it exactly."""
    _head, cases = export_settlement_corpus.read_corpus(fuzzed[0])
    spec = fixtures.mini_spec()
    engine = Engine(spec, frozenset(), trace_memo=True)
    raising = [case for case in cases if "raise" in case["result"]]
    assert raising
    for case in raising:
        left, token, rights = _call_of(case)
        result, raised = export_settlement_corpus._replay(engine, left, token, rights)
        assert raised
        assert result == case["result"]


def test_the_modes_a_fuzz_run_was_asked_for_reach_the_head_and_the_engine(tmp_path):
    """The pinned candidacy world is a whole second settlement semantics, not a sampling knob: what the head records has to be what the engine ran, or a replay reconstructs the wrong engine from it."""
    fuzz_settlement_corpus.main(
        [
            "--spec",
            "mini",
            "--configs",
            CONFIG,
            "--cases",
            "20",
            "--simulated-prospect",
            "0",
            "--vote-slots",
            "0",
            "--out",
            str(tmp_path),
        ]
    )
    path = fuzz_settlement_corpus.fuzz_path(tmp_path, CONFIG, False, False)
    head, cases = export_settlement_corpus.read_corpus(path)
    assert export_settlement_corpus.head_modes(head) == (False, False)
    assert cases


def test_the_two_corpora_settle_through_one_replay():
    """Not a style preference: two replay paths would let the piles disagree about what a case's fired delta is, and the delta is the field the differential is most valuable for."""
    assert fuzz_settlement_corpus._replay is export_settlement_corpus._replay


def test_a_fuzz_draw_uses_only_heights_the_stance_it_names_declares():
    """A fuzzed left is a wide call, not a malformed one — the port has to answer it, so it must be a call the kernel could receive: real runes, real stances, and seams and entries those stances actually declare."""
    spec = fixtures.mini_spec()
    engine = Engine(spec, frozenset(), trace_memo=True)
    for case in fuzz_settlement_corpus.fuzz_cases(spec, engine, 120, fuzz_settlement_corpus.DEFAULT_SEED):
        left = case["left"]
        if left["kind"] != "letter":
            assert left["settled"] is None
            continue
        rune, stance, entry, exit_, adjustments = left["settled"]["cell"]
        surface = spec.runes[rune].stances[stance].surface
        assert entry is None or entry in surface.entries
        assert exit_ is None or exit_ in surface.exits
        assert exit_ == left["settled"]["seam"]
        assert adjustments == []
        assert (left["settled"]["extension"] == 0) or exit_ is not None


# --- the differential harness -------------------------------------------------


def _call_of(case: dict):
    """A case line read back as the call it records — the Python mirror of what the Rust `cases.rs` parses, kept in the tests because the harness itself never needs it: the kernel replays cases, Python only ever wrote them."""
    boundaries = {"edge": EDGE, "space": SPACE, "zwnj": ZWNJ, "namer-dot": NAMER_DOT, "unknown": UNKNOWN}

    def token(row: dict) -> RightToken:
        return RightToken("letter", row["letter"]) if row["kind"] == "letter" else boundaries[row["kind"]]

    left_row = case["left"]
    if left_row["settled"] is None:
        left = settle.LeftContext(left_row["kind"])
    else:
        rune, stance, entry, exit_, adjustments = left_row["settled"]["cell"]
        cell = settle.CellId(rune, stance, entry, exit_, tuple(adjustments))
        left = settle.LeftContext(
            "letter",
            settle.Settled(cell, left_row["settled"]["seam"], left_row["settled"]["extension"]),
        )
    return left, RightToken("letter", case["input"]), tuple(token(row) for row in case["right"])


def _fuzz_bed(tmp_path: Path) -> tuple[Path, Path]:
    """A small fuzz corpus and the spec dump beside it — the two files every replay comparison needs."""
    spec = fixtures.mini_spec()
    path, _count = fuzz_settlement_corpus.fuzz_config(
        spec, "mini", CONFIG, tmp_path, 25, fuzz_settlement_corpus.DEFAULT_SEED, True, True
    )
    spec_path = tmp_path / "spec-mini.json"
    kernel_io.write_spec(spec, spec_path)
    return path, spec_path


def _stub_kernel(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stub-kernel"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


ECHO_STUB = 'if [ "$1" = "settle-cases" ]; then tail -n +2 "$3"; exit 0; fi\nexit 2'
MANGLE_STUB = (
    'if [ "$1" = "settle-cases" ]; then tail -n +2 "$3" | sed \'1s/"input"/"inpuT"/\'; exit 0; fi\nexit 2'
)


def test_the_guard_sweep_walks_every_triple_in_the_verbs_own_order():
    """The sweep is exhaustive rather than sampled because the verdict is a pure function of two raw slots. Order is contract, not presentation: the Rust verb prints its lines in this order and the comparison is positional."""
    spec = fixtures.mini_spec()
    letters = sorted(spec.runes)
    ligatures = sorted(name for name, rune in spec.runes.items() if rune.sequence)
    lines = kernel_differential.guard_lines(spec)
    assert ligatures
    assert len(lines) == len(ligatures) * len(letters) * (len(letters) + 5)
    fields = [line.split("\t") for line in lines]
    assert [row[0] for row in fields] == sorted(row[0] for row in fields)
    assert {row[3] for row in fields} <= {"blocked", "free"}
    assert [row[2] for row in fields[: len(letters) + 5]] == [
        *letters,
        "edge",
        "space",
        "zwnj",
        "namer-dot",
        "unknown",
    ]


def test_every_guard_line_states_the_verdict_the_kernel_computes():
    spec = fixtures.mini_spec()
    tokens = {"edge": EDGE, "space": SPACE, "zwnj": ZWNJ, "namer-dot": NAMER_DOT, "unknown": UNKNOWN}
    for line in kernel_differential.guard_lines(spec):
        liga, right1, right2, verdict = line.split("\t")
        token2 = tokens.get(right2) or RightToken("letter", right2)
        blocked = settle.formation_blocked(spec, liga, RightToken("letter", right1), token2)
        assert verdict == ("blocked" if blocked else "free"), line


def test_the_replay_flags_follow_the_head_rather_than_the_callers_defaults():
    """Features come from the configuration the head names and the mode flags are only passed when a mode is off, so the shipping default configuration invokes the verb with no flags at all."""
    assert (
        kernel_differential._replay_flags(
            {"config": "default", "modes": {"simulated_prospect": True, "vote_slots": True}}
        )
        == []
    )
    assert kernel_differential._replay_flags(
        {"config": "ss03+ss05", "modes": {"simulated_prospect": False, "vote_slots": False}}
    ) == ["--features=ss03,ss05", "--candidacy-prospect", "--vote-slots-off"]


def test_a_kernel_that_echoes_the_cases_back_diverges_nowhere(tmp_path, monkeypatch, capsys):
    """The plumbing under the comparison, proved without the verbs existing: the corpus is gunzipped to the plain ndjson the verb reads, the head line is there for the kernel to skip, and the case lines it hands back are compared as bytes."""
    monkeypatch.setattr(kernel_differential, "BINARY", _stub_kernel(tmp_path, ECHO_STUB))
    corpus_file, spec_path = _fuzz_bed(tmp_path)
    compared, divergences = kernel_differential.replay_corpus(spec_path, corpus_file, tmp_path, "stub")
    capsys.readouterr()
    assert compared > 0
    assert divergences == 0


def test_one_mangled_line_is_reported_as_one_divergence(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kernel_differential, "BINARY", _stub_kernel(tmp_path, MANGLE_STUB))
    corpus_file, spec_path = _fuzz_bed(tmp_path)
    _compared, divergences = kernel_differential.replay_corpus(spec_path, corpus_file, tmp_path, "stub")
    printed = capsys.readouterr().out
    assert divergences == 1
    assert "diverged" in printed
    assert "the kernel re-emitted a different input" in printed


def test_the_fuzz_arm_runs_end_to_end_against_a_stub_kernel(tmp_path, monkeypatch, capsys):
    """The harness's own wiring — spec dump, fuzz generation per mode combination, invocation, comparison, exit status — with only the verbs stubbed out."""
    monkeypatch.setattr(kernel_differential, "BINARY", _stub_kernel(tmp_path, ECHO_STUB))
    status = kernel_differential.main(["--specs", "mini", "--skip-corpus", "--skip-guard", "--cases", "12"])
    printed = capsys.readouterr().out
    assert status == 0
    assert "lines identical" in printed
    assert printed.count("OK") == len(kernel_differential.MODE_COMBINATIONS)


def test_a_kernel_without_the_verb_fails_cleanly_rather_than_diffing_against_nothing(
    tmp_path, monkeypatch, capsys
):
    """The harness lands before the verbs do, so the target has to be wired and testable meanwhile: exit 2 from the usage check is the verb being absent, and it reads as one line rather than as every case diverging."""
    monkeypatch.setattr(kernel_differential, "BINARY", _stub_kernel(tmp_path, "exit 2"))
    status = kernel_differential.main(["--specs", "mini", "--skip-corpus", "--skip-guard", "--cases", "8"])
    assert status == 1
    assert "kernel does not support settle-cases yet" in capsys.readouterr().err


def test_the_gunzipped_case_file_is_what_the_verb_contract_describes(tmp_path):
    """Guards the stubbed tests above from passing vacuously: the bed's own files have to be the ones the verb reads — a plain-text spec dump, and a plain-text case file whose first line is the head the kernel skips and whose remaining lines are the corpus's own bytes."""
    corpus_file, spec_path = _fuzz_bed(tmp_path)
    _head, marker, lines = kernel_differential.read_case_lines(corpus_file)
    plain = kernel_differential._plain_copy(marker, lines, tmp_path / "cases.ndjson")
    text = plain.read_text().splitlines()
    assert spec_path.read_text().startswith('{"format":"ams-m1-spec/1"')
    assert text[0] == marker
    assert text[0].startswith(f"# {export_settlement_corpus.CORPUS_FORMAT}\t")
    assert text[1:] == lines
    assert all(set(json.loads(line)) == {"left", "input", "right", "result"} for line in lines)
