# De-risking prototype report

This is the PLAN.md execution-order step 8 deliverable for the week-one prototype from `doc/rebuild-design.md` §7 ("The de-risking prototype comes first"): the settlement emitter for qsIt/qsTea/qsMay plus the qsTea_qsOy ligature, compiled through the §7 transducer encoding, measured against the kill criteria, and cross-shaper tested. Artifacts live under `prototype/out/` (gitignored); the consolidated machine-readable verdicts are in `prototype/out/budget.json`.

## TL;DR verdict

**No kill criterion tripped. The single-lookup primary path survives the prototype — proceed to the full build.** The one measured qualification is recorded under K2: at full-font scale the settlement lookup fits without the Extension escape hatch only while classes stay modest; large-class regimes and the 10,000-rule ceiling need GSUB type 7 Extension promotion, which works but counts against the primary path per PLAN.md §6d.

The pre-registered thresholds (PLAN.md §6d) against the measured numbers:

| Criterion            | Pre-registered threshold                                                                                                              | Measured/projected                                                                                                                                                                                                          | Verdict     |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| K1 — rule count      | projected full-font settlement rules > 10,000                                                                                         | 1,328 projected (59 rules / 3 core families × 45 runes × 1.5); break-even density 148 rules/rune vs. 19.7 measured (7.5× margin)                                                                                            | not tripped |
| K2 — offset headroom | largest per-family subtable > 49,151 B (headroom < 16,384 B), or cumulative > 65,535 B with Extension judged unacceptable             | measured at scale (not extrapolated): 31,271 B subtable headroom at projected rule count with no Extension needed (modest classes); Extension needed for 108-member classes and at the 10,000-rule ceiling, and it compiles | not tripped |
| K3 — semantics       | any cross-shaper divergence on within-lookup sequential substitution or default-ignorable handling not fixable inside the §7 encoding | HarfBuzz: 18,660/18,660 runs match the oracle, 59/59 rules exercised; CoreText: 96/96 runs agree with HarfBuzz; DirectWrite deferred with a written caveat                                                                  | not tripped |

Cross-shaper result in one line: HarfBuzz and CoreText agree with the settlement oracle and with each other on every run, including every backtrack-sees-settled chain and every ZWNJ placement — and the same harness run against today's shipping font finds a genuine HarfBuzz-vs-CoreText divergence that the prototype's encoding structurally eliminates (finding 1 below).

K3 is recorded in `budget.json` by the gates themselves: `conform.py` writes the HarfBuzz half and `coretext_smoke.py` the CoreText half, and the verdict trips if either fails. Summary logs persist at `out/conform_summary.json` and `out/coretext_summary.json`.

An honesty note up front: nothing here was committed to git (the task forbids commits), so pre-registration of the kill numbers is not provable from history. PLAN.md §6d states the thresholds and `budget.json` states the measurements; the thresholds were not edited after measurement, but you have only this report's word for it.

## What was built

A hand-encoded spec of the three worst accretion families (qsIt, qsTea, qsMay) plus the qsTea_qsOy ligature feeds a pure-Python §6.1 settlement oracle; a fixpoint decision-table builder tabulates the oracle over every reachable (settled-left, rune, raw-right¹, raw-right²) window and compresses the 2,436 rows into 59 outcome-partitioned rules; an FEA emitter renders them in the §7 encoding (formation → unconditional ss03 marker → ZWNJ chokepoint → one chained-context settlement lookup whose backtrack classes name settled glyphs and whose lookahead classes name raw glyphs, positive rules only, zero `ignore sub`); the existing pipeline's `build_font` compiles the OTF read-only; and two shaping gates plus a budget/extrapolation harness verify the result. Everything is additive under `prototype/` — no existing pipeline, YAML, test, or site file was touched.

