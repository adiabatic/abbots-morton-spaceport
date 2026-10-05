"""Tests for the deep replay's decisions, with the walk stubbed out: which runes it walks after an edit, which inputs it refuses, what it records on a pass and withdraws on a failure, how the cycle reports its status, the units it walks in and the width it walks them at, and the memo ceiling it passes to the crate. Each unit's walk is a `kernel_exec.replay_strings` call. The build's own length-4 replay exercises it everywhere except the `memo_windows` and `last` keywords, which the build never passes. `TestTheStringReplay` in rebuild/test_kernel_exec.py tests both through the subcommand."""

import json
from types import SimpleNamespace

import pytest

from rebuild.pipeline import conform
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_paths, memory_budget
from rebuild.tools import deep_replay, deep_sweep

RUNES = {"qsPea": "p1", "qsTea": "t1", "qsIt": "i1"}
MACHINE_48_GIB = 51_539_607_552
JOURNAL: list = []
IMPORTS = {"digest": "imports-1"}


# The stub spec's alphabet: its boundary token and its three letters, in code point order.
ALPHABET = (" ", "\ue650", "\ue652", "\ue670")


class Spec:
    """Three letters with code points and one boundary token, an alphabet of four symbols (`ALPHABET`)."""

    runes = {
        name: SimpleNamespace(codepoint=codepoint)
        for name, codepoint in zip(RUNES, (0xE650, 0xE652, 0xE670), strict=True)
    }
    registry = SimpleNamespace(boundary_tokens={"space": SimpleNamespace(codepoint=0x20)})


def every_text(max_length: int, symbols: int = len(ALPHABET)) -> int:
    return sum(symbols**length for length in range(1, max_length + 1))


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A stub repo root: rune digests come from `RUNES`, the tables stamp counts as current, the tables' imported windows hash to `IMPORTS["digest"]`, each rune's closure is itself, the crate counts as built, `AMS_DEEP_REPLAY_MEMO_WINDOWS` and `AMS_DEEP_REPLAY_THREADS` are unset so the walk uses the default ceiling and width whatever the developer's shell sets, and every record is redirected into tmp_path."""
    store = tmp_path / "rebuild" / "out" / "deep-replay-green.json"
    monkeypatch.delenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", raising=False)
    monkeypatch.delenv("AMS_DEEP_REPLAY_THREADS", raising=False)
    monkeypatch.setattr(deep_replay.kernel_exec, "ensure_built", lambda: None)
    monkeypatch.setattr(deep_replay, "ROOT", tmp_path)
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", store)
    monkeypatch.setattr(deep_replay, "tables_stamped", lambda: True)
    IMPORTS["digest"] = "imports-1"
    monkeypatch.setattr(deep_replay, "tables_imports_digest", lambda root=None: IMPORTS["digest"])
    monkeypatch.setattr(ac, "tables_imports_digest", lambda root=None: IMPORTS["digest"])
    monkeypatch.setattr(deep_sweep, "tables_imports_digest", lambda root=None: IMPORTS["digest"])
    JOURNAL.clear()
    monkeypatch.setattr(
        deep_replay, "record_check", lambda result, **kw: JOURNAL.append((result.outcome, kw.get("argv")))
    )
    monkeypatch.setattr(deep_replay, "load_default_spec", lambda: Spec())
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: dict(RUNES))
    monkeypatch.setattr(deep_replay.run_m1, "replay_structure_stamp", lambda spec: "structure-1")
    monkeypatch.setattr(
        deep_replay.spec_load, "rune_closure", lambda spec: {name: frozenset({name}) for name in RUNES}
    )
    return tmp_path


