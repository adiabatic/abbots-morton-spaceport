# Hot kernels, their real data, and the Python baseline

Seven kernels (six real, one control), each specified tightly enough to reimplement in Rust or Go from this document alone, each with an extracted real-data file and a measured CPython baseline.

## How to reproduce the baselines

```zsh
cd <repo>
uv run python raw/perf/attr-overhead/bench_kernels.py
```

The harness first re-verifies every reimplementation against the repo's own code (`rebuild.review.ink.InkComparator.config_diff` / `.signature`, `rebuild.review.ink.translate_outline`, `rebuild.validation.rowmodel.Row.from_tsv`) on 50 real `(text, config)` pairs and refuses to run if any output differs, so the baselines below are provably the same computation the repo performs.

All timings are `resource.getrusage(RUSAGE_SELF)` CPU seconds, reported as the **minimum of four runs** — the box had other agents on it, and CPU seconds inflate when the scheduler lands work on the four E-cores. Minimum is the least-contended estimate. Machine: Apple M4 Pro, macOS 26.6.1, CPython 3.14.6.

## Data files

| File | Bytes | Contents |
| --- | --- | --- |
| `raw/perf/attr-overhead/data/outlines-before.json` | 1,870,407 | 1,148 glyph outlines from `site/AbbotsMortonSpaceportSansSenior-Regular.otf`, decomposed by fontTools' `DecomposingRecordingPen`. `{glyph_name: [[operator, [[x, y] | null, ...]], ...]}` |
| `raw/perf/attr-overhead/data/outlines-after.json` | 280,444 | same, 212 glyphs, from `rebuild/out/m1/M1.otf` |
| `raw/perf/attr-overhead/data/shaped-runs.jsonl` | 1,014,970 | 3,000 real `(unit, codepoints, config)` triples with **both** fonts' HarfBuzz output already applied: `{"unit", "cps", "config", "before": [[glyph, x_off, y_off, x_adv], ...], "after": [...]}` |
| `raw/perf/attr-overhead/data/coord-types.json` | 18 | proof that **every one of the 139,392 outline coordinates in both fonts is a Python `int`** — no floats, no `None` points. A port never has to reproduce float `repr`. |
| `raw/perf/attr-overhead/data/baseline-rows.tsv` | 6,192,300 | 54,240 real rows of `rebuild/out/m1/baseline-default.subset.tsv.gz`, comments stripped |
| `raw/perf/attr-overhead/data/memo-keys.tsv.gz` | 6,287,188 | all 1,875,829 settlement memo keys from a real default-config trace store, ten tab-separated fields |
| `raw/perf/attr-overhead/data/candidate-fields.tsv` | small | 81 distinct real `(rune, stance, entry, exit)` quadruples with occurrence counts, harvested from `rebuild/out/m1/settlement-*.tsv` |

Shaping is deliberately **outside** every kernel. HarfBuzz is native in both languages; feeding pre-shaped runs keeps the comparison honest.

---

## K1 — `place_run` (the placed-ink computation)

**Where it lives:** `rebuild/review/ink.py:87` `translate_outline`, `:107` `OutlineCache.placed`, `:129` `InkComparator.ink_pieces`.

**Inputs:** an outline table (`glyph_name → sequence of (operator: string, points: sequence of (int, int) or null)`) and a shaped run (`sequence of (glyph_name, x_offset: i32, y_offset: i32, x_advance: i32)`).

**Algorithm:**

```
pen_x = 0
pieces = []
for (name, dx, dy, adv) in run:
    outline = table[name]
    if outline is non-empty:                       # inkless glyphs (space, ZWNJ, markers) contribute nothing
        pieces.push(translate(outline, pen_x + dx, dy))
    pen_x += adv
sort pieces                                        # see ordering note
return pieces as an immutable tuple

translate(outline, dx, dy) =
    for each (operator, points): (operator, [ (x + dx, y + dy) for each point; null passes through ])
```

