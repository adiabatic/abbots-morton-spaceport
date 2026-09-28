"""Tests for the on-demand deep sweep's decisions, with the sweep stubbed out: which inputs it refuses, what it records on a pass, what it clears on a failure, the per-edit sweep's green record it writes, the width it runs at, the window bound that width comes from, the progress report's interval, estimate, and lines, and how each worker's count reaches the parent's reports. Each configuration's sweep is `conform.conformance_config_worker`, which the font-facing gates already exercise."""

import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest

from rebuild.pipeline import conform
from rebuild.pipeline.model import ResolvedSpec
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import console, cycle_paths, deep_sweep, memory_budget

MACHINE_48_GIB = 51_539_607_552
MACHINE_32_GIB = 34_359_738_368
CHECKS: list = []


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A stub repo root with a behavior-class sidecar and the compile code files, a tables stamp that counts as current, `AMS_DEEP_SWEEP_JOBS` and `AMS_DEEP_SWEEP_REPORT_SECONDS` unset whatever the developer's shell sets, and every green record and journal line redirected so nothing touches rebuild/out. The journal lines land in `CHECKS`."""
    from rebuild.pipeline.emit_gsub import BEHAVIOR_CLASSES_FORMAT

    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    (m1 / "behavior_classes.json").write_text(
        json.dumps({"format": BEHAVIOR_CLASSES_FORMAT, "classes": ["namer-dot", "settle:bk0-la2"]})
    )
    for rel in ac.COMPILE_CODE_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")
    monkeypatch.setattr(deep_sweep, "ROOT", tmp_path)
    monkeypatch.setattr(cycle_paths, "DEEP_SWEEP_GREEN", tmp_path / "deep-sweep-green.json")
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", tmp_path / "conform-green.json")
    monkeypatch.setattr(deep_sweep, "tables_stamped", lambda: True)
    monkeypatch.setattr(cycle_paths, "DEEP_REPLAY_GREEN", tmp_path / "deep-replay-green.json")
    monkeypatch.setattr(deep_sweep, "refresh_deep_replay", lambda max_length, runes: None)
    monkeypatch.delenv(deep_sweep.JOBS_ENV, raising=False)
    monkeypatch.delenv(deep_sweep.REPORT_ENV, raising=False)
    CHECKS.clear()
    monkeypatch.setattr(deep_sweep, "record_check", lambda result, **kw: CHECKS.append((result.outcome, kw)))
    return tmp_path


PEAKS = {config: 1_000 + index for index, config in enumerate(conform.ACCEPTANCE_CONFIGS)}


def _stub_plan(monkeypatch, windows=1_000):
    monkeypatch.setattr(
        deep_sweep,
        "plan_sweep",
        lambda max_length: deep_sweep.SweepPlan(
            spec=cast(ResolvedSpec, None), glyphs={}, windows=windows, bound_seconds=0.0
        ),
    )


def _stub_sweep(monkeypatch, summary, swept=None):
    _stub_plan(monkeypatch)

    def fake(plan, max_length, jobs, report_every):
        if swept is not None:
            swept.append((max_length, jobs, report_every))
        return summary, dict(PEAKS)

    monkeypatch.setattr(deep_sweep, "run_sweep", fake)


def test_a_root_without_a_behavior_class_sidecar_is_refused(bench, monkeypatch):
    (bench / "rebuild" / "out" / "m1" / "behavior_classes.json").unlink()
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    with pytest.raises(SystemExit, match="behavior-class sidecar"):
        deep_sweep.main([])


def test_a_stale_tables_stamp_is_refused(bench, monkeypatch):
    """The sweep requires tables stamped from the sources on disk, not a green record. A --gates-only pass can leave a sweepable font without recording a green, and a failed interactive run deletes the green record without changing the artifacts."""
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    monkeypatch.setattr(deep_sweep, "tables_stamped", lambda: False)
    with pytest.raises(SystemExit, match="stale relative to the runes"):
        deep_sweep.main([])


