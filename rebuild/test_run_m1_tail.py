"""Tests for the end of `run_m1.run`: the table-only branch (the string replay, the witness stage, the shipped-order walks) running beside the glyph chain, the window packing, which reads each payload through a FIFO while the crate writes it, writes its part, and commits its windows head after the crate's clean exit, and the join that decides the gate. The build reports the first failure in serial order, whatever the glyph chain made of the tables. The witness stage runs after the replay that fills the settle memo it loads. The oracle starts once the replay has returned, but writes the settle memo only once the witness stage, which reads it and writes none of it, has returned. Every stage that runs the crate, spawns a process or compiles a font is stubbed with a barrier or a recorder; the packing tests build the mini fixture's real tables, because the packer is what they test."""

import dataclasses
import functools
import gzip
import itertools
import json
import os
import shutil
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from rebuild.pipeline import (
    conform,
    defects,
    fixtures,
    kernel_exec,
    output_sink,
    row_delta,
    run_m1,
    run_m1_gates,
    spec_load,
)
from rebuild.pipeline import table as table_module
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_paths
from rebuild.tools import cycle_timings as ct

SPEC = fixtures.mini_spec()
STAMP = "tail-test"
CONFIGS = conform.SETTLEMENT_CONFIGS
MACHINE_48_GIB = 51_539_607_552
TABLES = {config: (SimpleNamespace(rules=()), SimpleNamespace(rows=())) for config in CONFIGS}
GREEN_REPLAY = {"pass": True, "error": None, "max_length": run_m1.REPLAY_MAX_LENGTH, "families": None}
GREEN_WITNESSES = {"pass": True, "failures": [], "configs": {}}
GREEN_EMITTED = {"pass": True, "error": None, "configs": {}}
RED_REPLAY = {**GREEN_REPLAY, "pass": False, "error": "(qsPea, …) settlement says one thing"}
RED_WITNESSES = {**GREEN_WITNESSES, "pass": False, "failures": ["default rule 0: never fires"]}
RED_EMITTED = {**GREEN_EMITTED, "pass": False, "error": "default: row (qsPea, …) answered by rule 3"}


