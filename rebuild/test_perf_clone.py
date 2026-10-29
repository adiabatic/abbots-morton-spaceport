import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rebuild.review import store_lock
from rebuild.tools import cycle_paths, perf_clone

SENIOR = "site/AbbotsMortonSpaceportSansSenior-Regular.otf"
READY_LOG = "Retention:\nREADY - adjudicate at http://localhost:7294/#view=queue\n\nCycle complete.\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _clone(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    clone = main / "var" / "perf" / "shared"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    _write(
        clone / ".gitignore",
        "rebuild/out/\nvar/\ntmp/\n/site/*.otf\n/verdicts-*.json\n/verdicts-journal.ndjson\n",
    )
    return main, clone


def _warm_state(clone: Path) -> None:
    _write(clone / "rebuild/out/m1/windows-default.tsv.gz", "warm tables")
    _write(clone / "rebuild/out/review/manifest.json", "{}")
    _write(clone / perf_clone.TIMINGS_JOURNAL, '{"kind": "run", "run": "a"}\n')
    _write(clone / "var/cycle/journal-scan.json", '{"inode": 1}')
    _write(clone / "var/build-logs/run-a/terminal.log", READY_LOG)
    os.symlink("run-a", clone / "var/build-logs/latest")
    _write(clone / "tmp/complaints-data.json", "{}")
    _write(clone / SENIOR, "the font")
    _write(clone / "verdicts-autosave.json", "the store")
    _write(clone / "verdicts-autosave-20261001T000000Z.json", "an old stash")
    _write(clone / "verdicts-journal.ndjson", "base\nset\n")


def _quiet(main: Path, clone: Path | None, *, driver_pid: int | None = None) -> dict:
    return {
        "at": 0.0,
        "pmset": " powermode            0\n",
        "therm": "Note: No thermal warning level has been recorded\n",
        "batt": "Now drawing from 'AC Power'\n",
        "pressure": "1\n",
        "vm_stat": "",
        "swapusage": "",
        "pass_lock": None,
        "standing_daemon": False,
        "ps": "",
        "exclude_pid": 1,
        "main_journal_size": 0,
        "load_1m": 1.0,
        "server": "foreign",
        "disk_free": 500_000_000_000,
        "toolchain": {"hyperfine": "hyperfine 2.0.0", "rustc": "rustc 1.99.1"},
        "os_build": "26B123",
    }


def test_a_restore_returns_the_snapshot_and_keeps_the_timings_journal(tmp_path):
    _, clone = _clone(tmp_path)
    _commit(clone, {"tracked.txt": "x\n"}, "start")
    _warm_state(clone)
    snap = tmp_path / "main" / "var" / "perf" / "shared.snap" / "start"
    perf_clone.take_snapshot(clone, snap, ref="HEAD")
    journal = clone / "verdicts-journal.ndjson"
    inode = journal.stat().st_ino

    _write(clone / "rebuild/out/m1/windows-default.tsv.gz", "post-edit tables")
    _write(clone / "rebuild/out/m1/memo-default.tsv.gz", "a memo the run wrote")
    with (clone / perf_clone.TIMINGS_JOURNAL).open("a") as handle:
        handle.write('{"kind": "run", "run": "b"}\n')
    _write(clone / "var/cycle/journal-scan.json", '{"inode": 2}')
    _write(clone / "tmp/complaints-data.json", '{"groups": []}')
    _write(clone / SENIOR, "a rebuilt font")
    _write(clone / "verdicts-autosave.json", "the landed store")
    _write(clone / "verdicts-autosave-20261002T000000Z.json", "the run's stash")
    with journal.open("a") as handle:
        handle.write("land\n")

    assert perf_clone.restore_snapshot(clone, snap) == {"journal": "truncated", "journal_rescan": False}
    assert (clone / "rebuild/out/m1/windows-default.tsv.gz").read_text() == "warm tables"
    assert not (clone / "rebuild/out/m1/memo-default.tsv.gz").exists()
    assert (clone / perf_clone.TIMINGS_JOURNAL).read_text().count('"kind": "run"') == 2
    assert (clone / "var/cycle/journal-scan.json").read_text() == '{"inode": 1}'
    assert os.readlink(clone / "var/build-logs/latest") == "run-a"
    assert (clone / "tmp/complaints-data.json").read_text() == "{}"
    assert (clone / SENIOR).read_text() == "the font"
    assert (clone / "verdicts-autosave.json").read_text() == "the store"
    assert sorted(path.name for path in clone.glob(perf_clone.STASH_PATTERN)) == [
        "verdicts-autosave-20261001T000000Z.json"
    ]
    assert journal.read_text() == "base\nset\n" and journal.stat().st_ino == inode

    journal.write_text("another lineage\n")
    assert perf_clone.restore_snapshot(clone, snap)["journal"] == "rewritten"
    assert journal.read_text() == "base\nset\n" and journal.stat().st_ino == inode

    journal.unlink()
    journal.write_text("a replaced file\n")
    assert perf_clone.restore_snapshot(clone, snap) == {"journal": "copied", "journal_rescan": True}
    assert journal.read_text() == "base\nset\n"


