"""Attribution run (b) of issue #51: where the default-config fixpoint's peak actually lives, at rungs heavy enough that the deep-slot probe machinery (`_ProspectLiveness`, `_DeepFiberDeriver`) carries production weight rather than the ~2% it shows at k=16. One rung per invocation, one process per rung — a high-water mark is process-lifetime, so stacking rungs in one process would charge every rung with its predecessors. The snapshot is taken after `build_tables` returns with the tables still referenced, which is the retention question: what a finished configuration leaves resident (the trace memo, the memo keys, the probe engines `_LIVENESS_PROBES` keeps) rather than what briefly lived mid-fixpoint.

Alongside the site table, the row records both peaks per window — the traced allocation peak (compression-blind) and the process high-water (what Darwin kept resident) — which is the instrument for the tracker's open tension between the 8.4 kB/window ladder extrapolation and the measured six-config process. Run untraced (AMS_ATTR_TRACE=0) for the clean high-water figure; a traced run's RSS carries the tracer's own bookkeeping.

GC is frozen and disabled unless AMS_SCALING_GC=on, matching `run_m1`'s entry and scaling.py's sweep.

Run as: uv run python bench-the-rebuild/ram/attr_fixpoint.py [k]   (defaults to the full alphabet)
"""

import gc
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

import attr_common
from rebuild.pipeline import table as T
from rebuild.pipeline.spec_load import load_default_spec


def nested_subset(spec, k: int):
    """scaling.py's deterministic nested subsets, letter for letter — ligature closure first, then alphabetical — so rung k here is the same sub-alphabet as rung k there and the rows read side by side."""
    names = sorted(spec.runes)
    base = []
    for name in (n for n in names if spec.runes[n].sequence):
        for part in spec.runes[name].sequence:
            if part not in base:
                base.append(part)
        if name not in base:
            base.append(name)
    for name in names:
        if name not in base:
            base.append(name)
    keep = set(base[:k])
    keep = {n for n in keep if not spec.runes[n].sequence or set(spec.runes[n].sequence) <= keep}
    return replace(spec, runes={n: r for n, r in spec.runes.items() if n in keep})


def main() -> None:
    spec = load_default_spec()
    k = int(sys.argv[1]) if len(sys.argv) > 1 else len(spec.runes)
    sub = nested_subset(spec, k)
    if os.environ.get("AMS_SCALING_GC", "off") != "on":
        gc.collect()
        gc.freeze()
        gc.disable()
    attr_common.start()
    start = time.perf_counter()
    decision, treaty = T.build_tables(sub, frozenset())
    wall = time.perf_counter() - start
    record, _ = attr_common.phase("build_tables")
    windows = len(decision.transitions)
    row = {
        "k": k,
        "runes": len(sub.runes),
        "letters": sum(1 for n in sub.runes if not sub.runes[n].sequence),
        "windows": windows,
        "rules": len(decision.rules),
        "treaty_rows": len(treaty.rows),
        "cells": len(decision.reachable_cells()),
        "wall_s": round(wall, 1),
        "gc": "on" if gc.isenabled() else "frozen",
        "deep_classes": getattr(T, "DEEP_CLASSES_DEFAULT", None),
        "rss_bytes_per_window": round(record["rss_high_water_bytes"] / max(1, windows), 1),
        **record,
    }
    if "traced_peak_bytes" in record:
        row["traced_peak_bytes_per_window"] = round(record["traced_peak_bytes"] / max(1, windows), 1)
    attr_common.print_summary(record)
    per_window = row.get("traced_peak_bytes_per_window", row["rss_bytes_per_window"])
    print(f"[attr] k={k} windows={windows} wall={row['wall_s']}s peak/window={per_window}B", flush=True)
    path = attr_common.write_result(f"attr-fixpoint-k{k}" + ("" if attr_common.TRACING else "-untraced"), row)
    print(f"[attr] wrote {path}", flush=True)


if __name__ == "__main__":
    main()
