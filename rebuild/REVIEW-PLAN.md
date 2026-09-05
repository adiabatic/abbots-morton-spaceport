# Review surface plan (design §11, first workload: the M1 migration baseline diff)

Plan for the treaty-diff review app. Inputs: design §11 + §8 + §10.5 + §6.3, and the binding user decisions (output under `rebuild/out/review/`, served on port 7294, nothing outside `rebuild/` + `tmp/` touched).

This is the design record — why the surface is shaped the way it is, and the contracts it was built against. It is not the operator’s guide, and it does not track the live app: `rebuild/review/README.md` is the maintained account of what the surface does today (commands, keyboard map, triage flow, the machine-approval and dedup mechanisms), and the checkers in `rebuild/review/build.py` are the executable contract. Counts belong to their live homes rather than to this file: the census is `rebuild/review-census-pins.json` (refreshed by every artifact-cycle pass from the build’s census sidecar, with `uv run python -m rebuild.review.census --update` as the manual form), the per-build totals — `totals`, `machine_approved`, the `secondary_seams` census — are `rebuild/out/review/manifest.json`, and adjudication status is `make verdict-ready`. Section numbers here are a committed interface: the `rebuild/review/*.py` module docstrings and the README cite them, so sections are never renumbered.

## 1. Architecture

### 1.1 Source layout (committed-shape, under `rebuild/review/`)

A package, not a single module — the engine has several separable concerns. The live file list is the README’s “Source layout” bullet; what the design fixes is the shape. Two ingestion front ends produce the same unit model: `audit.py` (M1 mode — load `rebuild/out/m1/divergence-audit.tsv` + `rebuild/m1-divergences.yaml`, dedupe to units, group and order them) and `tablediff.py` (general mode — key-aligned diff of two settlement/treaty table directories, remove+add pairing, provenance-only demotion, witness-string search). Everything downstream of them is mode-blind: enrichment, the three verdict drafters, the ink census, the generation CLI, the triage-YAML export. The server is a line-for-line sibling of `tools/serve.py` over `rebuild/out/review/`. `rebuild/review/fixtures/` holds a hand-written miniature `manifest.json` plus one unit shard satisfying the §7 contract, so the frontend and the contract checker can be exercised without running a generation. Python tests live flat as `rebuild/test_review_*.py` alongside the existing `rebuild/test_*.py` files; the pure-logic ES modules get `node --test` under `rebuild/review/jstests/`.

Never touched: `rebuild/pipeline/`, `rebuild/validation/`, `glyph_data/quikscript.yaml`, `test/`, `site/`, `tools/`. The one cross-tree import (the data-expect parser) follows the proven `rebuild/validation/pins.py` `_import_test_shaping()` pattern, read-only.

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

A shard is capped at `build.SHARD_PART_BYTES` because the app parses each file it fetches as one JavaScript string, and a body past V8’s `String::kMaxLength` (2**29 − 24 bytes under pointer compression) reaches `JSON.parse` as the empty string rather than as an error. A class that fits in one part keeps the bare name; a class that does not is written as contiguous three-digit parts numbered from `000`, with the manifest’s `shards` list naming them in concatenation order.

Both font copies get their source path and sha256 recorded in `manifest.json` (the live site OTF is byte-identical to the oracle’s `font_sha256`, so “before” is faithful). The directory is fully self-contained; deleting it and rebuilding is always safe.

### 1.3 CLI surface (documented on the generated page itself, check.html-style)

The everyday spellings — `make review-build`, `make review-serve`, and the export CLI — are the README’s Commands block. Two invocations exist only for the design’s second input shape and its accept step:

| Task                       | Command                                                                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Build in table-diff mode   | `uv run python -m rebuild.review.build --mode table-diff --baseline <dir> --new <dir> --before-font <otf> --after-font <otf>`          |
| Snapshot an accepted state | `uv run python -m rebuild.review.build snapshot --tables rebuild/out/m1 --font rebuild/out/m1/M1.otf --to rebuild/out/review-baseline` |

Build and serve are separate commands (the server is long-running); `serve.py` prints the build command when the served directory is missing or stale. The two servers coexist: 7293 keeps serving `site/`, 7294 serves the review app, both via livereload’s tornado `NoCacheStaticHandler` clone with `Cache-Control: no-store` (stale cached OTFs silently invalidate visual judgments). The build suppresses `spec_load`’s `SpecWarning`s rather than treating them as errors.

## 2. Data model

### 2.1 Units, dedupe, ordering

