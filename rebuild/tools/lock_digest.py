"""Hash `uv.lock` for every stamp that includes it: the lock's text with the project's own `[[package]]` block removed, so dependency pins change the digest and the project's version does not.

A version bump rewrites only the project's block (its `name`, `version`, `dependencies`, `[package.dev-dependencies]` and `[package.metadata]`, everything from its `[[package]]` line to the next), and no stage or gate reads that block. Every dependency has its own `[[package]]` block, and those stay whole, so upgrading fontTools or uharfbuzz, adding or removing a package, or changing any pin changes the digest. The project block is the one with the `source = { virtual = "." }` line uv writes for the root project. A lock that does not decode, or has no such block, is hashed as raw bytes, so two different fake locks never get the same digest and a broken lock still changes the key. The rebuild suite's fake repos write `lock-1`, and rebuild/test_oracle_cache.py writes `version = 1`.

The module imports only the standard library, like `site_fonts`: the pyright gate hashes the lock too, and the root conftest imports that gate, so this function cannot live in `rebuild.pipeline.fingerprint` without adding the pipeline to every rebuild test's closure (`rebuild.tools.cycle_paths` explains why that matters). `fingerprint.lock_digest` is this function. It reads the lock whole, which is tens of kilobytes; rebuild/test_fingerprint.py's read-whole sweep exempts it on that bound.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE_HEADER = "[[package]]"
PROJECT_SOURCE_LINE = 'source = { virtual = "." }'


def without_project_block(text: str) -> str | None:
    """Return the lock's text without the block that contains `PROJECT_SOURCE_LINE`, or None when no block contains it."""
    separator = f"\n{PACKAGE_HEADER}\n"
    blocks = ("\n" + text).split(separator)
    kept = [block for block in blocks if PROJECT_SOURCE_LINE not in block.splitlines()]
    if len(kept) == len(blocks):
        return None
    return separator.join(kept)


def lock_digest(path: Path) -> str:
    """Return the SHA-256 of the lock's text without the project's block, or of its raw bytes when the file does not decode or has no project block."""
    raw = Path(path).read_bytes()
    try:
        projected = without_project_block(raw.decode())
    except UnicodeDecodeError:
        projected = None
    if projected is None:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(projected.encode()).hexdigest()
