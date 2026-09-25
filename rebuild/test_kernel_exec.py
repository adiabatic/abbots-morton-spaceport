"""Tests for `rebuild/pipeline/kernel_exec.py`, the Python side of the kernel boundary, and for the `run_m1` code that calls it: the mode flags passed to the crate, the product and tables it returns, the memo seed, the thread widths, the CLI, and the string replay. Every table is built on the mini fixture, which is enough to check the shape of the results; enumerating the live alphabet is the build's job.

No test skips. On a machine without `cargo` these tests fail with the remedy `KernelBuildError` carries, because the M1 build cannot run there either.
"""

import gzip
import itertools
import json
from collections import OrderedDict
from dataclasses import replace

import pytest

from rebuild.pipeline import (
    conform,
    fixtures,
    kernel_exec,
    kernel_io,
    oracle_cache,
    run_m1,
    settle,
    spec_load,
)
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import CellId, Settled
from rebuild.pipeline.settle import (
    EDGE,
    NAMER_DOT,
    SPACE,
    UNKNOWN,
    ZWNJ,
    LeftContext,
    RightToken,
    SettleError,
)

SPEC = fixtures.mini_spec()
STAMP = "kernel-pinned-stamp"
CONFIGS = {"default": frozenset(), "ss03": frozenset({"ss03"}), "ss04": frozenset({"ss04"})}


class Reached(Exception):
    """Raised from a stubbed stage to end a run the moment the arguments under test have arrived."""


@pytest.fixture(scope="module")
def products():
    return {name: kernel_exec.enumerate_transitions(SPEC, features) for name, features in CONFIGS.items()}


