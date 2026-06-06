# Leak triage: reconciling the 209 verdicts with the join contract

This is the durable record the prevention plan (`doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md`) promised: the reviewed, enumerated partition of the 387 depth-4 isolation leaks into “the contract erases it”, “the design wants it”, and “a human looked at it and said keep / fix”. It is generated from `site/isolation-leak-snapshot.txt`, the human verdicts in `doc/history/2026-06-03--leak-cleanup/leak-emergent-verdicts.txt`, and the read-only analysis in `tools/leak_contract_report.py`. Read the prevention plan first for the contract’s definition and the dangle vs cross-lookup-compose distinction; the phase amendments these findings imply are folded into it.

## The partition (measured, independently confirmed)

`tools/leak_contract_report.py` and a separate all-rules enforcement oracle agree exactly on the split of the 387 snapshot leaks:

| Class                           |   Count | Who handles it                                                              |
| ------------------------------- | ------: | --------------------------------------------------------------------------- |
| Droppable (single-rule)         |     164 | The per-rule join contract erases these by construction. No verdict needed. |
| Cosmetic (author-declared tuck) |      14 | The contract keeps these; they carry a `before-X` / `after-X` modifier.     |
| Emergent (across lookups)       |     209 | The contract cannot reach. These are the human-triaged residue below.       |
| **Total**                       | **387** |                                                                             |

Predicted post-contract snapshot: 387 − 164 = **223** entries (14 cosmetic + 209 emergent).

## The 209 verdicts reconcile 1:1 with the emergent set

Every one of the 209 human verdicts lands on an emergent signature; none spill onto a droppable or cosmetic row, and no emergent row lacks a verdict. So the contract erases exactly the rows nobody had to look at, and the verdicts cover exactly the rows it cannot reach.

| Verdict bucket                                             |   Count | Disposition                       |
| ---------------------------------------------------------- | ------: | --------------------------------- |
| `in context is outright broken`                            |      99 | Actionable backlog (see taxonomy) |
| `in context is OK, but halves-shaped-separately is better` |      51 | Accept — keep in snapshot         |
| `in context is just better than halves-shaped-separately`  |      45 | Accept — keep in snapshot         |
| custom (“both fine” / “look the same”)                     |      14 | Accept — keep in snapshot         |
| **Total**                                                  | **209** |                                   |

All 14 custom verdicts turned out to be “both forms are fine / they look the same” — none were YAML directives (“these letters should never join”). So the emergent residue is **99 broken** (a real backlog of visible leaks the contract provably cannot reach) and **110 accepted** (either form is fine, or one is merely preferable but the other is acceptable).

## The contract must live in the emitter, not as an FEA rewrite

A standalone oracle applied the contract predicate to all 12,598 Quikscript `sub` rules (guarding `space` / ZWNJ / punctuation, which are boundary context and never join neighbors). Scoped to the snapshot rows it reproduces the clean 164 droppable with zero inconsistencies. But applied blindly to every rule’s nearest context position it would drop 28,614 neighbors and empty 11,308 rule positions across 91% of all `sub` rules — obviously wrong; it would gut the `calt`.

The lesson, with numbers behind it: most non-joining neighbors in a rule’s context are incidental or positional, not the selection-_driving_ join target. The contract is only correctly scoped when it knows _why_ a variant was selected — which the emitter knows inside `_emit_fwd_general` / `_emit_bk_general` / `_emit_fwd_pairs`, and a post-hoc FEA pass does not. This is hard evidence for the plan’s insistence on in-emitter enforcement (Phase 0’s extraction is the real prerequisite, not optional cleanup).

## Root-cause taxonomy of the 99 broken (emergent) leaks

A workflow of 19 agents (one characterizer + one adversarial verifier per family, plus synthesis) traced each family at the rule + anchor-Y level. Every family’s “is this a genuine non-join” and “dangle vs cross-lookup-compose” verdict was independently recomputed by a second agent trying to refute it. Two refutations changed the original analysis (F1 and F4, below).

| Family                       | Rows | Leak type                           | Contract reach             | Remedy                      | Confidence |
| ---------------------------- | ---: | ----------------------------------- | -------------------------- | --------------------------- | ---------- |
| F1 `·They` before-may exit   |   28 | dangle                              | 14 per-rule / 14 2nd-order | Phase 2 pruning + 2nd-order | medium     |
| F2 `·May` baseline-exit      |   15 | dangle                              | second-order               | second-order contract       | high       |
| F3 `·Excite` vertical-exit   |   14 | dangle                              | second-order               | second-order contract       | high       |
| F5a `·They·Zoo` predecessor  |   11 | dangle                              | second-order               | second-order contract       | high       |
| F4 left entry-revert         |   10 | mixed (compose; 6 dangle / 4 cosm.) | none                       | accept in snapshot          | high       |
| F9 misc backward-entry       |    9 | dangle                              | second-order               | second-order contract       | high       |
| F6 `·Out·Tea` predecessor    |    6 | dangle                              | second-order               | second-order contract       | high       |
| F7 `·May`/`·No` predecessor  |    4 | dangle                              | second-order               | second-order contract       | high       |
| F8 `·Utter` reaches-way-back |    2 | dangle                              | second-order               | second-order contract       | high       |
| **Total**                    |   99 |                                     |                            |                             |            |

