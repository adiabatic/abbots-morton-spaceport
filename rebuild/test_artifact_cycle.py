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
from rebuild.review import landing
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import calibrate_budgets as cb
from rebuild.tools import console
from rebuild.tools import cycle_paths
from rebuild.tools import cycle_timings as ct
from rebuild.tools import make_test_gate as mtg
from rebuild.tools import write_meter
from rebuild.tools.peak_rss import format_gb
from rebuild.tools.cycle_timings import CycleTimings, PassWrites

REPO_ROOT = Path(__file__).resolve().parents[1]
# Width assertions use stated machine sizes, not the host running the suite. With DEFAULT_MEMO_BYTES at 1.1 GB, seven parked fold products at PARKED_FOLD_BYTES (0.3 GB), DELTA_SLOT_BYTES at 3.4 GB and FOLD_PREPARATION_BYTES at 2.7 GB, the whole wave is booked at 26.3 GB, above the rendezvous's 20.3 GB at READ_BACK_FOLD_BYTES (2.9 GB) a configuration. 32 GB, which leaves 24.0 GB after `memory_budget`'s reserve, fits six slots alone and five beside an eight-core machine's pytest pool; 33.6 GB, which leaves 25.6 GB, fits six beside the twelve-core pool and five beside a stated nine-worker pool; and 44 GB is `_plan`'s default machine, which the plan and width tests share. Changing a kernel term changes these expectations and can require a different size to keep the memory set aside for the pool visible in a width.
MACHINE_44_GB = 44_000_000_000
MACHINE_33_6_GB = 33_600_000_000
MACHINE_32_GB = 32_000_000_000
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


REAL_DO_LAND = ac._do_land


def _land_ok(report, *, spawn, emit, registry, plan):
    report.land = {"landed": True, "carried": False, "records": 0, "new_stamp": None}
    report.land_status = "landed"
    return report.land


@pytest.fixture(autouse=True)
def _redirect_contracts_lane_reads(monkeypatch, tmp_path):
    """Make this module see the live review corpus and build artifacts as absent. The conftest's `_redirect_cycle_writes` redirects writes only, but the cycle also resolves its read paths from the live repo at call time, so a test driving `_run_cycle` over mocked stages would otherwise read whatever corpus and behavior-class sidecar sit in rebuild/out. Every test here is in the contracts lane, so no live read needs to be kept, and the lane's audit guard fails any read this misses.

    `REVIEW_OUT`, `AUTOSAVE` and the deep replay's green record are constants and are redirected directly. The land, the build-lane step that needs a real store beside it, is stubbed as the other build-lane stages are in each test; a test of it sets the real function back (`REAL_DO_LAND`). The review server's /capabilities answers `NO_CAPABILITIES` (a 404), as a server from before the land protocol does, so no test probes the live port, and starting a server fails the test; a test that checks the probe patches `capabilities` itself. The behavior-class sidecar is not: `deep_sweep_skip_lines` re-roots `BEHAVIOR_CLASSES` against the root it is given, so redirecting the constant outside ROOT would break the tests that pass their own root, and the function is patched for the default root only. The gates' `*_skip_lines(ROOT)` also read through the per-file digest `_sha256_path`, which returns "absent" for a live path, and the deep replay's status reads the tables' heads through `tables_imports_digest`, which reads as no tables for the live root. Each patch changes the result only for the live root or a live path, so a test that passes its own root runs the real function.
    """

    real_sweep_lines = ac.deep_sweep_skip_lines
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
    monkeypatch.setattr(ac, "_do_land", _land_ok)
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "deep-replay-green.json")
    monkeypatch.setattr(
        ac,
        "_sha256_path",
        lambda path: "absent" if is_live_artifact_path(path) else real_sha(path),
    )
    monkeypatch.setattr(
        ac,
        "deep_sweep_skip_lines",
        lambda root=ac.ROOT: None if root == ac.ROOT else real_sweep_lines(root),
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
    }


def _write_pass_summaries(files):
    for name, summary in _pass_summaries().items():
        files[name].write_text(json.dumps(summary))


def test_gate_passes_on_clean_summaries():
    s = _pass_summaries()
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"])
    assert result.check == "run_m1"
    assert result.outcome == "green"
    assert result.status == "green"
    assert result.ok
    assert result.failures == []
    assert result.failed_ids == []


def test_gate_fails_on_defect_errors():
    s = _pass_summaries()
    s["pipeline"]["defect_errors"] = ["E-ANCHOR convention:foo: bad"]
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"])
    assert not result.ok
    assert result.outcome == "red"
    assert result.status == "FAILED"
    assert any("defect" in reason for reason in result.failures)


def test_gate_fails_on_a_manual_pin_gate_with_nothing_in_scope():
    """A Manual-pin summary with nothing in scope has `pass` true, because `pass` is `not disagreements`. The gate uses run_m1_gates's `manual_pin_gate_failure`, which also checks the scope, so the gate fails."""
    s = _pass_summaries()
    s["manual_pins"] = {"pass": True, "disagreements": [], "pins_in_scope": 0, "replayed": 0}
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"])
    assert not result.ok
    assert any("no pins in scope" in reason for reason in result.failures)


def test_gate_fails_on_manual_pins():
    s = _pass_summaries()
    s["manual_pins"] = {"pass": False, "disagreements": ["one", "two"]}
    result = ac.evaluate_run_m1_gate(s["pipeline"], s["manual_pins"])
    assert not result.ok
    assert any("Manual-pin" in reason for reason in result.failures)


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
    """A divergence names a window, not a test, so the result lists no failed ids. The summary's exemplars name the first of the windows."""
    assert ac.evaluate_conform_gate({"divergences": 3, "pass": False}).failed_ids == []
    assert ac.evaluate_conform_gate(None).failed_ids == []


def test_conform_gate_fails_on_bare_false_pass():
    result = ac.evaluate_conform_gate({"pass": False})
    assert result.status == "FAILED"
    assert result.failures == ["conform gate: pass is false"]


def test_classify_review_module_failures_are_hard():
    """A failure in a review module is a hard failure like any other."""
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
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=1,
        total_bytes=MACHINE_44_GB,
    )
    assert plan.scratch_dir is not None

    by_name = {step.name: step for step in plan.steps}
    assert by_name["run_m1"].argv == [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.pipeline.run_m1",
        "--kernel-threads",
        str(plan.kernel_threads),
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
    ]
    assert [step.name for step in plan.steps][:3] == ["run_m1", "corpus-build", "land"]
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
        "--tb=short",
        "--show-capture=no",
        "--code-highlight=no",
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


def test_the_table_diff_build_is_passed_no_width():
    """The table-diff build runs in one Python process, so its argv states no width, whatever the machine."""
    for ncores in (2, 18):
        argv = _argv({step.name: step for step in _plan(ncores=ncores).steps}["corpus-build"])
        assert argv[5:] == ["--out", str(ac.next_corpus_dir())]


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


def test_the_plan_lands_the_table_diff_corpus_right_after_its_build():
    """The pass builds the table-diff corpus beside the served one and lands it. The land reads the live store under the store's lock, so it is handed no store but the live one, and it is told which corpus to swap in and to leave the tree it swaps out to the pass, which deletes it once the tabs are told."""
    plan = _plan(short_id="abc1234")
    names = [step.name for step in plan.steps]
    assert names.index("land") == names.index("corpus-build") + 1
    assert plan.scratch_dir is not None and plan.land
    argv = _argv({step.name: step for step in plan.steps}["land"])
    assert argv[:5] == ["uv", "run", "python", "-m", "rebuild.review.landing"]
    for gone in ("--prepared", "--journal", "--scan-state", "--allow-orphans"):
        assert gone not in argv
    assert argv[argv.index("--run-dir") + 1] == str(plan.scratch_dir)
    assert argv[argv.index("--corpus") + 1] == str(ac.next_corpus_dir())
    assert argv[argv.index("--staged") + 1] == str(ac.next_corpus_dir())
    assert argv[argv.index("--live") + 1] == str(ac.REVIEW_OUT)
    assert argv[-1] == "--keep-discard"


def test_dry_run_plan_staging_pass_never_touches_the_autosave(tmp_path):
    """A staging pass builds the table-diff corpus into its own directory and lands nothing, so it never writes the store."""
    plan = _plan(review_out=tmp_path / "staged")
    by_name = {step.name: step for step in plan.steps}
    argv = _argv(by_name["corpus-build"])
    assert argv[argv.index("--out") + 1] == str(tmp_path / "staged")
    assert "land" not in by_name and not plan.land
    assert plan.scratch_dir is None


def test_dry_run_plan_first_run_builds_in_place_and_lands_nothing():
    plan = ac.build_plan(skip_gates=False, first_run=True, short_id="0000000")
    by_name = {step.name: step for step in plan.steps}
    assert "--out" not in _argv(by_name["corpus-build"])
    assert "land" not in by_name and not plan.land


def test_dry_run_plan_skip_gates():
    plan = ac.build_plan(skip_gates=True, first_run=False, short_id="abc")
    names = {step.name for step in plan.steps}
    assert "gate:js" not in names
    assert "gate:rebuild-contracts" not in names
    assert "gate:conform" not in names


def test_render_plan_is_stringable():
    plan = ac.build_plan(skip_gates=False, first_run=False, short_id="abc1234")
    text = _plan_text(plan)
    assert "rebuild.pipeline.run_m1" in text


def test_every_plan_step_says_what_it_is_for():
    """The banner prints every step's description, the land's included. The gates-only rerun is the one row whose description is not looked up under its own name: it spawns as run_m1:gates-only, reports under run_m1's row, and must describe the gates-only rerun, not the build."""
    for plan in (
        _plan(),
        _plan(skip_gates=True),
        _plan(rerun_gates_only=True, run_m1_note="comparison-side"),
        _plan(
            skip_corpus=True,
            promote_corpus=Path("var/staged-review"),
            corpus_note=ac.CORPUS_PROMOTE_NOTE,
        ),
        _plan(skip_corpus=True, corpus_note="same"),
        _plan(first_run=True),
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
    """gate:conform and gate:make-test are the rows the plan cannot decide. Each skip key covers artifacts run_m1 writes, so a pass that plans the gate may skip it once the build finishes, and the counts line shows a range. A pass that skips run_m1 shows both as certain, because nothing is rebuilt and `main` has already compared the keys and found no green record for them. A --fresh pass also shows them as certain, and a gates-only rerun, which writes no font, shows gate:make-test as certain."""
    plan = _plan()
    rows = ac.plan_rows(plan)
    by_name = {row.name: row for row in rows}
    assert by_name["gate:conform"].status == console.STATUS_MAYBE
    assert (by_name["gate:make-test"].status, by_name["gate:make-test"].note) == (
        console.STATUS_MAYBE,
        ac.MAKE_TEST_MAYBE_NOTE,
    )
    assert by_name["gate:rebuild-contracts"].status == console.STATUS_RUN
    assert by_name["run_m1"].status == console.STATUS_RUN
    assert by_name["gate:conform"].note == ac.CONFORM_MAYBE_NOTE
    text = _plan_text(plan)
    assert ac.CONFORM_MAYBE_NOTE in _step_lines(text, "gate:conform")
    assert "uv run pytest" in _step_lines(text, "gate:rebuild-contracts")
    assert console.counts_line(rows) == f"{len(rows)} steps: {len(rows) - 2}–{len(rows)} will run, 0 skipped"

    rerun = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    rerun_by_name = {row.name: row for row in ac.plan_rows(rerun)}
    assert rerun_by_name["gate:conform"].status == console.STATUS_MAYBE
    assert rerun_by_name["gate:make-test"].status == console.STATUS_RUN

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


def test_a_fresh_pass_has_run_m1_build_its_tables_with_the_crate():
    """Under `--fresh`, run_m1's build argv ends with `--fresh-tables`, so the pass runs the crate's table build where the table-reuse record would let it re-stamp the tables on disk; a build planned without `--fresh` does not carry it."""
    fresh = {step.name: step for step in _plan(fresh=True).steps}["run_m1"].argv
    built = {step.name: step for step in _plan().steps}["run_m1"].argv
    assert fresh is not None and fresh[-1] == "--fresh-tables"
    assert built is not None and "--fresh-tables" not in built


def test_the_plan_block_leads_with_its_arithmetic_and_puts_the_paths_after_the_rows():
    """The header goes straight to the step count and the rows. The paths this pass resolved (whether it is a first run, the corpus it builds) follow the rows, and the concurrency block comes last."""
    plan = _plan()
    lines = ac.render_plan(plan)
    assert lines[0].startswith("artifact cycle ")
    counts = lines.index(console.counts_line(ac.plan_rows(plan)))
    last_row = max(index for index, line in enumerate(lines) if re.match(_PLAN_ROW, line))
    paths = next(index for index, line in enumerate(lines) if line.startswith("  first run "))
    concurrency = next(index for index, line in enumerate(lines) if line.strip().startswith("Concurrency"))
    assert 0 < counts < last_row < paths < concurrency
    assert f"  corpus       : {ac.next_corpus_dir()}" in lines


def _built_corpus(tmp_path, **totals):
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"totals": totals}))
    return corpus


def _do_corpus_build_with(corpus, spawn):
    """Run `_do_corpus_build` over `corpus` with `spawn`, and return its result with the report it filled."""
    report = ac.CycleReport()
    ok = ac._do_corpus_build(
        report,
        spawn=spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        review_out=corpus,
        argv=["uv", "run", "python", "-m", "rebuild.review.build"],
    )
    return ok, report


def test_do_corpus_build_takes_its_totals_from_the_manifest_the_build_wrote(tmp_path):
    corpus = _built_corpus(tmp_path, units=15897, rows=81867, batches=16)
    ok, report = _do_corpus_build_with(corpus, lambda name, argv, **k: _step(name, 0))
    assert ok
    assert (report.corpus_units, report.corpus_rows, report.corpus_batches) == (15897, 81867, 16)


def test_do_corpus_build_fails_when_a_clean_build_left_no_manifest(tmp_path, capsys):
    corpus = tmp_path / "review"
    corpus.mkdir()
    ok, report = _do_corpus_build_with(corpus, lambda name, argv, **k: _step(name, 0))
    assert not ok
    assert "review.build exited 0 but left no readable manifest.json" in capsys.readouterr().out
    assert report.corpus_units is None


def test_do_corpus_build_reads_no_totals_from_a_failed_build(tmp_path, capsys):
    """After a nonzero exit, the manifest in the corpus directory is the previous pass's, so the step fails before reading any totals from it."""
    corpus = _built_corpus(tmp_path, units=1, rows=2, batches=3)
    ok, report = _do_corpus_build_with(corpus, lambda name, argv, **k: _step(name, 3))
    assert not ok
    assert "review.build exited 3" in capsys.readouterr().out
    assert (report.corpus_units, report.corpus_rows, report.corpus_batches) == (None, None, None)


def _argv(step: ac.Step) -> list[str]:
    assert step.argv is not None
    return step.argv


def _plan(**overrides: Any) -> ac.Plan:
    """Return a resolved plan for a stated machine: `ncores` sets every core-derived width and `total_bytes` every memory-derived one, so the plan is the same on any host. Any keyword can be overridden per test."""
    kw: dict[str, Any] = dict(
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
    """Return the result `_do_run_m1` returns for a passing build. A stubbed stage returns this and sets the pin result on the report itself, as the real stage does."""
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
    report.pins_pass = True
    return _run_m1_green()


def _corpus_ok(report, *, spawn, emit, registry, review_out, **_):
    report.corpus_units = 1
    return True


def _job_costs_clean(report, *, spawn, emit, registry, plan):
    report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
    report.job_costs_ok = True


def _js_ok(argv, spawn, emit, registry):
    return _step("gate:js", 0)


def _make_ok(argv, spawn, emit, registry):
    return _step("gate:make-test", 0)


def _contracts_green(
    queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
):
    return _lane_result("rebuild-contracts")


def _conform_green(queue, make_fut, spawn, emit, registry, argv):
    return _conform_result()


def _patch_gate_fingerprints(monkeypatch):
    """Stub the gate green records' keys, for tests that only check whether a green was recorded. Conformance keys are computed around the sweep; the contracts lifecycle snapshots and checks its roster around the suite, and each computation hashes files across the repo, which takes seconds per test and depends on the working tree. A separate test checks that a changed key prevents recording the green."""
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "cfp")
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "rfp-contracts")
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("rfp-contracts", {"key": "rfp-contracts"}))


def _patch_build_chain(monkeypatch):
    monkeypatch.setattr(ac, "_do_corpus_build", _corpus_ok)
    monkeypatch.setattr(ac, "_do_job_costs", _job_costs_clean)


def test_the_executor_spawns_the_argv_the_plan_holds():
    """build_plan writes each step's argv and the executor runs that list, so changing a step's argv changes what is spawned."""
    plan = _plan()
    sentinel = ["uv", "run", "python", "sentinel-land", "--only-here"]
    {step.name: step for step in plan.steps}["land"].argv = sentinel
    spy: list = []

    def spawn(name, argv, **_):
        spy.append((name, argv))
        return _step(name, 1)

    REAL_DO_LAND(ac.CycleReport(), spawn=spawn, emit=ac._Emitter(), registry=ac._ChildRegistry(), plan=plan)
    assert spy == [("land", sentinel)]


def test_js_launches_before_run_m1_finishes_and_make_test_once_its_gate_passes(monkeypatch):
    """gate:js reads no build artifact, so it starts at t=0, beside run_m1. gate:make-test checks the data-expect pins against the rebuilt Senior its session copies from run_m1's build, so it starts only once the run_m1 gate has passed."""
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
    assert not make_started.wait(0.2)
    assert "run_m1_finish" not in record
    release_run_m1.set()
    t.join()

    assert record["js_start"] < record["run_m1_finish"]
    assert record["run_m1_finish"] < record["make_start"]


