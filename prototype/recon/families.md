# Recon B: glyph data for the prototype subset (qsIt, qsTea, qsMay + one ligature)

Sources: `glyph_data/quikscript.yaml` (qsTea 564–707, qsTea_qsOy 708–724, qsMay 2120–2292, qsIt 2726–2892, qsOut_qsTea 3303–3335, context_sets 1–145), `tools/quikscript_ir.py`, `doc/rebuild-design.md` §5/§6.1/§7. Empirical shaping comes from `prototype/recon/probe_families.py` (run: `uv run python prototype/recon/probe_families.py`), which dumps the compiled variant inventory via `compile_glyph_set(data, "senior")` and shapes through HarfBuzz against `site/AbbotsMortonSpaceportSansSenior-Regular.otf`. Gap math spot-checks via `uv run python tools/inspect_join.py qsMay qsIt qsMay`.

Conventions: heights are glyph-space y (baseline y=0, x-height y=5, top y=8). `entry.x = min_ink_x_at_entry_y`, `exit.x = max_ink_x_at_exit_y + 1`. `en-ext-1`/`ex-ext-1` derived variants add 1 column of connector ink on that side and shift the anchor; `ex-con-1` contracts. `.noentry` twins are the ZWNJ chokepoint’s entry-locked copies (`sub uni200C @entry-live' by @entry-locked`).

## 1. Family inventories

### qsIt (U+E670, short, 6-row bitmap; prop is a 1-column vertical bar)

Hard pairing rule (the family’s ductus, lines 2727–2731): joined-at-x-height ⇒ exits-at-baseline, joined-at-baseline ⇒ exits-at-x-height — same-height pass-through exists only behind ss04. In rebuild terms this is the `pairings: only:` case (§3.2 says ·It is the `only:` example).

| Cell (entry, exit) | Stance (YAML key) | Anchors | Traits/mods | Conditions and derives |
| --- | --- | --- | --- | --- |
| (none, none) | bare `qsIt` | — | — | isolated/default |
| (none, y5) | `entry_nowhere_exit_xheight` | ex (1,5) | ex-y5 | not_after qsOwe; not_before {qsDay en-y5, qsYe, qsZoo} |
| (none, y0) | `entry_nowhere_exit_baseline` | ex (1,0) | ex-y0 | `strip_entry_before: true`; not_after {qsBay,qsDay,qsGay,qsShe}; not_before {qsTea,qsIt,qsRoe}; extend_exit_before by 1 → qsI |
| (none, y0) | `entry_nowhere_exit_baseline_before_day` | ex (1,0) | ex-y0, before-day, after-no-baseline-join | before {qsDay entry_y 0}; exists so post-ZWNJ `qsIt.noentry` upgrades correctly (comment at line 2855) |
| (none, y0) | `before_utter` | ex (1,0) | before-utter | before {qsUtter entry_y 0}; not_after {qsZoo,qsIt,qsEat,qsOwe,qsShe} |
| (y5, y0) | `entry_xheight_exit_baseline` | en (0,5), ex (1,0) | en-y5 | not_after qsIt; extend_entry_after by 1 ← halves_exit_xheight_no_pea; **extend_exit_when_entered by 1** |
| (y0, y5) | `entry_baseline_exit_xheight` | en (0,0), ex (1,5) | en-y0 | not_after {qsJay,qsYe,qsIt,qsEat}; not_before qsDay; extend_entry_after by 1 ← qsKey; extend_exit_before by 1 → {qsZoo,qsJai,qsCheer,qsOwe} |
| (y0, y0) ss04 | `entry_baseline_after_day` / `entry_baseline_before_low` / `entry_baseline_before_day` / `entry_baseline_before_utter` | en (0,0), ex (1,0) | en-y0, ex-y0, after-day / before-low / before-day / before-utter | all `gate_feature_behind: ss04`; contexts: after qsDay / before {qsLow entry_y 0} / before {qsDay entry_y 5} / before {qsUtter entry_y 0} |
| (y0, withdrawn) | `entry_baseline_exit_noentry` (+ `…_before_day_exam`) | en (0,0), exit null | en-y0, ex-noentry (+before-day-exam) | the entry-preserving withdrawal form; before-day-exam variant carries the big `not_after {exit_y: 0, except: […17 families]}` carve-out |
| (locked) | `qsIt.noentry` | — | noentry | ZWNJ entry-locked twin; plus derived `.noentry` siblings on every entry-bearing variant |

