Copy a glyph from Departure Mono to this font. Steps:

1. Extract the glyph bitmap:
   ```
   uv run python extract_glyph.py test/DepartureMono-Regular.otf $ARGUMENTS
   ```

2. Add the extracted bitmap to @glyph_data.yaml after all Quikscript letters (after `uniE67E`). Keep non-Quikscript glyphs in alphabetical order by glyph name.

3. Rebuild fonts:
   ```
   make all
   ```

4. Verify the glyph matches exactly:
   ```
   uv run python extract_glyph.py --compare $ARGUMENTS test/DepartureMono-Regular.otf test/AbbotsMortonSpaceportMono.otf
   ```

All metrics must match (advance_width, xMin, yMin, xMax, yMax, left_side_bearing). If they don't match, investigate and fix.

The glyph to copy is: $ARGUMENTS
