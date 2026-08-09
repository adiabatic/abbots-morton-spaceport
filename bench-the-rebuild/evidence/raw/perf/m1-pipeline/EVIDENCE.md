# M1 pipeline — measured evidence (slug: m1-pipeline)

Machine: Apple M4 Pro, macOS 26.6.1, Python 3.14.6 (CPython, GIL). All Python via `uv run`.
Nothing outside this directory was written. `rebuild/out/` keeps its July mtimes; verdict stores untouched.

Note on staleness: the artifacts in `rebuild/out/m1/` were built 2026-07-26 from a 15-letter alphabet
(inputs stamp `babc9f14…`, no `+simulated-prospect+vote-slots` suffix). The current tree stamps
`fb6c73d9…+simulated-prospect+vote-slots` and has an 18-token alphabet (15 letters + 3 boundary tokens),
6 acceptance configs (not 8). Counts read off `rebuild/out/m1/*.json` are therefore July counts and are
labeled as such; everything else here was measured against the current tree.

## Measured: table build (`table.build_tables`), one configuration, cold, nothing written

| run | CPU s | wall s | windows | rules | maxrss |
|---|---|---|---|---|---|
| default, flags on, no store, uncontended | 81.28 | 82.48 | 682,842 | 2,667 | 4.05 GB |
| default, flags on, no store, contended | 109–115 | 164–191 | 682,842 | 2,667 | 3.2–3.4 GB |
| default, `AMS_SIMULATED_PROSPECT=0 AMS_VOTE_SLOTS=0` | **8.44** | 8.44 | 479,371 | 1,165 | 0.61 GB |
| default, flags on, fresh persisted trace store | 120.0 (wall, driver `[t]`) | — | 682,842 | — | — |

`assert_outcome_partition` 3.19 s wall, `assert_e_stranded` 0.08 s wall (default, flags on).
`load_default_spec()` 0.10–0.13 s CPU.

Files: `timing-default.json`, `timing-default.log`, `timing-default-flagsoff.log`.

## Measured: all six acceptance configs with the cross-config trace share (nothing written)

`timing-all-configs-6.log` (ran partly under contention with a second identical process):

| config | CPU s | windows | rules | share_served |
|---|---|---|---|---|
| default (donor) | 109.3 | 682,842 | 2,667 | 0 |
| ss03 | 74.8 | 719,636 | 2,929 | 1,419,581 |
| ss04 | 47.4 | 665,875 | 2,662 | 1,340,331 |
| ss05 | 34.9 | 697,306 | 2,692 | 2,000,235 |
| ss03+ss05 | 55.0 | 724,875 | 2,950 | 1,405,321 |
| ss10 | 33.1 | 678,229 | 2,642 | 1,884,502 |
| **total** | **354.8** | ~4.17 M | — | peak rss 6.84 GB |

Contention-corrected estimate (default alone measured 81.3 s uncontended vs 109.3 s here, ≈0.74×):
≈260 s CPU for the whole table stage. Labeled derived.

## Measured: cProfile attribution of `build_tables` (fractions only — cProfile inflates ~8×)

`build_tables-default-cumtime.txt` / `-tottime.txt` (298.8 s profiled), `build_tables-ss04-*.txt` (381.7 s):

| frame | cum s | % of build_tables |
|---|---|---|
| `table.py:782 third_slot_filter.matters` | 250.4 | **84 %** |
| ` └ table.py:485 _ProspectLiveness.third_live` (5,515 calls) | 250.3 | 84 % |
| `   └ table.py:503 <genexpr> → fourth_live` (99,467 calls) | 189.8 | 64 % |
| `     └ table.py:576 _fourth_class_live → Engine._prospect` | 119.7 | 40 % |
| `   └ table.py:703 _seat_varies → table.py:730 _seat_outcome` | (ss04) 92.4 | 24 % |
| `   └ table.py:637 _vote_class_live` | (ss04) 66.8 | 18 % |
| `table.py:820 fourth_slot_filter.matters` | (ss04) 10.4 | 3 % |
| `settle.py:525 candidates` (9.16 M calls, 45.8 s tottime) | 136.4 | 46 % |

Top `tottime` (default): `candidates` 45.8 s; `dict.get` (58.0 M calls) 20.9 s;
`_transition_trace_uncached` 15.9 s; `_pairing_allowed` (21.1 M calls) 12.8 s;
`_virtual_left` (8.29 M calls) 11.4 s; `_prospect` 9.3 s; `_exit_sources` (10.5 M calls) 8.9 s;
frozen-dataclass `__hash__` `<string>:22` (51.5 M calls) 8.5 s; `cond_matches_right` (15.4 M calls) 8.5 s;
`builtins.hash` 67.6 M calls; `list.index` 17.0 M calls (ss04); `RightToken.letter` property 41.8 M calls (ss04).

## Measured: native-vs-Python split, `/usr/bin/sample` on the live default build

`sample-build-tables-default.txt`, 25,647 samples over 30 s, top-of-stack bucketed by binary:

| binary | samples | % |
|---|---|---|
| Python (interpreter + object machinery) | 24,344 | **95.6 %** |
| libdyld.dylib (`_tlv_get_addr`) | 815 | 3.2 % |
| libsystem_platform.dylib (memset/memcmp/memmove) | 301 | 1.2 % |

No harfbuzz, no zlib, no fontTools frames at all. Garbage collection is visible:
`gc_collect_main` 1,681 + `subtype_traverse` 593 + `visit_reachable` 473 + `visit_decref` 426 +
`tuple_traverse` 214 + `dict_traverse` 85 ≈ 3,472 samples ≈ **13.5 %** in GC.