Family-level derive: `extend_exit_before: {by: 1, targets: [qsJai, qsCheer, qsOwe]}` (line 2749).

### qsTea (U+E652, tall, 9-row bar; `half` shape is the top 4 rows, y5–y8)

| Cell (entry, exit) | Stance | Anchors | Traits/mods | Conditions and derives |
| --- | --- | --- | --- | --- |
| (none, none) | bare `qsTea` | — | — | isolated/default |
| (none, y0) | `exit_baseline` | ex (1,0) | ex-y0 | not_after qsEt; not_before {qsThaw,qsExcite,qsExam,qsIt} (two adjacent verticals at the baseline read as one thick stroke — comment at 665) |
| (none*, y5) half | `half_exit_xheight` | entry_curs_only (0,8), ex (1,5) | half, ex-y5 | not_after qsEt; not_before context_set `followers_that_reject_tea_half_xheight_exit` = {qsTea,qsFee,qsCheer,qsYe,qsOwe,qsFoot}; contract_exit_before by 1 → {qsZoo,qsJay} |
| (y8, y5) half | `half_entry_top_exit_xheight` | en (0,8), ex (1,5) | half, en-y8, ex-y5 | inherits half_exit_xheight; contract_exit_before by 1 → qsZoo only |
| (y5, none) half | `half_entry_xheight` | en (0,5) | half, en-y5 | not_before qsTea; extend_entry_after by 1 ← halves_exit_xheight |
| (y5, none) half, ss03 | `half_entry_xheight_ss03` | en (0,5) | half, en-y5, after-xheight-exit | gated ss03; after {qsMay exit_y 5, qsLow, qsI, qsAh, qsUtter exit_y 5, qsOut exit_y 5, qsOwe exit_y 5, qsFoot} |
| (y8, none) | `entry_top` | en (0,8) | en-y8 | — |
| (y8, y0) | `entry_top_exit_baseline` | en (0,8), ex (1,0) | en-y8, ex-y0 | not_before {qsThaw,qsExcite,qsExam,qsIt} |
| (y0, none) | `entry_baseline` | en (0,0) | en-y0 | not_after {qsPea,qsTea,qsYe,qsHe,qsExam,qsIt,qsEat}; extend_entry_after by 1 ← {qsKey,qsJay,qsEight} |
| (y0, y0) ss05 | `entry_baseline_exit_baseline_ss05` | en (0,0), ex (1,0) | en-y0, ex-y0 | gated ss05; after qsEt only |
| (y5, y0) ss02 | `entry_xheight` | en (0,5), ex (1,0) | en-y5 | gated ss02; after qsI only |
| (y5, y0) ss03 | `entry_xheight_after_fee` | en (0,5), ex (1,0) | en-y5, after-fee | gated ss03; after {qsFee exit_y 5} |
| (locked) | `qsTea.noentry` | — | noentry | ZWNJ twin; derived `.noentry` siblings throughout |

Family-level derive: `extend_exit_before: {by: 1, targets: [qsI]}` (line 600). *`entry_curs_only` (line 637) means the y8 anchor exists for GPOS cursive attachment but the stance is not selectable as an entry — kept as a flagged oddity in the rebuild (§3.2 `selectable: false`).

Implicit pairing facts: full ·Tea never does en-y0+ex-y0 outside ss05; ·Tea·Tea never joins in either direction (entry_baseline not_after qsTea; half_entry_xheight not_before qsTea; reject-set includes qsTea); ·It→·Tea never joins (entry_baseline not_after qsIt, and no half en-y5 trigger covers qsIt).

### qsMay (U+E665, deep, y_offset −3; four hand-drawn bitmaps of one motion)

Prose pairing notes (lines 2121–2123, the §3.2 `pairings: never:` worked example): ·May can join predecessor or follower at the baseline but not both; same at the x-height. Structurally honored — no stance has en-y0+ex-y0 or en-y5+ex-y5.

Bitmaps (`shapes:`): base `mono` (isolated; y5 row `"   ##"`, the col-4 pixel is the exit-connector stub kept only while the exit is live), `pulled_back_a_bit_for_entry_at_short_height` (y5 row `"   # "` — exit withdrawn), `exits_at_baseline` (the grounded-loop), `pulled_back…and_exits_at_baseline`, `pulled_back…without_stubbie` (after-·Fee entry form, baseline stub also gone).

