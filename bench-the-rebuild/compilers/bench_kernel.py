"""Settlement-kernel benchmark: run `table.build_tables` over a fixed subset of the real ResolvedSpec and checksum the full result.

The subset exists only to bring one build under ten seconds; every code path the full six-config stage walks is walked here (fixpoint over reachable left states, deep-slot liveness filters, formation guard with a real ligature, outcome-partition compression, treaty fold). The checksum covers every field of every emitted window, rule, reachable cell and treaty row, so an accelerator that changes the artifact cannot pass.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def load_peak_rss():
    """The live tree's peak-RSS helper, loaded by path rather than imported as `rebuild.tools.peak_rss`: this process runs against whichever accelerator tree `PYTHONPATH` names, and putting the repo root on `sys.path` for the sake of the yardstick would leak a second `rebuild` into the import closure the run is measuring."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "rebuild" / "tools" / "peak_rss.py"
    spec = importlib.util.spec_from_file_location("ams_peak_rss", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


peak_rss = load_peak_rss()

SUBSETS = {
    # 9 runes, one of them a real ligature (qsSee_qsUtter), so the section 5.7 late-formation guard fires.
    "k9": [
        "qsAh",
        "qsDay",
        "qsFee",
        "qsIt",
        "qsMay",
        "qsPea",
        "qsSee",
        "qsSee_qsUtter",
        "qsUtter",
    ],
    "k11": [
        "qsAh",
        "qsDay",
        "qsFee",
        "qsIt",
        "qsMay",
        "qsPea",
        "qsSee",
        "qsSee_qsUtter",
        "qsUtter",
        "qsRoe",
        "qsTea",
    ],
    "full": None,
}


def checksum(decision, treaty) -> dict:
    h = hashlib.sha256()
    for w in decision.transitions:
        h.update(
            "\t".join((w.input_glyph, w.left, w.right1, w.right2, w.right3, w.right4, w.outcome)).encode()
        )
        h.update(b"\n")
    windows_sha = h.hexdigest()

    r = hashlib.sha256()
    for rule in decision.rules:
        parts = [rule.input_glyph]
        for slot in (rule.backtrack, rule.look1, rule.look2, rule.look3, rule.look4):
            parts.append("#NONE" if slot is None else ",".join(slot))
        parts.append(rule.outcome)
        parts.append(",".join(rule.provenance))
        parts.append("1" if rule.joint else "0")
        r.update("\t".join(parts).encode())
        r.update(b"\n")
    rules_sha = r.hexdigest()

    c = hashlib.sha256()
    for cell in sorted(str(x) for x in decision.reachable_cells()):
        c.update(cell.encode())
        c.update(b"\n")
    cells_sha = c.hexdigest()

    t = hashlib.sha256()
    for row in treaty.rows:
        t.update("\t".join((row.left, row.right, row.junction, str(row.extension), str(row.kern))).encode())
        t.update(b"\n")
    treaty_sha = t.hexdigest()

    prov = hashlib.sha256()
    for p in sorted(decision.cited_provenance):
        prov.update(p.encode())
        prov.update(b"\n")

    combined = hashlib.sha256()
    for part in (windows_sha, rules_sha, cells_sha, treaty_sha, prov.hexdigest()):
        combined.update(part.encode())

    return {
        "config": decision.config,
        "n_windows": len(decision.transitions),
        "n_rules": len(decision.rules),
        "n_cells": len(decision.reachable_cells()),
        "n_treaty_rows": len(treaty.rows),
        "n_identity_guard_rules": decision.identity_guard_rules,
        "n_cited_provenance": len(decision.cited_provenance),
        "windows_sha256": windows_sha,
        "rules_sha256": rules_sha,
        "cells_sha256": cells_sha,
        "treaty_sha256": treaty_sha,
        "combined_sha256": combined.hexdigest(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--subset", default="k9", choices=sorted(SUBSETS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    t_import0 = time.perf_counter()
    from rebuild.pipeline import model as model_mod
    from rebuild.pipeline import settle as settle_mod
    from rebuild.pipeline import table as table_mod
    from rebuild.pipeline.spec_load import load_default_spec

    t_import = time.perf_counter() - t_import0

    def native(mod) -> bool:
        f = getattr(mod, "__file__", "") or ""
        return f.endswith(".so") or f.endswith(".pyd")

    t_spec0 = time.perf_counter()
    spec = load_default_spec()
    t_spec = time.perf_counter() - t_spec0

    keep = SUBSETS[args.subset]
    if keep is not None:
        spec = dataclasses.replace(spec, runes={k: v for k, v in spec.runes.items() if k in keep})

    gc_off = os.environ.get("AMS_BENCH_GC") == "off"
    if gc_off:
        gc.collect()
        if hasattr(gc, "freeze"):
            gc.freeze()
        gc.disable()

    reps = []
    result = None
    for _ in range(args.reps):
        t0 = time.perf_counter()
        c0 = time.process_time()
        decision, treaty = table_mod.build_tables(spec, frozenset())
        wall = time.perf_counter() - t0
        cpu = time.process_time() - c0
        result = checksum(decision, treaty)
        reps.append({"wall_s": wall, "cpu_s": cpu})
        del decision, treaty

    peak_rss_mb = peak_rss.peak_rss_self_bytes() / (1024 * 1024)

    payload = {
        "label": args.label,
        "subset": args.subset,
        "n_runes": len(spec.runes),
        "python": sys.version.split()[0],
        "implementation": sys.implementation.name,
        "import_s": t_import,
        "spec_load_s": t_spec,
        "reps": reps,
        "best_wall_s": min(r["wall_s"] for r in reps),
        "median_wall_s": sorted(r["wall_s"] for r in reps)[len(reps) // 2],
        "peak_rss_mb": peak_rss_mb,
        "gc_disabled": gc_off,
        "native_modules": {
            "settle": native(settle_mod),
            "table": native(table_mod),
            "model": native(model_mod),
        },
        "module_files": {
            "settle": getattr(settle_mod, "__file__", None),
            "table": getattr(table_mod, "__file__", None),
            "model": getattr(model_mod, "__file__", None),
        },
        "result": result,
        "env": {
            "AMS_SIMULATED_PROSPECT": os.environ.get("AMS_SIMULATED_PROSPECT", "1"),
            "AMS_VOTE_SLOTS": os.environ.get("AMS_VOTE_SLOTS", "1"),
            "AMS_BENCH_GC": os.environ.get("AMS_BENCH_GC", "on"),
        },
    }
    text = json.dumps(payload, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
