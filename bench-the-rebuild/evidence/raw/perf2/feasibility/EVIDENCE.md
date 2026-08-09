# Rewrite feasibility — measurements taken by this slice

All measured on this box (M4 Pro, CPython 3.14.6, `uv run`), read-only against the repo.
Scripts alongside: `closure.py`, `loc.py`, `loc2.py`, `funcsizes.py`, `portscope.py`,
`specdump.py`, `split.py`, `liveness.py`, `scaling.py`.

## 1. Import closure of the settlement kernel (MEASURED, ast walk)

`rebuild.pipeline.table` + `rebuild.pipeline.settle` closure = 6 source modules, 4,490 lines:
table 1651, settle 1554, trace_memo 454, model 357, fingerprint 248, specificity 226.
`trace_memo`/`fingerprint` enter only via a `TYPE_CHECKING` import plus caller injection —
runtime closure of `build_tables` is table + settle + model + specificity.
Third-party: **none** (stdlib + `yaml`, and `yaml` only through trace_memo's fingerprint hop).

`rebuild.pipeline.run_m1` closure = 27 modules, 15,859 lines, pulling fontTools, uharfbuzz,
tornado, livereload, `tools/build_font.py`, `test/test_shaping.py`.

## 2. Spec handoff (MEASURED)

`load_default_spec()` = **0.102 s**. Mechanical dataclass→JSON of the whole `ResolvedSpec` =
**155.1 KiB**, 3 ms to serialize, **20 distinct dataclass types**, ~950 instances:
238 Condition, 188 Provenance, 116 When, 113 PolicyRecord, 66 SurfaceRow, 50 Bitmap,
30 Stance/Surface/Pairings, 18 Rune/Policy.
18 runes (15 letters + 3 ligatures), 30 stances, 113 policy records, 8 predicate classes,
4 heights, 4 features, 57 registry families (44 with codepoints, 13 sequences).

## 3. Where build_tables' time goes (MEASURED, subset specs, one config, no store/share)

| slice | 8 runes | 12 runes |
|---|---:|---:|
| fixpoint + liveness | 98.8% | 98.5% |
| `_flag_prospect_joints` | 0.6% | 0.9% |
| `_rules_for_input` (405 lines, the largest function in table.py) | 0.6% | 0.6% |
| `assert_outcome_partition` (post) | 1.0% | 1.8% |
| `assert_e_stranded` (post) | 0.1% | 0.1% |

**Inside the fixpoint, `_ProspectLiveness` dominates** (12 runes, MEASURED):
`third_live` 1,564 calls / 7.395 s outermost, `fourth_live` 18,809 calls / 0.062 s;
**78.7% of build_tables is inside the deep-slot liveness probes**, 21.3% everything else
(including every `transition_trace` for all 101,751 windows).
Reproduces the cost model's cited 84% cumulative / 79.3% leaf.

## 4. Port scope by line trace (MEASURED, 8-rune build, sys.settrace)

Functions `build_tables` never enters:
settle.py 228 lines of 1554 (`form_ligatures`, `tokens_from_codepoints`, `settle_traces`,
`transition`, `settle`, `word_position`, `_incomparable_message`, `_apply_resolution`*,
`_left_exit_stroke`*, `_rune_entry_strokes`* — *reachable on the full spec, just not this subset);
table.py 192 lines of 1651 (all serialization + both assertions + rule-row codecs).

## 5. Alphabet scaling (MEASURED — new; nobody had measured this)

One config, no trace store, no share, nested rune subsets (ligature closure first):

| runes | letters | windows | rules | cells | CPU-s |
|---:|---:|---:|---:|---:|---:|
| 6 | 4 | 9,165 | 182 | 44 | 1.048 |
| 8 | 5 | 22,523 | 229 | 52 | 2.273 |
| 10 | 7 | 48,834 | 293 | 69 | 4.628 |
| 12 | 9 | 101,751 | 508 | 103 | 10.342 |
| 14 | 11 | 183,347 | 735 | 122 | 22.931 |
| 16 | 13 | 382,846 | 1,848 | 157 | 51.619 |
| 18 | 15 | 682,842 | 2,667 | 197 | 92.176 |

The 18-rune row reproduces the cost model's artifact exactly (682,842 / 2,667 / 197).

CPU exponent in **letters**: 4.28 (9→15), 4.49 (11→15), 4.05 (13→15). It has been *rising*
across the sweep (2.7 → 3.2 → 4.4 → 5.2 → 6.1 → 4.9 on consecutive rune pairs).

Projection to the registry's full 44 codepoint-bearing letters (DERIVED, exponent 4.0–4.5):
**74–127x** growth → six-config cold build **6.9–11.9 hours** in shipped Python,
21–36 min Rust serial, 4.3–7.4 min Rust six-way parallel.

Alphabet headroom at today's 337.9 s six-config wall (DERIVED, exponent 4.25):

| accelerator | letters supported |
|---|---:|
| shipped Python | 15.0 |
| six memoizations + NamedTuple + gc off (MEASURED 2.39x) | 18.4 |
| + free-threaded config parallelism (DERIVED ~5.3x) | 22.2 |
| Rust serial vs shipped (MEASURED 20.1x) | 30.4 |
| Rust serial vs *levered* × six-way (MEASURED 8.4x × ~4.8x) | 35.8 |
| Rust six-way parallel vs shipped (MEASURED 96.5x) | 44.0 |

In runes (57 needed for full migration, 18 today): 96.5x buys 52.7 at exponent 4.25,
56.4 at exponent 4.0.

## 6. Churn (MEASURED, git log)

`rebuild/` created 2026-06-10; measured over its whole 2-month life.
Commits touching settle.py OR table.py OR model.py OR specificity.py: **23**
(3 in June, 11 in July, 9 in August).
Commits touching `glyph_data/runes/`: **104**. Ratio ≈ 1 kernel-semantics commit per 4.5 rune edits.
Recent semantics diffs, settle+table+conform lines changed:
- 3eb6de5f "Score every promised join by what the follower will actually do" — 498
- 402c691e "Teach the letters to see four ahead" — 457
- eaf5820d "Teach the letters to see three ahead" — 492
- 2f180411 "Don't prefer ·Day+Utter so hard…" — 357
- fe73b51a "Let a rune record which preference wins when two runes' votes cross" — 224

## 7. Consumers of `settle` outside the fixpoint (MEASURED, grep)

19 non-test modules reference `settle`: conform.py (**75** references, drives
`Engine`/`settle_with_engine` per text and `formation_blocked` throughout the witness machinery),
emit_gsub.py (6, `formation_blocked` generates the formation-guard `ignore sub` rows),
trace_memo.py (5), run_m1.py (4), explain.py, probe.py, tablediff.py, unit_cache.py,
drafts.py, enrich.py, manual_pins.py.

## 8. Existing differential harness

`rebuild/test_rule_witnesses.py::test_the_stamped_table_is_what_a_fresh_fixpoint_builds`
already compares a serialized enumeration against a fresh in-process fixpoint:
rules, reachable cells, and every `(key, outcome)` pair. Same shape a Rust-vs-Python diff needs.
`table.write_windows` is byte-stable by construction (sorted, gzip mtime=0);
`table.windows_digest` exists; the head carries `cited_provenance` and `identity_guard_rules`.
`read_windows` and `run_m1.serialized_tables` are a working deserializer.
No reader exists for `treaties-<config>.tsv` (writer only).

## 9. Environment

CI (`.github/workflows/deploy.yml`, ubuntu-latest) runs `uv run pyright` and
`tools/build_font.py` only — the rebuild pipeline never runs in CI. `pyproject.toml`
declares two runtime deps (fonttools, pyyaml); no compiled first-party code anywhere.
1,162 tests collected under `rebuild/`.
