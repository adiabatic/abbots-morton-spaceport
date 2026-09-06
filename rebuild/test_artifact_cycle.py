import argparse
import functools
import hashlib
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

from rebuild.conftest import is_live_artifact_path
from rebuild.review import journal
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import calibrate_budgets as cb
from rebuild.tools import console
from rebuild.tools import cycle_timings as ct
from rebuild.tools.peak_rss import format_gb
from rebuild.tools.cycle_timings import CycleTimings

REPO_ROOT = Path(__file__).resolve().parents[1]
# The box every plan here is resolved against. A width asserted in this file has to be a fact about an invented machine rather than about whichever one is running the suite, and 44 GB separated the kernel fan-out's two arms at the divisor the row-side levers left — three configurations alone, two beside gate:make-test's pytest pool — which is what let a reservation that stopped happening show up as a changed number rather than the same one twice. At the divisor issue #168's probe-cascade lever priced it fits eight or more either way, so the reservation assertions resolve against 36 GB below, which separates the arms again at seven alone and six beside the pool; a re-measured CONFIG_PEAK_BYTES moves both numbers and may want the box re-chosen with it. Both spellings of 32 GB sit on an edge whose answer depends on the unit convention, which is a worse box to reason about.
BOX_44_GB = 44_000_000_000
BOX_36_GB = 36_000_000_000
# The fleet's two real machines, for the surface width's assertions. With a worker priced at its width-two peak, no box either machine offers separates the build's arms — the pool's bytes come off a box with a worker's worth of slack left over on both — so the reservation arithmetic is asserted at the `_surface_fit_terms` seam, where no box enters at all, and the widths here are asserted against the machines that actually run them rather than against one invented to sit where the subtraction would move a width: 51_539_607_552 is the 48 GiB box whose width-two pool outran the eight-wide worker seed, and 34_359_738_368 is the 32 GiB Mac the eight-wide core clamp drove into swap.
BOX_48_GIB = 51_539_607_552
BOX_32_GIB = 34_359_738_368


@pytest.fixture(autouse=True)
def _no_stated_widths(monkeypatch):
    """Both knobs that outrank every derived width in the tree, cleared for the whole file. Every plan built here carries a kernel width and a pytest-pool width now, and a developer who has exported either variable would otherwise watch these assertions pass or fail for a reason that has nothing to do with the arrangement the test set up. That the knobs do outrank the arithmetic is asserted by the tests that set them deliberately."""
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)


@pytest.fixture(autouse=True)
def _redirect_contracts_lane_reads(monkeypatch, tmp_path):
    """The read half of the suite's no-live-repo standard, and it needs its own argument, because a read costs the repo nothing and so was never covered by the write redirect in the conftest. What it costs instead is the truth of the test: the cycle resolves its *read* paths from the live repo at call time exactly as it resolves its write paths, so a test driving `_run_cycle` over mocked stages still renders its summary from whatever review surface and behavior-class sidecar happen to be sitting in rebuild/out — a number the test never built, from a build it never ran, which flips with the working tree under it. Every test here is a contracts-lane test, so there is no lane to check and no legitimate live read to preserve: this module sees the live artifacts as absent, and the audit guard fails anything that reaches past this for one.

    It says that at the four seams that turn a live path into a value the cycle keys on. The review surface is a constant and redirects as one. The behavior-class sidecar cannot: `deep_sweep_skip_lines` re-roots BEHAVIOR_CLASSES against ROOT, so pointing the constant outside ROOT makes it unreadable for *every* root and breaks the tests that pass their own — the seam that survives is the default root itself. The last two are what the gates' `*_skip_lines(ROOT)` reach through: two globs over rebuild/out (`baselines_value`, `_subset_tables`) and the per-file digest, which answers "absent" for a live path exactly as it already does for one that is missing. Every one of them answers only for the live root, so a caller that passes its own — which is how the tests about those functions are written — runs the real thing.
    """
    from rebuild.pipeline import fingerprint

    real_sweep_lines = ac.deep_sweep_skip_lines
    real_baselines = fingerprint.baselines_value
    real_subsets = ac._subset_tables
    real_sha = ac._sha256_path
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    monkeypatch.setattr(
        ac,
        "_sha256_path",
        lambda path: "absent" if is_live_artifact_path(path) else real_sha(path),
    )
    monkeypatch.setattr(
        ac,
        "_subset_tables",
        lambda root: [] if root == REPO_ROOT else real_subsets(root),
    )
    monkeypatch.setattr(
        ac,
        "deep_sweep_skip_lines",
        lambda root=ac.ROOT: None if root == ac.ROOT else real_sweep_lines(root),
    )
    monkeypatch.setattr(
        fingerprint,
        "baselines_value",
        lambda root: "contracts-lane" if root == REPO_ROOT else real_baselines(root),
    )


def _plan_text(plan: ac.Plan) -> str:
    """The plan block as one string, for the assertions that only care that a phrase is in it somewhere."""
    return "\n".join(ac.render_plan(plan))


_PLAN_ROW = r"^\s+\d+\s+(?:run\?|run|skip)\s+"


def _step_lines(text: str, name: str) -> str:
    """One step's row out of a plan block, with its `$ argv` line when it has one — the successor to grepping `<name>: <argv>` out of the old numbered plan, and the way a test says which step a phrase belongs to now that the row carries a status column and the argv sits on its own line."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(_PLAN_ROW + re.escape(name) + r"(?:\s|$)", line):
            block = [line]
            if index + 1 < len(lines) and lines[index + 1].lstrip().startswith("$ "):
                block.append(lines[index + 1])
            return "\n".join(block)
    return ""


def _pass_summaries():
    return {
        "pipeline": {"defect_errors": []},
        "manual_pins": {"pass": True, "disagreements": [], "pins_in_scope": 143, "replayed": 143},
        "oracle": {"unmatched": 8423, "multi_matched": 0},
    }


def test_gate_passes_on_clean_summaries():
    s = _pass_summaries()
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert outcome.check == "run_m1"
    assert outcome.verdict == "green"
    assert outcome.status == "green"
    assert outcome.ok
    assert outcome.failures == []
    assert outcome.failed_ids == []


def test_gate_fails_on_defect_errors():
    s = _pass_summaries()
    s["pipeline"]["defect_errors"] = ["E-ANCHOR convention:foo: bad"]
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert outcome.verdict == "red"
    assert outcome.status == "FAILED"
    assert any("defect" in reason for reason in outcome.failures)


def test_gate_fails_on_a_manual_pin_gate_with_nothing_in_scope():
    """The vacuous pass: `pass` is `not disagreements`, so a gate that replayed no pin reports green. The verdict here is run_m1's own, scope included, and it refuses that."""
    s = _pass_summaries()
    s["manual_pins"] = {"pass": True, "disagreements": [], "pins_in_scope": 0, "replayed": 0}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert any("no pins in scope" in reason for reason in outcome.failures)


def test_gate_fails_on_manual_pins():
    s = _pass_summaries()
    s["manual_pins"] = {"pass": False, "disagreements": ["one", "two"]}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert any("Manual-pin" in reason for reason in outcome.failures)


def test_gate_fails_on_multi_matched():
    s = _pass_summaries()
    s["oracle"] = {"unmatched": 8423, "multi_matched": 2}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not outcome.ok
    assert any("multi_matched" in reason for reason in outcome.failures)


def test_gate_unmatched_alone_is_not_a_failure():
    s = _pass_summaries()
    s["oracle"] = {"unmatched": 999999, "multi_matched": 0}
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert outcome.ok


def test_the_gate_carries_no_oracle_counts():
    """UNMATCHED and multi_matched were informational passengers on the old verdict, and both callers hold the oracle summary they came from — so the verdict answers for the judgment alone and the numbers are read where they live."""
    s = _pass_summaries()
    outcome = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not hasattr(outcome, "unmatched")
    assert not hasattr(outcome, "multi_matched")


def test_conform_gate_passes_on_clean_summary():
    verdict = ac.evaluate_conform_gate({"divergences": 0, "pass": True})
    assert verdict.check == "conform"
    assert verdict.verdict == "green"
    assert verdict.status == "green"
    assert verdict.failures == []


def test_conform_gate_fails_on_divergences():
    verdict = ac.evaluate_conform_gate({"divergences": 3, "pass": False})
    assert verdict.verdict == "red"
    assert verdict.status == "FAILED"
    assert verdict.failures == ["conform gate: 3 font-vs-settle divergence(s)"]


def test_conform_gate_fails_on_missing_summary():
    verdict = ac.evaluate_conform_gate(None)
    assert verdict.verdict == "red"
    assert verdict.status == "FAILED (no conform_summary.json)"
    assert verdict.failures == ["conform gate: run_m1 --conform-only wrote no summary"]


def test_conform_gate_names_no_failed_ids():
    """The sweep fails as a belt, not as a list of cases: what a divergence names is a window, and the audit beside the summary is where those are read."""
    assert ac.evaluate_conform_gate({"divergences": 3, "pass": False}).failed_ids == []
    assert ac.evaluate_conform_gate(None).failed_ids == []


def test_conform_gate_fails_on_bare_false_pass():
    verdict = ac.evaluate_conform_gate({"pass": False})
    assert verdict.status == "FAILED"
    assert verdict.failures == ["conform gate: pass is false"]


def test_classify_review_module_failures_are_hard():
    """The review modules were once forgiven as census hints, on the theory that a rune edit stales the pins under them. The pins are now the cycle's own output rather than an assertion the suite reads, so a review-module failure is a real failure like any other."""
    stdout = "\n".join(
        [
            "FAILED rebuild/test_review_build.py::test_totals",
            "FAILED rebuild/test_settle.py::test_x",
            "ERROR rebuild/test_review_ink.py::test_y",
        ]
    )
    outcome = ac.classify_rebuild_output(stdout, 1, "rebuild-contracts")
    assert outcome.check == "rebuild-contracts"
    assert outcome.verdict == "red"
    assert outcome.status == "FAILED (3 unexplained)"
    assert outcome.failed_ids == [
        "rebuild/test_review_build.py::test_totals",
        "rebuild/test_settle.py::test_x",
        "rebuild/test_review_ink.py::test_y",
    ]
    assert not outcome.recordable


def test_classify_rebuild_output_is_lane_blind():
    """The check name rides into the verdict so the record says which suite ran; nothing above it reads the name, so the same output judges the same way in either lane."""
    stdout = "FAILED rebuild/test_settle.py::test_x"
    contracts = ac.classify_rebuild_output(stdout, 1, "rebuild-contracts")
    validators = ac.classify_rebuild_output(stdout, 1, "rebuild-validators")
    assert contracts.check == "rebuild-contracts"
    assert validators.check == "rebuild-validators"
    assert (contracts.status, contracts.failures, contracts.failed_ids) == (
        validators.status,
        validators.failures,
        validators.failed_ids,
    )


def test_dry_run_plan_default():
    plan = ac.build_plan(
        verdicts=Path("verdicts-X.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=1,
        total_bytes=BOX_44_GB,
    )
    assert plan.snapshot_dir == ac.ROOT / "tmp" / "review-pre-abc1234"
    assert plan.carry_out == ac.ROOT / "verdicts-carried-abc1234.json"

    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.pipeline.run_m1",
        "--kernel-threads",
        str(plan.kernel_threads),
    ]
    assert by_name["surface-build"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.review.build",
        "--jobs",
        str(plan.surface_jobs),
    ]
    assert _argv(by_name["plumbing"])[:5] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.verdict_chain",
    ]
    assert by_name["census"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.review.census",
        "--update",
        "--surface",
        str(ac.REVIEW_OUT),
    ]
    assert by_name["gate:rebuild-contracts"].argv == [
        "uv",
        "run",
        "pytest",
        "rebuild/",
        "--lane",
        "contracts",
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        "--tb=no",
        "-rfE",
        "--durations=25",
    ]
    assert by_name["gate:rebuild-validators"].argv == [
        "uv",
        "run",
        "pytest",
        "rebuild/",
        "--lane",
        "validators",
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        "--tb=no",
        "-rfE",
        "--durations=25",
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
    assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", "6"]
    assert plan.conform_jobs == 6

    small = _plan(ncores=4)
    small_by_name = {step.name: step for step in small.steps}
    assert _argv(small_by_name["gate:conform"])[-2:] == ["--jobs", "4"]

    single = _plan(ncores=1)
    single_by_name = {step.name: step for step in single.steps}
    assert _argv(single_by_name["gate:conform"])[-1] == "--conform-only"


def test_dry_run_plan_states_a_surface_width_of_one_in_the_argv():
    plan = _plan(ncores=2)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", "1"]
    assert plan.surface_jobs == 1


def test_dry_run_plan_conform_horizon():
    plan = _plan(conform_horizon=3)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["gate:conform"])[-2:] == ["--conform-horizon", "3"]
    assert plan.conform_horizon == 3

    default = _plan()
    default_by_name = {step.name: step for step in default.steps}
    assert "--conform-horizon" not in _argv(default_by_name["gate:conform"])
    assert default.conform_horizon == ac.CONFORM_HORIZON_DEFAULT


def test_dry_run_plan_skip_conform():
    plan = _plan(skip_conform=True)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert by_name["gate:conform"].note == "SKIPPED (--skip-conform)"
    assert by_name["gate:rebuild-contracts"].argv is not None
    assert by_name["gate:rebuild-validators"].argv is not None


def test_dry_run_plan_runs_the_whole_chain_as_one_step():
    plan = _plan(snapshot_dir=None, short_id="abc1234")
    names = [step.name for step in plan.steps]
    assert names.index("plumbing") == names.index("surface-build") + 1
    assert names.index("census") == names.index("plumbing") + 1
    argv = {step.name: step for step in plan.steps}["plumbing"].argv
    assert argv is not None
    assert argv[:11] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.verdict_chain",
        "--surface",
        str(ac.REVIEW_OUT),
        "--source",
        str(ac.ROOT / "tmp" / "review-pre-abc1234"),
        "v.json",
        "--carry-out",
    ]
    assert argv[11] == str(ac.ROOT / "verdicts-carried-abc1234.json")
    assert "--no-merge" not in argv
    assert plan.do_merge is True


def test_dry_run_plan_no_merge_carries_and_stops():
    plan = _plan(no_merge=True)
    step = {step.name: step for step in plan.steps}["plumbing"]
    assert step.argv is not None
    assert "--no-merge" in step.argv
    assert "--source" in step.argv
    assert "--no-merge" in step.note or "carry only" in step.note
    assert plan.do_merge is False


def test_dry_run_plan_rehearsal_never_touches_the_autosave(tmp_path):
    plan = _plan(review_out=tmp_path / "reh")
    step = {step.name: step for step in plan.steps}["plumbing"]
    assert step.argv is not None
    assert "--no-merge" in step.argv
    assert "--no-complaints" in step.argv
    assert step.argv[step.argv.index("--surface") + 1] == str(tmp_path / "reh")
    assert "rehearsal" in step.note
    assert plan.do_merge is False


def test_dry_run_plan_complaints_rides_inside_the_chain(tmp_path, monkeypatch):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()
    names = [step.name for step in plan.steps]
    assert "complaints" not in names
    step = {step.name: step for step in plan.steps}["plumbing"]
    assert step.argv is not None
    assert "--no-complaints" not in step.argv
    assert "complaint docket" in step.note
    assert plan.complaints_note == ""


def test_the_chain_is_told_to_skip_the_docket_on_rehearsal_first_run_and_a_missing_store(
    tmp_path, monkeypatch
):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    rehearsal = _plan(review_out=tmp_path / "reh")
    step = {step.name: step for step in rehearsal.steps}["plumbing"]
    assert step.argv is not None and "--no-complaints" in step.argv
    assert "rehearsal" in rehearsal.complaints_note

    first = _plan(first_run=True, verdicts=None)
    by_name = {step.name: step for step in first.steps}
    assert by_name["plumbing"].argv is None
    assert "first run" in by_name["plumbing"].note
    assert "first run" in first.complaints_note

    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "missing.json")
    absent = _plan()
    step = {step.name: step for step in absent.steps}["plumbing"]
    assert step.argv is not None and "--no-complaints" in step.argv
    assert "no verdicts store" in absent.complaints_note


def test_the_docket_headline_is_scraped_and_never_fails_the_cycle(tmp_path, monkeypatch):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()

    report, failures = _run_plumbing(
        plan,
        _chain_stdout(
            (
                "complaints",
                [
                    "wrote /x/tmp/complaints-data.json: 3 open complaints (1 fresh / 2 standing) in 2 "
                    "groups — 5 park candidates, 4 approved sharers likely churn if fixed"
                ],
            )
        ),
    )
    assert failures == []
    assert report.complaints_status.startswith("3 open complaints")
    assert report.complaints_ok is True

    report, failures = _run_plumbing(plan, _chain_stdout(("complaints", ["no open complaints"])))
    assert report.complaints_status == "no open complaints"
    assert report.complaints_ok is True

    report, failures = _run_plumbing(
        plan, _chain_stdout(("complaints", ["boom"]), failed="complaints"), returncode=2
    )
    assert report.complaints_status == "FAILED (exit 2) — informational"
    assert report.complaints_ok is False
    assert failures == []


def test_the_plumbing_row_counts_the_carry_and_the_summary_quotes_what_the_fills_wrote(tmp_path, monkeypatch):
    """The chain runs as one child, so what its steps did reaches this process only as the lines they printed. Two of them are the pass's headline for anyone who has just finished a sitting — how many verdicts came forward and how much human queue that left — so they become the row's figure; the rest, a handful of lines each, are quoted under the two summary lines they belong to rather than left for `cycle_summary.json` and the step's own log."""
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()

    report, failures = _run_plumbing(
        plan,
        _chain_stdout(
            (
                "carry",
                [
                    "wrote verdicts-carried-testid.json: 15903 carried onto manifest 2026-09-04T12:00:00Z",
                    "kinds: {'ok': 15903}",
                    "human queue: 81 -> 12 still needing fresh verdicts",
                ],
            ),
            ("merge", ["merged 15903 verdicts into verdicts-autosave.json"]),
            ("echo-fill", ["wrote tmp/echo-fill.json: 4 echo-fill verdicts"]),
            ("echo-merge", ["nothing changed: the autosave already holds all 4 verdicts"]),
            ("standing-fill", ["wrote tmp/standing.json: 7 standing-approval verdicts"]),
            ("standing-merge", ["merged 7 verdicts into verdicts-autosave.json"]),
            ("complaints", ["wrote /x/tmp/complaints-data.json: 3 open complaints in 2 groups"]),
        ),
    )
    assert failures == []
    assert (
        ac.step_figure(report, "plumbing") == "15,903 carried, queue 81 -> 12; 3 open complaints in 2 groups"
    )

    block = ac.summary_cycle_lines(report, plan, [])
    assert "      human queue: 81 -> 12 still needing fresh verdicts" in block
    assert "      wrote tmp/standing.json: 7 standing-approval verdicts" in block
    assert "      wrote tmp/echo-fill.json: 4 echo-fill verdicts" in block
    assert "      merged 15903 verdicts into verdicts-autosave.json" in block
    carry = block.index("  carry output     : " + str(report.carry_out))
    plumbing = next(index for index, line in enumerate(block) if line.startswith("  verdict plumbing"))
    assert carry < block.index("      human queue: 81 -> 12 still needing fresh verdicts") < plumbing


def test_the_plumbing_row_falls_back_to_the_merge_when_no_carry_ran(tmp_path, monkeypatch):
    """The store-only route carries nothing — the surface did not move, so the carry would resolve every unit against itself — and there is no count to report. The row says what did happen instead of reading blank."""
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan(store_only=True)
    report, failures = _run_plumbing(
        plan,
        _chain_stdout(
            ("merge", ["merged 3 verdicts into verdicts-autosave.json"]),
            ("complaints", ["no open complaints"]),
        ),
    )
    assert failures == []
    assert ac.carry_figure(report.carry_lines) == ""
    assert ac.step_figure(report, "plumbing") == "merge merged; no open complaints"


def test_dry_run_plan_skips_the_chain_without_a_carry():
    no_carry = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="abc",
    )
    step = {step.name: step for step in no_carry.steps}["plumbing"]
    assert step.argv is None
    assert step.note == "SKIPPED (--no-carry)"
    first = ac.build_plan(
        verdicts=None,
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=True,
        short_id="abc",
    )
    step = {step.name: step for step in first.steps}["plumbing"]
    assert step.argv is None
    assert step.note == "SKIPPED (first run)"


def test_dry_run_plan_no_carry():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="def5678",
    )
    assert plan.carry_out is None
    by_name = {step.name: step for step in plan.steps}
    assert by_name["plumbing"].argv is None


def test_dry_run_plan_first_run_skips_snapshot_and_carry():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=True,
        short_id="0000000",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["snapshot"].argv is None
    assert by_name["plumbing"].argv is None
    assert plan.carry_out is None


def test_dry_run_plan_skip_gates():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=True,
        first_run=False,
        short_id="abc",
    )
    names = {step.name for step in plan.steps}
    assert "gate:js" not in names
    assert "gate:rebuild-contracts" not in names
    assert "gate:rebuild-validators" not in names
    assert "gate:conform" not in names


def test_render_plan_is_stringable():
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
    )
    text = _plan_text(plan)
    assert "review-pre-abc1234" in text
    assert "rebuild.pipeline.run_m1" in text


def test_every_plan_step_says_what_it_is_for():
    """The banner prints a description on every run, so a step without one prints a bare rule and leaves the reader to guess. The reuse route is the one row whose description is not looked up under its own name: it spawns as run_m1:gates-only and reports under run_m1's row, and what it says has to be the re-adjudication's sentence rather than the build's."""
    for plan in (_plan(), _plan(skip_gates=True), _plan(reuse_run_m1=True, run_m1_note="comparison-side")):
        for step in plan.steps:
            assert step.describe, step.name
            assert step.describe in ac.STEP_DESCRIPTIONS.values(), step.name
    reuse = _plan(reuse_run_m1=True, run_m1_note="comparison-side")
    assert reuse.describe("run_m1") == ac.STEP_DESCRIPTIONS[ac.RUN_M1_REUSE_STEP]
    assert "rebuilding nothing" in reuse.describe("run_m1")
    assert _plan().describe("run_m1") == ac.STEP_DESCRIPTIONS["run_m1"]


def test_a_step_that_spawns_nothing_is_not_automatically_a_skipped_one():
    """The run/skip column reads `skipped`, not `argv is None`, because the snapshot and the retention pass do real work in this process and the `gates` placeholder stands in for five steps at once. Reading the column off argv would file all three under `skip` and make the counts line a lie."""
    plan = _plan()
    by_name = {step.name: step for step in plan.steps}
    for name in ("snapshot", "retention"):
        assert by_name[name].argv is None
        assert by_name[name].skipped is False
    for step in plan.steps:
        if step.argv is not None:
            assert step.skipped is False, step.name
    gates_off = {step.name: step for step in _plan(skip_gates=True).steps}
    assert gates_off["gates"].skipped is True


def test_the_plan_block_counts_its_steps_and_leaves_the_sweep_and_the_validators_lane_undecided():
    """gate:conform and gate:rebuild-validators are the two rows the plan cannot settle: each one's key is taken over the artifacts run_m1 leaves, so a pass that plans the sweep or the lane may still prove it unnecessary once the build has finished. That is why the counts line carries a range — a flat number there would be a promise a legitimate pass breaks. A pass that skips run_m1 outright is the exception in the other direction: nothing rebuilds, `main` has already compared those same keys and found no green for them, so both will certainly run and a range there would be a promise the pass could never reach the top of. The reuse route is the one the validators row is undecided for: a gates-only pass over a contact-allow bless leaves the lane's whole closure where it was, and the plan cannot know that until the pass has run."""
    plan = _plan()
    rows = ac.plan_rows(plan)
    by_name = {row.name: row for row in rows}
    assert by_name["gate:conform"].status == console.STATUS_MAYBE
    assert by_name["gate:rebuild-validators"].status == console.STATUS_MAYBE
    assert by_name["gate:rebuild-contracts"].status == console.STATUS_RUN
    assert by_name["run_m1"].status == console.STATUS_RUN
    assert by_name["gate:conform"].note == ac.CONFORM_MAYBE_NOTE
    assert by_name["gate:rebuild-validators"].note == ac.VALIDATORS_MAYBE_NOTE
    text = _plan_text(plan)
    assert ac.CONFORM_MAYBE_NOTE in _step_lines(text, "gate:conform")
    validators_row = _step_lines(text, "gate:rebuild-validators")
    assert ac.VALIDATORS_MAYBE_NOTE in validators_row
    assert "uv run pytest" in validators_row
    assert console.counts_line(rows) == f"{len(rows)} steps: {len(rows) - 2}–{len(rows)} will run, 0 skipped"

    reuse = _plan(reuse_run_m1=True, run_m1_note="only comparison-side inputs moved")
    reuse_by_name = {row.name: row for row in ac.plan_rows(reuse)}
    assert reuse_by_name["gate:rebuild-validators"].status == console.STATUS_MAYBE
    assert reuse_by_name["gate:conform"].status == console.STATUS_MAYBE

    settled = _plan(
        skip_run_m1=True,
        run_m1_note="build inputs unchanged",
        skip_conform=True,
        conform_note=ac.CONFORM_SKIP_NOTE,
    )
    settled_rows = ac.plan_rows(settled)
    settled_by_name = {row.name: row for row in settled_rows}
    assert settled_by_name["gate:conform"].status == console.STATUS_SKIP
    assert settled_by_name["run_m1"].status == console.STATUS_SKIP
    assert "–" not in console.counts_line(settled_rows)

    text = _plan_text(settled)
    assert console.counts_line(settled_rows) in text
    assert f"SKIPPED ({ac.CONFORM_SKIP_NOTE})" in _step_lines(text, "gate:conform")

    certain = _plan(skip_run_m1=True, run_m1_note="build inputs unchanged")
    certain_rows = ac.plan_rows(certain)
    certain_by_name = {row.name: row for row in certain_rows}
    assert certain_by_name["gate:conform"].status == console.STATUS_RUN
    assert certain_by_name["gate:rebuild-validators"].status == console.STATUS_RUN
    assert certain_by_name["gate:rebuild-validators"].note == "submitted once the surface build settles"
    assert "–" not in console.counts_line(certain_rows)

    fresh = _plan(fresh=True)
    fresh_rows = ac.plan_rows(fresh)
    fresh_by_name = {row.name: row for row in fresh_rows}
    assert fresh_by_name["gate:conform"].status == console.STATUS_RUN
    assert fresh_by_name["gate:conform"].note == ""
    assert fresh_by_name["gate:rebuild-validators"].status == console.STATUS_RUN
    assert fresh_by_name["gate:rebuild-validators"].note == "submitted once the surface build settles"
    assert "–" not in console.counts_line(fresh_rows)


def test_the_plan_block_leads_with_its_arithmetic_and_puts_the_paths_after_the_rows():
    """What a reader came to the top of a pass for is how many steps there are and what each one will run, so the header goes straight into the count and the rows. The paths this pass resolved — where the snapshot lands, which master the carry reads, where the carried file goes — follow them rather than splitting the header from its own arithmetic, and the concurrency block, which answers how the steps share the box, still comes last."""
    plan = _plan()
    lines = ac.render_plan(plan)
    assert lines[0].startswith("artifact cycle ")
    counts = lines.index(console.counts_line(ac.plan_rows(plan)))
    last_row = max(index for index, line in enumerate(lines) if re.match(_PLAN_ROW, line))
    paths = next(index for index, line in enumerate(lines) if line.startswith("  first run "))
    concurrency = next(index for index, line in enumerate(lines) if line.strip().startswith("Concurrency"))
    assert 0 < counts < last_row < paths < concurrency
    assert [line for line in lines if line.startswith("  snapshot dir ")]
    assert [line for line in lines if line.startswith("  carry output ")]


