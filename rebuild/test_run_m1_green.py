"""Tests for the green records and check lines run_m1 writes, plus its exit handling and the oracle and belt fan-in. An interactive run_m1, `--conform-only`, and `--gates-only` record the same green files the artifact cycle skips on, so a fix verified by hand is not verified again by the next cycle, and each records its verdict as a check line in the timings journal. The verdicts come from artifact_cycle's evaluators (`evaluate_run_m1_gate`, `evaluate_conform_gate`). Unmatched oracle rows are not a failure, so a run that has them records a green and exits zero. `--gates-only` records run_m1's green only when a prior green exists and every input that moved since it is comparison-side (`artifact_cycle.gates_only_reuse`), so the next cycle can skip run_m1 after a ledger edit."""

import gzip
import itertools
import json
import pickle

import pytest

from rebuild.pipeline import conform, defects, fixtures, oracle, oracle_cache, run_m1
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import calibrate_budgets as cb
from rebuild.tools import console
from rebuild.tools import cycle_paths
from rebuild.tools import cycle_timings as ct


@pytest.fixture
def green_store(tmp_path):
    return tmp_path / "run-m1-green.json"


def _checks():
    """The check lines this test recorded. `ct.JOURNAL` is read at call time because rebuild/conftest.py's autouse fixture redirects it under tmp_path. The same fixture removes the cycle's run id from the environment; without that, running this suite inside a real cycle would make every entry point here skip its check line."""
    return ct.load_checks(ct.JOURNAL)


def _phases(output):
    """The phases this run opened and the labels its `[t]` lines closed, parsed with `console.parse_line` as the cycle's digest parses a child's output. A phase with no matching timing would reach the terminal with no duration."""
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


class _JoinedGates:
    """A `TableGates` whose branch has already passed, returned by a stubbed `run` beside its summary, so the two waits, the join, and the close all return at once."""

    def wait_for_replay(self):
        return None

    def wait_for_memo(self):
        return None

    def join(self):
        return None

    def first_red(self):
        return None

    def close(self):
        return None


def _stub_full_run(monkeypatch, *, defect_errors=(), pins=True, pins_in_scope=143, multi_matched=0):
    monkeypatch.setattr(run_m1.oracle, "unaliased_subset_names", lambda subset_dir, alias_path: {})
    monkeypatch.setattr(run_m1.baseline_subset, "ensure_fresh", lambda repo_root: False)
    monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
    monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
    monkeypatch.setattr(ac, "run_m1_skip_files", lambda root=None: {})
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None, memo_inputs=None, replay_threads=None: (
            {"defect_errors": list(defect_errors), "notes": []},
            _JoinedGates(),
        ),
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
    """run_m1 refreshes the subset tables before the build and the oracle read them, so an `M1_ALPHABET` edit cannot feed the oracle stale tables. The fingerprint stub returns a different key before and after the refresh, so the green records only if the key snapshot is taken after the refresh; moving the refresh after the snapshot makes the keys differ and fails this test."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
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
    monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: events.append("outgoing") or {})
    monkeypatch.setattr(
        run_m1,
        "run",
        lambda spec, inputs, kernel_threads=None, memo_inputs=None, replay_threads=None: events.append("run")
        or ({"defect_errors": [], "notes": []}, _JoinedGates()),
    )
    monkeypatch.setattr(
        run_m1,
        "run_oracle",
        lambda spec, jobs, **_cache: events.append("oracle") or {"unmatched": 0, "multi_matched": 0},
    )
    run_m1.main([])
    assert events == ["subset", "outgoing", "run", "oracle"]
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
    """`ensure_fresh` checks the subset identity while it refilters. The guard turns its `SubsetIdentityError` into a SystemExit carrying the message, as it does for a missing alias, before anything reads the tables the refilter would not stamp."""
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
    """A source table whose header names a font other than the one on disk stops the run at the same point as a diverged subset, because rows shaped by another font would make every oracle number wrong."""
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
    """Unmatched oracle rows are normal during the migration. They are judged on the review surface and never fail the build, so a run that has them records its green and exits zero."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch)
    run_m1.main([])
    record = ac.read_green_record(store)
    assert record is not None
    assert record["fingerprint"] == "fp-live"


def test_a_multi_matched_oracle_row_fails_the_run_and_clears_the_record(monkeypatch, tmp_path):
    """A row that matches two ledger entries is a ledger defect and the oracle's only failure condition, so the run exits nonzero, clears the green, and records a red check line."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, multi_matched=2)
    with pytest.raises(SystemExit) as error:
        run_m1.main([])
    assert "multi_matched = 2" in str(error.value)
    assert ac.read_green_record(store) is None
    assert _checks()[0]["verdict"] == "red"


def test_an_interactive_run_files_the_gates_verdict(monkeypatch):
    """The check line records the gate's green despite the unmatched rows. It has no `run` field because no cycle started this run."""
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch)
    run_m1.main([])
    checks = _checks()
    assert len(checks) == 1
    assert checks[0]["check"] == "run_m1"
    assert checks[0]["verdict"] == "green"
    assert "run" not in checks[0]


def test_a_run_that_never_reached_its_judge_files_the_message_it_died_with(monkeypatch):
    """A defect gate that stops the build leaves nothing for the evaluator to judge, so the red check line carries the message the run raised and no failed ids."""
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
    """The artifact cycle records run_m1's check line itself, tagged with its run id, so a run_m1 child that inherits that id records nothing. Each invocation gets one line."""
    monkeypatch.setenv(ct.CYCLE_RUN_ENV, "cafef00d1234")
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    _stub_full_run(monkeypatch)
    run_m1.main([])
    assert _checks() == []


