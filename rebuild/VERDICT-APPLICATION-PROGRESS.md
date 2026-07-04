# Verdict-application progress

The living checkpoint for turning the round-3 review verdicts into rune-data edits, one logical change at a time. Each change is made, eyeballed, and committed on its own before the next begins — commits are the real checkpoint; this file tracks what each commit accounted for, what remains, and the open questions.

> **Resume here (2026-07-03, updated again after the phase-end re-baseline).** This is an in-flight pass _within_ the M1 round-3 adjudication — finish it before resuming WHATNEXT.md's main migration thread. **The extensions/contractions phase is COMPLETE** (E1 `8749048`, E3 `5ffff0e`; E2 parked; same-seam + contractions no-op). **Phase 2 (`seam-loss-withdrawal`, 83 rejects) is FULLY DISCHARGED and fully COMMITTED — Groups A, B, D-1, and C are all in** (Group C landed as the user-directed alt-chain shape `·Tea ~b~ ·No.alt`, one line in `qsNo.yaml`, committed in `6ab6309`; D-2 and the "6 engine-limited" park dissolved — ZWNJ targets retired by the boundary rule, the ·Oy·Tea·No pair restored by Group C's acceptor). **The phase-end review-surface rebuild + census-pin re-baseline is DONE (2026-07-03):** `run_m1` recompiled M1.otf at HEAD (oracle 5,477 unmatched, `multi_matched` 0, boundary gate 885,808 runs / 0 divergences, Manual pins 17/17), the surface rebuilt in place into `rebuild/out/review` (15,972 units / 80,990 rows / 20 batches, stale shards cleared first), and every review census pin re-baselined — see "Gates / known state". **Next: the human blessing adjudication on the fresh surface** — the verdict families now sum 1,149 windows, including the ~980-row ·Tea·No chain (in `seam-loss-withdrawal`, now 373 units), Q2's ·Pea·No chains, and the Q1★/Q4 new-join gains. Workflow rules: one logical change per commit, verify each with the **full-rebuild recipe** (not just the settle probe — see "Resume commands"), show the user before committing, and keep this file honest. Working style the user likes: scope each edit tightly, prove it surgical, present pros/cons, don't commit without approval.

## Source of truth

- **Verdicts:** `verdicts-11.33.00PM.json` (format `ams-review-verdicts/1`, manifest `2026-06-29T04:45:25Z`, 4,828 unique-unit verdicts: 4,146 approve / 332 reject / 318 neither / 20 either / 12 identical). The manifest it pins matches the live `rebuild/out/review/manifest.json`.
- **Units:** `rebuild/out/review/units/*.json` — each unit (`u-NNNN`) carries `class`, `notation`, before/after cells + seams + `extensions`, the `explain` settle trace, and a `drafts.policy` suggestion (target rune file, keypath, a concrete record). The drafts mostly frame everything as `prefer`/`refuse`, so the **review class** is the reliable signal for what a window is about, not the draft keypath.

### Verdicts are evidence, not gospel

Rune edits are **rule-level** (one `extend`/`contract`/`refuse` record with a `when:` predicate); verdicts are **window-level evidence**. A single record decision usually subsumes dozens of windows. Where a recorded verdict no longer reflects intent (a mind-change), the override is captured **here**, at the rule level — the gitignored verdict export is left untouched.

**Recorded overrides:**

- **·It·Day connector = 1px, not 2px.** ~108 windows carry `approve` for the entered-·It exit extension before ·Day (`qsIt.yaml` `extend[1]`, the `ex-ext-1` 2px connector), plus 7 `either` and 10 already-`reject`. Mind-changed to: ·It·Day reverts to the legacy single-pixel connector. Handled by change **E1** below.
- **·Tea·No = the baseline alt chain, not the x-height join (2026-07-03).** The user's stated preference: absent other considerations, ·Tea·No should render `·Tea ~b~ ·No.alt` (full ·Tea joining alternate ·No at the baseline) rather than the old font's default `·Tea.half ~x~ ·No`. This supersedes, at rule level: the x-height-first reading of the u-14734–14742 reject notes ("if ·Tea doesn't join ·No at the x-height then we should use a full-size ·Tea and a ·No.alt" — the fallback clause is now the preference), the 8 trailing-pair approvals that pinned ·X·Tea·No at y5 (u-15258, u-1842, u-1848, u-1863, u-1881, u-1887, u-15496, u-15137), and the old-parity T8 shape that was briefly applied and then replaced the same day. Handled by the final Group C landing below.

## Phases

