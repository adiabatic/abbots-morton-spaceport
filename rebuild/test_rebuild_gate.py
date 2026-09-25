"""Tests for the rebuild suite's self-skipping wrapper (`rebuild/tools/rebuild_gate.py`): it skips on a matching green record, only a green run whose closure did not change writes a record, a red run deletes the record it contradicts, and every run, a skip included, writes one check line to the timings journal under the lane's pool name."""

import json

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_paths
from rebuild.tools import cycle_timings as ct
from rebuild.tools import rebuild_gate as rg

HARD_STDOUT = "FAILED rebuild/test_settle.py::test_x"
LANE = "contracts"


def _checks():
    """The check lines this test wrote. `ct.JOURNAL` is read at call time because rebuild/conftest.py's autouse fixture redirects it under tmp_path."""
    return ct.load_checks(ct.JOURNAL)


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    """The lane's green record under tmp_path. `rebuild_lane_green` reads the `cycle_paths` constant at call time, so this redirect reaches both the wrapper and the artifact cycle."""
    store = tmp_path / "rebuild-contracts-green.json"
    monkeypatch.setattr(cycle_paths, "REBUILD_CONTRACTS_GREEN", store)
    return store


def _fingerprints(monkeypatch, values):
    """Stub the lane closure with one key per call. A green run reads it twice, before and after the suite. The stub returns the key with a one-label digest map, so the selection finds no recorded per-test closures and runs the whole suite."""
    calls = iter(values)

    def closure(root, lane):
        key = next(calls)
        return key, (None if key is None else {"key": key})

    monkeypatch.setattr(rg, "rebuild_lane_closure", closure)


def _suite_stub(monkeypatch, outcome):
    """Stub `_run_suite`, recording (argv, env) per spawn. `outcome` is the (returncode, stdout) the spawn returns, or None for a run that must not spawn."""
    spawned = []

    def fake_run(argv, env):
        spawned.append((list(argv), dict(env)))
        assert outcome is not None, "the suite was spawned by a run that should have skipped"
        return outcome

    monkeypatch.setattr(rg, "_run_suite", fake_run)
    return spawned


def test_the_suite_is_one_lane():
    assert ac.REBUILD_LANES == (LANE,)
    assert list(rg.POOL_UNIT_BY_LANE) == [LANE]


def test_the_lane_skips_without_spawning_when_its_record_matches(green_store, monkeypatch, capsys):
    ac.record_green(green_store, "fp-contracts")
    _fingerprints(monkeypatch, ["fp-contracts"])
    spawned = _suite_stub(monkeypatch, None)
    assert rg.main([]) == 0
    assert spawned == []
    assert "contracts lane SKIPPED" in capsys.readouterr().out


def test_force_runs_the_lane_despite_a_matching_record(green_store, monkeypatch):
    ac.record_green(green_store, "fp-contracts")
    _fingerprints(monkeypatch, ["fp-contracts"] * 2)
    spawned = _suite_stub(monkeypatch, (0, ""))
    assert rg.main(["--force"]) == 0
    assert [argv for argv, _ in spawned] == [ac.rebuild_lane_argv(LANE)]


def test_a_clean_run_records_a_green(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["c-1"] * 2)
    spawned = _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    assert len(spawned) == 1
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "c-1"


def test_a_hard_failure_records_nothing_and_names_the_test(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["c-1"])
    _suite_stub(monkeypatch, (3, HARD_STDOUT))
    assert rg.main([]) == 3
    assert ac.read_green_record(green_store) is None
    assert "hard rebuild failure (contracts): rebuild/test_settle.py::test_x" in capsys.readouterr().out


def test_a_forced_hard_failure_deletes_the_contradicted_record(green_store, monkeypatch):
    ac.record_green(green_store, "c-1")
    _fingerprints(monkeypatch, ["c-1"])
    _suite_stub(monkeypatch, (1, HARD_STDOUT))
    assert rg.main(["--force"]) == 1
    assert ac.read_green_record(green_store) is None


