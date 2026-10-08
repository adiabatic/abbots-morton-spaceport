"""Tests for the contracts lane's per-test closures: the static import walk over a synthetic tree, the selection rule over a hand-written record, the merge of a run's sidecar into the previous record, the gate's narrowing and recording, the leaf-only imports of both conftests, and, end to end in a child pytest under `-p rebuild.conftest`, that the audit guard records reads, imports, fixture setups, spawns, and a fixture's announced import, and applies a selection file. The child runs as a subprocess because the guard is a `sys.addaudithook`, which cannot be uninstalled."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import Future
from pathlib import Path

import pytest

from rebuild.pipeline import kernel_exec
from rebuild.tools import artifact_cycle as ac
from rebuild.tools import contracts_closure as cc
from rebuild.tools import cycle_paths
from rebuild.tools import rebuild_gate as rg

pytest_plugins = ("pytester",)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "rebuild/review/fixtures/manifest.json"


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestHermeticChildren:
    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "rev-parse", "HEAD"],
            ["git", "cat-file", "-e", "abc123"],
            ("git", "archive", "--format=tar", "abc123"),
            ["/usr/bin/git", "rev-parse", "--short", "HEAD"],
        ],
    )
    def test_git_object_store_reads_are_hermetic(self, argv):
        assert cc.hermetic_child(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "status", "--porcelain"],
            ["git", "ls-files"],
            ["git", "diff"],
            ["uv", "run", "pytest"],
            ["true"],
            "git rev-parse HEAD",
            None,
        ],
    )
    def test_everything_else_leaves_inputs_untraced(self, argv):
        assert not cc.hermetic_child(argv)
        assert not cc.kernel_child(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            [str(kernel_exec.BINARY), "settle-cases", "/scratch/spec.json", "/scratch/cases.tsv"],
            ["cargo", "build", "--release", "--manifest-path", str(kernel_exec.MANIFEST)],
        ],
    )
    def test_the_kernel_and_its_build_are_kernel_children(self, argv):
        assert cc.kernel_child(argv)
        assert not cc.hermetic_child(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            ["cargo", "build", "--release"],
            ["cargo", "test", "--manifest-path", str(kernel_exec.MANIFEST)],
            ["cargo", "build", "--manifest-path", "/elsewhere/Cargo.toml"],
            ["/opt/bin/other-kernel", "settle-cases"],
        ],
    )
    def test_other_cargo_and_other_binaries_are_not(self, argv):
        assert not cc.kernel_child(argv)

    def test_kernel_files_are_the_crates_labels(self):
        files = {"rebuild/kernel-rs/src/engine.rs": "e", "rebuild/kernel-rs/Cargo.lock": "l", "a.yaml": "1"}
        assert cc.kernel_files(files) == {"rebuild/kernel-rs/src/engine.rs", "rebuild/kernel-rs/Cargo.lock"}


class TestReadNormalization:
    def test_bytecode_maps_to_its_source(self):
        assert (
            cc.source_of("rebuild/tools/__pycache__/peak_rss.cpython-314.pyc") == "rebuild/tools/peak_rss.py"
        )
        assert cc.source_of("rebuild/tools/peak_rss.py") == "rebuild/tools/peak_rss.py"

    @pytest.mark.parametrize(
        "rel", [".venv/lib/x.py", ".uv-cache/a", ".git/HEAD", "rebuild/kernel-rs/target/x"]
    )
    def test_the_interpreters_own_trees_are_not_recorded(self, rel):
        assert not cc.recordable(rel)

    def test_repo_sources_are_recorded(self):
        assert cc.recordable("glyph_data/runes/qsPea.yaml")
        assert cc.recordable("rebuild/tools/peak_rss.py")


@pytest.fixture
def synthetic_tree(tmp_path: Path) -> Path:
    """A small repo with every import form the walk must follow: a package, an absolute `from` import, a relative one, an import inside a function, a `TYPE_CHECKING` import, a bare sibling import, and a `test/` module imported by bare name."""
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/a.py", "from pkg import b\nfrom . import c\n\n\ndef f():\n    import pkg.lazy\n")
    _write(tmp_path, "pkg/b.py", "import json\n")
    _write(
        tmp_path,
        "pkg/c.py",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg.typed import T\n",
    )
    _write(tmp_path, "pkg/typed.py")
    _write(tmp_path, "pkg/lazy.py", "import sib\n")
    _write(tmp_path, "pkg/sib.py")
    _write(tmp_path, "test/test_shaping.py")
    _write(tmp_path, "x.py", "import test_shaping\n")
    _write(tmp_path, "broken.py", "def (\n")
    return tmp_path


class TestStaticImportClosure:
    def test_every_import_shape_is_followed(self, synthetic_tree: Path):
        closure = cc.ImportGraph(synthetic_tree).closure("pkg/a.py")
        assert closure == {
            "pkg/a.py",
            "pkg/__init__.py",
            "pkg/b.py",
            "pkg/c.py",
            "pkg/typed.py",
            "pkg/lazy.py",
            "pkg/sib.py",
        }

    def test_a_bare_name_resolves_against_the_sibling_roots(self, synthetic_tree: Path):
        assert cc.ImportGraph(synthetic_tree).closure("x.py") == {"x.py", "test/test_shaping.py"}

    def test_a_file_that_does_not_parse_is_its_own_closure(self, synthetic_tree: Path):
        assert cc.ImportGraph(synthetic_tree).closure("broken.py") == {"broken.py"}

    def test_modules_outside_the_repo_are_not_followed(self, synthetic_tree: Path):
        assert cc.ImportGraph(synthetic_tree).closure("pkg/b.py") == {"pkg/b.py"}


def _record(
    files: dict[str, str], tests: dict[str, dict], static: dict[str, list[str]] | None = None, **extra
):
    static = static if static is not None else {}
    for nodeid in tests:
        static.setdefault(cc.test_file_of(nodeid), [cc.test_file_of(nodeid)])
    for conftest in cc.CONFTEST_PATHS:
        static.setdefault(conftest, [conftest])
    return {
        "fingerprint": "fp",
        "files": files,
        "closures": {"static": static, "module_reads": extra.get("module_reads", {}), "tests": tests},
    }


BASE_FILES = {
    "conftest.py": "c",
    "rebuild/conftest.py": "c",
    "pyproject.toml": "p",
    "uv.lock": "u",
    "fonts": "f",
    "rebuild/test_t.py": "t",
    "rebuild/test_u.py": "t",
    "a.yaml": "1",
    "b.yaml": "2",
    "m.py": "m",
    "n.py": "n",
    "rebuild/kernel-rs/src/engine.rs": "e",
}
TESTS = {
    "rebuild/test_t.py::reads_a": {"reads": ["a.yaml"], "modules": [], "untraced_inputs": False},
    "rebuild/test_t.py::reads_b": {"reads": ["b.yaml"], "modules": [], "untraced_inputs": False},
    "rebuild/test_t.py::spawns": {"reads": [], "modules": [], "untraced_inputs": True},
    "rebuild/test_u.py::imports_m": {"reads": [], "modules": ["m.py"], "untraced_inputs": False},
}
STATIC = {"m.py": ["m.py", "n.py"]}


class TestSelection:
    def test_only_tests_whose_closure_misses_the_diff_are_kept_off(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, {**BASE_FILES, "a.yaml": "9"})
        assert selection.skip == {"rebuild/test_t.py::reads_b", "rebuild/test_u.py::imports_m"}
        assert selection.changed == ("a.yaml",)
        assert selection.known == 4
        assert not selection.reason

    def test_a_test_with_untraced_inputs_always_runs(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        assert "rebuild/test_t.py::spawns" not in cc.select(record, {**BASE_FILES, "a.yaml": "9"}).skip

    def test_a_record_that_spells_the_flag_unclosable_still_runs_the_test(self):
        spawns = {"reads": [], "modules": [], "unclosable": True}
        record = _record(BASE_FILES, {**TESTS, "rebuild/test_t.py::spawns": spawns}, dict(STATIC))
        assert "rebuild/test_t.py::spawns" not in cc.select(record, {**BASE_FILES, "a.yaml": "9"}).skip

    def test_a_test_that_spawned_the_kernel_runs_on_a_crate_edit_and_on_nothing_else_new(self):
        settles = {"reads": ["a.yaml"], "modules": [], "kernel": True, "untraced_inputs": False}
        record = _record(BASE_FILES, {**TESTS, "rebuild/test_t.py::settles": settles}, dict(STATIC))
        crate_edit = cc.select(record, {**BASE_FILES, "rebuild/kernel-rs/src/engine.rs": "9"})
        assert "rebuild/test_t.py::settles" not in crate_edit.skip
        assert "rebuild/test_t.py::reads_b" in crate_edit.skip
        other_edit = cc.select(record, {**BASE_FILES, "b.yaml": "9"})
        assert "rebuild/test_t.py::settles" in other_edit.skip

    def test_a_dynamically_imported_modules_closure_counts(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, {**BASE_FILES, "n.py": "9"})
        assert "rebuild/test_u.py::imports_m" not in selection.skip
        assert "rebuild/test_t.py::reads_a" in selection.skip

    def test_a_modules_import_time_reads_count(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC), module_reads={"n.py": ["b.yaml"]})
        selection = cc.select(record, {**BASE_FILES, "b.yaml": "9"})
        assert "rebuild/test_u.py::imports_m" not in selection.skip
        assert "rebuild/test_t.py::reads_a" in selection.skip

    def test_a_changed_test_module_runs_its_own_tests(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, {**BASE_FILES, "rebuild/test_t.py": "9"})
        assert selection.skip == {"rebuild/test_u.py::imports_m"}

    def test_a_test_whose_static_closure_is_missing_runs(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        del record["closures"]["static"]["m.py"]
        assert "rebuild/test_u.py::imports_m" not in cc.select(record, {**BASE_FILES, "a.yaml": "9"}).skip

    @pytest.mark.parametrize("label", sorted(cc.GLOBAL_LABELS))
    def test_a_global_label_runs_the_whole_lane(self, label):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, {**BASE_FILES, label: "9"})
        assert selection.skip == frozenset()
        assert "global" in selection.reason

    def test_an_added_input_runs_the_whole_lane(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, {**BASE_FILES, "new.yaml": "1"})
        assert selection.skip == frozenset()
        assert "added or removed" in selection.reason

    def test_a_removed_input_runs_the_whole_lane(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        current = dict(BASE_FILES)
        del current["b.yaml"]
        assert cc.select(record, current).skip == frozenset()

    def test_a_record_without_closures_runs_the_whole_lane(self):
        assert cc.select({"fingerprint": "fp", "files": BASE_FILES}, BASE_FILES).skip == frozenset()
        assert cc.select(None, BASE_FILES).skip == frozenset()

    def test_nothing_moved_keeps_every_test_with_traced_inputs_off(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, dict(BASE_FILES))
        assert selection.skip == {nodeid for nodeid, entry in TESTS.items() if not entry["untraced_inputs"]}

    def test_describe_says_what_runs(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        text = cc.select(record, {**BASE_FILES, "a.yaml": "9"}).describe()
        assert text.startswith("2 of 4 recorded tests run")
        assert "a.yaml" in text
        assert cc.Selection(reason="why").describe() == "every test runs (why)"


class TestSelectionFiles:
    def test_round_trip(self, tmp_path: Path):
        path = tmp_path / "selection.json"
        cc.write_selection(path, ["b", "a", "a"])
        assert cc.read_selection(path) == {"a", "b"}
        assert json.loads(path.read_text())["skip"] == ["a", "b"]

    def test_an_absent_or_malformed_file_keeps_nothing_off(self, tmp_path: Path):
        assert cc.read_selection(tmp_path / "missing.json") == frozenset()
        (tmp_path / "bad.json").write_text("[]")
        assert cc.read_selection(tmp_path / "bad.json") == frozenset()
        (tmp_path / "other.json").write_text(json.dumps({"format": "other", "skip": ["a"]}))
        assert cc.read_selection(tmp_path / "other.json") == frozenset()


@pytest.fixture
def contracts_tree(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for rel in (*cc.CONFTEST_PATHS, "rebuild/test_t.py", "rebuild/test_u.py"):
        _write(root, rel)
    _write(root, "extra.txt", "one")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    monkeypatch.setattr(cycle_paths, "REBUILD_CONTRACTS_GREEN", root / "out" / "rebuild-contracts-green.json")
    return root


def _save_contracts_record(root: Path) -> dict:
    key, roster = ac.contracts_closure(root)
    assert key is not None and roster is not None
    tests = {
        "rebuild/test_t.py::reads_extra": {"reads": ["extra.txt"], "modules": [], "untraced_inputs": False},
        "rebuild/test_u.py::plain": {"reads": [], "modules": [], "untraced_inputs": False},
    }
    previous = _record(roster, tests)
    files = cc.current_files(root, roster, previous)
    ac.record_green(ac.contracts_green(), key, files=files, closures=previous["closures"])
    previous["files"] = files
    return previous


class TestContractsLifecycle:
    def test_preparation_leaves_recorder_files_untouched(self, contracts_tree):
        _save_contracts_record(contracts_tree)
        sidecar = cc.sidecar_path(ac.contracts_green())
        sidecar.write_text("stale sidecar")
        run = cc.prepare_run(contracts_tree)
        assert run.skippable
        assert sidecar.read_text() == "stale sidecar"
        assert not cc.selection_path(run.record_path).exists()

    def test_an_extra_edit_prevents_a_roster_match_from_skipping(self, contracts_tree):
        _save_contracts_record(contracts_tree)
        original_key = ac.contracts_fingerprint(contracts_tree)
        _write(contracts_tree, "extra.txt", "two")
        run = cc.prepare_run(contracts_tree)
        assert run.key == original_key
        assert not run.skippable
        assert run.selection.skip == {"rebuild/test_u.py::plain"}
        assert run.selection.changed == ("extra.txt",)

    @pytest.mark.parametrize("force", [False, True])
    def test_an_extra_edit_during_a_run_withholds_publication(self, contracts_tree, force):
        previous = _save_contracts_record(contracts_tree)
        run = cc.prepare_run(contracts_tree, force)
        assert run.files is not None and run.files["extra.txt"] == previous["files"]["extra.txt"]
        cc.start_run(run)
        _write(contracts_tree, "extra.txt", "two")
        finished = cc.finish_run(contracts_tree, run, True)
        assert finished.status == "drifted"
        assert finished.payload is not None and finished.payload.moved == ("extra.txt",)
        record = ac.read_green_record(run.record_path)
        assert record is not None and record["files"] == previous["files"]

    def test_a_missing_sidecar_drops_closures_and_keeps_known_extra_inputs(self, contracts_tree):
        _save_contracts_record(contracts_tree)
        run = cc.prepare_run(contracts_tree, True)
        cc.write_sidecar(cc.sidecar_path(run.record_path), ["stale"], {"stale": {}})
        cc.start_run(run)
        assert not cc.sidecar_path(run.record_path).exists()
        assert cc.read_selection(cc.selection_path(run.record_path)) == frozenset()
        assert cc.finish_run(contracts_tree, run, True).status == "recorded"
        record = ac.read_green_record(run.record_path)
        assert record is not None and "closures" not in record
        assert "extra.txt" in record["files"]
        assert cc.prepare_run(contracts_tree).skippable
        _write(contracts_tree, "extra.txt", "two")
        assert not cc.prepare_run(contracts_tree).skippable

    def test_a_key_only_record_can_skip_on_its_roster(self, contracts_tree):
        key = ac.contracts_fingerprint(contracts_tree)
        assert key is not None
        ac.record_green(ac.contracts_green(), key)
        assert cc.prepare_run(contracts_tree).skippable

    def test_completion_merges_the_record_captured_by_preparation(self, contracts_tree):
        previous = _save_contracts_record(contracts_tree)
        _write(contracts_tree, "extra.txt", "two")
        run = cc.prepare_run(contracts_tree)
        cc.start_run(run)
        collected = list(previous["closures"]["tests"])
        cc.write_sidecar(
            cc.sidecar_path(run.record_path),
            collected,
            {collected[0]: previous["closures"]["tests"][collected[0]]},
        )
        ac.record_green(run.record_path, "unrelated", files={}, closures={"tests": {}})
        assert cc.finish_run(contracts_tree, run, True).status == "recorded"
        recorded = ac.read_green_record(run.record_path)
        assert recorded is not None
        assert recorded["closures"]["tests"][collected[1]] == previous["closures"]["tests"][collected[1]] | {
            "kernel": False
        }

    def test_a_tracked_unlink_and_reappearance_run_every_test(self, contracts_tree):
        _save_contracts_record(contracts_tree)
        source = contracts_tree / "rebuild" / "test_t.py"
        source.unlink()
        absent = cc.prepare_run(contracts_tree)
        assert absent.selection.skip == frozenset()
        assert "added or removed" in absent.selection.reason
        assert absent.files is not None and absent.files["rebuild/test_t.py"] == "absent"
        _save_contracts_record(contracts_tree)
        _write(contracts_tree, "rebuild/test_t.py")
        returned = cc.prepare_run(contracts_tree)
        assert returned.selection.skip == frozenset()
        assert "added or removed" in returned.selection.reason

    @pytest.mark.parametrize("matching", [False, True])
    def test_failure_clears_only_a_record_with_the_invocation_key(self, contracts_tree, matching):
        _save_contracts_record(contracts_tree)
        run = cc.prepare_run(contracts_tree, True)
        if not matching:
            ac.record_green(run.record_path, "unrelated")
        assert cc.finish_run(contracts_tree, run, False).status == "failed"
        record = ac.read_green_record(run.record_path)
        if matching:
            assert record is None
        else:
            assert record is not None and record["fingerprint"] == "unrelated"

    def test_no_git_runs_every_test_without_publication(self, contracts_tree, monkeypatch):
        monkeypatch.setattr(ac, "contracts_closure", lambda root: (None, None))
        run = cc.prepare_run(contracts_tree)
        assert not run.skippable and not run.selection.skip
        cc.start_run(run)
        assert cc.finish_run(contracts_tree, run, True).status == "unavailable"
        assert ac.read_green_record(run.record_path) is None


@pytest.mark.parametrize("caller", ["wrapper", "cycle"])
@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "extra",
        "forced-extra-drift",
        "roster-drift",
        "global",
        "failure",
        "no-git",
        "missing-sidecar",
    ],
)
def test_both_contracts_callers_make_the_same_lifecycle_decisions(contracts_tree, monkeypatch, caller, case):
    previous = _save_contracts_record(contracts_tree)
    monkeypatch.setattr(ac, "ROOT", contracts_tree)
    monkeypatch.setattr(rg, "ROOT", contracts_tree)
    record_path = ac.contracts_green()
    original = record_path.read_bytes()
    cc.write_sidecar(cc.sidecar_path(record_path), ["stale"], {"stale": {}})
    force = case in {"forced-extra-drift", "failure", "missing-sidecar"}
    if case in {"extra", "roster-drift"}:
        _write(contracts_tree, "extra.txt", "two")
    elif case == "global":
        _write(contracts_tree, "conftest.py", "VALUE = 1\n")
    elif case == "no-git":
        record_path.unlink()
        monkeypatch.setattr(ac, "contracts_closure", lambda root: (None, None))
    spawned = []

    def execute(argv):
        skip = cc.read_selection(Path(argv[argv.index("--closure-skip") + 1]))
        spawned.append(skip)
        assert not cc.sidecar_path(record_path).exists()
        if case != "missing-sidecar":
            tests = previous["closures"]["tests"]
            cc.write_sidecar(
                cc.sidecar_path(record_path),
                list(tests),
                {nodeid: entry for nodeid, entry in tests.items() if nodeid not in skip},
            )
        if case == "forced-extra-drift":
            _write(contracts_tree, "extra.txt", "three")
        elif case == "roster-drift":
            _write(contracts_tree, "rebuild/test_u.py", "VALUE = 1\n")
        return (1, "FAILED rebuild/test_t.py::reads_extra") if case == "failure" else (0, "")

    if caller == "wrapper":
        monkeypatch.setattr(rg, "_run_suite", lambda argv, env: execute(argv))
        result_ok = rg.main(["--force"] if force else []) == 0
    else:

        def spawn(name, argv, **kwargs):
            returncode, stdout = execute(argv)
            return ac._StepResult(name, returncode, stdout, "", 0.1)

        gate = ac._gate_contracts_task(
            "overlap", None, None, spawn, ac._Emitter(), ac._ChildRegistry(), ac.contracts_argv(), force, True
        )
        result_ok = gate.outcome in {"green", "skipped"}
    assert result_ok == (case != "failure")
    record = ac.read_green_record(record_path)
    if case == "unchanged":
        assert not spawned
        assert record_path.read_bytes() == original
        sidecar = cc.read_sidecar(cc.sidecar_path(record_path))
        assert sidecar is not None and sidecar["collected"] == ["stale"]
    elif case in {"failure", "no-git"}:
        assert len(spawned) == 1 and not spawned[0]
        assert record is None
    elif case in {"forced-extra-drift", "roster-drift"}:
        assert len(spawned) == 1
        assert record_path.read_bytes() == original
    else:
        assert len(spawned) == 1 and record is not None
        assert spawned[0] == ({"rebuild/test_u.py::plain"} if case == "extra" else frozenset())
        if case == "missing-sidecar":
            assert "closures" not in record
        else:
            assert set(record["closures"]["tests"]) == set(previous["closures"]["tests"])


def test_queued_contracts_refreshes_selection_after_waiting(contracts_tree, monkeypatch):
    _save_contracts_record(contracts_tree)
    monkeypatch.setattr(ac, "ROOT", contracts_tree)
    planned = cc.prepare_run(contracts_tree)
    assert planned.skippable
    finished = Future()

    def awaited(conform_fut, make_fut):
        assert make_fut is finished
        _write(contracts_tree, "conftest.py", "VALUE = 1\n")
        finished.set_result(None)

    monkeypatch.setattr(ac, "_await_gate_futures", awaited)
    selected = []

    def spawn(name, argv, **kwargs):
        selected.append(cc.read_selection(cc.selection_path(ac.contracts_green())))
        return ac._StepResult(name, 0, "", "", 0.1)

    gate = ac._gate_contracts_task(
        "queue", None, finished, spawn, ac._Emitter(), ac._ChildRegistry(), ac.contracts_argv(), False, True
    )
    assert gate.ok and selected == [frozenset()]
    record = ac.read_green_record(ac.contracts_green())
    assert record is not None and record["fingerprint"] == ac.contracts_fingerprint(contracts_tree)


@pytest.mark.parametrize("caller", ["wrapper", "cycle"])
def test_spawn_exception_clears_the_matching_record_and_propagates(contracts_tree, monkeypatch, caller):
    _save_contracts_record(contracts_tree)
    monkeypatch.setattr(ac, "ROOT", contracts_tree)
    monkeypatch.setattr(rg, "ROOT", contracts_tree)

    def failed(*args, **kwargs):
        raise FileNotFoundError("suite unavailable")

    with pytest.raises(FileNotFoundError, match="suite unavailable"):
        if caller == "wrapper":
            monkeypatch.setattr(rg, "_run_suite", failed)
            rg.main(["--force"])
        else:
            ac._gate_contracts_task(
                "overlap",
                None,
                None,
                failed,
                ac._Emitter(),
                ac._ChildRegistry(),
                ac.contracts_argv(),
                True,
                True,
            )
    assert ac.read_green_record(ac.contracts_green()) is None


def test_an_unpublished_cycle_run_leaves_the_green_record_alone(contracts_tree, monkeypatch):
    _save_contracts_record(contracts_tree)
    monkeypatch.setattr(ac, "ROOT", contracts_tree)
    original = ac.contracts_green().read_bytes()
    gate = ac._gate_contracts_task(
        "overlap",
        None,
        None,
        lambda name, argv, **kwargs: ac._StepResult(name, 1, "", "", 0.1),
        ac._Emitter(),
        ac._ChildRegistry(),
        ac.contracts_argv(),
        True,
        False,
    )
    assert not gate.ok
    assert ac.contracts_green().read_bytes() == original


class TestMerge:
    def test_a_narrowed_run_keeps_the_previous_closures_of_the_tests_it_kept_off(self, synthetic_tree: Path):
        _write(synthetic_tree, "rebuild/test_t.py", "from pkg import a\n")
        previous = {
            "static": {},
            "module_reads": {"pkg/b.py": ["old.yaml"]},
            "tests": {
                "rebuild/test_t.py::kept_off": {"reads": ["k.yaml"], "modules": [], "untraced_inputs": False},
                "rebuild/test_t.py::ran": {"reads": ["stale.yaml"], "modules": [], "untraced_inputs": False},
                "rebuild/test_t.py::gone": {"reads": [], "modules": [], "untraced_inputs": False},
            },
        }
        sidecar = {
            "format": cc.SIDECAR_FORMAT,
            "collected": ["rebuild/test_t.py::kept_off", "rebuild/test_t.py::ran", "rebuild/test_t.py::new"],
            "tests": {
                "rebuild/test_t.py::ran": {
                    "reads": ["fresh.yaml", "pkg/c.py"],
                    "modules": ["pkg/lazy.py"],
                    "kernel": True,
                    "untraced_inputs": False,
                },
                "rebuild/test_t.py::new": {"reads": [], "modules": [], "untraced_inputs": True},
            },
            "module_reads": {"pkg/b.py": ["new.yaml"], "elsewhere.py": ["x"]},
        }
        merged = cc.merge_closures(synthetic_tree, previous, sidecar)
        assert merged is not None
        assert set(merged["tests"]) == {
            "rebuild/test_t.py::kept_off",
            "rebuild/test_t.py::ran",
            "rebuild/test_t.py::new",
        }
        assert merged["tests"]["rebuild/test_t.py::kept_off"]["reads"] == ["k.yaml"]
        assert merged["tests"]["rebuild/test_t.py::ran"]["reads"] == ["fresh.yaml", "pkg/c.py"]
        assert merged["tests"]["rebuild/test_t.py::new"]["untraced_inputs"] is True
        assert merged["tests"]["rebuild/test_t.py::ran"]["kernel"] is True
        assert merged["tests"]["rebuild/test_t.py::kept_off"]["kernel"] is False
        assert set(merged["static"]) == {"rebuild/test_t.py", "pkg/lazy.py", "pkg/c.py", *cc.CONFTEST_PATHS}
        assert merged["tests"]["rebuild/test_t.py::ran"]["modules"] == ["pkg/c.py", "pkg/lazy.py"]
        assert "pkg/sib.py" in merged["static"]["rebuild/test_t.py"]
        assert merged["module_reads"] == {"pkg/b.py": ["new.yaml", "old.yaml"]}

    def test_no_sidecar_means_no_closures(self, synthetic_tree: Path):
        assert cc.merge_closures(synthetic_tree, None, None) is None


class TestRecordPayload:
    def test_extras_outside_the_roster_are_digested_and_a_moved_label_is_named(self, synthetic_tree: Path):
        _write(synthetic_tree, "extra.txt", "one")
        _write(synthetic_tree, "rebuild/test_t.py", "")
        sidecar = synthetic_tree / "sidecar.json"
        cc.write_sidecar(
            sidecar,
            ["rebuild/test_t.py::t"],
            {"rebuild/test_t.py::t": {"reads": ["extra.txt"], "modules": [], "untraced_inputs": False}},
        )
        roster = {"rebuild/test_t.py": "t"}
        payload = cc.record_payload(synthetic_tree, dict(roster), roster, None, sidecar)
        assert payload.moved == ()
        assert payload.closures is not None
        assert set(payload.files) == {"rebuild/test_t.py", "extra.txt", *cc.CONFTEST_PATHS}
        assert payload.files["extra.txt"] == ac._sha256_path(synthetic_tree / "extra.txt")

        widened = cc.current_files(synthetic_tree, roster, {"closures": payload.closures})
        assert widened == payload.files
        _write(synthetic_tree, "extra.txt", "two")
        later = cc.record_payload(synthetic_tree, widened, roster, {"closures": payload.closures}, sidecar)
        assert later.moved == ("extra.txt",)


class TestTheGateNarrows:
    """The gate writes the selection the record supports, runs the lane with it, and merges what the lane recorded. The suite is stubbed to write a sidecar as the real one does."""

    @pytest.fixture
    def contracts_store(self, tmp_path, monkeypatch):
        store = tmp_path / "out" / "rebuild-contracts-green.json"
        monkeypatch.setattr(cycle_paths, "REBUILD_CONTRACTS_GREEN", store)
        return store

    def _closures(self, monkeypatch, files_before, files_after):
        calls = iter([files_before, files_after])

        def closure(root):
            files = next(calls)
            return ac._digest_lines([f"{k}\t{v}" for k, v in files.items()]), files

        monkeypatch.setattr(ac, "contracts_closure", closure)

    def test_a_narrowed_run_keeps_off_what_the_record_checked_and_records_the_merge(
        self, contracts_store, monkeypatch, capsys
    ):
        before = {**BASE_FILES, "a.yaml": "9"}
        stale = _record(BASE_FILES, TESTS, dict(STATIC))
        ac.record_green(contracts_store, "old", files=stale["files"], closures=stale["closures"])
        self._closures(monkeypatch, before, dict(before))
        spawned = []

        def fake_run(argv, env):
            spawned.append(list(argv))
            skip = cc.read_selection(Path(argv[argv.index("--closure-skip") + 1]))
            assert skip == {"rebuild/test_t.py::reads_b", "rebuild/test_u.py::imports_m"}
            cc.write_sidecar(
                Path(argv[argv.index("--closure-record") + 1]),
                list(TESTS),
                {
                    nodeid: {"reads": ["a.yaml", "c.yaml"], "modules": [], "untraced_inputs": False}
                    for nodeid in TESTS
                    if nodeid not in skip
                },
            )
            return 0, ""

        monkeypatch.setattr(rg, "_run_suite", fake_run)
        assert rg.main([]) == 0
        assert spawned[0] == ac.contracts_argv()
        out = capsys.readouterr().out
        assert "2 of 4 recorded tests run" in out
        assert "per-test closures recorded" in out
        record = ac.read_green_record(contracts_store)
        assert record is not None
        assert record["files"]["a.yaml"] == "9"
        assert record["files"]["c.yaml"] == "absent"
        tests = record["closures"]["tests"]
        assert tests["rebuild/test_t.py::reads_a"]["reads"] == ["a.yaml", "c.yaml"]
        assert tests["rebuild/test_t.py::reads_b"]["reads"] == ["b.yaml"]
        assert tests["rebuild/test_t.py::spawns"]["reads"] == ["a.yaml", "c.yaml"]

    def test_force_runs_the_whole_lane(self, contracts_store, monkeypatch, capsys):
        stale = _record(BASE_FILES, TESTS, dict(STATIC))
        ac.record_green(contracts_store, "old", files=stale["files"], closures=stale["closures"])
        self._closures(monkeypatch, dict(BASE_FILES), dict(BASE_FILES))

        def fake_run(argv, env):
            if "--closure-skip" in argv:
                assert cc.read_selection(Path(argv[argv.index("--closure-skip") + 1])) == frozenset()
            return 0, ""

        monkeypatch.setattr(rg, "_run_suite", fake_run)
        assert rg.main(["--force"]) == 0
        assert "forced run of the whole suite" in capsys.readouterr().out

    def test_a_green_without_a_sidecar_records_no_closures(self, contracts_store, monkeypatch, capsys):
        self._closures(monkeypatch, dict(BASE_FILES), dict(BASE_FILES))
        monkeypatch.setattr(rg, "_run_suite", lambda argv, env: (0, ""))
        assert rg.main([]) == 0
        record = ac.read_green_record(contracts_store)
        assert record is not None
        assert "closures" not in record
        assert record["files"] == BASE_FILES
        assert "no per-test closures recorded yet" in capsys.readouterr().out

    def test_an_extra_that_moved_during_the_run_withholds_the_green(
        self, contracts_store, monkeypatch, capsys
    ):
        stale = _record(BASE_FILES, TESTS, dict(STATIC))
        ac.record_green(contracts_store, "old", files=stale["files"], closures=stale["closures"])
        self._closures(monkeypatch, {**BASE_FILES, "a.yaml": "9"}, {**BASE_FILES, "a.yaml": "9"})
        monkeypatch.setattr(
            cc, "record_payload", lambda *args: cc.RecordPayload(files={}, closures=None, moved=("a.yaml",))
        )
        monkeypatch.setattr(rg, "_run_suite", lambda argv, env: (0, ""))
        assert rg.main([]) == 0
        assert "changed while the suite ran" in capsys.readouterr().out
        record = ac.read_green_record(contracts_store)
        assert record is not None
        assert record["fingerprint"] == "old"


def test_the_lane_argv_names_the_closure_files_beside_the_record(tmp_path, monkeypatch):
    store = tmp_path / "rebuild-contracts-green.json"
    monkeypatch.setattr(cycle_paths, "REBUILD_CONTRACTS_GREEN", store)
    argv = ac.contracts_argv()
    assert argv[argv.index("--closure-skip") + 1] == str(tmp_path / "rebuild-contracts-selection.json")
    assert argv[argv.index("--closure-record") + 1] == str(tmp_path / "rebuild-contracts-closures.json")


def test_the_lane_key_is_the_digest_of_its_labels(tmp_path):
    """The selection compares label maps and the skip compares keys, so the key must be the digest of the label map and nothing else."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(tmp_path, "rebuild/test_x.py", "")
    _write(tmp_path, "glyph_data/runes/qsX.yaml", "rune: qsX\n")
    key, labels = ac.contracts_closure(tmp_path)
    assert key is not None and labels is not None
    assert key == ac.contracts_fingerprint(tmp_path)
    assert key == ac._digest_lines([f"{label}\t{digest}" for label, digest in labels.items()])
    assert {"rebuild/test_x.py", "glyph_data/runes/qsX.yaml", "fonts"} <= labels.keys()


