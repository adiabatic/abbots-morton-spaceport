import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from rebuild.review import journal
from rebuild.tools import artifact_cycle as ac
from rebuild.tools.cycle_timings import CycleTimings
from rebuild.tools.kernel_harness_gate import ARM_NAMES


def _pass_summaries():
    return {
        "pipeline": {"defect_errors": []},
        "boundary": {"pass": True, "divergences": 0},
        "manual_pins": {"pass": True, "disagreements": []},
        "oracle": {"unmatched": 8423, "multi_matched": 0},
    }


def test_gate_passes_on_clean_summaries():
    s = _pass_summaries()
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["boundary"], s["manual_pins"], s["oracle"])
    assert outcome.ok
    assert outcome.failures == []
    assert outcome.unmatched == 8423
    assert outcome.multi_matched == 0


def test_gate_fails_on_defect_errors():
    s = _pass_summaries()
    s["pipeline"]["defect_errors"] = ["E-ANCHOR convention:foo: bad"]
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["boundary"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert any("defect" in reason for reason in outcome.failures)


def test_gate_fails_on_boundary():
    s = _pass_summaries()
    s["boundary"] = {"pass": False, "divergences": 3}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["boundary"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert any("boundary" in reason for reason in outcome.failures)


def test_gate_fails_on_manual_pins():
    s = _pass_summaries()
    s["manual_pins"] = {"pass": False, "disagreements": ["one", "two"]}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["boundary"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert any("Manual-pin" in reason for reason in outcome.failures)


def test_gate_fails_on_multi_matched():
    s = _pass_summaries()
    s["oracle"] = {"unmatched": 8423, "multi_matched": 2}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["boundary"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert outcome.multi_matched == 2
    assert any("multi_matched" in reason for reason in outcome.failures)


def test_gate_unmatched_alone_is_not_a_failure():
    s = _pass_summaries()
    s["oracle"] = {"unmatched": 999999, "multi_matched": 0}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["boundary"], s["manual_pins"], s["oracle"])
    assert outcome.ok


def test_conform_gate_passes_on_clean_summary():
    status, failures = ac.evaluate_conform_gate(
        {"divergences": 0, "uncovered_rules": 0, "uncovered_transitions": 0, "pass": True}
    )
    assert status == "green"
    assert failures == []


def test_conform_gate_fails_on_divergences():
    status, failures = ac.evaluate_conform_gate(
        {"divergences": 3, "uncovered_rules": 0, "uncovered_transitions": 0, "pass": False}
    )
    assert status == "FAILED"
    assert failures == ["conform gate: 3 font-vs-settle divergence(s)"]


def test_conform_gate_fails_on_dead_rules_and_transitions():
    status, failures = ac.evaluate_conform_gate(
        {"divergences": 0, "uncovered_rules": 2, "uncovered_transitions": 5, "pass": False}
    )
    assert status == "FAILED"
    assert failures == [
        "conform gate: 2 dead settlement rule(s)",
        "conform gate: 5 dead decision-table transition(s)",
    ]


def test_conform_gate_fails_on_missing_summary():
    status, failures = ac.evaluate_conform_gate(None)
    assert status == "FAILED (no conform_summary.json)"
    assert failures == ["conform gate: run_m1 --conform-only wrote no summary"]


def test_conform_gate_fails_on_bare_false_pass():
    status, failures = ac.evaluate_conform_gate({"pass": False})
    assert status == "FAILED"
    assert failures == ["conform gate: pass is false"]


def _identical(*configs):
    arms = {"windows": "identical", "settlement": "identical", "treaties": "identical", "digest": "identical"}
    return {name: dict(arms) for name in configs}


def _kernel_summary(**overrides):
    summary = {"divergences": 0, "stale": [], "error": None, "configs": _identical("default", "ss03")}
    summary.update(overrides)
    return summary


def test_kernel_differential_gate_passes_when_every_arm_is_identical():
    status, failures = ac.evaluate_kernel_differential_gate(_kernel_summary())
    assert status == "green"
    assert failures == []


def test_kernel_differential_gate_names_the_config_and_the_artifacts_that_moved():
    configs = _identical("default", "ss03")
    configs["ss03"]["windows"] = "differs"
    configs["ss03"]["digest"] = "differs"
    status, failures = ac.evaluate_kernel_differential_gate(_kernel_summary(divergences=2, configs=configs))
    assert status == "FAILED"
    assert failures[0] == "kernel-differential gate: 2 Rust-vs-Python divergence(s)"
    assert failures[1] == "kernel-differential gate: ss03 differs on digest, windows"


def test_kernel_differential_gate_reads_stale_artifacts_as_red_pointing_at_the_cycle():
    """A stale rebuild/out/m1 is not a divergence: nothing was compared, so the gate is red with the artifact cycle as the remedy rather than a diff to chase."""
    stale = ["windows-ss03.tsv.gz: stamped for other inputs"]
    status, failures = ac.evaluate_kernel_differential_gate(_kernel_summary(stale=stale))
    assert status == "FAILED"
    assert "1 stale artifact(s)" in failures[0]
    assert "windows-ss03.tsv.gz" in failures[0]
    assert "run the artifact cycle" in failures[0]


def test_kernel_differential_gate_carries_a_run_error_through():
    status, failures = ac.evaluate_kernel_differential_gate(
        _kernel_summary(error="cargo not found: install the Rust toolchain", configs={})
    )
    assert status == "FAILED"
    assert failures[0] == "kernel-differential gate: cargo not found: install the Rust toolchain"


def test_kernel_differential_gate_fails_on_a_summary_that_compared_nothing():
    assert ac.evaluate_kernel_differential_gate(_kernel_summary(configs={})) == (
        "FAILED",
        ["kernel-differential gate: the summary compared no configs"],
    )
    status, failures = ac.evaluate_kernel_differential_gate(None)
    assert status == "FAILED (no kernel_differential_summary.json)"
    assert failures == ["kernel-differential gate: rebuild.tools.kernel_gate wrote no summary"]


def test_kernel_differential_argv_names_the_tool_and_the_thread_width():
    assert ac.kernel_differential_argv(6) == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.kernel_gate",
        "--threads",
        "6",
    ]
    assert ac.kernel_differential_argv()[-1] == str(ac.KERNEL_THREADS_DEFAULT)


def _ran(*names):
    return {
        name: {"exit": 0, "elapsed_s": 61.4, "tail": [f"{name}: the two engines agree"]} for name in names
    }


def _harness_summary(**overrides):
    summary = {
        "format": "ams-kernel-harness-summary/1",
        "binary": "rebuild/kernel-rs/target/release/ams-m1-kernel",
        "structure": "0" * 64,
        "arms": _ran(*ARM_NAMES),
        "error": None,
    }
    summary.update(overrides)
    return summary


def test_kernel_harness_gate_passes_when_every_arm_exits_zero():
    status, failures = ac.evaluate_kernel_harness_gate(_harness_summary())
    assert status == "green"
    assert failures == []


def test_kernel_harness_gate_names_the_failing_arm_its_exit_and_its_verdict_line():
    """The summary's tail is bounded because a diverging fixpoint's output is measured in megabytes, so the gate quotes the last line with anything on it — the harness's own verdict — and a reader never has to go back to the console for it."""
    arms = _ran("liveness-exhaustive", "fixpoint-pinned")
    arms["differential"] = {
        "exit": 1,
        "elapsed_s": 402.4,
        "tail": [
            "  guard fuzz: 4096 cases",
            "kernel differential: 3 divergence(s) over 12 configs",
            "  ",
            "",
        ],
    }
    status, failures = ac.evaluate_kernel_harness_gate(_harness_summary(arms=arms))
    assert status == "FAILED"
    assert (
        failures[0]
        == "kernel-harness gate: differential exited 1: kernel differential: 3 divergence(s) over 12 configs"
    )
    arms["differential"]["tail"] = []
    _, silent = ac.evaluate_kernel_harness_gate(_harness_summary(arms=arms))
    assert silent[0] == "kernel-harness gate: differential exited 1: no output"


def test_kernel_harness_gate_names_the_arms_the_run_never_reached():
    """The tool stops at the first failing arm and records the rest as absent rather than as skipped, so the driver has to read absence as unproven: five arms present and exiting zero is the only green there is."""
    status, failures = ac.evaluate_kernel_harness_gate(_harness_summary(arms=_ran(*ARM_NAMES[:2])))
    assert status == "FAILED"
    assert failures == [
        f"kernel-harness gate: {name} never ran (an earlier arm stopped the run)" for name in ARM_NAMES[2:]
    ]


def test_kernel_harness_gate_carries_a_run_error_through():
    status, failures = ac.evaluate_kernel_harness_gate(
        _harness_summary(error="no cargo on PATH — install the Rust toolchain", arms={})
    )
    assert status == "FAILED"
    assert failures[0] == "kernel-harness gate: no cargo on PATH — install the Rust toolchain"


def test_kernel_harness_gate_fails_on_a_summary_that_ran_nothing():
    assert ac.evaluate_kernel_harness_gate(_harness_summary(arms={})) == (
        "FAILED",
        ["kernel-harness gate: the summary ran no arms"],
    )
    status, failures = ac.evaluate_kernel_harness_gate({"error": None})
    assert status == "FAILED"
    assert failures == ["kernel-harness gate: the summary ran no arms"]
    status, failures = ac.evaluate_kernel_harness_gate(None)
    assert status == "FAILED (no kernel_harness_summary.json)"
    assert failures == ["kernel-harness gate: rebuild.tools.kernel_harness_gate wrote no summary"]


def test_kernel_harness_argv_names_the_tool():
    assert ac.kernel_harness_argv() == ["uv", "run", "python", "-m", "rebuild.tools.kernel_harness_gate"]


def test_classify_baseline():
    for test_id in ac.BASELINE_REBUILD_FAILURES:
        assert ac.classify_rebuild_failure(test_id, update_pins=False) == "baseline"
        assert ac.classify_rebuild_failure(test_id, update_pins=True) == "baseline"


def test_classify_census_hint_only_without_update_pins():
    test_id = "rebuild/test_review_build.py::test_totals_pinned"
    assert ac.classify_rebuild_failure(test_id, update_pins=False) == "census-hint"
    assert ac.classify_rebuild_failure(test_id, update_pins=True) == "hard"


def test_classify_hard_for_unknown():
    assert ac.classify_rebuild_failure("rebuild/test_something_else.py::test_x", update_pins=False) == "hard"
    assert ac.classify_rebuild_failure("rebuild/test_review_autosave.py::test_y", update_pins=False) == "hard"


def test_classify_update_pins_keeps_census_failures_hard():
    """Under --update-pins the gate only starts after the census step has rewritten the pins, so a census-pinned failure is judged against the pins the suite actually read — hard, with no amnesty to earn."""
    stdout = "\n".join(
        [
            "FAILED rebuild/test_review_build.py::test_totals_pinned",
            "FAILED rebuild/test_settle.py::test_x",
            "ERROR rebuild/test_review_ink.py::test_y",
        ]
    )
    outcome = ac.classify_rebuild_output(stdout, 1, update_pins=True)
    assert outcome.status == "FAILED (3 unexplained)"
    assert outcome.hard_ids == [
        "rebuild/test_review_build.py::test_totals_pinned",
        "rebuild/test_settle.py::test_x",
        "rebuild/test_review_ink.py::test_y",
    ]
    assert not outcome.recordable


def test_dry_run_plan_default():
    plan = ac.build_plan(
        verdicts=Path("verdicts-X.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=1,
    )
    assert plan.snapshot_dir == ac.ROOT / "tmp" / "review-pre-abc1234"
    assert plan.carry_out == ac.ROOT / "verdicts-carried-abc1234.json"

    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv == ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
    assert by_name["surface-build"].argv == ["uv", "run", "python", "-m", "rebuild.review.build"]
    assert by_name["carry"].argv == [
        "uv",
        "run",
        "python",
        str(ac.CARRY_TOOL),
        "--source",
        str(ac.ROOT / "tmp" / "review-pre-abc1234"),
        "verdicts-X.json",
        "--out",
        str(ac.ROOT / "verdicts-carried-abc1234.json"),
    ]
    assert by_name["census"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.review.census",
        "--check",
        "--surface",
        str(ac.REVIEW_OUT),
    ]
    assert by_name["gate:rebuild"].argv == [
        "uv",
        "run",
        "pytest",
        "rebuild/",
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        "--tb=no",
        "-rfE",
    ]
    assert by_name["gate:make-test"].argv == ["make", "test"]
    assert _argv(by_name["gate:js"])[:2] == ["node", "--test"]
    assert all(name.endswith(".test.js") for name in _argv(by_name["gate:js"])[2:])
    assert _argv(by_name["gate:conform"])[:6] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.pipeline.run_m1",
        "--conform-only",
    ]


def test_dry_run_plan_conform_jobs_cap():
    plan = _plan(ncores=12)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", "8"]
    assert plan.conform_jobs == 8

    small = _plan(ncores=4)
    small_by_name = {step.name: step for step in small.steps}
    assert _argv(small_by_name["gate:conform"])[-2:] == ["--jobs", "4"]

    single = _plan(ncores=1)
    single_by_name = {step.name: step for step in single.steps}
    assert _argv(single_by_name["gate:conform"])[-1] == "--conform-only"


def test_dry_run_plan_conform_horizon():
    plan = _plan(conform_horizon=4)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["gate:conform"])[-2:] == ["--conform-horizon", "4"]
    assert plan.conform_horizon == 4

    default = _plan()
    default_by_name = {step.name: step for step in default.steps}
    assert "--conform-horizon" not in _argv(default_by_name["gate:conform"])
    assert default.conform_horizon == ac.CONFORM_HORIZON_DEFAULT


def test_dry_run_plan_skip_conform():
    plan = _plan(skip_conform=True)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert by_name["gate:conform"].note == "SKIPPED (--skip-conform)"
    assert by_name["gate:rebuild"].argv is not None


def test_dry_run_plan_runs_the_kernel_differential_in_its_own_lane():
    plan = _plan()
    step = {step.name: step for step in plan.steps}["gate:kernel-differential"]
    assert step.argv == ac.kernel_differential_argv(ac.KERNEL_THREADS_DEFAULT)
    assert step.lane == "kernel"
    assert plan.kernel_threads == ac.KERNEL_THREADS_DEFAULT


def test_dry_run_plan_kernel_threads_reach_the_argv_and_the_lane():
    plan = _plan(kernel_threads=6)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["gate:kernel-differential"])[-2:] == ["--threads", "6"]
    assert "(--threads 6)" in ac.render_plan(plan)


def test_dry_run_plan_skip_kernel_differential():
    forced = _plan(skip_kernel_differential=True)
    by_name = {step.name: step for step in forced.steps}
    assert by_name["gate:kernel-differential"].argv is None
    assert by_name["gate:kernel-differential"].note == "SKIPPED (--skip-kernel-differential)"
    assert by_name["gate:conform"].argv is not None
    proved = _plan(skip_kernel_differential=True, kernel_differential_note="sources unchanged")
    proved_step = {step.name: step for step in proved.steps}["gate:kernel-differential"]
    assert proved_step.note == "SKIPPED (sources unchanged)"
    assert "Lane kernel                      : SKIPPED (sources unchanged)" in ac.render_plan(proved)


def test_dry_run_plan_deferred_kernel_differential_replaces_its_step():
    plan = _plan(deferred=frozenset({"kernel-differential"}))
    step = {step.name: step for step in plan.steps}["gate:kernel-differential"]
    assert step.argv is None
    assert step.note == f"DEFERRED ({ac.DEFER_NOTE})"
    rendered = ac.render_plan(plan)
    assert f"Lane kernel                      : DEFERRED ({ac.DEFER_NOTE})" in rendered
    assert "deferred to the next pass        : gate:kernel-differential" in rendered


def test_dry_run_plan_runs_the_kernel_harness_in_its_own_lane():
    plan = _plan()
    step = {step.name: step for step in plan.steps}["gate:kernel-harness"]
    assert step.argv == ac.kernel_harness_argv()
    assert step.lane == "harness"
    assert step.note == "submitted with gate:rebuild, once the census step has landed; parks behind it"


def test_dry_run_plan_skip_kernel_harness():
    forced = _plan(skip_kernel_harness=True)
    by_name = {step.name: step for step in forced.steps}
    assert by_name["gate:kernel-harness"].argv is None
    assert by_name["gate:kernel-harness"].note == "SKIPPED (--skip-kernel-harness)"
    assert by_name["gate:kernel-differential"].argv is not None
    proved = _plan(skip_kernel_harness=True, kernel_harness_note="alphabet structure unchanged")
    proved_step = {step.name: step for step in proved.steps}["gate:kernel-harness"]
    assert proved_step.note == "SKIPPED (alphabet structure unchanged)"
    assert "Lane harness                     : SKIPPED (alphabet structure unchanged)" in ac.render_plan(
        proved
    )


def test_dry_run_plan_deferred_kernel_harness_replaces_its_step():
    plan = _plan(deferred=frozenset({"kernel-harness"}))
    step = {step.name: step for step in plan.steps}["gate:kernel-harness"]
    assert step.argv is None
    assert step.note == f"DEFERRED ({ac.DEFER_NOTE})"
    rendered = ac.render_plan(plan)
    assert f"Lane harness                     : DEFERRED ({ac.DEFER_NOTE})" in rendered
    assert "deferred to the next pass        : gate:kernel-harness" in rendered


def test_dry_run_plan_merge_follows_carry():
    plan = _plan(snapshot_dir=None, short_id="abc1234")
    names = [step.name for step in plan.steps]
    assert names.index("merge") == names.index("carry") + 1
    assert names.index("echo-fill") == names.index("merge") + 1
    assert names.index("echo-merge") == names.index("echo-fill") + 1
    assert names.index("standing-fill") == names.index("echo-merge") + 1
    assert names.index("standing-merge") == names.index("standing-fill") + 1
    assert names.index("census") == names.index("standing-merge") + 1
    by_name = {step.name: step for step in plan.steps}
    assert by_name["merge"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.merge_verdicts",
        str(ac.ROOT / "verdicts-carried-abc1234.json"),
    ]
    assert by_name["echo-fill"].argv == [
        "uv",
        "run",
        "python",
        str(ac.ECHO_TOOL),
        str(ac.AUTOSAVE),
    ]
    assert by_name["echo-merge"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.merge_verdicts",
        str(ac.ROOT / "verdicts-echo-fill.json"),
    ]
    assert by_name["standing-fill"].argv == [
        "uv",
        "run",
        "python",
        str(ac.STANDING_TOOL),
        str(ac.AUTOSAVE),
    ]
    assert by_name["standing-merge"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.merge_verdicts",
        str(ac.ROOT / "verdicts-standing-fill.json"),
    ]
    assert plan.do_merge is True


def test_dry_run_plan_no_merge_skips_the_merge_step():
    plan = _plan(no_merge=True)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["merge"].argv is None
    assert by_name["merge"].note == "SKIPPED (--no-merge)"
    assert by_name["echo-fill"].argv is None
    assert by_name["echo-fill"].note == "SKIPPED (--no-merge)"
    assert by_name["echo-merge"].argv is None
    assert by_name["echo-merge"].note == "SKIPPED (--no-merge)"
    assert by_name["standing-fill"].argv is None
    assert by_name["standing-fill"].note == "SKIPPED (--no-merge)"
    assert by_name["standing-merge"].argv is None
    assert by_name["standing-merge"].note == "SKIPPED (--no-merge)"
    assert by_name["carry"].argv is not None
    assert plan.do_merge is False


def test_dry_run_plan_rehearsal_never_touches_the_autosave():
    plan = _plan(review_out=Path("tmp/reh"))
    by_name = {step.name: step for step in plan.steps}
    assert by_name["merge"].argv is None
    assert "rehearsal" in by_name["merge"].note
    assert by_name["echo-fill"].argv is None
    assert "rehearsal" in by_name["echo-fill"].note
    assert by_name["echo-merge"].argv is None
    assert "rehearsal" in by_name["echo-merge"].note
    assert by_name["standing-fill"].argv is None
    assert "rehearsal" in by_name["standing-fill"].note
    assert by_name["standing-merge"].argv is None
    assert "rehearsal" in by_name["standing-merge"].note
    assert plan.do_merge is False


def test_dry_run_plan_complaints_follows_census_and_reads_the_autosave(tmp_path, monkeypatch):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()
    names = [step.name for step in plan.steps]
    assert names.index("complaints") == names.index("census") + 1
    by_name = {step.name: step for step in plan.steps}
    assert by_name["complaints"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.complaint_docket",
        str(autosave),
    ]
    assert by_name["complaints"].note == "informational, non-gating"
    assert plan.complaints_note == ""


def test_dry_run_plan_complaints_skips_on_rehearsal_first_run_and_missing_autosave(tmp_path, monkeypatch):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    rehearsal = _plan(review_out=Path("tmp/reh"))
    by_name = {step.name: step for step in rehearsal.steps}
    assert by_name["complaints"].argv is None
    assert "rehearsal" in by_name["complaints"].note
    assert rehearsal.complaints_note != ""

    first = _plan(first_run=True, verdicts=None)
    by_name = {step.name: step for step in first.steps}
    assert by_name["complaints"].argv is None
    assert "first run" in by_name["complaints"].note

    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "missing.json")
    absent = _plan()
    by_name = {step.name: step for step in absent.steps}
    assert by_name["complaints"].argv is None
    assert "no verdicts store" in by_name["complaints"].note


