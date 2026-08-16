"""Does the SECOND character after a pair ever change how the pair renders?

That is exactly the coverage `max_chars_after=2 -> 1` would give up in test/test_calt_regressions.py's sharded sweeps. For each of the 17 pairs those sweeps guard, this shapes `before + L + R + a1` and `before + L + R + a1 + a2` over the full 46 x 46 suffix grid and compares the glyph run covering everything up to and including the pair (by HarfBuzz cluster, so a ligature spanning the seam still lands on the pair side).

A difference means a real invariant lives at suffix depth 2 and a one-character suffix cannot see it. Zero differences across every pair would mean the second suffix character is provably inert for these pairs and the 46x is free.

Run: PYTHONPATH=test uv run python bench-the-rebuild/cut-the-work/after2_coverage.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "test"))

from quikscript_shaping_helpers import _context_chars, _qs_text, _shape_with_clusters

PAIRS = [
    ("qsIt", "qsDay"),
    ("qsUtter", "qsGay"),
    ("qsSee", "qsOut"),
    ("qsYe", "qsIt"),
    ("qsIt", "qsIt"),
    ("qsEat", "qsYe"),
    ("qsJay", "qsHe"),
    ("qsHe", "qsOwe"),
    ("qsYe", "qsOwe"),
    ("qsJay", "qsYe"),
    ("qsJai", "qsYe"),
    ("qsIt", "qsOwe"),
    ("qsIt", "qsCheer"),
    ("qsIt", "qsJai"),
    ("qsYe", "qsI"),
    ("qsIt", "qsI"),
]


def head(text: str, cut: int) -> tuple[str, ...]:
    """The shaped glyphs covering input codepoints [0, cut) — everything up to and including the pair."""
    names, clusters = _shape_with_clusters(text)
    return tuple(name for name, cluster in zip(names, clusters) if cluster < cut)


def main() -> None:
    context = _context_chars()
    rows = []
    started = time.perf_counter()
    for left, right in PAIRS:
        pair_text = _qs_text(left, right)
        for before_name, before_char in (("", ""), *[(n, c) for n, c in context[:0]]):
            pass
        # Two left contexts: the empty prefix and one saturating single prefix per context entry
        for before_name, before_char in [("EMPTY", "")] + [(n, c) for n, c in context]:
            cut = len(before_char) + len(pair_text)
            differing = 0
            total = 0
            examples = []
            for a1_name, a1 in context:
                base = head(before_char + pair_text + a1, cut)
                for a2_name, a2 in context:
                    total += 1
                    deep = head(before_char + pair_text + a1 + a2, cut)
                    if deep != base:
                        differing += 1
                        if len(examples) < 3:
                            examples.append(
                                {
                                    "before": before_name,
                                    "a1": a1_name,
                                    "a2": a2_name,
                                    "with_a2": list(deep),
                                    "without_a2": list(base),
                                }
                            )
            rows.append(
                {
                    "pair": f"{left}+{right}",
                    "before": before_name,
                    "suffix_grid": total,
                    "second_suffix_char_matters": differing,
                    "examples": examples,
                }
            )
    elapsed = time.perf_counter() - started
    by_pair: dict[str, dict] = {}
    for row in rows:
        entry = by_pair.setdefault(row["pair"], {"grid": 0, "matters": 0, "examples": []})
        entry["grid"] += row["suffix_grid"]
        entry["matters"] += row["second_suffix_char_matters"]
        if row["examples"] and len(entry["examples"]) < 3:
            entry["examples"].extend(row["examples"][: 3 - len(entry["examples"])])
    out = {
        "context_set": len(context),
        "left_contexts_per_pair": len(context) + 1,
        "elapsed_s": elapsed,
        "totals": {
            "grid": sum(v["grid"] for v in by_pair.values()),
            "matters": sum(v["matters"] for v in by_pair.values()),
        },
        "by_pair": by_pair,
    }
    print(json.dumps(out, indent=2))
    Path(__file__).with_name("after2-coverage.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
