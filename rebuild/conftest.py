"""Shared fixtures for the rebuild suite, and the two-lane split that decides how wide it may run.

The suite divides into two lanes, and lane membership is *derived*, never hand-listed: `live_artifacts` is the one fixture that names the build's live output, so a test that requests it — directly, or through any fixture that requests it — is a **validators** test, and everything else is a **contracts** test. Contracts tests read only checked-in inputs and what they build themselves, so nothing in that lane reaches a live artifact at all and the lane may run at full xdist width; the validators lane now holds only assertions about the live artifact that no build check makes, each materializing what it needs and nothing more, so it takes a width divided out of the box in hand by what one of its own workers costs rather than every core there is. `--lane contracts` / `--lane validators` selects one (the default `all` runs both, which is what a bare `uv run pytest rebuild/` still does), and `pytest_xdist_auto_num_workers` here answers `-n auto` for both lanes, deferring to the root conftest — and to `PYTEST_XDIST_AUTO_NUM_WORKERS` ahead of it — in every other case.

A derived rule needs a check that the derivation is honest, so a `sys.addaudithook` guard makes lane membership structural rather than aspirational. It is installed once per process, sits inactive, and is switched on only for the setup, call, and teardown of a contracts-lane item; while active, any read or write whose path falls under the live-artifact trees (`rebuild/out/`, the whole of `tmp/`, the gate's own exempt prefixes, the root `verdicts-*` stores) raises `ContractsLaneViolation` naming the test and the path, and a phase that swallows that exception still fails through `pytest_runtest_makereport`. What the guard does not cover is documented at the hook: subprocess children run unaudited, and `Path.exists()`/stat never reach it — it is the content reads that are caught, which is the leak that matters.

`_redirect_cycle_writes` is the standing guarantee that running the suite never costs the working repo a file; it is autouse, so every module in rebuild/ gets it whether or not its author thought about the cycle.

What the validators lane still holds is short enough to name: the per-configuration rule-witness arms in `rebuild/test_rule_witnesses.py`, whose subject is the live tables. Everything else that once sat here reads the frozen mini bundle under `rebuild/review/fixtures/mini/` — the enrich and drafts worked examples through `example_units` below, the ink comparisons, the table-diff witnesses, the manual-pins teeth — because none of them was ever a claim about today's corpus, only about the code that reads one. What that buys is not only width: a dissolved exemplar now fails the bundle regeneration, which names the window, rather than a lane a rune edit later.
"""

import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from rebuild.review.fixtures.mini import pin
from rebuild.tools import artifact_cycle, cycle_timings, memory_budget

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
    "REBUILD_VALIDATORS_GREEN",
    "RUN_M1_GREEN",
    "MAKE_TEST_GREEN",
)

REBUILD_DIR = Path(__file__).resolve().parent
MINI = REBUILD_DIR / "review" / "fixtures" / "mini"
LANES = ("contracts", "validators")
LIVE_FIXTURE = "live_artifacts"
# What one validators worker holds at its peak, and so the divisor a box divides itself by to reach this lane's width. Nothing in the lane loads a corpus any more — a worker holds one configuration's stamped tables and the verified-witness memo beside them, which is where the seven gigabytes went: the retired whole-workload fixtures were the whole of the difference, and the first run without them stamped 0.90 GB where the run before it stamped 6.00. Two instruments report the peak and agree on its scale: the `peak RSS (GB): ... workers ...` line the root conftest prints at the end of every run, and the `peak_rss_bytes` the cycle-timings journal stamps on every `gate:rebuild-validators` step, which is the widest single process in that lane's tree and so an upper bound on any one worker. This seed rounds up well past that reading, for the same reason `kernel_exec.CONFIG_PEAK_BYTES` rounds up past its own measurement: a per-unit cost that errs low is what puts a box into swap, while one that errs high only narrows a pool — and here it narrows nothing, since `VALIDATORS_LANE_CAP` decides on any box this division would not already floor. `make job-costs` will report the historical six-gigabyte rows as an overrun until its recency window has turned over, which is what a deliberate downward re-seed looks like from that report rather than a peak to chase. So it is a reading to keep current rather than a contract — re-seed it off a validators run's own line whenever a fixture's working set moves, and expect the root conftest's restatement of it to move in the same commit.
VALIDATORS_WORKER_BYTES = 1_500_000_000
# The non-memory bound on the same width, and the half of this lane's argument that arithmetic cannot supply: the lane is short enough that a worker's own interpreter and collection cost shows against its total, and no one arm is its tail — the per-configuration rule-witness arms run hint-first off a verified memo, so four slots drain a spread of comparable arms rather than a queue behind one. Deliberately not tied to the acceptance-configuration count those arms are parametrized over, which would read as tighter and behave as looser — a matrix that grew by one would silently widen a pool nobody re-measured, and the arms are not the whole of what the lane runs. So it stays a stated bound with a stated reason, and what should move it is the lane's own `--durations` list saying four slots no longer drain what it collects, never the box getting bigger; a bigger box is what the divisor above is for.
VALIDATORS_LANE_CAP = 4


