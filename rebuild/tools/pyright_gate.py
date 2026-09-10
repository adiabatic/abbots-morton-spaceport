"""Pyright's self-skip: the type check a `make test` or `make test-rebuild` run carries spawns only when a file pyright can read has changed since its last green run.

The check's input closure is what `[tool.pyright]` in pyproject.toml points it at — every `.py` and `.pyi` under `include`, `extraPaths` and `stubPath`, tracked or untracked-unignored — plus pyproject.toml itself, which carries the checker's own settings, and uv.lock, which pins the checker and every package it resolves imports against. Nothing else pyright opens is content an edit in this tree can move. A rune edit, a glyph edit, a Markdown edit and a verdict all leave that closure where it was, so the suite they re-arm runs its tests without a type check standing ahead of them; the green record (`artifact_cycle.PYRIGHT_GREEN`) is written only after a pass whose closure still matches what was checked, and a red pass whose closure matches its record deletes the record. `AMS_RUN_PYRIGHT=1` asks for the check under the skip; `AMS_RUN_PYRIGHT=force`, which the make targets spell as `FORCE=1`, runs it regardless. Without git there is no closure to key on, so the check runs and records nothing.

The root conftest's `pytest_configure` is the caller: it begins the check before the workers spawn, overlapping the font build where there is one, and waits on it there so a type error still fails the run before a test has started.
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

from rebuild.tools import artifact_cycle
from rebuild.tools.artifact_cycle import (
    _digest_lines,
    _sha256_path,
    clear_contradicted_green,
    read_green_record,
    record_green,
)

PYRIGHT_ENV = "AMS_RUN_PYRIGHT"
FORCE = "force"
ARGV = ["uv", "run", "pyright"]
CONFIG_PATHS = ("pyproject.toml", "uv.lock")
SOURCE_SUFFIXES = (".py", ".pyi")


def checked_roots(root: Path) -> list[str]:
    """The paths `[tool.pyright]` has the checker read: `include`, `extraPaths` and `stubPath`, as pyproject.toml states them."""
    with open(Path(root) / "pyproject.toml", "rb") as handle:
        config = tomllib.load(handle).get("tool", {}).get("pyright", {})
    roots = [*config.get("include", []), *config.get("extraPaths", [])]
    stub = config.get("stubPath")
    if isinstance(stub, str):
        roots.append(stub)
    return sorted(set(roots))


def closure_files(root: Path) -> list[str] | None:
    """Every repo-relative file the check can read, sorted: the sources under the checked roots plus the two config files. None when git is unavailable."""
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
    """Content key over the closure, read from the worktree so uncommitted edits count; a deleted-but-tracked file hashes as absent. None without git."""
    files = closure_files(root)
    if files is None:
        return None
    return _digest_lines([f"{rel}\t{_sha256_path(Path(root) / rel)}" for rel in files])


def requested(environ: Mapping[str, str]) -> bool:
    return environ.get(PYRIGHT_ENV) in ("1", FORCE)


@dataclass
class Check:
    """One requested check: the process when it spawned, the key it was spawned over, and the line to print for how it ended."""

    process: subprocess.Popen | None
    before: str | None
    root: Path

    def wait(self) -> int:
        """The check's exit code, its green recorded or its record cleared, and the outcome printed; zero for a check the record answered before it spawned."""
        if self.process is None:
            return 0
        returncode = self.process.wait()
        print(conclude(self.root, self.before, returncode), flush=True)
        return returncode


def begin(
    environ: Mapping[str, str], root: Path = ROOT, env: Mapping[str, str] | None = None
) -> Check | None:
    """Start the check when the environment asks for one, unless its green record already vouches for this exact closure; None when it was not asked for. The skip is printed here, so the caller sees why no pyright output follows."""
    if not requested(environ):
        return None
    before = closure_fingerprint(root)
    recorded = read_green_record(artifact_cycle.PYRIGHT_GREEN)
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
    """What a finished check leaves behind: a green whose closure still matches records it, a red over a recorded closure deletes the record, and either way the line that says so."""
    record = artifact_cycle.PYRIGHT_GREEN
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
