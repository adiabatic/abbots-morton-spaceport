# Reconciled cost model: is Python the bottleneck here?

Synthesis of eight investigation slices under `raw/perf/`. Every number carries a tag: **M** measured on this machine, **D** derived from measured inputs, **C** cited from a commit message or docstring and never measured anywhere, **U** unmeasured.

**Contention discipline.** The `calibrate` slice ran on an exclusive box and is the authority for every wall-clock number where it has one. The mapping and attribution slices ran with three to five sibling agents at 80-100% CPU; their wall figures run 1.9-2.2x high and their CPU figures up to 38% high. Where they disagree with `calibrate`, `calibrate` wins. Execution-kind splits, `/usr/bin/sample` stack fractions and cProfile fractions are contention-robust and are taken from whichever slice measured them best.

---

## Headline

Yes — but only once you weight by what a human actually waits for, and the reason is not the CPU split. It is **parallelism and frequency**.

The whole-system CPU picture says Python is *not* the bottleneck: 43% of all CPU-seconds in this repo sit inside HarfBuzz. But that CPU is concentrated in `make test`, which runs at **10.76x on twelve cores** (M) and is armed by only **6.3% of commits** (M, measured from git log). The M1 decision-table fixpoint is the opposite on both axes: it runs at **1.00x — wall equals CPU to three digits, eleven cores idle for five and a half minutes** (M) — it is armed by roughly **68% of commits** (M), and a `/usr/bin/sample` call graph of a live build contains **zero frames of harfbuzz, zlib, hashlib, json, pickle or re** (M, 25,410 leaf samples).

So the answer flips with the denominator:

| Denominator | Rewritable by a rewrite of *this repo* | Amdahl ceiling |
|---|---|---|
| All CPU-seconds, both test gates included | 55-60% (M) | 2.2-2.5x |
| All CPU-seconds, artifact-production path only | 92.4% (M) | 13.2x |
| **Wall-seconds a human is blocked on, weighted by measured commit cadence** | **91.0%** (D) | **11.1x** |
| Wall-seconds of machine time per day, foreground + deferred gates | 93.4% (D) | 15.2x |

The third row is the one that answers the question as asked, and the single line that produces it is this: **the M1 table stage is 1,263 foreground wall-seconds per day (61% of everything a human waits on), it is 100% this repo's own Python by leaf-module attribution, and it is strictly single-threaded.**

---

## 1. The cost table

Wall and CPU are seconds. "Frequency" is per working day, anchored on a **measured** commit cadence of 8.89 commits/day over the last 80 commits and 9 active days, classified by which gate closure each commit's files fall in (`git log`, measured by this slice — no other slice did this).

| Workload | Cold wall | Cold CPU | Warm/skip wall | Warm CPU | How often | CPU split (bytecode / interp / native / io) | Rewritable by this repo |
|---|---|---|---|---|---|---|---|
| **M1 table stage, 6 configs** (`table.build_tables`) | **337.9** M | 337.7 M (358.9 incl. teardown) | **194.9** M | 194.7 M | 2.11 cold + 2.44 partial passes/day | 30.8 / 69.2 / **0.0** / 0.0 M | ~98.5% |
| **Review surface build** (`--jobs 6`) | **35.6** M | 143.1 M | **13.6** M | 13.5 M | 3.55 cold + 2.44 partial/day | 28.5 / 65.6 / 5.7 / 0.2 M | 91.3% |
| **`make test`** (6,753 tests) | **205.6** M | 2,212.4 M | **0.078** M | 0.072 M | 0.56 cold + 8.3 skips/day | 9.3 / 19.4 / **70.6** / 0.8 M | 28.6% |
| **`pytest rebuild/`** (1,162 tests) | 340.1 C-cont / **250** D | 1,444 C-cont / 1,150 D | ~0.13 D | — | 3 gate passes/day (deferred, background) | 32.4 / 62.6 / 4.2 / 0.8 M | 95% |
| **gate:conform** (six-config sweep) | **336** D *floor* | 2,017 D *floor* | 0.052 M (skip) | — | 3 gate passes/day (deferred) | ~36 / ~60 / ~4 M-partial | ~100% |
| **`make all`** (font build + typst) | **11.24** M | 22.76 M | *no skip path exists* | — | 2.56/day | 29.2 / 64.7 / 4.5 / 1.5 M | **22.9%** |
| **`census --check`** | 74.7 M-cont / **47** D | 46.1 M | 0.024 M (result record) | — | 3/day (deferred) | 28.5 / 55.7 / **15.8** / 0.0 M | 84.2% |
| **verdict plumbing + complaints** | **26** C | — U | 0.003 M (skip) | — | 8 passes/day | ~20 / ~60 / ~20 U | ~80% U |
| **`make review`** (scoped-anchor report) | **21.4** M | 43.0 M | *no skip path exists* | — | ~0.3/day | not attributed U | ~94% U |
| **`make verdict-ready`** | **0.976** M | 0.968 M | *no skip path exists* | — | ~6/day | 91% is one `json.loads` of 265 MB M | ~80% |
| **pyright** | 5.78 M | 9.90 M | — | — | inside `make test`, **fully overlapped** M | node/TypeScript | 0% |
| **gate:js** (`node --test`) | 0.104 M | 0.432 M | — | — | 13/day | node | 0% |
| **artifact-cycle plan resolution** | 0.176 M | 0.171 M | 0.176 M | — | 13/day | pure Python, and **already free** | irrelevant |
| **all seven skip fingerprints, summed** | 0.307 M | — | 0.307 M | — | per pass | sha256 over 15.6 + 2.6 + 59 + 5.3 MB | irrelevant |

