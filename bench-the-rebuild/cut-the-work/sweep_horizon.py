"""Time gate:conform's six-config sweep at a given --conform-horizon, against an isolated, self-consistent (spec, tables, M1.otf) triple.

Faithful to run_m1.run_font_conformance: same `conform.conformance_config_worker` per config in a spawn pool, same decision tables read back from the serialized window enumeration, same witness top-ups. Differences, all deliberate and reported: the out dir is a scratch copy so nothing in rebuild/out is touched, and the witness cache is per-horizon so every horizon is measured cold unless --warm is passed.

Per config it records the sweep/top-up split by watching Shaper.shape: the sweep only ever shapes texts of length <= horizon, so the first longer text marks the boundary.
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
from rebuild.pipeline import table as table_module


def timed_worker(spec, font_path, config, horizon, glyphs, windows_path, boundary_horizon, cache_path):
    """conformance_config_worker plus a shape-level clock. Patching Shaper.shape here (in the child, before the sweep starts) leaves the swept computation untouched; the counters ride module state and come back in the payload."""
    stats = {"calls": 0, "hb_s": 0.0, "setup_at": None, "first_topup_at": None}
    original = conform.Shaper.shape
    t0 = time.perf_counter()

    def counting_shape(self, text, features):
        if stats["setup_at"] is None:
            stats["setup_at"] = time.perf_counter() - t0
        if len(text) > horizon and stats["first_topup_at"] is None:
            stats["first_topup_at"] = time.perf_counter() - t0
        begin = time.perf_counter()
        out = original(self, text, features)
        stats["hb_s"] += time.perf_counter() - begin
        stats["calls"] += 1
        return out

    conform.Shaper.shape = counting_shape
    try:
        result = conform.conformance_config_worker(
            spec,
            font_path,
            config,
            horizon,
            glyphs,
            decision=None,
            windows_path=windows_path,
            boundary_horizon=boundary_horizon,
            witness_cache_path=cache_path,
        )
    finally:
        conform.Shaper.shape = original
    wall = time.perf_counter() - t0
    setup = stats["setup_at"] or 0.0
    return {
        "config": config,
        "wall_s": wall,
        "setup_s": setup,
        "sweep_only_s": (
            (stats["first_topup_at"] - setup) if stats["first_topup_at"] is not None else (wall - setup)
        ),
        "sweep_s": stats["first_topup_at"] if stats["first_topup_at"] is not None else wall,
        "topup_s": (wall - stats["first_topup_at"]) if stats["first_topup_at"] is not None else 0.0,
        "shape_calls": stats["calls"],
        "shape_s": stats["hb_s"],
        "sequences": result.sequences,
        "topped_up_sequences": result.topped_up_sequences,
        "topped_up_rules": result.topped_up_rules,
        "shaping_runs": result.shaping_runs,
        "divergences": len(result.divergences),
        "uncovered_rules": result.uncovered_rules,
        "uncovered_transitions": result.uncovered_transitions,
        "cpu_s": None,
    }


def main() -> None:
    out_dir = Path(sys.argv[1]).resolve()
    horizon = int(sys.argv[2])
    cache_dir = Path(sys.argv[3]).resolve()
    boundary = sys.argv[4] == "boundary-green"
    cache_dir.mkdir(parents=True, exist_ok=True)

    inputs = run_m1.tables_inputs()
    spec = run_m1.load_default_spec()
    serialized = run_m1.serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(f"{out_dir} holds no window enumeration matching the runes on disk")
    windows = {config: table_module.windows_path(out_dir, config) for config in serialized}
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
                windows[config],
                horizon if boundary else None,
                cache_dir / f"witnesses-{config}.tsv.gz",
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
        "sum_sweep_s": sum(r["sweep_s"] for r in ordered),
        "sum_topup_s": sum(r["topup_s"] for r in ordered),
        "sum_shape_s": sum(r["shape_s"] for r in ordered),
        "total_shape_calls": sum(r["shape_calls"] for r in ordered),
        "sequences_per_config": ordered[0]["sequences"],
        "total_shaping_runs": sum(r["shaping_runs"] for r in ordered),
        "total_topped_up_sequences": sum(r["topped_up_sequences"] for r in ordered),
        "total_topped_up_rules": sum(r["topped_up_rules"] for r in ordered),
        "total_divergences": sum(r["divergences"] for r in ordered),
        "total_uncovered_rules": sum(r["uncovered_rules"] for r in ordered),
        "total_uncovered_transitions": sum(r["uncovered_transitions"] for r in ordered),
        "configs": ordered,
    }
    summary["gate_passes"] = (
        summary["total_divergences"] == 0
        and summary["total_uncovered_rules"] == 0
        and summary["total_uncovered_transitions"] == 0
    )
    print(json.dumps(summary, indent=2), flush=True)
    Path(os.environ.get("SWEEP_JSON", cache_dir / f"sweep-h{horizon}.json")).write_text(
        json.dumps(summary, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
