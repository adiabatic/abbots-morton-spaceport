# Treaty-diff review surface — final report

## TL;DR

The §11 review surface is built, tested, audited, and ready for human triage. `rebuild/review/` (committed-shape source) generates a self-contained static app under the gitignored `rebuild/out/review/`: 2,411 review units covering all 15,528 M1 divergence rows from `rebuild/out/m1/divergence-audit.tsv`, sharded by the 15 ledger classes in `rebuild/m1-divergences.yaml` (14 nonzero; `kern-channel-out-of-scope` is count-0 by design), in 9 batches of up to 300. Every unit renders side-by-side in the old Senior Sans and the M1 mini-font via dual `@font-face`, with the divergent pair highlighted, correct per-row `font-feature-settings`, visible ZWNJ boundary marks, one-key home-row verdicts, and per-unit notes. The export loop closes: a downloaded `verdicts.json` converts to one triage YAML whose pins parse with the repo's real `parse_expect`, whose policy edits name resolvable `glyph_data/runes/*.yaml` keypaths and provenance, and whose any-of drafts feed the §10 channel. All gates are green: `make test` 6,753 passed, `uv run pytest rebuild/` 351 passed, `node --test` 38/38, byte-identical rebuilds, nothing committed or staged, and the existing build is untouched.

### Commands

```sh
uv run python -m rebuild.review.build                 # regenerate rebuild/out/review/
uv run python -m rebuild.review.serve                 # serve it on http://localhost:7294/
uv run python -m rebuild.review.export tmp/verdicts.json --out tmp/review-triage.yaml
```

Port 7294 deliberately coexists with `make serve` (`tools/serve.py`, port 7293); running both simultaneously was verified live. The server is not currently running — start it with the serve command above (the `tmp/serve7294.pid` left by the verification pass is stale).

## Quick start for triage

### Keyboard map

| Key      | Action                                              |
| -------- | --------------------------------------------------- |
| `j`      | Approve (the new behavior is right) + auto-advance  |
| `f`      | Reject (want the old behavior back) + auto-advance  |
| `d`      | Fine either way (any-of channel) + auto-advance     |
| `k`      | Skip + auto-advance                                 |
| `u`      | Undo last verdict                                   |
| `n`      | Focus the note input for the current unit           |
| `g`      | Approve the whole group under the cursor            |
| `x`      | Toggle the explain panel                            |
| `↓` `↑`  | Move cursor without verdicting                      |
| `[` `]`  | Previous / next batch                               |
| `?`      | Help overlay                                        |
| `Escape` | Blur input / close overlay                          |

Shortcuts are suppressed while a note input is focused; `Escape` gets you back out.

### Triage flow

- The sidebar lists the 14 ledger classes with the ledger's verbatim `why`/`status` text; clicking one filters to that class. Units within a class are grouped by family pair (fold/unfold per group), ordered by codepoints, and paged in batches of 300 (`[` / `]`).
- All view state (class, batch, cursor unit) lives in the URL hash — reloading or sharing the URL restores exactly where you were. Verdicts are in-memory only and do _not_ survive reload: export before closing the tab. The app warns when unexported verdicts exist.
- Each row shows the codepoint string in both fonts on a checkered background, the divergent pair highlighted with an amber band (precomputed x-ranges in font units; the frontend never measures text), config chips for ss03/ss10 rows rendered under their own feature settings, a visible tick on ZWNJ boundaries, a per-unit Copy button that yields a prompt preamble, and an explain panel (`x`) with the §6.3 attribution.
- _Export_ downloads `verdicts.json` (format `ams-review-verdicts/1`, includes notes); _Import_ merges a previous export back in by unit id (with a manifest-mismatch warning and a force path), so triage can span sessions.

### What to do with each exported artifact

Run the export CLI on the downloaded file; the resulting `tmp/review-triage.yaml` has three sections:

