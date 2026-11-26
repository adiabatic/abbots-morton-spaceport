# Cutover plan: the rebuild builds every font

The cutover of `doc/rebuild-design.md` §13.6: the rune files and the pipeline under `rebuild/` build Senior, Junior and Mono, `make all` runs that build, and the old engine and the comparison against it leave the tree. It prepares a release and makes none: the version, `README.md`, `FONTLOG.md` and the specimen pages' prose stay as they are.

## 1. Rulings

The author's, binding on every stage:

- **The blanks in the review queue do not gate the cutover**, and neither does a leak gate, which the rebuild has no counterpart to. §13.6's acceptance list is rewritten to match in stage 7.
- **The comparison against the old font retires.** The baseline tables, the oracle, the divergence ledger, the alias map and the old-against-new review corpus go, with git as their record. The review app's Queue then shows the accepted rebuild against an edit (`rebuild.review.build --mode table-diff` against a snapshot), and the kerning view stays.
- **The replacement for the review corpus is built before the old one is deleted** (stage 4).
- **A pin the rebuild disagrees with is marked pending**: it keeps the author's text, the suite reports it without failing, and `WHATNEXT.md` lists it for a ruling. The pending pins are the four regroupings in `site/extra-senior-words.html` (`·Owe ~x~ ·Fee | ·It` twice, `·I ~x~ ·Fee | ·Utter`, `·No | ·Utter.alt`), the Manual's `·Utter ~x~ ·May | ·Thaw`, and the Manual's `·It ~b~ ·Zoo.half ~b~ ·I`.
- **`|` keeps its isolation meaning**: the text before the break shapes the same alone. Settlement's lookahead reads across a break, which is why `·Utter ~x~ ·May | ·Thaw` is pending.
- **A variation sequence selects its stance's shape, unjoined**, in the rebuilt Senior. Forced stances that join are an open fork for `WHATNEXT.md`, and the Manual pin that types VS2 for a joining half ·Zoo is pending on it.
- **The milestone names and the directory are renamed** (stage 6). The proposal for the directory is `engine/`.
- **Each stage is committed on master once its gates are green**, without a pause for approval.

Settled before the cutover and carried through it: ss02 leaves the font and ss08 joins it, and the old font's ·He-before-ZWNJ kern (`he-before-noentry` in `glyph_data/opentype-features.yaml`) is not carried (`rebuild/KERN-PLAN.md` §1).

## 2. Stages

`var/keep/cutover/` holds the research each stage's design rests on: the three surveys and the designs for stages 2, 3 and 4.

Each stage ends with `make test`, `make test-rebuild` and a `make artifact-cycle` pass green, and its commit.