The render unit is the deduped triple: (`codepoints`, `baseline`, `new`) — one unit per triple, covering every audit row that shares it; the ledger class is a function of the triple. Each unit carries its config list plus a build-time `config_gate` and its prose join `config_note` — both null when the unit’s set covers every non-ss10 acceptance config (the overwhelmingly common case), otherwise the minimal conjunction of feature on/off constraints selecting exactly that set, on-constraints first, one clause per constraint and each clause carrying the text the badge prints for it; `build.config_badge` is the authority, including the literal “only under: …” fallback it leaves for a set no short conjunction pins — plus `render_groups` partitioning the configs by rendered-outcome identity (always a single group under the M1 dedupe key; extra groups would render stacked); a verdict fans out to all of the unit’s (config, codepoints) audit rows. Units are ordered for triage: ledger class in the ledger’s own file order, then group = lead family pair (code-point order), then codepoints.

**Kern-neutrality rule (binding for every review-surface comparison)**: the rebuild deliberately has no kerning until the design’s §12 milestone, so the old font’s `kern` feature is pure noise in any before/after comparison. Every place the review build shapes text — the ink census, highlight x-ranges, boundary-mark positions, pin-semantics validation — passes `kern: False` to HarfBuzz for **both** fonts (`ink.kern_neutral`, merging the config’s stylistic-set features with an unconditional kern-off), and the frontend renders both sample columns with `font-kerning: none` (composing with the per-row inline `font-feature-settings`). The before-font highlight pens come from live kern-neutral shaping rather than the §13.1 subset rows’ positions, because those were extracted with kerning on (the glyph identities are still checked against the subset row). A no-op on the after font today, but explicit so the rule survives §12, where kern differences get their own review. Other rebuild consumers (oracle conformance, pin replay) keep their own shaping semantics — the rule is scoped to `rebuild/review/`.

**Ink-identical machine approval (`ink.py`)**: at build time every unit is shaped in both shipped fonts under every config in its set (uharfbuzz via `rebuild.validation.shaping.Shaper`, kern-neutral per the rule above); each glyph’s outline is recorded with fontTools’ `DecomposingRecordingPen` and placed at the cumulative `x_advance` plus the glyph’s `x_offset`/`y_offset`. The verdict is `config_diff`’s identity sentinel: the two placed runs are aligned from both ends and the middles multiset-subtracted, and a unit whose localized delta is empty with no follower shift under **every** config is `ink_identical: true` — both fonts render it pixel-identically, only glyph names differ, so no human judgment is meaningful and the build machine-approves it. The sentinel implies the sorted-placed-pieces comparison the census originally ran; that retired formulation is held equal by a property test in `rebuild/test_review_ink.py`. The census is reproduced by every build and recorded as the last accepted census in `rebuild/review-census-pins.json`; the live totals — machine-approved units and rows, per class and per channel — are the manifest’s `machine_approved` record. In table-diff mode the same comparison runs over each entry’s witness string under its config; a witnessless entry has no renderable text to shape, so it cannot be proven ink-identical and stays `ink_identical: false` in the human workload.

**Batches cover the human workload only**: fixed slices of 300 non-ink-identical units in triage order, computed at generation time after the ink pass and recorded in the manifest. Ink-identical units carry `batch: null` and are never paged to a human; the manifest carries a separate `machine_approved` record (units, rows, the verification-method one-liner, per-class counts) and each class’s `machine_approved_count`, so sidebar counts, batch labels, and progress denominators count the human workload while the machine-approved total stays one click away, in the header strip’s surface-totals popover.

### 2.2 Encoding: sharded JSON, everything precomputed

Decision: **one `manifest.json` plus one JSON shard per ledger class, lazily fetched by the static app.** Measured basis: a unit runs ≈1.5–3 KB with its explain text and drafts, so one whole-surface JSON would be megabytes while a per-class shard is a fetch-on-demand cost, instant for the median class; each worker batches its settle/explain precompute through the Rust kernel before enriching the units. Therefore **all provenance, seams, highlight offsets, and all three verdict drafts are computed at generation time**; the browser never computes, only renders and collects verdicts. No server-side logic and no explain endpoint — that half is narrowed rather than kept whole: the app boots from a slim projection of the human units (§7.4) that omits `explain`, `provenance`, and `drafts`, and the explain panel reads them back on open with an HTTP Range request against the shard the build already wrote, addressed by the byte span the projection’s row carries. Still one static directory, still nothing computed in the browser; only the moment of the fetch moved, so the tab’s resident set is the queue rather than the corpus.

Per-unit precomputed fields (full contract in §7): notation, before/after facts (glyphs/cells, seams, extensions), divergent positions and the primary pair, highlight x-ranges in font units for both fonts (from `hmtx` advances, so the frontend draws the pair highlight with `px = units × font-size / upem` and never measures text), the explain render text for divergent positions, the deduped provenance pointers, exemplar status, and the three drafts with validation status.

