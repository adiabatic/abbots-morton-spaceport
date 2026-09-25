# Review surface plan (design §11, first workload: the M1 migration baseline diff)

This is the design record for the treaty-diff review app: why the surface has its shape, and the contracts it was built against. Its inputs are design §11, §8, §10.5 and §6.3. The app is built under `rebuild/out/review/` and served on port 7294.

`rebuild/review/README.md` describes what the app does today: commands, keyboard map, triage flow, and the machine-approval and deduplication mechanisms. The checkers in `rebuild/review/build.py` are the executable contract. Counts are not kept here. The last accepted review census is `rebuild/review-census-pins.json`, which every artifact-cycle pass refreshes from the build's census sidecar (`uv run python -m rebuild.review.census --update` is the manual form). A build's totals (`totals`, `machine_approved`, and the `secondary_seams` census) are in `rebuild/out/review/manifest.json`, and `make verdict-ready` reports adjudication status. The `rebuild/review/*.py` module docstrings and the README cite this file's section numbers, so sections are never renumbered.

## 1. Architecture

### 1.1 Source layout (committed-shape, under `rebuild/review/`)

`rebuild/review/` is a package; the README's “Source layout” bullet lists its files. Two ingestion front ends produce the same unit model. `audit.py` (M1 mode) loads `rebuild/out/m1/divergence-audit.tsv` and `rebuild/m1-divergences.yaml`, dedupes the rows to units, and orders them. `tablediff.py` (table-diff mode, §2.3) diffs two settlement/treaty table directories by key. Everything after ingestion is the same in both modes: enrichment, the three verdict drafters, the ink comparison, the generation CLI, and the triage-YAML export.

The server, `serve.py`, runs the same livereload server and no-store static handler as `tools/serve.py`, over `rebuild/out/review/`, and adds the `/autosave` and `/status` endpoints. `rebuild/review/fixtures/` holds a hand-written miniature surface (a `manifest.json` and two unit shards) that satisfies the §7 contract, so the frontend and the contract checker can run without a build. Python tests are `rebuild/test_review_*.py` and their neighbors (§5.1). The pure ES modules have `node --test` tests under `rebuild/review/jstests/`.

The only import from outside `rebuild/` is the data-expect parser in `test/test_shaping.py`, loaded read-only by `drafts._import_test_shaping()`, which follows `rebuild/validation/pins.py`'s helper of the same name.

### 1.2 Output layout (generated, gitignored via the existing `rebuild/out/` rule)

```text
rebuild/out/review/
  index.html            copied from static/
  app.css  app.js  …    the rest of static/, copied verbatim
  manifest.json         generation metadata, class index, font records
  units/<class-id>.json one shard per nonzero class, split into .000.json, .001.json, … parts when large
  fonts/before.otf      copy of site/AbbotsMortonSpaceportSansSenior-Regular.otf
  fonts/after.otf       copy of rebuild/out/m1/M1.otf
```

The build also writes other files beside the manifest, among them the app sidecars (§7.4), the plumbing's unit index (`units-index.ndjson.gz`), the unit cache's store (`unit-cache.ndjson.gz`), and `census-facts.json`.

A shard part is capped at `build.SHARD_PART_BYTES`. The app parses each file it fetches as one JavaScript string, and a body longer than V8's `String::kMaxLength` (2**29 − 24 bytes under pointer compression) reaches `JSON.parse` as an empty string instead of an error. A class that fits in one part keeps the bare name. A larger class is written as contiguous three-digit parts numbered from `000`, and the manifest's `shards` list names them in concatenation order.

`manifest.json` records each font copy's source path and sha256. The site OTF is byte-identical to the one the oracle records as `font_sha256`, so “before” is the font the baseline was extracted from. The directory is self-contained, and deleting and rebuilding it is always safe.

### 1.3 CLI surface (documented on the generated page itself, check.html-style)

The everyday commands (`make review-build`, `make review-serve`, and the export CLI) are in the README's Commands block. Two more commands serve the second input shape and its accept step:

| Task                       | Command                                                                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Build in table-diff mode   | `uv run python -m rebuild.review.build --mode table-diff --baseline <dir> --new <dir> --before-font <otf> --after-font <otf>`          |
| Snapshot an accepted state | `uv run python -m rebuild.review.build snapshot --tables rebuild/out/m1 --font rebuild/out/m1/M1.otf --to rebuild/out/review-baseline` |

Build and serve are separate commands because the server is long-running. `serve.py` prints the build command and exits when the directory has no `manifest.json`. The two servers run side by side: port 7293 serves `site/` and port 7294 serves the review app. Both send `Cache-Control: no-store`, because a stale cached OTF would make every visual judgment wrong without any sign of it. The build suppresses warnings, `spec_load`'s `SpecWarning`s among them, while it loads the spec.

## 2. Data model

### 2.1 Units, dedupe, ordering

A unit is one (`codepoints`, `baseline`, `new`) triple and covers every audit row that shares it. The ledger class can differ by config: a unit carries the per-config map in `config_classes`, and its own class is the single matched class, or UNMATCHED when any config leaves it unmatched. Because the triple compares glyph names, a config that only renames a glyph can split one visual question into two units; `audit.merge_ink_duplicate_units` folds such siblings back together when their ink is identical under every config.

Each unit carries its config list, a `config_gate`, and its prose form `config_note`. Both are null when the set covers every non-ss10 acceptance config, which is the common case. Otherwise the gate is the minimal conjunction of feature on/off constraints that selects exactly that set, on-constraints first, one clause per constraint, each clause carrying the text the badge prints. `build.config_badge` is the authority, including the literal “only under: …” fallback for a set no short conjunction selects. `render_groups` partitions the configs by rendered outcome. Under the M1 dedupe key it is always a single group; extra groups would render stacked. A verdict applies to every (config, codepoints) audit row of the unit.

