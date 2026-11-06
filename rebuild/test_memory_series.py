"""Tests for the memory series (`rebuild/tools/memory_series.py`). The steps build the mini fixture narrowed to ·Tea, ·May and ·It, a build of a fraction of a second, so they run the real crate; the meter, the machine reading and the waits are replaced where a test reads their arithmetic."""

import hashlib
import json
import os
import subprocess
from dataclasses import asdict

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, kernel_io, spec_load
from rebuild.review import store_lock
from rebuild.tools import memory_series as ms
from rebuild.tools import scaling_sweep, write_meter
from rebuild.tools.write_meter import Member, WriteReading

CONFIGS = list(conform.SETTLEMENT_CONFIGS)
FLAGS = ms.Flags(threads=2, overlap_memo_writes=True, scratch_beside_default=1)
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


def test_a_step_reads_through_fifos_what_a_build_on_files_writes_and_leaves_only_null_links(
    monkeypatch, tmp_path
):
    """A step's windows counts, memo byte counts and SHA-256s and digests are what the same build writes as files, counted and hashed after it exits, and while the crate runs its directory holds the FIFOs and the settlement and joins links to the null device and nothing else, so the build writes no file. The directory is gone once the step returns."""
    spec_path = _spec_path(tmp_path)
    files = tmp_path / "files"
    arguments = ms.step_arguments(kernel_exec.BINARY, spec_path, files, CONFIGS, STAMP, FLAGS)
    done = subprocess.run(arguments, capture_output=True, check=True)
    digests = {answer["config"]: answer["digest"] for answer in map(json.loads, done.stdout.splitlines())}
    windows = {}
    memos = {}
    for config in CONFIGS:
        payload = kernel_exec.windows_payload(files, config)
        with payload.open("rb") as source:
            windows[config] = list(scaling_sweep.counts(scaling_sweep.scan(source), payload))
        memo = (files / f"memo-{config}.tsv").read_bytes()
        memos[config] = {"bytes": len(memo), "sha256": hashlib.sha256(memo).hexdigest()}

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
    assert measured["memo"] == memos
    assert measured["digests"] == digests
    assert all(counts[0] > 0 for counts in windows.values()) and all(memo["bytes"] for memo in memos.values())
    expected = {}
    for config in CONFIGS:
        expected |= {f"settlement-{config}.tsv": os.devnull, f"joins-{config}.tsv": os.devnull}
        expected |= {f"windows-{config}.tsv": "fifo", f"memo-{config}.tsv": "fifo"}
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
