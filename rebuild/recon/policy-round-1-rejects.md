# Policy round 1 recon A: reject consolidation

Reconnaissance for consolidating the round-1 rejects into minimal policy records. Inputs: the cumulative verdict export `verdicts-10.42.06PM.json` (441 verdicts over 725 human review units), the per-unit shards under `rebuild/out/review/units/`, the per-row audit `rebuild/out/m1/divergence-audit.tsv` (15,528 divergent rows), the ledger `rebuild/m1-divergences.yaml`, and the rune files under `glyph_data/runes/`. Companion document: `rebuild/recon/policy-round-1-reconcile.md` (recon B — skips, contradictions, assertion inventory); cross-references below.

Method: every candidate record was validated empirically, not by hand arithmetic. The harness `tmp/recon_a_lab.py` copies `glyph_data/runes/` into `tmp/lab/<variant>/runes/`, applies the candidate edit, reloads the spec through `rebuild.pipeline.spec_load.load_spec`, re-runs the full section 13.1 oracle gate (`rebuild.pipeline.conform.compare_against_baseline` over all 51,546 subset rows, 11 configurations), and diffs the regenerated divergence audit against the committed one, mapping every changed row back to its review unit and verdict (`tmp/lab/<variant>/diff.json`). The control variant (no edits) reproduces the committed audit byte-identically, so every reported flip is real. Per-position attribution used `rebuild/pipeline/explain.py` and the `explain` text already embedded in each unit shard. Nothing under `glyph_data/` or `rebuild/` was modified by this recon; all scratch work lives in `tmp/`.

The four reject clusters: `halves-entry-extension-restored` (61 reject / 1 approve / 2 either / 129 unverdicted), `regrouping-floor-drift` (43 reject / 20 either / 1 unverdicted), `may-quad-order-deferral` (1 reject), `same-seam-extension-non-summing` (2 reject / 28 approve / 3 either). 107 rejects total.

## 1. Cluster 1: halves-entry-extension-restored (61 rejects)

### 1.1 Provenance chase

The class (ledger entry `halves-entry-extension-restored`, 1,218 rows, 193 units) decomposes into exactly two authored-record phenomena. Per-unit provenance plus the before/after cells identify them precisely:

- **1a — `glyph_data/runes/qsIt.yaml` `policy.extend[0]`** (`{stance: bar, entry: x-height, by: 1, when: {left: {class: halves-that-exit-at-x-height, except: [{family: qsPea}], joined_at: x-height}}}`): the entered ·It after a half-·Tea x-height exit picks up `en-ext-1`, widening the ·Tea·It connector by one pixel. This is the bulk: 156 of the 193 units have this as their only in-class delta, and another 21 have it alongside an approved-class delta. The record was transcribed faithfully from the old YAML (`glyph_data/quikscript.yaml` qsIt `entry_xheight_exit_baseline` → `extend_entry_after: {by: 1, targets: [{context_set: halves_exit_xheight_no_pea}]}`, where `halves_exit_xheight_no_pea` = half-·Tea + half-·He), authored per the M1-PLAN section 5 note "the record is the law, the gates arbitrate" — the comment above the record says so explicitly.
- **1b — `glyph_data/runes/qsMay.yaml` `policy.extend[3]`** (`{stance: loop, entry: baseline, by: 1, when: {left: {family: [qsPea, qsTea, qsTea_qsOy, qsYe, qsHe, qsIt], joined_at: baseline}}}`): under the ss03 configurations, when ·May both takes a baseline entry from ·Pea/·Tea and extends its x-height exit toward a following half-·Tea (`policy.extend[1]`), the new pipeline composes `en-ext-1 + ex-ext-1` on one cell; today's pipeline drops the entry extension there (the ss03 exit-extended ·May variant never carried the entry upgrade — a cascade accident, since plain ·Pea·May in the default configuration _does_ show `en-ext-1` in the baseline, see the `dangling-anchor-dropped` ledger exemplar). 17 units, all ss03-only.