def test_do_complaints_scrapes_the_headline_and_never_fails_the_cycle():
    def spawn(name, argv, *, emit, registry, stream):
        return _step(
            name,
            0,
            "wrote /x/tmp/complaints-data.json: 3 open complaints (1 fresh / 2 standing) in 2 groups — 5 park candidates, 4 approved sharers likely churn if fixed\n",
        )

    status = ac._do_complaints(spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry())
    assert status.startswith("3 open complaints")

    def spawn_empty(name, argv, *, emit, registry, stream):
        return _step(name, 0, "no open complaints\n")

    status = ac._do_complaints(spawn=spawn_empty, emit=ac._Emitter(), registry=ac._ChildRegistry())
    assert status == "no open complaints"

    def spawn_broken(name, argv, *, emit, registry, stream):
        return _step(name, 2, "boom\n")

    status = ac._do_complaints(spawn=spawn_broken, emit=ac._Emitter(), registry=ac._ChildRegistry())
    assert status == "FAILED (exit 2) — informational"


def test_dry_run_plan_merge_skipped_without_carry():
    no_carry = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="abc",
    )
    no_carry_by_name = {step.name: step for step in no_carry.steps}
    assert no_carry_by_name["merge"].note == "SKIPPED (--no-carry)"
    assert no_carry_by_name["echo-fill"].note == "SKIPPED (--no-carry)"
    assert no_carry_by_name["echo-merge"].note == "SKIPPED (--no-carry)"
    assert no_carry_by_name["standing-fill"].note == "SKIPPED (--no-carry)"
    assert no_carry_by_name["standing-merge"].note == "SKIPPED (--no-carry)"
    first = ac.build_plan(
        verdicts=None,
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=True,
        short_id="abc",
    )
    first_by_name = {step.name: step for step in first.steps}
    assert first_by_name["merge"].note == "SKIPPED (first run)"
    assert first_by_name["echo-fill"].note == "SKIPPED (first run)"
    assert first_by_name["echo-merge"].note == "SKIPPED (first run)"
    assert first_by_name["standing-fill"].note == "SKIPPED (first run)"
    assert first_by_name["standing-merge"].note == "SKIPPED (first run)"


def test_dry_run_plan_no_carry_and_update_pins():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        update_pins=True,
        skip_gates=False,
        first_run=False,
        short_id="def5678",
    )
    assert plan.carry_out is None
    by_name = {step.name: step for step in plan.steps}
    assert by_name["carry"].argv is None
    assert "--update" in _argv(by_name["census"])


def test_dry_run_plan_first_run_skips_snapshot_and_carry():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=True,
        short_id="0000000",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["snapshot"].argv is None
    assert by_name["carry"].argv is None
    assert plan.carry_out is None


def test_dry_run_plan_skip_gates():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=True,
        first_run=False,
        short_id="abc",
    )
    names = {step.name for step in plan.steps}
    assert "gate:js" not in names
    assert "gate:rebuild" not in names
    assert "gate:conform" not in names


def test_render_plan_is_stringable():
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
    )
    text = ac.render_plan(plan)
    assert "review-pre-abc1234" in text
    assert "rebuild.pipeline.run_m1" in text


def test_parse_surface_build_line():
    stderr = "some noise\nWrote /x/rebuild/out/review (15897 units, 81867 rows, 16 batches)\ntrailer\n"
    assert ac._parse_surface_build(stderr) == (15897, 81867, 16)


def test_parse_surface_build_missing():
    assert ac._parse_surface_build("nothing here\n") is None


def _argv(step: ac.Step) -> list[str]:
    assert step.argv is not None
    return step.argv


def _plan(**overrides: Any) -> ac.Plan:
    kw: dict[str, Any] = dict(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=Path("/tmp/snap-x"),
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="testid",
        ncores=4,
    )
    kw.update(overrides)
    return ac.build_plan(**kw)


def _step(name="x", rc=0, stdout="", stderr=""):
    return ac._StepResult(name, rc, stdout, stderr, 0.0)


def _pass_run_m1(report, *, spawn, emit, registry, budget, **_):
    report.unmatched = 1
    report.multi_matched = 0
    report.boundary_pass = True
    report.pins_pass = True
    return ac.GateOutcome(True, [], 1, 0)


def _surface_ok(report, *, spawn, emit, registry, review_out, budget, **_):
    report.surface_units = 1
    return True


def _carry_ok(report, *, spawn, emit, registry, plan):
    return True


def _merge_ok(report, *, spawn, emit, registry, plan):
    report.merge_status = "merged"
    return True


def _echo_fill_ok(report, *, spawn, emit, registry, plan):
    report.echo_fill_status = "filled"
    return True


def _echo_merge_ok(report, *, spawn, emit, registry, plan):
    report.echo_merge_status = "merged"
    return True


def _standing_fill_ok(report, *, spawn, emit, registry, plan):
    report.standing_fill_status = "filled"
    return True


def _standing_merge_ok(report, *, spawn, emit, registry, plan):
    report.standing_merge_status = "merged"
    report.standing_merge_lines = ["nothing changed: the autosave already holds all 3 verdicts"]
    return True


def _census_clean(*, spawn, emit, registry, update_pins, surface, **_):
    return "clean"


def _js_ok(spawn, emit, registry):
    return _step("gate:js", 0)


def _make_ok(spawn, emit, registry):
    return _step("gate:make-test", 0)


def _rebuild_green(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
    return ac.RebuildOutcome("green", [], [])


def _conform_green(pool_policy, make_fut, spawn, emit, registry, argv):
    return "green", []


def _kernel_green(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
    return "green", []


def _harness_green(pool_policy, rebuild_fut, spawn, emit, registry, argv):
    return "green", []


def _patch_gate_fingerprints(monkeypatch):
    """The gate greens' keys, for a test that only cares that a green was or wasn't recorded. Unstubbed these are the live ones: _run_cycle snapshots them before the gates and _record_gate_greens recomputes them after, and each pass runs git ls-files over the repo and sha256s all of rebuild/, glyph_data/, the fonts, and the baseline TSVs — several seconds per test, and an answer that depends on the working tree rather than on the arrangement the test set up. Whether a moved key withholds the green is its own test."""
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "rfp")
    monkeypatch.setattr(ac, "kernel_differential_skip_fingerprint", lambda root=None: "kfp")
    monkeypatch.setattr(ac, "kernel_harness_skip_fingerprint", lambda root=None: "hfp")


def _patch_build_chain(monkeypatch):
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", _echo_fill_ok)
    monkeypatch.setattr(ac, "_do_echo_merge", _echo_merge_ok)
    monkeypatch.setattr(ac, "_do_standing_fill", _standing_fill_ok)
    monkeypatch.setattr(ac, "_do_standing_merge", _standing_merge_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)


def test_merge_failure_fails_the_cycle(monkeypatch, capsys):
    def failing_merge(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", failing_merge)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.merge_status == "FAILED (exit 1)"
    assert "verdict merge failed" in capsys.readouterr().out


def test_merge_not_run_when_carry_fails(monkeypatch, capsys):
    called = {"merge": False}

    def failing_carry(report, *, spawn, emit, registry, plan):
        return False

    def watching_merge(report, *, spawn, emit, registry, plan):
        called["merge"] = True
        return True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", failing_carry)
    monkeypatch.setattr(ac, "_do_merge", watching_merge)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert not called["merge"]
    assert report.merge_status == "not run (carry failed)"
    assert "carry_verdicts failed" in capsys.readouterr().out


def test_echo_fill_failure_fails_the_cycle(monkeypatch, capsys):
    called = {"echo_merge": False}

    def failing_echo_fill(report, *, spawn, emit, registry, plan):
        report.echo_fill_status = "FAILED (exit 1)"
        return False

    def watching_echo_merge(report, *, spawn, emit, registry, plan):
        called["echo_merge"] = True
        return True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", failing_echo_fill)
    monkeypatch.setattr(ac, "_do_echo_merge", watching_echo_merge)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert not called["echo_merge"]
    assert report.echo_fill_status == "FAILED (exit 1)"
    assert report.echo_merge_status == "not run (echo-fill failed)"
    assert report.standing_fill_status == "not run (echo-fill failed)"
    assert report.standing_merge_status == "not run (echo-fill failed)"
    assert "echo-fill failed" in capsys.readouterr().out


def test_echo_merge_failure_fails_the_cycle(monkeypatch, capsys):
    def failing_echo_merge(report, *, spawn, emit, registry, plan):
        report.echo_merge_status = "FAILED (exit 1)"
        return False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", _echo_fill_ok)
    monkeypatch.setattr(ac, "_do_echo_merge", failing_echo_merge)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.echo_fill_status == "filled"
    assert report.echo_merge_status == "FAILED (exit 1)"
    assert report.standing_fill_status == "not run (echo-merge failed)"
    assert report.standing_merge_status == "not run (echo-merge failed)"
    assert "echo-merge failed" in capsys.readouterr().out


def test_standing_fill_failure_fails_the_cycle(monkeypatch, capsys):
    called = {"standing_merge": False}

    def failing_standing_fill(report, *, spawn, emit, registry, plan):
        report.standing_fill_status = "FAILED (exit 1)"
        return False

    def watching_standing_merge(report, *, spawn, emit, registry, plan):
        called["standing_merge"] = True
        return True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", _echo_fill_ok)
    monkeypatch.setattr(ac, "_do_echo_merge", _echo_merge_ok)
    monkeypatch.setattr(ac, "_do_standing_fill", failing_standing_fill)
    monkeypatch.setattr(ac, "_do_standing_merge", watching_standing_merge)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert not called["standing_merge"]
    assert report.standing_fill_status == "FAILED (exit 1)"
    assert report.standing_merge_status == "not run (standing-fill failed)"
    assert "standing-fill failed" in capsys.readouterr().out


def test_echo_helpers_not_run_when_do_merge_false(monkeypatch):
    called = {"echo_fill": False, "echo_merge": False}

    def watching_echo_fill(report, *, spawn, emit, registry, plan):
        called["echo_fill"] = True
        return True

    def watching_echo_merge(report, *, spawn, emit, registry, plan):
        called["echo_merge"] = True
        return True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", watching_echo_fill)
    monkeypatch.setattr(ac, "_do_echo_merge", watching_echo_merge)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan(no_merge=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert not called["echo_fill"]
    assert not called["echo_merge"]
    assert report.echo_fill_status == "not run"
    assert report.echo_merge_status == "not run"


def test_do_merge_parses_the_summary_line():
    stdout = "\n".join(
        [
            "verdicts-carried-abc.json: 5 added, 0 replaced, 2 kept newer",
            "merged 1 file(s) into verdicts-autosave.json: 5 added, 0 replaced, 2 kept newer; "
            "store holds 7 verdicts (7 effective) on manifest S1",
        ]
    )

    def fake_spawn(name, argv, *, emit, registry, stream):
        assert name == "merge"
        assert argv[:5] == ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts"]
        return _step(name, 0, stdout=stdout)

    report = ac.CycleReport()
    ok = ac._do_merge(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan()
    )
    assert ok
    assert report.merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.merge_lines)


def test_do_echo_fill_parses_the_summary_line():
    stdout = "\n".join(
        [
            "wrote verdicts-echo-fill.json: 37 echo-fill verdicts onto manifest S1",
            "no echo group holds disagreeing verdicts",
        ]
    )

    def fake_spawn(name, argv, *, emit, registry, stream):
        assert name == "echo-fill"
        assert argv == ["uv", "run", "python", str(ac.ECHO_TOOL), str(ac.AUTOSAVE)]
        return _step(name, 0, stdout=stdout)

    report = ac.CycleReport()
    ok = ac._do_echo_fill(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan()
    )
    assert ok
    assert report.echo_fill_status == "filled"
    assert any(
        line.startswith("wrote verdicts-echo-fill.json: 37 echo-fill verdicts")
        for line in report.echo_fill_lines
    )


def test_do_echo_fill_passes_through_the_disagreement_audit(capsys):
    stdout = "\n".join(
        [
            "wrote verdicts-echo-fill.json: 0 echo-fill verdicts onto manifest S1",
            "",
            "2 echo groups hold disagreeing verdicts — the same change judged differently; worth a re-check:",
            "  e-123  #units=u-1,u-2",
            "    u-1       ·Day ~b~ ·Tea                approve   looks right",
            "    u-2       ·Day ~b~ ·Tea                reject    stub too long",
        ]
    )

    def fake_spawn(name, argv, *, emit, registry, stream):
        return _step(name, 0, stdout=stdout)

    report = ac.CycleReport()
    ok = ac._do_echo_fill(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan()
    )
    assert ok
    out = capsys.readouterr().out
    assert "2 echo groups hold disagreeing verdicts" in out
    assert "e-123  #units=u-1,u-2" in out


def test_do_echo_merge_parses_the_summary_line():
    stdout = "\n".join(
        [
            "verdicts-echo-fill.json: 12 added, 0 replaced, 3 kept newer",
            "merged 1 file(s) into verdicts-autosave.json: 12 added, 0 replaced, 3 kept newer; "
            "store holds 40 verdicts (40 effective) on manifest S1",
        ]
    )

    def fake_spawn(name, argv, *, emit, registry, stream):
        assert name == "echo-merge"
        assert argv == [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.tools.merge_verdicts",
            str(ac.ECHO_FILL),
        ]
        return _step(name, 0, stdout=stdout)

    report = ac.CycleReport()
    ok = ac._do_echo_merge(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan()
    )
    assert ok
    assert report.echo_merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.echo_merge_lines)


def test_do_standing_fill_parses_the_summary_lines():
    stdout = "\n".join(
        [
            "wrote verdicts-standing-fill.json: 25 standing-approval verdicts onto manifest S1",
            "  tea-oy-ligature-break: 25 filled, 64 already verdicted, 0 held for review by except_left",
        ]
    )

    def fake_spawn(name, argv, *, emit, registry, stream):
        assert name == "standing-fill"
        assert argv == ["uv", "run", "python", str(ac.STANDING_TOOL), str(ac.AUTOSAVE)]
        return _step(name, 0, stdout=stdout)

    report = ac.CycleReport()
    ok = ac._do_standing_fill(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan()
    )
    assert ok
    assert report.standing_fill_status == "filled"
    assert any(
        line.startswith("wrote verdicts-standing-fill.json: 25 standing-approval verdicts")
        for line in report.standing_fill_lines
    )
    assert any(line.endswith("held for review by except_left") for line in report.standing_fill_lines)


def test_do_standing_merge_parses_the_summary_line():
    stdout = "\n".join(
        [
            "verdicts-standing-fill.json: 25 added, 0 replaced, 0 kept newer",
            "merged 1 file(s) into verdicts-autosave.json: 25 added, 0 replaced, 0 kept newer; "
            "store holds 65 verdicts (65 effective) on manifest S1",
        ]
    )

    def fake_spawn(name, argv, *, emit, registry, stream):
        assert name == "standing-merge"
        assert argv == [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.tools.merge_verdicts",
            str(ac.STANDING_FILL),
        ]
        return _step(name, 0, stdout=stdout)

    report = ac.CycleReport()
    ok = ac._do_standing_merge(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan()
    )
    assert ok
    assert report.standing_merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.standing_merge_lines)


def test_gates_launch_before_run_m1_finishes(monkeypatch):
    record = {}
    js_started = threading.Event()
    make_started = threading.Event()
    release_run_m1 = threading.Event()

    def fake_js(spawn, emit, registry):
        record["js_start"] = time.monotonic()
        js_started.set()
        return _step("gate:js", 0)

    def fake_make(spawn, emit, registry):
        record["make_start"] = time.monotonic()
        make_started.set()
        return _step("gate:make-test", 0)

    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        release_run_m1.wait()
        record["run_m1_finish"] = time.monotonic()
        return ac.GateOutcome(True, [], 1, 0)

    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    emit = ac._Emitter()
    registry = ac._ChildRegistry()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, emit, registry, spawn=lambda *a, **k: _step())
        )
    )
    t.start()
    js_started.wait()
    make_started.wait()
    assert "run_m1_finish" not in record
    release_run_m1.set()
    t.join()

    assert record["js_start"] < record["run_m1_finish"]
    assert record["make_start"] < record["run_m1_finish"]


def test_gate_rebuild_waits_for_run_m1_pass(monkeypatch):
    record = {}

    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        record["run_m1_finish"] = time.monotonic()
        return ac.GateOutcome(True, [], 1, 0)

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        record["rebuild_invoked"] = time.monotonic()
        return ac.RebuildOutcome("green", [], [])

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert record["rebuild_invoked"] >= record["run_m1_finish"]


def test_gate_rebuild_skipped_when_run_m1_fails(monkeypatch, capsys):
    called = {"rebuild": False}

    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        return None

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        called["rebuild"] = True
        return ac.RebuildOutcome("green", [], [])

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert not called["rebuild"]
    assert report.gate_rebuild == "not run (run_m1 gate failed)"
    assert report.gate_conform == "not run (run_m1 gate failed)"
    assert rc == 1
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_pool_queue_serializes_rebuild_after_make_test(monkeypatch):
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()

    def fake_make(spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:rebuild":
            record["rebuild_start"] = time.monotonic()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="queue")
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    make_running.wait()
    release_make.set()
    t.join()

    assert record["rebuild_start"] >= record["make_finish"]


def test_pool_overlap_starts_rebuild_before_make_test_done(monkeypatch):
    record = {}
    release_make = threading.Event()
    rebuild_started = threading.Event()

    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        record["run_m1_finish"] = time.monotonic()
        return ac.GateOutcome(True, [], 1, 0)

    def fake_make(spawn, emit, registry):
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:rebuild":
            record["rebuild_start"] = time.monotonic()
            rebuild_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    rebuild_started.wait()
    release_make.set()
    t.join()

    assert record["rebuild_start"] < record["make_finish"]
    assert record["rebuild_start"] >= record["run_m1_finish"]


