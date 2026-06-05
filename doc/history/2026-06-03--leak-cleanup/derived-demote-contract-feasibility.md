# Derived demote contract feasibility memo

This was a read-only spike for deriving the `predecessor_demote_overrides` and `trailing_demote_overrides` triple set from compiled anchor geometry. The oracle lives at `tools/derived_demote_oracle.py`; it writes the row-level partition to `tmp/derived-demote-oracle.txt`.

Run used for this memo:

```sh
uv run python tools/derived_demote_oracle.py
```

The default oracle run builds a temporary Senior font under `tmp/derived-demote-no-authored/` with only the two authored demote tables omitted, shapes all Quikscript letter sequences through depth 4, collects the actual final adjacent glyph pairs, derives non-join demote triples from `joins(left, right) == False`, and compares those derived triples with the healed authored tables. It also runs a separate authored-pair probe that ignores pair discovery and tests only whether the isolated target can be chosen from sibling geometry for each authored row.

## Counts

| Measurement                                              | Predecessor | Trailing | Total |
| -------------------------------------------------------- | ----------: | -------: | ----: |
| Authored rows                                            |         121 |       30 |   151 |
| Derived rows from the no-authored depth-4 pair source    |       2,224 |    1,724 | 3,948 |
| Reproduced authored rows in the full derived-set diff    |          63 |       21 |    84 |
| Missing authored rows in the full derived-set diff       |          58 |        9 |    67 |
| Extra derived rows not in the authored tables            |       2,161 |    1,703 | 3,864 |
| Authored-pair isolated-target probe reproduced           |          67 |       23 |    90 |

Six of the 67 full-diff missing rows are pair-source misses, not isolated-target misses: the authored-pair probe derives the correct isolated target, but the bounded no-authored depth-4 shaping sweep did not surface that exact pair. Those six are `qsExcite.en-y0.ex-y0.before-vertical + qsIt -> qsExcite`, `qsMay.ex-ext-1 + qsFee.ex-y5 -> qsMay`, `qsMay.ex-y0 + qsGay.ex-y0 -> qsMay`, `qsNo.ex-ext-1 + qsFee.ex-y5 -> qsNo`, and the two `qsIt.en-y0.ex-noentry.before-day-exam{,.en-ext-1} + qsDay.half.en-y0.ex-y0 -> qsDay` trailing rows.

The practical upper bound for a pure pairwise geometry contract is therefore 90 of 151 authored rows: the 84 rows reproduced in the full diff plus those six reachability-only rows. The exact row-level subset is in `tmp/derived-demote-oracle.txt` under the reproduced sections plus the six rows above.

## Isolated target derivation

The isolated target is not generally derivable as "the sibling that drops the dangling edge but keeps every load-bearing edge." The authored-pair probe reproduces only 90 of 151 targets.

The failures are not random name-ranking noise. They expose information that pairwise anchor geometry does not contain:

- Same-anchor sibling ambiguity: anchor geometry cannot distinguish targets with the same relevant anchors but different authored identity or bitmap, e.g. `qsExcite.en-y0.ex-y0.before-vertical.after-baseline-letter` should demote to `qsExcite.en-y0.noexit`, but geometry picks bare `qsExcite`; `qsMay.en-y5.ex-y0.after-i` should keep `after-i`, but geometry picks `qsMay.en-y5`; `qsThey...en-ext-1` should keep the entry extension, but geometry picks the non-extended sibling.
- Load-bearing opposite-edge traps: strict pairwise preservation chooses an entry-preserving or exit-preserving sibling when the authored table deliberately drops that opposite edge because wider context proves it is not load-bearing. Examples include `qsGay.en-y0.ex-y5 -> qsGay` rather than `qsGay.ex-y5`, `qsNo.alt.en-y0.ex-y0 -> qsNo` rather than a no-entry `qsNo` sibling, `qsRoe.en-y0.ex-y5 -> qsRoe` rather than `qsRoe.en-ext-1-at-0`, and the `qsDay` / `qsZoo` trailing rows.
- F8 bundled entry+exit nuance: `qsUtter.alt.en-y5.ex-y0.reaches-way-back` must revert all the way to bare `qsUtter`. Pairwise geometry can see that no entry-preserving no-exit sibling exists, but its sibling search picks `qsUtter.noentry`, not the authored bare target. The "bundled entry is emergent, drop the whole form" lesson is not encoded in adjacent anchors.
- Inverted/predecessor-aware edge creation: `qsMay.ex-noentry + qsThey_qsUtter.noentry -> qsMay` has no predecessor exit anchor to demote, and the authored isolated target actually restores bare `qsMay`'s exit. The predicate `exit_ys(left) & entry_ys(right) == empty` cannot derive a row whose source has no exit edge.

## Residual buckets

For the full derived-set diff, the 67 authored-missing rows partition as: 53 predecessor-of-predecessor / predecessor-aware target-choice rows, 13 rows involving ligature trigger forms, 1 inverted/predecessor-aware row, and 0 real-join rows. The row-level list is in `tmp/derived-demote-oracle.txt` under "Missing predecessor" and "Missing trailing."

For the stricter isolated-target question, 61 authored rows need more than pairwise geometry: 60 need predecessor/follower-aware target choice or authored identity beyond anchors, and 1 is the `qsMay.ex-noentry` inverted/predecessor-aware row. The ligature-trigger rows are not evidence that pairwise geometry can un-fuse ligatures; they are ordinary left/right demotes whose settled trigger happens to be a ligature, and their failures still reduce to target choice or pair-source reachability.

This matches the journal's residual lesson: pairwise geometry can only see the adjacent final anchors. It cannot know whether an entry was supplied by a real predecessor, whether a forward exit is still needed by a follower, whether a ligature trigger came from a compose path that requires un-fusing, or whether a non-joining pair is a real connected-text junction that tests intentionally preserve.

## Extra set

The oracle derives 3,864 rows that are not authored. Only 17 of those are live visible depth-4 snapshot signatures. The other 3,847 are not current visible leaks; treating all of them as demotes would be broad overreach.

The 17 live extras are not a clean "authored tables missed these" set. Twelve are the `qsGay.en-y0.ex-y5 -> qsGay` context-dependent cluster, which needs predecessor-of-predecessor awareness to avoid dropping a real incoming join. The two `qsTea.en-y0 + qsDay...` rows are in the real-join / junction class. The remaining three are predecessor-aware or load-bearing-edge traps. All 17 are non-joins according to `joins()`, but that is exactly the point: the non-join predicate alone is insufficient to decide that a demote is safe.

## Decision

No-go for retiring the authored tables with a pure pairwise geometry contract. Anchor geometry can reproduce a useful subset, but it cannot derive the isolated target reliably, and its extra set contains known context-dependent and real-join traps.

Go only for a narrower follow-up: use the oracle as an audit/suggestion tool for the 90-row reproducible subset, then design any real replacement contract with additional context inputs: predecessor-of-predecessor state for load-bearing entries, follower state for load-bearing exits, explicit handling for no-edge-to-bare restoration, and ligature-compose awareness where un-fusing rather than single-glyph demotion is required.