Three facts in that table matter more than the rest.

- **`make test`'s skip costs 78 milliseconds to avoid 2,212 CPU-seconds** (M) — a 28,000x avoidance ratio, in which the sha256 sweep over 100 files / 15.6 MB (13 ms) is cheaper than the interpreter that computes it (26 ms `uv` + 27 ms import). The layer that *looks* like the build system is already free. A Rust rewrite of `artifact_cycle.py`, `make_test_gate.py` or `rebuild_gate.py` would save a fraction of a second per pass.
- **`make all` has no skip path at all** (M, verified: all six variants printed "Font unchanged" and it still took 11.24 s). It is the only workload here that pays its full cost on every single invocation.
- **The M1 table stage is the only step in the repo where the machine is being wasted rather than used** (M): 337.66 CPU-s against 337.87 s wall.

### Partial-invalidation states (the ones a rune edit actually meets)

- **M1 after a one-rune edit: 224.7 s** D. Derived by interpolating the two measured endpoints (194.9 warm, 337.9 cold) at the **measured median memo invalidation of 20.9%** — recomputed by this slice from `memo-invalidation.json` (per-rune invalidated entries range 283,838 for ·Low to 804,890 for ·Utter, of 1,875,829; the m1-pipeline slice reported "~22%", mildly high).
- **Review surface after a one-letter edit: 19.9 s wall / 50.6 CPU-s** D, interpolating the calibrated endpoints at the 28.6% fresh-delta fraction that commit a894e226 reports (C).

---

## 2. The weighted picture

Split by whether the human is blocked. This matters because `make review-cycle --defer-gates` deliberately moves the three heavy gates and the census *off* the interactive path — CLAUDE.md's phrasing is that the deferred verification "is now background work rather than a lockout."

### Foreground — the human is waiting

**2,062 wall-seconds per day = 34.4 minutes.** Of that, **1,876 s (91.0%) is Python this repo owns.**

| Line | s/day | Rewritable | Rewritable s/day |
|---|---:|---:|---:|
| run_m1, cold (2.11 `rebuild/pipeline` edits/day) | 833.4 | 98.5% | 820.9 |
| run_m1, partial (2.44 rune edits/day) | 688.4 | 98.5% | 678.1 |
| verdict plumbing chain + complaints (8 passes) | 208.0 C | 80% U | 166.4 |
| `make test`, cold (0.56/day) | 114.2 | 28.6% | 32.7 |
| review surface, cold (3.55/day) | 126.5 | 91.3% | 115.4 |
| review surface, partial (2.44/day) | 48.6 | 91.3% | 44.4 |
| `make all`, standalone (2/day, assumed) | 22.5 | 22.9% | 5.2 |
| `make review` (0.3/day, assumed) | 6.4 | 94% U | 6.0 |
| driver + fingerprints + gate:js (13 passes) | 7.7 | 22.9% | 1.8 |
| `make verdict-ready` (6/day, assumed) | 5.9 | 80% | 4.7 |
| `make test`, skip (8.3/day) | 0.7 | 28.6% | 0.2 |