| Cell (entry, exit) | Stance | Anchors | Mods | Conditions and derives |
| --- | --- | --- | --- | --- |
| (none, y5) | bare `qsMay` (prop) | ex (5,5) | — | isolated form carries the live exit; extend_exit_before by 1 → {qsDay,qsFee,qsJai,qsJay,qsRoe,qsIt}; ss03-gated extend_exit_before → qsTea |
| (none, withdrawn) | `exit_noentry` | — | ex-noentry | pulled-back bitmap; the mid-word exit-declined form |
| (y5, withdrawn) | `entry_xheight` | en (3,5) | en-y5 | pulled-back bitmap; after {8 `qsX_qsUtter` ligatures + context_set `reaches_up_and_way_over…` = qsI + ex-ext-1 forms of qsAh/qsUtter/the qsX_qsUtter ligatures}; extend_entry_after by 1 ← halves_exit_xheight |
| (y5, withdrawn) | `entry_xheight_after_fee` | en (2,5) | en-y5, after-fee | without_stubbie bitmap; after context_set `xheight_exit_reaches_into_may` = {qsFee ex-y5} |
| (y5, withdrawn) | `entry_xheight_after_i` | en (3,5) | en-y5, after-i | inherits entry_xheight; after qsI |
| (y5, y0) | `entry_xheight_exit_baseline` (+`…_after_i`) | en (2,5), ex (4,0) | en-y5, ex-y0 | pulled-back+grounded bitmap; note the entry anchor moves from 3 to 2 with the combined form (§3.2’s per-cell `entry_x` example); not_before {qsDay,qsThaw,qsZoo,qsYe,qsHe,qsNo,qsRoe,qsIt,qsEat,qsUtter,qsOoze} |
| (y0, y5) | `entry_baseline` | en (0,0), ex (5,5) | en-y0 | mono bitmap; **extend_exit_when_entered by 1**; extend_entry_after by 1 ← context_set `vie_may_entry_extend_triggers` = {qsPea,qsTea,qsYe,qsHe,qsIt} |
| (y0, withdrawn) | `entry_baseline_exit_noentry` | en (0,0), exit null | en-y0, ex-noentry | pulled-back bitmap; the entry-preserving withdrawal (§5.1’s worked pair) |
| (none, y0) | `exit_baseline` | ex (4,0) | ex-y0 | exits_at_baseline bitmap; not_before {qsDay,qsZoo,qsHe,qsNo,qsRoe,qsIt,qsEat,qsUtter,qsOoze} |
| (locked) | `qsMay.noentry` | ex (5,5) | noentry | ZWNJ twin keeps the live exit |

## 2. How the three families join each other today (Senior, default features unless noted)

The 9 ordered pairs, from the probe (format: left variant [exit] | right variant [entry]):

| Pair | Result | Join | Extension |
| --- | --- | --- | --- |
| It·It | `qsIt.ex-y5` \| bare `qsIt` | none | first ·It still takes the ex-y5 upgrade — a benign dangling exit anchor (the prop bar adds no connector ink); today’s font tolerates committed exits with no acceptor, which the rebuild’s `E-STRANDED` would forbid |
| It·Tea | bare \| bare | none | — |
| It·May | `qsIt.ex-y0` (1,0) \| `qsMay.en-y0.ex-y5.en-ext-1` | baseline | 1 px on May’s entry side (vie_may trigger) |
| Tea·It | `qsTea.half.ex-y5` (1,5) \| `qsIt.en-y5.ex-y0` | x-height | none; ·Tea becomes a half letter |
| Tea·Tea | bare \| bare | none | — |
| Tea·May | `qsTea.ex-y0` (1,0) \| `qsMay.en-y0.ex-y5.en-ext-1` | baseline | 1 px on May’s entry side; corpus pin `·Tea ~b~ ·May` exists in site/the-manual.html |
| May·It | `qsMay.ex-ext-1` (6,5) \| `qsIt.en-y5.ex-y0` | x-height | 1 px on May’s exit side (family extend_exit_before → qsIt) |
| May·Tea | bare \| bare (default). ss03: `qsMay.ex-ext-1` \| `qsTea.half.en-y5.after-xheight-exit` | none / x-height under ss03 | ss03: 1 px on May’s exit (gated extend) |
| May·May | `qsMay.ex-y0` (4,0) \| `qsMay.en-y0.ex-y5` | baseline | none; second seam of May·May·May never joins (May’s en-y5 after-list excludes qsMay — its (5,5) exit can’t reach the set-back (3,5) entry) |

