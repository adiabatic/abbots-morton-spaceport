# Recon A: build pipeline and current FEA encoding

Findings for the week-one de-risking prototype (doc/rebuild-design.md §7). All line numbers are against the current working tree (commit a7fabef). The generated Senior FEA referenced throughout is `site/AbbotsMortonSpaceportSansSenior-Regular.fea` (30,838 lines), a build artifact of `make all`.

## 1. How the Senior Sans OTF gets built

### Entry points and CLI

- `make all` (Makefile:3-6) runs `uv run python tools/build_font.py glyph_data/ site/`, copies Departure Mono into `site/`, and compiles `site/print.typ`. All six OTFs **and** the `.fea` sidecars land in `site/`.
- `tools/build_font.py` `main()` (build_font.py:1284) takes `<glyph_data.yaml|glyph_data/> [output_dir]`. There is no flag to build a single variant; `main()` always builds all six fonts (Mono/Junior/Senior × Regular/Bold) in parallel via `ProcessPoolExecutor` with worker `_build_one_font` (build_font.py:1268).
- `load_glyph_data` (build_font.py:76) merges every `glyph_data/*.yaml` into one `GlyphData` dict with keys `metadata`, `glyphs` (legacy flat glyphs), `glyph_families` (Quikscript IR source), `context_sets`, `kerning`, `senior_kerning`, and three override lists.

### The per-font pipeline (`build_font`, build_font.py:829)

1. `compile_glyph_set(glyph_data, variant)` (glyph_compiler.py:288) produces a `CompiledGlyphSet`: `legacy_glyphs` (the flat `glyphs:` records, with `.prop` suffixes stripped for proportional variants by `prepare_proportional_glyphs`, glyph_compiler.py:46) plus `join_glyphs` (the Quikscript IR compiled from `glyph_families` by `compile_quikscript_ir` in tools/quikscript_ir.py). `glyph_definitions` merges both into plain bitmap dicts.
2. Each glyph’s bitmap rows become 50×50-unit rectangles via `bitmap_to_rectangles` (build_font.py:540) and are drawn with `T2CharStringPen` into CFF charstrings (build_font.py:1058-1067). `FontBuilder(units_per_em, isTTF=False)` builds an OTF; UPM is 550 and `pixel_size` is 50 (glyph_data/metadata.yaml), so glyph-space pixel coordinates multiply by 50 to get font units. Bold is the same grid with each rectangle’s right edge widened by `pixel_width // 2`.
3. Feature code is assembled as a list of FEA strings (`fea_code_parts`, build_font.py:1143-1188): kern (proportional), ccmp, mark, then for Senior the big senior FEA plus contextual senior kerning, then the namer-dot calt appended last. The combined string is compiled into the font with `addOpenTypeFeaturesFromString(fb.font, fea_code)` (build_font.py:1189) and also written verbatim to `output_path.with_suffix(".fea")` (build_font.py:1191-1194) — that is where `site/AbbotsMortonSpaceportSansSenior-Regular.fea` comes from.
4. For Senior with an `output_path`, `_report_gsub_budget(output_path, fea_code)` runs automatically after save (build_font.py:1212-1213).

### Where the senior FEA comes from

`build_senior_fea` (build_font.py:776) heals override glyph names and calls `emit_quikscript_senior_features` (quikscript_fea.py:6857), which concatenates four parts:

1. `_emit_quikscript_curs` (quikscript_fea.py:6514) — the `curs` feature.
2. `_emit_quikscript_ss_gate` (quikscript_fea.py:6599) — the ss02/ss03/ss04/ss05/ss07 capability gates (these run **before** calt in feature-application order because the stances they substitute are calt inputs).
3. `_emit_quikscript_calt` (quikscript_fea.py:2278) — the entire join engine, one `feature calt {}` block with 616 lookups.
4. `emit_quikscript_ss` (quikscript_fea.py:6568) — the ss06/ss10 post-calt revert overlays (ss10 is auto-generated variant → isolated base).

The senior FEA is byte-identical for Regular and Bold, so `main()` emits it once (≈3.2 s) and threads it into both jobs via the `senior_fea` parameter (build_font.py:1312-1316); when `senior_fea` is non-None, `build_font` skips the emitter entirely (build_font.py:1163-1166). This is the prototype’s main hook — see §5.

### Minimal build commands

- Full standard build: `uv run python tools/build_font.py glyph_data/ site/` (or `make all`).
- One font programmatically (the only way to build just one): `build_font(glyph_data, out_path, variant="senior", senior_fea=...)` — verified working, see §5.