Units are ordered for triage by ledger class in the ledger's file order, then by group (the lead family pair, in code-point order), then by window length and codepoints, then by id (`audit.triage_key`).

**Kern-neutrality rule (binding for every review-surface comparison)**: the rebuild has no kerning until the design's §12 milestone, so the old font's `kern` feature is noise in any before/after comparison. Everywhere the review build shapes text (the ink comparison, highlight x-ranges, boundary-mark positions, pin-semantics validation) it passes `kern: False` to HarfBuzz for both fonts through `ink.kern_neutral`, which adds kern-off to the config's stylistic-set features. The frontend renders both sample columns with `font-kerning: none`, alongside each row's inline `font-feature-settings`. The before-font highlight pens come from a live kern-neutral shaping, because the §13.1 subset rows' positions were extracted with kerning on; the glyph identities are still checked against the subset row. The rule changes nothing on the after font today, but it is explicit so it still holds after §12, where kern differences get their own review. Other rebuild consumers (oracle conformance, pin replay) keep their own shaping; the rule applies only in `rebuild/review/`.

**Ink-identical machine approval (`ink.py`)**: at build time every unit is shaped in both fonts under every config in its set, with uharfbuzz through `rebuild.validation.shaping.Shaper` and kern-neutral. Each glyph's outline is recorded with fontTools' `DecomposingRecordingPen` and placed at the cumulative `x_advance` plus the glyph's `x_offset`/`y_offset`. A unit whose sorted placed pieces (`ink_pieces`) compare equal under every config is `ink_identical: true`. Both fonts then render it identically and only glyph names differ, so the build approves it without a human. The picture channel below it reads `config_diff`'s identity sentinel, the same picture-grain delta the echo key and `ink_deltas` digest. `rebuild/test_review_ink.py` checks, over a stride of the frozen mini-bundle windows, that the sentinel equals the whole-run cell comparison and follows from piece identity. The manifest's `machine_approved` record holds the machine-approved unit and row totals, per class and per channel. In table-diff mode the same comparison runs over each entry's witness string under its config. An entry with no witness has nothing to shape, so it stays `ink_identical: false` and goes to a human.

**A unit's id is its content**: `unit_cache.unit_id_for` of the carry content key. The content key is the sha256 of the unit's carry projection (its non-presentation fields, `unit_cache.carry_projection`). The id is the key's first 64 bits written as eleven base58 symbols, zero-padded, behind the `u-` prefix: `u-3mJ7kPq2Xw9`. The base58 alphabet is Bitcoin's (`123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz`: the digits and both cases minus 0, O, I and l), and case matters. The id says nothing about the unit's place in the queue. So the same window content has the same id on every surface it appears on, a fragment's bytes depend only on its own content and the ledger, and a verdict follows its unit across rebuilds by id (`rebuild/tools/carry_verdicts.py`). Echo groups get their ids the same way from their own key (`unit_cache.echo_id_for`), and cluster ids are hashes of their inputs. Within a class shard, fragments are in id order, which is the order the app's locator binary-searches.

**Batches cover the human workload only**: the manifest's `human_unit_ids` is the triage index. It lists, in triage order, every unit that no machine channel approves and no ledger class exempts. A batch is a fixed slice of 300 of it, and `audit.batch_of` is the one rule that turns a position into a batch number. No fragment carries its position or its batch. The app-index rows (§7.4) carry each human unit's `order` and `batch`. Machine-approved and no-verdict units are outside the index and are never paged to a human. The manifest's `machine_approved` record (units, rows, the verification method, per-class counts) and each class's `machine_approved_count` let sidebar counts, batch labels, and progress denominators count only the human workload, while the machine-approved total is shown in the header strip's surface-totals popover.

### 2.2 Encoding: sharded JSON, everything precomputed

Decision: **one `manifest.json` plus one JSON shard per ledger class, with everything computed at build time.** A unit runs to about 1.5–3 KB with its explain text and drafts, so a single JSON file for the whole surface would be too large to load at once. Each worker batches its settle and explain work through the Rust kernel before it enriches the units. Provenance, seams, highlight offsets, and all three verdict drafts are computed at build time. The browser does not compute any of them; it renders and collects verdicts. There is no server-side logic and no explain endpoint.

The app boots from a slim index of the human units (§7.4). The index leaves out everything a card draws from the unit's own record: `explain`, `provenance`, and `drafts`, and also the sample text, the pair band, and the settled cells. When a card renders, and when its explain panel opens, the app reads the record with an HTTP Range request against the shard the build wrote, using the byte span in the index row. The directory stays static, and the tab holds the queue, not the corpus.

Per-unit precomputed fields (full contract in §7): notation, before/after facts (glyphs or cells, seams, extensions), divergent positions and the primary pair, highlight x-ranges in font units for both fonts, the explain text for divergent positions, the deduped provenance pointers, exemplar status, and the three drafts. The x-ranges come from `hmtx` advances, so the frontend draws the pair highlight with `px = units × font-size / upem` and never measures text.

**Secondary seams and home resolution**: a longer unit can contain divergent adjacencies besides its primary pair: the other divergent gaps, plus a neighbor seam for each divergent position no gap covers (the same fallback the primary pair uses). The build emits each as a `secondary_seams` entry with per-side x-range rects computed like the primary highlight, plus the seam's **home**. The home is the shortest unit that:

- has a codepoint string that is a substring of this unit's and contains the seam,
- has the same before and after outcomes at the corresponding positions (glyph identities, covering spans after offset adjustment, and seam tokens), and
- has that seam as its own primary pair, so the same behavior is its primary judgment.