- **`pins`** (from approvals): whole-word `data-expect` strings, each already validated for syntax (the repo's real `parse_expect`) and semantics against the after-font. Copy each into its `suggested_home` (e.g. `site/the-manual.html`) as a `data-expect` / `data-expect-noncanonically` attribute — this is the opinions-become-pins loop from §10 item 5. `duplicate_of` flags pins already covered.
- **`policy_edits`** (from rejections): one-line `policy.prefer[+]` / `policy.refuse[+]` / `policy.contract[+]` records naming the target rune file, an appendable keypath, the deciding provenance records, and the decided stage. These are _drafts_, never auto-applied: review each, paste into the named `glyph_data/runes/*.yaml`, and rebuild. Units with `no_mechanical_draft` have no expressible one-line counter-lever — start from `names_provenance` and the unit's explain panel by hand.
- **`any_of`** (from fine-either-way): candidate expect strings (after-behavior first) for the any-of channel, realized as `_assert_expect_any` until the corpus connective exists. Units whose before/after behaviors are whole-word-identical (cell-only divergences) deliberately carry a single candidate.

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

15,528 rows in 2,411 units across 14 nonzero classes (the 15th, `kern-channel-out-of-scope`, is genuinely count-0). Suggested order: start with the small, individually judgeable classes to calibrate; leave the two big name-grain classes for last, where `g` (group-approve) and per-class bulk decisions earn their keep.

| Order | Class                               | Status         | Units | Rows  |
| ----- | ----------------------------------- | -------------- | ----- | ----- |
| 1     | `may-quad-order-deferral`           | drift-accepted | 1     | 7     |
| 2     | `zwnj-follower-exit-restored`       | drift-accepted | 6     | 30    |
| 3     | `pre-ligature-cleanup-regularized`  | drift-accepted | 6     | 38    |
| 4     | `pea-chain-regularized`             | drift-accepted | 15    | 101   |
| 5     | `ss03-chain-join-gains`             | drift-accepted | 40    | 120   |
| 6     | `marker-staging-ligature-formation` | intended       | 52    | 187   |
| 7     | `same-seam-extension-non-summing`   | intended       | 33    | 225   |
| 8     | `entered-it-baseline-join-gain`     | drift-accepted | 46    | 319   |
| 9     | `regrouping-floor-drift`            | drift-accepted | 64    | 435   |
| 10    | `bare-name-live-join`               | intended       | 139   | 917   |
| 11    | `halves-entry-extension-restored`   | drift-accepted | 193   | 1,218 |
| 12    | `may-exit-withdrawal-generalized`   | drift-accepted | 269   | 1,227 |
| 13    | `zwnj-word-initial-unification`     | intended       | 213   | 1,421 |
| 14    | `dangling-anchor-dropped`           | intended       | 1,334 | 9,283 |

Classes 1–9 total 1,462 rows in 263 units — an afternoon of careful, one-at-a-time judgment. Classes 10–14 are the name-grain bulk (14,066 rows, 2,148 units) where the divergences are mechanical consequences of the same few seams; spot-check a group per family pair, then `g` through the rest or accept per-class.

## Architecture notes for cutover

- **Source layout**: `rebuild/review/` is a normal package — `audit.py` (TSV + ledger ingestion), `tablediff.py`, `enrich.py` (seams, divergent positions, highlight ranges, explain attribution, all precomputed at build time), `drafts.py` (the three verdict drafters), `build.py`, `export.py`, `serve.py`, `static/` (the five ES modules + CSS + HTML shell), `jstests/`. Tests live flat as `rebuild/test_review_*.py` plus `node --test` over `jstests/`.
- **Output contract**: `rebuild/out/review/` is fully self-contained — `index.html`, app JS/CSS, `manifest.json` (format `ams-review-manifest/1`, per-class counts and sha256-recorded font copies), `units/<class>.json` shards lazily fetched per class, `fonts/before.otf` + `fonts/after.otf`. The manifest plus shard schema (REVIEW-PLAN.md §7) is the only frontend↔engine interface, and the build runs its own contract self-check. Builds are byte-identical run to run (modulo `generated_at`).
- **Future migrations**: `build.py --mode table-diff --baseline <dir> --new <dir>` plus the `snapshot` subcommand generalize beyond the M1 audit TSV, so M2+ reviews reuse the same app unchanged.
- **At cutover**: the app moves out of `rebuild/out/` and gets a Makefile target plus (probably) a merge of the serve script into `tools/serve.py`'s livereload. Until then, no Makefile changes, no `site/` changes, no existing pipeline files touched.
- **Known leftovers, not blocking**: a `{"type":"module"}` `package.json` is copied verbatim from `static/` into the output (harmless); `rebuild/review/fixtures/` and `rebuild/review/jstests/fixtures/` are divergent copies that should converge on one source of truth before cutover.

## Audit outcome and fixes

The adversarial audit returned _gaps-found_: workload completeness, rendering faithfulness, the §11 line-by-line checklist, the user decisions (port, output location, URL state, nested CSS, no `.forEach`), and repo footprint all passed outright, but the policy drafter had two real gaps, both fixed and re-verified:

1. **Contract drafts riding a new join (major, 43 units)**: where the divergence includes a join the baseline didn't have, a `contract by: 1` lever cannot restore the old break. `drafts.py` now detects the new-join side against the baseline seam and drafts `policy.refuse[+]` on the anchor reaching across that gap, scoped to that neighbor (e.g. u-0275 ·May·It·Tea·Oy now drafts `{exit: x-height, when: {right: {family: [qsIt]}}, …}`). Contract drafts only fire when the gained extension rides a join both fonts share, with the `when` window following the extension's side.
2. **Refuse drafts on seam-identical units (minor, 826 units)**: a refuse would have broken joins both fonts share. 633 units now draft `policy.prefer[+]` with `mode: absolute`, pinning the baseline cell (or stance) over the new anchors; the remaining 193 adjustment-grain units (post-ZWNJ locked twins, bind pullbacks, non-summing-suppressed extensions) have no expressible one-line counter-lever and now carry no policy draft — `export.py` still emits them in `policy_edits` with null fields, the unit's provenance, and a `no_mechanical_draft` explanation, so rejected units never vanish from the triage YAML.

Post-fix population reconciliation across all 2,411 units: prefer 1,941 / refuse 126 / contract 151 / no-draft 193, with zero contracts riding a new join, zero refuses on seam-identical units, zero schema-invalid records. Five new pytest cases lock the fixes in; `uv run pytest rebuild/` is now 351 passed, 1 skipped (the pre-existing, unrelated jsonschema guard in `test_spec_load.py`), `make test` 6,753 passed.

## Recommended next step

Start the server (`uv run python -m rebuild.review.serve`), open `http://localhost:7294/`, and run the manual checklist above on a small class — `may-quad-order-deferral` (1 unit) then `zwnj-follower-exit-restored` (6 units) are the natural smoke tests. If the surface feels right, triage classes 1–9, export, run the export CLI, and review the drafted pins and policy edits against `glyph_data/runes/*.yaml` — that first real export is the end-to-end proof of the opinions-become-pins loop on your own verdicts. Defer the two 1,200+ unit name-grain classes to a bulk session with `g`. Nothing is committed; when you're satisfied, the `rebuild/review/` source tree (not `rebuild/out/`) is what gets committed.
