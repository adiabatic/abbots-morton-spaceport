# Policy round 1 recon B: reconciliation, conflicts, and the rest of the verdict file

Reconnaissance for applying the round-1 verdicts. Inputs: the cumulative verdict export `verdicts-10.42.06PM.json` (441 verdicts over 725 human review units; exported 2026-06-11T05:42:06Z against manifest 2026-06-11T05:02:37Z), the per-unit shards under `rebuild/out/review/units/`, the per-row audit `rebuild/out/m1/divergence-audit.tsv`, and the ledger `rebuild/m1-divergences.yaml`. The machine-readable assertion inventory for Phase 3 is generated at `rebuild/out/policy-round-1-assertions.json` (§5). Everything marked **PROPOSED** below awaits the user's confirmation; nothing has been recorded into the verdict file, per the no-fabrication rule.

Binding interpretation context: the user changed their mind mid-stream, and their final taste is that the ·May pulled-back bitmap is liked **generally**; the earlier "·May should use the loop stance" skip-notes are superseded by that final taste.

## 1. Contradictions and supersessions

### 1.1 The four "·May should use the loop stance" skips — superseded

Units u-0243, u-0244, u-0245, u-0250 (class `marker-staging-ligature-formation`, batch 0, recorded 02:40–02:41Z), all variants of ·May·Tea·Oy under the ss03 configurations (`E665:E652:E679` with space/namer-dot framing). Note on each: "an improvement but ·May should use the loop stance".

Where they stand now: in all four shards the M1 outcome **already settles ·May on the loop stance** — the after cell is `qsMay/loop/None/None/ex-bind-pulled-back`, i.e. the loop stance with the pulled-back exit bind (the old pipeline rendered `qsMay.ex-ext-1` joined at y5 into `qsTea.half`). What the note read as "not the loop stance" is the pulled-back exit bind on the loop, and the user's final taste likes the pulled-back bitmap generally; the same look subsequently collected 85 approvals and 52 eithers across `may-exit-withdrawal-generalized` (05:13–05:42Z). The notes are superseded on both grounds.

**PROPOSED:** record all four as **approve**. The user already called the change "an improvement", the rendered ·May is the loop stance they asked for, and the rest of the class took 32 approvals.

### 1.2 The two "narrower red underline" skips — question answered

Units u-0223 and u-0224 (class `marker-staging-ligature-formation`, batch 0, recorded 02:36Z) are the same window `00B7:200C:E652:E679` (· ◊ZWNJ ·Tea·Oy) split by configuration: u-0223 covers default/ss02/ss03/ss04/ss05/ss02+ss03/ss02+ss03+ss05, u-0224 covers ss10. Note on both: "why is the red underlined part narrower in the \"after\"?".

The question was answered during triage: the narrowing is genuine and intended. The `qsTea_qsOy` ligature (`bar-into-loop` stance) draws the ·Tea bar directly into the ·Oy loop, and its drawing is two pixels narrower than the separate `qsTea` + `qsOy` glyphs — the shard highlight extents confirm it (highlighted span 550 → 450 font units, pair advance 700 → 600, at 50 units per pixel). The old pipeline never formed the ligature after ZWNJ (it renamed the lead first); the new pipeline forms it unconditionally, which is exactly the class behavior (`marker-staging-ligature-formation`) the user approved 32 times elsewhere, including ligature-forming windows like u-0226 onward.

**PROPOSED:** record both as **approve**, consistent with the 32 same-class approvals. (If the user actually dislikes the narrower post-ZWNJ ligature, that would contradict their approvals of the identical formation elsewhere — worth one explicit confirmation.)

### 1.3 The two ·Tea·It widening skips — split verdict, resolve after the revert

Units u-0397 (·Tea·Tea·It·It) and u-0399 (·Tea·It·It), class `entered-it-baseline-join-gain`, batch 0, all non-ss10 configurations. Note on both: "I like the extra join but the extension between ·Tea and ·It shouldn't change because ·It joins to something else next".

