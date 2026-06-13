# M1 batch 2 — qsDay + qsUtter + qsNo: in-progress checkpoint

Status as of the pause: **all hard gates green; oracle not yet clean (2,535 unmatched rows of 128,832 compared, down from 6,189).** Nothing committed. This note is the resume point — read it, re-run the oracle, and continue from the "Remaining work" section. The approved plan is `~/.claude/plans/crystalline-sprouting-ripple.md`.

## Session update (2026-06-13): 6,189 → 2,535, the residue is verdict-gated

A triage session drove the oracle from 6,189 to 2,535 unmatched with **zero regressions** (every remaining unmatched window touches a new letter; matched-class counts only grew; the lone reviewed-rejected class that shrank did so because its rows *resolved*, and a seam-loss+seam-gain row always classifies as regrouping-floor-drift so it can never go unmatched). 0 multi-matched, 0 defect-gate errors, `make test` green.

Resolved this session:

- **ss10 isolation overlay (−2,823), `intended`.** The old shipped font's ss10 feature predates qsDay/qsNo/qsUtter and never isolated them, so it keeps drawing their joins under ss10 (verified: `qsMay|qsDay`, `qsNo|qsNo` carry y5 seams under ss10 while every existing|existing pair breaks), whereas the new model isolates every letter by design. New `ss10_isolation_completed` predicate (`conform.py`) + ledger entry; the predicate requires every lost seam to neighbor a new rune so an existing|existing ss10 regression would still fail.
- **qsDay_qsUtter ligature receivers (fix-to-match).** Taught qsPea (full+half), qsOy (loop), qsTea (half: an `except` on the family-grained `halves-that-exit-at-x-height` class + the ss03 unlock) to accept the ligature's exit exactly as they accept plain ·Utter — the ligature's left-context family match did no trailing-component expansion. Plus routed the post-marker qsDay_qsUtter formation windows to the ratified `marker-staging-ligature-formation` class (the old font forms the bare ligature in every config; only post-ZWNJ/namer-dot windows diverge).
- **ss04 entered-·It over-extension (fix-to-match).** Scoped qsIt's entered-exit baseline extend to x-height-entered ·It only (`left: {joined_at: x-height}`); the old font extends ·It's baseline exit iff ·It entered at the x-height, never on the en-y0 ss04 pass-through. + 21 contact-allow blessings, each verified against a real baseline join.
- **Two changes the triage agents pre-applied, reviewed and kept:** `zwnj-word-initial-seam-moved` (`drift-accepted`, 116 post-ZWNJ ·Utter·May seam-moves — the old `.noentry` shadow joined ·May at y5, word-initial settlement at y0; needs a verdict like `zwnj-follower-exit-restored`), and a `compare_against_baseline` position-logic fix preserving an ink-identical cell match against kern-attributable drift (188 rows).

**The remaining 2,535 (645 unique windows) are verdict-gated** — new joins the engine makes that the old font did not, the same kind of taste calls rounds 1–2 settled. Decision (2026-06-13): **checkpoint, then build the round-3 review surface and present all families fresh (no fast-tracking look-alikes).** The families, by unique-window count:

| Verdict family | rows | uniq |
|---|---|---|
| ·No-chain join-maximizer gains (flip-to-baseline, loop·Oy, It-both-sides) | 971 | 157 |
| Withdrawal / seam-loss — ·No flipped exit, ·Utter chain-flip (**context-dependent / engine-limited**) | 599 | 96 |
| ·May gains (·May·Utter reach-back, post-ZWNJ ·Utter·May·X) | 174 | 75 |
| ·Tea·It x-height join before ·Day/·Utter (resembles entered-it-baseline-join-gain) | 160 | 43 |
| ·Utter gains (reach-back into ·May; ss03 ·Utter·Tea ≈ ss03-chain-join-gains) | 160 | 52 |
| ss04 Group A — x-height-exiting lead lowered (the deferred ss04-semantics design question) | 138 | 111 |
| ·Oy·It baseline join before ·No (strict +1 join) | 127 | 20 |
| Extension non-summing / Tea·Oy·Day extension drop (lead-name artifact) | 105 | 15 |
| ss10 residue + ss04 ligature decline + misc | ~100 | ~90 |