def test_pool_queue_rebuild_waits_for_conform_gate(monkeypatch):
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    release_conform = threading.Event()
    conform_running = threading.Event()
    rebuild_started = threading.Event()

    def fake_make(spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        conform_running.set()
        release_conform.wait()
        record["conform_finish"] = time.monotonic()
        return "green", []

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:rebuild":
            record["rebuild_start"] = time.monotonic()
            record["rebuild_argv"] = argv
            rebuild_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_conform_task", fake_conform)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="queue")
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    make_running.wait()
    conform_running.wait()
    assert "rebuild_start" not in record
    release_make.set()
    assert not rebuild_started.wait(0.2)
    release_conform.set()
    rebuild_started.wait()
    t.join()

    assert record["rebuild_start"] >= record["conform_finish"]
    assert record["rebuild_start"] >= record["make_finish"]
    assert record["rebuild_argv"] == list(ac.REBUILD_PYTEST_ARGV)
    assert report.gate_rebuild == "green"
    assert box["rc"] == 0


def test_pool_queue_rebuild_falls_back_to_make_test_when_conform_skipped(monkeypatch):
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    rebuild_started = threading.Event()

    def fake_make(spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:rebuild":
            record["rebuild_start"] = time.monotonic()
            rebuild_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="queue", skip_conform=True)
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    make_running.wait()
    assert not rebuild_started.wait(0.2)
    release_make.set()
    rebuild_started.wait()
    t.join()

    assert record["rebuild_start"] >= record["make_finish"]
    assert report.gate_conform == "skipped (--skip-conform)"
    assert report.gate_rebuild == "green"
    assert box["rc"] == 0


def test_pool_queue_kernel_gate_waits_for_the_conform_sweep(monkeypatch):
    """The heavy chain runs make-test -> conform -> kernel-differential -> rebuild, so the kernel's child cannot spawn while the sweep is still hot. This drives the real gate task: the summary its child writes is the one the verdict comes from."""
    record = {}
    release_conform = threading.Event()
    conform_running = threading.Event()
    kernel_started = threading.Event()

    def fake_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        conform_running.set()
        release_conform.wait()
        record["conform_finish"] = time.monotonic()
        return "green", []

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:kernel-differential":
            record["kernel_start"] = time.monotonic()
            record["kernel_argv"] = argv
            ac.KERNEL_DIFFERENTIAL_SUMMARY.write_text(json.dumps(_kernel_summary()))
            kernel_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", fake_conform)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="queue")
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    conform_running.wait()
    assert not kernel_started.wait(0.2)
    release_conform.set()
    kernel_started.wait()
    t.join()

    assert record["kernel_start"] >= record["conform_finish"]
    assert record["kernel_argv"] == ac.kernel_differential_argv(ac.KERNEL_THREADS_DEFAULT)
    assert report.gate_kernel_differential == "green"
    assert box["rc"] == 0


def test_pool_queue_rebuild_waits_for_the_kernel_gate(monkeypatch):
    record = {}
    release_kernel = threading.Event()
    kernel_running = threading.Event()
    rebuild_started = threading.Event()

    def fake_kernel(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        kernel_running.set()
        release_kernel.wait()
        record["kernel_finish"] = time.monotonic()
        return "green", []

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:rebuild":
            record["rebuild_start"] = time.monotonic()
            rebuild_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", fake_kernel)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="queue")
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    kernel_running.wait()
    assert not rebuild_started.wait(0.2)
    release_kernel.set()
    rebuild_started.wait()
    t.join()

    assert record["rebuild_start"] >= record["kernel_finish"]
    assert report.gate_kernel_differential == "green"
    assert report.gate_rebuild == "green"
    assert box["rc"] == 0


def test_pool_queue_kernel_harness_waits_for_the_rebuild_suite(monkeypatch):
    """The harness is the tail of the whole heavy chain: parking on gate:rebuild transitively waits out make-test and conform too, so its child cannot spawn while the suite is still hot. This drives the real gate task: the summary its child writes is the one the verdict comes from."""
    record = {}
    release_rebuild = threading.Event()
    rebuild_running = threading.Event()
    harness_started = threading.Event()

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        rebuild_running.set()
        release_rebuild.wait()
        record["rebuild_finish"] = time.monotonic()
        return ac.RebuildOutcome("green", [], [])

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:kernel-harness":
            record["harness_start"] = time.monotonic()
            record["harness_argv"] = argv
            ac.KERNEL_HARNESS_SUMMARY.write_text(json.dumps(_harness_summary()))
            harness_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="queue")
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    rebuild_running.wait()
    assert not harness_started.wait(0.2)
    release_rebuild.set()
    harness_started.wait()
    t.join()

    assert record["harness_start"] >= record["rebuild_finish"]
    assert record["harness_argv"] == ac.kernel_harness_argv()
    assert report.gate_kernel_harness == "green"
    assert box["rc"] == 0


def test_the_gate_pool_seats_every_gate_task_at_once():
    """Under the queue policy a parked task holds its worker for the whole wait — conform on make-test, kernel-differential on conform, rebuild on all three, kernel-harness on rebuild — so the pool seats every gate task at once plus one spare, the same one-seat headroom the pool carried when it was five workers over four tasks. The chain cannot actually deadlock at a smaller width — submission order matches the parking order and the pool is FIFO, so a task only ever parks on a future already seated or done — but a seat short of the task count would serialize a wait behind an unrelated task's completion, which is the queueing this pool exists not to do."""
    gate_tasks = (
        ac._gate_js_task,
        ac._gate_make_test_task,
        ac._gate_conform_task,
        ac._gate_kernel_differential_task,
        ac._gate_rebuild_task,
        ac._gate_kernel_harness_task,
    )
    assert ac._GATE_POOL_WORKERS == len(gate_tasks) + 1


def test_gate_kernel_differential_task_judges_only_this_cycles_summary(monkeypatch):
    """The stale summary is unlinked before the child spawns, so a run that writes none can never be judged from the last pass's verdict."""
    ac.KERNEL_DIFFERENTIAL_SUMMARY.write_text(json.dumps(_kernel_summary()))
    seen = {}

    def fake_spawn(name, argv, *, emit, registry, stream):
        seen["existed"] = ac.KERNEL_DIFFERENTIAL_SUMMARY.exists()
        return _step(name, 0)

    status, failures = ac._gate_kernel_differential_task(
        "queue", None, None, fake_spawn, ac._Emitter(), ac._ChildRegistry(), ["kernel-gate"]
    )
    assert seen["existed"] is False
    assert status == "FAILED (no kernel_differential_summary.json)"
    assert failures == ["kernel-differential gate: rebuild.tools.kernel_gate wrote no summary"]


def test_gate_kernel_differential_task_fails_a_nonzero_exit_over_a_passing_summary():
    def fake_spawn(name, argv, *, emit, registry, stream):
        ac.KERNEL_DIFFERENTIAL_SUMMARY.write_text(json.dumps(_kernel_summary()))
        return _step(name, 3)

    status, failures = ac._gate_kernel_differential_task(
        "queue", None, None, fake_spawn, ac._Emitter(), ac._ChildRegistry(), ["kernel-gate"]
    )
    assert status == "FAILED (exit 3)"
    assert failures == ["kernel-differential gate: exited 3 despite a passing summary"]


def test_gate_kernel_harness_task_judges_only_this_cycles_summary():
    """The stale summary is unlinked before the child spawns, so a run that writes none can never be judged from the arms of the pass that last armed the gate."""
    ac.KERNEL_HARNESS_SUMMARY.write_text(json.dumps(_harness_summary()))
    seen = {}

    def fake_spawn(name, argv, *, emit, registry, stream):
        seen["existed"] = ac.KERNEL_HARNESS_SUMMARY.exists()
        return _step(name, 0)

    status, failures = ac._gate_kernel_harness_task(
        "queue", None, fake_spawn, ac._Emitter(), ac._ChildRegistry(), ["kernel-harness-gate"]
    )
    assert seen["existed"] is False
    assert status == "FAILED (no kernel_harness_summary.json)"
    assert failures == ["kernel-harness gate: rebuild.tools.kernel_harness_gate wrote no summary"]


def test_gate_kernel_harness_task_fails_a_nonzero_exit_over_a_passing_summary():
    def fake_spawn(name, argv, *, emit, registry, stream):
        ac.KERNEL_HARNESS_SUMMARY.write_text(json.dumps(_harness_summary()))
        return _step(name, 4)

    status, failures = ac._gate_kernel_harness_task(
        "queue", None, fake_spawn, ac._Emitter(), ac._ChildRegistry(), ["kernel-harness-gate"]
    )
    assert status == "FAILED (exit 4)"
    assert failures == ["kernel-harness gate: exited 4 despite a passing summary"]


def test_summary_exact_under_out_of_order_completion(monkeypatch, capsys):
    ev_js = threading.Event()
    ev_make = threading.Event()
    ev_rebuild = threading.Event()

    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        report.unmatched = 7777
        report.multi_matched = 0
        report.boundary_pass = True
        report.pins_pass = True
        return ac.GateOutcome(True, [], 7777, 0)

    def fake_surface(report, *, spawn, emit, registry, review_out, budget, **_):
        report.surface_units = 15903
        report.surface_rows = 81894
        report.surface_batches = 16
        report.echo_groups = 42
        return True

    def fake_js(spawn, emit, registry):
        ev_js.wait()
        return _step("gate:js", 0)

    def fake_make(spawn, emit, registry):
        ev_make.wait()
        return _step("gate:make-test", 0)

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        ev_rebuild.wait()
        return ac.RebuildOutcome("green (1 documented baseline)", [], [])

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", fake_surface)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc",
            ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()),
        )
    )
    t.start()
    ev_make.set()
    ev_rebuild.set()
    ev_js.set()
    t.join()

    assert report.surface_units == 15903
    assert report.surface_rows == 81894
    assert report.surface_batches == 16
    assert report.echo_groups == 42
    assert report.unmatched == 7777
    assert report.gate_js == "green"
    assert report.gate_make_test == "green"
    assert report.gate_rebuild == "green (1 documented baseline)"
    assert report.gate_conform == "green"
    out = capsys.readouterr().out
    assert out.count("ARTIFACT CYCLE SUMMARY") == 1
    assert "15903" in out
    assert "81894" in out
    assert "green (1 documented baseline)" in out


_CHILD_SCRIPT = (
    "import sys\n"
    "tag = sys.argv[1]\n"
    "for i in range(200):\n"
    "    print(f'{tag}-out-{i:04d}', flush=True)\n"
    "    print(f'{tag}-err-{i:04d}', file=sys.stderr, flush=True)\n"
)


def test_prefix_streaming_serialized_no_interleave(capsys):
    emit = ac._Emitter()
    registry = ac._ChildRegistry()

    def run(tag):
        ac._run_step(
            tag, [sys.executable, "-c", _CHILD_SCRIPT, tag], emit=emit, registry=registry, stream=True
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(run, "childA"), pool.submit(run, "childB")]
        for fut in futs:
            fut.result()

    out = capsys.readouterr().out
    pattern = re.compile(r"^\[(childA|childB)\] (childA|childB)-(out|err)-\d{4}$")
    body = [line for line in out.splitlines() if line.startswith("[child")]
    assert len(body) == 800
    for line in body:
        match = pattern.match(line)
        assert match is not None, line
        assert match.group(1) == match.group(2), line


def test_gate_rebuild_stays_captured_and_parses_failures(capsys):
    stdout = "\n".join(
        [
            "FAILED rebuild/test_unknown_thing.py::test_x - boom",
            "ERROR rebuild/test_boom.py::test_y",
            "FAILED rebuild/test_surface.py::test_real_cell_bindings_all_match - x",
            "FAILED rebuild/test_review_build.py::test_totals_pinned - x",
        ]
    )
    seen = {}

    def fake_spawn(name, argv, *, emit, registry, stream):
        seen["name"] = name
        seen["stream"] = stream
        return _step(name, 1, stdout=stdout)

    emit = ac._Emitter()
    registry = ac._ChildRegistry()
    outcome = ac._gate_rebuild_task("overlap", None, None, None, fake_spawn, emit, registry, False)

    assert seen["stream"] is False
    assert len(outcome.hard_ids) == 2
    assert outcome.status == "FAILED (2 unexplained)"

    report = ac.CycleReport()
    failures = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: outcome)
        ac._join_gates(report, failures, None, fut, None, None, None, None, emit)
    assert report.gate_rebuild == "FAILED (2 unexplained)"

    out = capsys.readouterr().out
    assert not any(line.startswith("[gate:rebuild]") for line in out.splitlines())


def test_classify_rebuild_reads_colored_pytest_output():
    """Under FORCE_COLOR (as set by the agent harness) pytest wraps its FAILED lines in ANSI escapes; the classifier must still recognize the documented baseline instead of reporting an unexplained failure."""
    colored = "\n".join(
        f"\x1b[31mFAILED\x1b[0m {file}::\x1b[1m{name}\x1b[0m - x"
        for file, _, name in (test_id.partition("::") for test_id in sorted(ac.BASELINE_REBUILD_FAILURES))
    )
    outcome = ac.classify_rebuild_output(colored, 1, update_pins=False)
    assert outcome.hard_ids == []
    assert outcome.status == f"green ({len(ac.BASELINE_REBUILD_FAILURES)} documented baseline)"


def test_failure_funnels_from_concurrent_branch(monkeypatch, capsys):
    def fake_surface(report, *, spawn, emit, registry, review_out, budget, **_):
        report.surface_units = 100
        return True

    def fake_make(spawn, emit, registry):
        return _step("gate:make-test", 1)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", fake_surface)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_make_test == "FAILED (exit 1)"
    assert report.gate_js == "green"
    assert report.surface_units == 100
    assert "make test failed" in capsys.readouterr().out


def test_gate_task_exception_still_prints_one_summary(monkeypatch, capsys):
    def raising_js(spawn, emit, registry):
        raise FileNotFoundError("node not found")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_gate_js_task", raising_js)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_js == "FAILED (exception)"
    assert report.gate_make_test == "green"
    assert report.gate_rebuild == "green"
    out = capsys.readouterr().out
    assert out.count("ARTIFACT CYCLE SUMMARY") == 1
    assert "gate:js raised: FileNotFoundError('node not found')" in out


def test_queue_policy_rebuild_runs_when_make_test_task_raises(monkeypatch, capsys):
    def raising_make(spawn, emit, registry):
        raise FileNotFoundError("make not found")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", raising_make)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_make_test == "FAILED (exception)"
    assert report.gate_rebuild == "green"
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_queue_policy_rebuild_runs_when_conform_task_raises(monkeypatch, capsys):
    def raising_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        raise FileNotFoundError("conform pool blew up")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", raising_conform)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_conform == "FAILED (exception)"
    assert report.gate_rebuild == "green"
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_run_m1_failure_still_collects_make_test(monkeypatch, capsys):
    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert report.gate_make_test == "green"
    assert report.gate_rebuild == "not run (run_m1 gate failed)"
    assert report.gate_conform == "not run (run_m1 gate failed)"
    assert rc == 1
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_keyboard_interrupt_terminates_children_and_returns_130(monkeypatch, capsys):
    registry = ac._ChildRegistry()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    registry.add(proc)

    def boom(report, *, spawn, emit, registry, budget, **_):
        raise KeyboardInterrupt

    monkeypatch.setattr(ac, "_do_run_m1", boom)

    plan = _plan(skip_gates=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), registry)

    assert rc == 130
    assert registry.killed_count >= 1
    assert proc.poll() is not None
    out = capsys.readouterr().out
    assert "ARTIFACT CYCLE SUMMARY" in out
    assert "CYCLE INTERRUPTED" in out


def test_registry_add_rejects_after_terminate_all():
    registry = ac._ChildRegistry()
    registry.terminate_all()
    assert registry.closed
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert registry.add(proc) is False
    finally:
        proc.terminate()
        proc.wait()


def test_run_step_refuses_to_spawn_after_registry_closed(tmp_path):
    registry = ac._ChildRegistry()
    registry.terminate_all()
    marker = tmp_path / "child-ran.txt"
    script = f"open({str(marker)!r}, 'w').close()"
    result = ac._run_step(
        "gate:rebuild", [sys.executable, "-c", script], emit=ac._Emitter(), registry=registry, stream=False
    )
    assert result.returncode == 130
    assert result.stdout == ""
    assert not marker.exists()


def test_stage_job_budget():
    assert ac.stage_job_budget(skip_gates=False, ncores=12) == 6
    assert ac.stage_job_budget(skip_gates=False, ncores=5) == 2
    assert ac.stage_job_budget(skip_gates=False, ncores=1) == 1
    assert ac.stage_job_budget(skip_gates=True, ncores=12) == 12
    assert ac.stage_job_budget(skip_gates=True, ncores=1) == 1
    assert ac.stage_job_budget(skip_gates=False, skip_make_test=True, ncores=12) == 12
    assert ac.stage_job_budget(skip_gates=False, skip_make_test=False, ncores=12) == 6
    assert ac.stage_job_budget(skip_gates=True, skip_make_test=True, ncores=12) == 12


def test_dry_run_renders_concurrency():
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
    )
    text = ac.render_plan(plan)
    assert "pool policy: queue" in text
    assert "Lane t0" in text
    assert "Lane build" in text
    assert "Lane rebuild" in text
    assert "Lane conform" in text
    assert "Lane kernel" in text
    assert "Lane harness" in text
    assert "census -> submit gate:rebuild, gate:kernel-harness" in text
    assert "QUEUED behind gate:make-test (queue policy — one heavy pool at a time)" in text
    assert "QUEUED behind gate:conform (queue policy — one heavy pool at a time)" in text
    assert "submitted after the census step lands its verdict;" in text
    assert "QUEUED behind gate:kernel-differential (queue policy — one heavy pool at a time)" in text
    assert (
        "submitted with gate:rebuild, after the census step; QUEUED behind it (queue policy — the longest pole, and nothing queues behind it)"
        in text
    )
    assert "--jobs budget        : 6" in text

    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[-2:] == ["--jobs", "6"]
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", "6"]


def test_dry_run_skip_gates_appends_jobs_budget():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=True,
        first_run=False,
        short_id="abc1234",
        ncores=12,
    )
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[-2:] == ["--jobs", "12"]
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", "12"]
    assert "--jobs budget: 12" in ac.render_plan(plan)

    default_plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
    )
    default_by_name = {step.name: step for step in default_plan.steps}
    assert _argv(default_by_name["run_m1"])[-2:] == ["--jobs", "6"]
    assert _argv(default_by_name["surface-build"])[-2:] == ["--jobs", "6"]


def test_review_out_rehearsal_plan(monkeypatch):
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        update_pins=False,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        review_out=Path("tmp/reh"),
    )
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["surface-build"])[-2:] == ["--out", "tmp/reh"]
    assert _argv(by_name["census"])[-2:] == ["--surface", "tmp/reh"]
    assert _argv(by_name["carry"])[-2:] == ["--current-surface", "tmp/reh"]
    assert plan.census_surface == Path("tmp/reh")
    assert plan.review_out == Path("tmp/reh")
    assert str(ac.REVIEW_OUT) in by_name["snapshot"].note

    monkeypatch.setattr(ac, "server_listening", lambda *a, **k: True)
    waiver = argparse.Namespace(review_out=Path("tmp/reh"), yes=False, stop_server=False)
    assert ac._preflight(waiver) is True
    refuse = argparse.Namespace(review_out=None, yes=False, stop_server=False)
    assert ac._preflight(refuse) is False


def _green_report():
    report = ac.CycleReport()
    report.gate_js = "green"
    report.gate_rebuild = "green"
    report.gate_conform = "green"
    report.gate_kernel_differential = "green"
    report.gate_kernel_harness = "green"
    report.gate_make_test = "green"
    return report


def test_cycle_summary_payload_all_green_exit_ok():
    payload = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert payload["format"] == "ams-cycle-summary/1"
    assert payload["exit"] == "ok"
    assert payload["failures"] == []
    assert set(payload["gates"]) == {
        "js",
        "rebuild",
        "conform",
        "kernel_differential",
        "kernel_harness",
        "make_test",
    }
    assert all(gate["green"] is True for gate in payload["gates"].values())
    assert payload["finished_at"].endswith("Z")


def test_cycle_summary_payload_annotated_green_rebuild_is_green():
    report = _green_report()
    report.gate_rebuild = "green (4 documented baseline)"
    payload = ac.cycle_summary_payload(report, [], _plan(), "ok")
    assert payload["gates"]["rebuild"]["green"] is True
    assert payload["gates"]["rebuild"]["status"] == "green (4 documented baseline)"


def test_cycle_summary_payload_skipped_conform_not_green():
    report = _green_report()
    report.gate_conform = "skipped (--skip-conform)"
    payload = ac.cycle_summary_payload(report, [], _plan(skip_conform=True), "ok")
    assert payload["gates"]["conform"]["green"] is False
    assert payload["gates"]["conform"]["status"] == "skipped (--skip-conform)"
    assert payload["gates"]["js"]["green"] is True
    assert payload["plan"]["skip_conform"] is True


def test_cycle_summary_payload_marks_a_forced_conform_skip_unproved():
    report = _green_report()
    report.gate_conform = "skipped (--skip-conform)"
    payload = ac.cycle_summary_payload(report, [], _plan(skip_conform=True), "ok")
    assert payload["gates"]["conform"]["skip"] == "forced"


def test_cycle_summary_payload_marks_auto_skips_proved():
    report = _green_report()
    report.gate_conform = "skipped (inputs unchanged)"
    report.gate_rebuild = "skipped (closure unchanged)"
    report.gate_make_test = "skipped (closure unchanged)"
    plan = _plan(
        skip_conform=True,
        conform_proven=True,
        skip_rebuild_gate=True,
        skip_make_test=True,
    )
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["conform"]["skip"] == "proved"
    assert payload["gates"]["rebuild"]["skip"] == "proved"
    assert payload["gates"]["make_test"]["skip"] == "proved"
    assert payload["gates"]["js"]["skip"] is None


def test_cycle_summary_payload_failures_exit_failed():
    payload = ac.cycle_summary_payload(_green_report(), ["make test failed"], _plan(), "failed")
    assert payload["exit"] == "failed"
    assert payload["failures"] == ["make test failed"]


def test_cycle_summary_payload_plan_block_and_argv():
    plan = _plan()
    payload = ac.cycle_summary_payload(_green_report(), [], plan, "ok")
    assert payload["plan"] == {
        "verdicts": "v.json",
        "carry_out": str(plan.carry_out),
        "do_merge": True,
        "conform_horizon": ac.CONFORM_HORIZON_DEFAULT,
        "kernel_threads": ac.KERNEL_THREADS_DEFAULT,
        "pool_policy": ac.REBUILD_POOL_POLICY_DEFAULT,
        "skip_gates": False,
        "skip_conform": False,
        "skip_kernel_differential": False,
        "skip_kernel_harness": False,
        "skip_run_m1": False,
        "skip_surface": False,
        "skip_rebuild_gate": False,
        "skip_census": False,
        "defer_census": False,
        "skip_plumbing": False,
        "deferred": [],
        "update_pins": False,
        "review_out": None,
        "first_run": False,
        "short_id": "testid",
    }
    assert payload["argv"] == list(sys.argv)


def test_write_cycle_summary_reads_module_attr_at_call_time(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere" / "cycle_summary.json"
    monkeypatch.setattr(ac, "CYCLE_SUMMARY", target)
    ac.write_cycle_summary({"format": "ams-cycle-summary/1"})
    assert json.loads(target.read_text()) == {"format": "ams-cycle-summary/1"}
    assert not list(target.parent.glob("*.tmp"))


def test_cycle_writes_green_summary_with_surface(monkeypatch, tmp_path):
    surface_dir = tmp_path / "surface"
    surface_dir.mkdir()
    (surface_dir / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T12:00:00Z", "inputs_fingerprint": {"runes": "abc123"}})
    )

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=surface_dir)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["format"] == "ams-cycle-summary/1"
    assert summary["exit"] == "ok"
    assert all(gate["green"] is True for gate in summary["gates"].values())
    assert summary["surface"]["dir"] == str(surface_dir)
    assert summary["surface"]["generated_at"] == "2026-07-17T12:00:00Z"
    assert summary["surface"]["inputs_fingerprint"] == {"runes": "abc123"}


def test_cycle_writes_failed_summary_on_run_m1_failure(monkeypatch, tmp_path):
    def fake_run_m1(report, *, spawn, emit, registry, budget, **_):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=tmp_path / "surface")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["exit"] == "failed"
    assert summary["failures"]


def test_cycle_writes_interrupted_summary(monkeypatch, tmp_path):
    def boom(report, *, spawn, emit, registry, budget, **_):
        raise KeyboardInterrupt

    monkeypatch.setattr(ac, "_do_run_m1", boom)

    plan = _plan(skip_gates=True, review_out=tmp_path / "surface")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry())

    assert rc == 130
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["exit"] == "interrupted"
    assert summary["interrupted"] is True