These windows mix two behaviors: the approved class behavior (the entered ·It now joins the next ·It at the baseline — 33 approvals) and the rejected widening (the ·It cell at the existing ·Tea y5 seam picks up `en-ext-1`, e.g. `qsIt/bar/x-height/baseline/en-ext-1+ex-ext-1` where baseline has `qsIt.en-y5.ex-y0` with no entry extension). The widening is the same mechanism as task A's cluster 1 — the 15 `halves-entry-extension-restored` rejects noted "this widens the ·Tea·It extension for no reason" (u-0444…u-0458). Cross-reference for task A: the cluster-1 policy must cover the halves extension wherever it fires, including inside `entered-it-baseline-join-gain` windows, not just inside the halves class — otherwise these two windows keep the rejected ink.

**PROPOSED:** record nothing now. After task A's cluster-1 revert, these two windows should show the kept ·It·It baseline join with the ·Tea·It seam back at baseline width; Phase 3 re-renders them and presents them for a fresh verdict (expected: approve).

### 1.4 u-0280 — reject whose drafted edit points the other way

Unit u-0280 (·Pea·Oy·Tea·It, `E650:E679:E652:E670`, class `regrouping-floor-drift`, all non-ss10 configurations), recorded **reject** at 03:09Z with the note "I think I would rather prefer ·Tea·It to join".

- Baseline: `qsPea | qsOy | qsTea.half.ex-y5 ~y5~ qsIt.en-y5` — ·Tea·It joins at the x-height; ·Oy·Tea breaks.
- M1: `qsOy ~y0~ qsTea` (the structural floor regroups leftward) and **·Tea·It breaks** — the exact opposite of what the note asks for.

The recorded reject is therefore internally coherent at the window level: flipping to baseline restores the ·Tea·It x-height join the note wants. The contradiction is in the unit's **drafted policy record**, which mechanically targets the headline change ("·Oy joins ·Tea at the baseline") rather than the note: `drafts.policy` proposes adding to `glyph_data/runes/qsOy.yaml` `policy.prefer[+]` the record `{cell: {exit: none}, when: {right: {family: [qsTea]}}, why: 'Reviewer rejected the M1 outcome for E650:E679:E652:E670 (·Pea·Oy·Tea·It)'}`. That is an anti-join edit — it would forbid the ·Oy·Tea join in **every** context and would encode "never join ·Oy·Tea" as user taste, while the note is pro-join (about ·Tea·It) and says nothing against ·Oy·Tea as such. Applying the draft verbatim would do the opposite of what the user wrote.

**PROPOSED:** keep the recorded reject (the window must flip to baseline, restoring ·Tea ~x~ ·It); do **not** apply the unit-local drafted record — let task A's class-level `regrouping-floor-drift` revert cover this window like its 42 sibling rejects; carry the note onto the follow-up ledger (§4) as a forward-looking preference that the engine should keep an existing ·Tea·It x-height join in preference to a floor regroup that destroys it.

### 1.5 u-0468 — the lone approve inside a rejected class

Unit u-0468 (·Pea·May·Tea·Tea, ss03 configurations) is the single **approve** among the 64 verdicted `halves-entry-extension-restored` units (61 rejects, 2 eithers). Its M1 outcome is `qsMay/loop/baseline/None/en-ext-1+ex-bind-pulled-back` — the approved pulled-back withdrawal **plus** the rejected `en-ext-1` entry-extension ink at the ·Pea y0 seam (baseline has `qsMay.en-y0.ex-y5.ex-ext-1` with no entry extension there). A class-wide revert of the halves extension will change this approved window, formally violating the "approved windows must not change from M1" assertion.

**PROPOSED:** treat u-0468 as an expected-to-change exception: the post-revert hybrid (pulled-back kept, `en-ext-1` dropped) matches the user's final taste better than either recorded endpoint, and Phase 3 should re-present it rather than hold the M1 pixels frozen. Flagged in the assertion file under `contradiction_flags`.

### 1.6 Notes scanned and found consistent

- u-0341 (·May·May·May·May, the single `may-quad-order-deferral` reject, "the old way seems nicer to write out by hand"): the reject targets the **lost ·May·May baseline joins** (M1 commits only the final pair), not the pulled-back bitmap — no conflict with the final ·May taste.
- u-0683 and u-0716 (the two `may-exit-withdrawal-generalized` neithers, recorded 05:34–05:35Z, **after** the change of mind): genuine "both behaviors look wrong" rulings on ZWNJ-locked ·May before ·Tea (old `qsMay.noentry` vs. new `qsMay/loop/None/None/locked+ex-bind-pulled-back`), not superseded; they go to the follow-up ledger.
- u-0278 ("the former looks less awkward to right") and the 46 "old way seems nicer" halves rejects and 43 regrouping rejects: all consistent with their own kind; nothing else in the 441 notes contradicts the final taste.

