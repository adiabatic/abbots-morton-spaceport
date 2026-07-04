# Better rune-schema documentation — working plan

The plan for slotting accessible documentation into `rune.schema.json` (the schema that governs `glyph_data/runes/*.yaml`, e.g. `qsMay.yaml`). Workflow: we interview, and the moment a description is settled we write it **straight into `rune.schema.json`** as that key’s `description`. This file is the task tracker — the live walk state and the open decisions. It is not itself shipped documentation.

## How we’re working

- One question at a time. Anything the codebase can answer, we answer by reading the codebase — not by asking. The interview is reserved for decisions only the owner can make: tone, audience, naming, taste, and where prose lives.
- Confirmed process: **every** open question is asked individually, including the low-stakes “how much detail / how to phrase it” calls — no batching, even where there’s an obvious lean.
- Each decision is written straight into the schema as the key’s `description`, logged in the tracker below (decision + why), then committed, before moving on.
- Guided by [Diátaxis](https://diataxis.fr/): a schema `description` is fundamentally _reference_ (austere, lookup-oriented), but this project wants it _understandable_, which pulls in some _explanation_. The decisions below resolve that tension deliberately rather than by accident.

## Decisions

### D1 — Audience and the reference/explanation split

**The sole consumer of this documentation is the owner (Nathan).** There is no third-party reader to calibrate for. So:

- We may freely assume fluency in the project’s own vocabulary — ·Letter names, `qsName` families, _stance_, _ductus_, _ink_, _trait_, _half_/_alt_, _anchor_, _seam_. We do **not** re-teach font internals the owner already knows.
- We **do** explain the schema-specific machinery the owner does _not_ carry in their head (what an `unlock` does, what `withdrawal: safe` promises, what `ok`/`split` on an `extend` mean, etc.).
- **Banned prose noun: “drawing”** (R39). It reads as an undefined term; say `bitmap` or use a plain verb instead. The `drawing` `$def` name and its `$ref`s stay (structural, not prose); verbs like “redraws”/“draws” are fine.
- **Austere reference goes up front; the “why” must be right there too — not buried.** The owner is explicitly unwilling to dig into `model.py` docstrings or the M1 plan to recover intent. So the “why” lives where the eyes already are, not one hop away.

Concretely, each `description` string is **two beats in one string**: a terse lead that says what the thing _is_ (the austere reference), immediately followed by the _why/how_ in the same string so both surface together. No separate explanation document the owner would have to go open. Diátaxis purity yields to convenience here because there is exactly one reader and they value “handy” over “clean.”

### D2 — Delivery surface: editor-hover tooltips

The descriptions are read as **editor hover tooltips in VS Code specifically** while authoring a rune YAML (the Red Hat YAML extension resolves `$schema` → `rune.schema.json` and pops the matching key’s `description`). Targeting one editor sharpens the budget:

- **Markdown renders — use it.** VS Code renders the `description` as Markdown in the hover, so backticks, **bold**, and bullet lists all display properly. No need to write for a plain-text fallback. Still keep it tight: a hover is a small floating box, so aim for a lead sentence plus the why, maybe a short bullet list — not a wall.
- **Tight and self-contained.** Each description stands on its own without a scroll or click-through.
- **Per-key, not per-region.** The hover is keyed to whatever the cursor is on, so every documented key carries its own complete answer; we don’t lean on a neighboring key’s description for context.

### D5 — Lead summary, then air (set 2026-07-03, mid-R40)

Owner instruction, governing every hover written or reworked from here on:

- **Open with a one-sentence quick summary, then a `\n\n` paragraph break** before anything else.
- **Vertical whitespace is load-bearing.** Prefer several short paragraphs over one dense one — "I need my vertical whitespace to keep the fatigue down."
- **Don't crib the committed hovers' style.** Much of the existing text was drafted by an earlier, less capable model (with significant human editing since); write plainer, with less jargon the owner doesn't personally know, rather than matching the incumbent voice. Restructuring a committed hover to this shape while touching it for other reasons is welcome.

### Interview-style learning (how to ask the owner)

The owner is, by design, not steeped in the schema’s internal machinery — that is the whole reason this documentation exists. So interview questions must **not** themselves lean on unexplained concepts. When the owner’s judgment is needed, anchor the question on something they already know (a join they author by hand, a ·X·Y outcome, a term from `CLAUDE.md`’s “How to do simple changes”) and, where possible, have them **react to a real drafted description** rather than answer an abstract meta-question. Show, don’t quiz.

Corollary (learned at q11): **plain beats precise-but-dense.** For an abstract field, three dead-simple sentences land where one technically-exact, term-stacked sentence reads as word salad. When a draft piles up jargon (`off-convention`, `binding mechanisms`, `opt out`, `suppress the check`), cut it to how you’d say it out loud.

### D3 — Every description ends with a worked example

Each description closes with **one real ·X·Y example pulled from an actual rune**. The owner picked this by reacting to two real drafts of the `extend` hover; the example-bearing one won. Maximal concreteness beats leanness here, since the reader values “handy” and there is only one reader to please.

This fixes a reusable template, validated by the winning draft:

1. **Lead** — one sentence: what the thing _is_ and _when you’d reach for it_, in plain terms (no internal machinery).
2. **Mechanics** — a short line naming the sub-keys the author actually sets (e.g. “names the side (`entry`/`exit`), how many pixels (`by:`), and when (`when:`)”).
3. **Example** — `Example: …` a single concrete ·X·Y outcome from a named rune.

Worked examples must be kept honest against the runes as they change; the example is chosen to be the most illustrative real instance, and updated if that rune’s behavior moves. (See the deferred note on auto-checking examples.)

### D4 — Inline, no separate guide; cross-cutting prose rides the nearest container’s hover

The schema-understanding survey recommended a two-tier split: terse reference inline, plus a separate `doc/rune-schema-guide.md` companion holding all the teaching. **We are not doing that** — it contradicts D1, where the owner ruled out opening a separate doc to recover intent. Everything the owner needs lives in the schema `description` strings, surfaced in VS Code hovers.

The one real problem that split was solving — cross-cutting concepts (the rune → stance → surface mental model; the left-settled / right-raw condition symmetry; the reserved-token history) don’t belong on any single leaf key — is handled without a second file: **cross-cutting explanation rides the nearest container key’s `description`.** Hover `when` and you get the left/right symmetry; hover a child like `leftCondition.joined_at` and you get the specific fact plus a one-clause pointer up to `when`. The root schema `description` and the `stances` / `surface` / `policy` container descriptions carry the orientation. No file to open; every concept is one hover away from where it’s used.

`BETTERRUNESCHEMA.md` (this file) is the task tracker: the live walk state and the open decisions. The hover **text** lives in `rune.schema.json`, written as each decision lands; recover the per-decision call-and-why from that file’s `git log`. It is not itself shipped.

## Walk status

qsPea walk + when-grammar region: **done through R39** — the full qsPea document-order walk (rows 1–29) and the settled Phase-2 when-grammar decisions (`policy`, `when`, `leftCondition`, the condition leaves, `rightCondition.then`, `when.word` — R27–R32) are locked; the hover text lives in `rune.schema.json`.

Still to draft (code-only — no owner decision; draft from the code and let the owner react): `when.feature`, `rightCondition` / `rightConditionNoThen`, `selfCondition`, `whenWindowDecidable` (its `word` should mirror the settled `when.word` wording), `staticCondition`, `groupDefinition`, and the scalar `$defs` (`familyName`, `familyOrList`, `boundaryValue`, the heights, stroke).

Still open (owner decision needed): the `motionName` reserved-token rule (q22) and migration bridging (q24) — both carried in “Open (leans to react to)” below.

## What is next

From here the walk follows the owner’s source-order rule (set 2026-06-19): document the next **bare** field that actually appears in a live rune YAML, in document order across any of the files, rather than picking by the abstract Phase-2 row codes. The **Walk status** above still maps the territory; this pass just reorders which leaf comes next, and biases toward leaves the owner will actually hover while authoring. Root document order is `rune` → `codepoint`/`sequence` → `ductus` → `notes` → `mono` → `stances` → `policy`; the first four were already done, so `notes` was next.

**Paused (2026-06-19) inside `surface`.** `entryRow` and `exitRow` are fully documented. The next bare field is the `pairing` `$def` (`entry`/`exit` under `surface.pairings` never/only), but the documented `pairings` container already explains each side — so the open fork is: document those leaves anyway, or treat them as container-covered (per D4) and skip to `cellBinding` (5 bare leaves: `entry`/`exit`/`bitmap`/`entry_x`/`exit_x`, the cell-grain mirror of `joined`/`joined_x`; real at qsMay.yaml:81, qsPea.yaml:98). Lean: skip to `cellBinding`. This fork was asked and deferred by the owner.

The R1–R39 decision log — the per-decision call-and-why behind every hover — is retired here. The hover **text** it produced lives on each key in `rune.schema.json`; recover the reasoning from that file’s `git log`.

- **R40 — example-honesty sweep, mechanical half.** The verdict-application phase (the pulled-back removal, the qsMay bitmap renames, the ·It self-join ban) staled the worked examples in seven committed hovers — the first live instance of D3's "examples must be kept honest against the runes as they change." The four fixes needing no judgment landed in one pass: `stance.bitmaps` and `entryRow.joined` (·May's `pulled-back` → `pulled-back-stubless`), `surface.require` (·Utter's join-born stance is named `alternate` now; `reaches-way-back` is its `joined` bitmap), and `extendRecord` (·May's quoted family list gained `qsDay_qsUtter` and the record's explicit `ok: [1, 1]`). The judgment half — replacement examples for `surface.cells` (·May's `x-height-withdrawn` cell is gone; heirs are qsUtter's `baseline-withdrawn` pair, qsPea's `half-dips-both-sides`, qsOy's `open-on-the-left`), `exitRow.withdrawal` (no rune names a withdrawal bitmap anymore; all live withdrawals are `safe`), `exitRow.toward` (·Utter.alt's `toward` is gone; live ones are qsPea's half y6/x-height), `rightCondition.then` (qsIt's `prefer` is `[]`; the only live `then` is qsUtter's), and `contractRecord`'s second example (·May's `bind` record is gone; no rune uses `bind`) — is asked one key at a time; see the open list. Minor drift also logged: the `when` example quotes ·Pea's y6 refuse without its `except`, and the `from` example omits `qsDay_qsUtter`. → schema (4 hovers).

- **R41 — `surface.cells` replacement example (R40's first judgment call).** The owner took the two-example treatment (the R24 pattern) with a `\n\n` between them: ·Pea's half joined at the x-height on both sides swapping in `half-dips-both-sides` (the plain override, matching the half ductus prose), then ·Utter's `alternate` entered at the x-height with its baseline exit withdrawn swapping in `reaches-way-back-withdrawn` (the `-withdrawn` demonstration q14 asked for, carried forward from the retired ·May cell). → schema (`surface.cells`).

- **R42 — `exitRow.withdrawal` replacement example.** The owner took the status-plus-cross-reference variant: ·Pea's `safe` stays, then the plain status ("No rune names a bitmap here today — every declared withdrawal is `safe`") with the pointer to where the one real declined-exit redraw lives — ·Utter's `reaches-way-back-withdrawn` riding a `-withdrawn` cell. Verified against surface.py (the withdrawal binding fires when the exit's base ink must come off; a `-withdrawn` cell overrides it). → schema (`exitRow.withdrawal`).

### Open (leans to react to)

- **Stale-example judgment calls (R40's second half).** Still pending: `exitRow.toward`, `rightCondition.then`, and `contractRecord` (its `bind` example); plus the minor `when`/`from` quote drift. One question each. _Lean: qsUtter's ·Tea `then` prefer for `then`; state plainly on `contract` that `bind` currently has no live user._

- **q22 — reserved-token history (when grammar/motionName).** Explain why `before`/`after`/`noentry`/… are forbidden in names (old display-name suffixes), or just list them. _Lean: principle inline (“names = the motion, not the neighbors”); history kept terse._
- **q24 — migration bridging (old quikscript.yaml).** None / brief mapping note / detailed side-by-side from the old `entry_xheight_exit_baseline`-style keys. _Lean: brief mapping note, kept terse._
- **bitmaps `$def` scope-precision follow-up (from R36/R38).** The `bitmaps` `$def` hover still says its names are “wired up elsewhere,” predating R36’s scope decision: `joined` / `withdrawal` / `cells.bitmap` resolve only to the stance’s own `bitmaps` map — not a sibling bitmap, not the base `bitmap`, not the rune’s `mono`. `exitRow.withdrawal` was reworked to say this (R38); give the `bitmaps` `$def` the same scope precision when the walk reaches it.
- **D5 retrofit of committed hovers.** The lead-summary + `\n\n` shape now governs new text; the ~50 already-committed hovers mostly don't have it. _Lean: retrofit opportunistically — restructure a committed hover whenever it's being touched anyway (as the R40 sweep is doing), not as a big-bang pass._

## Health note

Three `rebuild/test_spec_load.py` failures (`test_group_resolution`, `test_predicate_class_membership`, `test_loads_all_six_runes`) are pre-existing M1-batch debt — a rune-set mismatch between the test fixtures and the in-progress migrated data, where `qsPea` shows up as an “extra item”. They are orthogonal to this description-only work; do not chase them here. Note that `make test` does not cover `rebuild/`, so schema-loading tests must be run directly (`uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal`).
