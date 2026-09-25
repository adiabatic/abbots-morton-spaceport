"""Tests for the end of `run_m1.run`: the table-only branch (the string replay, the witness stage, the shipped-order walks) running beside the glyph chain, the window packing that runs after the head reads, and the join that decides the gate. The build reports the first failure in serial order, whatever the glyph chain made of the tables. The witness stage runs after the replay that fills the settle memo it loads. The oracle starts once the replay has returned, but writes the settle memo only once the witness stage's writes are on disk. Every stage that runs the crate or compiles a font is stubbed with a barrier or a recorder; the packing tests build the mini fixture's real tables, because the packer is what they test."""

import functools
import threading
import time
from types import SimpleNamespace

import pytest

from rebuild.pipeline import conform, defects, fixtures, kernel_exec, run_m1
from rebuild.pipeline import table as table_module
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import console

SPEC = fixtures.mini_spec()
STAMP = "tail-test"
CONFIGS = conform.SETTLEMENT_CONFIGS
BOX_32_GIB = 34_359_738_368
TABLES = {config: (SimpleNamespace(rules=()), SimpleNamespace(rows=())) for config in CONFIGS}
GREEN_REPLAY = {"pass": True, "complaint": None, "horizon": run_m1.REPLAY_HORIZON, "families": None}
GREEN_WITNESSES = {"pass": True, "failures": [], "configs": {}}
GREEN_EMITTED = {"pass": True, "complaint": None, "configs": {}}
RED_REPLAY = {**GREEN_REPLAY, "pass": False, "complaint": "(qsPea, …) settlement says one thing"}
RED_WITNESSES = {**GREEN_WITNESSES, "pass": False, "failures": ["default rule 0: never fires"]}
RED_EMITTED = {**GREEN_EMITTED, "pass": False, "complaint": "default: row (qsPea, …) answered by rule 3"}


def _stub_chain(monkeypatch, events, *, on_compile=None, readback_pass=True):
    """Stubs the glyph chain: every stage returns an empty value, the compile writes a placeholder file and calls `on_compile`, and the read-back passes or fails according to `readback_pass`."""
    monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, tables: {})
    monkeypatch.setattr(run_m1, "mint_raw_glyphs", lambda spec: ({}, {}, {}))
    monkeypatch.setattr(run_m1, "namer_dot_glyphs", lambda: {})
    monkeypatch.setattr(run_m1, "_run_defect_gates", lambda spec, tables, glyphs: defects.DefectReport())
    monkeypatch.setattr(
        run_m1.emit_gsub,
        "emit_gsub",
        lambda spec, tables, glyphs, ss10_twins: SimpleNamespace(fea_text="", rule_count=0),
    )
    monkeypatch.setattr(run_m1.emit_gsub, "behavior_classes", lambda plan: [])
    monkeypatch.setattr(run_m1.emit_gpos, "emit_gpos", lambda glyphs, spec: "")
    monkeypatch.setattr(run_m1.emit_gpos, "cursive_registrations", lambda glyphs, spec: {})
    monkeypatch.setattr(run_m1.fingerprint, "write_stage_a", lambda root, out_dir: None)

    def build_mini_font(glyphs, fea, path):
        events.append("compile")
        if on_compile is not None:
            on_compile()
        path.write_bytes(b"not a font")
        return path

    monkeypatch.setattr(run_m1.compile_font, "build_mini_font", build_mini_font)
    monkeypatch.setattr(
        run_m1.readback,
        "verify_font",
        lambda font, plan, registrations: {
            "pass": readback_pass,
            "divergences": [] if readback_pass else ["a divergence"],
        },
    )


