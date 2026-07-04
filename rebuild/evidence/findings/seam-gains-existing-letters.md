# Area: seam-gains on existing letters adjacent to the new runes (mostly verdict-gated)

Scope: `tmp/clusters/D_seam-gain.json` filtered to windows whose `gains` ⊆ {qsMay, qsOy, qsTea, qsIt, qsPea} — the GAINED join is on an already-shipped letter, triggered by the new-letter (qsDay/qsNo/qsUtter) context. **141 windows, 462 UNMATCHED rows** (every window has some non-unmatched rows already absorbed by `dangling-anchor-dropped`/`bare-name-live-join`/`ss03-chain-join-gains`/etc.; the 462 are the rows still failing the oracle).

## Headline conclusion

**None of these are wrong/impossible joins. Every gained join is the same join shape the letter makes elsewhere in the old font — the new engine simply keeps a join the old font's stance-selection (or its ss04 pass-through, or its post-ZWNJ shadow) deliberately dropped.** So there are **no FIX-to-match scope edits** in this area: the old breaks are not "the correct behavior the rune got wrong," they are cascade/feature artifacts of the old font that the design's join-maximizing settlement legitimately improves on. Every family is a **VERDICT-GATED taste call** (does the user want the extra join / the ss04 baseline flattening / the seam-move?), and several are **INTENDED-WIDEN candidates** — the same phenomenon as an already-adjudicated ledger class whose classifier just isn't firing because a co-occurring `seam-moved` (or a config gate, or a `+ex-ext-1`) trips an earlier `return None` in `classify_divergence`.

The 6 families below partition the 462 rows exactly.

## The six families

| Family | Windows | Rows | Shape | Old | New | Configs |
|---|---|---|---|---|---|---|
| A. Tea·It before Day/Utter | 41 | 159 | `[X]·Tea·It·Day`, `[X]·Tea·It·Utter` | Tea·It **break** | Tea·It **y5** join | default/ss02/ss04/ss05 |
| B. ss04 It baseline pass-through declined | 45 | 46 | `[X]·May·It·Day`, `·May·It·Utter`, etc. | x-height-exiting pred → It baseline pass-through (drops the x-height join) | keeps the x-height join | **ss04 only** |
| C. May·Utter reach-back | 23 | 92 | `[X]·May·Utter·May` | May·Utter **break** | May·Utter **y5** join (qsUtter.flipped reach-back) | default/ss02/ss04/ss05 |
| D. Oy·It before No | 19 | 122 | `[X]·Oy·It·No` | Oy·It **break**, It·No y0 | Oy·It **y0** join, It·No moved y0→**y5** | all (incl. ss03) |
| E. post-ZWNJ Utter·May | 7 | 37 | `◊ZWNJ·Utter·May·X` | Utter·May y5 (noentry shadow), May·X break | Utter·May moved y5→y0, May·X **y5** join | all |
| F. ss04 4-letter chains | 6 | 6 | `·X·Utter·It·Day`, `·X·Utter·It·Utter` (ss04) | leading X·Utter break, Utter·It y0 | X·Utter **y0** join, Utter·It moved y0→y5 | ss04 only |

---

### Family A — `·Tea·It` before `·Day`/`·Utter` (41 windows / 159 rows) — VERDICT-GATED

**Ground truth (default, baseline-default.subset).** Old `·Tea·It` joins at x-height (y5) before EVERYTHING except `·Day`/`·Utter`:

```
E652:E670:E650  y5,break   qsTea.half.ex-y5|qsIt.en-y5.ex-y0|qsPea
E652:E670:E665  y5,y0      qsTea.half.ex-y5|qsIt.en-y5.ex-y0.ex-ext-1|qsMay...
E652:E670:E653  break,y0   qsTea|qsIt.ex-y0.before-day.after-no-baseline-join|qsDay.half...   ← BREAK
E652:E670:E67A  break,y0   qsTea|qsIt.ex-y0.before-utter|qsUtter                              ← BREAK
```

