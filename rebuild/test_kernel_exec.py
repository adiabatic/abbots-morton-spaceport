"""The kernel boundary's Python face (issue #78, which left the crate as the only fixpoint): the flags that tell the kernel which world to enumerate, the product it hands back, and the plumbing between the crate and `run_m1` — the digest record, the thread cap, the CLI. Every table here is built on the mini fixture, because the live alphabet's enumeration is the build's business and nothing a contracts test should be paying for; what the fixture is enough to state is the shape of the answer, which is what this file is about.

Nothing skips. A box without `cargo` fails these tests with the remedy `KernelBuildError` carries, and that is the honest signal now that no in-process fixpoint exists to fall back to: the M1 build itself cannot run there either.
"""

import itertools
import json
from collections import OrderedDict

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, kernel_io, run_m1, spec_load
from rebuild.pipeline import table as table_module
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
        question = {
            "left": {"kind": "edge", "settled": None},
            "input": "qsMay",
            "right": [
                {"kind": "edge", "letter": None},
                {"kind": "edge", "letter": None},
                {"kind": "edge", "letter": None},
                {"kind": "edge", "letter": None},
            ],
            "result": None,
        }
        answer = {**question, "result": {"settled": "trace"}}
        calls = []

        class Finished:
            returncode = 0
            stdout = (json.dumps(answer, separators=(",", ":")) + "\n").encode()
            stderr = b""

        def run(arguments, verb):
            calls.append((arguments, verb))
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        got = kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.ndjson",
            [question],
            frozenset({"ss05", "ss03"}),
        )
        assert got == [answer["result"]]
        arguments = calls[0][0]
        assert arguments[1:4] == [
            "settle-cases",
            str(tmp_path / "spec.json"),
            str(tmp_path / "cases.ndjson"),
        ]
        assert "--features=ss03,ss05" in arguments
        assert "--candidacy-prospect" in arguments
        assert "--vote-slots-off" in arguments
        assert "--deep-classes-off" not in arguments

    def test_settle_cases_refuses_an_answer_to_a_different_question(self, monkeypatch, tmp_path):
        question = {"left": {}, "input": "qsMay", "right": [], "result": None}
        changed = {**question, "input": "qsIt", "result": {}}

        class Finished:
            returncode = 0
            stdout = (json.dumps(changed) + "\n").encode()
            stderr = b""

        monkeypatch.setattr(kernel_exec, "_run_kernel", lambda *args, **kwargs: Finished())
        with pytest.raises(kernel_exec.KernelRunError, match="changed"):
            kernel_exec._settle_cases(
                tmp_path / "spec.json",
                tmp_path / "cases.ndjson",
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
        """`ensure_built` is what every caller in a process shares, so a suite that builds a hundred tables consults cargo once. The memo is a module attribute precisely so a test can drive it."""
        builds = []
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        monkeypatch.setattr(kernel_exec, "cargo_build", lambda: builds.append(1))
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        assert builds == [1]

    def test_a_named_mode_overrides_the_processs_own_world(self, monkeypatch, tmp_path):
        """A `SettlementModes` is how a caller asks for a world other than its process's — the guard's pinned candidacy grain, a comparison replay, a test that has to state its own semantics — and it wins in both directions: it puts flags on an argv whose module defaults are all on, and leaves them off an argv whose defaults are all off."""
        question = {"left": {"kind": "edge", "settled": None}, "input": "qsMay", "right": [], "result": None}
        answer = {**question, "result": {"settled": "trace"}}
        calls = []

        class Finished:
            returncode = 0
            stdout = (json.dumps(answer, separators=(",", ":")) + "\n").encode()
            stderr = b""

        def run(arguments, verb):
            calls.append(arguments)
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.ndjson",
            [question],
            frozenset(),
            kernel_exec.SettlementModes(simulated_prospect=False, vote_slots=False),
        )
        assert calls[0][4:] == ["--candidacy-prospect", "--vote-slots-off"]
        for _flag, module, attribute in kernel_exec.WORLD_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        kernel_exec._settle_cases(
            tmp_path / "spec.json",
            tmp_path / "cases.ndjson",
            [question],
            frozenset(),
            kernel_exec.SettlementModes(simulated_prospect=True, vote_slots=True),
        )
        assert calls[1][4:] == []

    def test_a_refused_window_carries_the_crates_bucket_and_sentence(self, monkeypatch):
        """A window the crate refuses is not a broken boundary, and must not read as one: the answer is `{raise, message}`, and what a caller catches is a `SettleError` carrying the raise identity as its bucket and the crate's own sentence, verbatim, as its message."""
        question = {"left": {"kind": "edge", "settled": None}, "input": "qsMay", "right": [], "result": None}
        message = "E-STRANDED: qsPea.half.ex-y5 committed an exit at x-height but qsTea has no acceptor cell"
        answer = {**question, "result": {"raise": "E-UNREACHABLE", "message": message}}

        class Finished:
            returncode = 0
            stdout = (json.dumps(answer, separators=(",", ":")) + "\n").encode()
            stderr = b""

        monkeypatch.setattr(kernel_exec, "_run_kernel", lambda *arguments, **rest: Finished())
        with pytest.raises(SettleError) as complaint:
            kernel_exec.settle_windows(SPEC, [question], frozenset())
        assert complaint.value.bucket == "E-UNREACHABLE"
        assert str(complaint.value) == message
        assert not isinstance(complaint.value, kernel_exec.KernelRunError)

    def test_settle_windows_answers_one_settled_per_case_in_the_order_asked(self, monkeypatch):
        """The walker's verb, whose whole contract is positional: it decodes an answer to a `Settled` and nothing more, and it chunks so a batch of any size costs a bounded pile of case rows — `SETTLE_WINDOW_BATCH` in the shipping form, whatever `batch` says here."""
        sizes = []
        original = kernel_exec._settle_cases

        def recording(spec_path, cases_path, cases, features, modes=None, decode=kernel_exec._identity):
            sizes.append(len(cases))
            return original(spec_path, cases_path, cases, features, modes, decode)

        monkeypatch.setattr(kernel_exec, "_settle_cases", recording)
        names = ("qsMay", "qsIt", "qsTea", "qsDay", "qsOy")
        cases = [
            kernel_exec.case_row(LeftContext("edge"), RightToken("letter", name), (EDGE,) * 4)
            for name in names
        ]
        settled = kernel_exec.settle_windows(SPEC, cases, frozenset(), batch=2)
        assert [None if outcome is None else outcome.cell.rune for outcome in settled] == list(names)
        assert sizes == [2, 2, 1]

    def test_settle_windows_can_answer_none_for_a_refusal_and_keep_the_batch(self, monkeypatch):
        """`on_error="drop"`: the refusing case's slot answers `None`, every other line decodes as usual, and the answers stay lined up with the questions — which is what lets a caller prefill windows it may never read without one refusal taking the batch down."""
        names = ("qsMay", "qsIt", "qsTea")
        cases = [
            kernel_exec.case_row(LeftContext("edge"), RightToken("letter", name), (EDGE,) * 4)
            for name in names
        ]
        results: list[dict] = [
            {"settled": {"cell": [name, "full", None, None, []], "seam": None, "extension": 0}}
            for name in names
        ]
        results[1] = {"raise": "E-AMBIGUOUS", "message": "qsIt: two candidates tie at every stage"}

        class Finished:
            returncode = 0
            stdout = (
                "".join(
                    json.dumps({**case, "result": result}, separators=(",", ":")) + "\n"
                    for case, result in zip(cases, results)
                )
            ).encode()
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
        """A refusal mid-sequence under `on_error="drop"` costs that one sequence and nothing else: its neighbors in the same wave finish every position they had, and the answer stays positional, with `None` standing where the dropped sequence's traces would have been."""
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
        """The dump is the fixed cost of reaching the kernel, and the sweep, the settlement verbs and every batch under them read the same file: a walker that settles a spec in a hundred batches writes its spec.json once."""
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
        """Formation stages before everything, so an emitter, a surface build and a walker in one process all want the same verdict surface. They get the memo, keyed on spec identity: one invocation per spec, and a spec object that is merely equal to another is still its own."""
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
        """`guard_sweep_under` is the verb's other answer: one configuration's surface over exactly the quantified surface's keys, spawned per call because nothing that ships reads it. The quantified verdict blocks exactly where every subset of the capability-unlock features blocks — the powerset `guard.rs` quantifies over, walked here as the crate walks it."""
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
    """Class grain is asked for by the flag and granted only where a deep token can move an outcome at all: in the pinned candidacy world the crate has nothing to probe and enumerates at label grain however the flag reads."""
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
    """Each of these flags changes settlement semantics or enumeration grain without moving a single hashed source, so a key taken over the sources alone would read a flag-on enumeration as fresh to a flag-off process and the reverse. The order is the order a stamp appends them, and the class-grain token obeys `class_grain` rather than its own flag: the pinned candidacy world grants it to nobody, so a stamp that carried it there would name a grain the crate never enumerated at."""
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "VOTE_SLOTS_DEFAULT", votes)
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", deep)
    assert kernel_exec.enumeration_tokens() == wanted


