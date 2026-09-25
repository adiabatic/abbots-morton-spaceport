"""Shared fixtures for the rebuild suite, and the guard that keeps every test in it off live build artifacts.

The suite is one lane, **contracts**: every test reads only checked-in inputs and what it builds itself, so the suite runs at full xdist width. The build checks its own artifacts (`check_unit` and `check_shards` in `rebuild/review/build.py` for the surface, `run_m1.run_rule_witnesses` for the tables' rule certificates), so no test needs to read `rebuild/out/`. `--lane contracts` names the lane, and the default, `all`, collects the same tests. `pytest_xdist_auto_num_workers` here resolves `-n auto` under `--lane contracts` and otherwise defers to the root conftest. `PYTEST_XDIST_AUTO_NUM_WORKERS` overrides both.

A `sys.addaudithook` guard enforces the lane. It is installed once per process and is active only during the setup, call, and teardown of an item this conftest governs. While it is active, any audited file operation on a path under the live trees (`rebuild/out/`, all of `tmp/` and `var/`, the gate's exempt prefixes that are not source, and the root `verdicts-*` stores) raises `ContractsLaneViolation`, naming the test and the path. A phase that catches that exception still fails through `pytest_runtest_makereport`. The guard does not see subprocess children or `Path.exists()` and `os.stat`; `_audit` describes both gaps.

The same hook records each contracts item's input closure. `rebuild.tools.contracts_closure` reads the record and decides when a closure lets a test be skipped. The recorder adds to an item's closure every repo file the item opens, including a font `uharfbuzz` maps (`_wrap_blob_reads` reports that read as an `open` event), and every module the item imports for the first time in this process. A child process makes the item unclosable, with two exceptions: the git commands `closure_record.hermetic_child` accepts, and the kernel or its cargo build (`closure_record.kernel_child`), which flags the item so the crate's sources are added to its closure. A multiprocessing worker raises no audit event, so `BaseProcess.start` is wrapped to report it. A file a module opens while its body is being imported is credited to that module (`_attribute_import_read`), so every test whose closure includes the module gets the read. What a fixture scoped wider than a function reads during its setup is credited to the fixture and added to every item that requests it, because the fixture sets up once, under a single item. `--closure-record PATH` makes the controller write every worker's closures to a sidecar at session end, and `--closure-skip PATH` deselects the contracts items a selection file names. The gate passes both options, and a bare `uv run pytest rebuild/` neither records nor skips.

The autouse fixture `_redirect_cycle_writes` points every cycle write under `tmp_path` for every test under rebuild/, so running the suite never changes a file in the working repo.

This file imports only leaf modules, and must keep doing so. `closure_of` adds this file's static import closure to every test's, so a module-scope import here of the cycle driver, or of anything that reaches rebuild/pipeline/ or rebuild/review/, would put those trees into every closure, and no pipeline edit could let a test be skipped. So the redirect patches `rebuild.tools.cycle_paths`, the leaf the driver reads its paths from at call time. The recorder's helpers come from `rebuild.tools.closure_record` and not from `contracts_closure`, which imports the driver. The fixtures below load review-tree modules through `announced_import`, which imports the module at fixture setup and adds it to the closure of every test that requests the fixture. `rebuild/test_contracts_closure.py` checks both conftests' direct imports.

Tests about the review surface's code read the frozen mini bundle under `rebuild/review/fixtures/mini/`, never today's corpus. Besides allowing full width, this means an example window that no longer shows the property it was chosen for fails the bundle's regeneration, which names the window, instead of failing a test after a later rune edit.
"""

import importlib
import multiprocessing.process
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

from rebuild.tools import closure_record, cycle_paths, cycle_timings, memory_budget, standing_client

