# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone plans under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see “Keeping this file honest” at the bottom.

## Open forks in the road

### Fork: the round-2 extension rejects — engine work or accept the drift

Round 2 found that all 29 reviewer rejects are a single phenomenon — ·May’s `en-ext-1` baseline-entry extension — and an exhaustive, build-tested lever hunt (25 candidate records across 5 independent lever families, every one scratch-built against a harness proven byte-identical to the oracle; zero cleared the bar) proved that **no closed-vocabulary policy record can honor them**. The wall is a depth-2 left-context predicate the frozen `when:` grammar cannot express (the predecessor’s own entry state for the ss03 “pea-may” windows; the predecessor’s predecessor for the “it-may” windows), compounded by a token grammar in which `en-ext-1` is un-droppable by any `extend` (always re-emits it), `contract` (coexists without netting), or `refuse` (collapses the whole join).

The two paths:

- **Engine work** — extend the same-seam non-summing rule so it also fires on ·May’s _own_ summed exit (the ss03 “pea-may” case), and add a depth-2 left-context predicate (the “it-may” case). Touches `rebuild/pipeline/settle.py`, the rune schema, and tests. The 29 rejects then become honorable. Tracked below as the extension-suppression milestone.
- **Accept the drift** — keep the composed extension everywhere and retire the 29 rejects as taste-accepted divergence.

**Current lean (2026-06-12): accept the drift and postpone the engine work**, on the expectation that a more capable model will make the engine change cheaper and safer to attempt later. The drift is fully documented and recoverable: the 27 + 2 windows are recorded as engine-limited residue in the two reviewed ledger entries (`halves-entry-extension-restored`, `same-seam-extension-non-summing` in `rebuild/m1-divergences.yaml`), the proof and structural analysis live in those two ledger entries. The lever-hunt evidence is the workflow at `rebuild/evidence/lever-hunt-wf.js` and its result `rebuild/evidence/wf2-result.json`; the build harness that produced it is `rebuild/tools/scratch_build.py` (parameterized `load_spec(runes_dir, ...)` build into a scratch out-dir).

To revisit: re-read those two `rebuild/m1-divergences.yaml` ledger entries and the lever-hunt workflow (`rebuild/evidence/lever-hunt-wf.js`), then decide engine-vs-drift. If engine, the work is the extension-suppression milestone below.

## The main thread — M1 rune migration

**Current state: the verdict-application phases are committed; the immediate work is finishing the blessing adjudication.** Phase 5 and the SS03-MAYTEA full-·Tea-bar follow-up are committed, and their commit-time artifact cycles have run. That cycle is now mechanized as one command — `make artifact-cycle ARGS='--verdicts <master>.json'`, driven by `rebuild/tools/artifact_cycle.py`. **~1,337 units remain to verdict** on the current review surface (manifest `2026-07-04T20:22:20Z`). Resume by importing `verdicts-carried-final2.json` into the served surface — serve it with `uv run python -m rebuild.review.serve` on port 7294 — and adjudicate the remaining units, with the ssNN buckets still deferred.

The rebuild is migrating the cursive-join engine rune by rune, validating each batch against the old shipped font via the oracle. The immediate next work is finishing the ~1,337-unit blessing adjudication on the current surface; once those verdicts are recorded and applied, the rune migration resumes with the next batch.

## Deferred and tracked follow-ups

- **Extension-suppression engine milestone** — the round-2 fork’s engine path: a feature-scoped settlement change (self-exit non-summing + a depth-2 left-context predicate). Parked pending the fork decision above.
- **Ink-comparator widening** — there are now 8 `identical` verdicts sitting on pixel-identical dangling-exit windows (round 1 had 3: u-0401, u-0414, u-0415). This is the evidence pile for widening the ink comparator so dangling-exit-only deltas auto-classify as machine-approved instead of landing in the human review queue.
- **qsMay loop stance before ·Tea·Oy** — round-2 decision #1’s logged note: u-0243/u-0244/u-0245/u-0250 were approved as an improvement, but the user flagged that ·May should arguably use the loop stance there. A future `qsMay` stance item, not a blocker.
- **Unresolved design flags** — the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay’s x-height entry (needs either a design extension or proof the partners’ own extend records cover it when qsAh/qsUtter migrate), and the ss05/ss04 row-grain refusal coverage notes. Re-check at the qsEt/qsDay/qsLow/qsUtter migration.
- **qsZoo Manual-pin flag** — when qsZoo migrates, the Manual pin `·Tea ~b~ ·May.en-ext-1 ~x~ ·Zoo` will trip the no-waiver Manual-pin gate; re-transcribe the pin or fix the runes at that point.
- **Dormant `contract:` records** — the `contract:` policy records on qsNo/qsUtter/qsTea/qsMay never fired in this batch’s corpus; re-adjudicate them when qsJai/qsZoo/qsJay/qsFee migrate and can actually exercise them.
- **qsTea `full` ductus wording** — the third `full` ductus bullet in `qsTea.yaml` says “when the next letter joins ·Tea at the baseline”; it arguably should say “can join,” since the bare capability gate also draws the full bar before non-joining followers.
- **Dead emitted rules under ss10** — SS10-FORM left `conform_summary`’s `uncovered_rules` at 26 (up from 6): dead emitted rules, not a behavior bug — a candidate for an emitter dead-rule-pruning cleanup.
- **·Utter.alt x-height entry — open residues.** Two residues left for a fresh decision: the u-6554 ·No·Utter·May·{Day,No} windows don’t move (the ·No-decision tie needs a separate qsNo lever), and word-final / before-·Pea pairs stay unjoined (the alternate stance’s `require: [exit]` keeps the alt selectable only when it continues into a follower).
- **ss03 full-size ·Tea when a baseline-joiner follows — LANDED** (committed `a60d780`; FT1 in `rebuild/VERDICT-APPLICATION-PROGRESS.md`) — under ss03 an x-height exiter joins ·Tea, which takes its full-size bar (exiting at the baseline) when a baseline-joiner follows and the half stub otherwise.
- **·Pea·Pea chains into ·No.alt — LANDED** (2026-07-04, PPN1 in `rebuild/VERDICT-APPLICATION-PROGRESS.md` Phase 5) — the y6-entered second ·Pea now continues into alternate ·No at the baseline.
- **An ·Utter.alt that accepts an x-height entry — LANDED** (2026-07-04, UALT1 in `rebuild/VERDICT-APPLICATION-PROGRESS.md` Phase 5) — ·Utter·Utter·X, ·No·Utter·Tea, and the ·X·Utter·No·Utter chains now reach the backwards-reaching alt at the x-height.

## Keeping this file honest

This is a punch list of what’s _left_ — not a changelog of what happened. When work lands, edit the current-state description in place and delete the status it supersedes; never append a dated `Update (…)` paragraph. What got done — with what counts, and under which commits — is already in the git log, so this file records only the live frontier: the open forks, the next batch, and the consciously-parked follow-ups. When a landed change leaves a genuinely-open residue, record the residue as a forward bullet, not the change that produced it. The broader workflow rules — no after-the-fact reports, bounded progress files, evidence that outlives its decision — live in `AGENTS.md`’s “Note-taking and the rebuild logs” section; `AGENTS.md` points back here.
