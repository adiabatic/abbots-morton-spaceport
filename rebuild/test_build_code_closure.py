"""Checks that `fingerprint.COMPARISON_CODE_MODULES`, `fingerprint.FONT_COMPILE_TOOL_MODULES` and `run_m1.TABLE_BUILD_CODE_MODULES` match the import graph.

`COMPARISON_CODE_MODULES` lists, as repo-relative paths, the modules of `pipeline_code_paths` left out of a serialized window enumeration's stamp (`fingerprint.table_code_paths`, the code half of `tables_value`). Reusing the tables and M1.otf on disk, as `run_m1 --gates-only` and `--conform-only` do, is safe only if no module on that list can change a table or the font. The first check walks the import graph from every build-side module and requires it to reach nothing on the list. Like `rebuild/test_oracle_code_closure.py`, it works at module grain and follows `if TYPE_CHECKING:` imports, which errs toward including too much. The driver, `rebuild/pipeline/run_m1.py`, reaches the comparison side through rebuild/pipeline/run_m1_gates.py from `full_run` and its `python -m` entry, because a full run also runs the gates, so it is checked at function grain instead: no expression in `run_m1.run`, or in any module-level function it calls transitively, may name a comparison-side module or anything imported from one.

The reverse also holds: every entry on the list must be a module the driver reaches, so the list names only modules the gates run. The list is also checked against `oracle_cache.ORACLE_ROW_CODE_PATHS`, because a comparison-side module named there would drop the oracle row cache on every classifier edit for no benefit.

`FONT_COMPILE_TOOL_MODULES` lists the tools/ modules the M1 font compile runs. `compile_font` passes the mini font to tools/build_font.py, so those modules can change M1.otf's bytes, and every stamp keyed on `pipeline_code_paths` must hash them. The list must equal the import closure inside tools/: a missing module is a font edit no key notices, and an extra one makes an edit to code the compile never runs trigger a rebuild. The walk follows tools' bare imports (they are siblings on `sys.path`, not a package) at any nesting. It starts from every tools/ module the pipeline's Python imports, not only tools/build_font.py, so a pipeline module that starts importing another tools/ module directly fails this test. rebuild/test_verdict_update_closure.py does not follow imports into tools/ modules and relies on this check for them. rebuild/test_review_code_closure.py does not follow them either, but the corpus build reaches tools/ only through test/test_shaping.py, and the draft-harness line of `unit_cache.environment_stamp` hashes that file and `font_compile_tool_paths`, so a second check follows the bare imports of test_shaping and of the corpus build's Python (`unit_cache.corpus_code_paths`) through tools/ and test/, both on `sys.path` once the drafter imports test_shaping, and holds everything it reaches within that file and this list. The roster check does not cover the rebuild/tools modules the pipeline imports, which `pipeline_code_paths` does not hash (rebuild/test_verdict_update_closure.py's docstring names some).

`TABLE_BUILD_CODE_MODULES` is the Python half of `run_m1.table_build_code_paths`, the code that the memo stamp's structure (`run_m1.memo_structure_stamp`) and a table build record's `code` hash. The crate reads nothing but the spec dump and its command line, so the roster is the module-grain import closure of the crate driver and the dump's writer (`kernel_exec`, `kernel_io`), stopping at rebuild/test_review_code_closure.py's `WIDTH_AND_TELEMETRY_MODULES` as rebuild/test_oracle_code_closure.py does, with the same check that every module the walk stops at imports only other members. A module the walk reaches that the roster misses could change what the crate reads while the memo stamp holds, so a build would read memos settled under other inputs; a roster entry the walk never reaches drops every memo on an edit the crate cannot see. A second check holds the paths to the whole crate plus the roster, every one on disk, because `fingerprint.hash_paths` skips a missing file.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rebuild.pipeline import fingerprint, oracle_cache, run_m1
from rebuild.review import unit_cache
from rebuild.test_review_code_closure import WIDTH_AND_TELEMETRY_MODULES

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / "rebuild" / "pipeline"
BUILD_DRIVER = PIPELINE / "run_m1.py"
BUILD_ENTRY = "run"
TOOLS = REPO_ROOT / "tools"
FONT_COMPILE_ENTRY = TOOLS / "build_font.py"
TEST_DIR = REPO_ROOT / "test"
TEST_SHAPING = TEST_DIR / "test_shaping.py"
TABLE_BUILD_ENTRY_MODULES = ("rebuild.pipeline.kernel_exec", "rebuild.pipeline.kernel_io")


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
    """Return every repo module the file imports. `from X import y` is recorded as both X and X.y, so a submodule import is followed. A relative import, which rebuild/validation uses, is resolved against the file's package first."""
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


