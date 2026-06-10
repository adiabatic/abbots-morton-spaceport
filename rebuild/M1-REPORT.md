# M1 report: qsPea, qsTea, qsMay, qsIt (+ qsOy, qsTea_qsOy) migrated and accepted

The milestone-completion artifact promised by `rebuild/M1-PLAN.md` Phase 6. Section references are to `doc/rebuild-design.md` unless marked otherwise. Every number below was re-verified against the artifacts on 2026-06-10; the per-row evidence lives under `rebuild/out/m1/` (gitignored build artifacts) and in the committed-shape source files `rebuild/m1-divergences.yaml`, `rebuild/m1-aliases.yaml`, and `rebuild/m1-contact-allow.yaml`.

## 1. TL;DR

The four worst accretion offenders — qsIt, qsTea, qsPea, qsMay — plus qsOy and the qsTea_qsOy ligature (admitted to keep the conformance alphabet formation-closed) are migrated into the §3 rune-file format, and the §14 module skeleton exists as real, tested code under `rebuild/pipeline/`. The old pipeline's 42 stances across the four families collapse to 8 pen-motion stances; everything else became surface rows, cells, bindings, refusals, extends, contracts, and unlocks (§4 of this report). **Oracle conformance: PASS** — all 37,440 baseline rows over the 8-symbol alphabet × 8 acceptance configurations were compared, 15,528 diverge, and every divergent row matches exactly one of the 15 reviewed ledger entries (0 unmatched, 0 multi-matched, 0 silent). **Font conformance: PASS** — the exhaustive 299,584-run HarfBuzz sweep of every sequence of length 1–5 agrees with `settle()` with zero divergences and zero uncovered rules or transitions; CoreText agrees 440/440. The old build is byte-identical with the new files present, and an adversarial milestone audit ran, found three real gaps, and all three are closed (§8). All twelve M1-PLAN §7 gates are green. **One thing still needs a human: the ductus sign-off in §3.** Nothing has been committed or staged.

## 2. Gate results (M1-PLAN §7)

| #   | Gate                                              | Result                                                                                                                                                                                                                     |
|-----|---------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1   | Old-font byte identity                            | `make all`; `shasum -a 256 site/AbbotsMortonSpaceportSansSenior-Regular.otf` = `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35` with `glyph_data/runes/` present                                         |
| 2   | `make test`                                       | 6753 passed                                                                                                                                                                                                                |
| 3   | `uv run pytest rebuild/ -n auto --dist worksteal` | 282 passed, 1 skipped (the jsonschema cross-check, runnable only under `uv run --with jsonschema`)                                                                                                                         |
| 4   | Schema validation + lints                         | green over the six rune files + `rebuild/script.yaml`; stance-ID regex, ductus parity, `right.then` prohibition, predicate-class derivability all enforced at load                                                         |
| 5   | §9 hard E-gates                                   | 0 errors, 0 flags, 20 blessed off-anchor contact signatures (`rebuild/m1-contact-allow.yaml`); `E-STRANDED` + outcome partition asserted per config                                                                        |
| 6   | Dead policy                                       | 8 genuinely-dead-in-alphabet records, each explained in §4.5; 25 deferred-partner records listed in §4.6; exercised-ness is firing evidence, not string luck (§4.4)                                                        |
| 7   | Outcome partition + budget                        | partitions and first-match-wins replay green per config; GSUB 6,390 bytes, 51 lookups, settle lookup 166 format-3 subtables with 61,369 bytes of offset headroom; zero Extension promotions (`budget.json`)                |
| 8   | Oracle conformance                                | 37,440 rows compared across 8 configs; 15,528 divergent, every one matching exactly one ledger entry; 0 unmatched, 0 multi-matched; ss06/ss07/ss06+ss07 row-identical to default; positions compared on 33,533 rows (§5.2) |
| 9   | Font conformance                                  | exhaustive length-1–5 sweep, 37,448 sequences × 8 configs = 299,584 shaping runs, 0 divergences, 0 uncovered rules/transitions; CoreText smoke 55 sequences × 8 configs = 440 runs, 0 failures                             |
| 10  | Ductus parity + draft flags                       | every stance names a way; the full draft/unrealized enumeration for author sign-off is §3                                                                                                                                  |
| 11  | Formatting                                        | `make prettier`, `uv run --with black black -q rebuild/`, `markdownlint-cli2` clean                                                                                                                                        |
| 12  | Repo hygiene                                      | only additive untracked files under `rebuild/`, `glyph_data/runes/`, `tmp/`; nothing staged; no existing pipeline file modified                                                                                            |

## 3. Ductus drafts for author sign-off (§15.10 — read this section)