Settled-left-dependent chains (the backtrack-sees-settled cases the §7 prototype must reproduce):

- **Tea·It·May** → `qsTea.half.ex-y5 | qsIt.en-y5.ex-y0.ex-ext-1 | qsMay.en-y0.ex-y5`. Because ·It is _entered_, `extend_exit_when_entered` moves the 1 px connector onto It’s exit and May’s en-ext-1 does **not** fire — compare bare It·May where the pixel rides on May’s entry. The middle letter’s resolved entry state changes which side carries the extension.
- **May·It·May** → `qsMay.ex-ext-1 | qsIt.en-y5.ex-y0.ex-ext-1 | qsMay.en-y0.ex-y5.en-ext-1`. Same middle glyph as Tea·It·May, same follower, but here May’s en-ext-1 _also_ fires — both sides extend, giving a connector 1 px longer than Tea·It·May’s. Both verified gap = 0 by `inspect_join`, so this is a cascade-order artifact (today’s lookups saw different intermediate predecessor names), exactly the both-sides-summed class the redesign’s “same-side records never sum; only `split:` combines” rule is meant to make deliberate. The prototype’s settlement function has to pick one of these and the treaty diff will surface the change.
- **It·May·It** → `qsIt.ex-y0 | qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1 | qsIt.en-y5.ex-y0` — middle May extends both sides (entry after It, exit before It); also shows May entered-at-baseline exiting x-height (pairings honored).
- **May·Tea·It (ss03)** → `qsMay.ex-ext-1 | qsTea.half.en-y5.after-xheight-exit | qsIt` — under ss03 the left context flips Tea into the entry-only half (no exit), so the Tea·It seam that joins by default is _lost_. Three-position dependency through the settled left.
- **Tea·It·Tea** → `qsTea.half.ex-y5 | qsIt.en-y5.ex-y0 | qsTea` — entered ·It keeps its (1,0) exit toward an entryless ·Tea (benign anchor dangle, no ink), and `extend_exit_when_entered` does not fire because the exit join never realizes.

Boundaries (space, ZWNJ):

- Isolated forms: bare `qsIt` and `qsTea` carry no anchors; bare `qsMay` carries the live exit (5,5) and the col-4 stub (its isolated drawing includes the connector).
- Space blocks everything: `X space Y` shapes both sides bare in every feature configuration probed.
- ZWNJ: the follower is locked to its `.noentry` twin (`qsIt.noentry`, `qsTea.noentry`, `qsMay.noentry` — the May twin keeps ex (5,5)); the predecessor stays bare. HarfBuzz renders the ZWNJ position as a zero-width `space` glyph (default-ignorable handling); cmap maps U+200C → `uni200C`.
- **Present-day ss03 ZWNJ leak, confirmed by direct probe**: `qsMay ZWNJ qsTea` with ss03 on shapes `qsMay.ex-ext-1 | (zwnj) | qsTea.half.en-y5.after-xheight-exit` — both sides take their joined forms across the zero-width ZWNJ, so they visually join. A real space blocks it. Cause: `half_entry_xheight_ss03`’s `after:` list has no `uni200C`/`space` guard and the contextual lookup skips the default-ignorable. This is precisely the §7 “ZWNJ at every slot” obligation; the prototype must get this case right and can use today’s behavior as the _negative_ baseline.
- ss04 is a no-op inside this subset (its It contexts need qsDay/qsLow/qsUtter); ss02/ss05 need qsI/qsEt. ss03 is the only stylistic set the three-family subset exercises (May→Tea, and Fee/Out contexts that are out of scope).

## 3. Ligatures touching {qsIt, qsTea, qsMay}

Of the 13 ligatures, exactly two have a component in the set (none involve qsIt or qsMay):

