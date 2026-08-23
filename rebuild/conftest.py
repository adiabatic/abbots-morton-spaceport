"""Shared fixtures for the rebuild suite, and the two-lane split that decides how wide it may run.

The suite divides into two lanes, and lane membership is *derived*, never hand-listed: `live_artifacts` is the one fixture that names the build's live output, so a test that requests it — directly, or through any fixture that requests it — is a **validators** test, and everything else is a **contracts** test. Contracts tests read only checked-in inputs and what they build themselves, so nothing in that lane reaches a live artifact at all and the lane may run at full xdist width; the validators lane now holds only assertions about the live artifact that no build check makes, each materializing what it needs and nothing more, so it takes a measured width of its own rather than the repo-wide default. `--lane contracts` / `--lane validators` selects one (the default `all` runs both, which is what a bare `uv run pytest rebuild/` still does), and `pytest_xdist_auto_num_workers` here answers `-n auto` for both lanes, deferring to the root conftest — and to `PYTEST_XDIST_AUTO_NUM_WORKERS` ahead of it — in every other case.

A derived rule needs a check that the derivation is honest, so a `sys.addaudithook` guard makes lane membership structural rather than aspirational. It is installed once per process, sits inactive, and is switched on only for the setup, call, and teardown of a contracts-lane item; while active, any read or write whose path falls under the live-artifact trees (`rebuild/out/`, the whole of `tmp/`, the gate's own exempt prefixes, the root `verdicts-*` stores) raises `ContractsLaneViolation` naming the test and the path, and a phase that swallows that exception still fails through `pytest_runtest_makereport`. What the guard does not cover is documented at the hook: subprocess children run unaudited, and `Path.exists()`/stat never reach it — it is the content reads that are caught, which is the leak that matters.

`_redirect_cycle_writes` is the standing guarantee that running the suite never costs the working repo a file; it is autouse, so every module in rebuild/ gets it whether or not its author thought about the cycle.

`built_review_surface` yields the cycle's own rebuild/out/review, read-only, and **refuses rather than builds**: when `surface_build_skippable` cannot prove that surface reflects today's inputs, the fixture fails naming the command that fixes it. That is the decision `stamped_decision` already makes one file over for the settlement tables, made here for the same reason — building a surface inside a pytest worker cost the better part of ten minutes and fifteen gigabytes, sprang on exactly the bare `make test-rebuild` an author reaches for after a rune edit, and re-derived what the artifact cycle had just built. In the cycle the surface step settles before the gates are submitted, so the refusal essentially never fires there.

The whole-workload fixtures are gone with it. `enriched_units` enriched all 451k units to check properties of drafts the build itself computes for every shipped unit, and `workload` held the un-enriched graph so that sixteen worked examples could look up windows whose codepoints they already name; between them they were the reason this lane ran two workers wide. What stands in their place is exactly what those readers turn out to need: `example_units` streams the live audit once into a filtered copy and loads a Workload of the named windows only, and `workload_index` keeps the census grain — codepoints, class, no-verdict flag, configs, in workload order — for the two tests whose assertion is "the sidecar's flag at position *i* describes workload unit *i*" and which therefore need the ordered list rather than the graph.
"""

import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from rebuild.review.fixtures.mini import pin
from rebuild.tools import artifact_cycle

REAL_RUN_RETENTION = artifact_cycle.run_retention
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
LANES = ("contracts", "validators")
VALIDATORS_WORKERS = 4
LIVE_FIXTURE = "live_artifacts"

