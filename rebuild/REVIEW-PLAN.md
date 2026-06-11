# Review surface plan (design §11, first workload: the M1 migration baseline diff)

Plan for the treaty-diff review app. Inputs: `rebuild/recon/review-page.md`, `rebuild/recon/review-data.md`, design §11 + §8 + §10.5 + §6.3, and the binding user decisions (output under a new `rebuild/out/review/`, served on port 7294, nothing outside `rebuild/` + `tmp/` touched, no Makefile changes, never commit).

## 1. Architecture

### 1.1 Source layout (committed-shape, all new files under `rebuild/review/`)

A package, not a single module — the engine has five separable concerns and two implementers work in parallel.

| Path                          | Owner | Contents                                                                                                                                                                                                 |
| ----------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rebuild/review/__init__.py`  | A     | Empty package marker.                                                                                                                                                                                    |
| `rebuild/review/audit.py`     | A     | M1 mode: load `rebuild/out/m1/divergence-audit.tsv` + `rebuild/m1-divergences.yaml`, dedupe to units, group and order them.                                                                              |
| `rebuild/review/tablediff.py` | A     | General mode: key-aligned diff of two settlement/treaty table directories, remove+add pairing, provenance-only demotion, witness-string search. Produces the same unit model as `audit.py`.              |
| `rebuild/review/enrich.py`    | A     | Rune-name notation, old seams from the baseline subsets, the explain precompute (new seams, extensions, eliminations, render text), divergent-position computation, highlight offsets from font metrics. |
| `rebuild/review/drafts.py`    | A     | The three verdict drafters (pin, policy edit, any-of) plus their validation.                                                                                                                             |
| `rebuild/review/ink.py`       | A     | Ink-identity comparison: shape each unit in both fonts under every config in its set, compare the placed outlines, and machine-approve units whose ink is pixel-identical (only names differ).           |
| `rebuild/review/build.py`     | A     | The generation CLI: assembles units, writes `rebuild/out/review/` (manifest, shards, copied fonts, copied static app). Also the `snapshot` subcommand.                                                   |
| `rebuild/review/export.py`    | A     | The verdicts-to-triage-YAML CLI.                                                                                                                                                                         |
| `rebuild/review/serve.py`     | A     | Port-7294 livereload server, a line-for-line sibling of `tools/serve.py` over `rebuild/out/review/`, watch globs `**/*.html`, `**/*.css`, `**/*.js`, `**/*.otf`, `**/*.json`.                            |
| `rebuild/review/static/`      | B     | The app sources, copied verbatim by `build.py`: `index.html`, `app.css`, `app.js`, and pure-logic ES modules `state.js`, `keyboard.js`, `verdicts.js`, `render.js`.                                      |
| `rebuild/review/fixtures/`    | A     | A hand-written miniature `manifest.json` + one unit shard satisfying the §7 contract, so B can build and test before A's generator runs.                                                                 |
| `rebuild/review/jstests/`     | B     | `node --test` unit tests for the pure-logic modules.                                                                                                                                                     |
| `rebuild/test_review_*.py`    | A     | Pytest, flat alongside the existing `rebuild/test_*.py` files: `test_review_audit.py`, `test_review_tablediff.py`, `test_review_enrich.py`, `test_review_drafts.py`, `test_review_build.py`.             |

Never touched: `rebuild/pipeline/`, `rebuild/validation/`, `glyph_data/quikscript.yaml`, `test/`, `site/`, `tools/`, the Makefile. The one cross-tree import (the data-expect parser) follows the proven `rebuild/validation/pins.py` `_import_test_shaping()` pattern, read-only.

### 1.2 Output layout (generated, gitignored via the existing `rebuild/out/` rule)

```text
rebuild/out/review/
  index.html            copied from static/
  app.css  app.js  state.js  keyboard.js  verdicts.js  render.js
  manifest.json         generation metadata, class index, font records
  units/<class-id>.json one shard per nonzero ledger class (14 for M1)
  fonts/before.otf      copy of site/AbbotsMortonSpaceportSansSenior-Regular.otf
  fonts/after.otf       copy of rebuild/out/m1/M1.otf
```

Both font copies get their source path and sha256 recorded in `manifest.json` (the recon verified the live site OTF is byte-identical to the oracle's `font_sha256`, so "before" is faithful). The directory is fully self-contained; deleting it and rebuilding is always safe.

### 1.3 CLI surface (documented on the generated page itself, check.html-style; the originally binding "no Makefile changes" was later relaxed by the user to two additive targets, `review-build` and `review-serve` — plain `review` was already taken by the scoped-anchor-selector page)

| Task                       | Command                                                                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Build the M1 review app    | `uv run python -m rebuild.review.build`                                                                                                |
| Build in table-diff mode   | `uv run python -m rebuild.review.build --mode table-diff --baseline <dir> --new <dir> --before-font <otf> --after-font <otf>`          |
| Snapshot an accepted state | `uv run python -m rebuild.review.build snapshot --tables rebuild/out/m1 --font rebuild/out/m1/M1.otf --to rebuild/out/review-baseline` |
| Serve on 7294              | `uv run python -m rebuild.review.serve`                                                                                                |
| Export the triage document | `uv run python -m rebuild.review.export <verdicts.json> --out tmp/review-triage.yaml`                                                  |

Build and serve are separate commands (the server is long-running); `serve.py` prints the build command when the served directory is missing or stale. The two servers coexist: 7293 keeps serving `site/`, 7294 serves the review app, both via livereload's tornado `NoCacheStaticHandler` clone with `Cache-Control: no-store` (stale cached OTFs silently invalidate visual judgments). `spec_load` emits two known harmless `SpecWarning`s — the build treats them as expected, not errors.

## 2. Data model

### 2.1 Units, dedupe, ordering

The render unit is the recon's deduped triple: (`codepoints`, `baseline`, `new`) → 2,411 units covering all 15,528 audit rows; the ledger class is a function of the triple. Each unit carries its config list plus a build-time `config_note` — null when the unit's set covers every non-ss10 acceptance config (the overwhelmingly common case), otherwise a feature-gating phrase computed generically against the manifest's full config list: "only when f is on" when the set is exactly the configs containing feature tag f ("only under ss10" for the isolation overlay), "only when f is off" when it is exactly the non-ss10 configs without f, and the literal "only under: …" fallback otherwise — plus `render_groups` partitioning the configs by rendered-outcome identity (always a single group under the M1 dedupe key; extra groups would render stacked); a verdict fans out to all of the unit's (config, codepoints) audit rows. Units are ordered for triage: ledger class in the ledger's own file order, then group = lead family pair (code-point order), then codepoints.

**Kern-neutrality rule (binding for every review-surface comparison)**: the rebuild deliberately has no kerning until the design's §12 milestone, so the old font's `kern` feature is pure noise in any before/after comparison. Every place the review build shapes text — the ink census, highlight x-ranges, boundary-mark positions, pin-semantics validation — passes `kern: False` to HarfBuzz for **both** fonts (`ink.kern_neutral`, merging the config's stylistic-set features with an unconditional kern-off), and the frontend renders both sample columns with `font-kerning: none` (composing with the per-row inline `font-feature-settings`). The before-font highlight pens come from live kern-neutral shaping rather than the §13.1 subset rows' positions, because those were extracted with kerning on (the glyph identities are still checked against the subset row). A no-op on the after font today, but explicit so the rule survives §12, where kern differences get their own review. Other rebuild consumers (oracle conformance, pin replay) keep their own shaping semantics — the rule is scoped to `rebuild/review/`.

**Ink-identical machine approval (`ink.py`)**: at build time every unit is shaped in both shipped fonts under every config in its set (uharfbuzz via `rebuild.validation.shaping.Shaper`, kern-neutral per the rule above); each glyph's outline is recorded with fontTools' `DecomposingRecordingPen`, translated by the cumulative `x_advance` plus the glyph's `x_offset`/`y_offset`, and the sorted pieces compared. A unit whose placed ink is identical under **every** config is `ink_identical: true` — both fonts render it pixel-identically, only glyph names differ, so no human judgment is meaningful and the build machine-approves it. M1 facts (the kern-neutral census, reproduced by the build and pinned by tests): 1,686 of 2,411 units are ink-identical, falling entirely inside the three name-grain classes, each in full — dangling-anchor-dropped 1,334, zwnj-word-initial-unification 213, bare-name-live-join 139 (their former 137 visible stragglers were kern-only) — leaving 725 human-workload units, all in the other eleven classes. In table-diff mode the same comparison runs over each entry's witness string under its config; a witnessless entry has no renderable text to shape, so it cannot be proven ink-identical and stays `ink_identical: false` in the human workload.

**Batches cover the human workload only**: fixed slices of 300 non-ink-identical units in triage order, computed at generation time after the ink pass and recorded in the manifest (M1: 725 human units → 3 batches). Ink-identical units carry `batch: null` and are never paged to a human; the manifest carries a separate `machine_approved` record (units, rows, the verification-method one-liner, per-class counts) and each class's `machine_approved_count`, so sidebar counts, batch labels, and progress denominators count the human workload while the machine-approved total stays visible as its own line.

### 2.2 Encoding: sharded JSON, everything precomputed

Decision: **one `manifest.json` plus one JSON shard per ledger class, lazily fetched by the static app.** Measured basis: the raw audit TSV is 3.3 MB; with explain text and drafts each unit runs ~1.5–3 KB, so a single JSON would be ~5–8 MB while the largest class shard (1,334 units) stays ~2–4 MB — fine to fetch on demand, instant for the median class. Explain precompute is 0.09 ms/call (~1.3 s for everything), so **all provenance, seams, highlight offsets, and all three verdict drafts are computed at generation time**; the browser never computes, only renders and collects verdicts. No server-side logic, no lazy explain endpoint.

Per-unit precomputed fields (full contract in §7): notation, before/after facts (glyphs/cells, seams, extensions), divergent positions and the primary pair, highlight x-ranges in font units for both fonts (from `hmtx` advances, so the frontend draws the pair highlight with `px = units × font-size / upem` and never measures text), the explain render text for divergent positions, the deduped provenance pointers, exemplar status, and the three drafts with validation status.

**Secondary seams and home resolution**: a longer unit can contain divergent adjacencies beyond its primary pair — the remaining divergent gaps, plus a derived neighbor seam for each divergent position not already covered (mirroring the primary-pair fallback). The build emits each one as a `secondary_seams` entry with per-side x-range rects computed exactly like the primary highlight (same kern-neutral live-shaping pens), plus the seam's **home**: the shortest unit in the universe whose codepoint string is a substring of this unit's containing the seam, whose corresponding positions' before AND after outcomes (glyph identities, covering spans after offset adjustment, and the seam tokens) match this unit's, and whose own **primary pair is that seam** — the place where the same behavior is the primary judgment. Resolution rules: the shortest matching substring unit wins (ties break to the lowest unit id); when the home is ink-identical the marker is suppressed entirely — the divergence is an invisible name-grain rename, so there is nothing visible to judge, which keeps the page's promise that unmarked regions have nothing visible; when no home exists (a genuinely context-dependent divergence, possible at the depth-2 horizon) the marker is still emitted with `home: null` so it is never silently unmarked. The manifest carries the census under `secondary_seams` (units with visible markers, homed, home-less, suppressed-invisible), the contract checker validates the field shape and that every named home resolves to a unit in the output, and the frontend renders each visible seam as a dimmer dashed band in both columns with a chip linking to the home (or reading "only here" for `home: null`), never on machine-approved renderings.

### 2.3 The general table-vs-table treaty-diff mode

`tablediff.py` implements design §8's diff as the second input shape behind the same unit model:

- Settlement key (`config`, `input`, `backtrack`, `lookahead1`, `lookahead2`) → (`outcome`, `joint`, `provenance`); treaty key (`config`, `left`, `right`) → (`junction`, `extension`, `kern`).
- Classify added / removed / value-changed; pair removals with additions sharing (`config`, `input`) so a re-partitioned context renders as one regrouped row; demote provenance-only settlement changes to a low-priority bucket.
- Every changed row needs a witness string to render: brute-force depth ≤ 4 windows through `settle()` seeded by the row's backtrack/lookahead sets (affordable at 0.09 ms/call).
- A **baseline snapshot** is what the `snapshot` subcommand writes: the per-config `settlement-*.tsv` + `treaties-*.tsv`, the OTF they shipped with, and a `snapshot.json` recording sha256s, source paths, and the repo HEAD. **Accepting** a state after review = re-running `snapshot` over the new tables; the next migration's `--baseline` points at it. This mirrors the proven `site/before/` workflow.

M1 mode and table-diff mode converge on identical shard JSON; the frontend is mode-blind except for the manifest's `mode` field and class metadata (table-diff units carry bucket ids — `added`, `removed`, `regrouped`, `changed`, `provenance-only` — in place of ledger class ids).

## 3. Page UX

Design stance per the frontend doc's pre-coding questions — Purpose: a one-person, hours-long triage instrument for 2,411 units. Tone: utilitarian-precise, consistent with the existing `site/` house style (`light-dark()`, system chrome fonts, Menlo for code) — the content typography is the font under test. Differentiation: the keyboard-only verdict flow; a reviewer should be able to clear a batch without touching the mouse.

### 3.1 Rendering (§11 requirements, with the proven mechanics)

- **Dual @font-face**: families `AMS Review Before` / `AMS Review After` over `fonts/before.otf` / `fonts/after.otf`; rows are a grid of label, before sample, after sample with `align-items: baseline` and sticky column headers (check.html anatomy). Every sample gets `-webkit-font-smoothing: none; font-smooth: never;`; prose chrome re-asserts `subpixel-antialiased`.
- **Checkered background**: `--font-size: 88px` is kept exactly (8 px per font pixel at upem 550), so check.html's proven 16 px checker with `background-position: 0 5.6px, 8px 13.6px` carries over verbatim; if the size ever changes, recompute the phase rather than copying the numbers.
- **Per-row features**: JS sets `style.fontFeatureSettings` on the sample pair from the unit's primary render group (`ss02+ss03` → `"ss02" 1, "ss03" 1`; `default` → `normal`) — a pure, testable token-to-value function, mode-agnostic for future configs. The per-unit config-chip strip is gone — chips carried no information for the seven-config majority — and in its place a single inert badge appears only when `config_note` is non-null, surfacing exactly the judgment-relevant cases (ss03-gated, ss03-excluded, ss10-only), which also explain the row's `font-feature-settings`; the badge's title lists the full config set verbatim. Should a future unit ever carry more than one render group, each extra group's before/after pair renders stacked below the first with its own label and feature settings.
- **The pair under review unmistakably highlighted**: inline span-wrapping breaks shaping, so the highlight is drawn outside the text — an absolutely positioned underline band beneath the divergent pair in each sample, placed from the precomputed font-unit x-ranges (§2.2). The band gets a high-contrast accent color satisfying the 3:1 non-text contrast rule, in both schemes.
- **ZWNJ**: emitted as a literal `&#x200C;` inside the run (so real `uni200C` rules fire, invisible as browsers render it — desired); visible to the human as `◊ZWNJ` in the notation caption, plus a dotted tick mark drawn at the ZWNJ's precomputed x position under the run. Space shows as `␣` in captions. All Quikscript text is numeric character references in the JSON `text_entities` field, never raw PUA in source.

### 3.2 Triage flow

- **Batches and grouping**: class → family-pair group → batch of 300 units, per §2.1. The page shows one batch at a time; groups within a batch are `details.collapsible` folds with unit counts and a whole-group approve button (justified by the 1:6.4 dedupe ratio; `intended` classes are bulk-confirmable, `drift-accepted` classes get the eyeballs). A sidebar lists classes with status, deduped/raw counts, ledger `why`, and per-class progress.
- **One-key home-row verdicts** (exact map, also shown in a `?` help overlay; ignored while focus is in an input/textarea/select except Escape):

| Key                               | Action                                                                                                                            |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `a`                               | Skip (explicit no-verdict record), advance to next unverdicted unit                                                               |
| `s`                               | Reject (thumbs-down) — opens the follow-up popup below; a second key (`s`/`a`/`f`) records and auto-advances, `Escape` cancels    |
| `d`                               | Fine either way (any-of), record, auto-advance                                                                                    |
| `c`                               | Neither — both behaviors look wrong; flag for follow-up authoring, auto-advance                                                   |
| `f`                               | Approve (thumbs-up), record, auto-advance                                                                                         |
| `u`                               | Undo: pop the last verdict action and return the cursor to it                                                                     |
| `n`                               | Focus the current unit's note field (Escape returns to the list)                                                                  |
| `g`                               | Approve every remaining unverdicted unit in the current group                                                                     |
| `x`                               | Toggle the current unit's explain/provenance detail panel                                                                         |
| `ArrowDown`/`ArrowUp` (`k` / `i`) | Move the cursor without verdicting                                                                                                |
| `[` / `]`                         | Previous / next batch                                                                                                             |
| `?`                               | Help overlay                                                                                                                      |
| `Escape`                          | Blur input / close overlay (or cancel the reject popup)                                                                           |

The four main verdict keys run left to right along the left home row — `a` skip, `s` reject, `d` fine-either-way, `f` approve (per direct user feedback, twice reworked: the original opposite-hands `j`/`f`/`d`/`k` map became a/s/d/f, then the order settled as skip/reject/either/approve). A fifth verdict, `c` neither, records that both the old and the new behavior look wrong — the unit needs follow-up authoring work rather than a pick; its button sits in a second grid row directly below Either (the verdict-button container is a 4-column grid, `c` in column 3). `k` and `i` are navigation aliases for `ArrowDown` and `ArrowUp` (same input-focus and overlay suppression); `j` stays deliberately unbound. The keys are accelerators over real `<button>` elements (visible focus indicators, keyboard-accessible per WCAG); one delegated document `keydown` handler drives the same code path as clicks. Auto-advance scrolls with `behavior: smooth`, dropping to `auto` under `prefers-reduced-motion`. A row's recorded verdict also marks the after sample itself: approve draws an inset green outline around the after cell, reject overlays a non-interactive red X (`::after`, two crossing gradient strokes, `pointer-events: none`); both are CSS off the row's `data-verdict`, so they appear on record or import, survive re-render, and clear on undo.

- **Reject follow-up popup**: rejecting is deliberately two-step. `s` (or the row's Reject button) does not record; it opens a small menu absolutely positioned under that row's verdict buttons (so it follows the row through scrolling) with exactly three choices: `s` records the reject with the note untouched, `a` records it with the canned note "the old way seems nicer to write out by hand", `f` with "the new way is broken". A canned note overwrites whatever was in the unit's note field. `Escape` or a click anywhere outside cancels with no verdict recorded, and every other shortcut is suppressed while the popup is open. The mode lives in `keyboard.js` as a `rejectMenuOpen` context flag on the pure `actionForKey`, so the whole key model stays unit-testable; after a choice, the note flows through the existing note-update path and the reject records exactly as a one-step verdict did (visuals, `aria-pressed`, auto-advance).

- **Per-row notes**: a text input per unit, included in the verdict record and threaded into the drafted `why:` stubs.
- **Whole-unit verdicts**: a verdict always covers all of the unit's configs. (The originally planned per-config chip scoping was removed on user feedback — since every config renders identically, a click that changes nothing visible is misleading. The inert chip strip that replaced it was then removed too, in favor of the `config_note` badge above.)
- **Progress**: a sticky header strip — verdicted/total for the batch and overall, plus the class sidebar counts; `document.title` mirrors position (tables.html `updateTitle` pattern).
- **Copy-prompt preamble**: each unit keeps check.html's copy button, emitting "I'm looking at rebuild/out/review/ unit `<id>` — `<codepoints>` (`<notation>`)…" for pasting into an agent conversation.

### 3.3 URL state (and what stays out of it)

View state lives in `location.hash` as `URLSearchParams`, tables.html-style (`parseHash` / `writeHash` / single `applyHashState` renderer with rendered-state memos, `hashchange`-driven): `#class=…&batch=N&unit=u-NNNN&group=qsTea:qsOy&config=ss03&family=qsMay&status=unverdicted`. Filters: class, family (either side of the pair), config, verdict status. Every view is bookmarkable; reloading mid-batch returns to the exact cursor.

**Verdicts are not in the URL and not in localStorage.** They are held in an in-memory `Map` keyed by unit id, with an explicit export channel: a "Download verdicts.json" button (and a copy-to-clipboard twin) emitting the §4.1 format, and a re-import control (file picker + paste textarea) that merges by unit id and warns when the file's `manifest_generated_at` doesn't match the loaded manifest. The page warns via `beforeunload` when unexported verdicts exist. Periodic "you have N unexported verdicts" nudges in the progress strip replace silent persistence.

## 4. Verdict exports — closing the opinions-become-pins loop

All three drafts are precomputed per unit by `drafts.py` at generation time and shipped in the shard JSON; the browser only selects them. The authoritative export is two-stage:

1. **The page** exports `verdicts.json` (download/copy) — the canonical, re-importable work product.
2. **The CLI** (`uv run python -m rebuild.review.export verdicts.json --out tmp/review-triage.yaml`) joins verdicts to units, re-validates every selected draft, and writes **one triage YAML with four sections** for human placement. Nothing is auto-applied to the corpus or the rune files.

### 4.1 `verdicts.json`

```json
{
  "format": "ams-review-verdicts/1",
  "manifest_generated_at": "2026-06-10T17:02:11Z",
  "exported_at": "2026-06-10T18:40:02Z",
  "verdicts": [
    {"unit": "u-0412", "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"},
    {"unit": "u-0413", "verdict": "reject", "note": "seam looks reached-for", "at": "2026-06-10T18:21:40Z"}
  ]
}
```

`verdict` ∈ `approve` | `reject` | `either` | `neither` | `skip`; a verdict covers all of the unit's configs (the import path ignores the `configs` field that pre-rework exports carried).

### 4.2 The triage YAML (four sections)

```yaml
review:
  mode: m1-audit
  source: rebuild/out/m1/divergence-audit.tsv
  exported_at: 2026-06-10T18:45:00Z
  counts: {approve: 1980, reject: 14, either: 120, neither: 3, skip: 294, units_total: 2411, rows_covered: 13418}

pins:                       # one per approved unit — thumbs-up drafts a whole-word data-expect pin
  - unit: u-0412
    codepoints: "200C:E652:E679"
    text_entities: "&#x200C;&#xE652;&#xE679;"
    expect: "◊ZWNJ ·Tea+Oy"
    attribute: data-expect-noncanonically   # data-expect when the sequence is Manual-canonical
    stylistic_set: "03"                     # null for default; "02 05"-style for multi-set
    validated: {syntax: pass, semantics_after_font: pass}
    suggested_home: site/the-manual.html    # suggestion only; a human places the pin
    duplicate_of: null                      # set when the corpus already pins this text under this feature context — flagged, not emitted as new
    note: ""

policy_edits:               # one per rejected unit — thumbs-down drafts the one-line refuse/contract/prefer edit; rejects with no mechanical draft still appear, with keypath/suggested_record null and a no_mechanical_draft note
  - unit: u-0413
    codepoints: "E650:E665"
    file: glyph_data/runes/qsMay.yaml
    keypath: policy.refuse[+]               # [+] = append to the list
    suggested_record: "{left: {rune: qsPea, ex: x-height}, why: 'TODO'}"
    names_provenance:                       # the records explain attributed the new outcome to (§6.3)
      - glyph_data/runes/qsMay.yaml:policy.extend[1]
    decided_stage: prefer
    why_stub: "Reviewer rejected M1 outcome for E650:E665 (·Pea·May): seam looks reached-for"
    schema_valid: true

any_of:                     # one per fine-either-way unit — both behaviors as full expect strings
  - unit: u-0501
    text: "qsPea qsOwe qsMay"               # _qs_text-ready family tokens
    features: {}
    candidates:
      - "·Pea ~x~ ·Owe ~x~ ·May"            # the rebuild behavior, first
      - "·Pea | ·Owe ~x~ ·May"              # the baseline behavior, also acceptable
    realized_as: _assert_expect_any         # executable form until the corpus any-of connective (§10.5) exists
    note: ""

neither:                    # one per neither-verdicted unit — both behaviors look wrong; nothing automatic is drafted
  - unit: u-0533
    codepoints: "E652:200C:E652:E679"
    notation: "·Tea ◊ZWNJ ·Tea·Oy"
    note: "both joins look wrong; needs a fresh stance"
    names_provenance:                       # the records explain attributed the outcome to — the follow-up author's levers
      - glyph_data/runes/qsTea.yaml:policy.extend[0]
```

### 4.3 Drafter rules

- **Pin drafter (approve)**: whole-word, bare letter tokens only — no variant assertions (design §10.5: "whole-word assertions remain the preferred cheap lock"). Tokens from the notation map (`·Tea`, `◊space`, `◊ZWNJ`; the namer dot per `doc/data-expect.md`'s literal syntax); connections from the **after** settled seams (`y5`→`~x~`, `y0`→`~b~`, `y8`→`~t~`, `y6`→`~6~`, break→`|`, formed ligature→`+`). Attribute is `data-expect-noncanonically` unless the sequence is Manual-canonical; ss scope rides as the `stylistic_set` attribute value (in-string ss scoping is §10.5 future work — never drafted). **Syntax** validated with `test_shaping.parse_expect` (imported read-only via the `_import_test_shaping()` pattern); **semantics** validated against `fonts/after.otf` through the rebuild-side harness `rebuild/validate_pins.py` already uses (`rebuild/validation/pins.py` + `rebuild/validation/shaping.Shaper`) — never by monkeypatching the test module's `site/` font constants. A pin failing against the _old_ font is expected (it is the pin doing its job at cutover); `semantics_after_font` is the recorded gate. Duplicate discipline: the drafter checks the corpus for an existing `data-expect` on the same text under the same feature context and sets `duplicate_of` instead of emitting a redundant pin.
- **Policy drafter (reject)**: from the precomputed explain trace. Target file is the rune file of the divergent position; the draft names every provenance record in `trace.eliminations`/`notes` that decided the new outcome, plus `decided_stage`. The suggested record is the smallest one-line counter-lever, chosen in this order: (1) when the divergence includes a join the baseline broke (a break→yN gap adjacent to the divergent cell) and provenance is nonempty, a `refuse` on the anchor reaching across that gap, scoped to the neighbor — positive-record outcomes get a refuse, and only a refuse can restore the break (a contract would shrink the extension but keep the unwanted join); (2) when the divergent cell gained an en-ext/ex-ext on a join both fonts share and a `policy.extend` decided, a `contract` by the same amount on that side; (3) when the divergence is name-grain (both behaviors group the codepoints identically and agree on every seam — a refuse here would break a join both fonts share), a `prefer` with `mode: absolute` pinning the baseline cell's entry/exit (read from the alias map) `over` the new cell's, or its stance when only the stance differs; name-grain differences with no expressible lever (post-ZWNJ locked twins, bind pullbacks, suppressed extensions) get **no policy draft**, and the export surfaces the reject with `keypath: null` plus the unit's provenance for hand-editing; (4) otherwise, with nonempty provenance, a `refuse` of the cell's exit (or stance) in the window; (5) a `prefer` pinning the baseline outcome when the structural floor decided (empty provenance). Suggested records are validated against the rune schema under `rebuild/schema/` (`schema_valid`), and the `why:` stub embeds the unit id and the reviewer's note. It is a draft for human judgment, never applied.
- **Any-of drafter (either)**: both behaviors rendered as full expect strings by the engine (the reviewer never writes syntax) — after-behavior first, baseline-behavior second — each individually `parse_expect`-valid; `features` from the config token; realized as a generated `_assert_expect_any(_qs_text(...), [...])` test until the corpus-layer connective lands, at which point the records migrate mechanically.
- **Neither (no drafter)**: a neither verdict means neither the old nor the new behavior is right, so there is deliberately no automatic draft — no pin (nothing to lock), no policy edit (no behavior to restore), no any-of (nothing acceptable). The export carries only the unit's identity (id, codepoints, notation), the reviewer's note, and `names_provenance` so the follow-up author starts from the records that decided the outcome.

## 5. Testing strategy

### 5.1 Python (`rebuild/test_review_*.py`, run with `uv run pytest rebuild/ -n auto --dist worksteal`)

- `test_review_audit.py`: TSV/ledger loading on a fixture audit; dedupe counts (the real audit must produce exactly 2,411 units / 15,528 rows); every ledger exemplar resolves to a unit; ordering is deterministic (byte-identical shards across two builds).
- `test_review_tablediff.py`: added/removed/changed classification on synthetic table pairs; remove+add pairing on shared (`config`, `input`); provenance-only demotion; witness search returns a sequence that re-settles to the changed row; snapshot round-trip (write, diff against self = empty).
- `test_review_enrich.py`: notation map against `doc/glyph-names.md`; old seams agree with `rowmodel.iter_rows` on sampled rows; new seams/extensions agree with a direct `settle()` call; divergent-position computation on hand-built before/after pairs; highlight x-ranges match hand-computed `hmtx` sums for known glyphs.
- `test_review_drafts.py`: **every drafted pin in a real build passes `parse_expect`** (the repo's actual parser, not a reimplementation); sampled pins pass semantic validation against `rebuild/out/m1/M1.otf`; the seam-to-connector map is total over observed seams; policy drafts reference only provenance strings that occur in the unit's explain trace, name existing files, and carry schema-valid suggested records; any-of candidates are individually parseable and pairwise distinct; duplicate detection fires on a known corpus-pinned text.
- `test_review_build.py`: full build into a temp dir; manifest/shard JSON validates against a hand-rolled contract checker (the same one run over `rebuild/review/fixtures/`, so fixtures and real output can never drift apart); font copies' sha256s match the manifest; generated `index.html` passes the HTML sanity check (an `html.parser` subclass asserting balanced tags, exactly one `main`/`h1`, every internal `href`/`src` resolving to a file in the output dir); `node --check` passes on every shipped `.js` file (subprocess; skipped with a clear message if node is absent).
- `export` round-trip: a synthetic `verdicts.json` through `export.py` yields a triage YAML whose three sections parse, with counts matching.

### 5.2 JavaScript

`state.js` (hash parse/serialize), `keyboard.js` (the key map + input-focus guard), `verdicts.js` (the verdict map, undo stack, fan-out, export/import serialization), and the feature-settings token function in `render.js` are pure ES modules with no DOM access at top level. They get `node --test rebuild/review/jstests/` unit tests (node v26 is on this machine; its built-in runner needs no new dependency): hash round-trips, every keyboard binding dispatches the right action and is suppressed inside inputs, verdict/undo/auto-advance state machine transitions, import-merge semantics including the manifest-mismatch warning path. Playwright/puppeteer are **not** installed on this machine (checked: no Python package, no global npm package), so per the ground rules there is no headless-browser smoke; the HTML sanity check plus the manual serve-and-click pass in Phase 4 cover integration.

## 6. Gates (Phase 4 exit criteria)

1. `uv run pytest rebuild/ -n auto --dist worksteal` green.
2. `make test` green (proves `site/`, `test/`, the existing build untouched).
3. `node --check` on all shipped JS and `node --test rebuild/review/jstests/` green.
4. The generated `index.html` passes the HTML validity sanity check (also enforced inside pytest, gate 1).
5. Both servers run concurrently: `make serve` on 7293 and `uv run python -m rebuild.review.serve` on 7294, verified with curl against both ports (page, a shard, both OTFs with `Cache-Control: no-store`).
6. `git status` shows new/modified paths only under `rebuild/` (plus `tmp/` scratch); nothing staged, nothing committed; `rebuild/out/` remains gitignored.
7. `make prettier` run after all Python changes; the plan/report Markdown passes `markdownlint-cli2`.
8. Determinism: two consecutive builds produce byte-identical manifest and shards.

## 7. Contracts for the two parallel implementers

**Implementer A (engine)** owns everything in §1.1 marked A, and must land `rebuild/review/fixtures/` first — B develops against fixtures from minute one. **Implementer B (frontend)** owns `static/` + `jstests/`, reads `~/.agent-config/frontend-design.md` before writing any UI code, and follows the binding repo rules: nested CSS, `for-of` (never `.forEach()`), modern range syntax `(width > 40em)`, view state in the URL. Neither touches the other's files; the JSON below is the only interface.

### 7.1 `manifest.json`

```json
{
  "format": "ams-review-manifest/1",
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
  "configs": ["default", "ss02", "ss03", "ss04", "ss05", "ss02+ss03", "ss02+ss03+ss05", "ss10"],
  "batch_size": 300,
  "totals": {"units": 2411, "rows": 15528, "batches": 3},
  "machine_approved": {
    "units": 1686,
    "rows": 11621,
    "method": "Shaped with uharfbuzz in both shipped fonts (kerning disabled — …) under every config in the unit's set; …",
    "by_class": {"zwnj-word-initial-unification": 213, "dangling-anchor-dropped": 1334, "bare-name-live-join": 139}
  },
  "classes": [
    {
      "id": "dangling-anchor-dropped",
      "status": "drift-accepted",
      "ink_identical": false,
      "why": "…the ledger's reviewed rationale, verbatim…",
      "unit_count": 1334,
      "row_count": 9283,
      "machine_approved_count": 1334,
      "shard": "units/dangling-anchor-dropped.json",
      "batches": []
    }
  ],
  "build_command": "uv run python -m rebuild.review.build",
  "serve_command": "uv run python -m rebuild.review.serve"
}
```

Types: all counts are integers; `batches` lists the zero-based global batch indices the class's **human-workload** units occupy (`totals.batches` counts human batches too); `machine_approved.by_class` lists only classes with a nonzero count, while every class carries `machine_approved_count` (possibly 0); `classes` preserves ledger file order (triage order). In table-diff mode `classes` carries the diff buckets (`added`, `removed`, `regrouped`, `changed`, `provenance-only`) with `status: null` and `why` generated. The class-level `ink_identical` flag is the ledger's reviewed-classification metadata and is distinct from the per-unit `ink_identical` boolean, which is computed from the fonts at build time.

### 7.2 Unit shard (`units/<class-id>.json`) — an array of units in triage order

```json
{
  "id": "u-0412",
  "batch": 1,
  "ink_identical": false,
  "class": "marker-staging-ligature-formation",
  "group": "qsTea:qsOy",
  "codepoints": "200C:E652:E679",
  "text_entities": "&#x200C;&#xE652;&#xE679;",
  "notation": "◊ZWNJ ·Tea·Oy",
  "configs": ["ss03", "ss02+ss03", "ss02+ss03+ss05"],
  "config_note": "only when ss03 is on",
  "render_groups": [{"configs": ["ss03", "ss02+ss03", "ss02+ss03+ss05"]}],
  "kinds": ["ligation"],
  "exemplar": true,
  "before": {"glyphs": ["space", "qsTea_qsOy"], "seams": ["break", "lig"]},
  "after": {"cells": ["uni200C", "qsTea_qsOy/bar-into-loop/None/None/+locked"], "seams": ["break", "lig"], "extensions": [0, 0]},
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

Field semantics: `secondary_seams` is optional (`null` or absent when the unit has no visible secondary seam, and never present on machine-approved units): a list of `{pair: {left, right}, before: rect, after: rect, home: "u-NNNN" | null}` entries per the §2.2 resolution rules, with rects in the same font-unit form as `highlight`; `ink_identical` is required on every unit in both modes (the contract checker enforces it); when true the unit is machine-approved, `batch` is `null`, and the frontend shows it only behind the "Show machine-approved" toggle with verdict controls disabled; `text_entities` is the rendered run as numeric character references (never raw PUA — the frontend injects it with `innerHTML` into the sample cells only); `seams` arrays have one entry per inter-glyph gap (`break`, `lig`, or `yN`); `diff_positions` are glyph indices whose cell or trailing seam diverges; `pair` is the primary divergent adjacency to highlight (`null` for single-position divergences with no seam change); `highlight` x-values and `boundary_marks[].x` are in font units — the frontend converts with `font-size / upem`; `config_note` is null for the general case (the set covers every non-ss10 config) and otherwise the badge phrase computed by the §2.1 rule — the frontend renders it verbatim when present and nothing when null; `render_groups` partitions `configs` by rendered-outcome identity — exactly one group under the M1 dedupe key, with any extra group rendered as a stacked before/after pair under its own feature settings; `summary` is the always-visible one-line prose summary in rune-name notation; `explain` is display-only preformatted text; `stylistic_set` is `null` or the space-separated zero-padded form (`"02 05"`); all strings are NFC, all keys snake_case. The fixture shard under `rebuild/review/fixtures/` contains about six hand-written units exercising every branch (multi-config, ZWNJ, namer dot, ligation, `pair: null`, a `duplicate_of` pin), and the contract checker in `test_review_build.py` validates fixtures and real output identically.

### 7.3 `verdicts.json`

As in §4.1 — produced by B's `verdicts.js`, consumed by A's `export.py`; the round-trip test in §5.1 is the integration gate between the two implementers.

## 8. Implementation order

1. A: fixtures + contract checker; B: static app skeleton against fixtures (parallel from here).
2. A: `audit.py` → `enrich.py` → `drafts.py` → `build.py`/`serve.py`/`export.py`; B: rendering, verdict state machine, keyboard, URL state, export/import UI.
3. A: `tablediff.py` + snapshot (independent of B).
4. Integration: real build, both servers up, a manual batch triaged end-to-end, `export.py` over a real `verdicts.json`.
5. Gates (§6), then `rebuild/REVIEW-REPORT.md`.
