"""The contracts lane's per-test input closures, and the selection that skips a test whose closure the diff does not reach. `rebuild/conftest.py` records the closures while the tests run.

A test's closure is the union of these:

- Its reads: every repo file the audit hook saw opened during the test's setup, call, and teardown. This includes a font HarfBuzz maps, which the conftest reports to the hook. A `.pyc` is mapped back to its source. What a fixture scoped wider than a function reads during its setup is credited to every test that requests the fixture, because the setup runs once, under one test.
- The static import closure of the test module: the repo modules reachable through `import` statements at any nesting, absolute or relative, `if TYPE_CHECKING:` included, resolved against the repo root, the importer's directory, and the `test/` and `tools/` directories the suite puts on `sys.path`.
- The static import closure of every module the test imported dynamically, and of both conftests. The `import` audit event fires on a module's first load in a worker. `importlib.import_module` raises no such event but opens the source file, so every `.py` file a test read is also treated as an imported module.
- The import-time reads of every module in that closure. A module's body runs once per process, under whichever test imported it first, so the recorder credits what the body reads to the module, and every test that can reach the module gets those reads.
- The labels in `GLOBAL_LABELS`, which every test depends on without opening: the two conftests, pyproject.toml, uv.lock, and the `fonts` label, which the lane key hashes as one value.

Selection runs a test whenever skipping it cannot be shown safe. A test with no recorded closure runs, which covers a new or renamed test id. A test that spawned a child runs, because the hook sees nothing a subprocess or multiprocessing worker reads, with two exceptions. A `git` command that reads only the object store or a ref (`hermetic_child`) cannot see the diff. The M1 kernel and the `cargo build` that makes it (`kernel_child`) read only the crate's sources beyond the scratch files the parent wrote, so every tracked file under `KERNEL_PREFIX` is added to that test's closure. A diff that adds or removes any input runs the whole lane, because `Path.exists()` and `os.stat` raise no audit event and a test may depend on a directory listing or an existence check. A diff that touches a global label runs the whole lane. Any other test whose closure contains no changed file is skipped, because every input it reads is byte-identical to the run it passed.

The record is stored in the lane's green record beside the key; `rebuild_gate` and the artifact cycle both write it through `record_payload`. `files` is the per-label digest map the selection diffs against: the lane's roster plus any path a test read outside it. `closures` holds `static` (module file to its import closure), `module_reads` (module file to what its body reads when imported), and `tests` (test id to its reads, its dynamically imported modules, whether it spawned the kernel, and whether it is unclosable). A narrowed run merges its sidecar into the previous record: tests that ran replace their entries, tests the selection skipped keep theirs, and ids the run did not collect are dropped.

The recorder's helpers (read normalization, the two spawn checks, and the selection and sidecar files) are defined in `rebuild.tools.closure_record` and re-exported here. The conftests import that leaf module, not this one, because the selection functions here import the cycle driver, and through it rebuild/pipeline/ and rebuild/review/. `closure_of` adds the conftests' static import closures to every test's closure, so a conftest that imported this module would put a pipeline edit in every closure and no test could be skipped. `rebuild/test_contracts_closure.py` checks the conftests' import edges and that neither reaches a pipeline or review module.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.tools.closure_record import (
    HERMETIC_GIT_SUBCOMMANDS,
    IGNORED_PREFIXES,
    KERNEL_BINARY,
    KERNEL_MANIFEST,
    KERNEL_PREFIX,
    SELECTION_FORMAT,
    SIDECAR_FORMAT,
    hermetic_child,
    kernel_child,
    read_selection,
    read_sidecar,
    recordable,
    selection_path,
    sidecar_path,
    source_of,
    write_selection,
    write_sidecar,
)

CONFTEST_PATHS = ("conftest.py", "rebuild/conftest.py")
GLOBAL_LABELS = frozenset((*CONFTEST_PATHS, "pyproject.toml", "uv.lock", "fonts"))
SIBLING_ROOTS = ("test", "tools")


def kernel_files(files: dict[str, str]) -> frozenset[str]:
    """Return the crate's files among a record's labels, which a test that spawned the kernel depends on beyond its own reads. A crate file added since the record counts as an added input, so it runs the whole lane before this set is used."""
    return frozenset(label for label in files if label.startswith(KERNEL_PREFIX))


def _module_files(name: str, importer: Path, root: Path, level: int) -> list[Path]:
    """Return every file that importing `name` from `importer` can execute: the module itself (a `.py` or a package's `__init__.py`) and each package `__init__.py` above it. An absolute name is resolved against the repo root, the importer's own directory (a bare sibling import inside `tools/`), and the sibling roots the suite puts on `sys.path`: `test/`, for `rebuild/validation/pins.py`'s `import test_shaping`, and `tools/`, whose modules import each other by bare name. A relative name walks up `level - 1` directories from the importer's package. Every match is kept, not only the one `sys.path` order would pick, because an extra file in a closure can only make a test run."""
    parts = [part for part in name.split(".") if part]
    if level:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        bases = [base]
    else:
        bases = [root, importer.parent, *(root / sibling for sibling in SIBLING_ROOTS)]
    hits: list[Path] = []
    for base in bases:
        for depth in range(1, len(parts)):
            init = base.joinpath(*parts[:depth]) / "__init__.py"
            if init.is_file():
                hits.append(init)
        target = base.joinpath(*parts) if parts else base
        if parts and target.with_suffix(".py").is_file():
            hits.append(target.with_suffix(".py"))
        elif (target / "__init__.py").is_file():
            hits.append(target / "__init__.py")
    return hits


def direct_imports(root: Path, rel: str) -> frozenset[str]:
    """Return the repo files one module's `import` statements can load, at any nesting. `from X import y` is resolved as both X and X.y, so a submodule import is followed. A file that cannot be read or parsed has no edges, so its closure is the file itself."""
    path = root / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except OSError, SyntaxError, UnicodeDecodeError:
        return frozenset()
    found: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(_module_files(alias.name, path, root, 0))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.update(_module_files(module, path, root, node.level))
            for alias in node.names:
                joined = f"{module}.{alias.name}" if module else alias.name
                found.update(_module_files(joined, path, root, node.level))
    return frozenset(_relative(root, hit) for hit in found if _inside(root, hit))


def _inside(root: Path, path: Path) -> bool:
    return root == path.parent or root in path.parents


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


class ImportGraph:
    """Memoized reachability over `direct_imports` for one tree, so a module shared by many closures is parsed once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._edges: dict[str, frozenset[str]] = {}

    def edges(self, rel: str) -> frozenset[str]:
        cached = self._edges.get(rel)
        if cached is None:
            cached = self._edges[rel] = direct_imports(self.root, rel)
        return cached

    def closure(self, rel: str) -> frozenset[str]:
        """Every repo file reachable from `rel` through import statements, `rel` itself included."""
        seen: set[str] = set()
        queue = [rel]
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.edges(current))
        return frozenset(seen)


def test_file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def closure_of(closures: dict, nodeid: str, kernel: frozenset[str] = frozenset()) -> frozenset[str] | None:
    """Return one test's closure from a record, or None when the test must run: it is unclosable, it has no entry, or a static closure it needs is missing. `kernel` is the crate's files, added for a test that spawned the kernel."""
    entry = closures.get("tests", {}).get(nodeid)
    static = closures.get("static", {})
    module_reads = closures.get("module_reads", {})
    if not isinstance(entry, dict) or entry.get("unclosable"):
        return None
    paths: set[str] = set(entry.get("reads", ()))
    if entry.get("kernel"):
        paths.update(kernel)
    reachable: set[str] = set()
    for module in (test_file_of(nodeid), *CONFTEST_PATHS, *entry.get("modules", ())):
        closure = static.get(module)
        if not isinstance(closure, list):
            return None
        reachable.update(closure)
    paths.update(reachable)
    for module in reachable:
        paths.update(module_reads.get(module, ()))
    return frozenset(paths)


@dataclass(frozen=True)
class Selection:
    """What a narrowed run skips: `skip` is the test ids the diff cannot affect, `changed` is the labels that moved, and `reason` says why nothing was skipped when `skip` is empty. `known` is the number of test ids the record has entries for."""

    skip: frozenset[str] = frozenset()
    changed: tuple[str, ...] = ()
    known: int = 0
    reason: str = ""

    def describe(self) -> str:
        from rebuild.tools.artifact_cycle import capped_labels

        if self.reason:
            return f"every test runs ({self.reason})"
        running = self.known - len(self.skip)
        moved = capped_labels(list(self.changed)) if self.changed else "nothing"
        return f"{running} of {self.known} recorded tests run, plus any test the record has no closure for; {len(self.skip)} proven unaffected by the diff ({moved})"


def select(record: dict | None, current: dict[str, str]) -> Selection:
    """Return the tests the diff between a green record's `files` and `current` cannot affect. The selection is empty, with a reason, when there is no record or it has no closures, when an input was added or removed, or when a global label moved."""
    from rebuild.tools.artifact_cycle import capped_labels, moved_input_labels

    if (
        record is None
        or not isinstance(record.get("closures"), dict)
        or not isinstance(record.get("files"), dict)
    ):
        return Selection(reason="no per-test closures recorded yet")
    closures = record["closures"]
    known = len(closures.get("tests", {}))
    moved = moved_input_labels(record, current) or []
    stored = record["files"]
    structural = sorted(label for label in moved if label not in stored or label not in current)
    if structural:
        return Selection(
            changed=tuple(moved), known=known, reason=f"inputs added or removed: {capped_labels(structural)}"
        )
    global_moved = sorted(label for label in moved if label in GLOBAL_LABELS)
    if global_moved:
        return Selection(
            changed=tuple(moved), known=known, reason=f"a global input moved: {capped_labels(global_moved)}"
        )
    changed = frozenset(moved)
    kernel = kernel_files(stored)
    skip = {
        nodeid
        for nodeid in closures.get("tests", {})
        if (closure := closure_of(closures, nodeid, kernel)) is not None and not (closure & changed)
    }
    return Selection(skip=frozenset(skip), changed=tuple(sorted(changed)), known=known)


def merge_closures(root: Path, previous: dict | None, sidecar: dict | None) -> dict | None:
    """Return the `closures` payload a green run records. It holds the sidecar's entries for the tests that ran, the previous record's entries for the collected tests the selection skipped, a fresh static closure for every module any entry names, and the import-time reads of every module those closures reach. Import-time reads are the union of the previous record's and this run's, so a module this run did not import keeps its recorded reads. Returns None when the run left no sidecar; the green is then recorded without closures and the next run runs the whole lane."""
    if sidecar is None:
        return None
    stale = previous.get("tests", {}) if isinstance(previous, dict) else {}
    ran = sidecar["tests"]
    tests: dict[str, dict] = {}
    for nodeid in sidecar["collected"]:
        entry = ran.get(nodeid, stale.get(nodeid))
        if isinstance(entry, dict):
            reads = set(entry.get("reads", ()))
            tests[nodeid] = {
                "reads": sorted(reads),
                "modules": sorted(
                    set(entry.get("modules", ())) | {rel for rel in reads if rel.endswith(".py")}
                ),
                "kernel": bool(entry.get("kernel")),
                "unclosable": bool(entry.get("unclosable")),
            }
    graph = ImportGraph(root)
    modules: set[str] = set(CONFTEST_PATHS)
    for nodeid, entry in tests.items():
        modules.add(test_file_of(nodeid))
        modules.update(entry["modules"])
    static = {module: sorted(graph.closure(module)) for module in sorted(modules)}
    reachable = set().union(*static.values()) if static else set()
    module_reads: dict[str, set[str]] = {}
    stale_reads = previous.get("module_reads", {}) if isinstance(previous, dict) else {}
    for source in (stale_reads, sidecar.get("module_reads", {})):
        for module, reads in source.items():
            if module in reachable:
                module_reads.setdefault(module, set()).update(reads)
    return {
        "static": static,
        "module_reads": {module: sorted(reads) for module, reads in sorted(module_reads.items())},
        "tests": tests,
    }


def extra_paths(closures: dict | None, roster: dict[str, str]) -> list[str]:
    """Return every path a recorded closure names that the lane's roster does not hash, so the selection can see those paths change."""
    if not isinstance(closures, dict):
        return []
    named: set[str] = set()
    for entry in closures.get("tests", {}).values():
        named.update(entry.get("reads", ()))
    for reachable in closures.get("static", {}).values():
        named.update(reachable)
    for reads in closures.get("module_reads", {}).values():
        named.update(reads)
    return sorted(named - roster.keys())


def current_files(root: Path, roster: dict[str, str], record: dict | None) -> dict[str, str]:
    """Return the lane's per-label digests plus a digest of every extra path a previous record's closures name, so the selection diffs every path a closure can contain."""
    from rebuild.tools.artifact_cycle import _closure_digest

    closures = record.get("closures") if isinstance(record, dict) else None
    files = dict(roster)
    for rel in extra_paths(closures, roster):
        files[rel] = _closure_digest(root, rel)
    return files


@dataclass(frozen=True)
class RecordPayload:
    files: dict[str, str]
    closures: dict | None = None
    moved: tuple[str, ...] = field(default_factory=tuple)


def record_payload(
    root: Path, before: dict[str, str], after_roster: dict[str, str], previous: dict | None, sidecar: Path
) -> RecordPayload:
    """Return what a green contracts run writes beside its key. `before` is the digest map the selection was taken over, and `after_roster` is the lane's roster now. Every label in `before` must still have the same digest; `moved` names any that changed, because the run tested content no longer on disk and the caller should not record a green. Paths the run read for the first time outside the roster are digested here, since the sidecar is the first place they appear."""
    from rebuild.tools.artifact_cycle import _closure_digest

    closures = merge_closures(
        root, previous.get("closures") if isinstance(previous, dict) else None, read_sidecar(sidecar)
    )
    files = current_files(root, after_roster, {"closures": closures} if closures else None)
    for rel in before:
        if rel not in files:
            files[rel] = _closure_digest(root, rel)
    moved = tuple(sorted(rel for rel in before if files[rel] != before[rel]))
    return RecordPayload(files=files, closures=closures, moved=moved)
