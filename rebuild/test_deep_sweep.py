"""Tests for the on-demand deep sweep's decisions, with the sweep stubbed out: which inputs it refuses, what it records on a pass, what it clears on a failure, the per-edit sweep's green record it writes, the width it runs at, the window bound that width comes from, the units it splits the configurations into and how it merges them back, the progress report's interval, estimate, and lines, and how each unit's count reaches the parent's reports. Each unit's sweep is `conform.conformance_config_worker`, which the font-facing gates already exercise; `rebuild/test_conform.py` checks that the units partition a configuration's texts."""

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

TOY_ALPHABET = ("a", "b", "c")


def _plan(units: tuple[deep_sweep.SweepUnit, ...], windows: int = 1_000) -> deep_sweep.SweepPlan:
    return deep_sweep.SweepPlan(
        spec=cast(ResolvedSpec, None), glyphs={}, windows=windows, bound_seconds=0.0, units=units
    )


def _stub_plan(monkeypatch, windows=1_000):
    units = deep_sweep.sweep_units(TOY_ALPHABET, 5)
    monkeypatch.setattr(deep_sweep, "plan_sweep", lambda max_length: _plan(units, windows))


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


def test_both_fleet_machines_sweep_one_settlement_unit_at_a_time_at_the_default_maximum_length():
    """At the length-5 window bound on the alphabet with ·Ye (`settlement_window_bound`: 110,020,785 windows), both fleet machines (`doc/fleet.md`) sweep one unit at a time, the overlay first in that slot. On the 48 GiB machines that one unit's estimate fits the memory less the reserve and two do not. On the 32 GiB machine even one does not, the floor at one decides, and `memory_shortfall` warns without refusing, since one unit still fits the machine's memory in all. At the length-4 bound (3,304,968 windows) every machine sweeps as many units at once as there are settlement configurations. The suite does not catch a per-window cost that is too low: only a real run's check line, which records each configuration's highest unit peak beside its estimate, does."""
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
            conform.SETTLEMENT_CONFIGS
        )