def test_tables_stamped_asks_the_enumerations_own_stamp(monkeypatch):
    calls: list = []
    monkeypatch.setattr(deep_sweep.run_m1, "tables_inputs", lambda: "stamp")
    monkeypatch.setattr(
        deep_sweep.run_m1,
        "serialized_tables",
        lambda out_dir, inputs: calls.append((out_dir, inputs)),
    )
    assert deep_sweep.tables_stamped() is False
    assert calls == [(deep_sweep.run_m1.OUT_DIR, "stamp")]
    monkeypatch.setattr(deep_sweep.run_m1, "serialized_tables", lambda out_dir, inputs: {})
    assert deep_sweep.tables_stamped() is True


def test_a_max_length_below_the_per_edit_sweep_is_refused(bench, monkeypatch):
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    with pytest.raises(SystemExit, match="shorter than the per-edit sweep"):
        deep_sweep.main(["--max-length", str(ac.CONFORM_MAX_LENGTH_DEFAULT - 1)])
    assert swept == []


def test_a_green_run_records_its_max_length_and_hands_the_per_edit_sweep_its_green(bench, monkeypatch):
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    assert deep_sweep.main(["--max-length", "6", "--jobs", "3"]) == 0
    assert swept == [(6, 3, deep_sweep.REPORT_SECONDS_DEFAULT)]
    record = ac.read_green_record(bench / "deep-sweep-green.json")
    assert record is not None
    assert record["max_length"] == 6
    assert record["fingerprint"] == ac.deep_sweep_skip_fingerprint(bench)
    assert "class:namer-dot" in record["files"]
    per_edit = ac.read_green_record(bench / "conform-green.json")
    assert per_edit is not None
    assert per_edit["fingerprint"] == ac.conform_skip_fingerprint(bench, ac.CONFORM_MAX_LENGTH_DEFAULT)


def test_a_red_run_records_nothing_and_clears_a_contradicted_green(bench, monkeypatch):
    fingerprint = ac.deep_sweep_skip_fingerprint(bench)
    assert fingerprint is not None
    ac.record_deep_sweep_green(fingerprint, 5, path=bench / "deep-sweep-green.json")
    _stub_sweep(monkeypatch, {"pass": False, "divergences": 2})
    assert deep_sweep.main([]) == 1
    assert ac.read_green_record(bench / "deep-sweep-green.json") is None
    assert ac.read_green_record(bench / "conform-green.json") is None


def test_a_build_finishing_mid_sweep_records_nothing(bench, monkeypatch, capsys):
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    real = ac.deep_sweep_skip_fingerprint
    calls = [0]

    def moving(root=bench):
        calls[0] += 1
        return f"{real(root)}-{calls[0]}"

    monkeypatch.setattr(deep_sweep, "deep_sweep_skip_fingerprint", moving)
    assert deep_sweep.main([]) == 0
    assert ac.read_green_record(bench / "deep-sweep-green.json") is None
    assert "inputs changed while it ran" in capsys.readouterr().out


def _stub_runes_and_sweep(monkeypatch, edit_mid_sweep):
    current = {"qsPea": "p1"}
    monkeypatch.setattr("rebuild.pipeline.fingerprint.rune_digests", lambda root: dict(current))

    def fake(plan, max_length, jobs, report_every):
        if edit_mid_sweep:
            current["qsPea"] = "p2"
        return {"pass": True, "divergences": 0}, dict(PEAKS)

    _stub_plan(monkeypatch)
    monkeypatch.setattr(deep_sweep, "run_sweep", fake)
    refreshed: list = []
    monkeypatch.setattr(deep_sweep, "refresh_deep_replay", lambda *args: refreshed.append(args))
    return refreshed


def test_a_green_sweep_at_the_replay_max_length_refreshes_the_deep_replay_with_the_runes_it_swept(
    bench, monkeypatch, capsys
):
    refreshed = _stub_runes_and_sweep(monkeypatch, edit_mid_sweep=False)
    assert deep_sweep.main(["--max-length", str(ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT)]) == 0
    assert refreshed == [(ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT, {"qsPea": "p1"})]
    assert "deep replay: green too" in capsys.readouterr().out


