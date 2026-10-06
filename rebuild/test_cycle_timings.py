import json
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rebuild.tools import cycle_timings as ct
from rebuild.tools import memory_budget


def _result(name="run_m1", rc=0, stdout="", stderr="", elapsed=1.0):
    return SimpleNamespace(name=name, returncode=rc, stdout=stdout, stderr=stderr, elapsed=elapsed)


def _check_result(
    check="make-test",
    outcome="green",
    status="green",
    failures=None,
    failed_ids=None,
    recordable=False,
):
    return ct.CheckResult(
        check=check,
        outcome=outcome,
        status=status,
        failures=list(failures or []),
        failed_ids=list(failed_ids or []),
        recordable=recordable,
    )


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_journal(path, entries):
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def test_parse_inner_timings_reads_label_and_seconds():
    assert ct.parse_inner_timings("[t] run_m1 12.3s") == [{"label": "run_m1", "elapsed_s": 12.3}]


def test_parse_inner_timings_accepts_integer_seconds():
    assert ct.parse_inner_timings("[t] gate:js 3s") == [{"label": "gate:js", "elapsed_s": 3.0}]


def test_parse_inner_timings_strips_trailing_extras():
    text = "\n".join(
        [
            "[t] conform[default] 5.5s shaping_runs=123",
            "[t] build_tables 2.0s (refiltered)",
            "[t] settle 1.5s\tqueued=4",
        ]
    )
    assert ct.parse_inner_timings(text) == [
        {"label": "conform[default]", "elapsed_s": 5.5},
        {"label": "build_tables", "elapsed_s": 2.0},
        {"label": "settle", "elapsed_s": 1.5},
    ]


def test_parse_inner_timings_reads_a_trailing_rss_token():
    assert ct.parse_inner_timings("[t] build_tables_total 243.1s rss_gb=8.94") == [
        {"label": "build_tables_total", "elapsed_s": 243.1, "rss_gb": 8.94}
    ]
    assert ct.parse_inner_timings("[t] conform[default] 5.5s shaping_runs=123 rss_gb=0.80") == [
        {"label": "conform[default]", "elapsed_s": 5.5, "rss_gb": 0.8}
    ]
    assert ct.parse_inner_timings("[t] review.build plan 9.9s rss_gb=5.28 rss_now_gb=4.02") == [
        {"label": "review.build plan", "elapsed_s": 9.9, "rss_gb": 5.28, "rss_now_gb": 4.02}
    ]
    assert ct.parse_inner_timings("[t] review.build plan 9.9s rss_now_gb=4.02") == [
        {"label": "review.build plan", "elapsed_s": 9.9, "rss_now_gb": 4.02}
    ]


def test_parse_inner_timings_reads_the_corpus_builds_phase_lines():
    """The corpus build's phase lines carry the peak token, then the current-RSS token when the platform reports one, then a tab-separated note. `--inner` reads each phase's peak and current RSS from this form."""
    text = (
        "[t] review.build load 12.3s rss_gb=1.23 rss_now_gb=1.20\t(signatures: 40 cached, 2 shaped across 8 workers)\n"
        "[t] review.build units 900.0s rss_gb=15.40 rss_now_gb=9.75\t(jobs=1, recomputed=1,000,000, verified=0 cached)\n"
        "[t] review.build manifest+check 300.5s rss_gb=17.10\n"
    )
    assert ct.parse_inner_timings(text) == [
        {"label": "review.build load", "elapsed_s": 12.3, "rss_gb": 1.23, "rss_now_gb": 1.2},
        {"label": "review.build units", "elapsed_s": 900.0, "rss_gb": 15.4, "rss_now_gb": 9.75},
        {"label": "review.build manifest+check", "elapsed_s": 300.5, "rss_gb": 17.1},
    ]


def test_parse_inner_timings_reads_the_table_builds_record():
    """run_m1's `kernel_build_tables` line ends with the table build's record, and each token is kept under its key, typed by its reader: the runes and classes a reused memo excluded as lists, `-` and an empty value as none, and the counts as integers. A value its reader rejects is left out."""
    text = "\n".join(
        [
            "[t] previous_memos 1.9s edited= classes=-",
            "[t] kernel_build_tables 220.2s structure=454ec84a code=4a63df81 memos_read=5 edited=qsTea,qsWay classes=- width=5 runes=44",
            "[t] kernel_build_tables 3.0s memos_read=all width=5",
        ]
    )
    assert ct.parse_inner_timings(text) == [
        {"label": "previous_memos", "elapsed_s": 1.9, "edited": [], "classes": []},
        {
            "label": "kernel_build_tables",
            "elapsed_s": 220.2,
            "structure": "454ec84a",
            "code": "4a63df81",
            "memos_read": 5,
            "edited": ["qsTea", "qsWay"],
            "classes": [],
            "width": 5,
            "runes": 44,
        },
        {"label": "kernel_build_tables", "elapsed_s": 3.0, "width": 5},
    ]


def test_parse_inner_timings_reads_the_readback_figures():
    """run_m1's `readback` line ends with the commit HEAD was at and the settlement lookup's figures; the commit is kept as text and each figure as an integer."""
    text = "[t] readback 41.2s commit=95521363 settle_format2=2298 settle_format3=927 subtable_offset_headroom=27997 gsub_lookups=549 settle_rules=29214 largest_group_rule_bytes=33542"
    assert ct.parse_inner_timings(text) == [
        {
            "label": "readback",
            "elapsed_s": 41.2,
            "commit": "95521363",
            "settle_format2": 2298,
            "settle_format3": 927,
            "subtable_offset_headroom": 27997,
            "gsub_lookups": 549,
            "settle_rules": 29214,
            "largest_group_rule_bytes": 33542,
        }
    ]