def test_the_overlay_runs_in_a_settlement_units_slot_and_its_need_is_subtracted():
    """The overlay's worker holds far less than a settlement unit, so it is not sized as one, and it never gets a slot of its own: once it finishes, its slot takes a settlement unit, so a slot of its own would let one more settlement unit run than the memory fits. Its need is still subtracted, so a machine with room for five settlement units but not for the overlay beside them runs four."""
    roomy = 1_000_000_000_000
    need = 10_000_000_000
    settling = len(conform.SETTLEMENT_CONFIGS)
    assert deep_sweep.sweep_width(need, ncores=18, total_bytes=roomy) == settling
    assert deep_sweep.sweep_width(need, ncores=settling - 2, total_bytes=roomy) == settling - 2
    total = 100_000_000_000
    room = total - memory_budget.os_reserve_bytes(total_bytes=total) - deep_sweep.DEEP_SWEEP_BASE_BYTES
    assert deep_sweep.sweep_width(room // settling, ncores=18, total_bytes=total) == settling
    assert deep_sweep.sweep_width(room // settling + 1, ncores=18, total_bytes=total) == settling - 1
    assert "first in one of their slots" in deep_sweep.sweep_width_derivation(
        need, ncores=18, total_bytes=roomy
    )


def test_the_units_are_the_overlay_whole_then_each_settlement_configuration_by_last_symbol():
    """The pool takes the overlay first, whole, then each settlement configuration in order, one unit per alphabet symbol in the alphabet's order. A settlement unit shapes one text in every alphabet's worth of each length, so a configuration's units shape every text between them, and the overlay shapes its own two lengths."""
    units = deep_sweep.sweep_units(TOY_ALPHABET, 4)
    assert [(unit.config, unit.last) for unit in units] == [("ss10", None)] + [
        (config, symbol) for config in conform.SETTLEMENT_CONFIGS for symbol in TOY_ALPHABET
    ]
    assert units[0].overlay and units[0].texts == 3 + 9
    assert all(unit.texts == 1 + 3 + 9 + 27 and not unit.overlay for unit in units[1:])
    assert sum(unit.texts for unit in units if unit.config == "default") == 3 + 9 + 27 + 81


def test_a_stated_width_replaces_the_derived_one(bench, monkeypatch, capsys):
    """`AMS_DEEP_SWEEP_JOBS` states the width and `--jobs` overrides it; either is narrowed to the unit count and checked against nothing else. A value that is not a bare count raises an error naming the variable before anything is swept. With neither, the width is the one this machine's memory fits."""
    swept: list = []
    _stub_sweep(monkeypatch, {"pass": True, "divergences": 0}, swept)
    monkeypatch.setenv(deep_sweep.JOBS_ENV, "2")
    assert deep_sweep.main([]) == 0
    assert f"at 2 jobs stated by {deep_sweep.JOBS_ENV}" in capsys.readouterr().out
    assert deep_sweep.main(["--jobs", "3"]) == 0
    assert "at 3 jobs stated by --jobs" in capsys.readouterr().out
    assert deep_sweep.main(["--jobs", "9"]) == 0
    assert deep_sweep.main(["--jobs", "99"]) == 0
    units = len(deep_sweep.sweep_units(TOY_ALPHABET, 5))
    assert [jobs for _, jobs, _ in swept] == [2, 3, 9, units]
    monkeypatch.setenv(deep_sweep.JOBS_ENV, "2GB")
    with pytest.raises(RuntimeError, match=deep_sweep.JOBS_ENV):
        deep_sweep.main([])
    assert len(swept) == 4
    monkeypatch.delenv(deep_sweep.JOBS_ENV)
    assert deep_sweep.main([]) == 0
    assert swept[-1][1] == deep_sweep.sweep_width(deep_sweep.settlement_worker_bytes(1_000))


def test_each_run_records_every_workers_peak_beside_its_estimate(bench, monkeypatch):
    """Green or red, a run writes one `conform-deep` check line with each configuration's highest unit peak footprint and the need its width was derived from: the settlement unit's estimate for a settlement configuration, and the base for the overlay."""
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
    """At the length-6 window bound (2,251,636,269 windows) one settlement unit is estimated far past either fleet machine's memory in all, so `memory_shortfall` refuses it. `main` refuses such a run before sweeping anything and names the way to run it anyway; a stated width starts it with the warning instead."""
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


def test_the_estimate_places_each_queued_unit_in_the_slot_that_frees_first():
    """Two slots, running units with 100 and 300 seconds left, and two queued ones taking 200 and 50: the first queued one takes the slot freed at 100 and ends at 300, and the second takes a slot freed at 300 and ends at 350. A free slot starts a queued unit at once."""
    assert deep_sweep.schedule_finish({1: 100.0, 2: 300.0}, [(3, 200.0), (4, 50.0)], 2) == {
        1: 100.0,
        2: 300.0,
        3: 300.0,
        4: 350.0,
    }
    assert deep_sweep.schedule_finish({1: 100.0}, [(3, 200.0)], 2) == {1: 100.0, 3: 200.0}


def _many_unit_sweep() -> deep_sweep.SweepProgress:
    """Two slots over the overlay's 1,000 texts and two settlement configurations split into two units of 10,000 texts each, slots 1 and 2 for `default` and 3 and 4 for `ss03`, as read up to 115 seconds in. The overlay ran from 5 to 50 seconds, between two readings, so only its worker's own start dates it. `default`'s first unit started at 5 and ran at 100 texts a second, then 60, then 100 over its last tenth of the texts; its second took the overlay's slot at 55 and has run at 40, then 100. `ss03`'s units wait for slots."""
    units = (
        deep_sweep.SweepUnit("ss10", None, 1_000),
        deep_sweep.SweepUnit("default", "a", 10_000),
        deep_sweep.SweepUnit("default", "b", 10_000),
        deep_sweep.SweepUnit("ss03", "a", 10_000),
        deep_sweep.SweepUnit("ss03", "b", 10_000),
    )
    progress = deep_sweep.SweepProgress(units, 2, 0.0)
    progress.observe(1, 0, 5.0)
    progress.finish(0, 50.0, 1_000, began=5.0)
    progress.observe(2, 0, 55.0)
    for moment, first, second in ((55.0, 5_000, 0), (105.0, 8_000, 2_000), (115.0, 9_000, 3_000)):
        progress.observe(1, first, moment)
        progress.observe(2, second, moment)
    return progress


def test_the_estimate_runs_each_unit_at_its_own_rate_and_schedules_the_queue_at_its_kinds():
    """Each running unit's rate is taken over its last tenth of the texts, 100 a second, not its whole run's, which leaves `default`'s first unit 10 seconds and its second 70. Projected at those rates, the two whole walks take 120 and 130 seconds, so a settlement unit runs at 80 texts a second, and each queued `ss03` unit takes 125 seconds: the first from the slot freed at 10, ending at 135, and the second from the slot freed at 70, ending the sweep at 195. The units outnumber the slots, so the schedule, not the kind's rate over every unit at once, decides the finish. Once `default`'s first unit finishes at 125 and `ss03`'s first starts in its slot, that unit has shaped less than a tenth of its texts at a rate its worker's setup still slows, so it takes the settlement rate, and the second `ss03` unit starts when `default`'s second finishes."""
    progress = _many_unit_sweep()
    finishes, total = progress.estimate(115.0)
    assert finishes == pytest.approx({1: 10.0, 2: 70.0, 3: 135.0, 4: 195.0})
    assert total == pytest.approx(195.0)
    assert progress.config_finish("default", finishes) == pytest.approx(70.0)
    assert progress.config_finish("ss03", finishes) == pytest.approx(195.0)
    progress.observe(2, 4_000, 125.0)
    progress.finish(1, 125.0, 10_000)
    progress.observe(3, 300, 130.0, began=125.0)
    progress.observe(2, 4_500, 130.0)
    finishes, total = progress.estimate(130.0)
    assert finishes == pytest.approx({2: 55.0, 3: 9_700 / 80, 4: 55.0 + 125.0})
    assert total == pytest.approx(180.0)
    fresh = deep_sweep.SweepProgress(
        (deep_sweep.SweepUnit("default", "a", 100_000), deep_sweep.SweepUnit("default", "b", 100_000)), 1, 0.0
    )
    fresh.observe(0, 0, 5.0)
    assert fresh.estimate(10.0) == ({0: None, 1: None}, None)


def test_a_report_is_a_counter_line_with_one_line_per_configuration_after_it():
    """The report's first line is a `[progress]` counter over every unit's texts that `console.parse_line` reads, with the elapsed time, the rate, the finish, the running units' footprint and the swap in use riding on it; one plain line per configuration follows in submission order, summing its units, counting the ones running and finished, and ending when its last unit does. The next report's rates run from this one."""
    progress = _many_unit_sweep()
    wall = 1_790_000_000.0

    def at(seconds: float) -> str:
        return time.strftime("%a %H:%M", time.localtime(wall + seconds))

    lines = progress.report(115.0, wall, {1: 2_000_000_000, 2: 1_500_000_000}, 1_250_000_000)
    assert lines == [
        f"[progress] 13000/41000 texts, 1m55s elapsed, 113 texts/s since the start, finishing in 3m15s, at {at(195)}, 2 workers hold 3.50 GB, 1.25 GB of swap in use",
        "deep sweep[ss10]: 1,000/1,000 texts, finished in 45.0s",
        f"deep sweep[default]: 12,000/20,000 texts, 2 of 2 units running, running for 1m50s, 109 texts/s since it started, finishing in 1m10s, at {at(70)}, holding 3.50 GB",
        f"deep sweep[ss03]: 0/20,000 texts, queued, finishing in 3m15s, at {at(195)}",
    ]
    event = console.parse_line(lines[0])
    assert isinstance(event, console.Progress) and (event.done, event.total) == (13_000, 41_000)
    assert all(console.parse_line(line) is None for line in lines[1:])
    progress.observe(2, 4_000, 125.0)
    progress.finish(1, 125.0, 10_000)
    progress.observe(3, 300, 130.0, began=125.0)
    progress.observe(2, 4_500, 130.0)
    later = progress.report(130.0, wall + 15, {2: None, 3: 500_000_000}, None)
    assert "187 texts/s since the last report" in later[0]
    assert later[0].endswith("1 worker holds 0.50 GB, swap unreadable")
    assert later[2].startswith("deep sweep[default]: 14,500/20,000 texts, 1 of 2 units running, 1 finished,")
    assert "167 texts/s since the last report" in later[2] and later[2].endswith("footprint unreadable")
    assert later[3].startswith("deep sweep[ss03]: 300/20,000 texts, 1 of 2 units running, running for 5.0s,")
    assert later[3].endswith("holding 0.50 GB")


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


class _ThreadPool(ThreadPoolExecutor):
    def __init__(self, max_workers, mp_context, max_tasks_per_child, initializer, initargs):
        initializer(*initargs)
        super().__init__(max_workers=max_workers)


def _thread_sweep(tmp_path, monkeypatch, worker, max_length):
    """Point `run_sweep` at `worker` through a thread pool in place of the spawn pool, with the crate, the guard verdicts and the output directory stubbed, and each configuration's text count taken from the toy alphabet at `max_length`."""
    monkeypatch.setattr(deep_sweep, "_COUNTERS", None)
    monkeypatch.setattr(deep_sweep.kernel_exec, "ensure_built", lambda: None)
    monkeypatch.setattr(deep_sweep.kernel_exec, "guard_sweep", lambda spec: {})
    size = len(TOY_ALPHABET)
    monkeypatch.setattr(
        deep_sweep,
        "config_texts",
        lambda spec, length: {
            config: sum(
                size**n
                for n in range(
                    1, (conform.OVERLAY_MAX_LENGTH if config in conform.OVERLAY_CONFIGS else length) + 1
                )
            )
            for config in conform.ACCEPTANCE_CONFIGS
        },
    )
    monkeypatch.setattr(deep_sweep.run_m1, "OUT_DIR", tmp_path)
    monkeypatch.setattr(deep_sweep, "ProcessPoolExecutor", _ThreadPool)
    monkeypatch.setattr(deep_sweep, "_unit_worker", worker)


def test_the_sweep_reports_the_counts_its_workers_store_on_the_report_interval(tmp_path, monkeypatch):
    """`run_sweep` driven through a thread pool in place of the spawn pool, with every unit's worker storing half its texts and then waiting until the third report or a later one shows them all. Such a report counts the stored halves, reads the footprint of every running unit by the pid its worker recorded, and dates each unit from the clock start it recorded, converted to the parent's clock. Reports come at most one to each interval of `report_every` from the start, never one per reading. Once the workers return, every unit is finished with the texts it returned, each walk's samples hold the half its slot showed, and each configuration's peak is its units' highest."""
    max_length = 3
    units = deep_sweep.sweep_units(TOY_ALPHABET, max_length)
    halves = sum(unit.texts // 2 for unit in units)
    footprint_pids: list[int] = []
    monkeypatch.setattr(
        deep_sweep.peak_rss, "footprint_bytes", lambda pid: footprint_pids.append(pid) or 1_000_000_000
    )
    monkeypatch.setattr(deep_sweep.peak_rss, "swap_used_bytes", lambda: None)
    release = threading.Event()
    started: dict[int, float] = {}

    def worker(spec, font_path, config, last, max_length, glyphs, guard_verdicts, slot):
        started[slot] = time.monotonic()
        store = deep_sweep._counter_writer(slot)
        assert store is not None
        assert (units[slot].config, units[slot].last) == (config, last)
        store(units[slot].texts // 2)
        release.wait(timeout=30)
        return conform.ConformanceConfigResult(config=config, sequences=units[slot].texts), 7 + slot

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

    _thread_sweep(tmp_path, monkeypatch, worker, max_length)
    monkeypatch.setattr(deep_sweep, "SweepProgress", Recorded)
    every = 0.2
    summary, peaks = deep_sweep.run_sweep(_plan(units), max_length, len(units), report_every=every)
    assert release.is_set()
    assert summary["pass"] and summary["sequences"] == 3 + 9 + 27
    assert peaks == {
        config: 7 + max(slot for slot, unit in enumerate(units) if unit.config == config)
        for config in conform.ACCEPTANCE_CONFIGS
    }
    (progress,) = sweeps
    intervals = [int((now - progress.started) // every) for now, _ in reports]
    assert intervals[0] >= 1 and intervals == sorted(set(intervals))
    shown = next(
        lines for _, lines in reports if f"{halves}/{sum(unit.texts for unit in units)} texts" in lines[0]
    )
    assert f"{len(units)} workers hold {len(units)}.00 GB" in shown[0]
    assert shown[1].endswith("holding 1.00 GB")
    assert all(
        f"{len(TOY_ALPHABET)} of {len(TOY_ALPHABET)} units running, running for" in line
        and line.endswith(f"holding {len(TOY_ALPHABET)}.00 GB")
        for line in shown[2:]
    )
    assert set(footprint_pids) == {os.getpid()}
    for slot, walk in enumerate(progress.walks):
        assert walk.first is not None and walk.first[0] == pytest.approx(started[slot], abs=0.05)
        assert walk.finished is not None and walk.done == units[slot].texts
        assert any(done == units[slot].texts // 2 for _, done in walk.samples)


def test_the_units_merge_per_configuration_and_a_short_count_records_nothing(tmp_path, monkeypatch):
    """Each configuration's units merge into one result before the configurations merge, so the summary's exemplars come in configuration order and then in each configuration's text order, whatever the order the units finish in (here the last symbol runs backwards). A configuration whose units shaped fewer texts than every text of its lengths raises before the summary is written, so no green can be recorded from it."""
    max_length = 2
    units = deep_sweep.sweep_units(TOY_ALPHABET, max_length)
    ranks = {symbol: index for index, symbol in enumerate(TOY_ALPHABET)}
    lost: list[str] = []

    def worker(spec, font_path, config, last, max_length, glyphs, guard_verdicts, slot):
        if last is not None:
            time.sleep(0.01 * (len(TOY_ALPHABET) - ranks[last]))
        result = conform.ConformanceConfigResult(config=config)
        tally = conform.DivergenceTally(result, TOY_ALPHABET)
        for text in (text for length in (1, 2) for text in conform.sweep_texts(TOY_ALPHABET, length, last)):
            result.sequences += 1
            if config in lost and text == "aa":
                result.sequences -= 1
            tally.append(conform.Divergence(text, config, 0, "want", "got", f"kind-{text[-1]}"))
        return result, 1

    _thread_sweep(tmp_path, monkeypatch, worker, max_length)
    summary, _peaks = deep_sweep.run_sweep(_plan(units), max_length, len(units), report_every=60.0)
    expected = [
        f"{config} {':'.join(f'{ord(ch):04X}' for ch in text)} position 0 [kind-{text[-1]}] expected want got got"
        for config in conform.ACCEPTANCE_CONFIGS
        for text in ("a", "b", "c", "aa", "ab", "ac", "ba", "bb", "bc", "ca", "cb", "cc")
    ][: conform.EXEMPLAR_LIMIT]
    assert summary["divergence_exemplars"] == expected
    assert summary["divergences"] == 12 * len(conform.ACCEPTANCE_CONFIGS)
    written = json.loads((tmp_path / deep_sweep.SUMMARY_NAME).read_text())
    assert list(written["divergences_by_kind"]) == ["kind-a", "kind-b", "kind-c"]
    (tmp_path / deep_sweep.SUMMARY_NAME).unlink()
    lost.append("ss04")
    with pytest.raises(RuntimeError, match=r"deep sweep\[ss04\]: its units shaped 11 texts, not the 12"):
        deep_sweep.run_sweep(_plan(units), max_length, len(units), report_every=60.0)
    assert not (tmp_path / deep_sweep.SUMMARY_NAME).exists()


def test_a_unit_that_raises_cancels_the_units_still_queued(tmp_path, monkeypatch):
    """At width 1, when the first unit raises, `run_sweep` re-raises its error having run at most the one unit its freed slot took before the queue was cancelled, never the rest of the queue."""
    max_length = 2
    units = deep_sweep.sweep_units(TOY_ALPHABET, max_length)
    ran: list[int] = []

    def worker(spec, font_path, config, last, max_length, glyphs, guard_verdicts, slot):
        ran.append(slot)
        raise ValueError(f"unit {slot} failed")

    _thread_sweep(tmp_path, monkeypatch, worker, max_length)
    with pytest.raises(ValueError, match="unit 0 failed"):
        deep_sweep.run_sweep(_plan(units), max_length, 1, report_every=60.0)
    assert ran[0] == 0 and len(ran) <= 2 < len(units)
