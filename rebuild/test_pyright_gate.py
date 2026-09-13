"""The type check's self-skip (rebuild/tools/pyright_gate.py is the contract): what its closure holds, when the record answers before anything spawns, and what a finished check leaves behind. The spawn is stubbed — a real pyright is the suite's own gate, not a test's — and the record lives under tmp_path through the autouse redirect of every green constant."""

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
    """Capture every spawn's argv and environment; a None return code makes any spawn a test failure."""
    spawned: list[tuple[list[str], dict | None]] = []

    def fake_popen(argv, cwd, env=None):
        assert returncode is not None, "the record should have answered before anything spawned"
        spawned.append((list(argv), env))
        return _Process(returncode)

    monkeypatch.setattr(pg.subprocess, "Popen", fake_popen)
    return spawned


def _fingerprints(monkeypatch, values):
    """One entry per call the gate makes: the key it begins over, then the key it re-reads before recording."""
    sequence = iter(values)
    monkeypatch.setattr(pg, "closure_fingerprint", lambda root=ROOT: next(sequence))


def test_the_closure_is_what_pyright_is_pointed_at_and_nothing_else():
    """The roots come from `[tool.pyright]` as pyproject.toml states them, and the closure over them is Python sources and stubs only, plus the two files that configure the checker and pin what it resolves against — no data, no prose, and nothing under a live tree."""
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
    """A synthetic repo says which files count: sources under the configured roots whether tracked or not, stubs under the stub path, never an ignored file, never a non-source, and the config pair always."""
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
    """A forced red over content the record calls green contradicts the record, so the record goes; the exit code is the caller's to fail the run on."""
    ac.record_green(cycle_paths.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1"])
    _spawn_stub(monkeypatch, 1)
    check = pg.begin({pg.PYRIGHT_ENV: pg.FORCE}, ROOT)
    assert check is not None and check.wait() == 1
    assert ac.read_green_record(cycle_paths.PYRIGHT_GREEN) is None
    assert "pyright: FAILED (exit 1)" in capsys.readouterr().out


def test_an_abandoned_check_is_ended_and_judges_nothing(monkeypatch, capsys):
    """The interrupted run's path: the process is terminated and joined, its nonzero exit contradicts no record, and the line says the check was abandoned rather than red."""
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
    """A parked check that records how often it was joined and how often abandoned, and answers a join with the exit code it was built with."""

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
    """What the root conftest's two hooks ask a controller's `Config` for and nothing else: the argv paths, the invocation directory they resolve against, and `--dist`; no `workerinput`, unless the test asks for a worker's. The twin of `_StubConfig` in rebuild/test_memory_budget.py, stubbed for the reason its docstring gives."""

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
    """Run the wrapper hook the way pluggy does: up to its yield, then back through with the inner impls' result — or the exception an inner impl raised — and return whatever the wrapper hands back. `exitstatus` is the session's, as pytest's `wrap_session` passes it."""
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
    """The root conftest's two hooks, driven on the plugin pytest itself loaded (`_loaded_conftest` in rebuild/test_memory_budget.py argues why the live object and not an import). `pyright_gate.begin` is stubbed to hand back a fake check, the conftest's `subprocess` is stubbed so a `make all` on the wrong branch fails the test, and the parking list and shaping cache are swapped for fresh containers so driving the live plugin cannot leak into the session running this test."""

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
        """The lane `make test-rebuild` spawns: no font build, no wait before the workers, and the one join at session end."""
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
        """The exit both wrappers already read as hard: nonzero, with no FAILED/ERROR line of its own."""
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
        """`make test FORCE=1`'s promise, and the same branch for a rebuild-only run whose site fonts are absent: the build runs, the check is waited on beside it, and a red raises before a worker exists, with nothing parked for session end."""
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
        """The pin for the worker half of the wrapper: the peak is written into workeroutput before the hook yields, so xdist's own wrapper ships it back however the two nest, and every worker is counted in the peak-RSS summary line and the kind:"pool" journal records."""
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
        """The shape a Ctrl-C takes at this hook: pytest's `wrap_session` catches the KeyboardInterrupt and calls the hook normally with exitstatus INTERRUPTED. The parked check is abandoned, not judged — the same SIGINT killed pyright, so its exit would read as a false red and clear a green record that still holds — and the wrapper raises nothing, so INTERRUPTED stands."""
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
        """An inner sessionfinish impl that raises — an exploding teardown — still reaps the parked check, so no pyright is orphaned; a check whose session did not finish is abandoned, and the exception is what propagates."""
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
        """Only INTERRUPTED abandons the check: a session that ran to its end with failures is judged like a green one."""
        check = _FakeCheck(1)
        self._begin(monkeypatch, check)
        monkeypatch.setattr(root, "_rebuild_suite_fonts_present", lambda: True)
        root.pytest_configure(_RootStubConfig("rebuild/"))
        with pytest.raises(pytest.exit.Exception):
            _drive_sessionfinish(root, _RootStubConfig("rebuild/"), exitstatus=pytest.ExitCode.TESTS_FAILED)
        assert (check.waits, check.abandons) == (1, 0)

    def test_the_controller_half_is_the_outermost_sessionfinish_wrapper(self, root: ModuleType):
        """The flag the saving hinges on: the TerminalReporter registers its own sessionfinish wrapper after the conftests, so without `tryfirst` this one would run inside the summary and pyright's remaining wait would land inside the summary's clock and above its line. Pluggy stores the hookimpl options on the function, so the pin reads them there."""
        opts = root.pytest_sessionfinish.pytest_impl
        assert opts["wrapper"] and opts["tryfirst"]