def test_conclude_harvests_the_run_it_follows(tmp_path):
    main, clone = _clone(tmp_path)
    _commit(clone, {"tracked.txt": "x\n"}, "start")
    _warm_state(clone)
    record = tmp_path / "record"
    run_dir = record / "runs" / "0"
    run_dir.mkdir(parents=True)
    offset = perf_clone.journal_size(clone)
    stamp = {"k": 0, "latest_log": "run-a", "journal_offset": offset, "probes": _quiet(main, clone)}
    (run_dir / "stamp.json").write_text(json.dumps(stamp))

    _write(clone / "var/build-logs/run-b/terminal.log", READY_LOG)
    _write(clone / "var/build-logs/run-b/01-run_m1.log", "step output")
    os.unlink(clone / "var/build-logs/latest")
    os.symlink("run-b", clone / "var/build-logs/latest")
    new_lines = [
        {"kind": "step", "run": "b", "name": "verdict-update"},
        {"kind": "run", "run": "b", "exit": "ok"},
    ]
    with (clone / perf_clone.TIMINGS_JOURNAL).open("a") as handle:
        handle.write("".join(json.dumps(line) + "\n" for line in new_lines))

    assert perf_clone.conclude(clone, record, main=main, collect=_quiet, now=lambda: 5.0) == 0
    assert (run_dir / "logs" / "terminal.log").read_text() == READY_LOG
    assert (run_dir / "logs" / "01-run_m1.log").read_text() == "step output"
    assert [json.loads(line) for line in (run_dir / "journal.ndjson").read_text().splitlines()] == new_lines
    concluded = json.loads((run_dir / "stamp.json").read_text())
    assert concluded["classification"] == "ready" and concluded["log"] == "run-b"
    assert concluded["disturbed"] is None

    second = record / "runs" / "1"
    second.mkdir()
    unchanged = {**stamp, "k": 1, "latest_log": "run-b", "journal_offset": perf_clone.journal_size(clone)}
    (second / "stamp.json").write_text(json.dumps(unchanged))
    assert perf_clone.conclude(clone, record, main=main, collect=_quiet) == 1
    assert not (second / "logs").exists()
    assert json.loads((second / "stamp.json").read_text())["classification"] == "harness"


def test_a_clone_outside_var_perf_or_the_checkout_itself_is_refused(tmp_path):
    main = tmp_path / "main"
    (main / "var" / "perf" / "shared").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    assert perf_clone.check_clone_path(main / "var/perf/shared", main) == (main / "var/perf/shared").resolve()
    for path in (main, main / "var/perf", tmp_path / "elsewhere", main / "var/perf/../../elsewhere"):
        with pytest.raises(perf_clone.PerfRefused):
            perf_clone.check_clone_path(path, main)
    record = tmp_path / "record"
    assert perf_clone.prepare(main, tmp_path / "snap", "HEAD", None, record, main=main, collect=_quiet) == 2
    assert "not a scratch clone" in json.loads((record / "refused.json").read_text())["reason"]