## Measured: whole `run_m1.run()` build stage, liveness flags OFF (`stage-bench-flagsoff.log`)

```
build_tables_total 54.7s   (6 configs: 13.3 / 8.0 / 9.8 / 7.8 / 8.4 / 7.1)
glyph_minting       0.0s
defect_gates        0.1s
emit_gsub_gpos      0.0s
compile_font        0.9s   (feaLib + fontTools + pack_gsub + budget parse; 2,867 GSUB rules, 263 glyphs)
run cpu 55.7s  wall 55.8s  maxrss 0.95GB
```

So outside `build_tables` the whole build stage is ≈1 s. With the flags on, `build_tables[default]`
alone is 82–120 s, so the table stage is >98 % of the build.

## Measured: conformance sweep, one config, `bench_conform.py`

Scratch font + tables from `run-out-flagsoff/`. Per length: full `sweep_text` body
(shape + zwnj/split checks + walk + oracle + join gaps).

Flags OFF (`bench-conform.json`):

| length | sequences | CPU s | µs/text | shaping fraction | windows known |
|---|---|---|---|---|---|
| 3 | 5,832 | 0.51 | 87 | 10.7 % | 10,109 |
| 4 | 104,976 | 6.21 | 59 | **16.9 %** | 97,017 |

Flags ON (`bench-conform-flagson.json`), cost-only (outcomes meaningless against a flags-off font):

| length | sequences | CPU s | µs/text | shaping fraction | windows known |
|---|---|---|---|---|---|
| 3 | 5,832 | 47.98 | 8,227 | 0.18 % | 10,111 |
| 4 | 104,976 | 21.02 | 200 | 5.0 % | 98,295 |

The length-3 spike is the `_ProspectLiveness` probe pile being built (once per engine, per process, per config).

Isolated throughput (`bench-sweep.json`, 20,000 random length-5 texts, stale July M1.otf):
`shaper.shape` **11.6 µs/call**; `_SettledWindowWalk.walk` 2,806 µs/call cold with flags on;
warm second pass over the same texts **16.9 µs/text** (`walk-warm.json`);
`settle.settle()` with a fresh engine per text 254 µs/text.

`walk-cold-cumtime.txt`: 207.6 s of 210.0 s cumulative sits under `conform.py:580 _window_rights` →
`table.py:782 matters` → `third_live`.

## Measured: process-boundary payloads (`pickle-sizes.json`)

| object | pickled bytes | dumps | loads |
|---|---|---|---|
| `ResolvedSpec` | 64,595 | 0.001 s | 0.002 s |
| `DecisionTable` rules only (`read_windows(windows=False)`) | 301,239 | 0.001 s | 0.002 s |
| `DecisionTable` with 1,321,116 windows (July artifact) | **39,640,475** | **3.14 s** | **2.98 s** |

## Measured: baseline subset filter

`baseline_subset.filter_table` over `rebuild/out/baseline-default.tsv.gz` (4,985,767 rows, 42 MB gz):
**3.11 s CPU**, kept 111,150 rows. × 11 tables ≈ 34 s CPU (derived).
(The on-disk July subsets hold 54,247 rows each — the alphabet has grown since.)

## Measured: trace-memo shape and one-rune invalidation (`memo-invalidation.json`)

Default config, flags on: store holds **1,875,829 entries**, 17,338 distinct rune-sets, 135 provenance
pointers, 204 cells, 12.8 MB gz. Flags off the same store holds 209,051 entries.
Recipient configs store only their sensitive fraction (ss03: 791,756 flags-on; ss10: 1,666 flags-off).

Entries naming each rune (= entries a one-rune edit drops):

qsUtter 42.9 %, qsIt 38.5 %, qsNo 28.8 %, qsMay 28.3 %, qsTea 27.8 %, qsPea 23.8 %, qsDay 22.7 %,
qsFee 22.7 %, qsSee 22.1 %, qsAh 19.7 %, qsI 19.4 %, qsRoe 19.1 %, qsDay_qsUtter 18.4 %,
qsTea_qsOy 18.4 %, qsEt 18.3 %, qsOy 17.1 %, qsSee_qsUtter 15.9 %, qsLow 15.1 %.

Rune-set sizes: 1→17, 2→137, 3→696, 4→2,500, 5→6,487, 6→6,459, 7→1,042.

## Counts from the tree (current)

- 18 rune files; 18 modeled runes = 15 letters + 3 ligature runes (qsDay_qsUtter, qsSee_qsUtter, qsTea_qsOy).
- Conformance alphabet 18 tokens → exhaustive sweep per config Σ 18^L, L=1..5 = **2,000,718** sequences;
  × 6 configs = 12,004,308 sweep shapes, before witness top-ups.
- `ACCEPTANCE_CONFIGS` = ('default','ss03','ss04','ss05','ss03+ss05','ss10').
- Stances per rune: 1–4 (qsSee has 4); most have 2.
- July artifacts (stale): 8 configs, per-config windows 1,319,624–1,343,260, 1,041 rules/config,
  152 settled-cell glyphs, 209 total glyphs, 3,267 GSUB rules, 3,392 subtables, 109,970 GSUB bytes,
  conform_summary sequences 813,615, shaping_runs 14,335,037, topped_up_sequences 7,826,117,
  divergence-audit.tsv 59 MB.
