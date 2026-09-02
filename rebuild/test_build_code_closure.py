"""`fingerprint.COMPARISON_CODE_MODULES` is the roster of pipeline modules a serialized window enumeration's stamp leaves out (`fingerprint.table_code_paths`, the code half of `tables_value`), and every reuse that stamp licenses — `run_m1 --gates-only` re-adjudicating over the tables and M1.otf on disk, `--conform-only` sweeping them, the validators lane refusing a stale enumeration — rests on the claim that nothing on the roster can move a table or the font. A hand-written roster is only safe while something checks the claim, and this is that check, in two halves. The first walks the import graph from every build-side module and requires it to reach nothing on the roster: at module grain, the same approximation `rebuild/test_oracle_code_closure.py` makes, conservative in the safe direction, and following `if TYPE_CHECKING:` imports for the same reason that test does. The second is the driver, `rebuild/pipeline/run_m1.py`, which cannot be held to that rule — it imports the comparison side because it also runs the gates — so it is walked at function grain instead: from `run_m1.run`, the build entry, through every module-level function it calls, no expression may name a comparison-side module or anything imported from one.

The reverse direction holds too: every roster entry must be a module the driver reaches, so the roster stays the set of modules the gates run and cannot grow a stray that nothing exercises. And because the whole point of splitting the classifier out of conform.py was to put it outside the oracle row cache's stamp as well as the tables', the roster is also checked against `oracle_cache.ORACLE_ROW_CODE_PATHS` — a comparison-side module named there would drop the store on every classifier edit for nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint, oracle_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / "rebuild" / "pipeline"
BUILD_DRIVER = PIPELINE / "run_m1.py"
BUILD_ENTRY = "run"


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _imports(tree: ast.AST) -> set[str]:
    """Every repo module the tree names in an import, absolute form only — this tree has no relative imports, and a `from X import y` is recorded both as X and as X.y so a submodule import is followed."""
    found: set[str] = set()
    for node in ast.walk(tree):
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
        queue.extend(_imports(ast.parse(path.read_text(encoding="utf-8"))))
    return seen


def _comparison_paths() -> set[Path]:
    return {PIPELINE / name for name in fingerprint.COMPARISON_CODE_MODULES}


def _build_side_paths() -> list[Path]:
    """The Python half of `table_code_paths`, minus the driver: every module the stamp claims is build-side and can be held to the module-grain rule."""
    return [
        path
        for path in fingerprint.table_code_paths(REPO_ROOT)
        if path.suffix == ".py" and path != BUILD_DRIVER
    ]


def test_every_roster_entry_is_a_pipeline_module_on_disk():
    missing = sorted(name for name in fingerprint.COMPARISON_CODE_MODULES if not (PIPELINE / name).is_file())
    assert (
        missing == []
    ), f"COMPARISON_CODE_MODULES names files that are not under rebuild/pipeline/: {', '.join(missing)}"


def test_table_code_paths_is_pipeline_code_minus_exactly_the_roster():
    pipeline_code = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    table_code = set(fingerprint.table_code_paths(REPO_ROOT))
    assert table_code <= pipeline_code
    assert pipeline_code - table_code == _comparison_paths()
    assert PIPELINE / "conform.py" in table_code, "the producer side of the comparison is still stamped"
    assert PIPELINE / "run_m1.py" in table_code, "the driver is still stamped"


def test_no_build_side_module_reaches_the_comparison_side():
    comparison = _comparison_paths()
    offenders: list[str] = []
    walked = 0
    for path in _build_side_paths():
        reached = set(reachable_modules((_module_name(path),)).values())
        walked += 1
        for hit in sorted(reached & comparison):
            offenders.append(f"{path.relative_to(REPO_ROOT)} -> {hit.relative_to(REPO_ROOT)}")
    assert walked > 0, "the walk found nothing; table_code_paths moved"
    assert offenders == [], (
        "these build-side modules import a comparison-side module, so an edit there could change a table or the font "
        "while the enumeration's stamp stays put: "
        + "; ".join(offenders)
        + ". Either the import has to go, or the "
        "module has to leave COMPARISON_CODE_MODULES in rebuild/pipeline/fingerprint.py."
    )


def _comparison_bindings(tree: ast.Module) -> set[str]:
    """Every name the driver binds to a comparison-side module or to something imported from one, at any depth — a function-local import binds a name too."""
    comparison_modules = {_module_name(path) for path in _comparison_paths()}
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in comparison_modules:
                    bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if node.module in comparison_modules or f"{node.module}.{alias.name}" in comparison_modules:
                    bound.add(alias.asname or alias.name)
    return bound


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _build_entry_reach(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The module-level functions `run` calls, transitively, and every dotted name any of them mentions."""
    functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert (
        BUILD_ENTRY in functions
    ), f"{BUILD_DRIVER.name} no longer defines {BUILD_ENTRY}; BUILD_ENTRY names the wrong function"
    reached: set[str] = set()
    mentioned: set[str] = set()
    queue = [BUILD_ENTRY]
    while queue:
        name = queue.pop()
        if name in reached:
            continue
        reached.add(name)
        for node in ast.walk(functions[name]):
            dotted = _dotted(node)
            if dotted is None:
                continue
            mentioned.add(dotted)
            head = dotted.split(".")[0]
            if head in functions and head not in reached:
                queue.append(head)
    return reached, mentioned


def test_the_build_entry_never_names_the_comparison_side():
    tree = ast.parse(BUILD_DRIVER.read_text(encoding="utf-8"))
    bindings = _comparison_bindings(tree)
    assert (
        bindings
    ), f"{BUILD_DRIVER.name} binds no comparison-side name, so this check would be vacuous; the driver stopped importing the gates it runs"
    reached, mentioned = _build_entry_reach(tree)
    assert "build_tables" in reached, "the walk from `run` never reached build_tables; the build entry moved"
    offenders = sorted(name for name in mentioned if name.split(".")[0] in bindings)
    assert offenders == [], (
        f"the build entry `{BUILD_ENTRY}` reaches the comparison side through {', '.join(offenders)} (via "
        f"{', '.join(sorted(reached))}), so a comparison-side edit could move a table or the font while the "
        "enumeration's stamp stays put"
    )


def test_every_roster_entry_is_reached_from_the_driver():
    """The other direction, so the roster stays the set of modules the gates actually run: a stray entry costs no correctness, but it would be a module nothing exercises claiming a place outside the stamp."""
    reached = set(reachable_modules((_module_name(BUILD_DRIVER),)).values())
    strays = sorted(str(path.relative_to(REPO_ROOT)) for path in _comparison_paths() if path not in reached)
    assert (
        strays == []
    ), f"named in COMPARISON_CODE_MODULES but unreachable from {BUILD_DRIVER.name}: {', '.join(strays)}"


def test_the_comparison_side_is_outside_the_oracle_row_cache_stamp():
    named = {REPO_ROOT / relative for relative in oracle_cache.ORACLE_ROW_CODE_PATHS}
    inside = sorted(str(path.relative_to(REPO_ROOT)) for path in _comparison_paths() & named)
    assert inside == [], (
        "these comparison-side modules ride ORACLE_ROW_CODE_PATHS, so every classifier edit drops the whole store for "
        f"nothing — the classifier re-runs over served rows regardless: {', '.join(inside)}"
    )