**Secondary seams and home resolution**: a longer unit can contain divergent adjacencies beyond its primary pair — the remaining divergent gaps, plus a derived neighbor seam for each divergent position not already covered (mirroring the primary-pair fallback). The build emits each one as a `secondary_seams` entry with per-side x-range rects computed exactly like the primary highlight (same kern-neutral live-shaping pens), plus the seam’s **home**: the shortest unit in the universe whose codepoint string is a substring of this unit’s containing the seam, whose corresponding positions’ before AND after outcomes (glyph identities, covering spans after offset adjustment, and the seam tokens) match this unit’s, and whose own **primary pair is that seam** — the place where the same behavior is the primary judgment. Resolution rules: the shortest matching substring unit wins (ties break to the lowest unit id); when the home is ink- or picture-identical the marker is suppressed entirely — the divergence is an invisible name-grain rename, so there is nothing visible to judge, which keeps the page’s promise that unmarked regions have nothing visible; when no home exists (a genuinely context-dependent divergence, possible at the depth-2 horizon) the marker is still emitted with `home: null` so it is never silently unmarked. The manifest carries the census under `secondary_seams` (units with visible markers, homed, home-less, suppressed-invisible), the contract checker validates the field shape and that every named home resolves to a unit in the output, and the frontend renders each visible seam as a dimmer dashed band in both columns with a chip linking to the home (or reading “only here” for `home: null`), never on machine-approved renderings. A home-less seam is judged in this unit, so the frontend additionally underlines its covered tokens on the label’s notation and codepoints lines with a `.seam-mark` span in the band’s dashed amber (see §3.1).

### 2.3 The general table-vs-table treaty-diff mode

`tablediff.py` implements design §8’s diff as the second input shape behind the same unit model:

- Settlement key (`config`, `input`, `backtrack`, `lookahead1`, `lookahead2`) → (`outcome`, `joint`, `provenance`); treaty key (`config`, `left`, `right`) → (`junction`, `extension`, `kern`).
- Classify added / removed / value-changed; pair removals with additions sharing (`config`, `input`) so a re-partitioned context renders as one regrouped row; demote provenance-only settlement changes to a low-priority bucket.
- Every changed row needs a witness string to render: brute-force depth ≤ 4 windows through `settle()` seeded by the row’s backtrack/lookahead sets (affordable at the per-settle cost noted in §2.2).
- A **baseline snapshot** is what the `snapshot` subcommand writes: the per-config `settlement-*.tsv` + `treaties-*.tsv`, the OTF they shipped with, and a `snapshot.json` recording sha256s, source paths, and the repo HEAD. **Accepting** a state after review = re-running `snapshot` over the new tables; the next migration’s `--baseline` points at it. This mirrors the proven `site/before/` workflow.

M1 mode and table-diff mode converge on identical shard JSON; the frontend is mode-blind except for the manifest’s `mode` field and class metadata (table-diff units carry bucket ids — `added`, `removed`, `regrouped`, `changed`, `provenance-only` — in place of ledger class ids).

## 3. Page UX

Design stance per the frontend doc’s pre-coding questions — Purpose: a one-person, hours-long triage instrument for the whole divergence surface. Tone: utilitarian-precise, consistent with the existing `site/` house style (`light-dark()`, system chrome fonts, Menlo for code) — the content typography is the font under test. Differentiation: the keyboard-only verdict flow; a reviewer should be able to clear a batch without touching the mouse.

### 3.1 Rendering (§11 requirements, with the proven mechanics)

