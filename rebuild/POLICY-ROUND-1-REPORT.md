# Policy round 1 report: the verdict round applied

## TL;DR

Your 441 verdicts (246 approve / 107 reject / 77 either / 8 skip / 3 neither) became **six edits across four rune files**: one record deletion and five added yielding `prefer` records, every one carrying a `why:` that quotes your note. Of the 107 rejects, **79 windows returned to byte-identical old ink, 16 returned to the old seams with only final-taste-approved residual cells** (the pulled-back ·May you said you like generally, or the single `en-ext-1` connector you approved 28 times in `same-seam-extension-non-summing`), and **12 are deliberately unedited** pending two questions for you (the 10 ss03 phenomenon-1b contradictions and the 2 same-seam rejects). All 246 approved windows kept their outcomes — 241 bit-unchanged, 5 documented carve-outs that change by exactly the pixel you rejected by name while every gained join survives. Nothing was committed or staged; the old pipeline is untouched and byte-identical; no verdicts were fabricated — the 284 unverdicted units stay unverdicted and the 8 skips remain proposed-only (section 3).

| Gate                                              | Result                                                                                                                           |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Schema + lints (`spec_load`)                      | Green — five new records schema-valid; only the two preexisting `SpecWarning`s                                                   |
| `run_m1` pipeline + section 9 E-gates             | Green — 0 errors / 0 flags; `E-UNREALIZED` clean with the unextended halves join                                                 |
| Oracle conformance (section 13.1)                 | 15,525 divergent rows (was 15,528), 0 unmatched / 0 multi-matched; audit byte-identical to the settle-validated v-final2         |
| Acceptance assertions                             | Rejected 107/107, approved 246/246, eithers/skips/neithers all within documented expectations (`tmp/round1_assertions_check.py`) |
| Font conformance sweep                            | 37,448 sequences / 299,584 shaping runs, 0 divergences, full rule and transition coverage; CoreText smoke 440/440                |
| Explain spot-checks                               | 5/5 flipped windows cite the new records by name                                                                                 |
| `uv run pytest rebuild/ -n auto --dist worksteal` | 381 passed, 1 skipped (378 + the 3 new ·May chain pins; re-run after the audit fix)                                              |
| `make test`                                       | 6,753 passed (re-run after the audit fix)                                                                                        |
| Old-font byte identity                            | `site/AbbotsMortonSpaceportSansSenior-Regular.otf` SHA-256 `3211a7a7…cf35`, unchanged                                            |
| Review surface + verdict re-import                | Regenerated (725 → 539 human units); reconciliation 318 carried + 85 resolved-by-revert + 38 re-review = 441                     |

Full plan and proofs: `rebuild/POLICY-ROUND-1-PLAN.md`, `rebuild/recon/policy-round-1-rejects.md` (recon A), `rebuild/recon/policy-round-1-reconcile.md` (recon B). Adversarial audit outcome and the one fix it forced: section 6.

## The records

This is the entire `glyph_data/runes/` diff you will be approving. Six edits, four files.

### `glyph_data/runes/qsIt.yaml` — deletion: the halves entry-extension record

The transcribed `policy.extend[0]` (`{stance: bar, entry: x-height, by: 1, when: {left: {class: halves-that-exit-at-x-height, except: [{family: qsPea}], joined_at: x-height}}}`) is deleted. A deletion carries no record, so the verdict lives in the replacement comment:

```yaml
  extend:
  # The old YAML's halves-minus-qsPea x-height entry-extension record was transcribed here for M1 and deleted in the round-1 verdict pass: the shipped font never realizes it (probed: both ·Tea·It and ·He·It join un-extended today), and the reviewer rejected every window where M1 realized it — "this widens the ·Tea·It extension for no reason".
  - {stance: bar, entry: baseline, by: 1, when: {left: {family: qsKey, joined_at: baseline}}}
```

Deleted rather than narrowed because the shipped font never draws this extension anywhere (the only out-of-alphabet trigger, half-·He, was probed too), and your notes reject the realized ink as such. This single deletion flips 51 of the 61 halves rejects and discharges the u-0397/u-0399 skip notes.

### `glyph_data/runes/qsIt.yaml` — `policy.prefer[0]`: decline ·It·It before ·May

