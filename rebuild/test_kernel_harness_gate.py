"""The harness gate's own plumbing: the arm table the artifact cycle's verdict function walks, the stop-at-the-first-failure rule that keeps a divergence from costing another fifty minutes, and the summary every path has to leave behind.

None of it runs a harness, and none of it needs a Rust toolchain. The five arms this gate exists to spawn are the port's landing evidence and take the better part of an hour between them; what is testable here is everything around that — which process gets started with which flags in which world, what stops, and what lands in the summary — so the one place a process would be started is stood in for and each arm's exit code is dictated. The harnesses themselves are proved by their own suites and by the gate turning red when they exit nonzero, which is exactly the wire this file checks.

Every `main` invocation passes `--out` at scratch. The live `rebuild/out/m1` holds the summary a real cycle left, and this tool writes into whatever directory it is pointed at.
"""

import json
import os
import sys

import pytest

from rebuild.pipeline import kernel_exec
from rebuild.tools import kernel_harness_gate

# The arm table restated in the shape the make targets spell it, so a change to either side has to be made in both places on purpose. `kernel-liveness` passes --exhaustive, the three fixpoint targets pass --live-only and differ only in the AMS_* environment they run under, and `kernel-differential` is invoked bare.
EXPECTED = {
    "liveness-exhaustive": ("rebuild.tools.kernel_liveness", ["--exhaustive"], {}),
    "fixpoint-pinned": (
        "rebuild.tools.kernel_fixpoint",
        ["--live-only"],
        {"AMS_SIMULATED_PROSPECT": "0", "AMS_VOTE_SLOTS": "0"},
    ),
    "differential": ("rebuild.tools.kernel_differential", [], {}),
    "fixpoint-shipping": ("rebuild.tools.kernel_fixpoint", ["--live-only"], {}),
    "fixpoint-label-grain": ("rebuild.tools.kernel_fixpoint", ["--live-only"], {"AMS_DEEP_CLASSES": "0"}),
}


