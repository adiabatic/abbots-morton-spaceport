# TODO

- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)
- Document stylistic sets (ss01 = suppress ·Utter ~x~ ·Pea join, ss02 = full-size ·Tea entry at x-height after ·I)
- Document why ligatures live in `calt` (not `liga`): contextual alternates must run first and be able to block ligatures by changing glyph identity (e.g. in ·Day·Utter·Low, the forward rule replaces ·Utter with ·Utter.alt before the ligature lookup sees it, preventing the ·Day·Utter ligature from firing)

- Kern:
  - ·No·Tea
  - ·See·Eight

- Make an ·I that drags along the baseline so it works with ·Way.half better (see "wild" and "wise" in The Manual)
- Fix how qsFee.entry-xheight has a blank column on the right
- Make a ·Bay·Utter ligature (`qsBay_qsUtter.prop`)
- Make a ·Gay·Utter ligature (`qsGay_qsUtter.prop`) for "waggon"
