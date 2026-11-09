"""Tests for the memory series (`rebuild/tools/memory_series.py`). The table steps build the mini fixture narrowed to ·Tea, ·May and ·It, a build of a fraction of a second, so they run the real crate; the corpus lines build the frozen mini bundle (`mini_bundle`, `mini_corpus`) through the real child, a build of a few seconds. The meter, the machine reading and the waits are replaced where a test reads their arithmetic."""

import gzip
import io
import json
import os
import signal
import subprocess
import sys
import time
import zlib
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from rebuild.pipeline import conform, fingerprint, fixtures, kernel_exec, kernel_io, spec_load
from rebuild.review import audit, build, store_lock, subset_pack, unit_cache
from rebuild.tools import memory_series as ms
from rebuild.tools import memory_tally, scaling_sweep, write_meter
from rebuild.tools.write_meter import Member, WriteReading

CONFIGS = list(conform.SETTLEMENT_CONFIGS)
FLAGS = ms.Flags(threads=2, scratch_beside_default=1)
STAMP = "memory-series-test"
QUIET = ms.Machine(swap_used=None, compressor=None, swapouts=None, device=None)
MINI = Path(__file__).resolve().parent / "review" / "fixtures" / "mini"
WIDTHS = {"jobs": 2, "signature_jobs": 2, "write_jobs": 2}
clones = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="the corpus step clones its inputs, which APFS alone does without writing them",
)


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


def test_a_step_reads_through_fifos_what_a_build_on_files_writes_and_leaves_only_null_links(
    monkeypatch, tmp_path
):
    """A step's windows counts and digests are what the same build writes as files, counted and hashed after it exits, and while the crate runs its directory holds the FIFOs and the settlement and joins links to the null device and nothing else, so the build writes no file. The directory is gone once the step returns."""
    spec_path = _spec_path(tmp_path)
    files = tmp_path / "files"
    arguments = ms.step_arguments(kernel_exec.BINARY, spec_path, files, CONFIGS, STAMP, FLAGS)
    done = subprocess.run(arguments, capture_output=True, check=True)
    digests = {answer["config"]: answer["digest"] for answer in map(json.loads, done.stdout.splitlines())}
    windows = {}
    for config in CONFIGS:
        payload = kernel_exec.windows_payload(files, config)
        with payload.open("rb") as source:
            windows[config] = list(scaling_sweep.counts(scaling_sweep.scan(source), payload))

    step_dir = tmp_path / "step"
    seen: dict[str, str] = {}
    run_child = ms.run_child

    def looking(arguments, **rest):
        for path in step_dir.iterdir():
            seen[path.name] = "fifo" if path.is_fifo() else os.readlink(path) if path.is_symlink() else "file"
        return run_child(arguments, **rest)

    monkeypatch.setattr(ms, "run_child", looking)
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
        expected |= {f"windows-{config}.tsv": "fifo"}
    assert seen == expected
    assert not step_dir.exists()


def test_the_meter_reads_idle_then_before_the_fifos_the_end_and_after_the_wait_with_the_device_netted(
    tmp_path,
):
    """The meter is read once before the idle wait, once just before the step's FIFOs are made, once the build is done, and once more after the after-wait. Each block reads from the reading before the FIFOs, and its `device_net` is its device delta less the idle rate over that block's own seconds: 1 MB/s idle over 10 s, a 50 MB device delta over the step's 20 s and 80 MB over the 320 s to the later reading."""
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
    """An absorbs run clones the live `default` settle memo, its head and every part the head lists, into its tree, makes each distinct count's inputs once, and measures each named count, a count named twice twice, on every path `--paths` names, in order, each line's two jobs in the series' own environment; it records each line under the absorbs holder with its count, path and row counts, then each path's fit against rows, and removes the clone and every standing file it wrote however it ends. A run whose paths read no standing file writes none. Every segment a line ships is unlinked once the line ends, however it ends, and a run killed before its cleanup leaves their names for the next run's `empty_tree` to unlink."""
    from multiprocessing import shared_memory

    from rebuild.pipeline import oracle_cache, run_m1

    spec = fixtures.mini_spec()
    guard = kernel_exec.guard_sweep(spec)
    out_dir = tmp_path / "m1"
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

    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.spec_load, "load_default_spec", lambda: spec)
    monkeypatch.setattr(run_m1, "OUT_DIR", out_dir)
    monkeypatch.setattr(oracle_cache, "settle_memo_inputs", lambda root: "inputs")
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
    """The replay child marks the kernel built (`kernel_exec._BUILT`) before its replay only with `--built`, which the series passes once its own build has succeeded, so a line runs no `cargo build`, and a child run without it leaves the flag as it was, so the replay builds as run_m1's does."""
    import pickle

    from rebuild.pipeline import run_m1
    from rebuild.tools import memory_series_child

    seen: list[bool] = []

    def replay_and_absorb(spec, out_dir, memos, threads, **rest):
        seen.append(kernel_exec._BUILT)
        return run_m1.FullReplay({}, {}, True)

    spec = tmp_path / "spec.pickle"
    spec.write_bytes(pickle.dumps(fixtures.mini_spec()))
    monkeypatch.setattr(run_m1, "replay_and_absorb", replay_and_absorb)
    arguments = ["replay", "--spec", str(spec), "--spec-dump", "dump.json", "--out", str(tmp_path)]
    arguments += ["--configs", "default", "--width", "1"]
    for told in (False, True):
        monkeypatch.setattr(kernel_exec, "_BUILT", False)
        assert memory_series_child.main([*arguments, *(["--built"] if told else [])]) == 0
    assert seen == [False, True]
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["passed"]