def test_the_cycle_plan_names_the_narrowing(tmp_path, monkeypatch):
    store = tmp_path / "rebuild-contracts-green.json"
    monkeypatch.setattr(cycle_paths, "REBUILD_CONTRACTS_GREEN", store)
    stale = _record(BASE_FILES, TESTS, dict(STATIC))
    ac.record_green(store, "old", files=stale["files"], closures=stale["closures"])
    before = {**BASE_FILES, "a.yaml": "9"}
    monkeypatch.setattr(ac, "contracts_closure", lambda root: ("new", dict(before)))
    monkeypatch.setattr(ac, "contracts_fingerprint", lambda root: "new")
    monkeypatch.setattr(cc, "current_files", lambda root, roster, record: dict(roster))
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        skip_gates=False,
        first_run=False,
        short_id="abc",
        contracts_run=cc.prepare_run(ac.ROOT),
        contracts_note=cc.select(ac.read_green_record(store), before).describe(),
    )
    assert plan.contracts_run is not None
    assert sorted(plan.contracts_run.selection.skip) == [
        "rebuild/test_t.py::reads_b",
        "rebuild/test_u.py::imports_m",
    ]
    assert "2 of 4 recorded tests run" in plan.note_for("gate:rebuild-contracts")
    cc.start_run(plan.contracts_run)
    assert cc.read_selection(cc.selection_path(store)) == plan.contracts_run.selection.skip


