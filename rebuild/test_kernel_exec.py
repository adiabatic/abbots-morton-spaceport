"""Tests for `rebuild/pipeline/kernel_exec.py`, the kernel interface, and for the `run_m1` code that calls it: the mode flags passed to the crate, the product and tables it returns, the previous memos, the thread widths, the CLI, and the string replay. Every table is built on the mini fixture, which is enough to check the shape of the results; enumerating the live alphabet is the build's job.

No test skips. On a machine without `cargo` these tests fail with the remedy `KernelBuildError` carries, because the M1 build cannot run there either.
"""

import gzip
import itertools
import json
import os
import subprocess
import time
from collections import OrderedDict
from dataclasses import replace

import pytest

from rebuild.pipeline import (
    conform,
    fingerprint,
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
from rebuild.tools import cycle_timings as ct
from rebuild.tools import memory_budget

SPEC = fixtures.mini_spec()
STAMP = "kernel-pinned-stamp"
CONFIGS = {"default": frozenset(), "ss03": frozenset({"ss03"}), "ss04": frozenset({"ss04"})}
CONFIG_COUNT = len(conform.SETTLEMENT_CONFIGS)


class Reached(Exception):
    """Raised from a stubbed stage to end a run the moment the arguments under test have arrived."""


@pytest.fixture(scope="module")
def products():
    return {name: kernel_exec.enumerate_transitions(SPEC, features) for name, features in CONFIGS.items()}


class TestTheInvocationInterface:
    def test_the_mode_flags_reflect_the_python_side_defaults(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        assert kernel_exec.mode_flags() == []
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.mode_flags() == [flag for flag, _module, _attribute in kernel_exec.MODE_FLAGS]

    def test_one_default_switched_off_carries_one_flag(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        flag, module, attribute = kernel_exec.MODE_FLAGS[1]
        monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.mode_flags() == [flag]

    def test_settlement_flags_exclude_the_enumerations_deep_grain(self, monkeypatch):
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        assert kernel_exec.settlement_flags() == ["--candidacy-prospect", "--follower-prefer-slots-off"]
        assert "--deep-classes-off" not in kernel_exec.settlement_flags()

    def test_settle_cases_batches_case_lines_with_canonical_features_and_modes(self, monkeypatch, tmp_path):
        """Stdin carries only the case lines, one per line, and the cases path is `-`. The argv carries the sorted feature list and the mode flags. Each output line is its case line, a tab, and the case result, which decodes to parsed JSON by default."""
        case = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        calls = []

        class Finished:
            returncode = 0
            stdout = (case + '\t{"settled":"trace"}\n').encode()
            stderr = b""

        def run(arguments, verb, stdin=None, *, timeout):
            calls.append((arguments, stdin))
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        got = kernel_exec._settle_cases(
            tmp_path / "spec.json",
            [case],
            frozenset({"ss05", "ss03"}),
        )
        assert got == [{"settled": "trace"}]
        arguments, stdin = calls[0]
        assert stdin == (case + "\n").encode()
        assert arguments[1:4] == ["settle-cases", str(tmp_path / "spec.json"), "-"]
        assert "--features=ss03,ss05" in arguments
        assert "--candidacy-prospect" in arguments
        assert "--follower-prefer-slots-off" in arguments
        assert "--deep-classes-off" not in arguments
        assert "--settled-only" not in arguments

    def test_settle_cases_refuses_a_result_for_a_different_case_line(self, monkeypatch, tmp_path):
        """Each output line must begin with its case line's exact bytes followed by a tab. A result for another case line fails, and so does a result with no tab after its case line."""
        case = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        changed = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsIt"), (EDGE,) * 4)
        for stdout in (changed + "\t{}\n", case + "{}\n", case + "\n"):

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
                    [case],
                    frozenset(),
                )

    def test_each_verb_waits_with_its_own_limit(self, monkeypatch, tmp_path):
        """Each verb hands the call its own limit: `TIMEOUT` for `enumerate-configs`, `settle-cases` and `guard-sweep`; for `build-tables`, `build_tables_timeout` over the alphabet size its caller states; for `replay-strings`, `replay_timeout` over the spec's alphabet, the maximum length, the configurations, the width, and whether one last symbol narrows the walk. A call past its limit fails naming the limit and the verb."""
        waited = []

        def run(arguments, verb, stdin=None, *, timeout):
            waited.append((verb, timeout))
            raise kernel_exec._no_answer(arguments, verb, timeout)

        def reaped(arguments, verb, *, timeout):
            return run(arguments, verb, timeout=timeout)

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        monkeypatch.setattr(kernel_exec, "_run_kernel_reaped", reaped)
        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        spec_path = tmp_path / "spec.json"
        symbols = len(conform.spec_alphabet(SPEC))
        configs = conform.SETTLEMENT_CONFIGS
        case = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        calls = [
            lambda: kernel_exec.enumerate_configs(spec_path, tmp_path / "streams", ["default"], threads=1),
            lambda: kernel_exec.build_table_files(
                spec_path, tmp_path / "tables", configs, inputs=STAMP, threads=2, symbols=47
            ),
            lambda: kernel_exec._settle_cases(spec_path, [case], frozenset()),
            lambda: kernel_exec._guard_verdicts(SPEC, spec_path),
            lambda: kernel_exec.replay_strings(
                SPEC, tmp_path, configs, max_length=5, families=None, threads=len(configs)
            ),
            lambda: kernel_exec.replay_strings(
                SPEC, tmp_path, ["ss03"], max_length=5, families=["qsPea"], threads=1, last="\ue650"
            ),
        ]
        for call in calls:
            with pytest.raises(kernel_exec.KernelRunError, match="gave no answer within"):
                call()
        assert waited == [
            ("enumerate-configs", kernel_exec.TIMEOUT),
            ("build-tables", kernel_exec.build_tables_timeout(47)),
            ("settle-cases", kernel_exec.TIMEOUT),
            ("guard-sweep", kernel_exec.TIMEOUT),
            (
                "replay-strings",
                kernel_exec.replay_timeout(
                    symbols=symbols, max_length=5, configs=len(configs), threads=len(configs), last=False
                ),
            ),
            (
                "replay-strings",
                kernel_exec.replay_timeout(symbols=symbols, max_length=5, configs=1, threads=1, last=True),
            ),
        ]

    def test_the_two_growing_verbs_limits_grow_with_the_alphabet_and_never_fall_below_the_fixed_one(self):
        """At the measured alphabet a table build and a deep replay unit wait `TIMEOUT`, the floor. A table build's limit grows with the alphabet, and at the full alphabet's 47 symbols a whole replay at maximum length 5 in one call, five walks at once, waits longer than `TIMEOUT`. A narrower width, which walks configurations one after another, waits longer; a walk narrowed to one last symbol waits less."""
        measured = kernel_exec.MEASURED_SYMBOLS
        assert kernel_exec.build_tables_timeout(measured) == kernel_exec.TIMEOUT
        assert (
            kernel_exec.build_tables_timeout(47) > kernel_exec.build_tables_timeout(42) > kernel_exec.TIMEOUT
        )

        def replay(symbols, *, configs=5, threads=5, last=False):
            return kernel_exec.replay_timeout(
                symbols=symbols, max_length=5, configs=configs, threads=threads, last=last
            )

        assert replay(measured, configs=1, threads=1, last=True) == kernel_exec.TIMEOUT
        assert replay(47) > kernel_exec.TIMEOUT
        assert replay(47, threads=1) > replay(47) > replay(47, last=True)

    def test_a_call_past_its_limit_is_killed_after_waiting_that_long(self, monkeypatch):
        """Both runners wait for the limit the caller passes and no other. `_run_kernel` hands it to `communicate` and kills the child once it runs out. `_run_kernel_reaped` stops waiting on the child's pipes at that deadline and kills it, which closes them."""
        killed = []

        class Silent:
            def communicate(self, stdin=None, timeout=None):
                if timeout is not None:
                    killed.append(("waited", timeout))
                    raise subprocess.TimeoutExpired("ams-m1-kernel", timeout)
                return b"", b""

            def kill(self):
                killed.append("killed")

        monkeypatch.setattr(kernel_exec, "_spawn_kernel", lambda arguments, *, stdin: Silent())
        with pytest.raises(kernel_exec.KernelRunError, match="within 7 seconds on settle-cases"):
            kernel_exec._run_kernel(["ams-m1-kernel"], "settle-cases", b"", timeout=7)
        assert killed == [("waited", 7), "killed"]

        class Hung:
            def __init__(self):
                out, self._out = os.pipe()
                err, self._err = os.pipe()
                self.stdout = os.fdopen(out, "rb")
                self.stderr = os.fdopen(err, "rb")

            def kill(self):
                killed.append("hung killed")
                os.close(self._out)
                os.close(self._err)

            def wait(self):
                return -9

        monkeypatch.setattr(kernel_exec, "_spawn_kernel", lambda arguments, *, stdin: Hung())
        started = time.monotonic()
        with pytest.raises(kernel_exec.KernelRunError, match="on replay-strings"):
            kernel_exec._run_kernel_reaped(["ams-m1-kernel"], "replay-strings", timeout=0.2)
        assert time.monotonic() - started >= 0.2
        assert killed[-1] == "hung killed"

    def test_a_missing_binary_names_the_recipe_that_builds_one(self, monkeypatch, tmp_path):
        monkeypatch.setattr(kernel_exec, "BINARY", tmp_path / "ams-m1-kernel")
        with pytest.raises(kernel_exec.KernelRunError) as raised:
            kernel_exec.enumerate_configs(
                tmp_path / "spec.json", tmp_path / "streams", ["default"], threads=1
            )
        assert "make kernel-build" in str(raised.value)

    def test_a_machine_without_cargo_names_the_remedy(self, monkeypatch):
        def absent(*arguments, **rest):
            raise FileNotFoundError("cargo")

        monkeypatch.setattr(kernel_exec.subprocess, "run", absent)
        with pytest.raises(kernel_exec.KernelBuildError) as raised:
            kernel_exec.cargo_build()
        assert "Rust toolchain" in str(raised.value)

    def test_the_crate_is_built_once_per_process(self, monkeypatch):
        """`ensure_built` runs `cargo_build` once per process. `_BUILT` is a module attribute so a test can reset it."""
        builds = []
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        monkeypatch.setattr(kernel_exec, "cargo_build", lambda: builds.append(1))
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        kernel_exec.ensure_built()
        assert builds == [1]

    def test_a_named_mode_overrides_the_processs_own_mode_set(self, monkeypatch, tmp_path):
        """A `SettlementModes` passed to `_settle_cases` overrides the module defaults in both directions. With every default on, modes that turn both off add `--candidacy-prospect` and `--follower-prefer-slots-off`. With every default off, modes that turn both on add no flag."""
        case = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        calls = []

        class Finished:
            returncode = 0
            stdout = (case + '\t{"settled":"trace"}\n').encode()
            stderr = b""

        def run(arguments, verb, stdin=None, *, timeout):
            calls.append(arguments)
            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, True)
        kernel_exec._settle_cases(
            tmp_path / "spec.json",
            [case],
            frozenset(),
            kernel_exec.SettlementModes(simulated_prospect=False, follower_prefer_slots=False),
        )
        assert calls[0][4:] == ["--candidacy-prospect", "--follower-prefer-slots-off"]
        for _flag, module, attribute in kernel_exec.MODE_FLAGS:
            monkeypatch.setattr(module, attribute, False)
        kernel_exec._settle_cases(
            tmp_path / "spec.json",
            [case],
            frozenset(),
            kernel_exec.SettlementModes(simulated_prospect=True, follower_prefer_slots=True),
        )
        assert calls[1][4:] == []

    def test_a_refused_window_carries_the_crates_bucket_and_sentence(self, monkeypatch):
        """A crate refusal is `{raise, message}`. The caller gets a `SettleError` whose bucket is the `raise` value and whose message is the crate's message verbatim. It is not a `KernelRunError`, which is reserved for a failure of the kernel interface itself."""
        case = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        message = (
            "E-UNACCEPTED-EXIT: qsPea.half.ex-y5 committed an exit at x-height but qsTea has no acceptor cell"
        )
        refusal = json.dumps({"raise": "E-UNREACHABLE", "message": message}, separators=(",", ":"))

        class Finished:
            returncode = 0
            stdout = (case + "\t" + refusal + "\n").encode()
            stderr = b""

        monkeypatch.setattr(kernel_exec, "_run_kernel", lambda *arguments, **rest: Finished())
        with pytest.raises(SettleError) as raised:
            kernel_exec.settle_windows(SPEC, [case], frozenset())
        assert raised.value.bucket == "E-UNREACHABLE"
        assert str(raised.value) == message
        assert not isinstance(raised.value, kernel_exec.KernelRunError)
        with pytest.raises(SettleError) as traced:
            kernel_exec.settle_cases(SPEC, [case], frozenset(), decode=kernel_exec.trace_of)
        assert traced.value.bucket == "E-UNREACHABLE"
        assert str(traced.value) == message

    def test_settled_only_is_included_in_the_argv_settle_windows_builds_and_no_other(self, monkeypatch):
        """Only `settle_windows` passes `--settled-only` and gets the seven-field case result. `settle_cases` and `settle_sequences` get the full trace, because their callers need the ranking."""
        case = kernel_exec.case_line(LeftContext("edge"), RightToken("letter", "qsMay"), (EDGE,) * 4)
        record = {"cell": ["qsMay", "full", None, None, []], "junction": None, "extension": 0}
        trace = {
            "settled": record,
            "prospect": 0,
            "joint_tiebreak": False,
            "notes": [],
            "fired": [],
            "decided_stage": "only-candidate",
            "runner_up": None,
            "ranked": [],
            "eliminations": [],
        }
        calls = []

        def run(arguments, verb, stdin=None, *, timeout):
            calls.append(arguments)
            result = (
                "qsMay\tfull\t\t\t\t\t0"
                if "--settled-only" in arguments
                else json.dumps(trace, separators=(",", ":"))
            )

            class Finished:
                returncode = 0
                stdout = (case + "\t" + result + "\n").encode()
                stderr = b""

            return Finished()

        monkeypatch.setattr(kernel_exec, "_run_kernel", run)
        settled = kernel_exec.settle_windows(SPEC, [case], frozenset())[0]
        assert settled is not None and settled.cell.rune == "qsMay"
        assert kernel_exec.settle_cases(SPEC, [case], frozenset())[0] == trace
        traces = kernel_exec.settle_sequences(SPEC, [((RightToken("letter", "qsMay"),), frozenset())])[0]
        assert traces is not None and traces[0].settled is settled
        assert ["--settled-only" in arguments for arguments in calls] == [True, False, False]

    def test_the_settled_only_case_result_is_the_traces_own_settled_record(self):
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

    def test_a_forged_left_record_survives_the_case_line_both_ways(self):
        """The seven left-record fields pass through the case line intact. A left with adjustments, a junction, and a nonzero extension comes back as the same record under both result formats. Where the crate refuses such a left, the E-UNACCEPTED-EXIT message, the only place the left's full `cell_label` is written out with its adjustments, is identical under both formats and names every adjustment."""
        joined = LeftContext(
            "letter",
            Settled(
                CellId("qsMay", "full", None, "x-height", ("locked", "en-ext-1")),
                junction="x-height",
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
        unaccepted = LeftContext(
            "letter",
            Settled(
                CellId("qsTea", "full", None, "top", ("locked", "en-ext-1")), junction="top", extension=1
            ),
        )
        case = kernel_exec.case_line(unaccepted, RightToken("letter", "qsIt"), (EDGE,) * 4)
        with pytest.raises(SettleError) as fields:
            kernel_exec.settle_windows(SPEC, [case], frozenset())
        with pytest.raises(SettleError) as trace:
            kernel_exec.settle_cases(SPEC, [case], frozenset(), decode=kernel_exec.trace_of)
        assert str(fields.value) == str(trace.value)
        assert fields.value.bucket == trace.value.bucket == "E-UNREACHABLE"
        assert "locked" in str(fields.value) and "en-ext-1" in str(fields.value)

    def test_settle_windows_returns_one_settled_per_case_in_the_order_given(self, monkeypatch):
        """`settle_windows` returns one `Settled` per case, in the order given, and splits the cases into invocations of at most `batch` windows (`SETTLE_WINDOW_BATCH` by default)."""
        sizes = []
        original = kernel_exec._settle_cases

        def recording(spec_path, cases, features, modes=None, decode=None, settled_only=False):
            sizes.append(len(cases))
            assert settled_only
            return original(spec_path, cases, features, modes, decode, settled_only)

        monkeypatch.setattr(kernel_exec, "_settle_cases", recording)
        names = ("qsMay", "qsIt", "qsTea", "qsDay", "qsOy")
        cases = [
            kernel_exec.case_line(LeftContext("edge"), RightToken("letter", name), (EDGE,) * 4)
            for name in names
        ]
        settled = kernel_exec.settle_windows(SPEC, cases, frozenset(), batch=2)
        assert [None if outcome is None else outcome.cell.rune for outcome in settled] == list(names)
        assert sizes == [2, 2, 1]

    def test_settle_windows_can_give_none_for_a_refusal_and_keep_the_batch(self, monkeypatch):
        """With `on_error="drop"`, a refused case gets `None` in its slot and every other case decodes as usual, in order. This lets a caller prefill windows it may never read without one refusal failing the batch. The stubbed case results are settled-only tab records with one JSON refusal among them, so the test also checks that a refusal is recognized by its leading brace."""
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

    def test_guard_sweep_returns_the_complete_guard_verdict_map(self):
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
        """`guard_sweep_under` returns one configuration's guard verdict map with the same keys as the quantified map from `guard_sweep`, and runs the crate on every call. A quantified verdict blocks only where the verdict under every subset of the capability-unlock features blocks; `guard.rs` quantifies over the same powerset."""
        quantified = kernel_exec.guard_sweep(SPEC)
        features = spec_load.capability_features(SPEC)
        maps = [
            kernel_exec.guard_sweep_under(SPEC, frozenset(subset))
            for size in range(len(features) + 1)
            for subset in itertools.combinations(features, size)
        ]
        assert len(maps) == 2 ** len(features)
        assert all(verdict_map.keys() == quantified.keys() for verdict_map in maps)
        for key, blocked in quantified.items():
            assert blocked == all(verdict_map[key] for verdict_map in maps), key


@pytest.mark.parametrize(
    ("deep", "prospect", "follower_prefers", "wanted"),
    [
        (True, True, True, True),
        (True, True, False, True),
        (True, False, True, True),
        (True, False, False, False),
        (False, True, True, False),
        (False, False, False, False),
    ],
)
def test_the_class_grain_rule_needs_a_fiber_source(monkeypatch, deep, prospect, follower_prefers, wanted):
    """`AMS_DEEP_CLASSES` asks for class grain, and `class_grain` grants it only when the simulated prospect or the shifted follower prefer slots are on. With both off, the crate has no deep token to probe and enumerates at label grain whatever the flag says."""
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", deep)
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "FOLLOWER_PREFER_SLOTS_DEFAULT", follower_prefers)
    assert kernel_exec.class_grain() is wanted


@pytest.mark.parametrize(
    ("prospect", "follower_prefers", "deep", "wanted"),
    [
        (True, True, True, ["simulated-prospect", "follower-prefer-slots", "deep-classes"]),
        (True, False, True, ["simulated-prospect", "deep-classes"]),
        (False, True, True, ["follower-prefer-slots", "deep-classes"]),
        (True, True, False, ["simulated-prospect", "follower-prefer-slots"]),
        (False, False, True, []),
        (False, False, False, []),
    ],
)
def test_the_enumeration_tokens_name_every_flag_that_is_on(
    monkeypatch, prospect, follower_prefers, deep, wanted
):
    """Each flag changes settlement or enumeration grain without changing any hashed source, so a key over the sources alone could not tell a flag-on enumeration from a flag-off one. The tokens come in the order a stamp appends them. The class-grain token follows `class_grain`, not `DEEP_CLASSES_DEFAULT` alone, because with both settlement flags off the crate enumerates at label grain."""
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "FOLLOWER_PREFER_SLOTS_DEFAULT", follower_prefers)
    monkeypatch.setattr(kernel_exec, "DEEP_CLASSES_DEFAULT", deep)
    assert kernel_exec.enumeration_tokens() == wanted


def test_the_tables_stamp_appends_exactly_the_enumeration_tokens(monkeypatch):
    """`run_m1.tables_inputs` appends exactly the tokens `kernel_exec.enumeration_tokens` returns. `run_m1.previous_memos` (the memo head's mode set) and `run_m1.locality_lines` read the same function, so a new engine flag reaches all three."""
    monkeypatch.setattr(run_m1.fingerprint, "tables_value", lambda repo_root: "sources")
    assert run_m1.tables_inputs() == "+".join(["sources", *kernel_exec.enumeration_tokens()])


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheProductStandsAlone:
    """Nothing folds a stream on this side, so each product is checked by itself: rows come in the key order `fold::assert_key_sorted` requires, with no duplicates, and every cell a row names is in the product. The rows' joint flags are the trace's `joint_tiebreak` before the prospect-divergence pass; the crate test `fold::tests::the_prospect_pass_raises_joints_and_clears_none` covers that pass."""

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
        """A caller with no `out_dir` gets the tables and leaves no files: the kernel writes into a temporary directory, and the call returns each configuration's decision head and join rows."""
        monkeypatch.chdir(tmp_path)
        tables, digests = run_m1.build_tables(SPEC)
        assert list(tables) == list(conform.SETTLEMENT_CONFIGS)
        assert list(digests) == list(conform.SETTLEMENT_CONFIGS)
        assert all(decision.rules and joins.rows for decision, joins in tables.values())
        assert not sorted(tmp_path.iterdir())

    def test_a_narrowed_build_answers_for_the_configurations_it_was_asked_for(self, tmp_path):
        """With `configs=["default"]`, both returned mappings hold only `default`, with rules and join rows, and the out dir holds `default`'s TSVs and no file naming another configuration. The crate receives the narrowed list; the whole-set result is not filtered afterward."""
        out_dir = tmp_path / "one"
        tables, digests = run_m1.build_tables(SPEC, out_dir, inputs=STAMP, configs=["default"])
        assert list(tables) == ["default"] and list(digests) == ["default"]
        decision, joins = tables["default"]
        assert decision.rules and joins.rows
        assert (out_dir / "settlement-default.tsv").is_file()
        assert (out_dir / "joins-default.tsv").is_file()
        others = [config for config in conform.ACCEPTANCE_CONFIGS if config != "default"]
        assert not [path.name for path in out_dir.iterdir() if any(other in path.name for other in others)]

    def test_a_narrowed_build_files_the_windows_and_joins_the_whole_set_files(self, tmp_path):
        """`default` built alone enumerates the same windows and writes the same join TSV, byte for byte, as `default` built with the whole settlement set. `rebuild/tools/scratch_build.py --configs default` relies on this when it reads only `default`'s rows. Its settlement table matches only while the whole set imports nothing into `default`: the whole set's fold takes in the windows the other configurations keep live where `default`'s rules would answer them wrongly ahead of theirs (`rebuild/kernel-rs/src/crossconfig.rs`), and a narrowed build has no other configuration to take them from. The mini fixture imports nothing into `default`, so there the settlement TSV and the digest match too."""
        one, every = tmp_path / "one", tmp_path / "every"
        alone, narrowed = run_m1.build_tables(SPEC, one, inputs=STAMP, configs=["default"])
        whole_tables, whole = run_m1.build_tables(SPEC, every, inputs=STAMP)
        assert (one / "joins-default.tsv").read_bytes() == (every / "joins-default.tsv").read_bytes()
        _stamp, one_rows = table_module.read_windows(table_module.windows_path(one, "default"))
        _stamp, every_rows = table_module.read_windows(table_module.windows_path(every, "default"))
        assert one_rows.transitions == every_rows.transitions
        assert alone["default"][0].imports == ()
        assert whole_tables["default"][0].imports == (), "the fixture stopped importing nothing into default"
        assert narrowed["default"] == whole["default"]
        assert (one / "settlement-default.tsv").read_bytes() == (
            every / "settlement-default.tsv"
        ).read_bytes()

    def _observe_build(self, monkeypatch, tmp_path, asked, configs=None):
        """Record the arguments `build_tables` passes to `kernel_exec.build_table_files`: the configurations, the thread width, the timings tag, the stamp, and `default_memo_sharing`. The stub raises `Reached`, so the run ends there."""
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
            default_memo_sharing=True,
            **memo,
        ):
            seen.append((tuple(configs), threads, timings_tag, inputs, default_memo_sharing))
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
            (None, None),
            (2, 2),
            (99, len(conform.SETTLEMENT_CONFIGS)),
        ],
    )
    def test_the_thread_width_is_how_many_configurations_run_at_once(
        self, monkeypatch, tmp_path, asked, wanted
    ):
        """One process builds every settlement configuration, `default` first and the rest as deltas over its memo, and `threads` is how many deltas run at once; with no width asked for, it is the width this machine derives for the settlement set. The crate labels each configuration's timing lines itself, so no tag is passed, and the overlay configuration is never requested."""
        seen = self._observe_build(monkeypatch, tmp_path, asked)
        assert len(seen) == 1
        configs, threads, tag, stamp, default_memo_sharing = seen[0]
        assert configs == conform.SETTLEMENT_CONFIGS
        if wanted is None:
            wanted = kernel_exec.kernel_threads_default(configs=CONFIG_COUNT)
        assert threads == min(wanted, len(conform.SETTLEMENT_CONFIGS), run_m1.usable_cores())
        assert tag is None
        assert stamp == STAMP
        assert default_memo_sharing

    def test_the_memo_write_order_reaches_the_crate(self, monkeypatch, tmp_path):
        """`run_m1.build_tables` passes the crate the memo write order its caller chose. When the caller chose none, as a bare run does, it passes what `kernel_exec.memo_writes_overlap` derives at the build's width for the configurations it builds, with nothing beside it."""
        passed = []
        asked = []

        def build_table_files(*_args, overlap_memo_writes=False, **_rest):
            passed.append(overlap_memo_writes)
            raise Reached

        def memo_writes_overlap(width, **terms):
            asked.append((width, terms))
            return "derived"

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        monkeypatch.setattr(kernel_exec, "memo_writes_overlap", memo_writes_overlap)
        for chosen in (None, True, False):
            with pytest.raises(Reached):
                run_m1.build_tables(
                    SPEC, tmp_path, inputs=STAMP, kernel_threads=2, overlap_memo_writes=chosen
                )
        assert passed == ["derived", True, False]
        assert asked == [(min(2, run_m1.usable_cores()), {"configs": CONFIG_COUNT, "from_scratch": False})]

    def test_the_count_of_deltas_from_scratch_reaches_the_crate(self, monkeypatch, tmp_path):
        """`run_m1.build_tables` passes the crate the count of deltas to start from scratch beside `default` that its caller chose. When the caller chose none, as a bare run does, it passes what `kernel_exec.deltas_from_scratch` derives at the build's width and memo-write order for the configurations it builds, with nothing beside it."""
        passed = []
        asked = []

        def build_table_files(*_args, scratch_beside_default=0, **_rest):
            passed.append(scratch_beside_default)
            raise Reached

        def deltas_from_scratch(width, **terms):
            asked.append((width, terms))
            return 3

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        monkeypatch.setattr(kernel_exec, "deltas_from_scratch", deltas_from_scratch)
        for chosen in (None, 0, 2):
            with pytest.raises(Reached):
                run_m1.build_tables(
                    SPEC,
                    tmp_path,
                    inputs=STAMP,
                    kernel_threads=2,
                    overlap_memo_writes=True,
                    scratch_beside_default=chosen,
                )
        assert passed == [3, 0, 2]
        assert asked == [
            (min(2, run_m1.usable_cores()), {"configs": CONFIG_COUNT, "overlap": True, "from_scratch": False})
        ]

    def test_a_narrowed_cpu_allowance_narrows_the_fan_out(self, monkeypatch, tmp_path):
        """The width is also capped at `usable_cores()`, the cores this process may run on, so a container limited to part of its host's CPUs stays within that limit whatever the memory allows. The test fixes the allowance at two and asks for every configuration, so the test passes only if the cap applies."""
        allowance = 2
        monkeypatch.setattr(run_m1, "usable_cores", lambda: allowance)
        seen = self._observe_build(monkeypatch, tmp_path, len(conform.SETTLEMENT_CONFIGS))
        assert seen[0][1] == min(len(conform.SETTLEMENT_CONFIGS), allowance)

    def test_a_build_reading_the_previous_memos_files_the_bytes_a_from_scratch_build_files(
        self, tmp_path, monkeypatch
    ):
        """A build of an edited spec into a directory holding the previous build's memos reads them, with the edited rune named, and writes the same packed windows, settlement TSVs, and join TSVs, byte for byte, as a build of the edited spec into an empty directory. It also leaves its own memos under the edited spec's stamp."""
        asked = []
        asked_classes = []
        real = kernel_exec.build_table_files

        def build_table_files(*args, **rest):
            asked.append((rest.get("previous_memos"), rest.get("edited"), rest.get("memo_stamp")))
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
        reusing = tmp_path / "reusing"
        run_m1.build_tables(SPEC, reusing, inputs=STAMP)
        assert asked[-1] == (None, (), run_m1.memo_stamp(SPEC))
        for config in conform.SETTLEMENT_CONFIGS:
            assert kernel_exec.read_memo_head(kernel_exec.memo_path(reusing, config)) == kernel_exec.MemoHead(
                config, "+".join(kernel_exec.enumeration_tokens()), run_m1.memo_stamp(SPEC)
            )
        before = {path.name: path.read_bytes() for path in reusing.iterdir()}
        run_m1.build_tables(edited, reusing, inputs=STAMP)
        previous, named, stamp = asked[-1]
        assert previous is not None and named == ("qsTea",) and stamp == run_m1.memo_stamp(edited)
        assert asked_classes[-1] == ()
        scratch = tmp_path / "scratch"
        run_m1.build_tables(edited, scratch, inputs=STAMP)
        assert asked[-1] == (None, (), run_m1.memo_stamp(edited))
        moved = False
        for path in sorted(scratch.iterdir()):
            if path.name.startswith("memo-"):
                continue
            expected = path.read_bytes()
            assert (reusing / path.name).read_bytes() == expected, path.name
            moved |= before[path.name] != expected
        assert moved, "striking the refusal moves the tables, so the identity is not trivial"
        for config in conform.SETTLEMENT_CONFIGS:
            head = kernel_exec.read_memo_head(kernel_exec.memo_path(reusing, config))
            assert head is not None and head.stamp == run_m1.memo_stamp(edited)

    def test_a_build_into_an_out_dir_records_what_pairs_it_with_another(self, tmp_path, capsys):
        """A build into an `out_dir` ends its `[t] kernel_build_tables` line with its `TableBuildRecord`, which `cycle_timings.parse_inner_timings` reads back: the structure stamp its memos carry, the digest of the code the table build runs, the width, the rune count, and how many configurations read the previous build's memo. The first build reads none. A rebuild after a rune edit reads every configuration's memo, names the edited rune, and keeps the structure stamp and the code digest. The crate's own phase lines and that line are kept for run_m1's check line."""

        def recorded():
            (entry,) = [
                phase
                for phase in ct.parse_inner_timings(capsys.readouterr().out)
                if phase["label"] == "kernel_build_tables"
            ]
            return entry

        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, kernel_threads=2)
        first = recorded()
        assert first["structure"] == run_m1.memo_structure_stamp(SPEC)
        assert first["code"] == fingerprint.hash_paths(
            run_m1.REPO_ROOT, run_m1.table_build_code_paths(run_m1.REPO_ROOT)
        )
        assert {
            path.name for path in run_m1.table_build_code_paths(run_m1.REPO_ROOT) if path.suffix == ".py"
        } == run_m1.TABLE_BUILD_CODE_MODULES
        assert (first["memos_read"], first["edited"], first["classes"]) == (0, [], [])
        assert (first["width"], first["runes"]) == (run_m1._table_build_threads(2), len(SPEC.runes))
        tea = SPEC.runes["qsTea"]
        edited = replace(
            SPEC, runes={**SPEC.runes, "qsTea": replace(tea, policy=replace(tea.policy, refuse=()))}
        )
        run_m1.build_tables(edited, tmp_path, inputs=STAMP, kernel_threads=2)
        second = recorded()
        assert (second["memos_read"], second["edited"], second["classes"]) == (CONFIG_COUNT, ["qsTea"], [])
        assert (second["structure"], second["code"]) == (first["structure"], first["code"])
        labels = [phase["label"] for phase in ct.parse_inner_timings("\n".join(run_m1._build_phase_lines))]
        assert "enumerate[default]" in labels and labels[-1] == "kernel_build_tables"

    def test_the_records_code_digest_keeps_through_a_letter_batch_and_moves_with_the_crate(self, tmp_path):
        """Every letter batch adds its code point to `baseline_subset.M1_ALPHABET`, and a batch can touch a gate such as `conform`, so the record's `code` keeps through both edits, or no build before a batch could pair with the first build after it. An edit to the crate's code moves it."""
        pipeline = tmp_path / "rebuild" / "pipeline"
        source = tmp_path / "rebuild" / "kernel-rs" / "src"
        pipeline.mkdir(parents=True)
        source.mkdir(parents=True)
        (pipeline / "baseline_subset.py").write_text("M1_ALPHABET = frozenset({0xE650})\n", encoding="utf-8")
        (pipeline / "conform.py").write_text("UNCOVERED = frozenset({'qsAh'})\n", encoding="utf-8")
        (source / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        stamp = json.dumps({"structure": "s"})

        def code() -> str:
            return run_m1.table_build_record(SPEC, stamp, None, 1, root=tmp_path).code

        before = code()
        (pipeline / "baseline_subset.py").write_text(
            "M1_ALPHABET = frozenset({0xE650, 0xE661})\n", encoding="utf-8"
        )
        (pipeline / "conform.py").write_text("UNCOVERED = frozenset({'qsAh', 'qsThaw'})\n", encoding="utf-8")
        assert code() == before
        (source / "main.rs").write_text("fn main() { std::process::exit(1) }\n", encoding="utf-8")
        assert code() != before

    def test_a_configuration_delta_files_the_bytes_a_from_scratch_build_files(self, tmp_path):
        """Every configuration after `default`, enumerated as a delta over `default`'s memo, writes the same settlement TSV, join TSV, and window enumeration, byte for byte, and returns the same digest as the same configuration enumerated on its own (the window locality rule applied across configurations). The mini fixture's `ss03` unlocks a half-·Tea x-height entry, so the delta has windows to share and windows to settle itself. The memo-sharing run claims its deltas heaviest-first (`fanout::delta_worklist`), and its results must still match the from-scratch run configuration by configuration. So must those of a run three wide that starts the two heaviest deltas from scratch beside `default` and runs `ss04` and `ss05` as deltas: in the mini fixture `ss03` and `ss03+ss05` unlock the same runes, so they form the top tier the crate takes whole (`fanout::scratch_tiers`). Its memo files show that schedule ran, because a configuration enumerated from scratch memoizes every window it settles, while a delta memoizes only what `default`'s memo could not answer. A run that serves the deltas none of `default`'s liveness verdicts writes the sharing run's tables and memo files, byte for byte."""
        spec_path = tmp_path / "spec.json"
        kernel_io.write_spec(SPEC, spec_path)
        kernel_exec.ensure_built()
        answers = {}
        for name, default_memo_sharing, threads, beside, verdict_sharing in (
            ("sharing", True, 2, 0, True),
            ("scratch", False, 2, 0, True),
            ("beside", True, 3, 2, True),
            ("unserved", True, 2, 0, False),
        ):
            answers[name] = kernel_exec.build_table_files(
                spec_path,
                tmp_path / name,
                conform.SETTLEMENT_CONFIGS,
                inputs=STAMP,
                threads=threads,
                symbols=len(conform.spec_alphabet(SPEC)),
                default_memo_sharing=default_memo_sharing,
                memo_stamp=run_m1.memo_stamp(SPEC),
                scratch_beside_default=beside,
                verdict_sharing=verdict_sharing,
            ).digests
        assert answers["sharing"] == answers["scratch"] == answers["beside"] == answers["unserved"]
        for config in conform.SETTLEMENT_CONFIGS:
            for family in ("settlement", "joins", "windows"):
                name = f"{family}-{config}.tsv"
                for other in ("scratch", "beside", "unserved"):
                    assert (tmp_path / "sharing" / name).read_bytes() == (
                        tmp_path / other / name
                    ).read_bytes(), (other, name)
        memos = {
            (arm, config): (tmp_path / arm / f"memo-{config}.tsv").read_bytes()
            for arm in answers
            for config in conform.SETTLEMENT_CONFIGS
        }
        for config in ("ss03", "ss03+ss05"):
            assert memos["sharing", config] != memos["scratch", config] == memos["beside", config], config
        for config in ("default", "ss04", "ss05"):
            assert memos["sharing", config] == memos["beside", config], config
        for config in conform.SETTLEMENT_CONFIGS:
            assert memos["sharing", config] == memos["unserved", config], config

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
    def test_the_table_build_reads_previous_memos_of_the_configurations_it_builds(
        self, monkeypatch, tmp_path, configs
    ):
        """`build_tables` passes `previous_memos` the same configurations it passes the crate, so a whole-set build reads every settlement configuration's previous memo and a narrowed build only its own."""
        seen = {}

        def previous_memos(out_dir, stamp, scratch, configs):
            seen["previous"] = tuple(configs)
            return None

        def build_table_files(spec_path, out_dir, configs, **rest):
            seen["crate"] = tuple(configs)
            raise Reached

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(run_m1, "previous_memos", previous_memos)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        with pytest.raises(Reached):
            if configs is None:
                run_m1.build_tables(SPEC, tmp_path)
            else:
                run_m1.build_tables(SPEC, tmp_path, configs=configs)
        expected = tuple(conform.SETTLEMENT_CONFIGS) if configs is None else configs
        assert seen == {"previous": expected, "crate": expected}

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

    def test_previous_memos_reads_only_memos_whose_head_and_stamp_hold(self, tmp_path):
        stamp = run_m1.memo_stamp(SPEC)
        modes = "+".join(kernel_exec.enumeration_tokens())
        wrong = json.dumps({**json.loads(stamp), "structure": "elsewhere"})
        for config, head in (
            ("default", f"# {kernel_exec.MEMO_FORMAT}\tdefault\t{modes}\t{stamp}\n"),
            ("ss03", f"# {kernel_exec.MEMO_FORMAT}\tss03\t{modes}\t{wrong}\n"),
            ("ss04", f"# {kernel_exec.MEMO_FORMAT}\tss04\tanother-mode-set\t{stamp}\n"),
        ):
            with gzip.open(kernel_exec.memo_path(tmp_path, config), "wt") as handle:
                handle.write(head)
        previous = run_m1.previous_memos(tmp_path, stamp, tmp_path / "previous")
        assert previous is not None
        assert previous.configs == ("default",)
        assert previous.edited == ()
        assert previous.moved_classes == ()
        assert sorted(path.name for path in previous.directory.iterdir()) == ["memo-default.tsv"]
        assert run_m1.previous_memos(tmp_path / "empty", stamp, tmp_path / "previous2") is None

    def test_previous_memos_reads_only_the_configurations_the_build_names(self, tmp_path):
        """A narrowed build unpacks only its own configurations' memos and collects edited runes only from them. The default, the whole settlement set, reads every usable memo and collects edited runes from all of them."""
        stamp = run_m1.memo_stamp(SPEC)
        modes = "+".join(kernel_exec.enumeration_tokens())
        recorded = json.loads(stamp)
        rune = next(iter(recorded["runes"]))
        behind = json.dumps({**recorded, "runes": {**recorded["runes"], rune: "another-digest"}})
        for config, head in (
            ("default", f"# {kernel_exec.MEMO_FORMAT}\tdefault\t{modes}\t{stamp}\n"),
            ("ss03", f"# {kernel_exec.MEMO_FORMAT}\tss03\t{modes}\t{behind}\n"),
        ):
            with gzip.open(kernel_exec.memo_path(tmp_path, config), "wt") as handle:
                handle.write(head)
        narrowed = run_m1.previous_memos(tmp_path, stamp, tmp_path / "narrowed", configs=("default",))
        assert narrowed is not None
        assert narrowed.configs == ("default",)
        assert narrowed.edited == ()
        assert sorted(path.name for path in narrowed.directory.iterdir()) == ["memo-default.tsv"]
        whole = run_m1.previous_memos(tmp_path, stamp, tmp_path / "whole")
        assert whole is not None
        assert whole.configs == ("default", "ss03")
        assert whole.edited == (rune,)
        assert sorted(path.name for path in whole.directory.iterdir()) == [
            "memo-default.tsv",
            "memo-ss03.tsv",
        ]
        assert run_m1.previous_memos(tmp_path, stamp, tmp_path / "ss04", configs=("ss04",)) is None

    def test_a_memo_damaged_in_its_head_block_is_skipped_and_left_in_place(self, tmp_path):
        """Zeroed bytes after a valid gzip header make the head's decompression raise `zlib.error`, which `read_memo_head` treats as no readable memo."""
        packed = kernel_exec.memo_path(tmp_path, "default")
        packed.write_bytes(gzip.compress(b"")[:10] + bytes(4096))
        assert kernel_exec.read_memo_head(packed) is None
        assert run_m1.previous_memos(tmp_path, run_m1.memo_stamp(SPEC), tmp_path / "previous") is None
        assert packed.exists()

    def test_a_memo_damaged_after_a_readable_head_is_skipped_and_left_in_place(self, tmp_path):
        """A readable head followed by a gzip member whose deflate data is zeroed passes the head check, then raises `zlib.error` during unpacking, so `previous_memos` drops its partial plain file and reads no memo."""
        stamp = run_m1.memo_stamp(SPEC)
        modes = "+".join(kernel_exec.enumeration_tokens())
        packed = kernel_exec.memo_path(tmp_path, "default")
        head = f"# {kernel_exec.MEMO_FORMAT}\tdefault\t{modes}\t{stamp}\n"
        packed.write_bytes(gzip.compress(head.encode()) + gzip.compress(b"")[:10] + bytes(4096))
        assert kernel_exec.read_memo_head(packed) is not None
        assert run_m1.previous_memos(tmp_path, stamp, tmp_path / "previous") is None
        assert list((tmp_path / "previous").iterdir()) == []
        assert packed.exists()


class TestTheMemoryDerivedThreadDefault:
    """The default table-build width is the widest width, up to the configuration count, whose `table_build_booking_bytes` fits the machine's memory less the OS reserve: the configuration count when the whole wave fits, and otherwise the memory less `DEFAULT_MEMO_BYTES` and one `PARKED_FOLD_BYTES` per configuration, divided by `DELTA_SLOT_BYTES` and capped at the delta count. Its value depends on the machine running the suite, so these tests pass invented totals through the `total_bytes` keyword of `kernel_threads_default` and `replay_threads_default`, with the settlement set's configuration count."""

    @pytest.fixture(autouse=True)
    def _no_inherited_override(self, monkeypatch):
        """Clear `AMS_KERNEL_THREADS` and `AMS_REPLAY_THREADS` so a value exported in the shell cannot change these results."""
        monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
        monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)

    def test_the_shipped_default_is_a_startable_width(self):
        """The width this machine derives for the settlement set is an integer of at least one on any machine, because `how_many_fit` floors at one."""
        width = kernel_exec.kernel_threads_default(configs=len(conform.SETTLEMENT_CONFIGS))
        assert isinstance(width, int)
        assert width >= 1

    @pytest.mark.parametrize("stated, wanted", [("1", 1), ("3", 3), ("12", 12), ("0", 1), ("-3", 1)])
    def test_a_stated_width_short_circuits_ahead_of_the_arithmetic(self, monkeypatch, stated, wanted):
        """`AMS_KERNEL_THREADS` takes precedence over the memory arithmetic. Its value is floored at one and not otherwise capped here; `run_m1._table_build_threads` applies the configuration and core caps. The invented total is a terabyte, so the derived width would be the configuration count, which no stated value here equals, and a pass shows the override was used."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", stated)
        assert (
            kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=1_000_000_000_000) == wanted
        )

    @pytest.mark.parametrize("junk", ["", "   ", "banana", "9GB", "2.5"])
    def test_a_value_that_is_not_a_width_says_so_rather_than_being_quietly_ignored(self, monkeypatch, junk):
        """An unreadable `AMS_KERNEL_THREADS` raises an error naming the variable instead of falling back to the derived width. `AMS_TOTAL_MEMORY_BYTES` ignores a bad value, but this variable is set to keep a build out of swap, so silently using another width would defeat it. An empty or blank value counts as unreadable."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", junk)
        with pytest.raises(RuntimeError, match="AMS_KERNEL_THREADS"):
            kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=34_359_738_368)

    @pytest.mark.parametrize("junk", ["", "   ", "banana", "9GB", "2.5"])
    def test_a_replay_value_that_is_not_a_width_is_refused_the_same_way(self, monkeypatch, junk):
        """`AMS_REPLAY_THREADS` is read the same way as `AMS_KERNEL_THREADS`: a value that is not a bare count raises an error naming the variable."""
        monkeypatch.setenv("AMS_REPLAY_THREADS", junk)
        with pytest.raises(RuntimeError, match="AMS_REPLAY_THREADS"):
            kernel_exec.replay_threads_default(total_bytes=34_359_738_368)

    @pytest.mark.parametrize(
        "total, wanted", [(4_000_000_000, 1), (34_359_738_368, 3), (32_000_000_000, 2), (64_000_000_000, 5)]
    )
    def test_the_width_follows_the_machine_and_never_falls_below_one(self, total, wanted):
        """With `DEFAULT_MEMO_BYTES` (2.3 GB) and five parked products at `PARKED_FOLD_BYTES` (1.6 GB) off the machine, a 32 GiB machine fits three delta slots at `DELTA_SLOT_BYTES` (4.7 GB), and a decimal 32 GB machine fits two. A machine too small for one delta gets one, and a 64 GB machine fits the whole wave, every configuration at once, which is as wide as the width goes."""
        assert kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=total) == wanted

    def test_a_coresident_pool_comes_off_the_machine_before_it_is_divided(self):
        """`coresident_bytes` is memory used by something running beside the fan-out, such as the artifact cycle's pytest pool. It comes off the machine with the reserve, so 3 GB beside a 41 GB machine leaves too little for the whole wave's 31.2 GB booking, and the wave drops the slot of its own that `default`'s fold preparation takes. It defaults to zero because a bare run_m1 runs alone."""
        assert kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=41_000_000_000) == 5
        assert (
            kernel_exec.kernel_threads_default(
                configs=CONFIG_COUNT, coresident_bytes=3_000_000_000, total_bytes=41_000_000_000
            )
            == 4
        )

    def test_the_booking_counts_every_parked_product_and_each_slot_beyond_its_own(self):
        """Each configuration keeps its prepared fold product parked until the exchange ends, so every width books one `PARKED_FOLD_BYTES` per configuration beside `DEFAULT_MEMO_BYTES`. Each delta slot books `DELTA_SLOT_BYTES` beyond its own product, up to the delta count, and only a width above the delta count books the slot that runs `default`'s fold preparation alone, at `FOLD_PREPARATION_BYTES`. Below the whole wave, the width is the division by `DELTA_SLOT_BYTES` after the memo and the parked products come off the machine."""
        fixed = kernel_exec.DEFAULT_MEMO_BYTES + CONFIG_COUNT * kernel_exec.PARKED_FOLD_BYTES
        deltas = CONFIG_COUNT - 1
        for width in range(1, CONFIG_COUNT + 2):
            assert kernel_exec.table_build_booking_bytes(width, configs=CONFIG_COUNT) == (
                fixed
                + min(width, deltas) * kernel_exec.DELTA_SLOT_BYTES
                + (kernel_exec.FOLD_PREPARATION_BYTES if width > deltas else 0)
            )
        every = kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=34_359_738_368)
        budget = 34_359_738_368 - memory_budget.os_reserve_bytes(total_bytes=34_359_738_368) - fixed
        assert every == budget // kernel_exec.DELTA_SLOT_BYTES

    def test_the_memo_files_are_written_beside_the_wave_only_where_their_writers_still_fit(self):
        """`memo_writes_overlap` books `MEMO_WRITE_OVERLAP_BYTES` on top of a build's booking at its width. Over a range of invented machines, at the width `kernel_threads_default` derives, the memo files go beside the wave exactly when taking that term off the memory first would leave the width unchanged, except where taking it off floors that width at one, and the range holds a machine that runs the whole wave with its memo files written ahead. Memory something else holds beside the build counts against the writers as it does against the width, and a build without `default`'s memo writes each file ahead of its drain on any machine."""
        whole = kernel_exec.table_build_booking_bytes(CONFIG_COUNT, configs=CONFIG_COUNT)
        assert (
            kernel_exec.table_build_booking_bytes(CONFIG_COUNT, configs=CONFIG_COUNT, overlap=True)
            == whole + kernel_exec.MEMO_WRITE_OVERLAP_BYTES
        )
        seen = set()
        for total in range(4_000_000_000, 80_000_000_000, 250_000_000):
            width = kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=total)
            narrowed = kernel_exec.kernel_threads_default(
                configs=CONFIG_COUNT, coresident_bytes=kernel_exec.MEMO_WRITE_OVERLAP_BYTES, total_bytes=total
            )
            usable = total - memory_budget.os_reserve_bytes(total_bytes=total)
            floored = kernel_exec.table_build_booking_bytes(1, configs=CONFIG_COUNT, overlap=True) > usable
            overlaps = kernel_exec.memo_writes_overlap(width, configs=CONFIG_COUNT, total_bytes=total)
            assert overlaps == (narrowed == width and not floored), total
            seen.add((width, overlaps))
        assert {(CONFIG_COUNT, True), (CONFIG_COUNT, False), (1, False)} <= seen
        roomy = 1_000_000_000_000
        usable = roomy - memory_budget.os_reserve_bytes(total_bytes=roomy)
        assert kernel_exec.memo_writes_overlap(CONFIG_COUNT, configs=CONFIG_COUNT, total_bytes=roomy)
        assert not kernel_exec.memo_writes_overlap(
            CONFIG_COUNT, configs=CONFIG_COUNT, coresident_bytes=usable - whole, total_bytes=roomy
        )
        assert not kernel_exec.memo_writes_overlap(2, configs=2, total_bytes=roomy, from_scratch=True)

    def test_deltas_are_started_from_scratch_beside_default_while_their_peaks_fit(self):
        """`table_build_booking_bytes` books each delta started from scratch beside `default` at `SCRATCH_PEAK_BYTES` less its parked product in place of a delta's slot, and books such a build at no less than its start, where `default` and each of them hold a `SCRATCH_PEAK_BYTES` together; the count is capped at the delta count and one less than the width, as the crate caps it. `deltas_from_scratch` is the largest count whose booking, with the memo-write order given, fits the memory less the reserve and what runs beside, so over a range of invented machines it never shrinks as the memory grows and covers every count from none to all the deltas. A from-scratch build starts nothing beside `default`."""
        fixed = kernel_exec.DEFAULT_MEMO_BYTES + CONFIG_COUNT * kernel_exec.PARKED_FOLD_BYTES
        deltas = CONFIG_COUNT - 1
        slot = kernel_exec.SCRATCH_PEAK_BYTES - kernel_exec.PARKED_FOLD_BYTES
        for count in range(CONFIG_COUNT + 1):
            taken = min(count, deltas)
            wave = (
                fixed
                + taken * slot
                + (deltas - taken) * kernel_exec.DELTA_SLOT_BYTES
                + kernel_exec.FOLD_PREPARATION_BYTES
                + kernel_exec.MEMO_WRITE_OVERLAP_BYTES
            )
            assert kernel_exec.table_build_booking_bytes(
                CONFIG_COUNT, configs=CONFIG_COUNT, overlap=True, scratch_beside_default=count
            ) == (max(wave, (1 + taken) * kernel_exec.SCRATCH_PEAK_BYTES) if taken else wave)
        assert kernel_exec.table_build_booking_bytes(
            1, configs=CONFIG_COUNT, scratch_beside_default=deltas
        ) == kernel_exec.table_build_booking_bytes(1, configs=CONFIG_COUNT)
        roomy = 1_000_000_000_000
        for width in range(1, CONFIG_COUNT + 1):
            assert kernel_exec.deltas_from_scratch(
                width, configs=CONFIG_COUNT, overlap=True, total_bytes=roomy
            ) == min(deltas, width - 1)
        seen = []
        for total in range(30_000_000_000, 60_000_000_000, 100_000_000):
            usable = total - memory_budget.os_reserve_bytes(total_bytes=total)
            count = kernel_exec.deltas_from_scratch(
                CONFIG_COUNT, configs=CONFIG_COUNT, overlap=True, total_bytes=total
            )
            fits = [
                taken
                for taken in range(CONFIG_COUNT)
                if kernel_exec.table_build_booking_bytes(
                    CONFIG_COUNT, configs=CONFIG_COUNT, overlap=True, scratch_beside_default=taken
                )
                <= usable
            ]
            assert count == max(fits, default=0), total
            assert not seen or count >= seen[-1], total
            seen.append(count)
        assert set(range(CONFIG_COUNT)) <= set(seen)
        usable = roomy - memory_budget.os_reserve_bytes(total_bytes=roomy)
        whole = kernel_exec.table_build_booking_bytes(CONFIG_COUNT, configs=CONFIG_COUNT, overlap=True)
        assert (
            kernel_exec.deltas_from_scratch(
                CONFIG_COUNT,
                configs=CONFIG_COUNT,
                overlap=True,
                coresident_bytes=usable - whole,
                total_bytes=roomy,
            )
            == 0
        )
        assert (
            kernel_exec.deltas_from_scratch(2, configs=2, overlap=False, total_bytes=roomy, from_scratch=True)
            == 0
        )

    def test_a_build_without_default_s_memo_books_each_slot_from_scratch(self, monkeypatch, tmp_path):
        """A set without `default` enumerates every configuration from scratch, so its slots book `SCRATCH_PEAK_BYTES` less a parked product, not a delta's slot, and its width is that division, capped at the configuration count. On an invented 24 GB machine a two-configuration build that shares `default`'s memo runs both at once, while the from-scratch division fits one. `run_m1.build_tables` asks for that width exactly when `default` is not in the set it builds."""
        fixed = kernel_exec.DEFAULT_MEMO_BYTES + 2 * kernel_exec.PARKED_FOLD_BYTES
        slot = kernel_exec.SCRATCH_PEAK_BYTES - kernel_exec.PARKED_FOLD_BYTES
        assert kernel_exec.table_build_booking_bytes(2, configs=2, from_scratch=True) == fixed + 2 * slot
        total = 24_000_000_000
        budget = total - memory_budget.os_reserve_bytes(total_bytes=total) - fixed
        assert budget // slot < 2 == kernel_exec.kernel_threads_default(configs=2, total_bytes=total)
        assert (
            kernel_exec.kernel_threads_default(configs=2, total_bytes=total, from_scratch=True)
            == budget // slot
        )
        asked = []
        monkeypatch.setattr(
            kernel_exec, "kernel_threads_default", lambda **terms: asked.append(terms["from_scratch"]) or 1
        )
        for configs in (["ss03", "ss05"], ["default", "ss03"]):
            self._observe(monkeypatch, tmp_path, configs)
        assert asked == [True, False]

    @staticmethod
    def _observe(monkeypatch, tmp_path, configs):
        """Run `run_m1.build_tables` over `configs` with the crate stubbed out at its first call."""

        def build_table_files(*_args, **_rest):
            raise Reached

        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        with pytest.raises(Reached):
            run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, configs=configs)

    def test_a_stated_width_outranks_coresident_memory_too(self, monkeypatch):
        """A stated `AMS_KERNEL_THREADS` is used as given even when `coresident_bytes` would narrow the derived width."""
        monkeypatch.setenv("AMS_KERNEL_THREADS", "4")
        assert (
            kernel_exec.kernel_threads_default(
                configs=CONFIG_COUNT, coresident_bytes=60_000_000_000, total_bytes=64_000_000_000
            )
            == 4
        )