def test_a_defect_gate_failure_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, defect_errors=["qsAh: contact"])
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_a_failed_manual_pin_gate_clears_the_record(monkeypatch, tmp_path):
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, pins=False)
    with pytest.raises(SystemExit):
        run_m1.main([])
    assert ac.read_green_record(store) is None


def test_a_manual_pin_gate_with_nothing_in_scope_clears_the_record(monkeypatch, tmp_path):
    """`pass` is `not disagreements`, so a gate that replayed no pin reports a pass. run_m1 also requires pins in scope, so an empty replay fails the build."""
    store = tmp_path / "run-m1-green.json"
    monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
    monkeypatch.setattr(ac, "run_m1_skip_fingerprint", lambda root=None: "fp-live")
    ac.record_green(store, "fp-live")
    _stub_full_run(monkeypatch, pins_in_scope=0)
    with pytest.raises(SystemExit) as error:
        run_m1.main([])
    assert "no pins in scope" in str(error.value)
    assert ac.read_green_record(store) is None


def test_conform_only_records_its_own_green(monkeypatch, tmp_path):
    store = tmp_path / "conform-green.json"
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
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
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(
        run_m1, "run_font_conformance", lambda max_length, jobs: {"pass": False, "divergences": 3}
    )
    with pytest.raises(SystemExit):
        run_m1.main(["--conform-only"])
    assert ac.read_green_record(store) is None


def test_conform_only_files_its_own_check(monkeypatch):
    """The sweep records its own check line, named `conform` as the cycle names gate:conform, with the status `evaluate_conform_gate` returns. A divergence records `FAILED`, the status the cycle summary prints."""
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
    """The horizon is part of the conform green's key, so if run_m1's default differed from the cycle driver's, an interactive sweep would record a green no cycle could match."""
    store = tmp_path / "conform-green.json"
    swept = []
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})

    def fake_sweep(max_length, jobs):
        swept.append(max_length)
        return {"pass": True, "divergences": 0}

    monkeypatch.setattr(run_m1, "run_font_conformance", fake_sweep)
    run_m1.main(["--conform-only"])
    assert swept == [ac.CONFORM_HORIZON_DEFAULT]


def test_a_hand_conform_only_run_defaults_to_the_belts_budget(monkeypatch, tmp_path):
    """A hand `--conform-only` shares the machine with no surface build and no make-test pool, so its default is `conform_job_budget(skip_gates=True, skip_surface=True)`, not the oracle's `sweep_job_budget()`, which a bare run uses. A stated `--jobs`, including 1, overrides the default. The stub sets the oracle's budget one above the belt's, so a default taken from the wrong budget fails even on a machine where the two are equal."""
    store = tmp_path / "conform-green.json"
    handed = []
    belt = ac.conform_job_budget(skip_gates=True, skip_surface=True)
    monkeypatch.setattr(cycle_paths, "CONFORM_GREEN", store)
    monkeypatch.setattr(ac, "conform_skip_fingerprint", lambda root=None, horizon=4: "fp-conform")
    monkeypatch.setattr(ac, "conform_skip_files", lambda root=None, horizon=4: {})
    monkeypatch.setattr(ac, "sweep_job_budget", lambda ncores=None, total_bytes=None: belt + 1)

    def fake_sweep(max_length, jobs):
        handed.append(jobs)
        return {"pass": True, "divergences": 0}

    monkeypatch.setattr(run_m1, "run_font_conformance", fake_sweep)
    run_m1.main(["--conform-only"])
    run_m1.main(["--conform-only", "--jobs", "1"])
    run_m1.main(["--conform-only", "--jobs", "4"])
    assert handed == [max(1, belt), 1, 4]


class _FinishedFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _InlinePool:
    """A stand-in for the spawn pool that runs each worker when it is submitted, so the oracle's row ranges and the belt's configurations run without a process per unit and without a build to sweep."""

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def submit(self, function, *args, **kwargs):
        return _FinishedFuture(function(*args, **kwargs))


