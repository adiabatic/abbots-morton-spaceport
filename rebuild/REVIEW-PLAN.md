# Review corpus plan (design §11: the accepted build against an edit)

This is the design record for the table-diff review app: why the corpus has its shape, and the contracts it was built against. Its inputs are design §11, §8, §10.5 and §6.3. The app is built under `rebuild/out/review/` and served on port 7294.

`rebuild/review/README.md` describes the app as a reviewer uses it: the commands, the keyboard map, the triage flow, and what each machine-approval mechanism does for the reviewer, with pointers to the module docstrings that hold the detail. The checkers in `rebuild/review/build.py` are the executable contract. Counts are not kept here: a build's totals (`totals`, `machine_approved`, `table_rows` and `font_changes`) are in `rebuild/out/review/manifest.json`, and `make verdict-ready` reports adjudication status. The `rebuild/review/*.py` module docstrings and the README cite this file's section numbers, so sections are never renumbered.

## 1. Architecture

### 1.1 Source layout (checked-in, under `rebuild/review/`)

`rebuild/review/` is a Python package, and each module's docstring says what it does. `static/` holds the app's ES modules, CSS, and HTML shell, and `jstests/` holds the modules' `node --test` tests (§5.2). `tablediff.py` diffs the rows of a snapshot of the accepted build (`snapshot.py`) against a run_m1 build's, `diff_units.py` turns the changed rows into the visible changes the corpus shows (§2.3), and `build.py` writes them as shards beside the manifest and checks the result. `ink.py` compares the two fonts' ink, `shards.py` holds the shard layout, and `app_index.py` writes the app's sidecars (§7.4).

The server, `serve.py`, runs the same livereload server and no-store static handler as `tools/serve.py`, over `rebuild/out/review/`, and adds the `/autosave`, `/status` and `/capabilities` endpoints and the kerning view's routes (`kern_routes.py`, which writes the rune files' `kern:` blocks through `kern_writer.py`, rebuild/KERN-PLAN.md §6); `/capabilities` names the land protocol (`landing.py`) the cycle relies on to keep the server running through a pass. Unlike `tools/serve.py`, it reloads no tab on a file change: the cycle sends an `ams:` path to livereload's `/forcereload` when what the server serves changes, and the app decides when to reload (`static/reload.js`). `rebuild/review/fixtures/` holds a hand-written miniature corpus (a `manifest.json` and its unit shards) that satisfies the §7 contract, so the contract checker and the frontend's node tests run against it without a build. Python tests are `rebuild/test_review_*.py` and their neighbors (§5.1).

The data-expect parser is the grammar module `rebuild/validation/expect.py`, which imports nothing else from the repository.

### 1.2 Output layout (generated, gitignored via the existing `rebuild/out/` rule)

```text
rebuild/out/review/
  index.html            copied from static/
  app.css  app.js  …    the rest of static/, copied verbatim
  manifest.json         generation metadata, class index, font records
  units/<class-id>.json one shard per nonzero class, split into .000.json, .001.json, … parts when large
  fonts/before.otf      copy of the snapshot's M1.otf (the after font when no snapshot is present)
  fonts/after.otf       copy of rebuild/out/m1/M1.otf
```

The build also writes the app sidecars (§7.4) beside the manifest.

A shard part is capped at `shards.SHARD_PART_BYTES` on disk. The app decodes each fragment it fetches as one JavaScript string, and a string longer than V8's `String::kMaxLength` (2**29 − 24 bytes under pointer compression) cannot be built. A class's fragments go into its parts in id order (`shards.PartLayout`): the bare name when they fit in one part, and otherwise contiguous three-digit parts numbered from `000`. Each fragment is plain, ASCII JSON, a standalone element between its part's `[`, commas and closing bytes, so the app reads each one by the address its sidecar row records.

`manifest.json` records each font copy's source path and sha256. The directory is self-contained, and deleting and rebuilding it is always safe. It never needs clearing by hand either: after writing the manifest, the build deletes every shard the manifest no longer references (`build._prune_orphan_shards`).

### 1.3 CLI surface (documented on the generated page itself, check.html-style)

The everyday commands (`make review-build` and `make review-serve`) are in the README's Commands block. These commands serve the table diff:

| Task                         | Command                                                                                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Build the corpus             | `uv run python -m rebuild.review.build [--snapshot <dir>] [--new rebuild/out/m1] --out <dir>`                |
| Snapshot the accepted build  | `uv run python -m rebuild.review.snapshot take [--tables rebuild/out/m1] [--home var/keep/review-snapshot]`  |
| Accept the corpus's changes  | `make review-accept` (a dry run; `ARGS=--apply` writes, `ARGS='--apply --retake'` over the refusals)         |
| Remake the accepted snapshot | `make review-snapshot-remake` (`uv run python -m rebuild.review.accept remake [--rev <rev>] [--home <dir>]`) |

`rebuild.review.build snapshot` takes the same arguments as `snapshot take`, and `snapshot show` describes the current snapshot.

