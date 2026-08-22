"""Interactive run_m1 and --conform-only record the same last-green files the artifact cycle skips on, so a fix verified by hand is not re-verified by the next cycle. The gate verdicts come from artifact_cycle's own evaluators, never from run_m1's exit code, which is nonzero whenever the oracle carries UNMATCHED rows — the normal mid-migration state."""

import pytest

from rebuild.pipeline import run_m1
from rebuild.tools import artifact_cycle as ac


@pytest.fixture
def green_store(tmp_path):
    return tmp_path / "run-m1-green.json"


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
    monkeypatch.setattr(run_m1.sys, "stdout", FlushRecorder("stdout", events))
    monkeypatch.setattr(run_m1.sys, "stderr", FlushRecorder("stderr", events))

    def exit_(status):
        events.append(f"exit {status}")
        raise HardExit

    monkeypatch.setattr(run_m1.os, "_exit", exit_)
    with pytest.raises(HardExit):
        run_m1._hard_exit(7)
    assert events == ["flush stdout", "flush stderr", "exit 7"]


def test_cli_flushes_output_before_preserving_string_system_exit(monkeypatch):
    events = []
    monkeypatch.setattr(run_m1.sys, "stdout", FlushRecorder("stdout", events))
    monkeypatch.setattr(run_m1.sys, "stderr", FlushRecorder("stderr", events))

    def main():
        print("summary")
        raise SystemExit("expected failure")

    def exit_(status):
        events.append(f"exit {status}")
        raise HardExit(status)

    monkeypatch.setattr(run_m1, "main", main)
    monkeypatch.setattr(run_m1.os, "_exit", exit_)
    with pytest.raises(HardExit, match="1"):
        run_m1._run_cli()
    assert events.index("flush stdout") < events.index("write stderr expected failure")
    assert events[-3:] == ["flush stdout", "flush stderr", "exit 1"]


def test_cli_hard_exits_zero_after_a_normal_return(monkeypatch):
    monkeypatch.setattr(run_m1, "main", lambda: None)
    monkeypatch.setattr(run_m1, "_hard_exit", lambda status: (_ for _ in ()).throw(HardExit(status)))
    with pytest.raises(HardExit, match="0"):
        run_m1._run_cli()


def test_cli_leaves_unexpected_exceptions_to_the_interpreter(monkeypatch):
    monkeypatch.setattr(run_m1, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(run_m1, "_hard_exit", lambda _status: pytest.fail("must not hard-exit"))
    with pytest.raises(RuntimeError, match="boom"):
        run_m1._run_cli()


def test_records_when_the_key_holds_across_the_run(green_store):
    run_m1._settle_green(green_store, "fp-1", True, _keys(["fp-1"]), "run_m1")
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_records_nothing_when_the_inputs_moved_mid_run(green_store, capsys):
    run_m1._settle_green(green_store, "fp-1", True, _keys(["fp-2"]), "run_m1")
    assert ac.read_green_record(green_store) is None
    assert "changed while it ran" in capsys.readouterr().out


def test_red_deletes_a_contradicted_record(green_store):
    ac.record_green(green_store, "fp-1")
    run_m1._settle_green(green_store, "fp-1", False, _keys([]), "run_m1")
    assert ac.read_green_record(green_store) is None


def test_red_leaves_a_record_for_other_content_alone(green_store):
    ac.record_green(green_store, "fp-other")
    run_m1._settle_green(green_store, "fp-1", False, _keys([]), "run_m1")
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-other"


def _stub_full_run(monkeypatch, *, defect_errors=(), pins=True, pins_in_scope=143, oracle_pass=False):
    monkeypatch.setattr(run_m1.conform, "unaliased_subset_names", lambda subset_dir, alias_path: {})
    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda repo_root: False)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None: {
            "defect_errors": list(defect_errors),
            "notes": [],
        },
    )
    monkeypatch.setattr(
        run_m1,
        "run_manual_pin_gate",
        lambda spec: {
            "pass": pins,
            "disagreements": [],
            "pins_in_scope": pins_in_scope,
            "replayed": pins_in_scope,
        },
    )
    monkeypatch.setattr(
        run_m1,
        "run_oracle",
        lambda spec, jobs: {"pass": oracle_pass, "unmatched": 19837, "multi_matched": 0},
    )