**Of the 1,876 rewritable foreground seconds, 1,263 (67%) are the M1 decision-table fixpoint alone.**

### Background — the machine is busy, the human is not blocked

**1,899 s/day = 31.7 minutes**, at **98.8% rewritable**: gate:conform 1,008 s (D, floor), gate:rebuild 750 s (D), census 141 s. This is where a rewrite's second-largest prize is, and it is worth less per second because nobody is waiting on it.

### Combined

**3,961 s/day of machine time, 93.4% of it Python this repo owns.**

### Sensitivity

The frequency model rests on a **measured** commit cadence and **measured** gate-closure shares, plus one **assumption**: one refreshing cycle per artifact-arming commit, one settling pass per two refreshers, two verdict passes and two no-op passes a day. Halving or doubling cycles-per-commit moves the daily totals by roughly ±40% but moves the rewritable *fraction* by under two points, because the assumption scales the M1 and surface terms together and they are the two most-rewritable terms.

---

## 3. Amdahl

Speedup if the rewritable fraction gets 10x / 25x / 50x faster. Ceiling is the rewritable fraction driven to zero time.

| Workload | Rewritable | 10x | 25x | 50x | Ceiling |
|---|---:|---:|---:|---:|---:|
| M1 table stage | 98.5% | **8.81x** | **18.4x** | **28.8x** | 66.7x |
| `pytest rebuild/` | 95.0% | 6.90x | 11.4x | 14.5x | 20.0x |
| gate:conform | ~100% D | ~10x | ~25x | ~50x | unbounded D |
| Review surface, cold | 91.3% | 5.60x | 8.08x | 9.48x | 11.5x |
| Review surface, warm | 88.7% | 4.95x | 6.72x | 7.62x | 8.8x |
| `census --check` | 84.2% | 4.13x | 5.21x | 5.72x | 6.3x |
| verdict plumbing | 80% U | 3.57x | 4.31x | 4.63x | 5.0x |
| **`make test`** | **28.6%** | **1.35x** | **1.38x** | **1.39x** | **1.40x** |
| **`make all`** | **22.9%** | **1.26x** | **1.28x** | **1.29x** | **1.30x** |
| **Weighted, foreground (human waiting)** | **91.0%** | **5.51x** | **7.89x** | **9.21x** | **11.1x** |
| **Weighted, combined (foreground + deferred)** | **93.4%** | **6.27x** | **9.68x** | **11.8x** | **15.2x** |

Read the two extremes together. `make test` and `make all` are flat — the whole spread from 10x to infinity is 1.26x to 1.40x, so the *quality* of a rewrite is irrelevant there and only its existence would be a mistake. The M1 fixpoint is the mirror image: 10x already buys 8.8x end to end, and there is no native floor to divide by.

Note also that **10x already captures most of what infinity offers** everywhere except M1. That is the practical argument for scoping a rewrite tightly: a merely-competent Rust port of the settlement kernel gets 8.8x on the biggest thing a human waits for, and a heroic one gets 28.8x.

### Do the cheap Python fixes first — they are a large fraction of the ceiling

Two measured examples, both with byte-identical output proven by hash:

- **`make all`**: `yaml.CSafeLoader` + `gc.freeze()`/`gc.disable()` = **-16.8% CPU, 1.20x** (M, four full serial builds, identical OTF sha256 across all rows). That is **65% of the 1.30x ceiling** of an infinitely fast Rust rewrite of every line of this repo's font-build Python — for about three lines.
- **Review surface, warm path**: `config_badge` is 3.684 of a 13.69 CPU-s warm rebuild (**27%**) for only **12 distinct config tuples**, and one `lru_cache` makes it 198x faster (M). The warm path's read-265-MB / patch-19-scalars / write-265-MB round trip is another ~17%.

---

## 4. Contradictions between the agents, and my adjudication