def test_a_replay_run_builds_each_cut_once_and_records_every_named_line(monkeypatch, tmp_path):
    """A replay run builds each distinct cut's tables once, before any line, at the table holder's flags with the allocator's large cache off, writes each cut's spec dump and a pickle the child reads, and measures every named line in order, a count named twice twice, at the width a bare run_m1 derives or the one stated, each child told the series has built the kernel and run in the series' own environment, without the allocator setting, which reaches the walks alone; it records each line under the replay holder with the cut, the width and the configurations, then the fit, and removes the cuts' tables however it ends."""
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

    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
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
    monkeypatch.setenv(spec_load.M1_RUNES_ENV, "qsTea,qsMay")
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: pytest.fail("the crate was built"))
    with pytest.raises(SystemExit, match="cuts its own alphabets"):
        ms.main([])


def test_a_second_series_refuses_the_tree_another_holds(monkeypatch, tmp_path):
    """Two series would share the tree and each other's memory, so a run refuses while another holds the tree's lock."""
    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
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

    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
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


def _mini_inputs(mini_bundle, mini_corpus) -> ms.CorpusInputs:
    return ms.CorpusInputs(
        audit=MINI / "audit.tsv",
        m1=MINI,
        subset_pack=mini_bundle.subset_pack,
        after_font=MINI / "M1.otf",
        manifest=mini_corpus / "manifest.json",
        signatures=unit_cache.signature_store_path(mini_corpus),
        spec_root=mini_bundle.spec_root,
        ledger=mini_bundle.ledger,
        before_font=build.SITE_BEFORE_FONT,
        junior_font=build.SITE_JUNIOR_FONT,
    )


def _routes(inputs: ms.CorpusInputs) -> list[str]:
    live = json.loads(inputs.manifest.read_text(encoding="utf-8"))
    return ms.null_routes(
        live, [*(entry.id for entry in audit.load_ledger(inputs.ledger)), *audit.RESERVED_CLASS_IDS]
    )


def _measure_line(inputs, step_dir, cut, routes, **given):
    return ms.measure_corpus_line(
        inputs,
        step_dir,
        cut,
        WIDTHS,
        routes,
        **{
            "env": ms.corpus_environment(),
            "cwd": build.REPO_ROOT,
            "idle_s": 0,
            "after_s": 0,
            "interval_s": 0.05,
            "meter": lambda: write_meter.read(device=False),
            "machine": lambda: QUIET,
            **given,
        },
    )


def _audit_rows(excluded=frozenset()):
    rows = (MINI / "audit.tsv").read_text(encoding="utf-8").splitlines()[1:]
    return [row for row in rows if not {int(part, 16) for part in row.split("\t")[1].split(":")} & excluded]