- **Dual @font-face**: families `AMS Review Before` / `AMS Review After` over `fonts/before.otf` / `fonts/after.otf`; rows are a grid of label, before sample, after sample with `align-items: baseline` and sticky column headers (check.html anatomy). Every sample gets `-webkit-font-smoothing: none; font-smooth: never;`; prose chrome re-asserts `subpixel-antialiased`.
- **Checkered background**: `--font-size: 88px` is kept exactly (8 px per font pixel at upem 550), so check.html’s proven 16 px checker with `background-position: 0 5.6px, 8px 13.6px` carries over verbatim; if the size ever changes, recompute the phase rather than copying the numbers.
- **Per-row features**: JS sets `style.fontFeatureSettings` on the sample pair from the unit’s primary render group (`ss02+ss03` → `"ss02" 1, "ss03" 1`; `default` → `normal`) — a pure, testable token-to-value function, mode-agnostic for future configs. There is no per-unit config-chip strip listing the configs themselves: it carries no information for the units that diverge under every non-ss10 config. In its place a badge of one inert chip per `config_gate` clause appears when the gate is non-null, each chip in its stylistic set’s own color — lit for an on-constraint, muted for an off-constraint — and glossed with the manifest’s `feature_descriptions` entry; `configGateChips` in `render.js` is the pure, tested derivation, and it renders each clause’s `text` verbatim rather than re-parsing `config_note`. That surfaces the judgment-relevant cases (ss03-gated, ss03-excluded, ss10-only, and the narrower conjunctions), which also explain the row’s `font-feature-settings`; each chip’s title lists the full config set verbatim, and the docket’s cluster headers reuse the same chips. Should a future unit ever carry more than one render group, each extra group’s before/after pair renders stacked below the first with its own label and feature settings.
- **The pair under review unmistakably highlighted**: inline span-wrapping breaks shaping, so the highlight is drawn outside the text — an absolutely positioned underline band beneath the divergent pair in each sample, placed from the precomputed font-unit x-ranges (§2.2). The band gets a high-contrast accent color satisfying the 3:1 non-text contrast rule, in both schemes. The same `--hot` color also underlines the pair on the label’s notation and codepoints lines via a `.pair-mark` span: the covered codepoint span is computed at build time (`pair_codepoints`, with `notation_tokens` aligned one-to-one with codepoint positions) because ligatures make glyph positions diverge from codepoint positions; units with no primary pair render those lines unmarked. Homed secondary seams never mark the text lines — their judgment lives at the home unit — but a home-less (“only here”) seam gets its own subordinate `.seam-mark` underline, dashed in the secondary band’s amber and offset below the pair-mark line where the two overlap. Its codepoint span is derived client-side from `after.cells` (a formed ligature covers two codepoint positions) and cross-checked against `pair_codepoints`; on any disagreement the seam marks are dropped rather than risk underlining the wrong letters.
- **ZWNJ**: emitted as a literal `&#x200C;` inside the run (so real `uni200C` rules fire, invisible as browsers render it — desired); visible to the human as `◊ZWNJ` in the notation caption, plus a dotted tick mark drawn at the ZWNJ’s precomputed x position under the run. Space shows as `␣` in captions. All Quikscript text is numeric character references in the JSON `text_entities` field, never raw PUA in source.

### 3.2 Triage flow

- **Batches and grouping**: class → family-pair group → batch of 300 units, per §2.1. The page shows one batch at a time; groups within a batch are `details.collapsible` folds with unit counts and a whole-group approve button (justified by the dedupe ratio — one unit stands for many audit rows; `intended` classes are bulk-confirmable, `drift-accepted` classes get the eyeballs). A sidebar lists classes with status, deduped/raw counts, ledger `why`, and per-class progress.
- **One-key home-row verdicts**: the live key map is the README’s keyboard table, mirrored in the app’s `?` help overlay; keys are ignored while focus is in an input/textarea/select, except `Escape`. The four main verdict keys run left to right along the left home row — `a` skip, `s` reject, `d` fine-either-way, `f` approve — and `j` stays deliberately unbound. A fifth verdict, `c` neither, records that both the old and the new behavior look wrong — the unit needs follow-up authoring work rather than a pick. A sixth, `e` identical, records that the highlighted portion looks identical — the reviewer cannot see the flagged difference, which is signal for the ink-comparator and highlight tooling rather than for either font. The verdict-button container is a 4-column grid with DOM order Skip, Reject, Identical, Approve, Either, Neither: row 1 carries Skip / Reject / Identical / Approve, and Either and Neither are forced into column 3 so they stack directly below Identical in rows 2 and 3. `k` and `i` are navigation aliases for `ArrowDown` and `ArrowUp` (same input-focus and overlay suppression). The keys are accelerators over real `<button>` elements (visible focus indicators, keyboard-accessible per WCAG); one delegated document `keydown` handler drives the same code path as clicks. Auto-advance scrolls with `behavior: smooth`, dropping to `auto` under `prefers-reduced-motion`. A row’s recorded verdict also marks the after sample itself: approve draws an inset green outline around the after cell, reject overlays a non-interactive red X (`::after`, two crossing gradient strokes, `pointer-events: none`); both are CSS off the row’s `data-verdict`, so they appear on record or import, survive re-render, and clear on undo.

- **Reject follow-up popup**: rejecting is deliberately two-step. `s` (or the row’s Reject button) does not record; it opens a small menu absolutely positioned under that row’s verdict buttons (so it follows the row through scrolling), with a second key that records the reject — either with the note untouched or with one of the canned notes (the README’s keyboard map carries the live list). A canned note overwrites whatever was in the unit’s note field. `Escape` or a click anywhere outside cancels with no verdict recorded, and every other shortcut is suppressed while the popup is open. The mode lives in `keyboard.js` as a `rejectMenuOpen` context flag on the pure `actionForKey`, so the whole key model stays unit-testable; after a choice, the note flows through the existing note-update path and the reject records exactly as a one-step verdict did (visuals, `aria-pressed`, auto-advance).

