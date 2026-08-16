# M1 batch 13 — qsAwe

Scratch for the ·Awe migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- **The ss10 uncovered-set has pre-existing holes this batch measured but did not repair.** Five migrated runes whose anchors ride their base cmap glyphs (qsI, qsRoe, qsEt, qsSee, qsVie) keep bare-carrier joins under the old ss10 overlay but are not `SS10_UNCOVERED_BY_OLD_FONT` members, so their ss10 seam-loss rows sit UNMATCHED (the length-2 exemplars in `rebuild/out/m1/divergence-audit.tsv` name the uncovered pairs); they cost no review — every `deferred-ss10` unit machine-approves through the Junior-equivalence oracle — which is presumably why nobody noticed. Two adjacent drifts spotted at the same time: the `_ss10_isolation_completed` docstring's "an existing|existing seam never joins under the old ss10" claim is false for those pairs (the predicate stays sound through the weaker member-adjacency condition), and `classify_divergence`'s ss10 ligature arm names only three formable pairs, so ·Vie/·See/·J'ai+·Utter ss10 ligation rows fall UNMATCHED too. Repairing any of these is a deliberate conform-side decision, not a letter batch's.
- **Two file-hygiene nits, separate cleanup:** qsNo's flipped-stance from-list tail is not in code-point order (pre-existing; ·Awe inserted at the ordered head), and the qsJai/qsJai_qsUtter alias block sits appended at the end of `rebuild/m1-aliases.yaml` rather than in its code-point slot (qsAwe's entries went into the slot per the dominant convention).
- **The scaling sweep's memory tripwire is now at the line.** This batch's post-migration `bench-the-rebuild/scaling/scaling.py` run put the top rung's peak RSS at about half this box's RAM — exactly the `RUST-PORT-PLAN.md` tripwire row that names memory, not wall clock, as the binding constraint when it trips. The whole-ladder time exponents stay under their thresholds (read them off the run, fitted over every rung, never off consecutive pairs). Another letter or two likely pushes the Python-arm sweep past half the box; the lever inventory is the speed-up tracker's, not a letter batch's.

## Shape of the letter, as modeled

·Awe is Short — ·Ah's vertical mirror. One `hapax` stance: x-height entry at the crown bar's left end ([0, 5]), baseline exit past the tail ([7, 0]), both allowlisted from oracle evidence. Its entry world is byte-identical to ·J'ai's (the same sixteen families, the four `*_qsUtter` ligatures included); its exit world is ·J'ai's plus qsUtter (no qsAwe ligature exists, so ·Awe·Utter is a plain baseline join where ·J'ai's became the ligature). Policy is two records: the entry extend by 1 after the x-height-exiting halves (qsTea live, qsHe deferred; ·Pea excluded because its half dips instead), and the ·Tea yielding prefer in exactly qsJai's shape — `then: {except: [{family: qsPea}]}`, keeps at run end and before ·Pea/·Tea/·Fee all falling out of join-count.

## What the neighbors already knew

qsRoe's x-height toward-list and qsPea's baseline-exit refuse both named qsAwe from their own batches and verified correct. qsPea's baseline-entry from-list also named qsAwe, and that arm was **wrong** — ·Awe·Pea joins in no config (the old selector it transcribed never wins) — so it came off. The eight added arms: qsAh/qsNo(alt)/qsOut(both stances)/qsOut_qsTea baseline-entry from-lists, and qsFee/qsOut/qsJai_qsUtter x-height-exit toward-lists. Everything else rides existing scopes with no edit: qsMay's self-live exit extend covers the entered-·May extension before ·Awe through its except-list, qsIt's pairings/refusals produce the ·Awe·It baseline join and its before-·Day withholding (ss04 unlock included) with ·Awe as just another baseline-exiting left, qsSee's yield-backward-serve-forward prefer and qsUtter's alt-stance records take ·Awe through their class scopes, and the ·Tea yield lists on qsDay/qsNo stay closed — ·Day·Tea·Awe and ·No·Tea·Awe queue in `regrouping-floor-drift` with the closed-list answer on record. qsUtter's yielding prefer must **not** gain a qsAwe arm: inert in default, actively wrong under ss03.

## Recorded design overrides

- **Oracle-verified-only lists**, as every batch; the one policy-record deferred name is qsHe on the entry extend.
- **qsAwe joins `SS10_UNCOVERED_BY_OLD_FONT`** on the qsAh precedent, both sides at once — the old record has no stances, so both anchors ride the base cmap glyph and bare qsAwe keeps its seams under ss10.
- **The one old-font wart needed no new machinery**: the single window where half-·Tea joins bare ·Awe without the entry extension (·Tea·Awe·Tea·Oy) is absorbed by the standing `halves-entry-extension-restored` class — M1 restores the extension uniformly.
- **The declined baseline exit keeps its tail** — four dangle blessings in `rebuild/m1-contact-allow.yaml`, the qsJai idiom: unconditional letterform ink, no withdrawn form in the old font, the last pixel not vertical so no automatic `withdrawal: safe` proof.
- **No ligature obligation, no new ledger class, no alias beyond qsAwe's own three names** (`qsAwe`, `.en-ext-1`, `.noentry` — the qsEt pattern).

## Open questions for the sitting

- The fresh ·Awe windows the oracle census now carries — standing-family echoes dominate (`bare-name-live-join`, the ·Pea/·Tea·No family, the ·Tea·I extension, ss03 ·Fee·Tea games, the pre-existing ss10 gaps); the echo prefill and standing approvals fold most, and whatever queues is the sitting's docket.
- ·Day·Tea·Awe / ·No·Tea·Awe land in `regrouping-floor-drift` per the closed yield lists — the glance is done, nothing read worse than the eighteen standing shapes, so they carry the pre-adjudicated answer.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E677         # the entry extension, all configs
PYTHONPATH=. uv run python rebuild/tools/probe.py E677:E652:E653    # the yielding prefer, all configs
make test-rebuild
make test
make artifact-cycle ARGS='--update-pins'
make verdict-ready
```

## Resume

```zsh
make review-cycle ARGS='--update-pins'
```
