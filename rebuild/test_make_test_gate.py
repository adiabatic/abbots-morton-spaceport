"""The `make test` self-skip wrapper: skip on a matching green record, run and record otherwise, and never leave a record a red or moved closure has contradicted."""

import json
import os
from types import SimpleNamespace

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_timings as ct
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
    """Stub the suite spawn, capturing each argv and the environment it was handed, since the child's environment is where the pool's unit name is written."""
    spawned = []
    envs = []

    def fake_run(argv, cwd, env=None):
        spawned.append(argv)
        envs.append(env)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(mtg.subprocess, "run", fake_run)
    return spawned, envs


def test_skips_without_spawning_when_the_record_matches(green_store, monkeypatch, capsys):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-1"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == []
    assert "SKIPPED" in capsys.readouterr().out


def test_force_runs_despite_a_matching_record(green_store, monkeypatch):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main(["--force"]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    record = ac.read_make_test_green(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_green_run_records_the_fingerprint(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    record = ac.read_make_test_green(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-2"


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
    record = ac.read_make_test_green(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_green_run_with_midrun_drift_records_nothing(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["fp-2", "fp-3"])
    _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert ac.read_make_test_green(green_store) is None
    assert "changed while the suite ran" in capsys.readouterr().out


def test_runs_unconditionally_without_git(green_store, monkeypatch):
    _fingerprints(monkeypatch, [None])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert ac.read_make_test_green(green_store) is None


def test_the_font_suite_pool_names_itself_in_the_childs_environment(green_store, monkeypatch):
    """The name rides on the child's own environment dict and nowhere else, so the controller can file its per-worker peaks under `font-suite` while nothing this process spawns later inherits the label."""
    # Deleted first because this very suite may be running inside a lane that named its own pool: what is being pinned is that main() leaves the variable exactly as it found it, so it has to start from a known absence.
    monkeypatch.delenv(ct.POOL_UNIT_ENV, raising=False)
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    spawned, envs = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert envs[0] is not None
    assert envs[0][ct.POOL_UNIT_ENV] == "font-suite"
    assert "PATH" in envs[0]
    assert ct.POOL_UNIT_ENV not in os.environ


def test_stale_record_format_never_matches(green_store, monkeypatch):
    green_store.write_text(json.dumps({"fingerprint": 42}))
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    record = ac.read_make_test_green(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"
