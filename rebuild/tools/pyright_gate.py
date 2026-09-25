"""Pyright's self-skip: the type check that a `make test` or `make test-rebuild` run requests starts only when a file pyright reads has changed since its last green run.

The check's input closure is every `.py` and `.pyi` file under the `include`, `extraPaths` and `stubPath` of `[tool.pyright]` in pyproject.toml, tracked or untracked but not ignored, plus pyproject.toml, which holds the checker's settings, and uv.lock, which pins the checker and the packages it resolves imports against. uv.lock is hashed by its dependency pins (`rebuild.tools.lock_digest`), because the project's own block names nothing pyright resolves. The sources and pyproject.toml are hashed raw, because a `# pyright: ignore` comment changes the result. Nothing else pyright reads can change through an edit in this repository. A rune, glyph or Markdown edit, or a verdict, leaves the closure unchanged, so the suite that such an edit re-runs has no type check ahead of it. The green record (`cycle_paths.PYRIGHT_GREEN`) is written only after a passing run whose closure still matches the one it checked, and a failing run whose closure matches the record deletes the record. `AMS_RUN_PYRIGHT=1` requests the check subject to the skip, and `AMS_RUN_PYRIGHT=force`, which the make targets set for `FORCE=1`, runs it regardless. Without git there is no closure to key on, so the check runs and records nothing.

The root conftest calls this module from two hooks. In an xdist controller, `pytest_configure` starts the check before the workers spawn. A run that builds the fonts waits for the check there, beside the build, so a type error fails the run before any test starts. A run that skips the build (a rebuild-only run whose site fonts are present, which is what `make test-rebuild` starts) defers the check, and `pytest_sessionfinish` joins it. The check then runs beside the xdist pool, and a failure is reported as a nonzero exit after the suite, printed below the pytest summary. If the run was interrupted, that hook abandons the check (`Check.abandon`) instead of judging it, because the Ctrl-C that stopped the suite also stopped pyright. Because the conftest imports this module, its repo imports are limited to the leaf modules `cycle_paths`, `green_record` and `lock_digest`. The conftest's static import closure is added to every rebuild test's closure, so importing the cycle driver here would add the whole pipeline to it (`rebuild.tools.cycle_paths` explains why that matters).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.tools import cycle_paths
from rebuild.tools.green_record import (
    _digest_lines,
    _sha256_path,
    clear_contradicted_green,
    read_green_record,
    record_green,
)
from rebuild.tools.lock_digest import lock_digest

PYRIGHT_ENV = "AMS_RUN_PYRIGHT"
FORCE = "force"
ARGV = ["uv", "run", "pyright"]
CONFIG_PATHS = ("pyproject.toml", "uv.lock")
LOCK_PATH = "uv.lock"
SOURCE_SUFFIXES = (".py", ".pyi")


def checked_roots(root: Path) -> list[str]:
    """Return the paths `[tool.pyright]` in pyproject.toml has the checker read: `include`, `extraPaths` and `stubPath`."""
    with open(Path(root) / "pyproject.toml", "rb") as handle:
        config = tomllib.load(handle).get("tool", {}).get("pyright", {})
    roots = [*config.get("include", []), *config.get("extraPaths", [])]
    stub = config.get("stubPath")
    if isinstance(stub, str):
        roots.append(stub)
    return sorted(set(roots))


def closure_files(root: Path) -> list[str] | None:
    """Return every repo-relative file the check can read, sorted: the sources under the checked roots plus the two config files. Returns None when git is unavailable."""
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                *checked_roots(root),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    sources = {entry for entry in result.stdout.split("\0") if entry and entry.endswith(SOURCE_SUFFIXES)}
    return sorted(sources | set(CONFIG_PATHS))


def closure_fingerprint(root: Path = ROOT) -> str | None:
    """Return a content key over the closure, read from the worktree so uncommitted edits count. A tracked file that has been deleted hashes as absent. Every file is hashed raw except the lock, which is hashed by its dependency pins. Returns None without git."""
    files = closure_files(root)
    if files is None:
        return None
    return _digest_lines([f"{rel}\t{_closure_digest(Path(root) / rel, rel)}" for rel in files])


def _closure_digest(path: Path, rel: str) -> str:
    if rel != LOCK_PATH:
        return _sha256_path(path)
    try:
        return lock_digest(path)
    except OSError:
        return "absent"


def requested(environ: Mapping[str, str]) -> bool:
    return environ.get(PYRIGHT_ENV) in ("1", FORCE)


@dataclass
class Check:
    """One requested check: its process, or None when the green record skipped it, the closure key it started over, and the repo root."""

    process: subprocess.Popen | None
    before: str | None
    root: Path

    def wait(self) -> int:
        """Wait for the check, record or clear its green record, print the outcome, and return the exit code. Returns zero for a check the green record skipped."""
        if self.process is None:
            return 0
        returncode = self.process.wait()
        print(conclude(self.root, self.before, returncode), flush=True)
        return returncode

    def abandon(self) -> None:
        """Terminate and reap a check the run stopped waiting for, without judging it or touching the green record. A Ctrl-C reaches pyright as well as the suite, and a check killed that way has not failed."""
        if self.process is None:
            return
        self.process.terminate()
        self.process.wait()
        print("pyright: abandoned — the run was interrupted, so nothing was judged or recorded", flush=True)


def begin(
    environ: Mapping[str, str], root: Path = ROOT, env: Mapping[str, str] | None = None
) -> Check | None:
    """Start the check when the environment requests one, unless its green record already covers this closure. Returns None when no check was requested. A skip is printed here, so the caller sees why no pyright output follows."""
    if not requested(environ):
        return None
    before = closure_fingerprint(root)
    recorded = read_green_record(cycle_paths.PYRIGHT_GREEN)
    if (
        environ.get(PYRIGHT_ENV) != FORCE
        and before is not None
        and recorded is not None
        and before == recorded["fingerprint"]
    ):
        print(
            f"pyright: SKIPPED — its input closure is unchanged since its last green run ({recorded.get('finished_at')}). "
            f"FORCE=1 on the make target runs it anyway.",
            flush=True,
        )
        return Check(process=None, before=before, root=root)
    process = subprocess.Popen(ARGV, cwd=root, env=dict(env) if env is not None else None)
    return Check(process=process, before=before, root=root)


def conclude(root: Path, before: str | None, returncode: int) -> str:
    """Update the green record for a finished check and return the line that reports it. A passing check whose closure is unchanged is recorded, and a failing check over the recorded closure deletes the record."""
    record = cycle_paths.PYRIGHT_GREEN
    if returncode != 0:
        clear_contradicted_green(record, before)
        return f"pyright: FAILED (exit {returncode})"
    if before is None:
        return "pyright: green (closure fingerprint unavailable without git — not recorded)"
    if closure_fingerprint(root) != before:
        return "pyright: green, but its input closure changed while it ran — green not recorded"
    record_green(record, before)
    where = record.relative_to(root) if record.is_relative_to(root) else record
    return f"pyright: green — closure fingerprint recorded in {where}"
