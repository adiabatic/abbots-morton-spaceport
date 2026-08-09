# Interpreter tax, serialization tax, process tax — measurements

Slug `attr-overhead`. Everything here is measured on this machine (Apple M4 Pro, 8P+4E, 48 GB, macOS 26.6.1, CPython 3.14.6) unless labelled otherwise. **The box was shared with four other agents for most of the session** — load average reached 135 near the end — so every wall figure is contention-suspect and CPU-second figures inflate when work lands on the E-cores. Where a number mattered I took the minimum of repeated runs and cross-checked it with an independent method.

Nothing here wrote a tracked file, ran a git mutation, touched `verdicts-*.json`, or rebuilt `rebuild/out/review/`. Scratch is confined to `raw/perf/attr-overhead/`.

## 1. Process spawn and import tax

`spawn_tax.py` → `spawn-tax.json`; per-process costs from `spawn-costs.json` (median of 7 real child processes each).

| | wall | CPU |
| --- | ---: | ---: |
| bare interpreter init (`.venv/bin/python -c pass`) | 17.87 ms | 16.06 ms |
| `uv run` wrapper increment | 13.71 ms | 11.68 ms |

Entry-point import CPU above that floor, measured: `tools/build_font.py` 81.6 ms, `rebuild.review.build` 71.3 ms, `rebuild.pipeline.run_m1` 66.6 ms, `pytest` 64.4 ms, `rebuild.tools.carry_verdicts` 61.4 ms, `rebuild.review.census` 60.3 ms, `rebuild.tools.standing_verdicts` 40.6 ms, `rebuild.tools.artifact_cycle` 32.4 ms, `rebuild.tools.make_test_gate` 31.5 ms, `rebuild.tools.merge_verdicts` 16.3 ms, `rebuild.tools.verdict_ready` 14.7 ms, `rebuild.tools.complaint_docket` 14.0 ms, `rebuild.tools.echo_verdicts` 5.2 ms.

**Process count, counted from the code** against the plan `artifact_cycle.py --dry-run` resolves on this tree (`dryrun-plan.txt`; `--jobs 12` for the build stages because gate:make-test auto-skips):

- 13 `uv run` children (driver, `run_m1`, `review.build`, carry, 3× merge, echo-fill, standing-fill, census, complaints, gate:conform, gate:rebuild pytest)
- 60 raw interpreter spawns beneath them: `run_m1._spawn_pool` built 3× at `min(jobs, 6)` = 18; `review.build`'s signature pool 12 and units pool 12; gate:conform's pool 6; gate:rebuild's 12 xdist workers
- **73 Python processes**, rising to **87** when gate:make-test also runs (+2 `uv` children, +12 xdist workers)

**Total fixed tax: 5.94 CPU-seconds** (0.15 s of which is `uv` itself, 1.17 s interpreter init, 4.62 s entry-point imports); 7.06 CPU-s with gate:make-test. Serial process birth would be 1.48 s of wall; in practice the pools spawn concurrently.

Against an artifact path of 549 measured CPU-seconds (and a whole system of 4,190), that is **1.1% / 0.14%**. A Rust binary paying this once instead of 73 times saves under six CPU-seconds. **Deleting `uv` entirely saves 0.15 s.**

The genuinely large per-process cost is *not* spawn or import — it is module-level **setup** each worker redoes: the review workers each load both fonts, the spec and six subset tables (the review-surface agent measured the `--jobs 10` build at 202.2 CPU-s against 136.7 serial — 48% more CPU, i.e. ~65 CPU-s of duplicated setup), and each pytest worker re-parses `glyph_data/` and re-runs `compile_glyph_set` / `build_anchor_map` at 1.28 CPU-s (cited from the test-suite agent, 12–24 workers). Threads in Rust or Go would share that state; so would a `fork` start method, which this repo cannot use because uharfbuzz/fontTools C objects are not fork-safe.

## 2. Serialization across process boundaries

`serialization.py` → `serialization.json`. Every payload is a real object built from a real on-disk artifact.