@clones
def test_a_cut_line_builds_over_the_cut_audit_and_writes_only_what_the_build_reads_back(
    mini_bundle, mini_corpus, monkeypatch, tmp_path
):
    """A cut line feeds the build the audit rows whose windows use only the cut's letters, here every letter but ·Out and ·Fee. As the child starts, its out dir holds a clone of the live signature store and a link to the null device at every staging name `null_routes` gives, and once the line is done the only regular files it left are the ones the build reads back or does not route, and its directory is gone. The line reads every mark in `CORPUS_MARKS` order, the signatures all answered by the clone, both pools' workers from their pool records and from the sampler, and one held sidecar figure a write run from the write phase's note. The checkout's code digests are read after the idle wait and before the after-wait, so an edit made during either wait is not one made during the build."""
    inputs = _mini_inputs(mini_bundle, mini_corpus)
    routes = _routes(inputs)
    excluded = frozenset({0xE67B, 0xE658})
    seen: dict[str, str] = {}
    route = ms.route_corpus
    waits: list[float] = []
    monkeypatch.setattr(ms, "corpus_code", lambda checkout: {"corpus_code": f"after {len(waits)} waits"})

    def looking(step_dir, signatures, given):
        fifo = route(step_dir, signatures, given)
        for path in (step_dir / ms.OUT_NAME).rglob("*"):
            if path.is_symlink() or path.is_file():
                seen[path.relative_to(step_dir / ms.OUT_NAME).as_posix()] = (
                    os.readlink(path) if path.is_symlink() else "file"
                )
        return fifo

    monkeypatch.setattr(ms, "route_corpus", looking)
    step_dir = tmp_path / "step"
    measured = _measure_line(
        inputs,
        step_dir,
        ms.CorpusCut("r-test", ("qsDay",), 1, excluded),
        routes,
        idle_s=1,
        after_s=1,
        sleep=waits.append,
    )
    assert waits == [1, 1]
    assert measured["code"] == {"corpus_code": "after 1 waits"} and "code_after" not in measured
    assert seen == {**dict.fromkeys(routes, os.devnull), unit_cache.SIGNATURE_STORE_NAME: "file"}
    assert measured["rows"] == len(_audit_rows(excluded)) < len(_audit_rows())
    assert 0 < measured["feed_s"] < measured["wall_s"]
    assert [mark["label"] for mark in measured["marks"]] == list(ms.CORPUS_MARKS)
    assert measured["files"]["stray"] == {}
    assert {"out/manifest.json", "out/review-facts.json", ms.POOLS_NAME} <= set(measured["files"]["kept"])
    assert measured["signatures_shaped"] == 0
    for name in ("units", "write"):
        pool = measured["pools"][name]
        assert pool["width"] == 2 and pool["reading"] == "footprint", pool
        assert len(pool["peaks"]) == len(pool["resident"]) == 2, pool
    assert measured["pools"]["signature"]["peaks"] == []
    assert len(measured["sidecar_lines_mb"]) == len(measured["write_runs"]) and all(
        measured["sidecar_lines_mb"]
    )
    assert measured["recomputed"] == measured["units"] and measured["fragments"]["deflated"]
    assert not step_dir.exists()


@clones
def test_a_load_line_stops_at_the_plan_over_the_audit_and_its_moved_copy(mini_bundle, mini_corpus, tmp_path):
    """The ×1 load line feeds the live audit, the ×2 line the live audit and then its moved copy, so ×2 loads twice the rows and twice the units with no merge between them. The mark reporter stops each build at the plan's mark: the marks end there, no pool starts, and the line leaves no file."""
    inputs = _mini_inputs(mini_bundle, mini_corpus)
    lines = {}
    for scale in ms.LOAD_SCALES:
        cut = ms.CorpusCut(f"{ms.LOAD_PREFIX}{scale}", ("qsDay",), 1, frozenset(), scale, True)
        lines[scale] = _measure_line(inputs, tmp_path / cut.name, cut, _routes(inputs))
    plan = ms.CORPUS_MARKS.index(ms.PLAN_MARK)
    for line in lines.values():
        assert [mark["label"] for mark in line["marks"]] == list(ms.CORPUS_MARKS[: plan + 1])
        assert line["units_premerge"] == line["units"]
        assert line["files"] == {"kept": {}, "stray": {}}
        assert all(pool["width"] is None for pool in line["pools"].values())
    assert lines[1]["rows"] == len(_audit_rows())
    assert (lines[2]["rows"], lines[2]["units"]) == (2 * lines[1]["rows"], 2 * lines[1]["units"])


