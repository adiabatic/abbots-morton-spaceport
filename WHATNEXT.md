# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone plans under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see “Keeping this file honest” at the bottom.

## The main thread — M1 rune migration

**Current state: the qsLow sitting is fully adjudicated and the “Doable anytime” quartet is closed — the depth-3 grammar (nested `then:` chains, capped at two hops past `right`) landed with the orphaned-·Tea fix, the ·Day·Tea de-prioritization records are in qsDay, and the review queue stands at 1 blank (·Day·Tea·No·Tea, the chosen withdraw zigzag awaiting its verdict).** ·Day/·Oy·Tea·Utter·Low renders as the old font again (`y0,break,y0` — the predecessor keeps its exit when ·Utter-then-·Low would veto ·Tea’s forward join), the six ·X·Day·Tea·Utter windows and ·Day·Tea·No·{May,Low} converged to the old font too, and four of the five standing rejects retired with them — the survivor is ·No·No·Utter·Low. ·Day·Tea·No·Utter keeps the ·Day·Tea join per the recorded follower-of-·No scope; its approve-with-note is still worth setting in the app (currently carried as either). Word-initial ·No·Utter·May joins on both sides via ·Utter.alt (commit de17b7f); the tie-breaker is qsUtter’s before-·May follower prefer, so every predecessor that can reach ·Utter.alt at the x-height inherits it (·No is the only migrated letter where the tie arises — ·May and ·Utter already engage on strict gain), a sanctioned taste exception to the “alt-·Utter only before ·Low or on strict gain” principle and to the ·No.alt-on-ties taste (both otherwise enforced by the runes). The conform sweep is exact at 9 letters (271,452 sequences, 0 divergences, full rule and transition coverage — witness derivation is right3-aware now); the oracle stands at 9,406 unmatched verdict-gated taste rows; the boundary and Manual-pin (29/29) gates pass; census pins are clean. The carried verdict master is `rebuild/evidence/verdicts-carried-de17b7f.json` (3,841 carried — the drops are exactly the windows that converged to the old font). `make verdict-ready` — and the same status as a banner in the app — answers whether the surface, gates, and verdict store are ready.

The rebuild is migrating the cursive-join engine rune by rune, validating each batch against the old shipped font via the oracle.

## Deferred and tracked follow-ups

### Doable anytime

Nothing gates these — act on any cycle, whenever it’s worth it.

- **Harmonize the twelve cosmetic echo groups** — the app’s docket view lists them with a “View stacked” button per group, and typing a group id (e.g. `e-0061`) into the cross-class search stacks it directly. All twelve are approve-vs-either splits (harmonizing to the per-group majority — `either` everywhere except e-0112’s `approve` — changes nothing that ships; the minority approves cluster on ·Oy-led windows). Purely optional tidiness; the two substantive groups retired when their windows re-rendered under the ·No·Utter·May tie-breaker and the ·Day·Tea records.
- **·No·Utter·Day** — the identical 2-vs-2 tie to the landed ·No·Utter·May tie-breaker (qsUtter’s before-·May follower prefer), left mono to honor the why’s ·May scope; widen that record’s `right:` to `{family: [qsDay, qsMay]}` if the taste extends.

- **The ·Day·Tea·Utter·Tea orphan family** — ·{Tea,It,Utter}·Day·Tea·Utter·Tea leaves the final ·Tea unjoined: the left-scoped withdraw records carry only the ·Utter-then-·Low carve-out. Widening them to also except ·Utter-then-·Tea fixes those three but mints both-sides ·Utter orphans in ·Day·Tea·Utter·Tea·X one letter out — the two-hop except approximates a truth that needs a third hop, past even the depth-3 window. Needs a deeper mechanism or a deliberate bless; every affected window is length-5/6 and unverdicted.

### Do when a gate takes too long

Not gated on a named event — these come due when a cycle step gets slow enough that acting pays off.

- **Conform-gate horizon** — `gate:conform`, the exhaustive font-vs-settle sweep, runs every cycle at horizon 5 (`--conform-horizon` on the cycle driver, passed through to `run_m1 --conform-only`; `--skip-conform` opts out). The sweep grows with the fifth power of the alphabet (~50 s sharded at today’s 8 migrated letters, ~15 min by ~17, hours beyond), so when a migration batch makes this gate the cycle’s long pole, drop `--conform-horizon` below 5 — witness top-ups keep rule and transition coverage exact at any horizon; only off-corpus diff density shrinks.

### Waiting on a specific migration or milestone

Each is blocked until a named event lands — a particular rune’s migration, or the batch-2 close — and can’t meaningfully start before then.

- **Unresolved design flags** — the inexpressible `reaches_up_and_way_over` resolved-extension scope on qsMay’s x-height entry (needs either a design extension or proof the partners’ own extend records cover it; re-check at the qsAh migration, qsUtter’s half already landed), and the ss05 row-grain refusal coverage notes (re-check at the qsEt migration; the ss04 half went live and conform-verified with qsLow).
- **qsZoo Manual-pin flag** — when qsZoo migrates, the Manual pin `·Tea ~b~ ·May.en-ext-1 ~x~ ·Zoo` will trip the no-waiver Manual-pin gate; re-transcribe the pin or fix the runes at that point.
- **Dormant `contract:` records** — the `contract:` policy records on qsNo/qsUtter/qsTea/qsMay never fired in this batch’s corpus; re-adjudicate them when qsJai/qsZoo/qsJay/qsFee migrate and can actually exercise them.
- **The ·Day·Tea-before-·No follower set** — qsDay’s depth-3 withdraw list ({·Tea, ·May, ·Low}) mirrors where ·No.flipped engages today; when more baseline-entry followers migrate (qsVie, qsFee, qsRoe…), ·No.flipped will engage before them too and the list needs re-adjudication.
- **Batch-1 spec-pin re-baseline** — the rebuild suite fails exactly 4 tests on a clean HEAD by design: `test_surface.py::test_real_cell_bindings_all_match` and `test_spec_load.py`’s `test_loads_all_six_runes` / `test_predicate_class_membership` / `test_group_resolution` pin the M1-batch-1 spec shape (six runes, batch-1 predicate classes), and the artifact-cycle driver subtracts them as its expected `baseline` bucket. Re-baseline them when the batch-2 migration formally closes, and retire the driver’s baseline bucket at the same time. Distinguish real regressions in any rebuild-suite run by diffing the failure set against these four (plus the artifact-pinned census/Manual-pin tests, which go stale after any rune edit until `run_m1` + `review.build` re-run).

## Keeping this file honest

This is a punch list of what’s _left_ — not a changelog of what happened. When work lands, edit the current-state description in place and delete the status it supersedes; never append a dated `Update (…)` paragraph. What got done — with what counts, and under which commits — is already in the git log, so this file records only the live frontier: the open forks, the next batch, and the consciously-parked follow-ups. When a landed change leaves a genuinely-open residue, record the residue as a forward bullet, not the change that produced it. The broader workflow rules — no after-the-fact reports, bounded progress files, evidence that outlives its decision — live in `AGENTS.md`’s “Note-taking and the rebuild logs” section; `AGENTS.md` points back here.
