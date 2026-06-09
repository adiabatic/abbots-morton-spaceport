Create a ligature glyph in @glyph_data/quikscript.yaml by combining two existing glyphs. The arguments should be two glyph names, e.g. `qsDay qsUtter`, and then maybe afterwards other hints and tips.

Steps:

1. Find both source glyphs in the YAML files under `glyph_data/`. Prefer the `.prop` variant of each glyph if one exists, since ligatures are only used in the proportional Sans font. Read each glyph’s full definition (bitmap, y_offset, the anchors: mapping with entry / exit, etc.).

2. Determine the ligature family name using underscore convention: `{first}_{second}` (e.g., `qsDay_qsUtter`). List the components under a `sequence:` key (e.g. `sequence: [qsDay, qsUtter]`) and put the proportional bitmap in a `prop:` sub-record under that family key; the compiled glyph is named `qsDay_qsUtter`, with no `.prop` suffix. If either source glyph has an `.alt` variant that would be more appropriate for ligation (like `qsUtter.alt`), ask which variant to use.

3. Build the combined bitmap by placing both glyphs’ bitmaps side by side. The exit point of the first glyph should connect to the entry point of the second glyph — overlap or merge columns at the connection point so the stroke is continuous. Account for any `y_offset` differences between the two glyphs when aligning rows vertically. The resulting bitmap height should accommodate both glyphs (use the tallest span needed). Add the x-height / baseline comment markers (`#`) in the same style as existing glyphs.

4. Set properties on the ligature:
   - `y_offset`: if either glyph is Deep (y_offset: -3), the ligature needs it too.
   - `anchors.entry`: usually omit it — a two-glyph ligature inherits its entry anchor from the lead component automatically (`_inherit_ligature_entries_from_lead`), and a redundant explicit declaration emits a `LigatureEntryInheritanceWarning`. Declare `anchors.entry` only when the lead’s inheritable stance is context-restricted or the bitmap doesn’t share the lead’s leftmost-ink column at the entry’s Y.
   - `anchors.exit`: calculate based on the combined bitmap width and the second glyph’s exit Y coordinate.
   - `select` (with `after` / `before` / `not_after` / `not_before` lists) and `derive` rules: usually let the ligature inherit these from its components — `expand_selectors_for_ligatures` adds the ligature to followers’/predecessors’ selectors automatically, and `_iter_related_extension_targets` propagates the trailing component’s exit rules. Hand-author `select` / `derive` on the ligature only when it needs context beyond what the components supply (see AGENTS.md).

5. Place the ligature in @glyph_data/quikscript.yaml immediately after the last variant of the first component glyph (following existing ordering convention).

6. Show me the resulting bitmap so I can review and adjust it before rebuilding.

7. After I approve the bitmap, rebuild fonts:

   ```sh
   make all
   ```

The glyphs to ligate, and maybe other hints and tips: $ARGUMENTS