- **Per-row notes**: a text input per unit, included in the verdict record and threaded into the drafted `why:` stubs.
- **Whole-unit verdicts**: a verdict always covers all of the unit’s configs. Per-config scoping is deliberately unavailable — every config of a unit renders identically by construction, so a click that changes nothing visible would be misleading; the `config_note` badge above is the only per-config surface.
- **Progress**: a sticky header strip — verdicted/total for the batch and overall, plus the class sidebar counts; `document.title` mirrors position (tables.html `updateTitle` pattern).
- **Copy-prompt preamble**: each unit keeps check.html’s copy button, emitting “I’m looking at rebuild/out/review/ unit `<id>` — `<codepoints>` (`<notation>`)…” for pasting into an agent conversation.

### 3.3 URL state (and what stays out of it)

View state lives in `location.hash` as `URLSearchParams`, tables.html-style (`parseHash` / `writeHash` / single `applyHashState` renderer with rendered-state memos, `hashchange`-driven): `#class=…&batch=N&unit=u-NNNN&group=qsTea:qsOy&config=ss03&family=qsMay&status=unverdicted`. Filters: class, family (either side of the pair), config, verdict status. Every view is bookmarkable; reloading mid-batch returns to the exact cursor.

**Verdicts are not in the URL and not in localStorage.** They are held in an in-memory `Map` keyed by unit id, with an explicit export channel: a “Download verdicts.json” button emitting the §4.1 format, and a re-import control (file picker) that merges by unit id and warns when the file’s `manifest_generated_at` doesn’t match the loaded manifest. In-progress work is not lost to a reload: the store debounce-POSTs to the serve script’s `/autosave` endpoint, and every write is journaled — see the README’s triage-flow section. The `beforeunload` warning and the unexported-count nudge remain the fallback for when autosave is unavailable.

## 4. Verdict exports — closing the opinions-become-pins loop

All three drafts are precomputed per unit by `drafts.py` at generation time and shipped in the shard JSON; the browser only selects them. The authoritative export is two-stage:

1. **The page** exports `verdicts.json` (download/copy) — the canonical, re-importable work product.
2. **The CLI** (`uv run python -m rebuild.review.export verdicts.json --out tmp/review-triage.yaml`) joins verdicts to units, re-validates every selected draft, and writes **one triage YAML with five sections** for human placement. Nothing is auto-applied to the corpus or the rune files.

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

`verdict` ∈ `approve` | `reject` | `either` | `identical` | `neither` | `skip`; a verdict covers all of the unit’s configs (the import path ignores the `configs` field that pre-rework exports carried).

### 4.2 The triage YAML (five sections)

```yaml
review:
  mode: m1-audit
  source: rebuild/out/m1/divergence-audit.tsv
  exported_at: 2026-06-10T18:45:00Z
  counts: {approve: …, reject: …, either: …, identical: …, neither: …, skip: …, units_total: …, rows_covered: …}

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

identical:                  # one per identical-verdicted unit — the reviewer cannot see the flagged difference; nothing is drafted
  - unit: u-0540
    codepoints: "E665:E679"
    notation: "·May·Oy"
    note: "the highlighted joins look the same to me"
```

### 4.3 Drafter rules