class TestTheInvocationSeam:
    def test_the_world_flags_reflect_the_python_side_defaults(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        assert kernel_exec.world_flags() == []
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.world_flags() == [flag for flag, _module, _attribute in kernel_exec.WORLD_FLAGS]

    def test_one_default_switched_off_carries_one_flag(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        flag, module, attribute = kernel_exec.WORLD_FLAGS[1]
        monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.world_flags() == [flag]

    def test_settlement_flags_exclude_the_enumerations_deep_grain(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.settlement_flags() == ["--candidacy-prospect", "--vote-slots-off"]
        assert "--deep-classes-off" not in kernel_exec.settlement_flags()

    def test_settle_cases_batches_questions_with_canonical_features_and_modes(self, monkeypatch, tmp_path):
        """The cases file holds only the question lines, one per line. The argv carries the sorted feature list and the world flags. Each answer line is its question, a tab, and the answer, which decodes to parsed JSON by default."""
        question = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        calls = []

        class Finished:
            returncode = 0
            stdout = (question + '\t{"settled":"trace"}\n').encode()
            stderr = b""

        def run(arguments, verb):
            calls.append((arguments, verb))
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        got = kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.tsv",
            [question],
            frozenset({"ss05", "ss03"}),
        )
        assert got == [{"settled": "trace"}]
        assert (tmp_path / "cases.tsv").read_text() == question + "\n"
        arguments = calls[0][0]
        assert arguments[1:4] == [
            "settle-cases",
            str(tmp_path / "spec.json"),
            str(tmp_path / "cases.tsv"),
        ]
        assert "--features=ss03,ss05" in arguments
        assert "--candidacy-prospect" in arguments
        assert "--vote-slots-off" in arguments
        assert "--deep-classes-off" not in arguments
        assert "--settled-only" not in arguments

    def test_settle_cases_refuses_an_answer_to_a_different_question(self, monkeypatch, tmp_path):
        """Each answer line must begin with its question's exact bytes followed by a tab. An answer to another question fails, and so does an answer with no tab after its question."""
        question = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        changed = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsIt"), (EDGE,) * 4)
        for stdout in (changed + "\t{}\n", question + "{}\n", question + "\n"):

            class Finished:
                returncode = 0
                stderr = b""

                def __init__(self, stdout: bytes) -> None:
                    self.stdout = stdout

            monkeypatch.setattr(
                kernel_exec, "_run_kernel", lambda *args, out=stdout, **kwargs: Finished(out.encode())
            )
            with pytest.raises(kernel_exec.KernelRunError, match="changed"):
                kernel_exec._settle_cases(
                    tmp_path / "spec.json",
                    tmp_path / "cases.tsv",
                    [question],
                    frozenset(),
                )

    def test_a_missing_binary_names_the_recipe_that_builds_one(self, monkeypatch, tmp_path):
        monkeypatch.setattr(kernel_exec, "BINARY", tmp_path / "ams-m1-kernel")
        with pytest.raises(kernel_exec.KernelRunError) as complaint:
            kernel_exec.enumerate_configs(
                tmp_path / "spec.json", tmp_path / "streams", ["default"], threads=1
            )
        assert "make kernel-build" in str(complaint.value)

    def test_a_box_without_cargo_names_the_remedy(self, monkeypatch):
        def absent(*arguments, **rest):
            raise FileNotFoundError("cargo")

        monkeypatch.setattr(kernel_exec.subprocess, "run", absent)
        with pytest.raises(kernel_exec.KernelBuildError) as complaint:
            kernel_exec.cargo_build()
        assert "Rust toolchain" in str(complaint.value)

    def test_the_crate_is_built_once_per_process(self, monkeypatch):
        """`ensure_built` runs `cargo_build` once per process. `_BUILT` is a module attribute so a test can reset it."""
        builds = []
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        monkeypatch.setattr(kernel_exec, "cargo_build", lambda: builds.append(1))
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        assert builds == [1]

    def test_a_named_mode_overrides_the_processs_own_world(self, monkeypatch, tmp_path):
        """A `SettlementModes` passed to `_settle_cases` overrides the module defaults in both directions. With every default on, modes that turn both off add `--candidacy-prospect` and `--vote-slots-off`. With every default off, modes that turn both on add no flag."""
        question = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        calls = []

        class Finished:
            returncode = 0
            stdout = (question + '\t{"settled":"trace"}\n').encode()
            stderr = b""

        def run(arguments, verb):
            calls.append(arguments)
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.tsv",
            [question],
            frozenset(),
            kernel_exec.SettlementModes(simulated_prospect=False, vote_slots=False),
        )
        assert calls[0][4:] == ["--candidacy-prospect", "--vote-slots-off"]
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.tsv",
            [question],
            frozenset(),
            kernel_exec.SettlementModes(simulated_prospect=True, vote_slots=True),
        )
        assert calls[1][4:] == []

    def test_a_refused_window_carries_the_crates_bucket_and_sentence(self, monkeypatch):
        """A crate refusal is `{raise, message}`. The caller gets a `SettleError` whose bucket is the `raise` value and whose message is the crate's message verbatim. It is not a `KernelRunError`, which is reserved for a failure of the boundary itself."""
        question = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        message = "E-STRANDED: qsPea.half.ex-y5 committed an exit at x-height but qsTea has no acceptor cell"
        refusal = json.dumps({"raise": "E-UNREACHABLE", "message": message}, separators=(",", ":"))

        class Finished:
            returncode = 0
            stdout = (question + "\t" + refusal + "\n").encode()
            stderr = b""

        monkeypatch.setattr(kernel_exec, "_run_kernel", lambda *arguments, **rest: Finished())
        with pytest.raises(SettleError) as complaint:
            kernel_exec.settle_windows(SPEC, [question], frozenset())
        assert complaint.value.bucket == "E-UNREACHABLE"
        assert str(complaint.value) == message
        assert not isinstance(complaint.value, kernel_exec.KernelRunError)
        with pytest.raises(SettleError) as traced:
            kernel_exec.settle_cases(SPEC, [question], frozenset(), decode=kernel_exec.trace_of)
        assert traced.value.bucket == "E-UNREACHABLE"
        assert str(traced.value) == message

    def test_settled_only_rides_the_argv_settle_windows_builds_and_no_other(self, monkeypatch):
        """Only `settle_windows` passes `--settled-only` and gets the seven-field answer. `settle_cases` and `settle_sequences` get the full trace, because their callers need the explain ladder."""
        question = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        record = {"cell": ["qsMay", "full", None, None, []], "seam": None, "extension": 0}
        trace = {
            "settled": record,
            "prospect": 0,
            "joint_floor": False,
            "notes": [],
            "fired": [],
            "decided_stage": "only-candidate",
            "runner_up": None,
            "ranked": [],
            "eliminations": [],
        }
        calls = []

        def run(arguments, verb):
            calls.append(arguments)
            answer = (
                "qsMay\tfull\t\t\t\t\t0"
                if "--settled-only" in arguments
                else json.dumps(trace, separators=(",", ":"))
            )

            class Finished:
                returncode = 0
                stdout = (question + "\t" + answer + "\n").encode()
                stderr = b""

            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        settled = kernel_exec.settle_windows(SPEC, [question], frozenset())[0]
        assert settled is not None and settled.cell.rune == "qsMay"
        assert kernel_exec.settle_cases(SPEC, [question], frozenset())[0] == trace
        traces = kernel_exec.settle_sequences(SPEC, [((RightToken("letter", "qsMay"),), frozenset())])[0]
        assert traces is not None and traces[0].settled is settled
        assert ["--settled-only" in arguments for arguments in calls] == [True, False, False]

    def test_the_settled_only_answer_is_the_traces_own_settled_record(self):
        """Through the real binary over the mini alphabet: for every window of every text up to length three, `settle_windows` returns the same `Settled` object that `settle_sequences` reads from that window's trace. Checking identity, not equality, shows that both decoders intern into one table."""
        guard = kernel_exec.guard_sweep(SPEC)
        alphabet = sorted(ch for ch in conform.spec_alphabet(SPEC) if ord(ch) >= 0xE650)
        texts = [
            "".join(combo) for length in (1, 2, 3) for combo in itertools.product(alphabet, repeat=length)
        ]
        requests = [
            (
                settle.form_ligatures(
                    SPEC, settle.tokens_from_codepoints(SPEC, [ord(ch) for ch in text]), guard
                ),
                frozenset(),
            )
            for text in texts
        ]
        traced = kernel_exec.settle_sequences(SPEC, requests)
        cases = []
        expected = []
        for (tokens, _features), traces in zip(requests, traced):
            assert traces is not None
            left = LeftContext("edge")
            for position, (token, trace) in enumerate(zip(tokens, traces)):
                rights = tuple(
                    tokens[index] if index < len(tokens) else EDGE
                    for index in range(position + 1, position + 5)
                )
                cases.append(kernel_exec.case_line(left, token, rights))
                expected.append(trace.settled)
                left = LeftContext("letter", trace.settled)
        settled = kernel_exec.settle_windows(SPEC, cases, frozenset())
        assert len(settled) == len(expected) > len(texts)
        assert all(got is want for got, want in zip(settled, expected))

    def test_a_forged_left_record_survives_the_question_line_both_ways(self):
        """The seven left-record fields pass through the question line intact. A left with adjustments, a seam, and a nonzero extension comes back as the same record under both answer formats. Where the crate refuses such a left, the E-STRANDED message, the only place the left's full `cell_label` is written out with its adjustments, is identical under both formats and names every adjustment."""
        joined = LeftContext(
            "letter",
            Settled(
                CellId("qsMay", "full", None, "x-height", ("locked", "en-ext-1")),
                seam="x-height",
                extension=1,
            ),
        )
        case = kernel_exec.case_line(joined, RightToken("letter", "qsIt"), (EDGE,) * 4)
        assert case.split("\t")[: kernel_exec.CASE_INPUT_FIELD] == [
            "letter",
            "qsMay",
            "full",
            "",
            "x-height",
            "locked,en-ext-1",
            "x-height",
            "1",
        ]
        assert case.split("\t")[kernel_exec.CASE_INPUT_FIELD] == "qsIt"
        settled = kernel_exec.settle_windows(SPEC, [case], frozenset())[0]
        traced = kernel_exec.settle_cases(SPEC, [case], frozenset(), decode=kernel_exec.trace_of)[0]
        assert settled is traced.settled
        stranded = LeftContext(
            "letter",
            Settled(CellId("qsTea", "full", None, "top", ("locked", "en-ext-1")), seam="top", extension=1),
        )
        case = kernel_exec.case_line(stranded, RightToken("letter", "qsIt"), (EDGE,) * 4)
        with pytest.raises(SettleError) as fields:
            kernel_exec.settle_windows(SPEC, [case], frozenset())
        with pytest.raises(SettleError) as trace:
            kernel_exec.settle_cases(SPEC, [case], frozenset(), decode=kernel_exec.trace_of)
        assert str(fields.value) == str(trace.value)
        assert fields.value.bucket == trace.value.bucket == "E-UNREACHABLE"
        assert "locked" in str(fields.value) and "en-ext-1" in str(fields.value)

    def test_settle_windows_answers_one_settled_per_case_in_the_order_asked(self, monkeypatch):
        """`settle_windows` returns one `Settled` per case, in the order given, and splits the cases into invocations of at most `batch` windows (`SETTLE_WINDOW_BATCH` by default)."""
        sizes = []
        original = kernel_exec._settle_cases

        def recording(spec_path, cases_path, cases, features, modes=None, decode=None, settled_only=False):
            sizes.append(len(cases))
            assert settled_only
            return original(spec_path, cases_path, cases, features, modes, decode, settled_only)

        monkeypatch.setattr(kernel_exec, "_settle_cases", recording)
        names = ("qsMay", "qsIt", "qsTea", "qsDay", "qsOy")
        cases = [
            kernel_exec.case_line(LeftContext("edge"), RightToken("letter", name), (EDGE,) * 4)
            for name in names
        ]
        settled = kernel_exec.settle_windows(SPEC, cases, frozenset(), batch=2)
        assert [None if outcome is None else outcome.cell.rune for outcome in settled] == list(names)
        assert sizes == [2, 2, 1]

    def test_settle_windows_can_answer_none_for_a_refusal_and_keep_the_batch(self, monkeypatch):
        """With `on_error="drop"`, a refused case gets `None` in its slot and every other case decodes as usual, in order. This lets a caller prefill windows it may never read without one refusal failing the batch. The stubbed answers are settled-only tab records with one JSON refusal among them, so the test also checks that a refusal is recognized by its leading brace."""
        names = ("qsMay", "qsIt", "qsTea")
        cases = [
            kernel_exec.case_line(LeftContext("edge"), RightToken("letter", name), (EDGE,) * 4)
            for name in names
        ]
        results = [f"{name}\tfull\t\t\t\t\t0" for name in names]
        results[1] = json.dumps(
            {"raise": "E-AMBIGUOUS", "message": "qsIt: two candidates tie at every stage"},
            separators=(",", ":"),
        )

        class Finished:
            returncode = 0
            stdout = ("".join(f"{case}\t{result}\n" for case, result in zip(cases, results))).encode()
            stderr = b""

        monkeypatch.setattr(kernel_exec, "_run_kernel", lambda *arguments, **rest: Finished())
        settled = kernel_exec.settle_windows(SPEC, cases, frozenset(), on_error="drop")
        assert [None if outcome is None else outcome.cell.rune for outcome in settled] == [
            "qsMay",
            None,
            "qsTea",
        ]
        with pytest.raises(SettleError):
            kernel_exec.settle_windows(SPEC, cases, frozenset())

    def test_settle_sequences_drops_only_the_sequence_that_refused(self, monkeypatch):
        """Under `on_error="drop"`, a refusal partway through a sequence drops only that sequence. The other sequences in the same wave settle every position, and the result keeps its order, with `None` in the dropped sequence's slot."""
        requests = [
            ((RightToken("letter", "qsMay"), RightToken("letter", "qsIt")), frozenset()),
            ((RightToken("letter", "qsIt"), RightToken("letter", "qsMay")), frozenset()),
            ((RightToken("letter", "qsMay"), RightToken("letter", "qsTea")), frozenset()),
        ]
        original = kernel_exec.trace_of
        calls = []

        def refusing_at_the_second_wave(result):
            calls.append(result)
            if len(calls) == len(requests) + 1:
                raise SettleError("the second wave's first window will not settle", "E-INCOMPARABLE")
            return original(result)

        monkeypatch.setattr(kernel_exec, "trace_of", refusing_at_the_second_wave)
        traces = kernel_exec.settle_sequences(SPEC, requests, on_error="drop")
        assert traces[0] is None
        assert [None if answer is None else len(answer) for answer in traces] == [None, 2, 2]
        monkeypatch.setattr(kernel_exec, "trace_of", original)
        assert [len(answer or ()) for answer in kernel_exec.settle_sequences(SPEC, requests)] == [2, 2, 2]

    def test_one_spec_is_dumped_once_however_many_calls_read_it(self, monkeypatch):
        """The guard sweep and every settlement batch for one spec read one `spec.json`, written once per process."""
        dumps = []
        original = kernel_exec.kernel_io.write_spec

        def counting(spec, path):
            dumps.append(path)
            return original(spec, path)

        monkeypatch.setattr(kernel_exec, "_SPEC_DUMPS", OrderedDict())
        monkeypatch.setattr(kernel_exec, "_GUARD_SWEEPS", OrderedDict())
        monkeypatch.setattr(kernel_exec.kernel_io, "write_spec", counting)
        guard = kernel_exec.guard_sweep(SPEC)
        settled = kernel_exec.settle_codepoints(SPEC, [0xE665, 0xE670], frozenset(), guard)
        assert [outcome.cell.rune for outcome in settled] == ["qsMay", "qsIt"]
        assert len(dumps) == 1

    def test_a_second_sweep_of_one_spec_runs_no_second_process(self, monkeypatch):
        """`guard_sweep` memoizes on spec identity: repeated calls with one spec run one `guard-sweep` invocation, and a second spec object that is equal to the first gets its own invocation."""
        sweeps = []
        original = kernel_exec._guard_verdicts

        def counting(spec, spec_path):
            sweeps.append(spec_path)
            return original(spec, spec_path)

        monkeypatch.setattr(kernel_exec, "_GUARD_SWEEPS", OrderedDict())
        monkeypatch.setattr(kernel_exec, "_guard_verdicts", counting)
        first = kernel_exec.guard_sweep(SPEC)
        assert kernel_exec.guard_sweep(SPEC) is first
        assert len(sweeps) == 1
        assert kernel_exec.guard_sweep(fixtures.mini_spec()) == first
        assert len(sweeps) == 2

    def test_guard_sweep_returns_the_complete_semantic_surface(self):
        verdicts = kernel_exec.guard_sweep(SPEC)
        letters = tuple(RightToken("letter", name) for name in sorted(SPEC.runes))
        ligatures = tuple(name for name, rune in SPEC.runes.items() if rune.sequence)
        second_slots = (*letters, EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN)
        assert len(verdicts) == len(ligatures) * len(letters) * len(second_slots)
        assert set(verdicts.values()) <= {False, True}
        first = letters[0]
        for ligature in ligatures:
            assert (ligature, first, ZWNJ) in verdicts
            assert (ligature, first, NAMER_DOT) in verdicts

    def test_one_configuration_sweeps_the_same_keys_and_the_quantified_verdict_needs_every_one_to_block(self):
        """`guard_sweep_under` returns one configuration's surface with the same keys as the quantified surface from `guard_sweep`, and runs the crate on every call. A quantified verdict blocks only where the verdict under every subset of the capability-unlock features blocks; `guard.rs` quantifies over the same powerset."""
        quantified = kernel_exec.guard_sweep(SPEC)
        features = spec_load.capability_features(SPEC)
        surfaces = [
            kernel_exec.guard_sweep_under(SPEC, frozenset(subset))
            for size in range(len(features) + 1)
            for subset in itertools.combinations(features, size)
        ]
        assert len(surfaces) == 2 ** len(features)
        assert all(surface.keys() == quantified.keys() for surface in surfaces)
        for key, blocked in quantified.items():
            assert blocked == all(surface[key] for surface in surfaces), key


