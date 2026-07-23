# M1 batch 5 progress — qsAh

Scratch progress file for the in-flight qsAh migration; delete at batch close, lifting surviving forward-pointers into WHATNEXT.md.

## Committed

- a97767a Add ·Ah
- a1ceb3b Let ·Ah·May·Tea join on both sides of ·May again (the sitting's 17 rejects; the ss03/ss10 arms deliberately hold their adjudicated renders)
- c1814af Draw word-final ·Ah·Tea with the full bar under ss03 (the sitting's neither on ·Fee·Utter·Ah·Tea)

## Parked

- **Ductus DRAFT sign-off** — today's YAML carries no ductus for qsAh, so `qsAh.yaml`'s `hapax` motion is drafted fresh and carries the `# DRAFT — pending author sign-off` marker.
- **Three new dangle blessings await author review** — `dangle:qsAh.hapax[.en-y0|.locked]:exit:x-height` in `rebuild/m1-contact-allow.yaml`, with drafted `why:` prose in the section's idiom (the top-right y5 nub is unconditional letterform ink in the shipped font, like ·Utter/·No/·May's). Rewrite or bless the prose as yours.
- **The entry from-list's deferred-partner tail is recon-derived** — the eight unmigrated members (qsGay, qsThey, qsJai, qsYe, qsHe, qsWhy, qsRoe, qsExcite) came from an FEA sweep, not the oracle; re-verify each at its own migration (qsRoe is the solid one: `select.before {family: qsAh, entry_y: 0}` in today's YAML). Caution from this batch: the FEA sweep misses bare-carrier joins (no substitution emitted when the base drawing already holds the live anchor) — that's how qsDay/qsOy/qsTea_qsOy were nearly left out before the oracle exposed the seam-losses.

## Standing per-migration checks, resolved

- **qsMay `reaches_up_and_way_over` scope** — came due as the ·Ah·May 1px attachment drift; resolved as accepted-to-sitting (see Design overrides).
- **qsDay depth-3 withdraw list** — extended with qsAh on oracle evidence: the old font renders ·Day·Tea·No·Ah as the third form (`break, y0, y0`) in every non-ss10 config, so ·No.flipped engages before ·Ah exactly as the parked WHATNEXT bullet predicted.
- **qsDay/qsOy/qsTea_qsOy depth-4 fourth slot** — extended with qsAh (user decision, this batch): ·Ah is the eighth baseline-enterable follower and takes the yielded arm like its siblings; the windows are beyond oracle depth so this shipped on design consistency, not evidence.
- **qsFee own-prefer audit** — trivially clean: qsAh authors no prefers, so nothing can fire against a qsFee follower and demand the opposite seam.

## Design overrides

- **The ·Ah·May 1px drift is accepted and rides to the sitting (user decision, this batch)** — the qsMay `reaches_up_and_way_over` re-check came due: the old font attaches short-reachers (extended ·Ah, later ·I) on qsMay's stub-keeping form at x=3, while the rune's single x-height entry row binds `pulled-back-stubless` at `joined_x: 2` for every from-member, and the partners' extend records do not cover the scope. The rejected alternatives: per-scope `contract: bind:` records (first real settlement-level `bind:`, re-verification burden on every existing ·X·May window) and a schema extension (per-from-atom bindings). The 2,843 ·Ah·May rows land as verdict-gated review units; at batch close, resolve the WHATNEXT "Unresolved design flags" bullet's qsAh half accordingly (the qsEt half stays).

## Recorded lesson

- **Scope from the baseline TSV, never from FEA reconnaissance.** The old font joins bare pairs by GPOS cursive attachment alone whenever both bare glyphs carry same-height anchors — no calt rule fires, so FEA grep calls the pair "no interaction." That blind spot produced two wrong turns this batch: the initial missing qsDay/qsOy/qsTea_qsOy entries in qsAh's `from:` (bare-carrier baseline joins), and a wrong `toward:` scope on qsAh's exit that broke the real ·Ah·Day/·Ah·No/·Ah·It y5 joins (reverted; the exit is open like qsLow's, and qsDay/qsNo/qsIt's open x-height entries accept). The length-2 rows of `rebuild/out/m1/baseline-<config>.subset.tsv.gz` are the definitive pair-level join map.

## Verification recipe

- `uv run pytest rebuild/ -n auto --dist worksteal` — expect exactly 15 failures: the four documented baseline spec-pins plus the eleven census-pinned review tests (audit/enrich/ink/families/build), which re-baseline at the artifact cycle. The three qsAh-era content pins (test_table orphan window, test_emit guard lines ×2, test_specificity depth-3 axes) are already updated and green.
- `uv run python -m rebuild.pipeline.run_m1 --jobs 8` — defect gates pass (137 glyphs, 8 configs, 0 errors after the three qsAh dangle blessings); budget/boundary/Manual-pin gates pass; exits nonzero at the oracle stage as always.
- Oracle direct: expect `multi_matched: 0`, `unmatched: 19905` — the 13,984 standing verdict-gated rows plus ~5,900 new qsAh windows (the accepted ·Ah·May attachment drift, plus standing-family windows with ·Ah riding along).
- `uv run python -m rebuild.pipeline.run_m1 --conform-only --jobs 8` — must be exact (14-symbol alphabet).
- `make test` self-skips (the shipped legacy pipeline is untouched).

## Resume

1. `make review-cycle` (artifact cycle: snapshot, run_m1, surface rebuild, verdict carry + merge, census re-pin, gates), then `make verdict-ready`.
2. Prepare the sitting with the review-docket skill (echo prefill, docket bake), then adjudicate the qsAh blanks in the app's docket view (`#view=docket`).
3. At batch close: delete this file, lifting surviving forward-pointers into WHATNEXT.md — including resolving the WHATNEXT "Unresolved design flags" qsAh half and the "·Day·Tea-before-·No follower set" bullet's qsAh instance.
