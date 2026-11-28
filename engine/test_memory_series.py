"""Tests for the memory series (`engine/tools/memory_series.py`). The table steps build the mini fixture narrowed to ·Tea, ·May and ·It, a build of a fraction of a second, so they run the real crate. The meter, the machine reading and the waits are replaced where a test reads their arithmetic."""

import io
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from engine.pipeline import conform, fixtures, kernel_exec, kernel_io, spec_load
from engine.review import store_lock
from engine.tools import memory_series as ms
from engine.tools import write_meter
from engine.tools.write_meter import Member, WriteReading

CONFIGS = list(conform.SETTLEMENT_CONFIGS)
FLAGS = ms.Flags(threads=2, scratch_beside_default=1)
STAMP = "memory-series-test"
QUIET = ms.Machine(swap_used=None, compressor=None, swapouts=None, device=None)


def _spec_path(tmp_path):
    path = tmp_path / "spec.json"
    kernel_io.write_spec(spec_load.narrowed(fixtures.mini_spec(), ("qsTea", "qsMay", "qsIt")), path)
    kernel_exec.ensure_built()
    return path


def _reading(at, device, logical=0):
    usage = dict.fromkeys(write_meter._COALITION_FIELDS, 0) | {
        "tasks_started": 1,
        "logical_deferred_writes": logical,
    }
    return WriteReading(
        pid=100,
        at=at,
        wall=1000.0 + at,
        coalition=7,
        usage=usage,
        members=(Member(100, 1, 990.0, 0),),
        check=None,
        named=None,
        device=device,
        swap=0,
    )


def _listing(directory):
    return {
        path.name: "fifo" if path.is_fifo() else os.readlink(path) if path.is_symlink() else "file"
        for path in directory.iterdir()
    }


def test_a_step_reads_through_pipes_what_a_build_on_files_writes_and_leaves_only_null_links(
    monkeypatch, tmp_path
):
    """A step's windows counts and digests are what the same build writes as files, counted and hashed after it exits, and each payload's byte count the crate answers is its file's size. The step's crate takes each payload's pipe as `--windows=<config>=/dev/fd/<number>`, inheriting its write end, and its directory holds the settlement and joins links to the null device and nothing else at the spawn and while the crate writes the payloads, so the build writes no file and no payload stands at a path. The directory is gone once the step returns."""
    spec_path = _spec_path(tmp_path)
    files = tmp_path / "files"
    arguments = ms.step_arguments(kernel_exec.BINARY, spec_path, files, CONFIGS, STAMP, FLAGS)
    done = subprocess.run(arguments, capture_output=True, check=True)
    answers = {answer["config"]: answer for answer in map(json.loads, done.stdout.splitlines())}
    digests = {config: answer["digest"] for config, answer in answers.items()}
    windows = {}
    for config in CONFIGS:
        payload = kernel_exec.windows_payload(files, config)
        assert answers[config]["windows_bytes"] == payload.stat().st_size
        with payload.open("rb") as source:
            scanned = ms.scan(source)
        windows[config] = list(ms.counts(scanned, payload, answers[config]["windows_bytes"]))

    step_dir = tmp_path / "step"
    seen = {}
    run_child, scan = ms.run_child, ms.scan

    def looking(arguments, **rest):
        seen["spawn"] = _listing(step_dir)
        seen["windows"] = [argument for argument in arguments if argument.startswith("--windows=")]
        seen["pass_fds"] = rest["payloads"].pass_fds
        return run_child(arguments, **rest)

    def scanning(source):
        source.peek(1)
        seen.setdefault("writing", _listing(step_dir))
        return scan(source)

    monkeypatch.setattr(ms, "run_child", looking)
    monkeypatch.setattr(ms, "scan", scanning)
    measured = ms.measure_step(
        kernel_exec.BINARY,
        spec_path,
        step_dir,
        CONFIGS,
        FLAGS,
        STAMP,
        idle_s=0,
        after_s=0,
        interval_s=0.01,
        meter=lambda: write_meter.read(device=False),
        machine=lambda: QUIET,
    )
    assert measured["windows"] == windows
    assert measured["digests"] == digests
    assert all(counts[0] > 0 for counts in windows.values())
    expected = {}
    for config in CONFIGS:
        expected |= {f"settlement-{config}.tsv": os.devnull, f"joins-{config}.tsv": os.devnull}
    assert seen["spawn"] == expected
    assert seen["writing"] == expected
    assert [flag.split("=")[1] for flag in seen["windows"]] == CONFIGS
    assert {int(flag.rpartition("/dev/fd/")[2]) for flag in seen["windows"]} == set(seen["pass_fds"])
    assert not step_dir.exists()