| payload | bytes | dumps | loads | rate |
| --- | ---: | ---: | ---: | ---: |
| `ResolvedSpec` (task arg to every `run_m1` pool worker) | 64,595 | 0.5 ms | 0.7 ms | 118 MB/s |
| `DecisionTable`, windows dropped (normal conform path) | 301,239 | 1.3 ms | 2.4 ms | 234 MB/s |
| **`DecisionTable` WITH 1,321,116 windows** | **39,640,475** | **3.16 s** | **3.22 s** | **12.5 MB/s** |
| 2,000 real surface unit fragments (phase-2 pipe return) | 5,799,766 | 24.3 ms | 16.4 ms | 238 MB/s |

Two things stand out. First, the rate collapses by 19× on the object-graph payload: 2.39 µs per `Window` dataclass to pickle. Pickle cost here is per-object, not per-byte — the same interpreter tax wearing a different hat. Second, the dangerous payload is real: `rebuild/pipeline/run_m1.py:311` passes `decision=rebuilt[config][0]` in the standalone fallback taken whenever the serialized tables' fingerprint does not match, which pickles the full enumeration to all six workers — **~238 MB and ~38 CPU-seconds of pure serialization**, derived from the measured per-table cost. The normal path avoids it (workers read `windows_path` themselves).

Surface fragments scale linearly: 5.80 MB per 2,000 units → **180 MB per 62,148-unit build**, 0.76 s to dump and 0.51 s to load, which reproduces the review-surface agent's independent 172 MB figure.

**The rebuild test suite's cross-process caches, measured directly** (`rebuild/conftest.py`'s flock'd gzipped pickles, read back from the live cache under `tmp/`):

| cache | gz | raw pickle | gunzip | unpickle | rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| `enriched_units` (63,442 objects) | 27.8 MB | 313.7 MB | 0.163 s | **3.214 s** | 97.6 MB/s |
| `workload` | 7.7 MB | 88.5 MB | 0.036 s | 0.708 s | 125.0 MB/s |

Each of 12 xdist workers pays this: **≈47 CPU-seconds per rebuild-suite run** (3.4% of its 1,444 CPU-s) and, more importantly, ~2.35 GB resident per worker — which is exactly why the conftest holds the exclusive lock through the *read* rather than letting twelve workers materialize the graph at once. Threads in Rust or Go pay none of this: the graph would be built once and borrowed.

**pytest-xdist itself is not a serialization problem.** The test-suite agent measured the controller at 2.1 CPU-s against 2,212 CPU-s of workers — 0.09%.

## 3. GC pressure

`gc_experiment.py` → `gc-experiment.txt`, `gc-ab.txt`. Workload: `table.build_tables` for the default configuration with `trace_store=None` — the repo's single biggest serial cost.

Interleaved A/B, three reps each, default config (CPU seconds of `build_tables`):

| rep | gc on | gc off |
| --- | ---: | ---: |
| 1 | 103.17 (wall 134.4 — contended) | 70.92 (wall 71.0 — clean) |
| 2 | 81.80 (wall 81.9) | 76.48 (wall 79.1) |
| 3 | 85.63 (wall 86.2) | 95.64 (wall 121.2 — contended) |
| **min** | **81.80** | **70.92** |

Minimum-to-minimum: **13.3% of CPU is cyclic GC**. Independently, `/usr/bin/sample` leaf attribution on a live gc-on build puts GC symbols (`gc_collect_main`, `visit_decref`, `visit_reachable`, `subtype_traverse`, …) at **12.92%**, and two peer agents' samples of the same workload give 13.64% and 14.03%. Four independent measurements converge on **≈13%**. The gc-off sample confirms the complement: 0.00% GC.

`gc.freeze()` does **not** help (106.8 CPU-s vs 100.7 in the first sequence, i.e. no better than noise): it froze only 18,384 objects, because the multi-gigabyte graph the collector scans is built *during* the run, not before it.

