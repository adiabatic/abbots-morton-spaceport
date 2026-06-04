# Leak-prevention plan: derive a join contract in the emitter

This is an executable brief for the "prevention phase" sketched in `doc/leak-investigation-findings.md`. Read that note first — it has the evidence behind every claim here. The goal of this phase is to make the _dominant_ class of isolation/shaping leaks impossible to emit, by enforcing a derived "join contract" inside the `calt` emitter, with no new YAML authoring surface.

An agent picking this up should be able to execute it phase by phase, stopping at each acceptance gate. Do not skip the gates — the FEA is 14 MB of chained lookups and the only safe way through is small, verified steps.

## What this does and does not achieve

- It DOES make the true single-rule class impossible by construction: "a single emitted rule selects a glyph's exit (or entry) variant with a neighbor it does not cursively join _named in that rule's own adjacent context_." `tools/leak_contract_report.py` measured this directly against the snapshot: 164 of the 387 depth-4 leaks (42%) are this class and are dropped by the contract, plus 14 (4%) that are author-declared cosmetic tucks the contract keeps. The example is `·Ah·Gay·Tea·Oy`: one rule reads `sub […] qsGay' [qsTea_qsOy qsThaw.ex-y0] by qsGay.en-y5.ex-noentry`, naming the non-joining followers in its lookahead, so the contract drops them.
- The 63% figure in `doc/leak-investigation-findings.md` is an over-estimate: it counted a _mechanism cluster_ ("the exit changes for a follower that has no entry"), which conflates the single-rule class above with a second, structurally different class the contract cannot reach — see the dangle note below. The reporting pass replaced that estimate with the measured 42% (+4% cosmetic).
- It does NOT make _all_ leaks impossible, and you must not promise that or chase it. The investigation established that some leaks are _emergent_ across the ~600 chained lookups — they arise from the composition of several rules plus ligature formation, with no single rule (or single form) containing both sides of the leak. 209 of the 387 (54%) measured this way. There are two emergent shapes, and the second is the bulk of them:
  - **The cross-lookup compose.** `·Ah·It | ·Tea·Oy`: there is no rule mentioning both an `It`-form and `qsTea_qsOy`; the leak emerges from a backward `It` upgrade, a separate exit revert, and the `Tea`+`Oy` ligature interacting in sequence. No per-form contract can catch those.
  - **The dangle (the dominant emergent subclass, ~203 of the 209).** The offending exit/entry variant was set by a rule that is itself contract-_compliant_ — it keyed on a class of genuinely joining neighbors (e.g. `@entry_y0`) — and the actual adjacent neighbor was then rewritten _by a later lookup_ into a form that is no longer in that class, leaving the connector dangling. The per-rule join check passes on every rule involved, so the contract is blind to it. Worked example `·Ah·May | ·See·At`: `qsMay.en-y5` becomes `qsMay.en-y5.ex-y0` via `sub qsMay.en-y5' @entry_y0 by qsMay.en-y5.ex-y0`, but the following `qsSee.ex-y0` has no entry at all and so is never in `@entry_y0` — May's baseline exit was set for an earlier y0-entering follower and dangles once `·See` settles into its exit-only form. This is why a per-rule (or per-form) contract tops out near 42%, not 63%: a large slice of "exit changes for a no-entry follower" is a downstream dangle, not a single-rule selection.
- Therefore the depth-4 snapshot gate (`make test-leaks`, `tools/leak_snapshot.py`, `test/isolation-leak-snapshot.txt`) stays in place as the empirical backstop for the residual emergent leaks. The construction-time contract shrinks the snapshot; the snapshot guards what the contract cannot reach. Both are needed.

## The derived join contract

A non-join is `exit_ys(left) & entry_ys(right) == set()` — exactly `_pair_join_ys` in `test/quikscript_shaping_helpers.py`, and `joins()` / `any_join()` in `tools/leak_static_analysis.py` (reuse these; do not re-derive). `JoinGlyph` already exposes the anchor Ys (`tools/quikscript_ir.py`, the `entry` / `entry_curs_only` / `exit` fields and their Y helpers around line 119), so the contract is _derived_, not authored.