def test_a_step_whose_payload_lost_bytes_stops_the_run(monkeypatch, tmp_path):
    """A payload whose reader receives fewer bytes than the crate answers it wrote, here one row another reader took, stops the run naming both counts instead of recording a short count, and the step's directory is still removed."""
    scan = ms.scan

    def losing(source):
        head, columns, _taken, rest = source.readline(), source.readline(), source.readline(), source.read()
        return scan(io.BytesIO(head + columns + rest))

    monkeypatch.setattr(ms, "scan", losing)
    step_dir = tmp_path / "step"
    with pytest.raises(SystemExit, match=r"received \d+ bytes where the crate wrote \d+"):
        ms.measure_step(
            kernel_exec.BINARY,
            _spec_path(tmp_path),
            step_dir,
            CONFIGS,
            FLAGS,
            STAMP,
            idle_s=0,
            after_s=0,
            interval_s=0.01,
            meter=lambda: write_meter.read(device=False),
            machine=lambda: QUIET,
        )
    assert not step_dir.exists()


@pytest.mark.parametrize(
    "stdout",
    [
        b'{"config":"default","digest":"ab"}\n',
        b'{"config":"default","digest":"ab","windows_bytes":"12"}\n',
        b'{"config":"ss03","digest":"ab","windows_bytes":12}\n',
        b"not json\n",
    ],
    ids=["no byte count", "a byte count that is not a number", "another configuration", "not JSON"],
)
def test_an_answer_that_is_not_the_table_builds_stops_the_run(stdout):
    """Each `build-tables` answer line must be a `{config, digest, windows_bytes}` object with an integer byte count, for the configurations named and no others."""
    with pytest.raises(SystemExit, match="build-tables"):
        ms.table_answers(stdout, ["default"])
    good = b'{"config":"default","digest":"ab","windows_bytes":12}\n'
    assert ms.table_answers(good, ["default"]) == [{"config": "default", "digest": "ab", "windows_bytes": 12}]


def test_a_replay_runs_tables_are_written_plain_with_their_payloads_drained_from_pipes(tmp_path):
    """A replay run's unmeasured build writes each configuration's settlement and joins tables as plain files, which the walks read, and drains each windows payload from its pipe, so no payload file is left in the tables' directory."""
    out_dir = tmp_path / "tables"
    ms.build_replay_tables(_spec_path(tmp_path), out_dir, CONFIGS, FLAGS, STAMP, env=None)
    expected = {}
    for config in CONFIGS:
        expected |= {f"settlement-{config}.tsv": "file", f"joins-{config}.tsv": "file"}
    assert _listing(out_dir) == expected


def test_the_meter_reads_idle_then_before_the_spawn_the_end_and_after_the_wait_with_the_device_netted(
    tmp_path,
):
    """The meter is read once before the idle wait, once just before the step's child is spawned, once the build is done, and once more after the after-wait. Each block reads from the reading before the spawn, and its `device_net` is its device delta less the idle rate over that block's own seconds: 1 MB/s idle over 10 s, a 50 MB device delta over the step's 20 s and 80 MB over the 320 s to the later reading."""
    readings = iter(
        [
            _reading(0.0, 1_000_000_000),
            _reading(10.0, 1_010_000_000, logical=4096),
            _reading(30.0, 1_060_000_000, logical=4096),
            _reading(330.0, 1_090_000_000, logical=8192),
        ]
    )
    waits = []
    measured = ms.measure_step(
        kernel_exec.BINARY,
        _spec_path(tmp_path),
        tmp_path / "step",
        CONFIGS,
        FLAGS,
        STAMP,
        idle_s=10,
        after_s=300,
        interval_s=0.01,
        meter=lambda: next(readings),
        machine=lambda: QUIET,
        sleep=waits.append,
    )
    assert waits == [10, 300]
    written = measured["written"]
    assert written["idle_rate"] == 1_000_000
    assert written["end"] == {
        "basis": "own",
        "written": 0,
        "diskio": 0,
        "device": 50_000_000,
        "device_net": 30_000_000,
        "swap": 0,
        "swapouts": None,
        "seconds": 20.0,
    }
    assert written["after"]["written"] == 4096
    assert written["after"]["device_net"] == 80_000_000 - 320_000_000


