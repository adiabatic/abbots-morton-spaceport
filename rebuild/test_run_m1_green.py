"""Interactive run_m1 and --conform-only record the same last-green files the artifact cycle skips on, so a fix verified by hand is not re-verified by the next cycle, and each of them files what it decided as a check line in the timings journal. The gate verdicts come from artifact_cycle's own evaluators, never from run_m1's exit code, which is nonzero whenever the oracle carries UNMATCHED rows — the normal mid-migration state — and that is the fact the check lines here exist to pin: the same run that exits 1 files a green. `--gates-only` records that same run_m1 green under one further condition — a prior green to stand on and every input that moved since it comparison-side — which is what makes the artifact cycle's re-adjudication route worth taking rather than merely cheap."""

import json

import pytest

from rebuild.pipeline import conform, defects, oracle, oracle_cache, run_m1
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import console
from rebuild.tools import cycle_timings as ct


@pytest.fixture
def green_store(tmp_path):
    return tmp_path / "run-m1-green.json"


def _checks():
    """The check lines this run filed. The journal constant is read here rather than captured, because rebuild/conftest.py's autouse redirect is what points it under tmp_path and record_check resolves it at call time for exactly that reason — the same fixture that takes the cycle's run id off the environment, without which a run of this suite inside a real cycle would see every one of these entry points stand down."""
    return ct.load_checks(ct.JOURNAL)


def _phases(output):
    """What this run opened as a phase and what its `[t]` lines closed, read off the stream the way the cycle's digest reads a child's: an opened phase whose label no timing carries would reach the terminal with no duration behind it, and a timing whose label no phase opened is a line the digest keeps to the log."""
    events = [console.parse_line(line) for line in output.splitlines()]
    opened = [event.name for event in events if isinstance(event, console.Phase)]
    closed = [event.label for event in events if isinstance(event, console.Timing)]
    return opened, closed


def _keys(values):
    calls = iter(values)
    return lambda: next(calls)


class HardExit(Exception):
    pass


class FlushRecorder:
    def __init__(self, name, events):
        self.name = name
        self.events = events

    def flush(self):
        self.events.append(f"flush {self.name}")

    def write(self, value):
        self.events.append(f"write {self.name} {value}")
        return len(value)


def test_hard_exit_flushes_both_streams_before_os_exit(monkeypatch):
    events = []
    monkeypatch.setattr(run_m1.sys, "stdout", FlushRecorder("stdout", events))
    monkeypatch.setattr(run_m1.sys, "stderr", FlushRecorder("stderr", events))

    def exit_(status):
        events.append(f"exit {status}")
        raise HardExit

    monkeypatch.setattr(run_m1.os, "_exit", exit_)
    with pytest.raises(HardExit):
        run_m1._hard_exit(7)
    assert events == ["flush stdout", "flush stderr", "exit 7"]


def test_cli_flushes_output_before_preserving_string_system_exit(monkeypatch):
    events = []
    monkeypatch.setattr(run_m1.sys, "stdout", FlushRecorder("stdout", events))
    monkeypatch.setattr(run_m1.sys, "stderr", FlushRecorder("stderr", events))

    def main():
        print("summary")
        raise SystemExit("expected failure")

    def exit_(status):
        events.append(f"exit {status}")
        raise HardExit(status)

    monkeypatch.setattr(run_m1, "main", main)
    monkeypatch.setattr(run_m1.os, "_exit", exit_)
    with pytest.raises(HardExit, match="1"):
        run_m1._run_cli()
    assert events.index("flush stdout") < events.index("write stderr expected failure")
    assert events[-3:] == ["flush stdout", "flush stderr", "exit 1"]


def test_cli_hard_exits_zero_after_a_normal_return(monkeypatch):
    monkeypatch.setattr(run_m1, "main", lambda: None)
    monkeypatch.setattr(run_m1, "_hard_exit", lambda status: (_ for _ in ()).throw(HardExit(status)))
    with pytest.raises(HardExit, match="0"):
        run_m1._run_cli()