def _validators_workers(*, total_bytes: int | None = None, cores: int | None = None) -> int:
    """How many validators workers this box has room for: `VALIDATORS_WORKER_BYTES` divided into it by `memory_budget.how_many_fit`, which takes off the reserve that policy states and floors at one, under a cap that is `VALIDATORS_LANE_CAP` and the cores this process may actually run on together. Both belong in the one `min()` because neither is a memory fact — `VALIDATORS_LANE_CAP` is the lane's own short-run bound, and `usable_cores()` is there so a two-core box or a CPU-quota-limited container starts two workers rather than a cap's worth of idle ones, since it reads the affinity mask and the cgroup quota that `os.cpu_count()` reads straight past. Both keywords are injection points rather than lookups, so an assertion about another box is a pure function over an invented one."""
    return memory_budget.how_many_fit(
        VALIDATORS_WORKER_BYTES,
        total_bytes=total_bytes,
        cap=min(VALIDATORS_LANE_CAP, memory_budget.usable_cores() if cores is None else cores),
    )


# Resolved once at import rather than asked again per run, exactly as `kernel_exec.KERNEL_THREADS_DEFAULT` is and for the analogous reason: the name a test imports, the name WHATNEXT cites and the number the hook answers are then one reading of this box rather than three, which is what lets `VALIDATORS_WORKERS` survive as a name now that it has stopped being a literal. The consequence is the one `kernel_exec` documents too — `AMS_TOTAL_MEMORY_BYTES` reaches it only from the environment the interpreter started in, never from a fixture.
VALIDATORS_WORKERS = _validators_workers()

# The live trees, derived rather than listed: rebuild/out/ (everything the build and the cycle write), the whole of tmp/, the root-level verdicts-* stores, and whatever the rebuild gate exempts from its input closure. That last list is derived rather than copied so it tracks the gate, but it needs a subtraction, because its entries are exempt for two different reasons: rebuild/evidence/ and the census pins are regenerated state the gate refuses to hash, while rebuild/review/jstests/ and rebuild/m1-contact-allow.yaml are checked-in source that the gate merely has no reason to hash — the JS suite the cycle's own plan step globs, and the human-reviewed allow-list whose only reader is the defect gate — and source is what a contracts test is free to read. tmp/ is forbidden whole rather than by the cycle snapshots that happen to sit in it: the tree is entirely outside the suite's input closure, and the write standard below already bars every test from writing under the live repo, so nothing a contracts test may legitimately read can be there. A test that wants a scratch directory takes `tmp_path`.
_EXEMPT_SOURCE = ("rebuild/review/jstests/", "rebuild/m1-contact-allow.yaml")
_FORBIDDEN = tuple(
    os.path.join(str(REPO_ROOT), rel)
    for rel in (
        "rebuild/out/",
        "tmp/",
        "verdicts-",
        *(rel for rel in artifact_cycle.REBUILD_GATE_EXEMPT_PREFIXES if rel not in _EXEMPT_SOURCE),
    )
)
_FORBIDDEN_TREES = frozenset(prefix.rstrip(os.sep) for prefix in _FORBIDDEN if prefix.endswith(os.sep))
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


@dataclass(frozen=True)
class LiveArtifacts:
    """Where the build leaves its output. Nothing here is asserted to exist — a fresh clone has none of it, and the tests that read these paths already carry their own skips or fail loudly, which is the behavior this preserves."""

    m1: Path
    font: Path
    audit: Path
    surface: Path