def test_the_cache_lines_go_to_tables_resident_and_stats_by_configuration():
    """A length-and-capacity line is a table with its load, a `kb` line a resident size in bytes, and any other line its fields under a name that keeps its qualifiers, so two `shared_memo_hits` lines stay apart."""
    stderr = "\n".join(
        [
            "[t] spec_parse 0.0s",
            "[c] default trace_cache len=1767 cap=2688",
            "[c] default trace_rankings len=0 cap=0",
            "[c] default resident_before_release kb=5424",
            "[c] ss03 shared_memo_hits count=130",
            "[c] ss03 shared_memo_hits id=0 count=130",
            "[c] ss03 shared_verdicts hits=6 diverged=0 refused=210 misses=3",
        ]
    )
    assert ms.cache_lines(stderr) == {
        "tables": {"default": {"trace_cache": [1767, 2688, 0.657], "trace_rankings": [0, 0, None]}},
        "resident": {"default": {"resident_before_release": 5424 * 1024}},
        "stats": {
            "ss03": {
                "shared_memo_hits": {"count": 130},
                "shared_memo_hits id=0": {"count": 130},
                "shared_verdicts": {"hits": 6, "diverged": 0, "refused": 210, "misses": 3},
            }
        },
    }


def _replay_run(walks, absorbs, readings, commands, *, passed=True):
    """A replay child's run as `run_replay_child` returns it, with no machine to read."""
    child = ms.ChildRun(
        0, b"", "", 2.0, 1.0, 1, 1, QUIET, tuple(ms.Sample(at, 1, 1, QUIET) for at in (0.5, 1.0, 1.5)), QUIET
    )
    return ms.ReplayRun(
        child,
        tuple(ms.ReplayReading(at, reading) for at, reading in readings),
        commands,
        {"passed": passed, "walks": walks, "absorbs": absorbs},
    )


def _replay_walk(config, worker, lines=()):
    return {
        "counts": {"texts": 10, "windows": 20, "skipped": 0},
        "error": None,
        "lines": [f"[t] replay[{config}] 1.5s", *lines],
        "peaks": [300, 280, 2.5],
        "worker": worker,
    }


def _replay_absorb(rows):
    return {
        "resident": 120,
        "footprint": 110,
        "cpu": 0.75,
        "entries": rows,
        "seconds": 0.25,
        "warning": None,
        "digest": [4096, "ab" * 32],
    }


