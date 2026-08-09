# Is Python what makes this slow, and would Rust or Go help?

The decision package. Every number carries a status tag: **M** measured on this machine in this workflow, **D** derived from measured inputs, **C** cited from a docstring/commit and never measured, **U** unmeasured. Where an earlier phase's claim was refuted, it is stated as refuted rather than quietly dropped.

Machine: Apple M4 Pro, macOS Darwin 25.6, 12 logical cores (8P + 4E), CPython 3.14.6 (GIL), Go 1.26.5, Rust 1.97.1. Rust built `cargo build --release` with `opt-level = 3, lto = true, codegen-units = 1, panic = "abort"`; Go built with a plain `go build`, no non-default flags.

---

## 1. The answer

Yes, Python is what makes this slow — but only for one stage, and the stage is slow for two compounding reasons of which the language is the smaller. The M1 decision-table settlement fixpoint (`rebuild/pipeline/table.build_tables` driving `settle.Engine`) is 337.9 s cold / 194.9 s warm (M, confirmed from raw calibration files), strictly single-threaded (M, confirmed from the code: no `jobs` parameter reaches `build_tables` and no concurrency import exists in `table.py`/`settle.py`/`trace_memo.py`/`specificity.py`), and it is CPython to within the 0.61% floor of unattributed profiler samples (M) — no HarfBuzz, no zlib, no JSON. A Rust reimplementation of that fixpoint is genuinely worth **20.1x single-threaded** against the shipped Python (M, median of 3 full reps on a structurally faithful model; independently reproduced at 19.9x by a second agent, with all six configuration checksums *and* all four kernel call counters matching Python to the digit), and 20x is a **floor** rather than a ceiling because the model's Python is 0.754x the real kernel's cost per kernel operation and the missing third is interpreter overhead a port would erase — the honest band is 20-27x. Go is 16.1x and, unlike Rust, its multiplier decays with problem size (18.3x → 15.0x across a 5.7x volume change) so its real-kernel figure is an upper bound. **But 20.1x is against a baseline nobody should be comparing to.** Six ordinary memoizations, one `NamedTuple` and `gc.freeze()/gc.disable()` take the *real* kernel from 78.93 to 34.66 CPU-s at full 18-rune scale with a byte-identical artifact digest (2.28x, M), and adding mypyc on the same four modules takes it to 20.41 CPU-s (3.87x, M, 182/182 repo kernel tests passing). Against that stack the Rust port's advantage is **5.2-7.0x**, worth roughly **430-500 machine-seconds a day** on a stage that costs ~2,040 s/day (D) — against 30-60 engineer-days to build and a measured, recurring duplication tax, because the port does *not* retire `settle.py` (conform, `emit_gsub`, `explain` and `probe` all drive it) and the kernel is not finished (23 semantics-touching commits in `rebuild/`'s two-month life, at a flat-to-rising rate). The seconds-per-day case for a rewrite is therefore weak. The case that is *not* weak is scaling: nobody had measured how this workload grows, and it grows as **letters^4.0-4.5 with the exponent rising** (M, new this workflow), so full migration to the registry's 44 codepoint-bearing letters is a 74-127x growth — a 6.9-11.9 hour six-configuration cold build in shipped Python. Read as alphabet headroom at today's 337.9 s wait: shipped Python 15 letters, the Python levers 18, levers+mypyc ~21, Rust serial 29-34, Rust with six-way configuration parallelism 35-44 (D). **So: do the Python levers now — they are measured, byte-identical, and worth more per engineer-day than anything else here — and treat the Rust port as a question whose answer is set by how far the alphabet migration is going, not by seconds per day.** If the migration stops in the low twenties, the levers are sufficient and the port is a mistake. If it is going to 44 letters, every keep-the-Python path is exhausted within the next few migration batches and only a Rust port with configuration parallelism reaches the finish line — and even that arrives with little margin.

---

## 2. The measured multiplier table

Every port row below verified output-equivalent against the Python it replaces, on the same input, with a printed checksum. Equivalence status is stated per row; where it is weaker than byte-identity, that is named.

### 2.1 K1 — the settlement fixpoint (the whole game)

Measured on a structurally faithful **model** of the kernel implemented three times against one shared generated spec. No port of the real kernel exists.

| Variant | Python | Rust | Go | Unit | Rust x | Go x | Equivalence |
|---|---:|---:|---:|---|---:|---:|---|
| 6 configs, serial, with cross-config share | 181.60 | **9.047** | 11.293 | s wall, median of 3 | **20.07x** | 16.08x | Exact: all 6 config checksums agree in all 3 reps, and all four kernel call counters match to the digit |
| 6 configs, serial, no share | 181.60 | 9.41 | 12.16 | s wall | 19.30x | 14.94x | Exact, per-config |
| 6 configs, parallel, donor then 5 threads over a locked share | 181.60 | 3.375 | 4.28 | s wall | 53.81x | 42.43x | Exact, per-config |
| 6 configs, 6 independent parallel fixpoints, no share | 181.60 | **1.883** | 2.503 | s wall | **96.46x** | 72.55x | Exact, per-config — **but see the refutation below** |
| 1 config | 29.514 | 1.623 | 1.960 | s wall | 18.19x | 15.06x | 428,971 windows / 135 cells / checksum `2964411847154100471` in all three |
| Volume sweep, 75,290 windows | 5.225 | 0.253 | 0.286 | s wall | 20.68x | 18.30x | Checksums agree at every size |
| Volume sweep, 144,963 windows | 10.517 | 0.527 | 0.649 | s wall | 19.96x | 16.21x | Checksums agree |
| Volume sweep, 428,971 windows | 29.459 | 1.489 | 1.964 | s wall | 19.78x | 15.00x | Checksums agree |

**Fidelity discount, stated.** The model emits 428,971 windows against the real kernel's 682,842 (0.628x) and makes 4,903,617 `candidates` calls against 9,161,481 (0.535x) — a smaller instance of the same shape, not a smaller-shaped instance. Call mix is close (`_prospect`/`candidates` 0.817 model vs 1.025 real; `_prefer_favors`/`candidates` 1.030 vs 1.068; `transition_trace`/`candidates` 0.345 vs 0.364). Cost per kernel operation: model 1.915 µs vs real 2.482 µs = **0.754** (M, median of 3 reps, range 0.733-0.772). **The real kernel is 1.33x more expensive per operation than the model.** Because that extra third is Python-side interpretive overhead (deeper frozen dataclasses, `specificity.outranks`, the persisted `TraceStore` probe) that a port either skips or executes at port speed, the gap makes 20.1x a floor. **Honest band: 20-27x single-threaded; carry 20x.** *(This corrects the Build phase's claimed 0.904 — the gap is three times wider than it reported, because on a clean box the model's Python got faster while the real kernel barely moved.)*

