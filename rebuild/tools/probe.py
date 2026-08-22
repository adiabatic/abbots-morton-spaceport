"""Probe a codepoint window: old-font baseline (glyphs+seams, all configs) vs new settlement.
Usage: PYTHONPATH=. uv run python rebuild/tools/probe.py E653:E666:E652
"""

import gzip
import sys
from collections.abc import Sequence
from pathlib import Path

from rebuild.pipeline import conform
from rebuild.pipeline.explain import explain_many
from rebuild.pipeline.run_m1 import OUT_DIR
from rebuild.pipeline.spec_load import load_default_spec

CONFIGS = ["default", "ss03", "ss05", "ss03+ss05", "ss04", "ss10"]


def load_subset(cfg):
    rows = {}
    p = OUT_DIR / f"baseline-{cfg}.subset.tsv.gz"
    with gzip.open(p, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            rows[parts[0]] = parts
    return rows


def main(argv: Sequence[str] | None = None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    cps_str = arguments[0].upper()
    cps = [int(x, 16) for x in cps_str.split(":")]
    spec = load_default_spec()
    features = [conform.features_for_config(config) for config in CONFIGS]
    reports = explain_many(spec, [(cps, active) for active in features])
    print(f"=== window {cps_str} ===")
    for cfg, report in zip(CONFIGS, reports):
        sub = load_subset(cfg)
        # baseline
        b = sub.get(cps_str)
        bg = b[1] if b else "(not in subset)"
        bs = b[3] if b else ""
        # new settlement
        settled = report.settled
        cells = []
        seams = []
        for i, it in enumerate(settled):
            c = getattr(it, "cell", None)
            if c is not None and hasattr(c, "rune"):
                cells.append(f"{c.rune}.{c.stance}/en={c.entry}/ex={c.exit}/{'+'.join(c.adjustments)}")
            else:
                cells.append(getattr(it, "glyph_name", str(it)))
            if i < len(settled) - 1:
                sm = getattr(it, "seam", None)
                seams.append(
                    "break"
                    if sm is None
                    else (f"y{sm}" if isinstance(sm, int) else f"y{spec.registry.y_of(sm)}")
                )
        print(f"\n[{cfg}]")
        print(f"  OLD glyphs: {bg}")
        print(f"  OLD seams : {bs}")
        print(f"  NEW cells : {' | '.join(cells)}")
        print(f"  NEW seams : {','.join(seams)}")


if __name__ == "__main__":
    main()