def test_a_replay_line_sums_each_walk_with_its_absorb_and_every_process_at_each_sample(tmp_path):
    """A replay line tells each walk's crate by the configuration its command names and each absorb worker by the pid its walk reports, and ignores every other process below the child. A configuration's pair is its two processes summed at each sample, the step's combined peak every crate and worker summed, and the line's peak the sample of the highest combined footprint. Each configuration keeps its walk's and its absorb's own peaks and CPU seconds, its counts, its file's size and digest, its `[t]` lines and, from its `[c]` lines, the unit count, its largest unit's tables and resident size after release, the side memo and the row store. A replay that fails stops the run."""
    binary = f"/checkout/{ms.BINARY_NAME}"
    commands = {
        11: "python -c from multiprocessing.spawn import spawn_main",
        12: "python -c from multiprocessing.spawn import spawn_main",
        21: f"{binary} replay-strings spec.json out --configs=default --max-length=4",
        22: f"{binary} replay-strings spec.json out --configs=ss03 --max-length=4",
        30: "python -c from multiprocessing.resource_tracker import main",
    }
    readings = [
        (0.5, {11: (50, 40), 12: (50, 40), 21: (100, 90), 30: (999, 999)}),
        (1.0, {11: (60, 50), 12: (70, 60), 21: (400, 300), 22: (200, 150)}),
        (1.5, {11: (90, 80), 12: (80, 70), 22: (100, 100)}),
    ]
    cached = [
        "[c] default unit=1 walk_memo len=5 cap=8",
        "[c] default unit=1 trace_cache len=40 cap=64",
        "[c] default unit=1 resident_after_release kb=2",
        "[c] default unit=2 walk_memo len=7 cap=8",
        "[c] default unit=2 trace_cache len=50 cap=64",
        "[c] default unit=2 resident_after_release kb=3",
        "[c] default side_memo len=3 cap=4",
        "[c] default units count=2",
        "[c] default row_store rows=15 bytes=4194304 chunks=1",
        "[c] default resident_after_walk kb=4",
    ]
    walks = {"default": _replay_walk("default", 11, cached), "ss03": _replay_walk("ss03", 12)}
    absorbs = {"default": _replay_absorb(20), "ss03": _replay_absorb(20)}
    meters = iter(_reading(at, 1000 * at) for at in (0.0, 1.0, 5.0))
    measured = ms.measure_replay_line(
        ["child"],
        ["default", "ss03"],
        idle_s=0,
        after_s=0,
        meter=lambda: next(meters),
        machine=lambda: QUIET,
        sleep=lambda seconds: None,
        run=lambda arguments, **rest: _replay_run(walks, absorbs, readings, commands),
    )
    default, ss03 = measured["configs_measured"]["default"], measured["configs_measured"]["ss03"]
    assert default["pair"] == {"resident": 460, "footprint": 350} and ss03["pair"] == {
        "resident": 270,
        "footprint": 210,
    }
    assert measured["combined"] == {"resident": 730, "footprint": 560}
    assert (measured["peak"]["at_s"], measured["peak"]["resident"], measured["peak"]["footprint"]) == (
        1.0,
        730,
        560,
    )
    assert (measured["end"]["resident"], measured["end"]["footprint"]) == (270, 250)
    assert default["walk"] == {"peak_resident": 300, "peak_footprint": 280, "cpu_s": 2.5}
    assert default["absorb"] == {
        "peak_resident": 120,
        "peak_footprint": 110,
        "cpu_s": 0.75,
        "seconds": 0.25,
        "rows": 20,
        "bytes": 4096,
        "sha256": "ab" * 32,
        "warning": None,
    }
    assert default["counts"] == {"texts": 10, "windows": 20, "skipped": 0}
    assert default["phases"] == {"replay[default]": 1.5}
    assert default["cache"] == {
        "units": 2,
        "largest_unit": {
            "unit": 2,
            "tables": {"walk_memo": [7, 8, 0.875], "trace_cache": [50, 64, 0.781]},
            "resident_after_release": 3 * 1024,
        },
        "side_memo": [3, 4, 0.75],
        "row_store": {"rows": 15, "bytes": 4194304, "chunks": 1},
        "resident_after_walk": 4 * 1024,
    }
    assert measured["samples"] == 3 and measured["written"]["end"]["seconds"] == 1.0
    failed = {**walks, "ss03": {**walks["ss03"], "error": "ss03: 1 first-match-wins replay disagreement(s)"}}
    meters = iter(_reading(at, 0) for at in (0.0, 1.0))
    with pytest.raises(SystemExit, match="the replay failed: ss03"):
        ms.measure_replay_line(
            ["child"],
            ["default", "ss03"],
            idle_s=0,
            after_s=0,
            meter=lambda: next(meters),
            machine=lambda: QUIET,
            run=lambda arguments, **rest: _replay_run(failed, absorbs, readings, commands, passed=False),
        )


