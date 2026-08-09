# build-and-gate-topology — raw evidence

Machine: Apple M4 Pro, 12 logical cores (8P+4E), macOS Darwin 25.6, Python 3.14.6, `uv` warm.
Repo HEAD at measurement: 704bd210. Other agents may have been running concurrently — treat every
wall figure as contention-suspect; CPU (getrusage RUSAGE_CHILDREN) is the robust column.

All timings taken with `raw/perf/build-topology/timeit.py N <argv>` (best-of-N, wall +
child user/sys CPU) unless noted.

## Measured target/step costs

| what | wall (best) | child CPU | file |
| --- | --- | --- | --- |
| `uv run python -c pass` (launch floor) | 0.03 s | 0.03 s | t-startup.txt |
| `import rebuild.tools.artifact_cycle` | 0.028 s cumulative | — | `-X importtime` |
| `uv run python rebuild/tools/artifact_cycle.py --dry-run` | 0.18 s | 0.18 s | t-dryrun.txt, dryrun-plan.txt |
| `make test` skip path (fingerprint + green compare, equivalent script) | 0.15 s | 0.11 s | skip_make_test.py |
| `make test-rebuild` skip path (same) | 0.22 s | 0.20 s | skip_rebuild_gate.py |
| `make verdict-ready` | 0.99 s | 0.98 s | prof-verdict-ready.txt |
| gate:js (`node --test` × 7 files) | 0.18 s | 0.59 s | t-gate-js.txt |
| `make all`'s `tools/build_font.py` (→ scratch out dir) | 11.39 s | 23.19 s | t-build-font.txt |
| `uv run pyright` (whole tree) | 5.81 s | 10.05 s | t-pyright.txt |
| `rebuild.review.census --check` (1 process, rc=1 STALE) | 74.65 s | 46.07 s | t-census-check.txt |
| `make review` (`tools/review_scoped_anchor_selectors.py` → scratch) | 47.00 s | 54.85 s | — |

`make test` itself, `make test-rebuild` itself, `make artifact-cycle`, `make review-cycle`,
`rebuild.review.build` and `run_m1` were **not** run (forbidden / too destructive / too long).
Their costs below are cited from commit messages or module docstrings, never measured here.

## Fingerprint / skip-key costs (in-process, best-of-3, no profiler)

From `fingerprint_costs.py` → `fingerprint-costs.txt`:

```
make_test_closure_fingerprint          wall=  0.016s cpu=  0.010s
  git ls-files (make-test closure)     wall=  0.007s
run_m1_skip_fingerprint                wall=  0.052s cpu=  0.052s
  fingerprint.data_lines               wall=  0.047s   <- 18 rune YAML parses
  fingerprint.baselines_value          wall=  0.000s   <- stat sizes + digests.tsv only
  fingerprint.pipeline code lines      wall=  0.002s
conform_skip_fingerprint               wall=  0.052s   (= run_m1 lines + M1.otf + horizon)
rebuild_gate_skip_fingerprint          wall=  0.086s   (incl. sha256 of 59 MB divergence-audit.tsv)
census_skip_fingerprint                wall=  0.024s
surface_build_skippable                wall=  0.002s
plumbing_skip_fingerprint              wall=  0.003s
fingerprint.compute_all (stage A+B)    wall=  0.051s
fingerprint.tables_value               wall=  0.049s
fingerprint.rune_digests               wall=  0.046s
```

Sum of every skip key a fully-warm cycle computes ≈ **0.24 s CPU**.

## Green-record state on this machine (2026-08-08)

```
make-test      record=2026-07-26T05:52:20Z match=False has_files=False
run_m1         record=2026-07-27T00:13:14Z match=False has_files=False
conform        record=2026-07-26T17:26Z    match=False has_files=False
rebuild-gate   record=2026-07-27T00:26:26Z match=False has_files=False
census-result  NO RECORD (rebuild/out/census-green.json is the older filename; census-result.json absent)
plumbing       NO RECORD (plumbing-green.json absent — feature landed in 1d7691dc, never run here)
cycle-timings  NO JOURNAL (rebuild/out/cycle-timings.ndjson absent — 612d645a landed after the last cycle)
```

