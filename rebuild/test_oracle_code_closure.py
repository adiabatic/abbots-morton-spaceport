"""`oracle_cache.ORACLE_ROW_CODE_PATHS` is an enumerated roster rather than a glob over `rebuild/pipeline/`, and every served row rests on the claim that the roster is the comparison's whole code closure — a module that runs in `_compare_row` or `_SettledWindowWalk` but sits outside the stamp is a module whose fix a served run would skip, handing back the pre-fix verdict as fresh. A hand-written roster is only safe while something checks the name, and this is that check: it walks the import graph from the module those two entry points live in and requires everything reachable to be named, then walks the other direction so the roster stays the closure instead of drifting back into "everything under `rebuild/pipeline/`", which would collapse the store on every unrelated pipeline commit.

The walk is at module grain, not function grain, which is the same approximation `rebuild/test_plumbing_closure.py` makes and is conservative in the safe direction: a module conform imports for a purpose the comparison never exercises is still stamped, and the reverse — a module the comparison reaches that the import graph does not — cannot happen without a dynamic import, which this tree does not use. It follows `if TYPE_CHECKING:` imports too, because `ast.walk` does not care about the guard and because a type-only import is still a file whose contents shape the comparison's behavior; `rebuild/pipeline/emit_gsub.py` is on the roster for exactly that reason and would read as a stray under a walk that skipped them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import conform, oracle_cache

REPO_ROOT = Path(__file__).resolve().parent.parent

ORACLE_ENTRY_MODULES = ("rebuild.pipeline.conform",)


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
    """The transitive closure of repo modules the entry points import, keyed by module name."""
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
        queue.extend(_imports(path))
    return seen


def _reached_files() -> set[Path]:
    reached = reachable_modules(ORACLE_ENTRY_MODULES)
    # A package's empty __init__.py carries no behavior for a fingerprint to protect.
    return {path for path in reached.values() if path.name != "__init__.py"}


def test_both_comparison_entry_points_live_in_the_walked_module():
    """The roster's claim is about `_compare_row` and `_SettledWindowWalk` specifically, so the module the walk starts from has to be the module those two are still defined in — move either one to a new file and the walk below would keep passing while covering the wrong graph."""
    source = ast.parse((REPO_ROOT / "rebuild" / "pipeline" / "conform.py").read_text(encoding="utf-8"))
    defined = {node.name for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert {"_compare_row", "_SettledWindowWalk"} <= defined, (
        "the oracle cache's two comparison entry points are no longer defined in rebuild/pipeline/conform.py, "
        f"so ORACLE_ENTRY_MODULES names the wrong graph; conform.py defines {sorted(defined & {'_compare_row', '_SettledWindowWalk'})}"
    )
    assert hasattr(conform, "_compare_row") and hasattr(conform, "_SettledWindowWalk")


def test_oracle_row_code_paths_covers_the_comparison_import_graph():
    files = _reached_files()
    assert (
        REPO_ROOT / "rebuild" / "pipeline" / "settle.py" in files
    ), "the walk found nothing; the entry module moved"
    named = {REPO_ROOT / relative for relative in oracle_cache.ORACLE_ROW_CODE_PATHS}
    uncovered = sorted(str(path.relative_to(REPO_ROOT)) for path in files - named)
    assert uncovered == [], (
        "these modules run in the oracle's comparison but ORACLE_ROW_CODE_PATHS does not name them, so the whole-store "
        "stamp does not hash them and a fix to one would be served around as though the previous pass had already "
        f"applied it: {', '.join(uncovered)}. Add each to ORACLE_ROW_CODE_PATHS in rebuild/pipeline/oracle_cache.py, "
        "or move the code out of the comparison's reach."
    )


def test_the_named_oracle_closure_holds_no_module_the_comparison_never_reaches():
    """The other direction, so the roster stays the closure rather than growing into a glob over `rebuild/pipeline/`: every module it names must actually be reachable from the comparison. A stray name costs no correctness, only the store — it drops the whole cache on a commit that could not have moved a verdict."""
    files = _reached_files()
    strays = sorted(
        relative for relative in oracle_cache.ORACLE_ROW_CODE_PATHS if REPO_ROOT / relative not in files
    )
    assert strays == [], (
        "named in ORACLE_ROW_CODE_PATHS but unreachable from the comparison, so every store drops whenever one of "
        f"them moves and nothing is bought for it: {', '.join(strays)}"
    )


def test_every_named_oracle_path_exists():
    """A rename that leaves the roster behind drops a module out of the stamp silently — `fingerprint.hash_paths` reads a missing path as a stable absence, not as an error — so the roster's paths are checked against the disk directly. This covers the crate manifests too, since `oracle_code_paths` names `Cargo.toml` and `Cargo.lock` outright while it globs the Rust sources."""
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in oracle_cache.oracle_code_paths(REPO_ROOT)
        if not path.is_file()
    ]
    assert missing == [], f"the oracle row cache's stamp names files that are not there: {', '.join(missing)}"