def test_a_replay_child_takes_the_series_build_only_when_told(monkeypatch, tmp_path, capsys):
    """The replay child marks the kernel built (`kernel_exec._BUILT`) before its replay only with `--built`, which the series passes once its own build has succeeded, so a line runs no `cargo build`, and a child run without it leaves the flag as it was, so the replay builds as run_build's does."""
    import pickle

    from engine.pipeline import run_build
    from engine.tools import memory_series_child

    seen: list[bool] = []

    def replay_and_absorb(spec, out_dir, memos, threads, **rest):
        seen.append(kernel_exec._BUILT)
        return run_build.FullReplay({}, {}, True)

    spec = tmp_path / "spec.pickle"
    spec.write_bytes(pickle.dumps(fixtures.mini_spec()))
    monkeypatch.setattr(run_build, "replay_and_absorb", replay_and_absorb)
    arguments = ["replay", "--spec", str(spec), "--spec-dump", "dump.json", "--out", str(tmp_path)]
    arguments += ["--configs", "default", "--width", "1"]
    for told in (False, True):
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        assert memory_series_child.main([*arguments, *(["--built"] if told else [])]) == 0
    assert seen == [False, True]
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["passed"]


def test_a_replay_run_builds_the_tables_once_and_records_its_line(monkeypatch, tmp_path):
    """A replay run builds the tables once, before its line, at the table holder's flags with the allocator's large cache off, writes the spec dump and a pickle the child reads, and measures the replay once at the width a bare run_build derives or the one stated, its child told the series has built the kernel and run in the series' own environment, without the allocator setting, which reaches the walks alone. It records the line under the replay holder with the whole alphabet, the width and the configurations, making the record's directory, and removes the tables however it ends."""
    built: list = []
    measured: list = []
    environments: list = []

    def build_replay_tables(spec_path, out_dir, configs, flags, stamp, *, env):
        built.append((spec_path.name, out_dir.name, tuple(configs)))
        environments.append(("tables", env))
        out_dir.mkdir(parents=True)

    def measure_replay_line(arguments, configs, *, env, **rest):
        spec = arguments[arguments.index("--spec") + 1]
        measured.append((Path(spec).name, arguments[arguments.index("--width") + 1], tuple(configs)))
        assert "--built" in arguments
        environments.append(("line", env))
        walk = {
            "counts": {"texts": 1, "windows": 2, "skipped": 0},
            "walk": {"peak_resident": 10, "peak_footprint": 9, "cpu_s": 1.0},
            "absorb": {"peak_resident": 5, "peak_footprint": 4, "cpu_s": 0.5},
            "pair": {"resident": 15, "footprint": 13},
            "cache": {"largest_unit": None, "row_store": {"bytes": 4}},
        }
        return {
            "combined": {"resident": 20, "footprint": 18},
            "written": {"end": None, "after": None},
            "configs_measured": {config: walk for config in configs},
        }

    monkeypatch.delenv(spec_load.RUNES_ENV, raising=False)
    for name in kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.spec_load, "load_default_spec", fixtures.mini_spec)
    monkeypatch.setattr(ms.kernel_exec, "ensure_built", lambda: None)
    monkeypatch.setattr(ms, "build_replay_tables", build_replay_tables)
    monkeypatch.setattr(ms, "measure_replay_line", measure_replay_line)
    monkeypatch.setattr(ms, "_head", lambda: "head")
    monkeypatch.setattr(ms.os, "sync", lambda: None)
    monkeypatch.setattr(ms.time, "sleep", lambda seconds: None)
    out = tmp_path / "keep" / "series.txt"
    arguments = ["--holder", "replay", "--configs", "default", "--replay-threads", "1", "--out", str(out)]
    assert ms.main(arguments) == 0
    assert built == [(ms.SPEC_DUMP, ms.REPLAY, ("default",))]
    assert measured == [(ms.SPEC_PICKLE, "1", ("default",))]
    spec = fixtures.mini_spec()
    (line,) = [json.loads(text) for text in out.read_text(encoding="utf-8").splitlines()]
    assert (line["step"], line["runes"], line["width"], line["configs"]) == (
        "replay",
        len(spec.runes),
        1,
        ["default"],
    )
    assert line["alphabet"] == sorted(spec.runes)
    assert line["walks"]["default"]["counts"]["windows"] == 2
    assert not (tmp_path / ms.TREE_PATH / ms.REPLAY).exists()
    allocator = set(kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT)
    lines = [env for kind, env in environments if kind == "line"]
    assert len(lines) == 1 and all(env is not None and not allocator & set(env) for env in lines)
    if sys.platform == "darwin":
        assert all(
            env is not None and allocator <= set(env) for kind, env in environments if kind == "tables"
        )


