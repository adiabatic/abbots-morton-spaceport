# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone reports under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see "Keeping this file honest" at the bottom.

## Open forks in the road

### Fork: the round-2 extension rejects — engine work or accept the drift

Round 2 (`rebuild/POLICY-ROUND-2-REPORT.md`) found that all 29 reviewer rejects are a single phenomenon — ·May's `en-ext-1` baseline-entry extension — and an exhaustive, build-tested lever hunt (25 candidate records across 5 independent lever families, every one scratch-built against a harness proven byte-identical to the oracle; zero cleared the bar) proved that **no closed-vocabulary policy record can honor them**. The wall is a depth-2 left-context predicate the frozen `when:` grammar cannot express (the predecessor's own entry state for the ss03 "pea-may" windows; the predecessor's predecessor for the "it-may" windows), compounded by a token grammar in which `en-ext-1` is un-droppable by any `extend` (always re-emits it), `contract` (coexists without netting), or `refuse` (collapses the whole join).

The two paths:

- **Engine work** — extend the same-seam non-summing rule so it also fires on ·May's _own_ summed exit (the ss03 "pea-may" case), and add a depth-2 left-context predicate (the "it-may" case). Touches `rebuild/pipeline/settle.py`, the rune schema, and tests. The 29 rejects then become honorable. Tracked below as the extension-suppression milestone.
- **Accept the drift** — keep the composed extension everywhere and retire the 29 rejects as taste-accepted divergence.

**Current lean (2026-06-12): accept the drift and postpone the engine work**, on the expectation that a more capable model will make the engine change cheaper and safer to attempt later. The drift is fully documented and recoverable: the 27 + 2 windows are recorded as engine-limited residue in the two reviewed ledger entries (`halves-entry-extension-restored`, `same-seam-extension-non-summing` in `rebuild/m1-divergences.yaml`), the proof and structural analysis are in `rebuild/POLICY-ROUND-2-REPORT.md`, and the verdict pile is `verdicts-08.19.24PM.json`. The lever-hunt evidence is the workflow at `tmp/lever-hunt-wf.js` and its result `tmp/wf2-result.json`; the build harness that produced it is `tmp/scratch_build.py` (parameterized `load_spec(runes_dir, ...)` build into a scratch out-dir).

To revisit: re-read `rebuild/POLICY-ROUND-2-REPORT.md` ("Why it is structurally impossible"), then decide engine-vs-drift. If engine, the work is the extension-suppression milestone below.

## The main thread — M1 rune migration

**In progress (paused at a checkpoint): the qsDay + qsUtter + qsNo batch.** All four rune files (qsDay, qsUtter, qsNo, and the qsDay_qsUtter ligature) plus the qsIt Q2 fix and the alphabet/alias/contact wiring are authored; all hard gates are green; the oracle is down to **2,535 unmatched rows (from 6,189)** after the 2026-06-13 triage session, with zero regressions. The ss10 isolation overlay (−2,823, intended), the qsDay_qsUtter ligature receivers, and the ss04 entered-·It over-extension are resolved as fix-to-match. **The remaining 2,535 (645 unique windows) are verdict-gated** — new joins the engine makes that the old font did not — and the decision (2026-06-13) is to **checkpoint, then build the round-3 review surface and present every family fresh** (no fast-tracking the look-alikes of approved classes). Resume from **`rebuild/M1-BATCH2-PROGRESS.md`** — it now has the verdict-family table, the session's resolved-vs-residue split, the known-pending wiring tests (not regressions), the methodology learnings, and the re-run/probe commands. Nothing is committed.

The rebuild is migrating the cursive-join engine rune by rune and validating each batch against the old shipped font via the oracle (`rebuild/M1-REPORT.md`). The next batch (M1-REPORT §11.3) is **qsDay + qsUtter, plus qsNo or qsFee**:

- **qsDay + qsUtter** — dominate the deferred-partner list (§4.6) and adjudicate the two recorded expressiveness questions (qsIt's ss04 before-·Utter stance nulling inherited derives — the closed vocabulary has no negated-feature condition; the trait-qualified group widening in `utter-pass-through-vetoes`).
- **qsNo** — exercises prefer competition (the design's worked prefer examples, §5.2/§5.3).
- **qsFee** — exercises the `bind:` contract at settlement level.

Together qsNo and qsFee retire the two largest unexercised-semantics blocks in §7 with real records instead of synthetic fixtures.

## Deferred and tracked follow-ups

- **Extension-suppression engine milestone** — the round-2 fork's engine path: a feature-scoped settlement change (self-exit non-summing + a depth-2 left-context predicate). Parked pending the fork decision above.
- **Ink-comparator widening** — there are now 8 `identical` verdicts sitting on pixel-identical dangling-exit windows (round 1 had 3: u-0401, u-0414, u-0415). This is the evidence pile for widening the ink comparator so dangling-exit-only deltas auto-classify as machine-approved instead of landing in the human review queue.
- **qsMay loop stance before ·Tea·Oy** — round-2 decision #1's logged note: u-0243/u-0244/u-0245/u-0250 were approved as an improvement, but the user flagged that ·May should arguably use the loop stance there. A future `qsMay` stance item, not a blocker.
- **Unresolved design flags** (M1-REPORT §11.4) — the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay's x-height entry (needs either a design extension or proof the partners' own extend records cover it when qsAh/qsUtter migrate), and the ss05/ss04 row-grain refusal coverage notes. Re-check at the qsEt/qsDay/qsLow/qsUtter migration.

## Round 2 — status

Closed (`rebuild/POLICY-ROUND-2-REPORT.md`). Two tracked files changed — `rebuild/m1-divergences.yaml` (ledger prose) and the report — with no rune or font change; all gates green (oracle 0 unmatched / 0 multi-matched, `make test` 6753 passed, old Senior Sans OTF byte-identical by construction). Uncommitted pending the user's approval as of 2026-06-12.

## Keeping this file honest

This is a living document. Update it in the same change whenever you finish a migration batch, resolve a fork in the road, surface new deferred work, or close out a milestone report under `rebuild/`. `AGENTS.md` points here.
