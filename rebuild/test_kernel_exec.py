"""Tests for `rebuild/pipeline/kernel_exec.py`, the kernel interface, and for the `run_m1` code that calls it: the mode flags passed to the crate, the product and tables it returns, the table build record's structure stamp, the thread widths, the CLI, and the string replay. Every table is built on the mini fixture, which is enough to check the shape of the results; enumerating the live alphabet is the build's job.

No test skips. On a machine without `cargo` these tests fail with the remedy `KernelBuildError` carries, because the M1 build cannot run there either.
"""

import fcntl
import io
import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import fields, replace

import pytest

from rebuild.pipeline import (
    conform,
    fingerprint,
    fixtures,
    kernel_exec,
    kernel_io,
    oracle_cache,
    run_m1,
    run_m1_gates,
    settle,
    spec_load,
)
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import CellId, ResolvedSpec, Settled
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


_INTERRUPTED_RUN = """
import signal
import sys
from pathlib import Path

from rebuild.pipeline import kernel_exec

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.signal(signal.SIGHUP, signal.SIG_DFL)
spawn = kernel_exec._spawn_kernel


def spawned(arguments, **rest):
    process = spawn(arguments, **rest)
    print(process.pid, flush=True)
    return process


kernel_exec._spawn_kernel = spawned
try:
    kernel_exec.stream_payloads(
        {"default": kernel_exec.windows_payload(Path(sys.argv[1]), "default")},
        lambda _name, source: source.read(),
        lambda: kernel_exec._run_kernel_reaped(["/bin/sleep", "120"], "build-tables", timeout=600),
    )
except KeyboardInterrupt:
    print("interrupted", flush=True)
"""