def test_cycle_summary_surface_nulls_when_manifest_missing(monkeypatch, tmp_path):
    surface_dir = tmp_path / "surface"
    surface_dir.mkdir()

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=surface_dir)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["surface"]["dir"] == str(surface_dir)
    assert summary["surface"]["generated_at"] is None
    assert summary["surface"]["inputs_fingerprint"] is None


def _verdicts_doc(stamp, units):
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": [
            {"unit": unit, "verdict": "approve", "note": "", "at": "2026-07-17T21:00:00Z"} for unit in units
        ],
    }


def _seed_auto_repo(tmp_path, monkeypatch, *, stamp="2026-07-17T20:24:44Z"):
    review_out = tmp_path / "rebuild" / "out" / "review"
    review_out.mkdir(parents=True)
    (review_out / "manifest.json").write_text(json.dumps({"generated_at": stamp}))
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", review_out)
    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "verdicts-autosave.json")
    monkeypatch.setattr(ac, "JSTEST_DIR", tmp_path / "rebuild" / "review" / "jstests")
    monkeypatch.setattr(ac, "RUN_M1_GREEN", tmp_path / "rebuild" / "out" / "run-m1-green.json")
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "rebuild" / "out" / "conform-green.json")
    monkeypatch.setattr(ac, "REBUILD_GATE_GREEN", tmp_path / "rebuild" / "out" / "rebuild-gate-green.json")
    monkeypatch.setattr(ac, "CENSUS_RESULT", tmp_path / "rebuild" / "out" / "census-result.json")


def test_dry_run_auto_resolves_the_carry_source(tmp_path, monkeypatch, capsys):
    _seed_auto_repo(tmp_path, monkeypatch)
    (tmp_path / "verdicts-autosave.json").write_text(
        json.dumps(_verdicts_doc("2026-07-17T20:24:44Z", ["u-1", "u-2"]))
    )
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Auto-resolved carry source: verdicts-autosave.json (2 effective verdicts" in out
    assert "stamped for the served surface" in out
    assert str(tmp_path / "verdicts-autosave.json") in out


def test_auto_resolution_refuses_a_mismatched_stamp(tmp_path, monkeypatch, capsys):
    """When no candidate is stamped for the served surface, the cycle stops before any work rather than pairing the newest-stamped file with a snapshot it wasn't recorded against — the mis-carry the qsEt cycle hit."""
    _seed_auto_repo(tmp_path, monkeypatch)
    (tmp_path / "verdicts-carried-old.json").write_text(
        json.dumps(_verdicts_doc("2026-07-10T00:00:00Z", ["u-1"]))
    )
    assert ac.main(["--dry-run"]) == 2
    out = capsys.readouterr().out
    assert "ERROR: the best carry source, verdicts-carried-old.json" in out
    assert "not the served surface" in out
    assert "--no-carry" in out


def test_dry_run_degrades_to_no_carry_when_nothing_carryable(tmp_path, monkeypatch, capsys):
    _seed_auto_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "No carryable verdicts found" in out
    assert "(no carry)" in out


def test_explicit_verdicts_skips_auto_resolution(tmp_path, monkeypatch, capsys):
    _seed_auto_repo(tmp_path, monkeypatch)
    (tmp_path / "verdicts-autosave.json").write_text(
        json.dumps(_verdicts_doc("2026-07-17T20:24:44Z", ["u-1"]))
    )
    assert ac.main(["--dry-run", "--verdicts", "verdicts-mine.json"]) == 0
    out = capsys.readouterr().out
    assert "Auto-resolved" not in out
    assert "verdicts-mine.json" in out


def test_make_test_exempt_classification():
    for path in (
        "rebuild/pipeline/conform.py",
        "rebuild/tools/artifact_cycle.py",
        "glyph_data/runes/qsDay.yaml",
        "doc/glyph-names.md",
        "doc/rebuild-design.md",
        "WHATNEXT.md",
        "FONTLOG.md",
        "tmp/scratch.txt",
        ".claude/settings.json",
        "bench-the-rebuild/kernel-model/model.py",
        "bench-the-rebuild/kernel-model/rust/src/engine.rs",
        "bench-the-rebuild/fixtures/baseline-rows.tsv",
    ):
        assert ac.make_test_exempt(path), path
    for path in (
        "glyph_data/quikscript.yaml",
        "glyph_data/punctuation.yaml",
        "tools/build_font.py",
        "test/test_calt_regressions.py",
        "site/the-manual.html",
        "conftest.py",
        "Makefile",
        "pyproject.toml",
        "uv.lock",
    ):
        assert not ac.make_test_exempt(path), path


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "core.excludesFile", os.devnull], cwd=tmp_path, check=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build_font.py").write_text("print()\n")
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "notes.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("hello\n")
    return tmp_path


def test_closure_files_apply_the_exemptions(tmp_path):
    root = _git_repo(tmp_path)
    assert ac.make_test_closure_files(root) == ["tools/build_font.py"]


def test_closure_files_none_outside_a_git_repo(tmp_path):
    assert ac.make_test_closure_files(tmp_path) is None
    assert ac.make_test_closure_fingerprint(tmp_path) is None


def test_closure_fingerprint_moves_only_with_closure_content(tmp_path):
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    assert first is not None

    (root / "rebuild" / "notes.py").write_text("x = 2\n")
    (root / "README.md").write_text("changed\n")
    assert ac.make_test_closure_fingerprint(root) == first

    (root / "tools" / "build_font.py").write_text("print(2)\n")
    second = ac.make_test_closure_fingerprint(root)
    assert second != first

    (root / "test").mkdir()
    (root / "test" / "test_new.py").write_text("def test(): pass\n")
    assert ac.make_test_closure_fingerprint(root) not in (first, second)


def test_closure_fingerprint_moves_when_a_tracked_file_is_deleted(tmp_path):
    root = _git_repo(tmp_path)
    subprocess.run(["git", "add", "tools/build_font.py"], cwd=root, check=True)
    first = ac.make_test_closure_fingerprint(root)
    (root / "tools" / "build_font.py").unlink()
    assert ac.make_test_closure_fingerprint(root) != first


def test_prior_make_test_fingerprint_reads_the_summary(tmp_path):
    summary = tmp_path / "cycle_summary.json"
    green = tmp_path / "make-test-green.json"
    assert ac.prior_make_test_fingerprint(summary, green) is None
    summary.write_text(json.dumps({"make_test_fingerprint": "abc123"}))
    assert ac.prior_make_test_fingerprint(summary, green) == "abc123"
    summary.write_text(json.dumps({"make_test_fingerprint": None}))
    assert ac.prior_make_test_fingerprint(summary, green) is None
    summary.write_text("not json")
    assert ac.prior_make_test_fingerprint(summary, green) is None


def test_prior_make_test_fingerprint_prefers_the_green_record(tmp_path):
    summary = tmp_path / "cycle_summary.json"
    green = tmp_path / "make-test-green.json"
    summary.write_text(json.dumps({"make_test_fingerprint": "from-summary"}))
    ac.record_make_test_green("from-green", green)
    assert ac.prior_make_test_fingerprint(summary, green) == "from-green"
    record = ac.read_make_test_green(green)
    assert record is not None
    assert record["fingerprint"] == "from-green"
    assert isinstance(record.get("finished_at"), str)
    green.write_text("not json")
    assert ac.prior_make_test_fingerprint(summary, green) == "from-summary"
    green.write_text(json.dumps({"fingerprint": None}))
    assert ac.prior_make_test_fingerprint(summary, green) == "from-summary"


def test_dry_run_plan_skip_make_test():
    plan = _plan(skip_make_test=True, make_test_note="closure unchanged since its last green run")
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:make-test"].argv is None
    assert by_name["gate:make-test"].note == "SKIPPED (closure unchanged since its last green run)"
    assert by_name["gate:rebuild"].argv is not None
    rendered = ac.render_plan(plan)
    assert "gate:make-test not running, so no queueing" in rendered
    assert "Lane t0   [from t=0, background]  : gate:js" in rendered


def test_skip_make_test_frees_the_build_stage_budget():
    plan = _plan(skip_make_test=True, make_test_note="closure unchanged since its last green run")
    assert plan.job_budget == 4
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[-2:] == ["--jobs", "4"]
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", "4"]
    rendered = ac.render_plan(plan)
    assert "--jobs budget        : 4" in rendered
    assert "gate:make-test skipped, so the build stages fan out" in rendered

    gated = _plan(skip_make_test=False)
    assert gated.job_budget == 2
    gated_by_name = {step.name: step for step in gated.steps}
    assert _argv(gated_by_name["run_m1"])[-2:] == ["--jobs", "2"]
    assert "half the cores, sharing the box with gate:make-test's full-width pytest pool" in ac.render_plan(
        gated
    )


def test_summary_payload_carries_the_fingerprint_only_while_green(tmp_path):
    plan = _plan(skip_make_test=False, make_test_fingerprint="fp-1")
    report = ac.CycleReport()

    report.gate_make_test = "green"
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["make_test_fingerprint"] == "fp-1"

    report.gate_make_test = "FAILED (exit 2)"
    payload = ac.cycle_summary_payload(report, ["make test failed"], plan, "failed")
    assert payload["make_test_fingerprint"] is None

    skipped = _plan(
        skip_make_test=True,
        make_test_note="closure unchanged since its last green run",
        make_test_fingerprint="fp-1",
    )
    report = ac.CycleReport()
    report.gate_make_test = "skipped (closure unchanged since its last green run)"
    payload = ac.cycle_summary_payload(report, [], skipped, "ok")
    assert payload["make_test_fingerprint"] == "fp-1"

    gates_off = _plan(skip_gates=True)
    report = ac.CycleReport()
    payload = ac.cycle_summary_payload(report, [], gates_off, "ok")
    assert payload["make_test_fingerprint"] is None


def test_run_cycle_never_spawns_make_test_when_skipped(monkeypatch):
    record = {"make_calls": 0}

    def fake_make(spawn, emit, registry):
        record["make_calls"] += 1
        return _step("gate:make-test", 0)

    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(skip_make_test=True, make_test_note="closure unchanged since its last green run")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["make_calls"] == 0
    assert report.gate_make_test == "skipped (closure unchanged since its last green run)"
    assert report.gate_rebuild == "green"
    assert report.gate_conform == "green"


def test_green_record_roundtrip(tmp_path):
    path = tmp_path / "conform-green.json"
    assert ac.read_green_record(path) is None
    ac.record_green(path, "fp-1")
    record = ac.read_green_record(path)
    assert record is not None
    assert record["fingerprint"] == "fp-1"
    assert record["format"] == "ams-conform-green/1"
    ac.clear_contradicted_green(path, "fp-other")
    assert ac.read_green_record(path) is not None
    ac.clear_contradicted_green(path, None)
    assert ac.read_green_record(path) is not None
    ac.clear_contradicted_green(path, "fp-1")
    assert ac.read_green_record(path) is None


def test_run_m1_skip_fingerprint_moves_with_runes_and_subsets(tmp_path):
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    (tmp_path / "uv.lock").write_text("lock-1")
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("a: 1\n")
    first = ac.run_m1_skip_fingerprint(tmp_path)
    assert first == ac.run_m1_skip_fingerprint(tmp_path)
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("a: 2\n")
    second = ac.run_m1_skip_fingerprint(tmp_path)
    assert second != first
    (tmp_path / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz").write_bytes(b"rows")
    third = ac.run_m1_skip_fingerprint(tmp_path)
    assert third != second
    (tmp_path / "uv.lock").write_text("lock-2")
    assert ac.run_m1_skip_fingerprint(tmp_path) != third


def test_conform_skip_fingerprint_includes_horizon_and_font(tmp_path):
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    base = ac.conform_skip_fingerprint(tmp_path, 5)
    assert ac.conform_skip_fingerprint(tmp_path, 5) == base
    assert ac.conform_skip_fingerprint(tmp_path, 4) != base
    (tmp_path / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(b"OTTO")
    assert ac.conform_skip_fingerprint(tmp_path, 5) != base


def _kernel_key_tree(root: Path) -> None:
    """A scratch repo holding one file of every kind the kernel-differential key hashes: the spec inputs, the kernel's Python half, the gate's own executable, and the crate — plus a target/ tree the key must never see. Built rather than measured against the working tree so the assertions are about the key's coverage rather than about whatever the repo happens to hold, and so they hold before the gate's own module exists."""
    (root / "glyph_data" / "runes").mkdir(parents=True)
    (root / "glyph_data" / "runes" / "qsX.yaml").write_text("rune: qsX\nductus:\n  hapax: |\n    A stroke.\n")
    pipeline = root / "rebuild" / "pipeline"
    pipeline.mkdir(parents=True)
    for name in ac.KERNEL_PIPELINE_SOURCES:
        (pipeline / name).write_text(f"# {name}\n")
    tools = root / "rebuild" / "tools"
    tools.mkdir(parents=True)
    for name in ac.KERNEL_GATE_SOURCES:
        (tools / name).write_text(f"# {name}\n")
    crate = root / "rebuild" / "kernel-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "tests").mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\nname = 'ams-m1-kernel'\n")
    (crate / "Cargo.lock").write_text("# lock 1\n")
    (crate / "src" / "main.rs").write_text("fn main() {}\n")
    (crate / "tests" / "cli.rs").write_text("fn cli() {}\n")


def test_kernel_differential_key_moves_with_either_engines_sources(tmp_path):
    _kernel_key_tree(tmp_path)
    base = ac.kernel_differential_skip_fingerprint(tmp_path)
    assert ac.kernel_differential_skip_fingerprint(tmp_path) == base
    assert ac._digest_lines(ac.kernel_differential_skip_lines(tmp_path)) == base
    (tmp_path / "rebuild" / "kernel-rs" / "src" / "main.rs").write_text("fn main() { }\n")
    moved_rust = ac.kernel_differential_skip_fingerprint(tmp_path)
    assert moved_rust != base
    (tmp_path / "rebuild" / "pipeline" / "table.py").write_text("# table.py, edited\n")
    moved_python = ac.kernel_differential_skip_fingerprint(tmp_path)
    assert moved_python != moved_rust
    (tmp_path / "rebuild" / "tools" / "kernel_gate.py").write_text("# kernel_gate.py, edited\n")
    moved_gate = ac.kernel_differential_skip_fingerprint(tmp_path)
    assert moved_gate != moved_python
    (tmp_path / "rebuild" / "kernel-rs" / "Cargo.lock").write_text("# lock 2\n")
    assert ac.kernel_differential_skip_fingerprint(tmp_path) != moved_gate


def test_kernel_differential_key_moves_with_a_runes_geometry_but_not_its_prose(tmp_path):
    _kernel_key_tree(tmp_path)
    rune = tmp_path / "glyph_data" / "runes" / "qsX.yaml"
    base = ac.kernel_differential_skip_fingerprint(tmp_path)
    rune.write_text("rune: qsX\nductus:\n  hapax: |\n    Quite another stroke.\n")
    assert ac.kernel_differential_skip_fingerprint(tmp_path) == base
    rune.write_text('rune: qsX\nbitmap:\n  - "##"\nductus:\n  hapax: |\n    Quite another stroke.\n')
    assert ac.kernel_differential_skip_fingerprint(tmp_path) != base


def test_kernel_differential_key_never_hashes_the_binary_it_builds(tmp_path):
    """The key says "these sources"; the gate's own cargo build is what makes the binary match them. Hashing target/ would also mean hashing gigabytes of gitignored build cache on every pass."""
    _kernel_key_tree(tmp_path)
    base = ac.kernel_differential_skip_fingerprint(tmp_path)
    target = tmp_path / "rebuild" / "kernel-rs" / "target" / "release"
    target.mkdir(parents=True)
    (target / "ams-m1-kernel").write_bytes(b"\x7fELF")
    (target / "build.rs").write_text("fn built() {}\n")
    assert ac.kernel_differential_skip_fingerprint(tmp_path) == base
    files = ac.kernel_differential_skip_files(tmp_path)
    assert "rebuild/kernel-rs/src/main.rs" in files
    assert "rebuild/kernel-rs/tests/cli.rs" in files
    assert "rebuild/pipeline/table.py" in files
    assert "rebuild/tools/kernel_gate.py" in files
    assert not any(name.startswith("rebuild/kernel-rs/target/") for name in files)


def test_kernel_differential_key_carries_the_toolchain(tmp_path, monkeypatch):
    """The binary under test is sources times compiler: a rustc upgrade moves the key even when no hashed byte does."""
    _kernel_key_tree(tmp_path)
    lines = ac.kernel_differential_skip_lines(tmp_path)
    assert sum(line.startswith("rustc\t") for line in lines) == 1
    base = ac.kernel_differential_skip_fingerprint(tmp_path)
    monkeypatch.setattr(ac, "_rustc_identity", lambda: "another-toolchain")
    assert ac.kernel_differential_skip_fingerprint(tmp_path) != base


def test_a_box_without_rustc_keys_absent_instead_of_raising(monkeypatch):
    """No toolchain is a key value, not a crash: the gate itself is what reds there, carrying the remedy."""

    def absent(*arguments, **rest):
        raise FileNotFoundError("rustc")

    monkeypatch.setattr(ac.subprocess, "run", absent)
    assert ac._rustc_identity() == "absent"


def test_kernel_differential_key_ignores_what_feeds_no_table(tmp_path):
    """Deliberately narrow, by tables_value's reasoning: the compiled font, the oracle's subset tables, and the baselines move no window, so a cycle that only re-extracts them must not re-run the differential."""
    _kernel_key_tree(tmp_path)
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    base = ac.kernel_differential_skip_fingerprint(tmp_path)
    (m1 / "M1.otf").write_bytes(b"OTTO")
    (m1 / "baseline-default.subset.tsv.gz").write_bytes(b"rows")
    (tmp_path / "rebuild" / "out" / "baseline-default.tsv.gz").write_bytes(b"rows")
    assert ac.kernel_differential_skip_fingerprint(tmp_path) == base


def _harness_key_tree(root: Path) -> None:
    """The same scratch repo one gate deeper: the kernel's Python half, the harness gate's own executable together with the harnesses and corpus tools it drives, and the crate — plus a rune file the key must never read and, once a test makes one, a target/ tree it must never see."""
    (root / "glyph_data" / "runes").mkdir(parents=True)
    (root / "glyph_data" / "runes" / "qsX.yaml").write_text('rune: qsX\nbitmap:\n  - "##"\n')
    pipeline = root / "rebuild" / "pipeline"
    pipeline.mkdir(parents=True)
    for name in ac.KERNEL_PIPELINE_SOURCES:
        (pipeline / name).write_text(f"# {name}\n")
    tools = root / "rebuild" / "tools"
    tools.mkdir(parents=True)
    for name in ac.KERNEL_HARNESS_SOURCES:
        (tools / name).write_text(f"# {name}\n")
    crate = root / "rebuild" / "kernel-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "tests").mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\nname = 'ams-m1-kernel'\n")
    (crate / "Cargo.lock").write_text("# lock 1\n")
    (crate / "src" / "main.rs").write_text("fn main() {}\n")
    (crate / "tests" / "cli.rs").write_text("fn cli() {}\n")


def _structure_digest(monkeypatch, digest: str) -> None:
    """The alphabet's structure digest at the seam the key reads it through. `load_default_spec` resolves the live rune directory whatever root the key is handed, so an unstubbed key would answer for the working tree's alphabet rather than for the scratch repo the test built, and pay a spec load per call to do it."""
    from rebuild.pipeline import spec_load, trace_memo

    monkeypatch.setattr(spec_load, "load_default_spec", lambda: None)
    monkeypatch.setattr(trace_memo, "spec_structure_digest", lambda spec: digest)


def test_kernel_harness_key_moves_with_either_engines_sources(tmp_path, monkeypatch):
    _harness_key_tree(tmp_path)
    _structure_digest(monkeypatch, "structure-1")
    base = ac.kernel_harness_skip_fingerprint(tmp_path)
    assert ac.kernel_harness_skip_fingerprint(tmp_path) == base
    assert ac._digest_lines(ac.kernel_harness_skip_lines(tmp_path)) == base
    (tmp_path / "rebuild" / "kernel-rs" / "src" / "main.rs").write_text("fn main() { }\n")
    moved_rust = ac.kernel_harness_skip_fingerprint(tmp_path)
    assert moved_rust != base
    (tmp_path / "rebuild" / "pipeline" / "settle.py").write_text("# settle.py, edited\n")
    moved_python = ac.kernel_harness_skip_fingerprint(tmp_path)
    assert moved_python != moved_rust
    (tmp_path / "rebuild" / "tools" / "kernel_liveness.py").write_text("# kernel_liveness.py, edited\n")
    moved_harness = ac.kernel_harness_skip_fingerprint(tmp_path)
    assert moved_harness != moved_python
    (tmp_path / "rebuild" / "kernel-rs" / "Cargo.lock").write_text("# lock 2\n")
    assert ac.kernel_harness_skip_fingerprint(tmp_path) != moved_harness


