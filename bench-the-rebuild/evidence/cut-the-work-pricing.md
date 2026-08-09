# Pricing the cut-the-work levers

What the levers that no language change can reach are actually worth, measured on this machine, at this HEAD (`704bd210`), with the alphabet as it stands (15 migrated letters).

Every number carries a tag: **M** measured here, **D** derived from measured inputs by stated arithmetic, **C** cited from the cost model or a commit message and not re-measured, **U** unmeasured. Wall-clock figures are **contention-suspect**: sibling agents held ~3 of 12 cores for the whole session (`load average` 2.7–3.7 at start). CPU-seconds are the robust metric throughout; where a wall figure carries an argument, it is flagged.

**These are the user's calls, not mine.** The largest of them is a coverage decision wearing a performance decision's clothes, and the one everyone expected to be a coverage decision turns out to be a performance decision not worth taking. Both sides of each are priced so the trade is visible; a decision table sits before the method section at the end.

---

## Headline

1. **`--conform-horizon` is not the lever anyone thought it was — today it is worth almost nothing, and the reason is measurable.** Horizon 5 → 4 saves **5.5% of gate:conform's CPU** and **1.3% of its wall** (M). Not 18x. The exhaustive sweep does shrink by 18x, but the witness top-up it forces grows by 2.1x and eats the saving almost exactly. The two terms are in tension by construction, and nobody had measured the second one.
2. **The cost model's gate:conform figure is 1.4x too high.** Its 336 s wall / 2,017 CPU-s *derived floor* is now a measurement: **253.0 s wall / 1,424.7 CPU-s** for the full six-config horizon-5 sweep (M). The 1,008 s/day background term should be ~759 s/day.
3. **The horizon becomes the decisive lever later, and then no rewrite can substitute for it.** The sweep term is Θ(A⁵) in the alphabet. Going from today's 18 symbols to a fully-migrated 47 multiplies it by **117x** (D) — at the measured marginal cost that is **~2.5 hours of wall per gate pass at horizon 5, against ~3 minutes at horizon 4** (estimated). A 50x rewrite buys an alphabet of 39 letters at horizon 5; dropping to horizon 4 buys 37 letters *for free*, and the two compose to 99 (D). The horizon lever's leverage grows with the alphabet. A rewrite's does not.
4. **`max_chars_after: 2 → 1` in the calt sweep is real and large: it is a 46.0x cut on ~92% of `make test`'s CPU** (M, two independent routes) — 205.6 s wall / 2,212 CPU-s becomes roughly 30–45 s / 210–250 CPU-s (D). It is not free: **22 of the 215 pair renderings the sweep can reach exist only under a two-character suffix** (M), concentrated in three of sixteen pairs. Nine pairs provably lose nothing today, which is why the targeted tiers are priced separately.

---

## 1. gate:conform's `--conform-horizon`

### What the horizon actually controls

`conform._conformance_config` (`rebuild/pipeline/conform.py:1369`) does two things in sequence:

```python
for length in range(1, max_length + 1):
    for combo in itertools.product(alphabet, repeat=length):
        sweep_text("".join(combo))
```

then, for every settlement rule the sweep never fired and every decision-table transition it never realized, it hunts a *witness* — a shortest realizing string BFS-derived from the table's own windows — shapes it, and diffs it exactly like a swept text.

So `--conform-horizon` controls **only the exhaustive part**. It does **not** control rule coverage or transition coverage: those are completed by the top-up at any horizon. That is not a claim from the docstring; it is what the runs show.

| horizon | `sequences`/config | gate result | uncovered rules | uncovered transitions | divergences |
|---|---:|---|---:|---:|---:|
| 3 | 6,174 | **PASS** | 0 | 0 | 0 |
| 4 | 111,150 | **PASS** | 0 | 0 | 0 |
| 5 | 2,000,718 | **PASS** | 0 | 0 | 0 |

All M. `sequences`/config is exactly Σ 18ᵏ for k = 1..H, confirming the alphabet is 18 symbols (15 codepoint-bearing runes plus space, ZWNJ, and the namer dot; the three ligature runes carry no codepoint and never appear in a cmap buffer).