**Ordering note (load-bearing).** The sort is Python's lexicographic tuple comparison over `(operator: str, points: tuple[(int, int)])` pairs: compare element by element; strings compare by Unicode scalar value; a tuple that is a prefix of another sorts first. The sorted order is part of the value's identity — it feeds K3's digest, which is a persisted byte-identity contract — so a port must reproduce it exactly.

**Scale in the real build:** 41.1 `translate_outline` calls per unit, ~3,808 point iterations per unit, 62,148 units in the per-unit phase plus 152,730 rows in the signature phase. Mean outline is 42.8 points (M1) / 52.8 points (Senior), max 196.

**Python baseline (measured):** **70,483 ns** per record, where one record is *both* fonts' runs for one `(text, config)` pair — 7.875 glyphs across the two sides on average — i.e. **8,950 ns per placed glyph**.

---

## K2 — `config_diff` (the localized before→after ink delta)

**Where it lives:** `rebuild/review/ink.py:165` `run_ink`, `:176` `config_diff`.

**Inputs:** the same data as K1.

**Algorithm:**

1. `run_ink(side)` — like K1 but keeps run order and separates the pen position: one `(outline translated by (0, y_offset), pen_x + x_offset)` entry per inked glyph, `pen_x` accumulating `x_advance`.
2. Strip the common prefix: advance `start` while `before[start] == after[start]` (both outline *and* pen position equal).
3. Strip the common suffix: walking inward from both ends, keep stripping while the two outlines are equal **and** `pen_after - pen_before` equals a single constant `shift` fixed by the first stripped pair. `shift` is 0 if nothing was stripped.
4. Multiset (`Counter`) the remaining middles on each side, each element being `translate(outline, pen, 0)` — i.e. the piece moved into absolute coordinates.
5. `before_only = (Cb - Ca).elements()`, `after_only = (Ca - Cb).elements()` — multiset difference, saturating at zero, preserving multiplicity.
6. `x0` = minimum x over every point in `before_only + after_only`. If there are no points at all, return `((), (), shift)`.
7. Normalize each side: translate every piece by `(-x0, 0)`, then sort the pieces (same ordering rule as K1).
8. Return `(before_only_normalized, after_only_normalized, shift)`.

**Semantic notes.** `Counter` subtraction here hashes and compares whole nested outline tuples — that is where the CPython cost concentrates. `((), (), 0)` means ink-identical, which is the machine-approval predicate for 48,164 of 62,148 units.

**Scale in the real build:** one call per `(unit, config)` — 292,098 in a cold surface build (62,148 units × 4.70 configs).

**Python baseline (measured):** **97,065 ns** per record (both `run_ink` calls plus the delta). 849 of the 3,000 sampled records have a nonempty delta.

---

## K3 — `signature_digest` (repr + sha256)

**Where it lives:** `rebuild/review/ink.py:53` `signature_digest`, fed by `:144` `InkComparator.signature`.

**Input:** a signature = the pair `(K1(before run), K1(after run))`.

**Algorithm:** `sha256(repr(signature).encode("utf-8")).hexdigest()`.

**The repr is the specification.** It is CPython's tuple repr over a tree of tuples, strings and ints:

- a tuple prints as `(a, b, c)`, elements separated by `, `;
- a one-element tuple prints with a trailing comma: `(a,)`;
- the empty tuple prints as `()`;
- strings print single-quoted (all operator names here are plain ASCII identifiers, so no escaping arises);
- ints print in decimal, with `-` for negatives.

Because `coord-types.json` proves every coordinate is an `int`, a port never needs Python's float repr. **This digest is persisted** (`rebuild/out/review/ink-signatures.tsv.gz` and the unit cache), so byte-identity is a hard contract: changing the recipe orphans every stored digest.

**Scale in the real build:** 152,730 rows per cold surface build; mean repr length **8,941 bytes**, so the pass generates ~1.37 GB of throwaway text purely to hash it.

**Python baseline (measured):**