def test_cli_leaves_unexpected_exceptions_to_the_interpreter(monkeypatch):
    monkeypatch.setattr(run_m1, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(run_m1, "_hard_exit", lambda _status: pytest.fail("must not hard-exit"))
    with pytest.raises(RuntimeError, match="boom"):
        run_m1._run_cli()


def test_records_when_the_key_holds_across_the_run(green_store):
    run_m1._settle_green(green_store, "fp-1", True, _keys(["fp-1"]), "run_m1")
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-1"


def test_records_nothing_when_the_inputs_moved_mid_run(green_store, capsys):
    run_m1._settle_green(green_store, "fp-1", True, _keys(["fp-2"]), "run_m1")
    assert ac.read_green_record(green_store) is None
    assert "changed while it ran" in capsys.readouterr().out


def test_red_deletes_a_contradicted_record(green_store):
    ac.record_green(green_store, "fp-1")
    run_m1._settle_green(green_store, "fp-1", False, _keys([]), "run_m1")
    assert ac.read_green_record(green_store) is None


def test_red_leaves_a_record_for_other_content_alone(green_store):
    ac.record_green(green_store, "fp-other")
    run_m1._settle_green(green_store, "fp-1", False, _keys([]), "run_m1")
    record = ac.read_green_record(green_store)
    assert record is not None
    assert record["fingerprint"] == "fp-other"


def _stub_full_run(monkeypatch, *, defect_errors=(), pins=True, pins_in_scope=143, multi_matched=0):
    monkeypatch.setattr(run_m1.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda repo_root: False)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None: {
            "defect_errors": list(defect_errors),
            "notes": [],
        },
    )
    monkeypatch.setattr(
        run_m1,
        "run_manual_pin_gate",
        lambda spec: {
            "pass": pins,
            "disagreements": [],
            "pins_in_scope": pins_in_scope,
            "replayed": pins_in_scope,
        },
    )
    monkeypatch.setattr(
        run_m1,
        "run_oracle",
        lambda spec, jobs, **_cache: {"unmatched": 19837, "multi_matched": multi_matched},
    )


def test_main_refreshes_the_baseline_subset_before_anything_reads_it(monkeypatch, tmp_path, capsys):
    """The five-hand-updates trap, closed: run_m1 ensures the subset tables are current before the pipeline and its oracle run, so an M1_ALPHABET edit can no longer feed the oracle stale tables. The fingerprint stub is order-sensitive — it answers differently before and after the ensure — so the green below records only because the key snapshot happened after the refilter; moving the ensure below the snapshot mismatches the keys and fails this test."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    state = {"ensured": False}
    monkeypatch.setattr(
        ac,
        "run_m1_skip_fingerprint",
        lambda root=None: "fp-post-refilter" if state["ensured"] else "fp-pre-refilter",
    )
    _stub_full_run(monkeypatch)
    events = []

    def ensure(repo_root):
        state["ensured"] = True
        events.append("subset")
        return True

    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", ensure)
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None: events.append("run") or {"defect_errors": [], "notes": []},
    )
    monkeypatch.setattr(
        run_m1,
        "run_oracle",
        lambda spec, jobs, **_cache: events.append("oracle") or {"unmatched": 0, "multi_matched": 0},
    )
    run_m1.main([])
    assert events == ["subset", "run", "oracle"]
    opened, closed = _phases(capsys.readouterr().out)
    assert opened == [
        "baseline_subset",
        "alias_completeness",
        "run_total",
        "run_manual_pin_gate",
        "run_oracle",
    ]
    assert set(opened) <= set(closed)
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-post-refilter"


def test_a_diverged_subset_stops_the_run_before_the_alias_check(monkeypatch):
    """The identity proof moved into the refilter, so the guard's job is to turn its refusal into the same refusal the alias hole gets: a SystemExit carrying the remedy, raised before anything else reads the tables the refilter would not stamp."""
    reached: list[str] = []

    def ensure(repo_root):
        raise run_m1.baseline_subset.SubsetIdentityError("ss06 diverged")

    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", ensure)
    monkeypatch.setattr(
        run_m1.oracle,
        "unaliased_subset_names",
        lambda subset_dir, alias_path: reached.append("aliases") or {},
    )
    with pytest.raises(SystemExit, match="ss06 diverged"):
        run_m1._run_pregate_guards()
    assert reached == []


