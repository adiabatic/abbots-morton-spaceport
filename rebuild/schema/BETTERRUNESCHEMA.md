# Better rune-schema documentation — working plan

The plan for slotting accessible documentation into `rune.schema.json` (the schema that governs `glyph_data/runes/*.yaml`, e.g. `qsMay.yaml`). Workflow: we interview, and the moment a description is settled we write it **straight into `rune.schema.json`** as that key's `description`. This file is the task tracker — the walk, the field inventory, and the decision log (the call and its why, not the hover text). It is not itself shipped documentation.

## How we're working

- One question at a time. Anything the codebase can answer, we answer by reading the codebase — not by asking. The interview is reserved for decisions only the owner can make: tone, audience, naming, taste, and where prose lives.
- Confirmed process: **every** open question is asked individually, including the low-stakes "how much detail / how to phrase it" calls — no batching, even where there's an obvious lean.
- Each decision is written straight into the schema as the key's `description`, logged in the tracker below (decision + why), then committed, before moving on.
- Guided by [Diátaxis](https://diataxis.fr/): a schema `description` is fundamentally _reference_ (austere, lookup-oriented), but this project wants it _understandable_, which pulls in some _explanation_. The decisions below resolve that tension deliberately rather than by accident.

## Decisions

### D1 — Audience and the reference/explanation split

**The sole consumer of this documentation is the owner (Nathan).** There is no third-party reader to calibrate for. So:

- We may freely assume fluency in the project's own vocabulary — ·Letter names, `qsName` families, _stance_, _ductus_, _ink_, _trait_, _half_/_alt_, _anchor_, _seam_. We do **not** re-teach font internals the owner already knows.
- We **do** explain the schema-specific machinery the owner does _not_ carry in their head (what an `unlock` does, what `withdrawal: safe` promises, what `ok`/`split` on an `extend` mean, etc.).
- **Austere reference goes up front; the "why" must be right there too — not buried.** The owner is explicitly unwilling to dig into `model.py` docstrings or the M1 plan to recover intent. So the "why" lives where the eyes already are, not one hop away.

Concretely, each `description` string is **two beats in one string**: a terse lead that says what the thing _is_ (the austere reference), immediately followed by the _why/how_ in the same string so both surface together. No separate explanation document the owner would have to go open. Diátaxis purity yields to convenience here because there is exactly one reader and they value "handy" over "clean."

### D2 — Delivery surface: editor-hover tooltips

The descriptions are read as **editor hover tooltips in VS Code specifically** while authoring a rune YAML (the Red Hat YAML extension resolves `$schema` → `rune.schema.json` and pops the matching key's `description`). Targeting one editor sharpens the budget:

- **Markdown renders — use it.** VS Code renders the `description` as Markdown in the hover, so backticks, **bold**, and bullet lists all display properly. No need to write for a plain-text fallback. Still keep it tight: a hover is a small floating box, so aim for a lead sentence plus the why, maybe a short bullet list — not a wall.
- **Tight and self-contained.** Each description stands on its own without a scroll or click-through.
- **Per-key, not per-region.** The hover is keyed to whatever the cursor is on, so every documented key carries its own complete answer; we don't lean on a neighboring key's description for context.

### Interview-style learning (how to ask the owner)

The owner is, by design, not steeped in the schema's internal machinery — that is the whole reason this documentation exists. So interview questions must **not** themselves lean on unexplained concepts. When the owner's judgment is needed, anchor the question on something they already know (a join they author by hand, a ·X·Y outcome, a term from `CLAUDE.md`'s "How to do simple changes") and, where possible, have them **react to a real drafted description** rather than answer an abstract meta-question. Show, don't quiz.

Corollary (learned at q11): **plain beats precise-but-dense.** For an abstract field, three dead-simple sentences land where one technically-exact, term-stacked sentence reads as word salad. When a draft piles up jargon (`off-convention`, `binding mechanisms`, `opt out`, `suppress the check`), cut it to how you'd say it out loud.

### D3 — Every description ends with a worked example

Each description closes with **one real ·X·Y example pulled from an actual rune**. The owner picked this by reacting to two real drafts of the `extend` hover; the example-bearing one won. Maximal concreteness beats leanness here, since the reader values "handy" and there is only one reader to please.

This fixes a reusable template, validated by the winning draft:

1. **Lead** — one sentence: what the thing _is_ and _when you'd reach for it_, in plain terms (no internal machinery).
2. **Mechanics** — a short line naming the sub-keys the author actually sets (e.g. "names the side (`entry`/`exit`), how many pixels (`by:`), and when (`when:`)").
3. **Example** — `Example: …` a single concrete ·X·Y outcome from a named rune.

Worked examples must be kept honest against the runes as they change; the example is chosen to be the most illustrative real instance, and updated if that rune's behavior moves. (See the deferred note on auto-checking examples.)

### D4 — Inline, no separate guide; cross-cutting prose rides the nearest container's hover

The schema-understanding survey recommended a two-tier split: terse reference inline, plus a separate `doc/rune-schema-guide.md` companion holding all the teaching. **We are not doing that** — it contradicts D1, where the owner ruled out opening a separate doc to recover intent. Everything the owner needs lives in the schema `description` strings, surfaced in VS Code hovers.

The one real problem that split was solving — cross-cutting concepts (the rune → stance → surface mental model; the left-settled / right-raw condition symmetry; the reserved-token history) don't belong on any single leaf key — is handled without a second file: **cross-cutting explanation rides the nearest container key's `description`.** Hover `when` and you get the left/right symmetry; hover a child like `leftCondition.joined_at` and you get the specific fact plus a one-clause pointer up to `when`. The root schema `description` and the `stances` / `surface` / `policy` container descriptions carry the orientation. No file to open; every concept is one hover away from where it's used.

`BETTERRUNESCHEMA.md` (this file) is the task tracker: the walk, the field inventory, and the decision log. The hover **text** lives in `rune.schema.json`, written as each decision lands; this file records only the decision and its why. It is not itself shipped.

## Field inventory

Built from the schema-understanding survey and corroborated against `model.py` (read directly). Two of the six regional surveyors failed to return structured data — policy riders and the Diátaxis/house-style pass — so Region 5 below and the Diátaxis framing were reconstructed from the schema and `model.py` and spot-checked by hand. **Class** is `code` (meaning fixed by the pipeline — documented from the code, no interview needed) or `intent` (needs the owner's judgment about use, naming, or audience — these feed the interview queue).

### Region 1 — top-level skeleton and stance identity

| Path | Plain meaning | Provenance | Example | Class |
| --- | --- | --- | --- | --- |
| root description | One rune per file: a closed ductus, stances carrying full join surfaces, and policy riders. Stance identity is the structured tuple; generated display names never appear in authored data. | schema lines 4–5 | — | code |
| `$.rune` | PostScript family name for this letter, e.g. `qsMay`. Pattern `qs[A-Z][A-Za-z]*`, optionally a ligature like `qsTea_qsOy`. | line 10; `$defs/familyName` line 39; `Rune.name` model.py 199 | `rune: qsMay` | code |
| `$.codepoint` | Unicode code point (integer) for the raw cmap glyph. Mutually exclusive with `sequence`. | lines 11, 34–37; model.py 200 | `codepoint: 0xE665` | code |
| `$.sequence` | Ligatures only: the two-or-more component families. Mutually exclusive with `codepoint`. | lines 12, 34–37; model.py 201 | `sequence: [qsTea, qsOy]` | code |
| `$.ductus` | Map from way name to a stroke-narrative string. Documentation, not executable. | schema lines 13–16; model.py 202; CLAUDE.md §Ductus | qsMay `loop: '…'` | code |
| `$.notes` | Free-form text for design decisions and join _constraints_ (distinct from ductus, which is how-to-draw). | line 24; model.py 203 | qsTea_qsOy "Entryless by declaration…" | code |
| `$.mono` | The monospace reference drawing (`$defs/drawing`): a bitmap and optional `y_offset`. | line 25; `$defs/drawing` 59–66; model.py 204 | qsDay mono with `y_offset: -3` | code |
| `$.mono.bitmap` / `$defs/drawing.bitmap` | Array of row strings top-to-bottom; `#` = ink, space = blank. Bare trailing `#` mark the y=5 and y=0 rows. | line 64; geometry `_grid` | 9 rows for deep ·May | code |
| `$.mono.y_offset` / `$defs/drawing.y_offset` | Vertical pixel shift; negative pushes the bitmap down. Deep letters use `-3`; non-deep omit it (defaults 0). | line 65; CLAUDE.md "deep letters… y_offset of -3" | `y_offset: -3` | code |
| `$.stances` | Map of stance name → stance object. Each is a contextual shape the letter can take. | lines 26–30; model.py 205 | `{full: {…}, half: {…}}` | code |
| `$defs/stance` | One stance: required `way` and `bitmap`, optional `traits`, `y_offset`, named `bitmaps`, and `surface`. | lines 68–84; model.py Stance 155–162 | — | code |
| `$.stances[s].way` | The pen-motion id; must match a `ductus` key. Several stances may share a way. | line 73; `$defs/motionName` | `way: loop` | code |
| `$.stances[s].traits` | Array of design flags, currently only `half` (shortened to x-height) and `alt` (alternate form). Empty if neither. | line 74; model.py 159 | `traits: [half]`; `traits: [alt]` | code |
| `$.stances[s].bitmap` | This stance's own pixel grid. | line 75 | qsMay loop bitmap | code |
| `$.stances[s].y_offset` | Per-stance vertical shift; overrides mono when present. | line 76 | `y_offset: -3` | code |
| `$.stances[s].bitmaps` | Map of named alternate drawings (e.g. `pulled-back`) used by join bindings and withdrawals. | lines 77–81; model.py 161 | `{pulled-back: {…}}` | code |
| `$.stances[s].surface` | This stance's join surfaces — see Regions 2 and 3. | line 82; `$defs/surface` 85–111 | — | code |

### Region 2 — surface entry/exit rows

| Path | Plain meaning | Provenance | Example | Class |
| --- | --- | --- | --- | --- |
| `$defs/surface.entries` | Map height → entry row. One entry per height; defines where/how a predecessor joins in on the left. | lines 89–92; surface.py `effective_rows`; settle.py | qsMay baseline + x-height entries | code |
| `$defs/surface.exits` | Map height → exit row. One exit per height; defines how this stance reaches the successor on the right. | lines 94–97; surface.py | qsMay x-height exit | code |
| `$defs/entryRow.x` | Entry anchor x. Convention: leftmost ink column at that height's row. | line 118; surface.py `resolve_cell` | `x: 0` | code |
| `$defs/entryRow.stroke` | Stroke direction at the entry row (horizontal/vertical/diagonal); filters which left families may join. | line 119; settle.py `cond_matches_left` | `stroke: horizontal` | code |
| `$defs/entryRow.from` | List of left conditions restricting which predecessors may join here; absent = unrestricted. | line 120; settle.py | qsMay x-height `from` list | code |
| `$defs/entryRow.joined` | Name of a sibling bitmap used when this entry is live (the redraw that accommodates the join). | line 121; surface.py `resolve_cell` | `joined: pulled-back` | code |
| `$defs/entryRow.joined_x` | Override entry x that applies only while the `joined` bitmap is active. | line 122; surface.py | `joined_x: 2` (qsMay grounded-loop) | code |
| `$defs/entryRow.stub` | Phantom connector column(s) that appear/vanish at this row — see stub fields below. | line 123 | qsPea x-height entry stub | code |
| `$defs/entryRow.selectable` | Boolean (default true). False = a GPOS-only anchor that settlement never picks as a join, kept for cursive-positioning parity. | line 124; surface.py:68/199/279 | qsTea `top: {x: 0, selectable: false}` (the old `entry_curs_only [0, 8]`) | intent |
| `$defs/entryRow.x_off_convention` | `true` opts this entry x out of the leftmost-ink convention check; suppresses the warning only, does not move the anchor. | line 125; surface.py `check_anchor_conventions` | unused in current corpus | intent |
| `$defs/exitRow.x` | Exit anchor x. Convention: one column right of the rightmost ink at that height. | line 133; surface.py | `x: 5` | code |
| `$defs/exitRow.stroke` | Stroke direction at the exit row; filters which right families may receive it. | line 134; settle.py `cond_matches_right` | `stroke: horizontal` | code |
| `$defs/exitRow.toward` | List of right conditions restricting which successors may receive this exit; absent = unrestricted. | line 135; settle.py | qsUtter baseline exit `toward` | code |
| `$defs/exitRow.withdrawal` | What to draw when the exit is declined mid-word: a sibling bitmap name, or `safe` (base already correct — compiler verifies no reaching ink). | line 136; `$defs/withdrawalBinding` 142–147; surface.py | `withdrawal: safe`; `withdrawal: pulled-back` | code |
| `$defs/exitRow.stub` | Same as entry stub, exit side. | line 137 | qsPea x-height exit stub | code |
| `$defs/exitRow.ink_y` | Fallback row y to scan for ink when the declared exit height has no ink. Legacy convention. | line 138; geometry.py `seam_gap` | `ink_y: 6` (qsPea half) | intent |
| `$defs/exitRow.x_off_convention` | `true` opts this exit x out of the max-ink+1 convention check. Validation opt-out only. | line 139 | unused in current corpus | intent |
| `$defs/withdrawalBinding` | Either `safe` or a `motionName` naming a sibling bitmap. | lines 142–147; spec_load.py validates | — | code |
| `$defs/stub.cols` | Column indices where stub ink appears/disappears. | line 153; geometry.py `_apply_stub` | `cols: [0]` | code |
| `$defs/stub.inks_when` | The join state in which the stub columns are **inked** — a connector nub that blinks on with the seam. `joined` = blank in the base drawing, inks when a neighbor joins this side; `withdrawn` = the reverse, ink that retracts on joining (the qsGay case, unused in the corpus). Renamed from `when` (q13) to read in the positive: the field now names the _inked_ state, not the absent one. | model.py:70 Stub; geometry.py:201–206 | qsPea `stub: {cols: [0], inks_when: joined}` | intent |
| `$defs/height` | The four anchor heights: baseline (y0), x-height (y5), y6 (y6), top (y8). | line 53; geometry `HEIGHT_Y` | — | code |
| `$defs/strokeOrientation` | horizontal / vertical / diagonal. | line 56; settle.py | — | code |

### Region 3 — surface pairings, cells, unlocks, require

| Path | Plain meaning | Provenance | Example | Class |
| --- | --- | --- | --- | --- |
| `$defs/surface.pairings` | Two-sided join constraints: `never` (forbidden entry/exit pairs) or `only` (a complete whitelist). At least one must be present; `only` wins if both appear. | lines 99–106; settle.py `_pairing_allowed` | qsIt `only` of 7 pairs | code |
| `$defs/pairing` | One `{entry, exit}` height pair from `heightOrNone`. | lines 157–164; surface.py `_pairing` | `{entry: baseline, exit: baseline}` | code |
| `$defs/surface.cells` | Explicit per-state bitmap overrides: when a given entry/exit state is live, render this named bitmap instead of composing side bindings. | line 108; surface.py `resolve_cell` | qsMay `{entry: x-height, exit: x-height-withdrawn, bitmap: pulled-back}` | code |
| `$defs/cellBinding.entry` / `.exit` | The cell's state token, a `cellSide` value (a height, a `*-withdrawn` height, or `none`). | lines 171–172; surface.py `_matches_state` | `x-height-withdrawn` | code |
| `$defs/cellBinding.bitmap` | Name of the sibling bitmap (key under stance `bitmaps`) to render this cell with. | line 173; geometry `realize` | `pulled-back` | code |
| `$defs/cellBinding.entry_x` / `.exit_x` | Optional per-cell anchor x overrides. | lines 174–175; surface.py | rare | code |
| `$defs/surface.unlocks` | Rows/pairings that become available only when a stylistic-set feature is on (and any `when` matches). Lets users toggle otherwise-forbidden joins. | line 109; surface.py `effective_rows`, settle.py `_active_pairing_unlocks` | qsIt ss04 baseline-baseline unlocks | code |
| `$defs/unlock.feature` | Required `ssNN` feature tag that activates this unlock. | line 183; `$defs/featureTag` | `ss04` | code |
| `$defs/unlock.entry` / `.exit` / `.pairing` | Exactly one of these: the height row or the pairing the feature unlocks. | lines 184–193 | qsTea `{entry: x-height, feature: ss02}` | code |
| `$defs/unlock.when` | Optional context gate (left/right/self/word/feature) on the unlock. | line 187; settle.py | qsIt unlock `when: {left:…, right:…}` | code |
| `$defs/surface.require` | Array of `entry`/`exit`: a stance only settles if the named side is live. Excludes the none-on-that-side cells. For join-born stances. | line 110; settle.py `candidates` | `require: []` (typical) | code |
| `$defs/heightOrNone` | The four heights plus `none`. | line 54 | — | code |
| `$defs/cellSide` | The five `heightOrNone` values plus `*-withdrawn` variants (anchor stays, stroke retracts). | line 55; surface.py `_matches_state` | `x-height-withdrawn` | code |
| `$defs/featureTag` | A `ssNN` stylistic-set id, regex `^ss[0-9]{2}$`. | line 58; emit_gsub.py | `ss04` | code |

### Region 4 — the when/condition grammar

| Path | Plain meaning | Provenance | Example | Class |
| --- | --- | --- | --- | --- |
| `$defs/when` | Groups all constraints on one join decision: optional `left`, `right`, `self`, `word`, `feature`. All present axes must hold. | lines 339–349; settle.py `when_matches` | `{left:{…}, right:{family:[qsDay,qsUtter]}}` | code |
| `$defs/when.word` | Word position initial/medial/final/isolated, derived from run-splitting boundaries (space, ZWNJ; the namer-dot does not split). | line 347; settle.py `word_position` | `word: initial` | code |
| `$defs/when.feature` | `ssNN` feature that must be active for the when to match. | line 348; settle.py | `feature: ss03` | code |
| `$defs/whenWindowDecidable` | A `when` that forbids `then:` on its right side; used by refuse/require/resolve.at so the decision needs no peek past the next position. | lines 351–361; spec_load.py `_lint_refuse_window_rule` | qsMay refuse `when` | code |
| `$defs/leftCondition` | Constrains the (already-settled) left neighbor: `family`, `class`, `stance`, `joined_at`, `stroke`, `is`, `except`. | lines 363–375; settle.py `cond_matches_left` | `{family:[qsDay_qsUtter], joined_at: x-height}` | code |
| `…leftCondition.family` | Left family name or list. | line 368 | `{family:[qsTea,qsDay]}` | code |
| `…leftCondition.class` | Named predicate class / rune-local group (from `policy.groups`). | line 369; settle.py `_members` | `class: halves-that-exit-at-x-height` | code |
| `…leftCondition.stance` | The settled variant the left neighbor took (left-only — only the left is settled). | line 370 | `{family: qsDay, stance: half}` | code |
| `…leftCondition.joined_at` | The seam height the left neighbor joined at (left-only). | line 371; settle.py | `joined_at: x-height` | code |
| `…leftCondition.stroke` | Left neighbor's exit stroke orientation. | line 372 | — | code |
| `…leftCondition.is` | Boundary-token type on the left (boundary/space/zwnj/namer-dot). | line 373 | `{is: boundary}` | code |
| `…leftCondition.except` | Static-condition carve-outs; if any matches, the condition fails. | line 374; settle.py | `{except:[{family:qsDay}]}` | code |
| `$defs/rightCondition` | Constrains the _raw_ right neighbor (not yet settled): `family`, `class`, `stroke`, `is`, `except`, plus optional `then`. No `stance`/`joined_at`. | lines 377–388; settle.py `cond_matches_right` | `{family:qsIt, then:{family:qsMay}}` | code |
| `…rightCondition.then` | One-level lookahead at the position after next; only the `rightConditionNoThen` axes. For taste (prefer) coordination of two seams. | line 387; settle.py | qsIt prefer `then` | code |
| `$defs/rightConditionNoThen` | Same right axes minus `then`; used on refuse/require and exit `toward` scopes where lookahead is disallowed. | lines 390–400 | qsMay refuse right cond | code |
| `$defs/selfCondition` | Current letter's own seam state: `entry: live\|none`, `exit: live\|none`. | lines 402–410; settle.py `when_matches` | `{entry: none}` | code |
| `$defs/staticCondition` | Simple selector (family/class/stance/trait) used in group definitions and `except`. No joined_at/stroke/is. | lines 328–337 | `{family:qsZoo, trait:half}` | code |
| `$defs/boundaryValue` | boundary / space / zwnj / namer-dot — the `is:` token types; space and zwnj split runs, namer-dot does not. | line 57; settle.py | `is: boundary` | code |
| `$defs/motionName` | Way/stance id: `^[a-z][a-z0-9-]*$`, and must NOT contain `before`, `after`, `noentry`, `noexit`, `nonjoining`, or `ss[0-9]`. Names describe the motion, not the neighbors. | lines 46–51; spec_load.py `_lint_identifiers` | `loop`, `grounded-loop`, `bar` | code |
| `$defs/familyName` | `qsX` or underscore-joined ligature `qsX_qsY`. | line 39 | `qsTea_qsOy` | code |
| `$defs/familyOrList` | A single family or a non-empty list of families. | lines 40–45 | — | code |

### Region 5 — policy riders (the settlement-steering layer)

| Path | Plain meaning | Provenance | Example | Class |
| --- | --- | --- | --- | --- |
| `$.policy` | Container for the riders that steer settlement: `order`, `refuse`, `prefer`, `extend`, `contract`, `resolve`, `groups`. | lines 32, 195–210; model.py Policy 186–194 | — | code |
| `$.policy.order` | Stance preference order; `order[0]` is the default/isolated stance. | line 199; model.py 188; `Rune.default_stance` | `order: [loop, grounded-loop]` | code |
| `$.policy.refuse` | Records forbidding a `(stance, entry, exit)` combination under a window-decidable `when`. Optional `why`. | lines 200, 212–222; settle.py | qsMay refuse grounded-loop baseline before qsTea/… | code |
| `$.policy.prefer` | Tie-breakers: prefer one cell pattern `over` another, `mode` `yields-to-joins` (default) or `absolute`. Absolute records must give a `why` (schema enforces). | lines 201, 224–238; settle.py prefer stages | qsMay `{cell:{exit:baseline}, when:…, why:…}` | code |
| `…preferRecord.cell` / `.over` | The `cellPattern` (entry/exit, ≥1 key) that is preferred, and optionally the one it beats. | lines 230–231, 239–247 | `cell:{exit:baseline}` | code |
| `…preferRecord.mode` | `yields-to-joins` (defer if a join improves) or `absolute` (always wins; requires `why`). | lines 232, 236–237; settle.py | implicit yielding in qsMay | code |
| `$.policy.extend` | Widen connector ink by `by` pixels at one of `entry`/`exit` under `when`. Optional `ok`/`split` acceptance guards and `bind`. | lines 202, 248–266; model.py PolicyRecord; settle.py `ext` adjustments | qsMay loop exit `by:1`, `ok:[1,1]` | code |
| `…extendRecord.by` | Pixel count to widen (≥1). | line 256 | `by: 1` | code |
| `…extendRecord.ok` / `.split` | Two-int acceptance-window guards on the extension (settlement-acceptance bound; rare). | lines 257–258; model.py 178,181 | `ok: [1, 1]` (qsMay) | intent |
| `…extendRecord.bind` | Substitute a named sibling bitmap instead of arithmetic widening. | line 259 | — | code |
| `$.policy.contract` | Narrow a connector: `by` N pixels, `trim` N receiver-side pixels, or `bind` a sibling bitmap, at one of entry/exit under `when`. | lines 203, 268–296; settle.py `con`/`trim`/`bind` | qsMay loop entry `bind: pulled-back-stubless` | code |
| `…contractRecord.by` / `.trim` / `.bind` | The three contract operations (at least one required): pixel narrow / receiver-side ink blanking with anchor kept / named-bitmap substitution. | lines 276–296; model.py | `trim`, `by`, `bind` | code |
| `$.policy.resolve` | Hand-authored ambiguity resolution and migration tracking. `pick` + `why` required; optional `against`, `at`, `when`, `migrated`. Currently `[]` everywhere. | lines 204, 298–317; settle.py | `resolve: []` | code |
| `…resolveRecord.against` | The other rune (and optional id) the resolution is decided against. | lines 303–311 | — | code |
| `…resolveRecord.at` / `.when` | Window-decidable / general context for the resolution. | lines 312–313 | — | code |
| `…resolveRecord.pick` / `.migrated` | The chosen outcome object, and an optional migration-provenance string. | lines 314, 316 | — | code |
| `$.policy.groups` | Rune-local named predicate classes (union/minus of static conditions), referenced via `class:` in conditions. | lines 205–209, 319–337; model.py 194 | qsIt `utter-pass-through-vetoes` | code |
| `$defs/groupDefinition` | `union` and/or `minus` lists of static conditions defining a class's membership. | lines 319–327 | `{union:[{family:qsZoo, trait:half}, …]}` | code |
| `$defs/cellPattern` | An `{entry?, exit?}` pattern (≥1 key) used by prefer `cell`/`over`. | lines 239–247 | `{exit: baseline}` | code |

## Agenda — walking qsPea.yaml top to bottom

The interview follows **qsPea.yaml in document order**: we walk every key from the top of the file down, one at a time, so each concept arrives in reading order instead of by region — and so the plain "code" keys that the region pass skipped (`codepoint`, `way`, entry `x`/`stroke`, `require`, …) get a hover too. For each key we explain it, draft the hover, record it here, and commit. The earlier region/q-numbered work is preserved below as the resolved-question log and the open-question leans; the walk points into them by q-number.

Status keys: **done** — hover locked, see the q-entry · **open** — needs an owner decision · **code** — no owner judgment needed, but we still walk it and draft a hover straight from the code/inventory, and the owner reacts.

"Lean" in the open entries below is my recommendation, there to react to rather than start cold.

### The walk

| #  | Key (document order)            | Concept                                                           | Status                            |
| -- | ------------------------------- | ----------------------------------------------------------------- | --------------------------------- |
| 1  | `rune`                          | family name + the generated display-name scheme                   | done q05                          |
| 2  | `codepoint`                     | raw cmap glyph id (vs `sequence` for ligatures)                   | done R2 (sequence q06)            |
| 3  | `ductus` (full/half)            | stroke narrative; a way and how it's drawn                        | done R3                           |
| 4  | `mono` / `bitmap`               | monospace reference drawing; row-string + `#` row markers         | code                              |
| 5  | `mono.y_offset`                 | vertical shift; Deep letters -3                                   | done q08                          |
| 6  | `stances.full.way`              | motion id, must match a `ductus` key                              | code                              |
| 7  | `stances.full.traits`           | the closed `half`/`alt` set                                       | done q09                          |
| 8  | `stances.full.bitmap`           | this stance's own pixel grid                                      | code                              |
| 9  | `surface` / `entries` / `exits` | the join-surface containers                                       | code                              |
| 10 | entryRow `x`                    | entry anchor x = leftmost ink at the row                          | code                              |
| 11 | entryRow `stroke`               | entry stroke orientation; filters which families may join         | code                              |
| 12 | entryRow `stub`                 | connector nub (now `inks_when`)                                   | done q13                          |
| 13 | entryRow `from`                 | entry guest list + the left-settled/right-raw split + `joined_at` | open **NEXT** (q19, q21)          |
| 14 | exitRow `x` / `stroke`          | exit anchor x = max-ink+1; exit stroke                            | code                              |
| 15 | exitRow `withdrawal`            | what to draw when the exit is declined; `safe` vs a bitmap name   | open                              |
| 16 | `pairings`                      | which entry/exit combos this stance allows                        | open q16                          |
| 17 | `cells`                         | per-state bitmap overrides; the term "cell"; `-withdrawn`         | open q14, q15                     |
| 18 | `unlocks`                       | feature-gated extra joins                                         | open q17, q18                     |
| 19 | `require`                       | stance settles only if a named side is live                       | code                              |
| 20 | `stances.half` / `bitmaps`      | named alternate drawings (e.g. `half-dips-both-sides`)            | code                              |
| 21 | exitRow `toward`                | exit guest list (raw right neighbor)                              | open (pairs with `from`)          |
| 22 | exitRow `ink_y`                 | fallback ink row when the exit's own row is blank                 | done q12                          |
| 23 | `policy.order`                  | stance preference; `order[0]` is the isolated default             | code                              |
| 24 | `policy.refuse` (+ `why`)       | forbid a (stance, entry, exit) under a window; the `why` bar      | open + q25                        |
| 25 | `policy.prefer`                 | tie-breakers (qsPea has none)                                     | open                              |
| 26 | `policy.extend`                 | widen connector ink; `by` / `when` / `class` / `joined_at`        | done template (D3); open ok/split |
| 27 | `policy.contract`               | narrow a connector (qsPea has none)                               | code                              |
| 28 | `policy.resolve`                | hand-authored ambiguity (none anywhere yet)                       | code                              |
| 29 | `policy.groups`                 | rune-local predicate classes (qsPea has none)                     | code                              |

Notes: `selectable` / `x_off_convention` (resolved via qsTea, q10/q11) don't appear in qsPea, so they aren't walk rows here — we'll re-meet them on a rune that uses them. The when-grammar decisions q19–q23 (mental model, `then`, `except`, reserved tokens, namer-dot/word position) surface first at **`from`** (row 13) and recur at policy `when`; migration-bridging q24 and the `why`-bar q25 surface at `policy.refuse` (row 24). qsPea's only real `cells` and `toward` live on the **half** stance, so rows 17 and 21 get their worked examples there.

## Decision log

Source of truth for hover **text** is `rune.schema.json` — each locked description is written onto its key there. This log keeps the call and its why so a future reader knows it was deliberate; "→ schema (`key`)" points at where the text lives. Calibration so far: terse for concepts the owner uses daily (q06, R2, R3); fuller only for machinery they don't carry, and plain beats precise-but-dense (q11).

### Settled by D1–D4

- **q01 — audience.** D1: sole reader is the owner; assume project fluency, explain only schema-specific machinery.
- **q02 — doc architecture.** D1 + D4: inline, no separate guide; cross-cutting prose rides container hovers.
- **q03 — explanation doc.** D4: no standalone guide.
- **q04 — tone.** D3: plain what-it-is, sub-keys named, then a worked example.

### Resolved (text in schema)

- **q05 — `rune` display-name scheme.** Document the full generated naming scheme on the `rune` hover so a name reads back to what you authored (debugging FEA/glyph tables); generated only, never written. → schema (`rune`).
- **q06 — `sequence`.** Assume ligature knowledge; mechanics only. → schema (`sequence`).
- **q07 — `unrealized` removed.** The `{unrealized: true}` ductus form earned nothing (one dead data use), so it was deleted from the schema, `model.py`, `spec_load.py`, `qsMay.yaml`, `fixtures.py`, and `test_spec_load.py`; `ductus` values are strings only. Historical mentions remain in `doc/rebuild-design.md`, `rebuild/M1-*.md`, and `rebuild/recon/m1-families.md`. No hover.
- **q08 — `y_offset`.** Rule plus a one-line why (Deep letters −3). → schema (`drawing.y_offset`).
- **q09 — `traits`.** Closed `half`/`alt` set; anything else lives in named bitmaps or policy groups. → schema (`stance.traits`).
- **q10 — `selectable: false`.** Keep it, framed as a GPOS-parity carry-over — proven byte-identical settlement, only the parity anchors drop, and unlike `unrealized` it is _not_ safe to delete. → schema (`entryRow.selectable`).
- **q11 — `x_off_convention`.** Keep it, plainest wording ("I meant to put it there, skip the warning"; unused in every rune). → schema (entry/exit `x_off_convention`).
- **q12 — `ink_y`.** Neutral fallback-row tool, plain wording. → schema (`exitRow.ink_y`).
- **q13 — `stub.when` → `inks_when`.** Renamed to read in the positive (field names the _inked_ state; values flipped `withdrawn`↔`joined`), executed inline across code, schema, data, fixtures, and tests; behaviorally inert. Also fixed five rebuild/ tests that `make test` (which skips `rebuild/`) never ran. → schema (`stub`, `stub.cols`, `stub.inks_when`).
- **R2 — `codepoint`.** Terse, just the facts (owner knows code points; the cmap-vs-stance why over-explained). → schema (`codepoint`).
- **R3 — `ductus`.** What-it-is + docs-only; the parity lint omitted (you'll meet it if you trip it). → schema (`ductus`).

### Open (leans to react to)

- **q14 — `-withdrawn` visibility (surface/cells).** Explain withdrawn prominently, only in the cells section, or hide it? _Lean: in the cells section, tied to a qsMay example._
- **q15 — the term "cell" (surface).** Keep "cell" (code-aligned, greppable) or use a friendlier prose label? _Lean: keep "cell," define on first use as "one concrete entry-exit join state."_
- **q16 — `pairings` framing (surface).** Mechanical whitelist vs design-first ("the pairs this letter accepts"). _Lean: design-first + one-line mechanics._
- **q17 — ssNN user-facing meaning (surface/unlocks).** Do the stylistic sets (qsIt ss04, qsTea ss02/ss03/ss05) have user-facing purposes/names, or are they internal join toggles? _Lean: capture a one-line purpose per set you intend users to toggle; mark the rest engine-internal._
- **q18 — unlock didactics (surface).** Lead with the simple case then "optionally gated by context," or full gated form up front. _Lean: simple first, one gated qsIt example._
- **q19 — condition mental model (when grammar).** Teach the left-settled / right-raw symmetry as the organizing idea, or state each axis procedurally? _Lean: teach the symmetry on the `when` container hover; leaf keys stay procedural and point up._
- **q20 — `then` framing (when grammar).** Use-case framing (veto vs taste) vs window mechanics, for why `then` is allowed on prefer but banned on refuse/require. _Lean: use case first, mechanics as a note._
- **q21 — `except` framing (when grammar).** Positive carve-out ("all except these") vs logical negation. _Lean: positive carve-out + a brief logical note._
- **q22 — reserved-token history (when grammar/motionName).** Explain why `before`/`after`/`noentry`/… are forbidden in names (old display-name suffixes), or just list them. _Lean: principle inline ("names = the motion, not the neighbors"); history kept terse._
- **q23 — namer-dot / word position (when grammar).** Surface that space and ZWNJ split runs but the namer-dot does not, or keep word position opaque? _Lean: surface it (you author these conditions)._
- **q24 — migration bridging (old quikscript.yaml).** None / brief mapping note / detailed side-by-side from the old `entry_xheight_exit_baseline`-style keys. _Lean: brief mapping note, kept terse._
- **q25 — `why` rationale bar (policy).** Should the `why` description set a bar ("record the design verdict or constraint this rule honors, so a future reader won't delete it as redundant") or just say "free-text rationale"? _Lean: set a bar — the qsMay·May notes show why._