# The live trees, derived rather than listed: rebuild/out/ (everything the build and the cycle write), the whole of tmp/, the root-level verdicts-* stores, and whatever the rebuild gate exempts from its input closure. That last list is derived rather than copied so it tracks the gate, but it needs one subtraction, because its three entries are exempt for two different reasons: rebuild/evidence/ and the census pins are regenerated state the gate refuses to hash, while rebuild/review/jstests/ is checked-in JavaScript that is merely outside a Python closure — source, which a contracts test is free to read, and which the cycle's own plan step globs while enumerating the JS suite. tmp/ is forbidden whole rather than by the cycle snapshots that happen to sit in it: the tree is entirely outside the suite's input closure, and the write standard below already bars every test from writing under the live repo, so nothing a contracts test may legitimately read can be there. A test that wants a scratch directory takes `tmp_path`.
_FORBIDDEN = tuple(
    os.path.join(str(REPO_ROOT), rel)
    for rel in (
        "rebuild/out/",
        "tmp/",
        "verdicts-",
        *(rel for rel in artifact_cycle.REBUILD_GATE_EXEMPT_PREFIXES if rel != "rebuild/review/jstests/"),
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
        help="Run only one lane of the rebuild suite: contracts (no live build artifacts, full xdist width) or validators (reads rebuild/out, stays at the checked-in worker count).",
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
    """What `-n auto` resolves to for each lane of this suite. Both answers are measured rather than inherited: the root conftest's repo-wide 2 was sized for validators workers that peaked at 14–17 GB, and neither lane looks like that any more.

    Contracts takes every core — no test in it reaches a live artifact, so nothing there holds a working set worth bounding. Validators takes VALIDATORS_WORKERS, and the `peak RSS (GB): ... workers ...` line the root conftest prints at the end of every run is both its justification and its standing check. With the in-process surface build refused and the whole-workload fixtures retired, most of what a worker holds is a filtered Workload, a couple of subset tables, or a font; the one item still measured in gigabytes is `workload_index`, whose transient peak while `load_workload` builds the graph it projects put a worker at ≈2.5 GB. Four of those fit on the most RAM-constrained box that runs this repo with room to spare, and going wider buys little: the lane is short enough that per-worker interpreter and collection cost starts to show, and its tail is the six per-config witness arms, which four slots already cover.

    `PYTEST_XDIST_AUTO_NUM_WORKERS` still comes first for both, by returning None so the root conftest reads it: that stays the one way to widen any pool here a run at a time.
    """
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    lane = config.getoption("lane", default=None)
    if lane == "contracts":
        return os.cpu_count() or 2
    if lane == "validators":
        return min(VALIDATORS_WORKERS, os.cpu_count() or VALIDATORS_WORKERS)
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

    The writes are the green records and the cycle summary, each a module constant this can point under tmp_path. Left live, a test driving _run_cycle over mocked stages leaves a record in rebuild/out that the next real cycle reads as proof that content it never tested had passed.

    The deletes are the three stages that clear stale artifacts before rebuilding them: run_m1's four gate summaries and the summary gate:conform writes, each unlinked just before its subprocess spawns so the verdict can only come from this cycle, and the retention pass. Redirecting a constant is enough for the first two; retention takes none — it resolves every target from ROOT at call time — so it is stubbed out instead. Any test reaching a green finish with record_greens set would otherwise sweep the repo: every tmp/review-pre-* snapshot, the root's verdicts-carried-*.json exports, the autosave stashes, and a compaction of the verdict journal. That is destructive against a cycle running in another terminal — it deleted a live pass's only snapshot out from under its carry, stranding the pass's verdicts — and doubly so now that the rebuild gate is meant to run beside a live review server. A test that wants the real retention takes the `real_run_retention` fixture and points ROOT somewhere disposable; a test asserting that _finish reaches retention patches run_retention itself.
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
    monkeypatch.setattr(artifact_cycle, "run_retention", lambda plan: None)


@pytest.fixture
def real_run_retention():
    """The unstubbed retention pass, for the three tests that are about retention itself. Captured at import, before the autouse stub can land."""
    return REAL_RUN_RETENTION


@pytest.fixture
def live_deletion_targets():
    """The paths the autouse fixture redirects the pre-spawn unlinks away from, as they stand in a real cycle. The tripwire on that redirect compares against these."""
    return list(LIVE_DELETION_TARGETS)


@pytest.fixture(scope="session")
def built_review_surface(live_artifacts: LiveArtifacts):
    """Yields (surface_dir, manifest) for the cycle's own rebuild/out/review — read-only, never built here. `surface_build_skippable` is the proof: the manifest's recorded inputs fingerprint equals the one a build would stamp now, which is the same standard the artifact cycle skips its own surface step on, and in a cycle that step settles before any gate is submitted, so this is the taken branch every time the gate runs.

    When it cannot be proven, the fixture fails naming the command that fixes it instead of building a surface of its own. A build inside a pytest worker is a fifteen-gigabyte, several-hundred-second job that re-derives what the cycle already produced, and worksteal will happily land it on a worker that is holding something else; the cost of refusing is that a bare `make test-rebuild` after a rune edit fails fast rather than grinding, which is the trade `stamped_decision` in test_rule_witnesses already makes for the stamped tables.
    """
    from rebuild.tools.artifact_cycle import REVIEW_OUT, surface_build_skippable

    if not surface_build_skippable(REPO_ROOT):
        pytest.fail(
            f"no review surface under {REVIEW_OUT} is stamped with the current inputs — a stale or missing "
            "surface fails this gate instead of building one in-process; run `make review-cycle` (or "
            "`uv run python -m rebuild.review.build`) first"
        )
    manifest = json.loads((REVIEW_OUT / "manifest.json").read_text(encoding="utf-8"))
    return REVIEW_OUT, manifest


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


# The windows the worked examples name. Every test that used to scan the whole workload for one unit hard-coded its codepoints already; the three that asked for "some unit of class X" are pinned here instead, so an emptied class fails loudly rather than silently sampling something else.
EXAMPLE_WINDOWS = frozenset(
    {
        "0020:E650:E650",
        "200C:E652:E679",
        "200C:E665:E679:E650",
        "E650:E650:E670",
        "E650:E650:200C:E67A",
        "E658:E666",
        "E650:200C:E650:E665",
        "E650:200C:E650:E670",
        "E650:E670:E65D",
        "E652:E670",
        "E652:E653:E67A:E652",
        "E665:E666:E666",
        "E665:E670:E652:E679",
        "E670:E670",
        "E670:E67A:E670:E665",
    }
)


def _filter_audit(audit_path: Path, windows: Iterable[str], destination: Path) -> Path:
    """Copy the audit's header plus the rows of the named windows into `destination`, streaming — so the filtered load below goes through `load_audit` itself rather than a second parser that could drift from it, without ever holding the 307 MB original."""
    wanted = frozenset(windows)
    with open(audit_path, encoding="utf-8") as source, open(destination, "w", encoding="utf-8") as sink:
        sink.write(next(source))
        for line in source:
            fields = line.split("\t", 2)
            if len(fields) > 1 and fields[1] in wanted:
                sink.write(line)
    return destination


@pytest.fixture(scope="session")
def audit_windows(tmp_path_factory, live_artifacts: LiveArtifacts):
    """A loader for `Workload`s over named windows of the live audit: `audit_windows({"E652:E670", ...})` streams the audit once per distinct window set and returns the units those rows build. The dedupe key is (codepoints, baseline, new), so a window's units come out exactly as they do over the whole audit — same configs, kinds, class, config_classes, render groups. What differs is what the filter cannot preserve: unit ids, batches, and triage `group` ordering are relative to the filtered list, so nothing may assert on them here."""
    from rebuild.review.audit import load_workload
    from rebuild.review.build import M1_LEDGER
    from rebuild.review.enrich import LETTERS

    root = tmp_path_factory.mktemp("audit-windows")
    cache: dict[frozenset[str], object] = {}

    def load(windows: Iterable[str]):
        key = frozenset(windows)
        if key not in cache:
            path = _filter_audit(live_artifacts.audit, key, root / f"{len(cache)}.tsv")
            cache[key] = load_workload(path, M1_LEDGER, dict(LETTERS))
        return cache[key]

    return load


@pytest.fixture(scope="session")
def example_units(audit_windows):
    """The worked-example windows of `EXAMPLE_WINDOWS`, keyed by (codepoints, first config) the way the retired `units_by_key` was. This is what replaced the whole-workload `workload` fixture for the sixteen tests that wanted one unit each: a streamed filter and a few dozen units instead of a 451k-unit graph and its two gigabytes."""
    workload = audit_windows(EXAMPLE_WINDOWS)
    return {(unit.codepoints, unit.configs[0]): unit for unit in workload.units}


@dataclass(frozen=True)
class IndexedUnit:
    """One pre-merge unit at the census grain — the four fields `census.workload_digest` is defined over, and the whole of what a flag-alignment test needs to say "position *i* of the sidecar describes this window"."""

    codepoints: str
    class_id: str
    no_verdict: bool
    configs: tuple[str, ...]

    @property
    def codepoint_values(self) -> tuple[int, ...]:
        from rebuild.review.audit import parse_codepoints

        return parse_codepoints(self.codepoints)


@dataclass(frozen=True)
class WorkloadIndex:
    units: tuple[IndexedUnit, ...]
    row_count: int
    sibling_positions: tuple[int, ...]


@pytest.fixture(scope="session")
def workload_index(live_artifacts: LiveArtifacts) -> WorkloadIndex:
    """The live pre-merge unit list at census grain, in workload order, plus the positions of the multi-sibling windows the ink sample stratifies over. Built through `load_workload` — the ordering is the loader's and cannot drift from it — and projected immediately, so the graph is transient rather than resident: a worker that touches this pays the audit parse once and keeps tens of megabytes, where the retired `workload` fixture kept the whole graph and its AuditRows for the whole session.

    Anything needing a real `Unit` — an enrichment, a re-shape — takes `example_units` or `audit_windows` instead; this fixture deliberately holds no rows, so it cannot grow back into the graph it replaced.
    """
    from rebuild.review.audit import _sibling_windows, load_workload
    from rebuild.review.build import M1_AUDIT, M1_LEDGER
    from rebuild.review.enrich import LETTERS

    workload = load_workload(M1_AUDIT, M1_LEDGER, dict(LETTERS))
    position = {id(unit): index for index, unit in enumerate(workload.units)}
    siblings = tuple(
        sorted(position[id(unit)] for group in _sibling_windows(workload.units).values() for unit in group)
    )
    return WorkloadIndex(
        units=tuple(
            IndexedUnit(
                codepoints=unit.codepoints,
                class_id=unit.class_id,
                no_verdict=unit.no_verdict,
                configs=unit.configs,
            )
            for unit in workload.units
        ),
        row_count=workload.row_count,
        sibling_positions=siblings,
    )
