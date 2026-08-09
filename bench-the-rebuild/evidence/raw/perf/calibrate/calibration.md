# Calibration table: what a human actually waits on

Apple M4 Pro, 12 logical cores (8 P + 4 E), macOS 26.6.1, CPython 3.14 GIL build, repo head `704bd210`, working tree clean.

**The machine was exclusive for this session.** No other agent ran. Background OS load was checked before each block and never exceeded roughly 15% of one core. Every wall-clock number below is therefore trustworthy rather than contention-suspect — which matters, because several of them are 1.9–2.2x faster than the same measurement taken by the earlier mapping agents while they were competing with each other. Where that happened it is called out in the row.

Timing method: `raw/perf/calibrate/t.py` wraps each command, reporting wall from `time.perf_counter` and child CPU as the delta of `resource.getrusage(RUSAGE_CHILDREN)` user/sys, which folds in every reaped descendant. Long single-process runs additionally carry `/usr/bin/time -l`.

## The headline table

| Step | Command | State | Wall (s) | User CPU (s) | Sys CPU (s) | N |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Interpreter startup, via uv | `uv run python -c pass` | warm | 0.026 | 0.015 | 0.008 | 7 |
| Interpreter startup, bare | `.venv/bin/python -c pass` | warm | 0.015 | 0.010 | 0.003 | 7 |
| gate:make-test skip | `make test` (green) | skip | 0.078 | 0.051 | 0.021 | 5 |
| gate:js | `node --test …` (7 files) | cold | 0.104 | 0.357 | 0.076 | 3 |
| Whole cycle skip-decision surface | `artifact_cycle.py --dry-run` | skip | 0.176 | 0.143 | 0.027 | 5 |
| All seven skip fingerprints, summed | in-process | skip | 0.307 | — | — | 3 |
| pytest collection, main suite | `pytest test/ site/ --collect-only -n 0` | warm | 0.671 | 0.553 | 0.049 | 2 |
| `make verdict-ready` | `make verdict-ready` | warm | 0.976 | 0.896 | 0.072 | 5 |
| pyright | `uv run pyright` | warm | 5.784 | 9.618 | 0.280 | 3 |
| **`make all`** | `make all` | cold (only state) | **11.239** | 22.431 | 0.334 | 3 |
| — of which build_font.py | `uv run python tools/build_font.py …` | cold | 11.139 | 22.323 | 0.406 | 3 |
| — of which typst | `typst compile … print.typ` | cold | 0.092 | 0.054 | 0.038 | 3 |
| Review surface build | `rebuild.review.build --jobs 6 --out <scratch>` | warm | 13.61 | 13.13 | 0.40 | 1 |
| `make review` | `review_scoped_anchor_selectors.py` | cold (only state) | 21.396 | 42.674 | 0.283 | 2 |
| M1 table, default config | `table.build_tables` | warm memo | 28.54 | 33.44 | 0.35 | 1 |
| Review surface build | `rebuild.review.build --jobs 6 --out <scratch>` | **cold** | 35.56 | 141.15 | 1.98 | 1 |
| M1 table, default config | `table.build_tables`, no memo | cold | 77.51 | 77.15 | 0.46 | 1 |
| M1 table, default config | `table.build_tables`, writes memo | cold | 94.55 | 94.02 | 0.66 | 1 |
| **M1 table, all six configs** | `m1_all_configs.py warm` | warm | **194.86** | 194.70 | — | 1 |
| **`make test`** | `make test FORCE=1` | cold | **205.573** | 2197.021 | 15.360 | 1 |
| **M1 table, all six configs** | `m1_all_configs.py fresh` | **cold** | **337.87** | 337.66 | — | 1 |

Peak RSS where it matters: `make test` 2.07 GB, review surface build 2.41 GB, M1 six-config table stage **7.46 GB** (8.01 GB at process level).

## Startup and import

`uv` costs **10.8 ms per spawn** over the bare venv interpreter — measured as the difference of two 7-run medians, both extremely tight (25.0–26.2 ms and 14.8–18.2 ms).