| | ns/signature | share |
| --- | ---: | ---: |
| whole kernel | 97,831 | 100% |
| `repr()` alone | 93,825 | 95.9% |
| `sha256(...).hexdigest()` alone (native) | 3,286 | 3.4% |

This is the single most lopsided kernel in the repo: the part a rewrite eliminates is 96% of it, and the part it must keep is 3%. A Rust port that hashes the numbers directly (or serializes into a reusable byte buffer rather than allocating a fresh 8.9 KB Python string) removes essentially the whole cost — but only if it reproduces the digest, which means writing the same bytes.

---

## K4 — `row_from_tsv` (baseline row parse)

**Where it lives:** `rebuild/validation/rowmodel.py:63` `Row.from_tsv`.

**Input:** `data/baseline-rows.tsv`, 54,240 lines, 6,192,300 bytes.

**Line format:** five tab-separated fields.

1. codepoints — `:`-joined uppercase 4-digit hex → `Vec<u32>`
2. glyphs — `|`-joined glyph names → `Vec<String>`
3. clusters — `,`-joined decimal ints
4. seams — `,`-joined tokens; an **empty field means the empty list**, not a one-element list containing `""`
5. positions — `|`-joined `x,y,advance` decimal triples → `Vec<(i32, i32, i32)>`

The trailing newline is stripped before splitting; the split on tab is exact-arity (five fields, error otherwise).

**Scale in the real build:** ~325k subset rows parsed per surface worker (and re-parsed independently by each of 6–12 spawn workers), plus 292,098 audit rows in `audit.load_audit`. The bulk cousin `rebuild/pipeline/baseline_subset.py:51-65` parses only field 1 (`line.split('\t', 1)[0]`, then `int(token, 16)` per codepoint) over 4,985,767 rows per table × 11 tables.

**Python baseline (measured):** **1,809 ns per row** — 54,240 rows in 0.098 s CPU, i.e. **63.2 MB/s**. A competent Rust TSV+integer parser runs at 500–1500 MB/s on this shape, so this is the kernel with the widest plausible gap.

---

## K5 — `memo_key` build + hash-map probe

