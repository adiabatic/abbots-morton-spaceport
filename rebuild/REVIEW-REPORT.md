# Treaty-diff review surface — final report

## TL;DR

The §11 review surface is built, tested, audited, and ready for human triage. `rebuild/review/` (committed-shape source) generates a self-contained static app under the gitignored `rebuild/out/review/`: 2,411 review units covering all 15,528 M1 divergence rows from `rebuild/out/m1/divergence-audit.tsv`, sharded by the 15 ledger classes in `rebuild/m1-divergences.yaml` (14 nonzero; `kern-channel-out-of-scope` is count-0 by design). **The human workload is 725 units in 3 batches of up to 300**: the other 1,686 units are machine-approved at build time because both fonts render them pixel-identically once kerning is neutralized — `rebuild/review/ink.py` shapes every unit in both shipped fonts under every config in its set (uharfbuzz, with `kern` disabled on both sides), records each glyph's outline with fontTools' `DecomposingRecordingPen`, translates it by the cumulative advance plus per-glyph offsets, and compares the sorted placed pieces; only glyph names differ on those 1,686, so no human verdict is meaningful. Every unit renders side-by-side in the old Senior Sans and the M1 mini-font via dual `@font-face`, with the divergent pair highlighted, correct per-row `font-feature-settings`, visible ZWNJ boundary marks, one-key home-row verdicts, and per-unit notes. The export loop closes: a downloaded `verdicts.json` converts to one triage YAML whose pins parse with the repo's real `parse_expect`, whose policy edits name resolvable `glyph_data/runes/*.yaml` keypaths and provenance, and whose any-of drafts feed the §10 channel. All gates are green: `make test` 6,753 passed, `uv run pytest rebuild/` 368 passed, `node --test` 47/47, byte-identical rebuilds, nothing committed or staged, and the existing build is untouched. A user-feedback rework (2026-06-10) remapped the verdict keys to `a`/`s`/`d`/`f`, made config chips inert labels backed by build-time `render_groups` (whole-unit verdicts only), and added an always-visible one-line explain summary plus a Why? button per row. A follow-up (same day) removed the chip strip entirely: chips were uninformative for the 2,028 units covering all seven non-ss10 configs, so each unit now carries a build-time `config_note` and the page shows a single inert badge only when that note is non-null — the ss03-gated (149), ss03-excluded (217), and ss10-only (17) cases, which also explain the row's `font-feature-settings`. A second follow-up (same day) added the ink-identity machine approval described above: ink-identical units carry `batch: null`, are hidden behind the "Show machine-approved" toggle, and are reported as a `machine_approved` section in the manifest and the triage YAML. A third follow-up (same day) made the whole surface kern-neutral — every build-side shape call passes `kern: False` for both fonts and both sample columns render with `font-kerning: none` — which machine-approved the 137 former name-grain stragglers whose only difference was the old font's kerning; the census numbers (1,686 machine-approved / 725 human, with the three name-grain classes fully machine-approved) are pinned by tests and must reproduce on every rebuild. A fourth follow-up (2026-06-10) made reject a two-step verdict — `s` opens a popup offering `s` no comment, `a` the canned note "the old way seems nicer to write out by hand", `f` the canned note "the new way is broken", with `Escape` canceling — and added `k`/`i` as home-row aliases for next/previous unit.

### Commands

```sh
make review-build                                     # regenerate rebuild/out/review/
make review-serve                                     # serve it on http://localhost:7294/
uv run python -m rebuild.review.build                 # the same build, spelled out
uv run python -m rebuild.review.serve                 # the same server, spelled out
uv run python -m rebuild.review.export tmp/verdicts.json --out tmp/review-triage.yaml
```

The Makefile targets are `review-build` / `review-serve` because plain `review` was already taken by the scoped-anchor-selector review page.