@pytest.mark.parametrize(
    ("deep", "prospect", "votes", "wanted"),
    [
        (True, True, True, True),
        (True, True, False, True),
        (True, False, True, True),
        (True, False, False, False),
        (False, True, True, False),
        (False, False, False, False),
    ],
)
def test_the_class_grain_rule_needs_a_fiber_source(monkeypatch, deep, prospect, votes, wanted):
    """`AMS_DEEP_CLASSES` asks for class grain, and `class_grain` grants it only when the simulated prospect or the shifted vote slots are on. With both off, the crate has no deep token to probe and enumerates at label grain whatever the flag says."""
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", deep)
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "VOTE_SLOTS_DEFAULT", votes)
    assert kernel_exec.class_grain() is wanted


@pytest.mark.parametrize(
    ("prospect", "votes", "deep", "wanted"),
    [
        (True, True, True, ["simulated-prospect", "vote-slots", "deep-classes"]),
        (True, False, True, ["simulated-prospect", "deep-classes"]),
        (False, True, True, ["vote-slots", "deep-classes"]),
        (True, True, False, ["simulated-prospect", "vote-slots"]),
        (False, False, True, []),
        (False, False, False, []),
    ],
)
def test_the_enumeration_tokens_name_every_flag_that_is_on(monkeypatch, prospect, votes, deep, wanted):
    """Each flag changes settlement or enumeration grain without changing any hashed source, so a key over the sources alone could not tell a flag-on enumeration from a flag-off one. The tokens come in the order a stamp appends them. The class-grain token follows `class_grain`, not `DEEP_CLASSES_DEFAULT` alone, because with both settlement flags off the crate enumerates at label grain."""
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "VOTE_SLOTS_DEFAULT", votes)
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", deep)
    assert kernel_exec.enumeration_tokens() == wanted


def test_the_tables_stamp_appends_exactly_the_enumeration_tokens(monkeypatch):
    """`run_m1.tables_inputs` appends exactly the tokens `kernel_exec.enumeration_tokens` returns. `run_m1.memo_seed` (the memo head's world) and `run_m1.locality_lines` read the same function, so a new engine flag reaches all three."""
    monkeypatch.setattr(run_m1.fingerprint, "tables_value", lambda repo_root: "sources")
    assert run_m1.tables_inputs() == "+".join(["sources", *kernel_exec.enumeration_tokens()])


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheProductStandsAlone:
    """Nothing folds a stream on this side, so each product is checked by itself: rows come in the key order `fold::assert_key_sorted` requires, with no duplicates, and every cell a row names is in the product. The rows' joint flags are the trace's `joint_floor` before the prospect-divergence pass; the crate test `fold::tests::the_prospect_pass_raises_joints_and_clears_none` covers that pass."""

    def test_the_stream_is_key_sorted_without_duplicates(self, products, config):
        keys = [row.key for row in products[config].transitions]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

    def test_every_settled_cell_the_rows_name_is_in_the_product(self, products, config):
        product = products[config]
        for row in product.transitions:
            assert row.settled.cell in product.cells
            if row.left_settled is not None:
                assert row.left_settled.cell in product.cells


def test_the_default_configuration_enumerates_at_class_grain(products):
    product = products["default"]
    assert product.deep_classes
    for token, members in product.deep_classes.items():
        assert token.startswith(table_module.DEEP_CLASS_PREFIX)
        assert len(members) > 1


