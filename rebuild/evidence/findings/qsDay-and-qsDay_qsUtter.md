# qsDay (E653) and the qsDay_qsUtter ligature — oracle triage findings

Scope: (a) qsDay's own joins; (b) the qsDay_qsUtter ligature (all of B_ligation plus the D/C/E windows that involve the ligature). Source of truth is the probe and the `rebuild/out/m1/baseline-*.subset.tsv.gz` tables. Every claim below is grounded in actual probe/baseline output.

## Headline result

- The OLD shipped font **does** form the `qsDay_qsUtter` ligature for the bare 2-glyph sequence `E653:E67A` in **every** config (`OLD glyphs: qsDay_qsUtter`, `OLD seams: lig`). So this is **not** a "new joins where old drew two glyphs" verdict call — it is the same already-adjudicated `marker-staging-ligature-formation` phenomenon (formation staged unconditionally before markers/chokepoint), extended to a new ligature.
- qsDay's **own** joins are all correct. Every 2-letter `E653:X` seed matches old seam topology in default (Pea break, Tea y0, Day y0, May y0, No y0, It y0, Oy break, Utter lig). The two breaks (Day.Pea, Day.Oy) carry `+ex-bind-pulled-back` — Day withdrawing its declined diagonal foot-hook — which is the `may-exit-withdrawal-generalized` class (conform.py:620 matches any `+ex-bind-`).
- The real bugs are **three scope errors on the ligature's x-height exit**, all FIX-TO-MATCH, all verified to flip the seam toward old with **zero regressions across the whole alphabet and all 7 non-ss10 configs** (406 changed window-settlements, 100% toward old, 0 away).

## Derived old-font truth tables (default config)

### qsDay full/half exit (baseline foot-hook)

| follower | OLD seam | NEW seam | match |
|---|---|---|---|
| Pea | break (Day withdraws hook) | break (+ex-bind-pulled-back) | yes (withdrawal class) |
| Tea | y0 | y0 | yes |
| Day | y0 | y0 | yes |
| May | y0 | y0 | yes |
| No  | y0 | y0 | yes |
| It  | y0 | y0 | yes |
| Oy  | break (Day withdraws hook) | break (+ex-bind-pulled-back) | yes (withdrawal class) |
| Utter | lig | lig | yes (ligature) |

qsDay's entry: full enters x-height (unscoped), half enters baseline (unscoped). No qsDay-own entry bug found.

### qsDay_qsUtter ligature exit (inherits qsUtter's exit). OLD `E653:E67A:<foll>`

| follower | OLD seam | NEW (before fix) | NEW (after fix) | disposition |
|---|---|---|---|---|
| Pea | y5 (all configs) | **break** | y5 | FIX (qsPea from-scope) |
| Tea | break default/ss04; **y5 under ss03** | **y5 always** | break default/ss04, y5 ss03 | FIX (qsTea halves except + ss03 unlock) |
| Day | y5 | y5 | y5 | ok |
| May | y5 | y5 | y5 | ok (qsMay already lists qsDay_qsUtter in its from-scope) |
| No  | y5 | y5 | y5 | ok (qsNo x-height entry unscoped) |
| It  | y5 | y5 | y5 | ok (qsIt bar x-height entry unscoped) |
| Oy  | y5 (all configs) | **break** | y5 | FIX (qsOy from-scope) |
| Utter | break | break | break | ok |

## Root causes (grounded)

The ligature settles as `qsDay_qsUtter.full` and offers an x-height exit. Whether the follower joins depends on the **receiver's** x-height entry scope:

1. **Day_Utter.Pea (seam-loss) and Day_Utter.Oy (seam-loss).** qsPea's full and half x-height entries carry `from: [{family: qsMay,…}, {family: qsUtter,…}]`; qsOy's loop x-height entry carries `from: [{family: [… qsUtter qsOut], …}]`. Neither lists the ligature. In `settle.py:203` (`cond_matches_left`), a left context whose `cell.rune == "qsDay_qsUtter"` fails `"qsDay_qsUtter" not in ["qsUtter"]` — there is **no ligature-trailing-component expansion** in this matcher. So the receiver refuses the ligature's x-height exit and it withdraws. The correctly-joining receivers either list the ligature explicitly (qsMay.loop, settle confirmed: `from: [{family: qsDay_qsUtter}, …]`) or have an unscoped x-height entry (qsNo.loop, qsIt.bar, qsDay.full). The fix mirrors qsMay: add `{family: qsDay_qsUtter, joined_at: x-height}` to qsPea (both stances) and qsOy.

