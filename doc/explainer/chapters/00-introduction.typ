#import "../style.typ": *

= How Abbots Morton Spaceport builds connected Quikscript

Abbots Morton Spaceport is a pixel-based font family for Quikscript. The hard part is not drawing the letters; it is teaching a text engine when to swap letter shapes and when to slide them together so cursive joins look deliberate instead of accidental. This document explains that pipeline from Unicode code points to final positioned glyphs.

#key_idea([
A modern font is part drawing file, part rule engine. Quikscript joining needs both.
])

