# Baseline extraction report (§13.1)

## TL;DR

The §13.1 migration baseline exists, is validated, and is fit to serve as the §13 migration oracle. The depth-2 basis — every string of length 1–4 over the 47-symbol alphabet (44 runes + space + ZWNJ + namer dot), 4,985,760 strings per configuration — was shaped black-box through the current built Senior Sans font (SHA-256 `3211a7a7…25cf35`, repo SHA `ae9d08d`) across 11 stylistic-set configurations, recording 54,843,360 resolved-outcome rows total. Extraction took 272.7 s wall on 10 workers (≈25 s per configuration, far inside the ≤20 min/configuration budget). Tables live under `rebuild/out/` (gitignored): ≈42 MB gzipped each, 0.46 GB gzipped total, ≈554 MB uncompressed per table, with per-configuration SHA-256 digests and seam histograms in `rebuild/out/digests.tsv` and a narrative `rebuild/out/SUMMARY.md` as the diff-stable shape artifact.

Validation is clean on every axis: corpus-pin replay 530/530 with zero disagreements, byte-level determinism proven (full double extraction for the default configuration, sampled for the rest), the split-buffer cross-check behaves as the seam model predicts, and an adversarial audit re-derived the numbers independently (verdict below). The intended-equivalence triage (§3.4) recorded 39,371,623 divergence rows — today's font genuinely does not treat post-ZWNJ like word-initial, and the baseline now quantifies exactly how, as triage rows rather than silent changes. Verdict: **go** — proceed to ductus + first-rune migration.

## The basis and its accepted incompleteness

The basis is defined in `rebuild/BASELINE-PLAN.md` and realizes the §6.1 window `[resolved-left, self, raw-right, raw-right²]`:

