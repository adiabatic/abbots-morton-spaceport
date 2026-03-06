#import "../style.typ": *

= Appendix: YAML schema keys

This project's glyph definitions use the following keys:

#table(
  columns: (2.2fr, 5.8fr),
  stroke: (paint: frame, thickness: 0.5pt),
  inset: 5pt,
  [*Key*], [*Meaning*],
  [`bitmap`], [List of pixel rows (`#` on, space off) for the glyph shape.],
  [`advance_width`], [Explicit horizontal advance in pixels (before scaling to font units).],
  [`y_offset`], [Vertical pixel shift; negative values move deep glyphs below baseline.],
  [`cursive_entry`], [Entry anchor(s) for cursive attachment in pixel coordinates `[x, y]`.],
  [`cursive_exit`], [Exit anchor(s) for cursive attachment in pixel coordinates `[x, y]`.],
  [`calt_before`], [Force this variant when one of the listed glyphs follows (forward condition).],
  [`calt_after`], [Force this variant when one of the listed glyphs precedes (backward condition).],
  [`calt_not_before`], [Block a forward contextual substitution for listed following glyphs.],
  [`calt_not_after`], [Block a backward contextual substitution for listed preceding glyphs.],
  [`calt_word_final`], [Marks a word-final variant; build emits substitute/revert rules around word boundaries.],
  [`extend_entry_after`], [Requests auto-generated `.entry-extended` variants after listed glyphs for spacing refinements.],
)

#technical_detail([
Other keys exist in the full project (for marks, composites, kerning tags, and metadata), but the list above is the complete set requested for Quikscript joining behavior in this explainer.
])