Build and serve are separate commands because the server is long-running. `serve.py` prints the build command and exits when the directory has no `manifest.json`. The two servers run side by side: port 7293 serves `site/` and port 7294 serves the review app. Both send `Cache-Control: no-store`, because a stale cached OTF would make every visual judgment wrong without any sign of it. The build suppresses warnings, `spec_load`'s `SpecWarning`s among them, while it loads the spec.

### 1.4 At cutover

At cutover the app moves out of `rebuild/out/`, and the serve script is likely to merge into `tools/serve.py`'s livereload. The `review-build` and `review-serve` Makefile targets stay as they are, and the move needs no `site/` changes and touches no existing pipeline files. One leftover waits for the move without blocking anything: the build copies a `{"type":"module"}` `package.json` from `static/` into the output verbatim, which is harmless.

The namer dot's stand-in stays through cutover. Chromium ends a shaping run at U+00B7, so the dot is shaped without the letter after it, and the rebuild lowers the dot before a Short letter by a rule that reads that letter (`emit_gsub.namer_dot_lookup`); without the stand-in the app would draw every such dot unlowered. The server maps U+F8FF to the dot's glyph in the fonts the app loads (`namer_dot_stand_in_font`, served by `BrowserFontHandler` in `rebuild/review/serve.py`), and the app writes U+F8FF in the dot's place (`namerDotStandIn` and `namerDotStandInEntities` in `static/render.js`). `test_the_namer_dots_stand_in_keeps_the_dot_lowering_that_reads_the_next_letter` in `rebuild/test_review_autosave.py` fails once the dot's form no longer depends on the letter after it; then the stand-in, its tests in that file and `rebuild/review/jstests/render.test.js`, and the `/fonts/browser/` `@font-face` sources in `static/app.css` can go.

## 2. Data model

### 2.1 Units, ids, ordering

A unit is one visible change between the accepted build and the new one, shown in its shortest text under every configuration that text shows it under; §2.3 says how the build finds them.

Each unit carries its config list, a `config_gate`, and its prose form `config_note`. Both are null when the set covers every configuration of the roster (`tablediff.SETTLED_CONFIGS`), which is the common case. Otherwise the gate is the minimal conjunction of feature on/off constraints that selects exactly that set, on-constraints first, one clause per constraint, each clause carrying the text the badge prints. `build.config_badge` is the authority, including the literal “only under: …” fallback for a set no short conjunction selects, which a glyph only the ss10 overlay reaches takes. `render_groups` partitions the configs by rendered outcome; an extra group renders stacked. A verdict applies to the whole unit.

Units are ordered for triage by class in manifest order, then by group (the family pair that meets at the change, in code-point order), then by text length and codepoints, then by id (`audit.triage_key`). Manifest order is the sidebar's: by section (`audit.class_section`), then `diff_units.CLASS_IDS` order (§7.1).

