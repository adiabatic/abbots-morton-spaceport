# Shaper-semantics findings

Findings from the week-one de-risking verification (PLAN.md §6). The prototype itself is fully conformant: all 3,108 HarfBuzz runs match `settle.py` glyph-for-glyph with the gap-0 pen-position check active and every one of the 48 emitted settlement rules exercised at least once, and all 76 CoreText runs match HarfBuzz GID-for-GID and position-for-position. Every finding below is therefore about **today’s shipping font** (`site/AbbotsMortonSpaceportSansSenior-Regular.otf`), surfaced by running the same harnesses against it in mechanics mode. They are genuine shaper-semantics behaviors — not prototype bugs — and each one is structurally fixed or made irrelevant by the prototype’s encoding.

## 1. HarfBuzz and CoreText disagree on the ss03 join across ZWNJ (cross-shaper, load-bearing)

- **Input:** U+E665 U+200C U+E652 (·May ZWNJ ·Tea), ss03 enabled, today’s font.
- **HarfBuzz:** `qsMay.ex-ext-1 | <zwnj> | qsTea.half.en-y5.after-xheight-exit` — the x-height join forms across the ZWNJ.
- **CoreText:** `qsMay | <zwnj> | qsTea.noentry` — no join; the ZWNJ lock wins.
- **Analysis:** today’s ss03 gate (`qsTea.half.en-y5.after-xheight-exit` selected `after` an x-height exit) lacks a ZWNJ guard, and HarfBuzz skips U+200C as a default-ignorable when matching the chained context, so the rule fires across the boundary. CoreText’s context matcher does not skip the ZWNJ the same way (its chokepoint substitution to `qsTea.noentry` lands first), so the two shapers render different glyphs for the same text. This is the known leak from `recon/families.md` §4 row 20, now confirmed to be **cross-shaper divergent**, not merely wrong-but-consistent. It is the single divergence in the 76-run CoreText smoke against today’s font (75/76 agree).
- **Prototype status:** structurally impossible. The unconditional ss03 marker runs after formation and before the chokepoint, the chokepoint converts every entry-bearing glyph after ZWNJ into a locked twin, and locked twins appear in no raw lookahead class — so there is no rule whose context can match across the ZWNJ. Both shapers agree on the prototype (`qsMay | <zwnj> | qsTea`), which also restores split-buffer equivalence. PLAN.md §7 divergence 4 is this fix.

## 2. HarfBuzz split-buffer inequivalence on today’s font (72 informational divergences)

`conform.py --font site/...` (mechanics mode, 3,108 runs) reports 72 runs where shaping a ZWNJ-containing string in one buffer differs from shaping its ZWNJ-delimited halves separately. Three root causes, all confirmed expected:

| Family                     | Example                 | Full buffer                          | Split halves           | Cause                                                                                                      |
| -------------------------- | ----------------------- | ------------------------------------ | ---------------------- | ---------------------------------------------------------------------------------------------------------- |
| ss03 ZWNJ leak (finding 1) | `E665 200C E652` + ss03 | `qsMay.ex-ext-1 … qsTea.half…` join  | `qsMay … qsTea` bare   | GSUB context match skips ZWNJ                                                                              |
| ZWNJ-blocked ligature      | `200C E652 E679`        | `qsTea.noentry`, `qsOy` (2 glyphs)   | `qsTea_qsOy` (1 glyph) | today’s chokepoint locks qsTea before the late `calt_liga`, so the ligature never forms in the full buffer |
| GPOS kern across ZWNJ      | `E679 200C E679`        | qsOy advance 400                     | qsOy advance 450       | pair kerning skips the default-ignorable ZWNJ and applies across the boundary                              |

The leak family accounts for the cascading variants (e.g. `E652 E665 200C E652` + ss03, where the middle ·May’s spurious upgrade also changes the first ·Tea’s cell). **Prototype status:** zero split-buffer divergences in all 3,108 prototype runs. The ligature case inverts deliberately — formation is lookup 0, ahead of the chokepoint, so `ZWNJ qsTea qsOy` forms the (entryless) ligature in both full and split shaping (PLAN.md Deviations item 5). Kerning is omitted from the prototype font entirely, so the GPOS case does not arise; the full rebuild should re-test it once kerning returns.

## 3. Confirmations worth keeping (not divergences)

- **Backtrack-sees-settled holds in both shapers.** Every chained-context settlement rule whose backtrack class names already-substituted cell glyphs matched correctly in HarfBuzz and CoreText across all probed chains (Tea·It·May, May·It·May, It·May·It, May·Tea·It+ss03, Tea·It·Tea·It, and the ligature-withdrawal rows). Within-lookup sequential substitution — the contested semantics this de-risk exists to test — is consistent across both implementations at this scale. K3 does not trip.
- **CoreText honors `kCTFontOpenTypeFeatureTag`/`Value` for ss03** through the CGFont → CTFontCreateWithGraphicsFont route (flagged “not yet exercised” in `recon/shapers.md`; now verified — ss03 changes GIDs).
- **ZWNJ renders as a zero-advance, ink-free slot in both shapers** (HarfBuzz substitutes a space-GID placeholder, CoreText keeps its own placeholder); harnesses must assert zero-advance/no-ink at ZWNJ slots, never compare the slot’s GID or name.
- **DirectWrite remains untested** (see `prototype/directwrite.md` for the windows-latest CI follow-up); the OpenType-spec-mandated within-lookup sequencing is already exercised by today’s shipping font against DirectWrite users.
