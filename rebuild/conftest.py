"""Shared fixtures for the rebuild suite. The one resident is `built_review_surface`, which owes every test a read-only review surface at the current input state while building as little as possible. First preference: when `surface_build_skippable` proves the cycle's own rebuild/out/review already reflects these inputs byte for byte, the fixture yields that directory directly and builds nothing — the steady state under the artifact cycle, and the same standard of proof the cycle itself skips its surface step on. That path assumes no artifact cycle is concurrently rewriting rebuild/out/review, the same standing assumption the suite already makes about the out/m1 artifacts it reads.

When the live surface is stale or absent, the cross-process cache under tmp/review-surface-test-cache/<key>/ serves instead: one worker builds under an exclusive flock (parallel at SURFACE_BUILD_JOBS — the half-width budget the artifact cycle's job_budget uses, and for the same reason: the xdist pool is hot while the builder runs); every other worker blocks on the lock and then loads the finished surface from disk, so a suite run costs at most one build instead of one per worker. The key is content-only — the full inputs fingerprint (data, baselines, pipeline code, review code, static, fonts) plus the out/m1 artifacts build_m1 reads — and deliberately mtime-blind, so cross-run hits survive pure mtime churn (git checkout, a make all that rewrote identical bytes). The manifest's generated_at/repo_head provenance stamps sit outside the key: two content-identical builds can differ in those two scalars, which is why test_builds_are_byte_identical masks them rather than requiring stamp-exact identity. flock (not a sentinel spinloop) serializes builders because the kernel releases it if a building worker dies, so a crash mid-build leaves no deadlock, just a missing DONE marker the next holder rebuilds over.
"""

import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO_ROOT / "tmp" / "review-surface-test-cache"
CACHE_KEEP = 2
SURFACE_BUILD_JOBS = max(1, (os.process_cpu_count() or 2) // 2)


def surface_cache_key() -> str | None:
    """Content-only key over everything that can move a build byte: the full inputs fingerprint and the out/m1 artifacts build_m1 reads (M1.otf, the divergence audit, the subset tables, the recorded stage-A fingerprint). None when the out/m1 artifacts don't exist yet (fresh clone); the fixture then falls back to an uncached per-session build."""
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


def _prune_stale_entries(current: Path) -> None:
    """Drop all but the newest CACHE_KEEP-1 sibling entries, taking each victim's own lock non-blocking first so a concurrent pytest run still reading that entry (it holds the lock for its whole read) is skipped instead of yanked out from under. Lock files are never unlinked: removing one while another process holds it open would let a third process lock a fresh inode under the same name, and two holders of "the" lock is exactly the corruption flock exists to prevent."""
    entries = sorted(
        (entry for entry in CACHE_ROOT.iterdir() if entry.is_dir() and entry != current),
        key=lambda entry: entry.stat().st_mtime,
        reverse=True,
    )
    for stale in entries[CACHE_KEEP - 1 :]:
        with (CACHE_ROOT / f"{stale.name}.lock").open("w") as lock:
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
                _prune_stale_entries(entry)
            fcntl.flock(lock, fcntl.LOCK_SH)
        manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
        yield surface, manifest