| File                           | Role                                                                                                                                                           |
|--------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `spec.py`                      | Hand-encoded subset facts with per-record provenance (YAML line ranges at commit a7fabef, or the probe that pinned the behavior); two flagged encoding probes  |
| `settle.py`                    | Pure-Python §6.1 settlement oracle, `settle(sequence, features)`, with a 57-row self-check                                                                     |
| `table.py`                     | Exact decision table by fixpoint over reachable left states; outcome-partition compression; E-STRANDED and joint invariants; first-match-wins replay validator |
| `emit.py`                      | FEA emitter: the four-stage GSUB plus per-height `curs` lookups in today's verbatim shape                                                                      |
| `build.py`                     | OTF compile via `build_font(..., senior_fea=...)`, `_report_gsub_budget`, extrapolation, K1/K2 verdicts → `out/budget.json`                                    |
| `conform.py`                   | Exhaustive HarfBuzz gate: 18,660 runs diffed against the oracle, gap-0 pen positions, ZWNJ structural checks, split-buffer equivalence, rule coverage          |
| `coretext_smoke.py` / `.swift` | CoreText-vs-HarfBuzz diff (GID-for-GID, position-for-position) over 48 curated sequences × {default, ss03}                                                     |
| `leak_demo.py`                 | Rebuilds the font with the ZWNJ defenses stripped and proves HarfBuzz then leaks on all three witness shapes                                                   |
| `scale_stress.py`              | Compiles synthetic 1,128-glyph/45-family fonts at projected and ceiling rule counts → `out/scale_stress.json`                                                  |
| `probe_supplementary.py`       | Probes against today's built font whose outcomes are recorded as `spec.py` provenance                                                                          |
| `smoke_sequences.txt`          | The curated CoreText sequence set with per-row rationale                                                                                                       |
| `PLAN.md`                      | The phase-2 plan, §6d kill criteria, and the running deviations register (15 entries)                                                                          |
| `directwrite.md`               | The DirectWrite deferral and the windows-latest CI follow-up design                                                                                            |
| `recon/`                       | Phase-1 recon: pipeline route, family facts, shaper harness verification, and `findings.md` (genuine today's-font findings)                                    |
| `out/`                         | `Proto.otf`/`Proto.fea`, `settlement.tsv`, `budget.json`, `scale_stress.json`, gate summary logs (all gitignored)                                              |

## Measured budget (the prototype font itself)

The `_report_gsub_budget` print, verbatim:

```text
GSUB budget: 1,828 bytes, 12 lookups, 70 subtables
  GSUB offset headroom: LookupList 65,401 bytes, subtable 64,147 bytes in lookup 4
  Largest calt lookups by subtable count: lookup[4]=59, lookup[2]=1, lookup[0]=1
```

| Quantity                            | Value                                                                                                   |
|-------------------------------------|---------------------------------------------------------------------------------------------------------|
| Settlement rules (compressed)       | 59                                                                                                      |
| Settlement rows before partitioning | 2,436 (41.3× compression)                                                                               |
| Two-lookahead-slot rules            | 8                                                                                                       |
| Identity guard rules                | 4 (positive rules; the zero-`ignore sub` claim stands — `grep "ignore" out/Proto.fea` is empty)         |
| ZWNJ `ignore sub` guards needed     | 0                                                                                                       |
| Per-family rules                    | qsIt 22, qsTea 18, qsMay 16, qsTea_qsOy 3                                                               |
| Glyphs                              | 31                                                                                                      |
| GSUB                                | 1,828 B, 12 lookups, 70 subtables                                                                       |
| Settlement lookup                   | 1,426 B over 59 subtables (24.2 B/rule at this scale — see below for why this must not be extrapolated) |
| Offset headroom                     | LookupList 65,401 B; subtable 64,147 B                                                                  |
| FEA                                 | 148 lines                                                                                               |

The §6d secondary indicator (projected settlement share of FEA beyond the ~3–6 k-line envelope) does not trigger: 1,328 projected settlement rule lines sit inside the envelope.

## Extrapolation: 3 families → 45 runes, re-based on at-scale measurement

The original §6d arithmetic multiplied small-font bytes/rule by a projected rule count. The adversarial audit correctly flagged all three inputs as unvalidatable at prototype scale: 24 B/rule comes from a 31-glyph font where fontTools shares the small duplicate coverage tables aggressively; rules-per-rune comes from a closed 6-symbol alphabet; the ×1.5 pessimism factor was asserted. `prototype/scale_stress.py` (PLAN.md deviation 15) replaces the byte arithmetic with measurement at the destination scale and gives the density factor its only available empirical handle.

Assumptions as they now stand:

- **A1 (density):** qsIt/qsTea/qsMay are upper-quartile accretion density; ×1.5 pessimism on top of linear scaling. Empirical handle below.
- **A2 (rule count):** S₄₅ = (59 / 3) × 45 × 1.5 = 1,328. The 59 includes the ligature, marker, locked-twin, and encoding-probe rows, so the per-rune density is overstated, which is the conservative direction.
- **A3 (bytes):** superseded. Bytes are now measured by compiling at scale; the old arithmetic is kept in `budget.json` under `small_scale_arithmetic` marked superseded.
- **A4 (partitioning):** the per-family subtable split is authored in the synthetic lookup the way the real emitter would author it — one `subtable;` break per family — and fontTools picks the format per ruleset.

### At-scale compiles (1,128 glyphs, 45 families, §7 row shape, rule-shape mix copied from the real prototype table)

| Scenario                       | Rules  | Classes                   | GSUB bytes | B/rule | Subtable headroom | Extension needed        |
|--------------------------------|--------|---------------------------|------------|--------|-------------------|-------------------------|
| Projected, partitioned, modest | 1,328  | 60-member, slot-disjoint  | 102,692    | 77.3   | 31,271 B          | no                      |
| Projected, partitioned, large  | 1,328  | 108-member, slot-disjoint | 227,680    | 171.4  | 43,649 B          | yes                     |
| Projected, overlapping         | 1,328  | 60-member, overlapping    | 102,462    | 77.2   | 31,253 B          | no                      |
| K1 ceiling, partitioned        | 10,000 | slot-disjoint             | 384,242    | 38.4   | 56,791 B          | yes                     |
| K1 ceiling, overlapping        | 10,000 | overlapping               | —          | —      | —                 | does not compile at all |

Findings the old arithmetic could never have produced:

1. **Real bytes/rule at scale is 3–7× the small-font figure** (77–171 vs. 24). Had the K2 verdict stayed on the extrapolated 23.9 B/rule, it would have been resting on a number off by that factor. The verdict now rests on the compiles themselves.
2. **The primary path (no Extension) holds at the projected rule count with modest classes**, with 31,271 B of subtable-offset headroom — above the 16,384 B floor. This is the measured K2 basis.
3. **Extension promotion is real but survivable.** Large classes at projected scale, and everything at the 10,000-rule ceiling, need GSUB type 7. fontTools promotes and the fonts compile; per §6d this counts against the primary path and is recorded, not hidden.
4. **The DFA outcome-partition property is load-bearing for the encoding, not just for compression.** A 10,000-rule lookup whose slot classes are not disjoint (i.e., not a partition, which the real table builder guarantees by construction) cannot be expressed as format-2 class subtables, falls back to one format-3 subtable per rule, and overflows unrecoverably — even with Extension. At the ceiling, fontTools chose format-2 per-family subtables (45 subtables for 10,000 rules) precisely because the partition made that expressible.

### Density growth (the empirical handle on A1's ×1.5)

Rules per family as the prototype alphabet grows, measured by rebuilding the decision table over reduced specs:

| Alphabet                       | Rules | Rules/family | Growth |
|--------------------------------|-------|--------------|--------|
| qsIt + qsTea                   | 13    | 6.5          | —      |
| qsIt + qsTea + qsMay           | 39    | 13.0         | ×2.00  |
| full subset (+ qsOy, ligature) | 59    | 11.8         | ×0.91  |

Read honestly: density doubles when the added rune is a dense joiner (qsMay joins both neighbors) and dilutes when the added runes are inert or entryless (qsOy, the ligature). The full alphabet is a mix, and these three families were chosen as the worst accretion offenders, so 19.7 rules/rune is already an upper-quartile estimate before the ×1.5. The ×1.5 remains an assumption — a closed 6-symbol alphabet cannot prove a 45-rune number — but the kill margin no longer depends on it being precise: K1 trips only at 148 rules/rune, 7.5× the measured worst-family density, and K2 was additionally measured at the 10,000-rule ceiling itself, where the encoding still compiles (with Extension).

## Cross-shaper results

- **HarfBuzz** (`conform.py`): exhaustive enumeration, all 9,330 strings of length 1–5 over the 6-symbol alphabet × {default, ss03} = **18,660/18,660 shaping runs match the oracle**. Exact glyph-name match against `settle.py` per position, pen-position gap-0 arithmetic at every settled seam, zero advance and no ink at every ZWNJ slot, split-buffer equivalence for every ZWNJ sequence, and a coverage check proving all 59 rules fired. PASS, no divergences (`out/conform_summary.json`; rerun while writing this report — exit 0).
- **CoreText** (`coretext_smoke.py`): 48 curated sequences × 2 configurations = **96/96 runs agree with HarfBuzz** through the recon-verified CGFont/CTLine route, per-run resolved PostScript name asserted against silent fallback, GID-for-GID and position-for-position diff after unit conversion. Includes every backtrack-sees-settled chain, every ZWNJ row, and all encoding-probe rows (`out/coretext_summary.json`; rerun while writing this report — 96/96). `kCTFontOpenTypeFeatureTag`/`Value` ss03 plumbing verified working.
- **DirectWrite**: not run — there is no Windows machine in this environment, and Wine's dwrite is a reimplementation that proves nothing (`recon/shapers.md` §3). The honest caveat: within-lookup sequential substitution is OpenType-spec-mandated behavior that the shipping 30,838-line font already exercises against DirectWrite users daily, and HarfBuzz + CoreText are two independent implementations agreeing on every contested semantic. The concrete follow-up that closes the gap: a `windows-latest` CI job running a ~60-line harness around `IDWriteTextAnalyzer::GetGlyphs` / `GetGlyphPlacements`, asserting the same GID/position table as the CoreText harness, with the ZWNJ lock-fires-join-doesn't and every-rule-slot probes (now including the `leak_demo.py` witness shapes) as the priority assertions. Details in `prototype/directwrite.md`.

### ZWNJ coverage at every slot of the §7 row shape

The §7 encoding demands ZWNJ coverage at the backtrack, first-lookahead, and second-lookahead slots. The natural subset satisfied the last two only vacuously (zero two-slot rules; no backtrack-classed rule could face an adjacent ZWNJ), which the audit correctly refused to count. Two synthetic encoding probes (PLAN.md deviation 13, flagged with `synthetic encoding probe` provenance in `spec.py`) close this for real:

- **Probe 1** — a window-decidable right-square refusal (§6.1.3 shape) produces 8 genuine two-slot rules, including locked-input and backtrack-classed ones, each with the boundary row's explicit `uni200C` at the second slot ordered first.
- **Probe 2** — a settled-pair cell on the never-locked ligature (§7's "small settled-pair substitution stage" shape, §9's collision-refuse promotion target) produces a backtrack-classed rule on a glyph that can legitimately sit immediately after ZWNJ, defended by `sub uni200C qsTea_qsOy' by qsTea_qsOy;` ordered first.

Both probes deliberately involve qsOy/qsTea_qsOy — the runes the chokepoint never locks — so the locked-twin emitter invariant cannot mask a ZWNJ-skipping shaper. And the defenses are proven load-bearing, not decorative: `prototype/leak_demo.py` rebuilds the font with the guards stripped and HarfBuzz then leaks on all three witness shapes (settles `qsTea_qsOy.after-it` across `It ZWNJ Tea Oy`; drops the `It May ZWNJ …` boundary join by skipping to the qsOy beyond the break; withdraws the entered ·It in `Tea It May ZWNJ Oy`). With the guards in place, 18,660 HarfBuzz runs and 96 CoreText runs agree with the oracle, ZWNJ at every buffer position to length 5.

The chokepoint itself (`sub uni200C @proto_entry_live' by @proto_entry_locked`) is today's proven shape unchanged. Zero generated `ignore sub` guards were needed; the four identity guards are positive rules and are counted in `budget.json`.

## Genuine findings and surprises

The prototype itself ended fully conformant, so every shaper-semantics finding is about **today's shipping font**, surfaced by running the same harnesses against it in mechanics mode. Full detail with inputs and glyph sequences in `prototype/recon/findings.md` (its run counts reflect the pre-probe harness configuration under which the findings were gathered).

1. **HarfBuzz and CoreText disagree on today's ss03 join across ZWNJ** (`E665 200C E652` + ss03): HarfBuzz fires the x-height join across the ZWNJ (`qsMay.ex-ext-1 … qsTea.half.en-y5.after-xheight-exit`) because it skips the default-ignorable during chained-context matching and the ss03 gate has no ZWNJ guard; CoreText's chokepoint wins and yields no join (`qsMay … qsTea.noentry`). This proves the known row-20 leak is **cross-shaper divergent**, not merely wrong-but-consistent — and it is the strongest evidence that the prototype's marker-then-chokepoint ordering is load-bearing: both shapers agree on the prototype, where the leak is structurally impossible.
2. **72 HarfBuzz split-buffer inequivalences on today's font**, three root causes: the ss03 ZWNJ leak family above; ZWNJ-blocked ligature formation (today's chokepoint locks qsTea before the late `calt_liga`; the prototype's formation-first ordering inverts this deliberately and is split-buffer equivalent); and **GPOS pair kerning applying across ZWNJ** (`E679 200C E679`: qsOy advance 400 in the full buffer vs. 450 split). Kerning is outside the prototype — flagged for re-test in the full rebuild.
3. **Confirmations worth keeping**: backtrack-sees-settled within-lookup sequencing is consistent across HarfBuzz and CoreText at this scale (the contested semantic this de-risk exists to test); CoreText honors `kCTFontOpenTypeFeatureTag` for ss03 through the CGFont route; ZWNJ slots must be asserted by zero-advance/no-ink, never by GID or name (each shaper substitutes its own placeholder).
4. **From the scale stress, the surprise finding**: the outcome-partition property is not just a compression win — without slot-disjoint classes, a ceiling-scale lookup does not compile at all (extrapolation finding 4 above). The partition the table builder guarantees by construction must be preserved as a build invariant in the full rebuild.

### Divergence register (intended differences from today's font)

From PLAN.md §7 — these are design decisions, not test failures; `conform.py`'s oracle is `settle.py`:

1. **It·It**: first ·It settles bare instead of carrying today's benign dangling ex-y5 (lookahead closure; `E-STRANDED` semantics).
2. **Tea·It·Tea / Tea·It·Tea·It**: entered ·It before an entryless follower settles exit-withdrawn instead of keeping the dangling baseline anchor.
3. **May·It·May**: same-seam extensions don't sum; the right seam matches Tea·It·May. Today's both-sides-summed outcome is a cascade-order artifact.
4. **qsMay ZWNJ qsTea under ss03**: no join across ZWNJ — today's confirmed leak (finding 1), fixed structurally by the unconditional marker plus chokepoint ordering. The one row the prototype is required to change.
5. **qsMay qsTea qsOy under ss03**: the ligature forms — markers are staged after formation so a set cannot un-form a ligature.

Plus the deviations register in PLAN.md (entries 1–15), of which the user-visible ones beyond the five above are: generalized exit withdrawal before entryless followers (deviation 2), ss03 chains gaining joins under window join-count (3), qsOy modeled inert (4), `ZWNJ qsTea qsOy` forming the ligature (5), and the two synthetic encoding probes (13) — every It·May·Oy window leaves ·It bare, and qsIt-then-ligature renders the bitmap-identical `after-it` twin.

## Honestly out of scope (PLAN.md §3, verbatim)

- **Absolute and yielding prefers** (§6.1.4.1, 4.3) and the full **extensional specificity order** (§6.2): the subset has no competing prefer records; the few narrow-vs-broad overlaps (contract-vs-extend style) are resolved by hand in `spec.py`.
- **`E-INCOMPARABLE` / `E-AMBIGUOUS` arbitration and `resolve` records** (§6.2): nothing in the subset produces them.
- **Positive `word: final` records** and the substitute-then-revert reserve encoding (§7 table row "Word position") — only fallback-row ordering is exercised.
- **Multi-set composition and composite markers**: ss03 is the only set with any effect inside the subset (`recon/families.md` §2), so the `{default, ss03}` matrix is complete but proves nothing about union semantics.
- **Bound shapes / `bind:`** (the qsOut_qsTea after-·See case) — excluded with the ligature choice.
- **Late formation on settled classes** (§7's named contingency) — formation here is unconditional and first.
- **The §9 defect suite** beyond `E-STRANDED` and gap-0 spot checks in `conform.py` (no `E-DANGLE`, `E-UNREALIZED`-as-gate, off-anchor contact, collision detection).
- **Kerning, ss06/ss10 taste overlays, the namer dot, mark/ccmp** — omitted from the prototype font entirely.

What §3 lists as exercised by real data is correspondingly narrow but real: entry binding, lookahead closure (including predecessor withdrawal before the entryless ligature), absolute refusals, allowlist polarity, window join-count, the structural tie-break floor, `E-STRANDED` as a table invariant, and boundary semantics via run splitting plus the ZWNJ lock — each with a named witness sequence in PLAN.md §3.

### Naming-parity note

The subset touches attachment heights 0, 5, and 8, so three per-height `curs` lookups are emitted; y6 has no members and the lookup is omitted. This is a naming-parity detail against the full design's four-lookup table, not a deviation.

## Remaining honest weaknesses

- **DirectWrite was named in the §7 matrix and was not run.** Two of three shapers is the strongest evidence obtainable on this machine; the CI follow-up above is the close-out.
- **The two-slot and backtrack-ZWNJ challenges run on synthetic records.** They use sanctioned §6.1/§7 record shapes and the leak demo proves they exercise the real leak class, but the full font's organic two-slot rows may have shapes the probes do not anticipate (e.g., wider second-slot classes interacting with kerning-era glyphs).
- **A1's ×1.5 is still an assumption.** The density-growth curve and the 7.5× break-even margin bound the risk; they do not eliminate it.
- **The per-family format-2 subtable valve was only exercised where fontTools chose it** (the 10,000-rule ceiling). At projected scale fontTools preferred format 3 per rule; both fit, so nothing binds, but the format-2 path at intermediate scales is untested.
- **Pre-registration is unprovable** without commits, as noted at the top.

## Recommended next step

**Proceed to the full single-lookup rebuild.** No kill criterion tripped, the margins are wide (7.5× on K1 density, roughly 2× on K2 headroom at projected scale), and the contested semantics — backtrack-sees-settled and default-ignorable handling — held across two independent shaper implementations on every run. The two-strata staged-wave fallback stays in reserve but nothing measured here motivates it.

Carry these follow-ups into the full build, in priority order:

1. **Preserve the outcome-partition property as a hard build invariant.** The scale stress shows non-disjoint slot classes make a large settlement lookup unexpressible, not merely bigger. The full table builder must assert the partition the way `table.py` does, and the budget gate should fail the build if fontTools falls back to per-rule format-3 subtables past the headroom floor.
2. **Keep classes modest and watch Extension promotion in the budget gate.** The primary path holds at 60-member classes; 108-member classes already need GSUB type 7. Re-measure with the organic class sizes the real alphabet produces, and treat unplanned Extension promotion as a yellow flag, not a silent fallback.
3. **Stand up the DirectWrite CI harness** (`windows-latest`, `IDWriteTextAnalyzer::GetGlyphs`/`GetGlyphPlacements`) before the full rebuild ships, with the `leak_demo.py` witness shapes and the every-rule-slot probes as the priority assertions (`prototype/directwrite.md`).
4. **Re-test GPOS kerning across ZWNJ once kerning returns** (finding 2): today's pair kerning applies across the boundary; the full rebuild must decide and test the intended behavior.
5. **Bring the unexercised §6.1 semantics under the same oracle-plus-conformance discipline**: prefers and the extensional specificity order, `E-INCOMPARABLE`/`E-AMBIGUOUS` arbitration, positive `word: final` with the substitute-then-revert reserve encoding, multi-set composition, and `bind:` each need their own witness families before the full table is trusted.
6. **Exercise the format-2 per-family subtable valve at intermediate scales** (between 1,328 and 10,000 rules) so the size valve §7 relies on is validated where the full font will actually live.