def test_the_rebuild_suite_waits_for_run_m1_pass(monkeypatch):
    record = {}

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        record["run_m1_finish"] = time.monotonic()
        return _run_m1_green()

    def fake_contracts(
        queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
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
        queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
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


@pytest.mark.parametrize(
    "shape",
    [{"pool_policy": "queue"}, {"pool_policy": "overlap", "skip_run_m1": True, "skip_corpus": True}],
    ids=["queue-policy", "overlap-policy-no-build-lane"],
)
def test_pool_queue_serializes_the_contracts_lane_after_make_test(monkeypatch, shape):
    """The rebuild suite waits for gate:make-test whenever the heavy gates queue: under the queue policy, and under the overlap policy on a pass that runs gate:make-test and neither run_m1 nor the corpus build (`gates_queue`). While gate:make-test is held, the suite does not even begin to prepare its run."""
    record = {}
    release_make = threading.Event()
    make_running = threading.Event()
    contracts_preparing = threading.Event()
    prepare_run = ac.contracts.prepare_run

    def watched_prepare_run(*args, **kwargs):
        contracts_preparing.set()
        return prepare_run(*args, **kwargs)

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
    monkeypatch.setattr(ac.contracts, "prepare_run", watched_prepare_run)
    _patch_build_chain(monkeypatch)

    plan = _plan(**shape)
    report = ac.CycleReport()
    box = {}
    t = threading.Thread(
        target=lambda: box.__setitem__(
            "rc", ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=fake_spawn)
        )
    )
    t.start()
    make_running.wait()
    prepared_beside_make = contracts_preparing.wait(0.2)
    release_make.set()
    t.join()

    assert not prepared_beside_make
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

    def fake_conform(queue, make_fut, spawn, emit, registry, argv):
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
        report.pins_pass = True
        return _run_m1_green()

    def fake_corpus(report, *, spawn, emit, registry, review_out, **_):
        report.corpus_units = 15903
        report.corpus_rows = 81894
        report.corpus_batches = 16
        report.step_seconds["corpus-build"] = 61.0
        return True

    def fake_js(argv, spawn, emit, registry):
        ev_js.wait()
        return _step("gate:js", 0)

    def fake_make(argv, spawn, emit, registry):
        ev_make.wait()
        return _step("gate:make-test", 0)

    def fake_contracts(
        queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
    ):
        ev_contracts.wait()
        return _lane_result("rebuild-contracts", "green (annotated)")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", fake_corpus)
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
    assert "ok  pins pass" in out
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
    result = ac._gate_contracts_task(False, None, None, fake_spawn, emit, registry, argv)

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


def test_classify_rebuild_reads_the_whole_id_from_each_summary_shape():
    """A parameter can hold spaces, a ` - ` or a `]`, and a message can hold `] - `, so the failure note and `--by-outcome` get an id pytest can run, and siblings that differ after a space stay apart. A line that fits no shape keeps the run red and is filed under its own text. The lines sit in a loop, not in `parametrize`, because an id built from one of them would be a collected id that holds `] - `."""
    bare = "rebuild/test_x.py::test_y"
    shapes = [
        (f"FAILED {bare}", bare),
        (f"FAILED {bare} - boom", bare),
        (f"FAILED {bare} - ", bare),
        (f"FAILED {bare} - assert [1] == [2] - nope", bare),
        (f"FAILED {bare}[a b]", f"{bare}[a b]"),
        (f"FAILED {bare}[a b] - boom", f"{bare}[a b]"),
        (f"FAILED {bare}[a b] - ", f"{bare}[a b]"),
        (f"FAILED {bare}[a b] - assert [1] - nope", f"{bare}[a b]"),
        (f"FAILED {bare}[a - b]", f"{bare}[a - b]"),
        (f"FAILED {bare}[a - b] - boom", f"{bare}[a - b]"),
        (f"FAILED {bare}[[t] build 243.1s]", f"{bare}[[t] build 243.1s]"),
        (f"FAILED {bare}[[t] build 243.1s] - assert [1] - nope", f"{bare}[[t] build 243.1s]"),
        (f"FAILED {bare}[[]] - boom", f"{bare}[[]]"),
        ("ERROR rebuild/test_x.py - ImportError while importing [x]", "rebuild/test_x.py"),
        ("ERROR rebuild/test_x.py", "rebuild/test_x.py"),
        (f"ERROR {bare}[a b] - boom", f"{bare}[a b]"),
        ("\x1b[31mFAILED\x1b[0m rebuild/test_x.py::\x1b[1mtest_y[a b]\x1b[0m - boom", f"{bare}[a b]"),
    ]
    for line, expected in shapes:
        assert ac.classify_rebuild_output(line, 1, "rebuild-contracts").failed_ids == [expected]

    for line in (
        "FAILED ",
        "FAILED to open the pack",
        "ERROR the worker went away",
        f"FAILED {bare}[a - boom",
    ):
        for returncode in (0, 1):
            result = ac.classify_rebuild_output(line, returncode, "rebuild-contracts")
            assert result.outcome == "red"
            assert result.failed_ids == [f"unparsed summary line: {line}"]
            assert not result.recordable

    silent = ac.classify_rebuild_output(f"  FAILED {bare}\nFAILED", 2, "rebuild-contracts")
    assert silent.failed_ids == ["pytest exited 2 with no parsed FAILED/ERROR lines"]


def test_failure_funnels_from_concurrent_branch(monkeypatch, capsys):
    def fake_corpus(report, *, spawn, emit, registry, review_out, **_):
        report.corpus_units = 100
        return True

    def fake_make(argv, spawn, emit, registry):
        return _step("gate:make-test", 1)

    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", fake_corpus)
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
    def raising_conform(queue, make_fut, spawn, emit, registry, argv):
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


def test_run_m1_failure_runs_no_make_test(monkeypatch, capsys):
    """gate:make-test checks the pins against the rebuilt Senior, which a failed run_m1 leaves stale, so the pass does not start it."""

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        return None

    def fake_make(argv, spawn, emit, registry):
        pytest.fail("gate:make-test started after a failed run_m1")

    monkeypatch.setattr(ac, "_do_run_m1", fake_run_m1)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", fake_make)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    _patch_build_chain(monkeypatch)

    plan = _plan()
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=lambda *a, **k: _step())

    assert report.gate_make_test == "not run (run_m1 gate failed)"
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


def test_the_plan_passes_a_width_to_the_conformance_sweep_and_none_to_run_m1():
    plan = _plan(ncores=10, total_bytes=MACHINE_48_GIB)
    by_name = {step.name: step for step in plan.steps}
    assert "--jobs" not in _argv(by_name["run_m1"])
    assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", str(plan.conform_jobs)]


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
        pool_policy=plan.pool_policy,
        build_lane_runs=not (plan.skip_run_m1 and plan.skip_corpus),
        ncores=ncores,
        total_bytes=total_bytes,
    )


class TestTheConformSweepWidth:
    """The cycle sizes gate:conform's sweep: one spawn process per unit, each holding `CONFORM_SWEEP_UNIT_BYTES`. The conformance sweep is submitted when run_m1's gate passes, so it runs beside the build lane's corpus build, whose one process (`CORPUS_BUILD_BYTES`) is subtracted from memory before the division when the pass runs it. Its cap is the cores less the corpus build's process and gate:make-test's pool under the overlap policy, never below the acceptance-configuration count."""

    def test_the_corpus_build_comes_off_the_machine_before_the_division(self):
        """Checked at the fit terms, where no machine size enters. The corpus term is the build's one process, `CORPUS_BUILD_BYTES`. gate:make-test's pool is added under the overlap policy only, because the queue policy makes the conformance sweep wait for make-test. The cap is `_conform_core_cap`'s in every case."""

        def flags(skip_make_test, skip_corpus, pool_policy) -> dict[str, Any]:
            return dict(
                skip_gates=False,
                skip_make_test=skip_make_test,
                skip_corpus=skip_corpus,
                pool_policy=pool_policy,
                ncores=16,
                total_bytes=MACHINE_48_GIB,
            )

        def terms(*, skip_make_test=False, skip_corpus=False, pool_policy="queue"):
            return ac._conform_fit_terms(**flags(skip_make_test, skip_corpus, pool_policy))

        def cap(*, skip_make_test=False, skip_corpus=False, pool_policy="queue"):
            return ac._conform_core_cap(**flags(skip_make_test, skip_corpus, pool_policy))[0]

        per_unit, corpus = ac.CONFORM_SWEEP_UNIT_BYTES, ac.CORPUS_BUILD_BYTES
        assert terms() == (per_unit, corpus, cap())
        assert terms(skip_corpus=True) == (per_unit, 0, cap(skip_corpus=True))
        make_test = ac._make_test_pool_bytes(skip_make_test=False, ncores=16)
        assert make_test > 0
        assert terms(pool_policy="overlap") == (per_unit, corpus + make_test, cap(pool_policy="overlap"))
        assert terms(pool_policy="overlap", skip_corpus=True) == (
            per_unit,
            make_test,
            cap(pool_policy="overlap", skip_corpus=True),
        )
        assert terms(pool_policy="overlap", skip_make_test=True) == (
            per_unit,
            corpus,
            cap(pool_policy="overlap", skip_make_test=True),
        )
        assert terms(skip_make_test=True) == (per_unit, corpus, cap(skip_make_test=True))

    def test_the_corpus_build_is_the_build_lane_step_that_comes_off(self):
        """The conformance sweep starts beside the corpus build, so a pass that runs the build subtracts its one process, and a pass that skips it subtracts nothing."""
        machine: dict[str, Any] = dict(
            skip_gates=False, skip_make_test=False, total_bytes=MACHINE_48_GIB, ncores=40
        )
        assert ac._conform_build_lane(**machine, skip_corpus=False) == ("corpus-build", ac.CORPUS_BUILD_BYTES)
        assert (
            ac._conform_fit_terms(**machine, skip_corpus=False, pool_policy="queue")[1]
            == ac.CORPUS_BUILD_BYTES
        )
        assert ac._conform_build_lane(**machine, skip_corpus=True) == ("", 0)

    def test_every_fleet_machine_runs_the_conform_sweep_as_wide_as_its_cores_allow(self):
        """On the fleet machines (`doc/fleet.md`), the 48 GiB machines at twelve and eighteen cores, under either pool policy, the conformance sweep runs as many units as the machine has cores with the corpus build skipped, and beside a corpus build the cores less the build's process, never fewer than the acceptance configurations; under the overlap policy both widths also lose gate:make-test's pool. The division never limits the width before the cap does. The capped budget cannot distinguish the cap from a division that equals it, so the uncapped division is also checked, and a change to the constant that narrows a fleet machine fails here. The suite does not catch a constant that is too low: only the conform-sweep row of `make job-costs` compares it with the units that ran."""
        from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
        from rebuild.tools import memory_budget

        configs = len(ACCEPTANCE_CONFIGS)
        for ncores, total_bytes in ((12, MACHINE_48_GIB), (18, MACHINE_48_GIB)):
            corpus = 1
            for pool_policy, skip_corpus in itertools.product(ac.POOL_POLICIES, (False, True)):
                make_test = ac.make_test_pool_width(ncores=ncores) if pool_policy == "overlap" else 0
                gated: dict[str, Any] = dict(
                    skip_gates=False,
                    skip_make_test=False,
                    skip_corpus=skip_corpus,
                    pool_policy=pool_policy,
                    ncores=ncores,
                    total_bytes=total_bytes,
                )
                width = max(configs, ncores - (0 if skip_corpus else corpus) - make_test)
                assert ac.conform_job_budget(**gated) == width
                assert ac.conform_job_derivation(**gated).startswith(f"{width} at ")
                _per_unit, coresident, _cap = ac._conform_fit_terms(**gated)
                assert coresident > 0 or (skip_corpus and pool_policy == "queue")
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
        """Beside a corpus build the conformance sweep's cap is the cores less the build's process, and under the overlap policy less gate:make-test's pool as well, the subtraction the rebuild suite's width makes (`contracts_pool_width` gives the reasons). The cap never falls below the acceptance configurations, the width one process per configuration gives, while memory can still narrow the width below it. The derivation says which of the two bounds set the cap. With the corpus build skipped the sweep takes the build lane's cores beside gate:make-test's gate-lane share, and on a pass that runs neither run_m1 nor the corpus build the heavy gates queue, so the sweep subtracts nothing for that pool and takes every core after it."""
        from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS

        configs = len(ACCEPTANCE_CONFIGS)
        wide: dict[str, Any] = dict(skip_gates=False, skip_make_test=False, total_bytes=1_000_000_000_000)
        make_test = ac.make_test_pool_width(ncores=40)
        beside: dict[str, Any] = dict(wide, skip_corpus=False, ncores=40)
        assert ac.conform_job_budget(**beside, pool_policy="queue") == 40 - 1
        assert ac.conform_job_budget(**beside, pool_policy="overlap") == 40 - 1 - make_test
        assert ac.conform_job_derivation(**beside, pool_policy="queue").endswith(
            f"; the cap is 40 cores less {ac.CORPUS_BUILD_PROCESS}"
        )
        assert ac.conform_job_derivation(**beside, pool_policy="overlap").endswith(
            f"less {ac.CORPUS_BUILD_PROCESS}, less gate:make-test's {make_test} (overlap policy)"
        )
        unbuilt: dict[str, Any] = dict(wide, skip_corpus=True, ncores=40)
        assert ac.conform_job_budget(**unbuilt, pool_policy="overlap") == 40 - make_test
        assert ac.conform_job_budget(**unbuilt, pool_policy="overlap", build_lane_runs=False) == 40

        narrow: dict[str, Any] = dict(wide, skip_corpus=False, ncores=10, pool_policy="overlap")
        assert 10 - 1 - ac.make_test_pool_width(ncores=10) < configs
        assert ac.conform_job_budget(**narrow) == configs
        assert f"the cap is the {configs} acceptance configurations, its floor, since 10 cores less" in (
            ac.conform_job_derivation(**narrow)
        )
        assert ac.conform_job_budget(**dict(narrow, ncores=4)) == 4
        assert ac.conform_job_budget(**dict(narrow, total_bytes=15_000_000_000)) < configs

    def test_a_conform_sweep_width_of_one_is_stated_on_the_argv(self, monkeypatch):
        """A machine where the pooled conformance sweep does not fit beside the corpus build floors at one, the serial conformance sweep, and the argv states that width like any other; without it run_m1 would use its own default, a width this plan did not budget."""
        monkeypatch.setattr(ac, "CONFORM_SWEEP_UNIT_BYTES", 10**12)
        plan = _plan(ncores=12)
        by_name = {step.name: step for step in plan.steps}
        assert plan.conform_jobs == 1
        assert _argv(by_name["gate:conform"])[-2:] == ["--jobs", "1"]

    def test_the_plan_prints_the_conform_sweep_width_with_its_derivation(self):
        """Every Lane conform line that runs the conformance sweep quotes its width, the constant it divides by, and the derivation over the flags the plan resolved. The co-resident term is the corpus build when it runs, plus gate:make-test's pool under the overlap policy. For a pass whose corpus build does not run, the line says so and prints no co-resident term unless gate:make-test's pool runs beside the conformance sweep."""
        machine: dict[str, Any] = dict(ncores=16, total_bytes=MACHINE_32_GIB)
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
            line = _lane_conform_line(plan)
            derivation = _plan_conform_derivation(plan, **machine)
            assert (
                f"(--jobs {plan.conform_jobs}; CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, beside {ac.CORPUS_BUILD_PROCESS}"
                in line
            )
            assert line.endswith(f"; {derivation})")
            assert f"at {format_gb(ac.CONFORM_SWEEP_UNIT_BYTES)} GB each" in derivation
            assert "less a reserve of" in derivation
            assert "GB co-resident" in derivation
        assert " and gate:make-test's pool; " in _lane_conform_line(cases["overlap"])
        assert "gate:make-test's pool" not in _lane_conform_line(cases["queued"])

        idle = {"skip_corpus": True, "corpus_note": "inputs unchanged"}
        alone = _plan(**idle, pool_policy="queue", **machine)
        line = _lane_conform_line(alone)
        derivation = _plan_conform_derivation(alone, **machine)
        assert (
            f"(--jobs {alone.conform_jobs}; CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, the corpus build not running this pass, so nothing co-resident; "
            in line
        )
        assert line.endswith(f"; {derivation})")
        assert "GB co-resident" not in derivation

        overlap_alone = _plan(**idle, pool_policy="overlap", **machine)
        line = _lane_conform_line(overlap_alone)
        assert "so only gate:make-test's pool co-resident; " in line
        assert "GB co-resident" in _plan_conform_derivation(overlap_alone, **machine)

    def test_the_printed_derivation_is_the_one_that_produced_the_width(self):
        """The plan line quotes the derivation, and it and the width come from the same three terms, so in every case the clause starts with the width it explains."""
        for total_bytes in (MACHINE_32_GIB, 20_000_000_000):
            for skip_make_test in (False, True):
                for skip_corpus in (False, True):
                    for pool_policy in ac.POOL_POLICIES:
                        kw: dict[str, Any] = dict(
                            skip_gates=False,
                            skip_make_test=skip_make_test,
                            skip_corpus=skip_corpus,
                            pool_policy=pool_policy,
                            ncores=10,
                            total_bytes=total_bytes,
                        )
                        width = ac.conform_job_budget(**kw)
                        assert ac.conform_job_derivation(**kw).startswith(f"{width} at ")


def test_the_land_holds_less_than_the_build_lane_step_before_it():
    """The land follows the corpus build in the build lane, and the lane's reservation (`_conform_build_lane`) covers the build, so it covers the land only while the land's constant stays below what the build's process holds."""
    assert ac.LAND_BYTES < ac.CORPUS_BUILD_BYTES


def test_the_job_budgets_answer_the_cgroup_allowance_rather_than_the_hosts_core_count(monkeypatch):
    """A CPU quota is invisible to `os.cpu_count()`, so the conformance-sweep and gate:make-test budgets read their cores through `memory_budget.usable_cores`. The test runs the real probe over a fixture cgroup root that allows two cores, which is below `len(ACCEPTANCE_CONFIGS)`, so the conformance sweep returns the allowance, and a gated cycle gives gate:make-test the gate lane's share of it (`memory_budget.split_cores`). Each call passes a terabyte of memory so that only the core count limits the width. An explicit `ncores` still takes precedence over the probe."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
    from rebuild.tools import memory_budget

    host = os.process_cpu_count() or os.cpu_count() or 1
    probe = memory_budget.usable_cores
    root = REPO_ROOT / "rebuild" / "fixtures" / "memory_budget" / "container-v2"
    allowed = probe(root)
    monkeypatch.setattr(memory_budget, "usable_cores", functools.partial(probe, root))
    _build_lane, gate_lane = memory_budget.split_cores(allowed)
    assert allowed == min(host, 2) < len(ACCEPTANCE_CONFIGS)
    assert ac.make_test_pool_width() == gate_lane
    assert ac.conform_job_budget(skip_gates=True, skip_corpus=True, total_bytes=1_000_000_000_000) == allowed
    assert (
        ac.conform_job_budget(skip_gates=True, skip_corpus=True, ncores=12, total_bytes=1_000_000_000_000)
        == 12
    )


def test_make_test_pool_width_is_the_gate_lanes_share_of_the_cores():
    """While the build lane runs, gate:make-test's pool takes the gate lane's share of the cores (`memory_budget.split_cores`), floored at one, which is the width the budgets reserve for."""
    assert ac.make_test_pool_width(ncores=12) == 6
    assert ac.make_test_pool_width(ncores=6) == 3
    assert ac.make_test_pool_width(ncores=1) == 1


def test_make_test_takes_every_core_when_no_build_lane_runs():
    """On a pass that runs neither run_m1 nor the corpus build, nothing holds the build lane, so gate:make-test's pool takes every core."""
    assert ac.make_test_pool_width(build_lane_runs=False, ncores=18) == 18


