# Side-quest: rune-schema hover documentation

The open side-quest off the main M1 thread (`WHATNEXT.md`): write — and clean up the verbiage of — the hover documentation in `rebuild/schema/rune.schema.json`, the JSON Schema `description` strings that VS Code (via the Red Hat YAML extension) pops as tooltips while authoring `glyph_data/runes/*.yaml`. The sole audience is the owner; the text explains the schema-specific machinery without re-teaching font internals the owner already knows. The hover text itself is the source of truth and lives only in `rune.schema.json`.

`rebuild/schema/BETTERRUNESCHEMA.md` is the tracker, and it owns everything else: the governing decisions D1–D6 — including D6, the one-hover-per-round drafting loop — the walk status and the command that derives which `$defs` are still bare, what the walk does next, and the open leans to react to. Don't restate any of that here. This entry exists so the side-quest stays visible from the main punch list, not to hold a second copy of its state that can disagree with the tracker.

One standing authorization lives only here: per-round commits are pre-authorized for this effort.
