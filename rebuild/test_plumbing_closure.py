"""Checks that the verdict plumbing's green-record key hashes all the code the verdict chain runs.

The plumbing skips the chain when the key matches its green record, so the key must hash every module the chain executes. This test walks the import graph from `PLUMBING_ENTRY_POINTS` (rebuild.tools.verdict_chain, which runs every step) and requires every repo module it reaches to be in a fingerprint the key includes. `PLUMBING_TOOL_MODULES` lists the chain's tools instead of hashing all of rebuild/tools/, so that an edit to an unrelated tool does not re-run the chain, and this test keeps that list equal to the walked closure. The cycle driver is not an entry point: every argument it passes the chain names an input the key already hashes (the surface, the master, the store), a flag that disables the skip, or a width (`--standing-fill-jobs`) the chain's output does not depend on. The chain parses its own flags in verdict_chain.

The walk records a module in `fingerprint.pipeline_code_paths` but does not follow its imports, like rebuild/test_review_code_closure.py does at the rebuild/tools boundary. The plumbing key includes the pipeline_code component whole through its manifest line, so what a pipeline module imports beyond that component is outside this test's scope. That boundary is not sealed: pipeline modules import some rebuild/tools modules (fingerprint.py imports lock_digest and site_fonts, kernel_exec.py imports memory_budget), which `pipeline_code_paths` does not hash and this walk does not see. `oracle_cache.ORACLE_ROW_CODE_PATHS` names those modules because its closure test follows imports through the pipeline.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.tools import artifact_cycle as ac

REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Return every repo module the file imports. Only absolute imports are read. The modules whose imports this walk follows have no relative imports; rebuild/validation/shaping.py has some, but the walk stops at it because it is in the pipeline_code component. A `from X import y` is recorded as both X and X.y so that a submodule import is followed."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """Return the repo modules the entry points import transitively, keyed by module name. A module in the pipeline_code component is recorded but its imports are not followed (see the module docstring)."""
    seen: dict[str, Path] = {}
    pipeline = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    queue = list(entry_points)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        if path not in pipeline:
            queue.extend(_imports(path))
    return seen


def _covered(root: Path) -> set[Path]:
    covered = set(ac.plumbing_code_paths(root))
    covered.update(fingerprint.review_code_paths(root))
    covered.update(fingerprint.pipeline_code_paths(root))
    covered.add(root / "rebuild" / "review" / "serve.py")
    covered.add(root / "rebuild" / "review" / "verdict_store.py")
    covered.add(root / "rebuild" / "review" / "status.py")
    covered.add(root / "rebuild" / "review" / "journal.py")
    return covered


def test_the_plumbing_key_covers_every_module_its_chain_reaches():
    reached = reachable_modules(ac.PLUMBING_ENTRY_POINTS)
    assert "rebuild.tools.standing_verdicts" in reached, "the walk found nothing; the entry points moved"
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    files = {path for path in reached.values() if path.name != "__init__.py"}
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - _covered(REPO_ROOT))
    assert uncovered == [], (
        "these modules run in the verdict chain but no fingerprint the plumbing key carries hashes them, "
        f"so a fix to one would be skipped as already proven: {', '.join(uncovered)}"
    )


def test_the_named_tool_closure_holds_no_module_the_chain_never_reaches():
    """Every file `PLUMBING_TOOL_MODULES` names must be reachable from the entry points, so that the list does not grow into all of rebuild/tools/."""
    reached = {path for path in reachable_modules(ac.PLUMBING_ENTRY_POINTS).values()}
    strays = sorted(
        str(path.relative_to(REPO_ROOT)) for path in ac.plumbing_code_paths(REPO_ROOT) if path not in reached
    )
    assert strays == [], f"named in PLUMBING_TOOL_MODULES but unreachable from the chain: {', '.join(strays)}"


def test_every_named_path_exists():
    missing = [str(path) for path in ac.plumbing_code_paths(REPO_ROOT) if not path.is_file()]
    assert missing == [], f"PLUMBING_TOOL_MODULES names files that are not there: {', '.join(missing)}"


def test_the_driver_and_the_width_and_telemetry_tools_stay_outside_the_chain():
    """The cycle driver, the timings journal, and the memory-budget and peak-RSS modules must stay outside the chain's closure. Their edits cannot change a verdict, and any of them inside the key would re-run the whole chain on such an edit. A chain tool that starts importing one should fail here; adding the module to `PLUMBING_TOOL_MODULES` is not the fix."""
    outside = ("artifact_cycle", "cycle_timings", "memory_budget", "peak_rss")
    reached = reachable_modules(ac.PLUMBING_ENTRY_POINTS)
    inside = sorted(name for name in outside if f"rebuild.tools.{name}" in reached)
    assert inside == [], f"the chain reaches these again, so an edit to one re-runs it: {', '.join(inside)}"
    boundary = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    assert any(path in boundary for path in reached.values()), (
        "the walk no longer reaches any module riding the pipeline_code component, "
        "so this test is passing by not exercising the boundary at all"
    )
    named = sorted(path.stem for path in ac.plumbing_code_paths(REPO_ROOT) if path.stem in outside)
    assert named == [], f"PLUMBING_TOOL_MODULES names them anyway: {', '.join(named)}"
