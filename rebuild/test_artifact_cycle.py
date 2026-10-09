import argparse
import contextlib
import functools
import gzip
import hashlib
import itertools
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rebuild.conftest import is_live_artifact_path
from rebuild.review import journal, landing
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import calibrate_budgets as cb
from rebuild.tools import console
from rebuild.tools import cycle_paths
from rebuild.tools import cycle_timings as ct
from rebuild.tools import make_test_gate as mtg
from rebuild.tools.peak_rss import format_gb
from rebuild.tools.cycle_timings import CycleTimings

REPO_ROOT = Path(__file__).resolve().parents[1]
# Width assertions use stated machine sizes, not the host running the suite. With DEFAULT_MEMO_BYTES at 2.3 GB, five parked fold products at PARKED_FOLD_BYTES (1.6 GB) and DELTA_SLOT_BYTES at 4.7 GB, 38 GB is too small for the whole wave and fits four deltas alone and three beside an eight-core machine's pytest pool, and leaves room to test larger stated pool widths, and 44 GB is `_plan`'s default machine, which the plan and width tests share. Changing a kernel term changes these expectations and can require a different size to keep the memory set aside for the pool visible in a width.
MACHINE_44_GB = 44_000_000_000
MACHINE_38_GB = 38_000_000_000
MACHINE_36_GB = 36_000_000_000
# The fleet's RAM size (`doc/fleet.md`), for the fleet machines' width assertions. On both fleet machines the corpus build reaches its cap whether or not the pytest pool's bytes are subtracted, so no total separates the gated and solo cases; the arithmetic of the memory set aside for the pool is asserted through `_corpus_fit_terms`, which takes no total.
MACHINE_48_GIB = 51_539_607_552
# A stated machine smaller than any fleet machine, for the plan and width tests that are not about a fleet member's width; most pair it with ten cores.
MACHINE_32_GIB = 34_359_738_368


@pytest.fixture(autouse=True)
def _no_stated_widths(monkeypatch):
    """Clear the three environment variables that override derived widths, so a value a developer has exported cannot change these assertions. The tests that check the overrides set them themselves."""
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
    monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)


REAL_DO_CORPUS_SEED = ac._do_corpus_seed
REAL_DO_STORE_SNAPSHOT = ac._do_store_snapshot
REAL_DO_LAND = ac._do_land


def _seed_ok(report, *, emit, plan):
    report.seed_status = "cloned"
    return True


def _snapshot_ok(report, *, emit, plan):
    report.snapshot_status = "snapshot"
    return True


def _land_ok(report, *, spawn, emit, registry, plan):
    report.land = {"landed": True, "overlaid": 0, "records": 0, "new_stamp": None}
    report.land_status = "landed"
    return report.land


@pytest.fixture(autouse=True)
def _redirect_contracts_lane_reads(monkeypatch, tmp_path):
    """Make this module see the live review corpus and build artifacts as absent. The conftest's `_redirect_cycle_writes` redirects writes only, but the cycle also resolves its read paths from the live repo at call time, so a test driving `_run_cycle` over mocked stages would otherwise read whatever corpus and behavior-class sidecar sit in rebuild/out. Every test here is in the contracts lane, so no live read needs to be kept, and the lane's audit guard fails any read this misses.

    `REVIEW_OUT`, `AUTOSAVE` and the deep replay's green record are constants and are redirected directly. The three build-lane steps that need a real corpus or store beside them, the corpus seed, the store snapshot and the land, are stubbed as the other build-lane stages are in each test; a test of one of them sets the real function back (`REAL_DO_CORPUS_SEED`, `REAL_DO_STORE_SNAPSHOT`, `REAL_DO_LAND`). The review server's /capabilities answers `NO_CAPABILITIES` (a 404), as a server from before the land protocol does, so no test probes the live port, and starting a server fails the test; a test that checks the probe patches `capabilities` itself. The behavior-class sidecar is not: `deep_sweep_skip_lines` re-roots `BEHAVIOR_CLASSES` against the root it is given, so redirecting the constant outside ROOT would break the tests that pass their own root, and the function is patched for the default root only. The gates' `*_skip_lines(ROOT)` also read through two globs over rebuild/out (`baselines_value`, `_subset_tables`) and the per-file digest `_sha256_path`, which returns "absent" for a live path, and the deep replay's status reads the tables' heads through `tables_imports_digest`, which reads as no tables for the live root. Each patch changes the result only for the live root or a live path, so a test that passes its own root runs the real function.
    """
    from rebuild.pipeline import fingerprint

    real_sweep_lines = ac.deep_sweep_skip_lines
    real_baselines = fingerprint.baselines_value
    real_subsets = ac._subset_tables
    real_sha = ac._sha256_path
    real_contracts_closure = ac.contracts_closure
    monkeypatch.setattr(
        ac,
        "contracts_closure",
        lambda root: (
            ("contracts-fixture", {"fixture": "contracts-fixture"})
            if root == ac.ROOT
            else real_contracts_closure(root)
        ),
    )
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "verdicts-autosave.json")
    monkeypatch.setattr(ac, "capabilities", lambda port=ac.REVIEW_PORT: ac.NO_CAPABILITIES)
    monkeypatch.setattr(ac, "start_review_server", lambda: pytest.fail("started a review server"))
    monkeypatch.setattr(ac, "_do_corpus_seed", _seed_ok)
    monkeypatch.setattr(ac, "_do_store_snapshot", _snapshot_ok)
    monkeypatch.setattr(ac, "_do_land", _land_ok)
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "deep-replay-green.json")
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
    real_imports = ac.tables_imports_digest
    monkeypatch.setattr(
        ac,
        "tables_imports_digest",
        lambda root=None: None if Path(root or REPO_ROOT).resolve() == REPO_ROOT else real_imports(root),
    )


def _plan_text(plan: ac.Plan) -> str:
    """Return the rendered plan block as one string."""
    return "\n".join(ac.render_plan(plan))


_PLAN_ROW = r"^\s+(?:run\?|run|skip)\s+"


def _step_lines(text: str, name: str) -> str:
    """Return one step's row from a plan block, followed by its `$ argv` line when it has one, or an empty string when the step is not in the block."""
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


def _write_pass_summaries(files):
    for name, summary in _pass_summaries().items():
        files[name].write_text(json.dumps(summary))


def test_gate_passes_on_clean_summaries():
    s = _pass_summaries()
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert result.check == "run_m1"
    assert result.outcome == "green"
    assert result.status == "green"
    assert result.ok
    assert result.failures == []
    assert result.failed_ids == []


def test_gate_fails_on_defect_errors():
    s = _pass_summaries()
    s["pipeline"]["defect_errors"] = ["E-ANCHOR convention:foo: bad"]
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not result.ok
    assert result.outcome == "red"
    assert result.status == "FAILED"
    assert any("defect" in reason for reason in result.failures)


def test_gate_fails_on_a_manual_pin_gate_with_nothing_in_scope():
    """A Manual-pin summary with nothing in scope has `pass` true, because `pass` is `not disagreements`. The gate uses run_m1's `manual_pin_gate_failure`, which also checks the scope, so the gate fails."""
    s = _pass_summaries()
    s["manual_pins"] = {"pass": True, "disagreements": [], "pins_in_scope": 0, "replayed": 0}
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not result.ok
    assert any("no pins in scope" in reason for reason in result.failures)


def test_gate_fails_on_manual_pins():
    s = _pass_summaries()
    s["manual_pins"] = {"pass": False, "disagreements": ["one", "two"]}
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not result.ok
    assert any("Manual-pin" in reason for reason in result.failures)


def test_gate_fails_on_multi_matched():
    s = _pass_summaries()
    s["oracle"] = {"unmatched": 8423, "multi_matched": 2}
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not result.ok
    assert any("multi_matched" in reason for reason in result.failures)


def test_gate_unmatched_alone_is_not_a_failure():
    s = _pass_summaries()
    s["oracle"] = {"unmatched": 999999, "multi_matched": 0}
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert result.ok


def test_the_gate_carries_no_oracle_counts():
    """The result carries the outcome only. Callers read the oracle counts from the oracle summary they already hold."""
    s = _pass_summaries()
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"], s["oracle"])
    assert not hasattr(result, "unmatched")
    assert not hasattr(result, "multi_matched")


def test_conform_gate_passes_on_clean_summary():
    result = ac.evaluate_conform_gate({"divergences": 0, "pass": True})
    assert result.check == "conform"
    assert result.outcome == "green"
    assert result.status == "green"
    assert result.failures == []


def test_conform_gate_fails_on_divergences():
    result = ac.evaluate_conform_gate({"divergences": 3, "pass": False})
    assert result.outcome == "red"
    assert result.status == "FAILED"
    assert result.failures == ["conform gate: 3 font-vs-settle divergence(s)"]


def test_conform_gate_fails_on_missing_summary():
    result = ac.evaluate_conform_gate(None)
    assert result.outcome == "red"
    assert result.status == "FAILED (no conform_summary.json)"
    assert result.failures == ["conform gate: run_m1 --conform-only wrote no summary"]


def test_conform_gate_names_no_failed_ids():
    """A divergence names a window, not a test, so the result lists no failed ids. The audit written beside the summary lists the windows."""
    assert ac.evaluate_conform_gate({"divergences": 3, "pass": False}).failed_ids == []
    assert ac.evaluate_conform_gate(None).failed_ids == []


def test_conform_gate_fails_on_bare_false_pass():
    result = ac.evaluate_conform_gate({"pass": False})
    assert result.status == "FAILED"
    assert result.failures == ["conform gate: pass is false"]


def test_classify_review_module_failures_are_hard():
    """A failure in a review module is a hard failure like any other. The review-facts pins are the cycle's output, not an assertion the suite reads."""
    stdout = "\n".join(
        [
            "FAILED rebuild/test_review_build.py::test_totals",
            "FAILED rebuild/test_settle.py::test_x",
            "ERROR rebuild/test_review_ink.py::test_y",
        ]
    )
    result = ac.classify_rebuild_output(stdout, 1, "rebuild-contracts")
    assert result.check == "rebuild-contracts"
    assert result.outcome == "red"
    assert result.status == "FAILED (3 unexplained)"
    assert result.failed_ids == [
        "rebuild/test_review_build.py::test_totals",
        "rebuild/test_settle.py::test_x",
        "rebuild/test_review_ink.py::test_y",
    ]
    assert not result.recordable


def test_classify_rebuild_output_is_lane_blind():
    """The check name is copied into the result to name the suite. The classification ignores it, so the same output gets the same result under any name."""
    stdout = "FAILED rebuild/test_settle.py::test_x"
    contracts = ac.classify_rebuild_output(stdout, 1, "rebuild-contracts")
    other = ac.classify_rebuild_output(stdout, 1, "rebuild-other")
    assert contracts.check == "rebuild-contracts"
    assert other.check == "rebuild-other"
    assert (contracts.status, contracts.failures, contracts.failed_ids) == (
        other.status,
        other.failures,
        other.failed_ids,
    )


def test_dry_run_plan_default():
    plan = ac.build_plan(
        verdicts=Path("verdicts-X.json"),
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=1,
        total_bytes=MACHINE_44_GB,
    )
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
        "--overlap-memo-writes" if plan.overlap_memo_writes else "--no-overlap-memo-writes",
        "--scratch-beside-default",
        str(plan.scratch_beside_default),
        "--replay-threads",
        str(plan.replay_threads),
    ]
    assert by_name["corpus-build"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.review.build",
        "--out",
        str(ac.next_corpus_dir()),
        "--jobs",
        str(plan.corpus_jobs),
        "--signature-jobs",
        str(plan.signature_jobs),
    ]
    assert _argv(by_name["verdict-update"])[:7] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.verdict_update",
        "--corpus",
        str(ac.next_corpus_dir()),
    ]
    assert [step.name for step in plan.steps][:6] == [
        "run_m1",
        "corpus-seed",
        "corpus-build",
        "store-snapshot",
        "verdict-update",
        "land",
    ]
    assert by_name["review-facts"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.review.facts",
        "--update",
        "--corpus",
        str(ac.REVIEW_OUT),
    ]
    contracts_record = ac.contracts_green()
    assert by_name["gate:rebuild-contracts"].argv == [
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
        "--durations=25",
        "--closure-skip",
        str(contracts_record.with_name("rebuild-contracts-selection.json")),
        "--closure-record",
        str(contracts_record.with_name("rebuild-contracts-closures.json")),
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
    """gate:conform gets the conform sweep's own width. Beside a corpus build on twelve cores, the cores the build leaves, less gate:make-test's pool under the default overlap policy, fall below the acceptance configurations, so the width is that count, and a machine with fewer cores than configurations runs one unit per core. The argv states it at every value, including one, because an omitted `--jobs` would give run_m1 its own default instead of the width this plan budgeted beside the corpus build."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS

    plan = _plan(ncores=12)
    by_name = {step.name: step for step in plan.steps}
    assert (
        plan.conform_jobs
        == ac.conform_job_budget(
            skip_gates=plan.skip_gates,
            skip_make_test=plan.skip_make_test,
            skip_corpus=plan.skip_corpus,
            verdict_update_runs=plan.runs("verdict-update"),
            pool_policy=plan.pool_policy,
            ncores=12,
            total_bytes=MACHINE_44_GB,
        )
        == len(ACCEPTANCE_CONFIGS)
    )
    assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", str(len(ACCEPTANCE_CONFIGS))]

    small = _plan(ncores=4)
    small_by_name = {step.name: step for step in small.steps}
    assert _argv(small_by_name["gate:conform"])[-2:] == ["--jobs", "4"]

    single = _plan(ncores=1)
    single_by_name = {step.name: step for step in single.steps}
    assert _argv(single_by_name["gate:conform"])[-2:] == ["--jobs", "1"]


def test_dry_run_plan_states_a_corpus_width_of_one_in_the_argv():
    plan = _plan(ncores=2)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["corpus-build"])[-4:-2] == ["--jobs", "1"]
    assert plan.corpus_jobs == 1


def test_dry_run_plan_conform_max_length():
    plan = _plan(conform_max_length=3)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["gate:conform"])[-2:] == ["--conform-max-length", "3"]
    assert plan.conform_max_length == 3

    default = _plan()
    default_by_name = {step.name: step for step in default.steps}
    assert "--conform-max-length" not in _argv(default_by_name["gate:conform"])
    assert default.conform_max_length == ac.CONFORM_MAX_LENGTH_DEFAULT


def test_dry_run_plan_skip_conform():
    plan = _plan(skip_conform=True)
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert by_name["gate:conform"].note == "SKIPPED (--skip-conform)"
    assert by_name["gate:rebuild-contracts"].argv is not None


def test_dry_run_plan_runs_the_whole_verdict_update_as_one_step():
    """The verdict update is one step between the store snapshot and the land. It reads the corpus built beside the served one, carries the named master and the snapshot of the store, so the store's own verdicts are carried beside another master, and merges and fills into the prepared copy of the store in the pass's scratch directory, journaling there, so it never writes the live store."""
    ac.AUTOSAVE.write_text("{}")
    plan = _plan(short_id="abc1234")
    names = [step.name for step in plan.steps]
    assert names.index("store-snapshot") == names.index("corpus-build") + 1
    assert names.index("verdict-update") == names.index("store-snapshot") + 1
    assert names.index("land") == names.index("verdict-update") + 1
    assert names.index("review-facts") == names.index("land") + 1
    argv = {step.name: step for step in plan.steps}["verdict-update"].argv
    assert argv is not None
    assert plan.scratch_dir is not None
    snapshot = plan.scratch_dir / ac.landing.SNAPSHOT_NAME
    assert argv[:14] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.verdict_update",
        "--corpus",
        str(ac.next_corpus_dir()),
        "--verdicts",
        "v.json",
        "--verdicts",
        str(snapshot),
        "--carry-out",
        str(ac.ROOT / "verdicts-carried-abc1234.json"),
        "--autosave",
    ]
    assert argv[14] == str(plan.scratch_dir / ac.landing.PREPARED_NAME)
    assert argv[15:17] == ["--journal", str(plan.scratch_dir / "journal.ndjson")]
    assert "--no-merge" not in argv
    assert plan.do_merge is True


def test_with_no_live_store_the_carry_reads_only_the_master():
    """With no verdicts-autosave.json yet (a fresh checkout or worktree whose tabs have saved nothing), the store snapshot writes no file, so the carry is not handed one: it reads the master alone, and a store a tab creates during the pass reaches the landed store through the land's overlay instead."""
    assert not ac.AUTOSAVE.exists()
    plan = _plan()
    assert plan.scratch_dir is not None
    argv = _argv({step.name: step for step in plan.steps}["verdict-update"])
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "--verdicts"] == ["v.json"]
    assert "--no-complaints" in argv


def test_a_master_that_is_the_live_store_is_carried_from_its_snapshot():
    """When the master resolved to the live store, the carry reads the snapshot in its place and not the store beside it, and a direct merge merges the snapshot; the live store is never read or written by the verdict update."""
    plan = _plan(verdicts=ac.AUTOSAVE)
    assert plan.scratch_dir is not None
    snapshot = str(plan.scratch_dir / ac.landing.SNAPSHOT_NAME)
    argv = _argv({step.name: step for step in plan.steps}["verdict-update"])
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "--verdicts"] == [snapshot]
    assert str(ac.AUTOSAVE) not in argv

    direct = _plan(verdicts=ac.AUTOSAVE, skip_corpus=True, direct_merge=True, corpus_note="same")
    assert direct.scratch_dir is not None
    argv = _argv({step.name: step for step in direct.steps}["verdict-update"])
    assert argv[argv.index("--merge-master") + 1] == str(direct.scratch_dir / ac.landing.SNAPSHOT_NAME)
    assert argv[argv.index("--corpus") + 1] == str(ac.REVIEW_OUT)


def test_dry_run_plan_no_merge_carries_and_stops():
    plan = _plan(no_merge=True)
    step = {step.name: step for step in plan.steps}["verdict-update"]
    assert step.argv is not None
    assert "--no-merge" in step.argv
    assert "--verdicts" in step.argv
    assert "--no-merge" in step.note or "carry only" in step.note
    assert plan.do_merge is False


def test_dry_run_plan_staging_pass_never_touches_the_autosave(tmp_path):
    plan = _plan(review_out=tmp_path / "staged")
    step = {step.name: step for step in plan.steps}["verdict-update"]
    assert step.argv is not None
    assert "--no-merge" in step.argv
    assert "--no-complaints" in step.argv
    assert step.argv[step.argv.index("--corpus") + 1] == str(tmp_path / "staged")
    assert "staging" in step.note
    assert plan.do_merge is False


def test_dry_run_plan_complaints_runs_inside_the_verdict_update(tmp_path, monkeypatch):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()
    names = [step.name for step in plan.steps]
    assert "complaints" not in names
    step = {step.name: step for step in plan.steps}["verdict-update"]
    assert step.argv is not None
    assert "--no-complaints" not in step.argv
    assert "complaint list" in step.note
    assert plan.complaints_note == ""


def test_the_verdict_update_is_told_to_skip_the_complaint_list_on_a_staging_pass_first_run_and_a_missing_store(
    tmp_path, monkeypatch
):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    staging = _plan(review_out=tmp_path / "staged")
    step = {step.name: step for step in staging.steps}["verdict-update"]
    assert step.argv is not None and "--no-complaints" in step.argv
    assert "staging" in staging.complaints_note

    first = _plan(first_run=True, verdicts=None)
    by_name = {step.name: step for step in first.steps}
    assert by_name["verdict-update"].argv is None
    assert "first run" in by_name["verdict-update"].note
    assert "first run" in first.complaints_note

    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "missing.json")
    absent = _plan()
    step = {step.name: step for step in absent.steps}["verdict-update"]
    assert step.argv is not None and "--no-complaints" in step.argv
    assert "no verdicts store" in absent.complaints_note


def test_the_complaint_list_headline_is_scraped_and_never_fails_the_cycle(tmp_path, monkeypatch):
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()

    report, failures = _run_verdict_update(
        plan,
        _verdict_update_stdout(
            (
                "complaints",
                [
                    "wrote /x/tmp/complaints-data.json: 3 open complaints (1 new / 2 older) in 2 "
                    "groups — 5 defer candidates, 4 approved units a fix would likely change"
                ],
            )
        ),
    )
    assert failures == []
    assert report.complaints_status.startswith("3 open complaints")
    assert report.complaints_ok is True

    report, failures = _run_verdict_update(
        plan, _verdict_update_stdout(("complaints", ["no open complaints"]))
    )
    assert report.complaints_status == "no open complaints"
    assert report.complaints_ok is True

    report, failures = _run_verdict_update(
        plan, _verdict_update_stdout(("complaints", ["boom"]), failed="complaints"), returncode=2
    )
    assert report.complaints_status == "FAILED (exit 2) — informational"
    assert report.complaints_ok is False
    assert failures == []


def test_the_verdict_update_row_counts_the_carry_and_the_summary_quotes_what_the_fills_wrote(
    tmp_path, monkeypatch
):
    """The verdict update runs as one child, so its steps reach this process only through the lines they print. The carry count and the human queue before and after become the row's detail. The other lines are quoted in the summary under the line they belong to, instead of appearing only in `cycle_summary.json` and the step's log."""
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan()

    report, failures = _run_verdict_update(
        plan,
        _verdict_update_stdout(
            (
                "carry",
                [
                    "wrote verdicts-carried-testid.json: 15903 carried onto manifest 2026-09-04T12:00:00Z",
                    "kinds: {'ok': 15903}",
                    "human queue: 81 -> 12 still needing fresh verdicts",
                ],
            ),
            ("merge", ["merged 15903 verdicts into verdicts-autosave.json"]),
            ("duplicate-fill", ["wrote tmp/duplicate-fill.json: 4 duplicate-fill verdicts"]),
            ("duplicate-merge", ["nothing changed: the autosave already holds all 4 verdicts"]),
            ("standing-fill", ["wrote tmp/standing.json: 7 standing-approval verdicts"]),
            ("standing-merge", ["merged 7 verdicts into verdicts-autosave.json"]),
            ("complaints", ["wrote /x/tmp/complaints-data.json: 3 open complaints in 2 groups"]),
        ),
    )
    assert failures == []
    assert (
        ac.step_detail(report, "verdict-update")
        == "15,903 carried, queue 81 -> 12; 3 open complaints in 2 groups"
    )

    block = ac.summary_cycle_lines(report, plan, [])
    assert "      human queue: 81 -> 12 still needing fresh verdicts" in block
    assert "      wrote tmp/standing.json: 7 standing-approval verdicts" in block
    assert "      wrote tmp/duplicate-fill.json: 4 duplicate-fill verdicts" in block
    assert "      merged 15903 verdicts into verdicts-autosave.json" in block
    carry = block.index("  carry output     : " + str(report.carry_out))
    verdict_update = next(index for index, line in enumerate(block) if line.startswith("  verdict update"))
    assert carry < block.index("      human queue: 81 -> 12 still needing fresh verdicts") < verdict_update


def test_the_verdict_update_row_falls_back_to_the_merge_when_no_carry_ran(tmp_path, monkeypatch):
    """A direct merge runs no carry, because the corpus did not change, so there is no carry count. The row reports the merge instead of staying blank."""
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text("{}")
    monkeypatch.setattr(ac, "AUTOSAVE", autosave)
    plan = _plan(direct_merge=True)
    report, failures = _run_verdict_update(
        plan,
        _verdict_update_stdout(
            ("merge", ["merged 3 verdicts into verdicts-autosave.json"]),
            ("complaints", ["no open complaints"]),
        ),
    )
    assert failures == []
    assert ac.carry_detail(report.carry_lines) == ""
    assert report.carry_counts is None
    assert ac.cycle_summary_payload(report, [], plan, "ok")["carry"] is None
    assert ac.step_detail(report, "verdict-update") == "merge merged; no open complaints"


def test_dry_run_plan_skips_the_verdict_update_without_a_carry():
    no_carry = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc",
    )
    step = {step.name: step for step in no_carry.steps}["verdict-update"]
    assert step.argv is None
    assert step.note == "SKIPPED (--no-carry)"
    first = ac.build_plan(
        verdicts=None,
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=True,
        short_id="abc",
    )
    step = {step.name: step for step in first.steps}["verdict-update"]
    assert step.argv is None
    assert step.note == "SKIPPED (first run)"


def test_dry_run_plan_no_carry():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="def5678",
    )
    assert plan.carry_out is None
    by_name = {step.name: step for step in plan.steps}
    assert by_name["verdict-update"].argv is None


def test_dry_run_plan_first_run_skips_the_carry():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=True,
        short_id="0000000",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["verdict-update"].argv is None
    assert plan.carry_out is None


def test_dry_run_plan_skip_gates():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        skip_gates=True,
        first_run=False,
        short_id="abc",
    )
    names = {step.name for step in plan.steps}
    assert "gate:js" not in names
    assert "gate:rebuild-contracts" not in names
    assert "gate:conform" not in names


def test_render_plan_is_stringable():
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
    )
    text = _plan_text(plan)
    assert "rebuild.pipeline.run_m1" in text


def test_every_plan_step_says_what_it_is_for():
    """The banner prints every step's description, the land's steps included (the corpus seed, the store snapshot and the land). The gates-only rerun is the one row whose description is not looked up under its own name: it spawns as run_m1:gates-only, reports under run_m1's row, and must describe the gates-only rerun, not the build."""
    for plan in (
        _plan(),
        _plan(skip_gates=True),
        _plan(rerun_gates_only=True, run_m1_note="comparison-side"),
        _plan(
            skip_corpus=True,
            promote_corpus=Path("var/staged-review"),
            corpus_note=ac.CORPUS_PROMOTE_NOTE,
        ),
        _plan(skip_corpus=True, direct_merge=True, corpus_note="same"),
        _plan(no_carry=True),
    ):
        for step in plan.steps:
            assert step.describe, step.name
            assert step.describe in ac.STEP_DESCRIPTIONS.values(), step.name
    rerun = _plan(rerun_gates_only=True, run_m1_note="comparison-side")
    assert rerun.describe("run_m1") == ac.STEP_DESCRIPTIONS[ac.RUN_M1_GATES_ONLY_STEP]
    assert "rebuilding nothing" in rerun.describe("run_m1")
    assert _plan().describe("run_m1") == ac.STEP_DESCRIPTIONS["run_m1"]


def test_a_step_that_spawns_nothing_is_not_automatically_a_skipped_one():
    """The run/skip column reads `skipped`, not `argv is None`, because the retention step does its work in this process without spawning a child, and the `gates` placeholder that replaces the `gate:` steps under --skip-gates is marked skipped explicitly. Reading the column from argv would miscount the counts line."""
    plan = _plan()
    by_name = {step.name: step for step in plan.steps}
    assert by_name["retention"].argv is None
    assert by_name["retention"].skipped is False
    for step in plan.steps:
        if step.argv is not None:
            assert step.skipped is False, step.name
    gates_off = {step.name: step for step in _plan(skip_gates=True).steps}
    assert gates_off["gates"].skipped is True


def test_the_plan_block_counts_its_steps_and_leaves_the_sweep_undecided():
    """gate:conform is the one row the plan cannot decide. Its skip key covers the artifacts run_m1 writes, so a pass that plans the sweep may skip it once the build finishes, and the counts line shows a range. A pass that skips run_m1 shows the sweep as certain, because nothing is rebuilt and `main` has already compared the key and found no green record for it. A --fresh pass also shows it as certain."""
    plan = _plan()
    rows = ac.plan_rows(plan)
    by_name = {row.name: row for row in rows}
    assert by_name["gate:conform"].status == console.STATUS_MAYBE
    assert by_name["gate:rebuild-contracts"].status == console.STATUS_RUN
    assert by_name["run_m1"].status == console.STATUS_RUN
    assert by_name["gate:conform"].note == ac.CONFORM_MAYBE_NOTE
    text = _plan_text(plan)
    assert ac.CONFORM_MAYBE_NOTE in _step_lines(text, "gate:conform")
    assert "uv run pytest" in _step_lines(text, "gate:rebuild-contracts")
    assert console.counts_line(rows) == f"{len(rows)} steps: {len(rows) - 1}–{len(rows)} will run, 0 skipped"

    rerun = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    rerun_by_name = {row.name: row for row in ac.plan_rows(rerun)}
    assert rerun_by_name["gate:conform"].status == console.STATUS_MAYBE

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
    assert certain_by_name["gate:rebuild-contracts"].note == "submitted beside the corpus build"
    assert "–" not in console.counts_line(certain_rows)

    fresh = _plan(fresh=True)
    fresh_rows = ac.plan_rows(fresh)
    fresh_by_name = {row.name: row for row in fresh_rows}
    assert fresh_by_name["gate:conform"].status == console.STATUS_RUN
    assert fresh_by_name["gate:conform"].note == ""
    assert fresh_by_name["gate:rebuild-contracts"].status == console.STATUS_RUN
    assert "–" not in console.counts_line(fresh_rows)
    verdict_update = {step.name: step for step in fresh.steps}["verdict-update"].argv
    assert verdict_update is not None and "--fresh-standing-memo" in verdict_update
    settled_verdict_update = {step.name: step for step in settled.steps}["verdict-update"].argv
    assert settled_verdict_update is None or "--fresh-standing-memo" not in settled_verdict_update


def test_the_plan_block_leads_with_its_arithmetic_and_puts_the_paths_after_the_rows():
    """The header goes straight to the step count and the rows. The paths this pass resolved (the master the carry reads, where the carried file goes) follow the rows, and the concurrency block comes last."""
    plan = _plan()
    lines = ac.render_plan(plan)
    assert lines[0].startswith("artifact cycle ")
    counts = lines.index(console.counts_line(ac.plan_rows(plan)))
    last_row = max(index for index, line in enumerate(lines) if re.match(_PLAN_ROW, line))
    paths = next(index for index, line in enumerate(lines) if line.startswith("  first run "))
    concurrency = next(index for index, line in enumerate(lines) if line.strip().startswith("Concurrency"))
    assert 0 < counts < last_row < paths < concurrency
    assert [line for line in lines if line.startswith("  carry output ")]


def _built_corpus(tmp_path, **totals):
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"totals": totals}))
    return corpus


def test_do_corpus_build_takes_its_totals_from_the_manifest_the_build_wrote(tmp_path):
    corpus = _built_corpus(tmp_path, units=15897, rows=81867, batches=16, duplicate_groups=402)
    report = ac.CycleReport()
    ok = ac._do_corpus_build(
        report,
        spawn=lambda name, argv, **k: _step(name, 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=corpus,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    assert ok
    assert (report.corpus_units, report.corpus_rows, report.corpus_batches, report.duplicate_groups) == (
        15897,
        81867,
        16,
        402,
    )


def test_do_corpus_build_fails_when_a_clean_build_left_no_manifest(tmp_path, capsys):
    corpus = tmp_path / "review"
    corpus.mkdir()
    report = ac.CycleReport()
    ok = ac._do_corpus_build(
        report,
        spawn=lambda name, argv, **k: _step(name, 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=corpus,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    assert not ok
    assert "review.build exited 0 but left no readable manifest.json" in capsys.readouterr().out
    assert report.corpus_units is None


def test_do_corpus_build_reads_no_totals_from_a_failed_build(tmp_path, capsys):
    """After a nonzero exit, the manifest in the corpus directory is the previous pass's, so the step fails before reading any totals from it."""
    corpus = _built_corpus(tmp_path, units=1, rows=2, batches=3, duplicate_groups=4)
    report = ac.CycleReport()
    ok = ac._do_corpus_build(
        report,
        spawn=lambda name, argv, **k: _step(name, 3),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=corpus,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    assert not ok
    assert "review.build exited 3" in capsys.readouterr().out
    assert (report.corpus_units, report.corpus_rows, report.corpus_batches, report.duplicate_groups) == (
        None,
        None,
        None,
        None,
    )


def _argv(step: ac.Step) -> list[str]:
    assert step.argv is not None
    return step.argv


def _plan(**overrides: Any) -> ac.Plan:
    """Return a resolved plan for a stated machine: `ncores` sets every core-derived width and `total_bytes` every memory-derived one, so the plan is the same on any host. Any keyword can be overridden per test."""
    kw: dict[str, Any] = dict(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="testid",
        ncores=4,
        total_bytes=MACHINE_44_GB,
    )
    kw.update(overrides)
    return ac.build_plan(**kw)


def _step(name="x", rc=0, stdout="", stderr=""):
    return ac._StepResult(name, rc, stdout, stderr, 0.0)


def _run_m1_green():
    """Return the result `_do_run_m1` returns for a passing build. A stubbed stage returns this and sets the oracle counts on the report itself, as the real stage does."""
    return ct.CheckResult(check="run_m1", outcome="green", status="green", failures=[], failed_ids=[])


def _run_m1_red(*failures):
    return ct.CheckResult(
        check="run_m1", outcome="red", status="FAILED", failures=list(failures), failed_ids=[]
    )


def _lane_result(check, status="green", failed_ids=()):
    return ct.CheckResult(
        check=check,
        outcome="red" if failed_ids else "green",
        status=status,
        failures=[f"rebuild suite: {len(failed_ids)} unexplained failure(s)"] if failed_ids else [],
        failed_ids=list(failed_ids),
        recordable=not failed_ids,
    )


def _conform_result(status="green", failures=()):
    return ct.CheckResult(
        check="conform",
        outcome="red" if failures else "green",
        status=status,
        failures=list(failures),
        failed_ids=[],
    )


def _pass_run_m1(report, *, spawn, emit, registry, **_):
    report.unmatched = 1
    report.multi_matched = 0
    report.pins_pass = True
    return _run_m1_green()


def _corpus_ok(report, *, spawn, emit, registry, review_out, **_):
    report.corpus_units = 1
    return True


def _verdict_update_stdout(*sections, complete=True, failed=None):
    """Return a synthetic verdict_update stdout: for each step, the `[phase] <step>` line, the step's own lines and the closing `[t] <step>` line, then the completion or failure line. Those last two keep the verdict update's `[verdict-update] ` prefix, which `verdict_update_sections` uses to keep result lines out of the complaints section."""
    lines = []
    for name, body in sections:
        lines.append(console.PHASE + name)
        lines.extend(body)
        lines.append(f"[t] {name} 0.1s")
        if failed == name:
            lines.append(f"{console.FAILED_LINE}{name} (exit 1)")
            break
    if failed is None and complete:
        lines.append(f"{console.COMPLETE_LINE}duplicate, standing, duplicate")
    return "\n".join(lines) + "\n"


_FULL_VERDICT_UPDATE = (
    (
        "carry",
        [
            "wrote verdicts-carried-abc.json: 51946 carried onto manifest S1",
            "kinds: {'approve': 5}",
            "carry counts: human=60000 matched=51946 unmatched=8054 orphaned=12",
        ],
    ),
    (
        "merge",
        [
            "verdicts-carried-abc.json: 5 added, 0 replaced, 2 kept newer",
            "merged 1 file(s) into verdicts-autosave.json: 5 added, 0 replaced, 2 kept newer; "
            "store holds 7 verdicts (7 effective) on manifest S1",
        ],
    ),
    (
        "duplicate-fill",
        [
            "wrote verdicts-duplicate-fill.json: 37 duplicate-fill verdicts onto manifest S1",
            "no duplicate group holds disagreeing verdicts",
        ],
    ),
    (
        "duplicate-merge",
        [
            "merged 1 file(s) into verdicts-autosave.json: 12 added, 0 replaced, 3 kept newer; "
            "store holds 40 verdicts (40 effective) on manifest S1"
        ],
    ),
    (
        "standing-fill",
        [
            "wrote verdicts-standing-fill.json: 25 standing-approval verdicts onto manifest S1",
            "  tea-oy-ligature-break: 25 filled, 0 blocked by except_left, left for review",
            "  WARNING: a verdict outside approve/either/identical sits on 1 matched unit — u-9 under "
            "tea-oy-ligature-break (reject); a rule reaching a window the user judged otherwise is the "
            "shape an over-broad rule takes.",
        ],
    ),
    (
        "standing-merge",
        ["nothing changed: the autosave already holds all 65 verdicts (65 effective)."],
    ),
    ("duplicate-fill-2", ["wrote verdicts-duplicate-fill.json: 3 duplicate-fill verdicts onto manifest S1"]),
    (
        "duplicate-merge-2",
        [
            "merged 1 file(s) into verdicts-autosave.json: 3 added, 0 replaced, 0 kept newer; "
            "store holds 68 verdicts (68 effective) on manifest S1"
        ],
    ),
    ("complaints", ["no open complaints"]),
)


def _run_verdict_update(plan, stdout, returncode=0, spy=None):
    """Run `_do_verdict_update` over a canned stdout. The fake spawn passes every line through the emitter as `_run_step` does, so the terminal output is what a real pass would print."""

    def fake_spawn(name, argv, *, emit, registry, stream):
        if spy is not None:
            spy.append((name, argv))
        for line in stdout.splitlines():
            emit.child_line(name, console.STDOUT, line)
        return _step(name, returncode, stdout=stdout)

    report = ac.CycleReport()
    failures = ac._do_verdict_update(
        report, spawn=fake_spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=plan
    )
    return report, failures


def _verdict_update_ok(report, *, spawn, emit, registry, plan):
    report.merge_status = "merged"
    report.duplicate_fill_status = "filled"
    report.duplicate_merge_status = "merged"
    report.standing_fill_status = "filled"
    report.standing_merge_status = "merged"
    report.standing_merge_lines = ["nothing changed: the autosave already holds all 3 verdicts"]
    report.verdict_update_complete = True
    report.complaints_status = "no open complaints"
    report.complaints_ok = True
    report.carry_out = plan.carry_out
    return []


def _review_facts_clean(report, *, spawn, emit, registry, plan):
    report.facts_status = "updated (matches the last accepted review facts)"


def _job_costs_clean(report, *, spawn, emit, registry, plan):
    report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
    report.job_costs_ok = True


def _js_ok(argv, spawn, emit, registry):
    return _step("gate:js", 0)


def _make_ok(argv, spawn, emit, registry):
    return _step("gate:make-test", 0)


def _contracts_green(
    pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
):
    return _lane_result("rebuild-contracts")


def _conform_green(pool_policy, make_fut, spawn, emit, registry, argv):
    return _conform_result()


def _patch_gate_fingerprints(monkeypatch):
    """Stub the gate green records' keys, for tests that only check whether a green was recorded. Conformance keys are computed around the sweep; the contracts lifecycle snapshots and checks its roster around the suite, and each computation hashes files across the repo, which takes seconds per test and depends on the working tree. A separate test checks that a changed key prevents recording the green."""
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "cfp")
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "rfp-contracts")
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("rfp-contracts", {"key": "rfp-contracts"}))


def _patch_build_chain(monkeypatch):
    monkeypatch.setattr(ac, "_do_corpus_build", _corpus_ok)
    monkeypatch.setattr(ac, "_do_verdict_update", _verdict_update_ok)
    monkeypatch.setattr(ac, "_do_review_facts", _review_facts_clean)
    monkeypatch.setattr(ac, "_do_job_costs", _job_costs_clean)


def test_a_failing_merge_fails_the_cycle(monkeypatch, capsys):
    def failing(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return ["verdict merge failed"]

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", _corpus_ok)
    monkeypatch.setattr(ac, "_do_verdict_update", failing)
    monkeypatch.setattr(ac, "_do_review_facts", _review_facts_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.merge_status == "FAILED (exit 1)"
    assert "verdict merge failed" in capsys.readouterr().out


def test_nothing_runs_after_the_carry_fails():
    """The verdict update stops at its first failing step, and the driver reports every later step as not run because none of them printed its `[phase]` line."""
    report, failures = _run_verdict_update(
        _plan(),
        _verdict_update_stdout(("carry", ["boom"]), failed="carry"),
        returncode=1,
    )
    assert failures == ["carry_verdicts failed"]
    assert report.merge_status == "not run (carry failed)"
    assert report.duplicate_fill_status == "not run (carry failed)"
    assert report.standing_merge_status == "not run (carry failed)"


def test_a_failing_duplicate_fill_stops_the_cascade():
    report, failures = _run_verdict_update(
        _plan(),
        _verdict_update_stdout(
            *_FULL_VERDICT_UPDATE[:2], ("duplicate-fill", ["boom"]), failed="duplicate-fill"
        ),
        returncode=1,
    )
    assert failures == ["duplicate-fill failed"]
    assert report.merge_status == "merged"
    assert report.duplicate_fill_status == "FAILED (exit 1)"
    assert report.duplicate_merge_status == "not run (duplicate-fill failed)"
    assert report.standing_fill_status == "not run (duplicate-fill failed)"
    assert report.standing_merge_status == "not run (duplicate-fill failed)"


def test_a_failing_duplicate_merge_stops_the_cascade():
    report, failures = _run_verdict_update(
        _plan(),
        _verdict_update_stdout(
            *_FULL_VERDICT_UPDATE[:3], ("duplicate-merge", ["boom"]), failed="duplicate-merge"
        ),
        returncode=1,
    )
    assert failures == ["duplicate-merge failed"]
    assert report.duplicate_fill_status == "filled"
    assert report.duplicate_merge_status == "FAILED (exit 1)"
    assert report.standing_fill_status == "not run (duplicate-merge failed)"
    assert report.standing_merge_status == "not run (duplicate-merge failed)"


def test_a_failing_standing_fill_stops_the_cascade():
    report, failures = _run_verdict_update(
        _plan(),
        _verdict_update_stdout(
            *_FULL_VERDICT_UPDATE[:4], ("standing-fill", ["boom"]), failed="standing-fill"
        ),
        returncode=1,
    )
    assert failures == ["standing-fill failed"]
    assert report.standing_fill_status == "FAILED (exit 1)"
    assert report.standing_merge_status == "not run (standing-fill failed)"


@pytest.mark.parametrize(
    ("rounds", "failed", "status"),
    [
        ([("duplicate-fill-2", ["boom"])], "duplicate-fill", "duplicate_fill_status"),
        (
            [_FULL_VERDICT_UPDATE[6], ("duplicate-merge-2", ["boom"])],
            "duplicate-merge",
            "duplicate_merge_status",
        ),
    ],
)
def test_a_later_duplicate_fill_round_failure_leaves_the_first_round_reported_as_run(rounds, failed, status):
    """The verdict update runs the standing fill and merge in the first round, so a failure in round 2 reports them as done and names the round that failed."""
    report, failures = _run_verdict_update(
        _plan(),
        _verdict_update_stdout(*_FULL_VERDICT_UPDATE[:6], *rounds, failed=rounds[-1][0]),
        returncode=1,
    )
    assert failures == [f"{failed} round 2 failed"]
    statuses = {
        "merge_status": "merged",
        "duplicate_fill_status": "filled",
        "duplicate_merge_status": "merged",
        "standing_fill_status": "filled",
        "standing_merge_status": "merged",
    }
    statuses[status] += ", round 2 FAILED (exit 1)"
    assert {name: getattr(report, name) for name in statuses} == statuses


def test_a_carry_only_verdict_update_reports_the_fills_as_never_run():
    """--no-merge and a staging pass both stop the verdict update after the carry, so the fills print no `[phase]` line and the summary reports them as not run."""
    report, failures = _run_verdict_update(
        _plan(no_merge=True), _verdict_update_stdout(_FULL_VERDICT_UPDATE[0], complete=False)
    )
    assert failures == []
    assert report.merge_status == "not run"
    assert report.duplicate_fill_status == "not run"
    assert report.duplicate_merge_status == "not run"
    assert report.standing_fill_status == "not run"
    assert report.standing_merge_status == "not run"
    assert report.verdict_update_complete is False


def test_the_driver_reads_a_line_per_step_out_of_one_child(capsys):
    """One subprocess prints for all the verdict update's steps, and each step's summary lines are taken from that step's own section."""
    spy: list = []
    report, failures = _run_verdict_update(_plan(), _verdict_update_stdout(*_FULL_VERDICT_UPDATE), spy=spy)
    assert failures == []
    assert [name for name, _argv in spy] == ["verdict-update"]

    assert report.merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.merge_lines)
    assert report.duplicate_fill_status == "filled"
    assert any(
        line.startswith("wrote verdicts-duplicate-fill.json: 37 duplicate-fill verdicts")
        for line in report.duplicate_fill_lines
    )
    assert report.duplicate_merge_status == "merged"
    assert any(line.startswith("merged 1 file(s)") for line in report.duplicate_merge_lines)
    assert report.standing_fill_status == "filled"
    assert any(
        line.startswith("wrote verdicts-standing-fill.json: 25 standing-approval verdicts")
        for line in report.standing_fill_lines
    )
    assert any(
        line.endswith("blocked by except_left, left for review") for line in report.standing_fill_lines
    )
    assert any(line.startswith("WARNING:") for line in report.standing_fill_lines)
    assert report.standing_merge_status == "merged"
    assert any(line.startswith("nothing changed") for line in report.standing_merge_lines)
    assert any("carried onto manifest" in line for line in report.carry_lines)
    assert report.carry_counts == {"human": 60000, "matched": 51946, "unmatched": 8054, "orphaned": 12}
    assert ac.cycle_summary_payload(report, [], _plan(), "ok")["carry"] == report.carry_counts
    assert report.complaints_status == "no open complaints"
    assert report.verdict_update_complete is True
    assert len(report.duplicate_fill_lines) == 2
    assert len(report.duplicate_merge_lines) == 2


def test_standing_fill_news_keeps_rules_and_drops_steady_state_combined_matches():
    """Per-rule lines are kept at any count, so a newly added rule shows even at 0 filled. A combined-match line is kept only when it filled or blocked something, which keeps the quadratic number of unchanged combined-match lines out of the console block and cycle_summary.json. The disputed-match warning is always kept. Both line formats are handled: the verdict update runs the fill with --open-only, which prints no already-verdicted column, while a dry run over the whole domain prints it."""
    news = ac._standing_fill_news
    assert news("wrote verdicts-standing-fill.json: 25 standing-approval verdicts onto manifest S1")
    assert news("quiet-rule: 0 filled, 12 already verdicted, 0 blocked by except_left, left for review")
    assert news("quiet-rule: 0 filled, 0 blocked by except_left, left for review")
    assert news("rule-a + rule-b: 2 filled, 0 already verdicted, 0 blocked by except_left, left for review")
    assert news("rule-a + rule-b: 0 filled, 3 already verdicted, 1 blocked by except_left, left for review")
    assert news("rule-a + rule-b: 2 filled, 0 blocked by except_left, left for review")
    assert not news(
        "rule-a + rule-b: 0 filled, 9 already verdicted, 0 blocked by except_left, left for review"
    )
    assert not news("rule-a + rule-b: 0 filled, 0 blocked by except_left, left for review")
    assert news(
        "WARNING: a verdict outside approve/either/identical sits on 1 matched unit — u-9 under "
        "quiet-rule (reject); a rule reaching a window the user judged otherwise is the shape an "
        "over-broad rule takes."
    )
    assert not news(
        "REACHED NOTHING: quiet-rule matched no window on its own and was counted in no combined match."
    )
    assert not news(
        "except_left vocabulary: quiet-rule guards against qsOut, which no window on this corpus joins from."
    )
    assert not news("per-rule reach (3 rules):")


def test_a_later_duplicate_fill_round_folds_into_the_first_rounds_lines():
    """The second duplicate-fill round runs the same step again, so its lines are reported under the first round's name."""
    stdout = _verdict_update_stdout(
        *_FULL_VERDICT_UPDATE[:6],
        (
            "duplicate-fill-2",
            ["wrote verdicts-duplicate-fill.json: 3 duplicate-fill verdicts onto manifest S1"],
        ),
    )
    report, _failures = _run_verdict_update(_plan(), stdout)
    assert len(report.duplicate_fill_lines) == 2
    assert report.duplicate_fill_lines[-1].startswith(
        "wrote verdicts-duplicate-fill.json: 3 duplicate-fill verdicts"
    )


def test_the_disagreement_audit_reaches_the_console(capsys):
    stdout = _verdict_update_stdout(
        (
            "duplicate-fill",
            [
                "wrote verdicts-duplicate-fill.json: 0 duplicate-fill verdicts onto manifest S1",
                "",
                console.WARN + "2 duplicate groups hold disagreeing verdicts — the same change judged "
                "differently; worth a re-check:",
                "  e-123  #units=u-1,u-2",
                "    u-1       ·Day ~b~ ·Tea                approve   looks right",
                "    u-2       ·Day ~b~ ·Tea                reject    stub too long",
            ],
        )
    )
    _run_verdict_update(_plan(), stdout)
    out = capsys.readouterr().out
    assert "warn 2 duplicate groups hold disagreeing verdicts" in out
    # The per-group listing stays in the step's log; the terminal shows only that a disagreement exists.
    assert "e-123  #units=u-1,u-2" not in out


def test_the_executor_spawns_the_argv_the_plan_holds():
    """build_plan writes each step's argv and the executor runs that list, so changing a step's argv changes what is spawned."""
    plan = _plan()
    sentinel = ["uv", "run", "python", "sentinel-verdict-update", "--only-here"]
    {step.name: step for step in plan.steps}["verdict-update"].argv = sentinel
    spy: list = []
    _run_verdict_update(plan, _verdict_update_stdout(*_FULL_VERDICT_UPDATE), spy=spy)
    assert spy == [("verdict-update", sentinel)]


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


def test_the_rebuild_suite_waits_for_run_m1_pass(monkeypatch):
    record = {}

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        record["run_m1_finish"] = time.monotonic()
        return _run_m1_green()

    def fake_contracts(
        pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
    ):
        record["contracts_invoked"] = time.monotonic()
        return _lane_result("rebuild-contracts")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert record["contracts_invoked"] >= record["run_m1_finish"]


def test_the_rebuild_suite_is_skipped_when_run_m1_fails(monkeypatch, capsys):
    called = {"contracts": False}

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        return None

    def fake_contracts(
        pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
    ):
        called["contracts"] = True
        return _lane_result("rebuild-contracts")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert called == {"contracts": False}
    assert report.gate_contracts == "not run (run_m1 gate failed)"
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


def test_pool_queue_runs_make_test_then_conform_then_contracts(monkeypatch):
    """The full queue policy in one run: one heavy pool at a time, with the rebuild suite waiting for make-test and then conform."""
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    release_conform = threading.Event()
    conform_running = threading.Event()
    contracts_started = threading.Event()
    release_contracts = threading.Event()

    def fake_make(argv, spawn, emit, registry):
        make_running.set()
        release_make.wait()
        record["make_finish"] = time.monotonic()
        return _step("gate:make-test", 0)

    def fake_conform(pool_policy, make_fut, spawn, emit, registry, argv):
        conform_running.set()
        release_conform.wait()
        record["conform_finish"] = time.monotonic()
        return _conform_result()

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        if name == "gate:rebuild-contracts":
            record["contracts_start"] = time.monotonic()
            record["contracts_argv"] = argv
            contracts_started.set()
            release_contracts.wait()
            record["contracts_finish"] = time.monotonic()
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
    release_contracts.set()
    t.join()

    assert record["contracts_start"] >= record["conform_finish"]
    assert record["contracts_start"] >= record["make_finish"]
    assert record["contracts_argv"] == ac.contracts_argv()
    assert report.gate_contracts == "green"
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
    assert box["rc"] == 0


def test_the_gate_pool_runs_every_gate_task_at_once():
    """Under the queue policy a waiting task holds its worker for the whole wait (conform waits on make-test, contracts on both), so the pool has a worker for every gate task plus two spare. A smaller pool would not deadlock, because tasks are submitted in the order they wait on each other and the pool is FIFO, but a task could then wait for an unrelated task to finish before it starts."""
    gate_tasks = (
        ac._gate_js_task,
        ac._gate_make_test_task,
        ac._gate_conform_task,
        ac._gate_contracts_task,
    )
    assert ac._GATE_POOL_WORKERS == len(gate_tasks) + 2


def test_summary_exact_under_out_of_order_completion(monkeypatch, capsys):
    ev_js = threading.Event()
    ev_make = threading.Event()
    ev_contracts = threading.Event()

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        report.unmatched = 7777
        report.multi_matched = 0
        report.pins_pass = True
        return _run_m1_green()

    def fake_corpus(report, *, spawn, emit, registry, review_out, **_):
        report.corpus_units = 15903
        report.corpus_rows = 81894
        report.corpus_batches = 16
        report.duplicate_groups = 42
        report.step_seconds["corpus-build"] = 61.0
        return True

    def fake_js(argv, spawn, emit, registry):
        ev_js.wait()
        return _step("gate:js", 0)

    def fake_make(argv, spawn, emit, registry):
        ev_make.wait()
        return _step("gate:make-test", 0)

    def fake_contracts(
        pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
    ):
        ev_contracts.wait()
        return _lane_result("rebuild-contracts", "green (annotated)")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", fake_corpus)
    monkeypatch.setattr(ac, "_do_verdict_update", _verdict_update_ok)
    monkeypatch.setattr(ac, "_do_review_facts", _review_facts_clean)
    monkeypatch.setattr(ac, "_gate_js_task", fake_js)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
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
    ev_contracts.set()
    ev_js.set()
    t.join()

    assert report.corpus_units == 15903
    assert report.corpus_rows == 81894
    assert report.corpus_batches == 16
    assert report.duplicate_groups == 42
    assert report.unmatched == 7777
    assert report.gate_js == "green"
    assert report.gate_make_test == "green"
    assert report.gate_contracts == "green (annotated)"
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


def test_a_pass_files_one_log_per_step_beside_its_plan_and_a_copy_of_the_terminal(tmp_path, capsys):
    """Each run writes one directory holding the plan as printed, a copy of the terminal output, and one log per step with both of the child's streams in arrival order and the stderr lines tagged. `latest` links to it, so a reader tailing a run does not need the stamp."""
    plan = _plan()
    root = tmp_path / "build-logs"
    log_dir = root / "20260101T000000Z-testid"
    registry = ac._ChildRegistry()
    with console.CycleConsole(steps=[step.name for step in plan.steps], log_dir=log_dir) as cycle_console:
        cycle_console.plan_block(ac.render_plan(plan))
        ac._run_step(
            "gate:js",
            [sys.executable, "-c", _TWO_STREAM_CHILD.format(rc=0)],
            emit=cycle_console,
            registry=registry,
            stream=False,
        )

    assert (log_dir / console.PLAN_TXT).read_text().startswith("artifact cycle")
    step_log = log_dir / "01-gate-js.log"
    assert sorted(step_log.read_text().splitlines()) == ["on stdout", "stderr| on stderr"]
    terminal = (log_dir / console.TERMINAL_LOG).read_text()
    assert "gate:js" in terminal and "Runs the review app's node test suite" in terminal
    assert (root / console.LATEST_LINK).resolve() == log_dir.resolve()


def test_a_failed_step_replays_its_whole_output_under_its_own_banner(tmp_path, capsys):
    """A failing child's whole output is printed under its banner, so the reader does not have to find the step's log. The spawn prints the output and the stage prints the closing line, so the output comes before the close."""
    registry = ac._ChildRegistry()
    report = ac.CycleReport()
    with console.CycleConsole(log_dir=tmp_path / "logs") as cycle_console:
        result = ac._run_step(
            "gate:conform",
            [sys.executable, "-c", _TWO_STREAM_CHILD.format(rc=3)],
            emit=cycle_console,
            registry=registry,
            stream=False,
        )
        ac._close_step(cycle_console, report, "gate:conform", result)
    assert result.returncode == 3
    lines = capsys.readouterr().out.splitlines()
    assert "on stdout" in lines
    assert "stderr| on stderr" in lines
    assert "FAILED (exit 3)" in lines[-1] and "gate:conform" in lines[-1]
    assert lines.index("stderr| on stderr") < len(lines) - 1


def test_the_gates_only_rerun_banners_under_the_plans_run_m1_row(tmp_path, capsys):
    """`make cycle-timings --by-step` groups by step name, so the seconds-long gates-only rerun spawns under its own name to keep its time out of the row for a full M1 build. The reader watching the pass expects the plan's run_m1 row, so the alias maps the spawn name to it for the banner, the log filename and the step column."""
    plan = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    log_dir = tmp_path / "logs"
    registry = ac._ChildRegistry()
    report = ac.CycleReport()
    report.unmatched = 12
    report.pins_pass = True
    with console.CycleConsole(
        steps=[step.name for step in plan.steps], log_dir=log_dir, aliases=ac.STEP_ALIASES
    ) as cycle_console:
        result = ac._run_step(
            ac.RUN_M1_GATES_ONLY_STEP,
            [sys.executable, "-c", "print('rerunning the gates')"],
            emit=cycle_console,
            registry=registry,
            stream=False,
        )
        ac._close_step(cycle_console, report, ac.RUN_M1_GATES_ONLY_STEP, result, "ok")
    out = capsys.readouterr().out
    assert "step 1  run_m1  step" in out
    assert ac.RUN_M1_GATES_ONLY_STEP not in out
    assert "ok  12 unmatched, pins pass" in out
    assert (log_dir / "01-run_m1.log").read_text() == "rerunning the gates\n"


def test_two_verbatim_children_interleave_between_lines_and_never_inside_one(capsys):
    """Two real children print verbatim through one console. Lines from the two may interleave, but no line may be spliced into another."""
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


def test_contracts_stays_captured_and_parses_failures(capsys):
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
    argv = ac.contracts_argv()
    result = ac._gate_contracts_task("overlap", None, None, fake_spawn, emit, registry, argv)

    assert seen["name"] == "gate:rebuild-contracts"
    assert seen["stream"] is False
    assert result.check == "rebuild-contracts"
    assert len(result.failed_ids) == 3
    assert result.status == "FAILED (3 unexplained)"

    report = ac.CycleReport()
    failures = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(lambda: result)
        ac._join_gates(report, failures, None, fut, None, None, emit)
    assert report.gate_contracts == "FAILED (3 unexplained)"

    out = capsys.readouterr().out
    assert not any(line.startswith("[gate:rebuild-contracts]") for line in out.splitlines())
    assert "hard rebuild failure (contracts): rebuild/test_boom.py::test_y" in out


def test_gate_make_test_says_so_when_the_font_suite_skipped_itself(capsys):
    """`make test` exits zero whether it ran the suite or skipped it on its own green record. The wrapper's first line says which, and both the closing line and the table row report it, so a skipped suite is not shown as having run."""
    self_skipped = (
        "make test: SKIPPED — input closure unchanged since its last green run (2026-09-04T12:00:00Z). "
        "Run `make test FORCE=1` to run it anyway."
    )
    emit = ac._Emitter()
    result = ac._gate_make_test_task(
        ["make", "test"],
        lambda name, argv, *, emit, registry, stream: _step(name, 0, stdout=self_skipped),
        emit,
        ac._ChildRegistry(),
    )
    assert ac.make_test_self_skipped(result.stdout)
    assert ac.MAKE_TEST_SELF_SKIP_STATUS in capsys.readouterr().out

    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_gates(report, failures, None, None, None, pool.submit(lambda: result), emit)
    assert failures == []
    assert report.gate_make_test == ac.MAKE_TEST_SELF_SKIP_STATUS
    assert report.gate_make_test_green is True
    row = {row.name: row for row in ac.summary_rows(report, _plan(), retention_ran=False)}
    assert row["gate:make-test"].outcome == "ok"
    assert row["gate:make-test"].detail == ac.MAKE_TEST_SELF_SKIP_STATUS

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


def test_a_failed_gate_never_restates_its_outcome_as_its_detail(capsys):
    """A failed gate's detail leaves out the word FAILED, which the outcome column beside it already shows."""
    emit = ac._Emitter()
    ac._close_gate(emit, "gate:js", _step("gate:js", 1))
    ac._close_gate(
        emit,
        "gate:rebuild-contracts",
        _step("gate:rebuild-contracts", 1),
        _lane_result("rebuild-contracts", "FAILED (3 unexplained)", failed_ids=["a", "b", "c"]),
    )
    ac._close_gate(emit, "gate:conform", _step("gate:conform", 0))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")]
    assert lines[0].endswith("FAILED  exit 1")
    assert lines[1].endswith("FAILED  3 unexplained")
    assert lines[2].rstrip().endswith("ok")


def test_classify_rebuild_reads_colored_pytest_output():
    """Under FORCE_COLOR, which the agent harness sets, pytest wraps its FAILED lines in ANSI escapes, and the classifier must still parse the failing ids from them instead of reporting only the exit-code placeholder."""
    colored = "\x1b[31mFAILED\x1b[0m rebuild/test_settle.py::\x1b[1mtest_x\x1b[0m - x"
    result = ac.classify_rebuild_output(colored, 1, "rebuild-contracts")
    assert result.failed_ids == ["rebuild/test_settle.py::test_x"]
    assert result.status == "FAILED (1 unexplained)"


def test_failure_funnels_from_concurrent_branch(monkeypatch, capsys):
    def fake_corpus(report, *, spawn, emit, registry, review_out, **_):
        report.corpus_units = 100
        return True

    def fake_make(argv, spawn, emit, registry):
        return _step("gate:make-test", 1)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", fake_corpus)
    monkeypatch.setattr(ac, "_do_verdict_update", _verdict_update_ok)
    monkeypatch.setattr(ac, "_do_review_facts", _review_facts_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_make_test == "FAILED (exit 1)"
    assert report.gate_js == "green"
    assert report.corpus_units == 100
    assert "make test failed" in capsys.readouterr().out


def test_gate_task_exception_still_prints_one_summary(monkeypatch, capsys):
    def raising_js(argv, spawn, emit, registry):
        raise FileNotFoundError("node not found")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_gate_js_task", raising_js)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_js == "FAILED (exception)"
    assert report.gate_make_test == "green"
    assert report.gate_contracts == "green"
    out = capsys.readouterr().out
    assert out.count("ARTIFACT CYCLE SUMMARY") == 1
    assert "gate:js raised: FileNotFoundError('node not found')" in out


def test_queue_policy_contracts_runs_when_make_test_task_raises(monkeypatch, capsys):
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


def test_queue_policy_contracts_runs_when_conform_task_raises(monkeypatch, capsys):
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
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert report.gate_make_test == "green"
    assert report.gate_contracts == "not run (run_m1 gate failed)"
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


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_caught_stop_signal_takes_the_interrupt_path_and_names_itself(signum, monkeypatch, capsys):
    """SIGTERM and SIGHUP reach `_run_cycle` as `CycleStopped` and take the Ctrl-C path: every child is terminated and reaped, the interrupted summary is written, and the exit status is the shell's for that signal. The summary names the signal that stopped the pass, not SIGINT."""
    registry = ac._ChildRegistry()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    registry.add(proc)

    def stopped(report, *, spawn, emit, registry, **_):
        raise ac.CycleStopped(signum)

    monkeypatch.setattr(ac, "_do_run_m1", stopped)

    rc = ac._run_cycle(_plan(skip_gates=True), ac.CycleReport(), ac._Emitter(), registry)

    assert rc == 128 + signum
    assert proc.poll() is not None
    out = capsys.readouterr().out
    assert "CYCLE INTERRUPTED" in out
    assert f"{signal.Signals(signum).name}: terminated 1 child process(es)" in out
    assert "SIGINT: terminated" not in out
    assert json.loads(cycle_paths.CYCLE_SUMMARY.read_text())["exit"] == "interrupted"


@pytest.fixture
def _stop_dispositions():
    """Set the three stop signals to the dispositions a pass started from a terminal inherits, and restore them afterwards. A worker started under `nohup`, or in the background of a shell without job control, inherits SIGHUP or SIGINT ignored, and `stop_signals` leaves an ignored signal alone, so without this fixture the tests below would depend on how the suite was launched."""
    before = {signum: signal.getsignal(signum) for signum in ac.STOP_SIGNALS}
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
    yield
    for signum, handler in before.items():
        signal.signal(signum, signal.SIG_DFL if handler is None else handler)


def test_stop_signals_raises_once_leaves_an_ignored_signal_alone_and_restores_the_handlers(
    _stop_dispositions,
):
    """The handler raises for the first signal and ignores the rest, because a group signal reaches the driver twice (directly and forwarded by its `uv run` wrapper) and the second must not interrupt the cleanup the first started. A signal ignored on entry stays ignored, as `nohup` leaves SIGHUP, and every replaced handler is restored on exit. The test calls the handler directly, so the test process receives no signal."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    with ac.stop_signals():
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        stop = signal.getsignal(signal.SIGTERM)
        assert callable(stop)
        assert signal.getsignal(signal.SIGINT) is stop
        with pytest.raises(ac.CycleStopped) as caught:
            stop(signal.SIGTERM, None)
        assert caught.value.signum == signal.SIGTERM
        assert isinstance(caught.value, KeyboardInterrupt)
        assert stop(signal.SIGINT, None) is None
        assert stop(signal.SIGTERM, None) is None
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


_STOP_HARNESS = """
import signal
import sys

from rebuild.tools import artifact_cycle as ac

sleeper = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)"
signal.signal(signal.SIGHUP, signal.SIG_IGN)
registry = ac._ChildRegistry()
with ac.stop_signals():
    try:
        ac._run_step("sleeper", [sys.executable, "-c", sleeper, sys.argv[1]], emit=ac._Emitter(), registry=registry, stream=False)
    except ac.CycleStopped as stop:
        registry.terminate_all()
        print(signal.Signals(stop.signum).name, registry.killed_count, flush=True)
"""


def test_a_signal_to_the_driver_alone_stops_and_reaps_its_child(tmp_path):
    """A real SIGTERM sent to the driver's process alone, which is how `kill` on `make` reaches the driver, interrupts the step the driver is waiting on, and the child it spawned is terminated and reaped. The SIGHUP sent first is ignored, because the harness starts with SIGHUP ignored as `nohup` starts a pass."""
    pid_file = tmp_path / "sleeper.pid"
    driver = subprocess.Popen(
        [sys.executable, "-c", _STOP_HARNESS, str(pid_file)],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not (pid_file.exists() and pid_file.read_text().strip()):
            assert driver.poll() is None, driver.communicate()
            assert time.monotonic() < deadline
            time.sleep(0.05)
        sleeper = int(pid_file.read_text())
        driver.send_signal(signal.SIGHUP)
        driver.send_signal(signal.SIGTERM)
        out, err = driver.communicate(timeout=60)
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait()
    assert driver.returncode == 0, err
    assert out.splitlines()[-1] == "SIGTERM 1"
    with pytest.raises(ProcessLookupError):
        os.kill(sleeper, 0)


def test_a_step_child_stays_in_the_cycles_process_group():
    """A step's child stays in the cycle's process group, so a signal sent to that group (`kill -TERM -- -<pgid>`) reaches every process the pass started (doc/running-long-steps.md)."""
    result = ac._run_step(
        "probe",
        [sys.executable, "-c", "import os; print(os.getpgrp())"],
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        stream=False,
    )
    assert result.returncode == 0
    assert int(result.stdout.strip()) == os.getpgrp()


def test_the_land_runs_in_its_own_session_and_a_stop_waits_for_it(tmp_path):
    """The land starts its own session, so a signal to the cycle's group or a Ctrl-C at the terminal never reaches it, and `terminate_all` waits for it without signaling it, while it still terminates every other child."""
    result = ac._run_step(
        "land",
        [sys.executable, "-c", "import os; print(os.getsid(0))"],
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        stream=False,
        uninterruptible=True,
    )
    assert result.returncode == 0
    assert int(result.stdout.strip()) != os.getsid(0)

    marker = tmp_path / "signaled"
    script = (
        "import signal, sys, time\n"
        f"signal.signal(signal.SIGTERM, lambda *a: (open({str(marker)!r}, 'w').close(), sys.exit(3)))\n"
        "print('ready', flush=True)\n"
        "time.sleep(1.0)\n"
    )
    registry = ac._ChildRegistry()
    land = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert land.stdout is not None and land.stdout.readline().strip() == b"ready"
    assert registry.add(land, uninterruptible=True)
    assert registry.add(other)
    assert registry.waits_for_uninterruptible()
    registry.terminate_all()
    land.stdout.close()
    assert land.returncode == 0
    assert not marker.exists()
    assert other.returncode is not None and other.returncode != 0
    assert registry.killed_count == 1


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
    """A step's `env` is added to this process's environment for that child only: the child sees the stated variable and everything else it would inherit, and this process's environment does not change."""
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


def test_sweep_job_budget_is_the_cores_under_the_oracle_shards_memory_clamp():
    """The oracle's unit is a row range, so its width is the usable cores unless the machine's memory divided by `ORACLE_SHARD_BYTES` gives fewer. The test checks both bounds over stated machines: a large one where the cores limit the width, and a small one where the division does and the floor at one applies."""
    from rebuild.tools import memory_budget

    roomy = 1_000_000_000_000
    assert ac.sweep_job_budget(12, total_bytes=roomy) == 12
    assert ac.sweep_job_budget(3, total_bytes=roomy) == 3
    assert ac.sweep_job_budget(1, total_bytes=roomy) == 1
    reserve = memory_budget.os_reserve_bytes(total_bytes=MACHINE_32_GIB)
    fits = (MACHINE_32_GIB - reserve - ac._oracle_absorb_excess_bytes()) // ac.ORACLE_SHARD_BYTES
    assert 1 < fits < 64
    assert ac.sweep_job_budget(64, total_bytes=MACHINE_32_GIB) == fits
    assert ac.sweep_job_budget(64, total_bytes=ac.ORACLE_SHARD_BYTES) == 1
    assert f"at {format_gb(ac.ORACLE_SHARD_BYTES)} GB each" in ac.sweep_job_derivation(
        64, total_bytes=MACHINE_32_GIB
    )
    assert "capped at 64" in ac.sweep_job_derivation(64, total_bytes=roomy)


def test_the_oracle_width_prices_one_absorb_per_settlement_configuration(monkeypatch):
    """The settle-memo absorbs run on the oracle's pool after the ranges, one per settlement configuration at once whatever the width, so the width takes their excess over the range slots they run in off the machine's memory before it divides by `ORACLE_SHARD_BYTES`, and nothing when an absorb fits in a range's slot."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.tools import memory_budget

    monkeypatch.setattr(ac, "ORACLE_ABSORB_BYTES", ac.ORACLE_SHARD_BYTES)
    assert ac._oracle_absorb_excess_bytes() == 0
    monkeypatch.setattr(ac, "ORACLE_ABSORB_BYTES", ac.ORACLE_SHARD_BYTES + 1_000_000_000)
    excess = len(SETTLEMENT_CONFIGS) * 1_000_000_000
    assert ac._oracle_absorb_excess_bytes() == excess
    reserve = memory_budget.os_reserve_bytes(total_bytes=MACHINE_32_GIB)
    fits = (MACHINE_32_GIB - reserve - excess) // ac.ORACLE_SHARD_BYTES
    assert ac.sweep_job_budget(64, total_bytes=MACHINE_32_GIB) == fits
    assert f"less {format_gb(excess)} GB co-resident" in ac.sweep_job_derivation(
        64, total_bytes=MACHINE_32_GIB
    )


def test_both_fleet_machines_run_the_oracle_at_the_cores():
    """On both fleet machines (`doc/fleet.md`) the oracle runs at the cores: the eighteen-core 48 GiB machine at eighteen and the twelve-core one at twelve, with the division never limiting the width before the cap does. The capped budget cannot distinguish the cap from a division that equals it, so the uncapped division is also checked. A change to `ORACLE_SHARD_BYTES` that narrows either machine below its cores, or puts it at the edge of the division, fails here. The suite does not catch a constant that is too low: only the oracle-shard row of `make job-costs` (the cycle's job-costs step) compares it with the workers that ran."""
    from rebuild.tools import memory_budget

    for ncores in (18, 12):
        assert ac.sweep_job_budget(ncores, total_bytes=MACHINE_48_GIB) == ncores
        assert ac.sweep_job_derivation(ncores, total_bytes=MACHINE_48_GIB).startswith(f"{ncores} at ")
        assert (
            memory_budget.how_many_fit(
                ac.ORACLE_SHARD_BYTES,
                coresident_bytes=ac._oracle_absorb_excess_bytes(),
                total_bytes=MACHINE_48_GIB,
            )
            > ncores
        )


def test_the_plan_prints_the_sweep_width_with_its_derivation():
    plan = _plan(ncores=10, total_bytes=MACHINE_48_GIB)
    assert plan.sweep_jobs == ac.sweep_job_budget(10, total_bytes=MACHINE_48_GIB) == 10
    text = _plan_text(plan)
    assert f"run_m1 sweeps --jobs             : {plan.sweep_jobs}  (the oracle's row-range workers, " in text
    assert ac.sweep_job_derivation(10, total_bytes=MACHINE_48_GIB) in text
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[5:7] == ["--jobs", str(plan.sweep_jobs)]
    assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", str(plan.conform_jobs)]


class TestTheCorpusBuildWidth:
    """Both bounds are checked, because which one limits the width matters: the cap holds the build to the cores its lane has on a machine with memory to spare, and the division protects a machine with none."""

    def test_the_cores_bind_where_the_machine_has_room_to_spare(self):
        """A machine with memory for dozens of workers gets its cap, which is a count of cores less the parent: under a gated cycle the build lane's share (`memory_budget.split_cores`), whether or not gate:make-test runs, and by hand or under --skip-gates every usable core."""
        roomy: dict[str, Any] = dict(ncores=12, total_bytes=1_000_000_000_000)
        assert ac.corpus_job_budget(skip_gates=False, **roomy) == 5
        assert ac.corpus_job_budget(skip_gates=False, skip_make_test=True, **roomy) == 5
        assert ac.corpus_job_budget(skip_gates=True, **roomy) == 11

    def test_a_small_machine_splits_its_cores_between_the_lanes(self):
        """A five-core machine's build lane holds three cores, the odd one included, so a gated build runs two workers beside its parent, and a hand build runs four, with memory to spare in both cases. On one core both caps floor at one worker."""
        assert ac.corpus_job_budget(skip_gates=False, ncores=5, total_bytes=1_000_000_000_000) == 2
        assert ac.corpus_job_budget(skip_gates=True, ncores=5, total_bytes=1_000_000_000_000) == 4
        assert ac.corpus_job_budget(skip_gates=False, ncores=1, total_bytes=1_000_000_000_000) == 1
        assert ac.corpus_job_budget(skip_gates=True, ncores=1, total_bytes=1_000_000_000_000) == 1

    def test_both_fleet_machines_keep_a_pooled_build_under_a_gated_cycle(self):
        """On the fleet machines (`doc/fleet.md`) the build runs a pool at its lane share beside gate:make-test's pool: eight workers on the 18-core 48 GiB machine and five on the 12-core one. The lower bound is what a change to the worker constant must not cross, because a width of one is the serial build. `test_the_shipped_corpus_divisor_holds_the_fleet_at_its_lane_share_by_division` in rebuild/test_memory_budget.py checks their division apart from the cap. The derivation is checked too, because the plan line quotes it."""
        for ncores, width in ((18, 8), (12, 5)):
            assert ac.corpus_job_budget(skip_gates=False, ncores=ncores, total_bytes=MACHINE_48_GIB) == width
            assert ac.corpus_job_derivation(
                skip_gates=False, ncores=ncores, total_bytes=MACHINE_48_GIB
            ).startswith(f"{width} at ")

    def test_the_pytest_pool_comes_off_the_machine_before_the_division(self):
        """A cycle runs this build beside gate:make-test's pool, so the pool's bytes at the gate lane's share are added to the co-resident term, and the cap is the build lane's share less the parent. On nine cores the build lane holds five and the gate lane four. The test checks the fit terms directly, because on the fleet machines the memory subtraction does not change the resulting width, and a machine size chosen to sit where it would is a number every change to the constants would have to retune."""
        solo = ac._corpus_fit_terms(skip_gates=True, skip_make_test=False, ncores=9)
        beside = ac._corpus_fit_terms(skip_gates=False, skip_make_test=False, ncores=9)
        unpooled = ac._corpus_fit_terms(skip_gates=False, skip_make_test=True, ncores=9)
        assert solo == (ac.CORPUS_WORKER_BYTES, ac.CORPUS_PARENT_BYTES, 8)
        assert beside == (
            ac.CORPUS_WORKER_BYTES,
            ac.CORPUS_PARENT_BYTES + 4 * ac._font_suite_worker_bytes(),
            4,
        )
        assert unpooled == (ac.CORPUS_WORKER_BYTES, ac.CORPUS_PARENT_BYTES, 4)

    def test_the_floor_answers_one_on_a_machine_that_cannot_hold_a_worker(self):
        """A machine with no memory left after its reserve floors at one in both cases. Width one is the serial build: the parent runs the units phase itself instead of handing it to a worker pool."""
        assert ac.corpus_job_budget(skip_gates=True, ncores=12, total_bytes=8_000_000_000) == 1
        assert ac.corpus_job_budget(skip_gates=False, ncores=12, total_bytes=8_000_000_000) == 1

    def test_the_printed_derivation_is_the_one_that_produced_the_width(self):
        """The plan line and the `--jobs` help quote the derivation, and it and the width come from the same three terms, so the clause starts with the width it explains."""
        for skip_gates in (False, True):
            width = ac.corpus_job_budget(skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_48_GIB)
            derivation = ac.corpus_job_derivation(
                skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_48_GIB
            )
            assert derivation.startswith(f"{width} at ")


class TestTheStandingFillWidth:
    """The cycle sizes the standing fill's refill pool, and the fill uses the width it is given. The fill's code is part of its memo's stamp, so a width computed there would invalidate the memo on every edit to the arithmetic."""

    def test_the_pytest_pool_comes_off_the_machine_but_not_off_the_cap(self):
        """gate:make-test's pool shares the cores with the refill pool while both run, so only its bytes are subtracted, at the width that pool runs at: the gate lane's share while the build lane runs, and every core on a pass that runs neither run_m1 nor the corpus build."""
        solo = ac._standing_fill_terms(skip_gates=True, skip_make_test=False, ncores=9)
        beside = ac._standing_fill_terms(skip_gates=False, skip_make_test=False, ncores=9)
        laneless = ac._standing_fill_terms(
            skip_gates=False, skip_make_test=False, build_lane_runs=False, ncores=9
        )
        assert solo == (ac.STANDING_FILL_WORKER_BYTES, ac.STANDING_FILL_PARENT_BYTES, 9)
        assert beside == (
            ac.STANDING_FILL_WORKER_BYTES,
            ac.STANDING_FILL_PARENT_BYTES + 4 * ac._font_suite_worker_bytes(),
            9,
        )
        assert laneless == (
            ac.STANDING_FILL_WORKER_BYTES,
            ac.STANDING_FILL_PARENT_BYTES + 9 * ac._font_suite_worker_bytes(),
            9,
        )

    def test_the_cores_bind_on_both_fleet_machines(self):
        """A refill worker holds a chunk and two shapers, so on both fleet machines memory allows more workers than there are cores: gated, the eighteen-core 48 GiB machine runs eighteen beside gate:make-test's pool, and the twelve-core one alone runs twelve."""
        assert ac.standing_fill_jobs(skip_gates=False, ncores=18, total_bytes=MACHINE_48_GIB) == 18
        assert ac.standing_fill_jobs(skip_gates=True, ncores=12, total_bytes=MACHINE_48_GIB) == 12

    def test_a_machine_that_cannot_hold_the_parent_floors_at_one(self):
        assert ac.standing_fill_jobs(skip_gates=True, ncores=12, total_bytes=8_000_000_000) == 1
        assert ac.standing_fill_jobs(skip_gates=False, ncores=12, total_bytes=8_000_000_000) == 1

    def test_the_printed_derivation_is_the_one_that_produced_the_width(self):
        for skip_gates in (False, True):
            width = ac.standing_fill_jobs(skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_32_GIB)
            derivation = ac.standing_fill_derivation(
                skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_32_GIB
            )
            assert derivation.startswith(f"{width} at ")
        solo = ac.standing_fill_derivation(skip_gates=True, ncores=10, total_bytes=MACHINE_32_GIB)
        assert f"less {format_gb(ac.STANDING_FILL_PARENT_BYTES)} GB co-resident" in solo

    def test_the_plan_states_the_width_on_the_verdict_updates_argv_and_in_its_text(self):
        """Every width is stated on the command line, including one, and the plan block gives its derivation as it does for the corpus build."""
        for skip_gates in (False, True):
            plan = _plan(skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_32_GIB)
            width = ac.standing_fill_jobs(skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_32_GIB)
            by_name = {step.name: step for step in plan.steps}
            argv = _argv(by_name["verdict-update"])
            assert argv[argv.index("--standing-fill-jobs") + 1] == str(width)
            assert plan.standing_fill_jobs == width
            text = _plan_text(plan)
            assert "verdict-update --standing-fill-jobs" in text
            assert (
                ac.standing_fill_derivation(skip_gates=skip_gates, ncores=10, total_bytes=MACHINE_32_GIB)
                in text
            )
        small = _plan(ncores=1)
        assert _argv({step.name: step for step in small.steps}["verdict-update"])[-2:] == [
            "--standing-fill-jobs",
            "1",
        ]


def _lane_conform_line(plan: ac.Plan) -> str:
    """Return the plan block's one Lane conform line."""
    (line,) = [line for line in _plan_text(plan).splitlines() if "Lane conform" in line]
    return line


def _plan_conform_derivation(plan: ac.Plan, *, ncores: int, total_bytes: int) -> str:
    """Return the conformance sweep's derivation over the flags the plan resolved, so the call matches what `build_plan` computed."""
    return ac.conform_job_derivation(
        skip_gates=plan.skip_gates,
        skip_make_test=plan.skip_make_test,
        skip_corpus=plan.skip_corpus,
        verdict_update_runs=plan.runs("verdict-update"),
        pool_policy=plan.pool_policy,
        build_lane_runs=not (plan.skip_run_m1 and plan.skip_corpus),
        ncores=ncores,
        total_bytes=total_bytes,
    )


class TestTheConformSweepWidth:
    """The cycle sizes gate:conform's sweep: one spawn process per unit, each holding `CONFORM_SWEEP_UNIT_BYTES`. The conformance sweep is submitted when run_m1's gate passes, so it runs beside the build lane's corpus build or the verdict-update step after it, and the larger of the two that the pass runs is subtracted from memory before the division. Its cap is the cores less the corpus build's processes and gate:make-test's pool under the overlap policy, never below the acceptance-configuration count."""

    def test_the_corpus_build_comes_off_the_machine_before_the_division(self):
        """Checked at the fit terms, where no machine size enters, for the same reason as the memory set aside beside the corpus build: on no fleet machine does the subtraction change the conformance sweep's width. The corpus term is the build's parent plus the workers `corpus_job_budget` gives the same pass. A pass that runs the verdict-update step without the build subtracts the verdict update's process and the refill pool `standing_fill_jobs` gives instead. gate:make-test's pool is added under the overlap policy only, because the queue policy makes the conformance sweep wait for make-test. The cap is `_conform_core_cap`'s in every case."""

        def corpus(skip_make_test):
            return ac.CORPUS_PARENT_BYTES + ac.CORPUS_WORKER_BYTES * ac.corpus_job_budget(
                skip_gates=False, skip_make_test=skip_make_test, ncores=9, total_bytes=MACHINE_48_GIB
            )

        def verdict_update(skip_make_test):
            return ac.STANDING_FILL_PARENT_BYTES + ac.STANDING_FILL_WORKER_BYTES * ac.standing_fill_jobs(
                skip_gates=False, skip_make_test=skip_make_test, ncores=9, total_bytes=MACHINE_48_GIB
            )

        def flags(skip_make_test, skip_corpus, pool_policy) -> dict[str, Any]:
            return dict(
                skip_gates=False,
                skip_make_test=skip_make_test,
                skip_corpus=skip_corpus,
                pool_policy=pool_policy,
                ncores=9,
                total_bytes=MACHINE_48_GIB,
            )

        def terms(*, skip_make_test=False, skip_corpus=False, verdict_update_runs=False, pool_policy="queue"):
            return ac._conform_fit_terms(
                **flags(skip_make_test, skip_corpus, pool_policy), verdict_update_runs=verdict_update_runs
            )

        def cap(*, skip_make_test=False, skip_corpus=False, pool_policy="queue"):
            return ac._conform_core_cap(**flags(skip_make_test, skip_corpus, pool_policy))[0]

        per_unit = ac.CONFORM_SWEEP_UNIT_BYTES
        assert terms() == (per_unit, corpus(False), cap())
        assert terms(skip_corpus=True) == (per_unit, 0, cap(skip_corpus=True))
        make_test = ac._make_test_pool_bytes(skip_make_test=False, ncores=9)
        assert make_test > 0
        assert terms(pool_policy="overlap") == (
            per_unit,
            corpus(False) + make_test,
            cap(pool_policy="overlap"),
        )
        assert terms(pool_policy="overlap", skip_corpus=True) == (
            per_unit,
            make_test,
            cap(pool_policy="overlap", skip_corpus=True),
        )
        assert terms(pool_policy="overlap", skip_make_test=True) == (
            per_unit,
            corpus(True),
            cap(pool_policy="overlap", skip_make_test=True),
        )
        assert terms(skip_make_test=True) == (per_unit, corpus(True), cap(skip_make_test=True))

        assert 0 < corpus(False) < verdict_update(False)
        assert terms(verdict_update_runs=True) == (per_unit, verdict_update(False), cap())
        assert terms(skip_corpus=True, verdict_update_runs=True) == (
            per_unit,
            verdict_update(False),
            cap(skip_corpus=True),
        )
        assert terms(skip_corpus=True, verdict_update_runs=True, pool_policy="overlap") == (
            per_unit,
            verdict_update(False) + make_test,
            cap(skip_corpus=True, pool_policy="overlap"),
        )
        assert terms(skip_corpus=True, verdict_update_runs=True, skip_make_test=True) == (
            per_unit,
            verdict_update(True),
            cap(skip_corpus=True, skip_make_test=True),
        )

    def test_the_larger_build_lane_step_is_the_one_that_comes_off(self, monkeypatch):
        """The conformance sweep starts beside the corpus build, and a conformance sweep still running when the build finishes, or one the queue policy starts late, runs beside the verdict-update step, so a pass that runs both subtracts the larger. The corpus build stops at the build lane's share of the cores while the standing fill takes all of them, so on a machine with enough cores the verdict-update step is the larger and is the one subtracted. On one core each pool runs one worker, and a corpus worker outweighs a refill worker, so with the two parents held equal the corpus build is the larger there; the test states that ordering before relying on it."""
        machine: dict[str, Any] = dict(skip_gates=False, skip_make_test=False, total_bytes=MACHINE_48_GIB)
        wide: dict[str, Any] = dict(machine, ncores=40)
        corpus = ac.CORPUS_PARENT_BYTES + ac.CORPUS_WORKER_BYTES * ac.corpus_job_budget(**wide)
        verdict_update = (
            ac.STANDING_FILL_PARENT_BYTES + ac.STANDING_FILL_WORKER_BYTES * ac.standing_fill_jobs(**wide)
        )
        assert verdict_update > corpus
        assert ac._conform_build_lane(**wide, skip_corpus=False, verdict_update_runs=True) == (
            "verdict-update",
            verdict_update,
        )
        assert ac._conform_build_lane(**wide, skip_corpus=False, verdict_update_runs=False) == (
            "corpus-build",
            corpus,
        )
        assert (
            ac._conform_fit_terms(**wide, skip_corpus=False, verdict_update_runs=True, pool_policy="queue")[1]
            == verdict_update
        )
        monkeypatch.setattr(ac, "STANDING_FILL_PARENT_BYTES", ac.CORPUS_PARENT_BYTES)
        narrow: dict[str, Any] = dict(machine, ncores=1)
        narrow_corpus = ac.CORPUS_PARENT_BYTES + ac.CORPUS_WORKER_BYTES * ac.corpus_job_budget(**narrow)
        narrow_fill = ac.STANDING_FILL_PARENT_BYTES + ac.STANDING_FILL_WORKER_BYTES * ac.standing_fill_jobs(
            **narrow
        )
        assert narrow_corpus > narrow_fill
        assert ac._conform_build_lane(**narrow, skip_corpus=False, verdict_update_runs=True) == (
            "corpus-build",
            narrow_corpus,
        )
        assert ac._conform_build_lane(**narrow, skip_corpus=True, verdict_update_runs=False) == ("", 0)

    def test_every_fleet_machine_runs_the_conform_sweep_as_wide_as_its_cores_allow(self):
        """On the fleet machines (`doc/fleet.md`), the 48 GiB machines at twelve and eighteen cores, under either pool policy, the conformance sweep runs as many units as the machine has cores beside the verdict-update step alone, and beside a gated build lane (the corpus build and the verdict-update step) the cores less the build's parent and workers, never fewer than the acceptance configurations; under the overlap policy both widths also lose gate:make-test's pool. The division never limits the width before the cap does. The capped budget cannot distinguish the cap from a division that equals it, so the uncapped division is also checked, and a change to the constant that narrows a fleet machine fails here. The suite does not catch a constant that is too low: only the conform-sweep row of `make job-costs` compares it with the units that ran."""
        from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
        from rebuild.tools import memory_budget

        configs = len(ACCEPTANCE_CONFIGS)
        for ncores, total_bytes in ((12, MACHINE_48_GIB), (18, MACHINE_48_GIB)):
            corpus = 1 + ac.corpus_job_budget(
                skip_gates=False, skip_make_test=False, ncores=ncores, total_bytes=total_bytes
            )
            for pool_policy, skip_corpus in itertools.product(ac.POOL_POLICIES, (False, True)):
                make_test = ac.make_test_pool_width(ncores=ncores) if pool_policy == "overlap" else 0
                gated: dict[str, Any] = dict(
                    skip_gates=False,
                    skip_make_test=False,
                    skip_corpus=skip_corpus,
                    verdict_update_runs=True,
                    pool_policy=pool_policy,
                    ncores=ncores,
                    total_bytes=total_bytes,
                )
                width = max(configs, ncores - (0 if skip_corpus else corpus) - make_test)
                assert ac.conform_job_budget(**gated) == width
                assert ac.conform_job_derivation(**gated).startswith(f"{width} at ")
                _per_unit, coresident, _cap = ac._conform_fit_terms(**gated)
                assert coresident > 0
                assert (
                    memory_budget.how_many_fit(
                        ac.CONFORM_SWEEP_UNIT_BYTES, coresident_bytes=coresident, total_bytes=total_bytes
                    )
                    > width
                )

    def test_an_idle_build_lane_gives_the_conform_sweep_every_core(self):
        """A machine with memory to spare and nothing beside the conformance sweep runs one unit per core, since every fleet machine has fewer cores than the sweep has units, and the cores are read through `memory_budget.usable_cores`, so a small cgroup allowance never widens the conformance sweep past the cores it may use."""
        for ncores in (1, 2, 4, 6, 10, 12, 18):
            assert (
                ac.conform_job_budget(
                    skip_corpus=True, pool_policy="queue", ncores=ncores, total_bytes=1_000_000_000_000
                )
                == ncores
            )
            assert (
                ac.conform_job_budget(
                    skip_gates=True, skip_corpus=True, ncores=ncores, total_bytes=1_000_000_000_000
                )
                == ncores
            )

    def test_beside_a_corpus_build_the_conform_sweep_takes_the_cores_the_build_leaves(self):
        """Beside a corpus build the conformance sweep's cap is the cores less the build's parent and its workers, and under the overlap policy less gate:make-test's pool as well, the subtraction the rebuild suite's width makes (`contracts_pool_width` gives the reasons). The verdict-update step's refill pool is not subtracted. The cap never falls below the acceptance configurations, the width one process per configuration gives, while memory can still narrow the width below it. The derivation says which of the two bounds set the cap. Under the overlap policy beside a gated build the two lanes already fill the cores, so the subtraction leaves none and the floor sets the cap. With the corpus build skipped the sweep takes the build lane's cores beside gate:make-test's gate-lane share, and on a pass that runs neither run_m1 nor the corpus build that pool holds every core, so the floor sets the cap again."""
        from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS

        configs = len(ACCEPTANCE_CONFIGS)
        wide: dict[str, Any] = dict(skip_gates=False, skip_make_test=False, total_bytes=1_000_000_000_000)
        corpus = ac.corpus_job_budget(**wide, ncores=40)
        make_test = ac.make_test_pool_width(ncores=40)
        beside: dict[str, Any] = dict(wide, skip_corpus=False, ncores=40)
        assert ac.conform_job_budget(**beside, pool_policy="queue") == 40 - 1 - corpus
        assert (
            ac.conform_job_budget(**beside, pool_policy="queue", verdict_update_runs=True) == 40 - 1 - corpus
        )
        assert 40 - 1 - corpus - make_test == 0
        assert ac.conform_job_budget(**beside, pool_policy="overlap") == configs
        assert ac.conform_job_derivation(**beside, pool_policy="queue").endswith(
            f"; the cap is 40 cores less the corpus build's parent and its {corpus} workers"
        )
        assert ac.conform_job_derivation(**beside, pool_policy="overlap").endswith(
            f"less gate:make-test's {make_test} (overlap policy) leave none"
        )
        unbuilt: dict[str, Any] = dict(wide, skip_corpus=True, ncores=40)
        assert ac.conform_job_budget(**unbuilt, pool_policy="overlap") == 40 - make_test
        assert ac.conform_job_budget(**unbuilt, pool_policy="overlap", build_lane_runs=False) == configs

        narrow: dict[str, Any] = dict(wide, skip_corpus=False, ncores=10)
        assert 10 - 1 - ac.corpus_job_budget(**wide, ncores=10) < configs
        assert ac.conform_job_budget(**narrow) == configs
        assert f"the cap is the {configs} acceptance configurations, its floor, since 10 cores less" in (
            ac.conform_job_derivation(**narrow)
        )
        assert ac.conform_job_budget(**dict(narrow, ncores=4)) == 4
        assert ac.conform_job_budget(**dict(narrow, total_bytes=MACHINE_32_GIB // 2)) < configs

    def test_a_conform_sweep_width_of_one_is_stated_on_the_argv(self, monkeypatch):
        """A machine where the pooled conformance sweep does not fit beside the corpus build floors at one, the serial conformance sweep, and the argv states that width like any other; without it run_m1 would use its own default, a width this plan did not budget. The oracle's width is a separate budget and stays above one."""
        monkeypatch.setattr(ac, "CONFORM_SWEEP_UNIT_BYTES", 10**12)
        plan = _plan(ncores=12)
        by_name = {step.name: step for step in plan.steps}
        assert plan.conform_jobs == 1
        assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", "1"]
        assert plan.sweep_jobs > 1
        assert _argv(by_name["run_m1"])[5:7] == ["--jobs", str(plan.sweep_jobs)]

    def test_the_plan_prints_the_conform_sweep_width_with_its_derivation(self):
        """Every Lane conform line that runs the conformance sweep quotes its width, the constant it divides by, and the derivation over the flags the plan resolved. The co-resident term is the larger build-lane step, plus gate:make-test's pool under the overlap policy: the corpus build when it runs and holds more than the verdict-update step, the verdict-update step when the build does not run or the step holds more. For a pass that runs neither, the line says so and prints no co-resident term unless gate:make-test's pool runs beside the conformance sweep. On a ten-core machine the refill pool takes every core while the corpus build takes the build lane's share, so a gated pass that runs both steps subtracts the verdict-update step, and only a pass that runs the build alone shows the build's term."""
        machine: dict[str, Any] = dict(ncores=10, total_bytes=MACHINE_32_GIB)
        cases = {
            "queued": _plan(pool_policy="queue", **machine),
            "make-test skipped": _plan(
                skip_make_test=True, make_test_note="closure unchanged", pool_policy="queue", **machine
            ),
            "overlap": _plan(pool_policy="overlap", **machine),
        }
        assert "QUEUED behind gate:make-test" in _lane_conform_line(cases["queued"])
        assert "gate:make-test not running, so no queueing" in _lane_conform_line(cases["make-test skipped"])
        assert (
            "CO-RESIDENT with gate:make-test's pool and gate:rebuild-contracts' pool (overlap policy)"
            in _lane_conform_line(cases["overlap"])
        )
        for plan in cases.values():
            assert plan.runs("verdict-update")
            line = _lane_conform_line(plan)
            derivation = _plan_conform_derivation(plan, **machine)
            assert (
                f"(--jobs {plan.conform_jobs}; CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, the verdict-update step outweighing the corpus build, so beside the verdict update's process and its {plan.standing_fill_jobs} refill workers"
                in line
            )
            assert line.endswith(f"; {derivation})")
            assert f"at {format_gb(ac.CONFORM_SWEEP_UNIT_BYTES)} GB each" in derivation
            assert "less a reserve of" in derivation
            assert "GB co-resident" in derivation
        overlap = cases["overlap"]
        assert "workers and gate:make-test's pool; " in _lane_conform_line(overlap)
        _per_unit, coresident, _cap = ac._conform_fit_terms(
            skip_gates=False,
            skip_make_test=False,
            skip_corpus=False,
            verdict_update_runs=True,
            pool_policy="overlap",
            ncores=10,
            total_bytes=MACHINE_32_GIB,
        )
        assert coresident > ac.CORPUS_PARENT_BYTES + ac.CORPUS_WORKER_BYTES * overlap.corpus_jobs
        assert f"less {format_gb(coresident)} GB co-resident" in _plan_conform_derivation(overlap, **machine)
        assert "gate:make-test's pool" not in _lane_conform_line(cases["queued"])

        corpus_only = _plan(
            skip_verdict_update=True, verdict_update_note="nothing moved", pool_policy="queue", **machine
        )
        assert not corpus_only.runs("verdict-update")
        line = _lane_conform_line(corpus_only)
        assert (
            f"(--jobs {corpus_only.conform_jobs}; CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, beside the corpus build's parent and its {corpus_only.corpus_jobs} workers; "
            in line
        )
        assert line.endswith(f"; {_plan_conform_derivation(corpus_only, **machine)})")

        beside_verdict_update = _plan(
            skip_corpus=True, corpus_note="inputs unchanged", pool_policy="queue", **machine
        )
        line = _lane_conform_line(beside_verdict_update)
        derivation = _plan_conform_derivation(beside_verdict_update, **machine)
        workers = beside_verdict_update.standing_fill_jobs
        assert (
            f"(--jobs {beside_verdict_update.conform_jobs}; CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, the corpus build not running this pass, so beside the verdict update's process and its {workers} refill workers; "
            in line
        )
        assert line.endswith(f"; {derivation})")
        verdict_update = ac.STANDING_FILL_PARENT_BYTES + ac.STANDING_FILL_WORKER_BYTES * workers
        assert f"less {format_gb(verdict_update)} GB co-resident" in derivation

        idle = {
            "skip_corpus": True,
            "corpus_note": "inputs unchanged",
            "skip_verdict_update": True,
            "verdict_update_note": "nothing moved",
        }
        alone = _plan(**idle, pool_policy="queue", **machine)
        assert not alone.runs("verdict-update")
        line = _lane_conform_line(alone)
        derivation = _plan_conform_derivation(alone, **machine)
        assert (
            f"(--jobs {alone.conform_jobs}; CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, neither the corpus build nor the verdict-update step running this pass, so nothing co-resident; "
            in line
        )
        assert line.endswith(f"; {derivation})")
        assert "GB co-resident" not in derivation

        overlap_alone = _plan(**idle, pool_policy="overlap", **machine)
        line = _lane_conform_line(overlap_alone)
        assert "so only gate:make-test's pool co-resident; " in line
        assert "GB co-resident" in _plan_conform_derivation(overlap_alone, **machine)

        wide: dict[str, Any] = dict(ncores=40, total_bytes=MACHINE_48_GIB)
        outweighed = _plan(**wide, pool_policy="queue")
        line = _lane_conform_line(outweighed)
        assert (
            f"CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, the verdict-update step outweighing the corpus build, so beside the verdict update's process and its {outweighed.standing_fill_jobs} refill workers; "
            in line
        )
        assert line.endswith(f"; {_plan_conform_derivation(outweighed, **wide)})")

    def test_the_printed_derivation_is_the_one_that_produced_the_width(self):
        """The plan line quotes the derivation, and it and the width come from the same three terms, so in every case the clause starts with the width it explains."""
        for total_bytes in (MACHINE_32_GIB, 20_000_000_000):
            for skip_make_test in (False, True):
                for skip_corpus, verdict_update_runs in itertools.product((False, True), repeat=2):
                    for pool_policy in ac.POOL_POLICIES:
                        kw: dict[str, Any] = dict(
                            skip_gates=False,
                            skip_make_test=skip_make_test,
                            skip_corpus=skip_corpus,
                            verdict_update_runs=verdict_update_runs,
                            pool_policy=pool_policy,
                            ncores=10,
                            total_bytes=total_bytes,
                        )
                        width = ac.conform_job_budget(**kw)
                        assert ac.conform_job_derivation(**kw).startswith(f"{width} at ")


def test_the_land_holds_less_than_either_build_lane_step_before_it():
    """The land follows the corpus build and the verdict update in the build lane, and the lane's reservation (`_conform_build_lane`) covers the larger of those two, so it covers the land only while the land's constant stays below both parents'."""
    assert ac.LAND_BYTES < min(ac.CORPUS_PARENT_BYTES, ac.STANDING_FILL_PARENT_BYTES)


def test_the_job_budgets_answer_the_cgroup_allowance_rather_than_the_hosts_core_count(monkeypatch):
    """A CPU quota is invisible to `os.cpu_count()`, so the oracle, conformance-sweep, corpus and gate:make-test budgets read their cores through `memory_budget.usable_cores`. The test runs the real probe over a fixture cgroup root that allows two cores, which is below `len(ACCEPTANCE_CONFIGS)`, so the oracle and the conformance sweep return the allowance, a hand corpus build returns the allowance less its parent, and a gated cycle splits the allowance between the lanes (`memory_budget.split_cores`). Each call passes a terabyte of memory so that only the core count limits the width. An explicit `ncores` still takes precedence over the probe."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
    from rebuild.tools import memory_budget

    host = os.process_cpu_count() or os.cpu_count() or 1
    probe = memory_budget.usable_cores
    root = REPO_ROOT / "rebuild" / "fixtures" / "memory_budget" / "container-v2"
    allowed = probe(root)
    monkeypatch.setattr(memory_budget, "usable_cores", functools.partial(probe, root))
    build_lane, gate_lane = memory_budget.split_cores(allowed)
    assert allowed == min(host, 2) < len(ACCEPTANCE_CONFIGS)
    assert ac.sweep_job_budget(total_bytes=1_000_000_000_000) == allowed
    assert ac.corpus_job_budget(skip_gates=True, total_bytes=1_000_000_000_000) == max(1, allowed - 1)
    assert ac.corpus_job_budget(skip_gates=False, total_bytes=1_000_000_000_000) == max(1, build_lane - 1)
    assert ac.make_test_pool_width() == gate_lane
    assert ac.conform_job_budget(skip_gates=True, skip_corpus=True, total_bytes=1_000_000_000_000) == allowed
    assert ac.sweep_job_budget(12, total_bytes=1_000_000_000_000) == 12
    assert ac.corpus_job_budget(skip_gates=True, ncores=12, total_bytes=1_000_000_000_000) == 11


def test_make_test_pool_width_is_the_gate_lanes_share_of_the_cores():
    """While the build lane runs, gate:make-test's pool takes the gate lane's share of the cores (`memory_budget.split_cores`), floored at one, which is the width the budgets reserve for."""
    assert ac.make_test_pool_width(ncores=12) == 6
    assert ac.make_test_pool_width(ncores=6) == 3
    assert ac.make_test_pool_width(ncores=1) == 1


def test_make_test_takes_every_core_when_no_build_lane_runs():
    """On a pass that runs neither run_m1 nor the corpus build, nothing holds the build lane, so gate:make-test's pool takes every core."""
    assert ac.make_test_pool_width(build_lane_runs=False, ncores=18) == 18


def test_the_plan_gives_make_test_every_core_only_when_neither_build_lane_step_runs():
    """`build_plan` tells the budgets the build lane runs when run_m1 or the corpus build does. On a roomy 18-core machine gate:make-test's pool is the gate lane's share while either runs and every core when neither does, and the widths that subtract that pool (the rebuild suite's and the conformance sweep's cap under the overlap policy, and the standing fill's memory term) subtract the same width. The table build's width subtracts the gate lane's share on any pass, because run_m1 holds the build lane whenever it runs, and its plan line names that width."""
    machine: dict[str, Any] = dict(ncores=18, total_bytes=MACHINE_48_GIB, pool_policy="overlap")
    for shape in ({}, {"skip_run_m1": True}, {"skip_corpus": True}):
        assert _plan(**machine, **shape).make_test_workers == 9
    idle = _plan(**machine, skip_run_m1=True, skip_corpus=True)
    assert idle.make_test_workers == 18
    assert idle.contracts_workers == 1
    assert "gate:make-test's 18 (overlap policy)" in idle.conform_reason
    alone = ac.standing_fill_derivation(
        skip_gates=False, build_lane_runs=False, ncores=18, total_bytes=MACHINE_48_GIB
    )
    beside = ac.standing_fill_derivation(skip_gates=False, ncores=18, total_bytes=MACHINE_48_GIB)
    assert alone != beside
    assert idle.standing_fill_reason.endswith(alone)
    assert "less gate:make-test's 9 workers" in idle.kernel_reason


@pytest.mark.parametrize("ncores", [18, 12, 10])
def test_the_lanes_partition_the_cores_under_a_gated_cycle(ncores):
    """With memory to spare, a gated cycle's corpus build (its parent and its workers) and gate:make-test's pool together take exactly the machine's cores, so neither lane books a core the other holds."""
    corpus = ac.corpus_job_budget(skip_gates=False, ncores=ncores, total_bytes=1_000_000_000_000)
    assert 1 + corpus + ac.make_test_pool_width(ncores=ncores) == ncores


def test_a_stated_pool_width_is_the_width_the_cycle_reserves_by(monkeypatch):
    """The make-test child inherits this process's environment, so a width already set in PYTEST_XDIST_AUTO_NUM_WORKERS is the width its pool takes. The cycle reserves memory for that width, so the memory set aside for the pool matches the pool that runs."""
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "9")
    assert ac.make_test_pool_width(ncores=1) == 9
    assert ac.kernel_threads_budget(ncores=12, total_bytes=MACHINE_38_GB) == 3
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "64")
    assert ac.kernel_threads_budget(ncores=12, total_bytes=MACHINE_38_GB) == 1


def test_kernel_threads_budget_takes_the_pytest_pool_off_the_machine_first():
    """The kernel width subtracts the pytest pool along with default's retained memo and the parked fold products before dividing. A 38 GB machine fits four deltas alone, and subtracting an eight-core machine's pytest pool leaves room for three. At this boundary forgetting the memory set aside for the pool changes the answer."""
    solo = ac.kernel_threads_budget(skip_make_test=True, ncores=8, total_bytes=MACHINE_38_GB)
    beside = ac.kernel_threads_budget(ncores=8, total_bytes=MACHINE_38_GB)
    assert (solo, beside) == (4, 3)


def test_the_kernel_fits_as_many_deltas_with_the_test_gates_running_as_run_alone_on_both_fleet_machines(
    monkeypatch,
):
    """The kernel terms (`kernel_exec.table_build_booking_bytes`) are chosen so that gate:make-test's pytest pool costs neither fleet machine a worker slot or the memo writes beside the wave, and this test checks the gated widths and memo write orders, which only the cycle computes. On both 48 GiB machines, at eighteen and at twelve cores, the gated and skipped-gate widths are equal to the configuration count, every delta and `default`'s fold preparation in a slot of its own, so the wave runs in one round, and at that width both write the memo files beside the wave and start every delta from scratch beside `default`. `AMS_KERNEL_THREADS` is cleared first, because an exported width would pass these assertions whatever the constants are."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS

    monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
    for ncores in (18, 12):
        gated = ac.kernel_threads_budget(ncores=ncores, total_bytes=MACHINE_48_GIB)
        assert gated == len(SETTLEMENT_CONFIGS)
        assert (
            ac.kernel_threads_budget(skip_make_test=True, ncores=ncores, total_bytes=MACHINE_48_GIB) == gated
        )
        for skip_make_test in (False, True):
            assert ac.memo_writes_overlap_budget(
                gated, skip_make_test=skip_make_test, ncores=ncores, total_bytes=MACHINE_48_GIB
            )
            assert (
                ac.scratch_beside_default_budget(
                    gated,
                    overlap_memo_writes=True,
                    skip_make_test=skip_make_test,
                    ncores=ncores,
                    total_bytes=MACHINE_48_GIB,
                )
                == len(SETTLEMENT_CONFIGS) - 1
            )


def test_the_memo_write_order_takes_the_pytest_pool_off_the_machine_as_the_width_does():
    """The cycle decides the memo write order with gate:make-test's pool off the machine, because run_m1 cannot see that pool. On an invented machine just large enough for the whole wave with the memo writers beside it and nothing else, the width stays the configuration count with the pool running, but the writers no longer fit beside it, so the plan passes `--no-overlap-memo-writes`, and a pass that skips the gate passes `--overlap-memo-writes`."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import table_build_booking_bytes
    from rebuild.tools import memory_budget

    count = len(SETTLEMENT_CONFIGS)
    beside = table_build_booking_bytes(count, configs=count, overlap=True)
    total = next(
        total
        for total in range(beside, 2 * beside, 10_000_000)
        if total - memory_budget.os_reserve_bytes(total_bytes=total) >= beside
    )
    pool = ac._make_test_pool_bytes(skip_make_test=False, ncores=8)
    assert table_build_booking_bytes(count, configs=count) + pool <= total - memory_budget.os_reserve_bytes(
        total_bytes=total
    ), "the invented machine is meant to keep the whole wave with the pool running"
    for skip_make_test, flag in ((False, "--no-overlap-memo-writes"), (True, "--overlap-memo-writes")):
        assert ac.kernel_threads_budget(skip_make_test=skip_make_test, ncores=8, total_bytes=total) == count
        plan = _plan(ncores=8, total_bytes=total, skip_make_test=skip_make_test)
        assert plan.overlap_memo_writes is skip_make_test
        assert flag in plan.argv("run_m1")


def test_the_deltas_from_scratch_take_the_pytest_pool_off_the_machine_as_the_width_does():
    """The cycle decides how many deltas start from scratch beside `default` with gate:make-test's pool off the machine, because run_m1 cannot see that pool. On an invented machine just large enough for the whole wave with the memo writers beside it and every delta from scratch beside `default`, and nothing else, the width and the memo-write order stay as they are with the pool running, but fewer deltas fit from scratch, so the plan passes fewer than a pass that skips the gate."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import table_build_booking_bytes
    from rebuild.tools import memory_budget

    count = len(SETTLEMENT_CONFIGS)
    every = table_build_booking_bytes(count, configs=count, overlap=True, scratch_beside_default=count - 1)
    total = next(
        total
        for total in range(every, 2 * every, 10_000_000)
        if total - memory_budget.os_reserve_bytes(total_bytes=total) >= every
    )
    pool = ac._make_test_pool_bytes(skip_make_test=False, ncores=8)
    assert table_build_booking_bytes(
        count, configs=count, overlap=True
    ) + pool <= total - memory_budget.os_reserve_bytes(
        total_bytes=total
    ), "the invented machine is meant to keep the whole wave and the memo writers beside it with the pool running"
    passed = {}
    for skip_make_test in (False, True):
        plan = _plan(ncores=8, total_bytes=total, skip_make_test=skip_make_test)
        assert plan.kernel_threads == count and plan.overlap_memo_writes
        argv = plan.argv("run_m1")
        passed[skip_make_test] = int(argv[argv.index("--scratch-beside-default") + 1])
        assert passed[skip_make_test] == plan.scratch_beside_default
    assert passed[False] < passed[True] == count - 1


def test_replay_threads_budget_takes_the_pytest_pool_off_the_machine_first():
    """The replay width subtracts gate:make-test's pool before dividing, as the kernel width does. So the gated width is never wider than the skipped-gate width, and where the cap does not bind, the pool is what separates them. Both are capped at the configuration count and the cores."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import REPLAY_PEAK_BYTES

    solo = ac.replay_threads_budget(skip_make_test=True, ncores=8, total_bytes=MACHINE_36_GB)
    beside = ac.replay_threads_budget(ncores=8, total_bytes=MACHINE_36_GB)
    assert 1 <= beside <= solo <= len(SETTLEMENT_CONFIGS)
    assert ac.replay_threads_budget(ncores=2, total_bytes=MACHINE_44_GB) == 2
    narrow_machine = 8_000_000_000 + 3 * REPLAY_PEAK_BYTES + 300_000_000
    assert ac.replay_threads_budget(skip_make_test=True, ncores=8, total_bytes=narrow_machine) == 3
    assert ac.replay_threads_budget(ncores=8, total_bytes=narrow_machine) == 2


def test_every_configuration_replays_in_one_wave_with_the_test_gates_running_on_both_fleet_machines():
    """`REPLAY_PEAK_BYTES` is chosen so that every settlement configuration replays in one round even with gate:make-test's pytest pool subtracted. On both 48 GiB machines, at eighteen and at twelve cores, the gated and skipped-gate widths both equal the configuration count. This test checks the gated width, which only the cycle computes."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS

    for ncores in (18, 12):
        gated = ac.replay_threads_budget(ncores=ncores, total_bytes=MACHINE_48_GIB)
        assert gated == len(SETTLEMENT_CONFIGS)
        assert (
            ac.replay_threads_budget(skip_make_test=True, ncores=ncores, total_bytes=MACHINE_48_GIB) == gated
        )
        assert ac.replay_threads_derivation(ncores=ncores, total_bytes=MACHINE_48_GIB).endswith(
            f"capped at {gated}"
        )


def test_replay_threads_budget_cuts_a_stated_width_only_to_the_cap(monkeypatch):
    """`AMS_REPLAY_THREADS` overrides the memory arithmetic, as `AMS_KERNEL_THREADS` does for the kernel width. The budget only applies the cap that the crate and `run_m1._replay_threads` would apply anyway: a stated width above the configuration count is cut to it, and one below passes through."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS

    monkeypatch.setenv("AMS_REPLAY_THREADS", "2")
    assert ac.replay_threads_budget(ncores=8, total_bytes=MACHINE_44_GB) == 2
    monkeypatch.setenv("AMS_REPLAY_THREADS", "99")
    assert ac.replay_threads_budget(ncores=8, total_bytes=MACHINE_44_GB) == len(SETTLEMENT_CONFIGS)


def test_the_replay_plan_line_explains_the_width_it_prints_on_every_route(monkeypatch):
    """The clause printed beside the replay width always explains the width actually used. With no override it is `describe_fit` over the budget's terms. Under `AMS_REPLAY_THREADS` it names the variable and says whether the cap or the floor changed the value. The wave clause counts the rounds that width makes of the configuration count. The rendered plan line carries the same clause."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import REPLAY_PEAK_BYTES

    count = len(SETTLEMENT_CONFIGS)
    derived = ac.replay_threads_derivation(ncores=10, total_bytes=MACHINE_32_GIB)
    assert derived.startswith(f"every settlement configuration in one wave; {count} at ")
    assert "AMS_REPLAY_THREADS" not in derived
    narrow_machine = 8_000_000_000 + 3 * REPLAY_PEAK_BYTES + 300_000_000
    narrow = ac.replay_threads_derivation(skip_make_test=True, ncores=8, total_bytes=narrow_machine)
    assert narrow.startswith(f"2 waves over {count} settlement configurations; 3 at ")
    monkeypatch.setenv("AMS_REPLAY_THREADS", "2")
    stated = ac.replay_threads_derivation(ncores=10, total_bytes=MACHINE_32_GIB)
    assert stated == f"3 waves over {count} settlement configurations; AMS_REPLAY_THREADS states 2"
    assert " at " not in stated
    text = _plan_text(_plan(ncores=10, total_bytes=MACHINE_32_GIB))
    assert f"run_m1 --replay-threads          : 2  (the string replay's own ceiling, {stated})" in text
    monkeypatch.setenv("AMS_REPLAY_THREADS", "99")
    assert (
        ac.replay_threads_derivation(ncores=10, total_bytes=MACHINE_32_GIB)
        == f"every settlement configuration in one wave; AMS_REPLAY_THREADS states 99, cut to the cap of {count}"
    )
    monkeypatch.setenv("AMS_REPLAY_THREADS", "0")
    assert (
        ac.replay_threads_derivation(ncores=10, total_bytes=MACHINE_32_GIB)
        == f"{count} waves over {count} settlement configurations; AMS_REPLAY_THREADS states 0, floored at one"
    )


def test_kernel_threads_budget_never_narrows_a_stated_kernel_width(monkeypatch):
    """`AMS_KERNEL_THREADS` is set to keep a build out of swap, so it overrides every derivation here, including the memory set aside for the pytest pool. The budget still caps it at the configuration count and the cores, as `run_m1._table_build_threads` does, so a stated width at or below that cap passes through unchanged."""
    monkeypatch.setenv("AMS_KERNEL_THREADS", "5")
    assert ac.kernel_threads_budget(ncores=8, total_bytes=MACHINE_44_GB) == 5
    assert ac.kernel_threads_budget(skip_make_test=True, ncores=8, total_bytes=MACHINE_44_GB) == 5


def test_kernel_threads_budget_holds_its_answer_at_the_configuration_count_and_the_cores(monkeypatch):
    """The table build's width is capped at the configuration count and the cores, like the replay width. On the 48 GiB machines the memory arithmetic reaches the configuration count, which is as wide as it goes, so the cores bind only on a machine with fewer of them, and a stated `AMS_KERNEL_THREADS` can exceed the count. The test checks that the cores bind when they are smaller, that the rendered line mentions the cap, and that a stated `AMS_KERNEL_THREADS` above the configuration count is cut to it."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import kernel_threads_default

    monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
    count = len(SETTLEMENT_CONFIGS)
    assert kernel_threads_default(configs=count, total_bytes=MACHINE_48_GIB) == count
    assert ac.kernel_threads_budget(ncores=18, total_bytes=MACHINE_48_GIB) == count
    assert ac.kernel_threads_budget(skip_make_test=True, ncores=12, total_bytes=MACHINE_48_GIB) == count
    assert ac.kernel_threads_budget(ncores=2, total_bytes=MACHINE_48_GIB) == 2
    plan = _plan(ncores=18, total_bytes=MACHINE_48_GIB)
    assert plan.kernel_threads == count
    assert (
        f"run_m1 --kernel-threads          : {count}  (the table build's memory ceiling, less gate:make-test's {plan.make_test_workers} workers, capped at the configuration count and the cores)"
        in _plan_text(plan)
    )
    monkeypatch.setenv("AMS_KERNEL_THREADS", "99")
    assert ac.kernel_threads_budget(ncores=18, total_bytes=MACHINE_48_GIB) == count


def test_a_plan_reserves_for_the_pytest_pool_only_when_that_gate_runs():
    """The plan subtracts the pytest pool only when gate:make-test runs. When the gate is auto-skipped or `--skip-gates` is given, no pool runs, so the kernel width gets that memory back."""
    assert _plan(ncores=8, total_bytes=MACHINE_38_GB).kernel_threads == 3
    assert (
        _plan(
            ncores=8, total_bytes=MACHINE_38_GB, skip_make_test=True, make_test_note="closure unchanged"
        ).kernel_threads
        == 4
    )
    assert _plan(ncores=8, total_bytes=MACHINE_38_GB, skip_gates=True).kernel_threads == 4


def test_dry_run_renders_concurrency():
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=MACHINE_44_GB,
    )
    text = _plan_text(plan)
    assert "pool policy: overlap" in text
    assert "Lane t0" in text
    assert "Lane build" in text
    assert "Lane rebuild-contracts" in text
    assert "Lane conform" in text
    assert "Lane kernel" not in text
    assert (
        "run_m1 -> submit gate:rebuild-contracts -> corpus-seed -> corpus-build -> store-snapshot -> verdict-update -> land -> review-facts"
        in text
    )
    assert "CO-RESIDENT with gate:make-test's pool and gate:rebuild-contracts' pool (overlap policy)" in text
    assert (
        f"Lane rebuild-contracts           : submitted beside the corpus build, -n {plan.contracts_workers} ({plan.contracts_reason});"
        in text
    )
    assert "CO-RESIDENT with gate:make-test's pool and gate:conform's sweep (overlap policy)" in text
    queued = _plan_text(_plan(pool_policy="queue", ncores=12))
    assert "pool policy: queue" in queued
    assert "QUEUED behind gate:make-test (queue policy — one heavy pool at a time)" in queued
    assert "QUEUED behind gate:conform (queue policy — one heavy pool at a time)" in queued
    assert f"run_m1 sweeps --jobs             : {plan.sweep_jobs}" in text
    assert plan.sweep_jobs == ac.sweep_job_budget(12, total_bytes=MACHINE_44_GB)
    assert "run_m1 --kernel-threads          : " in text
    assert (
        f"run_m1 --replay-threads          : {plan.replay_threads}  (the string replay's own ceiling" in text
    )
    assert plan.replay_threads == ac.replay_threads_budget(ncores=12, total_bytes=MACHINE_44_GB)
    assert ac.replay_threads_derivation(ncores=12, total_bytes=MACHINE_44_GB) in text
    auto_skipped = _plan_text(_plan(skip_conform=True, conform_note=ac.CONFORM_SKIP_NOTE))
    assert f"Lane conform                     : SKIPPED ({ac.CONFORM_SKIP_NOTE})" in auto_skipped
    assert "Lane conform                     : SKIPPED (--skip-conform)" in _plan_text(
        _plan(skip_conform=True)
    )
    corpus_width = ac.corpus_job_budget(skip_gates=False, ncores=12, total_bytes=MACHINE_44_GB)
    assert f"corpus-build --jobs              : {corpus_width}" in text
    _per_unit, coresident, _cap = ac._corpus_fit_terms(skip_gates=False, skip_make_test=False, ncores=12)
    assert f"less {format_gb(coresident)} GB co-resident" in text

    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[1:6] == ["run", "python", "-m", "rebuild.pipeline.run_m1", "--jobs"]
    # Every width is passed on the command line, including a width of one. Without the flag the child would use its own default, which does not subtract the pytest pool and can differ from the planned width.
    assert _argv(by_name["corpus-build"])[-4:-2] == ["--jobs", str(corpus_width)]


def test_dry_run_skip_gates_appends_jobs_budgets():
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        skip_gates=True,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=MACHINE_44_GB,
    )
    by_name = {step.name: step for step in plan.steps}
    solo_width = ac.corpus_job_budget(skip_gates=True, ncores=12, total_bytes=MACHINE_44_GB)
    assert plan.sweep_jobs == ac.sweep_job_budget(12, total_bytes=MACHINE_44_GB)
    assert _argv(by_name["run_m1"])[5:7] == ["--jobs", str(plan.sweep_jobs)]
    assert _argv(by_name["corpus-build"])[-4:-2] == ["--jobs", str(solo_width)]
    assert f"run_m1 sweeps --jobs {plan.sweep_jobs}" in _plan_text(plan)
    assert f"corpus-build --jobs {solo_width}" in _plan_text(plan)

    default_plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=MACHINE_44_GB,
    )
    default_by_name = {step.name: step for step in default_plan.steps}
    gated_width = ac.corpus_job_budget(skip_gates=False, ncores=12, total_bytes=MACHINE_44_GB)
    assert _argv(default_by_name["run_m1"])[5:7] == ["--jobs", str(default_plan.sweep_jobs)]
    assert default_plan.sweep_jobs == plan.sweep_jobs
    assert _argv(default_by_name["corpus-build"])[-4:-2] == ["--jobs", str(gated_width)]


def test_review_out_staging_plan(monkeypatch, tmp_path):
    staged_out = tmp_path / "staged"
    plan = ac.build_plan(
        verdicts=Path("v.json"),
        no_carry=False,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        review_out=staged_out,
    )
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["corpus-build"])[5:7] == ["--out", str(staged_out)]
    assert by_name["review-facts"].argv is None
    assert by_name["review-facts"].note == "SKIPPED (staging: the checked-in pins track the live corpus)"
    argv = _argv(by_name["verdict-update"])
    assert argv[argv.index("--corpus") + 1] == str(staged_out)
    assert plan.corpus_dir == staged_out
    assert plan.review_out == staged_out

    monkeypatch.setattr(ac, "server_listening", lambda *a, **k: True)
    waiver = argparse.Namespace(review_out=staged_out, yes=False, stop_server=False)
    assert ac._preflight(waiver) is True
    refuse = argparse.Namespace(review_out=None, yes=False, stop_server=False)
    assert ac._preflight(refuse) is False


def _green_report():
    report = ac.CycleReport()
    report.gate_js = "green"
    report.gate_js_green = True
    report.gate_contracts = "green"
    report.gate_contracts_green = True
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
        "conform",
        "make_test",
    }
    assert all(gate["green"] is True for gate in payload["gates"].values())
    assert payload["finished_at"].endswith("Z")


def test_cycle_summary_payload_green_follows_the_boolean_not_the_status_prose():
    """The payload's `green` comes from the gate's recorded boolean, not its status text. An annotated green status stays green, and a status that reads "green" does not make `green` true."""
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
    report.gate_make_test = "skipped (closure unchanged)"
    report.gate_make_test_green = None
    plan = _plan(
        skip_conform=True,
        conform_proven=True,
        skip_contracts=True,
        skip_make_test=True,
    )
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["conform"]["skip"] == "proved"
    assert payload["gates"]["rebuild_contracts"]["skip"] == "proved"
    assert payload["gates"]["make_test"]["skip"] == "proved"
    assert payload["gates"]["js"]["skip"] is None


def test_cycle_summary_payload_failures_exit_failed():
    payload = ac.cycle_summary_payload(_green_report(), ["make test failed"], _plan(), "failed")
    assert payload["exit"] == "failed"
    assert payload["failures"] == ["make test failed"]


@pytest.mark.parametrize("planned_skip", [False, True])
@pytest.mark.parametrize("runtime", ["green", "red", "skipped"])
def test_contracts_summary_tracks_the_runtime_decision(planned_skip, runtime):
    from concurrent.futures import Future

    plan = _plan(skip_contracts=planned_skip, contracts_note="input closure unchanged")
    report = ac.CycleReport()
    gate = (
        ct.CheckResult(
            check="rebuild-contracts",
            outcome="skipped",
            status="skipped (input closure unchanged)",
            failures=[],
            failed_ids=[],
        )
        if runtime == "skipped"
        else ac.classify_rebuild_output("", 0 if runtime == "green" else 1, "rebuild-contracts")
    )
    future = Future()
    future.set_result(gate)
    failures = []
    ac._join_contracts(report, failures, future, ac._Emitter())
    payload = ac.cycle_summary_payload(report, failures, plan, "ok" if gate.ok else "failed")
    entry = payload["gates"]["rebuild_contracts"]
    assert entry["skip"] == ("proved" if runtime == "skipped" else None)
    assert entry["green"] == (runtime == "green")
    assert payload["plan"]["skip_contracts"] == (runtime == "skipped")
    assert payload["plan"]["contracts_workers"] == (None if runtime == "skipped" else plan.contracts_workers)
    row = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}[
        "gate:rebuild-contracts"
    ]
    assert row.outcome == {"green": "ok", "red": "FAILED", "skipped": "skipped"}[runtime]


def test_cycle_summary_payload_plan_block_and_argv():
    plan = _plan()
    payload = ac.cycle_summary_payload(_green_report(), [], plan, "ok")
    assert payload["plan"] == {
        "verdicts": "v.json",
        "carry_out": str(plan.carry_out),
        "do_merge": True,
        "conform_max_length": ac.CONFORM_MAX_LENGTH_DEFAULT,
        "kernel_threads": plan.kernel_threads,
        "overlap_memo_writes": plan.overlap_memo_writes,
        "scratch_beside_default": plan.scratch_beside_default,
        "replay_threads": plan.replay_threads,
        "sweep_jobs": plan.sweep_jobs,
        "corpus_jobs": plan.corpus_jobs,
        "corpus_gates_idle_jobs": plan.corpus_gates_idle_jobs,
        "signature_jobs": plan.signature_jobs,
        "standing_fill_jobs": plan.standing_fill_jobs,
        "make_test_workers": plan.make_test_workers,
        "contracts_workers": plan.contracts_workers,
        "conform_jobs": plan.conform_jobs,
        "pool_policy": ac.REBUILD_POOL_POLICY_DEFAULT,
        "skip_gates": False,
        "skip_conform": False,
        "skip_run_m1": False,
        "rerun_gates_only": False,
        "skip_corpus": False,
        "refresh_assets": False,
        "promote_corpus": None,
        "next_corpus": str(ac.next_corpus_dir()),
        "land": True,
        "skip_contracts": False,
        "skip_verdict_update": False,
        "review_out": None,
        "first_run": False,
        "short_id": "testid",
    }
    assert payload["argv"] == list(sys.argv)
    assert payload["assets_status"] == "not run"
    assert payload["land_status"] == "not run"
    assert payload["land"] is None


def test_cycle_summary_payload_records_an_assets_refresh():
    """The summary records the assets refresh as its own step, so a pass that skipped the corpus build can be told apart from one that copied new app assets over the served corpus."""
    report = _green_report()
    report.assets_status = "refreshed in place (units, sidecars and generated_at unmoved)"
    payload = ac.cycle_summary_payload(report, [], _plan(skip_corpus=True, refresh_assets=True), "ok")
    assert payload["plan"]["refresh_assets"] is True
    assert payload["assets_status"].startswith("refreshed in place")


def test_cycle_summary_payload_names_the_gates_only_rerun_and_passes_no_kernel_width_to_it():
    """The summary must tell the three run_m1 modes apart. A gates-only rerun is not a skip, and the thread widths it reports are the ones passed to the child, which for a gates-only rerun are none."""
    plan = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    payload = ac.cycle_summary_payload(_green_report(), [], plan, "ok")
    assert payload["plan"]["rerun_gates_only"] is True
    assert payload["plan"]["skip_run_m1"] is False
    assert payload["plan"]["kernel_threads"] is None
    assert payload["plan"]["overlap_memo_writes"] is None
    assert payload["plan"]["scratch_beside_default"] is None
    assert payload["plan"]["replay_threads"] is None


def test_cycle_summary_payload_records_null_for_the_width_of_each_step_the_pass_skips():
    """A width is recorded only for a step that spawns at it, so a run line never names a width nothing ran at, including gate:conform's when its green record skips it after run_m1. The gates-only rerun still runs the oracle at the sweep width, so that width stays."""
    widths = (
        "sweep_jobs",
        "corpus_jobs",
        "corpus_gates_idle_jobs",
        "signature_jobs",
        "standing_fill_jobs",
        "make_test_workers",
        "contracts_workers",
        "conform_jobs",
    )
    skipped = _plan(
        skip_run_m1=True,
        skip_corpus=True,
        skip_verdict_update=True,
        skip_make_test=True,
        skip_contracts=True,
        skip_conform=True,
    )
    skipped_report = _green_report()
    skipped_report.gate_contracts = "skipped (input closure unchanged)"
    skipped_report.gate_contracts_green = None
    skipped_report.gate_make_test = "skipped (closure unchanged)"
    skipped_report.gate_make_test_green = None
    skipped_report.gate_conform = "skipped (--skip-conform)"
    skipped_report.gate_conform_green = None
    assert {
        key: ac.cycle_summary_payload(skipped_report, [], skipped, "ok")["plan"][key] for key in widths
    } == dict.fromkeys(widths)
    gates_off = ac.cycle_summary_payload(ac.CycleReport(), [], _plan(skip_gates=True), "ok")["plan"]
    assert (gates_off["make_test_workers"], gates_off["contracts_workers"], gates_off["conform_jobs"]) == (
        None,
        None,
        None,
    )
    assert gates_off["corpus_jobs"] is not None
    assert gates_off["corpus_gates_idle_jobs"] is None
    proved = _green_report()
    proved.conform_proven = True
    assert ac.cycle_summary_payload(proved, [], _plan(), "ok")["plan"]["conform_jobs"] is None
    rerun = _plan(rerun_gates_only=True)
    assert (
        ac.cycle_summary_payload(_green_report(), [], rerun, "ok")["plan"]["sweep_jobs"] == rerun.sweep_jobs
    )


def test_write_cycle_summary_reads_module_attr_at_call_time(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere" / "cycle_summary.json"
    monkeypatch.setattr(cycle_paths, "CYCLE_SUMMARY", target)
    ac.write_cycle_summary({"format": "ams-cycle-summary/1"})
    assert json.loads(target.read_text()) == {"format": "ams-cycle-summary/1"}
    assert not list(target.parent.glob("*.tmp"))


def test_cycle_writes_green_summary_with_corpus(monkeypatch, tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T12:00:00Z", "inputs_fingerprint": {"runes": "abc123"}})
    )

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=corpus_dir)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    summary = json.loads(cycle_paths.CYCLE_SUMMARY.read_text())
    assert summary["format"] == "ams-cycle-summary/1"
    assert summary["exit"] == "ok"
    assert all(gate["green"] is True for gate in summary["gates"].values())
    assert summary["corpus"]["dir"] == str(corpus_dir)
    assert summary["corpus"]["generated_at"] == "2026-07-17T12:00:00Z"
    assert summary["corpus"]["inputs_fingerprint"] == {"runes": "abc123"}


def test_cycle_writes_failed_summary_on_run_m1_failure(monkeypatch, tmp_path):
    def fake_run_m1(report, *, spawn, emit, registry, **_):
        return None

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=tmp_path / "corpus")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    summary = json.loads(cycle_paths.CYCLE_SUMMARY.read_text())
    assert summary["exit"] == "failed"
    assert summary["failures"]


def test_cycle_writes_interrupted_summary(monkeypatch, tmp_path):
    def boom(report, *, spawn, emit, registry, **_):
        raise KeyboardInterrupt

    monkeypatch.setattr(ac, "_do_run_m1", boom)

    plan = _plan(skip_gates=True, review_out=tmp_path / "corpus")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry())

    assert rc == 130
    summary = json.loads(cycle_paths.CYCLE_SUMMARY.read_text())
    assert summary["exit"] == "interrupted"
    assert summary["interrupted"] is True


def test_cycle_summary_corpus_nulls_when_manifest_missing(monkeypatch, tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(review_out=corpus_dir)
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    summary = json.loads(cycle_paths.CYCLE_SUMMARY.read_text())
    assert summary["corpus"]["dir"] == str(corpus_dir)
    assert summary["corpus"]["generated_at"] is None
    assert summary["corpus"]["inputs_fingerprint"] is None


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
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", tmp_path / "rebuild" / "out" / "run-m1-green.json")
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", tmp_path / "rebuild" / "out" / "conform-green.json")
    monkeypatch.setattr(
        cycle_paths, "REBUILD_CONTRACTS_GREEN", tmp_path / "rebuild" / "out" / "rebuild-contracts-green.json"
    )


def test_dry_run_auto_resolves_the_carry_source(tmp_path, monkeypatch, capsys):
    _seed_auto_repo(tmp_path, monkeypatch)
    (tmp_path / "verdicts-autosave.json").write_text(
        json.dumps(_verdicts_doc("2026-07-17T20:24:44Z", ["u-1", "u-2"]))
    )
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Auto-resolved carry source: verdicts-autosave.json (2 effective verdicts" in out
    assert "stamped for the served corpus" in out
    assert str(tmp_path / "verdicts-autosave.json") in out


def test_auto_resolution_carries_a_mismatched_stamp_by_unit_id(tmp_path, monkeypatch, capsys):
    """When no candidate is stamped for the served corpus, the file with the newest stamp is still carried, and the line names it as stamped for an older corpus. A verdict names its unit by content id, so it reaches the unit with that id or none, and a stale stamp cannot put it on the wrong window."""
    _seed_auto_repo(tmp_path, monkeypatch)
    (tmp_path / "verdicts-carried-old.json").write_text(
        json.dumps(_verdicts_doc("2026-07-10T00:00:00Z", ["u-1"]))
    )
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert (
        "Auto-resolved carry source: verdicts-carried-old.json (1 effective verdicts, stamped 2026-07-10T00:00:00Z, an older corpus"
        in out
    )
    assert "land by unit id" in out
    assert "ERROR" not in out


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
        "var/build-logs/latest/plan.txt",
        ".claude/settings.json",
        "rebuild/tools/scaling_sweep.py",
        "rebuild/scaling-series.txt",
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
    (tmp_path / ".gitignore").write_text("tmp/\nvar/\n")
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
    """The Makefile is not hashed as a file. It enters the fingerprint as one `make -n` probe line per rule the suite runs (`all` and `test`), so a comment or an unrelated target does not make the gate due."""
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
    """Editing either rule the suite runs, `all` or `test`, changes the key."""
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
    """A comment, a target that `make test` does not run, and a new rule all leave the key unchanged, so edits to the cycle and kernel targets do not re-run the font suite."""
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
    """stderr and the return code are hashed into the probe's digest, so a Makefile that no longer parses changes the key instead of hashing as an empty recipe, and nothing raises."""
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    (root / "Makefile").write_text("all:\n\techo build\nfoo bar baz\n")
    broken = ac.make_test_closure_fingerprint(root)
    assert broken is not None
    assert broken != first


def test_recipe_probe_is_blind_to_the_callers_overrides(tmp_path, monkeypatch):
    """`make test FORCE=1` reaches the probe two ways: through MAKEFLAGS, which a sub-make reads as its own command line, and as an exported FORCE variable. Both the cycle's gate and `make test`'s wrapper run the probe as sub-makes. The probe must print the same recipe for a forced caller as for a plain one. Otherwise a forced green run would record a key no plain run matches, so the next plain run would rerun the whole suite, and a forced red run would leave in place the green record it had just contradicted."""
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
    """`MAKE_TEST_RECIPE_PINS` must pin every variable the repository's real `all` and `test` rules read, not only the one the fake Makefile uses. The test runs the probe on the live Makefile with FORCE=1 in the environment and expects the same two probe lines as without it."""
    monkeypatch.delenv("FORCE", raising=False)
    plain = ac.make_test_recipe_lines(ac.ROOT)
    assert plain is not None
    monkeypatch.setenv("FORCE", "1")
    assert ac.make_test_recipe_lines(ac.ROOT) == plain


def test_closure_fingerprint_is_none_when_make_is_unavailable(tmp_path, monkeypatch):
    """Without make, as without git, there is no fingerprint, so the caller runs the gate unconditionally."""
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
    """The cycle summary keeps a copy of the fingerprint for display, but the skip decision reads only the green record. After `clear_contradicted_green` deletes the record, reading the summary's copy would restore a green whose last run was red."""
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
    """With gate:make-test skipped, each gate lane names only the heavy pools that run beside it: under the default overlap policy the sweep and the suite are each other's only neighbor, and with one of them skipped too the other has none. The queue policy's lines say nothing is queued behind gate:make-test."""
    skipped: dict[str, Any] = dict(
        skip_make_test=True, make_test_note="closure unchanged since its last green run"
    )
    plan = _plan(**skipped)
    assert plan.pool_policy == "overlap"
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:make-test"].argv is None
    assert by_name["gate:make-test"].note == "SKIPPED (closure unchanged since its last green run)"
    assert by_name["gate:rebuild-contracts"].argv is not None
    rendered = _plan_text(plan)
    assert "Lane t0   [from t=0, background]  : gate:js" in rendered
    assert "CO-RESIDENT with gate:rebuild-contracts' pool (overlap policy)" in _lane_conform_line(plan)
    assert "CO-RESIDENT with gate:conform's sweep (overlap policy)" in rendered
    alone = _plan_text(_plan(**skipped, skip_conform=True))
    assert "CO-RESIDENT" not in alone
    assert "no other heavy gate pool runs this pass" in alone
    queued = _plan_text(_plan(**skipped, pool_policy="queue"))
    assert "gate:make-test not running, so no queueing" in queued
    assert "QUEUED behind gate:conform (queue policy — one heavy pool at a time)" in queued


def _make_test_gate_args(argv: list[str]) -> list[str]:
    """Return the arguments the live Makefile passes to `rebuild.tools.make_test_gate` for one gate:make-test argv, read from the `make -n` output after the module name. MAKEFLAGS, MFLAGS and FORCE are removed from the environment first, so a suite run under `make test-rebuild FORCE=1` gets the same answer as a plain one."""
    assert argv[:2] == ["make", "test"]
    env = {key: value for key, value in os.environ.items() if key not in ("MAKEFLAGS", "MFLAGS", "FORCE")}
    printed = subprocess.run(
        ["make", "-n", *argv[1:]], cwd=REPO_ROOT, capture_output=True, text=True, check=True, env=env
    ).stdout
    (recipe,) = [line for line in printed.splitlines() if "rebuild.tools.make_test_gate" in line]
    tokens = shlex.split(recipe)
    return tokens[tokens.index("rebuild.tools.make_test_gate") + 1 :]


def _planned(argv: list[str]) -> ac.Plan:
    """Return the plan `main --dry-run` resolves for one command line, captured as it is passed to `render_plan`."""
    seen: list[ac.Plan] = []
    real = ac.render_plan
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ac, "render_plan", lambda plan: seen.append(plan) or real(plan))
        assert ac.main(["--dry-run", *argv]) == 0
    (plan,) = seen
    return plan


def _font_suite_stub(monkeypatch, fingerprint: str) -> list[list[str]]:
    """Patch `make_test_gate` so its closure fingerprints as `fingerprint` and its suite spawn is recorded instead of run, and return the list of recorded argvs. The patch replaces `run` on the shared `subprocess` module, so call this only after the test's real spawns."""
    spawned: list[list[str]] = []
    monkeypatch.setattr(mtg, "make_test_closure_fingerprint", lambda root: fingerprint)
    monkeypatch.setattr(
        mtg.subprocess,
        "run",
        lambda argv, cwd, env=None: spawned.append(argv) or SimpleNamespace(returncode=0),
    )
    return spawned


@pytest.mark.parametrize("flag", ["--fresh", "--force-make-test"])
def test_a_forced_pass_hands_the_make_test_wrapper_force(tmp_path, monkeypatch, flag):
    """The wrapper behind `make test` reads the green record itself and exits early when the closure matches it. So `--fresh` and `--force-make-test` must reach the wrapper, not only the plan: the argv carries FORCE=1, the live Makefile turns it into `--force`, and the wrapper runs the suite. A plain pass over the same closure plans the gate as skipped."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "make_test_closure_fingerprint", lambda root=None: "fp")
    ac.record_make_test_green("fp")

    bare = {step.name: step for step in _planned([]).steps}["gate:make-test"]
    assert bare.skipped
    assert bare.argv is None

    forced = {step.name: step for step in _planned([flag]).steps}["gate:make-test"]
    assert not forced.skipped
    assert forced.argv == ["make", "test", "FORCE=1"]
    wrapper = _make_test_gate_args(_argv(forced))
    assert "--force" in wrapper

    spawned = _font_suite_stub(monkeypatch, "fp")
    assert mtg.main(wrapper) == 0
    assert spawned == [mtg.PYTEST_ARGV]


@pytest.mark.parametrize("flags", [[], ["--fresh"], ["--force-make-test"]])
@pytest.mark.parametrize("recorded", ["fp", "older", None])
def test_the_plan_reserves_make_tests_pool_exactly_when_the_wrapper_runs_it(
    tmp_path, monkeypatch, flags, recorded
):
    """The plan sets the corpus build's widths before `make test` decides anything, so the memory set aside for `make test` is correct only if the plan and the wrapper make the same skip decision. Memory reserved for a gate that then skips on its own green record is lost to the build. Every combination of forcing flag and green record ends one of two ways. Either the plan skips the gate, the wrapper would also skip over the same fingerprint and record, and the build gets the pool's memory back. Or the plan runs the gate, the argv the live Makefile passes to the wrapper runs the suite, and its pool is subtracted from the build's widths."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "make_test_closure_fingerprint", lambda root=None: "fp")
    if recorded is not None:
        ac.record_make_test_green(recorded)

    plan = _planned(flags)
    step = {step.name: step for step in plan.steps}["gate:make-test"]
    runs = not step.skipped
    assert runs is (bool(flags) or recorded != "fp")

    wrapper = _make_test_gate_args(_argv(step) if runs else ["make", "test"])
    spawned = _font_suite_stub(monkeypatch, "fp")
    assert mtg.main(wrapper) == 0
    assert spawned == ([mtg.PYTEST_ARGV] if runs else [])

    assert plan.corpus_jobs == ac.corpus_job_budget(skip_gates=False, skip_make_test=not runs)
    assert plan.signature_jobs == ac.signature_job_budget()
    assert plan.kernel_threads == ac.kernel_threads_budget(skip_make_test=not runs)
    rendered = _plan_text(plan)
    assert ("holds the gate lane and its bytes come off the machine's memory" in rendered) is runs
    assert ("gate:make-test skipped, so the other gates hold the gate lane" in rendered) is not runs


def test_the_signature_pool_takes_the_cores_the_corpus_width_cannot():
    """The ink-signature width is the one fan-out in the plan that memory does not derive: a signature worker holds one comparator, and no `*_BYTES` constant covers it. It is the whole machine whether gate:make-test runs, skips, or the gates are skipped, since that pool shares the cores while both run, so on a ten-core machine it is ten on every pass, above the corpus build's cap, which applies only to that build's unit workers. On a machine with a quarter of the memory, the reserve and the parent's co-resident memory exceed the total, so the corpus width floors at one while the signature width stays at ten. The argv passes the width after `--jobs`, and the plan shows it on its own row with its derivation."""
    derivation = "10 of 10 cores, the whole machine, shared with gate:make-test's pool on a cycle pass that runs that gate"
    assert ac.signature_job_budget(ncores=10) == 10
    assert ac.signature_job_derivation(ncores=10) == derivation

    gated = _plan(skip_make_test=False, ncores=10, total_bytes=MACHINE_32_GIB)
    assert gated.signature_jobs == 10 > gated.corpus_jobs == 4
    narrow = _plan(skip_make_test=False, ncores=10, total_bytes=MACHINE_32_GIB // 4)
    assert narrow.signature_jobs == 10 > narrow.corpus_jobs == 1
    gated_by_name = {step.name: step for step in gated.steps}
    assert _argv(gated_by_name["corpus-build"])[-4:] == [
        "--jobs",
        str(gated.corpus_jobs),
        "--signature-jobs",
        "10",
    ]
    rendered = _plan_text(gated)
    assert "    corpus-build --signature-jobs    : 10  (" in rendered
    assert derivation in rendered

    solo = _plan(
        skip_make_test=True, make_test_note="closure unchanged", ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert solo.signature_jobs == 10 > solo.corpus_jobs
    assert _argv({step.name: step for step in solo.steps}["corpus-build"])[-2:] == ["--signature-jobs", "10"]
    assert derivation in _plan_text(solo)

    skipped = _plan(skip_gates=True, ncores=10, total_bytes=MACHINE_32_GIB)
    assert skipped.signature_jobs == 10
    assert "corpus-build --signature-jobs 10 (" in _plan_text(skipped)

    assert ac.signature_job_budget(ncores=1) == 1
    assert ac.signature_job_derivation(ncores=1) == (
        "1 of 1 cores, the whole machine, shared with gate:make-test's pool on a cycle pass that runs that gate"
    )


def test_the_contracts_pool_is_the_cores_the_corpus_build_leaves():
    """The rebuild suite's width under a cycle is the second fan-out that memory does not derive. It runs beside the corpus build, so it gets the cores less the build's parent and its `corpus_job_budget` workers. Under the overlap policy it also loses gate:make-test's pool. Under the queue policy the suite waits until that pool finishes, so nothing is subtracted for it. With no corpus build it gets every core under the queue policy and every core less gate:make-test's pool under the overlap policy, and it never drops below one. Widths that depend on the corpus constants are computed from the budget functions, so re-measuring those constants does not require editing this test."""
    corpus = ac.corpus_job_budget(skip_gates=False, ncores=10, total_bytes=MACHINE_32_GIB)
    queue = ac.contracts_pool_width(
        skip_gates=False, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert queue == 10 - 1 - corpus >= 1
    assert f"{queue} of 10 cores, less the corpus build's parent and its {corpus} workers" == (
        ac.contracts_pool_derivation(
            skip_gates=False, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
        )
    )

    overlap = ac.contracts_pool_width(
        skip_gates=False, pool_policy="overlap", ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert overlap == max(1, queue - ac.make_test_pool_width(ncores=10))
    assert (
        f"less gate:make-test's {ac.make_test_pool_width(ncores=10)} (overlap policy)"
        in ac.contracts_pool_derivation(
            skip_gates=False, pool_policy="overlap", ncores=10, total_bytes=MACHINE_32_GIB
        )
    )

    solo = ac.contracts_pool_width(
        skip_gates=False, skip_make_test=True, ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert solo == 10 - 1 - ac.corpus_job_budget(
        skip_gates=False, skip_make_test=True, ncores=10, total_bytes=MACHINE_32_GIB
    )

    assert (
        ac.contracts_pool_width(
            skip_gates=False, skip_corpus=True, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
        )
        == 10
    )
    assert (
        ac.contracts_pool_derivation(
            skip_gates=False, skip_corpus=True, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
        )
        == "10 of 10 cores, the whole machine (no corpus build to share it with)"
    )
    solo_overlap = ac.contracts_pool_width(
        skip_gates=False, skip_corpus=True, pool_policy="overlap", ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert solo_overlap == 10 - ac.make_test_pool_width(ncores=10)
    assert ac.contracts_pool_derivation(
        skip_gates=False, skip_corpus=True, pool_policy="overlap", ncores=10, total_bytes=MACHINE_32_GIB
    ) == (
        f"{solo_overlap} of 10 cores, the machine (no corpus build to share it with), "
        f"less gate:make-test's {ac.make_test_pool_width(ncores=10)} (overlap policy)"
    )

    assert ac.contracts_pool_width(skip_gates=False, ncores=2, total_bytes=MACHINE_32_GIB) == 1
    assert ac.contracts_pool_derivation(skip_gates=False, ncores=2, total_bytes=MACHINE_32_GIB).endswith(
        "floored at one"
    )


def test_a_stated_contracts_width_is_the_width_the_cycle_hands_the_child(monkeypatch):
    """The rebuild suite's child inherits this process's environment, as gate:make-test's does, so a width already set in PYTEST_XDIST_AUTO_NUM_WORKERS is the width that pool takes. The plan reports that width instead of printing arithmetic the pool would ignore."""
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "9")
    assert ac.contracts_pool_width(skip_gates=False, ncores=2, total_bytes=MACHINE_32_GIB) == 9
    assert (
        ac.contracts_pool_derivation(skip_gates=False, ncores=2, total_bytes=MACHINE_32_GIB)
        == "PYTEST_XDIST_AUTO_NUM_WORKERS states 9"
    )
    plan = _plan(ncores=2, total_bytes=MACHINE_32_GIB)
    assert plan.contracts_workers == 9


def test_the_plan_states_the_contracts_pool_width_on_its_lane_line():
    """The lane line shows the suite's width and its derivation, and the build lane line shows the suite submitted before the corpus build. Beside a corpus build at its lane share, the queue policy gives the suite the gate lane's share, five workers on a ten-core machine and six on a twelve-core one. Under the overlap policy gate:make-test's pool already holds that share, so the arithmetic leaves none, the floor gives the suite one worker, and the plan says so. When the corpus build is skipped, the plan says the suite is submitted once the run_m1 gate passes, not that it runs beside a build the plan shows as SKIPPED."""
    gated = _plan(pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB)
    text = _plan_text(gated)
    assert (
        "Lane build[serial, main thread]  : run_m1 -> submit gate:rebuild-contracts -> corpus-seed -> corpus-build -> store-snapshot -> verdict-update -> land -> review-facts"
        in text
    )
    assert (
        f"Lane rebuild-contracts           : submitted beside the corpus build, -n {gated.contracts_workers} ({gated.contracts_reason});"
        in text
    )
    assert gated.contracts_workers == ac.contracts_pool_width(
        skip_gates=False, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert {step.name: step for step in gated.steps}["gate:rebuild-contracts"].note == (
        "submitted beside the corpus build"
    )

    overlap = _plan(pool_policy="overlap", ncores=10, total_bytes=MACHINE_32_GIB)
    assert overlap.contracts_workers == 1 < gated.contracts_workers == 5
    assert overlap.contracts_reason.endswith("floored at one")
    assert f"-n {overlap.contracts_workers} (" in _plan_text(overlap)
    assert "CO-RESIDENT with gate:make-test's pool and gate:conform's sweep (overlap policy)" in _plan_text(
        overlap
    )
    roomy = _plan(pool_policy="queue", ncores=12, total_bytes=MACHINE_48_GIB)
    roomy_overlap = _plan(pool_policy="overlap", ncores=12, total_bytes=MACHINE_48_GIB)
    assert roomy_overlap.contracts_workers == 1 < roomy.contracts_workers == 6
    assert roomy_overlap.contracts_reason.endswith("floored at one")
    assert f"-n {roomy_overlap.contracts_workers} (" in _plan_text(roomy_overlap)

    solo = _plan(
        skip_corpus=True, corpus_note="unchanged", pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
    )
    solo_text = _plan_text(solo)
    assert solo.contracts_workers == 10
    assert (
        f"Lane rebuild-contracts           : submitted once the run_m1 gate passes (no corpus build this pass), -n 10 ({solo.contracts_reason});"
        in solo_text
    )
    assert "submitted beside the corpus build" not in solo_text
    assert {step.name: step for step in solo.steps}["gate:rebuild-contracts"].note == (
        "submitted once the run_m1 gate passes (no corpus build this pass)"
    )

    assert "Lane rebuild-contracts" not in _plan_text(
        _plan(skip_gates=True, ncores=10, total_bytes=MACHINE_32_GIB)
    )


def test_skip_make_test_frees_the_corpus_build_budget():
    """gate:make-test does not affect the oracle sweep width, and the corpus build gets the pytest pool's memory back when that pool is not running, but not its cores: the other gates hold the gate lane, so the build keeps the build lane's share. On the 48 GiB machine both cases reach that share less the parent, so the widths are equal; `test_the_pytest_pool_comes_off_the_machine_before_the_division` checks that their terms differ. This test checks that the plan uses each case's own terms and that its reason line says which: the gated derivation includes the pool's memory in its co-resident amount, and the skipped-gate line says no pool's bytes come off. The widths are computed from the budget functions, so re-measuring either corpus constant does not require editing this test."""
    plan = _plan(
        skip_make_test=True,
        make_test_note="closure unchanged since its last green run",
        ncores=10,
        total_bytes=MACHINE_48_GIB,
    )
    solo_width = ac.corpus_job_budget(
        skip_gates=False, skip_make_test=True, ncores=10, total_bytes=MACHINE_48_GIB
    )
    assert plan.corpus_jobs == solo_width
    assert plan.sweep_jobs == ac.sweep_job_budget(10, total_bytes=MACHINE_48_GIB)
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["corpus-build"])[-4:-2] == ["--jobs", str(solo_width)]
    rendered = _plan_text(plan)
    assert f"corpus-build --jobs              : {solo_width}" in rendered
    assert f"less {format_gb(ac.CORPUS_PARENT_BYTES)} GB co-resident" in rendered
    assert (
        "the build lane holds 5 of 10 cores (memory_budget.split_cores), so the corpus build is capped at 5 less its parent; gate:make-test skipped, so the other gates hold the gate lane and no pytest pool's bytes come off the machine's memory"
        in rendered
    )

    gated = _plan(skip_make_test=False, ncores=10, total_bytes=MACHINE_48_GIB)
    gated_width = ac.corpus_job_budget(skip_gates=False, ncores=10, total_bytes=MACHINE_48_GIB)
    assert gated.corpus_jobs == gated_width
    assert gated.sweep_jobs == ac.sweep_job_budget(10, total_bytes=MACHINE_48_GIB)
    gated_by_name = {step.name: step for step in gated.steps}
    assert _argv(gated_by_name["corpus-build"])[-4:-2] == ["--jobs", str(gated_width)]
    _per_unit, gated_coresident, _cap = ac._corpus_fit_terms(
        skip_gates=False, skip_make_test=False, ncores=10
    )
    assert (
        f"corpus-build --jobs              : {gated_width}  (the build lane holds 5 of 10 cores (memory_budget.split_cores), so the corpus build is capped at 5 less its parent; gate:make-test's pool, 5 workers, holds the gate lane and its bytes come off the machine's memory beside the build's own parent; "
        f"{gated_width} at {format_gb(ac.CORPUS_WORKER_BYTES)} GB each out of 51.54 GB total, less a reserve of 8.00 GB, less {format_gb(gated_coresident)} GB co-resident, capped at 4)"
        in _plan_text(gated)
    )


def test_a_gated_corpus_build_is_offered_every_core_less_its_parent_for_an_idle_gate_lane():
    """A gated pass passes the corpus build two widths, the build lane's share less the parent and a hand build's every core less the parent, with the marker file that picks between them when the units pool starts, and the plan prints both widths and the condition. On the 18-core 48 GiB machine memory binds neither, so the two are the caps. `--skip-gates` already gives every core and is offered nothing wider, and neither is a machine whose memory holds both widths to one worker."""
    from rebuild.tools import memory_budget

    plan = _plan(ncores=18, total_bytes=MACHINE_48_GIB)
    lane = memory_budget.split_cores(18)[0] - 1
    assert (
        plan.corpus_jobs
        == lane
        == ac.corpus_job_budget(skip_gates=False, ncores=18, total_bytes=MACHINE_48_GIB)
    )
    assert (
        plan.corpus_gates_idle_jobs
        == 17
        == ac.corpus_job_budget(skip_gates=True, ncores=18, total_bytes=MACHINE_48_GIB)
    )
    marker = ac.gates_idle_marker_path()
    assert plan.gates_idle_marker == marker
    assert _argv({step.name: step for step in plan.steps}["corpus-build"])[-8:] == [
        "--gates-idle-jobs",
        "17",
        "--gates-idle-marker",
        str(marker),
        "--jobs",
        str(lane),
        "--signature-jobs",
        "18",
    ]
    rendered = _plan_text(plan)
    assert f"    corpus-build --jobs              : {lane}  (the build lane holds 9 of 18 cores" in rendered
    assert (
        "    corpus-build --gates-idle-jobs   : 17  (taken in place of --jobs when, as the units pool starts, gate:make-test, gate:conform and gate:rebuild-contracts have each skipped or finished, which the driver signals by writing "
        f"{marker}; every usable core less its parent; 17 at {format_gb(ac.CORPUS_WORKER_BYTES)} GB each"
    ) in rendered

    for unwidened in (
        _plan(skip_gates=True, ncores=18, total_bytes=MACHINE_48_GIB),
        _plan(ncores=10, total_bytes=MACHINE_32_GIB // 4),
    ):
        assert (unwidened.corpus_gates_idle_jobs, unwidened.gates_idle_marker) == (None, None)
        assert "--gates-idle-jobs" not in _argv({step.name: step for step in unwidened.steps}["corpus-build"])
        assert "--gates-idle-jobs" not in _plan_text(unwidened)


def test_the_corpus_build_finds_the_gates_idle_marker_only_once_every_gate_pool_has_finished(monkeypatch):
    """The driver writes the marker the corpus build reads when its units pool starts once gate:make-test, gate:conform and gate:rebuild-contracts have each finished or been skipped. A marker an earlier pass left is deleted before the build is spawned, so a build spawned while gate:make-test still runs does not find one; it appears when that gate finishes during the build, and the driver deletes it once the build exits. On a pass that skips all three gates the marker is there when the build starts."""
    seen: list[tuple[bool, bool]] = []
    release_make = threading.Event()

    def fake_make(argv, spawn, emit, registry):
        release_make.wait()
        return _step("gate:make-test", 0)

    def fake_corpus(report, *, spawn, emit, registry, review_out, **_):
        marker = ac.gates_idle_marker_path()
        at_spawn = marker.exists()
        release_make.set()
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        seen.append((at_spawn, marker.exists()))
        return True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_corpus_build", fake_corpus)
    _patch_gate_fingerprints(monkeypatch)
    marker = ac.gates_idle_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    busy = _plan()
    idle = _plan(
        skip_make_test=True,
        make_test_note="closure unchanged",
        skip_conform=True,
        skip_contracts=True,
        contracts_note="inputs unchanged",
    )
    for plan in (busy, idle):
        assert plan.gates_idle_marker == marker
        ac._run_cycle(
            plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()
        )
        assert not marker.exists()
    assert seen == [(False, True), (True, True)]


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
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(skip_make_test=True, make_test_note="closure unchanged since its last green run")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    assert record["make_calls"] == 0
    assert report.gate_make_test == "skipped (closure unchanged since its last green run)"
    assert report.gate_contracts == "green"
    assert report.gate_conform == "green"


def test_the_pool_width_is_handed_to_the_make_test_child_and_to_no_other(monkeypatch):
    """The planned pool width is passed only to gate:make-test's child, in that child's environment. run_m1, the corpus build and the rebuild suite are spawned from the same process, so setting the width on `os.environ` would also fix their `-n auto` pools. The test checks that gate:js gets no extra environment and that `os.environ` stays clean."""
    seen: dict[str, dict[str, str] | None] = {}

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        seen[name] = env
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)

    plan = _plan(ncores=8)
    rc = ac._run_cycle(plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)

    assert rc == 0
    assert plan.make_test_workers == ac.make_test_pool_width(ncores=8) == 4
    assert seen["gate:make-test"] == {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.make_test_workers)}
    assert seen["gate:js"] is None
    assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in os.environ


def test_the_rebuild_suite_names_its_pool_to_its_own_child(monkeypatch):
    """The suite's pytest controller records its per-worker peaks in the timings journal under the pool name in `AMS_POOL_UNIT`, and `make job-costs` reports the suite's measurements under that name. The cycle runs the suite as plain pytest, not through `rebuild_gate.py`, so the cycle sets the name itself. It sets it on the suite's child only, because on `os.environ` every other child would inherit it and record its measurements under the wrong pool. The same child also gets its width, `contracts_pool_width`, which the pool record's `width` then reports."""
    # When the cycle's contracts gate runs this suite, AMS_POOL_UNIT is already set in this process. Clear it so the `os.environ` check below starts from a known absence.
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
    assert seen["gate:rebuild-contracts"] == {
        "AMS_POOL_UNIT": "rebuild-contracts",
        "PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.contracts_workers),
    }
    assert "AMS_POOL_UNIT" not in os.environ
    assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in os.environ
    # artifact_cycle writes the variable and the pool name as literals, so check them against `ct.POOL_UNIT_ENV` and the pool names in `cb.UNITS`. A name no unit reads would be recorded under no unit, and `make job-costs` would report the suite as unmeasured, which looks the same as a machine that has not run it yet.
    known = {name for unit in cb.UNITS for name in unit.pool_units}
    assert "rebuild-contracts" in known
    assert ct.POOL_UNIT_ENV == "AMS_POOL_UNIT"


def test_a_timed_spawn_carries_a_child_its_environment(monkeypatch, tmp_path):
    """When a cycle records timings, `CycleTimings.wrap_spawn` wraps every spawn, so it must pass a child's `env` through. Otherwise the pool width would be dropped on real runs, since only tests run a cycle without timings."""
    seen: dict[str, dict[str, str] | None] = {}

    def fake_spawn(name, argv, *, emit, registry, stream, env=None):
        seen[name] = env
        return _step(name, 0)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
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


def test_conform_skip_fingerprint_includes_max_length_and_the_behavior_classes(tmp_path):
    """The per-edit sweep's key is the deep sweep's key plus the maximum length. With no behavior-class sidecar it still returns a key, with a line marking the sidecar absent, so a caller that asks before any build has run gets a key instead of an exception. A sidecar, a new class in it, and the maximum length each change the key; the font's bytes do not."""
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    base = ac.conform_skip_fingerprint(tmp_path, 5)
    assert ac.conform_skip_fingerprint(tmp_path, 5) == base
    assert ac.conform_skip_fingerprint(tmp_path, 4) != base
    assert ac.conform_skip_files(tmp_path, 5)["behavior_classes"] == "absent"
    (tmp_path / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(b"OTTO")
    assert ac.conform_skip_fingerprint(tmp_path, 5) == base
    _write_behavior_classes(tmp_path, ["backtrack:1"])
    with_sidecar = ac.conform_skip_fingerprint(tmp_path, 5)
    assert with_sidecar != base
    assert "class:backtrack:1" in ac.conform_skip_files(tmp_path, 5)
    _write_behavior_classes(tmp_path, ["backtrack:1", "lookahead:4"])
    assert ac.conform_skip_fingerprint(tmp_path, 5) != with_sidecar


def _fake_run_m1_root(tmp_path):
    """Build a repo skeleton with one file of each kind the run_m1 skip key reads: each data input, the contact allow-list, the baselines and their subsets, the table-side and comparison-side pipeline code, the crate, and uv.lock. It also writes a behavior-class sidecar, the compile code and M1.otf for the conform key. The files are real so the tests see the labels the real readers produce."""
    for rel in (
        "glyph_data/runes",
        "rebuild/schema",
        "rebuild/pipeline",
        "rebuild/validation",
        "rebuild/kernel-rs/src",
        "rebuild/out/m1",
        "tools",
    ):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    _write_behavior_classes(tmp_path, ["backtrack:1"])
    for rel, text in (
        ("tools/build_font.py", "build = 1\n"),
        ("rebuild/pipeline/emit_gsub.py", "emit = 1\n"),
        ("rebuild/pipeline/pack_gsub.py", "pack = 1\n"),
        ("rebuild/pipeline/compile_font.py", "compile = 1\n"),
        ("glyph_data/runes/qsX.yaml", "rune: qsX\n"),
        ("rebuild/schema/rune.json", "{}\n"),
        ("rebuild/script.yaml", "script: 1\n"),
        ("glyph_data/punctuation.yaml", "punctuation: 1\n"),
        ("rebuild/m1-aliases.yaml", "qsX: X\n"),
        ("rebuild/m1-divergences.yaml", "- id: x\n  status: intended\n  why: one\n"),
        ("glyph_data/senior_quikscript_kerning.yaml", "pairs: {}\n"),
        ("rebuild/m1-contact-allow.yaml", "- signature: junction-1\n  why: blessed once\n"),
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
    """`comparison_side_label` decides which inputs may change while a cycle reuses the enumeration and font on disk, so a label wrongly on it would let a pass rerun the gates against artifacts that no longer match their sources. uv.lock is left off although the tables' stamp does not cover it, because it pins fontTools and uharfbuzz, and a bump there can change the font the rerun would rely on."""
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
    """Every label in the run_m1 key must be covered by the tables' stamp or named by `comparison_side_label`; otherwise a gates-only rerun would reuse artifacts that no longer match that input. The one exception is uv.lock, which is neither, so a change to it prevents a gates-only rerun. A new key line that fits neither group fails this test."""
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
    """A missing contact allow-list contributes no line, the way `path_lines` drops any missing file, so a read error is never hashed into the key."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    assert fingerprint.CONTACT_ALLOW_LABEL in ac.run_m1_skip_files(root)
    (root / fingerprint.CONTACT_ALLOW_LABEL).unlink()
    assert fingerprint.CONTACT_ALLOW_LABEL not in ac.run_m1_skip_files(root)


def test_the_allow_list_line_ignores_prose(tmp_path):
    """Adding a contact signature must change this key, because the defect gate is the only stage that reads the file. Rewording a `why` or adding a comment must not, because re-running the gates for it would prove nothing new."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    allow = root / fingerprint.CONTACT_ALLOW_LABEL
    before = ac.run_m1_skip_fingerprint(root)
    allow.write_text("# a comment nobody reads\n- signature: junction-1\n  why: blessed twice over\n")
    assert ac.run_m1_skip_fingerprint(root) == before
    allow.write_text("- signature: junction-1\n- signature: junction-2\n")
    assert ac.run_m1_skip_fingerprint(root) != before


def test_the_divergence_ledger_line_ignores_prose(tmp_path):
    """Reclassifying a divergence class must change this key, because the oracle reads the ledger to classify rows. Rewording a class's `why` must not, because no classifier reads it. The review build copies the `why` into the manifest and the Stage B `explain_prose` component hashes it, so a reword costs a corpus rebuild that reuses every cached unit and no gates-only rerun."""
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
    """The run_m1 key and the conform key cover different inputs. The conformance sweep shapes the compiled font and re-settles the windows beside it. It reads no ledger, allow-list, kern sidecar, baseline or oracle code, so editing any of those changes the run_m1 key and leaves the conform key alone. A rune edit, a crate edit, a uv.lock edit and the font's bytes also leave the conform key alone; the crate's string replay inside run_m1 covers rune and crate edits. A behavior class the lookup has not emitted before, the compile code, the tools/ files the compile runs, the uharfbuzz version and the maximum length each change it."""
    root = _fake_run_m1_root(tmp_path)
    conform = ac.conform_skip_fingerprint(root, 4)
    run_key = ac.run_m1_skip_fingerprint(root)
    for rel, text in (
        ("rebuild/m1-divergences.yaml", "- id: y\n  status: intended\n  why: one\n"),
        ("rebuild/m1-aliases.yaml", "qsX: Y\n"),
        ("glyph_data/senior_quikscript_kerning.yaml", "pairs: {qsX_qsY: -1}\n"),
        ("rebuild/m1-contact-allow.yaml", "- signature: junction-2\n"),
        ("rebuild/pipeline/oracle.py", "verdict = 2\n"),
        ("rebuild/out/baseline-default.tsv.gz", "many more baseline rows\n"),
        ("rebuild/out/m1/baseline-default.subset.tsv.gz", "many more subset rows\n"),
        ("glyph_data/runes/qsX.yaml", "rune: qsX\nstances: {}\n"),
        ("rebuild/pipeline/settle.py", "settle = 2\n"),
        ("rebuild/kernel-rs/src/lib.rs", "fn settle() { loop {} }\n"),
        ("uv.lock", "lock-2\n"),
    ):
        (root / rel).write_text(text)
        moved = ac.run_m1_skip_fingerprint(root)
        assert moved != run_key, rel
        assert ac.conform_skip_fingerprint(root, 4) == conform, rel
        run_key = moved
    (root / "rebuild/out/m1/M1.otf").write_text("OTTO and then some\n")
    assert ac.conform_skip_fingerprint(root, 4) == conform

    for rel, text in (
        ("rebuild/pipeline/emit_gsub.py", "emit = 2\n"),
        ("rebuild/pipeline/pack_gsub.py", "pack = 2\n"),
        ("rebuild/pipeline/compile_font.py", "compile = 2\n"),
        ("tools/build_font.py", "build = 2\n"),
    ):
        (root / rel).write_text(text)
        moved = ac.conform_skip_fingerprint(root, 4)
        assert moved != conform, rel
        conform = moved
    _write_behavior_classes(root, ["backtrack:1", "lookahead:4"])
    assert ac.conform_skip_fingerprint(root, 4) != conform
    assert ac.conform_skip_fingerprint(root, 5) != ac.conform_skip_fingerprint(root, 4)
    files = ac.conform_skip_files(root, 4)
    assert "class:lookahead:4" in files and "uharfbuzz" in files
    assert "semantics" not in files and "M1.otf" not in files


def test_gates_only_rerun_licenses_only_a_diff_the_tables_stamp_cannot_see():
    """`gates_only_rerun` returns None in three cases, each meaning no gates-only rerun is planned: there is no usable green record, nothing moved (the plain skip handles that), or a build-side input moved and the tables must be rebuilt. The test also checks `moved_input_labels`, because the annotated labels the note prints, such as `name (changed)`, would match no `comparison_side_label` entry, and the rerun would silently never be planned."""
    stored = {
        "glyph_data/runes/qsX.yaml": "r1",
        "rebuild/m1-divergences.yaml": "d1",
        "rebuild/pipeline/oracle.py": "o1",
        "rebuild/pipeline/oracle_positions.py": "p1",
        "uv.lock": "l1",
    }
    record = {"files": dict(stored)}
    assert ac.gates_only_rerun(record, dict(stored)) is None
    assert ac.gates_only_rerun(None, dict(stored)) is None
    assert ac.gates_only_rerun({"fingerprint": "fp"}, dict(stored)) is None

    ledger = {**stored, "rebuild/m1-divergences.yaml": "d2", "rebuild/pipeline/oracle.py": "o2"}
    assert ac.gates_only_rerun(record, ledger) == [
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
    assert ac.gates_only_rerun(record, {**stored, "rebuild/pipeline/oracle_positions.py": "p2"}) == [
        "rebuild/pipeline/oracle_positions.py"
    ]

    assert ac.gates_only_rerun(record, {**stored, "baseline-default.subset.tsv.gz": "s1"}) == [
        "baseline-default.subset.tsv.gz"
    ]
    dropped = {name: value for name, value in stored.items() if name != "rebuild/m1-divergences.yaml"}
    assert ac.gates_only_rerun(record, dropped) == ["rebuild/m1-divergences.yaml"]

    assert ac.gates_only_rerun(record, {**stored, "uv.lock": "l2"}) is None
    assert ac.gates_only_rerun(record, {**stored, "glyph_data/runes/qsX.yaml": "r2"}) is None
    assert ac.gates_only_rerun(record, {**stored, "rebuild/pipeline/settle.py": "s1"}) is None
    assert (
        ac.gates_only_rerun(record, {name: value for name, value in stored.items() if name != "uv.lock"})
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
    assert not any(name.startswith("max_length") for name in files)
    assert ac._digest_lines(lines) == ac.deep_sweep_skip_fingerprint(tmp_path)


def test_deep_sweep_fingerprint_moves_with_a_class_or_the_compile_code(tmp_path):
    """A new behavior class, a statement edit to a compile module, and a new tools/ file each move the key. Rewording a compile module's docstring or comment does not, because both halves of the compile code are hashed through `fingerprint.path_lines`."""
    _write_behavior_classes(tmp_path, ["namer-dot"])
    base = ac.deep_sweep_skip_fingerprint(tmp_path)
    _write_behavior_classes(tmp_path, ["namer-dot", "guard-form:zwnj"])
    grown = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert grown != base
    code = tmp_path / ac.COMPILE_CODE_FILES[0]
    code.write_text('"""Emit."""\nEMIT = 1\n')
    written = ac.deep_sweep_skip_fingerprint(tmp_path)
    code.write_text('"""Emit, reworded."""\n\nEMIT = 1  # noted\n')
    assert ac.deep_sweep_skip_fingerprint(tmp_path) == written
    code.write_text('"""Emit, reworded."""\nEMIT = 2\n')
    rewritten = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert rewritten != written
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build_font.py").write_text("# fea emitter\n")
    with_tools = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert with_tools != rewritten
    files = ac.deep_sweep_skip_files(tmp_path)
    assert files is not None
    assert "tools/build_font.py" in files
    assert ac.conform_skip_files(tmp_path, 4)["tools/build_font.py"] == files["tools/build_font.py"]


def test_deep_sweep_status_walks_unknown_never_run_due_and_current(tmp_path, monkeypatch):
    store = tmp_path / "deep-sweep-green.json"
    monkeypatch.setattr(cycle_paths, "DEEP_SWEEP_GREEN", store)
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
    assert record["max_length"] == 5
    assert ac.deep_sweep_status(tmp_path) == ("current", "maximum length 5")
    assert ac.deep_sweep_status(tmp_path, max_length=4)[0] == "current"

    assert ac.deep_sweep_status(tmp_path, max_length=6)[0] == "due"
    assert "shorter" in ac.deep_sweep_status(tmp_path, max_length=6)[1]

    _write_behavior_classes(tmp_path, ["namer-dot", "guard-form:zwnj"])
    status, note = ac.deep_sweep_status(tmp_path)
    assert status == "due"
    assert "make conform-deep" in note
    assert "class:guard-form:zwnj (new)" in note


def test_a_deep_green_record_under_the_horizon_key_reads_at_its_max_length(tmp_path, monkeypatch):
    """A deep sweep or deep replay record that stores its maximum length under `horizon` reads back at that length, so neither check reports due for want of the `max_length` field."""
    store = tmp_path / "deep-sweep-green.json"
    monkeypatch.setattr(cycle_paths, "DEEP_SWEEP_GREEN", store)
    _write_behavior_classes(tmp_path, ["namer-dot"])
    fingerprint = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert fingerprint is not None
    ac.record_deep_sweep_green(fingerprint, 5, files=ac.deep_sweep_skip_files(tmp_path), path=store)
    record = json.loads(store.read_text())
    record["horizon"] = record.pop("max_length")
    store.write_text(json.dumps(record) + "\n")
    assert ac.deep_sweep_status(tmp_path) == ("current", "maximum length 5")
    assert ac.recorded_max_length({"horizon": 6}) == 6
    assert ac.recorded_max_length({"max_length": 5, "horizon": 6}) == 5


def test_a_shallower_deep_sweep_green_keeps_a_deeper_green_under_the_same_key(tmp_path, monkeypatch):
    """A green run shallower than the recorded green under the same key keeps the recorded depth, so the sweep stays current at it; a deeper run raises the depth, and a run under a different key writes its own depth."""
    store = tmp_path / "deep-sweep-green.json"
    monkeypatch.setattr(cycle_paths, "DEEP_SWEEP_GREEN", store)
    _write_behavior_classes(tmp_path, ["namer-dot"])
    fingerprint = ac.deep_sweep_skip_fingerprint(tmp_path)
    assert fingerprint is not None
    files = ac.deep_sweep_skip_files(tmp_path)
    ac.record_deep_sweep_green(fingerprint, 5, files=files, path=store)
    ac.record_deep_sweep_green(fingerprint, 4, files=files, path=store)
    assert ac.deep_sweep_status(tmp_path) == ("current", "maximum length 5")
    ac.record_deep_sweep_green(fingerprint, 6, files=files, path=store)
    assert ac.deep_sweep_status(tmp_path, max_length=6) == ("current", "maximum length 6")
    ac.record_deep_sweep_green("another-key", 4, files=files, path=store)
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "another-key" and record["max_length"] == 4


def test_cycle_summary_payload_carries_the_deep_sweep_status(monkeypatch):
    monkeypatch.setattr(ac, "deep_sweep_status", lambda root=ac.ROOT, max_length=5: ("due", "a new shape"))
    payload = ac.cycle_summary_payload(_green_report(), [], _plan(), "ok")
    assert payload["deep_sweep"] == {"status": "due", "note": "a new shape"}


def test_the_deep_sweep_line_never_fails_the_summary(monkeypatch):
    def explode(root=ac.ROOT, max_length=5):
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
    assert conform["max_length"] == "5"
    assert conform == {"behavior_classes": "absent", "max_length": "5"}


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
    """The test takes its labels from the real tree, not literals, because a mismatch makes `oracle_cache_note` return None, which looks the same as an unaffected cache. `moved_inputs_note` reports repo-relative POSIX labels, so comparing against basenames would return None for every real input."""
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
    for positional in (
        "rebuild/pipeline/oracle_positions.py",
        "glyph_data/senior_quikscript_kerning.yaml",
        "uv.lock",
    ):
        assert (
            ac.oracle_cache_note(f"{positional} (changed)")
            == f"the oracle row cache keeps its rows and re-shapes every position: {positional} is inside its position stamp"
        )
    note = ac.oracle_cache_note(f"{rune} (changed), rebuild/pipeline/oracle_positions.py (changed)")
    assert note is not None and note.startswith("the oracle row cache keeps its rows")
    note = ac.oracle_cache_note(f"{code} (changed), rebuild/pipeline/oracle_positions.py (changed)")
    assert note is not None and note.startswith("the oracle row cache drops whole")
    assert ac.oracle_cache_note("rebuild/m1-divergences.yaml (changed)") is None
    assert ac.oracle_cache_note("rebuild/pipeline/oracle.py (changed)") is None
    assert ac.oracle_cache_note(f"{rune} (changed) and 4 more") is None
    assert ac.oracle_cache_note(None) is None


def test_m1_artifacts_present(tmp_path):
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    names = [path.name for path in cycle_paths.M1_SUMMARY_FILES.values()] + list(ac.M1_ARTIFACT_NAMES)
    assert not ac.m1_artifacts_present(tmp_path)
    for name in names:
        (m1 / name).write_text("{}")
    assert ac.m1_artifacts_present(tmp_path)
    (m1 / "M1.otf").unlink()
    assert not ac.m1_artifacts_present(tmp_path)


def test_rebuild_gate_closure_scope_and_exemptions(tmp_path):
    """The test checks both edges of the closure. The paths in `cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES` are files no test reads: the carried-verdict evidence, the JS-only jstests, the review-facts pins the cycle rewrites mid-pass, and the contact allow-list, which only the defect gate reads, so adding a contact signature does not re-run the suite. The other edge is `REBUILD_GATE_HARNESS_PATHS`, the files the suite reads outside rebuild/ and glyph_data/. Only listed paths are included, so `tools/outside.py` stays out, and `doc/glyph-names.md` is included although other Markdown is filtered out."""
    assert "rebuild/m1-contact-allow.yaml" in cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild" / "evidence").mkdir(parents=True)
    (tmp_path / "rebuild" / "review" / "jstests").mkdir(parents=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / "rebuild" / "NOTES.md").write_text("")
    (tmp_path / "rebuild" / "evidence" / "verdicts-old.json").write_text("{}")
    (tmp_path / "rebuild" / "review" / "jstests" / "x.test.js").write_text("")
    (tmp_path / "rebuild" / "m1-contact-allow.yaml").write_text("- signature: junction-1\n")
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
    """Gate keys hash files through `_sha256_path`, and some of those files may be missing, such as a tracked file deleted from the worktree, so an unreadable path hashes as `absent` instead of raising. The autouse fixture returns `absent` for live artifact paths without calling the real function; a tmp path reaches the real function, so this test exercises its fallback."""
    assert ac._sha256_path(tmp_path / "never-built.otf") == "absent"
    assert ac._sha256_path(tmp_path) == "absent"
    built = tmp_path / "built.otf"
    built.write_bytes(b"OTTO")
    assert ac._sha256_path(built) != "absent"


def test_contracts_fingerprint_ignores_prose_in_runes(tmp_path):
    """The lane key includes the rune files, because contracts tests load the live spec. A structural edit to a rune changes the key, and a ductus prose edit does not."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    rune = tmp_path / "glyph_data" / "runes" / "qsX.yaml"
    rune.write_text("rune: qsX\nductus:\n  sole: |\n    A stroke.\n")
    before = ac.contracts_fingerprint(tmp_path)
    rune.write_text("rune: qsX\nductus:\n  sole: |\n    A different stroke.\n")
    assert ac.contracts_fingerprint(tmp_path) == before
    rune.write_text("rune: qsY\nductus:\n  sole: |\n    A different stroke.\n")
    assert ac.contracts_fingerprint(tmp_path) != before


def test_contracts_fingerprint_ignores_prose_in_the_ledgers(tmp_path):
    """The closure includes the divergence ledger and the standing approvals, with prose-insensitive hashes like a rune's. Tests across the suite read the review facts and class ids from the divergence ledger and each rule's `match` from the standing approvals, never a `why` or `note`, so re-running the suite after a reword would reproduce the same result. Reclassifying a class or changing a rule's verdict still changes the key."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    ledger = tmp_path / "rebuild" / "m1-divergences.yaml"
    ledger.write_text("- id: junction-moved\n  status: intended\n  no_verdict: true\n  why: one\n")
    standing = tmp_path / "rebuild" / "standing-approvals.yaml"
    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: approve\n    note: one\n"
    )
    before = ac.contracts_fingerprint(tmp_path)

    ledger.write_text(
        "# a header nobody classifies by\n- id: junction-moved\n  status: intended\n  no_verdict: true\n  why: two, at greater length\n"
    )
    standing.write_text(
        "format: ams-standing-approvals/1\n# a header nobody matches on\nrules:\n  - id: r1\n    verdict: approve\n    note: two, at greater length\n"
    )
    assert ac.contracts_fingerprint(tmp_path) == before

    ledger.write_text(
        "- id: junction-moved\n  status: intended\n  no_verdict: false\n  why: two, at greater length\n"
    )
    reclassified = ac.contracts_fingerprint(tmp_path)
    assert reclassified != before

    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: neither\n    note: two, at greater length\n"
    )
    assert ac.contracts_fingerprint(tmp_path) != reclassified


def test_every_harness_file_moves_the_contracts_key(tmp_path):
    """Every file in `REBUILD_GATE_HARNESS_PATHS` is read under `pytest rebuild/`: the tests that replay or draft data-expect pins import test/test_shaping.py, and the tools/ compile modules with it, and `rebuild_gate_closure_files` names the reader of every other entry. So editing any of them must change the lane key instead of skipping on a green record that did not see the edit."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel in ac.REBUILD_GATE_HARNESS_PATHS:
        harness_file = tmp_path / rel
        harness_file.parent.mkdir(parents=True, exist_ok=True)
        harness_file.write_text("")
    previous = ac.contracts_fingerprint(tmp_path)
    for rel in ac.REBUILD_GATE_HARNESS_PATHS:
        (tmp_path / rel).write_text(f"{rel} moved\n")
        current = ac.contracts_fingerprint(tmp_path)
        assert current != previous, rel
        previous = current


def test_the_harness_roster_names_the_whole_tools_tree():
    """The harness roster names each tools/*.py file instead of using a glob, because the closure is assembled from git pathspecs and a glob would also pick up any other file added under tools/. The roster must still cover every tools/*.py, because `unit_cache.environment_stamp` hashes them and the contracts tests that build a store recompute that stamp. This test fails when a script is added there and not to the roster."""
    assert {rel for rel in ac.REBUILD_GATE_HARNESS_PATHS if rel.startswith("tools/")} == {
        f"tools/{path.name}" for path in (REPO_ROOT / "tools").glob("*.py")
    }


def test_contracts_fingerprint_is_none_outside_git(tmp_path):
    assert ac.contracts_fingerprint(tmp_path) is None


def test_corpus_build_skippable_matches_manifest(tmp_path):
    """A skip means a rebuild would reproduce this corpus byte for byte, so the test checks each thing that must match: the inputs fingerprint (and the `ignore` exemption), every shard the manifest names, the per-unit index and both app sidecars, and the after font. The index and sidecars are written after the manifest and outside it, so each must be stamped for the current manifest, not merely present. No fingerprint component covers M1.otf, so only the manifest's recorded after-font sha compared with M1.otf on disk shows whether run_m1 has rebuilt it since."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import app_index, unit_index

    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    corpus = tmp_path / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    stage_a = {"data": "d", "baselines": "b", "pipeline_code": "p"}
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": fingerprint.FORMAT, **stage_a}))
    font = m1 / "M1.otf"
    font.write_bytes(b"OTTO")
    before_font, junior_font = fingerprint.font_paths(tmp_path)
    expected = {**stage_a, **fingerprint.stage_b(tmp_path, before_font, junior_font)}
    shard = corpus / "units-000.json"
    shard.write_text("[]")
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "inputs_fingerprint": expected,
        "classes": [{"id": "c", "shards": ["units-000.json"]}],
        "fonts": {"after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(b"OTTO").hexdigest()}},
    }

    def restamp():
        (corpus / "manifest.json").write_text(json.dumps(manifest))
        unit_index.write_index(corpus, [])
        app_index.write_app_artifacts(corpus, {}, {})

    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)
    shard.unlink()
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    shard.write_text("[]")
    manifest["inputs_fingerprint"] = {**expected, "data": "changed"}
    restamp()
    assert not ac.corpus_build_skippable(tmp_path, corpus)

    manifest["inputs_fingerprint"] = {**expected, "static": "moved"}
    restamp()
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    assert ac.corpus_build_skippable(tmp_path, corpus, ignore=("static",))
    del manifest["inputs_fingerprint"]["static"]
    restamp()
    assert not ac.corpus_build_skippable(tmp_path, corpus, ignore=("static",))

    manifest["inputs_fingerprint"] = expected
    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)

    fonts = manifest["fonts"]
    font.write_bytes(b"OTTO-newer")
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    font.write_bytes(b"OTTO")
    assert ac.corpus_build_skippable(tmp_path, corpus)
    del manifest["fonts"]
    restamp()
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    manifest["fonts"] = fonts
    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)

    for name, _fmt in app_index.ARTIFACTS:
        kept = app_index.artifact_path(corpus, name)
        raw = kept.read_bytes()
        kept.unlink()
        assert not ac.corpus_build_skippable(tmp_path, corpus)
        kept.write_bytes(raw)
    assert ac.corpus_build_skippable(tmp_path, corpus)
    unit_index.index_path(corpus).unlink()
    assert not ac.corpus_build_skippable(tmp_path, corpus)

    # Rewrite the manifest without rewriting the index and sidecars. Every shard is present and the fingerprint matches, so only the stale stamps on those three files show the mismatch.
    unit_index.write_index(corpus, [])
    manifest["generated_at"] = "2026-02-02T00:00:00Z"
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)


def _promotion_root(root):
    """Write a Stage A record and M1.otf under `root`, and return the inputs fingerprint a corpus must record to be skippable against them."""
    from rebuild.pipeline import fingerprint

    m1 = root / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True, exist_ok=True)
    stage_a = {"data": "d", "baselines": "b", "pipeline_code": "p"}
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": fingerprint.FORMAT, **stage_a}))
    (m1 / "M1.otf").write_bytes(b"OTTO")
    before_font, junior_font = fingerprint.font_paths(root)
    return {**stage_a, **fingerprint.stage_b(root, before_font, junior_font)}


def _write_corpus(corpus, recorded, stamp):
    """Write a synthetic corpus like the one in `test_corpus_build_skippable_matches_manifest`: one shard, a manifest with `recorded` as its inputs fingerprint and `stamp` as its generated_at, and the per-unit index and app sidecars stamped for that manifest."""
    from rebuild.review import app_index, unit_index

    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "units-000.json").write_text("[]")
    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "inputs_fingerprint": recorded,
                "classes": [{"id": "c", "shards": ["units-000.json"]}],
                "fonts": {
                    "after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(b"OTTO").hexdigest()}
                },
                "totals": {"units": 1, "rows": 1, "batches": 1, "duplicate_groups": 0},
            }
        )
    )
    unit_index.write_index(corpus, [])
    app_index.write_app_artifacts(corpus, {}, {})


def test_promotable_corpus_names_a_current_staged_corpus_and_refuses_the_rest(tmp_path):
    """`promotable_corpus` returns a directory a live pass may move into place, so each precondition must reject on its own: a missing directory, the live directory itself, a staged corpus whose fingerprint no longer matches, one stamped older than the live corpus, and an unreadable manifest on either side. `corpus_build_skippable` cannot detect the stamp case, because `generated_at` is the newest input mtime, not a build time. Rejecting it prevents `merge_verdicts` from refusing the store after the move."""
    expected = _promotion_root(tmp_path)
    live = tmp_path / "rebuild" / "out" / "review"
    staged = tmp_path / "var" / "staged-review"
    summary = tmp_path / "rebuild" / "out" / "cycle_summary.json"
    _write_corpus(live, expected, "2026-01-01T00:00:00Z")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps({"plan": {"review_out": "rebuild/out/review"}}))
    assert ac.promotable_corpus(tmp_path, summary, live) is None
    summary.unlink()
    _write_corpus(live, {**expected, "data": "stale"}, "2026-01-01T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) is None

    _write_corpus(staged, expected, "2026-01-02T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) == staged

    _write_corpus(staged, {**expected, "review_code": "moved"}, "2026-01-02T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) is None

    _write_corpus(staged, expected, "2025-12-31T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) is None
    _write_corpus(staged, expected, "2026-01-01T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) == staged

    (staged / "manifest.json").write_text("{ not json")
    assert ac.promotable_corpus(tmp_path, summary, live) is None
    _write_corpus(staged, expected, "2026-01-02T00:00:00Z")
    (live / "manifest.json").write_text("{ not json")
    assert ac.promotable_corpus(tmp_path, summary, live) is None


def test_promotable_corpus_reads_the_recorded_pointer_before_the_convention(tmp_path):
    """`--review-out` accepts any path, so the directory recorded in the last cycle summary is tried first, resolved against the root because the summary stores it repo-relative. A summary with no pointer, one that does not parse, or one naming a missing directory falls back to `var/staged-review` without raising."""
    expected = _promotion_root(tmp_path)
    live = tmp_path / "rebuild" / "out" / "review"
    _write_corpus(live, {**expected, "data": "stale"}, "2026-01-01T00:00:00Z")
    conventional = tmp_path / "var" / "staged-review"
    recorded = tmp_path / "var" / "elsewhere"
    _write_corpus(conventional, expected, "2026-01-02T00:00:00Z")
    _write_corpus(recorded, expected, "2026-01-02T00:00:00Z")
    summary = tmp_path / "rebuild" / "out" / "cycle_summary.json"

    summary.write_text(json.dumps({"plan": {"review_out": "var/elsewhere"}}))
    assert ac.promotable_corpus(tmp_path, summary, live) == recorded
    summary.write_text(json.dumps({"plan": {"review_out": None}}))
    assert ac.promotable_corpus(tmp_path, summary, live) == conventional
    summary.write_text("{ not json")
    assert ac.promotable_corpus(tmp_path, summary, live) == conventional
    summary.write_text(json.dumps({"plan": {"review_out": "var/vanished"}}))
    assert ac.promotable_corpus(tmp_path, summary, live) == conventional


def test_promotable_corpus_prefers_a_complete_review_next(tmp_path):
    """A complete `review.next` a stopped pass left is the first candidate, ahead of the staging directories, when it reproduces these inputs; one built for other inputs is passed over for the next candidate."""
    expected = _promotion_root(tmp_path)
    live = tmp_path / "rebuild" / "out" / "review"
    _write_corpus(live, {**expected, "data": "stale"}, "2026-01-01T00:00:00Z")
    staged = tmp_path / "var" / "staged-review"
    _write_corpus(staged, expected, "2026-01-02T00:00:00Z")
    summary = tmp_path / "rebuild" / "out" / "cycle_summary.json"
    beside = ac.next_corpus_dir(live)
    _write_corpus(beside, expected, "2026-01-02T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) == beside
    _write_corpus(beside, {**expected, "data": "older"}, "2026-01-02T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) == staged


def test_a_kept_review_next_seeds_the_build_when_run_m1_is_due(tmp_path, monkeypatch, capsys):
    """A complete `review.next` is promoted only on a pass whose run_m1 skipped, because the check compares it with the Stage A run_m1 recorded. On a pass whose run_m1 runs, it is kept as the build's seed instead, so the corpus build plans a seed that keeps it and no promotion. An unfinished one is deleted at the start of a real pass and left by a dry run."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ac, "promotable_corpus", lambda *a, **k: pytest.fail("promotion considered with run_m1 due")
    )
    beside = ac.next_corpus_dir()
    beside.mkdir()
    (beside / "manifest.json").write_text("{ unfinished")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "a corpus an earlier pass did not finish" in out
    assert beside.exists()
    assert "clones rebuild/out/review" in _step_lines(out, "corpus-seed")

    note = ac.recover_next_corpus()
    assert note is not None and note.startswith("Deleted ")
    assert not beside.exists()

    _write_corpus(beside, {}, "2026-07-17T20:24:44Z")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Kept " in out
    assert "keeps " in _step_lines(out, "corpus-seed")
    assert "--out " + str(beside) in _step_lines(out, "corpus-build")


def test_recover_superseded_corpus_settles_the_leftover_before_the_first_run_question(
    tmp_path, monkeypatch, capsys
):
    """A `.superseded` tree beside a live tree is the corpus a land's three-rename fallback replaced, so it is deleted. A `.superseded` tree with no live tree beside it is the live corpus an interrupted fallback moved aside, so it is moved back. `main` does this before checking whether a corpus exists, so an interrupted land is not treated as a first run. A dry run still moves a lone tree back, so its plan matches what a real pass would do, but leaves a tree that sits beside a live one for the next real pass to delete."""
    live = tmp_path / "review"
    superseded = tmp_path / "review.superseded"
    assert ac.recover_superseded_corpus(live) is None
    superseded.mkdir()
    (superseded / "manifest.json").write_text("{}")
    note = ac.recover_superseded_corpus(live)
    assert note is not None and note.startswith("Put ")
    assert (live / "manifest.json").exists()
    assert not superseded.exists()
    superseded.mkdir()
    note = ac.recover_superseded_corpus(live)
    assert note is not None and note.startswith("Deleted ")
    assert (live / "manifest.json").exists()
    assert not superseded.exists()

    _seed_auto_repo(tmp_path, monkeypatch)
    os.replace(ac.REVIEW_OUT, ac.REVIEW_OUT.with_name("review.superseded"))
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Put " in out and "review.superseded" in out
    assert "First-run mode" not in out
    assert (ac.REVIEW_OUT / "manifest.json").exists()

    leftover = ac.REVIEW_OUT.with_name("review.superseded")
    leftover.mkdir()
    (leftover / "manifest.json").write_text("{}")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Left " in out and "for the next real pass to delete" in out
    assert (leftover / "manifest.json").exists()


def test_a_landed_corpus_still_answers_for_itself(tmp_path, mini_corpus):
    """Every stamp inside a corpus depends only on its manifest's content, so a real corpus the land swaps in from another path still passes the skip's checks (the per-unit index and both app sidecars), and both stores load under the environment recorded in their headers. The outgoing tree ends at the staged path."""
    from rebuild.review import app_index, unit_cache, unit_index

    source = tmp_path / "staged"
    shutil.copytree(mini_corpus, source)
    live = tmp_path / "review"
    live.mkdir()
    (live / "stale.txt").write_text("outgoing")

    def header_environment(path):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.loads(next(stream))["environment"]

    environment = header_environment(unit_cache.store_path(source))
    signature_environment = header_environment(unit_cache.signature_store_path(source))

    landing.exchange_dirs(source, live)

    assert (source / "stale.txt").exists()
    assert not (live / "stale.txt").exists()
    assert unit_index.index_is_current(live)
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(live, name, fmt), name
    store = unit_cache.load_store(live, environment)
    assert store
    signatures = unit_cache.load_signature_store(live, signature_environment)
    assert signatures


REFUSE_RUNE = "rune: qsX\npolicy:\n  refuse:\n  - {exit: baseline, why: two verticals render thick}\n"


def _stamped_corpus(root):
    """Write a review corpus stamped for the current inputs under `root`, so `corpus_build_skippable` is true right after. After a prose edit, a failed skip shows the edit reached the corpus's stamp, and a passing skip shows it did not."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import app_index, unit_index

    stage_a = fingerprint.stage_a(root)
    m1 = root / "rebuild" / "out" / "m1"
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": fingerprint.FORMAT, **stage_a}))
    corpus = root / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    (corpus / "units-000.json").write_text("[]")
    before_font, junior_font = fingerprint.font_paths(root)
    (corpus / "manifest.json").write_text(
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
    unit_index.write_index(corpus, [])
    app_index.write_app_artifacts(corpus, {}, {})
    return corpus


def _upstream_keys(root):
    from rebuild.pipeline import fingerprint

    return {
        "run_m1": ac.run_m1_skip_fingerprint(root),
        "conform": ac.conform_skip_fingerprint(root),
        "tables": fingerprint.tables_value(root),
        "stage_a": fingerprint.stage_a(root),
        "contracts": ac.contracts_fingerprint(root),
    }


def test_a_refuse_why_edit_restamps_the_corpus_and_nothing_upstream(tmp_path):
    """A refusal's `why` is quoted into the explain text the corpus serves, so the corpus must notice a rewording. Nothing that builds an artifact reads it, so the run_m1 key, the conform key, the tables' stamp, the Stage A record and the lane key all stay unchanged, and the pass after such an edit rebuilds only the corpus."""
    root = _fake_run_m1_root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("rebuild/out/\n")
    rune = root / "glyph_data" / "runes" / "qsX.yaml"
    rune.write_text(REFUSE_RUNE)
    corpus = _stamped_corpus(root)
    assert ac.corpus_build_skippable(root, corpus)
    upstream = _upstream_keys(root)
    assert all(value is not None for value in upstream.values())

    rune.write_text(REFUSE_RUNE.replace("render thick", "render thin"))
    assert not ac.corpus_build_skippable(root, corpus)
    assert _upstream_keys(root) == upstream


def test_a_ledger_why_edit_restamps_the_corpus_and_nothing_upstream(tmp_path):
    """The review build copies each divergence class's `why` into the manifest, so the corpus must notice a rewording. The oracle does not read the `why`, so the run_m1 key, the conform key, the tables' stamp, the Stage A record and the lane key stay unchanged. Reclassifying the class must change the run_m1 key, the Stage A record and the lane key."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("rebuild/out/\n")
    ledger = root / fingerprint.DIVERGENCE_LEDGER_LABEL
    corpus = _stamped_corpus(root)
    assert ac.corpus_build_skippable(root, corpus)
    upstream = _upstream_keys(root)
    assert all(value is not None for value in upstream.values())

    ledger.write_text("- id: x\n  status: intended\n  why: one, said at greater length\n")
    assert not ac.corpus_build_skippable(root, corpus)
    assert _upstream_keys(root) == upstream

    ledger.write_text("- id: x\n  status: reviewed-approved\n  why: one, said at greater length\n")
    reclassified = _upstream_keys(root)
    assert reclassified["run_m1"] != upstream["run_m1"]
    assert reclassified["stage_a"] != upstream["stage_a"]
    assert reclassified["contracts"] != upstream["contracts"]


def test_a_standing_note_reword_moves_the_verdict_update_key_and_nothing_else(tmp_path):
    """The standing fill quotes a rule's `note` into every verdict note it writes, so rewording a note must change the verdict-update key. The corpus and every build key stay unchanged, and so does the lane key, because the tests read each rule's `match` and not its prose. Changing a rule's verdict must change the lane key and leave the run_m1 key unchanged."""
    root = _fake_run_m1_root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("rebuild/out/\n")
    standing = root / "rebuild" / "standing-approvals.yaml"
    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: approve\n    note: one\n"
    )
    master = root / "verdicts-autosave.json"
    master.write_text("{}")
    corpus = _stamped_corpus(root)
    assert ac.corpus_build_skippable(root, corpus)
    upstream = _upstream_keys(root)
    verdict_update_key = ac.verdict_update_skip_fingerprint(root, corpus, master)
    assert verdict_update_key is not None

    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: approve\n    note: one, said at greater length\n"
    )
    assert ac.verdict_update_skip_fingerprint(root, corpus, master) != verdict_update_key
    assert ac.corpus_build_skippable(root, corpus)
    assert _upstream_keys(root) == upstream

    standing.write_text(
        "format: ams-standing-approvals/1\nrules:\n  - id: r1\n    verdict: neither\n    note: one, said at greater length\n"
    )
    flipped = _upstream_keys(root)
    assert flipped["run_m1"] == upstream["run_m1"]
    assert flipped["contracts"] != upstream["contracts"]


def test_the_review_facts_pins_are_outside_the_rebuild_closure(tmp_path):
    """The review-facts step rewrites the pins during a pass, so hashing them would change the gate's key before its green record is written on every pass that refreshes them. No test reads them, so they are exempt and a refresh leaves the key unchanged."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    pins = tmp_path / "rebuild" / "review-facts-pins.json"
    pins.write_text(json.dumps({"invariant": {"classes": 3}, "volatile": {"rows": 1}}))
    assert ac.rebuild_gate_closure_files(tmp_path) == ["rebuild/test_x.py"]
    before = ac.contracts_fingerprint(tmp_path)
    pins.write_text(json.dumps({"invariant": {"classes": 3}, "volatile": {"rows": 2}}))
    assert ac.contracts_fingerprint(tmp_path) == before


def test_dry_run_plan_skip_run_m1_and_corpus_still_runs_the_review_facts():
    """A pass with nothing to rebuild still runs the review-facts step. The pins are the cycle's output, not a keyed stage, and refreshing them from the sidecar takes milliseconds."""
    plan = _plan(
        skip_run_m1=True,
        run_m1_note="build inputs unchanged since the last green M1 build; --fresh overrides",
        skip_corpus=True,
        corpus_note="the corpus already reflects these inputs byte for byte, stamp included; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv is None
    assert "SKIPPED (build inputs unchanged" in by_name["run_m1"].note
    assert by_name["corpus-build"].argv is None
    assert _argv(by_name["review-facts"])[-3:] == ["--update", "--corpus", str(ac.REVIEW_OUT)]
    assert by_name["verdict-update"].argv is not None
    assert by_name["gate:rebuild-contracts"].argv is not None


def test_dry_run_plan_skips_the_rebuild_suite():
    """A typical pass after an M1 rebuild: the artifacts changed, but the suite's key includes no build artifact, so the suite can still skip."""
    plan = _plan(
        skip_contracts=True,
        contracts_note="input closure unchanged since its last green run; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:rebuild-contracts"].argv is None
    assert "SKIPPED (input closure unchanged" in by_name["gate:rebuild-contracts"].note
    assert by_name["gate:conform"].argv is not None
    rendered = _plan_text(plan)
    assert "Lane rebuild-contracts           : SKIPPED" in rendered


def test_dry_run_plan_auto_skip_conform_note():
    plan = _plan(
        skip_conform=True,
        conform_note="font and sweep inputs unchanged since its last green sweep; --fresh overrides",
    )
    by_name = {step.name: step for step in plan.steps}
    assert by_name["gate:conform"].argv is None
    assert "font and sweep inputs unchanged" in by_name["gate:conform"].note


def test_run_cycle_never_spawns_a_skipped_rebuild_suite(monkeypatch):
    record = {"contracts": 0}

    def fake_contracts(
        pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
    ):
        record["contracts"] += 1
        return _lane_result("rebuild-contracts")

    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
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
    assert record == {"contracts": 0}
    assert report.gate_contracts.startswith("skipped (input closure unchanged")
    assert report.gate_conform == "green"


def test_cycle_summary_payload_tells_a_proved_skip_from_a_forced_one():
    report = _green_report()
    report.gate_contracts = "skipped (input closure unchanged)"
    report.gate_contracts_green = None
    report.gate_make_test = "skipped (closure unchanged)"
    report.gate_make_test_green = None
    report.gate_conform = "skipped (--skip-conform)"
    report.gate_conform_green = None
    plan = _plan(
        skip_contracts=True,
        skip_conform=True,
        skip_make_test=True,
    )
    payload = ac.cycle_summary_payload(report, [], plan, "ok")
    assert payload["gates"]["rebuild_contracts"]["skip"] == "proved"
    assert payload["gates"]["make_test"]["skip"] == "proved"
    assert payload["gates"]["conform"]["skip"] == "forced"
    assert payload["gates"]["js"]["skip"] is None


def test_finish_says_the_cycle_is_complete(monkeypatch, capsys):
    monkeypatch.setattr(ac, "run_retention", lambda plan: None)
    assert ac._finish(_green_report(), [], _plan()) == 0
    assert "Cycle complete." in capsys.readouterr().out


def test_a_green_finish_closes_on_the_readiness_checklist_instead_of_naming_the_command(monkeypatch, capsys):
    """A green pass prints the `make verdict-ready` checklist itself instead of telling the reader to run the command. A red pass prints none of it, and its failure block names the next step."""
    seen: list[ac.Plan] = []

    def block(plan):
        seen.append(plan)
        return ["Review corpus: here", "  ✓ gates: green", "", "READY - adjudicate at the review queue"]

    monkeypatch.setattr(cycle_paths, "READINESS_ENABLED", True)
    monkeypatch.setattr(ac, "readiness_block", block)
    plan = _plan()
    assert ac._finish(_green_report(), [], plan) == 0
    out = capsys.readouterr().out
    assert seen == [plan]
    assert "READY - adjudicate at the review queue" in out
    assert "make verdict-ready" not in out
    assert out.index("  ✓ gates: green") < out.index("Cycle complete.")

    assert ac._finish(_green_report(), ["boom"], plan) == 1
    out = capsys.readouterr().out
    assert seen == [plan]
    assert "READY" not in out and "make verdict-ready" not in out


def test_the_readiness_block_leaves_the_server_row_to_the_recipe_that_serves(monkeypatch):
    """`make review-cycle` passes `--stop-server` and starts the server after the pass, so that pass's checklist leaves out the server row instead of reporting the server as absent. A plain `make artifact-cycle` includes the row, and a staging pass prints no checklist."""
    from rebuild.tools import verdict_ready

    asked: list[bool] = []

    def fake_readiness(*, with_server, **kwargs):
        asked.append(with_server)
        return {"corpus": {"dir": "d", "generated_at": "g", "repo_head": "h"}, "checks": {}}, True

    monkeypatch.setattr(verdict_ready, "readiness", fake_readiness)

    assert ac.readiness_block(_plan(recipe_serves=True))[-1].startswith("READY")
    assert ac.readiness_block(_plan())[-1].startswith("READY")
    assert asked == [False, True]
    assert ac.readiness_block(_plan(review_out=Path("/tmp/staged"))) == []
    assert asked == [False, True]


def test_the_readiness_block_reports_a_checklist_it_could_not_compute(monkeypatch):
    from rebuild.tools import verdict_ready

    def boom(**kwargs):
        raise RuntimeError("no manifest")

    monkeypatch.setattr(verdict_ready, "readiness", boom)
    lines = ac.readiness_block(_plan())
    assert lines == ["readiness: the checklist could not be computed (RuntimeError('no manifest'))"]


def _unsettled_repo(tmp_path, monkeypatch, stamp="2026-07-17T20:24:44Z"):
    """Set up a repo where no keyed stage can skip: run_m1's key matches no record, the make-test fingerprint is None, and the rebuild lane's key matches no record. `_settled_repo` is the counterpart in which run_m1 and the corpus build both skip."""
    _seed_auto_repo(tmp_path, monkeypatch, stamp=stamp)
    (tmp_path / "var").mkdir()
    (tmp_path / "verdicts-autosave.json").write_text(json.dumps(_verdicts_doc(stamp, ["u-1"])))
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "key")
    monkeypatch.setattr(ac, "make_test_closure_fingerprint", lambda root=None: None)
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "no-match-contracts")
    monkeypatch.setattr(
        ac, "contracts_closure", lambda root: ("no-match-contracts", {"key": "no-match-contracts"})
    )


def test_main_runs_every_heavy_gate_on_a_pass_that_rebuilds(tmp_path, monkeypatch, capsys):
    """A pass that rebuilds still runs every heavy gate whose inputs it cannot show are unchanged."""
    _unsettled_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "uv run pytest" in _step_lines(out, "gate:rebuild-contracts")
    assert "uv run python -m rebuild.pipeline.run_m1 --conform-only" in _step_lines(out, "gate:conform")
    assert "make test" in _step_lines(out, "gate:make-test")


def test_main_auto_skips_the_rebuild_suite_even_when_run_m1_runs_live(tmp_path, monkeypatch, capsys):
    """The suite's closure includes no build artifact, so a live M1 rebuild cannot change its key during the pass, and the preflight can decide that skip in every run_m1 mode."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "key-contracts")
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("key-contracts", {"key": "key-contracts"}))
    ac.record_green(cycle_paths.REBUILD_CONTRACTS_GREEN, "key-contracts")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED (build inputs unchanged" not in out
    assert "SKIPPED (input closure unchanged" in _step_lines(out, "gate:rebuild-contracts")


def test_main_forces_the_rebuild_suite_under_fresh(tmp_path, monkeypatch, capsys):
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "key-contracts")
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("key-contracts", {"key": "key-contracts"}))
    ac.record_green(cycle_paths.REBUILD_CONTRACTS_GREEN, "key-contracts")
    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED" not in out
    assert "uv run pytest" in _step_lines(out, "gate:rebuild-contracts")


def _full_build_step(out: str):
    """Return the match for the rendered run_m1 step of a plan that builds, so the negative cases can show a build was planned instead of the gates-only argv. `--jobs` appears before `--kernel-threads` only when the sweep width is above one, the memo write order, the count of deltas started beside `default` and then `--replay-threads` always follow `--kernel-threads`, and `--fresh-oracle-cache` appears only under `--fresh`."""
    return re.search(
        r"^ +\$ uv run python -m rebuild\.pipeline\.run_m1"
        r"( --jobs \d+)? --kernel-threads \d+ --(no-)?overlap-memo-writes --scratch-beside-default \d+"
        r" --replay-threads \d+( --fresh-oracle-cache)?$",
        _step_lines(out, "run_m1"),
        re.MULTILINE,
    )


def _comparison_side_drift(tmp_path, monkeypatch, moved="rebuild/m1-divergences.yaml"):
    """Set up a repo whose last green M1 build differs from the current inputs only in `moved`, with `m1_artifacts_present` and `m1_tables_stamped` both true. The tests that use it each change one of these three conditions and read the chosen mode from the plan."""
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(cycle_paths.RUN_M1_GREEN, "green-key", files={moved: "before", "uv.lock": "lock-1"})
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {moved: "after", "uv.lock": "lock-1"})
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda root=None: True)


def test_main_reruns_the_gates_when_only_comparison_side_inputs_moved(tmp_path, monkeypatch, capsys):
    """When only a ledger changed, the pass re-runs the gates instead of rebuilding. The tables' stamp does not cover the changed file, so the enumeration and font on disk still match the runes, and `run_m1 --gates-only` re-runs the gates over them. No `--kernel-threads` or `--replay-threads` is passed, because this mode enumerates and replays nothing."""
    _comparison_side_drift(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    row = _step_lines(out, "run_m1")
    assert "rebuild/m1-divergences.yaml" in row
    assert re.search(
        r"^ +run +run_m1 +only comparison-side inputs moved[^\n]*the tables and font are reused[^\n]*\n"
        r" +\$ uv run python -m rebuild\.pipeline\.run_m1 --gates-only",
        row,
        re.MULTILINE,
    )
    assert "run_m1 --kernel-threads          : not passed" in out
    assert "run_m1 --replay-threads          : not passed" in out
    assert "inputs moved since its last green" not in row


def test_a_plan_that_skips_run_m1_never_also_reruns_its_gates():
    """The skip and the gates-only rerun are exclusive, and the skip wins because nothing moved and there are no gates to rerun. The plan resolves the pair itself instead of relying on its caller. The gates-only rerun is a step that runs, so it has an argv, and it passes no `--kernel-threads` or `--replay-threads`."""
    both = _plan(skip_run_m1=True, rerun_gates_only=True, run_m1_note="build inputs unchanged")
    assert both.rerun_gates_only is False
    assert not both.runs("run_m1")
    rerun = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    assert rerun.runs("run_m1")
    assert "--gates-only" in rerun.argv("run_m1")
    assert "--kernel-threads" not in rerun.argv("run_m1")
    assert "--replay-threads" not in rerun.argv("run_m1")


def test_main_skips_the_corpus_on_a_gates_only_rerun_only_when_stage_a_already_stands(
    tmp_path, monkeypatch, capsys
):
    """On a gates-only rerun the corpus build is skipped only when `m1_stage_a_current` says the Stage A record on disk matches the live sources. A contact allow-list edit is outside every Stage A component, so the record the gates-only pass rewrites is the one already on disk and the corpus cannot change. A divergence-ledger edit moves Stage A's data component, so the record on disk is stale until the pass rewrites it. The skip can trust the record because nothing moved at all."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="rebuild/m1-contact-allow.yaml")
    monkeypatch.setattr(ac, "corpus_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: True)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" in _step_lines(out, "run_m1")
    assert "SKIPPED (the corpus already reflects these inputs" in _step_lines(out, "corpus-build")

    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: False)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" in _step_lines(out, "run_m1")
    assert "uv run python -m rebuild.review.build" in _step_lines(out, "corpus-build")


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
    """A green record alone does not permit a gates-only rerun. It shows that the artifacts once came from a complete build of every build-side input; only the tables' stamp (`m1_tables_stamped`) shows that none of those inputs has moved since. Without the stamp the pass rebuilds."""
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
    """One moved build-side label forces a rebuild, whatever comparison-side labels moved with it: the artifacts on disk were built from sources that no longer exist, and re-running the gates over them would evaluate a font built from the previous runes."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="glyph_data/runes/qsX.yaml")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "inputs moved since its last green" in _step_lines(out, "run_m1")
    assert "--gates-only" not in out


def test_main_takes_neither_mode_under_fresh(tmp_path, monkeypatch, capsys):
    """--fresh disables both modes. It covers the case no input fingerprint can detect: an artifact on disk that is wrong for some other reason."""
    _comparison_side_drift(tmp_path, monkeypatch)
    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" not in out
    assert _full_build_step(out) is not None


def test_run_cycle_skips_the_sweep_after_run_m1_on_the_key_the_finished_artifacts_carry(
    monkeypatch, tmp_path, capsys
):
    """The conform skip is decided after run_m1, not in the plan, because only a finished build knows what the font came out as. All three run_m1 modes (skipped, gates-only, rebuilt) end on this same key. A skip over the artifacts the pass leaves behind is recorded as "proved", which is what `review/status.py` needs to call a corpus ready for review."""
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "cfp")
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "rfp-contracts")
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("rfp-contracts", {"key": "rfp-contracts"}))
    ac.record_green(cycle_paths.CONFORM_GREEN, "cfp")
    swept: list[list[str]] = []

    def conform_spy(pool_policy, make_fut, spawn, emit, registry, argv):
        swept.append(argv)
        return _conform_result()

    monkeypatch.setattr(ac, "_gate_conform_task", conform_spy)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
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
    """The converse: when the finished artifacts' key matches no green record, the sweep runs. This is why the skip cannot be decided in the plan, which is resolved before run_m1 has changed the font."""
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "cfp")
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "rfp-contracts")
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("rfp-contracts", {"key": "rfp-contracts"}))
    ac.record_green(cycle_paths.CONFORM_GREEN, "a-font-ago")
    swept: list[list[str]] = []

    def conform_spy(pool_policy, make_fut, spawn, emit, registry, argv):
        swept.append(argv)
        return _conform_result()

    monkeypatch.setattr(ac, "_gate_conform_task", conform_spy)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
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


def test_do_run_m1_skip_reads_recorded_summaries(monkeypatch, tmp_path):
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    files["pipeline"].write_text(json.dumps({"defect_errors": []}))
    files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
    files["oracle"].write_text(json.dumps({"unmatched": 7, "multi_matched": 0}))
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    ac.record_green(green, "fp-live")
    original = green.read_bytes()
    monkeypatch.setattr(
        ac, "run_m1_skip_fingerprint", lambda root=None: pytest.fail("skip recomputed inputs")
    )

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
        record=True,
        fingerprint="fp-live",
    )
    assert gate is not None and gate.ok
    assert report.unmatched == 7
    assert files["pipeline"].exists()
    assert green.read_bytes() == original


def test_do_run_m1_records_green_only_when_fingerprint_stable(monkeypatch, tmp_path):
    """A green is recorded only when the inputs did not change during the build. `run_m1_skip_files` is stubbed along with the fingerprint because the real one reads the live contact allow-list, which the contracts closure exempts on the grounds that no test reads it."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
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


def test_do_run_m1_gates_only_spares_the_summary_that_pass_rewrites(monkeypatch, tmp_path):
    """On a gates-only rerun the pass keeps pipeline_summary.json and clears the other two summaries. `--gates-only` rewrites that summary's defect fields in place and exits without one, so clearing it would break the pass. The two gate summaries are the child's own output and are cleared as on a full build, so a child that dies mid-pass cannot leave the last pass's results to be read as this one's. After the spawn the rerun follows the full build's path, including the green record."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-divergences.yaml": "d2"})
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda: True)
    ac.record_green(green, "fp-prior", files={"rebuild/m1-divergences.yaml": "d1"})
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
        gates_only=True,
        record=True,
        fingerprint="fp-live",
    )
    assert survivors == ["pipeline"]
    assert spawned == [ac.RUN_M1_GATES_ONLY_STEP] != ["run_m1"]
    assert gate is not None and gate.ok
    assert report.unmatched == 3
    assert json.loads(files["pipeline"].read_text())["gsub_rule_count"] == 4212
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "fp-live"
    assert record["files"] == {"rebuild/m1-divergences.yaml": "d2"}


def test_do_run_m1_a_full_build_clears_the_summary_a_gates_only_rerun_keeps(monkeypatch, tmp_path):
    """The converse, so the exemption cannot widen unnoticed: a full build writes its own pipeline summary, so it clears the stale one, which would otherwise be read as this build's if the child died before writing a new one."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
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
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
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


@pytest.mark.parametrize("gates_only", [False, True])
@pytest.mark.parametrize("prior", ["matching", "unrelated", "invalidated"])
def test_do_run_m1_nonzero_exit_overrides_passing_summaries(monkeypatch, tmp_path, gates_only, prior):
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-divergences.yaml": "d2"})
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda: True)
    key = "fp-unrelated" if prior == "unrelated" else "fp-live"
    ac.record_green(green, key, files={"rebuild/m1-divergences.yaml": "d1"})
    original = green.read_bytes()
    _write_pass_summaries(files)
    timings = CycleTimings(tmp_path / "timings.ndjson")

    def child(name, argv, **kwargs):
        _write_pass_summaries(files)
        if prior == "invalidated":
            ac.clear_contradicted_green(green, "fp-live")
        return _step(name, 9)

    report = ac.CycleReport()
    gate = ac._do_run_m1(
        report,
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        gates_only=gates_only,
        record=True,
        fingerprint="fp-live",
        timings=timings,
    )

    assert gate is not None and not gate.ok
    assert gate.status == "FAILED (exit 9)"
    assert gate.failures == ["run_m1 gate: exited 9 despite passing summaries"]
    (line,) = ct.load_checks(timings.path)
    assert (line["outcome"], line["status"]) == ("red", gate.status)
    if prior == "unrelated":
        assert green.read_bytes() == original
    else:
        assert not green.exists()


@pytest.mark.parametrize("mode", ["full", "gates-only", "skip"])
@pytest.mark.parametrize("key", ["fp-live", "fp-unrelated"])
def test_do_run_m1_missing_summaries_clear_only_matching_green(monkeypatch, tmp_path, mode, key):
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    ac.record_green(green, key, files={"rebuild/m1-divergences.yaml": "d1"})
    original = green.read_bytes()
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-divergences.yaml": "d2"})
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda: True)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: pytest.fail("red recomputed inputs"))

    def child(name, argv, **kwargs):
        assert mode != "skip"
        return _step(name, 2)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        skip=mode == "skip",
        gates_only=mode == "gates-only",
        record=True,
        fingerprint="fp-live",
    )

    assert gate is None
    if key == "fp-live":
        assert not green.exists()
    else:
        assert green.read_bytes() == original


@pytest.mark.parametrize(
    "condition",
    [
        "eligible",
        "missing-prior",
        "legacy-prior",
        "build-side",
        "unchanged",
        "stale-before",
        "stale-after",
        "drift",
    ],
)
def test_do_run_m1_gates_only_records_only_an_eligible_stable_rerun(monkeypatch, tmp_path, capsys, condition):
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    prior_files = {"rebuild/m1-divergences.yaml": "d1", "glyph_data/runes/qsPea.yaml": "r1"}
    current = {**prior_files, "rebuild/m1-divergences.yaml": "d2"}
    if condition == "build-side":
        current["glyph_data/runes/qsPea.yaml"] = "r2"
    elif condition == "unchanged":
        current = dict(prior_files)
    if condition != "missing-prior":
        ac.record_green(green, "fp-prior", files=None if condition == "legacy-prior" else prior_files)
    original = green.read_bytes() if green.exists() else None
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: dict(current))
    state = {"finished": False}
    monkeypatch.setattr(
        ac,
        "run_m1_skip_fingerprint",
        lambda root=None: "fp-edited" if condition == "drift" and state["finished"] else "fp-live",
    )
    monkeypatch.setattr(
        ac,
        "m1_tables_stamped",
        lambda: condition != "stale-before" and not (condition == "stale-after" and state["finished"]),
    )
    _write_pass_summaries(files)

    def child(name, argv, **kwargs):
        _write_pass_summaries(files)
        state["finished"] = True
        return _step(name)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        gates_only=True,
        record=True,
        fingerprint="fp-live",
    )

    assert gate is not None and gate.ok
    if condition == "eligible":
        record = ac.read_green_record(green)
        assert record is not None and record["fingerprint"] == "fp-live"
        assert record["files"] == current
    else:
        assert (green.read_bytes() if green.exists() else None) == original
        assert "green not recorded" in capsys.readouterr().out


@pytest.mark.parametrize("gates_only", [False, True])
def test_run_cycle_stops_before_corpus_and_land_on_nonzero_m1_with_passing_summaries(
    monkeypatch, tmp_path, gates_only
):
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)

    def must_not_run(*args, **kwargs):
        pytest.fail("failed run_m1 reached the corpus build or land")

    monkeypatch.setattr(ac, "_do_corpus_build", must_not_run)
    monkeypatch.setattr(ac, "_do_land", must_not_run)
    spawned = []

    def child(name, argv, **kwargs):
        spawned.append(name)
        _write_pass_summaries(files)
        return _step(name, 9)

    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(skip_gates=True, rerun_gates_only=gates_only),
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=child,
    )

    assert rc == 1 and report.run_m1_failed
    assert spawned == [ac.RUN_M1_GATES_ONLY_STEP if gates_only else "run_m1"]


def test_do_corpus_build_skip_reads_manifest_totals(monkeypatch, tmp_path):
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"totals": {"units": 5, "rows": 9, "batches": 2, "duplicate_groups": 3}})
    )
    monkeypatch.setattr(ac, "REVIEW_OUT", corpus)

    def no_spawn(*a, **k):
        raise AssertionError("skip path must not spawn")

    report = ac.CycleReport()
    ok = ac._do_corpus_build(
        report,
        spawn=no_spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=None,
        skip=True,
        skip_note="test",
    )
    assert ok
    assert (report.corpus_units, report.corpus_rows, report.corpus_batches, report.duplicate_groups) == (
        5,
        9,
        2,
        3,
    )


def test_record_conform_green_records_refuses_and_clears(monkeypatch, tmp_path):
    conform_green = tmp_path / "conform-green.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", conform_green)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "cfp")
    plan = _plan()
    report = ac.CycleReport()
    report.gate_conform = "green"
    report.gate_conform_green = True
    ac._record_conform_green(report, plan, "cfp", ac._Emitter())
    record = ac.read_green_record(conform_green)
    assert record is not None and record["fingerprint"] == "cfp"

    conform_green.unlink()
    ac._record_conform_green(report, plan, "moved", ac._Emitter())
    assert ac.read_green_record(conform_green) is None

    ac.record_green(conform_green, "cfp")
    report.gate_conform = "FAILED"
    report.gate_conform_green = False
    ac._record_conform_green(report, plan, "cfp", ac._Emitter())
    assert ac.read_green_record(conform_green) is None


@pytest.mark.parametrize("child_invalidates", [False, True])
def test_cycle_conform_nonzero_exit_cannot_publish_passing_summary(monkeypatch, tmp_path, child_invalidates):
    green = tmp_path / "conform-green.json"
    summary = tmp_path / "conform_summary.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", green)
    monkeypatch.setattr(cycle_paths, "CONFORM_SUMMARY", summary)
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    _patch_build_chain(monkeypatch)
    ac.record_green(green, "cfp")

    def child(name, argv, **kwargs):
        assert name == "gate:conform"
        summary.write_text(json.dumps({"divergences": 0, "pass": True}))
        if child_invalidates:
            ac.clear_contradicted_green(green, "cfp")
        return _step(name, 9)

    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(record_greens=True, fresh=True),
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=child,
    )

    assert rc == 1
    assert report.gate_conform_green is False
    assert report.gate_conform == "FAILED (exit 9)"
    assert not green.exists()


def test_classify_rebuild_recordable_whenever_it_is_green():
    """A green run is always recordable; only a failure withholds the record."""
    clean = ac.classify_rebuild_output("", 0, "rebuild-contracts")
    assert clean.status == "green"
    assert clean.recordable
    hard = ac.classify_rebuild_output("FAILED rebuild/test_settle.py::test_x", 1, "rebuild-contracts")
    assert not hard.recordable


_ACCEPTED_PINS = {
    "invariant": {
        "classes": ["boundary-window", "bare-name-live-join"],
        "machine_approved_classes": ["boundary-window", "bare-name-live-join"],
        "no_verdict_classes": ["boundary-window"],
        "unmatched_groups": ["no-chain-gains"],
    },
    "volatile": {"audit": {"row_count": 10, "units": 4}},
}

_LEDGER_YAML = """- id: boundary-window
  no_verdict: true
- id: bare-name-live-join
  ink_identical: true
- id: vie-baseline-entry-extension-dropped
  no_verdict: true
"""


def _review_facts_fixture(monkeypatch, tmp_path, *, accepted, current):
    """The review-facts step's inputs outside the real tree: the pins file standing in for what the refresh child just wrote, the index copy the step compares it with, and a three-entry ledger for the ledger-coverage line."""
    pins = tmp_path / "review-facts-pins.json"
    pins.write_text(json.dumps(current, indent=2) + "\n")
    ledger = tmp_path / "m1-divergences.yaml"
    ledger.write_text(_LEDGER_YAML)
    monkeypatch.setattr(ac, "FACTS_PINS", pins)
    monkeypatch.setattr(ac, "DIVERGENCE_LEDGER", ledger)
    monkeypatch.setattr(ac, "accepted_facts", lambda: accepted)


def _moved_invariant() -> dict:
    return {
        "invariant": {
            "classes": ["boundary-window", "bare-name-live-join", "vie-baseline-entry-extension-dropped"],
            "machine_approved_classes": ["boundary-window", "bare-name-live-join"],
            "no_verdict_classes": ["boundary-window", "vie-baseline-entry-extension-dropped"],
            "unmatched_groups": ["no-chain-gains", "deferred-ss10"],
        },
        "volatile": {"audit": {"row_count": 12, "units": 5}},
    }


def test_the_invariant_diff_prints_under_the_review_facts_step_in_full(tmp_path, capsys, monkeypatch):
    """When the invariant block moved, its diff is what a commit of the pins accepts, so it is printed in full: verbatim, with no step column in front so it can be copied, and without the volatile hunks. The diff is a substep of review-facts, so its lines go to that step's log and column, with no second banner and no log of its own."""
    plan = _plan()
    log_dir = tmp_path / "logs"
    registry = ac._ChildRegistry()
    report = ac.CycleReport()
    _review_facts_fixture(monkeypatch, tmp_path, accepted=_ACCEPTED_PINS, current=_moved_invariant())

    def spawn(name, argv, *, emit, registry, stream, **passthrough):
        return ac._run_step(name, [sys.executable, "-c", "pass"], emit=emit, registry=registry, stream=stream)

    with console.CycleConsole(steps=[step.name for step in plan.steps], log_dir=log_dir) as cycle_console:
        ac._do_review_facts(report, spawn=spawn, emit=cycle_console, registry=registry, plan=plan)
        assert cycle_console._open == {}

    out = capsys.readouterr().out
    assert '+    "deferred-ss10"' in out.splitlines()
    assert '+    "vie-baseline-entry-extension-dropped"' in out.splitlines()
    assert "row_count" not in out
    assert out.count("---- step ") == 1
    assert report.facts_status.startswith("invariant moved: ")
    facts_log = (log_dir / "01-review-facts.log").read_text()
    assert '+    "deferred-ss10"' in facts_log.splitlines()
    assert not (log_dir / "00-invariant-diff.log").exists()


def test_the_summary_table_carries_each_steps_detail_and_what_it_cost():
    """The summary table shows, per step, the outcome, the step's detail, and the seconds it took. A step that did not run shows no detail; otherwise a run_m1 the plan skipped would report the last build's unmatched count as this pass's. A failed gate's detail keeps only what the outcome column does not say: `FAILED  3 unexplained`, not `FAILED  FAILED (3 unexplained)`. The retention row reads `skipped` when the plan ruled it out and `not run` when a failure or a stop signal ended the pass before `_finish` reached it."""
    plan = _plan(skip_conform=True, conform_note=ac.CONFORM_SKIP_NOTE)
    report = ac.CycleReport()
    report.unmatched = 8423
    report.pins_pass = True
    report.corpus_units = 15903
    report.corpus_rows = 81894
    report.gate_conform = f"skipped ({ac.CONFORM_SKIP_NOTE})"
    report.gate_contracts = "FAILED (3 unexplained)"
    report.gate_contracts_green = False
    report.retention_detail = "removed 1 carried, 0 build logs, 0 stashes; journal intact"
    report.step_seconds = {"run_m1": 1988.0, "corpus-build": 61.0, "gate:rebuild-contracts": 92.0}
    report.step_returncodes = {"run_m1": 0, "corpus-build": 0, "gate:rebuild-contracts": 1}

    rows = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert rows["run_m1"].detail == "8,423 unmatched, pins pass"
    assert rows["run_m1"].outcome == "ok"
    assert rows["run_m1"].seconds == 1988.0
    assert rows["corpus-build"].detail == "15,903 units, 81,894 rows"
    assert rows["gate:conform"].outcome == "skipped"
    assert rows["gate:conform"].detail == ""
    assert rows["gate:rebuild-contracts"].outcome == "FAILED"
    assert rows["gate:rebuild-contracts"].detail == "3 unexplained"
    assert rows["gate:js"].outcome == "not run"
    assert rows["retention"].outcome == "not run"
    assert all(row.number is None for row in ac.summary_rows(report, plan, retention_ran=False))
    swept = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=True)}
    assert swept["retention"].outcome == "ok"
    assert swept["retention"].detail == report.retention_detail

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
    assert reused["run_m1"].detail == ""

    rerun = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    rerun_report = ac.CycleReport()
    ac._timed_spawn(lambda name, argv, **kw: ac._StepResult(name, 0, "", "", 4.0), rerun_report)(
        ac.RUN_M1_GATES_ONLY_STEP,
        ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--gates-only"],
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        stream=False,
    )
    reran = {row.name: row for row in ac.summary_rows(rerun_report, rerun, retention_ran=False)}
    assert reran["run_m1"].outcome == "ok"
    assert reran["run_m1"].seconds == 4.0


def test_a_step_that_came_back_nonzero_never_reads_as_an_ok_row():
    """The outcome column comes from each step's result (its exit status, or for run_m1 its gate result), not from whether the step took any time. Filled from seconds, it would read `ok` for every step that ran, including a run_m1 whose Manual pins failed and a corpus build whose child died. The two informational steps, review-facts and job-costs, are the exception: neither gates anything, and each reports its failure in its own detail."""
    plan = _plan()
    report = ac.CycleReport()
    report.unmatched = 5
    report.pins_pass = False
    report.run_m1_failed = True
    report.facts_status = "update FAILED (exit 2) — informational"
    report.job_costs_status = "OVERRUN (a measured peak outruns its checked-in constant)"
    report.step_seconds = {"run_m1": 9.0, "corpus-build": 3.0, "review-facts": 1.0, "job-costs": 1.0}
    report.step_returncodes = {"run_m1": 0, "corpus-build": 1, "review-facts": 2, "job-costs": 1}

    rows = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert rows["run_m1"].outcome == "FAILED"
    assert rows["run_m1"].detail == "5 unmatched, PINS FAILED"
    assert rows["corpus-build"].outcome == "FAILED"
    assert rows["review-facts"].outcome == "ok"
    assert rows["job-costs"].outcome == "ok"


def test_every_spawned_step_closes_with_its_own_detail_and_peak(capsys, tmp_path):
    """Each spawned step's closing line carries the detail its summary row will show and its peak memory. No stage knows its detail when its child exits (run_m1 reads three summaries, the corpus build opens a manifest, the verdict update splits its sections), so the closing line is written by the stage that reads them."""
    corpus = _built_corpus(tmp_path, units=15903, rows=81894, batches=16, duplicate_groups=402)

    def spawn(name, argv, *, emit, registry, stream, **passthrough):
        if name == "run_m1":
            for key, payload in _pass_summaries().items():
                cycle_paths.M1_SUMMARY_FILES[key].write_text(json.dumps(payload))
            return ac._StepResult(name, 0, "", "", 1988.0, 19_600_000_000)
        return ac._StepResult(name, 0, "", "", 1.0, 1_000_000_000)

    plan = _plan(skip_gates=True, review_out=corpus)
    report = ac.CycleReport()
    cycle_console = console.CycleConsole(steps=[step.name for step in plan.steps])
    assert ac._run_cycle(plan, report, cycle_console, ac._ChildRegistry(), spawn=spawn) == 0

    closing = [line for line in capsys.readouterr().out.splitlines() if "  cycle " in line]
    assert any(line.endswith("ok  8,423 unmatched, pins pass  rss 19.6G") for line in closing), closing
    assert any("ok  15,903 units, 81,894 rows  rss 1.0G" in line for line in closing), closing


def test_do_review_facts_names_the_invariant_movement_and_reports_ledger_coverage(monkeypatch, tmp_path):
    """The review-facts status names the invariant movement: which classes appeared, and which no-verdict exemptions and unmatched groups came with them. Beside it, the ledger-coverage line compares the ledger's declarations with the classes the corpus reached. Both go to the cycle log and to cycle_summary.json."""
    _review_facts_fixture(monkeypatch, tmp_path, accepted=_ACCEPTED_PINS, current=_moved_invariant())
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 0)

    report = ac.CycleReport()
    ac._do_review_facts(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["review-facts"]
    assert report.facts_status == (
        "invariant moved: classes +1 (vie-baseline-entry-extension-dropped);"
        " no-verdict +1 (vie-baseline-entry-extension-dropped); unmatched groups +1 (deferred-ss10)"
        " — its diff is shown above; review it at commit time"
    )
    assert report.ledger_coverage == (
        "machine-approved: 2 classes approve units, 1 undeclared; ink-identical: 1 declared, all approving;"
        " no-verdict: 2 of 2 declared reached; ledger: 3 of 3 classes reached"
    )
    assert report.ledger_coverage_sets is not None
    assert report.ledger_coverage_sets["machine_approved_undeclared"] == ["boundary-window"]
    payload = ac.cycle_summary_payload(report, [], _plan(), "ok")
    assert payload["facts_status"] == report.facts_status
    assert payload["ledger_coverage"] == report.ledger_coverage
    assert payload["ledger_coverage_sets"] == report.ledger_coverage_sets


def test_do_review_facts_says_the_invariant_is_unchanged_when_only_the_volatile_block_moved(
    monkeypatch, tmp_path
):
    """When only the volatile block moved, as it does on nearly every letter batch, the status says the invariant is unchanged and no diff is printed. The totals are recorded in cycle_summary.json."""
    current = {**_ACCEPTED_PINS, "volatile": {"audit": {"row_count": 12, "units": 5}}}
    _review_facts_fixture(monkeypatch, tmp_path, accepted=_ACCEPTED_PINS, current=current)
    printed: list[str] = []
    emit = ac._Emitter()
    monkeypatch.setattr(emit, "emit", printed.append)

    report = ac.CycleReport()
    ac._do_review_facts(
        report,
        spawn=lambda name, argv, **kw: _step(name, 0),
        emit=emit,
        registry=ac._ChildRegistry(),
        plan=_plan(),
    )
    assert report.facts_status == (
        "invariant unchanged (only the volatile totals moved; cycle_summary.json carries the corpus's)"
    )
    assert not any(line.startswith(("---", "+++", "@@")) for line in printed)
    assert report.ledger_coverage.startswith("machine-approved: ")
    assert "unreached 1 (vie-baseline-entry-extension-dropped)" in report.ledger_coverage


def test_do_review_facts_says_so_when_the_refresh_moved_nothing(monkeypatch, tmp_path):
    _review_facts_fixture(
        monkeypatch, tmp_path, accepted=_ACCEPTED_PINS, current=json.loads(json.dumps(_ACCEPTED_PINS))
    )

    report = ac.CycleReport()
    ac._do_review_facts(
        report,
        spawn=lambda name, argv, **kw: _step(name, 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        plan=_plan(),
    )
    assert report.facts_status == "updated (matches the last accepted review facts)"


def test_do_review_facts_says_when_there_are_no_accepted_facts_to_hold_the_pins_against(
    monkeypatch, tmp_path
):
    """An untracked pins file, or no git, leaves the step nothing to compare with. The ledger-coverage line is still computed, since it needs only the ledger and the pins just written."""
    _review_facts_fixture(monkeypatch, tmp_path, accepted=None, current=_moved_invariant())

    report = ac.CycleReport()
    ac._do_review_facts(
        report,
        spawn=lambda name, argv, **kw: _step(name, 0),
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        plan=_plan(),
    )
    assert (
        report.facts_status
        == "updated (no accepted review facts to compare against: the pins are not in the index)"
    )
    assert report.ledger_coverage.startswith("machine-approved: ")


def test_do_review_facts_reports_a_failed_refresh_and_compares_nothing():
    """A refresh can fail on a corpus built before the review-facts sidecar existed. The step is informational, so a failure compares and records nothing; the next pass that rebuilds the corpus writes the sidecar."""
    calls: list[str] = []

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        return _step(name, 2, stderr="no review-facts.json beside the manifest")

    report = ac.CycleReport()
    ac._do_review_facts(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["review-facts"]
    assert report.facts_status == "update FAILED (exit 2) — informational"
    assert report.ledger_coverage == "not computed (the refresh failed)"


def test_a_failed_review_facts_refresh_never_fails_the_cycle(monkeypatch):
    def review_facts_dies(report, *, spawn, emit, registry, plan):
        report.facts_status = "update FAILED (exit 2) — informational"

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_review_facts", review_facts_dies)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert report.facts_status == "update FAILED (exit 2) — informational"
    assert json.loads(cycle_paths.CYCLE_SUMMARY.read_text())["failures"] == []


def test_a_staging_pass_never_runs_the_review_facts(monkeypatch, tmp_path):
    """The checked-in pins describe the live corpus. A staging pass builds elsewhere, so refreshing the pins from it would replace the accepted review facts with the facts of a corpus nobody serves."""

    def review_facts_must_not_run(*args, **kwargs):
        raise AssertionError("a staging pass must not run the review-facts step")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_review_facts", review_facts_must_not_run)

    plan = _plan(review_out=tmp_path / "staged")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert report.facts_status == "skipped (staging: the checked-in pins track the live corpus)"
    assert report.ledger_coverage == "skipped (staging)"


def test_do_job_costs_reports_a_clean_check():
    """When every measured unit's peak still fits its constant, the status says so in one line and nothing else is shown."""
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
    """When the check trips, the status quotes the tripped row's proposal and leaves out the proposal of a row that has only used its headroom. The step then asks `calibrate_budgets --moved` which constants differ from their values at `HEAD`, to learn whether one has already been re-measured in the working tree (so the commit in hand is already the acceptance), and the status names each one. Unlike the invariant diff, this one runs only on a trip."""
    calls: list[str] = []
    seen: dict[str, list[str]] = {}
    tripped = "CORPUS_WORKER_BYTES at 0.83 GB (max 0.66 GB × 1.25, rounded up to a multiple of 0.01 GB); width here at 0.83 GB: 8 at 0.83 GB each"
    used = "ORACLE_SHARD_BYTES at 1.00 GB (max 0.76 GB × 1.25, rounded up to a multiple of 0.10 GB), headroom used, not yet an overrun; width here at 1.00 GB: 43 at 1.00 GB each"

    def spawn(name, argv, *, emit, registry, stream):
        calls.append(name)
        seen[name] = argv
        if name == "job-costs":
            return _step(
                name,
                1,
                stdout=f"  OVERRUN   : max 0.70 GB exceeds the constant by 6%\n  proposal  : {tripped}\n  proposal  : {used}\n",
            )
        return _step(
            name,
            0,
            stdout="DELTA_PEAK_BYTES: 5.50 GB at HEAD, 6.50 GB in the working tree\nTABLE_BUILD_PEAK_BYTES: 19.00 GB at HEAD, 21.00 GB in the working tree\n",
        )

    report = ac.CycleReport()
    ac._do_job_costs(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert calls == ["job-costs", "job-costs-diff"]
    assert seen["job-costs-diff"] == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.calibrate_budgets",
        "--moved",
    ]
    assert report.job_costs_status.startswith("OVERRUN")
    assert "that commit is the acceptance" in report.job_costs_status
    assert report.job_costs_status.endswith(
        f" — proposal: {tripped} — DELTA_PEAK_BYTES and TABLE_BUILD_PEAK_BYTES have already moved in the working tree"
    )
    assert "ORACLE_SHARD_BYTES" not in report.job_costs_status
    assert report.job_costs_ok is False


def test_a_tripped_check_over_an_unmoved_tree_says_only_that_it_tripped():
    """The already-moved clause depends on the constants' values, not on the trip or on other edits to their files: with the constants unchanged and a docstring in one of those files rewritten, the status must not suggest that the acceptance is already drafted."""

    def spawn(name, argv, *, emit, registry, stream):
        if name == "job-costs":
            return _step(name, 1)
        return _step(name, 0, stdout='-    """Old docstring."""\n+    """New docstring."""\n')

    report = ac.CycleReport()
    ac._do_job_costs(report, spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=_plan())
    assert report.job_costs_status.startswith("OVERRUN")
    assert "working tree" not in report.job_costs_status
    assert report.job_costs_ok is False


def test_do_job_costs_reports_a_broken_check_without_diffing():
    """Exit 1 means the tool found an overrun; any other nonzero exit means the tool itself failed. Then there is nothing to diff, and `job_costs_ok` stays None, which is neither green nor an overrun."""
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
    """A stale divisor makes a pool the wrong width but cannot make an artifact wrong. So a trip shows in the summary and the payload and adds nothing to the failure list of a pass whose artifacts are green."""

    def job_costs_trips(report, *, spawn, emit, registry, plan):
        report.job_costs_status = "OVERRUN (a measured peak outruns its checked-in constant)"
        report.job_costs_ok = False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_job_costs", job_costs_trips)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    summary = json.loads(cycle_paths.CYCLE_SUMMARY.read_text())
    assert summary["failures"] == []
    assert summary["job_costs_status"].startswith("OVERRUN")
    assert summary["job_costs_ok"] is False
    assert "job_costs" not in summary["gates"]


def test_the_job_costs_check_runs_in_a_staging_pass_too(monkeypatch, tmp_path):
    """The job-costs check runs in a staging pass, unlike the review-facts step. The pins track the live corpus, which a staging pass never writes, but every pass appends to the timings journal, and a staging pass's pool measurements are as valid as any."""
    ran: list[str] = []

    def job_costs_ran(report, *, spawn, emit, registry, plan):
        ran.append(plan.argv("job-costs")[-1])
        report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
        report.job_costs_ok = True

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_job_costs", job_costs_ran)

    plan = _plan(review_out=tmp_path / "staged")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert plan.runs("job-costs") is True
    assert ran == ["--check"]
    assert report.job_costs_ok is True


def test_the_plan_checks_job_costs_after_the_gates():
    """The check reads a journal that this pass's pools append to when they finish, so it runs after the gates join. Placed beside the review-facts step, it would report the previous pass's measurements."""
    plan = _plan()
    names = [step.name for step in plan.steps]
    by_name = {step.name: step for step in plan.steps}
    assert names.index("job-costs") > names.index("gate:rebuild-contracts")
    assert names.index("job-costs") > names.index("review-facts")
    # Retention is a plan step but runs inside _finish, after this check, and the printed plan must list steps in the order they run.
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
    """The step is never skipped: --skip-gates suppresses only the `gate:` steps, and this step, like the review-facts step, is not one of them."""
    assert _plan(skip_gates=True).runs("job-costs") is True


def test_the_contracts_suite_is_submitted_before_the_corpus_build_starts(monkeypatch):
    """The contracts suite is submitted after the run_m1 gate passes and before the corpus build starts, so it runs beside the build. The corpus fake waits until the suite's task has been invoked, which a submission after the build could never satisfy. The suite waits for nothing else, because the corpus, the carry, the merge and the review facts are not inputs to it; `test_the_rebuild_suite_is_skipped_when_run_m1_fails` checks the other bound."""
    contracts_invoked = threading.Event()
    order: list[str] = []

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        assert not contracts_invoked.is_set()
        order.append("run_m1")
        return _run_m1_green()

    def fake_contracts(
        pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
    ):
        order.append("contracts")
        contracts_invoked.set()
        return _lane_result("rebuild-contracts")

    def corpus_after(report, *, spawn, emit, registry, review_out, **_):
        assert contracts_invoked.wait(timeout=30)
        order.append("corpus")
        report.corpus_units = 1
        return True

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    monkeypatch.setattr(ac, "_do_corpus_build", corpus_after)

    plan = _plan(pool_policy="overlap")
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 0
    assert order == ["run_m1", "contracts", "corpus"]
    assert report.gate_contracts == "green"


def test_corpus_build_failure_still_joins_the_rebuild_suite_it_started(monkeypatch, capsys):
    """A failed corpus build stops the build lane, but the suite was already submitted and is running on a pool worker. The pass joins it and reports its real result; reporting it as not run would be false and would leave the worker unjoined."""
    calls = {"contracts": 0}

    def fake_contracts(
        pool_policy, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False
    ):
        calls["contracts"] += 1
        return _lane_result("rebuild-contracts")

    def failing_corpus(report, *, spawn, emit, registry, review_out, **_):
        return False

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", failing_corpus)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", fake_contracts)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert calls == {"contracts": 1}
    assert report.gate_contracts == "green"
    assert "corpus rebuild failed" in capsys.readouterr().out


def test_run_m1_failure_still_leaves_the_rebuild_suite_not_run(monkeypatch, capsys):
    """A run_m1 failure returns before the suite is submitted, so the gate reports why it did not run."""

    def failing_run_m1(report, *, spawn, emit, registry, **_):
        return _run_m1_red("Manual-pin gate failed (2 disagreements)")

    def must_not_run(*args, **kwargs):
        raise AssertionError("no rebuild lane may be submitted when run_m1's gate fails")

    monkeypatch.setattr(ac, "_do_run_m1", failing_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", must_not_run)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert rc == 1
    assert report.gate_contracts == "not run (run_m1 gate failed)"
    assert "Manual-pin gate failed" in capsys.readouterr().out


def test_verdict_update_skip_fingerprint_moves_with_every_input(tmp_path):
    """The verdict-update key moves with every input. The standing approvals are hashed by raw bytes here, although the rebuild lanes give them a prose-insensitive hash: the fill copies each rule's `note` into the verdict note it writes, so a reworded note changes what the verdict update writes and must re-run it."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    master = tmp_path / "verdicts-autosave.json"
    master.write_text("{}")
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")

    base = ac.verdict_update_skip_fingerprint(tmp_path, corpus, master)
    assert base is not None
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) == base
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, None) is None

    master.write_text('{"verdicts": []}')
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != base

    master.write_text("{}")
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: [{}]\n")
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != base

    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text(
        "rules:\n  - id: r1\n    verdict: approve\n    note: one\n"
    )
    noted = ac.verdict_update_skip_fingerprint(tmp_path, corpus, master)
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text(
        "rules:\n  - id: r1\n    verdict: approve\n    note: two, at greater length\n"
    )
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != noted

    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-18T00:00:00Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != base

    (corpus / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-17T20:24:44Z",
                "inputs_fingerprint": {"runes": "aaa", "static": "refreshed"},
            }
        )
    )
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) == base

    (corpus / "manifest.json").write_text(json.dumps({"generated_at": "2026-07-17T20:24:44Z"}))
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) is None


def test_verdict_update_skip_fingerprint_covers_its_own_code(tmp_path):
    """The verdict-update key covers the verdict update's own code, which lives in rebuild/tools/, where no other fingerprint reads it. Without it, a fix to a fill's matcher would be skipped as already checked. artifact_cycle.py, cycle_timings.py, memory_budget.py and peak_rss.py share that directory but run no step of the verdict update, so editing one leaves the key unchanged; serve.py and review_server.py, which the verdict update imports, move it, and so does landing.py, which puts the prepared store in place."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    master = tmp_path / "verdicts-autosave.json"
    master.write_text("{}")
    tools = tmp_path / "rebuild" / "tools"
    tools.mkdir(parents=True)
    (tmp_path / "rebuild" / "review").mkdir()
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")
    for name in ("duplicate_verdicts.py", "standing_verdicts.py", "carry_verdicts.py"):
        (tools / name).write_text("x = 1\n")
    (tmp_path / "rebuild" / "review" / "serve.py").write_text("y = 1\n")

    base = ac.verdict_update_skip_fingerprint(tmp_path, corpus, master)
    assert base is not None
    for edited in (
        tools / "duplicate_verdicts.py",
        tools / "standing_verdicts.py",
        tools / "carry_verdicts.py",
    ):
        original = edited.read_text()
        edited.write_text("x = 2\n")
        assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != base, edited.name
        edited.write_text(original)
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) == base

    (tmp_path / "rebuild" / "review" / "serve.py").write_text("y = 2\n")
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != base

    outside = ("artifact_cycle.py", "cycle_timings.py", "memory_budget.py", "peak_rss.py")
    for name in outside:
        (tools / name).write_text("x = 1\n")
    unmoved = ac.verdict_update_skip_fingerprint(tmp_path, corpus, master)
    for name in outside:
        (tools / name).write_text("x = 2\n")
        assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) == unmoved, name
        (tools / name).write_text("x = 1\n")

    (tools / "review_server.py").write_text("x = 1\n")
    probed = ac.verdict_update_skip_fingerprint(tmp_path, corpus, master)
    (tools / "review_server.py").write_text("x = 2\n")
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != probed

    landing_module = tmp_path / "rebuild" / "review" / "landing.py"
    landing_module.write_text("z = 1\n")
    landed = ac.verdict_update_skip_fingerprint(tmp_path, corpus, master)
    landing_module.write_text("z = 2\n")
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, master) != landed


def test_verdict_update_skip_fingerprint_sees_a_master_that_is_not_the_autosave(tmp_path):
    """The master is in the key because the autosave's hash cannot see it: an export at the repo root can outrank the store in the auto-resolution and hold verdicts the store has never had."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    (tmp_path / "verdicts-autosave.json").write_text("{}")
    export = tmp_path / "verdicts-export.json"
    export.write_text('{"verdicts": [1]}')
    before = ac.verdict_update_skip_fingerprint(tmp_path, corpus, export)
    export.write_text('{"verdicts": [1, 2]}')
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, export) != before


def test_verdict_update_skip_fingerprint_reads_the_stores_records_not_its_bytes(tmp_path):
    """The review server rewrites the store with a new `exported_at` on every save, and it lays the records out one per line where the merge indents them. Neither moves the key, and a changed record does. A master that resolved to the store is named `autosave` in the key, so spelling its path another way changes nothing."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    (tmp_path / "rebuild").mkdir()
    (tmp_path / "rebuild" / "standing-approvals.yaml").write_text("rules: []\n")
    autosave = tmp_path / "verdicts-autosave.json"
    records = [
        {"unit": "u-2", "verdict": "reject", "note": "", "at": "2026-07-17T21:00:00Z"},
        {"unit": "u-1", "verdict": "approve", "note": "", "at": "2026-07-17T21:00:00Z"},
    ]

    def store(exported_at, verdicts, indent=None):
        document = {
            "format": "ams-review-verdicts/1",
            "manifest_generated_at": "2026-07-17T20:24:44Z",
            "exported_at": exported_at,
            "verdicts": verdicts,
        }
        autosave.write_text(json.dumps(document, indent=indent))

    store("2026-07-17T21:00:00Z", records, indent=2)
    base = ac.verdict_update_skip_fingerprint(tmp_path, corpus, autosave)
    store("2026-07-17T22:30:00Z", list(reversed(records)))
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, autosave) == base
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, corpus / ".." / autosave.name) == base
    store("2026-07-17T22:30:00Z", [records[0], {**records[1], "verdict": "either"}])
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, autosave) != base


def test_the_key_a_pass_records_over_its_landed_store_is_the_next_plans_key(tmp_path):
    """After a land that laid nothing over the prepared store, the cycle keys the green on the store the land wrote (`store=`), and the next plan keys the live store: the two match while no save has changed a record, whatever `exported_at` each carries, and a save made after the land moves the next plan's key. The master that resolved to the live store is named `autosave` both times."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-07-17T20:24:44Z", "inputs_fingerprint": {"runes": "aaa"}})
    )
    autosave = tmp_path / "verdicts-autosave.json"
    landed = tmp_path / "var" / "cycle" / "run" / ac.landing.LANDED_NAME
    landed.parent.mkdir(parents=True)
    record = {"unit": "u-1", "verdict": "approve", "note": "", "at": "2026-07-17T21:00:00Z"}

    def document(exported_at, verdicts):
        return json.dumps(
            {
                "format": "ams-review-verdicts/1",
                "manifest_generated_at": "2026-07-17T20:24:44Z",
                "exported_at": exported_at,
                "verdicts": verdicts,
            }
        )

    landed.write_text(document("2026-07-17T21:00:00Z", [record]))
    autosave.write_text(document("2026-07-17T23:00:00Z", [record]))
    recorded = ac.verdict_update_skip_fingerprint(tmp_path, corpus, autosave, store=landed)
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, autosave) == recorded
    autosave.write_text(document("2026-07-17T23:00:00Z", [{**record, "verdict": "reject"}]))
    assert ac.verdict_update_skip_fingerprint(tmp_path, corpus, autosave) != recorded


def test_dry_run_plan_skip_verdict_update_replaces_the_whole_step():
    plan = _plan(skip_verdict_update=True, verdict_update_note=ac.VERDICT_UPDATE_SKIP_NOTE)
    assert plan.carry_out is None
    by_name = {step.name: step for step in plan.steps}
    assert by_name["verdict-update"].argv is None
    assert by_name["verdict-update"].note == f"SKIPPED ({ac.VERDICT_UPDATE_SKIP_NOTE})"
    assert plan.complaints_note == ac.VERDICT_UPDATE_SKIP_NOTE
    assert by_name["review-facts"].argv is not None


def test_dry_run_plan_direct_merge_merges_the_master():
    """When the corpus did not move, the carry would map every unit onto itself and keep each record's `at`, which is all the merge ranks by. So the verdict update skips the carry and merges the master directly: it is the one input the store's own hash cannot see."""
    plan = _plan(direct_merge=True)
    by_name = {step.name: step for step in plan.steps}
    assert plan.carry_out is None
    argv = _argv(by_name["verdict-update"])
    assert "--verdicts" not in argv
    assert argv[argv.index("--merge-master") + 1] == "v.json"
    assert "--no-merge" not in argv
    assert plan.do_merge
    assert "the carry is the identity" in by_name["verdict-update"].note


def test_dry_run_plan_direct_merge_still_honors_no_merge():
    plan = _plan(direct_merge=True, no_merge=True)
    by_name = {step.name: step for step in plan.steps}
    assert "--no-merge" in _argv(by_name["verdict-update"])
    assert not plan.do_merge


def test_the_direct_merge_report_still_names_the_fullest_verdicts_file(tmp_path, monkeypatch):
    carried = tmp_path / "verdicts-carried-abc.json"
    carried.write_text("{}")
    monkeypatch.setattr(ac, "fullest_verdicts_carry_out", lambda: carried)
    plan = _plan(direct_merge=True)
    report, failures = _run_verdict_update(plan, _verdict_update_stdout(*_FULL_VERDICT_UPDATE[1:]))
    assert failures == []
    assert report.carry_out == carried


def test_fullest_verdicts_carry_out_derives_the_stamp_aligned_fullest_verdicts_file_from_disk(
    tmp_path, monkeypatch
):
    """The summary names the fullest verdicts file by deriving it from disk the way its readers do, not from the verdict-update green record: a later export with more effective verdicts outranks the file the last recorded pass wrote."""
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
    assert ac.fullest_verdicts_carry_out() == tmp_path / "verdicts-export.json"
    (review / "manifest.json").write_text("not json")
    assert ac.fullest_verdicts_carry_out() is None


def test_run_cycle_never_spawns_the_verdict_update_when_skipped(monkeypatch, tmp_path):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the verdict-update skip path must spawn nothing")

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(ac, "_do_verdict_update", must_not_run)
    monkeypatch.setattr(cycle_paths, "VERDICT_UPDATE_GREEN", tmp_path / "verdict-update-green.json")

    carried = tmp_path / "verdicts-carried-abc.json"
    carried.write_text("{}")
    monkeypatch.setattr(ac, "fullest_verdicts_carry_out", lambda: carried)
    plan = _plan(
        skip_verdict_update=True,
        verdict_update_note=ac.VERDICT_UPDATE_SKIP_NOTE,
        record_greens=True,
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())
    assert rc == 0
    note = f"skipped ({ac.VERDICT_UPDATE_SKIP_NOTE})"
    assert report.merge_status == note
    assert report.duplicate_fill_status == note
    assert report.standing_merge_status == note
    assert report.complaints_status == note
    assert report.carry_out == carried
    assert not (tmp_path / "verdict-update-green.json").exists()


def test_run_cycle_records_the_verdict_update_green_only_after_a_complete_run(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    green = tmp_path / "verdict-update-green.json"
    monkeypatch.setattr(cycle_paths, "VERDICT_UPDATE_GREEN", green)
    monkeypatch.setattr(
        ac, "verdict_update_skip_fingerprint", lambda root=None, corpus=None, master=None, store=None: "plu"
    )

    plan = _plan(record_greens=True)
    rc = ac._run_cycle(
        plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step()
    )
    assert rc == 0
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "plu"
    assert record["format"] == "ams-verdict-update-green/1"

    green.unlink()

    def complaints_broken(report, *, spawn, emit, registry, plan):
        _verdict_update_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        report.complaints_status = "FAILED (exit 2) — informational"
        report.complaints_ok = False
        return []

    monkeypatch.setattr(ac, "_do_verdict_update", complaints_broken)
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

    monkeypatch.setattr(ac, "_do_verdict_update", standing_merge_fails)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 1
    assert not green.exists()


def test_run_cycle_records_no_verdict_update_green_without_schedule_completion(monkeypatch, tmp_path):
    """The verdict-update green requires completion of the duplicate, standing, duplicate schedule. A missing completion makes the next pass run the update again, even when its step statuses look successful."""

    def incomplete(report, *, spawn, emit, registry, plan):
        _verdict_update_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        report.verdict_update_complete = False
        return []

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_build_chain(monkeypatch)
    _patch_gate_fingerprints(monkeypatch)
    monkeypatch.setattr(
        ac, "verdict_update_skip_fingerprint", lambda root=None, corpus=None, master=None, store=None: "plu"
    )
    green = tmp_path / "verdict-update-green.json"
    monkeypatch.setattr(cycle_paths, "VERDICT_UPDATE_GREEN", green)

    monkeypatch.setattr(ac, "_do_verdict_update", incomplete)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert not green.exists()

    monkeypatch.setattr(ac, "_do_verdict_update", _verdict_update_ok)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda *a, **k: _step(),
    )
    assert rc == 0
    assert green.exists()


def test_verdict_update_completed_requires_its_own_completion_line():
    report = ac.CycleReport()
    assert ac._verdict_update_completed(report) is False
    report, _failures = _run_verdict_update(
        _plan(), _verdict_update_stdout(*_FULL_VERDICT_UPDATE, complete=False)
    )
    assert ac._verdict_update_completed(report) is False
    report, _failures = _run_verdict_update(_plan(), _verdict_update_stdout(*_FULL_VERDICT_UPDATE))
    assert ac._verdict_update_completed(report) is True


def test_verdict_update_completion_requires_a_successful_child():
    report, failures = _run_verdict_update(
        _plan(), _verdict_update_stdout(*_FULL_VERDICT_UPDATE), returncode=3
    )
    assert failures == ["the verdict update failed (exit 3)"]
    assert ac._verdict_update_completed(report) is False


@pytest.mark.parametrize("returncode", [0, 1])
def test_verdict_update_completion_rejects_a_failed_phase(returncode):
    stdout = _verdict_update_stdout(
        *_FULL_VERDICT_UPDATE[:7],
        ("duplicate-merge-2", ["boom"]),
        failed="duplicate-merge-2",
    )
    stdout += console.COMPLETE_LINE + "duplicate, standing, duplicate\n"
    report, failures = _run_verdict_update(_plan(), stdout, returncode=returncode)
    assert failures == ["duplicate-merge round 2 failed"]
    assert ac._verdict_update_completed(report) is False


def test_verdict_update_completion_rejects_failed_complaints():
    stdout = _verdict_update_stdout(*_FULL_VERDICT_UPDATE[:-1], ("complaints", ["boom"]), failed="complaints")
    stdout += console.COMPLETE_LINE + "duplicate, standing, duplicate\n"
    report, failures = _run_verdict_update(_plan(), stdout, returncode=2)
    assert failures == []
    assert report.complaints_ok is False
    assert report.complaints_status == "FAILED (exit 2) — informational"
    assert ac._verdict_update_completed(report) is False


def test_verdict_update_completion_line_stays_out_of_the_complaints_section():
    sections = ac.verdict_update_sections(_verdict_update_stdout(*_FULL_VERDICT_UPDATE))
    assert sections["complaints"] == ["no open complaints", "[t] complaints 0.1s"]


def test_verdict_update_without_complaints_can_complete():
    plan = _plan()
    assert "--no-complaints" in plan.argv("verdict-update")
    report, failures = _run_verdict_update(plan, _verdict_update_stdout(*_FULL_VERDICT_UPDATE[:-1]))
    assert failures == []
    assert ac._verdict_update_completed(report) is True
    assert report.complaints_ok is None


def _settled_repo(tmp_path, monkeypatch):
    """A repo whose run_m1 and corpus build both auto-skip: the converged pass, the only case the verdict-update skip is offered on."""
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(cycle_paths.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "corpus_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "no-match")
    monkeypatch.setattr(
        cycle_paths, "VERDICT_UPDATE_GREEN", tmp_path / "rebuild" / "out" / "verdict-update-green.json"
    )
    monkeypatch.setattr(
        ac, "verdict_update_skip_fingerprint", lambda root=None, corpus=None, master=None, store=None: "plu"
    )


def test_main_skips_the_verdict_update_on_a_matching_record(tmp_path, monkeypatch, capsys):
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"SKIPPED ({ac.VERDICT_UPDATE_SKIP_NOTE})" in _step_lines(out, "verdict-update")

    ac.record_verdict_update_green("moved")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    row = _step_lines(out, "verdict-update")
    assert "uv run python -m rebuild.tools.verdict_update" in row
    assert "--merge-master" in row


def test_main_runs_the_review_facts_on_the_pass_that_skips_the_verdict_update(tmp_path, monkeypatch, capsys):
    """The review-facts step always runs, even on a pass that skips the whole verdict update, because reading the sidecar and rewriting one small file is cheap."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"SKIPPED ({ac.VERDICT_UPDATE_SKIP_NOTE})" in _step_lines(out, "verdict-update")
    assert "uv run python -m rebuild.review.facts --update" in _step_lines(out, "review-facts")


def test_main_never_skips_the_verdict_update_on_a_pass_that_writes_the_corpus(tmp_path, monkeypatch, capsys):
    """The verdict-update skip is offered only when the corpus build is skipped, because only then is the manifest stamp the verdict update keys on known not to change during the pass."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cycle_paths, "VERDICT_UPDATE_GREEN", tmp_path / "rebuild" / "out" / "verdict-update-green.json"
    )
    monkeypatch.setattr(
        ac, "verdict_update_skip_fingerprint", lambda root=None, corpus=None, master=None, store=None: "plu"
    )
    ac.record_verdict_update_green("plu")
    assert ac.main(["--dry-run"]) == 0
    assert ac.VERDICT_UPDATE_SKIP_NOTE not in capsys.readouterr().out


def test_main_never_skips_the_verdict_update_under_fresh_or_a_partial_run(tmp_path, monkeypatch, capsys):
    """--fresh, --no-merge, --no-carry, a staging pass and --carry-out each disable the verdict-update skip. --carry-out is on the list because the skip writes no carried file, so the flag could not be honored."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    for argv in (
        ["--dry-run", "--fresh"],
        ["--dry-run", "--no-merge"],
        ["--dry-run", "--no-carry"],
        ["--dry-run", "--review-out", str(tmp_path / "staged")],
        ["--dry-run", "--carry-out", str(tmp_path / "carried.json")],
    ):
        assert ac.main(argv) == 0
        assert ac.VERDICT_UPDATE_SKIP_NOTE not in capsys.readouterr().out


def test_main_carries_a_master_stamped_for_another_corpus_instead_of_merging_it(
    tmp_path, monkeypatch, capsys
):
    """The direct merge passes the master to the merge unchanged, and the merge refuses any input stamped for another corpus, so the direct merge is planned only for a master stamped for the served corpus. A pass stopped after the corpus build and before the carry leaves the autosave stamped for the previous corpus, and the next pass skips the build as unchanged; that pass plans the full carry, as does a pass given such a master by --verdicts. For the auto-resolved master, alignment comes from the resolution, whose line already names the older stamp, so the master is not parsed again and the direct-merge decline note is not printed. A --verdicts master is checked with `master_stamped_for_corpus`, and the note says why its carry runs. Once the autosave is restamped for the served corpus, the direct merge is planned again."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("moved")
    served = "2026-07-17T20:24:44Z"
    older = "2026-07-10T00:00:00Z"
    autosave = tmp_path / "verdicts-autosave.json"
    autosave.write_text(json.dumps(_verdicts_doc(older, ["u-1"])))
    export = tmp_path / "masters" / "verdicts-export.json"
    export.parent.mkdir()
    export.write_text(json.dumps(_verdicts_doc(older, ["u-2"])))
    stamped_for_corpus = ac.master_stamped_for_corpus
    asked: list[Path] = []

    def asking(master, corpus):
        asked.append(Path(master))
        return stamped_for_corpus(master, corpus)

    monkeypatch.setattr(ac, "master_stamped_for_corpus", asking)

    outs = []
    for argv in (["--dry-run"], ["--dry-run", "--verdicts", str(export)]):
        assert ac.main(argv) == 0
        out = capsys.readouterr().out
        row = _step_lines(out, "verdict-update")
        assert "--verdicts" in row and "--carry-out" in row
        assert "--merge-master" not in row
        outs.append(out)
    resolved, named = outs
    assert f"stamped {older}, an older corpus than the served one" in resolved
    assert ac.DIRECT_MERGE_DECLINED_NOTE not in resolved
    assert ac.DIRECT_MERGE_DECLINED_NOTE in named
    assert asked == [export]

    autosave.write_text(json.dumps(_verdicts_doc(served, ["u-1"])))
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--merge-master" in _step_lines(out, "verdict-update")
    assert ac.DIRECT_MERGE_DECLINED_NOTE not in out
    assert asked == [export]

    corpus = tmp_path / "rebuild" / "out" / "review"
    assert stamped_for_corpus(autosave, corpus)
    assert not stamped_for_corpus(export, corpus)
    assert not stamped_for_corpus(tmp_path / "absent.json", corpus)


def _assets_only_repo(tmp_path, monkeypatch):
    """A settled repo whose only moved input is the copied review UI assets: the byte-identity check fails and the check that exempts the assets passes, which is the condition for the refresh step."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ac, "corpus_build_skippable", lambda root=None, review_out=None, ignore=(): bool(ignore)
    )


def test_main_refreshes_the_assets_when_only_the_static_component_moved(tmp_path, monkeypatch, capsys):
    """An app JS/CSS/HTML edit plans a copy and a restamp, not a corpus build. Downstream steps treat the pass as a skip: with a matching verdict-update record the verdict update is skipped too, because the manifest line in the verdict-update key leaves out the component the refresh rewrites."""
    _assets_only_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED (only the review UI assets moved" in _step_lines(out, "corpus-build")
    assert "uv run python -m rebuild.review.build refresh-assets" in _step_lines(out, "assets-refresh")
    assert f"SKIPPED ({ac.VERDICT_UPDATE_SKIP_NOTE})" in _step_lines(out, "verdict-update")

    ac.record_verdict_update_green("moved")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "uv run python -m rebuild.review.build refresh-assets" in _step_lines(out, "assets-refresh")
    assert "--merge-master" in _step_lines(out, "verdict-update")


def test_main_plans_no_assets_refresh_when_the_corpus_already_matches(tmp_path, monkeypatch, capsys):
    """The byte-identity check comes first, so a corpus that already matches has nothing copied over it. --fresh bypasses both checks and runs a real build."""
    _settled_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "assets-refresh" not in out
    assert "the corpus already reflects these inputs byte for byte" in _step_lines(out, "corpus-build")

    monkeypatch.setattr(
        ac, "corpus_build_skippable", lambda root=None, review_out=None, ignore=(): bool(ignore)
    )
    assert ac.main(["--dry-run", "--fresh"]) == 0
    out = capsys.readouterr().out
    assert "assets-refresh" not in out
    assert "uv run python -m rebuild.review.build" in _step_lines(out, "corpus-build")


def test_server_can_keep_running_only_when_the_pass_writes_neither_of_the_apps_files():
    """The predicate depends on what the plan writes. A --no-carry pass, or a --no-merge carry over an unmoved corpus, keeps the review server running; any pass that writes the store (a direct merge included) or rewrites the corpus stops the review server."""
    assert ac.server_can_keep_running(skip_corpus=True, writes_store=False) is True
    assert ac.server_can_keep_running(skip_corpus=True, writes_store=True) is False
    assert ac.server_can_keep_running(skip_corpus=False, writes_store=False) is False
    assert ac.server_can_keep_running(skip_corpus=False, writes_store=True) is False


def _preflight_args(**overrides):
    kw = dict(review_out=None, yes=False, stop_server=False)
    kw.update(overrides)
    return argparse.Namespace(**kw)


def test_preflight_keeps_a_listening_review_server_running_for_a_pass_that_writes_nothing_under_it(
    monkeypatch, capsys
):
    """A pass that writes neither the corpus nor the store keeps a listening review server running for the whole run. This holds with or without --stop-server, which permits stopping a server but does not require it."""
    stops: list[int] = []
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: stops.append(1) or True)
    for args in (_preflight_args(), _preflight_args(stop_server=True)):
        assert ac._preflight(args, can_keep_running=True) is True
    assert stops == []
    assert ac.SERVER_KEEPS_RUNNING_NOTE in capsys.readouterr().out


def test_preflight_stops_the_server_for_a_writing_pass_only_when_allowed(monkeypatch, capsys):
    """For a pass that writes under the server, --stop-server stops it; `make review-cycle` passes that flag. Without the flag the pass refuses, because a bare run should not end someone's review session."""
    stops: list[int] = []
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: stops.append(1) or True
    )

    assert ac._preflight(_preflight_args(stop_server=True), can_keep_running=False) is True
    assert stops == [1]
    assert "Stopping the review server" in capsys.readouterr().out

    assert ac._preflight(_preflight_args(), can_keep_running=False) is False
    assert stops == [1]
    assert "REFUSING TO RUN" in capsys.readouterr().out


def test_preflight_refuses_when_the_stop_leaves_the_port_held(monkeypatch, capsys):
    """If the port is still held after the stop (something else serves 7294, or the server hung during shutdown), the pass refuses rather than write the corpus under a live reader."""
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: False)
    assert ac._preflight(_preflight_args(stop_server=True), can_keep_running=False) is False
    assert "still listening" in capsys.readouterr().out


def test_stop_review_server_waits_for_the_port_to_come_free(monkeypatch):
    """`stop_review_server` waits for the port to be free: pkill returns once the signal is delivered, and a corpus build must not start while the socket is still open."""
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


def test_main_keeps_the_review_server_running_on_the_settled_pass(tmp_path, monkeypatch, capsys):
    """End to end through `main`: the pass that skips the corpus and the verdict update keeps the review server running and never stops it."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: pytest.fail("stopped")
    )
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert ac.SERVER_KEEPS_RUNNING_NOTE in capsys.readouterr().out


def test_main_stops_a_server_from_before_the_land_protocol_when_the_pass_rebuilds_the_corpus(
    tmp_path, monkeypatch, capsys
):
    """A server with no /capabilities (the module's fixture answers 404) takes no part in the land, so a rebuilding pass stops it once with --stop-server and says that is the last stop."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    stops: list[int] = []
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: stops.append(1) or True
    )
    ran: list[ac.Plan] = []
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: ran.append(plan) or 0)
    assert ac.main(["--stop-server"]) == 0
    assert stops == [1]
    assert ran[0].legacy_server is True
    out = capsys.readouterr().out
    assert "Stopping the review server" in out and "the last stop a pass needs" in out


def test_main_keeps_the_review_server_running_for_an_assets_refresh_pass(tmp_path, monkeypatch, capsys):
    """An assets refresh moves no shard and no stamp, so the review server keeps running and the cycle tells the tab to reload onto the new app shell. The same pass with a moved verdict-update record writes the store, so without --stop-server it refuses."""
    _assets_only_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: pytest.fail("stopped")
    )
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert ac.SERVER_KEEPS_RUNNING_NOTE in capsys.readouterr().out

    ac.record_verdict_update_green("moved")
    assert ac.main([]) == 2
    assert "REFUSING TO RUN" in capsys.readouterr().out


def _capable_server(monkeypatch, *, code=None, root=None):
    """Make the review server's port answer as a server that speaks the land protocol, with the working tree's code digest unless `code` says otherwise, from this checkout unless `root` does."""
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac,
        "capabilities",
        lambda port=ac.REVIEW_PORT: {
            "land_protocol": ac.landing.LAND_PROTOCOL,
            "code": code if code is not None else ac.landing.code_digest(ac.ROOT / "rebuild" / "review"),
            "root": str(root if root is not None else ac.ROOT),
            "autosave": str(ac.AUTOSAVE),
        },
    )
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: pytest.fail("stopped the server"))


def test_probe_server_reads_the_capabilities_answer(monkeypatch):
    """No listener is `none`, a 404 from /capabilities is `legacy`, another checkout's root is `foreign`, another code digest or protocol is `stale`, and this checkout on the tree's code is `current`."""
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    assert ac.probe_server().kind == "none"
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(ac, "capabilities", lambda port=ac.REVIEW_PORT: ac.NO_CAPABILITIES)
    assert ac.probe_server().kind == "legacy"
    _capable_server(monkeypatch)
    assert ac.probe_server().kind == "current"
    _capable_server(monkeypatch, code="older")
    assert ac.probe_server().kind == "stale"
    _capable_server(monkeypatch, root="/elsewhere/checkout")
    assert ac.probe_server() == ac.ServerProbe("foreign", "/elsewhere/checkout")


def test_a_server_that_does_not_answer_is_asked_again_and_never_stopped(monkeypatch, capsys):
    """Only a 404 marks a server from before the land protocol. A listener whose /capabilities times out is asked again, and a current server that answers on a later try is `current`; one that never answers is `unanswered`, and the pass refuses without stopping it, even with --stop-server."""
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    sleeps: list[float] = []
    monkeypatch.setattr(ac.time, "sleep", sleeps.append)
    current = {
        "land_protocol": ac.landing.LAND_PROTOCOL,
        "code": ac.landing.code_digest(ac.ROOT / "rebuild" / "review"),
        "root": str(ac.ROOT),
    }
    answers = [None, None, current]
    monkeypatch.setattr(ac, "capabilities", lambda port=ac.REVIEW_PORT: answers.pop(0))
    assert ac.probe_server().kind == "current"
    assert sleeps == [ac.CAPABILITIES_RETRY_S] * 2

    asked: list[int] = []
    monkeypatch.setattr(ac, "capabilities", lambda port=ac.REVIEW_PORT: asked.append(1))
    probe = ac.probe_server()
    assert probe.kind == "unanswered" and len(asked) == ac.CAPABILITIES_ATTEMPTS
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: pytest.fail("stopped the server"))
    monkeypatch.setattr(ac, "restart_review_server", lambda timeout=None: pytest.fail("restarted the server"))
    for args in (_preflight_args(), _preflight_args(stop_server=True), _preflight_args(yes=True)):
        assert ac._preflight(args, can_keep_running=False, probe=probe) is False
    assert "gave no /capabilities answer" in capsys.readouterr().out
    assert ac._preflight(_preflight_args(), can_keep_running=True, probe=probe) is True
    assert ac.SERVER_KEEPS_RUNNING_NOTE in capsys.readouterr().out


def test_main_keeps_a_current_server_running_through_a_rebuild(tmp_path, monkeypatch, capsys):
    """With a server from this checkout on the tree's code, a corpus-changing pass never stops it, even without --stop-server: it seeds review.next, builds there, snapshots the store, runs the verdict update on scratch paths, lands, and then refreshes the review facts. The server's tabs are sent the reload."""
    _unsettled_repo(tmp_path, monkeypatch)
    _capable_server(monkeypatch)
    ran: list[ac.Plan] = []
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: ran.append(plan) or 0)
    assert ac.main([]) == 0
    assert "The review server keeps running" in capsys.readouterr().out
    (plan,) = ran
    assert plan.broadcast is True
    assert plan.scratch_dir is not None
    names = [step.name for step in plan.steps]
    assert names[: names.index("review-facts") + 1] == [
        "run_m1",
        "corpus-seed",
        "corpus-build",
        "store-snapshot",
        "verdict-update",
        "land",
        "review-facts",
    ]
    build = plan.argv("corpus-build")
    assert build[build.index("--out") + 1] == str(ac.next_corpus_dir())
    update = plan.argv("verdict-update")
    assert update[update.index("--autosave") + 1] == str(plan.scratch_dir / ac.landing.PREPARED_NAME)
    assert update[update.index("--journal") + 1] == str(plan.scratch_dir / "journal.ndjson")
    land = plan.argv("land")
    assert land[land.index("--staged") + 1] == str(ac.next_corpus_dir())
    assert land[land.index("--live") + 1] == str(ac.REVIEW_OUT)
    assert land[land.index("--autosave") + 1] == str(ac.AUTOSAVE)
    assert land[land.index("--scan-state") + 1] == str(ac.journal_scan_path())


def test_preflight_restarts_a_server_running_other_code_and_refuses_when_it_does_not_return(
    monkeypatch, capsys
):
    """A server from this checkout whose code digest is not the tree's was started from other code, so the pass restarts it on the tree's code and runs once it answers as current. When it does not come back the pass refuses."""
    _capable_server(monkeypatch, code="older")
    events: list[str] = []
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: events.append("stop") or True)

    def start():
        events.append("start")
        _capable_server(monkeypatch)
        monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: events.append("stop") or True)

    monkeypatch.setattr(ac, "start_review_server", start)
    monkeypatch.setattr(ac.time, "sleep", lambda seconds: None)
    assert ac._preflight(_preflight_args()) is True
    assert events == ["stop", "start"]
    assert "restarting it on this tree's code" in capsys.readouterr().out

    _capable_server(monkeypatch, code="older")
    monkeypatch.setattr(ac, "stop_review_server", lambda timeout=0.0: True)
    monkeypatch.setattr(ac, "start_review_server", lambda: None)
    monkeypatch.setattr(ac, "SERVER_START_TIMEOUT", 0.0)
    assert ac._preflight(_preflight_args()) is False
    assert "REFUSING TO RUN" in capsys.readouterr().out


def test_preflight_keeps_a_server_running_other_code_through_a_pass_that_writes_nothing_under_it(
    monkeypatch, capsys
):
    """A pass that neither lands nor writes the store needs nothing from the server, so a server on other code keeps running through it rather than being stopped, which would leave the reviewer with no server if the tree's code did not come back."""
    _capable_server(monkeypatch, code="older")
    monkeypatch.setattr(ac, "restart_review_server", lambda timeout=None: pytest.fail("restarted the server"))
    assert ac._preflight(_preflight_args(), can_keep_running=True) is True
    out = capsys.readouterr().out
    assert "runs older code than this checkout and keeps running" in out


def test_main_leaves_a_foreign_server_alone_and_sends_it_nothing(tmp_path, monkeypatch, capsys):
    _unsettled_repo(tmp_path, monkeypatch)
    _capable_server(monkeypatch, root="/elsewhere/checkout")
    monkeypatch.setattr(ac, "start_review_server", lambda: pytest.fail("restarted a foreign server"))
    ran: list[ac.Plan] = []
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: ran.append(plan) or 0)
    assert ac.main([]) == 0
    assert "for another checkout (/elsewhere/checkout)" in capsys.readouterr().out
    assert ran[0].broadcast is False


def test_a_direct_merge_lands_the_store_alone_and_a_no_carry_rebuild_lands_an_empty_store():
    """A pass whose corpus did not move lands only the store the verdict update prepared. A corpus-changing pass that carries nothing lands the corpus with an empty store stamped for it, so the store and the corpus stay aligned; with no verdict update to prepare a store, the land gets none. A pass that neither moves the corpus nor writes the store lands nothing, and a staging pass never lands."""
    direct = _plan(skip_corpus=True, direct_merge=True, corpus_note="same")
    land = _argv({step.name: step for step in direct.steps}["land"])
    assert "--staged" not in land
    assert land[land.index("--corpus") + 1] == str(ac.REVIEW_OUT)
    assert "--prepared" in land

    no_carry = _plan(no_carry=True)
    land = _argv({step.name: step for step in no_carry.steps}["land"])
    assert "--prepared" not in land
    assert land[land.index("--staged") + 1] == str(ac.next_corpus_dir())
    assert "store-snapshot" in [step.name for step in no_carry.steps]

    settled = _plan(
        skip_corpus=True, skip_verdict_update=True, verdict_update_note="same", corpus_note="same"
    )
    assert not settled.land
    assert {"land", "store-snapshot", "corpus-seed"}.isdisjoint(step.name for step in settled.steps)

    first = _plan(first_run=True)
    assert not first.land
    assert "--out" not in _argv({step.name: step for step in first.steps}["corpus-build"])


def _staged_repo(tmp_path, monkeypatch):
    """A settled repo whose live corpus fails both the byte-identity check and the assets-exempt check, while a staged corpus passes the byte-identity check. This is the third check `main` makes, and the condition for the promotion step."""
    _settled_repo(tmp_path, monkeypatch)
    staged = tmp_path / "var" / "staged-review"
    staged.mkdir(parents=True)
    monkeypatch.setattr(
        ac,
        "corpus_build_skippable",
        lambda root=None, review_out=None, ignore=(): review_out is not None,
    )
    monkeypatch.setattr(ac, "promotable_corpus", lambda root=None, summary_path=None, live=None: staged)
    return staged


def test_main_promotes_a_current_staged_corpus_instead_of_rebuilding(tmp_path, monkeypatch, capsys):
    """A live pass after a staging pass plans a land, not a build: the land names the directory it swaps in, the corpus build reads as skipped under the promotion note, and no review.build command appears in the plan."""
    staged = _staged_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"--staged {staged}" in _step_lines(out, "land")
    assert f"SKIPPED ({ac.CORPUS_PROMOTE_NOTE})" in _step_lines(out, "corpus-build")
    assert "rebuild.review.build" not in out
    assert "assets-refresh" not in out


def test_a_promoting_pass_runs_the_whole_verdict_update(tmp_path, monkeypatch, capsys):
    """A promoting pass runs the full verdict update. The verdict-update skip and the direct merge both require an unmoved corpus, and a promotion moves it and gives it a new stamp. So even with a matching verdict-update record the verdict update runs the full carry, which puts the store's verdicts onto the promoted units by id, and the carry-source line says the master is stamped for the corpus this pass replaces."""
    _staged_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    row = _step_lines(out, "verdict-update")
    assert "uv run python -m rebuild.tools.verdict_update" in row
    assert "--verdicts" in row and "--carry-out" in row
    assert "--merge-master" not in row
    assert "SKIPPED" not in row
    assert "stamped for the corpus this pass replaces; its verdicts land by unit id" in out
    assert "stamped for the served corpus" not in out


def test_a_promoting_pass_stops_a_review_server_from_before_the_land_protocol(tmp_path, monkeypatch, capsys):
    """A promotion swaps every shard and the stamp under the app, so for a server from before the land protocol it counts as a corpus write whatever the skip flag says. The predicate returns False for it, and its other three answers are unchanged. End to end, such a server makes the pass refuse without --stop-server (a --no-merge pass too, since the corpus moves, not the store) and is stopped with it; a server that speaks the land protocol keeps running (`test_main_keeps_a_current_server_running_through_a_rebuild`)."""
    assert ac.server_can_keep_running(skip_corpus=True, writes_store=False, promotes_corpus=True) is False
    assert ac.server_can_keep_running(skip_corpus=True, writes_store=False) is True
    assert ac.server_can_keep_running(skip_corpus=True, writes_store=True) is False
    assert ac.server_can_keep_running(skip_corpus=False, writes_store=False) is False

    _staged_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    stops: list[int] = []
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: stops.append(1) or True
    )
    assert ac.main([]) == 2
    assert "REFUSING TO RUN" in capsys.readouterr().out
    assert ac.main(["--no-merge"]) == 2
    assert "REFUSING TO RUN" in capsys.readouterr().out
    assert stops == []
    assert ac.main(["--stop-server"]) == 0
    assert stops == [1]
    assert "Stopping the review server" in capsys.readouterr().out


def _carried(stamp):
    return json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "verdicts": []})


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


def _land_history(journal_path, *, base):
    """Journal a store's history with every land journaled as a base (`base` None) or as sets and clears (`base` False): a merge base, a land that stashes S1, a same-stamp restore that stashes the store it replaced, a land onto the same records that stashes S2, and an autosave."""
    store = [{"unit": "u-1", "verdict": "approve", "note": "", "at": "2026-07-10T00:00:00Z"}]
    landed = [*store, {"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-10T02:00:00Z"}]
    for source, stamp, old_stamp, old, new, stashed, hour, land in (
        ("merge", "S1", None, [], store, None, 1, False),
        ("land", "S2", "S1", store, landed, "verdicts-autosave-S1.json", 2, True),
        ("restore", "S2", "S2", landed, store, "verdicts-autosave-pre-restore-a.json", 3, False),
        ("land", "S3", "S2", store, store, "verdicts-autosave-S2.json", 4, True),
        ("autosave", "S3", "S3", store, landed, None, 5, False),
    ):
        journal.record_transition(
            journal_path,
            source=source,
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=old,
            new_verdicts=new,
            stashed=stashed,
            at=f"2026-07-10T0{hour}:00:00Z",
            base=base if land else None,
        )


@pytest.mark.parametrize("base", [None, False])
def test_prune_stashes_keeps_the_same_stashes_whether_lands_journal_bases_or_sets_and_clears(tmp_path, base):
    """The sweep anchors on the last event that is a base or moves the stamp, so a land journaled as sets and clears anchors it where the base it replaced did: the sweep keeps the stash the latest land named and those named since, and drops the earlier ones."""
    journal_path = tmp_path / "verdicts-journal.ndjson"
    _land_history(journal_path, base=base)
    assert [event.base for event in journal.scan(journal_path).events] == [
        True,
        base is None,
        False,
        base is None,
        False,
    ]
    names = ["S1", "pre-restore-a", "S2", "stray"]
    for name in names:
        (tmp_path / f"verdicts-autosave-{name}.json").write_text("{}")

    removed = ac.prune_stashes(tmp_path, journal_path)

    assert removed == [
        tmp_path / f"verdicts-autosave-{name}.json" for name in ("S1", "pre-restore-a", "stray")
    ]
    assert (tmp_path / "verdicts-autosave-S2.json").exists()


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


def test_build_plan_retention_off_on_a_staging_pass(tmp_path):
    plan = _plan(review_out=tmp_path / "staged")
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert "staging" in by_name["retention"].note


def test_retention_never_runs_for_real_during_the_suite(monkeypatch):
    """Checks the autouse switches that keep retention and readiness from running for real in the suite. Retention resolves its targets from ac.ROOT at call time, which no fixture redirects, so a real run from a test would delete the live repo's carried exports and compact its verdict journal; any test reaching a green finish with record_greens set would trigger it. The readiness checklist reads the served corpus and the root autosave, so it is switched off too."""
    assert cycle_paths.RETENTION_ENABLED is False
    assert cycle_paths.READINESS_ENABLED is False
    calls = {"retention": 0, "readiness": 0}
    monkeypatch.setattr(
        ac, "run_retention", lambda plan: calls.__setitem__("retention", calls["retention"] + 1)
    )
    monkeypatch.setattr(
        ac, "readiness_block", lambda plan: calls.__setitem__("readiness", calls["readiness"] + 1)
    )
    report = _green_report()
    assert ac._finish(report, [], _plan(record_greens=True)) == 0
    assert calls == {"retention": 0, "readiness": 0}
    assert "retention" in report.step_seconds


def test_finish_honors_the_two_green_finish_switches(monkeypatch, capsys):
    """With both switches off, `_finish` calls neither stage and the retention row still gets an outcome; with both on, it calls each once. A test that asserts either stage runs flips its switch and patches the callable, as the tests below do."""
    calls = {"retention": 0, "readiness": 0}

    def retention(plan):
        calls["retention"] += 1
        return ac.RetentionResult(["Retention (skip with --keep-history):"], "swept")

    def readiness(plan):
        calls["readiness"] += 1
        return ["READY - adjudicate at the review queue"]

    monkeypatch.setattr(ac, "run_retention", retention)
    monkeypatch.setattr(ac, "readiness_block", readiness)
    plan = _plan(record_greens=True)

    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", False)
    monkeypatch.setattr(cycle_paths, "READINESS_ENABLED", False)
    assert ac._finish(_green_report(), [], plan) == 0
    out = capsys.readouterr().out
    assert calls == {"retention": 0, "readiness": 0}
    assert "READY" not in out and "swept" not in out
    assert "Cycle complete." in out

    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(cycle_paths, "READINESS_ENABLED", True)
    assert ac._finish(_green_report(), [], plan) == 0
    out = capsys.readouterr().out
    assert calls == {"retention": 1, "readiness": 1}
    assert "READY - adjudicate at the review queue" in out
    assert "swept" in out


def test_the_gate_summaries_a_pass_clears_are_never_the_live_ones(tmp_path, live_deletion_targets):
    """Checks the redirect for the other stages that delete before they rebuild. run_m1 unlinks every file in `cycle_paths.M1_SUMMARY_FILES` and gate:conform unlinks its own summary, all before spawning and all from paths under the live rebuild/out/m1. A test that drove one of those stages unstubbed would empty the directory the corpus build reads and the auto-skip keys on, and a full rebuild would be needed to restore it. No test would fail, because missing summaries read as a failed gate, which most such tests assert anyway."""
    redirected = [
        *cycle_paths.M1_SUMMARY_FILES.values(),
        cycle_paths.CONFORM_SUMMARY,
    ]
    assert [path.parent for path in redirected] == [tmp_path] * len(redirected)
    assert [path.name for path in redirected] == [path.name for path in live_deletion_targets]
    assert all(path.parent == cycle_paths.M1_OUT for path in live_deletion_targets)


def test_finish_runs_retention_on_a_real_green_finish(monkeypatch):
    calls = {"n": 0}

    def stub(plan):
        calls["n"] += 1

    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(ac, "run_retention", stub)
    plan = _plan(record_greens=True)
    assert plan.retention is True and plan.record_greens is True
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0
    assert calls["n"] == 1


def _retention_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "deep-replay-green.json")
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "manifest.json").write_text(json.dumps({"generated_at": "2020-02-01T00:00:00Z"}))
    journal_path = tmp_path / journal.JOURNAL_NAME
    for stamp, old_stamp, stashed, at in (
        ("S1", None, "verdicts-autosave-old.json", "2020-01-01T00:00:00Z"),
        ("S2", "S1", "verdicts-autosave-S1.json", "2020-02-01T00:00:00Z"),
    ):
        journal.record_transition(
            journal_path,
            source="merge",
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=[],
            new_verdicts=[{"unit": "u-1", "verdict": "approve", "note": "", "at": at}],
            stashed=stashed,
            at=at,
        )
    for name in ("verdicts-autosave-old.json", "verdicts-autosave-S1.json"):
        (tmp_path / name).write_text("{}")
    return journal_path


@pytest.mark.parametrize("listening", [False, True])
def test_retention_compacts_the_journal_and_sweeps_stashes_under_the_store_lock(
    tmp_path, monkeypatch, listening
):
    """Retention prunes the stashes and compacts the journal whether or not the review server is up, since the server keeps running through every pass. Both read the journal without the verdict store's lock and take it only for the tail, the deletions, and the rewrite, and the server answers a save in that window with a retryable 503, as during a land."""
    plan = _plan(skip_verdict_update=True, verdict_update_note=ac.VERDICT_UPDATE_SKIP_NOTE)
    journal_path = _retention_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: listening)

    swept_up = ac.run_retention(plan)
    out = "\n".join(swept_up.lines)

    assert "stashes   : removed 1 " in out
    assert not (tmp_path / "verdicts-autosave-old.json").exists()
    assert (tmp_path / "verdicts-autosave-S1.json").exists()
    assert "restore floor now 2020-02-01T00:00:00Z" in out
    assert [event["stamp"] for event in journal.iter_events(journal_path)] == ["S2"]


def test_retention_leaves_the_journal_and_stashes_alone_beside_a_server_from_before_the_land_protocol(
    tmp_path, monkeypatch
):
    """A server from before the land protocol appends to the journal and moves stashes without the verdict store's lock, so a save it appended between the compaction's tail copy and its replacement of the journal would be lost. While one listens, retention leaves both for a later pass; once it is gone, they are pruned."""
    plan = _plan(skip_verdict_update=True, verdict_update_note=ac.VERDICT_UPDATE_SKIP_NOTE)
    plan.legacy_server = True
    journal_path = _retention_repo(tmp_path, monkeypatch)
    before = journal_path.read_bytes()
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)

    out = "\n".join(ac.run_retention(plan).lines)
    assert "stashes   : left intact (a review server from before the land protocol" in out
    assert "journal   : left intact" in out
    assert (tmp_path / "verdicts-autosave-old.json").exists()
    assert journal_path.read_bytes() == before

    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    out = "\n".join(ac.run_retention(plan).lines)
    assert "stashes   : removed 1 " in out
    assert not (tmp_path / "verdicts-autosave-old.json").exists()


def test_retention_leaves_the_journal_and_stashes_for_a_later_pass_while_the_store_stays_locked(
    tmp_path, monkeypatch
):
    plan = _plan()
    journal_path = _retention_repo(tmp_path, monkeypatch)
    before = journal_path.read_bytes()
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    monkeypatch.setattr(ac, "RETENTION_LOCK_TIMEOUT_S", 0.05)

    with ac.store_lock.store_lock(tmp_path / "verdicts-autosave.json"):
        swept_up = ac.run_retention(plan)
    out = "\n".join(swept_up.lines)

    assert "stashes   : left intact (the verdict store stayed locked" in out
    assert "journal   : left intact (the verdict store stayed locked" in out
    assert (tmp_path / "verdicts-autosave-old.json").exists()
    assert journal_path.read_bytes() == before
    assert not journal_path.with_name(journal_path.name + ".tmp").exists()


def _two_retention_passes(root, monkeypatch, plan, *, resume):
    """Run retention over `_retention_repo`, append a later base that stashes the S2 store, and run it again, keeping the scan state the first pass saved when `resume` and removing it otherwise. Returns the journal path, the second pass's lines, and the offset each of the second pass's scans began reading at."""
    root.mkdir()
    monkeypatch.setattr(cycle_paths, "CYCLE_VAR", root / "cycle")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    journal_path = _retention_repo(root, monkeypatch)
    ac.run_retention(plan)
    assert journal.load_scan_state(ac.journal_scan_path()) == journal.scan(journal_path).state
    if not resume:
        ac.journal_scan_path().unlink()
    journal.record_transition(
        journal_path,
        source="land",
        stamp="S3",
        old_stamp="S2",
        old_verdicts=[],
        new_verdicts=[{"unit": "u-2", "verdict": "approve", "note": "", "at": "2020-03-01T00:00:00Z"}],
        stashed="verdicts-autosave-S2.json",
        at="2020-03-01T00:00:00Z",
    )
    (root / "verdicts-autosave-S2.json").write_text("{}")
    starts: list[int] = []
    real_scan = journal.scan

    def spy(path, *, resume=None):
        scanned = real_scan(path, resume=resume)
        starts.append(scanned.start)
        return scanned

    monkeypatch.setattr(journal, "scan", spy)
    lines = ac.run_retention(plan).lines
    monkeypatch.setattr(journal, "scan", real_scan)
    return journal_path, lines, starts


def test_retention_resumes_its_journal_scan_where_the_last_pass_stopped(tmp_path, monkeypatch):
    """Retention reads the journal once per pass and saves where that scan stopped, rebased onto the compacted file when it compacts. The next pass parses only what was appended since, and it removes the same stashes, reports the same lines, and leaves the same journal bytes as a pass that scans from the start."""
    plan = _plan(skip_verdict_update=True, verdict_update_note=ac.VERDICT_UPDATE_SKIP_NOTE)
    resumed_path, resumed_lines, resumed_starts = _two_retention_passes(
        tmp_path / "resumed", monkeypatch, plan, resume=True
    )
    full_path, full_lines, full_starts = _two_retention_passes(
        tmp_path / "full", monkeypatch, plan, resume=False
    )

    assert full_starts[0] == 0 < resumed_starts[0]
    assert resumed_lines == full_lines
    assert any("stashes   : removed 1 " in line for line in resumed_lines)
    assert any("restore floor now 2020-03-01T00:00:00Z" in line for line in resumed_lines)
    assert resumed_path.read_bytes() == full_path.read_bytes()
    assert sorted(path.name for path in (tmp_path / "resumed").glob("verdicts-autosave-*.json")) == sorted(
        path.name for path in (tmp_path / "full").glob("verdicts-autosave-*.json")
    )
    assert journal.load_scan_state(ac.journal_scan_path()) == journal.scan(full_path).state


def test_prune_stashes_keeps_a_stash_journaled_while_it_waited_for_the_lock(tmp_path):
    """The sweep reads the journal before it holds the lock, so it re-reads the tail under it: a stash another writer made and journaled in between is kept."""
    journal_path = tmp_path / "verdicts-journal.ndjson"
    journal.record_transition(
        journal_path,
        source="merge",
        stamp="S1",
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=[],
        at="2026-07-10T01:00:00Z",
    )
    late = tmp_path / "verdicts-autosave-late.json"

    @contextlib.contextmanager
    def lock_after_a_late_writer():
        late.write_text("{}")
        journal.record_transition(
            journal_path,
            source="merge",
            stamp="S2",
            old_stamp="S1",
            old_verdicts=[],
            new_verdicts=[],
            stashed=late.name,
            at="2026-07-10T02:00:00Z",
        )
        yield

    assert ac.prune_stashes(tmp_path, journal_path, lock=lock_after_a_late_writer()) == []
    assert late.exists()


def test_a_second_pass_waits_on_the_pass_lock_and_names_the_holder(monkeypatch, capsys):
    waiting = threading.Event()
    entered: list[float] = []
    real_print = print

    def spy(*args, **kwargs):
        real_print(*args, **kwargs)
        if args and str(args[0]).startswith("waiting for pass"):
            waiting.set()

    monkeypatch.setattr("builtins.print", spy)
    with ac.pass_lock():
        assert ac.pass_lock_path().read_text().strip() == str(os.getpid())

        def run_second():
            with ac.pass_lock():
                entered.append(time.monotonic())

        second = threading.Thread(target=run_second)
        second.start()
        assert waiting.wait(10)
        assert entered == []
        released = time.monotonic()
    second.join(10)
    assert entered and entered[0] >= released
    assert f"waiting for pass {os.getpid()}" in capsys.readouterr().out


def test_the_land_keeps_the_pass_lock_held_after_the_driver_lets_go(tmp_path):
    """The land inherits the pass lock's descriptor, so a driver that dies while its land runs does not let the next pass in: the lock stays held until the land exits too. Any other step's child does not hold it."""
    ready = tmp_path / "ready"
    script = "import sys, time; open(sys.argv[1], 'w').close(); time.sleep(float(sys.argv[2]))"
    results: list[ac._StepResult] = []

    def run(name: str, seconds: float, uninterruptible: bool) -> None:
        results.append(
            ac._run_step(
                name,
                [sys.executable, "-c", script, str(ready), str(seconds)],
                emit=ac._Emitter(),
                registry=ac._ChildRegistry(),
                stream=False,
                uninterruptible=uninterruptible,
            )
        )

    def lock_free() -> bool:
        try:
            with ac.store_lock.hold_flock(ac.pass_lock_path(), blocking=False):
                return True
        except ac.store_lock.LockBusy:
            return False

    for name, uninterruptible, held_after in (("land", True, True), ("review-facts", False, False)):
        ready.unlink(missing_ok=True)
        with ac.pass_lock():
            child = threading.Thread(target=run, args=(name, 3.0, uninterruptible))
            child.start()
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(0.05)
        assert lock_free() is not held_after
        child.join(30)
        assert lock_free()
    assert [result.returncode for result in results] == [0, 0]
    assert ac._held_pass_lock == []


def test_main_runs_a_live_pass_under_the_pass_lock_with_its_own_scratch_directory(tmp_path, monkeypatch):
    """A live pass holds the pass lock from before its plan, sweeps the scratch directories a killed pass left, and deletes its own when it ends. A dry run holds the lock too, and gets no scratch directory."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    leftover = cycle_paths.CYCLE_VAR / "20200101T000000Z-dead"
    leftover.mkdir(parents=True)
    seen: list[tuple[bool, Path | None]] = []

    def pass_lock_held() -> bool:
        try:
            with ac.store_lock.hold_flock(ac.pass_lock_path(), blocking=False):
                return False
        except ac.store_lock.LockBusy:
            return True

    def run_cycle(plan, report, emit, registry, **_):
        seen.append((pass_lock_held(), plan.scratch_dir))
        assert plan.scratch_dir is not None and plan.scratch_dir.is_dir()
        assert not leftover.exists()
        return 0

    monkeypatch.setattr(ac, "_run_cycle", run_cycle)
    assert ac.main([]) == 0
    [(held, scratch)] = seen
    assert held is True
    assert scratch is not None and scratch.parent == cycle_paths.CYCLE_VAR and not scratch.exists()
    assert not pass_lock_held()

    dry: list[bool] = []
    real_run_pass = ac._run_pass
    monkeypatch.setattr(ac, "_run_pass", lambda args: dry.append(pass_lock_held()) or real_run_pass(args))
    assert ac.main(["--dry-run"]) == 0
    assert dry == [True]


def test_main_finishes_a_stopped_land_before_it_sweeps_the_run_directories(tmp_path, monkeypatch, capsys):
    """A land killed after it swapped the corpus in leaves its result in its run directory and its intent beside the store. The next pass finishes it under the store's lock before it sweeps the run directories, so the store follows the corpus; a dry run only says it is there."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    run_dir = cycle_paths.CYCLE_VAR / "20200101T000000Z-dead"
    run_dir.mkdir(parents=True)
    result = run_dir / landing.LANDING_NAME
    result.write_text(json.dumps(_verdicts_doc("2026-07-17T20:24:44Z", ["u-1", "u-landed"])))
    intent = landing.intent_path_for(ac.AUTOSAVE)
    intent.parent.mkdir(parents=True, exist_ok=True)
    intent.write_text(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "landing": str(result),
                "staged": None,
                "live": None,
                "old_stamp": "2026-07-17T20:24:44Z",
                "new_stamp": "2026-07-17T20:24:44Z",
                "stash": None,
                "journal": str(tmp_path / journal.JOURNAL_NAME),
                "journal_inode": None,
                "journal_length": None,
            }
        )
    )
    assert ac.main(["--dry-run"]) == 0
    assert "Left the land a stopped pass began" in capsys.readouterr().out
    assert intent.exists()
    assert ac.main([]) == 0
    assert (
        "Recovered a stopped pass's land: finished the land of 20200101T000000Z-dead"
        in capsys.readouterr().out
    )
    assert not intent.exists() and not run_dir.exists()
    landed = json.loads(ac.AUTOSAVE.read_text())
    assert {entry["unit"] for entry in landed["verdicts"]} == {"u-1", "u-landed"}


def test_a_dry_run_skips_the_recovery_while_a_pass_holds_the_lock(tmp_path, monkeypatch, capsys):
    """A dry run never waits on the pass lock. When a pass holds it, the dry run leaves the superseded corpus to that pass, whose land may be between its renames, and still prints its plan."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    recoveries: list[bool] = []
    monkeypatch.setattr(ac, "recover_superseded_corpus", lambda delete=True: recoveries.append(delete))

    with ac.pass_lock():
        assert ac.main(["--dry-run"]) == 0
    assert recoveries == []
    assert "so this dry run leaves any superseded corpus to it" in capsys.readouterr().out

    assert ac.main(["--dry-run"]) == 0
    assert recoveries == [False]


def test_a_staging_pass_holds_the_pass_lock(tmp_path, monkeypatch):
    """A live pass's promotion reads the corpus a staging pass writes, so a staging pass holds the pass lock for its whole run. It gets no scratch directory."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    seen: list[tuple[bool, Path | None]] = []

    def run_cycle(plan, report, emit, registry, **_):
        try:
            with ac.store_lock.hold_flock(ac.pass_lock_path(), blocking=False):
                held = False
        except ac.store_lock.LockBusy:
            held = True
        seen.append((held, plan.scratch_dir))
        return 0

    monkeypatch.setattr(ac, "_run_cycle", run_cycle)
    assert ac.main(["--review-out", str(tmp_path / "staged")]) == 0
    assert seen == [(True, None)]


def test_finish_skips_retention_when_failures(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(ac, "run_retention", lambda plan: calls.__setitem__("n", calls["n"] + 1))
    plan = _plan(record_greens=True)
    rc = ac._finish(ac.CycleReport(), ["boom"], plan)
    assert rc == 1
    assert calls["n"] == 0


def test_finish_skips_retention_when_plan_opts_out(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(ac, "run_retention", lambda plan: calls.__setitem__("n", calls["n"] + 1))
    plan = _plan(keep_history=True, record_greens=True)
    assert plan.retention is False
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0
    assert calls["n"] == 0


def test_finish_never_prunes_a_mocked_green_cycle(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(ac, "run_retention", lambda plan: calls.__setitem__("n", calls["n"] + 1))
    plan = _plan()
    assert plan.retention is True and plan.record_greens is False
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0
    assert calls["n"] == 0


def test_finish_survives_a_retention_error(monkeypatch):
    def boom(plan):
        raise RuntimeError("retention blew up")

    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(ac, "run_retention", boom)
    plan = _plan(record_greens=True)
    rc = ac._finish(ac.CycleReport(), [], plan)
    assert rc == 0


def test_a_retention_pass_that_raised_reads_failed_in_the_summary_table(monkeypatch):
    """Retention that started and raised reads `FAILED` in its summary row, beside the seconds it took, while the pass stays green. `not run` is kept for a retention that never started."""

    def boom(plan):
        raise RuntimeError("retention blew up")

    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", True)
    monkeypatch.setattr(ac, "run_retention", boom)
    plan = _plan(record_greens=True)
    report = ac.CycleReport()
    assert ac._finish(report, [], plan) == 0
    row = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}["retention"]
    assert row.outcome == "FAILED"
    assert row.seconds == report.step_seconds["retention"]


def test_a_stop_after_retention_finished_leaves_it_ok_in_the_interrupted_table(monkeypatch):
    """A stop signal that lands after retention finished, while the green summary is being composed, leaves retention reading `ok` in the interrupted table, as its own step line did."""

    def stop(plan):
        raise ac.CycleStopped(signal.SIGTERM)

    tables: list[list[console.SummaryRow]] = []

    class Recording(console.CycleConsole):
        def summary(self, rows, *args, **kwargs):
            tables.append(list(rows))

    monkeypatch.setattr(cycle_paths, "READINESS_ENABLED", True)
    monkeypatch.setattr(ac, "readiness_block", stop)
    plan = _plan(record_greens=True)
    report = ac.CycleReport()
    with pytest.raises(ac.CycleStopped):
        ac._finish(report, [], plan, emit=Recording())
    assert (
        ac._finish_interrupted(report, [], 0, plan, emit=Recording(), signum=signal.SIGTERM)
        == 128 + signal.SIGTERM
    )
    assert {row.name: row.outcome for row in tables[-1]}["retention"] == "ok"


def _spawning_run_m1(report, *, spawn, emit, registry, **_):
    spawn("run_m1", ["uv", "run", "fake-m1"], emit=emit, registry=registry, stream=True)
    report.unmatched = 1
    report.multi_matched = 0
    report.pins_pass = True
    return _run_m1_green()


def _spawning_corpus(report, *, spawn, emit, registry, review_out, **_):
    spawn("corpus", ["uv", "run", "fake-corpus"], emit=emit, registry=registry, stream=False)
    report.corpus_units = 1
    return True


def _patch_timing_cycle(monkeypatch):
    monkeypatch.setattr(ac, "_do_run_m1", _spawning_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", _spawning_corpus)
    monkeypatch.setattr(ac, "_do_verdict_update", _verdict_update_ok)
    monkeypatch.setattr(ac, "_do_review_facts", _review_facts_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)


def test_green_cycle_journals_steps_then_one_run_line(monkeypatch, tmp_path):
    """The journal gets one step line per spawned child, then one run line. job-costs spawns a child, so the timing wrapper records it, and a summary claiming the check ran can be matched against the journal. The stubbed verdict-update and review-facts stages spawn nothing and get no line."""
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
    assert [entry["name"] for entry in steps] == ["run_m1", "corpus", "job-costs"]
    assert len(runs) == 1
    assert entries[-1]["kind"] == "run"
    assert entries[-1]["exit"] == "ok"
    assert entries[-1]["interrupted"] is False
    assert {entry["run"] for entry in entries} == {entries[-1]["run"]}


def test_an_assets_refresh_journals_under_its_own_name(monkeypatch, tmp_path):
    """The assets refresh spawns a child in place of the corpus build, so `wrap_spawn` times it under its own step name. That keeps `calibrate_budgets`' sample of "corpus-build" limited to real builds."""
    _patch_timing_cycle(monkeypatch)

    journal_path = tmp_path / "timings.ndjson"
    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(skip_corpus=True, refresh_assets=True, corpus_note=ac.ASSETS_REFRESH_NOTE),
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
        "corpus",
        "job-costs",
    ]
    assert report.assets_status.startswith("refreshed in place")


def test_a_failed_assets_refresh_stops_the_pass_and_joins_the_suite_it_started(monkeypatch, capsys):
    """A failed assets refresh can leave a manifest and an app shell that disagree, so the pass stops at that step. The rebuild suite was submitted before it and is joined, and its result is reported beside the refresh's FAILED row."""
    _patch_timing_cycle(monkeypatch)

    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(skip_corpus=True, refresh_assets=True, corpus_note=ac.ASSETS_REFRESH_NOTE),
        report,
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name, rc=1 if name == "assets-refresh" else 0),
    )

    assert rc == 1
    assert report.assets_status.startswith("FAILED")
    assert report.gate_contracts == "green"
    assert "assets refresh failed" in capsys.readouterr().out


def _record_reloads(monkeypatch, *, enabled: bool = True) -> list[str]:
    """Record every path the cycle sends to the review server, and set the suite's reload switch. The stubbed verdict update also appends `verdict-update` to the same list, so a test can see which came first."""
    sent: list[str] = []

    def record(path):
        sent.append(path)
        return True

    def verdict_update(report, **kwargs):
        sent.append("verdict-update")
        return _verdict_update_ok(report, **kwargs)

    monkeypatch.setattr(ac, "force_reload", record)
    monkeypatch.setattr(ac, "_do_verdict_update", verdict_update)
    monkeypatch.setattr(cycle_paths, "REVIEW_RELOAD_ENABLED", enabled)
    return sent


def _pin_verdict_update_key(monkeypatch):
    monkeypatch.setattr(
        ac, "verdict_update_skip_fingerprint", lambda root=None, corpus=None, master=None, store=None: "plu"
    )


def _assets_refresh_plan():
    ac.REVIEW_OUT.mkdir(parents=True)
    (ac.REVIEW_OUT / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-09-30T10:00:00Z", "inputs_fingerprint": {"static": "static-new"}})
    )
    return _plan(skip_corpus=True, refresh_assets=True, corpus_note=ac.ASSETS_REFRESH_NOTE)


def test_an_assets_refresh_tells_the_open_tabs_to_reload_onto_the_new_app_files(monkeypatch):
    """The server reloads no tab on a file change, so the pass sends one `ams:assets/<static hash>` once the files are in place, with the hash the refreshed manifest records."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    _pin_verdict_update_key(monkeypatch)

    rc = ac._run_cycle(
        _assets_refresh_plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )

    assert rc == 0
    assert [path for path in sent if path != "verdict-update"] == ["ams:assets/static-new"]


def test_no_reload_is_sent_after_a_failed_assets_refresh(monkeypatch):
    """A failed refresh can leave the app files half copied, so no tab is told to load them."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)

    ac._run_cycle(
        _assets_refresh_plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name, rc=1 if name == "assets-refresh" else 0),
    )

    assert sent == []


def test_the_suites_switch_keeps_a_mocked_pass_from_reloading_the_live_tabs(monkeypatch):
    """`cycle_paths.REVIEW_RELOAD_ENABLED` off, as the rebuild suite sets it, sends nothing to the live port."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch, enabled=False)
    _pin_verdict_update_key(monkeypatch)

    ac._run_cycle(
        _assets_refresh_plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )

    assert [path for path in sent if path != "verdict-update"] == []


def _swapping_land(sent: list[str], source: Path):
    """Return a stubbed land that swaps `source` in for the served corpus, as the real land's exchange does, and records that it ran in `sent`."""

    def land(report, *, spawn, emit, registry, plan):
        sent.append("land")
        shutil.rmtree(ac.REVIEW_OUT)
        os.replace(source, ac.REVIEW_OUT)
        return _land_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)

    return land


def test_a_promotion_sends_the_promoted_stamp_after_the_land(monkeypatch, tmp_path):
    """A listening server is sent `ams:corpus/<generated_at>` of the promoted corpus once the land has swapped it in with the store, so its tabs move onto a corpus whose store is already on it. The verdict update reads the staged corpus before the land, and keeps the standing fill's memo beside the served corpus, where every other pass keeps it."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    _pin_verdict_update_key(monkeypatch)
    live = tmp_path / "rebuild" / "out" / "review"
    live.mkdir(parents=True)
    (live / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T10:00:00Z"}))
    source = tmp_path / "var" / "staged-review"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T11:00:00Z"}))
    monkeypatch.setattr(ac, "REVIEW_OUT", live)
    monkeypatch.setattr(ac, "_do_land", _swapping_land(sent, source))

    plan = _plan(skip_corpus=True, promote_corpus=source, corpus_note=ac.CORPUS_PROMOTE_NOTE)
    argv = _argv({step.name: step for step in plan.steps}["verdict-update"])
    assert argv[argv.index("--corpus") + 1] == str(source)
    assert argv[argv.index("--standing-memo") + 1] == str(live.parent / "standing-fill-memo.ndjson.gz")
    rc = ac._run_cycle(
        plan,
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )

    assert rc == 0
    assert sent == ["verdict-update", "land", "ams:corpus/2026-09-30T11:00:00Z"]


def test_a_built_corpus_is_announced_after_the_land_and_never_to_a_foreign_server(monkeypatch, tmp_path):
    """A pass that builds beside the served corpus sends the new stamp once its land has swapped the build in. A server that serves another checkout (`plan.broadcast` off) is sent nothing."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    _pin_verdict_update_key(monkeypatch)
    ac.REVIEW_OUT.mkdir(parents=True)
    (ac.REVIEW_OUT / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T10:00:00Z"}))
    built = ac.next_corpus_dir()

    def build(report, **kwargs):
        built.mkdir(parents=True, exist_ok=True)
        (built / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T11:00:00Z"}))
        return _spawning_corpus(report, **kwargs)

    monkeypatch.setattr(ac, "_do_corpus_build", build)
    monkeypatch.setattr(ac, "_do_land", _swapping_land(sent, built))

    for broadcast, expected in ((True, ["ams:corpus/2026-09-30T11:00:00Z"]), (False, [])):
        sent.clear()
        (ac.REVIEW_OUT / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T10:00:00Z"}))
        plan = _plan()
        plan.broadcast = broadcast
        rc = ac._run_cycle(
            plan,
            ac.CycleReport(),
            ac._Emitter(),
            ac._ChildRegistry(),
            spawn=lambda name, argv, **k: _step(name),
        )
        assert rc == 0
        assert sent == ["verdict-update", "land", *expected]


def test_a_stop_during_the_land_still_moves_the_tabs_and_reports_the_land(monkeypatch, tmp_path):
    """A Ctrl-C that arrives while the land runs does not stop it, and the pass waits for it. Once it has landed, the interrupted pass still tells the open tabs to move onto the new corpus, which no later pass would do because the served stamp is already the new one, deletes the tree the land swapped out, and reports the land as run."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    ac.REVIEW_OUT.mkdir(parents=True)
    (ac.REVIEW_OUT / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T10:00:00Z"}))
    built = ac.next_corpus_dir()
    built.mkdir(parents=True)
    (built / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T11:00:00Z"}))
    discard = tmp_path / "review.discard"

    def interrupted(report, *, spawn, emit, registry, plan):
        report.land_started = time.monotonic()
        shutil.rmtree(ac.REVIEW_OUT)
        os.replace(built, ac.REVIEW_OUT)
        discard.mkdir()
        assert plan.scratch_dir is not None
        plan.scratch_dir.mkdir(parents=True, exist_ok=True)
        (plan.scratch_dir / ac.landing.REPORT_NAME).write_text(
            json.dumps(
                {"landed": True, "records": 1, "new_stamp": "2026-09-30T11:00:00Z", "discard": str(discard)}
            )
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(ac, "_do_land", interrupted)
    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(
        plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )

    assert rc == 128 + signal.SIGINT
    assert sent == ["verdict-update", "ams:corpus/2026-09-30T11:00:00Z"]
    assert not discard.exists()
    assert report.land_status.startswith("landed 1 verdicts")
    outcomes = {row.name: row.outcome for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert outcomes["land"] == "ok"


def test_a_promoting_pass_reads_the_staged_corpus_and_lands_it(monkeypatch, tmp_path):
    """A promotion replaces the corpus build: nothing spawns under corpus-build, the reported totals come from the staged manifest, which the verdict update reads, and the land is what moves it in, after the verdict update. No corpus-seed or corpus-promote row is planned."""
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_review_facts", _review_facts_clean)
    monkeypatch.setattr(ac, "_do_job_costs", _job_costs_clean)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)
    _patch_gate_fingerprints(monkeypatch)
    live = tmp_path / "rebuild" / "out" / "review"
    live.mkdir(parents=True)
    (live / "manifest.json").write_text(json.dumps({"totals": {"units": 1, "rows": 1}}))
    source = tmp_path / "var" / "staged-review"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"totals": {"units": 7, "rows": 9}}))
    monkeypatch.setattr(ac, "REVIEW_OUT", live)
    order: list[str] = []

    def spawn(name, argv, **k):
        order.append(name)
        return _step(name)

    def verdict_update(report, **kwargs):
        order.append("verdict-update")
        return _verdict_update_ok(report, **kwargs)

    monkeypatch.setattr(ac, "_do_verdict_update", verdict_update)
    monkeypatch.setattr(ac, "_do_land", _swapping_land(order, source))
    plan = _plan(skip_corpus=True, promote_corpus=source, corpus_note=ac.CORPUS_PROMOTE_NOTE)
    names = [step.name for step in plan.steps]
    assert "corpus-seed" not in names and "corpus-promote" not in names
    assert plan.next_corpus == source
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=spawn)

    assert rc == 0
    assert "corpus-build" not in order
    assert order.index("verdict-update") < order.index("land")
    assert report.corpus_units == 7 and report.corpus_rows == 9
    assert json.loads((live / "manifest.json").read_text())["totals"]["units"] == 7
    outcomes = {row.name: row.outcome for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert outcomes["corpus-build"] == "skipped"


def test_a_failed_land_fails_the_pass_and_joins_the_suite_it_started(monkeypatch, capsys):
    """A land that did not complete fails the pass: its row reads FAILED, the rebuild suite submitted before it is joined and reports its real result, and no verdict-update green is recorded. The review facts are not refreshed, because the served corpus is still the old one and the new corpus's sidecar never went live. A verdict update that failed never reaches the land, and the review facts wait for the corpus too."""
    _patch_timing_cycle(monkeypatch)
    _pin_verdict_update_key(monkeypatch)
    monkeypatch.setattr(
        ac, "_do_review_facts", lambda report, **kwargs: pytest.fail("refreshed the pins from the old corpus")
    )

    def refused(report, *, spawn, emit, registry, plan):
        report.land_status = "FAILED (the live store moved from A to B since the snapshot)"
        report.step_seconds["land"] = 0.1
        report.step_returncodes["land"] = 1
        return None

    monkeypatch.setattr(ac, "_do_land", refused)
    plan = _plan(record_greens=True)
    report = ac.CycleReport()
    rc = ac._run_cycle(
        plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )

    assert rc == 1
    assert report.gate_contracts == "green"
    outcomes = {row.name: row.outcome for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert outcomes["land"] == "FAILED"
    assert outcomes["review-facts"] == "not run"
    assert report.facts_status.startswith("not run")
    assert "land failed" in capsys.readouterr().out
    assert not cycle_paths.VERDICT_UPDATE_GREEN.exists()

    def failing_update(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return ["verdict merge failed"]

    monkeypatch.setattr(ac, "_do_verdict_update", failing_update)
    monkeypatch.setattr(
        ac, "_do_land", lambda report, **kwargs: pytest.fail("landed after a failed verdict update")
    )
    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(), report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )
    assert rc == 1
    assert report.land_status.startswith("not run")
    assert report.facts_status.startswith("not run")


def test_a_failed_build_never_lands(monkeypatch):
    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_corpus_build", lambda report, **kwargs: False)
    monkeypatch.setattr(
        ac, "_do_store_snapshot", lambda report, **kwargs: pytest.fail("snapshot after a failed build")
    )
    monkeypatch.setattr(ac, "_do_land", lambda report, **kwargs: pytest.fail("landed after a failed build"))
    rc = ac._run_cycle(
        _plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )
    assert rc == 1


def test_a_rebuilding_pass_lands_its_corpus_and_store_with_the_saves_made_during_it(monkeypatch, tmp_path):
    """The seed, the snapshot and the land run for real around a stubbed build and verdict update. The build writes only beside the served corpus, the verdict update writes only the prepared copy, and a save the server applies to the live store while the pass runs is laid over the prepared store at the land. The land swaps the corpus in, stashes the old store, journals the move, deletes the outgoing tree, keys the verdict-update green on the landed store only when nothing was laid over, and the tabs are told the new stamp once."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    monkeypatch.setattr(ac, "_do_corpus_seed", REAL_DO_CORPUS_SEED)
    monkeypatch.setattr(ac, "_do_store_snapshot", REAL_DO_STORE_SNAPSHOT)
    monkeypatch.setattr(ac, "_do_land", REAL_DO_LAND)
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "JSTEST_DIR", tmp_path / "rebuild" / "review" / "jstests")
    live = tmp_path / "rebuild" / "out" / "review"
    monkeypatch.setattr(ac, "REVIEW_OUT", live)
    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "verdicts-autosave.json")
    keyed: list[Path | None] = []
    monkeypatch.setattr(
        ac,
        "verdict_update_skip_fingerprint",
        lambda root=None, corpus=None, master=None, store=None: keyed.append(store) or "plu",
    )
    live.mkdir(parents=True)
    (live / "manifest.json").write_text(json.dumps({"generated_at": "A", "human_unit_ids": ["u-1", "u-2"]}))
    (live / "units-000.json").write_text("old")
    ac.AUTOSAVE.write_text(json.dumps(_verdicts_doc("A", ["u-1", "u-2"])))
    next_dir = ac.next_corpus_dir()

    def build(report, *, spawn, emit, registry, review_out, **kwargs):
        assert review_out == next_dir
        assert (review_out / "units-000.json").read_text() == "old"
        (review_out / "units-000.json").write_text("new")
        (review_out / "manifest.json").write_text(
            json.dumps({"generated_at": "B", "human_unit_ids": ["u-1", "u-3"]})
        )
        return True

    def verdict_update(report, *, spawn, emit, registry, plan):
        argv = plan.argv("verdict-update")
        prepared = Path(argv[argv.index("--autosave") + 1])
        prepared.write_text(json.dumps(_verdicts_doc("B", ["u-1"])))
        live_store = json.loads(ac.AUTOSAVE.read_text())
        live_store["verdicts"].append(
            {"unit": "u-2", "verdict": "reject", "note": "", "at": "2026-07-18T00:00:00Z"}
        )
        live_store["verdicts"][0] = {
            "unit": "u-1",
            "verdict": "reject",
            "note": "late",
            "at": "2026-07-18T00:00:00Z",
        }
        ac.AUTOSAVE.write_text(json.dumps(live_store))
        return _verdict_update_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)

    def spawn(name, argv, **kwargs):
        if name == "land":
            return _step(name, ac.landing.main(argv[argv.index("rebuild.review.landing") + 1 :]))
        return _step(name)

    monkeypatch.setattr(ac, "_do_corpus_build", build)
    monkeypatch.setattr(ac, "_do_verdict_update", verdict_update)
    plan = _plan(record_greens=True)
    assert plan.scratch_dir is not None
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=spawn)

    assert rc == 0
    assert json.loads((live / "manifest.json").read_text())["generated_at"] == "B"
    assert (live / "units-000.json").read_text() == "new"
    assert not next_dir.exists()
    assert ac.landing.discard_paths(live) == []
    landed = json.loads(ac.AUTOSAVE.read_text())
    assert landed["manifest_generated_at"] == "B"
    assert [(record["unit"], record["note"]) for record in landed["verdicts"]] == [("u-1", "late")]
    assert json.loads((tmp_path / "verdicts-autosave-A.json").read_text())["manifest_generated_at"] == "A"
    orphans = json.loads((tmp_path / "var" / "verdict-orphans" / "A.json").read_text())
    assert [(record["unit"], record["reason"]) for record in orphans["verdicts"]] == [("u-2", "orphan")]
    events = list(journal.iter_events(tmp_path / journal.JOURNAL_NAME))
    assert [(event["source"], event["base"], event["stashed"]) for event in events] == [
        ("land", True, "verdicts-autosave-A.json")
    ]
    assert report.land is not None and report.land["overlaid"] == 1
    assert keyed == []
    assert not ac.landing.intent_path_for(ac.AUTOSAVE).exists()
    assert [path for path in sent if path != "verdict-update"] == ["ams:corpus/B"]


def test_a_promoting_pass_journals_no_corpus_build_line(monkeypatch, tmp_path):
    """A promotion builds nothing, so the journal has no corpus-build step line for it, which keeps the corpus-build row of `make cycle-timings ARGS='--by-step'` limited to real builds. The run line's plan block names the promoted directory, so promoting passes can be counted later."""
    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_corpus_build", _corpus_ok)

    journal_path = tmp_path / "timings.ndjson"
    source = tmp_path / "var" / "staged-review"
    rc = ac._run_cycle(
        _plan(skip_corpus=True, promote_corpus=source, corpus_note=ac.CORPUS_PROMOTE_NOTE),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
        timings=CycleTimings(journal_path),
    )

    assert rc == 0
    entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["name"] for entry in entries if entry["kind"] == "step"] == ["run_m1", "job-costs"]
    run = entries[-1]
    assert run["kind"] == "run"
    assert run["plan"]["skip_corpus"] is True
    assert run["plan"]["promote_corpus"] == str(source)


def test_the_verdict_update_green_is_keyed_on_the_landed_store_and_not_after_an_overlay(monkeypatch):
    """The green is keyed on the store the land wrote, so the next plan, which hashes the live store, matches it only when no save changed the store since. A land that laid saves made during the pass over the prepared store records no green, because the fills never saw those saves."""
    _patch_timing_cycle(monkeypatch)
    keyed: list[Path | None] = []

    def key(root=None, corpus=None, master=None, store=None):
        keyed.append(store)
        return "plu"

    monkeypatch.setattr(ac, "verdict_update_skip_fingerprint", key)
    plan = _plan(record_greens=True)
    assert plan.scratch_dir is not None
    rc = ac._run_cycle(
        plan, ac.CycleReport(), ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )
    assert rc == 0
    assert keyed == [plan.scratch_dir / ac.landing.LANDED_NAME]
    assert ac.read_green_record(cycle_paths.VERDICT_UPDATE_GREEN) is not None

    cycle_paths.VERDICT_UPDATE_GREEN.unlink()
    keyed.clear()

    def overlaid(report, *, spawn, emit, registry, plan):
        report.land = {"landed": True, "overlaid": 2, "records": 5, "new_stamp": "S"}
        return report.land

    monkeypatch.setattr(ac, "_do_land", overlaid)
    rc = ac._run_cycle(
        _plan(record_greens=True),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )
    assert rc == 0
    assert keyed == []
    assert not cycle_paths.VERDICT_UPDATE_GREEN.exists()


def test_green_cycle_files_one_check_line_per_gate_it_evaluated(monkeypatch, tmp_path):
    """Every gate the cycle joins writes one check line under this run, including js and make-test, which are evaluated by exit code alone. The children that did the work write nothing: they inherit the run id and skip their own line, so the lines count checks, not processes."""
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
    ]
    assert {entry["run"] for entry in checks} == {timings.run_id}
    assert {entry["outcome"] for entry in checks} == {"green"}
    assert all(entry["status"] == "green" for entry in checks)
    assert all("recordable" not in entry for entry in checks)


def test_a_red_lane_files_the_ids_it_failed_on(tmp_path):
    """A red lane writes the test ids it failed on. The lane's status already reaches the cycle summary, but the failed ids are recorded only here, and `--by-outcome` ranks them."""
    timings = CycleTimings(tmp_path / "timings.ndjson")
    result = ac.classify_rebuild_output(
        "FAILED rebuild/test_settle.py::test_x\nERROR rebuild/test_boom.py::test_y",
        1,
        "rebuild-contracts",
    )
    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_contracts(report, failures, pool.submit(lambda: result), ac._Emitter(), timings)

    (line,) = ct.load_checks(timings.path)
    assert line["check"] == "rebuild-contracts"
    assert line["outcome"] == "red"
    assert line["status"] == "FAILED (2 unexplained)"
    assert line["failed_ids"] == ["rebuild/test_settle.py::test_x", "rebuild/test_boom.py::test_y"]
    assert line["run"] == timings.run_id
    assert report.gate_contracts == "FAILED (2 unexplained)"


def test_a_lane_that_raised_files_no_check_line(tmp_path):
    """ "FAILED (exception)" describes the pool, not the suite: nothing evaluated the lane, so no check line is written."""
    timings = CycleTimings(tmp_path / "timings.ndjson")

    def boom():
        raise RuntimeError("the pool blew up")

    report = ac.CycleReport()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        ac._join_contracts(report, failures, pool.submit(boom), ac._Emitter(), timings)

    assert report.gate_contracts == "FAILED (exception)"
    assert ct.load_checks(timings.path) == []


def test_a_failing_make_test_files_its_exit_code_as_a_red_outcome(tmp_path):
    """make-test is evaluated by its exit code alone, which is reliable for the font suite (unlike run_m1's), so the result is built at the join with the same status strings the summary prints."""
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
            pool.submit(lambda: _step("gate:make-test", 3)),
            ac._Emitter(),
            timings,
        )

    (line,) = ct.load_checks(timings.path)
    assert (line["check"], line["outcome"], line["status"]) == ("make-test", "red", "FAILED (exit 3)")
    assert line["failures"] == ["make test failed"]
    assert report.gate_make_test == "FAILED (exit 3)"
    assert failures == ["make test failed"]


def test_do_run_m1_files_a_check_line_on_the_skip_path(monkeypatch, tmp_path):
    """The skip path writes a check line too: the skip is an evaluation of this build's own summaries, so it belongs in run_m1's record beside the passes that did the work."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
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
    assert (line["check"], line["outcome"], line["status"]) == ("run_m1", "green", "green")
    assert line["run"] == timings.run_id
    assert (report.unmatched, report.multi_matched) == (7, 0)


def test_do_run_m1_records_a_red_when_no_summaries_were_written(monkeypatch, tmp_path):
    """A build that wrote no summaries is recorded red, with the same failure text the cycle's failure list uses (`_run_m1_reasons(None)`)."""
    monkeypatch.setattr(
        cycle_paths,
        "M1_SUMMARY_FILES",
        {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES},
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
    assert (line["check"], line["outcome"], line["status"]) == ("run_m1", "red", "FAILED (no summaries)")
    assert line["failures"] == ac._run_m1_reasons(None)


def test_a_cycle_without_timings_still_evaluates_every_gate(monkeypatch):
    """The timings handle is optional wherever it is passed, so a caller without one (every test that drives the cycle for its console output) reaches the same results without recording them."""
    _patch_timing_cycle(monkeypatch)
    report = ac.CycleReport()
    rc = ac._run_cycle(
        _plan(), report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda name, argv, **k: _step(name)
    )
    assert rc == 0
    assert report.gate_make_test == "green"
    assert report.gate_conform == "green"


def test_main_hands_its_run_id_to_every_child_through_the_environment(tmp_path, monkeypatch):
    """`main` puts its run id in the environment, and every child inherits it. gate:make-test's wrapper and run_m1's CLI record their own check line unless this variable is set, so it keeps each check to one line. It is set on this process, not passed in a child's argv, because it must survive a Make recipe. The autouse fixture in rebuild/conftest.py removes it after the test, because a leftover run id would silence every check-recording test the worker ran next."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
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


def test_main_runs_the_pass_under_the_stop_handlers(tmp_path, monkeypatch, _stop_dispositions):
    """`main` runs `_run_cycle` with SIGTERM and SIGHUP handlers that raise `CycleStopped`, so a signal that reaches only the driver still reaps the children, and it restores the previous handlers when the pass returns."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    seen = {}

    def fake_cycle(plan, report, emit, registry, **kw):
        stop = signal.getsignal(signal.SIGTERM)
        assert callable(stop)
        seen["hup"] = signal.getsignal(signal.SIGHUP) is stop
        with pytest.raises(ac.CycleStopped) as caught:
            stop(signal.SIGTERM, None)
        seen["signum"] = caught.value.signum
        return 0

    monkeypatch.setattr(ac, "_run_cycle", fake_cycle)
    assert ac.main([]) == 0
    assert seen == {"hup": True, "signum": signal.SIGTERM}
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


def test_main_mints_one_run_directory_and_points_latest_at_it(tmp_path, monkeypatch):
    """`main` creates one log directory per pass, named by stamp and short sha (what a summary cites), and points `latest` at it (what an agent tails while the pass runs)."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    seen: dict[str, ac.Plan] = {}

    def fake_cycle(plan, report, emit, registry, **kw):
        seen["plan"] = plan
        return 0

    monkeypatch.setattr(ac, "_run_cycle", fake_cycle)
    assert ac.main([]) == 0

    plan = seen["plan"]
    assert plan.log_dir is not None
    assert plan.log_dir.parent == cycle_paths.BUILD_LOGS_ROOT
    assert plan.log_dir.name == f"{plan.stamp}-{plan.short_id}"
    assert (plan.log_dir / console.PLAN_TXT).exists()
    assert (plan.log_dir / console.TERMINAL_LOG).exists()
    assert (cycle_paths.BUILD_LOGS_ROOT / console.LATEST_LINK).resolve() == plan.log_dir.resolve()
    payload = ac.cycle_summary_payload(ac.CycleReport(), [], plan, "ok")
    assert payload["log_dir"] == str(plan.log_dir)


def test_main_copies_what_it_said_before_the_console_into_the_terminal_log(tmp_path, monkeypatch, capsys):
    """The carry-source line is printed before the plan is resolved and before the console exists. terminal.log is a copy of the terminal, so the line appears once in each."""
    _settled_repo(tmp_path, monkeypatch)
    ac.record_verdict_update_green("plu")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
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
    assert out.count("Auto-resolved carry source") == 1
    assert terminal.count("Auto-resolved carry source") == 1


def test_a_dry_run_mints_no_run_directory():
    """--dry-run resolves the plan and stops, so it creates no log directory and the plan block names none."""
    plan = _plan()
    assert plan.log_dir is None and plan.stamp == ""
    assert "logs " not in _plan_text(plan)
    assert "--dry-run: nothing executed" in _plan_text(plan)


def test_prune_build_logs_keeps_the_newest_runs_and_never_the_pointer(tmp_path):
    """Run directories are named `<UTC stamp>-<short sha>`, so a lexical sort is chronological and no mtime is read. The `latest` link is never a candidate, and the run it points at, the newest, is always kept."""
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


def test_retention_prunes_the_build_logs_under_a_live_server_too(tmp_path, monkeypatch):
    """Retention prunes the build logs while the review server is up, as it prunes everything else: the app writes nothing there."""
    plan = _plan(skip_verdict_update=True, verdict_update_note=ac.VERDICT_UPDATE_SKIP_NOTE)
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "REVIEW_OUT", tmp_path / "review")
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "deep-replay-green.json")
    monkeypatch.setattr(cycle_paths, "BUILD_LOGS_ROOT", tmp_path / "var" / "build-logs")
    (tmp_path / "var" / "build-logs").mkdir(parents=True)
    for index in range(cycle_paths.BUILD_LOGS_KEEP + 3):
        (tmp_path / "var" / "build-logs" / f"2026010{index // 9}T00000{index % 9}Z-abc").mkdir()
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)

    pruned = ac.run_retention(plan)

    assert any(
        f"build logs: removed 3; kept the last {cycle_paths.BUILD_LOGS_KEEP} runs" in line
        for line in pruned.lines
    )
    assert "3 build logs" in pruned.detail
    assert len(list((tmp_path / "var" / "build-logs").iterdir())) == cycle_paths.BUILD_LOGS_KEEP


def test_failing_cycle_still_journals_a_run_line(monkeypatch, tmp_path):
    def failing_merge(report, *, spawn, emit, registry, plan):
        report.merge_status = "FAILED (exit 1)"
        return ["verdict merge failed"]

    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_verdict_update", failing_merge)

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


_SKIP_LOCK = (
    "version = 1\n\n"
    '[[package]]\nname = "abbots-morton-spaceport"\nversion = "16.0.0"\nsource = { virtual = "." }\n\n'
    '[[package]]\nname = "fonttools"\nversion = "4.61.1"\nsource = { registry = "https://pypi.org/simple" }\n'
)


def test_a_version_bump_of_the_lock_moves_no_run_m1_input_while_a_pin_bump_still_rebuilds(tmp_path):
    """The run_m1 skip key hashes `uv.lock` without the project's own block (`fingerprint.lock_digest`). Changing the project version, which the bump-minor skill's `uv sync` writes, leaves the line and the key unchanged, so the green stands and `gates_only_rerun` sees nothing moved. Changing a dependency pin moves the line, and because the lock is not comparison-side the pass rebuilds. The `lock-1` stand-ins elsewhere in this module have no project block and hash by raw bytes, which the earlier tests cover."""
    root = _fake_run_m1_root(tmp_path)
    (root / "uv.lock").write_text(_SKIP_LOCK)
    stored = ac.run_m1_skip_files(root)
    key = ac.run_m1_skip_fingerprint(root)
    record = {"fingerprint": key, "files": stored}
    (root / "uv.lock").write_text(_SKIP_LOCK.replace('version = "16.0.0"', 'version = "16.1.0"'))
    assert ac.run_m1_skip_files(root)["uv.lock"] == stored["uv.lock"]
    assert ac.run_m1_skip_fingerprint(root) == key
    assert ac.moved_input_labels(record, ac.run_m1_skip_files(root)) is None
    assert ac.gates_only_rerun(record, ac.run_m1_skip_files(root)) is None
    (root / "uv.lock").write_text(_SKIP_LOCK.replace('version = "4.61.1"', 'version = "4.62.0"'))
    current = ac.run_m1_skip_files(root)
    assert current["uv.lock"] != stored["uv.lock"]
    assert ac.run_m1_skip_fingerprint(root) != key
    assert ac.moved_input_labels(record, current) == ["uv.lock"]
    assert ac.gates_only_rerun(record, current) is None
    assert ac.moved_inputs_note(record, current) == "uv.lock (changed)"