**Kern-neutrality rule (binding for every review-corpus comparison but a kern unit's)**: the corpus compares settlement, joins and drawings, and kerning stays out of it: the rebuild's `kern` lookup has its own plan (rebuild/KERN-PLAN.md). Everywhere the review build shapes text (the ink comparison, highlight x-ranges, boundary-mark positions) it passes `kern: False` to HarfBuzz for both fonts through `ink.kern_neutral`, which adds kern-off to the config's stylistic-set features. The frontend renders both sample columns with `font-kerning: none`, alongside each row's inline `font-feature-settings`. Other rebuild consumers (oracle conformance, pin replay) keep their own shaping; the rule applies only in `rebuild/review/`. The one exception is a kern unit (§2.3), which shows a moved kern value, so it is shaped, compared and drawn with kerning on in both fonts, and the corpus's freshness compares its after font byte for byte.

**Ink-identical machine approval (`ink.py`)**: at build time every unit's text is shaped in both fonts under every config in its set, with uharfbuzz through `rebuild.validation.shaping.Shaper` and kern-neutral. Each glyph's outline is recorded with fontTools' `DecomposingRecordingPen` and placed at the cumulative `x_advance` plus the glyph's `x_offset`/`y_offset`. A unit whose sorted placed pieces (`ink_pieces`) compare equal under every config is `ink_identical: true`. Both fonts then render it identically and only glyph names differ, so the build approves it without a human. The picture check below it reads `config_diff`'s identity sentinel, the same pixel-level delta a unit's `cluster` digests. `rebuild/test_review_ink.py` checks, over a stride of the frozen mini-bundle windows, that the sentinel equals the whole-run cell comparison and follows from piece identity. The manifest's `machine_approved` record holds the machine-approved unit and row totals, per class and per machine check. A unit with no text has nothing to shape, so it stays `ink_identical: false` and goes to a human.

**A unit's id is its content**: `unit_ids.unit_id_for` of its content key, the identity digest `diff_units.unit_identity` gives it (§2.3). The id is the key's first 64 bits written as eleven base58 symbols, zero-padded, behind the `u-` prefix: `u-3mJ7kPq2Xw9`. The base58 alphabet is Bitcoin's (`123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz`: the digits and both cases minus 0, O, I and l), and case matters. The id says nothing about the unit's place in the queue. So the same change has the same id on every corpus it appears on, a fragment's bytes depend only on its own content, and a verdict follows its unit across rebuilds by id (`verdict_store.carry`). A build-code change that re-keys units without changing any input bumps `build.UNIT_ASSEMBLY_EPOCH`, for the reason the docstring of `build._generated_at` gives. Cluster ids are hashes of their inputs (§2.3). Within a class shard, fragments are in id order, which is the order the app's locator binary-searches.

**Batches cover the human units only**: the manifest's `human_unit_ids` is the triage index. It lists, in triage order, every unit that no machine check approves and whose class takes a verdict. A batch is a fixed slice of `batch_size` (`audit.BATCH_SIZE`) of it, a unit's batch its position divided by that size. No fragment carries its position or its batch. The app-index rows (§7.4) carry each human unit's `order` and `batch`. Machine-approved and no-verdict units are outside the index and are never paged to a human. The manifest's `machine_approved` record (units, rows, the verification method, per-class counts) and each class's `machine_approved_count` let sidebar counts, batch labels, and progress denominators count only the human units, while the machine-approved total is shown in the header strip's corpus-totals popover.

### 2.2 Encoding: sharded JSON, everything precomputed

Decision: **one `manifest.json` plus one JSON shard per class, with everything computed at build time.** Provenance, junctions, highlight offsets and the explain text are computed at build time. The browser does not compute any of them; it renders and collects verdicts. There is no server-side logic and no explain endpoint.

The app boots from a slim index of the human units (§7.4). The index leaves out everything a card draws from the unit's own record: `explain` and `provenance`, and also the sample text, the pair band, and the settled cells. When a card renders, and when its explain panel opens, the app reads the record with an HTTP Range request against the shard the build wrote, using the byte span in the index row. The directory stays static, and the tab holds the queue, not the corpus.

Per-unit precomputed fields (full contract in §7): notation, before/after facts (glyphs or cells, junctions, extensions), the changed positions and the pair, highlight x-ranges in font units for both fonts, the explain text, and the provenance pointers. The x-ranges come from `hmtx` advances, so the frontend draws the pair highlight with `px = units × font-size / upem` and never measures text.

### 2.3 The table diff: the accepted build against an edit

The build (`build.build_table_diff`) builds a corpus of what an edit changes against a snapshot of the accepted build, design §8's diff behind the review app:

- **The snapshot** (`rebuild/review/snapshot.py`, format `ams-review-snapshot/3`) holds one run_m1 build: each settled configuration's settlement and join tables, gzipped, and its rule certificates as texts (`certificates-<config>.ndjson.gz`, one line per rule in rule order), the Regular font and the kern table, with a `snapshot.json` that records when it was taken, the tables' stamp and each file's sha256. Its digest, which leaves out when and where it was taken, names its directory under `var/keep/review-snapshot/`, and the `current` file beside the directories names the one a build compares against by default; taking one keeps it and the one it replaces. The module docstring gives the layout and the digest.
- **The pointer** (`rebuild/review-snapshot.json`, tracked) names the accepted snapshot: `{format, snapshot_format, digest, taken_at}`. It records no commit. The accept writes it before the author commits, its change goes into the commit with the edit it accepts, and so the commit a snapshot stands for is the last commit that changed the pointer (`snapshot.pointer_commit`), which the manifest's `baseline.commit` and the readiness rows name. `rebuild/test_review_snapshot.py` holds its `snapshot_format` to the reader's, so a format bump takes the snapshot again in the same commit.
- **The row diff** (`tablediff.py`): settlement key (`config`, `input`, `backtrack`, and up to four lookahead classes) → (`outcome`, `joint`, `provenance`); join key (`config`, `left`, `right`) → (`junction`, `extension`). Rows are classified as added, removed or changed, removals and additions that share (`config`, `input`) are paired as regrouped, and settlement changes that move only provenance are `provenance-only`. A configuration whose two tables hold the same bytes on both sides is passed over unparsed. The diff reads only the tables of configurations in `tablediff.SETTLED_CONFIGS` (the settled configurations, without the ss10 overlay).
- **Rows are evidence, not units.** A settlement row is a class-compressed rule over hundreds of windows, and one join change re-folds hundreds of rules, so `diff_units.py` turns the changed rows into visible changes. A settlement unit is one cell change: a letter the accepted build settles to one cell and the new build to another, found by shaping, in both fonts and with kern off, the certificates of the letter's old and new rules and every text of at most three letters that holds it, and folded across the left neighbors it is seen after, since a cell's label already names its entry and adjustments; the left glyphs are evidence its explain panel lists. A join unit is one changed join row with the shortest text that holds its two cells side by side in the font of each side the row exists on; a row no text is found for is a unit shown by its notation alone. A letter whose rows changed with no cell change found is one unit, machine-approved when every candidate draws alike or differs only where another glyph changed, and a letter whose rows changed only their provenance is a unit of the no-verdict `provenance-only` class. Every unit folds the configurations its one text shows its change under. A context that needs more than three letters can be missed; diffing the window rows themselves would close that gap. The module docstring gives the search and the fold.
- **Units that show one thing are one unit.** After the fold, join and settlement units with the same text under the same configuration set, which shape to the same runs in both fonts, merge (`diff_units.merge`): a removed join row, its added twin and the cell change at that junction are one unit, which carries all their kinds and rows and is in `joins` when any of them is a join row, and in `settlement` otherwise (`diff_units.KIND_PRECEDENCE`). Units whose configuration sets differ stay apart, and units with no text, quiet units and provenance units never merge. The row key names each change the unit shows, separated by semicolons.
- **Glyphs.** A bitmap, anchor or advance edit leaves the tables as they were, so the build also compares the snapshot's font with the new one glyph by glyph (`diff_units.glyph_changes`): each glyph both fonts have whose decomposed outline, advance and sidebearing, or cursive anchors (read without the lookup each sits in) differ is a unit of the `glyphs` class. Its text is the shortest certificate of either table whose rule settles to the glyph, else a code point a font maps to it alone, else the shortest kerning-inventory text that holds it, else its letters under ss10, each taken once a run of it holds the glyph; the unit lists every configuration under which a run of its text does. A glyph outside Quikscript is in the group `non-quikscript`, over its code point alone, and clusters by its ink delta like any other unit, so a Latin edit and the composites built from the edited glyph share a card. A glyph no text draws (a letter's bare glyph, which settlement always replaces) is a unit with no text, whose cluster is its group's, so one letter's such glyphs share a card. Glyph units merge with join and settlement units over the same text and configurations, after both in `diff_units.KIND_PRECEDENCE`. A glyph only one font has is the row diff's business.
- **Kerning.** The build diffs the snapshot's `kern.tsv` with the new one by (leading side, following side): each pair whose value moved, a pair one table lacks counting as kerned by 0, is a unit of the no-verdict `kerning` class over the kerning inventory's witness of the pair (its `pairs` are in `kern.tsv` order), or the two letters alone for a pair the new table lacks. Its fragment's `kerning` is true, and the build shapes and compares it, and the app draws it (`kerned` samples), with kerning on. The kerning view (`#view=kern`) is where kern values are authored and judged, so these units take no verdict, and the Queue and the sidebar still count them.
- **Classes** (`diff_units.CLASSES`, in sidebar order within each section): `joins`, `settlement`, `glyphs`, and the no-verdict `kerning` and `provenance-only`. A unit's `kinds` names the row buckets it stands for (`added`, `removed`, `changed`, `regrouped`, `provenance-only`, or `context` for a substitution whose letter's own rows did not change), and for a glyph what changed in it (`outline`, `metrics`, `anchors`).
- **Queue fields.** A human unit's `cluster` is `c-` and the first eight hex digits of the sha1 of the sorted distinct ink-delta digests (`ink.delta_digest` of `InkComparator.config_diff`) of its configurations, which leaves the configurations and the kind out, so units that show the same pixels share one Queue card; a unit with no text takes a key of its kind and its old and new values. `group` is the family pair that meets at the change (`qsOwe:qsDay`), or the letter alone after a boundary, so the app's family filter and group folds read real families. `pair`, `pair_codepoints`, `highlight`, `notation_tokens` and `boundary_marks` come from HarfBuzz's clusters, so the pair band lies on the changed junction or glyph.
- **Ids.** A unit's id is `unit_ids.unit_id_for` of `diff_units.unit_identity`, the sha256 of `table-diff/1`, the set of its changes' kinds, what each change is with its old and new values, its configurations, its code points, and the glyph names of its text's runs in each font, which `content_key` holds. The ink and the left glyphs a cell change was seen after are left out, so an edit that only redraws a glyph, or adds a left neighbor to a cell change, changes no unit's id.
- **Empty corpora.** An empty diff builds a corpus with no units and no classes, and so does a machine with no snapshot, whose manifest's `baseline` says no snapshot is present and whose before font is the after font, so the app shell and the kerning view boot; the Queue then says there are no unaccepted changes since the snapshot's commit, or that there is no snapshot.
- **Freshness and readiness.** The manifest's `inputs_fingerprint` is `fingerprint.TABLE_DIFF_COMPONENTS`: `snapshot` (the snapshot record's sha256), `tables` (the tables' stamp, copied from the new build's windows heads and recomputed by `run_m1.tables_inputs`), `font_inputs` (the font data and kern lines a kern-only rebuild answers for), `review_code` and `static`. `status.compute_status` fails a corpus that is not a table-diff corpus; on a table-diff corpus the freshness check compares these and the after font byte for byte, a `snapshot` check fails when no snapshot is current or the current one is not the one the pointer names (remedy `make review-snapshot-remake`), and a `diff` check never fails and counts the unaccepted changes by what they need (`status._diff_check`); `make verdict-ready` prints the rows in `verdict_ready.CHECK_ORDER`, and `verdict_ready.diff_summary_line` is the cycle's summary line.
- **The accept** (`rebuild/review/accept.py`, `make review-accept`) makes the build the corpus shows the accepted one: it takes the snapshot of `rebuild/out/m1`, writes the pointer, empties the verdict store beside a listening review server and tells the open tabs to reload onto it. It refuses on a corpus that is not a table-diff corpus, while readiness fails or the corpus does not show `rebuild/out/m1`, while a pass may write the store, and while a human unit is blank, skipped, rejected or neither; `--retake` takes the snapshot over those refusals for a format bump or an unremakeable snapshot. **The remake** (`make review-snapshot-remake`) installs the pointer's snapshot on a machine without it, from that commit's own build in a tree `git archive` exports. The module docstring gives both.