def test_a_line_that_fails_partway_kills_the_builds_whole_process_group(monkeypatch, tmp_path):
    """A corpus child runs in a session of its own, so an error or a stop signal while it runs kills every process it started, as a pool worker would be, and not the child alone: here the child starts a grandchild that sleeps for a minute and exits, and the reading after its exit fails."""
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
    fifo = tmp_path / ms.AUDIT_NAME
    os.mkfifo(fifo)
    with pytest.raises(RuntimeError, match="the footprint reading failed"):
        ms.run_corpus_child(
            [sys.executable, "-c", script],
            env=dict(os.environ),
            cwd=tmp_path,
            audit=MINI / "audit.tsv",
            fifo=fifo,
            pattern=None,
            scale=1,
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
        pytest.fail("the grandchild outlived its build")


def test_the_feed_keeps_the_rows_whose_windows_use_only_the_cuts_letters_and_moves_them_for_the_doubled_load(
    tmp_path,
):
    """The cut keeps a row when none of its window's code points is one the cut leaves out, matched as a whole token, so ·Bay's E651 does not drop a window holding 1E651. At a scale of 2 the kept rows follow again with each Quikscript code point moved up by `MOVED` and both name fields tagged; with nothing left out, every row is kept."""
    header = "\t".join(audit.AUDIT_HEADER)
    rows = [
        "default\tE650:E652\tcell\tjoin-dropped\tqsPea|qsTea\tqsPea/a|qsTea/b",
        "default\tE650:E651\tcell\tjoin-dropped\tqsPea|qsBay\tqsPea/a|qsBay/b",
        "ss03\t200C:E651\tcell\tUNMATCHED\tzwnj|qsBay\tzwnj/c|qsBay/d",
        "default\t0020:E650\tcell\tUNMATCHED\tspace|qsPea\tspace/c|qsPea/d",
        "default\t1E651:E650\tcell\tUNMATCHED\tx|qsPea\tx/c|qsPea/d",
    ]
    source = tmp_path / "audit.tsv"
    source.write_text("".join(f"{line}\n" for line in (header, *rows)), encoding="utf-8")
    sink = io.BytesIO()
    fed = ms.feed_audit(source, sink, ms.cut_pattern({0xE651}), 2)
    kept = [rows[0], rows[3], rows[4]]
    moved = [
        "default\tE750:E752\tcell\tjoin-dropped\tqsPea|qsTea.x\tqsPea/a|qsTea/b.x",
        "default\t0020:E750\tcell\tUNMATCHED\tspace|qsPea.x\tspace/c|qsPea/d.x",
        "default\t1E651:E750\tcell\tUNMATCHED\tx|qsPea.x\tx/c|qsPea/d.x",
    ]
    assert sink.getvalue().decode() == "".join(f"{line}\n" for line in (header, *kept, *moved))
    assert fed == 6
    assert ms.cut_pattern(()) is None
    whole = io.BytesIO()
    assert ms.feed_audit(source, whole, None, 1) == len(rows)
    assert whole.getvalue() == source.read_bytes()


def test_the_listing_names_every_file_the_build_left_and_strays_what_it_was_not_expected_to(tmp_path):
    """A line's listing holds every regular file under its directory with its size, and puts any the step does not expect under `stray`: an output whose staging name the routes missed is written for real and shows there, while a link to the null device, the FIFO and the directories are not files."""
    out_dir = tmp_path / ms.OUT_NAME
    (out_dir / "units").mkdir(parents=True)
    (out_dir / "manifest.json").write_text("{}")
    (out_dir / "units" / "join-dropped.json").symlink_to(os.devnull)
    (out_dir / "units-index.ndjson.gz").write_bytes(b"stray")
    (tmp_path / ms.POOLS_NAME).write_text("{}\n")
    os.mkfifo(tmp_path / ms.AUDIT_NAME)
    assert ms.step_files(tmp_path, ms.expected_files(False)) == {
        "kept": {"out/manifest.json": 2, ms.POOLS_NAME: 3},
        "stray": {"out/units-index.ndjson.gz": 5},
    }
    assert ms.step_files(tmp_path, ms.expected_files(True))["stray"] == {
        "out/manifest.json": 2,
        "out/units-index.ndjson.gz": 5,
    }


def test_a_corpus_run_records_each_line_and_stops_at_the_first_that_leaves_a_stray(
    mini_bundle, mini_corpus, monkeypatch, tmp_path, capsys
):
    """A corpus run records each line under the corpus holder as it finishes, and a line that leaves a file the step does not expect is recorded and then stops the run, naming the file, before any later line or the fit. Code that moved during a line's build is warned about under that line and recorded beside the digests it started with, code that moved since the first line is warned about too, and the run removes its clones however it ends."""
    measured_names: list[str] = []
    readings = iter(
        [
            {"code": {"corpus_code": "first"}, "code_after": {"corpus_code": "moved"}},
            {"code": {"corpus_code": "moved"}},
        ]
    )
    clones = tmp_path / ms.TREE_PATH / ms.CORPUS / "inputs"

    def clone_inputs(root, base):
        assert base == clones
        base.mkdir(parents=True)
        (base / "audit.tsv").write_text("clone")
        return _mini_inputs(mini_bundle, mini_corpus)

    def measure_corpus_line(inputs, step_dir, cut, *arguments, **rest):
        measured_names.append(cut.name)
        stray = {"out/units-index.ndjson.gz": 5} if len(measured_names) == 2 else {}
        return {
            "written": {"end": None, "after": None},
            "signatures_shaped": 0,
            "files": {"kept": {}, "stray": stray},
            **next(readings),
        }

    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
    monkeypatch.delenv(memory_tally.TALLY_ENV, raising=False)
    monkeypatch.setattr(ms, "ROOT", tmp_path)
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: None)
    monkeypatch.setattr(ms, "clone_inputs", clone_inputs)
    monkeypatch.setattr(ms, "check_inputs", lambda inputs: {"stage_a": {}, "corpus_generated_at": None})
    monkeypatch.setattr(ms.spec_load, "load_spec", lambda *paths: fixtures.mini_spec())
    monkeypatch.setattr(ms, "_head", lambda: "head")
    monkeypatch.setattr(ms.os, "sync", lambda: None)
    monkeypatch.setattr(ms.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ms, "measure_corpus_line", measure_corpus_line)
    out = tmp_path / "series.txt"
    with pytest.raises(SystemExit, match=r"r4 left files .*out/units-index\.ndjson\.gz \(5 B\)"):
        ms.main(
            ["--holder", "corpus", "3", "4", "load-x1", "--jobs", "1", "--write-jobs", "1", "--out", str(out)]
        )
    assert measured_names == ["r3", "r4"]
    recorded = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [(line["step"], line["runes"]) for line in recorded] == [("corpus", 3), ("corpus", 4)]
    assert recorded[1]["files"]["stray"] == {"out/units-index.ndjson.gz": 5}
    assert (recorded[0]["code"]["corpus_code"], recorded[0]["code_after"]["corpus_code"]) == (
        "first",
        "moved",
    )
    assert recorded[1]["code"]["corpus_code"] == "moved" and "code_after" not in recorded[1]
    warned = [line for line in capsys.readouterr().err.splitlines() if line.startswith("[warn]")]
    assert warned == [
        "[warn] r3: the checkout's corpus code or crate binary moved during the build",
        "[warn] r4: the checkout's corpus code or crate binary moved since the first line",
    ]
    assert not clones.parent.exists()


