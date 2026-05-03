# Things to learn to manage Abbots Morton Spaceport well

A reading list, sorted from most general / easy to most specific / thorny. Each item is a concept I want to be able to reason about when planning changes — not a recipe. Delete an entry once I'm confident I can answer "how should we do this?" questions about it without reaching for a reference.

(This means that one can track progress by viewing this file’s Git history.)

Items lower in the list usually depend on items higher in the list. Within a section, ordering is roughly easiest-first.

## 2. Typography fundamentals

- **Em square and font units.** The internal grid a font is drawn on. Here UPM is 550, pixel size is 50, so 11 px = 1 em.
- **Kerning.** Pair-level horizontal adjustment. We use it sparingly in Latin (`opentype-features.yaml`) and structurally for `qsHe` before noentry letters.
- **Bold by overstrike.** Bold is the Regular with each "on" pixel rendered 1.5 px wide. No separate bold drawings exist.

## 4. Font formats and the OpenType table model

- **Outline flavors.** Both TrueType (`glyf`, quadratic Béziers) and CFF (`CFF`/`CFF2`, cubic Béziers via PostScript Type 2 CharStrings) ride inside the same SFNT container, and OpenType layout tables (`GSUB`, `GPOS`, `GDEF`) work the same on either. We ship CFF outlines built from bitmap pixels via `T2CharStringPen`; the `.otf` extension is a consequence, not a meaningful axis.
- **The SFNT table set.** `name`, `cmap`, `GSUB`, `GPOS`, `GDEF`, `OS/2`, `hhea`, `hmtx`, `post`, etc. Worth recognizing each by name when something goes wrong.
- **GSUB vs GPOS.** Substitution (one-to-one, many-to-one, contextual) vs positioning (kerning, anchor attachment, cursive attachment).
- **Anchors.** Named points on glyphs (`entry`, `exit`, mark anchors). Drive cursive attachment and mark positioning.
- **GDEF.** Glyph categories (base, ligature, mark, component), mark attachment classes, ligature caret positions, and mark glyph sets. feaLib auto-builds it from our FEA — ligature-substitution targets (`qsX_qsY`) get classified as ligatures, `markClass` declarations land combiners in the mark class — so we don't author GDEF directly and `tools/quikscript_fea.py` has no `table GDEF` block. The `mark` feature reads the mark class to attach combiners to bases, and shaping engines use the classification when interpreting cursive chains across marks. HarfBuzz infers around gaps; Word/Uniscribe doesn't, so a font that joins fine in browsers but misbehaves there puts GDEF on the suspect list. Dump with `ttx -t GDEF test/<font>.otf`. `LigCaretList` (caret positions inside ligatures for text-selection UIs) is the one piece feaLib won't infer — if selecting inside a `qsX_qsY` ever feels wrong, that's the gap.

## 5. OpenType layout features used here

- **Lookup ordering.** Why `calt` running before `liga` matters (a `calt` rule can substitute glyph identity to suppress a `liga` rule from firing) — see TODO.md's note about ·Day·Utter·Low.
- **FEA syntax.** Feature files: classes, lookup blocks, contextual rules. We generate them with `tools/quikscript_fea.py` and load them via `feaLib.builder.addOpenTypeFeaturesFromString`.
- **Anchor classes in FEA.** `@exit_y{N}` / `@entry_y{N}` collect every variant carrying that anchor. Source-side selectors `{exit_y: N}` / `{entry_y: N}` mirror these classes.
- **Cursive attachment (`curs`).** A GPOS feature that snaps each glyph's entry anchor to the previous glyph's exit anchor. Designed for Arabic / Mongolian; less universally implemented for LTR scripts. This is why Senior doesn't render in Word.
- **Stylistic sets vs variation selectors.** Stylistic sets opt into rules globally (`ss02`–`ss07`, `ss10`); variation selectors target one glyph at a time. Both are documented in README.md.

## 6. Shaping engines and rendering

- **HarfBuzz.** The de-facto OpenType shaping engine; what current browsers, Typst, and our tests use. If something works in HarfBuzz it will probably work everywhere modern.
- **uharfbuzz.** The Python binding the test suite uses to shape buffers and verify output. Custom typings live in `typings/uharfbuzz/`.
- **What "shape" means.** Run a sequence of code points through the engine and emit a sequence of glyph IDs with positions. `data-expect` describes the expected output of this.
- **Why feature support varies.** `curs` works in WebKit, Gecko, Blink, Typst, anything HarfBuzz-backed. Microsoft Word 365 doesn't support it (yet).

## 7. The build pipeline at a glance

