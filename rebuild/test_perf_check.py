import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from rebuild.tools import perf_check as pc
from rebuild.tools.perf_clone import PerfRefused

TOOLCHAIN = ("rustc 1.99.1 (Homebrew)", "26B123")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def _run_line(started: str, finished: str, wall: float, *, exit: str = "ok", **plan) -> dict:
    return {
        "kind": "run",
        "exit": exit,
        "argv": ["rebuild/tools/artifact_cycle.py", "--stop-server"],
        "cpu_count": 18,
        "mem_total_bytes": 51539607552,
        "started_at": started,
        "finished_at": finished,
        "wall_s": wall,
        "plan": {"first_run": False, **plan},
        "gates": {},
    }


def _timed(k: int, time_s: float, lines: list[dict], prepared: str = "2026-10-28T10:00:00Z") -> pc.TimedRun:
    return pc.TimedRun(k=k, prepared_at=_epoch(prepared), time_s=time_s, lines=lines)


def _agreement(runs: list[pc.TimedRun]) -> pc.Agreement:
    return pc.journal_agreement(runs, cpu_count=18, mem_total_bytes=51539607552)


def test_every_candidate_run_faster_than_every_base_run_is_proven_faster():
    base = [100.0, 101.0, 102.0, 103.0, 104.0]
    faster = [90.0, 91.0, 92.0, 93.0, 99.9]

    def verdict(base_runs, candidate_runs, candidate_toolchain=TOOLCHAIN):
        return pc.classify(
            base_runs, candidate_runs, base_toolchain=TOOLCHAIN, candidate_toolchain=candidate_toolchain
        ).verdict

    assert verdict(base, faster) == "proven faster"
    assert verdict(base, [90.0, 91.0, 92.0, 93.0, 100.5]) == "within spread"
    assert verdict(faster, base) == "proven slower"
    assert verdict(base, faster, ("rustc 1.100.0 (Homebrew)", "26B123")) == "no verdict"
    with pytest.raises(ValueError):
        verdict(base[:4], faster[:4])


def test_a_run_is_classified_from_its_terminal_log_not_makes_exit():
    tail = "\n  ✓ blanks: 12\nREADY - adjudicate at http://localhost:7294/#view=queue\n\nCycle complete.\n"
    assert pc.classify_run("Retention:\n" + tail, 0) == "ready"
    assert pc.classify_run("Retention:\n" + tail, 2) == "harness"
    assert pc.classify_run("  ✗ server\nNOT READY\n\nCycle complete.\n", 0) == "failed"
    assert pc.classify_run("plan\nREFUSING TO RUN: something listens on 127.0.0.1:7294\n", 2) == "refused"
    assert pc.classify_run("table\n\nCYCLE FAILED:\n  - run_m1 exited 1\n", 2) == "failed"
    assert pc.classify_run("table\n\nCYCLE INTERRUPTED:\n  - SIGTERM\n", 2) == "interrupted"
    assert pc.classify_run("uv: error: no such file\n", 2) == "harness"


def test_each_timed_run_needs_exactly_one_ok_run_line_in_its_window():
    good = _run_line("2026-10-28T10:00:02Z", "2026-10-28T10:01:02Z", 59.6)
    assert _agreement([_timed(1, 62.0, [good])]).ok
    assert not _agreement([_timed(1, 62.0, [])]).ok
    assert not _agreement([_timed(1, 62.0, [good, dict(good)])]).ok
    failed = _run_line("2026-10-28T10:00:02Z", "2026-10-28T10:01:02Z", 59.6, exit="failed")
    assert not _agreement([_timed(1, 62.0, [failed])]).ok
    early = _run_line("2026-10-28T09:59:58Z", "2026-10-28T10:00:58Z", 59.6)
    assert not _agreement([_timed(1, 62.0, [early])]).ok
    step = {"kind": "step", "name": "run_m1", "elapsed_s": 30.0}
    assert _agreement([_timed(1, 62.0, [step, good])]).ok