- **Pin drafter (approve)**: whole-word, bare letter tokens only — no variant assertions (design §10.5: “whole-word assertions remain the preferred cheap lock”). Tokens from the notation map (`·Tea`, `◊space`, `◊ZWNJ`; the namer dot per `doc/data-expect.md`’s literal syntax); connections from the **after** settled seams (`y5`→`~x~`, `y0`→`~b~`, `y8`→`~t~`, `y6`→`~6~`, break→`|`, formed ligature→`+`). Attribute is `data-expect-noncanonically` unless the sequence is Manual-canonical; ss scope rides as the `stylistic_set` attribute value (in-string ss scoping is §10.5 future work — never drafted). **Syntax** validated with `test_shaping.parse_expect` (imported read-only via the `_import_test_shaping()` pattern); **semantics** validated against `fonts/after.otf` through the rebuild-side harness `rebuild/test_validation_suite.py`’s corpus replay uses (`rebuild/validation/pins.py` + `rebuild/validation/shaping.Shaper`) — never by monkeypatching the test module’s `site/` font constants. A pin failing against the _old_ font is expected (it is the pin doing its job at cutover); the after font is the recorded gate, and a pin the drafter cannot get past it — or past `parse_expect` — raises `DraftError` where the draft is made, so `pass` is the only value either field is ever written with and no fragment carries a `fail: …` value for a downstream check to reject. Duplicate discipline: the drafter checks the corpus for an existing `data-expect` on the same text under the same feature context and sets `duplicate_of` instead of emitting a redundant pin.
- **Policy drafter (reject)**: from the precomputed explain trace. Target file is the rune file of the divergent position; the draft names every provenance record in `trace.eliminations`/`notes` that decided the new outcome, plus `decided_stage`. The suggested record is the smallest one-line counter-lever, chosen in this order: (1) when the divergence includes a join the baseline broke (a break→yN gap adjacent to the divergent cell) and provenance is nonempty, a `refuse` on the anchor reaching across that gap, scoped to the neighbor — positive-record outcomes get a refuse, and only a refuse can restore the break (a contract would shrink the extension but keep the unwanted join); (2) when the divergent cell gained an en-ext/ex-ext on a join both fonts share and a `policy.extend` decided, a `contract` by the same amount on that side; (3) when the divergence is name-grain (both behaviors group the codepoints identically and agree on every seam — a refuse here would break a join both fonts share), a `prefer` with `mode: absolute` pinning the baseline cell’s entry/exit (read from the alias map) `over` the new cell’s, or its stance when only the stance differs; name-grain differences with no expressible lever (post-ZWNJ locked twins, bind pullbacks, suppressed extensions) get **no policy draft**, and the export surfaces the reject with `keypath: null` plus the unit’s provenance for hand-editing; (4) otherwise, with nonempty provenance, a `refuse` of the cell’s exit (or stance) in the window; (5) a `prefer` pinning the baseline outcome when the structural floor decided (empty provenance). Suggested records are validated against the rune schema under `rebuild/schema/` (`schema_valid`), and the `why:` stub embeds the unit id and the reviewer’s note. It is a draft for human judgment, never applied.
- **Any-of drafter (either)**: both behaviors rendered as full expect strings by the engine (the reviewer never writes syntax) — after-behavior first, baseline-behavior second — each individually `parse_expect`-valid; `features` from the config token; realized as a generated `_assert_expect_any(_qs_text(...), [...])` test until the corpus-layer connective lands, at which point the records migrate mechanically.
- **Neither (no drafter)**: a neither verdict means neither the old nor the new behavior is right, so there is deliberately no automatic draft — no pin (nothing to lock), no policy edit (no behavior to restore), no any-of (nothing acceptable). The export carries only the unit’s identity (id, codepoints, notation), the reviewer’s note, and `names_provenance` so the follow-up author starts from the records that decided the outcome.
- **Identical (no drafter)**: an identical verdict means the reviewer cannot see the flagged difference — the highlighted portion looks the same in both fonts. Nothing is drafted; the export carries only the unit’s identity (id, codepoints, notation) and the reviewer’s note. These entries are signal for the ink-comparator and highlight tooling — claims that a flagged divergence is invisible at human grain — not a judgment on either font’s behavior.

## 5. Testing strategy

### 5.1 Python (`rebuild/test_review_*.py` and their neighbors, in the contracts lane of `make test-rebuild`)

None of these reads the live surface. Every worked example takes its window from the frozen mini bundle under `rebuild/review/fixtures/mini/` (the `mini_bundle` and `example_units` fixtures in `rebuild/conftest.py`), and whatever the build can prove per unit is proven in the build — `check_unit` / `check_shards` in `build.py`, the drafter's and enricher's refusals, the served-vs-recomputed sample — rather than re-swept by a test over shipped shards.

- `test_review_audit.py`: TSV/ledger loading; the dedupe to per-config-class units; deterministic ordering, id assignment and batch slicing over the mini workload. That the dedupe conserves rows and every ledger exemplar resolves are `build_units`' own assertions, over every build.
- `test_review_tablediff.py`: added/removed/changed classification on synthetic table pairs; remove+add pairing on shared (`config`, `input`); provenance-only demotion; witness search re-settling to the changed row over the mini bundle's tables; snapshot round-trip (write, diff against self = empty).
- `test_review_enrich.py`: the notation map against `doc/glyph-names.md`; divergent-position and pair selection on named mini-bundle windows; highlight x-ranges against hand-computed `hmtx` sums; the secondary-seam home resolver over hand-built stubs. The audit-vs-re-settlement agreement, the before-seam derivations and the summary's shape are the build's, over every shipped unit.
- `test_review_drafts.py`: the semantic validator's teeth; the policy drafter's branch table on worked-example windows (contract, refuse, prefer on a name-grain divergence and on an empty trace, and the decline); any-of candidate ordering; duplicate detection against a synthetic corpus index. What every drafted pin and record must satisfy is enforced by the drafter's own refusal (`DraftError`, §4.3), not by a sweep.
- `test_review_build.py`: the §7 contract checker over `rebuild/review/fixtures/` (the same checker `build_m1` runs over its own output, so fixtures and real output can never drift apart); the config-note badge vocabulary; the app shell and `node --check` over every shipped `.js` file (skipped with a clear message if node is absent); the export round-trip — a synthetic `verdicts.json` with one verdict of each kind through `export.py` yields a triage YAML whose sections parse with the right per-section membership; the table-diff build.
- `test_review_ink.py`: the ink-identity comparator on mini-bundle windows shaped in the bundle's own font; the name-blindness of `signature` on the marker font; `delta_digest`; the pixel-grain readings the standing approvals work from.
- `test_surface_checks.py`: every `check_manifest` / `check_unit` / `check_shards` predicate exercised against the fixture surface as shipped and then with one field broken at a time. `test_app_index.py`: the two app sidecars and the byte spans that address the shards, over a mini build.

