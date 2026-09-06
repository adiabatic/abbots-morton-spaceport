"""The contracts lane's per-test input closure: what each test can read, recorded while it runs, and the selection that keeps a test off a run whose diff its closure cannot reach. It is `make_test_exempt`'s argument at test grain — a gate re-proves only what an edit can have moved — and `rebuild/conftest.py` is the recorder that feeds it.

A test's closure is the union of four things. Its **reads**: every repo file the audit hook saw opened during the test's setup, call, and teardown — a font HarfBuzz mapped included, which the conftest announces to the hook itself — with a `.pyc` mapped back to the source it was compiled from and the reads a shared fixture made during its own setup credited to every test that requests the fixture (the setup runs once per scope, so the hook sees it under one test only). Its **static import closure**: the repo modules reachable from the test module through `import` statements at any nesting, absolute or relative, `if TYPE_CHECKING:` included, resolved against the repo root and the sibling `test/` and `tools/` directories that go on `sys.path`. The same closure of every module the test imported **dynamically** — the `import` audit event fires for an import statement's first load in a worker, and a source file the test opened is followed as a module too, which is how `importlib.import_module` shows up, since it raises no import event and loads the source through `open` — and of both conftests, which run for every test. The **import-time reads** of every module in that closure: a file a module opens while its body executes is opened once per process, under whichever test or collection imported it first, so the recorder credits it to the module and the closure folds it back in for every test that can reach the module. And the four global inputs in `GLOBAL_LABELS`, which every test depends on without opening: the conftests, pyproject.toml, and uv.lock, plus the `fonts` label, which the lane key hashes as one value.

Selection is sound by construction or it is nothing, so every doubt resolves to running the test. A test with no recorded closure runs, which covers a new or renamed test id. A test that spawned a child runs — the hook sees nothing a subprocess or a multiprocessing worker reads — except when the child is a `git` command that reads the object store or a ref and never the working tree (`hermetic_child`), since nothing in the diff can reach those bytes. A diff that adds or removes any input runs the whole lane rather than reasoning about which directory listings or existence checks might have noticed, because `Path.exists()` and `os.stat` raise no audit event. A diff that touches a global label runs the whole lane. What is left is a test whose recorded closure misses every changed file, and that test's outcome is a function of inputs whose bytes are the ones it already passed against.

The record lives in the lane's green record beside the key (`rebuild_gate` and the artifact cycle both write it through `record_payload`): `files` is the per-label digest map the selection diffs against, widened past the lane's roster by any path a test read outside it, and `closures` holds `static` (module file to its import closure), `module_reads` (module file to what its body reads when imported) and `tests` (test id to its reads, dynamic modules and the unclosable flag). A narrowed run merges its sidecar into the previous record: tests that ran replace their entries, tests the selection kept off keep theirs — their inputs did not move, so neither did what they read — and ids the run no longer collected are dropped.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

SIDECAR_FORMAT = "ams-contracts-closures/1"
SELECTION_FORMAT = "ams-contracts-selection/1"
CONFTEST_PATHS = ("conftest.py", "rebuild/conftest.py")
GLOBAL_LABELS = frozenset((*CONFTEST_PATHS, "pyproject.toml", "uv.lock", "fonts"))
SIBLING_ROOTS = ("test", "tools")
IGNORED_PREFIXES = (
    ".git/",
    ".venv/",
    ".uv-cache/",
    ".pytest_cache/",
    "node_modules/",
    "rebuild/kernel-rs/target/",
)
HERMETIC_GIT_SUBCOMMANDS = frozenset(("rev-parse", "cat-file", "archive"))


def hermetic_child(argv: object) -> bool:
    """Whether a spawned command can read nothing the working tree holds. The three `git` subcommands here answer from the object store and the refs — `cat-file` and `archive` by sha, `rev-parse` by ref or `HEAD:<path>` — which no edit to a tracked or untracked file can reach, so a test that spawns one (the mini bundle materializing its pinned spec, the surface build stamping its manifest with HEAD) stays closable. Anything else that forks is unclosable: `git status`, `git ls-files` and `git diff` read the index and the working tree, and a non-git child can read anything at all."""
    if not isinstance(argv, (list, tuple)) or len(argv) < 2:
        return False
    try:
        head = os.path.basename(os.fsdecode(argv[0]))  # pyright: ignore[reportArgumentType]
        subcommand = os.fsdecode(argv[1])  # pyright: ignore[reportArgumentType]
    except TypeError, ValueError:
        return False
    return head == "git" and subcommand in HERMETIC_GIT_SUBCOMMANDS


def source_of(rel: str) -> str:
    """The repo-relative source a read names: a bytecode file under `__pycache__` stands for the module it was compiled from, since a valid cache is what the import system opens instead of the `.py`."""
    parent, name = os.path.split(rel)
    if os.path.basename(parent) == "__pycache__" and name.endswith(".pyc"):
        return os.path.join(os.path.dirname(parent), name.split(".", 1)[0] + ".py").replace(os.sep, "/")
    return rel


def recordable(rel: str) -> bool:
    return not rel.startswith(IGNORED_PREFIXES) and "/__pycache__/" not in f"/{rel}"


def _module_files(name: str, importer: Path, root: Path, level: int) -> list[Path]:
    """Every file importing `name` from `importer` can execute: the module itself (a `.py` or a package's `__init__.py`) and each package `__init__.py` on the way down. An absolute name resolves against the repo root first and then the sibling roots the suite puts on `sys.path` — `test/`, for the pins module's `import test_shaping`, and `tools/`, whose modules import each other bare — and against the importer's own directory, which is what a bare sibling import means from inside `tools/`. A relative name walks up `level - 1` directories from the importer's package. Every hit is kept rather than the first, because a resolver that guesses which `sys.path` entry wins is a resolver that can guess wrong, and an extra file in a closure only ever re-runs a test."""
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
    """The repo files one module's `import` statements can load, at any nesting. `from X import y` is resolved both as X and as X.y, so a submodule import is followed; a file that does not parse contributes nothing beyond itself, which is the conservative reading of a module that cannot run."""
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
    """Memoized reachability over `direct_imports`, for one tree: the parent computes a closure per test module, per conftest and per dynamically imported module, and the modules they share are parsed once."""

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


def closure_of(closures: dict, nodeid: str) -> frozenset[str] | None:
    """One test's whole closure out of a record, or None when the record cannot vouch for it: the test is unclosable, it has no entry, or a static closure it needs is missing. Every None is a test that runs."""
    entry = closures.get("tests", {}).get(nodeid)
    static = closures.get("static", {})
    module_reads = closures.get("module_reads", {})
    if not isinstance(entry, dict) or entry.get("unclosable"):
        return None
    paths: set[str] = set(entry.get("reads", ()))
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
    """What a narrowed run keeps off: `skip` is the ids the record proves unaffected, `changed` the labels that moved, and `reason` why nothing could be skipped when `skip` is empty. `known` counts the ids the record holds a closure for, which is the denominator a note reports against."""

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
    """The tests a green record proves unaffected by the diff between its `files` and `current`. Empty, with the reason, whenever soundness cannot be argued from the record: no record or no closures in it, an input added or removed, or a global label moved."""
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
    skip = {
        nodeid
        for nodeid in closures.get("tests", {})
        if (closure := closure_of(closures, nodeid)) is not None and not (closure & changed)
    }
    return Selection(skip=frozenset(skip), changed=tuple(sorted(changed)), known=known)


def selection_path(record_path: Path) -> Path:
    return record_path.with_name("rebuild-contracts-selection.json")


def sidecar_path(record_path: Path) -> Path:
    return record_path.with_name("rebuild-contracts-closures.json")


def write_selection(path: Path, skip: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": SELECTION_FORMAT, "skip": sorted(set(skip))}) + "\n")


def read_selection(path: Path) -> frozenset[str]:
    """The ids a selection file keeps off a run; empty for an absent or malformed file, so a run nobody narrowed runs everything."""
    try:
        payload = json.loads(path.read_text())
    except OSError, ValueError:
        return frozenset()
    if not isinstance(payload, dict) or payload.get("format") != SELECTION_FORMAT:
        return frozenset()
    return frozenset(item for item in payload.get("skip", ()) if isinstance(item, str))


def read_sidecar(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("format") != SIDECAR_FORMAT:
        return None
    if not isinstance(payload.get("collected"), list) or not isinstance(payload.get("tests"), dict):
        return None
    if not isinstance(payload.get("module_reads", {}), dict):
        return None
    return payload


def write_sidecar(
    path: Path,
    collected: list[str],
    tests: dict[str, dict],
    module_reads: Mapping[str, Iterable[str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": SIDECAR_FORMAT,
        "collected": sorted(set(collected)),
        "tests": tests,
        "module_reads": {module: sorted(reads) for module, reads in (module_reads or {}).items()},
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def merge_closures(root: Path, previous: dict | None, sidecar: dict | None) -> dict | None:
    """The `closures` payload a green run records: the sidecar's entries for the tests that ran, the previous record's for the collected tests the selection kept off, a fresh static closure for every module any entry leans on, and the import-time reads of every module those closures reach, unioned across the previous record and this run — a module the run never re-imported keeps what it was seen to read, and one it did adds to that. None when the run left no sidecar, which records the green without closures and puts the next run back to the whole lane."""
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
    """Every path a recorded closure names that the lane's roster does not hash — the reads the selection must still be able to see move."""
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
    """The lane's per-label digests widened by the extras a previous record named, so the selection diffs every path a recorded closure can name and not only the roster."""
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
    """What a green contracts run writes beside its key. `before` is the widened digest map the selection was taken over and `after_roster` the lane's roster as it stands now; every label both hold must agree, or the run tested content that is no longer on disk and `moved` names it so the caller can decline to record. Paths the run read for the first time outside the roster are digested here, which is the earliest anyone knows about them."""
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
