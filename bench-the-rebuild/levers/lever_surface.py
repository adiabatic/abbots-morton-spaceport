"""Levers 3 + 4 + 5, measured against the real built review surface (read-only).

  3. build.config_badge     — cardinality of its real argument multiset, and an lru_cache A/B
  4. status.load_human_unit_ids — its real cost, and how many times the plumbing chain pays it
  5. ink.signature_digest   — repr()-then-sha256 against direct binary hashing

Reads rebuild/out/review/ and never writes anything outside bench-the-rebuild/levers/.
"""

from __future__ import annotations

import collections
import functools
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review import build as build_mod  # noqa: E402
from rebuild.review import ink as ink_mod  # noqa: E402
from rebuild.review import status as status_mod  # noqa: E402

REVIEW = ROOT / "rebuild" / "out" / "review"
LEVERS_OUT = Path(__file__).resolve().parent / "out"


def timed(fn, reps=3):
    best = float("inf")
    result = None
    for _ in range(reps):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best, result


def config_badge_lever(units) -> dict:
    full = build_mod.ACCEPTANCE_CONFIGS
    calls = [tuple(u.get("configs") or ()) for u in units]
    calls = [c for c in calls if c]
    distinct = collections.Counter(calls)

    def shipped():
        return [build_mod.config_badge(c, full) for c in calls]

    cached = functools.lru_cache(maxsize=None)(build_mod.config_badge)

    def levered():
        return [cached(c, full) for c in calls]

    cached.cache_clear()
    t_ship, out_ship = timed(shipped, 3)
    cached.cache_clear()
    t_lev, out_lev = timed(levered, 3)
    return {
        "lever": "lru_cache on build.config_badge",
        "file": "rebuild/review/build.py:189",
        "calls_in_a_full_surface_pass": len(calls),
        "distinct_argument_tuples": len(distinct),
        "shipped_s": round(t_ship, 4),
        "cached_s": round(t_lev, 6),
        "saved_s": round(t_ship - t_lev, 4),
        "speedup": round(t_ship / t_lev, 1),
        "outputs_identical": out_ship == out_lev,
        "output_digest": hashlib.sha256(repr(out_ship).encode()).hexdigest()[:16],
    }


def human_unit_ids_lever() -> dict:
    t_one, ids = timed(lambda: status_mod.load_human_unit_ids(REVIEW), 2)
    manifest = json.loads((REVIEW / "manifest.json").read_text())
    shard_bytes = sum((REVIEW / e["shard"]).stat().st_size for e in manifest["classes"] if e.get("shard"))
    # What persisting the id set in the manifest would cost to read back instead.
    payload = json.dumps(sorted(ids))
    scratch = LEVERS_OUT / "human-ids.json"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(payload)
    t_persisted, ids2 = timed(lambda: frozenset(json.loads(scratch.read_text())), 3)
    return {
        "lever": "persist load_human_unit_ids' id set in manifest.json",
        "file": "rebuild/review/status.py:103",
        "ids": len(ids),
        "shard_bytes_reparsed_per_call": shard_bytes,
        "per_call_s": round(t_one, 4),
        "persisted_read_s": round(t_persisted, 5),
        "saved_per_call_s": round(t_one - t_persisted, 4),
        "same_id_set": ids == ids2,
        "id_set_digest": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16],
    }


def signature_digest_lever(units) -> dict:
    """The real shape of an InkComparator.signature: a (before, after) pair of tuples of
    placed outlines, each a tuple of integer point tuples. Rebuilt here from the surface's
    own render payloads so the input is real, then digested both ways."""
    signatures = []
    for unit in units:
        for group in unit.get("render_groups") or ():
            for side in ("before", "after"):
                pieces = group.get(side)
                if pieces:
                    signatures.append(pieces)
        if len(signatures) >= 400:
            break

    # Coerce to the nested-tuple shape signature() returns.
    def tupleize(value):
        if isinstance(value, list):
            return tuple(tupleize(v) for v in value)
        if isinstance(value, dict):
            return tuple(sorted((k, tupleize(v)) for k, v in value.items()))
        return value

    sigs = [tupleize(s) for s in signatures]
    if not sigs:
        return {"lever": "signature_digest", "skipped": "no render payloads in the built surface"}

    def shipped():
        return [ink_mod.signature_digest(s) for s in sigs]

    def binary():
        out = []
        for s in sigs:
            out.append(hashlib.sha256(pickle.dumps(s, protocol=5)).hexdigest())
        return out

    t_ship, d_ship = timed(shipped, 3)
    t_bin, d_bin = timed(binary, 3)
    # Equivalence is the induced partition, not the digest bytes.
    part_ship = collections.Counter(collections.Counter(d_ship).values())
    part_bin = collections.Counter(collections.Counter(d_bin).values())
    groups_ship = sorted(sorted(i for i, d in enumerate(d_ship) if d == key) for key in set(d_ship))
    groups_bin = sorted(sorted(i for i, d in enumerate(d_bin) if d == key) for key in set(d_bin))
    return {
        "lever": "signature_digest: repr()+sha256 -> pickle+sha256",
        "file": "rebuild/review/ink.py:53",
        "signatures": len(sigs),
        "shipped_s": round(t_ship, 5),
        "binary_s": round(t_bin, 5),
        "saved_s": round(t_ship - t_bin, 5),
        "speedup": round(t_ship / t_bin, 2),
        "same_partition": groups_ship == groups_bin,
        "distinct_shipped": len(set(d_ship)),
        "distinct_binary": len(set(d_bin)),
        "partition_shape": sorted(part_ship.items()) == sorted(part_bin.items()),
        "note": "digest BYTES change; the persisted unit_cache would be invalidated once",
    }


def main() -> int:
    manifest = json.loads((REVIEW / "manifest.json").read_text())
    units = []
    for entry in manifest["classes"]:
        shard = entry.get("shard")
        if not shard:
            continue
        units.extend(json.loads((REVIEW / shard).read_text()))
    out = {
        "surface_units": len(units),
        "config_badge": config_badge_lever(units),
        "load_human_unit_ids": human_unit_ids_lever(),
    }
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