## 3. Page UX

The app is a tool for one person triaging an edit's changes over several hours. Its chrome follows the `site/` style (`light-dark()`, system fonts, Menlo for code), and the samples use the fonts under test. A reviewer should be able to clear a batch from the keyboard alone. The README's “Quick start for triage” describes the page as the reviewer uses it (the keyboard map, the explain panel, the triage flow, and the exports), and the modules under `static/` are the authority on how it does so. This section keeps the design decisions that neither states.

### 3.1 Rendering (§11 requirements, with the proven mechanics)

- **The pair highlight is drawn outside the text**: wrapping part of a run in a span breaks shaping, so the highlight is an absolutely positioned band under the changed pair in each sample, placed from the precomputed font-unit x-ranges (§2.2). Its accent color meets the 3:1 non-text contrast rule in both color schemes.
- **ZWNJ**: emitted as a literal `&#x200C;` inside the run, so the real `uni200C` rules fire; browsers render it invisibly.
- **Checkered background**: `--font-size: 88px` gives 8 px per font pixel (50 units at upem 550), so check.html's 16 px checker with `background-position: 0 5.6px, 8px 13.6px` applies unchanged. If the size changes, recompute the offsets.

### 3.2 Triage flow

- **Keyboard access**: the verdict keys trigger real `<button>` elements, which have visible focus indicators and are keyboard-accessible per WCAG, and one delegated `keydown` handler on the document runs the same code as a click. Auto-advance scrolls with `behavior: smooth`, or `auto` under `prefers-reduced-motion`.
- **A testable key model**: `actionForKey` in `keyboard.js` is pure, and an open follow-up menu reaches it as a context flag (`rejectMenuOpen`), so the key model, menus included, stays unit-testable.
- **Whole-unit verdicts**: there is no per-config verdict, because every config of a unit shows the same change in its one text (§2.3), and a control that changes nothing visible would mislead.