The damning number is what the GC finds: across **15,698 collections** (14,374 gen-0, 1,306 gen-1, 18 gen-2) the run collected **12 objects**. This is a generational collector repeatedly scanning ~700k `Transition` objects and a 1.9 M-entry memo — all of it live, none of it cyclic garbage — for nothing. `gc.freeze()` after spec load plus `gc.disable()` around the fixpoint is a two-line, ~13% win available today with no rewrite. (The surface build's warm path is worse: 33.6% of its leaf samples are GC.)

## 4. Memory

`memory_model.py` → `memory-model.json`; peak RSS from `getrusage`.

Measured peaks: M1 default-config table build **3.82 GB**; all six configs **7.46 GB** (calibration); review surface serial 3.25 GB, 2.26 GB/process at `--jobs 10`; `make test` 1.6–1.8 GB across 12 workers; rebuild suite 5.4–5.6 GB.

Per-object cost of the two big real graphs, from RSS deltas:

| object | count | CPython | packed Rust | ratio |
| --- | ---: | ---: | ---: | ---: |
| settlement memo key (10-field tuple) | 1,875,829 | **137.0 B** | ~40 B | 3.4× |
| …plus its dict index | | **257.3 B** | ~46 B | 5.6× |
| `table.Window` (7 fields, `slots=True`) | 1,321,116 | **97.8 B** | 28 B | 3.5× |

137 B for a ten-element tuple is exactly CPython's charge: a 56-byte object header plus ten 8-byte pointers, rounded by the allocator. Note `Window` already uses `slots=True` and still costs 3.5×; the untouched `settle.Candidate` / `RightToken` family does not use slots at all.

Extrapolating the measured ratios, the M1 build's 3.82 GB would land near **0.7–1.1 GB** in Rust. That matters beyond RAM: the fixpoint's inner loop is a hash probe into a 1.9 M-entry table whose keys are pointer-chased tuples of separately-allocated objects. Packing them into contiguous arrays turns most of those probes from three cache misses into one — which is a large part of why the 10–50× band is plausible here at all.

## 5. Attribution — three independent methods

### Method A — `/usr/bin/sample` leaf attribution (preferred)

`bucket_sample.py` → `sample-buckets-all.json`. Buckets the "Sort by top of stack" section: leaf `_PyEval_EvalFrameDefault` = Python bytecode; anything else in the CPython binary or its runtime support = interpreter overhead; a third-party native library = native; kernel frames = io. Idle threads parked in `__psynch_cvwait` etc. are excluded from the denominator (this alone corrected one peer sample from a bogus 50% "io" to 82% native).

| sample | live leaves | bytecode | interp | native | io | (gc) | (alloc) | (dealloc) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| M1 `build_tables`, gc on (mine) | 25,704 | 32.17 | 67.83 | 0.00 | 0.00 | 12.92 | 4.94 | 7.04 |
| M1 `build_tables`, gc off (mine) | 20,704 | 37.02 | 62.95 | 0.00 | 0.03 | 0.00 | 4.95 | 7.92 |
| M1 `build_tables` (peer, m1-pipeline) | 25,460 | 31.37 | 68.63 | 0.00 | 0.00 | 13.64 | | |
| M1 `build_tables` (peer, attr-m1) | 25,410 | 30.81 | 69.19 | 0.00 | 0.00 | 14.03 | | |
| conform sweep L4 (peer) | 25,617 | 38.25 | 61.62 | 0.14 | 0.00 | 0.92 | | |
| surface build, units phase (peer) | 25,042 | 32.18 | 65.25 | 2.45 | 0.12 | 6.14 | 7.55 | 12.97 |
| surface build, load phase (peer) | 19,496 | 17.64 | 71.77 | 10.19 | 0.40 | 0.29 | 12.14 | 8.50 |
| surface build, warm rebuild (peer) | 8,299 | 14.91 | 76.68 | 7.87 | 0.54 | **33.56** | | |
| census `--check` (peer) | 8,408 | 28.45 | 55.74 | 15.81 | 0.00 | 0.00 | 7.23 | 16.75 |
| `make all` senior worker (peer) | 21,097 | 28.66 | 71.27 | 0.07 | 0.00 | 13.88 | | |
| feaLib build (peer) | 25,442 | 25.41 | 74.59 | 0.00 | 0.00 | 13.77 | | |
| **`make test` calt sweep (peer)** | 20,919 | **6.77** | **11.62** | **81.61** | 0.00 | 0.30 | | |
| `make test` sweep, second peer sample | 25,003 | 6.74 | 10.98 | 82.28 | 0.00 | 0.73 | | |
| rebuild suite witness hunt (peer) | 30,794 | 32.43 | 67.57 | 0.00 | 0.00 | 12.02 | | |

The shape is astonishingly consistent: **everywhere except the shaping tests, native libraries are 0–16% and CPython machinery is 55–77%**. Inside the shaping tests it inverts completely — 82% HarfBuzz.

Note also that "interpreter overhead" is always about **twice** the bytecode bucket. The interpreter spends two-thirds of its time not running the program but allocating, freeing, hashing, comparing and tracing the objects the program manipulates.

### Method B — cProfile attribution fractions

`bucket_pstats.py` → `pstats-buckets.json`. Classifies each pstats entry: `~` filename = a C function, split further into CPython's own object model (`list.append`, `dict.get`, `builtins.len`, …) versus a genuine native library; a real `.py` path is a Python frame, split into this repo versus site-packages.

| profile | tottime s | py (repo) | py (3rd-party) | C (interp builtins) | C (native lib) |
| --- | ---: | ---: | ---: | ---: | ---: |
| M1 `build_tables` default | 266.4 | 82.03 | 0.05 | 17.92 | 0.00 |
| surface phase 1 (1500 units) | 3.0 | 85.99 | 2.81 | 10.98 | 0.22 |
| surface phase 2 (1500 units) | 1.2 | 48.23 | 34.90 | 16.74 | 0.13 |
| surface warm rebuild | 49.3 | 62.42 | 18.04 | 14.27 | 5.28 |
| senior FEA emit | 7.4 | 64.02 | 0.01 | 35.97 | 0.00 |
| `build_font` senior | 19.1 | **4.20** | **74.47** | 20.60 | 0.73 |
| rebuild witness hunt | 213.2 | 77.96 | 0.00 | 22.04 | 0.00 |
| calt sweep shard | 3.0 | 89.92 | 3.33 | 6.74 | **0.00** |

**cProfile's distortion, measured** (`method-bc-flagsoff.json`): the same `build_tables` run costs 9.11 / 8.95 / 9.15 s plain and 21.50 s under the profiler — a **2.37× slowdown**. The overhead attaches per Python call event and (much less) per C call, so it **inflates the Python-frame bucket**; Method B therefore over-reports the rewritable share.

There is a second, larger bias, and the last row above is the proof of it: cProfile reports **0.00% native** for the calt sweep, when the native sampler says 82%. `uharfbuzz.shape` is a Cython call that cProfile cannot see as a separate event, so all of HarfBuzz's time lands in the calling Python frame's `tottime` (`test/quikscript_shaping_helpers.py:_shape`, 2.066 s of 2.993 s). **Any cProfile-only reading of the test suite is wrong by an order of magnitude.** Where the two methods disagree, Method A wins.

Where they agree they agree well: for `build_tables`, Method A says 32% bytecode / 68% interpreter machinery and Method B says 82% Python frames / 18% C builtins with **zero** native library — two different cuts of the same fact, that nothing outside CPython is running.

### Method C — in-process sampling thread (200 Hz, `sys._current_frames`)

`method_bc.py` → `method-bc-flagsoff.json`. Distortion measured at **0.8%** (8.98 s sampled vs 9.05 s plain median) — effectively free, unlike cProfile's 2.37×.

Top-of-stack Python frames during the (flags-off) default table build:

| frame | share of Python frames |
| --- | ---: |
| `<string>:__init__` (dataclass-generated) | 23.54% |
| `<string>:__hash__` (dataclass-generated) | 7.54% |
| `<string>:__eq__` (dataclass-generated) | 2.92% |
| `table.py:build_tables` | 6.00% |
| `settle.py:candidates` | 4.46% |
| by module: `settle.py` 46.31, `<string>` 34.00, `table.py` 17.69 | |

**A third of the Python-level execution of this repo's hottest code is the generated `__init__`/`__hash__`/`__eq__` of frozen dataclasses.** K7 in `kernels.md` prices that directly: 375 ns to construct one against 32 ns for a plain tuple.

## 6. The reconciled split, and the Amdahl arithmetic

`weighted_split.py` → `weighted-split.json`. Method A bucket sets weighted by measured CPU seconds per workload, with the Python share attributed to this repo versus third-party Python (fontTools, pytest) using Method B's repo/site-packages split. "Rewritable" = (bytecode + interpreter overhead) × this-repo share, because interpreter overhead belongs to whichever Python code drives it.

**Scope 1 — the artifact-production path (549.3 CPU-s: M1 table stage 337.7, surface build 143.1, census 46.1, `make all` 22.4):**

| bucket | % |
| --- | ---: |
| python_bytecode | 29.94 |
| interpreter_overhead | 67.08 |
| native_extension | 2.91 |
| io_or_wait | 0.07 |
| **rewritable by rewriting THIS repo** | **92.43** |

Amdahl: ceiling **13.2×**; **5.95×** at 10× on the rewritable part, **8.88×** at 25×, **10.62×** at 50×.

**Scope 2 — the whole system the human waits on (4,190.4 CPU-s: the above plus `make test` 2,197.0 and `pytest rebuild/` 1,444.1):**

| bucket | % |
| --- | ---: |
| python_bytecode | 18.63 |
| interpreter_overhead | 37.98 |
| **native_extension** | **43.38** |
| io_or_wait | 0.02 |
| **rewritable by rewriting THIS repo** | **55.37** |

Amdahl: ceiling **2.24×**; **1.99×** at 10×, **2.13×** at 25×, **2.19×** at 50×.

The entire difference between the two answers is `make test`: 2,197 CPU-seconds of which 82% is HarfBuzz applying a 9,555-subtable GSUB, 88.4 million times. No rewrite of this repository touches a second of it, and rewriting it in Rust with `rustybuzz` would destroy the point of the gate (that an *independent* shaper agrees).

**Excluded, and honestly so:** gate:conform and the seven-child verdict-plumbing chain have never been measured on any machine (the brief forbade running them, and `rebuild/out/cycle-timings.ndjson` still does not exist here). gate:conform is settlement code, so it would move the artifact scope's numbers *toward* more rewritable; the plumbing chain is dominated by re-parsing 264.5 MB of JSON seven times, which would move it toward less.

## Files

Scripts: `measure_spawn.py`, `spawn_tax.py`, `serialization.py`, `gc_experiment.py`, `memory_model.py`, `bench_kernels.py`, `extract_ink_data.py`, `extract_key_and_tsv_data.py`, `bucket_sample.py`, `bucket_pstats.py`, `method_bc.py`, `weighted_split.py`.

Results: `spawn-costs.json`, `spawn-tax.json`, `serialization.json`, `gc-experiment.txt`, `gc-ab.txt`, `memory-model.json`, `kernels-run1..4.json`, `kernels-best.json`, `sample-m1-gcon.txt`, `sample-m1-gcoff.txt`, `sample-buckets-m1.json`, `sample-buckets-all.json`, `pstats-buckets.json`, `method-bc-flagsoff.json`, `weighted-split.json`, `dryrun-plan.txt`, `build_tables-default-methodB.prof`.

Kernel data: `data/outlines-before.json`, `data/outlines-after.json`, `data/shaped-runs.jsonl`, `data/coord-types.json`, `data/baseline-rows.tsv`, `data/memo-keys.tsv.gz`, `data/candidate-fields.tsv`.

Specification: `kernels.md`.
