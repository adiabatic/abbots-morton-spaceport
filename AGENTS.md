# Instructions for agents

## General

- Always ask clarifying questions when there are multiple valid ways to do something.
- After any glyph or code changes, run `make test` to make sure nothing is broken.
- Never commit without explicit user approval. Show the changes and wait for the go-ahead before committing.
- In this repository only, it’s OK to have multiline commit messages, although by no means mandatory.
- Prefer commit messages that either:
  - describe the change in author/reader experience — what the YAML/HTML/code now lets you skip writing, or what now looks right. For example: “Make tables.html store state in the URL, not localStorage”.
  - describe how the font’s letters will look different (“Reduce the half-·He extension at the x-height”, “Don’t join ·Way·Thaw ever”)
- Contrariwise, messages that describe the mechanism are anti-preferred.
- “Orthodox” is Quikscript-speak for “English written in the Latin script”.

## HTML/CSS/JS

- Prefer nested CSS over flat CSS.
- Prefer for-of loops to `.forEach()`.
- Use modern range syntax for media/container queries: `(width > 40em)` not `(max-width: 40em)`.

## Python

IMPORTANT: Always use `uv run` instead of `python` or `python3` directly. For example:

- `uv run pytest` not `pytest`

If a sandbox prevents access to the default `uv` cache (e.g., `~/.cache/uv` or `~/Library/Caches/uv`), point `uv` at a project-local cache by setting `UV_CACHE_DIR=.uv-cache` (add `.uv-cache/` to `.gitignore` if it's not already covered).

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
- For "every letter that has an anchor at y=N" use `{exit_y: N}` / `{entry_y: N}` selectors. They expand at compile time to every variant carrying that exit / entry anchor, which mirrors the FEA `@exit_y{N}` / `@entry_y{N}` classes without hand-curating a `context_set`. Add `except: [{family: …}, …]` to drop specific families from the resolved set (e.g., `{exit_y: 0, except: [{family: qsYe}, {family: qsPea}, {family: qsTea}]}` skips ·Ye/·Pea/·Tea even though they exit at y=0).
- When multiple Quikscript forms share the same selector or anchor scaffolding, prefer `inherits` over copying the whole form, and clear inherited nested keys with `null` when a child form needs to drop them.
- When rewriting `glyph_data/quikscript.yaml`, keep anchor coordinate pairs inline as `[x, y]`. Keep short `traits`, `modifiers`, and `select` / `derive` reference lists inline too, and only fall back to block lists when entries are genuinely long enough that inline formatting hurts readability.
- The standard x-value for `cursive_exit` is one pixel to the right of the stroke. Usually this places the anchor just past the bitmap's right edge, but sometimes the anchor falls inside the bitmap — as with ·He, ·Ye, and `qsThey.exit-xheight` — because the stroke exits from the left or middle of the glyph.
- When a narrow `after:` selector competes with a broad fallback like a `context_set`, make sure the narrow selector wins first. `qsThaw.after-ing` must beat `qsThaw.after-tall`.
- When deciding whether a preserved join may survive later context, account for downstream ligatures too; if the right glyph is about to be consumed into a ligature with no matching entry, the left glyph must not keep a now-false exit.
- When a two-glyph ligature should beat a right-hand join on its second component, let `calt_liga` match the second component's forward-exit variants too, rather than relying on a broad guard that can spill into unrelated ligatures.
- When a ligature consumes a glyph, that consumed component must not keep choosing variants on the following glyph; normalize the follower back to what the ligature itself supports, then let any explicit after-ligature overrides reapply.
- After `·Et`, keep `·Tea` on its left baseline join and suppress its ordinary right joins; preserve `·Tea·Oy` through the ligature rules instead of an `·Et`-specific `qsTea` form that joins on both sides.
- Use `ss05` to opt back into `·Et ~b~ ·Tea ~b~ ...` baseline double-joins when needed; the default shaping should still suppress Tea's ordinary right joins after `·Et`.
- If adding an `after:` form moves a family out of `fwd_only`, only pull the family's plain forward-exit Ys early when later backward lookups truly depend on those Ys; do not pull same-Y forward pair overrides early just to preserve that dependency.
- For `·-ing` before `·Thaw`, extend `qsIng`'s exit rather than shifting `qsThaw`'s entry left.
- Group lookups by Y value to prevent cross-pair attachment between glyphs at different heights.
- During `calt`, a ZWNJ boundary is still the literal `uni200C` glyph; if a selector needs to block or require a ZWNJ boundary, target `uni200C` in `after` / `not_after` rather than `space`.
- Two-glyph ligatures (`qsX_qsY`) inherit their entry anchor from the lead `qsX` automatically: the build copies the entry coords from `qsX`'s prop (or, if the prop has no entry, from `qsX.entry-xheight` when that form is unrestricted), and `_iter_related_extension_targets` then propagates `qsX`'s `extend_entry_after` rules onto the ligature with no further YAML. Don't restate the entry on the ligature, and don't duplicate `extend_entry_after` there — adjustments to `qsX`'s entry-side joins will track automatically. Declare an explicit entry on the ligature only when the lead's inheritable form is intentionally context-restricted (`qsThey.entry-xheight`, `qsJai.entry-xheight`) or the ligature's bitmap doesn't share the lead's leftmost-ink column at the entry's Y; the build emits a `LigatureEntryInheritanceWarning` whenever an explicit declaration could otherwise be removed.
- The mirror image holds for the exit side: `_iter_related_extension_targets` propagates the trailing component's `extend_exit_before` / `contract_exit_before` onto a `qsX_qsY` ligature, and `calt_liga` then routes `(qsX, qsY.<exit-modifier>)` through to `qsX_qsY.<exit-modifier>` (extension or contraction) so the trailing component's join behavior survives the ligature collapse. Don't restate trailing-component exit rules on a ligature unless the ligature genuinely needs different behavior — and if it has its own `noentry_after`, the trailing-side propagation is skipped because the post-liga noentry routing currently only handles the base ligature glyph; carry your own exit rules there in YAML for now.
- A successor's `select.after: [{family: qsY}]` clause implicitly matches every ligature whose trailing component is `qsY` (and the symmetric `before: [{family: qsX}]` implicitly matches every ligature whose lead is `qsX`). `expand_selectors_for_ligatures` handles this at IR time: it adds the matching ligature variants to the selector list so the post-liga form-selection survives `calt_liga`. Don't hand-list ligature names like `qsJay_qsUtter` in `select.after` / `select.before` — the pass takes runtime-state into account (it skips base ligatures whose trailing component would mutate before the source's family, e.g., `qsJai`'s after only picks up `qsX_qsUtter.exit-doubly-contracted`, not the base `qsX_qsUtter`) and also skips ligatures whose own `select.after` / `select.before` excludes the source. The companion FEA emission narrows `calt_post_liga_*` trigger classes to ligature glyphs only, so `calt_cycle`'s pre-liga form-selection isn't disturbed.
- A ligature can opt out of all left-side joins by declaring `entry: null` on its `prop.anchors`. The merge preserves the `null` sentinel, `JoinGlyph.entry_explicitly_none` becomes True, and `_inherit_ligature_entries_from_lead` skips inheritance from the lead. `calt_post_liga_left_cleanup` then reverts any predecessor whose lead-component-targeted form was selected pre-liga back to its default (or its entryless sibling), so neither cursive attachment nor visual abutment survives the ligature collapse — `qsAt_qsMay` is the running example. `expand_selectors_for_ligatures` also skips these ligatures, so no later `calt` rule can re-apply a lead-targeted form against the ligature glyph. Don't hand-author `not_before: [qsX_qsY]` clauses on predecessor variants for new no-entry ligatures — the FEA emitter handles it data-driven off the flag, mirroring the trailing-side `calt_post_liga_cleanup` pass.

## How to do simple changes

Most one-line cursive-attachment tweaks fit one of the patterns below. Don't reach for a new form before checking these first.

### Make ·X·Y use Y's `<shape>` shape

Add `{family: qsX}` to the `select.after` list of the qsY form that uses that shape. If qsY's `prop.derive.extend_entry_after.targets` mirrors the same `select.after` list, add `{family: qsX}` there too to keep them in lockstep. Example: making ·Jay·Roe use Roe's `shortened_top` shape adds `{family: qsJay}` to both `qsRoe.forms.entry_extended_at_baseline.select.after` and `qsRoe.prop.derive.extend_entry_after.targets`.

### Extend the ·X·Y connection by N pixels

Add `{family: qsY}` to qsX's family-level `derive.extend_exit_before.targets` (creating the dict with `by: N` if it doesn't exist). The build widens X's exit stroke and shifts the exit anchor by the same amount, so the receiver moves right and the connecting stroke gains N pixels of ink. Example: extending ·Jay·Exam by a pixel adds `{family: qsExam}` to `qsJay.derive.extend_exit_before.targets`.