def _stub_gates(
    monkeypatch,
    events,
    *,
    replay=GREEN_REPLAY,
    witnesses=GREEN_WITNESSES,
    emitted=GREEN_EMITTED,
    on_replay=None,
    on_witnesses=None,
    on_emitted=None,
):
    """Stubs the three table-only stages as recorders: each appends its start and end to `events`, calls its hook in between, and returns the summary it was given. The table build is stubbed too, so no crate runs."""
    monkeypatch.setattr(
        run_m1,
        "build_tables",
        lambda spec, out_dir=None, inputs=None, kernel_threads=None, packing=None: (TABLES, {}),
    )

    def run_replay_strings(spec, out_dir, inputs, replay_threads=None, memo_inputs=None):
        events.append("replay:start")
        if on_replay is not None:
            on_replay()
        events.append("replay:done")
        return dict(replay)

    def run_rule_witnesses(spec, tables, out_dir, memo_inputs):
        events.append("witnesses:start")
        if on_witnesses is not None:
            on_witnesses()
        events.append("witnesses:done")
        return dict(witnesses)

    def run_emitted_order(spec, tables, out_dir, ready=None):
        for config in tables:
            if ready is not None:
                ready(config)
        events.append("emitted:start")
        if on_emitted is not None:
            on_emitted()
        events.append("emitted:done")
        return dict(emitted)

    monkeypatch.setattr(run_m1, "run_replay_strings", run_replay_strings)
    monkeypatch.setattr(run_m1, "run_rule_witnesses", run_rule_witnesses)
    monkeypatch.setattr(run_m1, "run_emitted_order", run_emitted_order)


def _run(tmp_path, **rest):
    return run_m1.run(out_dir=tmp_path, spec=SPEC, inputs=STAMP, **rest)


class TestTheBranches:
    def test_the_replay_the_walk_and_the_compile_are_in_flight_at_once(self, monkeypatch, tmp_path):
        """The replay, the shipped-order walk, and the compile wait on one three-party barrier, so if any two ran one after the other the barrier would time out and the test would fail instead of hanging."""
        events: list = []
        barrier = threading.Barrier(3, timeout=20)
        _stub_chain(monkeypatch, events, on_compile=barrier.wait)
        _stub_gates(monkeypatch, events, on_replay=barrier.wait, on_emitted=barrier.wait)
        summary, gates = _run(tmp_path)
        try:
            gates.join()
        finally:
            gates.close()
        assert summary["configs"] == list(CONFIGS)
        assert not barrier.broken

    def test_the_witness_stage_starts_after_the_replay_has_returned(self, monkeypatch, tmp_path):
        """The replay writes the settle memo file and the witness stage loads it, so the witness stage waits for the replay even though the walk runs beside both."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, on_replay=lambda: time.sleep(0.2))
        _summary, gates = _run(tmp_path)
        try:
            gates.join()
        finally:
            gates.close()
        assert events.index("replay:done") < events.index("witnesses:start")

    def test_a_red_replay_runs_no_witness_stage(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, replay=RED_REPLAY)
        _summary, gates = _run(tmp_path)
        try:
            with pytest.raises(SystemExit, match="tables incomplete"):
                gates.join()
        finally:
            gates.close()
        assert "witnesses:start" not in events
        assert "emitted:done" in events

    def test_run_returns_its_summary_and_a_handle_without_joining(self, monkeypatch, tmp_path):
        """`run` returns after the glyph chain finishes while the branch is still blocked, and the caller joins the branch through the returned handle."""
        events: list = []
        parked = threading.Event()
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, on_emitted=parked.wait)
        summary, gates = _run(tmp_path)
        assert "compile" in events and "emitted:done" not in events
        assert (tmp_path / "pipeline_summary.json").is_file()
        assert summary["font"] == str(tmp_path / "M1.otf")
        parked.set()
        gates.join()
        gates.close()
        assert "emitted:done" in events


class TestTheFirstRed:
    def test_a_red_replay_beats_a_red_readback(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events, readback_pass=False)
        _stub_gates(monkeypatch, events, replay=RED_REPLAY)
        with pytest.raises(SystemExit, match="tables incomplete"):
            _run(tmp_path)

    def test_a_red_witness_stage_beats_a_red_readback(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events, readback_pass=False)
        _stub_gates(monkeypatch, events, witnesses=RED_WITNESSES)
        with pytest.raises(conform.WitnessError, match="certificate does not fire"):
            _run(tmp_path)

    def test_a_red_walk_beats_a_red_readback(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events, readback_pass=False)
        _stub_gates(monkeypatch, events, emitted=RED_EMITTED)
        with pytest.raises(SystemExit, match="shipped settlement order"):
            _run(tmp_path)

    def test_a_green_branch_lets_the_chain_report_its_own_red(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events, readback_pass=False)
        _stub_gates(monkeypatch, events)
        with pytest.raises(run_m1.readback.ReadbackError):
            _run(tmp_path)

    def test_a_red_replay_beats_a_red_walk_at_the_join(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, replay=RED_REPLAY, emitted=RED_EMITTED)
        _summary, gates = _run(tmp_path)
        try:
            with pytest.raises(SystemExit, match="tables incomplete"):
                gates.wait_for_replay()
            with pytest.raises(SystemExit, match="tables incomplete"):
                gates.wait_for_memo()
            with pytest.raises(SystemExit, match="tables incomplete"):
                gates.join()
        finally:
            gates.close()

    def test_a_red_witness_stage_is_raised_before_the_oracle_writes_a_memo(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, witnesses=RED_WITNESSES)
        _summary, gates = _run(tmp_path)
        try:
            with pytest.raises(conform.WitnessError):
                gates.wait_for_memo()
        finally:
            gates.close()

    def test_the_replay_wait_returns_while_the_witness_stage_is_still_running(self, monkeypatch, tmp_path):
        """`wait_for_replay` returns while the witness stage is still blocked, and `wait_for_memo` returns only once that stage has returned."""
        events: list = []
        release = threading.Event()
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, on_witnesses=lambda: release.wait(timeout=20))
        _summary, gates = _run(tmp_path)
        try:
            gates.wait_for_replay()
            assert "replay:done" in events and "witnesses:done" not in events
            release.set()
            gates.wait_for_memo()
            assert "witnesses:done" in events
            gates.join()
        finally:
            release.set()
            gates.close()

    def test_a_red_walk_waits_for_the_join(self, monkeypatch, tmp_path):
        """A shipped-order failure is raised at `join`, not at `wait_for_memo`, which raises only failures of the replay and the witness stage."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, emitted=RED_EMITTED)
        _summary, gates = _run(tmp_path)
        try:
            gates.wait_for_memo()
            assert gates.first_red() is not None
            with pytest.raises(SystemExit, match="shipped settlement order"):
                gates.join()
        finally:
            gates.close()


