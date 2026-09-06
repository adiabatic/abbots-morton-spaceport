"""The rebuild suite's self-skipping wrapper: it skips on a matching green record, only the recordable flag writes a record, a red run deletes the record it contradicts, and every run that reaches a judgment — a skip included — files it in the timings journal under the lane's own name."""

import json

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_timings as ct
from rebuild.tools import rebuild_gate as rg

HARD_STDOUT = "FAILED rebuild/test_settle.py::test_x"
LANE = "contracts"


def _checks():
    """The check lines this run filed. The journal constant is read here rather than captured, because rebuild/conftest.py's autouse redirect is what points it under tmp_path and record_check resolves it at call time for exactly that reason."""
    return ct.load_checks(ct.JOURNAL)


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    """The lane's record under tmp_path. rebuild_lane_green resolves the module constant at call time, so redirecting it here is enough for both modules."""
    store = tmp_path / "rebuild-contracts-green.json"
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", store)
    return store


def _fingerprints(monkeypatch, values):
    """The fingerprint sequence: one entry per call the wrapper makes, so a two-element list is the before/after pair a run to a green consumes. The closure the wrapper reads is the key and one label carrying it, which is enough for the selection to find nothing recorded and run the whole suite."""
    calls = iter(values)

    def closure(root, lane):
        key = next(calls)
        return key, (None if key is None else {"key": key})

    monkeypatch.setattr(rg, "rebuild_lane_closure", closure)


def _suite_stub(monkeypatch, outcome):
    """Stub _run_suite, recording (argv, env) per spawn. `outcome` is the (returncode, stdout) the spawn answers, or None for a run that must spawn nothing."""
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
    """The lane's controller files its per-worker peaks under the lane's name, which only holds while the unit rides a copy of the environment written for that one spawn."""
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
    """One check line, named the way the lane's pool line is named, carrying the argv that was spawned and the seconds it took — and no run, because nothing spawns this wrapper but a person."""
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
    """A closure judged unchanged is a judgment worth counting, so the skip is on the record — but with no argv and no seconds, since nothing ran and a zero would land in the timing rows as a suite that finished instantly."""
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
    """The ids are the point of the record: a red run files what failed."""
    _fingerprints(monkeypatch, ["c-1"])
    _suite_stub(monkeypatch, (3, HARD_STDOUT))
    assert rg.main([]) == 3
    (check,) = _checks()
    assert check["check"] == "rebuild-contracts"
    assert check["verdict"] == "red"
    assert check["failed_ids"] == ["rebuild/test_settle.py::test_x"]
    assert check["argv"] == ac.rebuild_lane_argv(LANE)
