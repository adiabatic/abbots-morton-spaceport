"""Tests for the green records and check lines run_m1 writes, plus its exit handling and the conformance sweep's fan-in. An interactive run_m1, `--conform-only`, and `--gates-only` record the same green files the artifact cycle skips on, so a fix verified by hand is not verified again by the next cycle, and each records its result as a check line in the timings journal. The results come from artifact_cycle's evaluators (`evaluate_run_m1_gate`, `evaluate_conform_gate`). `--gates-only` records run_m1's green only when a prior green exists and every input that moved since it is comparison-side (`artifact_cycle.gates_only_rerun`), so the next cycle can skip run_m1 after a contact allow-list bless."""

import json
import os
import pickle
import subprocess
import sys
import time
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from multiprocessing import shared_memory
from pathlib import Path
from typing import cast

import pytest

from rebuild.pipeline import (
    conform,
    defects,
    fingerprint,
    fixtures,
    glyph_facts,
    row_delta,
    run_m1,
    run_m1_gates,
    shared_segments,
)
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import calibrate_budgets as cb
from rebuild.tools import console
from rebuild.tools import cycle_paths, green_record
from rebuild.tools import cycle_timings as ct

LONG_AGO_NS = 1_000_000_000

STAGE_A = {"data": "d", "pipeline_code": "p"}


@pytest.fixture
def green_store(tmp_path):
    return tmp_path / "run-m1-green.json"


def _checks():
    """The check lines this test recorded. `ct.JOURNAL` is read at call time because rebuild/conftest.py's autouse fixture redirects it under tmp_path. The same fixture removes the cycle's run id from the environment; without that, running this suite inside a real cycle would make every entry point here skip its check line."""
    return ct.load_checks(ct.JOURNAL)


def _phases(output):
    """The phases this run opened and the labels its `[t]` lines closed, parsed with `console.parse_line` as the cycle console parses a child's output. A phase with no matching timing would reach the terminal with no duration."""
    events = [console.parse_line(line) for line in output.splitlines()]
    opened = [event.name for event in events if isinstance(event, console.Phase)]
    closed = [event.label for event in events if isinstance(event, console.Timing)]
    return opened, closed


def _keys(values):
    calls = iter(values)
    return lambda: next(calls)


class HardExit(Exception):
    pass


class FlushRecorder:
    def __init__(self, name, events):
        self.name = name
        self.events = events

    def flush(self):
        self.events.append(f"flush {self.name}")

    def write(self, value):
        self.events.append(f"write {self.name} {value}")
        return len(value)


def test_hard_exit_flushes_both_streams_before_os_exit(monkeypatch):
    events = []
    monkeypatch.setattr(run_m1_gates.sys, "stdout", FlushRecorder("stdout", events))
    monkeypatch.setattr(run_m1_gates.sys, "stderr", FlushRecorder("stderr", events))

    def exit_(status):
        events.append(f"exit {status}")
        raise HardExit

    monkeypatch.setattr(run_m1_gates.os, "_exit", exit_)
    with pytest.raises(HardExit):
        run_m1_gates._hard_exit(7)
    assert events == ["flush stdout", "flush stderr", "exit 7"]


def test_cli_flushes_output_before_preserving_string_system_exit(monkeypatch):
    events = []
    monkeypatch.setattr(run_m1_gates.sys, "stdout", FlushRecorder("stdout", events))
    monkeypatch.setattr(run_m1_gates.sys, "stderr", FlushRecorder("stderr", events))

    def main():
        print("summary")
        raise SystemExit("expected failure")

    def exit_(status):
        events.append(f"exit {status}")
        raise HardExit(status)

    monkeypatch.setattr(run_m1_gates, "main", main)
    monkeypatch.setattr(run_m1_gates.os, "_exit", exit_)
    with pytest.raises(HardExit, match="1"):
        run_m1_gates._run_cli()
    assert events.index("flush stdout") < events.index("write stderr expected failure")
    assert events[-3:] == ["flush stdout", "flush stderr", "exit 1"]


def test_cli_hard_exits_zero_after_a_normal_return(monkeypatch):
    monkeypatch.setattr(run_m1_gates, "main", lambda: None)
    monkeypatch.setattr(run_m1_gates, "_hard_exit", lambda status: (_ for _ in ()).throw(HardExit(status)))
    with pytest.raises(HardExit, match="0"):
        run_m1_gates._run_cli()


def test_cli_leaves_unexpected_exceptions_to_the_interpreter(monkeypatch):
    monkeypatch.setattr(run_m1_gates, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(run_m1_gates, "_hard_exit", lambda _status: pytest.fail("must not hard-exit"))
    with pytest.raises(RuntimeError, match="boom"):
        run_m1_gates._run_cli()


def test_records_when_the_key_holds_across_the_run(green_store):
    run_m1_gates._settle_green(green_store, "fp-1", True, _keys(["fp-1"]), "run_m1")
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_records_nothing_when_the_inputs_moved_mid_run(green_store, capsys):
    run_m1_gates._settle_green(green_store, "fp-1", True, _keys(["fp-2"]), "run_m1")
    assert ac.read_green_record(green_store) is None
    assert "changed while it ran" in capsys.readouterr().out


def test_red_deletes_a_contradicted_record(green_store):
    ac.record_green(green_store, "fp-1")
    run_m1_gates._settle_green(green_store, "fp-1", False, _keys([]), "run_m1")
    assert ac.read_green_record(green_store) is None


def test_red_leaves_a_record_for_other_content_alone(green_store):
    ac.record_green(green_store, "fp-other")
    run_m1_gates._settle_green(green_store, "fp-1", False, _keys([]), "run_m1")
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-other"


@pytest.mark.parametrize("ok", [True, False])
def test_a_cycle_child_leaves_green_finalization_to_its_parent(monkeypatch, green_store, ok):
    """The presence of the cycle run id gives the parent ownership, including when its value is empty. A child neither publishes nor invalidates, and does not read finalization inputs."""
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "")
    monkeypatch.setattr(
        green_record, "settle_green", lambda *args, **kwargs: pytest.fail("child finalized a green")
    )
    run_m1_gates._settle_green(green_store, "fp-1", ok, _keys([]), "run_m1")


class _JoinedGates:
    """A `TableGates` whose branch has already passed, returned by a stubbed `run` beside its summary, so the join and the close return at once."""

    def join(self):
        return None

    def first_red(self):
        return None

    def close(self):
        return None


def _stub_full_run(monkeypatch, out_dir, *, defect_errors=(), pins=True, pins_in_scope=143):
    """Stubs everything `run_m1.full_run` calls, the Stage A capture included, and points `run_m1.OUT_DIR`, where the Stage A record is deleted and written, at `out_dir`."""
    monkeypatch.setattr(run_m1, "OUT_DIR", out_dir)
    monkeypatch.setattr(run_m1.fingerprint, "stage_a", lambda root: dict(STAGE_A))
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
    monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None, memo_inputs=None, replay_threads=None, scratch_beside_default=None, reuse=None: (
            {"defect_errors": list(defect_errors), "notes": []},
            _JoinedGates(),
        ),
    )
    monkeypatch.setattr(
        run_m1_gates,
        "run_manual_pin_gate",
        lambda spec: {
            "pass": pins,
            "disagreements": [],
            "pins_in_scope": pins_in_scope,
            "replayed": pins_in_scope,
        },
    )


def test_main_runs_the_outgoing_check_then_the_build_then_the_pin_gate(monkeypatch, tmp_path, capsys):
    """A bare run checks the ligatures' outgoing strokes before the build and replays the Manual pins after it, and each phase it opens on the console closes with a timing line. The fingerprint is taken before the build, so the green records the key the run started under."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    events = []
    monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: events.append("outgoing") or {})
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None, memo_inputs=None, replay_threads=None, scratch_beside_default=None, reuse=None: events.append(
            "run"
        )
        or ({"defect_errors": [], "notes": []}, _JoinedGates()),
    )
    monkeypatch.setattr(
        run_m1_gates,
        "run_manual_pin_gate",
        lambda spec: events.append("pins")
        or {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": 4},
    )
    run_m1_gates.main([])
    assert events == ["outgoing", "run", "pins"]
    opened, closed = _phases(capsys.readouterr().out)
    assert opened == ["run_total", "run_manual_pin_gate"]
    assert set(opened) <= set(closed)
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-live"


def test_a_passing_run_records_a_green_and_exits_zero(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    run_m1_gates.main([])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-live"


def test_a_bare_run_records_its_own_peak_footprint_once_its_gates_and_branch_have_finished(
    monkeypatch, tmp_path
):
    """A bare run whose gates passed and whose table-only branch joined records run_m1's own process's peak footprint as the one reading of a `run-m1-parent` pool record, the row `make job-costs` reads it under."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    monkeypatch.setattr(run_m1, "peak_footprint_self_bytes", lambda: 1_540_000_000)
    run_m1_gates.main([])
    records = ct.load_pool_records(ct.JOURNAL)
    assert [(r["unit"], r["width"], r["worker_peak_rss_bytes"]) for r in records] == [
        (run_m1.PARENT_POOL_UNIT, 1, {"parent": 1_540_000_000})
    ]
    assert records[0]["worker_reading"] == "footprint"
    assert any(run_m1.PARENT_POOL_UNIT in unit.pool_units for unit in cb.UNITS)


def test_an_interactive_run_files_the_gates_result(monkeypatch, tmp_path):
    """The check line records the gate's green. It has no `run` field because no cycle started this run."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    run_m1_gates.main([])
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["check"] == "run_m1"
    assert checks[0]["outcome"] == "green"
    assert "run" not in checks[0]


def test_an_interactive_run_files_its_table_builds_phases_and_a_run_that_builds_nothing_files_none(
    monkeypatch, tmp_path
):
    """A standalone run's check line carries its table build's phase lines as `inner`: the crate's own and the `kernel_build_tables` line with the build's record, which names the alphabet's rune count. An invocation that builds nothing carries none, even in the process that ran the earlier build."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    mini = fixtures.mini_spec()

    def building_run(spec, inputs, **rest):
        run_m1.build_tables(mini, tmp_path, inputs="green-test", kernel_threads=1)
        return {"defect_errors": [], "notes": []}, _JoinedGates()

    monkeypatch.setattr(run_m1, "run", building_run)
    run_m1_gates.main([])
    _stub_full_run(monkeypatch, tmp_path)
    run_m1_gates.main([])
    built, bare = _checks()
    labels = [phase["label"] for phase in built["inner"]]
    assert "enumerate[default]" in labels and labels[-1] == "kernel_build_tables"
    assert built["inner"][-1]["runes"] == len(mini.runes)
    assert "inner" not in bare


