# M1 integration recon: glob safety, promotion map, baseline access, mini-font path, schema infra

Recon B for milestone M1 (migrate qsIt, qsTea, qsPea, qsMay into the §3 rune-file format; stand up the §14 module skeleton). Everything below was verified against the working tree at the current HEAD (`a7fabef`), with the old build's Senior Sans OTF at SHA-256 `3211a7a7…25cf35`.

## 1. Glob safety: where the old pipeline discovers YAML, and the verdict on the §2 location

### The single discovery point

The old pipeline has exactly one YAML discovery site: `load_glyph_data` in `tools/build_font.py` (line 91):

```python
files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
```

`Path.glob("*.yaml")` is **non-recursive** — files in subdirectories of `glyph_data/` are invisible to it. Every consumer goes through this function with the `glyph_data/` directory: `make all` (`tools/build_font.py glyph_data/ site/`), `test/test_shaping.py:385`, `test/test_calt_regressions.py:3507`, `test/test_quikscript_context.py:19`, `test/test_quikscript_ir.py` (several call sites), `tools/audit_anchor_geometry.py:260`, `tools/inspect_join.py:216`, and `tools/derived_demote_oracle.py`. A repo-wide grep of `tools/*.py` for `glob|listdir|iterdir|rglob|walk(` confirms there is no other filesystem sweep (the other `walk` hits are AST/dict walkers).

### What each candidate file would do to the old build

- **`glyph_data/runes/qsPea.yaml` … (any subdirectory):** invisible to the glob, invisible to every test, zero effect. **Safe today.**
- **`glyph_data/script.yaml` (or any new file directly in `glyph_data/`):** swept in. Worse than merely loaded: `load_glyph_data` classifies every document carrying none of the `_STRUCTURAL_KEYS` (`metadata`, `glyphs`, `glyph_families`, `context_sets`, `kerning`, plus the three override-table keys; lines 62–73) as a **bare Senior kerning rule** (lines 114–115). A registries-only `script.yaml` has none of those keys, so it lands in `senior_kerning_rules`, and `generate_kern_fea` (lines 1168–1174 → 297) then does `definition["right"]` / `definition["value"]` on it — a `KeyError` that kills the Senior build outright. Even a non-crashing document would add a kern lookup and break byte identity. **Unsafe until cutover.**

### Other sweeps checked (all clean for new files outside `glyph_data/*.yaml` and the existing test/site trees)

| Sweep                | Scope                                                                                                             | Effect on M1 files                                                                                                            |
|----------------------|-------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `make all` / targets | Only `build_font.py glyph_data/` plus typst over `site/print.typ`                                                 | None for subdirectories of `glyph_data/` or anything under `rebuild/`                                                         |
| pytest collection    | `testpaths = ["test", "site"]` in `pyproject.toml`; the root `conftest.py` collects only three named HTML corpora | `rebuild/` is never collected by `make test`; M1 tests run via explicit `uv run pytest rebuild/ -n auto --dist worksteal`     |
| `make prettier`      | `black -q tools/ test/ conftest.py`                                                                               | Does **not** format `rebuild/`; M1 must run `uv run --with black black -q rebuild/` itself (line-length 110 from `pyproject`) |
| pyright gate         | `include = ["tools", "test", "conftest.py"]`                                                                      | `rebuild/` is unchecked by `make test`'s `AMS_RUN_PYRIGHT` gate; type-check it explicitly if desired                          |
| pre-commit           | `black --check` on pre-push only                                                                                  | Same scope as black above                                                                                                     |
| markdownlint-cli2    | `**/*.md` (ignores `.venv`, `node_modules`, `.uv-cache`, `tmp`)                                                   | **Does** sweep `rebuild/**/*.md` — M1 reports and plans must stay lint-clean                                                  |
| VS Code yaml.schemas | Maps only `glyph_data/quikscript.yaml` and `glyph_data/metadata.yaml`                                             | New YAML gets no editor schema until a mapping is added (optional; touches `.vscode/settings.json`, which is not build input) |