def test_parse_inner_timings_ignores_lines_without_seconds():
    assert ct.parse_inner_timings("[t] build_tables[default] done") == []
    assert ct.parse_inner_timings("plain noise\nnot a [t] line 3.0s") == []


def test_parse_inner_timings_consecutive_lines_both_match():
    assert ct.parse_inner_timings("[t] a 1.0s\n[t] b 2.0s") == [
        {"label": "a", "elapsed_s": 1.0},
        {"label": "b", "elapsed_s": 2.0},
    ]


def test_parse_inner_timings_finds_lines_amid_other_output():
    text = "building...\n[t] phase-a 3.5s\n1234 rows written\n[t] phase-b 0.5s\ndone\n"
    assert [item["label"] for item in ct.parse_inner_timings(text)] == ["phase-a", "phase-b"]


def test_record_step_writes_one_step_line(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    timings.record_step(_result(elapsed=12.34), ["uv", "run", "fake"])
    (entry,) = _lines(path)
    assert entry == {
        "format": ct.FORMAT,
        "kind": "step",
        "run": timings.run_id,
        "host": timings.host,
        "name": "run_m1",
        "argv": ["uv", "run", "fake"],
        "rc": 0,
        "elapsed_s": 12.3,
        "finished_at": entry["finished_at"],
    }
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["finished_at"])