class Spawns:
    """A stand-in for the one place this gate starts a process: every invocation's argv and environment recorded, nothing run, and `fail_at` making the nth arm exit 1 the way a diverging harness does. The canned output is longer than the tail the summary keeps, so a test can tell a truncated tail from a whole one."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail_at: int | None = None
        self.lines = [f"harness line {n}" for n in range(50)]

    def __call__(self, argv, environment):
        self.calls.append((argv, environment))
        failed = self.fail_at == len(self.calls) - 1
        return (1 if failed else 0), "\n".join(self.lines)

    @property
    def modules(self) -> list[str]:
        return [argv[2] for argv, _environment in self.calls]


@pytest.fixture
def spawns(monkeypatch):
    """A gate whose crate builds and whose arms answer without a subprocess between them."""
    record = Spawns()
    monkeypatch.setattr(kernel_harness_gate, "_spawn", record)
    monkeypatch.setattr(kernel_exec, "cargo_build", lambda: None)
    return record


def _run(out, *arguments):
    return kernel_harness_gate.main([*arguments, "--out", str(out)])


def _summary(out):
    return json.loads((out / kernel_harness_gate.SUMMARY_NAME).read_text())


class TestTheArmTable:
    def test_the_table_and_the_contract_the_cycle_imports_agree(self):
        """`ARM_NAMES` is what `evaluate_kernel_harness_gate` walks and the table is what gets spawned; a name in one and not the other is an arm that either never runs or never counts."""
        assert tuple(kernel_harness_gate.ARMS) == kernel_harness_gate.ARM_NAMES
        assert tuple(EXPECTED) == kernel_harness_gate.ARM_NAMES

    @pytest.mark.parametrize("name", list(EXPECTED))
    def test_each_arm_is_invoked_the_way_its_make_target_invokes_it(self, tmp_path, spawns, name):
        assert _run(tmp_path, "--skip-build") == 0
        module, arguments, world = EXPECTED[name]
        argv, environment = spawns.calls[kernel_harness_gate.ARM_NAMES.index(name)]
        assert argv == [sys.executable, "-m", module, *arguments]
        assert environment == dict(os.environ) | world

    def test_the_arms_run_cheapest_first_in_the_one_order_the_contract_names(self, tmp_path, spawns):
        assert _run(tmp_path, "--skip-build") == 0
        assert spawns.modules == [
            kernel_harness_gate.ARMS[name].module for name in kernel_harness_gate.ARM_NAMES
        ]


class TestAGreenRun:
    def test_every_arm_ran_and_the_summary_says_so(self, tmp_path, spawns):
        assert _run(tmp_path, "--skip-build") == 0
        summary = _summary(tmp_path)
        assert summary["format"] == kernel_harness_gate.SUMMARY_FORMAT
        assert summary["error"] is None
        assert list(summary["arms"]) == list(kernel_harness_gate.ARM_NAMES)
        for arm in summary["arms"].values():
            assert arm["exit"] == 0
            assert isinstance(arm["elapsed_s"], float)
            assert arm["tail"] == spawns.lines[-kernel_harness_gate.TAIL_LINES :]

    def test_the_summary_names_the_binary_and_the_alphabet_the_arms_swept(self, tmp_path, spawns):
        assert _run(tmp_path, "--skip-build") == 0
        summary = _summary(tmp_path)
        assert summary["binary"].endswith("ams-m1-kernel")
        assert len(summary["structure"]) == 64

    def test_every_arm_leaves_a_timing_line_the_cycle_journal_can_read(self, tmp_path, spawns, capsys):
        assert _run(tmp_path, "--skip-build") == 0
        printed = capsys.readouterr().out
        for name in kernel_harness_gate.ARM_NAMES:
            assert f"[t] {name} " in printed
            assert f"  {name:>20}  OK" in printed
        assert "5 arms agree" in printed


class TestTheRunStopsAtTheFirstFailingArm:
    def test_the_arms_behind_a_failure_are_never_spawned(self, tmp_path, spawns):
        spawns.fail_at = 1
        assert _run(tmp_path, "--skip-build") == 1
        assert list(_summary(tmp_path)["arms"]) == ["liveness-exhaustive", "fixpoint-pinned"]
        assert len(spawns.calls) == 2

    def test_the_failing_arms_exit_and_tail_are_what_the_driver_reads(self, tmp_path, spawns, capsys):
        spawns.fail_at = 0
        assert _run(tmp_path, "--skip-build") == 1
        arm = _summary(tmp_path)["arms"]["liveness-exhaustive"]
        assert arm["exit"] == 1
        assert arm["tail"] == spawns.lines[-kernel_harness_gate.TAIL_LINES :]
        assert "liveness-exhaustive exited 1" in capsys.readouterr().out

    def test_a_failure_on_the_last_arm_is_still_a_red_gate(self, tmp_path, spawns):
        spawns.fail_at = len(kernel_harness_gate.ARM_NAMES) - 1
        assert _run(tmp_path, "--skip-build") == 1
        assert list(_summary(tmp_path)["arms"]) == list(kernel_harness_gate.ARM_NAMES)


class TestACrateThatDoesNotBuild:
    def test_a_box_without_cargo_writes_an_error_summary_and_spawns_nothing(
        self, tmp_path, spawns, monkeypatch, capsys
    ):
        def absent():
            raise kernel_exec.KernelBuildError("no cargo on PATH — install the Rust toolchain")

        monkeypatch.setattr(kernel_exec, "cargo_build", absent)
        assert _run(tmp_path) == 1
        summary = _summary(tmp_path)
        assert "Rust toolchain" in summary["error"]
        assert summary["arms"] == {}
        assert spawns.calls == []
        assert "Rust toolchain" in capsys.readouterr().err

    def test_skip_build_runs_the_arms_against_the_binary_already_on_disk(self, tmp_path, spawns, monkeypatch):
        def never():
            raise AssertionError("--skip-build built the crate anyway")

        monkeypatch.setattr(kernel_exec, "cargo_build", never)
        assert _run(tmp_path, "--skip-build") == 0
        assert len(spawns.calls) == len(kernel_harness_gate.ARM_NAMES)


class TestTheToolWritesOnlyWhereItIsPointed:
    def test_the_summary_is_the_only_file_a_run_leaves_and_the_directory_is_made(self, tmp_path, spawns):
        out = tmp_path / "nested" / "m1"
        assert _run(out, "--skip-build") == 0
        assert [path.name for path in sorted(out.iterdir())] == [kernel_harness_gate.SUMMARY_NAME]

    def test_the_cycles_own_output_directory_is_untouched(self, tmp_path, spawns):
        """The default `--out` is the live `rebuild/out/m1`, which holds a real cycle's record; a suite that wrote there would overwrite the artifact `make verdict-ready` reads."""
        live = kernel_harness_gate.M1_OUT / kernel_harness_gate.SUMMARY_NAME
        before = live.read_bytes() if live.is_file() else None
        assert _run(tmp_path, "--skip-build") == 0
        after = live.read_bytes() if live.is_file() else None
        assert after == before