CHILD_CONFTEST = """
import pytest
from pathlib import Path

REPO_ROOT = Path({root!r})


@pytest.fixture(scope="session")
def shared():
    return (REPO_ROOT / "rebuild" / "review" / "fixtures" / "manifest.json").read_bytes()
"""

CHILD_TESTS = '''
"""One test per recorded fact: a plain read, a font mapped through HarfBuzz, a fixture's read credited to two requesters, a hermetic git child, a child named as the kernel is, an ordinary child, a multiprocessing child, and a dynamic import, which `importlib.import_module` performs without the import audit event a statement raises, so the module's source shows up as a read."""

import multiprocessing
import subprocess
from pathlib import Path

REPO_ROOT = Path({root!r})


def test_plain_read():
    assert (REPO_ROOT / "glyph_data" / "runes" / "qsPea.yaml").read_bytes()


def test_font_blob():
    import uharfbuzz as hb

    assert len(hb.Blob.from_file_path(str(REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf")))


def test_first_fixture_user(shared):
    assert shared


def test_second_fixture_user(shared):
    assert shared


def test_hermetic_git_child():
    subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True)


def test_kernel_child():
    kernel = Path(__file__).parent / "ams-m1-kernel"
    kernel.write_text("#!/bin/sh\\nexit 0\\n")
    kernel.chmod(0o755)
    subprocess.run([str(kernel)], check=True)


def test_ordinary_child():
    subprocess.run(["true"], check=True)


def test_multiprocessing_child():
    process = multiprocessing.get_context("spawn").Process(target=print, args=("child",))
    process.start()
    process.join()


def test_dynamic_import():
    import importlib

    assert importlib.import_module("rebuild.tools.memory_tally")
'''


