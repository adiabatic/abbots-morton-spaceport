#import "../style.typ": *

= What is a font, actually?

A font file is not just a directory of pictures. It is a compact data structure containing shape data and behavior rules.

== a. Glyphs

Glyphs are the concrete shapes. In this project they come from bitmap rows in YAML and become outline rectangles in the compiled OTF.

== b. Character map

The cmap maps Unicode code points to base glyph names. For example, the project maps `U+E661` to `qsWay` and `U+E67A` to `qsUtter`.

== c. Metrics

Metrics define advance widths, side bearings, baseline position, and vertical extents. Without these, text cannot be spaced consistently.

== d. OpenType tables

OpenType behavior is split into two major families:

- GSUB: substitutes one glyph name for another glyph name.
- GPOS: adjusts glyph positions after substitution.

Quikscript joining needs both: GSUB picks the right variant forms; GPOS attaches them.

#try_it([
Turn `calt` and `curs` off in a shaping debugger. You will still get Quikscript glyphs, but many joins will break because the base forms and positions remain unchanged.
])

