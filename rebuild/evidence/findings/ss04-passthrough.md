# ss04 pass-through cluster (M1 batch 2, bucket B) — findings

Scope: all 209 windows in `tmp/clusters/H_+ex-ext-1.json` (every one is `ss04`-only). Each window is a `·X·It·Y` (or `·X·It·Y·Z`) chain where the central ·It is the entered ss04 baseline pass-through (old name `qsIt.en-y0.ex-y0.before-day` / `.after-day` / `.before-utter`). The cluster phenomenon is `+ex-ext-1` plus name-grain noise. The audit splits the cluster cleanly into **two** independent phenomena.

## Ground truth — the old-font entered-·It baseline-exit extension law

Scanning every `baseline-*.subset.tsv.gz` for an ·It cell carrying `ex-ext-1` on its baseline exit: across the **entire** old font there is exactly one such name — `qsIt.en-y5.ex-y0.ex-ext-1` (entered at x-height, exits at baseline). The ss04 baseline pass-through names (`qsIt.en-y0.ex-y0.before-day` / `.after-day` / `.before-utter`) NEVER carry `ex-ext-1` in any config.

Confirmed structurally in `glyph_data/quikscript.yaml`:

- The extension lives on `qsIt.stances.entry_xheight_exit_baseline` (modifiers `[en-y5]`) via `derive.extend_exit_when_entered: {by: 1}` (line 2760). That stance is entry=x-height, exit=baseline.
- The ss04 pass-through stances `entry_baseline_after_day` (2774), `entry_baseline_before_day` (2791), `entry_baseline_before_utter` (2875) all `inherit: entry_baseline_exit_xheight` (en-y0, NO `extend_exit_when_entered`) and override `exit: [1, 0]`. `entry_baseline_before_utter` additionally sets `extend_exit_before: null`. None inherit the entered-exit extension.

**Old-font law: extend ·It's baseline exit by 1px iff ·It entered at x-height (`joined_at: x-height`). Never when ·It entered at the baseline (en-y0).**

The new qsIt rune over-applies it. `glyph_data/runes/qsIt.yaml:78`:
`{stance: bar, exit: baseline, by: 1, when: {self: {entry: live}}}` — fires on ANY live entry (en-y0 and en-y5 alike), so the entered baseline pass-through wrongly gains `ex-ext-1`.

## The two groups (209 windows)

| Group | Windows | Lead·It seam (old→new) | The divergence | Disposition |
|---|---|---|---|---|
| **B — `+ex-ext-1` only** | 123 | unchanged (baseline `y0` both, or break) | new ·It baseline pass-through gains a +1px exit extension the old font never drew | **FIX-TO-MATCH** |
| **A — lead·It seam moved** | 86 | `y0 → y5` | under ss04 the old font lowered the lead·It join to the baseline; the new font keeps the default x-height·It hump | **VERDICT-GATED** |

### Group B — FIX-TO-MATCH (123 windows)

Seams agree old↔new; the only delta is `+ex-ext-1` on the entered baseline-pass-through ·It. Grounding (probe, ss04):

- `E653:E670:E653` (·Day·It·Day): OLD `qsDay | qsIt.en-y0.ex-y0.before-day | qsDay.half` seams `y0,y0`; NEW `qsDay.full/ex=baseline | qsIt.bar/en=baseline/ex=baseline/ex-ext-1 | qsDay.half` seams `y0,y0`. Seams identical; new ·It carries `ex-ext-1`, old does not.
- `E679:E670:E653` (·Oy·It·Day) and `E653:E670:E67A` (·Day·It·Utter): same shape.

