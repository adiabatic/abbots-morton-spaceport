"""Tests for `rebuild/tools/make_test_gate.py`, the `make test` wrapper. It skips when the green record matches and otherwise runs the suite and records a pass. It never writes or keeps a green record that a failing run or a closure change during the run contradicts. It writes the run's check line to the timings journal unless a cycle is recording the same invocation."""

import json
import os
from types import SimpleNamespace

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_paths
from rebuild.tools import cycle_timings as ct
from rebuild.tools import make_test_gate as mtg


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    store = tmp_path / "make-test-green.json"
    monkeypatch.setattr(cycle_paths, "MAKE_TEST_GREEN", store)
    return store


def _checks():
    """Return the check lines written during this test. `ct.JOURNAL` is read at call time because the autouse fixture in rebuild/conftest.py redirects it under tmp_path. The same fixture removes the cycle's run id from the environment; without that, running this suite inside a cycle would make the wrapper skip recording in every test below."""
    return ct.load_checks(ct.JOURNAL)


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
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "make_test_exempt" in out


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
    """The pool name is set only in the child's environment dict, so the child's controller records its per-worker peaks under `font-suite` and nothing this process spawns later inherits the name."""
    # Removed first because this suite may run inside a pool that set its own name; the test checks that main() leaves the variable unset.
    monkeypatch.delenv(ct.POOL_UNIT_ENV, raising=False)
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    spawned, envs = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert envs[0] is not None
    assert envs[0][ct.POOL_UNIT_ENV] == "font-suite"
    assert "PATH" in envs[0]
    assert ct.POOL_UNIT_ENV not in os.environ


def test_a_green_run_files_a_green_check(green_store, monkeypatch):
    """The check line uses the name the cycle uses for this check and carries the spawned argv and the elapsed seconds. It has no `run` field because no cycle started this invocation."""
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["check"] == "make-test"
    assert checks[0]["verdict"] == "green"
    assert checks[0]["status"] == "green"
    assert checks[0]["argv"] == mtg.PYTEST_ARGV
    assert isinstance(checks[0]["elapsed_s"], int | float)
    assert "run" not in checks[0]


def test_a_red_run_files_the_exit_code_as_its_status(green_store, monkeypatch):
    """The exit code alone decides this suite's verdict, and the status is the cycle's label for a nonzero exit. `failed_ids` stays empty because the child's output goes straight to the terminal and is never captured."""
    _fingerprints(monkeypatch, ["fp-2"])
    _pytest_stub(monkeypatch, returncode=3)
    assert mtg.main([]) == 3
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["verdict"] == "red"
    assert checks[0]["status"] == "FAILED (exit 3)"
    assert checks[0]["failed_ids"] == []


def test_the_skip_path_files_a_skipped_check_with_no_timing(green_store, monkeypatch):
    ac.record_make_test_green("fp-1", green_store)
    _fingerprints(monkeypatch, ["fp-1"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == []
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["verdict"] == "skipped"
    assert "argv" not in checks[0]
    assert "elapsed_s" not in checks[0]


def test_a_cycle_spawned_run_files_nothing(green_store, monkeypatch):
    """The cycle runs this wrapper as gate:make-test and records the check itself, so the wrapper writes nothing when it inherits a run id. With one writer per invocation, `--by-outcome` counts each check once."""
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
    _fingerprints(monkeypatch, ["fp-2", "fp-2"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    assert _checks() == []


def test_stale_record_format_never_matches(green_store, monkeypatch):
    green_store.write_text(json.dumps({"fingerprint": 42}))
    _fingerprints(monkeypatch, ["fp-1", "fp-1"])
    spawned, _ = _pytest_stub(monkeypatch, returncode=0)
    assert mtg.main([]) == 0
    assert spawned == [mtg.PYTEST_ARGV]
    record = ac.read_make_test_green(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"
