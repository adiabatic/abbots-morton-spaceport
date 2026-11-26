"""Tests for the memory series (`engine/tools/memory_series.py`). The table steps build the mini fixture narrowed to ·Tea, ·May and ·It, a build of a fraction of a second, so they run the real crate. The meter, the machine reading and the waits are replaced where a test reads their arithmetic."""

import io
import json
import os
import signal
import subprocess
import sys
import time
import zlib
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

import pytest

from engine.pipeline import conform, fixtures, kernel_exec, kernel_io, spec_load
from engine.review import store_lock
from engine.tools import memory_series as ms
from engine.tools import scaling_sweep, write_meter
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
            scanned = scaling_sweep.scan(source)
        windows[config] = list(scaling_sweep.counts(scanned, payload, answers[config]["windows_bytes"]))

    step_dir = tmp_path / "step"
    seen = {}
    run_child, scan = ms.run_child, scaling_sweep.scan

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
    monkeypatch.setattr(scaling_sweep, "scan", scanning)
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
    scan = scaling_sweep.scan

    def losing(source):
        head, columns, _taken, rest = source.readline(), source.readline(), source.readline(), source.read()
        return scan(io.BytesIO(head + columns + rest))

    monkeypatch.setattr(scaling_sweep, "scan", losing)
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


def test_a_replay_cuts_tables_are_written_plain_with_their_payloads_drained_from_pipes(tmp_path):
    """A replay cut's unmeasured build writes each configuration's settlement and joins tables as plain files, which the walks read, and drains each windows payload from its pipe, so no payload file is left in the cut's directory."""
    cut_dir = tmp_path / "cut"
    ms.build_replay_tables(_spec_path(tmp_path), cut_dir, CONFIGS, FLAGS, STAMP, env=None)
    expected = {}
    for config in CONFIGS:
        expected |= {f"settlement-{config}.tsv": "file", f"joins-{config}.tsv": "file"}
    assert _listing(cut_dir) == expected


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


def _absorb_child(peak_footprint, sha256, absorb=None):
    """An absorb child's run as `run_absorb_child` returns it, with no machine to read."""
    child = ms.ChildRun(
        0,
        b"",
        "",
        2.0,
        1.5,
        peak_footprint + 13,
        peak_footprint,
        QUIET,
        (ms.Sample(1.0, 7, peak_footprint, QUIET),),
        QUIET,
    )
    return child, {"seconds": 0.75, "rows": 30, "size": 4096, "sha256": sha256, "absorb": absorb}


def test_an_absorbs_line_runs_both_absorbs_and_says_whether_their_files_agree(tmp_path):
    """An absorbs line runs the absorb's child and then the carry-and-append absorb's, each over its own job, between the meter's readings, unlinks any segment a child left, and keeps the absorb's path and row counts, both children's peaks, CPU and seconds, both files' rows, sizes and digests, and whether the digests are equal. An absorb that wrote its part into the line's clone of the standing memo is compared through that memo's materialized form, read once the meter's last reading is taken."""
    from multiprocessing import shared_memory

    stats = {
        "index": "rebuilt",
        "standing": 40,
        "carried": 20,
        "fresh": 10,
        "patched": 2,
        "dropped": 20,
        "slots": 128,
    }
    left = f"msl{os.getpid()}"
    shared_memory.SharedMemory(left, create=True, size=8, track=False).close()
    ran: list[bytes] = []

    def run(payload, **rest):
        ran.append(payload)
        return _absorb_child(100, "ab" * 32, stats) if payload == b"absorb" else _absorb_child(300, "ab" * 32)

    meters = iter(_reading(at, 1000 * at) for at in (0.0, 1.0))
    measured = ms.measure_absorb_line(
        {"absorb": b"absorb", "carry": b"carry"},
        [left],
        idle_s=0,
        after_s=0,
        meter=lambda: next(meters),
        machine=lambda: QUIET,
        sleep=lambda seconds: None,
        run=run,
    )
    assert ran == [b"absorb", b"carry"]
    with pytest.raises(FileNotFoundError):
        shared_memory.SharedMemory(left, track=False)
    assert measured["absorb"] == stats and measured["same"]
    assert (
        measured["peak_footprint"],
        measured["peak_resident"],
        measured["cpu_s"],
        measured["seconds"],
    ) == (100, 113, 1.5, 0.75)
    assert measured["carry_and_append"] == {
        "wall_s": 2.0,
        "seconds": 0.75,
        "cpu_s": 1.5,
        "peak_resident": 313,
        "peak_footprint": 300,
        "file_rows": 30,
        "size": 4096,
        "sha256": "ab" * 32,
    }
    assert measured["peak"]["footprint"] == 100 and measured["written"]["end"]["seconds"] == 1.0
    meters = iter(_reading(at, 0) for at in (0.0, 1.0))
    differing = ms.measure_absorb_line(
        {"absorb": b"absorb", "carry": b"carry"},
        [],
        idle_s=0,
        after_s=0,
        meter=lambda: next(meters),
        machine=lambda: QUIET,
        run=lambda payload, **rest: _absorb_child(1, "ab" * 32 if payload == b"absorb" else "cd" * 32),
    )
    assert not differing["same"]
    written = {**stats, "wrote": "segment", "part_bytes": 512}
    for digest, same in (("ab" * 32, True), ("cd" * 32, False)):
        meters = iter(_reading(at, 0) for at in (0.0, 1.0))
        compared = ms.measure_absorb_line(
            {"absorb": b"absorb", "carry": b"carry"},
            [],
            idle_s=0,
            after_s=0,
            meter=lambda: next(meters),
            machine=lambda: QUIET,
            run=lambda payload, **rest: (
                _absorb_child(1, "ef" * 32, written) if payload == b"absorb" else _absorb_child(1, "ab" * 32)
            ),
            materialized=lambda digest=digest: (30, 4096, digest),
        )
        assert compared["same"] == same and compared["sha256"] == digest
        assert compared["absorb"]["wrote"] == "segment" and compared["absorb"]["part_bytes"] == 512