### 1.7 Proposed resolutions for all 8 skips (summary)

| Unit   | Class                             | Window                 | Note (abridged)                        | PROPOSED resolution                                 |
| ------ | --------------------------------- | ---------------------- | -------------------------------------- | --------------------------------------------------- |
| u-0223 | marker-staging-ligature-formation | · ◊ZWNJ ·Tea·Oy        | why is the red underline narrower?     | approve (ligature genuinely 2 px narrower)          |
| u-0224 | marker-staging-ligature-formation | · ◊ZWNJ ·Tea·Oy (ss10) | why is the red underline narrower?     | approve (same window, ss10 configuration)           |
| u-0243 | marker-staging-ligature-formation | ·May·Tea·Oy            | ·May should use the loop stance        | approve (it already does; note superseded)          |
| u-0244 | marker-staging-ligature-formation | ␣ ·May·Tea·Oy          | ·May should use the loop stance        | approve (it already does; note superseded)          |
| u-0245 | marker-staging-ligature-formation | · ·May·Tea·Oy          | ·May should use the loop stance        | approve (it already does; note superseded)          |
| u-0250 | marker-staging-ligature-formation | ·May·Tea·Oy ␣          | ·May should use the loop stance        | approve (it already does; note superseded)          |
| u-0397 | entered-it-baseline-join-gain     | ·Tea·Tea·It·It         | like the join; ·Tea·It shouldn't widen | re-render after the cluster-1 revert, fresh verdict |
| u-0399 | entered-it-baseline-join-gain     | ·Tea·It·It             | like the join; ·Tea·It shouldn't widen | re-render after the cluster-1 revert, fresh verdict |

## 2. The 284 unverdicted units

725 human review units minus 441 verdicts leaves 284 unverdicted. None carry a verdict of any kind in the export, and none are exemplars (all of the ledger-exemplar units were triaged), so **none are blocking**: every one is a non-exemplar sibling inside a class whose behavior the user already ruled on, and Phase 3 treats them exactly as the workflow plans — siblings that may legitimately flip when the policy edits land, counted and reported but never auto-verdicted.

Breakdown by class and batch (unit id ranges are not contiguous; ids are the extremes):

| Class                             | Batch 0 | Batch 1 | Batch 2 | Total   | Rows     | Id range        | Class verdict context                  |
| --------------------------------- | ------- | ------- | ------- | ------- | -------- | --------------- | -------------------------------------- |
| marker-staging-ligature-formation | 13      | —       | —       | 13      | 41       | u-0225 … u-0264 | approved class (32 approve)            |
| regrouping-floor-drift            | 1       | —       | —       | 1       | 7        | u-0277          | rejected class (43 reject)             |
| entered-it-baseline-join-gain     | 11      | —       | —       | 11      | 74       | u-0400 … u-0420 | approved class (33 approve)            |
| halves-entry-extension-restored   | 6       | 123     | —       | 129     | 821      | u-0460 … u-0635 | rejected class (61 reject)             |
| may-exit-withdrawal-generalized   | —       | 5       | 125     | 130     | 606      | u-0673 … u-0937 | approved class (85 approve, 52 either) |
| **Total**                         | **31**  | **128** | **125** | **284** | **1549** |                 |                                        |

(The user stopped triage partway through batch 1 — 6 halves stragglers in batch 0 are windows they paged past — and never opened batch 2.)

Expected to leave the workload entirely once the rejected behavior reverts: the **129 halves units plus the 1 regrouping unit, ≈130 units (≈46% of the 284; 828 of the 1,549 rows)**. Their divergence _is_ the rejected behavior, so after the revert their rows match baseline again and they stop being divergences at all. Two hedges: (a) a window only re-converges if **every** divergent position reverts — any halves window that also contains an approved ·May withdrawal or ·It join gain stays a (smaller) divergence; (b) the count is exact only after Phase 3 re-runs the conformance audit. The remaining ≈154 (13 marker-staging + 11 entered-it + 130 may-exit) stay as divergences under approved classes and need no further verdicts unless the policy edits change their rows, which Phase 3 reports.