The rule the emitter must enforce, for every contextual `calt` substitution that selects a variant `V` of base `B`:

- A _forward_ rule `sub [backtrack] B' [followers] by V` may keep a follower `F` in its match set only if `V` can exit-join `F` — `exit_ys(V) & entry_ys(F) != set()`.
- A _backward_ rule `sub [preds] B' by V` (predecessor-driven) may keep a predecessor `P` only if `P` can exit-join `V` — `exit_ys(P) & entry_ys(V) != set()`.
- Any follower/predecessor that fails the test is a cross-break shape dependency. Drop it from the rule's context (or refuse to emit that rule), unless the cosmetic opt-out below applies.

The contract is symmetric and applies to both the substitution side (`calt`, this brief) and, in spirit, the positioning side (`curs`, `_emit_quikscript_curs` around line 6050) — but scope this phase to `calt` substitution leaks only. The `curs`-anchor leaks (same bitmap, different exit/entry anchor) are a smaller, separate class; note them as follow-up, do not bundle them.

## The cosmetic opt-out (no new YAML)

Some cross-break shape changes are intentional cosmetic tucks the design wants — `qsAt.ex-y0.before-may`, `qsExcite…before-vertical`, and similar. Approach: do _not_ add a YAML flag. Instead, treat the existing `before-<family>` / `after-<family>` modifier on a form as the opt-out signal: a variant `V` whose `modifiers` include `before-<fam>` (resp. `after-<fam>`) is an author-declared cosmetic interaction with that family, so it is _allowed_ to be selected for a non-joining `<fam>` neighbor. Read the modifier set from `JoinGlyph.modifiers` (`tools/quikscript_ir.py:65`).

Every allowed cosmetic interaction must then show up in `test/isolation-leak-snapshot.txt` (it is, by definition, a visible cross-break difference). So after enforcement, the snapshot's remaining entries should be exactly: the cosmetic opt-outs plus the emergent leaks the contract cannot reach. That is the reviewed, enumerated set the investigation doc promised — not a discovery problem.

If you find a cross-break selection that is neither joining nor a `before-X`/`after-X` cosmetic form, it is a bug: either the form should carry the cosmetic modifier (intended) or the selection should be dropped (leak). Surface these to the human; do not guess.

## Prerequisite: make the emitter instrumentable

`_emit_quikscript_calt` (`tools/quikscript_fea.py:2028`) is ~4,000 lines of nested closures sharing outer state, so you cannot test the contract in isolation against it as-is. Before enforcing anything, extract the candidate-selection points into inspectable form. The selection happens in three nested helpers: `_emit_fwd_general` (`:3526`), `_emit_bk_general` (`:4022`), and `_emit_fwd_pairs` (`:3203`); the upstream analysis that decides what each can select is `_analyze_quikscript_joins` (`:157`) and `_populate_exit_reachability` (`:807`).

Acceptance gate for this step: byte-identical FEA. Run `make snapshot-before` on the current commit, do the extraction, run `make all`, then `diff test/before/AbbotsMortonSpaceportSansSenior-Regular.fea test/AbbotsMortonSpaceportSansSenior-Regular.fea` (or `sha1sum` the six OTFs in each directory). Any divergence means the extraction changed behavior — fix it before proceeding. This mirrors the scoped-anchor cleanup protocol in the repo's AGENTS.md.

## Phase 1: classify, warn, validate (no behavior change)

Add the contract as a _reporting_ pass first. At each forward/backward selection point, classify every (base, neighbor, variant) candidate as `joining`, `cosmetic` (the modifier opt-out matches the neighbor family), or `leak`. Emit the `leak` ones as a build warning (a new `UserWarning` subclass alongside the existing `JoinContractWarning` in `tools/quikscript_join_analysis.py`) and dump the full set to a file under `tmp/`.

