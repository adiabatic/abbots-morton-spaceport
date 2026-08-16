import json, os, sys, time

t0 = time.perf_counter()
c0 = time.process_time()
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T
from rebuild.pipeline import settle as S
from rebuild.tools.peak_rss import peak_rss_self_bytes

spec = load_default_spec()
t_load = time.perf_counter() - t0

counters = {}
if os.environ.get("K1_COUNT") == "1":
    for cls, names in ((S.Engine, ("candidates", "_prospect", "transition_trace", "_prefer_favors")),):
        for n in names:
            orig = getattr(cls, n)
            counters[n] = [0]

            def mk(orig=orig, box=counters[n]):
                def wrapper(*a, **k):
                    box[0] += 1
                    return orig(*a, **k)

                return wrapper

            setattr(cls, n, mk())

features = frozenset()
t1 = time.perf_counter()
c1 = time.process_time()
decision, treaty = T.build_tables(spec, features)
t2 = time.perf_counter()
c2 = time.process_time()
out = {
    "spec_load_wall": t_load,
    "build_tables_wall": t2 - t1,
    "build_tables_cpu": c2 - c1,
    "n_windows": len(decision.transitions),
    "n_rules": len(decision.rules),
    "n_treaty": len(treaty.rows),
    "n_cells": len(decision.reachable_cells()),
    "windows_digest": T.windows_digest(decision),
    "peak_rss_bytes": peak_rss_self_bytes(),
    "counters": {k: v[0] for k, v in counters.items()},
}
if not out["counters"]:
    import pathlib

    side = pathlib.Path(__file__).with_name("real-kernel-counters.json")
    if side.exists():
        out["counters"] = json.loads(side.read_text())
        out["counters_source"] = (
            "separate instrumented pass (K1_COUNT=1); wrapping the four kernel methods costs ~8% so the timing pass runs uninstrumented"
        )
print(json.dumps(out, indent=1))