### 3.3 URL state (and what stays out of it)

View state lives in `location.hash` (`parseHash`, `writeHash`, and `STATE_KEYS` in `state.js`), so every view can be bookmarked and a reload mid-batch returns to the same cursor. **Verdicts are not in the URL, and localStorage holds only the outbox of changes the server has not yet acknowledged** (`outbox.js`). The page holds them in memory keyed by unit id, the server's `/autosave` endpoint keeps them across reloads, writing the store before it answers each save (the README's triage flow and the docstring of `verdict_store.py` describe both), and a downloaded export in the §4.1 format is the durable copy of the verdicts recorded since the last accept (§4.1).

## 4. Verdicts

The page exports `verdicts.json` (download or copy), the re-importable record of the reviewer's work since the last accept. Nothing is applied to the corpus or the rune files automatically: the just-verdicted-now-what skill reads a review's rejects from the store and the corpus and turns them into rune edits.

### 4.1 `verdicts.json`

```json
{
  "format": "ams-review-verdicts/1",
  "manifest_generated_at": "2026-06-10T17:02:11Z",
  "exported_at": "2026-06-10T18:40:02Z",
  "verdicts": [
    {"unit": "u-3mJ7kPq2Xw9", "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"},
    {"unit": "u-Nc3wVLJaPHB", "verdict": "reject", "note": "junction looks reached-for", "at": "2026-06-10T18:21:40Z"}
  ]
}
```

`verdict` is one of `approve`, `reject`, `either`, `identical`, `neither`, or `skip`. A verdict covers all of the unit's configs; the import ignores a `configs` field if a record has one.

The store, `verdicts-autosave.json`, is a document of the same format with one more top-level field, which a reader of `verdicts` alone passes over: `cleared`, its tombstones. It holds the verdicts made since the last accept, which empties it (`doc/review-cycle.md` § The verdict store). An Export carries no `cleared` field.

## 5. Testing strategy

### 5.1 Python (`rebuild/test_review_*.py` and their neighbors, in the contracts lane of `make test-rebuild`)

No review test reads the live corpus. Every example window comes from the frozen mini bundle under `rebuild/review/fixtures/mini/` (the `mini_bundle` fixture in `rebuild/conftest.py`), and the table diffs the tests build are over the pipeline's mini fixture spec (`rebuild/test_review_diff_units.py`). What the build can check per unit, it checks during the build (`check_unit` and the cross-unit checks in `build.py`), so no test re-checks shipped shards. Each test module's docstring says what it covers and which build-time check covers the rest.

### 5.2 JavaScript