Before `·Day`/`·Utter` the old font leaves `·Tea` **bare** (no `.half.ex-y5` stance) and selects an entry-less `·It` (`ex-y0.before-day.after-no-baseline-join` / `before-utter`) — declining the Tea·It join entirely so `·It` can sit baseline-to-baseline-ish into the half-`·Day`/`·Utter`. (Contrast `·May·It·Day` at `y5,y0`: there `·May` keeps its live x-height exit stance, so the join survives. The asymmetry is purely that old `·Tea` declines its x-height-exit stance in this context while old `·May` does not.)

**New (probe E652:E670:E653, default):** `qsTea.half/ex=x-height | qsIt.bar/en=x-height/ex=baseline/ex-ext-1 | qsDay.half/en=baseline` → seams **y5,y0**. The new engine keeps `·Tea` at its `half.ex-y5` stance and `·It` carries BOTH the x-height entry and the baseline exit. The gained y5 join is the *same* Tea·It x-height join drawn everywhere else; `·It`'s extended baseline exit (`ex-ext-1`) keeps the two verticals two columns apart (the same `why` that licenses `entered-it-baseline-join-gain`).

**Disposition:** VERDICT-GATED. This is structurally `entered-it-baseline-join-gain` (an entered `·It` keeps a join the old cascade dropped) — the classifier routes it to `None` because `gain_runes = {qsTea}` (not `qsIt`) hits the fall-through `return None` at conform.py:615. Resembles approved `entered-it-baseline-join-gain` / `pea-chain-regularized` most. One-line for the reviewer: *"·Tea·It now joins at the x-height before ·Day/·Utter, where the old font left ·Tea bare and broke the join."*

*If the user approves*, this becomes an **INTENDED-WIDEN**: a new ledger entry (e.g. `tea-it-xheight-join-before-day-utter`) or widen `entered-it-baseline-join-gain` to also fire when the gain rune is the x-height-exiting predecessor of an entered `·It` (the `·It` itself is entered and keeps both anchors). Predicate sketch: `gain_runes ⊆ {qsTea}` and the gained seam's right neighbor is an entered `·It` whose own baseline exit lands on `·Day`/`·Utter`. **Do not author a refuse** — the join is the design-correct one; the old break was the artifact.

---

### Family B — ss04 `·It` baseline pass-through declined (45 windows / 46 rows) — VERDICT-GATED

This is the qsIt ss04 unlock (`qsIt.yaml:45-48`) now firing for the first time (M1-REPORT §11.3 / line 208 flagged the ss04 unlocks as behaviorally inert until qsDay/qsUtter migrated).

**Ground truth (ss04, baseline-ss04.subset, `·X·It·Day`):** old `·It` becomes a baseline pass-through (`en-y0.ex-y0.before-day`):

```
E650:E670:E653  y0,y0      qsPea|qsIt.en-y0.ex-y0.before-day|qsDay.half     ← Pea pulled DOWN to baseline
E653:E670:E653  y0,y0      qsDay|qsIt.en-y0.ex-y0.before-day|qsDay.half
E665:E670:E653  break,y0   qsMay|qsIt.en-y0.ex-y0.before-day|qsDay.half     ← May break (x-height exit incompatible)
E652:E670:E653  break,y0   qsTea|qsIt.en-y0.ex-y0.before-day|qsDay.half     ← Tea break
```

For baseline-exiting predecessors (Day/No/Oy/Utter/Pea-bare) the chain flattens to all-baseline (`y0,y0`). For x-height-exiting predecessors (May, Tea) the incoming join **breaks** because the pass-through forces `·It` to a baseline entry.

**New (probe E650:E670:E653, ss04):** `qsPea.half/ex=x-height | qsIt.bar/en=x-height/ex=baseline | qsDay.half` → **y5,y0**. The new engine does **not** take the ss04 baseline pass-through for x-height-exiting predecessors — it keeps the natural x-height join (identical to default). (For baseline-exiting predecessors like `·Day·It·Day` the new engine DOES match the pass-through: probe `E653:E670:E653` ss04 = `y0,y0` both. So the divergence is exactly: *x-height-exiting predecessor + ss04 + It→Day/Utter*.)

