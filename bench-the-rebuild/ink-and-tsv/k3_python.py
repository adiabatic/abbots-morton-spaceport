"""K3 — placed-ink layer. Python baselines, the marshal-digest variant, and the binary export for the ports.

Every timed Python figure here calls the repo's own `rebuild.review.ink` code: `InkComparator.signature`,
`InkComparator.config_diff`, `translate_outline`, `signature_digest`, `delta_digest`. The only substitution
is the Shaper (pre-extracted runs stand in for HarfBuzz) and the OutlineCache's fill (pre-extracted
outlines stand in for fontTools), which is exactly the boundary the cost model draws around K3.
"""

from __future__ import annotations

import copy
import hashlib
import json
import marshal
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import k3_common  # noqa: E402

from rebuild.review.ink import delta_digest, signature_digest, translate_outline  # noqa: E402

HERE = Path(__file__).resolve().parent


def marshal_signature_digest(signature: tuple) -> str:
    """Direct binary hashing instead of repr-then-hash. marshal version 2 is used deliberately: versions
    >= 3 add a back-reference table keyed on object *identity*, so two equal-by-value signatures built
    with different aliasing could serialize to different bytes. Version 2 has no ref table and is a pure
    value encoding, and marshal's encoding is self-delimiting (type tag + length for every node), so it is
    injective over the tuple/str/int/None graph a signature is — the digest inherits sha256's collision
    resistance and nothing weaker."""
    return hashlib.sha256(marshal.dumps(signature, 2)).hexdigest()


def marshal4_signature_digest(signature: tuple) -> str:
    return hashlib.sha256(marshal.dumps(signature, 4)).hexdigest()


def _timeit(fn, reps=1):
    best = None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn()
        elapsed = time.perf_counter() - t
        best = elapsed if best is None else min(best, elapsed)
    return best, out


def full_pass(comparator, work, digest_fn):
    acc = hashlib.sha256()
    nonempty = 0
    for unit, text, config in work:
        sig = comparator.signature(text, config)
        sd = digest_fn(sig)
        diff = comparator.config_diff(text, config)
        dd = delta_digest(diff)
        if diff != ((), (), 0):
            nonempty += 1
        acc.update(f"{unit}\t{config}\t{sd}\t{dd}\n".encode())
    return acc.hexdigest(), nonempty


def signature_only_pass(comparator, work):
    acc = 0
    for _unit, text, config in work:
        acc += len(comparator.signature(text, config))
    return acc


def diff_only_pass(comparator, work):
    acc = 0
    for _unit, text, config in work:
        acc += len(comparator.config_diff(text, config))
    return acc