### Contract (opposite-of-extend) the ·X·Y connection by N pixels

Add `contract_entry_after: {by: N, targets: [{family: qsX}]}` to qsY's joining form's `derive` (typically `entry_xheight` for xheight joins, `entry_baseline` for baseline joins). Don't make a new form, even when qsY already has an `extend_entry_after` whose targets implicitly include qsX (e.g., through `halves_exit_xheight`) — the narrow contract rule (a single family) wins over the broad extend rule because the build orders narrower selectors first. Example: contracting ·He·Jay by a pixel adds `contract_entry_after: {by: 1, targets: [{family: qsHe}]}` to `qsJay.forms.entry_xheight.derive`, alongside the existing `extend_entry_after`.

### Conflicting `by` values

A single `derive` block holds at most one each of `extend_entry_after`, `extend_exit_before`, `contract_entry_after`, and `contract_exit_before`, with a single `by` and a list of targets per directive. When you'd need a different `by` for a new target on a directive that's already in use (e.g., `qsHe.forms.half` already has `contract_exit_before: {by: 2, targets: [{family: qsZoo}]}` and you want `by: 1` for a different target), put the new rule on the other side of the join (e.g., contract Y's entry-after instead of X's exit-before).

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
- To remove a duplicate test: remove the `data-expect` attribute. If the element is a `span` with no remaining attributes, unwrap the `span` (remove the tags but keep the text content in place). Never remove the text inside the element — it must remain identical before and after. The text frequently contains invisible PUA code points, so verify with a program (e.g., compare hex dumps of each modified line before and after) that only the attribute and/or tags were removed.
- When adding `data-expect` attributes, always check for content duplicates first — do not wrap a word that is already tested elsewhere in the document unless explicitly told to.
- Do not wrap one-letter Quikscript words in `data-expect` attributes unless explicitly told to — there is no point in testing joins when there is only one letter.
- When consolidating redundant tests, do not rewrite existing `data-expect` values in `test/the-manual.html`; preserve the manual corpus and remove redundant coverage elsewhere instead.

## Markdown-document style

- Use sentence case for titles, not title case.
- Do not hard-wrap lines. Let each paragraph or list item be a single long line.
- Ensure that Markdown tables are nicely formatted for humans and have nicely lined-up columns.
- Make sure `markdownlint-cli2` doesn’t have anything to complain about.
