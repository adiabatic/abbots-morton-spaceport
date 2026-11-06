"""Tests for rebuild/tools/fault_kit.py: selection over a synthetic tree, the result classification, whole runs against synthetic git repos under tmp_path whose step is a stub script, the kit's tree made from a synthetic checkout, each row's alphabet against the rune store and the Manual pins, and the meter gate over synthetic readings."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rebuild.pipeline import conform, manual_pins, spec_load
from rebuild.review import store_lock
from rebuild.review.landing import clone_tree
from rebuild.tools import fault_kit, write_meter
from rebuild.tools.write_meter import Member, WriteReading
from rebuild.validation.pins import ReplayReport, collect_pin_runs

GATES = {
    "string-replay": {
        "modules": ["rebuild.pipeline.replay"],
        "crate": True,
        "files": ["rebuild/pipeline/run.py"],
    },
    "witness": {"modules": ["rebuild.pipeline.witness"], "crate": True, "files": []},
    "read-back": {"modules": ["rebuild.pipeline.readback"], "crate": False, "files": []},
    "conform": {"modules": ["rebuild.pipeline.conform"], "crate": True, "files": []},
}
MODULES = {
    "__init__.py": "",
    "run.py": "",
    "replay.py": "from rebuild.pipeline import settle\n",
    "settle.py": "",
    "witness.py": "from rebuild.pipeline import settle\n",
    "readback.py": "from rebuild.pipeline import pack\n",
    "pack.py": "",
    "conform.py": "from rebuild.pipeline import shaping\n",
    "shaping.py": "",
    "emit.py": "",
    "gpos.py": "",
}
STUB = """import os
import sys
from pathlib import Path

print("alphabet", os.environ.get("AMS_M1_RUNES"))
faulted = "FAULT = True" in Path("rebuild/pipeline/gate.py").read_text()
checked = "CHECKED = True" in Path("rebuild/pipeline/check.py").read_text()
out = Path("rebuild/out/m1")
out.mkdir(parents=True, exist_ok=True)
(out / "effect.txt").write_text("faulted\\n" if faulted else "clean\\n")
if faulted:
    (out / "faulted-only.txt").write_text("left by the faulted build\\n")
if faulted and checked:
    (out / "summary.json").write_text('{"divergence": "qsAh"}\\n')
    sys.exit("gate: the fault is caught")
"""


def _header_patch(path: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y\n"


def _step(gate: str, exit_status: int = 1) -> dict:
    return {"argv": ["true", gate], "gate": gate, "exit": exit_status}


def _manifest(directory: Path, rows: list[dict], patches: dict[str, str], gates: dict | None = None):
    (directory / "faults").mkdir(parents=True, exist_ok=True)
    for row_id, text in patches.items():
        (directory / "faults" / f"{row_id}.patch").write_text(text)
    data = {"format": fault_kit.FORMAT, "gates": gates if gates is not None else GATES, "rows": rows}
    return fault_kit.parse_manifest(data, directory)


def _selection_kit(tmp_path: Path):
    """A tree whose gates have small import closures, and three rows shaped like §10's: 01 a crate fault caught by the replay and the sweep with the witness unreached, 05 a Python fault caught by read-back and the sweep, 08 a Python fault only read-back catches."""
    tree = tmp_path / "tree"
    (tree / "rebuild" / "pipeline").mkdir(parents=True)
    for name, source in MODULES.items():
        (tree / "rebuild" / "pipeline" / name).write_text(source)

    def row(row_id: str, steps: list[dict], **extra) -> dict:
        return {
            "id": row_id,
            "section10": int(row_id),
            "slug": f"fault-{row_id}",
            "patch": f"faults/{row_id}.patch",
            "alphabet": ["qsAh"],
            "effect": ["rebuild/out/m1/effect.txt"],
            "steps": steps,
            **extra,
        }

    manifest = _manifest(
        tmp_path / "perf",
        [
            row("01", [_step("string-replay"), _step("conform")], unreached=["witness"]),
            row("05", [_step("read-back"), _step("conform")]),
            row("08", [_step("read-back")]),
        ],
        {
            "01": _header_patch("rebuild/kernel-rs/src/liveness.rs"),
            "05": _header_patch("rebuild/pipeline/emit.py"),
            "08": _header_patch("rebuild/pipeline/gpos.py"),
        },
    )
    return manifest, tree