def export_binary(before_outlines, after_outlines, rows, path: Path) -> None:
    # Operator indices are assigned in lexicographic name order, so an index comparison in the ports is
    # exactly Python's string comparison of the operator names inside a piece sort.
    names = sorted({op for table in (before_outlines, after_outlines) for v in table.values() for op, _ in v})
    ops: dict[str, int] = {name: index for index, name in enumerate(names)}
    out = bytearray(b"K3B1")
    out += struct.pack("<I", len(ops))
    for name in names:
        raw = name.encode()
        out += struct.pack("<I", len(raw)) + raw
    out += struct.pack("<I", 2)
    for table in (before_outlines, after_outlines):
        out += struct.pack("<I", len(table))
        for name, value in table.items():
            raw = name.encode()
            out += struct.pack("<I", len(raw)) + raw
            out += struct.pack("<I", len(value))
            for operator, points in value:
                out += struct.pack("<II", ops[operator], len(points))
                for point in points:
                    if point is None:
                        out += struct.pack("<Bii", 1, 0, 0)
                    else:
                        out += struct.pack("<Bii", 0, point[0], point[1])
    out += struct.pack("<I", len(rows))
    for row in rows:
        text = "".join(chr(cp) for cp in row["cps"]).encode("utf-8")
        unit = row["unit"].encode()
        config = row["config"].encode()
        out += struct.pack("<I", len(unit)) + unit
        out += struct.pack("<I", len(config)) + config
        out += struct.pack("<I", len(text)) + text
        for side in ("before", "after"):
            entries = row[side]
            out += struct.pack("<I", len(entries))
            for name, xo, yo, xa in entries:
                raw = name.encode()
                out += struct.pack("<I", len(raw)) + raw
                out += struct.pack("<iii", xo, yo, xa)
    path.write_bytes(bytes(out))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "bench"
    comparator, work, before_outlines, after_outlines, rows = k3_common.build_comparator()

    if mode == "export":
        export_binary(before_outlines, after_outlines, rows, HERE / "k3-input.bin")
        checksum, nonempty = full_pass(comparator, work, signature_digest)
        (HERE / "k3-reference.json").write_text(
            json.dumps({"rows": len(work), "checksum": checksum, "nonempty_deltas": nonempty}, indent=2)
            + "\n"
        )
        print(json.dumps({"exported": len(rows), "checksum": checksum}))
        return

    result: dict = {"rows": len(work)}

    # --- equivalence / safety of the marshal digest -------------------------------------------------
    sigs = [comparator.signature(text, config) for _unit, text, config in work]
    by_repr: dict[str, set] = {}
    by_marshal: dict[str, set] = {}
    for index, sig in enumerate(sigs):
        by_repr.setdefault(signature_digest(sig), set()).add(index)
        by_marshal.setdefault(marshal_signature_digest(sig), set()).add(index)
    partitions_match = sorted(map(sorted, by_repr.values())) == sorted(map(sorted, by_marshal.values()))
    # Value-equal signatures must serialize identically no matter how they were built.
    fresh_stable = all(marshal.dumps(s, 2) == marshal.dumps(copy.deepcopy(s), 2) for s in sigs[:500])
    groups = {}
    for index, sig in enumerate(sigs):
        groups.setdefault(repr(sig), []).append(index)
    value_equal_same_bytes = all(
        len({marshal.dumps(sigs[i], 2) for i in members}) == 1 for members in groups.values()
    )
    result["marshal_digest_check"] = {
        "partition_identical_to_repr_digest": partitions_match,
        "distinct_signature_values": len(groups),
        "distinct_repr_digests": len(by_repr),
        "distinct_marshal_digests": len(by_marshal),
        "value_equal_signatures_serialize_identically": value_equal_same_bytes,
        "stable_under_alias_breaking_copy": fresh_stable,
    }

    # --- micro: translate_outline -------------------------------------------------------------------
    outlines = [value for value in before_outlines.values() if value]
    points = sum(len(pts) for value in outlines for _op, pts in value)
    reps = max(1, 400000 // max(points, 1))
    sink = 0
    elapsed = None
    for _ in range(5):
        t = time.perf_counter()
        for _ in range(reps):
            for value in outlines:
                sink += len(translate_outline(value, 3, 5))
        span = time.perf_counter() - t
        elapsed = span if elapsed is None else min(elapsed, span)
    result["translate_outline"] = {
        "calls": len(outlines) * reps,
        "points": points * reps,
        "us_per_call": elapsed / (len(outlines) * reps) * 1e6,
        "ns_per_point": elapsed / (points * reps) * 1e9,
        "sink": sink,
    }

    # --- micro: signature_digest split --------------------------------------------------------------
    sample = sigs[:600]
    blobs = [repr(sig).encode() for sig in sample]

    def micro(fn, items):
        best = None
        for _ in range(3):
            t = time.perf_counter()
            for item in items:
                fn(item)
            elapsed = time.perf_counter() - t
            best = elapsed if best is None else min(best, elapsed)
        return best / len(items) * 1e6

    repr_us = micro(repr, sample)
    sha_us = micro(lambda b: hashlib.sha256(b).hexdigest(), blobs)
    total_us = micro(signature_digest, sample)
    marshal_us = micro(lambda s: marshal.dumps(s, 2), sample)
    marshal_total_us = micro(marshal_signature_digest, sample)
    marshal4_total_us = micro(marshal4_signature_digest, sample)
    result["signature_digest_split"] = {
        "repr_us_per_row": repr_us,
        "sha256_us_per_row": sha_us,
        "repr_then_sha256_us_per_row": total_us,
        "marshal2_us_per_row": marshal_us,
        "marshal2_then_sha256_us_per_row": marshal_total_us,
        "marshal4_then_sha256_us_per_row": marshal4_total_us,
        "speedup_marshal2": total_us / marshal_total_us,
        "speedup_marshal4": total_us / marshal4_total_us,
        "mean_repr_bytes": sum(len(b) for b in blobs) / len(blobs),
        "mean_marshal2_bytes": sum(len(marshal.dumps(s, 2)) for s in sample) / len(sample),
    }
    del sigs, sample, blobs, groups, by_repr, by_marshal

    # --- full passes --------------------------------------------------------------------------------
    elapsed, (checksum, nonempty) = _timeit(lambda: full_pass(comparator, work, signature_digest), reps=7)
    result["full_pass_baseline"] = {
        "seconds": elapsed,
        "us_per_row": elapsed / len(work) * 1e6,
        "checksum": checksum,
        "nonempty_deltas": nonempty,
    }
    elapsed_m, (checksum_m, _) = _timeit(
        lambda: full_pass(comparator, work, marshal_signature_digest), reps=7
    )
    result["full_pass_marshal_digest"] = {
        "seconds": elapsed_m,
        "us_per_row": elapsed_m / len(work) * 1e6,
        "checksum_differs_by_design": checksum_m != checksum,
        "speedup_vs_baseline": elapsed / elapsed_m,
    }
    # --- fidelity: the same pass driven by the live fonts through HarfBuzz + fontTools ----------------
    # The ports take pre-shaped runs, which is the boundary the cost model draws around K3. This
    # measures what that boundary costs: same 3,000 rows, same checksum, but every glyph run comes
    # from uharfbuzz and every outline from a DecomposingRecordingPen over the shipped fonts.
    before_font = HERE.parents[1] / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
    after_font = HERE.parents[1] / "rebuild" / "out" / "m1" / "M1.otf"
    if before_font.exists() and after_font.exists():
        from rebuild.review.ink import InkComparator

        live = InkComparator(before_font, after_font)
        full_pass(live, work[:50], signature_digest)  # warm the outline caches
        elapsed_live, (checksum_live, _) = _timeit(lambda: full_pass(live, work, signature_digest), reps=3)
        result["live_font_pass"] = {
            "seconds": elapsed_live,
            "us_per_row": elapsed_live / len(work) * 1e6,
            "checksum": checksum_live,
            "checksum_matches_pre_extracted_corpus": checksum_live == checksum,
            "shaping_and_outline_overhead_seconds": elapsed_live - elapsed,
            "fraction_of_live_pass_the_ports_replace": elapsed / elapsed_live,
            "before_font": str(before_font.relative_to(HERE.parents[1])),
            "after_font": str(after_font.relative_to(HERE.parents[1])),
            "note": (
                "InkComparator's default plain Shaper, so four hb.shape calls per row (two per side, "
                "one for signature and one for config_diff). The surface build passes shaper_for, whose "
                "memo collapses those to two; see shaping_only for the per-row cost of those two."
            ),
        }
        del live

        # The irreducible native floor under K3: hb.shape itself, two calls per row (before + after)
        # once the surface build's shaper memo has collapsed the duplicate calls.
        from rebuild.review.ink import features_for, kern_neutral
        from rebuild.validation.shaping import Shaper

        shapers = {"before": Shaper(before_font), "after": Shaper(after_font)}
        pairs = [(text, kern_neutral(features_for(config))) for _unit, text, config in work]

        def shape_pass():
            total = 0
            for text, features in pairs:
                total += len(shapers["before"].shape(text, features).names)
                total += len(shapers["after"].shape(text, features).names)
            return total

        elapsed_shape, glyphs = _timeit(shape_pass, reps=5)
        result["shaping_only"] = {
            "hb_shape_calls": len(pairs) * 2,
            "seconds": elapsed_shape,
            "us_per_row": elapsed_shape / len(work) * 1e6,
            "glyphs": glyphs,
            "note": "uharfbuzz, not rewritable — the cost model's largest irreducible floor",
        }
        del shapers, pairs

    elapsed_s, _ = _timeit(lambda: signature_only_pass(comparator, work), reps=7)
    elapsed_d, _ = _timeit(lambda: diff_only_pass(comparator, work), reps=7)
    result["stage_split"] = {
        "signature_only_us_per_row": elapsed_s / len(work) * 1e6,
        "config_diff_only_us_per_row": elapsed_d / len(work) * 1e6,
    }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