def test_a_negative_or_unstable_gap_disagrees_with_the_journal():
    def run(k: int, time_s: float, wall: float) -> pc.TimedRun:
        return _timed(
            k, time_s, [_run_line("2026-10-28T10:00:01Z", f"2026-10-28T10:01:{int(wall) - 59:02d}Z", wall)]
        )

    negative = _agreement([run(1, 59.0, 60.0)])
    assert not negative.ok and any("shorter" in problem for problem in negative.problems)
    unstable = _agreement([run(1, 62.0, 60.0), run(2, 65.0, 60.0)])
    assert not unstable.ok and any("GAP_RANGE_S" in problem for problem in unstable.problems)
    stable = _agreement([run(1, 62.0, 60.0), run(2, 63.0, 60.0)])
    assert stable.ok and stable.gaps == [2.0, 3.0]


def test_a_changed_stage_flag_fails_the_shape_and_a_changed_width_is_only_reported():
    line = _run_line("t", "t", 1.0, skip_run_m1=False, kernel_threads=6, verdicts="/a/verdicts-autosave.json")
    wider = _run_line(
        "t", "t", 1.0, skip_run_m1=False, kernel_threads=8, verdicts="/b/verdicts-autosave.json"
    )
    skipping = _run_line(
        "t", "t", 1.0, skip_run_m1=True, kernel_threads=6, verdicts="/a/verdicts-autosave.json"
    )
    compared, reported = pc.compare_shapes(pc.pass_shape(line), pc.pass_shape(wider))
    assert compared == [] and reported == ["kernel_threads: 6 → 8"]
    compared, reported = pc.compare_shapes(pc.pass_shape(line), pc.pass_shape(skipping))
    assert compared == ["plan.skip_run_m1: false → true"] and reported == []
    assert pc.pass_shape(_run_line("t", "t", 1.0, brand_new_flag=True))["new"] == ["brand_new_flag"]


def _quiet_probes(**changes) -> dict:
    raw = {
        "at": 1.0,
        "pmset": "System-wide power settings:\n powermode            0\n womp                 1\n",
        "therm": "Note: No thermal warning level has been recorded\nNote: No performance warning level has been recorded\nNote: No CPU power status has been recorded\n",
        "batt": "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; AC attached; not charging present: true\n",
        "pressure": "1\n",
        "vm_stat": "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages occupied by compressor:                 236789.\n",
        "swapusage": "total = 1024.00M  used = 0.25M  free = 1023.75M  (encrypted)\n",
        "pass_lock": None,
        "standing_daemon": False,
        "ps": "    1     0 /sbin/launchd\n  500     1 uv run python -m rebuild.tools.perf_check\n  501   500 hyperfine --shell=none\n  502   501 /repo/.venv/bin/python3 /repo/.venv/bin/pytest rebuild/\n  600     1 /opt/tools/.cargo/bin/rust-analyzer\n  601     1 /bin/zsh -c source /opt/shell.zsh && grep -n artifact_cycle rebuild/tools/deep_replay.py\n  602     1 nvim rebuild/tools/artifact_cycle.py\n  603     1 /usr/bin/python3 -c import rebuild.tools.artifact_cycle\n",
        "exclude_pid": 500,
        "main_journal_size": 100,
        "load_1m": 4.5,
        "server": "foreign",
        "disk_free": 500_000_000_000,
        "toolchain": {
            "hyperfine": "hyperfine 2.0.0",
            "rustc": "rustc 1.99.1",
            "uv": "uv 0.13.6",
            "git": "git version 2.56.0",
            "typst": "typst 0.15.1",
        },
        "os_build": "26B123",
    }
    raw.update(changes)
    return raw


def _actions(raw: dict, phase: str = "prepare", before: dict | None = None) -> set[tuple[str, str]]:
    summary = pc.summarize_probes(raw)
    return {(item.check, item.action) for item in pc.probe_outcomes(summary, phase=phase, before=before)}


