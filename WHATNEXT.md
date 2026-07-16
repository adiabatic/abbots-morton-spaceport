# What next

The living punch list for the Abbots Morton Spaceport rebuild — the forward-looking companion to the per-milestone plans under `rebuild/`. It records what ought to happen next, the open forks in the road, and the deferred work we have consciously parked. Keep it current: see “Keeping this file honest” at the bottom.

## The main thread — M1 rune migration

**Current state: the qsLow batch is in flight — `glyph_data/runes/qsLow.yaml` (a hapax rune) is authored and wired into the alphabet/aliases/contact-allow, the font-vs-settle conform sweep is exact at 9 letters (271,452 sequences, 0 divergences, full rule coverage), and the oracle is triaged down to verdict-gated taste rows (8,761 unmatched: the 6,564 batch-2 leftovers plus ~2,064 fresh qsLow rows that join the same verdict families, plus the formation-fork rows below).** The carried verdict master remains `rebuild/evidence/verdicts-carried-7d6cc45.json` (import it into the review app at the next sitting); census pins and the review surface still describe the pre-qsLow state because the artifact cycle is parked on the fork below.

**Open fork — ·Day·Utter·Low late formation (blocks the batch close).** The Manual pin `·Day | ·Utter.alt ·Low` (site/the-manual.html:1721) trips the no-waiver Manual-pin gate: the old font suppresses the qsDay_qsUtter ligature before ·Low because ·Utter pre-flips to its alt stance to reach ·Low at the baseline, but the new model forms ligatures unconditionally (`doc/rebuild-design.md` §5.7 verified exactly this as safe — ·Day·Utter·Low is now a live counterexample). The 133 matching oracle windows are the same phenomenon. Two ways out, both design decisions: implement the §5.7 “late formation” contingency (designed but unbuilt; the design doc names it as “where the design bends first”), or re-transcribe the pin to ratify the new rendering (`qsDay_qsUtter | qsLow`). Until decided, `run_m1` stops at the pin gate and the artifact cycle (surface rebuild + carry + census re-pin) cannot run; the oracle/conform stages were run directly and are current.

The rebuild is migrating the cursive-join engine rune by rune, validating each batch against the old shipped font via the oracle.

## Deferred and tracked follow-ups

### Doable anytime

Nothing gates these — act on any cycle, whenever it’s worth it. (None open right now.)

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
