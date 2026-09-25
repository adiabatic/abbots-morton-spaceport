# Side-quest: rune-schema hover documentation

The open side-quest off the main M1 thread (`WHATNEXT.md`): write and clean up the hover documentation in `rebuild/schema/rune.schema.json`. These are the JSON Schema `description` strings that VS Code (through the Red Hat YAML extension) shows as tooltips while authoring `glyph_data/runes/*.yaml`. The only audience is the owner, so the text explains the schema-specific machinery without re-teaching font internals the owner already knows. The hover text lives only in `rune.schema.json`.

`rebuild/schema/BETTERRUNESCHEMA.md` is the tracker and holds everything else: the governing decisions D1–D6 (D6 is the one-hover-per-round drafting loop), the walk status and the command that lists which `$defs` are still bare, what the walk does next, and the open leans to react to. Don't restate any of that here. This entry keeps the side-quest visible from the main to-do list without a second copy of its state.

One standing authorization lives only here: per-round commits are pre-authorized for this effort.
