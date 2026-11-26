"""Checks that `settle_memo_keys.SETTLE_MEMO_CODE_PATHS` names exactly the code the settle memo's walk runs.

`SETTLE_MEMO_CODE_PATHS` is a hand-written list, not a glob over `rebuild/pipeline/`. If a module that runs in `_SettledWindowWalk` is missing from it, the memo's stamp does not hash that module, and a fix to it would be skipped and the old settlement served as current. This test walks the import graph from the module that defines the walk and requires every reachable module outside its stop-set to be listed. It also checks the other direction, that every listed module is reachable, because a listed module the walk never reaches drops every memo on every commit that edits it.

The walk is at module grain, as in `rebuild/test_review_code_closure.py`. That errs in the safe direction: a module conform.py imports for a purpose the walk never uses is still stamped. The one exception is the stop-set, rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES` (the pool-width, peak-memory, font-path and lock-digest helpers among them), which the walk neither records nor follows; the comment on `settle_memo_keys.SETTLE_MEMO_CODE_PATHS` says why none of them can change a settlement. A test checks that every member the walk reaches imports, transitively, only other members, so an edge into a member that imports beyond the set fails. The opposite error, a module the walk runs that the import graph does not show, would need a dynamic import, and the walked modules use none. The walk follows `if TYPE_CHECKING:` imports too, because `ast.walk` ignores the guard, so a type-only import is stamped like any other.

Because of the module grain, the witness stage's rule replay is in `rebuild/pipeline/witness.py` and not in conform.py. The replay imports `rebuild/pipeline/emit_gsub.py`, and if it were in conform.py, the roster would have to name the emitter.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import conform, settle_memo_keys
from rebuild.test_review_code_closure import WIDTH_AND_TELEMETRY_MODULES

REPO_ROOT = Path(__file__).resolve().parent.parent

WALK_ENTRY_MODULES = ("rebuild.pipeline.conform",)


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Return every repo module the file imports. Only absolute imports are read, because the walked modules use none of the relative kind. A `from X import y` is recorded as both X and X.y so that a submodule import is followed."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def _walk(entry_points: tuple[str, ...]) -> tuple[dict[str, Path], set[str]]:
    """Return the repo modules the entry points import transitively, keyed by module name, and the members of `WIDTH_AND_TELEMETRY_MODULES` the walk reached and stopped at (see the module docstring)."""
    seen: dict[str, Path] = {}
    stopped: set[str] = set()
    queue = list(entry_points)
    while queue:
        module = queue.pop()
        if module in WIDTH_AND_TELEMETRY_MODULES:
            stopped.add(module)
            continue
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        queue.extend(_imports(path))
    return seen, stopped


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """Return the repo modules the entry points import transitively, keyed by module name, stopping at `WIDTH_AND_TELEMETRY_MODULES` (see the module docstring)."""
    return _walk(entry_points)[0]


def _reached_files() -> set[Path]:
    reached = reachable_modules(WALK_ENTRY_MODULES)
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    return {path for path in reached.values() if path.name != "__init__.py"}


def test_the_walk_lives_in_the_walked_module():
    """The walk starts from conform.py, so `_SettledWindowWalk` must be defined there. If it moved to another file, the walk tests would keep passing while covering the wrong graph."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "conform.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert (
        "_SettledWindowWalk" in defined
    ), "the settle memo's walk is no longer defined in rebuild/pipeline/conform.py, so WALK_ENTRY_MODULES names the wrong graph"
    assert hasattr(conform, "_SettledWindowWalk")