The 45 windows are `·May·It·Day`, `·May·It·Utter`, `·Tea·It·Day`(ss04 variant), and 4-letter `·X·May·It·Day`/`·X·May·It·Utter` plus `·May·It·200C·Day` shapes — all the May/Tea (x-height-exiting) predecessor cases.

**Disposition:** VERDICT-GATED. The genuine question: *under ss04, should an It→Day/Utter chain flatten the whole run to the baseline (old) or keep the incoming x-height join (new)?* This is a real visual seam change (x-height vs baseline join on the predecessor·It seam) and a feature-semantics taste call. One-line: *"Under ss04, ·May·It·Day (and ·Tea/·It→Day/Utter) now keeps the x-height join into ·It instead of pulling the whole chain down to the baseline as the old ss04 feature did."*

**Could it be a FIX?** Only if the user decides the old ss04 flattening is correct — then the rune would need to PREFER the baseline pass-through over the x-height entry when ss04 is on and `·Day`/`·Utter` follows, for x-height-exiting predecessors. That is expressible (a `prefer {cell:{entry:baseline,exit:baseline}, over:{entry:x-height}}` scoped to ss04 + right Day/Utter on qsIt), but it is a deliberate design decision, not a scope bug — so it stays a verdict, not a confident fix.

---

### Family C — `·May·Utter` reach-back join (23 windows / 92 rows) — VERDICT-GATED (authored design intent)

**Ground truth (default, `·May·Utter`):** old `·May·Utter` **always breaks**. The only Utter reach-back in the old font is before `·No` (`qsUtter.alt.reaches-way-back`, y5). Before `·May`, old uses `qsUtter.alt.ex-y0.before-may` (baseline-only, no x-height entry → May·Utter breaks, Utter·May joins y0):

```
E665:E67A         break        qsMay|qsUtter
E665:E67A:E665    break,y0     qsMay|qsUtter.alt.ex-y0.before-may|qsMay.en-y0.ex-y5
```

**New (probe E665:E67A:E665, default):** `qsMay.loop/ex=x-height | qsUtter.flipped/en=x-height/ex=baseline | qsMay.loop/en=baseline` → **y5,y0**. The May·Utter join is gained at x-height via the **authored** `qsUtter.flipped` reach-back stance (`qsUtter.yaml:81`: `entries.x-height … joined: reaches-way-back, from: [{family: [qsMay, qsFee], joined_at: x-height}]`). The design law: `·Utter`'s flipped opening bar reaches back across an x-height-exiting `·May`/`·Fee`.

**Disposition:** VERDICT-GATED. This is a genuine new visual join, deliberately authored — the design now makes `·May·Utter` join where the old font always broke. One-line: *"·May·Utter now joins at the x-height (·Utter's flipped bar reaches back across ·May), where the old font always broke the pair."* Resembles no existing ledger class (it is the qsUtter design, not a cascade artifact); if approved it wants its **own intended/reviewed-approved ledger entry** (e.g. `utter-flipped-reach-back-join`), not a widen of an existing one.

---

### Family D — `·Oy·It` before `·No` (19 windows / 122 rows) — VERDICT-GATED

**Ground truth (default, `·Oy·It`):** old `·Oy·It` joins at baseline (y0) before most followers, but **breaks** before `·Day`/`·May`/`·No`/`·Utter`:

```
E679:E670:E650  y0,break   qsOy|qsIt.en-y0.ex-y5|qsPea
E679:E670:E666  break,y0   qsOy|qsIt.ex-y0|qsNo.alt.en-y0.ex-y0    ← Oy·It BREAK, It·No baseline
```

Before `·No`, old `·It` = `ex-y0` (no entry, baseline exit) → Oy·It breaks, It·No joins **baseline** via `qsNo.alt`.