LIVE_DELETION_TARGETS = (
    *cycle_paths.M1_SUMMARY_FILES.values(),
    cycle_paths.CONFORM_SUMMARY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GREEN_RECORDS = (
    "PLUMBING_GREEN",
    "CONFORM_GREEN",
    "REBUILD_CONTRACTS_GREEN",
    "RUN_M1_GREEN",
    "MAKE_TEST_GREEN",
    "PYRIGHT_GREEN",
)

REBUILD_DIR = Path(__file__).resolve().parent
MINI = REBUILD_DIR / "review" / "fixtures" / "mini"
LANES = ("contracts",)
# Collection skips the crate, whose target/ tree is nearly every entry the walk would otherwise visit, and the build output. Every visited entry costs each xdist worker a `collect_ignore` lookup on each conftest, and on CPython 3.13+ a module attribute miss formats its error through getcwd(), so without these entries the walk is most of a narrowed run's fixed startup cost.
collect_ignore = ["kernel-rs", "out"]
# The live trees: rebuild/out/, all of tmp/ and var/, the root verdicts-* stores, and the rebuild gate's exempt prefixes, minus `_EXEMPT_SOURCE`. The gate exempts rebuild/evidence/ and the census pins because they are regenerated state, but it exempts rebuild/review/jstests/ (the JS suite) and rebuild/m1-contact-allow.yaml (read only by the defect gate) only because it has no reason to hash them. Those two are checked-in source, which a contracts test may read. A test that needs a scratch directory takes `tmp_path`.
_EXEMPT_SOURCE = ("rebuild/review/jstests/", "rebuild/m1-contact-allow.yaml")
_FORBIDDEN = tuple(
    os.path.join(str(REPO_ROOT), rel)
    for rel in (
        "rebuild/out/",
        "tmp/",
        "var/",
        "verdicts-",
        *(rel for rel in cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES if rel not in _EXEMPT_SOURCE),
    )
)
_FORBIDDEN_TREES = frozenset(prefix.rstrip(os.sep) for prefix in _FORBIDDEN if prefix.endswith(os.sep))
_ROOT_PREFIX = str(REPO_ROOT) + os.sep
# Events whose first argument is a path the process reads, which the closure records. os.scandir and os.listdir are checked for violations but not recorded, because a listing changes only when an input is added or removed, and that diff runs the whole lane.
_READ_EVENTS = frozenset(("open", "shutil.copyfile", "shutil.copytree", "shutil.move"))
# Every way this interpreter starts a child that the hook can see. Only a subprocess.Popen argv is passed to `closure_record.hermetic_child` and `kernel_child`, so the os.* events always mark the item unclosable.
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
    """Return an audited argument as an absolute, normalized path, or None when it is not a path (audit events pass whatever the caller passed, such as a file descriptor or None). There is no `realpath`, because resolving symlinks would cost a stat on every open in the worker to catch a case this repo does not have."""
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
    """Return the repo-relative path a read names, for the closure, or None when the read is outside the repo or `closure_record.recordable` excludes it (a prefix in `closure_record.IGNORED_PREFIXES`, such as `.venv/` or the crate's `target/`). A bytecode file is mapped to the module it was compiled from (`closure_record.source_of`)."""
    path = _normalized(candidate)
    if path is None or not path.startswith(_ROOT_PREFIX):
        return None
    rel = closure_record.source_of(path[len(_ROOT_PREFIX) :].replace(os.sep, "/"))
    return rel if closure_record.recordable(rel) else None


class ContractsLaneViolation(RuntimeError):
    """Raised from the audit hook, inside the call that tried to reach a live path."""


@dataclass
class _Sink:
    """What one item, or one fixture's setup, depends on: the repo files it read, the modules it loaded for the first time in this process, whether it spawned the M1 kernel, and whether it started a child the hook cannot follow."""

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
# Per process: each non-function fixture's setup sink, keyed by fixture name (two fixtures with the same name share a sink, which can only widen a closure); the closure of every finished contracts item; every contracts id collected before the selection file deselected any; and the cached repo file of each imported module.
_fixture_sinks: dict[str, _Sink] = {}
_item_closures: dict[str, dict] = {}
_collected_contracts: list[str] = []
_module_files: dict[str, str | None] = {}
# Import-time reads, keyed by the repo module whose body was executing. A module opens such a file once per process, under whichever item or collection imported it first, so the read is credited to the module and added to every test whose closure includes it. `_pending_imports` holds the module names the import event reported whose load may still be running; each read drops the ones that have finished.
_pending_imports: set[str] = set()
_import_reads: dict[str, set[str]] = {}
# The closures each worker returns at session end, collected on the controller as each node shuts down.
_worker_closures: list[dict] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    """Check and record one audit event. It runs on every audited event in the process, so the inactive path stays cheap.

    The guard has two gaps. A subprocess child runs without this hook, so nothing a test spawns is checked, which is why a spawn makes the item unclosable. `Path.exists()` and `os.stat` raise no audit event, so a contracts test may still ask whether a live artifact exists. The guard catches content reads, which are what would make a test depend on today's artifacts.
    """
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
        if closure_record.hermetic_child(argv):
            return
        if closure_record.kernel_child(argv):
            _guard.kernel()
        else:
            _guard.spawn()


def _module_file(name: str) -> str | None:
    """Return the repo-relative file of an imported module, or None for a module outside the repo or not in `sys.modules`. Only a result for a module found in `sys.modules` is cached."""
    if name in _module_files:
        return _module_files[name]
    module = sys.modules.get(name)
    if module is None:
        return None
    origin = getattr(module, "__file__", None)
    _module_files[name] = repo_relative_read(origin) if origin else None
    return _module_files[name]


def _attribute_import_read(rel: str) -> None:
    """Credit a read to every repo module whose body is executing, and forget the pending names whose load has finished. importlib sets a module's `__spec__._initializing` from when it enters `sys.modules` until its body returns."""
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
    """Merge the item's sink and the sinks of every fixture it requested into one closure entry. `item.fixturenames` is the transitive set pytest resolved, so the entry includes what a fixture's own fixtures read when they were set up under an earlier item."""
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
    """Mark the item unclosable when it starts a multiprocessing worker. Under the spawn start method a worker starts through `_posixsubprocess.fork_exec` and raises no audit event, so the hook alone would treat a pooled build as closable. Every start method, `Pool`, and `ProcessPoolExecutor` go through `BaseProcess.start`."""
    original = multiprocessing.process.BaseProcess.start

    def start(self, *args, **kwargs):
        if _guard.active:
            _guard.spawn()
        return original(self, *args, **kwargs)

    multiprocessing.process.BaseProcess.start = start


def _wrap_blob_reads() -> None:
    """Report each `uharfbuzz.Blob.from_file_path` read to the hook as an `open` event. The function reads the file in C and raises no audit event, so without this a shaping test's font read would be invisible to the guard and the recorder. With it, a font under a live tree fails the test and a font under the repo is added to its closure."""
    import uharfbuzz as hb

    original = hb.Blob.from_file_path

    def from_file_path(cls, path):
        sys.audit("open", path, "rb", 0)
        return original(path)

    hb.Blob.from_file_path = classmethod(from_file_path)  # pyright: ignore[reportAttributeAccessIssue]


def announced_import(name: str) -> ModuleType:
    """Import a module at fixture setup and record it in the current closure as if an import statement had loaded it. `importlib.import_module` raises no `import` audit event, and a module already imported during collection raises none on a later import, so without this the fixture's dependency would be recorded only in the worker that loaded the module first. With it, the module and, through `merge_closures`, its static import closure are in the closure of every test that requests the fixture. This lets this file keep its own imports to leaf modules; the module docstring says why."""
    module = importlib.import_module(name)
    if _guard.active:
        _guard.module(name)
    return module


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
    """Return whether this conftest governs a collected file: everything under rebuild/, plus anything collected outside the repo. The second case lets the pytester subprocesses in test_lanes.py, which load this module with `-p rebuild.conftest` and collect from their own temp directory, see the same selection the real suite does. The rest of the repo's suite is excluded, so a combined `pytest rebuild/ test/` never guards or deselects the font tests."""
    if REBUILD_DIR == path.parent or REBUILD_DIR in path.parents:
        return True
    return REPO_ROOT != path.parent and REPO_ROOT not in path.parents


def _governed(item: pytest.Item) -> bool:
    path = getattr(item, "path", None)
    return path is not None and governs(Path(path))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the governed items the `--closure-skip` selection file names. Every governed id is recorded as collected first, because `merge_closures` keeps a skipped test's previous closure only when the sidecar lists the test as collected. `--lane` deselects nothing."""
    skip_path = config.getoption("closure_skip", default=None)
    skip = closure_record.read_selection(Path(skip_path)) if skip_path else frozenset()
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
    """Resolve `-n auto` under `--lane contracts` to `memory_budget.usable_cores()`, which unlike `os.cpu_count` counts a container's CPU quota. No test here reads a live artifact, so no worker holds a working set that would need a memory bound.

    Any other run returns None and falls through to the root conftest, which resolves it to the same cores. That covers a bare `uv run pytest rebuild/`, a single rebuild test file, and a mixed `pytest rebuild/ test/`, which all arrive as lane `all`. It also covers `pytest .`, which does not load this file until collection, after xdist has resolved `-n auto`.

    When `PYTEST_XDIST_AUTO_NUM_WORKERS` is set, this returns None so that the root conftest reads the variable, which overrides every default.
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
    """Activate the guard before setup, because a module- or session-scoped fixture that reads a live artifact is set up here, and a whole module of tests would depend on that one read."""
    if _governed(item):
        _guard.begin(item.nodeid)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """Record the setup of a fixture scoped wider than a function into its own sink as well as the item's, so what it reads during its one setup can be credited to every later item that requests it. A cached fixture value does not re-enter this hook. A function-scoped fixture sets up inside each requesting item's window and gets no sink, because one would pool its reads across items, which for a parametrized fixture means every parameter's file in every test."""
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
    """Return a worker's closures through workeroutput, or, on the controller or in an unpooled run, write the `--closure-record` sidecar after every node has reported. Nothing is written without the option, so a bare run and the pytester children of test_lanes leave no file behind."""
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
    closure_record.write_sidecar(Path(record), collected, tests, module_reads)


def pytest_testnodedown(node, error) -> None:
    payload = getattr(node, "workeroutput", None) or {}
    closures = payload.get("contracts_closures")
    if isinstance(closures, dict):
        _worker_closures.append(closures)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """Fail a phase that caught a `ContractsLaneViolation` and carried on, for example through a `try: ... except OSError` around the read or a helper that treats any failure as "absent". The recorded violation turns the phase's passing report into a failure. The list is cleared per phase, so a setup violation does not also fail the call report."""
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
    """Point the cycle's writes and deletes under `tmp_path`, so no test changes a file in the live repo.

    Every cycle stage resolves its paths at call time, so a test that forgets to redirect one still passes while the repo gains or loses a file. The fixture is autouse and lives here so that every module under rebuild/ gets it, including modules written later. Each patch is a default: a test that wants the real behavior patches over it, because the test's own monkeypatch is applied after this one.

    The green records in `GREEN_RECORDS` and the cycle summary are redirected because a test driving `_run_cycle` over mocked stages would otherwise leave a record in rebuild/out that the next real cycle reads as a pass. The build-log root is redirected because `main` creates a run directory and a `latest` symlink under it, which the next reader would take for the newest real pass.

    The timings journal (`cycle_timings.JOURNAL`) is redirected because most of its writers are not the cycle. A pooled surface build records its per-worker peaks there, so a test that runs `build_m1` at more than one job would record a mini-bundle worker's peak as a measurement for the real worker's `*_BYTES` constant, and `make job-costs` would compare the constant with that peak. Every judged check records a verdict there too, so a test driving either gate wrapper or run_m1's CLI would add a stubbed suite's outcome to what `make cycle-timings --by-outcome` reports. `record_pool` and `record_check` read the constant at call time, so this redirect reaches every writer. The lane's own pool record is unaffected, because the root conftest writes it on the controller from `pytest_terminal_summary`, after every fixture is torn down.

    `cycle_timings.CYCLE_RUN_ENV` (AMS_CYCLE_RUN) is removed from the environment, because run_m1's CLI and the make-test gate wrapper skip recording their check line when it is set. It can come from outside, because the rebuild suite runs as a cycle child and inherits a real pass's run id, or from inside, because `artifact_cycle.main` sets it on this process and it would stay set for whatever test the xdist worker runs next. `setenv` comes before `delenv` so that `delenv` cannot raise on an absent variable and so that teardown restores the variable's original state, removing any value the test set. A test that wants to be a cycle's child sets the variable itself.

    `standing_client.SOCKET` defaults to a path under `var/`, and every test that drives either standing tool would otherwise query a live daemon. It points at a socket under tmp_path that nothing binds, so the auto mode falls back silently. A daemon test binds its own socket under tmp_path and names it.

    Three steps delete stale artifacts before rebuilding them: the unlink of run_m1's three summaries and of gate:conform's summary, each just before its subprocess spawns, and the retention pass. Redirecting the constants covers the unlinks. Retention resolves most of its targets from `artifact_cycle.ROOT` at call time, so it is switched off instead (`cycle_paths.RETENTION_ENABLED`), and `_finish` then records a retention result with no lines. Otherwise a test reaching a green finish with `record_greens` set would delete the root's `verdicts-carried-*.json` exports and autosave stashes and compact the verdict journal, even under a cycle or review server running in another terminal. A test that wants the real retention calls `artifact_cycle.run_retention` with a plan whose `artifact_cycle.ROOT` points somewhere disposable, and a test asserting that `_finish` reaches retention sets the switch back on and patches `run_retention`. The readiness checklist is switched off the same way (`cycle_paths.READINESS_ENABLED`), because it reads the served surface and the root autosave; a test asserting that `_finish` prints it sets the switch and patches `readiness_block`.

    Every constant is patched on `cycle_paths` and not on the cycle driver, because this file must not import the driver (the module docstring says why). The driver reads each of these through `cycle_paths.<NAME>` at call time, so the patch reaches it.
    """
    monkeypatch.setattr(cycle_paths, "CYCLE_SUMMARY", tmp_path / "cycle_summary.json")
    for name in GREEN_RECORDS:
        monkeypatch.setattr(cycle_paths, name, tmp_path / f"{name.lower().replace('_', '-')}.json")
    monkeypatch.setattr(
        cycle_paths,
        "M1_SUMMARY_FILES",
        {name: tmp_path / path.name for name, path in cycle_paths.M1_SUMMARY_FILES.items()},
    )
    monkeypatch.setattr(cycle_paths, "CONFORM_SUMMARY", tmp_path / cycle_paths.CONFORM_SUMMARY.name)
    monkeypatch.setattr(cycle_paths, "BUILD_LOGS_ROOT", tmp_path / "build-logs")
    monkeypatch.setattr(cycle_paths, "RETENTION_ENABLED", False)
    monkeypatch.setattr(cycle_paths, "READINESS_ENABLED", False)
    monkeypatch.setattr(cycle_timings, "JOURNAL", tmp_path / "cycle-timings.ndjson")
    monkeypatch.setattr(standing_client, "SOCKET", tmp_path / "standing-daemon.sock")
    monkeypatch.setenv(cycle_timings.CYCLE_RUN_ENV, "")
    monkeypatch.delenv(cycle_timings.CYCLE_RUN_ENV)


@pytest.fixture
def live_deletion_targets():
    """Return the live paths of the summaries a cycle unlinks before spawning run_m1 and gate:conform, which the autouse fixture redirects."""
    return list(LIVE_DELETION_TARGETS)


@dataclass(frozen=True)
class MiniBundle:
    """The spec root materialized from the frozen mini bundle's pin, that spec root's divergence ledger, and the bundle's subset tables packed under the temp root, where the enricher would otherwise write the pack beside the checked-in tables."""

    spec_root: Path
    ledger: Path
    subset_pack: Path


@pytest.fixture(scope="session")
def mini_bundle(tmp_path_factory) -> MiniBundle:
    """Materialize the spec the mini bundle's rows settled under, once per session per worker, into pytest's temp root, and pack the bundle's subset tables beside it. The spec comes out of git from the tree and blob shas `rebuild/review/fixtures/mini/pin.json` records. Pass `spec_root` to `build_m1` or `load_spec`, `ledger` to `load_workload` or `load_ledger`, and `subset_pack` to `build_m1` and `Enricher`, and the settlement the enricher re-derives is the one the frozen rows were written under, whatever the working tree's runes say.

    The git subprocesses are `git cat-file` and `git archive` by sha, which `closure_record.hermetic_child` accepts: the bytes they read are content-addressed and the pin file that names them is read in this process, so every test built on this fixture stays closable.
    """
    pin = announced_import("rebuild.review.fixtures.mini.pin")
    subset_pack = announced_import("rebuild.review.subset_pack")
    audit = announced_import("rebuild.review.audit")
    spec_root = pin.materialize(tmp_path_factory.mktemp("mini-spec"))
    pack = subset_pack.ensure_pack(MINI, audit.ACCEPTANCE_CONFIGS, pack=spec_root / subset_pack.PACK_NAME)
    return MiniBundle(
        spec_root=spec_root, ledger=spec_root / "rebuild" / "m1-divergences.yaml", subset_pack=pack
    )


@pytest.fixture(scope="session")
def mini_surface(tmp_path_factory, mini_bundle: MiniBundle) -> Path:
    """Build the frozen mini bundle's surface once per session per worker under pytest's temp root, for every test that needs the bundle's unmodified surface. `build_m1` over these inputs is byte-stable, so one build serves all of them. A test that changes a surface copies this one first. The build runs at one job, so it writes no pool record."""
    build = announced_import("rebuild.review.build")

    out = tmp_path_factory.mktemp("mini-surface") / "surface"
    build.build_m1(
        out,
        audit_path=MINI / "audit.tsv",
        ledger_path=mini_bundle.ledger,
        subset_dir=MINI,
        after_font=MINI / "M1.otf",
        spec_root=mini_bundle.spec_root,
        subset_pack=mini_bundle.subset_pack,
        jobs=1,
    )
    return out


@pytest.fixture(scope="session")
def example_units(mini_bundle: MiniBundle):
    """Return the frozen worked-example units for the enrich and drafts tests, keyed by (codepoints, first config). `regenerate.EXAMPLE_WINDOWS` is the authority on the set. Regenerating the bundle fails when a member selects no audit row; the assertion below is a second check, for a window the bundle holds rows for but this loader cannot reach.

    The units come from the mini audit, whose rows settled under the pinned spec `mini_bundle` materializes, so the enricher re-derives the settlement they were written under. This fixture must never read the live audit.
    """
    audit = announced_import("rebuild.review.audit")
    enrich = announced_import("rebuild.review.enrich")
    regenerate = announced_import("rebuild.review.fixtures.mini.regenerate")

    workload = audit.load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(enrich.LETTERS))
    units = {
        (unit.codepoints, unit.configs[0]): unit
        for unit in workload.units()
        if unit.codepoints in regenerate.EXAMPLE_WINDOWS
    }
    reached = {codepoints for codepoints, _config in units}
    assert reached == set(regenerate.EXAMPLE_WINDOWS), sorted(regenerate.EXAMPLE_WINDOWS - reached)
    return units
