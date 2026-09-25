"""Tests for the pyright check's self-skip in `rebuild/tools/pyright_gate.py`: which files its closure covers, when a matching green record skips the check before anything spawns, and what a finished check records. The spawn is stubbed, because the suite's own pyright run is the real check. rebuild/conftest.py's autouse fixture redirects every green record under tmp_path."""

import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import cycle_paths
from rebuild.tools import pyright_gate as pg

ROOT = Path(__file__).resolve().parent.parent


class _Process:
    def __init__(self, returncode: int):
        self.returncode = returncode
        self.terminated = False

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


def _spawn_stub(monkeypatch, returncode: int | None):
    """Capture every spawn's argv and environment. With a None return code, any spawn fails the test."""
    spawned: list[tuple[list[str], dict | None]] = []

    def fake_popen(argv, cwd, env=None):
        assert returncode is not None, "the record should have answered before anything spawned"
        spawned.append((list(argv), env))
        return _Process(returncode)

    monkeypatch.setattr(pg.subprocess, "Popen", fake_popen)
    return spawned


def _fingerprints(monkeypatch, values):
    """Stub the closure fingerprint with one value per call: the key `begin` reads, then the key `conclude` reads again before recording."""
    sequence = iter(values)
    monkeypatch.setattr(pg, "closure_fingerprint", lambda root=ROOT: next(sequence))


def test_the_closure_is_what_pyright_is_pointed_at_and_nothing_else():
    """The roots are the `[tool.pyright]` paths in pyproject.toml. The closure holds only Python sources and stubs under them, plus pyproject.toml and uv.lock, and nothing under rebuild/out/ or glyph_data/."""
    assert pg.checked_roots(ROOT) == sorted({"conftest.py", "rebuild", "test", "tools", "typings"})
    files = pg.closure_files(ROOT)
    assert files is not None
    assert {
        "conftest.py",
        "rebuild/tools/pyright_gate.py",
        "typings/uharfbuzz/__init__.pyi",
        "pyproject.toml",
        "uv.lock",
    } <= set(files)
    assert files == sorted(files)
    assert all(rel.endswith(pg.SOURCE_SUFFIXES) or rel in pg.CONFIG_PATHS for rel in files)
    assert not any(rel.startswith("rebuild/out/") or rel.startswith("glyph_data/") for rel in files)