Migration is gated per rune on ductus (§13.2), and `way:` is mandatory, so every migrated rune now has written ways. Per the M1-PLAN §8 protocol, anything not byte-for-byte from today's YAML carries `# DRAFT — pending author sign-off` on its key line in the rune file — typo fixes included. The drafts are quoted in full below; **none of this prose is settled until you approve or rewrite it.** Edit the text in `glyph_data/runes/<rune>.yaml` and delete the DRAFT flag to sign off.

### 3.1 qsIt, way `bar` — verbatim, but with a structural relocation to approve

> - Either written from top to bottom or bottom to top.

Carried byte-for-byte from today's first ductus bullet, so no DRAFT flag. **For sign-off anyway:** today's ductus bullets 2–4 were join constraints, not stroke prose; they now live as `pairings: only:` plus the four ss04 unlock rows in `qsIt.yaml`. The drawing description lost nothing, but the ductus entry is shorter than it was.

### 3.2 qsTea, ways `full` and `half` — entirely new (the §13.2 hard gate: qsTea had no ductus at all)

`full` (DRAFT):

> - Written as a single vertical stroke from the top line straight down to the baseline.
> - Or the same stroke, written from the baseline straight up to the top line.

`half` (DRAFT):

> As the full way, but the stroke covers only the span from the top line down to the x-height.

Drafted from the bitmap by analogy with ·It's bidirectional bar. Open questions: (a) is ·Tea's bar genuinely bidirectional like ·It's, or top-down only? (b) the half is drafted as top-down only — and if the full way's bottom-up order is real, does "covers only the span from the top line down to the x-height" read right (a bottom-up half would start at the x-height)?

### 3.3 qsPea, ways `full` and `half` — entirely drafted (the old four-way entry collapses to two)

`full` (DRAFT):

> Begin at the left, slightly above the x-height; occasionally, another letter will join at the entry, and when the previous letter exits at the x-height, the left upward stroke begins at the x-height instead, dipping down to receive the join. Rise to the top and rightwards in a clockwise curve. Then pull straight down on the right toward the baseline. It may connect to the next letter at the baseline.

`half` (DRAFT):

> As in the full way, but the downstroke stops at the x-height, or just above it, where it connects to the next letter. When the next letter joins at the x-height, the right downstroke dips down to the x-height to meet it; when letters join at the x-height on both sides, both ends of the stroke dip down to the x-height.

Drafted from today's "Standard (full)" and "Half" prose. The old third way ("Dipping down to the x-height…") is folded into `full` as the entry-dip clause, and the old fourth way — the sentence that trailed off mid-thought as "As in the half way, but " — is finally finished as `half`'s dip clauses. The "recieve" typo is fixed. The dips deliberately did **not** become ways: the pen motion is the same; they are stubs and an explicit `cells:` composition per §4 (so ductus parity demands no stances for them). Open questions: (a) is the finished both-sides sentence what you meant in 2024? (b) should `full`'s entry-dip clause live inside the first sentence as written, or stand as its own sentence?

### 3.4 qsMay, ways `loop`, `grounded-loop`, `counterclockwise`