def test_a_run_that_never_reached_its_evaluator_files_the_gate_that_stopped_it(monkeypatch, tmp_path):
    """A defect gate that stops the build leaves nothing for the evaluator to read, so the red check line carries the defect gate's failure, the text `--gates-only` and the cycle file for it (`artifact_cycle._defect_gate_failure`), and no failed ids. Only the exit message adds where to look."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path, defect_errors=["qsAh: contact"])
    with pytest.raises(SystemExit) as error:
        run_m1_gates.main([])
    failure = "1 defect-gate error(s): qsAh: contact"
    assert str(error.value) == f"{failure}; see pipeline_summary.json"
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["outcome"] == "red"
    assert checks[0]["failures"] == [failure]
    assert checks[0]["failed_ids"] == []


@pytest.mark.parametrize("pins", [True, False])
@pytest.mark.parametrize("prior_key", [None, "fp-live", "fp-other"])
def test_a_cycle_spawned_run_publishes_no_checks_or_greens(monkeypatch, tmp_path, pins, prior_key):
    """The cycle owns a launched child's check line and green record. A passing child cannot publish or refresh a record, and a failed child leaves matching and unrelated prior records for the parent to settle."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    if prior_key is not None:
        ac.record_green(store, prior_key)
    before = store.read_bytes() if store.exists() else None
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path, pins=pins)
    if pins:
        run_m1_gates.main([])
    else:
        with pytest.raises(SystemExit):
            run_m1_gates.main([])
    assert (store.read_bytes() if store.exists() else None) == before
    assert _checks() == []


def test_a_defect_gate_failure_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, tmp_path, defect_errors=["qsAh: contact"])
    with pytest.raises(SystemExit):
        run_m1_gates.main([])
    assert ac.read_green_record(store) is None


def test_a_failed_manual_pin_gate_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, tmp_path, pins=False)
    with pytest.raises(SystemExit):
        run_m1_gates.main([])
    assert ac.read_green_record(store) is None


def test_a_manual_pin_summary_that_comes_out_the_same_is_left_in_place(monkeypatch, tmp_path):
    """A second run of the Manual-pin gate whose summary comes out the same leaves `manual_pins_summary.json` in place, keeping its inode and its modification time, here set long ago so that any write would move it."""
    monkeypatch.setattr(run_m1_gates, "load_default_spec", lambda: None)
    monkeypatch.setattr(run_m1_gates.manual_pins, "run_gate", lambda font, spec: None)
    monkeypatch.setattr(
        run_m1_gates.manual_pins,
        "summarize",
        lambda report: {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": 4},
    )
    summary = tmp_path / "manual_pins_summary.json"
    run_m1_gates.run_manual_pin_gate(tmp_path)
    os.utime(summary, ns=(LONG_AGO_NS, LONG_AGO_NS))
    inode = summary.stat().st_ino
    run_m1_gates.run_manual_pin_gate(tmp_path)
    assert (summary.stat().st_ino, summary.stat().st_mtime_ns) == (inode, LONG_AGO_NS)


def test_a_manual_pin_gate_with_nothing_in_scope_clears_the_record(monkeypatch, tmp_path):
    """`pass` is `not disagreements`, so a gate that replayed no pin reports a pass. run_m1 also requires pins in scope, so an empty replay fails the build."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, tmp_path, pins_in_scope=0)
    with pytest.raises(SystemExit) as error:
        run_m1_gates.main([])
    assert "no pins in scope" in str(error.value)
    assert ac.read_green_record(store) is None


def test_a_bare_run_that_raises_before_its_handlers_files_the_exception_and_clears_a_matching_green(
    monkeypatch, tmp_path
):
    """A rune that does not load stops a bare run before `run_m1.full_run` enters its handlers, since it loads the spec ahead of them. `main` still records the exception as a FAILED red naming it and clears the green over the same content before the exception propagates (`run_m1_gates._recording_failure`)."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, tmp_path)

    def load_default_spec():
        raise KeyError("stance")

    monkeypatch.setattr(run_m1, "load_default_spec", load_default_spec)
    with pytest.raises(KeyError):
        run_m1_gates.main([])
    assert [(check["outcome"], check["status"], check["failures"]) for check in _checks()] == [
        ("red", "FAILED", ["KeyError: 'stance'"])
    ]
    assert ac.read_green_record(store) is None


@pytest.mark.parametrize("stop", [None, SystemExit, ValueError])
def test_a_full_run_writes_the_stage_a_record_only_once_its_gates_pass(monkeypatch, tmp_path, stop):
    """A full run deletes the Stage A record immediately before its first write under rebuild/out/m1, and writes it back after the Manual-pin gate, once the run_m1 gate passes. A run that stops at a red gate (here the Manual-pin gate) or on an exception from that gate may already have rewritten the tables and M1.otf, so it leaves no record: a record from before it would match the sources again once an edit is reverted, and a corpus built by hand or the next cycle's skip would take that run's font for the one the record describes."""
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", tmp_path / "run-m1-green.json")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    (tmp_path / fingerprint.STAGE_A_FILENAME).write_text(
        json.dumps({"format": fingerprint.FORMAT, **{name: "before" for name in STAGE_A}})
    )
    during_pins = []

    def run_manual_pin_gate(spec):
        during_pins.append(fingerprint.read_stage_a(tmp_path))
        if stop is ValueError:
            raise ValueError("the pin corpus did not parse")
        replayed = 3 if stop is SystemExit else 4
        return {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": replayed}

    monkeypatch.setattr(run_m1_gates, "run_manual_pin_gate", run_manual_pin_gate)
    if stop is None:
        run_m1_gates.main([])
    else:
        with pytest.raises(stop):
            run_m1_gates.main([])
    assert during_pins == [None]
    assert fingerprint.read_stage_a(tmp_path) == (STAGE_A if stop is None else None)


@pytest.mark.parametrize("raised", [KeyboardInterrupt, run_m1.readback.ReadbackError])
def test_a_full_run_stopped_inside_the_build_leaves_no_stage_a_record(monkeypatch, tmp_path, raised):
    """The build rewrites the tables and M1.otf before `run` returns, so a Ctrl-C, or an error the run's handlers catch (here a read-back divergence), can stop it over half-rewritten artifacts. The Stage A record is already gone when the build starts, so neither stop leaves one to vouch for what the build left."""
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", tmp_path / "run-m1-green.json")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    (tmp_path / fingerprint.STAGE_A_FILENAME).write_text(
        json.dumps({"format": fingerprint.FORMAT, **{name: "before" for name in STAGE_A}})
    )
    during_run = []

    def run(spec, inputs, **rest):
        during_run.append(fingerprint.read_stage_a(tmp_path))
        raise raised("the build stopped")

    monkeypatch.setattr(run_m1, "run", run)
    with pytest.raises(KeyboardInterrupt if raised is KeyboardInterrupt else SystemExit):
        run_m1_gates.main([])
    assert during_run == [None]
    assert fingerprint.read_stage_a(tmp_path) is None


@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        ([], "rune that does not load"),
        (["--gates-only"], "stale stamp"),
    ],
)
def test_a_refusal_before_any_write_leaves_the_stage_a_record_byte_identical(
    monkeypatch, tmp_path, argv, refusal
):
    """A full run or a `--gates-only` pass that a check refuses, or that fails, before its first write (tables the runes have outgrown, a rune that does not load) leaves every artifact the Stage A record vouches for as it found them, so it leaves the record as it found it too. Deleting it would make the next cycle rebuild M1 and the corpus once the edit is fixed or reverted, though no artifact changed."""
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", tmp_path / "run-m1-green.json")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, tmp_path)
    if refusal == "rune that does not load":

        def load_default_spec():
            raise KeyError("stance")

        monkeypatch.setattr(run_m1, "load_default_spec", load_default_spec)
    else:
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: "fp")
        monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: None)
    record = tmp_path / fingerprint.STAGE_A_FILENAME
    record.write_text(json.dumps({"format": fingerprint.FORMAT, **{name: "before" for name in STAGE_A}}))
    before = record.read_bytes()
    with pytest.raises(KeyError if refusal == "rune that does not load" else SystemExit):
        run_m1_gates.main(argv)
    assert record.read_bytes() == before


def test_conform_only_records_its_own_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, max_length=4: {})
    monkeypatch.setattr(
        run_m1_gates, "run_font_conformance", lambda max_length, jobs: {"pass": True, "divergences": 0}
    )
    run_m1_gates.main(["--conform-only"])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-conform"


@pytest.mark.parametrize("reported_pass", [True, False])
def test_conform_only_divergences_fail_and_clear_a_matching_green(monkeypatch, tmp_path, reported_pass):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    ac.record_green(store, "fp-conform")
    monkeypatch.setattr(
        run_m1_gates,
        "run_font_conformance",
        lambda max_length, jobs: {"pass": reported_pass, "divergences": 3},
    )
    with pytest.raises(SystemExit):
        run_m1_gates.main(["--conform-only"])
    assert ac.read_green_record(store) is None


