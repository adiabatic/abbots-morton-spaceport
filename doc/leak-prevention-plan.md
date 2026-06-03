# Leak-prevention plan: derive a join contract in the emitter

This is an executable brief for the "prevention phase" sketched in `doc/leak-investigation-findings.md`. Read that note first — it has the evidence behind every claim here. The goal of this phase is to make the _dominant_ class of isolation/shaping leaks impossible to emit, by enforcing a derived "join contract" inside the `calt` emitter, with no new YAML authoring surface.

An agent picking this up should be able to execute it phase by phase, stopping at each acceptance gate. Do not skip the gates — the FEA is 14 MB of chained lookups and the only safe way through is small, verified steps.

## What this does and does not achieve

- It DOES make the dominant single-form class impossible by construction: "a glyph's exit (or entry) variant is selected because of a neighbor it does not cursively join." That was 245 of the 387 depth-4 leaks (63%).
- It does NOT make _all_ leaks impossible, and you must not promise that or chase it. The investigation established that some leaks are _emergent_ across the ~600 chained lookups — they arise from the composition of several rules plus ligature formation, with no single rule (or single form) containing both sides of the leak. The worked example is `·Ah·It | ·Tea·Oy`: there is no rule mentioning both an `It`-form and `qsTea_qsOy`. No per-form contract can catch those.
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

Acceptance gate: the warned `leak` set, projected to structural signatures, should be a subset of the current `test/isolation-leak-snapshot.txt`, and should account for the bulk of its single-form (non-emergent) entries. Cross-check with the prototype: `tools/leak_static_analysis.py` already parses the FEA and has the `joins()` predicate; use it to confirm the classifier agrees with what the emitted rules actually do. If the warned set contains signatures _not_ in the snapshot, the classifier is over-flagging — reconcile before enforcing. This phase changes no FEA bytes.

## Phase 2: enforce

Flip the pass from warn to enforce: drop non-joining, non-cosmetic neighbors from each rule's context (or refuse to emit the rule when its entire context drops out). Keep cosmetic opt-outs.

Acceptance gates, all required:

- `make test` stays green (the depth-3 gate and the whole shaping corpus).
- `make test-leaks` fails with the snapshot _shrinking_ — review the removed signatures; every one should be a genuine improvement (a leak you just made impossible). Re-bless with `make leak-snapshot` and commit the shrunken snapshot in the same change.
- `make check-html` and a visual pass on `test/check.html`: confirm the now-suppressed cross-break selections look _better_ (no dangling exits, no false tucks), not worse. If any intended cosmetic tuck disappeared, it was missing its `before-X`/`after-X` modifier — add it (that is the one legitimate YAML edit this phase allows) rather than weakening the contract.
- Spot-check the reactive machinery did not start double-firing: the contract should make some existing guards redundant, but it must not contradict them.

## Phase 3 (optional follow-on): retire the reactive machinery the contract subsumes

Once the contract holds, parts of the hand-curated leak-patching machinery become dead weight: `_PENDING_BK_ENTRY_GUARDS` and `_PENDING_LIGA_ENTRY_GUARDS` (`tools/quikscript_join_analysis.py:92`, `:135`), `_collect_noentry_shape_leak_warnings` (`:476`), and portions of the ZWNJ firewall and strip guards in the emitter. Remove only what the contract provably subsumes, one table at a time, each gated by byte-diff (`make snapshot-before`) plus `make test` plus `make test-leaks`. Do not batch these; a removed guard that was load-bearing will resurface as a snapshot regression, and you want to know exactly which removal caused it.

## Validation tooling you already have

- `tools/leak_static_analysis.py` — parses the emitted `calt` into structured rules and has the `joins()` predicate; the cheap oracle for "does this rule select across a non-join?".
- `tools/leak_snapshot.py` + `test/isolation-leak-snapshot.txt` — the depth-4 ground truth and the regression gate; `make leak-snapshot` re-blesses, `make test-leaks` checks.
- `make snapshot-before` + FEA/OTF diff — the byte-identical equivalence harness for the refactor and retirement steps.

## Definition of done

The single-form "selected across a non-join" class is gone from `test/isolation-leak-snapshot.txt`; everything remaining there is either an author-declared cosmetic tuck (carries a `before-X`/`after-X` modifier) or a documented emergent leak; `make test` and `make test-leaks` are green against the re-blessed snapshot; and the FEA for any non-behavioral step diffs to zero bytes. Stop there — the emergent residue is the snapshot gate's job, not this contract's.