The cross-check oracle this phase validates against already exists: `tools/leak_contract_report.py` is the standalone, read-only classifier (built on `tools/leak_static_analysis.py`'s parser and `joins()` predicate) that partitions the snapshot into droppable / cosmetic / emergent and dumps the per-row breakdown to `tmp/leak-contract-report.txt`. It changes no FEA bytes because it only reads the built FEA and the snapshot. The in-emitter warn pass you add here must agree with it: same `(base, neighbor, variant)` classification, same droppable set. Run the report first to get the target numbers (currently 164 droppable, 14 cosmetic, 209 emergent of 387), then build the emitter pass to reproduce them from the inside.

Acceptance gate: the warned `leak` set, projected to structural signatures, should be a subset of the current `test/isolation-leak-snapshot.txt`, and should be a _superset_ of `leak_contract_report.py`'s droppable set — not exactly equal. The report classifies each snapshot row by the neighbor's _settled_ form, but the in-emitter pass sees the neighbor's _bare, pre-lookup_ form at the moment the rule fires — which is what the contract actually drops. The triage in `doc/leak-triage.md` found 14 rows (family F1, the `·They`-before-may exit before bare Tea/Gay/It/Day followers) that the report tags `[no-rule]`/emergent because their settled neighbor differs, yet whose emit-time bare neighbor is genuinely non-joining and so droppable. So the correct gate is: in-emitter droppable ⊇ report droppable, and every _extra_ row must be an independently verified emit-time non-join (the F1 fourteen already are). Do not treat the superset as the classifier over-flagging; treat a row in the report's droppable set but _missing_ from the in-emitter set as the real bug. The report still confirms its half: every reachable verdict is a genuine non-join, zero anomalies. This phase changes no FEA bytes.

## Phase 2: enforce

Flip the pass from warn to enforce: drop non-joining, non-cosmetic neighbors from each rule's context (or refuse to emit the rule when its entire context drops out). Keep cosmetic opt-outs.

Acceptance gates, all required:

- `make test` stays green (the depth-3 gate and the whole shaping corpus).
- `make test-leaks` fails with the snapshot _shrinking_ — review the removed signatures; every one should be a genuine improvement (a leak you just made impossible). Expect the snapshot to shrink by _more_ than the report's 164: 164 droppable plus the emit-time-droppable rows the report misses (the 14 F1 rows above, and any others that surface once measured from inside). Budget for that; do not assert "exactly 164 removed." Re-bless with `make leak-snapshot` and commit the shrunken snapshot in the same change.
- `make check-html` and a visual pass on `test/check.html`: confirm the now-suppressed cross-break selections look _better_ (no dangling exits, no false tucks), not worse. If any intended cosmetic tuck disappeared, it was missing its `before-X`/`after-X` modifier — add it (that is the one legitimate YAML edit this phase allows) rather than weakening the contract.
- Spot-check the reactive machinery did not start double-firing: the contract should make some existing guards redundant, but it must not contradict them.

## Phase 3 (optional follow-on): retire the reactive machinery the contract subsumes

Once the contract holds, parts of the hand-curated leak-patching machinery become dead weight: `_PENDING_BK_ENTRY_GUARDS` and `_PENDING_LIGA_ENTRY_GUARDS` (`tools/quikscript_join_analysis.py:92`, `:135`), `_collect_noentry_shape_leak_warnings` (`:476`), and portions of the ZWNJ firewall and strip guards in the emitter. Remove only what the contract provably subsumes, one table at a time, each gated by byte-diff (`make snapshot-before`) plus `make test` plus `make test-leaks`. Do not batch these; a removed guard that was load-bearing will resurface as a snapshot regression, and you want to know exactly which removal caused it.

## Phase 4 (high payoff): the downstream-revalidation (second-order) contract

The per-rule contract of Phases 1–2 cannot reach the _dangle_: a contract-_compliant_ rule sets an exit (or, mirror-image, a backward entry) keyed on a join-class member that genuinely joins when the rule fires; a _later_ lookup then rewrites the actual adjacent neighbor out of that class — into an exit-only form, an entryless `noentry`/`noexit` form, or an entryless ligature (`qsTea_qsOy`, `qsSee_qsEat`, `qsThey_qsZoo`, `qsOut_qsTea`) — and the connector dangles. Every per-rule join check passes, so the per-rule contract is structurally blind. The triage (`doc/leak-triage.md`) measured this against the human verdicts: of the 99 "outright broken" emergent leaks, **75 are dangles reachable by one uniform second-order pass**, 14 are actually Phase-2 per-rule rows (F1), and 10 (family F4) are genuine cross-lookup-compose that no contract reaches.

The pass: after each lookup that rewrites an adjacent neighbor, re-run `exit_ys(left) & entry_ys(right)`; when it goes empty, revert the now-dangling exit/entry to the isolated form. Verifiers confirmed the revert is _safe_ — it restores the exact isolation target and breaks no real join — and that it _subsumes_ existing ad-hoc machinery (`predecessor_demote_overrides` / `calt_pred_demote_qsExcite`, the `qsOut_qsTea` reverts, the class-keyed backward upgrades are all special cases), so Phase 4 grows what Phase 3 can retire. One nuance from family F8: when the offending form bundles an emergent _entry_ upgrade with the dangling _exit_, revert the whole form to bare, not just the exit.

Build and validate it family-by-family in this order — biggest, most tractable, highest-confidence first, each gated by `make test` + `make test-leaks` with the snapshot shrinking:

1. **F2 `·May` baseline-exit (15)** — the canonical `·Ah·May | ·See·At` dangle; prove the mechanism here.
2. **F3 `·Excite` vertical-exit (14)** — second-order machinery already half-exists; the fix is enumerating unlisted demote triples, which de-risks the general pass.
3. **F5a `·They·Zoo` predecessor (11)** — backward entry dangle on a trailing glyph (no follower), so the revert is provably consequence-free; first backward test.
4. **F9 misc backward-entry (9)** — uniform backward dangle, all realized neighbors `exit=[]`; trivially safe revert.
5. **F6 `·Out·Tea` predecessor (6)** — backward entry dangle keyed on `@exit_y0`; mirrors F5a/F9.
6. **F7 `·May`/`·No` predecessor (4)** — same backward shape, smallest clean batch.
7. **F8 `·Utter` reaches-way-back (2)** — smallest, but exercises the bundled entry+exit revert nuance.
8. **F1 `·They` before-may exit (28)** — split: 14 Tea/Gay/It/Day rows go to Phase-2 pruning, 14 See/Thaw rows here. Do last, after the mechanism is proven on F2/F3.

Family F4 (10) gets no contract work — accept it in the snapshot, and keep `·Ah·It | ·Tea·Oy` there as the documented cross-lookup-compose archetype that no contract can reach.

## Validation tooling you already have

- `tools/leak_static_analysis.py` — parses the emitted `calt` into structured rules and has the `joins()` predicate; the cheap oracle for "does this rule select across a non-join?".
- `tools/leak_contract_report.py` — partitions the snapshot into droppable / cosmetic / emergent (the Phase-1 cross-check oracle).
- `tools/leak_enforcement_oracle.py` — predicts the Phase-2 enforcement delta and shows why a blind FEA rewrite misfires (enforcement must be in-emitter).
- `tools/leak_verdict_reconcile.py` + `tools/leak_emergent_families.py` + `doc/leak-emergent-verdicts.txt` — the human triage of the emergent residue and its root-cause families (the Phase-4 work-list); see `doc/leak-triage.md`.
- `tools/leak_snapshot.py` + `test/isolation-leak-snapshot.txt` — the depth-4 ground truth and the regression gate; `make leak-snapshot` re-blesses, `make test-leaks` checks.
- `make snapshot-before` + FEA/OTF diff — the byte-identical equivalence harness for the refactor and retirement steps.

## Definition of done

The single-form "selected across a non-join" class is gone from `test/isolation-leak-snapshot.txt` (Phases 1–2); `make test` and `make test-leaks` are green against the re-blessed snapshot; and the FEA for any non-behavioral step diffs to zero bytes. With Phase 4 also done, the dangle class is gone too, and everything remaining in the snapshot is one of: an author-declared cosmetic tuck (carries a `before-X`/`after-X` modifier), a `doc/leak-emergent-verdicts.txt`-accepted emergent leak (the 110 "either form is fine"), or a cross-lookup-compose leak no contract reaches (family F4). Every residual entry is then either author-declared cosmetic or human-blessed — the enumerated, reviewed set the investigation promised, not a discovery problem.
