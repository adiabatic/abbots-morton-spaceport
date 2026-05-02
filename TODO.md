# TODO

## Kerning

- ensure kerning pairs work just as well in Quikscript Junior
- Kern:
  - ·No·Tea
  - ·See·Eight

## More glyphs

- Standalone
  - an ·I that drags along the baseline before going up sharply so it works with ·Way.half better (see "wild" and "wise" in The Manual)
- Make ligatures
  - ·Bay·Utter
  - ·Gay·Utter ("waggon")
  - ·Gay·Out

## Other

- Use `{exit_y: N}` / `{entry_y: N}` (added for `·Excite`) to clean up other letters in `glyph_data/quikscript.yaml` whose `after:` / `before:` / `not_after:` / `not_before:` selectors enumerate every letter that has a particular anchor Y. Likely candidates: half-form `not_before:` lists, `extend_*` targets that mirror anchor classes, and any context_set whose name reads as "letters with y=N exit/entry".
- Some glyphs have an `exit:` of +1 past the end, while most have +2 past the end. We should look into regularizing this, especially since we have extend.by now

- Make lists of letters like "goes straight up and down on the left" and put these letters in the list, and declare that ·Owe never joins to it:
  - ·Tea
  - ·Day
  - ·He
  - ·It
- Straight up and down on the right:
  - ·Pea
  - ·Tea
  - ·It
- Document why ligatures live in `calt` (not `liga`): contextual alternates must run first and be able to block ligatures by changing glyph identity (e.g. in ·Day·Utter·Low, the forward rule replaces ·Utter with ·Utter.alt before the ligature lookup sees it, preventing the ·Day·Utter ligature from firing)

## Restructure source so join mismatches are inexpressible

Follow-on to the Phase A static validator (`tools/quikscript_join_analysis.py:validate_join_consistency`) and Phase B derived guards. Once those have stabilized for a few feature additions, look at where the validator is still doing meaningful work — i.e., places where the same join is declared on both sides and the two declarations have to be kept in sync (e.g., one form's `cursive_exit` paired with another form's matching `cursive_entry` and an `extend_entry_after`).

If a cluster of those exists, propose a top-level `joins:` (or similar) section in `glyph_data/quikscript.yaml` that declares pair-level joins as bilateral edges, with both sides' anchors derived from the edge declaration. The goal is to reduce mismatches from "build-fails on inconsistent state" to "inexpressible in source".

Don't design this until the Phase A complaints (and Phase B derivation gaps) tell us where the current source language is genuinely inadequate.

Current status: `tools/quikscript_join_analysis.py:collect_join_warnings` is clean for the real senior glyph set, and `test_real_join_warning_collector_is_clean` locks that in. Only revisit a top-level `joins:` section if future real-data warnings show repeated bilateral source-maintenance pain.

## Bare-form bitmap stubs from `noentry_after` predecessors

Now that `noentry_after`-driven `not_before` blocks backward selection (a `qsX qsY` ligature with `qsM` in its `noentry_after` reverts qsM to its bare `prop` form), the bare bitmap may still carry the same exit-side ink as the joining-shape variant. Concrete case: `·Roe ·May ·They ·Utter` reverts qsMay to bare instead of qsMay.entry-baseline, but qsMay's `prop.shape: mono` and qsMay.entry-baseline's `shape: mono` are the same bitmap — so the y=5 exit ink (`   ##` in the top row) still hangs over qsThey_qsUtter.noentry's empty top-left. Audit which `noentry_after`-fed families need an `.exit-noentry` shape variant (with the exit-side ink trimmed) and how to route the cleanup to choose it over the bare form.

## The Manual

- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)

## Mixed `before:` / `not_before:` follow-ups

After the mixed-selector consolidation landed in 25c8649 (qsGay / qsHe / qsRoe / qsWay), some cleanup remains:

- If a ligature ever needs a mixed `before:` + `not_before:` selector and the forward-path emission misbehaves (e.g. the pair-override doesn't reach the ligature's lead), revisit the `expand_selectors_for_ligatures` interaction in `tools/quikscript_ir.py`. Today only positive selectors are expanded for ligatures by design, and the mixed-selector change doesn't alter that.
- Consider adding an IR diagnostic for the case where a merged form's resolved `not_before:` exclusion list subsumes its resolved `before:` list (i.e. the form can never fire). Skipped initially because the existing missing-glyph errors already catch typos and the failure mode is a silent no-op rather than a wrong shape.
