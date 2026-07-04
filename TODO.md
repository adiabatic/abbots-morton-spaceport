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

## The Manual

- Leave a column on the right of the page to show page-number markers (that link to the PDF’s pages) (and also to leave space for the buttons)

## Mixed `before:` / `not_before:` follow-ups

After the mixed-selector consolidation landed in 25c8649 (qsGay / qsHe / qsRoe / qsWay), some cleanup remains:

- If a ligature ever needs a mixed `before:` + `not_before:` selector and the forward-path emission misbehaves (e.g. the pair-override doesn’t reach the ligature’s lead), revisit the `expand_selectors_for_ligatures` interaction in `tools/quikscript_ir.py`. Today only positive selectors are expanded for ligatures by design, and the mixed-selector change doesn’t alter that.
- Consider adding an IR diagnostic for the case where a merged stance’s resolved `not_before:` exclusion list subsumes its resolved `before:` list (i.e. the stance can never fire). Skipped initially because the existing missing-glyph errors already catch typos and the failure mode is a silent no-op rather than a wrong shape.
