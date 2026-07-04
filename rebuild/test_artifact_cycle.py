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
