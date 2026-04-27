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

Current status: `tools/quikscript_join_analysis.py:collect_join_warnings` now floods senior builds with `join-selection-one-sided` and `join-bitmap-gap` warnings. Burn this list down before promoting the warnings to errors or replacing source-side declarations with a top-level `joins:` section.

## The Manual

- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)

## Automate `before` selectors for ligature components

When a glyph form uses `before: [{family: qsUtter}]` to select an exit variant (e.g. `qsIt.before_utter` exits at baseline so ·Utter can pick its alt form), the `before` selector only sees the _immediate next glyph_ in the stream. Ligature formation (`calt_liga`) runs later, so a `before: [{family: qsUtter}]` selector will never match a pre-liga `qsDay qsUtter` sequence — the next glyph is `qsDay`, not `qsUtter`.

This means every `before` selector that targets a family which is also the _second_ component of a ligature must manually list the first component too, or the exit variant won't fire before the ligature's first component. We hit this with `qsIt.before_utter`: standalone ·It before ·Day·Utter was picking `exit_xheight` (y=5) instead of `before_utter` (y=0), so ·Day never became ·Day.half, so `calt_liga` produced `qsDay_qsUtter` instead of `qsDay_qsUtter.half`.

The fix was to add `{family: qsDay}` to the `before` list, but this is fragile — every new `_qsUtter` ligature (·Bay·Utter, ·Gay·Utter, etc.) would need the same treatment for every form that uses `before: [{family: qsUtter}]`. The same class of bug could appear for any family that appears as a later component of a ligature.

Possible approaches:

- **Build-time expansion**: have the compiler automatically expand `before: [{family: qsX}]` to include every family whose ligature sequence _starts with_ a glyph that precedes `qsX`. For each ligature `A_B`, if `B == qsX`, add `A` to the resolved `before` set. This keeps the YAML source clean and prevents the bug from recurring when new ligatures are added.
- **Lint/warning**: at minimum, emit a warning during compilation when a `before` selector targets a family that is a non-first ligature component and the first component is missing from the selector.