## 3. The approve/either record

**Export-to-shard alignment is clean.** The verdict file's `manifest_generated_at` (2026-06-11T05:02:37Z) equals the on-disk manifest's `generated_at`, so the export CLI's mismatch warning (`rebuild/review/export.py:107`) would not fire; all 441 verdict unit ids resolve in today's shards with no orphans. The manifest records `repo_head: 8b06b75`; the single commit since (HEAD, 3c00b27) is the review-surface secondary-seams feature, and the on-disk artifacts were built from that tree while it was still dirty — the manifest already carries the `secondary_seams` block, so the artifacts are current with HEAD even though the recorded head lags by one. The font identities still hold: `site/AbbotsMortonSpaceportSansSenior-Regular.otf` and `rebuild/out/review/fonts/before.otf` both hash to `3211a7…cf35`, and `rebuild/out/m1/M1.otf` and `rebuild/out/review/fonts/after.otf` both hash to `750f89…80ce`, matching the manifest. The 441 verdicts can be applied against today's artifacts as-is.

**The 246 approve pins all validate.** Every approved unit's `drafts.pin` has `syntax: pass` and `semantics_after_font: pass`, none is a `duplicate_of` another pin, and each carries a `suggested_home` (`site/the-manual.html`). By class: 85 may-exit-withdrawal, 40 ss03-chain-join-gains, 33 entered-it, 32 marker-staging, 28 same-seam, 15 pea-chain, 6 zwnj-follower, 6 pre-ligature-cleanup, 1 halves (u-0468 — see §1.5).

**The 77 either any-of drafts are all well-formed.** Every either unit's `drafts.any_of` has a non-empty `text`, a `features` map, and a non-empty list of non-empty string candidates. 20 units (all `regrouping-floor-drift`) carry two candidates — the baseline grouping and the M1 grouping are both acceptable renderings. 57 units carry a single candidate (52 may-exit-withdrawal, 3 same-seam, 2 halves): in those, before and after agree at seam grain and differ only at cell grain, so one rendering expectation legitimately covers both behaviors. No malformed drafts.

**The 3 neithers** (u-0215 marker-staging ·Pea·May·Tea·Oy; u-0683 ·Pea ◊ZWNJ ·May·Tea; u-0716 ·Tea ◊ZWNJ ·May·Tea) are carried to the follow-up ledger with full provenance in §4 — neither the baseline nor the M1 behavior is acceptable, so they are design work, not this round's policy edits.

## 4. The follow-up ledger

Forward-looking user input that is **not** this round's work, with units and context:

1. **Indefinite ·It join chains** — u-0421 (·May·It·It·It, approved): "it'd be even better if the ·It letters chained joins indefinitely". M1 chains ·May ~y5~ ·It ~y0~ ·It then breaks before the third ·It; the wish is for the chain to continue. A future policy/engine round on `qsIt`'s exit refusals.
2. **·Tea·It should join (even where the floor regroups)** — u-0280 (·Pea·Oy·Tea·It, recorded reject): "I think I would rather prefer ·Tea·It to join". The round-1 revert restores the baseline ·Tea ~x~ ·It join in this window; the forward-looking part is a settle-order preference (keep an existing ·Tea·It join rather than regroup ·Oy·Tea at the baseline) that should inform any future revisiting of the structural floor. See §1.4 for why the unit's drafted record must not be applied.
3. **·Tea·It extension must not widen when ·It joins onward** — u-0397 and u-0399 (skips): "I like the extra join but the extension between ·Tea and ·It shouldn't change because ·It joins to something else next". Cross-reference task A's cluster 1 (the 15 halves rejects "this widens the ·Tea·It extension for no reason", u-0444…u-0458): same `en-ext-1` mechanism at an existing y5 seam, so they are very likely the same issue and the cluster-1 revert should resolve both — this entry stays on the ledger only until Phase 3 confirms the entered-it windows lost the widening too. The nearby same-seam reject u-0636 ("new way has a worse ·May·It join", with u-0656) is the exit-side cousin at the ·May·It seam and is round-1 work, listed here only as context.
4. **The three neithers — both behaviors wrong, needs a third design:**
   - u-0215 (·Pea·May·Tea·Oy, `E650:E665:E652:E679`, ss03 configurations, marker-staging, recorded 02:34:52Z): old joins ·May ~y5~ into half-·Tea with no ligature; new forms `qsTea_qsOy` but ·May drops to the pulled-back break. Decided by `qsPea.yaml stances.half.surface.exits.y6` (join-count rank). Plausible third option: a ·May that joins **into** the `qsTea_qsOy` ligature.
   - u-0683 (·Pea ◊ZWNJ ·May·Tea, default/ss02/ss04/ss05, may-exit-withdrawal, 05:34:51Z) and u-0716 (·Tea ◊ZWNJ ·May·Tea, same configurations, 05:35:48Z): old shows `qsMay.noentry`, new shows the locked pulled-back loop (`qsMay/loop/None/None/locked+ex-bind-pulled-back`); the user rejected both word-initial ·May-before-·Tea drawings. Decided by `qsMay.yaml policy.refuse[0]` (declaration order). Needs a new word-initial ·May-before-·Tea shape, out of scope for round 1. Recorded after the mid-stream change of mind, so the final taste does not supersede them.