def _built_surface(tmp_path, **totals):
    surface = tmp_path / "review"
    surface.mkdir()
    (surface / "manifest.json").write_text(json.dumps({"totals": totals}))
    return surface


def test_do_surface_build_takes_its_totals_from_the_manifest_the_build_wrote(tmp_path):
    surface = _built_surface(tmp_path, units=15897, rows=81867, batches=16, echo_groups=402)
    report = ac.CycleReport()
    ok = ac._do_surface_build(
        report,
        spawn=lambda name, argv, **k: _step(name, 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=surface,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    assert ok
    assert (report.surface_units, report.surface_rows, report.surface_batches, report.echo_groups) == (
        15897,
        81867,
        16,
        402,
    )


def test_do_surface_build_fails_when_a_clean_build_left_no_manifest(tmp_path, capsys):
    surface = tmp_path / "review"
    surface.mkdir()
    report = ac.CycleReport()
    ok = ac._do_surface_build(
        report,
        spawn=lambda name, argv, **k: _step(name, 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=surface,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    assert not ok
    assert "review.build exited 0 but left no readable manifest.json" in capsys.readouterr().out
    assert report.surface_units is None


def test_do_surface_build_reads_no_totals_from_a_failed_build(tmp_path, capsys):
    """A nonzero review.build says nothing about the manifest beside it — that one is the previous pass's, and reporting its totals as this pass's would be a lie. So the failure short-circuits before the read."""
    surface = _built_surface(tmp_path, units=1, rows=2, batches=3, echo_groups=4)
    report = ac.CycleReport()
    ok = ac._do_surface_build(
        report,
        spawn=lambda name, argv, **k: _step(name, 3),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=surface,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    assert not ok
    assert "review.build exited 3" in capsys.readouterr().out
    assert (report.surface_units, report.surface_rows, report.surface_batches, report.echo_groups) == (
        None,
        None,
        None,
        None,
    )


def _argv(step: ac.Step) -> list[str]:
    assert step.argv is not None
    return step.argv


def _plan(**overrides: Any) -> ac.Plan:
    """A resolved plan over an invented machine: `ncores` decides every CPU-derived width and `total_bytes` the one width memory derives, so a plan's numbers are the same wherever the suite runs. Either is overridable per test the way every other keyword here is."""
    kw: dict[str, Any] = dict(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=Path("/tmp/snap-x"),
        skip_gates=False,
        first_run=False,
        short_id="testid",
        ncores=4,
        total_bytes=BOX_44_GB,
    )
    kw.update(overrides)
    return ac.build_plan(**kw)


def _step(name="x", rc=0, stdout="", stderr=""):
    return ac._StepResult(name, rc, stdout, stderr, 0.0)


def _run_m1_green():
    """What `_do_run_m1` hands back on a build that passed. A stubbed stage returns the verdict and sets the oracle's counts on the report itself, exactly as the real one does now that the two are no longer carried in one object."""
    return ct.CheckVerdict(check="run_m1", verdict="green", status="green", failures=[], failed_ids=[])


def _run_m1_red(*failures):
    return ct.CheckVerdict(
        check="run_m1", verdict="red", status="FAILED", failures=list(failures), failed_ids=[]
    )


def _lane_verdict(check, status="green", failed_ids=()):
    return ct.CheckVerdict(
        check=check,
        verdict="red" if failed_ids else "green",
        status=status,
        failures=[f"rebuild suite: {len(failed_ids)} unexplained failure(s)"] if failed_ids else [],
        failed_ids=list(failed_ids),
        recordable=not failed_ids,
    )


def _conform_verdict(status="green", failures=()):
    return ct.CheckVerdict(
        check="conform",
        verdict="red" if failures else "green",
        status=status,
        failures=list(failures),
        failed_ids=[],
    )


def _pass_run_m1(report, *, spawn, emit, registry, **_):
    report.unmatched = 1
    report.multi_matched = 0
    report.pins_pass = True
    return _run_m1_green()


def _surface_ok(report, *, spawn, emit, registry, review_out, **_):
    report.surface_units = 1
    return True


def _chain_stdout(*sections, fixpoint=True, failed=None):
    """A synthetic verdict_chain stdout: the `[phase] <step>` line each step opens with, that step's own lines, the `[t] <step>` that closes it, and the fixpoint or failure line the driver reads at the end. The two result lines keep the `[chain] ` prefix the chain gives them — they are what the cascade came to rather than work starting — which is what the driver's split relies on to keep a `failed:` line out of the complaints body."""
    lines = []
    for name, body in sections:
        lines.append(console.PHASE + name)
        lines.extend(body)
        lines.append(f"[t] {name} 0.1s")
        if failed == name:
            lines.append(f"{console.FAILED_LINE}{name} (exit 1)")
            break
    if failed is None and fixpoint:
        lines.append(f"{console.FIXPOINT_LINE}witnessed — a re-run of the fill cascade writes nothing")
    return "\n".join(lines) + "\n"


_FULL_CHAIN = (
    ("carry", ["wrote verdicts-carried-abc.json: 51946 carried onto manifest S1", "kinds: {'approve': 5}"]),
    (
        "merge",
        [
            "verdicts-carried-abc.json: 5 added, 0 replaced, 2 kept newer",
            "merged 1 file(s) into verdicts-autosave.json: 5 added, 0 replaced, 2 kept newer; "
            "store holds 7 verdicts (7 effective) on manifest S1",
        ],
    ),
    (
        "echo-fill",
        [
            "wrote verdicts-echo-fill.json: 37 echo-fill verdicts onto manifest S1",
            "no echo group holds disagreeing verdicts",
        ],
    ),
    (
        "echo-merge",
        [
            "merged 1 file(s) into verdicts-autosave.json: 12 added, 0 replaced, 3 kept newer; "
            "store holds 40 verdicts (40 effective) on manifest S1"
        ],
    ),
    (
        "standing-fill",
        [
            "wrote verdicts-standing-fill.json: 25 standing-approval verdicts onto manifest S1",
            "  tea-oy-ligature-break: 25 filled, 0 held for review by except_left",
            "  WARNING: a verdict outside approve/either/identical sits on 1 matched unit — u-9 under "
            "tea-oy-ligature-break (reject); a rule reaching a window the user judged otherwise is the "
            "shape an over-broad rule takes.",
        ],
    ),
    (
        "standing-merge",
        ["nothing changed: the autosave already holds all 65 verdicts (65 effective)."],
    ),
    ("echo-fill-2", ["wrote verdicts-echo-fill.json: 0 echo-fill verdicts onto manifest S1"]),
    ("complaints", ["no open complaints"]),
)


def _run_plumbing(plan, stdout, returncode=0, spy=None):
    """The chain step over a canned stdout, spawned the way `_run_step` spawns it: every line goes through the digest on its way to the step's log, so what the terminal shows here is what a real pass would show — the warnings and the phase pairs, and nothing else."""

    def fake_spawn(name, argv, *, emit, registry, stream):
        if spy is not None:
            spy.append((name, argv))
        for line in stdout.splitlines():
            emit.child_line(name, console.STDOUT, line)
        return _step(name, returncode, stdout=stdout)

    report = ac.CycleReport()
    failures = ac._do_plumbing(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=plan
    )
    return report, failures


def _plumbing_ok(report, *, spawn, emit, registry, plan):
    report.merge_status = "merged"
    report.echo_fill_status = "filled"
    report.echo_merge_status = "merged"
    report.standing_fill_status = "filled"
    report.standing_merge_status = "merged"
    report.standing_merge_lines = ["nothing changed: the autosave already holds all 3 verdicts"]
    report.plumbing_fixpoint = True
    report.complaints_status = "no open complaints"
    report.complaints_ok = True
    report.carry_out = plan.carry_out
    return []


def _census_clean(report, *, spawn, emit, registry, plan):
    report.census_status = "updated (matches the last accepted census)"


def _job_costs_clean(report, *, spawn, emit, registry, plan):
    report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
    report.job_costs_ok = True


def _js_ok(argv, spawn, emit, registry):
    return _step("gate:js", 0)


def _make_ok(argv, spawn, emit, registry):
    return _step("gate:make-test", 0)


def _contracts_green(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
    return _lane_verdict("rebuild-contracts")


def _validators_green(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
    return _lane_verdict("rebuild-validators")


def _conform_green(pool_policy, make_fut, spawn, emit, registry, argv):
    return _conform_verdict()


def _patch_gate_fingerprints(monkeypatch):
    """The gate greens' keys, for a test that only cares that a green was or wasn't recorded. Unstubbed these are the live ones: _run_cycle snapshots them before the gates and _record_gate_greens recomputes them after, and each pass runs git ls-files over the repo and sha256s all of rebuild/, glyph_data/, the fonts, and the baseline TSVs — several seconds per test, and an answer that depends on the working tree rather than on the arrangement the test set up. Whether a moved key withholds the green is its own test."""
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"rfp-{lane}")


def _patch_build_chain(monkeypatch):
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_plumbing", _plumbing_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_do_job_costs", _job_costs_clean)


def test_a_failing_merge_fails_the_cycle(monkeypatch, capsys):
    def failing(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return ["verdict merge failed"]

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _surface_ok)
    monkeypatch.setattr(ac, "_do_plumbing", failing)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.merge_status == "FAILED (exit 1)"
    assert "verdict merge failed" in capsys.readouterr().out


def test_nothing_runs_after_the_carry_fails():
    """The chain stops at its first failing step, and the driver reads the rest of the summary off what the banners never printed."""
    report, failures = _run_plumbing(
        _plan(),
        _chain_stdout(("carry", ["boom"]), failed="carry"),
        returncode=1,
    )
    assert failures == ["carry_verdicts failed"]
    assert report.merge_status == "not run (carry failed)"
    assert report.echo_fill_status == "not run (carry failed)"
    assert report.standing_merge_status == "not run (carry failed)"


def test_a_failing_echo_fill_stops_the_cascade():
    report, failures = _run_plumbing(
        _plan(),
        _chain_stdout(*_FULL_CHAIN[:2], ("echo-fill", ["boom"]), failed="echo-fill"),
        returncode=1,
    )
    assert failures == ["echo-fill failed"]
    assert report.merge_status == "merged"
    assert report.echo_fill_status == "FAILED (exit 1)"
    assert report.echo_merge_status == "not run (echo-fill failed)"
    assert report.standing_fill_status == "not run (echo-fill failed)"
    assert report.standing_merge_status == "not run (echo-fill failed)"


def test_a_failing_echo_merge_stops_the_cascade():
    report, failures = _run_plumbing(
        _plan(),
        _chain_stdout(*_FULL_CHAIN[:3], ("echo-merge", ["boom"]), failed="echo-merge"),
        returncode=1,
    )
    assert failures == ["echo-merge failed"]
    assert report.echo_fill_status == "filled"
    assert report.echo_merge_status == "FAILED (exit 1)"
    assert report.standing_fill_status == "not run (echo-merge failed)"
    assert report.standing_merge_status == "not run (echo-merge failed)"


def test_a_failing_standing_fill_stops_the_cascade():
    report, failures = _run_plumbing(
        _plan(),
        _chain_stdout(*_FULL_CHAIN[:4], ("standing-fill", ["boom"]), failed="standing-fill"),
        returncode=1,
    )
    assert failures == ["standing-fill failed"]
    assert report.standing_fill_status == "FAILED (exit 1)"
    assert report.standing_merge_status == "not run (standing-fill failed)"


def test_a_carry_only_chain_reports_the_fills_as_never_run():
    """--no-merge and rehearsal both stop the chain after the carry, so the fills print no banner and the summary says so rather than claiming a fill that never happened."""
    report, failures = _run_plumbing(_plan(no_merge=True), _chain_stdout(_FULL_CHAIN[0], fixpoint=False))
    assert failures == []
    assert report.merge_status == "not run"
    assert report.echo_fill_status == "not run"
    assert report.echo_merge_status == "not run"
    assert report.standing_fill_status == "not run"
    assert report.standing_merge_status == "not run"


def test_the_driver_reads_a_line_per_step_out_of_one_child(capsys):
    """One subprocess prints for seven steps, and every line the summary shows for a step reaches it, scraped out of that step's own section."""
    spy: list = []
    report, failures = _run_plumbing(_plan(), _chain_stdout(*_FULL_CHAIN), spy=spy)
    assert failures == []
    assert [name for name, _argv in spy] == ["plumbing"]

    assert report.merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.merge_lines)
    assert report.echo_fill_status == "filled"
    assert any(
        line.startswith("wrote verdicts-echo-fill.json: 37 echo-fill verdicts")
        for line in report.echo_fill_lines
    )
    assert report.echo_merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.echo_merge_lines)
    assert report.standing_fill_status == "filled"
    assert any(
        line.startswith("wrote verdicts-standing-fill.json: 25 standing-approval verdicts")
        for line in report.standing_fill_lines
    )
    assert any(line.endswith("held for review by except_left") for line in report.standing_fill_lines)
    assert any(line.startswith("WARNING:") for line in report.standing_fill_lines)
    assert report.standing_merge_status == "merged"
    assert any(line.startswith("nothing changed") for line in report.standing_merge_lines)
    assert any("carried onto manifest" in line for line in report.carry_lines)
    assert report.complaints_status == "no open complaints"
    assert report.plumbing_fixpoint is True


def test_standing_fill_news_keeps_rules_and_drops_steady_state_composed_pairs():
    """Per-rule lines survive whatever their counts — a just-landed rule gets quoted from the summary even at 0 filled — while a composed pair earns its summary line only by filling or holding something, so the quadratic steady-state roll call stays out of both the console block and cycle_summary.json. The tripwire's WARNING is kept whatever else is dropped, and both line shapes are judged the same way: the chain runs the fill in its --open-only form, which prints no already-verdicted column, while a dry run over the whole domain still does."""
    news = ac._standing_fill_news
    assert news("wrote verdicts-standing-fill.json: 25 standing-approval verdicts onto manifest S1")
    assert news("quiet-rule: 0 filled, 12 already verdicted, 0 held for review by except_left")
    assert news("quiet-rule: 0 filled, 0 held for review by except_left")
    assert news("rule-a + rule-b: 2 filled, 0 already verdicted, 0 held for review by except_left")
    assert news("rule-a + rule-b: 0 filled, 3 already verdicted, 1 held for review by except_left")
    assert news("rule-a + rule-b: 2 filled, 0 held for review by except_left")
    assert not news("rule-a + rule-b: 0 filled, 9 already verdicted, 0 held for review by except_left")
    assert not news("rule-a + rule-b: 0 filled, 0 held for review by except_left")
    assert news(
        "WARNING: a verdict outside approve/either/identical sits on 1 matched unit — u-9 under "
        "quiet-rule (reject); a rule reaching a window the user judged otherwise is the shape an "
        "over-broad rule takes."
    )
    assert not news(
        "REACHED NOTHING: quiet-rule matched no window on its own and no composed line credited it."
    )
    assert not news(
        "except_left vocabulary: quiet-rule guards against qsOut, which no window on this surface joins from."
    )
    assert not news("per-rule reach (3 rules):")


def test_a_later_echo_round_folds_into_the_first_rounds_lines():
    """The cascade's second echo pass is the same step run again, so it reports under the same name rather than as a step of its own."""
    stdout = _chain_stdout(
        *_FULL_CHAIN[:6],
        ("echo-fill-2", ["wrote verdicts-echo-fill.json: 3 echo-fill verdicts onto manifest S1"]),
    )
    report, _failures = _run_plumbing(_plan(), stdout)
    assert len(report.echo_fill_lines) == 2
    assert report.echo_fill_lines[-1].startswith("wrote verdicts-echo-fill.json: 3 echo-fill verdicts")


def test_the_disagreement_audit_reaches_the_console(capsys):
    stdout = _chain_stdout(
        (
            "echo-fill",
            [
                "wrote verdicts-echo-fill.json: 0 echo-fill verdicts onto manifest S1",
                "",
                console.WARN + "2 echo groups hold disagreeing verdicts — the same change judged "
                "differently; worth a re-check:",
                "  e-123  #units=u-1,u-2",
                "    u-1       ·Day ~b~ ·Tea                approve   looks right",
                "    u-2       ·Day ~b~ ·Tea                reject    stub too long",
            ],
        )
    )
    _run_plumbing(_plan(), stdout)
    out = capsys.readouterr().out
    assert "warn 2 echo groups hold disagreeing verdicts" in out
    # The per-group roll call stays in the step's log: what the terminal owes a watcher is that the disagreement exists, and the names of the units are what the log is for.
    assert "e-123  #units=u-1,u-2" not in out


def test_the_executor_spawns_the_argv_the_plan_holds():
    """The single authority: build_plan writes each step's command line and the executor runs that one, so rewriting a live step's argv is enough to change what gets spawned — no executor rebuilds its own copy."""
    plan = _plan()
    sentinel = ["uv", "run", "python", "sentinel-chain", "--only-here"]
    {step.name: step for step in plan.steps}["plumbing"].argv = sentinel
    spy: list = []
    _run_plumbing(plan, _chain_stdout(*_FULL_CHAIN), spy=spy)
    assert spy == [("plumbing", sentinel)]


def test_gates_launch_before_run_m1_finishes(monkeypatch):
    record = {}
    js_started = threading.Event()
    make_started = threading.Event()
    release_run_m1 = threading.Event()

    def fake_js(argv, spawn, emit, registry):
        record["js_start"] = time.monotonic()
        js_started.set()
        return _step("gate:js", 0)

    def fake_make(argv, spawn, emit, registry):
        record["make_start"] = time.monotonic()
        make_started.set()
        return _step("gate:make-test", 0)

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        release_run_m1.wait()
        record["run_m1_finish"] = time.monotonic()
        return _run_m1_green()

    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
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


def test_both_rebuild_lanes_wait_for_run_m1_pass(monkeypatch):
    record = {}

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        record["run_m1_finish"] = time.monotonic()
        return _run_m1_green()

    def fake_contracts(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        record["contracts_invoked"] = time.monotonic()
        return _lane_verdict("rebuild-contracts")

    def fake_validators(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        record["validators_invoked"] = time.monotonic()
        return _lane_verdict("rebuild-validators")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_validators_task", fake_validators)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert record["contracts_invoked"] >= record["run_m1_finish"]
    assert record["validators_invoked"] >= record["run_m1_finish"]


def test_both_rebuild_lanes_are_skipped_when_run_m1_fails(monkeypatch, capsys):
    called = {"contracts": False, "validators": False}

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        return None

    def fake_contracts(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        called["contracts"] = True
        return _lane_verdict("rebuild-contracts")

    def fake_validators(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        called["validators"] = True
        return _lane_verdict("rebuild-validators")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_validators_task", fake_validators)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert called == {"contracts": False, "validators": False}
    assert report.gate_contracts == "not run (run_m1 gate failed)"
    assert report.gate_validators == "not run (run_m1 gate failed)"
    assert report.gate_conform == "not run (run_m1 gate failed)"
    assert rc == 1
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_pool_queue_serializes_the_contracts_lane_after_make_test(monkeypatch):
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()

    def fake_make(argv, spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        if name == "gate:rebuild-contracts":
            record["contracts_start"] = time.monotonic()
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

    assert record["contracts_start"] >= record["make_finish"]


def test_pool_overlap_starts_the_contracts_lane_before_make_test_done(monkeypatch):
    record = {}
    release_make = threading.Event()
    contracts_started = threading.Event()

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        record["run_m1_finish"] = time.monotonic()
        return _run_m1_green()

    def fake_make(argv, spawn, emit, registry):
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        if name == "gate:rebuild-contracts":
            record["contracts_start"] = time.monotonic()
            contracts_started.set()
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
    contracts_started.wait()
    release_make.set()
    t.join()

    assert record["contracts_start"] < record["make_finish"]
    assert record["contracts_start"] >= record["run_m1_finish"]


def test_pool_queue_runs_make_test_then_conform_then_contracts_then_validators(monkeypatch):
    """The whole queue chain, in one run: only one heavy pool is hot at a time, and the short contracts lane goes ahead of the half-hour validators lane so a code error fails the cycle before the long one starts."""
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    release_conform = threading.Event()
    conform_running = threading.Event()
    contracts_started = threading.Event()
    release_contracts = threading.Event()
    validators_started = threading.Event()

    def fake_make(argv, spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        conform_running.set()
        release_conform.wait()
        record["conform_finish"] = time.monotonic()
        return _conform_verdict()

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        if name == "gate:rebuild-contracts":
            record["contracts_start"] = time.monotonic()
            record["contracts_argv"] = argv
            contracts_started.set()
            release_contracts.wait()
            record["contracts_finish"] = time.monotonic()
        if name == "gate:rebuild-validators":
            record["validators_start"] = time.monotonic()
            record["validators_argv"] = argv
            validators_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
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
    assert "contracts_start" not in record
    release_make.set()
    assert not contracts_started.wait(0.2)
    release_conform.set()
    contracts_started.wait()
    assert not validators_started.wait(0.2)
    release_contracts.set()
    validators_started.wait()
    t.join()

    assert record["contracts_start"] >= record["conform_finish"]
    assert record["contracts_start"] >= record["make_finish"]
    assert record["validators_start"] >= record["contracts_finish"]
    assert record["contracts_argv"] == ac.rebuild_lane_argv("contracts")
    assert record["validators_argv"] == ac.rebuild_lane_argv("validators")
    assert report.gate_contracts == "green"
    assert report.gate_validators == "green"
    assert box["rc"] == 0


def test_pool_queue_contracts_falls_back_to_make_test_when_conform_skipped(monkeypatch):
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    contracts_started = threading.Event()

    def fake_make(argv, spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        if name == "gate:rebuild-contracts":
            record["contracts_start"] = time.monotonic()
            contracts_started.set()
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
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
    assert not contracts_started.wait(0.2)
    release_make.set()
    contracts_started.wait()
    t.join()

    assert record["contracts_start"] >= record["make_finish"]
    assert report.gate_conform == "skipped (--skip-conform)"
    assert report.gate_contracts == "green"
    assert report.gate_validators == "green"
    assert box["rc"] == 0


def test_the_gate_pool_seats_every_gate_task_at_once():
    """Under the queue policy a parked task holds its worker for the whole wait — conform on make-test, contracts on both, validators on all three — so the pool seats every gate task at once, with two seats to spare. The chain cannot actually deadlock at a smaller width — submission order matches the parking order and the pool is FIFO, so a task only ever parks on a future already seated or done — but a seat short of the task count would serialize a wait behind an unrelated task's completion, which is the queueing this pool exists not to do."""
    gate_tasks = (
        ac._gate_js_task,
        ac._gate_make_test_task,
        ac._gate_conform_task,
        ac._gate_contracts_task,
        ac._gate_validators_task,
    )
    assert ac._GATE_POOL_WORKERS == len(gate_tasks) + 2


def test_summary_exact_under_out_of_order_completion(monkeypatch, capsys):
    ev_js = threading.Event()
    ev_make = threading.Event()
    ev_contracts = threading.Event()
    ev_validators = threading.Event()

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        report.unmatched = 7777
        report.multi_matched = 0
        report.pins_pass = True
        return _run_m1_green()

    def fake_surface(report, *, spawn, emit, registry, review_out, **_):
        report.surface_units = 15903
        report.surface_rows = 81894
        report.surface_batches = 16
        report.echo_groups = 42
        report.step_seconds["surface-build"] = 61.0
        return True

    def fake_js(argv, spawn, emit, registry):
        ev_js.wait()
        return _step("gate:js", 0)

    def fake_make(argv, spawn, emit, registry):
        ev_make.wait()
        return _step("gate:make-test", 0)

    def fake_contracts(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        ev_contracts.wait()
        return _lane_verdict("rebuild-contracts", "green (annotated)")

    def fake_validators(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        ev_validators.wait()
        return _lane_verdict("rebuild-validators")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", fake_surface)
    monkeypatch.setattr(ac, "_do_plumbing", _plumbing_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_validators_task", fake_validators)
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
    ev_validators.set()
    ev_contracts.set()
    ev_js.set()
    t.join()

    assert report.surface_units == 15903
    assert report.surface_rows == 81894
    assert report.surface_batches == 16
    assert report.echo_groups == 42
    assert report.unmatched == 7777
    assert report.gate_js == "green"
    assert report.gate_make_test == "green"
    assert report.gate_contracts == "green (annotated)"
    assert report.gate_validators == "green"
    assert report.gate_conform == "green"
    out = capsys.readouterr().out
    assert out.count("ARTIFACT CYCLE SUMMARY") == 1
    assert "15,903 units, 81,894 rows" in out
    assert "green (annotated)" in out


_CHILD_SCRIPT = (
    "import sys\n"
    "tag = sys.argv[1]\n"
    "for i in range(200):\n"
    "    print(f'{tag}-out-{i:04d}', flush=True)\n"
    "    print(f'{tag}-err-{i:04d}', file=sys.stderr, flush=True)\n"
)

_TWO_STREAM_CHILD = "import sys; print('on stdout'); print('on stderr', file=sys.stderr); sys.exit({rc})"


def _step_number(plan: ac.Plan, name: str) -> int:
    return [step.name for step in plan.steps].index(name) + 1


def test_a_pass_files_one_log_per_step_beside_its_plan_and_a_copy_of_the_terminal(tmp_path, capsys):
    """What the terminal does not show still has to be somewhere, and that somewhere is one directory per run: the plan as it was printed, a byte copy of the terminal, and a log per step holding both of that child's streams in arrival order with the stderr ones tagged. `latest` points at it so an agent tailing a run never has to know the stamp."""
    plan = _plan()
    root = tmp_path / "build-logs"
    log_dir = root / "20260101T000000Z-testid"
    registry = ac._ChildRegistry()
    with console.Digest(steps=[step.name for step in plan.steps], log_dir=log_dir) as digest:
        digest.plan_block(ac.render_plan(plan))
        ac._run_step(
            "gate:js",
            [sys.executable, "-c", _TWO_STREAM_CHILD.format(rc=0)],
            emit=digest,
            registry=registry,
            stream=False,
        )

    assert (log_dir / console.PLAN_TXT).read_text().startswith("artifact cycle")
    step_log = log_dir / f"{_step_number(plan, 'gate:js'):02d}-gate-js.log"
    assert sorted(step_log.read_text().splitlines()) == ["on stdout", "stderr| on stderr"]
    terminal = (log_dir / console.TERMINAL_LOG).read_text()
    assert "gate:js" in terminal and "Runs the review app's node test suite" in terminal
    assert (root / console.LATEST_LINK).resolve() == log_dir.resolve()


def test_a_failed_step_replays_its_whole_output_under_its_own_banner(tmp_path, capsys):
    """The path the captured-and-discarded gates never had. A child that fails has said everything it is going to say already, and a summary line naming an exit code sends the reader to a log they have to find; replaying it under the banner puts it where they are already looking. The dump comes out of the spawn and the closing line out of the stage that knows the figure, so the replay is above the close rather than after it."""
    registry = ac._ChildRegistry()
    report = ac.CycleReport()
    with console.Digest(log_dir=tmp_path / "logs") as digest:
        result = ac._run_step(
            "gate:conform",
            [sys.executable, "-c", _TWO_STREAM_CHILD.format(rc=3)],
            emit=digest,
            registry=registry,
            stream=False,
        )
        ac._close_step(digest, report, "gate:conform", result)
    assert result.returncode == 3
    lines = capsys.readouterr().out.splitlines()
    assert "on stdout" in lines
    assert "stderr| on stderr" in lines
    assert "FAILED (exit 3)" in lines[-1] and "gate:conform" in lines[-1]
    assert lines.index("stderr| on stderr") < len(lines) - 1


def test_the_reuse_route_banners_under_the_plans_run_m1_row(tmp_path, capsys):
    """`make cycle-timings --by-step` buckets on the step name, so a seconds-long re-adjudication has to spawn under its own name or it lands in the row that says what a full M1 build costs. What a reader watching the pass wants is the opposite — the row the plan showed them — so the alias resolves the one to the other, banner, log filename and step column alike."""
    plan = _plan(reuse_run_m1=True, run_m1_note="only comparison-side inputs moved")
    log_dir = tmp_path / "logs"
    registry = ac._ChildRegistry()
    report = ac.CycleReport()
    report.unmatched = 12
    report.pins_pass = True
    with console.Digest(
        steps=[step.name for step in plan.steps], log_dir=log_dir, aliases=ac.STEP_ALIASES
    ) as digest:
        result = ac._run_step(
            ac.RUN_M1_REUSE_STEP,
            [sys.executable, "-c", "print('re-adjudicating')"],
            emit=digest,
            registry=registry,
            stream=False,
        )
        ac._close_step(digest, report, ac.RUN_M1_REUSE_STEP, result, "ok")
    out = capsys.readouterr().out
    assert f"step {_step_number(plan, 'run_m1')} of {len(plan.steps)}  run_m1  step" in out
    assert ac.RUN_M1_REUSE_STEP not in out
    assert "ok  12 unmatched, pins pass" in out
    assert (log_dir / f"{_step_number(plan, 'run_m1'):02d}-run_m1.log").read_text() == "re-adjudicating\n"


def test_two_verbatim_children_interleave_between_lines_and_never_inside_one(capsys):
    """Two real children, both surfacing verbatim, through one digest. Every line either arrives whole or does not arrive: cross-line interleave is expected and harmless, a line spliced into another is the failure this serialization exists to prevent."""
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
    pattern = re.compile(r"^(childA|childB)-(out|err)-\d{4}$")
    body = [line for line in out.splitlines() if line.startswith("child")]
    assert len(body) == 800
    for line in body:
        assert pattern.match(line) is not None, line


@pytest.mark.parametrize("lane", ac.REBUILD_LANES)
def test_a_rebuild_lane_stays_captured_and_parses_failures(lane, capsys):
    stdout = "\n".join(
        [
            "FAILED rebuild/test_unknown_thing.py::test_x - boom",
            "ERROR rebuild/test_boom.py::test_y",
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
    argv = ac.rebuild_lane_argv(lane)
    if lane == "contracts":
        outcome = ac._gate_contracts_task("overlap", None, None, fake_spawn, emit, registry, argv)
    else:
        outcome = ac._gate_validators_task("overlap", None, None, None, fake_spawn, emit, registry, argv)

    assert seen["name"] == f"gate:rebuild-{lane}"
    assert seen["stream"] is False
    assert outcome.check == f"rebuild-{lane}"
    assert len(outcome.failed_ids) == 3
    assert outcome.status == "FAILED (3 unexplained)"

    report = ac.CycleReport()
    failures = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: outcome)
        futures = (fut, None) if lane == "contracts" else (None, fut)
        ac._join_gates(report, failures, None, *futures, None, None, emit)
    status = report.gate_contracts if lane == "contracts" else report.gate_validators
    assert status == "FAILED (3 unexplained)"

    out = capsys.readouterr().out
    assert not any(line.startswith(f"[gate:rebuild-{lane}]") for line in out.splitlines())
    assert f"hard rebuild failure ({lane}): rebuild/test_boom.py::test_y" in out


def test_gate_make_test_says_so_when_the_font_suite_stood_itself_down(capsys):
    """`make test` exits zero whether it ran the suite or stood down on its own green record, so a row closed off the exit code alone tells a watcher the font suite ran on a pass where it tested nothing. The wrapper says which it did in its first line, and both the closing line and the table row carry that."""
    stood_down = (
        "make test: SKIPPED — input closure unchanged since its last green run (2026-09-04T12:00:00Z). "
        "Run `make test FORCE=1` to run it anyway."
    )
    emit = ac._Emitter()
    result = ac._gate_make_test_task(
        ["make", "test"],
        lambda name, argv, *, emit, registry, stream: _step(name, 0, stdout=stood_down),
        emit,
        ac._ChildRegistry(),
    )
    assert ac.make_test_self_skipped(result.stdout)
    assert ac.MAKE_TEST_SELF_SKIP_STATUS in capsys.readouterr().out

    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_gates(report, failures, None, None, None, None, pool.submit(lambda: result), emit)
    assert failures == []
    assert report.gate_make_test == ac.MAKE_TEST_SELF_SKIP_STATUS
    assert report.gate_make_test_green is True
    row = {row.name: row for row in ac.summary_rows(report, _plan(), retention_ran=False)}
    assert row["gate:make-test"].outcome == "ok"
    assert row["gate:make-test"].figure == ac.MAKE_TEST_SELF_SKIP_STATUS

    ran = ac._gate_make_test_task(
        ["make", "test"],
        lambda name, argv, *, emit, registry, stream: _step(
            name, 0, stdout="make test: green — closure fingerprint recorded in .make-test-green.json"
        ),
        emit,
        ac._ChildRegistry(),
    )
    assert not ac.make_test_self_skipped(ran.stdout)
    assert ac.MAKE_TEST_SELF_SKIP_STATUS not in capsys.readouterr().out


def test_a_failed_gate_never_restates_its_outcome_as_its_figure(capsys):
    """`FAILED  FAILED (exit 1)` spent the table's widest column on the word already in the column beside it. What is left is the part the outcome never carried."""
    emit = ac._Emitter()
    ac._close_gate(emit, "gate:js", _step("gate:js", 1))
    ac._close_gate(
        emit,
        "gate:rebuild-contracts",
        _step("gate:rebuild-contracts", 1),
        _lane_verdict("rebuild-contracts", "FAILED (3 unexplained)", failed_ids=["a", "b", "c"]),
    )
    ac._close_gate(emit, "gate:conform", _step("gate:conform", 0))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")]
    assert lines[0].endswith("FAILED  exit 1")
    assert lines[1].endswith("FAILED  3 unexplained")
    assert lines[2].rstrip().endswith("ok")


def test_classify_rebuild_reads_colored_pytest_output():
    """Under FORCE_COLOR (as set by the agent harness) pytest wraps its FAILED lines in ANSI escapes; the classifier must still parse the failing ids out of them instead of reporting only the exit-code placeholder."""
    colored = "\x1b[31mFAILED\x1b[0m rebuild/test_settle.py::\x1b[1mtest_x\x1b[0m - x"
    outcome = ac.classify_rebuild_output(colored, 1, "rebuild-validators")
    assert outcome.failed_ids == ["rebuild/test_settle.py::test_x"]
    assert outcome.status == "FAILED (1 unexplained)"


def test_failure_funnels_from_concurrent_branch(monkeypatch, capsys):
    def fake_surface(report, *, spawn, emit, registry, review_out, **_):
        report.surface_units = 100
        return True

    def fake_make(argv, spawn, emit, registry):
        return _step("gate:make-test", 1)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", fake_surface)
    monkeypatch.setattr(ac, "_do_plumbing", _plumbing_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
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
    def raising_js(argv, spawn, emit, registry):
        raise FileNotFoundError("node not found")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_gate_js_task", raising_js)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_js == "FAILED (exception)"
    assert report.gate_make_test == "green"
    assert report.gate_contracts == "green"
    assert report.gate_validators == "green"
    out = capsys.readouterr().out
    assert out.count("ARTIFACT CYCLE SUMMARY") == 1
    assert "gate:js raised: FileNotFoundError('node not found')" in out


def test_queue_policy_rebuild_lanes_run_when_make_test_task_raises(monkeypatch, capsys):
    def raising_make(argv, spawn, emit, registry):
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
    assert report.gate_contracts == "green"
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_queue_policy_rebuild_lanes_run_when_conform_task_raises(monkeypatch, capsys):
    def raising_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        raise FileNotFoundError("conform pool blew up")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", raising_conform)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_conform == "FAILED (exception)"
    assert report.gate_contracts == "green"
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_run_m1_failure_still_collects_make_test(monkeypatch, capsys):
    def fake_run_m1(report, *, spawn, emit, registry, **_):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert report.gate_make_test == "green"
    assert report.gate_contracts == "not run (run_m1 gate failed)"
    assert report.gate_validators == "not run (run_m1 gate failed)"
    assert report.gate_conform == "not run (run_m1 gate failed)"
    assert rc == 1
    assert capsys.readouterr().out.count("ARTIFACT CYCLE SUMMARY") == 1


def test_keyboard_interrupt_terminates_children_and_returns_130(monkeypatch, capsys):
    registry = ac._ChildRegistry()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    registry.add(proc)

    def boom(report, *, spawn, emit, registry, **_):
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
        "gate:rebuild-contracts",
        [sys.executable, "-c", script],
        emit=ac._Emitter(),
        registry=registry,
        stream=False,
    )
    assert result.returncode == 130
    assert result.stdout == ""
    assert not marker.exists()


def test_run_step_measures_the_child_peak_rss(capsys):
    emit = ac._Emitter()
    result = ac._run_step(
        "gate:js",
        [sys.executable, "-c", "x = bytearray(64 * 1024 * 1024)"],
        emit=emit,
        registry=ac._ChildRegistry(),
        stream=False,
    )
    ac._close_gate(emit, "gate:js", result)
    assert result.returncode == 0
    assert result.peak_rss_bytes is not None and result.peak_rss_bytes > 64 * 1024 * 1024
    closing = [line for line in capsys.readouterr().out.splitlines() if "  ok  rss " in line]
    assert len(closing) == 1
    assert closing[0].endswith(f"ok  rss {console.fmt_rss(result.peak_rss_bytes)}")
    assert "gate:js" in closing[0]


def test_a_step_environment_is_an_overlay_and_not_a_replacement():
    """What a step states is added to this process's environment for that child alone: the child sees the stated variable and everything else it would have inherited, and this process never sees the stated one at all."""
    probe = "import os; print(os.environ.get('AMS_PROBE_WIDTH'), 'PATH' in os.environ)"
    result = ac._run_step(
        "probe",
        [sys.executable, "-c", probe],
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        stream=False,
        env={"AMS_PROBE_WIDTH": "3"},
    )
    assert result.stdout.strip() == "3 True"
    assert "AMS_PROBE_WIDTH" not in os.environ


def test_sweep_job_budget_is_one_process_per_acceptance_configuration():
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS

    assert ac.sweep_job_budget(12) == len(ACCEPTANCE_CONFIGS)
    assert ac.sweep_job_budget(len(ACCEPTANCE_CONFIGS)) == len(ACCEPTANCE_CONFIGS)
    assert ac.sweep_job_budget(3) == 3
    assert ac.sweep_job_budget(1) == 1


class TestTheSurfaceBuildWidth:
    """Both bounds get an assertion, because which of them binds is the whole design: the cap is what holds the fan-out where widening stops paying on a box with room to spare, and the division is what protects the box that has none."""

    def test_the_cap_binds_where_the_box_has_room_to_spare(self):
        """A box that could hold dozens of these workers is given eight, because the argument against the ninth is not memory at all — past that width the build stops scaling and a further worker buys a duplicated subset table and nothing else."""
        assert (
            ac.surface_job_budget(skip_gates=True, ncores=12, total_bytes=1_000_000_000_000)
            == ac.SURFACE_JOBS_CAP
        )

    def test_a_box_with_fewer_cores_than_the_cap_gets_its_cores(self):
        """The cap and the core count sit in one `min()` because neither is a memory fact, and the two cores gate:make-test's pool holds come out of the same place: a five-core box runs five workers alone and three beside that pool, on a box neither arm can run out of memory on."""
        assert ac.surface_job_budget(skip_gates=True, ncores=5, total_bytes=1_000_000_000_000) == 5
        assert ac.surface_job_budget(skip_gates=False, ncores=5, total_bytes=1_000_000_000_000) == 3

    def test_memory_binds_before_the_cap_once_the_box_is_small_enough(self):
        """The direction that makes deriving this width worth doing: the 48 GiB box has twelve cores' worth of permission, and what it runs is the pool that fits beside the parent's own pile rather than the eight a core clamp handed every box alike. Both bounds are inequalities because both surface constants are readings to keep current: a re-seed may move the width, but it must neither floor this box nor hand it the cap."""
        width = ac.surface_job_budget(skip_gates=True, ncores=12, total_bytes=BOX_48_GIB)
        assert 1 < width < ac.SURFACE_JOBS_CAP

    def test_the_pytest_pool_comes_off_the_box_before_the_division(self):
        """A cycle runs this build beside gate:make-test's pool rather than alone, so the pool's bytes join the co-resident term and its two cores come off the cap before anything divides. Asserted at the fit-terms seam rather than over an invented box: with a worker priced at its width-two peak, no machine in the fleet is roomy enough for the subtraction to move the resulting width, and a box invented to sit exactly where it would is a magic number every re-seed has to re-tune."""
        solo = ac._surface_fit_terms(skip_gates=True, skip_make_test=False, ncores=9)
        beside = ac._surface_fit_terms(skip_gates=False, skip_make_test=False, ncores=9)
        assert solo == (ac.SURFACE_WORKER_BYTES, ac.SURFACE_PARENT_BYTES, ac.SURFACE_JOBS_CAP)
        assert beside == (
            ac.SURFACE_WORKER_BYTES,
            ac.SURFACE_PARENT_BYTES + ac._font_suite_worker_bytes() * ac.make_test_pool_width(ncores=9),
            ac.SURFACE_JOBS_CAP - 1,
        )

    def test_the_floor_answers_one_on_a_box_that_cannot_hold_a_worker(self):
        """A box with no budget left after its reserve floors at one in both arms, and that is the serial build rather than a refusal: at width one there is no pool, every fragment exists once instead of twice, and the build is the cheapest it can be on a box that has outgrown the pooled shape."""
        assert ac.surface_job_budget(skip_gates=True, ncores=12, total_bytes=8_000_000_000) == 1
        assert ac.surface_job_budget(skip_gates=False, ncores=12, total_bytes=8_000_000_000) == 1

    def test_this_box_no_longer_takes_eight_surface_workers(self):
        """The regression this width was rewritten for. On the ten-core 32 GiB Mac that ran the 2026-08-27 full-fresh pass the old core clamp answered eight — ten cores less the pool's two, which met the cap exactly — and that pass read 17.76 GB as the widest single process under the step, a figure that could only ever see the parent and never the eight workers beside it. Both bounds are asserted as inequalities rather than as today's figure, because both surface constants are readings to keep current and re-seeding either must not have to come back here."""
        width = ac.surface_job_budget(skip_gates=False, ncores=10, total_bytes=BOX_32_GIB)
        assert width < ac.SURFACE_JOBS_CAP
        assert width < 10 - 2

    def test_the_printed_derivation_is_the_one_that_produced_the_width(self):
        """The plan line and the `--jobs` help quote a sentence, and a sentence that disagreed with the number beside it would be worse than none: both come from one resolution of the same three terms, so the clause opens with the width it explains."""
        for skip_gates in (False, True):
            width = ac.surface_job_budget(skip_gates=skip_gates, ncores=10, total_bytes=BOX_48_GIB)
            derivation = ac.surface_job_derivation(skip_gates=skip_gates, ncores=10, total_bytes=BOX_48_GIB)
            assert derivation.startswith(f"{width} at ")


def test_both_job_budgets_answer_the_cgroup_allowance_rather_than_the_hosts_core_count(monkeypatch):
    """A CPU quota is invisible to `os.cpu_count()`, so a budget that read it would give a two-core allowance on a many-core host a sweep process per acceptance configuration and a surface build at the cap — every one of them a core the process may not run on. Both budgets probe through `usable_cores`, and what stands in for the box here is the real probe over an invented cgroup root, so the allowance is a fixture rather than a stub: two cores sits under both caps, so each budget answers the allowance itself. The surface budget also divides an invented terabyte box, so its memory arithmetic never binds and the core clamp is the whole assertion. A stated `ncores` still outranks the probe, which is what keeps `build_plan`'s explicit threading its own."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
    from rebuild.tools import memory_budget

    host = os.process_cpu_count() or os.cpu_count() or 1
    probe = memory_budget.usable_cores
    root = REPO_ROOT / "rebuild" / "fixtures" / "memory_budget" / "container-v2"
    allowed = probe(root)
    monkeypatch.setattr(memory_budget, "usable_cores", functools.partial(probe, root))
    assert allowed == min(host, 2) < min(len(ACCEPTANCE_CONFIGS), ac.SURFACE_JOBS_CAP)
    assert ac.sweep_job_budget() == allowed
    assert ac.surface_job_budget(skip_gates=True, total_bytes=1_000_000_000_000) == allowed
    assert ac.sweep_job_budget(12) == len(ACCEPTANCE_CONFIGS)
    assert (
        ac.surface_job_budget(skip_gates=True, ncores=12, total_bytes=1_000_000_000_000)
        == ac.SURFACE_JOBS_CAP
    )


def test_make_test_pool_width_is_the_width_the_surface_budget_leaves_it():
    """The pool the cycle starts is the pool the cycle reserved for: surface_job_budget hands two cores away and prices the same pool's bytes as a co-resident term in the one budget, so two workers is what gate:make-test is handed back, and a box too small for that floors the two together at one rather than letting the pool outgrow the reservation."""
    assert ac.make_test_pool_width(ncores=12) == ac.MAKE_TEST_POOL_WORKERS
    assert ac.make_test_pool_width(ncores=6) == ac.MAKE_TEST_POOL_WORKERS
    assert ac.make_test_pool_width(ncores=1) == 1


def test_a_stated_pool_width_is_the_width_the_cycle_reserves_by(monkeypatch):
    """PYTEST_XDIST_AUTO_NUM_WORKERS is not something the cycle may narrow — the child inherits this process's environment, so a width already stated here is what that pool is going to take whatever the cycle would have preferred. Reserving by it is the only way the two stay one number."""
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "9")
    assert ac.make_test_pool_width(ncores=1) == 9
    assert ac.kernel_threads_budget(ncores=12, total_bytes=BOX_44_GB) == 8
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "64")
    assert ac.kernel_threads_budget(ncores=12, total_bytes=BOX_44_GB) == 4


def test_kernel_threads_budget_takes_the_pytest_pool_off_the_box_first():
    """The fan-out's width answers for the machine it will actually run on: a cycle runs it beside gate:make-test's pool rather than alone, so that pool's bytes come off this box before it is divided by a configuration, and on a box where those bytes are the difference between fitting a configuration and not the width lands one below the solo one. Both numbers move together if CONFIG_PEAK_BYTES is ever re-measured, and the box has to be re-chosen with them: 44 GB separated the arms at the pre-lever divisor and fits eight or more either way at this one, which is why the reservation is asserted on 36 GB."""
    solo = ac.kernel_threads_budget(skip_make_test=True, ncores=8, total_bytes=BOX_36_GB)
    beside = ac.kernel_threads_budget(ncores=8, total_bytes=BOX_36_GB)
    assert (solo, beside) == (7, 6)


def test_kernel_threads_budget_never_narrows_a_stated_kernel_width(monkeypatch):
    """AMS_KERNEL_THREADS is what someone reaches for to keep a build out of swap, so it outranks every derivation here, this reservation included."""
    monkeypatch.setenv("AMS_KERNEL_THREADS", "5")
    assert ac.kernel_threads_budget(ncores=8, total_bytes=BOX_44_GB) == 5
    assert ac.kernel_threads_budget(skip_make_test=True, ncores=8, total_bytes=BOX_44_GB) == 5


def test_a_plan_reserves_for_the_pytest_pool_only_when_that_gate_runs():
    """An auto-skipped gate and --skip-gates are the same fact — no pool is going to be co-resident — so the fan-out gets the whole box back rather than paying for a pool that never starts."""
    assert _plan(ncores=8, total_bytes=BOX_36_GB).kernel_threads == 6
    assert (
        _plan(
            ncores=8, total_bytes=BOX_36_GB, skip_make_test=True, make_test_note="closure unchanged"
        ).kernel_threads
        == 7
    )
    assert _plan(ncores=8, total_bytes=BOX_36_GB, skip_gates=True).kernel_threads == 7


def test_dry_run_renders_concurrency():
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=BOX_44_GB,
    )
    text = _plan_text(plan)
    assert "pool policy: queue" in text
    assert "Lane t0" in text
    assert "Lane build" in text
    assert "Lane rebuild-contracts" in text
    assert "Lane rebuild-validators" in text
    assert "Lane conform" in text
    assert "Lane kernel" not in text
    assert (
        "surface-build -> submit gate:rebuild-contracts, gate:rebuild-validators -> plumbing -> census"
        in text
    )
    assert "QUEUED behind gate:make-test (queue policy — one heavy pool at a time)" in text
    assert "Lane rebuild-contracts           : submitted once the surface build settles;" in text
    assert "QUEUED behind gate:conform (queue policy — one heavy pool at a time)" in text
    assert "Lane rebuild-validators          : submitted once the surface build settles;" in text
    assert (
        "QUEUED behind gate:rebuild-contracts, whose chain already waits on gate:conform and gate:make-test"
        in text
    )
    assert "run_m1 sweeps --jobs             : 6" in text
    assert "run_m1 --kernel-threads          : " in text
    auto_skipped = _plan_text(_plan(skip_conform=True, conform_note=ac.CONFORM_SKIP_NOTE))
    assert f"Lane conform                     : SKIPPED ({ac.CONFORM_SKIP_NOTE})" in auto_skipped
    assert "Lane conform                     : SKIPPED (--skip-conform)" in _plan_text(
        _plan(skip_conform=True)
    )
    surface_width = ac.surface_job_budget(skip_gates=False, ncores=12, total_bytes=BOX_44_GB)
    assert f"surface-build --jobs             : {surface_width}" in text
    _per_unit, coresident, _cap = ac._surface_fit_terms(skip_gates=False, skip_make_test=False, ncores=12)
    assert f"less {format_gb(coresident)} GB co-resident" in text

    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[1:6] == ["run", "python", "-m", "rebuild.pipeline.run_m1", "--jobs"]
    # Every width is stated on the command line, one included. A width of one that emitted no flag would hand the child its own default — the unreserved arm of the same budget, a different number wherever the pool subtraction changes the answer — and the memory term makes one a width the arithmetic actually reaches.
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", str(surface_width)]


def test_dry_run_skip_gates_appends_jobs_budgets():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=True,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=BOX_44_GB,
    )
    by_name = {step.name: step for step in plan.steps}
    solo_width = ac.surface_job_budget(skip_gates=True, ncores=12, total_bytes=BOX_44_GB)
    assert _argv(by_name["run_m1"])[5:7] == ["--jobs", "6"]
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", str(solo_width)]
    assert "run_m1 sweeps --jobs 6" in _plan_text(plan)
    assert f"surface-build --jobs {solo_width}" in _plan_text(plan)

    default_plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=BOX_44_GB,
    )
    default_by_name = {step.name: step for step in default_plan.steps}
    gated_width = ac.surface_job_budget(skip_gates=False, ncores=12, total_bytes=BOX_44_GB)
    assert _argv(default_by_name["run_m1"])[5:7] == ["--jobs", "6"]
    assert _argv(default_by_name["surface-build"])[-2:] == ["--jobs", str(gated_width)]


def test_review_out_rehearsal_plan(monkeypatch, tmp_path):
    rehearsal_out = tmp_path / "reh"
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        snapshot_dir=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        review_out=rehearsal_out,
    )
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["surface-build"])[-2:] == ["--out", str(rehearsal_out)]
    assert by_name["census"].argv is None
    assert by_name["census"].note == "SKIPPED (rehearsal: the checked-in pins track the live surface)"
    argv = _argv(by_name["plumbing"])
    assert argv[argv.index("--surface") + 1] == str(rehearsal_out)
    assert plan.surface_dir == rehearsal_out
    assert plan.review_out == rehearsal_out
    assert str(ac.REVIEW_OUT) in by_name["snapshot"].note

    monkeypatch.setattr(ac, "server_listening", lambda *a, **k: True)
    waiver = argparse.Namespace(review_out=rehearsal_out, yes=False, stop_server=False)
    assert ac._preflight(waiver) is True
    refuse = argparse.Namespace(review_out=None, yes=False, stop_server=False)
    assert ac._preflight(refuse) is False


def _green_report():
    report = ac.CycleReport()
    report.gate_js = "green"
    report.gate_js_green = True
    report.gate_contracts = "green"
    report.gate_contracts_green = True
    report.gate_validators = "green"
    report.gate_validators_green = True
    report.gate_conform = "green"
    report.gate_conform_green = True
    report.gate_make_test = "green"
    report.gate_make_test_green = True
    return report


def test_cycle_summary_payload_all_green_exit_ok():
    payload = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert payload["format"] == "ams-cycle-summary/1"
    assert payload["exit"] == "ok"
    assert payload["failures"] == []
    assert set(payload["gates"]) == {
        "js",
        "rebuild_contracts",
        "rebuild_validators",
        "conform",
        "make_test",
    }
    assert all(gate["green"] is True for gate in payload["gates"].values())
    assert payload["finished_at"].endswith("Z")


def test_cycle_summary_payload_green_follows_the_boolean_not_the_status_prose():
    """The payload's `green` is the judgment the gate recorded, so an annotated green stays green and prose that merely reads green cannot make it so."""
    report = _green_report()
    report.gate_contracts = "green (annotated)"
    payload = ac.cycle_summary_payload(report, [], _plan(), "ok")
    assert payload["gates"]["rebuild_contracts"]["green"] is True
    assert payload["gates"]["rebuild_contracts"]["status"] == "green (annotated)"

    report.gate_conform_green = False
    payload = ac.cycle_summary_payload(report, [], _plan(), "ok")
    assert payload["gates"]["conform"]["status"] == "green"
    assert payload["gates"]["conform"]["green"] is False


def test_cycle_summary_payload_skipped_conform_not_green():
    report = _green_report()
    report.gate_conform = "skipped (--skip-conform)"
    report.gate_conform_green = None
    payload = ac.cycle_summary_payload(report, [], _plan(skip_conform=True), "ok")
    assert payload["gates"]["conform"]["green"] is False
    assert payload["gates"]["conform"]["status"] == "skipped (--skip-conform)"
    assert payload["gates"]["js"]["green"] is True
    assert payload["plan"]["skip_conform"] is True


def test_cycle_summary_payload_marks_a_forced_conform_skip_unproved():
    report = _green_report()
    report.gate_conform = "skipped (--skip-conform)"
    report.gate_conform_green = None
    payload = ac.cycle_summary_payload(report, [], _plan(skip_conform=True), "ok")
    assert payload["gates"]["conform"]["skip"] == "forced"


def test_cycle_summary_payload_marks_auto_skips_proved():
    report = _green_report()
    report.gate_conform = "skipped (inputs unchanged)"
    report.gate_conform_green = None
    report.gate_contracts = "skipped (closure unchanged)"
    report.gate_contracts_green = None
    report.gate_validators = "skipped (closure unchanged)"
    report.gate_validators_green = None
    report.gate_make_test = "skipped (closure unchanged)"
    report.gate_make_test_green = None
    plan = _plan(
        skip_conform=True,
        conform_proven=True,
        skip_contracts=True,
        skip_validators=True,
        skip_make_test=True,
    )
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["conform"]["skip"] == "proved"
    assert payload["gates"]["rebuild_contracts"]["skip"] == "proved"
    assert payload["gates"]["rebuild_validators"]["skip"] == "proved"
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
        "kernel_threads": plan.kernel_threads,
        "pool_policy": ac.REBUILD_POOL_POLICY_DEFAULT,
        "skip_gates": False,
        "skip_conform": False,
        "skip_run_m1": False,
        "reuse_run_m1": False,
        "skip_surface": False,
        "refresh_assets": False,
        "skip_contracts": False,
        "skip_validators": False,
        "skip_plumbing": False,
        "review_out": None,
        "first_run": False,
        "short_id": "testid",
    }
    assert payload["argv"] == list(sys.argv)
    assert payload["assets_status"] == "not run"


def test_cycle_summary_payload_records_an_assets_refresh():
    """The refresh is a step of its own in the record, so a pass that skipped the surface build can still be told apart from one that copied a new app shell over it."""
    report = _green_report()
    report.assets_status = "refreshed in place (units, sidecars and generated_at unmoved)"
    payload = ac.cycle_summary_payload(report, [], _plan(skip_surface=True, refresh_assets=True), "ok")
    assert payload["plan"]["refresh_assets"] is True
    assert payload["assets_status"].startswith("refreshed in place")


def test_cycle_summary_payload_names_the_reuse_route_and_passes_no_kernel_width_on_it():
    """The machine record has to tell the three run_m1 routes apart on its own: a reuse pass is not a skip, and the width it reports must be the one the child was given, which on this route is none."""
    plan = _plan(reuse_run_m1=True, run_m1_note="only comparison-side inputs moved")
    payload = ac.cycle_summary_payload(_green_report(), [], plan, "ok")
    assert payload["plan"]["reuse_run_m1"] is True
    assert payload["plan"]["skip_run_m1"] is False
    assert payload["plan"]["kernel_threads"] is None


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
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
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
    def fake_run_m1(report, *, spawn, emit, registry, **_):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=tmp_path / "surface")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["exit"] == "failed"
    assert summary["failures"]


def test_cycle_writes_interrupted_summary(monkeypatch, tmp_path):
    def boom(report, *, spawn, emit, registry, **_):
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
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
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
    monkeypatch.setattr(
        ac, "REBUILD_CONTRACTS_GREEN", tmp_path / "rebuild" / "out" / "rebuild-contracts-green.json"
    )
    monkeypatch.setattr(
        ac, "REBUILD_VALIDATORS_GREEN", tmp_path / "rebuild" / "out" / "rebuild-validators-green.json"
    )


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
        "rebuild/tools/scaling_sweep.py",
        "rebuild/scaling-ladder.txt",
        "Makefile",
        ".vscode/settings.json",
        ".vscode/quikscript.schema.json",
        ".github/workflows/deploy.yml",
        "reference/csur/kingsley.ttf",
        "reference/Quikscript Manual.pdf",
        "reference/Shaw Alphabet Reading Key.png",
        "site/icons/copy.svg",
        "site/quikscript-title.svg",
        "site/gear-menu.js",
        "site/shared.css",
        ".gitignore",
        ".markdownlint-cli2.yaml",
        ".pre-commit-config.yaml",
        ".prettierrc",
        ".git-blame-ignore-revs",
        "LICENSE-OFL-1.1.txt",
    ):
        assert ac.make_test_exempt(path), path
    for path in (
        "glyph_data/quikscript.yaml",
        "glyph_data/punctuation.yaml",
        "tools/build_font.py",
        "test/test_calt_regressions.py",
        "test/test_shared.py",
        "site/the-manual.html",
        "site/shared.js",
        "site/print.typ",
        "conftest.py",
        "pyproject.toml",
        "postscript_glyph_names.yaml",
        "typings/uharfbuzz/__init__.pyi",
        "reference/DepartureMono-Regular.otf",
        "reference/LICENSE.DepartureMono.txt",
        "reference/nested/a.pdf",
        "site/nested/a.svg",
        "uv.lock",
    ):
        assert not ac.make_test_exempt(path), path


FAKE_MAKEFILE = """.PHONY: all test kernel-check

all:
\techo build

test:
\techo test $(if $(FORCE),--force)

# comment

kernel-check:
\techo kernel
"""


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "core.excludesFile", os.devnull], cwd=tmp_path, check=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build_font.py").write_text("print()\n")
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "notes.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("hello\n")
    (tmp_path / "Makefile").write_text(FAKE_MAKEFILE)
    (tmp_path / ".gitignore").write_text("tmp/\n")
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "settings.json").write_text("{}\n")
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "Manual.pdf").write_text("pdf\n")
    (tmp_path / "reference" / "DepartureMono-Regular.otf").write_text("otf\n")
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "icons").mkdir()
    (tmp_path / "site" / "icons" / "copy.svg").write_text("<svg/>\n")
    (tmp_path / "site" / "title.svg").write_text("<svg/>\n")
    (tmp_path / "site" / "shared.css").write_text("body {}\n")
    (tmp_path / "site" / "shared.js").write_text("export {};\n")
    return tmp_path


