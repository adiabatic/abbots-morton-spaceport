"""Tests for the live-artifact guard in `rebuild/conftest.py`: which paths count as live artifacts, which test files the guard governs, and, in a child pytest, that a rebuild test reading a live artifact fails. The child runs in a subprocess because the guard is a `sys.addaudithook`, which cannot be uninstalled, so an in-process run would leave its hook in place for every test after it."""

import os
from pathlib import Path

import pytest

from rebuild.conftest import collect_ignore, governs, is_live_artifact_path
from rebuild.tools import contracts_closure

pytest_plugins = ("pytester",)

REPO_ROOT = Path(__file__).resolve().parents[1]

CHILD_TESTS = '''
"""Two tests spanning the whole rule: a bare live read, which the guard fails, and a read of a checked-in fixture, which must stay legal."""

import os
from pathlib import Path

REPO_ROOT = Path({root!r})


def test_bare_live_read():
    assert os.listdir(REPO_ROOT / "rebuild" / "out")


def test_checked_in_fixture_read():
    assert (REPO_ROOT / "rebuild" / "review" / "fixtures" / "manifest.json").read_bytes()
'''


def _child(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *args: str):
    """Run the two-test file above in a fresh pytest with this conftest loaded as a plugin. rebuild/ is a namespace package with no __init__.py, so `PYTHONPATH` is needed for `-p rebuild.conftest` to resolve from the child's working directory."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    pytester.makepyfile(test_child=CHILD_TESTS.format(root=str(REPO_ROOT)))
    return pytester.runpytest_subprocess("-p", "rebuild.conftest", "-p", "no:cacheprovider", *args)


class TestGovernedScope:
    def test_a_rebuild_module_is_governed(self):
        assert governs(REPO_ROOT / "rebuild" / "test_surface.py")

    def test_the_repos_other_suites_are_left_alone(self):
        assert not governs(REPO_ROOT / "test" / "test_shaping.py")
        assert not governs(REPO_ROOT / "site" / "index.html")

    def test_a_collection_outside_the_repo_is_governed(self, tmp_path: Path):
        assert governs(tmp_path / "test_child.py")


class TestForbiddenPaths:
    @pytest.mark.parametrize(
        "relative",
        [
            "rebuild/out/m1/M1.otf",
            "rebuild/out/m1/divergence-audit.tsv",
            "rebuild/out/review/manifest.json",
            "rebuild/out",
            "tmp/review-triage.yaml",
            "tmp/scratch.txt",
            "var/review-pre-abc1234/manifest.json",
            "var/build-logs/latest/plan.txt",
            "var/keep/notes.md",
            "rebuild/evidence/anything.json",
            "rebuild/review-facts-pins.json",
            "verdicts-autosave.json",
            "verdicts-journal.ndjson",
            "verdicts-carried-abc1234.json",
        ],
    )
    def test_the_live_trees_are_forbidden(self, relative: str):
        assert is_live_artifact_path(REPO_ROOT / relative)

    @pytest.mark.parametrize(
        "relative",
        [
            "rebuild/review/fixtures/manifest.json",
            "rebuild/review/jstests/state.test.js",
            "rebuild/review/build.py",
            "rebuild/m1-divergences.yaml",
            "rebuild/standing-approvals.yaml",
            "glyph_data/quikscript.yaml",
            "glyph_data/runes/qsPea.yaml",
            "site/AbbotsMortonSpaceportSansSenior-Regular.otf",
        ],
    )
    def test_checked_in_inputs_are_allowed(self, relative: str):
        assert not is_live_artifact_path(REPO_ROOT / relative)

    def test_a_tmp_path_of_our_own_is_allowed(self, tmp_path: Path):
        assert not is_live_artifact_path(tmp_path / "out" / "m1" / "M1.otf")

    @pytest.mark.parametrize("candidate", [5, None, object()])
    def test_a_non_path_argument_is_not_a_path(self, candidate: object):
        assert not is_live_artifact_path(candidate)

    def test_bytes_paths_are_decoded(self):
        assert is_live_artifact_path(os.fsencode(str(REPO_ROOT / "rebuild" / "out" / "m1")))

    def test_a_relative_path_resolves_against_the_working_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.chdir(REPO_ROOT)
        assert is_live_artifact_path("rebuild/out/m1/M1.otf")
        monkeypatch.chdir(tmp_path)
        assert not is_live_artifact_path("rebuild/out/m1/M1.otf")


class TestGuardEndToEnd:
    @pytest.mark.parametrize("workers", ["0", "2", "auto"])
    def test_the_bare_read_fails_and_the_fixture_read_passes_at_each_worker_mode(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, workers: str
    ):
        monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
        result = _child(pytester, monkeypatch, "-n", workers)
        result.assert_outcomes(passed=1, failed=1)
        result.stdout.fnmatch_lines(["*ContractsLaneViolation*"])
        if workers != "0":
            expected = "3" if workers == "auto" else workers
            result.stdout.fnmatch_lines([f"*{expected} workers*2 items*"])

    @pytest.mark.parametrize("choice", ["contracts", "all"])
    def test_the_compatibility_option_keeps_the_same_collection_and_guard(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, choice: str
    ):
        result = _child(pytester, monkeypatch, "--lane", choice, "-n", "0")
        result.assert_outcomes(passed=1, failed=1)
        result.stdout.fnmatch_lines(["*ContractsLaneViolation*"])


def _mixed_child(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *args: str):
    """Collect a synthetic rebuild/ and font-test tree through the plugin, retaining the live-path boundary so live reads are guarded only in the rebuild file."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    pytester.makeconftest("""