def test_kernel_harness_key_moves_with_the_alphabets_structure(tmp_path, monkeypatch):
    """The migration-shaped change this gate exists to catch — a family joining the roster, a ligature sequence, a class membership — reaches the key through the structure digest and nothing else, so the digest moving has to move the key while every other line holds."""
    _harness_key_tree(tmp_path)
    _structure_digest(monkeypatch, "structure-1")
    lines = ac.kernel_harness_skip_lines(tmp_path)
    assert lines[0] == "spec_structure\tstructure-1"
    base = ac.kernel_harness_skip_fingerprint(tmp_path)
    _structure_digest(monkeypatch, "structure-2")
    moved_lines = ac.kernel_harness_skip_lines(tmp_path)
    assert ac.kernel_harness_skip_fingerprint(tmp_path) != base
    assert moved_lines[0] == "spec_structure\tstructure-2"
    assert moved_lines[1:] == lines[1:]


def test_kernel_harness_key_never_hashes_the_rune_ink(tmp_path, monkeypatch):
    """The deliberate blindness that keeps the hour off an ordinary look-edit-look pass. Geometry moves gate:kernel-differential's key on every cycle and this one not at all, so the ink edits between two migrations leave the deep sweep proved and the per-edit proof stays the standing gate's job."""
    _harness_key_tree(tmp_path)
    _structure_digest(monkeypatch, "structure-1")
    base = ac.kernel_harness_skip_fingerprint(tmp_path)
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text('rune: qsX\nbitmap:\n  - "#."\n')
    assert ac.kernel_harness_skip_fingerprint(tmp_path) == base
    assert not any(name.startswith("glyph_data") for name in ac.kernel_harness_skip_files(tmp_path))


def test_kernel_harness_key_never_hashes_the_binary_it_builds(tmp_path, monkeypatch):
    """The key says "these sources"; the gate's own cargo build is what makes the binary the arms ask match them."""
    _harness_key_tree(tmp_path)
    _structure_digest(monkeypatch, "structure-1")
    base = ac.kernel_harness_skip_fingerprint(tmp_path)
    target = tmp_path / "rebuild" / "kernel-rs" / "target" / "release"
    target.mkdir(parents=True)
    (target / "ams-m1-kernel").write_bytes(b"\x7fELF")
    (target / "build.rs").write_text("fn built() {}\n")
    assert ac.kernel_harness_skip_fingerprint(tmp_path) == base
    files = ac.kernel_harness_skip_files(tmp_path)
    assert "rebuild/kernel-rs/src/main.rs" in files
    assert "rebuild/kernel-rs/tests/cli.rs" in files
    assert "rebuild/pipeline/settle.py" in files
    assert "rebuild/tools/kernel_harness_gate.py" in files
    assert "rebuild/tools/kernel_liveness.py" in files
    assert not any(name.startswith("rebuild/kernel-rs/target/") for name in files)


def test_kernel_harness_key_carries_the_toolchain(tmp_path, monkeypatch):
    """The binary the arms ask is sources times compiler, so a rustc upgrade moves the key even when no hashed byte does."""
    _harness_key_tree(tmp_path)
    _structure_digest(monkeypatch, "structure-1")
    lines = ac.kernel_harness_skip_lines(tmp_path)
    assert sum(line.startswith("rustc\t") for line in lines) == 1
    base = ac.kernel_harness_skip_fingerprint(tmp_path)
    monkeypatch.setattr(ac, "_rustc_identity", lambda: "another-toolchain")
    assert ac.kernel_harness_skip_fingerprint(tmp_path) != base


def test_kernel_harness_key_reads_a_box_without_rustc_as_absent(tmp_path, monkeypatch):
    """No toolchain is a key value here too, so a box that cannot compile the crate still gets a key — and a gate that reds with the remedy rather than a key that raises."""

    def absent(*arguments, **rest):
        raise FileNotFoundError("rustc")

    _harness_key_tree(tmp_path)
    _structure_digest(monkeypatch, "structure-1")
    monkeypatch.setattr(ac.subprocess, "run", absent)
    assert "rustc\tabsent" in ac.kernel_harness_skip_lines(tmp_path)


def test_run_m1_skip_files_carry_the_lines_behind_the_fingerprint(tmp_path):
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    (tmp_path / "uv.lock").write_text("lock-1")
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("a: 1\n")
    files = ac.run_m1_skip_files(tmp_path)
    assert "glyph_data/runes/qsX.yaml" in files
    assert "uv.lock" in files
    assert ac._digest_lines(ac.run_m1_skip_lines(tmp_path)) == ac.run_m1_skip_fingerprint(tmp_path)
    conform = ac.conform_skip_files(tmp_path, 5)
    assert conform["horizon"] == "5"
    assert "M1.otf" in conform


def test_record_green_stores_the_files_and_the_reader_returns_them(tmp_path):
    path = tmp_path / "run-m1-green.json"
    ac.record_green(path, "fp-1", files={"glyph_data/runes/qsX.yaml": "d1"})
    record = ac.read_green_record(path)
    assert record is not None
    assert record["fingerprint"] == "fp-1"
    assert record["files"] == {"glyph_data/runes/qsX.yaml": "d1"}


def test_moved_inputs_note_names_changed_new_and_gone():
    record = {"files": {"a.yaml": "1", "b.yaml": "2", "gone.yaml": "3"}}
    note = ac.moved_inputs_note(record, {"a.yaml": "1", "b.yaml": "9", "new.yaml": "4"})
    assert note == "b.yaml (changed), new.yaml (new), gone.yaml (gone)"
    assert ac.moved_inputs_note(None, {"a.yaml": "1"}) is None
    assert ac.moved_inputs_note({"fingerprint": "fp"}, {"a.yaml": "1"}) is None
    assert ac.moved_inputs_note(record, dict(record["files"])) is None
    crowded = {"files": {f"file-{index:02}.yaml": "old" for index in range(12)}}
    note = ac.moved_inputs_note(crowded, {name: "new" for name in crowded["files"]})
    assert note is not None
    assert note.endswith("and 4 more")


def test_m1_artifacts_present(tmp_path):
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    names = [path.name for path in ac.M1_SUMMARY_FILES.values()] + list(ac.M1_ARTIFACT_NAMES)
    names.append("table-digests.json")
    assert not ac.m1_artifacts_present(tmp_path)
    for name in names:
        (m1 / name).write_text("{}")
    assert ac.m1_artifacts_present(tmp_path)
    (m1 / "M1.otf").unlink()
    assert not ac.m1_artifacts_present(tmp_path)


def test_a_lost_digest_record_re_arms_run_m1_rather_than_wedging_the_gate(tmp_path):
    """gate:kernel-differential reds on a missing table-digests.json with the cycle as its remedy, so the presence check must count the record among what a skipped run_m1 leaves behind — otherwise the remedy reproduces the skip and the loop never converges."""
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    names = [path.name for path in ac.M1_SUMMARY_FILES.values()] + list(ac.M1_ARTIFACT_NAMES)
    names.append("table-digests.json")
    for name in names:
        (m1 / name).write_text("{}")
    assert ac.m1_artifacts_present(tmp_path)
    (m1 / "table-digests.json").unlink()
    assert not ac.m1_artifacts_present(tmp_path)


def test_rebuild_gate_closure_scope_and_exemptions(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild" / "evidence").mkdir(parents=True)
    (tmp_path / "rebuild" / "review" / "jstests").mkdir(parents=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / "rebuild" / "NOTES.md").write_text("")
    (tmp_path / "rebuild" / "evidence" / "verdicts-old.json").write_text("{}")
    (tmp_path / "rebuild" / "review" / "jstests" / "x.test.js").write_text("")
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("")
    (tmp_path / "tools" / "outside.py").write_text("")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    files = ac.rebuild_gate_closure_files(tmp_path)
    assert files == [
        "conftest.py",
        "glyph_data/runes/qsX.yaml",
        "pyproject.toml",
        "rebuild/test_x.py",
        "uv.lock",
    ]


def test_rebuild_gate_closure_none_outside_git(tmp_path):
    assert ac.rebuild_gate_closure_files(tmp_path) is None


def test_rebuild_gate_fingerprint_is_prose_blind_for_runes(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    rune = tmp_path / "glyph_data" / "runes" / "qsX.yaml"
    rune.write_text("rune: qsX\nductus:\n  hapax: |\n    A stroke.\n")
    before = ac.rebuild_gate_skip_fingerprint(tmp_path)
    rune.write_text("rune: qsX\nductus:\n  hapax: |\n    A different stroke.\n")
    assert ac.rebuild_gate_skip_fingerprint(tmp_path) == before
    rune.write_text("rune: qsY\nductus:\n  hapax: |\n    A different stroke.\n")
    assert ac.rebuild_gate_skip_fingerprint(tmp_path) != before


def test_surface_build_skippable_matches_manifest(tmp_path):
    from rebuild.pipeline import fingerprint

    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    surface = tmp_path / "rebuild" / "out" / "review"
    surface.mkdir(parents=True)
    stage_a = {"data": "d", "baselines": "b", "pipeline_code": "p"}
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": fingerprint.FORMAT, **stage_a}))
    before_font, junior_font = fingerprint.font_paths(tmp_path)
    expected = {**stage_a, **fingerprint.stage_b(tmp_path, before_font, junior_font)}
    shard = surface / "units-000.json"
    shard.write_text("[]")
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "inputs_fingerprint": expected,
        "classes": [{"id": "c", "shard": "units-000.json"}],
    }
    (surface / "manifest.json").write_text(json.dumps(manifest))
    assert ac.surface_build_skippable(tmp_path, surface)
    shard.unlink()
    assert not ac.surface_build_skippable(tmp_path, surface)
    shard.write_text("[]")
    manifest["inputs_fingerprint"] = {**expected, "data": "changed"}
    (surface / "manifest.json").write_text(json.dumps(manifest))
    assert not ac.surface_build_skippable(tmp_path, surface)


def test_census_skip_fingerprint_moves_with_pins_and_surface(tmp_path):
    surface = tmp_path / "surface"
    surface.mkdir()
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    assert ac.census_skip_fingerprint(tmp_path, surface) is None
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "g", "inputs_fingerprint": {"data": "d"}})
    )
    first = ac.census_skip_fingerprint(tmp_path, surface)
    assert first is not None
    (tmp_path / "rebuild" / "review-census-pins.json").write_text("{}")
    second = ac.census_skip_fingerprint(tmp_path, surface)
    assert second != first
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "g2", "inputs_fingerprint": {"data": "d"}})
    )
    assert ac.census_skip_fingerprint(tmp_path, surface) != second


