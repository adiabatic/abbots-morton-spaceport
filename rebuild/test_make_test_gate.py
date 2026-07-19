"""The `make test` self-skip wrapper: skip on a matching green record, run and record otherwise, and never leave a record a red or moved closure has contradicted."""

import json
from types import SimpleNamespace

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import make_test_gate as mtg


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    store = tmp_path / "make-test-green.json"
    monkeypatch.setattr(ac, "MAKE_TEST_GREEN", store)
    monkeypatch.setattr(mtg, "MAKE_TEST_GREEN", store)
    return store


def _fingerprints(monkeypatch, values):
    calls = iter(values)
    monkeypatch.setattr(mtg, "make_test_closure_fingerprint", lambda root: next(calls))


def _pytest_stub(monkeypatch, returncode):
    spawned = []

    def fake_run(argv, cwd):
        spawned.append(argv)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(mtg.subprocess, "run", fake_run)
    return spawned


def test_skips_without_spawning_when_the_record_matches(green_store, monkeypatch, capsys):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-1"])
    spawned = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == []
    assert "SKIPPED" in capsys.readouterr().out


def test_force_runs_despite_a_matching_record(green_store, monkeypatch):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main(["--force"]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert ac.read_make_test_green(green_store)["fingerprint"] == "fp-1"


def test_green_run_records_the_fingerprint(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    spawned = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert ac.read_make_test_green(green_store)["fingerprint"] == "fp-2"


def test_red_run_propagates_the_exit_code_and_records_nothing(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["fp-2"])
    _pytest_stub(monkeypatch, returncode=3)
    assert mtg.main([]) == 3
    assert ac.read_make_test_green(green_store) is None


def test_forced_red_run_deletes_a_contradicted_record(green_store, monkeypatch):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-1"])
    _pytest_stub(monkeypatch, returncode=1)
    assert mtg.main(["--force"]) == 1
    assert ac.read_make_test_green(green_store) is None


def test_red_run_keeps_a_record_for_a_different_closure(green_store, monkeypatch):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-2"])
    _pytest_stub(monkeypatch, returncode=1)
    assert mtg.main([]) == 1
    assert ac.read_make_test_green(green_store)["fingerprint"] == "fp-1"


def test_green_run_with_midrun_drift_records_nothing(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["fp-2", "fp-3"])
    _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert ac.read_make_test_green(green_store) is None
    assert "changed while the suite ran" in capsys.readouterr().out


def test_runs_unconditionally_without_git(green_store, monkeypatch):
    _fingerprints(monkeypatch, [None])
    spawned = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert ac.read_make_test_green(green_store) is None


def test_stale_record_format_never_matches(green_store, monkeypatch):
    green_store.write_text(json.dumps({"fingerprint": 42}))
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert ac.read_make_test_green(green_store)["fingerprint"] == "fp-1"
