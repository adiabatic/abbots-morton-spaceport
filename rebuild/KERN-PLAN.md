# Kerning plan (design §12)

This is the design record for the rebuild's kerning: the `kern:` block of a rune file, the stage that resolves it, the GPOS lookup it compiles to, and the editor view of the review app that writes it. Its inputs are design §12 and the kerning section of `doc/core-idea.md`. §9 orders the work, and its first two steps are built: the schema key, the loader, the kern stage with its three errors and `kern.tsv`, then the GPOS lookup, the kern-only rebuild, and the gates of §7.

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
- **ss10 text kerns, and so does Junior.** Under ss10 no pair joins, so every pair of letters is a break between two isolated shapes, and those pairs take records like any other.

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

A record never names a boundary. A space, a text edge, and the namer dot are not runes, and a ZWNJ is invisible to kerning: a pair across one kerns as the same two cells kern side by side (§5).

**What a record matches.** A record matches a pair of adjacent settled cells when the pair is a break, the left cell is this rune's and fits the record's own-side keys, and the right cell fits `right`. A break is a pair whose left cell has no live exit and whose right cell has no live entry; the join table holds no other kind of break and no join with a declined facing side, and the kern stage asserts both.

**Precedence.** Among the records that match a cell pair, the one whose match set is contained in every other's decides the value, by the extensional order of design §6.2. Two matching records with different values and neither match set inside the other are the error `E-KERN-INCOMPARABLE`, which names both. No value is ever a sum.

**Ligatures.** A ligature stance that declares `outgoing.stance` takes the kern records of the component stance it maps to, those naming that stance and those naming none. Records with `entry` or `bitmap` stay with the component, as incoming behavior and bound drawings do under design § Preserved outgoing strokes. Every record in the ligature's own `kern` block outranks every inherited one. A ligature that opts out with `outgoing: {exception: …}` inherits nothing.

**Stale records.** A record that matches no reachable break is the error `E-KERN-UNMATCHED`. The old font's stale prefix kerns nothing and reports nothing.

## 3. Facing pairs

Most cells of a rune look the same from one side: an entry state changes nothing on the right of most letters, and an exit extension changes nothing on the left. The kern stage groups them.

- A **side** of a rune is a maximal set of its cells that the record grammar can select together and whose facing edge is the same pixel for pixel: for the leading side, cells with no live exit and the same ink-to-advance distance on every row; for the following side, cells with no live entry and the same leftmost ink on every row.
- Each side has a **name**: the coarsest own-side pattern (`family`, then `stance`, then the join state, then `bitmap`) that selects exactly its cells among the rune's cells on that side.
- Cells the grammar cannot tell apart (they differ only in an extension, contraction, or trim amount) are one side even when their edges differ. The coverage table marks such a side, and the editor draws each of its drawings.
- A **facing pair** is a leading side and a following side with at least one reachable break between their cells, in any settlement configuration, across a ZWNJ, or under the ss10 overlay.
- **Across a ZWNJ** every cell that settles immediately before a ZWNJ meets every cell that settles immediately after one: a window whose first right slot is a boundary reads nothing past it, and the window after a ZWNJ reads nothing before it. The cell after a ZWNJ is the `.locked` copy of a word-initial cell (for a rune with no entry on any stance, the word-initial cell itself), drawn and spaced as that cell is, so it falls on the following side its twin is on. Many such pairs meet nowhere else, such as a letter before a ligature it always joins mid-word, and they get facing pairs only a ZWNJ reaches.
- **Under ss10** every ordered pair of letters is a reachable break, and a letter's `.ss10` copy is a member of the side its source cell is in, so most ss10 pairs fall into a facing pair settlement already reaches. A pair of letters whose isolated shapes always join under settlement gets a facing pair only ss10 reaches.

The editor's rows and the coverage table's rows are facing pairs, and every class of the GPOS lookup is a union of whole sides (§5). A record may be broader than one facing pair and never narrower: one that gives two cells of a side different values is the error `E-KERN-NARROWER`.

## 4. The kern stage

`rebuild/pipeline/kern.py` runs in the glyph chain once the tables exist and the cell glyphs are minted, before the defect gates. It reads the spec's kern records, the break rows of every `joins-<config>.tsv`, the cells each configuration settles beside a ZWNJ, the letters' ss10 copies, and the realized bitmaps. No join row crosses a boundary, so the crate records the ZWNJ cells in each configuration's windows head (`zwnj_before` and `zwnj_after`, the crate's `fold::zwnj_cells`), outside both table digests since they follow from the window rows, and the stage takes their product as that configuration's breaks. It writes under `rebuild/out/m1/`:

