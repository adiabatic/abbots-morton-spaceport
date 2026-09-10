"""The type check's self-skip (rebuild/tools/pyright_gate.py is the contract): what its closure holds, when the record answers before anything spawns, and what a finished check leaves behind. The spawn is stubbed — a real pyright is the suite's own gate, not a test's — and the record lives under tmp_path through the autouse redirect of every green constant."""

import subprocess
from pathlib import Path

from rebuild.tools import artifact_cycle as ac
from rebuild.tools import pyright_gate as pg

ROOT = Path(__file__).resolve().parent.parent


class _Process:
    def __init__(self, returncode: int):
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


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
    ac.record_green(ac.PYRIGHT_GREEN, "p-1")
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
    record = ac.read_green_record(ac.PYRIGHT_GREEN)
    assert record is not None
    assert record["fingerprint"] == "p-1"
    assert "pyright: green — closure fingerprint recorded in" in capsys.readouterr().out


def test_a_matching_record_answers_before_anything_spawns(monkeypatch, capsys):
    ac.record_green(ac.PYRIGHT_GREEN, "p-1")
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
    ac.record_green(ac.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1", "p-1"])
    spawned = _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: pg.FORCE}, ROOT)
    assert check is not None and check.wait() == 0
    assert len(spawned) == 1


def test_a_red_check_over_its_recorded_closure_deletes_the_record(monkeypatch, capsys):
    """A forced red over content the record calls green contradicts the record, so the record goes; the exit code is the caller's to fail the run on."""
    ac.record_green(ac.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, ["p-1"])
    _spawn_stub(monkeypatch, 1)
    check = pg.begin({pg.PYRIGHT_ENV: pg.FORCE}, ROOT)
    assert check is not None and check.wait() == 1
    assert ac.read_green_record(ac.PYRIGHT_GREEN) is None
    assert "pyright: FAILED (exit 1)" in capsys.readouterr().out


def test_a_closure_that_moved_during_the_check_records_nothing(monkeypatch, capsys):
    _fingerprints(monkeypatch, ["p-1", "p-2"])
    _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: "1"}, ROOT)
    assert check is not None and check.wait() == 0
    assert ac.read_green_record(ac.PYRIGHT_GREEN) is None
    assert "green not recorded" in capsys.readouterr().out


def test_without_git_the_check_runs_and_records_nothing(monkeypatch, capsys):
    ac.record_green(ac.PYRIGHT_GREEN, "p-1")
    _fingerprints(monkeypatch, [None])
    spawned = _spawn_stub(monkeypatch, 0)
    check = pg.begin({pg.PYRIGHT_ENV: "1"}, ROOT)
    assert check is not None and check.wait() == 0
    assert len(spawned) == 1
    record = ac.read_green_record(ac.PYRIGHT_GREEN)
    assert record is not None and record["fingerprint"] == "p-1"
    assert "not recorded" in capsys.readouterr().out
