# Better rune-schema documentation — working plan

This is the plan for adding readable documentation to `rune.schema.json`, the schema for `glyph_data/runes/*.yaml` (for example `qsMay.yaml`). We work by interview, and as soon as a description is settled we write it into `rune.schema.json` as that key’s `description`. This file is the task tracker: it holds the walk state and the open decisions. It is not itself shipped documentation.

## How we’re working

- Ask one question at a time. Answer anything the codebase can answer by reading the codebase. Ask the owner only about what the owner alone can decide: tone, audience, naming, taste, and where prose lives.
- Ask **every** open question on its own, including low-stakes questions about how much detail to give or how to phrase something. Don’t batch questions, even where there is an obvious lean.
- Write each decision into the schema as the key’s `description` and commit it before moving on. The commit message records the decision and the reason.
- The approach follows [Diátaxis](https://diataxis.fr/). A schema `description` is _reference_ material (terse and made for lookup), but this project wants it to be _understandable_, which adds some _explanation_. The decisions below say how the two are balanced.

## Decisions

### D1 — Audience and the reference/explanation split

**The only reader of this documentation is the owner (Nathan).** There is no third-party reader to write for. So:

- Assume fluency in the project’s own vocabulary: ·Letter names, `qsName` families, _stance_, _ductus_, _ink_, _trait_, _half_/_alt_, _anchor_, _seam_. Don’t re-teach font internals the owner already knows.
- **Do** explain the schema-specific mechanisms the owner does _not_ keep in mind: what an `unlock` does, what `withdrawal: safe` promises, what `ok` and `split` mean on an `extend`, and so on.
- **Don’t use “drawing” as a noun in prose** (R39). It reads as an undefined term. Say `bitmap` or use a plain verb. The `drawing` `$def` name and its `$ref`s stay, since they are structure and not prose, and verbs such as “redraws” and “draws” are fine.
- **Put the terse reference first, and put the reason right after it.** The owner does not want to read `model.py` docstrings or the M1 plan to find out why a key exists, so the reason goes where the owner is already looking.

Each `description` string has two parts: a short lead that says what the thing _is_, followed in the same string by why it exists and how it works, so both appear together. There is no separate explanation document for the owner to open. This departs from Diátaxis because there is one reader, who prefers convenience to a clean separation.

### D2 — Delivery surface: editor-hover tooltips

The descriptions are read as **hover tooltips in VS Code** while editing a rune YAML file. The Red Hat YAML extension resolves `$schema` to `rune.schema.json` and shows the matching key’s `description`. Writing for one editor sets these limits:

- **Use Markdown.** VS Code renders the `description` as Markdown in the hover, so backticks, **bold**, and bullet lists display correctly. There is no need to write for a plain-text fallback. Keep it short, because a hover is a small floating box: a lead sentence plus the reason, and perhaps a short bullet list.
- **Keep each description self-contained.** It should need no scrolling or clicking through.
- **Document each key, not each region.** The hover shows whatever key the cursor is on, so every documented key carries its own complete answer and does not rely on a neighboring key’s description.

### D5 — Lead summary, then air

The owner’s instruction for every hover written or reworked:

- **Open with a one-sentence summary, then a `\n\n` paragraph break** before anything else.
- **Use vertical whitespace.** Prefer several short paragraphs to one dense one: “I need my vertical whitespace to keep the fatigue down.”
- **Don’t copy the committed hovers’ style.** An earlier, less capable model drafted much of the existing text, with significant human editing since. Write more plainly, with less jargon the owner doesn’t know, instead of matching that voice. When a committed hover is being edited for another reason, restructure it to this shape.

### D6 — Wording is edited in a drafts file, not picked from options

The owner works in VS Code, where AskUserQuestion previews are a poor place to edit wording. Instead, one hover per round is written to `var/hover-drafts.md` (editable Markdown; a blank line stands for the `\n\n` break; paragraphs are not wrapped) and at the same time into `rune.schema.json`, so the real tooltip appears on hover in a rune file (`.vscode/settings.json` maps the schema onto `glyph_data/runes/*.yaml`). The owner edits the draft directly, usually by pointing at a phrase and asking for plainer words, and then says “land it”. The text then goes into the schema verbatim through the usual steps: JSON validation, updating the walk state, lint, and commit. Use AskUserQuestion only for real choices between alternatives, never for wording. Between rounds the schema holds the unapproved draft, uncommitted, until the owner approves it.

### Interview-style learning (how to ask the owner)

The owner does not know the schema’s internal mechanisms well, which is the reason this documentation exists. So interview questions must not depend on concepts that haven’t been explained. When the owner’s judgment is needed, base the question on something the owner already knows (a join they author by hand, a ·X·Y outcome, a term from the tweak-an-old-font-join skill). Where possible, show a real drafted description for the owner to react to instead of asking an abstract question.

A lesson from q11: **plain is better than precise but dense.** For an abstract field, three simple sentences work where one exact sentence full of terms reads as nonsense. When a draft accumulates jargon (`off-convention`, `binding mechanisms`, `opt out`, `suppress the check`), rewrite it the way you would say it aloud.

### D3 — Every description ends with a worked example

Each description ends with **one real ·X·Y example taken from an actual rune**. The owner chose this after comparing two real drafts of the `extend` hover, and the one with an example won. Concreteness matters more than brevity here, because the one reader values convenience.

The winning draft gives this template:

1. **Lead**: one sentence saying what the thing _is_ and _when you would use it_, in plain terms, without internal mechanisms.
2. **Mechanics**: a short line naming the sub-keys the author sets (for example, “names the side (`entry`/`exit`), how many pixels (`by:`), and when (`when:`)”).
3. **Example**: `Example: …`, one concrete ·X·Y outcome from a named rune.

Keep each example accurate as the runes change. Choose the most illustrative real instance, and update it if that rune’s behavior changes.

### D4 — Inline, no separate guide; cross-cutting prose rides the nearest container’s hover

The alternative was a two-tier split: terse reference inline, and a separate `doc/rune-schema-guide.md` holding the explanations. **We are not doing that**, because it contradicts D1: the owner does not want to open a separate document to find out why. Everything the owner needs is in the schema `description` strings and appears in VS Code hovers.

The problem that split would have solved is that some concepts span many keys and belong on no single leaf key: how a rune, its stances, and its surface relate; the symmetry between left conditions, which read settled state, and right conditions, which read raw input; and the history of the reserved tokens. These go in the `description` of the nearest container key instead. Hovering `when` shows the left/right symmetry. Hovering a child such as `leftCondition.joined_at` shows the specific fact and a short pointer up to `when`. The root schema `description` and the `stances`, `surface`, and `policy` container descriptions give the overview. Every concept is one hover away from where it is used, with no file to open.

## Walk status

Every `$def` and key that has a `description` in `rune.schema.json` is settled. The schema is the record of which ones those are.

Still to draft: the `$defs` that have no `description` yet. They need no owner decision, so draft each one from the code and let the owner react. This command lists them:

```sh
uv run python -c "import json; d = json.load(open('rebuild/schema/rune.schema.json'))['\$defs']; print(sorted(k for k, v in d.items() if 'description' not in v))"
```

## What is next

The walk follows the owner’s source-order rule. Document the next field without a description that appears in a live rune YAML file, in document order across the files, instead of choosing by the Phase-2 row codes. Prefer the leaves the owner will hover while authoring. The root document order is `rune` → `codepoint`/`sequence` → `ductus` → `notes` → `mono` → `stances` → `policy`.

No round is in progress. Next: the remaining `when`-grammar containers and scalars listed under **Walk status**, then the open leans below.

The reasoning behind each hover is in the `git log` of `rune.schema.json`.

### Open (leans to react to)

- **q22 — reserved-token history (when grammar/motionName).** Either explain why `before`/`after`/`noentry`/… are forbidden in names (they were old display-name suffixes), or just list them. _Lean: state the principle inline (“names = the motion, not the neighbors”) and keep the history short._
- **q24 — migration bridging (old quikscript.yaml).** Options: none, a brief mapping note, or a detailed side-by-side comparison with the old `entry_xheight_exit_baseline`-style keys. _Lean: a brief mapping note._
- **Scope of the stance’s `bitmaps` hover.** The hover on the stance’s `bitmaps` property says its names are “wired up elsewhere”, which was written before the scope decision: `joined`, `withdrawal`, and `cells.bitmap` resolve only to names in the stance’s own `bitmaps` map, and not to a sibling stance’s bitmap, the base `bitmap`, or the rune’s `mono`. `exitRow.withdrawal` already says this. Give the `bitmaps` hover the same precision when the walk reaches it.
- **Applying D5 to committed hovers.** New text uses the lead-summary and `\n\n` shape, but most committed hovers don’t. _Lean: restructure a committed hover whenever it is being edited anyway, not all at once._

## Health note

Don’t investigate `rebuild/` suite failures unrelated to the schema here. `make test` does not cover `rebuild/`, so run the schema-loading tests directly (`uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal`) or run `make test-rebuild`.
