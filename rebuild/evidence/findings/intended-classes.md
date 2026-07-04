# M1 batch 2 oracle triage — likely-intended already-adjudicated classes (G/I/J/F/Z)

Scope: clusters G_-en-ext-1 (37), I_old-noentry (15), J_position (53), F_+en-ext-1 (1), Z_other (8). ss10 windows are out of scope throughout.

Two concrete classifier/ledger fixes land here and absorb 529 unmatched rows total (oracle 3366 -> 2837, zero multi-match, all 25 conform unit tests green). The rest are either genuine taste calls (verdict-gated seam topology) or out-of-scope ss10.

## Fix 1 (J cluster) — kern-attributable position drift must not demote an ink-identical cell match

`rebuild/pipeline/conform.py`, `compare_against_baseline`. When a row first matches a single ink-identical cell-grain class (zwnj-word-initial-unification, dangling-anchor-dropped, bare-name-live-join) and then `_position_drift` finds a **kern-attributable** drift (a ZWNJ adjacency or a sidecar-kerned pair), the code appended a `position` kind and re-classified through `classify_divergence`, which returns None for any position-bearing row (line 588), and the `kern_channel_out_of_scope` predicate requires `kinds == ("position",)` — so the cleanly-matched row got kicked to UNMATCHED.

The existing in-alphabet letters never tripped this (their post-ZWNJ rows reproduced the old advances exactly, so 0 position drift). The new letters introduce kern-attributable ZWNJ-adjacency drift on their post-ZWNJ rows.

Fix: when the re-match comes back empty, the drift was kern-attributable, and there was a prior single ink-identical match, keep that prior match. A non-kern-attributable drift still overrides (a genuine ink shift contradicts the ink-identity claim and belongs in the position channel). This matches the documented intent of `kern-channel-out-of-scope` ("non-kern position drift is chased to ground, never absorbed").

Ground truth (real-run, my clusters): every non-ss10 J window resolves to `kern-attr -> should-keep:zwnj-word-initial-unification`. Examples:

- `ss04 E653:200C:E670` — `qsDay|space|qsIt.en-y0.ex-y0.after-day` (seams break,break) vs `qsDay.full | zwnj.boundary | qsIt.bar.locked` (seams break,break). Seams agree; only `+locked` name grain + a ZWNJ-adjacency advance shuffle.
- `ss03 E653:200C:E67A:E652` — `qsDay|space|qsUtter.ex-ext-1|qsTea.half...` (break,break,y5) vs `qsDay.full|zwnj.boundary|qsUtter.mono.locked+ex-ext-1|qsTea.half` (break,break,y5). Seams agree.

Effect: `kern-channel-out-of-scope` count 0 -> 188.

## Fix 2 (I cluster) — ZWNJ word-initial unification that *moves* a seam

`rebuild/pipeline/conform.py` `classify_divergence` + a new ledger entry `zwnj-word-initial-seam-moved`.

The I cluster is post-ZWNJ ·Utter·May (`200C:E67A:E665` and `200C:200C:E67A:E665`, 116 rows). Phenomena: `old-noentry, seam-moved, stance, entry-moved, exit-added` — a pure seam move, no gain, no loss.

```
OLD  space | qsUtter.noentry | qsMay.en-y5            seams break,y5  (x-height join on the shadow)
NEW  zwnj.boundary | qsUtter.flipped/ex=baseline | qsMay.loop/en=baseline   seams break,y0  (baseline, word-initial)
```

The old `.noentry` shadow stance joins ·May at the x-height (y5). Settling the post-ZWNJ ·Utter as word-initial joins at the baseline (y0). Verified across default/ss03/ss04/ss02+ss03+ss05: the new post-ZWNJ ·Utter·May tail is **byte-identical to both the post-space form and the bare ·Utter·May**, all three joining at y0. The old shipped font is itself inconsistent — post-space ·Utter·May joins at y0 (`qsUtter.alt.ex-y0`) while post-ZWNJ joins at y5. The new model unifies post-ZWNJ to the word-initial form (design 3.4). This is the same law as `zwnj-word-initial-unification`, but where the old shadow joined at a different height than word-initial settlement, so the unification *moves* the seam rather than agreeing on it (the existing class's why says "Seams agree", which is why it can't absorb this).

Predicate (added inside the `seam-moved` branch, before the unconditional `return None`): fire `zwnj-word-initial-seam-moved` when `old-noentry` is present, there are no seam-gains, and no seam-loss — i.e. the sole seam change is the move. A post-ZWNJ row that also gains/loses a seam still falls through to its own class.

Ledger status: **drift-accepted** (not silently intended). This is a real visible seam-height change, so it should be ratified in a human verdict pass like `zwnj-follower-exit-restored` was, not absorbed unreviewed. Surface for the next verdict round.

Effect: `zwnj-word-initial-seam-moved` count 0 -> 116. Confirmed it does NOT absorb the 70 `(seam-gain)` post-ZWNJ ·No·No rows nor the 4 `(seam-loss)` ·Utter·May·Tea rows — those stay on their own (verdict-gated) routes.

## G cluster — the ·Tea·Oy·Day extension drop is a lead-expansion artifact in the OLD font (VERDICT-GATED)

The non-ss10 half of G is 16 windows on `E652:E679:E653` (·Tea·Oy·Day). The other half (`E652:E653:E67A` ·Tea·Day·Utter, ss10) is out of scope.