class TestOracleFanIn:
    """Each worker writes its own audit segment, so the order of `divergence-audit.tsv` comes from the parent's concatenation, not from the order the futures resolve. A run that fails partway must leave the previous audit in place: a short audit hashes differently, so the surface build would read it as a new, smaller audit instead of a stale one."""

    def _pool(self, monkeypatch, worker, rows=None):
        """Stubs out every process in the fan-out: an inline pool, futures resolved in reverse, the worker, the crate's guard sweep, and the subset stamp's row counts, which are `rows` or, by default, none, giving one range per configuration."""
        monkeypatch.setattr(run_m1, "_spawn_pool", lambda jobs, units: _InlinePool())
        monkeypatch.setattr(run_m1, "as_completed", lambda futures: reversed(list(futures)))
        monkeypatch.setattr(oracle, "oracle_config_worker", worker)
        monkeypatch.setattr(run_m1.kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(run_m1.kernel_exec, "guard_sweep", lambda spec: {})
        monkeypatch.setattr(run_m1.baseline_subset, "subset_row_counts", lambda out_dir: dict(rows or {}))
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: None)
        monkeypatch.setattr(
            run_m1,
            "oracle_row_cache_keys",
            lambda spec, out_dir: (
                {},
                {config: oracle_cache.EnvironmentStamp(lines=()) for config in conform.ACCEPTANCE_CONFIGS},
            ),
        )

    def _landed(self, out_dir):
        """The files the oracle left in its out directory, excluding the timings journal, which `rebuild/conftest.py` redirects into the same `tmp_path` and the fan-out's pool record writes to."""
        return sorted(path.name for path in out_dir.iterdir() if path.name != ct.JOURNAL.name)

    def _worker(self, refuse=None, overcount=None, record=None, shards=None, multi=None):
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
            guard_verdicts=None,
            shard=None,
        ):
            shard = oracle.OracleShard(config) if shard is None else shard
            if record is not None:
                record.append(row_cache)
            if shards is not None:
                shards.append((shard, settle_memo))
            if shard.label == refuse:
                raise RuntimeError(f"{shard.label} fell over")
            segment = oracle.oracle_audit_shard(audit_dir, config, shard.segment)
            segment.parent.mkdir(parents=True, exist_ok=True)
            with segment.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{config}\t{shard.first_row:04X}\tcell\tpea-half\tqsPea\tqsPea.half\n")
            return oracle.OracleConfigResult(
                config=config,
                rows_compared=1,
                divergent_rows=2 if shard.label == overcount else 1,
                multi_matched_count=0 if multi is None else multi(shard),
            )

        return worker

    def test_the_audit_follows_acceptance_order_however_the_workers_finish(self, monkeypatch, tmp_path):
        self._pool(monkeypatch, self._worker())
        run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        lines = (tmp_path / "divergence-audit.tsv").read_text(encoding="utf-8").splitlines()
        assert lines[0] == oracle.ORACLE_AUDIT_HEADER
        assert [line.split("\t")[0] for line in lines[1:]] == list(conform.ACCEPTANCE_CONFIGS)
        assert self._landed(tmp_path) == ["divergence-audit.tsv", "oracle_summary.json"]

    def test_the_summary_counts_every_ranges_multi_matched_rows(self, monkeypatch, tmp_path):
        """The gate reads `multi_matched` from `oracle_summary.json`, so the file must hold the sum of the counts the ranges return (one per configuration when nothing is cut, one per range when the tables are), or a ledger with overlapping entries would pass. Every range reports at least two, and each cut range one more than the range before it, so a summary that counted ranges instead of summing their counts would be short."""

        def per_range(shard):
            return shard.index + 2

        self._pool(monkeypatch, self._worker(multi=per_range))
        summary = run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        written = json.loads((tmp_path / "oracle_summary.json").read_text())
        assert summary["multi_matched"] == written["multi_matched"] == 2 * len(conform.ACCEPTANCE_CONFIGS)

        rows = {config: 1000 for config in conform.ACCEPTANCE_CONFIGS}
        self._pool(monkeypatch, self._worker(multi=per_range), rows=rows)
        summary = run_m1.run_oracle(out_dir=tmp_path, jobs=10)
        written = json.loads((tmp_path / "oracle_summary.json").read_text())
        planned = oracle.oracle_shard_plan(10, rows)
        assert len(planned) > len(conform.ACCEPTANCE_CONFIGS)
        assert (
            summary["multi_matched"] == written["multi_matched"] == sum(per_range(shard) for shard in planned)
        )

    def test_a_worker_that_falls_over_leaves_the_standing_audit_alone(self, monkeypatch, tmp_path):
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        self._pool(monkeypatch, self._worker(refuse=conform.ACCEPTANCE_CONFIGS[3]))
        with pytest.raises(RuntimeError):
            run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert [path.name for path in tmp_path.iterdir()] == ["divergence-audit.tsv"]

    def test_the_fan_in_counts_the_ranges_as_they_land(self, monkeypatch, tmp_path, capsys):
        """The oracle is the longest part of a pass that prints nothing else while it runs, so it prints a progress counter as each row range finishes, over the number of ranges submitted: one per configuration when the stamp counts no rows, more when it does. A count that stops short of the total is a future the fan-in never collected."""
        self._pool(monkeypatch, self._worker())
        run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        events = [console.parse_line(line) for line in capsys.readouterr().out.splitlines()]
        counters = [event for event in events if isinstance(event, console.Progress)]
        assert [event.text for event in counters] == [
            f"{landed}/{len(conform.ACCEPTANCE_CONFIGS)} shards"
            for landed in range(1, len(conform.ACCEPTANCE_CONFIGS) + 1)
        ]

        seen: list = []
        rows = {config: 1000 for config in conform.ACCEPTANCE_CONFIGS}
        self._pool(monkeypatch, self._worker(shards=seen), rows=rows)
        run_m1.run_oracle(out_dir=tmp_path, jobs=10)
        planned = oracle.oracle_shard_plan(10, rows)
        assert [shard for shard, _memo in seen] == planned and len(planned) > len(conform.ACCEPTANCE_CONFIGS)
        events = [console.parse_line(line) for line in capsys.readouterr().out.splitlines()]
        counters = [event for event in events if isinstance(event, console.Progress)]
        assert [event.text for event in counters] == [
            f"{landed}/{len(planned)} shards" for landed in range(1, len(planned) + 1)
        ]

    def test_a_cut_configurations_audit_follows_row_order_within_acceptance_order(
        self, monkeypatch, tmp_path, capsys
    ):
        """When a configuration is cut into several ranges, the audit is in acceptance order across configurations and row order within one, whichever future resolves first. Each range writes its settle memo windows to its own part, not to the shared file, and a configuration whose parts the parent absorbed gets a `[t] settle_memo_absorb` line."""
        seen: list = []
        rows = {config: 1000 for config in conform.ACCEPTANCE_CONFIGS}
        self._pool(monkeypatch, self._worker(shards=seen), rows=rows)
        memo_inputs = oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="c", data="d")
        monkeypatch.setattr(
            run_m1.conform,
            "settle_memo_files",
            lambda out_dir, spec, inputs: {
                config: conform.SettleMemoFile(out_dir / f"settle-memo-{config}.bin", "stamp")
                for config in conform.SETTLEMENT_CONFIGS
            },
        )
        monkeypatch.setattr(
            run_m1.conform,
            "absorb_settle_memo_parts",
            lambda memo, parts, spec: memo.path.name == "settle-memo-default.bin",
        )
        run_m1.run_oracle(out_dir=tmp_path, jobs=10, memo_inputs=memo_inputs)
        printed = capsys.readouterr().out
        assert "settle memo: absorbed the ranges' parts for default\n" in printed
        assert len([line for line in printed.splitlines() if line.startswith("[t] settle_memo_absorb ")]) == 1
        assert any(line.startswith("[t] settle_memo_absorb default ") for line in printed.splitlines())
        lines = (tmp_path / "divergence-audit.tsv").read_text(encoding="utf-8").splitlines()
        assert lines[0] == oracle.ORACLE_AUDIT_HEADER
        planned = sorted(
            oracle.oracle_shard_plan(10, rows),
            key=lambda shard: (conform.ACCEPTANCE_CONFIGS.index(shard.config), shard.first_row),
        )
        assert [tuple(line.split("\t")[:2]) for line in lines[1:]] == [
            (shard.config, f"{shard.first_row:04X}") for shard in planned
        ]
        for shard, memo in seen:
            if shard.config in conform.OVERLAY_CONFIGS:
                assert memo is None
            else:
                assert memo is not None and memo.write_path == oracle.settle_memo_part(
                    oracle.oracle_audit_scratch(tmp_path), shard.config, shard.index
                )
        assert self._landed(tmp_path) == ["divergence-audit.tsv", "oracle_summary.json"]

    def test_a_range_that_falls_over_leaves_the_standing_audit_alone(self, monkeypatch, tmp_path):
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        rows = {config: 1000 for config in conform.ACCEPTANCE_CONFIGS}
        planned = oracle.oracle_shard_plan(10, rows)
        cut = next(shard for shard in planned if shard.of > 1)
        self._pool(monkeypatch, self._worker(refuse=cut.label), rows=rows)
        with pytest.raises(RuntimeError) as failure:
            run_m1.run_oracle(out_dir=tmp_path, jobs=10)
        assert cut.label in str(failure.value)
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert [path.name for path in tmp_path.iterdir()] == ["divergence-audit.tsv"]

    def _memos(self, monkeypatch, order):
        """Gives every settlement configuration a settle memo file under the run's out directory and replaces the parent's absorb with a stub that records the file and the parts it was passed in `order`. Returns the memo inputs to pass to `run_oracle`."""
        monkeypatch.setattr(
            run_m1.conform,
            "settle_memo_files",
            lambda out_dir, spec, inputs: {
                config: conform.SettleMemoFile(out_dir / f"settle-memo-{config}.bin", "stamp")
                for config in conform.SETTLEMENT_CONFIGS
            },
        )

        def absorb(memo, parts, spec):
            order.append(("absorb", memo.path.name, list(parts)))
            return False

        monkeypatch.setattr(run_m1.conform, "absorb_settle_memo_parts", absorb)
        return oracle_cache.SettleMemoInputs(rune_digests={}, oracle_code="c", data="d")

    def test_every_range_files_a_part_and_the_absorbs_wait_for_memo_ready(self, monkeypatch, tmp_path):
        """No range of the pooled oracle writes a shared settle memo file, cut or not. Each writes the windows it settled to a part, and the parent absorbs every configuration's parts only after every range has finished and `memo_ready` has returned (in `main`, after the witness stage's absorb). So a pool started while the witness stage runs cannot write a file without that stage's windows or have its own windows overwritten by that stage. Nothing is cut here (the stamp counts no rows), so every configuration has one range, the case where a range could otherwise have written the shared file itself."""
        seen: list = []
        order: list = []
        worker = self._worker(shards=seen)

        def recording(*args, **kwargs):
            order.append("range")
            return worker(*args, **kwargs)

        self._pool(monkeypatch, recording)
        memo_inputs = self._memos(monkeypatch, order)
        run_m1.run_oracle(
            out_dir=tmp_path, jobs=6, memo_inputs=memo_inputs, memo_ready=lambda: order.append("ready")
        )
        scratch = oracle.oracle_audit_scratch(tmp_path)
        ranges = len(conform.ACCEPTANCE_CONFIGS)
        assert order[: ranges + 1] == ["range"] * ranges + ["ready"]
        assert order[ranges + 1 :] == [
            ("absorb", f"settle-memo-{config}.bin", [oracle.settle_memo_part(scratch, config, 0)])
            for config in conform.SETTLEMENT_CONFIGS
        ]
        for shard, memo in seen:
            assert shard.of == 1
            if shard.config in conform.OVERLAY_CONFIGS:
                assert memo is None
            else:
                assert memo is not None and memo.write_path == oracle.settle_memo_part(
                    scratch, shard.config, 0
                )

    def test_the_serial_oracle_calls_memo_ready_before_its_first_walk(self, monkeypatch, tmp_path):
        """At `--jobs 1` the walks rewrite the shared settle memo files as they go, so `memo_ready` is called before the first walk."""
        order: list = []
        self._pool(monkeypatch, self._worker())
        memo_inputs = self._memos(monkeypatch, order)

        def compare(*args, **kwargs):
            order.append("compare")
            raise RuntimeError("the serial compare started")

        monkeypatch.setattr(run_m1.oracle, "compare_against_baseline", compare)
        with pytest.raises(RuntimeError, match="the serial compare started"):
            run_m1.run_oracle(
                out_dir=tmp_path, jobs=1, memo_inputs=memo_inputs, memo_ready=lambda: order.append("ready")
            )
        assert order == ["ready", "compare"]

    def test_a_red_memo_ready_stops_the_oracle_before_any_absorb(self, monkeypatch, tmp_path):
        """A witness stage that fails while the pool runs reaches the oracle as `memo_ready` raising its error. No configuration's parts are absorbed into a memo file, and the previous audit stays in place, as it does when a range fails."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        order: list = []
        self._pool(monkeypatch, self._worker())
        memo_inputs = self._memos(monkeypatch, order)

        def red():
            raise conform.WitnessError("1 rule(s) whose certificate does not fire them")

        with pytest.raises(conform.WitnessError):
            run_m1.run_oracle(out_dir=tmp_path, jobs=6, memo_inputs=memo_inputs, memo_ready=red)
        assert order == []
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert self._landed(tmp_path) == ["divergence-audit.tsv"]

    def test_a_cut_configurations_store_is_joined_from_its_ranges_segments_and_read_back(
        self, monkeypatch, tmp_path
    ):
        """Runs the parent's row-store handling through `run_oracle`: each range stages a segment through `open_row_cache`, the parent joins a cut configuration's segments under the header its ranges agreed on and promotes every configuration's store, and the next pass's ranges load the joined store through the same reader and write under the next ordinal. The promoted store must equal what one writer over the whole table writes, which checks that the join's arguments (the stamp, the subset digest, the ordinal, the keys, and the row count) come from the range results."""
        spec = fixtures.mini_spec()
        rows = {config: 1000 for config in conform.ACCEPTANCE_CONFIGS}
        stamps = {
            config: oracle_cache.EnvironmentStamp(lines=(f"subset\t{config}-digest",))
            for config in conform.ACCEPTANCE_CONFIGS
        }
        keys = {"qsPea": "pea-key"}
        loaded: list = []

        def worker(
            _spec,
            subset_tables_dir,
            alias_path,
            ledger_path,
            config,
            font_path,
            kern_sidecar_path,
            audit_dir,
            row_cache=None,
            settle_memo=None,
            guard_verdicts=None,
            shard=None,
        ):
            shard = oracle.OracleShard(config) if shard is None else shard
            audit = oracle.oracle_audit_shard(audit_dir, config, shard.segment)
            audit.parent.mkdir(parents=True, exist_ok=True)
            audit.write_text("", encoding="utf-8")
            stop = rows[config] if shard.stop_row is None else shard.stop_row
            store, writer = oracle.open_row_cache(row_cache, spec, config, shard.segment)
            assert writer is not None
            loaded.append((shard.label, None if store is None else store.pass_ordinal))
            with writer:
                for index in range(shard.first_row, stop):
                    writer.append((0xE650, 0xE650 + index), None, writer.pass_ordinal)
            return oracle.OracleConfigResult(
                config=config, rows_compared=stop - shard.first_row, pass_ordinal=writer.pass_ordinal
            )

        self._pool(monkeypatch, worker, rows=rows)
        monkeypatch.setattr(
            run_m1, "oracle_row_cache_keys", lambda _spec, out_dir: (dict(keys), dict(stamps))
        )
        assert any(shard.of > 1 for shard in oracle.oracle_shard_plan(10, rows))
        run_m1.run_oracle(out_dir=tmp_path, jobs=10)
        assert loaded and all(ordinal is None for _label, ordinal in loaded)
        for config in conform.ACCEPTANCE_CONFIGS:
            promoted = oracle_cache.store_path(tmp_path, config)
            whole = tmp_path / f"whole-{config}.tsv.gz"
            with oracle_cache.RowWriter(whole, stamps[config], f"{config}-digest", 0, keys) as writer:
                for index in range(rows[config]):
                    writer.append((0xE650, 0xE650 + index), None, 0)
            assert gzip.decompress(promoted.read_bytes()) == gzip.decompress(whole.read_bytes())
            whole.unlink()
            store = oracle_cache.load_store(promoted, stamps[config], f"{config}-digest", spec, keys)
            assert store is not None and store.rows == rows[config] and store.pass_ordinal == 0

        loaded.clear()
        run_m1.run_oracle(out_dir=tmp_path, jobs=10)
        assert loaded and all(ordinal == 0 for _label, ordinal in loaded)
        for config in conform.ACCEPTANCE_CONFIGS:
            promoted = oracle_cache.store_path(tmp_path, config)
            store = oracle_cache.load_store(promoted, stamps[config], f"{config}-digest", spec, keys)
            assert store is not None and store.rows == rows[config] and store.pass_ordinal == 1

    def test_the_belts_pool_is_never_wider_than_the_acceptance_configurations(self):
        """The cycle passes the belt a width already capped at the acceptance configuration count (`artifact_cycle.conform_job_budget`), but a hand `--jobs` can be any number, so `_spawn_pool` caps the pool at one process per configuration. A smaller number narrows the pool."""
        wide = run_m1._spawn_pool(64, len(conform.ACCEPTANCE_CONFIGS))
        try:
            width = wide._max_workers  # pyright: ignore[reportAttributeAccessIssue]
            assert width == len(conform.ACCEPTANCE_CONFIGS)
        finally:
            wide.shutdown(wait=False)
        narrow = run_m1._spawn_pool(2, 15)
        try:
            assert narrow._max_workers == 2  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            narrow.shutdown(wait=False)

    def test_a_key_that_will_not_cut_costs_the_cache_and_not_the_gate(self, monkeypatch, tmp_path, capsys):
        """`alias_family_digests` raises on an alias head with no rune digest, which one typo in the hand-edited alias map causes. The oracle is the gate that judges the ledger, so a failure in the cache keys must not stop it. When the keys cannot be computed, the pass runs without the cache: every row is derived and no store is written."""
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
        """The renewal slice and the verification sample that keep a wrong record from being served forever both advance on the pass ordinal, which advances only when a store is written. `--gates-only` writes no store, so without a rotation it would renew the same slice and verify the same sample on every run. A pass that writes no store therefore passes a nonzero `rotation`; a writing pass passes zero."""
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
        """The counts reach the parent through the pool and the rows reach it on disk, so a segment that was truncated but closed cleanly shows up as a mismatch between the two. That mismatch is the only way the parent can tell a complete audit from a partial one."""
        standing = tmp_path / "divergence-audit.tsv"
        standing.write_bytes(b"the audit of the last green run\n")
        self._pool(monkeypatch, self._worker(overcount=conform.ACCEPTANCE_CONFIGS[2]))
        with pytest.raises(ValueError, match="7 divergent"):
            run_m1.run_oracle(out_dir=tmp_path, jobs=6)
        assert standing.read_bytes() == b"the audit of the last green run\n"
        assert self._landed(tmp_path) == ["divergence-audit.tsv"]


