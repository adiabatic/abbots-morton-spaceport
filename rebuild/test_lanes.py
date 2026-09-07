"""The live-artifact guard's own tests: that the forbidden-path rule draws the line where the build's output actually starts, that the guard governs the rebuild suite and leaves the repo's other suites alone, and — end to end, in a child pytest — that a rebuild test which reaches for a live artifact fails instead of quietly succeeding, whether or not the run named the suite's lane. The child runs as a subprocess rather than in-process because the guard is a `sys.addaudithook`, which cannot be uninstalled: a same-process rehearsal would leave this session's own hook armed against whatever ran next."""

import os
from pathlib import Path

import pytest

from rebuild.conftest import LANES, governs, is_live_artifact_path

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
    """Runs the two-test file above under a fresh pytest with this conftest force-loaded as a plugin. rebuild/ is a namespace package with no __init__.py, so PYTHONPATH is what lets `-p rebuild.conftest` resolve from the child's own working directory."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    pytester.makepyfile(test_child=CHILD_TESTS.format(root=str(REPO_ROOT)))
    return pytester.runpytest_subprocess("-p", "rebuild.conftest", "-p", "no:cacheprovider", *args)


def test_the_suite_has_one_lane_and_it_is_the_one_the_gate_names():
    """The lane's spelling is shared by the green record, the pool unit and the cycle's gate step, so the tuple here is what every one of them is derived from."""
    assert LANES == ("contracts",)


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
            "rebuild/review-census-pins.json",
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
    def test_the_lane_fails_the_bare_read_and_keeps_the_fixture_read(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ):
        result = _child(pytester, monkeypatch, "--lane", "contracts")
        result.assert_outcomes(passed=1, failed=1)
        result.stdout.fnmatch_lines(["*rebuild lane: contracts*"])
        result.stdout.fnmatch_lines(["*ContractsLaneViolation*"])

    def test_without_a_lane_every_test_runs_and_the_bare_read_still_fails(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ):
        result = _child(pytester, monkeypatch)
        result.assert_outcomes(passed=1, failed=1)
        result.stdout.fnmatch_lines(["*ContractsLaneViolation*"])