### 5.2 JavaScript

`state.js` (hash parse/serialize), `keyboard.js` (the key map + input-focus guard), `verdicts.js` (the verdict map, undo stack, fan-out, export/import serialization), and the feature-settings token function in `render.js` are pure ES modules with no DOM access at top level. They get `node --test rebuild/review/jstests/` unit tests (its built-in runner needs no new dependency): hash round-trips, every keyboard binding dispatches the right action and is suppressed inside inputs, verdict/undo/auto-advance state machine transitions, import-merge semantics including the manifest-mismatch warning path. There is no headless-browser smoke (no Playwright or puppeteer); the HTML sanity check plus a manual serve-and-click pass cover integration.

## 6. Gates

1. `uv run pytest rebuild/ -n auto --dist worksteal` green.
2. `make test` green (proves `site/`, `test/`, the existing build untouched).
3. `node --check` on all shipped JS and `node --test rebuild/review/jstests/` green.
4. The generated `index.html` passes the HTML validity sanity check (also enforced inside pytest, gate 1).
5. Both servers run concurrently: `make serve` on 7293 and `make review-serve` on 7294 — page, a shard, and both OTFs with `Cache-Control: no-store`.
6. `rebuild/out/` remains gitignored.
7. `make prettier` run after all Python changes; the Markdown passes `markdownlint-cli2`.
8. Determinism: two consecutive builds produce byte-identical manifest and shards.

## 7. The engine↔frontend contract

The executable authority is `check_manifest` / `check_unit` / `check_shards` / `check_output_dir` in `rebuild/review/build.py`, and a change has to satisfy them rather than this section. `check_shards` is what the build runs over its own output on every build, failing it on any violation: `check_unit` over every unit that build computed — a unit the unit cache served is held instead by its `content_key`, which the build compares against the stamp on the fragment it fetched, and which was written by a build where the checker did run — and every cross-unit predicate over both kinds. `check_manifest` and the beside-the-manifest file predicates run through `check_output_dir` instead, which the contracts lane holds to an empty error list over a real mini-bundle m1 build (`rebuild/test_app_index.py`) and a real table-diff build (`rebuild/test_review_build.py`); `rebuild/test_surface_checks.py` then holds each of their predicates to the checked-in fixture surface and to that surface with one field broken at a time. What follows orients a reader to the shape of the JSON and to the design decisions its fields carry; it is deliberately not exhaustive, and where it and the checkers disagree the checkers are right. The JSON is the only interface between the engine (`rebuild/review/*.py`) and the frontend (`static/` + `jstests/`); `rebuild/review/fixtures/` lets the frontend build against the contract without running a generation.

### 7.1 `manifest.json`

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

Types: `shards` is a nonempty list of the class’s parts in concatenation order, one entry for a class that fits in a single file and `units/<class-id>.000.json`, `units/<class-id>.001.json`, … for one that does not; all counts are integers; `batches` lists the zero-based global batch indices the class’s **human-workload** units occupy (`totals.batches` counts human batches too); `machine_approved.by_class` lists only classes with a nonzero count, while every class carries `machine_approved_count` (possibly 0); `classes` preserves ledger file order (triage order). In table-diff mode `classes` carries the diff buckets (`added`, `removed`, `regrouped`, `changed`, `provenance-only`) with `status: null` and `why` generated. The class-level `ink_identical` flag is the ledger’s reviewed-classification metadata and is distinct from the per-unit `ink_identical` boolean, which is computed from the fonts at build time.

### 7.2 Unit shard (the parts the class’s `shards` list names) — an array of units, the parts concatenating in triage order

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

