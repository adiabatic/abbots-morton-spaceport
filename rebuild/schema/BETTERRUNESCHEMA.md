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

The descriptions are read as **editor hover tooltips in VS Code specifically** while authoring a rune YAML (the Red Hat YAML extension resolves `$schema` → `rune.schema.json` and pops the matching key's `description`). Targeting one editor sharpens the budget:

- **Markdown renders — use it.** VS Code renders the `description` as Markdown in the hover, so backticks, **bold**, and bullet lists all display properly. No need to write for a plain-text fallback. Still keep it tight: a hover is a small floating box, so aim for a lead sentence plus the why, maybe a short bullet list — not a wall.
- **Tight and self-contained.** Each description stands on its own without a scroll or click-through.
- **Per-key, not per-region.** The hover is keyed to whatever the cursor is on, so every documented key carries its own complete answer; we don't lean on a neighboring key's description for context.

### Interview-style learning (how to ask the owner)

The owner is, by design, not steeped in the schema's internal machinery — that is the whole reason this documentation exists. So interview questions must **not** themselves lean on unexplained concepts. When the owner's judgment is needed, anchor the question on something they already know (a join they author by hand, a ·X·Y outcome, a term from `CLAUDE.md`'s "How to do simple changes") and, where possible, have them **react to a real drafted description** rather than answer an abstract meta-question. Show, don't quiz.

## Field inventory

*(To be populated from the schema-understanding survey: every field/`$def`, its plain meaning, where the pipeline consumes it, a real example, and whether it's codebase-answerable or needs the owner's intent.)*

## Interview queue

*(To be populated: the ordered list of owner-only questions, foundational first, then region by region in schema reading order.)*