def test_the_plan_gives_make_test_every_core_only_when_neither_build_lane_step_runs():
    """`build_plan` tells the budgets the build lane runs when run_m1 or the corpus build does. On a roomy 18-core machine gate:make-test's pool is the gate lane's share while either runs and every core when neither does, and the make-test lane line states that width. With no build lane the heavy gates queue under the overlap policy too, so the rebuild suite and the conformance sweep subtract nothing for that pool and take every core after it, and the lane lines say why they wait; a pass with no build lane that skips gate:make-test does not queue, and its sweep and suite run side by side. The table build's width subtracts nothing for that pool on any pass, because the gate starts once the run_m1 gate has passed, and its plan line says so."""
    machine: dict[str, Any] = dict(ncores=18, total_bytes=MACHINE_48_GIB, pool_policy="overlap")
    for shape in ({}, {"skip_run_m1": True}, {"skip_corpus": True}):
        plan = _plan(**machine, **shape)
        assert (plan.make_test_workers, plan.gates_queue) == (9, False)
    assert "Lane make-test                   : starts when run_m1's two JSONs pass" in _plan_text(
        _plan(**machine)
    )
    assert "; -n 9 (the gate lane's 9 of 18 cores" in _plan_text(_plan(**machine))
    idle = _plan(**machine, skip_run_m1=True, skip_corpus=True)
    assert (idle.make_test_workers, idle.gates_queue) == (18, True)
    text = _plan_text(idle)
    assert "; -n 18 (all 18 cores, since neither run_m1 nor the corpus build runs this pass)" in text
    assert "QUEUED behind gate:make-test (no build lane this pass — one heavy pool at a time)" in text
    assert "QUEUED behind gate:conform (no build lane this pass — one heavy pool at a time)" in text
    no_make = _plan(**machine, skip_run_m1=True, skip_corpus=True, skip_make_test=True)
    assert not no_make.gates_queue
    no_make_text = _plan_text(no_make)
    assert "QUEUED" not in no_make_text
    assert "CO-RESIDENT with gate:rebuild-contracts' pool (overlap policy)" in no_make_text
    assert "CO-RESIDENT with gate:conform's sweep (overlap policy)" in no_make_text
    assert no_make.contracts_workers == 18
    assert idle.contracts_workers == 18
    assert idle.contracts_reason == "18 of 18 cores, the whole machine (no corpus build to share it with)"
    assert idle.conform_reason.endswith("the cap is the 18 usable cores")
    assert "gate:make-test" not in idle.conform_reason
    assert (
        idle.kernel_reason
        == "the table build's memory ceiling, the one width RAM binds, capped at the configuration count and the cores"
    )
    assert "gate:make-test" not in _plan(**machine).kernel_reason


def test_a_stated_pool_width_is_the_width_the_cycle_reserves_by(monkeypatch):
    """The make-test child inherits this process's environment, so a width already set in PYTEST_XDIST_AUTO_NUM_WORKERS is the width its pool takes. The cycle reserves memory for that width, so the memory set aside for the pool matches the pool that runs."""
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "9")
    assert ac.make_test_pool_width(ncores=1) == 9
    assert ac.kernel_threads_budget(ncores=12, total_bytes=MACHINE_33_6_GB) == 5
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "64")
    assert ac.kernel_threads_budget(ncores=12, total_bytes=MACHINE_33_6_GB) == 1


def test_kernel_threads_budget_takes_the_pytest_pool_off_the_machine_first():
    """The kernel width subtracts the pytest pool along with default's retained memo and the parked fold products before dividing. A 32 GB machine alone has room for six slots, and subtracting an eight-core machine's pytest pool leaves room for five, so the pool costs the build a slot. At this boundary forgetting the memory set aside for the pool changes the answer."""
    solo = ac.kernel_threads_budget(skip_make_test=True, ncores=8, total_bytes=MACHINE_32_GB)
    beside = ac.kernel_threads_budget(ncores=8, total_bytes=MACHINE_32_GB)
    assert (solo, beside) == (6, 5)


def test_the_kernel_fits_as_many_deltas_with_the_test_gates_running_as_run_alone_on_both_fleet_machines(
    monkeypatch,
):
    """The kernel terms (`kernel_exec.table_build_booking_bytes`) are chosen so that gate:make-test's pytest pool costs neither fleet machine a worker slot, and this test checks the gated widths, which only the cycle computes. On both 48 GiB machines, at eighteen and at twelve cores, the gated and skipped-gate widths are equal to the configuration count, every delta and `default`'s fold preparation in a slot of its own, so the wave runs in one round. Each starts every delta from scratch beside `default`, gated or not, because memory still leaves room for all of them with the pool's memory off the machine, at eighteen cores and at twelve. `AMS_KERNEL_THREADS` is cleared first, because an exported width would pass these assertions whatever the constants are."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS

    monkeypatch.delenv("AMS_KERNEL_THREADS", raising=False)
    for ncores in (18, 12):
        gated = ac.kernel_threads_budget(ncores=ncores, total_bytes=MACHINE_48_GIB)
        assert gated == len(SETTLEMENT_CONFIGS)
        assert (
            ac.kernel_threads_budget(skip_make_test=True, ncores=ncores, total_bytes=MACHINE_48_GIB) == gated
        )
        for skip_make_test in (False, True):
            assert (
                ac.scratch_beside_default_budget(
                    gated, skip_make_test=skip_make_test, ncores=ncores, total_bytes=MACHINE_48_GIB
                )
                == len(SETTLEMENT_CONFIGS) - 1
            )


def test_the_deltas_from_scratch_take_the_pytest_pool_off_the_machine_as_the_width_does(monkeypatch):
    """`scratch_beside_default_budget` decides how many deltas start from scratch beside `default` with gate:make-test's pool off the machine when `skip_make_test` is False, as the table build's width does. The terms as they stand book a slot from scratch at a delta's slot, so wherever the whole wave fits every delta fits from scratch too; the test raises `SCRATCH_PEAK_BYTES` by 1 GB so a slot from scratch books more than a delta's. On an invented machine just large enough for the whole wave with every delta from scratch beside `default`, and nothing else, the width stays as it is with the pool off the machine, but fewer deltas fit from scratch. The cycle starts gate:make-test once the run_m1 gate has passed, so its plan passes the count with the pool's memory kept, whether or not the gate runs."""
    from rebuild.pipeline import kernel_exec
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import table_build_booking_bytes
    from rebuild.tools import memory_budget

    monkeypatch.setattr(kernel_exec, "SCRATCH_PEAK_BYTES", kernel_exec.SCRATCH_PEAK_BYTES + 1_000_000_000)
    count = len(SETTLEMENT_CONFIGS)
    every = table_build_booking_bytes(count, configs=count, scratch_beside_default=count - 1)
    total = next(
        total
        for total in range(every, 2 * every, 10_000_000)
        if total - memory_budget.os_reserve_bytes(total_bytes=total) >= every
    )
    pool = ac._make_test_pool_bytes(skip_make_test=False, ncores=8)
    assert table_build_booking_bytes(count, configs=count) + pool <= total - memory_budget.os_reserve_bytes(
        total_bytes=total
    ), "the invented machine is meant to keep the whole wave with the pool running"
    beside = ac.scratch_beside_default_budget(count, skip_make_test=False, ncores=8, total_bytes=total)
    alone = ac.scratch_beside_default_budget(count, skip_make_test=True, ncores=8, total_bytes=total)
    assert beside < alone == count - 1
    for skip_make_test in (False, True):
        plan = _plan(ncores=8, total_bytes=total, skip_make_test=skip_make_test)
        assert plan.kernel_threads == count
        argv = plan.argv("run_m1")
        assert int(argv[argv.index("--scratch-beside-default") + 1]) == plan.scratch_beside_default == alone


def test_replay_threads_budget_takes_the_pytest_pool_off_the_machine_first():
    """The replay width subtracts gate:make-test's pool before dividing, as the kernel width does. So the gated width is never wider than the skipped-gate width, and where the cap does not bind, the pool is what separates them. Both are capped at the configuration count and the cores. The invented machine holds two and a half walks beside the pool at six cores, so the gated width is two by division, and the skipped-gate width, which keeps the pool's memory for walks, lies between that and the cap."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import REPLAY_PEAK_BYTES

    solo = ac.replay_threads_budget(skip_make_test=True, ncores=8, total_bytes=MACHINE_36_GB)
    beside = ac.replay_threads_budget(ncores=8, total_bytes=MACHINE_36_GB)
    assert 1 <= beside <= solo <= len(SETTLEMENT_CONFIGS)
    assert ac.replay_threads_budget(ncores=2, total_bytes=MACHINE_44_GB) == 2
    pool = ac.make_test_pool_width(ncores=6) * ac._font_suite_worker_bytes()
    narrow_machine = 8_000_000_000 + pool + 2 * REPLAY_PEAK_BYTES + REPLAY_PEAK_BYTES // 2
    gated = ac.replay_threads_budget(ncores=6, total_bytes=narrow_machine)
    assert gated == 2
    assert (
        gated
        < ac.replay_threads_budget(skip_make_test=True, ncores=6, total_bytes=narrow_machine)
        < len(SETTLEMENT_CONFIGS)
    )


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
    narrow_machine = 8_000_000_000 + 3 * REPLAY_PEAK_BYTES + REPLAY_PEAK_BYTES // 2
    narrow = ac.replay_threads_derivation(skip_make_test=True, ncores=8, total_bytes=narrow_machine)
    assert narrow.startswith(f"{-(-count // 3)} waves over {count} settlement configurations; 3 at ")
    monkeypatch.setenv("AMS_REPLAY_THREADS", "2")
    stated = ac.replay_threads_derivation(ncores=10, total_bytes=MACHINE_32_GIB)
    assert (
        stated
        == f"{-(-count // 2)} waves over {count} settlement configurations; AMS_REPLAY_THREADS states 2"
    )
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
        f"run_m1 --kernel-threads          : {count}  (the table build's memory ceiling, the one width RAM binds, capped at the configuration count and the cores)"
        in _plan_text(plan)
    )
    monkeypatch.setenv("AMS_KERNEL_THREADS", "99")
    assert ac.kernel_threads_budget(ncores=18, total_bytes=MACHINE_48_GIB) == count


def test_the_table_build_never_reserves_for_the_pytest_pool():
    """gate:make-test starts once the run_m1 gate has passed, so no pytest pool runs beside the table build, and the kernel width is the same whether the gate runs, is auto-skipped, or `--skip-gates` is given."""
    assert _plan(ncores=8, total_bytes=MACHINE_32_GB).kernel_threads == 6
    assert (
        _plan(
            ncores=8, total_bytes=MACHINE_32_GB, skip_make_test=True, make_test_note="closure unchanged"
        ).kernel_threads
        == 6
    )
    assert _plan(ncores=8, total_bytes=MACHINE_32_GB, skip_gates=True).kernel_threads == 6


def test_dry_run_renders_concurrency():
    plan = ac.build_plan(
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        ncores=12,
        total_bytes=MACHINE_44_GB,
    )
    text = _plan_text(plan)
    assert "pool policy: overlap" in text
    assert "Lane t0   [from t=0, background]  : gate:js\n" in text
    assert "Lane make-test                   : starts when run_m1's two JSONs pass" in text
    assert "Lane build" in text
    assert "Lane rebuild-contracts" in text
    assert "Lane conform" in text
    assert "Lane kernel" not in text
    assert "run_m1 -> submit gate:rebuild-contracts -> corpus-build -> land\n" in text
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
    assert "run_m1 --kernel-threads          : " in text
    assert (
        f"run_m1 --replay-threads          : {plan.replay_threads}  (the string replay's own ceiling" in text
    )
    assert plan.replay_threads == ac.replay_threads_budget(
        skip_make_test=True, ncores=12, total_bytes=MACHINE_44_GB
    )
    assert ac.replay_threads_derivation(skip_make_test=True, ncores=12, total_bytes=MACHINE_44_GB) in text
    auto_skipped = _plan_text(_plan(skip_conform=True, conform_note=ac.CONFORM_SKIP_NOTE))
    assert f"Lane conform                     : SKIPPED ({ac.CONFORM_SKIP_NOTE})" in auto_skipped
    assert "Lane conform                     : SKIPPED (--skip-conform)" in _plan_text(
        _plan(skip_conform=True)
    )
    assert "corpus-build --jobs" not in text

    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["run_m1"])[1:6] == [
        "run",
        "python",
        "-m",
        "rebuild.pipeline.run_m1",
        "--kernel-threads",
    ]


def test_review_out_staging_plan(monkeypatch, tmp_path):
    staged_out = tmp_path / "staged"
    plan = ac.build_plan(
        skip_gates=False,
        first_run=False,
        short_id="abc1234",
        review_out=staged_out,
    )
    by_name = {step.name: step for step in plan.steps}
    assert _argv(by_name["corpus-build"])[5:] == ["--out", str(staged_out)]
    assert "land" not in by_name
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


def test_the_summary_leads_with_the_review_diff_line_when_it_has_one():
    """The summary's lines start with the table-diff corpus's `review diff` line when the pass's readiness gives one (`readiness_block`)."""
    lines = ac.summary_cycle_lines(
        _green_report(),
        _plan(),
        [],
        "review diff: 15 unaccepted changes since 1c562ab7c594 (15 need a verdict)",
    )
    assert lines[0] == "  review diff      : 15 unaccepted changes since 1c562ab7c594 (15 need a verdict)"
    bare = ac.summary_cycle_lines(_green_report(), _plan(), [])
    assert bare[0].startswith("  job costs ")


