# Policy round 2 report: the verdict round resolves an engine limitation

One-line verdict: the 29 round-2 rejects are all one phenomenon — ·May's `en-ext-1` baseline-entry extension — and an exhaustive, build-tested lever hunt proves no closed-vocabulary policy record can faithfully revert any of them without collateral. Round 2 therefore authors **no rune records**: it re-statuses two ledger entries to record the firm reject signal and the build proof, leaves the font byte-for-byte unchanged, and surfaces one feature-scoped engine task. Nothing is committed; the user approves all commits.

## Inputs

- Verdicts: `verdicts-08.19.24PM.json` (repo root, gitignored) — 539 verdicts: 440 approve, 59 either, 29 reject, 3 neither, 8 identical, 0 skip. Re-exported clean to `rebuild/evidence/review-triage-round2.yaml` (pins 440, policy_edits 29, any_of 59, neither 3, identical 8; rows covered 2604).
- Baseline HEAD: `15a33d3`. Old Senior Sans OTF byte-identity gate SHA-256 `3211a7a76be0e3c032c06eead1dace2d5cbf4f05c63a9a742c23c3117625cf35`.
- Pre-edit control audit: `rebuild/evidence/round2-control-audit.tsv` (15,525 divergent rows, oracle 0 unmatched / 0 multi-matched), reproduced byte-for-byte by the scratch-build harness `rebuild/tools/scratch_build.py`.

### Three pre-round decisions, all resolved before this round

1. **u-0223 / u-0224** (· ◊ZWNJ ·Tea·Oy) — approved. The narrower underline is the faithful `qsTea_qsOy` ligature (600u vs two separate letters at 700u); no policy record involved.
2. **u-0243 / u-0244 / u-0245 / u-0250** (·May before ·Tea·Oy) — approved-as-improvement; the "·May should use the loop stance" note is logged as a future `qsMay` stance item, not a round-2 action.
3. **163 `may-exit-withdrawal-generalized` units** — the user verdicted the whole class (302 units: 243 approve, 52 either, 2 neither, 5 identical, 0 reject, 0 skip). Settled taste, no action.

## The 29 rejects are one phenomenon

The handoff framed the rejects as two independently fixable seams (a ·Pea·May join seam and an ·It·May extension seam). Build inspection of the audit overturns that framing: **every reject is the same `en-ext-1` cell delta**, and the partition is 15 / 12 / 2, not the handoff's guessed 13 / 14 / 2.

| Bucket | Count | Window shape | Ledger class | What old vs new differ by |
| --- | --- | --- | --- | --- |
| pea-may | 15 | ·X·Pea·May·Tea under ss03 (incl. the u-0398 `qsTea_qsOy` ligature lead and u-0400 ·May·Pea·May·Tea) | halves-entry-extension-restored | new ·May carries `en-ext-1 + ex-ext-1` (summed); old dropped the entry one |
| it-may | 12 | [X]·Oy·It·May | halves-entry-extension-restored | new ·May carries `en-ext-1` on its restored ·It→·May baseline entry; old had none |
| same-seam | 2 | u-0417 ·Pea·May·It·May, u-0437 ◊ZWNJ ·May·It·May | same-seam-extension-non-summing | new sums the ·It→·May seam extension where the old font did not |

In every case the seam topology is identical between old and new; the sole delta is the `+1px` `en-ext-1` token on ·May, produced by `qsMay.policy.extend[3]` (the loop's baseline entry extension, which fires whenever ·May's immediate-left exits at the baseline). This is precisely the phenomenon round 1 documented as `halves-entry-extension-restored` residue (1) and (2) and surfaced as an open question; round 2's re-triage answers that question firmly toward reject.

The round-1 "contradiction" (rejects on ·Pea·May·Tea against approvals of the identical ·May cell) is now understood and is not a contradiction: the approved siblings — u-0393 ·Pea·May·Tea·Tea, u-0468 — are the cases where ·May pulls back (`ex-bind-pulled-back`) and never composes an `ex-ext-1`, so they carry no summed extension. The user rejects the _summing_, and keeps the single extension.

## The exhaustive lever hunt: no closed-vocabulary revert exists

To test whether the firm rejects can be honored in the frozen `when:` policy vocabulary, every candidate record was build-tested in isolation. The harness `rebuild/tools/scratch_build.py` builds the M1 pipeline against a scratch copy of `glyph_data/runes/` into a scratch out-dir and runs the full oracle; it was validated to reproduce `rebuild/evidence/round2-control-audit.tsv` byte-for-byte. **25 candidate records across 5 independent lever families were scratch-built. Zero cleared the strict success bar** (target reject reverts to old ink, oracle 0 unmatched / 0 defects, zero collateral on any non-target window).