def _selected(manifest, tree: Path, *changed: str) -> list[str]:
    return [row.id for row in fault_kit.select(manifest, "auto", frozenset(changed), tree).rows]


def test_a_change_inside_a_gates_import_closure_selects_only_its_rows(tmp_path):
    manifest, tree = _selection_kit(tmp_path)
    assert _selected(manifest, tree, "rebuild/pipeline/shaping.py") == ["01", "05"]
    assert _selected(manifest, tree, "rebuild/pipeline/pack.py") == ["05", "08"]


def test_a_crate_change_selects_only_crate_backed_gates_rows(tmp_path):
    manifest, tree = _selection_kit(tmp_path)
    assert _selected(manifest, tree, "rebuild/kernel-rs/src/engine.rs") == ["01", "05"]
    assert _selected(manifest, tree, "rebuild/kernel-rs/Cargo.lock") == ["01", "05"]
    assert _selected(manifest, tree, "rebuild/kernel-rs/tests/cli.rs") == []


@pytest.mark.parametrize(
    ("changed", "selected"),
    [
        ("rebuild/pipeline/gpos.py", ["08"]),
        ("rebuild/perf/faults/08.patch", ["08"]),
        ("rebuild/pipeline/emit.py", ["05"]),
        ("rebuild/perf/faults.yaml", ["01", "05", "08"]),
        ("rebuild/tools/fault_kit.py", ["01", "05", "08"]),
    ],
)
def test_a_change_to_a_patched_file_or_to_a_rows_patch_selects_that_row(tmp_path, changed, selected):
    manifest, tree = _selection_kit(tmp_path)
    assert _selected(manifest, tree, changed) == selected


def test_a_change_to_an_unreached_gate_is_reported_as_unreached(tmp_path):
    manifest, tree = _selection_kit(tmp_path)
    witness = fault_kit.select(manifest, "auto", frozenset(["rebuild/pipeline/witness.py"]), tree)
    assert [row.id for row in witness.rows] == ["01"]
    assert witness.unreached == {"witness": ("01",)}
    assert fault_kit.unreached_text(witness.unreached) == "unreached: witness (rows 01)"
    sweep = fault_kit.select(manifest, "auto", frozenset(["rebuild/pipeline/shaping.py"]), tree)
    assert sweep.unreached == {}


def _record(expected: int, exit_status: int | None, *, missing=(), changed=None, timed_out=False):
    return fault_kit.StepRecord(
        None, [], expected, exit_status, timed_out, 0.0, list(missing), [], [], changed, ""
    )


def test_a_row_is_caught_only_when_every_step_meets_its_exit_and_requirements():
    caught = [_record(1, 1), _record(1, 1)]
    assert fault_kit.classify(caught) == "caught"
    assert fault_kit.classify([_record(0, 0, changed=["tables"]), _record(1, 1)]) == "caught"
    assert fault_kit.classify([_record(1, 1), _record(1, 1, missing=["qsAh"])]) == "wrong-catch"
    assert fault_kit.classify([_record(1, 2)]) == "wrong-catch"
    assert fault_kit.classify([_record(1, None, timed_out=True)]) == "wrong-catch"


def test_an_exit_0_with_unchanged_effect_files_is_inert_and_with_changed_ones_missed():
    assert fault_kit.classify([_record(1, 0, changed=[])]) == "inert"
    assert fault_kit.classify([_record(0, 0, changed=[]), _record(1, 0, changed=[])]) == "inert"
    assert fault_kit.classify([_record(1, 0, changed=["rebuild/out/m1/M1.otf"])]) == "missed"
    assert fault_kit.classify([_record(1, 1), _record(1, 0, changed=["rebuild/out/m1/M1.otf"])]) == "missed"
    assert fault_kit.classify([_record(1, 1, missing=["qsAh"]), _record(1, 0, changed=[])]) == "wrong-catch"
    assert fault_kit.classify([_record(1, None, timed_out=True), _record(1, 0, changed=[])]) == "wrong-catch"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _clone(tmp_path: Path, checked: bool = True, switch: bool = True) -> tuple[Path, str]:
    """A repo shaped like the kit's tree: tracked sources, an ignored rebuild/out, the stub step at its root, and, unless `switch` is false, a spec_load.py that has the alphabet switch. Returns it with its first commit."""
    repo = tmp_path / "kit" / "var" / "perf" / "clone"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.excludesFile", os.devnull)
    files = {
        ".gitignore": "rebuild/out/\n",
        "step.py": STUB,
        "rebuild/pipeline/gate.py": "FAULT = False\n",
        "rebuild/pipeline/check.py": f"CHECKED = {checked}\n",
    }
    if switch:
        files[fault_kit.SWITCH_FILE] = 'M1_RUNES_ENV = "AMS_M1_RUNES"\n'
    first = _commit(repo, files, "start")
    return repo, first