def test_main_refreshes_the_baseline_subset_before_anything_reads_it(monkeypatch, tmp_path, capsys):
    """The five-hand-updates trap, closed: run_m1 ensures the subset tables are current before the pipeline and its oracle run, so an M1_ALPHABET edit can no longer feed the oracle stale tables. The fingerprint stub is order-sensitive — it answers differently before and after the ensure — so the green below records only because the key snapshot happened after the refilter; moving the ensure below the snapshot mismatches the keys and fails this test."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    state = {"ensured": False}
    monkeypatch.setattr(
        ac,
        "run_m1_skip_fingerprint",
        lambda root=None: "fp-post-refilter" if state["ensured"] else "fp-pre-refilter",
    )
    _stub_full_run(monkeypatch, oracle_pass=True)
    events = []

    def ensure(repo_root):
        state["ensured"] = True
        events.append("subset")
        return True

    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", ensure)
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None: events.append("run") or {"defect_errors": [], "notes": []},
    )
    monkeypatch.setattr(
        run_m1,
        "run_oracle",
        lambda spec, jobs: events.append("oracle") or {"pass": True, "unmatched": 0, "multi_matched": 0},
    )
    run_m1.main([])
    assert events == ["subset", "run", "oracle"]
    assert "[t] baseline_subset" in capsys.readouterr().out
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-post-refilter"


def test_unmatched_oracle_rows_still_record_a_green(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, oracle_pass=False)
    with pytest.raises(SystemExit):
        run_m1.main([])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-live"


def test_a_defect_gate_failure_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, defect_errors=["qsAh: contact"], oracle_pass=True)
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_a_failed_manual_pin_gate_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, pins=False, oracle_pass=True)
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_a_manual_pin_gate_with_nothing_in_scope_clears_the_record(monkeypatch, tmp_path):
    """The vacuous pass: `pass` is `not disagreements`, so a gate that replayed no pin at all reports green. run_m1 requires the scope too, so an empty replay fails the build rather than certifying it."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, pins_in_scope=0, oracle_pass=True)
    with pytest.raises(SystemExit) as error:
        run_m1.main([])
    assert "no pins in scope" in str(error.value)
    assert ac.read_green_record(store) is None


def test_conform_only_records_its_own_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})
    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": True, "divergences": 0}
    )
    run_m1.main(["--conform-only"])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-conform"


def test_conform_only_divergences_record_no_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": False, "divergences": 3}
    )
    with pytest.raises(SystemExit):
        run_m1.main(["--conform-only"])
    assert ac.read_green_record(store) is None


def test_the_conform_horizon_default_matches_the_cycle_driver(monkeypatch, tmp_path):
    """The horizon is part of the conform green's key, so if run_m1's own default ever drifts from the driver's, an interactive sweep would record a green no cycle can ever match."""
    store = tmp_path / "conform-green.json"
    swept = []
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})

    def fake_sweep(max_length, jobs):
        swept.append(max_length)
        return {"pass": True, "divergences": 0}

    monkeypatch.setattr(run_m1, "run_font_conformance", fake_sweep)
    run_m1.main(["--conform-only"])
    assert swept == [ac.CONFORM_HORIZON_DEFAULT]


class TestGatesOnly:
    """The cheap re-adjudication entry point: the Manual-pin gate and the oracle over the build already on disk. What it must not do is claim a build's green, and what it must refuse is a font the runes have outgrown."""

    def _reuse(self, monkeypatch, tables):
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: "fp")
        monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: tables)

    def test_it_refuses_tables_the_runes_have_outgrown(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, None)
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "it does not make one" in str(error.value)

    def test_it_refuses_a_missing_font(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, {})
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "no compiled font" in str(error.value)

    def test_it_runs_both_gates_and_records_no_green(self, monkeypatch, tmp_path, capsys):
        self._reuse(monkeypatch, {})
        monkeypatch.setattr(run_m1, "OUT_DIR", tmp_path)
        (tmp_path / "M1.otf").write_bytes(b"font")
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
        ran = []
        monkeypatch.setattr(
            run_m1,
            "run_manual_pin_gate",
            lambda out_dir, spec: ran.append("pins")
            or {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": 4},
        )
        monkeypatch.setattr(
            run_m1,
            "run_oracle",
            lambda out_dir, spec, jobs: ran.append("oracle")
            or {"pass": True, "unmatched": 0, "multi_matched": 0},
        )
        monkeypatch.setattr(
            run_m1, "_settle_green", lambda *args, **kwargs: pytest.fail("--gates-only recorded a green")
        )
        run_m1.main(["--gates-only", "--jobs", "6"])
        assert ran == ["pins", "oracle"]
        assert "[t] run_oracle" in capsys.readouterr().out

    def test_a_vacuous_pin_gate_stops_it_before_the_oracle(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, {})
        (tmp_path / "M1.otf").write_bytes(b"font")
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
        monkeypatch.setattr(
            run_m1,
            "run_manual_pin_gate",
            lambda out_dir, spec: {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": 3},
        )
        monkeypatch.setattr(
            run_m1, "run_oracle", lambda **kwargs: pytest.fail("the oracle ran behind a failed pin gate")
        )
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "replayed 3 of 4 pins" in str(error.value)
