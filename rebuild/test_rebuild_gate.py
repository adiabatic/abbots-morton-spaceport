"""The rebuild suite's self-skipping wrapper, now two lanes deep: each lane skips on its own matching green record, the cheap contracts lane runs first so a hard failure there never pays for the long validators lane, and only the recordable flag writes a record."""

import json

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import rebuild_gate as rg

BASELINE_STDOUT = "\n".join(f"FAILED {test_id}" for test_id in sorted(ac.BASELINE_REBUILD_FAILURES))
HARD_STDOUT = "FAILED rebuild/test_settle.py::test_x"


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    """Both lanes' records under tmp_path, keyed by lane. rebuild_lane_green resolves the module constants at call time, so redirecting them here is enough for both modules."""
    stores = {lane: tmp_path / f"rebuild-{lane}-green.json" for lane in ac.REBUILD_LANES}
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", stores["contracts"])
    monkeypatch.setattr(ac, "REBUILD_VALIDATORS_GREEN", stores["validators"])
    return stores


def _fingerprints(monkeypatch, values):
    """Per-lane fingerprint sequences: one entry per call the wrapper makes for that lane, so a two-element list is the before/after pair a lane that runs to a green consumes."""
    calls = {lane: iter(seq) for lane, seq in values.items()}
    monkeypatch.setattr(rg, "rebuild_lane_fingerprint", lambda root, lane: next(calls[lane]))


def _suite_stub(monkeypatch, outcomes):
    """Stub _run_suite, recording (lane, argv, env) per spawn. `outcomes` maps a lane to its (returncode, stdout)."""
    spawned = []

    def fake_run(argv, env):
        lane = argv[argv.index("--lane") + 1]
        spawned.append((lane, list(argv), dict(env)))
        return outcomes[lane]

    monkeypatch.setattr(rg, "_run_suite", fake_run)
    return spawned


def _lanes(spawned):
    return [lane for lane, _, _ in spawned]


def test_both_lanes_skip_without_spawning_when_their_records_match(green_store, monkeypatch, capsys):
    for lane, store in green_store.items():
        ac.record_green(store, f"fp-{lane}")
    _fingerprints(monkeypatch, {"contracts": ["fp-contracts"], "validators": ["fp-validators"]})
    spawned = _suite_stub(monkeypatch, {})
    assert rg.main([]) == 0
    assert spawned == []
    out = capsys.readouterr().out
    assert "contracts lane SKIPPED" in out
    assert "validators lane SKIPPED" in out


def test_force_runs_both_lanes_despite_matching_records(green_store, monkeypatch):
    for lane, store in green_store.items():
        ac.record_green(store, f"fp-{lane}")
    _fingerprints(
        monkeypatch,
        {"contracts": ["fp-contracts"] * 2, "validators": ["fp-validators"] * 2},
    )
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main(["--force"]) == 0
    assert _lanes(spawned) == ["contracts", "validators"]
    assert spawned[0][1] == ac.rebuild_lane_argv("contracts")
    assert spawned[1][1] == ac.rebuild_lane_argv("validators")


def test_a_clean_run_records_a_green_per_lane(green_store, monkeypatch):
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["contracts", "validators"]
    for lane, expected in (("contracts", "c-1"), ("validators", "v-1")):
        record = ac.read_green_record(green_store[lane])
        assert record is not None
        assert record["fingerprint"] == expected


def test_only_the_stale_lane_runs(green_store, monkeypatch):
    ac.record_green(green_store["contracts"], "c-1")
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["validators"]