def test_the_peak_moment_is_the_sample_with_the_highest_footprint():
    """`start` is the machine before the spawn with no child figures, `peak` the sample whose footprint is highest though another's resident set is higher, and `end` the machine after the reap beside the last sample's child figures. The maxima cover every reading."""

    def machine(compressor, swap):
        return ms.Machine(swap_used=swap, compressor=compressor, swapouts=7, device=None)

    child = ms.ChildRun(
        returncode=0,
        stdout=b"",
        stderr="",
        wall=3.0,
        cpu=5.0,
        peak_resident=9,
        peak_footprint=12,
        begin=machine(100, 1),
        samples=(
            ms.Sample(0.5, 9, 6, machine(400, 1)),
            ms.Sample(1.5, 7, 11, machine(300, 2)),
            ms.Sample(2.5, 2, 3, machine(200, 1)),
        ),
        end=machine(150, 1),
    )
    found = ms.moments(child)
    assert found["start"] == {"at_s": 0.0, "resident": None, "footprint": None, **asdict(machine(100, 1))}
    assert found["peak"] == {"at_s": 1.5, "resident": 7, "footprint": 11, **asdict(machine(300, 2))}
    assert found["end"] == {"at_s": 3.0, "resident": 2, "footprint": 3, **asdict(machine(150, 1))}
    assert (found["compressor_max"], found["swap_used_max"]) == (400, 2)


def test_the_series_refuses_while_the_alphabet_switch_is_set(monkeypatch):
    """A run measures the whole alphabet, which the switch narrows, so a switch left set refuses the run before anything is read or built."""
    monkeypatch.setenv(spec_load.RUNES_ENV, "qsTea,qsMay")
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: pytest.fail("the crate was built"))
    with pytest.raises(SystemExit, match="measures the whole alphabet"):
        ms.main([])


def test_a_second_series_refuses_the_tree_another_holds(monkeypatch, tmp_path):
    """Two series would share the tree and each other's memory, so a run refuses while another holds the tree's lock."""
    monkeypatch.delenv(spec_load.RUNES_ENV, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: pytest.fail("the crate was built"))
    lock = store_lock.lock_path_for(tmp_path / ms.TREE_PATH)
    with store_lock.hold_flock(lock, blocking=False):
        with pytest.raises(SystemExit, match="another memory series holds"):
            ms.main(["--out", str(tmp_path / "series.txt")])