def _comparison_paths() -> set[Path]:
    return {REPO_ROOT / relative for relative in fingerprint.COMPARISON_CODE_MODULES}


def _build_side_paths() -> list[Path]:
    """Return the Python files in `table_code_paths` except the driver: the build-side modules checked at module grain."""
    return [
        path
        for path in fingerprint.table_code_paths(REPO_ROOT)
        if path.suffix == ".py" and path != BUILD_DRIVER
    ]


def _tools_imports(tree: ast.AST) -> set[str]:
    """Return every name the tree imports, at any nesting. tools/ modules are siblings on `sys.path`, not a package, so they import one another by bare name, sometimes inside a function to break an import cycle."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _siblings(path: Path, directories: tuple[Path, ...] = (TOOLS,)) -> set[Path]:
    """Return the modules the file imports by bare name from the given `sys.path` directories. A name resolves only against <directory>/<head>.py, so stdlib, third-party, and `rebuild.*` imports are dropped. A name found in more than one directory yields every match, whatever the `sys.path` order."""
    found: set[Path] = set()
    for name in _tools_imports(ast.parse(path.read_text(encoding="utf-8"))):
        for directory in directories:
            sibling = directory / f"{name.split('.')[0]}.py"
            if sibling.is_file():
                found.add(sibling)
    return found


def _tools_entry_points() -> set[Path]:
    """Return the tools/ modules the pipeline's Python imports, at any nesting and including `if TYPE_CHECKING:` blocks. rebuild/pipeline/compile_font.py puts tools/ on `sys.path`, so a bare name resolves against tools/<head>.py the same way a tools/ sibling's does."""
    entries: set[Path] = set()
    for path in fingerprint.pipeline_code_paths(REPO_ROOT):
        if path.suffix != ".py" or path.parent == TOOLS:
            continue
        entries |= _siblings(path)
    return entries


def _sibling_closure(entries: set[Path], directories: tuple[Path, ...] = (TOOLS,)) -> set[Path]:
    """Return the modules in the given `sys.path` directories reachable by bare import from the given modules, themselves included. `rebuild.*` imports are dropped: `pipeline_code_paths` hashes rebuild/pipeline and rebuild/validation whole but not rebuild/tools, so a `rebuild.tools` module that tools/build_font.py imports is in no `fingerprint` component."""
    seen: set[Path] = set()
    queue = sorted(entries)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        queue.extend(_siblings(path, directories))
    return seen


def test_the_font_compile_roster_is_the_import_closure_of_what_the_pipeline_names_inside_tools():
    """The tools/ modules that run when rebuild/pipeline/compile_font.py compiles M1.otf are exactly the ones `FONT_COMPILE_TOOL_MODULES` lists, and so exactly the ones hashed into `pipeline_code_paths` and `table_code_paths`. A module the walk reaches that the list misses is a font edit that changes no key, and a list entry the walk never reaches makes edits to code the compile never runs trigger a rebuild. The walk starts from every tools/ module the pipeline imports, so a pipeline module that imports one the list lacks also fails here."""
    assert FONT_COMPILE_ENTRY.is_file(), "tools/build_font.py moved; the walk has no entry point"
    entries = _tools_entry_points()
    assert (
        FONT_COMPILE_ENTRY in entries
    ), "no pipeline module imports tools/build_font.py; the walk has no entry point"
    reached = {path.name for path in _sibling_closure(entries)}
    assert reached == set(fingerprint.FONT_COMPILE_TOOL_MODULES), (
        "FONT_COMPILE_TOOL_MODULES is no longer the import closure inside tools/ of what the pipeline imports from "
        f"tools/ ({', '.join(sorted(path.name for path in entries))}): the walk reaches {', '.join(sorted(reached))}, "
        "and an edit under that closure runs in the M1 build, so the roster in rebuild/pipeline/fingerprint.py has "
        "to name every one of them and nothing else."
    )
    strays = sorted(name for name in fingerprint.FONT_COMPILE_TOOL_MODULES if not (TOOLS / name).is_file())
    assert (
        strays == []
    ), f"FONT_COMPILE_TOOL_MODULES names files that are not under tools/: {', '.join(strays)}"


def test_the_draft_harness_imports_only_what_the_unit_store_stamp_hashes():
    """The approval drafter (`drafts._import_test_shaping`) and rebuild/validation/pins.py put test/ on `sys.path` to import test/test_shaping.py, which puts tools/ there too, so the walk resolves bare imports against both directories. It starts from that file and from every tools/ or test/ module that a Python module in `unit_cache.corpus_code_paths` imports by bare name, because once test_shaping is loaded such an import resolves too, and rebuild/test_review_code_closure.py drops bare names. `unit_cache.environment_stamp`'s draft-harness line hashes that file and `fingerprint.font_compile_tool_paths`, not the rest of tools/ and no other test/ module, so everything the walk reaches must be one of those files; `artifact_cycle.REBUILD_GATE_HARNESS_PATHS` relies on the same containment."""
    corpus_code = [
        path for path in unit_cache.corpus_code_paths(REPO_ROOT) if path.suffix == ".py" and path.is_file()
    ]
    assert (
        corpus_code
    ), "unit_cache.corpus_code_paths names no Python file; the walk has no corpus entry point"
    entries = {TEST_SHAPING}.union(*(_siblings(path, (TOOLS, TEST_DIR)) for path in corpus_code))
    reached = _sibling_closure(entries, (TOOLS, TEST_DIR))
    assert reached - {
        TEST_SHAPING
    }, "test/test_shaping.py imports no tools/ or test/ module, so this check would be vacuous"
    hashed = set(fingerprint.draft_harness_paths(REPO_ROOT))
    outside = sorted(path.relative_to(REPO_ROOT).as_posix() for path in reached - hashed)
    assert outside == [], (
        f"test/test_shaping.py or the corpus build reaches modules the unit store's draft-harness line does not hash: {', '.join(outside)}. "
        "An edit to one of them could change a unit's products without dropping the store or restamping the corpus. Either the import "
        "has to go, or `fingerprint.draft_harness_paths` and artifact_cycle.REBUILD_GATE_HARNESS_PATHS have to name these "
        "modules too."
    )