def _fault_patch(repo: Path, rel: str = "rebuild/pipeline/gate.py", text: str = "FAULT = True\n") -> str:
    """The patch that writes `text` into the tracked file `rel`."""
    original = (repo / rel).read_text()
    (repo / rel).write_text(text)
    patch = _git(repo, "diff") + "\n"
    (repo / rel).write_text(original)
    return patch


def _fault_row(row_id: str, alphabet: list[str], argv: list[str] | None = None) -> dict:
    """A row whose step the stub catches, once the patch sets its fault flag, by exiting 1 with a summary naming qsAh."""
    return {
        "id": row_id,
        "section10": int(row_id),
        "slug": "fault-flag-set",
        "patch": f"faults/{row_id}.patch",
        "alphabet": alphabet,
        "effect": ["rebuild/out/m1/effect.txt"],
        "steps": [
            {
                "argv": argv or [sys.executable, "step.py"],
                "gate": "check",
                "exit": 1,
                "require": {
                    "output": ["the fault is caught"],
                    "files": {"rebuild/out/m1/summary.json": ["qsAh"]},
                },
                "detail": {"output": ["gate:", "a line the stub never prints"]},
            }
        ],
    }


CHECK_GATES = {"check": {"modules": [], "crate": False, "files": []}}


def _fault_manifest(tmp_path: Path, repo: Path, argv: list[str] | None = None):
    """One row, 01 on the alphabet qsAh,qsGay, whose patch sets the stub's fault flag."""
    row = _fault_row("01", ["qsGay", "qsAh"], argv)
    return _manifest(tmp_path / "perf", [row], {"01": _fault_patch(repo)}, CHECK_GATES)


def _run(
    tmp_path: Path, manifest, repo: Path, *, base: str | None = None, kit_root: Path | None = None, meter=None
):
    out = tmp_path / "faults.json"
    status = fault_kit.run_kit(
        manifest,
        root=repo,
        store=tmp_path / "controls",
        rows="all",
        changed=None,
        base=base,
        out=out,
        log_dir=tmp_path / "logs",
        kit_root=kit_root or tmp_path / "kit",
        meter=meter,
    )
    return status, json.loads(out.read_text())


def test_a_row_is_applied_run_checked_and_reverted(tmp_path):
    repo, head = _clone(tmp_path)
    manifest = _fault_manifest(tmp_path, repo)
    status, report = _run(tmp_path, manifest, repo)
    assert status == 0
    [row] = report["rows"]
    assert (row["result"], row["on_candidate"], row["on_base"]) == ("caught", "caught", None)
    [step] = row["steps"]
    assert step["exit"] == 1 and step["required_missing"] == []
    assert (step["detail_matched"], step["detail_missing"]) == (["gate:"], ["a line the stub never prints"])
    assert "the fault is caught" in Path(step["log"]).read_text()
    assert "alphabet qsAh,qsGay" in Path(step["log"]).read_text()
    assert row["alphabet"] == ["qsAh", "qsGay"]
    [control] = report["control"]
    assert (control["exit"], control["alphabet"], control["reused"]) == (0, ["qsAh", "qsGay"], False)
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    out = repo / "rebuild" / "out" / "m1"
    assert sorted(path.name for path in out.iterdir()) == ["effect.txt"]
    assert (out / "effect.txt").read_text() == "clean\n"


