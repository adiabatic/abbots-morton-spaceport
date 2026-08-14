# M1 batch 10 — qsVie (and qsVie_qsUtter)

Scratch for the ·Vie migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

Nothing.

## State

The rune, the ligature rune, the alphabet entry, the aliases, the neighbor edits, and the smoke rows are in place, and the gates are green: the conform sweep is exact over the grown alphabet, the boundary-equivalence sweep is exact, and every in-scope Manual pin replays clean — the ·Vie pins became gate-eligible with this batch and agreed on the first build. The defect gates needed one blessing block in `rebuild/m1-contact-allow.yaml`: the ligature's declined-exit x-height arm (E-DANGLE on the qsEt/qsRoe terminal precedent, one signature per entry/lock variant of the same arm). The oracle's unmatched rows are up, as a new letter always puts them, and are verdict-gated rather than failing (`oracle_summary.json`); the unmatched delta over batch 9 is exactly the E659 rows, so no pre-existing row changed class and the neighbor edits disturbed nothing.

## Shape of the letter, as modeled

·Vie is a Deep bowl-and-descender: the bowl from the x-height down to the baseline bar, and a cup in the descender. It joins only at the baseline, on both sides. One baseline entry on the bar's left end, unrestricted on the qsRoe/qsEt/qsSee idiom — every baseline exiter in the old font joins it, and every breaker lacks a baseline exit that reaches it. The `swept-out` twin stance redraws the descender's end climbing out to a baseline exit, `toward:`-scoped to the five shipped receivers (qsIng, qsNo, qsLow, qsRoe, qsExcite — three migrated), and the choice between the stances is pure join-count: the old font's X·Vie·Y grid is separable (the left seam depends only on X, the right only on Y), so the rune carries no pairings, no prefers, and no refuses. The entry extends by 1 after a short list of lefts — drafted as a faithful port of the shipped derive, kept per the user's drafting-time decision (·May's twin record was dropped only for engine walls ·Vie doesn't hit), since narrowed by the sitting; read the surviving list off the rune — written as two records, one per stance, since both stances offer the entry.

qsVie_qsUtter is the batch's formation-closure obligation. It is never entryless: the shipped file omits `entry:` and inherits ·Vie's baseline entry and entry extension through the lead, which the rune transcribes explicitly (same unrestricted entry, same five-family extend), so no predecessor ever reverts and ZWNJ is the only formation blocker. One x-height exit that extends by 1 before ·Fee and, ss03-gated, before ·Tea; the shipped trailing-component derives toward qsBay/qsGay/qsThey (extend) and qsJai (contract) stay untranscribed, out of the migrated alphabet, deferred to those migrations. The un-form set — before the followers only alt-·Utter's baseline exit can serve (·Vie, ·See, ·Low, ·I, ·Ah among the migrated) — falls out of `settle.formation_blocked` with no rune data. One emit-grain consequence worth knowing: qsVie sits in the shared §5.7 guard class unconditionally while qsSee left it for conditional two-slot lines, because a following ·See·Utter pair forms entrylessly and takes ·See's entry out of play, but a following ·Vie·Utter pair keeps ·Vie's inherited entry live; `rebuild/test_emit.py` pins the shapes.

All ductus is drafted, not transcribed — the shipped YAML carries none for qsVie or the ligature — and is marked `# DRAFT — pending author sign-off` in both runes. The stance names `normal` / `swept-out` (and the ligature's `hapax`) are drafts on the same terms.

## What the neighbors already knew

Most wiring predated the batch: qsNo.flipped's baseline entry and qsRoe's hapax baseline exit already named qsVie, and qsFee's both-direction lists and qsMay's x-height-arrivers group already named qsVie_qsUtter. The three edits this batch made, all oracle-verified: qsTea.half's ss03 unlock left scope gains qsVie_qsUtter, and qsPea's full-stance and qsOy's x-height entry from-lists gain qsVie_qsUtter (the batch-9 pattern with qsSee_qsUtter; the qsPea half is never entered by the ligature, so its list stays). Every other list the pair map touches was already unrestricted.

## Recorded design overrides

- **The baseline entry is unrestricted where a from-list was possible** — the qsRoe/qsEt/qsSee idiom, on the same grounds as batches 8 and 9.
- **The entry extension was ported faithfully, and the sitting has been trimming it** — the port was asked and answered with the user at drafting time, with the known warts routed to the sitting rather than the ledger; the ·It and ·Tea arms have since come off there, and their disposition is the WHATNEXT bullet, not this file.
- **Only oracle-verified exit targets are authored on the ligature** — qsFee plus the shipped ss03-gated qsTea; the qsBay/qsGay/qsThey/qsJai trailing-component inheritance waits for those migrations.

## Open questions for the sitting

- **The `·Tea·Oy ·Vie` windows drop the old font's entry extension** (exemplar `E652:E679:E659`): the old pipeline extended ·Vie after the qsTea_qsOy ligature by lead-expansion, and the port scopes the extend to bare families, so M1 joins one pixel tighter there. The widen-to-name-the-ligature branch is closed by the sitting's ·Tea strike — the old font drew that pixel on the ·Oy·Vie seam only because the ligature's lead was ·Tea, and ·Tea no longer extends into ·Vie at all — so what is left folds into the dropped-·Tea·Vie-extension disposition in WHATNEXT, beside its day-baseline-entry-extension-dropped cousin.
- **A small ss04 formation-tie family** (exemplars `E659:E67A:E670:{E653,E667,E67A}`): the old font un-forms ·Vie·Utter so alt-·Utter can feed the ss04-entered ·It; formed and unformed tie on join-count, and M1's §5.7 guard un-forms only on strict gain, so M1 keeps the ligature — the batch-9 ·See·Utter·Tea·Tea shape, a principled gain-adjacent divergence.
- **`·Day·Tea·Vie` and `·No·Tea·Vie` join the ·Day·Tea·X yield family** (they land in regrouping-floor-drift) — the standing WHATNEXT fork, not a qsVie question.
- **The ss10 rows sit unmatched** until the per-rune `SS10_UNCOVERED_BY_OLD_FONT` adjudication is made at review, exactly as batch 9's qsSee rows did.
- Neither entered-left extension wart survived the sitting, and neither is a ledger question any more: the reviewer refused the pixel on the ss04 entered-·It arm and then on the ss05 entered-·Tea one, and since no ·Vie-side record can see whether its left was entered, both letters came off the entry extension outright. The disposition of the rows that leaves unmatched is the WHATNEXT bullet.

## The WHATNEXT items this batch was supposed to answer

- **The ·Tea·Day tight bond** — ·Vie cannot exit into ·Tea at all (the swept exit's `toward:` list has no qsTea, matching the shipped break), so the batch needed neither yielding shape and qsUtter's records are untouched.
- **qsFee's forward-preference left scope** — nothing needed: ·Vie has no x-height anchor on either side, ·Fee·Vie and ·Vie·Fee break in the old font and fall out of join-count, and the ligature's y5 reach into ·Fee rides qsFee's pre-wired from-list.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E659:E67A         # one window, all configs
make test-rebuild
make test
make artifact-cycle ARGS='--update-pins'
make verdict-ready
```

## Resume

```zsh
make review-cycle ARGS='--update-pins'
```
