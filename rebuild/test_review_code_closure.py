"""`fingerprint.review_code_paths` is rebuild/review/ minus an exclusion list (`REVIEW_NON_BUILD_MODULES`), and the surface stamp's whole claim is that the list names exactly what the build cannot execute: an excluded module the build starts importing would leave the surface stale-blind to its edits, while a hashed module the build never reaches costs a full surface rebuild and both per-unit cache stores for an edit that proves nothing — the deforming pressure rebuild/test_memory_budget.py records declining a one-line hoist over. A hand-written exclusion list is only safe while something checks it, and this is that check: it walks the import graph from rebuild.review.build in both directions, at the same module grain as rebuild/test_plumbing_closure.py and rebuild/test_oracle_code_closure.py.

One scoping choice those two do not need: the walk expands only modules inside rebuild/review. The roster under test hashes review/*.py and nothing else — rebuild/pipeline rides in the stamp's pipeline_code component and the chain's tools in the plumbing key — so a review module reached only through an out-of-package module (build.py lazily imports artifact_cycle for its --jobs default, and the driver's own lazy imports reach status.py) is that component's coverage question, not this roster's; following such an edge would force the build's stamp to hash plumbing-side modules the build never calls, which is the exact over-reach this roster exists to end.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "review"

BUILD_ENTRY_MODULES = ("rebuild.review.build",)


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _imports(path: Path) -> set[str]:
    """Every repo module the file names in an import, absolute form only — this tree has no relative imports, and a `from X import y` is recorded both as X and as X.y so a submodule import is followed."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] in ("rebuild", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """The transitive closure of repo modules the entry points import, keyed by module name, expanding only modules inside rebuild/review (see the module docstring for why the walk stops at the package boundary)."""
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
        if path.parent == REVIEW_DIR:
            queue.extend(_imports(path))
    return seen


def _reached_review_files() -> set[Path]:
    reached = reachable_modules(BUILD_ENTRY_MODULES)
    assert "rebuild.review.unit_index" in reached, "the walk found nothing; the entry point moved"
    return {path for path in reached.values() if path.parent == REVIEW_DIR and path.name != "__init__.py"}


def test_every_review_module_the_build_reaches_is_stamped():
    stamped = set(fingerprint.review_code_paths(REPO_ROOT))
    unstamped = sorted(str(path.relative_to(REPO_ROOT)) for path in _reached_review_files() - stamped)
    assert unstamped == [], (
        "these modules run in the surface build but review_code does not hash them, so the surface would "
        "go stale-blind to their edits — remove them from REVIEW_NON_BUILD_MODULES: " + ", ".join(unstamped)
    )


def test_no_stamped_review_module_is_outside_the_builds_reach():
    reached = _reached_review_files()
    strays = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in fingerprint.review_code_paths(REPO_ROOT)
        if path not in reached and path.name != "__init__.py"
    )
    assert strays == [], (
        "review_code hashes these modules but the surface build never imports them, so an edit to one costs "
        "a full surface rebuild and both per-unit cache stores while proving nothing — add them to "
        "REVIEW_NON_BUILD_MODULES: " + ", ".join(strays)
    )
