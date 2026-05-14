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

## HTML/CSS/JS

- Prefer nested CSS over flat CSS.
- Prefer for-of loops to `.forEach()`.
- Use modern range syntax for media/container queries: `(width > 40em)` not `(max-width: 40em)`.

## Python

IMPORTANT: Always use `UV_CACHE_DIR=.uv-cache uv run` instead of `python` or `python3` directly. The `UV_CACHE_DIR=.uv-cache` prefix points `uv` at a project-local cache so it works in sandboxed environments that can't reach the default `uv` cache (`~/.cache/uv` or `~/Library/Caches/uv`). For example:

- `UV_CACHE_DIR=.uv-cache uv run pytest` not `pytest`

## Background information

- “Orthodox” is Quikscript-speak for “English written in the Latin script”.

## Adding glyphs

- Whenever a glyph is added to any YAML file under `glyph_data/`, ensure it also has an entry in @postscript_glyph_names.yaml if it uses a standard PostScript name (not a `uniXXXX` name).
- Keep all glyphs alphabetized by code point (`uniXXXX`).
- See @doc/glyph-names.md for the canonical map between names (·Pea), PostScript family names (qsPea), and code points (U+E650).
- Tall letters have a height of 9 pixels (9 entries in `bitmap`).
- Short letters have a height of 6 pixels (6 entries in `bitmap`).
- Deep letters have a height of 9 pixels (9 entries in `bitmap`) and a `y_offset` of -3.
- "Ink" is a filled bitmap pixel — a `#` cell, as opposed to a space. The font's strokes are made of ink. The cursive-attachment tooling talks about a row's "leftmost-ink column" (the leftmost `#` in that row), "no ink at y=N" (the row at glyph-space y=N is all spaces), and `exit_ink_y` (the fallback row to scan when the exit anchor's own Y has no ink).
- For bitmap data in `glyph_data/quikscript.yaml`, use double-quoted row strings. Add bare trailing `#` comment markers on the rows whose glyph-space `y` values are 5 and 0; which bitmap rows those are depends on `y_offset`.

## Inspiration

- When referring to Quikscript letters, they are frequently prefixed by a `·`, like in `·Why`.
- Use @reference/csur/index.html to find out what Quikscript letters go with what code points.

## Locations of things

- Quikscript source data lives under `glyph_families` in `glyph_data/quikscript.yaml`, with separate `mono`, `prop`, `shapes`, and `forms` records. Put `entry` / `exit` anchors (under an `anchors:` mapping) in the proportional record or form that should compile into the proportional font; mono-only records do not carry `curs` anchors.
- Shared Quikscript bitmaps belong under a family's `shapes`, and contextual/alternate forms belong under `forms` with `anchors`, `select`, `derive`, `traits`, and `modifiers`. Preserve `traits: [alt]` and `traits: [half]` when those concepts are real; other suffixes belong under ordered `modifiers`.

## YAML files

- Quikscript form keys (`alt_reaches_way_back`, `entry_xheight`) are local labels — compiled glyph identity and compatibility metadata come from each form's explicit `traits` and `modifiers`. Structured selectors in `select` / `derive` can combine all three, e.g. `{family: qsUtter, traits: [alt], modifiers: [reaches-way-back]}`.
- Within `select` / `derive` lists and `context_sets`, keep `{family: qsX}` entries in code-point order (qsPea, qsBay, qsTea, …, qsOoze — see @postscript_glyph_names.yaml). For ligatures, sort by the lead family's code point, with the bare lead before any ligature that starts with it.
- When multiple Quikscript forms share the same selector or anchor scaffolding, prefer `inherits` over copying the whole form, and clear inherited nested keys with `null` when a child form needs to drop them.

### Formatting

- When rewriting `glyph_data/quikscript.yaml`, keep anchor coordinate pairs inline as `[x, y]`. Keep short `traits`, `modifiers`, and `select` / `derive` reference lists inline too, and only fall back to block lists when entries are genuinely long enough that inline formatting hurts readability.

### Selectors

- For "every letter that has an anchor at y=N" use `{exit_y: N}` / `{entry_y: N}` selectors instead of hand-curating a `context_set`. Add `except: [{family: …}, …]` to drop specific families from the resolved set (e.g., `{exit_y: 0, except: [{family: qsYe}, {family: qsPea}, {family: qsTea}]}` skips ·Ye/·Pea/·Tea even though they exit at y=0).
- For "this family, but only variants with a compatible anchor at y=N" use family-scoped anchor selectors such as `{family: qsMay, exit_y: 5}` in `after` lists or `{family: qsTea, entry_y: 0}` in `before` lists. These mirror normal family selector behavior, including compatible ligature/component expansion, then apply the anchor-Y filter. Use `tools/suggest_scoped_anchor_selectors.py` to find candidates, but do not replace a broad family selector unless generated Senior FEA or focused shaping tests prove the change is equivalent.
- Family-scoped anchor selectors may still compile to include the bare scoped glyph when that bare glyph is the pre-lookup form for an unrestricted entry/exit upgrade at the requested Y; this lets cyclic joins like ·They·May use `{family: qsMay, entry_y: 0}` instead of widening back to `{family: qsMay}`.
- Don't mechanically replace long family lists with `{entry_y: …}` / `{exit_y: …}` just because the list is long. Anchor selectors expand to every matching variant, so a shorter source list can still change generated FEA or create join warnings; prove equivalence with the generated Senior feature code or the shaping tests before committing such a cleanup.
- When a narrow `after:` selector competes with a broad fallback like a `context_set`, make sure the narrow selector wins first. `qsThaw.after-ing` must beat `qsThaw.after-tall`.

### Anchors

- The standard x-value for an `exit` anchor is one pixel to the right of the stroke (`exit.x = max_ink_x_at_exit_y + 1`). Usually this places the anchor just past the bitmap's right edge, but sometimes the anchor falls inside the bitmap — as with ·He, ·Ye, and `qsThey.exit-xheight` — because the stroke exits from the left or middle of the glyph.
- Symmetrically, the standard x-value for an `entry` anchor sits **on** the leftmost ink at the entry's row (`entry.x = min_ink_x_at_entry_y`). Wide letters whose entry attaches to an already-inset stroke land at the actual leftmost ink, not at `x = 0` — e.g. `qsBay.entry-xheight` at `(3, 5)` over `"   # "`, `qsThaw.prop` at `(2, 0)` over `"  #  "`. Derived `*.entry-extended` / `*.entry-contracted` / `*.entry-trimmed-by-N` variants intentionally diverge from this; see `tools/audit_anchor_geometry.py`'s docstring for which buckets are expected anomalies.

### …

- When the right glyph is about to be consumed into a ligature with no matching entry, the left glyph must not keep a now-false exit. `·Excite·Tea·Oy` is the worked example: `qsTea_qsOy` has no baseline entry, so `qsExcite.exit-baseline.before-vertical` must surrender its exit. Extend `_PENDING_LIGA_ENTRY_GUARDS` in `tools/quikscript_fea.py` rather than broadening the plain pair-guard machinery.
- When you'd otherwise author a near-duplicate `before:` form so an entryless forward-exit form can displace its entry-bearing siblings, reach for `strip_entry_before: true` instead; see `qsAt.entry_nowhere_exit_baseline` for the worked example, and the JSON schema description for the full rules.
- In `calt` selectors, ZWNJ is the literal `uni200C` glyph — list it alongside `space` in `after` / `not_after` when blocking word boundaries.
- Two-glyph ligatures inherit their entry anchor from the lead automatically (see `_inherit_ligature_entries_from_lead`); the build emits `LigatureEntryInheritanceWarning` for any redundant or mismatched explicit declaration. Keep an explicit entry only when the lead's inheritable form is context-restricted (e.g. `qsThey.entry-xheight`) or the ligature's bitmap doesn't share the lead's leftmost-ink column at the entry's Y.
- The mirror image holds for the exit side: `_iter_related_extension_targets` propagates the trailing component's `extend_exit_before` / `contract_exit_before` onto a `qsX_qsY` ligature, and `calt_liga` routes `(qsX, qsY.<exit-modifier>)` through to `qsX_qsY.<exit-modifier>`. Don't restate trailing-component exit rules on a ligature — unless it declares its own `noentry_after`, which skips the propagation (currently true for `qsThey_qsUtter`); in that case, carry the exit rules on the ligature in YAML.
- Don't hand-list ligature names (e.g. `qsJay_qsUtter`) in `select.after` / `select.before`; the `expand_selectors_for_ligatures` IR pass adds them automatically from the trailing/lead component. See its docstring for edge cases.
- A ligature opts out of all left-side joins by declaring `entry: null` on its `prop.anchors` (see `entry_explicitly_none` in `tools/quikscript_ir.py`). The FEA emitter reverts predecessors and skips the ligature from `expand_selectors_for_ligatures` automatically — don't hand-author `not_before: [qsX_qsY]` clauses on predecessor variants to compensate.
- When a `noentry_after` ligature leaves a predecessor's bare bitmap with unsupported exit-side ink, add an explicit `.exit-noentry` form with no exit anchor and the trimmed bitmap. Two flavors exist:
  - **Entryless** (`qsMay.exit-noentry`): no entry either; the post-liga cleanup chooses this when the predecessor's selected variant has no entry anchor (typical when nothing exits at the matching Y).
  - **Entry-preserving** (`qsMay.entry-baseline.exit-noentry`, authored via `inherits: entry_baseline` plus `anchors.exit: null` and a `[exit-noentry]` modifier): the entry side is kept so the join with the predecessor still attaches. Authoring this also opts the family out of the calt_cycle guard that `_propagate_noentry_after_to_not_before` would otherwise emit, so the matching entry-bearing form (e.g. `qsMay.entry-baseline`) is picked pre-liga and `·Roe·May` joins at the baseline whether or not the entryless ligature follows.
  The `_exit_noentry_fallback` in `tools/quikscript_fea.py` matches the input variant's entry side and modifier set when picking the replacement, so each input variant routes to the closest sibling. The second pass, `calt_post_liga_left_cleanup_pred`, only fires when the replacement is entryless: any pre-predecessor whose `select.before` clause was triggered by the now-demoted family is reverted to its bare base, so a glyph like `qsRoe.exit-baseline` doesn't keep extending toward an entry that no longer exists.

## How to do simple changes

Most one-line cursive-attachment tweaks fit one of the patterns below. Don't reach for a new form before checking these first.

### Make ·X·Y use Y's `<shape>` shape

Add `{family: qsX}` to the `select.after` list of the qsY form that uses that shape. If qsY's `prop.derive.extend_entry_after.targets` mirrors the same `select.after` list, add `{family: qsX}` there too to keep them in lockstep. Example: making ·Jay·Roe use Roe's `shortened_top` shape adds `{family: qsJay}` to both `qsRoe.forms.entry_extended_at_baseline.select.after` and `qsRoe.prop.derive.extend_entry_after.targets`.

### Extend the ·X·Y connection by N pixels

Add `{family: qsY}` to qsX's family-level `derive.extend_exit_before.targets` (creating the dict with `by: N` if it doesn't exist). The build widens X's exit stroke and shifts the exit anchor by the same amount, so the receiver moves right and the connecting stroke gains N pixels of ink. Example: extending ·Jay·Exam by a pixel adds `{family: qsExam}` to `qsJay.derive.extend_exit_before.targets`.

