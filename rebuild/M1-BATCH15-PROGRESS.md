# M1 batch 15 — qsEight

Scratch for the ·Eight migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Parked

- **The post-migration scaling sweep ran, on the kernel arm issue #77 moved it to; the RAM tripwire holds under the line.** Every rung goes through `ams-m1-kernel` now, so the top rung's peak is that kernel child's own high-water, and it sits under the `RUST-PORT-PLAN.md` half-the-box row — within reach of it, not at it, where the Python arm sat for batches 13 and 14 — while the whole-ladder window and CPU fits stay in their standing shape in both denominators, fitted over every rung, never off consecutive pairs, whose tail always reads steeper (`bench-the-rebuild/scaling/scaling.txt` is this run; a pre-#77 row compares on exponent, not constant). The lever inventory is the speed-up tracker's, not this batch's.

## Shape of the letter, as modeled

·Eight is Short — a small closed bowl under the x-height and a straight diagonal falling to the baseline. Two stances, ·I's pair mirrored: `normal-sized-loop` shares the mono drawing (`shape: mono` in the old record), and `smaller-loop` pulls the bowl in one column for the rising baseline followers; both carry the x-height entry at [0, 5] and baseline exit at [5, 0], allowlisted from oracle evidence, with the two exit toward-lists partitioning the receivers between them. Its entry and exit worlds are byte-identical to ·Awe's and ·Ox's — the same sixteen sources (the four `*_qsUtter` ligatures included) and the same sixteen receivers — and like both it forms no ligature anywhere in the old font. The ·Tea yielding prefer is qsJai's shape verbatim, as with ·Awe and ·Ox. The letter's extra policy weight over ·Ox is extension traffic on both sides: the entry extend names all three x-height-exiting halves (qsPea joins the list — half-·Pea's dipped exit takes the extension here, unlike before ·Awe or ·Ox — with qsHe still deferred) on both stances, and the old font's exit-extend-by-1 survives only for the deferred qsYe/qsHe/qsExam — the live rising followers take the smaller loop instead.

## What the neighbors already knew

Five files pre-named qsEight from their own batches, all verified correct against the pair map: qsFee (the exit toward-list and the exit-extend right list — ·Fee reaches ·Eight with `ex-ext-1`), qsTea (the baseline entry-extend left list, an arm the smaller-loop override below has since retired — ·Tea now meets the smaller bowl bare), qsI (both loop stances' exit-extend right lists), qsRoe (the x-height toward-list and the baseline entry-extend left list — `en-ext-1-at-0` after ·Eight), and qsPea (the baseline-exit refuse; ·Pea joins through the half's dip). The seven added arms are ·Ox's eight minus the pre-wired qsFee toward-list: qsAh/qsNo(alt)/qsOut(both stances)/qsOut_qsTea baseline-entry from-lists, and qsOut/qsJai_qsUtter x-height-exit toward-lists.

## Recorded design overrides

- **Oracle-verified-only surface lists**, as every batch; the policy-record deferred names are qsHe on the entry extend and qsYe/qsHe/qsExam on the exit extend, all from the old record's stated targets.
- **qsEight joins `SS10_UNCOVERED_BY_OLD_FONT`** on the qsAh/qsAwe/qsOx precedent, both sides at once — no stances in the old record, so both anchors ride the base cmap glyph.
- **The declined baseline exit is dangle-blessed, not proven** — ·Eight's terminal pixel arrives diagonally with no ink above it, so `withdrawal: safe`'s vertical-continuation proof cannot hold and the exit takes ·Awe's shape: no withdrawal binding, with the per-variant dangle blessings in `rebuild/m1-contact-allow.yaml`.
- **One ·Ox-style corner family rode along anyway**: ·Eight's bowl closes one column left of ·I's loop at y4 on the baseline-proven ·Eight·I join, so the contact-allow file also gains the ·Eight·I corners — every ·Eight exit variant against every ·I onward-exit variant, one signature each, the drawing identical throughout.
- **The one old-font wart needed no new machinery**: the single window where half-·Tea joins bare ·Eight without the entry extension (·Tea·Eight·Tea·Oy — the same shape as ·Awe's and ·Ox's) is absorbed by the standing `halves-entry-extension-restored` class.
- **No ligature obligation, no new ledger class**; the alias block carries five names (`qsEight`, `.en-ext-1`, `.ex-ext-1`, `.noentry`, `.noentry.ex-ext-1`) — the qsEt pattern plus the exit-extended pair.
- **The smaller loop replaces the exit-side de-rub extensions**: a second stance mirrors ·I's `smaller-loop` — the bowl pulled in one column — and the mechanism is structural, not a prefer: the two stances' exit toward-lists partition the receivers, `normal-sized-loop` handing ·Tea and ·It to `smaller-loop`, so candidacy and the join-count stage pick the smaller bowl exactly where the baseline exit joins them. (A stance prefer was tried first and sits E-AMBIGUOUS against the ·Tea yield in the tie windows, where yield-and-let-·Tea-join-onward versus join-with-the-smaller-bowl are genuinely different outcomes; the partition keeps the yield winning there.) qsIt left ·Eight's exit-extend and qsEight left qsTea's entry-extend in the same stroke. qsYe/qsHe/qsExam stay parked on the exit-extend record for their migrations to convert.

## Open questions for the sitting

- The fresh ·Eight windows the oracle census now carries — standing-family echoes dominate; the echo prefill and standing approvals fold most, and whatever queues is the sitting's docket.
- ·Day·Tea·Eight / ·No·Tea·Eight land in `regrouping-floor-drift` per the closed yield lists, with the pre-adjudicated answer on record; reopen only if a seam reads genuinely worse on the surface than the standing shapes do.
- The drafted `ductus` on qsEight awaits the author's vet — the `# DRAFT` marker is the worklist.
- The smaller-loop windows (·Eight before ·Tea and ·It) are fresh divergences for the docket, and no `why:` is recorded for the smaller-loop design yet — collect the user's words at the sitting.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E652:E673         # the entry extension, all configs
PYTHONPATH=. uv run python rebuild/tools/probe.py E673:E652:E653    # the yielding prefer, all configs
PYTHONPATH=. uv run python rebuild/tools/probe.py E673:E670         # the smaller loop before ·It (no extension), all configs
PYTHONPATH=. uv run python rebuild/tools/probe.py E673:E652:E650    # the smaller loop before ·Tea (no en-ext-1), all configs
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

## Resume

```zsh
make review-cycle
```
