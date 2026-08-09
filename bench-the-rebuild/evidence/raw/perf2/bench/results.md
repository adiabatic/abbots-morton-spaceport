# Bench phase: the measured Rust and Go multipliers on this repo's workloads

Every runner from the Build phase was re-run on an **exclusive box** (load average 1.9-3.4 throughout, which is this desktop's idle baseline; the Build phase's numbers were all taken under 80-100% sibling load and are superseded). Six runners, all completed, none failed.

Numbers are tagged **M** measured here, **D** derived from measured inputs, **C** cited from the cost model, **U** unmeasured.

---

## The one number

**Rust is 20.1x the shipped Python on the settlement fixpoint, single-threaded, and 96x with six-way parallelism** — measured on a model, not on the real kernel, and the fidelity gap cuts in the port's favour.

| | Python | Rust | Go |
|---|---:|---:|---:|
| Six configs, serial, wall (M, median of 3) | 181.60 s | **9.05 s (20.1x)** | 11.29 s (16.1x) |
| Six configs, best parallel form, wall (M) | 181.60 s | **1.88 s (96.5x)** | 2.50 s (72.6x) |
| One config, serial, wall (M) | 29.51 s | 1.62 s (18.2x) | 1.96 s (15.1x) |

**Fidelity discount, stated: the model's Python is 0.754x the real kernel's cost per kernel operation (M, median of 3 reps; 0.733-0.772).** The real kernel costs **1.33x more per operation** than the model's Python does. Because the missing third is Python-side overhead a Rust port would either not pay at all or pay at Rust speed, the direction of that gap makes 20.1x a **floor**, not a ceiling: the honest band is **20-27x single-threaded**, and the conservative figure to carry into the report is **20x**.

The 10x floor the cost model's Amdahl table assumed is comfortably beaten. The 50x top of its assumed band is not reached single-threaded, and is only reached by adding parallelism.

**But the denominator matters more than the multiplier.** The `python-levers` runner measured, on this same clean box, at full scale, with a byte-identical artifact: the real M1 kernel goes from **81.25 to 34.02 CPU-s in pure Python** (2.39x, M). Against that levered baseline the Rust advantage is **8.4x single-threaded**, not 20x. See "What this means for the decision" below.

---

## 1. Sanity checks on the runners

I checked each runner for the four failure modes named in the brief before trusting any number.