## 2. `_report_gsub_budget` (build_font.py:155)

```python
def _report_gsub_budget(font_path: Path, fea_code: str | None) -> None:
```

Behavior: opens the OTF with `TTFont(font_path)` (lazy reader), reads the raw GSUB bytes through `font.reader`, and prints three things to stdout:

1. `GSUB budget: {bytes}, {lookups}, {subtables}` — raw table length, LookupList count, total subtable count.
2. `GSUB offset headroom: LookupList {65,535 − max lookup offset} bytes, subtable {65,535 − max subtable offset} bytes in lookup N` — distance to the 16-bit offset overflow walls. **This is the kill-criterion number for the prototype**: both offsets are uint16, so a settlement encoding that pushes either past 65,535 fails to compile or needs extension-lookup workarounds.
3. `Largest calt lookups by subtable count: name=N, ...` (top 5). The names come from `_extract_feature_lookup_names(fea_code, "calt")` (build_font.py:130), a purely textual scan of the FEA source for `lookup NAME {` lines inside `feature calt { ... } calt;` — it relies on the FEA’s calt lookups appearing in the same order as the compiled FeatureRecord’s lookup indices.

`fea_code` may be `None`: the budget and headroom still print; calt lookup names fall back to `lookup[index]`. It returns `None` (print-only) and closes the font in a `finally`. There are no other side effects.

Standalone invocation (verified):

```python
import sys
sys.path.insert(0, "tools")  # run from the repo root
from pathlib import Path
from build_font import _report_gsub_budget
_report_gsub_budget(Path("prototype/out/Proto.otf"), fea_string_or_None)
```

The `tools/` modules import each other flatly (`from glyph_compiler import ...`), so `tools/` must be on `sys.path`; the repo’s own conftest.py:18-20 does exactly this.

## 3. Structure of the current generated FEA

Top-level block order in `site/AbbotsMortonSpaceportSansSenior-Regular.fea` (line numbers from the current artifact):

| Lines       | Block                               | Notes                                                                          |
| ----------- | ----------------------------------- | ------------------------------------------------------------------------------ |
| 1-13        | `lookup kern_he-before-noentry_val` | standalone GPOS value lookup referenced by the contextual kern below           |
| 15-25       | `feature kern`                      | flat pair kerns from `kerning:` YAML                                           |
| 27-37       | `feature ccmp`                      | dotted-base → dotless before top marks                                         |
| 39-246      | `feature mark`                      | markClass + base anchors                                                       |
| 248-1207    | `feature curs`                      | the four per-height cursive lookups                                            |
| 1209-1580   | `feature ss02 ss03 ss04 ss05 ss07`  | capability gates: contextual `sub ... qsTea' ... by qsTea.<stance>` unlocks    |
| 1582-29372  | `feature calt`                      | 616 lookups; class declarations at the top                                     |
| 29374-30022 | `feature ss06`, `feature ss10`      | post-calt reverts (variant → base)                                             |
| 30024-30829 | `feature kern`                      | senior contextual kerns (feaLib merges with the first kern block)              |
| 30833-30838 | `feature calt`                      | the namer-dot mini-calt, merged after the join calt so it sees settled stances |

### The four `curs` lookups

`cursive_y0` (line 249), `cursive_y5` (706), `cursive_y6` (1153), `cursive_y8` (1174) — one per attachment height, named `cursive_y{pixelY}`. Anchor format, from `_emit_quikscript_curs` (quikscript_fea.py:6514-6565):

```fea
feature curs {
    lookup cursive_y0 {
        pos cursive qsAh <anchor 0 0> <anchor NULL>;
        pos cursive qsBay <anchor NULL> <anchor 250 0>;
        pos cursive qsDay.half.en-y0.ex-y0 <anchor 0 0> <anchor 200 0>;
        ...
    } cursive_y0;
    ...
} curs;
```

- One `pos cursive <glyph> <entryAnchor> <exitAnchor>` statement per glyph per height it touches; the side that does not attach at that height is `<anchor NULL>`. A glyph with entry at y=5 and exit at y=0 appears in **both** `cursive_y5` (entry real, exit NULL) and `cursive_y0` (entry NULL, exit real) — this is the “NULLed anchors for cross-height glyphs” encoding §7 wants kept verbatim. Height mismatch is therefore impossible at GPOS level: a y=0 exit can only connect to a glyph carrying a real entry in `cursive_y0`.
- Anchor coordinates are glyph-space pixels × 50: y heights 0/5/6/8 emit anchor Y 0/250/300/400.
- Entryless `.noentry` twins with no exit are still registered in their original entry-Y lookup with `<anchor NULL> <anchor NULL>` (quikscript_fea.py:6542-6556) so they participate in the lookup’s coverage.
- **There is no `lookupflag` anywhere in the generated FEA** (zero matches in 30,838 lines) — curs and calt both run with default flags. ZWNJ skipping is handled by coverage, not flags (§4).