Ground truth probes of the shipped font (`site/AbbotsMortonSpaceportSansSenior-Regular.otf`, uharfbuzz via `conform.Shaper`): ·Tea·It shapes to `qsTea.half.ex-y5 | qsIt.en-y5.ex-y0` and ·He·It to `qsHe.half.ex-y5 | qsIt.en-y5.ex-y0` — **no entry extension after either trigger of the old record, in-alphabet or out**. The old record is a dead letter in the shipped font; M1 was the first time it ever drew ink.

### 1.2 The verdict signal

- The 15 rejects u-0444…u-0458 carry "this widens the ·Tea·It extension for no reason"; 46 more carry "the old way seems nicer to write out by hand". All 46 + 4 + 1 rejects whose delta is phenomenon 1a reject the same pixel.
- The two `entered-it-baseline-join-gain` skips u-0397/u-0399 say it outright: "I like the extra join but the extension between ·Tea and ·It shouldn't change because ·It joins to something else next" — keep the join gains, drop the widening. (Recon B sections 1.3 and 1.7 reach the same reading.)
- The 10 rejects whose delta is phenomenon 1b (u-0461…u-0467, u-0469, u-0471, u-0474, all ss03 ·Pea/·Tea·May·Tea shapes) carry "the old way seems nicer to write out by hand". But the same phenomenon was **approved** in u-0468 (·Pea·May·Tea·Tea) and shrugged at in u-0459 (either, "looks the same to me although the yellow part looks different") — and the identical double-extended ·May cell (`en-ext-1+ex-ext-1`) is the approved outcome of the 40 `ss03-chain-join-gains` units. The 1b signal is contradictory; the 1a signal is unanimous.

### 1.3 Proposed edit A1: delete `qsIt.policy.extend[0]`

Delete the record (and replace its transcription comment with one recording the verdict). Delete rather than narrow (`except: [{family: qsPea}, {family: qsTea}]`) because (i) within the M1 alphabet the two are byte-identical — the class resolves the trigger to half-·Tea only; (ii) the only out-of-alphabet trigger the old record names is half-·He, and the shipped font never draws the extension there either (probe above), so deletion is what ground truth says and narrowing would preserve a dead letter that M2's re-probe would delete anyway; (iii) the reviewer's note rejects the realized ink as such, not one trigger of it.

