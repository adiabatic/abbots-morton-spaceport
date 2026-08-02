import json
import os
import re
from types import SimpleNamespace

from rebuild.tools import cycle_timings as ct


def _result(name="run_m1", rc=0, stdout="", stderr="", elapsed=1.0):
    return SimpleNamespace(name=name, returncode=rc, stdout=stdout, stderr=stderr, elapsed=elapsed)


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
        "plan": {"short_id": "abc", "deferred": []},
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
                "inner": [{"label": "phase-a", "elapsed_s": 3.5}],
            },
            {
                "kind": "run",
                "run": "r1",
                "host": "h1",
                "cpu_count": 8,
                "started_at": "2026-01-01T00:00:00Z",
                "wall_s": 10.5,
                "exit": "ok",
                "plan": {"deferred": ["conform"]},
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
    assert "wall=10.5s" in out
    assert "exit=ok" in out
    assert "deferred=conform" in out
    assert out.index("slow") < out.index("fast")
    assert "(rc 1)" in out
    assert "phase-a" not in out


def test_main_inner_flag_expands_phase_lines(tmp_path, capsys):
    path = _view_journal(tmp_path)
    assert ct.main(["--journal", str(path), "--inner"]) == 0
    out = capsys.readouterr().out
    assert "phase-a" in out
    assert "3.5s" in out


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
    assert re.search(r"step\s+host\s+runs\s+median\s+max\s+latest", out)
    assert re.search(r"gate:conform\s+h1\s+4\s+2\.5s\s+8\.0s\s+3\.0s", out)