class TestConformFanIn:
    """The belt's fan-in writes its workers' peak memory as a `conform-belt` pool record, writes none for a serial or deeper sweep, and writes the same report as the serial belt at any width. Only the sweep is stubbed (`conform._conformance_config`, which the serial path and every worker call), so the real wrapper, worker, `run_conformance`, and merge run in every mode."""

    @staticmethod
    def _swept(
        shaper,
        spec,
        config,
        alphabet,
        splitters,
        glyph_names,
        anchors_of,
        max_length,
        guard_verdicts=None,
        settle_memo=None,
    ):
        """A deterministic sweep whose every field depends on its configuration, so a merge in completion order instead of acceptance order would write a different report."""
        index = conform.ACCEPTANCE_CONFIGS.index(config)
        return conform.ConformanceConfigResult(
            config=config,
            sequences=100,
            shaping_runs=100 + index,
            divergences=[
                conform.Divergence(
                    text=chr(0xE650 + index),
                    config=config,
                    position=index,
                    expected="qsPea",
                    got="qsPea.alt",
                    kind=f"kind-{config}",
                )
            ],
            notes=[f"{config}: swept at {max_length}"],
            modes=[f"mode-{index % 2}"],
        )

    def _pool(self, monkeypatch):
        """Stubs out every process and build input in the fan-out: an inline pool, futures resolved in reverse, the crate, no spec, no tables, and no memo, plus fakes for what the worker and `run_conformance` build before the sweep, since the spec is None."""
        monkeypatch.setattr(run_m1, "_spawn_pool", lambda jobs, units: _InlinePool())
        monkeypatch.setattr(run_m1, "as_completed", lambda futures: reversed(list(futures)))
        monkeypatch.setattr(run_m1.kernel_exec, "ensure_built", lambda: None)
        monkeypatch.setattr(run_m1.kernel_exec, "guard_sweep", lambda spec: {})
        monkeypatch.setattr(run_m1, "tables_inputs", lambda: None)
        monkeypatch.setattr(run_m1, "settle_memo_inputs", lambda: None)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: None)
        monkeypatch.setattr(run_m1, "serialized_tables", lambda out_dir, inputs: {})
        monkeypatch.setattr(run_m1, "mint_cell_glyphs", lambda spec, decisions: {})
        monkeypatch.setattr(conform, "settle_memo_files", lambda out_dir, spec, inputs: {})
        monkeypatch.setattr(conform, "Shaper", lambda font_path: object())
        monkeypatch.setattr(conform, "spec_alphabet", lambda spec: ())
        monkeypatch.setattr(conform, "splitting_boundary_chars", lambda spec: frozenset())
        monkeypatch.setattr(conform, "_conformance_config", self._swept)

    def test_a_pooled_belt_files_one_conform_belt_pool_record(self, monkeypatch, tmp_path):
        """A pooled belt writes one record, at the pool's width, with one observation per acceptance configuration, under a unit name listed in `calibrate_budgets.UNITS`; a record under an unlisted name would be ignored, as if the belt had never run pooled on this machine. Every peak reading is distinct, and the inline pool runs each worker at submission in acceptance order before the controller reads its own peak, so a fan-in that recorded one configuration's peak under another, or the controller's under a worker, would fail."""
        self._pool(monkeypatch)
        assert "conform-belt" in {name for unit in cb.UNITS for name in unit.pool_units}
        configs = conform.ACCEPTANCE_CONFIGS
        for jobs in (6, 2):
            monkeypatch.setattr(run_m1, "peak_rss_self_bytes", itertools.count(1).__next__)
            before = len(ct.load_pool_records(ct.JOURNAL))
            run_m1.run_font_conformance(out_dir=tmp_path, jobs=jobs)
            records = ct.load_pool_records(ct.JOURNAL)[before:]
            assert len(records) == 1
            (record,) = records
            assert record["unit"] == "conform-belt"
            assert record["width"] == min(jobs, len(configs))
            assert record["worker_peak_rss_bytes"] == {
                config: index + 1 for index, config in enumerate(configs)
            }
            assert record["controller_peak_rss_bytes"] == len(configs) + 1

    def test_a_serial_or_deep_belt_files_no_pool_record(self, monkeypatch, tmp_path):
        """The serial belt starts no pool to measure. A deeper sweep's worker holds its horizon's windows in memory, a different load from a belt worker's, so it must not be recorded as a belt worker."""
        self._pool(monkeypatch)
        run_m1.run_font_conformance(out_dir=tmp_path, jobs=1)
        run_m1.run_font_conformance(out_dir=tmp_path, max_length=conform.BELT_HORIZON + 1, jobs=6)
        assert ct.load_pool_records(ct.JOURNAL) == []

    def test_the_belt_writes_the_same_summary_at_every_width(self, monkeypatch, tmp_path):
        """Width 1 runs `conform.run_conformance` and widths 2 and 6 run the pooled fan-in, whose futures resolve here in reverse. The report is byte-identical at all three widths, so changing the belt's width does not change its report."""
        self._pool(monkeypatch)
        written = {}
        for jobs in (1, 2, 6):
            run_m1.run_font_conformance(out_dir=tmp_path, jobs=jobs)
            written[jobs] = (tmp_path / "conform_summary.json").read_bytes()
        assert written[1] == written[2] == written[6]
        summary = json.loads(written[1])
        assert list(summary["divergences_by_kind"]) == [
            f"kind-{config}" for config in conform.ACCEPTANCE_CONFIGS
        ]

    def test_the_priced_worker_pickles_for_spawn(self):
        """The inline pool never pickles what it runs, but a spawn pool pickles every submission by module and name, so a nested wrapper would pass every other test here and fail only on the first real pooled belt."""
        assert (
            pickle.loads(pickle.dumps(run_m1._priced_conformance_config)) is run_m1._priced_conformance_config
        )