def _child(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *args: str):
    """The child's kernel is a script named like the kernel binary under the pytester root, because `kernel_child` decides by argv. Building the real kernel here, after pytester has pointed `HOME` at a scratch directory, would make cargo rebuild it against an empty registry, and the next build under the real `HOME` would rebuild it again."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    pytester.makeconftest(CHILD_CONFTEST.format(root=str(REPO_ROOT)))
    pytester.makepyfile(test_child=CHILD_TESTS.format(root=str(REPO_ROOT)))
    return pytester.runpytest_subprocess(
        "-p", "rebuild.conftest", "-p", "no:cacheprovider", "--rootdir", str(pytester.path), *args
    )


class TestTheRecorderEndToEnd:
    @pytest.mark.parametrize("workers", ["0", "2"])
    def test_every_recorded_fact_is_written_to_the_sidecar(self, pytester, monkeypatch, tmp_path, workers):
        sidecar = tmp_path / "closures.json"
        result = _child(pytester, monkeypatch, "-n", workers, "--closure-record", str(sidecar))
        result.assert_outcomes(passed=9)
        payload = cc.read_sidecar(sidecar)
        assert payload is not None
        tests = payload["tests"]
        assert sorted(payload["collected"]) == sorted(tests)
        assert "glyph_data/runes/qsPea.yaml" in tests["test_child.py::test_plain_read"]["reads"]
        assert not tests["test_child.py::test_plain_read"]["untraced_inputs"]
        assert "rebuild/review/fixtures/mini/M1.otf" in tests["test_child.py::test_font_blob"]["reads"]
        assert not tests["test_child.py::test_font_blob"]["untraced_inputs"]
        for nodeid in ("test_child.py::test_first_fixture_user", "test_child.py::test_second_fixture_user"):
            assert MANIFEST in tests[nodeid]["reads"], nodeid
            assert not tests[nodeid]["untraced_inputs"]
        assert not tests["test_child.py::test_hermetic_git_child"]["untraced_inputs"]
        assert not tests["test_child.py::test_hermetic_git_child"]["kernel"]
        assert tests["test_child.py::test_kernel_child"]["kernel"]
        assert not tests["test_child.py::test_kernel_child"]["untraced_inputs"]
        assert tests["test_child.py::test_ordinary_child"]["untraced_inputs"]
        assert tests["test_child.py::test_multiprocessing_child"]["untraced_inputs"]
        assert "rebuild/tools/memory_tally.py" in tests["test_child.py::test_dynamic_import"]["reads"]

    def test_a_selection_file_keeps_its_tests_off_and_they_stay_collected(
        self, pytester, monkeypatch, tmp_path
    ):
        sidecar = tmp_path / "closures.json"
        selection = tmp_path / "selection.json"
        cc.write_selection(
            selection, ["test_child.py::test_plain_read", "test_child.py::test_ordinary_child"]
        )
        result = _child(
            pytester,
            monkeypatch,
            "-n",
            "0",
            "--closure-record",
            str(sidecar),
            "--closure-skip",
            str(selection),
        )
        result.assert_outcomes(passed=7, deselected=2)
        payload = cc.read_sidecar(sidecar)
        assert payload is not None
        assert "test_child.py::test_plain_read" in payload["collected"]
        assert "test_child.py::test_plain_read" not in payload["tests"]

    def test_without_the_option_no_sidecar_is_written(self, pytester, monkeypatch, tmp_path):
        result = _child(pytester, monkeypatch, "-n", "0")
        result.assert_outcomes(passed=9)
        assert not list(tmp_path.glob("*.json"))


# Every repo file each conftest imports directly. Both conftests are in every test's closure, so a new conftest import adds an input to every test. The sets are literals so that adding one shows up as a diff to review.
CONFTEST_EDGES = {
    "conftest.py": {
        "rebuild/tools/cycle_timings.py",
        "rebuild/tools/memory_budget.py",
        "rebuild/tools/peak_rss.py",
        "rebuild/tools/pyright_gate.py",
        "rebuild/tools/site_fonts.py",
        "test/test_shaping.py",
    },
    "rebuild/conftest.py": {
        "rebuild/tools/closure_record.py",
        "rebuild/tools/cycle_paths.py",
        "rebuild/tools/cycle_timings.py",
        "rebuild/tools/standing_client.py",
    },
}


class TestTheConftestsStayLeaves:
    """`closure_of` adds both conftests' static import closures to every test's closure. If a conftest reached rebuild/pipeline/ or rebuild/review/ through any chain of imports, that tree would be in every closure and no pipeline edit could let a test be skipped. The conftests import only the leaf modules in `CONFTEST_EDGES`; fixtures that need review-tree modules, the mini bundle's pin among them, load them through `announced_import`."""

    @pytest.mark.parametrize("conftest", cc.CONFTEST_PATHS)
    def test_neither_conftest_reaches_the_pipeline(self, conftest):
        closure = cc.ImportGraph(REPO_ROOT).closure(conftest)
        assert sorted(rel for rel in closure if rel.startswith("rebuild/pipeline/")) == []

    @pytest.mark.parametrize("conftest", cc.CONFTEST_PATHS)
    def test_neither_conftest_reaches_the_review_tree(self, conftest):
        closure = cc.ImportGraph(REPO_ROOT).closure(conftest)
        assert sorted(rel for rel in closure if rel.startswith("rebuild/review/")) == []

    @pytest.mark.parametrize("conftest", cc.CONFTEST_PATHS)
    def test_each_conftests_edge_set_is_the_checked_in_one(self, conftest):
        assert set(cc.ImportGraph(REPO_ROOT).edges(conftest)) == CONFTEST_EDGES[conftest]