Two structural notes carried forward: the **·No flipped-exit / ·Utter chain-flip family is the same depth-2 / cross-side inexpressibility** as the parked round-2 extension-suppression milestone (a flat `toward` list can't say "·No joins ·Tea only when ·Tea has no live forward join"), and **ss04 Group A** is exactly the design question M1-REPORT §11.3 deferred to this migration.

Triage evidence: per-rune findings under `tmp/findings/*.md`; the probe `tmp/probe.py` (`PYTHONPATH=. uv run python tmp/probe.py <cps>`); current unmatched dump `tmp/unmatched_after_fixes.json`.

### Still-pending wiring — KNOWN-FAILING, not regressions

`uv run pytest rebuild/` shows 5 failures + 59 errors, all pre-existing checkpoint state, NOT introduced this session — do not mistake them for regressions:

- `test_spec_load.py::test_loads_all_six_runes` (asserts 6 runes; 10 exist), `::test_group_resolution` (expects qsDay in `utter-pass-through-vetoes`; the Q2 fix dropped it), `::test_predicate_class_membership` (the qsDay_qsUtter ligature's half stance enrolled in `halves-that-exit-at-x-height` — the pollution the qsTea `except` works around; decide whether a ligature should be in that class rather than just updating the assertion).
- `test_surface.py::test_real_cell_bindings_all_match` — a real `qsUtter.yaml` flipped-cell warning (`cells[1]` x-height/baseline-withdrawn matches no enumerable cell) worth investigating, not silencing.
- The 59 `test_review_*` errors are `build_units` refusing to build the review surface while the oracle is dirty (a triple matched in ss03 but UNMATCHED in default). These self-resolve once the surface handles the multi-class-by-config case / the oracle is clean.

Decision (2026-06-13): leave these for batch-close — they don't impair the oracle-based regression signal used for verdict work, and several are entangled with real modeling questions. The earlier-listed items (`test_conform.py::test_eight_symbols`, `fixtures.py::mini_spec`, `test_baseline_subset.py`) remain part of that batch-close pass.

## What this batch is

Migrate qsDay (0xE653), qsUtter (0xE67A), qsNo (0xE666) into rune files, plus the now-formable `qsDay_qsUtter` ligature, validated against the old font via the oracle. The two recorded expressiveness questions were resolved **without engine work** (as the plan predicted): Q1 (ss04 before-·Utter null-derives) is a latent in-grammar gap that produces no in-alphabet divergence; Q2 (utter-pass-through-vetoes widening) is faithfully expressible via `left: {stance: half}` — both done.

## Files changed (all additive or rebuild-internal; old font untouched)

| File | Change |
|---|---|
| `glyph_data/runes/qsDay.yaml` | NEW — full + half stances (deep, y_offset −3). |
| `glyph_data/runes/qsNo.yaml` | NEW — loop + flipped (alt) stances; the §5.3 prefer; a cell-grain loop-x-height prefer. |
| `glyph_data/runes/qsUtter.yaml` | NEW — mono + flipped stances; the reaches-way-back cell; `require: [exit]` on flipped. |
| `glyph_data/runes/qsDay_qsUtter.yaml` | NEW — ligature (`sequence: [qsDay, qsUtter]`), full + half. |
| `glyph_data/runes/qsIt.yaml` | Q2 fix: added a `{stance: bar, exit: baseline, when: {self:{entry:live}, left:{family:qsDay, stance:half}, right:{family:qsUtter}}}` refuse; dropped `{family: qsDay, trait: half}` from `groups.utter-pass-through-vetoes`. |
| `rebuild/pipeline/baseline_subset.py` | `M1_ALPHABET` widened 8→11 (added 0xE653/0xE666/0xE67A). **Gate-12 evolution** — see below. |
| `rebuild/m1-aliases.yaml` | +30 alias entries (the new letters' old glyph names + qsIt/qsMay context-variants that surface now). |
| `rebuild/m1-contact-allow.yaml` | +72 blessings at the checkpoint, +21 this session (ligature-receiver + ss04 un-extended ·It corners) = 93. |
| `glyph_data/runes/qsPea.yaml` | Session: +`qsDay_qsUtter` in the full and half x-height entry from-scopes (ligature receiver). |
| `glyph_data/runes/qsOy.yaml` | Session: +`qsDay_qsUtter` in the loop x-height entry from-scope. |
| `glyph_data/runes/qsTea.yaml` | Session: `except: [{family: qsDay_qsUtter}]` on the half x-height `halves-that-exit-at-x-height` from-class + `qsDay_qsUtter` in the ss03 x-height unlock. |
| `glyph_data/runes/qsIt.yaml` | Session: scoped the entered-exit baseline extend (line 78) with `left: {joined_at: x-height}`. |
| `rebuild/pipeline/conform.py` | Session: `ss10_isolation_completed` predicate; `marker-staging-ligature-formation` routing for `E653:E67A`; `zwnj-word-initial-seam-moved` routing; position-logic preserving ink-identical matches against kern-attributable drift. |
| `rebuild/m1-divergences.yaml` | Session: `ss10-isolation-completed` (intended) and `zwnj-word-initial-seam-moved` (drift-accepted) ledger entries. |

After editing rune files always re-run `uv run python -m rebuild.pipeline.baseline_subset` is **not** needed; only re-run it if `M1_ALPHABET` changes. The build+oracle command is `uv run python -m rebuild.pipeline.run_m1` (writes `rebuild/out/m1/oracle_summary.json` and `divergence-audit.tsv`).

## Still-pending wiring (NOT yet done — needed before the gate suite passes)

- `rebuild/test_conform.py::test_eight_symbols` still asserts the old 8-symbol alphabet — rename/retune to 11, or point it at the real loader (see `fixtures.py:1` "swaps this module for the real loader").
- `rebuild/pipeline/fixtures.py::mini_spec` is hand-built with only the 6 M1 families; the conform unit tests use it. Decide: extend it, or migrate those tests to `load_default_spec`.
- `rebuild/test_baseline_subset.py` membership asserts against `M1_ALPHABET` — review.
- **Gate-12 evolution**: M1-PLAN gate 12 ("no existing pipeline file modified") is an M1-milestone freeze; a post-M1 batch necessarily edits `baseline_subset.py` + the conform test to grow the alphabet. Record this explicitly when finalizing (this note + the eventual report).
- Ductus prose and per-row `stroke:` orientations on all four new runes carry `# DRAFT — pending author sign-off` (qsDay/qsUtter had no source ductus; qsNo's `loop` is verbatim, `flipped` is drafted). Enumerate them for the author at sign-off.

## Methodology learnings (read before continuing — these cost real iterations)

1. **The old `select` semantics are subtler than "not_after = refuse."** Worked examples where a literal reading was wrong: qsDay `half.not_after [qsTea, qsYe, qsWay]` does NOT break ·Tea·Day — the baseline *joins* ·Tea·Day at the baseline/half **with the entry extension** (`not_after` + `extend_entry_after` together mean "always extend after these"; only qsWay, which has no paired extend, actually breaks). **Always check the baseline before encoding a select list as a refuse.**
2. **Check the seam column, not just the glyph names.** `qsMay|qsNo` (bare|bare) is a *join at y5* (bare-name-live-join), not a break. Misreading bare-name joins as breaks caused two wrong-direction fixes.
3. **Lead vs. entered stance choice.** A two-stance letter (qsNo loop/flipped, qsUtter mono/flipped) behaves differently as a word-initial *lead* (chooses its stance by join-count + floor) vs. *entered* (its entry from-scope already picked the stance). The floor prefers the *lower* seam (baseline), which fought the old font's preference for the x-height join on several ·No-lead pairs — resolved with a cell-grain `prefer {cell:{exit:x-height}, over:{exit:baseline}}` (scoped `left: except qsIt` to avoid an E-AMBIGUOUS clash with the §5.3 flipped-after-·It prefer).
4. **`require: [exit]` / `require: [entry]` gate a stance on a live side.** Used `require: [exit]` on qsUtter flipped so the reach-back only fires when ·Utter continues forward (·May·Utter·No joins; bare ·May·Utter breaks). Beware: ·No flipped is used word-initially with a *dangling* (not live) entry, so `require: [entry]` there was WRONG.
5. **Withdrawal bindings, not `withdrawal: safe`, for any non-vertical exit.** `verify_withdrawal_safe` requires the declined exit's terminal ink to continue vertically; diagonal/horizontal exit limbs (·No, ·Day hook, ·Utter, the ligature) need a named `withdrawal:` binding + a pulled-back `bitmaps:` sibling. Mid-word declines then classify as `may-exit-withdrawal-generalized` (the classifier matches any `+ex-bind-`, conform.py:620 — general, not qsMay-specific); boundaries keep the base drawing.
6. **Data-driven scoping is the fast path.** Querying the baseline subset for "which predecessors join X" / "the 2-letter seam map" resolved scopes far faster than reasoning from the design. The definitive 2-letter seam map for the new letters is in the appendix below — use it.

## Remaining work — superseded by the session update above

The original A/B/C bucket analysis below has been **largely resolved** by the 2026-06-13 session; it is kept for the methodology it records, but the live remaining work is the **verdict pile in the session update at the top of this file**, not these buckets.

### A. ss10 isolated-overlay — RESOLVED (was 2,926). The `ss10_isolation_completed` ledger entry (intended) absorbed 2,823; the diagnosis was *not* a classifier gap on the new font but the old font's ss10 never isolating the new letters. ~76 ss10 rows (position/extension residue) remain, folded into the verdict pile's "ss10 residue".

### B. ss04 pass-through — PARTIALLY RESOLVED (was 770). The ·It entered-exit over-extension (Group B, seams already agreed) is fixed via the qsIt line-78 scoping. The remainder is **Group A** (x-height-exiting lead lowered under ss04, 111 unique) — a genuine verdict/design question (does ss04 flatten the chain or keep the natural join?), plus 2 ss04 ligature-decline windows.

### C. Default / ss02 / ss03 / ss05 join-shaping tail — Day·Utter ligature joins RESOLVED (the ligature-receiver fixes + marker-staging routing). The rest is the verdict pile: ·No-chain gains, ·Tea·It / ·Oy·It / ·May·Utter gains, etc. The original adjacent-pair seeds below are stale; trust the verdict table at the top.

**Judgment call still open**: some of these may be acceptable drift (like the existing `regrouping-floor-drift` / `*-join-gains` classes) rather than bugs. Adjudicate per-window; don't assume everything must reach old-font-identical.

## How to re-run and interpret

```sh
uv run python -m rebuild.pipeline.run_m1            # build + oracle; exit 1 while oracle fails
uv run python -c "import json; d=json.load(open('rebuild/out/m1/oracle_summary.json')); print(d['pass'], d['unmatched'], d['multi_matched'])"
```
PASS = `unmatched == 0 and multi_matched == 0`. Per-row detail in `rebuild/out/m1/divergence-audit.tsv` (columns: config, codepoints, kinds, matched_entry, baseline, new); `matched_entry == 'UNMATCHED'` are the rows still to resolve. The baseline subset tables are `rebuild/out/m1/baseline-<config>.subset.tsv.gz` (TSV: codepoints, glyph-names `|`-joined, advances, **seam column**, positions). The seam column is column index 3 — check it, don't infer joins from bare names.

A new divergence class needs: a `predicate` function in `rebuild/pipeline/conform.py` (see `classify_divergence`) **and** an entry in `rebuild/m1-divergences.yaml` (`id / status / match{predicate, configs} / why`; count is filled by the run). Off-anchor contacts on new joins get blessed in `rebuild/m1-contact-allow.yaml` (signature `contact:<left>:<right>:y<row>`, baseline-citing why).

When the oracle is clean: run the full gate suite (`make test`, `uv run pytest rebuild/ -n auto --dist worksteal`, `make prettier` + black, markdownlint), confirm gate-1 byte identity (`make all` + the pinned shasum), invoke `run_font_conformance` (gate 9; not called by `run_m1.main`), write the batch report, and update `WHATNEXT.md`.

## Appendix — definitive 2-letter seam map for the new letters (from baseline-default)

`break` = no join; `yN` = seam height; `lig` = forms the ligature. This is ground truth; the rune scopes were derived from it.

```
Pea.Day  y0 (Pea→Day.half baseline)      Day.Pea  break        No.Pea  break        Utter.Pea  y5 (mono→Pea x-h)
Pea.No   y5 (Pea.half→No.loop x-h)       Day.Tea  y0           No.Tea  break        Utter.Tea  break
Pea.Utter y0                             Day.Day  y0 (→half)    No.Day  y5 (loop)    Utter.Day  y5 (mono→Day x-h)
Tea.Day  y0 (Tea→Day.half, en-ext-1)     Day.May  y0           No.May  y0 (flipped) Utter.May  y0 (flipped→May)
Tea.No   y5 (Tea.half→No.loop)           Day.No   y0 (→flipped) No.No  y5 (loop)     Utter.No   y5 (mono→No.loop)
Tea.Utter y0                             Day.It   y0           No.It   y5 (loop)     Utter.It   y5 (mono→It x-h)
                                         Day.Oy   break        No.Oy   y5 (loop)     Utter.Oy   y5 (mono→Oy x-h)
It.Day   y0 (It→Day.half)                Day.Utter lig         No.Utter y0 (flipped) Utter.Utter break
It.No    y0 (It→No.flipped)              May.Day  y5 (bare-live) Oy.Day y0           
It.Utter y0 (It→Utter.mono baseline)     May.No   y5 (bare-live) Oy.No  y0 (→flipped)
                                         May.Utter break        Oy.Utter y0
```

Key derived rules now encoded: qsNo loop x-height exit is receiver-gated (unscoped); qsNo flipped baseline **entry** is summoned by `[qsDay, qsVie, qsIt, qsOy, qsNo, qsUtter, qsTea_qsOy]` (joined_at baseline); qsNo flipped baseline **exit** toward `[qsDay, qsMay, qsNo, qsIt, qsOy, qsUtter]` (excludes Tea/Pea); qsUtter mono x-height exit is refused before qsMay (forcing the flipped baseline join); qsUtter reach-back `from: [qsMay, qsFee]` with `require: [exit]`.