```yaml
  prefer:
  - {cell: {exit: none}, over: {exit: baseline}, when: {right: {family: qsIt, then: {family: qsMay}}}, why: 'Round-1 verdict, ·X·It·It·May (u-0278, u-0282, u-0297): before a ·May that needs the baseline entry, the entered ·It declines the ·It·It join so ·It·May keeps it — "the former looks less awkward to right" (sic), "The old way seems nicer to write out by hand". Yielding and then-scoped, so the approved ·It·It join gains everywhere else are untouched.'}
```

The strongest surgical proof in the set: removal-and-rerun shows it changes exactly these 3 units and nothing else in the entire audit. `right.then` is legal on a `prefer` (the schema forbids it only on `refuse`); a bare refuse here would have overreached onto your 33 approved `entered-it-baseline-join-gain` windows.

### `glyph_data/runes/qsOy.yaml` — `policy.prefer[0]`: ·Oy yields the floor regroup

```yaml
  prefer:
  - {cell: {exit: none}, over: {exit: baseline}, when: {right: {family: [qsTea, qsIt]}}, why: 'Round-1 verdict, 37 windows: when the groupings tie, ·Oy leaves its baseline exit unrealized so the follower pair joins instead — "I think I would rather prefer ·Tea·It to join" (u-0280); "The old way seems nicer to write out by hand" (the rest). Yielding, so a strict-gain ·Oy·Tea/·Oy·It join is kept.'}
```

Flips 34 regrouping rejects. Because it is yielding, the bare ·Oy·Tea pair (which the old font joins) is untouched; only structural-floor ties flip.

### `glyph_data/runes/qsTea_qsOy.yaml` — `policy.prefer[0]`: the ligature twin

```yaml
  prefer:
  - {cell: {exit: none}, over: {exit: baseline}, when: {right: {family: [qsTea, qsIt]}}, why: 'Ligature twin of qsOy.policy.prefer[0] — ligatures do not inherit component policy (proven by u-0283/u-0284/u-0285), and forming ·Tea·Oy must not change the grouping: when the groupings tie, the ligature leaves its baseline exit unrealized so the follower pair joins — "The old way seems nicer to write out by hand".'}
```

Flips the 3 windows where `qsTea_qsOy` has formed and the component record is invisible.

### `glyph_data/runes/qsMay.yaml` — `policy.prefer[0..1]`: the greedy ·May·May pairing

```yaml
  prefer:
  - {cell: {exit: baseline}, when: {left: {is: boundary}, right: {family: qsMay}}, why: 'Round-1 verdict on ·May·May·May·May — "the old way seems nicer to write out by hand": at word start, pair up — the grounded baseline join into the next ·May beats declining when the join counts tie.'}
  - {cell: {exit: baseline}, when: {left: {family: qsMay, joined_at: none}, right: {family: qsMay}}, why: 'Chain interior of the same verdict ("the old way seems nicer to write out by hand"): after an unjoined ·May, pair with the next ·May. The acceptance oracle''s window universe tops out at four letters, where the word-start record alone already reproduces every outcome (including the u-0341 quad flip), so this record is invisible to the divergence audit — do not delete it as redundant. Its real load is ·May chains of five or more, which without it regress to the rejected defer-to-the-tail grouping instead of the old greedy y0 | break | y0 | break pairing the shipped font draws at every length; the length-5 and length-6 settle assertions in rebuild/test_settle.py pin it. Like the word-start record, its left: condition can never match a non-·May predecessor seam.'}
```

Together these flip u-0341 (·May·May·May·May settles `y0 | break | y0`, with the middle ·May wearing the pulled-back cell you like) and retire the `may-quad-order-deferral` ledger class (its predicate is removed from `rebuild/pipeline/conform.py`). The naive `{right: {family: qsMay}}` scoping was disproven empirically — it poisons 65 ·X·May·May windows, 28 of them approved — hence the two left-scoped records.

## Proposed resolutions awaiting you

Nothing below has been recorded as a verdict. Each is one question; answer in the review surface or just tell me.