Proposed replacement comment in `glyph_data/runes/qsIt.yaml` (the record's `why:` obligation transfers to the comment since a deletion carries no record):

```yaml
  extend:
  # The old YAML's halves-minus-qsPea x-height entry-extension record was transcribed here for M1 and deleted in the round-1 verdict pass: the shipped font never realizes it (probed: both ·Tea·It and ·He·It join un-extended today), and the reviewer rejected every window where M1 realized it — "this widens the ·Tea·It extension for no reason".
  - {stance: bar, entry: baseline, by: 1, when: {left: {family: qsKey, joined_at: baseline}}}
```

### 1.4 Flip enumeration (validated, variant `v-it`; identical inside `v-final2`)

All 193 class units, by verdict and post-edit status:

| Verdict           | Status after A1                                                              | Units                                                                                                                                             |
| ----------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| reject (50)       | flips to old ink (rows become `dangling-anchor-dropped` or fully conformant) | u-0444…u-0458, u-0475…u-0489, u-0491…u-0510                                                                                                       |
| reject (1)        | rejected pixel gone; residual is the approved ·May pulled-back cell only     | u-0490                                                                                                                                            |
| reject (10)       | unaffected — phenomenon 1b, no edit proposed (section 1.6)                   | u-0461…u-0467, u-0469, u-0471, u-0474                                                                                                             |
| approve (1)       | unaffected (1b window; would flip only under a future 1b fix — flagged)      | u-0468                                                                                                                                            |
| either (1)        | flips to old ink                                                             | u-0443                                                                                                                                            |
| either (1)        | unaffected (1b window; user: "looks the same to me")                         | u-0459                                                                                                                                            |
| unverdicted (105) | flips to old ink                                                             | u-0472, u-0473, u-0511…u-0574, u-0576, u-0578…u-0587, u-0589, u-0595…u-0597, u-0608, u-0609, u-0611…u-0625, u-0627, u-0628, u-0630…u-0633, u-0635 |
| unverdicted (19)  | rejected pixel gone; residual is the approved ·May withdrawal cell only      | u-0470, u-0575, u-0577, u-0591…u-0594, u-0598…u-0607, u-0626, u-0634                                                                              |
| unverdicted (5)   | unaffected (1b windows)                                                      | u-0460, u-0588, u-0590, u-0610, u-0629                                                                                                            |

Row arithmetic: the class drops from 1,218 rows to 160 (the 1b windows plus the regrouping residuals of section 2.5); 3 rows (u-0576, ss03 configurations of ·Tea·It·May·Tea) become fully conformant; 1,055 rows move to ink-identical `dangling-anchor-dropped`; the rest move to `may-exit-withdrawal-generalized` with only the final-taste-liked pulled-back delta remaining.

### 1.5 Side effects of A1 — all in the direction the user asked for

Outside the class, the same record fired inside windows of four approved/skipped classes; deleting it removes the same rejected pixel there while keeping every gained join (validated row by row):

- `entered-it-baseline-join-gain`: u-0383, u-0424 (approve), u-0397, u-0399 (the two skips whose notes demand exactly this), u-0400…u-0408, u-0410 (unverdicted). The ·It·It baseline join stays; the ·Tea·It seam returns to baseline width. This satisfies recon B's watch-list requirement that join-gain windows keep their joins.
- `pre-ligature-cleanup-regularized`: u-0272 (approve) — kept y5 join stays, widening gone.
- `ss03-chain-join-gains`: u-0356 (approve, 3 rows) — the post-ZWNJ ·Tea·It join stays; its connector returns to the unextended width. Note: this is a _newly gained_ seam (the old font breaks there), so the user has technically only seen it with the extension; flagged for the re-review pass rather than treated as a conflict, since the same connector at the same heights was explicitly rejected at preexisting seams.
- `zwnj-follower-exit-restored`: u-0267 (approve) — same as u-0356.

The edit round must re-run the M1 defect gates (`run_m1`) since the ledger's gap-arithmetic note ("E-UNREALIZED gap arithmetic is clean with the one-pixel-longer connector") was written with the extension present; the unextended join is today's shipped geometry at these heights, so no gap is expected.

### 1.6 Phenomenon 1b: no clean revert exists — surface, do not edit

The 10 rejected 1b windows cannot be reverted by a minimal record under the closed vocabulary, and the verdict signal on them is internally contradictory. Findings, all validated:

- `qsMay.policy.extend[3]` itself is correct and load-bearing: the baseline shows `en-ext-1` on ·Pea/·Tea/·It·May baseline joins in the default configuration (it is part of dozens of approved and machine-approved rows). Deleting or shrinking its target list would create _new_ unexplained divergences.
- The `when:` vocabulary has no feature negation, so the record cannot be scoped to "everywhere except ss03-before-·Tea".
- The contract counter-record (`{stance: loop, entry: baseline, by: 1, when: {self: {exit: live}, right: {family: qsTea}, feature: ss03}}`, variant `v-may-contract`) was built and rejected on evidence: entry-side extend and contract do not net at name grain (the cell label keeps both `en-ext-1` and `en-con-1`), so the rows do not flip — they stay in the class — **and** the oracle gate fails with 6 UNMATCHED rows, **and** the record fires inside 31 of the 40 approved `ss03-chain-join-gains` units plus u-0432 (`pea-chain-regularized`, approve), changing approved ink.
- The contradiction to put to the user: the rejected cell (`qsMay/loop` with `en-ext-1+ex-ext-1`) is the same cell they approved in u-0468 and in the 40 `ss03-chain-join-gains` units, and u-0459 — the bare trigram of the rejected set — was marked either with "looks the same to me". Any mechanism that fixes the 10 rejects flips u-0468 (recon B section 1.5 already proposes treating u-0468 as expected-to-change) and would have to be argued against the 40 approvals.

**PROPOSED (not recorded): one question for the user.** "Under ss03, when ·May joins from ·Pea/·Tea at the baseline and also extends toward a following half-·Tea, M1 lengthens the baseline connector by one pixel (the old font did not). You rejected this on ·Pea·May·Tea-shaped words but approved the identical ·May cell on ·Tea·May·Tea-shaped words. Keep M1's composed extension everywhere, or is this worth engine work (a feature-scoped suppression) to drop it?" Until answered, the 10 rejects (plus u-0463's ZWNJ sibling, included in the 10) stay divergent under the existing ledger entry, and the recon B assertion "rejected windows shape identically to baseline" must carve them out (section 7.3).

## 2. Cluster 2: regrouping-floor-drift (43 rejects, 20 eithers)

### 2.1 The window shapes and where the decision actually lives

Three shapes cover all 43 rejects:

- **·Oy steals the follower's join (37 rejects).** `[X]·Oy·Tea·[Y]` and `[X]·Oy·It·[Y]`: old breaks after ·Oy and joins ·Tea·Y / ·It·Y; M1 joins ·Oy·Tea / ·Oy·It at the baseline and breaks the follower pair. The earlier sketch worried that the ·May·Oy·Tea·May-shaped windows (u-0298…u-0300) were "decided by JOIN-COUNT" and unreachable by a yielding prefer — that turned out to be a misread of the unit summaries: the join-count decision they cite is the ·May position's unchanged x-height join into ·Oy. The regrouping decision is at the **·Oy exit** in every one of the 37, where the two groupings tie on window join-count and the structural floor breaks the tie toward realizing ·Oy's own seam ("decided by: floor" / "joint: the structural floor broke a realization tie" in the traces). A default `yields-to-joins` prefer sits exactly at the tier between join-count and the floor, so it flips every tie while leaving strict-gain joins (word-final ·Oy·Tea pairs, etc.) untouched. Settle confirms: no absolute prefer and no refuse is needed. A refuse would be actively wrong — it would kill the ·Oy·Tea join where it is the only join available (e.g. the bare ·Oy·Tea pair, which the old font joins and which stayed machine-approved under the prefer).
- **The ligature twin (3 rejects: u-0283, u-0284, u-0285).** When ·Tea·Oy has formed `qsTea_qsOy`, the settled rune is the ligature, and a component's policy does not transfer: variant `v-oy` (qsOy record only) flipped 34 of the 37 and left these 3 unchanged. The same record on `glyph_data/runes/qsTea_qsOy.yaml` (`v-oy2`) flips them.
- **·It·It steals ·It·May (3 rejects: u-0278, u-0282, u-0297).** `[X]·It·It·May`: old breaks ·It·It and joins ·It·May; M1 joins the entered ·It·It at the baseline (legal per the approved `entered-it-baseline-join-gain` law) and strands the ·May. The tie is at the first ·It's exit. A bare refuse `{exit: baseline, right: qsIt}` would overreach onto the 33 approved `entered-it-baseline-join-gain` windows, and refuse records may not use `right.then` (section 3.3 window decidability). A yielding prefer **may** use `right.then`, and it is surgical: variant `v-itit` changes exactly these 3 units and nothing else in the entire audit.

### 2.2 Proposed records

`glyph_data/runes/qsOy.yaml` (`policy.prefer`, currently `[]`):

```yaml
  prefer:
  - {cell: {exit: none}, over: {exit: baseline}, when: {right: {family: [qsTea, qsIt]}}, why: 'Round-1 verdict, 37 windows: when the groupings tie, ·Oy leaves its baseline exit unrealized so the follower pair joins instead — "I think I would rather prefer ·Tea·It to join" (u-0280); "The old way seems nicer to write out by hand" (the rest). Yielding, so a strict-gain ·Oy·Tea/·Oy·It join is kept.'}
```

`glyph_data/runes/qsTea_qsOy.yaml` (`policy.prefer`, currently `[]`): the same record verbatim (with the `why:` noting it is the ligature twin of qsOy's record — formation must not change the grouping; proven by u-0283/u-0284/u-0285).

`glyph_data/runes/qsIt.yaml` (`policy.prefer`, currently `[]`):

```yaml
  prefer:
  - {cell: {exit: none}, over: {exit: baseline}, when: {right: {family: qsIt, then: {family: qsMay}}}, why: 'Round-1 verdict, ·X·It·It·May (u-0278, u-0282, u-0297): before a ·May that needs the baseline entry, the entered ·It declines the ·It·It join so ·It·May keeps it — "the former looks less awkward to right" (sic), "The old way seems nicer to write out by hand". Yielding and then-scoped, so the approved ·It·It join gains everywhere else are untouched.'}
```

### 2.3 Flip enumeration (validated, `v-oy2` + `v-itit` inside `v-final2`)

All 64 class units:

| Verdict         | Status after the records                                                                     | Units                                                                                                      |
| --------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| reject (29)     | flips to old ink (rows become `dangling-anchor-dropped` or `bare-name-live-join`)            | u-0278, u-0280, u-0282…u-0284, u-0297…u-0299, u-0301, u-0302, u-0304…u-0315, u-0320…u-0324, u-0326, u-0339 |
| reject (13)     | flips at seam grain; residual one-pixel ·It·May / ·Oy-follower entry extension (section 2.5) | u-0281, u-0285, u-0300, u-0303, u-0327…u-0333, u-0336, u-0340                                              |
| reject (1)      | flips at seam grain; residual is the approved ·May withdrawal cell (ss03)                    | u-0316                                                                                                     |
| either (5)      | flips to old ink                                                                             | u-0279, u-0318, u-0319, u-0325, u-0338                                                                     |
| either (15)     | flips at seam grain with the residuals above (12 via the cluster-3 records)                  | u-0286…u-0296, u-0317, u-0334, u-0335, u-0337                                                              |
| unverdicted (1) | unaffected (·Pea·May·Pea·Pea — its drift involves no ·Oy/·It·It/·May·May seam)               | u-0277                                                                                                     |

All 43 rejects flip at seam grain. Ledger class count: 435 rows → 34 (u-0277's 7, u-0857's 7 from section 3, and the 20 new rows of section 2.4).

### 2.4 Side effects: four machine-approved units start regrouping (to more joins)

The qsOy prefer flips four unverdicted, previously ink-identical machine-approved units (20 rows) into visible regrouping drift: u-2068 ·Oy·Tea·It·It (7 rows), u-2224 ·Oy·It·May·Tea (3, ss03), u-2225 ·Oy·It·May·May (7), u-2386 ·Oy·Tea·May·Tea (3, ss03). In these the old cascade happened to _keep_ the word-initial ·Oy join (inconsistently with its own behavior one sibling over — old joins ·Oy·Tea in ·Oy·Tea·It·It but breaks it in ·Oy·Tea·It), so flipping ·Oy to decline trades that join for the follower joins the user said they prefer (u-2068 becomes ·Tea·It + ·It·It, a two-for-one). No record can split u-2068 from the rejected u-0326 (·Oy·Tea·It·Oy): they differ only at the fourth token and `right.then` reaches only one token past the follower. The rows still classify cleanly (`regrouping-floor-drift`, conformance green); the regenerated review surface will present them for verdicts. They are the only machine-approved rows anywhere in the audit that change.

### 2.5 The 13 seam-grain-only flips

In `[X]·Oy·It·May` and `·Oy·It·May·[Y]` shapes the restored ·It·May baseline join now carries ·May's `en-ext-1` (`qsMay.policy.extend[3]`, left ·It joined at the baseline), while the old cascade only extended after an _entered_ ·It (·Tea·It·May old: extension; ·Oy-broken ·It·May old: none). "The predecessor's predecessor is entered" is not expressible in the closed `when:` vocabulary, so these 13 windows return to the old grouping with a one-pixel-longer ·It·May connector. The residual is the same single-extension connector the user approved 28 times in `same-seam-extension-non-summing` — and the one same-seam reject note ("new way has a worse ·May·It join", section 5) complains the connector got _shorter_, so the residual errs in the direction the user has favored. Their rows reclassify into `halves-entry-extension-restored` (the `+en-ext-1` phenomenon bucket); the edit round should expect those 91 rows there (160-row post-edit class total) and the re-review pass should present them.

## 3. Cluster 3: may-quad-order-deferral (1 reject)

u-0341 ·May·May·May·May, "the old way seems nicer to write out by hand". Old: two pairs (`y0 | break | y0`); M1: one final pair (`break | break | y0`) because ·May's authored `order: [loop, grounded-loop]` breaks the deliberate optimistic tie toward declining at every position until the optimism runs out. The ledger pre-flagged the fix ("a yielding prefer on the grounded join would recover the pairing without touching the ss03 order-decided cases") — confirmed by settle, but the naive record needs scoping, and the scoping is the one genuinely subtle finding of this recon:

- `{cell: {exit: baseline}, when: {right: {family: qsMay}}}` (variant `v-maymay`) recovers the quad but **poisons 65 `·X·May·May` windows** (28 approved): through the section 5.9 follower-vote mechanism the record also votes on the ·Tea/·Pea/·It → ·May seam, favoring the predecessor _declining_ so ·May stays free to ground — the audit shows approved `·Tea·May·May` windows losing their ·Tea·May join.
- A follower-side record `{cell: {entry: baseline}, when: {left: {family: qsMay}}}` (variant `v-maymay3`) is a **no-op**: the follower-vote path requires the record's owner to be a different rune from the one being ranked, and in an all-·May run owner and rune coincide, so the record always takes the own-rune path and `left: {family: qsMay}` never matches at the deciding positions.
- The working form (variant `v-maymay4`) scopes the own-rune vote to the two window positions where the old greedy pairing grounds, and is invisible to the follower path because its `left:` conditions can never match a non-·May predecessor candidate:

`glyph_data/runes/qsMay.yaml` (`policy.prefer`, currently `[]`):

```yaml
  prefer:
  - {cell: {exit: baseline}, when: {left: {is: boundary}, right: {family: qsMay}}, why: 'Round-1 verdict on ·May·May·May·May — "the old way seems nicer to write out by hand": at word start, pair up — the grounded baseline join into the next ·May beats declining when the join counts tie.'}
  - {cell: {exit: baseline}, when: {left: {family: qsMay, joined_at: none}, right: {family: qsMay}}, why: 'Chain interior of the same verdict ("the old way seems nicer to write out by hand"): after an unjoined ·May, pair with the next ·May. The acceptance oracle''s window universe tops out at four letters, where the word-start record alone already reproduces every outcome (including the u-0341 quad flip), so this record is invisible to the divergence audit — do not delete it as redundant. Its real load is ·May chains of five or more, which without it regress to the rejected defer-to-the-tail grouping instead of the old greedy y0 | break | y0 | break pairing the shipped font draws at every length; the length-5 and length-6 settle assertions in rebuild/test_settle.py pin it. Like the word-start record, its left: condition can never match a non-·May predecessor seam.'}
```

Correction (post-apply adversarial audit): this recon originally justified the chain-interior record as needed "in triples and quads" — disproven. Within the oracle's length-≤4 window universe the word-start record alone reproduces every outcome (removing the chain-interior record and rerunning the full section 13.1 oracle leaves `divergence-audit.tsv` byte-identical, u-0341 included). The record's real load is ·May chains of length ≥ 5: without it, quints and longer regress to the rejected defer-to-the-tail grouping, while with it they match the shipped font's greedy `y0 | break | y0 | break` pairing (settle-probed at lengths 2–7; old-font shaping HarfBuzz-probed at lengths 4–6). The record's `why:` now states this, and length-4/5/6 settlement rows in `rebuild/test_settle.py` pin the greedy pairing so a remove-if-audit-invisible cleanup cannot silently delete it.

Validated effect (`v-maymay4`): u-0341 settles `y0 | break | y0` — the old pairing, with the middle ·May wearing the pulled-back withdrawal cell the user likes generally (rows reclassify to `may-exit-withdrawal-generalized`; the `may-quad-order-deferral` class goes to **zero** and its ledger entry should be updated/retired in the edit round, including its now-stale "flagged for review at qsMay's next policy pass" sentence). The 11 ·May·May·May eithers (u-0286…u-0296) flip back to the old first-pair grouping, which their either verdicts permit. One side effect: u-0857 ◊ZWNJ ·May·May·May (unverdicted) joins its first pair post-ZWNJ, exactly like its space-boundary sibling u-0287 and consistent with the six approved `zwnj-follower-exit-restored` units (the old break there was the muted `.noentry` shadow, which the user already voted against). No other row in the audit moves; conformance stays green.

## 4. Cluster 4: same-seam-extension-non-summing (2 rejects) — diagnose only, no edit

What actually changed in u-0636 (·Pea·May·It·May, "new way has a worse ·May·It join") and u-0656 (◊ZWNJ ·May·It·May, "the old way seems nicer to write out by hand"): the ·May·It x-height seam is pixel-identical old vs. new; the change is the **·It→·May baseline connector**, which the old pipeline drew with two extension pixels (·It `ex-ext-1` plus ·May `en-ext-1`, summed on one seam) and the new pipeline draws with one (`design section 6.2: extensions never sum on one seam` — the follower's entry extension is suppressed; class status `intended`, prototype divergence 3). The final ·May sits one pixel further left; nothing else differs.

No record is proposed because the verdicts contradict themselves at window grain, not just class grain:

| Window            | Unit   | Verdict | Same phenomenon, same seam                     |
| ----------------- | ------ | ------- | ---------------------------------------------- |
| ·Pea·It·May       | u-0637 | approve | the ledger exemplar of the class               |
| ␣ ·May·It·May     | u-0654 | either  | differs from u-0656 only in the boundary token |
| ◊ZWNJ ·May·It·May | u-0656 | reject  |                                                |
| ·May·It·May·It    | u-0663 | approve | interior ·It·May seam, identical suppression   |
| ·Pea·May·It·May   | u-0636 | reject  |                                                |

Mechanically honoring the 2 rejects would mean either repealing the section 6.2 non-summing law (engine semantics, not a record) or authoring a wider ·It exit extension toward word-final ·May — which would also hit u-0637/u-0663 and contradict the 28 approvals. **PROPOSED (not recorded):** re-present u-0636 and u-0656 next to u-0637/u-0654/u-0663 in the regenerated review surface and ask for one ruling on the pair; treat the 2 rejects as presumed noise until then. (Recon B's follow-up ledger item 3 already lists u-0636 as the "exit-side cousin" — this section is the per-unit diagnosis it defers to.)

## 5. The consolidated edit set and validated totals

Five one-record edits plus one deletion, all validated together as variant `v-final2` (`tmp/lab/v-final2/`):

| File                               | Edit                                                                                      | Covers                                          |
| ---------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `glyph_data/runes/qsIt.yaml`       | delete `policy.extend[0]` (comment records the verdict)                                   | 51 halves rejects + both ·Tea·It-widening skips |
| `glyph_data/runes/qsIt.yaml`       | add `policy.prefer[0]` (decline ·It·It before ·May, `right.then`-scoped)                  | 3 regrouping rejects                            |
| `glyph_data/runes/qsOy.yaml`       | add `policy.prefer[0]` (decline before ·Tea/·It on ties)                                  | 34 regrouping rejects                           |
| `glyph_data/runes/qsTea_qsOy.yaml` | add `policy.prefer[0]` (ligature twin of qsOy's record)                                   | 3 regrouping rejects                            |
| `glyph_data/runes/qsMay.yaml`      | add `policy.prefer[0..1]` (grounded ·May·May pairing at word start / after unjoined ·May) | 1 may-quad reject + 11 regrouping eithers       |

Validated totals for `v-final2` against the committed audit: 15,528 → 15,525 divergent rows, **0 unmatched, 0 multi-matched** (the ledger predicates still partition every row); 3 rows fully conformant; 1,738 rows changed class or cells. Of the **107 rejects: 79 return to byte-identical old ink, 16 return to the old seams with only final-taste-approved residual cell deltas (pulled-back ·May or a single-pixel entry extension), 12 are surfaced as contradictions with no edit (10 × phenomenon 1b + 2 × same-seam)**. Ledger count movements the edit round must fold into `rebuild/m1-divergences.yaml`:

| Ledger entry                    | Rows before | Rows after |
| ------------------------------- | ----------- | ---------- |
| halves-entry-extension-restored | 1,218       | 160        |
| regrouping-floor-drift          | 435         | 34         |
| may-quad-order-deferral         | 7           | 0 (retire) |
| dangling-anchor-dropped         | 9,283       | 10,586     |
| may-exit-withdrawal-generalized | 1,227       | 1,390      |
| bare-name-live-join             | 917         | 914        |
| (all other entries)             | unchanged   | unchanged  |

## 6. Engine and authoring lessons worth keeping

- **Ligatures do not inherit component policy.** A prefer on qsOy is invisible once `qsTea_qsOy` forms; the ligature rune needs its own record (u-0283/u-0284/u-0285 were the tell).
- **The section 5.9 follower vote makes a broad own-rune prefer bilateral.** `{cell: {exit: baseline}, when: {right: {family: qsMay}}}` on qsMay votes on the ·X·May seam too, against the ·X join. Scope the `left:` axis so the record cannot match a non-self predecessor candidate.
- **The follower-vote path is unavailable between same-rune neighbors** (owner and ranked rune coincide), so follower-side records cannot fix all-·May runs; the own-rune `left:` conditions must carry the scoping instead.
- **Entry-side extend + contract do not net at name grain** (`en-ext-1` and `en-con-1` both land on the cell label), so a contract is not a revert mechanism for an extension — only deleting/narrowing the extend record is.
- **Yielding prefers were sufficient for every flip in this round.** No `mode: absolute`, no new refuse; the section 3.3 linter preference for refuse over absolute prefer never had to be tested.

## 7. Hand-off notes for the edit round

1. Apply the section 5 edits with the quoted `why:` strings; keep records inline per the repo YAML style.
2. Re-run `rebuild/pipeline/run_m1.py` end to end (tables, defects gates including the gap arithmetic of section 1.5, FEA, mini font, oracle gate), refresh the ledger counts per section 5, retire/update the `may-quad-order-deferral` entry, and update the stale qsIt transcription comment.
3. Regenerate the review surface; the changed-unit set to re-present is exactly: the 13 + 91-row residual-extension windows (section 2.5), the 4 newly-regrouping machine units (section 2.4), u-0857, u-0356, u-0267, u-0272, the entered-·It units of section 1.5 (including skips u-0397/u-0399, expected to become approvable per their own notes), and the 12 unresolved rejects of sections 1.6 and 4 with their questions.
4. The recon B assertion buckets need two carve-outs: "rejected ⇒ baseline-identical" holds for 79 of 107, holds at seam grain with documented residuals for 16, and is deferred pending the user's answer for 12; "approved ⇒ unchanged from M1" holds everywhere except the section 1.5 units (whose only change is deleting the pixel the user rejected by name) — u-0468 ends up **unchanged** under this edit set, contrary to recon B's expectation that the halves revert would touch it (it flips only if the user later rules against phenomenon 1b).
5. The OLD pipeline is untouched by everything above; `make test` and the Senior Sans OTF hash are unaffected until the rune edits land, and the rune edits do not feed the old pipeline at all.

Scratch artifacts for reuse: `tmp/recon_a_lab.py` (variant harness; `v-final2` is the full proposed set), `tmp/lab/<variant>/diff.json` (row-grain diffs with unit/verdict tags), `tmp/lab/<variant>/summary.json` (oracle counts), `tmp/recon_a_agg.py` (per-unit aggregation), `tmp/recon_a_dump.py` / `tmp/recon_a_seams.py` (unit inspection).