def test_dry_run_plan_skip_run_m1_surface_and_census():
    plan = _plan(
        skip_run_m1=True,
        run_m1_note="build inputs unchanged since the last green M1 build; --fresh overrides",
        skip_surface=True,
        surface_note="the surface already reflects these inputs byte for byte, stamp included; --fresh overrides",
        skip_census=True,
        census_skip_note="surface, pins, and source inputs unchanged since the last clean check; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv is None
    assert "SKIPPED (build inputs unchanged" in by_name["run_m1"].note
    assert by_name["surface-build"].argv is None
    assert by_name["census"].argv is None
    assert by_name["carry"].argv is not None
    assert by_name["gate:rebuild"].argv is not None


def test_dry_run_plan_skip_rebuild_gate():
    plan = _plan(
        skip_rebuild_gate=True,
        rebuild_gate_note="input closure unchanged since its last green run; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:rebuild"].argv is None
    assert "SKIPPED (input closure unchanged" in by_name["gate:rebuild"].note
    assert by_name["gate:conform"].argv is not None
    rendered = ac.render_plan(plan)
    assert "Lane rebuild                     : SKIPPED" in rendered


def test_dry_run_plan_auto_skip_conform_note():
    plan = _plan(
        skip_conform=True,
        conform_note="font and sweep inputs unchanged since its last green sweep; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert "font and sweep inputs unchanged" in by_name["gate:conform"].note


def test_run_cycle_never_spawns_rebuild_gate_when_skipped(monkeypatch):
    record = {"rebuild_calls": 0}

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        record["rebuild_calls"] += 1
        return ac.RebuildOutcome("green", [], [])

    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(
        skip_rebuild_gate=True,
        rebuild_gate_note="input closure unchanged since its last green run; --fresh overrides",
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["rebuild_calls"] == 0
    assert report.gate_rebuild.startswith("skipped (input closure unchanged")
    assert report.gate_conform == "green"


def test_run_cycle_never_spawns_the_kernel_gate_when_skipped_or_deferred(monkeypatch):
    record = {"kernel_calls": 0}

    def fake_kernel(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        record["kernel_calls"] += 1
        return "green", []

    monkeypatch.setattr(ac, "_gate_kernel_differential_task", fake_kernel)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)

    skipped = _plan(
        skip_kernel_differential=True,
        kernel_differential_note="spec inputs and both engines' sources unchanged",
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(skipped, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["kernel_calls"] == 0
    assert report.gate_kernel_differential.startswith("skipped (spec inputs and both engines' sources")

    deferred = _plan(deferred=frozenset({"kernel-differential"}))
    report = ac.CycleReport()
    rc = ac._run_cycle(deferred, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["kernel_calls"] == 0
    assert report.gate_kernel_differential == f"deferred ({ac.DEFER_NOTE})"


def test_a_run_m1_failure_leaves_the_kernel_gate_not_run(monkeypatch):
    """The differential compares the artifacts run_m1 writes, so a failed build has nothing for it to read — the gate is never submitted, and the summary says so rather than reading as an unexplained skip."""

    def failing_run_m1(report, *, spawn, emit, registry, budget, **_):
        return ac.GateOutcome(False, ["boundary gate failed"], 0, 0)

    monkeypatch.setattr(ac, "_do_run_m1", failing_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    _patch_build_chain(monkeypatch)

    report = ac.CycleReport()
    rc = ac._run_cycle(_plan(), report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 1
    assert report.gate_kernel_differential == "not run (run_m1 gate failed)"


def test_run_cycle_never_spawns_the_kernel_harness_when_skipped_or_deferred(monkeypatch):
    record = {"harness_calls": 0}

    def fake_harness(pool_policy, rebuild_fut, spawn, emit, registry, argv):
        record["harness_calls"] += 1
        return "green", []

    monkeypatch.setattr(ac, "_gate_kernel_harness_task", fake_harness)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)

    skipped = _plan(
        skip_kernel_harness=True,
        kernel_harness_note="alphabet structure and both engines' sources unchanged",
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(skipped, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["harness_calls"] == 0
    assert report.gate_kernel_harness.startswith("skipped (alphabet structure and both engines' sources")

    deferred = _plan(deferred=frozenset({"kernel-harness"}))
    report = ac.CycleReport()
    rc = ac._run_cycle(deferred, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["harness_calls"] == 0
    assert report.gate_kernel_harness == f"deferred ({ac.DEFER_NOTE})"


def test_a_run_m1_failure_leaves_the_kernel_harness_not_run(monkeypatch):
    """The harness is submitted from the build lane, after the census step, so a build that never got that far never submitted it — and the summary says so rather than leaving an hour-long gate reading as an unexplained skip."""

    def failing_run_m1(report, *, spawn, emit, registry, budget, **_):
        return ac.GateOutcome(False, ["boundary gate failed"], 0, 0)

    monkeypatch.setattr(ac, "_do_run_m1", failing_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    _patch_build_chain(monkeypatch)

    report = ac.CycleReport()
    rc = ac._run_cycle(_plan(), report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 1
    assert report.gate_kernel_harness == "not run (run_m1 gate failed)"


ALL_RUN = {
    "rebuild": True,
    "conform": True,
    "kernel-differential": True,
    "kernel-harness": True,
    "make-test": True,
}


def test_deferred_gates_needs_both_the_flag_and_a_refreshing_pass():
    assert ac.deferred_gates(defer=True, refreshing=True, would_run=ALL_RUN) == frozenset(
        {"rebuild", "conform", "kernel-differential", "kernel-harness", "make-test"}
    )
    assert ac.deferred_gates(defer=False, refreshing=True, would_run=ALL_RUN) == frozenset()
    assert ac.deferred_gates(defer=True, refreshing=False, would_run=ALL_RUN) == frozenset()


def test_deferred_gates_never_demotes_a_gate_that_would_not_run():
    would_run = {
        "rebuild": False,
        "conform": False,
        "kernel-differential": False,
        "kernel-harness": False,
        "make-test": True,
    }
    assert ac.deferred_gates(defer=True, refreshing=True, would_run=would_run) == frozenset({"make-test"})
    assert ac.deferred_gates(defer=True, refreshing=True, would_run={}) == frozenset()


def test_dry_run_plan_deferred_gates_replace_their_steps():
    plan = _plan(deferred=frozenset({"rebuild", "conform", "make-test"}))
    by_name = {step.name: step for step in plan.steps}
    for name in ("gate:rebuild", "gate:conform", "gate:make-test"):
        assert by_name[name].argv is None
        assert by_name[name].note == f"DEFERRED ({ac.DEFER_NOTE})"
    assert by_name["gate:js"].argv is not None
    rendered = ac.render_plan(plan)
    assert "deferred to the next pass        : gate:conform, gate:make-test, gate:rebuild" in rendered


def test_deferring_make_test_frees_the_build_stage_budget():
    plan = _plan(deferred=frozenset({"make-test"}))
    assert plan.job_budget == 4
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[-2:] == ["--jobs", "4"]
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", "4"]
    rendered = ac.render_plan(plan)
    assert "gate:make-test deferred, so the build stages fan out" in rendered
    assert "gate:make-test not running, so no queueing" in rendered


def test_a_proved_skip_outranks_deferral_in_the_plan():
    plan = _plan(
        skip_rebuild_gate=True, rebuild_gate_note="closure unchanged", deferred=frozenset({"rebuild"})
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:rebuild"].note == "SKIPPED (closure unchanged)"


def test_a_proved_harness_skip_outranks_deferral_in_the_plan():
    """A deferral is a promise to run the gate on the converging pass; a green record is proof there is nothing left to run, so the hour is never queued up behind a promise it has already discharged."""
    plan = _plan(
        skip_kernel_harness=True,
        kernel_harness_note="alphabet structure unchanged",
        deferred=frozenset({"kernel-harness"}),
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:kernel-harness"].note == "SKIPPED (alphabet structure unchanged)"


def test_run_cycle_never_spawns_a_deferred_gate(monkeypatch):
    calls = {"rebuild": 0, "conform": 0, "make-test": 0}

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        calls["rebuild"] += 1
        return ac.RebuildOutcome("green", [], [])

    def fake_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        calls["conform"] += 1
        return "green", []

    def fake_make(spawn, emit, registry):
        calls["make-test"] += 1
        return _step("gate:make-test", 0)

    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", fake_conform)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)

    plan = _plan(deferred=frozenset({"rebuild", "conform", "make-test"}))
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert calls == {"rebuild": 0, "conform": 0, "make-test": 0}
    assert report.gate_js == "green"
    for status in (report.gate_rebuild, report.gate_conform, report.gate_make_test):
        assert status == f"deferred ({ac.DEFER_NOTE})"


def test_a_deferred_gate_keeps_its_status_when_run_m1_fails(monkeypatch):
    def failing_run_m1(report, *, spawn, emit, registry, budget, **_):
        return ac.GateOutcome(False, ["boundary gate failed"], 0, 0)

    monkeypatch.setattr(ac, "_do_run_m1", failing_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(deferred=frozenset({"rebuild", "conform", "make-test"}))
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 1
    assert report.gate_rebuild == f"deferred ({ac.DEFER_NOTE})"
    assert report.gate_conform == f"deferred ({ac.DEFER_NOTE})"


def test_run_cycle_records_no_green_for_a_deferred_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "REBUILD_GATE_GREEN", tmp_path / "rebuild-gate-green.json")
    monkeypatch.setattr(ac, "KERNEL_DIFFERENTIAL_GREEN", tmp_path / "kernel-differential-green.json")
    monkeypatch.setattr(ac, "KERNEL_HARNESS_GREEN", tmp_path / "kernel-harness-green.json")
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(
        deferred=frozenset({"rebuild", "conform", "kernel-differential", "kernel-harness", "make-test"}),
        record_greens=True,
    )
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) == 0
    assert not (tmp_path / "conform-green.json").exists()
    assert not (tmp_path / "rebuild-gate-green.json").exists()
    assert not (tmp_path / "kernel-differential-green.json").exists()
    assert not (tmp_path / "kernel-harness-green.json").exists()


def test_cycle_summary_payload_marks_deferred_skips():
    report = _green_report()
    report.gate_rebuild = f"deferred ({ac.DEFER_NOTE})"
    report.gate_conform = f"deferred ({ac.DEFER_NOTE})"
    report.gate_make_test = f"deferred ({ac.DEFER_NOTE})"
    plan = _plan(deferred=frozenset({"rebuild", "conform", "make-test"}))
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    for name in ("rebuild", "conform", "make_test"):
        assert payload["gates"][name]["skip"] == "deferred"
        assert payload["gates"][name]["green"] is False
    assert payload["gates"]["js"]["skip"] is None
    assert payload["make_test_fingerprint"] is None
    assert payload["plan"]["deferred"] == ["conform", "make-test", "rebuild"]


def test_cycle_summary_payload_prefers_proved_and_forced_over_deferred():
    report = _green_report()
    plan = _plan(
        skip_rebuild_gate=True,
        skip_conform=True,
        skip_make_test=True,
        deferred=frozenset({"rebuild", "conform", "make-test"}),
    )
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["rebuild"]["skip"] == "proved"
    assert payload["gates"]["make_test"]["skip"] == "proved"
    assert payload["gates"]["conform"]["skip"] == "forced"


def test_cycle_summary_payload_tells_a_proved_kernel_skip_from_a_forced_one():
    """The readiness checker reads only the skip kind, so an auto-skip the green record proved has to be distinguishable from --skip-kernel-differential switching the differential off."""
    report = _green_report()
    report.gate_kernel_differential = "skipped (sources unchanged)"
    proved = ac.cycle_summary_payload(
        report,
        [],
        _plan(skip_kernel_differential=True, kernel_differential_proven=True),
        "ok",
    )
    assert proved["gates"]["kernel_differential"]["skip"] == "proved"
    assert proved["gates"]["kernel_differential"]["green"] is False
    assert proved["plan"]["skip_kernel_differential"] is True
    forced = ac.cycle_summary_payload(report, [], _plan(skip_kernel_differential=True), "ok")
    assert forced["gates"]["kernel_differential"]["skip"] == "forced"
    report.gate_kernel_differential = f"deferred ({ac.DEFER_NOTE})"
    deferred = ac.cycle_summary_payload(report, [], _plan(deferred=frozenset({"kernel-differential"})), "ok")
    assert deferred["gates"]["kernel_differential"]["skip"] == "deferred"
    assert deferred["plan"]["deferred"] == ["kernel-differential"]
    live = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert live["gates"]["kernel_differential"] == {"status": "green", "green": True, "skip": None}


def test_cycle_summary_payload_tells_a_proved_harness_skip_from_a_forced_one():
    """The hour this gate costs makes its auto-skip the common case, so the readiness checker has to be able to tell the green record's proof from --skip-kernel-harness switching the deep sweep off for a pass."""
    report = _green_report()
    report.gate_kernel_harness = "skipped (alphabet structure unchanged)"
    proved = ac.cycle_summary_payload(
        report,
        [],
        _plan(skip_kernel_harness=True, kernel_harness_proven=True),
        "ok",
    )
    assert proved["gates"]["kernel_harness"]["skip"] == "proved"
    assert proved["gates"]["kernel_harness"]["green"] is False
    assert proved["plan"]["skip_kernel_harness"] is True
    forced = ac.cycle_summary_payload(report, [], _plan(skip_kernel_harness=True), "ok")
    assert forced["gates"]["kernel_harness"]["skip"] == "forced"
    report.gate_kernel_harness = f"deferred ({ac.DEFER_NOTE})"
    deferred = ac.cycle_summary_payload(report, [], _plan(deferred=frozenset({"kernel-harness"})), "ok")
    assert deferred["gates"]["kernel_harness"]["skip"] == "deferred"
    assert deferred["plan"]["deferred"] == ["kernel-harness"]
    live = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert live["gates"]["kernel_harness"] == {"status": "green", "green": True, "skip": None}


def test_finish_hands_a_deferred_green_cycle_to_the_next_pass(monkeypatch, capsys):
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    plan = _plan(deferred=frozenset({"conform", "rebuild"}))
    assert ac._finish(_green_report(), [], plan) == 0
    out = capsys.readouterr().out
    assert "Deferred, and so far unverified on this content: gate:conform, gate:rebuild." in out
    assert "run `make review-cycle` again" in out
    assert "NOT READY" in out


def test_finish_says_nothing_about_deferral_when_nothing_was_deferred(monkeypatch, capsys):
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    assert ac._finish(_green_report(), [], _plan()) == 0
    out = capsys.readouterr().out
    assert "Cycle complete." in out
    assert "Deferred" not in out


def test_resolve_snapshot_dir_takes_the_first_free_name(tmp_path):
    assert ac.resolve_snapshot_dir(tmp_path, "abc1234") == tmp_path / "review-pre-abc1234"
    (tmp_path / "review-pre-abc1234").mkdir()
    assert ac.resolve_snapshot_dir(tmp_path, "abc1234") == tmp_path / "review-pre-abc1234-2"
    (tmp_path / "review-pre-abc1234-2").mkdir()
    assert ac.resolve_snapshot_dir(tmp_path, "abc1234") == tmp_path / "review-pre-abc1234-3"
    assert ac.resolve_snapshot_dir(tmp_path, "def5678") == tmp_path / "review-pre-def5678"


def test_build_plan_gives_a_second_pass_at_one_head_its_own_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "JSTEST_DIR", tmp_path / "jstests")
    (tmp_path / "tmp").mkdir()
    first = _plan(snapshot_dir=None).snapshot_dir
    assert first == tmp_path / "tmp" / "review-pre-testid"
    first.mkdir()
    assert _plan(snapshot_dir=None).snapshot_dir == tmp_path / "tmp" / "review-pre-testid-2"


def test_prune_snapshots_collects_the_suffixed_names(tmp_path):
    for name in ("review-pre-abc1234", "review-pre-abc1234-2", "review-pre-abc1234-3"):
        (tmp_path / name).mkdir()
    keep = tmp_path / "review-pre-abc1234-3"
    removed = ac.prune_snapshots(tmp_path, keep)
    assert {path.name for path in removed} == {"review-pre-abc1234", "review-pre-abc1234-2"}
    assert keep.exists()


def _defer_repo(tmp_path, monkeypatch, stamp="2026-07-17T20:24:44Z"):
    _seed_auto_repo(tmp_path, monkeypatch, stamp=stamp)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "verdicts-autosave.json").write_text(json.dumps(_verdicts_doc(stamp, ["u-1"])))
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "key")
    monkeypatch.setattr(ac, "make_test_closure_fingerprint", lambda root=None: None)


def test_main_defers_on_a_refreshing_pass(tmp_path, monkeypatch, capsys):
    _defer_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run", "--defer-gates"]) == 0
    out = capsys.readouterr().out
    assert (
        "Heavy gates deferred to the next pass: gate:conform, gate:kernel-differential, gate:kernel-harness, gate:make-test, gate:rebuild"
        in out
    )
    assert "gate:rebuild: DEFERRED" in out
    assert "gate:kernel-differential: DEFERRED" in out
    assert "gate:kernel-harness: DEFERRED" in out


def test_main_does_not_defer_without_the_flag_or_under_fresh(tmp_path, monkeypatch, capsys):
    _defer_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    assert "deferred" not in capsys.readouterr().out.lower()
    assert ac.main(["--dry-run", "--defer-gates", "--no-defer-gates"]) == 0
    assert "deferred" not in capsys.readouterr().out.lower()
    assert ac.main(["--dry-run", "--defer-gates", "--fresh"]) == 0
    assert "deferred" not in capsys.readouterr().out.lower()


def test_main_never_defers_a_gate_a_flag_already_forces(tmp_path, monkeypatch, capsys):
    _defer_repo(tmp_path, monkeypatch)
    argv = [
        "--dry-run",
        "--defer-gates",
        "--skip-conform",
        "--skip-kernel-differential",
        "--force-make-test",
    ]
    assert ac.main(argv) == 0
    out = capsys.readouterr().out
    assert "Heavy gates deferred to the next pass: gate:kernel-harness, gate:rebuild" in out
    assert "gate:conform: SKIPPED (--skip-conform)" in out
    assert "gate:kernel-differential: SKIPPED (--skip-kernel-differential)" in out
    assert "gate:make-test: make test" in out


def test_main_defers_nothing_once_the_artifacts_have_settled(tmp_path, monkeypatch, capsys):
    """The converged pass: run_m1 and the surface both auto-skip, so there is no artifact work to prefer over verification and the pending gates run."""
    _defer_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "no-match")
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "no-match")
    assert ac.main(["--dry-run", "--defer-gates"]) == 0
    out = capsys.readouterr().out
    assert "Heavy gates deferred" not in out
    assert "gate:rebuild: uv run pytest" in out
    assert "gate:conform: uv run python -m rebuild.pipeline.run_m1 --conform-only" in out
    assert "gate:kernel-differential: uv run python -m rebuild.tools.kernel_gate" in out
    assert "gate:kernel-harness: uv run python -m rebuild.tools.kernel_harness_gate" in out


def test_main_auto_skips_the_kernel_gate_on_a_matching_green(tmp_path, monkeypatch, capsys):
    """Same discipline as gate:conform's auto-skip, and nested under run_m1's for the same reason: the skip is claimed only on a pass that rebuilds nothing the differential would read."""
    _defer_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "no-match")
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "no-match")
    monkeypatch.setattr(ac, "kernel_differential_skip_fingerprint", lambda root=None: "kfp")

    assert ac.main(["--dry-run"]) == 0
    assert "gate:kernel-differential: uv run python -m rebuild.tools.kernel_gate" in capsys.readouterr().out

    ac.record_green(ac.KERNEL_DIFFERENTIAL_GREEN, "moved")
    assert ac.main(["--dry-run"]) == 0
    assert "gate:kernel-differential auto-skipped" not in capsys.readouterr().out

    ac.record_green(ac.KERNEL_DIFFERENTIAL_GREEN, "kfp")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "gate:kernel-differential auto-skipped: spec inputs and both engines' sources unchanged" in out
    assert "gate:kernel-differential: SKIPPED (spec inputs and both engines' sources unchanged" in out
    assert ac.main(["--dry-run", "--fresh"]) == 0
    assert "gate:kernel-differential auto-skipped" not in capsys.readouterr().out


def test_main_auto_skips_the_kernel_harness_on_a_matching_green(tmp_path, monkeypatch, capsys):
    """The auto-skip that makes an hour-long gate affordable: nested under run_m1's like its siblings, and claimed on a key one grain coarser, so the ordinary rune edit between two migrations never re-arms the sweep."""
    _defer_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "no-match")
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "no-match")
    monkeypatch.setattr(ac, "kernel_harness_skip_fingerprint", lambda root=None: "hfp")

    assert ac.main(["--dry-run"]) == 0
    assert (
        "gate:kernel-harness: uv run python -m rebuild.tools.kernel_harness_gate" in capsys.readouterr().out
    )

    ac.record_green(ac.KERNEL_HARNESS_GREEN, "moved")
    assert ac.main(["--dry-run"]) == 0
    assert "gate:kernel-harness auto-skipped" not in capsys.readouterr().out

    ac.record_green(ac.KERNEL_HARNESS_GREEN, "hfp")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert (
        "gate:kernel-harness auto-skipped: alphabet structure and both engines' sources unchanged since its last green harness run; --fresh overrides"
        in out
    )
    assert "gate:kernel-harness: SKIPPED (alphabet structure and both engines' sources unchanged" in out
    assert ac.main(["--dry-run", "--fresh"]) == 0
    assert "gate:kernel-harness auto-skipped" not in capsys.readouterr().out


def test_main_skips_the_census_on_a_recorded_outcome_stale_included(tmp_path, monkeypatch, capsys):
    _defer_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "no-match")
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "cen-key")
    ac.record_census_result(
        ac.CENSUS_RESULT, "cen-key", "stale", ["ink.machine_total: pinned 1 != computed 2"]
    )
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "census auto-skipped: surface, pins, and source inputs unchanged since the last stale check" in out
    assert "census: SKIPPED (surface, pins, and source inputs unchanged since the last stale check" in out
    assert f"gate:rebuild: DEFERRED ({ac.STALE_CENSUS_DEFER_NOTE})" in out
    ac.record_census_result(ac.CENSUS_RESULT, "cen-key", "clean", [])
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "since the last clean check" in out
    assert "gate:rebuild: uv run pytest" in out
    ac.record_census_result(ac.CENSUS_RESULT, "moved-key", "stale", [])
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "census auto-skipped" not in out
    assert "gate:rebuild: uv run pytest" in out


def test_main_never_defers_a_rehearsal(tmp_path, monkeypatch, capsys):
    """A rehearsal writes its surface elsewhere, so its surface build is unskippable and every pass would look refreshing — deferring would never converge."""
    _defer_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "no-match")
    assert ac.main(["--dry-run", "--defer-gates", "--review-out", str(tmp_path / "rehearse")]) == 0
    out = capsys.readouterr().out
    assert "Heavy gates deferred" not in out
    assert "gate:rebuild: uv run pytest" in out


def test_unfinished_cycle_snapshot_is_only_claimed_from_a_red_summary(tmp_path):
    snapshot = tmp_path / "review-pre-abc1234"
    snapshot.mkdir()
    summary = tmp_path / "cycle_summary.json"
    assert ac.unfinished_cycle_snapshot(summary) is None
    for exit_kind in ("interrupted", "failed"):
        summary.write_text(json.dumps({"exit": exit_kind, "snapshot_dir": str(snapshot)}))
        assert ac.unfinished_cycle_snapshot(summary) == snapshot
    summary.write_text(json.dumps({"exit": "ok", "snapshot_dir": str(snapshot)}))
    assert ac.unfinished_cycle_snapshot(summary) is None
    summary.write_text(json.dumps({"exit": "failed", "snapshot_dir": str(tmp_path / "gone")}))
    assert ac.unfinished_cycle_snapshot(summary) is None


def test_retention_spares_the_snapshot_of_a_cycle_that_never_finished(tmp_path):
    for name in ("review-pre-abc1234", "review-pre-abc1234-2", "review-pre-old"):
        (tmp_path / name).mkdir()
    keep = tmp_path / "review-pre-abc1234-2"
    preserve = tmp_path / "review-pre-abc1234"
    removed = ac.prune_snapshots(tmp_path, keep, preserve)
    assert {path.name for path in removed} == {"review-pre-old"}
    assert keep.exists() and preserve.exists()


def test_do_run_m1_skip_reads_recorded_summaries(monkeypatch, tmp_path):
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    files["pipeline"].write_text(json.dumps({"defect_errors": []}))
    files["boundary"].write_text(json.dumps({"pass": True}))
    files["manual_pins"].write_text(json.dumps({"pass": True}))
    files["oracle"].write_text(json.dumps({"unmatched": 7, "multi_matched": 0}))

    def no_spawn(*a, **k):
        raise AssertionError("skip path must not spawn")

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=no_spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        budget=1,
        skip=True,
        skip_note="test skip",
    )
    assert gate is not None and gate.ok
    assert report.unmatched == 7
    assert files["pipeline"].exists()


def test_do_run_m1_records_green_only_when_fingerprint_stable(monkeypatch, tmp_path):
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")

    def write_summaries(*a, **k):
        files["pipeline"].write_text(json.dumps({"defect_errors": []}))
        files["boundary"].write_text(json.dumps({"pass": True}))
        files["manual_pins"].write_text(json.dumps({"pass": True}))
        files["oracle"].write_text(json.dumps({"unmatched": 0, "multi_matched": 0}))
        return _step("run_m1", 0)

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=write_summaries,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        budget=1,
        record=True,
        fingerprint="fp-live",
    )
    assert gate is not None and gate.ok
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "fp-live"

    green.unlink()
    gate = ac._do_run_m1(
        report,
        spawn=write_summaries,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        budget=1,
        record=True,
        fingerprint="fp-from-before-a-mid-run-edit",
    )
    assert gate is not None and gate.ok
    assert ac.read_green_record(green) is None


def test_do_run_m1_red_deletes_matching_green(monkeypatch, tmp_path):
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", green)
    ac.record_green(green, "fp-1")

    def write_red(*a, **k):
        files["pipeline"].write_text(json.dumps({"defect_errors": ["boom"]}))
        files["boundary"].write_text(json.dumps({"pass": True}))
        files["manual_pins"].write_text(json.dumps({"pass": True}))
        files["oracle"].write_text(json.dumps({"unmatched": 0, "multi_matched": 0}))
        return _step("run_m1", 0)

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=write_red,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        budget=1,
        record=True,
        fingerprint="fp-1",
    )
    assert gate is not None and not gate.ok
    assert ac.read_green_record(green) is None

    ac.record_green(green, "fp-1")

    def no_spawn(*a, **k):
        raise AssertionError("skip path must not spawn")

    gate = ac._do_run_m1(
        report,
        spawn=no_spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        budget=1,
        skip=True,
        skip_note="test",
        record=True,
        fingerprint="fp-1",
    )
    assert gate is not None and not gate.ok
    assert ac.read_green_record(green) is None


def test_do_surface_build_skip_reads_manifest_totals(monkeypatch, tmp_path):
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(
        json.dumps({"totals": {"units": 5, "rows": 9, "batches": 2, "echo_groups": 3}})
    )
    monkeypatch.setattr(ac, "REVIEW_OUT", surface)

    def no_spawn(*a, **k):
        raise AssertionError("skip path must not spawn")

    report = ac.CycleReport()
    ok = ac._do_surface_build(
        report,
        spawn=no_spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=None,
        budget=1,
        skip=True,
        skip_note="test",
    )
    assert ok
    assert (report.surface_units, report.surface_rows, report.surface_batches, report.echo_groups) == (
        5,
        9,
        2,
        3,
    )


def test_record_gate_greens_records_refuses_and_clears(monkeypatch, tmp_path):
    conform_green = tmp_path / "conform-green.json"
    rebuild_green = tmp_path / "rebuild-gate-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", conform_green)
    monkeypatch.setattr(ac, "REBUILD_GATE_GREEN", rebuild_green)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "rfp")
    plan = _plan()
    report = ac.CycleReport()
    report.gate_conform = "green"
    report.gate_rebuild = "green (4 documented baseline)"
    report.rebuild_recordable = True
    ac._record_gate_greens(report, plan, {"conform": "cfp", "rebuild": "rfp"}, ac._Emitter())
    conform_record = ac.read_green_record(conform_green)
    rebuild_record = ac.read_green_record(rebuild_green)
    assert conform_record is not None
    assert rebuild_record is not None
    assert conform_record["fingerprint"] == "cfp"
    assert rebuild_record["fingerprint"] == "rfp"

    conform_green.unlink()
    rebuild_green.unlink()
    ac._record_gate_greens(report, plan, {"conform": "moved", "rebuild": "moved-too"}, ac._Emitter())
    assert ac.read_green_record(conform_green) is None
    assert ac.read_green_record(rebuild_green) is None

    report.gate_rebuild = "green (1 stale census pins? (re-run with --update-pins))"
    report.rebuild_recordable = False
    ac._record_gate_greens(report, plan, {"rebuild": "rfp"}, ac._Emitter())
    assert ac.read_green_record(rebuild_green) is None

    ac.record_green(conform_green, "cfp")
    report.gate_conform = "FAILED"
    ac._record_gate_greens(report, plan, {"conform": "cfp"}, ac._Emitter())
    assert ac.read_green_record(conform_green) is None


def test_record_gate_greens_records_refuses_and_clears_the_kernel_differential(monkeypatch, tmp_path):
    green = tmp_path / "kernel-differential-green.json"
    monkeypatch.setattr(ac, "KERNEL_DIFFERENTIAL_GREEN", green)
    monkeypatch.setattr(ac, "kernel_differential_skip_fingerprint", lambda root=None: "kfp")
    monkeypatch.setattr(
        ac, "kernel_differential_skip_files", lambda root=None: {"rebuild/kernel-rs/src/main.rs": "d1"}
    )
    plan = _plan()
    report = ac.CycleReport()
    report.gate_kernel_differential = "green"
    ac._record_gate_greens(report, plan, {"kernel-differential": "kfp"}, ac._Emitter())
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "kfp"
    assert record["files"] == {"rebuild/kernel-rs/src/main.rs": "d1"}

    green.unlink()
    ac._record_gate_greens(report, plan, {"kernel-differential": "moved"}, ac._Emitter())
    assert ac.read_green_record(green) is None

    ac.record_green(green, "kfp")
    report.gate_kernel_differential = "FAILED"
    ac._record_gate_greens(report, plan, {"kernel-differential": "kfp"}, ac._Emitter())
    assert ac.read_green_record(green) is None


def test_record_gate_greens_records_refuses_and_clears_the_kernel_harness(monkeypatch, tmp_path):
    green = tmp_path / "kernel-harness-green.json"
    monkeypatch.setattr(ac, "KERNEL_HARNESS_GREEN", green)
    monkeypatch.setattr(ac, "kernel_harness_skip_fingerprint", lambda root=None: "hfp")
    monkeypatch.setattr(
        ac, "kernel_harness_skip_files", lambda root=None: {"rebuild/tools/kernel_liveness.py": "d1"}
    )
    plan = _plan()
    report = ac.CycleReport()
    report.gate_kernel_harness = "green"
    ac._record_gate_greens(report, plan, {"kernel-harness": "hfp"}, ac._Emitter())
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "hfp"
    assert record["files"] == {"rebuild/tools/kernel_liveness.py": "d1"}

    green.unlink()
    ac._record_gate_greens(report, plan, {"kernel-harness": "moved"}, ac._Emitter())
    assert ac.read_green_record(green) is None

    ac.record_green(green, "hfp")
    report.gate_kernel_harness = "FAILED"
    ac._record_gate_greens(report, plan, {"kernel-harness": "hfp"}, ac._Emitter())
    assert ac.read_green_record(green) is None


def test_classify_rebuild_recordable_only_when_unannotated():
    clean = ac.classify_rebuild_output("", 0, update_pins=False)
    assert clean.recordable
    baseline_ids = "\n".join(f"FAILED {test_id}" for test_id in sorted(ac.BASELINE_REBUILD_FAILURES))
    documented = ac.classify_rebuild_output(baseline_ids, 1, update_pins=False)
    assert documented.status.startswith("green")
    assert documented.recordable
    hinted = ac.classify_rebuild_output("FAILED rebuild/test_review_audit.py::test_x", 1, update_pins=False)
    assert hinted.status.startswith("green")
    assert not hinted.recordable
    hard = ac.classify_rebuild_output("FAILED rebuild/test_settle.py::test_x", 1, update_pins=False)
    assert not hard.recordable


