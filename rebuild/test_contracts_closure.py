"""The contracts lane's per-test closure, pinned at every seam: the static import walk over a synthetic tree, the selection rule over a hand-written record, the merge of a run's sidecar into the record it narrows against, the wrapper's narrowing and recording through the gate, and — end to end, in a child pytest under `-p rebuild.conftest` — that the audit guard records reads, imports, fixture setups and spawns the way the selection assumes, and honors a selection file. The child runs as a subprocess for the reason test_lanes gives: the guard is a `sys.addaudithook`, which cannot be uninstalled."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import contracts_closure as cc
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
    def test_everything_else_is_unclosable(self, argv):
        assert not cc.hermetic_child(argv)


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
    """A miniature repo with every import shape the walk has to follow: a package, an absolute `from` import, a relative one, a nested import inside a function, a `TYPE_CHECKING` import, a bare sibling import, and a `test/` module reached by name alone."""
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
}
TESTS = {
    "rebuild/test_t.py::reads_a": {"reads": ["a.yaml"], "modules": [], "unclosable": False},
    "rebuild/test_t.py::reads_b": {"reads": ["b.yaml"], "modules": [], "unclosable": False},
    "rebuild/test_t.py::spawns": {"reads": [], "modules": [], "unclosable": True},
    "rebuild/test_u.py::imports_m": {"reads": [], "modules": ["m.py"], "unclosable": False},
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

    def test_an_unclosable_test_always_runs(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        assert "rebuild/test_t.py::spawns" not in cc.select(record, {**BASE_FILES, "a.yaml": "9"}).skip

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

    def test_nothing_moved_keeps_every_closable_test_off(self):
        record = _record(BASE_FILES, TESTS, dict(STATIC))
        selection = cc.select(record, dict(BASE_FILES))
        assert selection.skip == {nodeid for nodeid, entry in TESTS.items() if not entry["unclosable"]}

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


class TestMerge:
    def test_a_narrowed_run_keeps_the_previous_closures_of_the_tests_it_kept_off(self, synthetic_tree: Path):
        _write(synthetic_tree, "rebuild/test_t.py", "from pkg import a\n")
        previous = {
            "static": {},
            "module_reads": {"pkg/b.py": ["old.yaml"]},
            "tests": {
                "rebuild/test_t.py::kept_off": {"reads": ["k.yaml"], "modules": [], "unclosable": False},
                "rebuild/test_t.py::ran": {"reads": ["stale.yaml"], "modules": [], "unclosable": False},
                "rebuild/test_t.py::gone": {"reads": [], "modules": [], "unclosable": False},
            },
        }
        sidecar = {
            "format": cc.SIDECAR_FORMAT,
            "collected": ["rebuild/test_t.py::kept_off", "rebuild/test_t.py::ran", "rebuild/test_t.py::new"],
            "tests": {
                "rebuild/test_t.py::ran": {
                    "reads": ["fresh.yaml", "pkg/c.py"],
                    "modules": ["pkg/lazy.py"],
                    "unclosable": False,
                },
                "rebuild/test_t.py::new": {"reads": [], "modules": [], "unclosable": True},
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
        assert merged["tests"]["rebuild/test_t.py::new"]["unclosable"] is True
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
            {"rebuild/test_t.py::t": {"reads": ["extra.txt"], "modules": [], "unclosable": False}},
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
    """The wrapper writes the selection the record proves, spawns the lane against it, and merges what the lane recorded — driven with the suite stubbed to write a sidecar the way the real one does."""

    @pytest.fixture
    def contracts_store(self, tmp_path, monkeypatch):
        store = tmp_path / "out" / "rebuild-contracts-green.json"
        monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", store)
        monkeypatch.setattr(
            ac, "REBUILD_VALIDATORS_GREEN", tmp_path / "out" / "rebuild-validators-green.json"
        )
        monkeypatch.setattr(rg, "m1_tables_stamped", lambda: True)
        return store

    def _closures(self, monkeypatch, files_before, files_after):
        calls = {"contracts": iter([files_before, files_after]), "validators": iter([{"v": "1"}, {"v": "1"}])}

        def closure(root, lane):
            files = next(calls[lane])
            return ac._digest_lines([f"{k}\t{v}" for k, v in files.items()]), files

        monkeypatch.setattr(rg, "rebuild_lane_closure", closure)

    def test_a_narrowed_run_keeps_off_what_the_record_proves_and_records_the_merge(
        self, contracts_store, monkeypatch, capsys
    ):
        before = {**BASE_FILES, "a.yaml": "9"}
        stale = _record(BASE_FILES, TESTS, dict(STATIC))
        ac.record_green(contracts_store, "old", files=stale["files"], closures=stale["closures"])
        self._closures(monkeypatch, before, dict(before))
        spawned = []

        def fake_run(argv, env):
            lane = argv[argv.index("--lane") + 1]
            spawned.append(list(argv))
            if lane == "contracts":
                skip = cc.read_selection(Path(argv[argv.index("--closure-skip") + 1]))
                assert skip == {"rebuild/test_t.py::reads_b", "rebuild/test_u.py::imports_m"}
                cc.write_sidecar(
                    Path(argv[argv.index("--closure-record") + 1]),
                    list(TESTS),
                    {
                        nodeid: {"reads": ["a.yaml", "c.yaml"], "modules": [], "unclosable": False}
                        for nodeid in TESTS
                        if nodeid not in skip
                    },
                )
            return 0, ""

        monkeypatch.setattr(rg, "_run_suite", fake_run)
        assert rg.main([]) == 0
        assert spawned[0] == ac.rebuild_lane_argv("contracts")
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
        assert "--force runs the whole lane" in capsys.readouterr().out

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
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", store)
    argv = ac.rebuild_lane_argv("contracts")
    assert argv[argv.index("--closure-skip") + 1] == str(tmp_path / "rebuild-contracts-selection.json")
    assert argv[argv.index("--closure-record") + 1] == str(tmp_path / "rebuild-contracts-closures.json")
    assert "--closure-skip" not in ac.rebuild_lane_argv("validators")


def test_the_lane_key_is_the_digest_of_its_labels(tmp_path):
    """The selection diffs the label map and the skip compares the key, and the two agree only because the key is nothing but the map's digest."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(tmp_path, "rebuild/test_x.py", "")
    _write(tmp_path, "glyph_data/runes/qsX.yaml", "rune: qsX\n")
    key, labels = ac.rebuild_lane_closure(tmp_path, "contracts")
    assert key is not None and labels is not None
    assert key == ac.rebuild_lane_fingerprint(tmp_path, "contracts")
    assert key == ac._digest_lines([f"{label}\t{digest}" for label, digest in labels.items()])
    assert {"rebuild/test_x.py", "glyph_data/runes/qsX.yaml", "fonts"} <= labels.keys()