class TestOracleShardPlan:
    """Invariants of `oracle_shard_plan`, a pure function: one configuration's ranges cover its table contiguously with the last one open-ended, a configuration the stamp does not count stays whole, the overlay configuration's rows weigh half, the ranges come back heaviest first, and each range is at most one worker's share."""

    ROWS = {config: 1_082_400 for config in conform.ACCEPTANCE_CONFIGS}

    def test_one_configurations_ranges_tile_its_table(self):
        plan = oracle.oracle_shard_plan(10, self.ROWS)
        for config in conform.ACCEPTANCE_CONFIGS:
            ranges = sorted(
                (shard for shard in plan if shard.config == config), key=lambda shard: shard.first_row
            )
            assert [shard.index for shard in ranges] == list(range(len(ranges)))
            assert {shard.of for shard in ranges} == {len(ranges)}
            assert ranges[0].first_row == 0 and ranges[-1].stop_row is None
            assert all(left.stop_row == right.first_row for left, right in zip(ranges, ranges[1:]))
            assert all(shard.stop_row is None or shard.stop_row > shard.first_row for shard in ranges)

    def test_the_pieces_are_a_workers_share_apiece_heaviest_first(self):
        plan = oracle.oracle_shard_plan(10, self.ROWS)
        assert len(conform.ACCEPTANCE_CONFIGS) < len(plan) <= 10 + len(conform.ACCEPTANCE_CONFIGS)

        def weight(shard):
            rows = (self.ROWS[shard.config] if shard.stop_row is None else shard.stop_row) - shard.first_row
            return rows * (oracle.OVERLAY_ROW_COST if shard.config in conform.OVERLAY_CONFIGS else 1)

        weights = [weight(shard) for shard in plan]
        assert weights == sorted(weights, reverse=True)
        total = sum(weights)
        assert max(weights) <= total / 10 + 1

    def test_the_overlay_configuration_weighs_half_a_settlement_one(self):
        plan = oracle.oracle_shard_plan(len(conform.ACCEPTANCE_CONFIGS) * 2, self.ROWS)
        by_config = {
            config: sum(shard.config == config for shard in plan) for config in conform.ACCEPTANCE_CONFIGS
        }
        assert all(by_config[config] < by_config["default"] for config in conform.OVERLAY_CONFIGS)

    def test_an_uncounted_configuration_stays_whole_and_one_worker_cuts_nothing(self):
        counted = {**self.ROWS, "ss03": None}
        del counted["ss05"]
        plan = oracle.oracle_shard_plan(10, counted)
        whole = [shard for shard in plan if shard.of == 1]
        assert {shard.config for shard in whole} >= {"ss03", "ss05"}
        assert all(shard.first_row == 0 and shard.stop_row is None for shard in whole)
        assert [shard.config for shard in plan[:2]] == ["ss03", "ss05"]
        serial = oracle.oracle_shard_plan(1, self.ROWS)
        assert [shard.config for shard in serial] == list(conform.ACCEPTANCE_CONFIGS)
        assert all(shard == oracle.OracleShard(shard.config) for shard in serial)
        assert oracle.oracle_shard_plan(10, {}) == serial

    def test_a_range_names_its_segment_and_its_label(self):
        assert oracle.OracleShard("default").segment is None
        assert oracle.OracleShard("default").label == "default"
        cut = oracle.OracleShard("ss03+ss05", 10, 20, 1, 3)
        assert cut.segment == 1 and cut.label == "ss03+ss05 2/3"