1. **The whole Senior font from the runes.** `run_m1` compiles it, Regular as `rebuild/out/m1/M1.otf` and Bold beside it as `M1-Bold.otf`, with every glyph, the metadata, the variation sequences and the Latin `kern`, `ccmp` and `mark` features the shipped Senior has, and the gates run on those bytes. The old build still writes `site/`.
2. **Mono and Junior from the runes.** Mono takes each rune's `mono:` bitmap and Junior its isolated cell, with the alternates the variation sequences name, the gapped ·Owe behind ss06, ss10 and the namer dot. The check is glyph-for-glyph equality with the old build's Mono and Junior, which is why the old engine outlives this stage. The angle parentheses move to a `glyph_data/` file that is not `quikscript.yaml`.
   - The runes already hold the data: every rune's `mono:` block equals the old Mono record, and every isolated cell equals the old Junior glyph. A build that feeds `tools/build_font.build_font` rune-derived legacy `glyphs:` records and Junior's ss06, ss10 and namer-dot FEA reproduces the four fonts byte for byte when it also carries the glyphs no rune holds and the old glyph names.
   - The stage lands in two commits. The first proves the path by that byte equality. The second takes the deliberate differences: Junior drops its glyphs no text reaches (its ligatures, ·Vie+Utter among them, and the alternates' contextual siblings), the alternates and the gapped ·Owe take the rebuild's glyph names, and Junior's namer dot stops lowering across a ZWNJ, as Senior's does.
   - `space` and the angle parentheses move to `glyph_data/punctuation.yaml` as unsuffixed records, which compile the same in all three fonts. The variation-sequence registry in `rebuild/script.yaml` carries a font scope: VS1 reaches all three fonts and VS2 only Senior. The Mono drawing of an alternate is the stance's base bitmap, which the build checks is five columns wide.
   - Mono and Junior need the spec load and no settlement table, so they build while the tables are red.
3. **The `data-expect` suite on the new fonts.** `parse_expect` and the corpus collector split from the old engine's internals, the suite shapes the new fonts, and each modifier a pin uses is restated in the rune vocabulary.
   - The grammar, the collector and the checker become three leaf modules under `rebuild/validation/` (`expect.py`, `corpus.py`, `check.py`) that import nothing from `pipeline/`, `review/` or `tools/`. `rebuild/review-rs/src/expect.rs` cites `expect.py`, and the root conftest and the build's pin gate (`manual_pins`) call the one checker over all three pages.
   - The checker reads a junction from the font's `curs` lookups, a base, stance, height and adjustment from the glyph name (`settle.cell_label`), and a trait, a bound bitmap and the isolated flag from a sidecar the build writes when it mints the cells.
   - `.half`, `.alt`, `.exit`, `.extended`, `.en-ext-N`, `.ex-ext-N` and `.reaches-way-back` keep their text. `.noentry` becomes `.locked`, and `.!baseline`, which no old glyph could fail, becomes `.!ex-y0`.
   - The pins that restate a ruling the runes already carry are rewritten with the switch, in one commit, since a rewritten pin fails on the old font: the three ss02 pins move to ss03, the two ·May `.en-ext-1` pins lose the modifier, and the required `·Vie+Utter` in `site/extra-senior-words.html` becomes `·Vie ~b~ ·Utter`.
   - Of `test/test_calt_regressions.py`, the any-of `data-expect` checks port to the checker and the tests that read old names or anchors go in stage 4. `test/test_namer_dot.py` compares bases.
4. **The comparison retires, `make all` switches, and the old engine goes.** The retirement is seven commits, each green, then the switch, then the old engine.
   1. **Table-diff mode made ready for the cycle**, with nothing deleted. `var/keep/cutover/stage4-table-diff-design.md` is the design, with the existing code each part builds on.
      - A settlement row is a class-compressed rule over hundreds of windows, and one join change re-folds hundreds of rules, so a changed row is evidence and never a unit. A settlement unit is one visible substitution (the input rune, the glyph to its left in each font, the old cell, the new cell) folded across configurations, and a join unit is one (left, right, old, new) across configurations.
      - The candidate texts for a changed input are its old and new rules' certificates (the windows head holds one per rule, and the snapshot carries its own) and every text of at most three letters that holds the input, shaped in both fonts. A context that needs a longer text can be missed; diffing the window rows themselves is the sound fix and a fork for the author.
      - A glyph bucket diffs the snapshot's font against the new one per glyph (outline, advance, cursive anchors), because a bitmap or advance edit leaves the tables unchanged, and a kern bucket diffs `kern.tsv`.
      - `cluster` is a digest of the unit's ink deltas, so the Queue (`static/queue.js` keeps only units with a `cluster`) groups by the pixels that change, as it does today.
      - An empty diff, and a machine with no snapshot, build a zero-unit corpus the app boots on. The manifest carries an inputs fingerprint (the snapshot's digest, the tables' stamp, the font inputs) for the skip and the readiness check.
      - The accept command re-takes the snapshot, refuses while a unit is blank or rejected or a build input differs from HEAD, and clears the store. The snapshot lives under `var/keep/review-snapshot/`, the tracked `rebuild/review-snapshot.json` records the commit it was taken at, and a build of that commit remakes it.
   2. **The cycle, the readiness check and `review-build` switch to it.** The first snapshot is taken, the store starts empty, and the verdict-update and review-facts steps leave the cycle. The diff against the snapshot is a readiness row and a summary line, never a red gate.
   3. **The leaf verdict tools go**: the standing approvals with their daemon, probe, skills and workflow, the novelty order, the complaint list, the queue snapshot, the duplicate fill, the re-key, the carry, the merge, the held store and the verdict update.
   4. **The accepted list and the close-out go**, and the store, the land, the server, the status and `verdicts.js` shrink to what a small transient store needs. The journal goes.
   5. **The m1-audit corpus goes**: the review crate, `corpus_exec`, `units_exec`, `subset_pack`, the facts, the change groups, the drafters, feature reach, the unit stores, and the m1 fixtures and JS. It goes before the oracle because it reads the oracle's audit.
   6. **The generic code leaves the oracle's files**: the settle-memo keying (`oracle_cache.py`), the shared-memory helpers (`oracle.py`) and the alphabet (`baseline_subset.M1_ALPHABET`) move to modules of their own. The memo's stamp hashes those paths, so the pass after it settles everything once.
   7. **The oracle and the baseline go**: the oracle's files, `baseline_subset`, `rebuild/baseline/`, the divergence ledger, the alias map, the probe's baseline column, the kerning importer, and the gates-only rerun cut down to the defect and Manual-pin gates.

   Then `make all` builds the six fonts from the runes, and the old engine goes with its tests, tools, skills and site pages, and `doc/abandoned-designs/` gains the demote-oracle and guard-table chapters §13.6 names.

   What the retirement gives up is the fixed outside reference: a mistake that is accepted becomes the reference. The `data-expect` pins are the outside check from then on.
5. **The moves the plans deferred to cutover.** The registry moves to `glyph_data/script.yaml` (`rebuild/M1-PLAN.md` §1), the namer dot's stand-in goes (`rebuild/REVIEW-PLAN.md` §1.4), and `.github/workflows/deploy.yml` builds with a Rust toolchain.
6. **The renames.** The milestone-named things that are permanent (`run_m1`, `M1.otf`, `rebuild/out/m1/`, the `m1-*` ledgers that survive stage 4, `M1_ALPHABET`, the `m1_` lookup prefix) and the directory. The check before it: the review unit ids and the tables do not move.
7. **The tidy.** `AGENTS.md`, `WHATNEXT.md`, the skills and the docs lose what describes only the old engine or the finished migration, `doc/rebuild-design.md` and the plans state the layout as it stands, and the dead code and the note-taking violations the survey found go. This file is deleted with it.

## 3. Open forks

- **Forced stances that join.** The old font's `calt` goes on to join the stance a variation sequence selects; settlement reads only the raw cmap glyphs. Whether settlement gains forced-stance letters is the author's.
- **The seven ·Owe kern pairs** the importer never carried (it ran before ·Owe had a rune file), and the rulings `var/kern/import-report.json` lists, are the author's (`WHATNEXT.md`).
- **The namer dot before a ZWNJ.** The old font lowers the dot in namer dot, ZWNJ, Short letter, and the rebuild does not (`emit_gsub`'s `ignore sub`), which design §3.4 calls for. `rebuild/M1-PLAN.md` §2 says the opposite and is corrected in stage 7.
