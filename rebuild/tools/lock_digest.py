"""The digest every stamp hashes `uv.lock` by: the lock's text with the project's own `[[package]]` block removed, so the dependency pins are in the digest and the project's version is not.

A version bump rewrites one block of the lock, the project's own — its `name`, `version`, `dependencies`, `[package.dev-dependencies]` and `[package.metadata]` sub-tables, everything between its `[[package]]` line and the next — and no stage or gate reads a byte of that block: the interpreter the tests and the build run under is decided by the pinned packages, each in a `[[package]]` block of its own. Those blocks stay whole, so a fontTools or uharfbuzz bump, a package added to or removed from the resolution, or a changed pin of anything moves the digest, while a bump of the project's version moves nothing keyed on it. The project block is found by the `source = { virtual = "." }` line uv writes for the root project and nothing else; a lock that will not decode, or that carries no such block — the rebuild suite's fake repos write `lock-1`, and rebuild/test_oracle_cache.py's writes `version = 1` — digests to its raw bytes, so two different fakes never collapse onto one value and a broken lock stays visible.

A leaf, like `site_fonts`: the pyright gate hashes the lock too, and the root conftest imports that gate, so the projection cannot live in `rebuild.pipeline.fingerprint` without dragging the pipeline into every rebuild test's closure (`rebuild.tools.cycle_paths` has the argument). `fingerprint.lock_digest` is this function. The lock is read whole — tens of kilobytes of text — which rebuild/test_fingerprint.py's read-whole sweep names as the bound.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE_HEADER = "[[package]]"
PROJECT_SOURCE_LINE = 'source = { virtual = "." }'


def without_project_block(text: str) -> str | None:
    """The lock's text with the block carrying `PROJECT_SOURCE_LINE` cut out, or None when no block carries it."""
    separator = f"\n{PACKAGE_HEADER}\n"
    blocks = ("\n" + text).split(separator)
    kept = [block for block in blocks if PROJECT_SOURCE_LINE not in block.splitlines()]
    if len(kept) == len(blocks):
        return None
    return separator.join(kept)


def lock_digest(path: Path) -> str:
    """One lock file's project-blind digest: the text minus the project's own block, hashed; the raw bytes when the file will not decode or holds no project block."""
    raw = Path(path).read_bytes()
    try:
        projected = without_project_block(raw.decode())
    except UnicodeDecodeError:
        projected = None
    if projected is None:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(projected.encode()).hexdigest()