def test_the_closure_follows_pyproject_and_git_rather_than_a_hand_list(tmp_path):
    """In a synthetic repo, the closure holds tracked and untracked sources under the configured roots, stubs under the stub path, and pyproject.toml and uv.lock. It leaves out ignored files and non-source files."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "typings").mkdir()
    (root / "lib").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pyright]\ninclude = ["src"]\nextraPaths = ["lib"]\nstubPath = "typings"\n'
    )
    (root / "src" / "a.py").write_text("a = 1\n")
    (root / "src" / "b.txt").write_text("not a source\n")
    (root / "typings" / "x.pyi").write_text("x: int\n")
    (root / "lib" / "c.py").write_text("c = 3\n")
    (root / "elsewhere.py").write_text("unchecked = 0\n")
    (root / ".gitignore").write_text("src/e.py\n")
    (root / "src" / "e.py").write_text("ignored = 5\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    (root / "src" / "d.py").write_text("untracked = 4\n")
    assert pg.checked_roots(root) == ["lib", "src", "typings"]
    assert pg.closure_files(root) == [
        "lib/c.py",
        "pyproject.toml",
        "src/a.py",
        "src/d.py",
        "typings/x.pyi",
        "uv.lock",
    ]
    key = pg.closure_fingerprint(root)
    (root / "src" / "d.py").write_text("untracked = 44\n")
    assert pg.closure_fingerprint(root) != key


def test_an_unrequested_check_neither_spawns_nor_consults_the_record(monkeypatch):
    _spawn_stub(monkeypatch, None)
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, [])
    assert pg.begin({}, ROOT) is None
    assert pg.begin({pg.PYRIGHT_ENV: "0"}, ROOT) is None


def test_a_green_check_records_the_closure_it_was_spawned_over(monkeypatch, capsys):
    _fingerprints(monkeypatch, ["p-1", "p-1"])
    spawned = _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: "1"}, ROOT, env={"PATH": "/bin"})
    assert check is not None
    assert spawned == [(pg.ARGV, {"PATH": "/bin"})]
    assert check.wait() == 0
    record = ac.read_green_record(cycle_paths.PYRIGHT_GREEN)
    assert record is not None
    assert record["fingerprint"] == "p-1"
    assert "pyright: green — closure fingerprint recorded in" in capsys.readouterr().out


def test_a_matching_record_answers_before_anything_spawns(monkeypatch, capsys):
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1"])
    _spawn_stub(monkeypatch, None)
    check = pg.begin({pg.PYRIGHT_ENV: "1"}, ROOT)
    assert check is not None
    assert check.process is None
    assert check.wait() == 0
    assert (
        "pyright: SKIPPED — its input closure is unchanged since its last green run"
        in capsys.readouterr().out
    )


def test_force_spawns_over_a_matching_record(monkeypatch):
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1", "p-1"])
    spawned = _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: pg.FORCE}, ROOT)
    assert check is not None and check.wait() == 0
    assert len(spawned) == 1


def test_a_red_check_over_its_recorded_closure_deletes_the_record(monkeypatch, capsys):
    """A forced red run over the recorded closure contradicts the record, so the record is deleted. The caller fails the run on the exit code."""
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1"])
    _spawn_stub(monkeypatch, 1)
    check = pg.begin({pg.PYRIGHT_ENV: pg.FORCE}, ROOT)
    assert check is not None and check.wait() == 1
    assert ac.read_green_record(cycle_paths.PYRIGHT_GREEN) is None
    assert "pyright: FAILED (exit 1)" in capsys.readouterr().out


def test_an_abandoned_check_is_ended_and_judges_nothing(monkeypatch, capsys):
    """An interrupted run terminates and joins the process, keeps the green record despite the nonzero exit, and prints that the check was abandoned instead of failed."""
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1"])
    spawned = _spawn_stub(monkeypatch, 130)
    check = pg.begin({pg.PYRIGHT_ENV: pg.FORCE}, ROOT)
    assert check is not None and isinstance(check.process, _Process)
    check.abandon()
    assert check.process.terminated
    assert len(spawned) == 1
    record = ac.read_green_record(cycle_paths.PYRIGHT_GREEN)
    assert record is not None and record["fingerprint"] == "p-1"
    out = capsys.readouterr().out
    assert "pyright: abandoned" in out and "FAILED" not in out
    pg.Check(process=None, before="p-1", root=ROOT).abandon()
    assert capsys.readouterr().out == ""


def test_a_closure_that_moved_during_the_check_records_nothing(monkeypatch, capsys):
    _fingerprints(monkeypatch, ["p-1", "p-2"])
    _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: "1"}, ROOT)
    assert check is not None and check.wait() == 0
    assert ac.read_green_record(cycle_paths.PYRIGHT_GREEN) is None
    assert "green not recorded" in capsys.readouterr().out


def test_without_git_the_check_runs_and_records_nothing(monkeypatch, capsys):
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, [None])
    spawned = _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: "1"}, ROOT)
    assert check is not None and check.wait() == 0
    assert len(spawned) == 1
    record = ac.read_green_record(cycle_paths.PYRIGHT_GREEN)
    assert record is not None and record["fingerprint"] == "p-1"
    assert "not recorded" in capsys.readouterr().out


class _FakeCheck:
    """A stand-in check that counts its `wait` and `abandon` calls and returns the exit code it was built with from `wait`."""

    def __init__(self, returncode: int):
        self.returncode = returncode
        self.waits = 0
        self.abandons = 0

    def wait(self) -> int:
        self.waits += 1
        return self.returncode

    def abandon(self) -> None:
        self.abandons += 1


class _RootStubConfig:
    """The parts of a `Config` the root conftest's two hooks read: the argv paths, the invocation directory they resolve against, and `--dist`. It has `workerinput` only when `worker` is set. It is modeled on `_StubConfig` in rebuild/test_memory_budget.py, whose docstring says why a real `Config` is not built."""

    def __init__(self, *args: str, dist: str = "load", worker: bool = False) -> None:
        self.args = list(args)
        self.invocation_params = SimpleNamespace(dir=ROOT)
        self._dist = dist
        if worker:
            self.workerinput = {"workerid": "gw0"}
            self.workeroutput: dict = {}

    def getoption(self, name: str, default: object = None) -> object:
        assert name == "dist", f"a hook asked for an option this stub does not carry: {name}"
        return self._dist


