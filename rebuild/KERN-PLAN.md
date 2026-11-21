# Kerning plan (design §12)

This is the design record for the rebuild's kerning: the `kern:` block of a rune file, the stage that resolves it, the GPOS lookup it compiles to, and the editor view of the review app that writes it. Its inputs are design §12 and the kerning section of `doc/core-idea.md`. Nothing here is built; §9 orders the work.

## 1. The rulings this plan is built on

These are the author's decisions, and each one closes a question the old font left open.

- **Kerning moves only pairs that do not join.** A joined pair's length belongs to `extend` and `contract`. The old font runs its Senior kern lookups after cursive attachment and expands a family key over every glyph of the family, so a rule set while looking at `·No.alt | ·Tea.half` also shortens `·No.alt ~b~ ·Tea`.
- **A pair's kerning lives in the leading letter's rune file.** The pair `·A | ·B` is kerned in ·A's file and nowhere else.
- **The most specific record wins, and values never add up.** `by: 0` is a real value that cancels a broader record.
- **The unit an author edits is a facing pair**: one right-hand edge against one left-hand edge (§3).
- **The editor writes each change through to the YAML**, keeps a journal for undo, and holds its writes while a cycle pass reads the runes.
- **The Senior tightening stays in the advance widths.** `tools/build_font.py` trims a pixel from every Quikscript letter's advance, so no record states it.
- **A break at a ZWNJ kerns exactly as the same two shapes kern at any other break.** The old font's rule that tightens ·He before any ZWNJ is not carried over.
- **The editor shows joined pairs and does not edit them.**

## 2. The record

`kern` is a top-level key of a rune file, a sibling of `policy`, and the last key in the file. It is outside `policy` because policy shapes settlement and kerning reads settlement's result.

```yaml
kern:
- {right: {family: qsBay}, by: -2}
- {stance: flipped, right: {family: qsPea}, by: -2}
- {stance: flipped, right: {family: qsTea, stance: half}, by: -1}
- {stance: loop, right: {family: qsGay}, by: -1}
```

`rebuild/schema/rune.schema.json` is the authority on the keys. Their meaning:

| Key                           | Names                                                           | Notes                                                                         |
| ----------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `stance`                      | This rune's stance                                              | Absent means every stance                                                     |
| `entry`                       | How this rune was entered: a height or `none`                   | For a rune whose right-hand edge depends on its entry                         |
| `bitmap`                      | A drawing a `bind:` names, a derived `padded-left-N` among them | For an edge only a bound drawing produces                                     |
| `right.family`                | The follower, one rune or a list                                | A ligature is named explicitly, as in every right-facing list (design §3.4)   |
| `right.class`, `right.except` | A predicate class or rune-local group, and carve-outs           | For a hand-written statement about many followers                             |
| `right.stance`, `right.exit`  | The follower's settled stance and exit state                    | Legal only in a kern record: both sides are settled when kerning is evaluated |
| `right.bitmap`                | A bound drawing of the follower                                 | As `bitmap`                                                                   |
| `by`                          | Whole pixels, signed                                            | Negative tightens                                                             |
| `why`                         | The author's rationale                                          | Written only from a note the author types; AGENTS.md's `why:` rule covers it  |

A record never names a boundary. A space, a text edge, and the namer dot are not runes, and a ZWNJ is invisible to kerning (§5).

**What a record matches.** A record matches a pair of adjacent settled cells when the pair is a break, the left cell is this rune's and fits the record's own-side keys, and the right cell fits `right`. A break is a pair whose left cell has no live exit and whose right cell has no live entry; the join table holds no other kind of break and no join with a declined facing side, and the kern stage asserts both.

**Precedence.** Among the records that match a cell pair, the one whose match set is contained in every other's decides the value, by the extensional order of design §6.2. Two matching records with different values and neither match set inside the other are the error `E-KERN-INCOMPARABLE`, which names both. No value is ever a sum.

**Ligatures.** A ligature stance that declares `outgoing.stance` takes the kern records of the component stance it maps to, those naming that stance and those naming none. Records with `entry` or `bitmap` stay with the component, as incoming behavior and bound drawings do under design § Preserved outgoing strokes. Every record in the ligature's own `kern` block outranks every inherited one. A ligature that opts out with `outgoing: {exception: …}` inherits nothing.

**Stale records.** A record that matches no reachable break is the error `E-KERN-UNMATCHED`. The old font's stale prefix kerns nothing and reports nothing.

## 3. Facing pairs

Most cells of a rune look the same from one side: an entry state changes nothing on the right of most letters, and an exit extension changes nothing on the left. The kern stage groups them.