def test_closure_files_apply_the_exemptions(tmp_path):
    root = _git_repo(tmp_path)
    assert ac.make_test_closure_files(root) == [
        "reference/DepartureMono-Regular.otf",
        "site/shared.js",
        "tools/build_font.py",
    ]


def test_closure_files_leave_the_makefile_to_the_recipe_probe(tmp_path):
    """The Makefile is not hashed as a file, so a comment or an unrelated target cannot re-arm the gate — it enters the fingerprint as one probe line per rule the suite executes."""
    root = _git_repo(tmp_path)
    files = ac.make_test_closure_files(root)
    assert files is not None
    assert "Makefile" not in files
    lines = ac.make_test_recipe_lines(root)
    assert lines is not None
    assert [line.split("\t")[0] for line in lines] == ["make -n all", "make -n test"]


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


def test_closure_fingerprint_moves_with_an_executed_recipe(tmp_path):
    """Editing either rule the suite runs moves the key, which is the whole point of keeping the Makefile in the closure at all."""
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("\techo build", "\techo build --twice"))
    second = ac.make_test_closure_fingerprint(root)
    assert second not in (None, first)
    (root / "Makefile").write_text(
        FAKE_MAKEFILE.replace("\techo build", "\techo build --twice").replace(
            "\techo test ", "\techo test --verbose "
        )
    )
    assert ac.make_test_closure_fingerprint(root) not in (None, first, second)