### Glyph classes

Declared at the top of the `feature calt` block (lines 1583-1595), before any lookup: `@exit_y0`, `@entry_y0`, `@entry_only_y0`, `@entry_y5`, `@entry_only_y5`, `@entry_y6`, `@entry_only_y6`, `@bridge_y0_y5`, then `@qs_has_entry` / `@qs_noentry` (the ZWNJ chokepoint classes, line 1594-1595) and, further down, `@qs_letters` for the word-final substitute-then-revert pairs. The `@entry_only_*` variants exclude glyphs that also have an exit at the same Y. Everything else in calt rules is spelled as inline `[...]` lists, regenerated per rule.

### calt lookup ordering

All 616 lookups live in one `feature calt {}` block and run in declaration order, each lookup completing its full buffer pass before the next starts. The observed phase order (lookup-name prefixes, in file order):

1. `calt_zwnj` — first lookup in calt (line 1597).
2. Per-family waves of `calt_fwd_pair_*` / `calt_pair_*` / `calt_fwd_*` / `calt_upgrade_*` / `calt_fwd_early_*` — backward (after-context) and forward (before-context) stance selection, ordered by a dependency-aware family schedule.
3. `calt_cycle` (line 3937) — cyclic-join resolution.
4. `calt_fwd_override_*`, `calt_post_upgrade_bk_*`, `calt_post_fwd_pair_*`, `calt_post_override_*`, `calt_post_pair_bk_*`, `calt_post_context_pair_*` (104 lookups — the biggest category), `calt_post_context_revert`.
5. `calt_reverse_upgrade_*`, `calt_reverse_upgrade_explicit_*`, `calt_ext_bk_*`.
6. `calt_liga` (line 10501) — the **single GSUB type-4 ligature lookup**, e.g. `sub qsDay qsUtter by qsDay_qsUtter;`, with one rule per (lead stance, trailing stance) pair. Note it sits **late**, after stance settlement — the redesign moves formation to the front of calt.
7. `calt_post_liga*` cleanups, `calt_pair_guard_reflip_*`, `calt_post_reflip_*`, `calt_final_fwd_pair_*`.
8. Demote/repair waves: `calt_trailing_demote_*`, `calt_pred_demote_*`, `calt_noentry_exit_contract_*`, `calt_successor_demote_*`, `calt_final_pred_demote_*`, `calt_when_entered_*`, `calt_final_trailing_demote_*`, `calt_final2_pred_demote_*` — the guard/demote/repair categories the redesign deletes outright.

Word-final handling uses the substitute-then-revert pair `calt_word_final_<v>` (`sub base by variant;`) followed by `calt_word_final_revert_<v>` (`sub variant' @qs_letters by base;`) (quikscript_fea.py:2521-2548).

## 4. ZWNJ (uni200C) handling — the proven encoding shape

`uni200C` is a real, encoded, zero-advance, empty-bitmap glyph (glyph_data/punctuation.yaml:52-54), so it survives into the glyph set and cmap and can be named in rules.

### The chokepoint substitution

Generated FEA lines 1594-1599 (emitted at quikscript_fea.py:2503-2519). Exact artifact text (classes elided to shape):

```fea
    @qs_has_entry = [qsAh qsAt qsAwe qsBay ... qsYe qsZoo];          # 47 bare family/ligature glyphs
    @qs_noentry = [qsAh.noentry qsAt.noentry ... qsZoo.noentry];     # their 47 locked twins

    lookup calt_zwnj {
        sub uni200C @qs_has_entry' by @qs_noentry;
    } calt_zwnj;
```

This is the design doc’s `sub uni200C @entry-live' by @entry-locked`. It is the **first lookup in calt**, so every later lookup sees the locked twin, not the live base. The `.noentry` twins are IR-generated stances (`is_noentry` / `noentry_for` on `JoinGlyph`); the emitter pairs each `base` with `base.noentry` only when both names exist in the compiled set (quikscript_fea.py:2504-2511). In curs, locked twins carry `<anchor NULL>` on the entry side (and `<anchor NULL> <anchor NULL>` registrations when they have no exit), so GPOS cannot attach across the boundary even if a stale rule fires.

### The default-ignorable coverage transform

