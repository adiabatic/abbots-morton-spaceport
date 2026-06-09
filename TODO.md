# TODO

## Ductus work

- Literally all of them.

## Kerning

- ensure kerning pairs work just as well in Quikscript Junior
- Kern:
  - ·No·Tea
  - ·See·Eight

## More/fewer glyphs

- Make ligatures
  - ·Bay·Utter
  - ·Gay·Utter (“waggon”)
  - ·Gay·Out

## Other

- Make lists of letters like “goes straight up and down on the left” and put these letters in the list, and declare that ·Owe never joins to it:
  - ·Tea
  - ·Day
  - ·He
  - ·It
- Straight up and down on the right:
  - ·Pea
  - ·Tea
  - ·It

## Consider making ·Jai join ·Excite and ·Gay at the baseline

Surfaced during the visual review of the second-order shaping-leak fix batch (commit b6b629e, “Stop dangling join strokes across non-joining breaks”). The leak fixes for ·Out·Tea·Jai·Excite and ·Out·Tea·Jai·Gay were accepted as improvements, but with the note that it’d be better still if ·Jai actually joined its follower instead of the connection being a clean non-join. Today ·Jai→·Excite and ·Jai→·Gay are genuine non-joins, so the batch correctly removed the dangling entry stubs the follower had grown back toward ·Jai across the break (·Excite reverted `en-y0.noexit`→bare, ·Gay reverted `en-y0.ex-y5`→bare, via `trailing_demote_overrides`/`predecessor_demote_overrides`).

Making them join is a separate, additive design decision — not a revert of the leak fix. Check ·Jai’s baseline exit anchor against ·Excite’s and ·Gay’s entry geometry, decide whether a baseline join is feasible and visually desirable, and if so add the join (anchors / joining stances) so the stroke genuinely connects; the existing demote-override cleanup then becomes moot for those pairs. Scope it and present options before changing anything.

## Restructure source so join mismatches are inexpressible

Follow-on to the Phase A static validator (`tools/quikscript_join_analysis.py:validate_join_consistency`) and Phase B derived guards. Once those have stabilized for a few feature additions, look at where the validator is still doing meaningful work — i.e., places where the same join is declared on both sides and the two declarations have to be kept in sync (e.g., one stance’s `cursive_exit` paired with another stance’s matching `cursive_entry` and an `extend_entry_after`).

If a cluster of those exists, propose a top-level `joins:` (or similar) section in `glyph_data/quikscript.yaml` that declares pair-level joins as bilateral edges, with both sides’ anchors derived from the edge declaration. The goal is to reduce mismatches from “build-fails on inconsistent state” to “inexpressible in source”.

Don’t design this until the Phase A complaints (and Phase B derivation gaps) tell us where the current source language is genuinely inadequate.

Current status: `tools/quikscript_join_analysis.py:collect_join_warnings` is clean for the real senior glyph set, and `test_real_join_warning_collector_is_clean` locks that in. Only revisit a top-level `joins:` section if future real-data warnings show repeated bilateral source-maintenance pain.

## The Manual

- Leave a column on the right of the page to show page-number markers (that link to the PDF’s pages) (and also to leave space for the buttons)

## Mixed `before:` / `not_before:` follow-ups

After the mixed-selector consolidation landed in 25c8649 (qsGay / qsHe / qsRoe / qsWay), some cleanup remains:

- If a ligature ever needs a mixed `before:` + `not_before:` selector and the forward-path emission misbehaves (e.g. the pair-override doesn’t reach the ligature’s lead), revisit the `expand_selectors_for_ligatures` interaction in `tools/quikscript_ir.py`. Today only positive selectors are expanded for ligatures by design, and the mixed-selector change doesn’t alter that.
- Consider adding an IR diagnostic for the case where a merged stance’s resolved `not_before:` exclusion list subsumes its resolved `before:` list (i.e. the stance can never fire). Skipped initially because the existing missing-glyph errors already catch typos and the failure mode is a silent no-op rather than a wrong shape.