def test_record_step_carries_inner_timings_from_both_streams(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    timings.record_step(_result(stdout="[t] phase-a 3.5s", stderr="[t] phase-b 2s"), [])
    (entry,) = _lines(path)
    assert entry["inner"] == [
        {"label": "phase-a", "elapsed_s": 3.5},
        {"label": "phase-b", "elapsed_s": 2.0},
    ]


def test_record_step_carries_the_step_peak_rss_when_measured(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    result = _result()
    result.peak_rss_bytes = 8_940_000_000
    timings.record_step(result, [])
    (entry,) = _lines(path)
    assert entry["peak_rss_bytes"] == 8_940_000_000
    timings.record_step(_result(), [])
    assert "peak_rss_bytes" not in _lines(path)[1]


def test_wrap_spawn_passes_through_and_records(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    seen = {}

    def spawn(name, argv, *, emit, registry, stream):
        seen.update(name=name, argv=argv, emit=emit, registry=registry, stream=stream)
        return _result(name=name, rc=3, elapsed=0.0)

    timed = timings.wrap_spawn(spawn)
    result = timed("gate:js", ["cmd"], emit="E", registry="R", stream=True)
    assert result.returncode == 3
    assert seen == {"name": "gate:js", "argv": ["cmd"], "emit": "E", "registry": "R", "stream": True}
    (entry,) = _lines(path)
    assert (entry["name"], entry["rc"], entry["elapsed_s"]) == ("gate:js", 3, 0.0)


def test_wrap_spawn_skips_only_the_never_started_sentinel(tmp_path):
    path = tmp_path / "j.ndjson"
    timed = ct.CycleTimings(path).wrap_spawn(
        lambda name, argv, **kwargs: _result(name=name, rc=130, elapsed=0.0)
    )
    timed("run_m1", [], emit=None, registry=None, stream=False)
    assert not path.exists()
    timed = ct.CycleTimings(path).wrap_spawn(
        lambda name, argv, **kwargs: _result(name=name, rc=130, elapsed=2.5)
    )
    timed("run_m1", [], emit=None, registry=None, stream=False)
    (entry,) = _lines(path)
    assert (entry["rc"], entry["elapsed_s"]) == (130, 2.5)


def test_finish_copies_the_summary_blocks(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    payload = {
        "exit": "ok",
        "interrupted": False,
        "failures": [],
        "gates": {"js": {"status": "green"}},
        "plan": {"short_id": "abc"},
        "argv": ["prog", "--fresh"],
        "carry": {"human": 60000, "matched": 51946, "unmatched": 8054, "orphaned": 12},
        "facts_status": "clean",
    }
    timings.finish(payload)
    (entry,) = _lines(path)
    assert entry["kind"] == "run"
    assert entry["format"] == ct.FORMAT
    assert entry["run"] == timings.run_id
    assert entry["host"] == timings.host
    assert entry["cpu_count"] == os.cpu_count()
    assert entry["mem_total_bytes"] == memory_budget.total_memory_bytes()
    assert entry["started_at"] == timings.started_at
    assert entry["wall_s"] >= 0.0
    for key in ("exit", "interrupted", "failures", "gates", "plan", "argv", "carry"):
        assert entry[key] == payload[key]
    assert "facts_status" not in entry


def test_finish_defaults_missing_summary_keys_to_null(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.CycleTimings(path).finish({})
    (entry,) = _lines(path)
    assert all(
        entry[key] is None for key in ("exit", "interrupted", "failures", "gates", "plan", "argv", "carry")
    )


def test_append_warns_once_and_never_raises(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    timings = ct.CycleTimings(blocker / "j.ndjson")
    timings.record_step(_result(), [])
    timings.finish({})
    err = capsys.readouterr().err
    assert err.count("warning: failed to append") == 1


def test_the_format_stamp_names_the_check_keyed_shape():
    assert ct.FORMAT == "ams-cycle-timings/2"


def test_check_result_ok_is_green_and_nothing_else():
    assert _check_result(outcome="green").ok
    assert not _check_result(outcome="red").ok
    assert not _check_result(outcome="skipped").ok


def test_record_check_writes_one_parentless_check_line(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.record_check(_check_result(), path=path)
    (entry,) = _lines(path)
    assert entry == {
        "format": ct.FORMAT,
        "kind": "check",
        "check": "make-test",
        "outcome": "green",
        "status": "green",
        "failures": [],
        "failed_ids": [],
        "host": socket.gethostname(),
        "cpu_count": os.cpu_count(),
        "mem_total_bytes": memory_budget.total_memory_bytes(),
        "finished_at": entry["finished_at"],
    }
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["finished_at"])


def test_a_check_line_carries_its_own_machine_context(tmp_path):
    """A check line records its own host, cores, and memory, because an interactive check has no run line to say which machine it ran on."""
    path = tmp_path / "j.ndjson"
    ct.record_check(_check_result(), path=path)
    (entry,) = _lines(path)
    assert entry["host"] == socket.gethostname()
    assert entry["cpu_count"] == os.cpu_count()
    assert entry["mem_total_bytes"] == memory_budget.total_memory_bytes()


def test_record_check_carries_the_parent_and_the_cost_when_given_them(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.record_check(
        _check_result(
            check="rebuild-contracts",
            outcome="red",
            status="FAILED (2 unexplained)",
            failures=["rebuild suite: 2 unexplained failure(s)"],
            failed_ids=["rebuild/test_a.py::test_x", "rebuild/test_b.py::test_y"],
        ),
        run="abc123def456",
        argv=["uv", "run", "pytest", "rebuild/"],
        elapsed_s=112.349,
        peak_rss_bytes=5_560_000_000,
        path=path,
    )
    (entry,) = _lines(path)
    assert entry["check"] == "rebuild-contracts"
    assert entry["outcome"] == "red"
    assert entry["status"] == "FAILED (2 unexplained)"
    assert entry["failures"] == ["rebuild suite: 2 unexplained failure(s)"]
    assert entry["failed_ids"] == ["rebuild/test_a.py::test_x", "rebuild/test_b.py::test_y"]
    assert entry["run"] == "abc123def456"
    assert entry["argv"] == ["uv", "run", "pytest", "rebuild/"]
    assert entry["elapsed_s"] == 112.3
    assert entry["peak_rss_bytes"] == 5_560_000_000


def test_record_check_carries_each_workers_peak_footprint_beside_its_estimate(tmp_path):
    """A check that sizes its own pool records every worker's peak footprint and the need its width assumed, keyed by worker, so a real run can be held against the estimate."""
    path = tmp_path / "j.ndjson"
    ct.record_check(
        _check_result(check="conform-deep"),
        worker_peak_footprint_bytes={"default": 16_930_000_000, "ss10": 45_000_000},
        worker_estimate_bytes={"default": 26_600_000_000, "ss10": 200_000_000},
        path=path,
    )
    (entry,) = _lines(path)
    assert entry["worker_peak_footprint_bytes"] == {"default": 16_930_000_000, "ss10": 45_000_000}
    assert entry["worker_estimate_bytes"] == {"default": 26_600_000_000, "ss10": 200_000_000}


def test_record_check_carries_phase_lines_only_when_given_them(tmp_path):
    """run_m1's CLI passes its table build's phase lines, in the form `parse_inner_timings` returns, so a standalone build's check line can be paired with another build as a cycle's run_m1 step line can. A check given none carries no `inner`."""
    path = tmp_path / "j.ndjson"
    inner = ct.parse_inner_timings(
        "[t] enumerate[default] 78.1s\n[t] kernel_build_tables 220.2s memos_read=0 width=5"
    )
    ct.record_check(_check_result(check="run_m1"), inner=inner, path=path)
    ct.record_check(_check_result(check="run_m1"), path=path)
    with_phases, without = _lines(path)
    assert with_phases["inner"] == [
        {"label": "enumerate[default]", "elapsed_s": 78.1},
        {"label": "kernel_build_tables", "elapsed_s": 220.2, "memos_read": 0, "width": 5},
    ]
    assert "inner" not in without


def test_a_check_line_never_journals_recordable(tmp_path):
    """`recordable` only tells the current pass whether it may write a green record, so it is not journaled."""
    path = tmp_path / "j.ndjson"
    ct.record_check(_check_result(recordable=True), path=path)
    (entry,) = _lines(path)
    assert "recordable" not in entry


def test_record_check_resolves_the_journal_when_the_call_is_made(tmp_path, monkeypatch):
    """rebuild/conftest.py's autouse redirect patches `JOURNAL`, which works only because no default argument binds it at import."""
    journal = tmp_path / "redirected.ndjson"
    monkeypatch.setattr(ct, "JOURNAL", journal)
    ct.record_check(_check_result())
    assert [entry["check"] for entry in _lines(journal)] == ["make-test"]


def test_cycle_record_check_tags_the_run_and_writes_to_the_instance_journal(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    timings.record_check(_check_result(check="conform"), elapsed_s=3.04)
    (entry,) = _lines(path)
    assert entry["kind"] == "check"
    assert entry["check"] == "conform"
    assert entry["run"] == timings.run_id
    assert entry["elapsed_s"] == 3.0


def test_record_check_warns_once_when_the_journal_cannot_be_written(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ct, "_check_warn_state", [False])
    blocker = tmp_path / "notadir"
    blocker.write_text("")
    for _ in range(2):
        ct.record_check(_check_result(), path=blocker / "j.ndjson")
    err = capsys.readouterr().err
    assert err.count("warning: failed to append") == 1


def test_load_checks_returns_every_check_line_in_file_order(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    ct.record_check(_check_result(check="rebuild-contracts"), path=path)
    timings.record_step(_result(), [])
    timings.record_check(_check_result(check="run_m1"))
    ct.record_check(_check_result(check="make-test", outcome="skipped", status="skipped"), path=path)
    timings.finish({})
    checks = ct.load_checks(path)
    assert [check["check"] for check in checks] == ["rebuild-contracts", "run_m1", "make-test"]
    assert [check.get("run") for check in checks] == [None, timings.run_id, None]


def test_load_checks_skips_a_torn_line(tmp_path):
    path = tmp_path / "j.ndjson"
    torn = json.dumps({"kind": "check", "check": "torn", "failed_ids": ["a"]})[:24]
    path.write_text(
        "\n".join(
            [
                json.dumps({"kind": "check", "check": "first"}),
                torn,
                json.dumps({"kind": "check", "check": "second"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert [check["check"] for check in ct.load_checks(path)] == ["first", "second"]


def test_load_checks_reads_a_missing_journal_as_no_checks(tmp_path):
    assert ct.load_checks(tmp_path / "absent.ndjson") == []


def test_load_journal_ignores_check_lines_parented_or_not(tmp_path):
    """Check lines are not steps, and a check line that names a run does not create a run entry."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": "r1", "host": "h1", "name": "gate:make-test", "elapsed_s": 1.0},
            {"kind": "check", "run": "r1", "check": "make-test", "outcome": "green", "elapsed_s": 1.0},
            {"kind": "check", "check": "rebuild-contracts", "outcome": "red", "elapsed_s": 2.0},
            {"kind": "check", "run": "r9", "check": "conform", "outcome": "green"},
        ],
    )
    runs, steps, order = ct.load_journal(path)
    assert order == ["r1"]
    assert runs == {}
    assert [step["name"] for step in steps["r1"]] == ["gate:make-test"]


def _mixed_journal(path):
    """A journal with one cycle's step and run lines and two pool lines from pytest controllers unrelated to that cycle."""
    timings = ct.CycleTimings(path)
    ct.record_pool(
        "rebuild-contracts",
        width=8,
        worker_peaks={"gw0": 1_900_000_000},
        controller_peak_bytes=300_000_000,
        path=path,
    )
    timings.record_step(_result(), ["uv", "run", "fake"])
    ct.record_pool(
        "corpus",
        width=4,
        worker_peaks={"gw0": 5_560_000_000, "gw1": 4_980_000_000},
        controller_peak_bytes=412_000_000,
        path=path,
    )
    timings.finish({})
    return timings.run_id


def test_record_pool_writes_one_pool_line(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.record_pool(
        "corpus",
        width=3,
        worker_peaks={"gw10": 4_980_000_000, "gw2": 5_210_000_000, "gw0": 5_560_000_000},
        controller_peak_bytes=412_000_000,
        path=path,
    )
    (entry,) = _lines(path)
    assert entry["format"] == ct.FORMAT
    assert entry["kind"] == "pool"
    assert entry["unit"] == "corpus"
    assert entry["width"] == 3
    assert entry["controller_peak_rss_bytes"] == 412_000_000
    assert entry["worker_peak_rss_bytes"] == {
        "gw0": 5_560_000_000,
        "gw2": 5_210_000_000,
        "gw10": 4_980_000_000,
    }
    assert list(entry["worker_peak_rss_bytes"]) == ["gw0", "gw2", "gw10"]
    assert entry["host"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["finished_at"])


def test_a_pool_record_carries_no_run_id(tmp_path):
    """A pool belongs to one suite invocation and a cycle pass spawns several, so a pool line has no run id even when the child inherits `CYCLE_RUN_ENV`."""
    path = tmp_path / "j.ndjson"
    ct.record_pool("font-suite", width=2, worker_peaks={"gw0": 1}, controller_peak_bytes=1, path=path)
    (entry,) = _lines(path)
    assert "run" not in entry


def test_record_pool_round_trips_through_load_pool_records(tmp_path):
    path = tmp_path / "j.ndjson"
    _mixed_journal(path)
    records = ct.load_pool_records(path)
    assert [record["unit"] for record in records] == ["rebuild-contracts", "corpus"]
    assert records[0]["worker_peak_rss_bytes"] == {"gw0": 1_900_000_000}
    assert records[1]["worker_peak_rss_bytes"] == {"gw0": 5_560_000_000, "gw1": 4_980_000_000}
    assert (records[0]["width"], records[1]["width"]) == (8, 4)


def test_a_conform_sweep_record_keys_its_workers_by_configuration(tmp_path):
    """The conformance sweep's pool record keys its workers by acceptance configuration, and `gateway_order` sorts those keys by their digits: `default` has none and sorts first, and `ss03+ss05` reads as 305 and sorts last. Match a peak to its configuration by key, not by position."""
    path = tmp_path / "j.ndjson"
    configs = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10")
    peaks = {config: 300_000_000 + index for index, config in enumerate(configs)}
    ct.record_pool("conform-sweep", width=6, worker_peaks=peaks, controller_peak_bytes=280_000_000, path=path)
    (record,) = ct.load_pool_records(path)
    assert record["unit"] == "conform-sweep"
    assert record["width"] == 6
    assert record["controller_peak_rss_bytes"] == 280_000_000
    assert record["worker_peak_rss_bytes"] == peaks
    assert list(record["worker_peak_rss_bytes"]) == ["default", "ss03", "ss04", "ss05", "ss10", "ss03+ss05"]


def test_load_journal_never_sees_a_pool_record(tmp_path):
    path = tmp_path / "j.ndjson"
    run_id = _mixed_journal(path)
    runs, steps, order = ct.load_journal(path)
    assert order == [run_id]
    assert set(runs) == {run_id}
    assert [step["name"] for step in steps[run_id]] == ["run_m1"]


def test_load_pool_records_skips_a_torn_line(tmp_path):
    path = tmp_path / "j.ndjson"
    torn = json.dumps({"kind": "pool", "unit": "torn", "worker_peak_rss_bytes": {"gw0": 3}})[:24]
    path.write_text(
        "\n".join(
            [
                json.dumps({"kind": "pool", "unit": "first"}),
                torn,
                json.dumps({"kind": "pool", "unit": "second"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert [record["unit"] for record in ct.load_pool_records(path)] == ["first", "second"]


def test_load_pool_records_reads_a_missing_journal_as_no_records(tmp_path):
    assert ct.load_pool_records(tmp_path / "absent.ndjson") == []


def test_record_pool_warns_once_when_the_journal_cannot_be_written(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ct, "_pool_warn_state", [False])
    blocker = tmp_path / "notadir"
    blocker.write_text("")
    for _ in range(2):
        ct.record_pool(
            "font-suite",
            width=2,
            worker_peaks={"gw0": 1},
            controller_peak_bytes=1,
            path=blocker / "j.ndjson",
        )
    err = capsys.readouterr().err
    assert err.count("warning: failed to append") == 1


def test_load_journal_missing_file(tmp_path):
    assert ct.load_journal(tmp_path / "absent.ndjson") == ({}, {}, [])


def test_load_journal_tolerates_junk_and_orphan_steps(tmp_path):
    path = tmp_path / "j.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"kind": "step", "run": "r1", "name": "a", "elapsed_s": 1.0}),
                "",
                "{not json",
                json.dumps([1, 2, 3]),
                json.dumps({"kind": "step", "name": "no-run-key"}),
                json.dumps({"kind": "run", "run": "r2", "exit": "ok"}),
                json.dumps({"kind": "step", "run": "r1", "name": "b", "elapsed_s": 2.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runs, steps, order = ct.load_journal(path)
    assert order == ["r1", "r2"]
    assert set(runs) == {"r2"}
    assert [step["name"] for step in steps["r1"]] == ["a", "b"]
    assert steps["r2"] == []


def test_load_journal_reads_a_plumbing_step_as_the_verdict_update(tmp_path):
    """Older lines name the verdict-update step `plumbing`; `--by-step` counts them with the `verdict-update` lines as one step."""
    path = tmp_path / "j.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"kind": "step", "run": "r1", "name": "plumbing", "host": "h", "elapsed_s": 1.0}),
                json.dumps(
                    {"kind": "step", "run": "r2", "name": "verdict-update", "host": "h", "elapsed_s": 3.0}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _, steps, order = ct.load_journal(path)
    assert [step["name"] for run in order for step in steps[run]] == ["verdict-update", "verdict-update"]
    rows = [line for line in ct.render_by_step(steps, order, []) if line.startswith("verdict-update")]
    assert len(rows) == 1 and rows[0].split()[2] == "2"


def test_load_journal_reads_a_census_step_as_the_review_facts(tmp_path):
    """Older lines name the review-facts step `census`; `--by-step` counts them with the `review-facts` lines as one step."""
    path = tmp_path / "j.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"kind": "step", "run": "r1", "name": "census", "host": "h", "elapsed_s": 1.0}),
                json.dumps(
                    {"kind": "step", "run": "r2", "name": "review-facts", "host": "h", "elapsed_s": 3.0}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _, steps, order = ct.load_journal(path)
    assert [step["name"] for run in order for step in steps[run]] == ["review-facts", "review-facts"]


def test_load_journal_reads_the_echo_steps_as_the_duplicate_steps(tmp_path):
    """Older lines name the duplicate fill and merge steps `echo-fill` and `echo-merge`; `--by-step` counts them with the `duplicate-fill` and `duplicate-merge` lines."""
    path = tmp_path / "j.ndjson"
    path.write_text(
        "\n".join(
            json.dumps({"kind": "step", "run": run, "name": name, "host": "h", "elapsed_s": 1.0})
            for run, name in (
                ("r1", "echo-fill"),
                ("r1", "echo-merge"),
                ("r2", "duplicate-fill"),
                ("r2", "duplicate-merge"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _, steps, order = ct.load_journal(path)
    assert [step["name"] for run in order for step in steps[run]] == ["duplicate-fill", "duplicate-merge"] * 2


def test_main_reports_a_missing_journal(tmp_path, capsys):
    assert ct.main(["--journal", str(tmp_path / "absent.ndjson")]) == 0
    out = capsys.readouterr().out
    assert "No timing journal at" in out
    assert "absent.ndjson" in out


def test_main_does_not_report_an_empty_journal_when_only_checks_are_recorded(tmp_path, capsys):
    """The empty-journal message is only for a journal with no records, and a journal with only check lines is not empty."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [{"kind": "check", "host": "h1", "check": "make-test", "outcome": "green", "elapsed_s": 90.0}],
    )
    assert ct.main(["--journal", str(path), "--by-outcome"]) == 0
    out = capsys.readouterr().out
    assert "No timing journal" not in out
    assert "make-test" in out


def _view_journal(tmp_path):
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": "r1", "host": "h1", "name": "fast", "rc": 1, "elapsed_s": 1.0},
            {
                "kind": "step",
                "run": "r1",
                "host": "h1",
                "name": "slow",
                "rc": 0,
                "elapsed_s": 9.0,
                "peak_rss_bytes": 8_940_000_000,
                "inner": [{"label": "phase-a", "elapsed_s": 3.5, "rss_gb": 8.12}],
            },
            {
                "kind": "run",
                "run": "r1",
                "host": "h1",
                "cpu_count": 8,
                "mem_total_bytes": 51_539_607_552,
                "started_at": "2026-01-01T00:00:00Z",
                "wall_s": 10.5,
                "exit": "ok",
                "plan": {"short_id": "abc"},
            },
        ],
    )
    return path


def test_main_default_view_lists_steps_slowest_first(tmp_path, capsys):
    path = _view_journal(tmp_path)
    assert ct.main(["--journal", str(path)]) == 0
    out = capsys.readouterr().out
    assert "1 runs recorded" in out
    assert "host=h1" in out
    assert "cpus=8" in out
    assert "ram=51.54GB" in out
    assert "wall=10.5s" in out
    assert "exit=ok" in out
    assert out.index("slow") < out.index("fast")
    assert "(rc 1)" in out
    assert "rss=8.94GB" in out
    assert "phase-a" not in out


def test_main_default_view_omits_ram_for_a_run_record_that_predates_it(tmp_path, capsys):
    """A run record without `mem_total_bytes` predates that field, so it shows no `ram=` clause. A missing `cpu_count` shows `cpus=?`, because that key has always been written."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {
                "kind": "run",
                "run": "r1",
                "host": "h1",
                "cpu_count": 8,
                "started_at": "2026-01-01T00:00:00Z",
                "wall_s": 10.5,
                "exit": "ok",
            }
        ],
    )
    assert ct.main(["--journal", str(path)]) == 0
    out = capsys.readouterr().out
    assert "cpus=8" in out
    assert "ram=" not in out


def test_main_inner_flag_expands_phase_lines(tmp_path, capsys):
    path = _view_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--inner"]) == 0
    out = capsys.readouterr().out
    assert "phase-a" in out
    assert "3.5s" in out
    assert "rss=8.12GB" in out


def test_main_default_view_flags_a_run_with_no_run_record(tmp_path, capsys):
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {
                "kind": "step",
                "run": "r1",
                "host": "h1",
                "name": "run_m1",
                "rc": 0,
                "elapsed_s": 5.0,
                "finished_at": "2026-01-01T00:05:00Z",
            }
        ],
    )
    assert ct.main(["--journal", str(path)]) == 0
    out = capsys.readouterr().out
    assert "no run record" in out
    assert "host=h1" in out
    assert "2026-01-01T00:05:00Z" in out


def _build_phases(commit, format2, headroom):
    return [
        {"label": "kernel_build_tables", "elapsed_s": 220.2, "runes": 44},
        {
            "label": "readback",
            "elapsed_s": 41.2,
            "commit": commit,
            "settle_format2": format2,
            "settle_format3": 927,
            "subtable_offset_headroom": headroom,
            "gsub_lookups": 549,
            "settle_rules": 29_214,
            "largest_group_rule_bytes": 33_542,
        },
    ]


def test_main_by_commit_lists_each_builds_figures_under_its_commit(tmp_path, capsys):
    """The view reads the cycle's run_m1 step lines and the run_m1 check lines recorded outside a cycle, oldest first by finish time. A rebuild that reproduces a row's figures adds to its count, and builds under one commit that disagree get a row each, since the tree can hold edits HEAD does not. A cycle's own check line and a read-back line without figures are not readings."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {
                "kind": "step",
                "run": "r1",
                "name": "run_m1",
                "finished_at": "2026-10-04T08:00:00Z",
                "inner": _build_phases("779dcad4", 2_100, 29_000),
            },
            {
                "kind": "check",
                "check": "run_m1",
                "run": "r1",
                "finished_at": "2026-10-04T08:00:01Z",
                "inner": _build_phases("779dcad4", 9_999, 1),
            },
            {
                "kind": "check",
                "check": "run_m1",
                "finished_at": "2026-10-05T09:00:00Z",
                "inner": _build_phases("95521363", 2_298, 27_997),
            },
            {
                "kind": "step",
                "run": "r2",
                "name": "run_m1",
                "finished_at": "2026-10-05T10:00:00Z",
                "inner": _build_phases("95521363", 2_298, 27_997),
            },
            {
                "kind": "step",
                "run": "r3",
                "name": "run_m1",
                "finished_at": "2026-10-04T09:00:00Z",
                "inner": _build_phases("779dcad4", 2_200, 28_500),
            },
            {
                "kind": "step",
                "run": "r4",
                "name": "run_m1",
                "finished_at": "2026-10-05T11:00:00Z",
                "inner": [{"label": "readback", "elapsed_s": 40.0}],
            },
        ],
    )
    assert ct.main(["--journal", str(path), "--by-commit"]) == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines() if re.match(r"^[0-9a-f]{8} ", line)]
    assert rows == [
        [
            "779dcad4",
            "44",
            "3,027",
            "2,100",
            "927",
            "29,000",
            "549",
            "29,214",
            "33,542",
            "1",
            "2026-10-04T08:00:00Z",
        ],
        [
            "779dcad4",
            "44",
            "3,127",
            "2,200",
            "927",
            "28,500",
            "549",
            "29,214",
            "33,542",
            "1",
            "2026-10-04T09:00:00Z",
        ],
        [
            "95521363",
            "44",
            "3,225",
            "2,298",
            "927",
            "27,997",
            "549",
            "29,214",
            "33,542",
            "2",
            "2026-10-05T09:00:00Z",
        ],
    ]


def test_main_by_step_aggregates_median_max_latest(tmp_path, capsys):
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": f"r{i}", "host": "h1", "name": "gate:conform", "rc": 0, "elapsed_s": s}
            for i, s in enumerate([1.0, 2.0, 8.0, 3.0], start=1)
        ],
    )
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"step\s+host\s+runs\s+median\s+max\s+latest\s+maxrss", out)
    assert re.search(r"gate:conform\s+h1\s+4\s+2\.5s\s+8\.0s\s+3\.0s", out)


def test_main_by_step_reports_the_max_recorded_rss_per_step(tmp_path, capsys):
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": "r1", "host": "h1", "name": "run_m1", "rc": 0, "elapsed_s": 1.0},
            {
                "kind": "step",
                "run": "r2",
                "host": "h1",
                "name": "run_m1",
                "rc": 0,
                "elapsed_s": 2.0,
                "peak_rss_bytes": 8_940_000_000,
            },
            {
                "kind": "step",
                "run": "r3",
                "host": "h1",
                "name": "run_m1",
                "rc": 0,
                "elapsed_s": 3.0,
                "peak_rss_bytes": 2_000_000_000,
            },
            {"kind": "step", "run": "r1", "host": "h1", "name": "merge", "rc": 0, "elapsed_s": 0.5},
        ],
    )
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"run_m1\s+h1\s+3\s+2\.0s\s+3\.0s\s+3\.0s\s+8\.94GB", out)
    assert re.search(r"merge\s+h1\s+1\s+0\.5s\s+0\.5s\s+0\.5s\s*$", out, re.MULTILINE)


def _check_timing_journal(tmp_path):
    """One cycle's gate:make-test step with its check line, plus three interactive runs of the same suite: two with an elapsed time and one skipped."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {
                "kind": "step",
                "run": "r1",
                "host": "h1",
                "name": "gate:make-test",
                "rc": 0,
                "elapsed_s": 200.0,
            },
            {
                "kind": "check",
                "run": "r1",
                "host": "h1",
                "check": "make-test",
                "outcome": "green",
                "elapsed_s": 200.0,
            },
            {"kind": "check", "host": "h1", "check": "make-test", "outcome": "green", "elapsed_s": 100.0},
            {"kind": "check", "host": "h1", "check": "make-test", "outcome": "green", "elapsed_s": 120.0},
            {"kind": "check", "host": "h1", "check": "make-test", "outcome": "skipped"},
        ],
    )
    return path


def test_main_by_step_gives_an_interactive_check_a_row_of_its_own(tmp_path, capsys):
    """A gate that shares the machine with a cycle pass and the same suite run alone are different measurements, so the gate:* row and the check's row are kept separate."""
    path = _check_timing_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^gate:make-test\s+h1\s+1\s+200\.0s\s+200\.0s\s+200\.0s", out, re.MULTILINE)
    assert re.search(r"^check:make-test\s+h1\s+2\s+110\.0s\s+120\.0s\s+120\.0s", out, re.MULTILINE)


def test_main_by_step_counts_neither_a_parented_check_nor_a_skipped_one(tmp_path, capsys):
    """The parented check's seconds are already in its step line. The skipped check has none, and counting it as zero would pull the median down."""
    path = _check_timing_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    row = re.search(r"^check:make-test\s+h1\s+(\d+)\s", out, re.MULTILINE)
    assert row is not None and row.group(1) == "2"


def test_main_by_step_keeps_the_run_m1_check_out_of_the_run_m1_build_row(tmp_path, capsys):
    """`run_m1` is both a step name and a check name, and an interactive `--gates-only` run records the check with no step. Merging the rows would inflate the build row's count and report the `--gates-only` run as the latest full build's cost."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": "r1", "host": "h1", "name": "run_m1", "rc": 0, "elapsed_s": 600.0},
            {"kind": "step", "run": "r2", "host": "h1", "name": "run_m1", "rc": 0, "elapsed_s": 620.0},
            {"kind": "check", "host": "h1", "check": "run_m1", "outcome": "green", "elapsed_s": 9.0},
        ],
    )
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^run_m1\s+h1\s+2\s+610\.0s\s+620\.0s\s+620\.0s", out, re.MULTILINE)
    assert re.search(r"^check:run_m1\s+h1\s+1\s+9\.0s\s+9\.0s\s+9\.0s", out, re.MULTILINE)


def _outcome_journal(tmp_path):
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "check", "host": "h1", "check": "make-test", "outcome": "green", "failed_ids": []},
            {
                "kind": "check",
                "host": "h1",
                "check": "make-test",
                "outcome": "red",
                "status": "FAILED (rc 1)",
                "failed_ids": ["test/test_a.py::test_x", "test/test_b.py::test_y"],
            },
            {
                "kind": "check",
                "run": "r1",
                "host": "h2",
                "check": "make-test",
                "outcome": "red",
                "failed_ids": ["test/test_b.py::test_y"],
            },
            {"kind": "check", "host": "h1", "check": "make-test", "outcome": "skipped"},
            {"kind": "check", "run": "r1", "host": "h2", "check": "conform", "outcome": "green"},
            {"kind": "step", "run": "r1", "host": "h2", "name": "gate:conform", "rc": 0, "elapsed_s": 60.0},
        ],
    )
    return path


def test_main_by_outcome_counts_every_invocation_across_hosts_and_parents(tmp_path, capsys):
    path = _outcome_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-outcome"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^check\s+runs\s+green\s+red\s+skipped$", out, re.MULTILINE)
    assert re.search(r"^conform\s+1\s+1\s+0\s+0$", out, re.MULTILINE)
    assert re.search(r"^make-test\s+4\s+1\s+2\s+1$", out, re.MULTILINE)
    assert out.index("conform") < out.index("make-test")


def test_main_by_outcome_ranks_the_failed_ids_under_their_check(tmp_path, capsys):
    path = _outcome_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-outcome"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^\s+2\s+test/test_b\.py::test_y$", out, re.MULTILINE)
    assert re.search(r"^\s+1\s+test/test_a\.py::test_x$", out, re.MULTILINE)
    assert out.index("test/test_b.py::test_y") < out.index("test/test_a.py::test_x")


def test_main_by_outcome_reads_check_lines_only(tmp_path, capsys):
    path = _outcome_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-outcome"]) == 0
    out = capsys.readouterr().out
    assert "gate:conform" not in out


def test_main_by_outcome_counts_a_check_line_that_carries_the_older_verdict_key(tmp_path, capsys):
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "check", "host": "h1", "check": "make-test", "verdict": "red"},
            {"kind": "check", "host": "h1", "check": "make-test", "outcome": "green"},
        ],
    )
    assert ct.main(["--journal", str(path), "--by-outcome"]) == 0
    assert re.search(r"^make-test\s+2\s+1\s+1\s+0$", capsys.readouterr().out, re.MULTILINE)


def test_critical_path_names_the_step_that_ended_each_pass_and_groups_passes_by_policy_and_shape(
    tmp_path, capsys
):
    """The last step and its margin come from the step lines alone, leaving out the job-costs step that runs once every lane has joined. The margin runs to the other lane's last step, so a queued gate is measured against the build lane rather than against gate:make-test before it, and a build-lane step against the gates rather than against the build step before it. Passes group by host, pool policy and pass shape, and a run whose plan block predates `make_test_workers` reads as unrecorded."""

    def stamp(seconds):
        return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def run(run_id, policy, wall, workers, spans):
        plan = (
            {"pool_policy": policy}
            if workers == "absent"
            else {"pool_policy": policy, "make_test_workers": workers}
        )
        steps = [
            {
                "kind": "step",
                "run": run_id,
                "host": "h1",
                "name": name,
                "elapsed_s": end - start,
                "finished_at": stamp(end),
            }
            for name, start, end in [*spans, ("job-costs", wall - 1, wall)]
        ]
        return [
            *steps,
            {
                "kind": "run",
                "run": run_id,
                "host": "h1",
                "started_at": stamp(0),
                "wall_s": wall,
                "plan": plan,
            },
        ]

    full = [("run_m1", 0, 240), ("corpus-build", 240, 530)]
    entries = [
        *run("a", "overlap", 701, 9, [*full, ("gate:make-test", 0, 700)]),
        *run("b", "queue", 721, 2, [*full, ("gate:make-test", 0, 690), ("gate:rebuild-contracts", 690, 720)]),
        *run("c", "queue", 801, 4, [*full, ("gate:make-test", 0, 800)]),
        *run(
            "d",
            "queue",
            151,
            "absent",
            [("corpus-build", 0, 120), ("verdict-update", 120, 150), ("gate:js", 0, 40)],
        ),
    ]
    path = tmp_path / "j.ndjson"
    _write_journal(path, entries)
    runs, steps, _ = ct.load_journal(path)
    assert ct.critical_path(runs["a"], steps["a"]) == {
        "last": "gate:make-test",
        "last_end_s": 700.0,
        "runner_up": "corpus-build",
        "runner_up_end_s": 530.0,
        "margin_s": 170.0,
        "shape": ("run_m1", "corpus-build", "gate:make-test"),
    }
    assert ct.main(["--journal", str(path), "--critical-path"]) == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines() if line.startswith("h1")]
    assert rows == [
        [
            "h1",
            "overlap",
            "run_m1+corpus-build+gate:make-test",
            "1",
            "170.0s",
            "701.0s",
            "9",
            "gate:make-test",
            "1",
        ],
        ["h1", "queue", "corpus-build", "1", "110.0s", "151.0s", "unrecorded", "verdict-update", "1"],
        [
            "h1",
            "queue",
            "run_m1+corpus-build+gate:make-test",
            "2",
            "230.0s",
            "761.0s",
            "3",
            "gate:make-test",
            "1,",
            "gate:rebuild-contracts",
            "1",
        ],
    ]


def test_parse_inner_timings_finds_every_label_when_two_branches_interleave():
    """run_m1's table-only branch and its glyph chain print to one step log at the same time. Each producer writes whole lines in one write, so every label is found however the lines interleave."""
    text = "\n".join(
        [
            "[t] build_tables_total 247.9s rss_gb=16.13",
            "[phase] replay_strings",
            "[phase] glyph_minting",
            "[t] glyph_minting 0.0s",
            "[progress] 1/5 packed configurations",
            "[phase] compile_font",
            "[t] pack_windows[ss04] 7.1s",
            "[t] compile_font 10.3s",
            "[t] emitted_order[ss04] 8.6s rows=7623532 checked_per_member=182",
            "[t] readback 1.0s",
            "[t] replay_strings 26.1s rss_gb=16.13",
            "replay_strings: maximum length 4, every text",
            "[t] run_total 260.0s rss_gb=16.13",
            "[t] rule_witnesses 18.2s",
            "[t] settle_memo_wait 31.4s",
            "[t] emitted_order 17.1s",
            "[t] pack_windows_total 22.7s",
            "[t] run_oracle 86.5s",
        ]
    )
    assert [item["label"] for item in ct.parse_inner_timings(text)] == [
        "build_tables_total",
        "glyph_minting",
        "pack_windows[ss04]",
        "compile_font",
        "emitted_order[ss04]",
        "readback",
        "replay_strings",
        "run_total",
        "rule_witnesses",
        "settle_memo_wait",
        "emitted_order",
        "pack_windows_total",
        "run_oracle",
    ]