- **Top-level flow.** `make all` → `uv run python tools/build_font.py glyph_data/ test/` → six OTFs in `test/`. `make test` runs pyright then pytest. `make explainer` builds the Typst explainer.
- **`tools/build_font.py`.** Loads YAML, calls the compiler, walks variants into FontBuilder, emits FEA via `quikscript_fea.py`, writes Mono / Junior / Senior × Regular / Bold.
- **`tools/glyph_compiler.py`.** Owns `CompiledGlyphSet` and `JoinGlyph`. Carries legacy flat `glyphs:` data, compiled Quikscript joins, and merged metadata. `JoinGlyph` is the canonical variant-level result.
- **`tools/quikscript_ir.py`.** The intermediate representation: family schema → resolved variants with selectors, anchors, ligature inheritance, derive rules.
- **`tools/quikscript_fea.py`.** Senior feature emission: `curs`, `calt_cycle`, `calt_liga`, `calt_post_liga_*`, gated stylistic sets.
- **`tools/quikscript_join_analysis.py`.** Static validator (`validate_join_consistency`, `collect_join_warnings`). The thing that locks down join correctness.
- **`tools/inspect_join.py` / `extract_glyph.py` / `shape_sequences.py`.** Diagnostics for one-off questions about a join, a glyph, or a shaping run.

## 8. Source-data schema

- **`glyph_data/` layout.** One YAML per script / category: `quikscript.yaml`, `latin_letters.yaml`, `numbers.yaml`, `punctuation.yaml`, `composites.yaml`, `exotics.yaml`, `shavian.yaml`, plus `metadata.yaml` and `opentype-features.yaml`.
- **`glyph_families:` structure.** Each family has up to four sibling records: `mono`, `prop`, `shapes`, `forms`. Anchors live on the proportional side only.
- **`shapes`.** Bitmaps shared by multiple forms in the same family.
- **`forms`.** Contextual / alternate variants. Carry `traits`, `modifiers`, `select`, `derive`, `anchors`, `inherits`.
- **`traits` vs `modifiers`.** `traits` are stable semantic concepts (`alt`, `half`); `modifiers` are everything else, ordered, and feed the compiled glyph name. Test assertions distinguish stable trait checks from compatibility-only modifier checks.
- **`select` and `derive`.** `select.after` / `before` / `not_after` / `not_before` choose when a form fires; `derive.extend_*` / `contract_*` widen or narrow the connecting stroke.
- **Family selectors.** `{family: qsX, traits: [...], modifiers: [...]}` picks specific variants. Anchor selectors `{exit_y: N}` / `{entry_y: N}` (with optional `except:`) expand to every variant carrying that anchor.
- **`context_sets`.** Top-level reusable selector lists, referenced as `{context_set: name}`. May nest. Keep entries in code-point order.
- **`inherits`.** A form references another form's scaffolding. Use `null` to clear inherited keys.
- **Bitmap conventions.** Double-quoted rows in `quikscript.yaml`; trailing bare `#` comment markers on the rows whose glyph-space y is 5 and 0; "ink" = `#`; "leftmost-ink column" / "no ink at y=N" / `exit_ink_y` are the cursive-attachment vocabulary.
- **Standard exit anchor x.** One pixel past the right edge of the stroke — usually outside the bitmap, occasionally inside (·He, ·Ye, `qsThey.exit-xheight`).

## 9. Cursive attachment (`curs`) in this codebase

- **Anchor coordinates.** `[x, y]` pairs in glyph space. y values most commonly 0 (baseline), 5 (x-height), 6, 8 (top).
- **Anchor classes by Y.** `{exit_y: N}` / `{entry_y: N}` give "every variant whose anchor is at y=N". Don't blindly substitute these for hand-curated lists — they re-expand and can change generated FEA. Prove equivalence first.
- **`extend_entry_after` / `extend_exit_before`.** Lengthen the connecting stroke by N pixels, shifting anchors and bitmaps in lockstep.
- **`contract_entry_after` / `contract_exit_before`.** The opposite. Narrow contract rules beat broad extend rules because narrower selectors get ordered first.
- **Conflicting `by` values.** A single `derive` block holds at most one each of the four directives. If a new target needs a different `by`, put the rule on the other side of the join.
- **`noentry_after`.** A successor opts out of a left-side join after specific predecessors. Predecessors get reverted to bare-prop in `calt_post_liga_left_cleanup`.
- **`entry: null` on a ligature.** Ligature opts out of all left-side joins (`qsAt_qsMay`). Triggers `entry_explicitly_none`, blocks lead-inheritance, and tells `calt_post_liga_left_cleanup` to revert any predecessor.
- **Narrow vs broad selector competition.** `qsThaw.after-ing` must beat `qsThaw.after-tall`. The build orders narrower selectors first, so a single-family contract rule beats a context-set extend rule.
- **Mixing `before:` and `not_before:` on one form.** A form can demand a positive trigger for one family and a broad fallback for everything-else-with-matching-anchor. Same family in both lists is an error.
- **Per-Y lookup grouping.** Lookups are grouped by Y to prevent cross-pair attachment between glyphs at different heights.

## 10. Ligatures and the calt cycle

