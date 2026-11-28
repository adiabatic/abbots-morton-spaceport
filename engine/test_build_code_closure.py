"""Checks that `fingerprint.GATE_SIDE_CODE_MODULES` and `run_build.TABLE_BUILD_CODE_MODULES` match the import graph, and that the build runs no module outside the trees `fingerprint.pipeline_code_paths` hashes.

`GATE_SIDE_CODE_MODULES` lists, as repo-relative paths, the modules of `pipeline_code_paths` left out of a serialized window enumeration's stamp (`fingerprint.table_code_paths`, the code half of `tables_value`). Reusing the tables and Senior-Regular.otf on disk, as `run_build --gates-only` and `--conform-only` do, is safe only if no module on that list can change a table or the font. The first check walks the import graph from every build-side module and requires it to reach nothing on the list. Like `engine/test_settle_memo_code_closure.py`, it works at module grain and follows `if TYPE_CHECKING:` imports, which errs toward including too much. The driver, `engine/pipeline/run_build.py`, reaches the gate side through engine/pipeline/run_build_gates.py from `full_run` and its `python -m` entry, because a full run also runs the gates, so it is checked at function grain instead: no expression in `run_build.run`, or in any module-level function it calls transitively, may name a gate-side module or anything imported from one.

The reverse also holds: every entry on the list must be a module the driver reaches, so the list names only modules the gates run. The list is also checked against `settle_memo_keys.SETTLE_MEMO_CODE_PATHS`, because a gate-side module named there would drop every settle memo on a gate edit for no benefit.

The font compile's generic assembly (`font_assembly`, `glyph_records`, `departure_mono_import`) is package modules under engine/pipeline, which `pipeline_code_paths` and `table_code_paths` hash whole, so no roster names them. What would escape both is a module the build imports by bare name from a directory those trees leave out, so a check holds the build's Python (`pipeline_code_paths`) to importing no tools/ or test/ module by bare name, and another holds the review corpus build's Python (`fingerprint.review_code_paths` and the modules the build reaches under engine/pipeline and engine/validation) to the same, since engine/test_review_code_closure.py follows only package imports. Neither check covers the engine/tools modules the pipeline imports, which `pipeline_code_paths` does not hash.

`TABLE_BUILD_CODE_MODULES` is the Python half of `run_build.table_build_code_paths`, the code that the table build record's structure stamp (`run_build.memo_structure_stamp`) and its `code` hash. The crate reads nothing but the spec dump and its command line, so the roster is the module-grain import closure of the crate driver and the dump's writer (`kernel_exec`, `kernel_io`), stopping at engine/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES` as engine/test_settle_memo_code_closure.py does, with the same check that every module the walk stops at imports only other members. A module the walk reaches that the roster misses could change what the crate reads while the stamp and `code` hold, so two builds of different inputs would pair as the same; a roster entry the walk never reaches moves both on an edit the crate cannot see. A second check holds the paths to the whole crate plus the roster, every one on disk, because `fingerprint.hash_paths` skips a missing file.
"""

from __future__ import annotations

import ast
from pathlib import Path

from engine.pipeline import fingerprint, run_build, settle_memo_keys
from engine.test_review_code_closure import BUILD_ENTRY_MODULES as CORPUS_ENTRY_MODULES
from engine.test_review_code_closure import WIDTH_AND_TELEMETRY_MODULES

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / "engine" / "pipeline"
BUILD_DRIVER = PIPELINE / "run_build.py"
BUILD_ENTRY = "run"
TOOLS = REPO_ROOT / "tools"
TEST_DIR = REPO_ROOT / "test"
TABLE_BUILD_ENTRY_MODULES = ("engine.pipeline.kernel_exec", "engine.pipeline.kernel_io")


