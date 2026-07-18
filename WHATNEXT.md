# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone plans under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see “Keeping this file honest” at the bottom.

## The main thread — M1 rune migration

**Current state: the qsLow batch is in flight — the sitting’s verdicts are adjudicated into the runes, and the review queue stands at 576 blanks (418 once the queued imports below land).** The ·Day·Utter·Low fork stays resolved in The Manual’s favor (§5.7 late formation, `settle.formation_blocked`), and qsUtter’s §5.9 follower one-liner (`policy.prefer[2]`) is now scoped `when: {right: {family: qsLow}}` per the sitting’s recorded verdict-note principle: alt-·Utter engages only before ·Low (The Manual’s pin) or where it strictly gains a connection, so the ~370 rejected one-join swaps render mono again and the 16 rejected ·X·Utter·Tea join losses are back — the record’s `why:` is stubbed `TODO`, text pending, the user’s to write. ·Day now yields before ·Tea-then-·Low so ·Tea joins forward into ·Low, and full-·Tea takes the ss03 x-height entry ungated after ·Low, ·Day·Utter, and now mono-·Utter (the u-9101 full-bar ruling, carried in the flip file below), so the full bar shows even word-finally. The font-vs-settle conform sweep is exact at 9 letters (271,452 sequences, 0 divergences, full rule and transition coverage); the oracle stands at 8,700 unmatched verdict-gated taste rows; the boundary and Manual-pin (29/29) gates pass. The artifact cycle has re-run over these edits with every gate green; the carried verdict master is now `rebuild/evidence/verdicts-carried-0835753.json` (3,161 of 3,195 verdicts carried — the 34 drops are exactly the ·Utter ~x~ ·Tea windows whose render the ruling flipped, re-queued as blanks). What remains is finishing the sitting through the app’s live docket view (`#view=docket`, recomputed from the verdict store as verdicts land — no re-bakes): import `rebuild/evidence/verdicts-carried-0835753.json`, then `verdicts-echo-fill.json` (145 fills), then `verdicts-flip-utter-tea-full-bar.json` (13 approves executing the ruling on the previously-verdicted windows the echo fill doesn’t reach), then work the remaining blanks — mostly the full-bar render changes this ruling produced plus the qsLow-sitting residue. `make verdict-ready` — and the same status as a banner in the app — answers whether the surface, gates, and verdict store are ready.

The rebuild is migrating the cursive-join engine rune by rune, validating each batch against the old shipped font via the oracle.

## Deferred and tracked follow-ups

### Doable anytime

Nothing gates these — act on any cycle, whenever it’s worth it.

- **Settle the 14 disagreeing echo groups** — the app’s docket view lists them with a “View stacked” button per group, and typing a group id (e.g. `e-0061`) into the cross-class search stacks it directly. All 14 are now mild approve-vs-either splits (plus one approve-vs-neither) in carried history; the sitting’s reject-vs-either conflict retired when its windows left the surface.
- **The ·It exit-extension-before-·Low fork** — the qsLow sitting split on the identical `+1` (12 dislikes, 55 approvals, sibling windows on both sides), and no expressible `when:` scope separates them. Settle it in a dedicated extensions-only pass, then either add `{family: qsLow}` to the `except` list of qsIt’s baseline exit-extension record or bless the extension and clear the dislikes.
- **·Utter residue from the qsLow sitting** — three small leftovers the prefer scoping deliberately does not decide: 4 “old way nicer” rejects on windows where alt genuinely adds a join, 3 before-·Low tie-rejects standing against 5 sibling approvals plus the Manual pin, and the 2-window ·Tea orphaning in ·Day/·Oy·Tea·Utter·Low where the predecessor withdraws on an optimistic ·Tea→·Utter prospect the scoped prefer then vetoes.

### Do when a gate takes too long

Not gated on a named event — these come due when a cycle step gets slow enough that acting pays off.

- **Conform-gate horizon** — `gate:conform`, the exhaustive font-vs-settle sweep, runs every cycle at horizon 5 (`--conform-horizon` on the cycle driver, passed through to `run_m1 --conform-only`; `--skip-conform` opts out). The sweep grows with the fifth power of the alphabet (~50 s sharded at today’s 8 migrated letters, ~15 min by ~17, hours beyond), so when a migration batch makes this gate the cycle’s long pole, drop `--conform-horizon` below 5 — witness top-ups keep rule and transition coverage exact at any horizon; only off-corpus diff density shrinks.

### Waiting on a specific migration or milestone

Each is blocked until a named event lands — a particular rune’s migration, or the batch-2 close — and can’t meaningfully start before then.

- **Unresolved design flags** — the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay’s x-height entry (needs either a design extension or proof the partners’ own extend records cover it; re-check at the qsAh migration, qsUtter’s half already landed), and the ss05 row-grain refusal coverage notes (re-check at the qsEt migration; the ss04 half went live and conform-verified with qsLow).
- **qsZoo Manual-pin flag** — when qsZoo migrates, the Manual pin `·Tea ~b~ ·May.en-ext-1 ~x~ ·Zoo` will trip the no-waiver Manual-pin gate; re-transcribe the pin or fix the runes at that point.
- **Dormant `contract:` records** — the `contract:` policy records on qsNo/qsUtter/qsTea/qsMay never fired in this batch’s corpus; re-adjudicate them when qsJai/qsZoo/qsJay/qsFee migrate and can actually exercise them.
- **Batch-1 spec-pin re-baseline** — the rebuild suite fails exactly 4 tests on a clean HEAD by design: `test_surface.py::test_real_cell_bindings_all_match` and `test_spec_load.py`’s `test_loads_all_six_runes` / `test_predicate_class_membership` / `test_group_resolution` pin the M1-batch-1 spec shape (six runes, batch-1 predicate classes), and the artifact-cycle driver subtracts them as its expected `baseline` bucket. Re-baseline them when the batch-2 migration formally closes, and retire the driver’s baseline bucket at the same time. Distinguish real regressions in any rebuild-suite run by diffing the failure set against these four (plus the artifact-pinned census/Manual-pin tests, which go stale after any rune edit until `run_m1` + `review.build` re-run).

## Keeping this file honest

This is a punch list of what’s _left_ — not a changelog of what happened. When work lands, edit the current-state description in place and delete the status it supersedes; never append a dated `Update (…)` paragraph. What got done — with what counts, and under which commits — is already in the git log, so this file records only the live frontier: the open forks, the next batch, and the consciously-parked follow-ups. When a landed change leaves a genuinely-open residue, record the residue as a forward bullet, not the change that produced it. The broader workflow rules — no after-the-fact reports, bounded progress files, evidence that outlives its decision — live in `AGENTS.md`’s “Note-taking and the rebuild logs” section; `AGENTS.md` points back here.