**New (probe E679:E670:E666, default):** `qsOy.loop/ex=baseline | qsIt.bar/en=baseline/ex=x-height | qsNo.loop/en=x-height` → **y0,y5**. The new engine keeps the Oy·It baseline join (entered `·It`) AND routes It→No to the x-height `qsNo.loop` — so the It·No seam **moves** y0→y5. Net: one gained join (Oy·It) plus a seam-move on the right.

**Disposition:** VERDICT-GATED. Most resembles `regrouping-floor-drift` (reviewed-**rejected**): a gain co-occurring with a seam-move that rearranges where the chain's joins land. But unlike floor-drift's "same number of joins on a different seam," here the new font has STRICTLY MORE joins (2 vs 1). It is the same `entered-it-baseline-join-gain` capability (entered `·It` keeps its left baseline join) with the right seam relocated to x-height because `·No`'s loop x-height entry is receiver-gated/unscoped. One-line: *"·Oy·It now joins at the baseline before ·No, with the ·It·No join riding up to the x-height (·No's loop), where the old font broke ·Oy·It and joined ·It·No at the baseline."* The classifier bails at `seam-moved → return None` (conform.py:596), which is why it is unmatched.

---

### Family E — post-ZWNJ `·Utter·May` unification (7 windows / 37 rows) — INTENDED-WIDEN candidate

**Ground truth (default, `◊ZWNJ·Utter·May·X`):** old uses the `qsUtter.noentry` word-initial shadow:

```
200C:E67A:E665:E650  break,y5,break  space|qsUtter.noentry|qsMay.en-y5|qsPea
200C:E67A:E665:E670  break,y5,break  space|qsUtter.noentry|qsMay.en-y5|qsIt
```

The old noentry shadow makes Utter·May join at **y5** and `·May` carry no rightward exit (May·X breaks). Compare the non-ZWNJ `·Utter·May·Pea` = `qsUtter.before-may|qsMay.en-y0.ex-y5.ex-ext-1|qsPea` seams `y0,y5` — Utter·May at **baseline**, May·Pea at y5.