Import cost is the wall of `python -c "import X"` (median of 5) minus the 15.1 ms bare-interpreter floor. `-X importtime` numbers are given alongside for ranking contributors, but they are inflated: the instrumentation reports 26.0 ms for a bare `pass` whose true cost is 15.1 ms.

| Entry point | Wall total (ms) | Wall self (ms) | `-X importtime` cumulative (ms) |
| --- | ---: | ---: | ---: |
| `tools/build_font.py` | 83.9 | **68.8** | 380.0 |
| `rebuild.review.build` | 77.2 | 62.1 | 282.3 |
| `rebuild.pipeline.run_m1` | 72.7 | 57.6 | 240.7 |
| `rebuild.review.census` | 66.3 | 51.2 | 221.1 |
| `rebuild.tools.artifact_cycle` | 41.8 | 26.7 | 110.7 |
| `tools/quikscript_fea.py` | 27.1 | 12.0 | 46.3 |
| `rebuild.tools.verdict_ready` | 26.4 | 11.3 | 67.0 |
| `rebuild.tools.make_test_gate` | not isolated | — | 132.9 |
| (bare interpreter baseline) | 15.1 | 0 | 26.0 |

`tools/build_font.py` is the heaviest by a wide margin and it is all fontTools: `feaLib.builder` 28.4 ms, `feaLib.parser` 26.1 ms, `feaLib.variableScalar` 24.8 ms, `varLib.models` 24.7 ms, `varLib` 24.7 ms, `ttLib.tables.otTables` 9.6 ms — with `build_font` itself 59.1 ms of the instrumented trace. `tools/quikscript_fea.py`, the FEA emitter, imports no fontTools at all and costs 12 ms.

## The skip machinery, measured

Every fingerprint, best of three, computed in-process:

| Fingerprint | Cost (s) | Matches its record right now? |
| --- | ---: | --- |
| `make_test_closure_fingerprint` | 0.013 | **yes** (this session recorded it) |
| `rebuild_gate_skip_fingerprint` | 0.079 | no |
| `run_m1_skip_fingerprint` | 0.050 | no |
| `conform_skip_fingerprint(5)` | 0.051 | no |
| `surface_build_skippable` | 0.002 | no (returns False) |
| `census_skip_fingerprint` | 0.022 | (no record on this machine) |
| `plumbing_skip_fingerprint` | 0.000 | (returns None — no record) |
| `fingerprint.data_lines` | 0.046 | — |
| `fingerprint.rune_digests` | 0.045 | — |

The `make test` skip is the most extreme work-avoidance ratio in the repo: **78 milliseconds to skip 2,212 CPU-seconds**, about 28,000x. Of those 78 ms, 26 ms is `uv`, 27 ms is importing `artifact_cycle`, and only 13 ms is the actual sha256 sweep of 100 files / 15.6 MB. In other words the fingerprint is already cheaper than the process that computes it.

The whole 16-step plan resolves in **0.176 s**. That is the total cost of the layer that looks like a build system.

## `make test` in detail

`make test FORCE=1` ran 6,753 tests green in **205.57 s wall / 2,212.4 CPU-seconds**. pytest's own session clock read 193.89 s, so the fixed prelude — `make all` plus pyright, overlapped inside `pytest_configure` — is **11.68 s**, which matches the independently measured 11.24 s font build.

**Parallel efficiency: 2212.4 / (205.57 × 12) = 89.7%**, a 10.76x speedup on 12 logical cores. That is unusually good, and the `--durations=40` output explains why: the slowest single test is 4.27 s and the top forty are all `test_calt_regressions.py` sweeps clustered between 3.29 s and 4.27 s. There is no long pole and nothing to rebalance. The 10% residue is E-core scheduling and per-worker setup, not a serial Python section.

This is also the number to quote at CLAUDE.md's "≈3 min wall under xdist": uncontended it is 3 minutes 26 seconds, and the earlier contended measurement of 252.8 s was 23% inflated.

## M1: the one place that is genuinely serial

The table stage is the single biggest step in the repository and it uses **one core of twelve**, by design — the cross-configuration `TraceShare` has to live in one process.