def _census_failing_rebuild(
    pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins
):
    return ac.classify_rebuild_output(
        "FAILED rebuild/test_review_build.py::test_totals_pinned", 1, update_pins=update_pins
    )


def test_update_pins_cycle_keeps_census_failures_hard(monkeypatch, capsys):
    """On an --update-pins pass the gate is submitted only after the census step has rewritten the pins, so a census-module failure is judged against the pins the suite actually read — a genuine failure that turns the cycle red."""

    def census_update(*, spawn, emit, registry, update_pins, surface, **_):
        return "updated (diff shown above — review every moved number)"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _census_failing_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_update)

    plan = _plan(update_pins=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_rebuild == "FAILED (1 unexplained)"
    assert "rebuild suite: 1 unexplained failure(s)" in capsys.readouterr().out


def test_update_pins_cycle_still_submits_the_gate_when_the_update_failed(monkeypatch, capsys):
    """A failed census --update is not a stale verdict: the gate still runs against whatever pins are on disk, and its census failures stay hard, so the cycle turns red instead of deferring past a broken tracked pins file."""

    def census_dies(*, spawn, emit, registry, update_pins, surface, **_):
        return "update FAILED"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _census_failing_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_dies)

    plan = _plan(update_pins=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.census_status == "update FAILED"
    assert report.rebuild_stale_deferred is False
    assert report.gate_rebuild == "FAILED (1 unexplained)"
    assert "census-pinned failure forgiven" not in capsys.readouterr().out


def test_update_pins_census_update_completes_before_the_rebuild_gate_spawns(monkeypatch):
    record = {}

    def census_update(*, spawn, emit, registry, update_pins, surface, **_):
        record["census_finish"] = time.monotonic()
        return "updated (no change)"

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:rebuild":
            record["rebuild_start"] = time.monotonic()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_update)

    plan = _plan(update_pins=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)

    assert rc == 0
    assert record["rebuild_start"] >= record["census_finish"]
    assert report.gate_rebuild == "green"


def test_update_pins_cycle_records_rebuild_green_after_the_refresh(monkeypatch, tmp_path):
    """The payoff of census-before-gate: the rebuild key is snapshotted at submission time, after the pins were rewritten, so a recordable suite outcome on the --update-pins pass itself lands in rebuild-gate-green.json instead of demanding one more full run."""
    rebuild_green = tmp_path / "rebuild-gate-green.json"
    conform_green = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    monkeypatch.setattr(ac, "REBUILD_GATE_GREEN", rebuild_green)
    monkeypatch.setattr(ac, "CONFORM_GREEN", conform_green)
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "rfp")
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")

    def recordable_rebuild(
        pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins
    ):
        return ac.RebuildOutcome("green", [], [], recordable=True)

    def census_update(*, spawn, emit, registry, update_pins, surface, **_):
        return "updated (no change)"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", recordable_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_update)

    plan = _plan(update_pins=True, record_greens=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert report.rebuild_recordable is True
    record = ac.read_green_record(rebuild_green)
    assert record is not None
    assert record["fingerprint"] == "rfp"


def test_a_stale_census_defers_the_rebuild_gate(monkeypatch, tmp_path, capsys):
    calls = {"rebuild": 0}

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        calls["rebuild"] += 1
        return ac.RebuildOutcome("green", [], [])

    def census_stale(*, spawn, emit, registry, update_pins, surface, **_):
        return "STALE (informational — re-run with --update-pins or edit by hand)"

    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    monkeypatch.setattr(ac, "REBUILD_GATE_GREEN", tmp_path / "rebuild-gate-green.json")
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "rfp")
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_stale)

    plan = _plan(record_greens=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert calls["rebuild"] == 0
    assert report.rebuild_stale_deferred is True
    assert report.gate_rebuild == f"deferred ({ac.STALE_CENSUS_DEFER_NOTE})"
    assert not (tmp_path / "rebuild-gate-green.json").exists()
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["gates"]["rebuild"]["skip"] == "deferred"
    assert summary["gates"]["rebuild"]["green"] is False
    out = capsys.readouterr().out
    assert f"gate:rebuild deferred: {ac.STALE_CENSUS_DEFER_NOTE}" in out
    assert "Cycle complete — but gate:rebuild was deferred" in out


def test_a_stale_census_never_defers_update_pins_rehearsal_or_no_defer(monkeypatch, tmp_path):
    calls = {"rebuild": 0}

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        calls["rebuild"] += 1
        return ac.RebuildOutcome("green", [], [])

    def census_stale(*, spawn, emit, registry, update_pins, surface, **_):
        return "STALE (informational — re-run with --update-pins or edit by hand)"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_stale)

    plans = [
        _plan(update_pins=True),
        _plan(review_out=tmp_path / "rehearse"),
        _plan(defer_rebuild_on_stale_census=False),
    ]
    for plan in plans:
        report = ac.CycleReport()
        rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
        assert rc == 0
        assert report.rebuild_stale_deferred is False
        assert report.gate_rebuild == "green"
    assert calls["rebuild"] == len(plans)


def test_surface_build_failure_leaves_the_rebuild_gate_not_run(monkeypatch, capsys):
    calls = {"rebuild": 0}

    def fake_rebuild(pool_policy, kernel_fut, conform_fut, make_fut, spawn, emit, registry, update_pins):
        calls["rebuild"] += 1
        return ac.RebuildOutcome("green", [], [])

    def failing_surface(report, *, spawn, emit, registry, review_out, budget, **_):
        return False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", failing_surface)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert calls["rebuild"] == 0
    assert report.gate_rebuild == "not run (surface build failed)"
    assert "surface rebuild failed" in capsys.readouterr().out


def test_stale_census_known_only_on_the_replay_path():
    stale_replay = {"fingerprint": "cen-fp", "status": "stale", "mismatches": []}
    clean_replay = {"fingerprint": "cen-fp", "status": "clean", "mismatches": []}
    assert ac.stale_census_known(_plan(skip_census=True, census_skip_note="x", census_replay=stale_replay))
    assert not ac.stale_census_known(
        _plan(skip_census=True, census_skip_note="x", census_replay=clean_replay)
    )
    assert not ac.stale_census_known(
        _plan(skip_census=True, census_skip_note="x", census_replay=stale_replay, update_pins=True)
    )
    assert not ac.stale_census_known(
        _plan(
            skip_census=True,
            census_skip_note="x",
            census_replay=stale_replay,
            defer_rebuild_on_stale_census=False,
        )
    )
    assert not ac.stale_census_known(_plan())


def test_dry_run_plan_defers_the_rebuild_gate_on_a_recorded_stale_census():
    plan = _plan(
        skip_census=True,
        census_skip_note="surface, pins, and source inputs unchanged since the last stale check; --fresh overrides",
        census_replay={
            "fingerprint": "cen-fp",
            "status": "stale",
            "mismatches": ["ink.machine_total: pinned 1 != computed 2"],
        },
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:rebuild"].argv is None
    assert by_name["gate:rebuild"].note == f"DEFERRED ({ac.STALE_CENSUS_DEFER_NOTE})"
    assert by_name["gate:conform"].argv is not None
    rendered = ac.render_plan(plan)
    assert f"Lane rebuild                     : DEFERRED ({ac.STALE_CENSUS_DEFER_NOTE})" in rendered


def test_cycle_summary_payload_marks_a_stale_deferred_rebuild_deferred():
    report = _green_report()
    report.gate_rebuild = f"deferred ({ac.STALE_CENSUS_DEFER_NOTE})"
    report.rebuild_stale_deferred = True
    payload = ac.cycle_summary_payload(report, [], _plan(), "ok")
    assert payload["gates"]["rebuild"]["skip"] == "deferred"
    assert payload["gates"]["rebuild"]["green"] is False
    assert payload["plan"]["deferred"] == []


def test_finish_points_a_stale_deferred_cycle_at_update_pins(monkeypatch, capsys):
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    report = _green_report()
    report.gate_rebuild = f"deferred ({ac.STALE_CENSUS_DEFER_NOTE})"
    report.rebuild_stale_deferred = True
    assert ac._finish(report, [], _plan()) == 0
    out = capsys.readouterr().out
    assert "Cycle complete — but gate:rebuild was deferred" in out
    assert "make review-cycle ARGS='--update-pins'" in out
    assert "NOT READY" in out


def test_do_census_records_clean_and_stale_outcomes(monkeypatch, tmp_path):
    result_path = tmp_path / "census-result.json"
    monkeypatch.setattr(ac, "CENSUS_RESULT", result_path)
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "cen-fp")
    status = ac._do_census(
        spawn=lambda *a, **k: _step("census", 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        update_pins=False,
        surface=tmp_path,
        record=True,
    )
    assert status == "clean"
    record = ac.read_census_result(result_path)
    assert record is not None
    assert (record["fingerprint"], record["status"], record["mismatches"]) == ("cen-fp", "clean", [])
    stale_stderr = "\n".join(
        [
            "census pins are stale:",
            "  ink.machine_total: pinned 1 != computed 2",
            "  ink.non_identical: pinned 3 != computed 4",
            "Re-baseline with: uv run python -m rebuild.review.census --update",
        ]
    )
    status = ac._do_census(
        spawn=lambda *a, **k: _step("census", 1, stderr=stale_stderr),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        update_pins=False,
        surface=tmp_path,
        record=True,
    )
    assert status.startswith("STALE")
    record = ac.read_census_result(result_path)
    assert record is not None
    assert (record["fingerprint"], record["status"]) == ("cen-fp", "stale")
    assert record["mismatches"] == [
        "ink.machine_total: pinned 1 != computed 2",
        "ink.non_identical: pinned 3 != computed 4",
    ]


def test_do_census_never_records_a_verdictless_failure(monkeypatch, tmp_path):
    """A nonzero check without the stale header — a crash, a missing pins file — has no replayable verdict: it must record nothing, and a prior record its key contradicts must go, so no later cycle can skip on it."""
    result_path = tmp_path / "census-result.json"
    monkeypatch.setattr(ac, "CENSUS_RESULT", result_path)
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "cen-fp")
    ac.record_census_result(result_path, "cen-fp", "clean", [])
    status = ac._do_census(
        spawn=lambda *a, **k: _step("census", 1, stderr="Traceback (most recent call last):\n  boom"),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        update_pins=False,
        surface=tmp_path,
        record=True,
    )
    assert status.startswith("STALE")
    assert ac.read_census_result(result_path) is None


def test_read_census_result_rejects_a_statusless_record(tmp_path):
    path = tmp_path / "census-result.json"
    ac.record_green(path, "cen-fp")
    assert ac.read_green_record(path) is not None
    assert ac.read_census_result(path) is None


def test_census_stale_stderr_matches_the_cycle_parser(monkeypatch, tmp_path, capsys):
    """The parse contract with rebuild.review.census, exercised against its real --check output: if the stale report's wording drifts, this fails instead of every stale check silently degrading to a re-run per pass."""
    from rebuild.review import census

    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"audit": {"row_count": 1}}))
    monkeypatch.setattr(census, "PINS_PATH", pins)
    monkeypatch.setattr(census, "compute_pins", lambda surface: {"audit": {"row_count": 2}})
    assert census.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert ac.census_mismatch_lines(err) == ["audit.row_count: pinned 1 != computed 2"]


def test_run_cycle_replays_a_recorded_stale_census(monkeypatch, capsys):
    def census_must_not_run(**_):
        raise AssertionError("skip path must not run the census")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_must_not_run)

    plan = _plan(
        skip_census=True,
        census_skip_note="surface, pins, and source inputs unchanged since the last stale check; --fresh overrides",
        census_replay={
            "fingerprint": "cen-fp",
            "status": "stale",
            "mismatches": ["ink.machine_total: pinned 1 != computed 2"],
        },
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert report.census_status.startswith("STALE (recorded outcome replayed")
    assert report.rebuild_stale_deferred is True
    assert report.gate_rebuild == f"deferred ({ac.STALE_CENSUS_DEFER_NOTE})"
    out = capsys.readouterr().out
    assert "census pins are stale (recorded outcome replayed" in out
    assert "  ink.machine_total: pinned 1 != computed 2" in out


def test_run_cycle_reads_a_replayed_clean_outcome_as_an_ordinary_skip(monkeypatch):
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(
        skip_census=True,
        census_skip_note="surface, pins, and source inputs unchanged since the last clean check; --fresh overrides",
        census_replay={"fingerprint": "cen-fp", "status": "clean", "mismatches": []},
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert report.census_status == f"skipped ({plan.census_skip_note})"


def test_dry_run_plan_defers_the_census():
    plan = _plan(defer_census=True)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["census"].argv is None
    assert by_name["census"].note == f"DEFERRED ({ac.DEFER_NOTE})"
    assert by_name["complaints"].argv is not None
    assert "deferred to the next pass        : census" in ac.render_plan(plan)


def test_run_cycle_never_spawns_a_deferred_census(monkeypatch):
    def census_must_not_run(**_):
        raise AssertionError("a deferred census must not run")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_must_not_run)

    plan = _plan(defer_census=True, deferred=frozenset({"rebuild"}))
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert report.census_status == f"deferred ({ac.DEFER_NOTE})"


def test_main_defers_the_census_only_on_a_refreshing_pass_without_update_pins(tmp_path, monkeypatch, capsys):
    """The census defers on exactly the passes the gates do — and never on an --update-pins pass, whose whole point is to refresh the pins the deferral would leave stale."""
    _defer_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run", "--defer-gates"]) == 0
    assert "Census deferred to the next pass" in capsys.readouterr().out
    assert ac.main(["--dry-run", "--defer-gates", "--update-pins"]) == 0
    out = capsys.readouterr().out
    assert "Census deferred" not in out
    assert "census: uv run python -m rebuild.review.census --update" in out
    assert ac.main(["--dry-run"]) == 0
    assert "Census deferred" not in capsys.readouterr().out


def test_main_never_leaves_the_rebuild_gate_live_beside_a_deferred_census(tmp_path, monkeypatch, capsys):
    """What makes the census safe to defer: nothing in the pass reads it. The one step whose scheduling depends on it — gate:rebuild, submitted only once the census lands a verdict, and deferred outright when that verdict is STALE — is deferred by the very same condition, so a live suite run can never sit beside a census that never ran."""
    _defer_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run", "--defer-gates"]) == 0
    out = capsys.readouterr().out
    assert f"census: DEFERRED ({ac.DEFER_NOTE})" in out
    assert "gate:rebuild: uv run pytest" not in out


def test_plumbing_skip_fingerprint_moves_with_every_input(tmp_path):
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    master = tmp_path / "verdicts-autosave.json"
    master.write_text("{}")
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")

    base = ac.plumbing_skip_fingerprint(tmp_path, surface, master)
    assert base is not None
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) == base
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, None) is None

    master.write_text('{"verdicts": []}')
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != base

    master.write_text("{}")
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: [{}]\n")
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != base

    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-18T00:00:00Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != base

    (surface / "manifest.json").write_text(json.dumps({"generated_at": "2026-07-17T20:24:44Z"}))
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) is None


def test_plumbing_skip_fingerprint_covers_the_chains_own_code(tmp_path):
    """Every other stage's key folds in its own executable; this chain's lives in rebuild/tools/, which no other fingerprint reads. Without it a fix to a fill's matcher would be skipped as already proven and silently never run."""
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    master = tmp_path / "verdicts-autosave.json"
    master.write_text("{}")
    tools = tmp_path / "rebuild" / "tools"
    tools.mkdir(parents=True)
    (tmp_path / "rebuild" / "review").mkdir()
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")
    for name in ("echo_verdicts.py", "standing_verdicts.py", "carry_verdicts.py"):
        (tools / name).write_text("x = 1\n")
    (tmp_path / "rebuild" / "review" / "serve.py").write_text("y = 1\n")

    base = ac.plumbing_skip_fingerprint(tmp_path, surface, master)
    assert base is not None
    for edited in (tools / "echo_verdicts.py", tools / "standing_verdicts.py", tools / "carry_verdicts.py"):
        original = edited.read_text()
        edited.write_text("x = 2\n")
        assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != base, edited.name
        edited.write_text(original)
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) == base

    (tmp_path / "rebuild" / "review" / "serve.py").write_text("y = 2\n")
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != base


def test_plumbing_skip_fingerprint_sees_a_master_that_is_not_the_autosave(tmp_path):
    """The one input the autosave's hash cannot see: an export at the repo root that outranks the store in the auto-resolution and carries verdicts it has never held."""
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    (tmp_path / "verdicts-autosave.json").write_text("{}")
    export = tmp_path / "verdicts-export.json"
    export.write_text('{"verdicts": [1]}')
    before = ac.plumbing_skip_fingerprint(tmp_path, surface, export)
    export.write_text('{"verdicts": [1, 2]}')
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, export) != before


def test_dry_run_plan_skip_plumbing_replaces_the_whole_chain():
    plan = _plan(skip_plumbing=True, plumbing_note=ac.PLUMBING_SKIP_NOTE)
    assert plan.carry_out is None
    by_name = {step.name: step for step in plan.steps}
    for name in ("carry", "merge", "echo-fill", "echo-merge", "standing-fill", "standing-merge"):
        assert by_name[name].argv is None
        assert by_name[name].note == f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})"
    assert by_name["snapshot"].argv is None
    assert by_name["snapshot"].note.startswith(f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})")
    assert by_name["complaints"].argv is None
    assert by_name["complaints"].note == f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})"
    assert by_name["census"].argv is not None


def test_run_cycle_never_spawns_the_plumbing_when_skipped(monkeypatch, tmp_path):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the plumbing skip path must spawn nothing")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    for name in ("_do_carry", "_do_merge", "_do_echo_fill", "_do_standing_fill", "_do_complaints"):
        monkeypatch.setattr(ac, name, must_not_run)
    monkeypatch.setattr(ac, "PLUMBING_GREEN", tmp_path / "plumbing-green.json")

    carried = tmp_path / "verdicts-carried-abc.json"
    carried.write_text("{}")
    plan = _plan(
        skip_plumbing=True,
        plumbing_note=ac.PLUMBING_SKIP_NOTE,
        plumbing_carry_out=carried,
        record_greens=True,
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    note = f"skipped ({ac.PLUMBING_SKIP_NOTE})"
    assert report.merge_status == note
    assert report.echo_fill_status == note
    assert report.standing_merge_status == note
    assert report.complaints_status == note
    assert report.carry_out == carried
    assert not (tmp_path / "plumbing-green.json").exists()


def test_run_cycle_records_the_plumbing_green_only_after_a_complete_chain(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    green = tmp_path / "plumbing-green.json"
    monkeypatch.setattr(ac, "PLUMBING_GREEN", green)
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")

    def complaints_ok(*, spawn, emit, registry):
        return "3 open complaints in 2 groups"

    monkeypatch.setattr(ac, "_do_complaints", complaints_ok)
    plan = _plan(record_greens=True)
    rc = ac._run_cycle(
        plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()
    )
    assert rc == 0
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "plu"
    assert record["carry_out"] == str(plan.carry_out)
    assert record["format"] == "ams-plumbing-green/1"

    green.unlink()

    def complaints_broken(*, spawn, emit, registry):
        return "FAILED (exit 2) — informational"

    monkeypatch.setattr(ac, "_do_complaints", complaints_broken)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert not green.exists()

    def standing_merge_fails(report, *, spawn, emit, registry, plan):
        report.standing_merge_status = "FAILED (exit 1)"
        return False

    monkeypatch.setattr(ac, "_do_complaints", complaints_ok)
    monkeypatch.setattr(ac, "_do_standing_merge", standing_merge_fails)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 1
    assert not green.exists()


def test_run_cycle_records_no_plumbing_green_when_standing_fills_landed(monkeypatch, tmp_path):
    """Standing-fill runs last and nothing re-reads it, so a standing fill that lands can leave an echo group unanimous with a blank sibling — work the next pass's echo-fill would take. Only a standing merge that moved nothing witnesses the fixpoint the green claims."""

    def standing_merge_landed(report, *, spawn, emit, registry, plan):
        report.standing_merge_status = "merged"
        report.standing_merge_lines = ["merged 4 verdicts: 4 added, 0 kept newer"]
        return True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(ac, "_do_complaints", lambda *, spawn, emit, registry: "no open complaints")
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")
    green = tmp_path / "plumbing-green.json"
    monkeypatch.setattr(ac, "PLUMBING_GREEN", green)

    monkeypatch.setattr(ac, "_do_standing_merge", standing_merge_landed)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert not green.exists()

    monkeypatch.setattr(ac, "_do_standing_merge", _standing_merge_ok)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert green.exists()


def test_plumbing_settled_reads_only_the_last_step():
    report = ac.CycleReport()
    assert ac._plumbing_settled(report) is False
    report.standing_merge_lines = ["merged 4 verdicts: 4 added, 0 kept newer"]
    assert ac._plumbing_settled(report) is False
    report.standing_merge_lines = [
        "nothing changed: the autosave already holds all 3 verdicts (3 effective)."
    ]
    assert ac._plumbing_settled(report) is True


def _settled_repo(tmp_path, monkeypatch):
    """A repo whose run_m1 and surface build both auto-skip — the converged pass, the only shape the plumbing skip is offered on."""
    _defer_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "rebuild_gate_skip_fingerprint", lambda root=None: "no-match")
    monkeypatch.setattr(ac, "census_skip_fingerprint", lambda root=None, surface=None: "no-match")
    monkeypatch.setattr(ac, "PLUMBING_GREEN", tmp_path / "rebuild" / "out" / "plumbing-green.json")
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")


def test_main_skips_the_plumbing_on_a_matching_record(tmp_path, monkeypatch, capsys):
    _settled_repo(tmp_path, monkeypatch)
    carried = tmp_path / "verdicts-carried-abc.json"
    carried.write_text("{}")
    ac.record_plumbing_green("plu", carried)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "verdict plumbing auto-skipped" in out
    assert f"carry: SKIPPED ({ac.PLUMBING_SKIP_NOTE})" in out
    assert f"complaints: SKIPPED ({ac.PLUMBING_SKIP_NOTE})" in out

    ac.record_plumbing_green("moved", carried)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "verdict plumbing auto-skipped" not in out
    assert "carry: uv run python" in out


def test_main_never_defers_the_census_on_the_pass_that_skips_the_plumbing(tmp_path, monkeypatch, capsys):
    """The two never co-occur: the plumbing skip demands a settled surface, and a settled surface is exactly what makes a pass non-refreshing — so the pass that skips the chain is also the pass that runs the census."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu", None)
    assert ac.main(["--dry-run", "--defer-gates"]) == 0
    out = capsys.readouterr().out
    assert "verdict plumbing auto-skipped" in out
    assert "census: DEFERRED" not in out


def test_main_never_skips_the_plumbing_on_a_pass_that_writes_the_surface(tmp_path, monkeypatch, capsys):
    """The skip rides the surface build's own skip: only then is the stamp the chain keys on known not to move mid-pass."""
    _defer_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "PLUMBING_GREEN", tmp_path / "rebuild" / "out" / "plumbing-green.json")
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")
    ac.record_plumbing_green("plu", None)
    assert ac.main(["--dry-run"]) == 0
    assert "verdict plumbing auto-skipped" not in capsys.readouterr().out