def test_a_rune_edited_mid_sweep_leaves_the_deep_replay_unrecorded(bench, monkeypatch, capsys):
    """The swept font was built from the runes read before the sweep started. A rune edit that adds no behavior class leaves the sweep's own key alone, but the deep replay's record keys on rune digests, so it is refreshed only with that snapshot and only while the runes on disk still match it."""
    refreshed = _stub_runes_and_sweep(monkeypatch, edit_mid_sweep=True)
    assert deep_sweep.main(["--max-length", str(ac.DEEP_REPLAY_MAX_LENGTH_DEFAULT)]) == 0
    assert refreshed == []
    assert "runes changed while the sweep ran" in capsys.readouterr().out
    assert ac.read_green_record(bench / "deep-sweep-green.json") is not None


def test_status_exits_on_whether_the_sweep_is_current(bench, monkeypatch, capsys):
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    assert deep_sweep.main(["--status"]) == 1
    assert "never-run" in capsys.readouterr().out
    deep_sweep.main(["--max-length", "5"])
    assert deep_sweep.main(["--status"]) == 0
    assert "current" in capsys.readouterr().out
    assert deep_sweep.main(["--status", "--max-length", "7"]) == 1


def test_the_window_bound_counts_every_ask_with_every_left_it_can_have():
    """`window_bound` over two one-character letters, whose families have three and two cells, and one boundary. At maximum length 2 the walk can hold 14 windows: the 2 one-letter texts; at position 0 of a two-character text, 4 letter pairs and 2 letter-boundary pairs; and at position 1, 2 letters after the boundary and each of 2 letters after each of 2 letters, whose settled cells differ by family. At maximum length 3 a letter with a letter before it can settle to at most its family's cells, which caps the count below what its lefts alone allow. A two-character ligature token adds the one window of the text it spans alone."""
    letters = {"a": 1, "b": 1}
    cells = {"a": 3, "b": 2}
    assert deep_sweep.window_bound(2, letters, 1, cells) == 14
    assert deep_sweep.window_bound(3, letters, 1, cells) == 50
    assert deep_sweep.window_bound(3, letters, 1, {}) == 56
    assert deep_sweep.window_bound(2, {**letters, "ab": 2}, 1, {**cells, "ab": 1}) == 15


def test_both_fleet_machines_sweep_one_settlement_configuration_at_a_time_at_the_default_maximum_length():
    """At the length-5 window bound on the alphabet with ·Ye (`settlement_window_bound`: 110,020,785 windows), both fleet machines (`doc/fleet.md`) sweep one settlement configuration at a time, the overlay first in that slot. On the 48 GiB machines that one worker's estimate fits the memory less the reserve and two do not. On the 32 GiB machine even one does not, the floor at one decides, and `memory_shortfall` warns without refusing, since one worker still fits the machine's memory in all. At the length-4 bound (3,304,968 windows) every machine sweeps every configuration at once, the overlay in a slot of its own. The suite does not catch a per-window cost that is too low: only a real run's check line, which records each worker's peak beside its estimate, does."""
    length_5 = deep_sweep.settlement_worker_bytes(110_020_785)
    budget_48 = MACHINE_48_GIB - memory_budget.os_reserve_bytes(total_bytes=MACHINE_48_GIB)
    assert length_5 + deep_sweep.DEEP_SWEEP_BASE_BYTES <= budget_48 < 2 * length_5
    for cores in (18, 12):
        assert deep_sweep.sweep_width(length_5, ncores=cores, total_bytes=MACHINE_48_GIB) == 1
    assert deep_sweep.sweep_width(length_5, ncores=10, total_bytes=MACHINE_32_GIB) == 1
    assert "floored at one" in deep_sweep.sweep_width_derivation(
        length_5, ncores=10, total_bytes=MACHINE_32_GIB
    )
    assert deep_sweep.memory_shortfall(length_5, total_bytes=MACHINE_48_GIB) is None
    shortfall = deep_sweep.memory_shortfall(length_5, total_bytes=MACHINE_32_GIB)
    assert shortfall is not None and not shortfall[0] and "may swap" in shortfall[1]
    length_4 = deep_sweep.settlement_worker_bytes(3_304_968)
    for cores, total in ((18, MACHINE_48_GIB), (12, MACHINE_48_GIB), (10, MACHINE_32_GIB)):
        assert deep_sweep.sweep_width(length_4, ncores=cores, total_bytes=total) == len(
            conform.ACCEPTANCE_CONFIGS
        )


