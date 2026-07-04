# qsNo (E666) oracle triage — findings

Scope: every unmatched window whose codepoints contain E666, drawn from `C_withdrawal+loss`, `C2_withdrawal`, `E_seam-loss`, `D2_gain+loss`, `D_seam-gain`. 282 distinct window-shapes (non-ss10; ss10 explicitly out of scope). Default-config seam deltas computed against the live settlement via `tmp/no_audit.py` and confirmed config-stable in the baseline subset.

## Ground-truth tables for old-font qsNo (config-stable across default/ss02/ss03/ss04/ss05/ss02+ss03/ss02+ss03+ss05)

### qsNo stance selection — chosen by the PREDECESSOR's exit height
- Predecessor exits at **baseline (y0)** -> No goes **flipped** (`qsNo.alt.en-y0.ex-y0`), entering at baseline. Triggers: `qsDay`, `qsIt.ex-y0`/`qsIt.en-y0.ex-y0`, `qsOy`, a preceding **flipped** `qsNo`, `qsTea.ex-y0`, `qsPea.noentry`, `qsUtter.alt…reaches-way-back`, `qsVie`, `qsTea_qsOy` — i.e. `joined_at: baseline`.
- Predecessor exits at **x-height (y5)** or No is word-initial -> No stays **loop** (`qsNo`, x-height entry/exit). Triggers: `qsMay`, plain `qsUtter` (mono), `qsTea.half.ex-y5`, `qsPea.half.ex-y5`, a preceding **loop** No, word boundary.

### LOOP (x-height) EXIT toward Y — receiver-gated, but CONTEXT-DEPENDENT on No's own predecessor
| follower | seam | note |
|---|---|---|
| Day E653 | y5 | join |
| No  E666 | y5 | join |
| It  E670 | y5 | join |
| Oy  E679 | **y5 OR break** | joins after word-initial/after-No lead; **breaks after May/Utter lead** |
| May, Tea, Utter, Pea | break | |