`loop` (DRAFT — today's prose with only the mid-sentence "Then" capitalization fixed):

> Written clockwise, starting from the leftmost pixel at the baseline, then it continues right, loops around underneath the baseline, and then exits at the x-height on the right.

`grounded-loop` (DRAFT — new prose for the real, reachable grounded drawing, taken from design §5.1):

> As the loop, but the final stroke stays down and rests on the baseline at the right.

`counterclockwise` — `{unrealized: true}`, carried from core-idea.md line 141: enumerated honestly, undrawn, no stance. No sign-off needed beyond confirming it should stay enumerated.

### 3.5 qsOy, way `loop` — entirely drafted (no ductus existed)

`loop` (DRAFT):

> A small loop sitting at the x-height, written clockwise from its top, closing on itself; the stroke then runs out diagonally down to the right, ending at the baseline, where it may connect to the next letter. When a previous letter joins at the x-height, the loop opens on the left to receive the join.

Drafted from the bitmap and the Manual's small-loops context. Open question: the clockwise direction and the top starting point are bitmap-inferred, not sourced.

### 3.6 qsTea_qsOy, way `bar-into-loop` — entirely drafted

`bar-into-loop` (DRAFT):

> Written as ·Tea flowing into ·Oy: the vertical bar first, from the top down through the x-height. Without lifting the pen, the small ·Oy loop is drawn clockwise around the foot of the bar, closing on it, and the stroke runs out diagonally down to the right, ending at the baseline, where it may connect to the next letter.

Open question: the bar-then-loop stroke order is inferred from the bitmap.

### 3.7 The `stroke:` orientation values — new data, all author judgment

Every entry/exit row in the six rune files carries a `stroke:` orientation (qsPea vertical on every row; qsMay horizontal on every row; bars and tails vertical elsewhere). Per design §15.9 these have no old-YAML source and no ground truth but your eye — please review them in the same pass.

## 4. Migration census

### 4.1 Per-family collapse

Old counts are from `glyph_data/quikscript.yaml` (stances + named shapes); new counts from `glyph_data/runes/*.yaml`. "Bound forms" counts `joined:`/`stub:`/named-`withdrawal:` bindings on surface rows.

| Rune       | Old stances | Old shapes | New stances | Surface rows | Cells | Bound forms | Unlocks | Refuse | Extend | Contract | Prefer |
|------------|-------------|------------|-------------|--------------|-------|-------------|---------|--------|--------|----------|--------|
| qsPea      | 10          | 4          | 2           | 8            | 1     | 3           | 0       | 2      | 2      | 0        | 0      |
| qsTea      | 11          | 1          | 2           | 6            | 0     | 0           | 4       | 6      | 4      | 2        | 0      |
| qsMay      | 9           | 4          | 2           | 5            | 1     | 3           | 0       | 2      | 6      | 1        | 0      |
| qsIt       | 12          | 0          | 1           | 4            | 0     | 0           | 4       | 8      | 6      | 0        | 0      |
| qsOy       | 1           | 1          | 1           | 2            | 1     | 1           | 0       | 0      | 1      | 0        | 0      |
| qsTea_qsOy | 0           | 0          | 1           | 1            | 0     | 0           | 0       | 0      | 0      | 0        | 0      |

The §13.6 prediction ("42 stances between them prove the collapse") lands exactly: the four offenders' 42 stances become 8 stances — every one a pen motion (`full`, `half`, `bar`, `loop`, `grounded-loop`, `bar-into-loop`) — and 0 prefer and 0 resolve records were needed anywhere.

### 4.2 CONTEXT records dissolved as emergent, with explain evidence

Per §13.3, CONTEXT records were attempted as _nothing_ first. The three deletions whose partners are in the M1 alphabet were each verified through the explain CLI (`uv run python -m rebuild.pipeline.explain`) against the baseline:

- **qsPea `half_exit_dips_to_xheight`'s `not_after: [qsMay, qsUtter]`** — a cascade-ordering artifact, deleted. Evidence: `E665:E650:E670` (·May·Pea·It) settles the both-dipped (en-y5, ex-y5) explicit `cells:` composition, matching the baseline with no negative context anywhere.
- **The demote/cleanup machinery around the entryless ligature** (`_PENDING_BK_ENTRY_GUARDS`' qsTea windows, `calt_post_liga_left_cleanup_pred`, `trailing_demote_overrides`) — deleted. Evidence: `E670:zwnj:E652:E679` shows the run split at ZWNJ and qsTea_qsOy forming post-ZWNJ without locking (it is not entry-bearing), with ·It fully bare; predecessor withdrawal before the ligature is cell semantics on the predecessor's side (·May renders `ex-bind-pulled-back` before qsTea_qsOy with zero policy on the ligature).
- **qsMay `entry_xheight_after_i` / `entry_xheight_exit_baseline_after_i`** — qsI is already in the entry row's from-scope; the demote-table keys were dead mechanism. (qsI itself is out of alphabet, but the deletion leaves no in-alphabet residue, confirmed by the oracle's zero unmatched rows.)

Deletions whose distinguishing partners are out of the M1 alphabet, recorded for re-adjudication at those partners' migration: qsIt `entry_nowhere_exit_baseline_before_day`, `before_utter` (its positive trigger; the `not_after` survives as the two-sided -ian refuse), and `entry_baseline_exit_noentry_before_day_exam` (its 17-family carve-out and trailing-demote rows are dead mechanism); both `reverse_upgrade_from` records.

### 4.3 Never-ported mechanisms (deleted on evidence, §13.4)

The `.noentry` shadow universe (the chokepoint mints generated locked twins instead), `strip_entry_before`, `restore_isolated_form_overrides` (8/8 rows target qsIt), `_PENDING_BK_ENTRY_GUARDS` (all four keys are qsTea windows), the demote-table rows, and `extend_exit_when_entered` (re-expressed as the `self: {entry: live}` condition). The baseline oracle proves their behaviors are preserved or surfaces the divergence — the ledger classes `zwnj-word-initial-unification`, `zwnj-follower-exit-restored`, and `pre-ligature-cleanup-regularized` are exactly these mechanisms' residue, each with a written why.

### 4.4 §4 watchdog counts and how exercised-ness is measured

- **Carried drawings: 19/19 byte-identical** to the old YAML (9 stance base bitmaps + 5 bound siblings + 5 mono bitmaps). Two old qsPea dip shapes (`half_right_dips_down_to_xheight`, `dips_down_to_short_height`) ceased to exist as named drawings and are re-derived by stub arithmetic; the explicit `cells:` composition `half-dips-both-sides` is the one sanctioned bitmap-watchdog exemption (it differs from the half base only at the attachment row, by design).
- **Migrated-resolve count: 0** (the §13.5 watchdog number). No arbitration triage was needed; nothing in M1 rests on a `migrated:` record.
- **Exercised-ness is firing evidence.** The settlement engine records the YAML provenance of every record that demonstrably fires under a configuration: refusals that kill a candidate (including inside the refusal-aware lookahead closure — e.g. `qsTea.yaml:policy.refuse[0]` is what keeps ·It·Tea broken and fires only there), unlocks that grant an entry/exit/pairing, row scopes that admit a side, and extends/contracts/prefers that shape a committed cell. The union over the eight acceptance configurations is `DecisionTable.cited_provenance`, consumed by `defects._check_dead_policy`; the same pointers ride into the settlement TSVs and the emitted FEA as per-rule provenance comments (design §6.3 compensation b — 106 of the 258 emitted rules cite at least one YAML record; the rest are structural-floor or capability-only outcomes with nothing to cite). Regression-pinned in `rebuild/test_table.py`, including a closure-only firing and an ss03-only firing.

### 4.5 The eight dead-in-alphabet records, each chased to ground

A record is **deferred-partner** when any family-constrained axis of its `when:` resolves to no modeled rune; it is **genuinely dead in the alphabet** when every axis has modeled members and it still never fires anywhere, closures included. All eight dead records are faithful transcriptions of today's YAML (the M1-PLAN §5 authoring note: author the record, let the gates arbitrate); none is a new-pipeline bug.

1. **`qsPea.yaml:policy.extend[0]` and `extend[1]`** (full/half x-height entry +1 when a half is joined at the x-height): ·Pea's x-height entry rows admit only `{qsMay, joined_at: x-height}` and `{qsUtter, joined_at: x-height}` — no half is in the from-scope, so the trigger state is unreachable through the entry's own allowlist. Mirrors today's YAML, where the class-broad extend coexists with the same narrow entry scope.
2. **`qsMay.yaml:policy.extend[4]` and `extend[5]`** (loop/grounded-loop x-height entry +1 after halves): same shape — ·May's x-height entries admit only the ·Utter-ligature families, qsI, qsAh, and qsUtter; no half is admitted, in or out of the alphabet.
3. **`qsOy.yaml:policy.extend[0]`** (x-height entry +1 after halves): ·Oy's single entry's from-scope contains no halves; additionally ·Tea·Oy always forms the ligature before any ·Tea-half join could be considered.
4. **`qsTea.yaml:policy.extend[2]`** (half x-height entry +1 after halves): the in-alphabet halves are ·Pea.half and ·Tea.half; ·Pea.half's x-height exit toward-scope carries `except: qsTea`, and a ·Tea·Tea x-height join is refused from both sides (`refuse[5]`, `refuse[3]`), so no half ever joins half-·Tea at the x-height in M1. Unlike groups 1–3, this one is only alphabet-dead — out-of-alphabet halves reach it after their migration.
5. **`qsPea.yaml:policy.refuse[1]`** (full baseline exit surrendered when entered at y6, before can-enter-at-x-height minus five excepts): toward qsIt the broader `refuse[0]` fires first and shadows it; toward qsPea and qsOy the lookahead closure removes the baseline-exit candidate before any refusal is consulted. Its distinguishing targets are out of alphabet.
6. **`qsIt.yaml:policy.refuse[4]`** (x-height exit refused when entered, after qsJay/qsYe/qsIt/qsEat): no in-alphabet follower accepts an x-height join from ·It at all, so the lookahead closure removes the candidate in every reachable window — ·It's x-height exit never realizes anywhere in the M1 alphabet (confirmed against the treaty tables).

Items 1–3 deserve an author look at the next policy pass: their triggers are contradicted by their own runes' entry from-scopes in the full script too, so they are either future-proofing for a scope widening or dead weight carried over from today's YAML.

### 4.6 Deferred-partner records (reported, not failed)

From `rebuild/out/m1/pipeline_summary.json` (25 entries): qsIt `policy.extend[1]` (qsKey), `extend[3]` (qsI), `extend[4]` (qsCheer, qsJai, qsOwe), `extend[5]` (qsCheer, qsJai, qsOwe, qsZoo), `refuse[1]` (qsDay), `refuse[2]` (qsYe, qsZoo), `refuse[3]` (qsOwe), `refuse[6]` (qsBay, qsDay, qsGay, qsShe), `refuse[7]` (qsUtter — the right axis is wholly unmodeled even though the left names qsIt), `stances.bar.surface.unlocks[0]` (qsDay); qsMay `policy.contract[0]` (qsFee), `refuse[1]` (qsThaw, qsYe), the loop and grounded-loop x-height entry scopes (the ·Utter-ligature/qsI/qsAh/qsUtter set); qsTea `policy.contract[0]` (qsZoo), `contract[1]` (qsJay), `extend[0]` (qsEight, qsJay, qsKey), `extend[1]`/`extend[3]` (qsI), `refuse[1]`/`refuse[4]` (qsEt), `stances.full.surface.unlocks[0]` (qsEt), `unlocks[1]` (qsI), `unlocks[2]` (qsFee); qsPea `full.entries.baseline.scope` (qsAwe, qsEt).

## 5. The divergence ledger

### 5.1 Class by class

15,528 of 37,440 oracle rows diverge; each matches exactly one of the 15 entries in `rebuild/m1-divergences.yaml` through the single phenomenon-set classifier `conform.classify_divergence` (so the partition holds by construction). Per-row detail is in `rebuild/out/m1/divergence-audit.tsv`. Full whys with exemplars are in the ledger; one-line summaries:

| Entry                               | Status         | Count | Why, in one line                                                                                                                                                                                                                |
|-------------------------------------|----------------|-------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `dangling-anchor-dropped`           | intended       | 9,283 | name grain only: today's compiled names keep anchors that never realize (a dangling exit before a non-acceptor, an entry name at word start); settled cells carry only committed anchors — ink machine-checked identical (§5.2) |
| `zwnj-word-initial-unification`     | intended       | 1,421 | post-ZWNJ is definitionally word-initial (§3.4): generated `.locked` twins replace the authored `.noentry` shadow universe — ink identical                                                                                      |
| `may-exit-withdrawal-generalized`   | drift-accepted | 1,227 | the withdrawal binding is cell semantics on every mid-word declined exit (§3.2/§5.1), generalizing today's before-entryless-ligature-only pull-back; boundaries keep the base drawing                                           |
| `halves-entry-extension-restored`   | drift-accepted | 1,218 | the halves-class entry extensions are authored faithfully from today's YAML but today's pipeline never fires them on these windows (probed cascade accident); the record is the law, gates clean                                |
| `bare-name-live-join`               | intended       | 917   | name grain only: today joins on the bare cmap glyph when its drawing already carries the live anchor; the new table always rewrites to the settled cell glyph — ink machine-checked identical                                   |
| `regrouping-floor-drift`            | drift-accepted | 435   | the deterministic ranking regroups chains today's greedy cascade pairs differently; 428 rows trade one seam for another (§15.4 do-not-care drift), the single ·Oy·Tea·May·May window (7 rows) nets +1 join                      |
| `entered-it-baseline-join-gain`     | drift-accepted | 319   | the old YAML puts the baseline-exit refusal on unentered ·It only, so the faithful self-scoped refusal lets an entered ·It join a following ·It/·Tea; today's break is a cascade artifact, not authored law                     |
| `same-seam-extension-non-summing`   | intended       | 225   | extensions never sum on one seam (§6.2): the follower's entry extension is suppressed when the predecessor's exit already carries the pixel                                                                                     |
| `marker-staging-ligature-formation` | intended       | 187   | formation is unconditional and staged before markers and chokepoint, so ·Tea·Oy forms even right after ZWNJ and under every stylistic set                                                                                       |
| `ss03-chain-join-gains`             | drift-accepted | 120   | window join-count maximization buys chains the one-pair-at-a-time cascade cannot see (·Tea/·May/·It·May·Tea shapes under ss03)                                                                                                  |
| `pea-chain-regularized`             | drift-accepted | 101   | today's cascade drops the ·Pea·Pea y6 join whenever the second ·Pea also joins rightward; settlement keeps both — a strict join gain on baseline-verified capability                                                            |
| `pre-ligature-cleanup-regularized`  | drift-accepted | 38    | today's `calt_post_liga` cleanup over-reaches onto seams the withdrawal never touched; settlement withdraws exactly the one declined exit                                                                                       |
| `zwnj-follower-exit-restored`       | drift-accepted | 30    | the chokepoint twin severs only the entry, so a post-ZWNJ letter's own right-side joins are restored (today's `.noentry` stances carried no exit)                                                                               |
| `may-quad-order-deferral`           | drift-accepted | 7     | the one observed window-local ranking cost: in the all-·May quad, ·May's `order:` breaks the optimistic tie toward declining, one join fewer than today — flagged for a yielding prefer at qsMay's next policy pass             |
| `kern-channel-out-of-scope`         | triaged        | 0     | the explicit position-residue triage channel; its zero is measured, not vacuous (§5.2)                                                                                                                                          |

