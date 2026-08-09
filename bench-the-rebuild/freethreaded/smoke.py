"""Thread-safety smoke: run the same work units serially and then on N threads, compare checksums."""

from __future__ import annotations
from pathlib import Path

import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench-the-rebuild/freethreaded"))
import kernel  # noqa: E402

KEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 9
NTHREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 6
REPS = int(sys.argv[3]) if len(sys.argv) > 3 else 1

spec = kernel.load(KEEP)
units = kernel.CONFIGS * REPS

t = time.perf_counter()
serial = [kernel.build_one(spec, c) for c in units]
t_serial = time.perf_counter() - t

t = time.perf_counter()
with ThreadPoolExecutor(max_workers=NTHREADS) as ex:
    threaded = list(ex.map(lambda c: kernel.build_one(spec, c), units))
t_threaded = time.perf_counter() - t

ok = serial == threaded
print(f"gil_enabled={getattr(sys, '_is_gil_enabled', lambda: 'n/a')()}")
print(f"units={len(units)} keep={KEEP} threads={NTHREADS}")
print(f"serial   {t_serial:7.2f}s")
print(f"threaded {t_threaded:7.2f}s  speedup {t_serial / t_threaded:.2f}x")
print(f"EQUIVALENT={ok}")
if not ok:
    for c, a, b in zip(units, serial, threaded):
        if a != b:
            print(f"  MISMATCH {c}: serial={a} threaded={b}")