def _stub_walk(
    monkeypatch,
    walked=None,
    disagree=None,
    ceilings=None,
    peak=None,
    calls=None,
    disagree_on=None,
    short=None,
):
    """Stub `kernel_exec.replay_strings` with a unit's walk as the crate answers it over `ALPHABET`: one configuration on one thread, every text ending in the unit's symbol walked in one window settle, nothing skipped. `walked` and `ceilings` get one entry a run, from the run's first unit; `calls` gets every unit's configuration and symbol as it starts. `disagree` makes a unit fail with that message, every unit or only the `disagree_on` one. `peak` is every unit's peak, and the `short` unit answers one text fewer than it should."""

    def fake(
        spec,
        out_dir,
        configs,
        *,
        max_length,
        families,
        threads,
        memo_windows,
        last,
        timings=False,
        on_peak=None,
    ):
        (config,) = configs
        assert threads == 1
        if calls is not None:
            calls.append((config, last))
        if (config, last) == (conform.SETTLEMENT_CONFIGS[0], ALPHABET[0]):
            if walked is not None:
                walked.append((max_length, families))
            if ceilings is not None:
                ceilings.append(memo_windows)
        if disagree is not None and disagree_on in (None, (config, last)):
            raise deep_replay.kernel_exec.ReplayDisagreement(disagree)
        if peak is not None and on_peak is not None:
            on_peak(peak)
        texts = sum(len(ALPHABET) ** (length - 1) for length in range(1, max_length + 1))
        if (config, last) == short:
            texts -= 1
        return {config: {"texts": texts, "windows": 1, "skipped": 0}}

    monkeypatch.setattr(deep_replay.kernel_exec, "replay_strings", fake)


def test_a_max_length_at_or_below_the_builds_own_is_refused(bench, monkeypatch):
    _stub_walk(monkeypatch)
    with pytest.raises(SystemExit, match="no deeper than the build's own replay"):
        deep_replay.main(["--max-length", str(ac.CONFORM_MAX_LENGTH_DEFAULT)])


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
    assert "run `make replay-deep ARGS='--all --max-length 6'` once" in ac.deep_replay_status(bench, 6)[1]
    assert deep_replay.main(["--families", "qsTea", "--threads", "2"]) == 0
    assert walked == [(ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT, ["qsTea"])]
    record = ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None
    assert record["files"] == {"qsTea": "t1"}
    assert record["max_length"] == ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT
    assert record["structure"] == "structure-1"
    assert ac.deep_replay_status(bench)[0] == "due"
    assert "qsIt" in ac.deep_replay_status(bench)[1] and "qsPea" in ac.deep_replay_status(bench)[1]


def test_all_walks_every_text_and_records_every_rune(bench, monkeypatch, capsys):
    walked: list = []
    _stub_walk(monkeypatch, walked)
    assert deep_replay.main(["--all", "--threads", "1"]) == 0
    assert walked == [(ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT, None)]
    assert JOURNAL == [("green", ["--all", "--threads", "1"])]
    record = ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None and record["files"] == RUNES
    assert ac.deep_replay_status(bench) == ("current", f"maximum length {ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT}")
    assert deep_replay.main(["--status"]) == 0
    assert "current" in capsys.readouterr().out