### 5.2 The position channel (M1-PLAN §6 step 3d)

The oracle gate compares old-vs-new positions, restoring the plan's step 3(d) after the audit (the earlier "positions ride the font side" deviation is retracted in M1-PLAN's deviations section). Every baseline row whose ligation and seam topology match settlement is shaped against `M1.otf` and compared on drawn geometry — per-slot glyph origins and the run's total advance, because the two fonts legitimately decompose a seam differently between the left glyph's advance and the right glyph's x_offset while drawing the identical join. Old advances are kern-normalized through `conform.KernEvaluator` over `glyph_data/senior_quikscript_kerning.yaml` (the in-alphabet kerned pairs are ·Oy·Pea at −3 px and ·Oy·Oy at −1 px); uni200C is default-ignorable, so the old font's GPOS pair kerning skips it and the normalization's kern partner skips ZWNJ slots accordingly (verified: ·Oy ZWNJ ·Pea carries the ·Oy·Pea kern in the baseline).

Results: **33,533 of 37,440 rows position-compared with zero residual divergence** — every clean row plus every row in the three classes marked `ink_identical: true` (`dangling-anchor-dropped`, `bare-name-live-join`, `zwnj-word-initial-unification`), so those classes' "name grain only" claims are machine-checked and the kern channel's zero count is measured. The 3,907 excluded rows are those whose seam topology or ligation legitimately changed or whose matched class legitimately redraws ink; their geometry is covered by the font-side gap-0 and off-anchor-contact gates instead. A position drift on an ink-identical-class row strips the class match and fails as unmatched; a position-only drift matches the kern channel only when the comparison itself marks the drifted slots kern-attributable, and fails otherwise.