- **`calt_cycle`, `calt_liga`, `calt_post_liga_*`.** The four-stage shape-selection / ligation / cleanup pass that Senior runs. Each stage has a specific job; mixing rules across stages is how regressions sneak in.
- **Why ligatures live in `calt`, not `liga`.** Contextual alternates must run first so they can change identity (and thus block) ligature lookups. Document this in the source when it matters.
- **Two-glyph ligature entry inheritance.** `qsX_qsY` inherits the entry anchor and `extend_entry_after` rules from `qsX`'s prop (or from `qsX.entry-xheight` if the prop has none). Don't restate them on the ligature — `LigatureEntryInheritanceWarning` will yell at you if you do.
- **Two-glyph ligature exit inheritance.** Mirror image: `extend_exit_before` / `contract_exit_before` from the trailing component propagate via `_iter_related_extension_targets`, and `calt_liga` routes `(qsX, qsY.<exit-modifier>)` to `qsX_qsY.<exit-modifier>`. Don't restate trailing exit rules on the ligature.
- **`expand_selectors_for_ligatures`.** A successor's `select.after: [{family: qsY}]` implicitly matches every ligature whose trailing component is `qsY`. Don't hand-list ligature names.
- **`calt_post_liga_left_cleanup`.** Reverts predecessors when a ligature with `entry: null` consumes the right-hand glyph, undoing both cursive attachment and visual abutment.
- **Dropping false joins after consumption.** When a ligature consumes a glyph, the consumed component must not keep choosing variants on the following glyph. Normalize the follower back to what the ligature itself supports.

## 11. Stylistic sets and variation selectors in this font

- **`ss02`–`ss07`, `ss10`.** What each turns on / off. Listed in README.md. They aren't stable across releases — read FONTLOG before relying on them.
- **VS1 (`alt`) and VS2 (`half`).** Per-letter overrides for the Manual transcription. Configured in `metadata.variation_sequences`.
- **Why both exist.** Stylistic sets are global toggles; variation selectors are localized escapes for the specific places the Manual disagrees with our defaults.

## 12. Test infrastructure

- **`test/the-manual.html`.** Full transcription of Read's Quikscript Manual. The corpus we measure ourselves against. Don't rewrite existing `data-expect` values — preserve the manual.
- **The two manual word lists.** "Common words to be fully spelt" and "Contractions" are the spelling source of truth.
- **The break-isolation invariant.** A `|` or non-joining `?` between two letter tokens additionally asserts each side shapes the same in isolation. `|?|` opts out for the rare cosmetic-only cross-break rule.
- **Duplicate levels.** Content / total / exact duplicates between two `data-expect` elements. The dedup workflow removes the attribute, then unwraps a bare `<span>` if nothing else is left.
- **Coverage checking.** `doc/checking-data-expect-coverage.md` — the documented Codex-driven workflow for finding manual-only words missing `data-expect` elsewhere.
- **`test/test_shaping.py`.** Parses `data-expect` and verifies HarfBuzz output against compiled glyph metadata.
- **`test/test_calt_regressions.py`, `test_quikscript_ir.py`, `test_quikscript_fea.py` (if present), `test_quikscript_context.py`, `test_quikscript_join_analysis.py`, `test_combining_marks.py`, `test_static_bold.py`, `test_shared.py`.** What each suite locks down.

## 13. Project-specific tooling I should be fluent in

- **Pyright.** Type checks `tools/` and `test/`. `make typecheck` runs it; `make test` runs it before pytest. New code must pass.
- **`pytest -n auto`.** Parallel pytest, configured via `pytest-xdist` in `pyproject.toml`.
- **Typst.** Used for `print.typ` (the printable manual snapshot) and `doc/explainer/`.

## 15. Standing open problems (read TODO.md before planning here)

- **Audit `{exit_y: N}` / `{entry_y: N}` substitutions.** Anchor selectors expand at compile time; mechanical replacement of long family lists has historically broken things. Prove equivalence with generated FEA or shaping tests.
- **Inconsistent exit anchor x offsets.** Most glyphs sit one pixel past the right edge; some sit two. Regularizing these is open work, especially with `extend.by` available.
- **Restructure source so join mismatches are inexpressible.** Phase A static validator is in; Phase B derived guards in progress. The eventual goal is a `joins:` section declaring bilateral edges. Don't design until validator complaints prove the current source language is inadequate.
- **Bare-form bitmap stubs from `noentry_after` predecessors.** Reverted bare forms still carry exit-side ink that overhangs into the entryless follower. Audit which families need `.exit-noentry` shape variants.
- **Mixed `before:` / `not_before:` follow-ups.** Possible IR diagnostic for forms whose resolved exclusion subsumes their inclusion (silent no-op). Possible `expand_selectors_for_ligatures` interaction work if a mixed-selector ligature ever misbehaves.
- **The Manual page-number markers.** Reserve a column on the right for PDF-page links and button targets.
- **Ligature backlog.** ·Bay·Utter, ·Gay·Utter ("waggon"), ·Gay·Out — plus the standalone ·I that drags along the baseline before going up sharply (for ·Way.half compatibility).
- **Why ligatures live in `calt` (not `liga`).** Document this in the source. The example to cite is ·Day·Utter·Low: the forward rule replaces ·Utter with ·Utter.alt before the ligature lookup sees it, blocking the ·Day·Utter ligature.