@pytest.fixture(scope="session")
def live_artifacts() -> LiveArtifacts:
    """The live build output, and the lane contract in one object: **requesting this fixture is what puts a test in the validators lane.** It holds no state and asserts nothing about existence; what it does is put its own name into the requesting test's fixture closure, where `lane_of` can see it — which is why a module-scoped wrapper that requests it (test_review_build's `built`, test_manual_pins' `gate_report`) carries its whole module's readers into the lane without any of them naming it.

    The consequence runs the other way too, and is enforced rather than trusted: a test that reads `rebuild/out/`, anything under `tmp/`, or a root verdict store *without* this fixture trips the contracts-lane audit guard and fails with `ContractsLaneViolation`. So the choice a new test faces is honest — either the live artifact is what it is asserting about, and it takes this fixture and pays the validators lane's narrow width, or the read was incidental and belongs against a synthetic root or `tmp_path` instead.
    """
    m1 = REPO_ROOT / "rebuild" / "out" / "m1"
    return LiveArtifacts(
        m1=m1,
        font=m1 / "M1.otf",
        audit=m1 / "divergence-audit.tsv",
        surface=artifact_cycle.REVIEW_OUT,
    )


def lane_for_fixturenames(names: Iterable[str]) -> str:
    """The whole classification rule, over a fixture closure's names. Split out from `lane_of` so it can be tested without a collected item behind it."""
    return "validators" if LIVE_FIXTURE in names else "contracts"


def lane_of(item: pytest.Item) -> str:
    """`item.fixturenames` is the *transitive* closure pytest resolved for the item, so a test that names no fixture of its own still reads as validators when some fixture it requests requests `live_artifacts`."""
    return lane_for_fixturenames(getattr(item, "fixturenames", ()))


def is_live_artifact_path(candidate: object) -> bool:
    """Whether an audited argument names something under the live trees. Takes `object` because audit events hand over whatever the caller passed — an int file descriptor, a None, a socket — and everything that is not a path is simply not a path, not an error. Normalization is `os.fsdecode` plus `os.path.normpath` against the cwd for relative names; deliberately no `realpath`, since resolving symlinks would cost a stat on every open in the worker to catch a case this repo does not have."""
    if isinstance(candidate, (str, bytes, os.PathLike)):
        try:
            path = os.fsdecode(candidate)
        except TypeError, ValueError, UnicodeDecodeError:
            return False
    else:
        return False
    if not path:
        return False
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    path = os.path.normpath(path)
    return path.startswith(_FORBIDDEN) or path in _FORBIDDEN_TREES


class ContractsLaneViolation(RuntimeError):
    """Raised out of the audit hook, inside whatever call tried the read."""


class _Guard:
    def __init__(self) -> None:
        self.active = False
        self.nodeid = ""
        self.violations: list[tuple[str, str]] = []


_guard = _Guard()
_guard_installed = False


def _audit(event: str, args: tuple[object, ...]) -> None:
    """The hook itself, called on every audited event in the process — so the inactive path is one attribute load and a return, and the active path does no work until the event is one of the handful that can carry a live path. Two gaps are deliberate and worth knowing: a subprocess child runs with its own hooks, so nothing a test spawns is covered, and `Path.exists()` / `os.stat` raise no audit event, so a contracts test may still ask whether a live artifact is there. It is the content that is guarded, which is the leak that turns a contracts test into a validators one."""
    if not _guard.active:
        return
    if event not in _AUDITED_EVENTS:
        return
    for arg in args:
        if is_live_artifact_path(arg):
            path = os.fsdecode(arg)  # pyright: ignore[reportArgumentType]
            _guard.violations.append((event, path))
            raise ContractsLaneViolation(
                f"{_guard.nodeid} is a contracts-lane test but reached a live build artifact: {event} on {path}. "
                f"A test whose assertion is about live build output belongs in the validators lane — request the "
                f"`live_artifacts` fixture. A test that only needed *a* directory should build one under `tmp_path`."
            )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--lane",
        action="store",
        default="all",
        choices=[*LANES, "all"],
        help="Run only one lane of the rebuild suite: contracts (no live build artifacts, every core this process may run on) or validators (reads rebuild/out, at the narrower width one of its own workers' measured cost leaves room for).",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _guard_installed
    if _guard_installed:
        return
    _guard_installed = True
    sys.addaudithook(_audit)


