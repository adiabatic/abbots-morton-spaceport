"""Tests for rebuild/tools/fault_kit.py: selection over a synthetic tree, the result classification, and whole runs against synthetic git repos under tmp_path whose step is a stub script."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rebuild.tools import fault_kit

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
STUB = """import sys
from pathlib import Path

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


def _clone(tmp_path: Path, checked: bool = True) -> tuple[Path, str]:
    """A repo shaped like a scratch clone: tracked sources, an ignored rebuild/out, and the stub step at its root. Returns it with its first commit."""
    repo = tmp_path / "kit" / "var" / "perf" / "clone"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.excludesFile", os.devnull)
    first = _commit(
        repo,
        {
            ".gitignore": "rebuild/out/\n",
            "step.py": STUB,
            "rebuild/pipeline/gate.py": "FAULT = False\n",
            "rebuild/pipeline/check.py": f"CHECKED = {checked}\n",
        },
        "start",
    )
    return repo, first


def _fault_manifest(tmp_path: Path, repo: Path, argv: list[str] | None = None):
    """One row whose patch sets the stub's fault flag and whose step the stub catches by exiting 1 with a summary naming qsAh."""
    gate = repo / "rebuild" / "pipeline" / "gate.py"
    gate.write_text("FAULT = True\n")
    patch = _git(repo, "diff") + "\n"
    _git(repo, "checkout", "--", "rebuild/pipeline/gate.py")
    row = {
        "id": "01",
        "section10": 1,
        "slug": "fault-flag-set",
        "patch": "faults/01.patch",
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
    gates = {"check": {"modules": [], "crate": False, "files": []}}
    return _manifest(tmp_path / "perf", [row], {"01": patch}, gates)


def _run(tmp_path: Path, manifest, repo: Path, *, base: str | None = None, kit_root: Path | None = None):
    out = tmp_path / "faults.json"
    status = fault_kit.run_kit(
        manifest,
        root=repo,
        snapshot=tmp_path / "faults.snap",
        rows="all",
        changed=None,
        base=base,
        out=out,
        log_dir=tmp_path / "logs",
        kit_root=kit_root or tmp_path / "kit",
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
    assert report["control"][0]["exit"] == 0
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


@pytest.mark.parametrize("case", ["own checkout", "outside var/perf", "dirty clone", "failing control"])
def test_the_kit_refuses_its_own_checkout_a_dirty_clone_and_a_failing_control(tmp_path, case):
    repo, head = _clone(tmp_path)
    argv = [sys.executable, "-c", "raise SystemExit(3)"] if case == "failing control" else None
    manifest = _fault_manifest(tmp_path, repo, argv)
    if case == "dirty clone":
        (repo / "stray.txt").write_text("someone's work\n")
    kit_root = {"own checkout": repo, "outside var/perf": tmp_path / "another"}.get(case)
    status, report = _run(tmp_path, manifest, repo, kit_root=kit_root)
    assert status == 2
    expected = {
        "own checkout": "own checkout",
        "outside var/perf": "not a scratch clone",
        "dirty clone": "not clean",
        "failing control": "exited 3",
    }[case]
    assert expected in report["refused"]
    assert report["rows"] == []
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ("?? stray.txt" if case == "dirty clone" else "")


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
