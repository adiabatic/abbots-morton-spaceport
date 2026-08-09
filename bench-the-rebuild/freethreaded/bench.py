"""One measurement of the settlement fixpoint. Prints one JSON object to stdout.

    bench.py <mode> <threads> <reps> <keep>

modes
  serial         one thread, one shared spec, units run back to back
  shared         `threads` threads over `units`, all reading ONE ResolvedSpec object graph
  own            `threads` threads over `units`, each thread holding a PRIVATE ResolvedSpec
  share-serial   the production shape: six configs serially over one live trace_memo.TraceShare
  share-fanout   the donor config alone, then the five recipients in parallel over that same share

`units` is the six acceptance configurations repeated `reps` times, so the thread sweep divides an
identical pile of work every way. Every mode returns the per-unit checksums; the runner compares
them against the GIL serial reference, so a configuration that scales by computing the wrong answer
is caught rather than reported.
"""

from __future__ import annotations

import gc
import json
import os
import resource
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kernel  # noqa: E402
from rebuild.pipeline import settle as settle_module  # noqa: E402
from rebuild.pipeline import table as table_module  # noqa: E402
from rebuild.pipeline import trace_memo  # noqa: E402

MODE = sys.argv[1]
NTHREADS = int(sys.argv[2])
REPS = int(sys.argv[3])
KEEP = int(sys.argv[4]) if len(sys.argv) > 4 else 9

# Count how often the two module-level LRU caches (cap 8 each) miss. Under threads these are the
# only mutable state the repo shares between concurrent fixpoints; a miss count above one per
# distinct spec / per distinct engine means concurrent builds are evicting each other's entries.
_counts = {"guard_fresh": 0, "probe_fresh": 0}
_clock = threading.Lock()
_orig_guard, _orig_probe = settle_module._guard_state, table_module._liveness_probe


def _counting_guard(spec):
    entry = settle_module._GUARD_STATES.get(id(spec))
    if not (entry is not None and entry[0] is spec):
        with _clock:
            _counts["guard_fresh"] += 1
    return _orig_guard(spec)


def _counting_probe(spec, engine):
    entry = table_module._LIVENESS_PROBES.get(id(engine))
    if not (entry is not None and entry[0] is engine):
        with _clock:
            _counts["probe_fresh"] += 1
    return _orig_probe(spec, engine)


settle_module._guard_state = _counting_guard
table_module._liveness_probe = _counting_probe


def cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


UNITS = list(kernel.CONFIGS) * REPS
_shared_spec = kernel.load(KEEP)
_local = threading.local()


def _own_spec():
    spec = getattr(_local, "spec", None)
    if spec is None:
        spec = _local.spec = kernel.load(KEEP)
    return spec


def _run() -> list:
    if MODE == "serial":
        return [kernel.build_one(_shared_spec, c) for c in UNITS]
    if MODE == "shared":
        with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
            return list(ex.map(lambda c: kernel.build_one(_shared_spec, c), UNITS))
    if MODE == "own":
        with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
            return list(ex.map(lambda c: kernel.build_one(_own_spec(), c), UNITS))
    if MODE == "share-serial":
        share = trace_memo.TraceShare(_shared_spec)
        try:
            return [kernel.build_one(_shared_spec, c, share=share) for c in UNITS]
        finally:
            share.release()
    if MODE == "share-fanout":
        share = trace_memo.TraceShare(_shared_spec)
        try:
            donor = kernel.build_one(_shared_spec, kernel.CONFIGS[0], share=share)
            rest = list(kernel.CONFIGS[1:]) * REPS
            with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
                tail = list(ex.map(lambda c: kernel.build_one(_shared_spec, c, share=share), rest))
            out = [donor] + tail
            order = [kernel.CONFIGS[0]] + rest
            by = dict(zip(order, out))
            return [by[c] for c in kernel.CONFIGS] * REPS
        finally:
            share.release()
    raise SystemExit(f"unknown mode {MODE}")


# AMS_BENCH_GC=off applies the cost model's already-measured font-build lever to the fixpoint. The
# two interpreters' collectors differ (the free-threaded build's is not the GIL build's), so this is
# the control that says whether a single-thread difference between them is really a GC difference.
GC_OFF = os.environ.get("AMS_BENCH_GC") == "off"
if GC_OFF:
    gc.collect()
    gc.freeze()
    gc.disable()

c0, t0 = cpu(), time.perf_counter()
results = _run()
wall, cpu_s = time.perf_counter() - t0, cpu() - c0

print(
    json.dumps(
        {
            "mode": MODE,
            "gc": "off" if GC_OFF else "on",
            "gc_collections": sum(s["collections"] for s in gc.get_stats()),
            "threads": NTHREADS,
            "units": len(UNITS),
            "keep": KEEP,
            "runes": len(_shared_spec.runes),
            "wall_s": round(wall, 3),
            "cpu_s": round(cpu_s, 3),
            "cpu_utilization": round(cpu_s / wall, 3),
            "peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 3),
            "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
            "python": sys.version.split()[0],
            "free_threading": "free-threading" in sys.version,
            "guard_state_rebuilds": _counts["guard_fresh"],
            "liveness_probe_rebuilds": _counts["probe_fresh"],
            "checksums": [r[0] for r in results],
            "rules": [r[1] for r in results],
            "windows": [r[2] for r in results],
        }
    )
)