Ties break to the lowest unit id. When the home is ink- or picture-identical, the marker is suppressed: the divergence is an invisible name-grain rename, and the page promises that unmarked regions have nothing visible to judge. When no home exists, the marker is still emitted with `home: null`, so it is never silently unmarked. The manifest's `secondary_seams` census counts units with visible markers, homed seams, home-less seams, and suppressed seams. The contract checker validates the field shape and that every named home is a unit in the output. The frontend draws each visible seam as a dimmer dashed band in both columns with a chip that links to the home, or reads “only here” for `home: null`, and never on machine-approved renderings. A home-less seam is judged in this unit, so the frontend also underlines its tokens on the notation and codepoints lines with a `.seam-mark` span in the band's dashed amber (§3.1).

### 2.3 The general table-vs-table treaty-diff mode

`tablediff.py` implements design §8's diff as the second input shape behind the same unit model:

- Settlement key (`config`, `input`, `backtrack`, and up to four lookahead classes) → (`outcome`, `joint`, `provenance`); treaty key (`config`, `left`, `right`) → (`junction`, `extension`, `kern`).
- Rows are classified as added, removed, or changed. Removals and additions that share (`config`, `input`) are paired, so a re-partitioned context renders as one regrouped row. Settlement changes that move only provenance go to a low-priority bucket.
- Every changed row needs a witness string to render. `tablediff.WitnessIndex` settles every sequence up to depth 5 per config, shortest first, and records the first sequence that matches each row.
- Both the diff and the snapshot read only the tables of configurations in `tablediff.SETTLED_CONFIGS` (the settled configurations, without the ss10 overlay), so a table left under the name of a configuration the build does not settle is never diffed or accepted.
- A **baseline snapshot** is what the `snapshot` subcommand writes: the per-config `settlement-*.tsv` and `treaties-*.tsv`, the OTF they shipped with, and a `snapshot.json` recording sha256s, source paths, and the repo HEAD. To accept a state after review, re-run `snapshot` over the new tables; the next migration's `--baseline` points at it. This follows the `site/before/` workflow.

Both modes produce the same shard JSON. The frontend reads the mode only from the manifest's `mode` field and the class metadata: table-diff units carry bucket ids (`added`, `removed`, `regrouped`, `changed`, `provenance-only`) in place of ledger class ids.

## 3. Page UX

The app is a tool for one person triaging the whole divergence surface over several hours. Its chrome follows the `site/` style (`light-dark()`, system fonts, Menlo for code), and the samples use the fonts under test. A reviewer should be able to clear a batch from the keyboard alone.

### 3.1 Rendering (§11 requirements, with the proven mechanics)

- **Dual @font-face**: families `AMS Review Before` and `AMS Review After` over `fonts/before.otf` and `fonts/after.otf`. Rows are a grid of label, before sample, and after sample with `align-items: baseline` and sticky column headers, as in check.html. Samples get `-webkit-font-smoothing: none; font-smooth: never;`, and the prose chrome sets `subpixel-antialiased`.
- **Checkered background**: `--font-size: 88px` gives 8 px per font pixel (50 units at upem 550), so check.html's 16 px checker with `background-position: 0 5.6px, 8px 13.6px` applies unchanged. If the size changes, recompute the offsets.
- **Per-row features**: JS sets `style.fontFeatureSettings` on the sample pair from the unit's primary render group (`ss02+ss03` → `"ss02" 1, "ss03" 1`; `default` → `normal`) through `render.featureSettingsValue`. There is no strip listing the unit's configs, because most units diverge under every non-ss10 config and the list says nothing. When `config_gate` is non-null, a badge shows one inert chip per clause, in the stylistic set's color, lit for an on-constraint and muted for an off-constraint, glossed with the manifest's `feature_descriptions` entry. `configGateChips` in `render.js` derives the chips and renders each clause's `text` as given, without re-parsing `config_note`. The badge marks the cases that matter for judgment (ss03-gated, ss03-excluded, ss10-only, and narrower conjunctions), which also explain the row's `font-feature-settings`. Each chip's title lists the full config set, and the docket's cluster headers reuse the chips. If a unit ever carries more than one render group, each extra group's before/after pair renders below the first with its own label and feature settings.
- **The pair under review is highlighted**: wrapping part of a run in a span breaks shaping, so the highlight is drawn outside the text, as an absolutely positioned underline band under the divergent pair in each sample, placed from the precomputed font-unit x-ranges (§2.2). The band uses a high-contrast accent that meets the 3:1 non-text contrast rule in both color schemes. The same `--hot` color underlines the pair on the label's notation and codepoints lines through a `.pair-mark` span. The covered codepoint span is computed at build time (`pair_codepoints`, with `notation_tokens` aligned one-to-one with codepoint positions), because ligatures make glyph positions differ from codepoint positions. A unit with no primary pair leaves those lines unmarked. Homed secondary seams never mark the text lines, since they are judged at their home unit. A home-less (“only here”) seam gets its own `.seam-mark` underline, dashed in the secondary band's amber and set below the pair-mark line where the two overlap. Its codepoint span is derived in the browser from `after.cells` (a formed ligature covers two codepoint positions) and checked against `pair_codepoints`; on any disagreement the seam marks are dropped so the wrong letters are never underlined.
- **ZWNJ**: emitted as a literal `&#x200C;` inside the run, so the real `uni200C` rules fire; browsers render it invisibly. The notation caption shows it as `◊ZWNJ`, and a dotted tick is drawn at its precomputed x position under the run. Space shows as `␣` in captions. All Quikscript text is stored as numeric character references in the JSON `text_entities` field, never as raw PUA characters.

### 3.2 Triage flow