def test_a_hard_failure_keeps_a_record_for_a_different_closure(green_store, monkeypatch):
    ac.record_green(green_store, "c-1")
    _fingerprints(monkeypatch, ["c-2"])
    _suite_stub(monkeypatch, (1, HARD_STDOUT))
    assert rg.main([]) == 1
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "c-1"


def test_nonzero_exit_with_no_parsed_lines_is_red(green_store, monkeypatch):
    _fingerprints(monkeypatch, ["c-1"])
    _suite_stub(monkeypatch, (2, ""))
    assert rg.main([]) == 2
    assert ac.read_green_record(green_store) is None


def test_a_green_run_whose_closure_drifted_records_nothing(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, ["c-1", "c-2"])
    _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    assert ac.read_green_record(green_store) is None
    assert "changed while the suite ran" in capsys.readouterr().out


def test_the_lane_runs_unconditionally_without_git(green_store, monkeypatch):
    _fingerprints(monkeypatch, [None])
    spawned = _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    assert len(spawned) == 1
    assert ac.read_green_record(green_store) is None


def test_a_stale_record_format_never_matches(green_store, monkeypatch):
    green_store.write_text(json.dumps({"fingerprint": 42}))
    _fingerprints(monkeypatch, ["c-1"] * 2)
    spawned = _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    assert len(spawned) == 1
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "c-1"


def test_the_lane_names_its_pool_on_its_own_child(green_store, monkeypatch):
    """The suite's xdist controller records its per-worker peaks under the pool unit name, which the wrapper sets only on the copy of the environment it passes to the spawn."""
    _fingerprints(monkeypatch, ["c-1"] * 2)
    spawned = _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    assert spawned[0][1][rg.POOL_UNIT_ENV] == "rebuild-contracts"


def test_pyright_rides_into_the_spawned_lane(green_store, monkeypatch):
    monkeypatch.setenv(rg.PYRIGHT_ENV, "1")
    _fingerprints(monkeypatch, ["c-1"] * 2)
    spawned = _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    assert spawned[0][1].get(rg.PYRIGHT_ENV) == "1"


def test_a_green_run_files_its_verdict_under_the_lanes_check_name(green_store, monkeypatch):
    """One check line under the lane's pool name, with the spawned argv and the elapsed seconds. It has no `run` key, because only `make test-rebuild` runs this wrapper and no artifact cycle is its parent."""
    _fingerprints(monkeypatch, ["c-1"] * 2)
    _suite_stub(monkeypatch, (0, ""))
    assert rg.main([]) == 0
    (check,) = _checks()
    assert check["check"] == "rebuild-contracts"
    assert check["verdict"] == "green"
    assert check["status"] == "green"
    assert check["failed_ids"] == []
    assert check["argv"] == ac.rebuild_lane_argv(LANE)
    assert isinstance(check["elapsed_s"], int | float)
    assert "run" not in check


def test_a_skipped_lane_files_a_skipped_check_with_no_timing(green_store, monkeypatch):
    """A skip is written to the journal so it is counted, but with no argv and no seconds. Nothing ran, and a zero would appear in the timing rows as a suite that finished instantly."""
    ac.record_green(green_store, "fp-contracts")
    _fingerprints(monkeypatch, ["fp-contracts"])
    _suite_stub(monkeypatch, None)
    assert rg.main([]) == 0
    (check,) = _checks()
    assert check["check"] == "rebuild-contracts"
    assert check["verdict"] == "skipped"
    assert "argv" not in check
    assert "elapsed_s" not in check


def test_a_hard_failure_files_the_ids_it_failed_on(green_store, monkeypatch):
    """A red run records the ids of the tests that failed."""
    _fingerprints(monkeypatch, ["c-1"])
    _suite_stub(monkeypatch, (3, HARD_STDOUT))
    assert rg.main([]) == 3
    (check,) = _checks()
    assert check["check"] == "rebuild-contracts"
    assert check["verdict"] == "red"
    assert check["failed_ids"] == ["rebuild/test_settle.py::test_x"]
    assert check["argv"] == ac.rebuild_lane_argv(LANE)
