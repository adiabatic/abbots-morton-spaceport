Copy a glyph from Departure Mono to this font. Steps:

1. Extract the glyph bitmap:
   ```
   uv run python extract_glyph.py test/DepartureMono-Regular.otf $ARGUMENTS
   ```

2. Add the extracted bitmap to @glyph_data/latin.yaml in the "Non-Quikscript glyphs" section at the end. Keep that section sorted by Unicode code point (not alphabetically by glyph name), with proportional variants (.prop) immediately after their base glyph.

3. If the bitmap has leading or trailing spaces on any row that are consistent across all rows (i.e., columns that are all spaces), create a `.prop` variant with those empty columns removed. Place it immediately after the base glyph.

4. Rebuild fonts:
   ```
   make all
   ```

5. Verify the glyph matches exactly:
   ```
   uv run python extract_glyph.py --compare $ARGUMENTS test/DepartureMono-Regular.otf test/AbbotsMortonSpaceportMono.otf
   ```

All metrics must match (advance_width, xMin, yMin, xMax, yMax, left_side_bearing). If they don't match, investigate and fix.

The glyph to copy is: $ARGUMENTS