@pytest.mark.parametrize(
    ("base_checked", "candidate_files", "on_candidate", "result"),
    [
        (True, {"rebuild/pipeline/check.py": "CHECKED = False\n"}, "missed", "lost"),
        (True, {"rebuild/pipeline/gate.py": "FAULT = 0\n"}, "stale-patch", "re-author"),
        (False, {"notes.txt": "an unrelated edit\n"}, "missed", "drifted"),
    ],
)
def test_a_row_failing_on_the_candidate_is_rerun_on_the_base(
    tmp_path, base_checked, candidate_files, on_candidate, result
):
    repo, base = _clone(tmp_path, checked=base_checked)
    manifest = _fault_manifest(tmp_path, repo)
    candidate = _commit(repo, candidate_files, "candidate")
    status, report = _run(tmp_path, manifest, repo, base=base)
    assert status == 1
    [row] = report["rows"]
    on_base = "caught" if base_checked else "missed"
    assert (row["on_candidate"], row["on_base"], row["result"]) == (on_candidate, on_base, result)
    assert report["base"] == base
    assert _git(repo, "rev-parse", "HEAD") == candidate
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "rebuild" / "out" / "m1" / "effect.txt").read_text() == "clean\n"


@pytest.mark.parametrize(
    "case", ["own checkout", "outside var/perf", "dirty clone", "failing control", "another run"]
)
def test_the_kit_refuses_its_own_checkout_a_dirty_clone_and_a_failing_control(tmp_path, case):
    repo, head = _clone(tmp_path)
    argv = [sys.executable, "-c", "raise SystemExit(3)"] if case == "failing control" else None
    manifest = _fault_manifest(tmp_path, repo, argv)
    if case == "dirty clone":
        (repo / "stray.txt").write_text("someone's work\n")
    kit_root = {"own checkout": repo, "outside var/perf": tmp_path / "another"}.get(case)
    with contextlib.ExitStack() as other:
        if case == "another run":
            other.enter_context(store_lock.hold_flock(store_lock.lock_path_for(repo), blocking=False))
        status, report = _run(tmp_path, manifest, repo, kit_root=kit_root)
    assert status == 2
    expected = {
        "own checkout": "own checkout",
        "outside var/perf": "not a scratch tree",
        "dirty clone": "not clean",
        "failing control": "exited 3",
        "another run": "another fault kit run holds",
    }[case]
    assert expected in report["refused"]
    assert report["rows"] == []
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ("?? stray.txt" if case == "dirty clone" else "")


def test_each_alphabet_keeps_its_control_until_the_sources_move(tmp_path):
    repo, _ = _clone(tmp_path)
    patch = _fault_patch(repo)
    rows = [_fault_row("01", ["qsAh"]), _fault_row("02", ["qsBay", "qsAh"])]
    manifest = _manifest(tmp_path / "perf", rows, {"01": patch, "02": patch}, CHECK_GATES)
    status, first = _run(tmp_path, manifest, repo)
    assert status == 0
    assert [(control["alphabet"], control["reused"]) for control in first["control"]] == [
        (["qsAh"], False),
        (["qsAh", "qsBay"], False),
    ]
    logs = {row["id"]: Path(row["steps"][0]["log"]).read_text() for row in first["rows"]}
    assert "alphabet qsAh\n" in logs["01"] and "alphabet qsAh,qsBay\n" in logs["02"]
    slots = sorted(path.name for path in (tmp_path / "controls").iterdir())
    assert slots == sorted(
        ["seed", *(f"tree-{fault_kit.alphabet_slug(alphabet)}" for alphabet in (["qsAh"], ["qsAh", "qsBay"]))]
    )
    unused = tmp_path / "controls" / "tree-000000000000"
    unused.mkdir()
    (unused / fault_kit.SNAPSHOT_MARKER).write_text("{}\n")
    status, second = _run(tmp_path, manifest, repo)
    assert status == 0
    assert not unused.exists()
    assert [row["result"] for row in second["rows"]] == ["caught", "caught"]
    assert [control["reused"] for control in second["control"]] == [True, True]
    _commit(repo, {"notes.txt": "the sources move\n"}, "moved")

    def stopped_in_the_snapshot(source: Path, target: Path) -> str:
        if target.parent.name.startswith("tree-"):
            target.mkdir()
            raise fault_kit.WriteCapReached("the gate stopped the snapshot partway")
        return clone_tree(source, target)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(fault_kit, "clone_tree", stopped_in_the_snapshot)
        status, _ = _run(tmp_path, manifest, repo)
    assert status == fault_kit.WRITE_CAP_STATUS
    (tmp_path / "controls" / "tree-000000000000.retired" / "out").mkdir(parents=True)
    status, third = _run(tmp_path, manifest, repo)
    assert status == 0
    assert [control["reused"] for control in third["control"]] == [False, False]
    assert (repo / "rebuild" / "out" / "m1" / "effect.txt").read_text() == "clean\n"
    assert sorted(path.name for path in (tmp_path / "controls").iterdir()) == slots