- **Batches and grouping**: class, then family-pair group, then batches of 300 units (§2.1). The page shows one batch at a time. Groups within a batch are `<details>` folds with unit counts and a button that approves the whole group, which is useful because one unit stands for many audit rows. A sidebar lists classes with status, deduped and raw counts, the ledger `why`, and per-class progress.
- **One-key home-row verdicts**: the README's keyboard table is the key map, mirrored in the app's `?` help overlay. The four main verdict keys run along the left home row (`a` skip, `s` reject, `d` fine-either-way, `f` approve), and the verdict buttons sit in a four-column grid laid out like the keys, with Identical (`e`) above Either and Neither (`c`) below it. Keys are ignored while focus is in an input, textarea, or select, except `Escape`. The keys trigger real `<button>` elements, which have visible focus indicators and are keyboard-accessible per WCAG; one delegated `keydown` handler on the document runs the same code as a click. Auto-advance scrolls with `behavior: smooth`, or `auto` under `prefers-reduced-motion`. The approve and reject marks on the after sample are CSS driven by the row's `data-verdict`, so they appear on record or import, survive re-render, and clear on undo.

- **Reject follow-up popup**: `s` or the row's Reject button opens a small menu under that row's verdict buttons, and a second key records the reject, with the note unchanged or with a canned note that replaces it (the README's keyboard map lists the choices). `Escape` or a click outside cancels without a verdict, and every other shortcut is suppressed while the menu is open. The Neither verdict (`c`) opens a follow-up menu of the same kind. The menu state is a `rejectMenuOpen` context flag on the pure `actionForKey` in `keyboard.js`, so the key model stays unit-testable.

- **Per-row notes**: a text input per unit. The note is saved with the verdict and included in the drafted `why:` stubs.
- **Whole-unit verdicts**: a verdict always covers all of the unit's configs. There is no per-config verdict, because every config of a unit renders identically by construction, and a control that changes nothing visible would mislead. The `config_note` badge is the only per-config element on the page.
- **Progress**: a sticky header strip shows verdicted/total for the batch and overall, and the class sidebar shows per-class counts. `document.title` shows the current position, as tables.html's `updateTitle` does.
- **Copy-prompt preamble**: each unit has a copy button, as in check.html, that copies “I'm looking at rebuild/out/review/ unit `<id>` — `<codepoints>` (`<notation>`).” for pasting into an agent conversation.

### 3.3 URL state (and what stays out of it)

View state lives in `location.hash` as `URLSearchParams`, as in tables.html: `parseHash` and `writeHash` in `state.js`, and one `applyHashState` renderer driven by `hashchange`. For example: `#class=…&batch=N&unit=u-3mJ7kPq2Xw9&group=qsTea:qsOy&config=ss03&family=qsMay&status=unverdicted`. The filters are class, family (either side of the pair), config, and verdict status. Every view can be bookmarked, and a reload mid-batch returns to the same cursor.

**Verdicts are not in the URL and not in localStorage.** They are held in an in-memory `Map` keyed by unit id. The page exports them with a “Download verdicts.json” button in the §4.1 format, and re-imports a file with a file picker that merges by unit id and warns when the file's `manifest_generated_at` does not match the loaded manifest. A reload loses no work: the store debounces and POSTs each change to the server's `/autosave` endpoint, and every write is journaled (see the README's triage-flow section). The `beforeunload` warning and the unexported-count reminder are the fallback for when autosave is unavailable.

## 4. Verdict exports — closing the opinions-become-pins loop

`drafts.py` computes all three drafts per unit at build time and they ship in the shard JSON; the browser only selects among them. The export has two stages:

1. **The page** exports `verdicts.json` (download or copy), the re-importable record of the reviewer's work.
2. **The CLI** (`uv run python -m rebuild.review.export verdicts.json --out tmp/review-triage.yaml`) joins the verdicts to the units, re-parses the expect strings of the selected pin and any-of drafts, and writes **one triage YAML with five sections** for a human to place. Nothing is applied to the corpus or the rune files automatically.

### 4.1 `verdicts.json`

```json
{
  "format": "ams-review-verdicts/1",
  "manifest_generated_at": "2026-06-10T17:02:11Z",
  "exported_at": "2026-06-10T18:40:02Z",
  "verdicts": [
    {"unit": "u-3mJ7kPq2Xw9", "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"},
    {"unit": "u-8nacGTcgMRS", "verdict": "reject", "note": "seam looks reached-for", "at": "2026-06-10T18:21:40Z"}
  ]
}
```

`verdict` is one of `approve`, `reject`, `either`, `identical`, `neither`, or `skip`. A verdict covers all of the unit's configs; the import ignores a `configs` field if a record has one.

### 4.2 The triage YAML (five sections)

`export.build_triage` is the authority on the file's shape. The example below is abridged: the file also carries a `machine_approved` section (counts, per-class counts, the verification method, and unit ids), and `review` carries more fields.

```yaml
review:
  mode: m1-audit
  source: rebuild/out/m1/divergence-audit.tsv
  exported_at: 2026-06-10T18:45:00Z
  counts: {approve: …, reject: …, either: …, identical: …, neither: …, skip: …, units_total: …, rows_covered: …}

pins:                       # one per approved unit: a whole-word data-expect pin
  - unit: u-3mJ7kPq2Xw9
    codepoints: "200C:E652:E679"
    text_entities: "&#x200C;&#xE652;&#xE679;"
    expect: "◊ZWNJ ·Tea+Oy"
    attribute: data-expect-noncanonically   # data-expect when the corpus already pins this text with data-expect
    stylistic_set: "03"                     # null for default; "02 05"-style for multi-set
    validated: {syntax: pass, semantics_after_font: pass}
    suggested_home: site/the-manual.html    # suggestion only; a human places the pin
    duplicate_of: null                      # set when the corpus already pins this text under this feature context; such a pin is flagged, not emitted as new
    note: ""

policy_edits:               # one per rejected unit: the one-line refuse/contract/prefer edit; a reject with no mechanical draft still appears, with keypath/suggested_record null and a no_mechanical_draft note
  - unit: u-8nacGTcgMRS
    codepoints: "E650:E665"
    file: glyph_data/runes/qsMay.yaml
    keypath: policy.refuse[+]               # [+] = append to the list
    suggested_record: "{left: {rune: qsPea, ex: x-height}, why: 'TODO'}"
    names_provenance:                       # the records explain attributed the new outcome to (§6.3)
      - glyph_data/runes/qsMay.yaml:policy.extend[1]
    decided_stage: prefer
    why_stub: "Reviewer rejected the M1 outcome for E650:E665 (·Pea·May): seam looks reached-for"
    schema_valid: true

any_of:                     # one per fine-either-way unit: both behaviors as full expect strings
  - unit: u-DdcTojn1hba
    text: "qsPea qsOwe qsMay"               # _qs_text-ready family tokens
    features: {}
    candidates:
      - "·Pea ~x~ ·Owe ~x~ ·May"            # the rebuild behavior, first
      - "·Pea | ·Owe ~x~ ·May"              # the baseline behavior, also acceptable
    realized_as: _assert_expect_any         # the executable form until the corpus any-of connective (§10.5) exists
    note: ""

neither:                    # one per neither-verdicted unit: both behaviors look wrong; nothing is drafted
  - unit: u-2WvdGAWe6bX
    codepoints: "E652:200C:E652:E679"
    notation: "·Tea ◊ZWNJ ·Tea·Oy"
    note: "both joins look wrong; needs a fresh stance"
    names_provenance:                       # the records explain attributed the outcome to, where follow-up authoring starts
      - glyph_data/runes/qsTea.yaml:policy.extend[0]

identical:                  # one per identical-verdicted unit: the reviewer cannot see the flagged difference; nothing is drafted
  - unit: u-hRgMc2EJjbs
    codepoints: "E665:E679"
    notation: "·May·Oy"
    note: "the highlighted joins look the same to me"
```

### 4.3 Drafter rules

- **Pin drafter (approve)**: a whole-word pin with bare letter tokens and no variant assertions (design §10.5: “whole-word assertions remain the preferred cheap lock”). Tokens come from the notation map (`·Tea`, `◊space`, `◊ZWNJ`, and the namer dot per `doc/data-expect.md`). Connections come from the **after** settled seams: `y5` → `~x~`, `y0` → `~b~`, `y8` → `~t~`, `y6` → `~6~`, break → `|`, formed ligature → `+`. The attribute is `data-expect` when the corpus (`drafts.CORPUS_FILES`) already pins the same text under the unit's first config with `data-expect`, and `data-expect-noncanonically` otherwise. Stylistic-set scope goes in the `stylistic_set` value; in-string set scoping is §10.5 future work and is never drafted. Syntax is checked with `test_shaping.parse_expect`. Semantics are checked against the after font with the rebuild-side harness that `rebuild/test_validation_suite.py`'s corpus replay uses (`rebuild/validation/pins.py` and `rebuild/validation/shaping.Shaper`), never by monkeypatching the test module's `site/` font constants. A pin is expected to fail against the old font. A pin that fails `parse_expect` or the after font raises `DraftError` where it is drafted, so `pass` is the only value either field is ever written with. The drafter looks up the corpus for an existing `data-expect` on the same text under the same feature context and sets `duplicate_of` instead of emitting a second pin.
- **Policy drafter (reject)**: works from the precomputed explain trace. The target file is the rune file of the divergent position. The draft names every provenance record that decided the new outcome, plus `decided_stage`. The suggested record is the smallest one-line record that reverses the change (the counter-lever), chosen in this order:
  1. When a gap next to the divergent cell is joined in the new behavior but was a break in the baseline, and provenance is nonempty: a `refuse` of the anchor that reaches across that gap, scoped to the neighbor. An outcome a positive record produced gets a refuse, because only a refuse restores the break; a contract would shorten the extension but keep the join.
  2. When the divergent cell gained an extension on a join both fonts share and a `policy.extend` decided it: a `contract` by the same amount on that side.
  3. When the divergence is name-grain (both behaviors group the codepoints the same way and agree on every seam, so a refuse would break a join both fonts share) and provenance is nonempty: a `prefer` with `mode: absolute` that pins the baseline cell's entry and exit (read from the alias map) over the new cell's, or its stance when only the stance differs. A name-grain difference with no such record (post-ZWNJ locked twins, bind pullbacks, suppressed extensions) gets **no policy draft**; the export lists the reject with `keypath: null` and the unit's provenance for hand editing.
  4. Otherwise, with nonempty provenance: a `refuse` of the cell's exit (or stance) in the window.
  5. With empty provenance (the structural floor decided): a `prefer` of the baseline exit.

  A record that fails the rune schema under `rebuild/schema/` raises `DraftError`, so `schema_valid` is always true on a shipped draft. The `why:` stub names the unit's codepoints and notation and adds the reviewer's note. The draft is for a human to judge and is never applied.
- **Any-of drafter (either)**: both behaviors as full expect strings built by the engine, so the reviewer never writes syntax. The after behavior comes first and the baseline behavior second, and each must pass `parse_expect`. A unit whose two behaviors write the same string gets a single candidate. `features` comes from the config token. The export names `_assert_expect_any` (`test/quikscript_shaping_helpers.py`) in `realized_as` as the executable form until the corpus-level any-of connective exists.
- **Neither (no drafter)**: neither the old nor the new behavior is right, so nothing is drafted: no pin, no policy edit, no any-of. The export carries the unit's id, codepoints, and notation, the reviewer's note, and `names_provenance`, so follow-up authoring starts from the records that decided the outcome.
- **Identical (no drafter)**: the reviewer cannot see the flagged difference. Nothing is drafted; the export carries the unit's id, codepoints, and notation, and the reviewer's note. These entries report a divergence that is invisible to a human, which is feedback for the ink comparator and the highlight tooling, not a judgment on either font.

## 5. Testing strategy

### 5.1 Python (`rebuild/test_review_*.py` and their neighbors, in the contracts lane of `make test-rebuild`)

None of these tests reads the live surface. Every worked example takes its window from the frozen mini bundle under `rebuild/review/fixtures/mini/` (the `mini_bundle` and `example_units` fixtures in `rebuild/conftest.py`). What the build can check per unit, it checks during the build (`check_unit` and the cross-unit checks in `build.py`, the drafter's and enricher's own errors, and the verification sample, which recomputes a sample of cache-served units and compares them with what the cache served), so no test re-checks shipped shards.

- `test_review_audit.py`: TSV and ledger loading, the dedupe to units with per-config classes, deterministic ordering and batch slicing over the mini workload, and that the dedupe loses no rows. Every build rechecks row conservation by comparing the manifest's row total with the rows summed over its classes (`_SurfaceCheck.finish`, which `check_shards` also runs).
- `test_review_tablediff.py`: added/removed/changed classification on synthetic table pairs, pairing of removals and additions that share (`config`, `input`), provenance-only demotion, witness search re-settling to the changed row over the mini bundle's tables, that a table directory diffed against itself is empty, and the snapshot round trip (a snapshot's copy diffs empty against its source).
- `test_review_enrich.py`: the notation map against `doc/glyph-names.md`, divergent-position and pair selection on named mini-bundle windows, highlight x-ranges against hand-computed `hmtx` sums, and the secondary-seam home resolver over hand-built stubs.
- `test_review_drafts.py`: that the semantic validator rejects a wrong pin, each branch of the policy drafter on worked-example windows (contract, refuse, prefer on a name-grain divergence and on an empty trace, and no draft), any-of candidate ordering, and duplicate detection. What every drafted pin and record must satisfy is enforced by `DraftError` at drafting time (§4.3).
- `test_review_build.py`: the §7 contract checker over `rebuild/review/fixtures/` (the checker every build runs over its own output), the config-note badge, the app shell, `node --check` over every shipped `.js` file (skipped if node is absent), the export round trip (a synthetic `verdicts.json` with one verdict of each kind produces a triage YAML with the right members in each section), and the table-diff build.
- `test_review_ink.py`: the ink-identity comparator on mini-bundle windows shaped in the bundle's own font, that `signature` ignores glyph names (on the marker font), `delta_digest`, and the pixel-grain readings the standing approvals use.
- `test_surface_checks.py`: every `check_manifest`, `check_unit`, and `check_shards` predicate, run against the fixture surface as shipped and then with one field broken at a time. `test_app_index.py`: the two app sidecars and the byte spans that address the shards, over a mini build.

### 5.2 JavaScript

The ES modules in `static/` other than `app.js` (`state.js`, `keyboard.js`, `verdicts.js`, `render.js`, `docket.js`, `slim.js`, `status.js`, `preview.js`) are pure modules with no DOM access at top level, and each has a `*.test.js` under `rebuild/review/jstests/`. Node's built-in runner needs no new dependency; run it as `node --test rebuild/review/jstests/*.test.js`, since node v26 rejects the bare-directory form (`artifact_cycle.jstest_argv`). The tests cover hash round trips, that every keyboard binding dispatches the right action and is suppressed inside inputs, verdict, undo, and auto-advance transitions, and import merging including the manifest-mismatch warning. There is no headless-browser test (no Playwright or puppeteer); the HTML sanity check and a manual serve-and-click pass cover integration.

## 6. Gates

1. `make test-rebuild` passes.
2. `make test` passes, which shows `site/`, `test/`, and the existing build are unaffected.
3. `node --check` passes on all shipped JS, and `node --test rebuild/review/jstests/*.test.js` passes.
4. `index.html` passes the HTML sanity check (`test_index_html_sanity`, part of gate 1).
5. Both servers run at once, `make serve` on 7293 and `make review-serve` on 7294, and the page, a shard, and both OTFs are served with `Cache-Control: no-store`.
6. `rebuild/out/` stays gitignored.
7. `make prettier` has run after every Python change, and the Markdown passes `markdownlint-cli2`.
8. Determinism: two consecutive builds of the same inputs produce byte-identical manifest and shards (`rebuild/test_unit_cache.py`).

## 7. The engine↔frontend contract

The executable authority is `check_manifest`, `check_unit`, `check_shards`, and `check_output_dir` in `rebuild/review/build.py`; a change must satisfy them, not this section. Where this section and the checkers disagree, the checkers are right.

The m1 build checks every unit it computes. `check_unit` runs in two subsets (`CHECKED_AT` in `build.py`; `check_unit`'s docstring says which predicates run in each): `DRAFTED` in the worker that drafts the fragment, and `PATCHED` in the parent that writes it. A unit the unit cache served skips `check_unit`: the build serves a fragment only when its stamp equals the unit's `content_key`, and the build that drafted the fragment ran `check_unit` on it. The fields a later build re-patches onto a served fragment (`echo`, `cluster`, and the secondary seams) are not re-checked per unit (`_SurfaceCheck.unit`). The cross-unit predicates run over every unit (`_SurfaceCheck`). `check_output_dir`, which re-reads a finished surface, and the table-diff build, which runs `check_shards` over the dicts it serialized, run all of `check_unit`. `check_manifest` and the checks on files beside the manifest run through `check_output_dir`. The contracts lane requires an empty error list from it over a real mini-bundle m1 build (`rebuild/test_app_index.py`) and a real table-diff build (`rebuild/test_review_build.py`), and `rebuild/test_surface_checks.py` runs each predicate over the checked-in fixture surface and over that surface with one field broken at a time.

What follows describes the shape of the JSON and the design decisions in its fields; it does not list every field. The JSON is the only interface between the engine (`rebuild/review/*.py`) and the frontend (`static/` and `jstests/`), and `rebuild/review/fixtures/` lets the frontend be built against the contract without running a build.

### 7.1 `manifest.json`

The example is abridged; `check_manifest` checks every required key.

```json
{
  "format": "ams-review-manifest/2",
  "mode": "m1-audit",
  "generated_at": "2026-06-10T17:02:11Z",
  "repo_head": "7fd5966",
  "source": {
    "audit": "rebuild/out/m1/divergence-audit.tsv",
    "ledger": "rebuild/m1-divergences.yaml"
  },
  "fonts": {
    "before": {"file": "fonts/before.otf", "family": "AMS Review Before", "source": "site/AbbotsMortonSpaceportSansSenior-Regular.otf", "sha256": "3211a7a7…", "upem": 550},
    "after": {"file": "fonts/after.otf", "family": "AMS Review After", "source": "rebuild/out/m1/M1.otf", "sha256": "…", "upem": 550}
  },
  "configs": ["default", … the rest of `conform.ACCEPTANCE_CONFIGS`, the membership authority …],
  "batch_size": 300,
  "totals": {"units": …, "rows": …, "batches": …},
  "machine_approved": {
    "units": …,
    "rows": …,
    "method": "Shaped with uharfbuzz in both shipped fonts (kerning disabled — …) under every config in the unit's set; …",
    "by_class": {"<class-id>": …}
  },
  "classes": [
    {
      "id": "dangling-anchor-dropped",
      "status": "drift-accepted",
      "ink_identical": false,
      "why": "…the ledger's reviewed rationale, verbatim…",
      "unit_count": …,
      "row_count": …,
      "machine_approved_count": …,
      "shards": ["units/dangling-anchor-dropped.json"],
      "batches": []
    }
  ],
  "build_command": "uv run python -m rebuild.review.build",
  "serve_command": "uv run python -m rebuild.review.serve"
}
```

Types:

- `shards` is a nonempty list of the class's parts in concatenation order: one entry for a class that fits in a single file, and `units/<class-id>.000.json`, `units/<class-id>.001.json`, … for one that does not.
- All counts are integers.
- `batches` lists the zero-based global batch indices the class's human-workload units occupy, and `totals.batches` counts human batches.
- `machine_approved.by_class` lists only classes with a nonzero count, while every class carries `machine_approved_count` (possibly 0).
- `classes` is in ledger file order, which is triage order. In table-diff mode `classes` holds the diff buckets (`added`, `removed`, `regrouped`, `changed`, `provenance-only`) with `status: null` and a generated `why`.
- The class-level `ink_identical` flag is the ledger's reviewed classification. It is separate from the per-unit `ink_identical` boolean, which the build computes from the fonts.

### 7.2 Unit shard (the parts the class’s `shards` list names) — an array of units, the parts concatenating in triage order

Within a class, the parts hold the fragments in id order (§2.1). The example is abridged; `check_unit` checks every required key.

```json
{
  "id": "u-3mJ7kPq2Xw9",
  "ink_identical": false,
  "class": "marker-staging-ligature-formation",
  "group": "qsTea:qsOy",
  "codepoints": "200C:E652:E679",
  "text_entities": "&#x200C;&#xE652;&#xE679;",
  "notation": "◊ZWNJ ·Tea·Oy",
  "configs": ["ss03", "ss02+ss03", "ss02+ss03+ss05"],
  "config_note": "only when ss03 is on",
  "config_gate": [{"feature": "ss03", "state": "on", "text": "only when ss03 is on"}],
  "render_groups": [{"configs": ["ss03", "ss02+ss03", "ss02+ss03+ss05"]}],
  "kinds": ["ligation"],
  "exemplar": true,
  "before": {"glyphs": ["space", "qsTea_qsOy"], "seams": ["break", "lig"]},
  "after": {"cells": ["uni200C", "qsTea_qsOy/hapax/None/None/+locked"], "seams": ["break", "lig"], "extensions": [0, 0]},
  "diff_positions": [0],
  "pair": {"left": 0, "right": 1},
  "highlight": {
    "before": {"x_min": 0, "x_max": 1100, "advance_total": 1650},
    "after": {"x_min": 0, "x_max": 1100, "advance_total": 1650}
  },
  "boundary_marks": [{"index": 0, "kind": "zwnj", "x": 0}],
  "summary": "New: ·Tea+Oy now forms as one ligature (the old pipeline rendered the letters separately) — decided by the only surviving candidate (no policy record involved).",
  "explain": "…ExplainReport.render() text for the divergent positions…",
  "provenance": ["glyph_data/runes/qsTea.yaml:policy.extend[0]"],
  "drafts": {
    "pin": {"expect": "◊ZWNJ ·Tea+Oy", "attribute": "data-expect-noncanonically", "stylistic_set": "03", "syntax": "pass", "semantics_after_font": "pass", "duplicate_of": null, "suggested_home": "site/the-manual.html"},
    "policy": {"file": "glyph_data/runes/qsTea.yaml", "keypath": "policy.refuse[+]", "suggested_record": "{…one-line flow mapping…}", "names_provenance": ["glyph_data/runes/qsTea.yaml:policy.extend[0]"], "decided_stage": "prefer", "schema_valid": true},
    "any_of": {"text": "ZWNJ qsTea qsOy", "features": {"ss03": true}, "candidates": ["◊ZWNJ ·Tea+Oy", "◊ZWNJ ·Tea ~b~ ·Oy"]}
  }
}
```

Field semantics:

- `secondary_seams` is optional: `null` or absent when the unit has no visible secondary seam, and never present on machine-approved units. Otherwise it is a list of `{pair: {left, right}, before: rect, after: rect, home: <unit id> | null}` entries resolved by the §2.2 rules, with rects in the same font-unit form as `highlight`.
- `ink_identical` is required on every unit in both modes. A unit that any machine channel approves is outside the manifest's `human_unit_ids`, and the frontend shows it only behind the “Show no-verdict units” toggle, with verdict controls disabled.
- A machine-approved unit, or a unit of a no-verdict class (`audit.slim_fragment`), has a slim fragment: `explain`, `drafts`, and `highlight` (`audit.SLIM_OMITTED_KEYS`) are absent, not null, so the app can tell a slim fragment from a whole record with a blank field. `check_unit` enforces this in both directions: a human unit always has its explain material, and a slim unit never has any of it.
- `text_entities` is the rendered run as numeric character references, never raw PUA. The frontend injects it with `innerHTML` into the sample cells only.
- `seams` arrays have one entry per inter-glyph gap (`break`, `lig`, or `yN`).
- `diff_positions` are the glyph indices whose cell or trailing seam diverges.
- `pair` is the primary divergent adjacency to highlight, or `null` for a single-position divergence with no seam change.
- `pair_codepoints` is the primary pair's covered codepoint positions as an inclusive `[start, end]`, `null` when `pair` is null. It is computed at build time because ligatures make cell indices differ from codepoint positions.
- `notation_tokens` is the display-token list aligned one-to-one with codepoint positions: letter names like `·May` and the boundary tokens `◊ZWNJ`, `␣`, and `·`. Joining them with the notation spacing rule (letters concatenate, boundary tokens take a space on each side) reproduces `notation`. The frontend uses `pair_codepoints` and `notation_tokens` together to underline the pair on the notation and codepoints lines.
- `highlight` x-values and `boundary_marks[].x` are in font units; the frontend converts with `font-size / upem`.
- `config_gate` is null when the set covers every non-ss10 config, and when no short conjunction selects the set. Otherwise it is the §2.1 clause list, each clause `{feature, state, text}` with `state` either `on` or `off`. `config_note` is the clause `text`s joined by spaces (the contract checker enforces this), or the literal “only under: …” fallback when the gate is null. The frontend draws one chip per clause and renders each `text` as given, falling back to a single chip with `config_note`.
- `render_groups` partitions `configs` by rendered outcome. There is one group under the M1 dedupe key; an extra group would render as a stacked before/after pair with its own feature settings.
- `summary` is the always-visible one-line summary in rune-name notation. `explain` is preformatted display text.
- `stylistic_set` is `null` or the space-separated, zero-padded form (`"02 05"`).
- `content_key` (m1-audit mode, required) is the sha256 of the unit's carry projection (`unit_cache.carry_content_hash`). The unit's id is derived from it (§2.1), and the unit cache compares it with the stamp in its store before it serves a fragment.
- All strings are NFC, and all keys are snake_case.

The shards under `rebuild/review/fixtures/` hold a handful of hand-written units that cover the contract's branches; `test_fixture_units_exercise_the_contract_branches` in `rebuild/test_review_build.py` lists the branches. The same checker validates the fixtures and real output.

### 7.3 `verdicts.json`

As in §4.1. `verdicts.js` writes it and `export.py` reads it; the export round-trip test in §5.1 covers the two together.

### 7.4 The app sidecars — `app-units.ndjson.gz` and the locator pair

These three files are projections of the shard fragments §7.2 defines, not a second copy of them. `rebuild/review/app_index.py` writes them after the manifest so they can carry its stamp, gzipped with fixed mtimes so two builds of the same inputs are byte-identical. The index and the locator table are NDJSON whose first line is a header `{format, manifest_sha256, generated_at, units}`, with formats `ams-review-app-index/1` and `ams-review-app-locator/2`; the table's header also carries `blocks`, `block_rows`, and `rows_bytes`. The app rejects a file whose header names a different `generated_at` instead of reading its byte spans, because one build's byte spans point at different bytes in the next.

`app-units.ndjson.gz` has one row per human unit, which is `manifest.human_unit_ids`, in shard order. Each row holds only the fields the app reads across the whole queue; `app_index.app_row` is the authority on the key list. Everything a card draws from its own record is left out: `explain`, `provenance`, and `drafts` (§2.2), and also `text_entities`, `highlight`, and `after`, which the card fetches from its record when it renders. The three machine-channel flags and `no_verdict` are asserted false and dropped, since `build.check_unit` ensures no human unit has any of them.

In place of the dropped fields, every row of the index and of the locator ends with an address: `shard_part`, `byte_start`, and `byte_length`, which index `manifest.classes[…].shards` and the bytes of that part. So `build._write_shard`'s framing is a byte-addressing contract as well as a serialization format. A fragment's bytes are a standalone JSON element with no enclosing punctuation mixed in, and `ensure_ascii=True` under a utf-8 handle makes a character offset equal to a byte offset, so `bytes[byte_start:byte_start + byte_length]` parses on its own. `rebuild/test_app_index.py` slices every fragment of a built surface back out through its span, which catches a change to the dump's `indent`, `ensure_ascii`, or `separators` that would break the addressing.

`app-locator-rows.ndjson.gz` holds `{id, class, shard_part, byte_start, byte_length}` for every unit the index omits (machine-approved and no-verdict), in shard order. It is written as a sequence of gzip members of at most `app_index.LOCATOR_BLOCK_ROWS` rows each, and a member never spans two classes, so each member decodes on its own from its byte span. `app-locator.ndjson.gz` is the table of those members, one line per block: `{class, byte_start, byte_length, first, last, units}`, giving the span inside the rows file and the ids of the block's first and last rows. Within a class the blocks are disjoint and ascend by id, because the build writes a class's fragments in id order. Across classes the id ranges overlap, so the file is in shard order with a table rather than in one global id order: a fold reads its class's blocks in sequence, and a deep link binary-searches each class's blocks for the one member per class that could hold its id. The rows file has no header and is stamped through the table, whose `rows_bytes` is the length it must have. `rebuild/test_app_index.py` decodes every block from its span and compares it with the table, and `app_index.artifact_is_current` rejects a table whose rows file has any other length. A tab keeps the table in memory and fetches the rows one block at a time, never the whole file.