def test_a_rune_edit_walks_the_moved_runes_and_their_readers(bench, monkeypatch):
    """The record holds every rune's digest. After an edit the walk covers the runes whose digest changed plus the runes whose records read them, and the new record holds the new digests beside the unchanged ones."""
    walked: list = []
    _stub_walk(monkeypatch, walked)
    ac.record_deep_replay_green(
        dict(RUNES),
        5,
        "structure-1",
        path=bench / "rebuild" / "out" / "deep-replay-green.json",
        imports="imports-1",
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
    assert ac.deep_replay_status(bench)[0] == "due"
    assert "qsPea" in ac.deep_replay_status(bench)[1]
    assert deep_replay.main(["--threads", "3"]) == 0
    assert walked == [(5, ["qsPea", "qsTea"])]
    record = ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None and record["files"] == moved
    assert ac.deep_replay_status(bench)[0] == "current"


def test_a_disagreement_records_nothing(bench, monkeypatch, capsys):
    _stub_walk(
        monkeypatch,
        disagree="(qsBay, qsUtter.mono.ex-y5.ex-ext-1, qsGay, qsIt, qsPea, #EDGE) at position 1 of qsUtter qsBay qsGay qsIt qsPea: settlement says qsBay.sole.en-y5.ex-y0, rules say qsBay.sole.en-y5",
    )
    assert deep_replay.main(["--families", "qsPea", "--threads", "1"]) == 1
    assert ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json") is None
    assert JOURNAL == [("red", ["--families", "qsPea", "--threads", "1"])]
    assert "qsUtter qsBay qsGay qsIt qsPea" in capsys.readouterr().err


def test_a_disagreement_withdraws_the_walked_runes_from_a_green_record(bench, monkeypatch):
    """A red walk over runes the record holds green at their current digests withdraws those runes, so the status reports them due and a bare walk covers them again, while the record keeps the runes the walk did not cover. A red `--all` walk deletes the record."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
    _stub_walk(monkeypatch, disagree="replay disagreement at position 1 of qsPea qsIt")
    assert deep_replay.main(["--families", "qsPea", "--threads", "1"]) == 1
    record = ac.read_green_record(path)
    assert record is not None
    assert record["files"] == {"qsTea": "t1", "qsIt": "i1"}
    assert record["max_length"] == 5 and record["structure"] == "structure-1"
    status, note = ac.deep_replay_status(bench)
    assert status == "due" and "qsPea" in note and "qsTea" not in note
    walked: list = []
    _stub_walk(monkeypatch, walked)
    assert deep_replay.main(["--threads", "1"]) == 0
    assert walked == [(5, ["qsPea"])]
    assert ac.deep_replay_status(bench)[0] == "current"
    _stub_walk(monkeypatch, disagree="replay disagreement at position 1 of qsTea qsIt")
    assert deep_replay.main(["--all", "--threads", "1"]) == 1
    assert ac.read_green_record(path) is None
    assert ac.deep_replay_status(bench)[0] == "never-run"


def test_a_disagreeing_bare_walk_withdraws_the_moved_runes_and_their_readers(bench, monkeypatch):
    """A red walk whose runes come from the record withdraws every rune it walked, including a reader whose own digest has not changed, and keeps the rest of the record, so the status names the reader due instead of reporting "nothing moved"."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
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
    assert walked == [(5, ["qsPea", "qsTea"])]
    record = ac.read_green_record(path)
    assert record is not None
    assert record["files"] == {"qsIt": "i1"}
    assert record["max_length"] == 5 and record["structure"] == "structure-1"
    status, note = ac.deep_replay_status(bench)
    assert status == "due" and "qsTea" in note


def test_the_walk_hands_the_crate_its_memo_ceiling(bench, monkeypatch, capsys):
    """Every walk passes the crate a memo ceiling: `DEEP_REPLAY_MEMO_WINDOWS` when `AMS_DEEP_REPLAY_MEMO_WINDOWS` is unset, its value when set, and a `RuntimeError` before any walk when the value is not a count. The per-configuration line reports `windows` as window settles, because a window can be settled again after a memo release. The journal line records the walk's peak RSS beside its elapsed time, which `make cycle-timings ARGS='--by-step'` prints for `check:replay-deep`."""
    ceilings: list = []
    _stub_walk(monkeypatch, ceilings=ceilings)
    checks: list = []
    monkeypatch.setattr(deep_replay, "record_check", lambda result, **kw: checks.append((result.outcome, kw)))
    monkeypatch.setattr(deep_replay.peak_rss, "peak_rss_children_bytes", lambda: 123)
    assert deep_replay.main(["--families", "qsTea", "--threads", "1"]) == 0
    assert ceilings == [deep_replay.DEEP_REPLAY_MEMO_WINDOWS]
    out = capsys.readouterr().out
    assert f"at most {deep_replay.DEEP_REPLAY_MEMO_WINDOWS} windows memoized per walk" in out
    assert f"deep replay[default]: {every_text(5)} texts, {len(ALPHABET)} window settles, 0 skipped" in out
    assert [(outcome, kw["peak_rss_bytes"]) for outcome, kw in checks] == [("green", 123)]
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
    assert [(outcome, kw["peak_rss_bytes"]) for outcome, kw in checks[2:]] == [("red", 123)]


def test_runes_moving_mid_walk_record_nothing(bench, monkeypatch, capsys):
    _stub_walk(monkeypatch)
    answers = iter([dict(RUNES), {**RUNES, "qsIt": "i2"}])
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: next(answers))
    assert deep_replay.main(["--all", "--threads", "1"]) == 0
    assert ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json") is None
    assert "changed while it ran" in capsys.readouterr().out