def test_the_overlay_gets_a_slot_of_its_own_only_beside_every_settlement_worker():
    """The overlay's worker holds far less than a settlement worker, so it is not sized as one. It gets a slot of its own when every settlement configuration fits beside it and a core is left over. Otherwise it runs first in a settlement worker's slot, and its need is still subtracted, so a machine with room for five settlement workers but not for the overlay beside them runs four."""
    roomy = 1_000_000_000_000
    need = 10_000_000_000
    settling = len(conform.SETTLEMENT_CONFIGS)
    assert deep_sweep.sweep_width(need, ncores=18, total_bytes=roomy) == settling + 1
    assert "slot of its own" in deep_sweep.sweep_width_derivation(need, ncores=18, total_bytes=roomy)
    assert deep_sweep.sweep_width(need, ncores=settling, total_bytes=roomy) == settling
    total = 100_000_000_000
    room = total - memory_budget.os_reserve_bytes(total_bytes=total) - deep_sweep.DEEP_SWEEP_BASE_BYTES
    assert deep_sweep.sweep_width(room // settling, ncores=18, total_bytes=total) == settling + 1
    assert deep_sweep.sweep_width(room // settling + 1, ncores=18, total_bytes=total) == settling - 1
    assert "first in one of their slots" in deep_sweep.sweep_width_derivation(
        room // settling + 1, ncores=18, total_bytes=total
    )


def test_a_stated_width_replaces_the_derived_one(bench, monkeypatch, capsys):
    """`AMS_DEEP_SWEEP_JOBS` states the width and `--jobs` overrides it; either is narrowed to the acceptance configuration count and checked against nothing else. A value that is not a bare count raises an error naming the variable before anything is swept. With neither, the width is the one this machine's memory fits."""
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    monkeypatch.setenv(deep_sweep.JOBS_ENV, "2")
    assert deep_sweep.main([]) == 0
    assert f"at 2 jobs stated by {deep_sweep.JOBS_ENV}" in capsys.readouterr().out
    assert deep_sweep.main(["--jobs", "3"]) == 0
    assert "at 3 jobs stated by --jobs" in capsys.readouterr().out
    assert deep_sweep.main(["--jobs", "9"]) == 0
    assert [jobs for _, jobs, _ in swept] == [2, 3, len(conform.ACCEPTANCE_CONFIGS)]
    monkeypatch.setenv(deep_sweep.JOBS_ENV, "2GB")
    with pytest.raises(RuntimeError, match=deep_sweep.JOBS_ENV):
        deep_sweep.main([])
    assert len(swept) == 3
    monkeypatch.delenv(deep_sweep.JOBS_ENV)
    assert deep_sweep.main([]) == 0
    assert swept[-1][1] == deep_sweep.sweep_width(deep_sweep.settlement_worker_bytes(1_000))


def test_each_run_records_every_workers_peak_beside_its_estimate(bench, monkeypatch):
    """Green or red, a run writes one `conform-deep` check line with each configuration worker's peak footprint and the need its width was derived from: the settlement worker's estimate for a settlement configuration, and the base for the overlay."""
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0})
    assert deep_sweep.main([]) == 0
    _stub_sweep(monkeypatch, {"pass": False, "divergences": 2})
    assert deep_sweep.main([]) == 1
    assert [outcome for outcome, _ in CHECKS] == ["green", "red"]
    estimate = deep_sweep.settlement_worker_bytes(1_000)
    for _, kw in CHECKS:
        assert kw["worker_peak_footprint_bytes"] == PEAKS
        assert kw["worker_estimate_bytes"] == {
            config: estimate if config in conform.SETTLEMENT_CONFIGS else deep_sweep.DEEP_SWEEP_BASE_BYTES
            for config in conform.ACCEPTANCE_CONFIGS
        }


