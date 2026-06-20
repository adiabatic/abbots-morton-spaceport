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

Since the qsPea walk the effort has continued through **R39**. The authoritative per-entry record is the decision log in `BETTERRUNESCHEMA.md`; in brief:

- **R27–R31 — the when-grammar region.** The `policy` container (R27), the `when` container with its left-settled / right-raw symmetry (R28), `leftCondition` (R29), the shared condition leaves and scalars plus the `except` carve-outs (R30), and `rightCondition.then` (R31, resolving q20).
- **R32 — `when.word`** (resolving q23).
- **R33–R39 — the source-order pass** (see "What is next" for the methodology): `notes` (R33), the `stances` map (R34), `stance.y_offset` (R35), `entryRow.joined` (R36) and `entryRow.joined_x` (R37), a clarity-and-scope rework of `exitRow.withdrawal` (R38), and a cross-cutting sweep banishing the prose noun **"drawing"** from every hover (R39 — now a banned term recorded in D1; say `bitmap` or a plain verb instead).

## What is next

**Selection rule (set this session): a source-order pass over the real rune YAML.** Instead of picking by abstract walk-row code, document the next still-**bare** field that actually appears in a live `glyph_data/runes/*.yaml`, walking document order across the files — root order `rune` → `codepoint`/`sequence` → `ductus` → `notes` → `mono` → `stances` → `policy`, then descending into each. The tracker's decision log has a **"Source-order pass over real YAML"** subsection spelling this out. The Phase-2 when-grammar walk table still maps the territory and lists what remains.

**Paused inside the `surface` block.** `entryRow` and `exitRow` are now fully documented. The immediate open fork — asked, then deferred by the owner — is whether to:

- give the `pairing` `$def` leaves (`entry`/`exit`, the `{entry, exit}` items under `surface.pairings` never/only) their own hovers, even though the documented `pairings` container already explains each side ("a height … or `none`"); **or**
- treat them as covered by the container (per D4) and **skip to `cellBinding`** — 5 genuinely bare leaves (`entry`/`exit`/`bitmap`/`entry_x`/`exit_x`), the cell-grain mirror of `joined`/`joined_x`. Real occurrences: `qsMay.yaml:81`, `qsPea.yaml:98`. **Recommended lean: skip to `cellBinding`.**

**Also open / logged in the tracker:**

- The `bitmaps` `$def` scope-precision follow-up (does it need the same "names resolve to this stance's own `bitmaps`, not the base `bitmap` or `mono`" note that `joined`/`withdrawal` got in R36/R38?).
- Remaining when-grammar leaves and containers: **q22** (`motionName` reserved-token history), **q24** (migration bridging), and the code-class condition containers (`rightCondition`/`rightConditionNoThen`, `selfCondition`, `whenWindowDecidable`, `staticCondition` — including its trap-warning `stance`/`trait`, `groupDefinition`). q19/q21 are already resolved (R28/R30).

## Health note

Three `rebuild/test_spec_load.py` failures (`test_group_resolution`, `test_predicate_class_membership`, `test_loads_all_six_runes`) are pre-existing M1-batch debt — a rune-set mismatch between the test fixtures and the in-progress migrated data, where `qsPea` shows up as an "extra item". They are orthogonal to this description-only work; do not chase them here. Note that `make test` does not cover `rebuild/`, so schema-loading tests must be run directly (`uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal`).