def test_a_family_walk_deeper_than_the_record_stays_at_the_depth_its_carried_runes_reached(
    bench, monkeypatch, capsys
):
    """A family walk deeper than the record carries the runes it did not walk at the depth they were walked to, so the record stays at that depth, the status at the walk's depth is due and names the bare walk at that depth, which walks every text, and the green line says where the record stays. An `--all` walk at that depth raises it too."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
    _stub_walk(monkeypatch)
    assert deep_replay.main(["--families", "qsTea", "--max-length", "6", "--threads", "1"]) == 0
    record = ac.read_green_record(path)
    assert record is not None and record["files"] == RUNES and record["max_length"] == 5
    status, note = ac.deep_replay_status(bench, 6)
    assert status == "due" and note.endswith("run `make replay-deep ARGS='--max-length 6'`")
    assert (
        "green at maximum length 6 — deep-replay-green.json stays at maximum length 5"
        in capsys.readouterr().out
    )
    assert deep_replay.main(["--all", "--max-length", "6", "--threads", "1"]) == 0
    assert ac.deep_replay_status(bench, 6) == ("current", "maximum length 6")


def test_a_bare_walk_deeper_than_the_record_walks_every_text_and_raises_it(bench, monkeypatch, capsys):
    """A bare walk deeper than the record walks every text although nothing moved, says why, and records every rune at its depth, after which a bare walk at or below that depth finds nothing moved. A red one deletes the record, as a red `--all` walk does."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
    walked: list = []
    _stub_walk(monkeypatch, walked)
    assert deep_replay.main(["--max-length", "6", "--threads", "1"]) == 0
    assert walked == [(6, None)]
    assert "the record holds maximum length 5, so raising it to 6 walks every text" in capsys.readouterr().out
    assert ac.deep_replay_status(bench, 6) == ("current", "maximum length 6")
    assert deep_replay.main(["--max-length", "6", "--threads", "1"]) == 0
    assert deep_replay.main(["--threads", "1"]) == 0
    assert walked == [(6, None)]
    _stub_walk(monkeypatch, walked, disagree="replay disagreement at position 1 of qsPea qsIt")
    assert deep_replay.main(["--max-length", "7", "--threads", "1"]) == 1
    assert walked == [(6, None), (7, None)]
    assert ac.read_green_record(path) is None