def test_a_row_inert_on_both_sides_is_not_covered(tmp_path):
    repo, base = _clone(tmp_path)
    untouched = _fault_patch(
        repo, "rebuild/pipeline/check.py", "CHECKED = True  # the stub reads no comment\n"
    )
    manifest = _manifest(tmp_path / "perf", [_fault_row("01", ["qsAh"])], {"01": untouched}, CHECK_GATES)
    status, report = _run(tmp_path, manifest, repo, base=base)
    assert status == 1
    [row] = report["rows"]
    assert (row["on_candidate"], row["on_base"], row["result"]) == ("inert", "inert", "not-covered")
    assert row["note"] == fault_kit.NOT_COVERED_NOTE
    assert [control["alphabet"] for control in report["base_control"]] == [["qsAh"]]


def test_a_base_without_the_alphabet_switch_is_not_run(tmp_path):
    repo, base = _clone(tmp_path, switch=False)
    manifest = _fault_manifest(tmp_path, repo)
    _commit(
        repo,
        {
            fault_kit.SWITCH_FILE: 'M1_RUNES_ENV = "AMS_M1_RUNES"\n',
            "rebuild/pipeline/check.py": "CHECKED = False\n",
        },
        "the switch, and a lost catch",
    )
    status, report = _run(tmp_path, manifest, repo, base=base)
    assert status == 1
    [row] = report["rows"]
    assert (row["on_candidate"], row["on_base"], row["result"]) == ("missed", None, "missed")
    assert "no alphabet switch" in report["base_control"]