**Coverage lost at horizon 4 versus 5** is therefore *not* "some rule goes unchecked". It is exactly this: the 1,889,568 five-character strings per configuration stop being diffed font-versus-settle, except for the ones that happen to be witnesses. What you give up is redundant confirmation density over short strings — the same property WHATNEXT already states ("only off-corpus diff density shrinks"), now with the gate's own verdict behind it.

Worth noticing while pricing this: **horizon 5 was never sufficient on its own anyway.** The decision window is six slots (input, left state, right1..right4), so a fully interior depth-4 window needs six tokens. The module docstring's worked example — `sub qsNo.loop qsMay' qsMay qsMay`, realized only by `·Day·Tea·No·May·May·May` — is one token past the sweep at horizon 5. The witnesses have always been what makes the gate complete; the horizon has always been buying density, not completeness.

### What it costs, measured

Six configurations in a spawn pool of six (identical to `run_m1.run_font_conformance` with `--jobs 8`, which `_spawn_pool` caps at the six configs), boundary-gate horizon supplied green, witness cache cold — the shape a cycle meets after a join-changing rune edit.

| horizon | pool wall (s) | CPU (s) | setup (s) | exhaustive sweep (s) | witness top-up (s) | top-up share | shaping runs | topped-up texts | topped-up rules |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 224.1 | 1,256.5 | ~5 | 416 | 853.2 | **67.9%** | 3,948,883 | 3,911,839 | 11,709 |
| 4 | 249.8 | 1,346.5 | 5.9 | 607.4 | 817.8 | **57.4%** | 4,140,575 | 3,473,675 | 6,636 |
| 5 | **253.0** | **1,424.7** | 7.8 | 1,040.2 | 389.4 | **27.2%** | 14,031,442 | 2,027,134 | 2,497 |

Two more horizon-5 variants, for the levers in §1a and §3a:

| horizon-5 variant | pool wall (s) | CPU (s) | sweep (s) | top-up (s) | `Shaper.shape` calls |
|---|---:|---:|---:|---:|---:|
| cold witnesses, boundary gate green (the row above) | 253.0 | 1,424.7 | 1,040.2 | 389.4 | 14,724,986 |
| **warm** witnesses, boundary gate green | 275.3 | 1,461.6 | 1,275.4 | **288.1** | 14,724,782 |
| cold witnesses, **no** proven boundary horizon | 265.4 | **1,490.3** | 1,131.1 | 367.7 | **23,357,306** |

All M. CPU is `user + sys` from `/usr/bin/time -l` on the pool parent. The sweep/top-up split comes from a clock on `Shaper.shape` inside each worker: the exhaustive phase only ever shapes texts of length ≤ H, so the first longer text marks the boundary exactly. Setup (reading the serialized window enumeration, building the engine, the third/fourth-slot liveness filters) is under a second per configuration and is not a factor. Peak RSS 0.78–0.91 GB for the whole pool.

**The saving from dropping the horizon:**

| change | wall saved | CPU saved | what it costs |
|---|---:|---:|---|
| 5 → 4 | **3.2 s (1.3%)** M | **78 CPU-s (5.5%)** M | the length-5 diff, ~1.89 M strings/config |
| 5 → 3 | **28.9 s (11.4%)** M | **168 CPU-s (11.8%)** M | the length-4 and length-5 diffs |

**Two independent mechanisms defeat the lever, and both are visible in the table.**

*First, the sweep does not fall 18x per step — it has a floor.* 1,040 → 607 → 416 s across horizons 5, 4, 3, against a sequence count that falls 18x per step. The marginal cost per swept text is **38.2 µs between horizon 5 and 4**, **303 µs between 4 and 3**, and **11.2 ms for the first 37k texts** (D, by differencing). That curve is `_SettledWindowWalk`'s memo warming: the first encounter of each distinct window runs the settlement kernel, and every recurrence after it is a dict probe. So the sweep's cost is really *"walk the reachable window space once, then shape"*, and the walk-once part — roughly **400 CPU-s** — is there at any horizon. At horizon 5 the sweep's marginal cost is barely more than the HarfBuzz shape itself (20.7 µs of the 38.2).

*Second, the top-up more than doubles as the horizon drops*, because every window the sweep no longer realizes becomes a hunt. At horizon 3, **99.1%** of all shaping runs are top-ups; at horizon 5, **14.5%** are.

**The two terms are coupled through the same table, and the coupling is what kills the lever.** It also bounds it: a floor of ~400 CPU-s of unavoidable walk plus a top-up that never drops below the ~390 CPU-s it costs at horizon 5 puts a hard floor of roughly **800 CPU-s under a 1,425 CPU-s gate** (D). At most ~44% of gate:conform is reachable by horizon reduction in principle, and 12% in practice.

### Is it a performance decision or a correctness decision?

**Performance, and today a bad trade.** Rule and transition coverage are exact at every horizon (M, three runs). Nothing a gate asserts changes. What changes is the density of off-corpus font-versus-settle confirmation, and 5 → 4 buys 5.5% of one deferred background gate for it. Pull the lever when the arithmetic below says to, not before.

### When it becomes the decisive lever

The sweep term is Σ Aᵏ ≈ A^H, and A grows one letter per migration. Today A = 18. At full migration A = 47 (44 plain Quikscript letters plus space, ZWNJ, namer dot).

| horizon | sequences/config today (A=18) | at full migration (A=47) | growth |
|---|---:|---:|---:|
| 3 | 6,174 | 106,079 | 17.2x |
| 4 | 111,150 | 4,985,760 | **44.9x** |
| 5 | 2,000,718 | 234,330,767 | **117.1x** |

All D, exact arithmetic. Read against the rewrite question the rest of this workflow is asking:

- A **10x** faster sweep, by any means, supports an alphabet of **28** letters at horizon 5. A **25x**, 34 letters. A **50x**, **39** letters. None of them reaches 47. (D: A′ = 18·K^{1/5}.)
- **Horizon 4 alone**, with no rewrite at all, supports **37** letters at today's cost. (D: A′ = (18⁵)^{1/4}.)
- **Horizon 4 plus a 10x rewrite** supports 66; plus a 50x rewrite, 99. (D.)

Put in seconds, using the measured horizon-5 marginal cost of 38.2 µs/text (which is the right constant to extrapolate with, since the memo saturates and the marginal text is essentially one HarfBuzz shape):

| at full migration (A=47) | sweep per config | six configs in parallel |
|---|---:|---:|
| horizon 5 | ~8,950 CPU-s | **~2.5 hours of wall, per gate pass** |
| horizon 4 | ~190 CPU-s | ~3 minutes |

Estimated, not measured — the marginal cost could drift and the settlement warm-up floor grows with the table too — but the two rows are three orders of magnitude apart and no plausible drift closes that.

That is the whole argument in one line: **a language rewrite divides the sweep by a constant; the horizon divides its exponent.** Each letter added multiplies the horizon-5 sweep by ((A+1)/A)⁵ — 1.32x at A=18, 1.11x at A=46 — so a 10x rewrite is spent by roughly eight more letters. The horizon lever, by contrast, gets *stronger* as the alphabet grows: dropping H by one divides the sweep by ≈A, which is 18x today and 47x at cutover.

Caveat that bounds all of it: **the top-up is a floor the horizon cannot reach.** At horizon 4 today the top-up is 818 CPU-s of a 1,346 CPU-s gate. The top-up scales with the decision table (665k–725k windows/config), not with A⁵ — `table.third_slot_filter` / `fourth_slot_filter` keep the deep blocks scaling with the authored chains — but it is not small, and dropping the horizon pushes work *into* it. Any horizon plan needs a top-up plan beside it.

### The adjacent levers this measurement exposes

**(a) The witness top-up's re-verification, 14.5% of horizon-5 shaping runs.** Warm-cache horizon-5 rerun (same witnesses on disk, table digest unchanged): top-up **389.4 → 288.1 CPU-s**, a 101 CPU-s / 26% saving on that term and ~7% of the gate (M). But `topped_up_sequences` is unchanged at 2,026,948 — by design, `_conformance_config` re-verifies recorded winners rather than trusting them, so the cache spares the candidate assembly and not the shaping. Trusting a recorded winner under an unchanged `table.windows_digest` would remove **2.03 M shapes and ~288–389 CPU-s, 20–27% of the gate** (D). That one *is* a correctness decision: name-grain conformance would be safe (an ink-only edit moves no GSUB rule), but `check_join_gaps` compares pen positions and anchors, which do move with ink. Whole-gate CPU cold-versus-warm (1,424.7 vs 1,461.6) is inside the contention band and should not be read as a regression.

**(b) `proven_boundary_horizon` — a gate already declining to re-verify, worth 4.4%.** With the boundary gate's horizon withheld, the sweep re-runs `check_zwnj_structure` and `check_split_buffer` itself: `Shaper.shape` calls rise from 14.72 M to **23.36 M** (+8.63 M) and the gate costs **1,490.3 CPU-s against 1,424.7** — the inheritance is worth **65.7 CPU-s (4.4%) and 12.4 s of wall (4.7%)** (M). Modest, but it is the pattern to judge every other "two gates prove overlapping things" question against, and it is the reason a cycle whose boundary gate is *not* green for the current font bytes pays a conform gate 4.4% dearer.

---

## 2. `max_chars_after` in the calt sweep

### The arithmetic, verified exactly

`_surround_combos` (`test/test_calt_regressions.py:71`) builds every combination of up to `max_chars` entries from `_context_chars()`, which is **46** entries (44 plain Quikscript letters plus `space` and `ZWNJ` — M, imported and counted).

- `max_chars_after=2` → 1 + 46 + 46² = **2,163** suffix combinations.
- `max_chars_after=1` → 1 + 46 = **47**.
- Ratio **46.02x**. The cost model's 2,163 → 47 arithmetic is exactly right (M).

Seventeen test functions run the (before=2, after=2) sweep, each sharded 46 ways over `before_first`. Per shard the prefix filter leaves 1 empty + 1 length-1 + 46 length-2 = 48 combinations, so **48 × 2,163 = 103,824 shaped strings per shard**. Instrumented run of one shard reports `_shape lookups=103824` — exact agreement (M). Across all 17 tests: 17 × 46 × 103,824 = **81,190,368 shaped strings** (D), against the cost model's measured 88,448,272 `hb.shape` calls for the whole suite — **91.8%** of them.

### What it costs, measured

| variant | wall (s) | CPU (s) | shaped strings | note |
|---|---:|---:|---:|---|
| `test_it_it_never_joins[qsAh]`, one shard | 3.58 | 3.57 | 103,824 | M, `-p no:xdist` |
| trivial test in the same module (fixed overhead) | 1.06 | 1.04 | 3 | M |
| marginal | **2.52** | **2.53** | 103,824 | D — **24.3 µs/string**, of which HarfBuzz is 19.4 µs (M, 80%) |
| all 17 sharded (2,2) sweeps, 782 ids, `-n auto` | 249.7 | **2,081.0** | 81.2 M | M, contention-suspect wall |
| whole `make test` | 205.6 | 2,212.4 | 88.4 M | C (cost model, exclusive box) |

Net the shared `make all` + pyright prelude (≈33 CPU-s), the 17 tests are **2,048 of `make test`'s 2,179 CPU-s of test body — 94%** (D, contention-suspect: contention inflates CPU somewhat through cache pressure). The independent, contention-free shape-count route gives **91.8%**. Two routes, one answer: **`make test` essentially *is* these seventeen sweeps.** I carry **92%** below. The 19.4 µs/shape measured here also cross-checks the cost model's harfbuzz term (1,578 CPU-s ÷ 88.45 M = 17.8 µs/shape) to within 9%, the gap being this session's contention.

### What the cut buys

Three tiers, because the coverage measured in the next section is not uniform across pairs. All D from the measured 92% share and the exact 46.02x factor; wall is the softer figure.

| change | tests kept at after=2 | `make test` CPU | wall |
|---|---|---|---|
| today | 17 of 17 | 2,212 CPU-s C | 205.6 s C |
| **blunt**: `max_chars_after=1` everywhere | 0 | **≈ 210–250 CPU-s (8.8–10.6x)** | **≈ 30–45 s (4.5–7x)** |
| **targeted-aggressive**: keep 2 only where it buys ≥3 renderings (·It+·J'ai, ·It+·Day ×2, ·Utter+·Gay) | 4 | **≈ 710 CPU-s (3.1x)** | **≈ 70–90 s** |
| **targeted-conservative**: keep 2 wherever it buys anything at all | 8 | **≈ 1,175 CPU-s (1.9x)** | **≈ 110–130 s** |

Wall is derived and softer than CPU: the residual includes `make all`'s ~11.2 s of serial prelude inside `pytest_configure`, which no test-level cut can touch, and xdist's 10.76x efficiency will fall as the big tasks vanish. Treat the wall band as indicative; the CPU figures are firm.

For scale: this single parameter is worth **more than the entire Amdahl ceiling of rewriting `make test` in another language** (1.40x, C). And unlike the M1 fixpoint, the calt sweep does **not** grow with the migration — `_context_chars()` already spans the full 44-letter alphabet, so this cost is at its final size today.

### What coverage is actually lost

The question that decides it: **can the second character after a pair change how the pair renders?** Measured directly — for each of the 16 pairs those tests guard, the set of shaped renderings *of the pair's own two slots* reachable with a suffix of length ≤ 1, against the set reachable with a length-2 suffix, prefixes held at their length-≤1 sweep.

| | renderings reachable at depth ≤ 1 | reachable **only** at depth 2 |
|---|---:|---:|
| **suffix** side (`max_chars_after`) | 215 | **22 (+10.2%)** |
| **prefix** side (`max_chars_before`) | 215 | **4 (+1.9%)** |

All M. Per pair, the suffix column:

| pair | depth ≤1 | suffix-only@2 | prefix-only@2 |
|---|---:|---:|---:|
| ·It+·J'ai | 13 | **11** | 0 |
| ·It+·Day | 8 | **4** | 1 |
| ·Utter+·Gay | 5 | **3** | 0 |
| ·See+·Out | 8 | 1 | 0 |
| ·Ye+·It | 29 | 1 | 0 |
| ·It+·It | 29 | 1 | **3** |
| ·J'ai+·Ye | 14 | 1 | 0 |
| ·Eat+·Ye, ·Jay+·He, ·He+·Owe, ·Ye+·Owe, ·Jay+·Ye, ·It+·Owe, ·It+·Cheer, ·Ye+·I, ·It+·I | — | **0** | **0** |

The worked example, from the same run: with suffix `·It`, ·J'ai renders `qsJai.en-y5.ex-y0.ex-ext-1`; append any of ·Ah/·At/·Day as a *second* suffix character and it becomes `qsJai.en-y5.ex-y0` — the exit extension is withdrawn from two positions away. Over the full grid, the second suffix character changes the rendering of everything up to and including the pair in **11,097 of 1,591,232 cells (0.70%)**, spread across 13 of the 16 pairs (M).

So the honest verdict on coverage:

- **It is not free.** A dependency at suffix depth 2 demonstrably exists in the shipped font, and it is exactly the kind the engine is designed to express — WHATNEXT documents depth-3 and depth-4 chains (`·Day·Tea·Utter·Tea·X`, ·Utter's vote reaching two positions), so this is a modelled behaviour, not an accident.
- **It is also not uniform.** Nine of sixteen pairs gain nothing at all from the suffix depth-2 sweep and four more gain a single rendering; three pairs account for 18 of the 22. The blunt global cut and the targeted cuts differ by 3–5x in savings and by a lot in risk — which is the whole reason to price them separately rather than argue the parameter.
- **And the sweep is already sampling, not proving.** The grammar is depth-4; the sweep is depth-2. Nobody should read `max_chars_after=2` as exhaustive coverage of the model — it never was.
- **The prefix side is the cheaper cut on this evidence** (4 renderings lost versus 22) but it is also what shards the tests 46 ways, so cutting it changes the parallel shape of the suite as well as its size.

**Performance or correctness?** Correctness — but with a mechanical safety net available. The "loses nothing" result is a fact about *today's* font; a later rune change could give a demoted pair a depth-2 dependency and the sweep would no longer notice. The measurement above (`depth2_states.py`, **47.9 s on one core**, M, for all sixteen pairs on both sides) is itself the guard: run it as a cheap meta-test that fails when a pair demoted to `max_chars_after=1` acquires a depth-2-only rendering. That converts a one-time coverage bet into a maintained invariant for about 1% of what the sweep it licenses cutting costs today.

---

## 3. Other places where the work itself is the choice

Ranked by measured or derived saving.

### 3a. The conform gate's structural checks, inherited rather than re-run — already built, worth measuring

`conform.proven_boundary_horizon` lets the sweep skip `check_zwnj_structure` and `check_split_buffer` for texts within the horizon the boundary gate already proved *for these exact font bytes* (it pins `max_length`, `configs`, and `font_sha256`). Measured at horizon 5, six configs, cold cache: see the boundary row in `results/`. This is the repo's own model for the general lever — **when two gates prove overlapping things about the same artifact, the second should decline** — and it is the pattern the top-up re-verification question (§1a) should be judged against.

### 3b. The cost model's own gate:conform term needs correcting downward

Not a lever, but it changes the ranking. Measured 253.0 s wall / 1,424.7 CPU-s against the model's derived 336 s / 2,017 CPU-s (C). The model's background total of 1,899 s/day drops to ~1,640 s/day, and the combined day from 3,961 to ~3,700 s. gate:conform is still the largest deferred term; it is just 25% smaller than assumed, and it is *already* about as cheap as the horizon lever can make it.

### 3c. Interpreter teardown after the six-config M1 build — 9.5 s, not 22

`/usr/bin/time -l` on the isolated build: **398.01 s real** against **388.5 s** of measured work — **9.5 s** of startup plus teardown, at a peak RSS of **8.40 GB** (M). The cost model carries ~22 s (C) from a run whose graph was 7.46 GB. Whatever the true figure, it is a work-avoidance target (`os._exit` after the last write) rather than a rewrite target, and it is smaller than advertised.

### 3d. `make all` still has no skip path, and now it is a larger share of what is left

Measured at 11.24 s wall / 22.76 CPU-s per invocation (C), fired unconditionally by `conftest.pytest_configure` on every non-`rebuild/`-only pytest run with xdist enabled (M, read at `conftest.py:57`). Today that is 5% of `make test`'s wall and invisible. **If `max_chars_after` is cut, it becomes 25–37% of it** (D) and the largest single remaining term. The two levers should be priced together: a skip for `make all` is nearly worthless now and near the top of the list afterwards.

### 3e. The 5,038 unsharded tests in `test_calt_regressions.py`

5,820 tests in that file, 782 of them the (2,2) shards. The other 5,038 cost ~130 CPU-s between them (D, by difference). Nothing to do here; recorded so nobody goes looking.

### 3f. Not a lever, but load-bearing for every estimate above

`build_tables` over six configurations, cold, on this box today: **386.1 s** of work (M) against the cost model's 337.9 s (C, exclusive box) — the gap is this session's contention plus three letters of alphabet growth since that measurement. The isolated build reproduces the model's cited artifact shape exactly (2,642–2,950 rules/config, 204 settled cell glyphs, 7,024 GSUB rules, zero defect errors), which is what licenses reading these conform numbers as the real gate's numbers.

---

## The decision table

Every row is the user's call. My job was the two right-hand columns.

| lever | saving today | coverage given up | kind of decision |
|---|---|---|---|
| `--conform-horizon` 5 → 4 | 78 CPU-s, 3.2 s wall (5.5% / 1.3% of one deferred gate) M | the length-5 font-vs-settle diff; rule and transition coverage unchanged M | performance — and a bad trade at today's alphabet |
| `--conform-horizon` 5 → 4, **at full migration** | ~2.5 h → ~3 min of wall per pass, estimated | same | performance — and by then unavoidable |
| trust recorded witnesses under an unchanged `windows_digest` | 288–389 CPU-s (20–27% of the gate) D | the gate stops re-proving that a recorded witness still fires; `check_join_gaps` is ink-sensitive, so an ink-only edit would go unchecked there | **correctness** |
| `max_chars_after` 2 → 1, blunt | ~1,960–2,000 CPU-s, ~165 s wall (8.8–10.6x on `make test`) D | 22 of 215 reachable pair renderings (10.2%) M | **correctness** |
| `max_chars_after` 2 → 1, keep the 4 tests that buy ≥3 | ~1,500 CPU-s (3.1x) D | 4 of 215 (1.9%) M | **correctness**, much cheaper |
| `max_chars_after` 2 → 1, keep the 8 tests that buy anything | ~1,035 CPU-s (1.9x) D | **nothing, today** M | correctness — needs the guard below to stay true |
| `max_chars_before` 2 → 1 | comparable size, unpriced here | 4 of 215 (1.9%) M — but it is also what shards the suite 46 ways | **correctness** plus a change to the suite's parallel shape |
| a depth-2 guard test (`depth2_states.py`) | costs 47.9 s on one core M | none — it adds coverage | free insurance for any `max_chars_*` cut |
| `make all` skip path | 22.8 CPU-s / 11.2 s wall per pytest run C | none | performance — worthless now, top of the list after a `max_chars_after` cut |
| `os._exit` after the M1 build's last write | 9.5 s wall per six-config build M | none | performance |

## Method, and what would make these numbers wrong

**Isolation.** Everything ran against `bench-the-rebuild/cut-the-work/m1`, a fresh (spec@HEAD, six decision tables, M1.otf) triple built by `run_m1.run(out_dir=...)` into scratch. `rebuild/out` was never written and never used as input — its artifacts are from 26 July and predate five rune commits, so a sweep against them would have measured a failing gate. No verdict store, journal, review surface, or tracked file was touched, and no `make review-cycle` / `artifact-cycle` / `merge_verdicts` was run.

**Equivalence.** The horizon runs are not a re-implementation: they call `conform.conformance_config_worker` — the same function `run_m1.run_font_conformance` submits — with the same per-config spawn pool, the same serialized window enumeration, the same witness machinery. The only patch is a wrapper around `Shaper.shape` that adds a timestamp and a counter. **All four horizon runs report 0 divergences, 0 uncovered rules, 0 uncovered transitions — the gate passes** — which is the equivalence evidence: the harness computes what the gate computes, and it does so at horizon 3, 4 and 5 alike.

**Deliberate divergences from the production gate**, all of which make my figures conservative or neutral:
- The witness cache lives in a per-horizon scratch directory, so each cold run is genuinely cold. A production pass on unchanged inputs skips the gate entirely via its green record.
- `boundary_horizon` was forced to the sweep horizon (the green-boundary-gate case) rather than resolved from a summary file I did not have. The `none` variant prices the other case.
- The parent process cost of `uv run python -m rebuild.pipeline.run_m1 --conform-only` (argument parsing, the skip-fingerprint sha256 over run_m1's inputs, writing `conform_summary.json`) is excluded; the cost model puts the whole skip-fingerprint family at 0.307 s (C).

**Contention.** Sibling agents held ~3 of 12 cores throughout. The horizon pool uses 6. Wall figures are therefore inflated by an unknown amount, likely 10–20%; the cold-versus-warm horizon-5 pair (253.0 versus 275.3 s wall for strictly less work) is a direct read on that noise floor and should be taken as the error bar on every wall number here. CPU figures move much less and carry the arguments.

**Not measured.** Whether any of the 22 depth-2-only renderings could ever flip one of the seventeen tests' actual assertions — that needs fault injection, not observation, and I did not do it. Whether the top-up term's growth continues below horizon 3. Whether the conform sweep's per-config wall stays balanced as the alphabet grows (it is balanced today: 213–252 s across the six).

## Artifacts

- `run.sh` — runs every variant and prints one JSON blob (`collect.py` folds it). ~22 min with the triple already built, ~29 with `FULL=1`.
- `build_isolated.py` — builds the scratch (spec, tables, font) triple.
- `sweep_horizon.py` — the timed six-config conform sweep at a given horizon.
- `depth2_states.py`, `after2_coverage.py`, `after2_states.py`, `before2_states.py` — the calt coverage measurements.
- `shapecount.py` — pytest plugin counting `hb.shape` calls and seconds.
- `k17.txt` — the `-k` expression selecting the seventeen (2,2) sweeps.
- Logs and JSON: `build.log`, `h3.log`, `h4.log`, `h5.log`, `h5warm.log`, `h5nb.log`, `k17.log`, `depth2-states.json`, `after2-coverage.json`, `after2-states.json`, `before2-states.json`.