**1. `make test`'s native fraction: 81.6% (attr-overhead) vs 70.6% (attr-tests).**
Adjudicated for **attr-tests, 70.55%.** attr-overhead applied the bucket of `raw/perf/test-suite/sample-sweep.txt` — a *single-process, isolated* sweep test — to all 2,197 CPU-s of the suite. attr-tests sampled **two live xdist workers** (68.15% and 76.83% harfbuzz over 42,255 samples) and independently instrumented **every one of the 88,448,272 `hb.shape` calls** with `process_time` (71.96% of in-test CPU), then accounted for the `make all` + pyright prelude and worker teardown separately. The isolated sweep is one of the shape-heaviest tests in the suite and contains none of the prelude. **Consequence: attr-overhead's whole-system rewritable fraction of 55.37% is ~5 points low; corrected it is ~60%.** I use 70.55%.

**2. Review-surface cold build: 190.1 s / 136.7 CPU (review-surface) vs 130.9 s / 125.2 CPU serial and 48.6 s / 196.9 CPU at `--jobs 6` (attr-surface) vs 35.6 s / 143.1 CPU at `--jobs 6` (calibrate).**
Adjudicated for **calibrate: 35.56 s / 143.13 CPU.** Identical command, identical output (62,148 units / 292,098 rows / 36 batches in every run). Contention inflated attr-surface's `--jobs 6` CPU by **37.6%** and its wall by **36.7%**. The serial-vs-parallel CPU gap (125.2 serial vs 143.1 at `--jobs 6`) is *not* contention and is real: each spawn worker independently loads both fonts, the spec, and six subset tables. Corollary the mapping slice got backwards: `--jobs 10` contended (67.8 s) is **worse** than `--jobs 6` uncontended (35.6 s) — more workers bought no wall time and each paid a full setup.

**3. `make review`: 47.0 s (build-topology) vs 21.4 s (calibrate). gate:make-test skip: 0.15 s vs 0.078 s. Census: 74.65 s wall at 62% CPU utilisation.**
All three are contention, 1.9-2.2x. Use **21.40 s**, **0.078 s**, and **~47 s** for the census (its 46.07 CPU-s is robust; only the wall was contended).

**4. "37% of leaf samples are frozen-dataclass methods that `slots=True` would cut" (attr-m1).**
**Half right, and the actionable half is wrong.** The 36.95% is real and confirmed (`pysample-build-tables-default.json`: `<string>` frames — the generated `__init__`/`__hash__`/`__eq__` — are 36.95% of Python-level leaf samples, with `<string>:2 __init__` alone at 25.14%). But `attr-overhead`'s own microbenchmark, on the same box, measures **frozen dataclass with `slots=True` at 361.7 ns/construct against 375.4 ns without — a 3.6% saving, not 37%** (`kernels-best.json`, K7a/K7b). The 37% is only recoverable by **not constructing the objects at all**: a plain tuple constructs at 32.0 ns (**11.7x**) and hashes at 21.2 ns against 107.0 ns (**5.0x**). This is therefore a *rewrite* argument, not a `slots=True` argument, and anyone who adds `slots=True` expecting a third of the build back will be disappointed.

**5. "gc.freeze()/gc.disable() is 13% of the M1 table build, nearly free money" (attr-m1, attr-overhead).**
**Directionally right, but the experiment offered as proof does not support it.** The three interleaved A/B reps measured GC-on at 103.17 / 81.80 / 85.63 CPU-s and GC-off at 70.92 / 76.48 / **95.64** — one rep has GC-off *slower by 12%*. The 13% figure is a minimum-of-reps comparison across a set whose spread is ±35% under contention. What *does* hold the claim up is independent and strong: two `/usr/bin/sample` runs put the GC group at 14.79% and 12.92% of leaf samples, and the fixpoint performs 15,698 collections that collect **12 objects** total (M). Verdict: the share is real, the A/B timing is not evidence for it. Contrast the font build's identical lever, which *was* cleanly measured at **-12.14% with byte-identical OTFs** (`attr-fontbuild/lever-experiments.txt`).

**6. "`AMS_SIMULATED_PROSPECT=0 AMS_VOTE_SLOTS=0` is a 9.6x lever" (m1-pipeline).**
**Confirmed as a measurement, rejected as a lever.** 8.44 CPU-s flags-off is confirmed (`timing-default.json`); against calibrate's uncontended 77.51 s flags-on that is 9.2x. But the flags-off build produces a *different artifact*: 479,371 windows against 682,842, 1,165 rules against 2,667, 2,867 GSUB rules against 7,024, 197 settled cells against 204 (M, `stage-bench-flagsoff.log` vs `stage-bench.log`). It is not the same computation done faster; it is weaker semantics. It cannot be banked without changing what the font does.