def test_the_cycle_plan_names_the_narrowing(tmp_path, monkeypatch):
    store = tmp_path / "rebuild-contracts-green.json"
    monkeypatch.setattr(ac, "REBUILD_CONTRACTS_GREEN", store)
    stale = _record(BASE_FILES, TESTS, dict(STATIC))
    ac.record_green(store, "old", files=stale["files"], closures=stale["closures"])
    before = {**BASE_FILES, "a.yaml": "9"}
    monkeypatch.setattr(ac, "rebuild_lane_closure", lambda root, lane: ("new", dict(before)))
    monkeypatch.setattr(ac, "rebuild_lane_fingerprint", lambda root, lane: "new")
    monkeypatch.setattr(cc, "current_files", lambda root, roster, record: dict(roster))
    plan = ac.build_plan(
        verdicts=None,
        no_carry=True,
        carry_out=None,
        snapshot_dir=tmp_path / "snap",
        skip_gates=False,
        first_run=False,
        short_id="abc",
        contracts_skip=sorted(cc.select(ac.read_green_record(store), before).skip),
        contracts_note=cc.select(ac.read_green_record(store), before).describe(),
    )
    assert plan.contracts_skip == ["rebuild/test_t.py::reads_b", "rebuild/test_u.py::imports_m"]
    assert "2 of 4 recorded tests run" in plan.note_for("gate:rebuild-contracts")
    ac._write_contracts_selection(plan)
    assert cc.read_selection(cc.selection_path(store)) == set(plan.contracts_skip)