def _stub_main(monkeypatch, tmp_path, events, *, on_oracle=None, after_memo=None, **gates):
    """Stubs everything `main` calls around `run` (the pre-gate guards, the keys, the spec, the pin gate, and the oracle) and leaves `run` real, writing under `tmp_path`. The oracle stub records its start, calls `on_oracle`, then the `memo_ready` it was passed (where the real oracle waits before its first memo write), then `after_memo`, and records its end."""
    real_run = run_m1.run
    monkeypatch.setattr(run_m1.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda repo_root: False)
    monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
    monkeypatch.setattr(run_m1, "settle_memo_inputs", lambda: None)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
    monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-tail")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(run_m1, "run", functools.partial(real_run, tmp_path))
    monkeypatch.setattr(
        run_m1,
        "run_manual_pin_gate",
        lambda spec: {"pass": True, "disagreements": [], "pins_in_scope": 3, "replayed": 3},
    )

    def run_oracle(spec, jobs, memo_ready=None, **rest):
        events.append("oracle:start")
        if on_oracle is not None:
            on_oracle()
        if memo_ready is not None:
            memo_ready()
        if after_memo is not None:
            after_memo()
        events.append("oracle")
        return {"unmatched": 0, "multi_matched": 0}

    monkeypatch.setattr(run_m1, "run_oracle", run_oracle)
    _stub_chain(monkeypatch, events)
    _stub_gates(monkeypatch, events, **gates)