def test_a_bare_walk_deeper_than_the_record_walks_every_text_when_runes_moved(bench, monkeypatch, capsys):
    """With a rune moved, a bare walk deeper than the record walks every text rather than the moved runes' texts, and records every rune at its depth. A record without a maximum length counts as shallower than any walk."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
    moved = {**RUNES, "qsPea": "p2"}
    monkeypatch.setattr(deep_replay.fingerprint, "rune_digests", lambda root: dict(moved))
    walked: list = []
    _stub_walk(monkeypatch, walked)
    assert deep_replay.main(["--max-length", "6", "--threads", "1"]) == 0
    assert walked == [(6, None)]
    record = ac.read_green_record(path)
    assert record is not None and record["files"] == moved and record["max_length"] == 6
    path.write_text(json.dumps({"fingerprint": "f", "structure": "structure-1", "files": moved}))
    assert deep_replay.main(["--threads", "1"]) == 0
    assert walked == [(6, None), (5, None)]
    assert (
        "the record holds no maximum length, so raising it to 5 walks every text" in capsys.readouterr().out
    )


def test_a_shallower_walk_or_sweep_over_unchanged_runes_keeps_a_deeper_record(bench, monkeypatch, capsys):
    """A walk or a deep sweep's refresh shallower than the record keeps the recorded depth when every rune it covers has the digest and structure stamp the record holds, and the green line says so. A changed rune or structure stamp records the shallower depth, since the deeper walk never settled what changed."""
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    monkeypatch.setattr("rebuild.pipeline.spec_load.load_default_spec", lambda: Spec())
    ac.record_deep_replay_green(dict(RUNES), 6, "structure-1", path=path, imports="imports-1")
    _stub_walk(monkeypatch)
    assert deep_replay.main(["--all", "--threads", "1"]) == 0
    assert ac.deep_replay_status(bench, 6) == ("current", "maximum length 6")
    assert (
        "green at maximum length 5 — deep-replay-green.json keeps maximum length 6" in capsys.readouterr().out
    )
    assert deep_sweep.refresh_deep_replay(5, dict(RUNES)) == 6
    assert deep_sweep.refresh_deep_replay(5, {**RUNES, "qsIt": "i2"}) == 5
    ac.record_deep_replay_green(dict(RUNES), 6, "structure-1", path=path, imports="imports-1")
    monkeypatch.setattr(deep_replay.run_m1, "replay_structure_stamp", lambda spec: "structure-2")
    assert deep_replay.main(["--all", "--threads", "1"]) == 0
    assert ac.deep_replay_status(bench, 6)[0] == "due"


def test_a_deeper_record_is_current_and_a_shallower_one_is_due(bench, monkeypatch):
    """A record at or past the depth asked about is current. A due status names the bare walk at the depth asked about, which clears it whether or not the record reaches that depth, since a bare walk past the record's depth walks every text."""
    ac.record_deep_replay_green(
        dict(RUNES),
        6,
        "structure-1",
        path=bench / "rebuild" / "out" / "deep-replay-green.json",
        imports="imports-1",
    )
    monkeypatch.setattr("rebuild.pipeline.fingerprint.rune_digests", lambda root: dict(RUNES))
    assert ac.deep_replay_status(bench, 5) == ("current", "maximum length 6")
    status, note = ac.deep_replay_status(bench, 7)
    assert status == "due" and note.endswith("run `make replay-deep ARGS='--max-length 7'`")
    monkeypatch.setattr("rebuild.pipeline.fingerprint.rune_digests", lambda root: {**RUNES, "qsPea": "p2"})
    assert ac.deep_replay_status(bench, 5)[1].endswith("run `make replay-deep`")
    assert ac.deep_replay_status(bench, 6)[1].endswith("run `make replay-deep ARGS='--max-length 6'`")
    assert ac.deep_replay_status(bench, 7)[1].endswith("run `make replay-deep ARGS='--max-length 7'`")


def test_moved_imported_windows_walk_every_text(bench, monkeypatch, capsys):
    """Each configuration's table takes in the windows the others keep live, so a rune edit can reshape a configuration's rules for texts that name no edited rune (`run_m1.imports_digest`). When the tables' imported windows differ from the record's, the status is due though no rune moved, a bare walk covers every text and records the new windows, and a family walk keeps no claim the record made for another rune."""
    walked: list = []
    _stub_walk(monkeypatch, walked)
    path = bench / "rebuild" / "out" / "deep-replay-green.json"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
    assert ac.deep_replay_status(bench, 5)[0] == "current"
    IMPORTS["digest"] = "imports-2"
    status, note = ac.deep_replay_status(bench, 5)
    assert status == "due" and "import" in note and note.endswith("which walks every text")
    assert deep_replay.main(["--families", "qsTea", "--threads", "1"]) == 0
    record = ac.read_green_record(path)
    assert record is not None and record["files"] == {"qsTea": "t1"} and record["imports"] == "imports-2"
    ac.record_deep_replay_green(dict(RUNES), 5, "structure-1", path=path, imports="imports-1")
    assert deep_replay.main(["--threads", "1"]) == 0
    assert walked[-1] == (5, None)
    assert "covers every text" in capsys.readouterr().out
    record = ac.read_green_record(path)
    assert record is not None and record["files"] == RUNES and record["imports"] == "imports-2"
    assert ac.deep_replay_status(bench, 5) == ("current", "maximum length 5")