| | Cold (s) | Warm (s) | Avoidance |
| --- | ---: | ---: | ---: |
| Default config only, fixpoint | 77.51 | 28.54 | 2.72x |
| Default config, incl. memo write | 94.55 | — | — |
| **All six configs** | **337.87** | **194.86** | **1.73x** |

Wall equals CPU to three digits in every row: 337.87 s wall against 337.66 s CPU. For five and a half minutes, eleven cores are idle.

Two things worth carrying forward. First, the persisted trace memo costs **17.0 s to write** (94.55 − 77.51) and saves 66 s on the next build — an excellent trade, and the 2.72x it buys on the default config independently reproduces commit `a649d630`'s cited 149 s → 55 s ratio on entirely different hardware. Second, whole-stage warm avoidance is only 1.73x, well short of the per-config 2.72x, because the cross-config share already carries most of the recipient configurations' work even on a cold run — so the persisted memo has less left to add. **Even with literally nothing changed, the table stage costs 195 seconds of single-threaded Python.**

One cost nobody attributes: `/usr/bin/time -l` on the six-config run reports 360.01 s of process real against 337.87 s of measured work — **22 seconds of interpreter teardown**, spent freeing a 7.46 GB object graph after the last line of output. The default-config run shows the same shape (91.03 s real vs 77.61 s measured, 13.4 s of teardown).

## Review surface

| | Wall (s) | CPU (s) | load | plan | units | manifest+check | cache |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold (`--jobs 6`) | 35.56 | 143.1 | 7.9 | 0.3 | 22.4 | 1.7 | 2.8 |
| Warm (`--jobs 6`) | 13.61 | 13.5 | 2.2 | 3.0 | 3.6 | 1.9 | 2.6 |

Cold really was cold: 0 cached signatures / 152,730 shaped, 0 of 62,148 units served. 62,148 units / 292,098 rows / 36 batches both times.

This is the row where contention distorted the earlier map most badly. The review-surface agent measured 67.8 s wall at `--jobs 10`; uncontended at `--jobs 6` it is **35.56 s — 1.9x faster with fewer workers**. Their CPU total (202.2 s) versus mine (143.1 s) shows why: the extra four workers bought no wall time and each cost a full font/spec/subset-table load. The cold build parallelizes 4.03x on 6 workers (67%); the warm build does not parallelize at all (0.99x), because the warm path is entirely the reduce side.

## Parallel efficiency, all steps

| Step | CPU (s) | Wall (s) | Speedup | Cores it could use | Efficiency |
| --- | ---: | ---: | ---: | ---: | ---: |
| `make test` | 2212.4 | 205.57 | 10.76x | 12 | **89.7%** |
| Review surface, cold | 143.1 | 35.56 | 4.03x | 6 | 67.1% |
| `make all` | 22.76 | 11.24 | 2.03x | 6 | 33.8% |
| `make review` | 42.96 | 21.40 | 2.01x | 12 | 16.7% |
| pyright | 9.90 | 5.78 | 1.71x | 12 | 14.3% |
| Review surface, warm | 13.53 | 13.61 | 0.99x | 6 | 16.6% |
| `make verdict-ready` | 0.97 | 0.98 | 0.99x | 12 | 8.3% |
| **M1 table stage, 6 configs** | 337.66 | 337.87 | **1.00x** | 12 | **8.3%** |

## Process spawn tax

Counted from the currently-resolved 16-step plan (`dryrun-plan.txt`, in which nothing skips) plus the pool constructions in the code.

**15 `uv run` spawns** per fully-cold cycle: the driver itself, `run_m1`, `review.build`, `carry_verdicts`, three `merge_verdicts`, `echo_verdicts`, `standing_verdicts`, `census`, `complaint_docket`, `gate:conform`, `uv run pytest rebuild/`, `make_test_gate`, and the `uv run pytest test/ site/` that `make_test_gate` spawns in turn.

**60 raw interpreter spawns** beneath them: `run_m1` constructs `_spawn_pool(6)` three separate times (conformance, boundary, oracle) = 18; `gate:conform` one pool of 6; `review.build` two spawn pools (signatures at `build.py:562`, units at `build.py:666`) × 6 = 12; and the two `pytest -n auto` pools contribute 12 xdist workers each.

