Copy a glyph from Departure Mono to this font. Only create a `.prop` variant unless explicitly asked for a mono (non-proportional) variant too.

Steps:

1. Extract the glyph bitmap:

   ```zsh
   uv run python extract_glyph.py test/DepartureMono-Regular.otf $ARGUMENTS
   ```

2. Create the `.prop` variant by trimming any leading or trailing columns that are all spaces across every row.

3. Add the `.prop` glyph to @glyph_data/latin.yaml in the "Non-Quikscript glyphs" section at the end, sorted by Unicode code point (not alphabetically by glyph name).

4. Rebuild fonts:

   ```zsh
   make all
   ```

5. Verify the glyph matches exactly:

   ```zsh
   uv run python extract_glyph.py --compare $ARGUMENTS test/DepartureMono-Regular.otf test/AbbotsMortonSpaceportMono.otf
   ```

   All metrics must match (advance_width, xMin, yMin, xMax, yMax, left_side_bearing). If they don't match, investigate and fix.

6. If the glyph uses a standard PostScript name (not a `uniXXXX` name), ensure it has an entry in @postscript_glyph_names.yaml.

The glyph to copy is: $ARGUMENTS
