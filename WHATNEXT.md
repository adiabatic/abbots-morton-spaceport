# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone plans under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see “Keeping this file honest” at the bottom.

## The main thread — M1 rune migration

**Current state: the qsLow batch is in flight — the ·Day·Utter·Low fork is resolved in The Manual’s favor (§5.7 late formation is built), the Manual-pin gate passes at 29/29, and the artifact cycle is unblocked.** Formation now yields per window wherever the unformed trailing component could reach the follower and the formed ligature could not (`settle.formation_blocked`, derived from the runes’ join surfaces with no per-ligature data — a newly migrated letter that only the unformed rendering can reach extends the guard automatically, so The Manual stays satisfied by default), and qsUtter carries the §5.9 follower one-liner (`policy.prefer[2]`, why: pending) so a predecessor withholds its exit when alt-·Utter’s forward baseline reach is realizable. The font-vs-settle conform sweep is exact at 9 letters (271,452 sequences, 0 divergences, full rule and transition coverage); the oracle stands at 10,653 unmatched verdict-gated taste rows — the 6,564 batch-2 leftovers, the fresh qsLow families, and the ·Utter-bearing rows this resolution moved (237 old formation-fork rows now match the shipped font; the additions are the alt-·Utter tie-flips and unformed-chain gains, every one containing ·Utter). The fork resolution is committed and the artifact cycle has re-run at 2f18041 with every gate green; the carried verdict master is now `rebuild/evidence/verdicts-carried-2f18041.json` (2,475 of the prior 2,562 verdicts carried — the 87 that did not re-resolve sit on renders the resolution changed and resurface as blanks). What remains is finishing the review sitting, now in flight: the carried master (2,475) and echo fill (419) are imported and an early sitting landed 93 fresh verdicts, whose ·Utter.alt principle (recorded on u-9111's verdict note) is folded into two pending import files — `verdicts-flip-utter-alt-swap.json` (flips the sitting's 33 approvals on pure one-join swaps to reject) and `verdicts-bulk-utter-alt-swap-blanks.json` (rejects the 30 matching blanks) — leaving a 927-blank queue through the app's live docket view (`#view=docket` on the review server, recomputed from the verdict store as verdicts land — no re-bakes; undo path `tmp/verdicts-undo-before-corrections.json`).

The rebuild is migrating the cursive-join engine rune by rune, validating each batch against the old shipped font via the oracle.

## Deferred and tracked follow-ups

### Doable anytime

Nothing gates these — act on any cycle, whenever it’s worth it.

- **Settle the 14 disagreeing echo groups** — the app’s docket view lists them with a “View stacked” button per group, and typing a group id (e.g. `e-0061`) into the cross-class search stacks it directly. 13 carry split imported history; e-0061 holds the qsLow sitting’s 21 fresh rejects against 6 imported eithers and 1 approve.

### Do when a gate takes too long

Not gated on a named event — these come due when a cycle step gets slow enough that acting pays off.

- **Conform-gate horizon** — `gate:conform`, the exhaustive font-vs-settle sweep, runs every cycle at horizon 5 (`--conform-horizon` on the cycle driver, passed through to `run_m1 --conform-only`; `--skip-conform` opts out). The sweep grows with the fifth power of the alphabet (~50 s sharded at today’s 8 migrated letters, ~15 min by ~17, hours beyond), so when a migration batch makes this gate the cycle’s long pole, drop `--conform-horizon` below 5 — witness top-ups keep rule and transition coverage exact at any horizon; only off-corpus diff density shrinks.

### Waiting on a specific migration or milestone

Each is blocked until a named event lands — a particular rune’s migration, or the batch-2 close — and can’t meaningfully start before then.

- **qsUtter `policy.prefer[2]` narrowing** — the qsLow sitting’s rejects mark the §5.9 follower prefer as over-firing where alt-·Utter merely moves the one realizable join to the other neighbor (see u-9111’s verdict note for the principle, and u-9099’s for the wrinkle that a realizable non-alt x-height join should beat an alt baseline gain). Narrow it once the sitting closes and the reject set is final; a `when:`-scoped variant is already structurally rejected (E-AMBIGUOUS), so the fix needs a derived-guard-style mechanism like `settle.formation_blocked`. The record’s `why:` is still pending and is the user’s to write.
- **Unresolved design flags** — the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay’s x-height entry (needs either a design extension or proof the partners’ own extend records cover it; re-check at the qsAh migration, qsUtter’s half already landed), and the ss05 row-grain refusal coverage notes (re-check at the qsEt migration; the ss04 half went live and conform-verified with qsLow).
- **qsZoo Manual-pin flag** — when qsZoo migrates, the Manual pin `·Tea ~b~ ·May.en-ext-1 ~x~ ·Zoo` will trip the no-waiver Manual-pin gate; re-transcribe the pin or fix the runes at that point.
- **Dormant `contract:` records** — the `contract:` policy records on qsNo/qsUtter/qsTea/qsMay never fired in this batch’s corpus; re-adjudicate them when qsJai/qsZoo/qsJay/qsFee migrate and can actually exercise them.
- **Batch-1 spec-pin re-baseline** — the rebuild suite fails exactly 4 tests on a clean HEAD by design: `test_surface.py::test_real_cell_bindings_all_match` and `test_spec_load.py`’s `test_loads_all_six_runes` / `test_predicate_class_membership` / `test_group_resolution` pin the M1-batch-1 spec shape (six runes, batch-1 predicate classes), and the artifact-cycle driver subtracts them as its expected `baseline` bucket. Re-baseline them when the batch-2 migration formally closes, and retire the driver’s baseline bucket at the same time. Distinguish real regressions in any rebuild-suite run by diffing the failure set against these four (plus the artifact-pinned census/Manual-pin tests, which go stale after any rune edit until `run_m1` + `review.build` re-run).

## Keeping this file honest

This is a punch list of what’s _left_ — not a changelog of what happened. When work lands, edit the current-state description in place and delete the status it supersedes; never append a dated `Update (…)` paragraph. What got done — with what counts, and under which commits — is already in the git log, so this file records only the live frontier: the open forks, the next batch, and the consciously-parked follow-ups. When a landed change leaves a genuinely-open residue, record the residue as a forward bullet, not the change that produced it. The broader workflow rules — no after-the-fact reports, bounded progress files, evidence that outlives its decision — live in `AGENTS.md`’s “Note-taking and the rebuild logs” section; `AGENTS.md` points back here.