def _module_path(module: str) -> Path | None:
    path = REPO_ROOT / Path(*module.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    """Return every repo module the file imports. `from X import y` is recorded as both X and X.y, so a submodule import is followed. A relative import, which engine/validation uses, is resolved against the file's package first."""
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
    return {name for name in found if name.split(".")[0] in ("engine", "tools")}


def reachable_modules(entry_points: tuple[str, ...]) -> dict[str, Path]:
    """Return the transitive closure of repo modules the entry points import, keyed by module name."""
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


def _gate_side_paths() -> set[Path]:
    return {REPO_ROOT / relative for relative in fingerprint.GATE_SIDE_CODE_MODULES}


def _build_side_paths() -> list[Path]:
    """Return the Python files in `table_code_paths` except the driver: the build-side modules checked at module grain."""
    return [
        path
        for path in fingerprint.table_code_paths(REPO_ROOT)
        if path.suffix == ".py" and path != BUILD_DRIVER
    ]


def _tools_imports(tree: ast.AST) -> set[str]:
    """Return every name the tree imports at any nesting by an absolute name, the form a bare import of a module on `sys.path` takes."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _siblings(path: Path, directories: tuple[Path, ...] = (TOOLS,)) -> set[Path]:
    """Return the modules the file imports by bare name from the given `sys.path` directories. A name resolves only against <directory>/<head>.py, so stdlib, third-party, and `engine.*` imports are dropped. A name found in more than one directory yields every match, whatever the `sys.path` order."""
    found: set[Path] = set()
    for name in _tools_imports(ast.parse(path.read_text(encoding="utf-8"))):
        for directory in directories:
            sibling = directory / f"{name.split('.')[0]}.py"
            if sibling.is_file():
                found.add(sibling)
    return found


def test_the_build_imports_no_tools_or_test_module():
    """No Python module the build hashes (`pipeline_code_paths`) imports a tools/ or test/ module by bare name, at any nesting and including `if TYPE_CHECKING:` blocks. Such a module would run in the build while an edit to it moved no stamp, since `pipeline_code_paths` and `table_code_paths` hash engine/pipeline and engine/validation whole and nothing outside them."""
    build_code = [path for path in fingerprint.pipeline_code_paths(REPO_ROOT) if path.suffix == ".py"]
    assert build_code, "pipeline_code_paths holds no Python file; the trees moved"
    reached = sorted(
        f"{path.relative_to(REPO_ROOT).as_posix()} -> {sibling.relative_to(REPO_ROOT).as_posix()}"
        for path in build_code
        for sibling in _siblings(path, (TOOLS, TEST_DIR))
    )
    assert reached == [], (
        f"the build imports tools/ or test/ modules by bare name: {', '.join(reached)}. "
        "An edit to one of them would change the font while no stamp moves; the module belongs under engine/pipeline."
    )


def test_the_corpus_build_reaches_no_tools_or_test_module():
    """engine/test_review_code_closure.py's walk follows only package imports, so no Python module the review corpus build runs may import a tools/ or test/ module by bare name. Otherwise an edit to such a module could change a unit without restamping the corpus."""
    corpus_code = [path for path in reachable_modules(CORPUS_ENTRY_MODULES).values() if path.suffix == ".py"]
    assert corpus_code, "the walk from the corpus build found no Python file; the entry point moved"
    reached = sorted(
        f"{path.relative_to(REPO_ROOT).as_posix()} -> {sibling.relative_to(REPO_ROOT).as_posix()}"
        for path in corpus_code
        for sibling in _siblings(path, (TOOLS, TEST_DIR))
    )
    assert reached == [], (
        f"the corpus build imports tools/ or test/ modules by bare name: {', '.join(reached)}. "
        "An edit to one of them could change a unit without restamping the corpus."
    )


def test_the_font_assembly_is_in_both_the_run_record_and_the_tables_stamp():
    """The compile is on the build side, so the font assembly it passes the font to belongs in `table_code_paths` as well as `pipeline_code_paths`. A font-compile module left out of `table_code_paths` would let a serialized enumeration pass as current after its sources changed."""
    paths = {PIPELINE / name for name in ("font_assembly.py", "glyph_records.py", "departure_mono_import.py")}
    assert all(path.is_file() for path in paths)
    assert paths <= set(fingerprint.pipeline_code_paths(REPO_ROOT))
    assert paths <= set(fingerprint.table_code_paths(REPO_ROOT))


def test_every_roster_entry_is_a_pipeline_module_on_disk():
    pipeline_code = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    missing = sorted(
        relative
        for relative in fingerprint.GATE_SIDE_CODE_MODULES
        if REPO_ROOT / relative not in pipeline_code
    )
    assert (
        missing == []
    ), f"GATE_SIDE_CODE_MODULES names files that are not in pipeline_code_paths on disk: {', '.join(missing)}"


def test_table_code_paths_is_pipeline_code_minus_exactly_the_roster():
    pipeline_code = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    table_code = set(fingerprint.table_code_paths(REPO_ROOT))
    assert table_code <= pipeline_code
    assert pipeline_code - table_code == _gate_side_paths()
    assert PIPELINE / "conform.py" in table_code, "the conformance sweep is still stamped"
    assert PIPELINE / "run_build.py" in table_code, "the driver is still stamped"


def test_no_build_side_module_reaches_the_gate_side():
    gate_side = _gate_side_paths()
    offenders: list[str] = []
    walked = 0
    for path in _build_side_paths():
        reached = set(reachable_modules((_module_name(path),)).values())
        walked += 1
        for hit in sorted(reached & gate_side):
            offenders.append(f"{path.relative_to(REPO_ROOT)} -> {hit.relative_to(REPO_ROOT)}")
    assert walked > 0, "the walk found nothing; table_code_paths moved"
    assert offenders == [], (
        "these build-side modules import a gate-side module, so an edit there could change a table or the font "
        "while the enumeration's stamp stays put: "
        + "; ".join(offenders)
        + ". Either the import has to go, or the "
        "module has to leave GATE_SIDE_CODE_MODULES in engine/pipeline/fingerprint.py."
    )


def _gate_side_bindings(tree: ast.Module) -> set[str]:
    """Return every name the driver binds to a gate-side module or to something imported from one, including names bound by function-local imports."""
    gate_side_modules = {_module_name(path) for path in _gate_side_paths()}
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in gate_side_modules:
                    bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if node.module in gate_side_modules or f"{node.module}.{alias.name}" in gate_side_modules:
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
    """Return the module-level functions `run` calls, transitively, and every dotted name any of them mentions."""
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


def test_the_build_entry_never_names_the_gate_side():
    tree = ast.parse(BUILD_DRIVER.read_text(encoding="utf-8"))
    bindings = _gate_side_bindings(tree)
    assert (
        bindings
    ), f"{BUILD_DRIVER.name} binds no gate-side name, so this check would be vacuous; the driver stopped importing the gates it runs"
    reached, mentioned = _build_entry_reach(tree)
    assert "build_tables" in reached, "the walk from `run` never reached build_tables; the build entry moved"
    assert (
        "_run_defect_gates" in reached
    ), "the walk from `run` never reached _run_defect_gates; the defect gate a gates-only rerun shares with the build has to stay inside this walk, or a helper both entry points call could reach the gate side unchecked"
    offenders = sorted(name for name in mentioned if name.split(".")[0] in bindings)
    assert offenders == [], (
        f"the build entry `{BUILD_ENTRY}` reaches the gate side through {', '.join(offenders)} (via "
        f"{', '.join(sorted(reached))}), so a gate-side edit could move a table or the font while the "
        "enumeration's stamp stays put"
    )


def test_every_roster_entry_is_reached_from_the_driver():
    """Every entry on the list is a module the driver reaches, so the list names only modules the gates run. An extra entry causes no wrong result, but it excludes from the stamp a module that no gate runs."""
    reached = set(reachable_modules((_module_name(BUILD_DRIVER),)).values())
    strays = sorted(str(path.relative_to(REPO_ROOT)) for path in _gate_side_paths() if path not in reached)
    assert (
        strays == []
    ), f"named in GATE_SIDE_CODE_MODULES but unreachable from {BUILD_DRIVER.name}: {', '.join(strays)}"


def test_the_gate_side_is_outside_the_settle_memo_stamp():
    named = {REPO_ROOT / relative for relative in settle_memo_keys.SETTLE_MEMO_CODE_PATHS}
    inside = sorted(str(path.relative_to(REPO_ROOT)) for path in _gate_side_paths() & named)
    assert inside == [], (
        "these gate-side modules ride SETTLE_MEMO_CODE_PATHS, so every edit to one drops every settle memo for "
        f"nothing, since no settlement runs them: {', '.join(inside)}"
    )


def _table_build_reach() -> tuple[set[Path], set[str]]:
    """Return the files `TABLE_BUILD_ENTRY_MODULES` import transitively, without packages' empty `__init__.py`, and the members of `WIDTH_AND_TELEMETRY_MODULES` the walk reached and stopped at."""
    seen: dict[str, Path] = {}
    stopped: set[str] = set()
    queue: list[str] = list(TABLE_BUILD_ENTRY_MODULES)
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
    return {path for path in seen.values() if path.name != "__init__.py"}, stopped


def test_the_table_build_roster_is_the_import_closure_of_the_crate_driver_and_the_dump_writer():
    """The modules `run_build.TABLE_BUILD_CODE_MODULES` names are exactly the ones the crate driver and the spec dump's writer reach, outside the stop-set, and every module the walk stops at imports only other members of it."""
    reached, stopped = _table_build_reach()
    assert (
        PIPELINE / "kernel_io.py" in reached
    ), "the walk found nothing; the crate driver or the dump writer moved"
    named = {REPO_ROOT / relative for relative in run_build.TABLE_BUILD_CODE_MODULES}
    missing = sorted(path.relative_to(REPO_ROOT).as_posix() for path in reached - named)
    assert missing == [], (
        f"the crate driver or the spec dump's writer reaches modules TABLE_BUILD_CODE_MODULES does not name: {', '.join(missing)}. "
        "An edit to one could change what the crate reads while the structure stamp and `code` hold, so two builds of "
        "different inputs would pair as the same. Add each to TABLE_BUILD_CODE_MODULES in engine/pipeline/run_build.py, or move the code out of reach."
    )
    strays = sorted(
        relative for relative in run_build.TABLE_BUILD_CODE_MODULES if REPO_ROOT / relative not in reached
    )
    assert strays == [], (
        f"named in TABLE_BUILD_CODE_MODULES but unreachable from {', '.join(TABLE_BUILD_ENTRY_MODULES)}, so an edit to "
        f"one moves the structure stamp and `code` although the crate cannot see it: {', '.join(strays)}"
    )
    queue = sorted(stopped)
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
            f"the walk from {', '.join(TABLE_BUILD_ENTRY_MODULES)} stops at {module}, which imports {', '.join(outside)} "
            "outside WIDTH_AND_TELEMETRY_MODULES, so neither the structure stamp nor `code` hashes that code although the table build runs it."
        )
        queue.extend(onward)


def test_the_table_build_code_is_the_whole_crate_and_the_roster_on_disk():
    """`run_build.table_build_code_paths` is the crate's manifest, lock and every Rust source, plus the modules the roster names, and every one of them is on disk, because `fingerprint.hash_paths` skips a missing file and a renamed module would drop out of the structure stamp and the record's `code` without a failure."""
    kernel = REPO_ROOT / "engine" / "kernel-rs"
    paths = run_build.table_build_code_paths(REPO_ROOT)
    assert set(paths) == {
        kernel / "Cargo.toml",
        kernel / "Cargo.lock",
        *(kernel / "src").rglob("*.rs"),
        *(REPO_ROOT / relative for relative in run_build.TABLE_BUILD_CODE_MODULES),
    }
    missing = sorted(path.relative_to(REPO_ROOT).as_posix() for path in paths if not path.is_file())
    assert missing == [], f"the table build's code names files that are not there: {', '.join(missing)}"