Working order (user's call): **extensions/contractions first** (most inconsistent, cheapest YAML), then the larger `prefer`/`refuse`/gain piles.

The extension/contraction story is concentrated in three review classes plus one cross-cutting record:

| Class | windows | approve | reject | other | meaning |
| --- | ---: | ---: | ---: | --- | --- |
| `halves-entry-extension-restored` | 183 | 1 | 172 | 10 identical | receivers (·Pea/·May/·Day/…) extending their **entry** for a half that exits at x-height — overwhelmingly rejected |
| `same-seam-extension-non-summing` | 88 | 88 | 0 | — | keep as-is |
| `extension-non-summing` | 30 | 20 | 10 | — | mixed (·Tea+Oy→·Day keep; ·May·Day·Utter reject) |
| _cross-cutting_ `qsIt` `extend[1]` (entered-·It exit extension) | — | see below | — | — | fires before ·Day/·It/·May/·Utter/·No; votes differ sharply by right-neighbor |

`qsIt` `extend[1]` firings by right-neighbor (windows with a verdict): ·Day 108a/10r/7e · ·It 93a/2r · ·May 89a/0r · ·Utter 63a/12r/48n/6e · ·No 1a · qsDay_qsUtter 4a/1r/1e.

## Extensions/contractions checklist

- [x] **E1 — ·It·Day single-pixel connector. COMMITTED `8749048`.** Narrow `glyph_data/runes/qsIt.yaml` `policy.extend[1]` with `right: {except: [family: qsDay, family: qsDay_qsUtter]}` so the entered-·It exit extension no longer fires before ·Day **or the ·Day+·Utter ligature** (same half-·Day baseline seam) — 1px connector — while ·It·It / ·It·May / ·It·Utter keep the 2px extension. **Verified by full rebuild** (see recipe below): net +1 UNMATCHED window in default/ss02/ss05, zero elsewhere. E1 makes ·It·Day **uniformly** 1px — a _deliberate divergence from the old font_ in the one window where the old font drew 2px (`u-14916` ◊ZWNJ·May·It·Day, ·It entered at x-height post-ZWNJ): **user-confirmed LGTM 2026-06-29, keep uniform 1px.** That window + its boundary siblings are intended divergence to bless at the next re-baseline, not a regression.
- [ ] **E2 — `halves-entry-extension-restored` (172 rejects). PARKED (decision 2026-06-29: skip, do easy items first).** Stays documented; revisit as either the empirical sibling-deletion test (~103) or the engine milestone (~27 wall). 0 approve / 172 reject (10 identical = pixel-identical, harmless). Traced to two distinct phenomena:
  - **The round-2 engine wall (~27, ·May).** `qsMay.extend[3]` (baseline entry, the `en-ext-1`) in the `pea-may` (composed `en-ext-1`+`ex-ext-1` under ss03) and `it-may` ([X]·Oy·It·May, predecessor's-predecessor entered) windows. `m1-divergences.yaml`'s `halves-entry-extension-restored` entry + `POLICY-ROUND-2-REPORT.md` record an **exhaustive, build-tested 25-candidate lever hunt that found no closed-vocabulary rune record can revert these without collateral** — the discriminator is a depth-2 left-context predicate the frozen `when:` grammar cannot express. Honoring these = the deferred **extension-suppression engine milestone**, not a rune edit.
  - **The x-height-halves siblings (~103, NEW).** `qsDay.extend[0]`, `qsOy.extend[0]`, `qsNo.extend[0]`, `qsPea.extend[0]`, `qsMay.extend[4]` — all the `{entry: x-height, by: 1, when: {left: {class: halves-that-exit-at-x-height, joined_at: x-height}}}` shape. These are the **same transcription-artifact record round-1 deleted from `qsIt`** ("the shipped font never realized it"); these siblings just weren't reached, and ~103 of them only surfaced as divergent after the 2026-06-27 pulled-back re-baseline, so they **predate the round-2 analysis**. **Hypothesis: cleanly deletable parity-revert.** Unconfirmed — needs an empirical oracle test (delete each record on a scratch build via `rebuild/tools/scratch_build.py`, confirm UNMATCHED drops with zero collateral on currently-matched windows) before any deletion, because the review surface contains only divergent windows and can't by itself prove a record never fires in a matched one.
- [x] **E3 — ·May·(Day+Utter) x-height exit extension restored.** The 10 `extension-non-summing` rejects are all ·May·Day·Utter (`ext=0`, decided by order, no record): the old font drew `qsMay.ex-ext-1` but the new font dropped it because `qsMay.extend[0]`'s trigger list had bare `qsDay` (and `qsFee/qsJai/qsJay/qsRoe/qsIt`) but **not** the `qsDay_qsUtter` ligature. Fixed by adding `qsDay_qsUtter` to that list (code-point order, right after bare `qsDay`) — the mirror of E1's ligature point. Verified: ·May·(Day+Utter) and its follower variants regain `ex-ext-1`; bare ·May·Day unchanged. The 20 approves (·Tea+Oy→·Day etc.) are a different record, untouched. **COMMITTED `5ffff0e`.** Trips two expected baseline pins (`test_review_enrich`, `test_review_build::test_secondary_seam_census` −6 units) that clear on the phase-end review rebuild.
- [x] **`same-seam-extension-non-summing` (88 approve) — NO-OP confirmed.** All approve; the engine's behavior is liked, no record changes.
- [x] **Contractions sweep — NO-OP this batch.** No unit cites any `policy.contract` record and no cell carries a contraction marker: every `contract:` trigger (·J'ai/·Zoo/·Jay/·Fee on `qsNo`/`qsUtter`/`qsTea`/`qsMay`) is an **unmigrated family**, so the records can't fire in the M1-batch-2 corpus. Re-adjudicate when those families migrate.

**Extensions/contractions phase: COMPLETE** (2026-06-29) — E1 + E3 committed, E2 parked, same-seam + contractions no-op.

## Phase 2 — seam-loss-withdrawal (FULLY DISCHARGED 2026-07-03; all groups committed)

The 83 `seam-loss-withdrawal` rejects (the 2026-06-27 pulled-back-removal aftermath, biggest actionable reject pile) triage into **three root causes / five logical changes**. Triaged + empirically settle-tested 2026-06-29 (workflow `seam-loss-withdrawal-triage`; harness `rebuild/tools/seam_loss_probe.py` and the clean-vs-candidate differ `rebuild/tools/seam_loss_diff.py`). **Use `seam_loss_diff.py` as the collateral arbiter** — it re-settles BOTH the real runes and a candidate dir under the default config and flags only windows that actually move; the bare shard comparison reads STALE shard `after` seams and reports phantom regressions (the trap two triage agents nearly fell into).

| # | Logical change | Rune edit | Restores | Collateral | Status |
| --- | --- | --- | ---: | --- | --- |
| **A** | word-initial ·Utter·No keeps its x-height join | `qsUtter` refuse `{stance: alternate, exit: baseline, when: {left: {is: boundary}, right: {family: qsNo}}}` | 43/config (44 ss03) | 0, all 8 configs | **COMMITTED** (`e300b13` + the `flipped`→`alternate` rename; user-framed as "'un-' shouldn't use alternate ·Utter", The Manual p21). Full-rebuild verified −304 oracle rows; `make test` green (6753). |
| **B** | ·It·It·Utter joins again | `qsIt` policy.refuse (the before-·Utter veto whose `when` is `self: {entry: none}`, `right: {family: qsUtter}`): drop `qsIt` from its `left.family` list | 16/16 | 0, all configs | **COMMITTED (2026-07-02, one commit with D-1).** Re-verified at HEAD (16/16, 0 collateral); full-rebuild diff: ss04 −15 UNMATCHED, each ss03-bearing config −1, default/ss02/ss05 net 0, zero new UNMATCHED; all 17 moved units are ·It·It·Utter windows landing in blessed ledger classes. Taste-note: under default, ·It·It·Utter·May now regroups to the ·It·Utter join and breaks ·Utter·May (old chose the opposite; both single-join; lands in `regrouping-floor-drift`). |
| **D-1** | ·No·May·May·May joins | `qsNo` policy.prefer `{cell: {exit: baseline}, over: {exit: x-height}, when: {left: {is: boundary}, right: {family: qsMay}}}` — **amended 2026-07-02: the `left: {is: boundary}` guard is load-bearing** | 1/3 | 0, all configs | **COMMITTED as amended (2026-07-02, one commit with B).** The triage's unguarded record predates the Group A commit and now regresses 15 restored ·Utter·No·May windows (y5 → break) — ·No flips for ·May and a joined predecessor loses its x-height entry. Boundary-guarded, the settle differ shows 1/3 restored (the u-15162 target; the other 2 are D-2's) with zero other movement; full-rebuild diff: −1 UNMATCHED in all 7 configs (the target resolves into `dangling-anchor-dropped`), all 15 moved units are word-initial ·No·May·May windows in blessed classes. |
| **C** | word-initial ·Pea·No / ·Tea·No join | see the "Group C resolved" section below — the ·Pea half was superseded by Q2's alt chain; the ·Tea half landed 2026-07-03 as **one line**: `qsTea` added to flipped ·No's baseline-entry `from:` list | every adjudicable reject discharged (·Tea·No now chains; the ·Oy windows byte-exact) | the 8 y5-pinned approvals flip by design (recorded override) | **COMMITTED `6ab6309` (2026-07-03, user-directed alt-chain shape: `·Tea ~b~ ·No.alt` absent other considerations).** The 2026-06-29 hold reason dissolved: the 4 ◊ZWNJ windows are definitionally word-initial under the ratified boundary rule and their rows are absorbed by `boundary-echo`, never adjudicated. |
| **D-2** | 2× ◊ZWNJ·Utter·May | — | — | — | **RETIRED BY RULE (2026-07-03):** both targets are ZWNJ windows — post-ZWNJ is definitionally word-initial and the rows ride `boundary-echo`; the old font's post-ZWNJ-only behavior is unrecoverable by design. No rune work remains. |
| **—** | 6 engine-limited | — | — | — | **DISSOLVED (2026-07-03):** the 4 ◊ZWNJ windows are retired by the boundary rule (above), and the 2 ·Oy·Tea·No windows are **restored byte-exact by Group C's acceptor record** — the "depth-2 wall" framing was an artifact of hunting for a `when:` predicate; making bare `qsTea` an acceptor on flipped ·No's baseline entry sidesteps it (the floor then chains exactly where the old font chained). Nothing is left to document as engine-limited residue from this phase. |

**Root causes:** A+C are the **qsNo chain-flip** (·No takes its baseline `flipped`/`alternate` stance to gain a forward join, lowering/breaking the predecessor join); B is the **·It·It·Utter withdrawal** (the second ·It, entered by the first, is vetoed before ·Utter by a carried-over before-·Utter refuse); D is the **·May tail**. The settle probe is **default-config only** — the commit gate is always the full per-config rebuild diff.

### Group C resolved (2026-07-03, committed `6ab6309`): ·Tea·No takes the baseline alt chain

The final landed shape is **one line**: `qsTea` added to flipped ·No's baseline-entry `from:` list in `qsNo.yaml` (code-point order, right after Q2's `qsPea`; the `qsTea_qsOy` ligature was already there). With the acceptor in place, the settlement floor and stance order make `·Tea ~b~ ·No.alt` the default wherever the chain can form — the user's stated preference, ratified the same day this landed ("absent other considerations, ·Tea·No should be `·Tea ~b~ ·No.alt` rather than `·Tea.half ~x~ ·No`"; see the recorded override above). The mirror of Q2's ·Pea decision.

The facts that dissolved the 2026-06-29 hold: (1) the ratified boundary rule makes the 4 ◊ZWNJ windows definitionally word-initial — the schema cannot address them, `boundary-echo` absorbs their rows, and post-ZWNJ ·Tea·No now equals bare ·Tea·No (which, chained, happens to match the old font's post-ZWNJ alt chain exactly); (2) Q2 already resolved the 7 word-initial ·Pea·No windows; (3) the "6 engine-limited" framing was an artifact — the ·Oy·Tea·No·May/·Utter windows restore **byte-exact** from the acceptor alone, no depth-2 predicate ever needed.

**The standing exceptions ("other considerations") the engine keeps:** before ·Day/·It/·Oy the pair stays `[y5, y5]` — `qsNo.prefer[1]` (exit x-height over baseline toward those followers) holds, ·No joins both sides at the x-height, and those windows remain **matched to the old font**; extending the chain to them would mean narrowing that record's `left: except` to include `qsTea` — a separate decision, not taken. And after an entered ·Tea (·Day·Tea·No and kin) the chain structurally cannot form (full ·Tea never pairs a baseline entry with a baseline exit), so those windows are unchanged too.

**Verification (full A/B scratch oracles, `tmp/t7-pre` = pre-change HEAD, 4,514 unmatched, vs `tmp/t1-post` = 5,477; gates re-run via `run_m1` on the real tree):** 17 rows leave the audit byte-exact (the two ·Oy·Tea·No windows × 7 configs, plus ·May·Tea·Tea·No under the 3 ss03-bearing configs); **980 rows / 152 unique windows enter as embraced divergence, every one containing a ·Tea·No adjacency** (perfect scoping — nothing else in the font moved): 690 rows previously matched the old font's y5 join and now draw the chain (bare ·Tea·No included — the deliberate look change), 290 complete already-divergent unverdicted `regrouping-floor-drift` windows into 3-letter chains, 289 of the total involve the namer dot (post-dot ·Tea·No chains like bare ·Tea·No; the dot does not split runs, so these need blessing like the rest). The 8 previously-approved y5 trailing-pair windows flip by design (the recorded override). Boundary gate 885,808 runs / 0 divergences; Manual pins 17/17 (no pin carries a ·Tea·No adjacency; the ·No-led pins like `·Day ~b~ ·No.alt ~b~ ·Tea` and `·It ~b~ ·No ~b~ ·Tea` are untouched and pass); `multi_matched` 0; defect gates clean. All 980 rows land in the phase-end review for family-level blessing, like Q2's ~1,850.

**The superseded interlude, kept for the record:** before the user stated the preference, a full old-font-parity shape (T8: the acceptor + four steering records — word-initial refuse/prefer on `qsTea`, word-final and before-·Pea entry-prefers on `qsNo`) was built, fully verified (63 rows restored byte-exact, +119 reshapes, 0 collateral), and briefly applied. It was replaced the same day by the one-line shape above. The candidate ladder survives in `tmp/cand-t{1,2,12,3,6,7,8}` with the mechanics documented: a `prefer` cannot beat the floor once the chain is live (T12), a refuse alone falls to the exitless-full order tiebreak (T6), and the steering-record kit in `tmp/cand-t8/` is the recipe if per-context ·Tea·No shaping is ever wanted back. Diff harness: `rebuild/tools/t7_audit_diff.py`.

## Phase 3 — the 2026-07-03 regrouping-floor-drift batch (IN PROGRESS)

The user recorded 32 new verdicts (`verdicts-12.04.13AM.json`) — **against the B+D-1 preview surface** (`tmp/review-preview-bd1`, manifest `2026-07-03T06:13:47Z`), not the served one. **ID caveat: that export re-stamps the old 4,828 verdicts onto the new manifest, but only 273 of those IDs mean the same window on both surfaces.** Rule: old verdicts resolve via `verdicts-11.33.00PM.json` + `rebuild/out/review`; the 32 new ones via the 12.04 file + the bd1 preview. Never join the merged file by ID. Harness for this batch: `rebuild/tools/regroup_diff.py` (targets = the new rejects/neithers with desired seams; guards = the batch's approves on bd1 plus every old latest-verdict window on the old surface, default config). **(2026-07-03, later) Both halves of that ID caveat are now mechanized:** `rebuild/tools/carry_verdicts.py` performs the content-keyed join for the blessing pass (its `verdicts-carried-forward.json` seeds the live surface with the 2,572 prior verdicts whose units are render-identical — the derived `explain` / `drafts` / `provenance` fields are excluded from the content key after the `flipped`→`alternate` settle-trace rename was found blocking 1,311 carries), and the review server now autosaves the in-tab store to `verdicts-autosave.json` after every mutation, refusing to restore across a manifest change — see WHATNEXT.md's 2026-07-03 "seatbelts" update and REVIEW-REPORT.md's "Triage flow".

- [x] **N1 — ·It·It before ·May/·No/·Utter (u-1318, u-1319; u-1317 keeps ·Day out).** Extended `qsIt` prefer[0]'s `then:` from `qsMay` to `[qsMay, qsNo, qsUtter]` — the round-1 "entered ·It declines so the follower keeps the join" record, per the user's note that these repeat standing opinions. APPLIED; settle-verified 2/2 restored, zero collateral both guard pools. Full rebuild pending.
- [x] **N4 — ss03 ·Utter·Tea yield (u-1327/1328/1330/1331; u-1329 keeps ·No out).** New `qsUtter` prefer: `{cell: {exit: none}, over: {exit: x-height}, when: {right: {family: qsTea, then: {family: [qsDay, qsMay, qsIt, qsUtter]}}}}`. APPLIED; settle-verified 4/4 restored under ss03, zero collateral. Full rebuild pending.
- [x] **Q1/N2 — SUPERSEDED by Q1★.** The ·It-steps-aside lever (`tmp/cand-n2b`) traded joins; Q1★ adds them instead. Not applied.
- [x] **Q1★/UXM2 — entered ·Utter reaches ·May at the x-height (the user's sidequest, 2026-07-03). APPLIED, user-approved ("a GREAT improvement"), with the stubless amendment.** The user's rule: use ·May's pulled-back x-height entry when ·Utter holds a baseline join from its predecessor and ·May isn't about to join its own follower at the x-height. Landed as: (1) `qsUtter` refuse[2] (mono x-height exit before ·May) narrowed with `self: {entry: none}`; (2) new `qsMay` prefer[0] `{cell: {exit: x-height}, over: {entry: x-height}, when: {left: {family: qsUtter}}}` for the condition-2 tie; (3) **the `pulled-back` bitmap deleted outright** — the x-height entry now binds `pulled-back-stubless` with `joined_x: 2` (user request: the baseline-tail stub is pointless when entered at the x-height), which also retires the dormant qsFee `contract` bind record; (4) the newly-exercised bare `qsMay.loop.en-y5` declined-exit terminal blessed in `m1-contact-allow.yaml`. Verified: u-1320/·No·It·Utter·May/·Utter·It·Utter·May → three joins; ·It·It·Utter·May gains ·Utter·May; ·Pea·Utter·May realizes u-1323's "even better" `[y0, y5]`; ·Pea·Utter·May·May → `[y0, y5, y0]`; bare ·Utter·May, ligature-fed ·May, the condition-2 approves (·It·Utter·May·Day/·No), and the ·May·May·May·May pin unchanged. Blast radius: 10 new-surface + 42 old-surface approves move — 50 pure gains, 2 Q3-shaped regroups (·No·Utter·May·Day, ·No·Utter·May·No) folded into the open Q3 question.
- [x] **Q2/N3 — the ·Pea·No.alt baseline chain (u-1280–1284, neither×5). APPLIED, user-approved ("excellent").** The broad lever (the grammar forbids `right.then` on refuses): qsNo dropped from `qsPea` refuse[0] + qsPea added to flipped-·No's baseline-entry `from:` list. Every ·Pea·No pair now starts an alt chain; the 5 wishes + 2 held Group-C ZWNJ windows restore to the old font's chain; ~17 old approves move (mid-word y5→y0 flips + 3-chain gains, all user-seen in the demo); adds ~1,850 rows of embraced new-vs-old divergence to bless at re-baseline.
- [x] **Q3 — resolved via Q1★ + two amendments (2026-07-03).** The ·Pea-yield lever (`tmp/cand-may`) was discarded. The user's refinement: ·Pea·Utter·May takes normal mono ·Utter + the x-height ·May reach (Q1★), _unless_ ·May has a higher-priority forward x-height join — and the ss03 ·May·Tea.half join is NOT higher-priority (it loses). Landed as `right: {except: [family: qsTea]}` on the condition-2 qsMay prefer, so under ss03 ·Pea·Utter·May·Tea → `[y0, y5, break]` (u-1337 resolved). **The ·May drawing question (Q3★): tuck-under won** — the user compared a back-reaching long-bar bitmap against the stubless pulled-back nesting under ·Utter in rendered fonts and chose the tuck for all windows; the invented `reaches-way-back` bitmap + contract bind were removed again. **Residue:** the follower-case rejects (·Pea·Utter·May·Pea/·Day/·No/·It, u-1335/1338/1340/1341) stay at `[y0, break, y5]` per the user's "unless" rule — the original wish for the old `[break, y0, y5]` grouping is superseded by the reformulated rule; the 2 ·No·Utter·May·X regroups ride the same rule.
- [x] **Q4/P1344 — ·Pea·Pea + ·Pea·Utter both join (u-1344, "the new way is broken"). APPLIED, user-approved ("looks great"), plus the ·May·Pea·Pea amendment.** qsUtter added to `qsPea` refuse[1]'s except list: ·Tea·Pea·Pea·Utter → `[break, y6, y0]`, 21 old approves gain the top join. The user's follow-up: in ·Pea·May·Pea·Pea the ·May-entered ·Pea should ALSO chain into the final ·Pea at y6 — landed by deleting `qsPea.half`'s `{entry: x-height, exit: y6}` never-pairing (the left dip comes from entry-stub arithmetic; the y6 exit bar already existed). ·Pea·May·Pea·Pea → `[y0, y5, y6]`; ·May·Pea·Pea / ·Utter·Pea·Pea gain the same y5→y6 chain; bare ·Pea·Pea and ·Pea·Pea·Pea unchanged. This supersedes the recorded u-1279 approve. Blessed: 3 contact signatures for the y6-entered ·Pea's baseline meeting with ·Utter (user-approved look), and 8 y7-corner siblings for the new en-y5.ex-y6 half-·Pea cells (the same corner long blessed on the bare ex-y6 cells).

- [x] **P-Indian — ·Day yields ·It to ·Utter, the Manual-pin gate's first catch (2026-07-03). APPLIED, Manual-mandated (site/the-manual.html:3828, "Indian" = ·It·No·Day·It·Utter·No).** New `qsDay` prefer `{cell: {exit: none}, over: {exit: baseline}, when: {right: {family: qsIt, then: {family: qsUtter}}}}` — the qsDay mirror of N1's ·It record: ·It cannot join at the baseline on both sides, so the floor-tiebreak ·Day·It join was foreclosing the Manual-mandated ·It·Utter join (the pin wants `·Day | ·It ~b~ ·Utter`; the engine gave `·Day ~b~ ·It | ·Utter`). Under ss04 the pass-through unlock outranks the yielding prefer, so ·Day·It·Utter keeps both joins there (probed: y0,y0). A/B scratch oracle diff (`tmp/pin-fix-pre` = HEAD runes vs `tmp/pin-fix-post`): 108 rows move `regrouping-floor-drift` → `bare-name-live-join`/`dangling-anchor-dropped` (their seams now match the old font — the restoration), zero rows regress, and exactly 6 rows — ·Day·It·Utter·May in each non-ss04/ss10 config — go `dangling-anchor-dropped` → UNMATCHED: the old font kept ·Day·It and reached ·May through the alternate ·Utter with ·It's exit dangling, while the fixed engine renders ·Day | ·It~b~·Utter~x~·May (the Manual's break composed with Q1★'s entered-·Utter reach); preserving the old shape would need a depth-3 right predicate the grammar forbids (`then` can't nest), and it leans on a dangling exit besides — the 6 rows go to the next review pass as intended divergence. Supersedes u-1278's "either" on ·Pea·Day·It·Utter (regrouping-floor-drift); the two deferred-ss04 rejects on ·Tea/·Oy·Day·It·Utter ("the new way is broken") stay parked with their bucket. No default-config approve contradicted the fix — the wrongly-joined windows were the unverdicted floor-drift stragglers.

Browser demo for Q1–Q4: `tmp/demo/index.html` + scratch-built fonts (`tmp/demo-build/{base,n2,n3,may,p1344}/M1.otf` via `rebuild/tools/scratch_build.py`), old font = `tmp/review-preview-bd1/fonts/before.otf`.

### Remaining Phase-2 piles (after seam-loss-withdrawal)

Mostly-neither: `regrouping-floor-drift` (24n), `deferred-ss04` (44r but ssNN-parked). Approve-heavy (likely confirm-only no-ops): `no-chain-gains` 160, `kern-channel-out-of-scope` 187, `may-utter-gains` 86, `ss03-chain-join-gains` 70, `entered-it-baseline-join-gain` 87, `tea-it-xheight` 39, `pea-chain-regularized` 37, `oy-it-baseline` 20.

## Open follow-ups / questions

- ~~**qsDay_qsUtter ligature after ·It.**~~ Resolved in E1: the exclusion covers both `qsDay` and the `qsDay_qsUtter` ligature.
- ~~After the extension phase, **rebuild the review surface** to re-baseline.~~ Done 2026-07-03 — see "Gates / known state".

## Gates / known state

- **Settle probe** (per-change spot check): see commands below.
- **Rebuild suite:** `uv run pytest rebuild/ -n auto --dist worksteal -q`. **Updated baseline (2026-07-03, post-re-baseline): a clean HEAD fails only the 4 documented spec pins** (`test_surface::test_real_cell_bindings_all_match`, `test_spec_load::{test_loads_all_six_runes, test_predicate_class_membership, test_group_resolution}`) — those pin the M1-batch-1 spec shape (six runes, batch-1 predicate classes) and predate this pass; re-baseline them when the batch-2 migration formally closes. The phase-end re-baseline (2026-07-03) rebuilt the surface in place at HEAD (stale shards cleared first, per the orphaned-shard wart) and re-pinned every review census test to the new build: 15,972 units / 80,990 rows / 20 batches; 10,083 machine-approved / 5,889 human (`boundary-echo` 4,465, `dangling-anchor-dropped` 4,148, `bare-name-live-join` 1,470); secondary seams 1,735 marked / 363 homed / 1,651 homeless / 344 suppressed; verdict families sum 1,149 windows (`seam-loss-withdrawal` 373, `deferred-ss04` 212, `unmatched-misc` 114, `no-chain-gains` 108, `extension-non-summing` 89, `may-utter-gains` 70, `deferred-ss10` 69, `deferred-ss03` 64, `tea-it-xheight` 33, `oy-it-baseline` 17). The drafts tests' worked examples re-point at `boundary-echo` (the class that absorbed `zwnj-word-initial-unification`). A future rune edit again trips the artifact-pinned tests until `run_m1` + `review.build` re-run. Distinguish real regressions by diffing the failure set against this clean-HEAD baseline.
- **`make test`** (main font, `test/ site/`) does not read the rune files; runs green independently (6753 passed).
- **The Manual-pin gate (2026-07-03):** `rebuild/pipeline/manual_pins.py` replays every corpus data-expect pin whose text falls inside the migrated alphabet (17 of 530 today) against the built M1.otf — trait assertions resolve through stance `traits:` in the spec, no waiver channel. Wired twice: a hard gate in `run_m1.main()` between the boundary gate and the oracle (writes `manual_pins_summary.json`; its `top_blocking_letters` is batch-prioritization signal), and `rebuild/test_manual_pins.py` in the rebuild suite. Artifact-pinned like the review census tests: a rune edit trips it until `run_m1` recompiles `rebuild/out/m1/M1.otf`.
- **Space and ZWNJ rows are gone from UNMATCHED as of 2026-07-03** — the ratified boundary-equals-word-boundary rule (see WHATNEXT.md) absorbs every non-position row whose window contains a run-splitting boundary (space or ZWNJ) into the `boundary-echo` ledger class (renamed from `zwnj-boundary-echo` when the space half landed) at top precedence, so per-config UNMATCHED diffs against pre-2026-07-03 audits will show space and ZWNJ windows vanishing; that is the rule working, not a regression. `run_m1.main()` now also runs the boundary-equals-text-edge split-buffer gate (hard-fails if any text renders differently around a space or ZWNJ than its segments do standing alone) before the oracle.
- **Collateral verification harness (this pass):** `rebuild/tools/seam_loss_diff.py <candidate_runes_dir>` re-settles the real runes and a candidate dir under the default config and reports per-group target restoration + any approved-window movement (real collateral). `rebuild/tools/seam_loss_probe.py` is the older shard-comparison probe — prefer the differ; the shard `after` seams are config-shadowed and produce phantom regressions.

## Resume commands

```bash
# Verdict ↔ unit ↔ class/draft join (counts by class, by keypath, by right-neighbor):
python3 - <<'PY'
import json, glob, collections
units={}
for f in glob.glob('rebuild/out/review/units/*.json'):
    for u in json.load(open(f)): units[u['id']]=u
V=json.load(open('verdicts-11.33.00PM.json'))['verdicts']
vbyu={}
for v in V:
    uid=v['unit']
    if uid not in vbyu or v['at']>vbyu[uid]['at']: vbyu[uid]=v
tab=collections.defaultdict(collections.Counter)
for uid,v in vbyu.items():
    u=units.get(uid); cls=u['class'] if u else '<MISSING>'
    tab[cls][v['verdict']]+=1
for cls in sorted(tab, key=lambda c:-sum(tab[c].values())):
    print(f'{cls:40s} {dict(tab[cls])}')
PY

# Per-change settle probe (edit the cases dict to the windows under review):
PYTHONPATH=. uv run python - <<'PY'
from rebuild.review.enrich import load_spec
from rebuild.pipeline.settle import settle, cell_label
spec = load_spec()
cases = {"ZWNJ Tea It Day":[0x200C,0xE652,0xE670,0xE653], "Tea It It":[0xE652,0xE670,0xE670]}
for label, cps in cases.items():
    print(f"{label:24s} -> {[cell_label(spec, s.cell) for s in settle(spec, cps, frozenset())]}")
PY

# Reflow a rune to canonical flow-vs-block formatting after editing:
uv run python tools/reflow_yaml.py glyph_data/runes/qsIt.yaml
```

### Full-rebuild verification (the real arbiter — the settle probe alone is not enough)

The served review surface (`rebuild/out/review/`, port 7294) is built from a **pre-compiled font** (`rebuild/out/m1/M1.otf`) plus **frozen unit shards**, so it never reflects an uncommitted or just-committed rune edit. The settle probe shows cell labels but not the rendered pixels or the oracle match/unmatched verdict. To truly verify a change (and catch divergences-from-old the probe hides), rebuild and diff:

```bash
uv run python -m rebuild.pipeline.run_m1                       # recompile M1.otf from the runes (writes rebuild/out/m1/, ~minutes)
uv run python -m rebuild.review.build --out tmp/review-preview # rebuild the surface non-destructively (leaves :7294 + verdicts untouched)
(cd tmp/review-preview && python3 -m http.server 7295)         # eyeball at http://localhost:7295/#class=...&unit=u-NNNN
```

Gotcha: `run_m1` exits nonzero whenever unmatched rows exist (i.e., always, mid-migration), so `run_m1 && review.build` silently skips the surface rebuild — run the two commands separately and check that `review.build` actually printed its `Wrote …` line before diffing.

Then compare UNMATCHED windows per config, keyed by codepoints (stable across rebuilds), preview vs the served surface:

```bash
python3 - <<'PY'
import json, glob, collections
def load(d):
    us={}
    for f in glob.glob(d+'/units/*.json'):
        for u in json.load(open(f)): us[u['codepoints']]=u
    return us
old, new = load('rebuild/out/review'), load('tmp/review-preview')
def bycfg(us):
    c=collections.Counter()
    for u in us.values():
        for cfg,cc in (u.get('config_classes') or {}).items():
            if cc=='UNMATCHED': c[cfg]+=1
    return c
co, cn = bycfg(old), bycfg(new)
for cfg in sorted(set(co)|set(cn)): print(f'{cfg:16s} {co[cfg]:4d} -> {cn[cfg]:4d}  {cn[cfg]-co[cfg]:+d}')
PY
```

**Lesson banked (2026-06-29):** the **stable old-font connector for a window lives in the config's `before` field, and it varies by config** — reading `before.glyphs` across mixed configs led me to a false "87 regressions" alarm for E1; the per-config rebuild diff is what set it straight (the true number was +1). Always confirm a divergence-from-old with the rebuild diff, per config, not the raw `before` field.

## Keeping this file honest

Update this file in the same change that lands a rune edit: tick the checklist item, note the commit, record any new override or follow-up. `WHATNEXT.md`'s M1 thread links here once the first edit commits.