**New (probe 200C:E67A:E665:E650, default):** `zwnj | qsUtter.flipped.locked/ex=baseline | qsMay.loop/en=baseline/ex=x-height/ex-ext-1 | qsPea/en=x-height` → **break,y0,y5**. The post-ZWNJ locked `·Utter` twin equals the live word-initial stance, so Utter exits **baseline** (matching the non-ZWNJ behavior, not the old noentry's y5) — the Utter·May seam moves y5→y0 — AND `·May`'s own rightward join is **restored** (May·X gains y5).

**Disposition:** INTENDED-WIDEN. The May·X gain is exactly `zwnj-follower-exit-restored` (the word-initial letter's follower regains its right-side join; the ledger exemplar is `200C:E665:E665:E670`). The co-occurring Utter·May seam-move is `zwnj-word-initial-unification` (the locked twin's drawing equals the live word-initial stance, here exiting baseline not the old shadow's y5). Both are already adjudicated/intended classes; the row is unmatched only because `classify_divergence` checks `seam-moved → return None` (conform.py:596) **before** the `gains`/`old-noentry` branch (conform.py:605-606) that would route it to `zwnj-follower-exit-restored`. See scope_fixes.

---

### Family F — ss04 4-letter `·X·Utter·It·Day/Utter` (6 windows / 6 rows) — VERDICT-GATED

The 6 windows: `E670:E67A:E670:E653`, `E670:E67A:E670:E67A` (gain qsIt), `E650:E67A:E670:E653`, `E650:E67A:E670:E67A` (gain qsPea), `E652:E67A:E670:E653`, `E652:E67A:E670:E67A` (gain qsTea) — all ss04 only.

**Ground truth & new (probe E670:E67A:E670:E653, ss04):**

```
OLD  qsIt.before-utter|qsUtter.alt.ex-y0|qsIt.before-day|qsDay.half     break,y0,y0
NEW  qsIt.bar/ex=baseline | qsUtter.mono/en=baseline/ex=x-height | qsIt.bar/en=x-height/ex=baseline | qsDay.half   y0,y5,y0
```

The leading X·Utter join is **gained** (old `·Utter` = `alt.ex-y0` baseline-only with no live baseline entry → break; new `qsUtter.mono` entered baseline → y0), and the Utter·It seam **moves** y0→y5. This is the ss04-chain analog of family B: under ss04 the old font picks an entry-less alt-Utter to serve the downstream It→Day pass-through, dropping the leading join; the new engine keeps it.

**Disposition:** VERDICT-GATED, same family as B (ss04 pass-through declined) but with a seam-move so it lands in the `seam-moved → return None` bucket. One-line: *"Under ss04, ·It·Utter·It·Day keeps the leading ·It·Utter baseline join (with the ·Utter·It join riding up to the x-height), where the old ss04 chain dropped it."* Adjudicate together with family B.

---

## Derived old-font join truth tables (default config unless noted)

### `·Tea·It·X` (default)
| X | seams | note |
|---|---|---|
| space/dot/zwnj/Pea/Tea/It/Oy | y5,break | Tea·It joins x-height |
| May, No | y5,y0 | join + It·X baseline |
| **Day, Utter** | **break,y0** | **Tea bare, Tea·It declined** |

### `·X·It·Day` (default vs ss04)
| X | default | ss04 |
|---|---|---|
| Pea(half), May, No, Utter | y5,y0 (x-height in) | **y0,y0 / break,y0** (pass-through) |
| Day, Oy, No(baseline) | break,y0 (default) | y0,y0 |
| space/dot/zwnj/Tea/It | break,y0 | break,y0 |

Under ss04, x-height-exiting predecessors (May, Tea) **break** before the pass-through It (`break,y0`); baseline-exiting predecessors (Pea-bare, Day, No, Oy, Utter) get pulled to `y0,y0`.

### `·Oy·It·X` (default)
| X | seams | note |
|---|---|---|
| space/dot/zwnj/Pea/Tea/It/Oy | y0,break | Oy·It joins baseline |
| **Day, May, No, Utter** | **break,y0** | **Oy·It declined, It·X baseline** |

### `·May·Utter·X` (default)
| X | seams | note |
|---|---|---|
| (most) | break / break,break | May·Utter always breaks |
| No | y5,y0 | reach-back (`qsUtter.alt.reaches-way-back`) |
| **May** | **break,y0** | before-may baseline-only Utter; May·Utter break |

### `·Utter·May·X` (default, non-ZWNJ vs post-ZWNJ)
| context | seams | note |
|---|---|---|
| non-ZWNJ `·Utter·May·Pea` | y0,y5 | Utter·May baseline, May·Pea y5 |
| post-ZWNJ `◊·Utter·May·Pea` | break,y5,break | noentry shadow: Utter·May y5, May·Pea break |

**Context-dependence note (methodology learning #1):** every break in these tables is context-dependent — the same `·Tea·It` / `·Oy·It` / `·May·Utter` pair joins in most contexts and breaks only when a specific follower (Day/Utter/May/No) forces a baseline-only or reach-back stance selection. A flat family list CANNOT capture this; these are not scope-narrowing fixes. They are settlement keeping more joins than the old cascade/feature selection chose.

## Disposition summary

- **0 FIX-to-match** edits. No gained join here is wrong/impossible; every old break is a cascade/feature/shadow artifact, not authored law the rune violates.
- **1 INTENDED-WIDEN (high confidence):** Family E — reorder `classify_divergence` so `old-noentry` rows route to `zwnj-follower-exit-restored` / `zwnj-word-initial-unification` even when `seam-moved` co-occurs. See scope_fixes.
- **5 VERDICT-GATED families (A, B, C, D, F):** genuine taste calls for the user's verdict pass. A and D additionally resemble already-approved `entered-it-baseline-join-gain` and may, on approval, become widens/new ledger entries; C wants its own `utter-flipped-reach-back-join` entry; B and F are the ss04-feature-semantics question.