def test_each_probe_refuses_waits_or_marks_a_run_disturbed():
    assert _actions(_quiet_probes()) == set()
    low_power = _quiet_probes(pmset=" powermode            1\n")
    assert _actions(low_power) == {("powermode", "wait")}
    assert _actions(low_power, "conclude") == {("powermode", "disturbed")}
    hot = _quiet_probes(therm="Note: No thermal warning level has been recorded\nCPU_Speed_Limit = 80\n")
    assert _actions(hot) == {("therm", "wait")}
    assert _actions(_quiet_probes(batt="Now drawing from 'Battery Power'\n")) == {("ac_power", "wait")}
    assert _actions(_quiet_probes(pressure="2\n"), "conclude") == {("pressure", "disturbed")}
    busy = _quiet_probes(
        ps=_quiet_probes()["ps"]
        + "  700     1 /repo/.venv/bin/python3 /repo/.venv/bin/pytest rebuild/ -n auto\n"
        + "  701     1 /opt/uv/python3.14 rebuild/tools/artifact_cycle.py --stop-server\n"
        + "  702     1 /opt/uv/python3.14 -u -m rebuild.pipeline.run_m1 --conform-only\n"
        + "  703     1 /opt/tools/.cargo/bin/cargo check --workspace\n"
    )
    assert _actions(busy) == {("heavy", "wait")}
    assert pc.summarize_probes(busy)["heavy"] == [
        "cargo",
        "pytest",
        "rebuild.pipeline.run_m1",
        "rebuild.tools.artifact_cycle",
    ]
    assert _actions(_quiet_probes(ps=None)) == {("unreadable", "wait")}
    assert _actions(_quiet_probes(therm=None), "conclude") == {("unreadable", "disturbed")}
    assert _actions(_quiet_probes(pass_lock="4242")) == {("heavy", "wait")}
    assert _actions(_quiet_probes(standing_daemon=True), "conclude") == {("heavy", "disturbed")}
    assert _actions(_quiet_probes(server="legacy")) == {("server", "wait")}
    assert _actions(_quiet_probes(server="legacy"), "conclude") == set()
    assert _actions(_quiet_probes(disk_free=10_000_000_000)) == {("disk", "refuse")}
    old = _quiet_probes(toolchain={**_quiet_probes()["toolchain"], "hyperfine": "hyperfine 1.18.0"})
    assert _actions(old) == {("toolchain", "refuse")}
    missing = _quiet_probes(toolchain={**_quiet_probes()["toolchain"], "typst": None})
    assert _actions(missing) == {("toolchain", "refuse")}
    grew = pc.summarize_probes(_quiet_probes(main_journal_size=200))
    before = pc.summarize_probes(_quiet_probes())
    assert {(i.check, i.action) for i in pc.probe_outcomes(grew, phase="conclude", before=before)} == {
        ("heavy", "disturbed")
    }