| Check | Verdict |
|---|---|
| **Equivalence actually ran and passed** | Yes, everywhere. k1-meso: all 6 configs x 12 variants agree on windows/cells/checksum in **all 3 reps** (default config checksum `2964411847154100471`), and the four kernel call counters match to the digit across Python/Rust/Go. k1-micro: 15 cross-language checks, `all_match: true`. k3-k5: every K3 variant prints `21fc76ce…`, every K5 variant matches its kernel's checksum, and the K3 checksum is reproduced a sixth time by a live uharfbuzz + fontTools replay. compilers: all 8 variants produce `combined_sha256 = 92f7ab25…` at k9. python-levers: all 11 bounded-slice arms and all 4 full-scale arms produce identical artifact digests. freethreaded: 19 runs, 0 mismatches. |
| **Rust built `--release`** | Yes, all four Rust trees: `cargo build --release` with `[profile.release] opt-level = 3, lto = true, codegen-units = 1, panic = "abort"` (k3-k5's k5 crate omits `panic = abort` only). Verified by reading `run.sh` and every `Cargo.toml`. Go is a plain `go build` with no non-default flags anywhere. |
| **Results consumed (no dead-code elimination)** | Yes. k1-meso folds every emitted window row into a printed FNV checksum, and additionally folds every generated elimination/note **string** into a printed `text_sink` (Rust and Go agree at `9460885704897709523`, proving the string formatting actually executed). k1-micro uses `std::hint::black_box` + escaping heap stores in Rust and package-level sinks + `runtime.KeepAlive` in Go, and verifies every accumulator by checksum. k3-k5's timed loops produce the very lines that get hashed. compilers' variants each materialize both tables and SHA-256 them. |
| **Same workload size in every language** | Yes. k1-meso passes one `L` to all three binaries and all three report identical window counts. k1-micro's `M8/NPROBE/NALLOC/REPS` constants are identical in `bench.py`, `main.rs` and `main.go`. Timing regions are symmetric (spec load excluded in all three; the fixpoint included in all three). |

**Asymmetries I found and how they bias:**

- k1-meso's `cpu_seconds` for Rust/Go comes from `/usr/bin/time` (whole process, including spec parse and startup) while Python reports its own in-process `process_time` for the fixpoint only. This charges the ports extra. I report **wall-vs-wall inside the fixpoint** for every headline, which is symmetric.
- k1-meso's memo microbenchmark computes `key_for()` (a splitmix64 plus key construction) **inside** the timed loop in all three languages. That is faithful to the real kernel — it constructs the key on every probe — but it means the "1,375 ns Python vs 35.4 ns Rust" figure is *construct + hash + probe*, not probe alone. It is labelled as such below.
- k1-micro's Python map-insert row grows a dict from empty while the Rust/Go rows are presized. Both variants exist in `all_results`; the headline row is the one where **Python wins** (see §4).

**Problems I found that the runners' own authors did not, or got wrong.** These are in §6.

---

## 2. Independent anchor: is the Python side of the ratio real?

I ran the **actual** `rebuild.pipeline.table.build_tables` under `/usr/bin/time -l`, not any model, at two scales.

| | wall | CPU | peak RSS | windows | rules | treaty | cells | digest |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Full 18 runes, default config (M) | 81.82 s | **81.36 s** | 4.045 GB | 682,842 | 2,667 | 3,563 | 197 | `f0abcf17…` |
| Bounded 9-rune slice (M) | 3.16 s | 3.14 s | 0.201 GB | 32,222 | 267 | 490 | 58 | `3b227f59…` |

The full-scale run reproduces the cost model's default-configuration artifact **exactly** (682,842 / 2,667 / 3,563 / 197). Process-level `/usr/bin/time` shows 86.10 s real against 81.82 s of `build_tables`, i.e. **4.3 s of import, spec load and teardown** outside the measured window.

k1-meso's own calibration ran the same kernel three more times: **78.63 / 79.15 / 80.94 CPU-s** (median 79.15, spread 2.9%). Against the cost model's exclusive-box 77.51 CPU-s that is **+2.1%**. The Python side of every ratio here is anchored in reality; this box and the calibrate box are the same machine to within noise.

### The fidelity ratio, prominently

| | model (python-baseline) | real kernel | ratio |
|---|---:|---:|---:|
| windows | 428,971 | 682,842 | 0.628 |
| `candidates` calls | 4,903,617 | 9,161,481 | 0.535 |
| CPU-s | 29.98 | 78.63 | — |
| **µs per kernel operation** | **1.915** | **2.482** | **0.754** (M, median of 3: 0.733/0.754/0.772) |
| µs per `candidates` call | 6.11 | 8.58 | 0.696 |

Call **mix** is close: `_prospect`/`candidates` 0.817 model vs 1.025 real; `_prefer_favors`/`candidates` 1.030 vs 1.068; `transition_trace`/`candidates` 0.345 vs 0.364. So the model is a smaller instance of the same shape, but **each of its operations is a third cheaper in Python than the real thing's**.

### Is the multiplier an artifact of one model size?

Not for Rust. I re-ran one config at three spec sizes (checksums agree at every size):

| letters | windows | Python | Rust | Go | Rust x | Go x |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 75,290 | 5.22 s | 0.253 s | 0.286 s | **20.7x** | 18.3x |
| 12 | 144,963 | 10.52 s | 0.527 s | 0.649 s | **20.0x** | 16.2x |
| 15 | 428,971 | 29.46 s | 1.489 s | 1.964 s | **19.8x** | 15.0x |

Rust is flat at 19.8-20.7x across a 5.7x change in problem size. **Go is not**: it decays monotonically from 18.3x to 15.0x as the working set grows, which is Go's GC scanning a bigger live heap. The real kernel is 1.6x larger again than the largest instance here, so **Go's real-kernel multiplier should be read as an upper bound**; Rust's should not.

---

## 3. The final multiplier table

All figures **M** unless tagged. Ratios are Python ÷ port.

### K1 — the settlement fixpoint (k1-meso, median of 3 reps)

| Variant | wall (s) | spread | vs python-six |
|---|---:|---:|---:|
| python-one | 29.51 | 2.4% | — |
| python-six (serial, with share) | 181.60 | 4.7% | 1.00x |
| rust-one | 1.62 | 6.1% | — |
| **rust-six (serial, share)** | **9.05** | 9.2% | **20.07x** |
| rust-six-noshare (serial) | 9.41 | 4.0% | 19.30x |
| rust-six-par (donor then 5 threads, RwLock share) | 3.37 | 10.9% | 53.81x |
| **rust-six-par-noshare (6 independent threads)** | **1.88** | 3.4% | **96.46x** |
| go-one | 1.96 | 5.1% | — |
| go-six (serial, share) | 11.29 | 1.7% | 16.08x |
| go-six-noshare | 12.16 | 1.4% | 14.94x |
| go-six-par (donor then 5 goroutines) | 4.28 | 6.7% | 42.43x |
| go-six-par-noshare | 2.50 | 4.8% | 72.55x |

Spreads are 1.4-10.9%; only `rust-six-par` exceeds 10%, which is expected for a five-thread fan-out whose critical path is the slowest recipient. **Parallelism alone** (port vs port): Rust 2.68x behind a serial donor, **4.81x** with six independent fixpoints; Go 2.64x / 4.51x.

Memory (M, six configs): Python 4.73 GB peak RSS, Rust 0.39 GB serial / 2.30 GB six-threaded, Go 0.69 / 2.60. The ports' serial six-config footprint is **12x smaller** than Python's.

### The memo sub-question

2,428,420 lookups against a 900,000-entry memo, same splitmix64 key stream, same printed checksum (`546831693182`) in all three.

| | ns per lookup (M, median of 3) | vs Python | bytes/entry |
|---|---:|---:|---:|
| Python (10-tuple of `str\|None` + 4 frozen dataclasses) | **1,367.0** | 1.0x | 254.2 B (M) |
| Rust (10-byte packed struct, hand-rolled Fx map) | **35.8** | **38.2x** | 10 B |
| Go (10-byte packed struct, built-in map) | **58.3** | **23.4x** | 10 B |

The 254.2 B/entry independently reproduces the cost model's 257.3 B/memo-key on a completely different code path. This includes key construction, which the real kernel also pays per probe.

### K1 primitives (k1-micro, raw ns/op, 15 equivalence checks all passing)

| Operation | Python | Rust | Go | Rust x | Go x |
|---|---:|---:|---:|---:|---:|
| loop skeleton (the interpreter-dispatch floor) | 29.16 | 1.63 | 1.83 | 17.9x | 16.0x |
| construct 8-field record (frozen dataclass) | 422.01 | 3.45 | 4.56 | 122x | 92x |
| construct 8-field record (Python: plain tuple) | 40.86 | 3.45 | 4.56 | 11.8x | 9.0x |
| construct 5-field `settle.Candidate` | 302.45 | 0.86 | 2.62 | 353x | 115x |
| hash 8-field record | 112.70 | 11.24 | 31.52 | 10.0x | 3.6x |
| equality, equal records | 116.33 | 9.80 | 6.21 | 11.9x | 18.7x |
| **map insert, 10-slot string key** | **76.32** | **153.82** | **170.25** | **0.50x** | **0.45x** |
| map lookup, 10-slot string key | 378.71 | 169.94 | 228.59 | 2.2x | 1.7x |
| map insert, packed-u64 key | 106.63 | 4.82 | 59.01 | 22.1x | 1.8x |
| map lookup, packed-u64 key | 318.70 | 7.41 | 21.16 | 43.0x | 15.1x |
| rank a 3-8 candidate list (the real sort predicates) | 879.30 | 24.49 | 66.92 | 35.9x | 13.1x |
| alloc + drop a small object | 417.03 | 12.20 | 17.23 | 34.2x | 24.2x |
| filter a 700k-row table | 23.61 | 1.10 | 1.16 | 21.4x | 20.4x |

The **weighted composite** over the measured call mix gives Rust **10.7x** and Go **13.5x** — but it is **ESTIMATED**, its recipe reproduces only **10.1%** of the measured six-config build, and its Go > Rust inversion is a weighting artifact. Treat it as a floor-level cross-check on the meso model, not a second measurement.

### K3 — placed-ink layer (k3-k5, least-contended of 3 passes, spread 1.03x)

3,000 real shaped runs; every variant prints `21fc76ced6d5f83c…`, including a live-fonts replay through uharfbuzz + fontTools.

| Variant | µs/row | x vs repo Python |
|---|---:|---:|
| python-baseline (the repo's own `InkComparator`) | 281.70 | 1.00x |
| python-optimized-digest (marshal v2 instead of `repr`) | 219.07 | 1.29x |
| **rust-single** | **16.90** | **16.67x** |
| rust-parallel (8 threads) | 3.09 | 91.08x |
| **go-single** | **18.49** | **15.23x** |
| go-parallel (8 threads) | 6.65 | 42.39x |

With the HarfBuzz floor added back (22.17 µs/row of `hb.shape` no rewrite may remove — **7.3%** of this kernel): whole-kernel Rust single **7.78x**, Rust 8-thread 12.03x, Go single 7.47x, against a **13.71x ceiling** with the arithmetic driven to zero (D from two measured values). `translate_outline` alone: Python 229.6 ns/point, Rust 11.4, Go 12.9 (**20.1x**).

The in-Python lever is confirmed: `signature_digest` is 93.08 µs/row of which `repr()` is **89.92 (96.6%)** and sha256 is 3.09. Swapping to sha256-over-`marshal.dumps(sig, 2)` gives 33.47 µs/row (**2.78x**) with an identical induced partition (631 values → 631 digests both ways).

### K5 — TSV parsing (k3-k5)

| Kernel | repo Python | best Python rewrite | Rust | Go | Rust x |
|---|---:|---:|---:|---:|---:|
| `Row.from_tsv` (54,240 rows) | 2,744.9 ns/row | 1,585.4 (1.73x) | 278.0 (borrowed) | 404.2 | **9.87x** |
| `audit.load_audit` (292,098 rows) | 1,414.4 ns/row | 640.0 (2.21x) | 318.4 | 370.3 | **4.44x** |
| `filter_table` (4,985,767 rows) | 2.217 s | 1.153 (1.92x) | 0.240 s | 0.421 s | **9.23x** |

Well under the cost model's cited 18-36x for Rust TSV parsers, because `str.split` and `int()` are already C. And the non-rewrite lever wins outright: **one mmap-shared parsed pack beats every port** — 6 spawn workers each re-parsing is 1.444 s wall / 7.754 worker-CPU-s, one shared pack is 0.116 s / 0.312 s = **12.5x wall, 24.8x CPU**, same checksum; at 12 workers **14.4x / 51.2x**.

### Keep-the-Python accelerators (compilers; k9 subset, median CPU-s per build, n=3-6, all artifacts byte-identical)

| Variant | CPU-s | x |
|---|---:|---:|
| cpython-baseline | 5.398 | 1.00x |
| cpython + `gc.freeze()`/`gc.disable()` | 4.504 | 1.20x |
| cython pure-Python mode | 4.638 | 1.16x |
| mypyc settle+table | 4.435 | 1.22x |
| mypyc all four modules | 3.902 | 1.38x |
| mypyc all + gc off | 2.954 | 1.83x |
| **PyPy 3.11** | **2.113** | **2.56x** |

**None reaches 10x.** The full-scale cross-check in that runner's output (CPython 81.5 / mypyc 55.2 / PyPy ~37.7 / Cython 76.2 CPU-s) is **cached from an earlier out-of-band run, not re-measured by me** — I flag it rather than quote it as mine.

### Free-threaded CPython 3.14t (freethreaded; 19 runs, 0 checksum mismatches)

On the real six-configuration shape: GIL serial 13.06 s → free-threaded 6 threads with a private spec **4.11 s = 3.18x** (M), at 5.07x CPU inflation. Keeping the `TraceShare` (donor then 5-way fan-out) reaches only **1.60x**. `gc.freeze()+gc.disable()` is worth **16.8%** on the GIL build and 5.2% on 3.14t, and the free-threaded single-thread advantage **reverses** once the collector is off (1.088x → 0.956x) — so the "free" single-thread win is the garbage collector, not free-threading. Projected onto the 337.9 s production stage: **2.31x** dropping the share, 1.60x keeping it (**D**).

### Work avoidance (cut-the-work; no language port here)

Newly measured on a clean box, and it changes the previous phase's story: gate:conform's six-config pool wall is **208.6 s at horizon 3, 194.3 s at horizon 4, 230.1 s at horizon 5**, and the gate passes (0 divergences, 0 uncovered rules, 0 uncovered transitions) at all three. **`--conform-horizon` is nearly flat** — the witness top-up shrinks as fast as the sweep grows (h3: 370.6 s sweep + 809.9 s top-up; h5: 951.7 + 350.0), so lowering the horizon buys almost nothing today. The `max_chars_after` lever is the real one: the 17 sharded sweeps are **1,995.7 CPU-s** of `make test`'s ~2,212, at a measured 103,824 shaped strings per shard against 2,256 at depth 1 (**46.0x**).

---

## 4. Things that are not what they look like

- **Python beats both ports on `map insert, 10-slot string key`** (76 ns vs 154/170). Real, and it is CPython's cached string hashes plus a pointer-identity fast path against Rust/Go hashing ten strings byte-by-byte. It is also the strongest argument for the packed key: the same insert on a packed u64 is 4.82 ns in Rust — **32x faster than Python's best row**. A port that keeps strings as keys loses; a port that packs wins enormously.
- **A hand-rolled FxHash finalizer is a 600x footgun** (k1-micro's caveat, which I did not re-verify but which is recorded in that runner's output): a bare `finish() { self.hash }` put a packed-key insert at 25,744 ns/op because this key's low bits are a 5-value alphabet. The shipped harness uses a splitmix finalizer. Anyone porting must check this.
- **`make test`'s Amdahl ceiling is unchanged.** Nothing here moves it; 70.6% of it is HarfBuzz and no measurement in this phase touches that.

---

## 5. What this means for the decision

Three numbers, all measured on this box, at full scale where possible:

1. A Rust port of the settlement kernel is worth **~20x** single-threaded and **~96x** with six-way parallelism, against the **shipped** Python.
2. Ordinary Python changes — six memoizations, one `NamedTuple`, `gc.freeze()`/`gc.disable()` — are worth **2.39x** on the **real** kernel at full scale (81.25 → 34.02 CPU-s, identical artifact digest `3026eaf52b…`, M).
3. Free-threading is worth **~2.3-3.2x** for ~50 lines, and PyPy **2.56x**, and they do not compose with each other.

So against a **levered** Python baseline the Rust port is **8.4x**, not 20x — and with parallelism ~40x. The cost model's Amdahl row "10x buys 8.81x on M1" was computed against the shipped baseline; the levers alone move the shipped baseline most of the way to where a 10x rewrite would have put it. **The rewrite is real and large, but roughly half of the prize is available in pure Python first, and taking it first is the only way to know what the rewrite is actually worth.**

---

## 6. Every methodological problem I found

Fixed where I could; named where I could not.

1. **The fidelity gap is 3x wider than the Build phase claimed.** It reported 0.904 cost-per-kernel-op; I measure **0.754** (median of 3, range 0.733-0.772). On a clean box the model's Python got faster (35.68 → 29.98 CPU-s) while the real kernel barely moved (79.88 → 78.63), so the gap opened. *Not fixable — reported, and the headline band widened from 19-23x to 20-27x.*
2. **"The share stops paying in a packed rewrite" is refuted as a language finding.** I ran the with/without-share pair in **all three** languages at L=12 (identical per-config checksums both ways): the model's cross-configuration share is worth **1.7% in Python, 4.1% in Rust, 7.0% in Go**. It never paid in any language, so the model cannot price the trade-off. What survives is the structural fact — a serial donor caps parallelism at 5-way, and dropping it is worth a measured **1.79x** in Rust. *Fixed by measuring; conclusion corrected.*
3. **k1-meso's `cpu_seconds` is asymmetric** — `/usr/bin/time` whole-process for Rust/Go, in-process fixpoint-only for Python. *Fixed by reporting wall-vs-wall for every headline.*
4. **k1-meso's memo benchmark times key construction inside the loop.** Faithful to the kernel, but the number is construct+hash+probe, not probe. *Fixed by labelling.*
5. **k1-micro's weighted composite reproduces only 10.1% of the real build** and inverts Go above Rust. *Not fixable — reported as ESTIMATED with its coverage ratio, and not used for any headline.*
6. **compilers' full-scale cross-check is read back from a cached earlier run**, not measured by this phase; its absolute seconds were taken under contention. *Not fixed (would cost ~4 more minutes of full-spec builds for a non-headline result) — flagged, and the k9 numbers it reports ARE freshly measured here.*
7. **The generated spec's condition contents are synthetic.** Its *shape* is copied from the real spec by introspection, but a real rune file's family masks and scope conditions have structure a PRNG does not, and that could shift branch prediction either way. *Unmeasured and unmeasurable without writing the real port. This is the largest residual risk in the headline.*
8. **The Go multiplier is volume-dependent and the Build phase treated it as flat.** 18.3x → 15.0x across a 5.7x volume change. *Fixed by measuring three sizes; Go's figure is now stated as an upper bound.*
9. **k1-micro's Python map-insert row grows from empty while Rust/Go are presized.** Both variants are in `all_results`. *Not a fix needed — the headline row is the one Python wins, so the asymmetry runs against the ports.*
10. **The K3 whole-kernel Amdahl figures are DERIVED**, combining a measured arithmetic pass with a separately measured HarfBuzz floor. *Labelled.*
11. **cut-the-work has no language port at all** and makes no rewrite claim. Its horizon numbers here contradict the previous phase's contended ones in a decision-relevant way (the horizon is nearly flat, not a lever). *Reported as a new measurement.*
12. **No runner failed and none was missing.** All six ran to completion on the exclusive box.

---

## Raw files

Everything under `raw/perf2/bench/`:
`results.json`, `results.md`, `reps/k1-meso.rep{1,2,3}.json` (+`.err`), `k1-micro.json`, `k3-k5.json`, `compilers.json`, `python-levers.json`, `freethreaded.json`, `cut-the-work.json`, `anchor-full.json`, `anchor-full.time`, `anchor-k9.json`, `anchor-k9.time`, `anchor.py`, `k1-scale.json`, `share-experiment.json`, `assemble_results.py`.
