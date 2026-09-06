"""The rebuild suite's self-skipping wrapper, now two lanes deep: each lane skips on its own matching green record, the cheap contracts lane runs first so a hard failure there never pays for the long validators lane, only the recordable flag writes a record, and every lane that reaches a judgment — a skip included — files it in the timings journal under its own name."""

import json

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_timings as ct
from rebuild.tools import rebuild_gate as rg

HARD_STDOUT = "FAILED rebuild/test_settle.py::test_x"


def _checks():
    """The check lines this run filed. The journal constant is read here rather than captured, because rebuild/conftest.py's autouse redirect is what points it under tmp_path and record_check resolves it at call time for exactly that reason."""
    return ct.load_checks(ct.JOURNAL)


@pytest.fixture(autouse=True)
def stamped(monkeypatch):
    """A stamped rebuild/out/m1 for every test but the ones about the stamp. This module is contracts-lane and reads no live artifact, so the real probe — which opens the six windows heads under rebuild/out — is stubbed out rather than consulted."""
    monkeypatch.setattr(rg, "m1_tables_stamped", lambda: True)


@pytest.fixture
def green_store(tmp_path, monkeypatch):
    """Both lanes' records under tmp_path, keyed by lane. rebuild_lane_green resolves the module constants at call time, so redirecting them here is enough for both modules."""
    stores = {lane: tmp_path / f"rebuild-{lane}-green.json" for lane in ac.REBUILD_LANES}
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", stores["contracts"])
    monkeypatch.setattr(ac, "REBUILD_VALIDATORS_GREEN", stores["validators"])
    return stores


def _fingerprints(monkeypatch, values):
    """Per-lane fingerprint sequences: one entry per call the wrapper makes for that lane, so a two-element list is the before/after pair a lane that runs to a green consumes. The closure the wrapper reads is the key and one label carrying it, which is enough for the contracts lane's selection to find nothing recorded and run the whole lane."""
    calls = {lane: iter(seq) for lane, seq in values.items()}

    def closure(root, lane):
        key = next(calls[lane])
        return key, (None if key is None else {"key": key})

    monkeypatch.setattr(rg, "rebuild_lane_closure", closure)


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


def test_each_lane_names_its_own_pool_without_leaking_into_the_next(green_store, monkeypatch):
    """Each lane's controller files its per-worker peaks under that lane's name, which only holds while the unit rides a per-lane copy of the environment: written into the shared dict instead, lane one's name would still be there when lane two spawned and the validators pool would be recorded as contracts."""
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["contracts", "validators"]
    assert spawned[0][2][rg.POOL_UNIT_ENV] == "rebuild-contracts"
    assert spawned[1][2][rg.POOL_UNIT_ENV] == "rebuild-validators"


def test_pyright_runs_in_the_first_spawned_lane_only(green_store, monkeypatch):
    """Pyright checks the whole tree from pyproject's include list, so its answer cannot change between two invocations of one working tree — the flag rides into whichever lane spawns first and is stripped from every lane after it."""
    monkeypatch.setenv(rg.PYRIGHT_ENV, "1")
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    assert spawned[0][2].get(rg.PYRIGHT_ENV) == "1"
    assert rg.PYRIGHT_ENV not in spawned[1][2]


def test_each_lane_files_its_verdict_under_its_own_check_name(green_store, monkeypatch):
    """One check line per lane, named the way that lane's pool line is named, carrying the argv that was spawned and the seconds it took — and no run, because nothing spawns this wrapper but a person."""
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"] * 2})
    _suite_stub(monkeypatch, {"contracts": (0, ""), "validators": (0, "")})
    assert rg.main([]) == 0
    checks = _checks()
    assert [check["check"] for check in checks] == ["rebuild-contracts", "rebuild-validators"]
    for check, lane in zip(checks, ac.REBUILD_LANES):
        assert check["verdict"] == "green"
        assert check["status"] == "green"
        assert check["failed_ids"] == []
        assert check["argv"] == ac.rebuild_lane_argv(lane)
        assert isinstance(check["elapsed_s"], int | float)
        assert "run" not in check