ANNOUNCE_CONFTEST = """
import importlib

import pytest

from rebuild import conftest as lane


@pytest.fixture(scope="session")
def announced():
    return lane.announced_import("rebuild.tools.site_fonts")


@pytest.fixture(scope="session")
def silent():
    return importlib.import_module("rebuild.tools.site_fonts")
"""

ANNOUNCE_TESTS = """
def test_uses_announced(announced):
    assert announced.font_paths


def test_uses_silent(silent):
    assert silent.font_paths
"""

IMPORTER_TESTS = """
import rebuild.tools.site_fonts


def test_imports_it_at_collection():
    assert rebuild.tools.site_fonts.font_paths
"""


class TestTheAnnouncedImport:
    """A session fixture that imports a module at setup adds that module to a requesting test's closure only through `announced_import`. Both load orders are tested. In the first, the fixture itself loads the module first, so its source read would be recorded anyway. In the second, another test module imports it at collection, so no import event or read happens during the fixture's setup. The real suite runs in this order, because collection imports every test module before any fixture runs. The silent fixture is the control: in both orders its requesting test records neither the module nor a read, which is the unsafe skip `announced_import` prevents."""

    @pytest.mark.parametrize("loaded_at_collection", [False, True])
    def test_the_announced_module_is_included_in_every_requesters_closure(
        self, pytester, monkeypatch, tmp_path, loaded_at_collection
    ):
        monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
        pytester.makeconftest(ANNOUNCE_CONFTEST)
        files = {"test_announced": ANNOUNCE_TESTS}
        if loaded_at_collection:
            files["test_importer"] = IMPORTER_TESTS
        pytester.makepyfile(**files)
        sidecar = tmp_path / "closures.json"
        result = pytester.runpytest_subprocess(
            "-p",
            "rebuild.conftest",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(pytester.path),
            "-n",
            "0",
            "--closure-record",
            str(sidecar),
        )
        result.assert_outcomes(passed=3 if loaded_at_collection else 2)
        payload = cc.read_sidecar(sidecar)
        assert payload is not None
        tests = payload["tests"]
        assert "rebuild/tools/site_fonts.py" in tests["test_announced.py::test_uses_announced"]["modules"]
        silent = tests["test_announced.py::test_uses_silent"]
        assert "rebuild/tools/site_fonts.py" not in silent["modules"]
        assert "rebuild/tools/site_fonts.py" not in silent["reads"]