def test_cycle_summary_payload_plan_block_and_argv():
    plan = _plan()
    payload = ac.cycle_summary_payload(_green_report(), [], plan, "ok")
    assert payload["plan"] == {
        "conform_max_length": ac.CONFORM_MAX_LENGTH_DEFAULT,
        "kernel_threads": plan.kernel_threads,
        "scratch_beside_default": plan.scratch_beside_default,
        "replay_threads": plan.replay_threads,
        "make_test_workers": plan.make_test_workers,
        "contracts_workers": plan.contracts_workers,
        "conform_jobs": plan.conform_jobs,
        "pool_policy": ac.REBUILD_POOL_POLICY_DEFAULT,
        "skip_gates": False,
        "skip_conform": False,
        "skip_run_m1": False,
        "rerun_gates_only": False,
        "rerun_kern_only": False,
        "skip_corpus": False,
        "refresh_assets": False,
        "promote_corpus": None,
        "next_corpus": str(ac.next_corpus_dir()),
        "land": True,
        "skip_contracts": False,
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


def test_the_land_row_says_whether_the_land_carried_the_store_and_what_it_kept_aside():
    """The land row names the stamp the land landed and the verdicts the store holds after it, whether it carried the store onto that stamp, what it orphaned and dropped, and how long it held the store's lock."""
    landed = {"landed": True, "records": 1200, "new_stamp": "B", "locked_s": 0.4}
    assert ac.land_detail({**landed, "carried": True, "orphaned": 3, "skips_dropped": 1500}) == (
        "carried 1,200 verdicts onto B, 3 orphaned, 1,500 skips dropped, store locked 0.4 s"
    )
    assert ac.land_detail({**landed, "carried": False, "orphaned": 0, "skips_dropped": 0}) == (
        "landed B, the store's 1,200 verdicts as they were, store locked 0.4 s"
    )


def test_cycle_summary_payload_names_the_gates_only_rerun_and_passes_no_kernel_width_to_it():
    """The summary must tell the three run_m1 modes apart. A gates-only rerun is not a skip, and the thread widths it reports are the ones passed to the child, which for a gates-only rerun are none."""
    plan = _plan(rerun_gates_only=True, run_m1_note="only comparison-side inputs moved")
    payload = ac.cycle_summary_payload(_green_report(), [], plan, "ok")
    assert payload["plan"]["rerun_gates_only"] is True
    assert payload["plan"]["skip_run_m1"] is False
    assert payload["plan"]["kernel_threads"] is None
    assert payload["plan"]["scratch_beside_default"] is None
    assert payload["plan"]["replay_threads"] is None


def test_cycle_summary_payload_records_null_for_the_width_of_each_step_the_pass_skips():
    """A width is recorded only for a step that spawns at it, so a run line never names a width nothing ran at, including gate:conform's when its green record skips it after run_m1."""
    widths = (
        "make_test_workers",
        "contracts_workers",
        "conform_jobs",
    )
    skipped = _plan(
        skip_run_m1=True,
        skip_corpus=True,
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
    proved = _green_report()
    proved.conform_proven = True
    assert ac.cycle_summary_payload(proved, [], _plan(), "ok")["plan"]["conform_jobs"] is None


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


def _replace_store(path: Path, data: dict) -> None:
    """Write a verdicts file through a temporary file and a rename, as every writer of the store, of the pass's snapshot and of its prepared store does, so a write never reaches the names `landing.snapshot` linked to the same file."""
    tmp = path.with_name(path.name + ".write")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


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
        "site/print.typ",
        ".gitignore",
        ".markdownlint-cli2.yaml",
        ".pre-commit-config.yaml",
        ".prettierrc",
        ".git-blame-ignore-revs",
        "LICENSE-OFL-1.1.txt",
    ):
        assert ac.make_test_exempt(path), path
    for path in (
        "glyph_data/latin_letters.yaml",
        "glyph_data/punctuation.yaml",
        "tools/build_font.py",
        "test/test_outlines.py",
        "test/test_shared.py",
        "site/the-manual.html",
        "site/shared.js",
        "conftest.py",
        "pyproject.toml",
        "postscript_glyph_names.yaml",
        "typings/uharfbuzz/__init__.pyi",
        "reference/DepartureMono-Regular.otf",
        "reference/LICENSE.DepartureMono.txt",
        "reference/nested/a.pdf",
        "site/nested/a.svg",
        "uv.lock",
        "rebuild/validation/check.py",
        "rebuild/tools/suite_fonts.py",
    ):
        assert not ac.make_test_exempt(path), path


def test_make_tests_rebuild_files_are_the_pin_checker_the_root_conftest_imports():
    """`MAKE_TEST_REBUILD_FILES` is the font suite's pin checker: the root conftest's import closure under rebuild/validation/ and the module that names the fonts it checks. A rebuild/validation module the conftest reaches that the list misses could change what the suite asserts while its key holds; a rebuild module it does not reach stays exempt."""
    closure = ac.contracts.ImportGraph(REPO_ROOT).closure("conftest.py")
    checker = {rel for rel in closure if rel.startswith("rebuild/validation/")} | {
        "rebuild/tools/suite_fonts.py"
    }
    assert set(ac.MAKE_TEST_REBUILD_FILES) == checker
    assert "rebuild/tools/suite_fonts.py" in closure
    assert ac.make_test_exempt("rebuild/pipeline/settle.py")


def test_make_tests_font_lines_key_the_senior_and_what_mono_and_junior_are_built_from(tmp_path, monkeypatch):
    """`make_test_font_lines` keys the fonts the session publishes: the Senior's two styles under rebuild/out/m1 by content, its glyph-facts sidecar, whether it is current, and the import closure under rebuild/ of what builds Mono and Junior with the spec's data. An edit to a module outside that closure moves no line, and one inside it moves a family line."""
    from rebuild.pipeline import glyph_facts
    from rebuild.tools import build_fonts

    root = tmp_path / "root"
    files = {
        "rebuild/pipeline/build_family.py": "from rebuild.pipeline import surface\n",
        "rebuild/pipeline/surface.py": "SURFACE = 1\n",
        "rebuild/pipeline/settle.py": "SETTLE = 1\n",
        "rebuild/tools/build_fonts.py": "BUILD_FONTS = 1\n",
        "rebuild/script.yaml": "heights: {baseline: 0}\n",
        "glyph_data/runes/qsMay.yaml": "rune: qsMay\n",
        "rebuild/out/m1/M1.otf": "not a font\n",
        "rebuild/out/m1/M1-Bold.otf": "not a bold font\n",
        f"rebuild/out/m1/{glyph_facts.FILE_NAME}": "{}\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    staleness: list[str | None] = [None]
    monkeypatch.setattr(build_fonts, "senior_staleness", lambda at: staleness[0])

    def lines() -> dict[str, str]:
        return dict(line.split("\t", 1) for line in ac.make_test_font_lines(root))

    base = lines()
    assert base["senior_current"] == "current"
    assert {key for key in base if key.startswith("family_code ")} == {
        "family_code rebuild/pipeline/build_family.py",
        "family_code rebuild/pipeline/surface.py",
        "family_code rebuild/tools/build_fonts.py",
    }
    assert "family_data glyph_data/runes/qsMay.yaml" in base
    (root / "rebuild/pipeline/settle.py").write_text("SETTLE = 2\n")
    assert lines() == base
    (root / "rebuild/pipeline/surface.py").write_text("SURFACE = 2\n")
    moved = lines()
    assert {key for key in base if moved[key] != base[key]} == {"family_code rebuild/pipeline/surface.py"}
    (root / "rebuild/out/m1/M1.otf").write_text("another font\n")
    staleness[0] = "its inputs moved"
    again = lines()
    assert {key for key in moved if again[key] != moved[key]} == {"senior Regular", "senior_current"}
    (root / "rebuild/out/m1/M1-Bold.otf").write_text("another bold font\n")
    bold = lines()
    assert {key for key in again if bold[key] != again[key]} == {"senior Bold"}


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
    """The Makefile is not hashed as a file. It enters the fingerprint as one `make -n` probe line per rule the suite runs (`test`), so a comment or an unrelated target does not make the gate due."""
    root = _git_repo(tmp_path)
    files = ac.make_test_closure_files(root)
    assert files is not None
    assert "Makefile" not in files
    lines = ac.make_test_recipe_lines(root)
    assert lines is not None
    assert [line.split("\t")[0] for line in lines] == ["make -n test"]


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
    """Editing the rule the suite runs, `test`, changes the key."""
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("\techo test ", "\techo test --verbose "))
    assert ac.make_test_closure_fingerprint(root) not in (None, first)


def test_closure_fingerprint_ignores_makefile_edits_the_suite_never_executes(tmp_path):
    """A comment, a target that `make test` does not run, `all` among them, and a new rule all leave the key unchanged, so edits to the build, cycle and kernel targets do not re-run the font suite."""
    root = _git_repo(tmp_path)
    first = ac.make_test_closure_fingerprint(root)
    assert first is not None
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("# comment", "# a different comment"))
    assert ac.make_test_closure_fingerprint(root) == first
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("\techo kernel", "\techo kernel --check"))
    assert ac.make_test_closure_fingerprint(root) == first
    (root / "Makefile").write_text(FAKE_MAKEFILE.replace("\techo build", "\techo build --twice"))
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
    """`MAKE_TEST_RECIPE_PINS` must pin every variable the repository's real `test` rule reads, not only the one the fake Makefile uses. The test runs the probe on the live Makefile with FORCE=1 in the environment and expects the same probe line as without it."""
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
    assert (
        "Lane make-test                   : SKIPPED (closure unchanged since its last green run)" in rendered
    )
    assert "CO-RESIDENT with gate:rebuild-contracts' pool (overlap policy)" in _lane_conform_line(plan)
    assert "CO-RESIDENT with gate:conform's sweep (overlap policy)" in rendered
    alone = _plan_text(_plan(**skipped, skip_conform=True))
    assert "CO-RESIDENT" not in alone
    assert "no other heavy gate pool runs this pass" in alone
    queued = _plan_text(_plan(**skipped, pool_policy="queue"))
    assert "gate:make-test not running, so no queueing" in queued
    assert "QUEUED behind gate:conform (queue policy — one heavy pool at a time)" in queued


def test_a_pass_whose_run_m1_rewrites_the_senior_leaves_make_tests_skip_to_its_wrapper(tmp_path, monkeypatch):
    """gate:make-test's key covers the rebuilt Senior, which a full or kern-only build rewrites, so a pass whose run_m1 builds cannot prove the skip from a matching green record before the build: it plans the gate to run, shows it as `run?` with the condition, and the wrapper decides once run_m1 has finished. `test_a_forced_pass_hands_the_make_test_wrapper_force` shows a pass whose run_m1 skips deciding at plan time."""
    _unsettled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "make_test_closure_fingerprint", lambda root=None: "fp")
    ac.record_make_test_green("fp")

    plan = _planned([])
    step = {step.name: step for step in plan.steps}["gate:make-test"]
    assert not step.skipped
    assert step.argv == ["make", "test"]
    assert plan.make_test_undecided()
    (row,) = [row for row in ac.plan_rows(plan) if row.name == "gate:make-test"]
    assert (row.status, row.note) == (console.STATUS_MAYBE, ac.MAKE_TEST_MAYBE_NOTE)


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
    """The wrapper behind `make test` reads the green record itself and exits early when the closure matches it. So `--fresh` and `--force-make-test` must reach the wrapper, not only the plan: the argv carries FORCE=1, the live Makefile turns it into `--force`, and the wrapper runs the suite. A plain pass over the same closure whose run_m1 skips plans the gate as skipped."""
    _settled_repo(tmp_path, monkeypatch)
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
    """The plan sets the gate widths before `make test` decides anything, so the memory and cores set aside for `make test` are correct only if the plan and the wrapper make the same skip decision. Memory reserved for a gate that then skips on its own green record is lost to the conformance sweep beside it. On a pass whose run_m1 skips, so that the rebuilt Senior stays as it is, every combination of forcing flag and green record ends one of two ways. Either the plan skips the gate, the wrapper would also skip over the same fingerprint and record, and the conformance sweep gets the pool's memory back. Or the plan runs the gate, the argv the live Makefile passes to the wrapper runs the suite, and its pool is subtracted from the conformance sweep's terms under the overlap policy. The table build's width never subtracts it, since the gate starts once the run_m1 gate has passed."""
    _settled_repo(tmp_path, monkeypatch)
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

    assert plan.skip_make_test is not runs
    assert plan.conform_jobs == ac.conform_job_budget(
        skip_gates=False,
        skip_make_test=not runs,
        skip_corpus=plan.skip_corpus,
        pool_policy=plan.pool_policy,
        build_lane_runs=not (plan.skip_run_m1 and plan.skip_corpus),
    )
    assert plan.kernel_threads == ac.kernel_threads_budget(skip_make_test=True)


def test_the_contracts_pool_is_the_cores_the_corpus_build_leaves():
    """The rebuild suite's width under a cycle is a fan-out that memory does not derive. It runs beside the corpus build, so it gets the cores less the build's one process. Under the overlap policy it also loses gate:make-test's pool. Under the queue policy the suite waits until that pool finishes, so nothing is subtracted for it. With no corpus build it gets every core under the queue policy and every core less gate:make-test's pool under the overlap policy, and it never drops below one."""
    queue = ac.contracts_pool_width(
        skip_gates=False, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert queue == 9
    assert f"9 of 10 cores, less {ac.CORPUS_BUILD_PROCESS}" == (
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
    assert solo == 9

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

    assert ac.contracts_pool_width(skip_gates=False, ncores=1, total_bytes=MACHINE_32_GIB) == 1
    assert ac.contracts_pool_derivation(skip_gates=False, ncores=1, total_bytes=MACHINE_32_GIB).endswith(
        "floored at one"
    )


def test_the_plan_names_the_process_of_the_corpus_build_that_will_run(monkeypatch):
    """The plan's clauses name what they subtract for the corpus build, its one process, so the clause and the width it explains count the same cores."""
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    kw: dict[str, Any] = dict(skip_gates=False, pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB)
    assert ac.contracts_pool_derivation(**kw).endswith(f"less {ac.CORPUS_BUILD_PROCESS}")
    assert ac.conform_job_derivation(**kw, skip_corpus=False).endswith(
        f"since 10 cores less {ac.CORPUS_BUILD_PROCESS} leave 9"
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
    """The lane line shows the suite's width and its derivation, and the build lane line shows the suite submitted before the corpus build and the land after it. Beside a corpus build, the queue policy gives the suite every core less the build's process, nine workers on a ten-core machine and eleven on a twelve-core one. Under the overlap policy gate:make-test's pool, the gate lane's share, comes off as well, leaving four and five. When the corpus build is skipped, the plan says the suite is submitted once the run_m1 gate passes, not that it runs beside a build the plan shows as SKIPPED."""
    gated = _plan(pool_policy="queue", ncores=10, total_bytes=MACHINE_32_GIB)
    text = _plan_text(gated)
    assert (
        "Lane build[main thread]          : run_m1 -> submit gate:rebuild-contracts -> corpus-build -> land\n"
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
    assert overlap.contracts_workers == 4 < gated.contracts_workers == 9
    assert overlap.contracts_reason.endswith("less gate:make-test's 5 (overlap policy)")
    assert f"-n {overlap.contracts_workers} (" in _plan_text(overlap)
    assert "CO-RESIDENT with gate:make-test's pool and gate:conform's sweep (overlap policy)" in _plan_text(
        overlap
    )
    roomy = _plan(pool_policy="queue", ncores=12, total_bytes=MACHINE_48_GIB)
    roomy_overlap = _plan(pool_policy="overlap", ncores=12, total_bytes=MACHINE_48_GIB)
    assert roomy_overlap.contracts_workers == 5 < roomy.contracts_workers == 11
    assert roomy_overlap.contracts_reason.endswith("less gate:make-test's 6 (overlap policy)")
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
    assert (
        "Lane build[main thread]          : run_m1 -> submit gate:rebuild-contracts -> corpus-build -> land\n"
        in solo_text
    )
    assert {step.name: step for step in solo.steps}["gate:rebuild-contracts"].note == (
        "submitted once the run_m1 gate passes (no corpus build this pass)"
    )

    assert "Lane rebuild-contracts" not in _plan_text(
        _plan(skip_gates=True, ncores=10, total_bytes=MACHINE_32_GIB)
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


def test_run_m1_skip_fingerprint_moves_with_runes_the_lock_and_the_crate(tmp_path):
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
    (tmp_path / "uv.lock").write_text("lock-2")
    fourth = ac.run_m1_skip_fingerprint(tmp_path)
    assert fourth != second
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
    """Build a repo skeleton with one file of each kind the run_m1 skip key reads: each data input, the contact allow-list, the table-side and comparison-side pipeline code, the crate, and uv.lock. It also writes a behavior-class sidecar, the compile code and M1.otf for the conform key. The files are real so the tests see the labels the real readers produce."""
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
        ("glyph_data/latin_letters.yaml", "glyphs: {a: {bitmap: ['#']}}\n"),
        ("rebuild/m1-contact-allow.yaml", "- signature: junction-1\n  why: blessed once\n"),
        ("rebuild/pipeline/manual_pins.py", "pins = 1\n"),
        ("rebuild/pipeline/settle.py", "settle = 1\n"),
        ("rebuild/validation/shaper.py", "shape = 1\n"),
        ("rebuild/kernel-rs/Cargo.toml", "[package]\n"),
        ("rebuild/kernel-rs/Cargo.lock", "[[package]]\n"),
        ("rebuild/kernel-rs/src/lib.rs", "fn settle() {}\n"),
        ("uv.lock", "lock-1\n"),
    ):
        (tmp_path / rel).write_text(text)
    (tmp_path / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(b"OTTO")
    return tmp_path


def test_comparison_side_label_names_only_the_inputs_no_table_stage_reads():
    """`comparison_side_label` decides which inputs may change while a cycle reuses the enumeration and font on disk, so a label wrongly on it would let a pass rerun the gates against artifacts that no longer match their sources. uv.lock is left off although the tables' stamp does not cover it, because it pins fontTools and uharfbuzz, and a bump there can change the font the rerun would rely on."""
    from rebuild.pipeline import fingerprint

    assert ac.comparison_side_label(fingerprint.CONTACT_ALLOW_LABEL)
    assert all(ac.comparison_side_label(label) for label in fingerprint.COMPARISON_CODE_MODULES)
    assert not ac.comparison_side_label("uv.lock")
    assert not ac.comparison_side_label("glyph_data/runes/qsPea.yaml")
    assert not ac.comparison_side_label("rebuild/script.yaml")
    assert not ac.comparison_side_label("rebuild/pipeline/settle.py")
    assert not ac.comparison_side_label("rebuild/kernel-rs/src/lib.rs")


def test_every_run_m1_label_is_stamped_the_toolchain_kern_or_comparison_side(tmp_path):
    """Every label in the run_m1 key must be covered by the tables' stamp, named by `comparison_side_label`, or reach the font alone (`font_only_label`: a rune's kern line or a font-data line); otherwise a gates-only rerun or a kern-only rebuild would reuse artifacts that no longer match that input. The one exception is uv.lock, which is none of them, so a change to it prevents both. A new key line that fits no group fails this test."""
    from rebuild.pipeline import fingerprint

    root = _fake_run_m1_root(tmp_path)
    stamped = {line.split("\t", 1)[0] for line in fingerprint.data_lines(root)}
    stamped |= {
        line.split("\t", 1)[0] for line in fingerprint.path_lines(root, fingerprint.table_code_paths(root))
    }
    labels = [line.split("\t", 1)[0] for line in ac.run_m1_skip_lines(root)]
    assert [
        label
        for label in labels
        if label not in stamped
        and label != "uv.lock"
        and not ac.comparison_side_label(label)
        and not ac.font_only_label(label)
    ] == []
    assert stamped & set(labels)
    assert [label for label in labels if ac.comparison_side_label(label)]
    assert [label for label in labels if ac.kern_side_label(label)]
    assert [label for label in labels if ac.font_data_label(label)] == ["glyph_data/latin_letters.yaml#font"]
    assert not stamped & {label for label in labels if ac.font_only_label(label)}
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


def test_a_comparison_side_edit_moves_the_run_key_and_leaves_the_sweeps_alone(tmp_path):
    """The run_m1 key and the conform key cover different inputs. The conformance sweep shapes the compiled font and re-settles the windows beside it. It reads no allow-list or Manual-pin code, so editing either changes the run_m1 key and leaves the conform key alone. A rune edit, a crate edit, a uv.lock edit and the font's bytes also leave the conform key alone; the crate's string replay inside run_m1 covers rune and crate edits. A behavior class the lookup has not emitted before, the compile code, the tools/ files the compile runs, the uharfbuzz version and the maximum length each change it."""
    root = _fake_run_m1_root(tmp_path)
    conform = ac.conform_skip_fingerprint(root, 4)
    run_key = ac.run_m1_skip_fingerprint(root)
    for rel, text in (
        ("rebuild/m1-contact-allow.yaml", "- signature: junction-2\n"),
        ("rebuild/pipeline/manual_pins.py", "pins = 2\n"),
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
        "rebuild/m1-contact-allow.yaml": "c1",
        "rebuild/pipeline/manual_pins.py": "m1",
        "rebuild/validation/expect.py": "e1",
        "uv.lock": "l1",
    }
    record = {"files": dict(stored)}
    assert ac.gates_only_rerun(record, dict(stored)) is None
    assert ac.gates_only_rerun(None, dict(stored)) is None
    assert ac.gates_only_rerun({"fingerprint": "fp"}, dict(stored)) is None

    blessed = {**stored, "rebuild/m1-contact-allow.yaml": "c2", "rebuild/pipeline/manual_pins.py": "m2"}
    assert ac.gates_only_rerun(record, blessed) == [
        "rebuild/m1-contact-allow.yaml",
        "rebuild/pipeline/manual_pins.py",
    ]
    assert ac.moved_input_labels(record, blessed) == [
        "rebuild/m1-contact-allow.yaml",
        "rebuild/pipeline/manual_pins.py",
    ]
    assert ac.moved_inputs_note(record, blessed) == (
        "rebuild/m1-contact-allow.yaml (changed), rebuild/pipeline/manual_pins.py (changed)"
    )
    assert ac.gates_only_rerun(record, {**stored, "rebuild/validation/expect.py": "e2"}) == [
        "rebuild/validation/expect.py"
    ]

    dropped = {name: value for name, value in stored.items() if name != "rebuild/m1-contact-allow.yaml"}
    assert ac.gates_only_rerun(record, dropped) == ["rebuild/m1-contact-allow.yaml"]

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


def test_m1_artifacts_present(tmp_path):
    """The plain skip's predicate needs every gate summary, every artifact the corpus build reads, the kern inventory, which only a build writes, and every settlement configuration's windows head and head file, so a missing gate summary alone fails it, and so does a missing inventory, and so does a missing head. A gates-only rerun is planned on `m1_gates_only_base_present` instead."""
    from rebuild.pipeline import conform

    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    names = [
        *(path.name for path in cycle_paths.M1_SUMMARY_FILES.values()),
        *ac.M1_ARTIFACT_NAMES,
        *ac.m1_table_head_names(),
    ]
    assert len(ac.m1_table_head_names()) == 2 * len(conform.SETTLEMENT_CONFIGS)
    assert "windows-default.tsv.gz.head" in names and "windows-default.head.json.gz" in names
    assert not ac.m1_artifacts_present(tmp_path)
    for name in names:
        (m1 / name).write_text("{}")
    assert ac.m1_artifacts_present(tmp_path)
    (m1 / "manual_pins_summary.json").unlink()
    assert not ac.m1_artifacts_present(tmp_path)
    (m1 / "manual_pins_summary.json").write_text("{}")
    (m1 / "M1.otf").unlink()
    assert not ac.m1_artifacts_present(tmp_path)
    (m1 / "M1.otf").write_text("{}")
    (m1 / "kern-inventory.json").unlink()
    assert not ac.m1_artifacts_present(tmp_path)
    (m1 / "kern-inventory.json").write_text("{}")
    (m1 / "windows-ss04.tsv.gz.head").unlink()
    assert not ac.m1_artifacts_present(tmp_path)


def test_rebuild_gate_closure_scope_and_exemptions(tmp_path):
    """The test checks both edges of the closure. The paths in `cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES` are files no test reads: the JS-only jstests and the contact allow-list, which only the defect gate reads, so adding a contact signature does not re-run the suite. The other edge is `REBUILD_GATE_HARNESS_PATHS`, the files the suite reads outside rebuild/ and glyph_data/. Only listed paths are included, so `tools/outside.py` stays out, and `doc/glyph-names.md` is included although other Markdown is filtered out. A tracked file deleted in the working tree has nothing to read, so it stays out too."""
    assert "rebuild/m1-contact-allow.yaml" in cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild" / "review" / "jstests").mkdir(parents=True)
    (tmp_path / "glyph_data" / "runes").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "rebuild" / "test_x.py").write_text("")
    (tmp_path / "rebuild" / "NOTES.md").write_text("")
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
    subprocess.run(["git", "add", "rebuild/test_x.py"], cwd=tmp_path, check=True)
    (tmp_path / "rebuild" / "test_x.py").unlink()
    assert "rebuild/test_x.py" not in (ac.rebuild_gate_closure_files(tmp_path) or [])


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


def test_every_harness_file_moves_the_contracts_key(tmp_path):
    """Every file in `REBUILD_GATE_HARNESS_PATHS` is read under `pytest rebuild/`: the tests that compile a font run the tools/ compile modules, and `rebuild_gate_closure_files` names the reader of every other entry. So editing any of them must change the lane key instead of skipping on a green record that did not see the edit."""
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


def test_the_harness_roster_names_the_font_compile_modules_in_tools():
    """The harness roster's tools/ entries are exactly the font compile's modules. The suite reaches tools/ only through the font compile, whose import closure there rebuild/test_build_code_closure.py holds within that list. This test fails when the list changes and the roster does not."""
    from rebuild.pipeline import fingerprint

    assert {rel for rel in ac.REBUILD_GATE_HARNESS_PATHS if rel.startswith("tools/")} == {
        f"tools/{name}" for name in fingerprint.FONT_COMPILE_TOOL_MODULES
    }


def test_contracts_fingerprint_is_none_outside_git(tmp_path):
    assert ac.contracts_fingerprint(tmp_path) is None


def test_corpus_build_skippable_matches_manifest(tmp_path):
    """A skip means a table-diff build would reproduce this corpus byte for byte, so the test checks each thing that must match: the mode, the inputs fingerprint (and the `ignore` exemption), every shard the manifest names, both app sidecars, and the after font, which must be M1.otf byte for byte, since the corpus's kerning units show its `kern` lookup, so a kern-only rebuild that rewrote M1.otf makes it stale. The sidecars are written after the manifest and outside it, so each must be stamped for the current manifest, not merely present. A corpus of another mode is never skippable, since the cycle builds the table-diff corpus."""
    from rebuild.review import app_index

    expected = _promotion_root(tmp_path)
    font = tmp_path / "rebuild" / "out" / "m1" / "M1.otf"
    corpus = tmp_path / "rebuild" / "out" / "review"
    shard = corpus / "units-000.json"

    def restamp(recorded=expected, stamp="2026-01-01T00:00:00Z", **options):
        _write_corpus(corpus, recorded, stamp, **options)

    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)
    shard.unlink()
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    restamp(recorded={**expected, "tables": "changed"})
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    restamp(recorded={**expected, "snapshot": "another"})
    assert not ac.corpus_build_skippable(tmp_path, corpus)

    restamp(recorded={**expected, "static": "moved"})
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    assert ac.corpus_build_skippable(tmp_path, corpus, ignore=("static",))
    restamp(recorded={key: value for key, value in expected.items() if key != "static"})
    assert not ac.corpus_build_skippable(tmp_path, corpus, ignore=("static",))

    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)
    restamp(mode="another-mode")
    assert not ac.corpus_build_skippable(tmp_path, corpus)

    restamp()
    font.write_bytes(b"OTTO-kerned")
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    font.write_bytes(b"OTTO")
    assert ac.corpus_build_skippable(tmp_path, corpus)
    restamp(fonts=False)
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    restamp()
    assert ac.corpus_build_skippable(tmp_path, corpus)

    for name, _fmt in app_index.ARTIFACTS:
        kept = app_index.artifact_path(corpus, name)
        raw = kept.read_bytes()
        kept.unlink()
        assert not ac.corpus_build_skippable(tmp_path, corpus)
        kept.write_bytes(raw)
    assert ac.corpus_build_skippable(tmp_path, corpus)

    # A manifest rewritten without its sidecars: every shard is present and the fingerprint matches, so only the stale stamps on the files beside it show the mismatch.
    restamp()
    restamp(stamp="2026-02-02T00:00:00Z", sidecars=False)
    assert not ac.corpus_complete(corpus)
    assert not ac.corpus_build_skippable(tmp_path, corpus)
    restamp(stamp="2026-02-02T00:00:00Z")
    assert ac.corpus_complete(corpus)
    assert ac.corpus_build_skippable(tmp_path, corpus)


def _promotion_root(root):
    """Write M1.otf under `root`, and return the inputs fingerprint a table-diff corpus must record to be skippable against it: no current snapshot and no windows heads there, so its `snapshot` and `tables` are None (`status.table_diff_inputs`)."""
    from rebuild.review import status

    m1 = root / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True, exist_ok=True)
    (m1 / "M1.otf").write_bytes(b"OTTO")
    return status.table_diff_inputs(root, m1_out=m1)


def _write_corpus(corpus, recorded, stamp, *, sidecars=True, mode="table-diff", fonts=True):
    """Write a synthetic corpus like the one in `test_corpus_build_skippable_matches_manifest`: one shard, a manifest of `mode` with `recorded` as its inputs fingerprint, `stamp` as its generated_at and, with `fonts`, M1.otf's sha256 as its after font's, and, with `sidecars`, the app sidecars stamped for that manifest. Without `sidecars`, those an earlier call wrote stay stamped for its manifest, as a build stopped before them leaves them."""
    from rebuild.review import app_index

    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "units-000.json").write_text("[]")
    manifest = {
        "format": "ams-review-manifest/2",
        "mode": mode,
        "generated_at": stamp,
        "inputs_fingerprint": recorded,
        "classes": [{"id": "c", "shards": ["units-000.json"]}],
        "totals": {"units": 1, "rows": 1, "batches": 1},
    }
    if fonts:
        manifest["fonts"] = {
            "after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(b"OTTO").hexdigest()}
        }
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    if sidecars:
        app_index.write_app_artifacts(corpus, {}, {})