def test_a_run_replaces_only_its_own_holders_lines_and_fit_in_the_record(tmp_path):
    """The record holds each holder's last run, tables first: a corpus run replaces the corpus lines and fit and keeps the table lines and fit as the file held them, and a later table run keeps the corpus run's."""
    path = tmp_path / "series.txt"
    tables = [
        json.dumps({"step": "tables", "runes": 45}),
        "",
        "whole-series fit over 1 sizes",
        "  windows ~ runes^2.00",
    ]
    old_corpus = [
        json.dumps({"step": "corpus", "runes": 30}),
        "",
        "the corpus fit needs two cuts; this run has 1",
    ]
    path.write_text("".join(f"{line}\n" for line in (*old_corpus, *tables)), encoding="utf-8")
    record = ms.Record(path, ms.CORPUS)
    assert path.read_text(encoding="utf-8").splitlines() == tables
    new_corpus = [
        json.dumps({"step": "corpus", "runes": 34}),
        json.dumps({"step": "corpus-load", "scale": 2}),
    ]
    record.add(*new_corpus)
    record.add("", "corpus fit over 1 cuts")
    assert path.read_text(encoding="utf-8").splitlines() == [
        *tables,
        *new_corpus,
        "",
        "corpus fit over 1 cuts",
    ]
    ms.Record(path, ms.TABLES).add(json.dumps({"step": "tables", "runes": 34}))
    assert path.read_text(encoding="utf-8").splitlines() == [
        json.dumps({"step": "tables", "runes": 34}),
        *new_corpus,
        "",
        "corpus fit over 1 cuts",
    ]


