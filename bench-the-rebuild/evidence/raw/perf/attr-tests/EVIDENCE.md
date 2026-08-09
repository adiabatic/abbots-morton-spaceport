# CPU attribution of the test suites — evidence index

Slug `attr-tests`. Everything here was measured on 2026-08-08 on the M4 Pro (8P+4E, 12 logical),
Python 3.14.6. **Other perf agents were running concurrently for part of the session** (I caught one
at 100% CPU while hunting for a PID), so every wall-clock figure below is contention-suspect and
CPU-seconds are mildly inflated by E-core scheduling. Fractions are the trustworthy quantities.

No tracked file was written. The suite was driven as raw `uv run pytest test/ site/ -n auto --dist
worksteal` (never through `make test`) so no green record was written to `rebuild/out/`.

## Headline

`make test` is **not** Python-bound. 70.6% of its CPU is inside native code a Rust/Go rewrite would
still have to call — overwhelmingly HarfBuzz's GSUB lookup-application loop, reached 88.4 million
times through `uharfbuzz.shape`. The rewritable fraction (Python bytecode in repo-owned code plus
CPython's object-model tax) is 28.6%, which caps a rewrite of *this* workload at **1.40x**, and at a
realistic 10x on the rewritable part, **1.35x**.

## Files

| file | what it is |
| --- | --- |
| `main-run-clean.txt` | full suite, uninstrumented, `--durations=40`, `/usr/bin/time -l`. **2273.65 CPU-s / 258.20 s wall / 6753 passed** |
| `main-run-clean2.txt` | repeat of the same. 2261.71 CPU-s / 247.11 s wall — 0.5% CPU reproducibility |
| `main-run-instr.txt` | same suite with `attr_plugin`. 2377.99 CPU-s / 275.71 s wall — the instrumentation costs +4.6% CPU |
| `attr_plugin.py` | read-only pytest plugin: per-test wall/CPU, `hb.shape` call count and CPU, per-worker rusage |
| `attrout-main/` | 13 NDJSON files (12 workers + controller) from the instrumented run |
| `agg.py`, `main-agg.txt` | per-worker, per-module, per-test aggregation of the above |
| `sample-sweep-serial.txt` | `/usr/bin/sample` 30 s slice of one single-process sweep test (25,285 samples) |
| `sample-worker-A.txt`, `sample-worker-B.txt` | `/usr/bin/sample` 25 s slices of two live xdist workers during a full run |
| `bucket_sample.py`, `sample-*-buckets.txt` | call-graph self-time bucketing (native / bytecode / interpreter / runtime) |
| `cum_under.py`, `sample-sweep-cum.txt` | cumulative sample share under the `hb.shape` entry point |
| `prof_sweep.py`, `cprofile-sweep.txt` | Method B: cProfile with `~`-classification, **and the proof that it is wrong here** |
| `decompose.py`, `decompose.txt` | Method C: profiler-free four-way decomposition of a sweep test |
| `noop-sessions.txt`, `noop/` | pytest + xdist fixed overhead from no-op sessions (1 item and 6753 items) |
| `pyright-standalone.txt` | `uv run pyright` x2 under `/usr/bin/time -l` |
| `make-all-time.txt` | `make all` x3 uncontended |
| `budget.txt` | the whole-suite CPU budget and the Amdahl arithmetic |
| `rebuild-run.txt`, `attrout-rebuild/`, `rebuild-agg.txt` | the second suite, `pytest rebuild/`, same instrumentation |

## Three independent methods, reconciled

Same quantity — the fraction of CPU inside HarfBuzz — measured three ways.

| method | scope | native share |
| --- | --- | --- |
| A `/usr/bin/sample`, self time by binary, 2 live xdist workers, 42,255 samples | whole suite mix | **72.48%** |
| B `attr_plugin`, `time.process_time()` around every `hb.shape` call, 88,448,272 calls | whole suite | **71.96%** of in-test CPU |
| A `/usr/bin/sample`, single-process `test_it_it_never_joins`, 25,285 samples | one sweep family | 81.93% self / 81.27% cumulative under `shape` |
| B `attr_plugin`, same family, 46 params | one sweep family | 79.2% |
| C decomposition, no profiler, same family | one sweep param | **82.5%** in the C shaper |

A and B agree to 0.5 pp on the suite and to 2 pp on the single family (the plugin's window excludes
its own wrapper frame, so it reads slightly low). The single family is shape-heavier than the suite
average — measured directly: per-family shape shares run 63.6%–79.2% across the 18 big sweeps.

## Method B's `~`-classification is unusable here — this is the important caveat

`cprofile-sweep.txt`: cProfile reports **6.8% "native"** for a workload the sampler and the
decomposition both put at **~82% native**. The reason is that `uharfbuzz.shape` is a Cython
`cyfunction`, not a `PyCFunction`, so `_lsprof` never emits a C-call event for it and its entire cost
is folded into the *Python* frame `quikscript_shaping_helpers.py:43(_shape)` (3.906 s tottime,
69.15%). Anyone who classifies pstats entries by `filename == '~'` on this repo will conclude the
suite is 93% Python. It is not. Profiler distortion measured separately: 1.27x slowdown (4.430 s
unprofiled -> 5.646 s profiled on the same cold slice), and it biases *toward* Python.

## The decomposition (Method C, zero distortion)

One cold sweep param, 103,824 shaped strings, `time.process_time()` only:

```
T_cold   sweep, everything cold            2.217 s   (21.81 us/shape)
T_warm   same param, all shapes cached     0.195 s    8.8%  <- repo Python outside _shape
T_body   _shape body, lru_cache bypassed   1.936 s          (18.65 us/shape)
T_hb     hb.shape + buffer only            1.828 s   82.5%  <- the C shaper (17.61 us/shape)
         Python glue inside _shape         0.108 s    4.9%  <- gid->name mapping, list build
         residue (lru_cache miss+insert)   0.086 s    3.8%
```

## Whole-`make test` CPU budget (2273.65 CPU-s)

```
make all prelude              22.77 s   1.00%   (11.24 s wall, 3-run mean, uncontended)
pyright                       10.63 s   0.47%   (6.10-6.53 s wall, fully overlapped by make all)
12 xdist workers            2177.66 s  95.78%   (de-instrumented from the 2282.0 s measured)
teardown + uv + make + shell  62.59 s   2.75%   (derived by subtraction)
```

Split, using the live-worker sampler fractions on the worker CPU:

```
native_extension       1604.0 s  70.55%   harfbuzz 1578 + pyright 10.6 + fontTools 14.8 + typst 0.1
python_bytecode         210.2 s   9.25%   repo test glue 205 + build_font 5.7
interpreter_overhead    440.4 s  19.37%   workers 332 + teardown 63 + dyld/memcpy 44 + imports 2
io_or_wait               18.9 s   0.83%   xdist IPC read() on worker main threads
```

## pytest / xdist framework overhead — measured on no-op sessions

| measurement | wall | CPU |
| --- | --- | --- |
| 1 trivial test, `-p no:xdist` | 0.14 s | 0.13 s |
| 1 trivial test, `-n auto` (12 workers) | 0.79 s | 2.61 s |
| 6753 trivial tests, `-n auto` | 1.89 s | 8.24 s |
| => xdist bootstrap, 12 workers | 0.65 s | 2.48 s |
| => marginal per-item bookkeeping | | 0.83 ms/item, 5.63 CPU-s for 6753 items |

On the real suite: worker prelude (import + collection) 0.48 CPU-s each = 5.87 CPU-s; collection
alone 0.45 CPU-s each = 5.45 CPU-s (every worker collects independently); framework residue
(worker CPU minus the sum of the runtest windows) 9.8 CPU-s. The lazy `_ensure_shaping_cache` (load
both fonts, build both anchor maps) lands inside each worker's *first* test at ~0.65 CPU-s, 12 times.

Total framework: **~18 CPU-s, 0.8% of the suite**. Repo-side per-worker duplication adds ~8 CPU-s.

## Module dominance

`test/test_calt_regressions.py` is 2173.0 of 2272.3 in-test CPU-s = **95.6%**, and 84,247,551 of
88,448,272 shape calls. `test/test_join_ink.py` is 3.9%. Everything else together is 0.5%.
`--durations=40` is 40 sweep params of that one module, 4.32–5.58 s each.

## The site/ data-expect corpora

602 collected items (`the-manual.html` 550, `extra-senior-words.html` 50, `index.html` 2),
**1,317 shape calls, 1.41 CPU-s total = 0.062% of in-test CPU**. They are free.

## The second suite: `pytest rebuild/` — the mirror image

Measured this session, same plugin, `-n auto --dist worksteal` (raw pytest, not `make test-rebuild`,
so no green record was written):

```
362.09 s wall / 1440.36 CPU-s tree / 20 failed, 1139 passed, 3 skipped
  12 workers, in-session                     1205.1 CPU-s (83.7%)
  build_m1 / spawn-pool children + teardown   235.3 CPU-s (16.3%, derived by subtraction)
  inside hb.shape                                 9.6 CPU-s = 0.8% of in-test CPU (767,936 calls)
  parallel efficiency                        1440.4 / 362.09 = 3.98x on 12 cores = 33%
  peak RSS                                   6.6 GB
```

The 20 failures are the documented baseline set plus the stale census pins; `make test-rebuild`'s
classifier reads those as green. They are not caused by anything measured here.

Module dominance: `rebuild/test_rule_witnesses.py` is **76.3%** of in-test CPU (915.1 s over 9 tests),
`test_review_drafts.py` 6.1%, `test_review_ink.py` 3.1%. Note that count and cost are unrelated:
`test_artifact_cycle.py`'s 203 tests total 1.3 CPU-s.

`/usr/bin/sample`, 25 s of one witness-hunt config running alone (21,240 samples, `sample-witness.txt`):

```
Python binary            95.76%   <- of which interpreter overhead 64.31%, bytecode 31.45%
libdyld (_tlv_get_addr)   3.05%
libsystem_platform        1.18%
ANY third-party native    0.00%   <- zero samples in harfbuzz, zlib, hashlib, anything
top frames: gc_collect_main 6.36%, _PyFrame_ClearExceptCode 4.07%, _PyTuple_FromArray 3.79%,
            tuple_dealloc 3.61%, _Py_dict_lookup 3.37%, subtype_traverse 2.15%
```

That single config, run alone: 143.07 s wall / 141.90 CPU-s.

So the two suites answer the rewrite question in opposite directions:

| | `make test` | `pytest rebuild/` |
| --- | --- | --- |
| tree CPU | 2273.65 s | 1440.36 s |
| native (not rewritable) | 70.6% | ~1% |
| rewritable (bytecode + interpreter) | 28.6% | ~95% |
| Amdahl ceiling | 1.40x | ~20x |
| gated by a green record | yes | yes |
| what the human waits on | usually this one | only after a `rebuild/` edit |

## The lever that is not a language change

One sweep param shapes exactly **103,824** strings, and the arithmetic is
`48 before-combos x 2163 after-combos` where 2163 = `1 + 46 + 46^2` over a 46-entry context set
(44 letters + space + ZWNJ) — measured directly (`decompose.py` recorded 103,824 texts). Dropping
`max_chars_after` from 2 to 1 takes the after dimension from 2163 to 47, a **46x** cut in shape
calls. That is a coverage decision, not a performance one, but it is the only lever that moves the
70% of `make test` no rewrite can touch. The other one is the font itself: 1,148 glyphs, GSUB
384,260 bytes, 1,345 lookups, 9,555 subtables (`calt_cycle` alone is 631) — the sample puts 20.9% of
suite CPU in `apply_forward`, 19.2% in `Coverage::get_coverage` and 14.5% in
`chain_context_apply_lookup`, all of which scale with that shape.
