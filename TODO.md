# TODO

- Find out if there is a way to do tests for AMS that depend on having some kind of extra OpenType thing turned on, like discretionary ligatures for Joins On Both Sides At The Same Height For ·Tea
- ensure kerning pairs work just as well in Quikscript Junior
- Make lists of letters like "goes straight up and down on the left" and put these letters in the list, and declare that ·Owe never joins to it:
  - ·Tea
  - ·Day
  - ·He
  - ·It
- Straight up and down on the right:
  - ·Pea
  - ·Tea
  - ·It
- Deeply reexamine ·Low and how it stabs left _hard_ and curls up at the end by default (ditto for ·Llan)
- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)
- Document why ligatures live in `calt` (not `liga`): contextual alternates must run first and be able to block ligatures by changing glyph identity (e.g. in ·Day·Utter·Low, the forward rule replaces ·Utter with ·Utter.alt before the ligature lookup sees it, preventing the ·Day·Utter ligature from firing)

- Kern:
  - ·No·Tea
  - ·See·Eight

- Make an ·I that drags along the baseline before going up sharply so it works with ·Way.half better (see "wild" and "wise" in The Manual)
- Make a ·Bay·Utter ligature (`qsBay_qsUtter.prop`)
- Make a ·Gay·Utter ligature (`qsGay_qsUtter.prop`) for "waggon"