class TestGatesOnly:
    """`--gates-only` re-runs the defect gate, the Manual-pin replay, and the oracle over the build already on disk, without the stages that make the artifacts. It exits with an error on a stamp that no longer matches the runes and on a build that left no summary to rewrite the defect fields into. It records a green only when every input that moved since the last green build is comparison-side, so a bless of the contact allow-list or a ledger edit leaves the next cycle nothing to do, while a toolchain bump does not."""

    def _reuse(self, monkeypatch, tables):
        """Stubs the stamp check and the two pre-gate guards that every path runs first. The guards are stubbed because both read the live subset tables under rebuild/out, which a contracts-lane test may not read. Returns the stage log the ordering test reads."""
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
        """Writes the summary a completed build leaves, whose defect fields this pass rewrites."""
        (out_dir / "pipeline_summary.json").write_text(
            json.dumps({"gsub_rule_count": 7, "font": "M1.otf", **fields}, indent=2) + "\n"
        )

    def _build(self, monkeypatch, tmp_path, ran, *, report=None):
        """Stubs the build this pass reuses: the font the stamp covers, the treaty tables the defect gate reads beside the enumeration, the minting, the defect gate (returning `report`), and the Stage A rewrite."""
        (tmp_path / "M1.otf").write_bytes(b"font")
        monkeypatch.setattr(run_m1, "OUT_DIR", tmp_path)
        monkeypatch.setattr(run_m1, "load_default_spec", lambda: object())
        monkeypatch.setattr(run_m1, "run_ligature_outgoing", lambda spec: {})
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
        """Stubs run_m1's green record under tmp_path, the key this pass computes over its inputs, and the per-file map it compares with the one the last green build stored. Every path past the summary check computes that key, so a test that omits this hashes inputs under rebuild/out and fails on the lane guard instead of its own assertion."""
        store = tmp_path / "run-m1-green.json"
        monkeypatch.setattr(cycle_paths, "RUN_M1_GREEN", store)
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
        """The defect fields are rewritten into the build's own summary, so the pass exits with an error when the build left none or left something that is not a summary. Otherwise it would write a summary no build produced and judge itself against it."""
        self._reuse(monkeypatch, {})
        (tmp_path / "M1.otf").write_bytes(b"font")
        if left_behind is not None:
            (tmp_path / "pipeline_summary.json").write_text(left_behind)
        with pytest.raises(SystemExit) as error:
            run_m1.run_gates_only(out_dir=tmp_path)
        assert "no readable" in str(error.value)
        assert _checks() == []

    def test_it_refuses_a_treaty_table_the_defect_gate_cannot_read(self, monkeypatch, tmp_path):
        """The defect gate reads the treaty tables beside the enumeration, so a matching stamp over a treaty table that will not parse means the build is only partly on disk. The pass exits with an error naming the file instead of a traceback from the gate."""
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
        """The two pre-gate guards protect the oracle, not the build (an unaliased subset name makes every oracle number wrong), so a pass that re-runs the oracle over an existing build runs them as a full build does. The defect gate runs first among the three gates because its errors stop the pass before any pin is replayed."""
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
        """The defect gate writes its result into the summary the build left, so the evaluator reads this pass's defect result, not the one from before the bless. The rest of the summary belongs to the build and is left as it was, since this pass compiles no font and counts no GSUB rule."""
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
        """A bless that does not cover a contact fails here with the same message as in a full build, and stops the pass before any pin is replayed. The pass clears the green its key matches, because the failure contradicts it."""
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
        """The prior green shows the tables and font on disk came from a completed build over every build-side input, the stamp shows none of those has changed since, and this pass re-runs the gates the changed inputs feed. Together these make the recorded green cover the new inputs, so the cycle after a ledger edit skips run_m1."""
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
        """A green here is a claim about artifacts this pass did not build, and without a prior green nothing shows those artifacts came from a completed build. The pass still runs and records its check line, which reports only how this invocation came out and does not let a later pass skip work."""
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
        """uv.lock pins fontTools and uharfbuzz, so a bump there can change the compiled font's bytes and how HarfBuzz shapes them, and a font built by another toolchain must not be reused. The printed line names the build-side input, so it is clear without a diff that a full build is needed."""
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
        """When nothing has changed, the existing green already covers these inputs, so the pass leaves it alone instead of rewriting it with a later time."""
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
        """The pass is judged by the same evaluator the cycle uses, records that verdict as a check line with no `run` field, and exits accordingly."""
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
        """A Manual-pin gate that replays only 3 of the 4 pins in scope fails the pass, which records a red check line carrying its message and clears the green its key matches. Leaving that green would let the next cycle skip run_m1 on a build the pin gate failed."""
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
        """A stamp that no longer matches the runes stops the pass before any gate runs. Nothing was judged, so the pass records no check line."""
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