def test_a_patch_that_does_not_reverse_at_the_base_makes_its_scenario_not_applicable(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"a.txt": "1\n", "b.txt": "x\n"}, "start")
    _commit(repo, {"a.txt": "2\n"}, "edit a")
    first = tmp_path / "first.patch"
    first.write_bytes(
        subprocess.run(["git", "-C", str(repo), "diff", "HEAD^", "HEAD"], capture_output=True).stdout
    )
    _commit(repo, {"b.txt": "y\n"}, "edit b")
    second = tmp_path / "second.patch"
    second.write_bytes(
        subprocess.run(["git", "-C", str(repo), "diff", "HEAD^", "HEAD"], capture_output=True).stdout
    )
    _commit(repo, {"b.txt": "z\n"}, "edit b again")
    index = tmp_path / "check.index"
    check = pc.patches_apply_at(repo, "HEAD", {"first": first, "second": second}, index)
    assert pc.revert_status(check, "first") is None
    reason = pc.revert_status(check, "second")
    assert reason is not None and reason.startswith("not applicable at B: re-aim")
    assert not index.exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_scenario_2_takes_a_candidate_only_when_its_files_are_unchanged_since_its_base(tmp_path):
    repo = _repo(tmp_path)
    letter_base = _commit(repo, {"src.py": "1\n", "other.py": "1\n", "doc/notes.md": "a\n"}, "letter base")
    base = _commit(
        repo, {"other.py": "2\n", "doc/notes.md": "b\n", "rebuild/tools/perf_check.py": "x = 1\n"}, "later"
    )

    def status(paths: list[str], **candidate) -> str | None:
        return pc.forward_status(repo, paths, letter_base=letter_base, base=base, **candidate)

    assert status(["src.py"]) is None
    assert status(["src.py", "other.py"]) == f"not measured: other.py changed since {letter_base[:8]}"
    harness_and_docs = ["rebuild/tools/perf_check.py", "rebuild/perf/scenarios.yaml", "doc/notes.md"]
    assert status(harness_and_docs) is None
    _commit(
        repo,
        {"src.py": "2\n", "doc/notes.md": "c\n", "rebuild/tools/perf_check.py": "x = 2\n"},
        "a candidate that also edits its docs and the harness",
    )
    patch = tmp_path / "candidate.patch"
    patch.write_text(_git(repo, "diff", "HEAD^", "HEAD") + "\n")
    index = tmp_path / "forward.index"
    paths = ["src.py", "doc/notes.md", "rebuild/tools/perf_check.py"]
    assert status(paths, candidate=patch, index=index) is None
    patch.write_text(patch.read_text().replace("-1\n+2", "-9\n+2"))
    reason = status(paths, candidate=patch, index=index)
    assert reason is not None and reason.startswith(
        f"not measured: the candidate does not apply at {letter_base[:8]}"
    )
    assert not index.exists()


def test_the_fingerprint_compare_names_every_difference_and_reports_stores_apart():
    base = {
        "tables": {"default": "t1", "ss03": "t2"},
        "fonts": {"rebuild/out/m1/M1.otf": "f1"},
        "corpus": {"digest": "c", "files": {"manifest.json": "m", "units/u-1.json": "u1"}},
        "verdicts": "v1",
        "complaints": "k1",
        "stores": {"unit-cache.ndjson.gz": "s1", "ink-signatures.tsv.gz": "s2"},
        "review_facts_pins": "p1",
    }
    candidate = {
        **base,
        "tables": {"default": "t9"},
        "corpus": {"digest": "d", "files": {"manifest.json": "m", "units/u-1.json": "u9"}},
        "verdicts": "v9",
        "stores": {"unit-cache.ndjson.gz": "s9", "ink-signatures.tsv.gz": "s2"},
    }
    diff = pc.compare_fingerprints(base, candidate)
    assert diff.differences == [
        "tables.default differs",
        "tables.ss03: missing on the candidate side",
        "corpus.units/u-1.json differs",
        "verdicts",
    ]
    assert diff.stores == ["stores.unit-cache.ndjson.gz: an intermediate store differs"]
    other_seed = pc.compare_fingerprints(base, candidate, same_seed=False)
    assert "verdicts" not in other_seed.differences
    assert other_seed.not_comparable == ["verdicts: not comparable: different seeds"]