@pytest.mark.parametrize("cycle", [False, True])
def test_a_conform_only_refusal_files_why_and_clears_a_matching_green_unless_a_cycle_owns_it(
    monkeypatch, tmp_path, capsys, cycle
):
    """A sweep over tables stamped from other sources than the ones on disk refuses before it sweeps anything. Run by hand, it records the refusal as a `REFUSED` check line and clears the conform green over the same content. A cycle's child prints that line instead, and gate:conform files it in place of the missing summary's reason."""
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    ac.record_green(store, "fp-conform")
    green = store.read_bytes()
    monkeypatch.setattr(run_m1, "tables_inputs", lambda: "fp")
    monkeypatch.setattr(run_m1, "settle_memo_inputs", lambda: None)
    monkeypatch.setattr(run_m1_gates, "load_default_spec", lambda: object())
    monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: None)
    if cycle:
        monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
    with pytest.raises(SystemExit, match="built from other sources than the ones on disk") as error:
        run_m1_gates.main(["--conform-only"])
    reason = str(error.value)
    if not cycle:
        assert [(check["check"], check["status"], check["failures"]) for check in _checks()] == [
            ("conform", ct.REFUSED_STATUS, [reason])
        ]
        assert ac.read_green_record(store) is None
        return
    assert _checks() == []
    assert store.read_bytes() == green
    out = capsys.readouterr().out
    gate = ac._gate_conform_task(
        False,
        None,
        lambda name, argv, **kwargs: ac._StepResult(name, 1, out, reason, 0.0),
        ac._Emitter(),
        ac._ChildRegistry(),
        [],
    )
    assert (gate.outcome, gate.status, gate.failures) == ("red", ct.REFUSED_STATUS, [reason])


@pytest.mark.parametrize("passed", [True, False])
@pytest.mark.parametrize("prior_key", [None, "fp-conform", "fp-other"])
def test_a_cycle_conform_child_publishes_no_checks_or_greens(monkeypatch, tmp_path, passed, prior_key):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, max_length=4: {})
    if prior_key is not None:
        ac.record_green(store, prior_key)
    before = store.read_bytes() if store.exists() else None
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
    monkeypatch.setattr(
        run_m1_gates,
        "run_font_conformance",
        lambda max_length, jobs: {"pass": passed, "divergences": 0 if passed else 3},
    )
    if passed:
        run_m1_gates.main(["--conform-only"])
    else:
        with pytest.raises(SystemExit, match="font conformance failed"):
            run_m1_gates.main(["--conform-only"])
    assert (store.read_bytes() if store.exists() else None) == before
    assert _checks() == []


def test_conform_only_files_its_own_check(monkeypatch):
    """The sweep records its own check line, named `conform` as the cycle names gate:conform, with the status `evaluate_conform_gate` returns. A divergence records `FAILED`, the status the cycle summary prints."""
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, max_length=4: {})
    monkeypatch.setattr(
        run_m1_gates, "run_font_conformance", lambda max_length, jobs: {"pass": True, "divergences": 0}
    )
    run_m1_gates.main(["--conform-only"])
    assert [(check["check"], check["status"]) for check in _checks()] == [("conform", "green")]

    monkeypatch.setattr(
        run_m1_gates, "run_font_conformance", lambda max_length, jobs: {"pass": False, "divergences": 3}
    )
    with pytest.raises(SystemExit):
        run_m1_gates.main(["--conform-only"])
    assert [(check["check"], check["status"]) for check in _checks()][-1] == ("conform", "FAILED")


def test_the_conform_max_length_default_matches_the_cycle_driver(monkeypatch, tmp_path):
    """The maximum length is part of the conform green's key, so if run_m1's default differed from the cycle driver's, an interactive sweep would record a green no cycle could match."""
    store = tmp_path / "conform-green.json"
    swept = []
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, max_length=4: {})

    def fake_sweep(max_length, jobs):
        swept.append(max_length)
        return {"pass": True, "divergences": 0}

    monkeypatch.setattr(run_m1_gates, "run_font_conformance", fake_sweep)
    run_m1_gates.main(["--conform-only"])
    assert swept == [ac.CONFORM_MAX_LENGTH_DEFAULT]


def test_a_hand_conform_only_run_defaults_to_the_conform_sweeps_budget(monkeypatch, tmp_path):
    """A hand `--conform-only` shares the machine with no corpus build and no make-test pool, so its default is `conform_job_budget(skip_gates=True, skip_corpus=True)`. A stated `--jobs`, including 1, overrides the default."""
    store = tmp_path / "conform-green.json"
    handed = []
    conform_jobs = ac.conform_job_budget(skip_gates=True, skip_corpus=True)
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, max_length=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, max_length=4: {})

    def fake_sweep(max_length, jobs):
        handed.append(jobs)
        return {"pass": True, "divergences": 0}

    monkeypatch.setattr(run_m1_gates, "run_font_conformance", fake_sweep)
    run_m1_gates.main(["--conform-only"])
    run_m1_gates.main(["--conform-only", "--jobs", "1"])
    run_m1_gates.main(["--conform-only", "--jobs", "4"])
    assert handed == [max(1, conform_jobs), 1, 4]