**Volume independence, checked.** Rust is flat at 19.8-20.7x across a 5.7x volume change; **Go is not** — it decays monotonically as its GC scans a bigger live heap. The real kernel is 1.6x larger again than the largest model point, so Go's real-kernel figure is an upper bound and Rust's is not.

**REFUTED: the 96.5x parallel headline.** The model's six acceptance configurations are nearly degenerate — 6 configs produce only 4 distinct checksums, and dropping the cross-configuration share costs just 5.7% more `candidates` calls. The real share is far more valuable: measured at **1.896x** on the real kernel (M, 8-rune slice, all six configurations, 2 reps, per-config checksums identical: six configs over one `TraceShare` 7.197 s against 13.644 s shareless), and 2.83x on the traced-entry axis (calibrate's counters: 4,388,710 entries traced with the share against 8,049,970 served from it, so a shareless build must trace 12,438,680). Dropping the share to unlock six-way parallelism therefore costs ~1.9x up front on the real kernel, where on the model it cost 4%. **Corrected real-kernel parallel band: 43-71x (D)**, from calibrate's per-configuration walls — 2.13-2.35x keeping the share and parallelizing five recipients behind the serial donor, 3.07-3.54x dropping it for six independent fixpoints.

**Every no-share parallel figure in this workflow inherits that correction** — Rust 96.5x, Rust-with-share 53.8x, Go 72.6x, Go-with-share 42.4x, and free-threading's 3.18x were all measured against a shareless Python baseline that is not what `run_m1.py:104` runs.

### 2.2 K1 — primitives and the memo (k1-micro, 15 cross-language checks, all matching)

| Operation | Python | Rust | Go | Unit | Rust x | Go x |
|---|---:|---:|---:|---|---:|---:|
| Memo lookup on the 10-tuple key (construct + hash + probe) | 1,367.0 | 35.8 | 58.3 | ns/lookup | **38.2x** | 23.4x |
| Memo bytes per entry | 254.2 | 10 | 10 | B/entry | 25x | 25x |
| Construct a 5-field `settle.Candidate` | 302.45 | 0.86 | 2.62 | ns/op | 353x | 115x |
| Construct 8-field record (Python: plain tuple instead) | 40.86 | 3.45 | 4.56 | ns/op | 11.8x | 9.0x |
| Rank a 3-8 candidate list (the real sort predicates) | 879.30 | 24.49 | 66.92 | ns/op | 35.9x | 13.1x |
| **Map insert, 10-slot string key** | **76.32** | 153.82 | 170.25 | ns/op | **0.50x** | **0.45x** |
| Map insert, packed u64 key | 106.63 | **4.82** | 59.01 | ns/op | 22.1x | 1.8x |
| Loop skeleton (the interpreter-dispatch floor) | 29.16 | 1.63 | 1.83 | ns/op | 17.9x | 16.0x |

Two of these actively caution against a naive port. **CPython beats both Rust and Go on inserting a ten-slot string key** (76 ns vs 154 and 170) because it caches string hashes; the same insert on a packed u64 key is 4.82 ns in Rust. **The packing, not the language, is the win.** And a hand-rolled FxHash with a bare `finish()` measured 25,744 ns/op — a 600x blowup against SipHash — because this key's low bits are a 5-value alphabet; a splitmix-style finalizer brings it to 4.9-12.7 ns.

The memo's 254.2 B/entry independently reproduces the cost model's 257.3 B/memo-key on a completely different code path.

**ESTIMATED, not measured:** the weighted composite over the measured call mix gives Rust 10.66x and Go 13.51x. Its recipe reproduces only **10.1%** of the measured six-configuration build and its Go-above-Rust inversion is a weighting artifact. It is a floor-level cross-check that happens to straddle the meso model's answer. It is used for no headline.

### 2.3 The real kernel anchor and the keep-the-Python ladder (all on the REAL kernel, full 18 runes, default config)

| Arm | CPU-s | x vs shipped | Equivalence |
|---|---:|---:|---|
| CPython 3.14.6, shipped (independent `/usr/bin/time -l` run) | 81.36 | 1.00x | 682,842 windows / 2,667 rules / 3,563 treaty rows / 197 cells, digest `f0abcf17…` — reproduces the cost model exactly |
| CPython shipped, clean-box mean of 2 | 78.93 | 1.00x | digest `3026eaf5…` |
| + `gc.freeze()`/`gc.disable()` only | ~71.2 | **1.14x** | Same digest, same counts |
| + six memoizations + `RightToken` NamedTuple only | 43.40 | **1.82x** | Same digest |
| + both | **34.66** | **2.28x** | Same digest; 182/182 repo kernel tests pass |
| mypyc on the four kernel modules + gc off | 42.35 | 1.86x | Same digest |
| **mypyc + levers + gc off** | **20.41** | **3.87x** | Same digest; 182/182 kernel tests pass |
| PyPy 7.3.23 / 3.11.15 (k9 subset) | — | 2.56x | Artifact-identical at full spec |
| Cython pure-Python mode (k9 subset) | — | 1.16x | Artifact-identical |
| Free-threaded 3.14t, 6 threads, private spec | — | 1.65x | 19 runs, 0 checksum mismatches |

**PyPy is DOMINATED and should be dropped from the ranking, not ranked.** On the six-configuration shape the code actually runs it is 1.53x, and **PyPy plus the six memoizations (1.74x) is slower than stock CPython plus the same memoizations (2.13x)** — the memos delete exactly the interpreted work the JIT was earning its keep on — at 4.6x the RSS (1.58 GB vs 0.35 GB on the slice; 5.39 vs 4.05 GB at full spec).

**Free-threading's 3.18x is REFUTED as a real-kernel figure**: its baseline was six configurations with the `TraceShare` switched off. Against the shape `run_m1.py:104` actually runs it is **1.65x**, and it is dominated by mypyc — levers + gc off + 3.14t five-way fanout is 3.046x on 2.8 cores against mypyc + levers' 3.079x on one. Its "free" single-thread win reverses (1.088x → 0.956x) once `gc.freeze/disable` is on: the win was the garbage collector, not free-threading. It additionally cannot install `uharfbuzz` at all (no cp314t wheel; `Py_LIMITED_API` is incompatible with `Py_GIL_DISABLED`) and `fontTools.feaLib.lexer` re-enables the GIL process-wide on import.