def test_the_tree_is_the_work_tree_and_a_refresh_clones_only_what_moved(tmp_path):
    source = tmp_path / "main"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "core.excludesFile", os.devnull)
    head = _commit(
        source,
        {
            ".gitignore": "var/\nrebuild/out/\nsite/*.otf\nrebuild/kernel-rs/target/\n",
            "kept.txt": "kept\n",
            "edited.txt": "before\n",
            "deleted.txt": "gone\n",
        },
        "start",
    )
    (source / "edited.txt").write_text("after\n")
    (source / "deleted.txt").unlink()
    (source / "new.txt").write_text("untracked\n")
    (source / "site").mkdir()
    (source / "site" / "Senior.otf").write_bytes(b"font")
    out = source / "rebuild" / "out"
    out.mkdir(parents=True)
    (out / "baseline-default.tsv.gz").write_bytes(b"rows")
    (out / "cycle-timings.ndjson").write_text("{}\n")
    release = source / fault_kit.CRATE_RELEASE
    release.mkdir(parents=True)
    (release / "ams-m1-kernel").write_bytes(b"binary")
    root, store = source / "var" / "perf" / "kit", source / "var" / "perf" / "kit-controls"

    commit = fault_kit.prepare_tree(source, root, store)
    assert _git(root, "status", "--porcelain") == ""
    assert (_git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^")) == (commit, head)
    assert _git(root, "ls-files").split() == [".gitignore", "edited.txt", "kept.txt", "new.txt"]
    assert (root / "edited.txt").read_text() == "after\n"
    assert (root / "site" / "Senior.otf").read_bytes() == b"font"
    assert (root / fault_kit.CRATE_RELEASE / "ams-m1-kernel").read_bytes() == b"binary"
    assert [path.name for path in (store / "seed" / "out").iterdir()] == ["baseline-default.tsv.gz"]

    kept = (root / "kept.txt").stat().st_mtime_ns
    edited = (root / "edited.txt").stat().st_mtime_ns
    (source / "edited.txt").write_text("again\n")
    (root / ".git" / "index.lock").write_text("")
    assert fault_kit.prepare_tree(source, root, store) not in (commit, head)
    assert (root / "kept.txt").stat().st_mtime_ns == kept
    assert (root / "edited.txt").stat().st_mtime_ns > edited
    assert (root / "edited.txt").read_text() == "again\n"

    clean = _commit(source, {}, "the edits")
    assert fault_kit.prepare_tree(source, root, store) == clean
    assert _git(root, "rev-parse", "HEAD") == clean
    assert _git(root, "status", "--porcelain") == ""

    foreign = source / "var" / "perf" / "someone-else"
    foreign.mkdir()
    (foreign / "work.txt").write_text("not the kit's\n")
    with pytest.raises(fault_kit.Refused, match="other than the kit's tree"):
        fault_kit.prepare_tree(source, foreign, store)
    assert (foreign / "work.txt").read_text() == "not the kit's\n"


def test_every_rows_alphabet_narrows_the_store_and_holds_a_manual_pin_and_a_ligature():
    """`run_m1` fails a build with no Manual pin in scope, so each row's alphabet holds every letter of some pin the gate replays (#618's decision 59); it holds a ligature, since without one the formation lookup comes out empty and read-back fails the unfaulted build; and the switch accepts it."""
    spec = spec_load.load_spec(
        spec_load.DEFAULT_RUNES_DIR, spec_load.DEFAULT_REGISTRY_PATH, spec_load.DEFAULT_SCHEMA_DIR
    )
    pins = [
        {ord(character) for character in pin.text}
        for pin in collect_pin_runs(ReplayReport(), configs=manual_pins.PIN_CONFIGS)
        if pin.source.startswith(manual_pins.MANDATING_CORPORA)
        and pin.config_token in conform.ACCEPTANCE_CONFIGS
    ]
    for row in fault_kit.load_manifest().rows:
        narrowed = spec_load.narrowed(spec, row.alphabet)
        alphabet = manual_pins.migrated_alphabet(narrowed)
        assert any(pin <= alphabet for pin in pins), row.id
        assert any(rune.sequence for rune in narrowed.runes.values()), row.id


def test_every_row_names_known_gates_and_an_existing_patch():
    manifest = fault_kit.load_manifest()
    assert manifest.rows
    for row in manifest.rows:
        assert row.patch_file == fault_kit.MANIFEST.parent / "faults" / f"{row.id}.patch"
        assert row.patch_file.is_file() and row.patched_files, row.id
        assert set(row.gates) <= set(manifest.gates), row.id
    for gate in manifest.gates.values():
        for module in gate.modules:
            assert fault_kit.module_files(fault_kit.ROOT, module), module


PARENT = os.getppid()
SIBLING = 99_999
STALL = """import os
import subprocess
import sys
import time
from pathlib import Path

out = Path("rebuild/out/m1")
out.mkdir(parents=True, exist_ok=True)
(out / "effect.txt").write_text("clean\\n")
if "FAULT = True" in Path("rebuild/pipeline/gate.py").read_text():
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    pids = Path(sys.argv[1])
    staged = pids.with_suffix(".staged")
    staged.write_text(f"{os.getpid()} {grandchild.pid}")
    staged.rename(pids)
    time.sleep(30)
"""


def _reading(
    members: list[Member], logical: int, *, device: bool, check: str | None = None, swap: int = 0
) -> WriteReading:
    """A reading of coalition 7 by this process, whose logical writes are `logical`, with the device's and swap's counters only when `device` asks for them, as `write_meter.read` takes one."""
    usage = dict.fromkeys(write_meter._COALITION_FIELDS, 0) | {
        "tasks_started": 10,
        "logical_immediate_writes": logical,
    }
    now = time.monotonic()
    return WriteReading(
        os.getpid(),
        now,
        time.time(),
        7,
        usage,
        tuple(members),
        check,
        None,
        logical if device else None,
        swap if device else None,
    )


def _kit() -> list[Member]:
    """The kit and the parent it runs under."""
    return [Member(os.getpid(), PARENT, 0.0, 0), Member(PARENT, 1, 0.0, 0)]


@pytest.mark.parametrize("basis", ["shared", "device"])
def test_the_gate_refuses_a_run_whose_first_reading_is_not_in_the_own_basis(tmp_path, basis):
    """A sibling under the kit's parent, as the sleep-assertion child `caffeinate` forks is beside a kit under `uv run`, puts the reading in the shared basis; a failed self-check puts it in the device basis."""
    repo, head = _clone(tmp_path)
    manifest = _fault_manifest(tmp_path, repo)
    members = [*_kit(), Member(SIBLING, PARENT, 0.0, 0)] if basis == "shared" else _kit()
    check = "the scan did not find the reader in its own coalition" if basis == "device" else None
    status, report = _run(
        tmp_path, manifest, repo, meter=lambda device: _reading(members, 0, device=device, check=check)
    )
    assert status == 2
    assert f"in the {basis} basis" in report["refused"]
    assert (report["control"], report["rows"], report["writes"]) == ([], [], None)
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="the gate polls on a Darwin kqueue timer")
def test_a_gated_run_records_its_writes_with_swap_beside_the_cap_and_never_in_it(tmp_path):
    repo, _ = _clone(tmp_path)
    manifest = _fault_manifest(tmp_path, repo)
    asked: list[bool] = []

    def meter(device: bool) -> WriteReading:
        later = bool(asked)
        asked.append(device)
        return _reading(_kit(), 1_000_000 if later else 0, device=device, swap=5_000_000_000 if later else 0)

    handler = signal.getsignal(fault_kit.GATE_SIGNAL)
    status, report = _run(tmp_path, manifest, repo, meter=meter)
    assert status == 0
    assert [row["result"] for row in report["rows"]] == ["caught"]
    writes = report["writes"]
    assert (writes["basis"], writes["bytes"], writes["swap_bytes"], writes["stopped"]) == (
        "own",
        1_000_000,
        5_000_000_000,
        None,
    )
    assert (writes["cap_bytes"], writes["watermark_bytes"]) == (
        fault_kit.WRITE_CAP_BYTES,
        fault_kit.WRITE_WATERMARK_BYTES,
    )
    assert asked[0] and asked[-1] and not any(asked[1:-1])
    assert writes["polls"] == len(asked) - 2
    assert signal.getsignal(fault_kit.GATE_SIGNAL) == handler