def test_a_length_whose_one_worker_exceeds_the_machine_is_refused_unless_a_width_is_stated(
    bench, monkeypatch, capsys
):
    """At the length-6 window bound (2,251,636,269 windows) one settlement worker is estimated far past either fleet machine's memory in all, so `memory_shortfall` refuses it. `main` refuses such a run before sweeping anything and names the way to run it anyway; a stated width starts it with the warning instead."""
    length_6 = deep_sweep.settlement_worker_bytes(2_251_636_269)
    for total in (MACHINE_48_GIB, MACHINE_32_GIB):
        shortfall = deep_sweep.memory_shortfall(length_6, total_bytes=total)
        assert shortfall is not None and shortfall[0]
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    _stub_plan(monkeypatch, windows=10**15)
    with pytest.raises(SystemExit, match=deep_sweep.JOBS_ENV):
        deep_sweep.main([])
    assert swept == []
    assert deep_sweep.main(["--jobs", "1"]) == 0
    assert "would swap for its whole length" in capsys.readouterr().out
    assert [jobs for _, jobs, _ in swept] == [1]


def test_the_report_interval_is_twenty_minutes_unless_its_variable_states_one(bench, monkeypatch, capsys):
    """`AMS_DEEP_SWEEP_REPORT_SECONDS` states the interval in decimal seconds, so a debugging run can report every few seconds, and the plan line names the interval. A value that is not a positive, finite number of seconds raises an error naming the variable before anything is swept."""
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    assert deep_sweep.main([]) == 0
    assert "a progress report every 20m00s" in capsys.readouterr().out
    monkeypatch.setenv(deep_sweep.REPORT_ENV, "2.5")
    assert deep_sweep.main([]) == 0
    assert "a progress report every 2.5s" in capsys.readouterr().out
    for stated in ("0", "-60", "20m", "nan", "inf"):
        monkeypatch.setenv(deep_sweep.REPORT_ENV, stated)
        with pytest.raises(RuntimeError, match=deep_sweep.REPORT_ENV):
            deep_sweep.main([])
    assert [every for _, _, every in swept] == [1200.0, 2.5]


def test_the_estimate_places_each_queued_configuration_in_the_slot_that_frees_first():
    """Two slots, running configurations with 100 and 300 seconds left, and two queued ones taking 200 and 50: the first queued one takes the slot freed at 100 and ends at 300, and the second takes a slot freed at 300 and ends at 350. A free slot starts a queued configuration at once."""
    assert deep_sweep.schedule_finish({"a": 100.0, "b": 300.0}, [("c", 200.0), ("d", 50.0)], 2) == {
        "a": 100.0,
        "b": 300.0,
        "c": 300.0,
        "d": 350.0,
    }
    assert deep_sweep.schedule_finish({"a": 100.0}, [("c", 200.0)], 2) == {"a": 100.0, "c": 200.0}


def _two_round_sweep() -> deep_sweep.SweepProgress:
    """Two slots over an overlay of 1,000 texts and three settlement configurations of 100,000, as read up to 205 seconds in. The overlay ran from 5 to 50 seconds, between two readings, so only its worker's own start dates it. `default` started at 5 and ran at 160 texts a second, then 40, then 100 over its last tenth of the texts. `ss03` took the overlay's slot at 55 and has run at 100 a second. `ss04` waits for the second round."""
    progress = deep_sweep.SweepProgress(
        {"ss10": 1_000, "default": 100_000, "ss03": 100_000, "ss04": 100_000}, ("ss10",), 2, 0.0
    )
    progress.observe("default", 0, 5.0)
    progress.finish("ss10", 50.0, 1_000, began=5.0)
    progress.observe("ss03", 0, 55.0)
    for moment, default, ss03 in ((55.0, 8_000, 0), (105.0, 10_000, 5_000), (205.0, 20_000, 15_000)):
        progress.observe("default", default, moment)
        progress.observe("ss03", ss03, moment)
    return progress


