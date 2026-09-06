import json
import os
import re
import socket
from types import SimpleNamespace

from rebuild.tools import cycle_timings as ct
from rebuild.tools import memory_budget


def _result(name="run_m1", rc=0, stdout="", stderr="", elapsed=1.0):
    return SimpleNamespace(name=name, returncode=rc, stdout=stdout, stderr=stderr, elapsed=elapsed)


def _verdict(
    check="make-test",
    verdict="green",
    status="green",
    failures=None,
    failed_ids=None,
    recordable=False,
):
    return ct.CheckVerdict(
        check=check,
        verdict=verdict,
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


def test_parse_inner_timings_reads_the_surface_builds_phase_lines():
    """The surface build's phase lines carry the token ahead of a tab-separated note, which is the shape `--inner` has to read a per-phase peak out of for the step whose high-water mark issue #156 wants attributed."""
    text = (
        "[t] review.build load 12.3s rss_gb=1.23\t(signatures: 40 cached, 2 shaped)\n"
        "[t] review.build units 900.0s rss_gb=15.40\t(jobs=1, fresh=1,000,000, verified=0 served)\n"
        "[t] review.build manifest+check 300.5s rss_gb=17.10\n"
    )
    assert ct.parse_inner_timings(text) == [
        {"label": "review.build load", "elapsed_s": 12.3, "rss_gb": 1.23},
        {"label": "review.build units", "elapsed_s": 900.0, "rss_gb": 15.4},
        {"label": "review.build manifest+check", "elapsed_s": 300.5, "rss_gb": 17.1},
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
        "census_status": "clean",
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
    for key in ("exit", "interrupted", "failures", "gates", "plan", "argv"):
        assert entry[key] == payload[key]
    assert "census_status" not in entry


def test_finish_defaults_missing_summary_keys_to_null(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.CycleTimings(path).finish({})
    (entry,) = _lines(path)
    assert all(entry[key] is None for key in ("exit", "interrupted", "failures", "gates", "plan", "argv"))


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


def test_check_verdict_ok_is_green_and_nothing_else():
    assert _verdict(verdict="green").ok
    assert not _verdict(verdict="red").ok
    assert not _verdict(verdict="skipped").ok


def test_record_check_writes_one_parentless_check_line(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.record_check(_verdict(), path=path)
    (entry,) = _lines(path)
    assert entry == {
        "format": ct.FORMAT,
        "kind": "check",
        "check": "make-test",
        "verdict": "green",
        "status": "green",
        "failures": [],
        "failed_ids": [],
        "host": socket.gethostname(),
        "cpu_count": os.cpu_count(),
        "mem_total_bytes": memory_budget.total_memory_bytes(),
        "finished_at": entry["finished_at"],
    }
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["finished_at"])


def test_a_check_line_carries_its_own_box_context(tmp_path):
    """The denormalization that makes an interactive record readable: no run line is coming to say which machine this was."""
    path = tmp_path / "j.ndjson"
    ct.record_check(_verdict(), path=path)
    (entry,) = _lines(path)
    assert entry["host"] == socket.gethostname()
    assert entry["cpu_count"] == os.cpu_count()
    assert entry["mem_total_bytes"] == memory_budget.total_memory_bytes()


def test_record_check_carries_the_parent_and_the_cost_when_given_them(tmp_path):
    path = tmp_path / "j.ndjson"
    ct.record_check(
        _verdict(
            check="rebuild-contracts",
            verdict="red",
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
    assert entry["verdict"] == "red"
    assert entry["status"] == "FAILED (2 unexplained)"
    assert entry["failures"] == ["rebuild suite: 2 unexplained failure(s)"]
    assert entry["failed_ids"] == ["rebuild/test_a.py::test_x", "rebuild/test_b.py::test_y"]
    assert entry["run"] == "abc123def456"
    assert entry["argv"] == ["uv", "run", "pytest", "rebuild/"]
    assert entry["elapsed_s"] == 112.3
    assert entry["peak_rss_bytes"] == 5_560_000_000


def test_a_check_line_never_journals_recordable(tmp_path):
    """recordable is this pass's permission to write a green record, not history — a reader months later could do nothing with it."""
    path = tmp_path / "j.ndjson"
    ct.record_check(_verdict(recordable=True), path=path)
    (entry,) = _lines(path)
    assert "recordable" not in entry


def test_record_check_resolves_the_journal_when_the_call_is_made(tmp_path, monkeypatch):
    """What rebuild/conftest.py's autouse redirect depends on: no default binds JOURNAL at import, so pointing the constant somewhere disposable reaches this writer too."""
    journal = tmp_path / "redirected.ndjson"
    monkeypatch.setattr(ct, "JOURNAL", journal)
    ct.record_check(_verdict())
    assert [entry["check"] for entry in _lines(journal)] == ["make-test"]


def test_cycle_record_check_tags_the_run_and_writes_to_the_instance_journal(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    timings.record_check(_verdict(check="conform"), elapsed_s=3.04)
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
        ct.record_check(_verdict(), path=blocker / "j.ndjson")
    err = capsys.readouterr().err
    assert err.count("warning: failed to append") == 1


def test_load_checks_returns_every_check_line_in_file_order(tmp_path):
    path = tmp_path / "j.ndjson"
    timings = ct.CycleTimings(path)
    ct.record_check(_verdict(check="rebuild-contracts"), path=path)
    timings.record_step(_result(), [])
    timings.record_check(_verdict(check="run_m1"))
    ct.record_check(_verdict(check="make-test", verdict="skipped", status="skipped"), path=path)
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
    """A check is a judgment, not a step, and a line that only points at a run must not conjure one."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": "r1", "host": "h1", "name": "gate:make-test", "elapsed_s": 1.0},
            {"kind": "check", "run": "r1", "check": "make-test", "verdict": "green", "elapsed_s": 1.0},
            {"kind": "check", "check": "rebuild-contracts", "verdict": "red", "elapsed_s": 2.0},
            {"kind": "check", "run": "r9", "check": "conform", "verdict": "green"},
        ],
    )
    runs, steps, order = ct.load_journal(path)
    assert order == ["r1"]
    assert runs == {}
    assert [step["name"] for step in steps["r1"]] == ["gate:make-test"]


def _mixed_journal(path):
    """A journal holding both kinds of writer: one cycle's step and run lines, and two pool lines from pytest controllers that know nothing about that cycle."""
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
        "surface",
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
        "surface",
        width=3,
        worker_peaks={"gw10": 4_980_000_000, "gw2": 5_210_000_000, "gw0": 5_560_000_000},
        controller_peak_bytes=412_000_000,
        path=path,
    )
    (entry,) = _lines(path)
    assert entry["format"] == ct.FORMAT
    assert entry["kind"] == "pool"
    assert entry["unit"] == "surface"
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
    """The deliberate omission, which the run id a spawned child now inherits does not change: a pool belongs to one suite invocation, and a cycle pass spawns several, so a run is the wrong grain to file one under."""
    path = tmp_path / "j.ndjson"
    ct.record_pool("font-suite", width=2, worker_peaks={"gw0": 1}, controller_peak_bytes=1, path=path)
    (entry,) = _lines(path)
    assert "run" not in entry


def test_record_pool_round_trips_through_load_pool_records(tmp_path):
    path = tmp_path / "j.ndjson"
    _mixed_journal(path)
    records = ct.load_pool_records(path)
    assert [record["unit"] for record in records] == ["rebuild-contracts", "surface"]
    assert records[0]["worker_peak_rss_bytes"] == {"gw0": 1_900_000_000}
    assert records[1]["worker_peak_rss_bytes"] == {"gw0": 5_560_000_000, "gw1": 4_980_000_000}
    assert (records[0]["width"], records[1]["width"]) == (8, 4)


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


def test_main_reports_a_missing_journal(tmp_path, capsys):
    assert ct.main(["--journal", str(tmp_path / "absent.ndjson")]) == 0
    out = capsys.readouterr().out
    assert "No timing journal at" in out
    assert "absent.ndjson" in out


def test_main_does_not_report_an_empty_journal_when_only_checks_are_recorded(tmp_path, capsys):
    """The early-out is about a box that has recorded nothing, and a box that only ever runs checks interactively has recorded plenty."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [{"kind": "check", "host": "h1", "check": "make-test", "verdict": "green", "elapsed_s": 90.0}],
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
    """A record written before the box became a field is older, not a record whose probe failed — so it renders with no ram= clause at all, where cpus=? would be the honest reading for a key that has always been written."""
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
    """One cycle's gate:make-test step with the check line that judged it, plus the same suite run interactively three times — twice with a wall to report, once skipped."""
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
                "verdict": "green",
                "elapsed_s": 200.0,
            },
            {"kind": "check", "host": "h1", "check": "make-test", "verdict": "green", "elapsed_s": 100.0},
            {"kind": "check", "host": "h1", "check": "make-test", "verdict": "green", "elapsed_s": 120.0},
            {"kind": "check", "host": "h1", "check": "make-test", "verdict": "skipped"},
        ],
    )
    return path


def test_main_by_step_gives_an_interactive_check_a_row_of_its_own(tmp_path, capsys):
    """A gate sharing the box with a cycle pass and the same suite alone on the box are different measurements, so the gate:* row and the check's row stay apart."""
    path = _check_timing_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^gate:make-test\s+h1\s+1\s+200\.0s\s+200\.0s\s+200\.0s", out, re.MULTILINE)
    assert re.search(r"^check:make-test\s+h1\s+2\s+110\.0s\s+120\.0s\s+120\.0s", out, re.MULTILINE)


def test_main_by_step_counts_neither_a_parented_check_nor_a_skipped_one(tmp_path, capsys):
    """The parented one's seconds are already the step line's; the skipped one has none, and a zero there would drag the median toward a run that never happened."""
    path = _check_timing_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--by-step"]) == 0
    out = capsys.readouterr().out
    row = re.search(r"^check:make-test\s+h1\s+(\d+)\s", out, re.MULTILINE)
    assert row is not None and row.group(1) == "2"


def test_main_by_step_keeps_the_run_m1_check_out_of_the_run_m1_build_row(tmp_path, capsys):
    """The one name a check and a step spell identically: the cycle spawns the M1 build as the step `run_m1` and its judge files the check `run_m1`, and an interactive `--gates-only` re-adjudication files that same check with no step of its own. Merged, it would inflate the build row's count and hand `latest` the re-adjudication as the most recent cost of a full build."""
    path = tmp_path / "j.ndjson"
    _write_journal(
        path,
        [
            {"kind": "step", "run": "r1", "host": "h1", "name": "run_m1", "rc": 0, "elapsed_s": 600.0},
            {"kind": "step", "run": "r2", "host": "h1", "name": "run_m1", "rc": 0, "elapsed_s": 620.0},
            {"kind": "check", "host": "h1", "check": "run_m1", "verdict": "green", "elapsed_s": 9.0},
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
            {"kind": "check", "host": "h1", "check": "make-test", "verdict": "green", "failed_ids": []},
            {
                "kind": "check",
                "host": "h1",
                "check": "make-test",
                "verdict": "red",
                "status": "FAILED (rc 1)",
                "failed_ids": ["test/test_a.py::test_x", "test/test_b.py::test_y"],
            },
            {
                "kind": "check",
                "run": "r1",
                "host": "h2",
                "check": "make-test",
                "verdict": "red",
                "failed_ids": ["test/test_b.py::test_y"],
            },
            {"kind": "check", "host": "h1", "check": "make-test", "verdict": "skipped"},
            {"kind": "check", "run": "r1", "host": "h2", "check": "conform", "verdict": "green"},
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
