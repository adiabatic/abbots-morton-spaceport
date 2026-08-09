"""Lever: CSafeLoader inside spec_load, which parses every rune file TWICE.

`_FileContext.__init__` runs `yaml.compose(text, Loader=yaml.SafeLoader)` for the
line index and then `yaml.safe_load(text)` for the data. Both are pure-Python today.
Equivalence is checked two ways: every file's line index must be identical
(the compose swap's only observable), and the resolved spec must digest the same.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from rebuild.pipeline import spec_load  # noqa: E402


def spec_digest(spec) -> str:
    h = hashlib.sha256()
    for name in sorted(spec.runes):
        h.update(repr(spec.runes[name]).encode())
    h.update(repr(spec.registry).encode())
    return h.hexdigest()


def best(fn, reps=3):
    lo = float("inf")
    out = None
    for _ in range(reps):
        t0 = time.perf_counter()
        out = fn()
        lo = min(lo, time.perf_counter() - t0)
    return lo, out


def main() -> int:
    rune_paths = sorted((ROOT / "glyph_data" / "runes").glob("*.yaml"))
    texts = [p.read_text() for p in rune_paths]

    # Observable of the compose swap: the line index dict spec_load builds per file.
    pure_index = [spec_load._line_index(t) for t in texts]
    original_safeloader = yaml.SafeLoader
    yaml.SafeLoader = yaml.CSafeLoader
    try:
        fast_index = [spec_load._line_index(t) for t in texts]
    finally:
        yaml.SafeLoader = original_safeloader
    index_identical = pure_index == fast_index

    shipped_s, shipped_spec = best(spec_load.load_default_spec, 3)
    shipped_digest = spec_digest(shipped_spec)

    saved_safe_load = yaml.safe_load
    saved_safe_load_all = yaml.safe_load_all
    yaml.SafeLoader = yaml.CSafeLoader
    yaml.safe_load = lambda stream: yaml.load(stream, yaml.CSafeLoader)
    yaml.safe_load_all = lambda stream: yaml.load_all(stream, yaml.CSafeLoader)
    try:
        fast_s, fast_spec = best(spec_load.load_default_spec, 3)
        fast_digest = spec_digest(fast_spec)
    finally:
        yaml.SafeLoader = original_safeloader
        yaml.safe_load = saved_safe_load
        yaml.safe_load_all = saved_safe_load_all

    print(
        json.dumps(
            {
                "lever": "CSafeLoader inside spec_load (compose + safe_load, per rune file)",
                "file": "rebuild/pipeline/spec_load.py:243,285",
                "rune_files": len(rune_paths),
                "shipped_s": round(shipped_s, 5),
                "cloader_s": round(fast_s, 5),
                "saved_s": round(shipped_s - fast_s, 5),
                "speedup": round(shipped_s / fast_s, 2),
                "line_index_identical": index_identical,
                "spec_digest_identical": shipped_digest == fast_digest,
                "equivalent": bool(index_identical and shipped_digest == fast_digest),
                "spec_digest": shipped_digest[:16],
                "verification": "every rune file's yaml line index compared exactly, plus a sha256 over the resolved spec's runes and registry",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