- A **side** of a rune is a maximal set of its cells that the record grammar can select together and whose facing edge is the same pixel for pixel: for the leading side, cells with no live exit and the same ink-to-advance distance on every row; for the following side, cells with no live entry and the same leftmost ink on every row.
- Each side has a **name**: the coarsest own-side pattern (`family`, then `stance`, then the join state, then `bitmap`) that selects exactly its cells among the rune's cells on that side.
- Cells the grammar cannot tell apart (they differ only in an extension, contraction, or trim amount) are one side even when their edges differ. The coverage table marks such a side, and the editor draws each of its drawings.
- A **facing pair** is a leading side and a following side with at least one reachable break between their cells, in any settlement configuration.

The editor's rows, the coverage table's rows, and the classes of the GPOS lookup are all facing pairs. A record may be broader than one facing pair and never narrower.

## 4. The kern stage

`rebuild/pipeline/kern.py` runs after the tables exist and the cell glyphs are minted. It reads the spec's kern records, the break rows of every `joins-<config>.tsv`, and the realized bitmaps, and writes under `rebuild/out/m1/`:

- **`kern.tsv`**, the coverage table: one row per facing pair, with both side names, the value, the record that decides it (file and key path), and the configurations that reach it. It is diff-stable, so a new stance that inherits a letter-wide value shows up as added rows in a commit's diff.
- **`kern-inventory.json`**, the editor's data: every reachable glyph's ink and advance, the sides, the facing pairs with their member cell pairs, the joined pairs of the same two runes, and for each a witness text with its settled cells and pen positions.
- The class assignment `emit_gpos` compiles (§5).

It raises `E-KERN-UNMATCHED` and `E-KERN-INCOMPARABLE`, and it hands the defect gates each break's kerned offset, so the off-anchor contact check (design §9) overlays a kerned pair where the font draws it and reports an overlap as `E-CONTACT`.

**Kerning stays out of the kernel.** `spec_load` parses `kern` into records that the kernel's spec dump does not carry, and the table stamp's data half (`fingerprint.tables_value`) leaves the key out of each rune's digest. A kern edit therefore reuses the tables on disk, and `run_m1` re-emits GPOS, compiles, and reads the font back without running the crate. The freshness fingerprint carries the parsed `kern` blocks as a component of their own, and the review corpus does not depend on it (§7).

The join table's `kern` column holds no information under this design: the crate drops it, and `table.py` and `rebuild/review/tablediff.py` with it.

## 5. Compilation

`emit_gpos` writes one class-based pair lookup (PairPos format 2) under the `kern` feature. Its first-glyph classes are the leading sides with at least one nonzero row and its second-glyph classes are the following sides; a value is `by` pixels of x-advance on the first glyph.

- **The lookup cannot move a join.** A glyph with a live exit is in no first-glyph class and a glyph with a live entry is in no second-glyph class. Read-back checks this on the written font: no glyph in the lookup's coverage carries an exit anchor in any `curs` lookup, and no glyph in a nonzero second class carries an entry anchor.
- **A ZWNJ needs no rule.** HarfBuzz skips a ZWNJ when it matches GPOS input, so the pair lookup kerns across one. The conformance sweep checks this on every swept text that holds a ZWNJ, and the CoreText smoke set gains a ZWNJ case. A shaper that does not read through gets contextual rows (GPOS type 8) for the same values, which the old font's ZWNJ kern shows work.
- **Read-back** expects GPOS to register `curs` and `kern`, and compares the lookup's classes and values with the stage's assignment.

## 6. The editor

The editor is the `#view=kern` view of the review app. `rebuild/review/README.md` describes it as its user sees it once it exists; this section is the contract.

**It draws pixels and shapes nothing.** The client loads `kern-inventory.json` and draws each witness as squares at the inventory's pen positions. A kern is a whole-pixel shift of everything after the junction, so a keypress redraws from data the client already holds, with no shaping and no request. Browser shaping cannot serve here: a span or a margin inside a run breaks the contextual substitutions, and the browser cannot say which glyphs it drew.

**Layout.** A grid of leading rune by following rune, each cell drawing the pair's most common facing pair and its value, with a count on a cell that has several and a mark on a cell whose value the built font lacks. Beside it, the detail panel of the focused cell: every facing pair of the two runes in a witness, the joined pairs of the same runes drawn and locked, the closest ink across each junction, and the Manual's words that hold the pair. A text box settles typed text through the kernel on the server; it is the one part that needs a request.

