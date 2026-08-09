"""Diagnose where the threaded run loses to the serial one: count fresh guard-state builds and
liveness-probe rebuilds, with and without pre-warming the two module-level caches."""

from __future__ import annotations
from pathlib import Path

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench-the-rebuild/freethreaded"))
import kernel  # noqa: E402
from rebuild.pipeline import settle as settle_module  # noqa: E402
from rebuild.pipeline import table as table_module  # noqa: E402

KEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 9
NTHREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 6
PREWARM = sys.argv[3] == "prewarm" if len(sys.argv) > 3 else False

counts = {"guard_fresh": 0, "probe_fresh": 0}
lock = threading.Lock()

_orig_guard = settle_module._guard_state
_orig_probe = table_module._liveness_probe


def counting_guard(spec):
    entry = settle_module._GUARD_STATES.get(id(spec))
    fresh = not (entry is not None and entry[0] is spec)
    if fresh:
        with lock:
            counts["guard_fresh"] += 1
    return _orig_guard(spec)


def counting_probe(spec, engine):
    entry = table_module._LIVENESS_PROBES.get(id(engine))
    fresh = not (entry is not None and entry[0] is engine)
    if fresh:
        with lock:
            counts["probe_fresh"] += 1
    return _orig_probe(spec, engine)


settle_module._guard_state = counting_guard
table_module._liveness_probe = counting_probe

spec = kernel.load(KEEP)
if PREWARM:
    settle_module._guard_state(spec)

t = time.perf_counter()
with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
    res = list(ex.map(lambda c: kernel.build_one(spec, c), kernel.CONFIGS))
dt = time.perf_counter() - t
print(
    f"threads={NTHREADS} prewarm={PREWARM} wall={dt:.2f}s "
    f"guard_fresh={counts['guard_fresh']} probe_fresh={counts['probe_fresh']} "
    f"cksums={[r[0][:8] for r in res]}"
)
