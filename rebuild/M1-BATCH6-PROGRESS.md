# M1 batch 6 — qsI

Scratch for the ·I migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Committed

The batch landed in three commits — the Manual-pin narrowing, ·I itself, and the census re-baseline. `git log glyph_data/runes/qsI.yaml` is the record; this file carries only what is still open.

## State

The artifact cycle is green and the surface is fresh; `make verdict-ready` is the standing check, and it clears everything but the server. The sitting's docket is the remaining blanks — the carry brought the human queue down by echo-fill and the standing approvals first, so what is left is genuinely new questions.

## Recorded design overrides

Each is a deliberate departure from the old font, to be adjudicated in the sitting rather than silently transcribed.

- **Both ·I stances carry the exit-side extend and contract records.** The old font gave `qsI.prop` all three derives but gave `entry_baseline_smaller_loop` only `contract_exit_before`, so `·Pea·I·Day` renders `qsPea|qsI.ex-ext-1|qsDay` — the smaller loop vanishes the moment ·I extends, because no extended smaller-loop form exists to fall back on. Read as an oversight rather than intent: the contract _was_ given to both stances, and both stances have the identical exit. The rune therefore keeps the smaller loop under extension. It surfaces on the `E650:E675:E665` family of rows as `cell,position`.
- **qsIt's extend-before-·I lost its `self: {entry: none}` scope.** The old font extends ·It's baseline exit before ·I when ·It has no entry _and_ when ·It was entered at the x-height (`qsIt.en-y5.ex-y0.ex-ext-1`, e.g. `E650:E670:E675`), which the narrow scope contradicted; the record only became reachable when ·I entered the alphabet. Widening it also cleared a hard E-CONTACT at y4 between ·It's bar and ·I's loop. Consequence to adjudicate: ·It entered at the baseline now extends and keeps a baseline join into ·I, where the old font took the x-height exit and broke.
- **Four `dangle:qsI.*` signatures blessed** in `rebuild/m1-contact-allow.yaml`, on the standing qsNo/qsDay/qsUtter precedent: ·I's top-right flick at y5 is reached diagonally from the loop, so `verify_withdrawal_safe`'s vertical-continuation test reads it as connector ink. The baseline has no withdrawn ·I form at all, so withdrawing it would change the letter's isolated shape.
- **The Manual-pin gate now binds only the mandating corpora.** `MANDATING_CORPORA` in `rebuild/pipeline/manual_pins.py` is `site/index.html` plus `site/the-manual.html`; `site/extra-senior-words.html` is a supplementary word list, not a transcription of Read's manual, so a pin there carries no Manual mandate. See the section below for what that defers.
- **ss02 is retired rather than adjudicated.** Migrating ·I made the ss02 windows live for the first time, and the user's call was to delete the feature instead of sitting on them: the qsTea ss02 unlock is gone, ·I·Tea keeps its x-height entry under ss03 (whose unlock list already admits ·I, with ss03's 1px ·I exit extension), and the acceptance matrix's multi-set coverage moved to ss03+ss05. The Manual's three `data-stylistic-set="02"` pins stay as shipped-font pins; the M1 gate skips them as `skipped_config`.

## What the corpus narrowing defers

Migrating ·I pulled `site/extra-senior-words.html:182` ("finally") into the no-waiver gate and it failed: the pin reads `·Fee | ·I ~x~ ·No | ·Utter.alt ~b~ ·Low ~x~ ·It`, while M1 shapes seam 2 as `y5` — ·No takes a backward x-height join into alt-·Utter where the pin breaks. Because the gate raises before the oracle step, this aborted every build.

·I did not cause the disagreement. It sits on the already-migrated `E666:E67A:E667` window, where the shipped font gives `qsNo|qsUtter.alt.ex-y0|qsLow` (`break,y0`) and M1 joins; ·I only made a pin that exercises it eligible. Nor was the pin a mistranscription — shaping the whole word against the shipped font returns `break,y5,break,y0,y5`, so it records that font exactly, and re-transcribing it to M1's reading would have broken `test/test_shaping.py`.

The underlying question is live and belongs in its own sitting: qsNo sits in `qsUtter.alternate`'s x-height entry `from:` list, deliberately — WHATNEXT records word-initial ·No·Utter·May joining via alt-·Utter as an accepted strict gain. What is new is _medial_ ·No before ·Low. At cutover `test/test_shaping.py` replays every corpus pin against the M1-built font, so it must be settled by then; the sibling pin on line 186 ("orally") pins the same `| ·Utter.alt ~b~ ·Low` shape behind an unmigrated ·Roe, so it also recurs at qsRoe.

## Open questions for the sitting

- The **qsMay 1px attachment drift** WHATNEXT predicted, now measured: `E675:E665` reports `slot 1 (qsMay.en-y5.after-i): origin want (150, 0), got (200, 0)` — 50 units at 50 units/pixel. The question is whether the qsAh precedent (accept it) still holds at ·I.
- The **smaller-loop-survives-extension override** above, on the `E650:E675:E665` rows.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.baseline_subset                   # after any M1_ALPHABET edit
uv run python -m rebuild.pipeline.run_m1                            # build + gates + oracle
uv run python rebuild/tools/probe.py E675:E665                      # the qsMay drift (PYTHONPATH=.)
make artifact-cycle
make verdict-ready
```

Oracle evidence is derived by scanning `rebuild/out/baseline-<config>.tsv.gz` directly. Two traps: `seams` is indexed per codepoint boundary, so map glyphs to codepoints through `clusters` rather than by position, and a ligature glyph owns several codepoint indices.

## Resume

```zsh
make review-cycle
```
