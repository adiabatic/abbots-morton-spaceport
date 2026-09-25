"""The helpers `rebuild/conftest.py` uses to record the contracts lane's per-test input closures: classifying a read, judging a spawned child, reading a selection file, and writing the sidecar. `rebuild.tools.contracts_closure` holds the static import walk, the selection, and the merge, and re-exports every name here.

This module imports nothing from the repo so that the conftest's static import closure stays small. `closure_of` adds both conftests' static import closures to every test's closure, and `contracts_closure` imports the cycle driver, and through it the pipeline, inside its selection functions. If the conftest imported `contracts_closure`, the pipeline would be in every test's closure, so any pipeline edit would rerun every test. `rebuild/test_contracts_closure.py` checks the conftests' direct imports and that neither reaches rebuild/pipeline/ or rebuild/review/.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

SIDECAR_FORMAT = "ams-contracts-closures/1"
SELECTION_FORMAT = "ams-contracts-selection/1"
IGNORED_PREFIXES = (
    ".git/",
    ".venv/",
    ".uv-cache/",
    ".pytest_cache/",
    "node_modules/",
    "rebuild/kernel-rs/target/",
)
HERMETIC_GIT_SUBCOMMANDS = frozenset(("rev-parse", "cat-file", "archive"))
KERNEL_PREFIX = "rebuild/kernel-rs/"
KERNEL_BINARY = "ams-m1-kernel"
KERNEL_MANIFEST = KERNEL_PREFIX + "Cargo.toml"


def hermetic_child(argv: object) -> bool:
    """Whether a spawned command reads nothing from the working tree. The `git` subcommands in `HERMETIC_GIT_SUBCOMMANDS` read only the object store and refs (`cat-file` and `archive` by sha, `rev-parse` by ref or `HEAD:<path>`), so no file edit can change their output and a test that spawns one stays closable. Any other child makes the test unclosable: `git status`, `git ls-files`, and `git diff` read the index and the working tree, and a non-git child can read anything."""
    if not isinstance(argv, (list, tuple)) or len(argv) < 2:
        return False
    try:
        head = os.path.basename(os.fsdecode(argv[0]))  # pyright: ignore[reportArgumentType]
        subcommand = os.fsdecode(argv[1])  # pyright: ignore[reportArgumentType]
    except TypeError, ValueError:
        return False
    return head == "git" and subcommand in HERMETIC_GIT_SUBCOMMANDS


def _argv_strings(argv: object) -> list[str] | None:
    if not isinstance(argv, (list, tuple)) or not argv:
        return None
    try:
        return [os.fsdecode(arg) for arg in argv]  # pyright: ignore[reportArgumentType]
    except TypeError, ValueError:
        return None


def kernel_child(argv: object) -> bool:
    """Whether a spawned command is the M1 kernel, or the `cargo build` of its crate that `kernel_exec.ensure_built` runs before a process first calls it. The kernel reads the files its argv names, which its parent wrote to a scratch directory from data the parent had already read, and its own binary, which is built from the crate's tracked sources. Cargo reads those sources and the registry crates the lockfile pins by hash. A test that spawns either is closable once the crate's files are added to its closure, which `closure_of` does for every entry flagged `kernel`."""
    strings = _argv_strings(argv)
    if strings is None:
        return False
    head = os.path.basename(strings[0])
    if head == KERNEL_BINARY:
        return True
    if head != "cargo" or len(strings) < 2 or strings[1] != "build":
        return False
    manifests = [arg for flag, arg in zip(strings, strings[1:]) if flag == "--manifest-path"]
    return bool(manifests) and all(
        manifest.replace(os.sep, "/").endswith("/" + KERNEL_MANIFEST) for manifest in manifests
    )


def source_of(rel: str) -> str:
    """Return the repo-relative source a read stands for. A `.pyc` under `__pycache__` maps to the `.py` it was compiled from, because the import system opens a valid cache instead of the source."""
    parent, name = os.path.split(rel)
    if os.path.basename(parent) == "__pycache__" and name.endswith(".pyc"):
        return os.path.join(os.path.dirname(parent), name.split(".", 1)[0] + ".py").replace(os.sep, "/")
    return rel


def recordable(rel: str) -> bool:
    return not rel.startswith(IGNORED_PREFIXES) and "/__pycache__/" not in f"/{rel}"


def selection_path(record_path: Path) -> Path:
    return record_path.with_name("rebuild-contracts-selection.json")


def sidecar_path(record_path: Path) -> Path:
    return record_path.with_name("rebuild-contracts-closures.json")


def write_selection(path: Path, skip: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": SELECTION_FORMAT, "skip": sorted(set(skip))}) + "\n")


def read_selection(path: Path) -> frozenset[str]:
    """Return the test ids a selection file skips. An absent or malformed file gives an empty set, so the run skips nothing."""
    try:
        payload = json.loads(path.read_text())
    except OSError, ValueError:
        return frozenset()
    if not isinstance(payload, dict) or payload.get("format") != SELECTION_FORMAT:
        return frozenset()
    return frozenset(item for item in payload.get("skip", ()) if isinstance(item, str))


def read_sidecar(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("format") != SIDECAR_FORMAT:
        return None
    if not isinstance(payload.get("collected"), list) or not isinstance(payload.get("tests"), dict):
        return None
    if not isinstance(payload.get("module_reads", {}), dict):
        return None
    return payload


def write_sidecar(
    path: Path,
    collected: list[str],
    tests: dict[str, dict],
    module_reads: Mapping[str, Iterable[str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": SIDECAR_FORMAT,
        "collected": sorted(set(collected)),
        "tests": tests,
        "module_reads": {module: sorted(reads) for module, reads in (module_reads or {}).items()},
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")