```
OLD  qsTea_qsOy | qsDay.half.en-y0.ex-y0.en-ext-1      seams lig,y0
NEW  qsTea_qsOy.bar-into-loop/ex=baseline | qsDay.half/en=baseline   seams y0
```

Baseline truth table for qsDay's half-baseline entry extension:

| predecessor | old qsDay cell | extended? |
|---|---|---|
| bare ·Tea (`E652:E653`) | `qsDay.half.en-y0.ex-y0.en-ext-1` | yes |
| bare ·Oy (`E679:E653`) | `qsDay.half.en-y0.ex-y0` | **no** |
| ·Tea·Oy ligature (`E652:E679:E653`) | `qsDay.half.en-y0.ex-y0.en-ext-1` | yes |

The old font extends qsDay after the ligature because old YAML's `{family: qsTea}` selector auto-expanded to any ligature led by qsTea (`expand_selectors_for_ligatures`). But the *actual exit shape* at that seam is the ·Oy loop exit — and bare ·Oy·Day is NOT extended. The new model resolves the extend `left:` family at the cell's own rune (`qsTea_qsOy`, neither qsTea nor qsYe — settle.py:203), so the ligature does not match qsDay's extend rule `{left: {family: [qsTea, qsYe]}}` (qsDay.yaml:98), and ·Tea·Oy·Day is drawn exactly like bare ·Oy·Day.

This is the one-pixel ink delta (the `position-drift` total-advance `want 700, got 650` in J's genuine-drift bucket is the same `en-ext-1`). The new behavior is arguably more faithful (the seam is the ·Oy loop exit, which is never extended), but it is a real visible 1-px change of exactly the kind the user has triaged sharply before (`halves-entry-extension-restored` rejected; `same-seam-extension-non-summing` approved). **VERDICT-GATED.** If the user wants the old extra pixel restored, the fix is to add `qsTea_qsOy` to qsDay.yaml `policy.extend[1].when.left.family` (currently `[qsTea, qsYe]`); confidence that this restores old ink is high, but it is a taste call, not a bug.

## F cluster — Pea·Pea·Day·Utter regroup (VERDICT-GATED, out of intended scope)

`E650:E650:E653:E67A`, 1 window: OLD seams `y6,y0,lig` (Day_Utter ligature, Pea·Pea·Day joins at baseline y0) vs NEW `y6,y5` (the 2nd Pea joins the Day_Utter ligature at x-height y5). A real grouping/seam-height change (the `+en-ext-1` rides the new ligature). Remains `cell,seam` UNMATCHED. This is the join-shaping tail (M1-BATCH2-PROGRESS bucket C, the Day·Utter ligature joins), not an already-adjudicated class. **VERDICT-GATED.**

## Z cluster — genuine seam topology (VERDICT-GATED / other buckets)

All 8 are real seam-topology changes, none an intended-widen:

- `E670:E666:E653:E67A` (·It·No·Day_Utter): OLD `y0,y0,lig` -> NEW `y5,y5`. Both joins moved baseline->x-height. Join-shaping tail (·No chains). **VERDICT-GATED.**
- `E679:E670:E653:E67A` (·Oy·It·Day_Utter, ss04): OLD `y0,y0,lig` -> NEW `y0,y5`. ·It·Day_Utter moved y0->y5. **VERDICT-GATED** (ss04 ·It pass-through, bucket B).
- `E650:E670:200C:E653`, `E666:E670:200C:E653`, `E67A:E670:200C:E653` (X·It ZWNJ ·Day, ss04): OLD `y0,break,break` -> NEW `y5,break,break`. The X·It join moved y0->y5 under ss04. The ss04 ·It baseline pass-through (bucket B). **VERDICT-GATED.**

These belong to the join-shaping tail / ss04 pass-through investigations (other agents' areas), not the already-adjudicated intended classes.

## Per-window disposition summary

| cluster | window family | configs | disposition | target |
|---|---|---|---|---|
| G | `E652:E679:E653` ·Tea·Oy·Day | default,ss02,ss03,ss04,ss05,ss02+ss03,ss02+ss03+ss05 (16 win) | VERDICT-GATED | (taste; if restore: qsDay.yaml extend[1] left family += qsTea_qsOy) |
| G | `E652:E653:E67A` ·Tea·Day·Utter | ss10 (21 win) | out of scope (ss10) | — |
| I | `200C:E67A:E665` ·Utter·May post-ZWNJ | all non-ss10 (15 win) | INTENDED-WIDEN | new ledger `zwnj-word-initial-seam-moved` + conform.py route |
| J | ss03/ss04 `+locked` post-ZWNJ | ss03,ss04 (29 win) | FIX-TO-MATCH (classifier bug) | conform.py kern-attributable preserve |
| J | ss10 Day·It·Utter etc | ss10 (24 win) | out of scope (ss10) | — |
| F | `E650:E650:E653:E67A` | non-ss10 (1 win) | VERDICT-GATED | join-shaping tail |
| Z | ·It·No / ·Oy·It / X·It-ZWNJ-·Day | default+ss04 (8 win) | VERDICT-GATED | join-shaping tail / ss04 pass-through |

## Re-run / verification

- `PYTHONPATH=. uv run python -m rebuild.pipeline.run_m1` -> unmatched 2837 (was 3366), multi_matched 0.
- `uv run pytest rebuild/test_conform.py -n auto --dist worksteal` -> 25 passed.
- New counts: `kern-channel-out-of-scope` 188, `zwnj-word-initial-seam-moved` 116.
