Create a ligature glyph in @glyph_data/quikscript.yaml by combining two existing glyphs. The arguments should be two glyph names, e.g. `qsDay qsUtter`, and then maybe afterwards other hints and tips.

Steps:

1. Find both source glyphs in the YAML files under `glyph_data/`. Prefer the `.prop` variant of each glyph if one exists, since ligatures are only used in the proportional Sans font. Read each glyph's full definition (bitmap, y_offset, cursive_entry, cursive_exit, etc.).

2. Determine the ligature glyph name using underscore convention: `{first}_{second}.prop` (e.g., `qsDay_qsUtter.prop`). If either source glyph has an `.alt` variant that would be more appropriate for ligation (like `qsUtter.alt`), ask which variant to use.

3. Build the combined bitmap by placing both glyphs' bitmaps side by side. The exit point of the first glyph should connect to the entry point of the second glyph — overlap or merge columns at the connection point so the stroke is continuous. Account for any `y_offset` differences between the two glyphs when aligning rows vertically. The resulting bitmap height should accommodate both glyphs (use the tallest span needed). Add the x-height / baseline comment markers (`#`) in the same style as existing glyphs.

4. Set properties on the ligature:
   - `y_offset`: if either glyph is Deep (y_offset: -3), the ligature needs it too.
   - `cursive_entry`: take from the first glyph's `.prop` variant (if it has one).
   - `cursive_exit`: calculate based on the combined bitmap width and the second glyph's exit Y coordinate.
   - `calt_after` / `calt_before`: carry over from the source glyphs if applicable.

5. Place the ligature in @glyph_data/quikscript.yaml immediately after the last variant of the first component glyph (following existing ordering convention).

6. Show me the resulting bitmap so I can review and adjust it before rebuilding.

7. After I approve the bitmap, rebuild fonts:

   ```sh
   make all
   ```

The glyphs to ligate, and maybe other hints and tips: $ARGUMENTS