def governs(path: Path) -> bool:
    """Which collected files this conftest gets to classify. Everything under rebuild/, plus anything collected outside the repo entirely — that second arm is how the pytester subprocesses in test_lanes.py, which load this module with `-p rebuild.conftest` and collect from their own temp directory, see the same selection the real suite does. What it deliberately leaves alone is the rest of the repo's own suite: a combined `pytest rebuild/ test/` under `--lane contracts` must not deselect the font tests, which have no lane and never requested one."""
    if REBUILD_DIR == path.parent or REBUILD_DIR in path.parents:
        return True
    return REPO_ROOT != path.parent and REPO_ROOT not in path.parents


def _governed(item: pytest.Item) -> bool:
    path = getattr(item, "path", None)
    return path is not None and governs(Path(path))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    lane = config.getoption("lane", default="all")
    if lane == "all":
        return
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        target = kept if not _governed(item) or lane_of(item) == lane else dropped
        target.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """What `-n auto` resolves to for each lane of this suite, both answers derived from what a worker in that lane actually costs rather than inherited from a repo-wide floor. The 2 that floor stood at was sized for validators workers that peaked at 14–17 GB, and neither lane has looked like that for a long time.

    Contracts takes every core this process may actually run on — no test in it reaches a live artifact, so nothing there holds a working set worth bounding, and `usable_cores` rather than `os.cpu_count` is what makes that true inside a container with a CPU quota as well as on a laptop. Validators takes VALIDATORS_WORKERS, whose divisor and cap each carry their own argument where they are defined: on a roomy box the cap decides, because what stops this lane going wider is its shape rather than its footprint, and on a small one the division decides and the lane narrows to what that box has room for instead of to a width someone measured elsewhere. The box this lane's timings were recorded on resolves to the cap, exactly as it did when the width was a literal — what changed is that every other box now gets its own answer.

    A run that names no lane answers None here and falls through to the root conftest on purpose. `--lane all` is what a bare `uv run pytest rebuild/`, a single rebuild test file and a mixed `pytest rebuild/ test/` all are, and none of them is this lane: each holds both lanes at once, so its memory bound is a validators worker while its CPU bound is the contracts majority, which is exactly the pair of facts the root fallback divides and caps by. Answering them at this lane's cap instead would slow the ordinary whole-suite run several-fold in order to bound a footprint the arithmetic there already bounds — and it would leave `pytest .`, which reaches these tests without ever loading this file, answered differently from `pytest rebuild/`, which collects strictly less.

    `PYTEST_XDIST_AUTO_NUM_WORKERS` still comes first for all of them, by returning None so the root conftest reads it: that stays the one way to widen any pool here a run at a time.
    """
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    lane = config.getoption("lane", default=None)
    if lane == "contracts":
        return memory_budget.usable_cores()
    if lane == "validators":
        return VALIDATORS_WORKERS
    return None


def pytest_report_header(config: pytest.Config) -> str:
    lane = config.getoption("lane", default="all")
    return f"rebuild lane: {lane}"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item):
    """Setup is inside the guarded window, not before it, because a module-scoped fixture that reads a live artifact is instantiated here — which is exactly the shape the guard exists to catch, a whole module of tests riding one unannounced read."""
    if lane_of(item) == "contracts":
        _guard.nodeid = item.nodeid
        _guard.violations.clear()
        _guard.active = True
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    try:
        return (yield)
    finally:
        _guard.active = False


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """The backstop for a phase that catches the violation and carries on — a `try: ... except OSError` around the read, or a helper that treats any failure as "absent". The exception alone would be swallowed there; the recorded violation is not, and turns the phase's report red with the same message. Consuming the list per phase keeps a setup violation from also reddening the call report it already prevented."""
    report = yield
    pending = _guard.violations[:]
    _guard.violations.clear()
    if pending and report.passed and lane_of(item) == "contracts":
        report.outcome = "failed"
        report.longrepr = "\n".join(
            [
                f"{item.nodeid} is a contracts-lane test but reached a live build artifact:",
                *(f"  {event} on {path}" for event, path in pending),
                "A test asserting about live build output must request the `live_artifacts` fixture.",
            ]
        )
    return report


