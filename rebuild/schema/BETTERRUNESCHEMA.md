# Better rune-schema documentation — working plan

The plan for slotting accessible documentation into `rune.schema.json` (the schema that governs `glyph_data/runes/*.yaml`, e.g. `qsMay.yaml`). This file is the scratchpad: we interview, record answers here, reconcile, and only then write the real `description` strings into the schema. It is not itself shipped documentation.

## How we're working

- One question at a time. Anything the codebase can answer, we answer by reading the codebase — not by asking. The interview is reserved for decisions only the owner can make: tone, audience, naming, taste, and where prose lives.
- Each answer gets written down here, then committed, before moving on.
- Guided by [Diátaxis](https://diataxis.fr/): a schema `description` is fundamentally *reference* (austere, lookup-oriented), but this project wants it *understandable*, which pulls in some *explanation*. The decisions below resolve that tension deliberately rather than by accident.

## Decisions

### D1 — Audience and the reference/explanation split

**The sole consumer of this documentation is the owner (Nathan).** There is no third-party reader to calibrate for. So:

- We may freely assume fluency in the project's own vocabulary — ·Letter names, `qsName` families, *stance*, *ductus*, *ink*, *trait*, *half*/*alt*, *anchor*, *seam*. We do **not** re-teach font internals the owner already knows.
- We **do** explain the schema-specific machinery the owner does *not* carry in their head (what an `unlock` does, what `withdrawal: safe` promises, what `ok`/`split` on an `extend` mean, etc.).
- **Austere reference goes up front; the "why" must be right there too — not buried.** The owner is explicitly unwilling to dig into `model.py` docstrings or the M1 plan to recover intent. So the "why" lives where the eyes already are, not one hop away.

Concretely, each `description` string is **two beats in one string**: a terse lead that says what the thing *is* (the austere reference), immediately followed by the *why/how* in the same string so both surface together. No separate explanation document the owner would have to go open. Diátaxis purity yields to convenience here because there is exactly one reader and they value "handy" over "clean."

### D2 — Delivery surface: editor-hover tooltips

The descriptions are read as **editor hover tooltips** while authoring a rune YAML (the YAML language server resolves `$schema` → `rune.schema.json` and pops the matching key's `description`). This sets the format budget:

- **Tight and self-contained.** A tooltip is a small floating box, not a page. Each description should land in roughly a short paragraph or two — lead sentence + the why — and stand on its own without requiring a scroll or a click-through.
- **Markdown that degrades gracefully.** Some editors render the description as Markdown in the hover; others flatten it to plain text. So light Markdown is fine (backticks, the occasional bullet), but the prose must still read cleanly with the markup stripped. No reliance on tables, headings, or multi-level nesting inside a description.
- **Per-key, not per-region.** Because the hover is keyed to whatever the cursor is on, every documented key carries its own complete answer; we don't lean on a neighboring key's description for context.

## Field inventory

*(To be populated from the schema-understanding survey: every field/`$def`, its plain meaning, where the pipeline consumes it, a real example, and whether it's codebase-answerable or needs the owner's intent.)*

## Interview queue

*(To be populated: the ordered list of owner-only questions, foundational first, then region by region in schema reading order.)*
