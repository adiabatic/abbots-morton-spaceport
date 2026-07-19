"""Shared fixtures for the rebuild suite. The one resident is the cross-process cache behind `built_review_surface`: under xdist every worker that picked up a test_review_build test used to pay its own full build_m1 (~57 s × up to 12 workers per run), rebuilding the same surface the artifact cycle's surface-build step had just produced. The cache builds once into tmp/review-surface-test-cache/<key>/ under an exclusive flock; every other worker blocks on the lock and then loads the finished surface from disk, so a suite run costs one build instead of one per worker.

The key covers everything that can move a build byte: the full inputs fingerprint (data, baselines, pipeline code, review code, static, fonts), the out/m1 artifacts build_m1 reads (M1.otf, the divergence audit, the subset tables, the recorded stage-A fingerprint), and the exact `generated_at` stamp a fresh build would write. That stamp is mtime-derived, and the root conftest's `make all` rewrites the before font (fresh mtime, identical bytes) at the head of every xdist run, so in practice each xdist session re-keys and pays one build; cross-run hits happen only for runs that skip the make-all hook (single-process pytest, `-p no:xdist` debugging). The stamp still belongs in the key because test_builds_are_byte_identical compares one always-fresh build against the cached one, and a key hit must therefore imply byte-identity — manifest stamp included. flock (not a sentinel spinloop) serializes builders because the kernel releases it if a building worker dies, so a crash mid-build leaves no deadlock, just a missing DONE marker the next holder rebuilds over."""

import fcntl
import hashlib
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO_ROOT / "tmp" / "review-surface-test-cache"
CACHE_KEEP = 2


def surface_cache_key() -> str | None:
    """None when the inputs are too incomplete to fingerprint (fresh clone with no out/m1); the fixture then falls back to an uncached per-session build."""
    from rebuild.pipeline import fingerprint
    from rebuild.review import build

    m1_inputs = [build.M1_AFTER_FONT, build.M1_AUDIT, build.M1_SUBSETS / fingerprint.STAGE_A_FILENAME]
    m1_inputs += sorted(build.M1_SUBSETS.glob("baseline-*.subset.tsv.gz"))
    try:
        payload = {
            "inputs": fingerprint.compute_all(REPO_ROOT),
            "m1_artifacts": fingerprint.hash_paths(REPO_ROOT, m1_inputs),
            "generated_at": build._generated_at(
                build.M1_AUDIT, build.M1_LEDGER, build.SITE_BEFORE_FONT, build.M1_AFTER_FONT
            ),
        }
    except OSError, ValueError:
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
    """Yields (surface_dir, manifest) and holds a shared flock on the entry for the whole session, so tests can read shards for minutes while a concurrent session's pruner (which takes the victim's lock exclusively, non-blocking) can never delete the entry out from under them. The builder path takes the lock exclusively, then downgrades to shared — a single-holder downgrade, never the two-reader upgrade that can deadlock flock."""
    from rebuild.review.build import build_m1

    key = surface_cache_key()
    if key is None:
        out_dir = tmp_path_factory.mktemp("review-out")
        build_m1(out_dir)
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
                build_m1(surface)
                done.write_text("")
                _prune_stale_entries(entry)
            fcntl.flock(lock, fcntl.LOCK_SH)
        manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
        yield surface, manifest