**Where it lives:** `rebuild/pipeline/settle.py:1136-1157` (`transition_trace`'s memo key and `cache.get(key)`), with the same key shape persisted by `rebuild/pipeline/trace_memo.py`.

**Input:** `data/memo-keys.tsv.gz`, 1,875,829 keys, ten tab-separated fields: `left_kind, cell_rune, cell_stance, seam, extension, input_rune, right1, right2, right3, right4`. `-` is the null marker. A right slot is `L:<rune>` for a letter token and `B:<kind>` for a boundary/unknown token (`edge`, `space`, `zwnj`, `namer-dot`, `unknown`). Only **23 distinct token values** occur in the whole store, and the token itself is a two-field frozen record `(kind, rune)`.

**Algorithm:** build the 10-field composite key from live components and probe a hash map holding all 1,875,829 entries. Benchmark probe set = every 7th key (267,976 probes, 100% hit rate); the real workload also misses, which in CPython costs about the same.

**Scale in the real build:** 2,428,420 `transition_trace` calls per configuration (measured by cProfile), each doing at least one key build + probe, and the probe is also on the path of every `_prospect` / liveness recursion above it.

**Python baseline (measured):**

| | ns/probe |
| --- | ---: |
| build the 10-tuple + `dict.get` | 630.7 |
| build the 10-tuple + `hash()` only | 403.2 |

**Memory (measured, RSS deltas):** the key tuples alone cost **137.0 B/key** (an 8-byte header plus ten 8-byte pointers — exactly what CPython charges), and **257.3 B/key** with the dict index. A packed Rust key — six interned-string `u32` ids plus a `u32` extension plus four `u16` token ids — is 40 B, ~46 B with a hashbrown index: **5.6× smaller**. On the M1 build's measured 3.82 GB peak RSS that difference is most of the working set.

---

## K6 — CONTROL: `json.loads` over a review shard

**Where it lives:** `rebuild/review/status.py:103` `load_human_unit_ids` (and every one of the seven verdict-plumbing children).

**Input:** `rebuild/out/review/units/boundary-echo.json` — 74,028,465 bytes, 18,368 units. The whole surface is 27 such shards, 264.5 MB.

**Algorithm:** parse the shard, then build the set of `id`s whose `batch` is not null.

**Python baseline (measured):** **282.1 MB/s** (0.263 s CPU for 74.0 MB), plus 73.4 ns/unit for the id-set comprehension over the parsed objects.

**Why it is here.** This is CPython's C decoder, and it is the *floor*, not a target. `serde_json` would plausibly reach 500–900 MB/s on this payload — call it 2–3× — against 1.05 s of a cold 549-CPU-second artifact path. Any claim that "JSON is the bottleneck" dies on this row. The real win available here is not a faster parser but not parsing at all: persist the id set in the manifest.

---

## K7 — the frozen-dataclass tax (a reference kernel, not a call site)

**Where it lives:** `rebuild/pipeline/settle.py:61-119` (`RightToken`, `LeftContext`, `Candidate`, `Elimination`, `RankedCandidate`) and `rebuild/pipeline/model.py:24-301` — all declared `@dataclass(frozen=True)` **without** `slots=True`.

**Input:** `data/candidate-fields.tsv` — 81 real `(stance, entry, seam, order_index, exit_index)` value sets, cycled to 2,000,000 constructions. This is `settle.Candidate`'s exact shape.

**Why it exists.** Method C (an in-process 200 Hz sampler, below) puts **34.0% of all Python frames** in the default table build inside `<string>:__init__` (23.5%), `<string>:__hash__` (7.5%) and `<string>:__eq__` (2.9%) — the dataclass-generated methods. That is not an algorithm; it is the object model. K7 prices it directly.

**Python baseline (measured):**

| operation | ns/op | vs plain tuple |
| --- | ---: | ---: |
| construct `@dataclass(frozen=True)`, no slots | 375.4 | 11.7× |
| construct `@dataclass(frozen=True, slots=True)` | 361.7 | 11.3× |
| construct a plain tuple | 32.0 | 1× |
| `hash()` a frozen dataclass (no slots) | 107.0 | 5.0× |
| `hash()` a plain tuple | 21.2 | 1× |

Note that `slots=True` barely helps *construction* — a frozen dataclass's generated `__init__` goes through `object.__setattr__` per field either way — though it does cut per-instance memory (measured elsewhere: `table.Window`, which does use slots, costs 97.8 B/row for 7 fields).

In Rust these operations are free: constructing a struct is a register move, and hashing it is one pass over packed bytes. This kernel is the cleanest single statement of what "being Python" costs this repo.

---

## What each kernel is worth, multiplied out

Using the measured per-item baselines and the measured real-world call counts:

| kernel | real call count per cold pass | implied CPU-s | notes |
| --- | ---: | ---: | --- |
| K2 `config_diff` | 292,098 (units × configs) | 28.4 | measured rate × measured count |
| K1 + K3 signature pass | 152,730 rows | 25.7 | K1 (both sides) + K3 per row |
| K4 `Row.from_tsv` | ~325k × 6 workers + 292k audit | 4.1 | plus the 11 × 4.99 M-row bulk variant |
| K5 memo probe | 2,428,420 per config × 6 | 9.2 | key handling only, not the kernel it guards |
| K6 shard parse | 264.5 MB per consumer | 0.94 | ×7 plumbing children + `make verdict-ready` |

These sum to roughly 70 CPU-seconds of a 549-CPU-second artifact path, which is the honest scale of "named hot kernels" versus the diffuse interpreter tax that is the rest of it. The diffuse part is not addressable kernel by kernel — it is addressable only by moving whole modules (`rebuild/pipeline/settle.py` + `table.py`, `rebuild/review/ink.py`) across the language boundary.