def test_promotable_corpus_names_a_current_staged_corpus_and_refuses_the_rest(tmp_path):
    """`promotable_corpus` returns a directory a live pass may move into place, so each precondition must reject on its own: a missing directory, the live directory itself, a staged corpus whose fingerprint no longer matches, one stamped older than the live corpus, and an unreadable manifest on either side. `corpus_build_skippable` cannot detect the stamp case, because `generated_at` is the newest input mtime, not a build time. Rejecting it keeps a pass from landing a corpus older than the one the store was carried onto."""
    expected = _promotion_root(tmp_path)
    live = tmp_path / "rebuild" / "out" / "review"
    staged = tmp_path / "var" / "staged-review"
    summary = tmp_path / "rebuild" / "out" / "cycle_summary.json"
    _write_corpus(live, expected, "2026-01-01T00:00:00Z")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps({"plan": {"review_out": "rebuild/out/review"}}))
    assert ac.promotable_corpus(tmp_path, summary, live) is None
    summary.unlink()
    _write_corpus(live, {**expected, "font_inputs": "stale"}, "2026-01-01T00:00:00Z")
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
    _write_corpus(live, {**expected, "font_inputs": "stale"}, "2026-01-01T00:00:00Z")
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
    """A complete `review.next` a stopped pass left is the first candidate, ahead of the staging directories, when it reproduces these inputs; one built for other inputs is passed over for the next candidate, and so is one whose build stopped before its sidecars, which the next pass deletes as unfinished."""
    expected = _promotion_root(tmp_path)
    live = tmp_path / "rebuild" / "out" / "review"
    _write_corpus(live, {**expected, "font_inputs": "stale"}, "2026-01-01T00:00:00Z")
    staged = tmp_path / "var" / "staged-review"
    _write_corpus(staged, expected, "2026-01-02T00:00:00Z")
    summary = tmp_path / "rebuild" / "out" / "cycle_summary.json"
    beside = ac.next_corpus_dir(live)
    _write_corpus(beside, expected, "2026-01-02T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) == beside
    _write_corpus(beside, {**expected, "font_inputs": "older"}, "2026-01-02T00:00:00Z")
    assert ac.promotable_corpus(tmp_path, summary, live) == staged
    _write_corpus(beside, expected, "2026-01-03T00:00:00Z", sidecars=False)
    assert ac.promotable_corpus(tmp_path, summary, live) == staged
    note = ac.recover_next_corpus(live)
    assert note is not None and note.startswith("Deleted ") and not beside.exists()


def test_a_kept_review_next_is_emptied_by_the_build_when_run_m1_is_due(tmp_path, monkeypatch, capsys):
    """A complete `review.next` is promoted only on a pass whose run_m1 skipped, because the check compares it with the build on disk. On a pass whose run_m1 runs, the corpus build empties it and builds there, since the table-diff build starts from an empty directory, and the plan says so. An unfinished one is deleted at the start of a real pass and left by a dry run."""
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
    assert "corpus-seed" not in out

    note = ac.recover_next_corpus()
    assert note is not None and note.startswith("Deleted ")
    assert not beside.exists()

    _write_corpus(beside, {}, "2026-07-17T20:24:44Z")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Kept " in out
    built = _step_lines(out, "corpus-build")
    assert "which it empties first" in built
    assert "--out " + str(beside) in built


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


def test_a_landed_corpus_still_answers_for_itself(tmp_path):
    """Every stamp inside a corpus depends only on its manifest's content, so a corpus the land swaps in from another path still passes the skip's checks on both app sidecars. The outgoing tree ends at the staged path."""
    from rebuild.review import app_index

    source = tmp_path / "staged"
    _write_corpus(source, {}, "2026-01-01T00:00:00Z")
    live = tmp_path / "review"
    live.mkdir()
    (live / "stale.txt").write_text("outgoing")

    landing.exchange_dirs(source, live)

    assert (source / "stale.txt").exists()
    assert not (live / "stale.txt").exists()
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(live, name, fmt), name
    assert ac.corpus_complete(live)


REFUSE_RUNE = "rune: qsX\npolicy:\n  refuse:\n  - {exit: baseline, why: two verticals render thick}\n"


def _stamped_corpus(root):
    """Write a table-diff review corpus stamped for the current inputs under `root`, so `corpus_build_skippable` is true right after. After an edit, a failed skip shows the edit reached the corpus's stamp, and a passing skip shows it did not."""
    from rebuild.review import status

    m1 = root / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True, exist_ok=True)
    (m1 / "M1.otf").write_bytes(b"OTTO")
    corpus = root / "rebuild" / "out" / "review"
    _write_corpus(corpus, status.table_diff_inputs(root, m1_out=m1), "2026-01-01T00:00:00Z")
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


def test_a_refuse_why_edit_moves_neither_the_corpus_nor_anything_upstream(tmp_path):
    """No key the table-diff corpus records reads a rune's prose, a refusal's `why` among them, and nothing that builds an artifact reads it either, so the corpus stays skippable and the run_m1 key, the conform key, the tables' stamp, the Stage A record and the lane key all stay unchanged."""
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
    assert ac.corpus_build_skippable(root, corpus)
    assert _upstream_keys(root) == upstream


def test_dry_run_plan_skip_run_m1_and_corpus_lands_nothing():
    """A pass with nothing to rebuild skips run_m1 and the corpus build, lands nothing, and still runs the gates its records do not answer for."""
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
    assert "land" not in by_name and not plan.land
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
        queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
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
    """A green pass prints the `make verdict-ready` checklist itself instead of telling the reader to run the command, and, on a table-diff corpus, the `review diff` line counting its unaccepted changes first among the summary's lines: unaccepted changes leave the pass green and READY. A red pass prints none of it, and its failure block names the next step."""
    seen: list[ac.Plan] = []

    def block(plan):
        seen.append(plan)
        return (
            ["Review corpus: here", "  ✓ gates: green", "", "READY - adjudicate at the review queue"],
            "review diff: 15 unaccepted changes since 1c562ab7c594 (15 need a verdict)",
        )

    monkeypatch.setattr(cycle_paths, "READINESS_ENABLED", True)
    monkeypatch.setattr(ac, "readiness_block", block)
    plan = _plan()
    assert ac._finish(_green_report(), [], plan) == 0
    out = capsys.readouterr().out
    assert seen == [plan]
    assert "READY - adjudicate at the review queue" in out
    assert "make verdict-ready" not in out
    assert out.index("  ✓ gates: green") < out.index("Cycle complete.")
    assert out.index(
        "  review diff      : 15 unaccepted changes since 1c562ab7c594 (15 need a verdict)"
    ) < out.index("  ✓ gates: green")

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

    checklist, diff_line = ac.readiness_block(_plan(recipe_serves=True))
    assert checklist[-1].startswith("READY") and diff_line is None
    assert ac.readiness_block(_plan())[0][-1].startswith("READY")
    assert asked == [False, True]
    assert ac.readiness_block(_plan(review_out=Path("/tmp/staged"))) == ([], None)
    assert asked == [False, True]


def test_the_readiness_block_reports_a_checklist_it_could_not_compute(monkeypatch):
    from rebuild.tools import verdict_ready

    def boom(**kwargs):
        raise RuntimeError("no manifest")

    monkeypatch.setattr(verdict_ready, "readiness", boom)
    assert ac.readiness_block(_plan()) == (
        ["readiness: the checklist could not be computed (RuntimeError('no manifest'))"],
        None,
    )


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
    """Return the match for the rendered run_m1 step of a plan that builds, so the negative cases can show a build was planned instead of the gates-only argv. The count of deltas started beside `default` and then `--replay-threads` always follow `--kernel-threads`, and `--fresh-tables` appears only under `--fresh`."""
    return re.search(
        r"^ +\$ uv run python -m rebuild\.pipeline\.run_m1"
        r" --kernel-threads \d+ --scratch-beside-default \d+"
        r" --replay-threads \d+( --fresh-tables)?$",
        _step_lines(out, "run_m1"),
        re.MULTILINE,
    )


def _comparison_side_drift(tmp_path, monkeypatch, moved="rebuild/m1-contact-allow.yaml"):
    """Set up a repo whose last green M1 build differs from the current inputs only in `moved`, with the build a gates-only rerun reads on disk (`m1_gates_only_base_present`). The tests that use it each change one of these conditions and read the chosen mode from the plan."""
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(cycle_paths.RUN_M1_GREEN, "green-key", files={moved: "before", "uv.lock": "lock-1"})
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {moved: "after", "uv.lock": "lock-1"})
    monkeypatch.setattr(ac, "m1_gates_only_base_present", lambda: True)