def test_a_skipped_lane_files_a_skipped_check_with_no_timing(green_store, monkeypatch):
    """A closure judged unchanged is a judgment worth counting, so the skip is on the record — but with no argv and no seconds, since nothing ran and a zero would land in the timing rows as a suite that finished instantly."""
    for lane, store in green_store.items():
        ac.record_green(store, f"fp-{lane}")
    _fingerprints(monkeypatch, {"contracts": ["fp-contracts"], "validators": ["fp-validators"]})
    _suite_stub(monkeypatch, {})
    assert rg.main([]) == 0
    checks = _checks()
    assert [check["check"] for check in checks] == ["rebuild-contracts", "rebuild-validators"]
    for check in checks:
        assert check["verdict"] == "skipped"
        assert "argv" not in check
        assert "elapsed_s" not in check


def test_a_hard_failure_files_the_ids_it_failed_on(green_store, monkeypatch):
    """The ids are the point of the record: a red lane files what failed, and the lane it stopped never files anything, because a check nobody asked for is not a skipped check."""
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"]})
    _suite_stub(monkeypatch, {"contracts": (3, HARD_STDOUT)})
    assert rg.main([]) == 3
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["check"] == "rebuild-contracts"
    assert checks[0]["verdict"] == "red"
    assert checks[0]["failed_ids"] == ["rebuild/test_settle.py::test_x"]
    assert checks[0]["argv"] == ac.rebuild_lane_argv("contracts")


def test_pyright_rides_into_the_validators_lane_when_contracts_skipped(green_store, monkeypatch):
    monkeypatch.setenv(rg.PYRIGHT_ENV, "1")
    ac.record_green(green_store["contracts"], "c-1")
    _fingerprints(monkeypatch, {"contracts": ["c-1"], "validators": ["v-1"] * 2})
    spawned = _suite_stub(monkeypatch, {"validators": (0, "")})
    assert rg.main([]) == 0
    assert _lanes(spawned) == ["validators"]
    assert spawned[0][2].get(rg.PYRIGHT_ENV) == "1"


def test_a_stale_tables_stamp_refuses_the_validators_lane_before_spawning(green_store, monkeypatch, capsys):
    """The lane's readers measure a live artifact, so tables the sources on disk no longer describe fail it on contents nobody edited. The refusal is filed as a red check of the lane's own name and costs the six windows heads rather than a whole collection."""
    monkeypatch.setattr(rg, "m1_tables_stamped", lambda: False)
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"]})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, "")})
    assert rg.main([]) == 1
    assert _lanes(spawned) == ["contracts"]
    contracts = ac.read_green_record(green_store["contracts"])
    assert contracts is not None
    assert contracts["fingerprint"] == "c-1"
    assert ac.read_green_record(green_store["validators"]) is None
    assert "rebuild.pipeline.run_m1" in capsys.readouterr().out
    checks = _checks()
    assert len(checks) == 2
    assert checks[1]["check"] == "rebuild-validators"
    assert checks[1]["verdict"] == "red"
    assert checks[1]["failed_ids"] == ["validators lane not spawned: stale tables stamp"]
    assert "argv" not in checks[1]


def test_force_does_not_bypass_the_stamp_refusal(green_store, monkeypatch):
    """Forcing a lane to run says the closure fingerprint may not excuse it; it says nothing about whether there is anything current to run it against."""
    monkeypatch.setattr(rg, "m1_tables_stamped", lambda: False)
    _fingerprints(monkeypatch, {"contracts": ["c-1"] * 2, "validators": ["v-1"]})
    spawned = _suite_stub(monkeypatch, {"contracts": (0, "")})
    assert rg.main(["--force"]) == 1
    assert _lanes(spawned) == ["contracts"]


def test_a_matching_validators_record_skips_without_consulting_the_stamp(green_store, monkeypatch):
    """The skip decision comes first: a lane whose closure is unchanged since its last green run is not being spawned, so what the tables on disk are stamped with is a question nobody has to ask."""

    def refuse():
        raise AssertionError("the stamp was read for a lane that skipped")

    monkeypatch.setattr(rg, "m1_tables_stamped", refuse)
    for lane, store in green_store.items():
        ac.record_green(store, f"fp-{lane}")
    _fingerprints(monkeypatch, {"contracts": ["fp-contracts"], "validators": ["fp-validators"]})
    _suite_stub(monkeypatch, {})
    assert rg.main([]) == 0