**7. `import_departure_mono` labelled "native-extension-bound" (build-topology).**
**Measured false** by attr-fontbuild's boundary instrumentation: the phase is **1.626 s of this repo's ray-casting against 0.062 s of fontTools charstring execution (96.3% repo)**, and its own `/usr/bin/sample` slice shows 0.12% native. It is repo Python — and it is entirely off the wall critical path (the mono workers finish at 1.2 s while the senior workers run 8.1 s), so it costs CPU, not waiting.

**8. `_ProspectLiveness`' share: 84% (m1-pipeline cProfile) vs 79.3% (attr-m1 sampler).**
**Not a real contradiction** — cumulative-time-under-a-call-site and leaf-stack-fraction measure different things. Both say it is the dominant call site. I carry **84%** for the cumulative claim and **79.3%** for the leaf claim, and use 84% when apportioning kernel 1.

**9. Surface size: 264.5 MB / 266.7 MiB / 279.7 MB / 253 MiB.**
MB-vs-MiB plus fresh-vs-live. I verified the live tree with `du`: **`rebuild/out/review` is 253 MiB = 265.3 MB.** All four figures agree once units are normalised; fresh builds differ by a few MB because they carry the current ledger.

**10. YAML loader speedup: 8.6x (font-build) vs 7.3x (attr-fontbuild) vs 7.7x (build-topology).**
Different corpora — `glyph_data/*.yaml` alone, the full `load_glyph_data` equivalent, and the 18 rune files. All three agree on the shape. I use **7.3x / 1.150 CPU-s saved per `make all`** (the full-corpus figure) and **7.7x / 0.040 s per fingerprint pass** for the runes.

**11. "Python is not the bottleneck" (attr-tests, 1.40x) vs "Python is emphatically the bottleneck" (attr-m1/attr-surface, 11-29x).**
**Both are right about their own workload, and the disagreement is the finding.** The reconciliation is the frequency-and-parallelism weighting in §2: `make test` is 2,212 CPU-s but only 205.6 wall-s and fires on 6.3% of commits; the M1 fixpoint is 337.9 CPU-s *and* 337.9 wall-s and fires on ~68%.

**12. attr-m1 reports the six-config table stage's CPU as 337.66 s.**
**Mildly understated.** `/usr/bin/time -l` on the same run shows **360.01 s real / 356.62 s user / 2.34 s sys** — the reported figure omits ~19 CPU-s and 22 wall-s of interpreter teardown freeing a 7.46 GB object graph, which the human waits through and no profiler attributes to anything. I carry the teardown explicitly.

---

## 5. Ranked rewrite targets

Ranked by **measured wall-seconds per day** the kernel accounts for. "10x saves" is 90% of that.

| # | Kernel | s/day (fg + bg) | 10x saves | Verdict |
|---|---|---:|---:|---|
| 1 | **Settlement kernel + deep-slot liveness** — `table.build_tables` fixpoint driving `settle.Engine.{candidates,_prospect,_prefer_favors,transition_trace}`, with `table.third_slot_filter`'s `matters` closure and `_ProspectLiveness.{third_live,fourth_live}` on top | **2,760** (1,263 + 1,497) | **2,484** | **Do this one.** Everything else is rounding. |
| 2 | `status.load_human_unit_ids` + the plumbing chain's 7x re-parse of the 265 MB surface | 65.8 | 59.2 | **Not a rewrite.** Persist the id set in the manifest. |
| 3 | `ink.translate_outline` / `run_ink` / `config_diff` — placed-outline arithmetic | 64.8 | 58.3 | Genuine Rust target, cleanest interface in the repo. |
| 4 | `rebuild.pipeline.explain` / `settle_traces` via `Enricher.enrich` | 26.5 | 23.8 | Free — kernel 1 covers it. |
| 5 | `rowmodel.Row.from_tsv` / `audit.load_audit` / `baseline_subset.filter_table` | 23.8 | 21.4 | Textbook Rust win, small prize. |
| 6 | `build.config_badge` via `unit_scaffold` | 22.1 | 19.9 | **One `lru_cache` line, 198x.** Not a rewrite. |
| 7 | `ink.signature` + `signature_digest` (repr → sha256) | 15.4 | 13.9 | Rust target, bundle with #3. |