### 5.3 Ledger honesty notes

- Every predicate is `classify(row) == id` over one classifier, so the "two-plus matches fails the ledger" failure mode is structurally unfireable; `multi_matched` is asserted zero anyway, and the kern-channel predicate (the one non-classifier predicate) is restricted to position-only rows so it cannot overlap a cell-grain class.
- Two whys were corrected after the audit, with the classifier sharpened to match: `regrouping-floor-drift` now states the 428/7 split honestly, and the 36 unentered-·It ss03 ·It·May·Tea rows were rerouted from `entered-it-baseline-join-gain` (now 319, all entered) into `ss03-chain-join-gains` (now 120). An unentered-·It gain outside ss03 matches nothing and fails conformance.

## 6. Module skeleton status against the §14 table

All code under `rebuild/pipeline/` (plus tests at `rebuild/test_<module>.py`, 282 passing). "Promoted" means the prototype's working code was the starting point per M1-PLAN §5's promotion map; "built" means written new for M1. Line counts are working Python, tests excluded, against §14's full-pipeline estimates — M1 modules implement the M1 subset of each contract, so under-estimate numbers are not "done early" and over-estimate numbers are explained.

| §14 module     | M1 status | Lines | §14 estimate | Notes                                                                                                                                                                                                                                         |
|----------------|-----------|-------|--------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `spec_load`    | built     | 1,011 | 400          | over estimate because it ships a built-in JSON Schema evaluator (the `jsonschema` package is not importable in the gate environment; the schemas stay the source of truth, cross-checked when importable) plus all load-time lints            |
| `surface`      | built     | 330   | 450          | cell enumeration, binding/stub resolution, `check_anchor_conventions` (E-ANCHOR)                                                                                                                                                              |
| `settle`       | promoted  | 1,077 | 700          | the prototype settlement function generalized to the full §6.1 stage list, the refusal-aware lookahead closure, ZWNJ/boundary handling, and fired-provenance recording                                                                        |
| `table`        | promoted  | 543   | 500          | outcome-partition compression from the prototype plus treaty tables, joint flags, `cited_provenance`, per-rule provenance comments                                                                                                            |
| (specificity)  | built     | 226   | (in settle)  | §6.2 extensional specificity as its own module with the dedicated regression class `rebuild/test_specificity.py`                                                                                                                              |
| `geometry`     | built     | 340   | 450          | cell bitmaps, stubs, withdrawal bindings, `bind:`, extensions, anchor math                                                                                                                                                                    |
| `defects`      | built     | 390   | 550          | the §9 hard gates, dead-policy partition, extension band (deliberately coarse at M1, recorded), contact-allow channel                                                                                                                         |
| `emit_gsub`    | promoted  | 366   | 800          | single-settlement-lookup emitter from the prototype plus the per-config marker fold, chokepoint, formation staging, ss10 overlay, namer-dot mini-calt                                                                                         |
| `emit_gpos`    | built     | 75    | 200          | the four per-height curs lookups; kerning is out of M1 scope (§12 sidecar compilation lands at its milestone)                                                                                                                                 |
| `compile_font` | promoted  | 164   | ±200         | prototype mini-font recipe; metric parity with `glyph_data/metadata.yaml` deferred to integration                                                                                                                                             |
| `explain`      | built     | 186   | 300          | the §6.3a CLI; every elimination attributed to file + key path of the deciding record                                                                                                                                                         |
| `conform`      | promoted  | 1,049 | 300          | over estimate because M1's conform carries both the font-vs-settle sweep (the §14 scope) and the entire §13.1 oracle comparison: seam/cell classification, the divergence classifier, the ledger matcher, and the old-vs-new position channel |
| `review`       | not built | —     | +400         | the §11 treaty-diff surface is M1's biggest absence and the recommended next deliverable (§9)                                                                                                                                                 |