- **Alphabet (47 symbols):** the 44 Quikscript runes (U+E650–E66C, U+E670–E67E), space, ZWNJ (U+200C), and the namer dot (U+00B7). The full alphabet membership of space, ZWNJ, and the dot means boundary tokens appear at every slot, so §3.4's `is: boundary | space | zwnj | namer-dot` refinements are all observable.
- **Input set:** all strings of length 1–4, each shaped in a fresh HarfBuzz buffer so run edges are implicit at both ends: 47 + 47² + 47³ + 47⁴ = 4,985,760 strings per configuration, in canonical (length, codepoints) order. No window-keyed dedup — dedup would presuppose the locality the baseline exists to measure.
- **Configurations (11):** `default`, `ss02`–`ss07`, `ss10`, plus the declared multi-set combinations `ss02+ss03`, `ss06+ss07`, `ss02+ss03+ss05`.
- **Recorded per row:** resolved glyph names (recovered via `TTFont`, immune to HarfBuzz's 63-byte name truncation), clusters, per-seam classification (join height y0/y5/y6/y8, ligature, or break — derived from per-height GPOS cursive-lookup intersection), and per-glyph positions.

Accepted incompleteness, by design: a resolved-left state that only arises after two or more settled joins appears in this basis with at most one symbol of raw-right lookahead; giving it both lookahead symbols would need length-5+ strings. Likewise, ligature-as-left or ligature-as-self windows with full two-symbol lookahead need length-5 strings. Neither is this artifact's job — §10's per-transition conformance gate is the designed closure (it derives a shortest example sequence per decision-table transition, including sequences longer than any fixed sweep depth). The corpus-pin replay quantified this horizon concretely: 42 depth-2-horizon findings across the Manual corpus, recorded as report-only rows in `rebuild/out/pin-disagreements.tsv`, not failures.

## Validation results

| Check                    | Result                                                                                                                                                                                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Corpus-pin replay        | **530/530 replayed runs agree, zero disagreements** (96.8 s). Of 607 corpus runs: 530 replayed (default 521, ss02 3, ss03 1, ss04 2, ss05 1, ss07 1, ss10 1), 3 skipped junior, 74 skipped non-basis Latin, 0 config-not-covered                                |
| Replay assertions        | 1,665 seam + 2,351 identity assertions checked; 343 length-≤4 baseline rows byte-identical; 601 embedded length-4 windows cross-checked in longer runs; 42 depth-2-horizon findings (report-only); 18 variant assertions skipped by design (need compiled YAML) |
| Determinism              | Default configuration extracted twice in full: byte-identical, 553,891,899 bytes, SHA-256 equal to the canonical run (`a503b2d1…`), 50.7 s for the pair. Remaining 10 configurations: sampled determinism (20,000 rows, 7 vs 3 workers), all byte-identical     |
| Split-buffer cross-check | 61,178 disagreement rows (`rebuild/out/split-check-disagreements.tsv`), ≈5,850–6,409 per configuration (ss10: 1,519), with ≈18k kern-only flank differences per configuration tallied separately as the legitimate GPOS kern channel (32.9 s)                   |
| Repo suite               | `uv run pytest rebuild/` 35 passed; `make test` 6,753 passed; `make prettier` clean; Senior Sans OTF SHA-256 unchanged after the test suite's internal rebuild, so the oracle font was never perturbed                                                          |

The split-buffer disagreements are not extractor bugs: a hand-verified sample confirmed they are genuine shaping differences between whole-run and split-at-seam shaping in today's font (e.g., the `periodcentered.lowered` isolation leak, where the namer dot lowers only when shaped in the same buffer as a following rune).

## Equivalence-triage findings

§3.4 asserts that post-ZWNJ shaping is _intended_ to equal word-initial shaping, while admitting today's hand-maintained alignment is incomplete. The triage measured exactly how incomplete: 39,371,623 divergence rows across the 11 configurations (`rebuild/out/equivalence-triage.tsv`, 6.19 GB; per-configuration counts in `SUMMARY.md`). For the default configuration:

| Check         | Divergent rows | Reading                                                                                       |
| ------------- | -------------- | --------------------------------------------------------------------------------------------- |
| zwnj-vs-edge  | 3,794,493      | Glyph identity after literal ZWNJ differs from run edge — the `.noentry` universe (see below) |
| space-vs-edge | 44,524         | Glyph identity after a space differs from run edge — the boundary-guard asymmetry class       |
| edge-vs-zwnj  | 106,080        | Position-only: kerning fires against the literal `uni200C` glyph before ZWNJ                  |
| edge-vs-space | 0              | Trailing space ≡ run edge holds universally in today's font                                   |

The most instructive examples, each re-confirmed by hand with raw uharfbuzz (no `rebuild/` library code):

- **ZWNJ + ·Pea**: after a literal ZWNJ, ·Pea resolves to `qsPea.noentry`; at a run edge or after a space it is bare `qsPea`. The font fires `.noentry` against the literal `uni200C` glyph for essentially every rune — including derived stances (`qsRoe.en-y0.ex-y5` → `qsRoe.en-y0.ex-y5.noentry`) — which is why this single mechanism accounts for ~3.8M of the ~3.9M rows.
- **·Excite·Tea**: at a run edge the pair joins at the baseline (`qsExcite.en-y0.ex-y0.before-vertical` | `qsTea.en-y0`), but after a space it breaks to bare `qsExcite` | `qsTea` — the §3.4 boundary-guard asymmetry, where ·Excite's guard treats a space differently from a run edge.
- **·He before ZWNJ**: position-only — ·He's advance drops from 200 to 50 because a kern rule targets the literal `uni200C` glyph.
- **·Day·Utter under ss10**: the ligature loses its `.half.en-y0.ex-y5` form after a space, breaking to the bare components.

A fixed-seed 27-row even-stride sample (`rebuild/tools/eyeball_triage.py`) was re-shaped with a fresh `hb.Font` and `TTFont` glyph order: 27 confirmed, 0 refuted — these are real current-font divergences, not classifier artifacts. The ss10 outlier (17,506 / 36 / 106,080 / 0) reflects its join-suppressing nature: fewer joins, fewer stances, fewer chances to diverge.

What happens to these during migration: in the new model, "post-ZWNJ behaves word-initial" is true by definition (§3.4), so all ~3.8M `.noentry`-after-ZWNJ rows and the boundary-guard asymmetries will _change behavior_ at cutover. Per §13.1 that is the designed outcome — each divergence is already a recorded triage row with the old and new outcomes side by side, so the change surfaces in the §11 review surface for an explicit verdict instead of slipping through as a silent change.

## Audit outcome and fixes

An independent adversarial audit re-derived the headline numbers with its own code (its own uharfbuzz shaping, its own GPOS cursive walk, a cursive-disabled re-shape to confirm join classification) and returned PASS on all seven questions: basis completeness (row counts recounted per length for all 11 configurations, canonical order, 0 violations), seam classification (21 spot rows byte-for-byte, zero misclassifications), pin replay (re-run, reproduced disagreements = 0), triage genuineness (5/5 re-shaped rows real), diff-stability (headers pin tool version, git SHA, font and alphabet SHA-256; two independent determinism re-runs byte-identical), oracle sufficiency for §13.4 (join height, variant choice, extension amount, ligation, and break-ness all recoverable), and git footprint (only the sanctioned `.gitignore` line; nothing staged).

The audit's one enumerated gap — the plan's §7 "name recovery" promise had no test proving the >63-byte-name workaround is load-bearing — was closed: the glyph-name lookup was extracted into a public `Shaper.glyph_name` method on both the extractor and validation Shapers, and four new tests now pin the census of long compiled names (4 names, max 67 bytes, e.g. `qsExcite.en-y0.ex-y0.before-vertical.after-baseline-letter.noentry`), prove the installed uharfbuzz truly truncates them, and assert both Shapers resolve them in full. The refactor was proven behavior-preserving: a 40,000-row re-extraction matches the shipped default table row-for-row, and a default-configuration triage re-run over all length ≤ 3 rows reproduced the shipped rows exactly (83,932 rows, identical). All validation gates were re-run green afterward (35 rebuild tests passed; pin replay still 0 disagreements; determinism still byte-identical). The four long-named glyphs are unreachable through shaping at depth ≤ 4 (brute-forced across all 11 configurations), so the mechanism-level test is the strongest available guard. The remaining audit caveats (length-5 ligature lookahead, hand-assembled `replay-report.json`, no rendered-ink record) sit inside PASS verdicts and are deferred by design to §10 tier 3, the migration tooling, and the §13.3 alias table respectively.

## How the next steps consume this artifact

- **§13.4 delete-on-evidence:** the 172 override rows, `_PENDING_BK_ENTRY_GUARDS`, and the guard/demote/reflip passes are never converted. Instead, the new pipeline's settlement table is diffed against these baseline tables; a matching row _is_ the proof that a deleted mechanism's behavior survived, and a mismatching row is a triage item that becomes an explicit refuse/prefer/resolve with a written reason. "Done" for a rune = its baseline rows match except where a reviewed row says otherwise.
- **§13.5 arbitration triage:** when the first full build throws `E-INCOMPARABLE`/`E-AMBIGUOUS`, the proposed resolve is the one that reproduces the baseline row, with provenance `migrated: matches pre-rebuild behavior`.
- **§11 review surface:** `make treaty-diff` against this baseline is the review page's first real workload — one rendered row per changed settlement entry, with the ~3.9M intended-equivalence rows as the first pre-triaged batch.
- **Diff stability (§8):** the committed-shape artifact is `digests.tsv` + `SUMMARY.md`; the canonical row order means a one-glyph behavioral change diffs as a readable per-string row, and the pinned provenance header (tool version, git SHA, font SHA-256, alphabet SHA-256) makes any oracle drift loud.

**Recommended next step (per §13.6 ordering):** begin ductus + first-rune migration with the worst accretion offenders — ·It (qsIt), ·Tea (qsTea), ·Pea (qsPea), ·May (qsMay), whose 42 stances between them prove the collapse — writing each rune's mandatory `way:` entries first, alongside the §14 module skeleton (`spec_load` → `surface` → `settle` → `table` as the minimal spine), using these baseline tables as the per-rune acceptance oracle from the first settled row.
