"""How many distinct pair renderings are reachable ONLY with a two-character suffix?

Sharper than counting differing grid cells: for each guarded pair and each left context, collect the set of shaped renderings of the pair under every suffix of length 0 or 1, then under every suffix of length 2. A rendering in the second set and not the first is a state that `max_chars_after=1` can never visit at all — the precise coverage the 46x cut would surrender.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "test"))

from after2_coverage import PAIRS, head
from quikscript_shaping_helpers import _context_chars, _qs_text


def main() -> None:
    context = _context_chars()
    report = {}
    total_shallow = 0
    total_deep_only = 0
    for left, right in PAIRS:
        pair_text = _qs_text(left, right)
        shallow: set = set()
        deep: set = set()
        for before_name, before_char in [("EMPTY", "")] + [(n, c) for n, c in context]:
            cut = len(before_char) + len(pair_text)
            shallow.add(head(before_char + pair_text, cut))
            for _, a1 in context:
                shallow.add(head(before_char + pair_text + a1, cut))
                for _, a2 in context:
                    deep.add(head(before_char + pair_text + a1 + a2, cut))
        only_deep = deep - shallow
        total_shallow += len(shallow)
        total_deep_only += len(only_deep)
        report[f"{left}+{right}"] = {
            "renderings_reachable_with_suffix_le_1": len(shallow),
            "renderings_reachable_with_suffix_2": len(deep),
            "renderings_ONLY_at_suffix_2": len(only_deep),
            "examples": [list(item) for item in sorted(only_deep)[:3]],
        }
    out = {
        "totals": {
            "renderings_reachable_with_suffix_le_1": total_shallow,
            "renderings_ONLY_at_suffix_2": total_deep_only,
        },
        "by_pair": report,
    }
    print(json.dumps(out, indent=2))
    Path(__file__).with_name("after2-states.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
