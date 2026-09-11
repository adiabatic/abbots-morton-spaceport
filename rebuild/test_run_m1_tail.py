"""The shape of `run_m1.run`'s tail: the table-only branch (the string replay, the witness stage, the shipped-order walks) beside the glyph chain, the window packing deferred behind the head reads, and the join that decides the gate. What these hold is the contract the serial form stated — the first red in the serial order is the build's complaint, whatever the chain made of the tables — plus the two orderings the branches need: the witness stage runs after the replay that fills the settle memo it loads, and the oracle starts only once the witness stage's memos are on disk. Every stage that costs a crate or a font is stubbed with a rendezvous or a recorder; the packing tests build the mini fixture's real tables, since the packer is what they are about."""

import functools
import threading
import time
from types import SimpleNamespace

import pytest

from rebuild.pipeline import conform, defects, fixtures, kernel_exec, run_m1
from rebuild.pipeline import table as table_module
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import console
from rebuild.tools.memory_budget import usable_cores

SPEC = fixtures.mini_spec()
STAMP = "tail-test"
CONFIGS = conform.SETTLEMENT_CONFIGS
TABLES = {config: (SimpleNamespace(rules=()), SimpleNamespace(rows=())) for config in CONFIGS}
GREEN_REPLAY = {"pass": True, "complaint": None, "horizon": run_m1.REPLAY_HORIZON, "families": None}
GREEN_WITNESSES = {"pass": True, "failures": [], "configs": {}}
GREEN_EMITTED = {"pass": True, "complaint": None, "configs": {}}
RED_REPLAY = {**GREEN_REPLAY, "pass": False, "complaint": "(qsPea, …) settlement says one thing"}
RED_WITNESSES = {**GREEN_WITNESSES, "pass": False, "failures": ["default rule 0: never fires"]}
RED_EMITTED = {**GREEN_EMITTED, "pass": False, "complaint": "default: row (qsPea, …) answered by rule 3"}


def _stub_chain(monkeypatch, events, *, on_compile=None, readback_pass=True):
    """The glyph chain with no glyph in it: every stage answers an empty shape, the compile writes a marker file and calls `on_compile`, and the read-back answers `readback_pass`."""
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
    """The three table-only stages as recorders: each appends its start and its end to `events`, calls its hook in between, and answers the summary it was given. The tables come from a stub too, so no crate runs."""
    monkeypatch.setattr(
        run_m1,
        "build_tables",
        lambda spec, out_dir=None, inputs=None, kernel_threads=None, packing=None: (TABLES, {}),
    )

    def run_replay_strings(spec, out_dir, inputs, kernel_threads=None, memo_inputs=None):
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

    def run_emitted_order(spec, tables, out_dir, kernel_threads=None, ready=None):
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
        """Three parties on one barrier — the replay, the shipped-order walk and the compile — so a serialization of any two breaks it and the test fails rather than hangs: the replay and the walk run beside each other on the table-only branch, and the chain does not wait for either."""
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
        """The replay writes the settle memo file whole and the witness stage loads it, so the two are a chain even though the walk runs beside them."""
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
        """The chain finishes while the branch is still parked; the handle is what the caller joins on."""
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
                gates.wait_for_memo()
            with pytest.raises(SystemExit, match="tables incomplete"):
                gates.join()
        finally:
            gates.close()

    def test_a_red_witness_stage_is_raised_before_the_oracle_would_start(self, monkeypatch, tmp_path):
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events, witnesses=RED_WITNESSES)
        _summary, gates = _run(tmp_path)
        try:
            with pytest.raises(conform.WitnessError):
                gates.wait_for_memo()
        finally:
            gates.close()

    def test_a_red_walk_waits_for_the_join(self, monkeypatch, tmp_path):
        """The shipped order is the last of the three, so its red arrives at the join and not at the memo wait, which only the replay-then-witness chain can raise through."""
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


def _stub_main(monkeypatch, tmp_path, events, **gates):
    """Everything `main` reaches around `run`: the pre-gate guards, the keys, the spec, the pin gate and the oracle, with `run` itself real and pointed at `tmp_path`."""
    real_run = run_m1.run
    monkeypatch.setattr(run_m1.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda repo_root: False)
    monkeypatch.setattr(run_m1, "tables_inputs", lambda: STAMP)
    monkeypatch.setattr(run_m1, "settle_memo_inputs", lambda: None)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: SPEC)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-tail")
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(run_m1, "run", functools.partial(real_run, tmp_path))
    monkeypatch.setattr(
        run_m1,
        "run_manual_pin_gate",
        lambda spec: {"pass": True, "disagreements": [], "pins_in_scope": 3, "replayed": 3},
    )

    def run_oracle(spec, jobs, **rest):
        events.append("oracle")
        return {"unmatched": 0, "multi_matched": 0}

    monkeypatch.setattr(run_m1, "run_oracle", run_oracle)
    _stub_chain(monkeypatch, events)
    _stub_gates(monkeypatch, events, **gates)