def test_a_font_provenance_refusal_stops_the_run_before_the_alias_check(monkeypatch):
    """The proof that no stamp can carry, surfaced the same way: a source table whose header names a font other than the one on disk stops the run where the diverged subset stops it, because rows some other font shaped would make every oracle number wrong just as quietly."""
    reached: list[str] = []

    def ensure(repo_root):
        raise run_m1.baseline_subset.BaselineProvenanceError(
            "baseline-ss03.tsv.gz was extracted from another font"
        )

    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", ensure)
    monkeypatch.setattr(
        run_m1.oracle,
        "unaliased_subset_names",
        lambda subset_dir, alias_path: reached.append("aliases") or {},
    )
    with pytest.raises(SystemExit, match="another font"):
        run_m1._run_pregate_guards()
    assert reached == []


def test_unmatched_oracle_rows_record_a_green_and_exit_zero(monkeypatch, tmp_path):
    """Unmatched oracle rows are the mid-migration steady state: they are verdict-gated on the review surface, never a failure of the build, so a run that holds them records its green and exits the way its gate judged."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch)
    run_m1.main([])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-live"


def test_a_multi_matched_oracle_row_fails_the_run_and_clears_the_record(monkeypatch, tmp_path):
    """The oracle's one gate: a row matching two ledger entries is a ledger defect, and the exit status is the judge's verdict."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, multi_matched=2)
    with pytest.raises(SystemExit) as error:
        run_m1.main([])
    assert "multi_matched = 2" in str(error.value)
    assert ac.read_green_record(store) is None
    assert _checks()[0]["verdict"] == "red"


def test_an_interactive_run_files_the_gates_verdict(monkeypatch):
    """What lands on the record is the green the run's own gate reached, unmatched rows notwithstanding. The line carries no run, because nobody drove this one."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch)
    run_m1.main([])
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["check"] == "run_m1"
    assert checks[0]["verdict"] == "green"
    assert "run" not in checks[0]


def test_a_run_that_never_reached_its_judge_files_the_message_it_died_with(monkeypatch):
    """A defect gate that stops the build leaves nothing to judge, so the red carries the sentence it raised and no failed ids — nothing here enumerated a case."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch, defect_errors=["qsAh: contact"])
    with pytest.raises(SystemExit):
        run_m1.main([])
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["verdict"] == "red"
    assert checks[0]["failures"] == ["1 defect-gate errors; see pipeline_summary.json"]
    assert checks[0]["failed_ids"] == []


def test_a_cycle_spawned_run_files_nothing(monkeypatch):
    """The artifact cycle judges run_m1 itself and tags the line with its own run, so the child it spawned stands down on the run id it inherited: one invocation is worth one line, whoever did the work."""
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch)
    run_m1.main([])
    assert _checks() == []


def test_a_defect_gate_failure_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, defect_errors=["qsAh: contact"])
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_a_failed_manual_pin_gate_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, pins=False)
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_a_manual_pin_gate_with_nothing_in_scope_clears_the_record(monkeypatch, tmp_path):
    """The vacuous pass: `pass` is `not disagreements`, so a gate that replayed no pin at all reports green. run_m1 requires the scope too, so an empty replay fails the build rather than certifying it."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, pins_in_scope=0)
    with pytest.raises(SystemExit) as error:
        run_m1.main([])
    assert "no pins in scope" in str(error.value)
    assert ac.read_green_record(store) is None


def test_conform_only_records_its_own_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})
    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": True, "divergences": 0}
    )
    run_m1.main(["--conform-only"])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-conform"


def test_conform_only_divergences_record_no_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": False, "divergences": 3}
    )
    with pytest.raises(SystemExit):
        run_m1.main(["--conform-only"])
    assert ac.read_green_record(store) is None


def test_conform_only_files_its_own_check(monkeypatch):
    """The sweep is its own check, named the way the cycle's gate:conform names it, and it files the judge's status rather than a second spelling invented here. A divergence is a red with the same label the cycle summary prints."""
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})
    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": True, "divergences": 0}
    )
    run_m1.main(["--conform-only"])
    assert [(check["check"], check["status"]) for check in _checks()] == [("conform", "green")]

    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": False, "divergences": 3}
    )
    with pytest.raises(SystemExit):
        run_m1.main(["--conform-only"])
    assert [(check["check"], check["status"]) for check in _checks()][-1] == ("conform", "FAILED")


def test_the_conform_horizon_default_matches_the_cycle_driver(monkeypatch, tmp_path):
    """The horizon is part of the conform green's key, so if run_m1's own default ever drifts from the driver's, an interactive sweep would record a green no cycle can ever match."""
    store = tmp_path / "conform-green.json"
    swept = []
    monkeypatch.setattr(ac, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})

    def fake_sweep(max_length, jobs):
        swept.append(max_length)
        return {"pass": True, "divergences": 0}

    monkeypatch.setattr(run_m1, "run_font_conformance", fake_sweep)
    run_m1.main(["--conform-only"])
    assert swept == [ac.CONFORM_HORIZON_DEFAULT]