HarfBuzz skips default-ignorable glyphs (ZWNJ included) when matching a lookup’s context **unless the lookup’s own coverage references the glyph**. Three post-passes rewrite the finished calt text at the end of `_emit_quikscript_calt` (quikscript_fea.py:6502-6505):

```python
lines = _strip_post_zwnj_from_ignore_contexts(lines, base_to_variants)
lines = _ensure_zwnj_coverage_for_calt_lookups(lines)
lines = _add_zwnj_guards_for_two_position_forward_rules(lines)
lines = _coalesce_consecutive_ignore_rules(lines)
```

- `_ensure_zwnj_coverage_for_calt_lookups` (quikscript_fea.py:2091): for every calt lookup containing a chained rule that doesn’t already mention `uni200C`, (a) replay run-initial rules whose input is a `.noentry` twin with `uni200C` as explicit backtrack, and run-final rules with `uni200C` as explicit lookahead (`_zwnj_boundary_replay_lines_for_calt_lookup`, line 2056) — these preserve correct shaping immediately at the boundary and are emitted **ahead of** the original rules; then (b) prepend `ignore sub uni200C TARGET';` for every marked input with backtrack context and `ignore sub TARGET' uni200C;` for inputs with lookahead context. This matches §7’s “boundary-outcome rows with uni200C explicit in the class at the boundary slot, ordered ahead of any join row”. `calt_zwnj` is the only exempt lookup (`_ZWNJ_FIREWALL_EXEMPT_LOOKUPS`, line 1881) because it must match across ZWNJ by design.
- `_add_zwnj_guards_for_two_position_forward_rules` (quikscript_fea.py:2166): two-position forward chains `sub TARGET' MID [LIST] by REPL;` can still match across `TARGET MID uni200C X` because the single-position guard doesn’t cover the second lookahead slot; this pass injects `ignore sub TARGET' MID uni200C;` immediately before each such rule. **For the prototype this is the critical precedent: every slot of the settlement rule shape (backtrack, first, and second lookahead) needs ZWNJ coverage, exactly as §7 says.**
- `_strip_post_zwnj_from_ignore_contexts` (quikscript_fea.py:1934): removes `.noentry` twins from `ignore sub` **lookahead** positions (they can only occur right after a ZWNJ, so leaving them lets an ignore rule match across the boundary); backtrack positions keep them because they also describe right-side-internal guards.

Representative generated artifact lines (lookup `calt_fwd_pair_qsAh_ex-con-2`, lines 1601-1607):

```fea
    lookup calt_fwd_pair_qsAh_ex-con-2 {
        sub uni200C qsAh.noentry' [qsJai ... qsJai_qsUtter.ex-ext-1.ex-con-2] by qsAh.noentry.ex-con-2;
        ignore sub qsAh' uni200C;
        ignore sub qsAh.noentry' uni200C;
        sub qsAh' [qsJai ...] by qsAh.ex-con-2;
        sub qsAh.noentry' [qsJai ...] by qsAh.noentry.ex-con-2;
    } calt_fwd_pair_qsAh_ex-con-2;
```

The §7 emitter invariant (“locked twins and chokepoint outputs appear in no raw lookahead class”) corresponds to `_strip_post_zwnj_from_ignore_contexts` plus the fact that forward selectors are built from raw variant expansion that excludes post-chokepoint outputs except where deliberately replayed.

One GPOS interaction worth copying: the ZWNJ-aware kern uses a contextual `pos [LEFT]' lookup kern_X_val uni200C;` driving a standalone value lookup (`generate_kern_fea`, build_font.py:345-353) — ZWNJ named in the rule gives the kern lookup coverage over it.

## 5. Compiling a small prototype font by reusing the machinery

### Verified recipe

`build_font` itself is the cleanest reuse point. The following was run successfully against the repo’s environment (`uv run`, `sys.path` including `tools/`):

```python
from build_font import build_font, _report_gsub_budget

glyph_data = {
    "metadata": {
        "font_name": "Proto", "version": 1.0,
        "units_per_em": 550, "pixel_size": 50,
        "ascender": 550, "descender": -150, "cap_height": 400, "x_height": 300,
    },
    "glyphs": {
        "space": {"advance_width": 7},
        "uni200C": {"advance_width": 0, "bitmap": []},
        "qsTea.prop": {"bitmap": ["#####", "    #", "    #", "    #", "    #", "    #"]},
        "qsTea.settled.prop": {"bitmap": [...]},  # one record per cell glyph
    },
    "glyph_families": {}, "context_sets": {}, "kerning": {}, "senior_kerning": [],
    "restore_isolated_form_overrides": [], "predecessor_demote_overrides": [], "trailing_demote_overrides": [],
}

font = build_font(glyph_data, out_path, variant="senior", senior_fea=hand_built_fea_string)
_report_gsub_budget(out_path, hand_built_fea_string)
```