def test_the_corpus_inputs_are_refused_when_the_live_corpus_or_the_pack_is_not_built_over_the_tables(
    mini_bundle, tmp_path
):
    """Inputs whose live manifest carries the Stage A record beside the tables, and whose pack is current for those tables, give the line its inputs' identity. A manifest built over another Stage A record is refused, since its signature store would miss, and so is a pack another table no longer matches, since the build would rewrite it."""
    m1 = tmp_path / "m1"
    m1.mkdir()
    for config in audit.ACCEPTANCE_CONFIGS:
        name = subset_pack.table_path(MINI, config).name
        (m1 / name).write_bytes((MINI / name).read_bytes())
    stage_a = {key: f"{key}-digest" for key in fingerprint.STAGE_A_COMPONENTS}
    (m1 / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"format": "x", **stage_a}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"generated_at": "then", "inputs_fingerprint": stage_a}), encoding="utf-8")
    inputs = ms.CorpusInputs.under(tmp_path)
    inputs = ms.CorpusInputs(
        **{**asdict(inputs), "m1": m1, "subset_pack": mini_bundle.subset_pack, "manifest": manifest}
    )
    assert ms.check_inputs(inputs) == {"stage_a": stage_a, "corpus_generated_at": "then"}
    manifest.write_text(json.dumps({"inputs_fingerprint": {**stage_a, "data": "older"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="was not built over the M1 outputs' Stage A record"):
        ms.check_inputs(inputs)
    manifest.write_text(json.dumps({"inputs_fingerprint": stage_a}), encoding="utf-8")
    table = subset_pack.table_path(m1, "default")
    table.write_bytes(gzip.compress(gzip.decompress(table.read_bytes()) + b"\n"))
    with pytest.raises(SystemExit, match="is not current for the subset tables"):
        ms.check_inputs(inputs)


def test_the_holder_picks_which_lines_and_flags_a_run_takes():
    """Rune counts name cut lines for every holder but the absorbs holder, a count named twice twice, and `load-x1` and `load-x2` name the corpus holder's load lines; the replay holder takes `--configs` and `--replay-threads`; the absorbs holder takes row counts and `live`, `1000000 live 9000000 12000000` by default, and `--paths`, every path by default. A load line under another holder, a corpus width under the table or replay holder, a table width under the corpus or replay holder, the replay's width under another holder, a width below one, an absorbs line that is neither a row count nor `live`, a path the holder does not know, `--paths` under another holder, and another holder's flag under the absorbs holder are refused."""
    args = ms.parse_arguments(["--holder", "corpus", "30", "load-x1", "45", "--jobs", "4"])
    assert (args.holder, args.sizes, args.loads, args.jobs) == ("corpus", [30, 45], [1], 4)
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
        ["--jobs", "2"],
        ["--replay-threads", "2"],
        ["--holder", "corpus", "--kernel-threads", "2"],
        ["--holder", "corpus", "--replay-threads", "2"],
        ["--holder", "corpus", "load-x3"],
        ["--holder", "replay", "load-x1"],
        ["--holder", "replay", "--kernel-threads", "2"],
        ["--holder", "replay", "--write-jobs", "2"],
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


def test_a_corpus_run_refuses_while_the_debug_tally_is_on(monkeypatch):
    """The tally keeps alive the collections it reads, which would move every corpus reading, so a corpus run refuses before anything is built while its switch is set."""
    monkeypatch.delenv(spec_load.M1_RUNES_ENV, raising=False)
    monkeypatch.setenv(memory_tally.TALLY_ENV, "1")
    monkeypatch.setattr(ms.kernel_exec, "cargo_build", lambda: pytest.fail("the crate was built"))
    with pytest.raises(SystemExit, match="debug tally"):
        ms.main(["--holder", "corpus"])


def test_a_worker_belongs_to_the_pool_whose_marks_bracket_its_first_sample():
    """A child of the parent that runs multiprocessing's spawn entry is a worker of the pool between whose closing mark and the previous pool's the sampler first saw it, and a pool's peaks come from its last pool record. The resource tracker is no pool's and nothing else's, and every other process, a git call the parent makes beside the write pool or a crate call below a worker, is no pool's and is summed apart."""
    spawned = "python -c from multiprocessing.spawn import spawn_main; spawn_main(pipe_handle=9) --multiprocessing-fork"
    marks = [
        {"label": "signatures resolved", "at_s": 2.0},
        {"label": "units pool stopped", "at_s": 10.0},
        {"label": "write pool stopped", "at_s": 20.0},
    ]
    found = [
        ms.Descendant(11, 1, 1.5, spawned, resident=100),
        ms.Descendant(
            12, 1, 1.0, "python -c from multiprocessing.resource_tracker import main;main(5)", resident=7
        ),
        ms.Descendant(13, 1, 3.0, spawned, resident=300),
        ms.Descendant(14, 1, 3.5, spawned, resident=500),
        ms.Descendant(15, 2, 4.0, "", resident=900, footprint=800),
        ms.Descendant(16, 1, 12.0, spawned, resident=40),
        ms.Descendant(17, 1, 13.0, "git rev-parse --short HEAD", resident=6, footprint=5),
    ]
    records = [
        {"unit": "corpus", "width": 2, "worker_peak_rss_bytes": {"w0": 1}, "controller_peak_rss_bytes": 9},
        {
            "unit": "corpus",
            "width": 2,
            "worker_peak_rss_bytes": {"w0": 310, "w1": 520},
            "controller_peak_rss_bytes": 900,
            "worker_reading": "footprint",
        },
    ]
    pools = ms.corpus_pools(records, found, marks)
    assert pools["signature"]["resident"] == [100] and pools["signature"]["width"] is None
    assert pools["units"] == {
        "width": 2,
        "reading": "footprint",
        "peaks": [310, 520],
        "peak_median": 415,
        "peak_top": 520,
        "controller": 900,
        "resident": [300, 500],
        "resident_median": 400,
        "resident_top": 500,
    }
    assert pools["write"]["resident"] == [40] and pools["write"]["peaks"] == []
    assert ms.subprocesses(found) == {"count": 2, "resident_top": 900, "footprint_top": 800}


def test_the_corpus_report_fits_against_units_and_rows_and_states_the_doubled_load_against_the_single():
    """The cut lines' figures are fitted against their units and the load's and the plan's footprints against their rows, and the ×2 load line's figures are stated as ratios to the ×1 line's, at the peak and at each mark both reached."""

    def marks(scale, last):
        labels = ms.CORPUS_MARKS[: ms.CORPUS_MARKS.index(last) + 1]
        return [{"label": label, "footprint": scale} for label in labels]

    def cut(units, rows):
        return {
            "step": "corpus",
            "units": units,
            "rows": rows,
            "peak_footprint": units * units,
            "peak_resident": units,
            "cpu_s": float(units),
            "pools": {"units": {"peak_top": 7}, "write": {"peak_top": None}},
            "fragments": {"arena_gb": units / 10},
            "sidecar_lines_mb": [units / 100, units / 100],
            "marks": marks(rows * rows * rows, "unit store written"),
        }

    lines = [
        cut(100, 10),
        cut(400, 20),
        {"step": "corpus-load", "scale": 1, "rows": 5, "peak_footprint": 10, "marks": marks(4, ms.PLAN_MARK)},
        {
            "step": "corpus-load",
            "scale": 2,
            "rows": 10,
            "peak_footprint": 19,
            "marks": marks(8, ms.PLAN_MARK),
        },
    ]
    report = ms.corpus_report(lines)
    assert report[0] == "corpus fit over 2 cuts (units 100..400)"
    fits = {text.split(" ~ ")[0].strip(): text.split(" ~ ")[1] for text in report[1:10]}
    assert fits == {
        "peak footprint": "units^2.00",
        "peak resident": "units^1.00",
        "cpu": "units^1.00",
        "units worker top": "units^0.00",
        "write worker top": "units^n/a",
        "arena": "units^1.00",
        "sidecar lines": "units^1.00",
        "compacted footprint": "rows^3.00",
        "plan footprint": "rows^3.00",
    }
    assert report[10] == "load-x2 over load-x1 (10 rows over 5)"
    ratios = {text.rsplit(" ", 1)[0].strip(): text.rsplit(" ", 1)[1] for text in report[11:]}
    reached = ms.CORPUS_MARKS[: ms.CORPUS_MARKS.index(ms.PLAN_MARK) + 1]
    assert ratios == {"peak footprint": "1.90", **{f"{label} footprint": "2.00" for label in reached}}
    assert ms.corpus_report(lines[:1] + lines[2:3]) == ["the corpus fit needs two cuts; this run has 1"]
