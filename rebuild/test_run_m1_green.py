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


def _stub_full_run(monkeypatch, *, defect_errors=(), boundary=True, pins=True, oracle_pass=False):
    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda repo_root: False)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
    monkeypatch.setattr(
        run_m1, "run", lambda spec, jobs, inputs: {"defect_errors": list(defect_errors), "notes": []}
    )
    monkeypatch.setattr(run_m1, "run_boundary_gate", lambda spec, jobs: {"pass": boundary, "divergences": 0})
    monkeypatch.setattr(run_m1, "run_manual_pin_gate", lambda spec: {"pass": pins, "disagreements": []})
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
        run_m1, "run", lambda spec, jobs, inputs: events.append("run") or {"defect_errors": [], "notes": []}
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


def test_a_failed_boundary_gate_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, boundary=False, oracle_pass=True)
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_conform_only_records_its_own_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=5: "fp-conform")
    monkeypatch.setattr(
        run_m1,
        "run_font_conformance",
        lambda max_length, jobs: {
            "pass": True,
            "divergences": 0,
            "uncovered_rules": 0,
            "uncovered_transitions": 0,
        },
    )
    run_m1.main(["--conform-only"])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-conform"


def test_conform_only_dead_rules_record_no_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=5: "fp-conform")
    monkeypatch.setattr(
        run_m1,
        "run_font_conformance",
        lambda max_length, jobs: {
            "pass": True,
            "divergences": 0,
            "uncovered_rules": 3,
            "uncovered_transitions": 0,
        },
    )
    run_m1.main(["--conform-only"])
    assert ac.read_green_record(store) is None


def test_the_conform_horizon_default_matches_the_cycle_driver(monkeypatch, tmp_path):
    """The horizon is part of the conform green's key, so if run_m1's own default ever drifts from the driver's, an interactive sweep would record a green no cycle can ever match."""
    store = tmp_path / "conform-green.json"
    swept = []
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=5: "fp-conform")

    def fake_sweep(max_length, jobs):
        swept.append(max_length)
        return {"pass": True, "divergences": 0, "uncovered_rules": 0, "uncovered_transitions": 0}

    monkeypatch.setattr(run_m1, "run_font_conformance", fake_sweep)
    run_m1.main(["--conform-only"])
    assert swept == [ac.CONFORM_HORIZON_DEFAULT]