from pathlib import Path
from rebuild import conftest as contracts

contracts.REPO_ROOT = Path(__file__).resolve().parent
contracts.REBUILD_DIR = contracts.REPO_ROOT / "rebuild"
""")
    pytester.makepyfile(
        **{
            "rebuild/test_contract": CHILD_TESTS.format(root=str(REPO_ROOT)),
            "test/test_font": f"""
from pathlib import Path

def test_live_read_is_outside_the_rebuild_guard():
    assert Path({str(REPO_ROOT / "rebuild" / "review-facts-pins.json")!r}).read_bytes()
""",
        }
    )
    return pytester.runpytest_subprocess(
        "-p",
        "rebuild.conftest",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(pytester.path),
        "-n",
        "0",
        *args,
    )


class TestCollectionModes:
    @pytest.mark.parametrize(
        ("targets", "passed"),
        [
            (("rebuild",), 1),
            (("rebuild/test_contract.py",), 1),
            (("rebuild", "test"), 2),
            ((".",), 2),
        ],
    )
    def test_collection_guards_the_rebuild_tests_and_leaves_the_font_test_alone(
        self,
        pytester: pytest.Pytester,
        monkeypatch: pytest.MonkeyPatch,
        targets: tuple[str, ...],
        passed: int,
    ):
        result = _mixed_child(pytester, monkeypatch, *targets)
        result.assert_outcomes(passed=passed, failed=1)
        result.stdout.fnmatch_lines(["*ContractsLaneViolation*"])

    def test_a_selection_file_deselects_and_records_only_the_rebuild_items(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        selection = tmp_path / "selection.json"
        sidecar = tmp_path / "closures.json"
        rebuild_id = "rebuild/test_contract.py::test_bare_live_read"
        font_id = "test/test_font.py::test_live_read_is_outside_the_rebuild_guard"
        contracts_closure.write_selection(selection, [rebuild_id, font_id])
        result = _mixed_child(
            pytester,
            monkeypatch,
            "rebuild",
            "test",
            "--closure-skip",
            str(selection),
            "--closure-record",
            str(sidecar),
        )
        result.assert_outcomes(passed=2, deselected=1)
        payload = contracts_closure.read_sidecar(sidecar)
        assert payload is not None
        assert rebuild_id in payload["collected"]
        assert rebuild_id not in payload["tests"]
        assert font_id not in payload["collected"]
        assert font_id not in payload["tests"]


def test_the_collection_walk_leaves_the_crate_and_the_build_output_alone():
    """Collection under rebuild/ skips two subtrees: the crate, named from `contracts_closure.KERNEL_PREFIX`, and the build output. Together they hold nearly every entry a worker would otherwise visit. Every other subtree is still walked, so a test file in a new subtree is found."""
    assert set(collect_ignore) == {Path(contracts_closure.KERNEL_PREFIX).name, "out"}
    assert all("/" not in name for name in collect_ignore)
