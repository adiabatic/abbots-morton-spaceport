"""Tests for the deep replay's decisions, with the walk stubbed out: which runes it walks after an edit, which inputs it refuses, what it records on a pass and withdraws on a failure, how the cycle reports its status, the width it walks at, and the memo ceiling it passes to the crate. The walk is `kernel_exec.replay_strings`. The build's own horizon-4 replay exercises it everywhere except the `memo_windows` keyword, which the build never passes. `TestTheStringReplay` in rebuild/test_kernel_exec.py tests `memo_windows` through the subcommand."""

import pytest

from rebuild.pipeline import conform
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_paths
from rebuild.tools import deep_replay, deep_sweep

RUNES = {"qsPea": "p1", "qsTea": "t1", "qsIt": "i1"}
BOX_48_GIB = 51_539_607_552
BOX_32_GIB = 34_359_738_368
JOURNAL: list = []


class Spec:
    runes = {name: None for name in RUNES}


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A stub repo root: rune digests come from `RUNES`, the tables stamp counts as current, each rune's closure is itself, `AMS_DEEP_REPLAY_MEMO_WINDOWS` is unset so the walk uses the default ceiling whatever the developer's shell sets, and every record is redirected into tmp_path."""
    store = tmp_path / "rebuild" / "out" / "deep-replay-green.json"
    monkeypatch.delenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", raising=False)
    monkeypatch.setattr(deep_replay, "ROOT", tmp_path)
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", store)
    monkeypatch.setattr(deep_replay, "tables_stamped", lambda: True)
    JOURNAL.clear()
    monkeypatch.setattr(
        deep_replay, "record_check", lambda verdict, **kw: JOURNAL.append((verdict.verdict, kw.get("argv")))
    )
    monkeypatch.setattr(deep_replay, "load_default_spec", lambda: Spec())
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: dict(RUNES))
    monkeypatch.setattr(deep_replay.run_m1, "replay_structure_stamp", lambda spec: "structure-1")
    monkeypatch.setattr(
        deep_replay.spec_load, "rune_closure", lambda spec: {name: frozenset({name}) for name in RUNES}
    )
    return tmp_path


def _stub_walk(monkeypatch, walked=None, disagree=None, ceilings=None):
    def fake(spec, out_dir, configs, *, horizon, families, threads, memo_windows, timings=False):
        if walked is not None:
            walked.append((horizon, families, threads))
        if ceilings is not None:
            ceilings.append(memo_windows)
        if disagree is not None:
            raise deep_replay.kernel_exec.ReplayDisagreement(disagree)
        return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

    monkeypatch.setattr(deep_replay.kernel_exec, "replay_strings", fake)


def test_a_horizon_at_or_below_the_builds_own_is_refused(bench, monkeypatch):
    _stub_walk(monkeypatch)
    with pytest.raises(SystemExit, match="no deeper than the build's own replay"):
        deep_replay.main(["--horizon", str(ac.CONFORM_HORIZON_DEFAULT)])


def test_a_stale_tables_stamp_is_refused(bench, monkeypatch):
    _stub_walk(monkeypatch)
    monkeypatch.setattr(deep_replay, "tables_stamped", lambda: False)
    with pytest.raises(SystemExit, match="stale relative to the runes"):
        deep_replay.main([])


def test_without_a_record_the_walk_needs_families_or_all(bench, monkeypatch):
    walked: list = []
    _stub_walk(monkeypatch, walked)
    with pytest.raises(SystemExit, match="no deep replay has been recorded"):
        deep_replay.main([])
    assert deep_replay.main(["--families", "qsTea", "--threads", "2"]) == 0
    assert walked == [(ac.DEEP_REPLAY_HORIZON_DEFAULT, ["qsTea"], 2)]
    record = ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None
    assert record["files"] == {"qsTea": "t1"}
    assert record["horizon"] == ac.DEEP_REPLAY_HORIZON_DEFAULT
    assert record["structure"] == "structure-1"
    assert ac.deep_replay_status(bench)[0] == "armed"
    assert "qsIt" in ac.deep_replay_status(bench)[1] and "qsPea" in ac.deep_replay_status(bench)[1]


def test_all_walks_the_universe_and_records_every_rune(bench, monkeypatch, capsys):
    walked: list = []
    _stub_walk(monkeypatch, walked)
    assert deep_replay.main(["--all", "--threads", "1"]) == 0
    assert walked == [(ac.DEEP_REPLAY_HORIZON_DEFAULT, None, 1)]
    assert JOURNAL == [("green", ["--all", "--threads", "1"])]
    record = ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None and record["files"] == RUNES
    assert ac.deep_replay_status(bench) == ("current", f"horizon {ac.DEEP_REPLAY_HORIZON_DEFAULT}")
    assert deep_replay.main(["--status"]) == 0
    assert "current" in capsys.readouterr().out


def test_a_rune_edit_walks_the_moved_runes_and_their_readers(bench, monkeypatch):
    """The record holds every rune's digest. After an edit the walk covers the runes whose digest changed plus the runes whose records read them, and the new record holds the new digests beside the unchanged ones."""
    walked: list = []
    _stub_walk(monkeypatch, walked)
    ac.record_deep_replay_green(
        dict(RUNES), 5, "structure-1", path=bench / "rebuild" / "out" / "deep-replay-green.json"
    )
    assert deep_replay.main(["--threads", "1"]) == 0
    assert walked == []
    moved = {**RUNES, "qsPea": "p2"}
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: dict(moved))
    monkeypatch.setattr(
        deep_replay.spec_load,
        "rune_closure",
        lambda spec: {
            "qsPea": frozenset({"qsPea"}),
            "qsTea": frozenset({"qsTea", "qsPea"}),
            "qsIt": frozenset({"qsIt"}),
        },
    )
    assert ac.deep_replay_status(bench)[0] == "armed"
    assert "qsPea" in ac.deep_replay_status(bench)[1]
    assert deep_replay.main(["--threads", "3"]) == 0
    assert walked == [(5, ["qsPea", "qsTea"], 3)]
    record = ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None and record["files"] == moved
    assert ac.deep_replay_status(bench)[0] == "current"


