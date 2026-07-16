# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone plans under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see “Keeping this file honest” at the bottom.

## The main thread — M1 rune migration

**Current state: the artifact cycle has rebuilt the surface onto the current runes — the fresh surface (manifest `2026-07-16T05:39:37Z`, 15,960 units, 950 echo groups) carries all 2,562 verdicts with zero units still needing fresh ones, and the carried master is now `rebuild/evidence/verdicts-carried-7d6cc45.json`, checked in as the sole surviving copy of the human decisions (import it into the review app at the next sitting).** The two x-height-halves deletion forks (`no-xheight-entry-extension-dropped`, `may-ligature-seam-loosened`) carry `no_verdict: true`, so they are adjudicated wholesale and never reach the human queue. Census pins are current and the rebuild suite is at exactly its four documented baseline failures. The Junior-equivalence oracle runs inside `review.build`, so ss10-only units carry `junior_equivalent` and never reach the human queue.

The rebuild is migrating the cursive-join engine rune by rune, validating each batch against the old shipped font via the oracle. The migration is between batches — the quiet window the boundary-keyed work below was parked for — and everything parked for this window has landed and is now surfaced on the fresh surface above.

## Deferred and tracked follow-ups

### Doable anytime

Nothing gates these — act on any cycle, whenever it’s worth it. (None open right now.)

### Do when a gate takes too long

Not gated on a named event — these come due when a cycle step gets slow enough that acting pays off.

- **Conform-gate horizon** — `gate:conform`, the exhaustive font-vs-settle sweep, runs every cycle at horizon 5 (`--conform-horizon` on the cycle driver, passed through to `run_m1 --conform-only`; `--skip-conform` opts out). The sweep grows with the fifth power of the alphabet (~50 s sharded at today’s 8 migrated letters, ~15 min by ~17, hours beyond), so when a migration batch makes this gate the cycle’s long pole, drop `--conform-horizon` below 5 — witness top-ups keep rule and transition coverage exact at any horizon; only off-corpus diff density shrinks.

### Waiting on a specific migration or milestone

Each is blocked until a named event lands — a particular rune’s migration, or the batch-2 close — and can’t meaningfully start before then.

- **Unresolved design flags** — the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay’s x-height entry (needs either a design extension or proof the partners’ own extend records cover it when qsAh/qsUtter migrate), and the ss05/ss04 row-grain refusal coverage notes. Re-check at the qsEt/qsDay/qsLow/qsUtter migration.
- **qsZoo Manual-pin flag** — when qsZoo migrates, the Manual pin `·Tea ~b~ ·May.en-ext-1 ~x~ ·Zoo` will trip the no-waiver Manual-pin gate; re-transcribe the pin or fix the runes at that point.
- **Dormant `contract:` records** — the `contract:` policy records on qsNo/qsUtter/qsTea/qsMay never fired in this batch’s corpus; re-adjudicate them when qsJai/qsZoo/qsJay/qsFee migrate and can actually exercise them.
- **Batch-1 spec-pin re-baseline** — the rebuild suite fails exactly 4 tests on a clean HEAD by design: `test_surface.py::test_real_cell_bindings_all_match` and `test_spec_load.py`’s `test_loads_all_six_runes` / `test_predicate_class_membership` / `test_group_resolution` pin the M1-batch-1 spec shape (six runes, batch-1 predicate classes), and the artifact-cycle driver subtracts them as its expected `baseline` bucket. Re-baseline them when the batch-2 migration formally closes, and retire the driver’s baseline bucket at the same time. Distinguish real regressions in any rebuild-suite run by diffing the failure set against these four (plus the artifact-pinned census/Manual-pin tests, which go stale after any rune edit until `run_m1` + `review.build` re-run).

## Keeping this file honest

This is a punch list of what’s _left_ — not a changelog of what happened. When work lands, edit the current-state description in place and delete the status it supersedes; never append a dated `Update (…)` paragraph. What got done — with what counts, and under which commits — is already in the git log, so this file records only the live frontier: the open forks, the next batch, and the consciously-parked follow-ups. When a landed change leaves a genuinely-open residue, record the residue as a forward bullet, not the change that produced it. The broader workflow rules — no after-the-fact reports, bounded progress files, evidence that outlives its decision — live in `AGENTS.md`’s “Note-taking and the rebuild logs” section; `AGENTS.md` points back here.