def test_closure_fingerprint_ignores_makefile_edits_the_suite_never_executes(tmp_path):
    """A comment, a target nothing under `make test` runs, and a brand-new rule are all invisible to the gate, so the cycle and kernel targets can churn without re-arming a quarter-hour of tests."""
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    assert first is not None
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("# comment", "# a different comment"))
    assert ac.make_test_closure_fingerprint(root) == first
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("\techo kernel", "\techo kernel --check"))
    assert ac.make_test_closure_fingerprint(root) == first
    (root / "Makefile").write_text(FAKE_MAKEFILE + "\nconform-deep:\n\techo deep\n")
    assert ac.make_test_closure_fingerprint(root) == first


def test_closure_fingerprint_moves_when_the_makefile_stops_parsing(tmp_path):
    """stderr and the return code ride into the probe's digest, so a Makefile make can no longer read moves the key instead of hashing an empty recipe — and nothing raises."""
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    (root / "Makefile").write_text("all:\n\techo build\nfoo bar baz\n")
    broken = ac.make_test_closure_fingerprint(root)
    assert broken is not None
    assert broken != first


def test_recipe_probe_is_blind_to_the_callers_overrides(tmp_path, monkeypatch):
    """`make test FORCE=1` reaches the probe by two routes at once — inside MAKEFLAGS, which a sub-make re-reads as its own command line, and as a plain exported FORCE that stripping the flags never touches — and both the cycle's gate and `make test`'s wrapper reach it as sub-makes. So the recipe expands the same way for a forced caller as for a bare one; otherwise a forced green would key on a recipe no bare run ever prints, re-arming the whole suite afterward on the very override that exists to run it once, and a forced red would leave standing the green it had just contradicted."""
    root = _git_repo(tmp_path)
    for name in ("MAKEFLAGS", "MFLAGS", "FORCE"):
        monkeypatch.delenv(name, raising=False)
    plain = ac.make_test_closure_fingerprint(root)
    assert plain is not None
    monkeypatch.setenv("MAKEFLAGS", " -- FORCE=1")
    monkeypatch.setenv("MFLAGS", "-j2")
    monkeypatch.setenv("FORCE", "1")
    assert ac.make_test_closure_fingerprint(root) == plain


def test_recipe_pins_cover_the_repos_own_executed_rules(monkeypatch):
    """The pin roster has to name every variable the real `all` and `test` rules read, not only the one the fake Makefile mimics, so this asks the live Makefile the question the documented override poses: FORCE=1 in the environment, and the same two probe lines back."""
    monkeypatch.delenv("FORCE", raising=False)
    plain = ac.make_test_recipe_lines(ac.ROOT)
    assert plain is not None
    monkeypatch.setenv("FORCE", "1")
    assert ac.make_test_recipe_lines(ac.ROOT) == plain


def test_closure_fingerprint_is_none_when_make_is_unavailable(tmp_path, monkeypatch):
    """A box without make takes git's absence path: no fingerprint, so the caller runs the gate unconditionally rather than trusting a key it could not compute."""
    root = _git_repo(tmp_path)
    real_run = ac.subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv[0] == "make":
            raise FileNotFoundError(argv[0])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(ac.subprocess, "run", fake_run)
    assert ac.make_test_recipe_lines(root) is None
    assert ac.make_test_closure_fingerprint(root) is None


def test_closure_fingerprint_moves_when_a_tracked_file_is_deleted(tmp_path):
    root = _git_repo(tmp_path)
    subprocess.run(["git", "add", "tools/build_font.py"], cwd=root, check=True)
    first = ac.make_test_closure_fingerprint(root)
    (root / "tools" / "build_font.py").unlink()
    assert ac.make_test_closure_fingerprint(root) != first


def test_prior_make_test_fingerprint_reads_only_the_green_record(tmp_path):
    """The cycle summary keeps a display copy of the fingerprint, but the skip decision must never read it: after clear_contradicted_green deletes the record, a summary copy would resurrect a green whose last observed run was red."""
    green = tmp_path / "make-test-green.json"
    assert ac.prior_make_test_fingerprint(green) is None
    ac.record_make_test_green("from-green", green)
    assert ac.prior_make_test_fingerprint(green) == "from-green"
    record = ac.read_make_test_green(green)
    assert record is not None
    assert record["fingerprint"] == "from-green"
    assert isinstance(record.get("finished_at"), str)
    green.write_text("not json")
    assert ac.prior_make_test_fingerprint(green) is None
    green.write_text(json.dumps({"fingerprint": None}))
    assert ac.prior_make_test_fingerprint(green) is None


def test_dry_run_plan_skip_make_test():
    plan = _plan(skip_make_test=True, make_test_note="closure unchanged since its last green run")
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:make-test"].argv is None
    assert by_name["gate:make-test"].note == "SKIPPED (closure unchanged since its last green run)"
    assert by_name["gate:rebuild-contracts"].argv is not None
    rendered = _plan_text(plan)
    assert "gate:make-test not running, so no queueing" in rendered
    assert "Lane t0   [from t=0, background]  : gate:js" in rendered


def test_skip_make_test_frees_the_surface_build_budget():
    """The sweeps' width is the configuration count either way — nothing about make-test bears on it — while the surface build is the stage that gives both cores and bytes back to a pytest pool that is actually running. On the 48 GiB box the pool's bytes sit inside a worker's worth of slack, so both arms answer the same width — the pair separating is the fit-terms seam's assertion — and what this checks is that the plan resolves each arm's own terms and its reason line says which one it resolved: the gated arm's derivation carries the pool's bytes in its co-resident clause, and the skip arm says the build takes the whole box. The widths are read off the budget rather than written here, so a re-seed of either surface constant never has to come back to this test."""
    plan = _plan(
        skip_make_test=True,
        make_test_note="closure unchanged since its last green run",
        ncores=10,
        total_bytes=BOX_48_GIB,
    )
    solo_width = ac.surface_job_budget(
        skip_gates=False, skip_make_test=True, ncores=10, total_bytes=BOX_48_GIB
    )
    assert plan.surface_jobs == solo_width
    assert plan.sweep_jobs == 6
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["surface-build"])[-2:] == ["--jobs", str(solo_width)]
    rendered = _plan_text(plan)
    assert f"surface-build --jobs             : {solo_width}" in rendered
    assert f"less {format_gb(ac.SURFACE_PARENT_BYTES)} GB co-resident" in rendered
    assert "gate:make-test skipped, so the surface build takes the whole box" in rendered

    gated = _plan(skip_make_test=False, ncores=10, total_bytes=BOX_48_GIB)
    gated_width = ac.surface_job_budget(skip_gates=False, ncores=10, total_bytes=BOX_48_GIB)
    assert gated.surface_jobs == gated_width
    assert gated.sweep_jobs == 6
    gated_by_name = {step.name: step for step in gated.steps}
    assert _argv(gated_by_name["surface-build"])[-2:] == ["--jobs", str(gated_width)]
    _per_unit, gated_coresident, _cap = ac._surface_fit_terms(
        skip_gates=False, skip_make_test=False, ncores=10
    )
    assert (
        f"surface-build --jobs             : {gated_width}  (gate:make-test's pytest pool held to 2 workers — its cores reserved here and its bytes off the box beside the build's own parent; "
        f"{gated_width} at {format_gb(ac.SURFACE_WORKER_BYTES)} GB each out of 51.54 GB total, less a reserve of 8.00 GB, less {format_gb(gated_coresident)} GB co-resident, capped at 8)"
        in _plan_text(gated)
    )


def test_summary_payload_carries_the_fingerprint_only_while_green(tmp_path):
    plan = _plan(skip_make_test=False, make_test_fingerprint="fp-1")
    report = ac.CycleReport()

    report.gate_make_test = "green"
    report.gate_make_test_green = True
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["make_test_fingerprint"] == "fp-1"

    report.gate_make_test = "FAILED (exit 2)"
    report.gate_make_test_green = False
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

    def fake_make(argv, spawn, emit, registry):
        record["make_calls"] += 1
        return _step("gate:make-test", 0)

    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(skip_make_test=True, make_test_note="closure unchanged since its last green run")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["make_calls"] == 0
    assert report.gate_make_test == "skipped (closure unchanged since its last green run)"
    assert report.gate_contracts == "green"
    assert report.gate_validators == "green"
    assert report.gate_conform == "green"


def test_the_pool_width_is_handed_to_the_make_test_child_and_to_no_other(monkeypatch):
    """The width the plan reserved for reaches the pool it reserved for, and reaches nothing else. It rides on that one child's environment because run_m1, the surface build and both rebuild lanes are spawned from this same process: a width set on os.environ would pin their `-n auto` pools too, and neither lane's is make-test's to choose."""
    seen: dict[str, dict[str, str] | None] = {}

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        seen[name] = env
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(ncores=8)
    rc = ac._run_cycle(plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)

    assert rc == 0
    assert plan.make_test_workers == ac.MAKE_TEST_POOL_WORKERS
    assert seen["gate:make-test"] == {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.make_test_workers)}
    assert seen["gate:js"] is None
    assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in os.environ


def test_each_rebuild_lane_names_its_pool_to_its_own_child(monkeypatch):
    """Each lane's pytest controller stamps its per-worker peaks into the timings journal under the name of the pool it ran, which is the join key `make job-costs` holds a checked-in constant against. The cycle spawns both lanes as bare pytest rather than through rebuild_gate.py, so the name has to be added here — on each lane's own child, never on os.environ, or the second lane would inherit the first lane's name and every measurement would be filed under the wrong constant."""
    # Deleted first because this very suite runs inside a lane that named its own pool whenever the cycle's contracts gate is what spawned it: what is being pinned is that the drive writes only the children's env dicts, so the check on os.environ below has to start from a known absence.
    monkeypatch.delenv("AMS_POOL_UNIT", raising=False)
    seen: dict[str, dict[str, str] | None] = {}

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        seen[name] = env
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    rc = ac._run_cycle(plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)

    assert rc == 0
    assert seen["gate:rebuild-contracts"] == {"AMS_POOL_UNIT": "rebuild-contracts"}
    assert seen["gate:rebuild-validators"] == {"AMS_POOL_UNIT": "rebuild-validators"}
    assert "AMS_POOL_UNIT" not in os.environ
    # Both the variable and the two unit names are spelled literally here, matching the neighboring width variable rather than importing one word — so the drift that spelling invites is what this pins instead. A name this side writes that the registry does not read is a pool filed under nothing: no row claims it, the unit it was meant to price reports itself unmeasured here, and that reads exactly like a box that has simply not run the lane yet.
    known = {name for unit in cb.UNITS for name in unit.pool_units}
    assert {"rebuild-contracts", "rebuild-validators"} <= known
    assert ct.POOL_UNIT_ENV == "AMS_POOL_UNIT"