class TestMain:
    def test_the_oracle_starts_only_after_the_witness_memos_are_written(self, monkeypatch, tmp_path, capsys):
        """The guard on the memo clobber: an oracle worker loads `settle-memo-<config>.gz` lazily and writes back what it settled, so one that started before the witness stage wrote its file could replace that file with a smaller one, and the next belt would settle cold. The witness stub parks long enough that an oracle started at the chain's end would land first."""
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, on_witnesses=lambda: time.sleep(0.3))
        run_m1.main([])
        assert events.index("witnesses:done") < events.index("oracle")
        labels = [
            event.label
            for event in map(console.parse_line, capsys.readouterr().out.splitlines())
            if isinstance(event, console.Timing)
        ]
        assert "settle_memo_wait" in labels
        assert labels.index("settle_memo_wait") < labels.index("run_oracle")

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
        assert "oracle" not in events

    def test_a_red_replay_beats_a_defect_error(self, monkeypatch, tmp_path):
        events: list = []
        _stub_main(monkeypatch, tmp_path, events, replay=RED_REPLAY, on_replay=lambda: time.sleep(0.2))
        broken = defects.DefectReport(errors=[defects.Defect("D1", "qsPea", "a contact")])
        monkeypatch.setattr(run_m1, "_run_defect_gates", lambda spec, tables, glyphs: broken)
        with pytest.raises(SystemExit, match="tables incomplete"):
            run_m1.main([])
        assert "oracle" not in events


def _gated_pack(monkeypatch, release):
    """`_pack_windows` parked per configuration: a window payload's pack waits on `release[config]` and a memo's runs through, both packing for real once released."""
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
        """With a `Packing` passed, the tables come back while every window pack is still parked, `close` blocks until each `.gz` is on disk, and what lands is byte for byte what the blocking form packs — the identity that says deferring the pack moved nothing in the artifact."""
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
        """Every walker is parked on its configuration's pack at once, and each is released alone: the verb sees the `.gz` for the configuration it was asked for, released or not being the whole of what it checks."""
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
                run_m1.run_emitted_order(
                    SPEC, tables, tmp_path, kernel_threads=len(CONFIGS), ready=packing.wait
                )
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


class TestTheTailWidth:
    def test_the_replay_and_the_walks_take_the_builds_width(self, monkeypatch, tmp_path):
        """`kernel_threads` — the memory-derived width, or a stated `--kernel-threads` — reaches the replay and the walks unchanged: the oracle cannot start until the replay has exited, and the walks' residue past the memo wait shares the box with the oracle's pool rather than being narrowed to the cores that whole-box pool would leave, which would serialize the walks onto one core."""
        events: list = []
        widths: dict[str, int | None] = {}
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)
        replay = run_m1.run_replay_strings
        walks = run_m1.run_emitted_order

        def recording_replay(spec, out_dir, inputs, kernel_threads=None, memo_inputs=None):
            widths["replay"] = kernel_threads
            return replay(spec, out_dir, inputs, kernel_threads=kernel_threads, memo_inputs=memo_inputs)

        def recording_walks(spec, tables, out_dir, kernel_threads=None, ready=None):
            widths["walks"] = kernel_threads
            return walks(spec, tables, out_dir, kernel_threads=kernel_threads, ready=ready)

        monkeypatch.setattr(run_m1, "run_replay_strings", recording_replay)
        monkeypatch.setattr(run_m1, "run_emitted_order", recording_walks)
        cores = usable_cores()
        _summary, gates = _run(tmp_path, kernel_threads=2)
        try:
            gates.join()
        finally:
            gates.close()
        assert widths == {"replay": min(2, cores), "walks": min(2, cores)}


class TestTheMemoWait:
    def test_a_branch_that_dies_before_its_chain_does_not_hang_the_wait(self, monkeypatch, tmp_path):
        """`wait_for_memo` blocks on the event the branch sets behind the witness stage; a branch that raises before it reaches that chain — a pool that cannot start its walker thread — has the event set for it when its future settles, and the wait raises the branch's own error rather than blocking forever."""
        events: list = []
        _stub_chain(monkeypatch, events)
        _stub_gates(monkeypatch, events)

        def dying_branch(*args, **rest):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(run_m1, "_run_table_gates", dying_branch)
        _summary, gates = _run(tmp_path)
        try:
            with pytest.raises(RuntimeError, match="can't start new thread"):
                gates.wait_for_memo()
            with pytest.raises(RuntimeError, match="can't start new thread"):
                gates.join()
        finally:
            gates.close()