class TestTheKernelInvocation:
    def test_a_caller_with_nowhere_to_write_still_gets_its_tables(self, tmp_path, monkeypatch):
        """A caller with no `out_dir` gets the tables and leaves no files: the kernel writes into a temporary directory, and the call returns each configuration's decision head and treaty rows."""
        monkeypatch.chdir(tmp_path)
        tables, digests = run_m1.build_tables(SPEC)
        assert list(tables) == list(conform.SETTLEMENT_CONFIGS)
        assert list(digests) == list(conform.SETTLEMENT_CONFIGS)
        assert all(decision.rules and treaty.rows for decision, treaty in tables.values())
        assert not sorted(tmp_path.iterdir())

    def test_a_narrowed_build_answers_for_the_configurations_it_was_asked_for(self, tmp_path):
        """With `configs=["default"]`, both returned mappings hold only `default`, with rules and treaty rows, and the out dir holds `default`'s TSVs and no file naming another configuration. The crate receives the narrowed list; the whole-set result is not filtered afterward."""
        out_dir = tmp_path / "one"
        tables, digests = run_m1.build_tables(SPEC, out_dir, inputs=STAMP, configs=["default"])
        assert list(tables) == ["default"] and list(digests) == ["default"]
        decision, treaty = tables["default"]
        assert decision.rules and treaty.rows
        assert (out_dir / "settlement-default.tsv").is_file()
        assert (out_dir / "treaties-default.tsv").is_file()
        others = [config for config in conform.ACCEPTANCE_CONFIGS if config != "default"]
        assert not [path.name for path in out_dir.iterdir() if any(other in path.name for other in others)]

    def test_a_narrowed_build_files_the_bytes_the_whole_set_files(self, tmp_path):
        """`default` built alone writes the same settlement and treaty TSVs, byte for byte, and reports the same digest as `default` built with the whole settlement set. `rebuild/tools/scratch_build.py --configs default` relies on this when it reads only `default`'s rows."""
        one, every = tmp_path / "one", tmp_path / "every"
        _tables, narrowed = run_m1.build_tables(SPEC, one, inputs=STAMP, configs=["default"])
        _tables, whole = run_m1.build_tables(SPEC, every, inputs=STAMP)
        assert narrowed["default"] == whole["default"]
        for name in ("settlement-default.tsv", "treaties-default.tsv"):
            assert (one / name).read_bytes() == (every / name).read_bytes(), name

    def _observe_build(self, monkeypatch, tmp_path, asked, configs=None):
        """Record the arguments `build_tables` passes to `kernel_exec.build_table_files`: the configurations, the thread width, the timings tag, the stamp, and `config_seed`. The stub raises `Reached`, so the run ends there."""
        seen = []

        def build_table_files(
            spec_path,
            out_dir,
            configs,
            *,
            inputs,
            threads,
            timings=False,
            timings_tag=None,
            config_seed=True,
            **memo,
        ):
            seen.append((tuple(configs), threads, timings_tag, inputs, config_seed))
            raise Reached

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        narrowing = {} if configs is None else {"configs": configs}
        with pytest.raises(Reached):
            run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, kernel_threads=asked, **narrowing)
        return seen

    def test_the_crate_is_asked_for_the_configurations_the_caller_named(self, monkeypatch, tmp_path):
        """A caller's `configs` reaches the crate in the order given. `test_the_thread_width_is_how_many_configurations_run_at_once` covers the unnarrowed call."""
        seen = self._observe_build(monkeypatch, tmp_path, None, configs=["ss03", "default"])
        assert [configs for configs, *_rest in seen] == [("ss03", "default")]

    @pytest.mark.parametrize(
        "asked, wanted",
        [
            (None, kernel_exec.KERNEL_THREADS_DEFAULT),
            (2, 2),
            (99, len(conform.SETTLEMENT_CONFIGS)),
        ],
    )
    def test_the_thread_width_is_how_many_configurations_run_at_once(
        self, monkeypatch, tmp_path, asked, wanted
    ):
        """One process builds every settlement configuration, `default` first and the rest as deltas over its memo, and `threads` is how many deltas run at once. The crate labels each configuration's timing lines itself, so no tag is passed, and the overlay configuration is never requested."""
        seen = self._observe_build(monkeypatch, tmp_path, asked)
        assert len(seen) == 1
        configs, threads, tag, stamp, config_seed = seen[0]
        assert configs == conform.SETTLEMENT_CONFIGS
        assert threads == min(wanted, len(conform.SETTLEMENT_CONFIGS), run_m1.usable_cores())
        assert tag is None
        assert stamp == STAMP
        assert config_seed

    def test_a_narrowed_cpu_allowance_narrows_the_fan_out(self, monkeypatch, tmp_path):
        """The width is also capped at `usable_cores()`, the cores this process may run on, so a container limited to part of its host's CPUs stays within that limit whatever the memory allows. The test fixes the allowance at two and asks for every configuration, so the test passes only if the cap applies."""
        allowance = 2
        monkeypatch.setattr(run_m1, "usable_cores", lambda: allowance)
        seen = self._observe_build(monkeypatch, tmp_path, len(conform.SETTLEMENT_CONFIGS))
        assert seen[0][1] == min(len(conform.SETTLEMENT_CONFIGS), allowance)

    def test_a_build_seeded_from_the_previous_memo_files_the_bytes_a_from_scratch_build_files(
        self, tmp_path, monkeypatch
    ):
        """A build of an edited spec into a directory holding the previous build's memos seeds from them, with the edited rune named, and writes the same packed windows, settlement TSVs, and treaty TSVs, byte for byte, as a build of the edited spec into an empty directory. It also leaves its own memos under the edited spec's stamp."""
        asked = []
        asked_classes = []
        real = kernel_exec.build_table_files

        def build_table_files(*args, **rest):
            asked.append((rest.get("seed"), rest.get("edited"), rest.get("memo_stamp")))
            asked_classes.append(rest.get("moved_classes"))
            return real(*args, **rest)

        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        tea = SPEC.runes["qsTea"]
        edited = replace(
            SPEC, runes={**SPEC.runes, "qsTea": replace(tea, policy=replace(tea.policy, refuse=()))}
        )
        assert run_m1.memo_edited(run_m1.memo_stamp(SPEC), run_m1.memo_stamp(edited)) == run_m1.MemoDelta(
            runes=("qsTea",), classes=()
        )
        seeded = tmp_path / "seeded"
        run_m1.build_tables(SPEC, seeded, inputs=STAMP)
        assert asked[-1] == (None, (), run_m1.memo_stamp(SPEC))
        for config in conform.SETTLEMENT_CONFIGS:
            assert kernel_exec.read_memo_head(kernel_exec.memo_path(seeded, config)) == kernel_exec.MemoHead(
                config, "+".join(kernel_exec.enumeration_tokens()), run_m1.memo_stamp(SPEC)
            )
        before = {path.name: path.read_bytes() for path in seeded.iterdir()}
        run_m1.build_tables(edited, seeded, inputs=STAMP)
        seed, named, stamp = asked[-1]
        assert seed is not None and named == ("qsTea",) and stamp == run_m1.memo_stamp(edited)
        assert asked_classes[-1] == ()
        scratch = tmp_path / "scratch"
        run_m1.build_tables(edited, scratch, inputs=STAMP)
        assert asked[-1] == (None, (), run_m1.memo_stamp(edited))
        moved = False
        for path in sorted(scratch.iterdir()):
            if path.name.startswith("memo-"):
                continue
            expected = path.read_bytes()
            assert (seeded / path.name).read_bytes() == expected, path.name
            moved |= before[path.name] != expected
        assert moved, "striking the refusal moves the tables, so the identity is not trivial"
        for config in conform.SETTLEMENT_CONFIGS:
            head = kernel_exec.read_memo_head(kernel_exec.memo_path(seeded, config))
            assert head is not None and head.stamp == run_m1.memo_stamp(edited)

    def test_a_configuration_delta_files_the_bytes_a_from_scratch_build_files(self, tmp_path):
        """Every configuration after `default`, enumerated as a delta over `default`'s memo, writes the same settlement TSV, treaty TSV, and window enumeration, byte for byte, and returns the same digest as the same configuration enumerated on its own (the configuration corollary of the window-locality theorem). The mini fixture's `ss03` unlocks a half-·Tea x-height entry, so the delta has windows to share and windows to settle itself. The seeded run claims its deltas heaviest-first (`fanout::delta_worklist`), and its results must still match the from-scratch run configuration by configuration."""
        spec_path = tmp_path / "spec.json"
        kernel_io.write_spec(SPEC, spec_path)
        kernel_exec.ensure_built()
        answers = {}
        for name, config_seed in (("seeded", True), ("scratch", False)):
            answers[name] = kernel_exec.build_table_files(
                spec_path,
                tmp_path / name,
                conform.SETTLEMENT_CONFIGS,
                inputs=STAMP,
                threads=2,
                config_seed=config_seed,
            )
        assert answers["seeded"] == answers["scratch"]
        for config in conform.SETTLEMENT_CONFIGS:
            for family in ("settlement", "treaties", "windows"):
                name = f"{family}-{config}.tsv"
                assert (tmp_path / "seeded" / name).read_bytes() == (
                    tmp_path / "scratch" / name
                ).read_bytes(), name

    def test_an_unstamped_build_names_a_stamp_the_kernel_will_accept(self, monkeypatch, tmp_path):
        """`build-tables` requires a stamp, so a build called without `inputs` passes `kernel_exec.UNSTAMPED_WINDOWS`. The windows payload is then read for its head and deleted, so that stamp never reaches an artifact."""
        seen = []

        def build_table_files(spec_path, out_dir, configs, *, inputs, **rest):
            seen.append(inputs)
            raise Reached

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        with pytest.raises(Reached):
            run_m1.build_tables(SPEC, tmp_path)
        assert set(seen) == {kernel_exec.UNSTAMPED_WINDOWS}
        assert kernel_exec.UNSTAMPED_WINDOWS

    @pytest.mark.parametrize("configs", [None, ("default",), ("ss03", "default")])
    def test_the_table_build_seeds_from_the_configurations_it_builds(self, monkeypatch, tmp_path, configs):
        """`build_tables` passes `memo_seed` the same configurations it passes the crate, so a whole-set build seeds from every settlement configuration's memo and a narrowed build only from its own."""
        seen = {}

        def memo_seed(out_dir, stamp, scratch, configs):
            seen["seed"] = tuple(configs)
            return None

        def build_table_files(spec_path, out_dir, configs, **rest):
            seen["crate"] = tuple(configs)
            raise Reached

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(run_m1, "memo_seed", memo_seed)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        with pytest.raises(Reached):
            if configs is None:
                run_m1.build_tables(SPEC, tmp_path)
            else:
                run_m1.build_tables(SPEC, tmp_path, configs=configs)
        expected = tuple(conform.SETTLEMENT_CONFIGS) if configs is None else configs
        assert seen == {"seed": expected, "crate": expected}

    def test_run_hands_the_width_to_the_table_build(self, monkeypatch, tmp_path):
        seen = {}

        def build_tables(spec, out_dir=None, **rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(run_m1, "build_tables", build_tables)
        with pytest.raises(Reached):
            run_m1.run(out_dir=tmp_path, spec=SPEC, inputs=STAMP, kernel_threads=5)
        assert seen["kernel_threads"] == 5
        assert "fold_jobs" not in seen

    @pytest.mark.parametrize(
        "argv, kernel, replay",
        [
            ([], None, None),
            (["--kernel-threads", "5"], 5, None),
            (["--replay-threads", "3"], None, 3),
            (["--kernel-threads", "5", "--replay-threads", "3"], 5, 3),
        ],
    )
    def test_the_cli_carries_the_thread_width_into_run(self, monkeypatch, argv, kernel, replay):
        """`--kernel-threads` and `--replay-threads` reach `run` as separate keywords. If one arrived as the other, every later test would still pass while the cycle sized the wrong stage."""
        from rebuild.tools import artifact_cycle

        seen = {}

        def run(**rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(artifact_cycle, "run_m1_skip_fingerprint", lambda root: "pinned-key")
        monkeypatch.setattr(run_m1.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
        monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda root: False)
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
        monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
        monkeypatch.setattr(run_m1, "run", run)
        with pytest.raises(Reached):
            run_m1.main(argv)
        assert seen["kernel_threads"] == kernel and seen["replay_threads"] == replay
        assert "fold_jobs" not in seen


class TestTheMemoStamp:
    """Tests for `run_m1.memo_stamp` and `run_m1.memo_edited`, which decide whether a previous build's memo may be read and which runes it may not answer for."""

    def test_a_reworded_rationale_moves_no_rune_digest(self):
        tea = SPEC.runes["qsTea"]
        record = tea.policy.refuse[0]
        reworded = replace(
            SPEC,
            runes={
                **SPEC.runes,
                "qsTea": replace(
                    tea,
                    notes="reworded",
                    policy=replace(
                        tea.policy, refuse=(replace(record, why="because"), *tea.policy.refuse[1:])
                    ),
                ),
            },
        )
        assert run_m1.rune_content_digests(reworded) == run_m1.rune_content_digests(SPEC)
        assert run_m1.memo_edited(run_m1.memo_stamp(SPEC), run_m1.memo_stamp(reworded)) == run_m1.MemoDelta(
            runes=(), classes=()
        )

    def test_a_moved_structure_or_another_format_refuses_the_memo_and_a_new_rune_is_edited(self):
        stamp = json.loads(run_m1.memo_stamp(SPEC))
        moved = json.dumps({**stamp, "structure": "elsewhere"})
        assert run_m1.memo_edited(moved, run_m1.memo_stamp(SPEC)) is None
        assert run_m1.memo_edited(json.dumps({**stamp, "format": "other"}), run_m1.memo_stamp(SPEC)) is None
        assert run_m1.memo_edited("not json", run_m1.memo_stamp(SPEC)) is None
        fewer = json.dumps(
            {**stamp, "runes": {name: digest for name, digest in list(stamp["runes"].items())[1:]}}
        )
        assert run_m1.memo_edited(fewer, run_m1.memo_stamp(SPEC)) == run_m1.MemoDelta(
            runes=(next(iter(stamp["runes"])),), classes=()
        )
        more = json.dumps({**stamp, "runes": {**stamp["runes"], "qsGone": "digest"}})
        assert run_m1.memo_edited(more, run_m1.memo_stamp(SPEC)) is None

    def test_a_class_whose_membership_moved_is_named_and_moves_no_structure(self):
        """When a rune joins a predicate class, the memo stays readable, because the structure stamp leaves out class membership, and `memo_edited` names only that class as moved. The crate then uses its read journal to re-settle only the windows that consulted that class."""
        registry = SPEC.registry
        name, members = next(iter(registry.predicate_classes.items()))
        joined = replace(
            SPEC,
            registry=replace(
                registry,
                predicate_classes={**registry.predicate_classes, name: frozenset(members) | {"qsGone"}},
            ),
        )
        assert run_m1.memo_structure_stamp(joined) == run_m1.memo_structure_stamp(SPEC)
        assert run_m1.memo_edited(run_m1.memo_stamp(SPEC), run_m1.memo_stamp(joined)) == run_m1.MemoDelta(
            runes=(), classes=(name,)
        )

    def test_the_seed_reads_only_memos_whose_head_and_stamp_hold(self, tmp_path):
        stamp = run_m1.memo_stamp(SPEC)
        world = "+".join(kernel_exec.enumeration_tokens())
        wrong = json.dumps({**json.loads(stamp), "structure": "elsewhere"})
        for config, head in (
            ("default", f"# {kernel_exec.MEMO_FORMAT}\tdefault\t{world}\t{stamp}\n"),
            ("ss03", f"# {kernel_exec.MEMO_FORMAT}\tss03\t{world}\t{wrong}\n"),
            ("ss04", f"# {kernel_exec.MEMO_FORMAT}\tss04\tanother-world\t{stamp}\n"),
        ):
            with gzip.open(kernel_exec.memo_path(tmp_path, config), "wt") as handle:
                handle.write(head)
        seed = run_m1.memo_seed(tmp_path, stamp, tmp_path / "seed")
        assert seed is not None
        assert seed.edited == ()
        assert seed.moved_classes == ()
        assert sorted(path.name for path in seed.directory.iterdir()) == ["memo-default.tsv"]
        assert run_m1.memo_seed(tmp_path / "empty", stamp, tmp_path / "seed2") is None

    def test_the_seed_reads_only_the_configurations_the_build_names(self, tmp_path):
        """A narrowed build unpacks only its own configurations' memos and collects edited runes only from them. The default, the whole settlement set, reads every usable memo and collects edited runes from all of them."""
        stamp = run_m1.memo_stamp(SPEC)
        world = "+".join(kernel_exec.enumeration_tokens())
        recorded = json.loads(stamp)
        rune = next(iter(recorded["runes"]))
        behind = json.dumps({**recorded, "runes": {**recorded["runes"], rune: "another-digest"}})
        for config, head in (
            ("default", f"# {kernel_exec.MEMO_FORMAT}\tdefault\t{world}\t{stamp}\n"),
            ("ss03", f"# {kernel_exec.MEMO_FORMAT}\tss03\t{world}\t{behind}\n"),
        ):
            with gzip.open(kernel_exec.memo_path(tmp_path, config), "wt") as handle:
                handle.write(head)
        narrowed = run_m1.memo_seed(tmp_path, stamp, tmp_path / "narrowed", configs=("default",))
        assert narrowed is not None
        assert narrowed.edited == ()
        assert sorted(path.name for path in narrowed.directory.iterdir()) == ["memo-default.tsv"]
        whole = run_m1.memo_seed(tmp_path, stamp, tmp_path / "whole")
        assert whole is not None
        assert whole.edited == (rune,)
        assert sorted(path.name for path in whole.directory.iterdir()) == [
            "memo-default.tsv",
            "memo-ss03.tsv",
        ]
        assert run_m1.memo_seed(tmp_path, stamp, tmp_path / "ss04", configs=("ss04",)) is None

    def test_a_memo_damaged_in_its_head_block_is_skipped_and_left_in_place(self, tmp_path):
        """Zeroed bytes after a valid gzip header make the head's decompression raise `zlib.error`, which `read_memo_head` treats as no readable memo."""
        packed = kernel_exec.memo_path(tmp_path, "default")
        packed.write_bytes(gzip.compress(b"")[:10] + bytes(4096))
        assert kernel_exec.read_memo_head(packed) is None
        assert run_m1.memo_seed(tmp_path, run_m1.memo_stamp(SPEC), tmp_path / "seed") is None
        assert packed.exists()

    def test_a_memo_damaged_after_a_readable_head_is_skipped_and_left_in_place(self, tmp_path):
        """A readable head followed by a gzip member whose deflate data is zeroed passes the head check, then raises `zlib.error` during unpacking, so the seed drops its partial plain file and reads no memo."""
        stamp = run_m1.memo_stamp(SPEC)
        world = "+".join(kernel_exec.enumeration_tokens())
        packed = kernel_exec.memo_path(tmp_path, "default")
        head = f"# {kernel_exec.MEMO_FORMAT}\tdefault\t{world}\t{stamp}\n"
        packed.write_bytes(gzip.compress(head.encode()) + gzip.compress(b"")[:10] + bytes(4096))
        assert kernel_exec.read_memo_head(packed) is not None
        assert run_m1.memo_seed(tmp_path, stamp, tmp_path / "seed") is None
        assert list((tmp_path / "seed").iterdir()) == []
        assert packed.exists()


class TestTheMemoryDerivedThreadDefault:
    """The default table-build width is the machine's memory, less the OS reserve and `DEFAULT_MEMO_BYTES`, divided by `DELTA_PEAK_BYTES`. Its value depends on the machine running the suite, so these tests pass invented totals through the `total_bytes` keyword of `kernel_threads_default` and `replay_threads_default`. `KERNEL_THREADS_DEFAULT` is resolved at import; changing it would mean reloading the module, which resets `_BUILT` and drops the spec dumps other tests in the session hold."""

    @pytest.fixture(autouse=True)
    def _no_inherited_override(self, monkeypatch):
        """Clear `AMS_KERNEL_THREADS` and `AMS_REPLAY_THREADS` so a value exported in the shell cannot change these results."""
        monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
        monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)

    def test_the_shipped_default_is_a_startable_width(self):
        """`KERNEL_THREADS_DEFAULT` is an integer of at least one on any machine, because `how_many_fit` floors at one. It must stay a plain module attribute: `TestTheKernelInvocation` reads it in a parametrize list at import."""
        assert isinstance(kernel_exec.KERNEL_THREADS_DEFAULT, int)
        assert kernel_exec.KERNEL_THREADS_DEFAULT >= 1

    @pytest.mark.parametrize("stated, wanted", [("1", 1), ("3", 3), ("12", 12), ("0", 1), ("-3", 1)])
    def test_a_stated_width_short_circuits_ahead_of_the_arithmetic(self, monkeypatch, stated, wanted):
        """`AMS_KERNEL_THREADS` takes precedence over the memory arithmetic. Its value is floored at one and not otherwise capped here; `run_m1._table_build_threads` applies the configuration and core caps. The invented total is a terabyte, so the derived width would be far above every stated value, and a pass shows the override was used."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", stated)
        assert kernel_exec.kernel_threads_default(total_bytes=1_000_000_000_000) == wanted

    @pytest.mark.parametrize("junk", ["", "   ", "banana", "9GB", "2.5"])
    def test_a_value_that_is_not_a_width_says_so_rather_than_being_quietly_ignored(self, monkeypatch, junk):
        """An unreadable `AMS_KERNEL_THREADS` raises an error naming the variable instead of falling back to the derived width. `AMS_TOTAL_MEMORY_BYTES` ignores a bad value, but this variable is set to keep a build out of swap, so silently using another width would defeat it. An empty or blank value counts as unreadable."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", junk)
        with pytest.raises(RuntimeError, match="AMS_KERNEL_THREADS"):
            kernel_exec.kernel_threads_default(total_bytes=34_359_738_368)

    @pytest.mark.parametrize("junk", ["", "   ", "banana", "9GB", "2.5"])
    def test_a_replay_value_that_is_not_a_width_is_refused_the_same_way(self, monkeypatch, junk):
        """`AMS_REPLAY_THREADS` is read the same way as `AMS_KERNEL_THREADS`: a value that is not a bare count raises an error naming the variable."""
        monkeypatch.setenv("AMS_REPLAY_THREADS", junk)
        with pytest.raises(RuntimeError, match="AMS_REPLAY_THREADS"):
            kernel_exec.replay_threads_default(total_bytes=34_359_738_368)

    @pytest.mark.parametrize(
        "total, wanted", [(4_000_000_000, 1), (34_359_738_368, 3), (32_000_000_000, 3), (64_000_000_000, 8)]
    )
    def test_the_width_follows_the_box_and_never_falls_below_one(self, total, wanted):
        """A 32 GiB machine fits three deltas at `DELTA_PEAK_BYTES` (6.3 GB) beside `DEFAULT_MEMO_BYTES` (2.5 GB), 1.34 GB short of a fourth, and a decimal 32 GB machine also fits three. A machine too small for one delta gets one, and a 64 GB machine fits eight before the caller's configuration and core caps."""
        assert kernel_exec.kernel_threads_default(total_bytes=total) == wanted

    def test_a_coresident_pool_comes_off_the_box_before_it_is_divided(self):
        """`coresident_bytes` is memory used by something running beside the fan-out, such as the artifact cycle's pytest pool. It is subtracted in addition to `DEFAULT_MEMO_BYTES`, so 10 GB costs the 64 GB machine two deltas. It defaults to zero because a bare run_m1 runs alone."""
        assert kernel_exec.kernel_threads_default(total_bytes=64_000_000_000) == 8
        assert (
            kernel_exec.kernel_threads_default(coresident_bytes=10_000_000_000, total_bytes=64_000_000_000)
            == 6
        )

    def test_a_stated_width_outranks_a_coresident_reservation_too(self, monkeypatch):
        """A stated `AMS_KERNEL_THREADS` is used as given even when `coresident_bytes` would narrow the derived width."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", "4")
        assert (
            kernel_exec.kernel_threads_default(coresident_bytes=60_000_000_000, total_bytes=64_000_000_000)
            == 4
        )


class TestTheStringReplay:
    """Tests for `kernel_exec.replay_strings` over tables built from the fixture. The crate replays the rules in the settlement TSVs over every text and compares each window's result with its own settlement. These tests check the per-configuration counts, that a family list narrows the texts, the memo options, and that an edited table raises an error naming the text."""

    @pytest.fixture(scope="class")
    def tables_dir(self, tmp_path_factory):
        out_dir = tmp_path_factory.mktemp("tables")
        run_m1.build_tables(SPEC, out_dir)
        return out_dir

    def test_a_clean_walk_answers_every_configuration_over_one_universe(self, tables_dir):
        answered = kernel_exec.replay_strings(
            SPEC, tables_dir, conform.SETTLEMENT_CONFIGS, horizon=3, families=None, threads=2
        )
        assert sorted(answered) == sorted(conform.SETTLEMENT_CONFIGS)
        texts = {counts["texts"] for counts in answered.values()}
        assert len(texts) == 1
        alphabet = len(conform.spec_alphabet(SPEC))
        assert texts == {alphabet + alphabet**2 + alphabet**3}
        assert all(counts["skipped"] == 0 and counts["windows"] > 0 for counts in answered.values())

    def test_a_family_list_walks_only_the_texts_naming_it(self, tables_dir):
        whole = kernel_exec.replay_strings(
            SPEC, tables_dir, ["default"], horizon=3, families=None, threads=1
        )["default"]
        narrowed = kernel_exec.replay_strings(
            SPEC, tables_dir, ["default"], horizon=3, families=["qsPea"], threads=1
        )["default"]
        assert 0 < narrowed["texts"] < whole["texts"]
        assert narrowed["texts"] + narrowed["skipped"] == whole["texts"]
        with pytest.raises(ValueError):
            kernel_exec.replay_strings(SPEC, tables_dir, ["default"], horizon=3, families=[], threads=1)

    def test_a_memo_directory_files_one_window_memo_per_configuration(self, tables_dir, tmp_path):
        """`memo_dir` is passed as `--memo-dir=`, and each configuration's window memo is written under it with the head `conform.absorb_replay_memo` reads. The counts match a walk without `memo_dir`, and that walk writes no memo beside the tables."""
        answered = kernel_exec.replay_strings(
            SPEC,
            tables_dir,
            conform.SETTLEMENT_CONFIGS,
            horizon=3,
            families=None,
            threads=2,
            memo_dir=tmp_path,
        )
        assert answered == kernel_exec.replay_strings(
            SPEC, tables_dir, conform.SETTLEMENT_CONFIGS, horizon=3, families=None, threads=2
        )
        for config in conform.SETTLEMENT_CONFIGS:
            dump = kernel_exec.replay_memo_dump(tmp_path, config)
            assert dump == tmp_path / f"replay-windows-{config}.bin" and dump.is_file()
            with dump.open("rb") as handle:
                marker, _, head = handle.readline().decode().rstrip("\n").partition("\t")
            assert marker == f"# {kernel_exec.REPLAY_MEMO_FORMAT}"
            assert json.loads(head)["config"] == config
            assert json.loads(head)["rows"] == answered[config]["windows"]
        assert not list(tables_dir.glob("replay-windows-*.bin"))

    def test_a_memo_ceiling_reaches_the_verb_and_moves_only_the_settle_count(
        self, tables_dir, tmp_path, monkeypatch
    ):
        """`memo_windows` is passed as `--memo-windows=`. A capped walk covers the same texts and skips as the uncapped walk and settles more windows, because a window met again after the memo is released is settled again. A ceiling together with a memo directory, or a ceiling below one window, raises `ValueError` before any process starts, so nothing is written to the directory."""
        uncapped = kernel_exec.replay_strings(
            SPEC, tables_dir, conform.SETTLEMENT_CONFIGS, horizon=3, families=None, threads=1
        )
        capped = kernel_exec.replay_strings(
            SPEC, tables_dir, conform.SETTLEMENT_CONFIGS, horizon=3, families=None, threads=1, memo_windows=1
        )
        assert sorted(capped) == sorted(uncapped)
        for config, counts in uncapped.items():
            assert capped[config]["texts"] == counts["texts"]
            assert capped[config]["skipped"] == counts["skipped"]
            assert capped[config]["windows"] > counts["windows"]

        def spawned(arguments, verb):
            raise AssertionError(f"a refused walk spawned {verb}")

        monkeypatch.setattr(kernel_exec, "_run_kernel", spawned)
        with pytest.raises(ValueError, match="not both"):
            kernel_exec.replay_strings(
                SPEC,
                tables_dir,
                ["default"],
                horizon=3,
                families=None,
                threads=1,
                memo_dir=tmp_path,
                memo_windows=1,
            )
        with pytest.raises(ValueError, match="at least one window"):
            kernel_exec.replay_strings(
                SPEC, tables_dir, ["default"], horizon=3, families=None, threads=1, memo_windows=0
            )
        assert not list(tmp_path.iterdir())

    def test_a_table_edited_behind_the_engine_is_refused_naming_the_text(self, tables_dir, tmp_path):
        for name in ("settlement-default.tsv", "settlement-ss03.tsv"):
            (tmp_path / name).write_text((tables_dir / name).read_text())
        lines = (tmp_path / "settlement-default.tsv").read_text().splitlines()
        fields = lines[2].split("\t")
        fields[6] = f"{fields[0]}.perturbed"
        lines[2] = "\t".join(fields)
        (tmp_path / "settlement-default.tsv").write_text("\n".join(lines) + "\n")
        with pytest.raises(kernel_exec.ReplayDisagreement) as caught:
            kernel_exec.replay_strings(
                SPEC, tmp_path, ["default", "ss03"], horizon=3, families=None, threads=2
            )
        assert "default" in str(caught.value)
        assert "replay disagreement" in str(caught.value)
        assert "at position" in str(caught.value)
        assert ".perturbed" in str(caught.value)


