"""Is the repo's two module-level LRU caches' access pattern safe and cheap under free threading?

`settle._guard_state` performs `OrderedDict.get` + `move_to_end` on every call — 309k times per
six-config build at the slice size used here — and every config-parallel thread would hit the SAME
entry. `table._liveness_probe` does the same on its own cap-8 OrderedDict. This exercises exactly
that pattern (hot read plus a structural `move_to_end` on a shared entry) plus the LRU's insert and
`popitem(last=False)` eviction, from N threads, and checks the container survives it.

Prints JSON. `checksum` is the total number of successful lookups, accumulated per thread and summed
so no loop body can be optimised away.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

OPS = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000
THREADS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["1", "2", "4", "6", "8"])]
CAP = 8


def hot_shared(n_threads, ops):
    """One entry, every thread reading and re-ordering it: the _GUARD_STATES shape."""
    d = OrderedDict()
    key = object()
    d[id(key)] = (key, {"verdicts": {}})

    def work(_):
        hits = 0
        for _ in range(ops):
            e = d.get(id(key))
            if e is not None:
                d.move_to_end(id(key))
                hits += 1
        return hits

    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        hits = sum(ex.map(work, range(n_threads)))
    return time.perf_counter() - t, hits, len(d)


def churning(n_threads, ops):
    """Insert-and-evict at the cap from every thread: the _LIVENESS_PROBES shape at >=CAP threads."""
    d = OrderedDict()
    lock_free_errors = []

    def work(tid):
        hits = 0
        for i in range(ops // 20):
            k = (tid, i)
            d[k] = tid
            while len(d) > CAP:
                try:
                    d.popitem(last=False)
                except KeyError:
                    lock_free_errors.append("popitem-race")
                    break
            if d.get(k) is not None:
                hits += 1
        return hits

    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        hits = sum(ex.map(work, range(n_threads)))
    return time.perf_counter() - t, hits, len(lock_free_errors)


out = {
    "python": sys.version.split()[0],
    "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
    "ops_per_thread": OPS,
    "hot_shared_entry": [],
    "insert_evict_at_cap": [],
}
for n in THREADS:
    dt, hits, size = hot_shared(n, OPS)
    out["hot_shared_entry"].append(
        {
            "threads": n,
            "wall_s": round(dt, 3),
            "ns_per_op": round(dt / (n * OPS) * 1e9, 1),
            "checksum_hits": hits,
            "dict_intact": size == 1,
        }
    )
for n in THREADS:
    dt, hits, errs = churning(n, OPS)
    out["insert_evict_at_cap"].append(
        {"threads": n, "wall_s": round(dt, 3), "checksum_hits": hits, "popitem_races": errs}
    )
print(json.dumps(out, indent=2))