class TestMain:
    def test_the_oracle_starts_behind_the_replay_and_writes_behind_the_witness_stage(
        self, monkeypatch, tmp_path, capsys
    ):
        """The oracle starts once the replay has returned, while the witness stage runs, and the `memo_ready` it is passed (which `run_oracle` calls before its first settle memo write) returns only once the witness stage has absorbed its part into the file. So the witness stage is the only writer while the two overlap, and the oracle's writes read what it wrote. The witness stub waits until the oracle has started, so an oracle that waited for the witness stage fails the wait instead of hanging. The stub then writes a part and absorbs it through `conform.absorb_settle_memo_parts` as the real stage does, and the oracle stub checks that the file is readable as soon as its wait returns."""
        events: list = []
        memo = conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", "stamp")
        part = tmp_path / "witness-part.gz"
        oracle_started = threading.Event()
        standing: list[bool] = []

        def witness():
            assert oracle_started.wait(timeout=20), "the oracle waited for the witness stage"
            time.sleep(0.3)
            assert conform._write_settle_memo_part(memo, [], part)
            assert conform.absorb_settle_memo_parts(memo, [part], SPEC)

        _stub_main(
            monkeypatch,
            tmp_path,
            events,
            on_witnesses=witness,
            on_oracle=oracle_started.set,
            after_memo=lambda: standing.append(conform.settle_memo_standing(memo)),
        )
        run_m1.main([])
        assert events.index("replay:done") < events.index("oracle:start") < events.index("witnesses:done")
        assert events.index("witnesses:done") < events.index("oracle")
        assert standing == [True]
        labels = [
            event.label
            for event in map(console.parse_line, capsys.readouterr().out.splitlines())
            if isinstance(event, console.Timing)
        ]
        assert "witness_memo_wait" in labels
        assert labels.index("witness_memo_wait") < labels.index("run_oracle")

    def test_a_red_witness_stage_stops_the_oracle_at_its_memo_wait(self, monkeypatch, tmp_path):
        """A witness-stage failure is raised in the oracle at its memo wait, before the oracle writes a memo or a summary, and it is the error the build reports."""
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, witnesses=RED_WITNESSES)
        with pytest.raises(SystemExit, match="certificate does not fire"):
            run_m1.main([])
        assert "oracle" not in events

    def test_a_red_walk_under_a_green_chain_and_oracle_is_the_builds_complaint(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, emitted=RED_EMITTED)
        with pytest.raises(SystemExit, match="shipped settlement order"):
            run_m1.main([])
        assert "oracle" in events

    def test_a_red_replay_is_raised_ahead_of_the_oracle(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, replay=RED_REPLAY)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1.main([])
        assert "oracle:start" not in events

    def test_a_red_replay_beats_a_defect_error(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, replay=RED_REPLAY, on_replay=lambda: time.sleep(0.2))
        broken = defects.DefectReport(errors=[defects.Defect("D1", "qsPea", "a contact")])
        monkeypatch.setattr(run_m1, "_run_defect_gates", lambda spec, tables, glyphs: broken)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1.main([])
        assert "oracle:start" not in events


def _gated_pack(monkeypatch, release):
    """Wraps `_pack_windows` so a window payload's pack waits on `release[config]` while a memo's pack runs at once. Both then pack for real."""
    real = run_m1._pack_windows

    def pack(payload, path):
        family, _, rest = payload.name.partition("-")
        if family == "windows":
            assert release[rest.removesuffix(".tsv")].wait(timeout=120)
        real(payload, path)

    monkeypatch.setattr(run_m1, "_pack_windows", pack)