def test_a_disagreement_records_nothing(bench, monkeypatch, capsys):
    _stub_walk(
        monkeypatch,
        disagree="(qsBay, qsUtter.mono.ex-y5.ex-ext-1, qsGay, qsIt, qsPea, #EDGE) at position 1 of qsUtter qsBay qsGay qsIt qsPea: settlement says qsBay.hapax.en-y5.ex-y0, rules say qsBay.hapax.en-y5",
    )
    assert deep_replay.main(["--families", "qsPea", "--threads", "1"]) == 1
    assert ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json") is None
    assert JOURNAL == [("red", ["--families", "qsPea", "--threads", "1"])]
    assert "qsUtter qsBay qsGay qsIt qsPea" in capsys.readouterr().err


def test_a_disagreement_withdraws_the_walked_runes_from_a_green_record(bench, monkeypatch):
    """A red walk over runes the record holds green at their current digests withdraws those runes, so the status reports them armed and a bare walk covers them again, while the record keeps the runes the walk did not cover. A red `--all` walk deletes the record."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path)
    _stub_walk(monkeypatch, disagree="replay disagreement at position 1 of qsPea qsIt")
    assert deep_replay.main(["--families", "qsPea", "--threads", "1"]) == 1
    record = ac.read_green_record(path)
    assert record is not None
    assert record["files"] == {"qsTea": "t1", "qsIt": "i1"}
    assert record["horizon"] == 5 and record["structure"] == "structure-1"
    status, note = ac.deep_replay_status(bench)
    assert status == "armed" and "qsPea" in note and "qsTea" not in note
    walked: list = []
    _stub_walk(monkeypatch, walked)
    assert deep_replay.main(["--threads", "1"]) == 0
    assert walked == [(5, ["qsPea"], 1)]
    assert ac.deep_replay_status(bench)[0] == "current"
    _stub_walk(monkeypatch, disagree="replay disagreement at position 1 of qsTea qsIt")
    assert deep_replay.main(["--all", "--threads", "1"]) == 1
    assert ac.read_green_record(path) is None
    assert ac.deep_replay_status(bench)[0] == "never-run"


def test_a_disagreeing_bare_walk_withdraws_the_moved_runes_and_their_readers(bench, monkeypatch):
    """A red walk whose runes come from the record withdraws every rune it walked, including a reader whose own digest has not changed, and keeps the rest of the record, so the status names the reader armed instead of reporting "nothing moved"."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path)
    moved = {**RUNES, "qsPea": "p2"}
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: dict(moved))
    monkeypatch.setattr(
        deep_replay.spec_load,
        "rune_closure",
        lambda spec: {
            "qsPea": frozenset({"qsPea"}),
            "qsTea": frozenset({"qsTea", "qsPea"}),
            "qsIt": frozenset({"qsIt"}),
        },
    )
    walked: list = []
    _stub_walk(monkeypatch, walked, disagree="replay disagreement at position 1 of qsTea qsPea")
    assert deep_replay.main(["--threads", "1"]) == 1
    assert walked == [(5, ["qsPea", "qsTea"], 1)]
    record = ac.read_green_record(path)
    assert record is not None
    assert record["files"] == {"qsIt": "i1"}
    assert record["horizon"] == 5 and record["structure"] == "structure-1"
    status, note = ac.deep_replay_status(bench)
    assert status == "armed" and "qsTea" in note