def test_the_tables_stamp_appends_exactly_the_enumeration_tokens(monkeypatch):
    """Two keys spell the semantics half of the engine's identity — the tables' own stamp and gate:conform's sweep key — and both now ask one function for it, so what is left to hold is that the stamp appends what that function answers and nothing beside it. A fourth flag added to the engine then reaches both keys in the same commit rather than one of them."""
    monkeypatch.setattr(run_m1.fingerprint, "tables_value", lambda repo_root: "sources")
    assert run_m1.tables_inputs() == "+".join(["sources", *kernel_exec.enumeration_tokens()])


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheProductStandsAlone:
    """What a stream carries has to stand on its own, because nothing folds it here any more: the rows in the key order `fold::assert_key_sorted` refuses a product for losing, and every cell they name present in the product that named them. The joint flags those rows carry are the trace's floor alone — what the prospect-divergence pass then makes of them is the crate's `fold::tests::the_prospect_pass_raises_joints_and_clears_none`, which can see the rows on both sides of the pass where this side sees only the artifacts."""

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
        """A caller with no `out_dir` gets the tables and leaves nothing behind: the kernel's artifacts land in a scratch directory that goes with the frame, and what comes back is the head every downstream stage reads plus the treaty rows the defect gates want."""
        monkeypatch.chdir(tmp_path)
        tables, digests = run_m1.build_tables(SPEC)
        assert list(tables) == list(conform.SETTLEMENT_CONFIGS)
        assert list(digests) == list(conform.SETTLEMENT_CONFIGS)
        assert all(decision.rules and treaty.rows for decision, treaty in tables.values())
        assert not sorted(tmp_path.iterdir())

    def _observe_build(self, monkeypatch, tmp_path, asked):
        """Everything `build_tables` asks of the kernel: the one invocation, with the configurations it named, the width it handed over, the tag and the stamp. The stub raises, so a run ends as soon as the invocation has been observed."""
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
        ):
            seen.append((tuple(configs), threads, timings_tag, inputs, config_seed))
            raise Reached

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        with pytest.raises(Reached):
            run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, kernel_threads=asked)
        return seen

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
        """One process answers every settlement configuration, `default` first and the rest as deltas over its memo, and the width is how many of those deltas the crate keeps in flight; the crate labels every configuration's timing lines itself, so nothing is tagged, and the overlay configuration is never asked for."""
        seen = self._observe_build(monkeypatch, tmp_path, asked)
        assert len(seen) == 1
        configs, threads, tag, stamp, config_seed = seen[0]
        assert configs == conform.SETTLEMENT_CONFIGS
        assert threads == min(wanted, len(conform.SETTLEMENT_CONFIGS), run_m1.usable_cores())
        assert tag is None
        assert stamp == STAMP
        assert config_seed

    def test_a_narrowed_cpu_allowance_narrows_the_fan_out(self, monkeypatch, tmp_path):
        """The third term is the cores this process may actually run on rather than the cores the box has, so a container held to a slice of its host keeps its width down to the slice however much memory the default was divided out of. The allowance is invented because the box running the suite is whatever it is — asking for every configuration against an allowance narrower than that is what makes a pass proof the term fired at all."""
        allowance = 2
        monkeypatch.setattr(run_m1, "usable_cores", lambda: allowance)
        seen = self._observe_build(monkeypatch, tmp_path, len(conform.SETTLEMENT_CONFIGS))
        assert seen[0][1] == min(len(conform.SETTLEMENT_CONFIGS), allowance)

    def test_a_configuration_delta_files_the_bytes_a_from_scratch_build_files(self, tmp_path):
        """The configuration corollary of the window-locality theorem, held at the artifact: every configuration past `default` enumerated as a delta over `default`'s memo files the same settlement TSV, treaty TSV and window enumeration, byte for byte, as the same configuration enumerated on its own, and answers the same digest. The mini fixture unlocks a `qsMay` entry under `ss03`, so the delta has both windows to share and windows to settle itself."""
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
        """The verb requires a stamp, and a build with none still has to name one: the payload it writes is where the head comes from, and it is deleted unread rather than kept, so the word it carried never reaches an artifact."""
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

    @pytest.mark.parametrize("argv, threads", [([], None), (["--kernel-threads", "5"], 5)])
    def test_the_cli_carries_the_thread_width_into_run(self, monkeypatch, argv, threads):
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
        monkeypatch.setattr(run_m1, "run", run)
        with pytest.raises(Reached):
            run_m1.main(argv)
        assert seen["kernel_threads"] == threads
        assert "fold_jobs" not in seen