def test_the_font_compile_roster_is_included_in_both_the_run_record_and_the_tables_stamp():
    """The compile is on the build side, so its tools/ closure belongs in `table_code_paths` as well as `pipeline_code_paths`. A font-compile module left out of `table_code_paths` would let a serialized enumeration pass as current after its sources changed."""
    paths = set(fingerprint.font_compile_tool_paths(REPO_ROOT))
    assert paths
    assert paths <= set(fingerprint.pipeline_code_paths(REPO_ROOT))
    assert paths <= set(fingerprint.table_code_paths(REPO_ROOT))


def test_every_roster_entry_is_a_pipeline_module_on_disk():
    pipeline_code = set(fingerprint.pipeline_code_paths(REPO_ROOT))
    missing = sorted(
        relative
        for relative in fingerprint.COMPARISON_CODE_MODULES
        if REPO_ROOT / relative not in pipeline_code
    )
    assert (
        missing == []
    ), f"COMPARISON_CODE_MODULES names files that are not in pipeline_code_paths on disk: {', '.join(missing)}"


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
    """Return every name the driver binds to a comparison-side module or to something imported from one, including names bound by function-local imports."""
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


def test_the_build_entry_never_names_the_comparison_side():
    tree = ast.parse(BUILD_DRIVER.read_text(encoding="utf-8"))
    bindings = _comparison_bindings(tree)
    assert (
        bindings
    ), f"{BUILD_DRIVER.name} binds no comparison-side name, so this check would be vacuous; the driver stopped importing the gates it runs"
    reached, mentioned = _build_entry_reach(tree)
    assert "build_tables" in reached, "the walk from `run` never reached build_tables; the build entry moved"
    assert (
        "_run_defect_gates" in reached
    ), "the walk from `run` never reached _run_defect_gates; the defect gate a gates-only rerun shares with the build has to stay inside this walk, or a helper both entry points call could reach the comparison side unchecked"
    offenders = sorted(name for name in mentioned if name.split(".")[0] in bindings)
    assert offenders == [], (
        f"the build entry `{BUILD_ENTRY}` reaches the comparison side through {', '.join(offenders)} (via "
        f"{', '.join(sorted(reached))}), so a comparison-side edit could move a table or the font while the "
        "enumeration's stamp stays put"
    )


