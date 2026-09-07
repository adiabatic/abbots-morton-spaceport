"""Shared fixtures for the rebuild suite, and the contract that decides how wide it may run: no test here reads a live build artifact.

The suite is one lane, **contracts**: every test reads only checked-in inputs and what it builds itself, so nothing reaches a live artifact and the suite runs at full xdist width. What the build can prove about its own artifacts it proves in the build — `check_unit` and `check_shards` in `rebuild/review/build.py` for the surface, `run_m1.run_rule_witnesses` for the tables' rule certificates — so no test has to read `rebuild/out/` to assert anything. `--lane contracts` names the lane (the default `all` collects the same tests, which is what a bare `uv run pytest rebuild/` does), and `pytest_xdist_auto_num_workers` here answers `-n auto` for it, deferring to the root conftest — and to `PYTEST_XDIST_AUTO_NUM_WORKERS` ahead of it — in every other case.

The contract needs a check that it holds, so a `sys.addaudithook` guard makes it structural rather than aspirational. It is installed once per process, sits inactive, and is switched on only for the setup, call, and teardown of an item this conftest governs; while active, any read or write whose path falls under the live-artifact trees (`rebuild/out/`, the whole of `tmp/` and `var/`, the gate's own exempt prefixes, the root `verdicts-*` stores) raises `ContractsLaneViolation` naming the test and the path, and a phase that swallows that exception still fails through `pytest_runtest_makereport`. What the guard does not cover is documented at the hook: subprocess children run unaudited, and `Path.exists()`/stat never reach it — it is the content reads that are caught, which is the leak that matters.

The same hook, in the same window, is the recorder behind the lane's per-test input closure (`rebuild.tools.contracts_closure` is the reader and the authority on what a closure means and when it may keep a test off a run). Every repo file a contracts item opens goes into that item's sink — a font `uharfbuzz` maps included, since `_wrap_blob_reads` announces that C-level read as the `open` event the hook handles — every module it imports for the first time into its module list, a file a module opens while its own body is being imported is credited to that module (`_attribute_import_read`) so every test whose closure holds the module inherits the read, and every child an item spawns marks it unclosable — a multiprocessing worker raises no audit event, so `BaseProcess.start` is wrapped to say so — with the two exceptions `contracts_closure.hermetic_child` and `contracts_closure.kernel_child` argue, the second flagging the item so the crate's sources join its closure. Reads a fixture makes during its own setup are credited to the fixture and folded into every item that requests it, since a session fixture sets up once and the hook sees that once under one item. `--closure-record PATH` has the controller write the sink of every worker to a sidecar at session end, and `--closure-skip PATH` deselects the contracts items a selection file names; both are the gate's to pass, and a bare `uv run pytest rebuild/` neither records nor skips.

`_redirect_cycle_writes` is the standing guarantee that running the suite never costs the working repo a file; it is autouse, so every module in rebuild/ gets it whether or not its author thought about the cycle.

Everything that is a claim about the review surface's code rather than about today's corpus reads the frozen mini bundle under `rebuild/review/fixtures/mini/` — the enrich and drafts worked examples through `example_units` below, the ink comparisons, the table-diff witnesses, the manual-pins teeth. What that buys is not only width: a dissolved exemplar fails the bundle regeneration, which names the window, rather than a suite a rune edit later.
"""

import multiprocessing.process
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rebuild.review.fixtures.mini import pin
from rebuild.tools import artifact_cycle, contracts_closure, cycle_timings, memory_budget