Every ES module in `static/` except the two that build the DOM is a pure module with no DOM access at top level, and each has a `*.test.js` under `rebuild/review/jstests/`. The two are `app.js` and `kern-view.js`, the kerning view's DOM (rebuild/KERN-PLAN.md §6), which touches nothing until `app.js` calls `createKernView`; the kerning view's model and drawing are the pure `kern.js` and `kern-draw.js`. Node's built-in runner needs no new dependency; run it as `node --test rebuild/review/jstests/*.test.js` (`artifact_cycle.jstest_argv` says why the files are listed instead of the directory). There is no headless-browser test (no Playwright or puppeteer); the HTML sanity check and a manual serve-and-click pass cover integration. `render.test.js` and `slim.test.js` read the §7 fixture corpus in `rebuild/review/fixtures/`, the same units the Python contract tests check, and `render.test.js` also reads `jstests/explain-samples.json`, explain dumps copied from a real build. `kern.test.js` and `kern-draw.test.js` read the kerning view's fixtures there, which the Python tests hold to the inventory's and `GET /kern`'s shapes (the fixtures' README).

## 6. Gates

A review-app change passes the gates the artifact cycle runs, and `doc/testing.md` maps each gate to the change it answers for. The app's own checks run inside them: `gate:js` runs the §5.2 suite, and `make test-rebuild` runs `node --check` over every shipped script and the HTML sanity check on `index.html` (both in `rebuild/test_review_build.py`) and checks the `Cache-Control: no-store` header the review server sends on every file (`rebuild/test_review_autosave.py`). One gate is this design's own:

- **Determinism**: two consecutive builds of the same inputs produce a byte-identical manifest, shards and sidecars, `generated_at` included, because the stamp is the latest input mtime (`build._generated_at`). `test_two_builds_of_the_same_inputs_write_the_same_bytes` in `rebuild/test_review_diff_units.py` checks it.

## 7. The engine↔frontend contract

The executable authority is `check_manifest`, `check_unit`, `check_shards`, and `check_output_dir` in `rebuild/review/build.py`; a change must satisfy them, not this section. Where this section and the checkers disagree, the checkers are right.

The build runs all of `check_unit` and the cross-unit predicates (`_CorpusCheck` in `build.py`) over the dicts it serialized, and `check_output_dir`, which re-reads a finished corpus, runs them with `check_manifest` and the checks on the files beside the manifest. The contracts lane requires an empty error list from it over real table-diff builds of the pipeline's mini fixture spec (`rebuild/test_review_diff_units.py`), and `rebuild/test_corpus_checks.py` runs each predicate over the checked-in fixture corpus and over that corpus with one field broken at a time.

What follows describes the shape of the JSON and the design decisions in its fields; it does not list every field. The JSON is the only interface between the build (`rebuild/review/*.py`) and the frontend (`static/` and `jstests/`), and `rebuild/review/fixtures/` lets the frontend be built and tested against the contract without running a build.

### 7.1 `manifest.json`

The example is abridged; `check_manifest` checks every required key.

```json
{
  "format": "ams-review-manifest/2",
  "mode": "table-diff",
  "generated_at": "2026-11-26T09:08:33Z",
  "repo_head": "7fd5966",
  "source": {"snapshot": "var/keep/review-snapshot/<digest>", "new": "rebuild/out/m1"},
  "baseline": {"commit": "bb61f6e003b0…", "taken_at": "2026-11-25T18:02:11Z", "digest": "…", "present": true},
  "fonts": {
    "before": {"file": "fonts/before.otf", "family": "AMS Review Before", "source": "var/keep/review-snapshot/<digest>/M1.otf", "sha256": "…", "upem": 550},
    "after": {"file": "fonts/after.otf", "family": "AMS Review After", "source": "rebuild/out/m1/M1.otf", "sha256": "…", "upem": 550}
  },
  "configs": ["default", … the rest of `tablediff.SETTLED_CONFIGS` …],
  "batch_size": 300,
  "human_unit_ids": ["u-AZd95bckcbg", …],
  "totals": {"units": …, "rows": …, "batches": …},
  "machine_approved": {
    "units": …,
    "rows": …,
    "method": "Shaped with uharfbuzz in both fonts, kern-neutral, under every config in the unit's set; …",
    "by_class": {"<class-id>": …},
    "checks": {"ink_identical": {"units": …, "rows": …, "method": "…"}, "picture_identical": {…}}
  },
  "table_rows": {"default": {"settlement": {"changed": …}, "joins": {"added": …, "removed": …}, "letters": …, "texts": …}},
  "font_changes": {"glyphs": {"outline": …}, "kern": {"changed": …}},
  "classes": [
    {
      "id": "joins",
      "title": "Joins",
      "summary": "Two settled cells meet differently: …",
      "section": "open-questions",
      "no_verdict": false,
      "why": "…what a unit of the class is…",
      "unit_count": …,
      "row_count": …,
      "machine_approved_count": …,
      "machine_checks": {"ink_identical": …, "picture_identical": …},
      "shards": ["units/joins.json"],
      "batches": […]
    }
  ],
  "build_command": "uv run python -m rebuild.review.build",
  "serve_command": "uv run python -m rebuild.review.serve"
}
```

Types:

- `shards` is a nonempty list of the class's parts in concatenation order: one entry for a class that fits in a single file, and `units/<class-id>.000.json`, `units/<class-id>.001.json`, … for one that does not.
- All counts are integers.
- `batches` lists the zero-based global batch indices the class's human units occupy, and `totals.batches` counts human batches.
- `machine_approved.by_class` lists only classes with a nonzero count, while every class carries `machine_approved_count` (possibly 0).
- `classes` is in triage order: by `section` (Open questions, then No review needed; `audit.SECTIONS`), then `diff_units.CLASS_IDS` order, and holds only the classes that have units. `audit.class_section` decides each class's `section` from its `no_verdict` and human unit count, and every reader takes it from here instead of re-deriving it. `title`, `summary` and `why` are the copy `diff_units.CLASSES` gives the class.
- `baseline` is `{commit, taken_at, digest, present}` of the snapshot the corpus was built against (`present: false`, and the other three null, with no snapshot; `commit` null too while the accept that took it is not committed). `table_rows` holds each diffed configuration's changed rows by table and bucket with the letters whose rows changed and the texts shaped, and `font_changes` the redrawn glyphs by what changed and the moved kern pairs by bucket. `inputs_fingerprint` maps `fingerprint.TABLE_DIFF_COMPONENTS`, and `configs` is every settled configuration, the universe each unit's config badge is resolved against.

### 7.2 Unit shard (the parts the class’s `shards` list names) — an array of units, the parts concatenating in id order

Within a class, the parts hold the fragments in id order (§2.1); the manifest's `human_unit_ids` gives the triage order. The example is the fixture's `u-MG7BYctpZgY` with its `content_key` cut short; `check_unit` checks every required key.

```json
{
  "id": "u-MG7BYctpZgY",
  "content_key": "79211e21…",
  "ink_identical": false,
  "picture_identical": false,
  "no_verdict": false,
  "kerning": false,
  "cluster": "c-5e0d1a22",
  "class": "joins",
  "group": "qsTea:qsOy",
  "row_key": "join qsTea.half.ex-y5 + qsOy.en-y5: none -> x-height+0",
  "codepoints": "200C:E652:E679",
  "text_entities": "&#x200C;&#xE652;&#xE679;",
  "notation": "◊ZWNJ ·Tea·Oy",
  "notation_tokens": ["◊ZWNJ", "·Tea", "·Oy"],
  "configs": ["ss03"],
  "config_note": "only when ss03 is on and ss05 is off",
  "config_gate": [{"feature": "ss03", "state": "on", "text": "only when ss03 is on"}, {"feature": "ss05", "state": "off", "text": "and ss05 is off"}],
  "render_groups": [{"configs": ["ss03"]}],
  "kinds": ["added"],
  "before": {"glyphs": ["uni200C", "qsTea.half", "qsOy"], "junctions": ["break", "break"]},
  "after": {"cells": ["uni200C", "qsTea.half.ex-y5", "qsOy.en-y5"], "junctions": ["break", "y5"], "extensions": [0, 0]},
  "diff_positions": [2],
  "pair": {"left": 1, "right": 2},
  "pair_codepoints": [1, 2],
  "highlight": {
    "before": {"x_min": 0, "x_max": 550, "advance_total": 550},
    "after": {"x_min": 0, "x_max": 550, "advance_total": 550}
  },
  "boundary_marks": [{"index": 0, "kind": "zwnj", "x": 0}],
  "summary": "·Tea meets ·Oy (qsTea.half.ex-y5 + qsOy.en-y5) in a join at the x-height, where the accepted build has no such pair",
  "explain": "joins (added): ·Tea meets ·Oy in a join at the x-height, where the accepted build has no such pair.\n  configurations: ss03\n  text: ◊ZWNJ ·Tea·Oy (a candidate text)",
  "provenance": ["glyph_data/runes/qsTea.yaml:policy.extend[0]"]
}
```

Field semantics:

- `ink_identical`, `picture_identical`, `no_verdict` and `kerning` are required booleans on every unit. A unit that a machine check approves, or whose class takes no verdict, is outside the manifest's `human_unit_ids` and carries `cluster: null`, and the frontend shows it only behind the “Show units that need no review” toggle, with verdict controls disabled.
- `text_entities` is the rendered run as numeric character references, never raw PUA. The frontend injects it with `innerHTML` into the sample cells only.
- `junctions` arrays have one entry per inter-glyph gap (`break`, `lig`, or `yN`).
- `diff_positions` are the glyph indices whose cell or trailing junction changed.
- `pair` is the changed adjacency to highlight, or `null` for a change at one position with no junction change, or a unit with no text.
- `pair_codepoints` is the pair's covered codepoint positions as an inclusive `[start, end]`, `null` when `pair` is null. It is computed at build time because ligatures make cell indices differ from codepoint positions.
- `notation_tokens` is the display-token list aligned one-to-one with codepoint positions: letter names like `·May` and the boundary tokens `◊ZWNJ`, `␣`, and `·`. Joining them with the notation spacing rule (letters concatenate, boundary tokens take a space on each side) reproduces `notation`. The frontend uses `pair_codepoints` and `notation_tokens` together to underline the pair on the notation and codepoints lines.
- `highlight` x-values and `boundary_marks[].x` are in font units; the frontend converts with `font-size / upem`.
- `config_gate` is null when the set covers every configuration of the roster, and when no short conjunction selects the set. Otherwise it is the §2.1 clause list, each clause `{feature, state, text}` with `state` either `on` or `off`. `config_note` is the clause `text`s joined by spaces (the contract checker enforces this), or the literal “only under: …” fallback when the gate is null. The frontend draws one chip per clause and renders each `text` as given, falling back to a single chip with `config_note`.
- `render_groups` partitions `configs` by rendered outcome; an extra group would render as a stacked before/after pair with its own feature settings.
- `summary` is the always-visible one-line summary in rune-name notation. `explain` is preformatted display text.
- `content_key` (required) is the unit's identity digest (`diff_units.unit_identity`, §2.3), and the unit's id is derived from it (§2.1).
- `row_key` (required) names what the unit shows in the tables' own names: a cell change's letter and cells, a join row's cells and values, a redrawn glyph's name and what changed in it, or a kern pair's sides and values, each change of a merged unit in turn, separated by semicolons. `check_unit` holds a unit that names a join row to the `joins` class and one that names none out of it, and a `glyphs` or `kerning` unit to naming only glyphs or only kern pairs (§2.3).
- `kerning` is true exactly on a unit of the `kerning` class, which the build shapes and the app draws with kerning on.
- All strings are NFC, and all field names are snake_case.

The shards under `rebuild/review/fixtures/` hold a handful of hand-written units that cover the contract's branches; `test_fixture_units_exercise_the_contract_branches` in `rebuild/test_review_build.py` lists the branches. The same checker validates the fixtures and real output. The app's node tests (§5.2) read the same units; the fixture's README says what each reader needs from it.

### 7.3 `verdicts.json`

As in §4.1. `verdicts.js` writes it, and the store's reader (`rebuild/review/verdict_store.py`) and the strict Import hold it to the record rule (`rebuild/review/fixtures/verdict-record-samples.json`).

### 7.4 The app sidecars — `app-units.ndjson.gz` and the locator pair

These three files are projections of the shard fragments §7.2 defines, not a second copy of them. `rebuild/review/app_index.py` writes them after the manifest so they can carry its stamp, gzipped with fixed mtimes so two builds of the same inputs are byte-identical. The index and the locator table are NDJSON whose first line is a header `{format, manifest_sha256, generated_at, units}`, with formats `ams-review-app-index/1` and `ams-review-app-locator/2`; the table's header also carries `blocks`, `block_rows`, and `rows_bytes`. The app rejects a file whose header names a different `generated_at` instead of reading its byte spans, because one build's byte spans point at different bytes in the next.

`app-units.ndjson.gz` has one row per human unit, which is `manifest.human_unit_ids`, in shard order. Each row holds only the fields the app reads across the whole queue; `app_index.app_row` is the authority on the key list. Everything a card draws from its own record is left out: `explain` and `provenance` (§2.2), and also `text_entities`, `highlight`, and `after`, which the card fetches from its record when it renders. The machine-check flags and `no_verdict` are asserted false and dropped, since `build.check_unit` ensures no human unit has any of them.

In place of the dropped fields, every row of the index and of the locator ends with an address: `shard_part`, `byte_start`, and `byte_length`, which index `manifest.classes[…].shards` and the bytes of that part on disk. So `build._write_shard`'s framing is a byte-addressing contract as well as a serialization format. A fragment's bytes are a standalone JSON element with no enclosing punctuation mixed in, and `ensure_ascii=True` under a utf-8 handle makes a character offset equal to a byte offset, so `bytes[byte_start:byte_start + byte_length]` parses on its own. `rebuild/test_app_index.py` slices every fragment of a built corpus back out through its span, which catches a change to the dump's `indent`, `ensure_ascii`, or `separators` that would break the addressing.

`app-locator-rows.ndjson.gz` holds `{id, class, shard_part, byte_start, byte_length}` for every unit the index omits (machine-approved and no-verdict), in shard order. It is written as a sequence of gzip members of at most `app_index.LOCATOR_BLOCK_ROWS` rows each, and a member never spans two classes, so each member decodes on its own from its byte span. `app-locator.ndjson.gz` is the table of those members, one line per block: `{class, byte_start, byte_length, first, last, units}`, giving the span inside the rows file and the ids of the block's first and last rows. Within a class the blocks are disjoint and ascend by id, because the build writes a class's fragments in id order. Across classes the id ranges overlap, so the file is in shard order with a table rather than in one global id order: a fold reads its class's blocks in sequence, and a deep link binary-searches each class's blocks for the one member per class that could hold its id. The rows file has no header and is stamped through the table, whose `rows_bytes` is the length it must have. `rebuild/test_app_index.py` decodes every block from its span and compares it with the table, and `app_index.artifact_is_current` rejects a table whose rows file has any other length. A tab keeps the table in memory and fetches the rows one block at a time, never the whole file.