def test_every_roster_entry_is_reached_from_the_driver():
    """Every entry on the list is a module the driver reaches, so the list names only modules the gates run. An extra entry causes no wrong result, but it excludes from the stamp a module that no gate runs."""
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
    """The modules `run_m1.TABLE_BUILD_CODE_MODULES` names are exactly the ones the crate driver and the spec dump's writer reach, outside the stop-set, and every module the walk stops at imports only other members of it."""
    reached, stopped = _table_build_reach()
    assert (
        PIPELINE / "kernel_io.py" in reached
    ), "the walk found nothing; the crate driver or the dump writer moved"
    named = {REPO_ROOT / relative for relative in run_m1.TABLE_BUILD_CODE_MODULES}
    missing = sorted(path.relative_to(REPO_ROOT).as_posix() for path in reached - named)
    assert missing == [], (
        f"the crate driver or the spec dump's writer reaches modules TABLE_BUILD_CODE_MODULES does not name: {', '.join(missing)}. "
        "An edit to one could change what the crate reads while the memo stamp holds, so a build would read memos settled "
        "under other inputs. Add each to TABLE_BUILD_CODE_MODULES in rebuild/pipeline/run_m1.py, or move the code out of reach."
    )
    strays = sorted(
        relative for relative in run_m1.TABLE_BUILD_CODE_MODULES if REPO_ROOT / relative not in reached
    )
    assert strays == [], (
        f"named in TABLE_BUILD_CODE_MODULES but unreachable from {', '.join(TABLE_BUILD_ENTRY_MODULES)}, so an edit to "
        f"one drops every memo although the crate cannot see it: {', '.join(strays)}"
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
            "outside WIDTH_AND_TELEMETRY_MODULES, so the memo stamp does not hash that code although the table build runs it."
        )
        queue.extend(onward)


def test_the_table_build_code_is_the_whole_crate_and_the_roster_on_disk():
    """`run_m1.table_build_code_paths` is the crate's manifest, lock and every Rust source, plus the modules the roster names, and every one of them is on disk, because `fingerprint.hash_paths` skips a missing file and a renamed module would drop out of the memo stamp and the record's `code` without a failure."""
    kernel = REPO_ROOT / "rebuild" / "kernel-rs"
    paths = run_m1.table_build_code_paths(REPO_ROOT)
    assert set(paths) == {
        kernel / "Cargo.toml",
        kernel / "Cargo.lock",
        *(kernel / "src").rglob("*.rs"),
        *(REPO_ROOT / relative for relative in run_m1.TABLE_BUILD_CODE_MODULES),
    }
    missing = sorted(path.relative_to(REPO_ROOT).as_posix() for path in paths if not path.is_file())
    assert missing == [], f"the table build's code names files that are not there: {', '.join(missing)}"
