# Side-quest: rune-schema hover documentation

Shelved checkpoint for the rune-schema documentation effort. This is a side-quest off the main M1 rune-migration thread (see `WHATNEXT.md`); pick it up here when you want to resume. Nothing about it is committed beyond the work described below, and it is fully self-contained.

## The task

Write accessible hover/`description` text into `rebuild/schema/rune.schema.json` — the schema that governs `glyph_data/runes/*.yaml` (e.g. `qsMay.yaml`). The strings are JSON Schema 2020-12 `description` keywords; they render as VS Code hover tooltips via the Red Hat YAML extension and are inert to validation. The sole audience is the owner, who hand-writes the rune YAML but is not a pipeline engineer — so the text explains the schema-specific machinery without re-teaching font internals the owner already knows.

The four governing decisions (D1–D4), the field inventory, the walk table, and the full decision log all live in `rebuild/schema/BETTERRUNESCHEMA.md`, which is the **task tracker**. The hover **text** itself is the source of truth and lives only in `rune.schema.json`; the tracker records the call and its why, not the text.

## Workflow (locked in — keep it)

- **One question at a time** via the AskUserQuestion tool. The owner reacts to **real drafted text shown in side-by-side previews**, not abstract meta-questions. Show the already-committed sibling hover in the preview when there is one, so the two can be compared.
- Previews are **hard-wrapped to about 50 columns for display only** — the text that actually lands in the schema is one unwrapped line (the no-hard-wrap rule). Say so in the question.
- **Every** open question is asked individually, including low-stakes "how much detail / how to phrase it" calls — no batching, even where there is an obvious lean.
- Per decision, in order: verify the example against the real YAML, write the description into `rune.schema.json`, validate the JSON (`uv run python -c "import json; json.load(open('rebuild/schema/rune.schema.json'))"`), update the tracker (flip the walk-row status, add an `R##` decision-log entry, delete any resolved `q##` from the Open list), **realign the walk table** with the embedded Python realigner (it splits on unescaped `|` so escaped pipes survive), lint with `npx markdownlint-cli2 rebuild/schema/BETTERRUNESCHEMA.md` (grep the output for `BETTERRUNESCHEMA.md:` — the only matches should be the linter's own "Finding" header), then commit. Per-answer commits are pre-authorized for this effort.
- **Calibration learned across the walk:** plain beats precise-but-dense; terse with no example for the keys the owner touches daily; examples only on complex or contextual keys. In practice the owner consistently picks the **more-informative** variant — spell out a build-checked promise, give the full wiring list, keep the steering note, show both examples — so lead with that as the recommended option.

## What is done

The entire `qsPea.yaml` document-order walk — all 29 rows, 43 descriptions in the schema, committed as `R11` through `R26` (commits `c79bec5` through `2160725`, the most recent being "Document policy groups (R26) — qsPea walk complete"). This covered the exit-row keys (`x`, `stroke`, `withdrawal`, `toward`), the surface keys (`pairings`, `cells`, `unlocks`, `require`, `bitmaps`), and the whole policy block (`order`, `refuse` + `why`, `prefer`, `extend` + `ok`/`split`, `contract`, `resolve`, `groups`). Owner-judgment forks q14–q18, q16, and q25 were resolved along the way.

Three things worth remembering from the walk:

- **q25 reversed its earlier lean.** The refuse `why` leaf landed as a plain optional note, not a "set a bar" convention, once the corpus turned out to leave many self-evident refusals deliberately bare — a bar would misrepresent the data.
- **A latent bug was fixed in passing:** the D3-templated `extend` hover had been recorded as decided but never actually written into the schema. It is written now (R23).
- **Owner wording corrections that stuck:** "·Utter.alt's `reaches-way-back` exit" on `toward`; "surface row" replaced with "that stance, joining at that height" on `refuse`; both examples shown on `contract`.

## What is next

The natural next phase is the **`when`-grammar condition region**. These are shared `$defs` that `qsPea` references but that were not part of its 29-row document-order walk: the `when` container itself, `leftCondition` / `rightCondition` / `selfCondition` and their sub-keys (`joined_at`, `then`, `except`, `stroke`, `class`), and the `motionName` reserved-token rule. They carry the still-open questions **q19–q24**, each already recorded with a lean in the "Open (leans to react to)" section of `BETTERRUNESCHEMA.md`:

- **q19 — condition mental model.** Teach the left-settled / right-raw symmetry as the organizing idea on the `when` container hover; leaf keys stay procedural and point up to it. (Partly resolved already — the asymmetry rode onto `from` at R10 — but the `when` container hover itself is still unwritten.)
- **q20 — `then` framing.** Why `then` is allowed on `prefer` but banned on `refuse`/`require`; lean is use-case first, window mechanics as a note.
- **q21 — `except` framing.** Positive carve-out ("all except these") vs logical negation; lean is the carve-out plus a brief logical note.
- **q22 — reserved-token history.** Why `before`/`after`/`noentry`/… and `ss[0-9]` are forbidden in stance names (they were old display-name suffixes); lean is the principle inline ("names describe the motion, not the neighbors") with the history kept terse.
- **q23 — namer-dot / word position.** Whether to surface that space and ZWNJ split runs but the namer-dot does not; lean is to surface it.
- **q24 — migration bridging.** Whether to map the old `quikscript.yaml` `entry_xheight_exit_baseline`-style keys to the new schema; lean is a brief mapping note kept terse.

Per D4, the cross-cutting symmetry belongs on the `when` **container** hover; the leaf condition keys stay procedural and carry a one-clause pointer up to the container.

## Health note

Three `rebuild/test_spec_load.py` failures (`test_group_resolution`, `test_predicate_class_membership`, `test_loads_all_six_runes`) are pre-existing M1-batch debt — a rune-set mismatch between the test fixtures and the in-progress migrated data, where `qsPea` shows up as an "extra item". They are orthogonal to this description-only work; do not chase them here. Note that `make test` does not cover `rebuild/`, so schema-loading tests must be run directly (`uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal`).