2. **Day_Utter.Tea (seam-gain).** qsTea.half's default x-height entry is `from: [{class: halves-that-exit-at-x-height}]`. That class is `{all: [{trait: half}, {can_exit_at: x-height}]}` and is resolved **family-grained**: `_evaluate_predicate_classes` (spec_load.py:807) marks a family a member if **any** of its stances satisfies. The ligature's `half` stance has `traits: [half]` and an x-height exit, so `qsDay_qsUtter` joins the class — and therefore the receiver admits the ligature even when it settled as its **full** stance. Plain `qsUtter` has no half stance, so it is correctly excluded and Tea breaks before it. Under ss03 the old font *does* join (via the same mechanism plain qsUtter uses: Tea.half's ss03 unlock listing `qsUtter`). Two-part fix: (a) exclude the ligature from the default halves from-scope so default/ss04 break like old; (b) add the ligature to the Tea.half ss03 unlock left-family list so ss03 joins like old.

## The two non-ZWNJ ss04 ligature windows — verdict-gated

`E653:E67A:E670:E653` and `E653:E67A:E670:E67A` (ss04 only). For the bare pair and the 3-glyph `E653:E67A:E670`, the OLD font forms the ligature and joins It at y5 in **all** configs (incl. ss04). It is only these **4-glyph** windows under ss04 where OLD **declines** the ligature: it draws `qsDay | qsUtter.alt.ex-y0 | qsIt.…before-day/before-utter | …` and drives a 3-way baseline pass-through (`break,y0,y0`). NEW keeps forming the ligature (formation is unconditional by the marker-staging law) and joins It at x-height (`y5,y0`).

This is the same "formation is unconditional" property already ratified for marker-staging, but here it produces a real seam topology change (It at y5 vs the old baseline pass-through). The closed `when:` vocabulary cannot decline a ligature formation conditionally on a downstream ss04 pass-through chain. Surfaced for a human verdict rather than fixed.

## The 23 post-ZWNJ / post-marker ligature windows — intended-widen

All other B_ligation windows are post-boundary cases: OLD `space|qsDay.noentry|qsUtter` (or a marker prefix), two glyphs joined at y0, where today's pipeline renamed the lead to `.noentry` and never formed the ligature; NEW stages the formation before the marker and emits `qsDay_qsUtter.locked` (one glyph). This is exactly `marker-staging-ligature-formation` (existing exemplar `200C:E652:E679` → `qsTea_qsOy`), and the formation is ink-faithful (the old two-glyph y0 join and the new one-glyph ligature draw the same connected stroke; only the cluster grain differs). The conform.py classifier hardcodes `"E652:E679" in row.codepoints`, so qsDay_qsUtter ligation rows currently return None and stay unmatched. Widen the predicate to also recognize `E653:E67A`.

## Out-of-scope-but-noted (qsDay present, divergence is on a neighbor)

- `seam-gain:qsMay / qsTea / qsNo` windows containing E653: the divergence is on the May/Tea/No seam (chain regrouping or the ss03 chain-join family), not qsDay's own join.
- `E653:E666:E670:E666`, `E653:E670:E653:E67A`: No/It chain regrouping (regrouping-floor-drift family); qsDay exits baseline correctly.
- Non-ligature E653 seam-loss windows (16) are dominated by **ss04 multi-It pass-through chains** (`qsIt.…before-utter/after-day`, the qsIt.yaml:44-48 unlocks) where OLD chains It→It→Day/Utter at baseline under ss04 and NEW breaks one It-It seam. This is the bucket-B ss04 expressiveness gap (qsIt's ss04 unlocks not reaching the multi-It chain), an open qsIt/ss04 question, not a qsDay-own bug. qsDay is only the word-final receiver here.
- Most C_withdrawal+loss (45/65) and E_seam-loss (18/34) E653 rows are ligature-lead Day_Utter→Pea/Oy windows resolved by the FIX edits above; the remainder are May/Tea/No-neighbor withdrawals (`may-exit-withdrawal-generalized`).

## Verification method

Snapshotted full settlement (cells+seams) for all 112,728 (config,window) rows in the subset tables with and without the three edits; classified every changed row by whether its seam topology moved toward or away from the old baseline. Result: 406 changed, 208 strictly toward old + 198 "toward old once the intra-glyph `lig` token is aligned out", **0 away**. Spot-checked plain Utter→Pea/Oy/Tea, May→Tea ss03, Pea.half→Tea, and Pea→It→Tea for regressions — none.

## Scope fixes (exact text)

### qsPea.yaml — full stance x-height entry (line 36)
current:
`        x-height: {x: 0, stroke: vertical, stub: {cols: [0], when: withdrawn}, from: [{family: qsMay, joined_at: x-height}, {family: qsUtter, joined_at: x-height}]}`
proposed:
`        x-height: {x: 0, stroke: vertical, stub: {cols: [0], when: withdrawn}, from: [{family: qsMay, joined_at: x-height}, {family: qsUtter, joined_at: x-height}, {family: qsDay_qsUtter, joined_at: x-height}]}`

### qsPea.yaml — half stance x-height entry (line 73)
same edit as the full stance (append `, {family: qsDay_qsUtter, joined_at: x-height}`).

### qsOy.yaml — loop x-height entry (line 36)
add `qsDay_qsUtter` to the family list (placed first, by lead code point qsDay E653):
`        x-height: {x: 0, stroke: horizontal, joined: open-on-the-left, from: [{family: [qsDay_qsUtter, qsGay, qsFee, qsMay, qsNo, qsLow, qsRoe, qsI, qsAh, qsUtter, qsOut], joined_at: x-height}]}`

### qsTea.yaml — half default x-height entry from-scope (line 65)
current:
`        x-height: {x: 0, stroke: vertical, from: [{class: halves-that-exit-at-x-height}]}`
proposed:
`        x-height: {x: 0, stroke: vertical, from: [{class: halves-that-exit-at-x-height, except: [{family: qsDay_qsUtter}]}]}`

### qsTea.yaml — half ss03 unlock left families (line 74)
add `qsDay_qsUtter` (first, by lead code point):
`      - {entry: x-height, feature: ss03, when: {left: {family: [qsDay_qsUtter, qsMay, qsLow, qsI, qsAh, qsUtter, qsOut, qsOwe, qsFoot], joined_at: x-height}}}`

### conform.py — widen marker-staging predicate (lines 591-594)
current:
```
    if "ligation" in phenomena:
        if "E652:E679" in row.codepoints and ("200C" in row.codepoints or "ss03" in row.config):
            return "marker-staging-ligature-formation"
        return None
```
proposed: also recognize `E653:E67A`. The qsDay_qsUtter post-marker windows are unconditional formation (every config, post-boundary), so the qualifier should admit them. Two ledger options: (1) widen the existing entry's predicate routing, or (2) add a new intended `qsday_qsutter_marker_staging` entry. The two ss04 windows must be excluded from any auto-match (they carry a real seam change) — gate the qsDay_qsUtter branch on `"200C" in row.codepoints` (post-ZWNJ/marker) so the bare ss04 4-glyph windows stay unmatched for the verdict. Recommended predicate body:
```
    if "ligation" in phenomena:
        if "E652:E679" in row.codepoints and ("200C" in row.codepoints or "ss03" in row.config):
            return "marker-staging-ligature-formation"
        if "E653:E67A" in row.codepoints and ("200C" in row.codepoints or "00B7" in row.codepoints):
            return "marker-staging-ligature-formation"
        return None
```
(Confirm the post-boundary qsDay_qsUtter rows all carry `200C` or `00B7` in codepoints — every B_ligation post-marker window does. The bare `E653:E67A:E670:*` ss04 windows carry neither, so they correctly stay unmatched and route to the verdict pile.)