**Fix:** narrow `qsIt.yaml:78` to fire only when ·It was joined-to at x-height (the old `extend_exit_when_entered` condition). The `when:` grammar cannot read `self.entry: x-height` (the model's `self_entry` is only `"live"`/`"none"`, model.py:62), but `when.left.joined_at: x-height` reads the seam into ·It, which IS ·It's entry height. Change to:

`{stance: bar, exit: baseline, by: 1, when: {self: {entry: live}, left: {joined_at: x-height}}}`

**Build-verified** with the scratch harness (`tmp/scratch_build.py`):

- All 123 seam-agreeing windows clear (`+ex-ext-1` gone, matching old un-extended exit). Per-position check: 123 → 0 "·It-ext-extra-vs-old".
- The approved `entered-it-baseline-join-gain` class is untouched — that join enters ·It at x-height (`qsIt.en-y5.ex-y0.ex-ext-1`, e.g. `E652:E670:E670` default), so `left.joined_at: x-height` keeps its extension.
- Full oracle over all 11 letters × 8 configs: **unmatched 3366 → 3240 (−126), zero new unmatched (zero regressions).**

**Collateral (also part of the fix):** the un-extended entered ·It cell makes a benign off-anchor ink contact at y=1 on these baseline joins (the same corner the already-blessed `.ex-ext-1` siblings make — the extension lengthens the y=0 connector, not the y=1 letter-body adjacency). 9 new `rebuild/m1-contact-allow.yaml` blessings are needed; each has an existing `.ex-ext-1`-bearing twin in the file, proving faithfulness:

```
contact:qsIt.bar.en-y0.ex-y0:qsIt.bar.en-y0:y1
contact:qsIt.bar.en-y0.ex-y0:qsIt.bar.en-y0.ex-y0:y1
contact:qsIt.bar.en-y0.ex-y0:qsUtter.mono.en-y0:y1
contact:qsIt.bar.en-y0.ex-y0:qsUtter.mono.en-y0.ex-bind-pulled-back:y1
contact:qsIt.bar.en-y0.ex-y0:qsUtter.mono.en-y0.ex-y5:y1
contact:qsOy.loop.en-y5.ex-y0:qsIt.bar.en-y0.ex-y0:y1
contact:qsOy.loop.ex-y0:qsIt.bar.en-y0.ex-y0:y1
contact:qsOy.loop.ex-y0.locked:qsIt.bar.en-y0.ex-y0:y1
contact:qsTea_qsOy.bar-into-loop.ex-y0:qsIt.bar.en-y0.ex-y0:y1
```

This also discharges the M1-BATCH2 **Q1** note (ss04 before-·Utter null-derives): where the before-utter baseline pass-through IS realized (baseline-exiting leads like ·Day·It·Utter), the exit extension is now correctly absent, matching the old `extend_exit_before: null` stance. No remaining Q1 divergence on baseline leads.

### Group A — VERDICT-GATED (86 windows)

The lead·It seam moves from baseline (old) to x-height (new). Dominant leads: ·Pea (21), ·No (19), ·Utter (19), word-boundary-then-those (18), plus ·Tea/·Tea_Oy/·It/·Oy/·Day stragglers. All share one cause: an **x-height-exiting lead** (·Pea.half, ·No.loop, ·Utter.mono all exit at the x-height).

Grounding (probe, `E650:E670:E67A` ·Pea·It·Utter):

- OLD **default**: `qsPea.half.ex-y5.ex-dips | qsIt.en-y5.ex-y0.ex-ext-1 | qsUtter` seams `y5,y0` (·Pea·It at x-height, ·It·Utter at baseline).
- OLD **ss04**: `qsPea | qsIt.en-y0.ex-y0.before-utter | qsUtter` seams `y0,y0` — ss04 deliberately DROPS the ·Pea·It join to the baseline, flattening the whole chain.
- NEW **ss04**: `qsPea.half/ex=x-height | qsIt.bar/en=x-height/ex=baseline/ex-ext-1 | qsUtter` seams `y5,y0` — identical to its own default; the ss04 baseline-lowering is not implemented.

Why the new font can't lower it: the new ·Pea `half` stance has no baseline exit; `full` refuses baseline-exit before ·It and declares `pairings.never: [{entry: baseline, exit: baseline}]` (qsPea.yaml). So ·Pea·It can only join at the x-height. The same holds for ·No.loop and ·Utter.mono. ·It's ss04 baseline-entry unlock (qsIt.yaml:47-48) is satisfiable only when the predecessor exits at the baseline — which is exactly why Group B (·Day/·Oy/·Tea_Oy baseline-exiting leads) works and Group A (x-height-exiting leads) does not.

This is the **Q1 latent gap made real** now that ·Day/·Utter are live. Restoring the old behavior is a multi-rune authorship task (·Pea/·No/·Utter each need an ss04-gated baseline exit toward ·It, and ·Pea.half has no baseline exit to gate), not a scope tweak — and it is a genuine taste call: the new font's x-height ·Pea·It is arguably more consistent (ss04 == default), the old font's flat baseline chain is the documented ss04 intent. **Not proposing a fix; surfacing for a verdict.**

One-line reviewer summary: _Under ss04, before a baseline pass-through ·It, an x-height-exiting lead (·Pea/·No/·Utter) — old font drops the lead·It join down to the baseline so the whole word runs flat; new font keeps the x-height ·Pea·It hump. Keep the new consistent hump, or restore the old ss04 baseline-flattening (multi-rune work)?_

## Disposition summary

| Cluster                                                 | Windows | Disposition   | Target                                    |
| ------------------------------------------------------- | ------- | ------------- | ----------------------------------------- |
| ss04 entered-·It baseline-exit over-extension           | 123     | FIX-TO-MATCH  | qsIt.yaml:78 narrow + 9 contact blessings |
| ss04 lead·It x-height-lead seam not lowered to baseline | 86      | VERDICT-GATED | multi-rune; user taste call               |
