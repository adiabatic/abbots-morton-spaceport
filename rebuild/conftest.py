"""Shared fixtures for the rebuild suite. Three reside here. `_redirect_cycle_writes` is the standing guarantee that running the suite never costs the working repo a file; it is autouse, so every module in rebuild/ gets it whether or not its author thought about the cycle. `built_review_surface` owes every test a read-only review surface at the current input state while building as little as possible. First preference: when `surface_build_skippable` proves the cycle's own rebuild/out/review already reflects these inputs byte for byte, the fixture yields that directory directly and builds nothing — the steady state under the artifact cycle, and the same standard of proof the cycle itself skips its surface step on. That path assumes no artifact cycle is concurrently rewriting rebuild/out/review, the same standing assumption the suite already makes about the out/m1 artifacts it reads.

When the live surface is stale or absent, the cross-process cache under tmp/review-surface-test-cache/<key>/ serves instead: one worker builds under an exclusive flock (parallel at SURFACE_BUILD_JOBS — the half-width budget the artifact cycle's job_budget uses, and for the same reason: the xdist pool is hot while the builder runs); every other worker blocks on the lock and then loads the finished surface from disk, so a suite run costs at most one build instead of one per worker. The key is content-only — the full inputs fingerprint (data, baselines, pipeline code, review code, static, fonts) plus the out/m1 artifacts build_m1 reads — and deliberately mtime-blind, so cross-run hits survive pure mtime churn (git checkout, a make all that rewrote identical bytes). The manifest's generated_at/repo_head provenance stamps sit outside the key: two content-identical builds can differ in those two scalars, which is why test_builds_are_byte_identical masks them rather than requiring stamp-exact identity. flock (not a sentinel spinloop) serializes builders because the kernel releases it if a building worker dies, so a crash mid-build leaves no deadlock, just a missing DONE marker the next holder rebuilds over.

`enriched_units` owes every test the whole live workload enriched, and runs the same cache for the same reason. Scoping that fixture to a module bought nothing: `--dist worksteal` hands each test to whichever worker is free, so a module whose tests sweep the enriched universe scatters over as many workers as it has such tests and every one of them re-enriched the workload from scratch. One worker now builds under the same exclusive flock and writes a compressed pickle; the rest block and unpickle it, at a small fraction of the enrichment it stands in for. That fixture holds its lock through the read as well, for a reason its docstring records: what a reader materializes here is gigabytes, not a manifest.
"""

import fcntl
import gzip
import hashlib
import json
import os
import pickle
import shutil
import warnings
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from rebuild.tools import artifact_cycle

if TYPE_CHECKING:
    from rebuild.review.enrich import EnrichedUnit

REAL_RUN_RETENTION = artifact_cycle.run_retention
LIVE_DELETION_TARGETS = (*artifact_cycle.M1_SUMMARY_FILES.values(), artifact_cycle.CONFORM_SUMMARY)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO_ROOT / "tmp" / "review-surface-test-cache"
ENRICH_CACHE_ROOT = REPO_ROOT / "tmp" / "review-enrich-test-cache"
CACHE_KEEP = 2
SURFACE_BUILD_JOBS = max(1, (os.process_cpu_count() or 2) // 2)
GREEN_RECORDS = (
    "PLUMBING_GREEN",
    "CONFORM_GREEN",
    "REBUILD_GATE_GREEN",
    "RUN_M1_GREEN",
    "MAKE_TEST_GREEN",
    "CENSUS_RESULT",
)


@pytest.fixture(autouse=True)
def _redirect_cycle_writes(monkeypatch, tmp_path):
    """The standard: nothing the suite runs may write to or delete from the live repo. Every cycle stage resolves its paths at call time, so a test that forgets to redirect one still passes while the repo quietly loses a file — which makes the default, not the individual test, the only thing that can be relied on. It is autouse and lives here rather than beside the tests that drive the cycle because a guard that covers one module is no guard at all: a new test module under rebuild/ inherits nothing, and the first one written after the fact will reach straight past it. Everything below is a default, and a test wanting the real behavior overrides it, since a per-test monkeypatch lands after this one and wins.

    The writes are the green records and the cycle summary, each a module constant this can point under tmp_path. Left live, a test driving _run_cycle over mocked stages leaves a record in rebuild/out that the next real cycle reads as proof that content it never tested had passed.

    The deletes are the three stages that clear stale artifacts before rebuilding them: run_m1's four gate summaries and gate:conform's, unlinked just before their subprocess spawns so the verdict can only come from this cycle, and the retention pass. Redirecting a constant is enough for the first two; retention takes none — it resolves every target from ROOT at call time — so it is stubbed out instead. Any test reaching a green finish with record_greens set would otherwise sweep the repo: every tmp/review-pre-* snapshot, the root's verdicts-carried-*.json exports, the autosave stashes, and a compaction of the verdict journal. That is destructive against a cycle running in another terminal — it deleted a live pass's only snapshot out from under its carry, stranding the pass's verdicts — and doubly so now that the rebuild gate is meant to run beside a live review server. A test that wants the real retention takes the `real_run_retention` fixture and points ROOT somewhere disposable; a test asserting that _finish reaches retention patches run_retention itself.
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
def built_review_surface(tmp_path_factory):
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


def enrich_cache_key() -> str | None:
    """`surface_cache_key` widened by the shaping and row-model code, which enrichment reads and no fingerprint covers. `compute_all` hashes rebuild/pipeline and rebuild/review; `enrich` also imports `Shaper` and `iter_rows` out of rebuild/validation, and a key blind to those would hand every worker units the code on disk no longer produces — with the gate reading green over them, which is the one failure a test cache must not have."""
    from rebuild.pipeline import fingerprint

    key = surface_cache_key()
    if key is None:
        return None
    validation = sorted((REPO_ROOT / "rebuild" / "validation").glob("*.py"))
    payload = f"{key}\n{fingerprint.hash_paths(REPO_ROOT, validation)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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
def enriched_units() -> list[EnrichedUnit]:
    """Every unit of the live workload, enriched, read-only for the session. The cache is `built_review_surface`'s — `enrich_cache_key`'s content key, a superset of what enrichment reads so it can only over-invalidate (None on a fresh clone falls back to an uncached build), the same one-builder-under-flock discipline, the same non-blocking prune. What it stores is a gzipped protocol-5 pickle of the real EnrichedUnits, written and read as a stream so neither side ever holds the serialized form beside the objects; at compresslevel 1 it is an order of magnitude smaller than the raw pickle and costs a fraction of a second to inflate. Every worker returns the round trip, the builder included, so no test can quietly come to depend on being the one that enriched.

    The lock is exclusive for the read too, which is where this fixture departs from `built_review_surface` and its shared-hold downgrade. That fixture's readers pull a few files off disk; this one's each materialize an enriched universe several gigabytes live, and letting the queued workers do that at once is not a small pessimization but a machine-wide one — measured on a 34 GB host, concurrent reads spent an order of magnitude more time in the kernel reclaiming pages than the whole serialized run costs, so the exclusive hold buys more by staggering the readers than the parallelism it gives up was ever worth. Staggering the readers is the whole of its job: a shared hold would fend off a concurrent pruner just as well. It is released before the first test runs either way.

    The payload, not the marker, is what proves the entry usable. An interrupted prune deletes the entry's files in directory order and can strand a DONE whose pickle is already gone, which on a one-file payload is the likely outcome rather than a corner — and a marker taken on trust would then wedge that key for every session that ever computes it again.
    """
    key = enrich_cache_key()
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
