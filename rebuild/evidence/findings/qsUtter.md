# Oracle triage — qsUtter (E67A) as a letter

Scope: unmatched windows containing E67A, excluding the qsDay_qsUtter ligature's own behavior (lig-containing windows, `E653:E67A` adjacency — owned by the qsDay agent) and ss10 (out of scope).

IMPORTANT: the shipped `rebuild/out/m1/divergence-audit.tsv` (Jun 13 00:39) is **stale** relative to the current `conform.py` + ledger. Re-classifying every E67A row with the live code shows **1201 genuinely-unmatched rows / 321 windows** in my scope (the stale audit said 2067 rows). The `zwnj-word-initial-seam-moved` class (added since the audit) already absorbs the simple post-ZWNJ ·Utter·May seam-moves. All counts below are from the **live** re-classification.

## Old-font truth tables for qsUtter (derived from baseline subset, all configs)

### qsUtter mono (default; baseline entry, x-height exit)

Bare `qsUtter` x-height exit (Rseam):

| Follower | OLD seam | Note |
|---|---|---|
| qsPea | y5 | mono x-height → Pea.en-y5 |
| qsDay | y5 | mono x-height → Day |
| qsNo | y5 | mono x-height → No.loop |
| qsIt | y5 | mono x-height → It.en-y5 |
| qsOy | y5 | mono x-height → Oy.en-y5 |
| qsTea | **break** (default/ss02/ss05/ss04); **y5** (ss03/ss02+ss03/ss02+ss03+ss05) | the `extend feature: ss03` rule |
| qsMay | break (bare) / sees flipped instead | mono x-height is refused before May (rune refuse) |
| qsUtter | break | |
| space/period | break | boundary |

Mono baseline ENTRY accepts any predecessor that exits at y0 (Pea, Tea, It.before-utter, Oy, Day, No, lig) — verified `qsPea|qsUtter` y0, `qsTea.ex-y0|qsUtter` y0, `qsIt.ex-y0.before-utter|qsUtter` y0, `qsOy|qsUtter` y0, `qsNo.alt.en-y0.ex-y0|qsUtter` y0.

### qsUtter flipped (alt; x-height entry reaches-way-back, baseline exit)

OLD flipped fires in exactly three stance flavors:

1. **`qsUtter.alt.ex-y0` / `qsUtter.alt.ex-y0.before-may`** — flipped baseline exit, NO live entry. Fires **only toward qsMay** (y0), from any predecessor (all break on the left) and word-initial. There is no old-font flipped-baseline exit toward any follower other than qsMay outside the reach-back.
2. **`qsUtter.alt.en-y5.ex-y0.reaches-way-back`** — the reach-back. Fires for **exactly one context: predecessor qsMay, follower qsNo** (Lseam y5, Rseam y0). This is ·May·Utter·No only — confirmed by an exhaustive scan of the whole baseline corpus across all configs.
3. **`qsUtter.noentry`** — post-ZWNJ shadow (deleted in the new model). Word-initial; joins followers at x-height (y5), including ·May at **y5** (the anomaly the new model unifies away).

### ss03 ·X·Utter·Tea law (left-context dependent)

Under ss03 the mono x-height exit into ·Tea (`qsUtter.ex-ext-1` → `qsTea.half.after-xheight-exit`) is suppressed after certain predecessors:

| Predecessor of ·Utter | OLD ·Utter→·Tea (ss03) | NEW |
|---|---|---|
| qsPea (joins y0) | **break** | y5 (gain) |
| qsTea (joins y0) | **break** | y5 (gain) |
| qsMay (breaks) | **break** | y5 (gain) |
| qsNo, qsIt, qsOy (join y0) | y5 | y5 (match) |
| qsUtter, space, period (break) | y5 | y5 (match) |

The discriminator is NOT simply "·Utter joined on the left" (Pea/Tea join y0 → break; No/It/Oy also join y0 → join). It is a depth-dependent left-context the closed `when:` cannot cleanly express on the extend; expressible only as a `refuse {left: {family: [qsPea,qsTea,qsMay]}, right: qsTea, feature: ss03}`, which would CONTRADICT the reviewed-approved `ss03-chain-join-gains` philosophy.

## Per-window-shape disposition

### 1. ss03 ·Utter·Tea x-height gain — INTENDED-WIDEN (48 windows / 150 rows)

`E67A>E652:break->y5` under ss03/ss02+ss03/ss02+ss03+ss05. Shapes: ·Pea·Utter·Tea, ·Tea·Utter·Tea, ·May·Utter·Tea (and word-boundary variants).