def test_the_walk_hands_the_crate_its_memo_ceiling(bench, monkeypatch, capsys):
    """Every walk passes the crate a memo ceiling: `DEEP_REPLAY_MEMO_WINDOWS` when `AMS_DEEP_REPLAY_MEMO_WINDOWS` is unset, its value when set, and a `RuntimeError` before any walk when the value is not a count. The per-configuration line reports `windows` as window settles, because a window can be settled again after a memo release. The journal line records the walk's peak RSS beside its elapsed time, which `make cycle-timings ARGS='--by-step'` prints for `check:replay-deep`."""
    ceilings: list = []
    _stub_walk(monkeypatch, ceilings=ceilings)
    checks: list = []
    monkeypatch.setattr(
        deep_replay, "record_check", lambda verdict, **kw: checks.append((verdict.verdict, kw))
    )
    monkeypatch.setattr(deep_replay.peak_rss, "peak_rss_children_bytes", lambda: 123)
    assert deep_replay.main(["--families", "qsTea", "--threads", "1"]) == 0
    assert ceilings == [deep_replay.DEEP_REPLAY_MEMO_WINDOWS]
    out = capsys.readouterr().out
    assert f"at most {deep_replay.DEEP_REPLAY_MEMO_WINDOWS} windows memoized per walk" in out
    assert "deep replay[default]: 1 texts, 1 window settles, 0 skipped" in out
    assert [(verdict, kw["peak_rss_bytes"]) for verdict, kw in checks] == [("green", 123)]
    monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", "2000000")
    assert deep_replay.main(["--families", "qsTea", "--threads", "1"]) == 0
    assert ceilings == [deep_replay.DEEP_REPLAY_MEMO_WINDOWS, 2_000_000]
    assert "at most 2000000 windows memoized per walk" in capsys.readouterr().out
    monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", "2M")
    with pytest.raises(RuntimeError, match="AMS_DEEP_REPLAY_MEMO_WINDOWS"):
        deep_replay.main(["--families", "qsTea", "--threads", "1"])
    assert len(ceilings) == 2
    monkeypatch.delenv("AMS_DEEP_REPLAY_MEMO_WINDOWS")
    _stub_walk(monkeypatch, disagree="replay disagreement at position 1 of qsTea qsIt")
    assert deep_replay.main(["--families", "qsTea", "--threads", "1"]) == 1
    assert [(verdict, kw["peak_rss_bytes"]) for verdict, kw in checks[2:]] == [("red", 123)]


def test_runes_moving_mid_walk_record_nothing(bench, monkeypatch, capsys):
    _stub_walk(monkeypatch)
    answers = iter([dict(RUNES), {**RUNES, "qsIt": "i2"}])
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: next(answers))
    assert deep_replay.main(["--all", "--threads", "1"]) == 0
    assert ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json") is None
    assert "changed while it ran" in capsys.readouterr().out