class TestTheReplayStage:
    """Tests for `run_m1.run_replay_strings`, the stage `run_m1.run` runs on the built tables beside the glyph chain: which texts it walks, what it records, and how a disagreement fails the build. The crate is stubbed; `TestTheStringReplay` exercises the real one."""

    def _record(self, structure, runes, **overrides):
        record = {
            "format": run_m1.REPLAY_FORMAT,
            "horizon": run_m1.REPLAY_HORIZON,
            "families": None,
            "walked": True,
            "configs": {},
            "structure": structure,
            "runes": dict(runes),
            "pass": True,
            "complaint": None,
        }
        record.update(overrides)
        return record

    def test_no_green_record_or_a_moved_structure_walks_the_whole_universe(self):
        runes = {name: f"d-{name}" for name in SPEC.runes}
        assert run_m1.replay_families(SPEC, None, "s1", runes) is None
        assert run_m1.replay_families(SPEC, self._record("s0", runes), "s1", runes) is None
        assert run_m1.replay_families(SPEC, self._record("s1", runes, **{"pass": False}), "s1", runes) is None
        assert run_m1.replay_families(SPEC, self._record("s1", runes, format="other"), "s1", runes) is None
        gone = self._record("s1", {**runes, "qsGone": "d"})
        assert run_m1.replay_families(SPEC, gone, "s1", runes) is None

    def test_nothing_moved_walks_nothing(self):
        runes = {name: f"d-{name}" for name in SPEC.runes}
        assert run_m1.replay_families(SPEC, self._record("s1", runes), "s1", runes) == []

    def test_a_moved_rune_walks_itself_and_every_rune_that_reads_it(self):
        from rebuild.pipeline import spec_load

        runes = {name: f"d-{name}" for name in SPEC.runes}
        moved = {**runes, "qsPea": "d-qsPea-2", "qsNew": "d-new"}
        closure = spec_load.rune_closure(SPEC)
        readers = {name for name, reads in closure.items() if "qsPea" in reads}
        edited = run_m1.replay_families(SPEC, self._record("s1", runes), "s1", moved)
        assert edited is not None
        assert set(edited) == readers | {"qsPea"}
        assert "qsNew" not in edited
        assert edited == sorted(edited)

    def test_the_structure_stamp_moves_with_the_horizon_and_the_semantics(self, monkeypatch):
        base = run_m1.replay_structure_stamp(SPEC)
        assert run_m1.replay_structure_stamp(SPEC) == base
        monkeypatch.setattr(run_m1, "REPLAY_HORIZON", run_m1.REPLAY_HORIZON + 1)
        assert run_m1.replay_structure_stamp(SPEC) != base
        monkeypatch.setattr(run_m1, "REPLAY_HORIZON", run_m1.REPLAY_HORIZON - 1)
        monkeypatch.setattr(kernel_exec, "enumeration_tokens", lambda: ["other-world"])
        assert run_m1.replay_structure_stamp(SPEC) != base

    def test_the_stage_records_what_it_walked_and_walks_the_delta_next_time(self, monkeypatch, tmp_path):
        asked: list = []

        def replay_strings(
            spec, out_dir, configs, *, horizon, families, threads, timings=False, memo_dir=None
        ):
            asked.append((tuple(configs), horizon, families, threads))
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        digests = {name: f"d-{name}" for name in SPEC.runes}
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: dict(digests))
        first = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert first["pass"] and first["families"] is None and first["walked"]
        assert asked == [
            (tuple(conform.SETTLEMENT_CONFIGS), run_m1.REPLAY_HORIZON, None, run_m1._replay_threads(None))
        ]
        assert run_m1.read_replay_record(tmp_path) == first
        assert first["runes"] == digests and first["structure"] == "s1"

        again = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert again["families"] == [] and not again["walked"] and again["pass"]
        assert len(asked) == 1
        assert run_m1.read_replay_record(tmp_path) == again

        digests["qsPea"] = "d-qsPea-2"
        third = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert third["families"] is not None and "qsPea" in third["families"] and third["walked"]
        assert asked[-1][2] == third["families"]

    def test_the_stage_walks_at_its_own_width_and_a_stated_one_is_only_ever_narrowed(
        self, monkeypatch, tmp_path
    ):
        """The replay's width comes from `_replay_threads`, not from the table build's width. With none stated it is `kernel_exec.replay_threads_default()` capped at the configuration count and the cores. A stated width reaches the crate unchanged unless it exceeds the configuration count, which caps it. The test clears `AMS_REPLAY_THREADS` and fixes the cores at 64 so the configuration count is the binding cap."""
        asked: list = []

        def replay_strings(
            spec, out_dir, configs, *, horizon, families, threads, timings=False, memo_dir=None
        ):
            asked.append(threads)
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)
        count = len(conform.SETTLEMENT_CONFIGS)
        assert run_m1._replay_threads(None) == min(kernel_exec.replay_threads_default(), count)
        run_m1.run_replay_strings(SPEC, tmp_path, None)
        run_m1.run_replay_strings(SPEC, tmp_path, None, replay_threads=1)
        run_m1.run_replay_strings(SPEC, tmp_path, None, replay_threads=count + 3)
        assert asked == [run_m1._replay_threads(None), 1, count]
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 2)
        assert run_m1._replay_threads(None) == min(kernel_exec.replay_threads_default(), count, 2)
        assert run_m1._replay_threads(count) == min(count, 2)

    def test_a_memo_file_absent_or_restamped_widens_the_walk_and_asks_for_a_dump(self, monkeypatch, tmp_path):
        """The settle memo's stamp covers modules the replay's stamp does not. So when a configuration's settle memo file is absent or has another stamp, a build that would otherwise walk nothing walks every text and asks for the window memo dumps that refill it. A rune edit walks that rune's families and asks for no dumps, and a pass where every memo file is current and nothing moved walks nothing. A missing dump (the stub writes none) is a warning and does not fail the stage."""
        asked: list = []

        def replay_strings(
            spec, out_dir, configs, *, horizon, families, threads, timings=False, memo_dir=None
        ):
            asked.append((families, memo_dir))
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        digests = {name: f"d-{name}" for name in SPEC.runes}
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: dict(digests))
        inputs = oracle_cache.SettleMemoInputs(rune_digests=dict(digests), oracle_code="code", data="data")
        memos = conform.settle_memo_files(tmp_path, SPEC, inputs)

        first = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", memo_inputs=inputs)
        assert first["pass"] and first["families"] is None
        assert asked == [(None, tmp_path)]
        for memo in memos.values():
            assert conform._write_settle_memo(memo, *conform._memo_columns([]))
        again = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", memo_inputs=inputs)
        assert again["families"] == [] and not again["walked"] and len(asked) == 1

        digests["qsPea"] = "d-qsPea-2"
        edited = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", memo_inputs=inputs)
        assert edited["families"] and "qsPea" in edited["families"]
        assert asked[-1] == (edited["families"], None)

        restamped = replace(memos["ss03"], stamp="another")
        assert conform._write_settle_memo(restamped, *conform._memo_columns([]))
        assert not conform.settle_memo_standing(memos["ss03"])
        widened = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", memo_inputs=inputs)
        assert widened["families"] is None and asked[-1] == (None, tmp_path)
        assert not list(tmp_path.glob("replay-windows-*.bin"))

    def test_a_narrowed_replay_walks_and_keys_only_the_configurations_it_names(self, monkeypatch, tmp_path):
        """With `configs=["default"]`, the stage asks the crate for `default` alone and handles only `default`'s window memo dump. With no `configs` it asks for every settlement configuration and handles every dump. `conform.settle_memo_files` returns every configuration either way, so the filtering happens in this stage."""
        asked: list = []
        dumps: list = []

        def replay_strings(
            spec, out_dir, configs, *, horizon, families, threads, timings=False, memo_dir=None
        ):
            asked.append((tuple(configs), memo_dir))
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        def replay_memo_dump(out_dir, config):
            dumps.append(config)
            return tmp_path / f"replay-windows-{config}.bin"

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(kernel_exec, "replay_memo_dump", replay_memo_dump)
        digests = {name: f"d-{name}" for name in SPEC.runes}
        inputs = oracle_cache.SettleMemoInputs(rune_digests=dict(digests), oracle_code="code", data="data")
        assert set(conform.settle_memo_files(tmp_path, SPEC, inputs)) == set(conform.SETTLEMENT_CONFIGS)

        narrowed = run_m1.run_replay_strings(SPEC, tmp_path, None, memo_inputs=inputs, configs=["default"])
        assert asked == [(("default",), tmp_path)]
        assert list(narrowed["configs"]) == ["default"]
        assert set(dumps) == {"default"}

        dumps.clear()
        whole = run_m1.run_replay_strings(SPEC, tmp_path, None, memo_inputs=inputs)
        assert asked[-1] == (tuple(conform.SETTLEMENT_CONFIGS), tmp_path)
        assert list(whole["configs"]) == list(conform.SETTLEMENT_CONFIGS)
        assert set(dumps) == set(conform.SETTLEMENT_CONFIGS)

    def test_a_narrowed_replay_with_a_stamp_is_refused_before_it_walks(self, monkeypatch, tmp_path):
        """A narrowed `configs` with an `inputs` stamp raises before the crate is called or any record is written. `replay_families` reads a stamped record as a passing walk of every text in every settlement configuration, which a narrowed walk is not."""

        def never(*args, **rest):
            raise AssertionError("the crate was reached")

        monkeypatch.setattr(kernel_exec, "replay_strings", never)
        with pytest.raises(ValueError, match="narrowed replay"):
            run_m1.run_replay_strings(SPEC, tmp_path, STAMP, configs=["default"])
        assert not (tmp_path / run_m1.REPLAY_SUMMARY).exists()

    def test_a_caller_with_no_stamp_walks_everything_and_records_nothing(self, monkeypatch, tmp_path):
        asked: list = []

        def replay_strings(
            spec, out_dir, configs, *, horizon, families, threads, timings=False, memo_dir=None
        ):
            asked.append((families, memo_dir))
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        summary = run_m1.run_replay_strings(SPEC, tmp_path, None)
        assert asked == [(None, None)]
        assert summary["structure"] is None and summary["runes"] == {}
        assert run_m1.read_replay_record(tmp_path) is None

    def test_a_disagreement_is_recorded_red_and_stops_the_build(self, monkeypatch, tmp_path):
        """A disagreement writes a failing record, the next build walks every text instead of a delta, and the build exits reporting the tables incomplete, ahead of any error from the glyph chain that runs beside the replay. `rebuild/test_run_m1_tail.py` covers that ordering for every combination."""

        def replay_strings(spec, out_dir, configs, **rest):
            raise kernel_exec.ReplayDisagreement(
                "default: 1 first-match-wins replay disagreement(s): (qsPea, …)"
            )

        def minting_fails(spec, tables):
            raise RuntimeError("the chain's own complaint")

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: {})
        summary = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert not summary["pass"]
        assert "qsPea" in summary["complaint"]
        assert run_m1.read_replay_record(tmp_path) == summary
        assert run_m1.replay_families(SPEC, summary, "s1", {}) is None

        monkeypatch.setattr(run_m1, "build_tables", lambda spec, out_dir, **rest: ({}, {}))
        monkeypatch.setattr(
            run_m1, "run_emitted_order", lambda *args, **rest: {"pass": True, "complaint": None}
        )
        monkeypatch.setattr(run_m1, "mint_cell_glyphs", minting_fails)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1.run(out_dir=tmp_path, spec=SPEC, inputs="stamp")
