"""Shared fixtures for the rebuild suite, and the two-lane split that decides how wide it may run.

The suite divides into two lanes, and lane membership is *derived*, never hand-listed: `live_artifacts` is the one fixture that names the build's live output, so a test that requests it — directly, or through any fixture that requests it — is a **validators** test, and everything else is a **contracts** test. Contracts tests read only checked-in inputs and what they build themselves, so nothing in that lane carries the 14–17 GB working set the live fixtures do and the lane may run at full xdist width; the validators lane keeps the checked-in 2-worker default the root conftest sets, which is sized for the most RAM-constrained box that runs this repo. `--lane contracts` / `--lane validators` selects one (the default `all` runs both, which is what a bare `uv run pytest rebuild/` still does), and `pytest_xdist_auto_num_workers` here answers `-n auto` for the contracts lane only, deferring to the root conftest — and to `PYTEST_XDIST_AUTO_NUM_WORKERS` ahead of it — in every other case.

A derived rule needs a check that the derivation is honest, so a `sys.addaudithook` guard makes lane membership structural rather than aspirational. It is installed once per process, sits inactive, and is switched on only for the setup, call, and teardown of a contracts-lane item; while active, any read or write whose path falls under the live-artifact trees (`rebuild/out/`, the whole of `tmp/`, the gate's own exempt prefixes, the root `verdicts-*` stores) raises `ContractsLaneViolation` naming the test and the path, and a phase that swallows that exception still fails through `pytest_runtest_makereport`. What the guard does not cover is documented at the hook: subprocess children run unaudited, and `Path.exists()`/stat never reach it — it is the content reads that are caught, which is the leak that matters.

`_redirect_cycle_writes` is the standing guarantee that running the suite never costs the working repo a file; it is autouse, so every module in rebuild/ gets it whether or not its author thought about the cycle. `built_review_surface` owes every test a read-only review surface at the current input state while building as little as possible. First preference: when `surface_build_skippable` proves the cycle's own rebuild/out/review already reflects these inputs byte for byte, the fixture yields that directory directly and builds nothing — the steady state under the artifact cycle, and the same standard of proof the cycle itself skips its surface step on. That path assumes no artifact cycle is concurrently rewriting rebuild/out/review, the same standing assumption the suite already makes about the out/m1 artifacts it reads.

When the live surface is stale or absent, the cross-process cache under tmp/review-surface-test-cache/<key>/ serves instead: one worker builds under an exclusive flock (parallel at SURFACE_BUILD_JOBS — two jobs, the standing width for a surface build under a hot xdist pool, sized like every parallelism default here for the most RAM-constrained box that runs this; the artifact cycle's own job_budget caps match it); every other worker blocks on the lock and then loads the finished surface from disk, so a suite run costs at most one build instead of one per worker. The key is content-only — the full inputs fingerprint (data, baselines, pipeline code, review code, static, fonts) plus the out/m1 artifacts build_m1 reads — and deliberately mtime-blind, so cross-run hits survive pure mtime churn (git checkout, a make all that rewrote identical bytes). The manifest's generated_at/repo_head provenance stamps sit outside the key: two content-identical builds can differ in those two scalars, which is why test_builds_are_byte_identical masks them rather than requiring stamp-exact identity. flock (not a sentinel spinloop) serializes builders because the kernel releases it if a building worker dies, so a crash mid-build leaves no deadlock, just a missing DONE marker the next holder rebuilds over.

`enriched_units` owes every test the whole live workload enriched, and runs the same cache for the same reason. Scoping that fixture to a module bought nothing: `--dist worksteal` hands each test to whichever worker is free, so a module whose tests sweep the enriched universe scatters over as many workers as it has such tests and every one of them re-enriched the workload from scratch. One worker now builds under the same exclusive flock and writes a compressed pickle; the rest block and unpickle it, at a small fraction of the enrichment it stands in for. That fixture holds its lock through the read as well, for a reason its docstring records: what a reader materializes here is gigabytes, not a manifest.

`workload` is the same collapse one stage upstream: the un-enriched live workload, which four review modules used to load module-scoped, each paying the 92 MB audit parse per worker — and worse than per-worker, since worksteal can hand a worker a module it already finalized and the fixture rebuilds. One session-scoped fixture on the same cache discipline now serves them all.
"""

import fcntl
import gzip
import hashlib
import json
import os
import pickle
import shutil
import sys
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from rebuild.tools import artifact_cycle