class _InlinePool:
    """A stand-in for the spawn pool that runs each task when it is submitted and returns a finished future of its outcome, so the conformance sweep's units and absorbs run without a process per task and without a build to sweep. `submitted` lists each task's function in the order it was submitted."""

    def __init__(self):
        self.submitted: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def submit(self, function, *args, **kwargs):
        self.submitted.append(function)
        future: Future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class TestConformFanIn:
    """The conformance sweep's fan-in runs one unit per process (the ss10 overlay and each cosmetic configuration whole, and each settlement configuration once per final symbol), writes its units' peak memory as a `conform-sweep` pool record, writes none for a serial or deeper sweep, prunes and absorbs each settlement configuration's memo in the parent, and writes the same report as the serial conformance sweep at any width. Only the sweep is stubbed (`conform._conformance_config`, which the serial path and every worker call), over a three-symbol alphabet, so the real unit worker, `run_conformance`, and both merges run in every mode."""

    ALPHABET = ("a", "b", "c")
    ROWS = 4

    @staticmethod
    def _swept(
        shaper,
        spec,
        config,
        alphabet,
        splitters,
        glyph_names,
        anchors_of,
        max_length,
        guard_verdicts=None,
        settle_memo=None,
        progress=None,
        last=None,
        triggers=frozenset(),
        max_windows=None,
        kern_sides=None,
    ):
        """A deterministic sweep that shapes nothing: every text of the configuration's lengths that ends in `last` (every text without it) is a sequence costing the configuration's own count of shaping runs, and every text ending in the configuration's own symbol diverges, so a merge that misordered the units or the configurations would write a different report. The per-edit sweep shapes every text in every configuration, so no unit gets trigger letters, and its walks share a settle memo, so none gets a window ceiling."""
        assert not triggers and max_windows is None
        index = conform.ACCEPTANCE_CONFIGS.index(config)
        top = (
            conform.OVERLAY_MAX_LENGTH
            if config in conform.OVERLAY_CONFIGS
            else conform.COSMETIC_MAX_LENGTH if config in conform.COSMETIC_CONFIGS else max_length
        )
        result = conform.ConformanceConfigResult(config=config)
        tally = conform.DivergenceTally(result, alphabet)
        for length in range(1, top + 1):
            for text in conform.sweep_texts(alphabet, length, last):
                result.sequences += 1
                result.shaping_runs += 1 + index
                if text[-1] == alphabet[index % len(alphabet)]:
                    tally.append(
                        conform.Divergence(
                            text=text,
                            config=config,
                            position=index,
                            expected="qsPea",
                            got="qsPea.alt",
                            kind=f"kind-{config}",
                        )
                    )
        result.modes = [f"mode-{index % 2}"]
        return result

    def _pool(self, monkeypatch):
        """Stubs out every process and build input in the fan-out: an inline pool, the crate and the parent's spec dump, no spec, no tables, no memo, and a three-symbol alphabet, plus fakes for what the worker and `run_conformance` build before the sweep, since the spec is None."""
        monkeypatch.setattr(
            run_m1, "_spawn_pool", lambda jobs, units, max_tasks_per_child=None: _InlinePool()
        )
        monkeypatch.setattr(run_m1.kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(run_m1.kernel_exec, "guard_sweep", lambda spec: {})
        monkeypatch.setattr(run_m1.kernel_exec, "_spec_dump", lambda spec: Path("parent-spec.json"))
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: None)
        monkeypatch.setattr(run_m1, "settle_memo_inputs", lambda: None)
        monkeypatch.setattr(run_m1_gates, "load_default_spec", lambda: None)
        monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: {})
        monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, decisions: {})
        monkeypatch.setattr(run_m1_gates, "load_build_kern_sides", lambda out_dir: None)
        monkeypatch.setattr(conform, "settle_memo_files", lambda out_dir, spec, inputs: {})
        monkeypatch.setattr(conform, "Shaper", lambda font_path: object())
        monkeypatch.setattr(conform, "spec_alphabet", lambda spec: self.ALPHABET)
        monkeypatch.setattr(conform, "splitting_boundary_chars", lambda spec: frozenset())
        monkeypatch.setattr(conform, "_conformance_config", self._swept)

    def _memos(
        self, monkeypatch, tmp_path, reached, parts=(), lose=None, other=None, retired=None
    ) -> dict[str, list]:
        """Give every settlement configuration a memo and every settlement unit a reach over one file version of `ROWS` rows: `reached[config][symbol]` names the rows that unit reached (every row between a configuration's units by default), a unit named in `parts` as `(config, symbol)` settled one window fresh, the unit named by `lose` shapes one text too few, the unit named by `other` mapped another version of the file, and the unit named by `retired` retired its store and reached nothing. The absorb task is stubbed to record the counts of the fresh windows it was handed and the prune, and to prune what the mask leaves unreached; the returned mapping collects them by configuration."""
        memos = {
            config: conform.SettleMemoFile(tmp_path / f"settle-memo-{config}.bin", "stamp")
            for config in conform.SETTLEMENT_CONFIGS
        }
        monkeypatch.setattr(conform, "settle_memo_files", lambda out_dir, spec, inputs: memos)
        spread = {"a": (0, 1), "b": (2,), "c": (3,)}

        def swept(*args, settle_memo=None, last=None, **kwargs):
            result = self._swept(*args, settle_memo=settle_memo, last=last, **kwargs)
            if (result.config, last) == lose:
                result.sequences -= 1
            if settle_memo is not None:
                assert settle_memo.returns_fresh and last is not None
                unit = (result.config, last)
                rows = () if unit == retired else reached.get(result.config, spread)[last]
                flags = bytes(int(row in rows) for row in range(self.ROWS))
                result.memo_reach = conform.MemoReach(
                    identity=(5, 6, 7, 8) if unit == other else (1, 2, 3, 4),
                    retired=unit == retired,
                    rows=self.ROWS,
                    live=self.ROWS,
                    loaded=self.ROWS,
                    stale=0,
                    reached=zlib.compress(flags, 1),
                    fresh=conform.FreshMemo([b"head", b"rows"], 1, 6, 1) if unit in parts else None,
                    seconds=0.25,
                )
            return result

        monkeypatch.setattr(conform, "_conformance_config", swept)
        absorbed: dict[str, list] = {}

        def absorb(memo, spec, parts, sweep, route=None):
            assert route is not None and route.output == memo.path.name
            flags = zlib.decompress(sweep.reached)
            absorbed[memo.path.stem.removeprefix("settle-memo-")] = [
                [counts for _shipped, counts in parts],
                sweep.identity,
                flags,
                sweep.prune,
                sweep.discard,
            ]
            pruned = self.ROWS if sweep.discard else self.ROWS - flags.count(1) if sweep.prune else 0
            kept = self.ROWS - pruned
            return (
                conform.MemoAbsorb(
                    True, kept, pruned, len(parts), standing=self.ROWS, parts=len(parts), index="rebuilt"
                ),
                0.0,
                1,
            )

        monkeypatch.setattr(run_m1_gates, "_absorb_memo", absorb)
        return absorbed

    def test_a_spawn_pool_is_never_wider_than_the_tasks_it_is_given(self):
        """A hand `--jobs` can be any number, so `_spawn_pool` caps the pool at one process per task the caller submits besides its absorbs, the conformance sweep's units. A smaller number narrows the pool. The conformance sweep's pool runs one task per process, so the keyword passes through, and it defaults to reuse, which the string replay's pool takes."""
        units = len(run_m1_gates.sweep_units(("a", "b", "c"), conform.SWEEP_MAX_LENGTH))
        wide = run_m1._spawn_pool(64, units, max_tasks_per_child=1)
        try:
            assert wide._max_workers == units  # pyright: ignore[reportAttributeAccessIssue]
            assert wide._max_tasks_per_child == 1  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            wide.shutdown(wait=False)
        narrow = run_m1._spawn_pool(2, 15)
        try:
            assert narrow._max_workers == 2  # pyright: ignore[reportAttributeAccessIssue]
            assert narrow._max_tasks_per_child is None  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            narrow.shutdown(wait=False)

    def test_a_pooled_sweep_files_one_conform_sweep_pool_record(self, monkeypatch, tmp_path):
        """A pooled conformance sweep writes one record, at the pool's width capped at the unit count, with one observation per acceptance configuration, under a unit name listed in `calibrate_budgets.UNITS`; a record under an unlisted name would be ignored, as if the conformance sweep had never run pooled on this machine. The inline pool runs each unit at submission in unit order and the futures resolve in reverse, and each configuration's highest reading belongs to its middle unit, which resolves neither first nor last: a fan-in that recorded a unit's peak under another configuration, or kept its first or last reading rather than its highest, would fail. The controller's own reading goes in its own field."""
        self._pool(monkeypatch)
        assert "conform-sweep" in {name for unit in cb.UNITS for name in unit.pool_units}
        units = run_m1_gates.sweep_units(self.ALPHABET, conform.SWEEP_MAX_LENGTH)
        monkeypatch.setattr(run_m1_gates, "peak_rss_self_bytes", lambda: 10**9)
        readings = [0] * len(units)
        for rank, config in enumerate(conform.ACCEPTANCE_CONFIGS, start=1):
            slots = [slot for slot, unit in enumerate(units) if unit.config == config]
            for index, slot in enumerate(slots):
                readings[slot] = 10 * rank + (2 if index == len(slots) // 2 else 1)
        for jobs in (6, len(units) + 5):
            monkeypatch.setattr(run_m1_gates, "peak_footprint_self_bytes", iter(readings).__next__)
            before = len(ct.load_pool_records(ct.JOURNAL))
            run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=jobs)
            records = ct.load_pool_records(ct.JOURNAL)[before:]
            assert len(records) == 1
            (record,) = records
            assert record["unit"] == "conform-sweep"
            assert record["width"] == min(jobs, len(units))
            assert record["worker_peak_rss_bytes"] == {
                config: 10 * rank + 2 for rank, config in enumerate(conform.ACCEPTANCE_CONFIGS, start=1)
            }
            assert record["controller_peak_rss_bytes"] == 10**9
            assert record["worker_reading"] == "footprint"

    def test_a_serial_or_deep_sweep_files_no_pool_record(self, monkeypatch, tmp_path):
        """The serial conformance sweep starts no pool to measure. A deeper sweep's unit holds its maximum length's windows in memory, a different load from a per-edit unit's, so it must not be recorded as a conformance-sweep unit."""
        self._pool(monkeypatch)
        run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=1)
        run_m1_gates.run_font_conformance(out_dir=tmp_path, max_length=conform.SWEEP_MAX_LENGTH + 1, jobs=6)
        assert ct.load_pool_records(ct.JOURNAL) == []

    def test_the_sweep_writes_the_same_summary_at_every_width(self, monkeypatch, tmp_path):
        """Width 1 runs `conform.run_conformance`, one whole walk per configuration, and wider widths run the pooled fan-in over the units, whose futures resolve here in reverse. The report is byte-identical at every width, up to one process per unit, so changing the conformance sweep's width does not change its report."""
        self._pool(monkeypatch)
        units = len(run_m1_gates.sweep_units(self.ALPHABET, conform.SWEEP_MAX_LENGTH))
        written = {}
        for jobs in (1, 2, 6, units):
            run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=jobs)
            written[jobs] = (tmp_path / "conform_summary.json").read_bytes()
        assert len(set(written.values())) == 1
        summary = json.loads(written[1])
        assert list(summary["divergences_by_kind"]) == [
            f"kind-{config}" for config in conform.ACCEPTANCE_CONFIGS
        ]

    def test_the_parent_absorbs_only_what_its_units_left_to_absorb(self, monkeypatch, tmp_path, capsys):
        """Each settlement configuration's units reach rows of one file version and return what they settled fresh; the parent ORs the reaches and submits an absorb only for a configuration whose units settled a window fresh (`ss03`) or left a live row unreached (`ss04`, whose units reach two of its four rows). The others hold a current memo, get no task, and print `fresh=0 pruned=0 written=no`. The parent prints one `[t] settle_memo` line per settlement configuration in configuration order, an absorbed one ending with the absorb's path and row counts, and each absorb's peak joins the pool record as `<config> absorb`."""
        self._pool(monkeypatch)
        absorbed = self._memos(
            monkeypatch,
            tmp_path,
            {"ss04": {"a": (0,), "b": (0, 1), "c": (1,)}},
            parts=[("ss03", "b")],
        )
        run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=6)
        assert set(absorbed) == {"ss03", "ss04"}
        (part,), identity, flags, prune, discard = absorbed["ss03"]
        assert part == conform.FreshMemo((), 1, 6, 1) and identity == (1, 2, 3, 4) and prune and not discard
        assert flags == b"\x01" * self.ROWS
        assert absorbed["ss04"][0] == [] and absorbed["ss04"][2] == b"\x01\x01\x00\x00"
        lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("[t] settle_memo ")]
        assert [line.split()[2] for line in lines] == list(conform.SETTLEMENT_CONFIGS)
        tails = {line.split()[2]: line.split(" ", 4)[4] for line in lines}
        assert tails["ss03"] == (
            "loaded=4 stale=0 fresh=1 pruned=0 written=yes index=rebuilt standing=4 kept=4 patched=0 dropped=0"
        )
        assert tails["ss04"] == (
            "loaded=4 stale=0 fresh=0 pruned=2 written=yes index=rebuilt standing=4 kept=2 patched=0 dropped=2"
        )
        for config in ("default", "ss05", "ss03+ss05"):
            assert tails[config] == "loaded=4 stale=0 fresh=0 pruned=0 written=no"
        (record,) = ct.load_pool_records(ct.JOURNAL)
        assert {name for name in record["worker_peak_rss_bytes"] if name.endswith(" absorb")} == {
            "ss03 absorb",
            "ss04 absorb",
        }

    def test_units_that_mapped_two_versions_of_the_file_prune_nothing(self, monkeypatch, tmp_path, capsys):
        """When one of `ss03`'s units mapped another version of the file than the others, their flags index two row sets, so the parent warns and its absorb keeps every row (`prune=False`) while still merging the unit's fresh windows, even though every other unit's reach leaves rows unreached. A configuration whose units agree, reach every row, and settled nothing fresh still gets no absorb."""
        self._pool(monkeypatch)
        absorbed = self._memos(
            monkeypatch,
            tmp_path,
            {"ss03": {"a": (0,), "b": (0,), "c": (1,)}},
            parts=[("ss03", "c")],
            other=("ss03", "c"),
        )
        run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=6)
        assert set(absorbed) == {"ss03"}
        (part,), _identity, _flags, prune, discard = absorbed["ss03"]
        assert part == conform.FreshMemo((), 1, 6, 1) and not prune and not discard
        out, err = capsys.readouterr()
        assert "settle-memo-ss03.bin was mapped at more than one version by ss03's units" in out + err
        tails = {
            line.split()[2]: line.split(" ", 4)[4]
            for line in err.splitlines()
            if line.startswith("[t] settle_memo ")
        }
        assert tails["ss03"] == (
            "loaded=4 stale=0 fresh=1 pruned=0 written=yes index=rebuilt standing=4 kept=4 patched=0 dropped=0"
        )

    def test_a_unit_that_retired_its_store_makes_the_absorb_discard_the_file(self, monkeypatch, tmp_path):
        """A unit whose probe found the file corrupt retired its store and reached nothing. Its configuration gets an absorb even with no fresh window, and the absorb is told to discard the file's rows rather than carry them into the rewrite."""
        self._pool(monkeypatch)
        absorbed = self._memos(monkeypatch, tmp_path, {}, retired=("ss05", "a"))
        run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=6)
        assert set(absorbed) == {"ss05"}
        parts, identity, _flags, _prune, discard = absorbed["ss05"]
        assert parts == [] and identity == (1, 2, 3, 4) and discard

    def test_a_unit_that_raises_cancels_the_units_still_queued_and_absorbs_nothing(
        self, monkeypatch, tmp_path
    ):
        """When the first unit raises, the sweep re-raises its error once the unit already running has finished, having submitted no more than the pool's width and cancelled what was still queued. No absorb is submitted, so no memo is pruned from a partial set of units, and no summary is written."""
        self._pool(monkeypatch)
        absorbed = self._memos(monkeypatch, tmp_path, {})
        monkeypatch.setattr(
            run_m1,
            "_spawn_pool",
            lambda jobs, units, max_tasks_per_child=None: ThreadPoolExecutor(max_workers=1),
        )
        ran: list[str] = []

        def unit(
            spec,
            font_path,
            config,
            last,
            max_length,
            glyphs,
            guard_verdicts,
            settle_memo,
            memo_to,
            spec_path,
            kern_sides=None,
        ):
            ran.append(config)
            if config in conform.OVERLAY_CONFIGS:
                raise ValueError("the overlay failed")
            time.sleep(0.2)
            return conform.ConformanceConfigResult(config=config), 1, None

        monkeypatch.setattr(run_m1_gates, "_conformance_unit", unit)
        with pytest.raises(ValueError, match="the overlay failed"):
            run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=2)
        assert ran[0] in conform.OVERLAY_CONFIGS
        assert len(ran) <= 2 < len(run_m1_gates.sweep_units(self.ALPHABET, conform.SWEEP_MAX_LENGTH))
        assert absorbed == {}
        assert not (tmp_path / "conform_summary.json").exists()

    def test_a_short_count_raises_before_its_configuration_is_absorbed(self, monkeypatch, tmp_path):
        """A configuration whose units shaped fewer texts than every text of its lengths raises before the summary is written and before its own absorb is submitted, so neither a green nor a memo pruned from a partial walk can follow from it. A configuration whose units all finished and checked before it (`default`) has been absorbed already, from its own units alone, and the segment each unit shipped its fresh windows in is unlinked whether or not an absorb mapped it."""
        self._pool(monkeypatch)
        absorbed = self._memos(
            monkeypatch, tmp_path, {}, parts=[("default", "a"), ("ss04", "a")], lose=("ss04", "c")
        )
        names: list[str] = []
        shipping = shared_segments.ship

        def ship(pieces, name):
            names.append(name)
            return shipping(pieces, name)

        monkeypatch.setattr(shared_segments, "ship", ship)
        expected = sum(len(self.ALPHABET) ** length for length in range(1, conform.SWEEP_MAX_LENGTH + 1))
        with pytest.raises(
            RuntimeError,
            match=rf"conformance sweep\[ss04\]: its units shaped {expected - 1} texts, not the {expected}",
        ):
            run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=6)
        assert set(absorbed) == {"default"}
        assert not (tmp_path / "conform_summary.json").exists()
        assert len(names) == 2
        for name in names:
            with pytest.raises(FileNotFoundError):
                shared_memory.SharedMemory(name, track=False)
        assert not list(tmp_path.glob(f"{run_m1_gates.SWEEP_SCRATCH_PREFIX}*"))

    def test_the_sweeps_file_is_the_same_whatever_order_its_units_finish_in(
        self, monkeypatch, tmp_path, capsys
    ):
        """Every unit of `default` restates one window with an outcome of its own, so the outcome the file keeps is the last unit's its absorb reads. The parent hands the absorb its units' fresh windows in unit-slot order, so whether the units finish in slot order or the reverse, the file, its first base, comes out byte for byte as the absorb writes it from the units in slot order. Each unit reads the parent's spec dump and runs in a process of its own, and the parent reports the most its units' segments held at once on its `[t] conform_fan_in` line."""
        self._pool(monkeypatch)
        memo = conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", "stamp")
        monkeypatch.setattr(conform, "settle_memo_files", lambda out_dir, spec, inputs: {"default": memo})
        edge = ("#EDGE",) * 4

        def fresh(symbol):
            outcome = cast(conform._Outcome, (f"settled-{symbol}", "", ""))
            windows = [
                cast(conform._Window, ("shared", "#EDGE", *edge)),
                cast(conform._Window, (f"qs{symbol}", "#EDGE", *edge)),
            ]
            return conform._fresh_memo([(window, outcome) for window in windows])

        def swept(*args, settle_memo=None, last=None, **kwargs):
            result = self._swept(*args, settle_memo=settle_memo, last=last, **kwargs)
            if settle_memo is not None:
                result.memo_reach = conform.MemoReach(
                    None, False, 0, 0, 0, 0, zlib.compress(b"", 1), fresh(last), 0.0
                )
            return result

        monkeypatch.setattr(conform, "_conformance_config", swept)
        adopted: list[Path] = []
        monkeypatch.setattr(run_m1.kernel_exec, "adopt_spec_dump", lambda spec, path: adopted.append(path))
        widths: list = []

        def pool(jobs, units, max_tasks_per_child=None):
            widths.append(max_tasks_per_child)
            return _InlinePool()

        monkeypatch.setattr(run_m1, "_spawn_pool", pool)
        written = {}
        for name, order in (
            ("slot", lambda futures: futures),
            ("reverse", lambda futures: list(reversed(futures))),
        ):
            monkeypatch.setattr(
                run_m1_gates, "wait", lambda futures, return_when, order=order: (order(list(futures)), set())
            )
            run_m1_gates.run_font_conformance(out_dir=tmp_path, jobs=4)
            head = row_delta.read_head(tmp_path, memo.path.name)
            assert head is not None and not head.segments
            written[name] = (tmp_path / head.base.name).read_bytes()
            for path in tmp_path.glob(f"{memo.path.name}*"):
                path.unlink()
        reference = tmp_path / "reference.bin"
        conform.absorb_settle_memo(
            memo,
            None,  # pyright: ignore[reportArgumentType]
            [fresh(symbol) for symbol in self.ALPHABET],
            emit=lambda finished: conform._put_settle_memo(finished, reference),
        )
        assert written["slot"] == written["reverse"] == reference.read_bytes()
        units = run_m1_gates.sweep_units(self.ALPHABET, conform.SWEEP_MAX_LENGTH)
        assert adopted == [Path("parent-spec.json")] * 2 * len(units)
        assert widths == [1, 1]
        fan_in = [
            entry
            for entry in ct.parse_inner_timings(capsys.readouterr().out)
            if entry["label"] == "conform_fan_in"
        ]
        assert len(fan_in) == 2 and all(entry["memo_held_gb"] >= 0 for entry in fan_in)

    def test_a_dead_sweeps_segments_are_unlinked_by_the_next_sweep(self, tmp_path):
        """A SIGKILL of a pooled conformance sweep's whole process group skips every exit, so the segments its units shipped and no absorb mapped stay in the kernel. The next sweep over the same out directory unlinks them by the names the dead sweep recorded under it, and keeps a running sweep's record."""
        finished = subprocess.Popen([sys.executable, "-c", ""])
        finished.wait()
        dead = tmp_path / f"{run_m1_gates.SWEEP_SCRATCH_PREFIX}{finished.pid}"
        dead.mkdir()
        (dead / shared_segments.SEGMENT_RECORD).write_text("0123456789ab 3\n")
        left = run_m1_gates._sweep_segment_names("0123456789ab", 3)[1]
        shared_memory.SharedMemory(left, create=True, size=8, track=False).close()
        live = tmp_path / f"{run_m1_gates.SWEEP_SCRATCH_PREFIX}{os.getppid()}"
        live.mkdir()
        with run_m1_gates._SweepSegments(tmp_path, 2) as segments:
            assert len({segments.name(0), segments.name(1)}) == 2
            assert not dead.exists() and live.is_dir()
            assert run_m1_gates._sweep_scratch(tmp_path).is_dir()
            with pytest.raises(FileNotFoundError):
                shared_memory.SharedMemory(left, track=False)
            shared_memory.SharedMemory(segments.name(1), create=True, size=8, track=False).close()
        with pytest.raises(FileNotFoundError):
            shared_memory.SharedMemory(segments.name(1), track=False)
        assert not run_m1_gates._sweep_scratch(tmp_path).exists()

    def test_the_unit_worker_and_the_absorb_pickle_for_spawn(self):
        """The inline pool never pickles what it runs, but a spawn pool pickles every submission by module and name, so a nested wrapper would pass every other test here and fail only on the first real pooled conformance sweep."""
        for task in (run_m1_gates._conformance_unit, run_m1_gates._absorb_memo):
            assert pickle.loads(pickle.dumps(task)) is task