Support modules outside the §14 table: `model.py` (330 — the frozen cross-group contract), `baseline_subset.py` (78), `coretext_smoke.py` (288 + the Swift harness), `run_m1.py` (220 — the driver), `fixtures.py` (706 — hand-built ResolvedSpec test fixtures). Total 7,379 lines including support and fixtures.

## 7. Semantics coverage (§6.1/§6.2 — the running list, carried forward)

The M1 subset contains **zero prefer records and zero resolve records**, and no organic E-INCOMPARABLE/E-AMBIGUOUS arose, as predicted in M1-PLAN §5.

Exercised by real records and asserted in `rebuild/test_settle.py` / `rebuild/test_surface.py`: allowlist polarity with left resolved-state scopes (qsPea's x-height entry from-scope), a refusal combining a predicate class with except carve-outs (qsPea `refuse[1]` — authored and lint-clean, though emergently shadowed in-alphabet, §4.5), explicit `cells:` composition (qsPea's both-dipped half; qsMay's pulled-back entry-live/exit-withdrawn cell), per-cell anchor override (qsMay `joined_x: 2`; qsOy `exit_x: 5`), stub-vs-withdrawal polarity in both directions, the flagged oddities `ink_y` (qsPea.half) and `selectable: false` (qsTea top entry), the y6 height keeping all four curs lookups live (·Pea·Pea), the `self:` condition (qsIt's entered-exit extension — fires, produces the entered-·It ex-ext-1 cells), ss-gated extends (qsMay toward qsTea under ss03 — fires under ss03 only, regression-pinned), four-set unlock coverage with narrowing `when:` (ss02/ss03/ss04/ss05), multi-set union composition with composite markers (ss02+ss03, ss02+ss03+ss05 on qsTea), `pairings: only:` (qsIt) and `never:` (qsTea, qsMay, qsPea), entryless-ligature formation with predecessor withdrawal (qsTea_qsOy), and the ss10 isolated overlay.