- **`kern.tsv`**, the coverage table: one row per facing pair, with both side names, the value, the record that decides it (file and key path), and the configurations that reach it. It is diff-stable, so a new stance that inherits a letter-wide value shows up as added rows in a commit's diff.
- **`kern-classes.json`**, the assignment the font is compiled from: each glyph's leading and following side and every facing pair's value, 0 included, so a shaped break whose two sides are no facing pair shows a break missing from the stage's list. The glyph chain writes it once read-back has passed, so it always describes the font beside it, and the conformance sweep checks every shaped break against it (§7).
- **`kern-inventory.json`**, the editor's data: every reachable glyph's ink and advance, the sides, the facing pairs with their member cell pairs, the joined pairs of the same two runes, and for each a witness text with its settled cells and pen positions (§9's third step).

It raises `E-KERN-UNMATCHED`, `E-KERN-INCOMPARABLE`, and `E-KERN-NARROWER`, and it hands the defect gates each kerned break's value (`KernResolution.member_values`), so the off-anchor contact check (design §9) places a kerned break where the font draws it, and an overlap there is `E-CONTACT`, appealable through the allow list under a signature that names the kern (`contact:L:R:overlap:kern-2`), so a bless at one value does not cover a deeper kern of the same pair. Ink that only touches side by side across a break is no error.

**Kerning stays out of the kernel and out of every key but the build's.** `spec_load` parses `kern` into records that the kernel's spec dump does not carry, and `fingerprint.rune_file_digest` leaves the block out, so a kern edit moves neither the tables' stamp, nor the `data` component, nor the family keys of the oracle row cache, the settle memo, the string replay, the deep replay or the review unit cache. Each rune's kern block reaches the run_m1 skip key through a line of its own (`fingerprint.kern_lines`). When only those lines move since the last green build, and the Stage A record shows that the summaries on disk are a passing run's over the other inputs, the cycle runs `run_m1 --kern-only` (`artifact_cycle.kern_only_rebuild`): the glyph chain alone over the tables on disk, the kern stage, the defect gates, the emission, the compile and read-back, and no string replay, witness stage, shipped-order walk, Manual-pin gate or oracle, whose inputs the edit did not touch. It runs the chain beside the build and replaces the build's files only once read-back passes, so a failed kern edit leaves the prior font in place and the next pass is a kern-only rebuild again (`run_m1.kern_only_run`).

**The review corpus does not depend on kerning.** It shapes with `kern` off and the app renders with `font-kerning: none`, so a kern edit changes no unit. Its freshness compares its copied after font with `M1.otf` without the `kern` feature's lookups (`fingerprint.kern_blind_font_digest`), and the GPOS wiring its stores key on leaves the kern lookups out (`fingerprint.gpos_kern_blind_wiring`), so the corpus and `make verdict-ready` stay current through a kern-only rebuild.

## 5. Compilation

`emit_gpos.kern_plan` turns the stage's resolution into one chained-context lookup (GPOS type 8) under the `kern` feature, defined after the `curs` lookups. A value is `by` pixels of x-advance on the first glyph, applied through a single-adjustment value lookup, one per distinct value.

- **Why it is not a pair lookup.** HarfBuzz skips a ZWNJ when it matches GPOS input, so a pair lookup (PairPos) kerns across one; CoreText does not skip it, so the same lookup leaves the pair unkerned in Safari and every other CoreText client, against the ruling that a break at a ZWNJ looks like any other break. Contextual rows beside a pair lookup would kern such a pair twice in HarfBuzz. So each kerned case is a rule for the adjacent pair followed by a rule with one explicit `uni200C` between the two glyphs: a shaper applies the first rule of a lookup that matches, HarfBuzz matches the adjacent rule across a ZWNJ and never reaches the second, and CoreText matches the second. A pair across two or more ZWNJs kerns in HarfBuzz alone.
- **The rules.** The leading sides whose rows (each following side with its value) are the same form one input class, and for each value of that row one rule pair reaches the union of the following sides that take it. feaLib picks each subtable's format. A letter's `.ss10` copy is in the classes of the side its source cell is in.
- **The lookup cannot move a join.** A glyph with a live exit is in no input class and a glyph with a live entry is in no lookahead class. `kern_plan` refuses a plan that breaks this, and a following glyph with a cursive entry anchor, which a `selectable: false` entry row gives every cell of its stance, with a message saying what to change. Read-back checks the same property on the written font: no glyph the lookup adjusts carries an exit anchor in any `curs` lookup, and no glyph it reads after one carries an entry anchor.
- **No kerned pair, no feature.** Only facing pairs whose value is not 0 are compiled, so with none the font has no `kern` feature.
- **Read-back** expects GPOS to register exactly `curs` when the plan has no rule and `curs` and `kern` otherwise. It rebuilds the (first glyph, second glyph) to value relation from the compiled rules and their value lookups, once for the adjacent rules and once for the one-ZWNJ rules, and compares each with the plan's.

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

| Gate or check                    | Under kerning                                                                                                                                                                                                                                                                                       |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The kern stage                   | Raises the three `E-KERN-` errors, and `KernInternalError` on a broken break invariant, a windows head that lists no ZWNJ cells, or a cell beside a ZWNJ with a live side facing it                                                                                                                 |
| `emit_gpos.kern_plan`            | Refuses a plan that would adjust a glyph with an exit anchor or read one with an entry anchor after it (`KernPlanError`)                                                                                                                                                                            |
| Read-back's GPOS checks          | Expects exactly `curs`, or `curs` and `kern` when the plan has a rule; compares both rule shapes' relation with the plan's and checks the no-anchor property of §5                                                                                                                                  |
| `conform.check_join_gaps`        | Unchanged; the kern lookup runs after the `curs` lookups, so a kern that reached a join would open a gap here                                                                                                                                                                                       |
| `conform.check_break_advances`   | At every slot of every swept text that does not exit into a join, `x_advance − x_offset` is the glyph's `hmtx` advance plus the value `kern-classes.json` gives it and the glyph after it, ZWNJ slots skipped; a shaped break between letters with no facing pair there is a `kern-side` divergence |
| `conform.check_split_buffer`     | A text split at a ZWNJ equals its segments shaped alone once the kern across the split is taken off the slot before it; a split at a space is unchanged                                                                                                                                             |
| The ss10 overlay's case          | Every pair is unformed and unjoined, and each slot sits at zero offset with its `hmtx` advance plus its kern                                                                                                                                                                                        |
| The conformance sweep's key      | Gains the kern lookup's rule shapes as behavior classes, so the sweep runs when the lookup appears or changes form; a kern edit that changes values alone leaves the key, and read-back checks the values on every build                                                                            |
| The oracle's position comparison | Stays kern-neutral: it shapes the new font with `kern` off and keeps normalizing the old font's kerns away                                                                                                                                                                                          |
| The review build                 | Stays kern-neutral, so a kern edit makes no unit stale, and the corpus's freshness compares the fonts without their kern lookups                                                                                                                                                                    |
| `defects._check_contact`         | Places each break a kern record moves at its kerned offset, the breaks across a ZWNJ and the overlay's pairs included; an overlap is `E-CONTACT`, side-by-side contact is not                                                                                                                       |

## 8. Carrying the old kerning over

`rebuild/tools/import_kerning.py` evaluates each rule of `glyph_data/senior_quikscript_kerning.yaml` over the old font's glyph pairs, maps the glyphs to cells through `rebuild/m1-aliases.yaml`, and sorts what it finds:

- A facing pair whose member cell pairs all carry one old value becomes a record, the broadest one that says no more than the old rules did.
- A facing pair whose members carry different old values is reported for a ruling.
- A joined pair an old rule tightens is reported for a ruling: dropped, or turned into a `contract`.
- The ZWNJ rule and the Latin rules of `glyph_data/opentype-features.yaml` are not carried over.

The pairs the importer sets are listed in `var/kern/imported.json`, and the editor marks each as carried over until the author has visited it. The old sidecar and `site/kerning.html` stay with the old font until cutover.

## 9. Order of work

1. Built: the schema key, the loader, the kern stage with its three errors, and `kern.tsv`.
2. Built: the GPOS lookup, the kern-only rebuild, and the gates of §7.
3. `kern-inventory.json` and the editor view, reading only.
4. The write route, the block writer, the journal, and undo.
5. The importer and the carried-over marks.

Each step ends with `make test-rebuild`, an M1 build, and `make artifact-cycle`; a step that changes the crate adds `make kernel-gate`.

## 10. Open questions

- **Glyphs with no rune file.** Punctuation, the namer dot, and Latin letters lead and follow Quikscript letters, and the old font kerns some of them. The mini-font compiles only the space, the namer dot, and the ZWNJ, so nothing waits on this yet. The candidate is the same `kern` list on the glyph's own entry in its `glyph_data/` file, with `right: {glyph: …}` naming a follower that is not a rune.
- **How the records reach Junior.** Junior's letters never join, so its pairs are the ones ss10 reaches, and the author's ruling is that they kern. The rebuild compiles only the Senior font, so which of a rune's records Junior reads, and on top of which default spacing (Junior lacks the Senior tightening), waits until the rebuild builds Junior.