| Lever family | Candidates | Best outcome |
| --- | --- | --- |
| qsMay right/self-scoped suppression | 4 | a competing more-specific `extend` builds 0-collateral / 0-unmatched but re-emits `en-ext-1` unchanged |
| extend-narrowing + non-summing | 3 | narrowing reverts the target but drops `en-ext-1` from 791–1190 approved rows |
| qsPea/qsTea predecessor-exit (seam 1) | 6 | **c6** reverts all pea-may targets to old ink with a perfectly clean oracle (0 unmatched, 0 defects) — but irreducibly strips the approved ·Pea·Pea·May·Tea |
| qsIt two-side (seam 2) | 7 | cannot separate reject ·Oy·It·May from the ink-identical approved ·Oy·It·May·Tea |
| ligature + same-seam | 5 | only `en-ext-1` suppressor is non-summing, which keys on the left predecessor's exit only |

Verdict tally across the 25: 6 revert-but-break-oracle, 10 revert-but-collateral, 7 no-revert, 2 schema-invalid, **0 clean-lever**.

### Why it is structurally impossible

Two walls, both build-confirmed:

1. **A depth-2 left-context predicate the grammar cannot express.** For pea-may, ·May's immediate-left predecessor is byte-identical (qsPea, full stance, baseline exit, vertical stroke, `joined_at` baseline) in the bare reject _and_ in the approved ·Pea·Pea·May·Tea — they diverge only in the predecessor's _own_ entry (unentered vs entered-at-y6). The closed `leftCondition` exposes `family/class/stance/joined_at/stroke/is/except`; it has no axis for the predecessor's own left seam (`joined_at` reads the predecessor→·May seam, not the predecessor's left seam), and `then` is legal only on the right. For it-may the missing context is one position further still (the predecessor's predecessor, ·Oy), and the reject ·Oy·It·May is ink-identical to the approved ·Oy·It·May·Tea but for the 4th glyph. The best candidate (c6) is the proof: it is the only record that reverts and keeps the oracle clean, and it _still_ cannot carve out the approved ·Pea·Pea·May·Tea.
2. **The token grammar makes `en-ext-1` un-droppable by any authored record.** An `extend` always emits the token (a competing, more-specific `extend` re-emits it — built: 0 collateral, 0 revert); a `contract` coexists with the matched `extend` rather than netting it (round 1's "do not net at name grain", reproduced here as 51 unmatched); a `refuse` removes the whole ·Pea→·May / ·It→·May cell, collapsing the join rather than dropping the token. The single mechanism that ever None-s a matched extend is the hard-coded same-seam non-summing rule (`settle.py`), which fires only on the _left predecessor's_ exit extension — never on ·May's own right/self context, so it cannot reach the summed-self ss03 case at all.

## Disposition

No rune records authored. `glyph_data/runes/` is untouched, so the new-pipeline shaping, the divergence audit, and all six fonts are unchanged, and the old Senior Sans OTF stays byte-identical to the gate SHA automatically.

Two ledger entries in `rebuild/m1-divergences.yaml` gain a round-2 paragraph (status, predicate, counts, and exemplars unchanged — there is no shaping delta):

- `halves-entry-extension-restored` (stays reviewed-rejected, count 160): records the firm round-2 reject of the 27 pea-may + it-may windows and the build-proven engine limitation.
- `same-seam-extension-non-summing` (stays reviewed-approved, count 225): records the re-confirmed u-0417 / u-0437 rejects as engine-limited residue alongside the 27.

## The one engine decision surfaced

Honoring all 29 verdicts collapses to a single feature-scoped settlement-engine task for a future milestone:

> Extend the same-seam non-summing rule so it also suppresses ·May's entry-side `en-ext-1` when ·May's _own_ exit already carries the seam pixel (the ss03 "summed-self" pea-may case), **and** add a depth-2 left-context predicate so the it-may windows ([X]·Oy·It·May) can be distinguished from their ink-identical approved siblings (·Oy·It·May·Tea). Both are out of scope for a closed-vocabulary policy round.

The alternative the user may instead choose is to keep the composed extension everywhere and retire the rejects as accepted drift. That is a taste call left open, not a defect.

## Acceptance gates

- Oracle: 0 unmatched / 0 multi-matched, 15,525 divergent rows; `divergence-audit.tsv` byte-identical to `rebuild/evidence/round2-control-audit.tsv` (no shaping change).
- Defect gates (§9, E-): green.
- All 440 approved pins, 59 either windows, 3 neither, 8 identical: unchanged (no edit can move them).
- Old Senior Sans OTF: byte-identical to `3211a7a7…` (round 2 edits only `rebuild/` text and the report; nothing feeds the old pipeline).
- `uv run pytest rebuild/ -n auto --dist worksteal`, `make test`: green (no pinned counts move — no shaping change).
- Review surface (`rebuild/out/review/`): unchanged; the after-font SHA is identical, so no regeneration is needed and the verdicts re-import against the same surface.
- Nothing committed.

## Files changed

- `rebuild/m1-divergences.yaml` — round-2 paragraphs on the two ledger entries above (prose only).
- `rebuild/POLICY-ROUND-2-REPORT.md` — this report.
- `tmp/` scratch (gitignored): `scratch_build.py` (the build harness), `round2-control-audit.tsv` (control), `review-triage-round2.yaml` (regenerated export), workflow scripts.