Port 7294 deliberately coexists with `make serve` (`tools/serve.py`, port 7293); running both simultaneously was verified live. The server is not currently running — start it with the serve command above (the `tmp/serve7294.pid` left by the verification pass is stale).

## Quick start for triage

### Kerning is disabled

Both columns render with kerning off (`font-kerning: none` on the sample cells, composing with each row's `font-feature-settings`), and every build-side comparison — the ink-identity census, highlight x-ranges, boundary-mark positions, pin semantics — shapes with `kern: False` in both fonts. The rebuild pipeline deliberately has no kerning until the §12 milestone, so the old font's kern feature is pure noise in before/after comparisons; kern differences get their own review when that milestone lands. This is a no-op on the after font today, but the rule is explicit so it survives §12. Consequence: the three name-grain classes' former visible stragglers (137 units) were kern-only and are now machine-approved.

### Keyboard map

| Key      | Action                                                                                                                                       |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `a`      | Skip + auto-advance                                                                                                                          |
| `s`      | Reject (want the old behavior back) — opens the follow-up popup: `s` no comment, `a` / `f` canned notes; `Escape` cancels. Then auto-advance |
| `d`      | Fine either way (any-of channel) + auto-advance                                                                                              |
| `c`      | Neither — both behaviors look wrong (follow-up) + auto-advance                                                                               |
| `f`      | Approve (the new behavior is right) + auto-advance                                                                                           |
| `u`      | Undo last verdict                                                                                                                            |
| `n`      | Focus the note input for the current unit (`Enter` saves and advances)                                                                       |
| `g`      | Approve the whole group under the cursor                                                                                                     |
| `x`      | Toggle the explain panel (same as the row's Why? button)                                                                                     |
| `↓` `↑`  | Move cursor without verdicting                                                                                                               |
| `k` `i`  | Same as `↓` `↑` — next / previous unit                                                                                                       |
| `[` `]`  | Previous / next class (toolbar arrows page batches)                                                                                          |
| `?`      | Help overlay                                                                                                                                 |
| `Escape` | Blur input / close overlay (or cancel the reject popup)                                                                                      |

The four main verdict keys sit on the left home row: `a` skip, `s` reject, `d` fine-either-way, `f` approve. Rejecting is a two-step: `s` (or the Reject button) opens a small popup anchored to the row's verdict buttons, and a second key records the reject — `s` with no comment, `a` with the canned note "the old way seems nicer to write out by hand", `f` with "the new way is broken" (a canned note overwrites whatever was in the note field), `x` records the reject and drops you straight into the note field for a custom comment; `Escape` or a click anywhere outside cancels with no verdict, and every other shortcut is suppressed while the popup is open. The fifth verdict, `c` neither, means both behaviors look wrong — the unit needs follow-up authoring work rather than a pick; its button sits directly below Either in the verdict grid. `k` and `i` are home-row aliases for `↓` and `↑`. Shortcuts are suppressed while a note input is focused; `Escape` gets you back out. A verdict always covers the whole unit — every config in its config set (most units show no config marker because they diverge under every non-ss10 config; when a small badge like "only when ss03 is on" is present, hover it for the full list). Approving a unit draws a green outline around its after sample; rejecting overlays a red X on it; either outlines both samples green (both are fine); neither overlays the red X on both (both are wrong) — all four visuals follow the row's recorded verdict, so they survive re-render and import, and clear on undo.

### Reading the explain panel

Every row carries an always-visible one-line summary under the renderings — what the new pipeline chose at the primary divergence and the single deciding record, in rune-name notation, e.g. "New: ·It joins ·It at the baseline (the old pipeline broke there) — decided by qsIt.yaml policy.extend[0] (join-count rank)." When that line is not enough, the Why? button (or `x`) opens the full panel:

- **Explain** is the candidate table the settlement function considered at each divergent position: every surviving candidate with its entry/seam and join-count, `->` marking the winner, the eliminated candidates each attributed to the YAML record that eliminated them, and a `decided by:` line naming the stage that separated the winner from the runner-up (join-count rank, declaration order, a prefer, or the structural floor).
- **Provenance** lists the `glyph_data/runes/*.yaml` records the outcome was attributed to — these are the levers a reject's policy draft will name.
- **Drafts** previews the three precomputed verdict artifacts (pin / policy edit / any-of) this unit would export.

### Triage flow

- The sidebar lists the 14 ledger classes with the ledger's verbatim `why`/`status` text; clicking one filters to that class. Units within a class are grouped by family pair (fold/unfold per group), ordered by codepoints, and paged in batches of 300 (`[` / `]`). Sidebar counts, batch labels, and progress denominators all count the human workload; classes with machine-approved units show that count as a separate "· N machine" suffix, and the header carries a distinct "1,686 ink-identical units machine-approved" line.
- Ink-identical (machine-approved) units are hidden by default. The toolbar's "Show machine-approved" toggle (state in the URL hash as `machine=1`) reveals them, grouped by class in collapsed folds below the human units, each row carrying an inert "ink-identical — machine approved" badge and disabled verdict buttons whose title explains why: both fonts render the unit identically, so no human input is meaningful. Deep-linking to `#unit=<an ink-identical id>` with the toggle off reveals just that one unit transiently — the persistent toggle stays off, the URL stays clean, and navigating away hides it again. Keyboard flow, auto-advance, and `g` group-approve operate over the human workload only.
- All view state (class, batch, cursor unit) lives in the URL hash — reloading or sharing the URL restores exactly where you were. Verdicts are in-memory only and do _not_ survive reload: export before closing the tab. The app warns when unexported verdicts exist.
- Each row shows the codepoint string in both fonts on a checkered background, the divergent pair highlighted with an amber band (precomputed x-ranges in font units; the frontend never measures text), a visible tick on ZWNJ boundaries, a per-unit Copy button that yields a prompt preamble, the one-line summary sentence, and a Why? button (or `x`) opening the explain panel with the §6.3 attribution. Unmarked regions can be trusted: they render identically in both fonts, or differ only invisibly at name grain.
- Longer units can contain divergent seams beyond the amber-banded primary pair. Each such secondary seam renders in both columns as a dimmer dashed band with a small chip naming its home unit — the shortest unit where the same before/after behavior is the primary judgment — and clicking the chip deep-links there (cross-batch, cross-class, machine-approved homes included). A chip reading "only here" means no shorter home exists (a genuinely context-dependent divergence at the depth-2 horizon): judge that seam in this unit. Seams whose home is ink-identical get no marker at all — the difference is an invisible name-grain rename with nothing to see. On M1 data: 159 units carry visible markers — 43 seams homed, 129 "only here", 51 suppressed as invisible (census pinned by tests and recorded in the manifest).
- There is no config-chip strip: chips carried no information for the 2,028-unit majority that diverges under all seven non-ss10 configs, so they were removed. Instead, a unit shows one small inert badge only when its build-time `config_note` is non-null — computed generically from the unit's config set against the manifest's full list as "only when f is on" (the set is exactly the configs containing feature f; phrased "only under ss10" for the isolation overlay), "only when f is off" (exactly the non-ss10 configs without f), or a literal "only under: …" fallback. On M1 data that surfaces exactly the judgment-relevant cases — ss03-gated (149), ss03-excluded (217), ss10-only (17) — which also explain the row's `font-feature-settings`; the badge's title lists the full config set verbatim.
- Units are deduplicated by (codepoints, baseline outcome, new outcome), so every config of a unit renders identically by construction; the build groups configs by rendered-outcome identity into `render_groups` and the tests pin the invariant that every M1 unit has exactly one group. If future data ever violates it, the app renders each extra group's before/after pair stacked below the first with its own config label and feature settings — nothing is collapsed, nothing needs clicking.
- Verdicts always cover the whole unit (all of its configs); the per-config verdict scoping that the original chips offered is gone, and `verdicts.json` records carry no `configs` field (legacy files importing one have it ignored).
- _Export_ downloads `verdicts.json` (format `ams-review-verdicts/1`, includes notes); _Import_ merges a previous export back in by unit id (with a manifest-mismatch warning and a force path), so triage can span sessions.

### What to do with each exported artifact

Run the export CLI on the downloaded file; the resulting `tmp/review-triage.yaml` has four sections:

- **`pins`** (from approvals): whole-word `data-expect` strings, each already validated for syntax (the repo's real `parse_expect`) and semantics against the after-font. Copy each into its `suggested_home` (e.g. `site/the-manual.html`) as a `data-expect` / `data-expect-noncanonically` attribute — this is the opinions-become-pins loop from §10 item 5. `duplicate_of` flags pins already covered.
- **`policy_edits`** (from rejections): one-line `policy.prefer[+]` / `policy.refuse[+]` / `policy.contract[+]` records naming the target rune file, an appendable keypath, the deciding provenance records, and the decided stage. These are _drafts_, never auto-applied: review each, paste into the named `glyph_data/runes/*.yaml`, and rebuild. Units with `no_mechanical_draft` have no expressible one-line counter-lever — start from `names_provenance` and the unit's explain panel by hand.
- **`any_of`** (from fine-either-way): candidate expect strings (after-behavior first) for the any-of channel, realized as `_assert_expect_any` until the corpus connective exists. Units whose before/after behaviors are whole-word-identical (cell-only divergences) deliberately carry a single candidate.
- **`neither`** (from neither-verdicts): both the old and the new behavior look wrong, so nothing automatic is drafted — no pin, no policy edit, no any-of. Each entry carries the unit id, codepoints, notation, the reviewer's note, and `names_provenance` (the `glyph_data/runes/*.yaml` records the outcome was attributed to — the follow-up author's levers). These units need fresh authoring work, starting from the provenance and the unit's explain panel.

## What was verified mechanically vs what needs your eyes

A real headless Chrome was driven over CDP (no project dependencies added), so the app's behavior — not just its code — was verified live: 300 rows render with full chrome; both `@font-face` fonts load and pass `document.fonts.check` for U+E650; a pair-highlight band is visible at the computed position; a ZWNJ row shows its boundary tick; computed `font-feature-settings` is `ss03` on an ss row; 10 keyed verdicts recorded with auto-advance and hash cursor tracking; undo, note entry (with shortcut suppression and `Escape` blur), class-filter clicks writing the hash, a true reload restoring class/batch/cursor from the URL, and the Download blob being valid `ams-review-verdicts/1` JSON all confirmed. Data fidelity was checked at full population: all 15,528 TSV rows map 1:1 onto the 2,411 units with zero missing/extra/duplicate keys, and HarfBuzz shaping of six representative rows in both shipped OTFs reproduces the stored highlight ranges exactly.

What only a human can confirm — this is the §8-step-4 manual pass:

- [ ] The rendered Quikscript actually looks right in both columns (glyph quality, the checkered background reads well at your zoom level)
- [ ] The highlight band lands on the pair your eye identifies as divergent
- [ ] Scroll feel and batch-navigation responsiveness with the 4 MB `dangling-anchor-dropped` shard loaded
- [ ] Clipboard copy UX (the per-unit Copy button and the verdict copy-out)
- [ ] The real file-download path through the browser chrome, and a round-trip Import of that file
- [ ] The actual triage judgments — every verdict below is yours to make

## The workload

15,528 rows in 2,411 units across 14 nonzero classes (the 15th, `kern-channel-out-of-scope`, is genuinely count-0). Of those, **1,686 units (11,621 rows) are machine-approved as ink-identical** under kern-neutral shaping and need no eyes, leaving **725 human units (3,907 rows) in 3 batches**. The three name-grain classes that used to be the bulk-approval slog are now fully machine-approved — their former 137 visible stragglers differed only in the old font's kerning, which the surface now disables on both sides: `dangling-anchor-dropped` 1,334 of 1,334, `zwnj-word-initial-unification` 213 of 213, `bare-name-live-join` 139 of 139. Suggested order: start with the small, individually judgeable classes to calibrate.

| Order | Class                               | Status         | Human units | Human rows | Machine-approved |
| ----- | ----------------------------------- | -------------- | ----------- | ---------- | ---------------- |
| 1     | `zwnj-word-initial-unification`     | intended       | 0           | 0          | 213              |
| 2     | `dangling-anchor-dropped`           | intended       | 0           | 0          | 1,334            |
| 3     | `bare-name-live-join`               | intended       | 0           | 0          | 139              |
| 4     | `may-quad-order-deferral`           | drift-accepted | 1           | 7          | 0                |
| 5     | `zwnj-follower-exit-restored`       | drift-accepted | 6           | 30         | 0                |
| 6     | `pre-ligature-cleanup-regularized`  | drift-accepted | 6           | 38         | 0                |
| 7     | `pea-chain-regularized`             | drift-accepted | 15          | 101        | 0                |
| 8     | `same-seam-extension-non-summing`   | intended       | 33          | 225        | 0                |
| 9     | `ss03-chain-join-gains`             | drift-accepted | 40          | 120        | 0                |
| 10    | `entered-it-baseline-join-gain`     | drift-accepted | 46          | 319        | 0                |
| 11    | `marker-staging-ligature-formation` | intended       | 52          | 187        | 0                |
| 12    | `regrouping-floor-drift`            | drift-accepted | 64          | 435        | 0                |
| 13    | `halves-entry-extension-restored`   | drift-accepted | 193         | 1,218      | 0                |
| 14    | `may-exit-withdrawal-generalized`   | drift-accepted | 269         | 1,227      | 0                |

Classes 4–12 total 263 units — careful, one-at-a-time judgment. Classes 13–14 (462 units) keep `g` (group-approve) useful within family-pair groups. Every remaining human unit genuinely renders differently in the two fonts under kern-neutral shaping (the per-unit machine verdict is what protects against blind class-level `g`-approval).

### Machine-approved units

`rebuild/review/ink.py` is the build-time gate: a unit is `ink_identical` iff shaping its text in both shipped fonts under **every** config in its set produces identical placed outlines (uharfbuzz shaping via the validation `Shaper`, always with `kern: False` merged into the config's features via `ink.kern_neutral`; per-glyph `DecomposingRecordingPen` outlines translated by the cumulative `x_advance` plus `x_offset`/`y_offset`; pieces sorted and compared). These units get `batch: null`, are excluded from batches, sidebar counts, and progress denominators, and surface in the app only behind the "Show machine-approved" toggle, badged and verdict-disabled. The manifest records the totals (`machine_approved`: 1,686 units / 11,621 rows, the method one-liner, per-class counts), the export CLI reports them as a `machine_approved` section in the triage YAML (counts, per-class counts, method, compact unit-id ranges — never as drafted pins), and `verdicts.json` stays human-verdicts-only. The numbers are pinned by tests and must reproduce exactly on a rebuild; in table-diff mode the same comparison runs over each entry's witness string, and a witnessless entry stays in the human workload because there is nothing to shape.

## Architecture notes for cutover

- **Source layout**: `rebuild/review/` is a normal package — `audit.py` (TSV + ledger ingestion), `tablediff.py`, `enrich.py` (seams, divergent positions, highlight ranges, explain attribution, all precomputed at build time), `drafts.py` (the three verdict drafters), `build.py`, `export.py`, `serve.py`, `static/` (the five ES modules + CSS + HTML shell), `jstests/`. Tests live flat as `rebuild/test_review_*.py` plus `node --test` over `jstests/`.
- **Output contract**: `rebuild/out/review/` is fully self-contained — `index.html`, app JS/CSS, `manifest.json` (format `ams-review-manifest/1`, per-class counts and sha256-recorded font copies), `units/<class>.json` shards lazily fetched per class, `fonts/before.otf` + `fonts/after.otf`. The manifest plus shard schema (REVIEW-PLAN.md §7) is the only frontend↔engine interface, and the build runs its own contract self-check. Builds are byte-identical run to run (modulo `generated_at`).
- **Future migrations**: `build.py --mode table-diff --baseline <dir> --new <dir>` plus the `snapshot` subcommand generalize beyond the M1 audit TSV, so M2+ reviews reuse the same app unchanged.
- **At cutover**: the app moves out of `rebuild/out/` and (probably) the serve script merges into `tools/serve.py`'s livereload. The user-sanctioned `review-build` / `review-serve` Makefile targets already exist (plain `review` was taken); no `site/` changes, no existing pipeline files touched.
- **Known leftovers, not blocking**: a `{"type":"module"}` `package.json` is copied verbatim from `static/` into the output (harmless); `rebuild/review/fixtures/` and `rebuild/review/jstests/fixtures/` are divergent copies that should converge on one source of truth before cutover.

## Audit outcome and fixes

The adversarial audit returned _gaps-found_: workload completeness, rendering faithfulness, the §11 line-by-line checklist, the user decisions (port, output location, URL state, nested CSS, no `.forEach`), and repo footprint all passed outright, but the policy drafter had two real gaps, both fixed and re-verified:

1. **Contract drafts riding a new join (major, 43 units)**: where the divergence includes a join the baseline didn't have, a `contract by: 1` lever cannot restore the old break. `drafts.py` now detects the new-join side against the baseline seam and drafts `policy.refuse[+]` on the anchor reaching across that gap, scoped to that neighbor (e.g. u-0275 ·May·It·Tea·Oy now drafts `{exit: x-height, when: {right: {family: [qsIt]}}, …}`). Contract drafts only fire when the gained extension rides a join both fonts share, with the `when` window following the extension's side.
2. **Refuse drafts on seam-identical units (minor, 826 units)**: a refuse would have broken joins both fonts share. 633 units now draft `policy.prefer[+]` with `mode: absolute`, pinning the baseline cell (or stance) over the new anchors; the remaining 193 adjustment-grain units (post-ZWNJ locked twins, bind pullbacks, non-summing-suppressed extensions) have no expressible one-line counter-lever and now carry no policy draft — `export.py` still emits them in `policy_edits` with null fields, the unit's provenance, and a `no_mechanical_draft` explanation, so rejected units never vanish from the triage YAML.

Post-fix population reconciliation across all 2,411 units: prefer 1,941 / refuse 126 / contract 151 / no-draft 193, with zero contracts riding a new join, zero refuses on seam-identical units, zero schema-invalid records. Five new pytest cases lock the fixes in; `uv run pytest rebuild/` is now 351 passed, 1 skipped (the pre-existing, unrelated jsonschema guard in `test_spec_load.py`), `make test` 6,753 passed.

## Recommended next step

Start the server (`uv run python -m rebuild.review.serve`), open `http://localhost:7294/`, and run the manual checklist above on a small class — `may-quad-order-deferral` (1 unit) then `zwnj-follower-exit-restored` (6 units) are the natural smoke tests. If the surface feels right, triage classes 1–9, export, run the export CLI, and review the drafted pins and policy edits against `glyph_data/runes/*.yaml` — that first real export is the end-to-end proof of the opinions-become-pins loop on your own verdicts. Defer the two 1,200+ unit name-grain classes to a bulk session with `g`. Nothing is committed; when you're satisfied, the `rebuild/review/` source tree (not `rebuild/out/`) is what gets committed.
