"""Lever 1: yaml.CSafeLoader in place of the pure-Python loader.

Measures parse cost per real corpus with both loaders and verifies the parsed
objects are equal (and that the derived rune digests are byte-identical).
Read-only. Prints one JSON object.
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

from rebuild.pipeline import fingerprint  # noqa: E402


def bench(fn, reps: int) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def corpus_texts(paths) -> list[str]:
    return [p.read_text() for p in paths]


def main() -> int:
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    corpora = {
        "runes (18 files)": sorted((ROOT / "glyph_data" / "runes").glob("*.yaml")),
        "glyph_data/*.yaml": sorted((ROOT / "glyph_data").glob("*.yaml")),
        "quikscript.yaml": [ROOT / "glyph_data" / "quikscript.yaml"],
        "rebuild ledgers": [
            ROOT / "rebuild" / "m1-aliases.yaml",
            ROOT / "rebuild" / "m1-contact-allow.yaml",
            ROOT / "rebuild" / "m1-divergences.yaml",
            ROOT / "rebuild" / "script.yaml",
            ROOT / "rebuild" / "standing-approvals.yaml",
        ],
    }
    out: dict[str, object] = {"lever": "yaml.CSafeLoader", "with_libyaml": yaml.__with_libyaml__}
    rows = []
    for label, paths in corpora.items():
        texts = corpus_texts(paths)
        pure = bench(lambda: [list(yaml.load_all(t, Loader=yaml.SafeLoader)) for t in texts], reps)
        fast = bench(lambda: [list(yaml.load_all(t, Loader=yaml.CSafeLoader)) for t in texts], reps)
        equal = all(
            list(yaml.load_all(t, Loader=yaml.SafeLoader)) == list(yaml.load_all(t, Loader=yaml.CSafeLoader))
            for t in texts
        )
        rows.append(
            {
                "corpus": label,
                "files": len(paths),
                "bytes": sum(len(t.encode()) for t in texts),
                "pure_python_s": round(pure, 6),
                "libyaml_s": round(fast, 6),
                "saved_s": round(pure - fast, 6),
                "speedup": round(pure / fast, 2),
                "parses_equal": equal,
            }
        )
    out["corpora"] = rows

    # The skip path: fingerprint.rune_file_digest over every rune file, as-shipped
    # against a CSafeLoader swap. Equivalence is the digest itself.
    rune_paths = sorted((ROOT / "glyph_data" / "runes").glob("*.yaml"))
    shipped = bench(lambda: [fingerprint.rune_file_digest(p) for p in rune_paths], reps)
    shipped_digests = [fingerprint.rune_file_digest(p) for p in rune_paths]

    def fast_digest(path: Path) -> str:
        raw = path.read_bytes()
        try:
            payload = json.dumps(
                fingerprint._projected_rune(yaml.load(raw.decode(), Loader=yaml.CSafeLoader)),
                ensure_ascii=False,
            )
        except yaml.YAMLError, UnicodeDecodeError, TypeError, ValueError:
            return hashlib.sha256(raw).hexdigest()
        return hashlib.sha256(payload.encode()).hexdigest()

    swapped = bench(lambda: [fast_digest(p) for p in rune_paths], reps)
    swapped_digests = [fast_digest(p) for p in rune_paths]

    out["rune_file_digest"] = {
        "shipped_s": round(shipped, 6),
        "cloader_s": round(swapped, 6),
        "saved_s": round(shipped - swapped, 6),
        "speedup": round(shipped / swapped, 2),
        "digests_identical": shipped_digests == swapped_digests,
        "digest_of_digests": hashlib.sha256("".join(shipped_digests).encode()).hexdigest()[:16],
    }

    # spec_load parses each rune file TWICE: yaml.compose for the line index and
    # yaml.safe_load for the data. Both are swappable.
    texts = [p.read_text() for p in rune_paths]
    compose_pure = bench(lambda: [yaml.compose(t, Loader=yaml.SafeLoader) for t in texts], reps)
    compose_fast = bench(lambda: [yaml.compose(t, Loader=yaml.CSafeLoader) for t in texts], reps)
    out["spec_load_compose"] = {
        "pure_python_s": round(compose_pure, 6),
        "libyaml_s": round(compose_fast, 6),
        "saved_s": round(compose_pure - compose_fast, 6),
        "speedup": round(compose_pure / compose_fast, 2),
    }
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