if TYPE_CHECKING:
    from rebuild.review.audit import Workload
    from rebuild.review.enrich import EnrichedUnit

REAL_RUN_RETENTION = artifact_cycle.run_retention
LIVE_DELETION_TARGETS = (
    *artifact_cycle.M1_SUMMARY_FILES.values(),
    artifact_cycle.CONFORM_SUMMARY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO_ROOT / "tmp" / "review-surface-test-cache"
ENRICH_CACHE_ROOT = REPO_ROOT / "tmp" / "review-enrich-test-cache"
WORKLOAD_CACHE_ROOT = REPO_ROOT / "tmp" / "review-workload-test-cache"
CACHE_KEEP = 2
SURFACE_BUILD_JOBS = 2
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
LIVE_FIXTURE = "live_artifacts"

# The live trees, derived rather than listed: rebuild/out/ (everything the build and the cycle write), the whole of tmp/, the root-level verdicts-* stores, and whatever the rebuild gate exempts from its input closure. That last list is derived rather than copied so it tracks the gate, but it needs one subtraction, because its three entries are exempt for two different reasons: rebuild/evidence/ and the census pins are regenerated state the gate refuses to hash, while rebuild/review/jstests/ is checked-in JavaScript that is merely outside a Python closure — source, which a contracts test is free to read, and which the cycle's own plan step globs while enumerating the JS suite. tmp/ is forbidden whole rather than by the fixture caches and cycle snapshots that happen to sit in it: the tree is entirely outside the suite's input closure, and the write standard below already bars every test from writing under the live repo, so nothing a contracts test may legitimately read can be there. A test that wants a scratch directory takes `tmp_path`.
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
    """What `-n auto` resolves to for the contracts lane, and nothing else. The checked-in default is 2 because a validators worker peaks at 14–17 GB and a full-width pool of them drives the most RAM-constrained box that runs this repo into swap — but a contracts worker builds nothing live and holds none of that, so the lane that provably contains no live fixture is free to take every core. Returning None hands the question back: to the root conftest's 2, or, when `PYTEST_XDIST_AUTO_NUM_WORKERS` is set, to the root conftest's reading of that override, which stays the one way to widen any pool here a run at a time."""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    if config.getoption("lane", default=None) == "contracts":
        return os.cpu_count() or 2
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


@cache
def surface_cache_key() -> str | None:
    """Content-only key over everything that can move a build byte: the full inputs fingerprint and the out/m1 artifacts build_m1 reads (M1.otf, the divergence audit, the subset tables, the recorded stage-A fingerprint). None when the out/m1 artifacts don't exist yet (fresh clone); the fixture then falls back to an uncached per-session build. Memoized because two fixtures now key off it and the hash reads a hundred-odd megabytes — the audit alone is most of it — which no worker should pay twice; the inputs cannot move under a running session."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import build

    if not build.M1_AUDIT.exists() or not build.M1_AFTER_FONT.exists():
        return None
    m1_inputs = [build.M1_AFTER_FONT, build.M1_AUDIT, build.M1_SUBSETS / fingerprint.STAGE_A_FILENAME]
    m1_inputs += sorted(build.M1_SUBSETS.glob("baseline-*.subset.tsv.gz"))
    try:
        payload = {
            "inputs": fingerprint.compute_all(REPO_ROOT),
            "m1_artifacts": fingerprint.hash_paths(REPO_ROOT, m1_inputs),
        }
    except OSError:
        return None
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _entry_mtime(entry: Path) -> float:
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


def _prune_stale_entries(root: Path, current: Path) -> None:
    """Drop all but the newest CACHE_KEEP-1 sibling entries under `root`, taking each victim's own lock non-blocking first so a concurrent pytest run still reading that entry (it holds the lock for its whole read) is skipped instead of yanked out from under. Lock files are never unlinked: removing one while another process holds it open would let a third process lock a fresh inode under the same name, and two holders of "the" lock is exactly the corruption flock exists to prevent. A victim that vanishes between the listing and its stat sorts last rather than raising: the pruner runs unlocked up to this point, so another session pruning the same root concurrently — two roots means a session now runs this twice — would otherwise take the whole run down over an entry it was about to delete anyway."""
    entries = sorted(
        (entry for entry in root.iterdir() if entry.is_dir() and entry != current),
        key=_entry_mtime,
        reverse=True,
    )
    for stale in entries[CACHE_KEEP - 1 :]:
        with (root / f"{stale.name}.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue
            try:
                shutil.rmtree(stale, ignore_errors=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


@pytest.fixture(scope="session")
def built_review_surface(tmp_path_factory, live_artifacts: LiveArtifacts):
    """Yields (surface_dir, manifest). The provably-fresh rebuild/out/review is yielded read-only with no lock — nothing prunes the live surface. A cache entry is held under a shared flock for the whole session, so tests can read shards for minutes while a concurrent session's pruner (which takes the victim's lock exclusively, non-blocking) can never delete the entry out from under them. The builder path takes the lock exclusively, then downgrades to shared — a single-holder downgrade, never the two-reader upgrade that can deadlock flock."""
    from rebuild.review.build import build_m1
    from rebuild.tools.artifact_cycle import REVIEW_OUT, surface_build_skippable

    if surface_build_skippable(REPO_ROOT):
        manifest = json.loads((REVIEW_OUT / "manifest.json").read_text(encoding="utf-8"))
        yield REVIEW_OUT, manifest
        return
    key = surface_cache_key()
    if key is None:
        out_dir = tmp_path_factory.mktemp("review-out")
        build_m1(out_dir, jobs=SURFACE_BUILD_JOBS)
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        yield out_dir, manifest
        return
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    entry = CACHE_ROOT / key
    surface = entry / "surface"
    done = entry / "DONE"
    with (CACHE_ROOT / f"{key}.lock").open("w") as lock:
        if done.exists():
            fcntl.flock(lock, fcntl.LOCK_SH)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not done.exists():
                shutil.rmtree(entry, ignore_errors=True)
                surface.mkdir(parents=True)
                build_m1(surface, jobs=SURFACE_BUILD_JOBS)
                done.write_text("")
                _prune_stale_entries(CACHE_ROOT, entry)
            fcntl.flock(lock, fcntl.LOCK_SH)
        manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
        yield surface, manifest


def _enrich_workload() -> list[EnrichedUnit]:
    """The live M1 workload with every unit enriched — what the cache holds, and what a cacheless run builds directly."""
    from rebuild.review.audit import load_workload
    from rebuild.review.build import M1_AFTER_FONT, M1_AUDIT, M1_LEDGER, M1_SUBSETS
    from rebuild.review.enrich import LETTERS, Enricher, load_spec

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(REPO_ROOT)
    enricher = Enricher(spec, M1_SUBSETS, M1_AFTER_FONT)
    workload = load_workload(M1_AUDIT, M1_LEDGER, dict(LETTERS))
    return [enricher.enrich(unit) for unit in workload.units]


@pytest.fixture(scope="session")
def enriched_units(live_artifacts: LiveArtifacts) -> list[EnrichedUnit]:
    """Every unit of the live workload, enriched, read-only for the session. The cache is `built_review_surface`'s — the same `surface_cache_key`, a superset of what enrichment reads (the inputs fingerprint's pipeline_code component covers the rebuild/validation shaping and row-model code enrichment imports) so it can only over-invalidate (None on a fresh clone falls back to an uncached build), the same one-builder-under-flock discipline, the same non-blocking prune. What it stores is a gzipped protocol-5 pickle of the real EnrichedUnits, written and read as a stream so neither side ever holds the serialized form beside the objects; at compresslevel 1 it is an order of magnitude smaller than the raw pickle and costs a fraction of a second to inflate. Every worker returns the round trip, the builder included, so no test can quietly come to depend on being the one that enriched.

    The lock is exclusive for the read too, which is where this fixture departs from `built_review_surface` and its shared-hold downgrade. That fixture's readers pull a few files off disk; this one's each materialize an enriched universe several gigabytes live, and letting the queued workers do that at once is not a small pessimization but a machine-wide one — measured on a 34 GB host, concurrent reads spent an order of magnitude more time in the kernel reclaiming pages than the whole serialized run costs, so the exclusive hold buys more by staggering the readers than the parallelism it gives up was ever worth. Staggering the readers is the whole of its job: a shared hold would fend off a concurrent pruner just as well. It is released before the first test runs either way.

    The payload, not the marker, is what proves the entry usable. An interrupted prune deletes the entry's files in directory order and can strand a DONE whose pickle is already gone, which on a one-file payload is the likely outcome rather than a corner — and a marker taken on trust would then wedge that key for every session that ever computes it again.
    """
    key = surface_cache_key()
    if key is None:
        return _enrich_workload()
    ENRICH_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    entry = ENRICH_CACHE_ROOT / key
    blob = entry / "units.pickle.gz"
    done = entry / "DONE"
    with (ENRICH_CACHE_ROOT / f"{key}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (done.exists() and blob.exists()):
            shutil.rmtree(entry, ignore_errors=True)
            entry.mkdir(parents=True)
            with gzip.open(blob, "wb", compresslevel=1) as handle:
                pickle.dump(_enrich_workload(), handle, protocol=5)
            done.write_text("")
            _prune_stale_entries(ENRICH_CACHE_ROOT, entry)
        with gzip.open(blob, "rb") as handle:
            return pickle.load(handle)


def workload_cache_key() -> str | None:
    """`load_workload`'s input closure: the audit and ledger bytes, the letter map, and rebuild/review/audit.py itself — the loader's own code is in the key because a pickle from before an audit.py change would hand every worker units the loader on disk no longer builds, with the gate reading green over them. Deliberately narrower than `surface_cache_key`: nothing else in the inputs fingerprint can move a Workload byte, so a rune edit or a review-code change elsewhere doesn't evict a still-true entry. None when the audit artifact doesn't exist yet (fresh clone); the fixture then falls back to an uncached per-session load."""
    from rebuild.pipeline import fingerprint
    from rebuild.review.build import M1_AUDIT, M1_LEDGER
    from rebuild.review.enrich import LETTERS

    if not M1_AUDIT.exists():
        return None
    inputs = [M1_AUDIT, M1_LEDGER, REPO_ROOT / "rebuild" / "review" / "audit.py"]
    try:
        digest = fingerprint.hash_paths(REPO_ROOT, inputs)
    except OSError:
        return None
    return hashlib.sha256(f"{digest}\n{sorted(LETTERS.items())!r}".encode()).hexdigest()[:16]


def _load_live_workload() -> Workload:
    """The live M1 workload as `load_workload` builds it — what the cache holds, and what a cacheless run builds directly."""
    from rebuild.review.audit import load_workload
    from rebuild.review.build import M1_AUDIT, M1_LEDGER
    from rebuild.review.enrich import LETTERS

    return load_workload(M1_AUDIT, M1_LEDGER, dict(LETTERS))


@pytest.fixture(scope="session")
def workload(live_artifacts: LiveArtifacts) -> Workload:
    """The live workload, loaded once per suite run instead of once per module per worker. Module scope was never the per-module cost it read as: under `--dist worksteal` it is per-worker at best and measured worse, because worksteal hands out items in no particular module order, so a worker bouncing back into a module it already finalized pays the same load again — a probed gate run built the four modules' fixtures 13 times for 63 CPU-seconds, single workers paying the same module twice. The cache is `enriched_units`' discipline exactly: one builder under an exclusive flock writing a gzipped protocol-5 pickle, every other worker blocking and unpickling at a fraction of the load it stands in for, the lock held through the read so the ≈0.8 GB inflations stagger instead of landing at once, and the payload — not the marker — proving the entry usable.

    Each worker's round trip is its own object graph, but within a worker every module now shares one, and `Unit` is mutable, so the graph is read-only by contract. The one test that writes is contained: `test_assign_batches_slices_the_human_workload_and_nulls_machine_units` restores the loader defaults in its `finally` — a restore this fixture load-bears on, since `test_unit_ids_are_sequential_and_batches_unassigned_until_ink_is_known` asserts those defaults on the shared graph.
    """
    key = workload_cache_key()
    if key is None:
        return _load_live_workload()
    WORKLOAD_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    entry = WORKLOAD_CACHE_ROOT / key
    blob = entry / "workload.pickle.gz"
    done = entry / "DONE"
    with (WORKLOAD_CACHE_ROOT / f"{key}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (done.exists() and blob.exists()):
            shutil.rmtree(entry, ignore_errors=True)
            entry.mkdir(parents=True)
            with gzip.open(blob, "wb", compresslevel=1) as handle:
                pickle.dump(_load_live_workload(), handle, protocol=5)
            done.write_text("")
            _prune_stale_entries(WORKLOAD_CACHE_ROOT, entry)
        with gzip.open(blob, "rb") as handle:
            return pickle.load(handle)