class TestTheStringReplay:
    """Tests for `kernel_exec.replay_strings` over tables built from the fixture. The crate replays the rules in the settlement TSVs over every text and compares each window's result with its own settlement. These tests check the per-configuration counts, that a family list narrows the texts, the memo options, and that an edited table raises an error naming the text."""

    @pytest.fixture(scope="class")
    def tables_dir(self, tmp_path_factory):
        out_dir = tmp_path_factory.mktemp("tables")
        run_m1.build_tables(SPEC, out_dir)
        return out_dir

    def test_a_clean_walk_answers_every_configuration_over_one_text_set(self, tables_dir):
        """A clean walk also hands `on_peak` the crate process's own peak RSS, once."""
        peaks: list[int] = []
        answered = kernel_exec.replay_strings(
            SPEC,
            tables_dir,
            conform.SETTLEMENT_CONFIGS,
            max_length=3,
            families=None,
            threads=2,
            on_peak=peaks.append,
        )
        assert len(peaks) == 1 and peaks[0] > 0
        assert sorted(answered) == sorted(conform.SETTLEMENT_CONFIGS)
        texts = {counts["texts"] for counts in answered.values()}
        assert len(texts) == 1
        alphabet = len(conform.spec_alphabet(SPEC))
        assert texts == {alphabet + alphabet**2 + alphabet**3}
        assert all(counts["skipped"] == 0 and counts["windows"] > 0 for counts in answered.values())

    def test_a_family_list_walks_only_the_texts_naming_it(self, tables_dir):
        whole = kernel_exec.replay_strings(
            SPEC, tables_dir, ["default"], max_length=3, families=None, threads=1
        )["default"]
        narrowed = kernel_exec.replay_strings(
            SPEC, tables_dir, ["default"], max_length=3, families=["qsPea"], threads=1
        )["default"]
        assert 0 < narrowed["texts"] < whole["texts"]
        assert narrowed["texts"] + narrowed["skipped"] == whole["texts"]
        with pytest.raises(ValueError):
            kernel_exec.replay_strings(SPEC, tables_dir, ["default"], max_length=3, families=[], threads=1)

    def test_a_last_symbol_walks_only_the_texts_ending_in_it(self, tables_dir, monkeypatch):
        """`last` reaches the subcommand as `--last=` and the symbol's code point. The walks over every symbol of the alphabet each walk the texts ending in it, and between them they partition a whole walk's texts, and with a family list a narrowed whole walk's texts and skipped texts. A last that is not one character raises `ValueError` before anything is spawned."""
        alphabet = conform.spec_alphabet(SPEC)

        def walk(families=None, last=None):
            return kernel_exec.replay_strings(
                SPEC, tables_dir, ["default"], max_length=3, families=families, threads=1, last=last
            )["default"]

        whole, named = walk(), walk(["qsPea"])
        units = [walk(last=symbol) for symbol in alphabet]
        assert {unit["texts"] for unit in units} == {1 + len(alphabet) + len(alphabet) ** 2}
        assert sum(unit["texts"] for unit in units) == whole["texts"]
        narrowed = [walk(["qsPea"], symbol) for symbol in alphabet]
        assert sum(unit["texts"] for unit in narrowed) == named["texts"]
        assert sum(unit["skipped"] for unit in narrowed) == named["skipped"]

        def spawned(arguments, verb, **rest):
            raise AssertionError(f"a refused walk spawned {verb}")

        monkeypatch.setattr(kernel_exec, "_run_kernel_reaped", spawned)
        for refused in ("", alphabet[:2]):
            with pytest.raises(ValueError, match="one symbol"):
                walk(last=refused)
        with pytest.raises(ValueError, match="memo directory or a last symbol"):
            kernel_exec.replay_strings(
                SPEC,
                tables_dir,
                ["default"],
                max_length=3,
                families=None,
                threads=1,
                last=alphabet[0],
                memo_dir=tables_dir,
            )

    def test_a_memo_directory_files_one_window_memo_per_configuration(self, tables_dir, tmp_path):
        """`memo_dir` is passed as `--memo-dir=`, and each configuration's window memo is written under it with the head `conform.absorb_replay_memo` reads. The counts match a walk without `memo_dir`, and that walk writes no memo beside the tables."""
        answered = kernel_exec.replay_strings(
            SPEC,
            tables_dir,
            conform.SETTLEMENT_CONFIGS,
            max_length=3,
            families=None,
            threads=2,
            memo_dir=tmp_path,
        )
        assert answered == kernel_exec.replay_strings(
            SPEC, tables_dir, conform.SETTLEMENT_CONFIGS, max_length=3, families=None, threads=2
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
            SPEC, tables_dir, conform.SETTLEMENT_CONFIGS, max_length=3, families=None, threads=1
        )
        capped = kernel_exec.replay_strings(
            SPEC,
            tables_dir,
            conform.SETTLEMENT_CONFIGS,
            max_length=3,
            families=None,
            threads=1,
            memo_windows=1,
        )
        assert sorted(capped) == sorted(uncapped)
        for config, counts in uncapped.items():
            assert capped[config]["texts"] == counts["texts"]
            assert capped[config]["skipped"] == counts["skipped"]
            assert capped[config]["windows"] > counts["windows"]

        def spawned(arguments, verb, **rest):
            raise AssertionError(f"a refused walk spawned {verb}")

        monkeypatch.setattr(kernel_exec, "_run_kernel", spawned)
        monkeypatch.setattr(kernel_exec, "_run_kernel_reaped", spawned)
        with pytest.raises(ValueError, match="not both"):
            kernel_exec.replay_strings(
                SPEC,
                tables_dir,
                ["default"],
                max_length=3,
                families=None,
                threads=1,
                memo_dir=tmp_path,
                memo_windows=1,
            )
        with pytest.raises(ValueError, match="at least one window"):
            kernel_exec.replay_strings(
                SPEC, tables_dir, ["default"], max_length=3, families=None, threads=1, memo_windows=0
            )
        assert not list(tmp_path.iterdir())

    def test_a_table_edited_behind_the_engine_is_refused_naming_the_text(self, tables_dir, tmp_path):
        """The refusal reaches `on_peak` with nothing, since only a clean walk is a reading of the replay's cost."""
        peaks: list[int] = []
        for name in ("settlement-default.tsv", "settlement-ss03.tsv"):
            (tmp_path / name).write_text((tables_dir / name).read_text())
        lines = (tmp_path / "settlement-default.tsv").read_text().splitlines()
        fields = lines[2].split("\t")
        fields[6] = f"{fields[0]}.perturbed"
        lines[2] = "\t".join(fields)
        (tmp_path / "settlement-default.tsv").write_text("\n".join(lines) + "\n")
        with pytest.raises(kernel_exec.ReplayDisagreement) as caught:
            kernel_exec.replay_strings(
                SPEC,
                tmp_path,
                ["default", "ss03"],
                max_length=3,
                families=None,
                threads=2,
                on_peak=peaks.append,
            )
        assert peaks == []
        assert "default" in str(caught.value)
        assert "replay disagreement" in str(caught.value)
        assert "at position" in str(caught.value)
        assert ".perturbed" in str(caught.value)