class _FinishedFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _InlinePool:
    """A stand-in for the spawn pool that runs each worker where it was submitted, so the oracle's fan-in can be exercised without a process per acceptance configuration and without a build to sweep."""

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def submit(self, function, *args, **kwargs):
        return _FinishedFuture(function(*args, **kwargs))


class TestOracleFanIn:
    """The workers write their own audit shards now, so the order of `divergence-audit.tsv` lives in the parent's concatenation rather than in the order the futures happened to resolve — and what a run that dies partway must not do is leave a short audit where a complete one was, because a short one hashes differently rather than reading as stale and comes back to the surface build as a fresh, smaller one."""

    def _pool(self, monkeypatch, worker):
        monkeypatch.setattr(run_m1, "_spawn_pool", lambda jobs: _InlinePool())
        monkeypatch.setattr(run_m1, "as_completed", lambda futures: reversed(list(futures)))
        monkeypatch.setattr(oracle, "oracle_config_worker", worker)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: None)
        monkeypatch.setattr(
            run_m1,
            "oracle_row_cache_keys",
            lambda spec, out_dir: (
                {},
                {config: oracle_cache.EnvironmentStamp(lines=()) for config in conform.ACCEPTANCE_CONFIGS},
            ),
        )

    def _worker(self, refuse=None, overcount=None, record=None):
        def worker(
            spec,
            subset_tables_dir,
            alias_path,
            ledger_path,
            config,
            font_path,
            kern_sidecar_path,
            audit_dir,
            row_cache=None,
            settle_memo=None,
        ):
            if record is not None:
                record.append(row_cache)
            if config == refuse:
                raise RuntimeError(f"{config} fell over")
            shard = oracle.oracle_audit_shard(audit_dir, config)
            shard.parent.mkdir(parents=True, exist_ok=True)
            with shard.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{config}\tE650\tcell\tpea-half\tqsPea\tqsPea.half\n")
            return oracle.OracleConfigResult(
                config=config, rows_compared=1, divergent_rows=2 if config == overcount else 1
            )

        return worker

    def test_the_audit_follows_acceptance_order_however_the_workers_finish(self, monkeypatch, tmp_path):
        self._pool(monkeypatch, self._worker())
        run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        lines = (tmp_path / "divergence-audit.tsv").read_text(encoding="utf-8").splitlines()
        assert lines[0] == oracle.ORACLE_AUDIT_HEADER
        assert [line.split("\t")[0] for line in lines[1:]] == list(conform.ACCEPTANCE_CONFIGS)
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "divergence-audit.tsv",
            "oracle_summary.json",
        ]

    def test_a_worker_that_falls_over_leaves_the_standing_audit_alone(self, monkeypatch, tmp_path):
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        self._pool(monkeypatch, self._worker(refuse=conform.ACCEPTANCE_CONFIGS[3]))
        with pytest.raises(RuntimeError):
            run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert [path.name for path in tmp_path.iterdir()] == ["divergence-audit.tsv"]

    def test_the_fan_in_counts_the_configurations_as_they_land(self, monkeypatch, tmp_path, capsys):
        """The oracle is the longest stretch of a pass that prints nothing else while it runs, and its acceptance configurations are the only honest denominator it has — so each one that lands says so, in the counter shape the cycle throttles onto the terminal. A count that stops short of the roster is a future the fan-in never collected."""
        self._pool(monkeypatch, self._worker())
        run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        events = [console.parse_line(line) for line in capsys.readouterr().out.splitlines()]
        counters = [event for event in events if isinstance(event, console.Progress)]
        assert [event.text for event in counters] == [
            f"{landed}/{len(conform.ACCEPTANCE_CONFIGS)} configurations"
            for landed in range(1, len(conform.ACCEPTANCE_CONFIGS) + 1)
        ]

    def test_a_key_that_will_not_cut_costs_the_cache_and_not_the_gate(self, monkeypatch, tmp_path, capsys):
        """`alias_family_digests` refuses an alias head no rune digest stands behind, and a hand-edited alias map arrives one typo from that refusal — but the oracle is the gate that adjudicates the ledger, and whether it can run at all must not turn on a file the comparison never reads. A key that will not cut leaves the pass with no cache and nothing else: every row derived, no store written, the gate doing exactly what it did before there was a cache."""
        seen: list = []
        self._pool(monkeypatch, self._worker(record=seen))

        def refuse(spec, out_dir):
            raise ValueError("qsShe.full buckets to 'qsShe', which has no family key")

        monkeypatch.setattr(run_m1, "oracle_row_cache_keys", refuse)
        run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        assert len(seen) == len(conform.ACCEPTANCE_CONFIGS) and set(seen) == {None}
        assert "[warn] oracle row cache: unavailable" in capsys.readouterr().out
        assert (tmp_path / "divergence-audit.tsv").is_file()

    def test_a_pass_that_may_not_write_a_store_rotates_its_coverage(self, monkeypatch, tmp_path):
        """Both mechanisms that keep a record from laundering itself advance on the pass ordinal, and the ordinal advances only when a store is written — so `--gates-only`, which may write nothing and which serves every row of the ledger re-adjudication it exists for, would otherwise retire the same twentieth of the table and re-prove the same sample on every run it ever makes. It declares a rotation instead; a writing pass, whose ordinal moves on its own, declares none."""
        seen: list = []
        self._pool(monkeypatch, self._worker(record=seen))
        run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        assert {cache.rotation for cache in seen} == {0}
        assert {cache.write_dir is None for cache in seen} == {False}

        seen.clear()
        run_m1.run_oracle(out_dir=tmp_path, jobs=6, write_cache=False)
        assert {cache.write_dir for cache in seen} == {None}
        assert all(cache.rotation > 0 for cache in seen)

    def test_an_audit_short_of_the_rows_the_workers_counted_is_refused(self, monkeypatch, tmp_path):
        """The counts reach the parent through the pipe and the rows reach it on disk, so a shard that was truncated but still closed clean shows up as the two disagreeing — the only way the parent can tell a whole audit from most of one."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        self._pool(monkeypatch, self._worker(overcount=conform.ACCEPTANCE_CONFIGS[2]))
        with pytest.raises(ValueError, match="7 divergent"):
            run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert [path.name for path in tmp_path.iterdir()] == ["divergence-audit.tsv"]


class TestGatesOnly:
    """The cheap re-adjudication entry point: everything a full run does after the table build except the stages that make the artifacts — the defect gate, the Manual-pin replay and the oracle — re-run over the build already on disk. Two things it must refuse are a stamp the runes have outgrown and a build that left no summary for the defect fields to be rewritten into. One thing licenses the green it may record: every input that has moved since the last green build being comparison-side, which is what lets a bless of the contact allow-list or a ledger edit leave the next cycle nothing to do while a toolchain bump does not."""

    def _reuse(self, monkeypatch, tables):
        """The stamp and the two pre-gate guards every path crosses before it reaches anything worth asserting about. The guards are stubbed rather than run because both read the live subset tables under rebuild/out, which is exactly what a contracts-lane test may not touch; the returned list is the stage log the ordering test reads."""
        ran: list[str] = []
        monkeypatch.setattr(
            run_m1.baseline_subset, "ensure_fresh", lambda repo_root: ran.append("subset") or False
        )
        monkeypatch.setattr(
            run_m1.oracle,
            "unaliased_subset_names",
            lambda subset_dir, alias_path: ran.append("aliases") or {},
        )
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: "fp")
        monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: tables)
        return ran

    def _summary(self, out_dir, **fields):
        """The summary a completed build left behind, which is the file this pass rewrites the defect fields of rather than authoring from nothing."""
        (out_dir / "pipeline_summary.json").write_text(
            json.dumps({"gsub_rule_count": 7, "font": "M1.otf", **fields}, indent=2) + "\n"
        )

    def _build(self, monkeypatch, tmp_path, ran, *, report=None):
        """The artifacts the pass stands on and the seams behind them: the font the stamp vouches for, the treaty tables the defect gate reads beside the enumeration, the minting, the gate itself stubbed to whatever report the caller wants judged, and the Stage A rewrite."""
        (tmp_path / "M1.otf").write_bytes(b"font")
        monkeypatch.setattr(run_m1, "OUT_DIR", tmp_path)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
        monkeypatch.setattr(run_m1.table_module, "read_treaty_tsv", lambda path: f"treaty {path.name}")
        monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, tables: {})
        monkeypatch.setattr(
            run_m1,
            "_run_defect_gates",
            lambda spec, tables, cell_glyphs: ran.append("defects")
            or (defects.DefectReport() if report is None else report),
        )
        monkeypatch.setattr(
            run_m1.fingerprint, "write_stage_a", lambda repo_root, out_dir: ran.append("stage_a") or {}
        )

    def _green(self, monkeypatch, tmp_path, *, files=None, prior=None, prior_key="fp-prior"):
        """run_m1's green record homed under tmp_path, the key this pass computes over its inputs, and the per-file map it compares against the one the last green build stored. Every path past the summary check reads that record, so a test that omits this reaches rebuild/out and trips the lane guard rather than failing on its own assertion."""
        store = tmp_path / "run-m1-green.json"
        monkeypatch.setattr(ac, "RUN_M1_GREEN", store)
        monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-now")
        monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: dict(files or {}))
        if prior is not None:
            ac.record_green(store, prior_key, files=prior)
        return store

    def _gates(self, monkeypatch, ran, *, replayed=4, multi_matched=0):
        monkeypatch.setattr(
            run_m1,
            "run_manual_pin_gate",
            lambda out_dir, spec: ran.append("pins")
            or {"pass": True, "disagreements": [], "pins_in_scope": 4, "replayed": replayed},
        )
        monkeypatch.setattr(
            run_m1,
            "run_oracle",
            lambda out_dir, spec, jobs, **_cache: ran.append("oracle")
            or {"unmatched": 19837, "multi_matched": multi_matched},
        )

    def test_it_refuses_tables_the_runes_have_outgrown(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, None)
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "it does not make one" in str(error.value)

    def test_it_refuses_a_missing_font(self, monkeypatch, tmp_path):
        self._reuse(monkeypatch, {})
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "no compiled font" in str(error.value)

    @pytest.mark.parametrize("left_behind", [None, "{ not a summary", "[]"])
    def test_it_refuses_a_build_that_left_no_summary_to_rewrite(self, monkeypatch, tmp_path, left_behind):
        """The defect fields are rewritten into the build's own summary, so a build that left none — or left something that is not one — is not a build this pass can stand on. Refusing is what keeps a gates-only pass from authoring a summary no build ever wrote and then judging itself against it."""
        self._reuse(monkeypatch, {})
        (tmp_path / "M1.otf").write_bytes(b"font")
        if left_behind is not None:
            (tmp_path / "pipeline_summary.json").write_text(left_behind)
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "no readable" in str(error.value)
        assert _checks() == []

    def test_it_refuses_a_treaty_table_the_defect_gate_cannot_read(self, monkeypatch, tmp_path):
        """The defect gate reads the treaty tables beside the enumeration, so a stamp that matches over a treaty that will not parse describes a build only half on disk. What that earns is a refusal naming the file, not a traceback out of the gate."""
        ran = self._reuse(monkeypatch, {"ss06": "decision"})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)

        def refuse(path):
            raise OSError("truncated")

        monkeypatch.setattr(run_m1.table_module, "read_treaty_tsv", refuse)
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "treaties-ss06.tsv is missing or unreadable" in str(error.value)

    def test_it_runs_the_guards_then_the_defect_gate_then_the_pins_then_the_oracle(
        self, monkeypatch, tmp_path, capsys
    ):
        """The two pre-gate guards belong to the oracle rather than to the build — an unaliased subset name makes every oracle number quietly wrong — so a pass that re-runs the oracle over a build it did not make runs them exactly as the pass that built does. The defect gate goes first among the three because its errors are what stop the pass before a pin is ever replayed."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran)
        run_m1.main(["--gates-only", "--jobs", "6"])
        assert ran == ["subset", "aliases", "defects", "stage_a", "pins", "oracle"]
        opened, closed = _phases(capsys.readouterr().out)
        assert opened == [
            "baseline_subset",
            "alias_completeness",
            "defect_gates",
            "run_manual_pin_gate",
            "run_oracle",
        ]
        assert set(opened) <= set(closed)

    def test_it_rewrites_only_the_defect_fields_of_the_builds_summary(self, monkeypatch, tmp_path):
        """The gate the allow-list feeds writes its answer back into the summary the build left, so the judge reads this pass's defect verdict rather than the one a build reached before the bless. Everything else in that summary belongs to the build and survives untouched — nothing here recompiled a font or counted a GSUB rule."""
        report = defects.DefectReport(
            flags=[defects.Defect("W-CONTACT", "qsAh~qsBay", "grazes")],
            dead_in_alphabet=["qsZoo", "qsAh"],
            deferred_partner=["qsNo"],
            notes=["blessed one signature"],
        )
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran, report=report)
        self._summary(
            tmp_path,
            settled_cell_glyphs=12,
            defect_errors=["E-CONTACT qsAh~qsBay: ink collision"],
            notes=["what the build said"],
        )
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran)
        run_m1.run_gates_only(out_dir=tmp_path)
        assert json.loads((tmp_path / "pipeline_summary.json").read_text()) == {
            "gsub_rule_count": 7,
            "font": "M1.otf",
            "settled_cell_glyphs": 12,
            "defect_errors": [],
            "notes": ["blessed one signature"],
            "defect_flags": ["W-CONTACT qsAh~qsBay: grazes"],
            "dead_in_alphabet": ["qsAh", "qsZoo"],
            "deferred_partner": ["qsNo"],
        }
        assert "stage_a" in ran

    def test_a_defect_error_exits_red_before_the_pin_gate_and_clears_the_green(self, monkeypatch, tmp_path):
        """A bless that turns out not to cover what it was meant to cover fails here exactly as it fails a full build, with the same sentence, and stops the pass before a pin is replayed: there is nothing to certify about a font whose contacts are unaccounted for. The green it clears is the one the moved input has just contradicted."""
        report = defects.DefectReport(errors=[defects.Defect("E-CONTACT", "qsAh~qsBay", "ink collision")])
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran, report=report)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, prior={}, prior_key="fp-now")
        monkeypatch.setattr(
            run_m1,
            "run_manual_pin_gate",
            lambda **kwargs: pytest.fail("the pin gate ran behind a defect error"),
        )
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert str(error.value) == "1 defect-gate errors; see pipeline_summary.json"
        assert ac.read_green_record(store) is None
        checks = _checks()
        assert len(checks) == 1
        assert checks[0]["verdict"] == "red"
        assert checks[0]["failures"] == ["1 defect-gate errors; see pipeline_summary.json"]
        assert json.loads((tmp_path / "pipeline_summary.json").read_text())["defect_errors"] == [
            "E-CONTACT qsAh~qsBay: ink collision"
        ]

    def test_a_comparison_side_diff_records_the_green_the_next_cycle_skips_on(self, monkeypatch, tmp_path):
        """The whole point of the route. The prior green proves the tables and font on disk came from a completed build over every build-side input, the stamp proves none of those has moved since, and this pass re-proves the gates the moved ones feed; with all three in hand the recorded green covers the new inputs too, so the cycle after a ledger edit skips run_m1 outright instead of re-adjudicating it a second time."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        current = {"rebuild/m1-divergences.yaml": "after", "uv.lock": "pinned"}
        store = self._green(
            monkeypatch,
            tmp_path,
            files=current,
            prior={"rebuild/m1-divergences.yaml": "before", "uv.lock": "pinned"},
        )
        self._gates(monkeypatch, ran)
        run_m1.run_gates_only(out_dir=tmp_path)
        record = ac.read_green_record(store)
        assert record is not None
        assert record["fingerprint"] == "fp-now"
        assert record["files"] == current

    def test_no_prior_green_records_nothing_and_says_why(self, monkeypatch, tmp_path, capsys):
        """A green here is a claim about artifacts this pass did not build, and without a prior green nothing says those artifacts ever came from a completed build at all. The pass still runs and still files its check line — how one invocation came out is a different claim from a license to skip work."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, files={"uv.lock": "pinned"})
        self._gates(monkeypatch, ran)
        run_m1.run_gates_only(out_dir=tmp_path)
        assert not store.exists()
        assert "there is no prior green M1 build for this pass to stand on" in capsys.readouterr().out
        assert [check["verdict"] for check in _checks()] == ["green"]

    def test_a_build_side_input_among_the_moved_records_nothing_and_names_it(
        self, monkeypatch, tmp_path, capsys
    ):
        """uv.lock pins fontTools and uharfbuzz, so a bump there can move the compiled font's bytes and what HarfBuzz makes of them: standing on a font a different toolchain built is the one reuse this must never license. The line names the label that refused it, so the remedy — a full build — is legible without a diff."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(
            monkeypatch,
            tmp_path,
            files={"uv.lock": "bumped", "rebuild/m1-aliases.yaml": "after"},
            prior={"uv.lock": "pinned", "rebuild/m1-aliases.yaml": "before"},
        )
        self._gates(monkeypatch, ran)
        run_m1.run_gates_only(out_dir=tmp_path)
        assert (
            "run_m1: green, but this pass recorded no green — these inputs are build-side, so the artifacts on disk are not the ones they describe: uv.lock"
            in capsys.readouterr().out
        )
        record = ac.read_green_record(store)
        assert record is not None
        assert record["fingerprint"] == "fp-prior"

    def test_inputs_that_never_moved_leave_the_standing_green_where_it_is(
        self, monkeypatch, tmp_path, capsys
    ):
        """Nothing moved is the plain skip's case rather than this one: the green already on the record covers these very inputs, so rewriting it would only restamp it with a later time and claim a fresher proof than this pass made."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        standing = {"rebuild/m1-aliases.yaml": "unchanged"}
        store = self._green(monkeypatch, tmp_path, files=standing, prior=standing)
        self._gates(monkeypatch, ran)
        run_m1.run_gates_only(out_dir=tmp_path)
        assert "nothing has moved since the last green M1 build" in capsys.readouterr().out
        record = ac.read_green_record(store)
        assert record is not None
        assert record["fingerprint"] == "fp-prior"

    def test_it_files_the_re_adjudications_verdict(self, monkeypatch, tmp_path):
        """A ledger edit is re-adjudicated in a loop while the unmatched rows remain; the pass is judged by the same evaluator the cycle runs over a build it reused, files that, and exits the way it judged."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran)
        run_m1.main(["--gates-only"])
        checks = _checks()
        assert len(checks) == 1
        assert checks[0]["check"] == "run_m1"
        assert checks[0]["verdict"] == "green"
        assert "run" not in checks[0]

    def test_a_pin_gate_that_refuses_the_build_files_a_red_and_clears_the_green(self, monkeypatch, tmp_path):
        """The one refusal here that is a judgment rather than a pre-flight: the pins replayed and disagreed, so the record says so and carries the sentence the pass died with. It is a red like the defect gate's and the oracle's, so it settles the green like theirs — a standing record whose key still matches this content is a claim this pass has just contradicted, and leaving it would let the next cycle skip run_m1 on the strength of a build the pins refused."""
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        store = self._green(monkeypatch, tmp_path, prior={}, prior_key="fp-now")
        self._gates(monkeypatch, ran, replayed=3)
        monkeypatch.setattr(
            run_m1, "run_oracle", lambda **kwargs: pytest.fail("the oracle ran behind a failed pin gate")
        )
        with pytest.raises(SystemExit):
            run_m1.main(["--gates-only"])
        assert ac.read_green_record(store) is None
        checks = _checks()
        assert len(checks) == 1
        assert checks[0]["verdict"] == "red"
        assert "replayed 3 of 4 pins" in checks[0]["failures"][0]

    def test_a_pre_flight_refusal_judges_nothing_and_files_nothing(self, monkeypatch, tmp_path):
        """A stamp that no longer matches the runes turns the pass away before any gate runs. Nothing was judged, so nothing belongs on a record of judgments — a red here would put a failure on run_m1's history that no run of run_m1 ever reached."""
        self._reuse(monkeypatch, None)
        with pytest.raises(SystemExit):
            run_m1.run_gates_only(out_dir=tmp_path)
        assert _checks() == []

    def test_a_vacuous_pin_gate_stops_it_before_the_oracle(self, monkeypatch, tmp_path):
        ran = self._reuse(monkeypatch, {})
        self._build(monkeypatch, tmp_path, ran)
        self._summary(tmp_path)
        self._green(monkeypatch, tmp_path)
        self._gates(monkeypatch, ran, replayed=3)
        monkeypatch.setattr(
            run_m1, "run_oracle", lambda **kwargs: pytest.fail("the oracle ran behind a failed pin gate")
        )
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "replayed 3 of 4 pins" in str(error.value)