def test_a_table_run_settles_its_setups_writes_before_the_idle_reading_and_records_the_whole_alphabet(
    monkeypatch, tmp_path
):
    """The crate's build, the tree, the spec dump and the head's index refresh all write before the step, so the run flushes them and waits for them to reach the device before the step's idle reading, which would otherwise count them as the device's idle rate. The step builds the whole alphabet's one spec dump in the tree's one step directory, and the run records its line, making the record's directory."""
    events: list[object] = []

    def prepare_tree(tree, binary):
        tree.mkdir(parents=True)
        events.append("tree")
        return tree / ms.BINARY_NAME

    def measure_step(binary, spec_path, step_dir, configs, flags, stamp, **rest):
        events.append(("step", spec_path.name, step_dir.name, stamp))
        return {"written": {"end": None, "after": None}}

    monkeypatch.delenv(spec_load.RUNES_ENV, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.spec_load, "load_default_spec", fixtures.mini_spec)
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: events.append("crate"))
    monkeypatch.setattr(ms, "prepare_tree", prepare_tree)
    monkeypatch.setattr(ms.kernel_io, "write_spec", lambda spec, path: events.append("spec"))
    monkeypatch.setattr(ms, "_head", lambda: events.append("head"))
    monkeypatch.setattr(ms.os, "sync", lambda: events.append("sync"))
    monkeypatch.setattr(ms.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(ms, "measure_step", measure_step)
    out = tmp_path / "keep" / "series.txt"
    assert ms.main(["--configs", "default", "--out", str(out)]) == 0
    spec = fixtures.mini_spec()
    step = ("step", ms.SPEC_DUMP, ms.TABLES, f"{ms.STAMP_PREFIX}{len(spec.runes)}")
    assert events == ["crate", "tree", "spec", "head", "sync", ("sleep", ms.SETTLE_S), step]
    (line,) = [json.loads(text) for text in out.read_text(encoding="utf-8").splitlines()]
    assert (line["step"], line["runes"], line["configs"]) == ("tables", len(spec.runes), ["default"])
    assert (
        line["ligs"]
        == sum(1 for rune in spec.runes.values() if rune.sequence)
        == line["runes"] - line["letters"]
    )
    assert line["alphabet"] == sorted(spec.runes)


def test_the_tree_is_emptied_and_holds_its_own_copy_of_the_binary_run_once(tmp_path):
    """Preparing the tree removes what an earlier run left, files and the step's directory alike, and copies the binary in, so a crate rebuilt elsewhere during the run changes nothing it measures; the copy has already run once."""
    tree = tmp_path / "series"
    (tree / ms.TABLES).mkdir(parents=True)
    (tree / ms.SPEC_DUMP).write_text("{}")
    ran = tmp_path / "ran"
    binary = tmp_path / "kernel"
    binary.write_text(f"#!/bin/sh\necho ran >> {ran}\nexit 2\n")
    binary.chmod(0o755)
    copy = ms.prepare_tree(tree, binary)
    assert sorted(path.name for path in tree.iterdir()) == [ms.BINARY_NAME]
    assert copy.read_bytes() == binary.read_bytes()
    assert ran.read_text() == "ran\n"


def test_a_line_that_fails_partway_kills_the_childs_whole_process_group(monkeypatch, tmp_path):
    """A replay child runs in a session of its own, so an error or a stop signal while it runs kills every process it started, as a walk's crate or an absorb worker would be, and not the child alone: here the child starts a grandchild that sleeps for a minute and exits, and the reading after its exit fails."""
    pid_path = tmp_path / "grandchild"
    script = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))"
    )

    def failing(pid):
        raise RuntimeError("the footprint reading failed")

    monkeypatch.setattr(ms.peak_rss, "peak_footprint_bytes", failing)
    with pytest.raises(RuntimeError, match="the footprint reading failed"):
        ms.run_replay_child(
            [sys.executable, "-c", script],
            env=dict(os.environ),
            interval=0.05,
            machine=lambda: QUIET,
        )
    grandchild = int(pid_path.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, signal.SIGKILL)
        pytest.fail("the grandchild outlived its child")


def test_a_run_replaces_only_its_own_holders_line_in_the_record(tmp_path):
    """The record holds each holder's last line, tables first: a replay run replaces the replay line and keeps the table line as the file held it, and a later table run keeps the replay run's."""
    path = tmp_path / "series.txt"
    tables = json.dumps({"step": "tables", "head": "a"})
    old_replay = json.dumps({"step": "replay", "head": "a"})
    path.write_text(f"{old_replay}\n{tables}\n", encoding="utf-8")
    record = ms.Record(path, ms.REPLAY)
    assert path.read_text(encoding="utf-8").splitlines() == [tables]
    new_replay = json.dumps({"step": "replay", "head": "b"})
    record.add(new_replay)
    assert path.read_text(encoding="utf-8").splitlines() == [tables, new_replay]
    new_tables = json.dumps({"step": "tables", "head": "c"})
    ms.Record(path, ms.TABLES).add(new_tables)
    assert path.read_text(encoding="utf-8").splitlines() == [new_tables, new_replay]


def test_the_holder_picks_which_flags_a_run_takes():
    """The table holder is the default, and the replay holder takes `--configs` and `--replay-threads`. A positional argument, a table width under the replay holder, the replay's width under the table holder, and a width below one are refused."""
    assert ms.parse_arguments([]).holder == "tables"
    replay = ms.parse_arguments(["--holder", "replay", "--configs", "default", "--replay-threads", "1"])
    assert (replay.holder, replay.configs, replay.replay_threads) == ("replay", "default", 1)
    for refused in (
        ["56"],
        ["--replay-threads", "2"],
        ["--holder", "replay", "--kernel-threads", "2"],
        ["--holder", "replay", "--replay-threads", "0"],
    ):
        with pytest.raises(SystemExit):
            ms.parse_arguments(refused)