So **no cycle has been run on this machine since 2026-07-27**, twelve commits ago, and the
timing journal `make cycle-timings` reads does not exist yet. `uv run python -m
rebuild.tools.cycle_timings --by-step` prints:
`No timing journal at .../rebuild/out/cycle-timings.ndjson yet — it appears after the first artifact cycle.`

`git diff --name-only HEAD~13..HEAD` filtered to gate:make-test's non-exempt closure yields
exactly one path: **`Makefile`**. That single edit — made by the perf work itself — is what
currently owes a full `make test`.

## Data volumes

```
surface manifest totals: 62,148 units / 292,098 rows / 36 batches / 1,104 echo groups
surface shards:          27 files, 264.5 MB of JSON
rebuild/out total:       810 MB
m1/divergence-audit.tsv: 59.0 MB
m1/M1.otf:               0.135 MB
subset tables:           11 files, 5.3 MB (.tsv.gz)
full baselines:          11 files, ~458 MB (.tsv.gz)
rune files:              18 files, 51 KB
make-test closure:       100 files, 15.6 MB
rebuild-gate closure:    171 files, 2.6 MB (+ m1 artifacts, fonts, baselines hashed separately)
verdicts-autosave.json:  2.1 MB, 9,260 effective verdicts
tests collected:         6,753 (test/ + site/) ; 1,162 (rebuild/)
last cycle summary:      28,203 unmatched oracle rows, 8,960 carried verdicts, multi_matched 0
```

## `make verdict-ready` breakdown (measured, no profiler)

```
shards: 27 files, 264.5 MB
read all shard bytes                       0.015s   <- page cache; I/O is free
json.loads all shards                      1.080s   <- C decoder, ~245 MB/s
status.load_human_unit_ids                 0.886s   <- the whole command, essentially
fingerprint.compute_all                    0.052s
fingerprint.data_lines                     0.050s
yaml.safe_load 18 runes (pure-Python)      0.046s
yaml.load CSafeLoader 18 runes             0.006s   <- 7.7x, libyaml IS installed and unused
```

`load_human_unit_ids` (rebuild/review/status.py:103) full-parses 264 MB of JSON to build a set
of unit ids where `batch is not None`. That is the whole of `make verdict-ready`'s ~1 s.

## `/usr/bin/sample` of `rebuild.review.census --check` (10 s slice, PID 41585)

File: `sample-census.txt`. Total 8,591 samples in the 10 s window, single-threaded.

Top-of-stack, collapsed (excerpt):

```
_PyEval_EvalFrameDefault  (in Python)                          2392
tuple_dealloc  (in Python)                                      603
_tlv_get_addr  (in libdyld.dylib)                               523
apply_forward(...)  (in _harfbuzz.abi3.so)                      370
OT::Layout::Common::Coverage::get_coverage  (in _harfbuzz...)   341
_PyTuple_FromStackRefStealOnSuccess  (in Python)                303
gen_dealloc  (in Python)                                        302
OT::chain_context_apply_lookup<...>  (in _harfbuzz.abi3.so)     228
_PyFrame_ClearExceptCode  (in Python)                           205
PyObject_RichCompareBool  (in Python)                           183
_Py_dict_lookup  (in Python)                                    172
pymalloc_alloc  (in Python)                                     152
hb_ot_map_t::apply<GSUBProxy>  (in _harfbuzz.abi3.so)           149
...
```

Summing the `_harfbuzz.abi3.so` rows gives ≈1,400 / 8,591 ≈ **16 % native harfbuzz**; the
remaining ≈84 % is CPython interpreter machinery (eval loop, tuple/generator alloc+dealloc,
dict lookup, richcompare). Note `_Py_MakeCoro` / `gen_dealloc` / `tupleiter_*` — generator-heavy
pure-Python code. In the call graph, `hb_shape_full` accounts for 1,345 of the 1,525 samples
under one `_PyObject_MakeTpCall` branch; a separate 2,392-sample branch is leaf-in-eval-loop.