- `E650:E67A:E652` ss03: OLD `qsPea|qsUtter|qsTea` seams `y0,break`; NEW `qsPea.full.ex-baseline|qsUtter.mono.ex-x-height.ex-ext-1|qsTea.half` seams `y0,y5`.
- `E665:E67A:E652` ss03: OLD `qsMay|qsUtter|qsTea` `break,break`; NEW `...|qsUtter.mono.ex-x-height.ex-ext-1|qsTea.half` `break,y5`.

The new font gains a ·Utter→·Tea x-height join under ss03 (join-count maximization) — the **same phenomenon** as the reviewed-approved `ss03-chain-join-gains` (·Tea·May·Tea, ·May·May·Tea, ·It·May·Tea gain the ·May·Tea / ·It·May join). The classifier records the gain as `seam-gain:qsUtter` (the gained seam's LEFT cell is qsUtter), but line 612 only recognizes `gain_runes & {qsTea, qsMay}` → falls through to None → UNMATCHED.

Fix: widen `ss03-chain-join-gains` to recognize the ·Utter-led x-height gain into ·Tea. CAVEAT: a naive `gain_runes` widening to include `qsUtter` is **unsafe** — there are non-Tea ss03 ·Utter-left changes (e.g. `E650:E67A:E665` is a `regrouping-floor-drift`, not a Tea gain). The safe condition keys on the gained seam's RECEIVER being qsTea at x-height. Medium confidence on the exact predicate form; high confidence it is the same already-approved phenomenon.

### 2. ·May·Utter·May reach-back gain — VERDICT-GATED (22 windows)

`E665>E67A:break->y5`. Shape: ·May·Utter·May (and variants).

- `E665:E67A:E665` default: OLD `qsMay|qsUtter.alt.ex-y0.before-may|qsMay.en-y0.ex-y5` seams `break,y0` (·May·Utter BREAKS, ·Utter→·May y0); NEW `qsMay.loop.ex-x-height|qsUtter.flipped.en-x-height.ex-baseline|qsMay.loop.en-baseline` seams `y5,y0` (·May·Utter JOINS at x-height via the reach-back, then ·Utter→·May y0).

The new font gains a ·May→·Utter x-height reach-back join the old font never drew. In OLD the reach-back fires **only** for ·May·Utter·No; for ·May·Utter·May the old font uses the plain `before-may` flipped stance (no left join). The rune's reach-back entry `from: [{family: [qsMay, qsFee], joined_at: x-height}]` + `require: [exit]` with flipped exit `toward: [qsMay, qsNo]` fires whenever the predecessor is ·May/·Fee and the exit is live — toward either ·May OR ·No. To match OLD it would need to fire only when the exit lands on ·No, a cross-side (entry-conditioned-on-exit-target) restriction the closed `from`/`require`/`toward` vocabulary cannot express. So this is a genuine new visual join, not a fixable scope error → VERDICT for the user. Old-vs-new one-liner: "·May·Utter·May: old breaks ·May→·Utter and only ·Utter→·May joins; new reaches ·Utter's bar back across ·May so ·May·Utter also connects at the x-height."

### 3. post-ZWNJ ·Utter·May seam unification — INTENDED (already classed) + multi-delta residue (VERDICT)

`200C:E67A:E665` and the simple post-ZWNJ ·Utter cases now classify as `zwnj-word-initial-seam-moved` (status drift-accepted) — OLD `qsUtter.noentry|qsMay.en-y5` joins ·May at y5 (the shadow anomaly), NEW unifies post-ZWNJ ·Utter to the word-initial flipped-baseline form (y0), matching its post-space sibling. These are now absorbed.

Residue (≈8 windows): post-ZWNJ ·Utter·May·X where a SECOND seam also changes (e.g. `200C:E67A:E665:E652` = `E67A>E665:y5->y0 ; E665>E652:y0->break`). The `zwnj-word-initial-seam-moved` predicate requires the seam move to be the SOLE change (no gain, no loss), so these fall through. They are the same ZWNJ unification compounded by a follower-side regroup → VERDICT-GATED (a fresh ·May·X seam moved by the unification cascade). Not a qsUtter scope error.

### 4. ·Utter·No flipped-chain — qsNo agent + depth-2 inexpressible (≈45 windows)

`E67A>E666:y5->y0 ; E666>{E665,E67A}:break->y0` (·Utter·No·May, ·Utter·No·Utter, ·No·No·Utter).

- `E67A:E666:E665` default: OLD `qsUtter|qsNo|qsMay` `y5,break` (·Utter→·No x-height, ·No→·May break, ·No stays loop); NEW `qsUtter.flipped.ex-baseline|qsNo.flipped|qsMay.loop` `y0,y0` (whole chain flips to baseline, ·No→·May join gained).

OLD never joins ·Utter→·No at baseline outside the reach-back; ·Utter·No is always x-height (mono). The chain flips because ·No can join ·May/·Utter at baseline (·No flipped exit `toward`), which summons ·No flipped (entry `from: [...qsUtter...]`), which pulls ·Utter to its flipped baseline exit (`toward: qsNo`). The gains and the chain flip are **qsNo-agent** behaviors (·No's flipped `from`/`toward`). From qsUtter's side, `toward: qsNo` and ·No's `from: qsUtter` are BOTH needed for the correct ·May·Utter·No reach-back, so neither can be removed without breaking the reach-back. The discriminator (·Utter flips before ·No only when ·Utter itself entered from ·May) is the **depth-2 left-context** wall already documented in `halves-entry-extension-restored`. → VERDICT / engine-limited drift; coordinate with the qsNo agent.

### 5. ·Utter·No·Oy x-height gain — qsNo agent (22 windows)

`E666>E679:break->y5`. `E67A:E666:E679` default: OLD `qsUtter|qsNo|qsOy` `y5,break`; NEW `qsUtter.mono|qsNo.loop|qsOy.loop` `y5,y5`. ·Utter→·No matches (x-height); the NEW gain is ·No→·Oy at x-height (·No loop exit toward ·Oy). Pure qsNo-loop-chain gain → qsNo agent.

### 6. ·X·It·Utter / It·Utter chains — qsIt agent (≈120 windows)

Buckets `E67A>E670:y0->y5` (34, ss04-only), `E665>E670` (23), `E652>E670` (21), `E650>E670` (20), `E670>E67A:y0->break` (22). The seam that moves is on the ·It side or the ·It→·Utter join:

- `E67A:E670:E653` ss04: OLD `y0,y0` (·Utter→·It at baseline — the ss04 ·It pass-through), NEW `y5,y0` (·Utter→·It x-height). ss04 ·It pass-through, qsIt agent (M1-PROGRESS bucket B).
- `E670:E670:E67A` default: OLD `break,y0` (·It·It break, ·It→·Utter y0), NEW `break,break` — ·It→·Utter **seam LOSS**. The middle entered ·It no longer joins ·Utter. This touches ·Utter's mono baseline entry but the cause is ·It's exit-stance selection in the It·It·Utter chain → qsIt agent, FLAG: a real seam loss.
- `E652:E670:E67A` / `E650:E670:E67A`: ·Tea·It / ·Pea·It gains feeding ·It→·Utter (`entered-it-baseline-join-gain` family); ·It→·Utter matches. qsIt agent.

·Utter's role is the correct mono baseline entry accepting ·It's y0 exit. No qsUtter scope error.

### 7. Name-grain only (seam agrees) — It/Day-side dangling (≈19 windows)

`E679:E670:E67A` (·Oy·It·Utter), `E67A:E653:E670` (·Utter·Day·It). Seams agree; the divergence is a dangling/dropped anchor on ·It or ·Day (e.g. `qsIt.ex-y0.before-utter` vs `qsIt.bar` with the exit dropped). `dangling-anchor-dropped` / `bare-name-live-join` territory, qsIt/qsDay agent.

## qsUtter rune-scope verdict on the authored records

- **mono x-height exit refuse before qsMay** — CORRECT and faithful. OLD ·Utter·May is always the flipped baseline (y0), never mono x-height; the refuse forces flipped. Verified `E67A:E665` y0 in every config.
- **flipped baseline exit `toward: [qsMay, qsNo]`** — `qsMay` is correct (the only old-font flipped-baseline target). `qsNo` is needed ONLY for the reach-back ·May·Utter·No; it over-fires for plain ·Utter·No·{May,Utter} (chain flip), but cannot be removed without breaking the reach-back. Leave as-is; the over-fire is depth-2 inexpressible drift (qsNo-agent coordination).
- **reach-back `from: [qsMay, qsFee], require: [exit]`** — over-fires: OLD's reach-back is ·May·Utter·**No** only, but the rune fires it for ·May·Utter·**May** too (toward either ·May or ·No). Inexpressible to restrict to No-only follower in the closed vocabulary. The ·May·Utter·May reach-back gain (22 windows) is the result → VERDICT.
- **ss03 extend `right: qsTea`** — correct seam-wise (matches OLD where ·Utter is unjoined/after-No/It/Oy); the ·Pea/·Tea/·May predecessor cases are a join GAIN, same as the approved ss03-chain class → widen the classifier, do not add a left-scoped refuse.

No FIX-TO-MATCH rune edits are warranted for qsUtter as a letter: every seam delta is either an already-approved-class phenomenon extended to ·Utter (ss03 gain → widen), a genuine new visual join the closed vocabulary cannot suppress without collateral (reach-back gain, No-chain flip → verdict / engine-limited), or another letter's scope (·It ss04 pass-through, ·No chains, ·Day/·It dangling names).