class TestThePacking:
    def test_build_tables_returns_before_the_packing_and_the_deferred_bytes_match_the_blocking_ones(
        self, monkeypatch, tmp_path
    ):
        """With a `Packing` passed, `build_tables` returns while every window pack is still blocked, `close` waits until each `.gz` is on disk, and the packed files are byte-identical to what the blocking form writes."""
        blocking = tmp_path / "blocking"
        run_m1.build_tables(SPEC, blocking, inputs=STAMP)
        release = {config: threading.Event() for config in CONFIGS}
        _gated_pack(monkeypatch, release)
        deferred = tmp_path / "deferred"
        packing = run_m1.Packing(len(CONFIGS))
        answer: dict = {}
        builder = threading.Thread(
            target=lambda: answer.update(
                zip(("tables", "digests"), run_m1.build_tables(SPEC, deferred, inputs=STAMP, packing=packing))
            )
        )
        builder.start()
        builder.join(timeout=300)
        assert not builder.is_alive(), "build_tables waited for a pack nothing had released"
        assert list(answer["tables"]) == list(CONFIGS)
        assert not [path.name for path in deferred.glob("windows-*.gz")]
        for config in CONFIGS:
            release[config].set()
        packing.close()
        for config in CONFIGS:
            packed = table_module.windows_path(deferred, config)
            assert packed.read_bytes() == table_module.windows_path(blocking, config).read_bytes(), config
            memo = kernel_exec.memo_path(deferred, config)
            assert memo.read_bytes() == kernel_exec.memo_path(blocking, config).read_bytes(), config
        assert not [
            path.name for path in deferred.glob("*.tsv") if path.name.startswith(("windows-", "memo-"))
        ]

    def test_close_raises_a_pack_failure_rather_than_swallowing_it(self, monkeypatch, tmp_path):
        def refuse(payload, path):
            raise OSError(f"no room for {path.name}")

        monkeypatch.setattr(run_m1, "_pack_windows", refuse)
        packing = run_m1.Packing(2)
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, packing=packing)
        with pytest.raises(OSError, match="no room"):
            packing.close()
        packing.close()

    def test_each_walk_waits_on_its_own_pack(self, monkeypatch, tmp_path):
        """The packs are released one at a time in reverse order, and each walk must find its own configuration's pack released and its `.gz` on disk when it calls `kernel_exec.replay_emitted`."""
        release = {config: threading.Event() for config in CONFIGS}
        _gated_pack(monkeypatch, release)
        packing = run_m1.Packing(len(CONFIGS))
        tables, _digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, packing=packing)
        walked: list = []

        def replay_emitted(windows, *, config, table, order, context, timings=False):
            assert release[config].is_set(), f"{config}'s walk started before its pack was released"
            assert windows.is_file(), f"{config}'s walk started before its pack was on disk"
            walked.append(config)
            return {"rows": 1, "expanded": 0}

        monkeypatch.setattr(kernel_exec, "replay_emitted", replay_emitted)
        failures: list = []

        def walk():
            try:
                run_m1.run_emitted_order(SPEC, tables, tmp_path, ready=packing.wait)
            except BaseException as error:
                failures.append(error)

        walker = threading.Thread(target=walk)
        walker.start()
        for config in reversed(CONFIGS):
            release[config].set()
            time.sleep(0.05)
        walker.join(timeout=300)
        packing.close()
        assert not walker.is_alive()
        assert failures == []
        assert sorted(walked) == sorted(CONFIGS)

    def test_every_walk_runs_in_one_wave_at_the_cores_not_the_builds_width(self, monkeypatch, tmp_path):
        """With the memory-derived width set to one and every pack already on disk, as many walkers as `_core_bound_threads` allows call `kernel_exec.replay_emitted` at the same time: the walk pool is sized from the configuration count and the cores, not from the table build's width. Each stub walker waits until that many walkers are inside, so a pool sized at the build's width fails on the wait's timeout instead of hanging."""
        width = run_m1._core_bound_threads(len(CONFIGS))
        monkeypatch.setattr(kernel_exec, "KERNEL_THREADS_DEFAULT", 1)
        packing = run_m1.Packing(len(CONFIGS))
        tables, _digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, packing=packing)
        for config in CONFIGS:
            packing.wait(config)
        lock = threading.Lock()
        full = threading.Event()
        inside = {"count": 0, "peak": 0}

        def replay_emitted(windows, *, config, table, order, context, timings=False):
            with lock:
                inside["count"] += 1
                inside["peak"] = max(inside["peak"], inside["count"])
                if inside["count"] >= width:
                    full.set()
            assert full.wait(timeout=20), f"{config}'s walk never saw {width} walkers in flight at once"
            with lock:
                inside["count"] -= 1
            return {"rows": 1, "expanded": 0}

        monkeypatch.setattr(kernel_exec, "replay_emitted", replay_emitted)
        try:
            summary = run_m1.run_emitted_order(SPEC, tables, tmp_path, ready=packing.wait)
        finally:
            packing.close()
        assert summary["pass"]
        assert inside["peak"] == width