def test_the_seed_scrub_drops_only_fills_of_rules_the_composite_lacks():
    at_base = "rules:\n  - id: old-rule\n  - id: new-rule\n  - id: widened\n    pivot: [qsIt, qsZoo]\n"
    composite = "rules:\n  - id: old-rule\n  - id: widened\n    pivot: [qsIt]\n"
    assert pc.changed_rule_ids(at_base, composite) == {"new-rule", "widened"}
    records = [
        ("u-1", "[standing: new-rule] a fill the edit's rule made"),
        ("u-2", "[standing: old-rule + new-rule] a; b"),
        ("u-3", "[duplicate-fill from u-9] [standing: new-rule] copied from its source"),
        ("u-4", "[standing: retired-rule] a rule retired before B"),
        ("u-5", "[standing: old-rule] a fill the composite's rules make too"),
        ("u-6", "human prose that quotes [standing: new-rule] later on"),
        ("u-7", ""),
        ("u-8", "[standing: widened] a fill the rule's wider pivot made"),
        ("u-10", "[duplicate-fill from u-3] [duplicate-fill from u-9] copied from its source"),
        ("u-11", "[duplicate-fill from u-10] [duplicate-fill from u-3] copied from its source"),
        ("u-12", "[duplicate-fill from u-5] [standing: old-rule] copied from a kept fill"),
    ]
    store = {
        "format": "ams-review-verdicts/1",
        "verdicts": [{"unit": unit, "verdict": "approve", "note": note, "at": "t"} for unit, note in records],
    }
    scrubbed, dropped = pc.scrub_seed(store, pc.changed_rule_ids(at_base, composite))
    assert [record["unit"] for record in scrubbed["verdicts"]] == ["u-4", "u-5", "u-6", "u-7", "u-12"]
    assert dropped == 6
    assert scrubbed["format"] == "ams-review-verdicts/1"


def test_a_seed_is_restored_no_earlier_than_the_journal_starts(tmp_path):
    journal = tmp_path / "verdicts-journal.ndjson"
    assert pc.seed_as_of("2026-10-08T20:55:48Z", journal) == "2026-10-08T20:55:48Z"
    journal.write_text(
        '{"kind": "event", "source": "land", "at": "2026-10-10T01:23:48Z", "base": true, "sets": 1}\n'
        '{"kind": "set", "unit": "u-1", "verdict": "approve", "at": "2026-10-09T00:00:00Z"}\n'
    )
    assert pc.seed_as_of("2026-10-08T20:55:48Z", journal) == "2026-10-10T01:23:48Z"
    assert pc.seed_as_of("2026-10-29T09:52:19Z", journal) == "2026-10-29T09:52:19Z"
    journal.write_text('{"kind": "event", "at": "not a stamp"}\n')
    assert pc.seed_as_of("2026-10-08T20:55:48Z", journal) == "2026-10-08T20:55:48Z"


def test_a_forward_scenario_needs_its_kept_seed_or_its_kept_root(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "PERF_DIR", tmp_path / "perf")
    monkeypatch.setattr(pc, "KEEP_DIR", tmp_path / "keep")
    data = b"{}"
    sha256 = hashlib.sha256(data).hexdigest()
    scenarios = [
        pc.Scenario(1, "tree", "", "none", None, None, None, None, (), None, {}),
        pc.Scenario(2, "letter", "", "forward", "abcdef12", {"sha256": sha256}, None, None, (), None, {}),
    ]
    args = argparse.Namespace(scenario=None, runs=pc.MIN_RUNS, faults="none", seed=None)
    check = pc.Check(args, scenarios, pc.Candidate("baseline", "b1", None, (), "baseline"))
    with pytest.raises(PerfRefused, match="kept seed"):
        check.require_kept_seeds()
    kept = tmp_path / "keep" / "seeds" / f"{sha256}.json"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"another store")
    with pytest.raises(PerfRefused, match="does not match"):
        check.require_kept_seeds()
    kept.write_bytes(data)
    check.require_kept_seeds()
    kept.unlink()
    (tmp_path / "perf" / "abcdef12.snap" / "root").mkdir(parents=True)
    check.require_kept_seeds()