### Contract (opposite-of-extend) the ·X·Y connection by N pixels

Add `contract_entry_after: {by: N, targets: [{family: qsX}]}` to qsY's joining form's `derive` (typically `entry_xheight` for xheight joins, `entry_baseline` for baseline joins). Don't make a new form, even when qsY already has an `extend_entry_after` whose targets implicitly include qsX (e.g., through `halves_exit_xheight`) — the narrow contract rule (a single family) wins over the broad extend rule because the build orders narrower selectors first. Example: contracting ·He·Jay by a pixel adds `contract_entry_after: {by: 1, targets: [{family: qsHe}]}` to `qsJay.forms.entry_xheight.derive`, alongside the existing `extend_entry_after`. If the directive on that side already has a different `by`, put the rule on the other side of the join instead.

### Mix `before:` and `not_before:` on a single form

When a form needs both a forced positive trigger and a broad anchor-class fallback, declare both `before:` and `not_before:` on the same form rather than splitting into two near-duplicates. The symmetric pair `after:` / `not_after:` works the same way. See `qsGay.forms.exit_baseline` for the canonical example.

## Bumping the version number

1. Update `version` in `glyph_data/metadata.yaml` (e.g., `3.000`)
2. Update `version` in `pyproject.toml` (e.g., `3.0.0`)
3. Run `uv sync` to update `uv.lock`

## Visual before/after diffs

- `test/check.html` is a side-by-side rendering harness for eyeballing glyph changes. On the baseline branch, run `make snapshot-before` to capture the "before" OTFs into `test/before/` (gitignored); make your changes on a branch, run `make check-html`, and reload. The whole file is regenerated by `tools/build_check_html.py` (called from the `check-html` target) — both auto-generated sections (corpus render diffs and isolation leaks) plus the surrounding chrome — so don't hand-edit it. The file itself documents the workflow in more detail.

## Markdown-document style

- Use sentence case for titles, not title case.
- Do not hard-wrap lines. Let each paragraph or list item be a single long line.
- Ensure that Markdown tables are nicely formatted for humans and have nicely lined-up columns.
- Make sure `markdownlint-cli2` doesn’t have anything to complain about.