def _drive_sessionfinish(
    root: ModuleType,
    config: _RootStubConfig,
    inner: BaseException | None = None,
    exitstatus: int = pytest.ExitCode.OK,
):
    """Run the wrapper hook the way pluggy does: up to its yield, then resume it with the inner hooks' result or the exception an inner hook raised, and return what the wrapper returns. `exitstatus` is the session's, as pytest's `wrap_session` passes it."""
    session = SimpleNamespace(config=config, exitstatus=exitstatus)
    gen = root.pytest_sessionfinish(session=session, exitstatus=exitstatus)
    next(gen)
    try:
        if inner is None:
            gen.send(None)
        else:
            gen.throw(inner)
    except StopIteration as done:
        return done.value
    raise AssertionError("the wrapper yielded twice")


class TestWhenTheCheckIsJoined:
    """Drive the root conftest's `pytest_configure` and `pytest_sessionfinish` on the plugin object pytest loaded (`_loaded_conftest` in rebuild/test_memory_budget.py says why it is not imported). `pyright_gate.begin` is stubbed to return a fake check, and the conftest's `subprocess` is stubbed to record any `make all` instead of running it. `_deferred_pyright` and `_shaping_cache` are replaced with fresh containers so the test cannot change the state of the session running it."""

    @pytest.fixture
    def root(self, pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
        plugin = pytestconfig.pluginmanager.get_plugin(str(ROOT / "conftest.py"))
        assert isinstance(plugin, ModuleType), "pytest has not loaded the root conftest as a plugin"
        monkeypatch.setattr(plugin, "_deferred_pyright", [])
        monkeypatch.setattr(plugin, "_shaping_cache", {})
        return plugin

    @pytest.fixture
    def builds(self, root: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        spawned: list[list[str]] = []

        def fake_run(argv, cwd, check, env):
            spawned.append(list(argv))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(root, "subprocess", SimpleNamespace(run=fake_run))
        return spawned

    def _begin(self, monkeypatch: pytest.MonkeyPatch, check: _FakeCheck | None) -> None:
        monkeypatch.setattr(pg, "begin", lambda environ, root, env=None: check)

    def test_a_rebuild_only_run_with_its_fonts_present_parks_the_check_and_waits_on_nothing(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        """In the pytest run that `make test-rebuild` spawns, no fonts are built, nothing waits on the check before the workers start, and the check is waited on once at session end."""
        check = _FakeCheck(0)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        assert builds == []
        assert check.waits == 0
        assert root._deferred_pyright == [check]
        assert _drive_sessionfinish(root, _RootStubConfig("rebuild/")) is None
        assert check.waits == 1

    def test_a_deferred_red_fails_the_run(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        """A red deferred check exits the session nonzero with no FAILED or ERROR line, which the gate wrappers treat as a hard failure."""
        self._begin(monkeypatch, _FakeCheck(1))
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        with pytest.raises(pytest.exit.Exception) as raised:
            _drive_sessionfinish(root, _RootStubConfig("rebuild/"))
        assert raised.value.returncode not in (None, 0)

    def test_a_deferred_green_leaves_the_session_alone_and_empties_the_parking_list(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        check = _FakeCheck(0)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        _drive_sessionfinish(root, _RootStubConfig("rebuild/"))
        assert root._deferred_pyright == []
        _drive_sessionfinish(root, _RootStubConfig("rebuild/"))
        assert check.waits == 1

    def test_a_run_that_does_not_ask_for_the_check_parks_nothing(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        self._begin(monkeypatch, None)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        assert root._deferred_pyright == []
        assert _drive_sessionfinish(root, _RootStubConfig("rebuild/")) is None

    @pytest.mark.parametrize(
        ("args", "fonts_present"),
        [(("test/", "site/"), True), (("rebuild/",), False)],
        ids=["font-suite", "rebuild-only-after-make-clean"],
    )
    def test_a_run_that_builds_the_fonts_still_fast_fails_before_the_workers_spawn(
        self,
        root: ModuleType,
        builds: list[list[str]],
        monkeypatch: pytest.MonkeyPatch,
        args: tuple[str, ...],
        fonts_present: bool,
    ):
        """When the run builds the fonts (`make test FORCE=1`, or a rebuild-only run whose site fonts are absent), the check runs alongside the build and a red check raises before any worker starts. Nothing is deferred to session end."""
        check = _FakeCheck(1)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: fonts_present)
        with pytest.raises(pytest.UsageError):
            root.pytest_configure(_RootStubConfig(*args))
        assert builds == [["make", "all"]]
        assert check.waits == 1
        assert root._deferred_pyright == []
        assert root._shaping_cache == {"_built": True}

    def test_a_worker_begins_no_check_and_still_reports_its_peak(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        """A worker writes its peak RSS into workeroutput before the hook yields, so xdist's wrapper sends it to the controller however the two wrappers nest. That puts every worker in the peak-RSS summary line and the kind:"pool" journal record."""
        self._begin(monkeypatch, _FakeCheck(1))
        config = _RootStubConfig("rebuild/", worker=True)
        root.pytest_configure(config)
        assert builds == []
        assert root._deferred_pyright == []
        assert root._shaping_cache == {"_built": True}
        session = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.OK)
        gen = root.pytest_sessionfinish(session=session, exitstatus=pytest.ExitCode.OK)
        next(gen)
        assert isinstance(config.workeroutput.get("peak_rss_bytes"), int)
        with pytest.raises(StopIteration):
            gen.send(None)

    def test_a_session_interrupted_by_ctrl_c_abandons_the_check_and_keeps_its_own_exit(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        """On Ctrl-C, pytest's `wrap_session` catches the KeyboardInterrupt and calls the hook with exitstatus INTERRUPTED. The deferred check is abandoned and its exit code ignored, because the same SIGINT killed pyright, and treating that exit as a failure would clear a valid green record. The wrapper raises nothing, so the session keeps its INTERRUPTED status."""
        check = _FakeCheck(130)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        result = _drive_sessionfinish(
            root, _RootStubConfig("rebuild/"), exitstatus=pytest.ExitCode.INTERRUPTED
        )
        assert result is None
        assert (check.waits, check.abandons) == (0, 1)
        assert root._deferred_pyright == []

    def test_an_inner_impl_that_raises_still_reaps_the_check_without_judging_it(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        """When an inner sessionfinish hook raises, the wrapper still abandons the deferred check so no pyright process is left running, and the exception propagates."""
        check = _FakeCheck(0)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        with pytest.raises(KeyboardInterrupt):
            _drive_sessionfinish(root, _RootStubConfig("rebuild/"), inner=KeyboardInterrupt())
        assert (check.waits, check.abandons) == (0, 1)
        assert root._deferred_pyright == []

    def test_a_deferred_red_still_fails_a_session_whose_tests_failed(
        self, root: ModuleType, builds: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ):
        """Only INTERRUPTED abandons the check. A session that finished with test failures still waits on the check and fails on a red one."""
        check = _FakeCheck(1)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        with pytest.raises(pytest.exit.Exception):
            _drive_sessionfinish(root, _RootStubConfig("rebuild/"), exitstatus=pytest.ExitCode.TESTS_FAILED)
        assert (check.waits, check.abandons) == (1, 0)

    def test_the_controller_half_is_the_outermost_sessionfinish_wrapper(self, root: ModuleType):
        """The TerminalReporter registers its own sessionfinish wrapper after the conftests. Without `tryfirst`, the conftest's wrapper would run inside the summary, so the wait for pyright would count toward the summary's time and the `pyright:` line would print above the summary. Pluggy stores the hookimpl options on the function, so the test reads them there."""
        opts = root.pytest_sessionfinish.pytest_impl
        assert opts["wrapper"] and opts["tryfirst"]


_GATE_LOCK = (
    "version = 1\n\n"
    '[[package]]\nname = "abbots-morton-spaceport"\nversion = "16.0.0"\nsource = { virtual = "." }\n\n'
    '[[package]]\nname = "pyright"\nversion = "1.1.411"\nsource = { registry = "https://pypi.org/simple" }\n'
)


def test_the_closure_reads_the_lock_by_its_dependency_pins_and_every_source_raw(tmp_path):
    """A version bump in the lock's project block leaves the key unchanged. A changed dependency pin, such as pyright's own, changes it. A `# pyright: ignore` comment in a source also changes it, because sources are hashed raw. The file list is the same in every case."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[tool.pyright]\ninclude = ["src"]\n')
    (root / "src" / "a.py").write_text("a = 1\n")
    (root / "uv.lock").write_text(_GATE_LOCK)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    files = pg.closure_files(root)
    key = pg.closure_fingerprint(root)
    (root / "uv.lock").write_text(_GATE_LOCK.replace('version = "16.0.0"', 'version = "16.1.0"'))
    assert pg.closure_files(root) == files
    assert pg.closure_fingerprint(root) == key
    (root / "uv.lock").write_text(_GATE_LOCK.replace('version = "1.1.411"', 'version = "1.1.414"'))
    assert pg.closure_fingerprint(root) != key
    (root / "uv.lock").write_text(_GATE_LOCK)
    (root / "src" / "a.py").write_text("a = 1  # pyright: ignore[reportUnknownRule]\n")
    assert pg.closure_fingerprint(root) != key
