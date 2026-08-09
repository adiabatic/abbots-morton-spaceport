"""Build a self-consistent (spec, decision tables, M1.otf) triple at HEAD into an isolated out dir, so the conform-horizon sweep can be timed without touching rebuild/out/m1."""

import json
import sys
import time
from pathlib import Path

from rebuild.pipeline import run_m1

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)

start = time.perf_counter()
inputs = run_m1.tables_inputs()
summary = run_m1.run(out_dir=OUT, inputs=inputs)
elapsed = time.perf_counter() - start
print(f"[T] run_total {elapsed:.1f}s", flush=True)
(OUT / "build_wall.json").write_text(json.dumps({"run_total_s": elapsed, "inputs": inputs}, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k != "notes"}, indent=2)[:2000])