Output: a working 5-glyph OTF, the `.fea` sidecar written next to it, and the GSUB budget report — `96 bytes, 2 lookups, 2 subtables`, headroom ≈65.5k each.

### Why this works

- With `glyph_families` empty, `compile_quikscript_ir` returns no join glyphs, the senior-only validations (`glyph_compiler.py:292-296`) no-op, and because `senior_fea` is non-None the 3-second IR emitter never runs (build_font.py:1163-1166). The hand-built FEA string is compiled as-is by `addOpenTypeFeaturesFromString`.
- All prototype glyphs go in the legacy `glyphs:` dict with a `.prop` suffix; `prepare_proportional_glyphs` (glyph_compiler.py:46) strips the suffix, so `"qsTea.settled.prop"` compiles as glyph `qsTea.settled`. Names without `.prop` also work for non-letter glyphs (`space`, `uni200C`).
- cmap: `_resolve_codepoint` (build_font.py:228) maps `qsX` names through `postscript_glyph_names.yaml` and `uniXXXX` names by hex; dotted cell-variant names get no cmap entry (correct — they are shaping-only) and sort by their base’s code point in the glyph order.

### Gotchas

- **`space` is mandatory** with an `advance_width`: build_font.py:941-942 does `glyphs_def.get("space", {})["advance_width"]` and raises KeyError without it. All eight `metadata` keys shown above are likewise mandatory (`metadata["font_name"]` etc. are direct subscripts).
- **Bitmap shape validation** (build_font.py:970-1022): proportional bitmaps must have uniform row widths (pad rows with trailing spaces); `qs*` glyphs must have 6 or 9 rows (12 only for the angle parens), and `y_offset: -3` (deep letters) requires 9 or 12 rows.
- **Advance widths**: default for a prototype glyph is `(max_row_width + 2) * 50`, then Senior shaves one pixel off the right sidebearing of every `qs*` glyph (`senior_tighten`, build_font.py:1032-1048). Pass an explicit `advance_width` (in pixels, not units) to opt out. Ink is centered: `x_offset = (advance_width - bitmap_width) // 2` — entry/exit anchor X values in the hand-built curs must account for the same centering offset if you want pixel-exact seams (the real pipeline computes anchors against the same coordinate frame as the drawn ink; with the standard `+2` advance the centering offset is exactly one pixel, 50 units).
- **Anchors do not come from glyph records on this path.** Legacy `glyphs:` records carry no `anchors:`; cursive attachment must be written into the hand-built FEA as `feature curs { lookup cursive_yN { pos cursive ... } }` statements (see §3 for the exact shape). Coordinates: `x_pixels * 50, y_pixels * 50` measured in the glyph’s drawn coordinate frame (after the centering offset), baseline at y=0.
- **Don’t use `variant="mono"`** — it imports Departure Mono from `reference/` and asserts no authored FEA. `variant="senior"` is the right choice; it is the only variant that accepts `senior_fea`.
- Run via `uv run` from the repo root with `sys.path.insert(0, "tools")` (or `sys.path.append(str(repo_root / "tools"))`) — the tools modules use flat sibling imports.
- Lower-level pieces are importable individually if needed: `parse_bitmap` (build_font.py:531), `bitmap_to_rectangles` (build_font.py:540), `_resolve_codepoint` (build_font.py:228). But the verified `build_font(..., senior_fea=...)` path already handles charstrings, hmtx, cmap, name, OS/2, post, gasp, head, and the cmap-14 variation sequences, so there is little reason to hand-roll FontBuilder calls.
- **FEA compile failures are loud**: feaLib raises on undefined glyph names in any rule or class, so every glyph named in the hand-built FEA must exist in `glyphs:`.

### Cross-shaper smoke tests

`uharfbuzz` is already a dev dependency (pyproject.toml) and `test/test_shaping.py:365-368` shows the load pattern: `hb.Blob.from_file_path` → `hb.Face` → `hb.Font`, then `hb.Buffer`/`hb.shape` with optional feature dicts. CoreText can be exercised on this Mac (e.g. via a small Swift/`CTLine` harness or rendering in a WebKit view); DirectWrite has no local runner — plan for a Windows VM or CI leg, or rely on HarfBuzz+CoreText locally for week one.