def test_the_estimate_runs_each_configuration_at_its_own_rate_and_the_queue_at_its_kinds():
    """`default`'s rate is taken over its last tenth of the texts, 100 a second, not its whole run's, and leaves it 800 seconds; `ss03` has 850 left at its own 100. Projected at those rates, both whole walks take 1,000 seconds, so the settlement configurations run at 100 texts a second, and the queued `ss04` takes 1,000 seconds from the slot `default` frees at 800, ending the sweep at 1,800. Once `default` finishes at 1,005 and `ss04` starts in its slot, `ss04` has shaped less than a tenth of its texts at a rate its worker's setup still slows, so it takes the settlement rate: `default`'s whole walk and `ss03`'s projected one."""
    progress = _two_round_sweep()
    finishes, total = progress.estimate(205.0)
    assert finishes == pytest.approx({"default": 800.0, "ss03": 850.0, "ss04": 1_800.0})
    assert total == pytest.approx(1_800.0)
    progress.observe("ss03", 95_000, 1_005.0)
    progress.finish("default", 1_005.0, 100_000)
    progress.observe("ss04", 250, 1_010.0, began=1_005.0)
    finishes, total = progress.estimate(1_010.0)
    kind = 200_000 / (1_000.0 + (1_010.0 - 55.0 + 50.0))
    assert finishes == pytest.approx({"ss03": 50.0, "ss04": 99_750 / kind})
    assert total == finishes["ss04"]
    fresh = deep_sweep.SweepProgress({"default": 100_000, "ss03": 100_000}, (), 1, 0.0)
    fresh.observe("default", 0, 5.0)
    assert fresh.estimate(10.0) == ({"default": None, "ss03": None}, None)


def test_a_report_is_a_counter_line_with_one_line_per_configuration_after_it():
    """The report's first line is a `[progress]` counter over every configuration's texts that `console.parse_line` reads, with the elapsed time, the rate, the finish, the workers' footprint and the swap in use riding on it; one plain line per configuration follows in submission order. The next report's rates run from this one."""
    progress = _two_round_sweep()
    wall = 1_790_000_000.0

    def at(seconds: float) -> str:
        return time.strftime("%a %H:%M", time.localtime(wall + seconds))

    lines = progress.report(205.0, wall, {"default": 12_000_000_000, "ss03": 6_500_000_000}, 1_250_000_000)
    assert lines == [
        f"[progress] 36000/301000 texts, 3m25s elapsed, 176 texts/s since the start, finishing in 30m00s, at {at(1_800)}, 2 workers hold 18.50 GB, 1.25 GB of swap in use",
        "deep sweep[ss10]: 1,000/1,000 texts, finished in 45.0s",
        f"deep sweep[default]: 20,000/100,000 texts, running for 3m20s, 100 texts/s since it started, finishing in 13m20s, at {at(800)}, holding 12.00 GB",
        f"deep sweep[ss03]: 15,000/100,000 texts, running for 2m30s, 100 texts/s since it started, finishing in 14m10s, at {at(850)}, holding 6.50 GB",
        f"deep sweep[ss04]: 0/100,000 texts, queued, finishing in 30m00s, at {at(1_800)}",
    ]
    event = console.parse_line(lines[0])
    assert isinstance(event, console.Progress) and (event.done, event.total) == (36_000, 301_000)
    assert all(console.parse_line(line) is None for line in lines[1:])
    progress.observe("default", 25_000, 305.0)
    progress.observe("ss03", 25_000, 305.0)
    later = progress.report(305.0, wall + 100, {"default": None, "ss03": 7_000_000_000}, None)
    assert "150 texts/s since the last report" in later[0]
    assert later[0].endswith("1 worker holds 7.00 GB, swap unreadable")
    assert "50 texts/s since the last report" in later[2] and later[2].endswith("footprint unreadable")


def test_a_worker_writes_its_start_pid_and_counts_into_its_own_slot_only(monkeypatch):
    """Outside a pool that attached the shared arrays a worker has nowhere to write, so it gets no writer. Inside one, it records its clock start and pid in its own slot, and its writer stores each count there, leaving every other slot alone."""
    monkeypatch.setattr(deep_sweep, "_COUNTERS", None)
    assert deep_sweep._counter_writer(1) is None
    context = multiprocessing.get_context("spawn")
    shaped, pids, began = context.RawArray("q", 3), context.RawArray("q", 3), context.RawArray("d", 3)
    deep_sweep._attach_counters(shaped, pids, began)
    before = time.time()
    store = deep_sweep._counter_writer(1)
    assert store is not None
    store(4_096)
    store(8_192)
    assert list(shaped) == [0, 8_192, 0]
    assert list(pids) == [0, os.getpid(), 0]
    assert began[0] == began[2] == 0.0 and before <= began[1] <= time.time()