1. **u-0243, u-0244, u-0245, u-0250** (skips, "·May should use the loop stance"): ·May already settles on the loop stance — what the note read as "not the loop stance" is the pulled-back exit bind you later said you like generally. **Record all four as approve?**
2. **u-0223, u-0224** (skips, "why is the red underlined part narrower in the after?"): the narrowing is genuine and intended — the `qsTea_qsOy` ligature draws the ·Tea bar directly into the ·Oy loop and is two pixels narrower than the separate glyphs (highlight 550 → 450 font units, advance 700 → 600). **Record both as approve?**
3. **u-0397, u-0399** (skips, "I like the extra join but the extension between ·Tea and ·It shouldn't change"): the halves deletion did exactly what the notes demand — both windows now keep the ·It·It baseline join with the ·Tea·It seam back at baseline width. **Re-render and give a fresh verdict (expected approve)?**
4. **u-0280** (recorded reject, note "I think I would rather prefer ·Tea·It to join"): the class-level qsOy record honors the reject and restores the ·Tea·It x-height join, but the unit's auto-drafted record was an anti-join `prefer` that would have done the opposite of the note — it was never applied and never should be. **Confirm the drafted record stays dead and the note moves to the follow-up ledger as a settle-order preference?**
5. **The 10 phenomenon-1b rejects** (u-0461…u-0467, u-0469, u-0471, u-0474, deliberately unedited): under ss03, when ·May joins from ·Pea/·Tea at the baseline and also extends toward a following half-·Tea, M1 composes a one-pixel-longer connector the old font did not draw — you rejected it on ·Pea·May·Tea-shaped words but approved the identical ·May cell on u-0468 and the 40 ss03-chain windows. **Keep M1's composed extension everywhere, or is a feature-scoped suppression worth engine work?**
6. **The 2 same-seam rejects** (u-0636 "new way has a worse ·May·It join", u-0656, deliberately unedited): they contradict your 28 same-phenomenon approvals at window grain, and honoring them would repeal the non-summing law. **Re-presented next to u-0637/u-0654/u-0663 — one ruling, please (presumed noise until then).**

## The follow-up ledger

Forward-looking input that is not this round's work, carried with full provenance in `rebuild/recon/policy-round-1-reconcile.md` section 4:

1. **Indefinite ·It join chains** — u-0421 (·May·It·It·It, approved with a wish): "it'd be even better if the ·It letters chained joins indefinitely". A future policy/engine round on `qsIt`'s exit refusals.
2. **Keep an existing ·Tea·It join over a floor regroup** — u-0280's note, now a settle-order preference to inform any future revisiting of the structural floor (the window itself is fixed this round).
3. **·Tea·It must not widen when ·It joins onward** — u-0397/u-0399; discharged by the halves deletion (confirmed in Phase 3: the entered-it windows lost the widening), closing once you give the fresh verdicts above.
4. **The 3 neithers, needing third designs**: u-0215 (·Pea·May·Tea·Oy, ss03, marker-staging — plausible fix is a ·May that joins into the `qsTea_qsOy` ligature); u-0683 and u-0716 (·Pea/·Tea ◊ZWNJ ·May·Tea — you rejected both the old `qsMay.noentry` drawing and the new locked pulled-back loop; recorded after the change of mind, so the final taste does not supersede them; needs a new word-initial ·May-before-·Tea shape).

## The regenerated review workload

725 → **539 human units** (2,410 total units, 15,525 rows, 2 batches; machine-approved 1,686 → 1,871). After-font SHA is now `dd45dc311d9ec312e2e65b160040278c09d29726724bc0ec423b45320fbb3b58` (recorded in the manifest); before-font unchanged.

| Class                               | Status            | Human units before | Human units after |
| ----------------------------------- | ----------------- | ------------------ | ----------------- |
| `may-quad-order-deferral`           | retired           | 1                  | — (entry deleted) |
| `zwnj-follower-exit-restored`       | reviewed-approved | 6                  | 6                 |
| `pre-ligature-cleanup-regularized`  | reviewed-approved | 6                  | 6                 |
| `pea-chain-regularized`             | reviewed-approved | 15                 | 15                |
| `same-seam-extension-non-summing`   | reviewed-approved | 33                 | 33                |
| `ss03-chain-join-gains`             | reviewed-approved | 40                 | 40                |
| `entered-it-baseline-join-gain`     | reviewed-approved | 46                 | 46                |
| `marker-staging-ligature-formation` | intended          | 52                 | 52                |
| `regrouping-floor-drift`            | reviewed-rejected | 64                 | 6                 |
| `halves-entry-extension-restored`   | reviewed-rejected | 193                | 33                |
| `may-exit-withdrawal-generalized`   | reviewed-approved | 269                | 302               |
| **Total**                           |                   | **725**            | **539**           |

