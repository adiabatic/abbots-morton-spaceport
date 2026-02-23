Copy an existing glyph from this font to a new glyph name so I can edit it. The arguments should be in the format `source-glyph new-glyph`, e.g. `o.prop myNewGlyph.prop`.

Steps:

1. Find the source glyph in the YAML files under `glyph_data/` and read its full definition (bitmap, y_offset, advance_width, etc.).

2. Add the new glyph to the appropriate YAML file, copying all properties from the source glyph.
   Literally duplicate the bitmap data — do not use YAML anchors/aliases (`&`/`*`) unless explicitly
   asked to copy "by reference". Follow the placement and ordering rules in @AGENTS.md.

3. Rebuild fonts:
   ```
   make all
   ```

4. If the new glyph uses a standard PostScript name (not a `uniXXXX` name), ensure it has an entry in @postscript_glyph_names.yaml.

5. If the new glyph was listed in @TODO.md, remove it.

The glyphs: $ARGUMENTS
