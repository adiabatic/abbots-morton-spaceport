"""Time gate:conform's six-config sweep at a given --conform-horizon, against an isolated, self-consistent (spec, tables, M1.otf) triple.

Faithful to run_m1.run_font_conformance: the same `conform.conformance_config_worker` per config in a spawn pool, the same glyph inventory minted from the serialized window enumeration, the same inherited boundary-gate horizon. One deliberate difference, reported: the out dir is a scratch copy, so nothing in rebuild/out is read as input or written.

Per config it records the shaping clock by watching Shaper.shape, whose call count is the whole sweep now that the belt shapes only its exhaustive enumeration.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rebuild.pipeline import conform, run_m1


def timed_worker(spec, font_path, config, horizon, glyphs, boundary_horizon):
    """conformance_config_worker plus a shape-level clock. Patching Shaper.shape here (in the child, before the sweep starts) leaves the swept computation untouched; the counters ride module state and come back in the payload."""
    stats = {"calls": 0, "hb_s": 0.0, "setup_at": None}
    original = conform.Shaper.shape
    t0 = time.perf_counter()

    def counting_shape(self, text, features):
        if stats["setup_at"] is None:
            stats["setup_at"] = time.perf_counter() - t0
        begin = time.perf_counter()
        out = original(self, text, features)
        stats["hb_s"] += time.perf_counter() - begin
        stats["calls"] += 1
        return out

    conform.Shaper.shape = counting_shape
    try:
        result = conform.conformance_config_worker(
            spec, font_path, config, horizon, glyphs, boundary_horizon=boundary_horizon
        )
    finally:
        conform.Shaper.shape = original
    wall = time.perf_counter() - t0
    setup = stats["setup_at"] or 0.0
    return {
        "config": config,
        "wall_s": wall,
        "setup_s": setup,
        "sweep_only_s": wall - setup,
        "shape_calls": stats["calls"],
        "shape_s": stats["hb_s"],
        "sequences": result.sequences,
        "shaping_runs": result.shaping_runs,
        "divergences": len(result.divergences),
        "cpu_s": None,
    }


def main() -> None:
    out_dir = Path(sys.argv[1]).resolve()
    horizon = int(sys.argv[2])
    report_dir = Path(sys.argv[3]).resolve()
    boundary = sys.argv[4] == "boundary-green"
    report_dir.mkdir(parents=True, exist_ok=True)

    inputs = run_m1.tables_inputs()
    spec = run_m1.load_default_spec()
    serialized = run_m1.serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(f"{out_dir} holds no window enumeration matching the runes on disk")
    cell_glyphs = run_m1.mint_cell_glyphs(spec, serialized)

    start = time.perf_counter()
    ctx = multiprocessing.get_context("spawn")
    results = {}
    with ProcessPoolExecutor(max_workers=len(conform.ACCEPTANCE_CONFIGS), mp_context=ctx) as pool:
        futures = {
            pool.submit(
                timed_worker,
                spec,
                out_dir / "M1.otf",
                config,
                horizon,
                cell_glyphs,
                horizon if boundary else None,
            ): config
            for config in conform.ACCEPTANCE_CONFIGS
        }
        for future in as_completed(futures):
            payload = future.result()
            results[payload["config"]] = payload
            print(f"[t] {payload['config']} {payload['wall_s']:.1f}s", flush=True)
    wall = time.perf_counter() - start

    ordered = [results[c] for c in conform.ACCEPTANCE_CONFIGS]
    summary = {
        "horizon": horizon,
        "boundary_horizon_supplied": boundary,
        "pool_wall_s": wall,
        "sum_config_wall_s": sum(r["wall_s"] for r in ordered),
        "sum_setup_s": sum(r["setup_s"] for r in ordered),
        "sum_sweep_only_s": sum(r["sweep_only_s"] for r in ordered),
        "sum_shape_s": sum(r["shape_s"] for r in ordered),
        "total_shape_calls": sum(r["shape_calls"] for r in ordered),
        "sequences_per_config": ordered[0]["sequences"],
        "total_shaping_runs": sum(r["shaping_runs"] for r in ordered),
        "total_divergences": sum(r["divergences"] for r in ordered),
        "configs": ordered,
    }
    summary["gate_passes"] = summary["total_divergences"] == 0
    print(json.dumps(summary, indent=2), flush=True)
    Path(os.environ.get("SWEEP_JSON", report_dir / f"sweep-h{horizon}.json")).write_text(
        json.dumps(summary, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