- **`qsTea_qsOy`** (lines 708–724): `sequence: [qsTea, qsOy]`, prop bitmap with `exit: [8, 0]` only. It declares no entry and inherits none — `_inherit_ligature_entries_from_lead` requires the ligature bitmap to share the lead’s leftmost-ink column at the entry Y, and the ligature’s leftmost ink at y0 (col 7) and y8 (col 3) match neither of qsTea’s en-y0/en-y8 x=0 anchors. So it is effectively entryless (not `entry: null`-declared; compiled meta simply has no entry and no `.noentry` twin). Consequences seen in the probe: predecessors withdraw — `qsIt qsTea qsOy` leaves ·It fully bare (entry_nowhere_exit_baseline’s `not_before: qsTea` expands to the ligature via lead expansion), `qsMay qsTea qsOy` leaves ·May in its bare (exit-anchor-only) form, and `qsExcite qsTea qsOy` is §5.7’s canonical `_PENDING_LIGA_ENTRY_GUARDS` case (Excite keeps en (0,0), surrenders its exit). Formation is _not_ unconditional in practice: with ss03 active and an x-height-exit predecessor (`qsMay qsTea qsOy` + ss03), the gated half-Tea substitution consumes qsTea before the liga lookup, and no ligature forms.
- **`qsOut_qsTea`** (lines 3303–3335): `sequence: [qsOut, qsTea]`, entry (0,0) (inherited from lead qsOut), **no exit** at all. Carries an `after_see` stance (`after: [{family: qsSee, exit_y: 0}]`, its own hand-drawn bitmap — the §3.3 `bind:` example), a derived `en-trim-1`, and a `.noentry` twin. Trailing-component expansion makes followers’ `after: [{family: qsTea}]` selectors pick it up, but with no exit it never joins forward (`qsOut qsTea qsIt` / `…qsMay` shape unjoined). Also suppressed by ss03 (`qsOut qsTea` + ss03 shapes as qsOut + half-Tea, no liga).

**Recommendation: `qsTea_qsOy`.** (a) Its lead component qsTea is in the family set, so formation interacts with the subset’s own stances; (b) it is the entryless ligature that forces predecessor withdrawal — §5.7’s exact story — which exercises the most interesting seam the prototype can de-risk: settlement must see, before committing anything at position i−1, that the seam offers no entry, and land the predecessor in its withdrawn cell (It bare, May bare/ex-noentry, Excite-style entry-keep is out of scope). qsOut_qsTea is the weaker pick: its interesting edge (after-·See bound shape) needs qsSee, outside the subset, and its entry side is an ordinary inherited (0,0). The one cost of qsTea_qsOy is that typing it requires the U+E679 (·Oy) codepoint as formation input; qsOy itself needs no stance modeling (the bare qsOy glyph only appears when formation is suppressed).

## 4. Concrete test sequences with established expected behavior

All expectations below were established by the probe against the current built font (and two corpus pins). Codepoints: ·It U+E670, ·Tea U+E652, ·May U+E665, ·Oy U+E679 (ligature input only), ZWNJ U+200C, space U+0020.

