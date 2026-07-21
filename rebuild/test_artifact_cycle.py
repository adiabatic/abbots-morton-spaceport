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

import pytest

from rebuild.tools import artifact_cycle as ac


@pytest.fixture(autouse=True)
def _redirect_cycle_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "CYCLE_SUMMARY", tmp_path / "cycle_summary.json")


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
    assert by_name["gate:js"].argv[:2] == ["node", "--test"]
    assert all(name.endswith(".test.js") for name in by_name["gate:js"].argv[2:])
    assert by_name["gate:conform"].argv[:6] == [
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
    assert by_name["gate:conform"].argv[-2:] == ["--jobs", "8"]
    assert plan.conform_jobs == 8

    small = _plan(ncores=4)
    small_by_name = {step.name: step for step in small.steps}
    assert small_by_name["gate:conform"].argv[-2:] == ["--jobs", "4"]

    single = _plan(ncores=1)
    single_by_name = {step.name: step for step in single.steps}
    assert single_by_name["gate:conform"].argv[-1] == "--conform-only"


def test_dry_run_plan_conform_horizon():
    plan = _plan(conform_horizon=4)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv[-2:] == ["--conform-horizon", "4"]
    assert plan.conform_horizon == 4

    default = _plan()
    default_by_name = {step.name: step for step in default.steps}
    assert "--conform-horizon" not in default_by_name["gate:conform"].argv
    assert default.conform_horizon == ac.CONFORM_HORIZON_DEFAULT


def test_dry_run_plan_skip_conform():
    plan = _plan(skip_conform=True)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert by_name["gate:conform"].note == "SKIPPED (--skip-conform)"
    assert by_name["gate:rebuild"].argv is not None


def test_dry_run_plan_merge_follows_carry():
    plan = _plan(snapshot_dir=None, short_id="abc1234")
    names = [step.name for step in plan.steps]
    assert names.index("merge") == names.index("carry") + 1
    assert names.index("echo-fill") == names.index("merge") + 1
    assert names.index("echo-merge") == names.index("echo-fill") + 1
    assert names.index("census") == names.index("echo-merge") + 1
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
    assert "--update" in by_name["census"].argv


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


def _plan(**overrides):
    kw = dict(
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


def _pass_run_m1(report, *, spawn, emit, registry, budget):
    report.unmatched = 1
    report.multi_matched = 0
    report.boundary_pass = True
    report.pins_pass = True
    return ac.GateOutcome(True, [], 1, 0)


def _surface_ok(report, *, spawn, emit, registry, review_out, budget):
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


def _census_clean(*, spawn, emit, registry, update_pins, surface):
    return "clean"


def _js_ok(spawn, emit, registry):
    return _step("gate:js", 0)


def _make_ok(spawn, emit, registry):
    return _step("gate:make-test", 0)


def _rebuild_green(pool_policy, make_fut, spawn, emit, registry, update_pins):
    return ac._RebuildOutcome("green", [], [])


def _conform_green(pool_policy, make_fut, spawn, emit, registry, argv):
    return "green", []


def _patch_build_chain(monkeypatch):
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_merge", _merge_ok)
    monkeypatch.setattr(ac, "_do_echo_fill", _echo_fill_ok)
    monkeypatch.setattr(ac, "_do_echo_merge", _echo_merge_ok)
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
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_rebuild_task", _rebuild_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.echo_fill_status == "filled"
    assert report.echo_merge_status == "FAILED (exit 1)"
    assert "echo-merge failed" in capsys.readouterr().out


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

    def fake_run_m1(report, *, spawn, emit, registry, budget):
        release_run_m1.wait()
        record["run_m1_finish"] = time.monotonic()
        return ac.GateOutcome(True, [], 1, 0)

    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
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

    def fake_run_m1(report, *, spawn, emit, registry, budget):
        record["run_m1_finish"] = time.monotonic()
        return ac.GateOutcome(True, [], 1, 0)

    def fake_rebuild(pool_policy, make_fut, spawn, emit, registry, update_pins):
        record["rebuild_invoked"] = time.monotonic()
        return ac._RebuildOutcome("green", [], [])

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert record["rebuild_invoked"] >= record["run_m1_finish"]


def test_gate_rebuild_skipped_when_run_m1_fails(monkeypatch, capsys):
    called = {"rebuild": False}

    def fake_run_m1(report, *, spawn, emit, registry, budget):
        return None

    def fake_rebuild(pool_policy, make_fut, spawn, emit, registry, update_pins):
        called["rebuild"] = True
        return ac._RebuildOutcome("green", [], [])

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_rebuild_task", fake_rebuild)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
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

    def fake_run_m1(report, *, spawn, emit, registry, budget):
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


def test_pool_queue_conform_waits_for_make_test_coresident_with_rebuild(monkeypatch, tmp_path):
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    release_rebuild = threading.Event()
    rebuild_running = threading.Event()
    conform_started = threading.Event()

    def fake_make(spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_rebuild(pool_policy, make_fut, spawn, emit, registry, update_pins):
        rebuild_running.set()
        release_rebuild.wait()
        record["rebuild_finish"] = time.monotonic()
        return ac._RebuildOutcome("green", [], [])

    def fake_spawn(name, argv, *, emit, registry, stream):
        if name == "gate:conform":
            record["conform_start"] = time.monotonic()
            record["conform_argv"] = argv
            conform_started.set()
        return _step(name, 0)

    summary_path = tmp_path / "conform_summary.json"
    summary_path.write_text(
        json.dumps({"divergences": 0, "uncovered_rules": 0, "uncovered_transitions": 0, "pass": True})
    )
    monkeypatch.setattr(ac, "CONFORM_SUMMARY", summary_path)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
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
    make_running.wait()
    rebuild_running.wait()
    assert "conform_start" not in record
    release_make.set()
    conform_started.wait()
    assert "rebuild_finish" not in record
    release_rebuild.set()
    t.join()

    assert record["conform_start"] >= record["make_finish"]
    assert record["conform_argv"][:6] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.pipeline.run_m1",
        "--conform-only",
    ]
    assert report.gate_conform == "green"
    assert box["rc"] == 0


def test_summary_exact_under_out_of_order_completion(monkeypatch, capsys):
    ev_js = threading.Event()
    ev_make = threading.Event()
    ev_rebuild = threading.Event()

    def fake_run_m1(report, *, spawn, emit, registry, budget):
        report.unmatched = 7777
        report.multi_matched = 0
        report.boundary_pass = True
        report.pins_pass = True
        return ac.GateOutcome(True, [], 7777, 0)

    def fake_surface(report, *, spawn, emit, registry, review_out, budget):
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

    def fake_rebuild(pool_policy, make_fut, spawn, emit, registry, update_pins):
        ev_rebuild.wait()
        return ac._RebuildOutcome("green (1 documented baseline)", [], [])

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", fake_surface)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
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
    outcome = ac._gate_rebuild_task("overlap", None, fake_spawn, emit, registry, False)

    assert seen["stream"] is False
    assert len(outcome.hard_ids) == 2
    assert outcome.status == "FAILED (2 unexplained)"

    report = ac.CycleReport()
    failures = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: outcome)
        ac._join_gates(report, failures, None, fut, None, None, False, emit)
    assert report.gate_rebuild == "FAILED (2 unexplained)"

    out = capsys.readouterr().out
    assert not any(line.startswith("[gate:rebuild]") for line in out.splitlines())


def test_classify_rebuild_reads_colored_pytest_output():
    """Under FORCE_COLOR (as set by the agent harness) pytest wraps its FAILED lines in ANSI escapes; the classifier must still recognize the documented baseline instead of reporting an unexplained failure."""
    colored = "\n".join(
        f"\x1b[31mFAILED\x1b[0m {file}::\x1b[1m{name}\x1b[0m - x"
        for file, _, name in (test_id.partition("::") for test_id in sorted(ac.BASELINE_REBUILD_FAILURES))
    )
    outcome = ac._classify_rebuild(_step(rc=1, stdout=colored), update_pins=False)
    assert outcome.hard_ids == []
    assert outcome.status == f"green ({len(ac.BASELINE_REBUILD_FAILURES)} documented baseline)"


def test_failure_funnels_from_concurrent_branch(monkeypatch, capsys):
    def fake_surface(report, *, spawn, emit, registry, review_out, budget):
        report.surface_units = 100
        return True

    def fake_make(spawn, emit, registry):
        return _step("gate:make-test", 1)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", fake_surface)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
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
    monkeypatch.setattr(ac, "_gate_make_test_task", raising_make)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_make_test == "FAILED (exception)"
    assert report.gate_rebuild == "green"
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_run_m1_failure_still_collects_make_test(monkeypatch, capsys):
    def fake_run_m1(report, *, spawn, emit, registry, budget):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
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

    def boom(report, *, spawn, emit, registry, budget):
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
    assert ac.stage_job_budget(skip_gates=False, ncores=12) == 1
    assert ac.stage_job_budget(skip_gates=True, ncores=12) == 12
    assert ac.stage_job_budget(skip_gates=True, ncores=1) == 1


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
    assert "CO-RESIDENT with gate:rebuild's pool" in text
    assert "--jobs budget        : 1" in text

    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv == ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
    assert by_name["surface-build"].argv == ["uv", "run", "python", "-m", "rebuild.review.build"]


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
    assert by_name["run_m1"].argv[-2:] == ["--jobs", "12"]
    assert by_name["surface-build"].argv[-2:] == ["--jobs", "12"]
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
    assert default_by_name["run_m1"].argv == ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
    assert default_by_name["surface-build"].argv == ["uv", "run", "python", "-m", "rebuild.review.build"]


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
    assert by_name["surface-build"].argv[-2:] == ["--out", "tmp/reh"]
    assert by_name["census"].argv[-2:] == ["--surface", "tmp/reh"]
    assert by_name["carry"].argv[-2:] == ["--current-surface", "tmp/reh"]
    assert plan.census_surface == Path("tmp/reh")
    assert plan.review_out == Path("tmp/reh")
    assert str(ac.REVIEW_OUT) in by_name["snapshot"].note

    monkeypatch.setattr(ac, "server_listening", lambda *a, **k: True)
    waiver = argparse.Namespace(review_out=Path("tmp/reh"), yes=False)
    assert ac._preflight(waiver) is True
    refuse = argparse.Namespace(review_out=None, yes=False)
    assert ac._preflight(refuse) is False


def _green_report():
    report = ac.CycleReport()
    report.gate_js = "green"
    report.gate_rebuild = "green"
    report.gate_conform = "green"
    report.gate_make_test = "green"
    return report


def test_cycle_summary_payload_all_green_exit_ok():
    payload = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert payload["format"] == "ams-cycle-summary/1"
    assert payload["exit"] == "ok"
    assert payload["failures"] == []
    assert set(payload["gates"]) == {"js", "rebuild", "conform", "make_test"}
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
        "pool_policy": ac.REBUILD_POOL_POLICY_DEFAULT,
        "skip_gates": False,
        "skip_conform": False,
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
    def fake_run_m1(report, *, spawn, emit, registry, budget):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
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
    def boom(report, *, spawn, emit, registry, budget):
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


def test_dry_run_auto_resolution_flags_a_mismatched_stamp(tmp_path, monkeypatch, capsys):
    _seed_auto_repo(tmp_path, monkeypatch)
    (tmp_path / "verdicts-carried-old.json").write_text(
        json.dumps(_verdicts_doc("2026-07-10T00:00:00Z", ["u-1"]))
    )
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Auto-resolved carry source: verdicts-carried-old.json" in out
    assert "not the served surface" in out


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
    assert "gate:make-test auto-skipped" in rendered


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
