# How to generate the explainer document

This file contains instructions for an LLM to generate a Typst document explaining how Abbots Morton Spaceport's cursive-attachment and OpenType machinery works to produce a font that ligates and joins Quikscript letters.

## Output files

Generate two files in this directory:

- **`style.typ`** — All styling, layout, page setup, fonts, colors, custom show rules, and reusable components (callout boxes, code blocks, figure templates, etc.). This file should be `#import`ed by the main document. It should not contain any prose.
- **`main.typ`** — The full document text. Imports `style.typ`. Contains `#include`d individual chapters. These chapters contain all headings, paragraphs, figures, and code examples. This file, and the individual chapters, can be deleted and regenerated without losing any styling work.

`main.typ` should start like this:

```typst
#import "style.typ": apply_explainer_style
#show: apply_explainer_style

// ⋮
```

Each chapter is a separate `.typ` file under `chapters/`. The `00-introduction.typ` file is a brief intro that doesn't count as a numbered chapter. Use real Typst heading levels (`=`, `==`, etc.) so section/subsection numbering is automatic. Do not hard-code section numbers in prose.

## Audience and tone

The reader is someone who:

- Knows what a font is and has used fonts before
- Has heard of OpenType but doesn't know how it works internally
- May or may not know what Quikscript is (explain it briefly)
- Has no prior knowledge of GPOS, GSUB, `curs`, `calt`, feature files, or font engineering
- Is comfortable with light technical content (pixel grids, coordinate systems) but needs concepts introduced one at a time

Write like a high-school textbook: clear, patient, building one concept on the last. Use analogies where helpful. Each section should assume the reader has read and understood everything before it, but nothing after it. Avoid jargon-first explanations — introduce the plain-English idea before naming the technical term.

## Document structure

The document should proceed in the following order. Each numbered item is a major section; lettered items are subsections. Not every subsection needs to be equally long.

Each major numbered section should start on a new page in the final rendered document.

Each major section heading should be formatted as two lines: first line `Chapter N` (automatic chapter number), second line the section title.

Chapter numbering must start at `Chapter 1` for the first level-1 heading in the assembled document.

### 1. The font and its pixel grid

Combine two topics into one chapter: what a font file contains, and how this particular font maps to a pixel grid.

Start with an aside (callout box) briefly explaining what Quikscript is: a phonetic alphabet for English designed by Kingsley Read (1966), evolved from Shavian, with Junior (unconnected) and Senior (connected/cursive) styles. Show the letter inventory at a glance. This is background context, not the main topic — keep it to a short aside.

Then cover:

a. **What's in a font file** — glyphs (shapes), a character map (Unicode → glyph), metrics (widths, baseline), and OpenType tables (instructions for substitution and positioning). Introduce GSUB and GPOS as the two families of smart-font tables, and tease that both are needed for Quikscript joining.
b. **Units per em and pixel size** — 550 UPM, each pixel is 50 font units, the em square is 11 pixels tall
c. **The three glyph heights** — Short (6px, baseline to x-height), Tall (9px, baseline to ascender), Deep (9px, 3px below baseline to x-height)
d. **Bitmap representation in YAML** — show a simple glyph's bitmap and explain the `#` markers that indicate the x-height zone
e. **y_offset** — how Deep letters use `y_offset: -3` to shift downward

Use a concrete example: show ·It (Short, 6px) vs. ·Tea (Tall, 9px) vs. ·Bay (Deep, 9px with y_offset: -3).

### 2. How letters join: the concept

Before getting into OpenType mechanics, explain *what* joining means visually:

a. **Entry and exit points** — every letter has a place where the pen arrives (entry) and a place where the pen leaves (exit). These happen at specific heights.
b. **Height matching** — a letter's exit height must match the next letter's entry height for a smooth join. Show examples of matching (exit at baseline → entry at baseline) and mismatching (exit at x-height → no entry at x-height = no join, use the base unconnected form).
c. **The basic idea of cursive attachment** — the text engine slides glyphs together so their exit and entry points overlap.

Use a simple two-letter example: ·Pea followed by ·Bay. Walk through how ·Pea's exit point at the baseline meets ·Bay's entry point at the baseline and the engine nudges them together.

### 3. Glyph variants: why one letter needs many shapes