| # | Sequence (families) | Codepoints | Expected (current font, default features) |
| --- | --- | --- | --- |
| 1 | qsIt | E670 | bare `qsIt` |
| 2 | qsTea | E652 | bare `qsTea` |
| 3 | qsMay | E665 | bare `qsMay` ex (5,5) |
| 4 | qsIt qsMay | E670 E665 | `qsIt.ex-y0` \| `qsMay.en-y0.ex-y5.en-ext-1` (baseline) |
| 5 | qsTea qsMay | E652 E665 | `qsTea.ex-y0` \| `qsMay.en-y0.ex-y5.en-ext-1`; corpus pin `·Tea ~b~ ·May` |
| 6 | qsTea qsIt | E652 E670 | `qsTea.half.ex-y5` \| `qsIt.en-y5.ex-y0`; corpus pin `\- \| ·It \| ·Tea ~x~ ·It` also pins the isolated-·It-then-break half |
| 7 | qsMay qsIt | E665 E670 | `qsMay.ex-ext-1` \| `qsIt.en-y5.ex-y0` (x-height, exit-extended) |
| 8 | qsMay qsMay | E665 E665 | `qsMay.ex-y0` \| `qsMay.en-y0.ex-y5` (baseline) |
| 9 | qsIt qsIt | E670 E670 | `qsIt.ex-y5` \| bare `qsIt` (no join; benign dangling exit) |
| 10 | qsMay qsTea | E665 E652 | bare \| bare; with ss03: `qsMay.ex-ext-1` \| `qsTea.half.en-y5.after-xheight-exit` |
| 11 | qsTea qsIt qsMay | E652 E670 E665 | **backtrack case**: `qsTea.half.ex-y5` \| `qsIt.en-y5.ex-y0.ex-ext-1` \| `qsMay.en-y0.ex-y5` — entered ·It carries the extension, May entry plain |
| 12 | qsMay qsIt qsMay | E665 E670 E665 | **backtrack case**: `qsMay.ex-ext-1` \| `qsIt.en-y5.ex-y0.ex-ext-1` \| `qsMay.en-y0.ex-y5.en-ext-1` — both-sides extension; differs from #11’s right seam despite identical middle glyph |
| 13 | qsIt qsMay qsIt | E670 E665 E670 | **backtrack case**: `qsIt.ex-y0` \| `qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1` \| `qsIt.en-y5.ex-y0` — middle May double-extended, pairing en-y0→ex-y5 honored |
| 14 | qsMay qsTea qsIt (ss03) | E665 E652 E670 | **backtrack case**: `qsMay.ex-ext-1` \| `qsTea.half.en-y5.after-xheight-exit` \| bare `qsIt` — left context costs the Tea·It join |
| 15 | qsTea qsIt qsTea qsIt | E652 E670 E652 E670 | repeats: `half.ex-y5 \| en-y5.ex-y0 \| half.ex-y5 \| en-y5.ex-y0`; middle ·It keeps a benign (1,0) exit toward the entryless half-Tea in the length-3 prefix `qsTea qsIt qsTea` |
| 16 | qsMay qsMay qsMay | E665 E665 E665 | `ex-y0 \| en-y0.ex-y5 \| bare` — second seam never joins |
| 17 | qsIt ZWNJ qsTea | E670 200C E652 | bare `qsIt` \| zwnj \| `qsTea.noentry` |
| 18 | qsTea qsIt ZWNJ qsMay | E652 E670 200C E665 | `qsTea.half.ex-y5 \| qsIt.en-y5.ex-y0` \| zwnj \| `qsMay.noentry` (join survives left of the break; follower locked) |
| 19 | qsMay ZWNJ qsIt qsTea | E665 200C E670 E652 | `qsMay` \| zwnj \| `qsIt.noentry` \| bare `qsTea` |
| 20 | qsMay ZWNJ qsTea (ss03) | E665 200C E652 | **today: leaks** (`qsMay.ex-ext-1` \| zwnj \| `qsTea.half.en-y5.after-xheight-exit`); the prototype must instead match #21’s space behavior — this row is a negative baseline, not a pin |
| 21 | qsMay space qsTea (ss03) | E665 0020 E652 | bare \| space \| bare (space correctly blocks the ss03 upgrade) |
| 22 | qsTea qsOy | E652 E679 | `qsTea_qsOy` ex (8,0) |
| 23 | qsIt qsTea qsOy | E670 E652 E679 | bare `qsIt` \| `qsTea_qsOy` — predecessor withdrawal before the entryless ligature |
| 24 | qsMay qsTea qsOy | E665 E652 E679 | bare `qsMay` \| `qsTea_qsOy`; with ss03: no ligature (`qsMay.ex-ext-1 \| qsTea.half.en-y5.after-xheight-exit \| qsOy`) — formation vs. ss-marker ordering must be decided deliberately in the prototype |
| 25 | qsIt qsIt qsTea qsOy | E670 E670 E652 E679 | `qsIt.ex-y5` \| bare `qsIt` \| `qsTea_qsOy` |

Rows 11–14 are the required ≥3 chains where the middle letter’s resolved form depends on its left neighbor’s resolved form; rows 17–20 are the ZWNJ-interleaved variants; row 20 is the one place the prototype should deliberately diverge from today’s font (document it as an intended-equivalence triage row per §3.4, not a regression).

## 5. Facts the prototype implementer should not miss

- `extend_exit_when_entered` (qsIt en-y5 stance, qsMay en-y0 stance) is the subset’s only `self: {entry: live}` condition — §3.4 retires it into exactly that. It fires only when the exit join actually realizes (see Tea·It·Tea).
- The double-extension divergence between Tea·It·May and May·It·May is real, gap-0 both ways, and must be settled one way; expect a treaty-diff row.
- qsMay’s en-y5 entry anchor is x=3 alone but x=2 in the combined (en-y5, ex-y0) cell — the per-cell `entry_x:` override of §3.2/§5.1 is load-bearing here.
- qsTea’s `half` trait is identity-relevant (data-expect `.half` assertions exist in the corpus); keep `traits: [half]` on the half cells.
- qsTea_qsOy’s entrylessness is _emergent_ (failed inheritance), not declared; in the rune-file format it should be an explicit empty `entries: {}` so the withdrawal obligation is visible.
- Within this subset the only stylistic set with any effect is ss03; a prototype feature matrix of {default, ss03} is complete.
- No corpus pins exist that are composed purely of these three families beyond the two quoted in rows 5–6; shaping probes (this file + `prototype/recon/probe_families.py`) are the expected-behavior source of record.