def test_write_patch_refuses_an_edit_that_does_not_resolve(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"a.txt": "1\n"}, "start")
    edit = _commit(repo, {"a.txt": "2\n"}, "the edit")
    data = repo / "perf"
    (data / "scenarios").mkdir(parents=True)
    patch = data / "scenarios" / "one.patch"
    patch.write_text("the checked-in patch\n")
    scenarios = data / "scenarios.yaml"

    def scenario(commit: str) -> pc.Scenario:
        scenarios.write_text(
            "format: ams-perf-scenarios/1\nscenarios:\n"
            f"  - name: one\n    form: revert\n    edit: {{commit: '{commit}', paths: [a.txt]}}\n"
            "    patch: scenarios/one.patch\n    shape: {}\n"
        )
        return pc.load_scenarios(scenarios)[0]

    with pytest.raises(PerfRefused, match="does not resolve"):
        pc.write_patch(repo, scenario("0" * 40))
    assert patch.read_text() == "the checked-in patch\n"
    pc.write_patch(repo, scenario(edit))
    assert "+2" in patch.read_text()


def _batch(side: str, times: list[float]) -> pc.Batch:
    probes = {"toolchain": {"rustc": TOOLCHAIN[0]}, "os_build": TOOLCHAIN[1]}
    stamps: list[dict] = [{"k": 0, "prepared_at": _epoch("2026-10-28T09:59:00Z"), "probes": probes}]
    lines: list[list[dict]] = [[]]
    for k, time_s in enumerate(times, start=1):
        stamps.append({"k": k, "prepared_at": _epoch(f"2026-10-28T10:{k:02d}:00Z"), "probes": probes})
        wall = time_s - 2.0
        finished = f"2026-10-28T10:{k:02d}:{1 + int(wall):02d}Z"
        lines.append([_run_line(f"2026-10-28T10:{k:02d}:01Z", finished, wall)])
    toolchain = pc.toolchain_key(probes)
    return pc.Batch(side, Path(side), "ok", times=times, stamps=stamps, lines=lines, toolchain=toolchain)


def _check(candidate_patch: bytes | None) -> pc.Check:
    scenario = pc.Scenario(1, "nothing-changed", "", "none", None, None, None, None, (), None, {})
    candidate = pc.Candidate("c-1" if candidate_patch else "baseline", "b" * 40, candidate_patch, (), "t")
    args = argparse.Namespace(scenario=None, runs=pc.MIN_RUNS, faults="auto", seed=None)
    check = pc.Check(args, [scenario], candidate)
    check.cpu_count, check.mem_total_bytes = 18, 51539607552
    return check


def test_a_baseline_takes_every_fault_row_unless_its_rows_are_named():
    assert _check(None).fault_rows() == "all"
    assert _check(b"a candidate").fault_rows() == "auto"
    named = _check(None)
    named.args.faults = "05"
    assert named.fault_rows() == "05"


def test_a_separation_that_fails_the_check_is_confirmed_and_an_aa_pair_needs_equal_stores():
    fingerprint = {"tables": {"default": "t"}, "stores": {"unit-cache.ndjson.gz": "s1"}}
    other_store = {**fingerprint, "stores": {"unit-cache.ndjson.gz": "s2"}}
    fast, slow, overlapping = [40.0, 41, 42, 43, 44], [50.0, 51, 52, 53, 54], [41.5, 42.5, 43.5, 44.5, 45.5]

    def judge(check: pc.Check, base: list[float], other: list[float], other_print: dict) -> tuple[str, bool]:
        scenario = check.scenarios[0]
        candidate_id = check.sides[1].id
        batches = {"base": _batch("base", base), candidate_id: _batch(candidate_id, other)}
        prints = {"base": fingerprint, candidate_id: other_print}
        result, confirm = check.judge(scenario, batches, prints)
        return result["status"], confirm

    aa, candidate = _check(None), _check(b"a candidate")
    assert judge(aa, fast, slow, fingerprint) == ("failed", True)
    assert judge(aa, slow, fast, fingerprint) == ("failed", True)
    assert judge(aa, fast, overlapping, other_store) == ("failed", False)
    assert judge(aa, fast, overlapping, fingerprint) == ("measured", False)
    assert judge(candidate, fast, slow, fingerprint) == ("failed", True)
    assert judge(candidate, slow, fast, other_store) == ("measured", False)
    assert judge(candidate, fast, slow, {**fingerprint, "verdicts": "v"}) == ("failed", False)


