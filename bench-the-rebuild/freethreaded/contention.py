"""Decisive control for the free-threaded scaling wall.

Same six fixpoints, three ways:
  serial        one thread, one spec
  shared-spec   N threads all traversing ONE ResolvedSpec object graph
  own-spec      N threads each traversing a PRIVATE ResolvedSpec object graph (loaded per thread)

If shared-spec inflates CPU and own-spec does not, the loss is contention on the shared read-only
object graph (atomic refcounts on hot shared objects), not on any lock or cache in this repo.
"""

from __future__ import annotations
from pathlib import Path

import os
import resource
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench-the-rebuild/freethreaded"))
import kernel  # noqa: E402

KEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 9
NTHREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 6


def cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def run(label, fn):
    c0, t0 = cpu(), time.perf_counter()
    res = fn()
    dt, dc = time.perf_counter() - t0, cpu() - c0
    print(f"{label:14s} wall={dt:7.2f}s cpu={dc:8.2f}s util={dc / dt:5.2f}x cksums={[r[0][:8] for r in res]}")
    return dt, dc, res


shared = kernel.load(KEEP)
run("serial", lambda: [kernel.build_one(shared, c) for c in kernel.CONFIGS])


def shared_spec():
    with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
        return list(ex.map(lambda c: kernel.build_one(shared, c), kernel.CONFIGS))


def own_spec():
    def one(c):
        return kernel.build_one(kernel.load(KEEP), c)

    with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
        return list(ex.map(one, kernel.CONFIGS))


run(f"shared-spec/{NTHREADS}", shared_spec)
run(f"own-spec/{NTHREADS}", own_spec)
print(f"peak_rss={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9:.2f}GB pid={os.getpid()}")
