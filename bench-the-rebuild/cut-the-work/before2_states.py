"""The mirror of after2_states: how many renderings of a guarded pair are reachable ONLY with a two-character PREFIX?

`max_chars_before=2` is what forces the 46-way shard split in test/test_calt_regressions.py, so it costs as much as `max_chars_after=2` does. This measures whether it buys as much: for each pair, the set of pair renderings reachable under every prefix of length 0 or 1, against the set reachable under every length-2 prefix. Suffix is held at the length-0 and length-1 cases so the pair still sees a right context.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "test"))

from after2_coverage import PAIRS
from quikscript_shaping_helpers import _context_chars, _qs_text, _shape_with_clusters


def pair_render(text: str, start: int, stop: int) -> tuple[str, ...]:
    names, clusters = _shape_with_clusters(text)
    return tuple(name for name, cluster in zip(names, clusters) if start <= cluster < stop)


def main() -> None:
    context = _context_chars()
    suffixes = [""] + [c for _, c in context]
    report = {}
    total_shallow = 0
    total_deep_only = 0
    started = time.perf_counter()
    for left, right in PAIRS:
        pair_text = _qs_text(left, right)
        shallow: set = set()
        deep: set = set()
        for suffix in suffixes:
            for prefix in [""] + [c for _, c in context]:
                shallow.add(
                    pair_render(prefix + pair_text + suffix, len(prefix), len(prefix) + len(pair_text))
                )
            for _, p1 in context:
                for _, p2 in context:
                    prefix = p1 + p2
                    deep.add(
                        pair_render(prefix + pair_text + suffix, len(prefix), len(prefix) + len(pair_text))
                    )
        only_deep = deep - shallow
        total_shallow += len(shallow)
        total_deep_only += len(only_deep)
        report[f"{left}+{right}"] = {
            "renderings_reachable_with_prefix_le_1": len(shallow),
            "renderings_reachable_with_prefix_2": len(deep),
            "renderings_ONLY_at_prefix_2": len(only_deep),
            "examples": [list(item) for item in sorted(only_deep)[:3]],
        }
    out = {
        "elapsed_s": time.perf_counter() - started,
        "totals": {
            "renderings_reachable_with_prefix_le_1": total_shallow,
            "renderings_ONLY_at_prefix_2": total_deep_only,
        },
        "by_pair": report,
    }
    print(json.dumps(out, indent=2))
    Path(__file__).with_name("before2-states.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