**The honest keep-the-Python ceiling on this kernel is 3.87x, single-threaded, on one core, for ~50 lines plus a build step.** Not the 1.4-2.6x an option list drawn from any single slice would suggest — nobody had stacked them until the verification pass did.

### 2.4 K3 — the placed-ink layer

3,000 real shaped runs. Every variant prints sha256 `21fc76ced6d5f83c…`, including a sixth pass replayed live through `uharfbuzz` + fontTools against the shipped fonts.

| Variant | µs/row | x |
|---|---:|---:|
| python-baseline (the repo's own `InkComparator`) | 281.70 | 1.00x |
| python-optimized-digest (marshal v2 instead of `repr`) | 219.07 | 1.29x |
| **rust-single** | 16.896 | **16.67x** |
| rust-parallel, 8 threads | 3.093 | 91.08x |
| go-single | 18.493 | 15.23x |
| go-parallel, 8 threads | 6.646 | 42.39x |

**With the HarfBuzz floor added back (D):** 22.17 µs/row of `hb.shape` no rewrite may remove (7.3% of this kernel) gives whole-kernel Rust single **7.78x**, Rust 8-thread 12.03x, Go single 7.47x, against a **13.71x ceiling** with the arithmetic driven to zero. The per-row port figures underneath are measured; the whole-kernel figures are derived.

**In-Python lever, no port:** `signature_digest` is 93.08 µs/row of which `repr()` is 89.92 (96.6%) and sha256 is 3.09. Swapping to sha256-over-`marshal.dumps(sig, 2)` gives 33.47 µs/row (**2.78x**) with an identical induced partition (631 values → 631 digests both ways). It re-stamps the persisted signature store once. It must **not** be applied to `delta_digest`, whose docstring declares byte identity with `rebuild/standing-approvals.yaml`.

### 2.5 K5 — TSV parsing

| Kernel | repo Python | best pure-Python rewrite | Rust | Go | Rust x |
|---|---:|---:|---:|---:|---:|
| `Row.from_tsv` (54,240 rows) | 2,744.9 ns/row | 1,585.4 (1.73x) | 278.0 (borrowed) | 404.2 | **9.87x** |
| `audit.load_audit` (292,098 rows) | 1,414.4 ns/row | 640.0 (2.21x) | 318.4 | 370.3 | **4.44x** |
| `filter_table` (4,985,767 rows) | 2.217 s | 1.153 (1.92x) | 0.240 s | 0.421 s | **9.23x** |

Well under the cost model's cited 18-36x for Rust TSV parsers, because `str.split` and `int()` are already C. Half of the Rust win on `load_audit` is available by writing the Python differently.

**The non-rewrite lever beats every port here, in pure Python:** one mmap-shared parsed pack against six spawn workers each re-parsing is 1.444 s → 0.116 s wall (**12.46x**, 24.8x on worker CPU; 14.4x / 51.2x at twelve workers), same checksum. **Caveat, measured:** that is the *warm* figure. Cold it is **0.80x** — slower than re-parsing — and building the pack costs 1.686 s. It only pays if the pack is content-keyed and reused.

### 2.6 Work avoidance (no port, no rewrite claim)

- **gate:conform measured end to end for the first time on any machine.** Six-configuration pool wall: **208.6 s at horizon 3, 194.3 s at horizon 4, 230.1 s at horizon 5** (M, clean box), with the gate passing at all three (0 divergences, 0 uncovered rules, 0 uncovered transitions). The cost model's derived floor of 336 s wall / 2,017 CPU-s is superseded by a measured 194-253 s / 1,307-1,425 CPU-s. **The two runs of this experiment disagree materially**: a contended run gave 224.1 / 249.8 / 253.0 s with the h3-vs-h4 ordering flipped. So `--conform-horizon 5→4` is worth somewhere between 3 s and 36 s per gate pass — **10-108 s/day (D)** — neither "flat" nor "a lever". The coupling that makes it nearly flat is real and is the finding: the witness top-up shrinks as fast as the sweep grows (h3: 370.6 s sweep + 809.9 s top-up; h5: 951.7 + 350.0).
- **`max_chars_after` 2 → 1 in the calt sweep is a 46.02x cut** (M, verified two ways: the repo's own `_context_chars()` returns exactly 46 entries, so 1+46+46² = 2,163 falls to 47; and the instrumented shard count is exactly 103,824 as predicted). The 17 sharded sweeps are 1,995.7 of `make test`'s ~2,212 CPU-s — **92%**. **The price is measured and is not zero:** 22 of 215 reachable pair renderings (10.2%) exist only under a two-character suffix, concentrated 18-of-22 in three pairs, with nine of sixteen pairs provably losing nothing today. This is a coverage decision, not a performance one. A tiered variant keeping the eight tests that carry depth-2 dependencies loses nothing today.
- **Interpreter teardown** after the six-configuration M1 build: the cost model's ~22 s is measured at **9.5 s**.

### 2.7 Workloads a rewrite should not touch

- **`make test`**: 70.55% is HarfBuzz (M, two independent methods agreeing at 72.0/72.5%: two live xdist workers sampled, plus `process_time` instrumentation of all 88,448,272 `hb.shape` calls). Amdahl ceiling **1.40x**; quote the band **1.23-1.40x** so the 70.6%-vs-81.6% dispute is visible and shown to be decision-irrelevant. Nothing in this workflow moves it.
- **`make all`**: 62.6% inside fontTools feaLib/otlLib. Ceiling 1.30x.
- **pyright**: zero *marginal* wall — it is spawned concurrently with `make all` inside `pytest_configure` and finishes with >4.5 s of margin. It is 9.6-10.6 CPU-s, and it becomes the critical path the moment `make all` drops below ~5.8 s.

---

## 3. The options, ranked

Ranked by measured seconds saved per day against engineering cost. **Denominator (D):** the M1 stage costs ~2,040 machine-seconds/day (1,063 foreground + ~900-1,050 background), after correcting the arming count from 68% to 41.2% and gate:conform from its derived floor to its measured cost. That whole figure carries ±40% from the cadence assumption, and the assumption is itself unverified — see §7.

| # | Option | s/day saved (total) | s/day saved (foreground) | Engineer-days | Risk | Reversibility | Verdict |
|---:|---|---:|---:|---|---|---|---|
| 1 | `gc.freeze()` + `gc.disable()` at `run_m1`'s entry | **252** | 131 | 0.5-1 | Low. Peak RSS +15% (3.77 → 4.34 GB); nothing reclaims cycles for the run's duration | Trivial — ~5 lines | **DO IT.** 1.14x measured on the real kernel at full scale, byte-identical digest |
| 2 | Six memoizations + `RightToken` NamedTuple (with #1: **2.28x**) | **1,144** | 596 | 3-8 | Medium-low. Introduces a returned-list aliasing invariant on `candidates()`; two scratch-patch blemishes to clean (`_PAIRING_SETS` uncapped at module level; P8 declares unused caches) | Reversible — ~50 lines, one file pair, no new runtime or tool | **DO IT.** Byte-identical artifact at full scale; 182/182 repo kernel tests pass including `test_trace_memo`, which drives `build_tables` through a real persisted store *and* a live share |
| 3 | mypyc on `model`/`specificity`/`settle`/`table`, on top of #2 (**3.87x**) | +368 (1,512 total) | +192 (788) | 5-10 | Medium. mypy becomes a second type checker alongside pyright and the two disagree; a C toolchain and per-platform per-version wheels become build inputs; `inspect.getsource` fails on compiled functions so pdb stepping into the kernel is gone; one `mypy_extensions` escape hatch lands in `table.py`'s runtime import path | Reversible but sticky | **PROBABLY.** The single largest keep-the-Python win, but the first option with real ongoing cost |
| 4 | `max_chars_after` 2 → 1 in the 17 calt sweeps (tiered) | 50-98 (foreground) | 50-98 | ~1 | **Coverage, not performance.** The blunt cut gives up 22 of 215 reachable pair renderings; the tier keeping the eight depth-2-dependent tests gives up nothing *today* but nothing guards that it stays true | One constant per test | **PUT IT TO THE USER.** Beats a full `make test` rewrite (1.23-1.40x ceiling) by 2-3x, and no rewrite can reach it |
| 5 | Cut-the-work bundle: `os._exit` after the M1 build's last write, `config_badge` `lru_cache`, persist `load_human_unit_ids`, `yaml.CSafeLoader` at the hot call sites, `signature_digest` → marshal | 45-90 | 30-70 | 2-4 | Low, except `signature_digest`, which re-stamps the persisted signature store once | Each is 1-10 lines | **DO THEM.** Tiny prizes individually; excellent ratio; all verified output-identical |
| 6 | `--conform-horizon` 5 → 4 | 10-108 (background) | 0 | ~0 (one default) | Coverage-neutral: the gate passes with 0 divergences and exact rule/transition coverage at h3, h4 and h5 alike | One flag | **HOLD.** Two runs disagree on both magnitude and ordering. Becomes decisive at full migration (117x sweep growth), where no rewrite substitutes for it |
| 7 | Do nothing | 0 | 0 | 0 | — | — | The honest floor, and it beats rows 8-10 on ratio |
| 8 | Free-threaded CPython 3.14t on top of #2 | +267 (1,411) | +118 | 5-10 | High. Cannot install `uharfbuzz`; `fontTools.feaLib.lexer` re-enables the GIL process-wide; needs a narrower subprocess entry point; ~20 GB for six concurrent production fixpoints (fits this 51.5 GB box, not a 16 GB one); gives up the 1.9x share | Reversible | **NO — DOMINATED** by row 3 at the same prize on one core instead of ~2.8 |
| 9 | Partial Rust port: K3 placed-ink only | ~56 | ~30 | 5-10 | Low. Cleanest interface in the repo, checksum-verifiable against a byte-identity contract | Binary can be deleted | **NOT YET.** Real 16.7x, but 7.8x whole-kernel after the HarfBuzz floor, on a 65 s/day line |
| 10 | Full Rust port of the M1 settlement kernel | 1,938-2,011 gross; **426-499 incremental over row 3** | 1,010-1,048 gross; **222-260 incremental** | **30-60 (centred ~40)** fluent; 60-100 learning; **plus 6-12 engineer-days/month recurring** | High, and the risk is not correctness. The verification story is unusually strong (see below). The risk is a permanent second implementation of the product's core semantics | The Python stays, so the binary is deletable — but the file format, toolchain and CI story are not | **CONDITIONAL — see §5.** Lowest ratio on today's workload; the only measured option that reaches a full alphabet |
| 11 | PyPy | negative on top of row 2 | negative | 5-15 | Second runtime for one subprocess; needs a new narrower entry point because `run_m1` pulls fontTools and uharfbuzz; PEP 758 `except A, B:` must be parenthesized in nine places and `requires-python` relaxed from `>=3.14` | — | **DROP.** Not ranked — actively worse than stock CPython once the memoizations land |
| 12 | PyO3 / FFI hybrid | — | — | row 10 + 10-20 | — | — | **STRUCK, not weighed.** Its only advantage over a subprocess is saving a serialization measured at ~2.5% of the build, and handing Python objects across the boundary rebuilds the exact 254 B → 10 B object graph the packing exists to delete |

### What the port would actually be (feasibility, measured)

The scope is smaller than anyone assumed, and the cut line is real — but it is **not** the subprocess boundary that exists today. `run_m1` already runs as its own process, yet it also mints glyphs, runs the defect gates, emits GSUB/GPOS, compiles `M1.otf` with fontTools and runs three HarfBuzz gates (27-module / 15,859-line import closure). The kernel boundary sits one level in, at `build_tables`, and the repo's own byte-stable `write_windows`/`read_windows` round-trip plus a 155.1 KiB JSON dump of the whole `ResolvedSpec` (3 ms to serialize, 20 dataclass types) already carry 90% of it. **No FFI, no PyO3, no shared memory: a binary that reads a 155 KiB spec and writes a widened windows file.**

Measurement shrinks the scope twice more: `spec_load.py` (1,238 lines) is outside the runtime closure; `trace_memo.py` (454 lines) should be **deleted rather than ported**, because at 20x the persisted memo's whole value (337.9 s cold → 194.9 s warm) collapses to ~7 s against gzip+JSON of ~1.9 M entries; and `_rules_for_input` (405 lines, the largest and hairiest function in `table.py`, and the one whose output *is* the shipped GSUB rule ordering) is **0.6% of runtime** and can stay in Python. What is left is **~2,750 lines of Python semantics → ~4,000-5,500 lines of Rust**, of which roughly 80% is "decide what the letters do" rather than "make it fast". **78.7% of the runtime lives in one 336-line class**, `_ProspectLiveness`, which is simultaneously the hottest, the subtlest and the easiest to get silently wrong — an under-opened liveness verdict omits windows and the font is simply wrong in a way only the conform sweep catches.

**The verification story is the strongest part of the case.** `rebuild/test_rule_witnesses.py::test_the_stamped_table_is_what_a_fresh_fixpoint_builds` already diffs a serialized enumeration against a fresh in-process fixpoint, so the differential harness is a day or two's work; and gate:conform — which still settles in Python — becomes a Rust-vs-Python differential mediated by an independent shaper across every string to horizon 5 in six configurations, on top of the boundary gate, the oracle, and 1,162 rebuild tests.

**The case against is duplication, and it is measured.** The port does not retire `settle.py`: 19 non-test modules reference it, `conform.py` alone with 75 references constructing `Engine` and running `settle_with_engine` per swept text, and `emit_gsub.py` calling `formation_blocked` to generate shipped FEA rows. So every future settlement change is written twice. Over `rebuild/`'s two-month life, **23 commits touched the kernel** (3 in June, 11 in July, 9 in August — flat to rising), and they are not tweaks: 498, 492, 457, 357 lines across `settle`+`table`+`conform`. At ~12 semantics commits per quarter each needing a matching Rust edit plus differential re-verification, the duplication tax is plausibly **6-12 engineer-days per month** — enough to re-pay the port's own cost every two to three quarters. Against that: three of the last five kernel commits (the fifty-minutes-to-twelve cut, the persisted trace memo, the cross-config share) exist *only* because Python is slow and would never have been written at 20x, and the next lever WHATNEXT names — persisting the liveness verdicts — would become unnecessary. That credit is real but one-off; the tax recurs.

Three author-facing things also break and must be budgeted: `explain.py` (the section 6.3a CLI whose entire value is replaying *the same code that built the table*) and `probe.py` would explain what Python thinks rather than what the font was built from; `settle.py`'s `_incomparable_message`, which prints a paste-ready YAML resolve stub, lives inside the kernel; and `uv sync` stops being sufficient to reproduce the project. CI runs only pyright and `tools/build_font.py` and never touches the rebuild pipeline, so nothing breaks today — but the binary would have no CI-built artifact and the M1 stage becomes macOS-ARM-only in practice.

**Rust, not Go**, if it happens: Rust's multiplier is flat across volume while Go's decays 18.3x → 15.0x as its GC scans a bigger live heap, and this workload is the worst possible shape for a tracing GC (a memoized fixpoint whose entire working set is live until the build ends) and the best possible shape for an arena.

---

## 4. The recommendation

**Do the Python levers now. Put the alphabet question to the user. Do not start a Rust port until that question is answered.**

The three specific first things, with what each is measured to be worth:

1. **`gc.freeze()` + `gc.disable()` at `run_m1`'s entry point.** Measured **1.14x** on the real kernel at full 18-rune scale: 81.25 → 71.24 CPU-s (python-levers) and independently 80.65 → 70.45 (verify-headline's interleaved fresh-process A/B), with the artifact digest byte-identical in all four arms and the same 682,842 windows / 2,667 rules / 3,563 treaty rows every time. ~5 lines. Worth **~252 machine-s/day / ~131 foreground-s/day (D)**. Cost: peak RSS rises 3.77 → 4.34 GB. *Note this also settles cost-model adjudication #5 — the GC share was real but the A/B offered as proof ran backwards in one rep under contention; it is now settled at 12.3-12.7%, 1.14x.*

2. **The six memoizations plus the `RightToken` NamedTuple in `settle.py`.** With #1 this is a measured **2.28x** on the real kernel at full scale (78.93 → 34.66 CPU-s, digest `3026eaf52b6b87d6…` identical in every arm, 182/182 repo kernel tests passing including `test_trace_memo`, which is the one place the memo's fired-pointer replay could have diverged). ~50 lines, one file pair, no new runtime and no new tool. Worth **~1,144 machine-s/day / ~596 foreground-s/day (D)**. The single biggest of the six is P6 — memoizing `candidates()` on the collapsed left key the trace memo already uses — because **95.2% of `candidates`' calls are redundant on that key**. Two negative results are already known and should not be attempted: memoizing `_prefer_favors` is 0.79x (the 12-element key costs more than the call) and memoizing the fixpoint's right2 option lists is inside the noise. Two blemishes in the scratch patches must be fixed before landing: `_PAIRING_SETS` sits at module level with no cap (the repo's own `_GUARD_STATES` pattern caps this at 8) and P8 declares two caches it never uses.

3. **Re-baseline, then decide.** One `make artifact-cycle` with `cycle-timings.ndjson` landing, after 1 and 2, so the M1 endpoints (337.9 s cold / 194.9 s warm) are re-measured against the levered kernel — plus one run of the alphabet-scaling sweep. Zero code. What it is worth is not seconds but honesty: **it changes the Rust port's advantage from 20.1x to 5.2-8.8x**, which is the difference between a decision made against 78.9 CPU-s and one made against 34.7 (or 20.4 with mypyc). Every rewrite number in this report is a ratio against a baseline these two actions move by 2.28x, and no port should be benchmarked against the shipped denominator once the levered one exists.

After those three, mypyc (row 3) is the next-best ratio and the last thing available before a port. The `max_chars_after` tier (row 4) is worth more foreground seconds than a full `make test` rewrite could ever buy and should be raised as the coverage decision it is.

---

## 5. What would change the answer

Stated as thresholds the user can check later.

1. **Alphabet target — the decisive one.** `build_tables` grows as letters^4.0-4.5 with the exponent rising (M, measured for the first time this workflow: 1.048 / 2.273 / 4.628 / 10.342 / 22.931 / 51.619 / 92.176 CPU-s at 4 / 5 / 7 / 9 / 11 / 13 / 15 letters, one config). Headroom at today's 337.9 s six-configuration wall, exponent 4.0-4.5 (D):

   | Accelerator | Letters supported |
   |---|---|
   | Shipped Python | 15.0 |
   | + levers (M 2.28x) | 18.0-18.4 |
   | + levers + mypyc (M 3.87x) | 20.3-21.0 |
   | Rust serial vs shipped (M 20.1x, band to 27x) | 29.2-34.2 |
   | Rust six-way parallel vs shipped (D 43-71x) | 34.6-43.5 |

   Full migration is 44 letters (57 runes). **If the migration is going past ~21 letters, every keep-the-Python path is exhausted, and it is exhausted within the next few migration batches.** If it stops in the low twenties, the levers are sufficient and the port is a mistake. *Note this corrects the earlier "Rust parallel reaches 44.0 letters" figure, which used the refuted 96.5x — at the corrected 43-71x band it reaches 34.6-43.5, i.e. only the optimistic end of both bands reaches the full alphabet.*

2. **Six-configuration cold wall.** If it passes ~600 s after the levers land, the levers are spent. If it passes 20 minutes at any point, the port becomes the only measured option. Check with the cycle timings, not by reasoning from the git log.

3. **Scaling exponent.** Re-run the rune-subset sweep after each migration batch. **If the measured exponent rises above ~4.5, even Rust with parallelism stops reaching 44 letters**, and work avoidance (persisted liveness verdicts under two-grain stamps, `--conform-horizon`) becomes mandatory regardless of language. The exponent has been rising across the sweep already (2.7 → 3.2 → 4.4 → 5.2 → 6.1 → 4.9 on consecutive rune pairs).

4. **Kernel churn.** The duplication tax is the port's largest recurring cost. If kernel-semantics commits fall below ~1 per quarter (they are running ~12 per quarter and the rate is flat to rising), the tax collapses and the port's cost drops by most of its recurring term. Check: `git log --oneline -- rebuild/pipeline/settle.py rebuild/pipeline/table.py rebuild/pipeline/model.py rebuild/pipeline/specificity.py`.

5. **Real build cadence.** Every seconds-per-day figure in this report is a model of *intended* workflow. This machine's own green records (`run-m1-green.json` finished 2026-07-27T00:13:14Z, `rebuild/out/m1` mtime Jul 26) show **zero M1 builds during the 80-commit window** in which 76 of those commits landed. If the real cadence turns out to be a quarter of assumed, M1 is still 52% of foreground wait but every s/day figure divides by four and the port's payback passes two years. **Land `cycle-timings.ndjson` and measure it.**

6. **The share.** If `TraceShare` can be made value-keyed so private per-thread specs share the donor's memo (its keys are tuples of strings and frozen dataclasses, so value-equality sharing looks feasible on inspection, but `FeatureSensitivity` is built from `self.spec` and `settle._guard_state` is keyed on `id(spec)`), then parallelism and the share stop excluding each other and the free-threaded and parallel-Rust figures both rise by ~1.9x. That single experiment could move free-threading from "dominated" to "competitive" and Rust-parallel from 43-71x to the high end.

7. **If `explain.py` stops being load-bearing** for authoring (or its trace rendering is ported into the binary — its `TransitionTrace` already carries `ranked`, `eliminations`, `decided_stage` and `runner_up`, so only the printing is Python), one of the three author-facing costs of a port goes away.

---

## 6. The confidence ledger

| Number | Value | Status | Uncertainty |
|---|---|---|---|
| M1 six-config cold / warm wall | 337.9 s / 194.9 s | **Measured** (calibrate, exclusive box; reproduced from raw files) | Wall/CPU is 99.71%, not "identical to three digits". The harness skips `run_m1`'s TSV persistence and both `assert_*` invariants, so the real stage is ≥ 337.9 s |
| M1 stage strictly single-threaded | Yes | **Measured + confirmed from code** | No `jobs` parameter reaches `build_tables`; zero concurrency imports in the four kernel modules; `getrusage` never exceeds wall; `sample` lists one thread. But it was a six-way pool until c569e198 — serial **by design**, to make the share possible, not inherently unparallelizable |
| Fixpoint is CPython, not native | native < 0.61% | **Measured** (25,565 samples; 155 leaf samples fall below `sample`'s print threshold) | Both samplers ran the **77.5 s no-store** kernel. ~12% of the 337.9 s cold stage (~39.9 s, **derived**) is trace-store serialization no profiler covered |
| Real kernel, default config, shipped | 81.36 CPU-s / 4.045 GB | **Measured** (independent `/usr/bin/time -l`) | Reproduces the cost model's artifact exactly. Clean-box mean of two further reps: 78.93 CPU-s |
| Rust, six configs serial, vs shipped Python | **20.07x** | **Measured** on a model, median of 3 reps, spread 4.7-9.2% | Independently reproduced at 19.9x. Fidelity discount 0.754 makes it a floor; band **20-27x** |
| Go, six configs serial | 16.08x | **Measured** on a model | Decays with volume (18.3 → 15.0x); the real kernel is 1.6x larger again, so this is an **upper bound** |
| Rust, six-way parallel, vs shipped | ~~96.46x~~ → **43-71x** | ~~Measured on the model~~ → **Derived** for the real kernel | **REFUTED as a real-kernel figure.** The model's share is worth 1.06x; the real one is 1.90x measured / 2.83x on the traced-entry axis. Derived from calibrate's per-configuration walls |
| Real kernel `TraceShare` value | **1.896x** | **Measured** (clean box, 2 reps, 8-rune slice, all six per-config checksums identical) | Slice-scale; at full scale it is cited at 27%. The traced-entry axis says 2.83x |
| Fidelity: model µs per kernel op ÷ real | **0.754** | **Measured**, median of 3 (0.733-0.772) | Supersedes the Build phase's 0.904. Direction favours the port |
| Python levers on the real kernel | **2.28x** (78.93 → 34.66 CPU-s) | **Measured**, full 18 runes, byte-identical digest, 182/182 kernel tests | python-levers' 2.39x was taken at load average 3.2-4.8; 2.28x is the clean-box figure. The full 1,162-test rebuild suite was **not** run |
| `gc.freeze()`/`gc.disable()` alone | **1.14x** (12.3-12.7%) | **Measured**, two independent full-scale runs agreeing to 0.3 points | Costs +15% peak RSS. The older contended A/B that "ran backwards" is superseded and is not evidence either way |
| mypyc + levers on the real kernel | **3.87x** (78.93 → 20.41 CPU-s) | **Measured**, full scale, byte-identical digest, 182/182 kernel tests | The keep-the-Python ceiling. mypyc alone is 1.86x; it cannot touch the 36.95% of leaf samples that are generated dataclass dunders — it makes the frozen-dataclass memo key *slower* (428 → 566 ns) |
| PyPy on the shipped six-config shape | 1.53x, and 1.74x with memos vs CPython's 2.13x | **Measured** | **Dominated.** k9-subset 2.56x is a no-share, gc-on figure and should not be carried |
| Free-threaded 3.14t on the real shape | **1.65x** | **Measured**, 19 runs, 0 checksum mismatches | The 3.18x headline used a **shareless** baseline. Dominated by mypyc |
| Memo lookup Rust vs Python | 38.2x (1,367 → 35.8 ns) | **Measured**, same key stream, same printed checksum | Construct + hash + probe, not probe alone (faithful to the kernel, which constructs the key per probe) |
| Memo bytes/entry | 254.2 B Python vs 10 B packed | **Measured** | Independently reproduces the cost model's 257.3 B on a different code path |
| CPython beats Rust/Go on a 10-slot string-key insert | 76 vs 154 / 170 ns | **Measured** | Python's row grows from empty, the ports' are presized — the asymmetry runs *against* the ports, so the finding stands |
| K3 placed-ink, Rust single | 16.67x | **Measured**, six-way checksum agreement including a live-fonts replay | Whole-kernel with the HarfBuzz floor is **7.78x (derived)**, ceiling 13.71x |
| K3 `signature_digest` marshal swap | 2.78x | **Measured**, partition-verified (631 → 631 both ways) | Changes the digest bytes; re-stamps the persisted signature store once |
| K5 parsers, Rust | 4.4-9.9x | **Measured**, checksums round-trip through parsed values | Well under the cost model's cited 18-36x; `str.split`/`int()` are already C |
| K5 mmap pack | 12.46x wall / 24.8x worker CPU | **Measured** | **Warm only.** Cold it is 0.80x and the pack costs 1.686 s to build |
| gate:conform, six-config pool wall | 194.3 s (h4) / 208.6 (h3) / 230.1 (h5) | **Measured** end to end for the first time on any machine | Supersedes the cost model's **derived** floor of 336 s / 2,017 CPU-s. Two runs disagree on magnitude and on the h3-vs-h4 ordering |
| `max_chars_after` 2 → 1 cut factor | 46.02x | **Measured**, verified two independent ways | Costs 22 of 215 reachable pair renderings; nine of sixteen pairs lose nothing today |
| `make test` native fraction / ceiling | 70.55% / 1.40x | **Measured**, two independent methods at 72.0/72.5% | Quote the band **1.23-1.40x**; the dispute is decision-irrelevant. Instrumentation bias runs toward *more* rewritable Python |
| M1 armed by | ~~68%~~ → **41.2%** of commits (33/80) | **Measured**, replaying the repo's own prose-blind skip closure | **REFUTED.** 68% double-counted a 6-commit overlap and folded in the review category the model itself marks as an M1 skip |
| M1 foreground share | ~~1,263 s/day, 61%~~ → **1,063 s/day, 60.0%** | **Derived** from the corrected arming count | Still 57.1% / 52.1% at half and a quarter the assumed cadence; ceiling 11.1x → 10.1x |
| M1 total daily budget | ~2,040 s/day (1,063 fg + ~900-1,050 bg) | **Derived**, two corrections applied | ±40% from the cadence assumption, which is itself **unverified** — see §7 |
| Alphabet scaling exponent | letters^4.0-4.5, rising | **Measured** (new; nobody had measured this) | Seven nested rune subsets; the 18-rune row reproduces the cost model's artifact exactly |
| Full-migration cold build in shipped Python | 6.9-11.9 hours | **Derived** from the measured exponent | Sensitive to the exponent, which is rising |
| Port scope | ~2,750 Python lines → ~4,000-5,500 Rust | **Measured** (line trace + import closure + per-function timing) | `spec_load` is outside the closure; `trace_memo` should be deleted; `_rules_for_input` is 0.6% of runtime and stays |
| `_ProspectLiveness` share of `build_tables` | 78.7% | **Measured** (12 runes, wrapped probe entry points) | Reproduces the cost model's 84% cumulative / 79.3% leaf |
| Port effort | 30-60 engineer-days, centred ~40 | **Estimated**, anchors named | The fidelity tail (8-20 days) carries all the variance. 60-100 if learning Rust |
| Duplication tax | 6-12 engineer-days/month | **Estimated** from measured churn (23 kernel commits in 2 months, 450-500 lines each) | The rate is flat to rising, not decaying |
| Interpreter teardown after six configs | 9.5 s | **Measured** | Supersedes the cost model's ~22 s |
| Weighted composite multiplier | Rust 10.66x / Go 13.51x | **Estimated** | Reproduces only 10.1% of the real build; the Go-above-Rust inversion is a weighting artifact. Used for no headline |
| Compilers' full-scale cross-check (CPython 81.5 / mypyc 55.2 / PyPy 37.7 / Cython 76.2) | — | **Cached from an out-of-band contended run** | Not re-measured. Its *equivalence* verdict is sound; its seconds are indicative |
| `config_badge` lru_cache | 2.4 wall-s/day (14.6 CPU-s/day) | **Measured** per-pass, **derived** per-day | The 14.6 belongs in a CPU budget, not a wall budget — the surface build runs at `--jobs 6` |
| `load_human_unit_ids` | 5 s/day measured-frequency; 45 s/day if the chain re-parses 7x | **Measured** per call (0.809 s), **cited** for the 7x | The seven re-parses have never been measured anywhere |

---

## 7. What we still do not know

**Left open by the previous phase, now closed:**
- gate:conform **has** now been measured end to end (194-253 s pool wall, six configurations, gate passing at h3/h4/h5) — but two runs of the same experiment disagree on magnitude and on the h3-vs-h4 ordering, so the horizon lever's value is a 3-36 s-per-pass range rather than a number.
- Whether Rust gets 10x on this workload: **closed.** 20.1x measured, 20-27x band, with 20x conservative.

**Still open:**

1. **The verdict plumbing chain's timings are still docstring-cited and have never been measured anywhere.** They carry 208 s/day — 5% of the foreground — on a `~23 s` + `~3 s` pair from `artifact_cycle.py`'s docstring. So is the "seven re-parses of the 265 MB surface per pass" count that the 45 s/day `load_human_unit_ids` figure rests on. What *is* measured is that one full-surface parse costs 0.81-1.05 s and that five independent `load_units`-style implementations exist in the tree.
2. **The real M1 build cadence is unmeasured and this box's evidence contradicts the model.** Every seconds-per-day figure assumes one refreshing cycle per arming commit; the green records show zero M1 builds across the 80-commit window. This is the largest single uncertainty in the entire cost model and one `make artifact-cycle` closes it.
3. **The generated model spec's condition *contents* are synthetic** (splitmix64), though its shape is copied from the real spec by introspection (per-rune stance counts, the refuse/prefer/extend/contract census, 8 predicate classes at real sizes, the real prefer-chain reach histogram). Real rune family masks and scope conditions have structure a PRNG does not, which could shift branch prediction in either direction. **This is the largest residual risk in the 20.1x and it cannot be closed without writing the real port.**
4. **An unstated asymmetry favouring Rust, magnitude unmeasured:** Python's `TransitionTrace` retains `ranked` and `eliminations` tuples in all 903,904 memo entries; the Rust model's `Trace` is `{settled, joint_floor, prospect, n_notes}` and retains neither (RSS 1.936 GB vs 0.352 GB). Rust *does* build and consume every note string (15 `format!` sites folded into a printed sink), so dead-code elimination is defeated — but the retained provenance payload is not paid.
5. **The real kernel's cross-configuration share value inside a full-scale cold six-config build.** Measured at 1.896x on an 8-rune slice and derived at 2.83x on the traced-entry axis; never measured at production scale. Every parallel figure depends on it.
6. **Whether `TraceShare` can be made value-keyed**, letting private per-thread specs share the donor's memo. Its keys look value-comparable on inspection, but `FeatureSensitivity` is built from `self.spec` and `settle._guard_state` is keyed on `id(spec)`. If it works, parallelism and the share compose instead of excluding each other.
7. **Whether the memoization patches survive the full rebuild suite.** 182 kernel tests pass (including `test_trace_memo`, which drives a real persisted store and a live share). `make test-rebuild`'s 1,162 tests were never run against them.
8. **mypyc under the persisted `TraceStore` path at production scale**, mypyc combined with free-threading, mypyc with `--strict-dunder-typing` or PGO/LTO, and whether the seven mypyc source edits keep pyright green (they are annotation widenings and a rename, so low risk, but pyright was not run on the mirror tree).
9. **Trace-store serialization** — ~12% of the 337.9 s cold stage (derived), gzip plus interning — was never sampled by any profiler in either workflow.
10. **gate:rebuild (750 s/day) and `census --check` (141 s/day) were not re-measured this phase**, and neither has an end-to-end measurement on this box.
11. **Whether any of the 22 depth-2-only pair renderings could flip one of the 17 calt tests' actual assertions.** That needs fault injection. What is established is that a real dependency exists at that depth, not that a regression would escape at depth 1.
12. **K3's scaling to a whole build.** The sampled corpus rebuilds ~1.4 M point tuples against a reported ~237 M per cold build; that extrapolation was not performed.
13. **`make review`, `make complaint-docket` and `make novelty-order`** still have no green record, no skip proof and no attribution slice.

---

## 8. Corrections applied, listed openly

Claims from earlier phases that this package **refutes or materially revises**:

- "M1 is armed by ~68% of commits" → **41.2%** (33/80, measured against the repo's own prose-blind skip closure). The recommendation is unchanged; M1 stays ~60% of foreground wait at every cadence tested.
- "1,263 foreground wall-seconds/day = 61%" → **1,063 s/day = 60.0%**. Foreground rewritable 91.0% → 90.1%; foreground Amdahl ceiling 11.1x → 10.1x; combined ceiling 15.2x → 14.1x.
- "96.5x with six-way parallelism" → **43-71x (derived)**. The model cannot price the cross-configuration share; the real one is ~2.8x more valuable than the model's on the traced-entry axis and 1.9x on measured wall.
- "The share never paid in any language" (bench) → **refuted**. That is a property of the generated model (1.7% Python / 4.1% Rust / 7.0% Go), not of the kernel. The real kernel's share is worth **1.896x, measured**.
- "Free-threading is worth 3.18x on the real six-configuration shape" → **1.65x** against the shape `run_m1.py` actually runs. Its baseline had the share switched off.
- "PyPy 2.56x" → **1.53x** on the shipped shape, and **dominated**: PyPy + memos (1.74x) is slower than stock CPython + memos (2.13x).
- "Python levers 2.39x" → **2.28x** on a clean box (the 81.25 CPU-s baseline was taken at load average 3.2-4.8).
- "None of the keep-the-Python accelerators reaches ten" → true, but **stacked they reach 3.87x**, which changes the port's incremental prize from ~1,940 s/day to ~430-500 s/day. Nobody had stacked them.
- "gc is 13% of the fixpoint" → **12.3-12.7%** (1.14x), with +15% peak RSS. The older A/B that ran backwards is superseded, not explained away.
- "Zero native frames — 100% CPython" → **native < 0.61%** (the unattributed-sample floor), and the sampled kernel was the 77.5 s no-store build.
- "Wall equals CPU to three digits" → **99.71%** (360.01 s real vs 358.96 s CPU under `/usr/bin/time -l`).
- "pyright contributes zero wall" → **zero *marginal* wall**; it is 9.6-10.6 CPU-s and becomes the critical path if `make all` drops below ~5.8 s.
- "The stage is already spawned as its own subprocess, so the port needs no FFI" → the premise is **wrong** (that subprocess is `run_m1`, which also compiles the font and runs three HarfBuzz gates), but the conclusion survives via a **narrower** boundary at `build_tables` that the repo's own serializers already 90% support.
- "Port scope is settle.py + table.py + deps (3,205+ lines)" → **~2,750 lines of semantics**; `spec_load` is outside the closure, `trace_memo` should be deleted rather than ported, and `_rules_for_input` (405 lines) is 0.6% of runtime.
- "`--conform-horizon` is nearly flat" (bench) vs "1.3% wall" (an earlier contended run) → **3-36 s per gate pass**, with the two runs disagreeing on both magnitude and ordering. Neither "flat" nor "a lever".
- "Interpreter teardown ~22 s" → **9.5 s**.
- The cost model's derived gate:conform floor (336 s wall / 2,017 CPU-s) → **measured at 194-253 s / 1,307-1,425 CPU-s**.
- PyO3 → **struck from the options list as strictly dominated**, not weighed.

---

## Raw evidence

`raw/perf/cost-model.md`; `raw/perf2/bench/{results.json,results.md,reps/*,k1-micro.json,k3-k5.json,compilers.json,python-levers.json,freethreaded.json,cut-the-work.json,anchor-*.json,k1-scale.json,share-experiment.json}`; `raw/perf2/verify-headline/{findings.json,derivations.txt,gc-ab.json,commits-80.json,classify_commits.py,rep-*.json}`; `raw/perf2/verify-alternatives/{summary.json,compose.ndjson,m1-reverify.ndjson,m1-fullscale-mypyc.ndjson,mypyc.ndjson,pypy.ndjson}`; `raw/perf2/feasibility/{EVIDENCE.md,scaling.json,scaling.txt,split-k12.txt,spec.json}`; and the per-slice trees under `raw/perf2/{k1-meso,k1-micro,k3-k5,compilers,python-levers,freethreaded,cut-the-work}/`.