def test_prepare_waits_for_the_clones_pass_lock_and_applies_the_edit_on_the_start_ref(tmp_path):
    main, clone = _clone(tmp_path)
    start = _commit(clone, {"a.txt": "start\n"}, "start")
    _warm_state(clone)
    snap = main / "var" / "perf" / "shared.snap" / "start"
    perf_clone.take_snapshot(clone, snap, ref=start)
    _commit(clone, {"a.txt": "elsewhere\n"}, "a later commit")
    patch = tmp_path / "edit.patch"
    patch.write_text("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-start\n+edited\n")
    record = tmp_path / "record"

    lock = contextlib.ExitStack()
    lock.enter_context(store_lock.hold_flock(clone / perf_clone.PASS_LOCK))
    seen_while_waiting: list[str] = []

    def sleep(_seconds: float) -> None:
        seen_while_waiting.append((clone / "a.txt").read_text())
        lock.close()

    rc = perf_clone.prepare(
        clone, snap, start, patch, record, main=main, collect=_quiet, sleep=sleep, now=lambda: 7.0
    )
    assert rc == 0
    assert seen_while_waiting == ["elsewhere\n"]
    assert _git(clone, "rev-parse", "HEAD") == start
    assert (clone / "a.txt").read_text() == "edited\n"
    stamp = json.loads((record / "runs" / "0" / "stamp.json").read_text())
    assert stamp["prepared_at"] == 7.0 and stamp["waited_s"] > 0 and stamp["latest_log"] == "run-a"


def _all_skip(**plan) -> dict:
    return {
        "kind": "run",
        "plan": {
            "skip_run_m1": True,
            "rerun_gates_only": False,
            "skip_corpus": True,
            "skip_verdict_update": True,
            **plan,
        },
        "gates": {
            "rebuild_contracts": {"skip": "proved"},
            "conform": {"skip": "proved"},
            "make_test": {"skip": "proved"},
        },
    }


def test_a_pass_that_skips_every_heavy_stage_is_settled():
    shape = {
        "plan": {
            "skip_run_m1": True,
            "rerun_gates_only": False,
            "skip_corpus": True,
            "skip_verdict_update": True,
        },
        "gates": {"rebuild_contracts": "proved", "conform": "proved", "make_test": "proved"},
    }

    def result(classification: str, line: dict | None) -> perf_clone.PassResult:
        return perf_clone.PassResult(classification, line, "", 0, 1.0)

    assert perf_clone.is_settled(result("ready", _all_skip()), shape)
    assert not perf_clone.is_settled(result("ready", _all_skip(skip_corpus=False)), shape)
    assert not perf_clone.is_settled(result("failed", _all_skip()), shape)
    assert not perf_clone.is_settled(result("ready", None), shape)


def test_a_clone_pass_finds_a_pkill_that_stops_nothing_and_sends_no_reload(tmp_path):
    main = tmp_path / "main"
    perf_clone.install_shims(main)
    env = perf_clone.child_env(main)
    assert env["PATH"].split(os.pathsep)[0] == str(main / perf_clone.SHIM_DIR)
    assert env[cycle_paths.REVIEW_RELOAD_OFF_ENV] == "1"
    shim = main / perf_clone.SHIM_DIR / "pkill"
    found = subprocess.run(["sh", "-c", "command -v pkill"], env=env, capture_output=True, text=True)
    assert found.stdout.strip() == str(shim)
    stopped = subprocess.run([str(shim)], capture_output=True, text=True)
    assert stopped.returncode == 1 and "stops nothing" in stopped.stderr
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rebuild.tools import cycle_paths; print(cycle_paths.REVIEW_RELOAD_ENABLED)",
        ],
        env={**env, "PYTHONPATH": str(perf_clone.ROOT)},
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "False"


def test_a_refused_warm_pass_runs_again_and_a_failed_one_is_the_trees(tmp_path, monkeypatch):
    main, clone = _clone(tmp_path)
    start = _commit(clone, {"a.txt": "start\n"}, "start")
    shape = {"plan": {"skip_corpus": True}}
    settled = perf_clone.PassResult(
        "ready", {"kind": "run", "plan": {"skip_corpus": True}}, READY_LOG, 0, 1.0
    )

    def warm(*classifications: str, retries: int = 1) -> int:
        results = iter(
            [perf_clone.PassResult(name, None, "", 2, 1.0) for name in classifications] + [settled]
        )
        monkeypatch.setattr(perf_clone, "run_pass", lambda *_args, **_kwargs: next(results))
        waits: list[int] = []
        passes = perf_clone.warm(
            clone,
            start,
            settle_shape=shape,
            log_dir=tmp_path / "logs",
            label="warm",
            max_passes=2,
            before_pass=lambda: waits.append(1),
            main=main,
            retries=retries,
        )
        assert len(waits) == len(passes)
        return len(passes)

    assert warm("refused") == 2
    assert warm("interrupted", "ready") == 3
    with pytest.raises(perf_clone.PerfRefused) as refused:
        warm("refused", "refused")
    assert not isinstance(refused.value, perf_clone.WarmFailed)
    with pytest.raises(perf_clone.WarmFailed):
        warm("failed")