**75 Python process spawns total.** The arithmetic:

- uv wrapper increment: 15 × 10.8 ms = **0.162 s**
- bare interpreter startup floor: 75 × 15.1 ms = **1.133 s**
- combined process birth: **1.30 s**
- imports in the 15 uv entry points: ~0.62 s (derived from the measured 12–69 ms self-import costs)
- imports in the 36 pipeline/review pool workers: ~2.4 CPU-s (derived)
- per-pytest-worker module-level setup: ~30.7 CPU-s — **cited** from the test-suite mapping agent's 1.28 CPU-s × 24 workers, not re-measured. This is *not* import or collection cost: collection for the whole main suite measures 0.671 s. It is the per-process `glyph_data/` YAML parse plus `compile_glyph_set` plus `build_anchor_map`.

Total process/startup layer: roughly **35 CPU-seconds, about 4 seconds of wall**, against a cold cycle whose critical path is fifteen-plus minutes. Under 0.5%. Removing `uv` entirely would save 0.16 s.

## What I could not measure, and why

- **The rebuild test suite.** Deprioritized: outside the minimum brief, 6–8 minutes, and heavily overlapping the M1 numbers I did take (its dominant test, `test_rule_witnesses`, *is* `build_tables` plus the witness hunt, six times over). I did verify it would have been safe — `rebuild/conftest.py` builds the surface into `tmp/review-surface-test-cache/` or a `tmp_path`, never into `rebuild/out/review/`. The contended prior figures are 451.8 s cold / 340.1 s warm; expect 25–35% less uncontended, by analogy with every other step re-measured here.
- **gate:rebuild's skip path end to end.** Its fingerprint does not match `rebuild-gate-green.json` on this tree, so `make test-rebuild` would have run the suite rather than skipped. Only the fingerprint component (0.0786 s) is measured; the end-to-end skip is *derived* at ~0.13 s.
- **A full `run_m1`.** It writes `rebuild/out/m1/` in place and restamps stage-A, which would invalidate the user's surface and every downstream green record. I isolated `build_tables` instead, with the trace store redirected to scratch.
- **gate:conform.** Not boundedly measurable on this tree: `rebuild/out/m1` carries a July stamp the current sources do not reproduce (verified — `conform_skip_fingerprint` does not match `conform-green.json`), so `--conform-only` would rebuild all six fixpoints in-process first (338 s) *and then* write `conform_summary.json` and settle `conform-green.json` in `rebuild/out`.
- **The verdict plumbing chain and complaint docket.** Every step writes the verdict store. Forbidden, and it would destroy real human adjudication work. The `~23 s` + `~3 s` figures in circulation come from `artifact_cycle`'s docstring and have never been measured on any machine.
- **A whole `make artifact-cycle` / `make review-cycle`,** and therefore `rebuild/out/cycle-timings.ndjson`, which still does not exist here. `make cycle-timings --by-step` still has no medians. Nothing in this session fills that gap.
- **`make complaint-docket`, `make novelty-order`,** and the `make all`-dependent targets (`check-html-*`, `test-leaks`, `leak-snapshot`, `woff2`, `print-job`) — each of the latter pays the measured 11.24 s font build first, but their own bodies were not timed.

## State this investigation changed

- `rebuild/out/make-test-green.json` was **rewritten** — `make test FORCE=1` passed 6,753/6,753 and the gate recorded the green. That is a repair, not damage: the record was stale from 25 July and the gate was owed a run. `gate:make-test` now auto-skips.
- `site/*.otf`, `*.fea`, `print.pdf` were rebuilt five times by `make all`, byte-identically — every variant printed "Font unchanged", so no mtime moved and no downstream fixture cache was disturbed.
- Everything else is confined to `raw/perf/calibrate/`. No tracked file was modified, no git command mutated anything, the verdict store and journal were never opened for writing, and `rebuild/out/review/` was never rebuilt in place — both surface builds went to `raw/perf/calibrate/review-out` via `--out`.