### Handoff detail for a Rust/Go benchmarking phase

**K1 — settlement kernel + deep-slot liveness.**
`rebuild/pipeline/table.py:485,503,537,576,637,703` and `rebuild/pipeline/settle.py:439,475,491,525,652,682,754,834,1120,1178`.
*Semantics.* In: a `ResolvedSpec` (18 modelled runes = 15 letters + 3 ligatures, 1-4 stances each) plus a feature set naming one of six acceptance configurations. Out: the emitted window set (665,875-724,875 rows/config), the settlement rule set (2,642-2,950 rules/config), treaty rows (3,563), and the fired-pointer provenance deltas. The core is a worklist fixpoint over `(reachable left state x rune) x right1 x right2 x right3-when-live x right4-when-live`, each slot drawn from a 19-option alphabet (15 letters + EDGE/SPACE/ZWNJ/NAMER_DOT); the deep-slot liveness filters exist precisely to stop the 19^4 = 130,321 product being enumerated. `Engine.transition_trace` is the kernel, memoised on a 10-tuple of `str|None` plus four `RightToken` records; `Engine.candidates` ranks stances by refusal/pairing/entry/acceptor predicates and is called from the fixpoint, the closure, `_prospect`, and every `_prefer_favors` vote.
*Data.* `glyph_data/runes/*.yaml` (18 files, 51 KB) → `rebuild.pipeline.spec_load.load_default_spec()`. Extracted kernel inputs already sitting on disk: `raw/perf/attr-overhead/data/memo-keys.tsv.gz` (real memo keys), `raw/perf/attr-overhead/data/candidate-fields.tsv`. Serialised outputs to diff against: `rebuild/out/m1/windows-<config>.tsv.gz`, `trace-memo-<config>.ndjson.gz`.
*Python baseline (all M).* Default config, no trace store: **77.51 CPU-s** / 682,842 windows / 3.85 GB peak RSS. All six configs cold: **337.87 s**; warm: **194.86 s**. Per build: 9,161,481 `candidates`, 9,394,188 `_prospect`, 2,428,420 `transition_trace`, 9,786,077 `_prefer_favors` calls. Constant factors: frozen dataclass construct 375.4 ns vs tuple 32.0 ns; hash 107.0 ns vs 21.2 ns. Memory: 257.3 B per memo key against ~46 B packed; 97.8 B per window row against 28 B packed.
*Saved/day.* 1,263 foreground + 1,497 background.

**K3 — placed-ink layer.** `rebuild/review/ink.py:53,87,144,176`.
*Semantics.* In: a shaped run (glyph-name sequence plus `(x_offset, y_offset, x_advance)` triples from HarfBuzz) and a static per-glyph decomposed-outline table. Out: either a delta tuple (`config_diff`: `Counter` subtraction over placed outlines) or a 32-byte digest (`signature_digest`). Nothing rasterises and no bitmap is walked anywhere — it is nested Python point tuples, 42.8 points/glyph mean in M1.otf and 52.8 in the Senior font.
*Data.* Already extracted: `raw/perf/attr-overhead/data/shaped-runs.jsonl`, `outlines-before.json`, `outlines-after.json`, `coord-types.json`.
*Python baseline (M).* `translate_outline` 14.46 µs/call, **334.1 ns/point**, 399,938 calls in 5.782 CPU-s; `config_diff` 173.9 µs/call, 0.918 ms/unit, 4.63 calls/unit; `signature()` 202.1 µs/row; `signature_digest()` 103.2 µs/row of which `repr()` is 98.8 and sha256 is 3.4. Volume: ~237 M point tuples rebuilt in the per-unit phase, plus ~59 M in the signature pass; 1.42 GB of `repr` text hashed per cold build.