def test_main_never_skips_the_plumbing_under_fresh_or_a_partial_chain(tmp_path, monkeypatch, capsys):
    """--carry-out and --snapshot-dir join the list because the skip writes neither file: honoring the flag and skipping the step cannot both happen, so the flag wins."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu", None)
    for argv in (
        ["--dry-run", "--fresh"],
        ["--dry-run", "--no-merge"],
        ["--dry-run", "--no-carry"],
        ["--dry-run", "--review-out", str(tmp_path / "rehearse")],
        ["--dry-run", "--carry-out", str(tmp_path / "carried.json")],
        ["--dry-run", "--snapshot-dir", str(tmp_path / "snap")],
    ):
        assert ac.main(argv) == 0
        assert "verdict plumbing auto-skipped" not in capsys.readouterr().out


def test_main_skipping_the_plumbing_takes_the_snapshot_with_it(tmp_path, monkeypatch):
    """No carry reads the snapshot and no surface write threatens the live copy, so the pass takes none — and retention says so instead of naming a directory that was never made."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu", None)
    calls: list[tuple] = []
    monkeypatch.setattr(ac, "snapshot_surface", lambda src, dst: calls.append((src, dst)) or "cloned")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert calls == []


def test_server_may_stay_up_only_when_the_pass_writes_neither_of_the_apps_files():
    assert ac.server_may_stay_up(skip_surface=True, skip_plumbing=True) is True
    assert ac.server_may_stay_up(skip_surface=True, skip_plumbing=False) is False
    assert ac.server_may_stay_up(skip_surface=False, skip_plumbing=True) is False
    assert ac.server_may_stay_up(skip_surface=False, skip_plumbing=False) is False


def _preflight_args(**overrides):
    kw = dict(review_out=None, yes=False, stop_server=False)
    kw.update(overrides)
    return argparse.Namespace(**kw)


def test_preflight_leaves_a_listening_server_up_for_a_pass_that_writes_nothing_under_it(monkeypatch, capsys):
    """The gate pass: no surface write to strand the tab, no store write for merge_verdicts to refuse. Nothing to take the port for, so the letters stay on screen for the whole half hour — and this holds without --stop-server, since the flag is permission to stop a server, not an instruction to."""
    stops: list[int] = []
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: stops.append(1) or True)
    for args in (_preflight_args(), _preflight_args(stop_server=True)):
        assert ac._preflight(args, may_stay_up=True) is True
    assert stops == []
    assert ac.SERVER_STAYS_UP_NOTE in capsys.readouterr().out


def test_preflight_stops_the_server_for_a_writing_pass_only_when_allowed(monkeypatch, capsys):
    """--stop-server is what `make review-cycle` passes in place of the recipe's old unconditional pkill; without it the refusal stands, because a bare run has no standing to end someone's verdicting session."""
    stops: list[int] = []
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: stops.append(1) or True
    )

    assert ac._preflight(_preflight_args(stop_server=True), may_stay_up=False) is True
    assert stops == [1]
    assert "Stopping the review server" in capsys.readouterr().out

    assert ac._preflight(_preflight_args(), may_stay_up=False) is False
    assert stops == [1]
    assert "REFUSING TO RUN" in capsys.readouterr().out


def test_preflight_refuses_when_the_stop_leaves_the_port_held(monkeypatch, capsys):
    """Something else is serving 7294, or the server wedged mid-shutdown. Either way the surface rewrite would land under a live reader, so the pass stops rather than building over it."""
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: False)
    assert ac._preflight(_preflight_args(stop_server=True), may_stay_up=False) is False
    assert "still listening" in capsys.readouterr().out


def test_stop_review_server_waits_for_the_port_to_come_free(monkeypatch):
    """The wait is the point: pkill returns as soon as the signal is delivered, and a surface build racing the socket's last breath is exactly what the old recipe's lsof loop was for."""
    killed: list[list[str]] = []
    monkeypatch.setattr(
        ac.subprocess, "run", lambda argv, **kw: killed.append(argv) or subprocess.CompletedProcess(argv, 0)
    )
    monkeypatch.setattr(ac.time, "sleep", lambda seconds: None)
    remaining = [True, True, True]
    monkeypatch.setattr(
        ac, "server_listening", lambda port=ac.REVIEW_PORT: bool(remaining and remaining.pop())
    )
    assert ac.stop_review_server() is True
    assert killed == [["pkill", "-f", ac.SERVER_STOP_PATTERN]]
    assert remaining == []

    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    assert ac.stop_review_server(timeout=0.0) is False


def test_main_leaves_the_server_up_on_the_settled_pass(tmp_path, monkeypatch, capsys):
    """End to end through the resolver: the pass that skips the surface and the plumbing is the one that keeps serving, and it never reaches for the port."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu", None)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: pytest.fail("stopped")
    )
    monkeypatch.setattr(ac, "snapshot_surface", lambda src, dst: "cloned")
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert ac.SERVER_STAYS_UP_NOTE in capsys.readouterr().out


def test_main_stops_the_server_when_the_pass_rebuilds_the_surface(tmp_path, monkeypatch, capsys):
    _defer_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    stops: list[int] = []
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: stops.append(1) or True
    )
    monkeypatch.setattr(ac, "snapshot_surface", lambda src, dst: "cloned")
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main(["--stop-server"]) == 0
    assert stops == [1]
    assert "Stopping the review server" in capsys.readouterr().out


def test_snapshot_surface_copies_tree(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.json").write_text("[1]")
    (src / "manifest.json").write_text("{}")
    dst = tmp_path / "dst"
    how = ac.snapshot_surface(src, dst)
    assert how in ("cloned", "copied")
    assert (dst / "manifest.json").read_text() == "{}"
    assert (dst / "sub" / "a.json").read_text() == "[1]"


def _carried(stamp):
    return json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "verdicts": []})


def test_prune_snapshots_removes_others_keeps_the_cycle_snapshot_and_ignores_files(tmp_path):
    (tmp_path / "review-pre-a").mkdir()
    (tmp_path / "review-pre-b").mkdir()
    keep = tmp_path / "review-pre-keep"
    keep.mkdir()
    a_file = tmp_path / "review-pre-x.json"
    a_file.write_text("{}")

    removed = ac.prune_snapshots(tmp_path, keep)

    assert removed == [tmp_path / "review-pre-a", tmp_path / "review-pre-b"]
    assert keep.exists()
    assert a_file.exists()
    assert not (tmp_path / "review-pre-a").exists()
    assert not (tmp_path / "review-pre-b").exists()


def test_prune_carried_keeps_aligned_and_keep_and_deletes_stale(tmp_path):
    stamp = "2026-07-17T20:24:44Z"
    aligned = tmp_path / "verdicts-carried-aligned.json"
    aligned.write_text(_carried(stamp))
    stale = tmp_path / "verdicts-carried-stale.json"
    stale.write_text(_carried("2026-07-10T00:00:00Z"))
    keep = tmp_path / "verdicts-carried-keep.json"
    keep.write_text(_carried("2026-07-10T00:00:00Z"))
    unreadable = tmp_path / "verdicts-carried-broken.json"
    unreadable.write_text("{ not json")
    not_a_dict = tmp_path / "verdicts-carried-list.json"
    not_a_dict.write_text(json.dumps(["a", "b"]))
    evidence = tmp_path / "rebuild" / "evidence"
    evidence.mkdir(parents=True)
    evidence_stale = evidence / "verdicts-carried-evidence.json"
    evidence_stale.write_text(_carried("2026-07-10T00:00:00Z"))

    removed, unread = ac.prune_carried(tmp_path, stamp, keep)

    assert set(removed) == {stale, not_a_dict}
    assert unread == [unreadable]
    assert aligned.exists()
    assert keep.exists()
    assert unreadable.exists()
    assert evidence_stale.exists()
    assert not stale.exists()
    assert not not_a_dict.exists()


def test_prune_carried_stamp_none_deletes_nothing(tmp_path):
    stale = tmp_path / "verdicts-carried-stale.json"
    stale.write_text(_carried("2026-07-10T00:00:00Z"))

    removed, unread = ac.prune_carried(tmp_path, None, None)

    assert removed == []
    assert unread == []
    assert stale.exists()


def test_prune_stashes_keeps_from_the_last_base_onward(tmp_path):
    journal_path = tmp_path / "verdicts-journal.ndjson"
    journal.record_transition(
        journal_path,
        source="autosave",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[],
        stashed="verdicts-autosave-A.json",
        at="2026-07-10T01:00:00Z",
    )
    journal.record_transition(
        journal_path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[],
        new_verdicts=[],
        stashed="verdicts-autosave-B.json",
        at="2026-07-10T02:00:00Z",
    )
    journal.record_transition(
        journal_path,
        source="merge",
        stamp="S2",
        old_stamp="S1",
        old_verdicts=[],
        new_verdicts=[],
        stashed="verdicts-autosave-C.json",
        at="2026-07-10T03:00:00Z",
    )
    journal.record_transition(
        journal_path,
        source="autosave",
        stamp="S2",
        old_stamp="S2",
        old_verdicts=[],
        new_verdicts=[],
        stashed="verdicts-autosave-D.json",
        at="2026-07-10T04:00:00Z",
    )
    stashes = {}
    for tag in ("A", "B", "C", "D", "E"):
        path = tmp_path / f"verdicts-autosave-{tag}.json"
        path.write_text("{}")
        stashes[tag] = path
    live = tmp_path / "verdicts-autosave.json"
    live.write_text("{}")

    removed = ac.prune_stashes(tmp_path, journal_path)

    assert removed == [stashes["A"], stashes["B"], stashes["E"]]
    assert not stashes["A"].exists()
    assert not stashes["B"].exists()
    assert not stashes["E"].exists()
    assert stashes["C"].exists()
    assert stashes["D"].exists()
    assert live.exists()


def test_prune_stashes_returns_none_without_a_base_event(tmp_path):
    journal_path = tmp_path / "verdicts-journal.ndjson"
    journal.record_transition(
        journal_path,
        source="autosave",
        stamp="S1",
        old_stamp="S1",
        old_verdicts=[],
        new_verdicts=[],
        stashed="verdicts-autosave-Z.json",
        at="2026-07-10T01:00:00Z",
    )
    orphan = tmp_path / "verdicts-autosave-Z.json"
    orphan.write_text("{}")

    result = ac.prune_stashes(tmp_path, journal_path)

    assert result is None
    assert orphan.exists()


def test_retention_cutoff_is_the_window_before_now():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
    expected = (
        (now - timedelta(days=ac.RETENTION_WINDOW_DAYS))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    assert ac.retention_cutoff(now) == expected
    assert ac.retention_cutoff(now) == "2026-07-14T12:00:00Z"


def test_build_plan_retention_default_on():
    plan = _plan()
    assert plan.retention is True
    by_name = {step.name: step for step in plan.steps}
    note = by_name["retention"].note
    assert "green finish" in note
    assert str(ac.RETENTION_WINDOW_DAYS) in note


def test_build_plan_retention_skipped_with_keep_history():
    plan = _plan(keep_history=True)
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert by_name["retention"].note == "SKIPPED (--keep-history)"


def test_build_plan_retention_off_on_first_run():
    plan = _plan(first_run=True, verdicts=None)
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert "first run" in by_name["retention"].note


def test_build_plan_retention_off_on_rehearsal():
    plan = _plan(review_out=Path("tmp/reh"))
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert "rehearsal" in by_name["retention"].note


def test_retention_never_runs_for_real_during_the_suite(real_run_retention):
    """The tripwire on the autouse stub. Retention resolves its targets from ac.ROOT at call time — no fixture redirects that — so a real run from inside the suite deletes the live repo's snapshots and carried exports, and compacts its verdict journal. Any test reaching a green finish with record_greens set would do it, and one did: a suite run deleted a live cycle's only snapshot between its build and its carry, stranding the pass's verdicts."""
    assert ac.run_retention is not real_run_retention
    assert ac.run_retention(_plan(record_greens=True)) is None


def test_the_gate_summaries_a_pass_clears_are_never_the_live_ones(tmp_path, live_deletion_targets):
    """The same tripwire for the other stages that delete before they rebuild. run_m1 unlinks its four summaries, and gate:conform, gate:kernel-differential and gate:kernel-harness each unlink their own, all before spawning and all from constants resolved against the live rebuild/out/m1 — so a test that drives any of those stages without stubbing it empties the directory the surface build consumes and the cycle's auto-skip keys on, at the price of a full rebuild to get it back. None would fail: the missing summaries read as a failed gate, which is what most such tests are asserting anyway."""
    redirected = [
        *ac.M1_SUMMARY_FILES.values(),
        ac.CONFORM_SUMMARY,
        ac.KERNEL_DIFFERENTIAL_SUMMARY,
        ac.KERNEL_HARNESS_SUMMARY,
    ]
    assert [path.parent for path in redirected] == [tmp_path] * len(redirected)
    assert [path.name for path in redirected] == [path.name for path in live_deletion_targets]
    assert all(path.parent == ac.M1_OUT for path in live_deletion_targets)


def test_finish_runs_retention_on_a_real_green_finish(monkeypatch):
    calls = {"n": 0}

    def stub(plan):
        calls["n"] += 1

    monkeypatch.setattr(ac, "run_retention", stub)
    plan = _plan(record_greens=True)
    assert plan.retention is True and plan.record_greens is True
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0
    assert calls["n"] == 1


def test_retention_leaves_the_snapshots_alone_when_the_pass_took_none(
    tmp_path, monkeypatch, capsys, real_run_retention
):
    """A skip pass never makes the snapshot retention prunes to, so pruning would delete the last stamp-aligned copy — the very one describe_carry_source tells you to recover from when a surface gets restamped outside a cycle."""
    skipping = _plan(skip_plumbing=True, plumbing_note=ac.PLUMBING_SKIP_NOTE)
    ordinary = _plan(snapshot_dir=tmp_path / "tmp" / "review-pre-fresh")
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    (tmp_path / "tmp").mkdir()
    survivor = tmp_path / "tmp" / "review-pre-abc1234"
    survivor.mkdir()
    monkeypatch.setattr(journal, "compact", lambda path, cutoff: {"compacted": False})
    monkeypatch.setattr(ac, "prune_stashes", lambda root, journal_path: [])
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)

    real_run_retention(skipping)
    assert survivor.is_dir()
    assert "snapshots : left intact" in capsys.readouterr().out

    real_run_retention(ordinary)
    assert not survivor.exists()


def test_retention_leaves_the_journal_and_stashes_alone_while_the_server_is_up(
    tmp_path, monkeypatch, capsys, real_run_retention
):
    """The app appends to the journal as the reviewer verdicts, and compact() rewrites the whole file around a read — an append landing in between is gone. The stash sweep reads that same journal for its reference index, so it waits too; the carried sweep, which the app never writes, still runs."""
    plan = _plan(skip_plumbing=True, plumbing_note=ac.PLUMBING_SKIP_NOTE)
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "manifest.json").write_text(json.dumps({"generated_at": "2026-08-07T00:00:00Z"}))
    (tmp_path / "tmp").mkdir()
    (tmp_path / "verdicts-carried-old.json").write_text(_carried("2026-01-01T00:00:00Z"))
    compacted: list[str] = []
    monkeypatch.setattr(
        journal, "compact", lambda path, cutoff: compacted.append(cutoff) or {"compacted": False}
    )
    swept: list[Path] = []
    monkeypatch.setattr(ac, "prune_stashes", lambda root, journal_path: swept.append(root) or [])
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)

    real_run_retention(plan)

    out = capsys.readouterr().out
    assert compacted == [] and swept == []
    assert "journal   : left intact (the review server is up" in out
    assert "stashes   : left intact (the review server is up" in out
    assert not (tmp_path / "verdicts-carried-old.json").exists()


def test_finish_skips_retention_when_failures(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ac, "run_retention", lambda plan: calls.__setitem__("n", calls["n"] + 1))
    plan = _plan(record_greens=True)
    rc = ac._finish(ac.CycleReport(), ["boom"], plan)
    assert rc == 1
    assert calls["n"] == 0


def test_finish_skips_retention_when_plan_opts_out(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ac, "run_retention", lambda plan: calls.__setitem__("n", calls["n"] + 1))
    plan = _plan(keep_history=True, record_greens=True)
    assert plan.retention is False
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0
    assert calls["n"] == 0


def test_finish_never_prunes_a_mocked_green_cycle(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ac, "run_retention", lambda plan: calls.__setitem__("n", calls["n"] + 1))
    plan = _plan()
    assert plan.retention is True and plan.record_greens is False
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0
    assert calls["n"] == 0


def test_finish_survives_a_retention_error(monkeypatch):
    def boom(plan):
        raise RuntimeError("retention blew up")

    monkeypatch.setattr(ac, "run_retention", boom)
    plan = _plan(record_greens=True)
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0


def _spawning_run_m1(report, *, spawn, emit, registry, budget, **_):
    spawn("run_m1", ["uv", "run", "fake-m1"], emit=emit, registry=registry, stream=True)
    report.unmatched = 1
    report.multi_matched = 0
    report.boundary_pass = True
    report.pins_pass = True
    return ac.GateOutcome(True, [], 1, 0)


def _spawning_surface(report, *, spawn, emit, registry, review_out, budget, **_):
    spawn("surface", ["uv", "run", "fake-surface"], emit=emit, registry=registry, stream=False)
    report.surface_units = 1
    return True


def _complaints_ok(*, spawn, emit, registry):
    return "no open complaints"


def _patch_timing_cycle(monkeypatch):
    monkeypatch.setattr(ac, "_do_run_m1", _spawning_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _spawning_surface)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", _echo_fill_ok)
    monkeypatch.setattr(ac, "_do_echo_merge", _echo_merge_ok)
    monkeypatch.setattr(ac, "_do_standing_fill", _standing_fill_ok)
    monkeypatch.setattr(ac, "_do_standing_merge", _standing_merge_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_do_complaints", _complaints_ok)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_kernel_differential_task", _kernel_green)
    monkeypatch.setattr(ac, "_gate_kernel_harness_task", _harness_green)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)


def test_green_cycle_journals_steps_then_one_run_line(monkeypatch, tmp_path):
    _patch_timing_cycle(monkeypatch)

    journal_path = tmp_path / "timings.ndjson"
    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(
        plan,
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
        timings=CycleTimings(journal_path),
    )

    assert rc == 0
    entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    steps = [entry for entry in entries if entry["kind"] == "step"]
    runs = [entry for entry in entries if entry["kind"] == "run"]
    assert [entry["name"] for entry in steps] == ["run_m1", "surface"]
    assert len(runs) == 1
    assert entries[-1]["kind"] == "run"
    assert entries[-1]["exit"] == "ok"
    assert entries[-1]["interrupted"] is False
    assert {entry["run"] for entry in entries} == {entries[-1]["run"]}


def test_failing_cycle_still_journals_a_run_line(monkeypatch, tmp_path):
    def failing_merge(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return False

    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_merge", failing_merge)

    journal_path = tmp_path / "timings.ndjson"
    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(
        plan,
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
        timings=CycleTimings(journal_path),
    )

    assert rc == 1
    entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["kind"] == "run"
    assert entries[-1]["exit"] == "failed"
    assert "verdict merge failed" in entries[-1]["failures"]


def test_cycle_without_timings_writes_no_journal(monkeypatch, tmp_path):
    _patch_timing_cycle(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(
        plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )

    assert rc == 0
    assert not list(tmp_path.glob("*.ndjson"))