REAL_RUN_RETENTION = artifact_cycle.run_retention
REAL_READINESS_BLOCK = artifact_cycle.readiness_block
LIVE_DELETION_TARGETS = (
    *artifact_cycle.M1_SUMMARY_FILES.values(),
    artifact_cycle.CONFORM_SUMMARY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GREEN_RECORDS = (
    "PLUMBING_GREEN",
    "CONFORM_GREEN",
    "REBUILD_CONTRACTS_GREEN",
    "RUN_M1_GREEN",
    "MAKE_TEST_GREEN",
)

REBUILD_DIR = Path(__file__).resolve().parent
MINI = REBUILD_DIR / "review" / "fixtures" / "mini"
LANES = ("contracts",)
# The live trees, derived rather than listed: rebuild/out/ (everything the build and the cycle write), the whole of tmp/ and var/, the root-level verdicts-* stores, and whatever the rebuild gate exempts from its input closure. That last list is derived rather than copied so it tracks the gate, but it needs a subtraction, because its entries are exempt for two different reasons: rebuild/evidence/ and the census pins are regenerated state the gate refuses to hash, while rebuild/review/jstests/ and rebuild/m1-contact-allow.yaml are checked-in source that the gate merely has no reason to hash — the JS suite the cycle's own plan step globs, and the human-reviewed allow-list whose only reader is the defect gate — and source is what a contracts test is free to read. var/ is forbidden whole rather than by the cycle snapshots that sit in it, and tmp/ with it: both trees are entirely outside the suite's input closure, and the write standard below already bars every test from writing under the live repo, so nothing a contracts test may legitimately read can be in either. A test that wants a scratch directory takes `tmp_path`.
_EXEMPT_SOURCE = ("rebuild/review/jstests/", "rebuild/m1-contact-allow.yaml")
_FORBIDDEN = tuple(
    os.path.join(str(REPO_ROOT), rel)
    for rel in (
        "rebuild/out/",
        "tmp/",
        "var/",
        "verdicts-",
        *(rel for rel in artifact_cycle.REBUILD_GATE_EXEMPT_PREFIXES if rel not in _EXEMPT_SOURCE),
    )
)
_FORBIDDEN_TREES = frozenset(prefix.rstrip(os.sep) for prefix in _FORBIDDEN if prefix.endswith(os.sep))
_ROOT_PREFIX = str(REPO_ROOT) + os.sep
# The events whose first argument is a path the process reads: a content read for the closure. os.scandir and os.listdir are audited for violations but not recorded, because a listing changes only when an input is added or removed, and that diff runs the whole lane.
_READ_EVENTS = frozenset(("open", "shutil.copyfile", "shutil.copytree", "shutil.move"))
# Every way this interpreter starts a child that the hook can see. subprocess.Popen carries the argv, which is what lets a hermetic git command through; the os.* events carry no argv worth reading and always mark the item.
_SPAWN_EVENTS = frozenset(
    ("subprocess.Popen", "os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "os.spawn", "os.system")
)
_AUDITED_EVENTS = frozenset(
    (
        "open",
        "os.scandir",
        "os.listdir",
        "os.remove",
        "os.unlink",
        "os.rename",
        "os.replace",
        "os.mkdir",
        "os.rmdir",
        "shutil.rmtree",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.move",
    )
)


def _normalized(candidate: object) -> str | None:
    """An audited argument as an absolute, normalized path, or None for everything that is not a path. Audit events hand over whatever the caller passed — an int file descriptor, a None, a socket — and everything that is not a path is simply not a path, not an error. Normalization is `os.fsdecode` plus `os.path.normpath` against the cwd for relative names; deliberately no `realpath`, since resolving symlinks would cost a stat on every open in the worker to catch a case this repo does not have."""
    if isinstance(candidate, (str, bytes, os.PathLike)):
        try:
            path = os.fsdecode(candidate)
        except TypeError, ValueError, UnicodeDecodeError:
            return None
    else:
        return None
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.normpath(path)


def is_live_artifact_path(candidate: object) -> bool:
    """Whether an audited argument names something under the live trees."""
    path = _normalized(candidate)
    return path is not None and (path.startswith(_FORBIDDEN) or path in _FORBIDDEN_TREES)


def repo_relative_read(candidate: object) -> str | None:
    """The repo-relative source a read names, for the closure, or None when the read is outside the repo or inside a tree no closure should hold: the interpreter's own packages under `.venv/`, the caches, the crate's build output, and bytecode, which `contracts_closure.source_of` maps back to the module it was compiled from."""
    path = _normalized(candidate)
    if path is None or not path.startswith(_ROOT_PREFIX):
        return None
    rel = contracts_closure.source_of(path[len(_ROOT_PREFIX) :].replace(os.sep, "/"))
    return rel if contracts_closure.recordable(rel) else None


class ContractsLaneViolation(RuntimeError):
    """Raised out of the audit hook, inside whatever call tried the read."""


@dataclass
class _Sink:
    """What one item, or one fixture's setup, was seen to depend on: the repo files it read, the names of the modules it loaded for the first time in this process, whether it spawned the M1 kernel, and whether it started a child the hook cannot follow."""

    reads: set[str] = field(default_factory=set)
    module_names: set[str] = field(default_factory=set)
    kernel: bool = False
    unclosable: bool = False


class _Guard:
    def __init__(self) -> None:
        self.active = False
        self.nodeid = ""
        self.violations: list[tuple[str, str]] = []
        self.item = _Sink()
        self.fixtures: list[_Sink] = []

    def begin(self, nodeid: str) -> None:
        self.nodeid = nodeid
        self.violations.clear()
        self.item = _Sink()
        self.fixtures.clear()
        self.active = True

    def sinks(self) -> Iterable[_Sink]:
        yield self.item
        yield from self.fixtures

    def read(self, rel: str) -> None:
        for sink in self.sinks():
            sink.reads.add(rel)

    def module(self, name: str) -> None:
        for sink in self.sinks():
            sink.module_names.add(name)

    def spawn(self) -> None:
        for sink in self.sinks():
            sink.unclosable = True

    def kernel(self) -> None:
        for sink in self.sinks():
            sink.kernel = True


_guard = _Guard()
_guard_installed = False
# Per process: what each fixture's own setup read, by fixture name — every name a fixture can be requested under is unioned rather than told apart by definition site, which can only widen a closure — the sink of every finished contracts item, and every contracts id this process collected before any selection file deselected it.
_fixture_sinks: dict[str, _Sink] = {}
_item_closures: dict[str, dict] = {}
_collected_contracts: list[str] = []
_module_files: dict[str, str | None] = {}
# Import-time reads, by the repo module whose body was executing: a module that opens a file as it is imported does so once per process, under whichever item or collection happened to import it first, so the read is attributed to the module and folded into every test whose closure holds that module. `_pending_imports` is the names the import event has announced whose load may still be running; a read checks each against `sys.modules` and drops the ones that have finished.
_pending_imports: set[str] = set()
_import_reads: dict[str, set[str]] = {}
# What the workers hand back at session end, gathered on the controller as each node shuts down.
_worker_closures: list[dict] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    """The hook itself, called on every audited event in the process — so the inactive path is one attribute load and a return, and the active path does no work until the event is one of the handful that can carry a live path, a read, an import, or a spawn. Two gaps are deliberate and worth knowing: a subprocess child runs with its own hooks, so nothing a test spawns is covered — which is why a spawn makes the item unclosable rather than being followed — and `Path.exists()` / `os.stat` raise no audit event, so a contracts test may still ask whether a live artifact is there. It is the content that is guarded, which is the leak that would turn a test of the code into a test of today's artifacts."""
    if event == "import":
        if args and isinstance(args[0], str):
            _pending_imports.add(args[0])
            if _guard.active:
                _guard.module(args[0])
        return
    if not _guard.active:
        if _pending_imports and event in _READ_EVENTS and args:
            rel = repo_relative_read(args[0])
            if rel is not None:
                _attribute_import_read(rel)
        return
    if event in _AUDITED_EVENTS:
        for arg in args:
            if is_live_artifact_path(arg):
                path = os.fsdecode(arg)  # pyright: ignore[reportArgumentType]
                _guard.violations.append((event, path))
                raise ContractsLaneViolation(
                    f"{_guard.nodeid} is a rebuild-suite test but reached a live build artifact: {event} on {path}. "
                    f"A claim about live build output belongs in the build itself (a run_m1 stage or a surface check), "
                    f"and a test that only needed *a* directory should build one under `tmp_path`."
                )
        if event in _READ_EVENTS and args:
            rel = repo_relative_read(args[0])
            if rel is not None:
                _guard.read(rel)
                if _pending_imports:
                    _attribute_import_read(rel)
    elif event in _SPAWN_EVENTS:
        argv = args[1] if event == "subprocess.Popen" and len(args) > 1 else None
        if contracts_closure.hermetic_child(argv):
            return
        if contracts_closure.kernel_child(argv):
            _guard.kernel()
        else:
            _guard.spawn()


def _module_file(name: str) -> str | None:
    """The repo-relative file behind a module name the hook saw imported, looked up in `sys.modules` once the import has finished; None for a module outside the repo or one that never finished loading, and only a loaded module's answer is cached."""
    if name in _module_files:
        return _module_files[name]
    module = sys.modules.get(name)
    if module is None:
        return None
    origin = getattr(module, "__file__", None)
    _module_files[name] = repo_relative_read(origin) if origin else None
    return _module_files[name]


def _attribute_import_read(rel: str) -> None:
    """Credit a read to every repo module whose body is executing right now — importlib flags a module's spec `_initializing` from the moment it enters `sys.modules` until its body returns — and forget the pending names whose load has finished."""
    for name in list(_pending_imports):
        module = sys.modules.get(name)
        if module is None:
            continue
        if getattr(getattr(module, "__spec__", None), "_initializing", False):
            file = _module_file(name)
            if file is not None:
                _import_reads.setdefault(file, set()).add(rel)
        else:
            _pending_imports.discard(name)


def _finish_item(item: pytest.Item) -> None:
    """Fold the item's own sink and the sinks of every fixture it requested into one closure entry. The fixture union goes by name over `item.fixturenames`, the transitive closure pytest resolved, so a test that names no fixture of its own still inherits what a fixture-of-a-fixture read when it was set up under an earlier item."""
    sinks = [
        _guard.item,
        *(
            sink
            for name in getattr(item, "fixturenames", ())
            if (sink := _fixture_sinks.get(name)) is not None
        ),
    ]
    reads: set[str] = set()
    modules: set[str] = set()
    for sink in sinks:
        reads.update(sink.reads)
        modules.update(path for name in sink.module_names if (path := _module_file(name)) is not None)
    _item_closures[item.nodeid] = {
        "reads": sorted(reads),
        "modules": sorted(modules),
        "kernel": any(sink.kernel for sink in sinks),
        "unclosable": any(sink.unclosable for sink in sinks),
    }


def _wrap_process_start() -> None:
    """A multiprocessing worker starts through `_posixsubprocess.fork_exec` under the spawn method and raises no audit event on the way, so the hook alone would let a pooled build pass as closable. Wrapping `BaseProcess.start` — the one method every start method, `Pool` and `ProcessPoolExecutor` go through — marks the item the way a `subprocess.Popen` event does."""
    original = multiprocessing.process.BaseProcess.start

    def start(self, *args, **kwargs):
        if _guard.active:
            _guard.spawn()
        return original(self, *args, **kwargs)

    multiprocessing.process.BaseProcess.start = start


def _wrap_blob_reads() -> None:
    """`uharfbuzz.Blob.from_file_path` maps the file in C and raises no audit event, so the one read a shaping test makes of its font would be invisible to the guard and the recorder alike. The wrap announces the path as the `open` event the hook already handles: a font under a live tree fails the test, and a font under the repo lands in its closure."""
    import uharfbuzz as hb

    original = hb.Blob.from_file_path

    def from_file_path(cls, path):
        sys.audit("open", path, "rb", 0)
        return original(path)

    hb.Blob.from_file_path = classmethod(from_file_path)  # pyright: ignore[reportAttributeAccessIssue]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--lane",
        action="store",
        default="all",
        choices=[*LANES, "all"],
        help="Name the rebuild suite's one lane, contracts: no live build artifacts, every core this process may run on. The default, all, collects the same tests.",
    )
    parser.addoption(
        "--closure-record",
        action="store",
        default=None,
        metavar="PATH",
        help="Write every item's recorded input closure to this sidecar at session end (rebuild.tools.contracts_closure reads it into the lane's green record).",
    )
    parser.addoption(
        "--closure-skip",
        action="store",
        default=None,
        metavar="PATH",
        help="Deselect the items this selection file names as proven unaffected by the diff since the lane's last green run.",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _guard_installed
    if _guard_installed:
        return
    _guard_installed = True
    sys.addaudithook(_audit)
    _wrap_process_start()
    _wrap_blob_reads()


def governs(path: Path) -> bool:
    """Which collected files this conftest gets to classify. Everything under rebuild/, plus anything collected outside the repo entirely — that second arm is how the pytester subprocesses in test_lanes.py, which load this module with `-p rebuild.conftest` and collect from their own temp directory, see the same selection the real suite does. What it deliberately leaves alone is the rest of the repo's own suite: a combined `pytest rebuild/ test/` under `--lane contracts` must not deselect the font tests, which have no lane and never requested one."""
    if REBUILD_DIR == path.parent or REBUILD_DIR in path.parents:
        return True
    return REPO_ROOT != path.parent and REPO_ROOT not in path.parents


def _governed(item: pytest.Item) -> bool:
    path = getattr(item, "path", None)
    return path is not None and governs(Path(path))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """One deselection, the selection file, after the governed ids are noted, because the sidecar has to name every test the suite holds — an id the selection kept off keeps its previous closure in the merge only by being listed as collected. `--lane` deselects nothing: the one lane there is holds every governed item."""
    skip_path = config.getoption("closure_skip", default=None)
    skip = contracts_closure.read_selection(Path(skip_path)) if skip_path else frozenset()
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        governed = _governed(item)
        if governed:
            _collected_contracts.append(item.nodeid)
        if governed and item.nodeid in skip:
            dropped.append(item)
        else:
            kept.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """What `-n auto` resolves to for the suite's lane: every core this process may actually run on — no test here reaches a live artifact, so nothing holds a working set worth bounding, and `usable_cores` rather than `os.cpu_count` is what makes that true inside a container with a CPU quota as well as on a laptop.

    A run that names no lane answers None here and falls through to the root conftest on purpose: a bare `uv run pytest rebuild/`, a single rebuild test file and a mixed `pytest rebuild/ test/` all arrive as lane `all`, and the root fallback answers them at the same cores — the one answer there is now that no rebuild worker holds a live artifact — which also keeps `pytest .`, which reaches these tests without ever loading this file, answered the same as `pytest rebuild/`.

    `PYTEST_XDIST_AUTO_NUM_WORKERS` still comes first, by returning None so the root conftest reads it: that stays the one way to widen any pool here a run at a time.
    """
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    if config.getoption("lane", default=None) == "contracts":
        return memory_budget.usable_cores()
    return None


def pytest_report_header(config: pytest.Config) -> str:
    lane = config.getoption("lane", default="all")
    return f"rebuild lane: {lane}"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item):
    """Setup is inside the guarded window, not before it, because a module-scoped fixture that reads a live artifact is instantiated here — which is exactly the shape the guard exists to catch, a whole module of tests riding one unannounced read."""
    if _governed(item):
        _guard.begin(item.nodeid)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """A fixture's own setup records into a sink of its own beside the item's, so what a session, module or class fixture reads the once it is set up can be credited to every later item that requests it. Only a real setup comes through here — a cached fixture value never re-enters the hook — which is exactly the once the closure needs. A function-scoped fixture sets up inside every requesting item's own window and needs no sink of its own; giving it one would only pool its reads across items, which for a parametrized fixture is every parameter's file in every test."""
    if not _guard.active or fixturedef.scope == "function":
        return (yield)
    sink = _fixture_sinks.setdefault(fixturedef.argname, _Sink())
    _guard.fixtures.append(sink)
    try:
        return (yield)
    finally:
        _guard.fixtures.remove(sink)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    try:
        return (yield)
    finally:
        if _guard.active and _governed(item):
            _finish_item(item)
        _guard.active = False


def pytest_sessionfinish(session: pytest.Session) -> None:
    """A worker hands its closures back through workeroutput; the controller — or the one process of an un-pooled run — writes the sidecar `--closure-record` named, after every node has reported. Nothing is written without the option, so a bare run and the pytester children of test_lanes leave no file behind."""
    config = session.config
    local = {
        "collected": list(_collected_contracts),
        "tests": dict(_item_closures),
        "module_reads": {module: sorted(reads) for module, reads in _import_reads.items()},
    }
    if hasattr(config, "workerinput"):
        config.workeroutput["contracts_closures"] = local  # pyright: ignore[reportAttributeAccessIssue]
        return
    record = config.getoption("closure_record", default=None)
    if not record:
        return
    collected: list[str] = []
    tests: dict[str, dict] = {}
    module_reads: dict[str, set[str]] = {}
    for payload in (*_worker_closures, local):
        collected.extend(payload["collected"])
        tests.update(payload["tests"])
        for module, reads in payload.get("module_reads", {}).items():
            module_reads.setdefault(module, set()).update(reads)
    contracts_closure.write_sidecar(Path(record), collected, tests, module_reads)


def pytest_testnodedown(node, error) -> None:
    payload = getattr(node, "workeroutput", None) or {}
    closures = payload.get("contracts_closures")
    if isinstance(closures, dict):
        _worker_closures.append(closures)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """The backstop for a phase that catches the violation and carries on — a `try: ... except OSError` around the read, or a helper that treats any failure as "absent". The exception alone would be swallowed there; the recorded violation is not, and turns the phase's report red with the same message. Consuming the list per phase keeps a setup violation from also reddening the call report it already prevented."""
    report = yield
    pending = _guard.violations[:]
    _guard.violations.clear()
    if pending and report.passed and _governed(item):
        report.outcome = "failed"
        report.longrepr = "\n".join(
            [
                f"{item.nodeid} is a rebuild-suite test but reached a live build artifact:",
                *(f"  {event} on {path}" for event, path in pending),
                "A claim about live build output belongs in the build itself, never in this suite.",
            ]
        )
    return report


@pytest.fixture(autouse=True)
def _redirect_cycle_writes(monkeypatch, tmp_path):
    """The standard: nothing the suite runs may write to or delete from the live repo. Every cycle stage resolves its paths at call time, so a test that forgets to redirect one still passes while the repo quietly loses a file — which makes the default, not the individual test, the only thing that can be relied on. It is autouse and lives here rather than beside the tests that drive the cycle because a guard that covers one module is no guard at all: a new test module under rebuild/ inherits nothing, and the first one written after the fact will reach straight past it. Everything below is a default, and a test wanting the real behavior overrides it, since a per-test monkeypatch lands after this one and wins.

    The writes are the green records and the cycle summary, each a module constant this can point under tmp_path. Left live, a test driving _run_cycle over mocked stages leaves a record in rebuild/out that the next real cycle reads as proof that content it never tested had passed. The build-log root joins them on the same argument: `main` mints a run directory under it before anything else happens, so a test that drives main at all would leave a directory — and a `latest` symlink pointing at it — in the live var/build-logs, which the next reader would take for the newest real pass.

    The timings journal is redirected on the same argument and reaches further than the cycle does, because most of what writes to it is not the cycle at all. A pooled surface build files its per-worker peaks there, so any test that runs `build_m1` at more than one job appends to the live journal — and what it would append is a mini-bundle worker's footprint filed as an observation of the constant that prices a real one, which `make job-costs` would then read as headroom nobody has. Every judged check files a verdict there too, so a test driving either gate wrapper or run_m1's CLI would otherwise put a stubbed suite's outcome into the history `make cycle-timings --by-outcome` reports as this box's. `record_pool` and `record_check` both resolve this constant when the call is made rather than binding it as a default, so redirecting it here reaches every writer. The lane's own pool record is unaffected: the controller files it from `pytest_terminal_summary`, long after any fixture has been torn down.

    The cycle's run id comes off the environment on the same standard, because two of this suite's subjects read AMS_CYCLE_RUN to decide whether to record anything at all and the variable arrives from both directions. From outside: the rebuild lanes are themselves cycle children, so a real pass's run id is in the environment every one of these tests inherits, and a test asserting that an interactive gate files its verdict would fail for a reason that has nothing to do with the code. From inside: `artifact_cycle.main` sets the variable on this process, so a test that drives a cycle leaves it set for whatever that xdist worker picks up next, silencing every check-recording test after it in a way that depends on how the pool happened to steal the work. The delete is preceded by a set because monkeypatch registers no undo for a name that was already absent, and it is that set which puts the restore on the stack. A test that wants to be a cycle's child sets the variable itself, and that per-test monkeypatch lands after this one and wins.

    The deletes are the three stages that clear stale artifacts before rebuilding them: run_m1's four gate summaries and the summary gate:conform writes, each unlinked just before its subprocess spawns so the verdict can only come from this cycle, and the retention pass. Redirecting a constant is enough for the first two; retention takes one and resolves every other target from ROOT at call time, so it is stubbed out instead — with the empty line list a green finish now folds into its summary. Any test reaching a green finish with record_greens set would otherwise sweep the repo: every var/review-pre-* snapshot, the root's verdicts-carried-*.json exports, the autosave stashes, and a compaction of the verdict journal. That is destructive against a cycle running in another terminal — it deleted a live pass's only snapshot out from under its carry, stranding the pass's verdicts — and doubly so now that the rebuild gate is meant to run beside a live review server. A test that wants the real retention takes the `real_run_retention` fixture and points ROOT somewhere disposable; a test asserting that _finish reaches retention patches run_retention itself. The readiness checklist a green finish closes on is stubbed to nothing on the same standard, since the real one reads the served surface and the root autosave, which no contracts-lane test may; a test asserting that _finish prints it patches readiness_block itself.
    """
    monkeypatch.setattr(artifact_cycle, "CYCLE_SUMMARY", tmp_path / "cycle_summary.json")
    for name in GREEN_RECORDS:
        monkeypatch.setattr(artifact_cycle, name, tmp_path / f"{name.lower().replace('_', '-')}.json")
    monkeypatch.setattr(
        artifact_cycle,
        "M1_SUMMARY_FILES",
        {name: tmp_path / path.name for name, path in artifact_cycle.M1_SUMMARY_FILES.items()},
    )
    monkeypatch.setattr(artifact_cycle, "CONFORM_SUMMARY", tmp_path / artifact_cycle.CONFORM_SUMMARY.name)
    monkeypatch.setattr(artifact_cycle, "BUILD_LOGS_ROOT", tmp_path / "build-logs")
    monkeypatch.setattr(artifact_cycle, "run_retention", lambda plan: [])
    monkeypatch.setattr(artifact_cycle, "readiness_block", lambda plan: [])
    monkeypatch.setattr(cycle_timings, "JOURNAL", tmp_path / "cycle-timings.ndjson")
    monkeypatch.setenv(cycle_timings.CYCLE_RUN_ENV, "")
    monkeypatch.delenv(cycle_timings.CYCLE_RUN_ENV)


@pytest.fixture
def real_run_retention():
    """The unstubbed retention pass, for the three tests that are about retention itself. Captured at import, before the autouse stub can land."""
    return REAL_RUN_RETENTION


@pytest.fixture
def real_readiness_block():
    """The unstubbed readiness checklist, for the tests that are about the checklist itself. Captured at import, before the autouse stub can land."""
    return REAL_READINESS_BLOCK


@pytest.fixture
def live_deletion_targets():
    """The paths the autouse fixture redirects the pre-spawn unlinks away from, as they stand in a real cycle. The tripwire on that redirect compares against these."""
    return list(LIVE_DELETION_TARGETS)


@dataclass(frozen=True)
class MiniBundle:
    """The spec root materialized from the frozen mini-M1 bundle's pin, and that spec root's ledger."""

    spec_root: Path
    ledger: Path


@pytest.fixture(scope="session")
def mini_bundle(tmp_path_factory) -> MiniBundle:
    """The mini bundle and the spec its rows settled under, the latter materialized out of git — from the tree and blob shas `rebuild/review/fixtures/mini/pin.json` records — once per session per worker, tens of milliseconds, into pytest's temp root. Hand `spec_root` to `build_m1` or `load_spec` and `ledger` to `load_workload` or `load_ledger`, and the settlement the enricher re-derives is the one the frozen rows were written under, whatever the working tree's runes say today.

    It reads `.git` through git subprocesses and writes only under pytest's temp root, never `rebuild/out` and never the repo's `tmp/` or `var/`, which is the standard every fixture here is held to. Those subprocesses are `git cat-file` and `git archive` by sha, which `contracts_closure.hermetic_child` lets through the closure recorder: the bytes they read are content-addressed and the pin file that names them is read in this process, so every test built on this fixture stays closable.
    """
    spec_root = pin.materialize(tmp_path_factory.mktemp("mini-spec"))
    return MiniBundle(spec_root=spec_root, ledger=spec_root / "rebuild" / "m1-divergences.yaml")


@pytest.fixture(scope="session")
def example_units(mini_bundle: MiniBundle):
    """The frozen worked-example windows, keyed by (codepoints, first config) — the whole of what the enrich and drafts examples ever wanted, since each of them names the codepoints it is about. `regenerate.EXAMPLE_WINDOWS` is the authority on the set, and the bundle refuses to regenerate while a member of it selects no audit row, so a dissolved exemplar is caught where the rows are frozen rather than here; the assertion below is the belt to that, catching a set the bundle holds rows for but this loader cannot reach.

    Deliberately a contracts fixture, and it must stay one: the mini audit's rows settled under the pinned spec `mini_bundle` materializes, so the enricher re-derives the settlement they were written under, and nothing here may reach the live audit.
    """
    from rebuild.review.audit import load_workload
    from rebuild.review.enrich import LETTERS
    from rebuild.review.fixtures.mini import regenerate

    workload = load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(LETTERS))
    units = {
        (unit.codepoints, unit.configs[0]): unit
        for unit in workload.units
        if unit.codepoints in regenerate.EXAMPLE_WINDOWS
    }
    reached = {codepoints for codepoints, _config in units}
    assert reached == set(regenerate.EXAMPLE_WINDOWS), sorted(regenerate.EXAMPLE_WINDOWS - reached)
    return units