The surviving `regrouping-floor-drift` class is exactly 6 units: old u-0277 (unverdicted ·Pea·May·Pea·Pea), the post-ZWNJ ·May triple (old u-0857), and the four formerly machine-approved ·Oy windows the qsOy record made visible — all drifting in the direction you said you prefer, all awaiting fresh verdicts.

**The 284 unverdicted units stay unverdicted.** 135 of them changed (880 rows): 105 fully re-converged to old ink and left the human workload, 19 halves units keep only the approved pulled-back ·May residual, and 11 entered-it units lose only the deleted pixel. None received a verdict of any kind.

**Verdict re-import — do not key on raw ids.** Unit ids are positional over the audit, and they shifted for 390 of your 441 verdicted units; new u-0278/u-0280/u-0282, for example, are the newly visible ·Oy regrouping windows, not the old rejects that carried those ids. Keyed on stable window identity (codepoints + configuration set), the reconciliation sums exactly: **318 carried** onto surviving units unchanged, **85 resolved-by-revert** (every one survives as a machine-approved ink-identical unit; none vanished outright), **38 re-review** (the 16 residual rejects, the 5 approved carve-outs, skips u-0397/u-0399, and the 15 surviving moved eithers). The old-id → `{new_id, status}` mapping is at `tmp/round1-verdict-id-mapping.json`. The export CLI runs clean against the new manifest (the `export.py` manifest-timestamp warning fires as documented).

## Audit outcome and fixes

The adversarial audit returned **gaps-found** with one real gap; everything else passed outright:

- **Passed**: flip authenticity (the auditor independently rebuilt the verdicted M1 font to the byte and ink-shaped 12 rejected + 14 approved windows across every cluster and class — all matched the documented expectations, including the residuals at name grain); record quality (schema-valid, every `why:` quote checked verbatim against the export); taste fidelity (zero `may-exit-withdrawal-generalized` rows reverted, the pulled-back ·May cell preserved in every residual); ledger honesty (oracle re-run from the working tree, byte-identical audit, cross-entry arithmetic nets −3 with the 3 fully conformant rows); no fabricated verdicts (441 exactly, skips and unverdicted untouched); footprint (diff confined to `glyph_data/runes/` and `rebuild/`, old font byte-identical).
- **The gap**: removal-and-rerun minimality showed `qsMay.policy.prefer[1]` (the chain-interior record) is byte-invisible to the entire acceptance audit — the oracle's window universe tops out at four letters, where the word-start record alone reproduces every outcome — and its `why:` misstated its load.
- **The fix**: the record is genuinely load-bearing for ·May chains of length five or more, which without it regress to the rejected defer-to-the-tail grouping (settle-probed at lengths 2–7; the shipped font HarfBuzz-probed as greedy `y0 | break | y0 | break` at every length). Its `why:` was rewritten to state exactly that, three settlement pins were added in `rebuild/test_settle.py` (lengths 4, 5, 6 over the real loaded rune YAML) so any remove-if-audit-invisible cleanup fails pytest, and the closure is recorded in the plan's minimality-nuance paragraph. Both suites re-ran green after the fix: `uv run pytest rebuild/` 381 passed / 1 skipped, `make test` 6,753 passed.

## Recommended next step

1. Open the review surface (`uv run python -m rebuild.review.serve`, `http://localhost:7294/`) and eyeball the flipped windows — the 38-unit re-review queue plus the 6 surviving `regrouping-floor-drift` units is an evening, not a weekend.
2. Answer the six questions in section 3 (the four skip groups, u-0280's dead draft, phenomenon 1b, and the same-seam ruling).
3. Approve the diff and commit (the `glyph_data/runes/` records above, the ledger, the conform predicate retirement, and the test pins).
4. Then the next migration batch per `rebuild/M1-REPORT.md`: qsDay/qsUtter plus qsNo or qsFee.
