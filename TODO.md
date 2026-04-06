# TODO

- Deeply reexamine ·Low and how it stabs left _hard_ and curls up at the end by default (ditto for ·Llan)
- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)
- Document why ligatures live in `calt` (not `liga`): contextual alternates must run first and be able to block ligatures by changing glyph identity (e.g. in ·Day·Utter·Low, the forward rule replaces ·Utter with ·Utter.alt before the ligature lookup sees it, preventing the ·Day·Utter ligature from firing)

- Kern:
  - ·No·Tea
  - ·See·Eight

- Make an ·I that drags along the baseline before going up sharply so it works with ·Way.half better (see "wild" and "wise" in The Manual)
- Make a ·Bay·Utter ligature (`qsBay_qsUtter.prop`)
- Make a ·Gay·Utter ligature (`qsGay_qsUtter.prop`) for "waggon"