def test_main_reruns_the_gates_when_only_comparison_side_inputs_moved(tmp_path, monkeypatch, capsys):
    """When only the contact allow-list changed, the pass re-runs the gates instead of rebuilding. The tables' stamp does not cover the changed file, so the enumeration's stamp still matches the runes, and `run_m1 --gates-only` re-runs the gates over the tables and font on disk. The rerun rests on the prior green, since a failed run never vouches for the artifacts it left, and the stamp is only a necessary condition for the tables and M1.otf on disk to be the green build's (`run_m1_gates.run_gates_only` names the gaps). The fixture's root has no rebuild/out/m1, so every file the rerun only writes is missing (manual_pins_summary.json and the Stage A record), a superset of what a red rerun leaves missing, and the plan rests only on what the rerun reads (`m1_gates_only_base_present`). No `--kernel-threads` or `--replay-threads` is passed, because this mode enumerates and replays nothing."""
    _comparison_side_drift(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    row = _step_lines(out, "run_m1")
    assert "rebuild/m1-contact-allow.yaml" in row
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
    """On a gates-only rerun the corpus build is skipped only when `m1_stage_a_current` says the Stage A record on disk matches the live sources. A contact allow-list edit is outside every Stage A component, so the record the gates-only pass writes back is the one already on disk and the corpus cannot change. The skip can trust the record because a run writes it only once its gates pass, after deleting the old one before it rewrites anything."""
    _comparison_side_drift(tmp_path, monkeypatch)
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


@pytest.mark.parametrize("record", ["retracted", "outgrown"])
def test_main_rebuilds_m1_and_the_corpus_when_the_stage_a_record_does_not_stand(
    tmp_path, monkeypatch, capsys, record
):
    """The plain run_m1 skip needs a Stage A record that matches the sources on disk, as well as a green whose key matches. A run that wrote under rebuild/out/m1 and then stopped leaves no record (`retracted`, which `m1_artifacts_present` refuses on its own). A run whose gates passed but that left no green, such as a gates-only child whose green the cycle declined, leaves a record for the sources it read, and putting those sources back makes the green match again (`outgrown`). Either way the pass rebuilds M1, which writes a record for these sources, and rebuilds the corpus over it. Skipping both would serve a corpus whose copied record `make verdict-ready` reports stale, which no later pass would rebuild."""
    from rebuild.pipeline import fingerprint

    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(cycle_paths.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(ac, "corpus_build_skippable", lambda root=None: True)
    monkeypatch.setattr(fingerprint, "stage_a", lambda root: {"data": "d", "pipeline_code": "p"})
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True, exist_ok=True)
    for name in [path.name for path in cycle_paths.M1_SUMMARY_FILES.values()] + ["M1.otf"]:
        (m1 / name).write_text("{}")
    if record == "outgrown":
        (m1 / fingerprint.STAGE_A_FILENAME).write_text(
            json.dumps({"format": fingerprint.FORMAT, "data": "edited", "pipeline_code": "p"})
        )
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert _full_build_step(out) is not None
    assert "uv run python -m rebuild.review.build" in _step_lines(out, "corpus-build")


def test_m1_stage_a_current_compares_the_record_against_the_live_sources(tmp_path, monkeypatch):
    from rebuild.pipeline import fingerprint

    out = tmp_path / "rebuild" / "out" / "m1"
    out.mkdir(parents=True)
    monkeypatch.setattr(fingerprint, "stage_a", lambda root: {"data": "d", "pipeline_code": "p"})
    assert ac.m1_stage_a_current(tmp_path) is False
    (out / fingerprint.STAGE_A_FILENAME).write_text(
        json.dumps({"format": fingerprint.FORMAT, "data": "d", "pipeline_code": "p"})
    )
    assert ac.m1_stage_a_current(tmp_path) is True
    monkeypatch.setattr(fingerprint, "stage_a", lambda root: {"data": "moved", "pipeline_code": "p"})
    assert ac.m1_stage_a_current(tmp_path) is False


def test_main_rebuilds_when_run_m1_gates_only_would_refuse_the_build_on_disk(tmp_path, monkeypatch, capsys):
    """A green record alone does not permit a gates-only rerun. It shows that a completed build over every build-side input once passed; only the build on disk shows that none of those inputs has moved since and that the child's check over that build passes (`m1_gates_only_base_present`: the tables' stamp, M1.otf, pipeline_summary.json and the join tables). Without it the pass rebuilds."""
    _comparison_side_drift(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "m1_gates_only_base_present", lambda: False)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "inputs moved since its last green: rebuild/m1-contact-allow.yaml (changed)" in _step_lines(
        out, "run_m1"
    )
    assert _full_build_step(out) is not None
    assert "--gates-only" not in out


def test_main_rebuilds_when_anything_build_side_moved(tmp_path, monkeypatch, capsys):
    """One moved build-side label forces a rebuild, whatever comparison-side labels moved with it, even over a build on disk that `m1_gates_only_base_present` accepts. The last green covers no build over the moved sources, so the artifacts on disk come either from the previous sources or from a build over these that left no green, and a failed run never vouches for the artifacts it left."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="glyph_data/runes/qsX.yaml")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "inputs moved since its last green" in _step_lines(out, "run_m1")
    assert "--gates-only" not in out


@pytest.mark.parametrize("stage_a", ["current", "retracted-by-a-red-rerun", "declined"])
def test_main_plans_a_kern_only_rebuild_only_over_a_stage_a_record_that_stands(
    tmp_path, monkeypatch, capsys, stage_a
):
    """Only a kern line moved since the last green, and the build a gates-only rerun reads is on disk. A kern-only rebuild keeps the Manual-pin summary it finds, so it is planned only when the Stage A record matches the sources, which shows a passing run over these inputs wrote it. After a gates-only rerun that went red at the pin gate (its edit since reverted), the summaries on disk are that red run's and the record is gone; after a gates-only pass whose green and record were declined, the summary may describe other sources. Either way the pass rebuilds, instead of re-evaluating a stale red summary on every later pass until `--fresh`."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="glyph_data/runes/qsNo.yaml#kern")
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: stage_a == "current")
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    if stage_a == "current":
        assert "--kern-only" in _step_lines(out, "run_m1")
        assert _full_build_step(out) is None
    else:
        assert "--kern-only" not in out
        assert _full_build_step(out) is not None


def test_a_kern_only_rebuild_builds_the_corpus_without_asking_whether_it_could_skip(
    tmp_path, monkeypatch, capsys
):
    """A kern-only rebuild rewrites M1.otf and kern.tsv, which the table-diff corpus's after font and kerning units show, so the pass builds the corpus without asking the skip, the assets refresh or the promotion, all of which compare against the font on disk before run_m1 rewrites it. A gates-only rerun, which writes no font, still asks."""
    _comparison_side_drift(tmp_path, monkeypatch, moved="glyph_data/runes/qsNo.yaml#kern")
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: True)
    monkeypatch.setattr(
        ac, "corpus_build_skippable", lambda *a, **k: pytest.fail("asked the corpus skip on a kern-only pass")
    )
    monkeypatch.setattr(ac, "promotable_corpus", lambda *a, **k: pytest.fail("asked for a promotion"))
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--kern-only" in _step_lines(out, "run_m1")
    assert "rebuild.review.build --out" in _step_lines(out, "corpus-build")

    asked: list[bool] = []
    _comparison_side_drift(tmp_path / "gates-only", monkeypatch)
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: True)
    monkeypatch.setattr(ac, "corpus_build_skippable", lambda *a, **k: asked.append(True) or True)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--gates-only" in _step_lines(out, "run_m1") and asked


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

    def conform_spy(queue, make_fut, spawn, emit, registry, argv):
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

    def conform_spy(queue, make_fut, spawn, emit, registry, argv):
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
    assert report.pins_pass
    assert files["pipeline"].exists()
    assert green.read_bytes() == original


def test_do_run_m1_records_green_only_when_fingerprint_stable(monkeypatch, tmp_path):
    """A green is recorded only when the inputs did not change during the build. `run_m1_skip_files` is stubbed along with the fingerprint because the real one reads the live contact allow-list, which the contracts closure exempts on the grounds that no test reads it."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-contact-allow.yaml": "d1"})

    def write_summaries(*a, **k):
        files["pipeline"].write_text(json.dumps({"defect_errors": []}))
        files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
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
    """On a gates-only rerun the pass keeps pipeline_summary.json and clears the Manual-pin summary. `--gates-only` rewrites that summary's defect fields in place and exits without one, so clearing it would break the pass. The Manual-pin summary is the child's own output and is cleared as on a full build, so a child that dies mid-pass cannot leave the last pass's results to be read as this one's. After the spawn the rerun follows the full build's path, including the green record."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-contact-allow.yaml": "d2"})
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda: True)
    ac.record_green(green, "fp-prior", files={"rebuild/m1-contact-allow.yaml": "d1"})
    for path in files.values():
        path.write_text(json.dumps({"stale": True}))
    files["pipeline"].write_text(json.dumps({"defect_errors": [], "gsub_rule_count": 4212}))
    survivors: list[str] = []
    spawned: list[str] = []

    def gates_only(name, argv, **kwargs):
        spawned.append(name)
        survivors.extend(sorted(key for key, path in files.items() if path.exists()))
        files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
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
    assert report.pins_pass
    assert json.loads(files["pipeline"].read_text())["gsub_rule_count"] == 4212
    record = ac.read_green_record(green)
    assert record is not None
    assert record["fingerprint"] == "fp-live"
    assert record["files"] == {"rebuild/m1-contact-allow.yaml": "d2"}


def _kern_only_world(monkeypatch, tmp_path):
    """A rebuild/out/m1 under `tmp_path` holding a passing build's two summaries, a green over the prior kern line, and the live key and files with that kern line moved; the tables' stamp and the Stage A record match. Returns the summary paths and the green record's path."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"glyph_data/runes/qsNo.yaml#kern": "k2"})
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda: True)
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: True)
    ac.record_green(green, "fp-prior", files={"glyph_data/runes/qsNo.yaml#kern": "k1"})
    files["pipeline"].write_text(json.dumps({"defect_errors": [], "gsub_rule_count": 4211}))
    files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 143, "replayed": 143}))
    return files, green


def _run_kern_only_child(spawn):
    report = ac.CycleReport()
    return ac._do_run_m1(
        report,
        spawn=spawn,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--kern-only"],
        kern_only=True,
        record=True,
        fingerprint="fp-live",
    )


def test_do_run_m1_kern_only_deletes_no_summary_and_records_the_green_its_child_earns(monkeypatch, tmp_path):
    """A kern-only child publishes its pipeline_summary.json only once read-back has passed (`run_m1._publish_kern_only`), so the pass deletes no summary before the spawn, and the Manual-pin summary stands. It spawns as `RUN_M1_KERN_ONLY_STEP`, and its green is recorded when only kern lines moved since the prior green."""
    files, green = _kern_only_world(monkeypatch, tmp_path)
    survivors: list[str] = []
    spawned: list[str] = []

    def kern_only(name, argv, **kwargs):
        spawned.append(name)
        survivors.extend(sorted(key for key, path in files.items() if path.exists()))
        files["pipeline"].write_text(json.dumps({"defect_errors": [], "gsub_rule_count": 4212}))
        return _step("run_m1", 0)

    gate = _run_kern_only_child(kern_only)
    assert survivors == ["manual_pins", "pipeline"]
    assert spawned == [ac.RUN_M1_KERN_ONLY_STEP]
    assert gate is not None and gate.ok
    record = ac.read_green_record(green)
    assert record is not None and record["fingerprint"] == "fp-live"


def test_a_failed_kern_only_child_is_red_and_leaves_the_next_pass_a_kern_only_rebuild(monkeypatch, tmp_path):
    """A kern-only child that fails at the kern stage or the read-back publishes nothing, so the prior build's summaries stay beside the prior font. Its nonzero exit is filed red even though those summaries pass, the prior green stands, and the next pass, the one that fixes the kern record, plans a kern-only rebuild again over the same build."""
    files, green = _kern_only_world(monkeypatch, tmp_path)
    before = {name: path.read_bytes() for name, path in files.items()}

    def failing(name, argv, **kwargs):
        return _step("run_m1", 1, stdout="E-KERN-UNMATCHED: qsNo.yaml:kern[0] matches no break\n")

    gate = _run_kern_only_child(failing)
    assert gate is not None and not gate.ok
    assert {name: path.read_bytes() for name, path in files.items()} == before
    record = ac.read_green_record(green)
    assert record is not None and record["fingerprint"] == "fp-prior"
    assert ac.kern_only_rebuild(record, ac.run_m1_skip_files(tmp_path)) == ["glyph_data/runes/qsNo.yaml#kern"]


def test_a_kern_only_child_green_is_not_recorded_over_a_stage_a_record_that_does_not_stand(
    monkeypatch, tmp_path
):
    files, green = _kern_only_world(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: False)

    def kern_only(name, argv, **kwargs):
        files["pipeline"].write_text(json.dumps({"defect_errors": [], "gsub_rule_count": 4212}))
        return _step("run_m1", 0)

    gate = _run_kern_only_child(kern_only)
    assert gate is not None and gate.ok
    record = ac.read_green_record(green)
    assert record is not None and record["fingerprint"] == "fp-prior"


def test_a_kern_only_rebuild_is_planned_only_when_every_moved_label_is_a_kern_line():
    """`kern_only_rebuild` is the kern counterpart of `gates_only_rerun`: it names the moved labels when every one is a rune's kern line, and None when there is no prior record, nothing moved, or anything else moved. A kern line is not comparison-side, since the font must be compiled again."""
    prior = {"files": {"glyph_data/runes/qsNo.yaml#kern": "k1", "glyph_data/runes/qsNo.yaml": "r1"}}
    kern_moved = {"glyph_data/runes/qsNo.yaml#kern": "k2", "glyph_data/runes/qsNo.yaml": "r1"}
    both_moved = {"glyph_data/runes/qsNo.yaml#kern": "k2", "glyph_data/runes/qsNo.yaml": "r2"}
    assert ac.kern_only_rebuild(prior, kern_moved) == ["glyph_data/runes/qsNo.yaml#kern"]
    assert ac.kern_only_rebuild(prior, both_moved) is None
    assert ac.kern_only_rebuild(prior, prior["files"]) is None
    assert ac.kern_only_rebuild(None, kern_moved) is None
    assert not ac.comparison_side_label("glyph_data/runes/qsNo.yaml#kern")
    assert ac.gates_only_rerun(prior, kern_moved) is None


def test_a_kern_only_rebuild_also_recompiles_the_font_over_moved_font_data():
    """A font-data line (`fingerprint.font_data_lines`) reaches the font alone, as a kern line does, so a pass whose moved labels are kern lines and font-data lines plans a kern-only rebuild, and one where a table-side input moved beside them does not."""
    prior = {
        "files": {
            "glyph_data/runes/qsNo.yaml#kern": "k1",
            "glyph_data/latin_letters.yaml#font": "l1",
            "glyph_data/runes/qsNo.yaml": "r1",
        }
    }
    latin_moved = {**prior["files"], "glyph_data/latin_letters.yaml#font": "l2"}
    both_moved = {**latin_moved, "glyph_data/runes/qsNo.yaml#kern": "k2"}
    rune_moved = {**latin_moved, "glyph_data/runes/qsNo.yaml": "r2"}
    assert ac.kern_only_rebuild(prior, latin_moved) == ["glyph_data/latin_letters.yaml#font"]
    assert ac.kern_only_rebuild(prior, both_moved) == [
        "glyph_data/latin_letters.yaml#font",
        "glyph_data/runes/qsNo.yaml#kern",
    ]
    assert ac.kern_only_rebuild(prior, rune_moved) is None
    assert ac.font_data_label("glyph_data/latin_letters.yaml#font")
    assert not ac.comparison_side_label("glyph_data/latin_letters.yaml#font")


def test_a_kern_only_plan_spawns_the_kern_only_child_and_sizes_no_fanout():
    plan = _plan(rerun_kern_only=True, run_m1_note="kern")
    assert plan.rerun_kern_only and not plan.rerun_gates_only
    assert plan.argv("run_m1")[-1] == "--kern-only"
    assert plan.describe("run_m1") == ac.STEP_DESCRIPTIONS[ac.RUN_M1_KERN_ONLY_STEP]
    rendered = "\n".join(ac.render_plan(plan))
    assert "kern-only rebuild" in rendered
    assert not _plan(rerun_kern_only=True, rerun_gates_only=True).rerun_kern_only


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
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-contact-allow.yaml": "d2"})
    monkeypatch.setattr(ac, "m1_tables_stamped", lambda: True)
    key = "fp-unrelated" if prior == "unrelated" else "fp-live"
    ac.record_green(green, key, files={"rebuild/m1-contact-allow.yaml": "d1"})
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
    """The cycle reads the gate that stopped a run only from a full build's own summaries. A stale pipeline_summary.json carrying defect errors, which a skip and a gates-only child keep, is never read as this run's red, so every mode files the missing-summaries reason, and that red clears only a green its key matches."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    files["pipeline"].write_text(json.dumps({"defect_errors": ["E-CONTACT stale"]}))
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    ac.record_green(green, key, files={"rebuild/m1-contact-allow.yaml": "d1"})
    original = green.read_bytes()
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {"rebuild/m1-contact-allow.yaml": "d2"})
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
    prior_files = {"rebuild/m1-contact-allow.yaml": "d1", "glyph_data/runes/qsPea.yaml": "r1"}
    current = {**prior_files, "rebuild/m1-contact-allow.yaml": "d2"}
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
    (corpus / "manifest.json").write_text(json.dumps({"totals": {"units": 5, "rows": 9, "batches": 2}}))
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
    assert (report.corpus_units, report.corpus_rows, report.corpus_batches) == (5, 9, 2)


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


def test_the_summary_says_what_the_pass_wrote_and_when_it_went_over_its_ceiling():
    """One line gives the pass's figure with its basis, the review server's share, the machine-wide device and swap counts, and the write class with its ceiling; a `rewritten whole: <output>` line follows for each output held as a base and segments that the pass rewrote whole outside a fold, the share rule or a letter batch, and a last line a pass over its ceiling. A pass that lost its first or last reading says it was not measured, and a pass without a meter gets no line."""
    own = {"basis": "own", "bytes": 46_300_000, "review_server_bytes": 1_200}
    own |= {"device_bytes": 410_000_000, "swap_bytes": 0}
    assert ac.written_lines(PassWrites(own, "narrowed", None)) == [
        "  written          : 46.3 MB (the pass's own coalition); review server 1.2 KB; machine-wide, the device took 410.0 MB and swap 0 B; class narrowed, no ceiling yet"
    ]
    shared = {"basis": "shared", "bytes": 9_000_000_000, "outsiders_bytes": 2_100_000, "outsiders_unread": 4}
    shared |= {"review_server_bytes": None, "device_bytes": None, "swap_bytes": None}
    assert ac.written_lines(PassWrites(shared, None, None)) == [
        "  written          : 9.0 GB at most (the shared coalition, less the 2.1 MB other processes wrote, with 4 processes' writes unread); class unclassified, no ceiling"
    ]
    assert ac.written_lines(PassWrites(shared, "full-replay", 5_000_000_000)) == [
        "  written          : 9.0 GB at most (the shared coalition, less the 2.1 MB other processes wrote, with 4 processes' writes unread); class full-replay, ceiling 5.0 GB",
        "  write ceiling    : OVER, 9.0 GB against the full-replay class's 5.0 GB (make cycle-timings ARGS='--writes')",
    ]
    rewritten = {**own, "outputs": {"files": 1, "bytes": 9, "changed_bytes": 9, "kept_bytes": 0}}
    rewritten["outputs"]["rewritten"] = ["settlement-default.tsv"]
    assert ac.written_lines(PassWrites(rewritten, "narrowed", None))[1:] == [
        "  rewritten whole: settlement-default.tsv"
    ]
    assert ac.written_lines(PassWrites(None, "nothing", None)) == [
        "  written          : not measured (the write meter's first or last reading failed; see the [warn] line)"
    ]
    assert ac.written_lines(None) == []


def test_the_review_server_is_named_only_when_one_process_listens_on_its_port(monkeypatch):
    """The write meter reports the named pid's writes apart, so a pid is named only when `lsof` finds exactly one listener on the review port."""
    replies = iter(["4242\n", "4242\n4243\n", ""])
    monkeypatch.setattr(
        ac.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout=next(replies))
    )
    assert [ac.review_server_pid() for _ in range(3)] == [4242, None, None]