def _stub_chain(monkeypatch, events, *, on_compile=None, readback_pass=True):
    """Stubs the glyph chain: every stage returns an empty value, the compile writes a placeholder file and calls `on_compile`, and the read-back passes or fails according to `readback_pass`."""
    monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, tables: {})
    monkeypatch.setattr(run_m1, "mint_raw_glyphs", lambda spec: ({}, {}, {}))
    monkeypatch.setattr(run_m1, "namer_dot_glyphs", lambda: {})
    monkeypatch.setattr(run_m1, "_run_defect_gates", lambda spec, tables, glyphs: defects.DefectReport())
    monkeypatch.setattr(
        run_m1.emit_gsub,
        "emit_gsub",
        lambda spec, tables, glyphs, ss10_copies: SimpleNamespace(fea_text="", rule_count=0),
    )
    monkeypatch.setattr(run_m1.emit_gsub, "behavior_classes", lambda plan: [])
    monkeypatch.setattr(run_m1.emit_gpos, "emit_gpos", lambda glyphs, spec: "")
    monkeypatch.setattr(run_m1.emit_gpos, "cursive_registrations", lambda glyphs, spec: {})

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
            "divergence_count": 0 if readback_pass else 1,
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
    """Stubs the three table-only stages as recorders: each appends its start and end to `events`, calls its hook in between, and returns the summary it was given. The witness stage is stubbed where the branch spawns its process (`_spawn_rule_witnesses`), so no process starts, and its stub reports a peak of zero. The table build is stubbed too, so no crate runs."""
    monkeypatch.setattr(
        run_m1,
        "build_tables",
        lambda spec, out_dir=None, inputs=None, kernel_threads=None, packing=None, scratch_beside_default=None: (
            TABLES,
            {},
        ),
    )

    def run_replay_strings(spec, out_dir, inputs, replay_threads=None, memo_inputs=None, imports=None):
        events.append("replay:start")
        if on_replay is not None:
            on_replay()
        events.append("replay:done")
        return dict(replay)

    def spawn_rule_witnesses(spec, tables, out_dir, memo_inputs):
        events.append("witnesses:start")
        if on_witnesses is not None:
            on_witnesses()
        events.append("witnesses:done")
        return dict(witnesses), 0

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
    monkeypatch.setattr(run_m1, "_spawn_rule_witnesses", spawn_rule_witnesses)
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

    def test_a_build_whose_summaries_come_out_the_same_leaves_the_glyph_chains_summaries_in_place(
        self, monkeypatch, tmp_path
    ):
        """A second build over the same tables writes the same `pipeline_summary.json` and `readback_summary.json`, so both stay in place, each keeping its inode and its modification time, here set long ago so that any write would move it."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)
        summaries = [tmp_path / "pipeline_summary.json", tmp_path / "readback_summary.json"]

        def build() -> None:
            _summary, gates = _run(tmp_path)
            try:
                gates.join()
            finally:
                gates.close()

        build()
        for path in summaries:
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        kept = [(path.stat().st_ino, 1_000_000_000) for path in summaries]
        build()
        assert [(path.stat().st_ino, path.stat().st_mtime_ns) for path in summaries] == kept

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


class TestTheReadbackRecord:
    @pytest.mark.parametrize("passed", [True, False])
    def test_the_readback_line_carries_the_commit_and_the_figures_even_when_read_back_fails(
        self, monkeypatch, tmp_path, capsys, passed
    ):
        """The `[t] readback` line ends with the commit `run` took before the build and the settlement lookup's figures from the read-back report, and is kept for run_m1's check line. A failed read-back prints the line before it raises, so a build that falls through the headroom floor still records its figures."""
        events: list = []
        _stub_chain(monkeypatch, events)
        report = {
            "pass": passed,
            "checked": {
                "gsub_budget": {
                    "lookups": 549,
                    "subtable_offset_headroom": 27_997,
                    "largest_group_rule_bytes": 33_542,
                },
                "settle_rules": 29_214,
                "settle_subtable_formats": {"format2": 2_298, "format3": 927},
            },
            "divergences": [] if passed else ["gsub budget: under the floor"],
            "divergence_count": 0 if passed else 1,
        }
        monkeypatch.setattr(run_m1.readback, "verify_font", lambda font, plan, registrations: report)
        monkeypatch.setattr(run_m1, "head_commit", lambda root=run_m1.REPO_ROOT: "95521363")
        _stub_gates(monkeypatch, events)
        if passed:
            _summary, gates = _run(tmp_path)
            gates.join()
            gates.close()
        else:
            with pytest.raises(run_m1.readback.ReadbackError):
                _run(tmp_path)
        (line,) = [
            phase for phase in ct.parse_inner_timings(capsys.readouterr().out) if phase["label"] == "readback"
        ]
        del line["elapsed_s"]
        assert line == {
            "label": "readback",
            "commit": "95521363",
            "settle_format2": 2_298,
            "settle_format3": 927,
            "subtable_offset_headroom": 27_997,
            "gsub_lookups": 549,
            "settle_rules": 29_214,
            "largest_group_rule_bytes": 33_542,
        }
        assert ct.parse_inner_timings(run_m1._build_phase_lines[-1])[0]["commit"] == "95521363"


class TestTheGlyphChainCollect:
    def test_one_collect_follows_the_glyph_chain_and_records_the_level_it_leaves(
        self, monkeypatch, tmp_path, capsys
    ):
        """`run` collects the glyph chain's cyclic garbage once, on the calling thread, after the chain has returned and before `run` returns, and its `[t] glyph_chain_collect` line carries the resident set the collect leaves, the level the oracle's pool starts beside."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)
        caller = threading.current_thread()
        monkeypatch.setattr(
            run_m1.gc,
            "collect",
            lambda *_: events.append(("collect", threading.current_thread() is caller)) or 0,
        )
        monkeypatch.setattr(run_m1, "current_rss_bytes", lambda: 451_000_000)
        _summary, gates = _run(tmp_path)
        collected = [event for event in events if isinstance(event, tuple)]
        try:
            gates.join()
        finally:
            gates.close()
        assert collected == [("collect", True)] and events.index("compile") < events.index(collected[0])
        (line,) = [
            phase
            for phase in ct.parse_inner_timings(capsys.readouterr().out)
            if phase["label"] == "glyph_chain_collect"
        ]
        assert line["rss_now_gb"] == 0.45


