# Side-quest: rune-schema hover documentation

The open side-quest off the main M1 thread (`WHATNEXT.md`): write — and clean up the verbiage of — the hover documentation in `rebuild/schema/rune.schema.json`, the JSON Schema `description` strings that VS Code (via the Red Hat YAML extension) pops as tooltips while authoring `glyph_data/runes/*.yaml`. The sole audience is the owner; the text explains the schema-specific machinery without re-teaching font internals the owner already knows. The governing decisions (D1–D6), the walk state, and the open leans live in `rebuild/schema/BETTERRUNESCHEMA.md`; the hover text itself is the source of truth and lives only in `rune.schema.json`.

## Workflow (locked in — D6)

One hover per round (or one coherent set of sibling one-liners). The draft is staged as editable Markdown in `tmp/hover-drafts.md` (a blank line becomes the `\n\n` break; each paragraph stays one unwrapped line) and written into `rune.schema.json` at the same time, so the real tooltip renders on hover in a rune file. The owner edits the draft directly — typically pointing at a phrase to dejargonize — then says “land it”; the text is synced verbatim, validated, linted, and committed (per-round commits are pre-authorized for this effort). AskUserQuestion is reserved for genuine forks, never wording. The schema deliberately carries the unapproved draft between rounds and is never committed until landed.

## What is next

- **The remaining bare when-grammar territory** — `whenWindowDecidable`’s `left` / `self` / `word` / `feature` (the container’s hover and its `right` one-liner are committed; `word` should mirror the settled `when.word` wording), `when.feature`, `rightCondition` / `rightConditionNoThen`, `selfCondition`, `staticCondition`, `groupDefinition`, and the scalar `$defs`; then the open leans (q22 reserved-token history, q24 migration bridging, the `bitmaps` `$def` scope note) recorded in the tracker.
- **The D5 retrofit** — most of the ~50 committed hovers predate the lead-summary + `\n\n` shape and read denser than the owner wants; restructure each whenever it’s touched anyway, not as a big-bang pass.