@pytest.fixture(autouse=True)
def _redirect_cycle_writes(monkeypatch, tmp_path):
    """The standard: nothing the suite runs may write to or delete from the live repo. Every cycle stage resolves its paths at call time, so a test that forgets to redirect one still passes while the repo quietly loses a file — which makes the default, not the individual test, the only thing that can be relied on. It is autouse and lives here rather than beside the tests that drive the cycle because a guard that covers one module is no guard at all: a new test module under rebuild/ inherits nothing, and the first one written after the fact will reach straight past it. Everything below is a default, and a test wanting the real behavior overrides it, since a per-test monkeypatch lands after this one and wins.

    The writes are the green records and the cycle summary, each a module constant this can point under tmp_path. Left live, a test driving _run_cycle over mocked stages leaves a record in rebuild/out that the next real cycle reads as proof that content it never tested had passed. The build-log root joins them on the same argument: `main` mints a run directory under it before anything else happens, so a test that drives main at all would leave a directory — and a `latest` symlink pointing at it — in the live tmp/build-logs, which the next reader would take for the newest real pass.

    The timings journal is redirected on the same argument and reaches further than the cycle does, because most of what writes to it is not the cycle at all. A pooled surface build files its per-worker peaks there, so any test that runs `build_m1` at more than one job appends to the live journal — and what it would append is a mini-bundle worker's footprint filed as an observation of the constant that prices a real one, which `make job-costs` would then read as headroom nobody has. Every judged check files a verdict there too, so a test driving either gate wrapper or run_m1's CLI would otherwise put a stubbed suite's outcome into the history `make cycle-timings --by-outcome` reports as this box's. `record_pool` and `record_check` both resolve this constant when the call is made rather than binding it as a default, so redirecting it here reaches every writer. The lane's own pool record is unaffected: the controller files it from `pytest_terminal_summary`, long after any fixture has been torn down.

    The cycle's run id comes off the environment on the same standard, because two of this suite's subjects read AMS_CYCLE_RUN to decide whether to record anything at all and the variable arrives from both directions. From outside: the rebuild lanes are themselves cycle children, so a real pass's run id is in the environment every one of these tests inherits, and a test asserting that an interactive gate files its verdict would fail for a reason that has nothing to do with the code. From inside: `artifact_cycle.main` sets the variable on this process, so a test that drives a cycle leaves it set for whatever that xdist worker picks up next, silencing every check-recording test after it in a way that depends on how the pool happened to steal the work. The delete is preceded by a set because monkeypatch registers no undo for a name that was already absent, and it is that set which puts the restore on the stack. A test that wants to be a cycle's child sets the variable itself, and that per-test monkeypatch lands after this one and wins.

    The deletes are the three stages that clear stale artifacts before rebuilding them: run_m1's four gate summaries and the summary gate:conform writes, each unlinked just before its subprocess spawns so the verdict can only come from this cycle, and the retention pass. Redirecting a constant is enough for the first two; retention takes one and resolves every other target from ROOT at call time, so it is stubbed out instead — with the empty line list a green finish now folds into its summary. Any test reaching a green finish with record_greens set would otherwise sweep the repo: every tmp/review-pre-* snapshot, the root's verdicts-carried-*.json exports, the autosave stashes, and a compaction of the verdict journal. That is destructive against a cycle running in another terminal — it deleted a live pass's only snapshot out from under its carry, stranding the pass's verdicts — and doubly so now that the rebuild gate is meant to run beside a live review server. A test that wants the real retention takes the `real_run_retention` fixture and points ROOT somewhere disposable; a test asserting that _finish reaches retention patches run_retention itself. The readiness checklist a green finish closes on is stubbed to nothing on the same standard, since the real one reads the served surface and the root autosave, which no contracts-lane test may; a test asserting that _finish prints it patches readiness_block itself.
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

    This is a contracts-lane fixture and must stay one: it reads `.git` through git subprocesses and writes only under pytest's temp root, never `rebuild/out` and never the repo's `tmp/`, and it must never request `live_artifacts`.
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