Field semantics: `secondary_seams` is optional (`null` or absent when the unit has no visible secondary seam, and never present on machine-approved units): a list of `{pair: {left, right}, before: rect, after: rect, home: "u-NNNN" | null}` entries per the §2.2 resolution rules, with rects in the same font-unit form as `highlight`; `ink_identical` is required on every unit in both modes (the contract checker enforces it); when true the unit is machine-approved, `batch` is `null`, and the frontend shows it only behind the “Show machine-approved” toggle with verdict controls disabled; on every machine-approved unit and every unit of a no-verdict class (`audit.slim_fragment`) the fragment is slim — `explain`, `drafts` and `highlight` (`audit.SLIM_OMITTED_KEYS`) are absent rather than null, so the app can tell a slim fragment from a whole record with a blank field — and `check_unit` refuses the opposite in both directions, so a human unit is never without its explain material and a slim unit never carries any of it; `text_entities` is the rendered run as numeric character references (never raw PUA — the frontend injects it with `innerHTML` into the sample cells only); `seams` arrays have one entry per inter-glyph gap (`break`, `lig`, or `yN`); `diff_positions` are glyph indices whose cell or trailing seam diverges; `pair` is the primary divergent adjacency to highlight (`null` for single-position divergences with no seam change); `pair_codepoints` is the primary pair’s covered codepoint-position span as an inclusive `[start, end]` (`null` exactly when `pair` is null) — computed at build time because ligatures make cell indices diverge from codepoint positions — and `notation_tokens` is the display-token list aligned one-to-one with codepoint positions (letter names like `·May` plus the boundary tokens `◊ZWNJ`/`␣`/`·`), such that joining them under the notation spacing rule (letters concatenate, boundary tokens take a space on each side) reproduces `notation` exactly; the frontend uses the two together to underline the pair on the notation and codepoints text lines; `highlight` x-values and `boundary_marks[].x` are in font units — the frontend converts with `font-size / upem`; `config_gate` is null for the general case (the set covers every non-ss10 config) and for a set no short conjunction pins, and otherwise the §2.1 clause list, each clause `{feature, state, text}` with `state` one of `on`/`off`; `config_note` is exactly the clause `text`s joined by spaces (the contract checker enforces that), or the literal “only under: …” fallback when the gate is null — the frontend draws one chip per clause and renders each `text` verbatim, falling back to a single unattributed chip carrying `config_note`; `render_groups` partitions `configs` by rendered-outcome identity — exactly one group under the M1 dedupe key, with any extra group rendered as a stacked before/after pair under its own feature settings; `summary` is the always-visible one-line prose summary in rune-name notation; `explain` is display-only preformatted text; `stylistic_set` is `null` or the space-separated zero-padded form (`"02 05"`); `content_key` (m1-audit mode, required) is the build-time stamp of the unit's carry identity — the sha256 of the presentation-free projection defined in `rebuild/review/unit_cache.py` — so `rebuild/tools/carry_verdicts.py` resolves prior verdicts by hash probe instead of re-serializing every unit, and older, unstamped surfaces hash to the same value; all strings are NFC, all keys snake_case. The fixture shard under `rebuild/review/fixtures/` contains about six hand-written units exercising every branch (multi-config, ZWNJ, namer dot, ligation, `pair: null`, a `duplicate_of` pin), and the contract checker in `test_review_build.py` validates fixtures and real output identically.

### 7.3 `verdicts.json`

As in §4.1 — produced by `verdicts.js`, consumed by `export.py`; the round-trip test in §5.1 is the integration gate.

### 7.4 The app sidecars — `app-units.ndjson.gz` and `app-locator.ndjson.gz`

A sub-contract of §7.2, not a second copy of it: both files are projections of the very shard fragments §7.2 defines, written by `rebuild/review/app_index.py` after the manifest so they can carry its stamp, and gzipped on disk with a pinned mtime so consecutive builds of the same inputs stay byte-identical. Each is NDJSON whose first line is a header — `{format, manifest_sha256, generated_at, units}`, formats `ams-review-app-index/1` and `ams-review-app-locator/1` — and a browser holding a header stamped for another `generated_at` refuses the file rather than reading its byte spans, because the ids of one build name other units in the next.

`app-units.ndjson.gz` carries one row per unit with `batch is not None` — exactly `manifest.human_unit_ids`, in shard order — projected onto the fields the app actually draws and nothing else. `app_index.app_row` is the authority for the key list and the projection; the shape worth knowing is that `explain`, `provenance`, and `drafts` are gone (§2.2), that the four machine-channel flags are dropped after being asserted false (a `batch is not None` unit provably carries none, which `build.check_unit` enforces), and that `after` is reduced to `{cells}` only for the rows whose `secondary_seams` include a homeless one, since those are the only rows that read it.

What replaces the dropped fields is an address: every row of both files ends with `shard_part`, `byte_start`, and `byte_length`, indexing `manifest.classes[…].shards` and the bytes of that part. That makes `build._write_shard`'s framing a byte-addressing contract as well as a serialization one — a fragment's bytes are a standalone JSON element with no enclosing punctuation interleaved, and `ensure_ascii=True` under a utf-8 handle makes a character offset a byte offset — so `bytes[byte_start:byte_start + byte_length]` parses on its own. `rebuild/test_app_index.py` slices every fragment of a built surface back out through its own span, which is what keeps a change to the dump's `indent`, `ensure_ascii`, or `separators` from breaking the addressing silently.

`app-locator.ndjson.gz` is `{id, class, shard_part, byte_start, byte_length}` for every unit the index omits — machine-approved and no-verdict alike — so a deep link to any id in the build resolves to a Range fetch of its record. It is a lookup file, never a resident one.