def _gone(pid: int) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(sys.platform != "darwin", reason="the gate polls on a Darwin kqueue timer")
def test_the_gate_stops_and_kills_every_member_but_the_kit_and_its_parent_at_the_watermark(
    tmp_path, monkeypatch
):
    """The stub step starts a grandchild in a session of its own, outside the step's process group, and stalls; once both have written their pids, the synthetic coalition holds them and its writes pass the watermark. The gate's signals reach only the stub's two processes, so a fault in the gate cannot signal this test's parent."""
    repo, _ = _clone(tmp_path)
    head = _commit(repo, {"stall.py": STALL}, "stall")
    pids = tmp_path / "stub.pids"
    manifest = _fault_manifest(tmp_path, repo, [sys.executable, "stall.py", str(pids)])

    def started() -> list[int]:
        return [int(pid) for pid in pids.read_text().split()] if pids.exists() else []

    def meter(device: bool) -> WriteReading:
        stub = started()
        members = _kit()
        if stub:
            members += [Member(stub[0], os.getpid(), 0.0, 0), Member(stub[1], stub[0], 0.0, 0)]
        return _reading(members, fault_kit.WRITE_WATERMARK_BYTES + 1 if stub else 0, device=device)

    sent: list[tuple[int, int]] = []
    send = fault_kit._send

    def recorded(pid: int, signum: int) -> None:
        sent.append((pid, signum))
        if pid in started():
            send(pid, signum)

    monkeypatch.setattr(fault_kit, "_send", recorded)
    status, report = _run(tmp_path, manifest, repo, meter=meter)
    stub, grandchild = started()
    assert status == fault_kit.WRITE_CAP_STATUS
    assert "watermark" in report["error"] and report["rows"] == []
    stopped = report["writes"]["stopped"]
    assert sorted(member["pid"] for member in stopped["members"]) == sorted([stub, grandchild])
    assert stopped["bytes"] > fault_kit.WRITE_WATERMARK_BYTES
    for pid in (stub, grandchild):
        assert [signum for target, signum in sent if target == pid] == [signal.SIGSTOP, signal.SIGKILL]
    assert {target for target, _ in sent} == {stub, grandchild}
    assert _gone(grandchild)
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "rebuild" / "out" / "m1" / "effect.txt").read_text() == "clean\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="the gate polls on a Darwin kqueue timer")
def test_a_poll_that_fails_stops_the_run(tmp_path):
    repo, head = _clone(tmp_path)
    manifest = _fault_manifest(tmp_path, repo, [sys.executable, "-c", "import time; time.sleep(30)"])

    def meter(device: bool) -> WriteReading:
        if not device:
            raise OSError("the coalition cannot be read")
        return _reading(_kit(), 0, device=device)

    status, report = _run(tmp_path, manifest, repo, meter=meter)
    assert status == fault_kit.WRITE_CAP_STATUS
    assert "poll failed" in report["error"]
    assert report["writes"]["failure"] == "OSError: the coalition cannot be read"
    assert report["writes"]["stopped"] is None
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