class TestTheTailWidth:
    def test_the_core_bound_width_is_the_count_capped_at_the_cores(self, monkeypatch):
        """`_core_bound_threads` is the configuration count capped at the cores this process may run on, with a minimum of one. Setting the memory-derived `KERNEL_THREADS_DEFAULT` to one does not change it, so the setting that keeps the table build out of swap does not narrow these pools."""
        monkeypatch.setattr(kernel_exec, "KERNEL_THREADS_DEFAULT", 1)
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 2)
        assert run_m1._core_bound_threads(len(CONFIGS)) == 2
        assert run_m1._core_bound_threads(99) == 2
        assert run_m1._core_bound_threads(0) == 1
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)
        assert run_m1._core_bound_threads(len(CONFIGS)) == len(CONFIGS)

    def test_the_replay_takes_its_own_width_not_the_builds(self, monkeypatch, tmp_path):
        """With `kernel_threads=2`, the replay runs at its own width, not at 2. Its width is budgeted from `kernel_exec.REPLAY_PEAK_BYTES`, so `run` passes it `replay_threads` (None for the derived width, or a stated `--replay-threads`, used as given) and never the build's width. `kernel_exec.replay_strings` is stubbed to record the width it is called with. The machine is set to the 32 GiB fleet Mac with many cores, so the derived width is the configuration count on any machine that runs the suite."""
        events: list = []
        widths: list[int] = []
        real_replay = run_m1.run_replay_strings
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)
        monkeypatch.setattr(run_m1, "run_replay_strings", real_replay)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: {})
        monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", str(BOX_32_GIB))
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)

        def replay_strings(
            spec, out_dir, configs, *, horizon, families, threads, timings=False, memo_dir=None
        ):
            widths.append(threads)
            return {config: {"texts": 1, "windows": 1, "skipped": 0} for config in configs}

        monkeypatch.setattr(kernel_exec, "replay_strings", replay_strings)
        for stated in (None, 1):
            _summary, gates = _run(tmp_path / f"stated-{stated}", kernel_threads=2, replay_threads=stated)
            try:
                gates.join()
            finally:
                gates.close()
        assert widths == [len(CONFIGS), 1] and len(CONFIGS) != 2

    def test_the_packing_and_the_walks_take_the_cores_not_the_builds_width(self, monkeypatch, tmp_path):
        """With `kernel_threads=1`, the `Packing` pool is still created at `_core_bound_threads`'s width, and `run_emitted_order` is called with no keyword argument but the pack wait (`ready`), so neither `--kernel-threads` nor `KERNEL_THREADS_DEFAULT` reaches either pool."""
        events: list = []
        widths: dict[str, int] = {}
        calls: list[set[str]] = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)
        packing_class = run_m1.Packing
        walks = run_m1.run_emitted_order

        class RecordingPacking(packing_class):
            def __init__(self, threads: int) -> None:
                widths["packing"] = threads
                super().__init__(threads)

        def recording_walks(spec, tables, out_dir, **rest):
            calls.append(set(rest))
            return walks(spec, tables, out_dir, **rest)

        monkeypatch.setattr(run_m1, "Packing", RecordingPacking)
        monkeypatch.setattr(run_m1, "run_emitted_order", recording_walks)
        _summary, gates = _run(tmp_path, kernel_threads=1)
        try:
            gates.join()
        finally:
            gates.close()
        assert widths == {"packing": run_m1._core_bound_threads(len(CONFIGS))}
        assert calls == [{"ready"}]


class TestTheMemoWait:
    @pytest.mark.parametrize("wait", ["wait_for_replay", "wait_for_memo"])
    def test_a_branch_that_dies_before_its_chain_does_not_hang_the_wait(self, monkeypatch, tmp_path, wait):
        """`wait_for_replay` waits on the event the branch sets after the string replay, and `wait_for_memo` on the one it sets after the witness stage. If the branch raises before either stage, for example because its pool cannot start a walker thread, both events are set when its future completes, and either wait raises the branch's error instead of blocking forever."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)

        def dying_branch(*args, **rest):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(run_m1, "_run_table_gates", dying_branch)
        _summary, gates = _run(tmp_path)
        try:
            with pytest.raises(RuntimeError, match="can't start new thread"):
                getattr(gates, wait)()
            with pytest.raises(RuntimeError, match="can't start new thread"):
                gates.join()
        finally:
            gates.close()