def test_the_rule_replay_lives_outside_the_walk_s_closure():
    """The witness stage's rule replay, which reads the GSUB emitter's rule fold, is defined in rebuild/pipeline/witness.py, the walk does not reach `emit_gsub`, and the roster does not name emit_gsub.py. An edit to the emitter alone therefore keeps every settle memo. The generic tests below would accept the replay moving back into conform.py if the roster gained emit_gsub.py; this test fails on that move."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "witness.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, ast.FunctionDef)}
    replay = {
        "_first_matching_rule",
        "_matched_windows",
        "_renamed_rules_by_input",
        "check_rule_certificates",
    }
    assert replay <= defined, f"witness.py defines {sorted(defined & replay)} of the replay's four functions"
    assert "rebuild.pipeline.emit_gsub" not in reachable_modules(
        WALK_ENTRY_MODULES
    ), "the walk's import closure reaches emit_gsub again, so an emitter edit drops every settle memo"
    assert "rebuild/pipeline/emit_gsub.py" not in settle_memo_keys.SETTLE_MEMO_CODE_PATHS


def test_the_roster_covers_the_walk_s_import_graph():
    files = _reached_files()
    assert (
        REPO_ROOT / "rebuild" / "pipeline" / "settle.py" in files
    ), "the walk found nothing; the entry module moved"
    named = {REPO_ROOT / relative for relative in settle_memo_keys.SETTLE_MEMO_CODE_PATHS}
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - named)
    assert uncovered == [], (
        "these modules run in the settle memo's walk but SETTLE_MEMO_CODE_PATHS does not name them, so the memo's "
        "stamp does not hash them and a fix to one would be served around as though the previous pass had already "
        f"applied it: {', '.join(uncovered)}. Add each to SETTLE_MEMO_CODE_PATHS in rebuild/pipeline/settle_memo_keys.py, "
        "or move the code out of the walk's reach."
    )


def test_the_roster_holds_no_module_the_walk_never_reaches():
    """Every module the roster names must be reachable from the walk. An extra name does not make results wrong, but it drops every memo on a commit that could not have changed a settlement."""
    files = _reached_files()
    strays = sorted(
        relative for relative in settle_memo_keys.SETTLE_MEMO_CODE_PATHS if REPO_ROOT / relative not in files
    )
    assert strays == [], (
        "named in SETTLE_MEMO_CODE_PATHS but unreachable from the walk, so every memo drops whenever one of "
        f"them moves and nothing is bought for it: {', '.join(strays)}"
    )


def test_the_walk_stops_only_at_modules_whose_imports_stay_inside_the_stop_set():
    """The walk neither records nor follows a module in `WIDTH_AND_TELEMETRY_MODULES`, so the roster does not hash what such a module imports. That is sound only while every member the walk reaches imports, transitively, nothing outside the set. The set is the corpus build's, and some members import far beyond it (`artifact_cycle` imports the review pipeline), so an edge from the walk into one of those fails here instead of being trusted."""
    queue = sorted(_walk(WALK_ENTRY_MODULES)[1])
    checked: set[str] = set()
    while queue:
        module = queue.pop()
        if module in checked:
            continue
        checked.add(module)
        path = _module_path(module)
        assert path is not None, f"{module} is in WIDTH_AND_TELEMETRY_MODULES but is not on disk"
        onward = {name for name in _imports(path) if _module_path(name) is not None}
        outside = sorted(onward - WIDTH_AND_TELEMETRY_MODULES)
        assert outside == [], (
            f"the walk from {', '.join(WALK_ENTRY_MODULES)} stops at {module}, which imports {', '.join(outside)} "
            "outside WIDTH_AND_TELEMETRY_MODULES, so the roster does not hash that code although the walk runs it. "
            f"Import a leaf of the set instead of {module}, or move the code out of the walk's reach."
        )
        queue.extend(onward)


def test_every_named_path_exists():
    """Every path `settle_memo_code_paths` returns must exist. `fingerprint.hash_paths` skips a missing path without error, so a renamed module would drop out of the stamp silently. This also covers `Cargo.toml` and `Cargo.lock`, which `settle_memo_code_paths` names directly; the Rust sources come from a glob."""
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in settle_memo_keys.settle_memo_code_paths(REPO_ROOT)
        if not path.is_file()
    ]
    assert missing == [], f"the settle memo's stamp names files that are not there: {', '.join(missing)}"