CHILD_CONFTEST = """
import pytest
from pathlib import Path

REPO_ROOT = Path({root!r})


@pytest.fixture(scope="session")
def shared():
    return (REPO_ROOT / "rebuild" / "review" / "fixtures" / "manifest.json").read_bytes()
"""

CHILD_TESTS = '''
"""One test per recorded fact: a plain read, a font mapped through HarfBuzz, a fixture's read credited to two requesters, a hermetic git child, an ordinary child, a multiprocessing child, and a dynamic import, which `importlib.import_module` performs without the import audit event a statement raises, so the module's source shows up as a read."""

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


def test_ordinary_child():
    subprocess.run(["true"], check=True)


def test_multiprocessing_child():
    process = multiprocessing.get_context("spawn").Process(target=print, args=("child",))
    process.start()
    process.join()


def test_dynamic_import():
    import importlib

    assert importlib.import_module("rebuild.tools.pile_tally")
'''


def _child(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    pytester.makeconftest(CHILD_CONFTEST.format(root=str(REPO_ROOT)))
    pytester.makepyfile(test_child=CHILD_TESTS.format(root=str(REPO_ROOT)))
    return pytester.runpytest_subprocess(
        "-p", "rebuild.conftest", "-p", "no:cacheprovider", "--rootdir", str(pytester.path), *args
    )


class TestTheRecorderEndToEnd:
    @pytest.mark.parametrize("workers", ["0", "2"])
    def test_every_recorded_fact_lands_in_the_sidecar(self, pytester, monkeypatch, tmp_path, workers):
        sidecar = tmp_path / "closures.json"
        result = _child(
            pytester, monkeypatch, "--lane", "contracts", "-n", workers, "--closure-record", str(sidecar)
        )
        result.assert_outcomes(passed=8)
        payload = cc.read_sidecar(sidecar)
        assert payload is not None
        tests = payload["tests"]
        assert sorted(payload["collected"]) == sorted(tests)
        assert "glyph_data/runes/qsPea.yaml" in tests["test_child.py::test_plain_read"]["reads"]
        assert not tests["test_child.py::test_plain_read"]["unclosable"]
        assert "rebuild/review/fixtures/mini/M1.otf" in tests["test_child.py::test_font_blob"]["reads"]
        assert not tests["test_child.py::test_font_blob"]["unclosable"]
        for nodeid in ("test_child.py::test_first_fixture_user", "test_child.py::test_second_fixture_user"):
            assert MANIFEST in tests[nodeid]["reads"], nodeid
            assert not tests[nodeid]["unclosable"]
        assert not tests["test_child.py::test_hermetic_git_child"]["unclosable"]
        assert tests["test_child.py::test_ordinary_child"]["unclosable"]
        assert tests["test_child.py::test_multiprocessing_child"]["unclosable"]
        assert "rebuild/tools/pile_tally.py" in tests["test_child.py::test_dynamic_import"]["reads"]

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
            "--lane",
            "contracts",
            "-n",
            "0",
            "--closure-record",
            str(sidecar),
            "--closure-skip",
            str(selection),
        )
        result.assert_outcomes(passed=6, deselected=2)
        payload = cc.read_sidecar(sidecar)
        assert payload is not None
        assert "test_child.py::test_plain_read" in payload["collected"]
        assert "test_child.py::test_plain_read" not in payload["tests"]

    def test_without_the_option_no_sidecar_is_written(self, pytester, monkeypatch, tmp_path):
        result = _child(pytester, monkeypatch, "--lane", "contracts", "-n", "0")
        result.assert_outcomes(passed=8)
        assert not list(tmp_path.glob("*.json"))