def test_a_step_that_came_back_nonzero_never_reads_as_an_ok_row():
    """The outcome column comes from each step's result (its exit status, or for run_m1 its gate result), not from whether the step took any time. Filled from seconds, it would read `ok` for every step that ran, including a run_m1 whose Manual pins failed and a corpus build whose child died. The informational job-costs step is the exception: it gates nothing and reports its failure in its own detail."""
    plan = _plan()
    report = ac.CycleReport()
    report.pins_pass = False
    report.run_m1_failed = True
    report.job_costs_status = "OVERRUN (a measured peak outruns its checked-in constant)"
    report.step_seconds = {"run_m1": 9.0, "corpus-build": 3.0, "job-costs": 1.0}
    report.step_returncodes = {"run_m1": 0, "corpus-build": 1, "job-costs": 1}

    rows = {row.name: row for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert rows["run_m1"].outcome == "FAILED"
    assert rows["run_m1"].detail == "PINS FAILED"
    assert rows["corpus-build"].outcome == "FAILED"
    assert rows["job-costs"].outcome == "ok"


def test_every_spawned_step_closes_with_its_own_detail_and_peak(capsys, tmp_path):
    """Each spawned step's closing line carries the detail its summary row will show and its peak memory. No stage knows its detail when its child exits (run_m1 reads two summaries, the corpus build opens a manifest), so the closing line is written by the stage that reads them."""
    corpus = _built_corpus(tmp_path, units=15903, rows=81894, batches=16)

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
    assert any(line.endswith("ok  pins pass  rss 19.6G") for line in closing), closing
    assert any("ok  15,903 units, 81,894 rows  rss 1.0G" in line for line in closing), closing


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
    tripped = "CONFORM_SWEEP_UNIT_BYTES at 0.90 GB (max 0.66 GB × 1.25, rounded up to a multiple of 0.10 GB); width here at 0.90 GB: 8 at 0.90 GB each"
    used = "DELTA_PEAK_BYTES at 1.00 GB (max 0.76 GB × 1.25, rounded up to a multiple of 0.10 GB), headroom used, not yet an overrun; width here at 1.00 GB: 43 at 1.00 GB each"

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
    assert "DELTA_PEAK_BYTES at 1.00 GB" not in report.job_costs_status
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
    """The job-costs check runs in a staging pass too: every pass appends to the timings journal, and a staging pass's pool measurements are as valid as any."""
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
    """The check reads a journal that this pass's pools append to when they finish, so it runs after the gates join. Placed beside the land, it would report the previous pass's measurements."""
    plan = _plan()
    names = [step.name for step in plan.steps]
    by_name = {step.name: step for step in plan.steps}
    assert names.index("job-costs") > names.index("gate:rebuild-contracts")
    assert names.index("job-costs") > names.index("land")
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
    """The step is never skipped: --skip-gates suppresses only the `gate:` steps, and this step is not one of them."""
    assert _plan(skip_gates=True).runs("job-costs") is True


def test_the_contracts_suite_is_submitted_before_the_corpus_build_starts(monkeypatch):
    """The contracts suite is submitted after the run_m1 gate passes and before the corpus build starts, so it runs beside the build. The corpus fake waits until the suite's task has been invoked, which a submission after the build could never satisfy. The suite waits for nothing else, because the corpus and the land are not inputs to it; `test_the_rebuild_suite_is_skipped_when_run_m1_fails` checks the other bound."""
    contracts_invoked = threading.Event()
    order: list[str] = []

    def fake_run_m1(report, *, spawn, emit, registry, **_):
        assert not contracts_invoked.is_set()
        order.append("run_m1")
        return _run_m1_green()

    def fake_contracts(
        queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
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
        queue, conform_fut, make_fut, spawn, emit, registry, argv, force=False, _record=False, *_
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


def _settled_repo(tmp_path, monkeypatch):
    """A repo whose run_m1 and corpus build both auto-skip: the converged pass."""
    _unsettled_repo(tmp_path, monkeypatch)
    ac.record_green(cycle_paths.RUN_M1_GREEN, "key")
    monkeypatch.setattr(ac, "m1_artifacts_present", lambda root=None: True)
    monkeypatch.setattr(ac, "m1_stage_a_current", lambda root=None: True)
    monkeypatch.setattr(ac, "corpus_build_skippable", lambda root=None: True)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=None: "no-match")


def _assets_only_repo(tmp_path, monkeypatch):
    """A settled repo whose only moved input is the copied review UI assets: the byte-identity check fails and the check that exempts the assets passes, which is the condition for the refresh step."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ac, "corpus_build_skippable", lambda root=None, review_out=None, ignore=(): bool(ignore)
    )


def test_main_refreshes_the_assets_when_only_the_static_component_moved(tmp_path, monkeypatch, capsys):
    """An app JS/CSS/HTML edit plans a copy and a restamp, not a corpus build, and lands nothing, since no shard or stamp moves."""
    _assets_only_repo(tmp_path, monkeypatch)
    assert ac.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED (only the review UI assets moved" in _step_lines(out, "corpus-build")
    assert "uv run python -m rebuild.review.build refresh-assets" in _step_lines(out, "assets-refresh")
    assert _step_lines(out, "land") == ""


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
    """End to end through `main`: the pass that skips the corpus build lands nothing, so it keeps the review server running and never stops it."""
    _settled_repo(tmp_path, monkeypatch)
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
    out = capsys.readouterr().out
    assert "Stopping the review server" in out and "the last stop a pass needs" in out


def test_main_keeps_the_review_server_running_for_an_assets_refresh_pass(tmp_path, monkeypatch, capsys):
    """An assets refresh moves no shard and no stamp, so the review server keeps running and the cycle tells the tab to reload onto the new app shell."""
    _assets_only_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: True)
    monkeypatch.setattr(
        ac, "stop_review_server", lambda timeout=ac.SERVER_STOP_TIMEOUT: pytest.fail("stopped")
    )
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: 0)
    assert ac.main([]) == 0
    assert ac.SERVER_KEEPS_RUNNING_NOTE in capsys.readouterr().out


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
    """With a server from this checkout on the tree's code, a corpus-changing pass never stops it, even without --stop-server: it builds the table-diff corpus in review.next and lands it, carrying the store onto it. The server's tabs are sent the reload."""
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
    assert names[: names.index("land") + 1] == ["run_m1", "corpus-build", "land"]
    build = plan.argv("corpus-build")
    assert build[build.index("--out") + 1] == str(ac.next_corpus_dir())
    land = plan.argv("land")
    assert "--prepared" not in land
    assert land[land.index("--staged") + 1] == str(ac.next_corpus_dir())
    assert land[land.index("--live") + 1] == str(ac.REVIEW_OUT)
    assert land[land.index("--autosave") + 1] == str(ac.AUTOSAVE)


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


def test_only_a_pass_that_moves_the_corpus_lands():
    """A corpus-changing pass lands its corpus with the store carried onto it, so the store and the corpus stay aligned, and a promotion lands the staged corpus; the land is handed no prepared store. A pass that does not move the corpus lands nothing, a first run builds in place and lands nothing, and a staging pass never lands."""
    built = _plan()
    land = _argv({step.name: step for step in built.steps}["land"])
    assert "--prepared" not in land
    assert land[land.index("--staged") + 1] == str(ac.next_corpus_dir())

    promoted = _plan(skip_corpus=True, promote_corpus=Path("var/staged-review"), corpus_note="staged")
    land = _argv({step.name: step for step in promoted.steps}["land"])
    assert land[land.index("--staged") + 1] == "var/staged-review"

    settled = _plan(skip_corpus=True, corpus_note="same")
    assert not settled.land
    assert "land" not in {step.name for step in settled.steps}

    first = _plan(first_run=True)
    assert not first.land
    assert "--out" not in _argv({step.name: step for step in first.steps}["corpus-build"])


def test_the_planned_land_carries_the_store_by_unit_id_and_keeps_a_tombstone_of_any_age():
    """The land the cycle plans, run with its own arguments, carries the live store onto the new corpus by unit id: a verdict on a unit the new corpus still has stays, one the new corpus lacks goes to the orphan document of the old stamp, and a clear has no age limit, so a tombstone decades older than the pass on one of the landed corpus's human units stays, and an older verdicts file cannot bring the cleared verdict back."""
    plan = _plan()
    argv = plan.argv("land")
    argv = argv[argv.index("rebuild.review.landing") + 1 :]
    for corpus_dir, corpus_stamp in ((ac.REVIEW_OUT, "A"), (ac.next_corpus_dir(), "B")):
        corpus_dir.mkdir(parents=True)
        (corpus_dir / "manifest.json").write_text(
            json.dumps({"generated_at": corpus_stamp, "human_unit_ids": ["u-1", "u-2"]})
        )
    cleared = [{"unit": "u-2", "at": "2001-01-01T00:00:00Z"}]
    ac.AUTOSAVE.write_text(json.dumps({**_verdicts_doc("A", ["u-1", "u-3"]), "cleared": cleared}))
    assert ac.landing.main(argv) == 0
    landed = json.loads(ac.AUTOSAVE.read_text())
    assert landed["manifest_generated_at"] == "B"
    assert [record["unit"] for record in landed["verdicts"]] == ["u-1"]
    assert landed["cleared"] == cleared
    orphans = json.loads((ac.AUTOSAVE.parent / "var" / "verdict-orphans" / "A.json").read_text())
    assert [(entry["unit"], entry["reason"]) for entry in orphans["verdicts"]] == [("u-3", "orphan")]


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


def test_a_promoting_pass_stops_a_review_server_from_before_the_land_protocol(tmp_path, monkeypatch, capsys):
    """A promotion swaps every shard and the stamp under the app, so for a server from before the land protocol it counts as a corpus write whatever the skip flag says. The predicate returns False for it, and its other three answers are unchanged. End to end, such a server makes the pass refuse without --stop-server and is stopped with it; a server that speaks the land protocol keeps running (`test_main_keeps_a_current_server_running_through_a_rebuild`)."""
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
    assert stops == []
    assert ac.main(["--stop-server"]) == 0
    assert stops == [1]
    assert "Stopping the review server" in capsys.readouterr().out


def test_build_plan_retention_default_on():
    plan = _plan()
    assert plan.retention is True
    by_name = {step.name: step for step in plan.steps}
    note = by_name["retention"].note
    assert "green finish" in note
    assert str(cycle_paths.BUILD_LOGS_KEEP) in note


def test_build_plan_retention_skipped_with_keep_history():
    plan = _plan(keep_history=True)
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert by_name["retention"].note == "SKIPPED (--keep-history)"


def test_build_plan_retention_off_on_first_run():
    plan = _plan(first_run=True)
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert "first run" in by_name["retention"].note


def test_build_plan_retention_off_on_a_staging_pass(tmp_path):
    plan = _plan(review_out=tmp_path / "staged")
    assert plan.retention is False
    by_name = {step.name: step for step in plan.steps}
    assert "staging" in by_name["retention"].note


def test_retention_never_runs_for_real_during_the_suite(monkeypatch):
    """Checks the autouse switches that keep retention and readiness from running for real in the suite. Retention resolves its targets from ac.ROOT at call time, which no fixture redirects, so a real run from a test would prune the live repo's build logs; any test reaching a green finish with record_greens set would trigger it. The readiness checklist reads the served corpus and the root autosave, so it is switched off too."""
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
        return ["READY - adjudicate at the review queue"], None

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


def test_retention_prunes_the_build_logs_beyond_the_newest_runs(tmp_path, monkeypatch):
    """Retention prunes the build logs only, keeping the newest `cycle_paths.BUILD_LOGS_KEEP` runs, and reports how many it removed."""
    plan = _plan()
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    logs = tmp_path / "var" / "build-logs"
    monkeypatch.setattr(cycle_paths, "BUILD_LOGS_ROOT", logs)
    monkeypatch.setattr(cycle_paths, "BUILD_LOGS_KEEP", 2)
    for name in ("20200101T000000Z-a", "20200102T000000Z-b", "20200103T000000Z-c"):
        (logs / name).mkdir(parents=True)
    swept = ac.run_retention(plan)
    assert swept.lines == [
        "Retention (skip with --keep-history):",
        "  build logs: removed 1; kept the last 2 runs under var/build-logs",
    ]
    assert swept.detail == "removed 1 build log"
    assert sorted(path.name for path in logs.iterdir()) == ["20200102T000000Z-b", "20200103T000000Z-c"]


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

    for name, uninterruptible, held_after in (("land", True, True), ("corpus-build", False, False)):
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
    """A live pass holds the pass lock from before its plan, sweeps the scratch directories and the counts file a killed pass left, and deletes its own when it ends. A dry run holds the lock too, and gets no scratch directory."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    leftover = cycle_paths.CYCLE_VAR / "20200101T000000Z-dead"
    leftover.mkdir(parents=True)
    leftover_counts = ct.CycleTimings(tmp_path / "dead-timings.ndjson").open_output_counts(
        cycle_paths.CYCLE_VAR
    )
    leftover_counts.write_text("kept\t4\n")
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
        assert not leftover.exists() and not leftover_counts.exists()
        assert Path(os.environ[ct.OUTPUT_COUNTS_ENV]).read_bytes() == b""
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
    report.pins_pass = True
    return _run_m1_green()


def _spawning_corpus(report, *, spawn, emit, registry, review_out, **_):
    spawn("corpus", ["uv", "run", "fake-corpus"], emit=emit, registry=registry, stream=False)
    report.corpus_units = 1
    return True


def _patch_timing_cycle(monkeypatch):
    monkeypatch.setattr(ac, "_do_run_m1", _spawning_run_m1)
    monkeypatch.setattr(ac, "_do_corpus_build", _spawning_corpus)
    monkeypatch.setattr(ac, "_gate_js_task", _js_ok)
    monkeypatch.setattr(ac, "_gate_make_test_task", _make_ok)
    monkeypatch.setattr(ac, "_gate_contracts_task", _contracts_green)
    monkeypatch.setattr(ac, "_gate_conform_task", _conform_green)


def _still_meter(device):
    """Return a write-meter reading of a coalition whose counters never move, for a cycle test that checks where the figures go rather than what they are."""
    return write_meter.WriteReading(
        pid=1,
        at=0.0,
        wall=0.0,
        coalition=7,
        usage=dict.fromkeys(write_meter._COALITION_FIELDS, 0) | {"tasks_started": 1},
        members=(write_meter.Member(1, 0, 0.0, 0),),
        check=None,
        named=None,
        device=0 if device else None,
        swap=0 if device else None,
    )


def test_green_cycle_journals_steps_then_one_run_line(monkeypatch, tmp_path):
    """The journal gets one step line per spawned child, then one run line. job-costs spawns a child, so the timing wrapper records it, and a summary claiming the check ran can be matched against the journal. With a write meter, each step line carries its window and the run line the pass's `written` block, and the report keeps both for the summary."""
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
        timings=CycleTimings(journal_path, meter=_still_meter),
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
    assert [(entry["written_bytes"], entry["written_overlap"]) for entry in steps] == [(0, [])] * 3
    assert (runs[0]["written"]["basis"], runs[0]["written"]["bytes"]) == ("own", 0)
    assert report.step_written["run_m1"] == (0, False)
    assert report.written is not None and report.written.block == runs[0]["written"]


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
    """Record every path the cycle sends to the review server, and set the suite's reload switch."""
    sent: list[str] = []

    def record(path):
        sent.append(path)
        return True

    monkeypatch.setattr(ac, "force_reload", record)
    monkeypatch.setattr(cycle_paths, "REVIEW_RELOAD_ENABLED", enabled)
    return sent


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

    rc = ac._run_cycle(
        _assets_refresh_plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )

    assert rc == 0
    assert sent == ["ams:assets/static-new"]


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

    ac._run_cycle(
        _assets_refresh_plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )

    assert sent == []


def _swapping_land(sent: list[str], source: Path):
    """Return a stubbed land that swaps `source` in for the served corpus, as the real land's exchange does, and records that it ran in `sent`."""

    def land(report, *, spawn, emit, registry, plan):
        sent.append("land")
        shutil.rmtree(ac.REVIEW_OUT)
        os.replace(source, ac.REVIEW_OUT)
        return _land_ok(report, spawn=spawn, emit=emit, registry=registry, plan=plan)

    return land


def test_a_promotion_sends_the_promoted_stamp_after_the_land(monkeypatch, tmp_path):
    """A listening server is sent `ams:corpus/<generated_at>` of the promoted corpus once the land has swapped it in with the store, so its tabs move onto a corpus whose store is already on it."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    live = tmp_path / "rebuild" / "out" / "review"
    live.mkdir(parents=True)
    (live / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T10:00:00Z"}))
    source = tmp_path / "var" / "staged-review"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T11:00:00Z"}))
    monkeypatch.setattr(ac, "REVIEW_OUT", live)
    monkeypatch.setattr(ac, "_do_land", _swapping_land(sent, source))

    plan = _plan(skip_corpus=True, promote_corpus=source, corpus_note=ac.CORPUS_PROMOTE_NOTE)
    rc = ac._run_cycle(
        plan,
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )

    assert rc == 0
    assert sent == ["land", "ams:corpus/2026-09-30T11:00:00Z"]


def test_a_built_corpus_is_announced_after_the_land_and_never_to_a_foreign_server(monkeypatch, tmp_path):
    """A pass that builds beside the served corpus sends the new stamp once its land has swapped the build in. A server that serves another checkout (`plan.broadcast` off) is sent nothing."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
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
        assert sent == ["land", *expected]


def test_a_stop_during_the_land_still_moves_the_tabs_and_reports_the_land(monkeypatch, tmp_path):
    """A Ctrl-C that arrives while the land runs does not stop it, and the pass waits for it. Once it has landed, the interrupted pass still tells the open tabs to move onto the new corpus, which no later pass would do because the served stamp is already the new one, deletes the tree the land swapped out, and reports the land as run."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    ac.REVIEW_OUT.mkdir(parents=True)
    (ac.REVIEW_OUT / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T10:00:00Z"}))
    built = ac.next_corpus_dir()
    discard = tmp_path / "review.discard"

    def build(report, **kwargs):
        built.mkdir(parents=True, exist_ok=True)
        (built / "manifest.json").write_text(json.dumps({"generated_at": "2026-09-30T11:00:00Z"}))
        return _spawning_corpus(report, **kwargs)

    monkeypatch.setattr(ac, "_do_corpus_build", build)

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
    assert sent == ["ams:corpus/2026-09-30T11:00:00Z"]
    assert not discard.exists()
    assert report.land_status.startswith("landed 2026-09-30T11:00:00Z, the store's 1 verdicts")
    outcomes = {row.name: row.outcome for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert outcomes["land"] == "ok"


def test_a_promoting_pass_reads_the_staged_corpus_and_lands_it(monkeypatch, tmp_path):
    """A promotion replaces the corpus build: nothing spawns under corpus-build, the reported totals come from the staged manifest, and the land is what moves it in. No corpus-seed or corpus-promote row is planned."""
    monkeypatch.setattr(ac, "_do_run_m1", _pass_run_m1)
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

    monkeypatch.setattr(ac, "_do_land", _swapping_land(order, source))
    plan = _plan(skip_corpus=True, promote_corpus=source, corpus_note=ac.CORPUS_PROMOTE_NOTE)
    names = [step.name for step in plan.steps]
    assert "corpus-seed" not in names and "corpus-promote" not in names
    assert plan.next_corpus == source
    report = ac.CycleReport()
    rc = ac._run_cycle(plan, report, ac._Emitter(), ac._ChildRegistry(), spawn=spawn)

    assert rc == 0
    assert "corpus-build" not in order
    assert "land" in order
    assert report.corpus_units == 7 and report.corpus_rows == 9
    assert json.loads((live / "manifest.json").read_text())["totals"]["units"] == 7
    outcomes = {row.name: row.outcome for row in ac.summary_rows(report, plan, retention_ran=False)}
    assert outcomes["corpus-build"] == "skipped"


def test_a_failed_land_fails_the_pass_and_joins_the_suite_it_started(monkeypatch, capsys):
    """A land that did not complete fails the pass: its row reads FAILED, and the rebuild suite submitted before it is joined and reports its real result."""
    _patch_timing_cycle(monkeypatch)

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
    assert "land failed" in capsys.readouterr().out


def test_a_failed_build_never_lands(monkeypatch):
    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_corpus_build", lambda report, **kwargs: False)
    monkeypatch.setattr(ac, "_do_land", lambda report, **kwargs: pytest.fail("landed after a failed build"))
    rc = ac._run_cycle(
        _plan(),
        ac.CycleReport(),
        ac._Emitter(),
        ac._ChildRegistry(),
        spawn=lambda name, argv, **k: _step(name),
    )
    assert rc == 1


def test_a_rebuilding_pass_lands_its_corpus_and_carries_the_store_with_the_saves_made_during_it(
    monkeypatch, tmp_path
):
    """The land runs for real around a stubbed build. The build writes only beside the served corpus, into an emptied `review.next`, and the land reads the live store under its lock, so a save the server applied while the pass ran is among what it carries onto the new corpus by unit id. The land swaps the corpus in, keeps the verdict whose unit the new corpus still has, sends the one whose unit it lacks to the orphan document, deletes the outgoing tree, and the tabs are told the new stamp once. The new corpus drops one of the store's two units, half its verdicts, and the land goes ahead, since no pass refuses over an orphan."""
    _patch_timing_cycle(monkeypatch)
    sent = _record_reloads(monkeypatch)
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    monkeypatch.setattr(ac, "JSTEST_DIR", tmp_path / "rebuild" / "review" / "jstests")
    live = tmp_path / "rebuild" / "out" / "review"
    monkeypatch.setattr(ac, "REVIEW_OUT", live)
    monkeypatch.setattr(ac, "AUTOSAVE", tmp_path / "verdicts-autosave.json")
    live.mkdir(parents=True)
    (live / "manifest.json").write_text(json.dumps({"generated_at": "A", "human_unit_ids": ["u-1", "u-2"]}))
    (live / "units-000.json").write_text("old")
    ac.AUTOSAVE.write_text(json.dumps(_verdicts_doc("A", ["u-1", "u-2"])))
    next_dir = ac.next_corpus_dir()
    next_dir.mkdir()
    (next_dir / "stale.txt").write_text("left by an earlier pass")

    def build(report, *, spawn, emit, registry, review_out, **kwargs):
        assert review_out == next_dir
        assert not review_out.exists()
        review_out.mkdir()
        (review_out / "units-000.json").write_text("new")
        (review_out / "manifest.json").write_text(
            json.dumps({"generated_at": "B", "human_unit_ids": ["u-1", "u-3"]})
        )
        return True

    def land(report, *, spawn, emit, registry, plan):
        live_store = json.loads(ac.AUTOSAVE.read_text())
        live_store["verdicts"][0] = {
            "unit": "u-1",
            "verdict": "reject",
            "note": "late",
            "at": "2026-07-18T00:00:00Z",
        }
        _replace_store(ac.AUTOSAVE, live_store)
        return REAL_DO_LAND(report, spawn=spawn, emit=emit, registry=registry, plan=plan)

    def spawn(name, argv, **kwargs):
        if name == "land":
            return _step(name, ac.landing.main(argv[argv.index("rebuild.review.landing") + 1 :]))
        return _step(name)

    monkeypatch.setattr(ac, "_do_corpus_build", build)
    monkeypatch.setattr(ac, "_do_land", land)
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
    orphans = json.loads((tmp_path / "var" / "verdict-orphans" / "A.json").read_text())
    assert [(record["unit"], record["reason"]) for record in orphans["verdicts"]] == [("u-2", "orphan")]
    assert report.land is not None and report.land["carried"] and report.land["orphaned"] == 1
    assert sorted(path.name for path in tmp_path.glob("verdicts-*.json")) == ["verdicts-autosave.json"]
    assert sent == ["ams:corpus/B"]


def test_the_cycle_takes_no_carry_flags(tmp_path, monkeypatch, capsys):
    """The land carries the store onto every corpus it lands by unit id, and a revert or a second edit orphans every verdict on the units it changes, which the orphan documents keep, so no pass refuses over them: the cycle's command line takes no flag that names a verdicts master or a form of carry."""
    _unsettled_repo(tmp_path, monkeypatch)
    _capable_server(monkeypatch)
    ran: list[ac.Plan] = []
    monkeypatch.setattr(ac, "_run_cycle", lambda plan, report, emit, registry, **_: ran.append(plan) or 0)
    assert ac.main([]) == 0
    (plan,) = ran
    assert "--allow-orphans" not in plan.argv("land")
    for flag in ("--allow-orphans", "--verdicts=v.json", "--no-carry", "--no-merge", "--carry-out=c.json"):
        with pytest.raises(SystemExit):
            ac.main([flag])
        assert "unrecognized arguments" in capsys.readouterr().err


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
    assert report.pins_pass


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


@pytest.mark.parametrize("stopped_at", ["defects", "pins", "after-the-pins", "killed-mid-write"])
def test_do_run_m1_names_the_gate_that_stopped_a_full_build(monkeypatch, tmp_path, capsys, stopped_at):
    """A full build exits at the defect gate before the Manual-pin gate writes its summary, so the summary it wrote names the gate that failed, and the cycle files the red under that gate's failure rather than as a run that did not complete; a red pin gate is read from the two summaries it leaves. Every child first rewrites a passing string replay record, as a real build whose replay returned before it stopped does. A build that stopped after a passing pin gate and a passing replay leaves both summaries passing, so the cycle files its nonzero exit, and one killed while it wrote a summary leaves one that does not parse and keeps the missing-summaries reason; neither child prints a `[check]` line here."""
    from rebuild.pipeline import run_m1

    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    ac.record_green(green, "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: pytest.fail("red recomputed inputs"))
    timings = CycleTimings(tmp_path / "timings.ndjson")

    def child(name, argv, **kwargs):
        (tmp_path / run_m1.REPLAY_SUMMARY).write_text(json.dumps({"pass": True, "error": None}))
        if stopped_at == "killed-mid-write":
            files["pipeline"].write_text('{"defect_errors": ["E-CONT')
            return _step(name, -9)
        defect_errors = ["E-CONTACT qsAh~qsBay: ink collision"] if stopped_at == "defects" else []
        files["pipeline"].write_text(json.dumps({"defect_errors": defect_errors}))
        if stopped_at != "defects":
            failed = stopped_at == "pins"
            files["manual_pins"].write_text(
                json.dumps(
                    {
                        "pass": not failed,
                        "disagreements": ["x"] if failed else [],
                        "pins_in_scope": 143,
                        "replayed": 143,
                    }
                )
            )
        return _step(name, 1)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
        record=True,
        fingerprint="fp-live",
        timings=timings,
    )

    assert not green.exists()
    (line,) = ct.load_checks(timings.path)
    out = capsys.readouterr().out
    if stopped_at == "killed-mid-write":
        assert gate is None
        assert (line["outcome"], line["status"]) == ("red", "FAILED (no summaries)")
        assert line["failures"] == ac._run_m1_reasons(None)
        assert "did not complete" in out
        return
    expected = {
        "defects": ["1 defect-gate error(s): E-CONTACT qsAh~qsBay: ink collision"],
        "pins": ["Manual-pin gate failed (1 disagreements)"],
        "after-the-pins": ["run_m1 gate: exited 1 despite passing summaries"],
    }[stopped_at]
    assert gate is not None and not gate.ok
    assert gate.failures == expected == ac._run_m1_reasons(gate)
    assert (line["check"], line["outcome"], line["status"], line["failures"]) == (
        "run_m1",
        "red",
        "FAILED (exit 1)" if stopped_at == "after-the-pins" else "FAILED",
        expected,
    )
    assert "did not complete" not in out


@pytest.mark.parametrize("replay", ["failed", "passed", "raised", "truncated"])
def test_do_run_m1_files_a_failed_string_replay_as_the_child_reports_it(monkeypatch, tmp_path, replay):
    """A string replay that found the tables incomplete fails a full build at the join after the Manual-pin gate, and the child reports it in place of the defect gate's failure, so the cycle files the red under the child's message from the replay's record, not under the defect gate, while a record whose walk passed leaves the defect gate the summaries name. A replay that returns always rewrites its record, so a record left unmoved means the replay raised or the child was killed before it returned, and one a failed or interrupted write left empty does not parse. Neither says what the child reported, and this child prints no `[check]` line, as one killed before it reported prints none, so the cycle keeps the missing-summaries reason rather than blame the defect gate."""
    from rebuild.pipeline import run_m1

    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    record = tmp_path / run_m1.REPLAY_SUMMARY
    record.write_text(json.dumps({"pass": False, "error": "an earlier build's disagreement"}))
    os.utime(record, ns=(0, 0))
    timings = CycleTimings(tmp_path / "timings.ndjson")

    def child(name, argv, **kwargs):
        files["pipeline"].write_text(json.dumps({"defect_errors": ["E-CONTACT qsAh~qsBay: ink collision"]}))
        if replay == "truncated":
            record.write_text("")
        elif replay != "raised":
            failed = replay == "failed"
            record.write_text(
                json.dumps({"pass": not failed, "error": "default: qsPea qsTea" if failed else None})
            )
        return _step(name, 1)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
        timings=timings,
    )

    defect = ["1 defect-gate error(s): E-CONTACT qsAh~qsBay: ink collision"]
    status, expected = {
        "failed": ("FAILED", ["the string replay found the tables incomplete: default: qsPea qsTea"]),
        "passed": ("FAILED", defect),
        "raised": ("FAILED (no summaries)", list(ac._NO_SUMMARIES_REASONS)),
        "truncated": ("FAILED (no summaries)", list(ac._NO_SUMMARIES_REASONS)),
    }[replay]
    assert (gate is None) == (replay in ("raised", "truncated"))
    assert ac._run_m1_reasons(gate) == expected
    (line,) = ct.load_checks(timings.path)
    assert (line["outcome"], line["status"], line["failures"]) == ("red", status, expected)