**Scope of an edit.** An edit made on the two letters sets the record that names only the two families. An edit made on one facing pair sets the record whose two sides are that pair's names. A record equal to what it would inherit is removed. The editor never rewrites a record that carries a `class`, a family list, or an `except`: it writes a narrower record beside it and shows which record each value comes from.

**Routes**, in a server-only module that `fingerprint.REVIEW_NON_BUILD_MODULES` lists:

- `GET /kern` returns every facing pair's value, its deciding record, and a digest of each rune file's `kern` block.
- `POST /kern` takes a delta of `{leading side, following side or family, by, base}` sets. Under the store lock's discipline the server appends the delta to `var/kern/journal.ndjson`, builds the new blocks, runs the kern loader and stage over the result, and only then replaces the files. A delta whose `base` no longer matches the file, or whose result the stage refuses, changes nothing and comes back as a conflict. Undo replays the journal's inverse.
- While a cycle pass holds the build, the server journals a delta as pending and applies it when the pass ends; the view shows how many wait.

**The block writer** regenerates a rune's whole `kern` block in canonical order (followers by code point, a ligature after its lead, a broader record before a narrower one, one flow-style record per line) and replaces the text from the `kern:` line to the end of the file. Every byte before that line is untouched by construction, and a test holds it to that. `tools/reflow_yaml.py` leaves the writer's output as it is. A comment inside the block does not survive a write; `why` is where a rationale goes.

**Keys.** `static/keyboard.js` holds the view's map and its test pins it.

## 7. Gates

| Gate or check                     | Under kerning                                                                                                                                   |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Read-back's GPOS feature list     | Expects `curs` and `kern`, and the no-anchor property of §5                                                                                     |
| `conform.check_join_gaps`         | Unchanged; it is the proof that no kern reaches a join                                                                                          |
| The conformance sweep's positions | At every break of every swept text, the shaped advance equals the cell's advance plus the coverage table's value                                |
| `conform.check_split_buffer`      | A text split at a ZWNJ equals its segments shaped alone once the kern across the split is added; a split at a space is unchanged                |
| The ss10 overlay's case           | Waits on §8's ss10 question                                                                                                                     |
| The oracle's position comparison  | Stays kern-neutral: it shapes the new font with `kern` off and keeps normalizing the old font's kerns away                                      |
| The review build                  | Stays kern-neutral under `REVIEW-PLAN.md`'s rule, so a kern edit makes no unit stale and the corpus's freshness leaves the `kern` component out |
| `defects._check_contact`          | Places a break at its kerned offset                                                                                                             |

## 8. Carrying the old kerning over

`rebuild/tools/import_kerning.py` evaluates each rule of `glyph_data/senior_quikscript_kerning.yaml` over the old font's glyph pairs, maps the glyphs to cells through `rebuild/m1-aliases.yaml`, and sorts what it finds:

- A facing pair whose member cell pairs all carry one old value becomes a record, the broadest one that says no more than the old rules did.
- A facing pair whose members carry different old values is reported for a ruling.
- A joined pair an old rule tightens is reported for a ruling: dropped, or turned into a `contract`.
- The ZWNJ rule and the Latin rules of `glyph_data/opentype-features.yaml` are not carried over.

The pairs the importer sets are listed in `var/kern/imported.json`, and the editor marks each as carried over until the author has visited it. The old sidecar and `site/kerning.html` stay with the old font until cutover.

## 9. Order of work

1. The schema key, the loader, the kern stage with its two errors, and `kern.tsv`. The font is unchanged.
2. The GPOS lookup, the table reuse for a kern-only edit, and the gates of §7.
3. `kern-inventory.json` and the editor view, reading only.
4. The write route, the block writer, the journal, and undo.
5. The importer and the carried-over marks.

Each step ends with `make test-rebuild`, an M1 build, and `make artifact-cycle`; step 2 adds `make kernel-gate` for the dropped column.

## 10. Open questions

- **Glyphs with no rune file.** Punctuation, the namer dot, and Latin letters lead and follow Quikscript letters, and the old font kerns some of them. The mini-font compiles only the space, the namer dot, and the ZWNJ, so nothing waits on this yet. The candidate is the same `kern` list on the glyph's own entry in its `glyph_data/` file, with `right: {glyph: …}` naming a follower that is not a rune.
- **Does ss10 kern?** Under ss10 every letter is its anchor-free copy and no pair joins. The old font gives each copy its bare glyph's kerning. The candidates are that each copy kerns as the isolated cell it copies, which adds the facing pairs only ss10 reaches to the inventory, and that ss10 text is unkerned.
- **Junior.** `TODO.md` asks that kerning work in Quikscript Junior, whose letters never join. This plan covers the rebuild's Senior font only.