def test_a_timed_spawn_carries_a_child_its_environment(monkeypatch, tmp_path):
    """The timing decorator wraps every spawn, so anything a caller adds to one has to survive it — the width would otherwise be dropped on exactly the runs that are real, since a cycle is only untimed in this suite."""
    seen: dict[str, dict[str, str] | None] = {}

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        seen[name] = env
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(ncores=8)
    rc = ac._run_cycle(
        plan,
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=fake_spawn,
        timings=CycleTimings(tmp_path / "timings.ndjson"),
    )

    assert rc == 0
    assert seen["gate:make-test"] == {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.make_test_workers)}
    assert seen["gate:js"] is None


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
    (tmp_path / "rebuild" / "kernel-rs" / "src").mkdir(parents=True)
    (tmp_path / "uv.lock").write_text("lock-1")
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("a: 1\n")
    (tmp_path / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("guard-1\n")
    first = ac.run_m1_skip_fingerprint(tmp_path)
    assert first == ac.run_m1_skip_fingerprint(tmp_path)
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("a: 2\n")
    second = ac.run_m1_skip_fingerprint(tmp_path)
    assert second != first
    (tmp_path / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz").write_bytes(b"rows")
    third = ac.run_m1_skip_fingerprint(tmp_path)
    assert third != second
    (tmp_path / "uv.lock").write_text("lock-2")
    fourth = ac.run_m1_skip_fingerprint(tmp_path)
    assert fourth != third
    (tmp_path / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("guard-2\n")
    assert ac.run_m1_skip_fingerprint(tmp_path) != fourth


def test_conform_skip_fingerprint_includes_horizon_and_font(tmp_path):
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    base = ac.conform_skip_fingerprint(tmp_path, 5)
    assert ac.conform_skip_fingerprint(tmp_path, 5) == base
    assert ac.conform_skip_fingerprint(tmp_path, 4) != base
    (tmp_path / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(b"OTTO")
    assert ac.conform_skip_fingerprint(tmp_path, 5) != base


def _fake_run_m1_root(tmp_path):
    """A repo skeleton holding one file of every kind the run_m1 skip key reaches: each data input, the contact allow-list, the baselines and the subsets extracted from them, both halves of the pipeline code, the crate, and uv.lock. Written out rather than stubbed, because what the tests over it are about is which labels the real readers produce over a real tree."""
    for rel in (
        "glyph_data/runes",
        "rebuild/schema",
        "rebuild/pipeline",
        "rebuild/validation",
        "rebuild/kernel-rs/src",
        "rebuild/out/m1",
    ):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    for rel, text in (
        ("glyph_data/runes/qsX.yaml", "rune: qsX\n"),
        ("rebuild/schema/rune.json", "{}\n"),
        ("rebuild/script.yaml", "script: 1\n"),
        ("glyph_data/punctuation.yaml", "punctuation: 1\n"),
        ("rebuild/m1-aliases.yaml", "qsX: X\n"),
        ("rebuild/m1-divergences.yaml", "- id: x\n  status: intended\n  why: one\n"),
        ("glyph_data/senior_quikscript_kerning.yaml", "pairs: {}\n"),
        ("rebuild/m1-contact-allow.yaml", "- signature: seam-1\n  why: blessed once\n"),
        ("rebuild/pipeline/oracle.py", "verdict = 1\n"),
        ("rebuild/pipeline/settle.py", "settle = 1\n"),
        ("rebuild/validation/shaper.py", "shape = 1\n"),
        ("rebuild/kernel-rs/Cargo.toml", "[package]\n"),
        ("rebuild/kernel-rs/Cargo.lock", "[[package]]\n"),
        ("rebuild/kernel-rs/src/lib.rs", "fn settle() {}\n"),
        ("uv.lock", "lock-1\n"),
    ):
        (tmp_path / rel).write_text(text)
    (tmp_path / "rebuild" / "out" / "baseline-default.tsv.gz").write_bytes(b"rows")
    (tmp_path / "rebuild" / "out" / "m1" / "baseline-default.subset.tsv.gz").write_bytes(b"rows")
    (tmp_path / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(b"OTTO")
    return tmp_path


def test_comparison_side_label_names_only_the_inputs_no_table_stage_reads():
    """The roster that lets a cycle stand on the enumeration and the font already on disk, so a label wrongly on it means a pass re-adjudicating against artifacts that no longer describe their sources. uv.lock is the deliberate exclusion and the interesting one: the tables' stamp misses it exactly as it misses the ledgers, but it pins fontTools and uharfbuzz, so a bump there can move the very bytes the reuse proposes to trust."""
    from rebuild.pipeline import fingerprint

    for label in fingerprint.NON_TABLE_DATA_LABELS:
        assert ac.comparison_side_label(label)
    assert ac.comparison_side_label(fingerprint.CONTACT_ALLOW_LABEL)
    assert ac.comparison_side_label("baselines")
    assert ac.comparison_side_label("baseline-default.subset.tsv.gz")
    assert all(
        ac.comparison_side_label(f"rebuild/pipeline/{name}") for name in fingerprint.COMPARISON_CODE_MODULES
    )
    assert not ac.comparison_side_label("uv.lock")
    assert not ac.comparison_side_label("glyph_data/runes/qsPea.yaml")
    assert not ac.comparison_side_label("rebuild/script.yaml")
    assert not ac.comparison_side_label("rebuild/pipeline/settle.py")
    assert not ac.comparison_side_label("rebuild/kernel-rs/src/lib.rs")
    assert not ac.comparison_side_label("baseline-default.tsv.gz")


def test_every_run_m1_label_is_stamped_the_toolchain_or_comparison_side(tmp_path):
    """The structural guard behind the whole route: an input in the run key that neither the tables' stamp covers nor `comparison_side_label` names would be reused as though the artifacts on disk still described it. Exactly one label is allowed to be neither, and it is the one the route refuses on purpose. A line added to the key with no home lands here rather than in a cycle quietly standing on a stale font."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    stamped = {line.split("\t", 1)[0] for line in fingerprint.table_data_lines(root)}
    stamped |= {
        line.split("\t", 1)[0] for line in fingerprint.path_lines(root, fingerprint.table_code_paths(root))
    }
    labels = [line.split("\t", 1)[0] for line in ac.run_m1_skip_lines(root)]
    assert [
        label
        for label in labels
        if label not in stamped and label != "uv.lock" and not ac.comparison_side_label(label)
    ] == []
    assert stamped & set(labels)
    assert [label for label in labels if ac.comparison_side_label(label)]
    assert "uv.lock" in labels


def test_a_missing_allow_list_contributes_no_line(tmp_path):
    """The allow-list is optional the way `path_lines` treats every missing file: a tree without one hashes as a tree without one, rather than folding a read error into the key."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    assert fingerprint.CONTACT_ALLOW_LABEL in ac.run_m1_skip_files(root)
    (root / fingerprint.CONTACT_ALLOW_LABEL).unlink()
    assert fingerprint.CONTACT_ALLOW_LABEL not in ac.run_m1_skip_files(root)


def test_the_allow_list_line_is_prose_blind(tmp_path):
    """Blessing a contact signature has to move this key — the defect gate is the only stage that reads the file, so nothing else will notice — while wording the bless must not, since a cycle that re-adjudicates over a reworded `why` spends its gates proving what it already proved."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    allow = root / fingerprint.CONTACT_ALLOW_LABEL
    before = ac.run_m1_skip_fingerprint(root)
    allow.write_text("# a comment nobody reads\n- signature: seam-1\n  why: blessed twice over\n")
    assert ac.run_m1_skip_fingerprint(root) == before
    allow.write_text("- signature: seam-1\n- signature: seam-2\n")
    assert ac.run_m1_skip_fingerprint(root) != before


def test_the_divergence_ledger_line_is_prose_blind(tmp_path):
    """Reclassifying a divergence class has to move this key — the oracle reads the ledger to name and classify the rows it adjudicates — while rewording the class's `why` must not, since nothing that classifies anything reads that sentence. Its one reader is the surface's explain panel, and the Stage B `explain_prose` component is what stamps it, so a reword costs a cache-served surface rebuild and no re-adjudication at all."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    ledger = root / fingerprint.DIVERGENCE_LEDGER_LABEL
    before = ac.run_m1_skip_fingerprint(root)
    ledger.write_text(
        "# a header nobody classifies by\n- id: x\n  status: intended\n  why: one, at greater length\n"
    )
    assert ac.run_m1_skip_fingerprint(root) == before
    ledger.write_text("- id: x\n  status: reviewed-approved\n  why: one, at greater length\n")
    assert ac.run_m1_skip_fingerprint(root) != before


def test_a_comparison_side_edit_moves_the_run_key_and_leaves_the_sweeps_alone(tmp_path):
    """Why the two keys had to stop being built from one another. The sweep shapes the compiled font and re-settles the windows beside it; it opens no ledger, no allow-list, no kern sidecar, no baseline and none of the oracle's code, so an edit to any of those can move the key that decides whether to rebuild without moving the key that decides whether to sweep. The second half is what keeps that honest: everything the sweep does read still moves it."""
    from rebuild.pipeline import kernel_exec

    root = _fake_run_m1_root(tmp_path)
    conform = ac.conform_skip_fingerprint(root, 4)
    run_key = ac.run_m1_skip_fingerprint(root)
    for rel, text in (
        ("rebuild/m1-divergences.yaml", "- id: y\n  status: intended\n  why: one\n"),
        ("rebuild/m1-aliases.yaml", "qsX: Y\n"),
        ("glyph_data/senior_quikscript_kerning.yaml", "pairs: {qsX_qsY: -1}\n"),
        ("rebuild/m1-contact-allow.yaml", "- signature: seam-2\n"),
        ("rebuild/pipeline/oracle.py", "verdict = 2\n"),
        ("rebuild/out/baseline-default.tsv.gz", "many more baseline rows\n"),
        ("rebuild/out/m1/baseline-default.subset.tsv.gz", "many more subset rows\n"),
    ):
        (root / rel).write_text(text)
        moved = ac.run_m1_skip_fingerprint(root)
        assert moved != run_key, rel
        assert ac.conform_skip_fingerprint(root, 4) == conform, rel
        run_key = moved

    for rel, text in (
        ("glyph_data/runes/qsX.yaml", "rune: qsX\nstances: {}\n"),
        ("rebuild/pipeline/settle.py", "settle = 2\n"),
        ("rebuild/kernel-rs/src/lib.rs", "fn settle() { loop {} }\n"),
        ("uv.lock", "lock-2\n"),
        ("rebuild/out/m1/M1.otf", "OTTO and then some\n"),
    ):
        (root / rel).write_text(text)
        moved = ac.conform_skip_fingerprint(root, 4)
        assert moved != conform, rel
        conform = moved
    assert ac.conform_skip_fingerprint(root, 5) != conform
    assert ac.conform_skip_files(root, 4)["semantics"] == "+".join(kernel_exec.enumeration_tokens())


def test_gates_only_reuse_licenses_only_a_diff_the_tables_stamp_cannot_see():
    """The licensing predicate, and each None it answers means a different thing that all come to "rebuild": no prior green to stand on, nothing moved at all (which is the plain skip's case and not this one), or something build-side moved and the enumeration has to be made again. `moved_input_labels` is asserted beside it because the annotated form the note prints would match no roster entry at all, and the failure would look like a route that simply never fires."""
    stored = {
        "glyph_data/runes/qsX.yaml": "r1",
        "rebuild/m1-divergences.yaml": "d1",
        "rebuild/pipeline/oracle.py": "o1",
        "uv.lock": "l1",
    }
    record = {"files": dict(stored)}
    assert ac.gates_only_reuse(record, dict(stored)) is None
    assert ac.gates_only_reuse(None, dict(stored)) is None
    assert ac.gates_only_reuse({"fingerprint": "fp"}, dict(stored)) is None

    ledger = {**stored, "rebuild/m1-divergences.yaml": "d2", "rebuild/pipeline/oracle.py": "o2"}
    assert ac.gates_only_reuse(record, ledger) == [
        "rebuild/m1-divergences.yaml",
        "rebuild/pipeline/oracle.py",
    ]
    assert ac.moved_input_labels(record, ledger) == [
        "rebuild/m1-divergences.yaml",
        "rebuild/pipeline/oracle.py",
    ]
    assert ac.moved_inputs_note(record, ledger) == (
        "rebuild/m1-divergences.yaml (changed), rebuild/pipeline/oracle.py (changed)"
    )

    assert ac.gates_only_reuse(record, {**stored, "baseline-default.subset.tsv.gz": "s1"}) == [
        "baseline-default.subset.tsv.gz"
    ]
    dropped = {name: value for name, value in stored.items() if name != "rebuild/m1-divergences.yaml"}
    assert ac.gates_only_reuse(record, dropped) == ["rebuild/m1-divergences.yaml"]

    assert ac.gates_only_reuse(record, {**stored, "uv.lock": "l2"}) is None
    assert ac.gates_only_reuse(record, {**stored, "glyph_data/runes/qsX.yaml": "r2"}) is None
    assert ac.gates_only_reuse(record, {**stored, "rebuild/pipeline/settle.py": "s1"}) is None
    assert (
        ac.gates_only_reuse(record, {name: value for name, value in stored.items() if name != "uv.lock"})
        is None
    )


def _write_behavior_classes(root, classes, fmt=None):
    from rebuild.pipeline.emit_gsub import BEHAVIOR_CLASSES_FORMAT

    m1 = root / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True, exist_ok=True)
    (m1 / "behavior_classes.json").write_text(
        json.dumps({"format": fmt or BEHAVIOR_CLASSES_FORMAT, "classes": list(classes)})
    )
    for rel in ac.COMPILE_CODE_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")
    return m1 / "behavior_classes.json"


def test_deep_sweep_skip_lines_need_a_sidecar_in_the_expected_format(tmp_path):
    assert ac.deep_sweep_skip_lines(tmp_path) is None
    assert ac.deep_sweep_skip_fingerprint(tmp_path) is None
    assert ac.deep_sweep_skip_files(tmp_path) is None
    sidecar = _write_behavior_classes(tmp_path, ["settle:bk0-la1"], fmt="ams-m1-behavior-classes/999")
    assert ac.deep_sweep_skip_lines(tmp_path) is None
    sidecar.write_text("not json")
    assert ac.deep_sweep_skip_lines(tmp_path) is None


def test_deep_sweep_skip_lines_name_the_classes_the_code_and_the_shaper(tmp_path):
    _write_behavior_classes(tmp_path, ["namer-dot", "settle:bk1-la2"])
    lines = ac.deep_sweep_skip_lines(tmp_path)
    assert lines is not None
    assert lines[:2] == ["class:namer-dot\tpresent", "class:settle:bk1-la2\tpresent"]
    files = ac.deep_sweep_skip_files(tmp_path)
    assert files is not None
    assert set(ac.COMPILE_CODE_FILES) <= set(files)
    assert "uharfbuzz" in files
    assert not any(name.startswith("horizon") for name in files)
    assert ac._digest_lines(lines) == ac.deep_sweep_skip_fingerprint(tmp_path)


def test_deep_sweep_fingerprint_moves_with_a_class_or_the_compile_code(tmp_path):
    _write_behavior_classes(tmp_path, ["namer-dot"])
    base = ac.deep_sweep_skip_fingerprint(tmp_path)
    _write_behavior_classes(tmp_path, ["namer-dot", "guard-form:zwnj"])
    grown = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert grown != base
    (tmp_path / ac.COMPILE_CODE_FILES[0]).write_text("# rewritten\n")
    assert ac.deep_sweep_skip_fingerprint(tmp_path) != grown


def test_deep_sweep_status_walks_unknown_never_run_armed_and_current(tmp_path, monkeypatch):
    store = tmp_path / "deep-sweep-green.json"
    monkeypatch.setattr(ac, "DEEP_SWEEP_GREEN", store)
    status, note = ac.deep_sweep_status(tmp_path)
    assert status == "unknown"
    assert "behavior-class sidecar" in note

    _write_behavior_classes(tmp_path, ["namer-dot"])
    status, note = ac.deep_sweep_status(tmp_path)
    assert status == "never-run"
    assert "make conform-deep" in note

    fingerprint = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert fingerprint is not None
    ac.record_deep_sweep_green(fingerprint, 5, files=ac.deep_sweep_skip_files(tmp_path), path=store)
    record = ac.read_green_record(store)
    assert record is not None
    assert record["horizon"] == 5
    assert ac.deep_sweep_status(tmp_path) == ("current", "horizon 5")
    assert ac.deep_sweep_status(tmp_path, horizon=4)[0] == "current"

    assert ac.deep_sweep_status(tmp_path, horizon=6)[0] == "armed"
    assert "shallower" in ac.deep_sweep_status(tmp_path, horizon=6)[1]

    _write_behavior_classes(tmp_path, ["namer-dot", "guard-form:zwnj"])
    status, note = ac.deep_sweep_status(tmp_path)
    assert status == "armed"
    assert "make conform-deep" in note
    assert "class:guard-form:zwnj (new)" in note


def test_cycle_summary_payload_carries_the_deep_sweep_status(monkeypatch):
    monkeypatch.setattr(ac, "deep_sweep_status", lambda root=ac.ROOT, horizon=5: ("armed", "a new shape"))
    payload = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert payload["deep_sweep"] == {"status": "armed", "note": "a new shape"}


def test_the_deep_sweep_line_never_fails_the_summary(monkeypatch):
    def explode(root=ac.ROOT, horizon=5):
        raise OSError("no record")

    monkeypatch.setattr(ac, "deep_sweep_status", explode)
    assert ac._deep_sweep_report()[0] == "unknown"
    payload = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert payload["deep_sweep"]["status"] == "unknown"


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
    assert "uv.lock" in conform
    assert "semantics" in conform


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


def test_oracle_cache_note_speaks_the_labels_a_skip_miss_actually_reports():
    """The note is built from the real trees rather than from literals because the way it fails is by matching nothing and saying nothing, which reads exactly like "the store is fine". `moved_inputs_note` reports repo-relative POSIX labels; a comparison against basenames answers `None` for every real input there is, and only a name nobody would ever be handed gets a note out of it."""
    from rebuild.pipeline import fingerprint, oracle_cache

    rune = sorted(path.relative_to(ac.ROOT).as_posix() for path in fingerprint.rune_paths(ac.ROOT))[0]
    code = oracle_cache.ORACLE_ROW_CODE_PATHS[0]
    assert any(line.startswith(f"{rune}\t") for line in fingerprint.data_lines(ac.ROOT))

    assert (
        ac.oracle_cache_note(f"{code} (changed)")
        == f"the oracle row cache drops whole: {code} is inside its stamp"
    )
    assert (
        ac.oracle_cache_note("rebuild/script.yaml (changed)")
        == "the oracle row cache drops whole: rebuild/script.yaml is inside its stamp"
    )
    assert (
        ac.oracle_cache_note(f"{rune} (changed)")
        == "the oracle row cache re-derives only the rows reaching those runes"
    )
    note = ac.oracle_cache_note(f"{rune} (changed), {code} (changed)")
    assert note is not None and note.startswith("the oracle row cache drops whole")
    assert ac.oracle_cache_note("rebuild/m1-divergences.yaml (changed)") is None
    assert ac.oracle_cache_note(f"{rune} (changed) and 4 more") is None
    assert ac.oracle_cache_note(None) is None


def test_m1_artifacts_present(tmp_path):
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    names = [path.name for path in ac.M1_SUMMARY_FILES.values()] + list(ac.M1_ARTIFACT_NAMES)
    assert not ac.m1_artifacts_present(tmp_path)
    for name in names:
        (m1 / name).write_text("{}")
    assert ac.m1_artifacts_present(tmp_path)
    (m1 / "M1.otf").unlink()
    assert not ac.m1_artifacts_present(tmp_path)


def test_rebuild_gate_closure_scope_and_exemptions(tmp_path):
    """Both edges of the closure at once. The exempt paths are the ones no test in either lane reads: the carried-verdict evidence, the JS-only jstests, the census pins the cycle itself rewrites mid-pass, and the contact allow-list, whose only reader is the defect gate — so blessing a contact signature must not re-run the whole suite to prove nothing. The harness roster is the opposite edge: files the suite reads from outside rebuild/ and glyph_data/, named one at a time, so a tools/ script no test opens stays out while doc/glyph-names.md comes in despite the Markdown filter."""
    assert "rebuild/m1-contact-allow.yaml" in ac.REBUILD_GATE_EXEMPT_PREFIXES
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild" / "evidence").mkdir(parents=True)
    (tmp_path / "rebuild" / "review" / "jstests").mkdir(parents=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / "rebuild" / "NOTES.md").write_text("")
    (tmp_path / "rebuild" / "evidence" / "verdicts-old.json").write_text("{}")
    (tmp_path / "rebuild" / "review" / "jstests" / "x.test.js").write_text("")
    (tmp_path / "rebuild" / "m1-contact-allow.yaml").write_text("- signature: seam-1\n")
    (tmp_path / "glyph_data" / "runes" / "qsX.yaml").write_text("")
    (tmp_path / "tools" / "outside.py").write_text("")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    for rel in ac.REBUILD_GATE_HARNESS_PATHS:
        harness_file = tmp_path / rel
        harness_file.parent.mkdir(parents=True, exist_ok=True)
        harness_file.write_text("")
    files = ac.rebuild_gate_closure_files(tmp_path)
    assert files is not None
    assert files == sorted(
        [
            "conftest.py",
            "glyph_data/runes/qsX.yaml",
            "pyproject.toml",
            "rebuild/test_x.py",
            "uv.lock",
            *ac.REBUILD_GATE_HARNESS_PATHS,
        ]
    )
    assert "doc/glyph-names.md" in files
    assert "tools/outside.py" not in files
    assert "rebuild/NOTES.md" not in files


def test_rebuild_gate_closure_none_outside_git(tmp_path):
    assert ac.rebuild_gate_closure_files(tmp_path) is None


def test_an_absent_artifact_hashes_to_a_sentinel_rather_than_raising(tmp_path):
    """Every gate key folds this answer in, and the validators key names three M1 artifacts that do not exist until the first build has run, so an unreadable path has to hash as a value rather than take the cycle down. The autouse fixture above substitutes the sentinel for live paths, which means nothing else here ever reaches the real fallback; a tmp path delegates, so this does."""
    assert ac._sha256_path(tmp_path / "never-built.otf") == "absent"
    assert ac._sha256_path(tmp_path) == "absent"
    built = tmp_path / "built.otf"
    built.write_bytes(b"OTTO")
    assert ac._sha256_path(built) != "absent"


@pytest.mark.parametrize("lane", ac.REBUILD_LANES)
def test_both_lane_fingerprints_are_prose_blind_for_runes(lane, tmp_path):
    """Both lanes carry the rune files, because contracts tests load the live spec too — so a geometry edit moves both keys and a prose edit moves neither."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    rune = tmp_path / "glyph_data" / "runes" / "qsX.yaml"
    rune.write_text("rune: qsX\nductus:\n  hapax: |\n    A stroke.\n")
    before = ac.rebuild_lane_fingerprint(tmp_path, lane)
    rune.write_text("rune: qsX\nductus:\n  hapax: |\n    A different stroke.\n")
    assert ac.rebuild_lane_fingerprint(tmp_path, lane) == before
    rune.write_text("rune: qsY\nductus:\n  hapax: |\n    A different stroke.\n")
    assert ac.rebuild_lane_fingerprint(tmp_path, lane) != before


@pytest.mark.parametrize("lane", ac.REBUILD_LANES)
def test_both_lane_fingerprints_are_prose_blind_for_the_ledgers(lane, tmp_path):
    """The two human-reviewed ledgers the closure still keeps, hashed the way a rune is. Tests in both lanes load them — the census facts and the audit's class ids from the divergence ledger, every standing rule's `match` from the approvals — and every one of those readers takes structure, so a reworded `why` or `note` would re-run both lanes to reproduce the same green. Reclassifying a class or flipping a rule's verdict still moves both keys, which is what the blindness is bought against."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    ledger = tmp_path / "rebuild" / "m1-divergences.yaml"
    ledger.write_text("- id: seam-moved\n  status: intended\n  no_verdict: true\n  why: one\n")
    standing = tmp_path / "rebuild" / "standing-approvals.yaml"
    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: approve\n    note: one\n"
    )
    before = ac.rebuild_lane_fingerprint(tmp_path, lane)

    ledger.write_text(
        "# a header nobody classifies by\n- id: seam-moved\n  status: intended\n  no_verdict: true\n  why: two, at greater length\n"
    )
    standing.write_text(
        "format: ams-standing-approvals/1\n# a header nobody matches on\nrules:\n  - id: r1\n    verdict: approve\n    note: two, at greater length\n"
    )
    assert ac.rebuild_lane_fingerprint(tmp_path, lane) == before

    ledger.write_text(
        "- id: seam-moved\n  status: intended\n  no_verdict: false\n  why: two, at greater length\n"
    )
    reclassified = ac.rebuild_lane_fingerprint(tmp_path, lane)
    assert reclassified != before

    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: neither\n    note: two, at greater length\n"
    )
    assert ac.rebuild_lane_fingerprint(tmp_path, lane) != reclassified


def test_only_the_validators_key_sees_the_build_artifacts(tmp_path):
    """The whole point of the split key: an artifact-only change re-runs the lane that reads artifacts and nothing else, so a live M1 rebuild can never invalidate the contracts key mid-cycle."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / ".gitignore").write_text("rebuild/out/\n")
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    (m1 / "M1.otf").write_bytes(b"OTTO")
    (m1 / "baseline-default.subset.tsv.gz").write_bytes(b"rows")
    contracts = ac.rebuild_lane_fingerprint(tmp_path, "contracts")
    validators = ac.rebuild_lane_fingerprint(tmp_path, "validators")
    assert contracts != validators

    (m1 / "M1.otf").write_bytes(b"OTTO-rebuilt")
    assert ac.rebuild_lane_fingerprint(tmp_path, "contracts") == contracts
    assert ac.rebuild_lane_fingerprint(tmp_path, "validators") != validators

    validators = ac.rebuild_lane_fingerprint(tmp_path, "validators")
    (m1 / "baseline-default.subset.tsv.gz").write_bytes(b"more rows")
    assert ac.rebuild_lane_fingerprint(tmp_path, "contracts") == contracts
    assert ac.rebuild_lane_fingerprint(tmp_path, "validators") != validators

    (tmp_path / "rebuild" / "test_x.py").write_text("x = 1\n")
    assert ac.rebuild_lane_fingerprint(tmp_path, "contracts") != contracts


def test_only_the_contracts_key_sees_the_review_app_shell(tmp_path):
    """The mirror image of the artifact split: nothing in the validators lane reads the copied app shell — its fixture already exempts the matching fingerprint component — while the two tests that do read it, the index-html sanity check and the `node --check` pass, read it at its source and sit in contracts. So an app JS/CSS/HTML edit re-runs one lane, and the pass that copies it over the surface skips the other."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / ".gitignore").write_text("rebuild/out/\n")
    contracts = ac.rebuild_lane_fingerprint(tmp_path, "contracts")
    validators = ac.rebuild_lane_fingerprint(tmp_path, "validators")
    static = tmp_path / "rebuild" / "review" / "static"
    static.mkdir(parents=True)
    (static / "app.js").write_text("export const app = 1;\n")
    assert ac.rebuild_lane_fingerprint(tmp_path, "contracts") != contracts
    assert ac.rebuild_lane_fingerprint(tmp_path, "validators") == validators


@pytest.mark.parametrize("lane", ac.REBUILD_LANES)
def test_every_harness_file_moves_both_lane_keys(lane, tmp_path):
    """The under-inclusive half the audit found: the shaping suite, the three corpora, the two prose fixtures and the tools/ tree are all read under `pytest rebuild/` — collection alone imports test/test_shaping.py in every process of both lanes, and the compile modules come with it — so editing one has to re-run the lane rather than skip on a green that never saw it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel in ac.REBUILD_GATE_HARNESS_PATHS:
        harness_file = tmp_path / rel
        harness_file.parent.mkdir(parents=True, exist_ok=True)
        harness_file.write_text("")
    previous = ac.rebuild_lane_fingerprint(tmp_path, lane)
    for rel in ac.REBUILD_GATE_HARNESS_PATHS:
        (tmp_path / rel).write_text(f"{rel} moved\n")
        current = ac.rebuild_lane_fingerprint(tmp_path, lane)
        assert current != previous, rel
        previous = current


def test_the_harness_roster_names_the_whole_tools_tree():
    """A roster rather than a glob, because the closure is assembled from git pathspecs and a glob would sweep in whatever else lands under tools/ — but the read it stands for really is the whole tree, since `unit_cache.environment_stamp` hashes tools/*.py and the contracts tests that build a store recompute that stamp. So a new script there is a read both lane keys have to see, and this is what says so when one lands."""
    assert {rel for rel in ac.REBUILD_GATE_HARNESS_PATHS if rel.startswith("tools/")} == {
        f"tools/{path.name}" for path in (REPO_ROOT / "tools").glob("*.py")
    }


@pytest.mark.parametrize("tree", ("rebuild/fixtures/", "rebuild/review/fixtures/units/"))
def test_only_the_contracts_key_sees_the_fixture_piles_the_validators_lane_never_opens(tree, tmp_path):
    """The over-inclusive half. Both piles are checked-in source every reader of which sits in contracts — the validators lane opens neither, the way it opens no part of the copied app shell — so regenerating a fixture re-runs the short lane and leaves the long one's green standing. What is left of rebuild/review/fixtures/ stays in both keys, since rebuild/conftest.py imports the mini bundle's pin module at module scope."""
    assert tree in ac.VALIDATORS_EXEMPT_PREFIXES
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / ".gitignore").write_text("rebuild/out/\n")
    contracts = ac.rebuild_lane_fingerprint(tmp_path, "contracts")
    validators = ac.rebuild_lane_fingerprint(tmp_path, "validators")
    fixture = tmp_path / tree / "sample.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("{}")
    assert ac.rebuild_lane_fingerprint(tmp_path, "contracts") != contracts
    assert ac.rebuild_lane_fingerprint(tmp_path, "validators") == validators


def test_both_lane_fingerprints_are_none_outside_git(tmp_path):
    for lane in ac.REBUILD_LANES:
        assert ac.rebuild_lane_fingerprint(tmp_path, lane) is None


def test_surface_build_skippable_matches_manifest(tmp_path):
    """The skip is a claim that a rebuild would reproduce this surface whole, so every file the claim covers has to answer for itself: the fingerprint, every shard the manifest names, the three files it does not name — the per-unit index and both app sidecars, each stamped for the manifest beside it rather than merely present, since they are written after the manifest and outside it — and the after font, which no fingerprint component covers at all, so only the manifest's recorded sha held against M1.otf on disk can say a run_m1 landed since."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import app_index, unit_index

    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    surface = tmp_path / "rebuild" / "out" / "review"
    surface.mkdir(parents=True)
    stage_a = {"data": "d", "baselines": "b", "pipeline_code": "p"}
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": fingerprint.FORMAT, **stage_a}))
    font = m1 / "M1.otf"
    font.write_bytes(b"OTTO")
    before_font, junior_font = fingerprint.font_paths(tmp_path)
    expected = {**stage_a, **fingerprint.stage_b(tmp_path, before_font, junior_font)}
    shard = surface / "units-000.json"
    shard.write_text("[]")
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "inputs_fingerprint": expected,
        "classes": [{"id": "c", "shards": ["units-000.json"]}],
        "fonts": {"after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(b"OTTO").hexdigest()}},
    }

    def restamp():
        (surface / "manifest.json").write_text(json.dumps(manifest))
        unit_index.write_index(surface, [])
        app_index.write_app_artifacts(surface, {}, {})

    restamp()
    assert ac.surface_build_skippable(tmp_path, surface)
    shard.unlink()
    assert not ac.surface_build_skippable(tmp_path, surface)
    shard.write_text("[]")
    manifest["inputs_fingerprint"] = {**expected, "data": "changed"}
    restamp()
    assert not ac.surface_build_skippable(tmp_path, surface)

    manifest["inputs_fingerprint"] = {**expected, "static": "moved"}
    restamp()
    assert not ac.surface_build_skippable(tmp_path, surface)
    assert ac.surface_build_skippable(tmp_path, surface, ignore=("static",))
    del manifest["inputs_fingerprint"]["static"]
    restamp()
    assert not ac.surface_build_skippable(tmp_path, surface, ignore=("static",))

    manifest["inputs_fingerprint"] = expected
    restamp()
    assert ac.surface_build_skippable(tmp_path, surface)

    fonts = manifest["fonts"]
    font.write_bytes(b"OTTO-newer")
    assert not ac.surface_build_skippable(tmp_path, surface)
    font.write_bytes(b"OTTO")
    assert ac.surface_build_skippable(tmp_path, surface)
    del manifest["fonts"]
    restamp()
    assert not ac.surface_build_skippable(tmp_path, surface)
    manifest["fonts"] = fonts
    restamp()
    assert ac.surface_build_skippable(tmp_path, surface)

    for name, _fmt in app_index.ARTIFACTS:
        kept = app_index.artifact_path(surface, name)
        raw = kept.read_bytes()
        kept.unlink()
        assert not ac.surface_build_skippable(tmp_path, surface)
        kept.write_bytes(raw)
    assert ac.surface_build_skippable(tmp_path, surface)
    unit_index.index_path(surface).unlink()
    assert not ac.surface_build_skippable(tmp_path, surface)

    # The manifest rewritten without the sidecars: every shard is still there and the fingerprint still matches, and the three stamped files are the only thing that can tell.
    unit_index.write_index(surface, [])
    manifest["generated_at"] = "2026-02-02T00:00:00Z"
    (surface / "manifest.json").write_text(json.dumps(manifest))
    assert not ac.surface_build_skippable(tmp_path, surface)
    restamp()
    assert ac.surface_build_skippable(tmp_path, surface)


REFUSE_RUNE = "rune: qsX\npolicy:\n  refuse:\n  - {exit: baseline, why: two verticals render thick}\n"


def _stamped_surface(root):
    """A review surface stamped for the inputs standing in `root` at the moment it is written, and skippable the moment it is. It is what lets a test ask a prose edit the one question no upstream key can answer for it: a refusal to skip afterwards says the wording reached the surface's stamp, and a skip that survives says it did not."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import app_index, unit_index

    stage_a = fingerprint.stage_a(root)
    m1 = root / "rebuild" / "out" / "m1"
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": fingerprint.FORMAT, **stage_a}))
    surface = root / "rebuild" / "out" / "review"
    surface.mkdir(parents=True)
    (surface / "units-000.json").write_text("[]")
    before_font, junior_font = fingerprint.font_paths(root)
    (surface / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00Z",
                "inputs_fingerprint": {
                    **stage_a,
                    **fingerprint.stage_b(root, before_font, junior_font),
                },
                "classes": [{"id": "c", "shards": ["units-000.json"]}],
                "fonts": {
                    "after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(b"OTTO").hexdigest()}
                },
            }
        )
    )
    unit_index.write_index(surface, [])
    app_index.write_app_artifacts(surface, {}, {})
    return surface


def _upstream_keys(root):
    from rebuild.pipeline import fingerprint

    return {
        "run_m1": ac.run_m1_skip_fingerprint(root),
        "conform": ac.conform_skip_fingerprint(root),
        "tables": fingerprint.tables_value(root),
        "stage_a": fingerprint.stage_a(root),
        **{f"lane:{lane}": ac.rebuild_lane_fingerprint(root, lane) for lane in ac.REBUILD_LANES},
    }


def test_a_refuse_why_edit_restamps_the_surface_and_nothing_upstream(tmp_path):
    """The bargain issue #114 struck, stated over every key a cycle consults at once. A refusal's `why` is quoted into the explain text the surface serves, so the surface has to notice a rewording — but nothing that builds an artifact reads it, so run_m1's green, the conform sweep's key, the tables' own stamp, the Stage A record and both suite lanes must all stay exactly where they were, and the pass that follows the edit rebuilds the surface over artifacts it never touches."""
    root = _fake_run_m1_root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("rebuild/out/\n")
    rune = root / "glyph_data" / "runes" / "qsX.yaml"
    rune.write_text(REFUSE_RUNE)
    surface = _stamped_surface(root)
    assert ac.surface_build_skippable(root, surface)
    upstream = _upstream_keys(root)
    assert all(value is not None for value in upstream.values())

    rune.write_text(REFUSE_RUNE.replace("render thick", "render thin"))
    assert not ac.surface_build_skippable(root, surface)
    assert _upstream_keys(root) == upstream


def test_a_ledger_why_edit_restamps_the_surface_and_nothing_upstream(tmp_path):
    """The same bargain, struck for the divergence ledger's class rationales — the whole of what issue #126 bought. The review build copies each class's `why` into the manifest, so the surface still has to notice a rewording; the oracle classifies rows by predicate, status and `no_verdict` and reads no rationale at all, so run_m1's green, the sweep's key, the tables' stamp, the Stage A record and both suite lanes stay exactly where they were. Reclassifying the class is the other half: that has to move the run key and both lanes, and it does."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("rebuild/out/\n")
    ledger = root / fingerprint.DIVERGENCE_LEDGER_LABEL
    surface = _stamped_surface(root)
    assert ac.surface_build_skippable(root, surface)
    upstream = _upstream_keys(root)
    assert all(value is not None for value in upstream.values())

    ledger.write_text("- id: x\n  status: intended\n  why: one, said at greater length\n")
    assert not ac.surface_build_skippable(root, surface)
    assert _upstream_keys(root) == upstream

    ledger.write_text("- id: x\n  status: reviewed-approved\n  why: one, said at greater length\n")
    reclassified = _upstream_keys(root)
    assert reclassified["run_m1"] != upstream["run_m1"]
    assert reclassified["stage_a"] != upstream["stage_a"]
    for lane in ac.REBUILD_LANES:
        assert reclassified[f"lane:{lane}"] != upstream[f"lane:{lane}"]


def test_a_standing_note_reword_moves_the_chain_and_nothing_else(tmp_path):
    """The standing approvals' half of the same bargain, and the one ledger whose prose still costs something. The fill quotes a rule's `note` verbatim into every verdict note it writes, so the plumbing chain has to re-run — while both suite lanes, which read the rules' `match` and never their prose, do not, and no build key reads the file at all. Flipping the rule's verdict is what the lanes are still there for."""
    root = _fake_run_m1_root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("rebuild/out/\n")
    standing = root / "rebuild" / "standing-approvals.yaml"
    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: approve\n    note: one\n"
    )
    master = root / "verdicts-autosave.json"
    master.write_text("{}")
    surface = _stamped_surface(root)
    assert ac.surface_build_skippable(root, surface)
    upstream = _upstream_keys(root)
    chain = ac.plumbing_skip_fingerprint(root, surface, master)
    assert chain is not None

    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: approve\n    note: one, said at greater length\n"
    )
    assert ac.plumbing_skip_fingerprint(root, surface, master) != chain
    assert ac.surface_build_skippable(root, surface)
    assert _upstream_keys(root) == upstream

    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: neither\n    note: one, said at greater length\n"
    )
    flipped = _upstream_keys(root)
    assert flipped["run_m1"] == upstream["run_m1"]
    for lane in ac.REBUILD_LANES:
        assert flipped[f"lane:{lane}"] != upstream[f"lane:{lane}"]


def test_the_census_pins_are_outside_the_rebuild_closure(tmp_path):
    """The census step rewrites the pins mid-pass, so counting them as an input would invalidate the gate's key at record time on every refreshing pass. The suite no longer reads them, so they are exempt and the refresh is invisible to the key."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    pins = tmp_path / "rebuild" / "review-census-pins.json"
    pins.write_text(json.dumps({"invariant": {"classes": 3}, "volatile": {"rows": 1}}))
    assert ac.rebuild_gate_closure_files(tmp_path) == ["rebuild/test_x.py"]
    before = {lane: ac.rebuild_lane_fingerprint(tmp_path, lane) for lane in ac.REBUILD_LANES}
    pins.write_text(json.dumps({"invariant": {"classes": 3}, "volatile": {"rows": 2}}))
    for lane in ac.REBUILD_LANES:
        assert ac.rebuild_lane_fingerprint(tmp_path, lane) == before[lane]


def test_dry_run_plan_skip_run_m1_and_surface_still_runs_the_census():
    """A settled pass rebuilds nothing, but the pins are the cycle's own output rather than a keyed stage: refreshing them from the sidecar costs milliseconds, so the step runs on every pass."""
    plan = _plan(
        skip_run_m1=True,
        run_m1_note="build inputs unchanged since the last green M1 build; --fresh overrides",
        skip_surface=True,
        surface_note="the surface already reflects these inputs byte for byte, stamp included; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv is None
    assert "SKIPPED (build inputs unchanged" in by_name["run_m1"].note
    assert by_name["surface-build"].argv is None
    assert _argv(by_name["census"])[-3:] == ["--update", "--surface", str(ac.REVIEW_OUT)]
    assert by_name["plumbing"].argv is not None
    assert by_name["gate:rebuild-validators"].argv is not None


def test_dry_run_plan_skip_rebuild_lanes():
    plan = _plan(
        skip_contracts=True,
        contracts_note="input closure unchanged since its last green run; --fresh overrides",
        skip_validators=True,
        validators_note="input closure unchanged since its last green run; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    for name in ("gate:rebuild-contracts", "gate:rebuild-validators"):
        assert by_name[name].argv is None
        assert "SKIPPED (input closure unchanged" in by_name[name].note
    assert by_name["gate:conform"].argv is not None
    rendered = _plan_text(plan)
    assert "Lane rebuild-contracts           : SKIPPED" in rendered
    assert "Lane rebuild-validators          : SKIPPED" in rendered


def test_dry_run_plan_skips_only_the_contracts_lane():
    """The common shape of a pass that rebuilt M1: the artifacts moved, so validators must run, while the contracts key — which holds no artifact — is still proved."""
    plan = _plan(skip_contracts=True, contracts_note="input closure unchanged since its last green run")
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:rebuild-contracts"].argv is None
    assert by_name["gate:rebuild-validators"].argv is not None
    rendered = _plan_text(plan)
    assert "Lane rebuild-contracts           : SKIPPED" in rendered
    assert "QUEUED behind gate:conform (queue policy; the contracts lane is not running)" in rendered


def test_dry_run_plan_auto_skip_conform_note():
    plan = _plan(
        skip_conform=True,
        conform_note="font and sweep inputs unchanged since its last green sweep; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert "font and sweep inputs unchanged" in by_name["gate:conform"].note


def test_run_cycle_never_spawns_a_skipped_rebuild_lane(monkeypatch):
    record = {"contracts": 0, "validators": 0}

    def fake_contracts(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        record["contracts"] += 1
        return _lane_verdict("rebuild-contracts")

    def fake_validators(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        record["validators"] += 1
        return _lane_verdict("rebuild-validators")

    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_validators_task", fake_validators)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    note = "input closure unchanged since its last green run; --fresh overrides"
    plan = _plan(skip_contracts=True, contracts_note=note)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record == {"contracts": 0, "validators": 1}
    assert report.gate_contracts.startswith("skipped (input closure unchanged")
    assert report.gate_validators == "green"
    assert report.gate_conform == "green"

    plan = _plan(skip_contracts=True, contracts_note=note, skip_validators=True, validators_note=note)
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) == 0
    assert record == {"contracts": 0, "validators": 1}
    assert report.gate_validators.startswith("skipped (input closure unchanged")


def test_cycle_summary_payload_tells_a_proved_skip_from_a_forced_one():
    report = _green_report()
    plan = _plan(
        skip_contracts=True,
        skip_validators=True,
        skip_conform=True,
        skip_make_test=True,
    )
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["rebuild_contracts"]["skip"] == "proved"
    assert payload["gates"]["rebuild_validators"]["skip"] == "proved"
    assert payload["gates"]["make_test"]["skip"] == "proved"
    assert payload["gates"]["conform"]["skip"] == "forced"
    assert payload["gates"]["js"]["skip"] is None


def test_finish_says_the_cycle_is_complete(monkeypatch, capsys):
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    assert ac._finish(_green_report(), [], _plan()) == 0
    assert "Cycle complete." in capsys.readouterr().out


def test_a_green_finish_closes_on_the_readiness_checklist_instead_of_naming_the_command(monkeypatch, capsys):
    """The answer `make verdict-ready` gives, printed by the pass itself, so nobody is sent to run it after a cycle. A red pass prints none of it: its next command is whatever the failure block names."""
    seen: list[ac.Plan] = []

    def block(plan):
        seen.append(plan)
        return ["Review surface: here", "  ✓ gates: green", "", "READY - adjudicate at the docket"]

    monkeypatch.setattr(ac, "readiness_block", block)
    plan = _plan()
    assert ac._finish(_green_report(), [], plan) == 0
    out = capsys.readouterr().out
    assert seen == [plan]
    assert "READY - adjudicate at the docket" in out
    assert "make verdict-ready" not in out
    assert out.index("  ✓ gates: green") < out.index("Cycle complete.")

    assert ac._finish(_green_report(), ["boom"], plan) == 1
    out = capsys.readouterr().out
    assert seen == [plan]
    assert "READY" not in out and "make verdict-ready" not in out


def test_the_readiness_block_leaves_the_server_row_to_the_recipe_that_serves(
    monkeypatch, real_readiness_block
):
    """`--stop-server` is `make review-cycle` saying it owns the server after the pass, so the checklist the pass closes on must not call a server the recipe is about to start absent. A bare `make artifact-cycle` has no recipe behind it, and its checklist carries the row."""
    from rebuild.tools import verdict_ready

    asked: list[bool] = []

    def fake_readiness(*, with_server, **kwargs):
        asked.append(with_server)
        return {"surface": {"dir": "d", "generated_at": "g", "repo_head": "h"}, "checks": {}}, True

    monkeypatch.setattr(verdict_ready, "readiness", fake_readiness)

    assert real_readiness_block(_plan(recipe_serves=True))[-1].startswith("READY")
    assert real_readiness_block(_plan())[-1].startswith("READY")
    assert asked == [False, True]
    assert real_readiness_block(_plan(review_out=Path("/tmp/rehearsal"))) == []
    assert asked == [False, True]


def test_the_readiness_block_reports_a_checklist_it_could_not_compute(monkeypatch, real_readiness_block):
    from rebuild.tools import verdict_ready

    def boom(**kwargs):
        raise RuntimeError("no manifest")

    monkeypatch.setattr(verdict_ready, "readiness", boom)
    lines = real_readiness_block(_plan())
    assert lines == ["readiness: the checklist could not be computed (RuntimeError('no manifest'))"]


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


def _unsettled_repo(tmp_path, monkeypatch, stamp="2026-07-17T20:24:44Z"):
    """A repo where no keyed stage can auto-skip: run_m1's key matches no record, the make-test closure is unreadable, and neither rebuild lane's key matches. `_settled_repo` is the converged counterpart."""
    _seed_auto_repo(tmp_path, monkeypatch, stamp=stamp)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "verdicts-autosave.json").write_text(json.dumps(_verdicts_doc(stamp, ["u-1"])))
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "key")
    monkeypatch.setattr(ac, "make_test_closure_fingerprint", lambda root=None: None)
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"no-match-{lane}")


def test_main_runs_every_heavy_gate_on_a_pass_that_rebuilds(tmp_path, monkeypatch, capsys):
    """Nothing is ever recorded pending: a pass whose artifacts move still verifies everything it cannot prove unchanged."""
    _unsettled_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "uv run pytest" in _step_lines(out, "gate:rebuild-contracts")
    assert "uv run pytest" in _step_lines(out, "gate:rebuild-validators")
    assert "uv run python -m rebuild.pipeline.run_m1 --conform-only" in _step_lines(out, "gate:conform")
    assert "make test" in _step_lines(out, "gate:make-test")


def test_main_auto_skips_the_contracts_lane_even_when_run_m1_runs_live(tmp_path, monkeypatch, capsys):
    """The contracts closure holds no build artifact at all, so a live M1 rebuild cannot invalidate its key mid-pass — which is why the preflight can settle that skip on every route. The validators closure holds the out/m1 artifacts the rebuild is about to write, so its skip is decided only once run_m1 has finished, and the plan a rebuilding pass prints shows the lane undecided rather than skipped, even over a record that matches the artifacts as they stand now."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"key-{lane}")
    ac.record_green(ac.REBUILD_CONTRACTS_GREEN, "key-contracts")
    ac.record_green(ac.REBUILD_VALIDATORS_GREEN, "key-validators")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED (build inputs unchanged" not in out
    assert "SKIPPED (input closure unchanged" in _step_lines(out, "gate:rebuild-contracts")
    validators_row = _step_lines(out, "gate:rebuild-validators")
    assert "uv run pytest" in validators_row
    assert "run?" in validators_row
    assert ac.VALIDATORS_MAYBE_NOTE in validators_row


def test_main_auto_skips_both_lanes_once_the_artifacts_have_settled(tmp_path, monkeypatch, capsys):
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"key-{lane}")
    ac.record_green(ac.REBUILD_CONTRACTS_GREEN, "key-contracts")
    ac.record_green(ac.REBUILD_VALIDATORS_GREEN, "key-validators")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED (input closure unchanged" in _step_lines(out, "gate:rebuild-contracts")
    assert "SKIPPED (input closure unchanged" in _step_lines(out, "gate:rebuild-validators")


def test_main_forces_both_lanes_under_fresh(tmp_path, monkeypatch, capsys):
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"key-{lane}")
    ac.record_green(ac.REBUILD_CONTRACTS_GREEN, "key-contracts")
    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED" not in out
    assert "uv run pytest" in _step_lines(out, "gate:rebuild-contracts")


def _full_build_step(out: str):
    """The rendered run_m1 step of a plan that builds: the whole point of the negative cases is that this is what got planned instead of the gates-only argv. --jobs rides in front of --kernel-threads only on a box wide enough to want it, and --fresh-oracle-cache only when the caller asked for one."""
    return re.search(
        r"^ +\$ uv run python -m rebuild\.pipeline\.run_m1"
        r"( --jobs \d+)? --kernel-threads \d+( --fresh-oracle-cache)?$",
        _step_lines(out, "run_m1"),
        re.MULTILINE,
    )


def _comparison_side_drift(tmp_path, monkeypatch, moved="rebuild/m1-divergences.yaml"):
    """A repo whose last green M1 build differs from now by one named input and nothing else, with both halves of the route's licence answered true. Every test here varies one of those three things and reads the route back out of the plan."""
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "green-key", files={moved: "before", "uv.lock": "lock-1"})
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {moved: "after", "uv.lock": "lock-1"})
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda root=None: True)


def test_main_re_adjudicates_when_only_comparison_side_inputs_moved(tmp_path, monkeypatch, capsys):
    """The route that makes a ledger edit cost the gates instead of a fixpoint: the tables' stamp cannot see the file that moved, so the enumeration and the font on disk are still the ones the runes describe and the pass re-runs the gates over them. No --kernel-threads goes with it, because nothing on this route enumerates anything to size a fan-out for."""
    _comparison_side_drift(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    row = _step_lines(out, "run_m1")
    assert "rebuild/m1-divergences.yaml" in row
    assert re.search(
        r"^ +\d+ +run +run_m1 +only comparison-side inputs moved[^\n]*the tables and font are reused[^\n]*\n"
        r" +\$ uv run python -m rebuild\.pipeline\.run_m1 --gates-only",
        row,
        re.MULTILINE,
    )
    assert "run_m1 --kernel-threads          : not passed" in out
    assert "inputs moved since its last green" not in row


def test_a_plan_that_skips_run_m1_never_also_reuses_it():
    """The two routes are exclusive and the skip is the stronger claim — nothing moved at all, so there is nothing to re-adjudicate — which is why the plan resolves the pair rather than trusting its caller to. The reuse route is a step that runs, so nothing downstream may read a missing argv as the only shape a non-building pass takes, and it carries no --kernel-threads: there is no fan-out on it to size."""
    both = _plan(skip_run_m1=True, reuse_run_m1=True, run_m1_note="build inputs unchanged")
    assert both.reuse_run_m1 is False
    assert not both.runs("run_m1")
    reuse = _plan(reuse_run_m1=True, run_m1_note="only comparison-side inputs moved")
    assert reuse.runs("run_m1")
    assert "--gates-only" in reuse.argv("run_m1")
    assert "--kernel-threads" not in reuse.argv("run_m1")


def test_main_skips_the_surface_on_the_reuse_route_only_when_stage_a_already_stands(
    tmp_path, monkeypatch, capsys
):
    """A contact-allow bless is the one comparison-side edit outside every Stage A component, so the record the gates-only pass will rewrite is the record already on disk and the surface it feeds cannot move; a ledger edit moves Stage A's data component, so the record on disk is stale until the pass rewrites it and the manifest's match against it proves nothing. The reuse route asks the live sources rather than the record, where the skip route may trust the record because nothing moved at all."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="rebuild/m1-contact-allow.yaml")
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: True)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" in _step_lines(out, "run_m1")
    assert "SKIPPED (the surface already reflects these inputs" in _step_lines(out, "surface-build")

    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: False)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" in _step_lines(out, "run_m1")
    assert "uv run python -m rebuild.review.build" in _step_lines(out, "surface-build")


def test_main_leaves_the_validators_lane_undecided_on_the_reuse_route(tmp_path, monkeypatch, capsys):
    """A contact-allow bless is outside the lane's closure, so the record on disk matches the key the gates-only pass will leave — but the plan cannot promise that, because only the finished pass knows what the out/m1 artifacts came out as. The dry run shows the lane as `run?` with its condition, never as skipped; the skip itself is the pass's to prove (`test_run_cycle_skips_the_validators_lane_after_run_m1_on_the_key_the_finished_artifacts_carry`). --fresh reads no record at all, so under it the row is a plain `run`."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="rebuild/m1-contact-allow.yaml")
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"key-{lane}")
    ac.record_green(ac.REBUILD_VALIDATORS_GREEN, "key-validators")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" in _step_lines(out, "run_m1")
    validators_row = _step_lines(out, "gate:rebuild-validators")
    assert "SKIPPED" not in validators_row
    assert "run?" in validators_row
    assert ac.VALIDATORS_MAYBE_NOTE in validators_row
    assert "uv run pytest" in validators_row

    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    validators_row = _step_lines(out, "gate:rebuild-validators")
    assert "run?" not in validators_row
    assert ac.VALIDATORS_MAYBE_NOTE not in validators_row
    assert "uv run pytest" in validators_row


def test_m1_stage_a_current_compares_the_record_against_the_live_sources(tmp_path, monkeypatch):
    from rebuild.pipeline import fingerprint

    out = tmp_path / "rebuild" / "out" / "m1"
    out.mkdir(parents=True)
    monkeypatch.setattr(
        fingerprint, "stage_a", lambda root: {"data": "d", "baselines": "b", "pipeline_code": "p"}
    )
    assert ac.m1_stage_a_current(tmp_path) is False
    (out / fingerprint.STAGE_A_FILENAME).write_text(
        json.dumps({"format": fingerprint.FORMAT, "data": "d", "baselines": "b", "pipeline_code": "p"})
    )
    assert ac.m1_stage_a_current(tmp_path) is True
    monkeypatch.setattr(
        fingerprint, "stage_a", lambda root: {"data": "moved", "baselines": "b", "pipeline_code": "p"}
    )
    assert ac.m1_stage_a_current(tmp_path) is False


def test_main_rebuilds_when_the_tables_on_disk_no_longer_carry_their_stamp(tmp_path, monkeypatch, capsys):
    """The green record alone never licenses the reuse. It proves the artifacts once came from a complete build of every build-side input; only the stamp proves none of those inputs has moved since, and without it the font beside the tables is a font nobody can name the sources of."""
    _comparison_side_drift(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda root=None: False)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "inputs moved since its last green: rebuild/m1-divergences.yaml (changed)" in _step_lines(
        out, "run_m1"
    )
    assert _full_build_step(out) is not None
    assert "--gates-only" not in out


def test_main_rebuilds_when_anything_build_side_moved(tmp_path, monkeypatch, capsys):
    """One build-side label among the moved ones is enough, however many comparison-side ones ride beside it: the artifacts on disk answer for sources that no longer exist, and re-running the gates over them would compare the new runes against the old font."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="glyph_data/runes/qsX.yaml")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "inputs moved since its last green" in _step_lines(out, "run_m1")
    assert "--gates-only" not in out


def test_main_takes_no_route_at_all_under_fresh(tmp_path, monkeypatch, capsys):
    """--fresh is the escape hatch for exactly the case the route cannot see: an artifact on disk that is wrong for a reason no input fingerprint records."""
    _comparison_side_drift(tmp_path, monkeypatch)
    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" not in out
    assert _full_build_step(out) is not None


def test_run_cycle_skips_the_sweep_after_run_m1_on_the_key_the_finished_artifacts_carry(
    monkeypatch, tmp_path, capsys
):
    """The sweep's skip is decided after run_m1 rather than in the plan, because only a finished build knows what the font came out as — and the three routes into it (skipped, re-adjudicated, rebuilt) all land on this one key. A skip taken over the artifacts the pass is leaving is proved rather than forced, which is what `review/status.py` reads to call a surface sitting-ready."""
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"rfp-{lane}")
    ac.record_green(ac.CONFORM_GREEN, "cfp")
    swept: list[list[str]] = []

    def conform_spy(pool_policy, make_fut, spawn, emit, registry, argv):
        swept.append(argv)
        return _conform_verdict()

    monkeypatch.setattr(ac, "_gate_conform_task", conform_spy)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) == 0
    assert swept == []
    assert report.conform_proven is True
    assert report.gate_conform == f"skipped ({ac.CONFORM_SKIP_NOTE})"
    skipped = [line for line in capsys.readouterr().out.splitlines() if "SKIPPED after run_m1 — " in line]
    assert len(skipped) == 1 and "gate:conform" in skipped[0]
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["conform"]["skip"] == "proved"
    assert payload["gates"]["conform"]["green"] is False


def test_run_cycle_sweeps_when_the_finished_artifacts_carry_no_green(monkeypatch, tmp_path, capsys):
    """The same decision the other way, and the reason the skip cannot ride the plan: a pass whose run_m1 moved the font has to sweep it, and the plan was resolved before anything knew that."""
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: f"rfp-{lane}")
    ac.record_green(ac.CONFORM_GREEN, "a-font-ago")
    swept: list[list[str]] = []

    def conform_spy(pool_policy, make_fut, spawn, emit, registry, argv):
        swept.append(argv)
        return _conform_verdict()

    monkeypatch.setattr(ac, "_gate_conform_task", conform_spy)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) == 0
    assert len(swept) == 1
    assert report.conform_proven is False
    assert report.gate_conform == "green"
    assert "SKIPPED after run_m1" not in capsys.readouterr().out
    assert ac.cycle_summary_payload(report, [], plan, "ok")["gates"]["conform"]["skip"] is None


def _validators_lane_repo(monkeypatch, tmp_path):
    """The validators lane's skip decision in isolation: both gate keys stubbed, the conform record absent so the sweep's own decision cannot color the run, and every other stage green. The validators task is the one thing each test here supplies, since whether it was spawned is the question."""
    monkeypatch.setattr(ac, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "REBUILD_VALIDATORS_GREEN", tmp_path / "rebuild-validators-green.json")
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", tmp_path / "rebuild-contracts-green.json")
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)


def test_run_cycle_skips_the_validators_lane_after_run_m1_on_the_key_the_finished_artifacts_carry(
    monkeypatch, tmp_path, capsys
):
    """The lane's key holds the out/m1 artifacts, so its skip is decided where conform's is — after run_m1, over the artifacts the pass is leaving — and the gates-only route is what that decision is for: a contact-allow bless re-adjudicates the tables and font on disk, moves nothing the lane reads, and the lane skips on its record. The skip is proved rather than forced, which is what `review/status.py` reads to call a surface sitting-ready, and the record it skipped on is left exactly as it was — a skip is not a green run and records nothing."""
    _validators_lane_repo(monkeypatch, tmp_path)
    ac.record_green(ac.REBUILD_VALIDATORS_GREEN, "rfp-validators")
    ran: list[list[str]] = []

    def validators_spy(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        ran.append(argv)
        return _lane_verdict("rebuild-validators")

    monkeypatch.setattr(ac, "_gate_validators_task", validators_spy)

    plan = _plan(reuse_run_m1=True, run_m1_note="only comparison-side inputs moved", record_greens=True)
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) == 0
    assert ran == []
    assert report.validators_proven is True
    assert report.gate_validators == f"skipped ({ac.VALIDATORS_SKIP_NOTE})"
    assert report.gate_contracts == "green"
    skipped = [line for line in capsys.readouterr().out.splitlines() if "SKIPPED after run_m1 — " in line]
    assert len(skipped) == 1 and "gate:rebuild-validators" in skipped[0]
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["rebuild_validators"]["skip"] == "proved"
    assert payload["gates"]["rebuild_validators"]["green"] is False
    assert payload["gates"]["rebuild_contracts"]["skip"] is None
    record = ac.read_green_record(ac.REBUILD_VALIDATORS_GREEN)
    assert record is not None and record["fingerprint"] == "rfp-validators"
    rows = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert rows["gate:rebuild-validators"].outcome == "skipped"
    assert rows["gate:rebuild-validators"].figure == ""


def test_run_cycle_runs_the_validators_lane_when_the_finished_artifacts_carry_no_green(
    monkeypatch, tmp_path, capsys
):
    """The same decision the other way: a pass whose run_m1 moved the out/m1 artifacts has to run the lane over them, and the plan was resolved before anything knew that. The green it then records is over the key the finished artifacts carry, which is the key the next pass will compare."""
    _validators_lane_repo(monkeypatch, tmp_path)
    ac.record_green(ac.REBUILD_VALIDATORS_GREEN, "an-artifact-ago")
    ran: list[list[str]] = []

    def validators_spy(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        ran.append(argv)
        return _lane_verdict("rebuild-validators")

    monkeypatch.setattr(ac, "_gate_validators_task", validators_spy)

    plan = _plan(record_greens=True)
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) == 0
    assert ran == [plan.argv("gate:rebuild-validators")]
    assert report.validators_proven is False
    assert report.gate_validators == "green"
    assert "SKIPPED after run_m1 — input closure" not in capsys.readouterr().out
    assert ac.cycle_summary_payload(report, [], plan, "ok")["gates"]["rebuild_validators"]["skip"] is None
    record = ac.read_green_record(ac.REBUILD_VALIDATORS_GREEN)
    assert record is not None and record["fingerprint"] == "rfp-validators"


def test_fresh_runs_the_validators_lane_over_a_matching_record_and_a_red_result_deletes_it(
    monkeypatch, tmp_path
):
    """--fresh reads no green, so a record that matches the finished artifacts still skips nothing — the lane runs. And a run that comes back red over the key its record claims is proof the record was wrong about this exact content, so the record goes with the failure rather than standing over it for the next pass to skip on."""
    _validators_lane_repo(monkeypatch, tmp_path)
    ac.record_green(ac.REBUILD_VALIDATORS_GREEN, "rfp-validators")
    ran: list[list[str]] = []

    def validators_red(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        ran.append(argv)
        return _lane_verdict(
            "rebuild-validators",
            status="FAILED (1 unexplained)",
            failed_ids=["rebuild/test_rule_witnesses.py::test_x"],
        )

    monkeypatch.setattr(ac, "_gate_validators_task", validators_red)

    plan = _plan(fresh=True, record_greens=True)
    report = ac.CycleReport()
    assert ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()) != 0
    assert len(ran) == 1
    assert report.validators_proven is False
    assert report.gate_validators_green is False
    assert ac.read_green_record(ac.REBUILD_VALIDATORS_GREEN) is None


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
    files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
    files["oracle"].write_text(json.dumps({"unmatched": 7, "multi_matched": 0}))

    def no_spawn(*a, **k):
        raise AssertionError("skip path must not spawn")

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=no_spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        skip=True,
        skip_note="test skip",
    )
    assert gate is not None and gate.ok
    assert report.unmatched == 7
    assert files["pipeline"].exists()


def test_do_run_m1_records_green_only_when_fingerprint_stable(monkeypatch, tmp_path):
    """A green is recorded only when the inputs held still for the whole build. The record's file list is stubbed for the same reason its fingerprint is: `run_m1_skip_files(ROOT)` opens the live contact allow-list, and the closure exempts that file on the grounds that no test in either lane reads it."""
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-divergences.yaml": "d1"})

    def write_summaries(*a, **k):
        files["pipeline"].write_text(json.dumps({"defect_errors": []}))
        files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
        files["oracle"].write_text(json.dumps({"unmatched": 0, "multi_matched": 0}))
        return _step("run_m1", 0)

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=write_summaries,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
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
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
        record=True,
        fingerprint="fp-from-before-a-mid-run-edit",
    )
    assert gate is not None and gate.ok
    assert ac.read_green_record(green) is None


def test_do_run_m1_reuse_spares_the_summary_the_gates_only_pass_rewrites(monkeypatch, tmp_path):
    """The one asymmetry of the middle route. `--gates-only` rewrites the defect fields of the build's own pipeline_summary.json in place and refuses outright without one, so clearing it before the spawn would take down the pass that was supposed to be cheap; the two gate summaries are the child's own output and are cleared exactly as a full build clears them, so a child that dies mid-pass cannot leave last pass's verdicts to be judged as this one's. Everything after the spawn is the full build's path, the green included."""
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-divergences.yaml": "d2"})
    for path in files.values():
        path.write_text(json.dumps({"stale": True}))
    files["pipeline"].write_text(json.dumps({"defect_errors": [], "gsub_rule_count": 4212}))
    survivors: list[str] = []
    spawned: list[str] = []

    def gates_only(name, argv, **kwargs):
        spawned.append(name)
        survivors.extend(sorted(key for key, path in files.items() if path.exists()))
        files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
        files["oracle"].write_text(json.dumps({"unmatched": 3, "multi_matched": 0}))
        return _step("run_m1", 0)

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=gates_only,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--gates-only"],
        reuse=True,
        record=True,
        fingerprint="fp-live",
    )
    assert survivors == ["pipeline"]
    assert spawned == [ac.RUN_M1_REUSE_STEP] != ["run_m1"]
    assert gate is not None and gate.ok
    assert report.unmatched == 3
    assert json.loads(files["pipeline"].read_text())["gsub_rule_count"] == 4212
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "fp-live"
    assert record["files"] == {"rebuild/m1-divergences.yaml": "d2"}


def test_do_run_m1_a_full_build_clears_the_summary_the_reuse_route_keeps(monkeypatch, tmp_path):
    """The other side of the same rule, so the exemption cannot quietly widen: a build that makes its own tables makes its own pipeline summary too, and a stale one left in place would be judged as this build's if the child died before writing one."""
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    for path in files.values():
        path.write_text(json.dumps({"stale": True}))
    survivors: list[str] = []
    spawned: list[str] = []

    def full_build(name, argv, **kwargs):
        spawned.append(name)
        survivors.extend(sorted(key for key, path in files.items() if path.exists()))
        return _step("run_m1", 0)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=full_build,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
    )
    assert survivors == []
    assert spawned == ["run_m1"]
    assert gate is None


def test_do_run_m1_red_deletes_matching_green(monkeypatch, tmp_path):
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", green)
    ac.record_green(green, "fp-1")

    def write_red(*a, **k):
        files["pipeline"].write_text(json.dumps({"defect_errors": ["boom"]}))
        files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
        files["oracle"].write_text(json.dumps({"unmatched": 0, "multi_matched": 0}))
        return _step("run_m1", 0)

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=write_red,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
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
    contracts_green = tmp_path / "rebuild-contracts-green.json"
    validators_green = tmp_path / "rebuild-validators-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", conform_green)
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", contracts_green)
    monkeypatch.setattr(ac, "REBUILD_VALIDATORS_GREEN", validators_green)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "cfp")
    _patch_gate_fingerprints(monkeypatch)
    keys = {"conform": "cfp", "contracts": "rfp-contracts", "validators": "rfp-validators"}
    plan = _plan()
    report = ac.CycleReport()
    report.gate_conform = "green"
    report.gate_conform_green = True
    report.gate_contracts = "green (annotated)"
    report.gate_contracts_green = True
    report.contracts_recordable = True
    report.gate_validators = "green"
    report.gate_validators_green = True
    report.validators_recordable = True
    ac._record_gate_greens(report, plan, keys, ac._Emitter())
    for path, expected in (
        (conform_green, "cfp"),
        (contracts_green, "rfp-contracts"),
        (validators_green, "rfp-validators"),
    ):
        record = ac.read_green_record(path)
        assert record is not None
        assert record["fingerprint"] == expected

    conform_green.unlink()
    contracts_green.unlink()
    validators_green.unlink()
    moved = {"conform": "moved", "contracts": "moved-too", "validators": "moved-as-well"}
    ac._record_gate_greens(report, plan, moved, ac._Emitter())
    for path in (conform_green, contracts_green, validators_green):
        assert ac.read_green_record(path) is None

    ac.record_green(contracts_green, "rfp-contracts")
    ac.record_green(validators_green, "rfp-validators")
    report.gate_contracts = "FAILED (1 unexplained)"
    report.gate_contracts_green = False
    report.contracts_recordable = False
    ac._record_gate_greens(report, plan, keys, ac._Emitter())
    assert ac.read_green_record(contracts_green) is None
    surviving = ac.read_green_record(validators_green)
    assert surviving is not None
    assert surviving["fingerprint"] == "rfp-validators"

    report.gate_validators = "FAILED (1 unexplained)"
    report.gate_validators_green = False
    report.validators_recordable = False
    ac._record_gate_greens(report, plan, keys, ac._Emitter())
    assert ac.read_green_record(validators_green) is None

    ac.record_green(conform_green, "cfp")
    report.gate_conform = "FAILED"
    report.gate_conform_green = False
    ac._record_gate_greens(report, plan, {"conform": "cfp"}, ac._Emitter())
    assert ac.read_green_record(conform_green) is None


def test_classify_rebuild_recordable_whenever_it_is_green():
    """A green run is always recordable; only a failure withholds the record."""
    clean = ac.classify_rebuild_output("", 0, "rebuild-contracts")
    assert clean.status == "green"
    assert clean.recordable
    hard = ac.classify_rebuild_output("FAILED rebuild/test_settle.py::test_x", 1, "rebuild-contracts")
    assert not hard.recordable


def test_the_census_diff_is_a_child_of_the_census_step_and_prints_in_full(tmp_path, capsys):
    """The diff is what a commit accepts, so it is the one child whose plain lines belong on the terminal verbatim — copy-pasteable, with no step column in front of them. It is also not a step of the plan: registering it under census puts its lines in census's log and its own summary under census's column, without a second banner for a step that is already open. The step stays open until the diff has run, because a sub-step spawned under a closed parent opens a state nobody closes — a log handle held to the end of the pass and a heartbeat for a step that finished."""
    plan = _plan()
    log_dir = tmp_path / "logs"
    registry = ac._ChildRegistry()
    report = ac.CycleReport()
    diff = '-  "rows": 1\n+  "rows": 2'

    def spawn(name, argv, *, emit, registry, stream, **passthrough):
        script = "pass" if name == "census" else f"print({diff!r})"
        return ac._run_step(name, [sys.executable, "-c", script], emit=emit, registry=registry, stream=stream)

    with console.Digest(steps=[step.name for step in plan.steps], log_dir=log_dir) as digest:
        ac._do_census(report, spawn=spawn, emit=digest, registry=registry, plan=plan)
        assert digest._open == {}

    out = capsys.readouterr().out
    assert '-  "rows": 1' in out.splitlines()
    assert '+  "rows": 2' in out.splitlines()
    assert out.count("---- step ") == 1
    assert "review it at commit time" in report.census_status
    census_log = (log_dir / f"{_step_number(plan, 'census'):02d}-census.log").read_text()
    assert diff in census_log
    assert not (log_dir / "00-git-diff.log").exists()


def test_the_summary_table_carries_each_steps_figure_and_what_it_cost():
    """The table is the pass in one screen: what ran, how it came out, its own headline number, and what it cost. A step that did not run contributes no figure — its reason is the plan block's, and a run_m1 the plan skipped would otherwise report the last build's unmatched count as though this pass had counted it. A failed gate's figure keeps only what the outcome column has not already said: `FAILED  3 unexplained`, never `FAILED  FAILED (3 unexplained)`. And the retention row tells the two ways it can be missing apart — `skipped` when the plan ruled it out, `not run` when a failure or a SIGINT stopped the pass before `_finish` reached it."""
    plan = _plan(skip_conform=True, conform_note=ac.CONFORM_SKIP_NOTE)
    report = ac.CycleReport()
    report.unmatched = 8423
    report.pins_pass = True
    report.surface_units = 15903
    report.surface_rows = 81894
    report.gate_conform = f"skipped ({ac.CONFORM_SKIP_NOTE})"
    report.gate_contracts = "FAILED (3 unexplained)"
    report.gate_contracts_green = False
    report.retention_figure = "removed 1 snapshot, 1 carried, 0 build logs, 0 stashes; journal intact"
    report.step_seconds = {"run_m1": 1988.0, "surface-build": 61.0, "gate:rebuild-contracts": 92.0}
    report.step_returncodes = {"run_m1": 0, "surface-build": 0, "gate:rebuild-contracts": 1}

    rows = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert rows["run_m1"].figure == "8,423 unmatched, pins pass"
    assert rows["run_m1"].outcome == "ok"
    assert rows["run_m1"].seconds == 1988.0
    assert rows["surface-build"].figure == "15,903 units, 81,894 rows"
    assert rows["gate:conform"].outcome == "skipped"
    assert rows["gate:conform"].figure == ""
    assert rows["gate:rebuild-contracts"].outcome == "FAILED"
    assert rows["gate:rebuild-contracts"].figure == "3 unexplained"
    assert rows["gate:js"].outcome == "not run"
    assert rows["retention"].outcome == "not run"
    assert [row.number for row in ac.summary_rows(report, plan, retention_ran=False)] == list(
        range(1, len(plan.steps) + 1)
    )
    swept = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=True)}
    assert swept["retention"].outcome == "ok"
    assert swept["retention"].figure == report.retention_figure

    ruled_out = _plan(keep_history=True, skip_conform=True, conform_note=ac.CONFORM_SKIP_NOTE)
    parked = {row.name: row for row in ac.summary_rows(report, ruled_out, retention_ran=False)}
    assert parked["retention"].outcome == "skipped"

    stale = _plan(
        skip_run_m1=True,
        run_m1_note="build inputs unchanged",
        skip_conform=True,
        conform_note=ac.CONFORM_SKIP_NOTE,
    )
    reused = {row.name: row for row in ac.summary_rows(report, stale, retention_ran=False)}
    assert reused["run_m1"].outcome == "skipped"
    assert reused["run_m1"].figure == ""

    reuse = _plan(reuse_run_m1=True, run_m1_note="only comparison-side inputs moved")
    reuse_report = ac.CycleReport()
    ac._timed_spawn(lambda name, argv, **kw: ac._StepResult(name, 0, "", "", 4.0), reuse_report)(
        ac.RUN_M1_REUSE_STEP,
        ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--gates-only"],
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        stream=False,
    )
    reused = {row.name: row for row in ac.summary_rows(reuse_report, reuse, retention_ran=False)}
    assert reused["run_m1"].outcome == "ok"
    assert reused["run_m1"].seconds == 4.0


def test_a_step_that_came_back_nonzero_never_reads_as_an_ok_row():
    """The outcome column is filled from what the step's child came to, never from the seconds it cost — a column filled from seconds reads `ok` for every step that ran at all: `ok  5 unmatched, PINS FAILED` for a run_m1 whose Manual pins failed, and `ok` beside a blank figure for a surface build whose child died. The two informational steps are the deliberate exception — neither gates anything, and each already says what went wrong in its own figure."""
    plan = _plan()
    report = ac.CycleReport()
    report.unmatched = 5
    report.pins_pass = False
    report.run_m1_failed = True
    report.census_status = "update FAILED (exit 2) — informational"
    report.job_costs_status = "OVERRUN (a measured peak outruns its checked-in constant)"
    report.step_seconds = {"run_m1": 9.0, "surface-build": 3.0, "census": 1.0, "job-costs": 1.0}
    report.step_returncodes = {"run_m1": 0, "surface-build": 1, "census": 2, "job-costs": 1}

    rows = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert rows["run_m1"].outcome == "FAILED"
    assert rows["run_m1"].figure == "5 unmatched, PINS FAILED"
    assert rows["surface-build"].outcome == "FAILED"
    assert rows["census"].outcome == "ok"
    assert rows["job-costs"].outcome == "ok"


def test_every_spawned_step_closes_with_its_own_figure_and_peak(capsys, tmp_path):
    """`ok` on its own sends the reader to the summary table for the number they were waiting for. No stage knows its figure at the moment its child exits — run_m1 has three summaries to read, the surface build a manifest to open, the chain its sections to split — so the closing line waits for the stage that reads them, and every step signs off with the figure its row will carry."""
    surface = _built_surface(tmp_path, units=15903, rows=81894, batches=16, echo_groups=402)

    def spawn(name, argv, *, emit, registry, stream, **passthrough):
        if name == "run_m1":
            for key, payload in _pass_summaries().items():
                ac.M1_SUMMARY_FILES[key].write_text(json.dumps(payload))
            return ac._StepResult(name, 0, "", "", 1988.0, 19_600_000_000)
        return ac._StepResult(name, 0, "", "", 1.0, 1_000_000_000)

    plan = _plan(skip_gates=True, review_out=surface)
    report = ac.CycleReport()
    digest = console.Digest(steps=[step.name for step in plan.steps])
    assert ac._run_cycle(plan, report, digest, ac._ChildRegistry(), spawn=spawn) == 0

    closing = [line for line in capsys.readouterr().out.splitlines() if "  cycle " in line]
    assert any(line.endswith("ok  8,423 unmatched, pins pass  rss 19.6G") for line in closing), closing
    assert any("ok  15,903 units, 81,894 rows  rss 1.0G" in line for line in closing), closing


def test_do_census_updates_the_pins_and_reports_the_diff():
    """The checked-in pins are the last accepted census, so the step always rewrites them and always shows the diff: what it prints is exactly what a commit would be accepting."""
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 0, stdout='-  "rows": 1\n+  "rows": 2\n' if name == "git-diff" else "")

    report = ac.CycleReport()
    ac._do_census(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["census", "git-diff"]
    assert report.census_status == (
        "updated (diff vs the last accepted census shown above — review it at commit time)"
    )


def test_do_census_says_so_when_the_refresh_moved_nothing():
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 0)

    report = ac.CycleReport()
    ac._do_census(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["census", "git-diff"]
    assert report.census_status == "updated (matches the last accepted census)"


def test_do_census_reports_a_failed_refresh_and_diffs_nothing():
    """A refresh can fail on a surface that predates the census sidecar. It is informational, so there is nothing to diff and nothing to record — the next pass that rebuilds the surface heals it."""
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 2, stderr="no census-facts.json beside the manifest")

    report = ac.CycleReport()
    ac._do_census(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["census"]
    assert report.census_status == "update FAILED (exit 2) — informational"


def test_a_failed_census_refresh_never_fails_the_cycle(monkeypatch):
    def census_dies(report, *, spawn, emit, registry, plan):
        report.census_status = "update FAILED (exit 2) — informational"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_dies)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert report.census_status == "update FAILED (exit 2) — informational"
    assert json.loads(ac.CYCLE_SUMMARY.read_text())["failures"] == []


def test_a_rehearsal_never_runs_the_census(monkeypatch, tmp_path):
    """The checked-in pins describe the live surface. A rehearsal builds somewhere else, so refreshing them from it would replace the accepted census with one of a surface nobody serves."""

    def census_must_not_run(*args, **kwargs):
        raise AssertionError("a rehearsal must not run the census")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_census", census_must_not_run)

    plan = _plan(review_out=tmp_path / "reh")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert report.census_status == "skipped (rehearsal: the checked-in pins track the live surface)"


def test_do_job_costs_reports_a_clean_check():
    """Nothing to show and nothing to accept: every measured unit still fits the constant that divides the box by it, so the step is one file read and the summary says so in a line."""
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 0)

    report = ac.CycleReport()
    ac._do_job_costs(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["job-costs"]
    assert report.job_costs_status == "checked (every measured unit's peak fits its checked-in constant)"
    assert report.job_costs_ok is True


def test_do_job_costs_diffs_the_constants_when_the_check_trips():
    """A trip asks one further question — has the constant already been re-seeded here, so the commit in hand is already the acceptance? — and the diff answers it. It is conditional where the census's is not: these four files hold a great deal besides their constants, so an unconditional diff would print unrelated work every pass."""
    calls: list[str] = []
    seen: dict[str, list[str]] = {}

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        seen[name] = argv
        if name == "job-costs":
            return _step(name, 1, stdout="  OVERRUN   : max 13.10 GB exceeds the constant by 9%")
        return _step(
            name, 0, stdout="-CONFIG_PEAK_BYTES = 5_500_000_000\n+CONFIG_PEAK_BYTES = 6_500_000_000\n"
        )

    report = ac.CycleReport()
    ac._do_job_costs(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["job-costs", "job-costs-diff"]
    assert seen["job-costs-diff"] == [
        "git",
        "diff",
        "--",
        "conftest.py",
        "rebuild/conftest.py",
        "rebuild/pipeline/kernel_exec.py",
        "rebuild/tools/artifact_cycle.py",
    ]
    assert report.job_costs_status.startswith("OVERRUN")
    assert "a constant has already moved in the working tree" in report.job_costs_status
    assert report.job_costs_ok is False


def test_a_tripped_check_over_an_unmoved_tree_says_only_that_it_tripped():
    """The already-moved clause is a fact about the working tree, not about the trip: with the constants untouched there is nothing to claim, and the status must not imply the acceptance is already drafted."""

    def spawn(name, argv, *, emit, registry, stream):
        return _step(name, 1 if name == "job-costs" else 0)

    report = ac.CycleReport()
    ac._do_job_costs(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert report.job_costs_status.startswith("OVERRUN")
    assert "working tree" not in report.job_costs_status
    assert report.job_costs_ok is False


def test_do_job_costs_reports_a_broken_check_without_diffing():
    """Exit 1 is the tool's verdict; anything else is the tool failing to reach one. There is then nothing to have already accepted, so nothing to diff — and the judgment stays None, which is neither green nor an overrun."""
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 2, stderr="Traceback (most recent call last):")

    report = ac.CycleReport()
    ac._do_job_costs(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["job-costs"]
    assert report.job_costs_status == "check FAILED (exit 2) — informational"
    assert report.job_costs_ok is None


def test_a_tripped_job_costs_check_never_fails_the_cycle(monkeypatch):
    """A stale divisor makes a pool the wrong width; it does not make an artifact wrong. So the trip is loud in the summary and in the payload, and contributes nothing to the failure list of a pass whose artifacts are green."""

    def job_costs_trips(report, *, spawn, emit, registry, plan):
        report.job_costs_status = "OVERRUN (a measured peak outruns its checked-in constant)"
        report.job_costs_ok = False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_job_costs", job_costs_trips)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    summary = json.loads(ac.CYCLE_SUMMARY.read_text())
    assert summary["failures"] == []
    assert summary["job_costs_status"].startswith("OVERRUN")
    assert summary["job_costs_ok"] is False
    assert "job_costs" not in summary["gates"]


def test_the_job_costs_check_runs_in_a_rehearsal_too(monkeypatch, tmp_path):
    """The census's rehearsal skip is not a reason this step can borrow: the pins track the live surface, which a rehearsal never writes, while the timings journal is appended to by every pass alike. A rehearsal's pools cost what they cost, so their measurements are as good as any."""
    ran: list[str] = []

    def job_costs_ran(report, *, spawn, emit, registry, plan):
        ran.append(plan.argv("job-costs")[-1])
        report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
        report.job_costs_ok = True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_job_costs", job_costs_ran)

    plan = _plan(review_out=tmp_path / "reh")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert plan.runs("job-costs") is True
    assert ran == ["--check"]
    assert report.job_costs_ok is True


def test_the_plan_checks_job_costs_after_the_gates():
    """The check reads a journal this pass's own pools append to at their terminal summaries, so it can only be honest about this pass once the gates have joined — placed beside the census it would be reporting the previous pass's measurements."""
    plan = _plan()
    names = [step.name for step in plan.steps]
    by_name = {step.name: step for step in plan.steps}
    assert names.index("job-costs") > names.index("gate:rebuild-validators")
    assert names.index("job-costs") > names.index("census")
    # Retention is a step of the plan but not a spawn: it runs inside _finish, after this check has already been made. The plan is what the cycle prints, so the two have to be printed in the order they happen or the printout describes a pass nobody ran.
    assert names.index("job-costs") < names.index("retention")
    assert _argv(by_name["job-costs"]) == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.calibrate_budgets",
        "--check",
    ]


def test_the_plan_checks_job_costs_even_when_the_gates_are_skipped():
    """The step is never skipped: --skip-gates suppresses the five gates, and this is not one of them — exactly as the census is not."""
    assert _plan(skip_gates=True).runs("job-costs") is True


def test_both_rebuild_lanes_are_submitted_once_the_surface_build_settles(monkeypatch):
    """The submission window is the same for both lanes: after the surface build and before everything else in the build lane. Validators waits for the surface because its session fixture reads the live surface whenever it is provably fresh, and a lane started mid-rewrite would see the manifest without the sidecar review.build writes after it; contracts reads no artifact but must not put a full-width pool beside the build either. Neither waits for anything further, because the carry, the merge and the census are not inputs to the suite."""
    spawned = {"gate:rebuild-contracts": threading.Event(), "gate:rebuild-validators": threading.Event()}
    order: list[str] = []

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        if name in spawned:
            spawned[name].set()
        return _step(name, 0)

    def surface_first(report, *, spawn, emit, registry, review_out, **_):
        assert not any(event.is_set() for event in spawned.values())
        order.append("surface")
        report.surface_units = 1
        return True

    def census_after(report, *, spawn, emit, registry, plan):
        assert all(event.wait(timeout=30) for event in spawned.values())
        order.append("census")
        report.census_status = "updated (matches the last accepted census)"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_surface_build", surface_first)
    monkeypatch.setattr(ac, "_do_census", census_after)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)

    assert rc == 0
    assert order == ["surface", "census"]
    assert report.gate_contracts == "green"
    assert report.gate_validators == "green"


def test_surface_build_failure_leaves_both_rebuild_lanes_not_run(monkeypatch, capsys):
    """A failed surface build stops the build lane before either submission, so there is no future to join — both gates report why they never ran."""
    calls = {"contracts": 0, "validators": 0}

    def fake_contracts(pool_policy, conform_fut, make_fut, spawn, emit, registry, argv):
        calls["contracts"] += 1
        return _lane_verdict("rebuild-contracts")

    def fake_validators(pool_policy, conform_fut, contracts_fut, make_fut, spawn, emit, registry, argv):
        calls["validators"] += 1
        return _lane_verdict("rebuild-validators")

    def failing_surface(report, *, spawn, emit, registry, review_out, **_):
        return False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", failing_surface)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_validators_task", fake_validators)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert calls == {"contracts": 0, "validators": 0}
    assert report.gate_contracts == "not run (surface build failed)"
    assert report.gate_validators == "not run (surface build failed)"
    assert "surface rebuild failed" in capsys.readouterr().out


def test_run_m1_failure_still_leaves_both_rebuild_lanes_not_run(monkeypatch, capsys):
    """The one early return that predates the submissions: nothing was queued, so both gates report why they never ran."""

    def failing_run_m1(report, *, spawn, emit, registry, **_):
        return _run_m1_red("Manual-pin gate failed (2 disagreements)")

    def must_not_run(*args, **kwargs):
        raise AssertionError("no rebuild lane may be submitted when run_m1's gate fails")

    monkeypatch.setattr(ac, "_do_run_m1", failing_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", must_not_run)
    monkeypatch.setattr(ac, "_gate_validators_task", must_not_run)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_contracts == "not run (run_m1 gate failed)"
    assert report.gate_validators == "not run (run_m1 gate failed)"
    assert "Manual-pin gate failed" in capsys.readouterr().out


def test_plumbing_skip_fingerprint_moves_with_every_input(tmp_path):
    """Every input, and the standing approvals by raw bytes — alone among the ledgers the rebuild lanes hash prose-blind. The fill quotes each rule's `note` verbatim into the verdict note it writes, so a reword changes what the chain would put in the store and has to re-run it."""
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

    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text(
        "rules:\n  - id: r1\n    verdict: approve\n    note: one\n"
    )
    noted = ac.plumbing_skip_fingerprint(tmp_path, surface, master)
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text(
        "rules:\n  - id: r1\n    verdict: approve\n    note: two, at greater length\n"
    )
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != noted

    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-18T00:00:00Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != base

    (surface / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-17T20:24:44Z",
                "inputs_fingerprint": {"runes": "aaa", "static": "refreshed"},
            }
        )
    )
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) == base

    (surface / "manifest.json").write_text(json.dumps({"generated_at": "2026-07-17T20:24:44Z"}))
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) is None


def test_plumbing_skip_fingerprint_covers_the_chains_own_code(tmp_path):
    """Every other stage's key folds in its own executable; this chain's lives in rebuild/tools/, which no other fingerprint reads. Without it a fix to a fill's matcher would be skipped as already proven and silently never run. The negative half is what issue #117 bought: the driver, the timings journal and the two width yardsticks share that directory but run no step of the chain, so an edit to one leaves the key exactly where it was and the letters stay on screen."""
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

    outside = ("artifact_cycle.py", "cycle_timings.py", "memory_budget.py", "peak_rss.py")
    for name in outside:
        (tools / name).write_text("x = 1\n")
    unmoved = ac.plumbing_skip_fingerprint(tmp_path, surface, master)
    for name in outside:
        (tools / name).write_text("x = 2\n")
        assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) == unmoved, name
        (tools / name).write_text("x = 1\n")

    (tools / "review_server.py").write_text("x = 1\n")
    probed = ac.plumbing_skip_fingerprint(tmp_path, surface, master)
    (tools / "review_server.py").write_text("x = 2\n")
    assert ac.plumbing_skip_fingerprint(tmp_path, surface, master) != probed


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
    assert by_name["plumbing"].argv is None
    assert by_name["plumbing"].note == f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})"
    assert by_name["snapshot"].argv is None
    assert by_name["snapshot"].note.startswith(f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})")
    assert plan.complaints_note == ac.PLUMBING_SKIP_NOTE
    assert by_name["census"].argv is not None


def test_dry_run_plan_store_only_merges_the_master_and_takes_no_snapshot():
    """The surface did not move, so the carry would resolve every unit against itself and its re-prefixed notes could never outrank the store. What is left is the one input the store's own hash cannot see — the master — so the chain merges that directly, and there is no snapshot to take."""
    plan = _plan(store_only=True)
    by_name = {step.name: step for step in plan.steps}
    assert plan.carry_out is None
    assert by_name["snapshot"].argv is None
    assert "the surface did not move" in by_name["snapshot"].note
    argv = _argv(by_name["plumbing"])
    assert "--source" not in argv
    assert argv[argv.index("--merge-master") + 1] == "v.json"
    assert "--no-merge" not in argv
    assert plan.do_merge
    assert "the carry is the identity" in by_name["plumbing"].note


def test_dry_run_plan_store_only_still_honors_no_merge():
    plan = _plan(store_only=True, no_merge=True)
    by_name = {step.name: step for step in plan.steps}
    assert "--no-merge" in _argv(by_name["plumbing"])
    assert not plan.do_merge


def test_the_store_only_report_still_names_the_frontier_carried_file(tmp_path, monkeypatch):
    carried = tmp_path / "verdicts-carried-abc.json"
    carried.write_text("{}")
    monkeypatch.setattr(ac, "frontier_carry_out", lambda: carried)
    plan = _plan(store_only=True)
    report, failures = _run_plumbing(plan, _chain_stdout(*_FULL_CHAIN[1:]))
    assert failures == []
    assert report.carry_out == carried


def test_frontier_carry_out_derives_the_stamp_aligned_frontier_from_disk(tmp_path, monkeypatch):
    """The summary's frontier name is derived the way its consumers derive it, never remembered in the plumbing green record — a later export with more effective verdicts outranks the file the last recorded pass happened to write."""
    review = tmp_path / "rebuild" / "out" / "review"
    review.mkdir(parents=True)
    (review / "manifest.json").write_text(json.dumps({"generated_at": "S1"}))

    def verdicts_file(path, stamp, units):
        path.write_text(
            json.dumps(
                {
                    "format": "ams-review-verdicts/1",
                    "manifest_generated_at": stamp,
                    "verdicts": [
                        {"unit": unit, "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}
                        for unit in units
                    ],
                }
            )
        )

    verdicts_file(tmp_path / "verdicts-old.json", "S0", ["u-1", "u-2", "u-3"])
    verdicts_file(tmp_path / "verdicts-carried-abc.json", "S1", ["u-1"])
    verdicts_file(tmp_path / "verdicts-export.json", "S1", ["u-1", "u-2"])
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", review)
    assert ac.frontier_carry_out() == tmp_path / "verdicts-export.json"
    (review / "manifest.json").write_text("not json")
    assert ac.frontier_carry_out() is None


def test_run_cycle_never_spawns_the_plumbing_when_skipped(monkeypatch, tmp_path):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the plumbing skip path must spawn nothing")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(ac, "_do_plumbing", must_not_run)
    monkeypatch.setattr(ac, "PLUMBING_GREEN", tmp_path / "plumbing-green.json")

    carried = tmp_path / "verdicts-carried-abc.json"
    carried.write_text("{}")
    monkeypatch.setattr(ac, "frontier_carry_out", lambda: carried)
    plan = _plan(
        skip_plumbing=True,
        plumbing_note=ac.PLUMBING_SKIP_NOTE,
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
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    green = tmp_path / "plumbing-green.json"
    monkeypatch.setattr(ac, "PLUMBING_GREEN", green)
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")

    plan = _plan(record_greens=True)
    rc = ac._run_cycle(
        plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()
    )
    assert rc == 0
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "plu"
    assert record["format"] == "ams-plumbing-green/1"

    green.unlink()

    def complaints_broken(report, *, spawn, emit, registry, plan):
        _plumbing_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        report.complaints_status = "FAILED (exit 2) — informational"
        report.complaints_ok = False
        return []

    monkeypatch.setattr(ac, "_do_plumbing", complaints_broken)
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
        return ["standing-merge failed"]

    monkeypatch.setattr(ac, "_do_plumbing", standing_merge_fails)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 1
    assert not green.exists()


def test_run_cycle_records_no_plumbing_green_until_the_chain_witnesses_its_fixpoint(monkeypatch, tmp_path):
    """The chain runs the echo pass again after the standing merge and says whether that second pass would have written anything. Only that witnessed standstill earns the green — a chain that stopped short of it leaves the next pass to close the cascade."""

    def unsettled(report, *, spawn, emit, registry, plan):
        _plumbing_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        report.plumbing_fixpoint = False
        return []

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")
    green = tmp_path / "plumbing-green.json"
    monkeypatch.setattr(ac, "PLUMBING_GREEN", green)

    monkeypatch.setattr(ac, "_do_plumbing", unsettled)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert not green.exists()

    monkeypatch.setattr(ac, "_do_plumbing", _plumbing_ok)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert green.exists()


def test_plumbing_settled_reads_the_chains_own_witness():
    report = ac.CycleReport()
    assert ac._plumbing_settled(report) is False
    report, _failures = _run_plumbing(_plan(), _chain_stdout(*_FULL_CHAIN, fixpoint=False))
    assert ac._plumbing_settled(report) is False
    report, _failures = _run_plumbing(_plan(), _chain_stdout(*_FULL_CHAIN))
    assert ac._plumbing_settled(report) is True


def _settled_repo(tmp_path, monkeypatch):
    """A repo whose run_m1 and surface build both auto-skip — the converged pass, the only shape the plumbing skip is offered on."""
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(ac.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "surface_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=None: "no-match")
    monkeypatch.setattr(ac, "PLUMBING_GREEN", tmp_path / "rebuild" / "out" / "plumbing-green.json")
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")


def test_main_skips_the_plumbing_on_a_matching_record(tmp_path, monkeypatch, capsys):
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})" in _step_lines(out, "plumbing")

    ac.record_plumbing_green("moved")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    row = _step_lines(out, "plumbing")
    assert "uv run python -m rebuild.tools.verdict_chain" in row
    assert "--merge-master" in row
    assert "SKIPPED (the surface did not move" in _step_lines(out, "snapshot")


def test_main_runs_the_census_on_the_pass_that_skips_the_plumbing(tmp_path, monkeypatch, capsys):
    """The census is never skipped: even the pass that skips the whole verdict chain refreshes the pins, because reading the sidecar and rewriting one small file costs nothing."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})" in _step_lines(out, "plumbing")
    assert "uv run python -m rebuild.review.census --update" in _step_lines(out, "census")


def test_main_never_skips_the_plumbing_on_a_pass_that_writes_the_surface(tmp_path, monkeypatch, capsys):
    """The skip rides the surface build's own skip: only then is the stamp the chain keys on known not to move mid-pass."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "PLUMBING_GREEN", tmp_path / "rebuild" / "out" / "plumbing-green.json")
    monkeypatch.setattr(ac, "plumbing_skip_fingerprint", lambda root=None, surface=None, master=None: "plu")
    ac.record_plumbing_green("plu")
    assert ac.main(["--dry-run"]) == 0
    assert ac.PLUMBING_SKIP_NOTE not in capsys.readouterr().out


def test_main_never_skips_the_plumbing_under_fresh_or_a_partial_chain(tmp_path, monkeypatch, capsys):
    """--carry-out and --snapshot-dir join the list because the skip writes neither file: honoring the flag and skipping the step cannot both happen, so the flag wins."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    for argv in (
        ["--dry-run", "--fresh"],
        ["--dry-run", "--no-merge"],
        ["--dry-run", "--no-carry"],
        ["--dry-run", "--review-out", str(tmp_path / "rehearse")],
        ["--dry-run", "--carry-out", str(tmp_path / "carried.json")],
        ["--dry-run", "--snapshot-dir", str(tmp_path / "snap")],
    ):
        assert ac.main(argv) == 0
        assert ac.PLUMBING_SKIP_NOTE not in capsys.readouterr().out


def test_main_skipping_the_plumbing_takes_the_snapshot_with_it(tmp_path, monkeypatch):
    """No carry reads the snapshot and no surface write threatens the live copy, so the pass takes none — and retention says so instead of naming a directory that was never made."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    calls: list[tuple] = []
    monkeypatch.setattr(ac, "snapshot_surface", lambda src, dst: calls.append((src, dst)) or "cloned")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert calls == []


def _assets_only_repo(tmp_path, monkeypatch):
    """A settled repo whose one moved input is the copied review UI assets: the byte-strict question answers no, the assets-exempt one answers yes, and that pair is the whole trigger for the refresh step."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ac, "surface_build_skippable", lambda root=None, review_out=None, ignore=(): bool(ignore)
    )


def test_main_refreshes_the_assets_when_only_the_static_component_moved(tmp_path, monkeypatch, capsys):
    """An app JS/CSS/HTML edit plans a copy and a restamp, never a whole surface build. Everything downstream inherits the skip: no snapshot, and — on a matching plumbing record — no chain either, since the manifest line the key hashes drops the component the refresh rewrites."""
    _assets_only_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED (only the review UI assets moved" in _step_lines(out, "surface-build")
    assert "uv run python -m rebuild.review.build refresh-assets" in _step_lines(out, "assets-refresh")
    assert "SKIPPED" in _step_lines(out, "snapshot")
    assert f"SKIPPED ({ac.PLUMBING_SKIP_NOTE})" in _step_lines(out, "plumbing")

    ac.record_plumbing_green("moved")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "uv run python -m rebuild.review.build refresh-assets" in _step_lines(out, "assets-refresh")
    assert "--merge-master" in _step_lines(out, "plumbing")
    assert "SKIPPED (the surface did not move" in _step_lines(out, "snapshot")


def test_main_plans_no_assets_refresh_when_the_surface_already_matches(tmp_path, monkeypatch, capsys):
    """The strict question is asked first, so a surface that would rebuild byte for byte has nothing copied over it — and --fresh takes the pass past both questions to a real build."""
    _settled_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "assets-refresh" not in out
    assert "the surface already reflects these inputs byte for byte" in _step_lines(out, "surface-build")

    monkeypatch.setattr(
        ac, "surface_build_skippable", lambda root=None, review_out=None, ignore=(): bool(ignore)
    )
    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    assert "assets-refresh" not in out
    assert "uv run python -m rebuild.review.build" in _step_lines(out, "surface-build")


def test_server_may_stay_up_only_when_the_pass_writes_neither_of_the_apps_files():
    """The predicate answers from the plan's writes, so a --no-carry pass and a --no-merge carry over an unmoved surface (skip_surface, no store merge) leave the server up, while any store-writing pass — store_only included — and any surface rewrite still take the port."""
    assert ac.server_may_stay_up(skip_surface=True, writes_store=False) is True
    assert ac.server_may_stay_up(skip_surface=True, writes_store=True) is False
    assert ac.server_may_stay_up(skip_surface=False, writes_store=False) is False
    assert ac.server_may_stay_up(skip_surface=False, writes_store=True) is False


def _preflight_args(**overrides):
    kw = dict(review_out=None, yes=False, stop_server=False)
    kw.update(overrides)
    return argparse.Namespace(**kw)


def test_preflight_leaves_a_listening_server_up_for_a_pass_that_writes_nothing_under_it(monkeypatch, capsys):
    """The gate pass: no surface write to strand the tab, no store write for merge_verdicts to refuse. Nothing to take the port for, so the letters stay on screen for the whole run — and this holds without --stop-server, since the flag is permission to stop a server, not an instruction to."""
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
    ac.record_plumbing_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: pytest.fail("stopped")
    )
    monkeypatch.setattr(ac, "snapshot_surface", lambda src, dst: "cloned")
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert ac.SERVER_STAYS_UP_NOTE in capsys.readouterr().out


def test_main_stops_the_server_when_the_pass_rebuilds_the_surface(tmp_path, monkeypatch, capsys):
    _unsettled_repo(tmp_path, monkeypatch)
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


def test_main_leaves_the_server_up_for_an_assets_refresh_pass(tmp_path, monkeypatch, capsys):
    """The refresh moves no shard and no stamp, so there is nothing under the app to take the port for: the letters stay on screen and livereload swaps the shell under them. A store write is still a store write, though, so the same pass with the plumbing record moved refuses without --stop-server."""
    _assets_only_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: pytest.fail("stopped")
    )
    monkeypatch.setattr(ac, "snapshot_surface", lambda src, dst: "cloned")
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert ac.SERVER_STAYS_UP_NOTE in capsys.readouterr().out

    ac.record_plumbing_green("moved")
    assert ac.main([]) == 2
    assert "REFUSING TO RUN" in capsys.readouterr().out


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


def test_build_plan_retention_off_on_rehearsal(tmp_path):
    plan = _plan(review_out=tmp_path / "reh")
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert "rehearsal" in by_name["retention"].note


def test_retention_never_runs_for_real_during_the_suite(real_run_retention):
    """The tripwire on the autouse stub. Retention resolves its targets from ac.ROOT at call time — no fixture redirects that — so a real run from inside the suite deletes the live repo's snapshots and carried exports, and compacts its verdict journal. Any test reaching a green finish with record_greens set would do it, and one did: a suite run deleted a live cycle's only snapshot between its build and its carry, stranding the pass's verdicts."""
    assert ac.run_retention is not real_run_retention
    assert ac.run_retention(_plan(record_greens=True)) == []


def test_the_gate_summaries_a_pass_clears_are_never_the_live_ones(tmp_path, live_deletion_targets):
    """The same tripwire for the other stages that delete before they rebuild. run_m1 unlinks its four summaries and gate:conform unlinks its own, all before spawning and all from constants resolved against the live rebuild/out/m1 — so a test that drives any of those stages without stubbing it empties the directory the surface build consumes and the cycle's auto-skip keys on, at the price of a full rebuild to get it back. None would fail: the missing summaries read as a failed gate, which is what most such tests are asserting anyway."""
    redirected = [
        *ac.M1_SUMMARY_FILES.values(),
        ac.CONFORM_SUMMARY,
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

    left_alone = real_run_retention(skipping)
    assert any("snapshots : left intact" in line for line in left_alone.lines)
    assert left_alone.figure.startswith("removed ") and "snapshots" in left_alone.figure
    assert survivor.is_dir()

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

    swept_up = real_run_retention(plan)
    out = "\n".join(swept_up.lines)

    assert compacted == [] and swept == []
    assert "journal   : left intact (the review server is up" in out
    assert "stashes   : left intact (the review server is up" in out
    assert swept_up.figure.endswith("stashes and journal left intact")
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


def _spawning_run_m1(report, *, spawn, emit, registry, **_):
    spawn("run_m1", ["uv", "run", "fake-m1"], emit=emit, registry=registry, stream=True)
    report.unmatched = 1
    report.multi_matched = 0
    report.pins_pass = True
    return _run_m1_green()


def _spawning_surface(report, *, spawn, emit, registry, review_out, **_):
    spawn("surface", ["uv", "run", "fake-surface"], emit=emit, registry=registry, stream=False)
    report.surface_units = 1
    return True


def _patch_timing_cycle(monkeypatch):
    monkeypatch.setattr(ac, "_do_run_m1", _spawning_run_m1)
    monkeypatch.setattr(ac, "_do_surface_build", _spawning_surface)
    monkeypatch.setattr(ac, "_do_plumbing", _plumbing_ok)
    monkeypatch.setattr(ac, "_do_census", _census_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_validators_task", _validators_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)


def test_green_cycle_journals_steps_then_one_run_line(monkeypatch, tmp_path):
    """job-costs is in the step list for the same reason the two stubbed stages are not: it spawns a child, so the wrapper times it and the journal carries a line naming it — which is how a summary claiming the check ran can be corroborated against a run that actually spawned it."""
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
    assert [entry["name"] for entry in steps] == ["run_m1", "surface", "job-costs"]
    assert len(runs) == 1
    assert entries[-1]["kind"] == "run"
    assert entries[-1]["exit"] == "ok"
    assert entries[-1]["interrupted"] is False
    assert {entry["run"] for entry in entries} == {entries[-1]["run"]}


def test_an_assets_refresh_journals_under_its_own_name(monkeypatch, tmp_path):
    """The refresh spawns a child where the surface build would have, so `wrap_spawn` times it — under its own step name rather than "surface-build", which keeps `calibrate_budgets`' sample of that step a sample of real builds."""
    _patch_timing_cycle(monkeypatch)

    journal_path = tmp_path / "timings.ndjson"
    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(skip_surface=True, refresh_assets=True, surface_note=ac.ASSETS_REFRESH_NOTE),
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
        timings=CycleTimings(journal_path),
    )

    assert rc == 0
    entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["name"] for entry in entries if entry["kind"] == "step"] == [
        "run_m1",
        "assets-refresh",
        "surface",
        "job-costs",
    ]
    assert report.assets_status.startswith("refreshed in place")


def test_a_failed_assets_refresh_stops_the_pass_before_the_lanes(monkeypatch, capsys):
    """A refresh that cannot land leaves a surface whose manifest may say one thing and whose shell says another, so the pass stops there and neither rebuild lane is claimed to have run."""
    _patch_timing_cycle(monkeypatch)

    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(skip_surface=True, refresh_assets=True, surface_note=ac.ASSETS_REFRESH_NOTE),
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name, rc=1 if name == "assets-refresh" else 0),
    )

    assert rc == 1
    assert report.assets_status.startswith("FAILED")
    assert report.gate_contracts == "not run (assets refresh failed)"
    assert report.gate_validators == "not run (assets refresh failed)"
    assert "assets refresh failed" in capsys.readouterr().out


def test_green_cycle_files_one_check_line_per_gate_it_judged(monkeypatch, tmp_path):
    """Every gate the cycle joins files a verdict under this run, including the two whose whole judgment is an exit code. The children that did the work file nothing: they inherit the run id and stand down, so a count of these lines is a count of checks rather than of processes with an opinion."""
    _patch_timing_cycle(monkeypatch)

    journal_path = tmp_path / "timings.ndjson"
    timings = CycleTimings(journal_path)
    rc = ac._run_cycle(
        _plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
        timings=timings,
    )

    assert rc == 0
    checks = ct.load_checks(journal_path)
    assert sorted(entry["check"] for entry in checks) == [
        "conform",
        "js",
        "make-test",
        "rebuild-contracts",
        "rebuild-validators",
    ]
    assert {entry["run"] for entry in checks} == {timings.run_id}
    assert {entry["verdict"] for entry in checks} == {"green"}
    assert all(entry["status"] == "green" for entry in checks)
    assert all("recordable" not in entry for entry in checks)


def test_a_red_lane_files_the_ids_it_failed_on(tmp_path):
    """The report the check line exists for. A lane's status already reaches the cycle summary; which test ids it failed on is recorded nowhere else, and that is what --by-outcome ranks."""
    timings = CycleTimings(tmp_path / "timings.ndjson")
    verdict = ac.classify_rebuild_output(
        "FAILED rebuild/test_settle.py::test_x\nERROR rebuild/test_boom.py::test_y",
        1,
        "rebuild-validators",
    )
    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_rebuild_lane(
            report, failures, pool.submit(lambda: verdict), "validators", ac._Emitter(), timings
        )

    (line,) = ct.load_checks(timings.path)
    assert line["check"] == "rebuild-validators"
    assert line["verdict"] == "red"
    assert line["status"] == "FAILED (2 unexplained)"
    assert line["failed_ids"] == ["rebuild/test_settle.py::test_x", "rebuild/test_boom.py::test_y"]
    assert line["run"] == timings.run_id
    assert report.gate_validators == "FAILED (2 unexplained)"


def test_a_lane_that_raised_files_no_check_line(tmp_path):
    """ "FAILED (exception)" describes the pool rather than the suite: nothing judged the lane, so nothing goes on the lane's record."""
    timings = CycleTimings(tmp_path / "timings.ndjson")

    def boom():
        raise RuntimeError("the pool blew up")

    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_rebuild_lane(report, failures, pool.submit(boom), "contracts", ac._Emitter(), timings)

    assert report.gate_contracts == "FAILED (exception)"
    assert ct.load_checks(timings.path) == []


def test_a_failing_make_test_files_its_exit_code_as_a_red_verdict(tmp_path):
    """make test has no judge of its own and never needed one — its exit code is honest for the font suite, unlike run_m1's — so the verdict is built at the join in the same two spellings the summary has always printed."""
    timings = CycleTimings(tmp_path / "timings.ndjson")
    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_gates(
            report,
            failures,
            None,
            None,
            None,
            None,
            pool.submit(lambda: _step("gate:make-test", 3)),
            ac._Emitter(),
            timings,
        )

    (line,) = ct.load_checks(timings.path)
    assert (line["check"], line["verdict"], line["status"]) == ("make-test", "red", "FAILED (exit 3)")
    assert line["failures"] == ["make test failed"]
    assert report.gate_make_test == "FAILED (exit 3)"
    assert failures == ["make test failed"]


def test_do_run_m1_files_a_check_line_on_the_skip_path(monkeypatch, tmp_path):
    """A skip is a judgment the cycle reached over this build's own summaries, not a check that never happened, so it belongs on run_m1's record beside the passes that did the work."""
    files = {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    monkeypatch.setattr(ac, "M1_SUMMARY_FILES", files)
    files["pipeline"].write_text(json.dumps({"defect_errors": []}))
    files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
    files["oracle"].write_text(json.dumps({"unmatched": 7, "multi_matched": 0}))
    timings = CycleTimings(tmp_path / "timings.ndjson")

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=lambda *a, **k: pytest.fail("skip path must not spawn"),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        skip=True,
        skip_note="test skip",
        timings=timings,
    )

    assert gate is not None and gate.ok
    (line,) = ct.load_checks(timings.path)
    assert (line["check"], line["verdict"], line["status"]) == ("run_m1", "green", "green")
    assert line["run"] == timings.run_id
    assert (report.unmatched, report.multi_matched) == (7, 0)


def test_do_run_m1_files_a_red_when_no_summaries_landed(monkeypatch, tmp_path):
    """A build that wrote no summaries never reached the judge, and the red recorded for it carries the same sentence the cycle's own failure list rolls up."""
    monkeypatch.setattr(
        ac, "M1_SUMMARY_FILES", {name: tmp_path / f"{name}.json" for name in ac.M1_SUMMARY_FILES}
    )
    timings = CycleTimings(tmp_path / "timings.ndjson")

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=lambda *a, **k: _step("run_m1", 1),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "fake-m1"],
        timings=timings,
    )

    assert gate is None
    (line,) = ct.load_checks(timings.path)
    assert (line["check"], line["verdict"], line["status"]) == ("run_m1", "red", "FAILED (no summaries)")
    assert line["failures"] == ac._run_m1_reasons(None)


def test_a_cycle_without_timings_still_judges_every_gate(monkeypatch):
    """The handle is optional everywhere it is threaded, so a caller that passes none — every test that drives the cycle for its console output — reaches the same verdicts with nothing to file them in."""
    _patch_timing_cycle(monkeypatch)
    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(), report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )
    assert rc == 0
    assert report.gate_make_test == "green"
    assert report.gate_conform == "green"


def test_main_hands_its_run_id_to_every_child_through_the_environment(tmp_path, monkeypatch):
    """The suppression the one-writer rule rests on: gate:make-test's wrapper and run_m1's CLI both judge a check of their own, and what tells them a cycle is already recording it is this variable in the environment they inherited. It is set on this process rather than added to a child's argv because it has to survive a Make recipe, which is also why the autouse redirect in rebuild/conftest.py takes it back off afterwards — a run id this test left behind would silence every check-recording test its worker picked up next."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "a-stale-run-id")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    seen = {}

    def fake_cycle(plan, report, emit, registry, **kw):
        seen["env"] = os.environ.get(ct.CYCLE_RUN_ENV)
        seen["run_id"] = kw["timings"].run_id
        return 0

    monkeypatch.setattr(ac, "_run_cycle", fake_cycle)
    assert ac.main([]) == 0
    assert seen["env"] == seen["run_id"]


def test_main_mints_one_run_directory_and_points_latest_at_it(tmp_path, monkeypatch):
    """A pass's logs are addressed two ways: by its own stamp and sha, which is what a summary or a later reader cites, and through `latest`, which is what an agent tails while the pass is still running."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    seen: dict[str, ac.Plan] = {}

    def fake_cycle(plan, report, emit, registry, **kw):
        seen["plan"] = plan
        return 0

    monkeypatch.setattr(ac, "_run_cycle", fake_cycle)
    assert ac.main([]) == 0

    plan = seen["plan"]
    assert plan.log_dir is not None
    assert plan.log_dir.parent == ac.BUILD_LOGS_ROOT
    assert plan.log_dir.name == f"{plan.stamp}-{plan.short_id}"
    assert (plan.log_dir / console.PLAN_TXT).exists()
    assert (plan.log_dir / console.TERMINAL_LOG).exists()
    assert (ac.BUILD_LOGS_ROOT / console.LATEST_LINK).resolve() == plan.log_dir.resolve()
    payload = ac.cycle_summary_payload(ac.CycleReport(), [], plan, "ok")
    assert payload["log_dir"] == str(plan.log_dir)


def test_main_copies_what_it_said_before_the_digest_into_the_terminal_log(tmp_path, monkeypatch, capsys):
    """Two of the pass's most consequential lines are printed before the plan is even resolved: which master the carry resolved to, and whether a red cycle's snapshot is being kept. terminal.log is meant to be a byte copy of the terminal, so they belong in it — once, on each side."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_plumbing_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    stranded = tmp_path / "tmp" / "review-pre-dead123"
    stranded.mkdir()
    monkeypatch.setattr(ac, "unfinished_cycle_snapshot", lambda summary_path=None: stranded)
    seen: dict[str, ac.Plan] = {}

    def fake_cycle(plan, report, emit, registry, **kw):
        seen["plan"] = plan
        return 0

    monkeypatch.setattr(ac, "_run_cycle", fake_cycle)
    assert ac.main([]) == 0

    out = capsys.readouterr().out
    log_dir = seen["plan"].log_dir
    assert log_dir is not None
    terminal = (log_dir / console.TERMINAL_LOG).read_text()
    kept = f"keeping its snapshot at {stranded} as well as this pass's."
    assert out.count(kept) == 1 and terminal.count(kept) == 1
    assert out.count("Auto-resolved carry source") == 1
    assert terminal.count("Auto-resolved carry source") == 1


def test_a_dry_run_mints_no_run_directory():
    """--dry-run resolves the plan and stops, so it prints the block with nothing to point at rather than creating a directory for a pass that never happens."""
    plan = _plan()
    assert plan.log_dir is None and plan.stamp == ""
    assert "logs " not in _plan_text(plan)
    assert "--dry-run: nothing executed" in _plan_text(plan)


def test_prune_build_logs_keeps_the_newest_runs_and_never_the_pointer(tmp_path):
    """The names are `<UTC stamp>-<short sha>`, so a lexical sort is chronological and no mtime is consulted. `latest` is a pointer rather than a run and is never a candidate — and what it points at, the newest run, is always kept."""
    for stamp in ("20260101T000000Z-aaa", "20260102T000000Z-bbb", "20260103T000000Z-ccc"):
        (tmp_path / stamp).mkdir()
    os.symlink("20260103T000000Z-ccc", tmp_path / console.LATEST_LINK, target_is_directory=True)

    assert ac.prune_build_logs(tmp_path, 5) == []
    removed = ac.prune_build_logs(tmp_path, 2)
    assert [path.name for path in removed] == ["20260101T000000Z-aaa"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "20260102T000000Z-bbb",
        "20260103T000000Z-ccc",
        console.LATEST_LINK,
    ]
    assert ac.prune_build_logs(tmp_path / "never-ran", 10) == []


def test_retention_prunes_the_build_logs_under_a_live_server_too(tmp_path, monkeypatch, real_run_retention):
    """The build logs sit beside the journal in the retention block but answer to nothing the app writes, so a listening review server — which parks the stash sweep and the compaction — leaves them prunable."""
    plan = _plan(skip_plumbing=True, plumbing_note=ac.PLUMBING_SKIP_NOTE)
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    monkeypatch.setattr(ac, "BUILD_LOGS_ROOT", tmp_path / "tmp" / "build-logs")
    (tmp_path / "tmp" / "build-logs").mkdir(parents=True)
    for index in range(ac.BUILD_LOGS_KEEP + 3):
        (tmp_path / "tmp" / "build-logs" / f"2026010{index // 9}T00000{index % 9}Z-abc").mkdir()
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)

    pruned = real_run_retention(plan)

    assert any(
        f"build logs: removed 3; kept the last {ac.BUILD_LOGS_KEEP} runs" in line for line in pruned.lines
    )
    assert "3 build logs" in pruned.figure
    assert len(list((tmp_path / "tmp" / "build-logs").iterdir())) == ac.BUILD_LOGS_KEEP


def test_failing_cycle_still_journals_a_run_line(monkeypatch, tmp_path):
    def failing_merge(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return ["verdict merge failed"]

    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_plumbing", failing_merge)

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