class TestGatesOnly:
    """`--gates-only` re-runs the defect gate and the Manual-pin replay over the build already on disk, without the stages that make the artifacts. It exits with an error on a stamp that no longer matches the runes and on a build that left no summary to rewrite the defect fields into. It records a green only when every input that moved since the last green build is comparison-side, so a bless of the contact allow-list leaves the next cycle nothing to do, while a toolchain bump does not."""

    def _reuse(self, monkeypatch, tables):
        """Stubs the Stage A capture and the stamp check, which read live files under rebuild/out that a contracts-lane test may not read. Returns the stage log the ordering test reads."""
        ran: list[str] = []
        monkeypatch.setattr(
            run_m1.fingerprint, "stage_a", lambda repo_root: ran.append("stage_a") or dict(STAGE_A)
        )
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: "fp")
        monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: tables)
        return ran

    def _summary(self, out_dir, **fields):
        """Writes the summary a completed build leaves, whose defect fields this pass rewrites."""
        (out_dir / "pipeline_summary.json").write_text(
            json.dumps({"gsub_rule_count": 7, "font": "M1.otf", **fields}, indent=2) + "\n"
        )

    def _build(self, monkeypatch, tmp_path, ran, *, report=None):
        """Stubs the build this pass reuses: the font and its glyph-facts sidecar beside the stamped tables, the join tables the defect gate reads beside the enumeration, the minting, and the defect gate (returning `report`)."""
        (tmp_path / "M1.otf").write_bytes(b"font")
        (tmp_path / glyph_facts.FILE_NAME).write_text("{}")
        monkeypatch.setattr(run_m1, "OUT_DIR", tmp_path)
        monkeypatch.setattr(run_m1_gates, "load_default_spec", lambda: object())
        monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
        monkeypatch.setattr(run_m1.table_module, "read_join_tsv", lambda path: f"joins {path.name}")
        monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, tables: {})
        monkeypatch.setattr(run_m1, "resolve_kern", lambda spec, tables, cell_glyphs: None)
        monkeypatch.setattr(
            run_m1,
            "_run_defect_gates",
            lambda spec, tables, cell_glyphs, kerned=None: ran.append("defects")
            or (defects.DefectReport() if report is None else report),
        )

    def _green(self, monkeypatch, tmp_path, *, files=None, prior=None, prior_key="fp-prior"):
        """Stubs run_m1's green record under tmp_path, the key this pass computes over its inputs, and the per-file map it compares with the one the last green build stored. Every path past `gates_only_base` computes that key, and outside a cycle so does a refusal before it, so a test that omits this hashes inputs under rebuild/out and fails on the lane guard instead of its own assertion."""
        store = tmp_path / "run-m1-green.json"
        monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
        monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-now")
        monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: dict(files or {}))
        if prior is not None:
            ac.record_green(store, prior_key, files=prior)
        return store

    def _gates(self, monkeypatch, ran, *, replayed=4):
        monkeypatch.setattr(
            run_m1_gates,
            "run_manual_pin_gate",
            lambda out_dir, spec: ran.append("pins")
            or {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": replayed},
        )

    def test_it_refuses_tables_the_runes_have_outgrown(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, None)
        self._green(monkeypatch, tmp_path)
        with pytest.raises(SystemExit) as error:
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert "it does not make one" in str(error.value)

    def test_it_refuses_a_missing_font(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, {})
        self._green(monkeypatch, tmp_path)
        with pytest.raises(SystemExit) as error:
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert "no compiled font" in str(error.value)

    @pytest.mark.parametrize("left_behind", [None, "{ not a summary", "[]"])
    def test_it_refuses_a_build_that_left_no_summary_to_rewrite(self, monkeypatch, tmp_path, left_behind):
        """The defect fields are rewritten into the build's own summary, so the pass exits with an error when the build left none or left something that is not a summary, and records the refusal. Otherwise it would write a summary no build produced and evaluate itself against it."""
        self._reuse(monkeypatch, {})
        self._green(monkeypatch, tmp_path)
        (tmp_path / "M1.otf").write_bytes(b"font")
        (tmp_path / glyph_facts.FILE_NAME).write_text("{}")
        if left_behind is not None:
            (tmp_path / "pipeline_summary.json").write_text(left_behind)
        with pytest.raises(SystemExit) as error:
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert "no readable" in str(error.value)
        assert [(check["outcome"], check["status"]) for check in _checks()] == [("red", ct.REFUSED_STATUS)]

    def test_it_refuses_a_join_table_the_defect_gate_cannot_read(self, monkeypatch, tmp_path):
        """The defect gate reads the join tables beside the enumeration, so a matching stamp over a join table that will not parse means the build is only partly on disk. The pass exits with an error naming the file instead of a traceback from the gate."""
        ran = self._reuse(monkeypatch, {"ss06": "decision"})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)

        def refuse(path):
            raise OSError("truncated")

        monkeypatch.setattr(run_m1.table_module, "read_join_tsv", refuse)
        with pytest.raises(SystemExit) as error:
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert "joins-ss06.tsv is missing or unreadable" in str(error.value)

    @pytest.mark.parametrize("missing", [None, "stamp", "font", "glyph-facts", "summary", "joins"])
    def test_the_cycle_plans_a_rerun_over_exactly_the_builds_this_pass_accepts(
        self, monkeypatch, tmp_path, missing
    ):
        """The cycle's plan and this pass share one check over the build on disk (`run_m1_gates.gates_only_base`), so the cycle never plans a rerun this pass refuses over that build and then plans it again on every pass. The build here has no manual_pins_summary.json or Stage A record, because this pass writes those, so a rerun that went red before writing them is planned again instead of a table build."""
        ran = self._reuse(monkeypatch, None if missing == "stamp" else {"ss06": "decision"})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        if missing == "font":
            (tmp_path / "M1.otf").unlink()
        elif missing == "glyph-facts":
            (tmp_path / glyph_facts.FILE_NAME).unlink()
        elif missing == "summary":
            (tmp_path / "pipeline_summary.json").unlink()
        elif missing == "joins":

            def refuse(path):
                raise OSError("truncated")

            monkeypatch.setattr(run_m1.table_module, "read_join_tsv", refuse)
        assert ac.m1_gates_only_base_present() is (missing is None)
        self._green(monkeypatch, tmp_path)
        if missing is None:
            self._gates(monkeypatch, ran)
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        else:
            with pytest.raises(SystemExit):
                run_m1_gates.run_gates_only(out_dir=tmp_path)

    def test_it_runs_the_defect_gate_then_the_pins(self, monkeypatch, tmp_path, capsys):
        """The defect gate runs first because its errors stop the pass before any pin is replayed. The Stage A values are captured before anything else and checked again after the pin gate, where the record they go into is written."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(
            monkeypatch,
            tmp_path,
            files={"rebuild/m1-contact-allow.yaml": "after"},
            prior={"rebuild/m1-contact-allow.yaml": "before"},
        )
        self._gates(monkeypatch, ran)
        run_m1_gates.main(["--gates-only"])
        assert ran == ["stage_a", "defects", "pins", "stage_a"]
        assert fingerprint.read_stage_a(tmp_path) == STAGE_A
        opened, closed = _phases(capsys.readouterr().out)
        assert opened == ["defect_gates", "run_manual_pin_gate"]
        assert set(opened) <= set(closed)

    @pytest.mark.parametrize("cycle", [False, True])
    def test_a_refused_pass_files_why_and_clears_a_matching_green_unless_a_cycle_owns_it(
        self, monkeypatch, tmp_path, capsys, cycle
    ):
        """A refusal before any gate (here tables the runes have outgrown) exits with a message that names the problem, not a traceback. Run by hand, the pass records that message as a `REFUSED` red check line and clears the green over the same content, as the cycle does for a red child, so no later `--gates-only` pass stands on it. A cycle's child prints the same red on the `[check]` line the cycle files (`artifact_cycle._reported_red`) and leaves the journal and the green to the cycle."""
        ran = self._reuse(monkeypatch, None)
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, prior={}, prior_key="fp-now")
        green = store.read_bytes()
        self._gates(monkeypatch, ran)
        if cycle:
            monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
        with pytest.raises(SystemExit) as error:
            run_m1_gates.main(["--gates-only"])
        reason = str(error.value)
        assert "--gates-only re-runs the gates over a build, it does not make one" in reason
        status = ct.REFUSED_STATUS
        if not cycle:
            assert [(check["outcome"], check["status"], check["failures"]) for check in _checks()] == [
                ("red", status, [reason])
            ]
            assert ac.read_green_record(store) is None
            return
        assert _checks() == []
        assert store.read_bytes() == green
        reported = ac._reported_red(capsys.readouterr().out, "run_m1")
        assert reported is not None
        assert (reported.status, reported.failures) == (status, [reason])

    @pytest.mark.parametrize("cycle", [False, True])
    def test_an_exception_past_the_passs_handlers_is_filed_and_clears_a_matching_green_unless_a_cycle_owns_it(
        self, monkeypatch, tmp_path, capsys, cycle
    ):
        """A contact allow-list entry with no signature makes the defect gate raise KeyError, which no handler in the pass catches. `main` records it as a FAILED red naming the exception (`run_m1_gates._recording_failure`) before it propagates with its traceback: by hand in the journal, clearing the green over the same content, and under a cycle on the `[check]` line the cycle files."""
        allow = tmp_path / "m1-contact-allow.yaml"
        allow.write_text("- why: no signature\n")
        monkeypatch.setattr(run_m1, "CONTACT_ALLOW_YAML", allow)
        run_defect_gates = run_m1._run_defect_gates
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        monkeypatch.setattr(run_m1, "_run_defect_gates", run_defect_gates)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, prior={}, prior_key="fp-now")
        green = store.read_bytes()
        if cycle:
            monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
        with pytest.raises(KeyError):
            run_m1_gates.main(["--gates-only"])
        red = ("FAILED", ["KeyError: 'signature'"])
        if not cycle:
            assert [(check["outcome"], check["status"], check["failures"]) for check in _checks()] == [
                ("red", *red)
            ]
            assert ac.read_green_record(store) is None
            return
        assert _checks() == []
        assert store.read_bytes() == green
        reported = ac._reported_red(capsys.readouterr().out, "run_m1")
        assert reported is not None
        assert (reported.status, reported.failures) == red

    def test_it_rewrites_only_the_defect_fields_of_the_builds_summary(self, monkeypatch, tmp_path):
        """The defect gate writes its result into the summary the build left, so the evaluator reads this pass's defect result, not the one from before the bless. The rest of the summary belongs to the build and is left as it was, since this pass compiles no font and counts no GSUB rule. A second pass whose defect fields come out the same leaves the file in place, keeping its inode and its modification time, here set long ago so that any write would move it."""
        report = defects.DefectReport(
            flags=[defects.Defect("W-CONTACT", "qsAh~qsBay", "grazes")],
            unused_records=["qsZoo", "qsAh"],
            waiting_on_unmigrated=["qsNo"],
            notes=["blessed one signature"],
        )
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran, report=report)
        self._summary(
            tmp_path,
            settled_cell_glyphs=12,
            defect_errors=["E-CONTACT qsAh~qsBay: ink collision"],
            notes=["what the build said"],
        )
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran)
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert json.loads((tmp_path / "pipeline_summary.json").read_text()) == {
            "gsub_rule_count": 7,
            "font": "M1.otf",
            "settled_cell_glyphs": 12,
            "defect_errors": [],
            "notes": ["blessed one signature"],
            "defect_flags": ["W-CONTACT qsAh~qsBay: grazes"],
            "unused_records": ["qsAh", "qsZoo"],
            "waiting_on_unmigrated": ["qsNo"],
        }
        summary = tmp_path / "pipeline_summary.json"
        os.utime(summary, ns=(LONG_AGO_NS, LONG_AGO_NS))
        inode = summary.stat().st_ino
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert (summary.stat().st_ino, summary.stat().st_mtime_ns) == (inode, LONG_AGO_NS)

    def test_a_defect_error_exits_red_before_the_pin_gate_and_clears_the_green(self, monkeypatch, tmp_path):
        """A bless that does not cover a contact fails here under the defect gate with the failure the cycle files for a full build that stopped there (`artifact_cycle._defect_gate_failure`), which names the first error, and stops the pass before any pin is replayed. The pass clears the green its key matches, because the failure contradicts it."""
        report = defects.DefectReport(errors=[defects.Defect("E-CONTACT", "qsAh~qsBay", "ink collision")])
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran, report=report)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, prior={}, prior_key="fp-now")
        monkeypatch.setattr(
            run_m1_gates,
            "run_manual_pin_gate",
            lambda **kwargs: pytest.fail("the pin gate ran behind a defect error"),
        )
        with pytest.raises(SystemExit) as error:
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        failure = "1 defect-gate error(s): E-CONTACT qsAh~qsBay: ink collision"
        assert str(error.value) == f"{failure}; see pipeline_summary.json"
        assert ac.read_green_record(store) is None
        checks = _checks()
        assert len(checks) == 1
        assert checks[0]["outcome"] == "red"
        assert checks[0]["failures"] == [failure]
        assert json.loads((tmp_path / "pipeline_summary.json").read_text())["defect_errors"] == [
            "E-CONTACT qsAh~qsBay: ink collision"
        ]

    def test_a_comparison_side_diff_records_the_green_the_next_cycle_skips_on(self, monkeypatch, tmp_path):
        """The prior green shows that a completed build over every build-side input its key names passed, the stamp shows the tables on disk were stamped from the current sources, and this pass re-runs the gates the changed inputs feed. Together these let the pass record a green over the new inputs, so the cycle after a contact allow-list bless skips run_m1. The two checks are necessary conditions for the tables and M1.otf on disk to be the green build's, not sufficient ones (`run_m1_gates.run_gates_only` names the gaps). Of the runs behind the pass, only the prior green vouches for the build it stands on: a failed run never vouches for the artifacts it left."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        current = {"rebuild/m1-contact-allow.yaml": "after", "uv.lock": "pinned"}
        store = self._green(
            monkeypatch,
            tmp_path,
            files=current,
            prior={"rebuild/m1-contact-allow.yaml": "before", "uv.lock": "pinned"},
        )
        self._gates(monkeypatch, ran)
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        record = ac.read_green_record(store)
        assert record is not None
        assert record["fingerprint"] == "fp-now"
        assert record["files"] == current

    @pytest.mark.parametrize("replayed", [4, 3])
    @pytest.mark.parametrize("prior_key", ["fp-now", "fp-other"])
    def test_a_cycle_gates_only_child_publishes_no_checks_or_greens(
        self, monkeypatch, tmp_path, replayed, prior_key
    ):
        """The child still checks the reused build and comparison-side eligibility, while the cycle owns publication and invalidation. Even an eligible green leaves the prior record byte-identical, and a red leaves a matching record for the parent to clear."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(
            monkeypatch,
            tmp_path,
            files={"rebuild/m1-contact-allow.yaml": "after", "uv.lock": "pinned"},
            prior={"rebuild/m1-contact-allow.yaml": "before", "uv.lock": "pinned"},
            prior_key=prior_key,
        )
        before = store.read_bytes()
        self._gates(monkeypatch, ran, replayed=replayed)
        monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
        if replayed < 4:
            with pytest.raises(SystemExit, match="replayed 3 of 4 pins"):
                run_m1_gates.main(["--gates-only"])
        else:
            run_m1_gates.main(["--gates-only"])
        assert store.read_bytes() == before
        assert _checks() == []

    def test_no_prior_green_records_nothing_and_says_why(self, monkeypatch, tmp_path, capsys):
        """A green here is a claim about artifacts this pass did not build, and without a prior green nothing shows that a completed build over every build-side input passed, and a failed run never vouches for the artifacts it left. The pass still runs and records its check line, which reports only how this invocation came out and does not let a later pass skip work. For the same reason it writes no Stage A record, which a corpus build would copy as the inputs of artifacts nothing green vouches for."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, files={"uv.lock": "pinned"})
        self._gates(monkeypatch, ran)
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert not store.exists()
        assert "there is no prior green M1 build for this pass to stand on" in capsys.readouterr().out
        assert [check["outcome"] for check in _checks()] == ["green"]
        assert fingerprint.read_stage_a(tmp_path) is None

    @pytest.mark.parametrize("build_side", ["uv.lock", "glyph_data/runes/qsWay.yaml"])
    def test_a_build_side_input_among_the_moved_records_nothing_and_names_it(
        self, monkeypatch, tmp_path, capsys, build_side
    ):
        """A build-side input that moved since the last green keeps the pass from recording one, and from writing the Stage A record. uv.lock is outside the tables' stamp and pins fontTools and uharfbuzz, so a bump there can change the compiled font's bytes and how HarfBuzz shapes them, and a font built by another toolchain must not be reused. A rune is inside the stamp, so tables stamped with the moved rune come from a build over it that left no green, such as one that went red at a comparison gate, and a failed run never vouches for the artifacts it left. The printed line names the input and holds in both cases, so it is clear without a diff that a full build is needed."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(
            monkeypatch,
            tmp_path,
            files={build_side: "after", "rebuild/m1-contact-allow.yaml": "after"},
            prior={build_side: "before", "rebuild/m1-contact-allow.yaml": "before"},
        )
        self._gates(monkeypatch, ran)
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert (
            "run_m1: green, but this pass recorded no green — these build-side inputs moved since the last green M1 build, and a failed run never vouches for the artifacts it left, so only a full build can record a green over them: "
            + build_side
            in capsys.readouterr().out
        )
        record = ac.read_green_record(store)
        assert record is not None
        assert record["fingerprint"] == "fp-prior"
        assert fingerprint.read_stage_a(tmp_path) is None

    def test_inputs_that_never_moved_leave_the_standing_green_where_it_is(
        self, monkeypatch, tmp_path, capsys
    ):
        """When nothing has changed, the existing green already covers these inputs, so the pass leaves it alone instead of rewriting it with a later time, and writes the Stage A record back over it."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        standing = {"rebuild/m1-contact-allow.yaml": "unchanged"}
        store = self._green(monkeypatch, tmp_path, files=standing, prior=standing)
        self._gates(monkeypatch, ran)
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert "nothing has moved since the last green M1 build" in capsys.readouterr().out
        record = ac.read_green_record(store)
        assert record is not None
        assert record["fingerprint"] == "fp-prior"
        assert fingerprint.read_stage_a(tmp_path) == STAGE_A

    def test_it_files_the_gates_only_reruns_result(self, monkeypatch, tmp_path):
        """The pass takes its result from the same evaluator the cycle uses, records that result as a check line with no `run` field, and exits accordingly."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran)
        run_m1_gates.main(["--gates-only"])
        checks = _checks()
        assert len(checks) == 1
        assert checks[0]["check"] == "run_m1"
        assert checks[0]["outcome"] == "green"
        assert "run" not in checks[0]

    def test_a_pin_gate_that_refuses_the_build_files_a_red_and_clears_the_green(self, monkeypatch, tmp_path):
        """A Manual-pin gate that replays only 3 of the 4 pins in scope fails the pass, which records a red check line carrying its message and clears the green its key matches. Leaving that green would let the next cycle skip run_m1 on a build the pin gate failed."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, prior={}, prior_key="fp-now")
        self._gates(monkeypatch, ran, replayed=3)
        with pytest.raises(SystemExit):
            run_m1_gates.main(["--gates-only"])
        assert ac.read_green_record(store) is None
        checks = _checks()
        assert len(checks) == 1
        assert checks[0]["outcome"] == "red"
        assert "replayed 3 of 4 pins" in checks[0]["failures"][0]

    def test_a_vacuous_pin_gate_exits_with_its_message(self, monkeypatch, tmp_path):
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran, replayed=3)
        with pytest.raises(SystemExit) as error:
            run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert "replayed 3 of 4 pins" in str(error.value)

    @pytest.mark.parametrize(
        ("stop", "raised"),
        [
            ("defect", SystemExit),
            ("pins", SystemExit),
            ("error", ValueError),
            ("interrupt", KeyboardInterrupt),
        ],
    )
    def test_a_pass_that_stops_before_its_gates_pass_leaves_no_stage_a_record(
        self, monkeypatch, tmp_path, stop, raised
    ):
        """After a contact allow-list edit, a pass that stops at a red defect or Manual-pin gate, on an exception from the pin gate, or at Ctrl-C in it leaves the previous green, which is for other inputs. It leaves no Stage A record beside the summaries it rewrote, neither the previous pass's nor one for the edited sources, so the next cycle does not skip over them (`artifact_cycle.m1_stage_a_current`) until a pass whose gates pass writes the record."""
        out = tmp_path / "rebuild" / "out" / "m1"
        out.mkdir(parents=True)
        (out / fingerprint.STAGE_A_FILENAME).write_text(
            json.dumps({"format": fingerprint.FORMAT, **{name: "before" for name in STAGE_A}})
        )
        report = defects.DefectReport(errors=[defects.Defect("E-CONTACT", "qsAh~qsBay", "ink collision")])
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, out, ran, report=report if stop == "defect" else None)
        self._summary(out)
        store = self._green(
            monkeypatch,
            tmp_path,
            files={"rebuild/m1-contact-allow.yaml": "after"},
            prior={"rebuild/m1-contact-allow.yaml": "before"},
        )
        green = store.read_bytes()
        self._gates(monkeypatch, ran, replayed=3 if stop == "pins" else 4)
        if raised is not SystemExit:

            def run_manual_pin_gate(out_dir, spec):
                raise raised("the pin gate stopped")

            monkeypatch.setattr(run_m1_gates, "run_manual_pin_gate", run_manual_pin_gate)
        with pytest.raises(raised):
            run_m1_gates.run_gates_only(out_dir=out)
        assert fingerprint.read_stage_a(out) is None
        assert store.read_bytes() == green

    def test_the_stage_a_record_is_gone_when_the_pass_first_writes_its_summary(self, monkeypatch, tmp_path):
        """The pass's first write under rebuild/out/m1 is the defect fields of pipeline_summary.json, so the Stage A record is already gone when that write starts, and a pass stopped in it or just after it (a Ctrl-C, a full disk) leaves no record to vouch for what it rewrote."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran)
        (tmp_path / fingerprint.STAGE_A_FILENAME).write_text(
            json.dumps({"format": fingerprint.FORMAT, **{name: "before" for name in STAGE_A}})
        )
        summary = tmp_path / "pipeline_summary.json"
        write_bytes = Path.write_bytes
        at_summary_write = []

        def watched(path, *args, **kwargs):
            if path == summary:
                at_summary_write.append(fingerprint.read_stage_a(tmp_path))
            return write_bytes(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_bytes", watched)
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert at_summary_write == [None]

    def test_a_stage_a_input_edited_while_the_pass_ran_leaves_no_stage_a_record(
        self, monkeypatch, tmp_path, capsys
    ):
        """An edit to a Stage A input after the pass captured its values may have reached the gate summaries, so a record of the captured values could match the sources again once the edit is reverted, beside summaries of the edit. The pass warns and writes no record, though its gates passed."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(
            monkeypatch,
            tmp_path,
            files={"rebuild/m1-contact-allow.yaml": "after"},
            prior={"rebuild/m1-contact-allow.yaml": "before"},
        )
        self._gates(monkeypatch, ran)
        values = iter([STAGE_A, {**STAGE_A, "data": "edited"}])
        monkeypatch.setattr(run_m1.fingerprint, "stage_a", lambda repo_root: dict(next(values)))
        run_m1_gates.run_gates_only(out_dir=tmp_path)
        assert fingerprint.read_stage_a(tmp_path) is None
        assert "Stage A record not written" in capsys.readouterr().out