`qsNo|qsOy`=y5, `qsNo|qsNo|qsOy`=y5,y5, but `qsMay|qsNo|qsOy`=y5,**break** and `qsUtter|qsNo|qsOy`=y5,**break**. The loop-No·Oy x-height join is dropped by the old greedy cascade whenever No itself received an x-height join from May/Utter on its left. A flat receiver-gate cannot express this (methodology learning #1).

### FLIPPED (baseline) EXIT toward Y — CONTEXT-DEPENDENT on the follower's own forward join
| follower | seam | note |
|---|---|---|
| Tea E652 | **y0 OR break** | **joins when Tea has no live forward join** (Tea-final, or Tea before Pea/Tea which break); **breaks when Tea joins rightward** (Tea before Day/It/No/Utter) — Tea's single baseline anchor goes to its right neighbor instead |
| Day E653 | y0 | join |
| May E665 | y0 | join |
| No  E666 | y0 | join |
| It  E670 | y0 | join |
| Utter E67A | y0 | join |
| Oy  E679 | **break** | flipped-No never joins Oy |
| Pea E650 | break | |

`qsDay|qsNo.alt|qsTea`=y0,**y0** (join) but `qsDay|qsNo.alt|qsTea|qsDay`=y0,**break**,y0 (No·Tea dropped, Tea·Day kept). Same join count, the old cascade gives Tea's baseline anchor to whichever neighbor "wins" the floor.

### FLIPPED baseline ENTRY from X — predecessors that exit baseline (y0)
Day, It (ex-y0 variants), Oy, flipped-No, Tea (ex-y0), Pea (noentry/ex-y0), Utter (reaches-way-back), Vie, Tea_qsOy. The current rune lists `[qsDay, qsVie, qsIt, qsOy, qsNo, qsUtter, qsTea_qsOy]` — **qsTea and qsPea are absent**, but they only summon flipped-No when they themselves exit baseline (the ZWNJ/word-boundary cases), and adding them flat regresses the 3-letter Tea·No·May case (see disposition 3).

## Disposition table (282 windows grouped by the No-seam delta)

| group | count | core shape | OLD -> NEW (No seam) | disposition |
|---|---|---|---|---|
| A | 52 | `X·No·Tea(·…)` flipped-No declines Tea | No·Tea y0 => break | **FIX** (add qsTea to flipped exit toward) — pure for Tea-final / Tea-before-Pea/Tea; regroups the 13 Tea-before-{Day,It,No,Utter} shapes |
| A-entry | 6 | `[ZWNJ/Oy]·Tea/Pea·No·May/Utter` | Tea/Pea·No y0 => break | FIX needs scoped/context selector — flat entry-from widening regresses 3-letter Tea·No·May (over-join); see disposition 3 |
| B | 43 | `[May/Utter/No/It]·No·Oy` loop-No·Oy | No·Oy break => y5 | **VERDICT-GATED** (gained x-height join; old cascade dropped it) |
| C | 17 | `It·No·Oy` It restructures, No loop | It·No y0=>y5 + No·Oy break=>y5 | **VERDICT-GATED** (It joins both sides; topology flip) |
| D1 | 16 | `Utter·No·May` | No·No/Utter·No y5=>y0, No·May break=>y0 | **VERDICT-GATED** (No-chain flips to baseline to reach May) |
| D2 | 16 | `Utter·No·Utter` | y5=>y0, break=>y0 | **VERDICT-GATED** (same, reaching Utter) |
| D3 | 15 | `No·No·May` | No·No y5=>y0, No·May break=>y0 | **VERDICT-GATED** |
| D4 | 15 | `No·No·Utter` | y5=>y0, break=>y0 | **VERDICT-GATED** |
| E | 15 | `Oy·It·No` | It·No y0=>y5 (loop) | **VERDICT-GATED / cross-area** (Oy·It gained; No-loop is downstream of It's stance) |
| F | 53 | various 3-4 letter chains | No seam MATCHES OLD | cell-grain only (withdrawal/ext/`lig`-token) -> existing classes (`may-exit-withdrawal-generalized`, etc.) or neighbor areas (qsDay_qsUtter, qsUtter, qsMay) — NOT a qsNo fix |
| misc | ~34 | singleton 4-letter chains | mixed | mostly No-flips-to-baseline cascades (VERDICT-GATED) or cross-area entry losses; a few No·May withdrawal losses route to qsMay's reviewed classes |

## Disposition 1 — No·Tea seam-loss (group A): FIX with a caveat

OLD `qsDay|qsNo.alt.en-y0.ex-y0|qsTea.en-y0` = y0,**y0**; NEW = y0,**break** (No declines Tea, pulls back). The flipped exit toward list omits qsTea. Adding `{family: qsTea}` makes Day·No·Tea = y0,y0 (verified by probe). This is ink-faithful: OLD draws a real baseline connecting stroke into `qsTea.en-y0`.

Caveat (methodology #1): empirically, adding flat `{family: qsTea}`:
- fixes the 42 Tea-final / Tea-before-boundary shapes (288 rows) cleanly;
- fixes Tea-before-Pea and Tea-before-Tea (Tea·Y breaks anyway in OLD, so No·Tea joins in both) cleanly;
- but **regroups** the 13 shapes `X·No·Tea·{Day,It,No,Utter}` (91 rows): OLD = y0,break,y0 (Tea→Y wins); NEW with the fix = y0,**y0**,break (No→Tea wins, Tea→Y dropped). Same join count, different seam. Tea's `never:[baseline,baseline]` pairing forces the single anchor to one side; the floor picks No·Tea over Tea·Y. These 91 rows would then classify as `regrouping-floor-drift` (reviewed-REJECTED).

So the fully faithful fix is a yielding prefer on No's flipped exit (yield the No·Tea join when Tea has a live forward baseline join) — not expressible as a flat family list. Recommendation: add `{family: qsTea}` as the high-value majority fix (288 rows), and either (a) accept the 91-row regrouping as same-join-count drift, or (b) author a yielding prefer. Confidence MEDIUM (the flat add is correct in direction and net-positive, but not pixel-pure on the 91 follower rows).

## Disposition 2 — qsOy is a dead/inaccurate entry in the flipped exit toward list: faithfulness cleanup

Truth: flipped-No·Oy = **break** in every context. The NEW font never uses No's flipped exit to join Oy (verified: zero `qsNo/flipped/…/baseline/|qsOy` y0 seams in the audit) because the prefer always routes Oy-followers through the loop. `{family: qsOy}` in `flipped.exits.baseline.toward` is therefore dead AND contradicts the truth table. Removing it is seam/ink-faithful and changes no shaping. Confidence HIGH (no shaping change; pure accuracy cleanup). Verify with the byte-identical FEA/OTF diff per CLAUDE.md before committing.

## Disposition 3 — Tea/Pea·No baseline-entry loss (group A-entry): scoped selector needed

`200C·Tea·No·May` and `Oy·Tea·No·May`: OLD = break,**y0**,y0 (Tea exits baseline -> flipped-No enters baseline). NEW = break,**break**,y0 (No flipped but its entry-from omits Tea/Pea). Adding qsTea/qsPea to the flipped entry-from `from` (already `joined_at: baseline`) fixes Oy·Tea·No·May and ZWNJ·Tea·No·May (verified), but **regresses the 3-letter `Tea·No·May`**: NEW then makes Tea choose its baseline exit to chase 2 joins (Tea·No + No·May) where OLD kept Tea at x-height (Tea·No y5, No·May break = 1 join). The join-maximizer over-joins. ZWNJ·Pea·No·May stays broken regardless (the `qsPea.full.locked` twin carries no baseline exit — a Pea-locked issue, not a No issue). So this needs the same yielding/scoped treatment as disposition 1, not a flat entry-from add. Confidence LOW for a flat fix.

## Disposition 4 — verdict-gated No-chain restructurings (groups B, C, D, E, misc)

These are the engine's join-maximizer making real new visual joins the old greedy cascade did not:
- **No-chain flips to baseline to reach trailing May/Utter** (D1-D4): `No·No·May`, `No·No·Utter`, `Utter·No·May`, `Utter·No·Utter` — OLD y5,break (1 join) vs NEW y0,y0 (2 joins). Same family as the round-2 "No goes flipped to reach trailing May."
- **loop-No·Oy gains the x-height join** (B): `May·No·Oy`/`Utter·No·Oy` OLD y5,break vs NEW y5,y5.
- **It joins both sides** (C, E): `It·No·Oy`, `Oy·It·No` — It restructures, No falls to loop.

Each is a genuine taste call (improvement vs regression) for the user, exactly like the round-1/round-2 verdict passes. None is a scope bug. The underlying mechanism — yields-to-joins join-count maximization vs old greedy cascade — is the documented `regrouping-floor-drift` / `*-join-gains` territory; a flat scope edit cannot revert them without repealing the maximizer.

## Cross-area note (group F + misc)

53+ windows have the No seam CORRECT; the unmatched delta is cell-grain on a neighbor (qsMay `ex-bind-pulled-back`/`en-ext-1`/`ex-ext-1`, qsDay_qsUtter `lig`-token vs resolved seam, qsUtter flipped). These route to `may-exit-withdrawal-generalized`, `same-seam-extension-non-summing`, `halves-entry-extension-restored`, or the qsDay_qsUtter/qsUtter areas — out of qsNo's scope.