**K5 — TSV parsing.** `rebuild/validation/rowmodel.py:63`, `rebuild/review/audit.py`, `rebuild/pipeline/baseline_subset.py:51-65`.
*Data.* `raw/perf/attr-overhead/data/baseline-rows.tsv` (extracted), `rebuild/out/m1/divergence-audit.tsv` (59 MB, 292,098 rows).
*Python baseline (M).* `Row.from_tsv` **1,809.2 ns/row** over 54,240 real rows; `audit.load_audit` 292,098 rows in 1.51 s; `baseline_subset.filter_table` 3.11 CPU-s per 4,985,767-row table x 11 tables. Rust TSV parsers land at 50-100 ns/row, so 18-36x. Note the parallel build pays the subset parse **six to twelve times over** — sharing a parsed table via mmap would beat rewriting the parser.

---

## 6. What is provably not rewritable

Measured seconds per day, against the 3,961 s/day combined machine time.

| Floor | Wall s/day | CPU s/day | Evidence |
|---|---:|---:|---|
| **HarfBuzz shaping** (uharfbuzz → `hb_shape_full` → GSUB apply) | **~220** | ~940 | `make test` 1,578 CPU-s/run at 69.4% of the tree (two live-worker samples + instrumentation of all 88,448,272 calls); gate:conform 10.4% of the marginal sweep (L4-minus-L3 differencing with `process_time` around every shape); census 15.81% of leaf samples; surface 5.84 CPU-s/cold build. **The single largest irreducible floor.** Substituting rustybuzz would be a port of the same algorithm and would destroy the gate's premise — that an *independent* shaper agrees. |
| **fontTools feaLib / otlLib / ttLib** | ~17 | ~37 | 14.509 CPU-s per `make all` = **62.6% of it** (boundary instrumentation; cross-checked at 63.4% by cProfile redistribution and 64.3% by a sampling thread). 50.2% of `make all` is `addOpenTypeFeaturesFromString` alone, superlinear in the 1,345 lookups / 9,555 subtables / 384,260-byte GSUB this repo asks for. Reaching it means writing a font compiler. |
| **CPython's own C: `_json`, zlib, `_hashlib`** | ~5 | ~14 | Whole 265 MB surface `json.dumps` 0.79 s / `loads` 1.05 s (C encoder at 282-380 MB/s); zlib 1.02 CPU-s/build; sha256 at 2,731 MB/s = 0.52 s for all 152,730 digests. **serde_json would save ~0.6 s of a 125 s cold build.** JSON is not the prize here. |
| **Disk I/O** | <5 | ~11 | `io_or_wait` buckets are 0.0-1.5% in every sample. Reading all 27 shards' *bytes* is **0.015 s** against 1.080 s to decode them — it is pure decode, not I/O. The 810 MB `rebuild/out` tree and the APFS `cp -Rc` snapshot are the only large disk terms; the snapshot is **U**. |
| **node — gate:js** | 1.4 | 5.6 | 0.104 s wall / 0.432 CPU-s for seven test files. Noise. |
| **typst** | 0.24 | 0.24 | 0.0917 s wall per `make all`, 0.4% of it. Already Rust, and 71 lines of `.typ`. |
| **pyright** | **0.0** | 5.5 | 5.78 s wall / 9.90 CPU-s over 156 files, but it is spawned by `conftest.pytest_configure` **concurrently with the 11.24 s `make all` and finishes inside it** (M). It contributes **zero** to any wall critical path and caps nothing. A rewrite changes it by exactly zero seconds. |
| **Process spawn + interpreter startup** | ~1.5 | ~6 | 73-87 Python processes per cold cycle; bare interpreter 15.1 ms, the `uv` wrapper adds 10.8 ms. **Deleting `uv` entirely would save 0.162 s.** |
| **Total floor** | **~250** | **~1,019** | **6.3% of the day's wall, 24% of its CPU.** |

One more thing that is *not* a floor but behaves like one: **~22 s of interpreter teardown after the six-config M1 run's last line of output** (M: 360.01 s process real against 337.87 s of measured work), and 13.4 s after the default-config run — the cost of freeing a 7.46 GB object graph, which no profiler attributes to anything. A Rust core with a packed working set (measured: 46 B/memo key against 257.3, 28 B/window against 97.8) removes this by removing the graph.

---

## 7. Open questions that block a rewrite decision