`.gitignore` covers `rebuild/out/` already; new source files stay untracked (M1 never commits), and untracked files cannot affect the old build given the glob shape above.

### Verdict

The design-§2 location is **half safe today**: `glyph_data/runes/*.yaml` can be created at its final address now with zero risk and no later move; `glyph_data/script.yaml` **must not exist until cutover** — it would crash (or at best alter) the old Senior build. Recommendation for M1-PLAN Phase 2: put rune files at `glyph_data/runes/`, park the registry file under `rebuild/` (e.g. `rebuild/script.yaml`) or inside `glyph_data/runes/`, and have `spec_load` take both paths explicitly rather than globbing — if the registry lives inside `runes/`, the loader must address it by name (or glob `runes/qs*.yaml` for runes) so it is never mistaken for a rune file. After creating the files, re-verify the old build once: `make all` then `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` against `3211a7a7…25cf35`.

## 2. Promotion map: prototype modules → §14 real modules

The prototype proved the architecture end to end; the real modules keep its skeletons and replace its hand-encoded inputs. Per module:

### `prototype/spec.py` → `spec_load` + part of `surface`

- **Lift:** the resolved-surface IR shape — `Selector.matches` (positive scope with optional `exit_y` and `feature`), `EntryRow` / `ExitRow` / `FamilySpec` (allowlist polarity via `from_scope` / `toward_scope`, `pairings_only`, `intrinsic`, `anchor_kept_at_boundary`, `extend_after` / `extend_toward`, `extension_suppressed_when_left_extended`), `GlyphRecord` with `entry_curs_only`, `anchors_in_font_units` (the conformance gap-check hook), `NOENTRY_PARITY_HEIGHTS` (locked-twin NULL/NULL curs parity), `MARKER_FAMILIES` / `ENTRY_BEARING_FAMILIES` / `LOCKED_EXIT_REUSES_PLAIN` as registry concepts, and `RefusalRecord` as the window-decidable refuse shape.
- **Prototype-only:** the hand-encoded `FAMILIES` / `GLYPHS` literals, the two synthetic encoding probes, the hard-coded `CODEPOINT_TO_TOKEN`.
- **Deltas:** populate the same IR from `runes/*.yaml` + the registry file (stances, ways, surfaces, cells, bindings, policy records); add qsPea; schema validation and the §3.1 naming linter (reject stance IDs matching `(before|after|noentry|noexit|nonjoining|ss[0-9])` — note the prototype's `.noentry` / `.after-it` strings are _generated glyph names_, which remain legal; the lint targets authored stance IDs); `pairings: never:` as well as `only:`; predicate classes and rune-local groups; ductus-parity gating.

### `prototype/settle.py` → `settle`

- **Lift:** `transition()` is the §6.1 ranking core and survives nearly whole — entry binding with the committed-seam-is-bilateral rule and the `E-STRANDED` raise, candidate enumeration over `_allowed_exit_rows`, the refusal-aware lookahead closure (`_toward_matches` + `_acceptor_exists` + `_refused`), join-count scoring with `_prospect`'s deliberately optimistic third term, the structural-floor tiebreak (lower seam height, then declaration order), the joint flag on floor-broken realization ties, extension logic including same-seam suppression, and the `LeftContext` / `RightToken` / `Settled` frames plus run-splitting in `settle()`.
- **Prototype-only:** `_glyph_name`'s per-family special cases (qsMay/qsIt naming hacks) — exactly what generated display names from the structured `(rune, stance, cell, adjustments)` tuple replace; `SETTLED_PAIR_CELLS` as a literal dict.
- **Deltas:** real specificity ordering per §6.2 (extensional match-set inclusion — the prototype has none; this is the module the design budgets extra paranoia for, with its own regression-test class), `prefer` records in both modes participating from both seam runes, `resolve` records and `E-INCOMPARABLE` / `E-AMBIGUOUS`, word-position and namer-dot boundary semantics, the cell lattice with withdrawal bindings instead of name hacks, stratified capability-then-policy evaluation.

### `prototype/table.py` → `table`

- **Lift:** the fixpoint enumeration over reachable left states (`_enumerate_config`'s worklist — exact, not string-enumerated), outcome-partition compression (`_signature_blocks` + `_rules_for_input`), the feature-fold-into-marker with the conflict-free assertion, the §7 rule-ordering discipline (boundary rows with explicit `uni200C` first, two-slot before one-slot, identity rows omitted, slot-dropped fallback last), the ZWNJ backtrack-slot coverage guards for never-locked inputs, `_validate_rules` (first-match-wins replay of every transition against the ordered rules), and `DecisionTable.write_tsv` as the germ of `build/settlement.tsv`.
- **Deltas:** treaty-table emission (`build/treaties.tsv` — reachable cell pairs with join height, summed extension, kern), provenance pointers to YAML records instead of line-range strings, the §8 capability matrices, `joint`-row routing into the expensive conformance tier, multi-configuration registry from the script registry instead of the hard-coded pair.

### `prototype/emit.py` → `emit_gsub` + `emit_gpos`

- **Lift:** `_ClassRegistry` (deduped class definitions), `_settle_rules` (chained-context single-substitution rendering), the four-stage GSUB in definition order (formation → marker → ZWNJ chokepoint → one settlement lookup; definition order fixes LookupList indices and hence cross-feature order on both shapers), `_curs_statements` (per-height cursive lookups, NULL anchors for cross-height cells, parity registrations), and `_assert_invariants` (no locked twin or chokepoint output in any raw lookahead class; zero `ignore sub`).
- **Deltas:** per-family `subtable;` breaks and the sanctioned Extension promotion at scale (the K2 finding: fine with modest classes, needed for 108-member classes), per-rule provenance comments (§6.3b), kerning emission (class-based PairPos + type-8 contextual), the ss10 overlay stage, the namer-dot mini-calt (see §4 below).

### `prototype/build.py` → `compile_font`

- **Lift:** the verified read-only recipe — `build_font(glyph_data_dict, out_path, variant="senior", senior_fea=fea)` with legacy `glyphs:` records only (qs glyph keys suffixed `.prop`), empty `glyph_families` so the old IR emitter never runs, a metadata dict, and the `.fea` sidecar for free; `_report_gsub_budget` reuse; `_settle_lookup_metrics` (OTTableWriter-compiled lookup bytes) if budget reporting stays wanted.
- **Prototype-only:** the K1/K2 extrapolation arithmetic, `budget.json`, kill-criterion verdicts.
- **Deltas:** glyph records come from the `geometry` module's resolved cell bitmaps instead of `spec.py` literals; metadata from `glyph_data/metadata.yaml` values if M1 wants metric parity with the real Senior font (the prototype used its own `Proto` metadata).

### `prototype/conform.py` → `conform`

- **Lift:** `Shaper` (MONOTONE_CHARACTERS cluster level so ZWNJ keeps its own cluster; glyph names via `TTFont.getGlyphName`, never HarfBuzz's 63-byte-truncating API), the enumeration loop, `check_oracle` with the ZWNJ-sentinel normalization, `check_zwnj_structure` (zero advance, no ink), `check_split_buffer` (outline+position signatures, name-blind because locked twins are bitmap-identical), `check_join_gaps` (gap-0 pen positions via `anchors_in_font_units`), and `RuleCoverage` (every emitted rule exercised at least once, with the raw-pipeline replay). The `Oracle` adapter's probing indirection is unnecessary once `settle` is a real module — call it directly.
- **Deltas:** exhaustive length-1–5 enumeration stays feasible at M1 scale (≈9 symbols) and should be kept; the §10 tier-3 per-transition shortest-example generator is the growth path. The big addition is the **baseline comparison** (§3 below) with the intended-divergence carve-out, and per-configuration runs from the registry instead of the hard-coded `{default, ss03}`.

### `prototype/coretext_smoke.py` / `.swift` → part of `conform`

- **Lift:** essentially as-is — the swiftc compile-per-session recipe, point→font-unit conversion (×upem/100), GID-for-GID + position diffing, and the ZWNJ-slot structural-contract exclusion. Extend `smoke_sequences.txt` for the M1 alphabet (qsPea rows, the four-family seams, ligature windows).
- **Prototype-only:** the K3 `budget.json` wiring (`record_k3_half`).

## 3. Baseline access for M1 conformance

### Reading API and row model

Two parallel row models exist; use **`rebuild/validation/rowmodel.py`** for reading — it has the gzip-aware `open_table`, `iter_rows` (yields `Row` objects, skipping `#` headers), `iter_line_chunks` for parallel scans, `read_header` / `header_config_token`, and `config_token_for_features`. (`rebuild/baseline/model.py` is the extractor-side twin with the same TSV shape and the `CONFIGS` registry.) A `Row` carries:

| Field        | Content                                                                                            |
|--------------|----------------------------------------------------------------------------------------------------|
| `codepoints` | The input string, e.g. `(0xE652, 0xE670)`; serialized `E652:E670`                                  |
| `glyphs`     | Resolved output glyph names per position (full names via TTFont — immune to 63-byte truncation)    |
| `clusters`   | Output-glyph → input-character mapping (ligation shows as merged clusters)                         |
| `seams`      | Per-adjacency classification: `y0` / `y5` / `y6` / `y8` / `lig` / `break` (GPOS curs intersection) |
| `positions`  | Per-glyph `(x_offset, y_offset, x_advance)` — the kern/advance channel, extension amounts included |

### Recommended approach: one streaming filter pass, cached sub-tables

The M1 sub-alphabet is `{0x0020, 0x00B7, 0x200C, 0xE650 qsPea, 0xE652 qsTea, 0xE665 qsMay, 0xE670 qsIt}` plus `0xE679 qsOy` (for `qsTea_qsOy`) and `0xE67B qsOut` if `qsOut_qsTea` is in scope — 8–9 symbols, so 4,680–7,380 strings per configuration out of 4,985,760. Stream each `rebuild/out/baseline-<config>.tsv.gz` once with `iter_rows`, keep rows where `set(row.codepoints) ⊆ subset`, and write a filtered per-configuration sub-table (same header + format) under `rebuild/out/` (gitignored) so conformance re-runs never re-scan. Cost calibration: the split-check's full pass over all 54.8M rows took 32.9 s, so this is a one-time ~minute. Canonical (length, codepoints) row order is preserved by filtering, keeping the sub-tables diff-stable.

Configurations: qsTea carries the ss02/ss03/ss05 unlocks, qsIt the ss04 gates, qsMay an ss03-gated extension; ss06/ss07 live on other families and ss10 is the global isolated overlay. The cheap and safe choice is to filter all 11 tables; the load-bearing ones for M1 acceptance are `default`, `ss02`, `ss03`, `ss04`, `ss05`, `ss02+ss03`, `ss02+ss03+ss05`, plus `ss10` if M1 emits the overlay.

### What conformance must compare

New-pipeline glyph names are generated display names, so name-for-name equality against the baseline is meaningless without the §13.3 alias table (old compiled name → new cell tuple; for four families a hand-written alias map is small and fine). The semantic comparison that works without full aliasing, per row: **(a)** ligation (cluster merge in `clusters` / ligature glyph in `glyphs`), **(b)** every seam's classification from `seams` (join height or break — on the new font, re-derive with `rebuild/validation/classify.py`'s `SeamClassifier`, which reads any font's GPOS curs lookups black-box), **(c)** `positions` for advances and kerns (extension amounts surface here as advance/anchor deltas). The BASELINE-REPORT's audit confirmed join height, variant choice, extension amount, ligation, and break-ness are all recoverable from these fields. Variant-choice comparison (which cell was picked) is where the alias map earns its keep.

### Identifying the intended-divergence rows

`rebuild/out/equivalence-triage.tsv` (6.19 GB, plain TSV) has columns `config check codepoints baseline_glyphs boundary_glyphs first_divergent_position baseline_seams boundary_seams divergence_kind`. Filter the same way: `codepoints` restricted to the sub-alphabet (a streaming scan or even `grep -E` on the third column; the subset is a few thousand rows). The carve-out logic for M1 acceptance:

- **`zwnj-vs-edge` rows** are the designed §3.4 divergence class: post-ZWNJ ≡ word-initial is true by definition in the new model. Where the new pipeline's post-ZWNJ outcome differs from the baseline's post-ZWNJ row, it is _correct_ iff it matches the corresponding word-initial (edge) outcome — and the triage row already carries both sides (`baseline_glyphs` = edge-shaped, `boundary_glyphs` = ZWNJ-shaped), so the check needs no re-shaping of the old font.
- **`space-vs-edge` rows** (the boundary-guard asymmetry, e.g. ·Excite·Tea) are the same shape: space splits runs in the new model, so post-space ≡ edge by definition, and these become intended divergences with the same both-sides-recorded acceptance rule.
- **`edge-vs-zwnj` rows** are position-only (kerns firing against the literal `uni200C` glyph). §12 keeps contextual ZWNJ kerns as a proven pattern, so these are _not_ automatically intended divergences — M1 should carry them as explicit triage decisions if any touch the subset.

## 4. The glyph compiler path for the M1 mini-font

What `prototype/build.py` proved: build the decision table, emit the FEA, then hand `build_font` a synthetic glyph-data dict containing **only legacy `glyphs:` records** (per glyph: `bitmap` rows, `y_offset`, optional `advance_width`; qs-named glyphs keyed `<name>.prop` so the senior variant compiler picks them) with `glyph_families` empty — so `compile_quikscript_ir` and the entire old FEA emitter never run — and the hand-built FEA threaded through `senior_fea=`. Output is the OTF plus a `.fea` sidecar, and `_report_gsub_budget` runs against the result. This recipe is read-only with respect to the old pipeline and carries straight into M1's `compile_font`.

The M1 font's glyph inventory:

- **All settlement-reachable cells** of qsPea, qsTea, qsIt, qsMay — minted from `table.reachable_glyphs` exactly as the prototype did (mint-on-reachability is the design's own rule).
- **Ligatures:** `qsTea_qsOy` (lead in the family set) and `qsOut_qsTea` (trail in the family set) are the only two ligatures in `glyph_data/quikscript.yaml` touching the four families (lines 708, 3303). Their formation partners qsOy (and qsOut, if included) come along as inert runes, prototype-style.
- **Boundary glyphs:** `space`, `uni200C`, and `periodcentered` (the namer dot — it is in the baseline alphabet and is a §3.4 condition value). Note: `build_font` appends the namer-dot calt itself via `_namer_dot_calt_fea` (build_font.py:1182), but that pass reads compiled `join_glyphs`, which are empty on the `senior_fea`-override path — so M1's emitter must include its own namer-dot stage (the §7 table says the existing final mini-calt carries over unchanged) or M1 explicitly scopes dot-lowering out of the mini-font.
- **Locked twins and markers:** `.noentry` chokepoint twins for every entry-bearing raw input (bare runes and marker twins), with the curs coverage-parity NULL/NULL registrations; ss marker twins for the sets that affect the four families (ss02/ss03/ss05 on qsTea, ss04 on qsIt), composite markers only if a multi-set configuration demands one (§7 unlock row).

Where geometry plugs in: between `table` and `compile_font`. The prototype hand-drew all 30 cell bitmaps in `spec.py` (`_MAY_MONO_EN_EXT_EX_EXT` and friends); M1's `geometry` module computes them instead — resolve each reachable cell's bitmap by the §3.2 order (explicit `cells:` binding > side bindings: `stub` / `joined` / `withdrawal` > base bitmap), apply `extend` / `contract` same-row connector arithmetic and `trim`, apply `bind:` hand-drawn siblings with per-cell anchor overrides, then place anchors per the standing conventions (`entry.x = min_ink_x_at_entry_y`, `exit.x = max_ink_x_at_exit_y + 1`, flagged exceptions). The output per cell is exactly the prototype's `GlyphRecord` shape (generated name, bitmap rows, `y_offset`, `entry` / `exit` / `entry_curs_only`, advance), which drops into the `_glyph_data()` dict unchanged. The §9 defect gates (`E-UNREALIZED` gap arithmetic, `E-DANGLE`, anchor-convention drift, off-anchor contact) all run against these same resolved bitmaps before any font is built.

## 5. JSON-schema infrastructure

- **An authored schema exists, but it is editor-only.** `.vscode/quikscript.schema.json` (593 lines, JSON Schema draft-07, `additionalProperties: false` discipline throughout — the `strip_entry_before` description CLAUDE.md cites is at line 451). It is wired to `glyph_data/quikscript.yaml` via `yaml.schemas` in `.vscode/settings.json` (with `permissive.schema.json` for `metadata.yaml`), so validation happens only in the VS Code YAML extension; nothing validates at build or test time. Error messages in `tools/quikscript_ir.py` (lines 2665, 2956) ask authors to keep the schema in sync by hand.
- **No Python validator is installed.** `uv.lock` contains no `jsonschema` (runtime deps: fonttools, pyyaml; dev: uharfbuzz, pytest, pytest-xdist, livereload, pyright, pre-commit, plus transitives).
- **Recommendation for the rune-file schema:** author it as a real JSON Schema (draft-07 or 2020-12) so the §3.4 closed `when:` vocabulary's `additionalProperties: false` is enforced mechanically and editors get it for free, and run it in the §10 tier-1 static-validation pass via `uv run --with jsonschema …` — the same zero-footprint pattern the Makefile already uses for black (`uv run --with black black …`), avoiding any `pyproject.toml` / `uv.lock` edit during M1. (Adding `jsonschema` to the dev group later is a one-liner needing user sign-off.) The checks JSON Schema cannot express — ductus parity, the stance-ID naming lint, extensional specificity, dead policy, refuse/require `right.then` rejection — belong in `spec_load` Python regardless; note `right.then` rejection and the stance-ID pattern _can_ additionally be hardened in the schema itself (`not` + `pattern`), which is worth doing so the editor catches them too.

## 6. Verdict summary

1. **Glob safety:** `glyph_data/runes/*.yaml` is safe at its final §2 address today (the only discovery glob is non-recursive); `glyph_data/script.yaml` would crash or alter the old Senior build and must be parked (under `rebuild/`, or inside `runes/` addressed by explicit name) until cutover. Nothing in make targets, pytest collection, black, pyright, or pre-commit sweeps the new locations; markdownlint sweeps `rebuild/**/*.md`.
2. **Promotion:** the prototype's settlement kernel, fixpoint table builder, outcome-partition compression, rule-ordering discipline, emitter invariants, `build_font(senior_fea=…)` recipe, and both conformance harnesses lift into the §14 modules nearly intact; the deltas are YAML-fed spec loading (with qsPea), §6.2 extensional specificity, policy-record semantics (prefer/resolve), geometry-computed cell bitmaps, and the defect gates.
3. **Baseline:** stream-filter the gzipped per-configuration tables once with `rebuild/validation/rowmodel.iter_rows` into cached sub-tables (a few thousand rows each); compare ligation, per-seam classification, break-ness, and positions (plus cell identity through a small hand alias map); carve out `zwnj-vs-edge` and `space-vs-edge` triage rows as intended divergences whose acceptance test is "matches the edge-shaped side of the recorded triage row".
4. **Mini-font:** prototype recipe verbatim, with geometry replacing hand-drawn cell bitmaps, two ligatures (`qsTea_qsOy`, optionally `qsOut_qsTea`), boundary glyphs including the namer dot (whose calt stage the `senior_fea` path must supply itself), locked twins, and ss02/ss03/ss04/ss05 markers.
5. **Schema:** write a real JSON Schema for rune files; validate in CI/tier-1 via `uv run --with jsonschema`; keep the deeper lints in `spec_load`.