def test_a_deeper_record_is_current_and_a_shallower_one_is_armed(bench, monkeypatch):
    ac.record_deep_replay_green(
        dict(RUNES), 6, "structure-1", path=bench / "rebuild" / "out" / "deep-replay-green.json"
    )
    monkeypatch.setattr("rebuild.pipeline.fingerprint.rune_digests", lambda root: dict(RUNES))
    assert ac.deep_replay_status(bench, 5) == ("current", "horizon 6")
    assert ac.deep_replay_status(bench, 7)[0] == "armed"


def test_the_width_is_the_boxs_memory_or_the_stated_knob(monkeypatch):
    monkeypatch.delenv("AMS_DEEP_REPLAY_THREADS", raising=False)
    assert deep_replay.replay_threads(total_bytes=deep_replay.DEEP_REPLAY_PEAK_BYTES) == 1
    assert deep_replay.replay_threads(total_bytes=deep_replay.DEEP_REPLAY_PEAK_BYTES * 40) == len(
        conform.SETTLEMENT_CONFIGS
    )
    monkeypatch.setenv("AMS_DEEP_REPLAY_THREADS", "2")
    assert deep_replay.replay_threads() == 2
    monkeypatch.setenv("AMS_DEEP_REPLAY_THREADS", "2GB")
    with pytest.raises(RuntimeError):
        deep_replay.replay_threads()


def test_the_memo_ceiling_is_the_stated_knob_or_the_priced_default(monkeypatch):
    """The ceiling is `AMS_DEEP_REPLAY_MEMO_WINDOWS` when set and `DEEP_REPLAY_MEMO_WINDOWS` otherwise. A set value that is not a bare decimal count of at least one raises an error naming the variable instead of falling back to the default."""
    monkeypatch.delenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", raising=False)
    assert deep_replay.replay_memo_windows() == deep_replay.DEEP_REPLAY_MEMO_WINDOWS
    monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", "2000000")
    assert deep_replay.replay_memo_windows() == 2_000_000
    for stated in ("2M", "0", "-5", "", "2.5e6"):
        monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", stated)
        with pytest.raises(RuntimeError, match="AMS_DEEP_REPLAY_MEMO_WINDOWS"):
            deep_replay.replay_memo_windows()


@pytest.mark.parametrize(
    "total, wanted",
    [(BOX_32_GIB, len(conform.SETTLEMENT_CONFIGS)), (BOX_48_GIB, len(conform.SETTLEMENT_CONFIGS))],
)
def test_the_shipped_walk_cost_holds_both_fleet_boxes_at_their_widths(total, wanted, monkeypatch):
    """Both fleet machines (`doc/fleet.md`) walk every settlement configuration at once under the checked-in `DEEP_REPLAY_PEAK_BYTES`. No cycle runs this walk, so `make job-costs` does not watch the constant, and the width assertions above pass for any positive value. This test fails if the constant goes above 5.27 GB, which drops the 32 GiB machine to four walks, or above 8.71 GB, which does the same on the 48 GiB machine."""
    monkeypatch.delenv("AMS_DEEP_REPLAY_THREADS", raising=False)
    assert deep_replay.replay_threads(total_bytes=total) == wanted


def test_a_green_deep_sweep_refreshes_the_replays_record(tmp_path, monkeypatch):
    """The deep HarfBuzz sweep over all texts settles every text it shapes, so a passing sweep at the replay's horizon covers the replay. Its refresh records every rune at the digest the sweep read before it started."""
    monkeypatch.setattr(
        cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "rebuild" / "out" / "deep-replay-green.json"
    )
    monkeypatch.setattr(deep_sweep.run_m1, "replay_structure_stamp", lambda spec: "structure-1")
    monkeypatch.setattr("rebuild.pipeline.spec_load.load_default_spec", lambda: Spec())
    deep_sweep.refresh_deep_replay(6, dict(RUNES))
    record = ac.read_green_record(tmp_path / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None and record["files"] == RUNES and record["horizon"] == 6
    assert record["structure"] == "structure-1"