def test_documented_baseline_failures_still_read_green_and_record(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    _suite_stub(monkeypatch, {"contracts": (1, BASELINE_STDOUT), "validators": (0, "")})
    assert rg.main([]) == 0
    record = ac.read_green_record(green_store["contracts"])
    assert record is not None
    assert record["fingerprint"] == "c-1"
    assert "documented baseline" in capsys.readouterr().out


def test_a_contracts_hard_failure_never_starts_the_validators_lane(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"]})
    spawned = _suite_stub(monkeypatch, {"contracts": (3, HARD_STDOUT)})
    assert rg.main([]) == 3
    assert _lanes(spawned) == ["contracts"]
    assert ac.read_green_record(green_store["contracts"]) is None
    assert ac.read_green_record(green_store["validators"]) is None
    out = capsys.readouterr().out
    assert "hard rebuild failure (contracts): rebuild/test_settle.py::test_x" in out
    assert "validators lane not run (contracts lane failed)" in out


def test_a_contracts_green_survives_a_validators_hard_failure(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"]})
    _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (2, HARD_STDOUT)})
    assert rg.main([]) == 2
    record = ac.read_green_record(green_store["contracts"])
    assert record is not None
    assert record["fingerprint"] == "c-1"
    assert ac.read_green_record(green_store["validators"]) is None
    assert "hard rebuild failure (validators): rebuild/test_settle.py::test_x" in capsys.readouterr().out


def test_a_forced_hard_failure_deletes_that_lanes_contradicted_record(green_store, monkeypatch):
    ac.record_green(green_store["contracts"], "c-1")
    ac.record_green(green_store["validators"], "v-1")
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"]})
    _suite_stub(monkeypatch, {"contracts": (1, HARD_STDOUT)})
    assert rg.main(["--force"]) == 1
    assert ac.read_green_record(green_store["contracts"]) is None
    validators = ac.read_green_record(green_store["validators"])
    assert validators is not None
    assert validators["fingerprint"] == "v-1"


def test_a_hard_failure_keeps_a_record_for_a_different_closure(green_store, monkeypatch):
    ac.record_green(green_store["contracts"], "c-1")
    _fingerprints(monkeypatch, {"contracts": ["c-2"], "validators": ["v-1"]})
    _suite_stub(monkeypatch, {"contracts": (1, HARD_STDOUT)})
    assert rg.main([]) == 1
    record = ac.read_green_record(green_store["contracts"])
    assert record is not None
    assert record["fingerprint"] == "c-1"


def test_nonzero_exit_with_no_parsed_lines_is_red(green_store, monkeypatch):
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"]})
    _suite_stub(monkeypatch, {"contracts": (2, "")})
    assert rg.main([]) == 2
    assert ac.read_green_record(green_store["contracts"]) is None


def test_a_green_lane_whose_closure_drifted_records_nothing(green_store, monkeypatch, capsys):
    _fingerprints(monkeypatch, {"contracts": ["c-1", "c-2"], "validators": ["v-1"] * 2})
    _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert ac.read_green_record(green_store["contracts"]) is None
    validators = ac.read_green_record(green_store["validators"])
    assert validators is not None
    assert "changed while the suite ran" in capsys.readouterr().out


def test_both_lanes_run_unconditionally_without_git(green_store, monkeypatch):
    _fingerprints(monkeypatch, {"contracts": [None], "validators": [None]})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["contracts", "validators"]
    assert ac.read_green_record(green_store["contracts"]) is None
    assert ac.read_green_record(green_store["validators"]) is None


def test_a_stale_record_format_never_matches(green_store, monkeypatch):
    green_store["contracts"].write_text(json.dumps({"fingerprint": 42}))
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["contracts", "validators"]
    record = ac.read_green_record(green_store["contracts"])
    assert record is not None
    assert record["fingerprint"] == "c-1"


def test_pyright_runs_in_the_first_spawned_lane_only(green_store, monkeypatch):
    """Pyright checks the whole tree from pyproject's include list, so its answer cannot change between two invocations of one working tree — the flag rides into whichever lane spawns first and is stripped from every lane after it."""
    monkeypatch.setenv(rg.PYRIGHT_ENV, "1")
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert spawned[0][2].get(rg.PYRIGHT_ENV) == "1"
    assert rg.PYRIGHT_ENV not in spawned[1][2]


def test_pyright_rides_into_the_validators_lane_when_contracts_skipped(green_store, monkeypatch):
    monkeypatch.setenv(rg.PYRIGHT_ENV, "1")
    ac.record_green(green_store["contracts"], "c-1")
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["validators"]
    assert spawned[0][2].get(rg.PYRIGHT_ENV) == "1"