Explain that a single Quikscript letter may need different shapes depending on context:

a. **Monospace vs. proportional** — the Quikscript source data defines separate `mono` and `prop` records inside each glyph family, and the Sans builds compile from the proportional side of that family
b. **Entry/exit variants** — a letter like ·Tea might need forms that enter at the top, at the x-height, at the baseline, or not at all. Each is a separate glyph in the font.
c. **Half-letter variants** — some Tall/Deep letters have a half-height form used when connecting to certain neighbors (e.g., ·Pea half). Explain with an example.
d. **Alternate forms** — ·Utter and ·No have alternate shapes designed to reduce pen lifts in specific contexts
e. **Stable vs. generated names** — explain that `.alt` and `.half` are real Quikscript concepts preserved in the source model, while other compiled names like `qsTea.entry-xheight.exit-baseline` are generated implementation detail from family `forms` and build-time variant expansion

### 4. Feature files and the OpenType pipeline

Introduce the `.fea` (feature file) syntax as the "programming language" for OpenType:

a. **What a feature is** — a named bundle of rules that a text engine can activate. Features have four-letter tags like `calt`, `curs`, `liga`.
b. **Lookups** — the individual rule sets within a feature. Introduce the concept without going deep yet.
c. **How the build script works at a high level** — YAML glyph data goes in, Python reads it, generates `.fea` code, and `fonttools` compiles it into binary OpenType tables inside the font file. The `.fea` file is also saved alongside the font for debugging.

### 5. GSUB: substituting the right glyph

Explain glyph substitution (GSUB) features used in this font:

a. **`calt` (contextual alternates)** — the engine looks at neighboring glyphs and swaps in the right variant. This is the heart of making joins work.
   - **Backward-looking rules** — "if the previous glyph exits at height Y, substitute the current glyph with a variant that enters at height Y." Walk through a concrete example.
   - **Forward-looking rules** — "if the next glyph enters at height Y, substitute the current glyph with a variant that exits at height Y." Walk through a concrete example.
   - **Explicit overrides** — the Quikscript family data stores context overrides under `select.before`, `select.after`, `select.not_before`, and `select.not_after`; explain how the build expands those into the specific substitution rules that override the height-based defaults.
   - **Word-final forms** — `calt_word_final: true` and how it triggers substitution at word boundaries (e.g., ·Out's final form).
   - **Rule ordering and topological sort** — briefly explain why the order of backward-looking rules matters (one substitution can create a new exit height that feeds the next rule) and how the build script uses a topological sort to get the order right.

b. **Ligature substitutions inside `calt`** — when two specific letters appear in sequence, the join machinery can replace them with a single pre-drawn combined glyph. In the source model those families declare an explicit `sequence`, while the compiled glyph names still use the underscore convention (`qsDay_qsUtter`). In the current build these substitutions are emitted from the Quikscript `calt` path (`calt_liga`), not from a separate standalone `liga` feature. Mention that ligatures can themselves have cursive anchors.

### 6. GPOS: positioning the joined glyphs

Explain glyph positioning (GPOS) features:

a. **`curs` (cursive attachment)** — the feature that actually slides glyphs together at their anchors.
   - **Anchor format** — each glyph declares an entry anchor, an exit anchor, or both, as `<anchor X Y>` in font units.
   - **How attachment works** — the text engine adjusts the position of each glyph in a cursive chain so that the exit anchor of glyph N overlaps with the entry anchor of glyph N+1.
   - **Y-grouped lookups** — explain the critical detail that glyphs are grouped into separate lookups by their Y values. This prevents a glyph entering at the baseline from accidentally attaching to a glyph exiting at the x-height. Walk through why this grouping is necessary with a concrete bad-case example.
   - **Multiple entry anchors** — some glyphs (like ·Roe) declare multiple entry anchors at different heights, meaning they can participate in cursive chains at either height.

b. **`kern` (kerning)** — brief mention that the font also kerns some Latin pairs (like `f` before short letters). This is separate from the Quikscript joining system.

### 7. Padding and spacing refinements

a. **`extend_entry_after`** — explain how certain glyph pairs need a little extra space when joined. In the source model this lives under a form's `derive` block, and the build script generates the shifted entry variants at compile time. Walk through an example (e.g., ·Ye followed by ·Roe).
b. **`.noentry` variants and ZWNJ** — explain that the Senior font auto-generates variants without entry anchors so that inserting a Zero Width Non-Joiner (U+200C) between two letters breaks the cursive chain. This is the "escape hatch" for when automatic joining is wrong.

### 8. The three font variants

Tie it all together by explaining the three output fonts:

a. **Mono** — fixed-width, no joining, no contextual features. Uses base glyphs only. Good for code editors and tabular display.
b. **Junior (Sans)** — proportional, no joining. Compiles each Quikscript family's `prop` form plus any non-contextual alternates. Has kerning and mark positioning but no `calt` or `curs`. Good for learning Quikscript (Junior Quikscript style).
c. **Senior (Sans)** — proportional with full joining. Compiles each Quikscript family's `prop` form plus contextual variants, half-letters, alternates, ligatures, padding, ZWNJ support, and cursive attachment. This is the font that produces Senior Quikscript.

### 9. Putting it all together: a worked example

Walk through a complete word being shaped, step by step:

1. The user types a sequence of Unicode code points
2. The character map resolves them to base glyphs
3. `calt` backward rules fire, substituting entry variants based on the preceding glyph's exit height
4. `calt` forward rules fire, substituting exit variants based on the following glyph's entry height
5. A later `calt` ligature lookup fires, combining adjacent glyphs into ligatures where applicable
6. `curs` fires, positioning the cursive chain by aligning anchors
7. The final positioned glyph stream is rendered

Pick a real word (maybe 4–6 letters) that exercises several features: a height transition, a half-letter, and/or a ligature. Show the glyph stream at each stage, ideally with small diagrams.

### 10. Appendix: the YAML schema

A reference section listing every key that can appear in a glyph definition in the YAML files, with a one-line explanation of each:

- `bitmap`
- `advance_width`
- `y_offset`
- `cursive_entry`
- `cursive_exit`
- `calt_before`
- `calt_after`
- `calt_not_before`
- `calt_not_after`
- `calt_word_final`
- `extend_entry_after`

## Style guidance for `style.typ`

- Use a clean, textbook-like layout. Generous margins, readable body font, monospace for code.
- Chapter headings should display `Chapter N` on one line and the chapter title on the next line.
- Add running page headers that automatically show the current section and subsection ("what section/subsection am I in?"), generated from the heading structure rather than hand-written text.
- Start each major chapter/section on a new page, preferably by an automatic heading-level rule in `style.typ` (not by manual `#pagebreak()` lines between sections).
- Use callout/aside boxes for:
  - **"Try it"** — hands-on exercises or things to look for in the font
  - **"Technical detail"** — deeper info that can be skipped on first read
  - **"Key idea"** — the one-sentence takeaway from a section
- Code blocks should be syntax-highlighted where possible (FEA code, YAML).
- Figures showing glyph bitmaps should ideally use a grid where filled pixels are colored squares and empty pixels are blank/lightly outlined. If Typst's drawing capabilities allow this, define a reusable function in `style.typ` for rendering a bitmap from a list of strings.
- Use consistent colors: one accent color for headings and callout borders, a second for code/technical elements.

## Notes for the generator

- Read `tools/build_font.py`, `tools/quikscript_fea.py`, and `tools/quikscript_ir.py` for the actual implementation. The live entry points are `plan_quikscript_joins()`, `emit_quikscript_calt()`, `emit_quikscript_curs()`, and the transform-expansion helpers in `tools/quikscript_ir.py` such as `generate_extended_entry_variants()`.
- Read `glyph_data/quikscript.yaml` for real glyph examples to use in the text.
- Read `glyph_data/metadata.yaml` for pixel-size and UPM values.
- Read `inspo/csur/index.html` for the code-point chart and character property descriptions.
- When showing FEA code, use real examples from the font's generated `.fea` output where possible, not made-up examples.
- The audience-appropriate depth level is: explain *what* the OpenType engine does at each step, not *how* the engine's internal state machine works. Think "user manual for how the font was built," not "OpenType spec tutorial."
- For the worked example in chapter 9, consider building the font first (`make`) and looking at the generated `.fea` files in `test/` for real rule sequences.