## 5. Acceptance assertion inventory (Phase 3)

Written to **`rebuild/out/policy-round-1-assertions.json`** (`format: ams-policy-round-1-assertions/1`), generated by `tmp/build_round1_assertions.py` from the verdict export plus today's shards. For each bucket it carries the unit index (class, batch, codepoints, configurations, verdict, note) and `windows_by_config` — the deduplicated codepoint-string lists per configuration that Phase 3 asserts over. Bucket semantics are embedded in the file; in short:

| Bucket      | Units | Assertion                                                                                       | Windows per configuration (default / ss02 / ss03 / ss04 / ss05 / ss02+ss03 / ss02+ss03+ss05 / ss10) |
| ----------- | ----- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| rejected    | 107   | must shape identically to the baseline tables (`rebuild/out/baseline-<config>.tsv.gz`)          | 97 / 97 / 105 / 97 / 97 / 105 / 105 / —                                                             |
| approved    | 246   | must shape identically to the verdicted M1 build (after-font SHA `750f89…80ce`)                 | 167 / 167 / 184 / 167 / 167 / 184 / 184 / 15                                                        |
| either      | 77    | report any change; both endpoints acceptable, any-of candidates enumerate acceptable renderings | 75 / 75 / 25 / 75 / 75 / 25 / 25 / —                                                                |
| skip        | 8     | report any change; resolutions in §1.7 are PROPOSED only, never auto-record                     | 3 / 3 / 7 / 3 / 3 / 7 / 7 / 1                                                                       |
| neither     | 3     | report any change; on the follow-up ledger                                                      | 2 / 2 / 1 / 2 / 2 / 1 / 1 / —                                                                       |
| unverdicted | 284   | count and report changes only; never treat a change as approval or rejection                    | 255 / 255 / 176 / 255 / 255 / 176 / 176 / 1                                                         |

Two integrator annotations ride along in the file:

- `contradiction_flags`: u-0280 (apply the class revert, not the unit-local drafted record) and u-0468 (approved window expected to change under the class revert; confirm with the user).
- `watch`: the 41 approved/either windows whose M1 outcome **gained** entry-extension ink relative to baseline (38 approve + 3 either; notably 28 of the 40 ss03-chain-join-gains approvals, plus u-0218, u-0237, u-0266, u-0267, u-0270, u-0272, u-0383, u-0424, u-0432, u-0443, u-0459, u-0468). Task A's halves revert must be scoped so extensions at **newly formed** seams (the approved join gains) survive, and only extensions at seams that already existed in baseline at the same Y flip back; Phase 3 should diff these 41 windows individually rather than relying on the bucket-level pass/fail.

The strict "approved must not change" assertion is expected to hold for 245 of 246 approved units; u-0468 is the documented exception (§1.5). Either-bucket flips are expected wherever the rejected behavior was the only cell-grain difference (the 2 halves eithers u-0443 and u-0459, and potentially the 20 regrouping eithers, which tolerate either grouping by construction).