class TestTheReplayScope:
    @pytest.mark.parametrize(
        ("families", "scope"), [(None, "full"), (["qsTea"], "narrowed"), ([], "narrowed")]
    )
    def test_the_replay_line_names_whether_every_text_was_walked(
        self, monkeypatch, tmp_path, capsys, families, scope
    ):
        """The `[t] replay_strings` line says `scope=full` when the replay walked every text and `scope=narrowed` when it walked the texts of some runes or of none, beside its peak."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, replay={**GREEN_REPLAY, "families": families})
        _summary, gates = _run(tmp_path)
        try:
            gates.join()
        finally:
            gates.close()
        (line,) = [
            phase
            for phase in ct.parse_inner_timings(capsys.readouterr().out)
            if phase["label"] == "replay_strings"
        ]
        assert line["scope"] == scope
        assert "rss_gb" in line


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

    def test_a_red_readback_names_the_total_count_rather_than_the_trimmed_list_length(
        self, monkeypatch, tmp_path
    ):
        events: list = []
        _stub_chain(monkeypatch, events, readback_pass=False)
        monkeypatch.setattr(
            run_m1.readback,
            "verify_font",
            lambda font, plan, registrations: {
                "pass": False,
                "divergences": ["a divergence", "… and 199 more"],
                "divergence_count": 200,
            },
        )
        _stub_gates(monkeypatch, events)
        with pytest.raises(run_m1.readback.ReadbackError, match=r"^200 read-back divergence\(s\)"):
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
    """Stubs what `run_m1.full_run` calls around `run` (the Stage A capture and the pre-gate guards, which read files under rebuild/out, the keys, the spec, the pin gate, and the oracle), points `run_m1.OUT_DIR`, where the Stage A record is deleted and written, at `tmp_path`, and leaves `run` real, writing under `tmp_path`, and the ledger guard real, compiling the checked-in ledger. The oracle stub records its start, calls `on_oracle`, then the `memo_ready` it was passed (where the real oracle waits before its first memo write), recording the seconds it returns in `events` as `("memo_wait", seconds)`, then `after_memo`, and records its end."""
    real_run = run_m1.run
    monkeypatch.setattr(run_m1, "OUT_DIR", tmp_path)
    monkeypatch.setattr(
        run_m1.fingerprint, "stage_a", lambda root: {"data": "d", "baselines": "b", "pipeline_code": "p"}
    )
    monkeypatch.setattr(run_m1_gates.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
    monkeypatch.setattr(
        run_m1_gates.baseline_subset, "ensure_fresh", lambda repo_root, before_refilter: False
    )
    monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
    monkeypatch.setattr(run_m1, "settle_memo_inputs", lambda: None)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
    monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-tail")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(run_m1, "run", functools.partial(real_run, tmp_path))
    monkeypatch.setattr(
        run_m1_gates,
        "run_manual_pin_gate",
        lambda spec: {"pass": True, "disagreements": [], "pins_in_scope": 3, "replayed": 3},
    )

    def run_oracle(spec, jobs, memo_ready=None, **rest):
        events.append("oracle:start")
        if on_oracle is not None:
            on_oracle()
        if memo_ready is not None:
            events.append(("memo_wait", memo_ready()))
        if after_memo is not None:
            after_memo()
        events.append("oracle")
        return {"unmatched": 0, "multi_matched": 0}

    monkeypatch.setattr(run_m1_gates, "run_oracle", run_oracle)
    _stub_chain(monkeypatch, events)
    _stub_gates(monkeypatch, events, **gates)


class TestMain:
    def test_the_oracle_starts_behind_the_replay_and_writes_behind_the_witness_stage(
        self, monkeypatch, tmp_path
    ):
        """The oracle starts once the replay has returned, while the witness stage runs, and the `memo_ready` it is passed (which `run_oracle` calls before its first settle memo write) returns only once the witness stage has returned. So the stage reads each settle memo file before any oracle write, as the replay left it. The witness stub waits until the oracle has started, so an oracle that waited for the witness stage fails the wait instead of hanging. The real stage writes nothing the oracle could see, so the stub then writes the file through `conform.absorb_settle_memo` as its last act, a mark of its end, and the oracle stub checks that the file is readable as soon as its wait returns. The wait returns the seconds it blocked, which the oracle reports, and prints nothing itself."""
        events: list = []
        memo = conform.SettleMemoFile(tmp_path / "settle-memo-default.bin", "stamp")
        oracle_started = threading.Event()
        standing: list[bool] = []

        def witness():
            assert oracle_started.wait(timeout=20), "the oracle waited for the witness stage"
            time.sleep(0.3)
            assert conform.absorb_settle_memo(memo, SPEC, [conform._fresh_memo([])]).written

        _stub_main(
            monkeypatch,
            tmp_path,
            events,
            on_witnesses=witness,
            on_oracle=oracle_started.set,
            after_memo=lambda: standing.append(conform.settle_memo_standing(memo)),
        )
        run_m1_gates.main([])
        assert events.index("replay:done") < events.index("oracle:start") < events.index("witnesses:done")
        assert events.index("witnesses:done") < events.index("oracle")
        assert standing == [True]
        (waited,) = [event[1] for event in events if isinstance(event, tuple) and event[0] == "memo_wait"]
        assert waited is not None and waited > 0

    def test_a_red_witness_stage_stops_the_oracle_at_its_memo_wait(self, monkeypatch, tmp_path):
        """A witness-stage failure is raised in the oracle at its memo wait, before the oracle writes a memo or a summary, and it is the error the build reports."""
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, witnesses=RED_WITNESSES)
        with pytest.raises(SystemExit, match="certificate does not fire"):
            run_m1_gates.main([])
        assert "oracle" not in events

    def test_a_red_walk_under_a_green_chain_and_oracle_is_the_builds_error(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, emitted=RED_EMITTED)
        with pytest.raises(SystemExit, match="shipped settlement order"):
            run_m1_gates.main([])
        assert "oracle" in events

    @pytest.mark.parametrize("cycle_owned", [True, False])
    def test_a_late_red_walk_cannot_leave_a_standalone_green(self, monkeypatch, tmp_path, cycle_owned):
        """The emitted-order stage waits until the oracle writes a passing summary. Its failure then decides the CLI result even though all three summaries pass; a standalone run invalidates its green, while a cycle child leaves the same record for its parent to invalidate."""
        events: list = []
        oracle_written = threading.Event()
        store = tmp_path / "run-m1-green.json"
        monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
        ac.record_green(store, "fp-tail")
        before = store.read_bytes()
        if cycle_owned:
            monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")

        def emitted():
            assert oracle_written.wait(timeout=20), "the emitted-order stage did not reach the oracle"

        def write_oracle():
            (tmp_path / "oracle_summary.json").write_text(json.dumps({"unmatched": 0, "multi_matched": 0}))
            oracle_written.set()

        _stub_main(
            monkeypatch,
            tmp_path,
            events,
            emitted=RED_EMITTED,
            on_emitted=emitted,
            after_memo=write_oracle,
        )

        def pin_gate(spec):
            summary = {"pass": True, "disagreements": [], "pins_in_scope": 3, "replayed": 3}
            (tmp_path / "manual_pins_summary.json").write_text(json.dumps(summary))
            return summary

        monkeypatch.setattr(run_m1_gates, "run_manual_pin_gate", pin_gate)
        try:
            with pytest.raises(SystemExit, match="shipped settlement order"):
                run_m1_gates.main([])
        finally:
            oracle_written.set()
        summaries = [
            json.loads((tmp_path / name).read_text())
            for name in ("pipeline_summary.json", "manual_pins_summary.json", "oracle_summary.json")
        ]
        assert ac.evaluate_run_m1_gate(*summaries).ok
        assert "oracle" in events
        if cycle_owned:
            assert store.read_bytes() == before
            assert ct.load_checks(ct.JOURNAL) == []
        else:
            assert ac.read_green_record(store) is None
            checks = ct.load_checks(ct.JOURNAL)
            assert len(checks) == 1 and checks[0]["outcome"] == "red"
            assert "shipped settlement order" in checks[0]["failures"][0]

    def test_a_red_replay_is_raised_ahead_of_the_oracle(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, replay=RED_REPLAY)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1_gates.main([])
        assert "oracle:start" not in events

    def test_a_red_replay_beats_a_defect_error(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, replay=RED_REPLAY, on_replay=lambda: time.sleep(0.2))
        broken = defects.DefectReport(errors=[defects.Defect("D1", "qsPea", "a contact")])
        monkeypatch.setattr(run_m1, "_run_defect_gates", lambda spec, tables, glyphs: broken)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1_gates.main([])
        assert "oracle:start" not in events


def _gated_pack(monkeypatch, release):
    """Wraps `_pack_config` so each configuration's pack after the crate's exit, the staged head file's rename and its windows head's commit, waits on `release[config]` and then runs for real."""
    real = run_m1._pack_config

    def pack(tables_dir, config, route, inputs, digest):
        assert release[config].wait(timeout=120)
        real(tables_dir, config, route, inputs, digest)

    monkeypatch.setattr(run_m1, "_pack_config", pack)


def _windows_bytes(out_dir) -> dict[str, bytes]:
    """Every file under `out_dir` named for a configuration's windows rows, by name, with its bytes."""
    return {path.name: path.read_bytes() for path in sorted(out_dir.glob("windows-*"))}