Unexercised by real records, recorded honestly: prefers in both modes and both grains, `resolve` and the arbitration errors, positive `word:` records, `require`, `split:`, `trim:`, `bind:` at settlement level (qsMay's after-·Fee bound contract is deferred-partner; geometry unit-tests `bind:` synthetically), `is: namer-dot` conditions (the token is in scope, the condition axis is registered, no record uses it), `stroke:` conditions in policy, case-group promotion and the subsumption linter, late formation. §6.2 extensional specificity is implemented in full with `rebuild/test_specificity.py` as its dedicated regression module; its two named design cases (the decline-discriminator window, the qsJay contract-vs-extend overlap) need qsThey/qsJay and run as fixture analogs, not M1 rune files. ss02/ss04/ss05 unlocks are authored but behaviorally inert in-alphabet over the baseline windows (asserted identical-to-default at the oracle, not assumed); ss06/ss07/ss06+ss07 are covered by the asserted row-identity of their sub-tables to default's.

## 8. Audit outcome and fixes

An independent adversarial audit ran after integration. Its confirmations: all recorded baselines reproduce byte-for-byte against a freshly rebuilt Senior OTF; the full oracle and font sweeps rerun live to identical numbers; the big `dangling-anchor-dropped` class is genuinely name-grain; all stance IDs name pen motions and the lints reject adversarial inputs (a `bar_before_tea` stance ID, a `right.then` on refuse, an unknown `when:` key — each rejected at load with file/path/line); the three in-alphabet emergent deletions verify via the explain CLI; the subset tables are set-equal to the full enumeration; byte identity and both test suites reproduce; explain attributions hand-check against the YAML on three further sequences.

It also found three real gaps, all closed before this report:

1. **The dead-policy gate was vacuous** — provenance never flowed into table rules, so demonstrably firing records were listed dead. Fixed: the engine records fired provenance (§4.4), the deferred-partner partition is axis-wise, `except:` carve-outs no longer count as positive partners, and the emitted FEA carries per-rule provenance comments. `dead_in_alphabet` went from 33 (wrong) to 8 (each chased to ground, §4.5), regression-pinned.
2. **This report did not exist** while three gates pointed at it. Fixed: you are reading it.
3. **Old-vs-new positions were compared nowhere** (the plan's §6 step 3(d) had been dropped by deviation; `KernEvaluator` was dead code). Fixed: the position channel of §5.2, which en route surfaced two real findings — the old font kerns across ZWNJ (default-ignorable skipping), and the in-alphabet kerned pairs are ·Oy·Pea −3 / ·Oy·Oy −1.

Plus two ledger whys that misdescribed ~43 absorbed rows, corrected with the classifier sharpened (§5.3). Separately, integration itself had caught two YAML authoring bugs against ground truth (qsTea missing from qsMay's grounded-exit refusal — today May·Tea breaks while May·May joins, and the contact gate independently rejected the join; qsTea_qsOy missing from qsMay's baseline entry-extension triggers — the baseline shows en-ext-1), both fixed in `glyph_data/runes/qsMay.yaml` rather than ledgered. All gates were rerun green after every fix.

## 9. Recommended next step

Grounded in what M1 actually revealed, in order:

1. **Build the §11 review surface (`make treaty-diff`) before migrating more runes.** M1 produced 15,528 ledgered rows that were reviewed through a classifier and a TSV; that was survivable once, but the drift-accepted classes (the 435 regrouping rows, the 1,218 restored extensions, the 1,227 generalized withdrawals) are exactly the don't-care-drift stratum §11 is designed to render in the live font for one-key verdicts, and every subsequent milestone multiplies the row count. `rebuild/out/m1/divergence-audit.tsv` plus the settlement/treaty TSVs are the ready-made input; the design names the migration baseline diff as the surface's first deliverable.
2. **A small qsMay/qsPea policy pass with the author**, bundling the items this milestone flagged: the yielding prefer that would recover the ·May quad join (`may-quad-order-deferral`, 7 rows), the three dead extend groups whose triggers contradict their own entry scopes (§4.5 items 1–3), and the ductus sign-off of §3.
3. **Next runes: qsDay + qsUtter, plus qsNo or qsFee.** qsDay and qsUtter dominate the deferred-partner list (§4.6) and adjudicate the two recorded expressiveness questions (qsIt's ss04 before-·Utter stance nulls inherited derives — the closed vocabulary has no negated-feature condition; the trait-qualified group widening in `utter-pass-through-vetoes`). qsNo lights up prefers (§5.2/§5.3 are the design's worked prefer examples) and qsFee lights up the `bind:` contract at settlement level — together they would retire the two largest unexercised-semantics blocks in §7 with real records instead of synthetic fixtures.
4. **Carry forward unresolved design flags:** the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay's x-height entry (recon's "hardest scope" — needs either a design extension or proof the partners' own extend records cover it when qsAh/qsUtter migrate), and the ss05/ss04 row-grain refusal coverage notes recorded in the authoring caveats (re-check at qsEt/qsDay/qsLow/qsUtter migration).

## 10. Location deviation restated (M1-PLAN §1)

The script registry lives at `rebuild/script.yaml`, not the design's `glyph_data/script.yaml`, until cutover: `tools/build_font.py`'s `load_glyph_data` classifies any YAML document without a structural key as a bare Senior kerning rule, and `generate_kern_fea` raises KeyError on it, killing the old build. The rune files themselves are at their final §2 address (`glyph_data/runes/` — invisible to the old pipeline's non-recursive `path.glob("*.yaml")`, proven by gate 1's byte identity). `spec_load` takes both paths explicitly, so the cutover move is a one-line call-site change. Only ss02–ss05 and ss10 are registered in `script.yaml`; ss06/ss07 live wholly on unmigrated families (their row-identity to default is asserted at the oracle without a registry entry) and join the registry at their owners' migration.