class TestTheReplayStage:
    """Tests for `run_m1.run_replay_strings`, the stage `run_m1.run` runs on the built tables beside the glyph chain: which texts it walks, what it records, and how a disagreement fails the build. The crate is stubbed; `TestTheStringReplay` exercises the real one."""

    def _record(self, structure, runes, **overrides):
        record = {
            "format": run_m1.REPLAY_FORMAT,
            "max_length": run_m1.REPLAY_MAX_LENGTH,
            "families": None,
            "walked": True,
            "configs": {},
            "structure": structure,
            "runes": dict(runes),
            "pass": True,
            "error": None,
        }
        record.update(overrides)
        return record

    def test_no_green_record_or_a_moved_structure_walks_every_text(self):
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

    def test_moved_imported_windows_walk_every_text(self):
        """A configuration's rules depend on the windows it imports from the others, which a rune edit can move for texts that name no edited rune, so a change in `run_m1.imports_digest` walks every text."""
        from rebuild.pipeline.table import DecisionTable

        runes = {name: f"d-{name}" for name in SPEC.runes}
        record = self._record("s1", runes, imports="i1")
        assert run_m1.replay_families(SPEC, record, "s1", runes, "i1") == []
        assert run_m1.replay_families(SPEC, record, "s1", runes, "i2") is None
        window = ("qsPea", "qsTea.full", "qsMay@", "#NA", "#NA", "#NA", "qsPea.half", "default")
        plain = {"ss03": (DecisionTable(config="ss03"), None)}
        importing = {"ss03": (DecisionTable(config="ss03", imports=(window,)), None)}
        assert run_m1.imports_digest(plain) != run_m1.imports_digest(importing)
        assert run_m1.imports_digest(importing) == run_m1.imports_digest(dict(importing))

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

    def test_the_structure_stamp_moves_with_the_max_length_and_the_semantics(self, monkeypatch):
        base = run_m1.replay_structure_stamp(SPEC)
        assert run_m1.replay_structure_stamp(SPEC) == base
        monkeypatch.setattr(run_m1, "REPLAY_MAX_LENGTH", run_m1.REPLAY_MAX_LENGTH + 1)
        assert run_m1.replay_structure_stamp(SPEC) != base
        monkeypatch.setattr(run_m1, "REPLAY_MAX_LENGTH", run_m1.REPLAY_MAX_LENGTH - 1)
        monkeypatch.setattr(kernel_exec, "enumeration_tokens", lambda: ["other-mode-set"])
        assert run_m1.replay_structure_stamp(SPEC) != base

    def test_the_stage_records_what_it_walked_and_walks_the_delta_next_time(self, monkeypatch, tmp_path):
        asked: list = []

        def replay_strings(
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
        ):
            asked.append((tuple(configs), max_length, families, threads))
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        digests = {name: f"d-{name}" for name in SPEC.runes}
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: dict(digests))
        first = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert first["pass"] and first["families"] is None and first["walked"]
        assert asked == [
            (tuple(conform.SETTLEMENT_CONFIGS), run_m1.REPLAY_MAX_LENGTH, None, run_m1._replay_threads(None))
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

    def test_a_walk_that_answers_records_its_share_of_the_crates_peak(self, monkeypatch, tmp_path):
        """A walk that answers records one `replay-walk` pool observation for `make job-costs`: the crate process's peak divided by the walks it ran at once."""
        from rebuild.tools import cycle_timings

        def replay_strings(
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
        ):
            if on_peak is not None:
                on_peak(9_000_000_000)
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)
        run_m1.run_replay_strings(SPEC, tmp_path, None, replay_threads=3)
        records = cycle_timings.load_pool_records(cycle_timings.JOURNAL)
        assert [(r["unit"], r["width"], r["worker_peak_rss_bytes"]) for r in records] == [
            (run_m1.REPLAY_POOL_UNIT, 3, {"per walk": 3_000_000_000})
        ]

    def test_the_stage_walks_at_its_own_width_and_a_stated_one_is_only_ever_narrowed(
        self, monkeypatch, tmp_path
    ):
        """The replay's width comes from `_replay_threads`, not from the table build's width. With none stated it is `kernel_exec.replay_threads_default()` capped at the configuration count and the cores. A stated width reaches the crate unchanged unless it exceeds the configuration count, which caps it. The test clears `AMS_REPLAY_THREADS` and fixes the cores at 64 so the configuration count is the binding cap."""
        asked: list = []

        def replay_strings(
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
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
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
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
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
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
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
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
            raise RuntimeError("the chain's own error")

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: {})
        summary = run_m1.run_replay_strings(SPEC, tmp_path, "stamp")
        assert not summary["pass"]
        assert "qsPea" in summary["error"]
        assert run_m1.read_replay_record(tmp_path) == summary
        assert run_m1.replay_families(SPEC, summary, "s1", {}) is None

        monkeypatch.setattr(run_m1, "build_tables", lambda spec, out_dir, **rest: ({}, {}))
        monkeypatch.setattr(run_m1, "run_emitted_order", lambda *args, **rest: {"pass": True, "error": None})
        monkeypatch.setattr(run_m1, "mint_cell_glyphs", minting_fails)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1.run(out_dir=tmp_path, spec=SPEC, inputs="stamp")