class TestKernOnly:
    """`--kern-only` recompiles the font over the build on disk after an edit that moved only kern blocks. It stands on a Stage A record that matches the sources, which it carries forward and never writes, and it runs the glyph chain beside rebuild/out/m1, replacing the build's files only once read-back has passed."""

    def _world(self, monkeypatch, tmp_path, *, stage_a=True):
        """A build under `tmp_path / "m1"`: a font, the two summaries, and a Stage A record matching the sources when `stage_a` is set. The green before the kern edit is recorded, and the sources now differ from it in one kern line."""
        out = tmp_path / "m1"
        out.mkdir()
        monkeypatch.setattr(run_m1, "OUT_DIR", out)
        monkeypatch.setattr(run_m1.fingerprint, "stage_a", lambda repo_root: dict(STAGE_A))
        if stage_a:
            fingerprint.write_stage_a(out, STAGE_A)
        files = {name: out / f"{name}_summary.json" for name in cycle_paths.M1_SUMMARY_FILES}
        monkeypatch.setattr(cycle_paths, "M1_SUMMARY_FILES", files)
        (out / "M1.otf").write_bytes(b"old font")
        (out / "kern-classes.json").write_text("old classes\n")
        files["pipeline"].write_text(json.dumps({"defect_errors": [], "font": str(out / "M1.otf")}) + "\n")
        files["manual_pins"].write_text(json.dumps({"pass": True, "pins_in_scope": 4, "replayed": 4}))
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: "fp")
        monkeypatch.setattr(
            run_m1_gates,
            "gates_only_base",
            lambda out_dir, inputs: run_m1_gates.GatesOnlyBase(tables={}, pipeline_summary={}),
        )
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
        monkeypatch.setattr(run_m1, "head_commit", lambda: None)
        monkeypatch.setattr(run_m1, "_collect_glyph_chain", lambda: None)
        store = tmp_path / "run-m1-green.json"
        monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
        monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-now")
        monkeypatch.setattr(
            ac, "run_m1_skip_files", lambda root=None: {"glyph_data/runes/qsNo.yaml#kern": "k2"}
        )
        ac.record_green(store, "fp-prior", files={"glyph_data/runes/qsNo.yaml#kern": "k1"})
        return out, files, store

    def _chain(self, monkeypatch, ran, *, error=None):
        """Stubs the glyph chain: it writes a new font, kern assignment and summary into the directory it is handed, then raises `error` when one is given, as a kern error or a read-back divergence would after the compile."""

        def chain(spec, tables, out_dir, commit):
            ran.append(out_dir)
            (out_dir / "M1.otf").write_bytes(b"new font")
            (out_dir / "kern-classes.json").write_text("new classes\n")
            if error is not None:
                raise error
            summary = {"defect_errors": [], "font": str(out_dir / "M1.otf")}
            (out_dir / "pipeline_summary.json").write_text(json.dumps(summary))
            return summary

        monkeypatch.setattr(run_m1, "_run_glyph_chain", chain)

    def _snapshot(self, out):
        return {path.name: path.read_bytes() for path in sorted(out.iterdir())}

    @pytest.mark.parametrize("why", ["absent", "other-sources"])
    def test_it_refuses_a_build_whose_stage_a_record_does_not_stand_and_writes_none(
        self, monkeypatch, tmp_path, why
    ):
        """After a gates-only pass whose green and record were declined (`absent`), or a record written over sources since edited (`other-sources`), the summaries beside it are not known to be a passing run's over these inputs. The pass refuses before it reads anything else, writes no record, and runs no chain."""
        out, _files, store = self._world(monkeypatch, tmp_path, stage_a=why == "other-sources")
        if why == "other-sources":
            fingerprint.write_stage_a(out, {**STAGE_A, "data": "before the edit"})
        ran: list = []
        self._chain(monkeypatch, ran)
        before = self._snapshot(out)
        with pytest.raises(SystemExit, match="Stage A record"):
            run_m1.kern_only_run(started=time.perf_counter())
        assert ran == []
        assert self._snapshot(out) == before
        record = ac.read_green_record(store)
        assert record is not None and record["fingerprint"] == "fp-prior"

    @pytest.mark.parametrize(
        "error",
        [
            run_m1.kern.KernError([("E-KERN-UNMATCHED", "qsNo.yaml:kern[0] matches no break")]),
            run_m1.readback.ReadbackError("1 read-back divergence(s)"),
            run_m1.emit_gpos.KernPlanError("qsTea.half has an entry anchor"),
        ],
        ids=["kern-error", "readback", "kern-plan"],
    )
    def test_a_failed_chain_changes_nothing_under_the_build_and_leaves_the_next_pass_a_kern_only_rebuild(
        self, monkeypatch, tmp_path, error
    ):
        """The chain runs beside the build, so a failure after the compile leaves the prior font beside the summaries that describe it and the Stage A record still matching: the next cycle plans a kern-only rebuild again once the record is fixed. The red keeps the prior green, which is over other content."""
        out, _files, store = self._world(monkeypatch, tmp_path)
        ran: list = []
        self._chain(monkeypatch, ran, error=error)
        before = self._snapshot(out)
        with pytest.raises(SystemExit):
            run_m1.kern_only_run(started=time.perf_counter())
        assert ran == [run_m1.kern_only_staging()]
        assert self._snapshot(out) == before
        assert fingerprint.read_stage_a(out) == STAGE_A
        record = ac.read_green_record(store)
        assert record is not None and record["fingerprint"] == "fp-prior"

    def test_a_passing_chain_replaces_the_build_s_files_and_carries_the_record_forward(
        self, monkeypatch, tmp_path
    ):
        out, files, store = self._world(monkeypatch, tmp_path)
        record_bytes = (out / fingerprint.STAGE_A_FILENAME).read_bytes()
        ran: list = []
        self._chain(monkeypatch, ran)
        run_m1.kern_only_run(started=time.perf_counter())
        assert (out / "M1.otf").read_bytes() == b"new font"
        assert (out / "kern-classes.json").read_text() == "new classes\n"
        assert json.loads(files["pipeline"].read_text())["font"] == str(out / "M1.otf")
        assert (out / fingerprint.STAGE_A_FILENAME).read_bytes() == record_bytes
        assert not run_m1.kern_only_staging().exists()
        record = ac.read_green_record(store)
        assert record is not None and record["fingerprint"] == "fp-now"

    def test_a_passing_chain_leaves_the_kern_inventory_in_place(self, monkeypatch, tmp_path):
        """A kern-only pass hands the glyph chain no stamp for the kern inventory, which depends on no kern block (rebuild/pipeline/kern_inventory.py), so the chain writes none into the staging directory, and the publish, which moves only what the chain wrote, leaves the build's `kern-inventory.json` as it was: the same bytes and the same file."""
        out, _files, _store = self._world(monkeypatch, tmp_path)
        inventory = out / run_m1.kern_inventory.KERN_INVENTORY_JSON
        inventory.write_text('{"format": "ams-m1-kern-inventory/1"}\n')
        before = inventory.stat()
        ran: list = []
        self._chain(monkeypatch, ran)
        run_m1.kern_only_run(started=time.perf_counter())
        assert (out / "M1.otf").read_bytes() == b"new font"
        after = inventory.stat()
        assert inventory.read_text() == '{"format": "ams-m1-kern-inventory/1"}\n'
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    def test_a_stage_a_input_edited_while_it_ran_publishes_nothing(self, monkeypatch, tmp_path):
        out, _files, _store = self._world(monkeypatch, tmp_path)
        ran: list = []
        self._chain(monkeypatch, ran)
        calls = iter([dict(STAGE_A), {**STAGE_A, "data": "edited mid-run"}])
        monkeypatch.setattr(run_m1.fingerprint, "stage_a", lambda repo_root: next(calls))
        before = self._snapshot(out)
        with pytest.raises(SystemExit, match="changed while --kern-only ran"):
            run_m1.kern_only_run(started=time.perf_counter())
        assert self._snapshot(out) == before