def test_each_passing_scenario_writes_its_row_unless_it_differs_from_another_machines_at_the_same_base(
    tmp_path, monkeypatch
):
    fingerprint = {"tables": {"default": "t"}, "verdicts": "v", "stores": {"unit-cache.ndjson.gz": "s"}}
    mini = {"cpu_count": 12, "mem_total_bytes": 51539607552, "base": "b1", "seed_sha256": "seed"}
    document = {"machines": [{**mini, "fingerprint": fingerprint}]}
    row = {"cpu_count": 18, "mem_total_bytes": 51539607552, "base": "b1", "seed_sha256": "seed"}
    assert pc.machine_differences(document, {**row, "fingerprint": fingerprint}) == []
    assert pc.machine_differences(document, {**row, "base": "b2", "fingerprint": {}}) == []
    drifted = {**fingerprint, "tables": {"default": "u"}, "verdicts": "w"}
    assert pc.machine_differences(document, {**row, "fingerprint": drifted}) == [
        "tables.default differs (base side: the cpus=12 ram=51.54GB row)",
        "verdicts (base side: the cpus=12 ram=51.54GB row)",
    ]
    other_seed = {**row, "seed_sha256": "another", "fingerprint": {**fingerprint, "verdicts": "w"}}
    assert pc.machine_differences(document, other_seed) == []
    merged = pc.merge_machine_row(document, {**row, "fingerprint": fingerprint})
    assert [entry["cpu_count"] for entry in merged["machines"]] == [12, 18]
    assert (
        pc.merge_machine_row(merged, {**row, "fingerprint": drifted})["machines"][1]["fingerprint"] == drifted
    )

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    kept = json.dumps({"format": pc.BASELINE_FORMAT, "scenario": "drifts", "patch_sha256": None, **document})
    (baselines / "drifts.json").write_text(kept)
    monkeypatch.setattr(pc, "PERF_DATA", tmp_path)
    monkeypatch.setattr(pc, "disk_use", lambda _perf_dir: {})
    names = ("passes", "fails", "drifts")
    scenarios = [
        pc.Scenario(number, name, "", "none", None, None, None, None, (), None, {})
        for number, name in enumerate(names, start=1)
    ]
    args = argparse.Namespace(scenario=None, runs=pc.MIN_RUNS, faults="none", seed=None)
    check = pc.Check(args, scenarios, pc.Candidate("baseline", "b1", None, (), "baseline"))
    check.record = tmp_path / "record"
    check.record.mkdir()
    prints = {"passes": fingerprint, "drifts": drifted}
    monkeypatch.setattr(
        check, "baseline_row", lambda scenario, _result: {**row, "fingerprint": prints[scenario.name]}
    )
    monkeypatch.setattr(
        check,
        "measure_pair",
        lambda scenario, *_: (
            {
                "name": scenario.name,
                "number": scenario.number,
                "status": "failed" if scenario.name == "fails" else "measured",
            },
            False,
        ),
    )
    for scenario in scenarios:
        check.measure(scenario, tmp_path, {})
    assert sorted(path.name for path in baselines.iterdir()) == ["drifts.json", "passes.json"]
    assert [
        entry["cpu_count"] for entry in json.loads((baselines / "passes.json").read_text())["machines"]
    ] == [18]
    assert (baselines / "drifts.json").read_text() == kept
    check.results.append({"name": "check", "status": "stopped", "detail": "stopped by signal 15"})
    assert check.report() == 1
    report = (check.record / "report.txt").read_text()
    assert "baselines: written for passes\n" in report
    assert "differs from another machine: drifts: verdicts (base side: the cpus=12 ram=51.54GB row)" in report
