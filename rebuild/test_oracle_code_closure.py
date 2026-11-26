"""Checks that `oracle_cache.POSITION_CODE_PATHS` names exactly the code the oracle's position comparison runs. The row comparison's roster is the settle memo's (`settle_memo_keys.SETTLE_MEMO_CODE_PATHS`), which rebuild/test_settle_memo_code_closure.py checks.

The walk is at module grain, as in `rebuild/test_review_code_closure.py`, with the same stop-set, rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES`, which the walk neither records nor follows. A test checks that every member the comparison reaches imports, transitively, only other members.

The position comparison's walk starts from `rebuild/pipeline/oracle_positions.py`, and `POSITION_CODE_PATHS` must name exactly the modules it reaches outside the stop-set. The position stamp is checked on its own, without the row stamp, because a stored position is served across a move of the row stamp when the row's settled stream did not change, so a module the position comparison reaches is covered only when that roster names it. `rebuild/pipeline/oracle.py`, the classifier, must be unreachable from the position comparison: it re-runs over every served verdict and is in neither roster, and if the position comparison imported it, a predicate edit would change `position_code` and re-shape every position. The row store, the settlement walk and the crate driver must be unreachable too, or an edit to one would re-shape every position where the settled digest already covers what it decides.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import conform, oracle_cache, oracle_positions, settle_memo_keys
from rebuild.test_review_code_closure import WIDTH_AND_TELEMETRY_MODULES

REPO_ROOT = Path(__file__).resolve().parent.parent

POSITION_ENTRY_MODULES = ("rebuild.pipeline.oracle_positions",)
POSITION_COMPARISON_NAMES = frozenset(
    {
        "_position_mismatch",
        "_kern_normalized_positions",
        "_cached_position",
        "_served_position",
        "settled_digest",
        "_verify_served_positions",
        "KernEvaluator",
        "_shaper_for",
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


def test_both_comparison_entry_points_live_in_the_walked_module():
    """The row comparison's walk starts from conform.py (rebuild/test_settle_memo_code_closure.py checks the roster it gives), so `_compare_row` and `_SettledWindowWalk` must be defined there. If either moved to another file, the roster tests would keep passing while covering the wrong graph."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "conform.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert {"_compare_row", "_SettledWindowWalk"} <= defined, (
        "the oracle cache's two comparison entry points are no longer defined in rebuild/pipeline/conform.py, "
        f"so the walk names the wrong graph; conform.py defines {sorted(defined & {'_compare_row', '_SettledWindowWalk'})}"
    )
    assert hasattr(conform, "_compare_row") and hasattr(conform, "_SettledWindowWalk")


def _position_reached_files() -> set[Path]:
    reached = reachable_modules(POSITION_ENTRY_MODULES)
    return {path for path in reached.values() if path.name != "__init__.py"}


def test_the_position_comparison_s_entry_points_live_in_the_walked_module():
    """The position walk starts from oracle_positions.py, so every name in `POSITION_COMPARISON_NAMES` must be defined there. If one moved to oracle.py, the walk tests would keep passing while the stamp covered the wrong code."""
    source = ast.parse(
        (REPO_ROOT / "rebuild" / "pipeline" / "oracle_positions.py").read_text(encoding="utf-8")
    )
    defined = {node.name for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert POSITION_COMPARISON_NAMES <= defined, (
        "the position comparison's entry points are not all defined in rebuild/pipeline/oracle_positions.py, so "
        f"POSITION_ENTRY_MODULES names the wrong graph; it defines {sorted(defined & POSITION_COMPARISON_NAMES)}"
    )
    assert all(hasattr(oracle_positions, name) for name in POSITION_COMPARISON_NAMES)


def test_the_position_comparison_s_import_graph_is_inside_its_own_roster():
    """A stored position is served across a move of the row stamp, so the position stamp covers the position comparison's code on its own: every module the comparison reaches outside the walk's stop-set (see the module docstring) must be named by `POSITION_CODE_PATHS`. A fix to a module it does not name would be served around as though the previous pass had already applied it."""
    files = _position_reached_files()
    assert (
        REPO_ROOT / "rebuild" / "pipeline" / "shapers.py" in files
    ), "the walk found nothing; the entry module moved"
    named = {REPO_ROOT / relative for relative in oracle_cache.POSITION_CODE_PATHS}
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - named)
    assert uncovered == [], (
        "these modules run in the oracle's position comparison but POSITION_CODE_PATHS does not name them, so a fix "
        f"to one would be served around as though the previous pass had already applied it: {', '.join(uncovered)}"
    )


def test_the_position_comparison_reaches_neither_the_store_nor_the_walk():
    """The position stamp leaves out the row store (oracle_cache.py), the settlement walk and settle memo (conform.py) and the crate driver (kernel_exec.py), because the settled digest a record keeps covers what they decide, so an edit to one re-derives every row and re-shapes only the rows whose settled cells changed. The roster tests would accept the position comparison importing one of them again if `POSITION_CODE_PATHS` named it too; this test fails on that import, which would re-shape every position on the commonest pipeline edits."""
    reached = reachable_modules(POSITION_ENTRY_MODULES)
    for module in (
        "rebuild.pipeline.oracle_cache",
        "rebuild.pipeline.conform",
        "rebuild.pipeline.kernel_exec",
    ):
        assert module not in reached, f"the position comparison's import closure reaches {module}"
        assert f"{module.replace('.', '/')}.py" not in oracle_cache.POSITION_CODE_PATHS


def test_the_classifier_lives_outside_the_position_comparison_s_closure():
    """The classifier and the ledger match are defined in rebuild/pipeline/oracle.py, the position comparison's walk does not reach `rebuild.pipeline.oracle`, and neither roster names oracle.py. A predicate edit therefore keeps every stored position and every stored row. This test fails if the position comparison imports oracle.py, even when a roster is extended to cover it."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "oracle.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, ast.FunctionDef)}
    assert {"classify_divergence", "compile_ledger", "_match_compiled"} <= defined
    assert "rebuild.pipeline.oracle" not in reachable_modules(
        POSITION_ENTRY_MODULES
    ), "the position comparison's import closure reaches oracle.py, so a classifier edit re-shapes every position"
    assert "rebuild/pipeline/oracle.py" not in settle_memo_keys.SETTLE_MEMO_CODE_PATHS
    assert "rebuild/pipeline/oracle.py" not in oracle_cache.POSITION_CODE_PATHS


def test_the_position_roster_names_only_the_position_comparison_and_every_entry_exists():
    """Every path in `POSITION_CODE_PATHS` must exist and be reachable from the position comparison. `settle_memo_keys.settle_memo_code_paths` does not cover this roster and `fingerprint.hash_paths` skips a missing path, so a renamed position comparison module would drop out of the position stamp silently. A name the walk never reaches would re-shape every position on an edit that could not change one."""
    files = _position_reached_files()
    missing = [
        relative for relative in oracle_cache.POSITION_CODE_PATHS if not (REPO_ROOT / relative).is_file()
    ]
    assert missing == [], f"POSITION_CODE_PATHS names files that are not there: {', '.join(missing)}"
    strays = [relative for relative in oracle_cache.POSITION_CODE_PATHS if REPO_ROOT / relative not in files]
    assert (
        strays == []
    ), f"named in POSITION_CODE_PATHS but unreachable from the position comparison: {', '.join(strays)}"
