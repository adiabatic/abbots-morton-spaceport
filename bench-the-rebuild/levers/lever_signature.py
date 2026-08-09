"""Lever 5: signature_digest is repr()-then-sha256. Measure a direct binary hash.

Builds REAL InkComparator signatures from the built surface's own fonts and texts,
then digests them the shipped way and three cheaper ways. Equivalence for a digest
whose only contract is "equal digest iff equal signature" is the induced PARTITION,
so that is what is checked — plus the fact that the shipped digest is unchanged for
the identity-preserving variant.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review import ink as ink_mod  # noqa: E402

REVIEW = ROOT / "rebuild" / "out" / "review"


def timed(fn, reps=3):
    best = float("inf")
    out = None
    for _ in range(reps):
        t0 = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - t0)
    return best, out


def flatten(value, acc: bytearray) -> None:
    if isinstance(value, tuple):
        acc += b"("
        for item in value:
            flatten(item, acc)
        acc += b")"
    elif isinstance(value, int):
        acc += value.to_bytes(8, "little", signed=True)
    elif isinstance(value, float):
        acc += repr(value).encode()
    elif isinstance(value, str):
        acc += value.encode()
        acc += b"\x00"
    elif value is None:
        acc += b"N"
    else:
        acc += repr(value).encode()


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    manifest = json.loads((REVIEW / "manifest.json").read_text())
    comparator = ink_mod.InkComparator(
        REVIEW / manifest["fonts"]["before"]["file"], REVIEW / manifest["fonts"]["after"]["file"]
    )
    texts: list[tuple[str, str]] = []
    for entry in manifest["classes"]:
        shard = entry.get("shard")
        if not shard:
            continue
        for unit in json.loads((REVIEW / shard).read_text()):
            codepoints = unit.get("codepoints")
            configs = unit.get("configs") or ("default",)
            if isinstance(codepoints, str) and codepoints:
                text = "".join(chr(int(part, 16)) for part in codepoints.split(":"))
                texts.append((text, configs[0]))
            if len(texts) >= limit:
                break
        if len(texts) >= limit:
            break

    t_sig, sigs = timed(lambda: [comparator.signature(t, c) for t, c in texts], 2)

    def shipped():
        return [ink_mod.signature_digest(s) for s in sigs]

    def via_pickle():
        return [hashlib.sha256(pickle.dumps(s, protocol=5)).hexdigest() for s in sigs]

    def via_flatten():
        out = []
        for s in sigs:
            acc = bytearray()
            flatten(s, acc)
            out.append(hashlib.sha256(bytes(acc)).hexdigest())
        return out

    t_ship, d_ship = timed(shipped, 3)
    t_pick, d_pick = timed(via_pickle, 3)
    t_flat, d_flat = timed(via_flatten, 3)

    def partition(digests):
        groups: dict[str, list[int]] = {}
        for index, digest in enumerate(digests):
            groups.setdefault(digest, []).append(index)
        return sorted(sorted(v) for v in groups.values())

    p_ship = partition(d_ship)
    result = {
        "lever": "signature_digest: repr()+sha256 -> binary hash",
        "file": "rebuild/review/ink.py:53",
        "signatures": len(sigs),
        "shape_build_s": round(t_sig, 4),
        "shipped_repr_sha256_s": round(t_ship, 5),
        "pickle_sha256_s": round(t_pick, 5),
        "flatten_sha256_s": round(t_flat, 5),
        "speedup_pickle": round(t_ship / t_pick, 2),
        "speedup_flatten": round(t_ship / t_flat, 2),
        "per_signature_us_shipped": round(t_ship / len(sigs) * 1e6, 1),
        "per_signature_us_pickle": round(t_pick / len(sigs) * 1e6, 1),
        "partition_identical_pickle": p_ship == partition(d_pick),
        "partition_identical_flatten": p_ship == partition(d_flat),
        "distinct": {
            "shipped": len(set(d_ship)),
            "pickle": len(set(d_pick)),
            "flatten": len(set(d_flat)),
        },
        "partition_digest": hashlib.sha256(repr(p_ship).encode()).hexdigest()[:16],
        "caveat": "digest BYTES change, so the persisted unit_cache is invalidated once",
    }
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