1. **gate:conform has never been measured end to end, on any machine.** My 336 s/pass is a **derived floor**, from the marginal 168 µs/text at length 4, excluding witness top-ups — which were *over half* of the July artifact's 14,335,037 shaping runs. The true figure could be 2-3x higher. This is 25% of the combined day and it is the largest hole in the model.
2. **The verdict plumbing chain's "~23 s" and complaints' "~3 s" come from `artifact_cycle.py`'s docstring and have never been measured anywhere.** They carry 208 s/day (5% of the foreground) in this model.
3. **`rebuild/out/cycle-timings.ndjson` still does not exist on this machine.** One `make artifact-cycle` closes questions 1 and 2 and turns every commit-message-cited figure into a measured one on this hardware. WHATNEXT.md's own rule applies: "Slow enough is measured, not remembered."
4. **Nobody has measured whether a Rust settlement kernel actually gets 10x on *this* workload.** The constant-factor microbenchmarks and the memory model support the 10-40x band; no port exists. K1's data files and baselines above are exactly what a benchmarking phase needs to close this.
5. **Can `build_tables` be parallelised at all?** It is serial *by design* because the cross-configuration `TraceShare` must live in one process — and the share is worth a lot (it carried 1.3-2.0 M entries per recipient config on the cold run). A rewrite fast enough to change the calculus should re-ask whether the share still pays.
6. **A rewrite competes against work avoidance that has already taken most of the cold cost.** The trace memo plus the cross-config share take the table stage 337.9 → 194.9 s; the unit cache takes the surface 35.6 → 13.6 s wall and 143.1 → 13.5 CPU-s. What is left for a rewrite is the *fresh* slice, and twelve commits of this repo's recent history were spent shrinking exactly that.
7. **`--conform-horizon` may be a cheaper lever than any rewrite** for the largest background term — WHATNEXT.md already names it. Likewise `max_chars_after` in the calt sweep: the after dimension is 1 + 46 + 46² = 2,163 combinations and would fall to 47, a **46x cut** in `make test`'s 88.4 M shapes. Both are coverage decisions, not performance ones, and both move numbers no language change can reach.
8. **`make review`, `make complaint-docket` and `make novelty-order` have no green record, no skip proof, and no attribution slice.** `make review` alone re-derives everything on every invocation for 21.4 s.

---

## Evidence

Raw evidence re-read and spot-checked for this synthesis:

- `raw/perf/calibrate/` — `calibration.json`, `m1-all-fresh.txt`, `m1-all-fresh.time.txt`, `m1-all-warm.txt`, `m1-all-warm.time.txt`, `review-cold.err`, `review-warm.err`, `make-test-force.summary.json`, `make-test-skip.summary.json`, `make-all.summary.json`, `verdict-ready.summary.json`, `skipcosts.json`
- `raw/perf/attr-m1/` — `rollup.json`, `bucket-build-tables-default.json`, `pysample-build-tables-default.json`
- `raw/perf/attr-overhead/` — `weighted-split.json`, `kernels-best.json`, `sample-buckets-all.json`, `gc-ab.txt`, `gc-experiment.txt`, `serialization.json`, `spawn-tax.json`, `memory-model.json`
- `raw/perf/attr-surface/` — `build-j1-cold.stderr`, `build-j1-warm2.stderr`, `build-j6-cold.stderr`, `reconcile.txt`, `ink-bench.txt`, `signature-bench.txt`, `split-bench.txt`, `native-split.txt`, `config-badge-memo.txt`
- `raw/perf/attr-tests/` — `budget.txt`, `sample-workers-buckets.txt`, `sample-sweep-serial-buckets.txt`
- `raw/perf/attr-fontbuild/` — `final-budget.txt`, `lever-experiments.txt`, `yaml-bench.txt`
- `raw/perf/m1-pipeline/` — `timing-default.json`, `memo-invalidation.json`, `stage-bench.log`, `stage-bench-flagsoff.log`
- `raw/perf/build-topology/` — `t-census-check.txt`, `t-status-components.txt`, `sample-census.txt`
- `raw/perf/review-surface/` — `config-badge-cardinality.txt`

Produced by this synthesis: `raw/perf/synthesize/model.py` (the weighting arithmetic), `raw/perf/synthesize/model-out.json`, `raw/perf/synthesize/kernels.json`, `raw/perf/cost-model.json`.