By leak type: **89 dangle, 10 mixed/compose** (all of F4). By contract reach and remedy:

| Reach        | Rows | Remedy                                           | Families                                      |
| ------------ | ---: | ------------------------------------------------ | --------------------------------------------- |
| per-rule     |   14 | Phase-2 lookahead pruning (already planned)      | F1 (the Tea/Gay/It/Day half)                  |
| second-order |   75 | a downstream-revalidation contract (new Phase 4) | F1 See/Thaw (14), F2, F3, F5a, F9, F6, F7, F8 |
| none         |   10 | accept in snapshot                               | F4                                            |

### The dominant mechanism: the dangle (75 of 99)

Seven-and-a-half families share one mechanism, and it is the plan’s `·Ah·May | ·See·At` worked example generalized: a contract-_compliant_ rule sets an exit (or, mirror-image, a backward entry) keyed on a join-class member that genuinely joins at the time the rule fires; a _later_ lookup then rewrites the actual adjacent neighbor out of that class — into an exit-only form, an entryless `noentry`/`noexit` form, or an entryless ligature (`qsTea_qsOy`, `qsSee_qsEat`, `qsThey_qsZoo`, `qsOut_qsTea`) — and the connector dangles. Every per-rule join check passes, so the per-rule contract is blind to it. The fix is uniform: re-run `exit_ys(left) ∩ entry_ys(right)` after each neighbor-rewriting lookup and revert the now-dangling exit/entry to the isolated form. Verifiers checked the revert is _safe_ in F2/F5a/F6/F7/F8/F9 — it restores the exact isolation target and breaks no real join (F8’s verifier even confirmed the `·Fee·Utter` join it might seem to break is itself emergent).

### Two verifier corrections that change the plan

- **F1 is not uniformly out of reach — it splits 14/14.** The original analysis put all 28 `·They`-before-may rows in “second-order, contract cannot reach.” The verifier refuted this: `tools/leak_contract_report.py` tags these `[no-rule]` because it keys on the _settled_ neighbor, but the contract acts at _emit time_ on the _bare_ follower in rules 2293/2299. For 14 rows (followers Tea/Gay/It/Day) the bare follower already has `entry_ys` of `{}` or `{5}` when the rule fires, so Phase-2 lookahead pruning drops it and the offending exit is never selected. Only the 14 See/Thaw rows (bare follower enters y0, demoted later) are genuinely second-order. **Consequence: Phase 2 reaches more than the report’s 164 droppable — the report under-counts because it classifies by settled neighbor, not emit-time neighbor.**
- **F4 is cross-lookup-compose, not a dangle.** The verifier relabeled all 10 (the mechanism is the findings doc’s canonical `·Ah·It | ·Tea·Oy`: no single rule couples the offending form to the non-joining follower; the upgrade is set by a backward predecessor-keyed rule and suppressed by author-intentional follower-keyed `ignore` guards). This does _not_ change the remedy — still accept-in-snapshot — but F4 should be the worked example for the compose archetype, kept distinct from the dangles.

## Re-plan

The contract direction is sound and the gates hold, but this triage forces three amendments — Phase 1’s acceptance gate loosened to a superset, Phase 2 sized to shrink the snapshot by more than 164, and a new Phase 4 second-order downstream-revalidation contract that catches the 75 reachable broken rows. Those amendments are folded into `doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md` (its Phase 1, Phase 2, and the new Phase 4); read them there as the executable sequence. The one-line summary: F2 → F3 → F5a → F9 → F6 → F7 → F8 → F1 for the Phase-4 build order, with F4 left in the snapshot as the cross-lookup-compose archetype.

After Phases 2 and 4, the depth-4 snapshot’s residual should be: the 14 cosmetic tucks, the 110 accepted emergent leaks, the 10 F4 compose leaks, and any genuinely-unreachable tail — every entry either author-declared cosmetic or human-blessed. That is the enumerated, reviewed set the investigation promised.

## Reproducing this

- `uv run python tools/leak_contract_report.py` — the 164 / 14 / 209 partition.
- `uv run python tools/leak_verdict_reconcile.py` — the verdict ↔ class join, the 1:1 emergent check, and the 99 / 110 bucket split.
- `uv run python tools/leak_enforcement_oracle.py` — the blind all-rules enforcement sizing and the snapshot cross-check.
- `uv run python tools/leak_emergent_families.py` — the 99 broken grouped into the root-cause families.

The human verdicts are in `doc/history/2026-06-03--leak-cleanup/leak-emergent-verdicts.txt`, keyed by verbatim `site/isolation-leak-snapshot.txt` signature.