Wall 74.65 s vs child CPU 46.07 s ⇒ ~62 % CPU utilisation — the shortfall is either real I/O
(264 MB surface + 59 MB audit + gz subsets) or concurrent-agent contention. Contention-suspect.

## Cited-from-commit-message performance figures (NOT measured here)

- `c2336237` "Cut the M1 table rebuild from fifty minutes to twelve" — memoize traces over the
  collapsed left state; rule folding / outcome-partition built from present rows only (O(rows),
  was full right1×right2×right3×right4 label product); prospect-joint flag pass indexes by right1.
  Byte-identical TSVs across all six configurations.
- `a649d630` "Stop a one-rune edit from re-tracing every M1 window from scratch" — persisted
  per-config trace memo (`trace-memo-<config>.ndjson.gz`), per-entry invalidation by prose-blind
  rune digests + whole-store stamp. *"default configuration: fresh 149s, an unchanged-rune
  rebuild 55s, a single-rune edit about 94s serving two-thirds of its entries."*
- `c569e198` "Stop the stylistic-set configurations from re-tracing what the default already
  settled" — in-process `trace_memo.TraceShare` across the six acceptance configs;
  *"the build stage costs roughly a third of the CPU it did."*
- `a894e226` "Make a review-surface rebuild cost the edit, not the alphabet" — per-unit content
  key + persisted unit cache. *"a full build 114s, a rebuild with nothing changed 72s, and a
  simulated one-letter edit 84s with the per-unit phase falling from 53s to 13s."*
- `704bd210` "Stop the surface build from re-shaping every window before the letters go up" —
  persisted ink-signature store + one memoized shaper per font per worker. *"The load phase goes
  from forty-seven seconds to five and a warm rebuild lands near thirty."*
- `8dea2628` "Stop the conform gate from paying for the same witness twice" — candidate dedup,
  persisted per-config witnesses, inherited boundary-gate structural checks.
  *"the gate drops about 16% on a warm cache."*
- `b0860f45` "Stop gate:conform re-settling windows it has already seen" (memoized
  `conform._SettledWindowWalk`).
- `16af0310` "Stop depth-3 windows fanning out over third tokens that can't matter"
  (`table.third_slot_filter` / `fourth_slot_filter`).
- `45f95235` "Stop the witness gate from rebuilding tables the build already wrote" —
  *"A rebuild suite run now costs roughly half the wall time it did."*
- `b0a2895e` "Stop the drafts gate from enriching the whole workload once per worker" — session
  fixture + flock'd gzipped-pickle cross-process cache. *"about seven CPU-minutes"* given back.
- `8774c7d3` "Load the review workload once a suite run, not thirteen times."
- `77e66e84` "Stop a rebuild suite run from deleting real files out of the working repo" — the
  module *"runs in about a third the time, having stopped hashing the whole repo and sweeping
  the filesystem."*
- `1d7691dc` / `74b95d9a` / `abba437a` / `22827249` / `76ed99b3` / `6384b959` — scheduling and
  green-record work (deferral, plumbing green, census replay, stale-pin deferral, server policy).
- `612d645a` — added the timing journal itself.

## Cited-from-docstring figures (NOT measured here)

- `rebuild/tools/artifact_cycle.py` docstring: gate:make-test is *"~15 CPU-minutes"*.
- Same docstring, on the complaint docket: *"trading ~3s once for the whole chain's ~23s on
  every pass after"* ⇒ verdict-plumbing chain ≈ 23 s, complaints ≈ 3 s.
- Same docstring: gate:rebuild's wall time *"roughly tripled"* when co-resident with another
  full-width pytest pool (hence the default `queue` pool policy).
- `Makefile` comment: `make test-leaks` is a *"Deep (≈1 min) isolation-leak gate"*.
- `CLAUDE.md`: full suite ≈3 min under xdist, ≈20 min single-threaded (6× tax).