def _committed_names(out_dir) -> list[str]:
    """The names a committed build leaves for every configuration's windows rows: its head, its head file and every part its head lists."""
    names = []
    for config in CONFIGS:
        head = table_module.windows_head(out_dir, config)
        names += [
            table_module.windows_head_path(out_dir, config).name,
            table_module.windows_head_file(out_dir, config).name,
            *(part.name for part in head.parts),
        ]
    return sorted(names)


def _outcome_within(seconds, call):
    """Run `call` on a thread and return what it raised, or None when it returned, failing instead of hanging when it has not finished within `seconds`."""
    raised: list[BaseException] = []

    def target():
        try:
            call()
        except BaseException as error:
            raised.append(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"no answer within {seconds} seconds"
    return raised[0] if raised else None


def _reference_pack(payload: bytes, path) -> bytes:
    """Pack `payload` from a plain file in one stream with the artifact's gzip settings (no file name, mtime 0, level 1), the reference a FIFO reader's artifact must match, and return its bytes."""
    plain = path.with_name(path.name + ".plain")
    plain.write_bytes(payload)
    with (
        plain.open("rb") as source,
        path.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as packed,
    ):
        shutil.copyfileobj(source, packed, length=1 << 20)
    plain.unlink()
    return path.read_bytes()


class TestThePacking:
    def test_build_tables_returns_before_the_packing_and_the_deferred_bytes_match_the_blocking_ones(
        self, monkeypatch, tmp_path
    ):
        """With a `Packing` passed, `build_tables` returns while every configuration's pack is still blocked, so no head file has been renamed into place and no windows head committed, `close` waits until each is, and the files are byte-identical to what the blocking form writes."""
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
        assert not list(deferred.glob("*.tsv.gz.head")) and not list(deferred.glob("*.head.json.gz"))
        for config in CONFIGS:
            release[config].set()
        packing.close()
        assert _windows_bytes(deferred) == _windows_bytes(blocking)
        assert sorted(_windows_bytes(deferred)) == _committed_names(deferred)
        assert not list(deferred.glob("*.tmp"))

    def test_close_raises_a_pack_failure_rather_than_swallowing_it(self, monkeypatch, tmp_path):
        """`close` raises the failure of a pack that raised, and a second `close` returns. Every pack refuses once its rename is done, and the build runs on the mini fixture narrowed to ·Tea, ·May and ·It (`spec_load.narrowed`), whose files are a small fraction of the whole fixture's."""
        real = run_m1._pack_config

        def refuse(tables_dir, config, route, inputs, digest):
            real(tables_dir, config, route, inputs, digest)
            raise OSError(f"no room for {config}")

        monkeypatch.setattr(run_m1, "_pack_config", refuse)
        packing = run_m1.Packing(2)
        spec = spec_load.narrowed(SPEC, ("qsTea", "qsMay", "qsIt"))
        run_m1.build_tables(spec, tmp_path, inputs=STAMP, packing=packing)
        with pytest.raises(OSError, match="no room"):
            packing.close()
        packing.close()

    def test_each_payload_reaches_its_artifact_through_a_fifo_and_leaves_no_plain_file(
        self, monkeypatch, tmp_path
    ):
        """When the crate starts, a FIFO stands at each configuration's payload path, so the plain payload never reaches the disk. Once the build returns, each configuration's head file and windows head are in place under the build's stamp over its base, and nothing else is left under a `windows-` name: no FIFO, no plain payload and no staged file."""
        real = kernel_exec.build_table_files
        fifos = {}

        def build_table_files(spec_bytes, out_dir, configs, **rest):
            for config in configs:
                fifos[config] = stat.S_ISFIFO(kernel_exec.windows_payload(out_dir, config).lstat().st_mode)
            return real(spec_bytes, out_dir, configs, **rest)

        monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        assert fifos == dict.fromkeys(CONFIGS, True)
        for config in CONFIGS:
            assert table_module.read_windows_head(tmp_path, config)[0] == STAMP, config
        assert sorted(path.name for path in tmp_path.glob("windows-*")) == _committed_names(tmp_path)

    @pytest.mark.parametrize("failure", ["a reader fails", "the crate dies mid-write", "an interrupt"])
    def test_a_failed_or_interrupted_build_leaves_the_previous_head_file_head_and_parts(
        self, monkeypatch, tmp_path, failure
    ):
        """A build that fails, or is interrupted before the crate's clean exit, commits nothing: each configuration's head file, windows head, base and segment are the previous build's, byte for byte, and nothing it staged or wrote is left, no FIFO, no staged head file and no part. The previous build wrote a segment over a base, so the failing build is on the segment route and feeds both to the crate. The failures are a reader that raises once it has the head, which breaks the real crate's pipe, so the crate's failure names the reader's as its cause; a crate that writes half of `default`'s payload, opens no other FIFO and fails; and an interrupt before the crate opens any FIFO. A reader left waiting on a FIFO would hang the build, so the build runs on a thread that must finish in time."""
        narrow = spec_load.narrowed(SPEC, ("qsTea", "qsMay", "qsIt"))
        run_m1.build_tables(narrow, tmp_path, inputs=STAMP)
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        assert all(len(table_module.windows_head(tmp_path, config).segments) == 1 for config in CONFIGS)
        before = _windows_bytes(tmp_path)
        if failure == "a reader fails":
            real = run_m1._stage_head_file

            def stage(tables_dir, config, head):
                if config == "default":
                    raise OSError("no room for the pack")
                return real(tables_dir, config, head)

            monkeypatch.setattr(run_m1, "_stage_head_file", stage)
        else:
            payload = b"".join(table_module.materialized_windows(tmp_path, "default"))

            def build_table_files(spec_bytes, out_dir, configs, **_rest):
                if failure == "an interrupt":
                    raise KeyboardInterrupt
                with kernel_exec.windows_payload(out_dir, "default").open("wb") as sink:
                    sink.write(payload[: len(payload) // 2])
                raise kernel_exec.KernelRunError("the kernel exited 1 on build-tables: killed")

            monkeypatch.setattr(kernel_exec, "build_table_files", build_table_files)
        error = _outcome_within(120, lambda: run_m1.build_tables(narrow, tmp_path, inputs=STAMP))
        if failure == "an interrupt":
            assert isinstance(error, KeyboardInterrupt)
        else:
            assert isinstance(error, kernel_exec.KernelRunError), error
        if failure == "a reader fails":
            assert isinstance(error.__cause__, OSError) and "no room" in str(error.__cause__), error
        assert _windows_bytes(tmp_path) == before

    def test_a_part_no_head_lists_is_deleted_by_the_next_build(self, tmp_path):
        """A part a failed build left under its final name, a segment past what the head lists or a base of the next generation, is no head's, and the next build deletes it before it writes, leaving only what its own head lists. So is the staging file a build killed while it filled a part or the head leaves, whose name, a part's carrying its generation and number, no later write may take again."""
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        output = table_module.windows_output("default")
        strays = [
            tmp_path / row_delta.segment_name(output, 0, 1),
            tmp_path / row_delta.base_name(output, 1),
            tmp_path / f"{row_delta.segment_name(output, 0, 17)}{output_sink.STAGING_SUFFIX}",
            tmp_path / f"{row_delta.head_name(output)}{output_sink.STAGING_SUFFIX}",
        ]
        for path in strays:
            path.write_bytes(b"a part a failed build left\n")
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        assert not any(path.exists() for path in strays)
        assert sorted(path.name for path in tmp_path.glob("windows-*")) == _committed_names(tmp_path)

    @pytest.mark.parametrize("damage", ["a flipped byte", "another format", "a segment in another format"])
    def test_a_part_whose_sha256_differs_writes_a_new_base_and_counts_it_rewritten(
        self, monkeypatch, tmp_path, capsys, damage
    ):
        """A part that is not the one its head records, here `default`'s base with one byte flipped and its size kept, fails the plan's check, so the build writes `default` a new base, the next generation's, and counts it as rewritten whole on the pass's counts file, which the cycle prints as `rewritten whole: windows-default.tsv.gz`. So does a part that hashes to its head but was written in another format, as every part is once its format moves, a base whose head line names another windows format or a segment whose header names another segment format: the plan runs every header check a part's feeder makes before the crate starts, rather than the feeder failing the build and every build after it. Every other configuration writes its head alone, and every configuration's rows materialize to the build's own."""
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        expected = {
            config: b"".join(table_module.materialized_windows(tmp_path, config)) for config in CONFIGS
        }
        head = table_module.windows_head(tmp_path, "default")
        base = tmp_path / head.base.name
        if damage == "a flipped byte":
            raw = bytearray(base.read_bytes())
            raw[len(raw) // 2] ^= 1
            base.write_bytes(bytes(raw))
        elif damage == "a segment in another format":
            header = row_delta.segment_header(head.output, head.generation, 1, row_delta.MERGE_KEY)
            assert row_delta.SEGMENT_FORMAT.encode() in header

            def fill(stream):
                stream.write(header.replace(row_delta.SEGMENT_FORMAT.encode(), b"ams-row-delta-segment/0"))
                return 0, 0

            segment = row_delta.write_part(
                tmp_path, row_delta.segment_name(head.output, head.generation, 1), fill
            )
            row_delta.commit(tmp_path, dataclasses.replace(head, segments=(segment,)))
        else:
            payload = gzip.decompress(base.read_bytes())
            assert payload.startswith(table_module.WINDOWS_MARKER)
            older = payload.replace(table_module.WINDOWS_FORMAT.encode(), b"ams-m1-windows/2", 1)
            base.write_bytes(gzip.compress(older, compresslevel=1, mtime=0))
            row_delta.commit(
                tmp_path,
                dataclasses.replace(
                    head,
                    base=dataclasses.replace(
                        head.base, size=base.stat().st_size, sha256=row_delta.file_sha256(base)
                    ),
                ),
            )
        counts = tmp_path / "counts"
        counts.write_text("")
        monkeypatch.setenv(output_sink.OUTPUT_COUNTS_ENV, str(counts))
        capsys.readouterr()
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        output = capsys.readouterr().out
        assert "wrote=base reason=unusable" in output
        assert output.count("wrote=head") == len(CONFIGS) - 1
        assert f"{output_sink.REWRITTEN}\t{table_module.windows_output('default')}\n" in counts.read_text()
        assert table_module.windows_head(tmp_path, "default").generation == 1
        assert not base.exists()
        for config in CONFIGS:
            assert (
                b"".join(table_module.materialized_windows(tmp_path, config, sha256=True)) == expected[config]
            )

    def test_the_widest_build_and_its_walks_run_under_the_default_descriptor_limit(self, tmp_path):
        """Every configuration at its widest, a base and twenty segments, with the pass's fold held by another output so none folds, merged by the build and walked by the shipped-order stage, each started under the 256-descriptor soft limit macOS gives a process by default: the build and the walks raise the limit toward the hard limit before they open a part (`kernel_exec.raise_descriptor_limit`), so the build holds a part handle and a pipe for each of the 126 parts and every walk passes."""
        import resource

        tables, _digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        widest = row_delta.SEGMENT_CAP + row_delta.FOLD_OVERRUN - 1
        for config in CONFIGS:
            head = table_module.windows_head(tmp_path, config)
            segments = []
            for number in range(1, widest + 1):

                def fill(stream, number=number):
                    stream.write(row_delta.segment_header(head.output, 0, number, row_delta.MERGE_KEY))
                    return 0, 0

                segments.append(
                    row_delta.write_part(tmp_path, row_delta.segment_name(head.output, 0, number), fill)
                )
            row_delta.commit(
                tmp_path,
                row_delta.Head(
                    head.output, 0, head.content, head.stamps, head.base, tuple(segments), head.merge
                ),
            )
        assert row_delta.claim_fold("unit-cache.ndjson.gz")
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
            tables, _digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
            summary = run_m1.run_emitted_order(SPEC, tables, tmp_path)
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        assert summary["pass"], summary
        for config in CONFIGS:
            assert len(table_module.windows_head(tmp_path, config).segments) == widest, config

    def test_the_packed_base_is_the_reference_packing_of_its_payload(self, tmp_path):
        """Each base a payload's FIFO reader packs is byte for byte the reference packing of the same payload from a plain file (`_reference_pack`), and so is the payload written into a FIFO in ragged pieces, so how the payload arrives changes no base."""
        run_m1.build_tables(SPEC, tmp_path, inputs=STAMP)
        for config in CONFIGS:
            packed = (tmp_path / table_module.windows_head(tmp_path, config).base.name).read_bytes()
            reference = _reference_pack(gzip.decompress(packed), tmp_path / f"reference-{config}")
            assert packed == reference, config
        payload = b"".join(table_module.materialized_windows(tmp_path, "ss04"))
        reference = _reference_pack(payload, tmp_path / "reference")
        ragged = tmp_path / "ragged"
        ragged.mkdir()
        fifo = ragged / "ragged.tsv"

        def write():
            with fifo.open("wb", buffering=0) as sink:
                offset = 0
                for size in itertools.cycle((1, 511, 65_537, 3, 1 << 20, 4_093)):
                    if offset >= len(payload):
                        break
                    offset += sink.write(payload[offset : offset + size])

        route = run_m1.WindowsRoute("ss04", ragged, run_m1.BASE_ROUTE, "first")
        _ran, heads = kernel_exec.stream_payloads(
            {"ss04": fifo}, lambda _config, source: run_m1._take_windows(source, route), write
        )
        assert heads["ss04"] == payload[: payload.index(b"\n") + 1]
        assert route.part is not None and (ragged / route.part.name).read_bytes() == reference
        assert gzip.decompress((route.staged or ragged).read_bytes()) == heads["ss04"]

    def test_each_walk_waits_on_its_own_pack(self, monkeypatch, tmp_path):
        """The packs are released one at a time in reverse order, and each walk must find its own configuration's pack released and its windows head committed when it calls `kernel_exec.replay_emitted`."""
        release = {config: threading.Event() for config in CONFIGS}
        _gated_pack(monkeypatch, release)
        packing = run_m1.Packing(len(CONFIGS))
        tables, _digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, packing=packing)
        walked: list = []

        def replay_emitted(head, *, config, table, order, order_rules, context, timings=False):
            assert release[config].is_set(), f"{config}'s walk started before its pack was released"
            assert head.is_file(), f"{config}'s walk started before its head was committed"
            walked.append(config)
            return {"rows": 1, "checked_per_member": 0, "open_rows": 0, "continued": 0}

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
        monkeypatch.setattr(kernel_exec, "kernel_threads_default", lambda **_: 1)
        packing = run_m1.Packing(len(CONFIGS))
        tables, _digests = run_m1.build_tables(SPEC, tmp_path, inputs=STAMP, packing=packing)
        for config in CONFIGS:
            packing.wait(config)
        lock = threading.Lock()
        full = threading.Event()
        inside = {"count": 0, "peak": 0}

        def replay_emitted(head, *, config, table, order, order_rules, context, timings=False):
            with lock:
                inside["count"] += 1
                inside["peak"] = max(inside["peak"], inside["count"])
                if inside["count"] >= width:
                    full.set()
            assert full.wait(timeout=20), f"{config}'s walk never saw {width} walkers in flight at once"
            with lock:
                inside["count"] -= 1
            return {"rows": 1, "checked_per_member": 0, "open_rows": 0, "continued": 0}

        monkeypatch.setattr(kernel_exec, "replay_emitted", replay_emitted)
        try:
            summary = run_m1.run_emitted_order(SPEC, tables, tmp_path, ready=packing.wait)
        finally:
            packing.close()
        assert summary["pass"]
        assert inside["peak"] == width


class TestTheTailWidth:
    def test_the_core_bound_width_is_the_count_capped_at_the_cores(self, monkeypatch):
        """`_core_bound_threads` is the configuration count capped at the cores this process may run on, with a minimum of one. Setting the memory-derived `kernel_threads_default` to one does not change it, so the setting that keeps the table build out of swap does not narrow these pools."""
        monkeypatch.setattr(kernel_exec, "kernel_threads_default", lambda **_: 1)
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 2)
        assert run_m1._core_bound_threads(len(CONFIGS)) == 2
        assert run_m1._core_bound_threads(99) == 2
        assert run_m1._core_bound_threads(0) == 1
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)
        assert run_m1._core_bound_threads(len(CONFIGS)) == len(CONFIGS)

    def test_the_replay_takes_its_own_width_not_the_builds(self, monkeypatch, tmp_path):
        """With `kernel_threads=2`, the replay runs at its own width, not at 2. Its width is budgeted from `kernel_exec.REPLAY_PEAK_BYTES`, so `run` passes it `replay_threads` (None for the derived width, or a stated `--replay-threads`, used as given) and never the build's width. `kernel_exec.replay_strings` is stubbed to record the width it is called with. The machine is set to a 48 GiB fleet Mac with many cores, so the derived width is the configuration count on any machine that runs the suite."""
        events: list = []
        widths: list[int] = []
        real_replay = run_m1.run_replay_strings
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)
        monkeypatch.setattr(run_m1, "run_replay_strings", real_replay)
        monkeypatch.setattr(run_m1, "replay_structure_stamp", lambda spec, root=None: "s1")
        monkeypatch.setattr(run_m1.fingerprint, "rune_digests", lambda root: {})
        monkeypatch.delenv("AMS_REPLAY_THREADS", raising=False)
        monkeypatch.setenv("AMS_TOTAL_MEMORY_BYTES", str(MACHINE_48_GIB))
        monkeypatch.setattr(run_m1, "usable_cores", lambda: 64)

        def replay_strings(
            spec,
            out_dir,
            configs,
            *,
            max_length,
            families,
            threads,
            timings=False,
            memo_dir=None,
            on_peak=None,
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
        """With `kernel_threads=1`, the `Packing` pool is still created at `_core_bound_threads`'s width, and `run_emitted_order` is called with no keyword argument but the pack wait (`ready`), so neither `--kernel-threads` nor `kernel_exec.kernel_threads_default` reaches either pool."""
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