class TestTheMemoryDerivedThreadDefault:
    """The width the fan-out falls back to is the box, less `default`'s retained memo, divided by `CONFIG_PEAK_BYTES` (issue #63, sub-issue #86), so what can be asserted about it here is its shape and its branches, never its value — the value is whatever machine is running the suite. Every branch is exercised through `kernel_threads_default`'s `total_bytes` keyword, which is a pure function over an invented box; `KERNEL_THREADS_DEFAULT` itself is resolved at import and could only be moved by reloading the module, which would reset `_BUILT` and drop the live spec dumps underneath whatever else the session is holding."""

    @pytest.fixture(autouse=True)
    def _no_inherited_override(self, monkeypatch):
        """A shell that exported a width of its own must not decide what these assertions mean, so the variable is cleared before each of them and set back only by the tests whose subject it is."""
        monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)

    def test_the_shipped_default_is_a_startable_width(self):
        """Whatever box resolved it, the constant is an integer a pool can start on: `how_many_fit` floors at one, because a build that refuses to start on a small machine is strictly worse than one that runs slowly. It also has to stay a plain module attribute rather than becoming a callable — `TestTheKernelInvocation` parametrizes on it by reference at import, and a function object there would be compared against a thread count on every box."""
        assert isinstance(kernel_exec.KERNEL_THREADS_DEFAULT, int)
        assert kernel_exec.KERNEL_THREADS_DEFAULT >= 1

    @pytest.mark.parametrize("stated, wanted", [("1", 1), ("3", 3), ("12", 12), ("0", 1), ("-3", 1)])
    def test_a_stated_width_short_circuits_ahead_of_the_arithmetic(self, monkeypatch, stated, wanted):
        """`AMS_KERNEL_THREADS` is read before anything is divided, and what it states is floored at one and clamped no further — the configuration count and the cores this process may actually run on are not memory facts and are applied by `run_m1.build_tables`. The invented box is a terabyte so the arithmetic would answer far above any width stated here, which is what makes a pass proof that the short-circuit fired rather than a coincidence."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", stated)
        assert kernel_exec.kernel_threads_default(total_bytes=1_000_000_000_000) == wanted

    @pytest.mark.parametrize("junk", ["", "   ", "banana", "9GB", "2.5"])
    def test_a_value_that_is_not_a_width_says_so_rather_than_being_quietly_ignored(self, monkeypatch, junk):
        """This is the one place the two environment knobs in the derivation disagree, and deliberately: `AMS_TOTAL_MEMORY_BYTES` reproduces a box and swallows a typo, while this one is what someone reaches for to keep a build out of swap, so a value it cannot read is refused with the variable and its spelling named rather than silently replaced by a derived width. A variable declared without a value counts as unreadable, not as unset."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", junk)
        with pytest.raises(RuntimeError, match="AMS_KERNEL_THREADS"):
            kernel_exec.kernel_threads_default(total_bytes=34_359_738_368)

    @pytest.mark.parametrize(
        "total, wanted", [(4_000_000_000, 1), (34_359_738_368, 5), (32_000_000_000, 5), (64_000_000_000, 12)]
    )
    def test_the_width_follows_the_box_and_never_falls_below_one(self, total, wanted):
        """The whole point of the derivation: a 32 GB box holds its whole delta wave at once — five deltas beside `default`'s memo, one more than the acceptance set has — in either spelling of 32 GB, so the answer does not rest on a unit convention; a box too small for one configuration gets one anyway, and a roomier box gets what it has room for instead of the constrained box's width."""
        assert kernel_exec.kernel_threads_default(total_bytes=total) == wanted

    def test_a_coresident_pool_comes_off_the_box_before_it_is_divided(self):
        """What a caller running the fan-out beside something else — the artifact cycle, beside its pytest pool — takes off the top, so the width answers for the machine the configurations will actually share rather than for an empty one. It is the caller's fact and defaults to nothing, because a bare run_m1 has nothing beside it."""
        assert kernel_exec.kernel_threads_default(total_bytes=64_000_000_000) == 12
        assert (
            kernel_exec.kernel_threads_default(coresident_bytes=13_000_000_000, total_bytes=64_000_000_000)
            == 9
        )

    def test_a_stated_width_outranks_a_coresident_reservation_too(self, monkeypatch):
        """Nothing derived narrows a width someone stated, a co-resident pool included: the knob exists to keep a build out of swap, and a reservation that quietly took a configuration off it would be the failure it was set to prevent."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", "4")
        assert (
            kernel_exec.kernel_threads_default(coresident_bytes=60_000_000_000, total_bytes=64_000_000_000)
            == 4
        )


class TestTheStringReplay:
    """The `replay-strings` seam over the fixture's own tables: the crate reads the settlement TSVs a build left and holds them to its engine over every string, so what this side checks is that a clean walk answers per configuration, that a family list narrows the universe, and that a table edited behind the engine's back is refused naming the text."""

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
    """The stage `run_m1.run` puts between the table build and the minting: which texts it walks, what it records, and how a disagreement reaches the build's verdict. The crate is stubbed here — the seam above is where the real one is exercised — so what these test is the delta arithmetic and the record."""

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

        def replay_strings(spec, out_dir, configs, *, horizon, families, threads, timings=False):
            asked.append((tuple(configs), horizon, families, threads))
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        digests = {name: f"d-{name}" for name in SPEC.runes}
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: dict(digests))
        first = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", kernel_threads=2)
        assert first["pass"] and first["families"] is None and first["walked"]
        assert asked == [(tuple(conform.SETTLEMENT_CONFIGS), run_m1.REPLAY_HORIZON, None, 2)]
        assert run_m1.read_replay_record(tmp_path) == first
        assert first["runes"] == digests and first["structure"] == "s1"

        again = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", kernel_threads=2)
        assert again["families"] == [] and not again["walked"] and again["pass"]
        assert len(asked) == 1
        assert run_m1.read_replay_record(tmp_path) == again

        digests["qsPea"] = "d-qsPea-2"
        third = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", kernel_threads=2)
        assert third["families"] is not None and "qsPea" in third["families"] and third["walked"]
        assert asked[-1][2] == third["families"]

    def test_a_caller_with_no_stamp_walks_everything_and_records_nothing(self, monkeypatch, tmp_path):
        asked: list = []

        def replay_strings(spec, out_dir, configs, *, horizon, families, threads, timings=False):
            asked.append(families)
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        summary = run_m1.run_replay_strings(SPEC, tmp_path, None)
        assert asked == [None]
        assert summary["structure"] is None and summary["runes"] == {}
        assert run_m1.read_replay_record(tmp_path) is None

    def test_a_disagreement_is_recorded_red_and_stops_the_build(self, monkeypatch, tmp_path):
        def replay_strings(spec, out_dir, configs, **rest):
            raise kernel_exec.ReplayDisagreement(
                "default: 1 first-match-wins replay disagreement(s): (qsPea, …)"
            )

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: {})
        summary = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert not summary["pass"]
        assert "qsPea" in summary["complaint"]
        assert run_m1.read_replay_record(tmp_path) == summary
        assert run_m1.replay_families(SPEC, summary, "s1", {}) is None

        monkeypatch.setattr(run_m1, "build_tables", lambda spec, out_dir, **rest: ({}, {}))
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1.run(out_dir=tmp_path, spec=SPEC, inputs="stamp")
