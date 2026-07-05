import argparse
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rebuild.tools import artifact_cycle as ac


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


def _census_clean(*, spawn, emit, registry, update_pins, surface):
    return "clean"


def _js_ok(spawn, emit, registry):
    return _step("gate:js", 0)


def _make_ok(spawn, emit, registry):
    return _step("gate:make-test", 0)


def _rebuild_green(pool_policy, make_fut, spawn, emit, registry, update_pins):
    return ac._RebuildOutcome("green", [], [])


def _patch_build_chain(monkeypatch):
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_carry", _carry_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)


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
        ac._join_gates(report, failures, None, fut, None, False, emit)
    assert report.gate_rebuild == "FAILED (2 unexplained)"

    out = capsys.readouterr().out
    assert not any(line.startswith("[gate:rebuild]") for line in out.splitlines())


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
