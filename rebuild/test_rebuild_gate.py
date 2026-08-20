"""The rebuild suite's self-skip wrapper: skip on a matching green record, run and judge through the cycle's failure classifier otherwise, and record only what the recordable flag allows."""

import json

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import rebuild_gate as rg

BASELINE_STDOUT = "\n".join(f"FAILED {test_id}" for test_id in sorted(ac.BASELINE_REBUILD_FAILURES))


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    store = tmp_path / "rebuild-gate-green.json"
    monkeypatch.setattr(ac, "REBUILD_GATE_GREEN", store)
    monkeypatch.setattr(rg, "REBUILD_GATE_GREEN", store)
    return store


def _fingerprints(monkeypatch, values):
    calls = iter(values)
    monkeypatch.setattr(rg, "rebuild_gate_skip_fingerprint", lambda root: next(calls))


def _suite_stub(monkeypatch, returncode, stdout=""):
    spawned = []

    def fake_run():
        spawned.append(rg.REBUILD_PYTEST_ARGV)
        return returncode, stdout

    monkeypatch.setattr(rg, "_run_suite", fake_run)
    return spawned


def test_skips_without_spawning_when_the_record_matches(green_store, monkeypatch, capsys):
    ac.record_green(green_store, "fp-1")
    _fingerprints(monkeypatch, ["fp-1"])
    spawned = _suite_stub(monkeypatch, returncode=0)
    assert rg.main([]) == 0
    assert spawned == []
    assert "SKIPPED" in capsys.readouterr().out


def test_force_runs_despite_a_matching_record(green_store, monkeypatch):
    ac.record_green(green_store, "fp-1")
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned = _suite_stub(monkeypatch, returncode=0)
    assert rg.main(["--force"]) == 0
    assert spawned == [rg.REBUILD_PYTEST_ARGV]
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_clean_green_run_records_the_fingerprint(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    spawned = _suite_stub(monkeypatch, returncode=0)
    assert rg.main([]) == 0
    assert spawned == [rg.REBUILD_PYTEST_ARGV]
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-2"


def test_documented_baseline_failures_still_read_green_and_record(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    _suite_stub(monkeypatch, returncode=1, stdout=BASELINE_STDOUT)
    assert rg.main([]) == 0
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-2"
    assert "documented baseline" in capsys.readouterr().out


def test_hard_failure_propagates_the_exit_code_and_records_nothing(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["fp-2"])
    _suite_stub(monkeypatch, returncode=3, stdout="FAILED rebuild/test_settle.py::test_x")
    assert rg.main([]) == 3
    assert ac.read_green_record(green_store) is None
    assert "hard rebuild failure: rebuild/test_settle.py::test_x" in capsys.readouterr().out


def test_forced_hard_failure_deletes_a_contradicted_record(green_store, monkeypatch):
    ac.record_green(green_store, "fp-1")
    _fingerprints(monkeypatch, ["fp-1"])
    _suite_stub(monkeypatch, returncode=1, stdout="FAILED rebuild/test_settle.py::test_x")
    assert rg.main(["--force"]) == 1
    assert ac.read_green_record(green_store) is None


def test_hard_failure_keeps_a_record_for_a_different_closure(green_store, monkeypatch):
    ac.record_green(green_store, "fp-1")
    _fingerprints(monkeypatch, ["fp-2"])
    _suite_stub(monkeypatch, returncode=1, stdout="FAILED rebuild/test_settle.py::test_x")
    assert rg.main([]) == 1
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_nonzero_exit_with_no_parsed_lines_is_red(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["fp-2"])
    _suite_stub(monkeypatch, returncode=2, stdout="")
    assert rg.main([]) == 2
    assert ac.read_green_record(green_store) is None


def test_green_run_with_midrun_drift_records_nothing(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["fp-2", "fp-3"])
    _suite_stub(monkeypatch, returncode=0)
    assert rg.main([]) == 0
    assert ac.read_green_record(green_store) is None
    assert "changed while the suite ran" in capsys.readouterr().out


def test_runs_unconditionally_without_git(green_store, monkeypatch):
    _fingerprints(monkeypatch, [None])
    spawned = _suite_stub(monkeypatch, returncode=0)
    assert rg.main([]) == 0
    assert spawned == [rg.REBUILD_PYTEST_ARGV]
    assert ac.read_green_record(green_store) is None


def test_stale_record_format_never_matches(green_store, monkeypatch):
    green_store.write_text(json.dumps({"fingerprint": 42}))
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned = _suite_stub(monkeypatch, returncode=0)
    assert rg.main([]) == 0
    assert spawned == [rg.REBUILD_PYTEST_ARGV]
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"