def test_the_sweep_reports_the_counts_its_workers_store_on_the_report_interval(tmp_path, monkeypatch):
    """`run_sweep` driven through a thread pool in place of the spawn pool, with every configuration's worker storing half its texts and then waiting until the third report or a later one shows them all. Such a report counts the stored halves, reads each footprint by the pid its worker recorded, and dates each worker from the clock start it recorded, converted to the parent's clock. Reports come at most one to each interval of `report_every` from the start, never one per reading. Once the workers return, every configuration is finished with the texts it returned, and each walk's samples hold the half its slot showed."""
    monkeypatch.setattr(deep_sweep, "_COUNTERS", None)
    texts = {config: 1_000 * (index + 1) for index, config in enumerate(conform.ACCEPTANCE_CONFIGS)}
    halves = sum(count // 2 for count in texts.values())
    monkeypatch.setattr(deep_sweep.kernel_exec, "ensure_built", lambda: None)
    monkeypatch.setattr(deep_sweep.kernel_exec, "guard_sweep", lambda spec: {})
    monkeypatch.setattr(deep_sweep, "config_texts", lambda spec, max_length: dict(texts))
    monkeypatch.setattr(deep_sweep.run_m1, "OUT_DIR", tmp_path)
    footprint_pids: list[int] = []
    monkeypatch.setattr(
        deep_sweep.peak_rss, "footprint_bytes", lambda pid: footprint_pids.append(pid) or 1_000_000_000
    )
    monkeypatch.setattr(deep_sweep.peak_rss, "swap_used_bytes", lambda: None)
    release = threading.Event()
    started: dict[str, float] = {}

    def worker(spec, font_path, config, max_length, glyphs, guard_verdicts, slot):
        started[config] = time.monotonic()
        store = deep_sweep._counter_writer(slot)
        assert store is not None
        store(texts[config] // 2)
        release.wait(timeout=30)
        return conform.ConformanceConfigResult(config=config, sequences=texts[config]), 7

    class ThreadPool(ThreadPoolExecutor):
        def __init__(self, max_workers, mp_context, max_tasks_per_child, initializer, initargs):
            initializer(*initargs)
            super().__init__(max_workers=max_workers)

    sweeps: list[deep_sweep.SweepProgress] = []
    reports: list[tuple[float, list[str]]] = []

    class Recorded(deep_sweep.SweepProgress):
        def __init__(self, *args) -> None:
            super().__init__(*args)
            sweeps.append(self)

        def report(self, now, wall, footprints, swap):
            lines = super().report(now, wall, footprints, swap)
            reports.append((now, lines))
            event = console.parse_line(lines[0])
            if isinstance(event, console.Progress) and event.done == halves and len(reports) >= 3:
                release.set()
            return lines

    monkeypatch.setattr(deep_sweep, "ProcessPoolExecutor", ThreadPool)
    monkeypatch.setattr(deep_sweep, "_config_worker", worker)
    monkeypatch.setattr(deep_sweep, "SweepProgress", Recorded)
    plan = deep_sweep.SweepPlan(spec=cast(ResolvedSpec, None), glyphs={}, windows=0, bound_seconds=0.0)
    every = 0.2
    summary, peaks = deep_sweep.run_sweep(plan, 5, len(texts), report_every=every)
    assert release.is_set()
    assert summary["pass"] and peaks == {config: 7 for config in texts}
    (progress,) = sweeps
    intervals = [int((now - progress.started) // every) for now, _ in reports]
    assert intervals[0] >= 1 and intervals == sorted(set(intervals))
    shown = next(lines for _, lines in reports if f"{halves}/{sum(texts.values())} texts" in lines[0])
    assert f"{len(texts)} workers hold {len(texts)}.00 GB" in shown[0]
    assert all("running for" in line and line.endswith("holding 1.00 GB") for line in shown[1:])
    assert set(footprint_pids) == {os.getpid()}
    for config, walk in progress.walks.items():
        assert walk.first is not None and walk.first[0] == pytest.approx(started[config], abs=0.05)
        assert walk.finished is not None and walk.done == texts[config]
        assert any(done == texts[config] // 2 for _, done in walk.samples)
