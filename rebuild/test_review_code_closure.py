"""Checks the code roster that stamps the review corpus against the import closure it is meant to cover.

`fingerprint.review_code_paths` hashes rebuild/review/ minus `REVIEW_NON_BUILD_MODULES`, and that exclusion list must name exactly the modules the corpus build never imports. An excluded module that the build starts importing would leave the corpus stale after an edit to it. A hashed module that the build never imports forces a corpus rebuild for an edit that cannot change the corpus. These tests walk the import graph from `rebuild.review.build` and check the list in both directions, at the same module granularity as rebuild/test_settle_memo_code_closure.py.

The walk expands rebuild/review, rebuild/pipeline and rebuild/validation, and records, without expanding, the modules the build reaches under rebuild/tools, which must all be in `WIDTH_AND_TELEMETRY_MODULES`, the set of rebuild/tools modules that cannot change a unit's output. A test checks that, so a new import of a module that could change output fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "review"
PIPELINE_DIR = REPO_ROOT / "rebuild" / "pipeline"
VALIDATION_DIR = REPO_ROOT / "rebuild" / "validation"
EXPANDED_DIRS = (REVIEW_DIR, PIPELINE_DIR, VALIDATION_DIR)

BUILD_ENTRY_MODULES = ("rebuild.review.build",)
# The rebuild/tools modules a code-closure walk records without expanding, because none can change what the code it stamps produces. The fan-out widths: `artifact_cycle` and `memory_budget` under it and under kernel_exec's own width. The cost and progress readings: `peak_rss`, the `cycle_timings` pool record, and the `console` phase and progress lines the cycle reads back. A leaf module the fingerprints read: `lock_digest`, the `uv.lock` digest, a leaf because the pyright gate, which the root conftest imports, hashes the lock with it. A leaf module of paths, `cycle_paths`, for the pass's fold claim (`row_delta.claim_fold`), which decides whether an output held as a base and segments folds or writes a segment this pass, never its materialized form. The corpus build reaches some of them; rebuild/test_settle_memo_code_closure.py's walk, which decides what the settle memo's stamp hashes, and rebuild/test_build_code_closure.py's `_table_build_reach`, which decides what `run_m1.memo_structure_stamp` hashes, stop at the whole set, so a new module here needs the argument that it cannot move a unit's bytes, a settle memo entry or a crate memo entry.
WIDTH_AND_TELEMETRY_MODULES = frozenset(
    {
        "rebuild.tools.artifact_cycle",
        "rebuild.tools.console",
        "rebuild.tools.cycle_paths",
        "rebuild.tools.cycle_timings",
        "rebuild.tools.lock_digest",
        "rebuild.tools.memory_budget",
        "rebuild.tools.peak_rss",
    }
)


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Return every repo module (under `rebuild` or `tools`) that the file imports, at any nesting level. A `from X import y` is recorded as both X and X.y, so a submodule import is followed. A relative import, which rebuild/validation uses, is resolved against the file's package first."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                package = ".".join(base.relative_to(REPO_ROOT).parts)
                module = f"{package}.{node.module}" if node.module else package
            elif node.module:
                module = node.module
            else:
                continue
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """Return the repo modules the entry points import, transitively, keyed by module name. Modules under rebuild/review, rebuild/pipeline, and rebuild/validation are expanded; modules elsewhere are recorded but not expanded (the module docstring says why)."""
    seen: dict[str, Path] = {}
    queue = list(entry_points)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        if path.parent in EXPANDED_DIRS:
            queue.extend(_imports(path))
    return seen


def _reached() -> dict[str, Path]:
    reached = reachable_modules(BUILD_ENTRY_MODULES)
    assert "rebuild.review.diff_units" in reached, "the walk found nothing; the entry point moved"
    return reached


def _reached_files_under(directory: Path) -> set[Path]:
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    return {path for path in _reached().values() if path.parent == directory and path.name != "__init__.py"}


def _relative(paths) -> list[str]:
    return sorted(str(path.relative_to(REPO_ROOT)) for path in paths)


def test_every_review_module_the_build_reaches_is_stamped():
    stamped = set(fingerprint.review_code_paths(REPO_ROOT))
    unstamped = _relative(_reached_files_under(REVIEW_DIR) - stamped)
    assert unstamped == [], (
        "these modules run in the corpus build but review_code does not hash them, so an edit "
        "to them would leave the corpus stale — remove them from REVIEW_NON_BUILD_MODULES: "
        + ", ".join(unstamped)
    )


def test_no_stamped_review_module_is_outside_the_builds_reach():
    reached = _reached_files_under(REVIEW_DIR)
    strays = _relative(
        path
        for path in fingerprint.review_code_paths(REPO_ROOT)
        if path not in reached and path.name != "__init__.py"
    )
    assert strays == [], (
        "review_code hashes these modules but the corpus build never imports them, so an edit to one costs "
        "a corpus rebuild while proving nothing — add them to REVIEW_NON_BUILD_MODULES: " + ", ".join(strays)
    )


def test_the_build_reaches_nothing_outside_the_three_trees_but_width_and_telemetry():
    """Every module the build reaches outside the three expanded trees is in `WIDTH_AND_TELEMETRY_MODULES`, so a new import of a module that could change a unit's output fails here."""
    outside = {name for name, path in _reached().items() if path.parent not in EXPANDED_DIRS}
    assert "rebuild.tools.console" in outside, (
        "the walk no longer crosses the rebuild/tools boundary at all, so this check exercises nothing; "
        "the build stopped printing its progress through console"
    )
    assert outside <= WIDTH_AND_TELEMETRY_MODULES, (
        "the corpus build reaches modules outside rebuild/review, rebuild/pipeline and rebuild/validation that "
        "WIDTH_AND_TELEMETRY_MODULES does not vouch for — argue that each cannot move a unit's bytes before "
        f"adding it: {sorted(outside - WIDTH_AND_TELEMETRY_MODULES)}"
    )


def test_every_module_in_width_and_telemetry_is_on_disk():
    """The set is a stop list three walks share, so a renamed member would stop no walk and fail none of them."""
    missing = sorted(name for name in WIDTH_AND_TELEMETRY_MODULES if _module_path(name) is None)
    assert (
        missing == []
    ), f"WIDTH_AND_TELEMETRY_MODULES names modules that are not on disk: {', '.join(missing)}"


LABELS_LEAF_IMPORTS = frozenset({"rebuild.pipeline.model", "rebuild.pipeline.settle"})


def test_the_labels_leaf_imports_nothing_that_would_regrow_the_closure():
    """`rebuild/pipeline/labels.py` gives the corpus build the sweep's vocabulary without importing the sweep, so it may import only `model` and `settle`. A new import there would add its module to the build's closure. This test pins the import list so the change fails here with that diagnosis."""
    imports = {
        name
        for name in _imports(PIPELINE_DIR / "labels.py")
        if (path := _module_path(name)) is not None and path.name != "__init__.py"
    }
    assert imports == LABELS_LEAF_IMPORTS, sorted(imports ^ LABELS_LEAF_IMPORTS)