def test_the_width_is_the_machines_memory_capped_at_its_cores_or_the_stated_knob(monkeypatch):
    monkeypatch.delenv("AMS_DEEP_REPLAY_THREADS", raising=False)
    assert deep_replay.replay_threads(total_bytes=deep_replay.DEEP_REPLAY_PEAK_BYTES, ncores=18) == 1
    assert deep_replay.replay_threads(total_bytes=deep_replay.DEEP_REPLAY_PEAK_BYTES * 40, ncores=7) == 7
    monkeypatch.setenv("AMS_DEEP_REPLAY_THREADS", "2")
    assert deep_replay.replay_threads() == 2
    monkeypatch.setenv("AMS_DEEP_REPLAY_THREADS", "2GB")
    with pytest.raises(RuntimeError):
        deep_replay.replay_threads()


def test_the_memo_ceiling_is_the_stated_knob_or_the_checked_in_default(monkeypatch):
    """The ceiling is `AMS_DEEP_REPLAY_MEMO_WINDOWS` when set and `DEEP_REPLAY_MEMO_WINDOWS` otherwise. A set value that is not a bare decimal count of at least one raises an error naming the variable instead of falling back to the default."""
    monkeypatch.delenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", raising=False)
    assert deep_replay.replay_memo_windows() == deep_replay.DEEP_REPLAY_MEMO_WINDOWS
    monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", "2000000")
    assert deep_replay.replay_memo_windows() == 2_000_000
    for stated in ("2M", "0", "-5", "", "2.5e6"):
        monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", stated)
        with pytest.raises(RuntimeError, match="AMS_DEEP_REPLAY_MEMO_WINDOWS"):
            deep_replay.replay_memo_windows()


def test_a_walk_at_the_checked_in_ceiling_records_each_configurations_highest_unit_peak(bench, monkeypatch):
    """A green walk records one `deep-replay-walk` pool record, one observation per settlement configuration, the highest crate peak among its units, at the width its units walked at, which is capped at the unit count. A walk at another ceiling records nothing, because `DEEP_REPLAY_PEAK_BYTES` does not estimate it."""
    from rebuild.tools import cycle_timings

    _stub_walk(monkeypatch, peak=10_000_000_000)
    assert deep_replay.main(["--families", "qsTea", "--threads", "2"]) == 0
    monkeypatch.setenv("AMS_DEEP_REPLAY_MEMO_WINDOWS", "2000000")
    assert deep_replay.main(["--families", "qsTea", "--threads", "2"]) == 0
    monkeypatch.delenv("AMS_DEEP_REPLAY_MEMO_WINDOWS")
    assert deep_replay.main(["--families", "qsTea", "--threads", "400"]) == 0
    records = cycle_timings.load_pool_records(cycle_timings.JOURNAL)
    per_config = {config: 10_000_000_000 for config in conform.SETTLEMENT_CONFIGS}
    assert [(r["unit"], r["width"], r["worker_peak_rss_bytes"]) for r in records] == [
        ("deep-replay-walk", 2, per_config),
        ("deep-replay-walk", len(conform.SETTLEMENT_CONFIGS) * len(ALPHABET), per_config),
    ]


def test_the_units_partition_every_text_and_each_configuration_merges_its_own(bench, monkeypatch, capsys):
    """A walk over every text runs one unit per settlement configuration and alphabet symbol, each once, at the stated width, and each configuration's line sums its own units: every text of its lengths, one window settle a unit."""
    calls: list = []
    _stub_walk(monkeypatch, calls=calls)
    assert deep_replay.main(["--all", "--threads", "3"]) == 0
    assert sorted(calls) == sorted(
        (config, symbol) for config in conform.SETTLEMENT_CONFIGS for symbol in ALPHABET
    )
    out = capsys.readouterr().out
    units = len(conform.SETTLEMENT_CONFIGS) * len(ALPHABET)
    assert f"in {units} units of one configuration and one last symbol, 3 at a time" in out
    for config in conform.SETTLEMENT_CONFIGS:
        assert (
            f"deep replay[{config}]: {every_text(5)} texts, {len(ALPHABET)} window settles, 0 skipped" in out
        )