@pytest.mark.parametrize("stopped_at", ["defects", "pins", "refused", "killed"])
def test_do_run_m1_files_the_red_a_gates_only_child_reported(monkeypatch, tmp_path, capsys, stopped_at):
    """A gates-only child keeps the build's pipeline_summary.json, whose defect fields stay stale until its defect gate rewrites them, so the cycle files its stop from the red the child printed on its `[check]` line (`_reported_red`), never from that file. A child that went red at its defect gate or its Manual-pin gate is filed under that gate with the failure a full build is filed under, and one refused before either gate under its refusal, though the stale summary beside it names a defect. A child killed before it reported keeps the missing-summaries reason."""
    from rebuild.pipeline import run_m1_gates

    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    files["pipeline"].write_text(
        json.dumps({"defect_errors": ["E-CONTACT qsAh~qsBay: an earlier build's collision"]})
    )
    timings = CycleTimings(tmp_path / "timings.ndjson")
    status, failure = {
        "defects": ("FAILED", "1 defect-gate error(s): E-CONTACT qsAh~qsBay: ink collision"),
        "pins": ("FAILED", "Manual-pin gate failed (1 disagreements)"),
        "refused": (ct.REFUSED_STATUS, "the stamped window enumerations are missing"),
        "killed": ("FAILED (no summaries)", ac._NO_SUMMARIES_REASONS[0]),
    }[stopped_at]

    def child(name, argv, **kwargs):
        assert name == ac.RUN_M1_GATES_ONLY_STEP
        if stopped_at == "killed":
            return _step(name, -9)
        if stopped_at == "defects":
            files["pipeline"].write_text(
                json.dumps({"defect_errors": ["E-CONTACT qsAh~qsBay: ink collision"]})
            )
        elif stopped_at == "pins":
            files["pipeline"].write_text(json.dumps({"defect_errors": []}))
            files["manual_pins"].write_text(
                json.dumps({"pass": False, "disagreements": ["x"], "pins_in_scope": 143, "replayed": 143})
            )
        monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
        run_m1_gates._record_cli_check(run_m1_gates._failed_check("run_m1", failure, status), 0.0)
        return _step(name, 1, stdout=capsys.readouterr().out)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--gates-only"],
        gates_only=True,
        timings=timings,
    )

    assert (gate is None) == (stopped_at == "killed")
    assert ac._run_m1_reasons(gate) == [failure]
    (line,) = ct.load_checks(timings.path)
    assert (line["outcome"], line["status"], line["failures"]) == ("red", status, [failure])
    assert ("did not complete" in capsys.readouterr().out) == (stopped_at == "killed")


@pytest.mark.parametrize("skip", [True, False])
def test_do_run_m1_files_an_unreadable_summary_as_a_red_that_names_it(monkeypatch, tmp_path, skip):
    """When every summary is present, one cut short, as a full build killed while it wrote the last of them or a full disk leaves it, is filed red under the file's name, on the skip path as after a spawn, and clears the green over that content. Without that the driver would stop on the parse before it records the check line or settles the green."""
    files = {name: tmp_path / f"{name}.json" for name in cycle_paths.M1_SUMMARY_FILES}
    monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
    green = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", green)
    ac.record_green(green, "fp-live")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: pytest.fail("red recomputed inputs"))
    timings = CycleTimings(tmp_path / "timings.ndjson")

    def leave_summaries():
        _write_pass_summaries(files)
        files["manual_pins"].write_text('{"pass": true, "pins_in')

    leave_summaries()

    def child(name, argv, **kwargs):
        assert not skip
        leave_summaries()
        return _step(name, -9)

    gate = ac._do_run_m1(
        ac.CycleReport(),
        spawn=child,
        emit=ac._Emitter(),
        registry=ac._ChildRegistry(),
        argv=["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
        skip=skip,
        skip_note="test skip",
        record=True,
        fingerprint="fp-live",
        timings=timings,
    )

    expected = [f"run_m1 gate: {files['manual_pins']} is unreadable or not a JSON object"]
    assert gate is not None and gate.failures == expected == ac._run_m1_reasons(gate)
    (line,) = ct.load_checks(timings.path)
    assert (line["outcome"], line["status"], line["failures"]) == (
        "red",
        "FAILED (unreadable summary)",
        expected,
    )
    assert not green.exists()


@pytest.mark.parametrize("left", ['{"divergences": 0, "pass": tr', "[]"])
def test_gate_conform_files_an_unreadable_summary_as_a_red_that_names_it(monkeypatch, tmp_path, left):
    """A gate:conform child that left a conform_summary.json cut short, or one that is not an object, and printed no `[check]` line, as one killed while it wrote the file does, is filed red under the file's name, not as a sweep that wrote no summary."""
    summary = tmp_path / "conform_summary.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_SUMMARY", summary)

    def child(name, argv, **kwargs):
        summary.write_text(left)
        return _step(name, -9)

    gate = ac._gate_conform_task(False, None, child, ac._Emitter(), ac._ChildRegistry(), [])

    assert (gate.outcome, gate.status, gate.failures) == (
        "red",
        "FAILED (unreadable summary)",
        [f"conform gate: {summary} is unreadable or not a JSON object"],
    )


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


def test_main_hands_its_run_id_and_its_counts_file_to_every_child_through_the_environment(
    tmp_path, monkeypatch
):
    """`main` puts its run id in the environment, and every child inherits it. gate:make-test's wrapper and run_m1's CLI record their own check line unless this variable is set, so it keeps each check to one line. It is set on this process, not passed in a child's argv, because it must survive a Make recipe. The pass's counts file goes the same way (`cycle_timings.OUTPUT_COUNTS_ENV`), so every write through `output_sink`'s helper in any child counts toward the pass: it exists, empty, while the pass runs, and is gone once `main` returns, here from a pass that ended without its run line. The autouse fixture in rebuild/conftest.py removes both variables after the test, because a leftover run id would silence every check-recording test the worker ran next."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "a-stale-run-id")
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    seen = {}

    def fake_cycle(plan, report, emit, registry, **kw):
        seen["env"] = os.environ.get(ct.CYCLE_RUN_ENV)
        seen["run_id"] = kw["timings"].run_id
        seen["counts"] = Path(os.environ[ct.OUTPUT_COUNTS_ENV])
        seen["held"] = seen["counts"].read_bytes()
        return 0

    monkeypatch.setattr(ac, "_run_cycle", fake_cycle)
    assert ac.main([]) == 0
    assert seen["env"] == seen["run_id"]
    assert seen["counts"].name == f"output-counts-{seen['run_id']}.tsv" and seen["held"] == b""
    assert not seen["counts"].exists()


def test_main_runs_the_pass_under_the_stop_handlers(tmp_path, monkeypatch, _stop_dispositions):
    """`main` runs `_run_cycle` with SIGTERM and SIGHUP handlers that raise `CycleStopped`, so a signal that reaches only the driver still reaps the children, and it restores the previous handlers when the pass returns."""
    _settled_repo(tmp_path, monkeypatch)
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
    """What the recovery says, here that it kept a complete `review.next`, is printed before the plan is resolved and before the console exists. terminal.log is a copy of the terminal, so the line appears once in each."""
    _settled_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(ac, "server_listening", lambda port=ac.REVIEW_PORT: False)
    _write_corpus(ac.next_corpus_dir(), {}, "2026-07-17T20:24:44Z")
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
    assert out.count("the complete corpus an earlier pass built beside the served one") == 1
    assert terminal.count("the complete corpus an earlier pass built beside the served one") == 1


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
    plan = _plan(skip_corpus=True, corpus_note="same")
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
    def failing_land(report, *, spawn, emit, registry, plan):
        report.land_status = "FAILED (exit 1)"
        return None

    _patch_timing_cycle(monkeypatch)
    monkeypatch.setattr(ac, "_do_land", failing_land)

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
    assert "land failed" in entries[-1]["failures"]


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
