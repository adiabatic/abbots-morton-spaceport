"""Both sides of the calt sweep's depth-2 question, measured identically so they can be compared.

For each pair the sweeps guard, and for each side (prefix / suffix), collect the set of renderings OF THE PAIR ITSELF (the shaped glyphs whose HarfBuzz clusters fall inside the pair's two input codepoints) reachable when that side is swept to depth <= 1, and again when it is swept to depth 2, with the opposite side held at its full depth-<=1 sweep. A rendering in the depth-2 set and not the depth-<=1 set is a state `max_chars_* = 1` could never visit.
"""

from __future__ import annotations

import json
import sys
import time
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "test"))

from after2_coverage import PAIRS
from quikscript_shaping_helpers import _context_chars, _qs_text, _shape_with_clusters


def pair_render(prefix: str, pair: str, suffix: str) -> tuple[str, ...]:
    names, clusters = _shape_with_clusters(prefix + pair + suffix)
    start, stop = len(prefix), len(prefix) + len(pair)
    return tuple(name for name, cluster in zip(names, clusters) if start <= cluster < stop)


def main() -> None:
    context = tuple(c for _, c in _context_chars())
    depth01 = ("",) + context
    depth2 = tuple("".join(combo) for combo in product(context, repeat=2))
    report = {}
    started = time.perf_counter()
    for left, right in PAIRS:
        pair = _qs_text(left, right)
        entry = {}
        # suffix side: prefixes held at depth <= 1
        shallow = {pair_render(p, pair, s) for p in depth01 for s in depth01}
        deeper = {pair_render(p, pair, s) for p in depth01 for s in depth2}
        entry["suffix"] = {
            "reachable_depth_le_1": len(shallow),
            "reachable_depth_2": len(deeper),
            "only_at_depth_2": len(deeper - shallow),
            "examples": [list(x) for x in sorted(deeper - shallow)[:2]],
        }
        # prefix side: suffixes held at depth <= 1
        deeper_p = {pair_render(p, pair, s) for p in depth2 for s in depth01}
        entry["prefix"] = {
            "reachable_depth_le_1": len(shallow),
            "reachable_depth_2": len(deeper_p),
            "only_at_depth_2": len(deeper_p - shallow),
            "examples": [list(x) for x in sorted(deeper_p - shallow)[:2]],
        }
        report[f"{left}+{right}"] = entry
    out = {
        "elapsed_s": time.perf_counter() - started,
        "totals": {
            "suffix_only_at_depth_2": sum(v["suffix"]["only_at_depth_2"] for v in report.values()),
            "prefix_only_at_depth_2": sum(v["prefix"]["only_at_depth_2"] for v in report.values()),
            "reachable_depth_le_1": sum(v["suffix"]["reachable_depth_le_1"] for v in report.values()),
        },
        "by_pair": report,
    }
    print(json.dumps(out, indent=2))
    Path(__file__).with_name("depth2-states.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