def test_units_that_miss_a_text_record_nothing(bench, monkeypatch):
    """A configuration whose units do not sum to every text of its lengths stops the walk before anything is recorded, so a lost unit cannot pass as a green."""
    _stub_walk(monkeypatch, short=(conform.SETTLEMENT_CONFIGS[-1], ALPHABET[-1]))
    with pytest.raises(RuntimeError, match=f"not every one of the {every_text(5)} texts"):
        deep_replay.main(["--all", "--threads", "2"])
    assert JOURNAL == []
    assert ac.read_green_record(bench / "rebuild" / "out" / "deep-replay-green.json") is None


def test_a_disagreeing_unit_stops_the_units_not_yet_started(bench, monkeypatch, capsys):
    """A disagreement in one unit starts none of the units queued behind it, and the walk reports the disagreement and records the red."""
    calls: list = []
    second = (conform.SETTLEMENT_CONFIGS[0], ALPHABET[1])
    _stub_walk(
        monkeypatch,
        calls=calls,
        disagree="replay disagreement at position 1 of qsTea qsPea",
        disagree_on=second,
    )
    assert deep_replay.main(["--all", "--threads", "1"]) == 1
    assert calls == [(conform.SETTLEMENT_CONFIGS[0], ALPHABET[0]), second]
    assert JOURNAL == [("red", ["--all", "--threads", "1"])]
    assert "position 1 of qsTea qsPea" in capsys.readouterr().err


def test_the_shipped_walk_cost_walks_more_units_than_configurations_on_both_fleet_machines(monkeypatch):
    """Each fleet machine (`doc/fleet.md`), 48 GiB with 18 or 12 cores, walks as many units at once as its memory holds at the checked-in `DEEP_REPLAY_PEAK_BYTES`, capped at its cores, and that is more than one walk per settlement configuration. No cycle runs this walk, so the deep-replay-walk row of `make job-costs` watches the constant only when someone runs it. This test fails at 7.26 GB or more, where the 48 GiB machines would walk no more units at once than there are configurations."""
    monkeypatch.delenv("AMS_DEEP_REPLAY_THREADS", raising=False)
    for cores in (18, 12):
        width = deep_replay.replay_threads(total_bytes=MACHINE_48_GIB, ncores=cores)
        assert width == memory_budget.how_many_fit(
            deep_replay.DEEP_REPLAY_PEAK_BYTES, cap=cores, total_bytes=MACHINE_48_GIB
        )
        assert width > len(conform.SETTLEMENT_CONFIGS)


def test_a_green_deep_sweep_refreshes_the_replays_record(tmp_path, monkeypatch):
    """The deep HarfBuzz sweep over all texts settles every text it shapes, so a passing sweep at the replay's maximum length covers the replay. Its refresh records every rune at the digest the sweep read before it started."""
    monkeypatch.setattr(
        cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "rebuild" / "out" / "deep-replay-green.json"
    )
    monkeypatch.setattr(deep_sweep.run_m1, "replay_structure_stamp", lambda spec: "structure-1")
    monkeypatch.setattr("rebuild.pipeline.spec_load.load_default_spec", lambda: Spec())
    monkeypatch.setattr(deep_sweep, "tables_imports_digest", lambda root=None: "imports-1")
    deep_sweep.refresh_deep_replay(6, dict(RUNES))
    record = ac.read_green_record(tmp_path / "rebuild" / "out" / "deep-replay-green.json")
    assert record is not None and record["files"] == RUNES and record["max_length"] == 6
    assert record["structure"] == "structure-1"
    assert record["imports"] == "imports-1"