_ORPHANED_WORKERS = """
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

from rebuild.pipeline import kernel_exec, run_m1


def park(where, scratch):
    state = run_m1._REPLAY_WORKER
    state.reports.put((where, os.getpid()))
    if where == "slot":
        state.gate.acquire()
    elif where == "verdict":
        state.decided.wait()
    elif where == "walk":
        spawn = kernel_exec._spawn_kernel

        def spawned(arguments, **rest):
            process = spawn(arguments, **rest)
            state.reports.put(("crate", process.pid))
            return process

        kernel_exec._spawn_kernel = spawned
        kernel_exec.stream_payloads(
            {"memo": Path(scratch) / "memo.bin"},
            lambda _name, source: source.read(),
            lambda: kernel_exec._run_kernel_reaped(["/bin/sleep", "120"], "replay-strings", timeout=600),
        )


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    context = multiprocessing.get_context("spawn")
    gate, reports, decided = context.Semaphore(0), context.Queue(), context.Event()
    shared = (gate, reports, decided, context.Value("b", 0), context.Value("i", 0), 4, False)
    pool = run_m1._spawn_pool(4, 4, initializer=run_m1._replay_worker_init, initargs=(False, "spec.json", *shared))
    for where in ("slot", "verdict", "walk"):
        pool.submit(park, where, sys.argv[1])
    for _ in range(4):
        print(*reports.get(), flush=True)
    pool.submit(park, "idle", sys.argv[1]).result()
    print(*reports.get(), flush=True)
    time.sleep(600)
"""


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

        def reaped(arguments, verb, *, timeout, env=None, pipes=None):
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
                kernel_io.spec_bytes(SPEC), tmp_path / "tables", configs, inputs=STAMP, threads=2, symbols=47
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

    def test_on_macos_the_table_build_and_the_replay_run_with_the_allocators_large_cache_off(
        self, monkeypatch, tmp_path
    ):
        """On macOS, `build_table_files` spawns `build-tables`, and `replay_strings` spawns `replay-strings` for the string replay and for each deep replay unit, with this process's environment and `KERNEL_ALLOCATOR_ENVIRONMENT` (`MallocLargeCache=0`), so the tables a memo outgrows go back to the system as they are freed. Elsewhere each passes no environment, which leaves the child this process's own."""
        environments = []

        def reaped(arguments, verb, *, timeout, env=None, pipes=None):
            environments.append((verb, env))
            raise kernel_exec._no_answer(arguments, verb, timeout)

        monkeypatch.setattr(kernel_exec, "_run_kernel_reaped", reaped)
        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setenv("AMS_SOME_SETTING", "kept")
        calls = [
            lambda: kernel_exec.build_table_files(
                kernel_io.spec_bytes(SPEC),
                tmp_path / "tables",
                ["default"],
                inputs=STAMP,
                threads=1,
                symbols=47,
            ),
            lambda: kernel_exec.replay_strings(
                SPEC, tmp_path, ["default"], max_length=4, families=None, threads=1, memo_dir=tmp_path
            ),
            lambda: kernel_exec.replay_strings(
                SPEC,
                tmp_path,
                ["default"],
                max_length=5,
                families=None,
                threads=1,
                memo_windows=8,
                last="\ue650",
            ),
        ]
        for platform in ("darwin", "linux"):
            monkeypatch.setattr(kernel_exec.sys, "platform", platform)
            for call in calls:
                with pytest.raises(kernel_exec.KernelRunError):
                    call()
        verbs = ["build-tables", "replay-strings", "replay-strings"]
        allocator = {**os.environ, "MallocLargeCache": "0"}
        assert environments == [(verb, allocator) for verb in verbs] + [(verb, None) for verb in verbs]
        assert environments[0][1]["AMS_SOME_SETTING"] == "kept"

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

        monkeypatch.setattr(
            kernel_exec, "_spawn_kernel", lambda arguments, *, stdin, env=None, pass_fds=(): Hung()
        )
        started = time.monotonic()
        with pytest.raises(kernel_exec.KernelRunError, match="on replay-strings"):
            kernel_exec._run_kernel_reaped(["ams-m1-kernel"], "replay-strings", timeout=0.2)
        assert time.monotonic() - started >= 0.2
        assert killed[-1] == "hung killed"

    @pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
    def test_an_interrupt_kills_and_reaps_the_child_before_it_propagates(self, tmp_path, stop):
        """A real stop signal sent to the waiting process alone, as `kill -INT`, `kill -TERM` or `kill -HUP` sends one, reaches its caller as `KeyboardInterrupt` only once `_run_kernel_reaped` has killed and reaped the child and `kernel_exec.stream_payloads` has ended its FIFO's read and removed the FIFO, so no crate outlives the caller's cleanup or is left to block in the open of a FIFO no one reads. A SIGTERM or SIGHUP would otherwise end the process with no cleanup at all. A `sleep` stands in for the crate. The waiting process starts from the dispositions a run started from a terminal inherits, so the test does not depend on whether the suite was started under `nohup` or in the background."""
        waiter = subprocess.Popen(
            [sys.executable, "-c", _INTERRUPTED_RUN, str(tmp_path)],
            cwd=kernel_exec.REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert waiter.stdout is not None
            child = int(waiter.stdout.readline())
            time.sleep(0.5)
            waiter.send_signal(stop)
            out, err = waiter.communicate(timeout=60)
        finally:
            if waiter.poll() is None:
                waiter.kill()
                waiter.wait()
        assert out.split() == ["interrupted"], err
        assert not list(tmp_path.iterdir())
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        os.kill(child, signal.SIGKILL)
        pytest.fail("the child outlived the interrupt")

    def test_a_crate_that_exits_before_it_reads_its_spec_fails_and_leaves_no_writer_behind(self, tmp_path):
        """A configuration named twice is a usage error `build-tables` gives before it reads its spec, so the crate exits without reading the pipe its spec comes through. The dump is padded past what a pipe holds with the whitespace JSON allows, so its writer is still writing when the crate exits and must end on the broken pipe instead of blocking. The build fails as a usage error, and no thread the call started outlives it."""
        kernel_exec.ensure_built()
        before = set(threading.enumerate())
        with pytest.raises(kernel_exec.KernelRunError, match="usage error"):
            kernel_exec.build_table_files(
                kernel_io.spec_bytes(SPEC).ljust(1 << 20),
                tmp_path / "tables",
                ["default", "default"],
                inputs=STAMP,
                threads=1,
                symbols=len(conform.spec_alphabet(SPEC)),
            )
        assert [thread for thread in threading.enumerate() if thread not in before] == []

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

    def test_a_build_keeps_the_relink_lock_through_the_new_binarys_first_run(self, monkeypatch, tmp_path):
        """macOS kills a new binary's first run if the file disappears during its scan, so `cargo_build` runs the binary to its exit before another build may replace it, and a first run that ends in anything but the usage exit fails the build."""
        monkeypatch.setattr(kernel_exec, "LOCK_PATH", tmp_path / "relink.lock")
        ran = []
        first_run_exit = 2

        def run(arguments, **rest):
            with kernel_exec.LOCK_PATH.open() as second_open, pytest.raises(BlockingIOError):
                fcntl.flock(second_open, fcntl.LOCK_SH | fcntl.LOCK_NB)
            ran.append(arguments[0])
            returncode = 0 if arguments[0] == "cargo" else first_run_exit
            return subprocess.CompletedProcess(arguments, returncode, b"", b"")

        monkeypatch.setattr(kernel_exec.subprocess, "run", run)
        kernel_exec.cargo_build()
        assert ran == ["cargo", str(kernel_exec.BINARY)]

        first_run_exit = -9
        with pytest.raises(kernel_exec.KernelRunError, match="exited -9 on its first run"):
            kernel_exec.cargo_build()

    def test_make_kernel_build_takes_the_relink_lock_and_leaves_cargo_on_the_terminal(
        self, monkeypatch, tmp_path
    ):
        """A `make kernel-build` by hand beside a running suite replaces the binary like any other build, so its recipe is this module's build and not cargo's. An author still sees all of cargo's output, not the first run's usage text, and a failed build ends in one line."""
        recipe = subprocess.run(
            ["make", "-n", "kernel-build"],
            cwd=kernel_exec.REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "python -m rebuild.pipeline.kernel_exec" in recipe.stdout
        monkeypatch.setattr(kernel_exec, "LOCK_PATH", tmp_path / "relink.lock")
        captured = {}
        cargo_exit = 0

        def run(arguments, **rest):
            captured[arguments[0]] = rest["capture_output"]
            returncode = cargo_exit if arguments[0] == "cargo" else 2
            return subprocess.CompletedProcess(arguments, returncode, None, None)

        monkeypatch.setattr(kernel_exec.subprocess, "run", run)
        kernel_exec.main()
        assert captured == {"cargo": False, str(kernel_exec.BINARY): True}

        cargo_exit = 101
        with pytest.raises(SystemExit, match=r"^the kernel did not build \(cargo exited 101\)$"):
            kernel_exec.main()

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
    """`run_m1.tables_inputs` appends exactly the tokens `kernel_exec.enumeration_tokens` returns. `run_m1.memo_structure_stamp` and `run_m1.locality_lines` read the same function, so a new engine flag reaches every stamp."""
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
    def test_every_configuration_digest_and_class_id_matches_python_hashlib(self, tmp_path):
        """The digests returned by `run_m1.build_tables` and the deep-class tokens in every configuration match Python's independent `hashlib` contracts. The returned tables hold only their heads, so the digest comparison reads each synthetic build's retained windows to include every row the crate hashes."""
        tables, digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        assert list(tables) == list(conform.SETTLEMENT_CONFIGS)
        assert list(digests) == list(conform.SETTLEMENT_CONFIGS)
        assert any(head.deep_classes for head, _joins in tables.values())
        for config, (_head, joins) in tables.items():
            stamp, decision = table_module.read_windows(table_module.windows_path(tmp_path, config))
            assert stamp == STAMP
            assert decision.transitions
            assert digests[config] == table_module.table_digest(decision, joins), config
            for token, members in decision.deep_classes.items():
                assert token == table_module.deep_class_id(members), (config, token, members)

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
            spec_bytes,
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

    def test_the_count_of_deltas_from_scratch_reaches_the_crate(self, monkeypatch, tmp_path):
        """`run_m1.build_tables` passes the crate the count of deltas to start from scratch beside `default` that its caller chose. When the caller chose none, as a bare run does, it passes what `kernel_exec.deltas_from_scratch` derives at the build's width for the configurations it builds, with nothing beside it."""
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
                    scratch_beside_default=chosen,
                )
        assert passed == [3, 0, 2]
        assert asked == [(min(2, run_m1.usable_cores()), {"configs": CONFIG_COUNT, "from_scratch": False})]

    def test_a_narrowed_cpu_allowance_narrows_the_fan_out(self, monkeypatch, tmp_path):
        """The width is also capped at `usable_cores()`, the cores this process may run on, so a container limited to part of its host's CPUs stays within that limit whatever the memory allows. The test fixes the allowance at two and asks for every configuration, so the test passes only if the cap applies."""
        allowance = 2
        monkeypatch.setattr(run_m1, "usable_cores", lambda: allowance)
        seen = self._observe_build(monkeypatch, tmp_path, len(conform.SETTLEMENT_CONFIGS))
        assert seen[0][1] == min(len(conform.SETTLEMENT_CONFIGS), allowance)

    def test_a_build_into_an_out_dir_records_what_pairs_it_with_another(self, tmp_path, capsys):
        """A build into an `out_dir` ends its `[t] kernel_build_tables` line with its `TableBuildRecord`, which `cycle_timings.parse_inner_timings` reads back: the structure stamp, the digest of the code the table build runs, the width and the rune count, then the `build-tables` process's own resident peak and, on Darwin, its peak footprint, which `build_table_files` reads before it reaps the process. A rebuild after a rune edit keeps the structure stamp and the code digest. The crate's own phase lines and that line are kept for run_m1's check line."""

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
        assert (first["width"], first["runes"]) == (run_m1._table_build_threads(2), len(SPEC.runes))
        assert "rss_gb" in first and ("footprint_gb" in first or sys.platform != "darwin")
        tea = SPEC.runes["qsTea"]
        edited = replace(
            SPEC, runes={**SPEC.runes, "qsTea": replace(tea, policy=replace(tea.policy, refuse=()))}
        )
        run_m1.build_tables(edited, tmp_path, inputs=STAMP, kernel_threads=2)
        second = recorded()
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

        def code() -> str:
            return run_m1.table_build_record(SPEC, 1, root=tmp_path).code

        before = code()
        (pipeline / "baseline_subset.py").write_text(
            "M1_ALPHABET = frozenset({0xE650, 0xE661})\n", encoding="utf-8"
        )
        (pipeline / "conform.py").write_text("UNCOVERED = frozenset({'qsAh', 'qsThaw'})\n", encoding="utf-8")
        assert code() == before
        (source / "main.rs").write_text("fn main() { std::process::exit(1) }\n", encoding="utf-8")
        assert code() != before

    def test_a_configuration_delta_files_the_bytes_a_from_scratch_build_files(self, monkeypatch, tmp_path):
        """Every configuration after `default`, enumerated as a delta over `default`'s memo, writes the same settlement TSV, join TSV, and window enumeration, byte for byte, and returns the same digest as the same configuration enumerated on its own (the window locality rule applied across configurations). The four runs build the mini fixture narrowed to ·Tea, ·Day, ·May and ·It (`spec_load.narrowed`), whose window enumerations are a small fraction of the whole fixture's. There `ss03` allows `·May ~x~ ·Tea.half` and `ss04` allows `·Day ~b~ ·It ~b~ ·May`, so those deltas have windows to share and windows to settle themselves. The memo-sharing run claims its deltas heaviest-first (`fanout::delta_worklist`), and its results must still match the from-scratch run configuration by configuration. So must those of a run three wide that starts the two heaviest deltas from scratch beside `default` and runs `ss04` and `ss05` as deltas: `ss03` and `ss03+ss05` unlock the same two runes, ·Tea and ·May, where `ss04` and `ss05` unlock one each, so the first two form the top tier the crate takes whole (`fanout::scratch_tiers`). A run that serves the deltas none of `default`'s liveness verdicts writes the sharing run's tables, byte for byte. Each run's crate is asked for its own schedule: the sharing run passes none of the schedule flags, and each other run passes only its own."""
        spec = spec_load.narrowed(SPEC, ("qsTea", "qsDay", "qsMay", "qsIt"))
        kernel_exec.ensure_built()
        run_kernel_reaped = kernel_exec._run_kernel_reaped
        schedules = {}

        def reaped(arguments, verb, **rest):
            schedules[os.path.basename(arguments[3])] = {
                argument
                for argument in arguments
                if argument.startswith(
                    ("--no-default-memo-sharing", "--no-verdict-sharing", "--scratch-beside-default")
                )
            }
            return run_kernel_reaped(arguments, verb, **rest)

        monkeypatch.setattr(kernel_exec, "_run_kernel_reaped", reaped)
        answers = {}
        for name, default_memo_sharing, threads, beside, verdict_sharing in (
            ("sharing", True, 2, 0, True),
            ("scratch", False, 2, 0, True),
            ("beside", True, 3, 2, True),
            ("unserved", True, 2, 0, False),
        ):
            answers[name] = kernel_exec.build_table_files(
                kernel_io.spec_bytes(spec),
                tmp_path / name,
                conform.SETTLEMENT_CONFIGS,
                inputs=STAMP,
                threads=threads,
                symbols=len(conform.spec_alphabet(spec)),
                default_memo_sharing=default_memo_sharing,
                scratch_beside_default=beside,
                verdict_sharing=verdict_sharing,
            ).digests
        assert schedules == {
            "sharing": set(),
            "scratch": {"--no-default-memo-sharing"},
            "beside": {"--scratch-beside-default=2"},
            "unserved": {"--no-verdict-sharing"},
        }
        assert answers["sharing"] == answers["scratch"] == answers["beside"] == answers["unserved"]
        for config in conform.SETTLEMENT_CONFIGS:
            for family in ("settlement", "joins", "windows"):
                name = f"{family}-{config}.tsv"
                for other in ("scratch", "beside", "unserved"):
                    assert (tmp_path / "sharing" / name).read_bytes() == (
                        tmp_path / other / name
                    ).read_bytes(), (other, name)

    def test_an_unstamped_build_names_a_stamp_the_kernel_will_accept(self, monkeypatch, tmp_path):
        """`build-tables` requires a stamp, so a build called without `inputs` passes `kernel_exec.UNSTAMPED_WINDOWS`. The build then keeps only the payload's head and drops the rest as it arrives, so that stamp never reaches an artifact."""
        seen = []

        def build_table_files(spec_bytes, out_dir, configs, *, inputs, **rest):
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

    @pytest.mark.parametrize(
        "argv, kernel, replay",
        [
            ([], None, None),
            (["--kernel-threads", "5"], 5, None),
            (["--replay-threads", "3"], None, 3),
            (["--kernel-threads", "5", "--replay-threads", "3"], 5, 3),
        ],
    )
    def test_the_cli_carries_the_thread_width_into_run(self, monkeypatch, tmp_path, argv, kernel, replay):
        """`--kernel-threads` and `--replay-threads` reach `run` as separate keywords. If one arrived as the other, every later test would still pass while the cycle sized the wrong stage."""
        from rebuild.tools import artifact_cycle

        seen = {}

        def run(**rest):
            seen.update(rest)
            raise Reached

        monkeypatch.setattr(artifact_cycle, "run_m1_skip_fingerprint", lambda root: "pinned-key")
        monkeypatch.setattr(run_m1, "OUT_DIR", tmp_path)
        monkeypatch.setattr(run_m1.fingerprint, "stage_a", lambda root: {})
        monkeypatch.setattr(run_m1_gates.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
        monkeypatch.setattr(run_m1_gates.baseline_subset, "ensure_fresh", lambda root, before_refilter: False)
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
        monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
        monkeypatch.setattr(run_m1, "run", run)
        with pytest.raises(Reached):
            run_m1_gates.main(argv)
        assert seen["kernel_threads"] == kernel and seen["replay_threads"] == replay
        assert "fold_jobs" not in seen


class TestThePayloadPath:
    """Tests for what a caller puts at the crate's window payload path (`kernel_exec.windows_payload`), which the crate opens with `File::create`, and for the FIFOs `kernel_exec.stream_payloads` reads payloads through. `rebuild/test_run_m1_tail.py` tests the build's packing through them, and `rebuild/test_scaling_sweep.py` the sweep's count."""

    def test_the_crate_writes_its_payload_through_what_the_caller_put_at_the_path(self, tmp_path):
        """With nothing at the path, as a test leaves it, the crate writes the payload as a file whose head reads back under the build's stamp. With a symbolic link to the null device there, as a reading that wants no payload puts, the link is left as it was and nothing is written through it, and the build answers with the same digest and settlement table. The build is `default` over the mini fixture narrowed to ·Tea, ·May and ·It."""
        spec = spec_load.narrowed(SPEC, ("qsTea", "qsMay", "qsIt"))
        kernel_exec.ensure_built()
        built = {}
        for put in ("nothing", "null device"):
            out_dir = tmp_path / put
            out_dir.mkdir()
            if put == "null device":
                kernel_exec.windows_payload(out_dir, "default").symlink_to(os.devnull)
            built[put] = kernel_exec.build_table_files(
                kernel_io.spec_bytes(spec),
                out_dir,
                ["default"],
                inputs=STAMP,
                threads=1,
                symbols=len(conform.spec_alphabet(spec)),
            )
        written = kernel_exec.windows_payload(tmp_path / "nothing", "default")
        assert written.is_file() and not written.is_symlink()
        with written.open("rt", encoding="utf-8") as handle:
            stamp, head = table_module.read_windows(handle, windows=False)
        assert stamp == STAMP and head.rules
        linked = kernel_exec.windows_payload(tmp_path / "null device", "default")
        assert linked.is_symlink() and os.readlink(linked) == os.devnull
        assert built["null device"].digests == built["nothing"].digests
        assert (tmp_path / "null device" / "settlement-default.tsv").read_bytes() == (
            tmp_path / "nothing" / "settlement-default.tsv"
        ).read_bytes()

    @pytest.mark.parametrize("late, runs", [(False, 1), (True, 1), (False, 200)])
    def test_a_fifo_no_crate_opens_gives_its_reader_an_empty_stream(self, monkeypatch, tmp_path, late, runs):
        """A run that ends without opening any FIFO, as a crate that fails before it writes does, leaves no reader waiting: each reads an empty stream, the run's answer comes back, and every FIFO is removed. That holds whether each reader is already waiting in its open when the run ends or, `late`, reaches its open only after `finish` has first tried the FIFO, which finds no reader and tries again, and across `runs` calls in a row, where `finish` often closes its writer while a reader is still inside its open. A reader left waiting would hang the call, so it runs on a thread that must finish in time."""
        if late:
            run = kernel_exec._PayloadFifo._run

            def late_run(fifo):
                time.sleep(0.2)
                run(fifo)

            monkeypatch.setattr(kernel_exec._PayloadFifo, "_run", late_run)
        paths = {f"payload-{index}": tmp_path / f"payload-{index}.tsv" for index in range(6)}
        for _ in range(runs):
            answer = {}
            caller = threading.Thread(
                target=lambda: answer.update(
                    zip(
                        ("ran", "read"),
                        kernel_exec.stream_payloads(
                            paths, lambda _name, source: source.read(), lambda: "ran"
                        ),
                    )
                ),
                daemon=True,
            )
            caller.start()
            caller.join(timeout=30)
            assert not caller.is_alive(), "a reader of a FIFO no crate opened is still waiting"
            assert answer == {"ran": "ran", "read": dict.fromkeys(paths, b"")}
            assert not list(tmp_path.iterdir())


class TestTheStructureStamp:
    """Tests for `run_m1.memo_structure_stamp`, the structure a `run_m1.TableBuildRecord` carries."""

    def test_a_class_whose_membership_moved_moves_no_structure(self):
        """When a rune joins a predicate class, the structure stamp holds still, because it leaves out class membership."""
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

    def test_the_structure_keeps_through_an_edit_the_crate_never_reads_and_moves_with_the_code_that_runs_it(
        self, tmp_path
    ):
        """An emitter, read-back, run_m1.py and the spec loader are none of the structure stamp's code lines, so an edit there moves the stamp only where it changes the dump or what run_m1.py's stamp code and `spec_load.capability_features` compute from it (`run_m1.memo_structure_stamp`), and these edits change neither. The same edits move the string replay's stamp, so the replay walks every text. An edit to the crate or to any module `run_m1.TABLE_BUILD_CODE_MODULES` names moves the structure stamp."""
        main = tmp_path / "rebuild" / "kernel-rs" / "src" / "main.rs"
        roster = [tmp_path / relative for relative in sorted(run_m1.TABLE_BUILD_CODE_MODULES)]
        unread = [
            tmp_path / "rebuild" / "pipeline" / f"{name}.py"
            for name in ("emit_gsub", "readback", "run_m1", "spec_load")
        ]
        for path in (*roster, *unread):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("VALUE = 1\n", encoding="utf-8")
        main.parent.mkdir(parents=True)
        main.write_text("fn main() {}\n", encoding="utf-8")
        base = run_m1.memo_structure_stamp(SPEC, tmp_path)
        replay = run_m1.replay_structure_stamp(SPEC, tmp_path)
        for path in unread:
            path.write_text("VALUE = 2\n", encoding="utf-8")
        assert run_m1.memo_structure_stamp(SPEC, tmp_path) == base
        assert run_m1.replay_structure_stamp(SPEC, tmp_path) != replay
        for path in (*roster, main):
            before = run_m1.memo_structure_stamp(SPEC, tmp_path)
            added = "fn other() {}\n" if path.suffix == ".rs" else "OTHER = 1\n"
            path.write_text(path.read_text(encoding="utf-8") + added, encoding="utf-8")
            assert run_m1.memo_structure_stamp(SPEC, tmp_path) != before, path.relative_to(tmp_path)

    def test_the_structure_moves_with_the_dumped_registry_the_rune_order_and_the_semantics(self, monkeypatch):
        """The registry and the order of the runes reach the crate through the dump, and the mode set through its command line, so each moves the structure stamp."""
        assert [entry.name for entry in fields(ResolvedSpec)] == ["runes", "registry"], (
            "the structure stamp hashes the spec dump as its runes and its registry, so a new top-level ResolvedSpec field "
            "reaches the crate unhashed until run_m1.memo_structure_stamp hashes it"
        )
        base = run_m1.memo_structure_stamp(SPEC)
        heights = SPEC.registry.heights
        height = next(iter(heights))
        raised = replace(
            SPEC, registry=replace(SPEC.registry, heights={**heights, height: heights[height] + 1})
        )
        reordered = replace(SPEC, runes=dict(reversed(list(SPEC.runes.items()))))
        for changed in (raised, reordered):
            assert run_m1.memo_structure_stamp(changed) != base
        monkeypatch.setattr(kernel_exec, "enumeration_tokens", lambda: ["other-mode-set"])
        assert run_m1.memo_structure_stamp(SPEC) != base


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
        "total, wanted",
        [
            (4_000_000_000, 1),
            (14_000_000_000, 2),
            (15_500_000_000, 3),
            (17_000_000_000, 4),
            (64_000_000_000, 6),
        ],
    )
    def test_the_width_follows_the_machine_and_never_falls_below_one(self, total, wanted):
        """With `DEFAULT_MEMO_BYTES` (1.4 GB) and six parked products at `PARKED_FOLD_BYTES` (0.2 GB) off the machine, a 14 GB machine fits two delta slots at `DELTA_SLOT_BYTES` (1.4 GB), a 15.5 GB machine three and a 17 GB machine four, the widest whose wave fits even where the rendezvous, six products read back at `READ_BACK_FOLD_BYTES` (1.5 GB), does not, since every width holds it. A machine too small for one delta gets one, and a 64 GB machine fits the whole wave, every configuration at once, which is as wide as the width goes."""
        assert kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=total) == wanted

    def test_a_coresident_pool_comes_off_the_machine_before_it_is_divided(self):
        """`coresident_bytes` is memory used by something running beside the fan-out, such as the artifact cycle's pytest pool. It comes off the machine with the reserve, so 3 GB beside a 21.8 GB machine leaves too little for the whole wave's 11.0 GB booking, and the wave drops the slot of its own that `default`'s fold preparation takes. It defaults to zero because a bare run_m1 runs alone."""
        assert kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=21_800_000_000) == 6
        assert (
            kernel_exec.kernel_threads_default(
                configs=CONFIG_COUNT, coresident_bytes=3_000_000_000, total_bytes=21_800_000_000
            )
            == 5
        )

    def test_the_booking_counts_every_parked_product_and_each_slot_beyond_its_own(self, monkeypatch):
        """Each configuration keeps its prepared fold product parked until every configuration has parked, so every width books one `PARKED_FOLD_BYTES` per configuration beside `DEFAULT_MEMO_BYTES`. Each delta slot books `DELTA_SLOT_BYTES` beyond its own product, up to the delta count, and only a width above the delta count books the slot that runs `default`'s fold preparation alone, at `FOLD_PREPARATION_BYTES`. Every configuration then reads its product back at the rendezvous whatever the width, so no width books less than `READ_BACK_FOLD_BYTES` per configuration: with that term set so the rendezvous lies midway between the narrowest wave and the widest, the narrow widths, a from-scratch build's among them, book the rendezvous and the wide ones their wave. Below the whole wave, the width is the division by `DELTA_SLOT_BYTES` after the memo and the parked products come off the machine."""
        fixed = kernel_exec.DEFAULT_MEMO_BYTES + CONFIG_COUNT * kernel_exec.PARKED_FOLD_BYTES
        deltas = CONFIG_COUNT - 1
        widths = range(1, CONFIG_COUNT + 2)

        def wave(width):
            return (
                fixed
                + min(width, deltas) * kernel_exec.DELTA_SLOT_BYTES
                + (kernel_exec.FOLD_PREPARATION_BYTES if width > deltas else 0)
            )

        def booked():
            return [kernel_exec.table_build_booking_bytes(width, configs=CONFIG_COUNT) for width in widths]

        rendezvous = CONFIG_COUNT * kernel_exec.READ_BACK_FOLD_BYTES
        assert booked() == [max(wave(width), rendezvous) for width in widths]
        every = kernel_exec.kernel_threads_default(configs=CONFIG_COUNT, total_bytes=18_000_000_000)
        budget = 18_000_000_000 - memory_budget.os_reserve_bytes(total_bytes=18_000_000_000) - fixed
        assert every == budget // kernel_exec.DELTA_SLOT_BYTES
        monkeypatch.setattr(
            kernel_exec, "READ_BACK_FOLD_BYTES", -(-(wave(1) + wave(CONFIG_COUNT)) // (2 * CONFIG_COUNT))
        )
        rendezvous = CONFIG_COUNT * kernel_exec.READ_BACK_FOLD_BYTES
        assert wave(1) < rendezvous < wave(CONFIG_COUNT)
        assert booked() == [max(wave(width), rendezvous) for width in widths]
        assert booked()[0] == rendezvous
        assert kernel_exec.table_build_booking_bytes(1, configs=CONFIG_COUNT, from_scratch=True) == rendezvous

    def test_deltas_are_started_from_scratch_beside_default_while_their_peaks_fit(self, monkeypatch):
        """`table_build_booking_bytes` books each delta started from scratch beside `default` at `SCRATCH_PEAK_BYTES` less its parked product in place of a delta's slot, and books such a build at no less than its start, where each of them holds a `SCRATCH_PEAK_BYTES` and `default` an `UNCAPPED_SCRATCH_PEAK_BYTES` while fewer of them start from scratch than there are deltas, since a delta then reads `default`'s whole memo, or one more `SCRATCH_PEAK_BYTES` once every delta does; the count is capped at the delta count and one less than the width, as the crate caps it. The start is checked with the uncapped term as it stands and raised far enough above the capped one to bind, so the two terms cannot stand in for each other. `deltas_from_scratch` is the largest count up to the delta count whose booking fits the memory less the reserve and what runs beside, so over a range of invented machines it never shrinks as the memory grows and covers every count whose booking is below the booking of every larger count, which with the uncapped term as it stands is every count from none to every delta. With the uncapped term raised, a build that starts every delta from scratch books less than one that starts some but not all of them, so the count goes from none straight to every delta, which a count that stopped at the first booking that misses would never reach. A from-scratch build starts nothing beside `default`."""
        fixed = kernel_exec.DEFAULT_MEMO_BYTES + CONFIG_COUNT * kernel_exec.PARKED_FOLD_BYTES
        deltas = CONFIG_COUNT - 1
        slot = kernel_exec.SCRATCH_PEAK_BYTES - kernel_exec.PARKED_FOLD_BYTES
        for raised in (0, 20_000_000_000):
            with monkeypatch.context() as patched:
                patched.setattr(
                    kernel_exec,
                    "UNCAPPED_SCRATCH_PEAK_BYTES",
                    kernel_exec.UNCAPPED_SCRATCH_PEAK_BYTES + raised,
                )
                for count in range(CONFIG_COUNT + 1):
                    taken = min(count, deltas)
                    wave = (
                        fixed
                        + taken * slot
                        + (deltas - taken) * kernel_exec.DELTA_SLOT_BYTES
                        + kernel_exec.FOLD_PREPARATION_BYTES
                    )
                    lead = (
                        kernel_exec.SCRATCH_PEAK_BYTES
                        if taken == deltas
                        else kernel_exec.UNCAPPED_SCRATCH_PEAK_BYTES
                    )
                    assert kernel_exec.table_build_booking_bytes(
                        CONFIG_COUNT, configs=CONFIG_COUNT, scratch_beside_default=count
                    ) == (max(wave, lead + taken * kernel_exec.SCRATCH_PEAK_BYTES) if taken else wave), (
                        raised,
                        count,
                    )
                seen = []
                for total in range(16_000_000_000, 32_000_000_000, 100_000_000):
                    usable = total - memory_budget.os_reserve_bytes(total_bytes=total)
                    count = kernel_exec.deltas_from_scratch(
                        CONFIG_COUNT, configs=CONFIG_COUNT, total_bytes=total
                    )
                    fits = [
                        taken
                        for taken in range(CONFIG_COUNT)
                        if kernel_exec.table_build_booking_bytes(
                            CONFIG_COUNT, configs=CONFIG_COUNT, scratch_beside_default=taken
                        )
                        <= usable
                    ]
                    assert count == max(fits, default=0), (raised, total)
                    assert not seen or count >= seen[-1], (raised, total)
                    seen.append(count)
                if raised:
                    assert set(seen) == {0, deltas}
                else:
                    booked = [
                        kernel_exec.table_build_booking_bytes(
                            CONFIG_COUNT, configs=CONFIG_COUNT, scratch_beside_default=taken
                        )
                        for taken in range(CONFIG_COUNT)
                    ]
                    assert {
                        taken
                        for taken in range(CONFIG_COUNT)
                        if all(booked[taken] < later for later in booked[taken + 1 :])
                    } <= set(seen)
        assert kernel_exec.table_build_booking_bytes(
            1, configs=CONFIG_COUNT, scratch_beside_default=deltas
        ) == kernel_exec.table_build_booking_bytes(1, configs=CONFIG_COUNT)
        roomy = 1_000_000_000_000
        usable = roomy - memory_budget.os_reserve_bytes(total_bytes=roomy)
        whole = kernel_exec.table_build_booking_bytes(CONFIG_COUNT, configs=CONFIG_COUNT)
        assert (
            kernel_exec.deltas_from_scratch(
                CONFIG_COUNT, configs=CONFIG_COUNT, coresident_bytes=usable - whole, total_bytes=roomy
            )
            == 0
        )
        assert kernel_exec.deltas_from_scratch(2, configs=2, total_bytes=roomy, from_scratch=True) == 0

    def test_a_build_without_default_s_memo_books_each_slot_from_scratch(self, monkeypatch, tmp_path):
        """A set without `default` enumerates every configuration from scratch, so its slots book `SCRATCH_PEAK_BYTES` less a parked product, not a delta's slot, and its width is that division, capped at the configuration count. On an invented 13 GB machine, whose 5.0 GB beside the reserve holds a two-configuration build that shares `default`'s memo at 4.6 GB, both run at once, while the from-scratch division fits one: the memo and two parked products leave 3.2 GB, and `SCRATCH_PEAK_BYTES` (2.1 GB) less a parked product (0.2 GB) is 1.9 GB a slot. `run_m1.build_tables` asks for that width exactly when `default` is not in the set it builds."""
        fixed = kernel_exec.DEFAULT_MEMO_BYTES + 2 * kernel_exec.PARKED_FOLD_BYTES
        slot = kernel_exec.SCRATCH_PEAK_BYTES - kernel_exec.PARKED_FOLD_BYTES
        assert kernel_exec.table_build_booking_bytes(2, configs=2, from_scratch=True) == fixed + 2 * slot
        total = 13_000_000_000
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

    @staticmethod
    def _in_threads(monkeypatch):
        """Run a full replay's absorb workers as threads of this process, so a stubbed walk and footprint reach them: the spawn pool becomes a thread pool that runs the same initializer, whose marks this undoes when the test ends."""
        from concurrent.futures import ThreadPoolExecutor

        monkeypatch.setattr(kernel_exec, "_BUILT", kernel_exec._BUILT)
        monkeypatch.setattr(run_m1, "_REPLAY_WORKER", None)
        monkeypatch.setattr(
            run_m1,
            "_spawn_pool",
            lambda jobs, units, *, max_tasks_per_child=None, initializer=None, initargs=(): ThreadPoolExecutor(
                max_workers=units, initializer=initializer, initargs=initargs
            ),
        )

    @staticmethod
    def _walked(asked: list, peak: int = 400_000_000, refused: str | None = None):
        """A stubbed `kernel_exec.replay_memo_walk` that records each call's spec dump and configuration and hands the worker's reader an empty window memo, or for `refused` a stream with no head, as a walk the crate never wrote a memo for gives."""
        from rebuild.tools.peak_rss import ReapedPeaks

        def walk(spec_path, out_dir, config, *, max_length, symbols, read, cache_stats=False):
            asked.append((spec_path, config))
            head = json.dumps(
                {"config": config, "max_length": max_length, "rows": 0, "labels": 0, "records": 0, "width": 2}
            )
            memo = b"" if config == refused else f"# {kernel_exec.REPLAY_MEMO_FORMAT}\t{head}\n".encode()
            return kernel_exec.ReplayWalk(
                {"texts": 1, "windows": 1, "skipped": 0},
                (f"[t] replay[{config}] 0.1s", "[t] replay_total 0.1s"),
                ReapedPeaks(peak, peak - 1, 1.0),
                read(io.BytesIO(memo)),
            )

        return walk

    def test_each_walk_and_each_absorb_that_answers_records_its_own_peak(self, monkeypatch, tmp_path, capsys):
        """A full replay records one `replay-walk` observation a configuration, that walk's own crate peak, and one `<config> absorb` observation of the `replay-absorb` record a worker that read its memo whole, its peak footprint, the row `make job-costs` reads it under; an absorb whose memo the crate never wrote warns, records nothing and writes no file, and the other configurations' files move into place. Each walk's `[t]` lines come out tagged with its configuration where they carry no bracket. The workers run as threads, so the stubbed walk and footprint reach them."""
        from rebuild.tools import calibrate_budgets, cycle_timings

        self._in_threads(monkeypatch)
        asked: list = []
        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(kernel_exec, "replay_memo_walk", self._walked(asked, refused="ss08"))
        monkeypatch.setattr(run_m1, "peak_footprint_self_bytes", lambda: 315_000_000)
        digests = {name: f"d-{name}" for name in SPEC.runes}
        inputs = oracle_cache.SettleMemoInputs(rune_digests=dict(digests), oracle_code="code", data="data")
        assert run_m1.run_replay_strings(SPEC, tmp_path, None, memo_inputs=inputs)["pass"]
        records = cycle_timings.load_pool_records(cycle_timings.JOURNAL)
        assert [
            record["worker_peak_rss_bytes"] for record in records if record["unit"] == run_m1.REPLAY_POOL_UNIT
        ] == [dict.fromkeys(conform.SETTLEMENT_CONFIGS, 400_000_000)]
        absorbs = [record for record in records if record["unit"] == run_m1.REPLAY_ABSORB_POOL_UNIT]
        assert [record["worker_peak_rss_bytes"] for record in absorbs] == [
            {f"{config} absorb": 315_000_000 for config in conform.SETTLEMENT_CONFIGS if config != "ss08"}
        ]
        assert [record["worker_reading"] for record in absorbs] == ["footprint"]
        assert any(run_m1.REPLAY_ABSORB_POOL_UNIT in unit.pool_units for unit in calibrate_budgets.UNITS)
        memos = conform.settle_memo_files(tmp_path, SPEC, inputs)
        assert [config for config, memo in memos.items() if conform.settle_memo_standing(memo)] == [
            config for config in conform.SETTLEMENT_CONFIGS if config != "ss08"
        ]
        captured = capsys.readouterr()
        assert "not absorbed" in captured.out and "replay-windows-ss08.bin" in captured.out
        assert "[t] replay[ss03] 0.1s" in captured.err and "[t] replay_total[ss03] 0.1s" in captured.err
        assert not list(tmp_path.glob("replay-windows-*")) and not list(tmp_path.glob("*.tmp"))

    def test_the_workers_take_the_parents_build_and_spec_dump(self, monkeypatch, tmp_path):
        """The parent builds the kernel and dumps the spec once, and its workers' initializer marks the kernel built only from the parent's word: every walk gets the parent's spec dump, no worker dumps one of its own, and an initializer told the parent never built leaves `_BUILT` as it was, so a worker cannot skip cargo after a crate edit."""
        self._in_threads(monkeypatch)
        asked: list = []
        built: list = []
        dumps: list = []
        dump = tmp_path / "parent-spec.json"
        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: built.append("built"))
        monkeypatch.setattr(kernel_exec, "_spec_dump", lambda spec: (dumps.append(spec), dump)[1])
        monkeypatch.setattr(kernel_exec, "replay_memo_walk", self._walked(asked))
        memos = conform.settle_memo_files(
            tmp_path, SPEC, oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
        )
        replayed = run_m1.replay_and_absorb(SPEC, tmp_path, memos, 2)
        assert replayed.passed and built == ["built"] and dumps == [SPEC]
        assert sorted(asked) == sorted((dump, config) for config in conform.SETTLEMENT_CONFIGS)
        assert kernel_exec._BUILT
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        run_m1._replay_worker_init(False, str(dump), None, None, None, None, None, 0, False)
        assert not kernel_exec._BUILT
        run_m1._replay_worker_init(True, str(dump), None, None, None, None, None, 0, False)
        assert kernel_exec._BUILT

    def test_a_narrow_replay_stages_a_file_at_once_while_a_walk_waits_and_a_red_one_removes_it(
        self, monkeypatch, tmp_path
    ):
        """Below the configuration count a worker whose file is finished while a walk still waits for a slot stages it at once and waits for the verdict holding no file, so the walks running and the files held never outnumber the width, and a red replay removes every file staged that way; at the configuration count no worker stages before the verdict, so a red replay stages nothing. The workers run as threads with a disagreement in `ss08`'s walk; at width 1 every worker but the last to take the slot stages early, `ss08`'s own excepted."""
        self._in_threads(monkeypatch)
        monkeypatch.setattr(kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)
        walked = self._walked([])

        def walk(spec_path, out_dir, config, **rest):
            if config == "ss08":
                raise kernel_exec.ReplayDisagreement("ss08: a window settles two ways")
            return walked(spec_path, out_dir, config, **rest)

        staged: list = []
        stage = conform.FinishedSettleMemo.stage
        monkeypatch.setattr(
            conform.FinishedSettleMemo,
            "stage",
            lambda self, path: (staged.append(path), stage(self, path))[1],
        )
        monkeypatch.setattr(kernel_exec, "replay_memo_walk", walk)
        memos = conform.settle_memo_files(
            tmp_path, SPEC, oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
        )
        configs = len(conform.SETTLEMENT_CONFIGS)
        for width, early in ((1, {configs - 2, configs - 1}), (configs, {0})):
            staged.clear()
            replayed = run_m1.replay_and_absorb(SPEC, tmp_path, memos, width)
            assert not replayed.passed and replayed.failure().config == "ss08"
            assert len(staged) in early, width
            assert not list(tmp_path.glob("settle-memo-*")) and not list(tmp_path.glob("*.tmp"))

    def test_a_worker_whose_parent_has_gone_stops_wherever_it_waits(self, tmp_path):
        """A stop that ends a full replay's parent alone, as the artifact cycle's driver's SIGTERM to `uv run` ends run_m1, leaves no absorb worker behind, wherever the worker waits: for a slot, for the verdict, idle in its pool after its task, or inside a walk, whose crate it kills and reaps and whose FIFO it removes before it ends. A real spawn pool under the replay's initializer stands in for the replay, a parent that never sets the verdict and never frees a slot, and a `sleep` for the crate; the parent ends at SIGTERM's default action, with no cleanup of its own."""
        script = tmp_path / "orphaned_workers.py"
        script.write_text(_ORPHANED_WORKERS)
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        log = tmp_path / "orphaned_workers.log"
        pids: dict[str, int] = {}
        with log.open("w") as errors:
            parent = subprocess.Popen(
                [sys.executable, str(script), str(scratch)],
                cwd=kernel_exec.REPO_ROOT,
                env={**os.environ, "PYTHONPATH": str(kernel_exec.REPO_ROOT)},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
            )
            try:
                assert parent.stdout is not None
                for _ in range(5):
                    where, pid = parent.stdout.readline().split()
                    pids[where] = int(pid)
                time.sleep(0.5)
                parent.terminate()
                parent.wait(timeout=60)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait()

        def alive(pid: int) -> bool:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        assert sorted(pids) == ["crate", "idle", "slot", "verdict", "walk"], log.read_text()
        deadline = time.monotonic() + 30
        while (left := [where for where, pid in pids.items() if alive(pid)]) and time.monotonic() < deadline:
            time.sleep(0.05)
        for where in left:
            os.kill(pids[where], signal.SIGKILL)
        assert not left, log.read_text()
        assert not list(scratch.iterdir())

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

    @staticmethod
    def _absorbing(asked: list):
        """A stubbed `run_m1.replay_and_absorb` that records the configurations it was asked to walk and absorb and passes them all."""

        def replay_and_absorb(spec, out_dir, memos, threads, **rest):
            asked.append(("absorbed", tuple(memos)))
            walks = {
                config: run_m1.ReplayWalkReport(
                    config, {"texts": 1, "windows": 1, "skipped": 0}, None, False, (), None, 0
                )
                for config in memos
            }
            return run_m1.FullReplay(walks, {}, True)

        return replay_and_absorb

    def test_a_memo_file_absent_or_restamped_widens_the_walk_and_asks_for_a_dump(self, monkeypatch, tmp_path):
        """When a configuration's settle memo file is absent or has another stamp while the replay's structure stamp still matches, a build that would otherwise walk nothing walks every text, each configuration beside the absorb that refills its file (`run_m1.replay_and_absorb`). A rune edit walks that rune's families in one crate process and asks for no memo, and a pass where every memo file is current and nothing moved walks nothing."""
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
        monkeypatch.setattr(run_m1, "replay_and_absorb", self._absorbing(asked))
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        digests = {name: f"d-{name}" for name in SPEC.runes}
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: dict(digests))
        inputs = oracle_cache.SettleMemoInputs(rune_digests=dict(digests), oracle_code="code", data="data")
        memos = conform.settle_memo_files(tmp_path, SPEC, inputs)

        first = run_m1.run_replay_strings(SPEC, tmp_path, "stamp", memo_inputs=inputs)
        assert first["pass"] and first["families"] is None
        assert asked == [("absorbed", tuple(conform.SETTLEMENT_CONFIGS))]
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
        assert widened["families"] is None and asked[-1] == ("absorbed", tuple(conform.SETTLEMENT_CONFIGS))
        assert not list(tmp_path.glob("replay-windows-*.bin"))

    def test_a_narrowed_replay_walks_and_keys_only_the_configurations_it_names(self, monkeypatch, tmp_path):
        """With `configs=["default"]`, the stage walks and absorbs `default` alone and clears only `default`'s window memo path. With no `configs` it walks and absorbs every settlement configuration and clears every path. `conform.settle_memo_files` returns every configuration either way, so the filtering happens in this stage."""
        asked: list = []
        dumps: list = []

        def replay_memo_dump(out_dir, config):
            dumps.append(config)
            return tmp_path / f"replay-windows-{config}.bin"

        monkeypatch.setattr(run_m1, "replay_and_absorb", self._absorbing(asked))
        monkeypatch.setattr(kernel_exec, "replay_memo_dump", replay_memo_dump)
        digests = {name: f"d-{name}" for name in SPEC.runes}
        inputs = oracle_cache.SettleMemoInputs(rune_digests=dict(digests), oracle_code="code", data="data")
        assert set(conform.settle_memo_files(tmp_path, SPEC, inputs)) == set(conform.SETTLEMENT_CONFIGS)

        narrowed = run_m1.run_replay_strings(SPEC, tmp_path, None, memo_inputs=inputs, configs=["default"])
        assert asked == [("absorbed", ("default",))]
        assert list(narrowed["configs"]) == ["default"]
        assert set(dumps) == {"default"}

        dumps.clear()
        whole = run_m1.run_replay_strings(SPEC, tmp_path, None, memo_inputs=inputs)
        assert asked[-1] == ("absorbed", tuple(conform.SETTLEMENT_CONFIGS))
        assert list(whole["configs"]) == list(conform.SETTLEMENT_CONFIGS)
        assert set(dumps) == set(conform.SETTLEMENT_CONFIGS)

    @pytest.fixture(scope="class")
    def tables_dir(self, tmp_path_factory):
        out_dir = tmp_path_factory.mktemp("replay-stage-tables")
        run_m1.build_tables(SPEC, out_dir)
        return out_dir

    @staticmethod
    def _tables(tables_dir, out_dir):
        """A copy of every configuration's settlement table under `out_dir`, which the replay reads and beside which it writes the settle memo files."""
        out_dir.mkdir(parents=True)
        for config in conform.SETTLEMENT_CONFIGS:
            name = f"settlement-{config}.tsv"
            (out_dir / name).write_text((tables_dir / name).read_text())
        return out_dir

    def test_a_full_replay_walks_each_configuration_in_a_crate_of_its_own_beside_its_absorb(
        self, tables_dir, tmp_path, capsys
    ):
        """A full replay with memo inputs runs one `replay-strings` process a configuration, each writing its own `spec_parse[<config>]` line, and one absorb worker beside each, at widths 1 and 2 alike: it answers the counts the one-process walk answers, writes every configuration's settle memo file, leaves no FIFO and no staged file, and records each walk's own peak and each absorb's footprint, one observation a configuration."""
        from rebuild.tools import cycle_timings

        inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
        for width in (1, 2):
            out_dir = self._tables(tables_dir, tmp_path / f"w{width}")
            summary = run_m1.run_replay_strings(SPEC, out_dir, None, replay_threads=width, memo_inputs=inputs)
            assert summary["pass"] and list(summary["configs"]) == list(conform.SETTLEMENT_CONFIGS)
            assert summary["configs"] == kernel_exec.replay_strings(
                SPEC,
                out_dir,
                conform.SETTLEMENT_CONFIGS,
                max_length=run_m1.REPLAY_MAX_LENGTH,
                families=None,
                threads=2,
            )
            err = capsys.readouterr().err
            assert [
                config for config in conform.SETTLEMENT_CONFIGS if f"[t] spec_parse[{config}] " in err
            ] == list(conform.SETTLEMENT_CONFIGS)
            memos = conform.settle_memo_files(out_dir, SPEC, inputs)
            assert all(conform.settle_memo_standing(memo) for memo in memos.values())
            assert not list(out_dir.glob("replay-windows-*")) and not list(out_dir.glob("*.tmp"))
        records = cycle_timings.load_pool_records(cycle_timings.JOURNAL)
        walks = [record for record in records if record["unit"] == run_m1.REPLAY_POOL_UNIT]
        assert [(record["width"], sorted(record["worker_peak_rss_bytes"])) for record in walks] == [
            (width, sorted(conform.SETTLEMENT_CONFIGS)) for width in (1, 2)
        ]
        absorbs = [record for record in records if record["unit"] == run_m1.REPLAY_ABSORB_POOL_UNIT]
        assert [sorted(record["worker_peak_rss_bytes"]) for record in absorbs] == [
            sorted(f"{config} absorb" for config in conform.SETTLEMENT_CONFIGS)
        ] * 2

    def test_a_red_walk_in_any_configuration_writes_no_settle_memo_byte(
        self, tables_dir, tmp_path, monkeypatch
    ):
        """A disagreement in one configuration's walk, not the first's, makes a replay at the configuration count red with that walk's message and no counts, every absorb discards its file, so no settle memo file appears and a standing one keeps its bytes and its inode, and no FIFO or staged file is left."""
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)
        out_dir = self._tables(tables_dir, tmp_path / "red")
        table = out_dir / "settlement-ss04.tsv"
        lines = table.read_text().splitlines()
        fields = lines[2].split("\t")
        fields[6] = f"{fields[0]}.perturbed"
        lines[2] = "\t".join(fields)
        table.write_text("\n".join(lines) + "\n")
        inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
        standing = conform.settle_memo_files(out_dir, SPEC, inputs)["default"]
        assert conform._write_settle_memo(standing, *conform._memo_columns([]))
        before = (standing.path.read_bytes(), standing.path.stat().st_ino)
        summary = run_m1.run_replay_strings(
            SPEC, out_dir, None, replay_threads=len(conform.SETTLEMENT_CONFIGS), memo_inputs=inputs
        )
        assert not summary["pass"] and summary["configs"] == {}
        assert "ss04" in summary["error"] and ".perturbed" in summary["error"]
        assert (standing.path.read_bytes(), standing.path.stat().st_ino) == before
        assert [path.name for path in out_dir.glob("settle-memo-*")] == [standing.path.name]
        assert not list(out_dir.glob("replay-windows-*")) and not list(out_dir.glob("*.tmp"))

    def test_a_settle_memo_file_that_cannot_be_moved_into_place_is_a_warning_and_the_others_move(
        self, tables_dir, tmp_path, capsys
    ):
        """A configuration whose settle memo file cannot be replaced, here because a directory stands at its path, is a warning on a passing replay; every other configuration's file moves into place, and the staged file is removed."""
        out_dir = self._tables(tables_dir, tmp_path / "blocked")
        inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="code", data="data")
        memos = conform.settle_memo_files(out_dir, SPEC, inputs)
        memos["ss03"].path.mkdir()
        summary = run_m1.run_replay_strings(SPEC, out_dir, None, replay_threads=2, memo_inputs=inputs)
        assert summary["pass"]
        assert f"{memos['ss03'].path} not written" in capsys.readouterr().out
        assert [config for config, memo in memos.items() if conform.settle_memo_standing(memo)] == [
            config for config in conform.SETTLEMENT_CONFIGS if config != "ss03"
        ]
        assert not list(out_dir.glob("replay-windows-*")) and not list(out_dir.glob("*.tmp"))

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
