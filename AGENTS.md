# Instructions for agents

## General

- Always ask clarifying questions when there are multiple valid ways to do something.
- After any glyph or code changes, run `make test` to make sure nothing is broken.
- Never commit without explicit user approval. Show the changes and wait for the go-ahead before committing.
- In this repository only, it’s OK to have multiline commit messages, although by no means mandatory.
- “Orthodox” is Quikscript-speak for “English written in the Latin script”.

## HTML/CSS/JS

- Prefer nested CSS over flat CSS.
- Prefer for-of loops to `.forEach()`.
- Use modern range syntax for media/container queries: `(width > 40em)` not `(max-width: 40em)`.

## Python

IMPORTANT: Always use `uv run` instead of `python` or `python3` directly. For example:

- `uv run pytest` not `pytest`

## Adding glyphs

- Whenever a glyph is added to any YAML file under `glyph_data/`, ensure it also has an entry in @postscript_glyph_names.yaml if it uses a standard PostScript name (not a `uniXXXX` name).

## Generating glyphs for the first time in `glyph_data/quikscript.yaml`

- Keep all glyphs alphabetized by code point (`uniXXXX`).
- When looking at @reference/manual-page-2.pdf, ignore the hyphens in the names when looking up the names (like `T-ea` for ·Tea).
- Tall letters have a height of 9 pixels (9 entries in `bitmap`).
- Short letters have a height of 6 pixels (6 entries in `bitmap`).
- Deep letters have a height of 9 pixels (9 entries in `bitmap`) and a `y_offset` of -3.
- For bitmap data in `glyph_data/quikscript.yaml`, use double-quoted row strings. Add bare trailing `#` comment markers on the rows whose glyph-space `y` values are 5 and 0; which bitmap rows those are depends on `y_offset`.

## Inspiration

- This is a font that’s supposed to match Departure Mono’s metrics.
- When in doubt, look at @test/DepartureMono-Regular.otf to check metrics.
- When referring to Quikscript letters, they are frequently prefixed by a `·`, like in `·Why`.
- Use @reference/csur/index.html to find out what Quikscript letters go with what code points.
- To understand a Quikscript letter shape, zoom/crop @reference/manual-page-2.pdf (the colons between the letters show the vertical dimension of a Short letter) and, if needed, compare against @reference/csur/kingsley.ttf, then translate the stroke path into the 5×6 or 5×9 pixel grid.

## Cursive attachment (`curs`)

- Quikscript source data now lives under `glyph_families` in `glyph_data/quikscript.yaml`, with separate `mono`, `prop`, `shapes`, and `forms` records. Put `cursive_entry` / `cursive_exit` anchors in the proportional record or form that should compile into the proportional font; mono-only records do not carry `curs` anchors.
- Shared Quikscript bitmaps belong under a family's `shapes`, and contextual/alternate forms belong under `forms` with `anchors`, `select`, `derive`, `traits`, and `modifiers`. Preserve `traits: [alt]` and `traits: [half]` when those concepts are real; other suffixes belong under ordered `modifiers`.
- Quikscript form keys are local labels, not the compiled glyph naming API. The build now seeds compiled glyph identity and compatibility metadata from each form's explicit `traits` and `modifiers`, while `select` / `derive` family references use structured selectors like `{family: qsUtter, traits: [alt], modifiers: [reaches-way-back]}`.
- Repeated `select` / `derive` reference lists belong under top-level `context_sets` and are referenced inline as `{context_set: some_name}`. `context_sets` may include other `context_set` references when composing a larger list from smaller ones keeps the source clearer.
- When multiple Quikscript forms share the same selector or anchor scaffolding, prefer `inherits` over copying the whole form, and clear inherited nested keys with `null` when a child form needs to drop them.
- When rewriting `glyph_data/quikscript.yaml`, keep anchor coordinate pairs inline as `[x, y]`. Keep short `traits`, `modifiers`, and `select` / `derive` reference lists inline too, and only fall back to block lists when entries are genuinely long enough that inline formatting hurts readability.
- The standard x-value for `cursive_exit` is one pixel to the right of the stroke. Usually this places the anchor just past the bitmap's right edge, but sometimes the anchor falls inside the bitmap — as with ·He, ·Ye, and `qsThey.exit-xheight` — because the stroke exits from the left or middle of the glyph.
- When a narrow `after:` selector competes with a broad fallback like a `context_set`, make sure the narrow selector wins first. `qsThaw.after-ing` must beat `qsThaw.after-tall`.
- If adding an `after:` form moves a family out of `fwd_only`, only pull the family's plain forward-exit Ys early when later backward lookups truly depend on those Ys; do not pull same-Y forward pair overrides early just to preserve that dependency.
- For `·-ing` before `·Thaw`, extend `qsIng`'s exit rather than shifting `qsThaw`'s entry left.
- Group lookups by Y value to prevent cross-pair attachment between glyphs at different heights.
- During `calt`, a ZWNJ boundary is still the literal `uni200C` glyph; if a selector needs to block or require a ZWNJ boundary, target `uni200C` in `after` / `not_after` rather than `space`.

## Bumping the version number

1. Update `version` in `glyph_data/metadata.yaml` (e.g., `3.000`)
2. Update `version` in `pyproject.toml` (e.g., `3.0.0`)
3. Run `uv sync` to update `uv.lock`

## Transcribing passages from the manual

- The two word-list sections in `test/the-manual.html` — "Common words to be fully spelt" and "Contractions" — are the source of truth for how to spell things in Senior QS. Parse the `<dt>` for the English word and the `<dd>` (or its child `<span>` elements) for the QS text. Multi-form entries (e.g., `time/s`) use `data-orthodox` attributes on each `<span>` to label which English word each QS form represents.
- The `data-orthodox` attribute provides the English text for each passage.

## Tests

- See @test/data-expect.md for the `data-expect` attribute syntax (glyph tokens, connection operators, variant assertions, ligature notation, and duplicate rules).
- See @test/span-wrapping.md for how to wrap QS words in `data-expect` spans in passage blockquotes.
- Three levels of duplicate exist for elements with a `data-expect` attribute:
  - **Content duplicate:** two elements whose text content is byte-identical (same code points in the same order).
  - **Total duplicate:** a content duplicate where the two `data-expect` values also express the same sequence of assertions (identical after collapsing whitespace).
  - **Exact duplicate:** a total duplicate where the raw `data-expect` strings are character-for-character identical (whitespace and all).
- To remove a duplicate test: remove the `data-expect` attribute. If the element is a `span` with no remaining attributes, unwrap the `span` (remove the tags but keep the text content in place). Never remove the text inside the element — it must remain identical before and after. The text frequently contains invisible PUA code points, so verify with a program (e.g., compare hex dumps of each modified line before and after) that only the attribute and/or tags were removed.
- When adding `data-expect` attributes, always check for content duplicates first — do not wrap a word that is already tested elsewhere in the document unless explicitly told to.
- Do not wrap one-letter Quikscript words in `data-expect` attributes unless explicitly told to — there is no point in testing joins when there is only one letter.

## Markdown-document style

- Use sentence case for titles, not title case.
- Do not hard-wrap lines. Let each paragraph or list item be a single long line.
- Ensure that Markdown tables are nicely formatted for humans and have nicely lined-up columns.
- Make sure `markdownlint-cli2` doesn’t have anything to complain about.
