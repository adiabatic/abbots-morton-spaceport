# Review-surface recon B: data and provenance plumbing

Recon for the §11 review surface generator. All paths are repo-relative. Numbers were measured against HEAD `7fd5966` artifacts on 2026-06-10.

## 1. Artifact schemas

### 1.1 `rebuild/out/m1/divergence-audit.tsv`

Written by `compare_against_baseline` in `rebuild/pipeline/conform.py` (the `audit_lines` block). One header line plus 15,528 data rows. Tab-separated, six columns:

| #   | Column          | Meaning                                                                                                                                                                                                                                                                                                          |
| --- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `config`        | Acceptance config token, one of `default`, `ss02`, `ss03`, `ss04`, `ss05`, `ss02+ss03`, `ss02+ss03+ss05`, `ss10` (`ACCEPTANCE_CONFIGS` in `conform.py`). Row counts: 2,245 each for default/ss02/ss04/ss05, 2,177 each for ss03/ss02+ss03/ss02+ss03+ss05, 17 for ss10.                                           |
| 2   | `codepoints`    | Colon-joined uppercase hex, e.g. `200C:E652:E679`. This is the input string; lengths are 2 (102 rows), 3 (1,324), or 4 (14,102). Alphabet: `E650 E652 E665 E670 E679` plus boundary tokens `0020` (space), `00B7` (namer dot), `200C` (ZWNJ).                                                                    |
| 3   | `kinds`         | Comma-joined divergence channels from `DivergentRow.kinds`. Observed values: `cell` (14,291), `cell,seam` (1,050), `ligation` (187). The schema also allows `unaliased` and `position`, which a passing run never emits.                                                                                         |
| 4   | `matched_entry` | The single ledger class id from `classify_divergence` / `_match_ledger`, e.g. `dangling-anchor-dropped`. Would be `UNMATCHED` or `id1+id2` on a failing run; the committed audit has exactly one id per row. 14 classes appear (the ledger's 15th, `kern-channel-out-of-scope`, has count 0).                    |
| 5   | `baseline`      | `\|`-joined old compiled glyph names exactly as today's font shapes the string (from the subset baseline `Row.glyphs`), e.g. `qsPea.half.ex-y5.ex-dips\|qsIt.en-y5.ex-y0`. Boundary positions appear as `space` / `periodcentered` (the old font shapes U+200C to the `space` glyph).                            |
| 6   | `new`           | `\|`-joined new cell tokens from `_cell_token`: `rune/stance/entry/exit/adjustments` with literal `None` for absent anchors and `+`-joined adjustments, e.g. `qsPea/half/None/x-height/` or `qsMay/loop/baseline/None/en-ext-1`. Entry/exit are row names (`baseline`, `x-height`, `y6`), not `en-yN` spellings. |

Sample rows:

```text
default    E650:E665    cell    dangling-anchor-dropped    qsPea|qsMay.en-y0.ex-y5.en-ext-1    qsPea/full/None/baseline/|qsMay/loop/baseline/None/en-ext-1
default    E650:E670    cell    dangling-anchor-dropped    qsPea.half.ex-y5.ex-dips|qsIt.en-y5.ex-y0    qsPea/half/None/x-height/|qsIt/bar/x-height/None/
ss03    200C:E652:E679    ligation    marker-staging-ligature-formation    space|qsTea_qsOy    uni200C|qsTea_qsOy/bar-into-loop/None/None/+locked
```

Row identity: **(`config`, `codepoints`) is the primary key** — each baseline string appears once per config table. The old outcome is column 5 (plus old seams, which are _not_ in the TSV; see §5). The new outcome is column 6 (new seams also not in the TSV). The ledger class is column 4. Exemplar status is not a column: a row is an exemplar iff its (`config`, `codepoints`) pair appears in some ledger entry's `exemplars` list (all 19 ledger exemplar keys resolve to audit rows; verified).

### 1.2 `rebuild/m1-divergences.yaml`

Hand-reviewed ledger, a YAML list of 15 entries. Fields per entry:

| Field           | Type                                          | Notes                                                                                                                                                                                 |
| --------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`            | string                                        | Joins to audit column `matched_entry`.                                                                                                                                                |
| `status`        | `intended` \| `drift-accepted` \| `triaged`   | 5 intended, 9 drift-accepted, 1 triaged (the zero-count kern class).                                                                                                                  |
| `match`         | mapping                                       | `{predicate: <name in conform.PREDICATES>, configs: all}` for every nonzero entry; the schema also supports `configs: [list]`, `window:` (substring of `codepoints`), `seam_change:`. |
| `ink_identical` | bool, optional                                | Present on 3 entries; gates the §9 position-drift channel.                                                                                                                            |
| `count`         | int                                           | Filled from the conformance run; sums to 15,528.                                                                                                                                      |
| `exemplars`     | list of `{config, codepoints, baseline, new}` | 19 total. `baseline` / `new` here are prose-annotated (parenthetical commentary like `(y5 join kept)`) — display-only, not machine-parseable; key on `config` + `codepoints`.         |
| `why`           | block scalar                                  | The reviewed rationale — the natural per-class blurb for the review page.                                                                                                             |

Class counts: dangling-anchor-dropped 9,283; zwnj-word-initial-unification 1,421; may-exit-withdrawal-generalized 1,227; halves-entry-extension-restored 1,218; bare-name-live-join 917; regrouping-floor-drift 435; entered-it-baseline-join-gain 319; same-seam-extension-non-summing 225; marker-staging-ligature-formation 187; ss03-chain-join-gains 120; pea-chain-regularized 101; pre-ligature-cleanup-regularized 38; zwnj-follower-exit-restored 30; may-quad-order-deferral 7; kern-channel-out-of-scope 0.

### 1.3 Settlement tables: `rebuild/out/m1/settlement-{config}.tsv` (8 files)

Written by `DecisionTable.write_tsv` in `rebuild/pipeline/table.py`. Line 1 is a comment `# settlement table, config <token>`; line 2 is the column header. Sorted, diff-stable. 96 data rows for `default`.

| Column                      | Meaning                                                                                                                                                                                  |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `input`                     | The raw (pre-settlement) glyph at the position, a bare rune name like `qsIt`.                                                                                                            |
| `backtrack`                 | Space-joined set of allowed settled-left labels (cell labels in dot form, or boundary glyph names `space` / `uni200C` / `periodcentered`); `-` means unconstrained (the fallback row).   |
| `lookahead1` / `lookahead2` | Space-joined sets of allowed raw right-window glyphs; `-` means unconstrained.                                                                                                           |
| `outcome`                   | The settled cell as a `settle.cell_label` dot-form name, e.g. `qsIt.bar.en-y5.ex-y0.ex-ext-1`.                                                                                           |
| `joint`                     | `joint` when the row is §6.1 joint-dependent (routed to the expensive test tier), else `-`.                                                                                              |
| `provenance`                | Semicolon-joined deduped pointers to the deciding YAML records, `glyph_data/runes/qsIt.yaml:policy.extend[0]` style, occasionally followed by a prose note from the trace. May be empty. |

Sample rows (`settlement-default.tsv`):

```text
qsIt    qsTea.half.ex-y5 qsTea.half.ex-y5.locked    uni200C space periodcentered    -    qsIt.bar.en-y5.en-ext-1    -    glyph_data/runes/qsIt.yaml:policy.extend[0]
qsIt    qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1 ... qsPea.half.ex-y5.locked    qsIt qsMay    -    qsIt.bar.en-y5.ex-y0.ex-ext-1    joint    glyph_data/runes/qsIt.yaml:policy.extend[2]
qsIt    qsIt.bar.en-y5.ex-y0.en-ext-1.ex-ext-1 ... qsTea_qsOy.bar-into-loop.ex-y0    -    -    qsIt.bar.en-y0    -    (empty provenance)
```

### 1.4 Treaty tables: `rebuild/out/m1/treaties-{config}.tsv` (8 files)

Written by `TreatyTable.write_tsv` in `table.py`. Same two header lines, then one row per reachable adjacent cell pair. 309 data rows for `default`.

| Column      | Meaning                                                           |
| ----------- | ----------------------------------------------------------------- |
| `left`      | Left cell label, dot form (`qsIt.bar`, `qsOy.loop.ex-y0.locked`). |
| `right`     | Right cell label, dot form.                                       |
| `junction`  | `break` or `yN` (the seam row, e.g. `y0`, `y5`, `y6`).            |
| `extension` | Summed seam extension in pixels (int).                            |
| `kern`      | Kern value (int; all 0 in M1 — the kern channel is out of scope). |

Sample rows:

```text
qsIt.bar    qsIt.bar    break    0    0
qsIt.bar    qsOy.loop.ex-y0    break    0    0
qsIt.bar    qsPea.full.ex-y0    break    0    0
```

Both TSV families exist for the same 8 config tokens as the audit (no ss06/ss07 in M1 scope).

### 1.5 `rebuild/m1-contact-allow.yaml`

Hand-reviewed allow list for the §9 off-anchor-contact gate. YAML list of 20 entries, each `{signature, why}`. Signature shape is `defects.py`'s `contact:<left glyph>:<right glyph>:y<row>`, with glyphs in dot-form cell-label names:

```yaml
- signature: contact:qsOy.loop.ex-y0:qsIt.bar.en-y0:y1
  why: ·Oy·It joins at the baseline today (baseline row E679:E670, seam y0); ...
```

For the review surface this is reference material (it explains why certain near-touching corners are not defects), not a row source.

## 2. The explain CLI: programmatic API, output, cost

`rebuild/pipeline/explain.py` exposes a clean library API; no subprocess needed.

```python
from pathlib import Path
from rebuild.pipeline import spec_load
from rebuild.pipeline.explain import explain, parse_sequence

repo = Path(...)
spec = spec_load.load_spec(repo / "glyph_data" / "runes", repo / "rebuild" / "script.yaml", repo / "rebuild" / "schema")
features = frozenset() if config == "default" else frozenset(config.split("+"))
report = explain(spec, parse_sequence(spec, "200C:E652:E679"), features)
```

`parse_sequence` accepts the audit's `codepoints` column verbatim (hex, qs-names, and boundary names, colon-separated). `explain` returns an `ExplainReport` with `positions: tuple[PositionReport, ...]`; each `PositionReport.trace` is a `settle.TransitionTrace` carrying everything the thumbs-down draft needs:

- `trace.ranked` — the full candidate table (`candidate.stance` / `.entry` / `.seam`, `join_count`, `prospect`).
- `trace.eliminations` — each with `stage`, `description`, and `provenance`, where provenance is the file-plus-key-path string, e.g. `glyph_data/runes/qsPea.yaml:policy.refuse[0]` or `glyph_data/runes/qsPea.yaml:stances.half.surface.exits.y6`. **These are the YAML records that decided the outcome.**
- `trace.decided_stage`, `trace.runner_up`, `trace.joint_floor`, `trace.notes` (notes repeat the fired provenance pointers).
- `trace.settled` — the chosen cell, seam, and extension (the new per-seam join facts come from here).

`report.render()` produces the human-readable text block (sequence, settled labels, per-position candidate table, eliminations with `[provenance]`, decided-by line) — suitable for embedding verbatim in a row's expandable detail.

Measured cost (spec loaded once): **spec load 0.078 s; 100 explain calls including `render()` took 0.009 s ≈ 0.09 ms/call; all 15,528 rows project to ≈ 1.3 s single-threaded.** Verdict: **precompute provenance for every row at generation time.** There is no need for a lazy/on-demand server; the review app can be fully static. (Loading the spec emits two known `SpecWarning`s — harmless; the generator should not treat them as errors.)

## 3. Fonts

| Font | Path                                               | Coverage                                                                                                                                                                                                                                                                                                  |
| ---- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| New  | `rebuild/out/m1/M1.otf`                            | 17.6 KB, 88 glyphs, cmap of exactly the 8 alphabet codepoints (space, periodcentered, uni200C, qsPea, qsTea, qsMay, qsIt, qsOy). Full name "AbbotsMortonSpaceportM1 Sans Senior Regular", upem 550. GSUB: calt, ss02–ss05, ss10. GPOS: curs only (no kern — matches the kern-channel-out-of-scope class). |
| Old  | `site/AbbotsMortonSpaceportSansSenior-Regular.otf` | 544 KB, 1,148 glyphs, 431 cmap entries, upem 550. GSUB: calt, ccmp, ss02–ss07, ss10.                                                                                                                                                                                                                      |

Both fonts cover every codepoint that appears in any audit row (verified against the full alphabet histogram from §1.1), and both carry every feature tag the 8 acceptance configs need. Critically, the current `site/` Senior OTF's sha256 (`3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`) is byte-identical to the `font_sha256` recorded in the `rebuild/out/m1/baseline-*.subset.tsv.gz` headers — the live site font _is_ the §13.1 oracle font, so "before" rendering with it is faithful.

Per the user decision, the generator should **copy** both OTFs into the output (e.g. `rebuild/out/review/fonts/before.otf` and `after.otf`) so the directory is self-contained, and record each copy's sha256 plus the source path in the generated page for traceability. Render via dual `@font-face` with distinct family names; apply the row's config with `font-feature-settings` (e.g. `"ss02" 1, "ss03" 1`). One rendering caveat: browsers treat U+200C as a default-ignorable and may render it invisibly regardless of cmap — which is the desired look; do not substitute a visible placeholder inside the rendered string itself (show the ZWNJ in the caption notation instead).

## 4. The general treaty-diff mode (future migrations)

§8 makes the settlement and treaty tables the committed, diff-stable behavioral record, and §11's `make treaty-diff` is defined _against a baseline_. Concretely:

- **Keying.** A settlement row's key is (`config`, `input`, `backtrack`, `lookahead1`, `lookahead2`) — the full context tuple; its value is (`outcome`, `joint`, `provenance`). A treaty row's key is (`config`, `left`, `right`); its value is (`junction`, `extension`, `kern`). Files are sorted and deterministic, so a plain key-aligned diff is stable.
- **Changed-row detection.** For each config, load both versions into key → value maps; classify keys as added, removed, or value-changed. Treat `provenance`-only changes on settlement rows as a separate, lower-priority bucket (the behavior is identical; only attribution moved). Note the settlement key embeds label _sets_, so a spec change that re-partitions contexts shows up as remove+add pairs rather than a value change — the review page should pair removals and additions that share (`config`, `input`) so the human sees one regrouped row, not two unrelated ones.
- **Rendering a changed treaty row** needs a witness string: a shortest sequence whose settlement realizes that adjacent cell pair under that config. At 0.09 ms per `settle()` call, brute-forcing depth ≤ 4 windows over the alphabet at generation time is affordable; the settlement table's backtrack/lookahead sets can seed the search.
- **Future baseline snapshot.** A copy of the per-config `settlement-_.tsv` + `treaties-_.tsv` (and the OTF they shipped with, for "before" rendering) taken at an accepted state — i.e. an "accepted" directory the next run diffs against, exactly like `site/before/` in the existing `check-html` workflow.

So the generator should support two input shapes behind one row model: (a) M1 mode — rows from `divergence-audit.tsv` + ledger, fonts old-Senior/new-M1; (b) table-vs-table mode — rows from diffing two table directories, fonts snapshot-OTF/new-OTF. Both produce the same render unit: input string, config, before/after rendering, before/after facts, provenance, verdict widget.

## 5. Row enrichment and batching

Cheaply derivable per audit row at generation time:

- **Rune-name notation.** Map codepoints through `doc/glyph-names.md`: `E650`→·Pea, `E652`→·Tea, `E665`→·May, `E670`→·It, `E679`→·Oy; boundary tokens `0020`→word space, `00B7`→the namer dot (·), `200C`→ZWNJ. So `200C:E652:E679` displays as "ZWNJ ·Tea·Oy". Trivial static dict.
- **Ledger `why:`.** Join `matched_entry` to the ledger entry; show `status`, `why`, `ink_identical`, and whether this row is one of the 19 exemplars (key match on `config` + `codepoints`).
- **Per-seam join facts, old vs new.** _Not_ in the audit TSV (the writer drops `baseline_seams` / `new_seams` and `position`). Old seams: read the `seams` column from `rebuild/out/m1/baseline-{config}.subset.tsv.gz` (4,687 rows per config; load all 8 into dicts keyed by codepoints — `rebuild/validation/rowmodel.py` has `iter_rows` / `Row.seams`, values like `break,y5`; `lig` marks ligature-internal seams). New seams and extensions: re-settle each row (`trace.settled.seam`, `.extension`) — already free as a byproduct of the §2 provenance precompute (~1.3 s total).
- **Shortest containing exemplar.** Audit strings are at most 4 codepoints (102 two-long, 1,324 three-long, 14,102 four-long), so no row is "long" in the corpus sense; the useful inverse is collapsing repetition: for each row, if a shorter audit row's codepoint string is a contiguous substring with the same local before/after window, link the longer row to it as "same change in context". Cheap (substring index over 2,254 distinct strings), but optional for v1 — the dedupe below does most of the work.

**Batching reality.** The 15,528 rows are highly redundant:

- Dedupe by (`codepoints`, `baseline`, `new`) → **2,411 unique visual units**; the ledger class is a function of that triple (distinct (class, triple) is also 2,411). Config multiplicity per unit: 7 configs × 2,028 units, 4 × 217, 3 × 149, 1 × 17 — so one rendered row with a config-chip list covers up to 7 audit rows, and a verdict can fan out to all of them (with per-config override available since rendering only differs when the triple differs).
- Group deduped units by (ledger class, first letter-bigram in the string) → **166 groups** (measured by scanning the TSV; 219 groups if a unit lands in every bigram it contains rather than the first). Sizes: max 121, median 2; per class the deduped totals are dangling-anchor-dropped 1,334, may-exit-withdrawal-generalized 269, zwnj-word-initial-unification 213, halves-entry-extension-restored 193, bare-name-live-join 139, regrouping-floor-drift 64, marker-staging-ligature-formation 52, entered-it-baseline-join-gain 46, ss03-chain-join-gains 40, same-seam-extension-non-summing 33, pea-chain-regularized 15, zwnj-follower-exit-restored 6, pre-ligature-cleanup-regularized 6, may-quad-order-deferral 1.
- Recommended triage shape: order by ledger class (the ledger's own order, or status — `intended` classes are bulk-confirmable, `drift-accepted` classes deserve the eyeballs), then by family pair within the class, paginated in batches of a few hundred deduped units per §11. That makes the worst class (dangling-anchor-dropped) about 5–6 batches and most classes a single screen, with class-level "accept whole group" affordances justified by the 1:6.4 dedupe ratio.