def test_the_absorbs_inputs_restate_the_live_rows_under_labels_of_their_own(tmp_path):
    """A count's standing file holds that many rows: a prefix of the live rows below the live count, and above it the live rows followed by the live rows restated under labels suffixed with their copy, every window distinct and naming the same family. Each path's fresh windows are the ones its absorb takes: the standing file's last rows restated as they stand, the same rows under labels the file lacks, and every row in the units' parts; the prune path drops the file's last rows through the sweep's prune, and the fresh paths read no standing file. A run whose paths read no standing file writes none and makes no tail or new windows, only the units' parts."""
    spec = fixtures.mini_spec()
    guard = kernel_exec.guard_sweep(spec)
    keys = {name: f"{name}@0" for name in spec.registry.families}
    live = conform.SettleMemoFile(tmp_path / "live.bin", "stamp", keys)
    walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=live)
    walker.walk_many([a + b for a in conform.spec_alphabet(spec) for b in conform.spec_alphabet(spec)])
    assert walker.save_memo()
    store = conform._MemoStore()
    store.load(live, spec, None, lambda item: (item, "", ""))
    try:
        source = ms._CountRows(store)
        rows = source.live
        for name, count in ((rows // 2, rows // 2), (ms.LIVE, rows), (2 * rows + 5, 2 * rows + 5)):
            made = ms.absorb_count(name, source, live, live.path, tmp_path, 3, True)
            assert made.standing is not None
            assert made.rows == count and made.written == (name != ms.LIVE)
            header = ms._memo_header(made.standing)
            assert header is not None and header["rows"] == count
            windows = [window for window, _outcome in source.rows(0, count)]
            assert len(set(windows)) == count
            assert {conform._label_family(window[0]) for window in windows} <= {
                conform._label_family(label) for label in store.labels
            }
            tail = min(ms.ABSORB_TAIL, count)
            assert sum(part.rows for part in made.tail) == tail and len(made.tail) == ms.ABSORB_RANGES
            room = min(ms.ABSORB_TAIL, header["slots"] // 2 - count)
            assert sum(part.rows for part in made.new) == room and len(made.new) == ms.ABSORB_RANGES
            assert sum(part.rows for part in made.units) == count and len(made.units) == 3
            absent = tmp_path / "absent.bin"
            for path in ms.ABSORB_PATHS:
                memo, parts, sweep = ms.absorb_job(path, made, live, absent)
                expected = {"fast": made.new, "duplicate": made.tail, "stale": made.tail, "prune": []}
                assert parts == expected.get(path, made.units)
                assert (memo.path == absent) == path.startswith("fresh")
                assert (sweep is not None) == (path in ("prune", "fresh-sweep"))
                if path == "stale":
                    assert memo.family_keys[ms.ABSORB_STALE_FAMILY] != keys.get(ms.ABSORB_STALE_FAMILY)
                if path == "prune":
                    assert sweep is not None and zlib.decompress(sweep.reached).count(0) == min(
                        ms.ABSORB_PRUNED, count
                    )
        bare = tmp_path / "bare"
        bare.mkdir()
        for name, count in ((2 * rows + 5, 2 * rows + 5), (ms.LIVE, rows)):
            made = ms.absorb_count(name, source, live, live.path, bare, 3, False)
            assert (made.rows, made.standing, made.written, made.tail, made.new) == (
                count,
                None,
                False,
                [],
                [],
            )
            assert sum(part.rows for part in made.units) == count and len(made.units) == 3
            for path in ("fresh", "fresh-sweep"):
                assert ms.absorb_job(path, made, live, bare / "absent.bin")[1] == made.units
            with pytest.raises(ValueError, match="no standing file"):
                ms.absorb_job("fast", made, live, bare / "absent.bin")
        assert list(bare.iterdir()) == []
    finally:
        store.close()


def test_an_absorbs_run_makes_each_counts_inputs_once_and_records_every_line(monkeypatch, tmp_path):
    """An absorbs run clones the live `default` settle memo, its head and every part the head lists, into its tree, makes each distinct count's inputs once, and measures each named count, a count named twice twice, on every path `--paths` names, in order, each line's two jobs in the series' own environment, without the allocator setting; it records each line under the absorbs holder with its count, path and row counts, then each path's fit against rows, and removes the clone and every standing file it wrote however it ends. A run whose paths read no standing file writes none. Every segment a line ships is unlinked once the line ends, however it ends, and a run killed before its cleanup leaves their names for the next run's `empty_tree` to unlink."""
    from multiprocessing import shared_memory

    from engine.pipeline import run_build, settle_memo_keys

    spec = fixtures.mini_spec()
    guard = kernel_exec.guard_sweep(spec)
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    keys = {name: f"{name}@0" for name in spec.registry.families}
    live = conform.SettleMemoFile(out_dir / "settle-memo-default.bin", "stamp", keys)
    alphabet = list(conform.spec_alphabet(spec))
    windows: set = set()
    for texts in (alphabet[: len(alphabet) // 2], alphabet):
        walker = conform._SettledWindowWalk(spec, frozenset(), {}, guard, memo=live)
        walker.walk_many(texts)
        assert walker.save_memo()
        windows |= set(walker.windows)
    head = ms.row_delta.read_head(out_dir, live.path.name)
    assert head is not None and len(head.segments) == 1
    cloned: list = []
    clone_memo = ms.clone_memo

    def cloning(memo, directory):
        cloned.append(sorted(path.name for path in memo.path.parent.glob(f"{memo.path.name}*")))
        return clone_memo(memo, directory)

    monkeypatch.setattr(ms, "clone_memo", cloning)
    made: list = []
    inputs: list = []
    measured: list = []
    shipped: list = []
    stop: list = []
    absorb_count = ms.absorb_count

    def counting(name, *args):
        made.append(name)
        inputs.append(absorb_count(name, *args))
        return inputs[-1]

    def measure_absorb_line(jobs, names, *, env, **rest):
        assert set(jobs) == {"absorb", "carry"} and env is not None
        assert not set(kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT) & set(env)
        for name in names:
            with suppress(FileNotFoundError):
                shared_memory.SharedMemory(name, track=False).close()
                shipped.append(name)
        if stop:
            raise ms.artifact_cycle.CycleStopped(signal.SIGTERM)
        measured.append(len(names))
        return {
            "absorb": {"index": "rebuilt"},
            "peak_footprint": 10 * len(measured),
            "peak_resident": 12,
            "same": True,
            "carry_and_append": {"peak_footprint": 20},
            "written": {"end": None, "after": None},
        }

    monkeypatch.delenv(spec_load.RUNES_ENV, raising=False)
    for name in kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.spec_load, "load_default_spec", lambda: spec)
    monkeypatch.setattr(run_build, "OUT_DIR", out_dir)
    monkeypatch.setattr(settle_memo_keys, "settle_memo_inputs", lambda root: "inputs")
    monkeypatch.setattr(conform, "settle_memo_files", lambda out, spec, inputs: {"default": live})
    monkeypatch.setattr(ms, "absorb_count", counting)
    monkeypatch.setattr(ms, "measure_absorb_line", measure_absorb_line)
    monkeypatch.setattr(ms, "_head", lambda: "head")
    monkeypatch.setattr(ms.os, "sync", lambda: None)
    monkeypatch.setattr(ms.time, "sleep", lambda seconds: None)
    out = tmp_path / "series.txt"
    assert ms.main(["--holder", "absorbs", "5", "live", "5", "--paths", "fast,fresh", "--out", str(out)]) == 0
    assert made == [5, ms.LIVE]
    text = out.read_text(encoding="utf-8").splitlines()
    recorded = [json.loads(line) for line in text if line.startswith("{")]
    assert [(line["step"], line["count"], line["path"], line["rows"]) for line in recorded] == [
        ("absorbs", 5, "fast", 5),
        ("absorbs", 5, "fresh", 5),
        ("absorbs", "live", "fast", len(windows)),
        ("absorbs", "live", "fresh", len(windows)),
        ("absorbs", 5, "fast", 5),
        ("absorbs", 5, "fresh", 5),
    ]
    assert recorded[1]["standing_rows"] == 0 and recorded[0]["standing_rows"] == 5
    assert any(line.startswith("absorbs fast over 3 lines") for line in text)
    assert not (tmp_path / ms.TREE_PATH / ms.ABSORBS).exists()
    assert conform.settle_memo_standing(live)
    assert cloned[0] == sorted([ms.row_delta.head_name(live.path.name), *(part.name for part in head.parts)])
    assert [count.written for count in inputs] == [True, False]

    del made[:], inputs[:]
    assert ms.main(["--holder", "absorbs", "5", "--paths", "fresh,fresh-sweep", "--out", str(out)]) == 0
    assert [(count.standing, count.written) for count in inputs] == [(None, False)]
    before = len(shipped)
    stop.append(True)
    assert (
        ms.main(["--holder", "absorbs", "5", "--paths", "fresh", "--out", str(out)]) == 128 + signal.SIGTERM
    )
    assert len(shipped) > before and not (tmp_path / ms.TREE_PATH / ms.ABSORBS).exists()
    for name in shipped:
        with pytest.raises(FileNotFoundError):
            shared_memory.SharedMemory(name, track=False)

    left = f"msk{os.getpid()}"
    shared_memory.SharedMemory(left, create=True, size=8, track=False).close()
    record = tmp_path / ms.TREE_PATH / ms.ABSORBS / ms.ABSORB_SEGMENTS
    record.parent.mkdir(parents=True)
    record.write_text(f"{left}\n", encoding="utf-8")
    ms.empty_tree(tmp_path / ms.TREE_PATH)
    with pytest.raises(FileNotFoundError):
        shared_memory.SharedMemory(left, track=False)
    assert list((tmp_path / ms.TREE_PATH).iterdir()) == []


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


def test_a_replay_run_builds_each_cut_once_and_records_every_named_line(monkeypatch, tmp_path):
    """A replay run builds each distinct cut's tables once, before any line, at the table holder's flags with the allocator's large cache off, writes each cut's spec dump and a pickle the child reads, and measures every named line in order, a count named twice twice, at the width a bare run_build derives or the one stated, each child told the series has built the kernel and run in the series' own environment, without the allocator setting, which reaches the walks alone; it records each line under the replay holder with the cut, the width and the configurations, then the fit, and removes the cuts' tables however it ends."""
    built: list = []
    measured: list = []
    environments: list = []

    def build_replay_tables(spec_path, cut_dir, configs, flags, stamp, *, env):
        built.append((spec_path.name, cut_dir.name, tuple(configs)))
        environments.append(("tables", env))
        cut_dir.mkdir(parents=True)

    def measure_replay_line(arguments, configs, *, env, **rest):
        spec = arguments[arguments.index("--spec") + 1]
        measured.append((Path(spec).name, arguments[arguments.index("--width") + 1], tuple(configs)))
        assert "--built" in arguments
        environments.append(("line", env))
        walk = {
            "counts": {"texts": 1, "windows": 2 * len(measured), "skipped": 0},
            "walk": {"peak_resident": 10 * len(measured), "peak_footprint": 9, "cpu_s": 1.0},
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
    out = tmp_path / "series.txt"
    assert (
        ms.main(
            [
                "--holder",
                "replay",
                "3",
                "4",
                "3",
                "--configs",
                "default",
                "--replay-threads",
                "1",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert built == [("spec-r3.json", "r3", ("default",)), ("spec-r4.json", "r4", ("default",))]
    assert measured == [
        ("spec-r3.pickle", "1", ("default",)),
        ("spec-r4.pickle", "1", ("default",)),
        ("spec-r3.pickle", "1", ("default",)),
    ]
    text = out.read_text(encoding="utf-8").splitlines()
    recorded = [json.loads(line) for line in text if line.startswith("{")]
    assert [(line["step"], line["runes"], line["width"], line["configs"]) for line in recorded] == [
        ("replay", 3, 1, ["default"]),
        ("replay", 4, 1, ["default"]),
        ("replay", 3, 1, ["default"]),
    ]
    assert recorded[0]["walks"]["default"]["counts"]["windows"] == 2
    assert any(line.startswith("replay fit over 3 lines") for line in text)
    assert not (tmp_path / ms.TREE_PATH / ms.REPLAY).exists()
    allocator = set(kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT)
    lines = [env for kind, env in environments if kind == "line"]
    assert len(lines) == 3 and all(env is not None and not allocator & set(env) for env in lines)
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


def test_the_report_fits_the_windows_over_every_configuration_and_each_tables_length():
    """The windows are summed over the configurations before the fit, each collection of the first configuration's `tables` is fitted on its length, and a line without a figure is left out of that figure's fit."""

    def line(runes, scale, footprint):
        return {
            "runes": runes,
            "configs": ["default", "ss03"],
            "windows": {"default": [scale, 1, 1], "ss03": [scale, 1, 1]},
            "peak_footprint": footprint,
            "peak_resident": scale,
            "cpu_s": float(scale),
            "tables": {"default": {"trace_cache": [scale * 2, scale * 4, 0.5]}},
        }

    lines = [line(10, 100, 50), line(20, 400, None), line(40, 1600, 800)]
    report = ms.report(lines)
    assert report[0] == "whole-series fit over 3 sizes (runes 10..40)"
    fits = {text.split(" ~ ")[0].strip(): text.split("^")[1] for text in report[1:]}
    assert fits == {
        "windows": "2.00",
        "peak footprint": "2.00",
        "peak resident": "2.00",
        "cpu": "2.00",
        "default trace_cache len": "2.00",
    }
    assert ms.report(lines[:1]) == ["the whole-series fit needs two sizes; this run has 1"]


def test_the_series_refuses_while_the_alphabet_switch_is_set(monkeypatch):
    """The series cuts its own alphabets from the whole store, so a switch left set refuses the run before anything is read or built."""
    monkeypatch.setenv(spec_load.RUNES_ENV, "qsTea,qsMay")
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: pytest.fail("the crate was built"))
    with pytest.raises(SystemExit, match="cuts its own alphabets"):
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


def test_the_setups_writes_are_flushed_and_settled_before_the_first_steps_idle_reading(monkeypatch, tmp_path):
    """The crate's build, the tree, the spec dumps and the head's index refresh all write before the first step, so the run flushes them and waits for them to reach the device before that step's idle reading, which would otherwise count them as the device's idle rate."""
    events: list[object] = []

    class FirstStep(Exception):
        pass

    def prepare_tree(tree, binary):
        tree.mkdir(parents=True)
        events.append("tree")
        return tree / ms.BINARY_NAME

    def measure_step(*arguments, **rest):
        events.append("step")
        raise FirstStep

    monkeypatch.delenv(spec_load.RUNES_ENV, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.spec_load, "load_default_spec", fixtures.mini_spec)
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: events.append("crate"))
    monkeypatch.setattr(ms, "prepare_tree", prepare_tree)
    monkeypatch.setattr(ms.kernel_io, "write_spec", lambda cut, path: events.append("spec"))
    monkeypatch.setattr(ms, "_head", lambda: events.append("head"))
    monkeypatch.setattr(ms.os, "sync", lambda: events.append("sync"))
    monkeypatch.setattr(ms.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(ms, "measure_step", measure_step)
    with pytest.raises(FirstStep):
        ms.main(["3", "--out", str(tmp_path / "series.txt")])
    assert events == ["crate", "tree", "spec", "head", "sync", ("sleep", ms.SETTLE_S), "step"]


def test_the_tree_is_emptied_and_holds_its_own_copy_of_the_binary_run_once(tmp_path):
    """Preparing the tree removes what an earlier run left, files and step directories alike, and copies the binary in, so a crate rebuilt elsewhere during the series changes nothing it measures; the copy has already run once."""
    tree = tmp_path / "series"
    (tree / "r34").mkdir(parents=True)
    (tree / "spec-r34.json").write_text("{}")
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


def test_a_run_replaces_only_its_own_holders_lines_and_fit_in_the_record(tmp_path):
    """The record holds each holder's last run, tables first: a replay run replaces the replay lines and fit and keeps the table lines and fit as the file held them, and a later table run keeps the replay run's."""
    path = tmp_path / "series.txt"
    tables = [
        json.dumps({"step": "tables", "runes": 45}),
        "",
        "whole-series fit over 1 sizes",
        "  windows ~ runes^2.00",
    ]
    old_replay = [
        json.dumps({"step": "replay", "runes": 34}),
        "",
        "the replay fit needs two cuts; this run has 1",
    ]
    path.write_text("".join(f"{line}\n" for line in (*old_replay, *tables)), encoding="utf-8")
    record = ms.Record(path, ms.REPLAY)
    assert path.read_text(encoding="utf-8").splitlines() == tables
    new_replay = [
        json.dumps({"step": "replay", "runes": 38}),
        json.dumps({"step": "replay", "runes": 41}),
    ]
    record.add(*new_replay)
    record.add("", "replay fit over 2 lines")
    assert path.read_text(encoding="utf-8").splitlines() == [
        *tables,
        *new_replay,
        "",
        "replay fit over 2 lines",
    ]
    ms.Record(path, ms.TABLES).add(json.dumps({"step": "tables", "runes": 34}))
    assert path.read_text(encoding="utf-8").splitlines() == [
        json.dumps({"step": "tables", "runes": 34}),
        *new_replay,
        "",
        "replay fit over 2 lines",
    ]


def test_the_holder_picks_which_lines_and_flags_a_run_takes():
    """Rune counts name cut lines for the table and replay holders, a count named twice twice; the replay holder takes `--configs` and `--replay-threads`; the absorbs holder takes row counts and `live`, `1000000 live 9000000 12000000` by default, and `--paths`, every path by default. A line that is not a rune count, a table width under the replay holder, the replay's width under another holder, a width below one, an absorbs line that is neither a row count nor `live`, a path the holder does not know, `--paths` under another holder, and another holder's flag under the absorbs holder are refused."""
    assert (ms.parse_arguments(["34"]).holder, ms.parse_arguments(["34"]).sizes) == ("tables", [34])
    replay = ms.parse_arguments(
        ["--holder", "replay", "45", "45", "--configs", "default", "--replay-threads", "1"]
    )
    assert (replay.holder, replay.sizes, replay.configs, replay.replay_threads) == (
        "replay",
        [45, 45],
        "default",
        1,
    )
    for refused in (
        ["load-x1"],
        ["--replay-threads", "2"],
        ["--holder", "replay", "load-x1"],
        ["--holder", "replay", "--kernel-threads", "2"],
        ["--holder", "replay", "--replay-threads", "0"],
        ["--holder", "absorbs", "load-x1"],
        ["--holder", "absorbs", "lively"],
        ["--holder", "absorbs", "0"],
        ["--holder", "absorbs", "--paths", "fast,slow"],
        ["--holder", "absorbs", "--configs", "default"],
        ["--holder", "absorbs", "--replay-threads", "2"],
        ["--holder", "replay", "--paths", "fast"],
    ):
        with pytest.raises(SystemExit):
            ms.parse_arguments(refused)
    absorbs = ms.parse_arguments(["--holder", "absorbs", "live", "9000000", "live", "--paths", "stale,fast"])
    assert (absorbs.holder, absorbs.counts, absorbs.paths) == (
        "absorbs",
        ["live", 9_000_000, "live"],
        ["stale", "fast"],
    )
    defaults = ms.parse_arguments(["--holder", "absorbs"])
    assert (defaults.counts, defaults.paths) == (
        [1_000_000, "live", 9_000_000, 12_000_000],
        list(ms.ABSORB_PATHS),
    )
